"""Synthetic SDK RFID catalogue, permission and lifecycle boundaries; no devices."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import voluptuous as vol

from load_integration import cap, load
from test_transport_lifecycle import load_source

badges = load('connect3.badges')
cgi = load('connect3.cgi')
c3 = load('connect3.hub')
r002 = load('r002.hub')
AUTH = 'SYNTHETIC_LOCAL_AUTH'
CARD_ID = '000AbC'
NAME = 'PRIVATE_SYNTHETIC_OWNER'

# Independent response shape from GetDeviceRFIDListContentResp, not a builder
# or an observed device response. The SDK envelope need not echo the command.
CATALOG = (b'{"body":{"error":0,"content":{"cards":[{"cardid":"000AbC",'
           b'"name":"PRIVATE_SYNTHETIC_OWNER","starttime":"-1","endtime":"-1",'
           b'"channels":[{"number":0,"name":"PRIVATE_CHANNEL"},{"number":2},'
           b'{"number":2},{"number":65535}]}],'
           b'"info":[{"number":1,"remain":31,"max":32}]}}}')


def response(cards=None, *, error=0, **extra):
    return json.dumps({'body': {'error': error, 'content': {
        'cards': [] if cards is None else cards, **extra}}}).encode()


class BadgeFormatTests(unittest.TestCase):
    def test_sdk_lan_request_uses_fixed_read_command_and_omits_null_content(self):
        request = badges.badge_request(AUTH)
        self.assertEqual(json.loads(request), {
            'header': {'security': 'username', 'username': 'adminapp2',
                       'password': sha256(AUTH.encode()).hexdigest(), 'passwordencode': 1},
            'body': {'command': 'get.rfid.info'}})
        self.assertNotIn(AUTH.encode(), request)
        self.assertNotIn(b'content', request)
        self.assertEqual(json.loads(badges.badge_request('a' * 64))['header']['password'], 'a' * 64)
        for invalid in ('', None, 'CONTROL\0AUTH'):
            with self.subTest(invalid=invalid), self.assertRaises(cgi.CGIError):
                badges.badge_request(invalid)

    def test_default_and_truthy_nonboolean_options_never_expose_private_details(self):
        expected = {'authentication': 'cgi_accepted', 'badge_count': 1,
                    'badge_event_correlation_verified': False}
        self.assertEqual(badges.parse_badges(CATALOG), expected)
        for option in (False, None, 0, 1, 'false', 'true', [True], {'details': True}):
            with self.subTest(option=option):
                result = badges.parse_badges(CATALOG, include_details=option)
                self.assertEqual(result, expected)
                for private in (CARD_ID, NAME, 'PRIVATE_CHANNEL', 'starttime', 'endtime'):
                    self.assertNotIn(private, json.dumps(result))

    def test_explicit_details_preserve_id_case_zeroes_and_native_channels(self):
        result = badges.parse_badges(CATALOG, include_details=True)
        self.assertEqual(result['badges'], [{'card_id': CARD_ID, 'name': NAME,
                                            'native_channels': [0, 2, 65535]}])
        self.assertFalse(result['badge_event_correlation_verified'])
        self.assertNotIn('PRIVATE_CHANNEL', json.dumps(result))
        rows = [{'cardid': value} for value in ('0001', '1', '000a', '000A')]
        result = badges.parse_badges(response(rows), include_details=True)
        self.assertEqual([row['card_id'] for row in result['badges']], ['0001', '1', '000a', '000A'])
        self.assertTrue(all(row['name'] == '' for row in result['badges']))

    def test_catalog_never_interprets_event_history_or_output_fields(self):
        row = {'cardid': CARD_ID, 'name': NAME, 'event': 63, 'state': 5,
               'alarminfo': '2', 'last_user': 'PRIVATE_INFERRED_USER'}
        result = badges.parse_badges(response([row], history=[row]), include_details=True)
        self.assertEqual(set(result), {'authentication', 'badge_count',
                                      'badge_event_correlation_verified', 'badges'})
        self.assertEqual(result['badges'], [{'card_id': CARD_ID, 'name': NAME, 'native_channels': []}])
        self.assertFalse(result['badge_event_correlation_verified'])
        with self.assertRaises(cgi.CGIError):
            badges.parse_badges(response([{'event': 63, 'state': 5, 'alarminfo': CARD_ID}]))

    def test_duplicate_assignments_rejected_even_when_labels_agree(self):
        for other_name in (NAME, 'PRIVATE_OTHER_OWNER'):
            with self.subTest(name=other_name), self.assertRaisesRegex(
                    cgi.CGIError, '^ambiguous_badge_catalog$'):
                badges.parse_badges(response([{'cardid': CARD_ID, 'name': NAME},
                                             {'cardid': CARD_ID, 'name': other_name}]))

    def test_malformed_json_duplicates_and_nonfinite_values_rejected_privately(self):
        malformed = [b'\xff', b'{', b'[]', b'null', b'{"body":[]}',
            b'{"body":{"error":true}}', b'{"body":{"error":"0"}}',
            b'{"body":{},"body":{"error":0,"content":{"cards":[]}}}',
            b'{"body":{"error":401,"error":0,"content":{"cards":[]}}}',
            b'{"body":{"error":0,"content":{"cards":[],"cards":[]}}}',
            b'{"body":{"error":0,"content":{"cards":[{"cardid":"1","cardid":"2"}]}}}',
            b'{"body":{"error":0,"content":{"cards":[]}},"extra":NaN}',
            b'{"body":{"error":0,"content":{"cards":[]}},"extra":Infinity}',
            b'{"body":{"error":0,"content":{"cards":[]}},"extra":-Infinity}',
            b'{"body":{"error":0,"command":"set.device.opendoor","content":{"cards":[]}}}']
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                    cgi.CGIError, '^invalid_badge_response$') as caught:
                badges.parse_badges(payload, include_details=True)
            self.assertNotIn('PRIVATE', str(caught.exception))

    def test_missing_catalog_is_unavailable_and_valid_empty_catalog_is_empty(self):
        for content in (None, {}, {'cards': None}, {'cards': {}}, {'info': []}):
            with self.subTest(content=content), self.assertRaisesRegex(
                    cgi.CGIError, '^badge_catalog_unavailable$'):
                badges.parse_badges(json.dumps({'body': {'error': 0, 'content': content}}).encode())
        self.assertEqual(badges.parse_badges(response())['badge_count'], 0)

    def test_native_error_codes_are_bounded_and_do_not_become_private_messages(self):
        for code, reason in ((401, 'auth_code_rejected'), (-1, 'device_rejected'),
                              (503, 'device_rejected')):
            with self.subTest(code=code), self.assertRaisesRegex(cgi.CGIError, '^' + reason + '$') as caught:
                badges.parse_badges(response(error=code, private=NAME))
            self.assertEqual(caught.exception.device_error_code, code)
        for code in (True, None, '401', 1.5, -(1 << 31) - 1, 1 << 31):
            with self.subTest(code=code), self.assertRaisesRegex(cgi.CGIError, '^invalid_badge_response$'):
                badges.parse_badges(response(error=code))

    def test_payload_complexity_and_record_limits(self):
        for payload in (b'', '', None, b' ' * (cgi.MAX_XML + 1)):
            with self.subTest(kind=type(payload)), self.assertRaisesRegex(cgi.CGIError, '^badge_response_size$'):
                badges.parse_badges(payload)
        for extra in ([0] * (cgi.MAX_NODES + 1), [[[[[[[[[[[[[[0]]]]]]]]]]]]]]):
            with self.assertRaisesRegex(cgi.CGIError, '^badge_response_complexity$'):
                badges.parse_badges(response(extra=extra))
        rows = [{'cardid': str(index)} for index in range(badges.MAX_CARDS)]
        self.assertEqual(badges.parse_badges(response(rows))['badge_count'], badges.MAX_CARDS)
        with self.assertRaisesRegex(cgi.CGIError, '^badge_catalog_limit$'):
            badges.parse_badges(response(rows + [{'cardid': 'overflow'}]))

    def test_invalid_card_labels_and_channel_shapes_rejected(self):
        invalid = [None, [], 'PRIVATE_ROW']
        invalid.extend({'cardid': value} for value in (None, 123, True, '', 'x' * 129, 'PRIVATE\0ID', 'PRIVATE\x7fID'))
        invalid.extend({'cardid': CARD_ID, 'name': value} for value in (None, 1, 'x' * 129, 'PRIVATE\nNAME'))
        invalid.extend({'cardid': CARD_ID, 'channels': value} for value in (
            None, {}, [None], [{}], [{'number': True}], [{'number': 1.5}],
            [{'number': -1}], [{'number': 65536}], [{'number': 1}] * (badges.MAX_CARD_CHANNELS + 1)))
        for row in invalid:
            with self.subTest(row=row), self.assertRaisesRegex(cgi.CGIError, '^invalid_badge_response$'):
                badges.parse_badges(response([row]))


class HAError(Exception):
    pass


registration = SimpleNamespace(async_register_platform_entity_service=Mock())
services = load_source('services', {'HomeAssistantError': HAError, 'POLICY_CONTROL': 'control',
    'DeviceVariant': cap.DeviceVariant, 'DOMAIN': 'welcomeeye_local', 'vol': vol,
    'cv': SimpleNamespace(boolean=object()), 'service': registration,
    'SupportsResponse': SimpleNamespace(ONLY='only', OPTIONAL='optional')})


class BadgeServiceTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, variant=cap.DeviceVariant.CONNECT3, **data):
        user = SimpleNamespace(is_admin=True, permissions=SimpleNamespace(check_entity=Mock(return_value=True)))
        entity = SimpleNamespace(entity_id='sensor.synthetic_protocol',
            hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user))),
            hub=SimpleNamespace(stopped=False, variant=variant, execute=AsyncMock(return_value={'badge_count': 0})))
        call = SimpleNamespace(context=SimpleNamespace(user_id='synthetic_admin'), data=data)
        return entity, call, user

    def test_registration_targets_sensor_with_details_off_and_response_required(self):
        registration.async_register_platform_entity_service.reset_mock()
        services.async_setup_services(object())
        calls = [call for call in registration.async_register_platform_entity_service.call_args_list
                 if call.args[2] == 'read_badge_catalog']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs['entity_domain'], 'sensor')
        self.assertEqual(calls[0].kwargs['supports_response'], 'only')
        self.assertIs(calls[0].kwargs['func'], services._async_read_badges)
        fields = list(calls[0].kwargs['schema'])
        self.assertEqual([str(field) for field in fields], ['include_details'])
        self.assertIs(fields[0].default(), False)

    async def test_identified_admin_and_entity_control_required_before_io(self):
        for denied in ('anonymous', 'missing_user', 'non_admin', 'no_control'):
            entity, call, user = self.fixture(include_details=True)
            if denied == 'anonymous':
                call.context.user_id = None
            elif denied == 'missing_user':
                entity.hass.auth.async_get_user.return_value = None
            elif denied == 'non_admin':
                user.is_admin = False
            else:
                user.permissions.check_entity.return_value = False
            with self.subTest(denied=denied), self.assertRaises(HAError):
                await services._async_read_badges(entity, call)
            entity.hub.execute.assert_not_awaited()

    async def test_only_running_qv_models_can_read_catalog(self):
        for variant in (cap.DeviceVariant.R001, cap.DeviceVariant.V1, cap.DeviceVariant.LEGACY_UNKNOWN, None):
            entity, call, _ = self.fixture(variant)
            with self.subTest(variant=variant), self.assertRaises(HAError):
                await services._async_read_badges(entity, call)
            entity.hub.execute.assert_not_awaited()
        entity, call, _ = self.fixture()
        entity.hub.stopped = True
        with self.assertRaises(HAError):
            await services._async_read_badges(entity, call)
        entity.hub.execute.assert_not_awaited()

    async def test_fixed_read_operation_and_explicit_details_only(self):
        for variant in (cap.DeviceVariant.CONNECT3, cap.DeviceVariant.R002):
            for detailed in (False, True):
                entity, call, user = self.fixture(variant, **({'include_details': True} if detailed else {}))
                result = await services._async_read_badges(entity, call)
                self.assertEqual(result, {'badge_count': 0})
                user.permissions.check_entity.assert_called_once_with(entity.entity_id, 'control')
                entity.hub.execute.assert_awaited_once_with('badges', include_details=detailed)

    async def test_internal_error_text_is_not_exposed(self):
        for error in (ValueError(NAME), RuntimeError(NAME)):
            entity, call, _ = self.fixture()
            entity.hub.execute.side_effect = error
            with self.assertRaises(HAError) as caught:
                await services._async_read_badges(entity, call)
            self.assertNotIn(NAME, str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)


class BadgeHubTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self, profile):
        data = {'host': '192.0.2.1', 'auth_code': AUTH, 'certificate_sha256': 'a' * 64}
        if profile == 'r002_manual':
            data['r002_connection_mode'] = 'manual'
        hub_class = c3.Connect3Hub if profile == 'connect3' else r002.R002InvestigationHub
        hub = hub_class(None, SimpleNamespace(data=data))
        await hub.start()
        self.addAsyncCleanup(hub.stop)
        return hub

    async def test_explicit_details_returned_once_but_not_saved_to_hub_or_diagnostics(self):
        for profile in ('connect3', 'r002_manual', 'r002_discovery'):
            with self.subTest(profile=profile):
                hub = await self.fixture(profile)
                original = deepcopy(hub.entry.data)
                module = r002 if profile == 'r002_discovery' else c3

                async def read(*args, **kwargs):
                    self.assertEqual(kwargs['operation'], 'badges')
                    self.assertIs(kwargs['include_details'], True)
                    kwargs['diagnostics'].update(authentication_status='accepted', request_sent_count=1)
                    return badges.parse_badges(CATALOG, include_details=True)

                with patch.object(r002, 'resolve_endpoint', AsyncMock(return_value={'cgi_port': 443})), patch.object(
                        module, 'read_device', AsyncMock(side_effect=read)) as request:
                    result = await hub.execute('badges', include_details=True)
                request.assert_awaited_once()
                self.assertEqual(result['badges'][0]['card_id'], CARD_ID)
                self.assertTrue(result['device_authenticated'])
                self.assertFalse(result['badge_event_correlation_verified'])
                self.assertEqual(hub.entry.data, original)
                self.assertIsNone(hub.live.task)
                self.assertIsNone(hub._task)
                diagnostics = json.dumps(hub.diagnostics())
                for private in (CARD_ID, NAME, AUTH, 'a' * 64, 'PRIVATE_CHANNEL'):
                    self.assertNotIn(private, diagnostics)
                self.assertEqual(hub._summary['badge_count'], 1)
                self.assertNotIn('badges', hub._summary)

    async def test_reconfiguration_during_identity_or_endpoint_lookup_blocks_send(self):
        for profile in ('connect3', 'r002_manual', 'r002_discovery'):
            for changed in ('host', 'auth_code', 'certificate_sha256', 'credential_device_uid', 'stopped'):
                with self.subTest(profile=profile, changed=changed):
                    hub = await self.fixture(profile)
                    module = r002 if profile == 'r002_discovery' else c3
                    lookup = 'resolve_endpoint' if profile == 'r002_discovery' else 'discover'
                    if profile != 'r002_discovery':
                        hub.entry.data.update(credential_source='apk_space', credential_device_uid='PRIVATE_UID')

                    async def resolve(*args, **kwargs):
                        if changed == 'stopped':
                            hub.stopped = True
                        else:
                            hub.entry.data[changed] = 'CHANGED_SYNTHETIC_VALUE'
                        return {'cgi_port': 443, 'credential_identity_status': 'matched'}

                    with patch.object(module, lookup, AsyncMock(side_effect=resolve)) as discovery, patch.object(
                            module, 'read_device', AsyncMock()) as request:
                        result = await hub.execute('badges', include_details=True)
                    discovery.assert_awaited_once()
                    request.assert_not_awaited()
                    self.assertEqual(result['reason'], 'configuration_changed')
                    self.assertFalse(result['device_authenticated'])
                    self.assertIsNone(hub._task)

    async def test_live_view_blocks_catalog_before_discovery_or_read(self):
        for profile in ('connect3', 'r002_manual', 'r002_discovery'):
            hub = await self.fixture(profile)
            hub.live.consumers.add('synthetic_viewer')
            with patch.object(c3, 'discover', AsyncMock()) as discover, patch.object(
                    r002, 'resolve_endpoint', AsyncMock()) as resolve, patch.object(
                    c3, 'read_device', AsyncMock()) as c3_read, patch.object(
                    r002, 'read_device', AsyncMock()) as r002_read:
                with self.assertRaises(RuntimeError):
                    await hub.execute('badges')
            for operation in (discover, resolve, c3_read, r002_read):
                operation.assert_not_awaited()
            hub.live.consumers.clear()


if __name__ == '__main__':
    unittest.main()
