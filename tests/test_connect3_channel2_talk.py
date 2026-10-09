"""Synthetic secondary talk over an accepted selected live context, never hardware.

The wire keeps the APK's 65535 talk selector. These checks cannot prove which
physical speaker a monitor routes that context to; the user trial stays opt-in.
"""
import asyncio
import json
import struct
import unittest
from unittest.mock import AsyncMock, patch

from load_integration import load
import test_connect3_channel2 as channel_tests
from test_connect3_media_protocol import KEY, aes, control_response
from test_connect3_media_session import Writer, setup
from test_connect3_talk import FakeEncoder, open_response, pcm

talk = load('connect3.talk')
live = load('connect3.live')
route = load('connect3.talk_route')


class Channel2TalkTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = channel_tests.Channel2Tests.asyncSetUp
    asyncTearDown = channel_tests.Channel2Tests.asyncTearDown
    wait_for = channel_tests.Channel2Tests.wait_for
    commands = channel_tests.Channel2Tests.commands

    def wire(self):
        return (setup() + open_response()
            + b''.join(control_response(command=12))
            + b''.join(control_response(command=13)))

    async def test_explicit_channel2_selected_context_65535_audio_and_close_both_transports(self):
        for transport in ('tls', 'connect3_tcp'):
            with self.subTest(transport=transport):
                self.data['experimental_channel2_microphone'] = True
                self.data['media_transport'] = transport
                self.data['trust_endpoint'] = {'host': self.data['host'], 'cgi_port': 443,
                    'media_port': 8443 if transport == 'tls' else 34567, 'media_transport': transport}
                self.data['tls_certificate_expires'] = {
                    'cgi': '2099-01-01T00:00:00+00:00', 'media': '2099-01-01T00:00:00+00:00'}
                reader, writer = asyncio.StreamReader(), Writer()
                reader.feed_data(self.wire())
                opener = 'open_media_tls' if transport == 'tls' else 'open_connect3_media_tcp'
                other = 'open_connect3_media_tcp' if transport == 'tls' else 'open_media_tls'
                with (patch.object(talk, opener, AsyncMock(return_value=(reader, writer))) as connect,
                        patch.object(talk, other, AsyncMock()) as no_fallback,
                        patch.object(talk, 'AudioEncoder', FakeEncoder)):
                    await self.proxy.acquire('video')
                    reads = live.read_stream_material.await_count
                    media_writer = self.writers[-1]
                    media_session = self.proxy.live.session
                    await self.proxy.talkback.start('viewer')
                    await self.proxy.talkback.feed('viewer', pcm())
                    self.assertTrue(self.proxy.talkback.active)
                    self.assertIs(self.proxy.live.session, media_session)
                    self.assertEqual(live.read_stream_material.await_count, reads)
                    self.assertEqual(self.commands(media_writer), [169, 1])
                    self.assertEqual(self.commands(writer), [169, 11, 12, 13, 13, 162])
                    raw_open = aes(writer.writes[1][:32], decrypt=True)
                    self.assertEqual(struct.unpack_from('<H', raw_open, 13)[0], 65535)
                    for request in writer.writes[2:5]:
                        self.assertEqual(aes(request[:32], decrypt=True)[11:13], b'\xff\xff')
                    diag = self.proxy.diagnostics()['microphone']
                    self.assertEqual(diag['requested_channel'], 2)
                    self.assertEqual(diag['talk_selector'], 65535)
                    self.assertEqual(diag['route_context'], 'selected_live_channel')
                    self.assertTrue(diag['route_experimental'])
                    self.assertFalse(diag['physical_route_verified'])
                    self.assertEqual(diag['frames_sent'], 1)
                    for secret in (KEY, 'SYNTHETIC_AUTH', 'SYNTHETIC_OPENING', '192.0.2.33'):
                        self.assertNotIn(secret, json.dumps(self.proxy.diagnostics()))
                    await self.proxy.talkback.stop('viewer')
                    self.assertEqual(self.commands(writer)[-1], 7)
                    self.assertEqual(writer.close_count, 1)
                    self.assertTrue(self.proxy.connected)
                    connect.assert_awaited_once()
                    no_fallback.assert_not_called()
                    await self.proxy.release('video')
                    self.assertEqual(self.commands(media_writer), [169, 1, 7])
                    self.assertIsNone(self.hub._media_claim)

    async def test_option_requires_boolean_opt_in_and_current_accepted_selected_video(self):
        await self.proxy.acquire('video')
        with patch.object(talk, 'open_connect3_media_tcp', AsyncMock()) as connect:
            for value in (None, False, 1, 'true'):
                self.data['experimental_channel2_microphone'] = value
                self.assertFalse(self.proxy.capabilities.talkback)
                with self.assertRaisesRegex(talk.TalkError, 'microphone_channel_route_unverified'):
                    await self.proxy.talkback.start('viewer')
            self.data['experimental_channel2_microphone'] = True
            observation = self.proxy.live.observation
            for field, value in (('play_accepted', False), ('cgi_https_verified', False),
                    ('selected_channel', 1), ('decoded_frames', 0)):
                old = observation[field]
                observation[field] = value
                with self.assertRaisesRegex(talk.TalkError, 'microphone_channel_route_unverified'):
                    await self.proxy.talkback.start('viewer')
                observation[field] = old
            self.proxy.live._session_profile_binding = 'invalidated'
            with self.assertRaisesRegex(RuntimeError, 'microphone route'):
                self.proxy.live.talk_parameters()
            connect.assert_not_called()
        self.assertEqual(self.commands(self.writers[-1]), [169, 1])

    async def test_tcp_opt_in_is_still_required_and_r002_does_not_gain_microphone2(self):
        self.data['experimental_channel2_microphone'] = True
        await self.proxy.acquire('video')
        for key in ('experimental_tcp_controls', 'media_tcp_approved'):
            self.data[key] = False
            self.assertFalse(self.proxy.capabilities.talkback)
            self.assertFalse(route.secondary_talk_context_valid(self.proxy, self.proxy.live.session))
            self.data[key] = True
        self.proxy.variant = load('capabilities').DeviceVariant.R002
        self.assertFalse(self.proxy.capabilities.talkback)
        self.assertFalse(route.secondary_talk_context_valid(self.proxy, self.proxy.live.session))

    async def test_revoke_while_setup_pending_sends_no_open_credentials(self):
        self.data['experimental_channel2_microphone'] = True
        await self.proxy.acquire('video')
        reader, writer = asyncio.StreamReader(), Writer()
        with patch.object(talk, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, writer))) as connect:
            starting = asyncio.create_task(self.proxy.talkback.start('viewer'))
            await self.wait_for(lambda: len(writer.writes) == 1)
            self.data['experimental_channel2_microphone'] = False
            reader.feed_data(setup())
            with self.assertRaisesRegex(talk.TalkError, 'cancelled_or_media_lost'):
                await starting
            self.assertEqual(self.commands(writer), [169])
            self.assertEqual(writer.close_count, 1)
            self.assertIsNone(self.proxy.talkback.owner)
            self.assertIsNone(self.proxy.talkback._material)
            self.assertTrue(self.proxy.connected)
            connect.assert_awaited_once()

    async def test_profile_change_after_start_drops_audio_closes_only_talk(self):
        self.data['experimental_channel2_microphone'] = True
        await self.proxy.acquire('video')
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(self.wire())
        with (patch.object(talk, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, writer))),
                patch.object(talk, 'AudioEncoder', FakeEncoder)):
            await self.proxy.talkback.start('viewer')
            self.data['host'] = '192.0.2.34'
            await self.proxy.talkback.feed('viewer', pcm())
            self.assertFalse(self.proxy.talkback.active)
            self.assertNotIn(162, self.commands(writer))
            self.assertEqual(writer.close_count, 1)
            self.assertTrue(self.proxy.connected)
            self.assertEqual(self.commands(self.writers[-1]), [169, 1])

    async def test_replacement_live_session_never_reuses_previous_microphone_context(self):
        self.data['experimental_channel2_microphone'] = True
        await self.proxy.acquire('video')
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(self.wire())
        with (patch.object(talk, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, writer))),
                patch.object(talk, 'AudioEncoder', FakeEncoder)):
            await self.proxy.talkback.start('viewer')
            previous = self.proxy.live.session
            self.proxy.live.session = object()
            try:
                await self.proxy.talkback.feed('viewer', pcm())
                self.assertFalse(self.proxy.talkback.active)
                self.assertNotIn(162, self.commands(writer))
                self.assertEqual(writer.close_count, 1)
                self.assertIsNone(self.proxy.talkback._live_session)
            finally:
                self.proxy.live.session = previous

    async def test_video_release_closes_talk_before_releasing_channel_and_unload_leaves_no_owner(self):
        self.data['experimental_channel2_microphone'] = True
        await self.proxy.acquire('video')
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(self.wire())
        with (patch.object(talk, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, writer))),
                patch.object(talk, 'AudioEncoder', FakeEncoder)):
            await self.proxy.talkback.start('viewer')
            await self.proxy.release('video')
            self.assertEqual(writer.close_count, 1)
            self.assertIsNone(self.proxy.talkback.owner)
            self.assertIsNone(self.proxy.talkback._read_task)
            self.assertIsNone(self.proxy.talkback._material)
            self.assertIsNone(self.hub._media_claim)
            await self.hub.acquire('primary')
            await self.hub.release('primary')
            await self.hub.stop()
            self.assertIsNone(self.proxy.talkback.owner)


if __name__ == '__main__':
    unittest.main()
