"""R002 secondary QV trials against synthetic readers; never real devices."""
import asyncio
from hashlib import sha256
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
import test_connect3_channel2 as channel2_fixture
from test_connect3_channel2 import live, session, cgi, control, channels
from test_connect3_control import packet_from_wire, unlock_reply
from test_connect3_media_protocol import KEY, aes, control_response, frame_bytes, media_response
from test_connect3_media_session import Writer
from test_connect3_tcp_security import setup
from test_r002_qv_hub import packet, observation

r002 = load('r002.hub')
qv = load('r002.qv')


class R002OutputTargetTests(unittest.IsolatedAsyncioTestCase):
    wait_for = channel2_fixture.Channel2Tests.wait_for
    commands = channel2_fixture.Channel2Tests.commands

    async def asyncSetUp(self):
        self.data = {'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_AUTH',
            'certificate_sha256': 'a' * 64, 'experimental_video': True,
            'second_channel_enabled': True, 'experimental_outputs': True,
            'opening_code': 'SYNTHETIC_OPENING',
            'channel2_strike_trial_enabled': True, 'channel2_gate_trial_enabled': True}
        self.hub = r002.R002InvestigationHub(None, SimpleNamespace(data=self.data))
        self.proxy = self.hub.channel2
        self.readers, self.writers, self.order = [], [], []

        async def discover(*args, **kwargs):
            self.order.append('discovery')
            return observation(packet())

        async def material(*args, diagnostics=None, **kwargs):
            self.order.append('cgi')
            diagnostics.update(authentication_status='accepted', tls_verified=True,
                tls_policy='certificate_pin')
            return cgi.StreamMaterial(KEY)

        async def connect(*args):
            self.order.append('media')
            reader, writer = asyncio.StreamReader(), Writer()
            reader.feed_data(setup() + b''.join(control_response()) +
                b''.join(media_response(frame_bytes())))
            self.readers.append(reader)
            self.writers.append(writer)
            return reader, writer

        decoder = SimpleNamespace(errors=0,
            feed=Mock(return_value=([SimpleNamespace(pts=1)], b'SYNTHETIC_JPEG')), close=Mock())
        self.patchers = [
            patch.object(qv, 'discover_qv', AsyncMock(side_effect=discover)),
            patch.object(live, 'read_stream_material', AsyncMock(side_effect=material)),
            patch.object(live, 'VideoDecoder', Mock(return_value=decoder)),
            patch.object(live, 'discover', AsyncMock(side_effect=AssertionError('Wrong discovery'))),
            patch.object(session, 'open_r002_media_tcp', AsyncMock(side_effect=connect)),
            patch.object(session, 'open_connect3_media_tcp', AsyncMock(side_effect=AssertionError('Wrong transport'))),
            patch.object(session, 'open_media_tls', AsyncMock(side_effect=AssertionError('No fallback'))),
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

    def writes(self, writer):
        return [value for value in writer.writes[1:] if aes(value[:32], decrypt=True)[0] == 0xFE]

    async def start_output(self, target, writer=None):
        count = len(self.writes(writer)) if writer is not None else 0
        task = asyncio.create_task(self.hub.control.unlock_target(target))
        if writer is None:
            await self.wait_for(lambda: bool(self.writers) or task.done())
            if task.done():
                await task
            writer = self.writers[-1]
        await self.wait_for(lambda: len(self.writes(writer)) > count or task.done())
        if task.done():
            await task
        return task

    async def test_four_targets_keep_exact_channel_output_and_existing_session(self):
        for target, (channel, output) in control.OUTPUT_TARGETS.items():
            with self.subTest(target=target):
                media = self.hub if channel == 1 else self.proxy
                await media.acquire('viewer')
                writer, reader = self.writers[-1], self.readers[-1]
                opened = len(self.writers)
                selected = aes(writer.writes[1][:32], decrypt=True)
                self.assertEqual(struct.unpack_from('<H', selected, 13)[0], channel)
                task = await self.start_output(target, writer)
                request = packet_from_wire(self.writes(writer)[0])
                self.assertEqual(request.parameters[:4], bytes((output, 0, channel, 1)))
                self.assertEqual(request.parameters[16:], sha256(b'SYNTHETIC_OPENING').hexdigest().encode())
                reader.feed_data(unlock_reply())
                self.assertTrue((await task).accepted)
                self.assertEqual(len(self.writers), opened)
                self.assertEqual(media.consumers, {'viewer'})
                await media.release('viewer')
        self.assertEqual(self.order, ['discovery', 'cgi', 'media'] * 4)
        session.open_connect3_media_tcp.assert_not_called()
        session.open_media_tls.assert_not_called()
        diagnostics = self.hub.diagnostics()
        self.assertEqual(diagnostics['control']['native_control_path'], 'r002_live_transparent_order_4')
        for target in control.OUTPUT_TARGETS:
            target_obs = diagnostics['control']['targets'][target]
            self.assertEqual(target_obs['request_send_attempt_count'], 1)
            self.assertTrue(target_obs['native_ack_accepted'])
            self.assertFalse(target_obs['physical_activation_verified'])
        for secret in ('SYNTHETIC', KEY, '192.0.2.1', 'a' * 64,
                sha256(b'SYNTHETIC_OPENING').hexdigest()):
            self.assertNotIn(secret, json.dumps(diagnostics))

    async def test_no_viewer_creates_one_discovered_session_and_closes_it(self):
        task = await self.start_output('gate_2')
        self.readers[0].feed_data(unlock_reply())
        await task
        self.assertEqual(self.commands(self.writers[0]), [0xA9, 1, 0xFE, 7])
        self.assertEqual(self.order, ['discovery', 'cgi', 'media'])
        self.assertTrue(self.writers[0].closed)
        self.assertIsNone(self.hub._media_claim)
        self.assertFalse(self.proxy.consumers)

    async def test_each_target_needs_exact_consent_and_prerequisites_before_io(self):
        for target in ('strike_2', 'gate_2'):
            option = control.SECONDARY_OUTPUT_OPTIONS[target]
            for override in ({option: False}, {option: 1}, {option: 'true'},
                    {'second_channel_enabled': False}, {'second_channel_enabled': 1},
                    {'experimental_video': False}, {'experimental_outputs': False}, {'opening_code': ''}):
                previous = dict(self.data)
                self.data.update(override)
                with self.subTest(target=target, override=override), self.assertRaises(control.OutputFailure):
                    await self.hub.control.unlock_target(target)
                self.data.clear()
                self.data.update(previous)
        qv.discover_qv.assert_not_called()
        live.read_stream_material.assert_not_called()
        self.assertFalse(self.writers)

    async def test_secondary_context_is_explicit_idle_and_never_inferred_from_c3_observations(self):
        for value in (None, False, 1, 'true'):
            data = {**self.data, 'second_channel_enabled': value}
            if value is None:
                del data['second_channel_enabled']
            data['observed_media_channels'] = {
                'binding': channels.media_profile_binding(data), 'channels': [1, 2]}
            hub = r002.R002InvestigationHub(None, SimpleNamespace(data=data))
            await hub.start()
            self.assertIsNone(hub.channel2)
            self.assertEqual(hub.confirmed_media_channels, frozenset())
            self.assertEqual(hub.channel_diagnostics()['channel_detection']['observed_count'], 0)
            await hub.stop()
        self.assertIsNone(self.proxy.live.task)
        qv.discover_qv.assert_not_called()
        self.assertFalse(self.writers)

    async def test_other_channel_never_interrupted_and_secondary_blocks_probes(self):
        for active, target in ((self.hub, 'strike_2'), (self.proxy, 'gate_1')):
            await active.acquire('viewer')
            writer = self.writers[-1]
            count = qv.discover_qv.await_count
            with self.assertRaisesRegex(RuntimeError, 'other channel busy'):
                await self.hub.control.unlock_target(target)
            for operation in (lambda: self.hub.execute('access'), self.hub.probe,
                    self.hub.check_certificate, self.hub.discover_qv):
                with self.assertRaises(RuntimeError):
                    await operation()
            self.assertEqual(qv.discover_qv.await_count, count)
            self.assertEqual(self.writes(writer), [])
            self.assertTrue(active.connected)
            await active.release('viewer')

    async def test_discovery_profile_rejection_blocks_secondary_without_cgi_or_fallback(self):
        with patch.object(qv, 'discover_qv', AsyncMock(return_value=observation(packet(channels=2)))) as discovery:
            with self.assertRaises(RuntimeError):
                await self.hub.control.unlock_target('strike_2')
            discovery.assert_awaited_once()
        live.read_stream_material.assert_not_called()
        self.assertFalse(self.writers)
        self.assertIsNone(self.hub._media_claim)
        self.assertEqual(self.proxy.live.observation['last_error_reason'], 'r002_discovery_endpoint_not_supported')

    async def test_direct_child_session_and_child_controller_have_no_output_grant(self):
        await self.proxy.acquire('viewer')
        current = self.proxy.live.session
        for output in (1, 2):
            with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
                await current.execute_output(output, 'UNSENT', channel=2)
        with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
            await current._send(b'UNSENT', physical=True)
        with self.assertRaisesRegex(control.OutputFailure, 'output_channel_not_supported'):
            await control.Connect3OutputController(self.proxy).unlock_target('strike_2')
        with self.assertRaisesRegex(RuntimeError, 'microphone route'):
            self.proxy.live.talk_parameters()
        self.assertFalse(self.writes(self.writers[-1]))

    async def test_revoked_target_or_changed_configuration_cannot_write_after_waiting(self):
        for key in ('channel2_strike_trial_enabled', 'second_channel_enabled',
                    'opening_code', 'auth_code', 'host', 'certificate_sha256'):
            await self.proxy.acquire('viewer')
            current, writer = self.proxy.live.session, self.writers[-1]
            original = self.data[key]
            await current._write_lock.acquire()
            task = asyncio.create_task(self.hub.control.unlock_target('strike_2'))
            try:
                await self.wait_for(lambda: current._output_future is not None)
                self.data[key] = False if key.endswith('enabled') else 'CHANGED_PRIVATE_VALUE'
            finally:
                current._write_lock.release()
            with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
                await task
            self.assertFalse(self.writes(writer))
            self.assertEqual(current.output_diagnostics()['request_send_attempt_count'], 0)
            self.data[key] = original
            await self.proxy.release('viewer')

    async def test_timeout_is_uncertain_without_retry_and_late_ack_does_not_unlock_next_target(self):
        await self.proxy.acquire('viewer')
        with patch.object(session, 'UNLOCK_TIMEOUT', 0.01):
            with self.assertRaisesRegex(control.OutputFailure, 'output_confirmation_timeout'):
                await self.hub.control.unlock_target('strike_2')
        self.readers[0].feed_data(unlock_reply())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(control.OutputFailure, 'output_session_uncertain'):
            await self.hub.control.unlock_target('gate_2')
        self.assertEqual(len(self.writes(self.writers[0])), 1)
        self.assertEqual(len(self.writers), 1)
        self.assertTrue(self.hub.control.diagnostics()['targets']['strike_2']['physical_request_uncertain'])

    async def test_parent_serializes_outputs_and_unload_cancels_pending_action(self):
        await self.proxy.acquire('viewer')
        task = await self.start_output('gate_2', self.writers[0])
        for target in control.OUTPUT_TARGETS:
            with self.assertRaisesRegex(control.OutputFailure, 'output_busy'):
                await self.hub.control.unlock_target(target)
        await self.hub.stop()
        with self.assertRaises((asyncio.CancelledError, control.OutputFailure)):
            await task
        self.assertTrue(self.writers[0].closed)
        self.assertEqual(len(self.writes(self.writers[0])), 1)
        self.assertFalse(self.hub.control._busy)
        self.assertIsNone(self.hub.control._active_session)
        self.assertFalse(self.proxy.consumers)
        self.assertTrue(self.hub.control.diagnostics()['targets']['gate_2']['physical_request_uncertain'])


if __name__ == '__main__':
    unittest.main()
