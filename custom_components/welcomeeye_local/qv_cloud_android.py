"""Native Android FCM enrollment for the two QV APK profiles.

Only public APK configuration is shared with Android apps. Check-in identity
and Firebase installation credentials are generated independently for HA.
The existing V1 Web Push receiver and its stored credentials remain separate.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
import math
import re
import secrets
import time

from firebase_messaging.proto.android_checkin_pb2 import DEVICE_ANDROID_OS

from .qv_cloud_fcm import QVFCMReceiver
from .v1_cloud_fcm import (
    AndroidCheckinRequest, AndroidCheckinResponse, FCMError, FCMReceiver,
    FCM_INSTALLATION, GCM_CHECKIN_URL, GCM_REGISTER_URL, REGISTER_TIMEOUT,
    _b64, _text, _unique_json_object, _varint,
)


@dataclass(frozen=True)
class AndroidAppProfile:
    """Public metadata extracted from the APK manifest and signing block."""

    package: str
    certificate_sha1: str
    version_code: int
    version_name: str
    target_sdk: int


DOOR_CONNECT_ANDROID = AndroidAppProfile(
    'com.extel.philipsdoorconnect',
    'E23CA2E769D41E28F45B3C0B823D098909497BE3', 7, '1.0.123.3(2)', 36,
)
WELCOMEEYE_ANDROID = AndroidAppProfile(
    'com.extel.philipswelcomeeye',
    '83B9A96C794DC8196DBAF3E35199E5F141E430D0', 63, '6.1.58.24', 36,
)

# Independent virtual client metadata, not a borrowed phone fingerprint.
# microG v0.3.16.252432 build.gradle: SDK29, 252432*1000 + 16*2.
ANDROID_SDK = 29
GMS_VERSION = 252432032
FIS_SDK_VERSION = 'a:17.1.3'
FCM_SDK_VERSION = 'fcm-23.1.2'
FIREBASE_APP_NAME_HASH = _b64(hashlib.sha1(b'[DEFAULT]').digest()).rstrip('=')
_FID = re.compile(r'[cdef][A-Za-z0-9_-]{21}\Z')


def _opaque(value, maximum=16384):
    """Validate private opaque text without exposing it in an exception."""
    value = _text(value, maximum)
    if not value.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise FCMError('invalid_response')
    return value


def _uint64(value):
    value = _opaque(value, 20)
    if not value.isdecimal() or not 0 < int(value) < 2**64:
        raise FCMError('invalid_credentials')
    return int(value)


def _android_checkin(previous=None):
    """Encode the documented Android Build field omitted by our dependency.

    microG's fixed-tag checkin.proto: Checkin.build=1 (message),
    Build.clientId=6 (string), Build.sdkVersion=10 (int32). All other Build
    members are optional. MergeFromString preserves these unknown fields.
    """
    request = AndroidCheckinRequest(version=3, user_serial_number=0,
                                    fragment=1 if previous else 0)
    request.checkin.type = DEVICE_ANDROID_OS
    request.checkin.user_number = 0
    client = b'android-google'
    build = b'\x32' + _varint(len(client)) + client + b'\x50' + _varint(ANDROID_SDK)
    request.checkin.MergeFromString(b'\x0a' + _varint(len(build)) + build)
    request.account_cookie.append('')
    if previous is not None:
        identity = _uint64(previous['android_id'])
        request.id = identity if identity < 2**63 else identity - 2**64
        request.security_token = _uint64(previous['security_token'])
    return request


class QVAndroidFCMReceiver(QVFCMReceiver):
    """QV parser and MCS lifecycle with native, independently owned credentials."""

    def __init__(self, *args, variant='connect3', **kwargs):
        super().__init__(*args, variant=variant, **kwargs)
        self._android = {'connect3': DOOR_CONNECT_ANDROID,
                         'connect2_r002': WELCOMEEYE_ANDROID}[variant]
        self._migration_status = ('pending' if self._credential_transport() == 'web'
                                  else 'not_needed')

    def _credential_transport(self):
        if self._credentials is None:
            return 'none'
        if not isinstance(self._credentials, dict):
            return 'invalid'
        marker = self._credentials.get('transport')
        return ('android' if marker == 'android' else 'web' if marker in (None, 'web')
                else 'invalid')

    def diagnostics(self):
        return {**super().diagnostics(), 'enrollment_transport': 'android',
                'credential_transport': self._credential_transport(),
                'migration_status': self._migration_status}

    def _config(self):
        return {'bundle_id': self._profile.bundle_id,
                'project_id': self._profile.project_id,
                'app_id': self._profile.app_id}

    def _fis_headers(self):
        return {'x-goog-api-key': self._profile.api_key,
                'X-Android-Package': self._android.package,
                'X-Android-Cert': self._android.certificate_sha1,
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'Content-Encoding': 'gzip', 'Cache-Control': 'no-cache'}

    async def _checkin(self, previous=None):
        request = _android_checkin(previous)
        body = await self._post(GCM_CHECKIN_URL,
            headers={'Content-Type': 'application/x-protobuffer',
                     'Content-Encoding': 'gzip',
                     'User-Agent': 'Android-Checkin/2.0 (generic UNKNOWN); gzip'},
            data=gzip.compress(request.SerializeToString(), mtime=0))
        response = AndroidCheckinResponse.FromString(body)
        if not response.IsInitialized() or not response.android_id or not response.security_token:
            raise FCMError('invalid_checkin')
        if previous and str(response.android_id) != previous['android_id']:
            raise FCMError('identity_changed')
        self._diag['checkins'] += 1
        return {'android_id': str(response.android_id),
                'security_token': str(response.security_token)}

    def _validate_credentials(self, credentials=None):
        try:
            saved = self._credentials if credentials is None else credentials
            if (not isinstance(saved, dict) or saved.get('transport') != 'android'
                    or saved.get('config') != self._config()
                    or saved.get('android') != asdict(self._android) or 'keys' in saved):
                raise ValueError
            gcm = saved['gcm']
            _uint64(gcm['android_id'])
            _uint64(gcm['security_token'])
            if gcm['app_id'] != self._profile.sender_id:
                raise ValueError
            token = _opaque(saved['fcm']['registration']['token'])
            if _opaque(gcm['token']) != token:
                raise ValueError
            installation = saved['fcm']['installation']
            if not _FID.fullmatch(_opaque(installation['fid'], 22)):
                raise ValueError
            _opaque(installation['token'])
            _opaque(installation['refresh_token'])
            expiry, created = installation['expires_in'], installation['created_at']
            if (type(expiry) is not int or not 0 < expiry < 2**31
                    or type(created) not in (int, float) or not math.isfinite(created)
                    or not 0 < created < 2**63):
                raise ValueError
        except Exception:
            raise FCMError('invalid_credentials') from None

    def _validate_legacy_credentials(self):
        """Migration is allowed only from this profile's valid Web credential set."""
        try:
            if (self._credentials.get('transport') not in (None, 'web')
                    or 'android' in self._credentials):
                raise ValueError
            FCMReceiver._validate_credentials(self)
            if not self._credentials['gcm']['app_id'].startswith('wp:'):
                raise ValueError
        except Exception:
            raise FCMError('invalid_credentials') from None

    async def _enroll(self):
        gcm = await self._checkin()
        fid_bytes = bytearray(secrets.token_bytes(17))
        fid_bytes[0] = 0x70 | (fid_bytes[0] & 0x0f)
        fid = _b64(fid_bytes)[:22]
        body = {'appId': self._profile.app_id, 'authVersion': 'FIS_v2',
                'fid': fid, 'sdkVersion': FIS_SDK_VERSION}
        installation = json.loads(await self._post(
            FCM_INSTALLATION + f'projects/{self._profile.project_id}/installations',
            headers=self._fis_headers(),
            data=gzip.compress(json.dumps(body).encode('utf-8'), mtime=0)),
            object_pairs_hook=_unique_json_object)
        if installation['fid'] != fid:
            raise FCMError('invalid_response')
        auth = _opaque(installation['authToken']['token'])
        expires = installation['authToken']['expiresIn']
        if (not isinstance(expires, str) or not re.fullmatch(r'[0-9]{1,10}s', expires)
                or not 0 < int(expires[:-1]) < 2**31):
            raise FCMError('invalid_response')
        stored_installation = {'token': auth,
            'refresh_token': _opaque(installation['refreshToken']), 'fid': fid,
            'expires_in': int(expires[:-1]), 'created_at': time.time()}
        gcm['app_id'] = self._profile.sender_id
        form = {
            'app': self._android.package, 'cert': self._android.certificate_sha1.lower(),
            'app_ver': str(self._android.version_code), 'target_ver': str(self._android.target_sdk),
            'sender': self._profile.sender_id, 'device': gcm['android_id'],
            'X-scope': '*', 'X-subtype': self._profile.sender_id,
            'X-gmp_app_id': self._profile.app_id, 'X-gmsv': str(GMS_VERSION),
            'X-osv': str(ANDROID_SDK), 'X-app_ver': str(self._android.version_code),
            'X-app_ver_name': self._android.version_name,
            'X-firebase-app-name-hash': FIREBASE_APP_NAME_HASH,
            'X-Goog-Firebase-Installations-Auth': auth, 'X-appid': fid,
            'X-cliv': FCM_SDK_VERSION,
        }
        result = await self._post(GCM_REGISTER_URL,
            headers={'Authorization': f"AidLogin {gcm['android_id']}:{gcm['security_token']}",
                     'Content-Type': 'application/x-www-form-urlencoded',
                     'app': self._android.package,
                     'User-Agent': 'Android-GCM/1.5 (generic UNKNOWN)'}, data=form)
        label, separator, token = result.decode('utf-8').strip().partition('=')
        if label != 'token' or not separator:
            raise FCMError('registration_rejected')
        token = _opaque(token)
        gcm['token'] = token
        self._diag['registrations'] += 1
        return {'transport': 'android', 'config': self._config(),
                'android': asdict(self._android), 'gcm': gcm,
                'fcm': {'registration': {'token': token}, 'installation': stored_installation}}

    async def register(self):
        """Replace a Web credential set only after a native candidate is durable."""
        self._ensure_open()
        if self._register_task or self._run_task:
            raise FCMError('already_running')
        self._register_task = asyncio.current_task()
        previous = deepcopy(self._credentials)
        migrating = previous is not None and (
            not isinstance(previous, dict) or previous.get('transport') != 'android')
        try:
            async with asyncio.timeout(REGISTER_TIMEOUT):
                if migrating:
                    self._validate_legacy_credentials()
                    self._migration_status = 'running'
                elif previous is not None:
                    self._validate_credentials()
                if previous is None or migrating:
                    candidate = await self._enroll()
                else:
                    candidate = deepcopy(previous)
                    candidate['gcm'].update(await self._checkin(previous['gcm']))
                self._validate_credentials(candidate)
                try:
                    await self._callback(self._on_credentials, deepcopy(candidate))
                except Exception:
                    raise FCMError('storage_failed') from None
                self._credentials = candidate
                if migrating:
                    self._seen.clear()
                    self._migration_status = 'complete'
                self._diag['last_error'] = None
                return candidate['fcm']['registration']['token']
        except BaseException as exc:
            self._credentials = previous
            if migrating:
                self._migration_status = ('cancelled' if isinstance(exc, asyncio.CancelledError)
                                          else 'failed')
            if isinstance(exc, asyncio.CancelledError):
                raise
            if not isinstance(exc, Exception):
                raise
            reason = (exc.reason if isinstance(exc, FCMError)
                      else 'registration_timeout' if isinstance(exc, TimeoutError)
                      else 'registration_failed')
            self._diag['last_error'] = reason
            raise FCMError(reason) from None
        finally:
            self._register_task = None

    def _login(self):
        request = super()._login()
        request.id = f'android-{ANDROID_SDK}'
        return request

    def _message_target_error(self, message):
        if message.raw_data:
            return 'unexpected_transport'
        if message.category != self._android.package:
            return 'category_mismatch'
        return None
