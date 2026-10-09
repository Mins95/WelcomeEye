"""Synthetic channel selection and isolated lifecycle; no intercom is contacted."""
import asyncio
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_connect3_media_protocol import KEY, aes, control_response, frame_bytes, media_response
from test_connect3_media_session import Writer
from test_connect3_tcp_security import audio_frame_bytes, setup

hubs = load('connect3.hub')
live = load('connect3.live')
session = load('connect3.session')
cgi = load('connect3.cgi')
channels = load('connect3.channels')
talk = load('connect3.talk')
control = load('connect3.control')
r002 = load('r002.hub')


class Channel2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data = {'host': '192.0.2.33', 'auth_code': 'SYNTHETIC_AUTH',
            'certificate_sha256': 'a' * 64, 'media_certificate_sha256': 'b' * 64,
            'experimental_video': True,
            'second_channel_enabled': True,
            'experimental_tcp_controls': True, 'experimental_outputs': True,
            'opening_code': 'SYNTHETIC_OPENING', 'media_transport': 'connect3_tcp',
            'media_tcp_approved': True,
            'trust_endpoint': {'host': '192.0.2.33', 'cgi_port': 443,
                'media_port': 34567, 'media_transport': 'connect3_tcp'},
            'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}}
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=self.data))
        self.proxy = self.hub.channel2
        self.readers, self.writers = [], []
        self.frames, self.main_frames = [], []
        self.proxy.frame_listeners.add(lambda kind, frame: self.frames.append((kind, frame)))
        self.hub.frame_listeners.add(lambda kind, frame: self.main_frames.append((kind, frame)))
        self.video_decoder = SimpleNamespace(errors=0,
            feed=Mock(return_value=([SimpleNamespace(pts=1)], b'SYNTHETIC_JPEG')), close=Mock())

        async def material(*args, diagnostics=None, **kwargs):
            diagnostics.update(authentication_status='accepted', tls_verified=True,
                tls_policy='certificate_pin')
            return cgi.StreamMaterial(KEY)

        async def connect(*args):
            reader, writer = asyncio.StreamReader(), Writer()
            reader.feed_data(setup() + b''.join(control_response()) +
                b''.join(media_response(frame_bytes())) +
                b''.join(media_response(audio_frame_bytes())))
            self.readers.append(reader)
            self.writers.append(writer)
            return reader, writer

        self.patchers = [
            patch.object(live, 'read_stream_material', AsyncMock(side_effect=material)),
            patch.object(live, 'VideoDecoder', Mock(return_value=self.video_decoder)),
            patch.object(live, 'discover', AsyncMock(side_effect=AssertionError('Manual discovery forbidden'))),
            patch.object(session, 'open_connect3_media_tcp', AsyncMock(side_effect=connect)),
            patch.object(session, 'open_media_tls', AsyncMock(side_effect=connect)),
            patch.object(session, 'KEEPALIVE_INTERVAL', 999),
        ]
        for patcher in self.patchers:
            patcher.start()
        await self.hub.start()

    async def asyncTearDown(self):
        try:
            await self.hub.stop()
            self.assertIsNone(self.proxy.live.task)
            self.assertIsNone(self.hub._media_claim)
            self.assertFalse(self.proxy.consumers)
            self.assertTrue(all(writer.closed for writer in self.writers))
        finally:
            for patcher in reversed(self.patchers):
                patcher.stop()

    async def wait_for(self, predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0)

    def commands(self, writer):
        return [packet[0] if index == 0 else aes(packet[:32], decrypt=True)[0]
                for index, packet in enumerate(writer.writes)]

    async def test_channel_uses_parent_credentials_and_fixed_channel_with_audio_without_controls(self):
        await self.proxy.acquire('viewer')
        await self.wait_for(lambda: any(kind == 'audio' for kind, _ in self.frames))
        self.assertIs(self.proxy.entry, self.hub.entry)
        live.read_stream_material.assert_awaited_once_with('192.0.2.33', 'SYNTHETIC_AUTH',
            port=443, certificate_sha256='a' * 64, diagnostics={
                'authentication_status': 'accepted', 'tls_verified': True,
                'tls_policy': 'certificate_pin'})
        session.open_connect3_media_tcp.assert_awaited_once()
        session.open_media_tls.assert_not_called()
        live.discover.assert_not_called()
        header = aes(self.writers[0].writes[1][:32], decrypt=True)
        self.assertEqual(struct.unpack_from('<H', header, 13)[0], 2)
        self.assertEqual(header[16], 1)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        self.assertEqual([kind for kind, _ in self.frames], ['video', 'audio'])
        self.assertEqual(self.main_frames, [])
        self.assertIsNone(self.hub.image)
        self.assertEqual(self.proxy.image, b'SYNTHETIC_JPEG')
        self.assertTrue(self.proxy.capabilities.downstream_audio)
        for capability in ('talkback', 'strike', 'gate', 'manual_snapshot'):
            self.assertFalse(getattr(self.proxy.capabilities, capability))
        with self.assertRaisesRegex(RuntimeError, 'microphone route'):
            self.proxy.live.talk_parameters()
        with self.assertRaisesRegex(session.OutputFailure, 'channel_controls_unavailable'):
            await self.proxy.live.session.execute_output(1, 'UNSENT')
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        await self.proxy.release('viewer')
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1, 7])
        self.assertEqual(self.proxy.diagnostics()['selected_channel'], 2)
        self.assertFalse(self.proxy.diagnostics()['physical_channel_verified'])
        for secret in ('SYNTHETIC', KEY, 'a' * 64, 'b' * 64, '192.0.2.33'):
            self.assertNotIn(secret, json.dumps(self.hub.diagnostics()))

    async def test_tls_trial_keeps_media_pin_and_blocks_output_at_write_boundary(self):
        self.data['media_transport'] = 'tls'
        self.data.pop('trust_endpoint')
        self.data.pop('tls_certificate_expires')
        await self.proxy.acquire('viewer')
        session.open_media_tls.assert_awaited_once()
        self.assertEqual(session.open_media_tls.await_args.args[:3], ('192.0.2.33', 8443, 'b' * 64))
        session.open_connect3_media_tcp.assert_not_called()
        with self.assertRaisesRegex(session.OutputFailure, 'channel_controls_unavailable'):
            await self.proxy.live.session._send(b'UNSENT', physical=True)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])

    async def test_main_controls_and_explicit_diagnostics_refuse_before_network_during_trial(self):
        await self.proxy.acquire('viewer')
        with self.assertRaisesRegex(RuntimeError, 'other channel busy'):
            await self.hub.acquire('main')
        with self.assertRaises(RuntimeError):
            await self.hub.control.unlock(0)
        with self.assertRaises(RuntimeError):
            await self.hub.execute('access')
        self.assertEqual(live.read_stream_material.await_count, 1)
        self.assertEqual(len(self.writers), 1)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        self.assertTrue(self.proxy.connected)
        self.assertEqual(self.hub.control.diagnostics()['request_sent_count'], 0)

    async def test_main_reservation_and_busy_controls_prevent_trial_without_any_network(self):
        for field, value in (('_media_claim', self.hub.live),):
            setattr(self.hub, field, value)
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                await self.proxy.acquire('trial')
            setattr(self.hub, field, None)
        self.hub.control._busy = True
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            await self.proxy.acquire('trial')
        self.hub.control._busy = False
        self.hub.talkback.owner = object()
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            await self.proxy.acquire('trial')
        self.hub.talkback.owner = None
        self.hub._task = asyncio.create_task(asyncio.sleep(60))
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            await self.proxy.acquire('trial')
        self.hub._task.cancel()
        await asyncio.gather(self.hub._task, return_exceptions=True)
        self.hub._task = None
        live.read_stream_material.assert_not_called()
        self.assertEqual(self.writers, [])

    async def test_two_channel_viewers_share_single_session_without_global_deadline(self):
        await asyncio.gather(self.proxy.acquire('one'), self.proxy.acquire('two'))
        self.assertFalse(hasattr(self.proxy, '_deadline'))
        self.assertEqual(len(self.writers), 1)
        await self.proxy.release('one')
        self.assertFalse(self.writers[0].closed)
        await self.proxy.release('two')
        self.assertTrue(self.writers[0].closed)

    async def test_sequential_channels_keep_audio_video_and_observations_separate(self):
        for hub, owner, frames, count in ((self.hub, 'first', self.main_frames, 2),
                (self.proxy, 'second', self.frames, 2), (self.hub, 'first-again', self.main_frames, 4)):
            await hub.acquire(owner)
            await self.wait_for(lambda: len(frames) == count)
            await hub.release(owner)
        self.assertEqual([struct.unpack_from('<H', aes(writer.writes[1][:32], decrypt=True), 13)[0]
            for writer in self.writers], [1, 2, 1])
        self.assertEqual([kind for kind, _ in self.main_frames], ['video', 'audio', 'video', 'audio'])
        self.assertEqual([kind for kind, _ in self.frames], ['video', 'audio'])
        observed = self.hub.channel_diagnostics()
        self.assertEqual(observed['channel_detection']['observed_channels'], [1, 2])
        for channel in ('1', '2'):
            self.assertEqual(observed['channels'][channel]['selected_channel'], int(channel))
            self.assertEqual(observed['channels'][channel]['media']['decoded_frames'], 1)
        self.assertIsNone(self.hub._media_claim)

    async def test_first_verified_frame_persists_private_observation_once_and_survives_reload(self):
        entry = self.hub.entry
        update = Mock(side_effect=lambda updated_entry, *, data: setattr(updated_entry, 'data', data))
        self.hub.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
        self.assertNotIn('observed_media_channels', entry.data)
        await self.proxy.acquire('viewer')
        record = entry.data['observed_media_channels']
        self.assertEqual(set(record), {'binding', 'channels'})
        self.assertEqual(record['channels'], [2])
        self.assertEqual(record['binding'], channels.media_profile_binding(entry.data))
        self.assertEqual(self.data['auth_code'], entry.data['auth_code'])
        await self.proxy.release('viewer')
        await self.proxy.acquire('again')
        await self.proxy.release('again')
        update.assert_called_once()
        restored_data = {key: value for key, value in entry.data.items() if key != 'second_channel_enabled'}
        restored = hubs.Connect3Hub(None, SimpleNamespace(data=restored_data))
        try:
            self.assertIsNotNone(restored.channel2)
            self.assertIsNone(restored.live.task)
            self.assertIsNone(restored.channel2.live.task)
            self.assertEqual(restored.channel_diagnostics()['channel_detection']['source'], 'observed_stream')
            self.assertNotIn(record['binding'], json.dumps(restored.diagnostics()))
        finally:
            await restored.stop()

    async def test_observation_needs_first_frame_verified_cgi_play_and_unchanged_profile(self):
        binding = channels.media_profile_binding(self.data)
        update = Mock()
        self.hub.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
        accepted = {'cgi_https_verified': True, 'play_accepted': True, 'decoded_frames': 1}
        for changed in ({'cgi_https_verified': False}, {'play_accepted': False}, {'decoded_frames': 0}):
            self.hub.record_channel_observed(2, {**accepted, **changed}, binding)
        self.hub.record_channel_observed(2, accepted, None)
        self.hub.record_channel_observed(True, accepted, binding)
        self.data['host'] = '192.0.2.34'
        self.hub.record_channel_observed(2, accepted, binding)
        update.assert_not_called()
        self.assertEqual(self.hub.channel_diagnostics()['channel_detection']['observed_count'], 0)

    async def test_persistence_failure_does_not_break_live_or_expose_exception_details(self):
        self.hub.hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_update_entry=Mock(side_effect=ValueError('SECRET_PATH'))))
        await self.proxy.acquire('viewer')
        self.assertTrue(self.proxy.connected)
        diag = self.hub.channel_diagnostics()
        self.assertEqual(diag['channel_detection']['observation_save_error_type'], 'ValueError')
        self.assertNotIn('SECRET_PATH', json.dumps(diag))
        await self.proxy.release('viewer')

    async def test_direct_microphone_and_outputs_cannot_use_channel2_credentials(self):
        await self.proxy.acquire('viewer')
        talkback = talk.Talkback(self.proxy)
        with self.assertRaisesRegex(talk.TalkError, 'microphone_channel_route_unverified'):
            await talkback.start('viewer')
        controller = control.Connect3OutputController(self.proxy)
        with self.assertRaisesRegex(session.OutputFailure, 'output_channel_not_supported'):
            await controller.unlock(0)
        self.assertEqual(talkback.diagnostics['start_attempts'], 0)
        self.assertEqual(controller.diagnostics()['request_send_attempt_count'], 0)
        self.assertEqual(len(self.writers), 1)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])

    async def test_main_session_cannot_redirect_outputs_to_another_channel(self):
        await self.hub.acquire('main')
        with self.assertRaisesRegex(session.OutputFailure, 'output_channel_mismatch'):
            await self.hub.live.session.execute_output(0, 'UNSENT', channel=2)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        await self.hub.release('main')

    async def test_close_closes_viewers_and_session_then_allows_another_explicit_view(self):
        closed = AsyncMock()
        self.proxy.close_listeners.add(closed)
        await self.proxy.acquire('viewer')
        await self.proxy.stop(reason='viewer_closed')
        closed.assert_awaited_once()
        self.assertFalse(self.proxy.consumers)
        self.assertFalse(self.proxy.connected)
        self.assertIsNone(self.hub._media_claim)
        self.assertTrue(self.writers[0].closed)
        self.assertEqual(self.proxy.diagnostics()['close_reason'], 'viewer_closed')
        await self.proxy.acquire('again')
        self.assertEqual(len(self.writers), 2)
        self.assertTrue(self.proxy.connected)
        await self.proxy.release('again')

    async def test_close_cancels_pending_https_acquisition_without_credentials_retry(self):
        started = asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await asyncio.Future()
        with patch.object(live, 'read_stream_material', AsyncMock(side_effect=pending)) as read:
            acquiring = asyncio.create_task(self.proxy.acquire('viewer'))
            await asyncio.wait_for(started.wait(), 5)
            await self.proxy.stop(reason='viewer_closed')
            with self.assertRaises(RuntimeError):
                await acquiring
            read.assert_awaited_once()
        self.assertEqual(self.writers, [])
        self.assertIsNone(self.hub._media_claim)

    async def test_cancel_acquisition_releases_reservation_without_orphans(self):
        started = asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await asyncio.Future()
        with patch.object(live, 'read_stream_material', AsyncMock(side_effect=pending)):
            acquiring = asyncio.create_task(self.proxy.acquire('viewer'))
            await asyncio.wait_for(started.wait(), 5)
            acquiring.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await acquiring
        self.assertIsNone(self.proxy.live.task)
        self.assertIsNone(self.hub._media_claim)

    async def test_failed_cgi_never_opens_media_and_releases_reservation(self):
        with patch.object(live, 'read_stream_material', AsyncMock(side_effect=cgi.CGIError('auth_code_rejected'))) as read:
            with self.assertRaises(RuntimeError):
                await self.proxy.acquire('viewer')
            read.assert_awaited_once()
        self.assertEqual(self.writers, [])
        self.assertEqual(self.proxy.live.observation['last_error_reason'], 'auth_code_rejected')
        self.assertIsNone(self.hub._media_claim)

    async def test_unload_during_channel2_closes_listener_and_rejects_reopen(self):
        closed = AsyncMock()
        self.proxy.close_listeners.add(closed)
        await self.proxy.acquire('viewer')
        await self.hub.stop()
        closed.assert_awaited_once()
        self.assertEqual(self.proxy.diagnostics()['close_reason'], 'integration_unload')
        with self.assertRaises(RuntimeError):
            await self.proxy.acquire('again')
        self.assertEqual(len(self.writers), 1)

    async def test_option_enables_only_idle_channel2_and_r002_never_gets_second_channel(self):
        hub = hubs.Connect3Hub(None, SimpleNamespace(data=dict(self.data)))
        self.assertIsNotNone(hub.channel2)
        self.assertIsNone(hub.channel2.live.task)
        self.assertFalse(hasattr(hub.channel2, '_deadline'))
        await hub.stop()
        for disabled in (None, False, 1, 'yes'):
            data = {**self.data, 'second_channel_enabled': disabled}
            hub = hubs.Connect3Hub(None, SimpleNamespace(data=data))
            self.assertIsNone(hub.channel2)
            await hub.stop()
        hub = hubs.Connect3Hub(None, SimpleNamespace(data={**self.data, 'experimental_video': False}))
        self.assertIsNone(hub.channel2)
        await hub.stop()
        hub = r002.R002InvestigationHub(None, SimpleNamespace(data=self.data))
        self.assertIsNone(hub.channel2)
        await hub.stop()

    async def test_invalid_channel_policy_is_rejected_before_transport(self):
        for channel, controls_enabled in ((0, False), (3, False), (True, False), (2, True), (2, 1)):
            with self.assertRaisesRegex(session.qv.MediaProtocolError, 'invalid_media_channel_policy'):
                session.QVSession('192.0.2.33', 8443, 'a'*64, KEY, 'UNSENT', {},
                    channel=channel, controls_enabled=controls_enabled)


if __name__ == '__main__':
    unittest.main()
