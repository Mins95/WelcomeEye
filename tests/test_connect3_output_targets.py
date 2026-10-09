"""Synthetic four-target output routing; no physical device is contacted."""
import asyncio
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

import test_connect3_channel2 as fixture
from test_connect3_channel2 import control, live, session
from test_connect3_control import packet_from_wire, unlock_reply
from test_connect3_media_protocol import KEY, aes


class OutputTargetTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.Channel2Tests.asyncSetUp
    asyncTearDown = fixture.Channel2Tests.asyncTearDown
    wait_for = fixture.Channel2Tests.wait_for
    commands = fixture.Channel2Tests.commands

    def enable_secondary(self):
        self.data.update(channel2_strike_trial_enabled=True, channel2_gate_trial_enabled=True)

    def writes(self, writer):
        return [value for value in writer.writes[1:] if aes(value[:32], decrypt=True)[0] == 0xFE]

    async def start_output(self, target, writer=None):
        count = len(self.writes(writer)) if writer is not None else 0
        task = asyncio.create_task(self.hub.control.unlock_target(target))
        if writer is None:
            await self.wait_for(lambda: bool(self.writers))
            writer = self.writers[-1]
        await self.wait_for(lambda: len(self.writes(writer)) > count or task.done())
        if task.done():
            await task
        return task

    async def test_four_targets_encode_exact_channel_and_output_on_the_selected_live_session(self):
        self.enable_secondary()
        for transport in ('connect3_tcp', 'tls'):
            self.data['media_transport'] = transport
            self.data['trust_endpoint'] = {'host': self.data['host'], 'cgi_port': 443,
                'media_port': 34567 if transport == 'connect3_tcp' else 8443,
                'media_transport': transport}
            self.data['tls_certificate_expires'] = {'cgi': '2099-01-01T00:00:00+00:00',
                **({'media': '2099-01-01T00:00:00+00:00'} if transport == 'tls' else {})}
            for target, pair in control.OUTPUT_TARGETS.items():
                with self.subTest(transport=transport, target=target):
                    channel, output = pair
                    media_hub = self.hub if channel == 1 else self.proxy
                    await media_hub.acquire('viewer')
                    writer, reader = self.writers[-1], self.readers[-1]
                    opened = len(self.writers)
                    task = await self.start_output(target, writer)
                    packet = packet_from_wire(self.writes(writer)[-1])
                    self.assertEqual(packet.parameters[:4], bytes((output, 0, channel, 1)))
                    self.assertEqual(packet.parameters[16:], sha256(b'SYNTHETIC_OPENING').hexdigest().encode())
                    reader.feed_data(unlock_reply())
                    self.assertTrue((await task).accepted)
                    self.assertEqual(len(self.writers), opened)
                    self.assertEqual(media_hub.consumers, {'viewer'})
                    self.assertTrue(media_hub.connected)
                    self.assertFalse(writer.closed)
                    await media_hub.release('viewer')
        diagnostics = self.hub.control.diagnostics()
        self.assertEqual(diagnostics['request_send_attempt_count'], 8)
        for target, pair in control.OUTPUT_TARGETS.items():
            observed = diagnostics['targets'][target]
            self.assertEqual((observed['channel'], observed['output']), pair)
            self.assertEqual(observed['request_send_attempt_count'], 2)
            self.assertEqual(observed['response_count'], 2)
            self.assertTrue(observed['native_ack_received'])
            self.assertTrue(observed['native_ack_accepted'])
            self.assertFalse(observed['physical_activation_verified'])
        for forbidden in ('SYNTHETIC', KEY, '192.0.2.33', sha256(b'SYNTHETIC_OPENING').hexdigest()):
            self.assertNotIn(forbidden, json.dumps(diagnostics))

    async def test_secondary_action_without_viewer_acquires_and_releases_one_target_session(self):
        self.enable_secondary()
        task = await self.start_output('gate_2')
        self.readers[0].feed_data(unlock_reply())
        await task
        self.assertEqual(len(self.writers), 1)
        self.assertEqual(self.commands(self.writers[0]), [0xA9, 1, 0xFE, 7])
        self.assertTrue(self.writers[0].closed)
        self.assertIsNone(self.hub._media_claim)
        self.assertFalse(self.proxy.consumers)
        self.assertFalse(self.hub.consumers)

    async def test_two_independent_trial_options_default_off_and_never_fall_back(self):
        self.assertTrue(self.hub.control.target_enabled('strike_1'))
        for target in ('strike_2', 'gate_2'):
            self.assertFalse(self.hub.control.target_enabled(target))
            with self.assertRaisesRegex(control.OutputFailure, 'channel2_output_trial_disabled'):
                await self.hub.control.unlock_target(target)
        self.data['channel2_strike_trial_enabled'] = True
        self.assertTrue(self.hub.control.target_enabled('strike_2'))
        self.assertFalse(self.hub.control.target_enabled('gate_2'))
        for value in (1, 'true', None, False):
            self.data['channel2_gate_trial_enabled'] = value
            with self.assertRaisesRegex(control.OutputFailure, 'channel2_output_trial_disabled'):
                await self.hub.control.unlock_target('gate_2')
        for target in ('strike', '', 'gate_3', None, [], True):
            with self.assertRaisesRegex(control.OutputFailure, 'invalid_output_target'):
                await self.hub.control.unlock_target(target)
        live.read_stream_material.assert_not_called()
        self.assertFalse(self.writers)

    async def test_all_secondary_prerequisites_and_opening_code_are_checked_before_io(self):
        self.enable_secondary()
        for override in ({'second_channel_enabled': False}, {'experimental_outputs': False},
                {'experimental_video': False}, {'opening_code': ''}, {'opening_code': None},
                {'experimental_tcp_controls': False}, {'media_tcp_approved': False}):
            previous = dict(self.data)
            self.data.update(override)
            with self.subTest(override=override), self.assertRaises(control.OutputFailure):
                await self.hub.control.unlock_target('strike_2')
            self.data.clear()
            self.data.update(previous)
        self.assertEqual(self.data['auth_code'], 'SYNTHETIC_AUTH')
        live.read_stream_material.assert_not_called()
        self.assertFalse(self.writers)

    async def test_other_channel_viewer_blocks_output_before_cgi_without_interrupting_viewer(self):
        self.enable_secondary()
        for current, target in ((self.hub, 'strike_2'), (self.proxy, 'gate_1')):
            await current.acquire('viewer')
            writer = self.writers[-1]
            count = live.read_stream_material.await_count
            with self.assertRaisesRegex(RuntimeError, 'other channel busy'):
                await self.hub.control.unlock_target(target)
            self.assertTrue(current.connected)
            self.assertEqual(live.read_stream_material.await_count, count)
            self.assertEqual(self.writes(writer), [])
            self.assertEqual(self.hub.control.diagnostics()['targets'][target]['request_send_attempt_count'], 0)
            await current.release('viewer')

    async def test_direct_session_and_child_controller_cannot_bypass_parent_grant(self):
        self.enable_secondary()
        await self.proxy.acquire('viewer')
        current = self.proxy.live.session
        self.assertFalse(self.proxy.capabilities.talkback)
        with self.assertRaisesRegex(RuntimeError, 'microphone route'):
            self.proxy.live.talk_parameters()
        for output in (1, 2):
            with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
                await current.execute_output(output, 'UNSENT', channel=2)
        with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
            await current._send(b'UNSENT', physical=True)
        child_controller = control.Connect3OutputController(self.proxy)
        with self.assertRaisesRegex(control.OutputFailure, 'output_channel_not_supported'):
            await child_controller.unlock_target('strike_2')
        self.assertFalse(self.writes(self.writers[-1]))

    async def test_one_parent_lock_covers_all_targets_and_ack_stays_with_its_target(self):
        self.enable_secondary()
        await self.proxy.acquire('viewer')
        task = await self.start_output('strike_2', self.writers[-1])
        for target in control.OUTPUT_TARGETS:
            with self.assertRaisesRegex(control.OutputFailure, 'output_busy'):
                await self.hub.control.unlock_target(target)
        self.readers[-1].feed_data(unlock_reply(order=3))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.readers[-1].feed_data(unlock_reply())
        await task
        self.assertEqual(len(self.writes(self.writers[-1])), 1)
        for target, obs in self.hub.control.diagnostics()['targets'].items():
            self.assertEqual(obs['response_count'], int(target == 'strike_2'))

    async def test_secondary_timeout_blocks_the_same_session_and_does_not_retry(self):
        self.enable_secondary()
        await self.proxy.acquire('viewer')
        with patch.object(session, 'UNLOCK_TIMEOUT', 0.01):
            with self.assertRaisesRegex(control.OutputFailure, 'output_confirmation_timeout'):
                await self.hub.control.unlock_target('strike_2')
        self.readers[-1].feed_data(unlock_reply())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(control.OutputFailure, 'output_session_uncertain'):
            await self.hub.control.unlock_target('gate_2')
        self.assertEqual(len(self.writers), 1)
        self.assertEqual(len(self.writes(self.writers[-1])), 1)
        diag = self.hub.control.diagnostics()['targets']['strike_2']
        self.assertTrue(diag['physical_request_uncertain'])
        self.assertFalse(diag['native_ack_received'])
        self.assertFalse(diag['physical_activation_verified'])

    async def test_revoking_only_the_pending_target_before_write_sends_nothing(self):
        self.enable_secondary()
        await self.proxy.acquire('viewer')
        current = self.proxy.live.session
        await current._write_lock.acquire()
        task = asyncio.create_task(self.hub.control.unlock_target('strike_2'))
        try:
            await self.wait_for(lambda: current._output_future is not None)
            self.data['channel2_strike_trial_enabled'] = False
        finally:
            current._write_lock.release()
        with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
            await task
        self.assertTrue(self.hub.control.target_enabled('gate_2'))
        self.assertEqual(self.writes(self.writers[-1]), [])
        self.assertFalse(current.output_diagnostics()['physical_request_uncertain'])

    async def test_cancellation_after_secondary_write_is_uncertain_and_cleans_owned_lease(self):
        self.enable_secondary()
        await self.proxy.acquire('viewer')
        task = await self.start_output('gate_2', self.writers[-1])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.proxy.consumers, {'viewer'})
        self.assertEqual(len(self.writes(self.writers[-1])), 1)
        self.assertTrue(self.hub.control.diagnostics()['targets']['gate_2']['physical_request_uncertain'])
        self.assertFalse(self.hub.control._busy)
        self.assertIsNone(self.hub.control._active_session)

    async def test_opening_code_or_connection_identity_changed_before_write_revokes_grant(self):
        self.enable_secondary()
        for key in ('opening_code', 'auth_code', 'host', 'certificate_sha256'):
            await self.proxy.acquire('viewer')
            current = self.proxy.live.session
            original = self.data[key]
            await current._write_lock.acquire()
            task = asyncio.create_task(self.hub.control.unlock_target('strike_2'))
            try:
                await self.wait_for(lambda: current._output_future is not None)
                self.data[key] = 'CHANGED_PRIVATE_VALUE'
            finally:
                current._write_lock.release()
            with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
                await task
            self.assertEqual(self.writes(self.writers[-1]), [])
            self.assertFalse(current.output_diagnostics()['physical_request_uncertain'])
            self.data[key] = original
            await self.proxy.release('viewer')

    async def test_legacy_unlock_always_targets_channel_one_and_never_follows_channel_two_view(self):
        self.enable_secondary()
        await self.proxy.acquire('viewer')
        with self.assertRaisesRegex(RuntimeError, 'other channel busy'):
            await self.hub.control.unlock(0)
        self.assertEqual(self.writes(self.writers[-1]), [])
        self.assertEqual(self.hub.control.diagnostics()['last_target'], 'strike_1')

    async def test_tls_block_while_waiting_write_lock_revokes_grant_before_async_close(self):
        self.enable_secondary()
        await self.proxy.acquire('viewer')
        current = self.proxy.live.session
        writer = self.writers[-1]
        await current._write_lock.acquire()
        task = asyncio.create_task(self.hub.control.unlock_target('strike_2'))
        try:
            await self.wait_for(lambda: current._output_future is not None)
            self.hub._block_tls('certificate_changed', 'cgi')
            self.assertFalse(self.proxy.live.connected)
            # Release immediately, without waiting for the scheduled socket
            # cleanup: the write grant itself must already have been revoked.
        finally:
            current._write_lock.release()
        with self.assertRaises(control.OutputFailure):
            await task
        await self.hub._tls_close_task
        self.assertEqual(self.writes(writer), [])
        self.assertEqual(current.output_diagnostics()['request_send_attempt_count'], 0)
        self.assertFalse(current.output_diagnostics()['physical_request_uncertain'])
        self.assertTrue(writer.closed)
        self.assertFalse(self.proxy.consumers)

    async def test_child_closing_or_disconnected_revokes_pending_grant_without_physical_write(self):
        self.enable_secondary()
        for closing in (True, False):
            await self.proxy.acquire('viewer')
            current = self.proxy.live.session
            await current._write_lock.acquire()
            task = asyncio.create_task(self.hub.control.unlock_target('gate_2'))
            try:
                await self.wait_for(lambda: current._output_future is not None)
                if closing:
                    self.proxy._closing = True
                else:
                    self.proxy.live.connected = False
            finally:
                current._write_lock.release()
            with self.assertRaisesRegex(control.OutputFailure, 'channel_controls_unavailable'):
                await task
            self.assertEqual(self.writes(self.writers[-1]), [])
            self.assertEqual(current.output_diagnostics()['request_send_attempt_count'], 0)
            self.assertFalse(current.output_diagnostics()['physical_request_uncertain'])
            self.proxy._closing = False
            await self.proxy.release('viewer')


if __name__ == '__main__':
    unittest.main()
