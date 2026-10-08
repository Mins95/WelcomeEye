"""Ephemeral local certificates and mocked failures; no device or cloud I/O."""
import asyncio
import errno
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from ipaddress import IPv4Address
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import warnings

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier

from load_integration import load

trust = load('connect3.trust')
DEFAULT_CONTEXT = ssl.create_default_context


def generate(key, *, issuer_cert=None, issuer_key=None, cn='synthetic.invalid',
             days_before=-1, days_after=1, address='127.0.0.1', ca=False,
             critical_unknown=False, unknown_extension=None, unknown_critical=False,
             unknown_oid='1.2.3.4.5'):
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (x509.CertificateBuilder().subject_name(name)
        .issuer_name(issuer_cert.subject if issuer_cert is not None else name)
        .public_key(key.public_key()).serial_number(1)
        .not_valid_before(now + timedelta(days=days_before))
        .not_valid_after(now + timedelta(days=days_after))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
        # Python 3.13+ default contexts use VERIFY_X509_STRICT. Use a complete
        # synthetic CA/leaf profile instead of relaxing production validation.
        .add_extension(x509.KeyUsage(digital_signature=not ca,
            content_commitment=False, key_encipherment=not ca,
            data_encipherment=False, key_agreement=False,
            key_cert_sign=ca, crl_sign=ca, encipher_only=False,
            decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
            (issuer_key or key).public_key()), critical=False))
    if not ca:
        builder = builder.add_extension(x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    if address:
        builder = builder.add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(IPv4Address(address))]), critical=False)
    if critical_unknown or unknown_extension is not None:
        builder = builder.add_extension(x509.UnrecognizedExtension(
            ObjectIdentifier(unknown_oid), b'\x05\x00' if critical_unknown else unknown_extension),
            critical=True if critical_unknown else unknown_critical)
    return builder.sign(issuer_key or key, hashes.SHA256())


def encoded(tag, value):
    return trust._encoded(SimpleNamespace(tag=tag, value=value))


def legacy_der(cert, key, serial):
    """Re-sign synthetic eziotest with a zero/negative serial, never rewrite a peer."""
    root = trust.der_reader._tree(cert.public_bytes(serialization.Encoding.DER))
    fields = list(root.children[0].children)
    position = 1 if fields[0].tag == 0xa0 else 0
    values = [trust._encoded(node) for node in fields]
    values[position] = encoded(2, serial)
    tbs = encoded(0x30, b''.join(values))
    signature = key.sign(tbs, padding.PKCS1v15(), hashes.SHA256())
    return encoded(0x30, tbs + trust._encoded(root.children[1]) + encoded(3, b'\x00' + signature))


def writer(der):
    peer = Mock()
    peer.getpeercert.return_value = der
    return SimpleNamespace(start_tls=AsyncMock(), get_extra_info=Mock(return_value=peer),
        close=Mock(), wait_closed=AsyncMock(), transport=SimpleNamespace(abort=Mock()),
        write=Mock())


class TrustTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory(prefix='welcomeeye-trust-synthetic-')
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.ca = generate(cls.ca_key, cn='synthetic root', address=None, ca=True)
        cls.ca_file = Path(cls.directory.name) / 'root.pem'
        cls.ca_file.write_bytes(cls.ca.public_bytes(serialization.Encoding.PEM))
        cls.cert = generate(cls.key)
        cls.other = generate(cls.other_key)
        cls.ca_leaf = generate(cls.key, issuer_cert=cls.ca, issuer_key=cls.ca_key)
        cls.expired = generate(cls.key, days_before=-2, days_after=-1)
        cls.future = generate(cls.key, days_before=1, days_after=2)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.servers = []
        self.handlers = set()
        self.application_bytes = []
        self.previous_handler = asyncio.get_running_loop().get_exception_handler()
        def expected_handshake_errors(loop, context):
            if isinstance(context.get('exception'), (ssl.SSLError, ConnectionResetError)):
                return
            loop.default_exception_handler(context)
        asyncio.get_running_loop().set_exception_handler(expected_handshake_errors)

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
            await server.wait_closed()
        tasks = tuple(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.get_running_loop().set_exception_handler(self.previous_handler)

    async def server(self, certificate, key=None):
        index = len(self.servers)
        cert_file = Path(self.directory.name) / f'leaf-{index}.pem'
        key_file = Path(self.directory.name) / f'key-{index}.pem'
        cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes((key or self.key).private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_file), str(key_file))
        async def serve(reader, stream):
            task = asyncio.current_task()
            self.handlers.add(task)
            try:
                value = await reader.read(65536)
                if value:
                    self.application_bytes.append(value)
            finally:
                await trust.close_writer(stream)
                self.handlers.discard(task)
        server = await asyncio.start_server(serve, '127.0.0.1', 0, ssl=context)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    def der(self, certificate=None):
        return (certificate or self.cert).public_bytes(serialization.Encoding.DER)

    async def test_self_signed_requires_first_use_approval_with_independent_pins(self):
        cgi_port = await self.server(self.cert)
        media_port = await self.server(self.other, self.other_key)
        result = await trust.inspect_trust('127.0.0.1', cgi_port, media_port)
        self.assertTrue(result.requires_approval)
        self.assertFalse(result.trusted)
        self.assertFalse(result.failed)
        for endpoint, certificate in ((result.cgi, self.cert), (result.media, self.other)):
            self.assertEqual(endpoint.status, 'candidate')
            self.assertEqual(endpoint.fingerprint, sha256(self.der(certificate)).hexdigest())
            self.assertEqual(endpoint.validity_status, 'valid')
            self.assertEqual(endpoint.identity_status, 'matches')
            self.assertFalse(endpoint.system_trusted)
            self.assertNotIn(endpoint.fingerprint, repr(result))
        self.assertNotEqual(result.cgi.fingerprint, result.media.fingerprint)
        self.assertEqual(self.application_bytes, [])

    async def test_tcp_mode_inspects_only_cgi_without_media_handshake_or_application_bytes(self):
        port = await self.server(self.cert)
        real_probe = trust._probe
        with patch.object(trust, '_probe', AsyncMock(wraps=real_probe)) as probe:
            candidate = await trust.inspect_trust('127.0.0.1', port, 34567, media_tls=False)
            self.assertTrue(candidate.requires_approval)
            self.assertEqual(candidate.media.status, 'not_applicable')
            self.assertTrue(all(call.args[1] == port for call in probe.await_args_list))
            pinned = await trust.inspect_trust('127.0.0.1', port, 34567, media_tls=False,
                                             cgi_pin=candidate.cgi.fingerprint)
        self.assertTrue(pinned.trusted)
        self.assertEqual(self.application_bytes, [])

    async def test_tcp_inspection_cancellation_closes_only_cgi_and_no_fallback(self):
        stream = writer(self.der())
        async def blocked(*args, **kwargs):
            await asyncio.Future()
        stream.start_tls.side_effect = blocked
        with patch.object(trust.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))) as connect:
            task = asyncio.create_task(trust.inspect_trust('192.0.2.1', media_tls=False))
            while not stream.start_tls.await_count:
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        connect.assert_awaited_once()
        stream.close.assert_called_once()
        stream.wait_closed.assert_awaited_once()
        stream.write.assert_not_called()

    async def test_system_ca_and_ip_identity_are_automatically_trusted(self):
        port = await self.server(self.ca_leaf)
        def local_ca_context():
            context = DEFAULT_CONTEXT(cafile=str(self.ca_file))
            # Check the stricter profile on Python 3.12 too, so incomplete
            # fixtures cannot pass just because an older default is tolerant.
            context.verify_flags |= ssl.VERIFY_X509_STRICT
            return context
        with patch.object(trust.ssl, 'create_default_context', local_ca_context):
            result = await trust.inspect_trust('127.0.0.1', port, port)
        self.assertTrue(result.trusted)
        self.assertFalse(result.requires_approval)
        self.assertEqual(result.cgi.status, 'system_ca')
        self.assertTrue(result.cgi.system_trusted)
        self.assertEqual(self.application_bytes, [])

    async def test_unknown_noncritical_vendor_value_keeps_ca_and_exact_pin_validation(self):
        leaf = generate(self.key, issuer_cert=self.ca, issuer_key=self.ca_key,
                        unknown_extension=b'SYNTHETIC_OPAQUE_VENDOR_DATA')
        der = self.der(leaf)
        self.assertEqual(trust.der_reader.metadata(der)['certificate_metadata_status'], 'parsed')
        self.assertEqual(x509.load_der_x509_certificate(der).serial_number, 1)
        port = await self.server(leaf)
        def local_ca_context():
            context = DEFAULT_CONTEXT(cafile=str(self.ca_file))
            context.verify_flags |= ssl.VERIFY_X509_STRICT
            return context
        with patch.object(trust.ssl, 'create_default_context', local_ca_context):
            automatic = await trust.inspect_trust('127.0.0.1', port, media_tls=False)
            retained = await trust.inspect_trust('127.0.0.1', port, media_tls=False,
                                               cgi_pin=sha256(der).hexdigest())
            changed = await trust.inspect_trust('127.0.0.1', port, media_tls=False, cgi_pin='0' * 64)
        self.assertEqual(automatic.cgi.status, 'system_ca')
        self.assertEqual(retained.cgi.status, 'pinned')
        self.assertEqual(changed.cgi.status, 'pin_mismatch')
        self.assertTrue(automatic.cgi.system_trusted)
        self.assertEqual(retained.cgi.fingerprint, sha256(der).hexdigest())
        self.assertEqual(automatic.cgi.key_type, 'rsa')
        self.assertEqual(automatic.cgi.key_bits, 2048)
        self.assertEqual(self.application_bytes, [])
        self.assertNotIn('SYNTHETIC_OPAQUE_VENDOR_DATA', repr(automatic))

    async def test_opaque_noncritical_selfsigned_value_still_requires_explicit_approval(self):
        leaf = generate(self.key, unknown_extension=b'SYNTHETIC_OPAQUE_VENDOR_DATA')
        port = await self.server(leaf)
        result = await trust.inspect_trust('127.0.0.1', port, media_tls=False)
        self.assertEqual(result.cgi.status, 'candidate')
        self.assertFalse(result.cgi.system_trusted)
        self.assertTrue(result.requires_approval)
        self.assertEqual(self.application_bytes, [])

    async def test_ca_valid_self_issued_leaf_is_not_assumed_self_signed(self):
        leaf = generate(self.key, issuer_cert=self.ca, issuer_key=self.ca_key, cn='synthetic root')
        self.assertEqual(leaf.subject, leaf.issuer)
        port = await self.server(leaf)
        def local_ca_context():
            context = DEFAULT_CONTEXT(cafile=str(self.ca_file))
            context.verify_flags |= ssl.VERIFY_X509_STRICT
            return context
        with patch.object(trust.ssl, 'create_default_context', local_ca_context):
            result = await trust.inspect_trust('127.0.0.1', port, media_tls=False)
        self.assertEqual(result.cgi.status, 'system_ca')
        self.assertTrue(result.cgi.system_trusted)
        self.assertTrue(result.cgi.self_issued)
        self.assertEqual(self.application_bytes, [])

    def test_critical_unknown_and_malformed_san_still_fail_closed(self):
        critical = generate(self.key, unknown_extension=b'SYNTHETIC_OPAQUE_VENDOR_DATA',
                            unknown_critical=True)
        with self.assertRaisesRegex(trust.CertificatePolicyError, 'certificate_unknown_critical_extension'):
            trust._properties(self.der(critical), '127.0.0.1')
        malformed_san = generate(self.key, address=None,
            unknown_extension=b'SYNTHETIC_INVALID_SAN', unknown_oid='2.5.29.17')
        with self.assertRaises(trust.der_reader.CertificateMetadataError):
            trust._properties(self.der(malformed_san), '127.0.0.1')

    async def test_weak_key_failure_reports_safe_policy_details_without_relaxing_limits(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        certificate = generate(key)
        der = self.der(certificate)
        # No socket needed: this tests the post-handshake policy, independent
        # of platform TLS security levels. A matching pin does not bypass it.
        with patch.object(trust, '_probe', AsyncMock(return_value=der)):
            result = await trust.inspect_trust('127.0.0.1', media_tls=False,
                                              cgi_pin=sha256(der).hexdigest())
        self.assertEqual(result.cgi.status, 'failed')
        self.assertEqual(result.cgi.reason, 'certificate_weak_key')
        self.assertEqual(result.cgi.key_type, 'rsa')
        self.assertEqual(result.cgi.key_bits, 1024)
        self.assertEqual(result.cgi.serial_status, 'positive')
        self.assertEqual(result.cgi.validity_status, 'valid')
        self.assertEqual(result.cgi.fingerprint, '')
        self.assertNotIn(der.hex(), repr(result))

    async def test_ca_chain_with_wrong_ip_still_requires_approval(self):
        leaf = generate(self.key, issuer_cert=self.ca, issuer_key=self.ca_key, address='192.0.2.1')
        port = await self.server(leaf)
        with patch.object(trust.ssl, 'create_default_context',
                          lambda: DEFAULT_CONTEXT(cafile=str(self.ca_file))):
            result = await trust.inspect_trust('127.0.0.1', port, port)
        self.assertEqual(result.cgi.status, 'candidate')
        self.assertEqual(result.cgi.identity_status, 'mismatch')
        self.assertFalse(result.cgi.system_trusted)
        self.assertEqual(self.application_bytes, [])

    async def test_matching_manual_pin_is_preserved_and_changed_pin_requires_approval(self):
        port = await self.server(self.ca_leaf)
        fingerprint = sha256(self.der(self.ca_leaf)).hexdigest()
        with patch.object(trust.ssl, 'create_default_context',
                          lambda: DEFAULT_CONTEXT(cafile=str(self.ca_file))):
            result = await trust.inspect_trust('127.0.0.1', port, port,
                cgi_pin=fingerprint.upper(), media_pin='0' * 64)
        self.assertEqual(result.cgi.status, 'pinned')
        self.assertEqual(result.media.status, 'pin_mismatch')
        self.assertTrue(result.media.system_trusted)
        self.assertTrue(result.media.requires_approval)
        self.assertEqual(result.media.reason, 'certificate_pin_mismatch')
        self.assertEqual(self.application_bytes, [])

    async def test_expired_and_future_cannot_be_approved_even_with_matching_pin(self):
        for certificate, reason in ((self.expired, 'certificate_expired'),
                                    (self.future, 'certificate_not_yet_valid')):
            port = await self.server(certificate)
            result = await trust.inspect_trust('127.0.0.1', port, port,
                cgi_pin=sha256(self.der(certificate)).hexdigest())
            self.assertTrue(result.failed)
            self.assertFalse(result.requires_approval)
            self.assertEqual(result.cgi.reason, reason)
            self.assertEqual(result.cgi.fingerprint, '')
        self.assertEqual(self.application_bytes, [])

    async def test_malformed_oversized_or_missing_der_is_not_candidate(self):
        for der in (b'bad PRIVATE_CERTIFICATE', self.der() + b'\x00', b'x' * 65537, None):
            streams = [writer(der), writer(der)]
            with patch.object(trust.asyncio, 'open_connection', AsyncMock(side_effect=[
                    (Mock(), streams[0]), (Mock(), streams[1])])):
                result = await trust.inspect_trust('192.0.2.1')
            self.assertTrue(result.failed)
            self.assertFalse(result.requires_approval)
            self.assertEqual(result.cgi.reason, 'certificate_malformed')
            self.assertNotIn('PRIVATE', repr(result))
            for stream in streams:
                stream.close.assert_called_once()
                stream.wait_closed.assert_awaited_once()
                stream.write.assert_not_called()

    def test_legacy_non_positive_serial_eziotest_is_bounded_warning_free(self):
        certificate = generate(self.key, cn='eziotest')
        for serial in (b'\x00', b'\xff'):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('error')
                properties = trust._properties(legacy_der(certificate, self.key, serial), '127.0.0.1')
            self.assertEqual(properties['serial_status'], 'non_positive')
            self.assertEqual(properties['validity_status'], 'valid')
            self.assertEqual(caught, [])
        with self.assertRaisesRegex(trust.CertificatePolicyError, 'certificate_non_positive_serial'):
            trust._properties(legacy_der(self.cert, self.key, b'\x00'), '127.0.0.1')

    def test_invalid_self_signature_key_or_critical_extension_rejected(self):
        corrupted = self.der()[:-1] + bytes([self.der()[-1] ^ 1])
        with self.assertRaisesRegex(trust.CertificatePolicyError, 'certificate_invalid_signature'):
            trust._properties(corrupted, '127.0.0.1')
        critical = generate(self.key, critical_unknown=True)
        with self.assertRaisesRegex(trust.CertificatePolicyError, 'certificate_unknown_critical_extension'):
            trust._properties(self.der(critical), '127.0.0.1')
        from test_r002_certificate import synthetic_certificate
        with self.assertRaises(ValueError):
            trust._properties(synthetic_certificate(), '127.0.0.1')

    async def test_network_and_protocol_failures_have_no_unverified_retry(self):
        for error, reason in ((OSError('PRIVATE_HOST'), 'certificate_network_error'),
                              (ConnectionRefusedError(errno.ECONNREFUSED, 'PRIVATE_HOST'), 'certificate_connection_refused'),
                              (OSError(errno.EHOSTUNREACH, 'PRIVATE_HOST'), 'certificate_network_unreachable'),
                              (ConnectionResetError(errno.ECONNRESET, 'PRIVATE_HOST'), 'certificate_connection_reset'),
                              (ssl.SSLError('PRIVATE_CERT'), 'certificate_tls_error')):
            with patch.object(trust.asyncio, 'open_connection', AsyncMock(side_effect=error)) as connect:
                result = await trust.inspect_trust('192.0.2.1')
            self.assertEqual(connect.await_count, 2)  # One independent attempt per endpoint.
            self.assertEqual(result.cgi.reason, reason)
            self.assertNotIn('PRIVATE', repr(result))

    async def test_timeout_cancellation_and_close_failure_release_both_writers(self):
        async def block(*args, **kwargs):
            await asyncio.Future()
        for cancel in (False, True):
            streams = [writer(self.der()), writer(self.der())]
            for stream in streams:
                stream.start_tls.side_effect = block
            with patch.object(trust.asyncio, 'open_connection', AsyncMock(side_effect=[
                    (Mock(), streams[0]), (Mock(), streams[1])])), patch.object(trust, 'ENDPOINT_TIMEOUT', .1):
                task = asyncio.create_task(trust.inspect_trust('192.0.2.1'))
                while not all(stream.start_tls.await_count for stream in streams):
                    await asyncio.sleep(0)
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    result = await task
                    self.assertEqual(result.cgi.reason, 'certificate_timeout')
                    self.assertEqual(result.media.reason, 'certificate_timeout')
            for stream in streams:
                stream.close.assert_called_once()
                stream.wait_closed.assert_awaited_once()
                stream.write.assert_not_called()
        streams = [writer(self.der()), writer(self.der())]
        for stream in streams:
            stream.wait_closed.side_effect = TimeoutError
        with patch.object(trust.asyncio, 'open_connection', AsyncMock(side_effect=[
                (Mock(), streams[0]), (Mock(), streams[1])])):
            result = await trust.inspect_trust('192.0.2.1')
        self.assertFalse(result.failed)
        for stream in streams:
            stream.transport.abort.assert_called_once()

    async def test_invalid_endpoint_or_pin_rejected_before_any_socket(self):
        for host, cgi_port, media_port, pin in (
                ('invalid', 443, 8443, ''), ('0.0.0.0', 443, 8443, ''),
                ('224.0.0.1', 443, 8443, ''), ('255.255.255.255', 443, 8443, ''),
                ('192.0.2.1', True, 8443, ''), ('192.0.2.1', 443, 65536, ''),
                ('192.0.2.1', 443, 8443, 'bad')):
            with patch.object(trust.asyncio, 'open_connection', AsyncMock()) as connect:
                with self.assertRaises(ValueError):
                    await trust.inspect_trust(host, cgi_port, media_port, cgi_pin=pin)
                connect.assert_not_called()

    def test_approval_tuple_binds_host_and_each_port_while_legacy_stays_compatible(self):
        data = {'host': '192.0.2.1'}
        self.assertTrue(trust.trust_endpoint_matches(data))
        data['trust_endpoint'] = {'host': '192.0.2.1', 'cgi_port': 443, 'media_port': 8443}
        self.assertTrue(trust.trust_endpoint_matches(data))
        for field, value in (('host', '192.0.2.2'), ('cgi_port', 444), ('media_port', 8444)):
            changed = {**data, field: value}
            self.assertFalse(trust.trust_endpoint_matches(changed))
        self.assertFalse(trust.trust_endpoint_matches({**data, 'trust_endpoint': {}}))


if __name__ == '__main__':
    unittest.main()
