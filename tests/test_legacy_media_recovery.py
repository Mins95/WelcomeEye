"""Real legacy H.264 recovery with generated images; no device/network I/O."""
from fractions import Fraction
from io import BytesIO
import unittest

import av

from load_integration import load

media = load('media')


def video_packets(count=10):
    encoder = av.CodecContext.create('libx264', 'w')
    encoder.width, encoder.height = 64, 48
    encoder.pix_fmt = 'yuv420p'
    encoder.time_base, encoder.framerate = Fraction(1, 20), Fraction(20)
    encoder.thread_count, encoder.max_b_frames = 1, 0
    encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                       'x264-params': 'repeat-headers=1:scenecut=0'}
    packets = []
    for index in range(count):
        frame = av.VideoFrame(64, 48, 'yuv420p')
        for plane_index, plane in enumerate(frame.planes):
            plane.update(bytes([40 + index * 10 if plane_index == 0 else 128])
                         * plane.buffer_size)
        frame.pts, frame.time_base = index, Fraction(1, 20)
        if index % 3 == 0:
            frame.pict_type = av.video.frame.PictureType.I
        packets.extend(encoder.encode(frame))
    packets.extend(encoder.encode(None))
    return packets


def without_parameter_sets(data):
    starts = list(media._ANNEX_B_START.finditer(data))
    return b''.join(data[item.start():starts[index + 1].start()
                        if index + 1 < len(starts) else len(data)]
                    for index, item in enumerate(starts)
                    if data[item.end()] & 31 not in (7, 8))


class LegacyMediaRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.packets = video_packets()
        decoder = av.CodecContext.create('h264', 'r')
        self.reference = [frame for packet in self.packets
                          for frame in decoder.decode(packet)]
        self.frames, self.images, self.chunks = [], [], []
        self.pipeline = media.MediaPipeline(
            media.StreamFormat(64, 48, 20, 8000, 0x7a19, 1),
            self.chunks.append, self.images.append,
            lambda kind, frame: self.frames.append((kind, frame)))
        self.addCleanup(self.pipeline.close)

    def feed(self, index, *, strip_headers=False):
        packet = self.packets[index]
        body = bytes(packet)
        if strip_headers:
            body = without_parameter_sets(body)
        return self.pipeline.feed_video(body, keyframe=packet.is_keyframe)

    def video_frames(self):
        return [frame for kind, frame in self.frames if kind == 'video']

    def assert_image(self, frame, index):
        self.assertEqual(frame.to_ndarray().tobytes(),
                         self.reference[index].to_ndarray().tobytes())

    def test_complete_image_is_delivered_without_waiting_for_next_packet(self):
        for index in range(len(self.packets)):
            self.assertTrue(self.feed(index))
            frames = self.video_frames()
            self.assertEqual(len(frames), index + 1)
            self.assert_image(frames[-1], index)
        self.assertTrue(self.images[0].startswith(b'\xff\xd8'))
        self.assertEqual(self.pipeline.video_decode_errors, 0)
        self.assertEqual([frame.pts for frame in self.video_frames()],
                         [index * 4500 for index in range(10)])

    def test_corruption_does_not_discard_next_valid_idr_or_leak_into_ts(self):
        self.feed(0)
        self.feed(1)
        before_chunks = len(self.chunks)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.assertFalse(self.pipeline.feed_video(b'\0\0\0\1\x41\xff'))
        self.assertEqual(len(self.chunks), before_chunks)
        self.assertTrue(self.pipeline.video_waiting_for_keyframe)
        self.assertTrue(self.pipeline.video_recovery_pending)
        self.assertEqual(self.pipeline.video_recoveries, 0)
        self.assertTrue(self.feed(3, strip_headers=True))
        self.assert_image(self.video_frames()[-1], 3)
        self.assertFalse(self.pipeline.video_waiting_for_keyframe)
        self.assertFalse(self.pipeline.video_recovery_pending)
        self.assertEqual(self.pipeline.video_recoveries, 1)
        self.assertGreaterEqual(self.pipeline.video_last_recovery_ms, 0)
        for index in range(4, 10):
            self.feed(index, strip_headers=True)
        self.pipeline.close()
        with av.open(BytesIO(b''.join(self.chunks)), format='mpegts') as container:
            decoded = list(container.decode(video=0))
        self.assertEqual(len(decoded), 9)
        self.assert_image(decoded[2], 3)
        self.assertEqual(self.pipeline.video_decode_errors, 1)
        self.assertEqual(self.pipeline.video_decoder_resets, 1)

    def test_bad_keyframe_cannot_claim_recovery_and_dependent_frames_are_dropped(self):
        self.feed(0)
        self.feed(1)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.assertFalse(self.pipeline.feed_video(b'\0\0\0\1\x41\xff'))
        before = len(self.video_frames())
        self.assertFalse(self.pipeline.feed_video(b'\0\0\0\1\x65\x88\x84', keyframe=True))
        self.assertTrue(self.pipeline.video_waiting_for_keyframe)
        self.assertFalse(self.feed(4))
        self.assertEqual(len(self.video_frames()), before)
        self.assertEqual(self.pipeline.video_dropped_until_keyframe, 1)
        self.assertTrue(self.feed(6, strip_headers=True))
        self.assert_image(self.video_frames()[-1], 6)

    def test_audio_and_monotonic_video_continue_across_recovery(self):
        self.feed(0)
        self.feed(1)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.pipeline.feed_video(b'\0\0\0\1\x41\xff')
        self.pipeline.feed_audio(bytes([0xd5]) * 160)
        self.assertTrue(any(kind == 'audio' for kind, _ in self.frames))
        self.feed(3, strip_headers=True)
        self.feed(4)
        self.assertEqual([frame.pts for frame in self.video_frames()], [0, 4500, 9000, 13500])
        self.assert_image(self.video_frames()[-1], 4)
        self.pipeline.close()
        self.pipeline.close()

    def test_invalid_parameter_sets_do_not_poison_recovery(self):
        self.feed(0)
        self.feed(1)
        saved_headers = dict(self.pipeline._parameter_sets)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.assertFalse(self.pipeline.feed_video(
                b'\0\0\0\1\x67\xff\0\0\0\1\x41\xff'))
        self.assertEqual(self.pipeline._parameter_sets, saved_headers)
        self.assertTrue(self.feed(3, strip_headers=True))
        self.assert_image(self.video_frames()[-1], 3)

    def test_recovery_is_not_confirmed_until_decoder_produces_an_image(self):
        self.feed(0)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.pipeline.feed_video(b'\0\0\0\1\x41\xff')
        decoder = self.pipeline.decoder

        class DelayedDecoder:
            """Keep real libavcodec decoding; delay its first image delivery."""
            pending = None

            def decode(self, packet):
                frames = decoder.decode(packet)
                if self.pending is None:
                    self.pending = frames
                    return []
                previous, self.pending = self.pending, []
                return previous + frames

        self.pipeline.decoder = DelayedDecoder()
        self.feed(3, strip_headers=True)
        self.assertTrue(self.pipeline.video_recovery_pending)
        self.assertEqual(self.pipeline.video_recoveries, 0)
        self.assertIsNone(self.pipeline.video_last_recovery_ms)
        self.feed(4)
        self.assertFalse(self.pipeline.video_recovery_pending)
        self.assertEqual(self.pipeline.video_recoveries, 1)
        self.assert_image(self.video_frames()[-1], 4)

    def test_badly_labelled_dependent_image_cannot_restart_decoder(self):
        self.feed(0)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.pipeline.feed_video(b'\0\0\0\1\x41\xff')
        self.assertFalse(self.pipeline.feed_video(bytes(self.packets[4]), keyframe=True))
        self.assertTrue(self.pipeline.video_waiting_for_keyframe)
        self.assertTrue(self.pipeline.video_recovery_pending)
        self.assertEqual(self.pipeline.video_recoveries, 0)
        self.assertTrue(self.feed(6, strip_headers=True))
        self.assert_image(self.video_frames()[-1], 6)

    def test_skipped_dependent_image_cannot_replace_known_good_headers(self):
        self.feed(0)
        saved_headers = dict(self.pipeline._parameter_sets)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.pipeline.feed_video(b'\0\0\0\1\x41\xff')
        self.assertFalse(self.pipeline.feed_video(
            b'\0\0\0\1\x67\xff' + bytes(self.packets[4])))
        self.assertEqual(self.pipeline._parameter_sets, saved_headers)
        self.assertTrue(self.feed(6, strip_headers=True))
        self.assert_image(self.video_frames()[-1], 6)

    def test_recovery_headers_are_private_to_one_pipeline(self):
        self.feed(0)
        self.feed(1)
        other_frames = []
        other = media.MediaPipeline(
            self.pipeline.format, lambda chunk: None, lambda jpeg: None,
            lambda kind, frame: other_frames.append(frame), v1_recovery=True)
        self.addCleanup(other.close)
        with self.assertLogs(media.__name__, level='WARNING'):
            self.assertFalse(other.feed_video(
                without_parameter_sets(bytes(self.packets[3])), keyframe=True))
        self.assertEqual(other_frames, [])
        self.assertEqual(self.pipeline.video_decode_errors, 0)
        self.assertTrue(self.feed(3, strip_headers=True))
        self.assert_image(self.video_frames()[-1], 3)


if __name__ == '__main__':
    unittest.main()
