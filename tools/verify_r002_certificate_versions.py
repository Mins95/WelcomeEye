"""Compare X.509 loader behaviour with metadata parsing; SYNTHETIC certs only."""
import json
from pathlib import Path
import sys
import warnings
from unittest.mock import patch

import cryptography
from cryptography import x509
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tests'))
from test_r002_certificate import certificate, synthetic_certificate


def main():
    results = []
    for serial, status in ((b'\x01', 'positive'), (b'\x00', 'non_positive'), (b'\xff', 'non_positive')):
        der = synthetic_certificate(serial)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            try:
                parsed = x509.load_der_x509_certificate(der)
                assert parsed.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == 'eziotest'
                loader = 'accepted'
            except ValueError:
                loader = 'ValueError'
        if status == 'positive':
            assert loader == 'accepted', 'Normal synthetic structure must load in X.509'
        with warnings.catch_warnings(record=True) as metadata_warnings:
            warnings.simplefilter('error')
            with patch.object(x509, 'load_der_x509_certificate', side_effect=ValueError('Strict loader')):
                result = certificate.metadata(der)
        assert result['certificate_serial_status'] == status
        assert result['tls_certificate_cn'] == result['tls_certificate_issuer_cn'] == 'eziotest'
        assert result['certificate_trust_authenticated'] is False
        assert not metadata_warnings
        results.append({'serial_status': status, 'x509_loader': loader,
                        'x509_warnings': [type(item.message).__name__ for item in caught],
                        'metadata_parser': 'parsed_without_warning'})
    print(json.dumps({'cryptography': cryptography.__version__, 'synthetic_results': results}))


if __name__ == '__main__':
    main()
