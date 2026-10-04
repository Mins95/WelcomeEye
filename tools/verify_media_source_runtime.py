"""Actual HA media browser, branding and protected JPEG delivery; no device I/O."""
import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.components import media_source
from homeassistant.components.brands import BrandsIntegrationView
from homeassistant.components.media_source import const as media_const
from homeassistant.components.media_source.local_source import LocalMediaView, LocalSource
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.core import HomeAssistant
from PIL import Image

from verify_image_runtime import load


async def main(root):
    component = root / 'custom_components/welcomeeye_local'
    scope = {'__name__': __name__, 'DOMAIN': 'welcomeeye_local', '_finish_task': None}
    load(component / 'media_storage.py', scope)
    load(component / 'media_source.py', scope)
    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        media_root = Path(temporary) / 'private_media'
        hass.config.media_dirs = {'local': str(media_root)}
        await hass.config.async_set_time_zone('Europe/Paris')
        # This file is exactly the beta.5 layout, created before source setup.
        old = media_root / 'WelcomeEye/welcomeeye_fixture/2026-10-04/2026-10-04_14-00-00_manual.jpg'
        old.parent.mkdir(parents=True)
        image = BytesIO()
        Image.new('RGB', (16, 16), 'green').save(image, format='JPEG')
        old.write_bytes(image.getvalue())
        before = (old.stat().st_mtime_ns, sha256(old.read_bytes()).hexdigest())
        source = await scope['async_get_media_source'](hass)
        local = LocalSource(hass, 'media_source', 'My media', hass.config.media_dirs, '/media')
        # Exercise both native Core registration models. Only discovery of the
        # unrelated, network-dependent component initializer is substituted.
        if hasattr(media_const, 'MEDIA_SOURCE_DATA'):
            hass.data[media_const.MEDIA_SOURCE_DATA] = {'media_source': local, source.domain: source}
        else:
            from homeassistant.helpers.integration_platform import LazyIntegrationPlatforms
            hass.data[media_const.DATA_LOCAL_SOURCE] = local
            platforms = LazyIntegrationPlatforms(hass, 'media_source', media_source._process_media_source_platform)
            hass.data[media_const.DATA_MEDIA_SOURCE_PLATFORMS] = platforms
            integration = SimpleNamespace(domain=source.domain,
                platforms_exists=lambda names: 'media_source' in names,
                async_get_platform=AsyncMock(return_value=SimpleNamespace(
                    async_get_media_source=scope['async_get_media_source'])))
            hass.config.top_level_components.add(source.domain)
        try:
            from homeassistant.components.media_source import models
            with patch.object(models, 'async_get_cached_translations', return_value={}):
                if hasattr(media_const, 'MEDIA_SOURCE_DATA'):
                    root_node = await MediaSourceItem(hass, None, '', None).async_browse()
                else:
                    with patch('homeassistant.helpers.integration_platform.async_get_integrations',
                               AsyncMock(return_value={source.domain: integration})):
                        root_node = await MediaSourceItem(hass, None, '', None).async_browse()
            tiles = [node for node in root_node.children if node.title == 'WelcomeEye']
            assert len(tiles) == 1
            assert tiles[0].media_content_id == 'media-source://welcomeeye_local'
            assert tiles[0].thumbnail == '/api/brands/integration/welcomeeye_local/logo.png'
            # Check actual Core brands lookup using the shipped files locally.
            with patch('homeassistant.components.brands.async_get_custom_components',
                       AsyncMock(return_value={source.domain: SimpleNamespace(
                           has_branding=True, file_path=component)})):
                response = await BrandsIntegrationView(hass)._serve_from_custom_integration(
                    source.domain, 'logo.png')
                assert response.content_type == 'image/png'
                assert response.body == (component / 'brand/logo.png').read_bytes()
            node = await MediaSourceItem(hass, source.domain, '', None).async_browse()
            assert node.title == 'WelcomeEye'
            assert len(node.children) == 1
            node = await MediaSourceItem.from_uri(hass, node.children[0].media_content_id, None).async_browse()
            node = await MediaSourceItem.from_uri(hass, node.children[0].media_content_id, None).async_browse()
            file_item = MediaSourceItem.from_uri(hass, node.children[0].media_content_id, None)
            resolved = await file_item.async_resolve()
            assert resolved.mime_type == 'image/jpeg'
            assert resolved.path == old
            assert resolved.url.startswith('/media/local/WelcomeEye/')
            old_uri = 'media-source://media_source/local/' + old.relative_to(media_root).as_posix()
            old_resolved = await MediaSourceItem.from_uri(hass, old_uri, None).async_resolve()
            assert old_resolved.path == resolved.path
            assert (old.stat().st_mtime_ns, sha256(old.read_bytes()).hexdigest()) == before
            # Serve exactly that URL via the real authenticated local-media view.
            assert LocalMediaView(hass, local).requires_auth is True
            request = SimpleNamespace()
            view = LocalMediaView(hass, local)
            response = await view.get(request, 'local', old.relative_to(media_root).as_posix())
            assert response._path == old
            for invalid in ('local/other.jpg', 'local/WelcomeEye/%2e%2e/other.jpg'):
                try:
                    await source.async_resolve_media(MediaSourceItem(hass, source.domain, invalid, None))
                except media_source.Unresolvable:
                    pass
                else:
                    raise AssertionError('Photo resolver escaped the WelcomeEye folder')
        finally:
            await hass.async_stop(force=True)
    print('REAL_HA_WELCOMEEYE_ROOT_TILE_BRAND_BETA5_ADOPTION_AND_PRIVATE_JPEG_OK')


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).resolve().parents[1]))
