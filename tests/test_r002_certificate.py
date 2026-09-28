"""SYNTHETIC DER structures only; not the tester's certificate or trusted certs."""
import unittest
import warnings
from unittest.mock import patch

from load_integration import load

certificate = load('r002.certificate')


def tlv(tag, value):
    length = len(value)
    size = bytes([length]) if length < 128 else bytes([0x82])+length.to_bytes(2, 'big') if length > 255 else bytes([0x81, length])
    return bytes([tag])+size+value


def seq(*children):
    return tlv(0x30, b''.join(children))


def name(cn='eziotest', oid=b'\x55\x04\x03', string_tag=12):
    encoding = {12: 'utf-8', 19: 'ascii', 30: 'utf-16-be'}[string_tag]
    return seq(tlv(0x31, seq(tlv(6, oid), tlv(string_tag, cn.encode(encoding)))))


def synthetic_certificate(serial=b'\x01', cn='eziotest', issuer='eziotest', subject=None):
    algorithm = seq(tlv(6, bytes.fromhex('2a864886f70d01010b')), tlv(5, b''))
    key_algorithm = seq(tlv(6, bytes.fromhex('2a864886f70d010101')), tlv(5, b''))
    validity = seq(tlv(23, b'260101000000Z'), tlv(23, b'270101000000Z'))
    # Syntactically structured key/signature, not usable for trust authentication.
    key = seq(tlv(2, b'\x01'), tlv(2, b'\x03'))
    tbs = seq(tlv(0xa0, tlv(2, b'\x02')), tlv(2, serial), algorithm, name(issuer),
              validity, subject if subject is not None else name(cn), seq(key_algorithm, tlv(3, b'\x00'+key)))
    return seq(tbs, algorithm, tlv(3, b'\x00SYNTHETIC_SIGNATURE'))


class CertificateTests(unittest.TestCase):
    def test_positive_zero_negative_and_no_warnings(self):
        for serial, expected in ((b'\x01', 'positive'), (b'\x00', 'non_positive'), (b'\xff', 'non_positive')):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('error')
                result = certificate.metadata(synthetic_certificate(serial))
            self.assertEqual(caught, [])
            self.assertEqual(result['certificate_serial_status'], expected)
            self.assertEqual(result['tls_certificate_cn'], 'eziotest')
            self.assertEqual(result['tls_certificate_issuer_cn'], 'eziotest')
            self.assertFalse(result['certificate_trust_authenticated'])

    def test_true_cn_oid_not_strings_anywhere(self):
        self.assertEqual(certificate.metadata(synthetic_certificate(cn='other'))['tls_certificate_cn'], 'other')
        result = certificate.metadata(synthetic_certificate(subject=name('eziotest', b'\x55\x04\x0a')))
        self.assertEqual(result['tls_certificate_cn'], 'other')
        self.assertEqual(result['tls_certificate_issuer_cn'], 'eziotest')

    def test_supported_cn_encodings(self):
        for tag in (12, 19, 30):
            result = certificate.metadata(synthetic_certificate(subject=name(string_tag=tag)))
            self.assertEqual(result['tls_certificate_cn'], 'eziotest')

    def test_duplicate_cn_is_not_recognized(self):
        attributes = tlv(0x31, seq(tlv(6, certificate.CN_OID), tlv(12, b'eziotest')))
        result = certificate.metadata(synthetic_certificate(subject=seq(attributes, attributes)))
        self.assertEqual(result['tls_certificate_cn'], 'other')

    def test_truncation_trailing_and_size_limits(self):
        der = synthetic_certificate()
        for data in [der[:i] for i in range(len(der))] + [der+b'\x00', bytes(65537)]:
            with self.assertRaises(certificate.CertificateMetadataError):
                certificate.metadata(data)

    def test_non_der_lengths_integer_and_wrong_schema(self):
        for data in (b'\x30\x80\x00\x00', b'\x30\x81\x01\x00', b'\x30\xff',
                     seq(tlv(12, b'eziotest')), synthetic_certificate(b''), synthetic_certificate(b'\x00\x01'),
                     synthetic_certificate(b'\xff\xff'), synthetic_certificate(subject=seq(tlv(12, b'eziotest')))):
            with self.assertRaises(certificate.CertificateMetadataError):
                certificate.metadata(data)

    def test_depth_node_bounds_and_bad_oid(self):
        nested = tlv(12, b'eziotest')
        for _ in range(14):
            nested = seq(nested)
        for data in (nested, seq(*[tlv(5, b'')]*1024),
                     synthetic_certificate(subject=name(oid=b'\x80\x55\x04\x03'))):
            with self.assertRaises(certificate.CertificateMetadataError):
                certificate.metadata(data)

    def test_metadata_does_not_depend_on_cryptography_tolerance(self):
        import builtins
        original = builtins.__import__
        def reject(name, *args, **kwargs):
            if name.startswith('cryptography'):
                raise ValueError('Simulated strict X509 loader')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=reject):
            self.assertEqual(certificate.metadata(synthetic_certificate(b'\xff'))['tls_certificate_cn'], 'eziotest')
