"""Explicitly SYNTHETIC prefixes/certificates, never hardware fixtures."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_r002 import transport, protocol, fingerprint, hub_module, Writer, packet
from test_r002_certificate import synthetic_certificate


class PrefixTests(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, raw, *, detailed=True, kind=14, fragment=False):
        reader, writer = asyncio.StreamReader(), Writer()
        if fragment:
            async def deliver():
                for byte in raw:
                    reader.feed_data(bytes([byte]))
                    await asyncio.sleep(0)
                reader.feed_eof()
            producer = asyncio.create_task(deliver())
        else:
            reader.feed_data(raw)
            reader.feed_eof()
            producer = None
        original = reader.readexactly
        reader.readexactly = AsyncMock(side_effect=original)
        with patch.object(transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connection:
            result = await transport.probe_one('192.0.2.1', kind, transport.new_counters(), include_header=detailed)
        if producer:
            await producer
        self.assertEqual(connection.await_count, 1)
        self.assertEqual(writer.sent, [protocol.request(kind)])
        self.assertTrue(writer.closed)
        return result, reader

    async def test_rejected_candidates_keep_all_errors_without_body_read(self):
        cases = [({'padding': 0x12345678}, ['nonzero_field_8_11']),
                 ({'flags': 8}, ['nonzero_flags']),
                 ({'kind': 99, 'version': 9, 'flags': 8, 'length': 65535, 'padding': 42},
                  ['unexpected_type', 'unexpected_version', 'nonzero_flags', 'nonzero_field_8_11', 'oversized_length'])]
        for fields, errors in cases:
            raw = packet(**fields)[:12]+b'BODY_MUST_NOT_BE_READ'
            result, reader = await self.run_probe(raw)
            self.assertEqual(result['header_validation_errors'], errors)
            self.assertEqual(result['raw_header_hex'], raw[:12].hex())
            self.assertEqual(result['prefix_bytes_received'], 12)
            self.assertEqual(result['last_stage'], 'response_header')
            self.assertEqual(result['status'], 'failed')
            self.assertIn('field_8_11_u32', result['decoded_candidate'])
            reader.readexactly.assert_awaited_once_with(12)

    async def test_default_omits_raw_and_ambiguous_values(self):
        result, _ = await self.run_probe(packet(padding=0x12345678), detailed=False)
        self.assertNotIn('decoded_candidate', result)
        self.assertNotIn('raw_header_hex', result)
        self.assertNotIn('305419896', json.dumps(result))
        self.assertEqual(result['header_validation_errors'], ['nonzero_field_8_11'])

    async def test_short_eof_keeps_only_received_bytes(self):
        for length in (0, 1, 7, 11):
            raw = packet()[:length]
            result, reader = await self.run_probe(raw, fragment=True)
            self.assertEqual(result['last_error_type'], 'IncompleteReadError')
            self.assertEqual(result['raw_header_hex'], raw.hex())
            self.assertEqual(result['prefix_bytes_received'], length)
            self.assertIsNone(result['decoded_candidate'])
            self.assertEqual(result['header_validation_errors'], ['invalid_header_size'])
            reader.readexactly.assert_awaited_once_with(12)

    async def test_accepted_26_28_fragmented_unchanged(self):
        for kind, length in ((26, 20), (28, 0)):
            raw = packet(kind=kind, length=length)
            result, reader = await self.run_probe(raw, kind=kind, fragment=True)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['received_length'], length)
            self.assertEqual(result['header_validation_errors'], [])
            self.assertEqual([c.args[0] for c in reader.readexactly.await_args_list], [12, length])

    async def test_detailed_timeout_has_no_extra_read_or_request(self):
        reader, writer = asyncio.StreamReader(), Writer()
        reader.readexactly = AsyncMock(side_effect=reader.readexactly)
        with patch.object(transport, 'REQUEST_TIMEOUT', .01), patch.object(
            transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connect:
            result = await transport.probe_one('192.0.2.1', 14, transport.new_counters(), include_header=True)
        self.assertEqual(result['last_error_type'], 'TimeoutError')
        self.assertEqual(result['raw_header_hex'], '')
        self.assertEqual(result['prefix_bytes_received'], 0)
        reader.readexactly.assert_awaited_once_with(12)
        self.assertEqual(connect.await_count, 1)
        self.assertEqual(writer.sent, [protocol.request(14)])
        self.assertTrue(writer.closed)

    async def test_detailed_probe_never_persists_prefix_or_candidate(self):
        entry = SimpleNamespace(data={'host': '192.0.2.1'}, options={})
        hub = hub_module.R002InvestigationHub(None, entry)
        await hub.start()
        raw = packet(padding=0x12345678)[:12]
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(raw); reader.feed_eof()
        with patch.object(transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))), self.assertLogs('welcomeeye_offline_fixture.r002', 'DEBUG') as logs:
            response = await hub.probe([14], include_header=True)
        self.assertEqual(response['results']['14']['raw_header_hex'], raw.hex())
        persisted = json.dumps([hub.diagnostics(), hub._per_type, entry.data, entry.options]) + str(logs.output)
        for forbidden in ('raw_header_hex', 'decoded_candidate', 'field_8_11_u32', raw.hex(), '305419896'):
            self.assertNotIn(forbidden, persisted)
        self.assertEqual(hub._per_type['14']['header_validation_errors'], ['nonzero_field_8_11'])
        self.assertIsNone(hub._task)
        await hub.stop()


class CertificateNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_recheck_unload_cancels_and_excludes_concurrent_probe(self):
        entry = SimpleNamespace(data={'host': '192.0.2.1'})
        hub = hub_module.R002InvestigationHub(None, entry)
        await hub.start()
        entered = asyncio.Event()
        async def check(host):
            entered.set()
            await asyncio.Event().wait()
        callback = Mock()
        hub.subscribe(callback)
        with patch.object(hub_module, 'check_certificate', side_effect=check):
            task = asyncio.create_task(hub.check_certificate())
            await entered.wait()
            with self.assertRaises(RuntimeError):
                await hub.probe([14])
            await hub.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        callback.assert_not_called()
        self.assertIsNone(hub._task)
        self.assertEqual(hub.certificate_check_runs, 0)

    async def test_recheck_tls_only_and_no_identity_changes(self):
        writer = Writer()
        writer.get_extra_info = lambda key: SimpleNamespace(getpeercert=lambda **kwargs: synthetic_certificate(b'\x00'))
        entry = SimpleNamespace(unique_id='r002-fixed', data={'host': '192.0.2.1', 'fingerprint': {'detected': True}}, options={})
        hub = hub_module.R002InvestigationHub(None, entry)
        await hub.start()
        before = json.dumps([entry.data, entry.options])
        with patch.object(fingerprint.asyncio, 'open_connection', AsyncMock(return_value=(asyncio.StreamReader(), writer))) as connect:
            result = await hub.check_certificate()
        self.assertEqual(connect.await_count, 1)
        self.assertEqual(connect.await_args.args[1], 443)
        self.assertEqual(writer.sent, [])
        self.assertTrue(writer.closed)
        self.assertEqual(result['certificate_serial_status'], 'non_positive')
        self.assertEqual(result['tls_certificate_cn'], 'eziotest')
        self.assertFalse(result['certificate_trust_authenticated'])
        self.assertEqual(before, json.dumps([entry.data, entry.options]))
        self.assertEqual(entry.unique_id, 'r002-fixed')
        self.assertEqual(hub.probe_request_count, 0)
        self.assertEqual(hub.detection_confidence, 'probable')
        await hub.stop()

    async def test_bad_der_does_not_turn_handshake_into_identification(self):
        writer = Writer()
        with patch.object(fingerprint.asyncio, 'open_connection', AsyncMock(return_value=(asyncio.StreamReader(), writer))):
            result = await fingerprint.fingerprint('192.0.2.1', 2)
        self.assertTrue(result['tls_443_handshake_ok'])
        self.assertEqual(result['certificate_metadata_status'], 'invalid')
        self.assertEqual(result['certificate_parse_error_type'], 'CertificateMetadataError')
        self.assertFalse(result['detected'])
