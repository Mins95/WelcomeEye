"""Real HA 2026.9 APIs with an offline frame fixture, never a device session."""
import asyncio
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.components.media_source.local_source import LocalMediaView, LocalSource
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES
from PIL import Image

from verify_image_runtime import load


async def main(root):
    component = root / 'custom_components/welcomeeye_local'
    scope = {'__name__': __name__, 'WebRTCManager': lambda hub: None}
    for module in ('capabilities', 'const', 'snapshot', 'media_storage', 'entity', 'ring_image',
                   'manual_snapshot', 'image', 'switch', 'services', 'camera'):
        load(component / f'{module}.py', scope)

    fixture = BytesIO()
    Image.new('RGB', (16, 16), 'blue').save(fixture, format='JPEG')
    jpeg = fixture.getvalue()
    fresh_frame = AsyncMock(return_value=jpeg)
    scope['capture_fresh_image'] = fresh_frame

    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        hass.config.media_dirs = {'local': str(Path(temporary) / 'private_media')}
        await hass.config.async_set_time_zone('Europe/Paris')
        hass.config_entries = ConfigEntries(hass, {})
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                            title='WelcomeEye fixture', unique_id='fixture-uid',
                            data={}, options={}, source='user', subentries_data=None,
                            discovery_keys=MappingProxyType({}))
        # Seed an entry without invoking its network-dependent setup.
        hass.config_entries._entries[entry.entry_id] = entry
        hub = SimpleNamespace(hass=hass, loop=asyncio.get_running_loop(), entry=entry,
                              stopped=False, connected=False, local_ring_supported=True,
                              capabilities=scope['MATRIX'][scope['DeviceVariant'].R001],
                              device_model='WelcomeEye Connect 2', _notify=Mock(),
                              subscribe=lambda callback: lambda: None,
                              ring_image_capture_entity_id=None)
        hub.ring_image = scope['RingImageCapture'](hub)
        hub.manual_snapshot = scope['ManualSnapshotCapture'](hub)
        try:
            switch = scope['WelcomeEyeRingCaptureSwitch'](hub)
            switch.hass = hass
            switch.async_write_ha_state = Mock()  # No frontend platform in this smoke test.
            assert isinstance(switch, SwitchEntity)
            assert switch.is_on is False
            await switch.async_turn_on()
            assert entry.options['ring_image_capture'] is True
            assert switch.is_on is True
            # Flush the real ConfigEntry Store to disk, bypassing only its delay.
            await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
            stored = await hass.async_add_executor_job(
                (Path(temporary) / '.storage/core.config_entries').read_text,
            )
            assert json.loads(stored)['data']['entries'][0]['options']['ring_image_capture'] is True
            await switch.async_turn_off()
            assert entry.options['ring_image_capture'] is False
            assert switch.is_on is False

            storage = scope['CaptureMediaStorage'](hub)
            saved = await storage.save(jpeg, datetime(2026, 9, 24, 14, 42, 31, tzinfo=timezone.utc), 'manual')
            assert saved['filename'].endswith('2026-09-24_16-42-31_manual.jpg')
            item = MediaSourceItem.from_uri(hass, saved['media_content_id'], None)
            source = LocalSource(hass, 'media_source', 'My media', hass.config.media_dirs, '/media')
            resolved = await source.async_resolve_media(item)
            assert resolved.mime_type == 'image/jpeg'
            assert await hass.async_add_executor_job(resolved.path.read_bytes) == jpeg
            assert resolved.url.startswith('/media/local/WelcomeEye/')
            assert LocalMediaView(hass, source).requires_auth is True

            camera = scope['WelcomeEyeCamera'](hub)
            camera.hass = hass
            camera.entity_id = 'camera.welcomeeye_fixture'
            # Only the HA platform entity index is seeded; dispatch, validation,
            # response wrapping and permissions run through actual HA helpers.
            hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {
                ('camera', 'welcomeeye_local'): {camera.entity_id: camera},
            }
            scope['async_setup_services'](hass)
            assert hass.services.supports_response('welcomeeye_local', 'capture_snapshot') is SupportsResponse.OPTIONAL
            result = await hass.services.async_call('welcomeeye_local', 'capture_snapshot',
                {'entity_id': 'all', 'save_to_media': True}, blocking=True, return_response=True)
            assert result[camera.entity_id]['saved'] is True
            assert result[camera.entity_id]['media_content_id'].startswith('media-source://media_source/local/')
            assert hub.manual_snapshot.jpeg == jpeg
            assert hub.ring_image.jpeg is None

            calls_before = fresh_frame.await_count
            denied_user = SimpleNamespace(is_admin=False,
                permissions=SimpleNamespace(check_entity=Mock(return_value=False)))
            hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=denied_user))
            try:
                await hass.services.async_call('welcomeeye_local', 'capture_snapshot',
                    {'entity_id': 'all'}, blocking=True, return_response=True,
                    context=Context(user_id='fixture-user'))
            except HomeAssistantError:
                pass
            else:
                raise AssertionError('Snapshot service bypassed entity control permission')
            assert fresh_frame.await_count == calls_before
            denied_user.permissions.check_entity.assert_called_with(camera.entity_id, 'control')
        finally:
            await hub.manual_snapshot.close()
            await hub.ring_image.close()
            # This isolated Core instance never runs async_start(); HA otherwise
            # ignores async_stop and leaves delayed Store writes pending.
            await hass.async_stop(force=True)
    print('REAL_HA_CAPTURE_SWITCH_SERVICE_PERMISSION_AND_PRIVATE_MEDIA_OK')


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).resolve().parents[1]))
