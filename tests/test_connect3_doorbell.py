"""Synthetic events only: observing order23 is not a physical-ring validation."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from load_integration import load

d = load('connect3.doorbell')


def packet(order=23, data=b'\x01PRIVATE_IDENTIFIER'):
    header = bytearray(32)
    header[13] = order
    return SimpleNamespace(header=SimpleNamespace(command=254, plaintext=bytes(header)), parameters=data)


class DoorbellTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SimpleNamespace(_close_task=None)
        self.hub = SimpleNamespace(stopped=False, connected=True,
            live=SimpleNamespace(session=self.session), acquire=AsyncMock(), release=AsyncMock())
        self.observer = d.DoorbellObservation(self.hub)

    async def asyncTearDown(self):
        self.observer.finish()
        self.hub.acquire.assert_not_called()
        self.hub.release.assert_not_called()

    async def test_requires_live_and_never_opens_media(self):
        self.hub.connected = False
        with self.assertRaises(RuntimeError):
            self.observer.execute('start')
        self.assertEqual(self.observer.runs, 0)

    async def test_bounded_candidate_metadata_marker_and_no_payload(self):
        self.observer.execute('start', 90)
        self.observer.observe(self.session, packet())
        self.observer.execute('mark')
        self.observer.observe(self.session, packet(1, b'\x00'))
        self.observer.observe(self.session, packet(23, b''))
        result = self.observer.execute('stop')
        self.assertFalse(result['local_detection_implemented'])
        self.assertEqual(result['ring_events_emitted'], 0)
        obs = result['observation']
        self.assertEqual(obs['other_doorbell_call_candidates'], 1)
        self.assertEqual(obs['hangup_candidates'], 1)
        self.assertEqual(obs['malformed_candidates'], 1)
        self.assertEqual(len(obs['markers']), 1)
        self.assertEqual(obs['status'], 'finished')
        self.assertEqual(obs['end_reason'], 'user_stop')
        self.assertNotIn('PRIVATE_IDENTIFIER', json.dumps(result))
        result['observation']['events'].clear()
        self.assertEqual(len(self.observer.diagnostics()['observation']['events']), 3)

    async def test_deadline_no_sleep_and_no_late_events(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'call_later', wraps=loop.call_later) as schedule:
            self.observer.execute('start', 30)
            schedule.assert_called_once_with(30, self.observer.finish, 'deadline')
        self.observer._deadline = loop.time() - 1
        self.observer.observe(self.session, packet())
        self.assertEqual(self.observer.diagnostics()['observation']['end_reason'], 'deadline')
        self.assertEqual(self.observer.diagnostics()['observation']['control_messages'], 0)
        self.assertIsNone(self.observer._timer)
        with self.assertRaises(RuntimeError):
            self.observer.execute('mark')

    async def test_foreign_session_unload_and_repeat(self):
        self.observer.execute('start')
        self.observer.observe(object(), packet())
        self.assertEqual(self.observer.diagnostics()['observation']['control_messages'], 0)
        with self.assertRaises(RuntimeError):
            self.observer.execute('start')

        self.observer.media_closed(self.session)
        self.observer.observe(self.session, packet())
        self.assertEqual(self.observer.diagnostics()['observation']['end_reason'], 'media_closed')
        self.observer.execute('start')
        self.observer.finish()
        self.assertIsNone(self.observer._timer)
        self.assertIsNone(self.observer._session)
        self.assertEqual(self.observer.diagnostics()['runs'], 2)
        self.hub.stopped = True
        with self.assertRaises(RuntimeError):
            self.observer.execute('start')

    async def test_due_timer_cannot_accept_late_mark_before_callback(self):
        self.observer.execute('start')
        self.observer._deadline = asyncio.get_running_loop().time() - 1
        with self.assertRaises(RuntimeError):
            self.observer.execute('mark')
        self.assertEqual(self.observer.execute('status')['observation']['end_reason'], 'deadline')
        self.assertEqual(self.observer.diagnostics()['observation']['markers'], [])

    async def test_limits(self):
        for duration in (0, 121, True, 1.5):
            with self.assertRaises(ValueError):
                self.observer.execute('start', duration)
        self.observer.execute('start')
        for _ in range(130):
            self.observer.observe(self.session, packet())
        obs = self.observer.diagnostics()['observation']
        self.assertEqual(len(obs['events']), 128)
        self.assertEqual(obs['dropped_events'], 2)
        for _ in range(5):
            self.observer.execute('mark')
        with self.assertRaises(RuntimeError):
            self.observer.execute('mark')
