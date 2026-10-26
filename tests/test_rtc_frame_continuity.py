"""DeviceTrack queue continuity and cleanup, without HA or network I/O.

Only the aiortc lifecycle base is replaced when aiortc is not installed in the
offline test matrix. PyAV frames and production queue / manager code are real.
The separate native browser harness exercises the real aiortc RTP sender.
"""
import ast
import asyncio
from fractions import Fraction
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

import av

try:
    from aiortc import MediaStreamTrack
    from aiortc.mediastreams import MediaStreamError
except ImportError:
    class MediaStreamTrack:
        readyState = 'live'

        def stop(self):
            self.readyState = 'ended'

    class MediaStreamError(Exception):
        pass


SOURCE = Path(os.environ.get('WELCOMEEYE_RTC_TEST_SOURCE') or
              Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local/rtc.py')


def load_tracks(wait_for=asyncio.wait_for):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    track = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DeviceTrack')
    manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'WebRTCManager')
    manager.body = [n for n in manager.body if isinstance(n, ast.FunctionDef) and n.name == '_frame']
    namespace = dict(av=av, MediaStreamTrack=MediaStreamTrack, MediaStreamError=MediaStreamError,
                     asyncio=SimpleNamespace(Queue=asyncio.Queue, wait_for=wait_for))
    exec(compile(ast.Module(body=[track, manager], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['DeviceTrack'], namespace['WebRTCManager']


def video_frame(pts=9000):
    frame = av.VideoFrame(64, 48, 'yuv420p')
    for plane in frame.planes:
        plane.update(bytes([64]) * plane.buffer_size)
    frame.pts, frame.time_base = pts, Fraction(1, 90000)
    return frame


class RTCFrameContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_recovers_on_same_track_after_repeated_empty_waits(self):
        waits = []
        entered = asyncio.Event()

        async def short_wait(awaitable, timeout):
            waits.append(timeout)
            if len(waits) >= 3:
                entered.set()
            return await asyncio.wait_for(awaitable, .01)

        Track, _ = load_tracks(short_wait)
        track = Track('video')
        receiving = asyncio.create_task(track.recv())
        try:
            # Before the fix, the first timeout raises MediaStreamError and
            # aiortc permanently stops this track instead of waiting again.
            done, _ = await asyncio.wait({receiving, waiter := asyncio.create_task(entered.wait())},
                                         timeout=.5, return_when=asyncio.FIRST_COMPLETED)
            if receiving in done:
                await receiving
            self.assertTrue(entered.is_set())
            self.assertEqual(track.readyState, 'live')
            self.assertTrue(track.feed(video_frame()))
            result = await asyncio.wait_for(receiving, .5)
            self.assertEqual(result.pts, 9000)
            self.assertEqual(result.time_base, Fraction(1, 90000))
            self.assertEqual(result.to_ndarray().shape, (72, 64))
            self.assertTrue(all(timeout == 15 for timeout in waits))
        finally:
            track.stop()
            receiving.cancel()
            waiter.cancel()
            await asyncio.gather(receiving, waiter, return_exceptions=True)

    async def test_stop_wakes_empty_receive_without_waiting_for_timeout(self):
        entered = asyncio.Event()

        async def observe_wait(awaitable, timeout):
            entered.set()
            return await asyncio.wait_for(awaitable, timeout)

        Track, _ = load_tracks(observe_wait)
        track = Track('video')
        receiving = asyncio.create_task(track.recv())
        await entered.wait()
        track.stop()
        with self.assertRaises(MediaStreamError):
            await asyncio.wait_for(receiving, .2)
        self.assertEqual(track.readyState, 'ended')
        self.assertFalse(track.feed(video_frame()))

    async def test_cancellation_propagates_and_does_not_leave_a_queue_waiter(self):
        entered = asyncio.Event()

        async def observe_wait(awaitable, timeout):
            entered.set()
            return await asyncio.wait_for(awaitable, timeout)

        Track, _ = load_tracks(observe_wait)
        track = Track('video')
        receiving = asyncio.create_task(track.recv())
        await entered.wait()
        receiving.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await receiving
        self.assertFalse(track.queue._getters)
        self.assertTrue(track.feed(video_frame(18000)))
        self.assertEqual((await track.recv()).pts, 18000)
        track.stop()

    async def test_bounded_queue_drops_old_frames_and_stop_preserves_sentinel(self):
        Track, _ = load_tracks()
        track = Track('video')
        for pts in range(5):
            self.assertTrue(track.feed(video_frame(pts)))
        self.assertEqual(track.queue.qsize(), 2)
        self.assertEqual((await track.recv()).pts, 3)
        track.stop()
        self.assertFalse(track.feed(video_frame()))
        self.assertEqual(track.queue.qsize(), 1)
        self.assertIsNone(track.queue.get_nowait())
        with self.assertRaises(MediaStreamError):
            await track.recv()

    async def test_downstream_counter_counts_only_frames_accepted_by_live_tracks(self):
        Track, Manager = load_tracks()
        active, ended = Track('video'), Track('video')
        ended.stop()
        manager = Manager()
        manager.hub = SimpleNamespace(webrtc_diagnostics={})
        manager.viewers = {'active':SimpleNamespace(tracks={'video':active}),
                           'ended':SimpleNamespace(tracks={'video':ended})}
        manager._frame('video', video_frame())
        self.assertEqual(manager.hub.webrtc_diagnostics['downstream_frames_queued'], {'video':1})
        active.stop()
        manager._frame('video', video_frame())
        self.assertEqual(manager.hub.webrtc_diagnostics['downstream_frames_queued'], {'video':1})


if __name__ == '__main__':
    unittest.main()
