"""QV account and own-installation push formats recovered from the vendor APKs.

No network I/O belongs here. QV account subscriptions are independent of the
legacy LT service and of the local intercom password. Values returned by these
helpers are private transport data and must never be included in diagnostics.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import UUID
from xml.etree import ElementTree as ET

MAX_RESPONSE_BYTES = 262144
MAX_DEVICES = 512
CALL_DETAIL = 1 << 18
AUTH_UP_PATH = '/auth/user;jus_duplex=up'
AUTH_DOWN_PATH = '/auth/user;jus_duplex=down'
OAUTH_PATH = '/qvoauthv2/token'
ALARM_PATH = '/UserAlarm'
PUSH_INFO_PATH = '/openapi-tdk/client/info'
PUSH_TOKEN_PATH = '/openapi-tdk/client/push/token'
ERROR_CODES = frozenset({
    'invalid_input', 'invalid_response', 'response_too_large', 'authentication_failed',
    'provider_rejected', 'region_redirect', 'unauthorized_device', 'invalid_channel',
    'connection_failed', 'request_timeout', 'closed', 'not_authenticated',
    'not_registered', 'invalid_service',
})
_IDENTIFIER = re.compile(r'[A-Za-z0-9_.:@-]{1,256}', re.ASCII)


class QvCloudError(ValueError):
    """Fixed public category; never a server response, URL or credential."""

    def __init__(self, code='invalid_response'):
        self.code = code if code in ERROR_CODES else 'invalid_response'
        super().__init__(self.code)


class QvRegionRedirect(QvCloudError):
    def __init__(self, region_id):
        self.region_id = _integer(region_id, 1, 65535)
        super().__init__('region_redirect')


@dataclass(frozen=True)
class QvCloudProfile:
    app_id: int
    oem_id: str
    auth_version: str = 'v1.13'


CONNECT3_PROFILE = QvCloudProfile(4123, 'G0123,A0058,G0058')
R002_PROFILE = QvCloudProfile(4058, 'A0058,G0058,G0079')


def profile_for(variant):
    """Choose the branded account namespace without cross-profile retries."""
    value = getattr(variant, 'value', variant)
    if value == 'connect3':
        return CONNECT3_PROFILE
    if value == 'connect2_r002':
        return R002_PROFILE
    raise QvCloudError('invalid_input')


@dataclass(frozen=True)
class QvCloudServices:
    auth_url: str = field(repr=False)
    alarm_url: str = field(repr=False)
    oauth_url: str = field(repr=False)
    openapi_url: str = field(repr=False)
    region_id: int = 0

    def __post_init__(self):
        for name in ('auth_url', 'alarm_url', 'oauth_url', 'openapi_url'):
            object.__setattr__(self, name, validate_service_url(
                getattr(self, name), alarm=name == 'alarm_url'))
        _integer(self.region_id, 0, 65535)


@dataclass(frozen=True)
class QvCloudAccount:
    session_id: str = field(repr=False)
    account_id: str = field(repr=False)


@dataclass(frozen=True)
class QvCloudDevice:
    uid: str = field(repr=False)
    name: str = field(repr=False)
    model: str = field(repr=False)
    channels: tuple[int, ...]
    shared: bool = False
    cloud_type: int = 0


@dataclass(frozen=True)
class QvOAuthTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _integer(value, minimum=0, maximum=2147483647):
    if type(value) is not int or not minimum <= value <= maximum:
        raise QvCloudError('invalid_input')
    return value


def _text(value, maximum=8192, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value)
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise QvCloudError('invalid_input')
    return value


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise QvCloudError('invalid_input')
    return value


def validate_service_url(value, *, alarm=False):
    """Only HTTPS vendor services; discovery cannot redirect secrets elsewhere."""
    try:
        parsed = urlsplit(_text(value, 1024))
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or not host.endswith('.qvcloud.net')
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or parsed.port not in ((None, 443, 4443) if alarm else (None, 443))):
            raise ValueError
    except (ValueError, TypeError):
        raise QvCloudError('invalid_service') from None
    port = ':4443' if parsed.port == 4443 else ''
    return f'https://{host}{port}/'


def make_client_id(installation_id, profile=CONNECT3_PROFILE):
    """A persisted random installation UUID, never a borrowed phone identity."""
    try:
        canonical = str(UUID(str(installation_id)))
    except (ValueError, TypeError, AttributeError):
        raise QvCloudError('invalid_input') from None
    return f'003-{profile.app_id}-{canonical}'


def password_hash(password):
    """Normal user passwords are SHA-256 UTF-8, lowercase hex."""
    return hashlib.sha256(_text(password, 1024).encode('utf-8')).hexdigest()


def _child(parent, name, value):
    node = ET.SubElement(parent, name)
    node.text = str(value)
    return node


def _envelope(command, session='', seq=0, *, client_id=None, profile=CONNECT3_PROFILE):
    root = ET.Element('envelope')
    header = ET.SubElement(root, 'header')
    _child(header, 'flag', 'tdkcloud')
    if client_id is not None:
        _child(header, 'version', profile.auth_version)
    _child(header, 'command', command)
    _child(header, 'seq', _integer(seq))
    _child(header, 'session', _text(session, 4096, empty=True))
    if client_id is not None:
        _child(header, 'user-data', '')
        client = ET.SubElement(header, 'client')
        for name, value in (('id', _identifier(client_id)), ('type', 3),
                            ('oem', profile.oem_id), ('app', profile.app_id)):
            _child(client, name, value)
    return root, ET.SubElement(root, 'content')


def _serialize(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def build_login_request(client_id, email, digest, region_id=0, *, seq=1,
                        profile=CONNECT3_PROFILE):
    root, content = _envelope('login', seq=seq, client_id=client_id, profile=profile)
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise QvCloudError('invalid_input')
    for name, value in (('account', _text(email, 320)), ('password', digest),
                        ('auth-type', 0), ('auth-code', ''),
                        ('ip-region-id', _integer(region_id, 0, 65535))):
        _child(content, name, value)
    return _serialize(root)


def build_device_list_request(client_id, session_id, *, seq=2,
                              profile=CONNECT3_PROFILE):
    root, content = _envelope('get-device-list', session_id, seq,
                              client_id=client_id, profile=profile)
    for name, value in (('filter', ''), ('order', 0), ('count', 0), ('page', 0),
                        ('owner', '')):
        _child(content, name, value)
    return _serialize(root)


def build_oauth_params(client_id, email, digest, region_id, profile=CONNECT3_PROFILE):
    # OAuth consumes the same encoded QvUser password as the XML login.
    build_login_request(client_id, email, digest, region_id, profile=profile)
    return {'grant_type': 'password', 'client_id': client_id, 'client_type': '3',
            'oemid': profile.oem_id, 'appid': str(profile.app_id), 'usr': email,
            'pwd': digest, 'region_id': str(region_id), 'client_flag': '1'}


def build_push_info(client_id, account_id, *, profile=CONNECT3_PROFILE,
                    language='en', timezone='GMT+00:00'):
    return {'clientId': _identifier(client_id), 'appId': str(profile.app_id),
            'oemId': profile.oem_id, 'userId': _identifier(account_id),
            'systemType': 'android', 'systemVersion': 'Home Assistant',
            'clientType': '3', 'appVersion': 'Home Assistant', 'voipEnable': '0',
            'language': _text(language, 16), 'timezone': _text(timezone, 32),
            'pushType': 'fcm'}


def build_push_token(client_id, token, profile=CONNECT3_PROFILE):
    return {'appId': str(profile.app_id), 'clientId': _identifier(client_id),
            'serverTypeToken': [{'serverType': 'fcm', 'pushToken': _text(token)}]}


def build_alarm_login(client_id, token, account, auth_url, *, profile=CONNECT3_PROFILE,
                       language='en', timezone='GMT+00:00'):
    root, content = _envelope('client-login', account.session_id)
    client = ET.SubElement(content, 'client')
    for name, value in (('id', _identifier(client_id)), ('token', _text(token)),
                        ('tokentype', 1), ('type', 3), ('oemid', profile.oem_id),
                        ('appid', profile.app_id), ('notifylang', _text(language, 16)),
                        ('timezone', _text(timezone, 32))):
        _child(client, name, value)
    owner = ET.SubElement(content, 'account')
    _child(owner, 'id', _identifier(account.account_id))
    _child(owner, 'authserver', urlsplit(validate_service_url(auth_url)).hostname)
    return _serialize(root)


def build_subscription_request(session_id, device, channel, enabled):
    if type(enabled) is not bool:
        raise QvCloudError('invalid_input')
    if type(channel) is not int or channel not in device.channels:
        raise QvCloudError('invalid_channel')
    # QV only. Compatibility clouds have different authorization/protocols.
    if device.cloud_type != 0:
        raise QvCloudError('unauthorized_device')
    root, content = _envelope('client-update-dev', session_id)
    # QvDevice.getDeviceCloudTypeInfo returns null for QV, so ifhsdev is omitted.
    for name, value in (('devid', _identifier(device.uid)),
                        ('chno', channel), ('ifshare', int(device.shared)),
                        ('ifpush', int(enabled)), ('detail', CALL_DETAIL if enabled else 0)):
        _child(content, name, value)
    return _serialize(root)


def parse_envelope(payload):
    if not isinstance(payload, (bytes, str)):
        raise QvCloudError('invalid_response')
    try:
        raw = payload.encode('utf-8') if isinstance(payload, str) else payload
        if len(raw) > MAX_RESPONSE_BYTES:
            raise QvCloudError('response_too_large')
        if b'<!' in raw or b'\x00' in raw:
            raise QvCloudError('invalid_response')
        root = ET.fromstring(raw)
        if root.tag != 'envelope' or len(root.findall('header')) != 1:
            raise QvCloudError('invalid_response')
        stack = [(root, 0)]
        total = 0
        while stack:
            node, depth = stack.pop()
            total += 1
            if depth > 16 or total > 12000:
                raise QvCloudError('invalid_response')
            stack.extend((child, depth + 1) for child in node)
        return root
    except QvCloudError:
        raise
    except (ET.ParseError, UnicodeError, ValueError):
        raise QvCloudError('invalid_response') from None


def response_sequence(payload):
    root = parse_envelope(payload)
    try:
        return _integer(int(root.findtext('header/seq', '-1')))
    except (ValueError, TypeError):
        raise QvCloudError('invalid_response') from None


def _result(root, *, authentication=False):
    try:
        result = int(root.findtext('header/result', ''))
    except (ValueError, TypeError):
        raise QvCloudError('invalid_response') from None
    if authentication and result == 100101000:
        try:
            raise QvRegionRedirect(int(root.findtext('content/redirect-region-id', '')))
        except (ValueError, TypeError) as err:
            if isinstance(err, QvRegionRedirect):
                raise
            raise QvCloudError('invalid_response') from None
    if result != 0:
        raise QvCloudError('authentication_failed' if authentication else 'provider_rejected')
    return root


def parse_login_response(payload):
    root = _result(parse_envelope(payload), authentication=True)
    try:
        return QvCloudAccount(_text(root.findtext('header/session/id'), 4096),
                              _identifier(root.findtext('content/account-id')))
    except QvCloudError:
        raise QvCloudError('invalid_response') from None


def parse_device_list(payload):
    root = _result(parse_envelope(payload))
    rows = root.findall('content/device')
    if len(rows) > MAX_DEVICES:
        raise QvCloudError('response_too_large')
    # The SDK can return both an owned and a shared row for the same device.
    # QvDeviceHelper.getDeviceList keeps the owned row in that case. Never merge
    # the channel grants of two shared records or invent grants for an empty one.
    rows_by_uid = {}
    try:
        for row in rows:
            uid = _identifier(row.findtext('id'))
            cloud_type = _integer(int(row.findtext('is-hs-device', '0')), 0, 32)
            shared = _integer(int(row.findtext('from-share', '0'))) > 0
            count = _integer(int(row.findtext('channel-num', '1')), 1, 256)
            auth_channels = row.find('auth-chans')
            if auth_channels is not None:
                channels = tuple(_integer(int(item.findtext('channel', '-1')), 0, 255)
                                 for item in auth_channels.findall('item'))
            else:
                channels = tuple(range(count))
            if len(channels) != len(set(channels)):
                raise QvCloudError('invalid_response')
            device = QvCloudDevice(uid, _text(row.findtext('name', ''), 256, empty=True),
                                   _text(row.findtext('model', ''), 128, empty=True),
                                   channels, shared, cloud_type)
            rows_by_uid.setdefault(uid, []).append(device)
        devices = []
        for copies in rows_by_uid.values():
            if len({device.cloud_type for device in copies}) != 1:
                raise QvCloudError('invalid_response')
            # Match the SDK's two-pass precedence even if several shared copies
            # arrive before the owned record. No shared grant augments ownership.
            owners = [device for device in copies if not device.shared]
            authorized = owners or copies
            selected = authorized[0]
            if any(device != selected for device in authorized[1:]):
                raise QvCloudError('invalid_response')
            devices.append(selected)
    except (TypeError, ValueError):
        raise QvCloudError('invalid_response') from None
    # An explicit empty auth-chans is not authorization for channel 0 (or all
    # channels). Exclude that device without hiding other usable account rows.
    return tuple(device for device in devices if device.channels)


def validate_alarm_response(payload):
    _result(parse_envelope(payload))


def parse_json(payload):
    try:
        if not isinstance(payload, (str, bytes)) or len(payload) > MAX_RESPONSE_BYTES:
            raise QvCloudError('response_too_large')
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except QvCloudError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise QvCloudError('invalid_response') from None


def parse_oauth_response(payload):
    value = parse_json(payload)
    try:
        return QvOAuthTokens(_text(value.get('access_token')),
                             _text(value.get('refresh_token')))
    except QvCloudError:
        raise QvCloudError('authentication_failed') from None


def validate_openapi_response(payload):
    value = parse_json(payload)
    if type(value.get('code')) is not int:
        raise QvCloudError('invalid_response')
    if value['code'] != 0:
        raise QvCloudError('provider_rejected')
