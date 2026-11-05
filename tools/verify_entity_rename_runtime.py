"""Verify WelcomeEye rename hooks against actual HA entity classes.

Run in the existing isolated Core CI containers. Transport/capture boundaries
are in-memory fixtures; this does not install packages or connect to a device.
The actual HA 2026.11 inheritance checker decides whether renames occur in place.
"""
import asyncio
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity as entity_helper

from verify_image_runtime import load


class OfflineRTC:
    """Observe manager lifetime without starting an ICE session."""

    def __init__(self, hub):
        self.close_all = AsyncMock()
        self._frame = Mock()
        self.viewers = {'existing-viewer': object()}
        hub.frame_listeners.add(self._frame)
        hub.close_listeners.add(self.close_all)


def capture():
    value = SimpleNamespace(entity_id=None, enabled=True, default_enabled=True,
        jpeg=b'previous-capture', updated=datetime(2026, 11, 4, tzinfo=timezone.utc),
        source='fixture', status='ready', media_content_id=None, filename=None,
        image_sequence=1, diagnostics={'last_save_error_type': None})
    value.set_enabled = Mock(side_effect=lambda enabled: setattr(value, 'enabled', enabled))
    return value


async def main(root):
    component = root / 'custom_components/welcomeeye_local'
    scope = {'__name__': 'custom_components.welcomeeye_local.rename_fixture',
        'DOMAIN': 'welcomeeye_local', 'OPTION_RING_IMAGE_CAPTURE': 'ring_image_capture',
        'WebRTCManager': OfflineRTC}
    for module in ('entity', 'camera', 'image', 'switch'):
        load(component / f'{module}.py', scope)

    classes = [value for value in scope.values() if isinstance(value, type)
               and issubclass(value, scope['WelcomeEyeEntity'])]
    checker = getattr(entity_helper, '_entity_class_requires_readd', None)
    version = importlib.metadata.version('homeassistant')
    if tuple(map(int, version.split('.')[:2])) >= (2026, 11):
        assert checker is not None, 'HA 2026.11 rename compatibility checker missing'
    if checker is not None:
        for cls in classes:
            assert not checker(cls), f'{cls.__name__} would still be removed on rename'

    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        listeners = set()

        def subscribe(listener):
            listeners.add(listener)
            return lambda: listeners.discard(listener)

        hub = SimpleNamespace(hass=hass,
            entry=SimpleNamespace(unique_id='rename-fixture', title='Fixture', options={}),
            device_model='WelcomeEye fixture', stopped=False, connected=True,
            capabilities=SimpleNamespace(ring_image_capture=True),
            ring_image=capture(), manual_snapshot=capture(),
            frame_listeners=set(), close_listeners=set(),
            ring_image_capture_entity_id=None, subscribe=subscribe, _notify=Mock())
        try:
            for name in ('WelcomeEyeCamera', 'WelcomeEyeConnect3Camera',
                         'WelcomeEyeConnect3Channel2Camera'):
                camera = scope[name](hub)
                camera.hass = hass
                camera.entity_id = 'camera.renamed_fixture'
                manager, viewers = camera.rtc, dict(camera.rtc.viewers)
                stream = SimpleNamespace(source=AsyncMock(return_value='fixture://stream'),
                                         close=AsyncMock())
                hub._local_video_stream = stream
                unique_id = camera.unique_id
                camera.async_entity_id_changed('camera.previous_fixture')
                assert camera.unique_id == unique_id
                assert camera.rtc is manager and camera.rtc.viewers == viewers
                assert not camera._stream_removed
                assert await camera.stream_source() == 'fixture://stream'
                stream.close.assert_not_called()
                manager.close_all.assert_not_called()

            for name in ('WelcomeEyeRingImage', 'WelcomeEyeSecondaryRingImage',
                         'WelcomeEyeSnapshotImage', 'WelcomeEyeSecondarySnapshotImage'):
                image = scope[name](hass, hub)
                image.entity_id = 'image.before_rename'
                await image.async_added_to_hass()
                prior = (image.unique_id, image.capture, image.capture.jpeg,
                         image.image_last_updated, set(listeners))
                image.entity_id = 'image.after_rename'
                image.async_entity_id_changed('image.before_rename')
                assert image.capture.entity_id == image.entity_id
                assert (image.unique_id, image.capture, image.capture.jpeg,
                        image.image_last_updated, listeners) == prior
                await image.async_will_remove_from_hass()
                assert image.capture.entity_id is None

            switch = scope['WelcomeEyeRingCaptureSwitch'](hub)
            switch.hass = hass
            switch.entity_id = 'switch.before_rename'
            await switch.async_added_to_hass()
            unique_id, prior_listeners = switch.unique_id, set(listeners)
            hub.ring_image.set_enabled.reset_mock()
            switch.entity_id = 'switch.after_rename'
            switch.async_entity_id_changed('switch.before_rename')
            assert hub.ring_image_capture_entity_id == switch.entity_id
            assert switch.unique_id == unique_id and switch.is_on
            assert listeners == prior_listeners
            hub.ring_image.set_enabled.assert_not_called()
            await switch.async_will_remove_from_hass()
            hub.ring_image.set_enabled.assert_called_once_with(False)
            assert hub.ring_image_capture_entity_id is None
        finally:
            await hass.async_stop(force=True)

    print(f'REAL_HA_ENTITY_RENAME_HOOKS_OK {version} classes={len(classes)} '
          f'in_place_checker={checker is not None}')


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).resolve().parents[1]))
