"""Real loopback HTTPS account exchanges, using synthetic identities only.

The server independently enforces the APK's two separate XML headers. Its
native error fixture reproduces the public, unauthenticated header comparison;
no vendor service or real account is contacted by these tests.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from load_integration import load

api = load('qv_cloud_api')
protocol = load('qv_cloud_protocol')
SERVICES = protocol.QvCloudServices(*(f'https://{name}.qvcloud.net/'
    for name in ('auth', 'alarm', 'oauth', 'openapi')))
INSTALLATION = '77777777-1111-2222-3333-444444444444'
EMAIL = 'synthetic@example.invalid'
PASSWORD = 'SYNTHETIC_ACCOUNT_PASSWORD'
FCM_TOKEN = 'SYNTHETIC_FCM_TOKEN'
NATIVE_ERROR = b'CAUTHNETClient::Response(packet): BeginResponseGenerator return failed\r\n'


def response_xml(sequence=1, content=''):
    return (f'<envelope><header><seq>{sequence}</seq><result>0</result>'
            '<session><id>SYNTHETIC_SESSION</id></session></header>'
            f'<content>{content}</content></envelope>').encode()


class LocalSession:
    """Redirect only the test's fixed vendor authorities to its TLS server."""

    def __init__(self, owner, kind, *, trust=True, old_headers=False):
        self.owner, self.kind, self.old_headers = owner, kind, old_headers
        self.context = owner.trusted_context if trust else ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(), trust_env=False)

    @property
    def closed(self):
        return self.session.closed

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        self.owner.assertIn(parsed.hostname, {
            'auth.qvcloud.net', 'alarm.qvcloud.net', 'oauth.qvcloud.net', 'openapi.qvcloud.net'})
        self.owner.assertEqual(parsed.scheme, 'https')
        self.owner.assertIs(kwargs.pop('ssl'), True)
        self.owner.assertIs(kwargs.get('allow_redirects'), False)
        if self.old_headers and method == 'POST' and kwargs.get('data') is not None:
            kwargs['headers'] = {'Content-Type': 'application/xml; charset=utf-8'}
        local_url = f'https://localhost:{self.owner.port}{parsed.path}'
        if parsed.query:
            local_url += '?' + parsed.query
        return self.session.request(method, local_url, ssl=self.context, **kwargs)

    async def close(self):
        await self.session.close()


class QvCloudHTTPTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory(prefix='welcomeeye-qv-account-tls-')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1234)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(key, hashes.SHA256()))
        directory = Path(cls.directory.name)
        cert_path, key_path = directory / 'certificate.pem', directory / 'key.pem'
        cls.certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
        cert_path.write_bytes(cls.certificate_pem)
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cls.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_context.load_cert_chain(cert_path, key_path)
        cls.trusted_context = ssl.create_default_context(cadata=cls.certificate_pem.decode())

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.clients, self.sessions, self.requests, self.server_errors = [], [], [], []
        self.handlers, self.writers = set(), set()
        self.down = asyncio.Queue()
        self.down_count = 0
        self.reply = True
        self.post_received = asyncio.Event()
        self.pending_down = asyncio.Event()
        self.down_disconnected = asyncio.Event()
        self.server = await asyncio.start_server(self.serve, '127.0.0.1', 0,
            ssl=self.server_context, ssl_handshake_timeout=1)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for client in self.clients:
            await client.async_close()
        self.server.close()
        await self.server.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        for handler in tuple(self.handlers):
            handler.cancel()
        await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        self.assertFalse(self.handlers)
        self.assertFalse(self.writers)
        self.assertTrue(all(session.closed for session in self.sessions))
        self.assertEqual(self.server_errors, [])

    def client(self, **kwargs):
        def factory(kind):
            session = LocalSession(self, kind, **kwargs)
            self.sessions.append(session)
            return session
        client = api.QvCloudClient(SERVICES, INSTALLATION, session_factory=factory)
        self.clients.append(client)
        return client

    async def down_body(self, reader):
        self.pending_down.set()
        received = asyncio.create_task(self.down.get())
        disconnected = asyncio.create_task(reader.read(1))
        try:
            done, _ = await asyncio.wait((received, disconnected),
                                        return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                self.down_disconnected.set()
                return None
            return received.result()
        finally:
            for task in (received, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(received, disconnected, return_exceptions=True)

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        try:
            async with asyncio.timeout(5):
                head = await reader.readuntil(b'\r\n\r\n')
                lines = head.decode('ascii').split('\r\n')
                method, target, version = lines[0].split(' ')
                self.assertEqual(version, 'HTTP/1.1')
                headers = {name.lower(): value.strip() for name, value in
                           (line.split(':', 1) for line in lines[1:] if line)}
                length = int(headers.get('content-length', '0'))
                self.assertLessEqual(length, 16384)
                body = await reader.readexactly(length)
                self.requests.append((method, target, headers, body))
                path = urlsplit(target).path
                cookies = ''
                if path == '/auth/user;jus_duplex=down':
                    self.down_count += 1
                    if self.down_count == 1:
                        payload = b''
                        cookies = 'Set-Cookie: jsessionid=SYNTHETIC_AUTH_COOKIE; Path=/auth; Secure; HttpOnly\r\n'
                    else:
                        self.assertEqual(headers.get('cookie'), 'jsessionid=SYNTHETIC_AUTH_COOKIE')
                        payload = await self.down_body(reader)
                        if payload is None:
                            return
                elif path == '/auth/user;jus_duplex=up':
                    self.assertEqual(method, 'POST')
                    self.assertEqual(headers.get('cookie'), 'jsessionid=SYNTHETIC_AUTH_COOKIE')
                    self.post_received.set()
                    root = ET.fromstring(body)
                    seq = int(root.findtext('header/seq'))
                    if not self.exact_xml_headers(headers):
                        self.down.put_nowait(NATIVE_ERROR)
                    elif self.reply:
                        command = root.findtext('header/command')
                        if command == 'login':
                            self.assertEqual(root.findtext('content/account'), EMAIL)
                            self.assertEqual(root.findtext('content/password'), sha256(PASSWORD.encode()).hexdigest())
                            self.assertNotIn(PASSWORD.encode(), body)
                            self.down.put_nowait(response_xml(seq, '<account-id>SYNTHETIC_ACCOUNT</account-id>'))
                        else:
                            self.assertEqual(command, 'get-device-list')
                            self.assertEqual(root.findtext('header/session'), 'SYNTHETIC_SESSION')
                            self.down.put_nowait(response_xml(seq,
                                '<count>1</count><device><id>SYNTHETIC_DEVICE</id><name>Entrance</name>'
                                '<channel-num>2</channel-num><is-hs-device>0</is-hs-device></device>'))
                    payload = b'HTTP ACK IS NOT AUTHENTICATION'
                elif path == '/qvoauthv2/token':
                    self.assertEqual(method, 'GET')
                    payload = b'{"access_token":"SYNTHETIC_ACCESS","refresh_token":"SYNTHETIC_REFRESH"}'
                elif path in ('/openapi-tdk/client/info', '/openapi-tdk/client/push/token'):
                    self.assertEqual(headers.get('token'), 'SYNTHETIC_ACCESS')
                    content = json.loads(body)
                    self.assertEqual(content['clientId'], '003-4123-' + INSTALLATION)
                    if path.endswith('/token'):
                        self.assertEqual(content['serverTypeToken'], [{'serverType': 'fcm', 'pushToken': FCM_TOKEN}])
                    payload = b'{"code":0}'
                elif path == '/UserAlarm':
                    self.assertTrue(self.exact_xml_headers(headers))
                    self.assertNotIn('SYNTHETIC_AUTH_COOKIE', headers.get('cookie', ''))
                    root = ET.fromstring(body)
                    if root.findtext('header/command') == 'client-login':
                        self.assertEqual(root.findtext('content/client/token'), FCM_TOKEN)
                        cookies = 'Set-Cookie: alarm=SYNTHETIC_ALARM_COOKIE; Path=/; Secure; HttpOnly\r\n'
                    else:
                        self.assertEqual(headers.get('cookie'), 'alarm=SYNTHETIC_ALARM_COOKIE')
                        self.assertEqual(root.findtext('header/command'), 'client-update-dev')
                        self.assertEqual(root.findtext('content/devid'), 'SYNTHETIC_DEVICE')
                        self.assertEqual(root.findtext('content/chno'), '1')
                    payload = response_xml()
                else:
                    raise AssertionError('Unexpected test endpoint')
                writer.write((f'HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n'
                    f'{cookies}Content-Type: application/xml\r\nConnection: close\r\n\r\n').encode() + payload)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.server_errors.append(repr(exc))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
            self.writers.discard(writer)
            self.handlers.discard(task)

    @staticmethod
    def exact_xml_headers(headers):
        return headers.get('content-type') == 'application/xml' and headers.get('charset') == 'utf-8'

    async def test_apk_headers_complete_https_login_registration_and_scoped_subscription(self):
        client = self.client()
        async with asyncio.timeout(5):
            account = await client.async_login(EMAIL, PASSWORD)
            self.assertEqual(account.account_id, 'SYNTHETIC_ACCOUNT')
            devices = await client.async_list_devices()
            self.assertEqual(devices[0].channels, (0, 1))
            await client.async_register(FCM_TOKEN)
            await client.async_subscribe(devices[0], 1)
            await client.async_subscribe(devices[0], 1, False)
        xml_requests = [request for request in self.requests if request[0] == 'POST'
                        and request[1] in ('/auth/user;jus_duplex=up', '/UserAlarm')]
        self.assertEqual(len(xml_requests), 5)
        self.assertTrue(all(self.exact_xml_headers(request[2]) for request in xml_requests))
        alarms = [ET.fromstring(request[3]) for request in xml_requests if request[1] == '/UserAlarm']
        self.assertEqual([root.findtext('content/ifpush') for root in alarms[1:]], ['1', '0'])
        self.assertEqual({session.kind for session in self.sessions}, {'auth', 'alarm', 'openapi'})
        await client.async_close()
        self.assertFalse(client._pending)
        self.assertIsNone(client._poll_task)

    async def test_combined_charset_header_reproduces_native_error_without_authentication(self):
        client = self.client(old_headers=True)
        async with asyncio.timeout(5):
            with self.assertRaises(protocol.QvCloudError) as raised:
                await client.async_login(EMAIL, PASSWORD)
        self.assertEqual(raised.exception.code, 'invalid_response')
        self.assertIsNone(client._account)
        self.assertEqual(sum(method == 'POST' for method, *_ in self.requests), 1)
        self.assertFalse(any('/UserAlarm' in request[1] for request in self.requests))

    async def test_http_ack_alone_never_authenticates_and_cancel_closes_down_connection(self):
        client = self.client()
        self.reply = False
        login = asyncio.create_task(client.async_login(EMAIL, PASSWORD))
        try:
            async with asyncio.timeout(5):
                await self.post_received.wait()
                await self.pending_down.wait()
            self.assertFalse(login.done())
            self.assertIsNone(client._account)
            login.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await login
            await client.async_close()
            async with asyncio.timeout(2):
                await self.down_disconnected.wait()
            self.assertFalse(client._pending)
            self.assertTrue(all(session.closed for session in self.sessions))
        finally:
            if not login.done():
                login.cancel()
                await asyncio.gather(login, return_exceptions=True)

    async def test_untrusted_https_certificate_stops_before_any_http_or_credentials(self):
        client = self.client(trust=False)
        async with asyncio.timeout(5):
            with self.assertRaises(protocol.QvCloudError):
                await client.async_login(EMAIL, PASSWORD)
        self.assertEqual(self.requests, [])
        self.assertIsNone(client._account)
        self.assertFalse(self.post_received.is_set())
