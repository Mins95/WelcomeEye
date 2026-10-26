"""Compare QV families through a real loopback HTTPS CGI and synthetic media.

Only 127.0.0.1 is configured. The approved media connector is replaced by the
in-memory QV peer; discovery and other media transports are forbidden. No
intercom, cloud account or physical output is involved.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import ssl
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from load_integration import cap, load

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from qv_runtime_support import PASSWORD, STREAM_KEY, SyntheticQVPeer

c3 = load('connect3.hub')
r002 = load('r002.hub')
live = load('connect3.live')
session = load('connect3.session')


class QVAuthenticationParityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.requests = []
        self.handlers = set()
        self.device_error = 0
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-qv-cgi')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
        self.pin = sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
        cert_path, key_path = (Path(self.directory.name) / name for name in ('cert.pem', 'key.pem'))
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        self.server = await asyncio.start_server(self.handle_request, '127.0.0.1', 0, ssl=context)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        if self.handlers:
            await asyncio.wait_for(asyncio.gather(*tuple(self.handlers)), 2)
        self.directory.cleanup()

    async def handle_request(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            async with asyncio.timeout(2):
                header = await reader.readuntil(b'\r\n\r\n')
                lines = header.split(b'\r\n')
                fields = {name.lower(): value.strip() for name, value in (
                    line.split(b':', 1) for line in lines[1:] if line)}
                length = int(fields[b'content-length'])
                self.assertLessEqual(length, 8192)
                body = await reader.readexactly(length)
                self.requests.append((lines[0], fields, body))
                content = f'<key>{STREAM_KEY}</key>' if not self.device_error else ''
                response = (f'<envelope><body><error>{self.device_error}</error>'
                    f'<content>{content}</content></body></envelope>').encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/xml\r\n'
                    + f'Content-Length: {len(response)}\r\n'.encode()
                    + b'Connection: close\r\n\r\n' + response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            # A rejected leaf pin closes TLS before any HTTP request is sent.
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.handlers.discard(task)

    def hub(self, family, *, pin=None):
        data = {
            'host': '127.0.0.1', 'cgi_port': self.port, 'auth_code': PASSWORD,
            'credential_source': 'manual', 'certificate_sha256': pin or self.pin,
            'media_transport': 'connect3_tcp', 'media_tcp_approved': True,
            'experimental_video': True, 'experimental_tcp_controls': True,
            'trust_endpoint': {'host': '127.0.0.1', 'cgi_port': self.port,
                'media_port': 34567, 'media_transport': 'connect3_tcp'},
            'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'},
        }
        if family == 'r002':
            data.update(protocol_family=cap.ProtocolFamily.R002, device_variant=cap.DeviceVariant.R002,
                r002_connection_mode='manual')
            return r002.R002InvestigationHub(None, SimpleNamespace(data=data))
        data.update(protocol_family=cap.ProtocolFamily.CONNECT3, device_variant=cap.DeviceVariant.CONNECT3)
        return c3.Connect3Hub(None, SimpleNamespace(data=data))

    def assert_request(self, request):
        first_line, fields, body = request
        self.assertEqual(first_line, b'POST /tdkcgi HTTP/1.1')
        self.assertEqual(fields[b'host'], f'127.0.0.1:{self.port}'.encode())
        xml = ET.fromstring(body)
        self.assertEqual({node.tag: node.text for node in xml.find('header')}, {
            'security': 'username', 'username': 'adminapp2',
            'password': sha256(PASSWORD.encode()).hexdigest(), 'passwordencode': '1'})
        self.assertEqual(xml.findtext('body/command'), 'get.device.streamkey')
        self.assertEqual(list(xml.find('body/content')), [])
        self.assertNotIn(PASSWORD.encode(), body)

    async def exercise(self, family, *, accepted=True, pin=None):
        hub = self.hub(family, pin=pin)
        peer = SyntheticQVPeer()
        received = {'video': 0, 'audio': 0}

        def frame(kind, _frame):
            received[kind] += 1

        async def media(host, port, *args, **kwargs):
            self.assertEqual((host, port), ('127.0.0.1', 34567))
            self.assertTrue(hub.live.observation['cgi_https_verified'])
            self.assertTrue(hub.live.observation['streamkey_received'])
            return peer.reader, peer

        hub.frame_listeners.add(frame)
        forbidden = AsyncMock(side_effect=AssertionError('Unexpected discovery or alternate transport'))
        with patch.object(r002, 'resolve_endpoint', forbidden), \
                patch.object(live, 'discover', forbidden), \
                patch.object(session, 'open_r002_media_tcp', forbidden), \
                patch.object(session, 'open_media_tls', forbidden), \
                patch.object(session, 'open_connect3_media_tcp', AsyncMock(side_effect=media)) as connect:
            await hub.start()
            try:
                if accepted:
                    await hub.acquire('viewer')
                    async with asyncio.timeout(3):
                        while not all(received.values()):
                            await asyncio.sleep(.01)
                    self.assertEqual(hub._authentication['status'], 'accepted')
                    self.assertTrue(hub.live.observation['setup_accepted'])
                    self.assertTrue(hub.live.observation['play_accepted'])
                    self.assertEqual(hub.live.observation['decode_errors'], 0)
                    self.assertEqual(hub.live.observation['audio']['decode_errors'], 0)
                    await hub.release('viewer')
                    connect.assert_awaited_once()
                    self.assertEqual(peer.teardowns, 1)
                    self.assertEqual(peer.close_count, 1)
                    self.assertEqual(peer.outputs, [])
                    self.assertIsNone(hub.live.task)
                else:
                    with self.assertRaises(RuntimeError):
                        await hub.acquire('viewer')
                    connect.assert_not_awaited()
                    self.assertFalse(hub.live.observation['streamkey_received'])
                    self.assertEqual(hub.live.observation['decoded_frames'], 0)
                    self.assertEqual(peer.commands, [])
                    if pin is None:
                        self.assertEqual(hub.live.observation['last_error_reason'], 'auth_code_rejected')
                        self.assertEqual(hub._authentication['status'], 'rejected')
                        self.assertTrue(hub.live.observation['cgi_https_verified'])
                forbidden.assert_not_awaited()
            finally:
                await hub.stop()

    async def test_same_credentials_produce_identical_cgi_and_play_in_both_families(self):
        for family in ('connect3', 'r002'):
            with self.subTest(family=family):
                await self.exercise(family)
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assert_request(request)
        self.assertEqual(self.requests[0][2], self.requests[1][2])

    async def test_device_401_has_same_single_request_and_never_opens_media(self):
        self.device_error = 401
        for family in ('connect3', 'r002'):
            with self.subTest(family=family):
                await self.exercise(family, accepted=False)
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assert_request(request)
        self.assertEqual(self.requests[0][2], self.requests[1][2])

    async def test_changed_certificate_prevents_http_credentials_for_both_families(self):
        for family in ('connect3', 'r002'):
            with self.subTest(family=family):
                await self.exercise(family, accepted=False, pin='0' * 64)
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
