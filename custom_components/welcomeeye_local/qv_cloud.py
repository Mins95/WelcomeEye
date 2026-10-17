"""Opt-in QV cloud calls; independent of every local/media/photo connection."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import time

from .capabilities import DeviceVariant, variant_for
from .connect3.channels import channel2_enabled
from .qv_cloud_events import QVEventFilter, parse_qv_ring
from .qv_cloud_protocol import QvCloudError, make_client_id, profile_for
from .snapshot import _finish_task

STORAGE_VERSION = 1
MAX_CONNECTION_ATTEMPTS = 3
RECONNECT_DELAYS = (5.0, 30.0)
START_TIMEOUT = 120.0
CLEANUP_TIMEOUT = 90.0
CLOSE_TIMEOUT = 10.0
_VARIANTS = (DeviceVariant.CONNECT3, DeviceVariant.R002)
_ERRORS = frozenset({'storage_failed', 'configuration_changed', 'invalid_configuration',
                    'unauthorized_device', 'invalid_channel', 'authentication_failed',
                    'provider_rejected', 'invalid_response', 'invalid_service',
                    'connection_failed', 'request_timeout', 'registration_failed',
                    'cleanup_failed', 'callback_failed'})


class QVCloudStateError(ValueError):
    """A private installation cannot be loaded or durably saved."""

    def __init__(self):
        super().__init__('storage_failed')


def _verify_saved_state(path, key, data):
    try:
        with open(path, encoding='utf-8') as handle:
            saved = json.load(handle)
    except (OSError, ValueError, TypeError):
        raise QVCloudStateError() from None
    if saved != {'version': STORAGE_VERSION, 'minor_version': 1, 'key': key, 'data': data}:
        raise QVCloudStateError()


class QVCloudDoorbell:
    """Own exactly one entry's independent cloud installation and subscriptions."""

    def __init__(self, hass, entry, on_ring, *, on_state=None):
        self.hass, self.entry = hass, entry
        self._on_ring, self._on_state = on_ring, on_state
        self._account = deepcopy(entry.data.get('qv_cloud_account'))
        self._local_uid = entry.unique_id
        self._entry_id = entry.entry_id
        self._storage_key = f'welcomeeye_local.qv_cloud.{entry.entry_id}'
        try:
            self._variant = variant_for(entry.data)
        except (TypeError, ValueError):
            self._variant = None
        self._uid = (self._account.get('device', {}).get('uid')
                     if isinstance(self._account, dict)
                     and isinstance(self._account.get('device'), dict) else None)
        self._task = self._close_task = None
        self._save_tasks = set()
        self._save_lock = asyncio.Lock()
        self._message_lock = asyncio.Lock()
        self._store = self._session = self._data = self._filter = None
        self._client = self._receiver = self._device = None
        self._allowed_channels = frozenset()
        self._subscribed = False
        self._closed = True
        self._started = None
        self._transport_diag = {}
        self._diag = {'status': 'disabled', 'registration_status': 'not_started',
                      'subscription_status': 'not_started', 'connection_attempts': 0,
                      'rings_received': 0, 'ignored_messages': 0,
                      'last_error': None, 'last_error_stage': None,
                      'cleanup_error': None, 'delivery_observed': False,
                      'phone_coexistence_verified': False}

    def _configuration_valid(self):
        if (self._variant not in _VARIANTS or not isinstance(self._account, dict)
                or self.entry.entry_id != self._entry_id or self.entry.unique_id != self._local_uid
                or self.entry.data.get('qv_cloud_account') != self._account):
            return False
        try:
            if variant_for(self.entry.data) != self._variant:
                return False
            make_client_id(self._account['installation_id'], profile_for(self._variant))
            if (not isinstance(self._uid, str) or not 1 <= len(self._uid) <= 256
                    or not isinstance(self._account.get('email'), str)
                    or not 3 <= len(self._account['email']) <= 320
                    or not isinstance(self._account.get('password'), str)
                    or not 1 <= len(self._account['password']) <= 1024):
                return False
            bound_uid = self.entry.data.get('credential_device_uid')
            return not bound_uid or bound_uid == self._uid
        except (KeyError, TypeError, ValueError):
            return False

    def _eligible(self):
        return self.entry.data.get('qv_cloud_enabled') is True and self._configuration_valid()

    def _binding(self):
        identity = [self._entry_id, self._local_uid, str(self._variant),
                    self._account['installation_id'], self._account['email'], self._uid]
        return hashlib.sha256(json.dumps(identity, ensure_ascii=True,
            separators=(',', ':')).encode('ascii')).hexdigest()

    def _configured_channels(self):
        return frozenset((1, 2) if channel2_enabled(self.entry.data) else (1,))

    @property
    def connected(self):
        return bool(not self._closed and self._eligible() and self._subscribed
                    and self._receiver is not None and self._receiver.connected)

    def diagnostics(self):
        self._remember_transport()
        return {**self._diag, 'connected': self.connected, 'transport': 'fcm',
                'scope': 'qv_calls_only', 'fcm': dict(self._transport_diag),
                'events': self._filter.diagnostics() if self._filter else {},
                'automatic_connection_attempt_limit': MAX_CONNECTION_ATTEMPTS}

    def _remember_transport(self):
        if self._receiver is None:
            return
        values = self._receiver.diagnostics()
        for name in ('registrations', 'checkins', 'messages', 'delivered', 'duplicates',
                     'malformed', 'ignored', 'callback_errors', 'acknowledged'):
            value = values.get(name)
            if type(value) is int and value >= 0:
                self._transport_diag[name] = value
        for name, allowed in (
            ('timestamp_source', ('none', 'google_sent_time', 'mcs_sent_milliseconds')),
            ('timestamp_status', ('unobserved', 'valid', 'invalid', 'conflicting', 'missing')),
            ('http_stage', (None, 'checkin', 'gcm_registration', 'installation', 'fcm_registration', 'unknown')),
        ):
            if name in values and values[name] in allowed:
                self._transport_diag[name] = values[name]
        for name in ('mcs_sent_present', 'google_sent_time_present', 'google_sent_time_valid'):
            if type(values.get(name)) is bool:
                self._transport_diag[name] = values[name]
        status = values.get('http_status')
        if status is None or (type(status) is int and 100 <= status <= 599):
            self._transport_diag['http_status'] = status

    def _notify(self):
        if not self._closed and self._on_state:
            try:
                self._on_state()
            except Exception:
                self._diag['last_error'] = 'callback_failed'

    def _error(self, exc, stage):
        code = ('storage_failed' if isinstance(exc, QVCloudStateError)
                else 'request_timeout' if isinstance(exc, TimeoutError)
                else getattr(exc, 'code', None))
        self._diag['last_error'] = code if code in _ERRORS else 'connection_failed'
        self._diag['last_error_stage'] = stage

    async def start(self):
        if not self._eligible() or (self._task and not self._task.done()):
            return
        self._closed = False
        self._close_task = None
        self._started = time.time()
        self._diag['status'] = 'starting'
        self._task = asyncio.create_task(self._run(), name='welcomeeye-qv-cloud')

    async def _load(self, *, create=True):
        from homeassistant.helpers.storage import Store
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        self._store = Store(self.hass, STORAGE_VERSION, self._storage_key,
                            private=True, atomic_writes=True)
        self._session = async_get_clientsession(self.hass)
        data = await self._store.async_load()
        if data is None:
            if not create:
                return
            data = {'binding': self._binding(), 'credentials': None,
                    'client_id': make_client_id(self._account['installation_id'], profile_for(self._variant)),
                    'subscriptions': [], 'events': None}
        expected_client = make_client_id(self._account['installation_id'], profile_for(self._variant))
        if (not isinstance(data, dict) or data.get('binding') != self._binding()
                or data.get('client_id') != expected_client
                or not isinstance(data.get('subscriptions'), list)
                or len(data['subscriptions']) > 2
                or any(type(ch) is not int or ch not in (1, 2) for ch in data['subscriptions'])
                or len(set(data['subscriptions'])) != len(data['subscriptions'])
                or (data.get('credentials') is not None and not isinstance(data['credentials'], dict))):
            raise QVCloudStateError()
        try:
            self._filter = QVEventFilter(self._uid, startup_time=self._started,
                                         state=data.get('events'))
        except ValueError:
            raise QVCloudStateError() from None
        self._data = data
        if create:
            await self._save()

    async def _save(self, *, subscriptions=None):
        task = asyncio.create_task(self._save_confirmed(subscriptions), name='welcomeeye-qv-cloud-save')
        self._save_tasks.add(task)
        try:
            await _finish_task(task, cancel_on_cancel=False)
        finally:
            self._save_tasks.discard(task)

    async def _save_confirmed(self, subscriptions):
        async with self._save_lock:
            if self._data is None or self._store is None:
                raise QVCloudStateError()
            saved = deepcopy(self._data)
            saved['events'] = self._filter.export_state()
            if subscriptions is not None:
                saved['subscriptions'] = sorted(subscriptions)
            try:
                await self._store.async_save(saved)
                await self.hass.async_add_executor_job(_verify_saved_state,
                    self.hass.config.path('.storage', self._storage_key), self._storage_key, saved)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise QVCloudStateError() from None
            self._data['events'] = saved['events']
            if subscriptions is not None:
                self._data['subscriptions'] = saved['subscriptions']

    async def _credentials_changed(self, credentials):
        if self._closed or not self._eligible():
            raise QvCloudError('closed')
        self._data['credentials'] = deepcopy(credentials)
        await self._save()

    def _receiver_factory(self):
        from .qv_cloud_fcm import QVFCMReceiver
        return QVFCMReceiver(self._session, self._message, self._credentials_changed,
            credentials=self._data.get('credentials'), on_state=self._notify,
            variant=str(self._variant))

    async def _authorize(self, *, cleanup=False):
        from .qv_cloud_api import QvCloudClient
        from .qv_cloud_discovery import resolve_services

        profile = profile_for(self._variant)
        installation_id = self._account['installation_id']
        async def resolver(region_id):
            return await resolve_services(profile, installation_id, region_id=region_id)
        services = await resolver(self._account.get('region_id', 0))
        self._client = QvCloudClient(services, installation_id, profile=profile, resolver=resolver)
        await self._client.async_login(self._account['email'], self._account['password'])
        devices = await self._client.async_list_devices()
        matches = [device for device in devices if device.uid == self._uid and device.cloud_type == 0]
        if len(matches) != 1 or not self._configuration_valid():
            raise QvCloudError('unauthorized_device')
        self._device = matches[0]
        # SDK account channels and client-update-dev are zero based; HA is one based.
        self._allowed_channels = frozenset(ch for ch in self._configured_channels()
                                          if ch - 1 in self._device.channels)
        if not self._allowed_channels and not cleanup:
            raise QvCloudError('invalid_channel')

    async def _connect(self):
        self._diag['last_error_stage'] = 'account_authorization'
        await self._authorize()
        if self._closed or not self._eligible():
            raise QvCloudError('closed')
        self._diag['last_error_stage'] = 'fcm_registration'
        self._receiver = self._receiver_factory()
        token = await self._receiver.register()
        self._diag['registration_status'] = 'registered'
        if self._closed or not self._eligible():
            raise QvCloudError('closed')
        self._diag['last_error_stage'] = 'vendor_registration'
        # FCM's callback has already verified a durable installation/token save.
        await self._client.async_register(token)
        self._diag['last_error_stage'] = 'vendor_subscription'
        for channel in tuple(self._data['subscriptions']):
            if channel not in self._allowed_channels:
                await self._disable_channel(channel)
        self._diag['subscription_status'] = 'registering'
        for channel in sorted(self._allowed_channels):
            if self._closed or not self._eligible():
                raise QvCloudError('closed')
            if channel not in self._configured_channels():
                raise QvCloudError('invalid_channel')
            # Persist intent before a request whose reply might be lost.
            await self._save(subscriptions=set(self._data['subscriptions']) | {channel})
            if self._closed or not self._eligible():
                raise QvCloudError('closed')
            if channel not in self._configured_channels():
                raise QvCloudError('invalid_channel')
            await self._client.async_subscribe(self._device, channel - 1, True)
        self._subscribed = True
        self._diag['subscription_status'] = 'accepted'

    async def _run(self):
        try:
            self._diag['last_error_stage'] = 'storage'
            await self._load()
            for attempt in range(MAX_CONNECTION_ATTEMPTS):
                if self._closed or not self._eligible():
                    return
                self._diag['connection_attempts'] += 1
                self._diag['status'] = 'connecting'
                try:
                    async with asyncio.timeout(START_TIMEOUT):
                        await self._connect()
                    self._diag.update(status='listening', last_error=None, last_error_stage=None)
                    self._notify()
                    await self._receiver.run()
                    if not self._closed:
                        raise ConnectionError()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._error(exc, self._diag['last_error_stage'] or 'fcm_receive')
                    self._diag['status'] = 'disconnected'
                finally:
                    await self._close_connections()
                    self._notify()
                if self._closed or not self._eligible():
                    return
                if attempt + 1 < MAX_CONNECTION_ATTEMPTS and attempt < len(RECONNECT_DELAYS):
                    await asyncio.sleep(RECONNECT_DELAYS[attempt])
            self._diag['status'] = 'failed_reload_required'
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error(exc, self._diag['last_error_stage'] or 'storage')
            self._diag['status'] = 'failed_reload_required'
        finally:
            await self._close_connections()
            self._notify()

    async def _message(self, data, persistent_id, sent_ms):
        async with self._message_lock:
            if (self._closed or not self._eligible() or not self._subscribed
                    or self._filter is None):
                return
            candidate = parse_qv_ring(data, self._uid)
            if (candidate is None or candidate.channel not in self._allowed_channels
                    or candidate.channel not in self._configured_channels()):
                self._diag['ignored_messages'] += 1
                return
            event = self._filter.accept(data, persistent_id, sent_ms)
            if event is None:
                return
            try:
                await self._save()
            except QVCloudStateError as exc:
                self._error(exc, 'event_storage')
                self._notify()
                return
            # A stop/reconfigure may have occurred during the disk operation.
            if (self._closed or not self._eligible() or not self._subscribed
                    or event.channel not in self._configured_channels()):
                return
            try:
                self._on_ring(event.channel)
            except Exception:
                self._diag['last_error'] = 'callback_failed'
                return
            self._diag['rings_received'] += 1
            self._diag['delivery_observed'] = True
            self._notify()

    async def _disable_channel(self, channel):
        if channel - 1 not in self._device.channels:
            raise QvCloudError('unauthorized_device')
        await self._client.async_subscribe(self._device, channel - 1, False)
        await self._save(subscriptions=set(self._data['subscriptions']) - {channel})

    async def cleanup_pending(self):
        """Start own-installation cleanup after explicit disable, without blocking HA."""
        if (self.entry.data.get('qv_cloud_enabled') is not False
                or not self._configuration_valid() or (self._task and not self._task.done())):
            return
        self._closed = False
        self._close_task = None
        self._started = time.time()
        self._task = asyncio.create_task(self._cleanup_only(), name='welcomeeye-qv-cloud-unsubscribe')

    async def _cleanup_only(self, *, remove=False):
        self._subscribed = False
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT):
                await self._load(create=False)
                if self._data is None:
                    return
                if self._data['subscriptions']:
                    credentials = self._data.get('credentials') or {}
                    token = credentials.get('fcm', {}).get('registration', {}).get('token')
                    if not isinstance(token, str) or not token:
                        raise QVCloudStateError()
                    await self._authorize(cleanup=True)
                    await self._client.async_register(token)
                    for channel in tuple(self._data['subscriptions']):
                        await self._disable_channel(channel)
                self._diag['subscription_status'] = 'disabled'
                self._diag['cleanup_error'] = None
                if remove:
                    await self._store.async_remove()
                    self._data = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diag['cleanup_error'] = ('storage_failed' if isinstance(exc, QVCloudStateError)
                                           else 'cleanup_failed')
            self._diag['subscription_status'] = 'cleanup_pending'
        finally:
            await self._close_connections()
            self._diag['status'] = 'disabled'
            self._notify()

    async def _close_connections(self):
        self._subscribed = False
        receiver, client = self._receiver, self._client
        self._remember_transport()
        self._receiver = self._client = self._device = None
        self._allowed_channels = frozenset()
        for obj, method in ((receiver, 'close'), (client, 'async_close')):
            if obj is not None:
                try:
                    async with asyncio.timeout(CLOSE_TIMEOUT):
                        await getattr(obj, method)()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._diag['cleanup_error'] = 'cleanup_failed'

    async def stop(self):
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop(), name='welcomeeye-qv-cloud-stop')
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
        if self._save_tasks:
            await asyncio.gather(*self._save_tasks, return_exceptions=True)
        await self._close_connections()
        self._diag['status'] = 'stopped'

    async def async_remove(self):
        """Explicit entry removal: await bounded cleanup of this installation only."""
        await self.stop()
        if not self._configuration_valid():
            return
        self._started = time.time()
        task = asyncio.create_task(self._cleanup_only(remove=True), name='welcomeeye-qv-cloud-remove')
        await _finish_task(task, cancel_on_cancel=False)

    close = stop
    remove = async_remove
