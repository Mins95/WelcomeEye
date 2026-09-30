"""Reported hardware PREFIXES only; all complete responses below are SYNTHETIC."""
import asyncio
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_r002 import Writer, transport, protocol, hub_module

observation = load('r002.observation')

# Exact prefixes from tinymop21, issue #7 comment 5914474400 (2026-09-30).
# No invented continuation or claim that these are complete hardware responses.
HARDWARE_PREFIXES = {
    14: bytes.fromhex('0e00 0100 0000 0600 0000 0e00'),
    15: bytes.fromhex('0f00 0100 0000 0e00 0000 0f00'),
    26: bytes.fromhex('1a00 0100 0000 1400 0000 0000'),
    28: bytes.fromhex('1c00 0100 0000 0000 0000 0000'),
}


def synthetic_response(header_size=12, body=b'abcdef', kind=14):
    return struct.pack('<HHHH', kind, 1, 0, len(body)) + bytes(header_size - 8) + body


class AnalysisTests(unittest.TestCase):
    def test_reported_prefixes_without_invented_body(self):
        for kind, raw in HARDWARE_PREFIXES.items():
            result = observation.analyze_response(raw, kind)
            self.assertEqual(result['bytes_collected'], 12)
            self.assertEqual(result['raw_header_hex'], raw.hex())
            self.assertEqual(result['header_validation_errors'], ['nonzero_field_8_11'] if kind in (14, 15) else [])
            self.assertFalse(result['framing_confirmed'])
            if kind in (14, 15):
                self.assertEqual(result['decoded_candidate']['field_8_11_u32'], kind << 16)
                for size in (10, 12):
                    self.assertIsNone(result[f'header_{size}_candidate']['body_candidate_hex'])
            if kind == 28:
                self.assertEqual(result['framing_assessment'], 'ambiguous')

    def test_synthetic_ten_byte_header_only_has_sufficient_ten_slice(self):
        result = observation.analyze_response(synthetic_response(10), 14)
        ten, twelve = result['header_10_candidate'], result['header_12_candidate']
        self.assertEqual(ten['body_candidate_hex'], b'abcdef'.hex())
        self.assertEqual((ten['expected_total'], ten['missing_bytes'], ten['extra_bytes']), (16, 0, 0))
        self.assertEqual((twelve['body_bytes_available'], twelve['missing_bytes']), (4, 2))
        self.assertIsNone(twelve['body_candidate_hex'])
        self.assertEqual(result['framing_assessment'], 'header_10_has_enough_bytes')
        self.assertFalse(result['framing_confirmed'])

    def test_synthetic_twelve_byte_header_still_ambiguous(self):
        result = observation.analyze_response(synthetic_response(12), 14)
        self.assertEqual(result['header_12_candidate']['body_candidate_hex'], b'abcdef'.hex())
        self.assertEqual(result['header_10_candidate']['body_candidate_hex'], b'\x00\x00abcd'.hex())
        self.assertEqual(result['header_10_candidate']['extra_hex'], b'ef'.hex())
        self.assertEqual(result['framing_assessment'], 'ambiguous')
        self.assertFalse(result['framing_confirmed'])

    def test_truncated_and_extra_synthetic_bytes(self):
        short = observation.analyze_response(synthetic_response()[:14], 14)
        self.assertEqual(short['header_12_candidate']['missing_bytes'], 4)
        self.assertEqual(short['framing_assessment'], 'insufficient_data')
        extra = observation.analyze_response(synthetic_response() + b'EXTRA', 14)
        self.assertEqual(extra['header_12_candidate']['extra_bytes'], 5)
        self.assertEqual(extra['header_12_candidate']['extra_hex'], b'EXTRA'.hex())
        self.assertEqual(extra['framing_assessment'], 'ambiguous')

    def test_incomplete_length_no_guess_and_oversized_length_no_allocation(self):
        for size in range(8):
            result = observation.analyze_response(synthetic_response()[:size], 14)
            self.assertIsNone(result['header_10_candidate']['declared_length'])
            self.assertIsNone(result['header_12_candidate']['expected_total'])
        raw = struct.pack('<HHHHI', 14, 1, 0, 65535, 0)
        result = observation.analyze_response(raw, 14)
        self.assertEqual(result['header_12_candidate']['missing_bytes'], 65535)
        self.assertEqual(result['header_validation_errors'], ['oversized_length'])
        self.assertIsNone(result['header_12_candidate']['body_candidate_hex'])
        with self.assertRaises(ValueError):
            observation.analyze_response(bytes(65), 14)


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, data, *, eof=True, fragmented=False, fail=None, kind=14, include_header=False):
        reader, writer = asyncio.StreamReader(), Writer()
        reader.read = AsyncMock(side_effect=reader.read)
        reader.readexactly = AsyncMock(side_effect=AssertionError('No header-sized observation read'))
        async def produce():
            for chunk in ([bytes([byte]) for byte in data] if fragmented else [data]):
                reader.feed_data(chunk)
                await asyncio.sleep(0)
            if fail:
                # Deliver the bytes before surfacing a subsequent socket error.
                while reader._buffer:
                    await asyncio.sleep(0)
                reader.set_exception(fail)
            elif eof:
                reader.feed_eof()
        producer = asyncio.create_task(produce())
        counters = transport.new_counters()
        with patch.object(transport, 'REQUEST_TIMEOUT', .25), patch.object(
            transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connect:
            result = await transport.probe_one('192.0.2.1', kind, counters,
                include_response=True, include_header=include_header)
        await producer
        self.assertEqual(connect.await_count, 1)
        self.assertEqual(connect.await_args.args[1], 8765)
        self.assertEqual(writer.sent, [protocol.request(kind)])
        self.assertTrue(writer.closed)
        self.assertEqual(counters['connects'], counters['disconnects'])
        reader.readexactly.assert_not_called()
        self.assertTrue(all(0 < call.args[0] <= 64 for call in reader.read.await_args_list))
        return result, counters, reader

    async def test_fragmented_eof_and_no_wait_for_sixty_four(self):
        raw = synthetic_response()
        result, counters, reader = await self.collect(raw, fragmented=True)
        self.assertGreater(reader.read.await_count, 1)
        self.assertEqual(result['response_hex'], raw.hex())
        self.assertEqual(result['collection_end_reason'], 'remote_eof')
        self.assertEqual(result['observation_status'], 'collected')
        self.assertEqual(counters['remote_eof'], 1)
        self.assertEqual(counters['observation_deadlines'], 0)

    async def test_deadline_retains_fragments_and_is_not_parse_failure(self):
        raw = synthetic_response(10)
        with self.assertLogs(transport._LOGGER, 'DEBUG') as logs:
            result, counters, _ = await self.collect(raw, eof=False, fragmented=True)
        self.assertEqual(result['response_hex'], raw.hex())
        self.assertEqual(result['collection_end_reason'], 'deadline')
        self.assertEqual(result['status'], 'observed')
        self.assertIsNone(result['last_error_type'])
        self.assertEqual(counters['observation_deadlines'], 1)
        self.assertEqual(counters['remote_eof'], 0)  # Client close is not server EOF.
        self.assertEqual(counters['timeouts'], 0)
        self.assertNotIn('probe.failed', str(logs.output))

    async def test_empty_eof_and_empty_deadline_distinguished(self):
        for eof, reason in ((True, 'remote_eof'), (False, 'deadline')):
            result, _, _ = await self.collect(b'', eof=eof)
            self.assertEqual(result['bytes_collected'], 0)
            self.assertEqual(result['collection_end_reason'], reason)
            self.assertEqual(result['observation_status'], 'empty')

    async def test_size_cap_exact_no_peek_or_eof_inference(self):
        raw = synthetic_response(body=bytes(range(100)))
        result, counters, reader = await self.collect(raw)
        self.assertEqual(result['response_hex'], raw[:64].hex())
        self.assertEqual(result['bytes_collected'], 64)
        self.assertEqual(result['collection_end_reason'], 'size_limit')
        self.assertEqual(counters['bytes_rx'], 64)
        self.assertEqual(counters['remote_eof'], 0)
        reader.read.assert_awaited_once_with(64)

    async def test_truncation_preserved_on_real_eof(self):
        raw = synthetic_response()[:11]
        result, _, _ = await self.collect(raw)
        self.assertEqual(result['raw_header_hex'], raw.hex())
        self.assertEqual(result['header_validation_errors'], ['invalid_header_size'])
        self.assertEqual(result['collection_end_reason'], 'remote_eof')
        self.assertEqual(result['status'], 'observed')

    async def test_network_error_preserves_bytes_and_is_distinct(self):
        raw = synthetic_response()
        result, counters, _ = await self.collect(raw, fail=ConnectionResetError('secret_address'))
        self.assertEqual(result['response_hex'], raw.hex())
        self.assertEqual(result['collection_end_reason'], 'network_error')
        self.assertEqual(result['observation_status'], 'network_error')
        self.assertEqual(result['last_error_type'], 'ConnectionResetError')
        self.assertEqual(counters['remote_eof'], 0)
        self.assertNotIn('secret_address', json.dumps(result))

    async def test_connect_deadline_is_not_an_observed_response(self):
        async def stalled(*args, **kwargs):
            await asyncio.Event().wait()
        with patch.object(transport, 'REQUEST_TIMEOUT', .01), patch.object(
            transport.asyncio, 'open_connection', side_effect=stalled) as connect:
            result = await transport.probe_one('192.0.2.1', 14, transport.new_counters(), include_response=True)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(result['collection_end_reason'], 'deadline')
        self.assertEqual(result['observation_status'], 'not_started')
        self.assertEqual(result['last_stage'], 'connecting')
        self.assertEqual(result['last_error_type'], 'TimeoutError')

    async def test_os_socket_timeout_is_not_our_observation_deadline(self):
        result, counters, _ = await self.collect(b'partial', fail=TimeoutError('OS timeout'))
        self.assertEqual(result['collection_end_reason'], 'network_error')
        self.assertEqual(result['last_error_type'], 'TimeoutError')
        self.assertEqual(counters['observation_deadlines'], 0)

    async def test_single_absolute_deadline_not_reset_on_fragments(self):
        self.assertEqual(transport.REQUEST_TIMEOUT, 3.0)
        with patch.object(transport.asyncio, 'timeout', wraps=asyncio.timeout) as deadline:
            await self.collect(synthetic_response(), fragmented=True)
        # One network deadline at entry, and the existing bounded close only.
        self.assertEqual([call.args[0] for call in deadline.call_args_list], [.25, 1.0])

    async def test_header_option_does_not_add_a_second_read_path(self):
        result, _, reader = await self.collect(synthetic_response(), include_header=True)
        self.assertEqual(result['bytes_collected'], 18)
        self.assertEqual(result['prefix_bytes_received'], 12)
        reader.readexactly.assert_not_called()


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_requests_and_no_persistent_raw_history(self):
        entry = SimpleNamespace(data={'host': '192.0.2.1'}, options={})
        hub = hub_module.R002InvestigationHub(None, entry)
        await hub.start()
        writers = []
        raw_values = []
        async def connect(*args, **kwargs):
            kind = protocol.ALLOWED_TYPES[len(writers)]
            # Complete SYNTHETIC responses, not continuations of hardware prefixes.
            raw = synthetic_response(body=b'SYNTHETIC_PRIVATE', kind=kind)
            raw_values.append(raw)
            reader, writer = asyncio.StreamReader(), Writer()
            reader.feed_data(raw); reader.feed_eof()
            writers.append(writer)
            return reader, writer
        with patch.object(transport.asyncio, 'open_connection', side_effect=connect) as connections, self.assertLogs(
            'welcomeeye_offline_fixture.r002', 'DEBUG') as logs:
            result = await hub.probe(include_response=True)
        self.assertEqual(connections.call_count, 4)
        for kind, writer in zip(protocol.ALLOWED_TYPES, writers):
            self.assertEqual(writer.sent, [protocol.request(kind)])
            self.assertTrue(writer.closed)
            self.assertEqual(hub._per_type[str(kind)]['observations'], 1)
            self.assertEqual(hub._per_type[str(kind)]['successes'], 0)
        self.assertEqual(hub.status, 'probe_observed')
        self.assertIsNone(hub._task)
        self.assertIn('response_hex', result['results']['14'])
        persisted = json.dumps([hub.diagnostics(), hub._per_type, entry.data, entry.options]) + str(logs.output)
        for value in ['response_hex', 'decoded_candidate', 'header_10_candidate', 'header_12_candidate',
                      'raw_header_hex', 'body_candidate_hex', 'extra_hex', 'SYNTHETIC_PRIVATE',
                      b'SYNTHETIC_PRIVATE'.hex()] + [raw.hex() for raw in raw_values]:
            self.assertNotIn(value, persisted)
        await hub.stop()

    async def test_unload_cancels_collection_without_retries_or_late_callbacks(self):
        hub = hub_module.R002InvestigationHub(None, SimpleNamespace(data={'host': '192.0.2.1'}))
        await hub.start()
        entered = asyncio.Event()
        reader, writer = asyncio.StreamReader(), Writer()
        async def read(size):
            entered.set()
            return await asyncio.StreamReader.read(reader, size)
        reader.read = read
        callback = Mock()
        hub.subscribe(callback)
        with patch.object(transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connect:
            task = asyncio.create_task(hub.probe(include_response=True))
            await entered.wait()
            with self.assertRaises(RuntimeError):
                await hub.probe([14], include_response=True)
            await hub.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(connect.await_count, 1)
        self.assertEqual(writer.sent, [protocol.request(14)])
        self.assertTrue(writer.closed)
        self.assertEqual(callback.call_count, 1)  # Only initial "probing" callback.
        self.assertIsNone(hub._task)
