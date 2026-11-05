"""Actual HA EventEntity + notification blueprint APIs with offline fixtures.

Run only in the existing Core CI containers. No WelcomeEye receiver, hardware,
network notification provider or user Home Assistant is started.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import faulthandler
import importlib.metadata
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

print('DOORBELL_RUNTIME imports BEGIN', flush=True)
faulthandler.enable()
# Imports happen before asyncio can enforce a deadline. Also reveal synchronous
# import deadlocks, rather than leaving a Core matrix job silent for minutes.
faulthandler.dump_traceback_later(45, exit=True)
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA, PLATFORM_SCHEMA
from homeassistant.components.event import DoorbellEventType, EventDeviceClass, EventExtraStoredData
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers import device_registry as dr, entity as entity_helper, entity_registry as er, restore_state
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.template import Template
from homeassistant.util import yaml as yaml_util

from verify_image_runtime import load

faulthandler.cancel_dump_traceback_later()
print('DOORBELL_RUNTIME imports OK', flush=True)


async def phase(name, operation, *, timeout=20):
    """Bound each async phase and expose pending stacks on timeout.

    The thread watchdog also terminates a blocked event loop or a coroutine
    which swallows cancellation; no timeout is converted into a passing check.
    """
    print(f'DOORBELL_RUNTIME {name} BEGIN', flush=True)
    faulthandler.dump_traceback_later(timeout + 5, exit=True)
    try:
        async with asyncio.timeout(timeout):
            result = await operation
    except BaseException:
        print(f'DOORBELL_RUNTIME {name} FAILED', file=sys.stderr, flush=True)
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                print(f'Pending task: {task.get_name()}', file=sys.stderr, flush=True)
                task.print_stack(limit=8, file=sys.stderr)
        raise
    else:
        print(f'DOORBELL_RUNTIME {name} OK', flush=True)
        return result
    finally:
        faulthandler.cancel_dump_traceback_later()


async def verify_events(hass, entry, root):
    print('DOORBELL_RUNTIME events BEGIN', flush=True)
    scope = {'__name__': 'welcomeeye_event_fixture', 'DOMAIN': 'welcomeeye_local'}
    for name in ('entity', 'event'):
        load(root / f'custom_components/welcomeeye_local/{name}.py', scope)
    cls = scope['WelcomeEyeDoorbellEvent']
    checker = getattr(entity_helper, '_entity_class_requires_readd', None)
    if checker:
        assert not checker(cls), 'Event entity would be removed on rename'
    listeners = set()
    subscription_changes = []

    def subscribe(listener):
        listeners.add(listener)
        subscription_changes.append('add')

        def unsubscribe():
            listeners.discard(listener)
            subscription_changes.append('remove')

        return unsubscribe

    hub = SimpleNamespace(hass=hass, entry=entry, device_model='WelcomeEye fixture',
        capabilities=SimpleNamespace(doorbell=True, cloud_ring=True, local_ring=False),
        qv_cloud=SimpleNamespace(connected=True), secondary_channel_enabled=True,
        _cloud_ring_sequence=4, stopped=False, subscribe=subscribe)
    entry.runtime_data = hub
    created = []
    await phase('event_factory', scope['async_setup_entry'](hass, entry, created.extend))
    assert len(created) == 2
    # Use the real platform add path: it binds platform/platform_data, registers
    # entity/device/source indexes, initializes state metadata and owns removal.
    # Calling async_internal_added_to_hass directly omits those prerequisites.
    platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
        domain='event', platform_name='welcomeeye_local', platform=None,
        scan_interval=timedelta(seconds=30), entity_namespace=None)
    platform.config_entry = entry
    platform.async_prepare()
    written = []
    restored = '2026-10-01T10:00:00.000+00:00'
    for index, entity in enumerate(created):
        entity.entity_id = f'event.fixture_doorbell_{index + 1}'
        write_state = entity.async_write_ha_state

        @callback
        def write_and_observe(entity=entity, write_state=write_state):
            write_state()
            written.append((entity.entity_id, entity.state, dict(entity.state_attributes)))

        entity.async_write_ha_state = write_and_observe
        entity.async_get_last_state = AsyncMock(return_value=State(entity.entity_id, restored))
        entity.async_get_last_event_data = AsyncMock(return_value=EventExtraStoredData('ring', {'ring_sequence': 99}))
    await phase('platform_add_restore', platform.async_add_entities(created))
    await phase('drain_after_restore', hass.async_block_till_done())
    assert len(platform.entities) == 2
    for entity in created:
        assert entity.platform is platform
        assert entity.platform_data is platform.platform_data
        assert platform.entities[entity.entity_id] is entity
        assert entity.registry_entry.config_entry_id == entry.entry_id
        assert entity.device_class == EventDeviceClass.DOORBELL
        assert entity.event_types == [DoorbellEventType.RING]
        assert entity.state == restored
        assert hass.states.get(entity.entity_id).state == restored
        # Only the pre-existing storage result is mocked. Subsequent removal
        # and rename exercise actual RestoreEntity storage and lookup.
        del entity.async_get_last_state
        del entity.async_get_last_event_data
    assert written and all(item[1] == restored and item[2]['ring_sequence'] == 99
                           for item in written)
    written.clear()  # Initial restored-state publication is not a new ring.

    def ring(sequence, channel=1):
        hass.bus.async_fire('welcomeeye_local.ring', {'entry_id': entry.entry_id,
            'channel': channel, 'source': 'cloud_qv', 'ring_sequence': sequence})

    hub._cloud_ring_sequence = 6
    ring(4)  # Baseline restored/reloaded state is never a fresh ring.
    ring(5)
    ring(6)
    ring(6)
    ring(999999)
    await phase('dispatch_queued_rings', hass.async_block_till_done())
    assert [item[2]['ring_sequence'] for item in written] == [5, 6]
    if tuple(map(int, importlib.metadata.version('homeassistant').split('.')[:2])) >= (2026, 11):
        assert written[0][1] < written[1][1]
    hub._cloud_ring_sequence = 7
    ring(7, 2)
    await phase('dispatch_channel_2', hass.async_block_till_done())
    assert written[-1][0] == created[1].entity_id
    assert written[-1][2]['channel'] == 2
    registry = er.async_get(hass)
    for entity in created:
        previous = (entity.unique_id, entity.state, dict(entity.state_attributes), set(listeners))
        old_id = entity.entity_id
        previous_subscription_changes = len(subscription_changes)
        registry.async_update_entity(old_id, new_entity_id=old_id + '_renamed')
        await phase(f'rename_{old_id}', hass.async_block_till_done())
        assert entity.entity_id == old_id + '_renamed'
        assert old_id not in platform.entities and hass.states.get(old_id) is None
        assert platform.entities[entity.entity_id] is entity
        assert (entity.unique_id, entity.state, entity.state_attributes, listeners) == previous
        if checker:
            assert len(subscription_changes) == previous_subscription_changes
        await phase(f'remove_{entity.entity_id}', entity.async_remove(force_remove=True))
        assert hass.states.get(entity.entity_id) is None
    assert not listeners
    assert not platform.entities
    assert not platform.domain_entities and not platform.domain_platform_entities
    await phase('platform_destroy', platform.async_destroy())
    count = len(written)
    hub._cloud_ring_sequence = 8
    ring(8)
    await phase('dispatch_after_unload', hass.async_block_till_done())
    assert len(written) == count
    print('DOORBELL_RUNTIME events OK', flush=True)


async def verify_blueprint(hass, entry, root):
    print('DOORBELL_RUNTIME blueprint BEGIN', flush=True)
    registry = er.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id,
        identifiers={('welcomeeye_local', entry.unique_id)}, name='Offline fixture')
    image = registry.async_get_or_create('image', 'welcomeeye_local', 'fixture_last_ring',
        config_entry=entry, device_id=device.id)
    path = root / 'blueprints/automation/welcomeeye_local/ring_photo_notification.yaml'
    raw = await phase('blueprint_load', hass.async_add_executor_job(yaml_util.load_yaml_dict, path))
    AUTOMATION_BLUEPRINT_SCHEMA(deepcopy(raw))
    inputs = {key: value['default'] for key, value in raw['blueprint']['input'].items() if 'default' in value}
    inputs.update(doorbell_device=device.id, ring_image=image.entity_id,
                  notification_action='notify.mobile_app_offline_fixture')
    assert yaml_util.extract_inputs(raw) <= inputs.keys()
    config = yaml_util.substitute(raw, inputs)
    del config['blueprint']
    validated = PLATFORM_SCHEMA(deepcopy(config))
    validated['actions'] = await phase('blueprint_validate_actions',
        async_validate_actions_config(hass, validated['actions']))
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
    print('DOORBELL_RUNTIME blueprint_render BEGIN', flush=True)
    assert condition.async_render(variables) is True
    # A newer capture appearing after conditions cannot change this file URL.
    hass.states.async_set(image.entity_id, attributes['captured_at'], {**attributes, 'ring_sequence': 2})
    assert condition.async_render(variables) is False
    print('DOORBELL_RUNTIME blueprint_render OK', flush=True)
    script = Script(hass, validated['actions'], 'Offline photo notification', 'automation')
    await phase('blueprint_notify_fixture', script.async_run(variables, Context()))
    assert len(received) == 1
    notification = received[0]['data']
    assert notification['image'] == '/media/My%20photos/' + filename
    assert notification['actions'] == [{'action': 'URI', 'title': 'View doorbell', 'uri': '/lovelace/welcomeeye'}]
    assert notification['url'] == notification['clickAction'] == '/lovelace/welcomeeye'
    hass.services.async_remove('notify', 'mobile_app_offline_fixture')

    async def verify_missing_service():
        try:
            await script.async_run(variables, Context())
        except ServiceNotFound:
            pass
        else:
            raise AssertionError('Missing mobile_app service did not fail explicitly')
        assert len(received) == 1, 'Missing notification service triggered a fallback send'

    await phase('blueprint_missing_service', verify_missing_service())
    print('DOORBELL_RUNTIME blueprint OK', flush=True)


async def main(root):
    print(f'DOORBELL_RUNTIME core={importlib.metadata.version("homeassistant")}', flush=True)
    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await phase('device_registry_load', dr.async_load(hass))
        await phase('entity_registry_load', er.async_load(hass))
        # Core 2026.11 separates bootstrap loading from RestoreEntity registration;
        # older Core releases initialize storage from their restore mixin.
        if hasattr(restore_state, 'async_load'):
            await phase('restore_storage_load', restore_state.async_load(hass))
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Offline fixture', data={}, options={}, source='user', unique_id='doorbell-fixture',
            discovery_keys={}, subentries_data=None)
        hass.config_entries._entries[entry.entry_id] = entry
        try:
            await verify_events(hass, entry, root)
            await verify_blueprint(hass, entry, root)
        finally:
            await phase('hass_stop', hass.async_stop(force=True))
    print(f'REAL_HA_DOORBELL_BLUEPRINT_OK {importlib.metadata.version("homeassistant")}', flush=True)


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).resolve().parents[1]))
