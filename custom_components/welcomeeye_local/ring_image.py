"""Opt-in ring photos, serialized on the existing shared media worker."""
import asyncio
from datetime import datetime, timezone
import threading
import time

from .media_storage import CaptureMediaStorage, new_save_diagnostics, save_capture, validate_jpeg
from .snapshot import capture_fresh_image, _finish_task

CAPTURE_TIMEOUT = 20.0
RING_IMAGE_FALLBACK_DELAY = 4.0
OPTION_RING_IMAGE_CAPTURE = 'ring_image_capture'


class RingImageCapture:
    """One bounded capture; newer rings supersede unpublished results."""

    def __init__(self, hub):
        self.hub = hub
        self.storage = CaptureMediaStorage(hub)
        self.enabled = (getattr(hub.entry, 'options', {}).get(OPTION_RING_IMAGE_CAPTURE) is True
                        and hub.capabilities.ring_image_capture)
        self.jpeg = self.updated = self.source = self.image_sequence = None
        self.media_content_id = self.filename = None
        self.sequence = 0
        self.status = 'idle' if self.enabled else 'disabled'
        self.entity_id = None
        self._pending = self._task = self._timer = None
        self._closed = False
        self._revision = 0
        self._save_cancelled = None
        self._clock = time.monotonic
        self._call_later = hub.loop.call_later
        self._wake = asyncio.Event()
        self.diagnostics = {
            'enabled': self.enabled, 'ring_capture_requests': 0,
            'ring_capture_successes': 0, 'ring_capture_failures': 0,
            'ring_capture_superseded': 0, 'last_ring_capture_source': None,
            'last_ring_capture_elapsed_ms': None, 'ring_image_generation': 0,
            'last_error_type': None, 'fallback_delay_seconds': RING_IMAGE_FALLBACK_DELAY,
            'last_fallback_started_ms': None, **new_save_diagnostics(),
        }

    def _invalidate(self):
        self._revision += 1
        self._pending = None
        if self._save_cancelled is not None:
            self._save_cancelled.set()
        if self._timer is not None:
            self._timer.cancel()
        self._wake.set()

    def set_enabled(self, enabled):
        """Switch writes persistent entry options; this method changes runtime."""
        enabled = bool(enabled and self.hub.local_ring_supported)
        if self._closed or self.enabled == enabled:
            return
        self.enabled = bool(enabled)
        self.diagnostics['enabled'] = self.enabled
        self._invalidate()
        self.status = 'idle' if self.enabled else 'disabled'
        self.hub._notify()

    def request(self, sequence, message):
        """Called after the immediate ring event; OFF does no photo work."""
        if self._closed or not self.enabled or not self.hub.local_ring_supported:
            return
        if self._pending is not None:
            self.diagnostics['ring_capture_superseded'] += 1
        self._invalidate()
        self.sequence = sequence
        self.diagnostics['ring_capture_requests'] += 1
        self._pending = (sequence, self._revision, self._clock())
        self.status = 'pending'
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name='welcomeeye-ring-image')

    def _current(self, revision):
        return (not self._closed and self.enabled and not self.hub.stopped and self.hub.local_ring_supported
                and revision == self._revision)

    async def _wait_fallback(self, revision, deadline):
        while self._current(revision):
            remaining = deadline - self._clock()
            if remaining <= 0:
                return True
            self._wake.clear()
            timer = self._call_later(remaining, self._wake.set)
            self._timer = timer
            try:
                await self._wake.wait()
            finally:
                timer.cancel()
                if self._timer is timer:
                    self._timer = None
        return False

    async def _run(self):
        while self._pending is not None and not self._closed:
            sequence, revision, started = self._pending
            self._pending = None
            data = result = None
            self.diagnostics['last_error_type'] = None
            self.diagnostics['last_fallback_started_ms'] = None
            try:
                if not await self._wait_fallback(revision, started + RING_IMAGE_FALLBACK_DELAY):
                    self.diagnostics['ring_capture_superseded'] += 1
                    continue
                remaining = CAPTURE_TIMEOUT - (self._clock() - started)
                if remaining <= 0:
                    raise TimeoutError
                self.diagnostics['last_fallback_started_ms'] = round((self._clock() - started) * 1000)
                data = await capture_fresh_image(
                    self.hub, timeout=min(15.0, remaining), single_session_attempt=True,
                )
                if data is None:
                    raise TimeoutError
                data = await self.hub.hass.async_add_executor_job(validate_jpeg, data)
                if self._clock() - started > CAPTURE_TIMEOUT:
                    raise TimeoutError
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                data = None
                self.diagnostics['last_error_type'] = type(exc).__name__
            if not self._current(revision):
                self.diagnostics['ring_capture_superseded'] += 1
                continue
            self.diagnostics['last_ring_capture_elapsed_ms'] = round((self._clock() - started) * 1000)
            if data is None:
                self.status = 'failed'
                self.diagnostics['ring_capture_failures'] += 1
                self.hub._notify()
                continue
            updated = datetime.now(timezone.utc)
            cancelled = self._save_cancelled = threading.Event()
            result = await save_capture(self, data, updated, 'ring', sequence=sequence, cancelled=cancelled)
            if not self._current(revision):
                try:
                    await self.storage.discard(result)
                except Exception as exc:
                    self.diagnostics['save_failures'] += 1
                    self.diagnostics['last_save_error_type'] = type(exc).__name__
                else:
                    if result is not None:
                        self.diagnostics['save_successes'] -= 1
                        self.diagnostics['last_media_filename'] = None
                self.diagnostics['ring_capture_superseded'] += 1
                continue
            self._save_cancelled = None
            self.jpeg, self.source, self.image_sequence = data, 'fresh_snapshot', sequence
            self.updated = updated
            self.media_content_id = result['media_content_id'] if result else None
            self.filename = result['filename'] if result else None
            self.status = 'ready'
            self.diagnostics['ring_capture_successes'] += 1
            self.diagnostics['last_ring_capture_source'] = self.source
            self.diagnostics['ring_image_generation'] += 1
            self.hub._notify()
            if self.entity_id is not None:
                self.hub.hass.bus.async_fire('welcomeeye_local.ring_image', {
                    'entry_id': self.hub.entry.entry_id,
                    'image_entity_id': self.entity_id, 'ring_sequence': sequence,
                    'source': self.source, 'media_content_id': self.media_content_id,
                    'filename': self.filename,
                })

    async def close(self):
        """Suppress publication immediately and drain any owned media lease."""
        self._closed = True
        self.entity_id = None
        self._invalidate()
        if self._task is not None:
            task = self._task
            try:
                if not task.cancelled():
                    await _finish_task(task, cancel_on_cancel=False)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            finally:
                if task.done():
                    self._task = None
