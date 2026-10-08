"""Family isolation with synthetic entries; real HA coverage is in tools/CI."""
import ast
import asyncio
from hashlib import sha256
from ipaddress import IPv4Address
import re
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from load_integration import cap, CAP_IMPORTS, ROOT, load
from test_r002_config_flow import Base
from test_transport_lifecycle import load_source

trust_module = load('connect3.trust')


def inspected(host, cgi_port=443, media_port=8443, *, cgi_pin='', media_pin='', media_tls=True):
    """Synthetic TLS results; never inspect the documentation IP."""
    def endpoint(pin, fallback):
        return trust_module.EndpointTrust('pinned' if pin else 'system_ca', pin or fallback,
            validity_status='valid', not_valid_after='2099-01-01T00:00:00+00:00')
    return trust_module.TrustInspection(endpoint(cgi_pin, 'a' * 64),
        endpoint(media_pin, 'b' * 64) if media_tls else trust_module.EndpointTrust('not_applicable'))


async def approve(instance, result):
    if result.get('step_id') in ('connect3_tls_confirm', 'connect3_tls_changed'):
        return await instance.async_step_connect3_tls_confirm({'trust': True})
    return result


class Connect3FlowBase(Base):
    def async_show_menu(self, **kwargs):
        return {'type': 'menu', **kwargs}


def flow():
    # Form rendering itself is exercised with actual voluptuous/HA in CI.
    vol = SimpleNamespace(Schema=lambda value: value, Required=lambda key, **kw: key,
        Optional=lambda key, **kw: key, All=lambda *args: str, Coerce=lambda *args: int,
        Range=lambda **kwargs: int)
    selector = SimpleNamespace(TextSelector=lambda *args: str,
        TextSelectorConfig=lambda **kwargs: str, TextSelectorType=SimpleNamespace(PASSWORD='password'),
        SelectSelector=lambda *args: str, SelectSelectorConfig=lambda **kwargs: str,
        SelectOptionDict=lambda **kwargs: kwargs)
    namespace = dict(**CAP_IMPORTS, asyncio=asyncio, IPv4Address=IPv4Address, sha256=sha256, uuid4=uuid4,
        re=re, vol=vol, selector=selector, section=lambda schema, options: schema,
        inspect_trust=AsyncMock(side_effect=inspected), async_clear_tls_issue=Mock(),
        trust_endpoint_matches=trust_module.trust_endpoint_matches,
        encode_auth_code=load('connect3.cgi').encode_auth_code,
        CredentialImportError=load('connect3.credentials').CredentialImportError,
        parse_installation_qr=load('connect3.credentials').parse_installation_qr,
        config_entries=SimpleNamespace(ConfigFlow=Connect3FlowBase), DOMAIN='welcomeeye_local')
    module = load_source('config_flow', namespace)
    result = module.WelcomeEyeConfigFlow()
    result.hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=AssertionError('Network forbidden')))
    result.test_module = module
    return result


class ConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_opt_in_requires_its_own_code_and_preserves_secrets(self):
        instance = flow()
        result = await instance.async_step_connect3({'host': '192.0.2.1', 'confirm': True,
            'auth_code': 'CONNECTION_SECRET', 'experimental_video': True,
            'experimental_outputs': True})
        self.assertEqual(result['errors']['base'], 'connect3_opening_code_required')
        result = await instance.async_step_connect3({'host': '192.0.2.1', 'confirm': True,
            'auth_code': 'CONNECTION_SECRET', 'opening_code': 'OPENING_SECRET',
            'experimental_video': True, 'experimental_outputs': True})
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['data']['opening_code'], 'OPENING_SECRET')
        entry = SimpleNamespace(entry_id='fixture', unique_id=instance.uid, data=result['data'])
        instance._get_reconfigure_entry = lambda: entry
        form = await instance.async_step_reconfigure()
        self.assertNotIn('OPENING_SECRET', repr(form))
        self.assertNotIn('CONNECTION_SECRET', repr(form))
        await instance.async_step_reconfigure({'host': '192.0.2.1', 'opening_code': ''})
        self.assertEqual(entry.data['opening_code'], 'OPENING_SECRET')
        await instance.async_step_reconfigure({'host': '192.0.2.1', 'clear_credentials': True})
        self.assertEqual(entry.data['opening_code'], '')
        self.assertFalse(entry.data['experimental_outputs'])
        self.assertFalse(entry.data['experimental_video'])
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_qr_import_stores_only_credential_binding_and_never_redisplays_secret(self):
        instance = flow()
        qr = 'PRIVATE_AP SYNTHETIC_UID SYNTHETIC_SECRET IDS94E6SW'
        result = await instance.async_step_connect3({'host': '192.0.2.1', 'confirm': True,
                                                   'installation_qr': qr})
        data = result['data']
        self.assertEqual(data['auth_code'], 'SYNTHETIC_SECRET')
        self.assertEqual(data['credential_device_uid'], 'SYNTHETIC_UID')
        self.assertEqual(data['credential_source'], 'apk_space')
        self.assertNotIn('installation_qr', data)
        self.assertNotIn('PRIVATE_AP', repr(data))
        entry = SimpleNamespace(entry_id='fixture', unique_id=instance.uid, data=data)
        instance._get_reconfigure_entry = lambda: entry
        form = await instance.async_step_reconfigure()
        self.assertNotIn('SYNTHETIC', repr(form))
        result = await instance.async_step_reconfigure({'host': '192.0.2.1',
            'installation_qr': 'AP DIFFERENT_UID PRIVATE_NEW_SECRET IDS94E6SW'})
        self.assertEqual(result['errors']['base'], 'invalid_connect3_qr')
        self.assertNotIn('PRIVATE', repr(result))
        self.assertEqual(entry.data['auth_code'], 'SYNTHETIC_SECRET')
        await instance.async_step_reconfigure({'host': '192.0.2.1', 'clear_credentials': True})
        self.assertEqual(entry.data['credential_device_uid'], '')
        self.assertEqual(entry.data['credential_source'], '')
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_manual_replacement_removes_old_qr_binding_and_ambiguous_input_rejected(self):
        instance = flow()
        entry = SimpleNamespace(entry_id='fixture', data={'host': '192.0.2.1', 'protocol_family': cap.ProtocolFamily.CONNECT3,
            'auth_code': 'OLD', 'credential_device_uid': 'UID', 'credential_source': 'apk_space'})
        instance._get_reconfigure_entry = lambda: entry
        result = await instance.async_step_reconfigure({'host': '192.0.2.1', 'auth_code': 'NEW',
            'installation_qr': 'AP UID CODE IDS94E6SW'})
        self.assertEqual(result['errors']['base'], 'invalid_connect3_qr')
        self.assertEqual(entry.data['auth_code'], 'OLD')
        await instance.async_step_reconfigure({'host': '192.0.2.1', 'auth_code': 'NEW'})
        self.assertEqual(entry.data['auth_code'], 'NEW')
        self.assertEqual(entry.data['credential_source'], 'manual')
        self.assertEqual(entry.data['credential_device_uid'], '')

    async def test_initial_choice_unicast_inspection_only_and_manual_identity(self):
        instance = flow()
        menu = await instance.async_step_user()
        self.assertEqual(menu['menu_options'], ['legacy', 'connect3'])
        result = await instance.async_step_connect3({'host': '192.0.2.1', 'auth_code': 'SYNTHETIC'})
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['data']['protocol_family'], cap.ProtocolFamily.CONNECT3)
        self.assertTrue(instance.uid.startswith('connect3-'))
        self.assertNotIn(sha256(b'192.0.2.1').hexdigest()[:16], instance.uid)
        self.assertEqual(result['data']['auth_code'], 'SYNTHETIC')
        instance.test_module.inspect_trust.assert_awaited_once_with('192.0.2.1', 443, 8443,
                                                                  cgi_pin='', media_pin='', media_tls=True)
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_confirmation_invalid_address_and_duplicates(self):
        instance = flow()
        for address in ('bad', '224.0.0.1', '0.0.0.0', '255.255.255.255'):
            result = await instance.async_step_connect3({'host': address, 'confirm': True})
            self.assertEqual(result['errors']['base'], 'invalid_connect3_config')
        result = await instance.async_step_connect3({'host': '192.0.2.1'})
        self.assertEqual(result['errors']['base'], 'connect3_local_password_required')
        instance._async_current_entries = lambda: [SimpleNamespace(data={'host': '192.0.2.1'})]
        result = await instance.async_step_connect3({'host': '192.0.2.1', 'confirm': True})
        self.assertEqual(result['reason'], 'already_configured')

    async def test_reconfigure_identity_secrets_preserved_clear_explicit(self):
        instance = flow()
        entry = SimpleNamespace(entry_id='fixture', unique_id='connect3-original', data={
            'host': '192.0.2.1', 'protocol_family': cap.ProtocolFamily.CONNECT3,
            'auth_code': 'SYNTHETIC_SECRET', 'certificate_sha256': 'a' * 64, 'cgi_port': 8443})
        instance._get_reconfigure_entry = lambda: entry
        form = await instance.async_step_reconfigure()
        self.assertEqual(form['step_id'], 'connect3_reconfigure')
        self.assertNotIn('SYNTHETIC_SECRET', repr(form))
        await approve(instance, await instance.async_step_reconfigure({'host': '192.0.2.2', 'auth_code': ''}))
        self.assertEqual(entry.unique_id, 'connect3-original')
        self.assertEqual(entry.data['auth_code'], 'SYNTHETIC_SECRET')
        self.assertEqual(entry.data['cgi_port'], 8443)
        await instance.async_step_reconfigure({'host': '192.0.2.2', 'clear_credentials': True})
        self.assertEqual(entry.data['auth_code'], '')
        self.assertEqual(entry.data['certificate_sha256'], '')
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_reauth_connect3_r002_unknown_never_legacy(self):
        for family in (cap.ProtocolFamily.CONNECT3, cap.ProtocolFamily.R002, 'future_unknown', ''):
            instance = flow()
            entry = SimpleNamespace(data={'host': '192.0.2.1', 'protocol_family': family})
            instance._get_reauth_entry = lambda: entry
            result = await instance.async_step_reauth_confirm({'password': 'SYNTHETIC'})
            self.assertEqual(result['type'], 'abort')
            instance.hass.async_add_executor_job.assert_not_called()

    def test_unknown_and_conflicting_persisted_families_fail_closed(self):
        for data in ({'protocol_family': None}, {'protocol_family': ''}, {'protocol_family': 'future'},
                     {'device_variant': 'connect3'}, {'device_variant': 'future'},
                     {'protocol_family': cap.ProtocolFamily.R002, 'device_variant': cap.DeviceVariant.CONNECT3},
                     {'protocol_family': cap.ProtocolFamily.CONNECT3, 'device_variant': cap.DeviceVariant.V1}):
            with self.assertRaises(ValueError):
                cap.variant_for(data)
        with self.assertRaises(ValueError):
            cap.family_for('future')
        self.assertEqual(cap.variant_for({}), cap.DeviceVariant.LEGACY_UNKNOWN)

    async def test_only_diagnostic_entity_no_camera_or_controls(self):
        from test_capabilities import CapabilityTests
        result = await CapabilityTests().entities(cap.DeviceVariant.CONNECT3)
        self.assertEqual(result, ['WelcomeEyeConnect3Status'])
        self.assertEqual([k for k, v in cap.MATRIX[cap.DeviceVariant.CONNECT3].as_dict().items() if v], ['connect3_read'])

    def test_no_legacy_or_media_transport_imported_by_connect3(self):
        for path in (ROOT / 'connect3').glob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level != 1:
                    self.assertNotIn(node.module, ('client', 'protected', 'control', 'v1_control', 'media', 'rtc', 'ring'))
