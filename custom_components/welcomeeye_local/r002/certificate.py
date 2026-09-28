"""Bounded DER certificate metadata reader, NOT X.509 trust validation.

Non-positive serials are reported, never rewritten. No cryptography loader is
used: its deprecated tolerance for these certificates is not a dependency.
Only subject/issuer CN labels and serial sign escape this module.
"""
from dataclasses import dataclass
from datetime import datetime

MAX_CERTIFICATE_SIZE = 65536
MAX_DEPTH = 12
MAX_NODES = 1024
PARSER = 'bounded_der_metadata_v1'
CN_OID = b'\x55\x04\x03'  # 2.5.4.3, only inside a Name AttributeTypeAndValue.


class CertificateMetadataError(ValueError):
    """Sanitized malformed/unsupported certificate, never includes input bytes."""


def require(condition):
    if not condition:
        raise CertificateMetadataError('Invalid certificate metadata structure')


@dataclass(frozen=True)
class Node:
    tag: int
    value: bytes
    children: tuple


def _integer(value):
    require(bool(value))
    if len(value) > 1:
        require(not (value[0] == 0 and not value[1] & 0x80))
        require(not (value[0] == 255 and value[1] & 0x80))


def _bits(value):
    require(bool(value) and value[0] <= 7)
    require(len(value) > 1 or value[0] == 0)
    if value[0]:
        require(value[-1] & ((1 << value[0]) - 1) == 0)


def _oid(value):
    require(bool(value) and len(value) <= 128)
    at_start = True
    for byte in value:
        require(not (at_start and byte == 0x80))
        at_start = not bool(byte & 0x80)
    require(at_start)


def _tree(data):
    require(type(data) is bytes and 0 < len(data) <= MAX_CERTIFICATE_SIZE)
    nodes = 0

    def read(start, limit, depth):
        nonlocal nodes
        nodes += 1
        require(nodes <= MAX_NODES and depth <= MAX_DEPTH and start + 2 <= limit)
        tag, first = data[start:start+2]
        require(tag != 0 and tag & 31 != 31)  # No EOC/high-tag-number forms needed.
        pos = start + 2
        if first & 0x80:
            count = first & 0x7f
            require(1 <= count <= 3 and pos + count <= limit and data[pos] != 0)
            length = int.from_bytes(data[pos:pos+count], 'big')
            require(length >= 128)  # DER definite, shortest length encoding.
            pos += count
        else:
            length = first
        end = pos + length
        require(end <= limit)
        children = []
        if tag & 0x20:
            cursor = pos
            while cursor < end:
                child, cursor = read(cursor, end, depth+1)
                children.append(child)
        value = data[pos:end]
        if tag == 2:
            _integer(value)
        elif tag == 3:
            _bits(value)
        elif tag == 6:
            _oid(value)
        elif tag == 5:
            require(not value)
        elif tag == 1:
            require(value in (b'\x00', b'\xff'))
        return Node(tag, value, tuple(children)), end

    root, end = read(0, len(data), 0)
    require(end == len(data))
    return root


def _sequence(node, count=None):
    require(node.tag == 0x30 and (count is None or len(node.children) == count))
    return node.children


def _algorithm(node):
    fields = _sequence(node)
    require(1 <= len(fields) <= 2 and fields[0].tag == 6)


def _name(node):
    rdns = _sequence(node)
    matches = []
    for rdn in rdns:
        require(rdn.tag == 0x31 and bool(rdn.children))
        # DER SET ordering is lexicographic on complete encodings. For our
        # metadata purpose, every attribute is structurally checked regardless.
        for attribute in rdn.children:
            oid, value = _sequence(attribute, 2)
            require(oid.tag == 6 and not value.tag & 0x20)
            if oid.value != CN_OID:
                continue
            require(0 < len(value.value) <= 1024)
            encoding = {12: 'utf-8', 19: 'ascii', 20: 'latin-1',
                        28: 'utf-32-be', 30: 'utf-16-be'}.get(value.tag)
            require(encoding is not None)
            try:
                text = value.value.decode(encoding)
            except UnicodeError:
                raise CertificateMetadataError('Invalid common name encoding') from None
            matches.append(text == 'eziotest')
    # Ambiguous/multiple CNs do not establish our fingerprint.
    return 'eziotest' if matches == [True] else 'other'


def metadata(der):
    root = _tree(der)
    tbs, algorithm, signature = _sequence(root, 3)
    _algorithm(algorithm)
    require(signature.tag == 3 and len(signature.value) > 1 and signature.value[0] == 0)
    fields = list(_sequence(tbs))
    version = 0
    if fields and fields[0].tag == 0xa0:
        explicit = fields.pop(0).children
        require(len(explicit) == 1 and explicit[0].tag == 2)
        require(explicit[0].value in (b'\x01', b'\x02'))
        version = explicit[0].value[0]
    require(6 <= len(fields) <= 9)
    serial, tbs_algorithm, issuer, validity, subject, spki = fields[:6]
    require(serial.tag == 2 and len(serial.value) <= 32)
    _algorithm(tbs_algorithm)
    require(tbs_algorithm == algorithm)
    for moment in _sequence(validity, 2):
        require(moment.tag in (23, 24))
        try:
            value = moment.value.decode('ascii')
            require(len(value) == (13 if moment.tag == 23 else 15) and value.endswith('Z')
                    and value[:-1].isdigit())
            datetime.strptime(value, '%y%m%d%H%M%SZ' if moment.tag == 23 else '%Y%m%d%H%M%SZ')
        except (UnicodeError, ValueError):
            raise CertificateMetadataError('Invalid certificate validity structure') from None
    key_algorithm, key = _sequence(spki, 2)
    _algorithm(key_algorithm)
    require(key.tag == 3 and len(key.value) > 1)
    last_tag = 0
    for optional in fields[6:]:
        require(optional.tag in (0x81, 0x82, 0xa3) and optional.tag > last_tag)
        last_tag = optional.tag
        if optional.tag in (0x81, 0x82):
            require(version >= 1)
            _bits(optional.value)
        else:
            require(version == 2 and len(optional.children) == 1)
            extensions = _sequence(optional.children[0])
            require(bool(extensions))
            seen = set()
            for extension in extensions:
                values = _sequence(extension)
                require(len(values) in (2, 3) and values[0].tag == 6 and values[-1].tag == 4)
                require(values[0].value not in seen)
                seen.add(values[0].value)
                if len(values) == 3:
                    require(values[1].tag == 1 and values[1].value == b'\xff')
    # Parse both Names fully before returning any identifying label.
    issuer_cn, subject_cn = _name(issuer), _name(subject)
    positive = not bool(serial.value[0] & 0x80) and any(serial.value)
    return {'tls_certificate_cn': subject_cn, 'tls_certificate_issuer_cn': issuer_cn,
            'certificate_metadata_status': 'parsed',
            'certificate_serial_status': 'positive' if positive else 'non_positive',
            'certificate_parser': PARSER, 'certificate_parse_error_type': None,
            'certificate_trust_authenticated': False}
