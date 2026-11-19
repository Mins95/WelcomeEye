"""Bounded JPEG validation and private, atomic Home Assistant Media storage."""
import asyncio
from contextlib import contextmanager, ExitStack
import errno
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .snapshot import _finish_task

MAX_JPEG_BYTES = 10 * 1024 * 1024
MAX_JPEG_PIXELS = 16_000_000
MEDIA_DIRECTORY = 'WelcomeEye'
MEDIA_DISK_LOCK = threading.RLock()


def capture_media_id(source_id, relative):
    """Expose the existing private files through the WelcomeEye source."""
    return (f'media-source://welcomeeye_local/{quote(source_id, safe="")}/'
            f'{quote(relative, safe="/")}')


def _resolved_path(path):
    """Normalize Windows' optional extended prefix during concurrent mkdir."""
    value = path.resolve().as_posix()
    if os.name == 'nt' and value.startswith('//?/'):
        value = '//' + value[8:] if value.startswith('//?/UNC/') else value[4:]
    return Path(value)


def _checked_directory(path):
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise ValueError('Linked capture directory')
    if path.exists() and not path.is_dir():
        raise ValueError('Invalid capture directory')


@contextmanager
def _directory_fd(root, parts, *, create=False):
    """Anchor every private path component for POSIX writes and deletions."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as handles:
        descriptor = os.open(root, flags)
        handles.callback(os.close, descriptor)
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            descriptor = os.open(part, flags, dir_fd=descriptor)
            handles.callback(os.close, descriptor)
        yield descriptor


@contextmanager
def _capture_folder(root, slug, day, *, create, allow_root_creation=False):
    """Pin private directories on Linux. Never resolve a sibling as the owner."""
    _checked_directory(root)
    if create and allow_root_creation:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise FileNotFoundError('Configured media directory unavailable')
    parts = (MEDIA_DIRECTORY, slug, day)
    directory = root.joinpath(*parts)
    if sys.platform.startswith('linux'):
        try:
            with _directory_fd(root, parts, create=create) as descriptor:
                # The descriptor keeps tempfile, link and cleanup in the same
                # opened directory across renames.
                yield Path('/proc/self/fd') / str(descriptor)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError('Linked or invalid capture directory') from None
            raise
    else:
        current = root
        for part in parts:
            current = current / part
            _checked_directory(current)
            if create:
                current.mkdir(exist_ok=True)
        if _resolved_path(directory) != _resolved_path(root).joinpath(*parts):
            raise ValueError('Capture directory changed')
        yield directory


def _publish_file(source, destination):
    """Publish exclusively. Return True when publication consumes the temporary name."""
    try:
        os.link(source, destination)
        return False
    except OSError as exc:
        if (exc.errno not in (errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM)
                and getattr(exc, 'winerror', None) not in (1, 50)):
            raise
    if os.name == 'nt':
        # Windows rename is exclusive by default, including on FAT volumes.
        os.rename(source, destination)
        return True
    if sys.platform.startswith('linux'):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        args = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        # AT_FDCWD, RENAME_NOREPLACE (Linux UAPI).
        if rename is not None:
            rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            result = rename(*args)
        else:
            # Older libc builds can omit the wrapper despite kernel support.
            # Linux x86_64 syscall_64.tbl and the arm64 generic syscall table.
            number = {'x86_64': 316, 'aarch64': 276}.get(os.uname().machine)
            if number is None or ctypes.sizeof(ctypes.c_void_p) != 8:
                raise OSError(errno.ENOTSUP, 'Exclusive file publication unavailable')
            libc.syscall.restype = ctypes.c_long
            result = libc.syscall(ctypes.c_long(number), *args)
        if result == 0:
            return True
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    raise OSError(errno.ENOTSUP, 'Exclusive file publication unavailable')


def _file_identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Invalid capture file')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


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
        self._destination = None
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
        # Directory pruning must not race mkdir and temporary-file creation.
        with MEDIA_DISK_LOCK:
            return self._write_locked(jpeg, captured_at, kind, sequence, cancelled, media_dirs, time_zone)

    def _write_locked(self, jpeg, captured_at, kind, sequence, cancelled, media_dirs, time_zone):
        if not media_dirs:
            raise FileNotFoundError('No Home Assistant media directory configured')
        # HA's local_source uses the media_dirs key as the first URI component.
        selected = getattr(self.hub.entry, 'data', {}).get('photo_media_source', '')
        source_id = selected or ('local' if 'local' in media_dirs else next(iter(media_dirs)))
        if source_id not in media_dirs:
            raise FileNotFoundError('Selected media directory unavailable')
        root = Path(media_dirs[source_id])
        local_time = captured_at.astimezone(ZoneInfo(time_zone))
        if kind not in ('ring', 'manual'):
            raise ValueError('Unknown capture kind')
        suffix = f'ring_{int(sequence)}' if kind == 'ring' else 'manual'
        if cancelled.is_set():
            return None
        day = local_time.strftime('%Y-%m-%d')
        stem = f'{local_time:%Y-%m-%d_%H-%M-%S}_{suffix}'
        if selected:
            info = root.lstat()
            destination = (source_id, info.st_dev, info.st_ino)
            if self._destination and self._destination[0] == source_id and self._destination != destination:
                raise OSError('Selected media volume changed')
        with _capture_folder(root, self.device_slug, day, create=True, allow_root_creation=not selected) as directory:
            if selected:
                self._destination = destination
            result = self._publish(jpeg, directory, stem, cancelled)
            if result is None:
                return None
            name, identity = result
            # The folder may have been renamed while the temporary file was
            # being flushed. Keep the original handle for cleanup in that case.
            try:
                with _capture_folder(root, self.device_slug, day, create=False) as visible:
                    if not visible.samefile(directory):
                        raise ValueError('Capture directory changed')
            except (ValueError, OSError):
                if _file_identity(directory / name) == identity:
                    (directory / name).unlink()
                raise
            relative = '/'.join((MEDIA_DIRECTORY, self.device_slug, day, name))
            return {'filename': relative, 'media_content_id': capture_media_id(source_id, relative),
                    '_source': source_id, '_identity': identity}

    @staticmethod
    def _publish(jpeg, directory, stem, cancelled):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.welcomeeye-', suffix='.tmp',
                                             dir=directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(jpeg)
                stream.flush()
                os.fsync(stream.fileno())
            for collision in range(10000):
                if cancelled.is_set():
                    return None
                tail = f'_{collision}' if collision else ''
                candidate = directory / f'{stem}{tail}.jpg'
                try:
                    if _publish_file(temporary, candidate):
                        temporary = None
                except FileExistsError:
                    continue
                break
            else:
                raise FileExistsError('Capture filename collision limit exceeded')
            identity = _file_identity(candidate)
            if cancelled.is_set():
                if _file_identity(candidate) == identity:
                    candidate.unlink(missing_ok=True)
                return None
            return candidate.name, identity
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def discard(self, result):
        """Remove only an unpublished stale capture made by this coordinator."""
        if result is None:
            return
        media_dirs = dict(self.hub.hass.config.media_dirs)
        def remove():
            parts = result['filename'].split('/')
            source = result.get('_source')
            if source not in media_dirs or len(parts) != 4 or parts[:2] != [MEDIA_DIRECTORY, self.device_slug]:
                return
            from datetime import date
            day, name = parts[2:]
            if (date.fromisoformat(day).isoformat() != day or not name.startswith(day+'_')
                    or not name.endswith('.jpg') or any(ch in name for ch in '/\\:\0')):
                return
            with MEDIA_DISK_LOCK:
                try:
                    with _capture_folder(Path(media_dirs[source]), self.device_slug, day, create=False) as directory:
                        path = directory / name
                        if _file_identity(path) == result.get('_identity'):
                            path.unlink()
                except FileNotFoundError:
                    pass
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
