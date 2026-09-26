"""Actual flow methods with simulated HA/discovery, no device traffic."""
from hashlib import sha256
from ipaddress import IPv4Address
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import CAP_IMPORTS
from test_transport_lifecycle import client, load_source, ProtocolError


class Base:
    def __init_subclass__(cls, **kwargs):
        pass
    def _async_current_entries(self):
        return []
    def async_show_form(self, **kwargs):
        return {'type': 'form', **kwargs}
    def async_create_entry(self, **kwargs):
        return {'type': 'create_entry', **kwargs}
    def async_abort(self, **kwargs):
        return {'type': 'abort', **kwargs}
    async def async_set_unique_id(self, uid):
        self.uid = uid
    def _abort_if_unique_id_configured(self, **kwargs):
        pass
    def async_update_reload_and_abort(self, entry, data_updates):
        entry.data.update(data_updates)
        return {'type': 'abort', 'reason': 'reconfigure_successful'}


flow_module = load_source('config_flow', dict(
    **CAP_IMPORTS, IPv4Address=IPv4Address, sha256=sha256,
    config_entries=SimpleNamespace(ConfigFlow=Base),
    vol=SimpleNamespace(Schema=lambda data: data, Required=lambda key, **kw: key),
    DOMAIN='welcomeeye_local', DEFAULT_NAME='WelcomeEye',
    AuthenticationError=client.AuthenticationError, DiscoveryTimeout=client.DiscoveryTimeout,
    ProtocolError=ProtocolError, validate_connection=Mock(), fingerprint=AsyncMock(),
))
flow_module.schema = lambda defaults: defaults


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def flow(self, *, error=None):
        flow = flow_module.WelcomeEyeConfigFlow()
        flow.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value='legacy_uid', side_effect=error))
        return flow

    async def configure(self, flow):
        return await flow.async_step_user({'host': '192.0.2.1', 'username': 'admin', 'password': 'fixture-password'})

    async def test_normal_legacy_entries_remain_unclassified_until_media(self):
        for uid in ('r001_fixture_uid', 'v1_fixture_uid'):
            flow = self.flow()
            flow.hass.async_add_executor_job.return_value = uid
            with patch.object(flow_module, 'fingerprint', AsyncMock()) as fingerprint:
                result = await self.configure(flow)
            self.assertEqual(result['type'], 'create_entry')
            self.assertEqual(result['data']['protocol_family'], 'legacy_owsp')
            self.assertEqual(result['data']['uid'], uid)
            fingerprint.assert_not_called()

    async def test_only_discovery_absence_triggers_fingerprint(self):
        for error in (TimeoutError(), ConnectionRefusedError(), client.AuthenticationError(), ProtocolError()):
            with patch.object(flow_module, 'fingerprint', AsyncMock()) as fingerprint:
                result = await self.configure(self.flow(error=error))
            self.assertEqual(result['type'], 'form')
            fingerprint.assert_not_called()

    async def test_no_signature_remains_cannot_connect(self):
        with patch.object(flow_module, 'fingerprint', AsyncMock(return_value={'detected': False})) as fingerprint:
            result = await self.configure(self.flow(error=client.DiscoveryTimeout()))
        fingerprint.assert_awaited_once_with('192.0.2.1', 2)
        self.assertEqual(result['errors'], {'base': 'cannot_connect'})

    async def test_confirmation_required_and_no_credentials_persisted(self):
        flow = self.flow(error=client.DiscoveryTimeout())
        with patch.object(flow_module, 'fingerprint', AsyncMock(return_value={'detected': True})):
            result = await self.configure(flow)
        self.assertEqual(result['step_id'], 'r002_confirm')
        result = await flow.async_step_r002_confirm({'confirm': True})
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['data']['device_variant'], 'connect2_r002')
        self.assertEqual(result['data']['identity_source'], 'provisional_host_hash')
        self.assertEqual(flow.uid, 'r002-' + sha256(b'192.0.2.1').hexdigest()[:16])
        self.assertNotIn('password', result['data'])
        self.assertNotIn('username', result['data'])
        self.assertNotIn('uid', result['data'])

    async def test_decline_creates_no_entry(self):
        self.assertEqual(await self.flow().async_step_r002_confirm({'confirm': False}),
                         {'type': 'abort', 'reason': 'experimental_declined'})

    async def test_reconfigure_preserves_provisional_identity(self):
        flow = self.flow()
        entry = SimpleNamespace(entry_id='fixture', unique_id='r002-original',
            data={'host': '192.0.2.1', 'protocol_family': 'r002_experimental'})
        flow._get_reconfigure_entry = lambda: entry
        result = await flow.async_step_reconfigure({'host': '192.0.2.2'})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertEqual(entry.unique_id, 'r002-original')
        self.assertEqual(entry.data['host'], '192.0.2.2')
        flow.hass.async_add_executor_job.assert_not_called()

    async def test_invalid_address_and_existing_host_no_network(self):
        flow = self.flow()
        result = await flow.async_step_user({'host': 'invalid', 'username': '', 'password': ''})
        self.assertEqual(result['errors']['base'], 'cannot_connect')
        flow._async_current_entries = lambda: [SimpleNamespace(data={'host': '192.0.2.1'})]
        self.assertEqual((await self.configure(flow))['reason'], 'already_configured')
        flow.hass.async_add_executor_job.assert_not_called()

    async def test_discovery_timeout_keeps_type_through_executor(self):
        import asyncio
        def validate():
            raise client.DiscoveryTimeout(1)
        with self.assertRaises(client.DiscoveryTimeout) as error:
            await asyncio.to_thread(validate)
        self.assertEqual(error.exception.probe_count, 1)
