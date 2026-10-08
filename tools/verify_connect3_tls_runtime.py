"""Native HA config/Repairs flows and persistence; synthetic TLS results only.

Run in the supported HA Core images. The manifest's existing aiortc dependency
is installed with HA's package helper; no new dependency is introduced. All
device TCP, UDP, CGI, media and output paths are forbidden throughout the test.
"""
import asyncio
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import voluptuous_serialize

from homeassistant import config_entries
from homeassistant.components.repairs import RepairsFlowManager
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package

DOMAIN = 'welcomeeye_local'
AUTH = 'SYNTHETIC_TLS_LOCAL_PASSWORD'
OPENING = 'SYNTHETIC_TLS_OPENING_CODE'
CGI_PIN, MEDIA_PIN = 'a' * 64, 'b' * 64
NEW_CGI_PIN, NEW_MEDIA_PIN = 'c' * 64, 'd' * 64
INPUT = {'host': '192.0.2.10', 'auth_code': AUTH,
         'experimental_video': True, 'experimental_outputs': True, 'opening_code': OPENING}


def serialize_form(result):
    """Use the same serializer as HA's native config/Repairs HTTP views."""
    return voluptuous_serialize.convert(result['data_schema'], custom_serializer=cv.custom_serializer)


def field(fields, name):
    return next(item for item in fields if item['name'] == name)


async def initialize_hass(directory):
    hass = HomeAssistant(directory)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await ir.async_load(hass)
    await hass.config_entries.async_initialize()
    if hasattr(dr, 'async_setup'):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    return hass


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        assert await asyncio.to_thread(install_package, manifest['requirements'][0], **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        package = 'custom_components.welcomeeye_local'
        integration = importlib.import_module(package)
        config = importlib.import_module(package + '.config_flow')
        trust = importlib.import_module(package + '.connect3.trust')
        hub_module = importlib.import_module(package + '.connect3.hub')
        live = importlib.import_module(package + '.connect3.live')
        cgi = importlib.import_module(package + '.connect3.cgi')
        discovery = importlib.import_module(package + '.connect3.discovery')
        qv = importlib.import_module(package + '.r002.qv_discovery')
        repairs = importlib.import_module(package + '.repairs')
        diagnostics = importlib.import_module(package + '.diagnostics')

        def inspection(status='candidate', *, cgi_pin=CGI_PIN, media_pin=MEDIA_PIN,
                       media_status=None, reason=None):
            return trust.TrustInspection(
                trust.EndpointTrust(status, cgi_pin, reason, validity_status='valid',
                    identity_status='ip_match', not_valid_after='2099-01-01T00:00:00+00:00'),
                trust.EndpointTrust(media_status or status, media_pin, reason, validity_status='valid',
                    identity_status='ip_match', not_valid_after='2099-01-02T00:00:00+00:00'))

        hass = await initialize_hass(temporary)
        hubs = []
        with ExitStack() as stack:
            # Flow/platform discovery is isolated; the real HA manager still
            # validates schemas, creates ConfigEntries and processes results.
            async def create_config_flow(handler, *, context=None, data=None):
                assert handler == DOMAIN
                flow = config.WelcomeEyeConfigFlow()
                flow.init_step = context['source']
                return flow
            stack.enter_context(patch.object(hass.config_entries.flow, 'async_create_flow', side_effect=create_config_flow))
            if hasattr(config_entries, '_support_single_config_entry_only'):
                stack.enter_context(patch.object(config_entries, '_support_single_config_entry_only', AsyncMock(return_value=False)))
            stack.enter_context(patch.object(hass.config_entries, 'async_setup', AsyncMock(return_value=True)))
            stack.enter_context(patch.object(hass.config_entries, 'async_reload', AsyncMock(return_value=True)))
            forbidden = 'Device networking or physical operation forbidden in TLS flow verification'
            stack.enter_context(patch.object(asyncio, 'open_connection', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(socket, 'create_connection', Mock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(qv, '_open_listener', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(discovery, 'discover', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(hub_module, 'discover', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(live, 'discover', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(cgi, '_post', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(live, 'read_stream_material', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(live, 'QVSession', Mock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(config, 'validate_connection', Mock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(config, 'fingerprint', AsyncMock(side_effect=AssertionError(forbidden))))

            async def new_form():
                menu = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'})
                assert menu['type'] == 'menu' and 'connect3' in menu['menu_options']
                form = await hass.config_entries.flow.async_configure(menu['flow_id'], {'next_step_id': 'connect3'})
                assert form['step_id'] == 'connect3'
                schema = serialize_form(form)
                assert {item['name'] for item in schema} == {
                    'host', 'auth_code', 'experimental_video', 'experimental_outputs', 'opening_code', 'advanced'}
                advanced = field(schema, 'advanced')
                assert advanced['type'] == 'expandable' and advanced['expanded'] is False
                assert {'cgi_port', 'media_port', 'installation_qr', 'certificate_sha256',
                        'media_certificate_sha256'} <= {item['name'] for item in advanced['schema']}
                assert field(schema, 'auth_code')['selector']['text']['type'] == 'password'
                return form

            def assert_confirmation(result, *, changed=False):
                assert result['step_id'] == ('connect3_tls_changed' if changed else 'connect3_tls_confirm')
                schema = serialize_form(result)
                assert field(schema, 'trust')['default'] is False
                details = field(schema, 'certificate_details')
                assert details['type'] == 'expandable' and details['expanded'] is False
                for name in ('cgi_fingerprint', 'media_fingerprint'):
                    assert field(details['schema'], name)['selector']['text']['read_only'] is True
                assert AUTH not in json.dumps(schema) and OPENING not in json.dumps(schema)

            # First-use refusal creates no entry and drops the pending secrets.
            first = await new_form()
            count = len(hass.config_entries.async_entries(DOMAIN))
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection())) as inspect:
                pending = await hass.config_entries.flow.async_configure(first['flow_id'], INPUT)
                assert_confirmation(pending)
                flow = hass.config_entries.flow._progress[pending['flow_id']]
                declined = await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': False})
                assert declined['reason'] == 'connect3_tls_declined'
                assert flow._connect3_pending is None and flow._connect3_inspection is None
                inspect.assert_awaited_once_with('192.0.2.10', 443, 8443, cgi_pin='', media_pin='')
            assert len(hass.config_entries.async_entries(DOMAIN)) == count

            # Normal system trust needs no confirmation; private pins persist.
            ca_form = await new_form()
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection('system_ca'))):
                created = await hass.config_entries.flow.async_configure(ca_form['flow_id'], INPUT)
            assert created['type'] == 'create_entry'
            entry = created['result']
            assert hass.config_entries.async_get_entry(entry.entry_id) is entry
            assert entry.data['certificate_sha256'] == CGI_PIN
            assert entry.data['media_certificate_sha256'] == MEDIA_PIN
            assert entry.data['trust_endpoint'] == {'host': '192.0.2.10', 'cgi_port': 443, 'media_port': 8443}
            assert entry.data['auth_code'] == AUTH and trust.trust_endpoint_matches(entry.data)
            retained_id, retained_uid = entry.entry_id, entry.unique_id
            entity = er.async_get(hass).async_get_or_create('sensor', DOMAIN,
                f'{entry.unique_id}_connect3_status', config_entry=entry)

            # Independent self-signed certificates require one explicit decision
            # and the exact displayed certificates are rechecked before saving.
            tofu_form = await new_form()
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[inspection(), inspection('pinned')])) as inspect:
                pending = await hass.config_entries.flow.async_configure(tofu_form['flow_id'], {**INPUT, 'host': '192.0.2.11'})
                assert_confirmation(pending)
                tofu = await hass.config_entries.flow.async_configure(pending['flow_id'], {
                    'trust': True, 'certificate_details': {'cgi_fingerprint': 'e' * 64, 'media_fingerprint': 'f' * 64}})
                assert tofu['type'] == 'create_entry'
                assert tofu['result'].data['certificate_sha256'] == CGI_PIN
                assert tofu['result'].data['media_certificate_sha256'] == MEDIA_PIN
                assert inspect.await_args.kwargs == {'cgi_pin': CGI_PIN, 'media_pin': MEDIA_PIN}

            # Bad certificate/time/network results never offer approval or save
            # the successful CGI endpoint while the media endpoint failed.
            for reason in ('certificate_expired', 'certificate_malformed', 'certificate_connection_refused'):
                failed_form = await new_form()
                before = len(hass.config_entries.async_entries(DOMAIN))
                with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection(
                        'system_ca', media_status='failed', reason=reason))):
                    failed = await hass.config_entries.flow.async_configure(failed_form['flow_id'],
                        {**INPUT, 'host': '192.0.2.12'})
                assert failed['step_id'] == 'connect3' and failed['errors']['base']
                assert len(hass.config_entries.async_entries(DOMAIN)) == before
                hass.config_entries.flow.async_abort(failed['flow_id'])

            hub = hub_module.Connect3Hub(hass, entry)
            hubs.append(hub)
            await hub.start()
            entry.runtime_data = hub
            mismatch = aiohttp.ServerFingerprintMismatch(bytes.fromhex(CGI_PIN), bytes.fromhex(NEW_CGI_PIN), '192.0.2.10', 443)
            with patch.object(hub_module, 'read_device', AsyncMock(side_effect=mismatch)) as read:
                assert (await hub.execute('access'))['reason'] == 'tls_reapproval_required'
                assert (await hub.execute('access'))['reason'] == 'tls_reapproval_required'
                read.assert_awaited_once()
            issue_id = repairs.tls_issue_id(entry.entry_id)
            registry = ir.async_get(hass)
            issue = registry.async_get_issue(DOMAIN, issue_id)
            assert issue and issue.is_fixable and issue.is_persistent and issue.severity == ir.IssueSeverity.ERROR
            assert issue.translation_placeholders == {'config_entry': entry.entry_id}
            assert entry.data['certificate_sha256'] == CGI_PIN
            exported = json.dumps(await diagnostics.async_get_config_entry_diagnostics(hass, entry))
            public_issue = json.dumps({'data': issue.data, 'placeholders': issue.translation_placeholders})
            for private in (AUTH, OPENING, CGI_PIN, MEDIA_PIN, NEW_CGI_PIN, '192.0.2.10'):
                assert private not in exported and private not in public_issue, 'TLS material exposed'

            # Real Repairs manager, factory isolated only from platform loading.
            repair_manager = RepairsFlowManager(hass)
            async def create_repair_flow(handler, *, context=None, data=None):
                assert handler == DOMAIN
                repair_issue_id = (context or {}).get('issue_id') or (data or {}).get('issue_id')
                current_issue = registry.async_get_issue(handler, repair_issue_id)
                assert current_issue and current_issue.is_fixable
                repair = await repairs.async_create_fix_flow(hass, repair_issue_id, current_issue.data)
                repair.issue_id, repair.data = repair_issue_id, current_issue.data
                return repair
            with patch.object(repair_manager, 'async_create_flow', side_effect=create_repair_flow):
                # HA 2026.10 moved the issue ID from init data into context.
                repair_init = ({'context': {'issue_id': issue_id}}
                    if hasattr(repairs.repairs_platform, 'RepairsFlowContext')
                    else {'data': {'issue_id': issue_id}})
                repair_form = await repair_manager.async_init(DOMAIN, **repair_init)
                assert repair_form['step_id'] == 'confirm'
                serialize_form(repair_form)
                before_progress = len(hass.config_entries.flow.async_progress())
                routed = await repair_manager.async_configure(repair_form['flow_id'], {})
            assert registry.async_get_issue(DOMAIN, issue_id) is not None
            if repairs._FLOW_TYPE is None:
                assert routed['reason'] == 'reconfigure_legacy' and 'next_flow' not in routed
                assert len(hass.config_entries.flow.async_progress()) == before_progress
                change_form = await hass.config_entries.flow.async_init(DOMAIN,
                    context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            else:
                assert routed['next_flow'][0] == repairs._FLOW_TYPE.CONFIG_FLOW
                change_form = hass.config_entries.flow.async_get(routed['next_flow'][1])
            assert change_form['step_id'] == 'connect3_reconfigure'
            assert AUTH not in json.dumps(serialize_form(change_form))
            original = dict(entry.data)
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection('pin_mismatch', cgi_pin=NEW_CGI_PIN))):
                changed = await hass.config_entries.flow.async_configure(change_form['flow_id'], {'host': entry.data['host']})
                assert_confirmation(changed, changed=True)
                declined = await hass.config_entries.flow.async_configure(changed['flow_id'], {'trust': False})
                assert declined['reason'] == 'connect3_tls_declined'
            assert dict(entry.data) == original and registry.async_get_issue(DOMAIN, issue_id) is not None

            # Reapproval keeps HA identities/entities and resolves the issue only
            # after current, explicitly selected replacement pins are rechecked.
            changed_form = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pin_mismatch', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN)])):
                pending = await hass.config_entries.flow.async_configure(changed_form['flow_id'], {'host': entry.data['host']})
                assert_confirmation(pending, changed=True)
                assert dict(entry.data) == original
                approved = await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': True})
            assert approved['reason'] == 'reconfigure_successful'
            assert entry.entry_id == retained_id and entry.unique_id == retained_uid
            assert er.async_get(hass).async_get(entity.entity_id) is entity
            assert entry.data['certificate_sha256'] == NEW_CGI_PIN
            assert entry.data['media_certificate_sha256'] == NEW_MEDIA_PIN
            assert entry.data['auth_code'] == AUTH and registry.async_get_issue(DOMAIN, issue_id) is None

            # Same pin on a changed endpoint still requires explicit approval.
            endpoint_form = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN)])):
                pending = await hass.config_entries.flow.async_configure(endpoint_form['flow_id'],
                    {'host': '192.0.2.13', 'advanced': {'cgi_port': 444, 'media_port': 8444}})
                assert_confirmation(pending, changed=True)
                assert entry.data['host'] == '192.0.2.10'
                await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': True})
            assert entry.entry_id == retained_id and entry.unique_id == retained_uid
            assert trust.trust_endpoint_matches(entry.data)

            # An external data edit cannot move existing approval to another
            # endpoint. Guard before I/O and require the changed trust dialog
            # even when reconfigure submits that current host unchanged.
            hass.config_entries.async_update_entry(entry, data={**entry.data, 'host': '192.0.2.14'})
            endpoint_hub = hub_module.Connect3Hub(hass, entry)
            hubs.append(endpoint_hub)
            await endpoint_hub.start()
            with patch.object(hub_module, 'read_device', AsyncMock(side_effect=AssertionError(forbidden))) as read:
                assert (await endpoint_hub.execute('access'))['reason'] == 'tls_reapproval_required'
                read.assert_not_called()
            assert registry.async_get_issue(DOMAIN, issue_id).data['reason'] == 'endpoint_changed'
            external_form = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN)])):
                pending = await hass.config_entries.flow.async_configure(external_form['flow_id'], {'host': entry.data['host']})
                assert_confirmation(pending, changed=True)
                assert entry.data['trust_endpoint']['host'] == '192.0.2.13'
                await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': True})
            assert trust.trust_endpoint_matches(entry.data)
            assert registry.async_get_issue(DOMAIN, issue_id) is None

            # Config entry private storage and the persistent issue survive a
            # native HA reload. A dismissed warning cannot restore device I/O.
            repairs.async_report_tls_issue(hass, entry.entry_id, 'certificate_changed', 'media')
            await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
            await registry._store.async_save(registry._data_to_save())
            await hass.async_block_till_done()
            for owned in hubs:
                await owned.stop()
            await hass.async_stop(force=True)

            restarted = await initialize_hass(temporary)
            restored_entry = restarted.config_entries.async_get_entry(retained_id)
            assert restored_entry and restored_entry.unique_id == retained_uid
            assert restored_entry.data['certificate_sha256'] == NEW_CGI_PIN
            assert restored_entry.data['media_certificate_sha256'] == NEW_MEDIA_PIN
            assert restored_entry.data['auth_code'] == AUTH and trust.trust_endpoint_matches(restored_entry.data)
            restored = hub_module.Connect3Hub(restarted, restored_entry)
            await restored.start()
            try:
                restored.check_tls_trust()
            except cgi.CGIError as exc:
                assert str(exc) == 'tls_reapproval_required'
            else:
                raise AssertionError('Persistent trust failure was cleared by restart')
            await integration.async_remove_entry(restarted, restored_entry)
            assert ir.async_get(restarted).async_get_issue(DOMAIN, repairs.tls_issue_id(retained_id)) is None
            await restored.stop()
            await restarted.async_stop(force=True)
        print('Connect 3 autoTLS actual HA: native schema/sections, CA/TOFU, refusal, private pins, Repairs, identity, persistence PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
