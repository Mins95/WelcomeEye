"""WelcomeEye photos as a native, authenticated Home Assistant media source.

Beta.5 already saves under <media_dir>/WelcomeEye. Adopt those files in place:
the native source changes navigation, not their bytes, names or old local URLs.
"""
from pathlib import Path
from urllib.parse import quote, unquote

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source.error import MediaSourceError, Unresolvable
from homeassistant.components.media_source.local_source import LocalSource
from homeassistant.components.media_source.models import (
    BrowseMediaSource, MediaSource, MediaSourceItem, PlayMedia,
)

from .const import DOMAIN
from .media_storage import MEDIA_DIRECTORY, _resolved_path

BRAND_LOGO = f'/api/brands/integration/{DOMAIN}/logo.png'


async def async_get_media_source(hass):
    """Discovered by HA's native media-source platform loader."""
    return WelcomeEyeMediaSource(hass)


def _blocked_link(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _checked_path(media_dirs, source_id, parts):
    """Check every component in the executor; expose only our private folder."""
    if source_id not in media_dirs or not parts or parts[0] != MEDIA_DIRECTORY:
        raise ValueError('Unknown WelcomeEye media directory')
    if any(not part or part in ('.', '..') or part.startswith('.')
           or any(char in part for char in '/\\\x00:') for part in parts):
        raise ValueError('Invalid WelcomeEye media path')
    root = _resolved_path(Path(media_dirs[source_id]))
    current = root
    for part in parts:
        current = current / part
        if _blocked_link(current):
            raise ValueError('Linked WelcomeEye media path')
    if not _resolved_path(current).is_relative_to(root / MEDIA_DIRECTORY):
        raise ValueError('WelcomeEye media path escapes its folder')
    return current


def _identifier(source_id, parts):
    return '/'.join(quote(part, safe='') for part in (source_id, *parts))


class WelcomeEyeMediaSource(MediaSource):
    """Browse existing and new saved photos without contacting an intercom."""

    name = 'WelcomeEye'

    def __init__(self, hass):
        super().__init__(DOMAIN)
        self.hass = hass

    def _parse(self, item):
        if item.domain != DOMAIN or not item.identifier:
            raise ValueError('Invalid WelcomeEye media identifier')
        parts = [unquote(part) for part in item.identifier.split('/')]
        if len(parts) < 2:
            raise ValueError('Invalid WelcomeEye media identifier')
        return parts[0], parts[1:]

    async def async_resolve_media(self, item):
        """Delegate JPEG delivery to HA's existing authenticated /media view."""
        try:
            source_id, parts = self._parse(item)
            media_dirs = dict(self.hass.config.media_dirs)
            path = await self.hass.async_add_executor_job(
                self._resolve_file, media_dirs, source_id, parts,
            )
        except (ValueError, OSError) as exc:
            raise Unresolvable('WelcomeEye photo unavailable') from exc
        local_item = MediaSourceItem(
            self.hass, 'media_source', '/'.join((source_id, *parts)), item.target_media_player,
        )
        local_source = LocalSource(self.hass, 'media_source', 'My media', media_dirs, '/media')
        resolved = await local_source.async_resolve_media(local_item)
        return PlayMedia(quote(resolved.url, safe='/'), 'image/jpeg', path=path)

    @staticmethod
    def _resolve_file(media_dirs, source_id, parts):
        path = _checked_path(media_dirs, source_id, parts)
        if path.suffix.lower() not in ('.jpg', '.jpeg') or not path.is_file():
            raise ValueError('Not a saved WelcomeEye photo')
        return path

    async def async_browse_media(self, item):
        try:
            if item.domain != DOMAIN:
                raise ValueError('Unknown WelcomeEye media domain')
            location = self._parse(item) if item.identifier else None
            return await self.hass.async_add_executor_job(
                self._browse, dict(self.hass.config.media_dirs), location,
            )
        except (ValueError, OSError) as exc:
            raise MediaSourceError('WelcomeEye media unavailable') from exc

    def _directory(self, identifier, title, children):
        return BrowseMediaSource(
            domain=DOMAIN, identifier=identifier, title=title,
            media_class=MediaClass.DIRECTORY, media_content_type=None,
            can_play=False, can_expand=True, children=children,
            thumbnail=BRAND_LOGO if title == self.name else None,
        )

    def _browse(self, media_dirs, location):
        if location is None:
            if len(media_dirs) == 1:
                source_id = next(iter(media_dirs))
                children = self._children(media_dirs, source_id, [MEDIA_DIRECTORY])
            else:
                children = [self._directory(
                    _identifier(source_id, [MEDIA_DIRECTORY]), source_id, [],
                ) for source_id in sorted(media_dirs)]
            return self._directory('', self.name, children)
        source_id, parts = location
        return self._directory(
            _identifier(source_id, parts),
            self.name if parts == [MEDIA_DIRECTORY] else parts[-1],
            self._children(media_dirs, source_id, parts),
        )

    def _children(self, media_dirs, source_id, parts):
        directory = _checked_path(media_dirs, source_id, parts)
        if not directory.exists():
            if parts == [MEDIA_DIRECTORY]:
                return []
            raise ValueError('WelcomeEye media folder unavailable')
        if not directory.is_dir():
            raise ValueError('Not a WelcomeEye media folder')
        children = []
        # Reverse chronological within each group; directories before JPEGs.
        for child in sorted(directory.iterdir(), key=lambda path: path.name, reverse=True):
            if child.name.startswith('.') or _blocked_link(child):
                continue
            child_parts = [*parts, child.name]
            try:
                path = _checked_path(media_dirs, source_id, child_parts)
            except ValueError:
                continue
            identifier = _identifier(source_id, child_parts)
            if path.is_dir():
                children.append(self._directory(identifier, child.name, []))
            elif path.is_file() and path.suffix.lower() in ('.jpg', '.jpeg'):
                children.append(BrowseMediaSource(
                    domain=DOMAIN, identifier=identifier, title=child.name,
                    media_class=MediaClass.IMAGE, media_content_type='image/jpeg',
                    can_play=True, can_expand=False,
                ))
        return sorted(children, key=lambda node: not node.can_expand)
