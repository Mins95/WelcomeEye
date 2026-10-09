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
        self.session2 = SimpleNamespace(_close_task=None)
        self.hub.channel2 = SimpleNamespace(stopped=False, connected=True,
            live=SimpleNamespace(session=self.session2), acquire=AsyncMock(), release=AsyncMock())
        self.observer = d.DoorbellObservation(self.hub)

    async def asyncTearDown(self):
        self.observer.finish()
        self.hub.acquire.assert_not_called()
        self.hub.release.assert_not_called()
        self.hub.channel2.acquire.assert_not_called()
        self.hub.channel2.release.assert_not_called()

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
        self.assertEqual(obs['channel'], 1)
        self.assertEqual([item['candidate_type'] for item in obs['events']],
                         ['other_doorbell_call', 'hangup', 'other_doorbell_call'])
        self.assertTrue(all(item['channel'] == 1 for item in obs['events']))
        self.assertFalse(result['physical_ring_confirmed'])
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
        for duration in (0, 29, 301, True, 1.5):
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

    async def test_channel_two_observes_only_its_existing_session_without_retargeting(self):
        self.observer.execute('start', channel=2)
        self.observer.observe(self.session, packet())
        self.observer.observe(self.session2, packet(data=b'\x02\xffPRIVATE_UTF8_TAIL'))
        for operation in ('mark', 'stop'):
            with self.assertRaises(ValueError):
                self.observer.execute(operation, channel=1)
        self.observer.execute('mark', channel=2)
        result = self.observer.execute('status')
        self.assertEqual(result['observation']['channel'], 2)
        self.assertEqual(result['observation']['control_messages'], 1)
        self.assertEqual(result['observation']['events'][0]['channel'], 2)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.observer.media_closed(self.session)
        self.assertEqual(self.observer.diagnostics()['observation']['status'], 'observing')
        self.observer.media_closed(self.session2)
        self.assertEqual(self.observer.diagnostics()['observation']['end_reason'], 'media_closed')

    async def test_five_minute_observation_then_other_channel_without_concurrency(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'call_later', wraps=loop.call_later) as schedule:
            self.observer.execute('start', 300, channel=1)
            schedule.assert_called_once_with(300, self.observer.finish, 'deadline')
        self.observer.observe(self.session, packet())
        with self.assertRaises(RuntimeError):
            self.observer.execute('start', 300, channel=2)
        first = self.observer.execute('stop', channel=1)
        self.assertEqual(first['observation']['channel'], 1)
        self.assertEqual(first['observation']['duration_seconds'], 300)

        self.observer.execute('start', 300, channel=2)
        self.observer.observe(self.session, packet())
        self.observer.observe(self.session2, packet())
        self.observer.execute('mark', channel=2)
        second = self.observer.execute('stop', channel=2)
        self.assertEqual(second['runs'], 2)
        self.assertEqual(second['observation']['channel'], 2)
        self.assertEqual(second['observation']['control_messages'], 1)
        self.assertEqual(len(second['observation']['markers']), 1)
        self.assertEqual(first['observation']['channel'], 1)
        self.assertEqual(first['ring_events_emitted'], 0)
        self.assertEqual(second['ring_events_emitted'], 0)

    async def test_channel_validation_and_secondary_unavailable_do_not_start(self):
        for value in (0, 3, True, '2', None):
            with self.assertRaises(ValueError):
                self.observer.execute('start', channel=value)
        self.hub.channel2.connected = False
        with self.assertRaises(RuntimeError):
            self.observer.execute('start', channel=2)
        self.hub.channel2.connected = True
        self.session2._close_task = object()
        with self.assertRaises(RuntimeError):
            self.observer.execute('start', channel=2)
        self.assertEqual(self.observer.runs, 0)

    async def test_changed_session_ends_observation_without_following_new_session(self):
        self.observer.execute('start', channel=2)
        self.hub.channel2.live.session = object()
        self.observer.observe(self.session2, packet())
        result = self.observer.diagnostics()['observation']
        self.assertEqual(result['end_reason'], 'media_closed')
        self.assertEqual(result['control_messages'], 0)
        self.assertIsNone(self.observer._session)
        with self.assertRaises(RuntimeError):
            self.observer.execute('mark', channel=2)

    async def test_markers_correlate_relative_times_without_interpreting_payload(self):
        self.observer.execute('start')
        with patch.object(self.observer, '_elapsed', return_value=100):
            self.observer.observe(self.session, packet())
        with patch.object(self.observer, '_elapsed', return_value=125):
            self.observer.execute('mark')
        with patch.object(self.observer, '_elapsed', return_value=200):
            self.observer.observe(self.session, packet(1, b'\xff'))
            report = self.observer.execute('stop')
        self.assertTrue(report['marker_correlation_only'])
        self.assertEqual(report['ring_events_emitted'], 0)
        events = report['observation']['events']
        self.assertEqual([event['marker_delta_ms'] for event in events], [-25, 75])
        self.assertEqual([event['nearest_marker'] for event in events], [1, 1])
        self.assertEqual([event['sequence'] for event in events], [1, 2])
        self.assertTrue(all(event['header_bytes'] == 32 for event in events))
        self.assertNotIn('PRIVATE', json.dumps(report))

    async def test_malformed_header_is_counted_without_index_error(self):
        self.observer.execute('start')
        malformed = packet()
        malformed.header.plaintext = b''
        self.observer.observe(self.session, malformed)
        result = self.observer.execute('stop')['observation']
        self.assertEqual(result['malformed_candidates'], 1)
        self.assertFalse(result['events'][0]['candidate_structure_valid'])

    async def test_candidate_equality_distinguishes_order_and_bytes_without_payload_export(self):
        self.observer.execute('start', channel=2)
        for candidate in (packet(), packet(), packet(1),
                          packet(data=b'\x02PRIVATE_IDENTIFIER'), packet(data=b'')):
            self.observer.observe(self.session2, candidate)
        report = self.observer.diagnostics()
        obs = report['observation']
        self.assertEqual(obs['control_messages'], 5)
        self.assertEqual(obs['compared_candidate_messages'], 4)
        self.assertEqual(obs['matching_candidate_messages'], 1)
        self.assertEqual(obs['events'][1]['same_candidate_as_sequence'], 1)
        self.assertTrue(all('same_candidate_as_sequence' not in obs['events'][i]
                            for i in (0, 2, 3, 4)))
        self.assertTrue(report['candidate_content_equality_is_not_ring_deduplication'])
        self.assertEqual(report['ring_events_emitted'], 0)
        serialized = json.dumps(report)
        self.assertNotIn('PRIVATE_IDENTIFIER', serialized)
        self.assertNotIn(self.observer._comparison_key.hex(), serialized)
        for digest in self.observer._candidate_fingerprints:
            self.assertNotIn(digest.hex(), serialized)

    async def test_order23_selector_is_raw_unsigned_byte_not_inferred_channel(self):
        self.observer.execute('start', channel=2)
        for value in (0, 1, 2, 127, 128, 255):
            self.observer.observe(self.session2,
                packet(data=bytes((value,)) + b'PRIVATE_CALLER'))
        self.observer.observe(self.session2, packet(1, b'\xffPRIVATE_CALLER'))
        self.observer.observe(self.session2, packet(23, b''))
        result = self.observer.execute('stop', channel=2)
        events = result['observation']['events']
        self.assertEqual([event['candidate_selector'] for event in events[:6]],
                         [0, 1, 2, 127, 128, 255])
        self.assertTrue(all(event['channel'] == 2 for event in events))
        self.assertTrue(all(event['candidate_selector_interpretation'] == 'unknown'
                            for event in events[:6]))
        self.assertNotIn('candidate_selector', events[6])
        self.assertNotIn('candidate_selector', events[7])
        self.assertNotIn('PRIVATE_CALLER', json.dumps(result))
        self.assertEqual(result['ring_events_emitted'], 0)
        self.assertFalse(result['physical_ring_confirmed'])

    async def test_candidate_comparison_state_is_bounded_and_cleared_on_stop(self):
        self.observer.execute('start')
        first_key = self.observer._comparison_key
        for index in range(130):
            self.observer.observe(self.session, packet(data=index.to_bytes(2, 'little')))
        self.assertEqual(len(self.observer._candidate_fingerprints), 128)
        obs = self.observer.execute('stop')['observation']
        self.assertEqual(obs['compared_candidate_messages'], 128)
        self.assertEqual(obs['matching_candidate_messages'], 0)
        self.assertEqual(obs['dropped_events'], 2)
        self.assertIsNone(self.observer._comparison_key)
        self.assertFalse(self.observer._candidate_fingerprints)

        self.observer.execute('start')
        self.assertNotEqual(self.observer._comparison_key, first_key)
        self.observer.observe(self.session, packet(data=b'\x00\x00'))
        self.assertNotIn('same_candidate_as_sequence',
                         self.observer.diagnostics()['observation']['events'][0])
        self.observer.media_closed(self.session)
        self.assertIsNone(self.observer._comparison_key)
        self.assertFalse(self.observer._candidate_fingerprints)

    async def test_comparison_state_discarded_on_deadline_and_unload(self):
        for reason in ('deadline', 'integration_unload'):
            self.observer.execute('start')
            self.observer.observe(self.session, packet())
            self.observer.finish(reason)
            self.assertIsNone(self.observer._comparison_key)
            self.assertFalse(self.observer._candidate_fingerprints)
            self.assertEqual(self.observer.diagnostics()['observation']['end_reason'], reason)
