"""Real H264 decode of locally generated frames, not a hardware validation."""
from fractions import Fraction
import unittest

import av

from load_integration import load

p = load('connect3.protocol')
video = load('connect3.video')


def synthetic_video_packets(count=3):
    encoder = av.CodecContext.create('libx264', 'w')
    encoder.width, encoder.height = 64, 48
    encoder.pix_fmt = 'yuv420p'
    encoder.time_base = Fraction(1, 20)
    encoder.framerate = Fraction(20)
    encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
    packets = []
    for index in range(count):
        frame = av.VideoFrame(64, 48, 'yuv420p')
        for plane_index, plane in enumerate(frame.planes):
            plane.update(bytes([50 + index*20 if plane_index == 0 else 128]) * plane.buffer_size)
        frame.pts, frame.time_base = index, Fraction(1, 20)
        packets.extend(encoder.encode(frame))
    packets.extend(encoder.encode(None))
    return packets


class DecodeTests(unittest.TestCase):
    def test_late_native_reader_receives_cached_sps_pps_without_media_history(self):
        encoder = av.CodecContext.create('libx264', 'w')
        encoder.width, encoder.height, encoder.pix_fmt = 64, 48, 'yuv420p'
        encoder.time_base, encoder.framerate = Fraction(1, 20), Fraction(20)
        encoder.thread_count, encoder.max_b_frames = 1, 0
        encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                           'x264-params': 'repeat-headers=1:scenecut=0'}
        decoder = video.VideoDecoder()
        for index in range(3):
            frame = av.VideoFrame(64, 48, 'yuv420p')
            for plane_index, plane in enumerate(frame.planes):
                plane.update(bytes((50 + index * 20 if plane_index == 0 else 128,)) * plane.buffer_size)
            frame.pts, frame.time_base = index, Fraction(1, 20)
            if index == 2:
                frame.pict_type = av.video.frame.PictureType.I
            packet, = encoder.encode(frame)
            payload = bytes(packet)
            if index == 2:
                self.assertTrue(packet.is_keyframe)
                payload = b''.join(b'\0\0\0\1' + nal
                    for nal in video._ANNEX_B_START.split(payload)
                    if nal and nal[0] & 31 not in (7, 8))
            original = p.MediaFrame(1 if packet.is_keyframe else 0, 1, 64, 48, 20, 0, 0, payload)
            frames, _ = decoder.feed(original)
            self.assertEqual(len(frames), 1)
            if index == 2:
                self.assertEqual(original.payload, payload)
                self.assertGreater(len(decoder.native_packet.payload), len(payload))
                newcomer = av.CodecContext.create('h264', 'r')
                joined = newcomer.decode(av.Packet(decoder.native_packet.payload))
                self.assertEqual(len(joined), 1)
                self.assertEqual(bytes(joined[0].planes[0]), bytes(frames[0].planes[0]))
        self.assertEqual(set(decoder._parameter_sets), {7, 8})
        self.assertLess(sum(map(len, decoder._parameter_sets.values())), 131073)
        decoder.close()
        self.assertFalse(decoder._parameter_sets)
        self.assertIsNone(decoder.native_packet)

    def test_actual_three_new_decoded_images_and_jpeg(self):
        decoder = video.VideoDecoder()
        frames, images = [], []
        for packet in synthetic_video_packets():
            decoded, image = decoder.feed(p.MediaFrame(
                1 if packet.is_keyframe else 0, 1, 64, 48, 20, 0, 0, bytes(packet)))
            frames.extend(decoded)
            if image:
                images.append(image)
        self.assertEqual(len(frames), 3)
        self.assertEqual([frame.pts for frame in frames], [0, 4500, 9000])
        self.assertTrue(images[0].startswith(b'\xff\xd8'))
        self.assertEqual((frames[0].width, frames[0].height), (64, 48))
        decoder.close()
        self.assertIsNone(decoder._decoder)

    def test_unknown_codec_and_oversize_fail_precisely(self):
        decoder = video.VideoDecoder()
        with self.assertRaisesRegex(p.MediaProtocolError, '^unsupported_video_codec$'):
            decoder.feed(p.MediaFrame(1, 2, 64, 48, 20, 0, 0, b'SYNTHETIC'))
        with self.assertRaisesRegex(p.MediaProtocolError, '^invalid_video_format$'):
            decoder.feed(p.MediaFrame(1, 1, 8192, 48, 20, 0, 0, b'SYNTHETIC'))

    def test_wait_for_intra_and_bounded_decode_failures(self):
        decoder = video.VideoDecoder()
        self.assertEqual(decoder.feed(p.MediaFrame(0, 1, 64, 48, 20, 0, 0, b'SYNTHETIC')), ([], None))
        self.assertEqual(decoder.dropped, 1)
        bad = p.MediaFrame(1, 1, 64, 48, 20, 0, 0, b'\0\0\0\1\x65SYNTHETIC_INVALID')
        for _ in range(2):
            self.assertEqual(decoder.feed(bad), ([], None))
        with self.assertRaisesRegex(p.MediaProtocolError, '^video_decode_error$'):
            decoder.feed(bad)
        self.assertEqual(decoder.errors, 3)


if __name__ == '__main__':
    unittest.main()
