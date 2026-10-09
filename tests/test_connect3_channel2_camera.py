"""Channel-2 HA camera contract; all peers and WebRTC signaling are synthetic."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from load_integration import ROOT, cap, load
from test_transport_lifecycle import load_source


class Entity:
    pass


class Camera:
    pass


class RTC:
    """No network; exercise camera manager lifetime and dispatch contracts."""

    initializations = 0

    def __init__(self, hub):
        RTC.initializations += 1
        self.hub = hub
        self.closed = False
        self.offers_seen = []
        self.cleanup_gate = None
        self.cleanup_entered = asyncio.Event()
        hub.frame_listeners.add(self._frame)
        hub.close_listeners.add(self.close_all)

    def _frame(self, *args):
        pass

    async def close_all(self):
        self.cleanup_entered.set()
        if self.cleanup_gate is not None:
            await self.cleanup_gate.wait()
        self.closed = True
        self.hub.frame_listeners.discard(self._frame)
        self.hub.close_listeners.discard(self.close_all)

    async def offer(self, offer_sdp, session_id, send_message, **kwargs):
        if not self.closed and not self.hub.stopped:
            self.offers_seen.append(session_id)


entity_module = load_source('entity', dict(Entity=Entity, DeviceInfo=lambda **kw: kw,
                                         DOMAIN='welcomeeye_local'))
camera_module = load_source('camera', dict(asyncio=asyncio, Camera=Camera,
    CameraEntityFeature=SimpleNamespace(STREAM=1), UNDEFINED=object(),
    WelcomeEyeEntity=entity_module.WelcomeEyeEntity, WebRTCManager=RTC,
    capture_fresh_image=AsyncMock(side_effect=AssertionError('Unexpected snapshot I/O'))))
hub_module = load('connect3.hub')


def hub(*, video=True, secondary=True, variant=cap.DeviceVariant.CONNECT3):
    cls = hub_module.Connect3Hub if variant == cap.DeviceVariant.CONNECT3 else load('r002.hub').R002InvestigationHub
    data = {'host': '192.0.2.1', 'experimental_video': video, 'second_channel_enabled': secondary,
            'experimental_outputs': True, 'experimental_tcp_controls': True,
            'opening_code': 'SYNTHETIC_OPENING_CODE'}
    entry = SimpleNamespace(unique_id='existing-device', entry_id='synthetic',
                            title='Existing primary', data=data)
    result = cls(SimpleNamespace(), entry)
    entry.runtime_data = result
    result.stopped = False
    return result


class Channel2CameraTests(unittest.IsolatedAsyncioTestCase):
    async def cameras(self, current):
        entities = []
        await camera_module.async_setup_entry(None, current.entry, entities.extend)
        return entities

    async def test_camera_link_same_device_main_identity_preserved_and_separate_audio(self):
        current = hub()
        entities = await self.cameras(current)
        self.assertEqual(len(entities), 2)
        primary, trial = entities
        self.assertIs(primary.hub, current)
        self.assertIs(trial.hub, current.channel2)
        self.assertEqual(primary._attr_unique_id, 'existing-device_camera')
        self.assertEqual(trial._attr_unique_id, 'existing-device_camera_channel_2')
        self.assertEqual(primary._attr_device_info, trial._attr_device_info)
        self.assertEqual(trial._attr_translation_key, 'channel_2_trial')
        self.assertTrue(trial._supports_native_async_webrtc)
        self.assertIsNone(primary._attr_name)
        self.assertIs(trial._attr_name, camera_module.UNDEFINED)
        self.assertEqual({key for key, value in trial.extra_state_attributes[
            'welcomeeye_capabilities'].items() if value}, {'camera', 'live_media', 'downstream_audio'})
        self.assertIsNone(trial.extra_state_attributes['ring_image_capture_entity_id'])
        self.assertFalse(current.live.consumers)
        self.assertFalse(current.channel2.live.consumers)
        self.assertIsNone(current.channel2.live.task)
        self.assertEqual(primary.extra_state_attributes['welcomeeye_channel'], 1)
        self.assertEqual(trial.extra_state_attributes['welcomeeye_channel'], 2)
        self.assertTrue(primary.extra_state_attributes['welcomeeye_multichannel_available'])

    async def test_no_preview_session_from_still_image_poll_or_stream_source(self):
        current = hub()
        trial = (await self.cameras(current))[1]
        current.channel2.acquire = AsyncMock(side_effect=AssertionError('Unexpected video acquisition'))
        for _ in range(3):
            self.assertIsNone(await trial.async_camera_image())
            self.assertIsNone(await trial.stream_source())
        current.channel2.live.image = b'SYNTHETIC_JPEG'
        self.assertEqual(await trial.async_camera_image(), b'SYNTHETIC_JPEG')
        current.channel2.acquire.assert_not_called()
        camera_module.capture_fresh_image.assert_not_called()

    async def test_no_trial_for_disabled_video_or_r002(self):
        current = hub(video=False)
        self.assertEqual(await self.cameras(current), [])
        self.assertIsNone(current.channel2)
        r002 = hub(variant=cap.DeviceVariant.R002)
        entities = await self.cameras(r002)
        self.assertEqual(len(entities), 1)
        self.assertIsNone(r002.channel2)
        self.assertEqual(entities[0]._attr_unique_id, 'existing-device_camera')
        self.assertNotIn('welcomeeye_channel', entities[0].extra_state_attributes)

    async def test_single_channel_has_no_secondary_camera_or_selector(self):
        current = hub(secondary=False)
        entities = await self.cameras(current)
        self.assertEqual(len(entities), 1)
        self.assertIsNone(current.channel2)
        self.assertFalse(entities[0].extra_state_attributes['welcomeeye_multichannel_available'])

    async def test_legacy_second_camera_preserves_primary_hls_and_separate_metadata(self):
        from test_legacy_multichannel import Hub
        for variant in ('connect_v1', 'connect2_r001', 'legacy_unknown'):
            entry = SimpleNamespace(unique_id='existing-legacy', entry_id='fixture',
                title='Renamed intercom', data={'host': '192.0.2.1',
                    'device_variant': variant, 'second_channel_enabled': True})
            current = Hub(None, entry)
            entry.runtime_data = current
            current.stopped = False
            primary, secondary = await self.cameras(current)
            try:
                self.assertFalse(primary._supports_native_async_webrtc)
                self.assertTrue(secondary._supports_native_async_webrtc)
                self.assertEqual(primary._attr_unique_id, 'existing-legacy_camera')
                self.assertEqual(secondary._attr_unique_id, 'existing-legacy_camera_channel_2')
                self.assertEqual(primary._attr_device_info, secondary._attr_device_info)
                self.assertEqual(primary.extra_state_attributes['welcomeeye_channel'], 1)
                self.assertEqual(secondary.extra_state_attributes['welcomeeye_channel'], 2)
                self.assertFalse(secondary.hub.capabilities.talkback)
                self.assertFalse(secondary.hub.capabilities.strike)
                self.assertIsNone(await secondary.async_camera_image())
                self.assertIsNone(await secondary.stream_source())
                self.assertIsNone(current.thread)
            finally:
                await current.stop()

    async def test_disabling_video_preserves_registry_identity_for_reenable(self):
        current = hub()
        trial = (await self.cameras(current))[1]
        registry = SimpleNamespace(config_entry_id=current.entry.entry_id,
            platform='welcomeeye_local', domain='camera', unique_id=trial._attr_unique_id,
            entity_id='camera.renamed_trial')
        self.assertEqual(list(cap.unsupported_entity_ids([registry], current.entry.entry_id,
            current.entry.unique_id, cap.MATRIX[cap.DeviceVariant.CONNECT3])), [])
        self.assertEqual(await self.cameras(hub(video=False)), [])
        recreated = (await self.cameras(hub()))[1]
        self.assertEqual(recreated._attr_unique_id, trial._attr_unique_id)

    async def test_unload_never_recreates_rtc_listeners(self):
        current = hub()
        trial = (await self.cameras(current))[1]
        manager = trial.rtc
        before = RTC.initializations
        current.stopped = True
        await manager.close_all()
        await manager.offer('synthetic', 'stopped', lambda _: None)
        self.assertEqual(RTC.initializations, before)
        self.assertFalse(trial.hub.frame_listeners)
        self.assertFalse(trial.hub.close_listeners)
        self.assertEqual(manager.offers_seen, [])

    def test_camera_names_translated_without_trial_label(self):
        for relative, name in (('strings.json', 'Secondary camera'),
                               ('translations/en.json', 'Secondary camera'),
                               ('translations/fr.json', 'Caméra secondaire')):
            document = json.loads((ROOT / relative).read_text(encoding='utf-8'))
            self.assertEqual(document['entity']['camera']['channel_2_trial']['name'], name)
            self.assertNotIn('experimental_channel2', document['config']['step']['connect3']['data'])


if __name__ == '__main__':
    unittest.main()
