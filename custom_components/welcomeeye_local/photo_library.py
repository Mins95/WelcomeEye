"""Private photo administration, confirmed snapshots and daily retention."""
import asyncio
from datetime import date, datetime, timedelta, timezone
import secrets
import re
import threading
import time
from zoneinfo import ZoneInfo

from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .photo_files import MAX_PHOTOS, PhotoError, checked_day, delete_snapshot, inventory
from .snapshot import _finish_task

DATA_PHOTOS = DOMAIN + '_photos'
RETENTION_KEY = 'photo_retention_days'
RETENTION_DAYS = (0, 7, 30, 90)
CONFIRMATION_TTL = 120
MAX_PLANS = 20


def retention_days(data):
    value = data.get(RETENTION_KEY, 0)
    return value if type(value) is int and value in RETENTION_DAYS else 0


def expired_photo(photo, now, days, time_zone):
    cutoff = now - timedelta(days=days)
    local_cutoff = cutoff.astimezone(ZoneInfo(time_zone))
    photo_day = date.fromisoformat(photo.day)
    if photo_day != local_cutoff.date():
        return photo_day < local_cutoff.date()
    match = re.fullmatch(re.escape(photo.day) +
        r'_(\d{2})-(\d{2})-(\d{2})_(?:manual|ring_\d+)(?:_\d+)?\.(?:jpg|jpeg)', photo.name, re.I)
    if match:
        try:
            captured = datetime.fromisoformat(photo.day).replace(
                hour=int(match[1]), minute=int(match[2]), second=int(match[3]), tzinfo=ZoneInfo(time_zone))
        except ValueError:
            return False
        return captured.astimezone(timezone.utc) < cutoff
    # Older filenames without a capture time retain the boundary day safely.
    return False


class PhotoLibrary:
    """Only a loaded entry's opaque photo folders, including both panels."""

    def __init__(self, hass, hub):
        self.hass, self.hub = hass, hub
        self.entry = hub.entry
        self._store = Store(hass, 1, DOMAIN + '.photo_retention.' + self.entry.entry_id)
        self._remove_timer = None
        self._closed = False
        self._cancelled = threading.Event()
        self._tasks = set()
        self._lock = asyncio.Lock()
        self.diagnostics = dict(retention_days=retention_days(self.entry.data),
            last_cleanup_run=None, last_cleanup_deleted=0, last_cleanup_missing=0,
            last_cleanup_failures=0, last_cleanup_error=None, manual_deleted=0)

    async def start(self):
        previous = await self._store.async_load() or {}
        self.diagnostics['last_cleanup_run'] = previous.get('last_cleanup_run')
        for key in ('last_cleanup_deleted', 'last_cleanup_missing', 'last_cleanup_failures'):
            value = previous.get(key, 0)
            if type(value) is int and 0 <= value <= MAX_PHOTOS:
                self.diagnostics[key] = value
        # Scheduling never runs cleanup during installation or browsing.
        if retention_days(self.entry.data):
            self._remove_timer = async_track_time_change(self.hass, self._scheduled_cleanup,
                hour=3, minute=10, second=0)

    async def _scheduled_cleanup(self, now):
        if not self._closed:
            task = asyncio.current_task()
            self._tasks.add(task)
            try:
                await self.cleanup(now)
            finally:
                self._tasks.discard(task)

    def _roots(self):
        return dict(self.hass.config.media_dirs)

    async def list(self, day=None):
        if self._closed:
            raise PhotoError('device_unavailable')
        return await self.hass.async_add_executor_job(inventory, self._roots(), self.entry.entry_id, day)

    def _reconcile(self, photos):
        """Drop stale file URLs; preserve the actual last captured JPEG in memory."""
        removed = {photo.public()['media_content_id'] for photo in photos}
        changed = False
        owners = (self.hub, getattr(self.hub, 'channel2', None))
        for owner in owners:
            if owner is None:
                continue
            for name in ('ring_image', 'manual_snapshot'):
                capture = getattr(owner, name, None)
                if capture is None:
                    continue
                if getattr(capture, 'media_content_id', None) in removed:
                    capture.filename = capture.media_content_id = None
                    capture.diagnostics['last_media_filename'] = None
                    changed = True
        if changed:
            self.hub._notify()

    async def delete(self, photos, roots):
        """Drain one executor transaction on cancellation and integration unload."""
        if self._closed or roots != self._roots():
            raise PhotoError('photo_configuration_changed')
        async def work():
            async with self._lock:
                if self._closed:
                    raise PhotoError('device_unavailable')
                result = await self.hass.async_add_executor_job(delete_snapshot,
                    roots, self.entry.entry_id, photos, self._cancelled)
                missing = set(result['missing'])
                self._reconcile([*result['deleted'], *(p for p in photos if p.id in missing)])
                return result
        task = asyncio.create_task(work(), name='welcomeeye-photo-delete')
        self._tasks.add(task)
        try:
            return await _finish_task(task, cancel_on_cancel=False)
        finally:
            self._tasks.discard(task)

    async def cleanup(self, now=None):
        """At most one attempt in 24h, persisted across restart and reload."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        days = retention_days(self.entry.data)
        if self._closed or not days:
            return None
        async with self._lock:
            previous = self.diagnostics['last_cleanup_run']
            try:
                last = datetime.fromisoformat(previous) if previous else None
                if last and (now - last).total_seconds() < 86400:
                    return None
            except (TypeError, ValueError):
                # Invalid persisted state must not authorize an early cleanup.
                self.diagnostics['last_cleanup_error'] = 'invalid_cleanup_state'
                return None
            stamp = now.isoformat()
            # Persist BEFORE deletion; storage failure refuses this attempt.
            try:
                await self._store.async_save({'last_cleanup_run': stamp})
            except Exception:
                self.diagnostics['last_cleanup_error'] = 'retention_state_save_failed'
                return None
            self.diagnostics.update(last_cleanup_run=stamp, last_cleanup_deleted=0,
                last_cleanup_missing=0, last_cleanup_failures=0, last_cleanup_error=None)
        # Keep cutoff on folder date, retaining the partially eligible boundary
        # day. Older-format JPEG names work without relying on filesystem mtime.
        try:
            photos = await self.list()
            selected = [photo for photo in photos if expired_photo(photo, now, days, self.hass.config.time_zone)]
            result = await self.delete(selected, self._roots())
        except (PhotoError, OSError):
            self.diagnostics['last_cleanup_error'] = 'retention_cleanup_failed'
            return None
        self.diagnostics.update(last_cleanup_deleted=len(result['deleted']),
            last_cleanup_missing=len(result['missing']), last_cleanup_failures=len(result['failures']),
            last_cleanup_error='file_errors' if result['failures'] else None)
        try:
            await self._store.async_save({key: value for key, value in self.diagnostics.items()
                                         if key.startswith('last_cleanup_')})
        except Exception:
            self.diagnostics['last_cleanup_error'] = 'retention_result_save_failed'
        return self.public_result(result, len(selected))

    @staticmethod
    def public_result(result, requested):
        return dict(requested=requested, deleted=len(result['deleted']),
            missing=len(result['missing']), failed=len(result['failures']), failures=result['failures'])

    async def close(self):
        self._closed = True
        self._cancelled.set()
        if self._remove_timer:
            self._remove_timer()
            self._remove_timer = None
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


class PhotoManager:
    """Confirmation tokens are single-use, owner-bound and short-lived."""

    def __init__(self, hass):
        self.hass = hass
        self.libraries = {}
        self._plans = {}

    async def attach(self, hub):
        library = PhotoLibrary(self.hass, hub)
        await library.start()
        self.libraries[hub.entry.entry_id] = library
        hub.photo_library = library
        return library

    async def detach(self, entry_id):
        library = self.libraries.pop(entry_id, None)
        for token, plan in list(self._plans.items()):
            if plan['entry_id'] == entry_id:
                self._plans.pop(token)
        if library is not None:
            await library.close()

    def library(self, entry_id):
        if entry_id not in self.libraries:
            raise PhotoError('device_unavailable')
        return self.libraries[entry_id]

    async def browse(self, entry_id, day=None):
        library = self.library(entry_id)
        photos = await library.list(day)
        days = {}
        for photo in photos:
            days[photo.day] = days.get(photo.day, 0) + 1
        return dict(days=[{'day': day, 'count': count} for day, count in sorted(days.items(), reverse=True)],
            photos=[photo.public() for photo in photos] if day else [], count=len(photos),
            retention_days=retention_days(library.entry.data))

    async def prepare(self, user_id, entry_id, scope, day=None, ids=None):
        if scope not in ('selection', 'day', 'device'):
            raise PhotoError('invalid_delete_scope')
        if scope == 'day' and day is None:
            raise PhotoError('invalid_day')
        if scope == 'selection' and (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_PHOTOS
                or any(not isinstance(value, str) or len(value) != 64 for value in ids)
                or len(set(ids)) != len(ids)):
            raise PhotoError('invalid_photo_selection')
        library = self.library(entry_id)
        roots = library._roots()
        photos = await library.list(day if scope in ('day', 'selection') else None)
        if scope == 'selection':
            selected = set(ids)
            photos = [photo for photo in photos if photo.id in selected]
            if len(photos) != len(selected):
                raise PhotoError('photo_selection_changed')
        now = time.monotonic()
        self._plans = {key: value for key, value in self._plans.items() if value['expires'] > now}
        if len(self._plans) >= MAX_PLANS:
            raise PhotoError('confirmation_limit')
        token = secrets.token_urlsafe(32)
        self._plans[token] = dict(user_id=user_id, entry_id=entry_id, photos=photos,
            roots=roots, expires=now + CONFIRMATION_TTL, library=library)
        return dict(token=token, count=len(photos), scope=scope, expires_in=CONFIRMATION_TTL)

    async def confirm(self, user_id, token):
        plan = self._plans.get(token)
        if plan is None or plan['expires'] <= time.monotonic() or plan['user_id'] != user_id:
            raise PhotoError('confirmation_invalid_or_expired')
        self._plans.pop(token)
        library = self.library(plan['entry_id'])
        if plan['library'] is not library:
            raise PhotoError('photo_configuration_changed')
        result = await library.delete(plan['photos'], plan['roots'])
        library.diagnostics['manual_deleted'] += len(result['deleted'])
        return library.public_result(result, len(plan['photos']))

    def cancel(self, user_id, token):
        plan = self._plans.get(token)
        if plan is not None and plan['user_id'] == user_id:
            self._plans.pop(token)
        return {'cancelled': True}


def manager(hass):
    return hass.data.setdefault(DATA_PHOTOS, PhotoManager(hass))
