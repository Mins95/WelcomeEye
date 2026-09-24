"""Explicit fresh snapshots without touching the last visitor photo."""
import asyncio
from datetime import datetime, timezone
import threading

from homeassistant.exceptions import HomeAssistantError

from .media_storage import CaptureMediaStorage, new_save_diagnostics, save_capture, validate_jpeg
from .snapshot import capture_fresh_image, _finish_task


class ManualSnapshotCapture:
    def __init__(self, hub):
        self.hub = hub
        self.storage = CaptureMediaStorage(hub)
        self.jpeg = self.updated = self.entity_id = None
        self.media_content_id = self.filename = None
        self.source = 'fresh_snapshot'
        self.status = 'idle'
        self._task = None
        self._closed = False
        self._save_cancelled = threading.Event()
        self.diagnostics = {'requests': 0, 'successes': 0, 'failures': 0,
                            'last_error_type': None, **new_save_diagnostics()}

    async def capture(self, *, save_to_media=True):
        if self._closed or self.hub.stopped:
            raise HomeAssistantError('WelcomeEye is unavailable')
        if self._task is not None and not self._task.done():
            raise HomeAssistantError('A WelcomeEye snapshot is already in progress')
        self.diagnostics['requests'] += 1
        self._save_cancelled = threading.Event()
        self._task = asyncio.create_task(self._capture(save_to_media), name='welcomeeye-manual-snapshot')
        return await _finish_task(self._task, cancel_on_cancel=True)

    async def _capture(self, save_to_media):
        self.status = 'pending'
        self.diagnostics['last_error_type'] = None
        self.diagnostics['last_save_error_type'] = None
        self.diagnostics['last_media_filename'] = None
        try:
            data = await capture_fresh_image(self.hub)
            if data is None:
                raise TimeoutError
            data = await self.hub.hass.async_add_executor_job(validate_jpeg, data)
            if self._closed or self.hub.stopped:
                raise HomeAssistantError('WelcomeEye stopped during capture')
            updated = datetime.now(timezone.utc)
            result = None
            if save_to_media:
                result = await save_capture(self, data, updated, 'manual', cancelled=self._save_cancelled)
            if self._closed or self.hub.stopped:
                await self.storage.discard(result)
                raise HomeAssistantError('WelcomeEye stopped during capture')
            self.jpeg, self.updated = data, updated
            self.media_content_id = result['media_content_id'] if result else None
            self.filename = result['filename'] if result else None
            self.status = 'ready'
            self.diagnostics['successes'] += 1
            self.hub._notify()
            response = {
                'saved': result is not None, 'media_content_id': self.media_content_id,
                'filename': self.filename, 'image_entity_id': self.entity_id,
                'captured_at': updated.isoformat(),
                'save_error': self.diagnostics['last_save_error_type'],
            }
            if result is not None:
                self.hub.hass.bus.async_fire('welcomeeye_local.snapshot_saved', {
                    'entry_id': self.hub.entry.entry_id, 'source': self.source, **response,
                })
            return response
        except asyncio.CancelledError:
            self.status = 'cancelled'
            raise
        except Exception as exc:
            self.status = 'failed'
            self.diagnostics['failures'] += 1
            self.diagnostics['last_error_type'] = type(exc).__name__
            if not self._closed and not self.hub.stopped:
                self.hub._notify()
            raise HomeAssistantError(f'Unable to capture a fresh WelcomeEye image ({type(exc).__name__})') from exc

    async def close(self):
        self._closed = True
        self._save_cancelled.set()
        self.entity_id = None
        if self._task is not None:
            task = self._task
            try:
                if not task.cancelled():
                    await _finish_task(task, cancel_on_cancel=False)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except HomeAssistantError:
                pass  # The service caller receives the bounded capture failure.
            finally:
                if task.done():
                    self._task = None
