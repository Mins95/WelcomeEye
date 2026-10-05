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
