"""Dedicated QV cloud trust, separate from system trust and local intercom TLS.

The APK public device.pem bundle contains the vendor CA below. It signs the
known sh2.qvcloud.net directory certificate, whose subject has no DNS CN/SAN.
Authentication therefore establishes a certificate from this vendor CA, not a
DNS-name certificate. Restrict its use to the exact bootstrap authority;
never apply this context to local intercoms, arbitrary URLs or redirects.
Native CTDKSsl mode 3 also verifies a configured CA without DNS matching. We
retain validity checks and TLS 1.2 minimum; no APK client key is imported.
"""
import ssl
from urllib.parse import urlsplit

from .qv_cloud_protocol import QvCloudError

VENDOR_CA_SHA256 = 'cbc77b01d58308cc0fa8ee4af2e508af2cb3864e5a88628b0e7d0e474f4d7763'
VENDOR_CA_PEM = '-----BEGIN CERTIFICATE-----\nMIIDGzCCAgOgAwIBAgIBATANBgkqhkiG9w0BAQsFADBIMREwDwYDVQQIEwhTaGFu\nZ0hhaTELMAkGA1UEBhMCQ04xJjAkBgNVBAoTHVFVQUxWSVNJT04gVEVDSE5PTE9H\nWSBDTy4sTFREMB4XDTE3MTIxMTE3MDIxNloXDTM3MTIwNjE3MDIxNlowSDERMA8G\nA1UECBMIU2hhbmdIYWkxCzAJBgNVBAYTAkNOMSYwJAYDVQQKEx1RVUFMVklTSU9O\nIFRFQ0hOT0xPR1kgQ08uLExURDCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoC\nggEBAPTTeZRO+DByeoNFFkeROvjlLjNubbeQfL9IZ6Gkyf+4kTDSczMx2NWTPENL\n4pbHxv7xV6SkMCEhCSgv4tfF26W/sPyuF3uyTj6BNkaEDnxuRCQy+UcdDnibZ0O5\ntjNdm2urWtrAZqMh5HDKt7mZcaDL+Qnv+dVgw+zGQ8YHmIF6tiMaE+VJt1nnWqpr\nYKz50LvfoqxrRp9M/edTMVM/RCX9z4dEmlgPaOYNk77NNlikOo0wb4QlQCdh0IKM\nDyhzup/xAdvEC8QnTE480eh85czjDmavp3LgXqtZlc/bFKvWGWMMJuTkIkP0NPeZ\nm9gun/V7n+dk2dE3RoxRKoKo/dcCAwEAAaMQMA4wDAYDVR0TBAUwAwEB/zANBgkq\nhkiG9w0BAQsFAAOCAQEACOgo8820KnfBK9k2dTKB9v4mEkMCn3GB3LX7UjeU9o5O\niTWa53Ymh083NB/ZPYXQjbdYTbWj/WrlvIWhL+jvt6eL+3U5oS9RlpqDxQNK9g/v\nI75YeCK9dDhklh6mC7ig5OwPJO7sOOm7OiUuwr4Tf2CfNUZUgqrnharPrFddx9sZ\n/cVdQfs2q1POyFD9Uo5rQ7G3ve+86sRcRNs1LYI0lYeN/0Gxkk2WQEXLlptW1Hh7\nKYnCQpWOt/0VLg/viuQ/VCnMsKDmjcKwS/xeLhGa+E+AQsOGSt8h2fWUgOVOBYUS\nhA4Zy71olLpCxmBYxRsnxRO/oOKVk3y103GyLMvMOg==\n-----END CERTIFICATE-----\n'


def bootstrap_ssl_context(url):
    """Authenticate only the known bootstrap directory with the dedicated vendor CA."""
    try:
        target = urlsplit(url)
        hostname = target.hostname or ''
        if (target.scheme != 'https' or (hostname, target.port or 443) != ('sh2.qvcloud.net', 443)
                or target.username is not None or target.password is not None
                or target.path != '/mst/query' or target.query or target.fragment):
            raise ValueError
    except (ValueError, TypeError):
        raise QvCloudError('invalid_service') from None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=VENDOR_CA_PEM)
    return context
