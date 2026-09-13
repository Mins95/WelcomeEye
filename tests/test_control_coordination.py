"""Deterministic simulated sessions: never connect to or open a real device."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import struct
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

PACKAGE = 'welcomeeye_coordination_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local')]
sys.modules[PACKAGE] = package
ring = importlib.import_module(PACKAGE + '.ring')
control = importlib.import_module(PACKAGE + '.control')
protected = importlib.import_module(PACKAGE + '.protected')


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for simulated worker')
        threading.Event().wait(.002)


class InlineLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


class Tracker:
    def __init__(self):
        self.lock = threading.RLock()
        self.events, self.packets, self.profiles = [], [], []
        self.ring_live = self.control_live = self.maximum_live = 0
        self.rings = []
        self.connect_entered, self.connect_release = threading.Event(), threading.Event()
        self.command_entered, self.command_release = threading.Event(), threading.Event()
        self.connect_release.set()
        self.command_release.set()
        self.connect_error = self.command_error = self.send_error = None
        self.control_connect_error = None
        self.fail_ring_close = self.fail_control_close = self.fail_diagnostics = False
        self.fail_video = False
        self.control_uid = 'TESTUID000'
        self.initial_ring_reads = []

    def opened(self, kind):
        with self.lock:
            if kind == 'ring': self.ring_live += 1
            else: self.control_live += 1
            self.maximum_live = max(self.maximum_live, self.ring_live + self.control_live)
            self.events.append(kind + '_opened')

    def ring_factory(self, *args, **kwargs):
        assert kwargs == dict(channel=0, stream=3, mode=0)
        item = RingSession(self)
        self.rings.append(item)
        return item

    def control_factory(self, *args, **kwargs):
        self.profiles.append((kwargs['channel'], kwargs['stream'], kwargs['mode']))
        return ControlSession(self, kwargs['channel'])

    def select(self, readers, *_):
        session = readers[0]
        if session.reads:
            return readers, [], []
        session.closed.wait(.01)
        return [], [], []


class RingSession:
    def __init__(self, tracker):
        self.tracker = tracker
        self.info = types.SimpleNamespace(uid='TESTUID000')
        self.sock = self
        self.closed = threading.Event()
        self.opened = False
        self.zero_frame_count = self.keepalive_count = 0
        self.last_keepalive = time.monotonic() - 11
        self.reads = deque(tracker.initial_ring_reads)

    def connect(self):
        self.opened = True
        self.tracker.opened('ring')
        self.tracker.connect_entered.set()
        assert self.tracker.connect_release.wait(3)
        if self.tracker.connect_error:
            raise self.tracker.connect_error
        if self.closed.is_set():
            raise ConnectionAbortedError()
        return [(502, b'login')]

    def read(self):
        item = self.reads.popleft()
        if item == 'padding':
            self.zero_frame_count += 1
            raise TimeoutError()
        return item

    def send_keepalive(self):
        self.keepalive_count += 1
        self.last_keepalive = time.monotonic()

    def framing_diagnostics(self):
        return {'keepalive_count': self.keepalive_count}

    def close(self):
        with self.tracker.lock:
            if self.tracker.fail_ring_close:
                raise OSError('simulated close failure')
            if self.opened:
                self.tracker.ring_live -= 1
                self.tracker.events.append('ring_closed')
                self.opened = False
            self.closed.set()


class ControlSession:
    def __init__(self, tracker, channel):
        self.tracker, self.channel = tracker, channel
        self.info = types.SimpleNamespace(uid=tracker.control_uid)
        self.encryption_profile = 0
        self.sock = self
        self.opened = False

    def connect(self):
        self.opened = True
        self.tracker.opened('control')
        if self.channel == 16 and self.tracker.fail_video:
            raise OSError('video unavailable')
        if self.tracker.control_connect_error:
            raise self.tracker.control_connect_error
        return [(203, b'format')]

    def device_now(self): return 1
    def settimeout(self, timeout): pass

    def sendall(self, packet):
        self.tracker.events.append('send_505')
        self.tracker.packets.append(packet)
        self.tracker.command_entered.set()
        if self.tracker.send_error:
            raise self.tracker.send_error

    def read(self):
        assert self.tracker.command_release.wait(3)
        if self.tracker.command_error:
            raise self.tracker.command_error
        return [(506, b'confirmation')]

    def framing_diagnostics(self):
        if self.tracker.fail_diagnostics:
            raise RuntimeError('diagnostic failure')
        return {}

    def close(self):
        with self.tracker.lock:
            if self.tracker.fail_control_close:
                raise OSError('simulated close failure')
            if self.opened:
                self.tracker.control_live -= 1
                self.tracker.events.append('control_closed')
                self.opened = False


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.tracker = Tracker()
        entry = types.SimpleNamespace(unique_id='TESTUID000', data={
            'host': 'unused', 'username': 'unused', 'password': 'unused'})
        self.states = []
        self.listener = ring.RingListener(InlineLoop(), entry, lambda msg: None,
                                         lambda *args: self.states.append(args))
        self.hub = types.SimpleNamespace(entry=entry, ring_listener=self.listener,
                                        device_model='WelcomeEye Connect V1', connected=False)
        self.controller = control.DeviceController(self.hub)
        patches = [patch.object(ring, 'Session', self.tracker.ring_factory),
                   patch.object(ring, 'select', types.SimpleNamespace(select=self.tracker.select)),
                   patch.object(control, 'Session', self.tracker.control_factory),
                   patch.object(control, 'time', types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None)),
                   patch.object(control, 'build_unlock_request', lambda uid, profile, now, pwd, output:
                                protected.owsp(protected.tlv(505, bytes([output])))),
                   patch.object(control, 'decode_unlock_reply', lambda uid, body: (0, 1, 0))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        self.tracker.fail_ring_close = self.tracker.fail_control_close = False
        self.tracker.connect_release.set()
        self.tracker.command_release.set()
        self.listener.close()
        if self.listener.thread:
            self.listener.thread.join(3)
            self.assertFalse(self.listener.thread.is_alive())
        self.controller.close()
        self.assertEqual(self.tracker.ring_live + self.tracker.control_live, 0)

    def start_ring(self):
        self.listener.start()
        wait_for(lambda: (True, None) in self.states)
        return self.listener.thread

    def assert_resumed_once(self, worker):
        wait_for(lambda: self.listener.resume_reconnected_count == 1)
        self.assertIs(self.listener.thread, worker)
        self.assertEqual(self.tracker.maximum_live, 1)
        self.assertFalse(self.listener.paused_for_control)
        self.assertFalse(self.controller.lock.locked())

    def test_v1_output_0_release_send_once_and_resume(self):
        self._success(0)

    def test_v1_output_1_release_send_once_and_resume(self):
        self._success(1)

    def _success(self, output):
        worker = self.start_ring()
        self.controller.unlock(output)
        self.assert_resumed_once(worker)
        self.assertEqual(len(self.tracker.packets), 1)
        self.assertEqual(protected.parse_tlvs(self.tracker.packets[0][8:]), [(505, bytes([output]))])
        events = self.tracker.events
        self.assertLess(events.index('ring_closed'), events.index('control_opened'))
        self.assertLess(events.index('control_closed'), len(events) - 1)
        self.assertTrue(self.controller.command_started_after_ring_release)

    def test_confirmation_failure_no_retry_and_finally_resume(self):
        worker = self.start_ring()
        self.tracker.command_error = TimeoutError('no confirmation')
        with self.assertRaises(TimeoutError): self.controller.unlock(0)
        self.assert_resumed_once(worker)
        self.assertEqual(len(self.tracker.packets), 1)

    def test_control_connection_failure_before_send_resumes_ring(self):
        worker = self.start_ring()
        self.tracker.control_connect_error = OSError('connection failure')
        with self.assertRaises(OSError): self.controller.unlock(0)
        self.assert_resumed_once(worker)
        self.assertEqual(self.tracker.packets, [])
        self.assertEqual(self.controller.last_error_stage, 'opening_session')

    def test_confirmation_decode_failure_resumes_without_resending(self):
        worker = self.start_ring()
        with patch.object(control, 'decode_unlock_reply', side_effect=ValueError('invalid confirmation')):
            with self.assertRaises(ValueError): self.controller.unlock(0)
        self.assert_resumed_once(worker)
        self.assertEqual(self.controller.decode_failures, 1)
        self.assertEqual(len(self.tracker.packets), 1)

    def test_uncertain_send_is_not_retried_and_cooldown_remains(self):
        worker = self.start_ring()
        self.tracker.send_error = OSError('partial write')
        with self.assertRaises(OSError): self.controller.unlock(1)
        self.assert_resumed_once(worker)
        with self.assertRaises(protected.ProtocolError): self.controller.unlock(1)
        self.assertEqual(len(self.tracker.packets), 1)
        self.assertEqual(self.controller.request_send_attempt_count, 1)
        self.assertEqual(self.controller.request_sent_count, 0)

    def test_pause_waits_for_worker_abandonment_not_only_socket_close(self):
        self.tracker.connect_release.clear()
        self.listener.start()
        self.assertTrue(self.tracker.connect_entered.wait(1))
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(self.controller.unlock, 0)
            wait_for(lambda: self.listener.paused_for_control)
            wait_for(lambda: self.tracker.ring_live == 0)
            self.assertFalse(future.done())
            self.assertNotIn('control_opened', self.tracker.events)
            self.assertIsNotNone(self.listener.session)
            self.tracker.connect_release.set()
            future.result(2)
        self.assertEqual(len(self.tracker.packets), 1)
        self.assertEqual(self.tracker.maximum_live, 1)

    def test_pause_timeout_sends_nothing_and_finally_lifts_barrier(self):
        self.tracker.connect_release.clear()
        self.listener.start()
        self.assertTrue(self.tracker.connect_entered.wait(1))
        pause = self.listener.pause_for_control
        with patch.object(self.listener, 'pause_for_control', lambda timeout: pause(.02)):
            with self.assertRaises(TimeoutError): self.controller.unlock(0)
        self.assertEqual(self.tracker.packets, [])
        self.assertEqual(self.tracker.profiles, [])
        self.assertEqual(self.controller.ring_pause_timeout, 1)
        self.assertEqual(self.controller.ring_resume_requested, 1)
        self.assertFalse(self.listener.paused_for_control)
        self.assertFalse(self.controller.lock.locked())
        self.tracker.connect_release.set()

    def test_real_ring_release_failure_blocks_control_and_reconnect(self):
        self.start_ring()
        self.tracker.fail_ring_close = True
        with self.assertRaises(TimeoutError): self.controller.unlock(0)
        self.assertEqual(self.tracker.packets, [])
        self.assertEqual(self.tracker.profiles, [])
        self.assertTrue(self.listener.paused_for_control)
        self.assertTrue(self.listener.coordination_diagnostics()['release_failed'])
        self.assertFalse(self.controller.lock.locked())

    def test_disconnected_without_worker_is_immediately_available(self):
        self.controller.unlock(0)
        wait_for(lambda: self.listener.resume_reconnected_count == 1)
        self.assertEqual(len(self.tracker.packets), 1)
        self.assertEqual(self.listener.control_pause_timeout_count, 0)
        self.assertEqual(self.listener.connection_attempts, 1)

    def test_disconnected_in_backoff_yields_without_waiting_for_backoff(self):
        self.tracker.connect_error = OSError('offline')
        backoff_entered = threading.Event()
        original = self.listener._wait_backoff
        def backoff(delay):
            backoff_entered.set()
            original(delay)
        with patch.object(self.listener, '_wait_backoff', backoff):
            self.listener.start()
            self.assertTrue(backoff_entered.wait(1))
            self.tracker.connect_error = None
            self.controller.unlock(1)
        self.assertEqual(len(self.tracker.packets), 1)
        self.assertEqual(self.tracker.maximum_live, 1)

    def test_rapid_concurrent_buttons_and_cooldown_send_once(self):
        worker = self.start_ring()
        self.tracker.command_release.clear()
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(self.controller.unlock, 0)
            self.assertTrue(self.tracker.command_entered.wait(1))
            with self.assertRaises(protected.ProtocolError): self.controller.unlock(1)
            self.tracker.command_release.set()
            future.result(2)
        with self.assertRaises(protected.ProtocolError): self.controller.unlock(1)
        self.assert_resumed_once(worker)
        self.assertEqual(len(self.tracker.packets), 1)

    def test_repeated_start_and_resume_do_not_duplicate_worker(self):
        worker = self.start_ring()
        for _ in range(3): self.listener.start()
        self.assertTrue(self.listener.pause_for_control())
        self.listener.resume_after_control()
        self.assert_resumed_once(worker)
        self.assertEqual(self.listener.connection_attempts, 2)

    def test_diagnostics_failure_cannot_prevent_close_or_resume(self):
        worker = self.start_ring()
        self.tracker.fail_diagnostics = True
        self.controller.unlock(0)
        self.assert_resumed_once(worker)
        self.assertEqual(self.controller.cleanup_error_type, 'RuntimeError')

    def test_control_close_failure_keeps_ring_paused_and_unlocks_mutex(self):
        self.start_ring()
        self.tracker.fail_control_close = True
        with self.assertRaises(OSError): self.controller.unlock(0)
        self.assertTrue(self.listener.paused_for_control)
        self.assertFalse(self.controller.lock.locked())
        self.assertEqual(self.listener.connection_attempts, 1)
        self.assertEqual(self.controller.ring_resume_success, 0)
        with self.assertRaises(protected.ProtocolError): self.controller.unlock(0)
        self.assertEqual(len(self.tracker.packets), 1)

    def test_uid_mismatch_never_sends_command_and_resumes_ring(self):
        worker = self.start_ring()
        self.tracker.control_uid = 'WRONG'
        with self.assertRaises(protected.ProtocolError): self.controller.unlock(0)
        self.assert_resumed_once(worker)
        self.assertEqual(self.tracker.packets, [])

    def test_shutdown_during_pause_never_sends_or_restarts(self):
        self.start_ring()
        pause = self.listener.pause_for_control
        def stop_after_pause(timeout):
            result = pause(timeout)
            self.controller.close()
            return result
        with patch.object(self.listener, 'pause_for_control', stop_after_pause):
            with self.assertRaises(protected.ProtocolError): self.controller.unlock(0)
        self.assertEqual(self.tracker.packets, [])
        self.assertFalse(self.controller.lock.locked())

    def test_connect2_idle_active_and_fallback_do_not_pause_listener(self):
        self.hub.device_model = 'WelcomeEye Connect 2'
        with patch.object(self.listener, 'pause_for_control', side_effect=AssertionError('V1 only')):
            for connected, fail_video, expected in [
                (False, False, [(16, 1, 2)]), (True, False, [(0, 3, 0)]),
                (False, True, [(16, 1, 2), (0, 3, 0)])]:
                self.hub.connected = connected
                self.tracker.fail_video = fail_video
                self.tracker.profiles.clear()
                self.controller.last_command = float('-inf')
                before = len(self.tracker.packets)
                self.controller.unlock(0)
                self.assertEqual(self.tracker.profiles, expected)
                self.assertEqual(len(self.tracker.packets) - before, 1)
        self.assertEqual(self.controller.ring_pause_requested, 0)

    def test_padding_timeouts_and_tlv57_keep_listener_alive_without_alarm(self):
        self.tracker.initial_ring_reads = ['padding'] + [[(57, bytes(4))]] * 17
        worker = self.start_ring()
        wait_for(lambda: self.listener.tlv_counts.get(57) == 17)
        self.assertEqual(self.listener.connection_attempts, 1)
        self.assertEqual(self.listener.listen_timeout_count, 1)
        self.assertEqual(self.listener.zero_activity_timeout_count, 1)
        self.assertEqual(self.listener.alarm_type_counts, {})
        self.assertTrue(worker.is_alive())
        self.assertGreaterEqual(self.listener.coordination_diagnostics()['keepalive_count'], 1)

    def test_coordination_diagnostics_contain_only_counts_and_booleans(self):
        self.controller.unlock(0)
        snapshot = self.listener.coordination_diagnostics()
        self.assertTrue(all(type(v) in (bool, int) for v in snapshot.values()))
        self.assertNotIn('TESTUID000', json.dumps(snapshot))

    def test_resume_exception_still_releases_command_mutex(self):
        self.start_ring()
        with patch.object(self.listener, 'resume_after_control', side_effect=RuntimeError('resume failed')):
            with self.assertRaises(RuntimeError): self.controller.unlock(0)
        self.assertFalse(self.controller.lock.locked())
        self.assertEqual(self.tracker.control_live, 0)
        self.assertEqual(len(self.tracker.packets), 1)

    def test_stale_connected_callback_is_dropped_during_pause(self):
        queued = []
        self.listener.loop = types.SimpleNamespace(call_soon_threadsafe=lambda *args: queued.append(args))
        self.listener._emit(self.listener.on_state, True, None)
        self.assertTrue(self.listener.pause_for_control())
        for callback, *args in queued:
            callback(*args)
        self.assertNotIn((True, None), self.states)
        self.assertIn((False, 'ControlPriority'), self.states)

    def test_all_known_alarms_decode_after_padding_and_keepalives(self):
        packets = []
        for index, alarm_type in enumerate((7, 14, 19, 47)):
            raw = json.dumps({'name': 'reportAlarm', 'mode': 'set', 'param': {
                'alarm_type': alarm_type, 'channel': 0, 'timestamp': '20300101000000',
                'timestamp_svr': 1893456000 + index}}).encode() + b'\0'
            inner = protected.owsp(protected.tlv(14854, struct.pack('<I', len(raw)) + raw))
            first, last = b'A' * 16, b'B' * 16
            clear = struct.pack('<Q', 1893456000) + inner
            encrypted = protected.aes_cfb(first[4:13] + last[5:10] + bytes(2),
                                         last[3:9] + bytes(10), clear)
            envelope = protected.rc4(b'TESTUID000', struct.pack('<I', 1) + first + encrypted + last)
            packets.append([(510, envelope)])
        received = []
        self.listener.on_ring = received.append
        self.tracker.initial_ring_reads = ['padding'] + [[(57, bytes(4))]] * 17 + packets
        worker = self.start_ring()
        wait_for(lambda: len(received) == 4)
        self.assertTrue(worker.is_alive())
        self.assertEqual(self.listener.connection_attempts, 1)
        self.assertEqual(self.listener.alarm_type_counts, {7: 1, 14: 1, 19: 1, 47: 1})
        self.assertEqual(self.listener.inner_tlv_counts, {14854: 4})

    def test_refused_auth_is_not_retried_on_resume(self):
        self.tracker.connect_error = ring.AuthenticationError()
        self.listener.start()
        wait_for(lambda: not self.listener.thread.is_alive())
        self.assertTrue(self.listener.pause_for_control(timeout=0))
        self.assertFalse(self.listener.resume_after_control())
        self.assertEqual(self.listener.connection_attempts, 1)
        self.assertFalse(self.listener.paused_for_control)


if __name__ == '__main__': unittest.main()
