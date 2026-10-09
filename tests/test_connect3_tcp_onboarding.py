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
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from load_integration import load
from test_connect3_config import flow
import test_connect3_end_to_end as media_fixture
import test_connect3_https as https_fixture
from test_connect3_media_protocol import KEY
from test_connect3_trust import generate
from test_connect3_video import synthetic_video_packets

trust = load('connect3.trust')
hubs = load('connect3.hub')
live = load('connect3.live')
session = load('connect3.session')
tls = load('connect3.tls')
OBSERVED_DATE = '1969-12-31T16:00:27+00:00'


def signed_fixture_der(cert, key, *, zero_duration=False):
    """Re-sign our synthetic leaf with a zero serial and optional observed dates."""
    root = trust.der_reader._tree(cert.public_bytes(serialization.Encoding.DER))
    fields = list(root.children[0].children)
    position = 1 if fields[0].tag == 0xa0 else 0
    encoded = lambda tag, value: trust._encoded(SimpleNamespace(tag=tag, value=value))
    values = [trust._encoded(node) for node in fields]
    values[position] = encoded(2, b'\x00')
    if zero_duration:
        moment = encoded(23, b'691231160027Z')
        values[position + 3] = encoded(0x30, moment + moment)
    tbs = encoded(0x30, b''.join(values))
    signature = key.sign(tbs, padding.PKCS1v15(), hashes.SHA256())
    key.public_key().verify(signature, tbs, padding.PKCS1v15(), hashes.SHA256())
    return encoded(0x30, tbs + trust._encoded(root.children[1]) + encoded(3, b'\x00' + signature))


class TCPOnboardingTests(unittest.IsolatedAsyncioTestCase):
    zero_duration = False
    legacy_key = False
    key_bits = 2048

    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory(prefix='welcomeeye-tcp-onboarding-')
        # An unusable noncritical CertificatePolicies value is intentionally
        # opaque. The local TLS peer/key remain valid; this extension is not
        # part of the owner's exact-certificate TOFU trust decision. Beta.2
        # recursively parsed it as DER and failed before approval.
        directory = Path(cls.directory.name)
        for name in ('original', 'changed'):
            key = rsa.generate_private_key(public_exponent=65537, key_size=cls.key_bits)
            cert = generate(key, cn='eziotest', address=None,
                unknown_oid='2.5.29.32', unknown_extension=b'SYNTHETIC_OPAQUE_VALUE')
            der = signed_fixture_der(cert, key, zero_duration=cls.zero_duration)
            cert_path, key_path = directory / f'{name}.pem', directory / f'{name}-key.pem'
            cert_path.write_text(ssl.DER_cert_to_PEM_cert(der), encoding='ascii')
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            if cls.legacy_key:
                # Only our server must load its deliberately weak private key.
                # Production inspection/Fingerprint client contexts are intact.
                context.set_ciphers('DEFAULT:@SECLEVEL=1')
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.maximum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(cert_path), str(key_path))
            if name == 'original':
                cls.der, cls.context, cls.fingerprint = der, context, sha256(der).hexdigest()
            else:
                cls.changed_context = context

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.hub = None
        self.client_security_level = ssl.create_default_context().security_level
        self.assertEqual(trust._inspection_context().security_level, self.client_security_level)
        self.assertEqual(aiohttp.connector._SSL_CONTEXT_UNVERIFIED.security_level, self.client_security_level)
        self.http = https_fixture.HTTPSTests()
        self.http.context, self.http.fingerprint = self.context, self.fingerprint
        self.https_tls_versions = []
        original_serve = self.http._serve
        async def checked_https(reader, writer):
            peer = writer.get_extra_info('ssl_object')
            version = peer.version() if peer is not None else None
            self.https_tls_versions.append(version)
            if self.legacy_key:
                try:
                    self.assertEqual(version, 'TLSv1.2')
                except AssertionError:
                    self.http.handler_errors.append('UnexpectedTLSVersion')
                    await trust.close_writer(writer)
                    return
            # Version is checked before the first HTTP header/body read.
            await original_serve(reader, writer)
        self.http._serve = checked_https
        await self.http.asyncSetUp()
        self.http.response_body = (f'<envelope><body><error>0</error><content>'
            f'<key>{KEY}</key></content></body></envelope>').encode()
        self.handlers, self.writers, self.failures = set(), set(), []
        self.connections = []
        self.overlapping_extension, self.with_audio = True, True
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
    async def serve_media(self, reader, writer):
        """One live socket: encrypted PLAY, H264 and observed type3/codec4 PCMA."""
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        record = {'commands': [], 'closed': asyncio.Event()}
        self.connections.append(record)
        try:
            async with asyncio.timeout(5):
                self.assertEqual(await reader.readexactly(32), b'\xa9' + bytes(31))
                record['commands'].append(0xA9)
                setup = bytearray(32)
                setup[0], setup[10], setup[11] = 0xA9, 2, 1
                writer.write(setup)
                await writer.drain()
                header = media_fixture.aes(await reader.readexactly(32), decrypt=True)
                self.assertEqual(header[0], 1)
                record['commands'].append(1)
                extension, length = struct.unpack_from('<HH', header, 9)
                body = media_fixture.aes(await reader.readexactly(extension), decrypt=True)
                expected = b'adminapp2&&' + sha256(https_fixture.AUTH_CODE.encode()).hexdigest().encode() + b'\0\0'
                self.assertEqual(header[13:17], b'\1\0\1\1')
                self.assertEqual(length, len(expected))
                self.assertEqual(body[:length], expected)
                self.assertEqual(body[length:length + 32], sha256(header + expected).digest())
                writer.write(b''.join(media_fixture.control_response()))
                for packet in self.packets:
                    frame = bytearray(media_fixture.frame_bytes(payload=bytes(packet),
                        frame_type=1 if packet.is_keyframe else 0))
                    struct.pack_into('<HH', frame, 16, 64, 48)
                    writer.write(b''.join(media_fixture.overlapping_media_response(
                        bytes(frame), encrypted=packet.is_keyframe)))
                # E3/codec4 matches the observed downstream A-law framing.
                # Both 20ms packets arrive on this same established socket.
                for payload in (b'\xd5' * 160, b'\x55' * 160):
                    frame = bytearray(media_fixture.frame_bytes(payload=payload, frame_type=3, codec=4))
                    frame[15] = 1
                    struct.pack_into('<H', frame, 16, 8000)
                    writer.write(b''.join(media_fixture.overlapping_media_response(bytes(frame))))
                await writer.drain()
                while True:
                    header = media_fixture.aes(await reader.readexactly(32), decrypt=True)
                    self.assertEqual(struct.unpack_from('<H', header, 9)[0], 32)
                    body = media_fixture.aes(await reader.readexactly(32), decrypt=True)
                    self.assertEqual(body, sha256(header).digest())
                    record['commands'].append(header[0])
                    self.assertIn(header[0], (0, 7))
                    if header[0] == 7:
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failures.append(type(exc).__name__)
        finally:
            await trust.close_writer(writer)
            self.writers.discard(writer)
            self.handlers.discard(task)
            record['closed'].set()

    async def pending_flow(self, *, controls=False):
        instance = flow()
        instance.test_module.inspect_trust = trust.inspect_trust
        result = await instance.async_step_connect3({
            'host': '127.0.0.1', 'auth_code': https_fixture.AUTH_CODE,
            'experimental_video': True, 'media_transport': 'connect3_tcp',
            'experimental_tcp_controls': controls, 'experimental_outputs': controls,
            **({'opening_code': 'SYNTHETIC_OPENING_CODE'} if controls else {}),
            'advanced': {'cgi_port': self.http.port}})
        self.assertEqual(result['step_id'], 'connect3_tcp_tls_confirm', result)
        self.assertEqual(instance._connect3_inspection.cgi.serial_status, 'non_positive')
        self.assertEqual(instance._connect3_inspection.cgi.fingerprint, self.fingerprint)
        self.assertEqual(instance._connect3_inspection.cgi.key_type, 'rsa')
        self.assertEqual(instance._connect3_inspection.cgi.key_bits, self.key_bits)
        if self.legacy_key:
            self.assertIn('accept_legacy_key', result['data_schema'])
        else:
            self.assertNotIn('accept_legacy_key', result['data_schema'])
        if self.zero_duration:
            endpoint = instance._connect3_inspection.cgi
            self.assertEqual(endpoint.validity_status, 'zero_duration')
            self.assertEqual(endpoint.not_valid_before, OBSERVED_DATE)
            self.assertEqual(endpoint.not_valid_after, OBSERVED_DATE)
            self.assertIn('accept_zero_duration', result['data_schema'])
        self.assertEqual(instance._connect3_inspection.media.status, 'not_applicable')
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])
        self.assertNotIn(34567, self.dialed)
        self.assertNotIn(https_fixture.AUTH_CODE, repr(result))
        return instance

    async def approved_data(self, *, controls=False):
        instance = await self.pending_flow(controls=controls)
        approval = {'trust': True}
        if self.zero_duration:
            approval['accept_zero_duration'] = True
        if self.legacy_key:
            approval['accept_legacy_key'] = True
        result = await instance.async_step_connect3_tcp_tls_confirm(approval)
        self.assertEqual(result['type'], 'create_entry', result)
        data = result['data']
        self.assertEqual(data['certificate_sha256'], self.fingerprint)
        self.assertEqual(data['media_transport'], 'connect3_tcp')
        self.assertIs(data['media_tcp_approved'], True)
        self.assertTrue(trust.trust_endpoint_matches(data))
        if self.zero_duration:
            self.assertEqual(data['tls_certificate_date_exceptions'], {'cgi': {
                'policy': 'zero_duration_v1', 'certificate_sha256': self.fingerprint,
                'not_valid_before': OBSERVED_DATE, 'not_valid_after': OBSERVED_DATE}})
        else:
            self.assertFalse(data.get('tls_certificate_date_exceptions'))
        self.assertNotIn('accept_zero_duration', data)
        self.assertNotIn('accept_legacy_key', data)
        if self.legacy_key:
            self.assertEqual(data['tls_certificate_key_exceptions'], {'cgi': {
                'policy': 'rsa1024_v1', 'certificate_sha256': self.fingerprint,
                'key_type': 'rsa', 'key_bits': 1024}})
        else:
            self.assertFalse(data.get('tls_certificate_key_exceptions'))
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])
        self.assertNotIn(34567, self.dialed)
        return data

    async def test_tcp_controls_require_their_separate_opt_in(self):
        data = await self.approved_data(controls=True)
        self.assertTrue(data['experimental_tcp_controls'])
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=json.loads(json.dumps(data))))
        await self.hub.start()
        self.hub.check_tls_trust()
        self.assertTrue(self.hub.capabilities.downstream_audio and self.hub.capabilities.talkback)
        self.assertTrue(self.hub.capabilities.strike and self.hub.capabilities.gate)
        self.assertEqual(self.connections, [])
        self.assertEqual(self.http.requests, [])

    async def test_legacy_tofu_then_real_https_qv_tcp_video_three_cycles(self):
        data = await self.approved_data()
        # Previously saved output choices cannot bypass the TCP-controls opt-in.
        data.update(experimental_outputs=True, opening_code='SYNTHETIC_OPENING_CODE')
        # Serializing/reloading private entry data must retain trust, without
        # retaining the short-lived flow object or manually entering any pin.
        data = json.loads(json.dumps(data))
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=data))
        await self.hub.start()
        self.hub.check_tls_trust()  # Private JSON reload preserves exact exception binding.
        self.assertTrue(self.hub.capabilities.downstream_audio)
        self.assertFalse(self.hub.capabilities.talkback or self.hub.capabilities.strike or self.hub.capabilities.gate)
        decoded, decoded_audio, complete = [], [], asyncio.Event()
        def on_frame(kind, frame):
            if kind == 'video':
                decoded.append((kind, frame.width, frame.height, frame.pts))
            else:
                self.assertEqual(kind, 'audio')
                decoded_audio.append((frame.format.name, frame.sample_rate, frame.layout.name,
                    frame.samples, frame.pts, bytes(frame.planes[0])[:frame.samples * 2]))
            if decoded and len(decoded) % 2 == 0 and len(decoded_audio) == len(decoded):
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
                    audio = media['audio']
                    self.assertEqual((audio['codec_id'], audio['codec'], audio['sample_rate'], audio['channels']),
                        (4, 'pcm_alaw', 8000, 1))
                    self.assertEqual((audio['input_packets'], audio['input_bytes'], audio['decoded_frames'], audio['decoded_samples']),
                        (2, 320, 2, 320))
                    self.assertEqual((audio['unsupported_packets'], audio['decode_errors']), (0, 0))
                    if cycle == 0:
                        before_dials = list(self.dialed)
                        with self.assertRaises((RuntimeError, ValueError)):
                            await self.hub.talkback.start('unauthorized-tcp-controls')
                        with self.assertRaises((RuntimeError, ValueError)):
                            await self.hub.control.unlock(0)
                        self.assertEqual(self.dialed, before_dials)
                        self.assertEqual(self.connections[-1]['commands'], [0xA9, 1])
                    for private in (KEY, self.fingerprint, https_fixture.AUTH_CODE, '127.0.0.1',
                                    OBSERVED_DATE, 'tls_certificate_date_exceptions',
                                    'tls_certificate_key_exceptions', 'rsa1024_v1'):
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
        self.assertEqual(decoded_audio, [('s16', 8000, 'mono', 160, pts, sample * 160)
            for _ in range(3) for pts, sample in ((0, b'\x08\x00'), (160, b'\xf8\xff'))])
        for method, target, _, _, body in self.http.requests:
            self.assertEqual((method, target), ('POST', '/tdkcgi'))
            self.assertIn(b'get.device.streamkey', body)
            self.assertNotIn(https_fixture.AUTH_CODE.encode(), body)
        self.assertEqual(trust._inspection_context().security_level, self.client_security_level)
        self.assertEqual(aiohttp.connector._SSL_CONTEXT_UNVERIFIED.security_level, self.client_security_level)
        if self.legacy_key:
            self.assertTrue(self.https_tls_versions)
            self.assertEqual(set(self.https_tls_versions), {'TLSv1.2'})

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
        # Replace the actual peer certificate at the same endpoint. The saved
        # pin/date record still pass local policy, but HTTP authentication must
        # never be sent to this changed peer.
        self.http.server.close()
        await self.http.server.wait_closed()
        self.http.server = await asyncio.start_server(self.http._serve, '127.0.0.1',
            self.http.port, ssl=self.changed_context, ssl_handshake_timeout=1.0)
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=data))
        await self.hub.start()
        with self.assertRaisesRegex(RuntimeError, 'Connect 3 media failed'):
            await self.hub.acquire('viewer')
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])
        self.assertNotIn(34567, self.dialed)
        self.assertFalse(self.hub.consumers)
        self.assertEqual(self.hub.live.observation['last_error_type'], aiohttp.ServerFingerprintMismatch.__name__)


class ZeroDurationTCPOnboardingTests(TCPOnboardingTests):
    """Observed date values, synthetic key/serial, unchanged media security gates."""

    zero_duration = True

    async def test_explicit_date_ack_required_before_reinspection_or_entry(self):
        instance = await self.pending_flow()
        before = list(self.dialed)
        for approval in ({'trust': True}, {'trust': True, 'accept_zero_duration': False}):
            rejected = await instance.async_step_connect3_tcp_tls_confirm(approval)
            self.assertEqual(rejected['errors']['base'], 'connect3_zero_duration_approval_required')
            self.assertEqual(self.dialed, before)
            self.assertFalse(hasattr(instance, 'uid'))
            self.assertEqual(self.http.requests, [])
            self.assertEqual(self.connections, [])
        declined = await instance.async_step_connect3_tcp_tls_confirm({
            'trust': False, 'accept_zero_duration': True})
        self.assertEqual(declined['reason'], 'connect3_tls_declined')
        self.assertIsNone(instance._connect3_pending)
        self.assertEqual(self.dialed, before)

    async def test_matching_saved_exception_does_not_repeat_confirmation(self):
        data = await self.approved_data()
        entry = SimpleNamespace(entry_id='zero-duration-entry', unique_id='retained-identity',
            data=json.loads(json.dumps(data)))
        instance = flow()
        instance.test_module.inspect_trust = trust.inspect_trust
        instance._get_reconfigure_entry = lambda: entry
        original = json.loads(json.dumps(entry.data['tls_certificate_date_exceptions']))
        original_key = json.loads(json.dumps(entry.data.get('tls_certificate_key_exceptions', {})))
        result = await instance.async_step_connect3_reconfigure({'host': '127.0.0.1'})
        self.assertEqual(result['reason'], 'reconfigure_successful', result)
        self.assertEqual(entry.data['tls_certificate_date_exceptions'], original)
        self.assertEqual(entry.data.get('tls_certificate_key_exceptions', {}), original_key)
        self.assertEqual(entry.unique_id, 'retained-identity')
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.connections, [])


class LegacyRSA1024TCPOnboardingTests(ZeroDurationTCPOnboardingTests):
    """Both explicit exceptions, real default clients and unchanged TCP video."""

    legacy_key = True
    key_bits = 1024

    async def test_both_exception_consents_are_independently_required(self):
        instance = await self.pending_flow()
        before = list(self.dialed)
        cases = (
            ({'trust': True, 'accept_zero_duration': True}, 'connect3_legacy_key_approval_required'),
            ({'trust': True, 'accept_zero_duration': True, 'accept_legacy_key': False},
             'connect3_legacy_key_approval_required'),
            ({'trust': True, 'accept_legacy_key': True}, 'connect3_zero_duration_approval_required'),
        )
        for approval, error in cases:
            result = await instance.async_step_connect3_tcp_tls_confirm(approval)
            self.assertEqual(result['errors']['base'], error)
            self.assertEqual(self.dialed, before)
            self.assertFalse(hasattr(instance, 'uid'))
            self.assertEqual(self.http.requests, [])
            self.assertEqual(self.connections, [])
        rejected = await instance.async_step_connect3_tcp_tls_confirm({
            'trust': False, 'accept_zero_duration': True, 'accept_legacy_key': True})
        self.assertEqual(rejected['reason'], 'connect3_tls_declined')
        self.assertIsNone(instance._connect3_pending)
        self.assertEqual(self.dialed, before)


if __name__ == '__main__':
    unittest.main()
