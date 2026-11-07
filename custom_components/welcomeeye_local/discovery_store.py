"""Private, expiring hints for already authenticated legacy protected endpoints.

Store owns filesystem work through HA's executor. No credentials, diagnostic
payloads, or config-entry updates are written here. This is an availability hint;
the normal protected login is still required on every connection.
"""
import asyncio
import hashlib
import ipaddress
import json
import time

from .capabilities import DeviceVariant, variant_for
from .client import register_discovery_binding
from .protected import DiscoveryInfo

STORAGE_VERSION = 1
HINT_MAX_AGE = 24 * 60 * 60
STORE_TIMEOUT = 5
_STORE_LOCKS = {}


def storage_key(entry_id):
    return 'welcomeeye_local.discovery_endpoint.' + entry_id


def _store(hass, entry_id):
    from homeassistant.helpers.storage import Store
    return Store(hass, STORAGE_VERSION, storage_key(entry_id),
                 private=True, atomic_writes=True)


def _fingerprint(entry):
    """Private binding includes credentials without persisting their text."""
    fields = {key: entry.data.get(key) for key in (
        'host', 'username', 'password', 'device_variant', 'protocol_family',
        'detected_model', 'firmware', 'legacy_unprotected_approval')}
    fields['uid'] = entry.unique_id
    return hashlib.sha256(json.dumps(fields, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _validated_hint(data, entry, fingerprint, now):
    if (type(data) is not dict or data.get('binding') != fingerprint
            or data.get('policy') != 'lt_protected'
            or type(data.get('authenticated_at')) not in (int, float)
            or not 0 <= now - data['authenticated_at'] < HINT_MAX_AGE):
        return None
    fields = data.get('endpoint')
    if (type(fields) is not dict or fields.get('uid') != entry.unique_id
            or fields.get('address') != entry.data.get('host')
            or fields.get('protected') is not True
            or type(fields.get('uid')) is not str
            or not 1 <= len(fields['uid']) <= 32
            or not all(33 <= ord(c) < 127 for c in fields['uid'])
            or any(type(fields.get(key)) is not int or not 1 <= fields[key] <= 65535
                   for key in ('udp_port', 'tcp_port'))):
        return None
    try:
        if str(ipaddress.IPv4Address(fields['address'])) != fields['address']:
            return None
    except (ValueError, TypeError):
        return None
    return DiscoveryInfo(fields['uid'], fields['udp_port'], fields['tcp_port'],
                         fields['address'], True)


class DiscoveryStore:
    """Own one entry's registration and serialize worker-to-loop updates."""

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self._loop = asyncio.get_running_loop()
        self._store = None
        self._binding = self._unregister = None
        self._fingerprint = _fingerprint(entry)
        self._lock = _STORE_LOCKS.setdefault(storage_key(entry.entry_id), asyncio.Lock())
        self._revision = 0
        self._pending = False
        self._record = None
        self._save_task = None
        self._closed = False
        self._persist = False
        self._diag = {'hint_loaded': False, 'load_error_type': None,
                      'save_error_type': None, 'save_count': 0}

    async def start(self):
        if self._closed or self._binding is not None:
            return
        protected = not bool(self.entry.data.get('legacy_unprotected_approval'))
        self._persist = protected and variant_for(self.entry.data) in (
            DeviceVariant.V1, DeviceVariant.R001)
        hint, expires = None, 0
        self._store = _store(self.hass, self.entry.entry_id)
        if self._persist:
            try:
                async with asyncio.timeout(STORE_TIMEOUT):
                    async with self._lock:
                        data = await self._store.async_load()
                hint = _validated_hint(data, self.entry, self._fingerprint, time.time())
                if hint is not None:
                    self._record = data
                    expires = data['authenticated_at'] + HINT_MAX_AGE
                    self._diag['hint_loaded'] = True
            except Exception as exc:
                # A missing, corrupt or unreadable hint cannot prevent discovery.
                self._diag['load_error_type'] = type(exc).__name__
        self._binding, self._unregister = register_discovery_binding(
            self.entry.data['host'], self.entry.unique_id, protected, self._changed,
            hint=hint, hint_expires=expires)

    def _changed(self, revision, info):
        self._loop.call_soon_threadsafe(self._accept, revision, info)

    def _accept(self, revision, info):
        if self._closed or revision <= self._revision:
            return
        self._revision = revision
        if not self._persist:
            return
        if info is None:
            self._record = None
        else:
            candidate = {'binding': self._fingerprint, 'policy': 'lt_protected',
                'authenticated_at': time.time(), 'endpoint': {
                    'uid': info.uid, 'udp_port': info.udp_port, 'tcp_port': info.tcp_port,
                    'address': info.address, 'protected': info.protected}}
            if _validated_hint(candidate, self.entry, self._fingerprint, time.time()) is None:
                return
            self._record = candidate
        self._pending = True
        if self._save_task is None or self._save_task.done():
            self._save_task = self._loop.create_task(self._save())

    async def _save(self):
        # One shared lock also orders a slow final flush before reload/removal.
        async with self._lock:
            while self._pending:
                self._pending = False
                record = self._record
                try:
                    await self._store.async_save(record)
                    self._diag['save_count'] += 1
                    self._diag['save_error_type'] = None
                except Exception as exc:
                    self._diag['save_error_type'] = type(exc).__name__

    def detach(self):
        """Stop worker observations immediately without waiting for disk IO."""
        if self._unregister is not None:
            self._unregister()
            self._unregister = None

    async def close(self):
        self.detach()
        # Accept callbacks queued before unregister; later workers are detached.
        await asyncio.sleep(0)
        self._closed = True
        if self._save_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._save_task), STORE_TIMEOUT)
            except TimeoutError:
                # Do not cancel an executor write: it still owns the shared lock
                # and must complete before a later entry removal or replacement.
                self._diag['save_error_type'] = 'TimeoutError'

    def diagnostics(self):
        return dict(self._diag)


async def async_remove_discovery_store(hass, entry_id):
    """Remove only this private entry hint after any outstanding atomic save."""
    lock = _STORE_LOCKS.setdefault(storage_key(entry_id), asyncio.Lock())
    async with lock:
        await _store(hass, entry_id).async_remove()
