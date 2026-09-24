"""Bounded JPEG validation and private, atomic Home Assistant Media storage."""
import asyncio
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .snapshot import _finish_task

MAX_JPEG_BYTES = 10 * 1024 * 1024
MAX_JPEG_PIXELS = 16_000_000


def _resolved_path(path):
    """Normalize Windows' optional extended prefix during concurrent mkdir."""
    value = path.resolve().as_posix()
    if os.name == 'nt' and value.startswith('//?/'):
        value = '//' + value[8:] if value.startswith('//?/UNC/') else value[4:]
    return Path(value)


def validate_jpeg(data):
    """Decode in the executor before publishing or writing any JPEG."""
    from PIL import Image

    if not isinstance(data, bytes) or not 4 <= len(data) <= MAX_JPEG_BYTES:
        raise ValueError('Invalid image size')
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise ValueError('Incomplete JPEG')
    with Image.open(BytesIO(data)) as image:
        if image.format != 'JPEG' or image.width * image.height > MAX_JPEG_PIXELS:
            raise ValueError('Invalid JPEG')
        image.load()
    return data


def new_save_diagnostics():
    return {'save_successes': 0, 'save_failures': 0,
            'last_save_error_type': None, 'last_media_filename': None}


class CaptureMediaStorage:
    """Resolve HA media_dirs; never create a public /www endpoint.

    The generated device folder uses only an opaque digest of HA's entry ID,
    never a device title, hardware UID, IP address, account name or credential.
    """

    def __init__(self, hub):
        self.hub = hub
        entry_id = str(getattr(hub.entry, 'entry_id', ''))
        self.device_slug = 'welcomeeye_' + sha256(entry_id.encode()).hexdigest()[:12]

    async def save(self, jpeg, captured_at, kind, *, sequence=None, cancelled=None):
        """Drain executor work on cancellation so no late write escapes unload."""
        cancelled = cancelled or threading.Event()
        config = self.hub.hass.config
        task = asyncio.create_task(self._save(
            jpeg, captured_at, kind, sequence, cancelled,
            dict(config.media_dirs), config.time_zone,
        ))
        try:
            await asyncio.wait({task})
            return task.result()
        except asyncio.CancelledError as cancel_error:
            cancelled.set()
            try:
                try:
                    await _finish_task(task, cancel_on_cancel=False)
                finally:
                    # A completed executor may have won the race with cancel.
                    # Its own token check then preceded cancellation: remove
                    # only this unpublished result before returning to unload.
                    if task.done() and not task.cancelled() and task.exception() is None:
                        await self.discard(task.result())
            finally:
                raise cancel_error

    async def _save(self, jpeg, captured_at, kind, sequence, cancelled, media_dirs, time_zone):
        return await self.hub.hass.async_add_executor_job(
            self._write, jpeg, captured_at, kind, sequence, cancelled, media_dirs, time_zone,
        )

    def _write(self, jpeg, captured_at, kind, sequence, cancelled, media_dirs, time_zone):
        validate_jpeg(jpeg)
        if not media_dirs:
            raise FileNotFoundError('No Home Assistant media directory configured')
        # HA's local_source uses the media_dirs key as the first URI component.
        source_id = 'local' if 'local' in media_dirs else next(iter(media_dirs))
        root = _resolved_path(Path(media_dirs[source_id]))
        local_time = captured_at.astimezone(ZoneInfo(time_zone))
        if kind not in ('ring', 'manual'):
            raise ValueError('Unknown capture kind')
        suffix = f'ring_{int(sequence)}' if kind == 'ring' else 'manual'
        directory = root / 'WelcomeEye' / self.device_slug / local_time.strftime('%Y-%m-%d')
        if not _resolved_path(directory).is_relative_to(root):
            raise ValueError('Capture directory escapes media directory')
        if cancelled.is_set():
            return None
        directory.mkdir(parents=True, exist_ok=True)
        if not _resolved_path(directory).is_relative_to(root):
            raise ValueError('Capture directory escapes media directory')
        stem = f'{local_time:%Y-%m-%d_%H-%M-%S}_{suffix}'
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.welcomeeye-', suffix='.tmp',
                                             dir=directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(jpeg)
                stream.flush()
                os.fsync(stream.fileno())
            # Exclusive linking publishes complete bytes atomically and cannot
            # overwrite another capture. Unsupported filesystems fail safely.
            for collision in range(10000):
                if cancelled.is_set():
                    return None
                tail = f'_{collision}' if collision else ''
                candidate = directory / f'{stem}{tail}.jpg'
                try:
                    os.link(temporary, candidate)
                except FileExistsError:
                    continue
                break
            else:
                raise FileExistsError('Capture filename collision limit exceeded')
            if cancelled.is_set():
                candidate.unlink(missing_ok=True)
                return None
            relative = candidate.relative_to(root).as_posix()
            return {
                'filename': relative,
                'media_content_id': f'media-source://media_source/{quote(source_id, safe="")}/'
                                    f'{quote(relative, safe="/")}',
            }
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def discard(self, result):
        """Remove only an unpublished stale capture made by this coordinator."""
        if result is None:
            return
        media_dirs = dict(self.hub.hass.config.media_dirs)
        source_id = 'local' if 'local' in media_dirs else next(iter(media_dirs))
        root = Path(media_dirs[source_id])
        path = root / result['filename']
        def remove():
            if _resolved_path(path).is_relative_to(_resolved_path(root)):
                path.unlink(missing_ok=True)
        async def discard_in_executor():
            await self.hub.hass.async_add_executor_job(remove)
        await _finish_task(asyncio.create_task(discard_in_executor()), cancel_on_cancel=False)


async def save_capture(capture, jpeg, updated, kind, *, sequence=None, cancelled=None):
    """A disk failure is diagnostic data and must preserve the captured image."""
    capture.diagnostics['last_save_error_type'] = None
    capture.diagnostics['last_media_filename'] = None
    try:
        result = await capture.storage.save(
            jpeg, updated, kind, sequence=sequence, cancelled=cancelled,
        )
    except Exception as exc:
        capture.diagnostics['save_failures'] += 1
        capture.diagnostics['last_save_error_type'] = type(exc).__name__
        return None
    if result is not None:
        capture.diagnostics['save_successes'] += 1
        capture.diagnostics['last_media_filename'] = result['filename']
    return result
