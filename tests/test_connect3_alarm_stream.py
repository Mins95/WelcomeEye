"""Encrypted control framing tests with synthetic bytes only."""
import asyncio
import struct
import unittest
from unittest.mock import Mock

from load_integration import load

wire = load('connect3.alarm_protocol')
stream_module = load('connect3.alarm_stream')


def packet(command, body=b''):
    header = bytearray(32)
    header[0] = command
    struct.pack_into('<I', header, 4, len(body))
    return bytes(header) + body


class ControlStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.material = wire.ControlCipherMaterial('SYNTHETIC_KEY_' + 'K' * 32)
        self.reader = asyncio.StreamReader()
        self.obs = {}
        self.stream = stream_module.ControlStream(self.reader, Mock(), self.material, self.obs)

    def feed(self, chunk):
        self.reader.feed_data(wire.encode_control_packet(chunk, self.material, timestamp_seconds=17))

    async def test_packet_spanning_records_and_multiple_packets_in_one_record(self):
        one, two = packet(0x69, b'ONE'), packet(0x69, b'TWO')
        self.feed(one[:32])
        self.feed(one[32:] + two)
        self.assertEqual(await self.stream.read_packet(), one)
        self.assertEqual(await self.stream.read_packet(), two)
        self.assertEqual(self.obs['control_records_received'], 2)
        self.assertTrue(self.obs['native_checksum_verified'])

    async def test_corrupt_digest_does_not_release_payload(self):
        data = bytearray(wire.encode_control_packet(packet(0x69, b'PRIVATE'),
            self.material, timestamp_seconds=1))
        data[-17] ^= 1
        self.reader.feed_data(data)
        with self.assertRaises(wire.AlarmProtocolError):
            await self.stream.read_packet()
        self.assertEqual(self.stream._buffer, b'')

    async def test_partial_frame_eof(self):
        self.feed(packet(0x69, b'BODY')[:32])
        self.reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.stream.read_packet()

    async def test_physical_or_media_packet_not_allowed_on_write(self):
        for command in (0xFE, 1, 0x10, 0x11):
            with self.assertRaises(wire.AlarmProtocolError):
                await self.stream.write_native(packet(command), allowed_commands=(0x68,))
        self.stream.writer.write.assert_not_called()

    async def test_total_receive_limit_before_body_read(self):
        self.stream._received = stream_module.MAX_WIRE_BYTES - 32
        self.feed(packet(0x69))
        with self.assertRaises(wire.AlarmProtocolError):
            await self.stream.read_packet()

    def test_clear_removes_key_and_partial_plaintext(self):
        self.stream._buffer.extend(b'PRIVATE')
        self.stream.clear()
        self.assertEqual(self.stream._buffer, b'')
        self.assertIsNone(self.stream._material)
