"""Synthetic TLS errors and HA Repairs routing; no device or physical command."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import ssl
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from load_integration import load, PACKAGE

hub_module = load('connect3.hub')
live = load('connect3.live')
cgi = load('connect3.cgi')
talk = load('connect3.talk')
cap = load('capabilities')
r002 = load('r002.hub')
ROOT = Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local'
PIN, AUTH = 'a' * 64, 'SYNTHETIC_RUNTIME_AUTH'
ZERO_DATE = '1969-12-31T16:00:27+00:00'


def date_exception(pin=PIN, date=ZERO_DATE):
    return {'policy': 'zero_duration_v1', 'certificate_sha256': pin,
            'not_valid_before': date, 'not_valid_after': date}


class RepairBase:
    def async_show_form(self, **kwargs):
        return {'type': 'form', **kwargs}
    def async_abort(self, **kwargs):
        return {'type': 'abort', **kwargs}


def repairs_module():
    """Run production Repairs code against a small registry/flow interface."""
    tree = ast.parse((ROOT / 'repairs.py').read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    module = ModuleType(f'{PACKAGE}.repairs')
    scope = module.__dict__
    scope.update(RepairsFlow=RepairBase,
        repairs_platform=SimpleNamespace(FlowType=SimpleNamespace(CONFIG_FLOW='config_flow')),
        SOURCE_RECONFIGURE='reconfigure', DOMAIN='welcomeeye_local',
        DeviceVariant=cap.DeviceVariant, variant_for=cap.variant_for,
        vol=SimpleNamespace(Schema=lambda data: data))
    exec(compile(tree, str(ROOT / 'repairs.py'), 'exec'), scope)
    return module


class TLSRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.issues = {}
        self.create_count = 0
        self.repairs = repairs_module()
        def create(hass, domain, issue_id, **kwargs):
            self.create_count += 1
            self.issues[domain, issue_id] = SimpleNamespace(**kwargs)
        self.registry = SimpleNamespace(async_get_issue=lambda domain, issue_id: self.issues.get((domain, issue_id)))
        self.repairs.ir = SimpleNamespace(async_create_issue=create,
            async_delete_issue=lambda hass, domain, issue_id: self.issues.pop((domain, issue_id), None),
            async_get=lambda hass: self.registry, IssueSeverity=SimpleNamespace(ERROR='error'))
        self.patcher = patch.dict(sys.modules, {self.repairs.__name__: self.repairs})
        self.patcher.start()
        self.entry = SimpleNamespace(entry_id='SYNTHETIC_ENTRY_UUID', data={
            'host': '192.0.2.33', 'auth_code': AUTH, 'certificate_sha256': PIN,
            'media_certificate_sha256': 'b' * 64, 'experimental_video': True,
            'protocol_family': cap.ProtocolFamily.CONNECT3})
        self.hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda entry_id: self.entry))
        self.hub = hub_module.Connect3Hub(self.hass, self.entry)
        await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()
        self.patcher.stop()

    def issue(self):
        return self.issues['welcomeeye_local', self.repairs.tls_issue_id(self.entry.entry_id)]

    def approve_zero_duration(self):
        self.entry.data.update(
            trust_endpoint={'host': '192.0.2.33', 'cgi_port': 443,
                            'media_port': 8443, 'media_transport': 'tls'},
            tls_certificate_expires={'cgi': ZERO_DATE,
                'media': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
            tls_certificate_date_exceptions={'cgi': date_exception()})

    def clear_test_block(self):
        self.hub._tls_blocked_reason = self.hub._tls_blocked_endpoint = None
        self.repairs.async_clear_tls_issue(self.hass, self.entry.entry_id)

    async def assert_expiry_blocked_without_request(self, endpoint='cgi'):
        with patch.object(hub_module, 'read_device', AsyncMock()) as read:
            result = await self.hub.execute('access')
        read.assert_not_called()
        self.assertEqual(result['reason'], 'tls_reapproval_required')
        self.assertEqual(self.issue().data, {
            'entry_id': self.entry.entry_id, 'reason': 'certificate_expired', 'endpoint': endpoint})

    async def test_private_exact_pin_date_approval_survives_stored_entry_reload(self):
        self.approve_zero_duration()
        stored = json.dumps(self.entry.data)
        reloaded = hub_module.Connect3Hub(self.hass,
            SimpleNamespace(entry_id=self.entry.entry_id, data=json.loads(stored)))
        await reloaded.start()
        try:
            for hub in (self.hub, reloaded):
                hub.check_tls_trust()
                with patch.object(hub_module, 'read_device', AsyncMock(
                        return_value={'authentication': 'cgi_accepted'})) as read:
                    result = await hub.execute('access')
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(read.await_args.kwargs['certificate_sha256'], PIN)
                self.assertEqual(hub.entry.data['tls_certificate_expires']['cgi'], ZERO_DATE)
                exported = json.dumps([result, hub.diagnostics()])
                for private in (PIN, AUTH, ZERO_DATE, 'zero_duration_v1',
                                'tls_certificate_date_exceptions'):
                    self.assertNotIn(private, exported)
            self.assertFalse(self.issues)
            self.assertEqual(json.dumps(reloaded.entry.data), stored)
        finally:
            await reloaded.stop()

    async def test_missing_or_malformed_date_approval_never_bypasses_past_expiry(self):
        self.approve_zero_duration()
        malformed = (
            None, [], False, {}, {'cgi': None}, {'cgi': []},
            {'cgi': {**date_exception(), 'policy': 'PRIVATE_UNKNOWN_POLICY'}},
            {'cgi': {**date_exception(), 'extra': True}},
            {'cgi': {'policy': 'zero_duration_v1', 'certificate_sha256': PIN}},
            {'cgi': {**date_exception(), 'not_valid_before': '1969-12-31T16:00:26+00:00'}},
            {'cgi': {**date_exception(), 'not_valid_before': 'PRIVATE_DATE'}},
            {'cgi': date_exception('c' * 64)}, {'cgi': date_exception('')},
            {'media': date_exception()}, date_exception(),
        )
        for record in malformed:
            with self.subTest(record=record):
                self.entry.data['tls_certificate_date_exceptions'] = record
                await self.assert_expiry_blocked_without_request(
                    'both' if record is not None and type(record) is not dict else 'cgi')
                self.assertNotIn('PRIVATE', json.dumps([self.hub.diagnostics(), vars(self.issue())]))
                self.clear_test_block()

    async def test_date_approval_requires_current_pin_and_exact_stored_expiry(self):
        self.approve_zero_duration()
        for changes in ({'certificate_sha256': 'c' * 64}, {'certificate_sha256': ''},
                        {'tls_certificate_expires': {'cgi': '1969-12-31T16:00:28+00:00',
                            'media': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}):
            with self.subTest(changes=changes):
                self.entry.data.update(changes)
                await self.assert_expiry_blocked_without_request()
                self.clear_test_block()
                self.approve_zero_duration()
                self.entry.data['certificate_sha256'] = PIN

    async def test_date_approval_does_not_rebind_endpoint_or_apply_to_legacy_unbound_entry(self):
        self.approve_zero_duration()
        self.entry.data['host'] = '192.0.2.34'
        with patch.object(hub_module, 'read_device', AsyncMock()) as read:
            result = await self.hub.execute('access')
        read.assert_not_called()
        self.assertEqual(result['reason'], 'tls_reapproval_required')
        self.assertEqual(self.issue().data['reason'], 'endpoint_changed')
        self.clear_test_block()
        self.entry.data['host'] = '192.0.2.33'
        self.entry.data['trust_endpoint'] = None
        await self.assert_expiry_blocked_without_request()
        self.clear_test_block()
        self.entry.data.pop('tls_certificate_expires')
        await self.assert_expiry_blocked_without_request('both')

    async def test_cgi_and_media_date_approvals_are_independent(self):
        self.approve_zero_duration()
        self.entry.data['tls_certificate_expires']['media'] = ZERO_DATE
        await self.assert_expiry_blocked_without_request('media')
        self.clear_test_block()
        self.entry.data['tls_certificate_date_exceptions']['media'] = date_exception(PIN)
        await self.assert_expiry_blocked_without_request('media')
        self.clear_test_block()
        self.entry.data['tls_certificate_date_exceptions']['media'] = date_exception('b' * 64)
        self.hub.check_tls_trust()
        self.assertFalse(self.issues)
        self.entry.data['media_certificate_sha256'] = ''
        self.entry.data['tls_certificate_date_exceptions']['media'] = date_exception(PIN)
        self.hub.check_tls_trust()
        self.assertFalse(self.issues)

    async def test_tcp_date_approval_checks_only_its_bound_https_endpoint(self):
        self.approve_zero_duration()
        self.entry.data.update(media_transport='connect3_tcp', media_tcp_approved=True)
        self.entry.data['trust_endpoint'].update(media_transport='connect3_tcp', media_port=34567)
        self.entry.data['tls_certificate_expires'] = {'cgi': ZERO_DATE}
        self.hub.check_tls_trust()
        self.assertFalse(self.issues)
        self.entry.data['trust_endpoint']['media_port'] = 8443
        with self.assertRaisesRegex(cgi.CGIError, '^tls_reapproval_required$'):
            self.hub.check_tls_trust()
        self.assertEqual(self.issue().data['reason'], 'endpoint_changed')

    async def test_ordinary_expired_media_remains_denied_with_cgi_date_approval(self):
        self.approve_zero_duration()
        self.entry.data['tls_certificate_expires']['media'] = (
            datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.entry.data['tls_certificate_date_exceptions']['media'] = date_exception('b' * 64)
        await self.assert_expiry_blocked_without_request('media')

    async def test_future_date_record_still_requires_exact_pin_date_and_policy(self):
        self.approve_zero_duration()
        future = '2099-01-01T00:00:00+00:00'
        self.entry.data['tls_certificate_expires']['cgi'] = future
        self.entry.data['tls_certificate_date_exceptions']['cgi'] = date_exception(date=future)
        self.hub.check_tls_trust()
        for record in (date_exception('c' * 64, future), date_exception(date=ZERO_DATE),
                       {**date_exception(date=future), 'policy': 'PRIVATE_INVALID_POLICY'}, None):
            with self.subTest(record=record):
                self.entry.data['tls_certificate_date_exceptions']['cgi'] = record
                await self.assert_expiry_blocked_without_request()
                self.clear_test_block()

    async def test_future_ordinary_expiry_without_exception_keeps_previous_policy(self):
        self.approve_zero_duration()
        self.entry.data['tls_certificate_expires']['cgi'] = '2099-01-01T00:00:00+00:00'
        for container in ({}, None):
            self.entry.data['tls_certificate_date_exceptions'] = container
            self.hub.check_tls_trust()
            self.assertFalse(self.issues)
        self.entry.data.pop('tls_certificate_date_exceptions')
        self.hub.check_tls_trust()
        for malformed in ([], '', False):
            with self.subTest(container=malformed):
                self.entry.data['tls_certificate_date_exceptions'] = malformed
                await self.assert_expiry_blocked_without_request('both')
                self.clear_test_block()

    async def test_cgi_pin_change_reports_persistent_repair_and_blocks_next_attempt(self):
        mismatch = aiohttp.ServerFingerprintMismatch(bytes.fromhex(PIN), bytes.fromhex('b' * 64), '192.0.2.33', 443)
        with patch.object(hub_module, 'read_device', AsyncMock(side_effect=mismatch)) as read:
            first = await self.hub.execute('access')
            second = await self.hub.execute('access')
        read.assert_awaited_once()
        self.assertEqual(first['reason'], 'tls_reapproval_required')
        self.assertEqual(second['reason'], 'tls_reapproval_required')
        self.assertEqual(self.issue().translation_key, 'connect3_tls_certificate_changed')
        self.assertTrue(self.issue().is_persistent)
        self.assertTrue(self.issue().is_fixable)
        self.assertEqual(self.issue().severity, 'error')
        self.assertEqual(self.create_count, 1)
        self.assertEqual(self.entry.data['certificate_sha256'], PIN)
        text = json.dumps([first, second, self.hub.diagnostics(), vars(self.issue())])
        for private in (PIN, 'b' * 64, AUTH, '192.0.2.33'):
            self.assertNotIn(private, text)

    async def test_media_pin_change_reports_and_blocks_cgi_on_next_acquisition(self):
        session = SimpleNamespace(run=AsyncMock(side_effect=live.MediaTLSFailure('media_certificate_pin_mismatch')),
                                  close=AsyncMock())
        decoder = SimpleNamespace(errors=0, close=Mock())
        audio = SimpleNamespace(diagnostics={}, close=Mock())
        with patch.object(live, 'read_stream_material', AsyncMock(return_value=cgi.StreamMaterial('SYNTHETIC_KEY'))) as read, \
                patch.object(live, 'QVSession', Mock(return_value=session)) as factory, \
                patch.object(live, 'VideoDecoder', Mock(return_value=decoder)), \
                patch.object(live, 'AudioDecoder', Mock(return_value=audio)):
            for _ in range(2):
                with self.assertRaises((RuntimeError, cgi.CGIError)):
                    await self.hub.acquire('viewer')
        read.assert_awaited_once()
        factory.assert_called_once()
        session.close.assert_awaited_once()
        self.assertFalse(self.hub.connected)
        self.assertFalse(self.hub.consumers)
        self.assertEqual(self.issue().data['endpoint'], 'media')
        self.assertEqual(self.issue().data['reason'], 'certificate_changed')

    async def test_approved_endpoint_change_stops_before_discovery_cgi_or_media(self):
        self.entry.data['trust_endpoint'] = {'host': '192.0.2.33', 'cgi_port': 443, 'media_port': 8443}
        self.entry.data['host'] = '192.0.2.34'
        self.entry.data.update(credential_source='apk_space', credential_device_uid='SYNTHETIC_UID')
        with patch.object(hub_module, 'read_device', AsyncMock()) as read, \
                patch.object(hub_module, 'discover', AsyncMock()) as discover:
            result = await self.hub.execute('access')
        with patch.object(live, 'read_stream_material', AsyncMock()) as material, \
                patch.object(live, 'QVSession', Mock()) as session:
            with self.assertRaises((RuntimeError, cgi.CGIError)):
                await self.hub.acquire('viewer')
        for call in (read, discover, material, session):
            call.assert_not_called()
        self.assertEqual(result['reason'], 'tls_reapproval_required')
        self.assertEqual(self.issue().translation_key, 'connect3_tls_endpoint_changed')

    async def test_expiry_guard_blocks_new_metadata_before_network(self):
        self.entry.data['tls_certificate_expires'] = {
            'cgi': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            'media': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
        with patch.object(hub_module, 'read_device', AsyncMock()) as read:
            result = await self.hub.execute('access')
        read.assert_not_called()
        self.assertEqual(result['reason'], 'tls_reapproval_required')
        self.assertEqual(self.issue().translation_key, 'connect3_tls_certificate_expired')
        self.assertEqual(self.issue().data['endpoint'], 'media')

    async def test_malformed_nonempty_expiry_fails_closed(self):
        self.entry.data['tls_certificate_expires'] = {'cgi': 'PRIVATE_MALFORMED_DATE', 'media': 'PRIVATE'}
        with patch.object(hub_module, 'read_device', AsyncMock()) as read:
            await self.hub.execute('access')
        read.assert_not_called()
        self.assertEqual(self.issue().data['reason'], 'certificate_expired')
        self.assertNotIn('PRIVATE', json.dumps(vars(self.issue())))

    async def test_falsy_malformed_expiry_fails_closed_even_for_legacy_entry(self):
        for malformed in ([], '', False, 0):
            with self.subTest(metadata=malformed):
                self.entry.data['tls_certificate_expires'] = malformed
                with patch.object(hub_module, 'read_device', AsyncMock()) as read:
                    result = await self.hub.execute('access')
                read.assert_not_called()
                self.assertEqual(result['reason'], 'tls_reapproval_required')
                self.assertEqual(self.issue().data['reason'], 'certificate_expired')
                self.hub._tls_blocked_reason = self.hub._tls_blocked_endpoint = None
                self.repairs.async_clear_tls_issue(self.hass, self.entry.entry_id)

    async def test_bound_endpoint_requires_exact_complete_expiry_metadata(self):
        self.entry.data['trust_endpoint'] = {'host': '192.0.2.33', 'cgi_port': 443, 'media_port': 8443}
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        for metadata in ({}, None, 'absent', {'cgi': future}, {'cgi': future, 'media': future, 'extra': future}):
            with self.subTest(metadata=metadata):
                if metadata == 'absent':
                    self.entry.data.pop('tls_certificate_expires', None)
                else:
                    self.entry.data['tls_certificate_expires'] = metadata
                with patch.object(hub_module, 'read_device', AsyncMock()) as read:
                    result = await self.hub.execute('access')
                read.assert_not_called()
                self.assertEqual(result['reason'], 'tls_reapproval_required')
                self.assertEqual(self.issue().data['reason'], 'certificate_expired')
                self.hub._tls_blocked_reason = self.hub._tls_blocked_endpoint = None
                self.repairs.async_clear_tls_issue(self.hass, self.entry.entry_id)

    async def test_legacy_and_cleared_metadata_keep_existing_manual_pins(self):
        for extra in ({}, {'trust_endpoint': None, 'tls_certificate_expires': {}}):
            self.entry.data.update(extra)
            with patch.object(hub_module, 'read_device', AsyncMock(return_value={'authentication': 'cgi_accepted'})) as read:
                result = await self.hub.execute('access')
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(read.await_args.kwargs['certificate_sha256'], PIN)
            self.assertFalse(self.issues)

    async def test_system_ca_failure_is_actionable_without_auto_pin(self):
        self.entry.data['certificate_sha256'] = ''
        failure = aiohttp.ClientConnectorCertificateError(SimpleNamespace(), ssl.SSLCertVerificationError('PRIVATE_CERT_ERROR'))
        with patch.object(hub_module, 'read_device', AsyncMock(side_effect=failure)):
            result = await self.hub.execute('access')
        self.assertEqual(result['reason'], 'tls_reapproval_required')
        self.assertEqual(self.issue().translation_key, 'connect3_tls_system_ca_failed')
        self.assertEqual(self.entry.data['certificate_sha256'], '')
        self.assertNotIn('PRIVATE_CERT_ERROR', json.dumps([result, self.hub.diagnostics(), vars(self.issue())]))

    async def test_media_ca_error_and_missing_certificate_are_distinct(self):
        self.assertEqual(self.hub.report_tls_error(ssl.SSLCertVerificationError('PRIVATE'), endpoint='media'), 'system_ca_failed')
        self.assertEqual(self.issue().data['endpoint'], 'media')
        self.assertIsNone(self.hub.report_tls_error(live.MediaTLSFailure('missing_media_certificate'), endpoint='media'))
        self.assertIsNone(self.hub.report_tls_error(TimeoutError('PRIVATE'), endpoint='cgi'))

    async def test_block_survives_reload_and_ignored_issue_until_reapproval(self):
        self.repairs.async_report_tls_issue(self.hass, self.entry.entry_id, 'certificate_changed', 'media')
        self.issue().dismissed_version = '2026.10.0'
        reloaded = hub_module.Connect3Hub(self.hass, self.entry)
        await reloaded.start()
        try:
            with self.assertRaisesRegex(cgi.CGIError, '^tls_reapproval_required$'):
                reloaded.check_tls_trust()
            self.repairs.async_clear_tls_issue(self.hass, self.entry.entry_id)
            approved = hub_module.Connect3Hub(self.hass, self.entry)
            await approved.start()
            approved.check_tls_trust()
            await approved.stop()
        finally:
            await reloaded.stop()

    async def test_shared_r002_hub_does_not_activate_connect3_policy(self):
        data = {**self.entry.data, 'trust_endpoint': {'host': 'PRIVATE'},
                'tls_certificate_expires': {'cgi': 'PRIVATE'},
                'tls_certificate_date_exceptions': {'cgi': date_exception()}}
        r002_hub = r002.R002InvestigationHub(self.hass, SimpleNamespace(entry_id='R002_ENTRY', data=data))
        await r002_hub.start()
        try:
            r002_hub.check_tls_trust()
            r002_hub.report_tls_error(live.MediaTLSFailure('media_certificate_pin_mismatch'), endpoint='media')
            self.assertFalse(self.issues)
            self.assertNotIn('tls_trust', r002_hub.diagnostics())
        finally:
            await r002_hub.stop()

    async def test_talk_tls_failure_reports_before_any_application_write(self):
        self.hub.live.connected = True
        params = {'host': '192.0.2.33', 'port': 8443, 'pin': PIN, 'stream_key': 'PRIVATE', 'password': AUTH}
        with patch.object(self.hub.live, 'talk_parameters', Mock(return_value=params)), \
                patch.object(talk, 'open_media_tls', AsyncMock(side_effect=live.MediaTLSFailure('media_certificate_pin_mismatch'))), \
                patch.object(self.hub.talkback, '_send', AsyncMock()) as send:
            with self.assertRaises(live.MediaTLSFailure):
                await self.hub.talkback.start('viewer')
        send.assert_not_called()
        self.assertEqual(self.issue().data['endpoint'], 'media')
        self.assertFalse(self.hub.talkback.active)

    async def test_repair_forwards_to_reconfigure_without_clearing_or_inspecting(self):
        self.repairs.async_report_tls_issue(self.hass, self.entry.entry_id, 'certificate_changed', 'cgi')
        self.hass.config_entries.flow = SimpleNamespace(async_init=AsyncMock(return_value={'type': 'form', 'flow_id': 'SYNTHETIC_FLOW'}))
        repair = await self.repairs.async_create_fix_flow(self.hass, self.repairs.tls_issue_id(self.entry.entry_id), self.issue().data)
        repair.hass, repair.data = self.hass, self.issue().data
        self.assertEqual((await repair.async_step_init({'issue_id': self.repairs.tls_issue_id(self.entry.entry_id)}))['step_id'], 'confirm')
        self.hass.config_entries.flow.async_init.assert_not_called()
        result = await repair.async_step_confirm({})
        self.assertEqual(result['next_flow'], ('config_flow', 'SYNTHETIC_FLOW'))
        self.hass.config_entries.flow.async_init.assert_awaited_once_with('welcomeeye_local',
            context={'source': 'reconfigure', 'entry_id': self.entry.entry_id})
        self.assertTrue(self.issues)
        self.assertEqual(self.entry.data['certificate_sha256'], PIN)

    async def test_repair_refuses_missing_or_other_family_entry(self):
        repair = self.repairs.Connect3TLSRepairFlow()
        repair.hass, repair.data = self.hass, {'entry_id': self.entry.entry_id}
        self.hass.config_entries.async_get_entry = lambda _: None
        self.assertEqual((await repair.async_step_confirm({}))['reason'], 'entry_not_found')
        self.entry.data['protocol_family'] = cap.ProtocolFamily.R002
        self.hass.config_entries.async_get_entry = lambda _: self.entry
        self.assertEqual((await repair.async_step_confirm({}))['reason'], 'entry_not_found')
        self.assertIsNone(await self.repairs.async_create_fix_flow(self.hass, 'other_issue', {}))

    async def test_legacy_repairs_api_links_settings_without_starting_invisible_flow(self):
        self.repairs.async_report_tls_issue(self.hass, self.entry.entry_id, 'certificate_changed', 'cgi')
        self.repairs._FLOW_TYPE = None
        self.hass.config_entries.flow = SimpleNamespace(async_init=AsyncMock())
        repair = self.repairs.Connect3TLSRepairFlow()
        repair.hass, repair.data = self.hass, self.issue().data
        self.assertEqual((await repair.async_step_init())['step_id'], 'confirm')
        result = await repair.async_step_confirm({})
        self.assertEqual(result['reason'], 'reconfigure_legacy')
        self.assertEqual(result['description_placeholders'], {'config_entry': self.entry.entry_id})
        self.hass.config_entries.flow.async_init.assert_not_called()
        self.assertTrue(self.issues)

    async def test_talk_certificate_change_closes_active_live_and_blocks_shared_acquire(self):
        self.hub.live.connected = True
        self.hub.live.consumers.add('existing_viewer')
        self.hub.live.task = asyncio.create_task(asyncio.sleep(30))
        self.hub.report_tls_error(live.MediaTLSFailure('media_certificate_pin_mismatch'), endpoint='media')
        self.assertFalse(self.hub.connected)
        with self.assertRaisesRegex(cgi.CGIError, '^tls_reapproval_required$'):
            await self.hub.acquire('another_viewer')
        await self.hub._tls_close_task
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.live.task)

    async def test_removing_connect3_entry_clears_its_persistent_issue(self):
        tree = ast.parse((ROOT / '__init__.py').read_text(encoding='utf-8'))
        tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'async_remove_entry']
        scope = {'__name__': PACKAGE, '__package__': PACKAGE, 'HomeAssistant': object,
                 'ConfigEntry': object, 'variant_for': cap.variant_for, 'DeviceVariant': cap.DeviceVariant}
        exec(compile(tree, str(ROOT / '__init__.py'), 'exec'), scope)
        self.repairs.async_report_tls_issue(self.hass, self.entry.entry_id, 'certificate_changed', 'cgi')
        await scope['async_remove_entry'](self.hass, self.entry)
        self.assertFalse(self.issues)
        for data in ({'protocol_family': 'unknown'}, {},
                     {'protocol_family': cap.ProtocolFamily.R002}):
            with patch.object(self.repairs, 'async_clear_tls_issue') as clear:
                await scope['async_remove_entry'](self.hass, SimpleNamespace(data=data))
                clear.assert_not_called()


if __name__ == '__main__':
    unittest.main()
