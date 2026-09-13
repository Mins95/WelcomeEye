"""Offline-only regression for V1 listener-to-control handover timing."""
import types
import unittest
from unittest.mock import patch

from test_control_coordination import InlineLoop, Tracker, wait_for, ring, control, protected


class V1ControlHandoverRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tracker = Tracker()
        entry = types.SimpleNamespace(unique_id='TESTUID000', data={
            'host': 'unused', 'username': 'unused', 'password': 'unused'})
        self.states = []
        self.listener = ring.RingListener(
            InlineLoop(), entry, lambda msg: None, lambda *args: self.states.append(args)
        )
        hub = types.SimpleNamespace(
            entry=entry,
            ring_listener=self.listener,
            device_model='WelcomeEye Connect V1',
            connected=False,
        )
        self.controller = control.DeviceController(hub)
        self.sleep_calls = []
        patches = [
            patch.object(ring, 'Session', self.tracker.ring_factory),
            patch.object(ring, 'select', types.SimpleNamespace(select=self.tracker.select)),
            patch.object(control, 'Session', self.tracker.control_factory),
            patch.object(control.time, 'sleep', lambda seconds: self.sleep_calls.append(seconds)),
            patch.object(control, 'build_unlock_request', lambda uid, profile, now, pwd, output:
                         protected.owsp(protected.tlv(505, bytes([output])))),
            patch.object(control, 'decode_unlock_reply', lambda uid, body: (0, 1, 0)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        self.tracker.connect_release.set()
        self.tracker.command_release.set()
        self.listener.close()
        if self.listener.thread:
            self.listener.thread.join(3)
        self.controller.close()

    def _start_listener(self):
        self.listener.start()
        wait_for(lambda: (True, None) in self.states)

    def test_settle_interval_occurs_after_listener_release(self):
        self._start_listener()
        self.controller.unlock(0)
        self.assertIn(control.V1_CONTROL_SETTLE_SECONDS, self.sleep_calls)
        self.assertEqual(self.controller.v1_settle_wait_count, 1)
        self.assertEqual(self.controller.v1_settle_requested_ms, 1000)
        events = self.tracker.events
        self.assertLess(events.index('ring_closed'), events.index('control_opened'))
        self.assertEqual(self.controller.request_sent_count, 1)

    def test_session_open_timeout_still_sends_nothing(self):
        self._start_listener()
        self.tracker.control_connect_error = TimeoutError('timed out')
        with self.assertRaises(TimeoutError):
            self.controller.unlock(0)
        self.assertEqual(self.controller.v1_settle_wait_count, 1)
        self.assertEqual(self.controller.request_send_attempt_count, 0)
        self.assertEqual(self.controller.request_sent_count, 0)
        self.assertEqual(self.tracker.packets, [])
        self.assertEqual(self.controller.last_error_stage, 'opening_session')


if __name__ == '__main__':
    unittest.main()
