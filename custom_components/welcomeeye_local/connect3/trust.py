"""Private, bounded TLS inspection for an explicitly configured Connect3.

Only TLS handshakes are sent. A system-CA handshake checks the configured IP
identity first. A local inspection context can then observe a certificate for
explicit first-use approval; it never authenticates CGI or media. The fingerprint
and certificate dates returned here belong to the private config flow, not logs
or diagnostics. No global SSL context or trust store is changed.

The system-CA path validates the issuer chain and configured IP through SSL.
Explicit pin trust binds the original certificate bytes, rather than asserting
issuer authority. Bounded structure, validity and key health remain mandatory;
equal certificate dates need a separate approval bound to the exact pin/dates.
issuer signatures/extensions and serial/CN labels are not separate pin gates.
Non-positive serials remain visible without a deprecated X.509 loader or rewriting
the certificate. No key-strength or TLS security setting is weakened.
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

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, rsa

from ..r002 import certificate as der_reader
from ..r002.transport import close_writer
from .certificate import _inspection_context, failure_reason

ENDPOINT_TIMEOUT = 6.0
HANDSHAKE_TIMEOUT = 2.0
SAN_OID = bytes.fromhex('551d11')
DATE_EXCEPTION_POLICY = 'zero_duration_v1'
FAILURE_STAGES = frozenset(('not_started', 'ca_context', 'ca_handshake',
    'inspection_context', 'inspection_handshake', 'certificate_receive',
    'certificate_metadata', 'certificate_validity', 'certificate_public_key',
    'certificate_key_policy', 'certificate_pin', 'complete'))


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
    failure_stage: str = 'not_started'

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


def _canonical_utc_date(value):
    # The bounded DER reader only accepts whole seconds; approval records use
    # that same fixed format, not arbitrary ISO inputs from persisted data.
    if (type(value) is not str or len(value) != 25
            or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00', value)):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
            and parsed.astimezone(timezone.utc).isoformat() == value)


def date_exception_matches(record, pin, *, before=None, after=None):
    """Recognize only an explicit zero-duration approval for this exact pin.

    Dates must be canonical UTC ISO values. A supplied current date also has to
    match the record; the equal pair in the record always binds both dates.
    Invalid persisted data never authorizes an exception.
    """
    keys = {'policy', 'certificate_sha256', 'not_valid_before', 'not_valid_after'}
    if type(record) is not dict or set(record) != keys:
        return False
    fingerprint = record['certificate_sha256']
    if (type(record['policy']) is not str or record['policy'] != DATE_EXCEPTION_POLICY
            or type(pin) is not str
            or not re.fullmatch(r'[a-fA-F0-9]{64}', pin)
            or type(fingerprint) is not str
            or not re.fullmatch(r'[a-fA-F0-9]{64}', fingerprint)):
        return False
    recorded_before, recorded_after = record['not_valid_before'], record['not_valid_after']
    if (not _canonical_utc_date(recorded_before) or not _canonical_utc_date(recorded_after)
            or recorded_before != recorded_after):
        return False
    if ((before is not None and (type(before) is not str or before != recorded_before))
            or (after is not None and (type(after) is not str or after != recorded_after))):
        return False
    return compare_digest(bytes.fromhex(pin), bytes.fromhex(fingerprint))


def date_exception_record(endpoint):
    """Build private approval data only for an inspected zero-duration cert."""
    if not isinstance(endpoint, EndpointTrust) or endpoint.validity_status != 'zero_duration':
        return None
    record = {'policy': DATE_EXCEPTION_POLICY, 'certificate_sha256': endpoint.fingerprint,
              'not_valid_before': endpoint.not_valid_before, 'not_valid_after': endpoint.not_valid_after}
    if not date_exception_matches(record, endpoint.fingerprint):
        return None
    record['certificate_sha256'] = endpoint.fingerprint.lower()
    return record


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
            # SSL owns PKI extension processing for CA verification. In pin
            # mode, only SAN is useful optional identity metadata; the bounded
            # reader already validates every outer Extension/OID/OCTET STRING.
            if oid != SAN_OID:
                continue
            value = der_reader._tree(extension.children[-1].value)
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


def _properties(der, address, *, system_trusted=False):
    """Pin-safe structure/date/key checks, separate from SSL issuer authority."""
    try:
        summary = der_reader.metadata(der)
        root = der_reader._tree(der)
    except ValueError:
        raise CertificatePolicyError('certificate_malformed',
            {'failure_stage': 'certificate_metadata', 'validity_status': 'invalid'}) from None
    fields = list(root.children[0].children)
    if fields[0].tag == 0xa0:
        fields.pop(0)
    issuer, validity, subject, spki = fields[2:6]
    properties = dict(validity_status='unknown',
        serial_status=summary['certificate_serial_status'],
        identity_status='unknown', key_type='unknown', key_bits=None,
        subject_label=summary['tls_certificate_cn'],
        issuer_label=summary['tls_certificate_issuer_cn'],
        self_issued=issuer == subject)
    try:
        before, after = map(_time, validity.children)
    except ValueError:
        properties.update(failure_stage='certificate_validity', validity_status='invalid')
        raise CertificatePolicyError('certificate_invalid_validity', properties) from None
    # These parsed, sanitized values remain useful even when the interval is
    # equal/reversed. Never retain the original ASN.1 bytes in the result.
    properties.update(not_valid_before=before.isoformat(), not_valid_after=after.isoformat())
    if before > after:
        properties.update(failure_stage='certificate_validity', validity_status='invalid')
        raise CertificatePolicyError('certificate_invalid_validity', properties)
    now = datetime.now(timezone.utc)
    status = ('zero_duration' if before == after else
              'expired' if now > after else 'not_yet_valid' if now < before else 'valid')
    properties['validity_status'] = status
    try:
        properties['identity_status'] = _identity(fields, address)
    except ValueError:
        # This cannot turn an unsuccessful SSL identity check into CA trust.
        properties['identity_status'] = 'unavailable'
    if status not in ('valid', 'zero_duration'):
        properties['failure_stage'] = 'certificate_validity'
        return properties
    properties['failure_stage'] = 'certificate_public_key'
    try:
        key = serialization.load_der_public_key(_encoded(spki))
    except UnsupportedAlgorithm:
        raise CertificatePolicyError('certificate_unsupported_key', properties) from None
    except ValueError:
        raise CertificatePolicyError('certificate_invalid_public_key', properties) from None
    properties['failure_stage'] = 'certificate_key_policy'
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
    properties['failure_stage'] = 'complete'
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
            raise CertificatePolicyError('certificate_malformed',
                {'failure_stage': 'certificate_receive', 'validity_status': 'invalid'})
        return der
    finally:
        if writer is not None:
            try:
                await close_writer(writer)
            except BaseException:
                writer.transport.abort()
                raise


async def _inspect_endpoint(address, port, pin, *, date_exception=None):
    properties = {}
    stage = 'ca_context'
    try:
        async with asyncio.timeout(ENDPOINT_TIMEOUT):
            context = await asyncio.to_thread(ssl.create_default_context)
            stage = 'ca_handshake'
            try:
                der = await _probe(address, port, context)
                system_trusted = True
            except ssl.SSLCertVerificationError:
                # Only failed certificate trust authorizes this observation.
                # A network/protocol error never becomes an insecure fallback.
                stage = 'inspection_context'
                context = await asyncio.to_thread(_inspection_context)
                stage = 'inspection_handshake'
                der = await _probe(address, port, context)
                system_trusted = False
            stage = 'certificate_metadata'
            properties = await asyncio.to_thread(_properties, der, address, system_trusted=system_trusted)
            if properties['validity_status'] not in ('valid', 'zero_duration'):
                return EndpointTrust('failed', reason='certificate_' + properties['validity_status'], **properties)
            fingerprint = sha256(der).hexdigest()
            if pin and not compare_digest(bytes.fromhex(pin), bytes.fromhex(fingerprint)):
                status, reason = 'pin_mismatch', 'certificate_pin_mismatch'
                properties['failure_stage'] = 'certificate_pin'
            elif properties['validity_status'] == 'zero_duration':
                # Equal dates have no ordinary validity interval. Neither a
                # manual pin nor a CA result silently grants this extra policy.
                if pin and date_exception_matches(date_exception, pin,
                        before=properties['not_valid_before'], after=properties['not_valid_after']):
                    status, reason = 'pinned', None
                else:
                    status, reason = 'candidate', 'certificate_zero_duration'
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
        return EndpointTrust('failed', reason='certificate_timeout', failure_stage=stage)
    except CertificatePolicyError as exc:
        return EndpointTrust('failed', reason=str(exc), **exc.properties)
    except ssl.SSLError:
        # SSLCertVerificationError also inherits ValueError: TLS classification
        # must precede generic local parsing/context errors.
        return EndpointTrust('failed', reason='certificate_tls_error', failure_stage=stage)
    except ValueError:
        reason = 'certificate_context_error' if stage in ('ca_context', 'inspection_context') else 'certificate_malformed'
        return EndpointTrust('failed', reason=reason, validity_status='invalid', failure_stage=stage)
    except OSError as exc:
        return EndpointTrust('failed', reason='certificate_' + failure_reason(exc), failure_stage=stage)


async def inspect_trust(host, cgi_port=443, media_port=8443, *, cgi_pin='', media_pin='',
                        media_tls=True, date_exceptions=None):
    """Inspect selected unicast TLS endpoints independently without credentials.

    An existing manual pin keeps precedence even when system trust now succeeds.
    A changed valid certificate is reviewable, but cannot replace that pin until
    approved. Callers decide whether an old CGI pin was also the old media pin.
    Explicit non-TLS media skips all media certificate/network inspection.
    Equal-date certificates require a separate per-endpoint approval record.
    """
    address = _endpoint(host, cgi_port)
    if type(media_tls) is not bool:
        raise ValueError('invalid_media_tls_policy')
    if media_tls:
        _endpoint(address, media_port)
    for pin in ((cgi_pin, media_pin) if media_tls else (cgi_pin,)):
        if type(pin) is not str or (pin and not re.fullmatch(r'[a-fA-F0-9]{64}', pin)):
            raise ValueError('invalid_certificate_pin')
    if type(date_exceptions) is not dict or not set(date_exceptions) <= {'cgi', 'media'}:
        date_exceptions = {}
    if not media_tls:
        # The owner-selected TCP media endpoint has no TLS certificate. Do not
        # probe it, 8443, or any alternative port from this private inspection.
        return TrustInspection(await _inspect_endpoint(address, cgi_port, cgi_pin,
                                   date_exception=date_exceptions.get('cgi')),
                               EndpointTrust('not_applicable'))
    cgi, media = await asyncio.gather(_inspect_endpoint(address, cgi_port, cgi_pin,
                                        date_exception=date_exceptions.get('cgi')),
                                    _inspect_endpoint(address, media_port, media_pin,
                                        date_exception=date_exceptions.get('media')))
    return TrustInspection(cgi, media)
