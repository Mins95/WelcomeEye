"""Real encrypted packets on a simulated media worker; no network or outputs."""
import asyncio
import importlib
import struct
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from test_control_coordination import PACKAGE, control, protected

v1_control = importlib.import_module(PACKAGE + '.v1_control')
standby = importlib.import_module(PACKAGE + '.standby_ring')


def response(uid='TESTUID000', result=1, reason=0):
    a, b = b'abcdefghijklmno\0', b'ABCDEFGHIJKLMNO\0'
    clear = struct.pack('<QHH', 1234, result, reason)
    key, iv = a[9:12] + b[2:12] + bytes(3), a[4:13] + bytes(7)
    return protected.rc4(uid.encode(), struct.pack('<I', 1) + a + protected.aes_cfb(key, iv, clear) + b)


def inspect_request(packet):
    """Independent field assertions against the native 100-byte layout."""
    assert struct.unpack_from('>I', packet)[0] == len(packet) - 4
    assert packet[4:8] == bytes(4)
    kind, body = protected.parse_tlvs(packet[8:])[0]
    assert kind == 505 and len(body) == 100
    raw = protected.rc4(b'TESTUID000', body)
    a, b, c = raw[:16], raw[16:32], raw[-16:]
    plain = protected.aes_cfb(a[8:14] + b[4:9] + bytes(5), c[4:15] + bytes(5), raw[32:-16], decrypt=True)
    return struct.unpack('<QI32sIBB2s', plain)


class FakeMedia:
    def __init__(self):
        self.info = types.SimpleNamespace(uid='TESTUID000')
        self.channel, self.stream, self.mode = 16, 1, 2
        self.encryption_profile = 0x060A0401
        self.closed = threading.Event()
        self.sock = self
        self.packets = []
        self.send_error = None

    def device_now(self):
        return 1234

    def sendall(self, packet):
        self.packets.append(packet)
        if self.send_error:
            raise self.send_error


class V1MediaControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = FakeMedia()
        self.leases = set()
        self.opened = 0
        self.stops = 0
        self.auto_send = self.auto_reply = True
        self.sent_observed = False
        self.acquire_error = None
        self.release_error = None
        self.reply = response()
        self.release_started = asyncio.Event()
        self.listener = standby.StandbyRingListener()
        self.hub = types.SimpleNamespace(
            entry=types.SimpleNamespace(unique_id='TESTUID000', data={
                'host': 'unused', 'username': 'unused', 'password': 'test-code'}),
            loop=asyncio.get_running_loop(), device_model='WelcomeEye Connect V1',
            session=self.session, connected=True, ring_listener=self.listener,
            acquire=self.acquire, release=self.release,
        )
        self.controller = control.DeviceController(self.hub)
        self.worker = asyncio.create_task(self.pump())
        self.timeout_patch = patch.object(v1_control, 'RESPONSE_TIMEOUT', 0.15)
        self.timeout_patch.start()
        self.network_patch = patch.object(control, 'Session', side_effect=AssertionError('V1 must use hub media'))
        self.network_patch.start()

    async def asyncTearDown(self):
        self.controller.close()
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        await asyncio.sleep(0)
        self.timeout_patch.stop()
        self.network_patch.stop()
        self.assertEqual(self.leases, set())
        self.assertEqual(self.listener.connection_attempts, 0)
        self.assertEqual(self.listener.control_pause_count, 0)
        self.assertEqual(self.listener.resume_count, 0)
        self.assertFalse(self.controller.lock.locked())

    async def acquire(self, lease):
        self.leases.add(lease)
        if self.acquire_error:
            raise self.acquire_error
        if not self.hub.connected:
            self.opened += 1
            self.hub.connected = True

    async def release(self, lease):
        self.leases.discard(lease)
        self.release_started.set()
        if self.opened and not self.leases:
            self.stops += 1
        if self.release_error:
            raise self.release_error

    async def pump(self):
        while True:
            if self.auto_reply and self.session.packets and not self.sent_observed:
                self.sent_observed = True
                parts = [(57, bytes(4)), (100, b'video-untouched'), (506, self.reply)]
                self.controller.v1_media.observe(self.session, parts)
                self.assertEqual(parts[1], (100, b'video-untouched'))
            if self.auto_send:
                self.controller.v1_media.send_pending(self.hub.session)
            await asyncio.sleep(0.001)

    async def unlock(self, output=0):
        return await asyncio.to_thread(self.controller.unlock, output)

    async def wait_pending(self):
        for _ in range(500):
            if self.controller.v1_media.pending is not None:
                return self.controller.v1_media.pending
            await asyncio.sleep(0.001)
        self.fail('no pending command')

    async def test_output0_exact_native_payload_once_existing_media_unchanged(self):
        await self.unlock(0)
        self.assertEqual(len(self.session.packets), 1)
        timestamp, device, password, delay, output, action, reserved = inspect_request(self.session.packets[0])
        self.assertEqual((timestamp, device, delay, output, action, reserved), (1234, 0, 0, 0, 1, bytes(2)))
        self.assertEqual(password.rstrip(b'\0').decode(), control.encode_password('test-code'))
        self.assertEqual(self.opened, 0)
        self.assertEqual(self.stops, 0)
        self.assertFalse(self.session.closed.is_set())
        self.assertEqual(self.controller.request_send_attempt_count, 1)
        self.assertEqual(self.controller.request_sent_count, 1)
        self.assertEqual(self.controller.response_count, 1)
        self.assertEqual(self.controller.v1_media.stage, 'acknowledged_not_physically_verified')

    async def test_output1_one_native_command_temporary_media_released(self):
        self.hub.connected = False
        await self.unlock(1)
        self.assertEqual(inspect_request(self.session.packets[0])[-3:], (1, 1, bytes(2)))
        self.assertEqual(len(self.session.packets), 1)
        self.assertEqual((self.opened, self.stops), (1, 1))

    async def test_acquire_failure_zero_packets_and_release(self):
        self.acquire_error = TimeoutError('no media')
        with self.assertRaises(TimeoutError):
            await self.unlock()
        self.assertEqual(self.session.packets, [])
        self.assertEqual(self.controller.last_error_stage, 'acquiring_v1_media')

    async def test_uid_mismatch_zero_packets(self):
        self.session.info.uid = 'WRONG'
        with self.assertRaises(protected.ProtocolError):
            await self.unlock()
        self.assertEqual(self.session.packets, [])

    async def test_wrong_profile_zero_packets_no_fallback(self):
        self.session.channel = 0
        with self.assertRaises(protected.ProtocolError):
            await self.unlock()
        self.assertEqual(self.session.packets, [])

    async def test_build_failure_zero_packets(self):
        with patch.object(v1_control, 'build_unlock_request', side_effect=ValueError('invalid config')):
            with self.assertRaises(ValueError):
                await self.unlock()
        self.assertEqual(self.session.packets, [])
        self.assertEqual(self.controller.last_error_stage, 'building_request')

    async def test_confirmation_timeout_one_packet_no_second_on_same_session(self):
        self.auto_reply = False
        with self.assertRaises(TimeoutError):
            await self.unlock()
        self.controller.last_command = float('-inf')
        with self.assertRaises(protected.ProtocolError):
            await self.unlock()
        self.assertEqual(len(self.session.packets), 1)

    async def test_queued_deadline_zero_packets_even_if_worker_resumes_later(self):
        self.auto_send = False
        with self.assertRaises(TimeoutError):
            await self.unlock()
        self.controller.v1_media.send_pending(self.session)
        self.assertEqual(self.session.packets, [])

    async def test_partial_send_error_one_attempt_and_cooldown(self):
        self.session.send_error = OSError('partial send')
        with self.assertRaises(OSError):
            await self.unlock()
        with self.assertRaises(protected.ProtocolError):
            await self.unlock()
        self.assertEqual(len(self.session.packets), 1)
        self.assertEqual(self.controller.request_send_attempt_count, 1)
        self.assertEqual(self.controller.request_sent_count, 0)

    async def test_decode_error_one_packet_no_retry(self):
        self.reply = b'invalid'
        with self.assertRaises(protected.ProtocolError):
            await self.unlock()
        self.assertEqual(len(self.session.packets), 1)
        self.assertEqual(self.controller.decode_failures, 1)

    async def test_negative_response_recorded_and_not_retried(self):
        self.reply = response(result=0, reason=7)
        with self.assertRaises(protected.ProtocolError):
            await self.unlock()
        self.assertEqual(len(self.session.packets), 1)
        self.assertEqual((self.controller.last_result, self.controller.last_reason), (0, 7))

    async def test_concurrent_click_and_cooldown_do_not_send_second_packet(self):
        self.auto_send = False
        first = asyncio.create_task(self.unlock())
        await self.wait_pending()
        with self.assertRaises(protected.ProtocolError):
            await self.unlock(1)
        self.auto_send = True
        await first
        with self.assertRaises(protected.ProtocolError):
            await self.unlock(1)
        self.assertEqual(len(self.session.packets), 1)

    async def test_stop_before_send_zero_packets(self):
        self.auto_send = False
        task = asyncio.create_task(self.unlock())
        await self.wait_pending()
        self.controller.close()
        with self.assertRaises(BaseException):
            await task
        await asyncio.wait_for(self.release_started.wait(), 1)
        self.controller.v1_media.send_pending(self.session)
        self.assertEqual(self.session.packets, [])

    async def test_reconnect_never_transfers_pending_physical_command(self):
        self.auto_send = False
        task = asyncio.create_task(self.unlock())
        await self.wait_pending()
        self.controller.v1_media.media_closed(self.session)
        replacement = FakeMedia()
        self.hub.session = replacement
        self.controller.v1_media.send_pending(replacement)
        with self.assertRaises(ConnectionError):
            await task
        self.assertEqual(self.session.packets, [])
        self.assertEqual(replacement.packets, [])

    async def test_stale_confirmation_before_send_is_ignored(self):
        self.auto_send = self.auto_reply = False
        task = asyncio.create_task(self.unlock())
        await self.wait_pending()
        self.controller.v1_media.observe(self.session, [(506, response())])
        self.assertEqual(self.controller.response_count, 0)
        self.auto_send = True
        with self.assertRaises(TimeoutError):
            await task
        self.assertEqual(len(self.session.packets), 1)

    async def test_uid_changed_between_queue_and_send_zero_packets(self):
        self.auto_send = False
        task = asyncio.create_task(self.unlock())
        await self.wait_pending()
        self.session.info.uid = 'WRONG'
        self.auto_send = True
        with self.assertRaises(protected.ProtocolError):
            await task
        self.assertEqual(self.session.packets, [])

    async def test_cleanup_failure_releases_command_mutex(self):
        self.release_error = RuntimeError('cleanup failure')
        with self.assertRaises(RuntimeError):
            await self.unlock()
        self.assertEqual(len(self.session.packets), 1)
        self.assertEqual(self.controller.cleanup_error_type, 'RuntimeError')
        self.assertFalse(self.controller.lock.locked())


class RealWorkerControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_v1_output_on_actual_worker_preserves_decoded_video_and_single_session(self):
        from test_v1_video_receive import Clock, make_session, synthetic_h264
        from test_beta2_v1_video import hardware_wire
        helpers = types.ModuleType('homeassistant.helpers')
        helpers.device_registry = types.ModuleType('homeassistant.helpers.device_registry')
        with patch.dict(sys.modules, {'homeassistant': types.ModuleType('homeassistant'),
                                     'homeassistant.helpers': helpers,
                                     'homeassistant.helpers.device_registry': helpers.device_registry}):
            hub_module = importlib.import_module(PACKAGE + '.hub')
        entry = types.SimpleNamespace(unique_id='TESTUID000', data={
            'host': 'unused', 'username': 'unused', 'password': 'test-code',
            'detected_model': 'WelcomeEye Connect V1'})
        hub = hub_module.WelcomeEyeHub(types.SimpleNamespace(), entry)
        hub.stopped = False
        hub._observe_device_model = lambda *args: None
        frames = []
        hub.frame_listeners.add(lambda kind, frame: frames.append(kind))
        fmt = bytearray(40)
        fmt[4:8] = b'H264'
        struct.pack_into('<HH', fmt, 12, 352, 288)
        fmt[16] = 20
        struct.pack_into('<I', fmt, 20, 8000)
        struct.pack_into('<HH', fmt, 28, 0x7A19, 1)
        packets = synthetic_h264()
        chunks = b''.join(hardware_wire(kind, body) for kind, body in packets)
        session = make_session([chunks], Clock(), False)
        session.info = types.SimpleNamespace(uid='TESTUID000')
        session.encryption_profile = 0x060A0401
        session.device_now = lambda: 1234
        session.last_keepalive = time.monotonic()
        session.connect = lambda: [(203, bytes(fmt))]
        lifecycle = []
        session.send_start_av = lambda: lifecycle.append('start')
        session.send_stop_av = lambda: lifecycle.append('stop')
        session.send_manufacturer = lambda data: None
        sock = session.sock
        physical = []
        original_recv = sock.recv

        def recv(size):
            if not sock.chunks:
                time.sleep(.002)
                sock.chunks.append(protected.owsp(protected.tlv(57, bytes(4))))
            return original_recv(size)

        def sendall(packet):
            kind, _body = protected.parse_tlvs(packet[8:])[0]
            if kind == 505:
                physical.append(packet)
                sock.chunks.append(protected.owsp(protected.tlv(506, response())))
            elif kind != 49:
                raise AssertionError('unexpected command')

        sock.recv = recv
        sock.sendall = sendall
        viewer = object()
        with patch.object(hub_module, 'Session', return_value=session) as factory:
            try:
                await hub.acquire(viewer)
                await asyncio.to_thread(hub.control.unlock, 0)
                self.assertEqual(len(physical), 1)
                self.assertEqual(inspect_request(physical[0])[-3:], (0, 1, bytes(2)))
                self.assertEqual(factory.call_count, 1)
                self.assertIs(hub.session, session)
                self.assertTrue(hub.thread.is_alive())
                self.assertEqual(hub.consumers, {viewer})
                self.assertTrue(hub.image and hub.image.startswith(b'\xff\xd8'))
                self.assertGreaterEqual(frames.count('video'), 6)
                self.assertEqual(lifecycle, ['start'])
                self.assertEqual(hub.control.response_count, 1)
                self.assertEqual(hub.ring_listener.connection_attempts, 0)
            finally:
                await hub.release(viewer)
        self.assertEqual(lifecycle, ['start', 'stop'])
        self.assertTrue(session.closed.is_set())
        self.assertIsNone(hub.thread)
