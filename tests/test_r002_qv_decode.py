"""Synthetic encrypted QV records; no R002 hardware packet or device contact."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_connect3_discovery import synthetic_packet, synthetic_record
from test_r002_qv_discovery import FakeNetwork, qv
from test_transport_lifecycle import load_source

hub_module = load('r002.hub')


def packet():
    # Reuse the independently tested container, with clearly synthetic metadata.
    record = synthetic_record()
    record[0x188:0x19c] = b'SYNTH_R002' + bytes(10)
    return synthetic_packet(record)


class DecodeTests(unittest.IsolatedAsyncioTestCase):
    async def run_discovery(self, payloads, *, include_response=False, include_details=False):
        entry = SimpleNamespace(data={'host': '192.0.2.1'}, options={})
        hub = hub_module.R002InvestigationHub(None, entry)
        await hub.start()
        network = FakeNetwork([(payload, ('192.0.2.1', 5000), 5003) for payload in payloads])
        with patch.object(qv, '_open_listener', side_effect=network.open), patch.object(qv, 'TIMEOUT', .01), patch.object(
                hub_module, 'discover_qv', wraps=qv.discover_qv) as discovery:
            with self.assertNoLogs('welcomeeye_offline_fixture.r002', 'DEBUG'):
                result = await hub.discover_qv(include_response=include_response,
                                              include_details=include_details)
        discovery.assert_awaited_once_with('192.0.2.1', include_response=True)
        self.assertEqual(network.sent, [(5003, qv.REQUEST, qv.DESTINATION)])
        network.check_closed(self)
        self.assertIsNone(hub._task)
        self.assertEqual(hub.qv_discovery_runs, 1)
        await hub.stop()
        return result, hub, entry

    async def test_default_decodes_same_single_observation_without_exporting_bytes(self):
        data = packet()
        result, hub, _ = await self.run_discovery([data])
        self.assertEqual(result['decoded_records'], 1)
        self.assertTrue(result['metadata_decoded'])
        self.assertFalse(result['device_authenticated'])
        self.assertFalse(result['model_confirmed'])
        self.assertEqual(hub.status, 'qv_decoded')
        self.assertNotIn('records', result)
        for private in ('response_hex', data.hex(), 'SYNTHETIC_PRIVATE_UID', '192.0.2.1', 'SYNTH_R002'):
            self.assertNotIn(private, json.dumps(result))

    async def test_metadata_opt_in_is_ephemeral_and_never_establishes_identity(self):
        data = packet()
        result, hub, entry = await self.run_discovery([data], include_details=True)
        self.assertEqual(result['records'], [dict(device_type='SYNTH_R002', firmware='SYNTHETIC',
            stream_port=34567, cgi_port=443, tls_media_port=34568, channels=1)])
        self.assertNotIn('response_hex', json.dumps(result))
        self.assertFalse(result['device_authenticated'])
        self.assertFalse(result['model_confirmed'])
        stored = json.dumps([hub.diagnostics(), hub._per_type, hub._qv_discovery, entry.options])
        for private in ('SYNTH_R002', 'SYNTHETIC', 'SYNTHETIC_PRIVATE_UID', '192.0.2.1',
                        data.hex(), 'response_hex', '"records":', '"responses":'):
            self.assertNotIn(private, stored)
        self.assertEqual(entry.data, {'host': '192.0.2.1'})

    async def test_both_explicit_options_preserve_only_requested_response_detail(self):
        data = packet()
        result, hub, _ = await self.run_discovery([data], include_response=True, include_details=True)
        self.assertEqual(result['responses'][0]['response_hex'], data.hex())
        self.assertEqual(result['records'][0]['device_type'], 'SYNTH_R002')
        self.assertNotIn(data.hex(), json.dumps(hub.diagnostics()))
        self.assertNotIn('"records":', json.dumps(hub.diagnostics()))

    async def test_duplicate_replies_decode_once_without_another_request(self):
        result, hub, _ = await self.run_discovery([packet(), packet()], include_details=True)
        self.assertEqual(result['decoded_records'], 1)
        self.assertEqual(result['duplicate_records'], 1)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['request_sent_count'], 1)
        self.assertEqual(hub.diagnostics()['qv_discovery']['duplicate_records'], 1)

    async def test_rejected_packets_are_counted_without_inventing_metadata_or_retrying(self):
        result, hub, _ = await self.run_discovery([
            b'UNKNOWN_PRIVATE_PAYLOAD', b'ASZENO.SEARCH.V4.1', b'x' * 2049], include_details=True)
        self.assertEqual(result['decoded_records'], 0)
        self.assertFalse(result['metadata_decoded'])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['decode_errors'], {
            'unsupported_prefix': 1, 'truncated_envelope': 1, 'datagram_size': 1})
        self.assertEqual(result['request_sent_count'], 1)
        self.assertEqual(hub.status, 'qv_observed')
        self.assertNotIn('PRIVATE', json.dumps([result, hub.diagnostics()]))
        # Mutating a caller-owned response must not contaminate persisted state.
        result['decode_errors']['PRIVATE_CALLER_FIELD'] = 1
        self.assertNotIn('PRIVATE', json.dumps(hub.diagnostics()))
        downloaded = hub.diagnostics()
        downloaded['qv_discovery']['decode_errors']['PRIVATE_CALLER_FIELD'] = 1
        self.assertNotIn('PRIVATE', json.dumps(hub.diagnostics()))

    async def test_no_reply_keeps_observation_inconclusive(self):
        result, hub, _ = await self.run_discovery([], include_details=True)
        self.assertEqual(result['decoded_records'], 0)
        self.assertFalse(result['metadata_decoded'])
        self.assertEqual(result['collection_end_reason'], 'deadline')
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(hub.status, 'qv_observed')

    async def test_sensor_attributes_exclude_details_and_forward_explicit_options(self):
        class Entity:
            def __init__(self, hub, key):
                self.hub = hub
        class Sensor:
            pass
        class HAError(Exception):
            pass
        module = load_source('sensor', {'WelcomeEyeEntity': Entity, 'SensorEntity': Sensor,
            'EntityCategory': SimpleNamespace(DIAGNOSTIC='diagnostic'), 'HomeAssistantError': HAError})
        _, hub, _ = await self.run_discovery([packet()], include_response=True, include_details=True)
        sensor = module.WelcomeEyeProtocolStatus(hub)
        attributes = json.dumps(sensor.extra_state_attributes)
        for private in ('SYNTH_R002', 'SYNTHETIC_PRIVATE_UID', 'response_hex', 'records', '192.0.2.1'):
            self.assertNotIn(private, attributes)
        with patch.object(hub, 'discover_qv', AsyncMock(return_value={'metadata_decoded': True})) as discover:
            self.assertTrue((await sensor.async_r002_discover_qv(
                include_response=False, include_details=True))['metadata_decoded'])
        discover.assert_awaited_once_with(include_response=False, include_details=True)

    async def test_unload_before_response_never_decodes_or_leaves_a_task(self):
        hub = hub_module.R002InvestigationHub(None, SimpleNamespace(data={'host': '192.0.2.1'}))
        await hub.start()
        network = FakeNetwork()
        with patch.object(qv, '_open_listener', side_effect=network.open), patch.object(
                hub_module, 'decode_observation', wraps=hub_module.decode_observation) as decode:
            task = asyncio.create_task(hub.discover_qv(include_details=True))
            await network.sending.wait()
            await hub.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        decode.assert_not_called()
        self.assertIsNone(hub._task)
        network.check_closed(self)
        self.assertEqual(len(network.sent), 1)


class ServicePermissionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_identified_admin_and_entity_control_required_before_discovery(self):
        class HAError(Exception):
            pass
        module = load_source('services', {'HomeAssistantError': HAError, 'POLICY_CONTROL': 'control'})
        operation = AsyncMock(return_value={'metadata_decoded': True})
        permissions = SimpleNamespace(check_entity=Mock(return_value=True))
        user = SimpleNamespace(is_admin=True, permissions=permissions)
        auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
        entity = SimpleNamespace(entity_id='sensor.synthetic_r002', hass=SimpleNamespace(auth=auth),
            hub=SimpleNamespace(capabilities=SimpleNamespace(r002_probe=True)),
            async_r002_discover_qv=operation)
        call = SimpleNamespace(context=SimpleNamespace(user_id='synthetic_user'),
            data={'include_response': False, 'include_details': True})
        result = await module._async_r002_discover_qv(entity, call)
        self.assertTrue(result['metadata_decoded'])
        permissions.check_entity.assert_called_once_with('sensor.synthetic_r002', 'control')
        operation.assert_awaited_once_with(include_response=False, include_details=True)
        for admin, permission, user_id in ((False, True, 'user'), (True, False, 'user'),
                                          (True, True, None)):
            operation.reset_mock()
            user.is_admin = admin
            permissions.check_entity.return_value = permission
            call.context.user_id = user_id
            with self.assertRaises(HAError):
                await module._async_r002_discover_qv(entity, call)
            operation.assert_not_called()

    async def test_unavailable_family_never_performs_discovery(self):
        class HAError(Exception):
            pass
        module = load_source('services', {'HomeAssistantError': HAError, 'POLICY_CONTROL': 'control'})
        entity = SimpleNamespace(hub=SimpleNamespace(capabilities=SimpleNamespace(r002_probe=False)))
        with self.assertRaises(HAError):
            await module._async_r002_discover_qv(entity, SimpleNamespace())
