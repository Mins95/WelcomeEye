"""Pure LT cloud request and notification formats observed in the vendor APK.

This module performs no I/O. The accountless push2u API and FCM delivery are
separate from the local GLNK session. A successful subscription is not a ring.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import base64
import binascii
import hashlib
import json
import re


ALARM_SET_URL = 'https://tdpush.push2u.com/pda_more_api.php'
ALARM_GET_URL = 'https://tdpush.push2u.com/pushCheck.php'
ALARM_SET_URL_LEGACY = 'https://lbs.push2u.com/pda_more_api.php'
ALARM_GET_URL_LEGACY = 'https://lbs.push2u.com/pushCheck.php'
PUSH_TAG = 'philips'
FCM_APP_KEY = 'tiandi.json'
COMPANY_ID = 'LT4a7a46c756098'
APP_ID = 4000
CLIENT_TYPE = 3
MAX_ENCODED_BYTES = 65536
MAX_JSON_BYTES = 49152
MAX_TOKEN_LENGTH = 8192
MAX_NOTIFICATION_LENGTH = 4096
MAX_STATUS_ROWS = 256

# Public protocol constant in LtEncrypt.java, not a device/user credential.
_RC4_KEY = b'AliGoolinkCloud'
_IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]+', re.ASCII)
_DECIMAL = re.compile(r'[0-9]+', re.ASCII)
_ERROR_CODES = frozenset({
    'invalid_input', 'payload_too_large', 'invalid_encoding', 'invalid_json',
    'invalid_response', 'provider_rejected', 'invalid_notification',
})


class CloudProtocolError(ValueError):
    """A fixed diagnostic category, never a provider body or private value."""

    def __init__(self, code='invalid_response'):
        self.code = code if code in _ERROR_CODES else 'invalid_response'
        super().__init__(self.code)


@dataclass(frozen=True)
class CloudRingNotification:
    """Validated own-device ring; device identity and display text are discarded.

    The APK provides local-looking wall time without a timezone. It must not be
    interpreted as UTC or used alone to prove freshness. dedup_key is internal,
    opaque correlation metadata, not intended for entity attributes/diagnostics.
    """

    channel: int
    occurred_at: datetime = field(repr=False)
    dedup_key: str = field(repr=False)

    @property
    def timestamp(self):
        value = self.occurred_at
        return (f'{value.year:04d}{value.month:02d}{value.day:02d}'
                f'{value.hour:02d}{value.minute:02d}{value.second:02d}')


def _identifier(value, maximum=160):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not _IDENTIFIER.fullmatch(value):
        raise CloudProtocolError('invalid_input')
    return value


def _token(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_TOKEN_LENGTH
            or not value.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise CloudProtocolError('invalid_input')
    return value


def make_client_id(instance_id):
    """Use a fresh persisted installation ID, never the phone's ID/token."""
    return f'00{CLIENT_TYPE}-{APP_ID}-{_identifier(instance_id, 128)}'


def installation_account(client_id):
    """New accountless installation; do not emulate a migrated phone account."""
    return f'N_{PUSH_TAG}/{_identifier(client_id)}'


def provider_urls(encrypted=True):
    """Select once from known device metadata; no cross-provider fallback.

    QvDevice.isLtEncryptDevice defaults true when capability data is absent.
    """
    if type(encrypted) is not bool:
        raise CloudProtocolError('invalid_input')
    if encrypted:
        return ALARM_SET_URL, ALARM_GET_URL
    return ALARM_SET_URL_LEGACY, ALARM_GET_URL_LEGACY


def build_subscription_request(client_id, token, uid, enabled):
    """Build the APK's own-installation registration or zero-mask disable."""
    if type(enabled) is not bool:
        raise CloudProtocolError('invalid_input')
    account = installation_account(client_id)
    token, uid = _token(token), _identifier(uid, 128)
    return {
        'account': account,
        'act_type': '3',
        'app_token_list': {'fcm': {
            'app_key': FCM_APP_KEY, 'app_token': token, 'push_platform': '4',
        }},
        'dev_list': [{
            'alarm_type': '14,26' if enabled else '',
            'channel_no': -1, 'channel_vir_no': -1, 'company_id': COMPANY_ID,
            'gid': uid, 'gid_name': 'Home Assistant', 'push_mode': 0,
            'switch_state': '1' if enabled else '0',
        }],
        'log_sys_id': PUSH_TAG, 'phone_imei': client_id,
        'phone_lang': '2', 'phone_type': '2', 'push_mode': 1,
    }


def build_check_request(client_id, uid):
    return {'account': installation_account(client_id), 'action_flag': 2, 'gid': _identifier(uid, 128)}


def _check_structure(value):
    """Bound JSON traversal before encoding or accepting decoded structures."""
    pending = [(value, 0)]
    count = 0
    text_size = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 2048 or depth > 8:
            raise CloudProtocolError('payload_too_large')
        if isinstance(item, dict):
            if len(item) > 64:
                raise CloudProtocolError('payload_too_large')
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise CloudProtocolError('invalid_input')
                text_size += len(key)
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_STATUS_ROWS:
                raise CloudProtocolError('payload_too_large')
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > MAX_TOKEN_LENGTH:
                raise CloudProtocolError('payload_too_large')
            text_size += len(item)
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int and -(2**63) <= item < 2**63:
            pass
        else:
            raise CloudProtocolError('invalid_input')
        if text_size > MAX_JSON_BYTES:
            raise CloudProtocolError('payload_too_large')


def _rc4(data, key):
    """Standard RC4 KSA/PRGA, reset for each message (no drop/IV)."""
    if not key:
        raise CloudProtocolError('invalid_input')
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 255
        state[i], state[j] = state[j], state[i]
    out = bytearray()
    i = j = 0
    for value in data:
        i = (i + 1) & 255
        j = (j + state[i]) & 255
        state[i], state[j] = state[j], state[i]
        out.append(value ^ state[(state[i] + state[j]) & 255])
    return bytes(out)


def encode_payload(payload):
    """Return the text/plain request body without retaining/logging plaintext."""
    if not isinstance(payload, dict):
        raise CloudProtocolError('invalid_input')
    _check_structure(payload)
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        for char in "<>&='\u2028\u2029":
            text = text.replace(char, '\\u%04x' % ord(char))
        raw = text.encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CloudProtocolError('invalid_input') from None
    if len(raw) > MAX_JSON_BYTES:
        raise CloudProtocolError('payload_too_large')
    return base64.b64encode(_rc4(raw, _RC4_KEY)).decode('ascii')


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CloudProtocolError('invalid_json')
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise CloudProtocolError('invalid_json')


def decode_payload(body):
    """Decode a bounded encrypted provider JSON object, with safe failures."""
    if not isinstance(body, (str, bytes)):
        raise CloudProtocolError('invalid_encoding')
    if len(body) > MAX_ENCODED_BYTES:
        raise CloudProtocolError('payload_too_large')
    try:
        text = body.decode('ascii') if isinstance(body, bytes) else body
        if not text.isascii():
            raise ValueError
        compact = ''.join(text.split())
        if not compact:
            raise ValueError
        raw = base64.b64decode(compact, validate=True)
    except (ValueError, UnicodeError, binascii.Error):
        raise CloudProtocolError('invalid_encoding') from None
    if len(raw) > MAX_JSON_BYTES:
        raise CloudProtocolError('payload_too_large')
    try:
        value = json.loads(_rc4(raw, _RC4_KEY).decode('utf-8'),
                           object_pairs_hook=_json_object, parse_constant=_invalid_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise CloudProtocolError('invalid_json') from None
    if not isinstance(value, dict):
        raise CloudProtocolError('invalid_response')
    _check_structure(value)
    return value


def validate_subscription_response(response):
    """Accept only the observed success value; other codes are not guessed."""
    if not isinstance(response, dict):
        raise CloudProtocolError('invalid_response')
    _check_structure(response)
    result = response.get('re')
    if result is None or type(result) not in (str, int):
        raise CloudProtocolError('invalid_response')
    # Gson coerces an integral JSON number to the declared String property.
    if result != '1' and not (type(result) is int and result == 1):
        raise CloudProtocolError('provider_rejected')


def check_subscription_response(response, token):
    """Return only own-token presence; never expose other clients' tokens."""
    token = _token(token)
    validate_subscription_response(response)
    rows = response.get('data')
    if rows is None:
        return False
    if not isinstance(rows, list) or len(rows) > MAX_STATUS_ROWS:
        raise CloudProtocolError('invalid_response')
    present = False
    for row in rows:
        if not isinstance(row, dict):
            raise CloudProtocolError('invalid_response')
        candidate = row.get('app_token')
        if candidate is not None and (not isinstance(candidate, str) or len(candidate) > MAX_TOKEN_LENGTH):
            raise CloudProtocolError('invalid_response')
        present = present or bool(candidate) and candidate == token
    return present


def parse_ring_notification(data, expected_uid):
    """Parse the FCM data mapping for an exact own-UID LT CALL (event 14).

    Other families/UIDs/events return None. A malformed candidate raises a fixed
    CloudProtocolError. Display text and account/token identifiers are discarded.
    """
    expected_uid = _identifier(expected_uid, 128)
    if not isinstance(data, Mapping) or len(data) > 64:
        raise CloudProtocolError('invalid_notification')
    if data.get('CameraId') or data.get('SrcAccountId') or 'message_content' not in data:
        return None
    content = data.get('message_content')
    if (not isinstance(content, str) or not content
            or len(content) > MAX_NOTIFICATION_LENGTH
            or any(ord(char) < 32 for char in content)):
        raise CloudProtocolError('invalid_notification')
    # Only the first five fields carry required identity/event metadata. The APK
    # treats the sixth as optional display text; never persist or return it.
    parts = content.split('|', 5)
    if len(parts) < 5 or len(parts[0]) > 128:
        raise CloudProtocolError('invalid_notification')
    if parts[1] != expected_uid:
        return None
    if parts[3] != '14':
        return None
    channel_text, timestamp = parts[2], parts[4]
    if (not 1 <= len(channel_text) <= 3 or not _DECIMAL.fullmatch(channel_text)
            or len(timestamp) != 14 or not _DECIMAL.fullmatch(timestamp)):
        raise CloudProtocolError('invalid_notification')
    channel = int(channel_text)
    if channel > 255:
        raise CloudProtocolError('invalid_notification')
    try:
        occurred_at = datetime(int(timestamp[:4]), int(timestamp[4:6]), int(timestamp[6:8]),
                               int(timestamp[8:10]), int(timestamp[10:12]), int(timestamp[12:14]))
    except ValueError:
        raise CloudProtocolError('invalid_notification') from None
    # Ignore display text so renaming a device cannot defeat deduplication.
    digest = hashlib.sha256(f'{expected_uid}|{channel}|14|{timestamp}'.encode('ascii')).hexdigest()
    return CloudRingNotification(channel + 1, occurred_at, digest)
