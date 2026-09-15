"""Decode recovery uses generated H264 and real libavcodec, no camera capture."""
import re

from test_beta7_stability import FailingDecoder
from test_beta8_lifecycle import generated_video
from test_v1_video_receive import media


def remove_parameter_sets(data):
    starts = list(re.finditer(b'\x00\x00(?:\x00)?\x01', data))
    return b''.join(data[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(data)]
                    for i, m in enumerate(starts) if data[m.end()] & 31 not in (7, 8))


def test_v1_recovery_idr_without_repeated_parameter_sets():
    packets = generated_video(352, 288)
    frames = []
    pipeline = media.MediaPipeline(media.StreamFormat(352, 288, 20, 8000, 0x7a19, 1),
        lambda b: None, lambda b: None, lambda kind, frame: frames.append((kind, frame)), v1_recovery=True)
    try:
        for kind, body in packets[:4]:
            pipeline.feed_video(body, keyframe=kind == 100)
        before = len(frames)
        assert before > 0
        pipeline.decoder = FailingDecoder()
        assert not pipeline.feed_video(packets[3][1])
        assert pipeline.video_waiting_for_keyframe
        for kind, body in packets[4:]:
            pipeline.feed_video(remove_parameter_sets(body), keyframe=kind == 100)
        assert len(frames) > before
        assert pipeline.video_decode_errors == 1
    finally:
        pipeline.close()
