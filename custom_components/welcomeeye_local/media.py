"""Convert WelcomeEye H.264 and G.711 audio to MPEG-TS/JPEG."""
from dataclasses import dataclass
from fractions import Fraction
import io
import logging
import struct
import time

import av

_LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class H264PacketInfo:
    detected: bool
    nal_types: tuple[int, ...]
    keyframe: bool
    has_sps: bool
    has_pps: bool
    framing: str | None


def _annex_b_nal_types(data):
    positions = []
    index = 0
    size = len(data)
    while index + 3 <= size:
        prefix = 0
        if data[index:index + 4] == b'\x00\x00\x00\x01':
            prefix = 4
        elif data[index:index + 3] == b'\x00\x00\x01':
            prefix = 3
        if prefix:
            nal = index + prefix
            if nal < size:
                positions.append(data[nal] & 0x1F)
            index = nal + 1
        else:
            index += 1
    return positions


def _avcc_nal_types(data):
    result = []
    offset = 0
    while offset + 4 <= len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        offset += 4
        if length <= 0 or offset + length > len(data):
            return []
        nal_type = data[offset] & 0x1F
        if not 1 <= nal_type <= 23:
            return []
        result.append(nal_type)
        offset += length
    return result if result and offset == len(data) else []


def inspect_h264_packet(data):
    nal_types = _annex_b_nal_types(data)
    framing = 'annex_b' if nal_types else None
    if not nal_types:
        nal_types = _avcc_nal_types(data)
        framing = 'avcc' if nal_types else None
    detected = bool(nal_types) and all(1 <= item <= 23 for item in nal_types)
    unique = tuple(sorted(set(nal_types))) if detected else ()
    return H264PacketInfo(
        detected=detected,
        nal_types=unique,
        keyframe=5 in unique,
        has_sps=7 in unique,
        has_pps=8 in unique,
        framing=framing,
    )


def normalize_h264_packet(data, framing):
    if framing != 'avcc':
        return data
    output = bytearray()
    offset = 0
    while offset + 4 <= len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        offset += 4
        if length <= 0 or offset + length > len(data):
            return data
        output += b'\x00\x00\x00\x01'
        output += data[offset:offset + length]
        offset += length
    return bytes(output) if output and offset == len(data) else data


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
        self.video_decode_errors = 0
        self.video_decoder_resets = 0
        self.video_waiting_for_keyframe = False
        self.video_dropped_until_keyframe = 0
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
        if fmt.audio_format == 0x7a19 and fmt.channels == 1 and fmt.sample_rate == 8000:
            self.audio_decoder = av.CodecContext.create('pcm_alaw', 'r')
            self.audio_decoder.sample_rate = fmt.sample_rate
            self.audio_decoder.layout = 'mono'
            self.audio = self.output.add_stream('aac', rate=fmt.sample_rate)
            self.audio.layout = 'mono'
            self.audio.bit_rate = 24000
            self.audio.time_base = Fraction(1, fmt.sample_rate)

    def _reset_video_decoder(self):
        self.decoder = av.CodecContext.create('h264', 'r')
        self.video_decoder_resets += 1
        self.video_waiting_for_keyframe = True

    def feed_video(self, body, *, keyframe=False):
        if not self.started and not keyframe:
            return False
        if self.video_waiting_for_keyframe and not keyframe:
            self.video_dropped_until_keyframe += 1
            return False

        frames = []
        try:
            for part in self.decoder.parse(body):
                frames.extend(self.decoder.decode(part))
        except av.InvalidDataError:
            self.video_decode_errors += 1
            self._reset_video_decoder()
            if self.video_decode_errors == 1 or self.video_decode_errors % 25 == 0:
                _LOGGER.warning(
                    'Dropped invalid WelcomeEye H264 packet; waiting for next keyframe'
                )
            return False

        packet = av.Packet(body)
        packet.stream = self.video
        packet.pts = packet.dts = self.video_pts
        packet.time_base = Fraction(1, 90000)
        packet.is_keyframe = keyframe
        self.video_pts += 90000 // self.format.fps
        self.output.mux(packet)

        if keyframe:
            self.video_waiting_for_keyframe = False
        if not self.started:
            self.started = True

        for frame in frames:
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
        return True

    def feed_audio(self, body):
        if not self.started or not self.audio:
            return
        for frame in self.audio_decoder.decode(av.Packet(body)):
            frame.pts = self.audio_pts
            frame.time_base = Fraction(1, self.format.sample_rate)
            self.audio_pts += frame.samples
            if self.on_frame:
                self.on_frame('audio', frame)
            for packet in self.audio.encode(frame):
                self.output.mux(packet)

    def feed(self, kind, body, *, keyframe=None):
        if kind == 98:
            self.feed_audio(body)
            return self.started
        if kind in (97, 99, 100, 101):
            return self.feed_video(body, keyframe=(kind == 100 if keyframe is None else keyframe))
        return False

    def close(self):
        if self.audio:
            for packet in self.audio.encode(None):
                self.output.mux(packet)
        self.output.close()
