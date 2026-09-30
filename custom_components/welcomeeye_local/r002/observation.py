"""Compare two provisional slices of one bounded buffer, never prove framing."""
from .protocol import HEADER, R002ProtocolError, inspect_prefix, parse_header

RESPONSE_LIMIT = 64


def _candidate(data, header_size):
    declared = int.from_bytes(data[6:8], 'little') if len(data) >= 8 else None
    expected = header_size + declared if declared is not None else None
    enough = expected is not None and len(data) >= expected
    return {
        'header_size': header_size,
        'declared_length': declared,
        'expected_total': expected,
        'body_bytes_available': max(0, len(data) - header_size),
        'body_candidate_hex': data[header_size:expected].hex() if enough else None,
        'missing_bytes': max(0, expected - len(data)) if expected is not None else None,
        'extra_bytes': max(0, len(data) - expected) if expected is not None else None,
        'extra_hex': data[expected:].hex() if enough else None,
        'has_enough_bytes': enough,
    }


def analyze_response(data, requested_type):
    """Detailed response only: callers must not persist any candidate/raw fields."""
    if len(data) > RESPONSE_LIMIT:
        raise ValueError('Observation exceeds response limit')
    prefix = data[:HEADER.size]
    decoded, errors = inspect_prefix(prefix, requested_type)
    error_type = None
    try:
        parse_header(prefix, requested_type)
    except R002ProtocolError as exc:
        error_type = type(exc).__name__
    ten, twelve = _candidate(data, 10), _candidate(data, 12)
    if ten['has_enough_bytes'] and twelve['has_enough_bytes']:
        assessment = 'ambiguous'
    elif ten['has_enough_bytes']:
        assessment = 'header_10_has_enough_bytes'
    else:
        assessment = 'insufficient_data'
    return {
        'response_hex': data.hex(), 'bytes_collected': len(data),
        'raw_header_hex': prefix.hex(), 'prefix_bytes_received': len(prefix),
        'decoded_candidate': decoded, 'header_valid': not errors,
        'header_validation_errors': errors, 'beta2_error_type': error_type,
        'header_10_candidate': ten, 'header_12_candidate': twelve,
        'framing_assessment': assessment, 'framing_confirmed': False,
    }
