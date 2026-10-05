"""Synthetic QV framing fixtures derived from native disassembly, never hardware.

Independent expectations use struct/cryptography directly, not production
builders. No private stream key, UID, device response or physical command.
"""
from hashlib import sha256
import struct
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from load_integration import load

p = load("connect3.protocol")
KEY = "SYNTHETIC_ASCII_STREAM_KEY_32_CHARACTERS_NOT_HEX"
MATERIAL = p.CipherMaterial(KEY)


def aes(data, *, decrypt=False, mode=2):
    key = KEY.encode("ascii")[:16 if mode == 1 else 32]
    cipher = Cipher(algorithms.AES(key), modes.CBC(b"0" * 16))
    operation = cipher.decryptor() if decrypt else cipher.encryptor()
    return operation.update(data) + operation.finalize()


def control_response(*, command=1, result=0, action=1, parameters=b"", bad_sha=False):
    # OnRecvCommand 0x4a464c: extension length u16@9, response fields@11/12;
    # only FE supplies parameter length u16@11 rather than a status byte.
    header = bytearray(32)
    header[0] = command
    extension_size = (len(parameters) + 32 + 15) // 16 * 16
    struct.pack_into("<H", header, 9, extension_size)
    if command == 0xFE:
        struct.pack_into("<H", header, 11, len(parameters))
    else:
        header[11], header[12] = result, action
    digest = bytes(32) if bad_sha else sha256(bytes(header) + parameters).digest()
    body = parameters + digest
    body += bytes(extension_size - len(body))
    return aes(bytes(header)), aes(body)


def frame_bytes(*, payload=b"\0\0\0\1\x67SYNTHETIC", frame_type=1, codec=1):
    # CFramePack 0x4aafa4 / CPacket accessors 0x874240 etc: QV20 then H264.
    header = bytearray(20)
    header[:4] = b"\0\0\1" + bytes((0xE0 + frame_type,))
    struct.pack_into("<I", header, 4, len(payload))
    struct.pack_into("<I", header, 8, 0xABCDEF01)
    struct.pack_into("<H", header, 12, 321)
    header[14], header[15] = codec, 80
    struct.pack_into("<HH", header, 16, 1920, 1080)
    return bytes(header) + payload


def media_response(frame, *, command=0xA1, encrypted=True, extension=b""):
    # OnRecvData 0x4a58f0: total body u32@11; PackFrame0x4a5b90:
    # extension u16@9, encrypted flag@15, skipped prefix u16@16.
    encoded_frame = frame
    if encrypted:
        encoded_frame += bytes((-len(encoded_frame)) % 16)
        encoded_frame = aes(encoded_frame)
    header = bytearray(32)
    header[0] = command
    struct.pack_into("<H", header, 9, len(extension))
    struct.pack_into("<I", header, 11, len(extension) + len(encoded_frame))
    header[15] = int(encrypted)
    struct.pack_into("<H", header, 16, len(extension))
    return aes(bytes(header)), aes(extension) + encoded_frame


class SetupTests(unittest.TestCase):
    def test_exact_native_request(self):
        self.assertEqual(p.build_setup_request(), b"\xa9" + bytes(31))

    def test_accept_and_rejection_metadata(self):
        data = bytearray(32)
        data[0], data[10], data[11] = 0xA9, 2, 1
        result = p.parse_setup_response(bytes(data))
        self.assertEqual((result.result, result.encryption_mode, result.sha_mode), (0, 2, 1))
        data[9] = 2
        self.assertEqual(p.parse_setup_response(bytes(data)).result, 2)

    def test_setup_size_command_and_modes(self):
        for data in (b"", bytes(31), bytes(33), bytes(32), b"\xa9" + bytes(9) + b"\x03" + bytes(21),
                     b"\xa9" + bytes(10) + b"\x02" + bytes(20)):
            with self.subTest(length=len(data)), self.assertRaises(p.MediaProtocolError):
                p.parse_setup_response(data)


class BuilderTests(unittest.TestCase):
    def test_play_independent_wire_and_ascii_key(self):
        data = p.build_play_request(MATERIAL, username="adminapp2", password="SYNTHETIC_TDC",
                                    channel=1, stream=0, timestamp_seconds=1700000000)
        header, body = aes(data[:32], decrypt=True), aes(data[32:], decrypt=True)
        params = b"adminapp2&&SYNTHETIC_TDC\0\0"
        expected = bytearray(32)
        expected[0] = 1
        struct.pack_into("<Q", expected, 1, 1700000000)
        struct.pack_into("<H", expected, 9, 64)
        struct.pack_into("<H", expected, 11, len(params))
        struct.pack_into("<H", expected, 13, 1)
        expected[15] = 1
        self.assertEqual(header, bytes(expected))
        self.assertEqual(body, params + sha256(header + params).digest() + bytes(64 - len(params) - 32))
        self.assertEqual(MATERIAL.key, KEY.encode()[:32])
        self.assertNotIn(KEY, repr(MATERIAL))

    def test_custom_and_client_ids_native_nul_separators(self):
        data = p.build_play_request(MATERIAL, username="adminapp2", password="SYNTHETIC_TDC",
                                    channel=2, stream=1, timestamp_seconds=1,
                                    custom_id="SYNTHETIC_CUSTOM", client_id="SYNTHETIC_CLIENT")
        body = aes(data[32:], decrypt=True)
        self.assertTrue(body.startswith(b"adminapp2&&SYNTHETIC_TDC\0SYNTHETIC_CUSTOM\0SYNTHETIC_CLIENT\0"))

    def test_keepalive_and_teardown_only_live_commands(self):
        for builder, command in ((p.build_keepalive, 0), (p.build_teardown, 7)):
            data = builder(MATERIAL, timestamp_seconds=5)
            header, body = aes(data[:32], decrypt=True), aes(data[32:], decrypt=True)
            expected = bytearray(32)
            expected[0] = command
            struct.pack_into("<Q", expected, 1, 5)
            struct.pack_into("<H", expected, 9, 32)
            self.assertEqual(header, bytes(expected))
            self.assertEqual(body, sha256(header).digest())
            self.assertEqual(p.decode_packet(p.decode_packet_header(data[:32], MATERIAL), data[32:], MATERIAL).header.command, command)

    def test_mode_zero_and_one(self):
        plain = p.CipherMaterial(KEY, 0, 0)
        data = p.build_keepalive(plain, timestamp_seconds=1)
        self.assertEqual(len(data), 32)
        self.assertEqual(p.decode_packet(p.decode_packet_header(data, plain), b"", plain).header.command, 0)
        mode_one = p.CipherMaterial(KEY, 1, 1)
        data = p.build_keepalive(mode_one, timestamp_seconds=1)
        self.assertEqual(aes(data[:32], decrypt=True, mode=1)[0], 0)
        self.assertEqual(p.decode_packet(p.decode_packet_header(data[:32], mode_one), data[32:], mode_one).header.command, 0)

    def test_invalid_inputs_never_leak_secrets(self):
        for key in ("", "short", "\0" * 32, "é" * 32):
            with self.subTest(key_length=len(key)), self.assertRaises(p.MediaProtocolError):
                p.CipherMaterial(key)
        for kwargs in ({"channel": -1}, {"stream": 256}, {"timestamp_seconds": True},
                       {"password": "SECRET\0VALUE"}):
            fields = dict(username="adminapp2", password="SYNTHETIC", channel=1, stream=0, timestamp_seconds=1)
            fields.update(kwargs)
            with self.assertRaises(p.MediaProtocolError) as caught:
                p.build_play_request(MATERIAL, **fields)
            self.assertNotIn("SECRET", str(caught.exception))


class PacketTests(unittest.TestCase):
    def test_control_success_and_auth_refusal(self):
        for status in (0, 1, 2, 255):
            header, body = control_response(result=status)
            packet = p.decode_packet(p.decode_packet_header(header, MATERIAL), body, MATERIAL)
            self.assertIsInstance(packet, p.ControlPacket)
            self.assertEqual((packet.header.result, packet.header.action), (status, 1))
            self.assertNotIn(body.hex(), repr(packet))

    def test_fe_parameters_and_sha_checked(self):
        header, body = control_response(command=0xFE, parameters=b"SYNTHETIC")
        self.assertEqual(p.decode_packet(p.decode_packet_header(header, MATERIAL), body, MATERIAL).parameters, b"SYNTHETIC")
        header, body = control_response(bad_sha=True)
        with self.assertRaisesRegex(p.MediaProtocolError, "control_sha_mismatch"):
            p.decode_packet(p.decode_packet_header(header, MATERIAL), body, MATERIAL)

    def test_plain_and_encrypted_media_with_independent_extension(self):
        for encrypted in (False, True):
            frame = frame_bytes()
            header, body = media_response(frame, encrypted=encrypted, extension=b"SYNTHETIC_EXT__!")
            parsed = p.decode_packet_header(header, MATERIAL)
            self.assertEqual(parsed.body_length, len(body))
            self.assertEqual(parsed.media_offset, 16)
            chunk = p.decode_packet(parsed, body, MATERIAL)
            self.assertIsInstance(chunk, p.MediaChunk)
            received = p.FrameAssembler().feed(chunk.data)[0]
            self.assertEqual(received.payload, frame[20:])
            self.assertTrue(received.is_h264)
            self.assertTrue(received.is_keyframe)
            self.assertEqual((received.width, received.height, received.fps), (1920, 1080, 20.0))
            self.assertEqual((received.packed_time, received.milliseconds), (0xABCDEF01, 321))

    def test_header_size_command_lengths_and_alignment(self):
        with self.assertRaisesRegex(p.MediaProtocolError, "header_size"):
            p.decode_packet_header(bytes(31), MATERIAL)
        cases = []
        raw = bytearray(32)
        raw[0] = 0x99
        cases.append((bytes(raw), "unsupported_command"))
        raw[0] = 1
        struct.pack_into("<H", raw, 9, 17)
        cases.append((bytes(raw), "unaligned_extension"))
        struct.pack_into("<H", raw, 9, 16)
        cases.append((bytes(raw), "control_extension_size"))
        raw = bytearray(32)
        raw[0] = 0xA1
        struct.pack_into("<I", raw, 11, p.MAX_PACKET_BODY + 1)
        cases.append((bytes(raw), "media_size"))
        struct.pack_into("<I", raw, 11, 32)
        struct.pack_into("<H", raw, 16, 32)
        cases.append((bytes(raw), "media_offset"))
        for raw, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(p.MediaProtocolError, reason):
                p.decode_packet_header(aes(raw), MATERIAL)

    def test_incomplete_body_and_encrypted_unaligned_media(self):
        header, body = control_response()
        with self.assertRaisesRegex(p.MediaProtocolError, "body_size"):
            p.decode_packet(p.decode_packet_header(header, MATERIAL), body[:-1], MATERIAL)
        raw = bytearray(32)
        raw[0], raw[15] = 0xA1, 1
        struct.pack_into("<I", raw, 11, 31)
        with self.assertRaisesRegex(p.MediaProtocolError, "unaligned_cipher_input"):
            p.decode_packet(p.decode_packet_header(aes(bytes(raw)), MATERIAL), bytes(31), MATERIAL)


class FrameTests(unittest.TestCase):
    def test_fragmented_frame_and_reset(self):
        frame = frame_bytes()
        assembler = p.FrameAssembler()
        for piece in (frame[:1], frame[1:4], frame[4:19], frame[19:25]):
            self.assertEqual(assembler.feed(piece), [])
        self.assertEqual(assembler.feed(frame[25:])[0].payload, frame[20:])
        self.assertEqual(assembler.feed(frame[:7]), [])
        assembler.reset()
        self.assertEqual(assembler.feed(frame)[0].payload, frame[20:])

    def test_supported_h264_codec_range_and_other_media_classification(self):
        for codec in (1, 0x10, 0x1F):
            self.assertTrue(p.FrameAssembler().feed(frame_bytes(codec=codec))[0].is_h264)
        for codec, frame_type in ((0x20, 1), (4, 3), (1, 4)):
            self.assertFalse(p.FrameAssembler().feed(frame_bytes(codec=codec, frame_type=frame_type))[0].is_h264)

    def test_native_discards_chunk_tail_and_replaces_incomplete_frame(self):
        first = frame_bytes(payload=b"SYNTHETIC_FIRST")
        second = frame_bytes(payload=b"SYNTHETIC_SECOND")
        assembler = p.FrameAssembler()
        self.assertEqual(assembler.feed(first + second)[0].payload, first[20:])
        self.assertEqual(assembler.feed(second)[0].payload, second[20:])
        self.assertEqual(assembler.feed(first[:-2]), [])
        self.assertEqual(assembler.feed(second)[0].payload, second[20:])
        self.assertEqual(assembler.feed(first + b"nonzero tail")[0].payload, first[20:])

    def test_zero_payload_and_frame_larger_than_outer_chunk(self):
        assembler = p.FrameAssembler()
        self.assertEqual(assembler.feed(frame_bytes(payload=b""))[0].payload, b"")
        payload = b"S" * (p.MAX_PACKET_BODY + 64)
        frame = frame_bytes(payload=payload)
        self.assertEqual(assembler.feed(frame[:p.MAX_PACKET_BODY]), [])
        self.assertEqual(assembler.feed(frame[p.MAX_PACKET_BODY:])[0].payload, payload)

    def test_bad_magic_oversized_payload_and_bounds(self):
        frame = frame_bytes()
        huge = bytearray(frame)
        struct.pack_into("<I", huge, 4, p.MAX_FRAME_PAYLOAD + 1)
        for data, reason in ((b"x" * 20, "frame_magic"), (bytes(huge), "frame_payload_size"),
                             (bytes(p.MAX_PACKET_BODY + 1), "frame_buffer_size"),
                             (b"", "frame_buffer_size")):
            assembler = p.FrameAssembler()
            with self.subTest(reason=reason), self.assertRaisesRegex(p.MediaProtocolError, reason):
                assembler.feed(data)
            self.assertEqual(assembler.feed(frame)[0].payload, frame[20:])


if __name__ == "__main__":
    unittest.main()
