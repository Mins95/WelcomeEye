"""Independent QV XML fixtures, voluntary-read policy and local HTTPS only."""
import asyncio
import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

import aiohttp

from load_integration import cap, load
import test_badge_catalog as badge_tests
from test_transport_lifecycle import load_source
import test_connect3_https as https

cgi = load('connect3.cgi')
device_info = load('connect3.device_info')
c3 = load('connect3.hub')
r002 = load('r002.hub')
AUTH = 'SYNTHETIC_DEVICE_INFO_AUTH'
HAError, registration, services = badge_tests.HAError, badge_tests.registration, badge_tests.services
PRIVATE = 'PRIVATE_SYNTHETIC_VALUE'
# Two native header bytes, then 19 bitmap bytes. Bits 0,1,2,5,8,9,10,19,21,116.
ABILITY = base64.b64encode(bytes.fromhex('101327072800000000000000000000001000000000')).decode()
CARD = ('<formatting>false</formatting><totalsum>32768</totalsum><freesum>8192</freesum>'
        '<datalist><data><diskid>0</diskid><exist>true</exist><status>normal</status>'
        '<total>32768</total><free>8192</free></data></datalist>')


def xml(content):
    return ('<envelope><body><error>0</error><content>' + content +
            '</content></body></envelope>').encode()


STATUS = xml('<info><model>QV synthetic</model><version>1.2.3</version>'
             '<releasedate>2026-01-01</releasedate><mac>' + PRIVATE + '</mac></info>'
             '<devability>' + ABILITY + '</devability><tfcard>' + CARD + '</tfcard>'
             '<uid>' + PRIVATE + '</uid><key>' + PRIVATE + '</key>'
             '<network><address>' + PRIVATE + '</address></network>'
             '<wifiinfo><ssid>' + PRIVATE + '</ssid></wifiinfo>')
NO_CARD = xml('<info><version>1.2.3</version></info><devability>' + ABILITY + '</devability>')


class DeviceInfoFormatTests(unittest.TestCase):
    def test_independent_wire_fixture_and_private_fields_are_excluded(self):
        info = device_info.parse_device_status(STATUS)
        self.assertEqual(info['model'], 'QV synthetic')
        self.assertEqual(info['firmware'], '1.2.3')
        self.assertEqual(info['firmware_release_date'], '2026-01-01')
        self.assertFalse(info['reported_capabilities_verified'])
        self.assertEqual(info['capabilities_source'], 'device_reported')
        self.assertEqual(info['reported_capabilities'], {
            'video_input': True, 'audio_output': True, 'audio_input': True, 'sd_card': True,
            'motion_detection': True, 'picture': True, 'recording': True,
            'record_schedule': False, 'multi_channel': True, 'call': True,
            'record_download': False, 'rfid_management': True})
        card = info['sd_card']
        self.assertEqual((card['state'], card['total_native'], card['free_native'], card['used_percent']),
                         ('normal', 32768, 8192, 75.0))
        self.assertEqual(card['capacity_unit'], 'unknown')
        self.assertEqual(card, device_info.parse_tfcard_info(xml(CARD)))
        for private in (PRIVATE, ABILITY, 'mac', 'ssid', 'network'):
            self.assertNotIn(private, json.dumps(info))

    def test_optional_unknown_fields_and_short_bitmap_do_not_invent_support(self):
        info = device_info.parse_device_status(xml(''))
        self.assertIsNone(info['firmware'])
        self.assertIsNone(info['sd_card'])
        self.assertEqual(info['reported_capabilities'], {})
        short = base64.b64encode(bytes.fromhex('1003000000')).decode()
        caps = device_info.reported_capabilities(short)
        self.assertIs(caps['sd_card'], False)
        self.assertNotIn('rfid_management', caps)
        card = device_info.parse_tfcard_info(xml('<totalsum>0</totalsum><freesum>0</freesum>'))
        self.assertEqual(card['state'], 'unknown')
        self.assertIsNone(card['used_percent'])

    def test_native_states_formatting_and_unknown_remote_text(self):
        for state in ('nodisk', 'normal', 'notformatted', 'error', 'sleep', PRIVATE):
            card = device_info.parse_tfcard_info(xml(CARD.replace('normal', state)))
            self.assertEqual(card['state'], state if state != PRIVATE else 'unknown')
            self.assertNotIn(PRIVATE, json.dumps(card))
        formatting = device_info.parse_tfcard_info(xml(CARD.replace('<formatting>false', '<formatting>true')))
        self.assertEqual(formatting['state'], 'formatting')
        self.assertEqual(formatting['disks'], [])
        self.assertIsNone(formatting['used_percent'])

    def test_duplicate_malformed_and_capacity_bounds_fail_without_remote_text(self):
        invalid = [
            '<info/><info/>', '<info><version>one</version><version>two</version></info>',
            '<info><model><private/></model></info>', '<info><version>' + 'x' * 129 + '</version></info>',
            '<devability>!!!!</devability>', '<devability>' + base64.b64encode(b'\0' * 22).decode() + '</devability>',
            '<tfcard><totalsum>-1</totalsum></tfcard>',
            '<tfcard><totalsum>2147483648</totalsum></tfcard>',
            '<tfcard><totalsum>1</totalsum><freesum>2</freesum></tfcard>',
            '<tfcard><formatting>1</formatting></tfcard>',
            '<tfcard><datalist><data><diskid>0</diskid></data><data><diskid>0</diskid></data></datalist></tfcard>',
            '<tfcard><datalist>' + '<data/>' * 17 + '</datalist></tfcard>',
        ]
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(cgi.CGIError) as caught:
                device_info.parse_device_status(xml(content))
            self.assertNotIn(PRIVATE, str(caught.exception))

    def test_only_exact_read_commands_and_no_writable_fields(self):
        for command in ('get.device.status', 'get.tfcard.info'):
            root = ET.fromstring(cgi.envelope(command, AUTH))
            self.assertEqual(root.findtext('body/command'), command)
            self.assertEqual(len(root.find('body/content')), 0)
            with self.assertRaises(cgi.CGIError):
                cgi.envelope(command, AUTH, {'formatting': 'true'})
        for forbidden in ('set.tfcard.format', 'g.sys.ability', 'get.audio.outvolume', 'get.call.time'):
            with self.assertRaises(cgi.CGIError):
                cgi.envelope(forbidden, AUTH)


class DeviceInfoCGITests(unittest.IsolatedAsyncioTestCase):
    async def test_at_most_two_reads_and_fallback_only_on_explicit_sd_support(self):
        for status, expected in ((STATUS, ['get.device.status']),
                (NO_CARD, ['get.device.status', 'get.tfcard.info']),
                (xml('<info><version>1</version></info>'), ['get.device.status'])):
            commands = []

            async def post(session, url, ssl, request, observation):
                commands.append(ET.fromstring(request).findtext('body/command'))
                return status if len(commands) == 1 else xml(CARD)

            with patch.object(cgi, '_post', side_effect=post):
                result = await cgi.read_device('192.0.2.1', AUTH, operation='device_info')
            self.assertEqual(commands, expected)
            self.assertEqual(result['authentication'], 'cgi_accepted')

    async def test_request_guard_can_prevent_initial_and_fallback_request(self):
        for before_second in (False, True):
            guard = Mock(side_effect=[None, cgi.CGIError('configuration_changed')]
                         if before_second else cgi.CGIError('configuration_changed'))
            with patch.object(cgi, '_post', new=AsyncMock(return_value=NO_CARD)) as post:
                with self.assertRaisesRegex(cgi.CGIError, '^configuration_changed$'):
                    await cgi.read_device('192.0.2.1', AUTH, operation='device_info', _request_guard=guard)
            self.assertEqual(post.await_count, int(before_second))


class DeviceInfoServiceTests(unittest.IsolatedAsyncioTestCase):
    fixture = badge_tests.BadgeServiceTests.fixture

    def test_registration_empty_schema_sensor_and_response_required(self):
        registration.async_register_platform_entity_service.reset_mock()
        services.async_setup_services(object())
        calls = [call for call in registration.async_register_platform_entity_service.call_args_list
                 if call.args[2] == 'read_device_info']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs['schema'], {})
        self.assertEqual(calls[0].kwargs['entity_domain'], 'sensor')
        self.assertEqual(calls[0].kwargs['supports_response'], 'only')
        self.assertIs(calls[0].kwargs['func'], services._async_read_device_info)

    async def test_admin_control_model_and_stopped_guards_before_io(self):
        for denial in ('anonymous', 'missing', 'non_admin', 'no_control', 'stopped', 'r001', 'v1'):
            entity, call, user = self.fixture()
            if denial == 'anonymous': call.context.user_id = None
            if denial == 'missing': entity.hass.auth.async_get_user.return_value = None
            if denial == 'non_admin': user.is_admin = False
            if denial == 'no_control': user.permissions.check_entity.return_value = False
            if denial == 'stopped': entity.hub.stopped = True
            if denial == 'r001': entity.hub.variant = cap.DeviceVariant.R001
            if denial == 'v1': entity.hub.variant = cap.DeviceVariant.V1
            with self.subTest(denial=denial), self.assertRaises(HAError):
                await services._async_read_device_info(entity, call)
            entity.hub.execute.assert_not_awaited()
        for variant in (cap.DeviceVariant.CONNECT3, cap.DeviceVariant.R002):
            entity, call, _ = self.fixture(variant)
            await services._async_read_device_info(entity, call)
            entity.hub.execute.assert_awaited_once_with('device_info')


class DeviceInfoHubTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self, profile):
        data = {'host': '192.0.2.1', 'auth_code': AUTH, 'certificate_sha256': 'a' * 64}
        if profile == 'r002_manual': data['r002_connection_mode'] = 'manual'
        cls = c3.Connect3Hub if profile == 'connect3' else r002.R002InvestigationHub
        hub = cls(None, SimpleNamespace(data=data))
        await hub.start()
        self.addAsyncCleanup(hub.stop)
        return hub

    async def test_no_startup_io_snapshot_is_safe_copied_and_invalidated_on_reconfigure(self):
        for profile in ('connect3', 'r002_manual', 'r002_discovery'):
            module = r002 if profile == 'r002_discovery' else c3
            received = {'authentication': 'cgi_accepted', 'device_info': device_info.parse_device_status(STATUS)}
            with (patch.object(module, 'read_device', new=AsyncMock(return_value=received)) as read,
                    patch.object(r002, 'resolve_endpoint', new=AsyncMock(return_value={'cgi_port': 443}))):
                hub = await self.fixture(profile)
                self.assertIsNone(hub.device_info_snapshot)
                read.assert_not_awaited()
                capabilities = hub.capabilities
                result = await hub.execute('device_info')
                self.assertEqual(result['status'], 'ok')
                self.assertIs(hub.capabilities, capabilities)
                self.assertEqual(hub.device_info_snapshot['firmware'], '1.2.3')
                snapshot = hub.device_info_snapshot
                snapshot['reported_capabilities']['sd_card'] = False
                self.assertTrue(hub.device_info_snapshot['reported_capabilities']['sd_card'])
                self.assertNotIn(PRIVATE, json.dumps(hub.diagnostics()))
                self.assertNotIn(AUTH, json.dumps(hub.diagnostics()))
                self.assertNotIn('device_info', hub._summary)
                hub.entry.data = {**hub.entry.data, 'auth_code': 'changed'}
                self.assertIsNone(hub.device_info_snapshot)

    async def test_reconfiguration_before_or_during_read_never_saves_stale_snapshot(self):
        for profile in ('connect3', 'r002_manual', 'r002_discovery'):
            for phase in ('discovery', 'read'):
                hub = await self.fixture(profile)
                hub.entry.data.update(credential_source='apk_json', credential_device_uid='SYNTHETIC_UID')
                module = r002 if profile == 'r002_discovery' else c3

                async def discover(*args, **kwargs):
                    if phase == 'discovery': hub.entry.data = {**hub.entry.data, 'auth_code': 'changed'}
                    return {'cgi_port': 443, 'credential_identity_status': 'matched'}

                async def read(*args, **kwargs):
                    kwargs['_request_guard']()
                    if phase == 'read': hub.entry.data = {**hub.entry.data, 'auth_code': 'changed'}
                    kwargs['diagnostics'].update(authentication_status='accepted')
                    return {'authentication': 'cgi_accepted', 'device_info': device_info.parse_device_status(STATUS)}

                with (patch.object(module, 'read_device', side_effect=read) as posted,
                        patch.object(c3, 'discover', side_effect=discover),
                        patch.object(r002, 'resolve_endpoint', side_effect=discover)):
                    result = await hub.execute('device_info')
                self.assertEqual(result['reason'], 'configuration_changed')
                self.assertIsNone(hub.device_info_snapshot)
                self.assertNotIn('device_info', result)
                self.assertFalse(result['device_authenticated'])
                self.assertEqual(posted.await_count, int(phase == 'read'))

    async def test_cancelled_read_even_if_connector_swallows_cancel_does_not_store(self):
        for profile in ('connect3', 'r002_manual', 'r002_discovery'):
            hub = await self.fixture(profile)
            module = r002 if profile == 'r002_discovery' else c3
            entered = asyncio.Event()

            async def read(*args, **kwargs):
                entered.set()
                try: await asyncio.Event().wait()
                except asyncio.CancelledError: pass
                return {'device_info': device_info.parse_device_status(STATUS)}

            with (patch.object(module, 'read_device', side_effect=read),
                    patch.object(r002, 'resolve_endpoint', new=AsyncMock(return_value={'cgi_port': 443}))):
                task = asyncio.create_task(hub.execute('device_info'))
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
            self.assertIsNone(hub.device_info_snapshot)
            self.assertIsNone(hub._task)

    async def test_total_deadline_includes_identity_or_endpoint_discovery(self):
        original_timeout = asyncio.timeout
        for profile in ('connect3', 'r002_manual', 'r002_discovery'):
            hub = await self.fixture(profile)
            hub.entry.data.update(credential_source='apk_json', credential_device_uid='SYNTHETIC_UID')
            async def stalled(*args, **kwargs): await asyncio.Event().wait()
            with (patch.object(asyncio, 'timeout', side_effect=lambda seconds: original_timeout(0.01)),
                    patch.object(c3, 'discover', side_effect=stalled),
                    patch.object(r002, 'resolve_endpoint', side_effect=stalled),
                    patch.object(c3, 'read_device', new=AsyncMock()) as read):
                result = await hub.execute('device_info')
            self.assertEqual(result['last_error_type'], 'TimeoutError')
            self.assertEqual(result['status'], 'failed')
            self.assertIsNone(hub.device_info_snapshot)
            read.assert_not_awaited()


class DeviceInfoHTTPSTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the loopback fixture without inheriting/rerunning unrelated tests.
    setUpClass = classmethod(https.HTTPSTests.setUpClass.__func__)
    tearDownClass = classmethod(https.HTTPSTests.tearDownClass.__func__)
    asyncSetUp = https.HTTPSTests.asyncSetUp
    asyncTearDown = https.HTTPSTests.asyncTearDown
    _serve = https.HTTPSTests._serve
    read = https.HTTPSTests.read

    @property
    def response_body(self):
        return self.responses[min(max(len(self.requests) - 1, 0), len(self.responses) - 1)]

    @response_body.setter
    def response_body(self, value):
        self.responses = [value]

    async def test_pinned_https_reads_exact_commands_and_safe_result(self):
        self.responses = [NO_CARD, xml(CARD)]
        result = await self.read(operation='device_info')
        self.assertEqual(result['device_info']['sd_card']['used_percent'], 75.0)
        self.assertEqual(len(self.requests), 2)
        for request, expected in zip(self.requests, ('get.device.status', 'get.tfcard.info')):
            method, path, version, headers, body = request
            self.assertEqual((method, path), ('POST', '/tdkcgi'))
            root = ET.fromstring(body)
            self.assertEqual(root.findtext('body/command'), expected)
            self.assertEqual(len(root.find('body/content')), 0)
            self.assertEqual(root.findtext('header/username'), 'adminapp2')
        self.assertNotIn(PRIVATE, json.dumps(result))

    async def test_untrusted_tls_sends_no_device_info_request(self):
        with self.assertRaises(aiohttp.ClientError):
            await self.read(operation='device_info', certificate_sha256='')
        self.assertEqual(self.requests, [])

    async def test_cancelled_or_timed_out_read_closes_connection_without_fallback(self):
        self.wait_for_client_close = True
        task = asyncio.create_task(self.read(operation='device_info'))
        await self.request_received.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(len(self.requests), 1)
        with patch.object(cgi, 'TIMEOUT', 0.2):
            with self.assertRaises(TimeoutError): await self.read(operation='device_info')
        # The deadline may also expire during TLS on a slow runner. In either
        # case there is at most one request, never a fallback after timeout.
        self.assertLessEqual(len(self.requests), 2)


class SensorBase:
    _attr_should_poll = False

    def __init__(self, hub, key):
        self.hub = hub
        self._attr_unique_id = f'{hub.entry.unique_id}_{key}'


class HASensor:
    pass


sensors = load_source('sensor', {'WelcomeEyeEntity': SensorBase, 'SensorEntity': HASensor,
    'SensorDeviceClass': SimpleNamespace(ENUM='enum'),
    'EntityCategory': SimpleNamespace(DIAGNOSTIC='diagnostic'),
    'HomeAssistantError': HAError, 'supports_cloud_status': lambda hub: False})


class DeviceInfoSensorTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_qv_profiles_add_two_sensors_unknown_before_explicit_read(self):
        for variant in cap.DeviceVariant:
            hub = SimpleNamespace(variant=variant, capabilities=cap.MATRIX[variant],
                entry=SimpleNamespace(unique_id='stable-id'), stopped=False, device_info_snapshot=None)
            entities = []
            await sensors.async_setup_entry(None, SimpleNamespace(runtime_data=hub), entities.extend)
            info_sensors = [entity for entity in entities if isinstance(entity, sensors.WelcomeEyeDeviceInfoSensor)]
            self.assertEqual(len(info_sensors), 2 if variant in (cap.DeviceVariant.CONNECT3, cap.DeviceVariant.R002) else 0)
            for entity in info_sensors:
                self.assertTrue(entity.available)
                self.assertFalse(entity._attr_should_poll)
                self.assertIsNone(entity.native_value)
                self.assertEqual(entity.extra_state_attributes, {})
                self.assertEqual(entity._attr_unique_id, 'stable-id_' + entity.key)

    def test_sensor_values_are_last_reported_firmware_and_unitless_sd_usage(self):
        info = {**device_info.parse_device_status(STATUS), 'read_at': '2026-01-01T00:00:00+00:00'}
        hub = SimpleNamespace(entry=SimpleNamespace(unique_id='stable-id'), stopped=False, device_info_snapshot=info)
        firmware = sensors.WelcomeEyeDeviceInfoSensor(hub, 'reported_firmware')
        card = sensors.WelcomeEyeDeviceInfoSensor(hub, 'sd_card_status')
        self.assertEqual(firmware.native_value, '1.2.3')
        self.assertFalse(firmware.extra_state_attributes['reported_capabilities_verified'])
        self.assertEqual(card.native_value, 'normal')
        self.assertEqual(card.extra_state_attributes['used_percent'], 75.0)
        self.assertEqual(card.extra_state_attributes['capacity_unit'], 'unknown')
        self.assertEqual(card._attr_options, list(device_info.SD_STATES))
        self.assertNotIn(PRIVATE, json.dumps(card.extra_state_attributes))
