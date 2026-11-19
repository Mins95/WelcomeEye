"""Exercise HA 2026.11's camera capability, websocket offer and entity permissions.

The signaling boundary records the resolved permission. No media or device
connections are made. Media start, mute and resume have separate RTC tests.
"""
import asyncio
from importlib.metadata import version
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.auth.models import Group, User
from homeassistant.auth.permissions import PermissionLookup
from homeassistant.auth.permissions.system_policies import READ_ONLY_POLICY, USER_POLICY
from homeassistant.components import websocket_api
from homeassistant.components.camera.const import DATA_COMPONENT, PREVIEW_FEATURE_TWO_WAY_AUDIO
from homeassistant.components.camera.webrtc import async_register_ws
from homeassistant.components.labs.const import LABS_DATA
from homeassistant.components.labs.models import LabsData, LabsStoreData
from homeassistant.core import HomeAssistant


async def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from custom_components.welcomeeye_local.camera import (
        WelcomeEyeCamera, WelcomeEyeConnect3Camera, WelcomeEyeConnect3Channel2Camera,
    )

    with tempfile.TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        hub = SimpleNamespace(entry=SimpleNamespace(unique_id='fixture', title='Fixture'),
            frame_listeners=set(), close_listeners=set(), device_model='Fixture', stopped=False,
            capabilities=SimpleNamespace(talkback=True))
        primary, secondary, legacy = (
            entity_class(hub) for entity_class in (
                WelcomeEyeConnect3Camera, WelcomeEyeConnect3Channel2Camera, WelcomeEyeCamera)
        )
        for entity in (primary, secondary, legacy):
            entity.hass = hass
        primary.entity_id = 'camera.fixture'
        primary.rtc = SimpleNamespace(offer=AsyncMock(), schedule_close=Mock())
        hass.data[DATA_COMPONENT] = SimpleNamespace(get_entity=lambda key: (
            primary if key == primary.entity_id else None))
        features = set()
        hass.data[LABS_DATA] = LabsData(store=None, data=LabsStoreData(preview_feature_status=features))
        async_register_ws(hass)
        viewer_group = Group('Viewer', READ_ONLY_POLICY)
        control_group = Group('Control', USER_POLICY)
        user = User('Fixture', PermissionLookup(None, None), is_active=True, groups=[control_group])
        connection = websocket_api.ActiveConnection(logging.getLogger(__name__), hass,
            Mock(), user, None, None)

        async def offer(seq, allowed):
            primary.rtc.offer.reset_mock()
            connection.async_handle({'type': 'camera/webrtc/offer', 'id': seq,
                                     'entity_id': primary.entity_id, 'offer': 'synthetic SDP'})
            await hass.async_block_till_done()
            primary.rtc.offer.assert_awaited_once()
            args, opts = primary.rtc.offer.await_args
            assert args[0] == 'synthetic SDP' and args[1] and callable(args[2])
            assert opts['allow_talk'] is allowed
            assert opts['native_talk_allowed']() is allowed
            return opts['native_talk_allowed']

        try:
            assert not primary.camera_capabilities.two_way_audio
            await offer(1, False)
            features.add(('camera', PREVIEW_FEATURE_TWO_WAY_AUDIO))
            assert primary.camera_capabilities.two_way_audio
            assert not secondary.camera_capabilities.two_way_audio
            assert not legacy.camera_capabilities.two_way_audio
            authorized = await offer(2, True)
            user.groups = [viewer_group]
            assert not authorized()
            await offer(3, False)
            user.groups = [control_group]
            user.is_active = False
            assert not authorized()
            user.is_active = True
            assert authorized()
            features.clear()
            assert not authorized()
        finally:
            connection.async_handle_close()
            await hass.async_stop(force=True)
    print(f"Home Assistant {version('homeassistant')} native camera: Labs, primary/secondary, "
          "websocket offer, read/control and revoked authorization passed")


if __name__ == '__main__':
    asyncio.run(main())
