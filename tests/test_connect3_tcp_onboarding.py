"""Synthetic onboarding through real local HTTPS and QV TCP video sockets.

The certificate is generated for this test, including its zero serial and an
opaque noncritical extension that beta.2 misclassified as malformed. It is
not the certificate from Yohan's hardware. Only the socket destination for the
fixed media port is redirected to an ephemeral loopback port; the production
trust, flow, CGI, session, packet and video-decoder paths all run unchanged.
"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from load_integration import load
from test_connect3_config import flow
import test_connect3_end_to_end as media_fixture
import test_connect3_https as https_fixture
from test_connect3_media_protocol import KEY
from test_connect3_trust import generate, legacy_der
from test_connect3_video import synthetic_video_packets

trust = load('connect3.trust')
hubs = load('connect3.hub')
live = load('connect3.live')
session = load('connect3.session')
tls = load('connect3.tls')


class TCPOnboardingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory(prefix='welcomeeye-tcp-onboarding-')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # An unusable noncritical CertificatePolicies value is intentionally
        # opaque. The local TLS peer/key remain valid; this extension is not
        # part of the owner's exact-certificate TOFU trust decision. Beta.2
        # recursively parsed it as DER and failed before approval.
        cert = generate(key, cn='eziotest', address=None,
            unknown_oid='2.5.29.32', unknown_extension=b'SYNTHETIC_OPAQUE_VALUE')
        cls.der = legacy_der(cert, key, b'\x00')
        directory = Path(cls.directory.name)
        cert_path, key_path = directory / 'certificate.pem', directory / 'key.pem'
        cert_path.write_text(ssl.DER_cert_to_PEM_cert(cls.der), encoding='ascii')
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cls.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.context.load_cert_chain(str(cert_path), str(key_path))
        cls.fingerprint = sha256(cls.der).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.hub = None
        self.http = https_fixture.HTTPSTests()
        self.http.context, self.http.fingerprint = self.context, self.fingerprint
        await self.http.asyncSetUp()
        self.http.response_body = (f'<envelope><body><error>0</error><content>'
            f'<key>{KEY}</key></content></body></envelope>').encode()
        self.handlers, self.writers, self.failures = set(), set(), []
        self.connections = []
        self.overlapping_extension, self.with_audio = True, False
        self.packets = synthetic_video_packets(count=2)
        self.media_server = await asyncio.start_server(self.serve_media, '127.0.0.1', 0)
        media_port = self.media_server.sockets[0].getsockname()[1]
        self.dialed = []
        real_open = asyncio.open_connection

        async def loopback_only(host, port, **kwargs):
            self.assertEqual(host, '127.0.0.1')
            self.assertIn(port, (self.http.port, 34567))
            self.dialed.append(port)
            if port == 34567:
                self.assertNotIn('ssl', kwargs)
                port = media_port
            return await real_open(host, port, **kwargs)

        self.socket_patch = patch.object(tls.asyncio, 'open_connection', side_effect=loopback_only)
        self.socket_patch.start()
        # Expected TLS-only CA rejection is part of the trust-first flow.
        loop = asyncio.get_running_loop()
        self.previous_handler = loop.get_exception_handler()
        def expected_handshake_errors(loop, context):
            if isinstance(context.get('exception'), (ssl.SSLError, ConnectionResetError)):
                return
            loop.default_exception_handler(context)
        loop.set_exception_handler(expected_handshake_errors)

    async def asyncTearDown(self):
        if self.hub is not None:
            await self.hub.stop()
            self.assertFalse(self.hub.consumers)
            self.assertIsNone(self.hub.live.task)
            self.assertIsNone(self.hub.live.session)
        self.media_server.close()
        await self.media_server.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        tasks = tuple(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertFalse(self.handlers)
        self.assertFalse(self.writers)
        self.assertEqual(self.failures, [])
        await self.http.asyncTearDown()
        self.socket_patch.stop()
        asyncio.get_running_loop().set_exception_handler(self.previous_handler)

    # Independent wire expectations: literal A9, decoded PLAY fields/digest,
    # synthetic H264, encrypted keepalive/teardown, and exact close ownership.
    serve_media = media_fixture.EndToEndTests.serve_media

    async def pending_flow(self):
        instance = flow()
        instance.test_module.inspect_trust = trust.inspect_trust
        result = await instance.async_step_connect3({
            'host': '127.0.0.1', 'auth_code': https_fixture.AUTH_CODE,
            'experimental_video': True, 'media_transport': 'connect3_tcp',
            'advanced': {'cgi_port': self.http.port}})
        self.assertEqual(result['step_id'], 'connect3_tcp_tls_confirm', result)
        self.assertEqual(instance._connect3_inspection.cgi.serial_status, 'non_positive')
        self.assertEqual(instance._connect3_inspection.cgi.fingerprint, self.fingerprint)
        self.assertEqual(instance._connect3_inspection.media.status, 'not_applicable')
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])
        self.assertNotIn(34567, self.dialed)
        self.assertNotIn(https_fixture.AUTH_CODE, repr(result))
        return instance

    async def approved_data(self):
        instance = await self.pending_flow()
        result = await instance.async_step_connect3_tcp_tls_confirm({'trust': True})
        self.assertEqual(result['type'], 'create_entry', result)
        data = result['data']
        self.assertEqual(data['certificate_sha256'], self.fingerprint)
        self.assertEqual(data['media_transport'], 'connect3_tcp')
        self.assertIs(data['media_tcp_approved'], True)
        self.assertTrue(trust.trust_endpoint_matches(data))
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])
        self.assertNotIn(34567, self.dialed)
        return data

    async def test_legacy_tofu_then_real_https_qv_tcp_video_three_cycles(self):
        data = await self.approved_data()
        # Serializing/reloading private entry data must retain trust, without
        # retaining the short-lived flow object or manually entering any pin.
        data = json.loads(json.dumps(data))
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=data))
        await self.hub.start()
        decoded, complete = [], asyncio.Event()
        def on_frame(kind, frame):
            decoded.append((kind, frame.width, frame.height, frame.pts))
            if len(decoded) % 2 == 0:
                complete.set()
        self.hub.frame_listeners.add(on_frame)
        with patch.object(live, 'discover', side_effect=AssertionError('No discovery allowed')), \
                patch.object(session, 'open_media_tls', side_effect=AssertionError('No TLS media fallback')):
            async with asyncio.timeout(15):
                for cycle in range(3):
                    complete.clear()
                    await asyncio.gather(self.hub.acquire('one'), self.hub.acquire('two'))
                    await complete.wait()
                    self.assertEqual(len(self.connections), cycle + 1)
                    self.assertTrue(self.hub.image.startswith(b'\xff\xd8'))
                    diag = self.hub.diagnostics()
                    media = diag['media']
                    self.assertTrue(media['cgi_https_verified'])
                    self.assertEqual(media['cgi_tls_policy'], 'certificate_pin')
                    self.assertEqual(media['media_transport_selected'], 'connect3_tcp')
                    self.assertTrue(media['media_setup_accepted'])
                    self.assertTrue(media['media_play_accepted'])
                    self.assertEqual(media['credential_protection'], 'qv_aes256_sha256')
                    self.assertFalse(media['media_tls_verified'])
                    self.assertEqual(media['decoded_frames'], 2)
                    self.assertEqual(media['decode_errors'], 0)
                    for private in (KEY, self.fingerprint, https_fixture.AUTH_CODE, '127.0.0.1'):
                        self.assertNotIn(private, json.dumps(diag))
                    await self.hub.release('one')
                    self.assertTrue(self.hub.connected)
                    await self.hub.release('two')
                    await self.connections[-1]['closed'].wait()
                    self.assertEqual(self.connections[-1]['commands'], [0xA9, 1, 7])
                    self.assertFalse(self.hub.consumers)
                    self.assertIsNone(self.hub.live.task)
        self.assertEqual(self.dialed.count(34567), 3)
        self.assertEqual(len(self.http.requests), 3)
        self.assertEqual(decoded, [('video', 64, 48, pts) for _ in range(3) for pts in (0, 4500)])
        for method, target, _, _, body in self.http.requests:
            self.assertEqual((method, target), ('POST', '/tdkcgi'))
            self.assertIn(b'get.device.streamkey', body)
            self.assertNotIn(https_fixture.AUTH_CODE.encode(), body)

    async def test_decline_sends_no_http_credentials_or_media_setup(self):
        instance = await self.pending_flow()
        rejected = await instance.async_step_connect3_tcp_tls_confirm({'trust': False})
        self.assertEqual(rejected['reason'], 'connect3_tls_declined')
        self.assertIsNone(instance._connect3_pending)
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])
        self.assertNotIn(34567, self.dialed)

    async def test_changed_pin_blocks_real_https_before_credentials_and_media(self):
        data = await self.approved_data()
        # A pin different from the live peer represents a changed certificate.
        data['certificate_sha256'] = '0' * 64
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=data))
        await self.hub.start()
        with self.assertRaisesRegex(RuntimeError, 'Connect 3 media failed'):
            await self.hub.acquire('viewer')
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])
        self.assertNotIn(34567, self.dialed)
        self.assertFalse(self.hub.consumers)
        self.assertEqual(self.hub.live.observation['last_error_type'], aiohttp.ServerFingerprintMismatch.__name__)


if __name__ == '__main__':
    unittest.main()
