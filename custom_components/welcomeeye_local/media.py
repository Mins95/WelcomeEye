"""Convert the device's Annex B H.264 and G.711 audio to MPEG-TS/JPEG."""
from dataclasses import dataclass
from fractions import Fraction
import io
import struct
import time

import av


@dataclass(frozen=True)
class StreamFormat:
    width: int
    height: int
    fps: int
    sample_rate: int
    audio_format: int
    channels: int

    @classmethod
    def parse(cls, body):
        if len(body) != 40 or body[4:8] != b'H264':
            raise ValueError('Unsupported stream format')
        width, height = struct.unpack_from('<HH', body, 12)
        rate = struct.unpack_from('<I', body, 20)[0]
        audio, channels = struct.unpack_from('<HH', body, 28)
        if not (0 < width <= 4096 and 0 < height <= 4096 and 0 < body[16] <= 60):
            raise ValueError('Invalid video dimensions or rate')
        return cls(width, height, body[16], rate, audio, channels)


class Sink:
    def __init__(self, callback):
        self.callback = callback

    def write(self, data):
        self.callback(bytes(data))
        return len(data)

    def writable(self):
        return True


class MediaPipeline:
    def __init__(self, fmt, on_ts, on_image, on_frame=None):
        self.format = fmt
        self.on_image = on_image
        self.on_frame = on_frame
        self.decoded_video_pts = 0
        self.last_image = 0.0
        self.video_pts = 0
        self.audio_pts = 0
        self.started = False
        self.decoder = av.CodecContext.create('h264', 'r')
        self.output = av.open(Sink(on_ts), 'w', format='mpegts', options={
            'mpegts_flags': 'resend_headers+pat_pmt_at_frames', 'flush_packets': '1',
        })
        self.video = self.output.add_stream('h264', rate=fmt.fps)
        self.video.width = fmt.width
        self.video.height = fmt.height
        self.video.pix_fmt = 'yuv420p'
        self.video.time_base = Fraction(1, 90000)
        self.audio = None
        # 0x7a19 is the Connect 2 G.711 A-law stream identifier.
        if fmt.audio_format == 0x7a19 and fmt.channels == 1 and fmt.sample_rate == 8000:
            self.audio_decoder = av.CodecContext.create('pcm_alaw', 'r')
            self.audio_decoder.sample_rate = fmt.sample_rate
            self.audio_decoder.layout = 'mono'
            self.audio = self.output.add_stream('aac', rate=fmt.sample_rate)
            self.audio.layout = 'mono'
            self.audio.bit_rate = 24000
            self.audio.time_base = Fraction(1, fmt.sample_rate)

    def feed(self, kind, body):
        if kind in (100, 101):
            if not self.started:
                if kind != 100:
                    return
                self.started = True
            packet = av.Packet(body)
            packet.stream = self.video
            packet.pts = packet.dts = self.video_pts
            packet.time_base = Fraction(1, 90000)
            packet.is_keyframe = kind == 100
            self.video_pts += 90000 // self.format.fps
            self.output.mux(packet)
            for part in self.decoder.parse(body):
                for frame in self.decoder.decode(part):
                    frame.pts = self.decoded_video_pts
                    frame.time_base = Fraction(1, 90000)
                    self.decoded_video_pts += 90000 // self.format.fps
                    if self.on_frame:
                        self.on_frame('video', frame)
                    now = time.monotonic()
                    if now - self.last_image >= 0.5:
                        image = io.BytesIO()
                        frame.to_image().save(image, format='JPEG', quality=85)
                        self.on_image(image.getvalue())
                        self.last_image = now
        elif kind == 98 and self.started and self.audio:
            for frame in self.audio_decoder.decode(av.Packet(body)):
                frame.pts = self.audio_pts
                frame.time_base = Fraction(1, self.format.sample_rate)
                self.audio_pts += frame.samples
                if self.on_frame:
                    self.on_frame('audio', frame)
                for packet in self.audio.encode(frame):
                    self.output.mux(packet)

    def close(self):
        if self.audio:
            for packet in self.audio.encode(None):
                self.output.mux(packet)
        self.output.close()
