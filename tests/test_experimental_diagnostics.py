"""Synthetic offline SDK experiments; no hardware fixtures or network targets."""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from load_integration import load

experimental = load('experimental_diagnostics')
protected = load('protected')
cap = load('capabilities')
_spec = importlib.util.spec_from_file_location('udt_offline_test', Path(__file__).resolve().parents[1] / 'tools/inspect_experimental_udt.py')
udt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(udt)


def version_reply(metadata=None, kind=470):
    return protected.owsp(protected.tlv(kind, json.dumps(metadata or {'AppCom': 'V401.R001.A350',
        'SolCom': 'R001', 'ReleaseTime': '2026-09-15', 'HDVersion': 'G0123.B025'}).encode()))


def user(admin=True, control=True):
    return SimpleNamespace(id='synthetic-user', is_admin=admin,
                           permissions=SimpleNamespace(check_entity=Mock(return_value=control)))


def hub():
    return SimpleNamespace(entry=SimpleNamespace(data={'host': '192.0.2.20',
        'username': 'synthetic-user', 'password': 'synthetic-password'}),
        protocol_family=cap.ProtocolFamily.LEGACY, variant=cap.DeviceVariant.V1,
        stopped=False, lock=asyncio.Lock(), control=SimpleNamespace(lock=threading.Lock()),
        consumers=set(), session=None, thread=None,
        ring_listener=SimpleNamespace(session=None, thread=None),
        manual_snapshot=None, ring_image=None)


class FakeSession:
    """Explicit synthetic session with plaintext test replies; never a fixture UID."""
    instances = []
    reads = None
    connect_error = None
    block = False
    started = threading.Event()
    cleanup_hold = False
    cleanup_started = threading.Event()
    cleanup_release = threading.Event()

    def __init__(self, host, username, password, channel, **kwargs):
        self.channel, self.stream, self.mode = channel, kwargs['stream'], kwargs['mode']
        self.operation = kwargs['operation']
        self.sock = None
        self.info = SimpleNamespace(uid='SYNTHETIC-NOT-A-DEVICE')
        self.encryption_profile = 1
        self.authenticated = False
        self.closed = threading.Event()
        self.cancelled = kwargs.get('cancel_event') or threading.Event()
        self.v1_video_receive = False
        self.connection_stage = 'tcp_connecting'
        self.sent_counts = {}
        self.read_deadline = float('inf')
        self.items = list(self.reads or [[(510, b'synthetic-private-response')]])
        self.instances.append(self)

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.sock = object()
        self.sent_counts[501] = 1
        self.authenticated = True

    def set_read_budget(self, seconds):
        self.read_deadline = time.monotonic() + seconds

    def begin_cleanup(self):
        self.set_read_budget(2)

    def enable_v1_video_receive(self):
        self.v1_video_receive = True

    def send_manufacturer(self, data):
        assert data == bytes.fromhex('0000000c00000000d501040000000000')
        self.sent_counts[509] = self.sent_counts.get(509, 0) + 1

    def send_start_av(self):
        self.sent_counts[5007] = self.sent_counts.get(5007, 0) + 1

    def send_stop_av(self):
        self.sent_counts[5009] = self.sent_counts.get(5009, 0) + 1

    def send_session_stop(self):
        self.sent_counts[5005] = self.sent_counts.get(5005, 0) + 1

    def read(self):
        self.started.set()
        if self.block:
            self.cancelled.wait(3)
        if self.cancelled.is_set():
            raise ConnectionAbortedError('synthetic cancellation')
        if not self.items:
            raise TimeoutError('synthetic timeout')
        result = self.items.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def cancel(self):
        self.cancelled.set()

    def close(self):
        if self.cleanup_hold:
            self.cleanup_started.set()
            self.cleanup_release.wait(3)
        self.closed.set()
        self.sock = None


class VersionEvidenceTests(unittest.TestCase):
    def test_exact_apk_inner_query_and_response_pair(self):
        self.assertEqual(experimental.version_query().hex(), '0000000c00000000d501040000000000')
        metadata = experimental.decode_version_response(version_reply())
        self.assertEqual(set(metadata), {'AppCom', 'SolCom', 'ReleaseTime', 'HDVersion'})
        self.assertEqual(experimental.decode_version_response(version_reply(kind=14854)), None)

    def test_metadata_allowlist_and_unsafe_values(self):
        found = experimental.decode_version_response(version_reply({
            'AppCom': 'https://192.0.2.20/UIDsecret', 'SolCom': 'R001',
            'HDVersion': '192.0.2.20', 'ReleaseTime': '2026-09-15',
            'UID': 'private-synthetic-marker', 'password': 'another-marker'}))
        self.assertEqual(found, {'SolCom': 'R001', 'ReleaseTime': '2026-09-15'})
        self.assertNotIn('marker', json.dumps(found))

    def test_invalid_envelope_and_metadata_never_promoted(self):
        payloads = [b'bad', bytes(1025), version_reply() + b'x',
            protected.owsp(protected.tlv(470, b'[]')),
            protected.owsp(protected.tlv(470, b'{"AppCom":"1","AppCom":"2"}')),
            protected.owsp(protected.tlv(470, b'x' * 513)),
            protected.owsp(protected.tlv(470, b'{}') + protected.tlv(14854, b'{}'))]
        for payload in payloads:
            with self.subTest(size=len(payload)), self.assertRaises(protected.ProtocolError):
                experimental.decode_version_response(payload)


class WireBudgetTests(unittest.TestCase):
    def session(self, op='version_469'):
        instance = experimental._DiagnosticSession('192.0.2.20', 'synthetic', 'synthetic',
            0 if op == 'version_469' else 18, stream=3 if op == 'version_469' else 1,
            mode=0 if op == 'version_469' else 2, operation=op)
        instance.sock = Mock()
        return instance

    def test_forbidden_packets_never_reach_socket(self):
        for operation in ('version_469', 'additional_camera'):
            instance = self.session(operation)
            for kind in (505, 507, 5006, 503, 433, 1233):
                with self.subTest(operation=operation, kind=kind), self.assertRaises(protected.ProtocolError):
                    instance.send_packet(protected.owsp(protected.tlv(kind, b'no')))
            instance.sock.sendall.assert_not_called()

    def test_partial_send_consumes_the_single_attempt(self):
        instance = self.session()
        instance.sock.sendall.side_effect = BrokenPipeError('synthetic private marker')
        packet = protected.owsp(protected.tlv(509, b'synthetic-query'))
        with self.assertRaises(BrokenPipeError):
            instance.send_packet(packet)
        with self.assertRaises(protected.ProtocolError):
            instance.send_packet(packet)
        self.assertEqual(instance.sock.sendall.call_count, 1)
        self.assertEqual(instance.sent_counts, {509: 1})
        self.assertLessEqual(instance.sock.settimeout.call_args.args[0], .5)

    def test_cancel_allows_cleanup_only_and_send_deadline_is_global(self):
        instance = self.session('additional_camera')
        instance.cancel()
        with self.assertRaises(ConnectionAbortedError):
            instance.send_packet(protected.owsp(protected.tlv(5007, b'x')))
        instance.send_packet(protected.owsp(protected.tlv(5009, b'x')))
        instance.send_packet(protected.owsp(protected.tlv(5005, b'')))
        self.assertEqual(instance.sent_counts, {5009: 1, 5005: 1})
        expired = self.session()
        expired.network_deadline = time.monotonic() - 1
        with self.assertRaises(TimeoutError):
            expired.send_packet(protected.owsp(protected.tlv(509, b'x')))
        expired.sock.sendall.assert_not_called()
        self.assertEqual(expired.sent_counts, {509: 1})

    def test_only_correct_operation_packets_are_allowed(self):
        query = self.session()
        camera = self.session('additional_camera')
        with self.assertRaises(protected.ProtocolError):
            query.send_packet(protected.owsp(protected.tlv(5007, b'x')))
        with self.assertRaises(protected.ProtocolError):
            camera.send_packet(protected.owsp(protected.tlv(509, b'x')))

    def test_refused_tcp_does_not_repeat_discovery_or_tcp(self):
        instance = self.session()
        udp = Mock()
        udp.recvfrom.return_value = (b'synthetic-discovery', ('192.0.2.20', 1500))
        info = SimpleNamespace(address='192.0.2.20', protected=True, tcp_port=19000, uid='synthetic')
        with patch.object(experimental.socket, 'socket', return_value=udp) as make_udp, \
             patch.object(experimental, 'decode_discovery', return_value=info), \
             patch.object(experimental.socket, 'create_connection', side_effect=ConnectionRefusedError()) as tcp:
            with self.assertRaises(ConnectionRefusedError):
                instance.connect()
        self.assertEqual(udp.sendto.call_count, 1)
        self.assertEqual(make_udp.call_count, 1)
        self.assertEqual(tcp.call_count, 1)
        udp.close.assert_called_once()

    def test_discovery_identity_mismatch_prevents_tcp_and_login(self):
        instance = self.session()
        instance.expected_uid = 'expected-synthetic-device'
        udp = Mock()
        udp.recvfrom.return_value = (b'synthetic-discovery', ('192.0.2.20', 1500))
        info = SimpleNamespace(address='192.0.2.20', protected=True, tcp_port=19000, uid='different-synthetic-device')
        with patch.object(experimental.socket, 'socket', return_value=udp), \
             patch.object(experimental, 'decode_discovery', return_value=info), \
             patch.object(experimental.socket, 'create_connection') as tcp:
            with self.assertRaises(protected.ProtocolError):
                instance.connect()
        tcp.assert_not_called()
        self.assertEqual(udp.sendto.call_count, 1)

    def test_one_login_and_exact_channels_without_cache(self):
        for op, channel, stream, mode in (('version_469', 0, 3, 0), ('additional_camera', 18, 1, 2)):
            instance = self.session(op)
            udp = Mock()
            udp.recvfrom.return_value = (b'synthetic', ('192.0.2.20', 1500))
            tcp = Mock()
            info = SimpleNamespace(address='192.0.2.20', protected=True, tcp_port=19000, uid='synthetic')
            packet = protected.owsp(protected.tlv(40, b'x') + protected.tlv(501, b'login'))
            with patch.object(experimental.socket, 'socket', return_value=udp), \
                 patch.object(experimental, 'decode_discovery', return_value=info), \
                 patch.object(experimental.socket, 'create_connection', return_value=tcp), \
                 patch.object(experimental, 'build_protected_login', return_value=packet) as login, \
                 patch.object(instance, 'read', return_value=[(502, b'synthetic-login')]), \
                 patch.object(experimental, 'decode_login_reply', return_value=(1, 0, {'AppId': 1}, 100)):
                self.assertEqual(instance.connect(), [])
            self.assertTrue(instance.authenticated)
            self.assertEqual(instance.sent_counts, {40: 1, 501: 1})
            self.assertEqual(login.call_args.kwargs, {'channel': channel, 'stream': stream, 'mode': mode})
            self.assertEqual(tcp.sendall.call_count, 1)

    def test_discovery_timeout_is_one_query(self):
        instance = self.session()
        udp = Mock()
        udp.recvfrom.side_effect = TimeoutError()
        with patch.object(experimental.socket, 'socket', return_value=udp), \
             patch.object(experimental.socket, 'create_connection') as tcp:
            with self.assertRaises(experimental.DiscoveryTimeout) as caught:
                instance.connect()
        self.assertEqual(caught.exception.probe_count, 1)
        self.assertEqual(udp.sendto.call_count, 1)
        tcp.assert_not_called()

    def test_exact_fragmentation_timeout_eof_and_budgets(self):
        instance = self.session()
        instance.sock.recv.side_effect = [TimeoutError(), b'ab', b'cd']
        self.assertEqual(instance._exact(4), b'abcd')
        self.assertEqual(instance.received_bytes, 4)
        instance.sock.recv.return_value = b''
        instance.sock.recv.side_effect = None
        with self.assertRaises(ConnectionError):
            instance._exact(1)
        instance.read_deadline = time.monotonic() - 1
        with self.assertRaises(TimeoutError):
            instance._exact(1)
        with self.assertRaises(protected.ProtocolError):
            instance._exact(1048577)

    def test_partial_frame_keeps_no_unbounded_orphan_reader(self):
        instance = self.session()
        instance.received_bytes = experimental.MAX_RECEIVED_BYTES
        instance.sock.recv.return_value = b'x'
        with self.assertRaises(protected.ProtocolError):
            instance._exact(1)
        instance = self.session()
        instance.cancel()
        with self.assertRaises(ConnectionAbortedError):
            instance._exact(1)
        instance.sock.shutdown.assert_called_once_with(socket.SHUT_RD)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeSession.instances = []
        FakeSession.connect_error = None
        FakeSession.reads = None
        FakeSession.block = False
        FakeSession.started.clear()
        FakeSession.cleanup_hold = False
        FakeSession.cleanup_started.clear()
        FakeSession.cleanup_release.clear()
        self.fake = patch.object(experimental, '_DiagnosticSession', FakeSession)
        self.fake.start()
        self.addCleanup(self.fake.stop)
        self.private = patch.object(experimental, 'decode_private_reply', return_value=(100, version_reply()))
        self.private.start()
        self.addCleanup(self.private.stop)
        self.target = hub()
        self.backend = experimental.ExperimentalDiagnostics(self.target)

    async def run_op(self, op='version_469', **kwargs):
        return await self.backend.execute(op, confirm=True, user=user(), entity_id='camera.synthetic', **kwargs)

    async def test_permissions_every_operation_fail_before_network(self):
        for op in ('version_469', 'additional_camera'):
            for identity, entity_id in ((None, 'camera.synthetic'), (user(admin=False), 'camera.synthetic'),
                    (user(control=False), 'camera.synthetic'), (user(), 'sensor.synthetic')):
                with self.subTest(operation=op), self.assertRaises(PermissionError):
                    await self.backend.execute(op, confirm=True, user=identity, entity_id=entity_id)
        self.assertEqual(FakeSession.instances, [])

    async def test_opt_in_family_and_unload_guards(self):
        for confirm in (False, None, 1):
            result = await self.backend.execute('version_469', confirm=confirm, user=user(), entity_id='camera.synthetic')
            self.assertEqual(result['reason'], 'explicit_confirmation_required')
        for family in (cap.ProtocolFamily.CONNECT3, cap.ProtocolFamily.R002):
            self.target.protocol_family = family
            self.assertEqual((await self.run_op())['reason'], 'legacy_protocol_required')
        self.target.protocol_family = cap.ProtocolFamily.LEGACY
        await self.backend.stop()
        self.assertEqual((await self.run_op())['reason'], 'entry_stopped')
        self.assertEqual(FakeSession.instances, [])

    async def test_active_media_and_ring_busy_no_pause_or_resume(self):
        for attribute, value in (('session', object()), ('thread', Mock(is_alive=Mock(return_value=True))),
                                  ('consumers', {'synthetic-consumer'})):
            setattr(self.target, attribute, value)
            self.assertEqual((await self.run_op())['reason'], 'busy')
            setattr(self.target, attribute, set() if attribute == 'consumers' else None)
        ring = self.target.ring_listener
        ring.session = object()
        ring.pause_for_control, ring.resume_after_control = Mock(), Mock()
        self.assertEqual((await self.run_op())['reason'], 'busy')
        ring.session = None
        ring.thread = Mock(is_alive=Mock(return_value=True))
        self.assertEqual((await self.run_op())['reason'], 'busy')
        ring.pause_for_control.assert_not_called()
        ring.resume_after_control.assert_not_called()
        self.assertEqual(FakeSession.instances, [])

    async def test_locks_and_snapshot_tasks_are_busy(self):
        await self.target.lock.acquire()
        self.assertEqual((await self.run_op())['reason'], 'busy')
        self.target.lock.release()
        self.target.control.lock.acquire()
        self.assertEqual((await self.run_op())['reason'], 'busy')
        self.target.control.lock.release()
        self.target.manual_snapshot = SimpleNamespace(_task=Mock(done=Mock(return_value=False)))
        self.assertEqual((await self.run_op())['reason'], 'busy')
        self.assertEqual(FakeSession.instances, [])

    async def test_success_is_single_correlated_query_and_private_report(self):
        identity = user()
        result = await self.backend.execute('version_469', confirm=True, user=identity, entity_id='camera.synthetic')
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(result['response_correlated'])
        self.assertEqual(result['requests_attempted'], {'501': 1, '509': 1, '5005': 1})
        self.assertTrue(result['cleanup']['tcp_closed'])
        identity.permissions.check_entity.assert_called_once_with('camera.synthetic', 'control')
        self.assertEqual((await self.run_op())['reason'], 'already_attempted_for_loaded_entry')
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(self.target.entry.data, {'host': '192.0.2.20', 'username': 'synthetic-user', 'password': 'synthetic-password'})
        for marker in ('192.0.2.20', 'synthetic-password', 'SYNTHETIC-NOT-A-DEVICE', 'synthetic-private-response'):
            self.assertNotIn(marker, json.dumps(result))
        self.assertFalse(hasattr(self.target, 'metadata'))
        self.assertIsNone(self.backend._session)

    async def test_failure_consumes_trial_no_retry_and_sanitizes_exception(self):
        FakeSession.connect_error = ConnectionRefusedError('192.0.2.20 synthetic-password')
        result = await self.run_op()
        self.assertEqual(result['error_type'], 'ConnectionRefusedError')
        self.assertEqual(result['last_stage'], 'tcp_connecting')
        self.assertEqual((await self.run_op())['reason'], 'already_attempted_for_loaded_entry')
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertNotIn('synthetic-password', json.dumps(result))
        self.assertFalse(self.target.control.lock.locked())
        self.assertFalse(self.target.lock.locked())

    async def test_unrelated_alarm_never_correlated(self):
        self.private.stop()
        with patch.object(experimental, 'decode_private_reply', return_value=(100, version_reply(kind=14854))):
            result = await self.run_op()
        self.assertEqual(result['status'], 'not_validated')
        self.assertFalse(result['response_correlated'])
        self.assertEqual(result['unrelated_private_responses'], 1)
        self.assertEqual(result['error_type'], 'TimeoutError')
        self.assertTrue(result['cleanup']['session_stop_sent'])

    async def test_bad_private_reply_fails_closed_without_second_query(self):
        self.private.stop()
        with patch.object(experimental, 'decode_private_reply', side_effect=protected.ProtocolError('private marker')):
            result = await self.run_op()
        self.assertEqual(result['status'], 'not_validated')
        self.assertEqual(result['requests_attempted'].get('509'), 1)
        self.assertNotIn('private marker', json.dumps(result))

    async def test_concurrent_cancellation_and_unload_finish_cleanup(self):
        FakeSession.block = True
        first = asyncio.create_task(self.run_op())
        await asyncio.to_thread(FakeSession.started.wait, 1)
        self.assertTrue(self.target.lock.locked())
        self.assertTrue(self.target.control.lock.locked())
        self.assertEqual((await self.run_op('additional_camera'))['reason'], 'busy')
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertTrue(FakeSession.instances[0].closed.is_set())
        self.assertIsNone(self.backend._task)
        self.assertFalse(self.target.lock.locked())
        self.assertFalse(self.target.control.lock.locked())
        self.assertEqual(len(FakeSession.instances), 1)
        await self.backend.stop()

    async def test_stop_during_observation_waits_for_worker(self):
        FakeSession.block = True
        first = asyncio.create_task(self.run_op())
        await asyncio.to_thread(FakeSession.started.wait, 1)
        await self.backend.stop()
        result = await first
        self.assertTrue(result['cleanup']['tcp_closed'])
        self.assertIsNone(self.backend._task)
        self.assertEqual((await self.run_op('additional_camera'))['reason'], 'entry_stopped')

    async def test_repeated_cancel_does_not_release_locks_before_cleanup(self):
        FakeSession.block = True
        FakeSession.cleanup_hold = True
        first = asyncio.create_task(self.run_op())
        await asyncio.to_thread(FakeSession.started.wait, 1)
        first.cancel()
        await asyncio.to_thread(FakeSession.cleanup_started.wait, 1)
        first.cancel()
        await asyncio.sleep(0)
        self.assertFalse(first.done())
        self.assertIsNotNone(self.backend._task)
        self.assertTrue(self.target.lock.locked())
        self.assertTrue(self.target.control.lock.locked())
        self.assertEqual((await self.run_op('additional_camera'))['reason'], 'busy')
        FakeSession.cleanup_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertIsNone(self.backend._task)
        self.assertTrue(FakeSession.instances[0].closed.is_set())
        self.assertFalse(self.target.lock.locked())
        self.assertFalse(self.target.control.lock.locked())

    async def test_repeated_unload_cancel_drains_before_return(self):
        FakeSession.block = True
        FakeSession.cleanup_hold = True
        first = asyncio.create_task(self.run_op())
        await asyncio.to_thread(FakeSession.started.wait, 1)
        unloading = asyncio.create_task(self.backend.stop())
        await asyncio.to_thread(FakeSession.cleanup_started.wait, 1)
        unloading.cancel()
        await asyncio.sleep(0)
        unloading.cancel()
        await asyncio.sleep(0)
        self.assertFalse(unloading.done())
        self.assertTrue(self.target.lock.locked())
        FakeSession.cleanup_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await unloading
        await first
        self.assertTrue(FakeSession.instances[0].closed.is_set())
        self.assertIsNone(self.backend._task)
        self.assertFalse(self.target.lock.locked())

    async def test_additional_camera_timeout_always_native_cleanup_no_retry(self):
        FakeSession.reads = [TimeoutError('synthetic media timeout')]
        result = await self.run_op('additional_camera')
        instance = FakeSession.instances[0]
        self.assertEqual((instance.channel, instance.stream, instance.mode), (18, 1, 2))
        self.assertTrue(instance.v1_video_receive)
        self.assertEqual(instance.sent_counts, {501: 1, 5007: 1, 5009: 1, 5005: 1})
        self.assertTrue(result['cleanup']['stop_av_sent'])
        self.assertFalse(result['cleanup']['stop_av_response_received'])
        self.assertEqual(result['source_mapping_status'], 'not_validated')
        self.assertNotIn('505', result['requests_attempted'])

    async def test_additional_camera_synthetic_decode_three_frames(self):
        import av
        from fractions import Fraction
        encoder = av.CodecContext.create('libx264', 'w')
        encoder.width = encoder.height = 64
        encoder.pix_fmt = 'yuv420p'
        encoder.time_base = Fraction(1, 10)
        encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
        packets = []
        for index in range(3):
            frame = av.VideoFrame(64, 64, 'yuv420p')
            frame.pts = index
            for plane in frame.planes:
                plane.update(bytes([64 + index * 20]) * plane.buffer_size)
            packets.extend(bytes(packet) for packet in encoder.encode(frame))
        self.target.variant = cap.DeviceVariant.R001
        FakeSession.reads = [[(5008, b'synthetic-start')]] + [[(100, packet)] for packet in packets] + [[(5010, b'synthetic-stop')]]
        with patch.object(experimental, 'decode_start_av_reply', return_value=(100, 1, 0)), \
             patch.object(experimental, 'decode_stop_av_reply', return_value=(100, 1, 0)):
            result = await self.run_op('additional_camera')
        self.assertEqual(result['decoded_frames'], 3)
        self.assertEqual(result['decoded_dimensions'], [64, 64])
        self.assertTrue(result['start_accepted'])
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['source_mapping_status'], 'not_validated')
        self.assertTrue(result['cleanup']['stop_av_response_received'])
        self.assertEqual(result['cleanup']['stop_av_result'], 1)
        self.assertNotIn('raw', json.dumps(result))
        self.assertNotIn('image', result)


def synthetic_elf(bits=32, include_symbol=True, defined=True):
    """Three-section synthetic ELF, not an APK/hardware fixture."""
    endian = '<'
    header = struct.Struct(endian + ('HHIIIIIHHHHHH' if bits == 32 else 'HHIQQQIHHHHHH'))
    section = struct.Struct(endian + ('IIIIIIIIII' if bits == 32 else 'IIQQQQIIQQ'))
    symbol = struct.Struct(endian + ('IIIBBH' if bits == 32 else 'IBBHQQ'))
    ident = b'\x7fELF' + bytes([1 if bits == 32 else 2, 1, 1]) + bytes(9)
    strings = b'\0' + (udt._SYMBOLS['udt_constructor'] if include_symbol else b'no_udt') + b'\0'
    sym_start = 16 + header.size
    str_start = sym_start + symbol.size
    sh_start = str_start + len(strings)
    values = (3, 40 if bits == 32 else 62, 1, 0, 0, sh_start, 0,
              16 + header.size, 0, 0, section.size, 3, 0)
    entry = symbol.pack(1, 0, 0, 0x12, 0, 1 if defined else 0) if bits == 32 else symbol.pack(1, 0x12, 0, 1 if defined else 0, 0, 0)
    sym_section = section.pack(0, 11, 0, 0, sym_start, symbol.size, 2, 0, 4, symbol.size)
    str_section = section.pack(0, 3, 0, 0, str_start, len(strings), 0, 0, 1, 0)
    return ident + header.pack(*values) + entry + strings + bytes(section.size) + sym_section + str_section


class OfflineUDTTests(unittest.TestCase):
    def analyze(self, data, **kwargs):
        return udt.analyze_elf(data, confirm=True, protocol_family='legacy_owsp', legacy_discovery_absent=True, **kwargs)

    def test_real_symbol_tables_not_arbitrary_string_search(self):
        for bits in (32, 64):
            result = self.analyze(synthetic_elf(bits))
            self.assertTrue(result['fixed_symbols_observed']['udt_constructor'])
            self.assertEqual(result['status'], 'not_validated')
            self.assertFalse(result['active_probe_available'])
            self.assertEqual(result['requests_sent'], 0)
            self.assertNotIn('raw', json.dumps(result))
        result = self.analyze(synthetic_elf(include_symbol=False) + udt._SYMBOLS['udt_constructor'])
        self.assertFalse(result['fixed_symbols_observed']['udt_constructor'])
        self.assertFalse(self.analyze(synthetic_elf(defined=False))['fixed_symbols_observed']['udt_constructor'])

    def test_confirmation_family_and_discovery_absence_guards(self):
        for kwargs in ({'confirm': False}, {'protocol_family': 'connect3_qv_experimental'}, {'legacy_discovery_absent': False}):
            settings = {'confirm': True, 'protocol_family': 'legacy_owsp', 'legacy_discovery_absent': True}
            settings.update(kwargs)
            with self.subTest(settings=kwargs), self.assertRaises((ValueError, PermissionError)):
                udt.analyze_elf(synthetic_elf(), **settings)

    def test_malformed_truncated_and_oversize_elf(self):
        for data in (b'not-an-elf', synthetic_elf()[:-1], b'x' * (udt.MAX_ELF_SIZE + 1),
                     synthetic_elf()[:16] + bytes(60)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                self.analyze(data)


if __name__ == '__main__':
    unittest.main()
