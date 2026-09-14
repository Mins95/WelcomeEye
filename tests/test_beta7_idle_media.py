"""Beta 7 V1 idle-media regressions; fully offline."""
import unittest

from test_v1_video_receive import Clock, I_FRAME, make_session, video_wire


class V1IdleMediaTests(unittest.TestCase):
    def test_clean_header_timeout_keeps_v1_reader_during_output_window(self):
        clock = Clock()
        session = make_session([TimeoutError(), video_wire(100, I_FRAME)], clock)
        session.v1_allow_idle_timeouts = True

        self.assertEqual(session.read(), [])
        self.assertFalse(session._v1_read_failed)
        self.assertFalse(session.closed.is_set())
        self.assertEqual(session.read()[-1], (100, I_FRAME))

    def test_padding_then_idle_timeout_is_not_connection_failure_during_output(self):
        clock = Clock()
        session = make_session([bytes(8), TimeoutError(), video_wire(100, I_FRAME)], clock)
        session.v1_allow_idle_timeouts = True

        self.assertEqual(session.read(), [])
        self.assertEqual(session.zero_frame_count, 2)
        self.assertFalse(session._v1_read_failed)
        self.assertFalse(session.closed.is_set())
        self.assertEqual(session.read()[-1], (100, I_FRAME))

    def test_normal_v1_timeout_behavior_is_unchanged_outside_output_window(self):
        clock = Clock()
        session = make_session([TimeoutError()], clock)

        with self.assertRaises(TimeoutError):
            session.read()
        self.assertFalse(session.v1_allow_idle_timeouts)
        self.assertFalse(session._v1_read_failed)

    def test_connect2_timeout_behavior_is_unchanged(self):
        clock = Clock()
        session = make_session([TimeoutError()], clock, False)

        with self.assertRaises(TimeoutError):
            session.read()
        self.assertFalse(session.v1_video_receive)


if __name__ == "__main__":
    unittest.main()
