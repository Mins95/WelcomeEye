"""Bounded QV live framing reconstructed from Door Connect's ARM64 native code.

This module does not open sockets, authenticate over CGI, or send physical
commands. Evidence: liblive_player.so SHA256 bb375c04df0f9b15407a8c158163ff121
dc517be15ce55def0a4be2064019c68; CQUIIStreamLive/CQUIIStreamBase addresses below.
Synthetic tests establish the implementation, not hardware interoperability.
"""
from dataclasses import dataclass, field
from hashlib import sha256
from hmac import compare_digest
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEADER_SIZE = 32
FRAME_HEADER_SIZE = 20
MAX_PACKET_BODY = 1024 * 1024
MAX_FRAME_PAYLOAD = 4 * 1024 * 1024
MAX_PARAMETERS = 4096
IV = b"0" * 16
MEDIA_COMMANDS = frozenset((0xA0, 0xA1, 0xA2, 0xA3))
# OnRecvCommand 0x4a464c dispatches these native response types. Constructors
# below intentionally expose only live play, keepalive and live teardown.
CONTROL_COMMANDS = frozenset((0, 1, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                              17, 0xA4, 0xAA, 0xFE))


class MediaProtocolError(ValueError):
    """A fixed local reason; never include remote bytes or credentials."""


@dataclass(frozen=True)
class CipherMaterial:
    """The CGI stream key; JNI 0x454a10 supplies the same string three times."""

    stream_key: str = field(repr=False)
    encryption_mode: int = 2
    sha_mode: int = 1

    def __post_init__(self):
        if self.encryption_mode not in (0, 1, 2) or self.sha_mode not in (0, 1):
            raise MediaProtocolError("unsupported_crypto_mode")
        if not isinstance(self.stream_key, str) or not self.stream_key or len(self.stream_key) > 256:
            raise MediaProtocolError("invalid_stream_key")
        try:
            raw = self.stream_key.encode("ascii")
        except UnicodeError:
            raise MediaProtocolError("invalid_stream_key") from None
        if any(c < 32 or c == 127 for c in raw):
            raise MediaProtocolError("invalid_stream_key")
        if self.encryption_mode and len(raw) < self.key_size:
            raise MediaProtocolError("short_stream_key")

    @property
    def key_size(self):
        # GetKeyLen 0x4a6304: AES128(mode1), AES256(mode2). No hex decoding.
        return {0: 0, 1: 16, 2: 32}[self.encryption_mode]

    @property
    def key(self):
        return self.stream_key.encode("ascii")[:self.key_size]

    @property
    def digest_size(self):
        return 32 if self.sha_mode else 0


def _crypt(data, material, *, decrypt=False):
    if not isinstance(data, bytes):
        raise MediaProtocolError("invalid_cipher_input")
    if not material.encryption_mode:
        return data
    if len(data) % 16:
        raise MediaProtocolError("unaligned_cipher_input")
    # EncryptData 0x4a41c4 and EncryptMediaData 0x4a60e0 reset IV per call.
    context = Cipher(algorithms.AES(material.key), modes.CBC(IV))
    operation = context.decryptor() if decrypt else context.encryptor()
    return operation.update(data) + operation.finalize()


@dataclass(frozen=True)
class SetupResponse:
    result: int
    encryption_mode: int
    sha_mode: int


def build_setup_request():
    """SendSetup 0x4a78b8: 32 zero bytes except A9 at offset 0 (live=0@9)."""
    return b"\xa9" + bytes(HEADER_SIZE - 1)


def parse_setup_response(data):
    # OnRecv 0x4a3c04; OnRecvSetup 0x4a4034. Setup is never AES encrypted.
    if not isinstance(data, bytes) or len(data) != HEADER_SIZE:
        raise MediaProtocolError("setup_size")
    if data[0] != 0xA9:
        raise MediaProtocolError("setup_command")
    if data[10] not in (0, 1, 2) or data[11] not in (0, 1):
        raise MediaProtocolError("unsupported_crypto_mode")
    return SetupResponse(data[9], data[10], data[11])


def _bounded_number(value, maximum, reason):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise MediaProtocolError(reason)
    return value


def _cstring(value):
    if not isinstance(value, str):
        raise MediaProtocolError("invalid_parameter")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raise MediaProtocolError("invalid_parameter") from None
    if len(raw) > MAX_PARAMETERS or any(c < 32 or c == 127 for c in raw):
        raise MediaProtocolError("invalid_parameter")
    return raw


def _encode_command(header, parameters, material):
    extension_size = len(parameters) + material.digest_size
    if material.encryption_mode:
        extension_size = (extension_size + 15) // 16 * 16
    if extension_size > 0xFFFF:
        raise MediaProtocolError("parameters_size")
    struct.pack_into("<H", header, 9, extension_size)
    plaintext = bytes(header)
    body = parameters
    if material.sha_mode:
        body += sha256(plaintext + parameters).digest()
    body += bytes(extension_size - len(body))
    return _crypt(plaintext, material) + _crypt(body, material)


def build_play_request(material, *, username, password, channel, stream,
                       timestamp_seconds, fast=False, custom_id="", client_id=""):
    """OnSendPlay 0x4a8384 / SendFastPlay 0x4a7978 (newcn=1 uses AA)."""
    _bounded_number(channel, 0xFFFF, "invalid_channel")
    _bounded_number(stream, 0xFF, "invalid_stream")
    _bounded_number(timestamp_seconds, 0xFFFFFFFFFFFFFFFF, "invalid_timestamp")
    if fast and (material.encryption_mode != 2 or material.sha_mode != 1):
        raise MediaProtocolError("fast_play_crypto_mode")
    parameters = (_cstring(username) + b"&&" + _cstring(password) + b"\0"
                  + _cstring(custom_id) + b"\0")
    if client_id:
        parameters += _cstring(client_id) + b"\0"
    if len(parameters) > MAX_PARAMETERS:
        raise MediaProtocolError("parameters_size")
    header = bytearray(HEADER_SIZE)
    header[0] = 0xAA if fast else 1
    struct.pack_into("<Q", header, 1, timestamp_seconds)
    struct.pack_into("<H", header, 11, len(parameters))
    struct.pack_into("<H", header, 13, channel)
    header[15], header[16] = 1, stream
    return _encode_command(header, parameters, material)


def _empty_command(command, material, timestamp_seconds):
    _bounded_number(timestamp_seconds, 0xFFFFFFFFFFFFFFFF, "invalid_timestamp")
    header = bytearray(HEADER_SIZE)
    header[0] = command
    struct.pack_into("<Q", header, 1, timestamp_seconds)
    return _encode_command(header, b"", material)


def build_keepalive(material, *, timestamp_seconds):
    """SendKeepAlive 0x4a37f0: command 0, SHA over the 32-byte header."""
    return _empty_command(0, material, timestamp_seconds)


def build_teardown(material, *, timestamp_seconds):
    """SendTeardown 0x4a5414: command 7; no output/door/gate opcode."""
    return _empty_command(7, material, timestamp_seconds)


@dataclass(frozen=True)
class PacketHeader:
    command: int
    body_length: int
    extension_length: int
    parameter_length: int
    media_offset: int
    media_encrypted: bool
    result: int | None
    action: int | None
    plaintext: bytes = field(repr=False)

    @property
    def is_media(self):
        return self.command in MEDIA_COMMANDS


def decode_packet_header(data, material, *, diagnostics=None):
    """Decode one prefix; optionally retain only allowlisted media metadata."""
    if diagnostics is not None:
        diagnostics.clear()
    if not isinstance(data, bytes) or len(data) != HEADER_SIZE:
        raise MediaProtocolError("header_size")
    raw = _crypt(data, material, decrypt=True)
    command = raw[0]
    if command not in MEDIA_COMMANDS | CONTROL_COMMANDS:
        raise MediaProtocolError("unsupported_command")
    extension_length = struct.unpack_from("<H", raw, 9)[0]
    if command in MEDIA_COMMANDS:
        # OnRecvData 0x4a58f0 reads u32@11; PackFrame 0x4a5b90 reads
        # encrypted extension u16@9, media flag@15 and skipped prefix u16@16.
        body_length = struct.unpack_from("<I", raw, 11)[0]
        media_offset = struct.unpack_from("<H", raw, 16)[0]
        errors = []
        if material.encryption_mode and extension_length % 16:
            errors.append("unaligned_extension")
        if not body_length or body_length > MAX_PACKET_BODY:
            errors.append("media_size")
        if extension_length > body_length:
            errors.append("media_extension_size")
        if media_offset >= body_length:
            errors.append("media_offset")
        if diagnostics is not None:
            diagnostics.update(command=command, body_length=body_length,
                extension_length=extension_length, media_offset=media_offset,
                media_encrypted=bool(raw[15]),
                extension_within_body=extension_length <= body_length,
                offset_within_body=media_offset < body_length,
                offset_before_extension=media_offset < extension_length,
                header_validation_errors=errors.copy())
        if errors:
            raise MediaProtocolError(errors[0])
        return PacketHeader(command, body_length, extension_length, 0,
                            media_offset, bool(raw[15]), None, None, raw)
    if material.encryption_mode and extension_length % 16:
        raise MediaProtocolError("unaligned_extension")
    # OnRecvCommand 0x4a464c only FE carries a variable parameter length at11.
    parameter_length = struct.unpack_from("<H", raw, 11)[0] if command == 0xFE else 0
    if parameter_length > MAX_PARAMETERS:
        raise MediaProtocolError("parameters_size")
    if extension_length < parameter_length + material.digest_size:
        raise MediaProtocolError("control_extension_size")
    return PacketHeader(command, extension_length, extension_length,
                        parameter_length, 0, False, raw[11], raw[12], raw)


@dataclass(frozen=True)
class ControlPacket:
    header: PacketHeader
    parameters: bytes = field(repr=False)


@dataclass(frozen=True)
class MediaChunk:
    header: PacketHeader
    data: bytes = field(repr=False)


def decode_packet(header, body, material):
    if not isinstance(body, bytes) or len(body) != header.body_length:
        raise MediaProtocolError("body_size")
    if header.is_media:
        # PackFrame 0x4a5d94..0x4a5ddc restores the decrypted extension in
        # place. 0x4a5e48..0x4a5e50 selects body+offset, even INSIDE that
        # extension; it is not extension+offset. Media has no SHACheck.
        restored = (_crypt(body[:header.extension_length], material, decrypt=True)
                    + body[header.extension_length:])
        data = restored[header.media_offset:]
        if header.media_encrypted:
            data = _crypt(data, material, decrypt=True)
        return MediaChunk(header, data)
    decoded = _crypt(body, material, decrypt=True)
    parameters = decoded[:header.parameter_length]
    if material.sha_mode:
        digest = decoded[header.parameter_length:header.parameter_length + 32]
        if not compare_digest(digest, sha256(header.plaintext + parameters).digest()):
            raise MediaProtocolError("control_sha_mismatch")
    return ControlPacket(header, parameters)


@dataclass(frozen=True)
class MediaFrame:
    frame_type: int
    codec: int
    width: int
    height: int
    fps: float
    packed_time: int
    milliseconds: int
    payload: bytes = field(repr=False)

    @property
    def is_h264(self):
        # CPacket.FrameIsVideo 0x874728; FrameIsH264 0x874cdc.
        return (self.frame_type in (0, 1, 9, 10, 11)
                and (self.codec == 1 or 0x10 <= self.codec <= 0x1F))

    @property
    def is_keyframe(self):
        return self.frame_type in (1, 4, 11)

    @property
    def is_audio(self):
        # CPacket.FrameIsAudio 0x87488c: exclude metadata types 7/8,
        # then select the explicitly known audio codec identifiers.
        return self.frame_type not in (7, 8) and self.codec in (4, 5, 6, 7, 8, 9, 12, 13)

    @property
    def sample_rate(self):
        # FrameGetFreq 0x875080: LE u16 at byte 16 (video width slot).
        return self.width if self.is_audio else None

    @property
    def channels(self):
        # FrameGetChnNum 0x8750c4: byte 15 (video fps*4 slot).
        return int(self.fps * 4) if self.is_audio else None


class FrameAssembler:
    """Assemble one bounded QV frame across sequential outer media packets.

    CFramePack.PackFrame 0x4aafa4: 20-byte header then payload u32@4.
    There is no scan for guessed start codes. Reset explicitly on a discontinuity.
    """

    def __init__(self):
        self._buffer = bytearray()

    def reset(self):
        self._buffer.clear()

    def feed(self, data):
        if not isinstance(data, bytes) or not data or len(data) > MAX_PACKET_BODY:
            self.reset()
            raise MediaProtocolError("frame_buffer_size")
        # Native 0x4ab040 resets an incomplete frame when the next chunk
        # starts with a QV header. Only the chunk's beginning is examined.
        if len(data) >= 4 and data[:3] == b"\0\0\1" and 0xE0 <= data[3] <= 0xEB:
            self.reset()
        consumed = min(len(data), max(0, FRAME_HEADER_SIZE - len(self._buffer)))
        self._buffer.extend(data[:consumed])
        if len(self._buffer) < FRAME_HEADER_SIZE:
            return []
        raw = self._buffer
        if raw[:3] != b"\0\0\1" or not 0xE0 <= raw[3] <= 0xEB:
            self.reset()
            raise MediaProtocolError("frame_magic")
        payload_length = struct.unpack_from("<I", raw, 4)[0]
        if payload_length > MAX_FRAME_PAYLOAD:
            self.reset()
            raise MediaProtocolError("frame_payload_size")
        total = FRAME_HEADER_SIZE + payload_length
        needed = total - len(raw)
        self._buffer.extend(data[consumed:consumed + needed])
        if len(raw) < total:
            return []
        # 0x4ab45c..0x4ab524 copies only the remaining bytes of this frame;
        # its caller 0x4a6064..0x4a6070 discards the rest of the chunk. Do
        # not invent concatenated frames or a particular padding content.
        frame = MediaFrame(raw[3] - 0xE0, raw[14],
                           struct.unpack_from("<H", raw, 16)[0],
                           struct.unpack_from("<H", raw, 18)[0], raw[15] / 4,
                           struct.unpack_from("<I", raw, 8)[0],
                           struct.unpack_from("<H", raw, 12)[0],
                           bytes(raw[FRAME_HEADER_SIZE:total]))
        self.reset()
        return [frame]
