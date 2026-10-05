"""Decode explicitly identified QV H.264 frames for the existing WebRTC tracks."""
from fractions import Fraction
from io import BytesIO
import time

from .protocol import MediaProtocolError


class VideoDecoder:
    def __init__(self):
        import av
        self._av = av
        self._decoder = None
        self._pts = 0
        self._last_image = 0.0
        self.errors = 0
        self.dropped = 0
        self.frames = 0

    def feed(self, packet):
        if not packet.is_h264:
            # Other codec families are not inferred from an H264-like payload.
            raise MediaProtocolError('unsupported_video_codec')
        if self._decoder is None:
            if not packet.is_keyframe:
                self.dropped += 1
                return [], None
            if not (0 < packet.width <= 4096 and 0 < packet.height <= 4096
                    and 0 < packet.fps <= 60):
                raise MediaProtocolError('invalid_video_format')
            self._decoder = self._av.CodecContext.create('h264', 'r')
            self._decoder.thread_count = 1
            self._decoder.options = {'max_pixels': str(4096 * 4096)}
            self._fps = packet.fps
        try:
            frames = self._decoder.decode(self._av.Packet(packet.payload))
        except self._av.InvalidDataError:
            self.errors += 1
            self._decoder = None
            if self.errors >= 3:
                raise MediaProtocolError('video_decode_error') from None
            return [], None
        if len(frames) > 16 or any(frame.width > 4096 or frame.height > 4096 for frame in frames):
            raise MediaProtocolError('decoded_frame_limit')
        jpeg = None
        for frame in frames:
            frame.pts, frame.time_base = self._pts, Fraction(1, 90000)
            self._pts += round(90000 / self._fps)
            self.frames += 1
            now = time.monotonic()
            if now - self._last_image >= 0.5:
                image = frame.to_image()
                image.thumbnail((1280, 720))
                stream = BytesIO()
                image.save(stream, format='JPEG', quality=80)
                jpeg = stream.getvalue()
                self._last_image = now
        return frames, jpeg

    def close(self):
        self._decoder = None
