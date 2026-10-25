"""Manual R002 setup and media use synthetic peers, never a real intercom."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import cap, load
from test_connect3_config import flow
from test_connect3_tls_flow import result as inspected_result
import test_r002_qv_config as legacy_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from qv_runtime_support import PASSWORD, OPENING_CODE, STREAM_KEY, SyntheticQVPeer

hubs = load('r002.hub')
live = load('connect3.live')
session = load('connect3.session')
talk = load('connect3.talk')
cgi = load('connect3.cgi')
cloud = load('qv_cloud_protocol')


def manual_data(**updates):
    return {'host': '192.0.2.10', 'protocol_family': cap.ProtocolFamily.R002,
        'device_variant': cap.DeviceVariant.R002, 'r002_connection_mode': 'manual',
        'auth_code': PASSWORD, 'opening_code': OPENING_CODE, 'experimental_video': True,
        'experimental_outputs': True, 'experimental_tcp_controls': True,
        'media_transport': 'connect3_tcp', 'media_tcp_approved': True,
        'certificate_sha256': 'a' * 64, 'cgi_port': 443, 'media_port': 8443,
        'trust_endpoint': {'host': '192.0.2.10', 'cgi_port': 443, 'media_port': 34567,
                           'media_transport': 'connect3_tcp'},
        'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}, **updates}


class ManualConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_manual_tls_entry_retains_r002_family_and_cloud_profile(self):
        instance = flow()
        menu = await instance.async_step_user()
        self.assertIn('r002', menu['menu_options'])
        form = await instance.async_step_r002()
        self.assertEqual(form['step_id'], 'r002')
        self.assertNotIn('installation_qr', form['data_schema']['advanced'])
        created = await instance.async_step_r002({'host': '192.0.2.10', 'auth_code': PASSWORD,
                                                  'media_transport': 'tls'})
        data = created['data']
        self.assertEqual(data['protocol_family'], cap.ProtocolFamily.R002)
        self.assertEqual(data['device_variant'], cap.DeviceVariant.R002)
        self.assertEqual(data['r002_connection_mode'], 'manual')
        self.assertTrue(instance.uid.startswith('r002-'))
        self.assertEqual(cloud.profile_for(cap.variant_for(data)), cloud.R002_PROFILE)
        self.assertEqual(data['certificate_sha256'], 'a' * 64)
        self.assertEqual(data['media_certificate_sha256'], 'b' * 64)
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_tcp_requires_explicit_approval_and_decline_creates_nothing(self):
        instance = flow()
        result = await instance.async_step_r002({'host': '192.0.2.10', 'auth_code': PASSWORD,
                                                 'media_transport': 'connect3_tcp'})
        self.assertEqual(result['step_id'], 'connect3_tcp_confirm')
        self.assertNotIn(PASSWORD, repr(result))
        declined = await instance.async_step_connect3_tls_confirm({'trust': False})
        self.assertEqual(declined['reason'], 'connect3_tls_declined')
        self.assertIsNone(instance._connect3_pending)

    async def test_tcp_approval_persists_only_approved_endpoint_and_controls(self):
        instance = flow()
        await instance.async_step_r002({'host': '192.0.2.10', 'auth_code': PASSWORD,
                                       'media_transport': 'connect3_tcp'})
        result = await instance.async_step_connect3_tcp_confirm({'trust': True})
        self.assertEqual(result['data']['media_transport'], 'connect3_tcp')
        self.assertTrue(result['data']['media_tcp_approved'])
        self.assertTrue(result['data']['experimental_tcp_controls'])
        self.assertEqual(result['data']['trust_endpoint']['media_port'], 34567)
        self.assertEqual(result['data']['protocol_family'], cap.ProtocolFamily.R002)

    async def test_old_entry_changes_mode_only_after_approval_preserving_identity(self):
        instance, entry = legacy_config.R002ConfigTests().configured(auth_code=PASSWORD, opening_code=OPENING_CODE,
            certificate_sha256='a' * 64, experimental_video=True, experimental_outputs=True,
            qv_cloud_enabled=True, qv_cloud_account={'device': {'uid': 'SYNTHETIC_UID'}})
        before = deepcopy(entry.data)
        result = await instance.async_step_reconfigure({'r002_connection_mode': 'manual'})
        self.assertEqual(result['step_id'], 'connect3_tcp_tls_confirm')
        self.assertEqual(entry.data, before)
        result = await instance.async_step_connect3_tcp_confirm({'trust': True})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertEqual((entry.entry_id, entry.unique_id, entry.title),
                         ('existing-r002', 'r002-original', 'My R002'))
        for key in ('auth_code', 'opening_code', 'certificate_sha256', 'fingerprint',
                    'protocol_family', 'device_variant', 'qv_cloud_account', 'qv_cloud_enabled'):
            self.assertEqual(entry.data[key], before[key])
        self.assertEqual(entry.data['r002_connection_mode'], 'manual')
        self.assertTrue(entry.data['experimental_tcp_controls'])
        form = await instance.async_step_reconfigure()
        self.assertEqual(form['step_id'], 'r002_connection')
        self.assertNotIn('legacy_alarm_tcp_approved', form['data_schema']['advanced'])

    async def test_unknown_certificate_and_changed_pin_require_new_approval(self):
        instance = flow()
        instance.test_module.inspect_trust = AsyncMock(return_value=inspected_result(status='candidate'))
        result = await instance.async_step_r002({'host': '192.0.2.10', 'auth_code': PASSWORD})
        self.assertEqual(result['step_id'], 'connect3_tls_confirm')
        self.assertIsNotNone(instance._connect3_pending)

    async def test_connection_ip_change_is_not_saved_before_reapproval(self):
        instance, entry = legacy_config.R002ConfigTests().configured(**{key: value for key, value in manual_data().items()
            if key not in ('protocol_family', 'device_variant')})
        before = deepcopy(entry.data)
        result = await instance.async_step_reconfigure({'host': '192.0.2.11'})
        self.assertEqual(result['step_id'], 'connect3_tcp_tls_confirm')
        self.assertEqual(entry.data, before)

    async def test_r002_does_not_import_a_connect3_qr_or_leak_flow_family(self):
        instance = flow()
        result = await instance.async_step_r002({'host': '192.0.2.10',
            'installation_qr': 'SYNTHETIC_PRIVATE_QR'})
        self.assertEqual(result['errors']['base'], 'invalid_connect3_qr')
        self.assertNotIn('SYNTHETIC_PRIVATE_QR', repr(result))
        result = await instance.async_step_connect3({'host': '192.0.2.11', 'auth_code': PASSWORD})
        self.assertEqual(result['data']['device_variant'], cap.DeviceVariant.CONNECT3)
        self.assertNotIn('r002_connection_mode', result['data'])


class ManualMediaTests(unittest.IsolatedAsyncioTestCase):
    def hub(self, **updates):
        return hubs.R002InvestigationHub(None, SimpleNamespace(data=manual_data(**updates)))

    async def test_access_and_certificate_use_configured_ports_without_discovery(self):
        hub = self.hub(cgi_port=9443, trust_endpoint={'host': '192.0.2.10',
            'cgi_port': 9443, 'media_port': 34567, 'media_transport': 'connect3_tcp'})
        await hub.start()

        async def read(host, auth, **kwargs):
            self.assertEqual((host, auth, kwargs['port']), ('192.0.2.10', PASSWORD, 9443))
            self.assertEqual(kwargs['certificate_sha256'], 'a' * 64)
            kwargs['diagnostics'].update(authentication_status='accepted', tls_verified=True)
            return {'streamkey_received': True}

        try:
            c3 = load('connect3.hub')
            with patch.object(hubs, 'resolve_endpoint', AsyncMock(side_effect=AssertionError('No UDP'))), \
                    patch.object(c3, 'read_device', AsyncMock(side_effect=read)) as access, \
                    patch.object(c3, 'inspect_certificate', AsyncMock(return_value={'status': 'observed'})) as inspect:
                self.assertEqual((await hub.execute('access'))['status'], 'ok')
                await hub.execute('certificate')
                self.assertEqual(access.await_count, 1)
                inspect.assert_awaited_once_with('192.0.2.10', port=9443, include_details=False)
            self.assertEqual(hub.diagnostics()['connection_mode'], 'manual')
            self.assertEqual(hub.variant, cap.DeviceVariant.R002)
        finally:
            await hub.stop()

    async def test_changed_endpoint_blocks_credentials_even_after_hub_recreation(self):
        for _ in range(2):
            hub = self.hub(host='192.0.2.11')
            await hub.start()
            try:
                with patch.object(live, 'read_stream_material', AsyncMock()) as read:
                    with self.assertRaises(cgi.CGIError):
                        await hub.acquire('viewer')
                    read.assert_not_awaited()
                self.assertEqual(hub._tls_blocked_reason, 'endpoint_changed')
            finally:
                await hub.stop()

    async def test_tcp_without_consent_and_tls_failure_never_fall_back(self):
        hub = self.hub(media_tcp_approved=False)
        with self.assertRaises(cgi.CGIError):
            await hub.prepare_media_endpoint({})
        self.assertFalse(self.hub(experimental_tcp_controls=False).capabilities.talkback)
        self.assertFalse(self.hub(experimental_tcp_controls=False).capabilities.strike)
        hub = self.hub(media_transport='tls', trust_endpoint={'host': '192.0.2.10',
            'cgi_port': 443, 'media_port': 8443, 'media_transport': 'tls'},
            tls_certificate_expires={'cgi': '2099-01-01T00:00:00+00:00',
                                     'media': '2099-01-01T00:00:00+00:00'})
        obs = {}
        self.assertIsNone(await hub.prepare_media_endpoint(obs))
        self.assertEqual(obs['media_transport_selected'], 'tls')
        self.assertEqual(obs['media_port_selected'], 8443)
        self.assertEqual(obs['discovery_request_count'], 0)

    async def test_qr_identity_binding_still_blocks_credentials_when_unmatched(self):
        hub = self.hub(credential_source='apk_space', credential_device_uid='PRIVATE_BOUND_UID')
        await hub.start()
        try:
            with patch.object(live, 'discover', AsyncMock(return_value={
                    'credential_identity_status': 'not_observed'})) as discover, \
                    patch.object(live, 'read_stream_material', AsyncMock()) as read:
                with self.assertRaises(RuntimeError):
                    await hub.acquire('viewer')
                discover.assert_awaited_once_with('192.0.2.10', expected_uid='PRIVATE_BOUND_UID')
                read.assert_not_awaited()
        finally:
            await hub.stop()

    async def test_rejected_local_password_never_opens_a_media_connection(self):
        hub = self.hub()
        await hub.start()
        try:
            with patch.object(live, 'read_stream_material', AsyncMock(side_effect=cgi.CGIError(
                    'auth_code_rejected'))) as read, patch.object(live, 'QVSession', Mock()) as media:
                with self.assertRaises(RuntimeError):
                    await hub.acquire('viewer')
                self.assertEqual(read.await_count, 1)
                media.assert_not_called()
                self.assertEqual(hub.live.observation['last_error_reason'], 'auth_code_rejected')
        finally:
            await hub.stop()

    async def test_tls_media_failure_never_retries_tcp_or_discovers_an_endpoint(self):
        hub = self.hub(media_transport='tls', certificate_sha256='a' * 64,
            media_certificate_sha256='b' * 64, trust_endpoint={'host': '192.0.2.10',
                'cgi_port': 443, 'media_port': 8443, 'media_transport': 'tls'},
            tls_certificate_expires={'cgi': '2099-01-01T00:00:00+00:00',
                                     'media': '2099-01-01T00:00:00+00:00'})

        async def material(*args, diagnostics, **kwargs):
            diagnostics.update(tls_verified=True, tls_policy='certificate_pin')
            return cgi.StreamMaterial(STREAM_KEY)

        await hub.start()
        try:
            with patch.object(live, 'read_stream_material', AsyncMock(side_effect=material)), \
                    patch.object(hubs, 'resolve_endpoint', AsyncMock(side_effect=AssertionError('No UDP'))), \
                    patch.object(session, 'open_media_tls', AsyncMock(side_effect=
                        load('connect3.tls').MediaTLSFailure('media_certificate_pin_mismatch'))) as connect, \
                    patch.object(session, 'open_connect3_media_tcp', AsyncMock()) as tcp, \
                    patch.object(session, 'open_r002_media_tcp', AsyncMock()) as legacy_tcp:
                with self.assertRaises(RuntimeError):
                    await hub.acquire('viewer')
                self.assertEqual(connect.await_count, 1)
                tcp.assert_not_awaited()
                legacy_tcp.assert_not_awaited()
                self.assertEqual(hub._tls_blocked_reason, 'certificate_changed')
                with self.assertRaises(cgi.CGIError):
                    await hub.acquire('another_viewer')
                self.assertEqual(connect.await_count, 1)
        finally:
            await hub.stop()

    async def test_real_qv_peer_decodes_manual_r002_audio_video_and_releases(self):
        hub = self.hub()
        peer = SyntheticQVPeer(audio_codec=4, audio_channels=0, audio_frame_type=3)
        received = {'video': 0, 'audio': 0}

        def frame(kind, _frame):
            received[kind] += 1

        async def material(host, auth, **kwargs):
            self.assertEqual((host, auth, kwargs['port']), ('192.0.2.10', PASSWORD, 443))
            self.assertEqual(kwargs['certificate_sha256'], 'a' * 64)
            kwargs['diagnostics'].update(tls_verified=True, tls_policy='certificate_pin',
                                          authentication_status='accepted')
            return cgi.StreamMaterial(STREAM_KEY)

        hub.frame_listeners.add(frame)
        await hub.start()
        try:
            with patch.object(hubs, 'resolve_endpoint', AsyncMock(side_effect=AssertionError('No UDP'))), \
                    patch.object(live, 'discover', AsyncMock(side_effect=AssertionError('No broadcast'))), \
                    patch.object(live, 'read_stream_material', AsyncMock(side_effect=material)), \
                    patch.object(session, 'open_connect3_media_tcp', AsyncMock(return_value=(peer.reader, peer))) as connect, \
                    patch.object(session, 'open_r002_media_tcp', AsyncMock(side_effect=AssertionError('No fallback'))), \
                    patch.object(session, 'open_media_tls', AsyncMock(side_effect=AssertionError('No TLS fallback'))):
                await hub.acquire('viewer')
                async with asyncio.timeout(3):
                    while not all(received.values()):
                        await asyncio.sleep(.01)
                self.assertEqual(hub.live.observation['endpoint_source'], 'configured_r002_ip')
                self.assertEqual(hub.live.observation['audio']['channels'], 1)
                self.assertEqual(hub.live.observation['audio']['reported_channels'], 0)
                self.assertEqual(hub.live.observation['audio']['decode_errors'], 0)
                self.assertTrue(hub.live.observation['play_accepted'])
                self.assertTrue(hub.live.observation['cgi_https_verified'])
                self.assertEqual(peer.outputs, [])
                await hub.release('viewer')
                self.assertEqual(connect.await_count, 1)
                self.assertEqual(peer.teardowns, 1)
                self.assertTrue(peer.closed)
                self.assertIsNone(hub.live.task)
        finally:
            await hub.stop()
