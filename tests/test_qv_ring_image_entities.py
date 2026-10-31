"""Actual image/switch entity code with in-memory HA and capture boundaries."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_fresh_snapshot import capture_module


class HAEntity:
    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        for remove in getattr(self, '_removers', ()):
            remove()
        self._removers = []

    def async_on_remove(self, callback):
        self._removers = [*getattr(self, '_removers', ()), callback]

    def async_write_ha_state(self):
        pass


class HAImage(HAEntity):
    def __init__(self, hass):
        self.hass = hass


entity = capture_module('entity.py', {'Entity': HAEntity,
    'DeviceInfo': lambda **values: values, 'DOMAIN': 'welcomeeye_local'})
images = capture_module('image.py', {'WelcomeEyeEntity': entity['WelcomeEyeEntity'],
    'ImageEntity': HAImage})
switches = capture_module('switch.py', {'WelcomeEyeEntity': entity['WelcomeEyeEntity'],
    'SwitchEntity': type('HASwitch', (HAEntity,), {}),
    'OPTION_RING_IMAGE_CAPTURE': 'ring_image_capture'})


def capture(channel, *, legacy=False):
    result = SimpleNamespace(enabled=legacy, updated=None, jpeg=None, source=None,
        status='idle' if legacy else 'disabled', image_sequence=None,
        media_content_id=None, filename=None, entity_id=None,
        diagnostics={'last_save_error_type': None})
    if not legacy:
        result.channel, result.default_enabled = channel, False

    def set_enabled(enabled):
        result.enabled = enabled
        result.status = 'idle' if enabled else 'disabled'

    result.set_enabled = Mock(side_effect=set_enabled)
    return result


class QVRingImageEntityTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, variant='connect3', *, secondary=True, capable=True,
                secondary_capable=True, legacy=False, options=None):
        entry = SimpleNamespace(unique_id=f'fixture-{variant}', title='Fixture',
            data={'device_variant': variant}, options=dict(options or {}))

        def update(entry, *, options):
            entry.options = options

        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_update_entry=Mock(side_effect=update)),
            async_add_executor_job=Mock(side_effect=AssertionError('Image reads must not perform IO')))

        def hub_for(channel, ring_capable):
            listeners = set()

            def subscribe(callback):
                listeners.add(callback)
                return lambda: listeners.discard(callback)

            return SimpleNamespace(entry=entry, hass=hass, variant=variant,
                stopped=False, connected=False, device_model='Fixture',
                capabilities=SimpleNamespace(last_ring_image=ring_capable,
                    ring_image_capture=ring_capable, last_snapshot=True),
                ring_image=capture(channel, legacy=legacy), manual_snapshot=capture(channel),
                subscribe=subscribe, _notify=Mock(), ring_image_capture_entity_id=None)

        hub = hub_for(1, capable)
        hub.channel2 = hub_for(2, secondary_capable) if secondary else None
        entry.runtime_data = hub
        if not legacy:
            def set_all(enabled):
                hub.ring_image.set_enabled(enabled)
                if hub.channel2 is not None:
                    hub.channel2.ring_image.set_enabled(enabled)
            hub.set_ring_image_capture_enabled = Mock(side_effect=set_all)
        return hass, hub

    async def setup(self, hass, hub):
        created_images, created_switches = [], []
        await images['async_setup_entry'](hass, hub.entry, created_images.extend)
        await switches['async_setup_entry'](hass, hub.entry, created_switches.extend)
        for platform, entities in (('image', created_images), ('switch', created_switches)):
            for item in entities:
                item.hass = hass
                item.entity_id = f'{platform}.{item._attr_unique_id}'
        return created_images, created_switches

    async def test_both_qv_families_have_two_ring_images_and_one_shared_switch(self):
        for variant in ('connect3', 'connect2_r002'):
            with self.subTest(variant=variant):
                hass, hub = self.fixture(variant)
                created, controls = await self.setup(hass, hub)
                self.assertEqual([item._attr_translation_key for item in created],
                    ['last_ring', 'last_ring_channel_2', 'last_snapshot', 'last_snapshot_channel_2'])
                self.assertEqual(len(controls), 1)
                self.assertEqual(created[0]._attr_unique_id, f'fixture-{variant}_last_ring')
                self.assertEqual(created[1]._attr_unique_id, f'fixture-{variant}_last_ring_channel_2')
                self.assertEqual(created[0]._attr_device_info, created[1]._attr_device_info)
                self.assertIs(created[0].capture, hub.ring_image)
                self.assertIs(created[1].capture, hub.channel2.ring_image)

    async def test_single_channel_or_unsupported_secondary_adds_no_second_ring_image(self):
        for variant in ('connect3', 'connect2_r002'):
            for secondary, secondary_capable in ((False, False), (True, False)):
                with self.subTest(variant=variant, secondary=secondary):
                    hass, hub = self.fixture(variant, secondary=secondary,
                                             secondary_capable=secondary_capable)
                    created, controls = await self.setup(hass, hub)
                    self.assertNotIn('last_ring_channel_2',
                                     [item._attr_translation_key for item in created])
                    self.assertEqual(len(controls), 1)
                    self.assertEqual(len(created), 3 if secondary else 2)

    async def test_cloud_capture_capability_off_exposes_no_capture_switch_or_ring_image(self):
        for variant in ('connect3', 'connect2_r002'):
            hass, hub = self.fixture(variant, capable=False, secondary_capable=False)
            created, controls = await self.setup(hass, hub)
            self.assertEqual(controls, [])
            self.assertEqual([item._attr_translation_key for item in created],
                             ['last_snapshot', 'last_snapshot_channel_2'])

    async def test_qv_default_off_does_not_persist_an_implicit_preference(self):
        for variant in ('connect3', 'connect2_r002'):
            hass, hub = self.fixture(variant, options={'unrelated': 'keep'})
            _, controls = await self.setup(hass, hub)
            switch = controls[0]
            await switch.async_added_to_hass()
            self.assertFalse(switch.is_on)
            self.assertFalse(hub.channel2.ring_image.enabled)
            hub.set_ring_image_capture_enabled.assert_called_once_with(False)
            hass.config_entries.async_update_entry.assert_not_called()
            self.assertEqual(hub.entry.options, {'unrelated': 'keep'})
            self.assertEqual(hub.ring_image_capture_entity_id, switch.entity_id)
            await switch.async_will_remove_from_hass()

    async def test_shared_switch_persists_both_captures_and_restores_on_and_off(self):
        for variant in ('connect3', 'connect2_r002'):
            hass, hub = self.fixture(variant, options={'unrelated': 'keep'})
            _, controls = await self.setup(hass, hub)
            switch = controls[0]
            await switch.async_added_to_hass()
            await switch.async_turn_on()
            self.assertTrue(hub.ring_image.enabled)
            self.assertTrue(hub.channel2.ring_image.enabled)
            self.assertEqual(hub.entry.options, {'unrelated': 'keep', 'ring_image_capture': True})
            await switch.async_will_remove_from_hass()
            self.assertFalse(hub.ring_image.enabled)
            self.assertFalse(hub.channel2.ring_image.enabled)
            self.assertIsNone(hub.ring_image_capture_entity_id)
            self.assertTrue(hub.entry.options['ring_image_capture'])
            await switch.async_added_to_hass()
            self.assertTrue(hub.ring_image.enabled)
            self.assertTrue(hub.channel2.ring_image.enabled)
            await switch.async_turn_off()
            self.assertFalse(hub.ring_image.enabled)
            self.assertFalse(hub.channel2.ring_image.enabled)
            self.assertEqual(hub.entry.options, {'unrelated': 'keep', 'ring_image_capture': False})
            await switch.async_will_remove_from_hass()
            await switch.async_added_to_hass()
            self.assertFalse(switch.is_on)
            self.assertFalse(hub.channel2.ring_image.enabled)
            await switch.async_will_remove_from_hass()

    async def test_second_image_uses_own_bytes_timestamp_channel_and_entity_binding(self):
        for variant in ('connect3', 'connect2_r002'):
            hass, hub = self.fixture(variant)
            created, _ = await self.setup(hass, hub)
            primary, secondary = created[:2]
            for channel, image in enumerate((primary, secondary), 1):
                await image.async_added_to_hass()
                image.capture.jpeg = f'jpeg-channel-{channel}'.encode()
                image.capture.updated = datetime(2026, 10, 28, 12, channel, tzinfo=timezone.utc)
                image.capture.image_sequence = channel * 10
                image.capture.status = 'ready'
            self.assertEqual(await primary.async_image(), b'jpeg-channel-1')
            self.assertEqual(await secondary.async_image(), b'jpeg-channel-2')
            self.assertNotEqual(primary.image_last_updated, secondary.image_last_updated)
            self.assertEqual(primary.extra_state_attributes['channel'], 1)
            self.assertEqual(secondary.extra_state_attributes['channel'], 2)
            self.assertEqual(secondary.extra_state_attributes['ring_sequence'], 20)
            self.assertEqual(hub.ring_image.entity_id, primary.entity_id)
            self.assertEqual(hub.channel2.ring_image.entity_id, secondary.entity_id)
            await secondary.async_will_remove_from_hass()
            self.assertIsNone(hub.channel2.ring_image.entity_id)
            self.assertEqual(hub.ring_image.entity_id, primary.entity_id)
            self.assertEqual(await secondary.async_image(), b'jpeg-channel-2')
            await primary.async_will_remove_from_hass()
            hass.async_add_executor_job.assert_not_called()

    async def test_channel_attribute_is_optional_and_does_not_coerce_unknown_values(self):
        hass, hub = self.fixture(secondary=False)
        image = images['WelcomeEyeRingImage'](hass, hub)
        for value in (None, True, '2', 3):
            hub.ring_image.channel = value
            self.assertNotIn('channel', image.extra_state_attributes)
        del hub.ring_image.channel
        self.assertNotIn('channel', image.extra_state_attributes)

    async def test_legacy_default_on_and_direct_backend_setter_are_preserved(self):
        hass, hub = self.fixture('connect2_r001', secondary=False, legacy=True)
        created, controls = await self.setup(hass, hub)
        switch = controls[0]
        await switch.async_added_to_hass()
        self.assertTrue(switch.is_on)
        hub.ring_image.set_enabled.assert_called_once_with(True)
        self.assertEqual(hub.entry.options, {})
        self.assertEqual(created[0]._attr_unique_id, 'fixture-connect2_r001_last_ring')
        self.assertNotIn('channel', created[0].extra_state_attributes)
        await switch.async_turn_off()
        self.assertFalse(switch.is_on)
        self.assertEqual(hub.entry.options, {'ring_image_capture': False})
        await switch.async_will_remove_from_hass()


if __name__ == '__main__':
    unittest.main()
