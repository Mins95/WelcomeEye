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


def hub(*, video=True, variant=cap.DeviceVariant.CONNECT3):
    cls = hub_module.Connect3Hub if variant == cap.DeviceVariant.CONNECT3 else load('r002.hub').R002InvestigationHub
    data = {'host': '192.0.2.1', 'experimental_video': video,
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

    async def test_camera_link_same_device_main_identity_preserved_and_video_only(self):
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
            'welcomeeye_capabilities'].items() if value}, {'camera', 'live_media'})
        self.assertIsNone(trial.extra_state_attributes['ring_image_capture_entity_id'])
        self.assertFalse(current.live.consumers)
        self.assertFalse(current.channel2.live.consumers)
        self.assertIsNone(current.channel2.live.task)
        self.assertIsNone(current.channel2._deadline)

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

    async def test_expired_rtc_can_reopen_from_native_or_card_offer(self):
        trial = (await self.cameras(hub()))[1]
        manager = trial.rtc
        await manager.close_all()
        before = RTC.initializations
        # Both entry points use the same restart-aware manager.
        await trial.async_handle_async_webrtc_offer('synthetic', 'native', lambda _: None)
        self.assertEqual(RTC.initializations, before + 1)
        await manager.close_all()
        await manager.offer('synthetic', 'card', lambda _: None, allow_talk=False)
        self.assertIs(trial.rtc, manager)
        self.assertEqual(manager.offers_seen, ['card'])
        self.assertEqual(len(trial.hub.frame_listeners), 1)
        self.assertEqual(len(trial.hub.close_listeners), 1)

    async def test_concurrent_reopen_waits_for_cleanup_and_restarts_manager_once(self):
        trial = (await self.cameras(hub()))[1]
        manager = trial.rtc
        manager.closed = True
        manager.cleanup_gate = asyncio.Event()
        before = RTC.initializations
        first = asyncio.create_task(manager.offer('synthetic', 'first', lambda _: None))
        await manager.cleanup_entered.wait()
        second = asyncio.create_task(manager.offer('synthetic', 'second', lambda _: None))
        await asyncio.sleep(0)
        self.assertEqual(RTC.initializations, before)
        manager.cleanup_gate.set()
        await asyncio.gather(first, second)
        self.assertEqual(RTC.initializations, before + 1)
        self.assertEqual(manager.offers_seen, ['first', 'second'])

    async def test_unload_during_cleanup_never_recreates_rtc_listeners(self):
        current = hub()
        trial = (await self.cameras(current))[1]
        manager = trial.rtc
        manager.closed = True
        manager.cleanup_gate = asyncio.Event()
        before = RTC.initializations
        pending = asyncio.create_task(manager.offer('synthetic', 'stopped', lambda _: None))
        await manager.cleanup_entered.wait()
        current.stopped = True
        manager.cleanup_gate.set()
        await pending
        self.assertEqual(RTC.initializations, before)
        self.assertFalse(trial.hub.frame_listeners)
        self.assertFalse(trial.hub.close_listeners)
        self.assertEqual(manager.offers_seen, [])

    def test_camera_names_translated_without_new_config_fields(self):
        for relative, name in (('strings.json', 'Channel 2 camera test'),
                               ('translations/en.json', 'Channel 2 camera test'),
                               ('translations/fr.json', 'Test caméra canal 2')):
            document = json.loads((ROOT / relative).read_text(encoding='utf-8'))
            self.assertEqual(document['entity']['camera']['channel_2_trial']['name'], name)
            self.assertNotIn('experimental_channel2', document['config']['step']['connect3']['data'])


if __name__ == '__main__':
    unittest.main()
