"""Synthetic preflight checks: no native connection or physical bell is simulated."""
import json
import unittest
from unittest.mock import patch

from load_integration import load

listener = load('connect3.ring_listener')


class AutonomousDoorbellDiagnosticTests(unittest.TestCase):
    def test_start_refuses_without_socket_timer_or_background_task(self):
        diagnostic = listener.AutonomousDoorbellDiagnostic()
        with patch('asyncio.open_connection') as connection, \
                patch('socket.socket') as socket, \
                patch('asyncio.get_running_loop') as loop, \
                patch('asyncio.create_task') as task:
            result = diagnostic.execute('start')
            connection.assert_not_called()
            socket.assert_not_called()
            loop.assert_not_called()
            task.assert_not_called()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['last_error_reason'], 'native_login_protocol_unestablished')
        self.assertEqual(result['requested_duration_seconds'], 90)
        self.assertEqual(result['effective_duration_seconds'], 0)
        self.assertEqual(result['last_stage'], 'protocol_preflight')
        self.assertFalse(result['listen_established'])
        self.assertFalse(result['video_session_created'])
        self.assertFalse(result['microphone_session_created'])
        self.assertFalse(result['credentials_sent'])
        self.assertEqual(result['ring_events_emitted'], 0)

    def test_status_and_stop_never_claim_a_completed_ninety_second_observation(self):
        diagnostic = listener.AutonomousDoorbellDiagnostic()
        self.assertEqual(diagnostic.execute('status')['status'], 'not_started')
        started = diagnostic.execute('start')
        reported = diagnostic.execute('status')
        stopped = diagnostic.execute('stop')
        for result in (started, reported, stopped):
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['elapsed_ms'], 0)
            self.assertEqual(result['messages_received'], 0)
            self.assertFalse(result['listening'])
            self.assertFalse(result['physical_ring_confirmed'])
            self.assertEqual(result['events'], [])
        self.assertEqual(stopped['last_error_reason'], started['last_error_reason'])
        self.assertEqual(stopped['close_reason'], 'user_stop')

    def test_only_explicit_start_counts_and_invalid_inputs_leave_state_unchanged(self):
        diagnostic = listener.AutonomousDoorbellDiagnostic()
        for duration in (0, 91, True, 1.5, '90', None):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                diagnostic.execute('start', duration)
        for operation in ('listen', 'mark', '', None, []):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                diagnostic.execute(operation)
        self.assertEqual(diagnostic.start_attempts, 0)
        self.assertEqual(diagnostic.diagnostics()['status'], 'not_started')
        for duration in (1, 90):
            self.assertEqual(diagnostic.execute('start', duration)['status'], 'unavailable')
        diagnostic.execute('status')
        diagnostic.execute('stop')
        self.assertEqual(diagnostic.start_attempts, 2)

    def test_results_are_detached_and_have_no_configured_device_or_secret_fields(self):
        diagnostic = listener.AutonomousDoorbellDiagnostic()
        result = diagnostic.execute('start')
        result['blockers'].clear()
        result['events'].append({'payload': 'PRIVATE'})
        repeated = diagnostic.execute('status')
        self.assertEqual(repeated['blockers'], list(listener.NATIVE_LISTENER_BLOCKERS))
        self.assertEqual(repeated['events'], [])
        exported = json.dumps(repeated)
        for sensitive in ('password', 'stream_key', 'authCode', 'UID', 'PRIVATE', '192.168.'):
            self.assertNotIn(sensitive, exported)
        self.assertIsNone(repeated['transport'])
        self.assertIsNone(repeated['port'])

    def test_shutdown_is_idempotent_and_future_start_refuses_before_io(self):
        for reason in ('integration_unload', 'home_assistant_stop'):
            with self.subTest(reason=reason):
                diagnostic = listener.AutonomousDoorbellDiagnostic()
                diagnostic.execute('start')
                diagnostic.finish(reason)
                diagnostic.finish(reason)
                self.assertEqual(diagnostic.diagnostics()['close_reason'], reason)
                result = diagnostic.execute('start')
                self.assertEqual(result['last_error_reason'], 'integration_unloaded')
                self.assertEqual(result['connection_attempts'], 0)
                self.assertTrue(result['connection_closed'])

    def test_shutdown_reason_cannot_insert_arbitrary_text_into_diagnostics(self):
        diagnostic = listener.AutonomousDoorbellDiagnostic()
        with self.assertRaises(ValueError):
            diagnostic.finish('PRIVATE_DEVICE_IDENTIFIER')
        self.assertNotIn('PRIVATE_DEVICE_IDENTIFIER', json.dumps(diagnostic.diagnostics()))
