"""Doorbell entity boundaries: accepted rings only, no receiver/media side effects."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class EntityBoundary:
    async def async_added_to_hass(self):
        pass

    def async_on_remove(self, callback):
        if not hasattr(self, '_remove_callbacks'):
            self._remove_callbacks = []
        self._remove_callbacks.append(callback)

    def remove(self):
        for callback in self._remove_callbacks:
            callback()
        self._remove_callbacks.clear()


class EventBoundary(EntityBoundary):
    state = None

    def _trigger_event(self, kind, attributes):
        self.events.append((kind, attributes))
        self.state = 'new-event'


def namespace():
    scope = {'Entity': EntityBoundary, 'EventEntity': EventBoundary,
             'EventDeviceClass': SimpleNamespace(DOORBELL='doorbell'),
             'callback': lambda function: function, 'DeviceInfo': dict,
             'DOMAIN': 'welcomeeye_local'}
    for module in ('entity', 'event'):
        path = ROOT / f'custom_components/welcomeeye_local/{module}.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom)
                     and (node.level or node.module.startswith('homeassistant')))]
        exec(compile(tree, str(path), 'exec'), scope)
    return scope


class Bus:
    def __init__(self):
        self.listeners = []

    def async_listen(self, event_type, listener):
        assert event_type == 'welcomeeye_local.ring'
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def fire(self, data):
        for listener in list(self.listeners):
            listener(SimpleNamespace(data=data))


class DoorbellEventTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, qv=False, local=False, cloud=False, secondary=False, sequence=0):
        bus = Bus()
        hass = SimpleNamespace(bus=bus)
        listeners = set()

        def subscribe(listener):
            listeners.add(listener)
            return lambda: listeners.discard(listener)

        hub = SimpleNamespace(hass=hass, entry=SimpleNamespace(unique_id='stable',
            entry_id='entry', title='WelcomeEye'), device_model='Fixture', stopped=False,
            capabilities=SimpleNamespace(doorbell=local or cloud, local_ring=local, cloud_ring=cloud),
            secondary_channel_enabled=secondary, ring_count=sequence, _cloud_ring_sequence=sequence,
            ring_connected=local, ringing=False, v1_cloud=SimpleNamespace(connected=cloud),
            subscribe=subscribe)
        if qv:
            hub.qv_cloud = SimpleNamespace(connected=cloud)
        hub.entry.runtime_data = hub
        return hass, hub, listeners

    async def setup(self, **kwargs):
        hass, hub, listeners = self.fixture(**kwargs)
        created = []
        await namespace()['async_setup_entry'](hass, hub.entry, created.extend)
        for index, entity in enumerate(created):
            entity.hass = hass
            entity.entity_id = f'event.fixture_{index}'
            entity.async_write_ha_state = Mock()
            entity.events = []
            await entity.async_added_to_hass()
        return hass, hub, created, listeners

    def ring(self, hass, hub, *, sequence=1, channel=1, source='cloud_qv', **extra):
        hub.ring_count = hub._cloud_ring_sequence = sequence
        data = {'entry_id': 'entry', 'ring_sequence': sequence, 'channel': channel, **extra}
        if source is not None:
            data['source'] = source
        hass.bus.fire(data)
        return data

    async def test_capability_matrix_and_new_stable_ids_leave_existing_ids_alone(self):
        for options, expected in (({}, 0), ({'local': True}, 1), ({'cloud': True}, 1),
                                  ({'qv': True}, 0), ({'qv': True, 'cloud': True}, 1),
                                  ({'qv': True, 'cloud': True, 'secondary': True}, 2),
                                  ({'local': True, 'secondary': True}, 1)):
            _, _, entities, _ = await self.setup(**options)
            self.assertEqual(len(entities), expected)
            self.assertEqual([item._attr_unique_id for item in entities],
                ['stable_doorbell_event', 'stable_doorbell_event_channel_2'][:expected])
            self.assertTrue(all(item._attr_event_types == ['ring'] for item in entities))
            self.assertTrue(all(item._attr_device_class == 'doorbell' for item in entities))

    async def test_qv_filters_entry_channel_and_duplicates_without_copying_payload(self):
        hass, hub, entities, _ = await self.setup(qv=True, cloud=True, secondary=True)
        data = self.ring(hass, hub, channel=2, arbitrary_secret='never publish')
        hass.bus.fire(data)
        self.assertEqual(entities[0].events, [])
        self.assertEqual(entities[1].events, [('ring', {'source': 'cloud_qv',
            'ring_sequence': 1, 'reported_channel': 2, 'channel': 2})])
        self.ring(hass, hub, sequence=2, channel=1)
        self.assertEqual(len(entities[0].events), 1)
        self.assertEqual(len(entities[1].events), 1)

    async def test_malformed_wrong_source_old_and_forged_future_sequences_do_not_poison(self):
        hass, hub, (entity,), _ = await self.setup(qv=True, cloud=True)
        hub._cloud_ring_sequence = 1
        valid = {'entry_id': 'entry', 'ring_sequence': 1, 'channel': 1, 'source': 'cloud_qv'}
        for override in ({'entry_id': 'other'}, {'channel': True}, {'channel': '1'},
                         {'channel': 2}, {'source': 'cloud'}, {'source': None},
                         {'ring_sequence': True}, {'ring_sequence': 0},
                         {'ring_sequence': '1'}, {'ring_sequence': 999999999999}):
            hass.bus.fire({**valid, **override})
        hass.bus.fire(None)
        self.assertFalse(entity.events)
        hass.bus.fire(valid)
        self.assertEqual(len(entity.events), 1)
        hub._cloud_ring_sequence = 3
        hass.bus.fire({**valid, 'ring_sequence': 0})
        self.assertEqual(len(entity.events), 1)
        hass.bus.fire({**valid, 'ring_sequence': 3})
        self.assertEqual(len(entity.events), 2)

    async def test_two_accepted_rings_queued_before_dispatch_both_reach_the_entity(self):
        hass, hub, (entity,), _ = await self.setup(qv=True, cloud=True)
        hub._cloud_ring_sequence = 2
        for sequence in (1, 2):
            hass.bus.fire({'entry_id': 'entry', 'ring_sequence': sequence,
                           'channel': 1, 'source': 'cloud_qv'})
        self.assertEqual([event[1]['ring_sequence'] for event in entity.events], [1, 2])

    async def test_legacy_raw_channel_is_reported_without_claiming_physical_channel(self):
        for options, source, channel in (({'local': True}, None, 16),
                                         ({'cloud': True}, 'cloud', 256)):
            hass, hub, (entity,), _ = await self.setup(**options, secondary=True)
            self.ring(hass, hub, source=source, channel=channel)
            attributes = entity.events[0][1]
            self.assertEqual(attributes['reported_channel'], channel)
            self.assertNotIn('channel', attributes)
            self.assertEqual(attributes['source'], source or 'local')
            self.ring(hass, hub, sequence=2, source='cloud_qv', channel=1)
            self.assertEqual(len(entity.events), 1)

    async def test_no_startup_replay_rename_preserves_state_unload_removes_subscriptions(self):
        hass, hub, (entity,), listeners = await self.setup(qv=True, cloud=True, sequence=5)
        self.assertIsNone(entity.state)
        self.ring(hass, hub, sequence=5)
        self.assertFalse(entity.events)
        self.ring(hass, hub, sequence=6)
        old_id = entity._attr_unique_id
        entity.async_entity_id_changed('event.previous_name')
        self.assertEqual(entity._attr_unique_id, old_id)
        self.assertEqual(len(hass.bus.listeners), 1)
        self.assertEqual(entity.state, 'new-event')
        callback = hass.bus.listeners[0]
        entity.remove()
        self.assertFalse(hass.bus.listeners)
        self.assertFalse(listeners)
        hub._cloud_ring_sequence = 7
        callback(SimpleNamespace(data={'entry_id': 'entry', 'ring_sequence': 7,
            'channel': 1, 'source': 'cloud_qv'}))
        await entity.async_added_to_hass()
        self.ring(hass, hub, sequence=7)
        self.assertEqual(len(entity.events), 1)
        self.ring(hass, hub, sequence=8)
        self.assertEqual(len(entity.events), 2)

    async def test_connection_changes_and_stopped_or_disabled_receiver_never_emit(self):
        hass, hub, (entity,), listeners = await self.setup(qv=True, cloud=True)
        self.assertTrue(entity.available)
        hub.qv_cloud.connected = False
        for listener in listeners:
            listener()
        self.assertFalse(entity.available)
        self.assertFalse(entity.events)
        hub.stopped = True
        self.ring(hass, hub)
        hub.stopped = False
        hub.capabilities.cloud_ring = False
        self.ring(hass, hub, sequence=2)
        self.assertFalse(entity.events)
