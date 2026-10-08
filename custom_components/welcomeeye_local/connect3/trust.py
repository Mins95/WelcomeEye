"""Private, bounded TLS inspection for an explicitly configured Connect3.

Only TLS handshakes are sent. A system-CA handshake checks the configured IP
identity first. A local inspection context can then observe a certificate for
explicit first-use approval; it never authenticates CGI or media. The fingerprint
and certificate dates returned here belong to the private config flow, not logs
or diagnostics. No global SSL context or trust store is changed.

Expired, future, malformed, unusable-key and invalid self-signature certificates
are never first-use candidates. Non-positive serials from legacy eziotest devices
remain inspectable with the existing bounded DER reader, without relying on the
deprecated X.509 loader tolerance or rewriting the certificate.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from ipaddress import IPv4Address
import re
import socket
import ssl

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, padding, rsa

from ..r002 import certificate as der_reader
from ..r002.transport import close_writer
from .certificate import _inspection_context, failure_reason

ENDPOINT_TIMEOUT = 6.0
HANDSHAKE_TIMEOUT = 2.0
SAN_OID = bytes.fromhex('551d11')
KNOWN_CRITICAL_EXTENSIONS = {bytes.fromhex(value) for value in (
    '551d0f', '551d11', '551d13', '551d1e', '551d20', '551d23', '551d25',
)}


class CertificatePolicyError(ValueError):
    """Fixed labels for a well-formed certificate outside inspection policy."""

    def __init__(self, reason, properties=None):
        super().__init__(reason)
        self.properties = properties or {}


@dataclass(frozen=True)
class EndpointTrust:
    """Private outcome; even repr excludes certificate fingerprints."""
    status: str
    fingerprint: str = field(default='', repr=False)
    reason: str | None = None
    system_trusted: bool = False
    validity_status: str = 'unknown'
    serial_status: str = 'unknown'
    identity_status: str = 'unknown'
    subject_label: str = 'unknown'
    issuer_label: str = 'unknown'
    not_valid_before: str | None = None
    not_valid_after: str | None = None
    self_issued: bool | None = None
    key_type: str = 'unknown'
    key_bits: int | None = None

    @property
    def trusted(self):
        return self.status in ('system_ca', 'pinned')

    @property
    def requires_approval(self):
        return self.status in ('candidate', 'pin_mismatch')


@dataclass(frozen=True)
class TrustInspection:
    """Independent CGI and media results, never a shared implicit pin."""
    cgi: EndpointTrust
    media: EndpointTrust

    @property
    def failed(self):
        return any(endpoint.status == 'failed' for endpoint in (self.cgi, self.media))

    @property
    def requires_approval(self):
        return any(endpoint.requires_approval for endpoint in (self.cgi, self.media))

    @property
    def trusted(self):
        return self.cgi.trusted and (self.media.trusted or self.media.status == 'not_applicable')


def _endpoint(host, port):
    try:
        address = IPv4Address(host)
    except (TypeError, ValueError):
        raise ValueError('invalid_tls_endpoint') from None
    if (address.is_multicast or address.is_unspecified or int(address) == 0xffffffff
            or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('invalid_tls_endpoint')
    return str(address)


def trust_endpoint_matches(data):
    """Keep legacy TLS bindings; new approvals also bind the selected transport."""
    transport = data.get('media_transport', 'tls')
    if transport not in ('tls', 'connect3_tcp'):
        return False
    if data.get('trust_endpoint') is None:
        return transport == 'tls'
    approved = data['trust_endpoint']
    legacy_keys = {'host', 'cgi_port', 'media_port'}
    if type(approved) is not dict or set(approved) not in (legacy_keys, legacy_keys | {'media_transport'}):
        return False
    if 'media_transport' not in approved and transport != 'tls':
        return False
    try:
        current = {'host': _endpoint(data.get('host'), data.get('cgi_port', 443)),
                   'cgi_port': data.get('cgi_port', 443),
                   'media_port': 34567 if transport == 'connect3_tcp' else data.get('media_port', 8443)}
        if 'media_transport' in approved:
            current['media_transport'] = transport
        _endpoint(current['host'], current['media_port'])
        approved_host = _endpoint(approved.get('host'), approved.get('cgi_port'))
        _endpoint(approved_host, approved.get('media_port'))
    except ValueError:
        return False
    return current == approved


def _encoded(node):
    """Reconstitute a node already checked to have canonical DER lengths."""
    length = len(node.value)
    if length < 128:
        size = bytes([length])
    else:
        width = (length.bit_length() + 7) // 8
        size = bytes([0x80 | width]) + length.to_bytes(width, 'big')
    return bytes([node.tag]) + size + node.value


def _time(node):
    value = node.value.decode('ascii')
    if node.tag == 23:
        # X.509 UTCTime's pivot is 1950, not strptime's platform pivot.
        year = int(value[:2])
        value = str(1900 + year if year >= 50 else 2000 + year) + value[2:]
    return datetime.strptime(value, '%Y%m%d%H%M%SZ').replace(tzinfo=timezone.utc)


def _identity(fields, address):
    ips = []
    for optional in fields[6:]:
        if optional.tag != 0xa3:
            continue
        for extension in optional.children[0].children:
            oid = extension.children[0].value
            if len(extension.children) == 3 and oid not in KNOWN_CRITICAL_EXTENSIONS:
                raise CertificatePolicyError('certificate_unknown_critical_extension')
            # Unknown noncritical values are opaque vendor data. The outer
            # Extension/OID/OCTET STRING was already checked by metadata();
            # only extensions whose ASN.1 syntax we understand are decoded.
            if oid not in KNOWN_CRITICAL_EXTENSIONS:
                continue
            value = der_reader._tree(extension.children[-1].value)
            if oid != SAN_OID:
                continue
            names = der_reader._sequence(value)
            der_reader.require(bool(names))
            for name in names:
                # GeneralName must be one of the explicitly tagged alternatives.
                der_reader.require(name.tag in (0xa0, 0x81, 0x82, 0xa3, 0xa4,
                                               0xa5, 0x86, 0x87, 0x88))
                if name.tag == 0x87:
                    der_reader.require(len(name.value) in (4, 16))
                    ips.append(name.value)
                elif name.tag in (0x81, 0x82, 0x86):
                    der_reader.require(bool(name.value) and all(byte < 128 for byte in name.value))
    if not ips:
        return 'missing'
    return 'matches' if IPv4Address(address).packed in ips else 'mismatch'


def _verify_self_signature(root, key):
    tbs, algorithm, signature = root.children
    oid = algorithm.children[0].value
    digest = {
        bytes.fromhex('2a864886f70d010105'): hashes.SHA1,
        bytes.fromhex('2a864886f70d01010b'): hashes.SHA256,
        bytes.fromhex('2a864886f70d01010c'): hashes.SHA384,
        bytes.fromhex('2a864886f70d01010d'): hashes.SHA512,
        bytes.fromhex('2a8648ce3d0401'): hashes.SHA1,
        bytes.fromhex('2a8648ce3d040302'): hashes.SHA256,
        bytes.fromhex('2a8648ce3d040303'): hashes.SHA384,
        bytes.fromhex('2a8648ce3d040304'): hashes.SHA512,
    }.get(oid)
    value, data = signature.value[1:], _encoded(tbs)
    if isinstance(key, rsa.RSAPublicKey) and digest and oid.startswith(bytes.fromhex('2a864886f70d0101')):
        key.verify(value, data, padding.PKCS1v15(), digest())
    elif isinstance(key, ec.EllipticCurvePublicKey) and digest and oid.startswith(bytes.fromhex('2a8648ce3d04')):
        key.verify(value, data, ec.ECDSA(digest()))
    elif ((isinstance(key, ed25519.Ed25519PublicKey) and oid == bytes.fromhex('2b6570'))
          or (isinstance(key, ed448.Ed448PublicKey) and oid == bytes.fromhex('2b6571'))):
        key.verify(value, data)
    else:
        raise UnsupportedAlgorithm('unsupported_certificate_signature')


def _properties(der, address, *, system_trusted=False):
    """Bound structural work before key/signature operations; no X.509 loader."""
    summary = der_reader.metadata(der)
    root = der_reader._tree(der)
    fields = list(root.children[0].children)
    if fields[0].tag == 0xa0:
        fields.pop(0)
    issuer, validity, subject, spki = fields[2:6]
    before, after = map(_time, validity.children)
    der_reader.require(before < after)
    now = datetime.now(timezone.utc)
    status = 'expired' if now > after else 'not_yet_valid' if now < before else 'valid'
    properties = dict(validity_status=status,
        serial_status=summary['certificate_serial_status'],
        identity_status='unknown', key_type='unknown', key_bits=None,
        subject_label=summary['tls_certificate_cn'],
        issuer_label=summary['tls_certificate_issuer_cn'],
        not_valid_before=before.isoformat(), not_valid_after=after.isoformat(),
        self_issued=issuer == subject)
    try:
        properties['identity_status'] = _identity(fields, address)
    except CertificatePolicyError as exc:
        raise CertificatePolicyError(str(exc), properties) from None
    if (properties['serial_status'] == 'non_positive'
            and (properties['subject_label'], properties['issuer_label']) != ('eziotest', 'eziotest')):
        raise CertificatePolicyError('certificate_non_positive_serial', properties)
    if status != 'valid':
        return properties
    try:
        key = serialization.load_der_public_key(_encoded(spki))
    except UnsupportedAlgorithm:
        raise CertificatePolicyError('certificate_unsupported_key', properties) from None
    if isinstance(key, rsa.RSAPublicKey):
        properties.update(key_type='rsa', key_bits=key.key_size)
        if key.key_size < 2048:
            raise CertificatePolicyError('certificate_weak_key', properties)
        if key.key_size > 8192:
            raise CertificatePolicyError('certificate_unsupported_key', properties)
    elif isinstance(key, ec.EllipticCurvePublicKey):
        properties.update(key_type='ec', key_bits=key.key_size)
        if key.key_size < 224:
            raise CertificatePolicyError('certificate_weak_key', properties)
        if key.key_size > 521:
            raise CertificatePolicyError('certificate_unsupported_key', properties)
    elif isinstance(key, ed25519.Ed25519PublicKey):
        properties['key_type'] = 'ed25519'
    elif isinstance(key, ed448.Ed448PublicKey):
        properties['key_type'] = 'ed448'
    else:
        raise CertificatePolicyError('certificate_unsupported_key', properties)
    if issuer == subject and not system_trusted:
        # Equal distinguished names mean self-issued, not necessarily signed
        # by the leaf's own key. A successful CA+IP handshake has already
        # verified the issuer chain; rechecking with the leaf key is incorrect.
        try:
            _verify_self_signature(root, key)
        except InvalidSignature:
            raise CertificatePolicyError('certificate_invalid_signature', properties) from None
        except UnsupportedAlgorithm:
            raise CertificatePolicyError('certificate_unsupported_algorithm', properties) from None
    return properties


async def _probe(address, port, context):
    writer = None
    try:
        _, writer = await asyncio.open_connection(address, port, family=socket.AF_INET)
        await writer.start_tls(context, server_hostname=address,
                               ssl_handshake_timeout=HANDSHAKE_TIMEOUT)
        peer = writer.get_extra_info('ssl_object')
        der = peer.getpeercert(binary_form=True) if peer is not None else None
        if type(der) is not bytes or not 0 < len(der) <= der_reader.MAX_CERTIFICATE_SIZE:
            raise der_reader.CertificateMetadataError('invalid_certificate_size')
        return der
    finally:
        if writer is not None:
            try:
                await close_writer(writer)
            except BaseException:
                writer.transport.abort()
                raise


async def _inspect_endpoint(address, port, pin):
    properties = {}
    try:
        async with asyncio.timeout(ENDPOINT_TIMEOUT):
            context = await asyncio.to_thread(ssl.create_default_context)
            try:
                der = await _probe(address, port, context)
                system_trusted = True
            except ssl.SSLCertVerificationError:
                # Only failed certificate trust authorizes this observation.
                # A network/protocol error never becomes an insecure fallback.
                context = await asyncio.to_thread(_inspection_context)
                der = await _probe(address, port, context)
                system_trusted = False
            properties = await asyncio.to_thread(_properties, der, address, system_trusted=system_trusted)
            if properties['validity_status'] != 'valid':
                return EndpointTrust('failed', reason='certificate_' + properties['validity_status'], **properties)
            fingerprint = sha256(der).hexdigest()
            if pin and not compare_digest(bytes.fromhex(pin), bytes.fromhex(fingerprint)):
                status, reason = 'pin_mismatch', 'certificate_pin_mismatch'
            elif pin:
                status, reason = 'pinned', None
            elif system_trusted:
                status, reason = 'system_ca', None
            else:
                status = 'candidate'
                reason = ('legacy_non_positive_serial' if properties['serial_status'] == 'non_positive'
                          else 'certificate_not_system_trusted')
            return EndpointTrust(status, fingerprint, reason, system_trusted, **properties)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return EndpointTrust('failed', reason='certificate_timeout')
    except InvalidSignature:
        return EndpointTrust('failed', reason='certificate_invalid_signature', validity_status='invalid')
    except UnsupportedAlgorithm:
        return EndpointTrust('failed', reason='certificate_unsupported_algorithm', validity_status='invalid')
    except CertificatePolicyError as exc:
        return EndpointTrust('failed', reason=str(exc), **exc.properties)
    except ValueError:
        return EndpointTrust('failed', reason='certificate_malformed', validity_status='invalid')
    except ssl.SSLError:
        return EndpointTrust('failed', reason='certificate_tls_error')
    except OSError as exc:
        return EndpointTrust('failed', reason='certificate_' + failure_reason(exc))


async def inspect_trust(host, cgi_port=443, media_port=8443, *, cgi_pin='', media_pin='', media_tls=True):
    """Inspect selected unicast TLS endpoints independently without credentials.

    An existing manual pin keeps precedence even when system trust now succeeds.
    A changed valid certificate is reviewable, but cannot replace that pin until
    approved. Callers decide whether an old CGI pin was also the old media pin.
    Explicit non-TLS media skips all media certificate/network inspection.
    """
    address = _endpoint(host, cgi_port)
    if type(media_tls) is not bool:
        raise ValueError('invalid_media_tls_policy')
    if media_tls:
        _endpoint(address, media_port)
    for pin in ((cgi_pin, media_pin) if media_tls else (cgi_pin,)):
        if type(pin) is not str or (pin and not re.fullmatch(r'[a-fA-F0-9]{64}', pin)):
            raise ValueError('invalid_certificate_pin')
    if not media_tls:
        # The owner-selected TCP media endpoint has no TLS certificate. Do not
        # probe it, 8443, or any alternative port from this private inspection.
        return TrustInspection(await _inspect_endpoint(address, cgi_port, cgi_pin),
                               EndpointTrust('not_applicable'))
    cgi, media = await asyncio.gather(_inspect_endpoint(address, cgi_port, cgi_pin),
                                    _inspect_endpoint(address, media_port, media_pin))
    return TrustInspection(cgi, media)
