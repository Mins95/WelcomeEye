"""Independent synthetic QV peer for offline runtime checks, never device evidence."""
import asyncio
from fractions import Fraction
from hashlib import sha256
import struct

import av
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

STREAM_KEY = 'SYNTHETIC_STREAM_KEY_OF_SUFFICIENT_LENGTH'
PASSWORD = 'SYNTHETIC_PASSWORD'
OPENING_CODE = 'SYNTHETIC_OPENING_CODE'


def crypt(data, *, decrypt=False):
    cipher = Cipher(algorithms.AES(STREAM_KEY.encode()[:32]), modes.CBC(b'0' * 16))
    operation = cipher.decryptor() if decrypt else cipher.encryptor()
    return operation.update(data) + operation.finalize()


def response(command, parameters=b'', *, rejected=False):
    header = bytearray(32)
    header[0] = command
    size = (len(parameters) + 47) // 16 * 16
    struct.pack_into('<H', header, 9, size)
    if command == 0xFE:
        struct.pack_into('<H', header, 11, len(parameters))
        header[13] = 4
    elif command == 0x0B:
        header[12], header[14] = 1, 1  # G711 A-law, normal QV audio frame.
    else:
        header[11], header[12] = int(rejected), 1
    raw = bytes(header)
    body = parameters + sha256(raw + parameters).digest()
    return crypt(raw) + crypt(body + bytes(size - len(body)))


def media_packet(payload, *, frame_type, codec, index):
    inner = bytearray(20)
    inner[:4] = b'\0\0\1' + bytes((0xE0 + frame_type,))
    struct.pack_into('<I', inner, 4, len(payload))
    struct.pack_into('<H', inner, 12, (index * 50) % 1000)
    inner[14] = codec
    if frame_type == 2:
        inner[15] = 1
        struct.pack_into('<H', inner, 16, 8000)
    else:
        inner[15] = 80
        struct.pack_into('<HH', inner, 16, 64, 48)
    body = bytes(inner) + payload
    outer = bytearray(32)
    outer[0] = 0xA1
    struct.pack_into('<H', outer, 9, 16)
    struct.pack_into('<I', outer, 11, len(body))
    return crypt(bytes(outer)) + crypt(body[:16]) + body[16:]


class SyntheticQVPeer:
    """A stream reader/writer boundary; production QV reader and codecs remain real."""

    def __init__(self, *, audio_codec=4, allowed_outputs=((1, 1), (1, 2))):
        self.reader = asyncio.StreamReader()
        self.allowed_outputs = frozenset(allowed_outputs)
        self.output_targets = []
        self.audio_codec = audio_codec
        self.mode = None
        self.closed = False
        self.close_count = self.teardowns = self.audio_packets = 0
        self.commands, self.outputs = [], []
        self.reject_output = False
        self.producer = None
        self.teardown_started = asyncio.Event()
        self.teardown_release = asyncio.Event()
        self.teardown_release.set()

    def write(self, data):
        assert not self.closed
        if self.mode is None:
            assert len(data) == 32 and data[0] == 0xA9 and data[9] in (0, 2)
            self.mode = 'live' if data[9] == 0 else 'talk'
            self.commands.append(0xA9)
            setup = bytearray(32)
            setup[0], setup[10], setup[11] = 0xA9, 2, 1
            self.reader.feed_data(bytes(setup))
            return
        header = crypt(data[:32], decrypt=True)
        command = header[0]
        if command == 0xA2:
            assert self.mode == 'talk'
            assert len(data) == 32 + struct.unpack_from('<I', header, 11)[0]
            self.audio_packets += 1
            return
        self.commands.append(command)
        size = struct.unpack_from('<H', header, 9)[0]
        assert len(data) == 32 + size
        body = crypt(data[32:], decrypt=True)
        length = struct.unpack_from('<H', header, 11)[0] if command in (1, 0x0B, 0xFE) else 0
        parameters = body[:length]
        assert body[length:length + 32] == sha256(header + parameters).digest()
        if command in (1, 0x0B):
            assert parameters == b'adminapp2&&' + sha256(PASSWORD.encode()).hexdigest().encode() + b'\0\0'
            assert (command == 1) == (self.mode == 'live')
            self.reader.feed_data(response(command))
            if command == 1:
                self.producer = asyncio.create_task(self._produce(), name='synthetic-qv-video')
        elif command == 0xFE:
            assert self.mode == 'live' and header[13] == 4
            target = (parameters[2], parameters[0])
            assert target in self.allowed_outputs and parameters[1] == 0 and parameters[3] == 1
            assert parameters[16:] == sha256(OPENING_CODE.encode()).hexdigest().encode()
            self.output_targets.append(target)
            self.outputs.append(parameters[0])
            self.reader.feed_data(response(0xFE, b'\1\2' if self.reject_output else b'\0\0'))
        elif command == 7:
            self.teardowns += 1
            self.teardown_started.set()
        else:
            assert command in (0, 12, 13), command
            self.reader.feed_data(response(command))

    async def _produce(self):
        encoder = av.CodecContext.create('libx264', 'w')
        encoder.width, encoder.height, encoder.pix_fmt = 64, 48, 'yuv420p'
        encoder.time_base, encoder.framerate = Fraction(1, 20), Fraction(20)
        encoder.gop_size = 20
        encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
        audio_encoder = None
        if self.audio_codec == 8:
            audio_encoder = av.CodecContext.create('aac', 'w')
            audio_encoder.sample_rate, audio_encoder.layout = 8000, 'mono'
            audio_encoder.format, audio_encoder.bit_rate = 'fltp', 24000
        index = 0
        while not self.closed:
            frame = av.VideoFrame(64, 48, 'yuv420p')
            for plane_index, plane in enumerate(frame.planes):
                plane.update(bytes([50 + index % 100 if plane_index == 0 else 128]) * plane.buffer_size)
            frame.pts, frame.time_base = index, Fraction(1, 20)
            for packet in encoder.encode(frame):
                self.reader.feed_data(media_packet(bytes(packet),
                    frame_type=1 if packet.is_keyframe else 0, codec=1, index=index))
            audio = [b'\xd5' * 400]
            if audio_encoder is not None:
                source = av.AudioFrame(format='fltp', layout='mono', samples=400)
                source.sample_rate, source.pts, source.time_base = 8000, index * 400, Fraction(1, 8000)
                source.planes[0].update(struct.pack('<400f', *([.1, -.1] * 200)))
                audio = []
                for packet in audio_encoder.encode(source):
                    payload = bytes(packet)
                    size = len(payload) + 7
                    adts = bytes((0xff, 0xf1, 0x6c, 0x40 | (size >> 11), (size >> 3) & 0xff,
                                  ((size & 7) << 5) | 0x1f, 0xfc))
                    audio.append(adts + payload)
            for payload in audio:
                self.reader.feed_data(media_packet(payload, frame_type=2, codec=self.audio_codec, index=index))
            index += 1
            await asyncio.sleep(.05)

    async def drain(self):
        if self.teardowns:
            await self.teardown_release.wait()

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True
        self.close_count += 1
        if self.producer is not None:
            self.producer.cancel()
        self.reader.feed_eof()

    async def wait_closed(self):
        if self.producer is not None:
            results = await asyncio.gather(self.producer, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
        assert self.closed
