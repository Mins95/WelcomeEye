"""Bounded QV V4 discovery decoding, reconstructed from the Door Connect ARM64 SDK.

This format has encryption but no authenticated integrity. A decoded record is
an observation, never proof of model, identity or successful authentication.
"""
from dataclasses import dataclass, field
from ipaddress import IPv4Address
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..r002.qv_discovery import discover_qv, safe_summary
from .kdf import derive_key

PREFIXES = (b'ASZENO.SEARCH.V4.1', b'ASZENO.SEARCH.V4')
MAX_DATAGRAM = 2048
RECORD_SIZE = 520


class DiscoveryDecodeError(ValueError):
    """Only a fixed error code is exposed; never packet contents."""


@dataclass(frozen=True, repr=False)
class DeviceRecord:
    address: str
    uid: str = field(repr=False)
    device_type: str
    firmware: str
    stream_port: int
    cgi_port: int
    tls_media_port: int
    channels: int
    firmware_base: str | None = field(default=None, repr=False)
    firmware_base_status: str = 'not_requested'

    def details(self):
        # Deliberately omit UID/address even in the optional service response.
        return {**{key: getattr(self, key) for key in (
            'device_type', 'firmware', 'stream_port', 'cgi_port', 'tls_media_port', 'channels')},
            'firmware_sdk': self.firmware, 'firmware_base': self.firmware_base,
            'firmware_source': 'sdk_override_0x1bc',
            'firmware_base_status': self.firmware_base_status}


def _text(record, start, size, *, required=False):
    segment = record[start:start + size]
    if b'\0' not in segment:
        raise DiscoveryDecodeError('unterminated_text')
    try:
        value = segment.split(b'\0', 1)[0].decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise DiscoveryDecodeError('invalid_encoding') from None
    if (required and not value) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise DiscoveryDecodeError('invalid_text')
    return value


def decode_datagram(data: bytes, *, include_details=False) -> DeviceRecord:
    if not isinstance(data, bytes) or len(data) > MAX_DATAGRAM:
        raise DiscoveryDecodeError('datagram_size')
    prefix = next((p for p in PREFIXES if data.startswith(p)), None)
    if prefix is None:
        raise DiscoveryDecodeError('unsupported_prefix')
    envelope = data[len(prefix):]
    if len(envelope) < 40:
        raise DiscoveryDecodeError('truncated_envelope')
    seed_size, cipher_size = struct.unpack_from('<II', envelope, 8)
    if seed_size > 1024:
        raise DiscoveryDecodeError('seed_size')
    # Native ParseData copies one 520-byte record. Support only its minimal CBC
    # container (528 bytes); do not silently decode another record revision.
    if cipher_size != 528:
        raise DiscoveryDecodeError('unsupported_record_size')
    if len(envelope) != 40 + seed_size + cipher_size:
        raise DiscoveryDecodeError('length_mismatch')
    seed = envelope[40:40 + seed_size]
    decryptor = Cipher(algorithms.AES(derive_key(seed)), modes.CBC(b'0' * 16)).decryptor()
    record = (decryptor.update(envelope[40 + seed_size:]) + decryptor.finalize())[:RECORD_SIZE]
    # The eight unused decrypted tail bytes have no established padding scheme.
    address = IPv4Address(record[0x64:0x68])
    if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
        raise DiscoveryDecodeError('invalid_address')
    uid = _text(record, 0xc8, 64, required=True)
    kind = _text(record, 0x188, 20)
    # JNI overwrites the base version with the field at 0x1bc (not 0x108).
    version = _text(record, 0x1bc, 16)
    base, base_status = None, 'not_requested'
    if include_details:
        # Observed base firmware ends before the date-like slot at 0x128.
        # This conservative metadata window is not a proven C member width.
        # Never make optional metadata a new condition for endpoint acceptance.
        try:
            base = _text(record, 0x108, 32)
            base_status = 'decoded'
        except DiscoveryDecodeError:
            base_status = 'invalid'
    port = lambda offset: struct.unpack_from('<H', record, offset)[0]
    return DeviceRecord(str(address), uid, kind, version,
                        port(0x78), port(0x1a8), port(0x1cc), port(0x1a4),
                        base, base_status)


def decode_observation(host, result, *, include_details=False, expected_uid=None):
    """Decode an already collected QV observation without further device I/O.

    Raw datagrams are ephemeral. Normal output and persisted summaries exclude
    all arbitrary decoded strings, addresses, identifiers and encrypted bytes.
    """
    summary = {**safe_summary(result), 'decoded_records': 0, 'duplicate_records': 0,
               'decode_errors': {}, 'device_authenticated': False,
               'model_confirmed': False}
    details, seen, identities = [], set(), set()
    for response in result['responses']:
        try:
            if response['truncated']:
                raise DiscoveryDecodeError('datagram_size')
            record = decode_datagram(bytes.fromhex(response['response_hex']),
                                     include_details=include_details)
            if record.address != str(IPv4Address(host)):
                raise DiscoveryDecodeError('address_mismatch')
            # Keep conflicting model observations even when the SDK's normal
            # display deduplication would collapse the same UID/port tuple.
            identities.add((record.uid, record.device_type))
            identity = (record.uid, record.address, record.stream_port, record.cgi_port)
            if identity in seen:
                summary['duplicate_records'] += 1
                continue
            seen.add(identity)
            summary['decoded_records'] += 1
            if include_details:
                details.append(record.details())
        except DiscoveryDecodeError as exc:
            code = str(exc)  # Fixed literals generated above, never a remote message.
            summary['decode_errors'][code] = summary['decode_errors'].get(code, 0) + 1
    if expected_uid is not None:
        # Only a consistency check before sending an imported credential, not
        # authenticated discovery or a substitute for TLS trust/pinning.
        summary['credential_identity_status'] = (
            'not_observed' if not identities else
            'ambiguous' if len(identities) != 1 else
            'matched' if identities == {(expected_uid, 'IDS94E6SW')} else 'mismatch')
    return {**summary, 'metadata_decoded': bool(summary['decoded_records']),
            **({'records': details} if include_details else {})}


async def discover(host, *, include_details=False, expected_uid=None):
    """One independently confirmed UDP exchange, never an R002 TCP probe."""
    result = await discover_qv(host, include_response=True)
    return decode_observation(host, result, include_details=include_details, expected_uid=expected_uid)
