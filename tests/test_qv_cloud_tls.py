"""Bootstrap-only vendor trust and an in-memory outsider-CA rejection."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import ssl
import tempfile
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from load_integration import load

tls = load('qv_cloud_tls')
protocol = load('qv_cloud_protocol')
URL = 'https://sh2.qvcloud.net/mst/query'


class TrustTests(unittest.TestCase):
    def test_only_the_apk_public_ca_is_trusted_with_validity_checks(self):
        context = tls.bootstrap_ssl_context(URL)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertFalse(context.check_hostname)
        self.assertFalse(context.verify_flags & 0x200000)  # Native no-date-check flag is not copied.
        roots = context.get_ca_certs(binary_form=True)
        self.assertEqual(len(roots), 1)
        self.assertEqual(sha256(roots[0]).hexdigest(), tls.VENDOR_CA_SHA256)
        self.assertNotIn('PRIVATE KEY', tls.VENDOR_CA_PEM)

    def test_context_cannot_be_reused_for_another_authority_or_path(self):
        for url in ('http://sh2.qvcloud.net/mst/query',
                    'https://sh2.qvcloud.net:4443/mst/query',
                    'https://other.qvcloud.net/mst/query',
                    'https://sh2.qvcloud.net.evil.example/mst/query',
                    'https://sh2.qvcloud.net/auth/user',
                    'https://127.0.0.1/mst/query',
                    URL + '?redirect=somewhere', URL + '#fragment',
                    'https://user:password@sh2.qvcloud.net/mst/query'):
            with self.subTest(url=url):
                with self.assertRaises(protocol.QvCloudError):
                    tls.bootstrap_ssl_context(url)

    def test_outsider_certificate_is_rejected_before_application_bytes(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'sh2.qvcloud.net')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(123)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
        with tempfile.TemporaryDirectory() as directory:
            cert_path, key_path = Path(directory) / 'server.pem', Path(directory) / 'key.pem'
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(cert_path, key_path)
            client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
            server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
            client = tls.bootstrap_ssl_context(URL).wrap_bio(client_in, client_out,
                server_side=False, server_hostname='sh2.qvcloud.net')
            server = server_context.wrap_bio(server_in, server_out, server_side=True)
            rejected = False
            for _ in range(10):
                try:
                    client.do_handshake()
                except ssl.SSLWantReadError:
                    pass
                except ssl.SSLCertVerificationError:
                    rejected = True
                    break
                server_in.write(client_out.read())
                try:
                    server.do_handshake()
                except ssl.SSLWantReadError:
                    pass
                client_in.write(server_out.read())
            self.assertTrue(rejected, 'An unrelated CA must never authorize directory requests')


if __name__ == '__main__':
    unittest.main()
