"""Offline beta 7 regression for control confirmation during media recovery."""
import asyncio
import types
import unittest

from test_beta5_v1_control import FakeMedia, response
from test_control_coordination import control
from test_beta7_stability import FailingDecoder
from test_v1_video_receive import media, synthetic_h264


class ControlResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_survives_h264_decoder_recovery(self):
        session = FakeMedia()
        leases = set()

        async def acquire(lease):
            leases.add(lease)

        async def release(lease):
            leases.discard(lease)

        hub = types.SimpleNamespace(
            entry=types.SimpleNamespace(
                unique_id="TESTUID000",
                data={"host": "unused", "username": "unused", "password": "test-code"},
            ),
            loop=asyncio.get_running_loop(),
            device_model="WelcomeEye Connect V1",
            session=session,
            connected=True,
            acquire=acquire,
            release=release,
        )
        controller = control.DeviceController(hub)
        pipeline = media.MediaPipeline(
            media.StreamFormat(352, 288, 20, 8000, 0x7A19, 1),
            lambda _data: None,
            lambda _data: None,
        )
        packets = synthetic_h264()

        try:
            task = asyncio.create_task(asyncio.to_thread(controller.unlock, 0))
            for _ in range(500):
                if controller.v1_media.pending is not None:
                    break
                await asyncio.sleep(0.001)
            else:
                self.fail("command never queued")

            controller.v1_media.send_pending(session)
            self.assertEqual(len(session.packets), 1)
            self.assertEqual(controller.request_send_attempt_count, 1)
            self.assertEqual(controller.request_sent_count, 1)
            self.assertTrue(session.v1_allow_idle_timeouts)

            pipeline.decoder = FailingDecoder()
            self.assertFalse(pipeline.feed_video(packets[0][1], keyframe=True))
            self.assertTrue(pipeline.video_waiting_for_keyframe)
            self.assertFalse(session.closed.is_set())
            self.assertEqual(len(session.packets), 1)

            controller.v1_media.observe(session, [(506, response())])
            await asyncio.wait_for(task, 2)
            self.assertFalse(session.v1_allow_idle_timeouts)
            self.assertEqual(controller.response_count, 1)
            self.assertEqual(controller.last_result, 1)
            self.assertEqual(controller.request_send_attempt_count, 1)
            self.assertEqual(controller.request_sent_count, 1)
            self.assertEqual(len(session.packets), 1)
            self.assertEqual(controller.v1_media.stage, "acknowledged_not_physically_verified")
            self.assertEqual(leases, set())
        finally:
            controller.close()
            pipeline.close()


if __name__ == "__main__":
    unittest.main()
