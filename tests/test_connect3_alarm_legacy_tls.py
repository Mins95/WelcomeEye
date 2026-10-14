"""Legacy native alarm framing inside verified TLS; synthetic bytes only."""
import asyncio
from hashlib import sha256
import json
import struct
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

wire = load('connect3.alarm_protocol')
stream_module = load('connect3.alarm_stream')

USERNAME = 'SYNTHETIC_LEGACY_USER'
PASSWORD = sha256(b'SYNTHETIC_LEGACY_AUTH').hexdigest()
CUSTOMER = 'A0000G0000C0000'


def legacy_setup():
    # Native setup chooses DVR-II from A9 byte 9 in 1..50, independently of
    # bytes 10/11; these fields do not advertise a modern crypto profile.
    response = bytearray(32)
    response[0], response[9], response[10], response[11] = 0xA9, 1, 255, 255
    return wire.parse_control_setup_response(bytes(response))


def native_packet(command, body=b''):
    header = bytearray(32)
    header[0] = command
    struct.pack_into('<I', header, 4, len(body))
    return bytes(header) + body


def native_login(command=0x0A, *, username=USERNAME, password=PASSWORD,
                 client_type=3, customer=CUSTOMER):
    # Independent native fixture, never production login/envelope encoders.
    parameters = (username + '&&' + password).encode('utf-8')
    header = bytearray(32)
    header[:4] = bytes((command, client_type, 1, 0x50))
    struct.pack_into('<I', header, 4, len(parameters))
    header[8] = 255
    header[9:9 + len(customer)] = customer.encode('ascii')
    header[24], header[25], header[30], header[31] = 4, 1, 0xA1, 0xAA
    return bytes(header) + parameters


class TLSLegacyLoginTests(unittest.TestCase):
    def request(self, **overrides):
        options = dict(tls_verified=True, username=USERNAME, password=PASSWORD,
                       client_type=3, customer=CUSTOMER)
        options.update(overrides)
        return wire.build_tls_legacy_login_requests(legacy_setup(), **options)

    def test_exact_independent_native_pair_without_control_envelope(self):
        first, second = self.request()
        self.assertEqual(first, native_login())
        self.assertEqual(second, native_login(0xA0))
        self.assertEqual(first[1:], second[1:])
        self.assertEqual(first[32:], (USERNAME + '&&' + PASSWORD).encode())
        self.assertEqual(len(first), 32 + struct.unpack_from('<I', first, 4)[0])
        self.assertFalse(first.endswith(b'\0'))

    def test_tls_identity_guard_runs_before_credential_validation(self):
        for tls_verified in (False, None, 0, 1, 'verified', object()):
            with self.subTest(tls_verified=tls_verified):
                with self.assertRaises(wire.AlarmProtocolError) as caught:
                    self.request(tls_verified=tls_verified,
                                 username=object(), password=object())
                self.assertNotIn('parameter', str(caught.exception))

    def test_only_successful_legacy_v2_may_construct_secret_packets(self):
        setups = (
            None,
            wire.ControlSetupResponse(wire.SetupVariant.LEGACY_V1, 0, 0, 0),
            wire.ControlSetupResponse(wire.SetupVariant.LEGACY_V2, 7, 0, 0),
            wire.ControlSetupResponse(wire.SetupVariant.MODERN_V3, 0, 0, 0),
            wire.ControlSetupResponse(wire.SetupVariant.MODERN_V3, 0, 2, 1),
            wire.ControlSetupResponse(wire.SetupVariant.REJECTED, 51, 0, 0),
        )
        for setup in setups:
            with self.subTest(setup=setup):
                with self.assertRaises(wire.AlarmProtocolError) as caught:
                    wire.build_tls_legacy_login_requests(setup, tls_verified=True,
                        username=object(), password=object(), client_type=3,
                        customer=CUSTOMER)
                self.assertNotIn('parameter', str(caught.exception))

    def test_credentials_client_and_customer_retain_native_bounds(self):
        for key, values in (
            ('username', (None, '', 'SYNTHETIC&&USER', 'SYNTHETIC\0USER', 'X' * 257)),
            ('password', (None, '', 'SYNTHETIC&&SECRET', 'SYNTHETIC\0SECRET', '\ud800')),
            ('client_type', (-1, 256, True, '3')),
            ('customer', (None, 'X', 'C0000' * 5, '1234\0', '1234é')),
        ):
            for value in values:
                with self.subTest(key=key, type=type(value).__name__):
                    with self.assertRaises(wire.AlarmProtocolError) as caught:
                        self.request(**{key: value})
                    self.assertNotIn('SYNTHETIC', str(caught.exception))

    def test_explicit_client_and_bounded_customer_fixture(self):
        first, second = self.request(client_type=7, customer='C0000')
        self.assertEqual(first, native_login(client_type=7, customer='C0000'))
        self.assertEqual(second, native_login(0xA0, client_type=7, customer='C0000'))


class TLSControlStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader = asyncio.StreamReader()
        self.writer = Mock()
        self.writer.is_closing.return_value = False
        self.writer.drain = AsyncMock()
        self.observation = {}
        self.stream = stream_module.TLSControlStream(self.reader, self.writer,
            self.observation, tls_verified=True)

    def test_default_or_truthy_tls_claim_is_rejected_without_io(self):
        for options in ({}, {'tls_verified': False}, {'tls_verified': None},
                        {'tls_verified': 1}, {'tls_verified': 'verified'}):
            reader, writer = Mock(), Mock()
            with self.subTest(options=options):
                with self.assertRaises(wire.AlarmProtocolError):
                    stream_module.TLSControlStream(reader, writer, {}, **options)
                reader.readexactly.assert_not_called()
                writer.write.assert_not_called()

    async def test_fragmented_native_packet_and_coalesced_following_packet(self):
        first = native_packet(0xB0, b'SYNTHETIC_PRIVATE_LOGIN_BODY')
        second = native_packet(0x69, b'SYNTHETIC_PRIVATE_NOTIFICATION_BODY')
        self.reader.feed_data(first[:7])
        pending = asyncio.create_task(self.stream.read_packet())
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.reader.feed_data(first[7:32])
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.reader.feed_data(first[32:34])
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.reader.feed_data(first[34:] + second)
        self.assertEqual(await pending, first)
        self.assertEqual(await self.stream.read_packet(), second)
        self.assertEqual(self.observation['control_records_received'], 2)
        self.assertEqual(self.observation['control_bytes_received'], len(first + second))
        self.assertFalse(self.observation.get('native_checksum_verified', False))
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(self.observation))

    async def test_login_and_alarm_writes_are_exact_native_bytes(self):
        for packet in (native_login(), native_login(0xA0)):
            await self.stream.write_login(packet)
            self.writer.write.assert_called_with(packet)
        for command in (0x68, 0xA1):
            packet = native_packet(command)
            await self.stream.write_native(packet, allowed_commands=(command,))
            self.writer.write.assert_called_with(packet)
        self.assertEqual(self.writer.write.call_count, 4)
        self.assertEqual(self.writer.drain.await_count, 4)
        self.assertNotIn(PASSWORD, json.dumps(self.observation))

    async def test_relay_media_and_unrequested_commands_cannot_be_written(self):
        for command in (0, 1, 0x10, 0x11, 0xFE, 0xB0, 0x69, 0xA9):
            with self.subTest(command=command):
                with self.assertRaises(wire.AlarmProtocolError):
                    await self.stream.write_login(native_packet(command))
                with self.assertRaises(wire.AlarmProtocolError):
                    await self.stream.write_native(native_packet(command),
                                                   allowed_commands=(command,))
        for command in (0x0A, 0xA0):
            with self.assertRaises(wire.AlarmProtocolError):
                await self.stream.write_native(native_login(command),
                                               allowed_commands=(command,))
        for command in (0x68, 0xA1):
            with self.assertRaises(wire.AlarmProtocolError):
                await self.stream.write_native(native_packet(command),
                                               allowed_commands=())
            with self.assertRaises(wire.AlarmProtocolError):
                await self.stream.write_login(native_packet(command))
        self.writer.write.assert_not_called()
        self.writer.drain.assert_not_awaited()

    async def test_malformed_native_writes_never_reach_writer(self):
        valid = native_login()
        for packet in (None, 'SYNTHETIC_PRIVATE', bytearray(valid), b'',
                       valid[:31], valid[:-1], valid + b'X'):
            with self.subTest(type=type(packet).__name__):
                with self.assertRaises(wire.AlarmProtocolError):
                    await self.stream.write_login(packet)
        self.writer.write.assert_not_called()

    async def test_login_body_bounds_and_empty_subscription_body(self):
        # Native username/password limits are 256 UTF-8 bytes each plus &&.
        bounded = native_login(username='U' * 256, password='P' * 256)
        self.assertEqual(len(bounded) - 32, 514)
        await self.stream.write_login(bounded)
        self.writer.write.assert_called_once_with(bounded)
        self.writer.reset_mock()
        for body in (b'', bytes(515)):
            with self.assertRaises(wire.AlarmProtocolError):
                await self.stream.write_login(native_packet(0x0A, body))
        for command in (0x68, 0xA1):
            with self.assertRaises(wire.AlarmProtocolError):
                await self.stream.write_native(native_packet(command, b'X'),
                                               allowed_commands=(command,))
        self.writer.write.assert_not_called()

    async def test_header_or_body_eof_fails_without_exporting_partial_bytes(self):
        complete = native_packet(0x69, b'SYNTHETIC_PRIVATE_BODY')
        for cut in (0, 7, 31, 32, len(complete) - 1):
            reader, observation = asyncio.StreamReader(), {}
            stream = stream_module.TLSControlStream(reader, self.writer,
                observation, tls_verified=True)
            reader.feed_data(complete[:cut])
            reader.feed_eof()
            with self.subTest(cut=cut):
                with self.assertRaises(asyncio.IncompleteReadError):
                    await stream.read_packet()
                self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(observation))
            stream.clear()

    async def test_oversized_native_body_is_refused_before_body_read(self):
        header = bytearray(32)
        header[0] = 0x69
        struct.pack_into('<I', header, 4, 0xFFFFFFFF)
        reader = Mock()
        reader.readexactly = AsyncMock(return_value=bytes(header))
        stream = stream_module.TLSControlStream(reader, self.writer, {}, tls_verified=True)
        with self.assertRaises(wire.AlarmProtocolError):
            await stream.read_packet()
        reader.readexactly.assert_awaited_once_with(32)

    async def test_total_receive_budget_is_checked_before_body_read(self):
        header = native_packet(0x69, bytes(33))[:32]
        reader = Mock()
        reader.readexactly = AsyncMock(return_value=header)
        stream = stream_module.TLSControlStream(reader, self.writer, {}, tls_verified=True)
        with patch.object(stream_module, 'MAX_WIRE_BYTES', 64):
            with self.assertRaises(wire.AlarmProtocolError):
                await stream.read_packet()
        reader.readexactly.assert_awaited_once_with(32)

    async def test_native_packet_count_budget_does_not_read_next_packet(self):
        self.reader.feed_data(native_packet(0x69) + native_packet(0x69))
        with patch.object(stream_module, 'MAX_RECORDS', 1):
            self.assertEqual(await self.stream.read_packet(), native_packet(0x69))
            with self.assertRaises(wire.AlarmProtocolError):
                await self.stream.read_packet()
        self.assertEqual(self.observation['control_records_received'], 1)

    async def test_closed_writer_refuses_secret_write(self):
        self.writer.is_closing.return_value = True
        with self.assertRaises(wire.AlarmProtocolError):
            await self.stream.write_login(native_login())
        self.writer.write.assert_not_called()

    async def test_clear_is_idempotent_and_closes_read_and_write_paths(self):
        self.stream.clear()
        self.stream.clear()
        self.reader.feed_data(native_packet(0x69, b'SYNTHETIC_PRIVATE'))
        with self.assertRaises(wire.AlarmProtocolError):
            await self.stream.read_packet()
        with self.assertRaises(wire.AlarmProtocolError):
            await self.stream.write_login(native_login())
        with self.assertRaises(wire.AlarmProtocolError):
            await self.stream.write_native(native_packet(0x68), allowed_commands=(0x68,))
        self.writer.write.assert_not_called()
        self.assertNotIn(PASSWORD, json.dumps(self.observation))
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(self.observation))


if __name__ == '__main__':
    unittest.main()
