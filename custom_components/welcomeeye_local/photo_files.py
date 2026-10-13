"""Bounded private photo inventory and unlinking; all callers use an executor."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import threading

from .media_storage import MEDIA_DIRECTORY, capture_media_id, _resolved_path

MAX_PHOTOS = 10000
MAX_DAYS = 5000
DISK_LOCK = threading.RLock()


class PhotoError(ValueError):
    """Fixed reason labels only, never absolute paths."""


def device_slug(entry_id):
    return 'welcomeeye_' + sha256(str(entry_id).encode()).hexdigest()[:12]


def checked_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise PhotoError('invalid_day')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise PhotoError('invalid_day') from None
    return value


def _link(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


@dataclass(frozen=True)
class Photo:
    source: str
    slug: str
    day: str
    name: str
    signature: tuple

    @property
    def id(self):
        return sha256('\0'.join((self.source, self.slug, self.day, self.name)).encode()).hexdigest()

    @property
    def filename(self):
        return '/'.join((MEDIA_DIRECTORY, self.slug, self.day, self.name))

    def public(self):
        return dict(id=self.id, name=self.name, day=self.day,
            channel=2 if self.slug.endswith('_channel_2') else 1,
            media_content_id=capture_media_id(self.source, self.filename))


def _safe_directory(media_dirs, source, slug, day=None):
    if source not in media_dirs or not re.fullmatch(r'welcomeeye_[a-f0-9]{12}(?:_channel_[12])?', slug):
        raise PhotoError('invalid_device_directory')
    configured = Path(media_dirs[source])
    if _link(configured):
        raise PhotoError('linked_photo_directory')
    root = _resolved_path(configured)
    current = root
    for part in (MEDIA_DIRECTORY, slug, *((checked_day(day),) if day else ())):
        current = current / part
        if _link(current):
            raise PhotoError('linked_photo_directory')
        if current.exists() and not current.is_dir():
            raise PhotoError('invalid_photo_directory')
    if not _resolved_path(current).is_relative_to(root / MEDIA_DIRECTORY / slug):
        raise PhotoError('photo_path_outside_device')
    return current


def inventory(media_dirs, entry_id, day=None):
    """Only the owner's original and channel-suffixed folders, without migration."""
    if day is not None:
        checked_day(day)
    result = []
    base = device_slug(entry_id)
    seen_roots = set()
    scanned = 0
    with DISK_LOCK:
        for source in sorted(media_dirs, key=lambda key: key != 'local'):
            root = _resolved_path(Path(media_dirs[source]))
            if root in seen_roots:
                continue
            seen_roots.add(root)
            for slug in (base, base + '_channel_1', base + '_channel_2'):
                directory = _safe_directory(media_dirs, source, slug)
                if not directory.exists():
                    continue
                days = [day] if day else []
                if day is None:
                    for index, child in enumerate(directory.iterdir()):
                        if index >= MAX_DAYS:
                            raise PhotoError('photo_library_limit')
                        try:
                            checked_day(child.name)
                        except PhotoError:
                            continue
                        if not _link(child) and child.is_dir():
                            days.append(child.name)
                for selected in days:
                    folder = _safe_directory(media_dirs, source, slug, selected)
                    if not folder.exists():
                        continue
                    for index, path in enumerate(folder.iterdir()):
                        scanned += 1
                        if scanned > 30000:
                            raise PhotoError('photo_library_limit')
                        if index >= MAX_PHOTOS * 2:
                            raise PhotoError('photo_library_limit')
                        if (path.name.startswith('.') or path.suffix.lower() not in ('.jpg', '.jpeg')
                                or _link(path) or any(c in path.name for c in '\\:\0')):
                            continue
                        try:
                            info = path.lstat()
                        except FileNotFoundError:
                            continue
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            continue
                        result.append(Photo(source, slug, selected, path.name, signature(info)))
                        if len(result) > MAX_PHOTOS:
                            raise PhotoError('photo_library_limit')
    return sorted(result, key=lambda photo: (photo.day, photo.name, photo.source, photo.slug), reverse=True)


@contextmanager
def _directory_fd(root, parts):
    """Anchor each POSIX directory component against symlink substitution."""
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open(root, flags))
        for part in parts:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _unlink(media_dirs, photo):
    directory = _safe_directory(media_dirs, photo.source, photo.slug, photo.day)
    path = directory / photo.name
    if (not photo.name or photo.name.startswith('.') or '/' in photo.name or '\\' in photo.name
            or '\0' in photo.name or ':' in photo.name or path.suffix.lower() not in ('.jpg', '.jpeg')):
        raise PhotoError('invalid_photo_name')
    if os.name == 'posix':
        root = _resolved_path(Path(media_dirs[photo.source]))
        with _directory_fd(root, (MEDIA_DIRECTORY, photo.slug, photo.day)) as descriptor:
            info = os.stat(photo.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PhotoError('linked_photo_file')
            if signature(info) != photo.signature:
                raise PhotoError('photo_changed')
            os.unlink(photo.name, dir_fd=descriptor)
    else:
        if _link(path):
            raise PhotoError('linked_photo_file')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or signature(info) != photo.signature:
            raise PhotoError('photo_changed')
        # Repeat every ancestor check directly before unlink on non-dirfd hosts.
        if _safe_directory(media_dirs, photo.source, photo.slug, photo.day) != directory:
            raise PhotoError('photo_changed')
        path.unlink()


def delete_snapshot(media_dirs, entry_id, photos, cancelled=None):
    """Delete exactly the confirmed inventory, never a fresh directory wildcard."""
    if len(photos) > MAX_PHOTOS or len({p.id for p in photos}) != len(photos):
        raise PhotoError('invalid_photo_selection')
    base = device_slug(entry_id)
    deleted, missing, failures = [], [], []
    with DISK_LOCK:
        for photo in photos:
            if cancelled is not None and cancelled.is_set():
                failures.append(dict(id=photo.id, reason='cancelled'))
                continue
            if photo.slug not in (base, base + '_channel_1', base + '_channel_2'):
                failures.append(dict(id=photo.id, reason='wrong_device'))
                continue
            try:
                _unlink(media_dirs, photo)
            except FileNotFoundError:
                missing.append(photo.id)
            except PhotoError as exc:
                failures.append(dict(id=photo.id, reason=str(exc)))
            except OSError:
                failures.append(dict(id=photo.id, reason='file_error'))
            else:
                deleted.append(photo)
    return dict(deleted=deleted, missing=missing, failures=failures)
