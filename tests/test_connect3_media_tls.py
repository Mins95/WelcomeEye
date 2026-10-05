"""Actual TLS loopback with ephemeral synthetic certificate, no device I/O."""
import asyncio
import ssl
import unittest
from unittest.mock import patch

from load_integration import load
import test_connect3_https as https_fixture

tls = load('connect3.tls')


class MediaTLSTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        https_fixture.HTTPSTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.received = []
        self.handlers = set()
        async def serve(reader, writer):
            self.handlers.add(asyncio.current_task())
            try:
                value = await reader.read(32)
                if value:
                    self.received.append(value)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
                self.handlers.discard(asyncio.current_task())
        self.server = await asyncio.start_server(serve, '127.0.0.1', 0, ssl=self.context)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        for task in tuple(self.handlers):
            task.cancel()
        await asyncio.gather(*tuple(self.handlers), return_exceptions=True)

    async def test_pin_verified_before_application_write(self):
        observation = {}
        _, writer = await tls.open_media_tls('127.0.0.1', self.port, self.fingerprint, observation)
        self.assertTrue(observation['media_tls_verified'])
        writer.write(b'SYNTHETIC_APPLICATION_MESSAGE')
        await writer.drain()
        await tls.close_writer(writer)
        await asyncio.gather(*tuple(self.handlers))
        self.assertEqual(self.received, [b'SYNTHETIC_APPLICATION_MESSAGE'])
        self.assertNotIn(self.fingerprint, repr(observation))
        self.assertNotIn('127.0.0.1', repr(observation))

    async def test_different_media_certificate_sends_no_application_bytes(self):
        observation = {}
        with self.assertRaisesRegex(tls.MediaTLSFailure, '^media_certificate_pin_mismatch$'):
            await tls.open_media_tls('127.0.0.1', self.port, '0'*64, observation)
        await asyncio.gather(*tuple(self.handlers))
        self.assertFalse(observation['media_tls_verified'])
        self.assertEqual(self.received, [])

    async def test_no_pin_preserves_system_trust(self):
        observation = {}
        with self.assertRaises(ssl.SSLCertVerificationError):
            await tls.open_media_tls('127.0.0.1', self.port, '', observation)
        self.assertFalse(observation['media_tls_verified'])
        self.assertEqual(self.received, [])

    async def test_invalid_endpoint_rejected_before_connection(self):
        with patch.object(tls.asyncio, 'open_connection') as connect:
            for host, port, pin in [('0.0.0.0', 8443, ''), ('224.0.0.1', 8443, ''),
                                    ('192.0.2.1', 0, ''), ('192.0.2.1', 8443, 'invalid')]:
                with self.assertRaises(tls.MediaTLSFailure):
                    await tls.open_media_tls(host, port, pin, {})
        connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
