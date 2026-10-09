"""LT secondary output routes with a synthetic worker; no sockets/device I/O."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from load_integration import load
from test_legacy_multichannel import Hub

control = load('control')
v1 = load('v1_control')
legacy = load('legacy_channel')


class LegacyOutputTargetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = SimpleNamespace(unique_id='SYNTHETIC_UID', data={
            'host': '192.0.2.70', 'username': 'SYNTHETIC_USER',
            'password': 'SYNTHETIC_PASSWORD', 'device_variant': 'connect2_r001',
            'channel': 16, 'second_channel_enabled': True,
            'channel2_strike_trial_enabled': True, 'channel2_gate_trial_enabled': True})
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()))
        self.hub = Hub(hass, self.entry)
        self.hub.stopped = False
        self.sessions = []
        self.allow_send = threading.Event()
        self.allow_send.set()
        self.respond = True
        self.send_error = None
        self.override_profile = None
        self.override_uid = None
        self.builder = Mock(return_value=b'SYNTHETIC-NOT-A-COMMAND')
        self.decoder = Mock(return_value=(123, 1, 0))
        self.patches = [patch.object(v1, 'build_unlock_request', self.builder),
                        patch.object(v1, 'decode_unlock_reply', self.decoder),
                        patch.object(v1, 'RESPONSE_TIMEOUT', .15)]
        for current in self.patches:
            current.start()

        def worker(generation, stop):
            _, channel, stream, mode = self.hub._profile_order()[0]
            if self.override_profile is not None:
                channel, stream, mode = self.override_profile
            session = SimpleNamespace(channel=channel, stream=stream, mode=mode,
                closed=threading.Event(), info=SimpleNamespace(uid=self.override_uid or self.entry.unique_id),
                encryption_profile=1, device_now=lambda: 123,
                send_packet=Mock(side_effect=self.send_error), interrupt_read=Mock(),
                connection_stage='ready')
            self.sessions.append(session)
            self.hub.session = session
            self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, self.hub._state, True)
            try:
                while not stop.wait(.002):
                    if self.allow_send.is_set():
                        self.hub.control.v1_media.send_pending(session)
                    if self.respond and session.send_packet.called:
                        self.hub.control.v1_media.observe(session, [(506, b'SYNTHETIC-REPLY')])
            finally:
                session.closed.set()
                self.hub.control.v1_media.media_closed(session)
        self.hub._worker = worker

    async def asyncTearDown(self):
        await self.hub.stop()
        for current in self.patches:
            current.stop()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)
        self.assertFalse(self.hub.control.lock.locked())

    async def wait_pending(self):
        async with asyncio.timeout(2):
            while self.hub.control.v1_media.pending is None:
                await asyncio.sleep(.001)

    async def test_each_secondary_target_uses_same_panel17_with_distinct_zero_based_output(self):
        for variant in ('connect_v1', 'connect2_r001'):
            self.entry.data['device_variant'] = variant
            for target, output in (('strike_2', 0), ('gate_2', 1)):
                self.hub.control.last_command = float('-inf')
                await self.hub.control.unlock_target(target)
                session = self.sessions[-1]
                self.assertEqual((session.channel, session.stream, session.mode), (17, 1, 2))
                session.send_packet.assert_called_once_with(b'SYNTHETIC-NOT-A-COMMAND')
                self.assertEqual(self.builder.call_args.args[-1], output)
                self.assertFalse(self.hub.consumers)
                self.assertFalse(self.hub.control.lock.locked())
        self.assertEqual(len(self.sessions), 4)
        self.assertEqual(self.hub.control.request_send_attempt_count, 4)
        diag = self.hub.control.diagnostics()['targets']
        for target in ('strike_2', 'gate_2'):
            self.assertEqual(diag[target]['request_send_attempt_count'], 2)
            self.assertTrue(diag[target]['native_ack_accepted'])
            self.assertFalse(diag[target]['physical_activation_verified'])

    async def test_policy_requires_distinct_strict_options_and_known_legacy_identity(self):
        for key in ('second_channel_enabled', 'channel2_strike_trial_enabled'):
            for value in (None, False, 1, 'true'):
                self.entry.data[key] = value
                with self.assertRaises(control.ProtocolError):
                    await self.hub.control.unlock_target('strike_2')
                self.assertFalse(self.hub.control.target_enabled('strike_2'))
            self.entry.data[key] = True
        self.entry.data['channel2_gate_trial_enabled'] = False
        self.assertTrue(self.hub.control.target_enabled('strike_2'))
        self.assertFalse(self.hub.control.target_enabled('gate_2'))
        self.entry.data['device_variant'] = 'legacy_unknown'
        self.assertFalse(self.hub.control.target_enabled('strike_2'))
        for invalid in ('strike', 'strike_3', 0, None, []):
            self.assertFalse(self.hub.control.target_enabled(invalid))
        self.assertEqual(self.sessions, [])
        self.builder.assert_not_called()

    async def test_existing_secondary_viewer_is_reused_and_retained(self):
        await self.hub.channel2.acquire('viewer')
        worker = self.hub.thread
        await self.hub.control.unlock_target('gate_2')
        self.assertIs(self.hub.thread, worker)
        self.assertTrue(worker.is_alive())
        self.assertEqual(len(self.sessions), 1)
        self.assertTrue(self.hub.channel2.connected)
        self.assertEqual(self.hub.channel2.consumers, {'viewer'})
        await self.hub.channel2.release('viewer')

    async def test_main_media_busy_fails_before_second_session_or_command(self):
        await self.hub.acquire('main-viewer')
        with self.assertRaises(legacy.ChannelBusyError):
            await self.hub.control.unlock_target('strike_2')
        self.assertEqual(len(self.sessions), 1)
        self.sessions[0].send_packet.assert_not_called()
        self.builder.assert_not_called()
        self.assertTrue(self.hub.connected)
        await self.hub.release('main-viewer')

    async def test_wrong_session_identity_or_profile_never_builds_or_sends(self):
        for profile, uid in (((16, 1, 2), None), ((17, 1, 1), None), (None, 'WRONG_DEVICE')):
            self.override_profile, self.override_uid = profile, uid
            with self.assertRaises(control.ProtocolError):
                await self.hub.control.unlock_target('strike_2')
            self.sessions[-1].send_packet.assert_not_called()
        self.builder.assert_not_called()

    async def test_configuration_revocation_after_queue_before_send_sends_nothing(self):
        self.allow_send.clear()
        action = asyncio.create_task(self.hub.control.unlock_target('strike_2'))
        await self.wait_pending()
        self.entry.data['channel2_strike_trial_enabled'] = False
        self.allow_send.set()
        with self.assertRaisesRegex(control.ProtocolError, 'output_authorization_changed'):
            await action
        self.sessions[-1].send_packet.assert_not_called()
        self.assertFalse(self.hub.control.physical_result_uncertain)

    async def test_cancel_before_send_drains_owned_worker_and_releases_output_lock(self):
        self.allow_send.clear()
        action = asyncio.create_task(self.hub.control.unlock_target('strike_2'))
        await self.wait_pending()
        with self.assertRaises(legacy.ChannelBusyError):
            await self.hub.channel2.acquire('unrelated-viewer')
        action.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await action
        self.assertFalse(self.hub.control.lock.locked())
        self.assertIsNone(self.hub.thread)
        self.sessions[-1].send_packet.assert_not_called()
        self.assertIsNone(self.hub.control.v1_media.pending)

    async def test_send_failure_no_retry_and_uncertain_session_blocks_both_secondary_targets(self):
        self.send_error = BrokenPipeError('SYNTHETIC_SECRET')
        self.respond = False
        await self.hub.channel2.acquire('viewer')
        with self.assertRaises(BrokenPipeError):
            await self.hub.control.unlock_target('strike_2')
        self.assertTrue(self.hub.control.physical_result_uncertain)
        self.hub.control.last_command = float('-inf')
        with self.assertRaisesRegex(control.ProtocolError, 'incertaine'):
            await self.hub.control.unlock_target('gate_2')
        self.sessions[-1].send_packet.assert_called_once()
        self.assertEqual(len(self.sessions), 1)
        diag = self.hub.control.diagnostics()['targets']
        self.assertTrue(diag['strike_2']['physical_request_uncertain'])
        self.assertFalse(diag['gate_2']['physical_request_uncertain'])
        self.assertNotIn('SYNTHETIC', json.dumps(diag))
        await self.hub.channel2.release('viewer')

    async def test_repeated_cancellation_keeps_lock_until_owned_cleanup_finishes(self):
        self.allow_send.clear()
        cleanup_started, permit_cleanup = asyncio.Event(), asyncio.Event()
        original_release = self.hub.release

        async def blocked_release(lease):
            cleanup_started.set()
            await permit_cleanup.wait()
            await original_release(lease)

        with patch.object(self.hub, 'release', side_effect=blocked_release):
            action = asyncio.create_task(self.hub.control.unlock_target('strike_2'))
            await self.wait_pending()
            action.cancel()
            await asyncio.wait_for(cleanup_started.wait(), 2)
            action.cancel()
            await asyncio.sleep(0)
            self.assertFalse(action.done())
            self.assertTrue(self.hub.control.lock.locked())
            with self.assertRaisesRegex(control.ProtocolError, 'déjà en cours'):
                await self.hub.control.unlock_target('gate_2')
            permit_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await action
        self.assertFalse(self.hub.control.lock.locked())
        self.assertFalse(self.hub.consumers)
        self.sessions[-1].send_packet.assert_not_called()

    async def test_timeout_and_cancel_after_send_are_uncertain_and_do_not_replay(self):
        for cancel in (False, True):
            self.respond = False
            self.hub.control.last_command = float('-inf')
            before = len(self.sessions)
            action = asyncio.create_task(self.hub.control.unlock_target('gate_2'))
            async with asyncio.timeout(2):
                while len(self.sessions) == before or not self.sessions[-1].send_packet.called:
                    await asyncio.sleep(.001)
            if cancel:
                action.cancel()
            with self.assertRaises(asyncio.CancelledError if cancel else TimeoutError):
                await action
            self.assertTrue(self.hub.control.physical_result_uncertain)
            self.sessions[-1].send_packet.assert_called_once()
            self.assertIsNone(self.hub.control.v1_media.pending)

    async def test_bad_or_rejected_ack_is_never_a_physical_verification(self):
        for failure in (control.ProtocolError('bad acknowledgment'), None):
            self.decoder.side_effect = failure
            self.decoder.return_value = (123, 0, 2)
            self.hub.control.last_command = float('-inf')
            with self.assertRaises(control.ProtocolError):
                await self.hub.control.unlock_target('gate_2')
            obs = self.hub.control.diagnostics()['targets']['gate_2']
            self.assertEqual(obs['physical_request_uncertain'], failure is not None)
            self.assertFalse(obs['physical_activation_verified'])
            self.assertFalse(obs['native_ack_accepted'])


if __name__ == '__main__':
    unittest.main()
