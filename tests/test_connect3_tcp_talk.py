"""Explicit TCP talk with synthetic peers; no devices or physical commands."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
import test_connect3_talk as existing
from test_connect3_media_protocol import KEY, MATERIAL, aes, control_response
from test_connect3_media_session import Writer
from test_connect3_tcp_security import setup

t = existing.t
caps = load('capabilities')


class TCPTalkTests(existing.TalkTests):
    """Run the established owner/codec/cancellation lifecycle on TCP too."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tls_connect = self.connect
        self.hub.variant = caps.DeviceVariant.CONNECT3
        self.hub.entry = SimpleNamespace(data={'media_transport': 'connect3_tcp',
            'experimental_tcp_controls': True, 'media_tcp_approved': True})
        self.hub.capabilities = SimpleNamespace(talkback=True)
        self.live_session = SimpleNamespace(_transport='connect3_tcp', _close_task=None)
        self.live.session = self.live_session
        self.parameters = dict(host='192.0.2.1', port=34567, pin='',
            stream_key=KEY, password='SYNTHETIC_PASSWORD',
            transport='connect3_tcp', cgi_verified=True)
        self.live.talk_parameters = Mock(side_effect=lambda: dict(self.parameters))
        self.tcp_patch = patch.object(t, 'open_connect3_media_tcp',
            AsyncMock(return_value=(self.reader, self.writer)))
        self.connect = self.tcp_patch.start()
        self.other_patch = patch.object(t, 'open_r002_media_tcp', AsyncMock(
            side_effect=AssertionError('No R002 transport fallback')))
        self.other_connect = self.other_patch.start()

    async def asyncTearDown(self):
        try:
            await super().asyncTearDown()
            self.tls_connect.assert_not_called()
            self.other_connect.assert_not_called()
            self.assertIsNone(self.talk._transport)
            self.assertFalse(self.talk._tcp_setup_attempted)
            self.assertFalse(self.talk._tcp_open_attempted)
        finally:
            self.tcp_patch.stop()
            self.other_patch.stop()

    async def test_controls_opt_in_and_capability_are_checked_before_material_or_socket(self):
        for value in (False, None, 1, 'true'):
            self.hub.entry.data['experimental_tcp_controls'] = value
            with self.assertRaisesRegex(t.TalkError, '^connect3_tcp_microphone_disabled$'):
                await self.talk.start('viewer')
        self.hub.entry.data['experimental_tcp_controls'] = True
        self.hub.capabilities.talkback = False
        with self.assertRaisesRegex(t.TalkError, '^connect3_tcp_microphone_disabled$'):
            await self.talk.start('viewer')
        self.live.talk_parameters.assert_not_called()
        self.connect.assert_not_called()

    async def test_verified_https_origin_and_matching_live_transport_are_required_before_socket(self):
        for verified in (False, None, 1, 'true'):
            self.parameters['cgi_verified'] = verified
            with self.assertRaisesRegex(t.TalkError, '^connect3_tcp_verified_cgi_required$'):
                await self.talk.start('viewer')
            self.assertIsNone(self.talk.owner)
        self.parameters['cgi_verified'] = True
        self.live_session._transport = 'tls'
        with self.assertRaisesRegex(t.TalkError, '^talk_start_cancelled_or_media_lost$'):
            await self.talk.start('viewer')
        self.connect.assert_not_called()
        self.assertEqual(self.writer.writes, [])

    async def test_each_crypto_downgrade_is_refused_before_building_credentials(self):
        for mode, sha in ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0)):
            with self.subTest(mode=mode, sha=sha):
                self.reader, self.writer = asyncio.StreamReader(), Writer()
                self.connect.return_value = self.reader, self.writer
                self.reader.feed_data(setup(mode=mode, sha=sha))
                with patch.object(t.qv, 'CipherMaterial') as material, patch.object(t, 'build_open') as opening:
                    with self.assertRaisesRegex(t.TalkError, '^connect3_tcp_unsafe_crypto_mode$'):
                        await self.talk.start('viewer')
                material.assert_not_called()
                opening.assert_not_called()
                self.assertEqual(self.writer.writes, [t.build_setup()])
                self.assertEqual(self.writer.close_count, 1)
                self.assertFalse(self.talk.diagnostics['teardown_attempted'])
                self.assertFalse(self.talk.active)
                self.assertIsNone(self.talk.owner)

    async def test_short_key_and_refused_setup_never_build_open(self):
        for response, key in ((setup(result=1), KEY), (setup(), 'short')):
            self.reader, self.writer = asyncio.StreamReader(), Writer()
            self.connect.return_value = self.reader, self.writer
            self.parameters['stream_key'] = key
            self.reader.feed_data(response)
            with patch.object(t, 'build_open') as opening:
                with self.assertRaises((t.TalkError, t.qv.MediaProtocolError)):
                    await self.talk.start('viewer')
            opening.assert_not_called()
            self.assertEqual(self.writer.writes, [t.build_setup()])
            self.assertFalse(self.talk.diagnostics['teardown_attempted'])

    async def test_tcp_write_guard_rejects_clear_duplicate_and_non_talk_commands(self):
        await self.start()
        before = len(self.writer.writes)
        rejected = (t.build_setup(), b'adminapp2&&SYNTHETIC_PASSWORD' * 4,
            t.build_open(MATERIAL, 'SYNTHETIC_PASSWORD', timestamp_seconds=0),
            b''.join(control_response(command=0xFE, parameters=b'NEVER_SEND_OPENING_CODE')),
            t.qv.build_play_request(MATERIAL, username='adminapp2', password='SYNTHETIC_PASSWORD',
                channel=1, stream=1, timestamp_seconds=0))
        for packet in rejected:
            with self.assertRaises((t.TalkError, t.qv.MediaProtocolError)):
                await self.talk._send(packet)
        self.assertEqual(len(self.writer.writes), before)
        for wire in self.writer.writes:
            self.assertNotIn(b'adminapp2', wire)
            self.assertNotIn(b'SYNTHETIC_PASSWORD', wire)
        self.assertEqual(self.talk.diagnostics['credential_protection'], 'qv_aes256_sha256')
        self.assertEqual(self.talk.diagnostics['microphone_audio_protection'], 'encrypted_prefix_only')
        self.assertFalse(self.talk.diagnostics['media_replay_protected'])

    async def test_audio_write_requires_native_negotiated_layout_and_active_sending(self):
        await self.start()
        packet = t.build_audio(MATERIAL, bytes(range(80)), 4, 0, timestamp_seconds=0)
        with self.assertRaisesRegex(t.TalkError, '^connect3_tcp_talk_command_not_allowed$'):
            await self.talk._send(packet)
        bad = bytearray(aes(packet[:32], decrypt=True))
        bad[15] = 1
        with self.assertRaisesRegex(t.TalkError, '^connect3_tcp_talk_command_not_allowed$'):
            await self.talk._send(aes(bytes(bad)) + packet[32:], audio=True)
        await self.talk._send(packet, audio=True)
        self.assertEqual(self.writer.writes[-1][64:], packet[64:])
        self.talk.disable('viewer')
        before = len(self.writer.writes)
        self.assertFalse(await self.talk._send(packet, audio=True))
        self.assertEqual(len(self.writer.writes), before)

    async def test_live_policy_change_stops_microphone_without_closing_video(self):
        await self.start()
        self.hub.entry.data['experimental_tcp_controls'] = False
        await self.talk.feed('viewer', existing.pcm())
        self.assertFalse(self.talk.active)
        self.assertIsNone(self.talk.owner)
        self.assertTrue(self.live.connected)
        self.assertEqual(self.commands()[-1], 7)

    async def test_tcp_cleanup_guard_error_is_sanitized_and_closes_once(self):
        await self.start()
        with patch.object(self.talk, '_check_connect3_tcp_write', side_effect=t.TalkError('PRIVATE')):
            await self.talk.stop('viewer')
        self.assertEqual(self.writer.close_count, 1)
        self.assertEqual(self.talk.diagnostics['cleanup_error_type'], 'teardown_failed')
        self.assertNotIn('PRIVATE', json.dumps(self.talk.diagnostics))
        self.assertIsNone(self.talk.owner)
