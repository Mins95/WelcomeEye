"""Regression captured from real V1 beta1 video diagnostics."""
import importlib
import struct
import unittest
from unittest.mock import patch

from test_v1_video_receive import PACKAGE, Clock, make_session, media, protected, synthetic_h264, v1


def metadata12():
    return bytes(12)


def hardware_wire(kind, body):
    payload = protected.tlv(99, metadata12()) + struct.pack('<HH', kind, 1) + body
    return protected.owsp(payload)


class V1HardwareVideoRegressionTests(unittest.TestCase):
    def test_terminal_length_one_consumes_annexb_owsp_remainder(self):
        kind, body = synthetic_h264()[0]
        parts = v1.parse_video_tlvs(hardware_wire(kind, body)[8:])
        self.assertEqual(parts[0], (99, metadata12()))
        self.assertEqual(parts[1], (100, body))

    def test_12byte_metadata_terminal_images_reach_real_pyav(self):
        packets = synthetic_h264()
        clock = Clock()
        session = make_session([b''.join(hardware_wire(kind, body) for kind, body in packets)], clock)
        receiver = v1.V1VideoReceiver(session.media_observer)
        ts, images, frames, accepted = [], [], [], []
        pipeline = media.MediaPipeline(
            media.StreamFormat(352, 288, 20, 8000, 0x7A19, 1),
            ts.append,
            images.append,
            lambda kind, frame: frames.append((kind, frame)),
        )
        client = importlib.import_module(PACKAGE + '.client')
        try:
            with patch.object(client.time, 'monotonic', clock):
                for _ in packets:
                    for kind, data in session.read():
                        video = receiver.receive(kind, data)
                        if video is not None:
                            packet, keyframe = video
                            accepted.append(pipeline.feed_video(packet, keyframe=keyframe))
            self.assertEqual(accepted, [True] * len(packets))
            self.assertTrue(pipeline.started)
            self.assertTrue(images and images[0].startswith(b'\xff\xd8'))
            stats = session.media_observer.snapshot()['video_receive']
            self.assertEqual(stats.get('video_metadata_12_count'), len(packets))
            self.assertEqual(stats.get('complete_video_count'), len(packets))
        finally:
            pipeline.close()

    def test_short_terminal_length_without_native_metadata_or_annexb_is_rejected(self):
        with self.assertRaises(protected.ProtocolError):
            v1.parse_video_tlvs(struct.pack('<HH', 100, 1) + b'not-h264')
        payload = protected.tlv(99, metadata12()) + struct.pack('<HH', 100, 1) + b'not-h264'
        with self.assertRaises(protected.ProtocolError):
            v1.parse_video_tlvs(payload)


if __name__ == '__main__':
    unittest.main()
