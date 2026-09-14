"""Offline regression for Connect V1 doorbell standby mode."""
import importlib
import time
import types
import unittest
from unittest.mock import patch

from test_control_coordination import PACKAGE, Tracker, control, protected

standby_ring = importlib.import_module(PACKAGE + '.standby_ring')


class V1DoorbellStandbyTests(unittest.TestCase):
    def setUp(self):
        self.tracker = Tracker()
        self.entry = types.SimpleNamespace(unique_id='TESTUID000', data={
            'host': 'unused', 'username': 'unused', 'password': 'unused'})
        self.listener = standby_ring.StandbyRingListener()
        self.hub = types.SimpleNamespace(
            entry=self.entry,
            ring_listener=self.listener,
            device_model='WelcomeEye Connect V1',
            connected=False,
            v1_doorbell_standby=True,
        )
        self.controller = control.DeviceController(self.hub)
        fake_time = types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda _seconds: None)
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
        self.controller.close()
        self.listener.close()

    def test_standby_never_starts_ring_network_worker(self):
        self.assertFalse(self.listener.start())
        self.assertIsNone(self.listener.thread)
        self.assertIsNone(self.listener.session)
        self.assertEqual(self.listener.connection_attempts, 0)
        self.assertEqual(self.listener.coordination_diagnostics()['mode'], 'standby')

    def test_connect2_direct_control_does_not_touch_inactive_ring(self):
        # V1's replacement media route is covered in test_beta5_v1_control.
        self.hub.device_model = 'WelcomeEye Connect 2'
        self.hub.connected = True
        self.controller.unlock(0)
        self.assertEqual(self.tracker.ring_live, 0)
        self.assertEqual(self.tracker.maximum_live, 1)
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
        self.assertEqual(self.listener.control_pause_count, 0)
        self.assertEqual(self.listener.resume_count, 0)
        self.assertEqual(self.listener.resume_reconnected_count, 0)
        self.assertIsNone(self.listener.thread)
        self.assertIsNone(self.listener.session)


if __name__ == '__main__':
    unittest.main()
