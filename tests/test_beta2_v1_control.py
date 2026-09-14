"""Keep the former direct-session regression on Connect 2; V1 uses beta5 tests."""
import time
import types
import unittest
from unittest.mock import patch

from test_control_coordination import Tracker, control, protected


class DirectControlRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tracker = Tracker()
        entry = types.SimpleNamespace(unique_id='TESTUID000', data={
            'host': 'unused', 'username': 'unused', 'password': 'unused'})
        listener = types.SimpleNamespace(
            pause_for_control=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError('direct V1 control must not pause the doorbell listener')
            ),
            resume_after_control=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError('direct V1 control must not resume the doorbell listener')
            ),
        )
        hub = types.SimpleNamespace(
            entry=entry,
            ring_listener=listener,
            device_model='WelcomeEye Connect 2',
            connected=True,
        )
        self.controller = control.DeviceController(hub)
        self.sleep_calls = []
        fake_time = types.SimpleNamespace(
            monotonic=time.monotonic,
            sleep=lambda seconds: self.sleep_calls.append(seconds),
        )
        patches = [
            patch.object(control, 'Session', self.tracker.control_factory),
            patch.object(control, 'time', fake_time),
            patch.object(control, 'build_unlock_request', lambda uid, profile, now, pwd, output:
                         protected.owsp(protected.tlv(505, bytes([output])))),
            patch.object(control, 'decode_unlock_reply', lambda uid, body: (0, 1, 0)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        self.tracker.command_release.set()
        self.controller.close()

    def test_connect2_uses_direct_dedicated_control_session_without_settle(self):
        self.controller.unlock(0)
        self.assertEqual(self.sleep_calls, [])
        self.assertEqual(self.tracker.profiles, [(0, 3, 0)])
        self.assertEqual(len(self.tracker.packets), 1)
        self.assertEqual(
            protected.parse_tlvs(self.tracker.packets[0][8:]),
            [(505, bytes([0]))],
        )
        self.assertEqual(self.controller.request_sent_count, 1)
        self.assertEqual(self.controller.response_count, 1)
        self.assertEqual(self.controller.last_result, 1)
        self.assertEqual(self.controller.last_reason, 0)

    def test_session_open_timeout_sends_nothing(self):
        self.tracker.control_connect_error = TimeoutError('timed out')
        with self.assertRaises(TimeoutError):
            self.controller.unlock(0)
        self.assertEqual(self.controller.request_sent_count, 0)
        self.assertEqual(self.tracker.packets, [])
        self.assertEqual(self.controller.last_error_stage, 'opening_session')


if __name__ == '__main__':
    unittest.main()
