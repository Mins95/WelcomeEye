"""Bounded, private FCM receiver for the optional V1 doorbell subscription.

Only the protobuf definitions/constants from firebase-messaging are reused.
Its network client logs server bodies and identifiers, so this adapter owns
HTTP/MCS I/O and never logs payloads or exceptions. Google accepting the APK's
Android app ID for this independent web registration remains experimental.
"""
from __future__ import annotations

import asyncio
import base64
from collections import deque
from copy import deepcopy
import inspect
import json
import secrets
import ssl
import time
import uuid

from aiohttp import ClientTimeout
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from firebase_messaging.const import (
    AUTH_VERSION, FCM_INSTALLATION, FCM_REGISTRATION, FCM_SEND_URL,
    GCM_CHECKIN_URL, GCM_REGISTER_URL, GCM_SERVER_KEY_B64, MCS_HOST,
    MCS_PORT, MCS_VERSION, SDK_VERSION,
)
from firebase_messaging.proto.android_checkin_pb2 import (
    DEVICE_CHROME_BROWSER, ChromeBuildProto,
)
from firebase_messaging.proto.checkin_pb2 import (
    AndroidCheckinRequest, AndroidCheckinResponse,
)
from firebase_messaging.proto.mcs_pb2 import (
    Close, DataMessageStanza, HeartbeatAck, HeartbeatPing, IqStanza,
    LoginRequest, LoginResponse, SelectiveAck, StreamErrorStanza,
)
from http_ece import decrypt

from .snapshot import _finish_task

# Public app configuration, APK res/values/strings.xml lines 93, 97, 98, 1145.
PROJECT_ID = 'tdkvdp'
APP_ID = '1:1004874260689:android:74d7f81b899910c7'
API_KEY = 'AIzaSyDJi1uweWGBp_094ci3wMc7Kj2KH1SNWgE'
SENDER_ID = '1004874260689'
CHROME_VERSION = '94.0.4606.51'
BUNDLE_ID = 'welcomeeye.homeassistant'
MAX_BYTES = 65536
MAX_PERSISTENT_IDS = 128
MAX_ID_LENGTH = 256
HTTP_TIMEOUT = 12.0
REGISTER_TIMEOUT = 55.0
CONNECT_TIMEOUT = 15.0
FRAME_TIMEOUT = 12.0
LOGIN_TIMEOUT = 15.0
HEARTBEAT_INTERVAL = 60.0
HEARTBEAT_TIMEOUT = 15.0
CLOSE_TIMEOUT = 3.0
CALLBACK_TIMEOUT = 10.0
MAX_TIMESTAMP = (1 << 63) - 1
TIMESTAMP_SOURCES = frozenset({'none', 'google_sent_time', 'mcs_sent_milliseconds'})
TIMESTAMP_STATUSES = frozenset({'unobserved', 'valid', 'invalid', 'conflicting', 'missing'})

_TAGS = {0: HeartbeatPing, 1: HeartbeatAck, 3: LoginResponse, 4: Close,
         7: IqStanza, 8: DataMessageStanza, 10: StreamErrorStanza}
ERROR_REASONS = frozenset({
    'invalid_response', 'invalid_payload', 'closed', 'http_rejected',
    'response_too_large', 'invalid_checkin', 'identity_changed',
    'invalid_credentials', 'registration_rejected', 'already_running',
    'registration_failed', 'not_connected', 'frame_too_large',
    'protocol_version', 'frame_length', 'partial_frame_timeout',
    'unsupported_encoding', 'login_rejected', 'heartbeat_timeout',
    'server_closed', 'connection_failed', 'http_timeout',
    'connect_timeout', 'login_timeout', 'storage_failed', 'registration_timeout',
})


class FCMError(Exception):
    """An error whose message contains only a fixed local category."""

    def __init__(self, reason):
        self.reason = reason if reason in ERROR_REASONS else 'connection_failed'
        super().__init__(self.reason)


def _text(value, maximum=16384):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise FCMError('invalid_response')
    return value


def _unb64(value):
    value = _text(value)
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_',
                            validate=True)


def _b64(value):
    return base64.urlsafe_b64encode(value).decode('ascii')


def _varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _header_parameter(value, name):
    matches = []
    for part in value.split(';'):
        key, separator, data = part.strip().partition('=')
        if separator and key.strip().lower() == name:
            matches.append(data.strip().strip('"'))
    if len(matches) != 1:
        raise FCMError('invalid_payload')
    return _unb64(matches[0])


def _positive_timestamp(value):
    """Parse a positive int64 without accepting booleans/floats or coercion."""
    if isinstance(value, str):
        if not 1 <= len(value) <= 19 or not value.isascii() or not value.isdecimal():
            return None
        value = int(value)
    if type(value) is not int or not 0 < value <= MAX_TIMESTAMP:
        return None
    return value


def _unique_json_object(pairs):
    """Do not let duplicate envelope fields choose an arbitrary timestamp."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise FCMError('invalid_payload')
        result[key] = value
    return result


class FCMReceiver:
    """One registration/connection; the integration owns retries and storage."""

    def __init__(self, session, on_notification, on_credentials, credentials=None,
                 persistent_ids=None, on_state=None):
        self._session = session
        self._on_notification = on_notification
        self._on_credentials = on_credentials
        self._on_state = on_state
        self._credentials = deepcopy(credentials)
        self._connected = self._closed = False
        self._register_task = self._run_task = self._close_task = None
        self._reader = self._writer = self._ssl = None
        self._first_incoming = True
        self._stream_id = 0
        self._seen = deque(maxlen=MAX_PERSISTENT_IDS)
        for item in (persistent_ids or [])[-MAX_PERSISTENT_IDS:]:
            if isinstance(item, str) and 0 < len(item) <= MAX_ID_LENGTH:
                self._seen.append(item)
        self._diag = {'registrations': 0, 'checkins': 0, 'messages': 0,
                      'delivered': 0, 'duplicates': 0, 'malformed': 0,
                      'ignored': 0, 'callback_errors': 0, 'acknowledged': 0,
                      'last_error': None, 'http_stage': None, 'http_status': None}
        self._diag.update(timestamp_source='none', timestamp_status='unobserved',
                          mcs_sent_present=False, mcs_sent_digits=0,
                          google_sent_time_present=False, google_sent_time_valid=False)

    @property
    def credentials(self):
        return deepcopy(self._credentials)

    @property
    def connected(self):
        return self._connected and not self._closed

    def diagnostics(self):
        return {**self._diag, 'connected': self.connected, 'closed': self._closed}

    def _ensure_open(self):
        if self._closed:
            raise FCMError('closed')

    async def _callback(self, callback, *args):
        self._ensure_open()
        result = callback(*args)
        if inspect.isawaitable(result):
            async with asyncio.timeout(CALLBACK_TIMEOUT):
                await result
        self._ensure_open()

    async def _state(self, connected):
        changed = connected != self._connected
        self._connected = connected
        if changed and self._on_state and not self._closed:
            try:
                await self._callback(self._on_state)
            except Exception:
                self._diag['callback_errors'] += 1

    async def _tls_context(self):
        if self._ssl is None:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                self._ssl = await asyncio.to_thread(ssl.create_default_context)
        self._ensure_open()
        return self._ssl

    async def _post(self, url, *, headers=None, data=None, json_body=None):
        self._ensure_open()
        context = await self._tls_context()
        # URLs are private implementation constants, never diagnostic values.
        self._diag['http_stage'] = {
            GCM_CHECKIN_URL: 'checkin', GCM_REGISTER_URL: 'gcm_registration',
            FCM_INSTALLATION + f'projects/{PROJECT_ID}/installations': 'installation',
            FCM_REGISTRATION + f'projects/{PROJECT_ID}/registrations': 'fcm_registration',
        }.get(url, 'unknown')
        self._diag['http_status'] = None
        async with asyncio.timeout(HTTP_TIMEOUT):
            async with self._session.post(
                url, headers=headers, data=data, json=json_body,
                allow_redirects=False, ssl=context,
                timeout=ClientTimeout(total=HTTP_TIMEOUT),
            ) as response:
                self._diag['http_status'] = response.status
                if response.status != 200:
                    # Never read/log an error body, URL with credentials or repr.
                    raise FCMError('http_rejected')
                body = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FCMError('response_too_large')
        self._ensure_open()
        return bytes(body)

    async def _checkin(self, previous=None):
        request = AndroidCheckinRequest(version=3, user_serial_number=0)
        request.checkin.type = DEVICE_CHROME_BROWSER
        chrome = request.checkin.chrome_build
        chrome.platform = ChromeBuildProto.PLATFORM_LINUX
        chrome.chrome_version = CHROME_VERSION
        chrome.channel = ChromeBuildProto.CHANNEL_STABLE
        if previous is not None:
            identity = int(previous['android_id'])
            # Response is fixed64; the request field is signed int64.
            request.id = identity if identity < 2**63 else identity - 2**64
            request.security_token = int(previous['security_token'])
        body = await self._post(GCM_CHECKIN_URL,
            headers={'Content-Type': 'application/x-protobuf'},
            data=request.SerializeToString())
        response = AndroidCheckinResponse.FromString(body)
        if not response.IsInitialized() or not response.android_id or not response.security_token:
            raise FCMError('invalid_checkin')
        if previous and str(response.android_id) != previous['android_id']:
            # Never silently enroll another installation after a saved failure.
            raise FCMError('identity_changed')
        self._diag['checkins'] += 1
        return {'android_id': str(response.android_id),
                'security_token': str(response.security_token)}

    @staticmethod
    def _keys():
        key = ec.generate_private_key(ec.SECP256R1())
        return {
            'private': _b64(key.private_bytes(serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
            'public': _b64(key.public_key().public_bytes(serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint)),
            'secret': _b64(secrets.token_bytes(16)),
        }

    def _validate_credentials(self):
        credentials = self._credentials
        gcm = credentials['gcm']
        for key in ('android_id', 'security_token'):
            value = _text(gcm[key], 20)
            if not value.isdecimal() or not 0 < int(value) < 2**64:
                raise FCMError('invalid_credentials')
        _text(gcm['app_id'], 256)
        _text(credentials['fcm']['registration']['token'])
        keys = credentials['keys']
        key = serialization.load_der_private_key(_unb64(keys['private']), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise FCMError('invalid_credentials')
        if len(_unb64(keys['secret'])) != 16:
            raise FCMError('invalid_credentials')

    async def _enroll(self):
        keys = self._keys()
        gcm = await self._checkin()
        gcm['app_id'] = f'wp:{BUNDLE_ID}#{uuid.uuid4()}'
        result = await self._post(GCM_REGISTER_URL,
            headers={'Authorization': f"AidLogin {gcm['android_id']}:{gcm['security_token']}",
                     'Content-Type': 'application/x-www-form-urlencoded'},
            data={'app': 'org.chromium.linux', 'X-subtype': gcm['app_id'],
                  'device': gcm['android_id'], 'sender': GCM_SERVER_KEY_B64})
        label, separator, token = result.decode('utf-8').strip().partition('=')
        if label != 'token' or not separator:
            raise FCMError('registration_rejected')
        gcm['token'] = _text(token)
        fid = bytearray(secrets.token_bytes(17))
        fid[0] = 0x70 | (fid[0] & 0x0f)
        installation = json.loads(await self._post(
            FCM_INSTALLATION + f'projects/{PROJECT_ID}/installations',
            headers={'x-goog-api-key': API_KEY},
            json_body={'appId': APP_ID, 'authVersion': AUTH_VERSION,
                       'fid': _b64(fid)[:22], 'sdkVersion': SDK_VERSION}))
        auth = _text(installation['authToken']['token'])
        stored_installation = {
            'token': auth, 'refresh_token': _text(installation['refreshToken']),
            'fid': _text(installation['fid'], 128),
            'expires_in': int(installation['authToken']['expiresIn'].removesuffix('s')),
            'created_at': time.time(),
        }
        registration = json.loads(await self._post(
            FCM_REGISTRATION + f'projects/{PROJECT_ID}/registrations',
            headers={'x-goog-api-key': API_KEY, 'x-goog-firebase-installations-auth': auth},
            json_body={'web': {'applicationPubKey': None, 'auth': keys['secret'],
                              'endpoint': FCM_SEND_URL + gcm['token'],
                              'p256dh': keys['public']}}))
        token = _text(registration['token'])
        self._diag['registrations'] += 1
        return {'keys': keys, 'gcm': gcm,
                'fcm': {'registration': {'token': token}, 'installation': stored_installation},
                'config': {'bundle_id': BUNDLE_ID, 'project_id': PROJECT_ID}}

    async def register(self):
        """Check in or enroll once; await durable credentials before returning."""
        self._ensure_open()
        if self._register_task or self._run_task:
            raise FCMError('already_running')
        self._register_task = asyncio.current_task()
        try:
            async with asyncio.timeout(REGISTER_TIMEOUT):
                if self._credentials is not None:
                    self._validate_credentials()
                    checked = await self._checkin(self._credentials['gcm'])
                    self._credentials['gcm'].update(checked)
                else:
                    self._credentials = await self._enroll()
                self._validate_credentials()
                try:
                    await self._callback(self._on_credentials, self.credentials)
                except Exception:
                    raise FCMError('storage_failed') from None
                self._diag['last_error'] = None
                return self._credentials['fcm']['registration']['token']
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = (exc.reason if isinstance(exc, FCMError)
                      else 'registration_timeout' if isinstance(exc, TimeoutError)
                      else 'registration_failed')
            self._diag['last_error'] = reason
            raise FCMError(reason) from None
        finally:
            self._register_task = None

    async def _send(self, tag, message, *, first=False):
        self._ensure_open()
        if self._writer is None:
            raise FCMError('not_connected')
        payload = message.SerializeToString()
        if len(payload) > MAX_BYTES:
            raise FCMError('frame_too_large')
        prefix = bytes((MCS_VERSION, tag)) if first else bytes((tag,))
        self._writer.write(prefix + _varint(len(payload)) + payload)
        async with asyncio.timeout(FRAME_TIMEOUT):
            await self._writer.drain()

    async def _read_frame(self, idle_timeout):
        # A timeout before consuming a tag is safe for a heartbeat. A partial
        # frame timeout closes this connection, never resumes a misaligned frame.
        async with asyncio.timeout(idle_timeout):
            first = (await self._reader.readexactly(1))[0]
        try:
            async with asyncio.timeout(FRAME_TIMEOUT):
                if self._first_incoming:
                    if first not in (38, MCS_VERSION):
                        raise FCMError('protocol_version')
                    first = (await self._reader.readexactly(1))[0]
                    self._first_incoming = False
                size = 0
                for index in range(5):
                    value = (await self._reader.readexactly(1))[0]
                    size |= (value & 127) << (index * 7)
                    if not value & 128:
                        break
                else:
                    raise FCMError('frame_length')
                if size > MAX_BYTES:
                    raise FCMError('frame_too_large')
                payload = await self._reader.readexactly(size)
        except TimeoutError:
            raise FCMError('partial_frame_timeout') from None
        self._stream_id += 1
        return first, payload

    def _login(self):
        gcm = self._credentials['gcm']
        request = LoginRequest(id=CHROME_VERSION, domain='mcs.android.com',
            user=gcm['android_id'], resource=gcm['android_id'],
            auth_token=gcm['security_token'], device_id=f"android-{int(gcm['android_id']):x}",
            auth_service=LoginRequest.ANDROID_ID, adaptive_heartbeat=False,
            use_rmq2=True, network_type=1)
        request.setting.add(name='new_vc', value='1')
        request.received_persistent_id.extend(self._seen)
        return request

    async def _ack(self, persistent_id):
        message = IqStanza(type=IqStanza.SET, id='',
                           last_stream_id_received=self._stream_id)
        if persistent_id:
            message.extension.id = 12
            message.extension.data = SelectiveAck(id=[persistent_id]).SerializeToString()
        else:
            message.extension.id = 13
            message.extension.data = b''
        await self._send(7, message)
        self._diag['acknowledged'] += 1

    def _timestamp(self, message, google_values):
        """Normalize downstream transport metadata to positive Unix milliseconds.

        Android RemoteMessage.getSentTime() reads google.sent_time in Unix ms.
        microG McsService.handleAppMessage forwards downstream msg.sent into
        that field unchanged. The seconds comment in mcs.proto describes a
        client-sent message; applying it to incoming notifications multiplied
        their epoch-ms value by 1000 and incorrectly rejected them as future.
        Never guess units by digit count, use reception time, or use LT's
        timezone-less date to make an otherwise rejected message fresh.
        """
        self._diag['google_sent_time_present'] = bool(google_values)
        mcs_present = message.HasField('sent')
        mcs_value = _positive_timestamp(message.sent) if mcs_present else None
        if google_values:
            self._diag['timestamp_source'] = 'google_sent_time'
            values = [_positive_timestamp(value) for value in google_values]
            if any(value is None for value in values):
                self._diag['timestamp_status'] = 'invalid'
                return 0
            if len(set(values)) != 1:
                self._diag['timestamp_status'] = 'conflicting'
                return 0
            self._diag['google_sent_time_valid'] = True
            # Conservative agreement check: do not select a fresh timestamp
            # over another documented field which is invalid or older. Keep
            # the rejection observable instead of inventing a tolerance.
            if mcs_present and mcs_value is None:
                self._diag['timestamp_status'] = 'invalid'
                return 0
            if mcs_present and mcs_value != values[0]:
                self._diag['timestamp_status'] = 'conflicting'
                return 0
            self._diag['timestamp_status'] = 'valid'
            return values[0]
        if not mcs_present:
            self._diag['timestamp_status'] = 'missing'
            return 0
        self._diag['timestamp_source'] = 'mcs_sent_milliseconds'
        if mcs_value is None:
            self._diag['timestamp_status'] = 'invalid'
            return 0
        self._diag['timestamp_status'] = 'valid'
        return mcs_value

    def _payload(self, message):
        self._diag.update(timestamp_source='none', timestamp_status='unobserved',
                          mcs_sent_present=message.HasField('sent'),
                          mcs_sent_digits=(len(str(abs(message.sent)))
                                           if message.HasField('sent') else 0),
                          google_sent_time_present=False, google_sent_time_valid=False)
        # Plain Android data needs the project's sender ID. Encrypted web data
        # instead binds to our private key/auth secret AND exact GCM subtype;
        # its outer MCS sender can be Google's web-push routing identity.
        if not message.raw_data and getattr(message, 'from') != SENDER_ID:
            return None
        if len(message.app_data) > 64:
            raise FCMError('invalid_payload')
        data = {}
        for item in message.app_data:
            if item.key in data or len(item.key) > 128 or len(item.value) > 16384:
                raise FCMError('invalid_payload')
            data[item.key] = item.value
        google_values = ([data['google.sent_time']] if 'google.sent_time' in data else [])
        if data.get('message_type') == 'deleted_messages':
            return None
        subtype = data.get('subtype')
        if message.raw_data and subtype != self._credentials['gcm']['app_id']:
            return None
        if subtype is not None and subtype != self._credentials['gcm']['app_id']:
            return None
        if message.raw_data:
            keys = self._credentials['keys']
            private = serialization.load_der_private_key(_unb64(keys['private']), password=None)
            encoding = data.get('content-encoding', 'aesgcm')
            if encoding == 'aesgcm':
                clear = decrypt(message.raw_data, private_key=private,
                    dh=_header_parameter(data['crypto-key'], 'dh'),
                    salt=_header_parameter(data['encryption'], 'salt'),
                    auth_secret=_unb64(keys['secret']), version='aesgcm')
            elif encoding == 'aes128gcm':
                clear = decrypt(message.raw_data, private_key=private,
                    auth_secret=_unb64(keys['secret']), version='aes128gcm')
            else:
                raise FCMError('unsupported_encoding')
            if len(clear) > MAX_BYTES:
                raise FCMError('invalid_payload')
            data = json.loads(clear, object_pairs_hook=_unique_json_object)
            if not isinstance(data, dict):
                raise FCMError('invalid_payload')
            # Reserved envelope metadata only: data[...] is developer payload
            # and must never be allowed to manufacture a transport timestamp.
            if 'google.sent_time' in data:
                google_values.append(data['google.sent_time'])
            if 'data' in data:
                data = data['data']
        if not isinstance(data, dict):
            raise FCMError('invalid_payload')
        if 'message_content' not in data:
            return None
        # Notification titles/bodies never become a doorbell event.
        return ({'message_content': _text(data['message_content'])},
                self._timestamp(message, google_values))

    async def _data_message(self, message):
        self._diag['messages'] += 1
        persistent_id = message.persistent_id
        if len(persistent_id) > MAX_ID_LENGTH:
            persistent_id = ''
        try:
            if persistent_id and persistent_id in self._seen:
                self._diag['duplicates'] += 1
            else:
                decoded = self._payload(message)
                if decoded is None:
                    self._diag['ignored'] += 1
                elif not self._closed:
                    try:
                        data, sent_ms = decoded
                        await self._callback(self._on_notification, data,
                                             persistent_id, sent_ms)
                        self._diag['delivered'] += 1
                    except Exception:
                        self._diag['callback_errors'] += 1
        except Exception:
            self._diag['malformed'] += 1
        if not self._closed:
            # Invalid/deleted/duplicate payloads must not poison reconnects.
            await self._ack(persistent_id)
            if persistent_id and persistent_id not in self._seen:
                self._seen.append(persistent_id)

    async def run(self):
        """Receive until disconnect/stop. No background retry or orphan worker."""
        self._ensure_open()
        if self._run_task or self._register_task:
            raise FCMError('already_running')
        self._run_task = asyncio.current_task()
        try:
            self._validate_credentials()
            context = await self._tls_context()
            try:
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    self._reader, self._writer = await asyncio.open_connection(
                        MCS_HOST, MCS_PORT, ssl=context, server_hostname=MCS_HOST,
                        ssl_handshake_timeout=CONNECT_TIMEOUT, ssl_shutdown_timeout=CLOSE_TIMEOUT,
                        limit=MAX_BYTES)
            except TimeoutError:
                raise FCMError('connect_timeout') from None
            self._ensure_open()
            self._first_incoming = True
            self._stream_id = 0
            try:
                async with asyncio.timeout(LOGIN_TIMEOUT):
                    await self._send(2, self._login(), first=True)
                    tag, payload = await self._read_frame(LOGIN_TIMEOUT)
                    if tag != 3:
                        raise FCMError('login_rejected')
                    response = LoginResponse.FromString(payload)
                    if not response.IsInitialized() or response.HasField('error'):
                        raise FCMError('login_rejected')
            except TimeoutError:
                raise FCMError('login_timeout') from None
            self._diag['last_error'] = None
            await self._state(True)
            loop = asyncio.get_running_loop()
            next_ping = loop.time() + HEARTBEAT_INTERVAL
            awaiting_ack = False
            while not self._closed:
                if awaiting_ack and loop.time() >= next_ping:
                    raise FCMError('heartbeat_timeout')
                try:
                    tag, payload = await self._read_frame(max(0.001, next_ping - loop.time()))
                except TimeoutError:
                    if awaiting_ack:
                        raise FCMError('heartbeat_timeout') from None
                    await self._send(0, HeartbeatPing(last_stream_id_received=self._stream_id))
                    awaiting_ack = True
                    next_ping = loop.time() + HEARTBEAT_TIMEOUT
                    continue
                if tag in (4, 10):
                    raise FCMError('server_closed')
                message_type = _TAGS.get(tag)
                if message_type is None:
                    self._diag['ignored'] += 1
                    continue
                try:
                    message = message_type.FromString(payload)
                    if not message.IsInitialized():
                        raise ValueError()
                except Exception:
                    self._diag['malformed'] += 1
                    if tag == 8:
                        await self._ack('')
                    continue
                if tag == 8:
                    await self._data_message(message)
                elif tag == 0:
                    await self._send(1, HeartbeatAck(last_stream_id_received=self._stream_id))
                elif tag == 1:
                    awaiting_ack = False
                    next_ping = loop.time() + HEARTBEAT_INTERVAL
                # Server IQ acknowledgements do not themselves require an ACK.
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = exc.reason if isinstance(exc, FCMError) else 'connection_failed'
            self._diag['last_error'] = reason
            raise FCMError(reason) from None
        finally:
            await self._state(False)
            await self._shutdown_writer()
            self._run_task = None

    async def _shutdown_writer(self):
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is None:
            return
        writer.close()
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT):
                await writer.wait_closed()
        except (Exception, asyncio.CancelledError):
            writer.transport.abort()

    async def close(self):
        """Invalidate callbacks first, then cancel/drain all work we own."""
        self._closed = True
        self._connected = False
        if self._close_task is None:
            caller = asyncio.current_task()
            self._close_task = asyncio.create_task(self._close(caller), name='welcomeeye-fcm-close')
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _close(self, caller):
        tasks = {task for task in (self._register_task, self._run_task)
                 if task and task is not caller and not task.done()}
        for task in tasks:
            task.cancel()
        await self._shutdown_writer()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
