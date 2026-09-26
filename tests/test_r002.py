"""SYNTHETIC framing/fingerprint fixtures; no hardware capture or device access."""
import asyncio
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

protocol = load('r002.protocol')
transport = load('r002.transport')
fingerprint = load('r002.fingerprint')
hub_module = load('r002.hub')


class Writer:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.transport = SimpleNamespace(abort=Mock())
        self.start_tls = AsyncMock()
    def write(self, data):
        self.sent.append(data)
    async def drain(self):
        pass
    def close(self):
        self.closed = True
    async def wait_closed(self):
        pass
    def get_extra_info(self, key):
        return SimpleNamespace(getpeercert=lambda **kw: b'SYNTHETIC CERTIFICATE')


def packet(kind=14, version=1, flags=0, length=6, padding=0, body=None):
    return struct.pack('<HHHHI', kind, version, flags, length, padding) + (
        bytes(length) if body is None else body)


class ProtocolTests(unittest.TestCase):
    def test_exact_requests(self):
        for kind in (14, 15, 26, 28):
            self.assertEqual(protocol.request(kind), bytes([kind, 0, 0, 0, 0, 0, 0, 0]))

    def test_no_arbitrary_type_or_payload(self):
        for kind in (0, 1, 29, 505, -1, 65536, True, 14.0, '14'):
            with self.assertRaises(ValueError):
                protocol.request(kind)

    def test_header_little_endian(self):
        result = protocol.parse_header(packet(length=258)[:12], 14)
        self.assertEqual(result.declared_length, 258)

    def test_short_header(self):
        for size in range(12):
            with self.assertRaises(protocol.R002ProtocolError):
                protocol.parse_header(bytes(size), 14)

    def test_unrecognized_header_fields(self):
        for fields, exception in (({'kind': 15}, protocol.UnexpectedType),
            ({'version': 2}, protocol.UnexpectedVersion),
            ({'flags': 1}, protocol.R002ProtocolError),
            ({'padding': 1}, protocol.R002ProtocolError),
            ({'length': 65535}, protocol.OversizedFrame)):
            with self.subTest(fields=fields), self.assertRaises(exception):
                protocol.parse_header(packet(**fields)[:12], 14)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def response(self, data, kind=14, fragment=False):
        reader, writer = asyncio.StreamReader(), Writer()
        if fragment:
            async def produce():
                for byte in data:
                    reader.feed_data(bytes([byte]))
                    await asyncio.sleep(0)
                reader.feed_eof()
            producer = asyncio.create_task(produce())
        else:
            reader.feed_data(data)
            reader.feed_eof()
            producer = None
        counters = transport.new_counters()
        with patch.object(transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connect:
            result = await transport.probe_one('192.0.2.1', kind, counters)
        if producer:
            await producer
        self.assertTrue(writer.closed)
        self.assertEqual(connect.await_count, 1)
        self.assertEqual(writer.sent, [protocol.request(kind)])
        self.assertEqual(counters['connects'], counters['disconnects'])
        return result, counters

    async def test_fragmented_response(self):
        result, counters = await self.response(packet(body=b'secret'), fragment=True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['received_length'], 6)
        self.assertEqual(counters['bytes_rx'], 18)
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('payload', result)

    async def test_empty_response_type_28(self):
        result, _ = await self.response(packet(kind=28, length=0), 28)
        self.assertEqual(result['status'], 'ok')

    async def test_eof_header_and_body(self):
        for data in (b'', packet()[:7], packet()[:15]):
            result, counters = await self.response(data)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(counters['remote_eof'], 1)
            self.assertEqual(counters['bytes_rx'], len(data))

    async def test_malformed_headers_counted(self):
        for fields, counter in (({'kind': 15}, 'unexpected_types'),
            ({'version': 9}, 'unexpected_versions'), ({'flags': 1}, 'malformed_headers'),
            ({'length': 65535}, 'oversized_frames')):
            result, counters = await self.response(packet(**fields)[:12])
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(counters[counter], 1)
            self.assertEqual(counters['bytes_rx'], 12)

    async def test_deadline_and_no_retry(self):
        reader, writer = asyncio.StreamReader(), Writer()
        with patch.object(transport, 'REQUEST_TIMEOUT', .01), patch.object(
            transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connect:
            counters = transport.new_counters()
            result = await transport.probe_one('192.0.2.1', 14, counters)
        self.assertEqual(result['last_error_type'], 'TimeoutError')
        self.assertEqual(counters['timeouts'], 1)
        self.assertTrue(writer.closed)
        self.assertEqual(connect.await_count, 1)

    async def test_cancellation_closes_original_socket(self):
        reader, writer = asyncio.StreamReader(), Writer()
        with patch.object(transport.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))):
            task = asyncio.create_task(transport.probe_one('192.0.2.1', 14, transport.new_counters()))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(writer.closed)
        self.assertEqual(len(writer.sent), 1)

    async def test_invalid_request_never_opens_socket(self):
        with patch.object(transport.asyncio, 'open_connection', AsyncMock()) as connect:
            with self.assertRaises(ValueError):
                await transport.probe_one('192.0.2.1', 505, transport.new_counters())
            connect.assert_not_called()

    async def test_logs_do_not_contain_response_or_address(self):
        with self.assertLogs(transport._LOGGER, 'DEBUG') as logs:
            await self.response(packet(body=b'secret'))
        output = '\n'.join(logs.output)
        self.assertNotIn('192.0.2.1', output)
        self.assertNotIn('secret', output)
        self.assertIn('response_header', output)


class FingerprintTests(unittest.IsolatedAsyncioTestCase):
    async def observe(self, cn='eziotest', fail=None):
        writers = []
        async def connect(host, port, **kwargs):
            if fail:
                raise fail
            writer = Writer()
            writers.append(writer)
            return asyncio.StreamReader(), writer
        with patch.object(fingerprint.asyncio, 'open_connection', side_effect=connect) as connector, patch.object(
            fingerprint, '_certificate_signature', return_value=(cn, cn)), patch.object(
            fingerprint, '_tls_context', return_value=object()):
            result = await fingerprint.fingerprint('192.0.2.1', 2)
        self.assertEqual([call.args[1] for call in connector.call_args_list], [8765, 443])
        for writer in writers:
            self.assertEqual(writer.sent, [])
            self.assertTrue(writer.closed)
        return result

    async def test_combined_signature_is_probable_only(self):
        result = await self.observe()
        self.assertTrue(result['detected'])
        self.assertEqual(result['detection_confidence'], 'probable')
        self.assertIsNone(result['tcp_6987_reachable'])

    async def test_open_8765_alone_is_not_identification(self):
        self.assertFalse((await self.observe(cn='other'))['detected'])

    async def test_offline_and_timeout(self):
        for error in (ConnectionRefusedError('sensitive endpoint'), TimeoutError()):
            result = await self.observe(fail=error)
            self.assertFalse(result['detected'])
            self.assertNotIn('sensitive', json.dumps(result))


class InvestigationTests(unittest.IsolatedAsyncioTestCase):
    def hub(self):
        return hub_module.R002InvestigationHub(None, SimpleNamespace(data={
            'host': '192.0.2.1', 'password': 'credential_fixture', 'uid': 'uid_fixture',
            'fingerprint': {'detected': True, 'tls_certificate_cn': 'secret-certificate',
                            'raw': 'secret-packet'},
        }))

    async def test_startup_has_no_sessions_or_network(self):
        hub = self.hub()
        with patch.object(asyncio, 'open_connection', AsyncMock(side_effect=AssertionError('network'))):
            await hub.start()
            self.assertFalse(hub.stopped)
            for name in ('session', 'server', 'control', 'ring_listener', 'talkback', 'ring_image'):
                self.assertFalse(hasattr(hub, name))
            await hub.stop()

    async def test_explicit_allowlist_no_retry(self):
        hub = self.hub()
        await hub.start()
        with patch.object(hub_module, 'probe_one', AsyncMock(return_value={
            'status': 'failed', 'last_error_type': 'TimeoutError', 'elapsed_ms': 1})) as probe:
            result = await hub.probe()
        self.assertEqual([call.args[1] for call in probe.call_args_list], [14, 15, 26, 28])
        self.assertEqual(hub.probe_request_count, 4)
        self.assertEqual(hub.status, 'probe_failed')
        self.assertNotIn('host', result)
        await hub.stop()

    async def test_unsupported_and_duplicate_types_refused(self):
        hub = self.hub()
        await hub.start()
        for kinds in ([], [505], [14, 14], [14.0], [True], [14, 15, 26, 28, 14]):
            with self.assertRaises(ValueError):
                await hub.probe(kinds)
        self.assertEqual(hub.probe_request_count, 0)
        await hub.stop()

    async def test_unload_cancels_probe_and_prevents_late_callbacks(self):
        hub = self.hub()
        await hub.start()
        entered = asyncio.Event()
        async def probe(*args):
            entered.set()
            await asyncio.Event().wait()
        callback = Mock()
        hub.subscribe(callback)
        with patch.object(hub_module, 'probe_one', side_effect=probe):
            task = asyncio.create_task(hub.probe([14]))
            await entered.wait()
            with self.assertRaises(RuntimeError):
                await hub.probe([15])
            await hub.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(callback.call_count, 1)
        self.assertIsNone(hub._task)
        with self.assertRaises(RuntimeError):
            await hub.probe([14])

    def test_diagnostics_privacy_allowlist(self):
        text = json.dumps(self.hub().diagnostics())
        for secret in ('192.0.2.1', 'credential_fixture', 'uid_fixture', 'secret-certificate', 'secret-packet'):
            self.assertNotIn(secret, text)
        for field in ('host', 'username', 'password', 'uid', 'mac', 'payload', 'token'):
            self.assertNotIn(f'"{field}"', text)
