"""Bounded observational microphone/video traces; no network or equipment."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from load_integration import load
import test_connect3_live as live_fixture
import test_connect3_talk as talk_fixture

live_module = load('connect3.live')
talk_module = load('connect3.talk')


class ObservedLive:
    def __init__(self):
        self.session = object()
        self.connected = True
        self.calls = 0
        self.values = dict(available=True, same_session=True, session_count=7,
            connected=True, video_packets_received=100, video_frames_decoded=80,
            last_video_packet_age_ms=10, last_video_frame_age_ms=20,
            private='PRIVATE_SESSION_SECRET')

    def continuity_snapshot(self, session):
        assert session is self.session
        self.calls += 1
        return dict(self.values)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.now = 1234567.0
        self.live = ObservedLive()
        self.hub = SimpleNamespace(live=self.live)
        self.trace = talk_module._VideoContinuityTrace(self.hub, self.live.session, 1,
                                                      clock=lambda: self.now)

    def sample(self, seconds=1.0, **values):
        self.now += seconds
        self.live.values.update(values)
        self.trace.point('sample')
        return self.trace.diagnostics()

    def test_progress_frozen_decode_and_absent_reception_are_distinguishable(self):
        progressing = self.sample(video_packets_received=105, video_frames_decoded=84)
        self.assertEqual((progressing['video_packets_delta'], progressing['video_frames_delta']), (5, 4))
        frozen = self.sample(video_packets_received=110, video_frames_decoded=84,
                             last_video_packet_age_ms=10, last_video_frame_age_ms=1020)
        point = frozen['checkpoints'][-1]
        self.assertEqual((point['video_packets_delta'], point['video_frames_delta']), (5, 0))
        self.assertEqual(point['last_video_frame_age_ms'], 1020)
        absent = self.sample(last_video_packet_age_ms=1010, last_video_frame_age_ms=2020)
        point = absent['checkpoints'][-1]
        self.assertEqual((point['video_packets_delta'], point['video_frames_delta']), (0, 0))
        self.assertEqual(absent['max_video_packet_age_ms'], 1010)
        self.assertEqual(absent['max_video_frame_age_ms'], 2020)
        self.assertEqual(absent['max_observation_gap_ms'], 1000)
        # These are observations, never automatic stall/error decisions.
        self.assertNotIn('error', absent)
        self.assertNotIn('stall', absent)

    def test_session_replacement_never_mixes_counters_even_if_old_session_returns(self):
        self.sample(video_packets_received=103, video_frames_decoded=82)
        old_session = self.live.session
        calls = self.live.calls
        self.live.session = object()
        changed = self.sample(video_packets_received=999999, video_frames_decoded=888888)
        self.assertTrue(changed['session_changed'])
        self.assertFalse(changed['checkpoints'][-1]['available'])
        self.assertEqual((changed['video_packets_delta'], changed['video_frames_delta']), (3, 2))
        self.assertEqual(self.live.calls, calls)
        self.live.session = old_session
        self.sample()
        self.assertEqual(self.live.calls, calls)
        self.assertNotIn('999999', json.dumps(self.trace.diagnostics()))
        self.assertNotIn('888888', json.dumps(self.trace.diagnostics()))

    def test_counter_generation_change_is_not_aggregated(self):
        changed = self.sample(session_count=8, video_packets_received=2, video_frames_decoded=1)
        self.assertTrue(changed['session_changed'])
        self.assertEqual(changed['video_packets_delta'], 0)

    def test_one_sample_per_second_preserves_all_milestones_and_bounds(self):
        for _ in range(9):
            self.sample(.1)
        self.assertEqual(self.live.calls, 1)
        self.sample(.2)
        self.assertEqual(self.live.calls, 2)
        for _ in range(40):
            self.sample()
        self.trace.point('first_audio_sent')
        milestone = self.trace.diagnostics()['checkpoints'][-1]
        for _ in range(100):
            self.sample()
            self.trace.point('first_audio_sent')  # A repeated marker is ignored.
        self.trace.point('finished')
        diagnostic = self.trace.diagnostics()
        self.assertEqual(len(diagnostic['checkpoints']), 24)
        self.assertEqual(diagnostic['checkpoints'][0]['stage'], 'before_open')
        self.assertIn(milestone, diagnostic['checkpoints'])
        self.assertEqual(diagnostic['checkpoints'][-1]['stage'], 'finished')
        self.assertEqual(diagnostic['observations'] - 24, diagnostic['checkpoints_dropped'])
        self.assertIsNone(self.trace._session)
        self.assertIsNone(self.trace._live)
        calls = self.live.calls
        self.sample()
        self.trace.point('finished')
        self.assertEqual(self.live.calls, calls)

    def test_late_baseline_is_explicit_and_retained(self):
        self.live.values['available'] = False
        trace = talk_module._VideoContinuityTrace(self.hub, self.live.session, 2,
                                                 clock=lambda: self.now)
        self.assertFalse(trace.diagnostics()['available'])
        self.now += 2
        self.live.values['available'] = True
        trace.point('sample')
        self.assertEqual(trace.diagnostics()['baseline_elapsed_ms'], 2000)
        self.assertEqual(trace.diagnostics()['video_packets_delta'], 0)
        for _ in range(40):
            self.now += 1
            trace.point('sample')
        baseline = [point for point in trace.diagnostics()['checkpoints'] if point.get('baseline')]
        self.assertEqual(len(baseline), 1)
        self.assertEqual(baseline[0]['elapsed_ms'], 2000)

    def test_optional_missing_failed_or_malformed_snapshots_are_private(self):
        for callback in (None, lambda _session: {'private': 'PRIVATE_PAYLOAD'},
                         lambda _session: {**self.live.values, 'connected': 'PRIVATE_VALUE'},
                         lambda _session: {**self.live.values, 'last_video_frame_age_ms': True}):
            source = SimpleNamespace(session=object(), continuity_snapshot=callback)
            trace = talk_module._VideoContinuityTrace(SimpleNamespace(live=source), source.session, 1)
            self.assertFalse(trace.diagnostics()['available'])
            trace.point('finished')
            self.assertNotIn('PRIVATE', json.dumps(trace.diagnostics()))
        def failed(_session):
            raise RuntimeError('PRIVATE_EXCEPTION')
        self.live.continuity_snapshot = failed
        self.sample()
        self.assertNotIn('PRIVATE', json.dumps(self.trace.diagnostics()))
        report = self.trace.diagnostics()
        report['checkpoints'][0]['private'] = 'PRIVATE_MUTATION'
        self.assertNotIn('PRIVATE', json.dumps(self.trace.diagnostics()))
        self.assertNotIn('1234567', json.dumps(self.trace.diagnostics()))


class LiveContinuityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = live_fixture.LiveTests.asyncSetUp
    asyncTearDown = live_fixture.LiveTests.asyncTearDown

    async def test_counts_only_video_and_counts_reception_before_successful_decode(self):
        class Decoder(live_fixture.Decoder):
            def feed(self, packet):
                return ([], None) if packet.empty else super().feed(packet)
        class AudioDecoder:
            diagnostics = {}
            def feed(self, packet):
                return []
            def close(self):
                pass
        self.frames = [SimpleNamespace(is_audio=True),
                       SimpleNamespace(frame_type=1, empty=False),
                       SimpleNamespace(frame_type=1, empty=True),
                       SimpleNamespace(frame_type=99)]
        self.produce.set()
        with patch.object(live_module, 'VideoDecoder', Decoder), \
                patch.object(live_module, 'AudioDecoder', AudioDecoder):
            for count in (1, 2):
                await self.hub.acquire('viewer')
                async with asyncio.timeout(1):
                    while self.hub.live.observation['ignored_nonvideo_frames'] != 1:
                        await asyncio.sleep(0)
                session = self.hub.live.session
                snapshot = self.hub.live.continuity_snapshot(session)
                self.assertEqual(snapshot['session_count'], count)
                self.assertEqual(snapshot['video_packets_received'], 2)
                self.assertEqual(snapshot['video_frames_decoded'], 1)
                self.assertIsInstance(snapshot['last_video_packet_age_ms'], int)
                self.assertIsInstance(snapshot['last_video_frame_age_ms'], int)
                self.assertTrue(all(type(value) in (int, bool) for value in snapshot.values()))
                self.assertEqual(self.hub.live.observation['video_packets_received'], 2)
                self.assertFalse(self.hub.live.continuity_snapshot(object())['available'])
                await self.hub.release('viewer')
                self.assertFalse(self.hub.live.continuity_snapshot(session)['available'])
        self.assertEqual(len(self.sessions), 2)


class TalkContinuityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = talk_fixture.TalkTests.asyncSetUp
    asyncTearDown = talk_fixture.TalkTests.asyncTearDown
    start = talk_fixture.TalkTests.start
    wire = talk_fixture.TalkTests.wire
    commands = talk_fixture.TalkTests.commands

    def observe(self):
        observed = ObservedLive()
        observed.session = self.live.session
        self.live.continuity_snapshot = observed.continuity_snapshot
        return observed

    async def test_checkpoints_keep_wire_sequence_and_close_unchanged(self):
        observed = self.observe()
        await self.start()
        stages = [point['stage'] for point in self.talk.diagnostics['video_continuity']['checkpoints']]
        self.assertEqual(stages, ['before_open', 'socket_opened', 'setup_accepted',
            'open_accepted', 'transmit_accepted', 'receive_accepted', 'receive_disable_sent'])
        self.reader.feed_data(b''.join(talk_fixture.control_response(command=13)))
        async with asyncio.timeout(1):
            while not self.talk.diagnostics['receive_disable_accepted']:
                await asyncio.sleep(0)
        observed.values.update(video_packets_received=103, video_frames_decoded=82)
        await self.talk.feed('viewer', talk_fixture.pcm())
        await self.talk.stop('viewer')
        trace = self.talk.diagnostics['video_continuity']
        self.assertEqual((trace['video_packets_delta'], trace['video_frames_delta']), (3, 2))
        self.assertTrue(trace['finished'])
        stages = [point['stage'] for point in trace['checkpoints']]
        self.assertIn('receive_disable_accepted', stages)
        self.assertIn('first_audio_sent', stages)
        self.assertEqual(stages[-1], 'finished')
        self.assertEqual(self.commands(), [169, 11, 12, 13, 13, 162, 7])
        self.connect.assert_awaited_once()
        self.assertEqual(self.writer.close_count, 1)
        self.assertTrue(self.live.connected)
        self.assertIsNone(self.talk.diagnostics['last_error_type'])
        self.assertNotIn('PRIVATE', json.dumps(self.talk.diagnostics))

    async def test_feed_samples_freeze_without_changing_sending_and_retains_three_attempts(self):
        observed = self.observe()
        for attempt in range(1, 6):
            if attempt > 1:
                self.reader, self.writer = asyncio.StreamReader(), talk_fixture.Writer()
                self.connect.return_value = self.reader, self.writer
            await self.start()
            trace = self.talk._video_trace
            clock = [trace._started]
            trace._clock = lambda: clock[0]
            for index in range(4):
                clock[0] += 1.1
                observed.values['video_packets_received'] += 2
                observed.values['last_video_frame_age_ms'] = (index + 1) * 1100
                await self.talk.feed('viewer', talk_fixture.pcm())
            await self.talk.stop('viewer')
            diagnostic = self.talk.diagnostics
            current = diagnostic['video_continuity']
            self.assertEqual(current['attempt'], attempt)
            self.assertEqual(current['video_packets_delta'], 8)
            self.assertEqual(current['video_frames_delta'], 0)
            self.assertEqual(current['max_video_frame_age_ms'], 4400)
            self.assertEqual(diagnostic['frames_sent'], 4)
            self.assertIsNone(diagnostic['last_error_type'])
            self.assertEqual(self.writer.close_count, 1)
        previous = self.talk.diagnostics['previous_video_continuity']
        self.assertEqual([item['attempt'] for item in previous], [2, 3, 4])
        self.assertTrue(all(item['max_video_frame_age_ms'] == 4400 for item in previous))
        self.assertTrue(all('checkpoints' not in item for item in previous))
        previous[0]['attempt'] = 999
        self.assertEqual(self.talk.diagnostics['previous_video_continuity'][0]['attempt'], 2)

    async def test_absent_snapshot_remains_optional_and_negotiation_failure_is_preserved(self):
        self.reader.feed_data(talk_fixture.setup() + talk_fixture.open_response(mask=4))
        with self.assertRaisesRegex(talk_module.TalkError, 'unsupported_talk_codec'):
            await self.talk.start('viewer')
        diagnostic = self.talk.diagnostics
        self.assertFalse(diagnostic['video_continuity']['available'])
        self.assertTrue(diagnostic['video_continuity']['finished'])
        self.assertEqual(self.commands(), [169, 11, 7])
        self.assertEqual(diagnostic['last_error_reason'], 'unsupported_talk_codec')

    async def test_session_change_at_feed_is_recorded_before_existing_cleanup(self):
        observed = self.observe()
        await self.start()
        calls = observed.calls
        self.live.session = object()
        observed.values.update(video_packets_received=999999, video_frames_decoded=888888)
        await self.talk.feed('viewer', talk_fixture.pcm())
        diagnostic = self.talk.diagnostics
        self.assertTrue(diagnostic['video_continuity']['session_changed'])
        self.assertTrue(diagnostic['video_continuity']['finished'])
        self.assertEqual(diagnostic['video_continuity']['video_packets_delta'], 0)
        self.assertEqual(observed.calls, calls)
        self.assertEqual(diagnostic['frames_sent'], 0)
        self.assertEqual(diagnostic['last_error_reason'], 'microphone_heartbeat_or_media_lost')
        self.assertEqual(self.commands(), [169, 11, 12, 13, 13, 7])
        self.assertNotIn('999999', json.dumps(diagnostic))


if __name__ == '__main__':
    unittest.main()
