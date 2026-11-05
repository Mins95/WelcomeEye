"""Read-only RFID catalogue formats from the QV vendor SDK.

DeviceJsonRequestHelp / GetDeviceRFIDListContentResp define get.rfid.info.
This is a configured-card catalogue, not proof that a history row identifies
one of these cards. No create, update, delete or unlock command is supported.
"""
import json

from .cgi import CGIError, MAX_DEPTH, MAX_NODES, MAX_XML, encode_auth_code

MAX_CARDS = 256
MAX_CARD_CHANNELS = 64


def badge_request(auth_code):
    """SDK LAN header and fixed read command. No user-defined command or URL."""
    return json.dumps({
        'header': {'security': 'username', 'username': 'adminapp2',
                   'password': encode_auth_code(auth_code), 'passwordencode': 1},
        # RetrofitUtil's Gson omits the null content passed for the full list.
        'body': {'command': 'get.rfid.info'},
    }, ensure_ascii=True, separators=(',', ':')).encode('utf-8')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CGIError('invalid_badge_response')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


def _label(value, maximum, *, empty=False):
    if (not isinstance(value, str) or (not empty and not value)
            or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise CGIError('invalid_badge_response')
    return value


def parse_badges(payload, *, include_details=False):
    """Return only counts by default; identifiers/names require an admin action."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_XML:
        raise CGIError('badge_response_size')
    try:
        root = json.loads(payload.decode('utf-8'), object_pairs_hook=_object,
                          parse_constant=_reject_constant)
    except (ValueError, RecursionError, UnicodeError):
        raise CGIError('invalid_badge_response') from None
    stack, count = [(root, 0)], 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise CGIError('badge_response_complexity')
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    if not isinstance(root, dict) or not isinstance(root.get('body'), dict):
        raise CGIError('invalid_badge_response')
    body = root['body']
    error = body.get('error')
    if type(error) is not int or not -(1 << 31) <= error < (1 << 31):
        raise CGIError('invalid_badge_response')
    if error:
        raise CGIError('auth_code_rejected' if error == 401 else 'device_rejected',
                       device_error_code=error)
    if 'command' in body and body['command'] != 'get.rfid.info':
        raise CGIError('invalid_badge_response')
    content = body.get('content')
    if not isinstance(content, dict) or not isinstance(content.get('cards'), list):
        raise CGIError('badge_catalog_unavailable')
    if len(content['cards']) > MAX_CARDS:
        raise CGIError('badge_catalog_limit')
    cards, seen = [], set()
    for row in content['cards']:
        if not isinstance(row, dict):
            raise CGIError('invalid_badge_response')
        card_id = _label(row.get('cardid'), 128)
        name = _label(row.get('name', ''), 128, empty=True)
        channels = row.get('channels', [])
        if not isinstance(channels, list) or len(channels) > MAX_CARD_CHANNELS:
            raise CGIError('invalid_badge_response')
        numbers = []
        for channel in channels:
            if (not isinstance(channel, dict) or type(channel.get('number')) is not int
                    or not 0 <= channel['number'] <= 65535):
                raise CGIError('invalid_badge_response')
            if channel['number'] not in numbers:
                numbers.append(channel['number'])
        if card_id in seen:
            # Do not silently choose one of two conflicting assignments.
            raise CGIError('ambiguous_badge_catalog')
        seen.add(card_id)
        # Preserve IDs, leading zeroes, case and native channel numbers exactly.
        cards.append({'card_id': card_id, 'name': name, 'native_channels': numbers})
    return {'authentication': 'cgi_accepted', 'badge_count': len(cards),
            'badge_event_correlation_verified': False,
            **({'badges': cards} if include_details is True else {})}
