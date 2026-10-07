"""Offline trials: single reader, deadlines, no physical commands or ring events."""
import asyncio
import json
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from load_integration import load

trial = load('v1_doorbell_trial')
experimental = load('experimental_diagnostics')
protected = load('protected')
cap = load('capabilities')
ring = load('ring')
REAL_SESSION = trial._TrialSession


def hub():
    return SimpleNamespace(
        entry=SimpleNamespace(data={'host': '192.0.2.20', 'username': 'synthetic-user',
                                    'password': 'synthetic-password'}, unique_id='synthetic-device'),
        protocol_family=cap.ProtocolFamily.LEGACY, variant=cap.DeviceVariant.V1,
        stopped=False, lock=asyncio.Lock(), control=SimpleNamespace(lock=threading.Lock()),
        consumers=set(), session=None, thread=None, loop=asyncio.get_running_loop(),
        ring_listener=SimpleNamespace(session=None, thread=None, enabled=False),
        manual_snapshot=None, ring_image=None, _notify=Mock(), _ring=Mock(),
        hass=SimpleNamespace(bus=SimpleNamespace(async_fire=Mock())))


class FakeSession:
    instances = []
    fail_connect = False
    login_hold = threading.Event()
    cleanup_hold = threading.Event()
    cleanup_started = threading.Event()
    constructed = threading.Event()
    read_started = threading.Event()
    auto_finish = False

    def __init__(self, host, username, password, channel, *, stream, mode, on_parts, cancel_event):
        self.channel, self.stream, self.mode = channel, stream, mode
        self.on_parts, self.cancelled = on_parts, cancel_event
        self.authenticated = False
        self.sock = None
        self.info = SimpleNamespace(uid='synthetic-device')
        self.closed = threading.Event()
        self.connection_stage = 'tcp_connecting'
        self.received_bytes = self.read_count = self.zero_frame_count = self.keepalive_count = 0
        self.partial_frame_at_end = False
        self.sent_counts = {}
        self.network_deadline = time.monotonic() + 12
        self.instances.append(self)
        self.constructed.set()

    def connect(self):
        self.login_hold.wait(2)
        if self.fail_connect:
            raise ConnectionRefusedError('synthetic-password 192.0.2.20 private endpoint')
        if self.cancelled.is_set():
            raise ConnectionAbortedError()
        self.sock = Mock()
        self.authenticated = True
        self.connection_stage = 'authenticated'
        self.sent_counts.update({40: 1, 501: 1})
        self.on_parts([(502, b'synthetic-private-login')], False)

    def begin_observation(self, duration):
        self.duration = duration
        self.network_deadline = time.monotonic() + duration

    def read(self):
        self.read_started.set()
        if self.auto_finish:
            self.network_deadline = time.monotonic() - 1
            raise TimeoutError()
        self.cancelled.wait(2)
        if self.cancelled.is_set():
            raise ConnectionAbortedError()
        raise ConnectionError('synthetic-password private payload')

    def cancel(self):
        self.cancelled.set()

    def begin_cleanup(self):
        pass

    def send_session_stop(self):
        self.sent_counts[5005] = self.sent_counts.get(5005, 0) + 1

    def close(self):
        self.cleanup_started.set()
        self.cleanup_hold.wait(2)
        self.closed.set()
        self.sock = None


async def wait_for(event):
    if not await asyncio.to_thread(event.wait, 2):
        raise AssertionError('Synthetic worker did not reach its checkpoint')


class WireTests(unittest.TestCase):
    def session(self, stream=3):
        session = trial._TrialSession('192.0.2.20', 'synthetic', 'synthetic', 0,
                                    stream=stream, mode=0, on_parts=Mock())
        session.sock = Mock()
        return session

    def test_only_login_keepalive_session_stop_can_be_sent(self):
        session = self.session()
        for kind in (505, 507, 509, 5007, 5009, 5006, 433, 14853):
            with self.subTest(kind=kind), self.assertRaises(protected.ProtocolError):
                session.send_packet(protected.owsp(protected.tlv(kind, b'x')))
        for kind, body in ((49, b'bad'), (5005, b'x')):
            with self.assertRaises(protected.ProtocolError):
                session.send_packet(protected.owsp(protected.tlv(kind, body)))
        session.sock.sendall.assert_not_called()
        login = protected.owsp(protected.tlv(40, bytes(4)) + protected.tlv(501, b'synthetic'))
        session.send_packet(login)
        session.send_keepalive()
        session.send_session_stop()
        self.assertEqual(session.sent_counts, {40: 1, 501: 1, 49: 1, 5005: 1})
        self.assertEqual(session.sock.sendall.call_count, 3)

    def test_partial_send_consumes_attempt_and_cancellation_allows_cleanup_only(self):
        session = self.session()
        login = protected.owsp(protected.tlv(40, bytes(4)) + protected.tlv(501, b'login'))
        session.sock.sendall.side_effect = BrokenPipeError()
        with self.assertRaises(BrokenPipeError):
            session.send_packet(login)
        with self.assertRaises(protected.ProtocolError):
            session.send_packet(login)
        self.assertEqual(session.sock.sendall.call_count, 1)
        session.sock.sendall.side_effect = None
        session.cancel()
        with self.assertRaises(ConnectionAbortedError):
            session.send_keepalive()
        session.send_session_stop()
        session.close()
        before = session.sent_counts.copy()
        with self.assertRaises(ConnectionAbortedError):
            session.send_keepalive()
        self.assertEqual(session.sent_counts, before)

    def test_fragmented_frame_survives_idle_slices_without_restarting_reader(self):
        session = self.session()
        wire = protected.owsp(protected.tlv(70, b'synthetic'))
        chunks = [wire[:1], TimeoutError(), wire[1:4], wire[4:7], TimeoutError(), wire[7:]]
        session.sock.recv.side_effect = chunks
        session.last_keepalive = time.monotonic()
        self.assertEqual(session.read(), [(70, b'synthetic')])
        self.assertEqual(session.read_count, 1)
        session.on_parts.assert_called_once_with([(70, b'synthetic')], False)
        self.assertEqual(session.received_bytes, len(wire))

    def test_five_second_keepalive_runs_inside_partial_reads(self):
        session = self.session()
        session.authenticated = True
        session.last_keepalive = 4.0
        session.network_deadline = session.read_deadline = 20.0
        session.sock.recv.side_effect = [b'a', TimeoutError(), b'bcd']
        with patch.object(trial.time, 'monotonic', return_value=10.0):
            self.assertEqual(session._exact(4), b'abcd')
        self.assertEqual(session.sent_counts, {49: 1})
        packet = session.sock.sendall.call_args.args[0]
        self.assertEqual(protected.parse_tlvs(packet[8:]), [(49, bytes(4))])

    def test_full_observation_budget_starts_after_login_and_reserves_cleanup(self):
        with patch.object(trial.time, 'monotonic', return_value=100):
            session = self.session()
        self.assertEqual(session.network_deadline, 112)
        with patch.object(trial.time, 'monotonic', return_value=111):
            session.begin_observation(300)
        self.assertEqual(session.read_deadline, 411)
        self.assertEqual(session.deadline, 413)
        with patch.object(trial.time, 'monotonic', return_value=411):
            session.begin_cleanup()
        self.assertEqual(session.read_deadline, 413)

    def test_read_deadline_byte_and_frame_limits_are_terminal(self):
        session = self.session()
        session.read_deadline = time.monotonic() - 1
        session._v1_reading_header = False
        with self.assertRaises(TimeoutError):
            session._exact(4)
        self.assertTrue(session.partial_frame_at_end)
        session = self.session()
        session.read_count = trial.MAX_READS + 1
        with self.assertRaises(trial._ReceiveLimit):
            session._exact(4)
        session = self.session()
        session.received_bytes = trial.MAX_RECEIVED_BYTES
        session.sock.recv.return_value = b'x'
        with self.assertRaises(trial._ReceiveLimit):
            session._exact(1)

    def test_selected_profile_one_discovery_one_tcp_one_login_no_fallback(self):
        for stream in (3, 7):
            session = self.session(stream)
            udp, tcp = Mock(), Mock()
            udp.recvfrom.return_value = (b'synthetic', ('192.0.2.20', 1500))
            info = SimpleNamespace(address='192.0.2.20', protected=True, tcp_port=19000,
                                   uid='synthetic-device')
            login = protected.owsp(protected.tlv(40, bytes(4)) + protected.tlv(501, b'login'))
            with patch.object(experimental.socket, 'socket', return_value=udp), \
                 patch.object(experimental.socket, 'create_connection', return_value=tcp) as connect, \
                 patch.object(experimental, 'decode_discovery', return_value=info), \
                 patch.object(experimental, 'build_protected_login', return_value=login) as build, \
                 patch.object(session, 'read', return_value=[(502, b'login')]), \
                 patch.object(experimental, 'decode_login_reply', return_value=(1, 0, {'AppId': 1}, 100)):
                session.connect()
            self.assertEqual(build.call_args.kwargs, {'channel': 0, 'stream': stream, 'mode': 0})
            self.assertEqual(connect.call_count, 1)
            self.assertEqual(udp.sendto.call_count, 1)
            self.assertEqual(session.sent_counts, {40: 1, 501: 1})


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeSession.instances = []
        FakeSession.fail_connect = FakeSession.auto_finish = False
        FakeSession.login_hold.set()
        FakeSession.cleanup_hold.set()
        for event in (FakeSession.cleanup_started, FakeSession.constructed, FakeSession.read_started):
            event.clear()
        self.fake = patch.object(trial, '_TrialSession', FakeSession)
        self.fake.start()
        self.addCleanup(self.fake.stop)
        self.hub = hub()
        self.backend = trial.V1DoorbellTrial(self.hub)

    async def asyncTearDown(self):
        FakeSession.login_hold.set()
        FakeSession.cleanup_hold.set()
        await self.backend.close()

    async def test_start_waits_for_authenticated_readiness_and_does_not_require_marks(self):
        FakeSession.login_hold.clear()
        starting = asyncio.create_task(self.backend.start(confirm=True, profile='long_connection'))
        await wait_for(FakeSession.constructed)
        self.assertFalse(starting.done())
        self.assertTrue(self.backend.active)
        self.assertTrue(self.hub.lock.locked())
        self.assertTrue(self.hub.control.lock.locked())
        FakeSession.login_hold.set()
        result = await starting
        self.assertEqual(result['status'], 'observing')
        self.assertTrue(result['login_accepted'])
        self.assertEqual(result['duration_seconds'], 300)
        self.assertEqual(result['markers'], [])
        session = FakeSession.instances[0]
        self.assertEqual((session.channel, session.stream, session.mode), (0, 7, 0))
        self.assertEqual(session.duration, 300)

    async def test_confirmation_family_duration_and_existing_cooldown_before_io(self):
        self.assertEqual((await self.backend.start())['reason'], 'explicit_confirmation_required')
        for value in (0, 29, 301, True):
            with self.assertRaises(ValueError):
                await self.backend.start(confirm=True, duration=value)
        with self.assertRaises(ValueError):
            await self.backend.start(confirm=True, profile='unknown')
        self.hub.variant = cap.DeviceVariant.R001
        self.assertEqual((await self.backend.start(confirm=True))['reason'], 'v1_legacy_required')
        self.hub.variant = cap.DeviceVariant.V1
        self.hub._last_v1_stop_started = time.monotonic()
        self.assertEqual((await self.backend.start(confirm=True))['reason'], 'previous_session_settling')
        self.assertEqual(FakeSession.instances, [])

    async def test_busy_media_capture_ring_and_output_do_not_start(self):
        for field, value in (('consumers', {'viewer'}), ('session', object()),
                             ('thread', SimpleNamespace(is_alive=lambda: True)),
                             ('manual_snapshot', SimpleNamespace(_task=SimpleNamespace(done=lambda: False))),
                             ('ring_image', SimpleNamespace(_worker=SimpleNamespace(done=lambda: False)))):
            before = getattr(self.hub, field)
            setattr(self.hub, field, value)
            self.assertEqual((await self.backend.start(confirm=True))['reason'], 'busy')
            setattr(self.hub, field, before)
        self.hub.ring_listener.enabled = True
        self.assertEqual((await self.backend.start(confirm=True))['reason'], 'busy')
        self.hub.ring_listener.enabled = False
        self.hub.control.lock.acquire()
        try:
            self.assertEqual((await self.backend.start(confirm=True))['reason'], 'busy')
        finally:
            self.hub.control.lock.release()
        self.assertEqual(FakeSession.instances, [])

    async def test_second_start_refused_and_final_snapshot_retained_after_cleanup(self):
        await self.backend.start(confirm=True)
        self.assertEqual((await self.backend.start(confirm=True))['reason'], 'busy')
        result = await self.backend.stop()
        self.assertEqual(result['status'], 'stopped')
        self.assertFalse(result['active'])
        self.assertEqual(result['cleanup'], {'session_stop_attempted': True,
            'session_stop_sent': True, 'tcp_closed': True, 'error_types': []})
        self.assertEqual(result['requests_attempted'], {'40': 1, '501': 1, '5005': 1})
        self.assertEqual(result, self.backend.snapshot())
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertFalse(self.hub.lock.locked())
        self.assertFalse(self.hub.control.lock.locked())

    async def test_denials_persist_while_idle_and_use_only_fixed_metadata(self):
        self.backend._created = time.monotonic() - 1
        self.assertEqual(self.backend.mark()['reason'], 'not_observing')
        result = await self.backend.start()
        report = self.backend.snapshot()
        self.assertEqual(report['status'], 'idle')
        self.assertEqual(report['denial_count'], 2)
        self.assertEqual(report['last_denial']['operation'], 'start')
        self.assertEqual(report['last_denial']['reason'], 'explicit_confirmation_required')
        self.assertGreaterEqual(report['last_denial']['elapsed_since_controller_created_ms'], 1000)
        result['last_denial']['reason'] = 'changed by caller'
        self.assertEqual(self.backend.snapshot()['last_denial']['reason'], 'explicit_confirmation_required')
        self.backend._denied('private-operation', 'synthetic-password')
        report = self.backend.snapshot()
        self.assertEqual(report['last_denial']['operation'], 'other')
        self.assertEqual(report['last_denial']['reason'], 'other')
        self.assertEqual(report['denial_count'], 3)
        self.assertEqual(FakeSession.instances, [])

    async def test_denials_preserve_active_and_final_observation_and_survive_next_start(self):
        await self.backend.start(confirm=True)
        self.backend._parts([(70, b'private-payload-marker')], True)
        before = self.backend.snapshot()
        refusal = await self.backend.start(confirm=True)
        active = self.backend.snapshot()
        self.assertEqual(refusal['reason'], 'busy')
        self.assertEqual(active['status'], 'observing')
        self.assertEqual(active['events'], before['events'])
        self.assertEqual(active['top_level_counts'], before['top_level_counts'])
        self.assertEqual(active['last_denial']['reason'], 'busy')
        finished = await self.backend.stop()
        self.assertEqual(self.backend.mark()['reason'], 'not_observing')
        self.hub.consumers.add('viewer')
        self.assertEqual((await self.backend.start(confirm=True))['reason'], 'busy')
        retained = self.backend.snapshot()
        for key, value in finished.items():
            if key not in ('denial_count', 'last_denial'):
                self.assertEqual(retained[key], value, key)
        self.assertEqual(retained['denial_count'], 3)
        self.hub.consumers.clear()
        restarted = await self.backend.start(confirm=True)
        self.assertEqual(restarted['status'], 'observing')
        self.assertEqual(restarted['denial_count'], 3)
        self.assertEqual(restarted['last_denial'], retained['last_denial'])

    async def test_event_history_keeps_start_and_recent_late_alarm_in_sequence_order(self):
        await self.backend.start(confirm=True)
        for _ in range(200):
            self.backend._parts([(70, b'private-payload-marker')], True)
        self.backend._observation_started = time.monotonic() - 299

        def decode(uid, kind, body, observer=None):
            observer.record('inner_tlv', kind=14854, length=123)
            return [SimpleNamespace(alarm_type=14, message=None)], {14854: 1}

        with patch.object(trial, 'decode_alarm_observations', side_effect=decode):
            self.backend._parts([(510, b'private-late-candidate')], True)
        report = await self.backend.stop()
        total = report['events_total']
        self.assertEqual(len(report['events']), trial.MAX_EVENTS)
        self.assertEqual(report['events_dropped'], total - trial.MAX_EVENTS)
        self.assertEqual([event['sequence'] for event in report['events']],
                         list(range(1, 33)) + list(range(total - 95, total + 1)))
        self.assertEqual(report['events'][0]['phase'], 'login')
        self.assertEqual(report['events'][-1]['alarm_type'], 14)
        self.assertGreaterEqual(report['events'][-1]['elapsed_ms'], 299000)
        self.assertEqual(report['inner_tlv_counts'], {'14854': 1})
        self.assertEqual(report['alarm_candidates'], 1)
        self.assertEqual(report['ring_events_emitted'], 0)

    async def test_bad_json_retains_tlv_metadata_and_sanitized_error_timing(self):
        await self.backend.start(confirm=True)
        for _ in range(40):
            self.backend._parts([(70, b'private-payload-marker')], True)
        bad_json = b'{"synthetic-password":"192.0.2.20","synthetic-device":'
        payload = struct.pack('<I', len(bad_json)) + bad_json
        inner = protected.owsp(protected.tlv(40000, b'private-payload-marker')
                               + protected.tlv(14854, payload))
        with patch.object(ring, 'decode_private_reply', return_value=(0, inner)):
            self.backend._parts([(510, b'private-outer')], True)
        report = self.backend.snapshot()
        self.assertEqual(report['inner_tlv_counts'], {'40000': 1, '14854': 1})
        self.assertEqual(report['decode_failures'], 1)
        self.assertEqual(report['decode_error_counts'], {'invalid_json': 1})
        error = report['last_decode_error']
        self.assertEqual(error['event'], 'decode_error')
        self.assertEqual(error['category'], 'invalid_json')
        self.assertEqual(error['error_type'], 'JSONDecodeError')
        self.assertEqual(error['phase'], 'observation')
        self.assertEqual(error['decode_phase'], 'inner_payload')
        self.assertIsInstance(error['elapsed_ms'], int)
        self.assertEqual(report['events'][-1], error)
        for _ in range(200):
            self.backend._parts([(70, b'private-payload-marker')], True)
        retained = self.backend.snapshot()
        self.assertEqual(retained['last_decode_error'], error)
        self.assertNotIn(error, retained['events'])
        encoded = json.dumps(retained)
        for secret in ('synthetic-password', '192.0.2.20', 'synthetic-device',
                       'private-payload-marker', 'private-outer'):
            self.assertNotIn(secret, encoded)

    async def test_decode_error_categories_are_fixed_and_bounded_even_for_subclasses(self):
        await self.backend.start(confirm=True)
        private_error = type('synthetic-password-192.0.2.20', (ValueError,), {})
        failures = (
            (protected.ProtocolError('private envelope'), 'invalid_envelope', 'ProtocolError'),
            (UnicodeError('private text'), 'invalid_text', 'UnicodeError'),
            (struct.error('private structure'), 'invalid_structure', 'struct.error'),
            (TypeError('private type'), 'invalid_value_type', 'TypeError'),
            (RecursionError('private nesting'), 'nesting_limit', 'RecursionError'),
            (private_error('private value'), 'invalid_value', 'ValueError'),
        )
        for exc, category, error_type in failures:
            with patch.object(trial, 'decode_alarm_observations', side_effect=exc):
                self.backend._parts([(510, b'private-payload-marker')], True)
            error = self.backend.snapshot()['last_decode_error']
            self.assertEqual(error['category'], category)
            self.assertEqual(error['error_type'], error_type)
            self.assertEqual(error['decode_phase'], 'private_envelope_or_tlvs')
        report = self.backend.snapshot()
        self.assertEqual(report['decode_failures'], len(failures))
        self.assertEqual(report['decode_error_counts'], {category: 1 for _, category, _ in failures})
        self.assertLessEqual(len(report['decode_error_counts']), len(trial._DECODE_ERRORS))
        for value in ('private envelope', 'private text', 'private structure', 'private type',
                      'private nesting', 'private value', 'private-payload-marker'):
            self.assertNotIn(value, json.dumps(report))
        self.assertNotIn('synthetic-password', json.dumps(report))

    async def test_start_failure_is_sanitized_and_not_retried(self):
        FakeSession.fail_connect = True
        result = await self.backend.start(confirm=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['last_stage'], 'tcp_connecting')
        self.assertEqual(result['error_type'], 'ConnectionRefusedError')
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertNotIn('synthetic-password', json.dumps(result))
        self.assertNotIn('192.0.2.20', json.dumps(result))
        self.assertFalse(result['cleanup']['session_stop_attempted'])

    async def test_repeated_stop_cancellation_retains_locks_until_cleanup_settles(self):
        await self.backend.start(confirm=True)
        FakeSession.cleanup_hold.clear()
        stopping = asyncio.create_task(self.backend.stop())
        await wait_for(FakeSession.cleanup_started)
        stopping.cancel()
        await asyncio.sleep(0)
        stopping.cancel()
        await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        self.assertTrue(self.hub.lock.locked())
        self.assertTrue(self.hub.control.lock.locked())
        FakeSession.cleanup_hold.set()
        with self.assertRaises(asyncio.CancelledError):
            await stopping
        self.assertFalse(self.hub.lock.locked())
        self.assertFalse(self.backend.active)

    async def test_cancelling_start_during_login_drains_worker_and_disallows_post_close_start(self):
        FakeSession.login_hold.clear()
        starting = asyncio.create_task(self.backend.start(confirm=True))
        await wait_for(FakeSession.constructed)
        starting.cancel()
        FakeSession.login_hold.set()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertFalse(self.backend.active)
        self.assertFalse(self.hub.lock.locked())
        await self.backend.close()
        self.assertEqual((await self.backend.start(confirm=True))['reason'], 'entry_stopped')

    async def test_duration_completion_is_not_a_positive_doorbell_result(self):
        FakeSession.auto_finish = True
        await self.backend.start(confirm=True)
        await self.backend._task
        result = self.backend.snapshot()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result'], 'not_validated')
        self.assertEqual(result['reason'], 'duration_elapsed')
        self.assertEqual(result['ring_events_emitted'], 0)

    async def test_real_reader_payload_cutoff_finishes_without_reusing_partial_frame(self):
        class ExpirySession(REAL_SESSION):
            def connect(self):
                self.authenticated = True
                self.info = SimpleNamespace(uid='synthetic-device')
                self.sock = Mock()
                self.last_keepalive = time.monotonic()
                self.connection_stage = 'authenticated'
                self.header_sent = False
                self.sock.recv.side_effect = self.receive

            def receive(self, size):
                if not self.header_sent:
                    self.header_sent = True
                    return struct.pack('>I', 8)
                self.network_deadline = self.read_deadline = time.monotonic() - 1
                raise TimeoutError()

        with patch.object(trial, '_TrialSession', ExpirySession):
            await self.backend.start(confirm=True)
            await self.backend._task
        result = self.backend.snapshot()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['reason'], 'duration_elapsed')
        self.assertTrue(result['partial_frame_at_end'])
        self.assertTrue(result['cleanup']['tcp_closed'])
        self.assertEqual(result['requests_attempted'], {'5005': 1})

    async def test_candidate_types_and_markers_are_metadata_only_and_bounded(self):
        await self.backend.start(confirm=True)
        message = SimpleNamespace(channel=1, timestamp='private-clock', timestamp_svr=999)
        values = [SimpleNamespace(alarm_type=14, message=message),
                  SimpleNamespace(alarm_type=999999, message=None)]

        def decode(uid, kind, body, observer=None):
            observer.record('inner_tlv', kind=14854, length=123)
            observer.record('inner_tlv', kind=14854, length=123)
            return values, {14854: 2}

        with patch.object(trial, 'decode_alarm_observations', side_effect=decode):
            for _ in range(trial.MAX_EVENTS + 1):
                self.backend._parts([(510, b'private-payload-marker')], True)
        for kind in range(1000, 1100):
            self.backend._parts([(kind, b'private-payload-marker')], True)
            trial._AlarmMetadataObserver(self.backend).record('inner_tlv', kind=kind, length=123)
        for _ in range(trial.MAX_MARKERS):
            self.backend.mark()
        self.assertEqual(self.backend.mark()['reason'], 'marker_limit')
        report = self.backend.snapshot()
        self.assertEqual(len(report['events']), trial.MAX_EVENTS)
        self.assertGreater(report['events_dropped'], 0)
        self.assertLessEqual(len(report['top_level_counts']), trial.MAX_COUNTER_KEYS)
        self.assertLessEqual(len(report['inner_tlv_counts']), trial.MAX_COUNTER_KEYS)
        self.assertEqual(set(report['alarm_type_counts']), {'14', 'other'})
        self.assertEqual(report['ring_events_emitted'], 0)
        encoded = json.dumps(report)
        for secret in ('private-clock', 'private-payload-marker', 'synthetic-device', 'synthetic-user'):
            self.assertNotIn(secret, encoded)
        self.hub._ring.assert_not_called()
        self.hub.hass.bus.async_fire.assert_not_called()

    async def test_notifications_already_queued_are_ignored_after_unload(self):
        callbacks = []
        self.hub.loop = SimpleNamespace(call_soon_threadsafe=lambda callback: callbacks.append(callback))
        self.backend._notify()
        await self.backend.close()
        for callback in callbacks:
            callback()
        self.hub._notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
