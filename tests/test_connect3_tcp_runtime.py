"""Explicit C3 TCP video-only routing with synthetic frames; no device I/O."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load, ROOT

caps = load('capabilities')
hubs = load('connect3.hub')
live = load('connect3.live')
cgi = load('connect3.cgi')
control = load('connect3.control')
talk = load('connect3.talk')
r002 = load('r002.hub')
PIN, AUTH = 'a' * 64, 'SYNTHETIC_TCP_LOCAL_PASSWORD'


def tcp_data():
    return {'host': '192.0.2.33', 'auth_code': AUTH, 'certificate_sha256': PIN,
        'media_certificate_sha256': 'b' * 64, 'cgi_port': 443, 'media_port': 9443,
        'experimental_video': True, 'experimental_outputs': True,
        'opening_code': 'SYNTHETIC_OPENING_CODE', 'media_transport': 'connect3_tcp',
        'media_tcp_approved': True,
        'trust_endpoint': {'host': '192.0.2.33', 'cgi_port': 443,
            'media_port': 34567, 'media_transport': 'connect3_tcp'},
        'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}}


class TCPRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=tcp_data()))
        self.sessions = []
        self.decoder = SimpleNamespace(errors=0,
            feed=Mock(return_value=([SimpleNamespace(pts=1)], b'SYNTHETIC_TCP_JPEG')), close=Mock())
        owner = self
        class Session:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs
                self.observation = args[-1]
                self._transport = kwargs['transport']
                self._close_task = None
                self.closed = False
                owner.sessions.append(self)
            async def run(self, callback):
                self.observation.update(stage='waiting_video', setup_accepted=True,
                    play_accepted=True, media_packets=2, media_tcp_connected=True,
                    media_tls_verified=False, media_tls_policy='connect3_explicit_plain_tcp')
                await callback(SimpleNamespace(is_audio=True))
                await callback(SimpleNamespace(frame_type=1))
                await asyncio.Future()
            async def close(self):
                self.closed = True
                self.observation['tcp_closed'] = True
        async def read(*args, diagnostics=None, **kwargs):
            diagnostics.update(authentication_status='accepted', tls_verified=True, tls_policy='certificate_pin')
            return cgi.StreamMaterial('SYNTHETIC_CGI_PRIVATE_STREAM_KEY')
        self.patchers = [patch.object(live, 'QVSession', Session),
            patch.object(live, 'VideoDecoder', Mock(return_value=self.decoder)),
            patch.object(live, 'AudioDecoder', Mock(side_effect=AssertionError('TCP audio decoder forbidden'))),
            patch.object(live, 'read_stream_material', AsyncMock(side_effect=read))]
        for patcher in self.patchers:
            patcher.start()
        self.read = live.read_stream_material
        await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.live.task)
        self.assertTrue(all(session.closed for session in self.sessions))

    async def test_tcp_effective_capabilities_are_video_only_with_poisoned_saved_flags(self):
        self.assertTrue(self.hub.capabilities.camera)
        self.assertTrue(self.hub.capabilities.live_media)
        for name in ('downstream_audio', 'talkback', 'strike', 'gate'):
            self.assertFalse(getattr(self.hub.capabilities, name), name)
        self.assertTrue(self.hub.entry.data['experimental_outputs'])
        self.assertEqual(self.hub.entry.data['opening_code'], 'SYNTHETIC_OPENING_CODE')
        tls_caps = caps.connect3_capabilities(True, True)
        self.assertTrue(all(getattr(tls_caps, name) for name in ('downstream_audio', 'talkback', 'strike', 'gate')))
        self.assertTrue(all(getattr(caps.r002_capabilities(True, True), name)
                            for name in ('downstream_audio', 'talkback', 'strike', 'gate')))

    async def test_manual_tcp_video_routes_once_with_verified_cgi_and_no_discovery_or_audio(self):
        callback = Mock()
        self.hub.frame_listeners.add(callback)
        with patch.object(live, 'discover', AsyncMock(side_effect=AssertionError('Manual discovery forbidden'))) as discover:
            await self.hub.acquire('viewer')
        discover.assert_not_called()
        self.read.assert_awaited_once()
        self.assertEqual(self.read.await_args.kwargs['port'], 443)
        self.assertEqual(self.read.await_args.kwargs['certificate_sha256'], PIN)
        self.assertEqual(len(self.sessions), 1)
        session = self.sessions[0]
        self.assertEqual(session.args[:3], ('192.0.2.33', 34567, ''))
        self.assertEqual(session.kwargs, {'transport': 'connect3_tcp', 'cgi_verified': True})
        callback.assert_called_once()
        self.assertEqual(callback.call_args.args[0], 'video')
        self.assertTrue(self.hub.connected)
        self.assertEqual(self.hub.image, b'SYNTHETIC_TCP_JPEG')
        media = self.hub.diagnostics()['media']
        for name in ('media_tcp_connected', 'media_setup_accepted', 'media_play_accepted',
                     'cgi_https_verified', 'streamkey_received'):
            self.assertTrue(media[name], name)
        self.assertFalse(media['media_tls_verified'])
        self.assertEqual(media['media_transport_selected'], 'connect3_tcp')
        self.assertEqual(media['media_port_selected'], 34567)
        self.assertEqual(media['media_packets_received'], 2)
        self.assertEqual(media['decoded_frames'], 1)
        self.assertEqual(media['ignored_audio_frames'], 1)
        self.assertEqual(media['audio']['status'], 'disabled_video_only')
        self.assertEqual(self.hub.entry.data['media_port'], 9443)
        for secret in (AUTH, PIN, 'b' * 64, 'SYNTHETIC', '192.0.2.33'):
            self.assertNotIn(secret, json.dumps(self.hub.diagnostics()))
        await self.hub.release('viewer')
        self.assertEqual(self.hub.diagnostics()['media']['close_reason'], 'viewer_closed')

    async def test_missing_or_nonboolean_tcp_approval_blocks_before_any_network(self):
        for approved in (None, False, 1, 'yes'):
            with self.subTest(approved=approved):
                self.hub.entry.data['media_tcp_approved'] = approved
                with patch.object(live, 'discover', AsyncMock()) as discover:
                    with self.assertRaisesRegex(cgi.CGIError, '^connect3_tcp_approval_required$'):
                        await self.hub.acquire('viewer')
                discover.assert_not_called()
                self.read.assert_not_called()
                self.assertFalse(self.sessions)

    async def test_network_failure_reports_fixed_reason_stage_and_closes_without_retry(self):
        outer = self
        for error, reason in ((ConnectionRefusedError('PRIVATE_ENDPOINT'), 'connection_refused'),
                              (TimeoutError('PRIVATE_ENDPOINT'), 'timeout'),
                              (ConnectionResetError('PRIVATE_ENDPOINT'), 'connection_reset')):
            class FailingSession:
                def __init__(self, *args, **kwargs):
                    self.observation = args[-1]
                    self.closed = False
                    outer.sessions.append(self)
                async def run(self, callback):
                    self.observation['stage'] = 'media_tcp_connect'
                    raise error
                async def close(self):
                    self.closed = True
                    self.observation['tcp_closed'] = True
            before = len(self.sessions)
            with patch.object(live, 'QVSession', FailingSession):
                with self.assertRaises(RuntimeError):
                    await self.hub.acquire('viewer')
            self.assertEqual(len(self.sessions), before + 1)
            media = self.hub.diagnostics()['media']
            self.assertEqual(media['last_error_reason'], reason)
            self.assertEqual(media['failed_at_stage'], 'media_tcp_connect')
            self.assertEqual(media['close_reason'], 'acquisition_failed')
            self.assertFalse(media['media_tcp_connected'])
            self.assertTrue(media['tcp_closed'])
            self.assertFalse(self.hub.consumers)
            self.assertNotIn('PRIVATE', json.dumps(media))

    async def test_qr_identity_check_still_precedes_tcp_cgi_and_media(self):
        self.hub.entry.data.update(credential_source='apk_space', credential_device_uid='SYNTHETIC_UID')
        with patch.object(live, 'discover', AsyncMock(return_value={'credential_identity_status': 'mismatch'})) as discover:
            with self.assertRaises(RuntimeError):
                await self.hub.acquire('viewer')
        discover.assert_awaited_once_with('192.0.2.33', expected_uid='SYNTHETIC_UID')
        self.read.assert_not_called()
        self.assertFalse(self.sessions)

    async def test_direct_outputs_and_microphone_block_without_acquire_or_material(self):
        self.hub.live.connected = True
        with patch.object(self.hub, 'acquire', AsyncMock()) as acquire:
            with self.assertRaisesRegex(control.OutputFailure, '^connect3_tcp_outputs_disabled$'):
                await self.hub.control.unlock(0)
        acquire.assert_not_called()
        self.assertEqual(self.hub.control.diagnostics()['command_count'], 0)
        with patch.object(self.hub.live, 'talk_parameters', Mock()) as params:
            with self.assertRaisesRegex(talk.TalkError, '^connect3_tcp_microphone_disabled$'):
                await self.hub.talkback.start('viewer')
        params.assert_not_called()
        self.assertIsNone(self.hub.talkback.owner)
        with self.assertRaisesRegex(RuntimeError, 'TCP microphone disabled'):
            self.hub.live.talk_parameters()

    async def test_existing_microphone_cannot_continue_after_selecting_tcp(self):
        session = SimpleNamespace(_close_task=None, _transport='tls')
        self.hub.live.connected = True
        self.hub.live.session = session
        self.hub.talkback._live_session = session
        self.assertFalse(self.hub.talkback._live_valid())
        self.hub.live.session = None

    async def test_tcp_expiry_checks_only_cgi_and_keeps_old_media_pin(self):
        self.hub.entry.data['tls_certificate_expires']['media'] = 'PRIVATE_OLD_MEDIA_DATE'
        self.hub.check_tls_trust()
        self.assertEqual(self.hub.entry.data['media_certificate_sha256'], 'b' * 64)
        self.hub.entry.data['tls_certificate_expires']['cgi'] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with patch.object(hubs, 'read_device', AsyncMock()) as read:
            result = await self.hub.execute('access')
        read.assert_not_called()
        self.assertEqual(result['reason'], 'tls_reapproval_required')

    async def test_tcp_media_certificate_action_is_unavailable_without_tls_probe(self):
        with patch.object(hubs, 'inspect_certificate', AsyncMock()) as inspect:
            result = await self.hub.execute('media_certificate')
        inspect.assert_not_called()
        self.assertEqual(result['reason'], 'media_tls_not_selected')

    async def test_unverified_cgi_never_grants_tcp_verified_flag(self):
        async def unverified(*args, diagnostics=None, **kwargs):
            diagnostics.update(authentication_status='accepted', tls_verified=False)
            return cgi.StreamMaterial('SYNTHETIC_PRIVATE_KEY')
        real_session = load('connect3.session').QVSession
        with patch.object(live, 'read_stream_material', AsyncMock(side_effect=unverified)), \
                patch.object(live, 'QVSession', real_session), \
                patch.object(real_session, 'run', AsyncMock()) as run:
            with self.assertRaises(RuntimeError):
                await self.hub.acquire('viewer')
        run.assert_not_called()
        self.assertEqual(self.hub.live.observation['last_error_reason'], 'connect3_tcp_verified_cgi_required')

    async def test_tcp_platforms_create_camera_status_and_no_output_or_audio_entities(self):
        created = []
        for platform in ('camera', 'button', 'sensor', 'binary_sensor', 'switch', 'image'):
            tree = ast.parse((ROOT / f'{platform}.py').read_text(encoding='utf-8'))
            scope = {node.name: (lambda *args, name=node.name: name)
                     for node in tree.body if isinstance(node, ast.ClassDef)}
            setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                         and node.name == 'async_setup_entry')
            exec(compile(ast.Module(body=[setup], type_ignores=[]), str(ROOT / f'{platform}.py'), 'exec'), scope)
            await scope['async_setup_entry'](None, SimpleNamespace(runtime_data=self.hub), created.extend)
        self.assertEqual(set(created), {'WelcomeEyeConnect3Camera', 'WelcomeEyeConnect3Status'})


if __name__ == '__main__':
    unittest.main()
