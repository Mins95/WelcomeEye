"""Disabled physical targets never reach session/packet boundaries."""
import asyncio
from itertools import product
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_output_single_shot import v1

legacy = load('control')
qv = load('connect3.control')
cap = load('capabilities')
policy = load('opening_commands')
session_module = load('connect3.session')
protocol = load('connect3.protocol')


def hub_for(variant, preferences):
    data = dict(protocol_family=str(cap.family_for(variant)), device_variant=str(variant),
        second_channel_enabled=True, channel2_strike_trial_enabled=True,
        channel2_gate_trial_enabled=True, experimental_video=True, experimental_outputs=True,
        opening_code='SYNTHETIC_ONLY', **preferences)
    capabilities = cap.connect3_capabilities(True, True) if variant == cap.DeviceVariant.CONNECT3 else (
        cap.r002_capabilities(True, True) if variant == cap.DeviceVariant.R002 else cap.MATRIX[variant])
    return SimpleNamespace(entry=SimpleNamespace(data=data), variant=variant, channel=1,
        capabilities=capabilities, channel2=object(), secondary_channel_enabled=True, stopped=False)


class CommandPolicyTests(unittest.TestCase):
    def test_independent_choices_on_all_four_models_keep_fixed_pairs(self):
        for variant in (cap.DeviceVariant.V1, cap.DeviceVariant.R001,
                        cap.DeviceVariant.R002, cap.DeviceVariant.CONNECT3):
            for flags in product((False, True), repeat=4):
                choices = dict(zip(policy.COMMAND_FIELDS.values(), flags))
                hub = hub_for(variant, choices)
                controller = (qv.Connect3OutputController(hub) if variant in (
                    cap.DeviceVariant.R002, cap.DeviceVariant.CONNECT3) else legacy.DeviceController(hub))
                for (target, expected), enabled in zip(qv.OUTPUT_TARGETS.items(), flags):
                    with self.subTest(variant=variant, target=target, choices=flags):
                        state = controller.target_status(target)
                        self.assertEqual(state['enabled'], enabled)
                        self.assertEqual((state['channel'], state['output']), expected)
                hub.entry.data['second_channel_enabled'] = False
                hub.secondary_channel_enabled = False
                self.assertFalse(controller.target_enabled('strike_2'))
                self.assertFalse(controller.target_enabled('gate_2'))
                self.assertEqual(hub.entry.data['strike_2_enabled'], flags[2])
                self.assertEqual(hub.entry.data['gate_2_enabled'], flags[3])

    def test_legacy_disabled_primary_never_opens_a_session(self):
        for variant in (cap.DeviceVariant.V1, cap.DeviceVariant.R001):
            hub = hub_for(variant, {'strike_1_enabled': False, 'gate_1_enabled': False})
            controller = legacy.DeviceController(hub)
            with patch.object(legacy, 'Session') as opened:
                for output in (0, 1):
                    with self.assertRaisesRegex(legacy.ProtocolError, 'output_not_enabled'):
                        controller.unlock(output)
                opened.assert_not_called()
            self.assertEqual(controller.request_send_attempt_count, 0)


class QueuedPrimaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_qv_primary_preference_revoked_while_waiting_write_lock(self):
        choices = {'strike_1_enabled': True}
        session = session_module.QVSession('192.0.2.1', 8443, 'a' * 64,
            'x' * 32, 'SYNTHETIC', {}, output_authorizer=lambda current, channel, output:
                channel == 1 and policy.command_enabled(choices, 'strike_1' if output == 1 else 'gate_1'))
        session._material = protocol.CipherMaterial('x' * 32, 2, 1)
        writer = session._writer = SimpleNamespace(write=Mock(), drain=AsyncMock())
        session.observation['play_accepted'] = True
        await session._write_lock.acquire()
        task = asyncio.create_task(session.execute_output(1, 'SYNTHETIC_OPENING'))
        for _ in range(5):
            await asyncio.sleep(0)
        choices['strike_1_enabled'] = False
        session._write_lock.release()
        with self.assertRaises(qv.OutputFailure):
            await task
        writer.write.assert_not_called()
        writer.drain.assert_not_called()
        self.assertEqual(session.output_diagnostics()['request_send_attempt_count'], 0)

    async def test_disabling_primary_while_queued_prevents_worker_send(self):
        session = SimpleNamespace(closed=threading.Event(), info=SimpleNamespace(uid='synthetic'),
                                  send_packet=Mock(), write_lock=threading.RLock())
        controller = SimpleNamespace(closed=threading.Event(), entry=SimpleNamespace(
            unique_id='synthetic', data={'strike_1_enabled': True}),
            request_send_attempt_count=0, request_sent_count=0)
        output = v1.V1MediaOutput(controller)
        future = asyncio.get_running_loop().create_future()
        output.pending = v1.PendingOutput(session, b'SYNTHETIC-NOT-A-PHYSICAL-FRAME', future,
            time.monotonic() + 10, command_target='strike_1')
        controller.entry.data['strike_1_enabled'] = False
        output.send_pending(session)
        output.send_pending(session)
        await asyncio.sleep(0)
        self.assertIsInstance(future.exception(), v1.ProtocolError)
        session.send_packet.assert_not_called()
        self.assertEqual(controller.request_send_attempt_count, 0)

    async def test_v1_revocation_while_waiting_for_microphone_write_lock_never_attempts(self):
        class ObservedWriteLock:
            """Synthetic RLock; record only the worker's attempted acquisition."""
            def __init__(self):
                self.lock = threading.RLock()
                self.waiting = threading.Event()
            def __enter__(self):
                self.waiting.set()
                self.lock.acquire()
            def __exit__(self, *args):
                self.lock.release()

        for changed in ('preference', 'close'):
            write_lock = ObservedWriteLock()
            session = SimpleNamespace(closed=threading.Event(), info=SimpleNamespace(uid='synthetic'),
                send_packet=Mock(), write_lock=write_lock)
            controller = SimpleNamespace(closed=threading.Event(), entry=SimpleNamespace(
                unique_id='synthetic', data={'strike_1_enabled': True}),
                request_send_attempt_count=0, request_sent_count=0)
            output = v1.V1MediaOutput(controller)
            future = asyncio.get_running_loop().create_future()
            output.pending = v1.PendingOutput(session, b'SYNTHETIC-NOT-A-PHYSICAL-FRAME', future,
                time.monotonic() + 10, command_target='strike_1')
            write_lock.lock.acquire()
            worker = threading.Thread(target=output.send_pending, args=(session,))
            worker.start()
            try:
                self.assertTrue(await asyncio.to_thread(write_lock.waiting.wait, 2))
                if changed == 'preference':
                    controller.entry.data['strike_1_enabled'] = False
                else:
                    # Close must remain possible while the media worker waits
                    # for the microphone's write lock; it cannot deadlock.
                    output.close()
            finally:
                write_lock.lock.release()
            await asyncio.to_thread(worker.join, 2)
            self.assertFalse(worker.is_alive())
            await asyncio.sleep(0)
            self.assertIsInstance(future.exception(), v1.ProtocolError)
            session.send_packet.assert_not_called()
            self.assertEqual(controller.request_send_attempt_count, 0)
            self.assertEqual(controller.request_sent_count, 0)
            self.assertEqual(output.pending.packet, b'')


if __name__ == '__main__':
    unittest.main()
