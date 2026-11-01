"""Opt-in QV cloud calls; independent of every local/media/photo connection."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import re
import time

from .capabilities import DeviceVariant, variant_for
from .connect3.channels import channel2_enabled
from .qv_cloud_events import (
    PAYLOAD_BRANCHES, PAYLOAD_REJECTION_SUBREASONS, REQUIRED_FIELDS,
    QVEventFilter, parse_qv_ring,
)
from .qv_cloud_protocol import (
    CALL_EVENTS, QvCloudError, make_client_id, profile_for,
    subscription_scope, subscription_scope_reason,
)
from .qv_cloud_structure import KNOWN_FIELDS
from .snapshot import _finish_task

STORAGE_VERSION = 1
MAX_CONNECTION_ATTEMPTS = 3
RECONNECT_DELAYS = (5.0, 30.0)
START_TIMEOUT = 120.0
CLEANUP_TIMEOUT = 90.0
CLOSE_TIMEOUT = 10.0
_VARIANTS = (DeviceVariant.CONNECT3, DeviceVariant.R002)
_UNCHANGED_CREDENTIALS = object()
_ERRORS = frozenset({'storage_failed', 'configuration_changed', 'invalid_configuration',
                    'unauthorized_device', 'invalid_channel', 'authentication_failed',
                    'provider_rejected', 'invalid_response', 'invalid_service',
                    'connection_failed', 'request_timeout', 'registration_failed',
                    'cleanup_failed', 'callback_failed', 'subscription_mismatch'})
_RECEIVE_STAGES = frozenset({'not_started', 'validating', 'tls_connect', 'mcs_login',
                            'receiving', 'disconnected', 'failed', 'closed'})
_TRANSPORT_DISPOSITIONS = frozenset({'unobserved', 'delivered_to_filter', 'rejected',
                                   'duplicate', 'receiver_closed'})
_REJECTION_REASONS = frozenset({'sender_mismatch', 'subtype_mismatch', 'deleted_notice',
                              'qv_payload_rejected', 'malformed_payload',
                              'unsupported_encoding', 'callback_failed',
                              'category_mismatch', 'unexpected_transport'})
_TRANSPORT_ERRORS = frozenset({'invalid_credentials', 'registration_rejected',
    'registration_failed', 'registration_timeout', 'http_rejected', 'http_timeout',
    'connect_timeout', 'login_timeout', 'login_rejected', 'heartbeat_timeout',
    'server_closed', 'connection_failed', 'frame_too_large', 'protocol_version',
    'frame_length', 'partial_frame_timeout', 'invalid_response', 'invalid_checkin',
    'identity_changed', 'response_too_large', 'storage_failed', 'closed'})
_FILTER_DISPOSITIONS = frozenset({'rejected_payload', 'unavailable_timestamp',
    'invalid_timestamp', 'expired_timestamp', 'future_timestamp', 'before_startup', 'duplicate'})

_SHAPE_STAGES = ('transport_data', 'decrypted_envelope', 'parser_input')
_SHAPE_TYPES = frozenset({'map', 'list', 'string', 'boolean', 'integer', 'number',
                         'null', 'bytes', 'other'})
_SHAPE_KEYS = KNOWN_FIELDS | _SHAPE_TYPES | frozenset({
    'root_type', 'nodes', 'inspected_nodes', 'inspected_fields', 'json_strings_inspected',
    'json_bytes_inspected', 'invalid_json_strings', 'limits', 'depth', 'fields',
    'json_bytes', 'path', 'type', 'recursive', 'field_count', 'known_fields',
    'unknown_fields', 'qv_fields', 'case_variant_fields', 'missing_required_fields',
    'fields_truncated', 'item_count', 'inspected_items', 'item_types', 'items_truncated',
    'json_candidate', 'json_status', 'status',
})
_SHAPE_VALUES = KNOWN_FIELDS | _SHAPE_TYPES | frozenset({
    'depth_limit', 'byte_limit', 'invalid_utf8', 'malformed', 'decoded', 'inspection_failed',
})
_SHAPE_PATH = re.compile(r'\$(?:\.(?:' + '|'.join(re.escape(name) for name in
    sorted(KNOWN_FIELDS, key=len, reverse=True)) + r'|json|field_(?:[0-9]|[1-5][0-9]|6[0-3]))'
    r'|\[(?:[0-9]|[1-5][0-9]|6[0-3])\]){0,3}')


def _safe_payload_shape(value):
    """Validate the receiver's structural report again at the HA boundary."""
    remaining = 2048

    def copy(item, key=None, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 7:
            raise ValueError()
        if type(item) is dict and len(item) <= 64:
            result = {}
            for name, child in item.items():
                if type(name) is not str or name not in _SHAPE_KEYS:
                    raise ValueError()
                result[name] = copy(child, name, depth + 1)
            return result
        if type(item) is list and len(item) <= 64:
            return [copy(child, key, depth + 1) for child in item]
        if type(item) is bool:
            return item
        if type(item) is int and 0 <= item <= 65536:
            return item
        if type(item) is str:
            if key == 'path':
                if len(item) <= 512 and _SHAPE_PATH.fullmatch(item):
                    return item
            elif item in _SHAPE_VALUES:
                return item
        raise ValueError()

    try:
        if type(value) is not dict:
            raise ValueError()
        if ('nodes' in value and
                (type(value['nodes']) is not list or len(value['nodes']) > 16)):
            raise ValueError()
        return copy(value)
    except (ValueError, RecursionError):
        return {'status': 'invalid_structure'}


def _safe_payload_shapes(values):
    return {stage: _safe_payload_shape(values[stage]) for stage in _SHAPE_STAGES
            if stage in values} if type(values) is dict else {}


def _safe_payload_wire(value):
    if type(value) is not dict:
        return None
    result = {}
    transport = value.get('transport')
    if type(transport) is str and transport in ('android_data', 'encrypted_web'):
        result['transport'] = transport
    count = value.get('app_data_fields')
    if type(count) is int and 0 <= count <= 65:
        result['app_data_fields'] = count
    for key in ('app_data_limit_exceeded', 'raw_data_present'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    return result


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
                      'notification_callbacks': 0, 'rejected_notifications': 0,
                      'last_message_disposition': 'unobserved', 'last_transport_error': None,
                      'last_error': None, 'last_error_stage': None,
                      'cleanup_error': None, 'delivery_observed': False,
                      'phone_coexistence_verified': False,
                      'subscription_scope': 'not_checked',
                      'subscription_scope_source': 'not_checked',
                      'requested_call_events': list(CALL_EVENTS),
                      'subscription_readback': {}}

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
                    and self._receiver is not None and self._receiver.connected is True)

    @property
    def subscription_status(self):
        """Last vendor subscription result; separate from FCM readiness."""
        return self._diag['subscription_status']

    def diagnostics(self):
        self._sync_receive_state()
        self._remember_transport()
        return {**deepcopy(self._diag), 'connected': self.connected, 'transport': 'fcm',
                'reception_status': self._reception_status(),
                'scope': 'qv_calls_only', 'fcm': deepcopy(self._transport_diag),
                'events': self._filter.diagnostics() if self._filter else {},
                'automatic_connection_attempt_limit': MAX_CONNECTION_ATTEMPTS}

    def _sync_receive_state(self):
        """An accepted subscription alone never proves that MCS is receiving."""
        if self._diag['status'] not in ('connecting', 'listening'):
            return
        if self.connected:
            self._diag['status'] = 'listening'
            if self._diag['last_error_stage'] in ('fcm_connect', 'fcm_login', 'fcm_receive'):
                self._diag['last_error_stage'] = None
        elif self._closed or not self._eligible():
            self._diag['status'] = 'disconnected'
        elif self._diag['status'] == 'listening':
            self._diag['status'] = 'connecting'
            if self._diag['last_error_stage'] is None:
                self._diag['last_error_stage'] = 'fcm_receive'

    def _reception_status(self):
        """Summarize observed delivery, without claiming a physical ring test."""
        if self._closed or not self._eligible():
            return 'inactive'
        if self._receiver is None or self._receiver.connected is not True:
            return 'not_connected'
        if not self._subscribed:
            return 'subscription_not_ready'
        if (self._diag['last_error'] is not None
                and self._diag['last_error_stage'] in ('event_storage', 'event_callback')):
            return 'event_delivery_failed'
        if self._diag['delivery_observed']:
            return 'ring_received'
        if self._transport_diag.get('messages', 0) or self._diag['notification_callbacks']:
            return 'message_rejected'
        return 'waiting_for_message'

    def _remember_transport(self, receiver=None):
        receiver = self._receiver if receiver is None else receiver
        if receiver is None:
            return
        values = receiver.diagnostics()
        for name in ('registrations', 'checkins', 'messages', 'delivered', 'duplicates',
                     'malformed', 'ignored', 'callback_errors', 'acknowledged'):
            value = values.get(name)
            if type(value) is int and value >= 0:
                self._transport_diag[name] = value
        for name, allowed in (
            ('timestamp_source', ('none', 'google_sent_time', 'mcs_sent_milliseconds')),
            ('timestamp_status', ('unobserved', 'valid', 'invalid', 'conflicting', 'missing')),
            ('http_stage', (None, 'checkin', 'gcm_registration', 'installation', 'fcm_registration', 'unknown')),
            ('receive_stage', _RECEIVE_STAGES),
            ('receive_failure_stage', _RECEIVE_STAGES | {None}),
            ('last_message_disposition', _TRANSPORT_DISPOSITIONS),
            ('last_rejection_reason', _REJECTION_REASONS | {None}),
            ('last_payload_subreason', PAYLOAD_REJECTION_SUBREASONS | {None}),
            ('last_payload_branch', PAYLOAD_BRANCHES | {None}),
            ('enrollment_transport', ('android',)),
            ('credential_transport', ('none', 'web', 'android', 'invalid')),
            ('migration_status', ('not_needed', 'pending', 'running', 'complete', 'failed', 'cancelled')),
        ):
            if name in values and (values[name] is None or type(values[name]) is str) and values[name] in allowed:
                self._transport_diag[name] = values[name]
        for name in ('mcs_sent_present', 'google_sent_time_present', 'google_sent_time_valid', 'receiver_ready'):
            if type(values.get(name)) is bool:
                self._transport_diag[name] = values[name]
        for name, allowed in (
            ('rejection_counts', _REJECTION_REASONS),
            ('payload_rejection_counts', PAYLOAD_REJECTION_SUBREASONS),
            ('payload_branch_counts', PAYLOAD_BRANCHES),
            ('payload_missing_field_counts', REQUIRED_FIELDS),
        ):
            counts = values.get(name)
            if type(counts) is dict:
                self._transport_diag[name] = {key: value for key, value in counts.items()
                    if key in allowed and type(value) is int and value >= 0}
        missing = values.get('last_payload_missing_fields')
        if type(missing) in (list, tuple):
            self._transport_diag['last_payload_missing_fields'] = sorted({
                name for name in missing if type(name) is str and name in REQUIRED_FIELDS})
        if type(values.get('payload_structure_version')) is int and values['payload_structure_version'] == 1:
            self._transport_diag['payload_structure_version'] = 1
            self._transport_diag['last_payload_wire'] = _safe_payload_wire(values.get('last_payload_wire'))
            self._transport_diag['last_payload_shapes'] = _safe_payload_shapes(values.get('last_payload_shapes'))
            records = values.get('recent_payload_rejections')
            history = []
            if type(records) is list:
                for record in records[-3:]:
                    if type(record) is not dict:
                        continue
                    reason, subreason = record.get('reason'), record.get('subreason')
                    number = record.get('message_number')
                    if (type(reason) is not str or reason not in _REJECTION_REASONS or
                            type(number) is not int or not 0 <= number < 2 ** 63):
                        continue
                    history.append({'message_number': number, 'reason': reason,
                        'subreason': subreason if type(subreason) is str
                            and subreason in PAYLOAD_REJECTION_SUBREASONS else None,
                        'wire': _safe_payload_wire(record.get('wire')),
                        'stages': _safe_payload_shapes(record.get('stages'))})
            self._transport_diag['recent_payload_rejections'] = history
        status = values.get('http_status')
        if status is None or (type(status) is int and 100 <= status <= 599):
            self._transport_diag['http_status'] = status

    def _notify(self):
        self._sync_receive_state()
        if not self._closed and self._on_state:
            try:
                self._on_state()
            except Exception:
                self._diag['last_error'] = 'callback_failed'

    def _error(self, exc, stage):
        reason = getattr(exc, 'reason', None)
        if type(reason) is str and reason in _TRANSPORT_ERRORS:
            self._diag['last_transport_error'] = reason
            self._remember_transport()
            if stage in ('fcm_connect', 'fcm_receive'):
                stage = {'validating': 'fcm_credentials', 'tls_connect': 'fcm_connect',
                         'mcs_login': 'fcm_login', 'receiving': 'fcm_receive'}.get(
                             self._transport_diag.get('receive_failure_stage'), stage)
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
                    'subscriptions': [], 'device_subscription': False, 'events': None}
        expected_client = make_client_id(self._account['installation_id'], profile_for(self._variant))
        if (not isinstance(data, dict) or data.get('binding') != self._binding()
                or data.get('client_id') != expected_client
                or not isinstance(data.get('subscriptions'), list)
                or len(data['subscriptions']) > 2
                or any(type(ch) is not int or ch not in (1, 2) for ch in data['subscriptions'])
                or len(set(data['subscriptions'])) != len(data['subscriptions'])
                or type(data.get('device_subscription', False)) is not bool
                or (data.get('credentials') is not None and not isinstance(data['credentials'], dict))):
            raise QVCloudStateError()
        try:
            self._filter = QVEventFilter(self._uid, startup_time=self._started,
                                         state=data.get('events'))
        except ValueError:
            raise QVCloudStateError() from None
        # Old channel intentions retain their meaning; device scope is separate.
        data.setdefault('device_subscription', False)
        self._data = data
        if create:
            await self._save()

    async def _save(self, *, subscriptions=None, device_subscription=None,
                    credentials=_UNCHANGED_CREDENTIALS):
        task = asyncio.create_task(self._save_confirmed(
            subscriptions, device_subscription, credentials),
                                   name='welcomeeye-qv-cloud-save')
        self._save_tasks.add(task)
        try:
            await _finish_task(task, cancel_on_cancel=False)
        finally:
            self._save_tasks.discard(task)

    async def _save_confirmed(self, subscriptions, device_subscription=None,
                              credentials=_UNCHANGED_CREDENTIALS):
        async with self._save_lock:
            if self._data is None or self._store is None:
                raise QVCloudStateError()
            saved = deepcopy(self._data)
            saved['events'] = self._filter.export_state()
            if credentials is not _UNCHANGED_CREDENTIALS:
                saved['credentials'] = deepcopy(credentials)
            if subscriptions is not None:
                saved['subscriptions'] = sorted(subscriptions)
            if device_subscription is not None:
                if type(device_subscription) is not bool:
                    raise QVCloudStateError()
                saved['device_subscription'] = device_subscription
            try:
                await self._store.async_save(saved)
                await self.hass.async_add_executor_job(_verify_saved_state,
                    self.hass.config.path('.storage', self._storage_key), self._storage_key, saved)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise QVCloudStateError() from None
            self._data['events'] = saved['events']
            if credentials is not _UNCHANGED_CREDENTIALS:
                # A failed write must not replace the previous installation in
                # memory. Vendor registration only follows this durable save.
                self._data['credentials'] = saved['credentials']
            if subscriptions is not None:
                self._data['subscriptions'] = saved['subscriptions']
            if device_subscription is not None:
                self._data['device_subscription'] = saved['device_subscription']

    async def _credentials_changed(self, credentials):
        if self._closed or not self._eligible():
            raise QvCloudError('closed')
        await self._save(credentials=deepcopy(credentials))

    def _receiver_factory(self):
        from .qv_cloud_android import QVAndroidFCMReceiver
        return QVAndroidFCMReceiver(self._session, self._message, self._credentials_changed,
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
        self._subscribed = False
        self._diag.update(subscription_status='not_started', subscription_scope='not_checked',
                          subscription_scope_source='not_checked', subscription_readback={})
        self._notify()
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
        scope = subscription_scope(self._device)
        self._diag.update(
            subscription_scope=scope,
            subscription_scope_source=subscription_scope_reason(self._device),
            subscription_readback={},
        )
        targets = (None,) if scope == 'device' else tuple(sorted(self._allowed_channels))
        # Read the current state before changing this HA client's subscriptions.
        # Global query is read-only and never extends a shared account's grants.
        if not self._device.shared:
            await self._observe_subscription(None, 'before')
        for channel in targets:
            if channel is not None:
                await self._observe_subscription(channel, 'before')
        self._check_subscription_target(None)
        self._diag['last_error_stage'] = 'vendor_subscription'
        for channel in tuple(self._data['subscriptions']):
            self._check_subscription_target(None)
            if scope == 'device' or channel not in self._allowed_channels:
                await self._disable_channel(channel)
        if scope != 'device' and self._data.get('device_subscription', False):
            self._check_subscription_target(None)
            await self._disable_device()
        self._diag['subscription_status'] = 'registering'
        self._notify()
        for channel in targets:
            self._check_subscription_target(channel)
            # Persist intent before a request whose reply might be lost.
            if channel is None:
                await self._save(device_subscription=True)
            else:
                await self._save(subscriptions=set(self._data['subscriptions']) | {channel})
            self._check_subscription_target(channel)
            self._diag['last_error_stage'] = 'vendor_subscription'
            await self._client.async_subscribe(
                self._device, None if channel is None else channel - 1, True)
            self._diag['subscription_status'] = 'accepted'
            self._notify()
            state = await self._observe_subscription(channel, 'after', required=True)
            # Match the APK's isEnableCall: CALL and CALL_ACCEPTED are both set.
            if not state.enabled or not {19, 20} <= set(state.effective_call_events):
                self._diag['subscription_status'] = 'mismatch'
                raise QvCloudError('subscription_mismatch')
            self._check_subscription_target(channel)
        self._subscribed = True
        self._diag['subscription_status'] = 'confirmed'
        self._notify()

    def _check_subscription_target(self, channel):
        if self._closed or not self._eligible():
            raise QvCloudError('closed')
        if channel is not None and channel not in self._configured_channels():
            raise QvCloudError('invalid_channel')

    async def _observe_subscription(self, channel, phase, *, required=False):
        """Record only safe readback flags, never the response body or identity."""
        self._diag['last_error_stage'] = 'subscription_readback'
        key = 'device' if channel is None else f'channel_{channel}'
        observations = self._diag['subscription_readback'].setdefault(key, {})
        try:
            state = await self._client.async_query_subscription(
                self._device, None if channel is None else channel - 1)
        except QvCloudError as exc:
            observations[phase] = {'status': 'failed',
                                   'error': exc.code if exc.code in _ERRORS else 'invalid_response'}
            if required:
                self._diag['subscription_status'] = 'unverified'
                raise
            return None
        observations[phase] = {'status': 'observed', 'enabled': state.enabled,
                               'call_events': list(state.effective_call_events),
                               'detail_present': state.detail_present}
        return state

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
                    self._diag.update(status='connecting', last_error=None,
                                      last_transport_error=None, last_error_stage='fcm_connect')
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
            self._diag['notification_callbacks'] += 1
            candidate = parse_qv_ring(data, self._uid)
            if (candidate is None or candidate.channel not in self._allowed_channels
                    or candidate.channel not in self._configured_channels()):
                self._diag['ignored_messages'] += 1
                self._diag['rejected_notifications'] += 1
                self._diag['last_message_disposition'] = ('not_a_call_for_device'
                    if candidate is None else 'channel_not_enabled_or_authorized')
                self._notify()
                return
            event = self._filter.accept(data, persistent_id, sent_ms)
            if event is None:
                reason = self._filter.diagnostics().get('last_disposition')
                self._diag['rejected_notifications'] += 1
                self._diag['last_message_disposition'] = reason if reason in _FILTER_DISPOSITIONS else 'rejected_payload'
                self._notify()
                return
            try:
                await self._save()
            except QVCloudStateError as exc:
                self._diag['last_message_disposition'] = 'event_storage_failed'
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
                self._diag['last_error_stage'] = 'event_callback'
                self._diag['last_message_disposition'] = 'event_callback_failed'
                self._notify()
                return
            self._diag['rings_received'] += 1
            self._diag['delivery_observed'] = True
            self._diag['last_message_disposition'] = 'ring_emitted'
            if self._diag['last_error_stage'] in ('event_storage', 'event_callback'):
                self._diag.update(last_error=None, last_error_stage=None)
            self._notify()

    async def _disable_channel(self, channel):
        if channel - 1 not in self._device.channels:
            raise QvCloudError('unauthorized_device')
        await self._client.async_subscribe(self._device, channel - 1, False)
        state = await self._observe_subscription(channel, 'after_disable', required=True)
        if state.enabled:
            raise QvCloudError('subscription_mismatch')
        await self._save(subscriptions=set(self._data['subscriptions']) - {channel})

    async def _disable_device(self):
        await self._client.async_subscribe(self._device, None, False)
        state = await self._observe_subscription(None, 'after_disable', required=True)
        if state.enabled:
            raise QvCloudError('subscription_mismatch')
        await self._save(device_subscription=False)

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
                if self._data['subscriptions'] or self._data.get('device_subscription', False):
                    credentials = self._data.get('credentials') or {}
                    token = credentials.get('fcm', {}).get('registration', {}).get('token')
                    if not isinstance(token, str) or not token:
                        raise QVCloudStateError()
                    await self._authorize(cleanup=True)
                    await self._client.async_register(token)
                    for channel in tuple(self._data['subscriptions']):
                        await self._disable_channel(channel)
                    if self._data.get('device_subscription', False):
                        await self._disable_device()
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
        self._remember_transport(receiver)
        self._transport_diag['receiver_ready'] = False

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
