"""Regression captured from real V1 beta1 video diagnostics."""
import importlib
import struct
import unittest
from unittest.mock import patch

from test_v1_video_receive import PACKAGE, Clock, make_session, media, protected, synthetic_h264, v1


def metadata12():
    return bytes(12)


def hardware_wire(body):
    payload = protected.tlv(99, metadata12()) + struct.pack('<HH', 100, 1) + body
    return protected.owsp(payload)


class V1HardwareVideoRegressionTests(unittest.TestCase):
    def test_terminal_length_one_consumes_annexb_owsp_remainder(self):
        body = synthetic_h264()[0][1]
        parts = v1.parse_video_tlvs(hardware_wire(body)[8:])
        self.assertEqual(parts[0], (99, metadata12()))
        self.assertEqual(parts[1], (100, body))

    def test_12byte_metadata_terminal_image_reaches_real_pyav(self):
        body = synthetic_h264()[0][1]
        clock = Clock()
        session = make_session([hardware_wire(body)], clock)
        receiver = v1.V1VideoReceiver(session.media_observer)
        ts, images, frames = [], [], []
        pipeline = media.MediaPipeline(
            media.StreamFormat(352, 288, 20, 8000, 0x7A19, 1),
            ts.append,
            images.append,
            lambda kind, frame: frames.append((kind, frame)),
        )
        client = importlib.import_module(PACKAGE + '.client')
        with patch.object(client.time, 'monotonic', clock):
            accepted = []
            for kind, data in session.read():
                video = receiver.receive(kind, data)
                if video is not None:
                    packet, keyframe = video
                    accepted.append(pipeline.feed_video(packet, keyframe=keyframe))
        self.assertEqual(accepted, [True])
        self.assertTrue(pipeline.started)
        self.assertTrue(images)
        stats = session.media_observer.snapshot()['video_receive']
        self.assertEqual(stats.get('video_metadata_12_count'), 1)
        self.assertEqual(stats.get('complete_video_count'), 1)

    def test_short_terminal_length_without_native_metadata_or_annexb_is_rejected(self):
        with self.assertRaises(protected.ProtocolError):
            v1.parse_video_tlvs(struct.pack('<HH', 100, 1) + b'not-h264')
        payload = protected.tlv(99, metadata12()) + struct.pack('<HH', 100, 1) + b'not-h264'
        with self.assertRaises(protected.ProtocolError):
            v1.parse_video_tlvs(payload)


if __name__ == '__main__':
    unittest.main()
