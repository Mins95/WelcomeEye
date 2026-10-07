"""Opt-in LT/FCM doorbell for V1 only; never owns a device/media connection.

Registration is an independent installation, not the phone's token. The APK
documents the vendor wire format, but delivery/coexistence still need a real
V1 trial. Secrets live in HA private storage, never in diagnostics or events.
"""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import hashlib
import time
import uuid

from .capabilities import DeviceVariant, variant_for
from .snapshot import _finish_task
from .v1_cloud_protocol import (
    CloudProtocolError, build_check_request, build_subscription_request,
    check_subscription_response, decode_payload, encode_payload, make_client_id,
    parse_ring_notification, validate_subscription_response,
)

MAX_RESPONSE_BYTES = 65536
REQUEST_TIMEOUT = 12.0
START_TIMEOUT = 60.0
MAX_CONNECTION_ATTEMPTS = 3
RECONNECT_DELAYS = (5.0, 30.0)
MAX_RING_AGE_SECONDS = 120
SEEN_LIMIT = 256
STORAGE_VERSION = 1
# These are the encrypted LT-device endpoints selected by the WelcomeEye APK.
SUBSCRIBE_URL = 'https://tdpush.push2u.com/pda_more_api.php'
CHECK_URL = 'https://tdpush.push2u.com/pushCheck.php'


class CloudStateError(Exception):
    """Private state is absent or does not belong to this entry."""


class V1CloudDoorbell:
    """Own the receiver, subscription and private state for exactly one V1."""

    def __init__(self, hass, entry, on_ring, *, on_state=None):
        self.hass, self.entry = hass, entry
        self._on_ring, self._on_state = on_ring, on_state
        self._uid = entry.unique_id
        self._task = self._close_task = self._save_task = None
        self._receiver = None
        self._store = self._session = None
        self._data = None
        self._seen = deque(maxlen=SEEN_LIMIT)
        self._save_lock = asyncio.Lock()
        self._seen_revision = 0
        self._transport_diag = {}
        self._closed = True
        self._subscribed = False
        self._diag = {
            'status': 'disabled', 'registration_status': 'not_started',
            'subscription_status': 'not_started', 'connection_attempts': 0,
            'messages_received': 0, 'rings_received': 0, 'duplicate_messages': 0,
            'ignored_messages': 0, 'stale_messages': 0, 'malformed_messages': 0,
            'last_error_type': None, 'last_error_stage': None,
            'storage_error_type': None, 'cleanup_error_type': None,
            'delivery_observed': False, 'phone_coexistence_verified': False,
        }

    def _eligible(self):
        try:
            variant = variant_for(self.entry.data)
        except (ValueError, TypeError):
            return False
        return (variant == DeviceVariant.V1
                and self.entry.data.get('v1_cloud_doorbell_enabled') is True
                and self.entry.unique_id == self._uid
                and isinstance(self._uid, str) and bool(self._uid))

    @property
    def connected(self):
        return bool(not self._closed and self._eligible() and self._subscribed
                    and self._receiver is not None and self._receiver.connected)

    def diagnostics(self):
        # Explicit scalar allowlist: never forward credentials/receiver payloads.
        if self._receiver is not None:
            self._remember_transport()
        return {**self._diag, 'connected': self.connected,
                'transport': 'fcm', 'scope': 'v1_doorbell_only',
                'fcm': dict(self._transport_diag),
                'automatic_connection_attempt_limit': MAX_CONNECTION_ATTEMPTS}

    def _remember_transport(self):
        method = getattr(self._receiver, 'diagnostics', None)
        if method is None:
            return
        values = method()
        # No arbitrary strings/integers from protobufs or HTTP results.
        for name in ('registrations', 'checkins', 'messages', 'delivered',
                     'duplicates', 'malformed', 'ignored', 'callback_errors',
                     'acknowledged'):
            value = values.get(name)
            if type(value) is int and value >= 0:
                self._transport_diag[name] = value
        reason = values.get('last_error')
        from .v1_cloud_fcm import ERROR_REASONS
        if reason is None or reason in ERROR_REASONS:
            self._transport_diag['last_error'] = reason
        stage = values.get('http_stage')
        if stage in (None, 'checkin', 'gcm_registration', 'installation',
                     'fcm_registration', 'unknown'):
            self._transport_diag['http_stage'] = stage
        status = values.get('http_status')
        if status is None or (type(status) is int and 100 <= status <= 599):
            self._transport_diag['http_status'] = status

    def _notify(self):
        if not self._closed and self._on_state:
            self._on_state()

    async def start(self):
        if not self._eligible() or (self._task and not self._task.done()):
            return
        self._closed = False
        self._close_task = None
        self._diag['status'] = 'starting'
        # This long-lived receiver must not enter HA's startup task barrier.
        self._task = asyncio.create_task(self._run(), name='welcomeeye-v1-cloud')

    async def cleanup_pending(self):
        """Retry only a previous own unsubscribe after an explicit disable.

        No receiver, registration or new installation is created by this path.
        Merely upgrading an entry without the option does not invoke it.
        """
        if (self.entry.data.get('v1_cloud_doorbell_enabled') is not False
                or variant_for(self.entry.data) != DeviceVariant.V1
                or (self._task and not self._task.done())):
            return
        self._closed = False
        self._close_task = None
        self._task = asyncio.create_task(self._cleanup_only(),
                                         name='welcomeeye-v1-cloud-unsubscribe')

    async def _cleanup_only(self):
        try:
            await self._load(create=False)
            if self._data is not None:
                await self._unsubscribe()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diag['cleanup_error_type'] = type(exc).__name__
        finally:
            self._diag['status'] = 'disabled'
            self._notify()

    async def _load(self, *, create=True):
        from homeassistant.helpers.storage import Store
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        self._store = Store(self.hass, STORAGE_VERSION,
                            f'welcomeeye_local.v1_cloud.{self.entry.entry_id}',
                            private=True)
        self._session = async_get_clientsession(self.hass)
        data = await self._store.async_load()
        if data is None:
            if not create:
                return
            data = {'uid': self._uid, 'client_id': make_client_id(uuid.uuid4().hex),
                    'credentials': None, 'seen': [], 'subscription_pending': False}
        if (not isinstance(data, dict) or data.get('uid') != self._uid
                or not isinstance(data.get('client_id'), str)):
            raise CloudStateError()
        self._data = data
        self._seen.extend(value for value in data.get('seen', [])[-SEEN_LIMIT:]
                          if isinstance(value, str) and len(value) == 64)
        await self._save()

    async def _save(self):
        async with self._save_lock:
            if self._data is not None:
                self._data['seen'] = list(self._seen)
                await self._store.async_save(deepcopy(self._data))

    async def _save_seen(self):
        try:
            while True:
                revision = self._seen_revision
                await self._save()
                if revision == self._seen_revision:
                    break
        except Exception as exc:
            self._diag['storage_error_type'] = type(exc).__name__

    async def _credentials_changed(self, credentials):
        token = credentials.get('fcm', {}).get('registration', {}).get('token')
        rotate = (self._data.get('subscription_pending') and isinstance(token, str)
                  and token != self._data.get('subscription_token'))
        if rotate:
            # Keep the old token until its own association has been disabled.
            await self._unsubscribe()
            if self._data.get('subscription_pending'):
                raise CloudStateError()
        self._data['credentials'] = deepcopy(credentials)
        # Save BEFORE associating this installation with the user's device.
        await self._save()
        if rotate and not self._closed and self._eligible():
            await self._subscribe(token)

    def _receiver_factory(self):
        # Imported only after opt-in; no registration or network on module import.
        from .v1_cloud_fcm import FCMReceiver
        return FCMReceiver(
            self._session, self._message, self._credentials_changed,
            credentials=self._data.get('credentials'), on_state=self._notify)

    async def _post(self, url, payload):
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with self._session.post(
                    url, data=encode_payload(payload), allow_redirects=False,
                    headers={'Content-Type': 'text/plain; charset=utf-8'}) as response:
                if response.status != 200:
                    raise CloudStateError()
                body = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise CloudStateError()
                return decode_payload(bytes(body))

    async def _subscribe(self, token):
        self._diag['subscription_status'] = 'registering'
        # Persist intent first, so cancellation/lost HTTP reply can still clean up.
        self._data['subscription_pending'] = True
        self._data['subscription_token'] = token
        await self._save()
        result = await self._post(SUBSCRIBE_URL, build_subscription_request(
            self._data['client_id'], token, self._uid, True))
        validate_subscription_response(result)
        self._diag['subscription_status'] = 'accepted'
        result = await self._post(CHECK_URL, build_check_request(
            self._data['client_id'], self._uid))
        if not check_subscription_response(result, token):
            raise CloudStateError()
        self._subscribed = True
        self._diag['subscription_status'] = 'confirmed'
        self._notify()

    async def _unsubscribe(self):
        self._subscribed = False
        self._notify()
        if not self._data or not self._data.get('subscription_pending'):
            return
        try:
            result = await self._post(SUBSCRIBE_URL, build_subscription_request(
                self._data['client_id'], self._data['subscription_token'],
                self._uid, False))
            validate_subscription_response(result)
            self._data['subscription_pending'] = False
            self._data.pop('subscription_token', None)
            await self._save()
            self._diag['subscription_status'] = 'disabled'
        except Exception as exc:
            # Keep private intent for the next reload. Never disable all clients.
            self._diag['cleanup_error_type'] = type(exc).__name__
            self._diag['subscription_status'] = 'cleanup_pending'

    async def _run(self):
        stage = 'storage'
        try:
            await self._load()
            for attempt in range(MAX_CONNECTION_ATTEMPTS):
                if self._closed or not self._eligible():
                    return
                self._diag['connection_attempts'] += 1
                try:
                    # Remove an uncertain previous association of THIS identity.
                    if self._data.get('subscription_pending'):
                        await self._unsubscribe()
                        if self._data.get('subscription_pending'):
                            raise CloudStateError()
                    stage = 'fcm_registration'
                    self._diag['status'] = 'connecting'
                    self._receiver = self._receiver_factory()
                    async with asyncio.timeout(START_TIMEOUT):
                        token = await self._receiver.register()
                    self._diag['registration_status'] = 'registered'
                    stage = 'vendor_subscription'
                    await self._subscribe(token)
                    stage = 'fcm_receive'
                    self._diag['status'] = 'listening'
                    self._diag['last_error_type'] = None
                    self._diag['last_error_stage'] = None
                    self._notify()
                    await self._receiver.run()
                    if not self._closed:
                        raise ConnectionError()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._diag['last_error_type'] = type(exc).__name__
                    self._diag['last_error_stage'] = stage
                    self._diag['status'] = 'disconnected'
                finally:
                    if self._receiver:
                        self._remember_transport()
                        await self._receiver.close()
                        self._receiver = None
                    self._notify()
                if self._closed or not self._eligible():
                    return
                if attempt < len(RECONNECT_DELAYS):
                    await asyncio.sleep(RECONNECT_DELAYS[attempt])
            self._diag['status'] = 'failed_reload_required'
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diag['status'] = 'failed_reload_required'
            self._diag['last_error_type'] = type(exc).__name__
            self._diag['last_error_stage'] = stage
        finally:
            await self._unsubscribe()
            self._notify()

    def _message(self, data, persistent_id, sent_ms):
        if self._closed or not self._eligible() or not self._subscribed:
            return
        self._diag['messages_received'] += 1
        # UTC transport time, not the timezone-less local timestamp inside LT.
        # Reject backlog and unknown times rather than ring for a previous visit.
        age = time.time() - sent_ms / 1000 if type(sent_ms) is int else float('inf')
        if not -30 <= age <= MAX_RING_AGE_SECONDS:
            self._diag['stale_messages'] += 1
            return
        try:
            ring = parse_ring_notification(data, self._uid)
        except CloudProtocolError:
            self._diag['malformed_messages'] += 1
            return
        if ring is None:
            self._diag['ignored_messages'] += 1
            return
        key = ring.dedup_key
        transport_key = hashlib.sha256(str(persistent_id).encode()).hexdigest()
        if key in self._seen or (persistent_id and transport_key in self._seen):
            self._diag['duplicate_messages'] += 1
            return
        self._seen.append(key)
        if persistent_id:
            self._seen.append(transport_key)
        self._seen_revision += 1
        self._diag['rings_received'] += 1
        self._diag['delivery_observed'] = True
        # Synchronous event handoff; disk writes never delay ringing.
        self._on_ring(ring.channel)
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._save_seen(),
                                                 name='welcomeeye-v1-cloud-state')

    async def close(self):
        self._closed = True  # Invalidate callbacks before awaiting anything.
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(),
                                                  name='welcomeeye-v1-cloud-close')
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _close(self):
        if self._task and not self._task.done():
            self._task.cancel()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
        if self._save_task:
            await asyncio.gather(self._save_task, return_exceptions=True)
        # Final save includes any event accepted while a preceding save ran.
        await self._save_seen()
        self._diag['status'] = 'stopped'
