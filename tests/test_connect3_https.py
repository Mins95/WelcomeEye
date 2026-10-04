"""Real HTTPS on loopback with synthetic credentials and a temporary certificate.

The server checks an independent wire expectation. No device, manufacturer
service, committed private key, authCode or hardware response is involved.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from load_integration import load

cgi = load('connect3.cgi')
AUTH_CODE = 'SYNTHETIC_LOOPBACK_AUTHCODE'
STREAM_KEY = 'SYNTHETIC_PRIVATE_STREAM_KEY'
SUCCESS_XML = (f'<envelope><body><error>0</error><content><key>{STREAM_KEY}</key>'
               '</content></body></envelope>').encode()


class HTTPSTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory(prefix='welcomeeye-synthetic-tls-')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic.invalid')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
        directory = Path(cls.directory.name)
        cert_path, key_path = directory / 'certificate.pem', directory / 'key.pem'
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cls.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.context.load_cert_chain(str(cert_path), str(key_path))
        cls.fingerprint = sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.requests = []
        self.handlers = set()
        self.writers = set()
        self.handler_errors = []
        self.response_body = SUCCESS_XML
        self.http_status = 200
        self.wait_for_client_close = False
        self.request_received = asyncio.Event()
        self.server = await asyncio.start_server(self._serve, '127.0.0.1', 0,
            ssl=self.context, ssl_handshake_timeout=1.0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        for handler in tuple(self.handlers):
            handler.cancel()
        await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        self.assertFalse(self.handlers)
        self.assertFalse(self.writers)
        self.assertEqual(self.handler_errors, [])

    async def _serve(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        try:
            async with asyncio.timeout(2.0):
                head = await reader.readuntil(b'\r\n\r\n')
                lines = head.decode('ascii').split('\r\n')
                method, target, version = lines[0].split(' ')
                headers = dict(line.split(':', 1) for line in lines[1:] if line)
                headers = {key.lower(): value.strip() for key, value in headers.items()}
                length = int(headers['content-length'])
                if not 0 <= length <= 4096:
                    raise ValueError('Synthetic request exceeded its fixed limit')
                body = await reader.readexactly(length)
                self.requests.append((method, target, version, headers, body))
                self.request_received.set()
                if self.wait_for_client_close:
                    await reader.read(1)
                    return
                response = (f'HTTP/1.1 {self.http_status} Synthetic\r\n'
                    f'Content-Length: {len(self.response_body)}\r\n'
                    'Content-Type: application/xml\r\nConnection: close\r\n\r\n').encode()
                writer.write(response + self.response_body)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            # Rejected TLS trust/pinning may close before any HTTP request.
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.handler_errors.append(type(exc).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
            finally:
                self.writers.discard(writer)
                self.handlers.discard(task)

    async def read(self, auth_code=AUTH_CODE, **kwargs):
        return await cgi.read_device('127.0.0.1', auth_code, port=self.port,
            certificate_sha256=kwargs.pop('certificate_sha256', self.fingerprint), **kwargs)

    def assert_one_expected_request(self, auth_code=AUTH_CODE, *, expected_password=None):
        self.assertEqual(len(self.requests), 1)
        method, target, version, headers, body = self.requests[0]
        self.assertEqual((method, target, version), ('POST', '/tdkcgi', 'HTTP/1.1'))
        self.assertEqual(headers['content-type'].replace(' ', '').lower(),
                         'application/xml;charset=utf-8')
        root = ET.fromstring(body)
        self.assertEqual(root.tag, 'envelope')
        self.assertEqual([child.tag for child in root], ['header', 'body'])
        self.assertEqual(len(root.find('header')), 4)
        self.assertEqual({child.tag: child.text for child in root.find('header')}, {
            'security': 'username', 'username': 'adminapp2',
            'password': expected_password or sha256(auth_code.encode('utf-8')).hexdigest(),
            'passwordencode': '1'})
        self.assertEqual([child.tag for child in root.find('body')], ['command', 'content'])
        self.assertEqual(root.findtext('body/command'), 'get.device.streamkey')
        content = root.find('body/content')
        self.assertIsNotNone(content)
        self.assertEqual(len(content), 0)
        self.assertFalse(content.text)
        self.assertNotIn(AUTH_CODE.encode(), body)

    async def test_pinned_https_authentication_uses_exact_apk_xml_once(self):
        observation = {}
        result = await self.read(diagnostics=observation)
        self.assert_one_expected_request()
        self.assertEqual(result['authentication'], 'cgi_accepted')
        self.assertTrue(result['streamkey_received'])
        self.assertFalse(result['media_available'])
        self.assertEqual(observation, {
            'last_stage': 'cgi_accepted', 'request_sent_count': 1,
            'tls_verified': True, 'tls_policy': 'certificate_pin',
            'authentication_status': 'accepted', 'http_status': 200})
        for private in (AUTH_CODE, STREAM_KEY, self.fingerprint, '127.0.0.1'):
            self.assertNotIn(private, json.dumps(result))
            self.assertNotIn(private, json.dumps(observation))

    async def test_already_encoded_authcode_is_not_hashed_again(self):
        encoded = sha256(AUTH_CODE.encode('utf-8')).hexdigest()
        result = await self.read(encoded)
        self.assert_one_expected_request(encoded, expected_password=encoded)
        self.assertEqual(result['authentication'], 'cgi_accepted')
        self.assertNotIn(encoded, json.dumps(result))

    async def test_wrong_pin_prevents_http_and_is_not_retried(self):
        observation = {}
        with self.assertRaises(aiohttp.ServerFingerprintMismatch):
            await self.read(certificate_sha256='0' * 64, diagnostics=observation)
        self.assertEqual(self.requests, [])
        self.assertEqual(observation['last_stage'], 'tcp_tls_connect')
        self.assertEqual(observation['request_sent_count'], 0)
        self.assertFalse(observation['tls_verified'])
        self.assertEqual(observation['authentication_status'], 'not_checked')

    async def test_normal_tls_trust_rejects_synthetic_self_signed_certificate(self):
        observation = {}
        with self.assertRaises(aiohttp.ClientSSLError):
            await self.read(certificate_sha256='', diagnostics=observation)
        self.assertEqual(self.requests, [])
        self.assertEqual(observation['tls_policy'], 'system_ca')
        self.assertEqual(observation['request_sent_count'], 0)
        self.assertFalse(observation['tls_verified'])

    async def test_device_refusal_is_single_shot_and_does_not_expose_code(self):
        self.response_body = b'<envelope><body><error>401</error><content/></body></envelope>'
        observation = {}
        with self.assertRaises(cgi.CGIError) as caught:
            await self.read(diagnostics=observation)
        self.assert_one_expected_request()
        self.assertNotIn(AUTH_CODE, str(caught.exception))
        self.assertEqual(str(caught.exception), 'auth_code_rejected')
        self.assertEqual(observation['device_error_code'], 401)
        self.assertEqual(observation['error_source'], 'xml_device')
        self.assertEqual(observation['authentication_status'], 'rejected')
        self.assertEqual(observation['http_status'], 200)
        self.assertEqual(observation['request_sent_count'], 1)

    async def test_http_refusal_is_single_shot(self):
        self.http_status = 401
        self.response_body = b'SYNTHETIC_PRIVATE_REMOTE_ERROR'
        observation = {}
        with self.assertRaises(cgi.CGIError) as caught:
            await self.read(diagnostics=observation)
        self.assert_one_expected_request()
        self.assertNotIn('SYNTHETIC_PRIVATE_REMOTE_ERROR', str(caught.exception))
        self.assertEqual(str(caught.exception), 'http_unauthorized')
        self.assertEqual(observation['error_source'], 'http')
        self.assertEqual(observation['authentication_status'], 'not_checked')
        self.assertEqual(observation['http_status'], 401)
        self.assertEqual(observation['request_sent_count'], 1)

    async def test_malformed_xml_is_single_shot(self):
        self.response_body = b'<envelope><body><error>0</error><content><key>PRIVATE'
        with self.assertRaises(cgi.CGIError) as caught:
            await self.read()
        self.assert_one_expected_request()
        self.assertNotIn('PRIVATE', str(caught.exception))

    async def test_missing_key_is_not_success(self):
        self.response_body = b'<envelope><body><error>0</error><content><key/></content></body></envelope>'
        with self.assertRaises(cgi.CGIError):
            await self.read()
        self.assert_one_expected_request()

    async def test_response_timeout_is_single_shot_and_closes_connection(self):
        self.wait_for_client_close = True
        observation = {}
        with patch.object(cgi, 'TIMEOUT', 0.5):
            with self.assertRaises(TimeoutError):
                await self.read(diagnostics=observation)
        self.assertTrue(self.request_received.is_set())
        self.assert_one_expected_request()
        self.assertEqual(observation['last_stage'], 'request_sent')
        self.assertEqual(observation['request_sent_count'], 1)
        self.assertTrue(observation['tls_verified'])
        self.assertEqual(observation['authentication_status'], 'not_checked')

    async def test_cancellation_after_request_closes_connection_without_retry(self):
        self.wait_for_client_close = True
        task = asyncio.create_task(self.read())
        await asyncio.wait_for(self.request_received.wait(), timeout=2.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_one_expected_request()
