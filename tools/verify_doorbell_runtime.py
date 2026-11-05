"""Actual HA EventEntity + notification blueprint APIs with offline fixtures.

Run only in the existing Core CI containers. No WelcomeEye receiver, hardware,
network notification provider or user Home Assistant is started.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA, PLATFORM_SCHEMA
from homeassistant.components.event import DoorbellEventType, EventDeviceClass, EventExtraStoredData
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr, entity as entity_helper, entity_registry as er
from homeassistant.helpers.script import Script
from homeassistant.helpers.template import Template
from homeassistant.util import yaml as yaml_util

from verify_image_runtime import load


async def verify_events(hass, entry, root):
    scope = {'__name__': 'welcomeeye_event_fixture', 'DOMAIN': 'welcomeeye_local'}
    for name in ('entity', 'event'):
        load(root / f'custom_components/welcomeeye_local/{name}.py', scope)
    cls = scope['WelcomeEyeDoorbellEvent']
    checker = getattr(entity_helper, '_entity_class_requires_readd', None)
    if checker:
        assert not checker(cls), 'Event entity would be removed on rename'
    listeners = set()

    def subscribe(listener):
        listeners.add(listener)
        return lambda: listeners.discard(listener)

    hub = SimpleNamespace(hass=hass, entry=entry, device_model='WelcomeEye fixture',
        capabilities=SimpleNamespace(doorbell=True, cloud_ring=True, local_ring=False),
        qv_cloud=SimpleNamespace(connected=True), secondary_channel_enabled=True,
        _cloud_ring_sequence=4, stopped=False, subscribe=subscribe)
    entry.runtime_data = hub
    created = []
    await scope['async_setup_entry'](hass, entry, created.extend)
    assert len(created) == 2
    written = []
    restored = '2026-10-01T10:00:00.000+00:00'
    for index, entity in enumerate(created):
        entity.hass = hass
        entity.entity_id = f'event.fixture_doorbell_{index + 1}'
        entity.async_write_ha_state = Mock(side_effect=lambda entity=entity: written.append(
            (entity.entity_id, entity.state, dict(entity.state_attributes))))
        entity.async_get_last_state = AsyncMock(return_value=State(entity.entity_id, restored))
        entity.async_get_last_event_data = AsyncMock(return_value=EventExtraStoredData('ring', {'ring_sequence': 99}))
        await entity.async_internal_added_to_hass()
        await entity.async_added_to_hass()
        assert entity.device_class == EventDeviceClass.DOORBELL
        assert entity.event_types == [DoorbellEventType.RING]
        assert entity.state == restored and not written

    def ring(sequence, channel=1):
        hass.bus.async_fire('welcomeeye_local.ring', {'entry_id': entry.entry_id,
            'channel': channel, 'source': 'cloud_qv', 'ring_sequence': sequence})

    hub._cloud_ring_sequence = 6
    ring(4)  # Baseline restored/reloaded state is never a fresh ring.
    ring(5)
    ring(6)
    ring(6)
    ring(999999)
    await hass.async_block_till_done()
    assert [item[2]['ring_sequence'] for item in written] == [5, 6]
    if tuple(map(int, importlib.metadata.version('homeassistant').split('.')[:2])) >= (2026, 11):
        assert written[0][1] < written[1][1]
    hub._cloud_ring_sequence = 7
    ring(7, 2)
    await hass.async_block_till_done()
    assert written[-1][0] == created[1].entity_id
    assert written[-1][2]['channel'] == 2
    for entity in created:
        previous = (entity.unique_id, entity.state, dict(entity.state_attributes), set(listeners))
        old_id = entity.entity_id
        if checker:
            entity.entity_id += '_renamed'
        entity.async_entity_id_changed(old_id)
        assert (entity.unique_id, entity.state, entity.state_attributes, listeners) == previous
        await entity.async_remove(force_remove=True)
    assert not listeners
    count = len(written)
    hub._cloud_ring_sequence = 8
    ring(8)
    await hass.async_block_till_done()
    assert len(written) == count


async def verify_blueprint(hass, entry, root):
    registry = er.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id,
        identifiers={('welcomeeye_local', entry.unique_id)}, name='Offline fixture')
    image = registry.async_get_or_create('image', 'welcomeeye_local', 'fixture_last_ring',
        config_entry=entry, device_id=device.id)
    path = root / 'blueprints/automation/welcomeeye_local/ring_photo_notification.yaml'
    raw = await hass.async_add_executor_job(yaml_util.load_yaml_dict, path)
    AUTOMATION_BLUEPRINT_SCHEMA(deepcopy(raw))
    inputs = {key: value['default'] for key, value in raw['blueprint']['input'].items() if 'default' in value}
    inputs.update(doorbell_device=device.id, ring_image=image.entity_id,
                  notification_action='notify.mobile_app_offline_fixture')
    assert yaml_util.extract_inputs(raw) <= inputs.keys()
    config = yaml_util.substitute(raw, inputs)
    del config['blueprint']
    validated = PLATFORM_SCHEMA(deepcopy(config))
    received = []

    @callback
    def receive(call):
        received.append(dict(call.data))

    hass.services.async_register('notify', 'mobile_app_offline_fixture', receive)
    filename = 'WelcomeEye/welcomeeye_abcdef012345/2026-11-05/2026-11-05_11-00-00_ring_1.jpg'
    media = 'media-source://welcomeeye_local/My%20photos/' + filename
    attributes = {'channel': 2, 'ring_sequence': 1, 'capture_status': 'ready',
        'captured_at': datetime.now(timezone.utc).isoformat(), 'filename': filename, 'media_content_id': media}
    hass.states.async_set(image.entity_id, attributes['captured_at'], attributes)
    data = {'entry_id': entry.entry_id, 'image_entity_id': image.entity_id,
            'channel': 2, 'ring_sequence': 1, 'filename': filename, 'media_content_id': media}
    variables = {'trigger': {'event': {'data': data}}}
    for key, value in config['variables'].items():
        variables[key] = Template(value, hass).async_render(variables) if isinstance(value, str) else value
    condition = Template(config['conditions'][0]['value_template'], hass)
    assert condition.async_render(variables) is True
    # A newer capture appearing after conditions cannot change this file URL.
    hass.states.async_set(image.entity_id, attributes['captured_at'], {**attributes, 'ring_sequence': 2})
    assert condition.async_render(variables) is False
    script = Script(hass, validated['actions'], 'Offline photo notification', 'automation')
    await script.async_run(variables, Context())
    assert len(received) == 1
    notification = received[0]['data']
    assert notification['image'] == '/media/My%20photos/' + filename
    assert notification['actions'] == [{'action': 'URI', 'title': 'View doorbell', 'uri': '/lovelace/welcomeeye'}]
    assert notification['url'] == notification['clickAction'] == '/lovelace/welcomeeye'
    hass.services.async_remove('notify', 'mobile_app_offline_fixture')


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Offline fixture', data={}, options={}, source='user', unique_id='doorbell-fixture',
            discovery_keys={}, subentries_data=None)
        hass.config_entries._entries[entry.entry_id] = entry
        try:
            await verify_events(hass, entry, root)
            await verify_blueprint(hass, entry, root)
        finally:
            await hass.async_stop(force=True)
    print(f'REAL_HA_DOORBELL_BLUEPRINT_OK {importlib.metadata.version("homeassistant")}')


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).resolve().parents[1]))
