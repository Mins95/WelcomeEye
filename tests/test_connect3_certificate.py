"""Synthetic DER and mocked sockets; no device or public endpoint contacted."""
import asyncio
from hashlib import sha256
import json
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_r002_certificate import synthetic_certificate

module = load('connect3.certificate')
hub_module = load('connect3.hub')


def writer(der=None):
    peer = Mock()
    peer.getpeercert.return_value = synthetic_certificate() if der is None else der
    return SimpleNamespace(start_tls=AsyncMock(), get_extra_info=Mock(return_value=peer),
        close=Mock(), wait_closed=AsyncMock(), transport=SimpleNamespace(abort=Mock()), write=Mock())


class CertificateTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_only_opt_in_fingerprint_never_trust_or_pin(self):
        for detailed in (False, True):
            stream = writer(synthetic_certificate(b'\x00'))
            with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))) as connect:
                result = await module.inspect_certificate('192.0.2.1', include_details=detailed)
            connect.assert_awaited_once_with('192.0.2.1', 443, family=module.socket.AF_INET)
            stream.start_tls.assert_awaited_once()
            stream.write.assert_not_called()
            stream.close.assert_called_once()
            stream.wait_closed.assert_awaited_once()
            self.assertEqual(result['status'], 'observed')
            self.assertEqual(result['certificate_serial_status'], 'non_positive')
            self.assertFalse(result['certificate_trust_authenticated'])
            self.assertFalse(result['certificate_pin_saved'])
            self.assertEqual('certificate_sha256' in result, detailed)
            if detailed:
                self.assertEqual(result['certificate_sha256'], sha256(synthetic_certificate(b'\x00')).hexdigest())
            self.assertNotIn('192.0.2.1', json.dumps(result))
            self.assertNotIn('SYNTHETIC_SIGNATURE', json.dumps(result))

    async def test_failure_stages_bounded_and_sanitized(self):
        stream = writer()
        with patch.object(module.asyncio, 'open_connection', AsyncMock(side_effect=OSError('PRIVATE_ADDRESS'))):
            result = await module.inspect_certificate('192.0.2.1')
        self.assertEqual(result['last_stage'], 'tcp_connect')
        self.assertEqual(result['last_error_type'], 'NetworkError')
        stream.start_tls.side_effect = ssl.SSLError('PRIVATE_CERTIFICATE')
        with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))):
            result = await module.inspect_certificate('192.0.2.1')
        self.assertEqual(result['last_stage'], 'tls_handshake')
        self.assertEqual(result['last_error_type'], 'TLSHandshakeError')
        self.assertNotIn('PRIVATE', json.dumps(result))
        stream.close.assert_called_once()
        for der in (b'bad', b'x' * 65537):
            stream = writer(der)
            with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))):
                result = await module.inspect_certificate('192.0.2.1', include_details=True)
            self.assertEqual(result['last_error_type'], 'CertificateMetadataError')
            self.assertTrue(result['tls_handshake_ok'])
            self.assertNotIn('certificate_sha256', result)

    async def test_timeout_cancellation_and_close_timeout(self):
        entered = asyncio.Event()
        async def block(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        for cancel in (False, True):
            stream = writer()
            stream.start_tls.side_effect = block
            entered.clear()
            with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))), patch.object(module, 'TIMEOUT', .05):
                task = asyncio.create_task(module.inspect_certificate('192.0.2.1'))
                await entered.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertEqual((await task)['last_error_type'], 'TimeoutError')
            stream.close.assert_called_once()
            stream.wait_closed.assert_awaited_once()
        stream = writer()
        stream.wait_closed.side_effect = TimeoutError
        with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))):
            await module.inspect_certificate('192.0.2.1')
        stream.transport.abort.assert_called_once()

    async def test_invalid_address_or_port_no_socket(self):
        for host, port in (('invalid', 443), ('0.0.0.0', 443), ('192.0.2.1', 0), ('192.0.2.1', True)):
            with patch.object(module.asyncio, 'open_connection', AsyncMock()) as connect:
                with self.assertRaises(ValueError):
                    await module.inspect_certificate(host, port=port)
            connect.assert_not_called()

    async def test_certificate_detail_not_in_hub_diagnostics_or_entry(self):
        entry = SimpleNamespace(data={'host': '192.0.2.1'}, options={})
        hub = hub_module.Connect3Hub(None, entry)
        await hub.start()
        before = dict(entry.data)
        stream = writer()
        with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))):
            result = await hub.execute('certificate', include_details=True)
        self.assertIn('certificate_sha256', result)
        self.assertNotIn(result['certificate_sha256'], json.dumps(hub.diagnostics()))
        self.assertNotIn('certificate_sha256', hub._summary)
        self.assertEqual(entry.data, before)
        self.assertEqual(entry.options, {})
        await hub.stop()
