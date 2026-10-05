"""Synthetic UDT service coordination; no device or external network access."""
import asyncio
import json
import threading
import unittest
from unittest.mock import patch

from test_experimental_diagnostics import experimental, hub, user, cap


class UdtBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hub = hub()
        self.backend = experimental.ExperimentalDiagnostics(self.hub)
        self.kwargs = dict(confirm=True, legacy_discovery_absent=True,
                           udp_port=12345, user=user(), entity_id='camera.synthetic')

    async def execute(self, **overrides):
        return await self.backend.execute('udt_handshake', **(self.kwargs | overrides))

    async def test_requires_admin_control_legacy_missing_discovery_and_explicit_endpoint(self):
        with patch.object(self.backend, '_run_udt') as worker:
            for identity in (None, user(admin=False), user(control=False)):
                with self.assertRaises(PermissionError):
                    await self.execute(user=identity)
            self.assertEqual((await self.execute(confirm=False))['reason'],
                             'explicit_confirmation_required')
            self.assertEqual((await self.execute(legacy_discovery_absent=False))['reason'],
                             'legacy_discovery_absence_required')
            for port in (None, 0, -1, 65536, True, '12345'):
                self.assertEqual((await self.execute(udp_port=port))['reason'],
                                 'established_udp_endpoint_required')
            for family in (cap.ProtocolFamily.CONNECT3, cap.ProtocolFamily.R002):
                self.hub.protocol_family = family
                self.assertEqual((await self.execute())['reason'], 'legacy_protocol_required')
            worker.assert_not_called()
            self.assertEqual(self.backend._attempted, set())

    async def test_busy_never_disrupts_media_and_one_attempt_does_not_retain_endpoint_or_report(self):
        before = json.dumps(vars(self.hub.entry))
        with patch.object(self.backend, '_run_udt', return_value={
                'status': 'observed', 'handshake_accepted': True}) as worker:
            self.hub.ring_listener.enabled = True
            self.assertEqual((await self.execute())['reason'], 'busy')
            self.hub.ring_listener.enabled = False
            self.hub.consumers.add('existing-video')
            self.assertEqual((await self.execute())['reason'], 'busy')
            self.hub.consumers.clear()
            result = await self.execute()
            self.assertTrue(result['handshake_accepted'])
            self.assertEqual((await self.execute(udp_port=12346))['reason'],
                             'already_attempted_for_loaded_entry')
            worker.assert_called_once()
        self.assertEqual(before, json.dumps(vars(self.hub.entry)))
        self.assertIsNone(self.backend._task)
        self.assertIsNone(self.backend._session)
        self.assertNotIn('12345', repr(vars(self.backend)))
        self.assertNotIn('handshake_accepted', repr(vars(self.backend)))

    async def test_repeated_cancel_holds_locks_until_worker_closes_and_prevents_new_work(self):
        started, closing, finish = threading.Event(), threading.Event(), threading.Event()

        def worker(port, report):
            started.set()
            self.backend._cancel_requested.wait(3)
            closing.set()
            finish.wait(3)
            return report

        with patch.object(self.backend, '_run_udt', side_effect=worker):
            task = asyncio.create_task(self.execute())
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            self.assertTrue(await asyncio.to_thread(closing.wait, 2))
            task.cancel()
            await asyncio.sleep(0)
            self.assertTrue(self.hub.lock.locked())
            self.assertTrue(self.hub.control.lock.locked())
            stopped = asyncio.create_task(self.backend.stop())
            await asyncio.sleep(0)
            self.assertFalse(stopped.done())
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await stopped
            self.assertFalse(self.hub.lock.locked())
            self.assertFalse(self.hub.control.lock.locked())
            self.assertIsNone(self.backend._task)
            self.assertEqual((await self.execute())['reason'], 'entry_stopped')

    async def test_cancel_before_thread_start_does_not_send(self):
        self.backend._cancel_requested.set()
        # The early exit is tested without importing or constructing a socket.
        from load_integration import load
        udt = load('experimental_udt')
        with patch.object(udt, 'probe_handshake', side_effect=AssertionError('No I/O')) as probe:
            result = self.backend._run_udt(12345, {})
        probe.assert_not_called()
        self.assertEqual(result['error_type'], 'ConnectionAbortedError')


if __name__ == '__main__':
    unittest.main()
