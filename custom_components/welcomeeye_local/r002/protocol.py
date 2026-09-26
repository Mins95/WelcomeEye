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


def parse_header(data, expected_type):
    if len(data) != HEADER.size:
        raise R002ProtocolError('Invalid header size')
    kind, version, flags, length, padding = HEADER.unpack(data)
    if kind != expected_type:
        raise UnexpectedType('Unexpected response type')
    if version != 1:
        raise UnexpectedVersion('Unexpected response version')
    if flags != 0 or padding != 0:
        raise R002ProtocolError('Unrecognized header fields')
    if length > MAX_BODY:
        raise OversizedFrame('Response exceeds investigation limit')
    return Header(kind, version, flags, length)
