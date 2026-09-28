"""Observed issue #7 framing, not an identification of message semantics."""
from dataclasses import dataclass
import struct

ALLOWED_TYPES = (14, 15, 26, 28)
HEADER = struct.Struct('<HHHHI')
MAX_BODY = 4096


class R002ProtocolError(ValueError):
    """A bounded, sanitized protocol failure."""
    counter = 'malformed_headers'


class OversizedFrame(R002ProtocolError):
    counter = 'oversized_frames'


class UnexpectedType(R002ProtocolError):
    counter = 'unexpected_types'


class UnexpectedVersion(R002ProtocolError):
    counter = 'unexpected_versions'


@dataclass(frozen=True)
class Header:
    response_type: int
    version: int
    flags: int
    declared_length: int


def request(message_type):
    if type(message_type) is not int or message_type not in ALLOWED_TYPES:
        raise ValueError('Unsupported investigation type')
    return struct.pack('<H', message_type) + bytes(6)


def inspect_prefix(data, expected_type):
    """Provisional interpretation only; values must not enter persistent diagnostics."""
    if len(data) != HEADER.size:
        return None, ['invalid_header_size']
    values = dict(zip(('response_type', 'version', 'flags', 'declared_length',
                       'field_8_11_u32'), HEADER.unpack(data)))
    errors = [reason for reason, invalid in (
        ('unexpected_type', values['response_type'] != expected_type),
        ('unexpected_version', values['version'] != 1),
        ('nonzero_flags', values['flags'] != 0),
        ('nonzero_field_8_11', values['field_8_11_u32'] != 0),
        ('oversized_length', values['declared_length'] > MAX_BODY),
    ) if invalid]
    return values, errors


def parse_header(data, expected_type):
    values, errors = inspect_prefix(data, expected_type)
    if errors:
        # Preserve beta.1 verdict, precedence and exception classes.
        error_class = {'unexpected_type': UnexpectedType,
                       'unexpected_version': UnexpectedVersion,
                       'oversized_length': OversizedFrame}.get(errors[0], R002ProtocolError)
        raise error_class(errors[0])
    return Header(*(values[key] for key in ('response_type', 'version', 'flags', 'declared_length')))
