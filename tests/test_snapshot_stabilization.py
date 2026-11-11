"""Real H.264/JPEG startup selection with a controlled monotonic clock."""
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from load_integration import load
from test_legacy_media_recovery import video_packets
from PIL import Image, ImageStat

media = load('media')
readiness = load('snapshot_readiness')


class SnapshotStabilizationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.clock = patch.object(media, 'time', SimpleNamespace(monotonic=lambda: self.now))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.packets = video_packets()

    def pipeline(self):
        images, frames, transport = [], [], []
        pipeline = media.MediaPipeline(media.StreamFormat(64, 48, 20, 8000, 0, 0),
            transport.append, images.append, lambda kind, frame: frames.append(frame))
        self.addCleanup(pipeline.close)
        return pipeline, images, frames, transport

    def feed(self, pipeline, index, at):
        self.now = at
        packet = self.packets[index]
        self.assertTrue(pipeline.feed_video(bytes(packet), keyframe=packet.is_keyframe))

    def test_live_video_is_immediate_but_photo_uses_new_frame_after_warmup(self):
        pipeline, images, frames, transport = self.pipeline()
        self.feed(pipeline, 0, 100)
        self.assertEqual(len(frames), 1)
        self.assertTrue(transport)
        self.assertFalse(images)
        self.feed(pipeline, 1, 103.99)
        self.assertFalse(images)
        # Time passing alone cannot publish the cached first image.
        self.now = 104.1
        self.assertFalse(images)
        self.feed(pipeline, 2, 104.2)
        self.assertEqual(len(images), 1)
        self.assertEqual(len(frames), 3)
        with Image.open(BytesIO(images[0])) as jpeg:
            measured = ImageStat.Stat(jpeg.convert('L')).mean[0]
        expected = ImageStat.Stat(frames[-1].to_image().convert('L')).mean[0]
        initial = ImageStat.Stat(frames[0].to_image().convert('L')).mean[0]
        self.assertAlmostEqual(measured, expected, delta=2)
        self.assertGreater(abs(measured-initial), 10)
        # Already established streams retain the normal 0.5s JPEG cadence.
        self.feed(pipeline, 3, 104.8)
        self.assertEqual(len(images), 2)

    def test_decode_error_does_not_start_warmup_and_night_monochrome_is_valid(self):
        pipeline, images, frames, _ = self.pipeline()
        with self.assertLogs(media.__name__, level='WARNING'):
            pipeline.feed_video(b'\0\0\0\1\x65\xff', keyframe=True)
        self.assertIsNone(pipeline.snapshot_readiness.first_frame_at)
        self.feed(pipeline, 0, 200)
        self.feed(pipeline, 1, 203.99)
        self.assertFalse(images)
        self.feed(pipeline, 2, 204)
        self.assertEqual(len(images), 1)
        with Image.open(BytesIO(images[0])) as jpeg:
            self.assertLess(ImageStat.Stat(jpeg.convert('HSV').getchannel('S')).mean[0], 1)

    def test_each_new_session_or_channel_has_its_own_warmup(self):
        first, images1, _, _ = self.pipeline()
        second, images2, _, _ = self.pipeline()
        self.feed(first, 0, 100)
        self.feed(second, 0, 102)
        self.feed(first, 1, 104)
        self.feed(second, 1, 104)
        self.assertEqual(len(images1), 1)
        self.assertFalse(images2)
        self.feed(second, 2, 106)
        self.assertEqual(len(images2), 1)
        replacement, replacement_images, _, _ = self.pipeline()
        self.feed(replacement, 0, 107)
        self.assertFalse(replacement_images)

    def test_readiness_reset_does_not_reuse_previous_stream_age(self):
        gate = readiness.SnapshotReadiness()
        self.assertFalse(gate.observe(0))
        self.assertTrue(gate.observe(4))
        gate.reset()
        self.assertFalse(gate.observe(100))
        self.assertFalse(gate.observe(103.99))
        self.assertTrue(gate.observe(104))


if __name__ == '__main__':
    unittest.main()
