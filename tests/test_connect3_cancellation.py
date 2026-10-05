"""Synthetic cancellation races: drain owned cleanup without detached readers."""
import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_connect3_media_protocol import KEY, aes, control_response, frame_bytes, media_response
from test_connect3_media_session import Writer, setup

session_module = load('connect3.session')
hub_module = load('connect3.hub')
live_module = load('connect3.live')
cgi_module = load('connect3.cgi')


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    def assert_no_media_tasks(self):
        unfinished = [task.get_name() for task in asyncio.all_tasks()
                      if not task.done() and task is not asyncio.current_task()
                      and task.get_name().startswith(('welcomeeye-qv-', 'welcomeeye-connect3-'))]
        self.assertEqual(unfinished, [])

    async def test_diagnostic_rejected_while_last_media_release_is_closing(self):
        hub = hub_module.Connect3Hub(SimpleNamespace(), SimpleNamespace(data={
            'host': '192.0.2.1', 'experimental_video': True}))
        await hub.start()
        closing, allow_close = asyncio.Event(), asyncio.Event()

        async def worker():
            try:
                await asyncio.Future()
            finally:
                closing.set()
                await allow_close.wait()

        hub.live.task = asyncio.create_task(worker(), name='welcomeeye-connect3-media')
        hub.live.consumers.add('viewer')
        await asyncio.sleep(0)
        release = asyncio.create_task(hub.release('viewer'))
        try:
            await asyncio.wait_for(closing.wait(), 1)
            self.assertFalse(hub.consumers)
            self.assertFalse(hub.live.task.done())
            with patch.object(hub, '_run', AsyncMock(return_value={})) as probe:
                with self.assertRaisesRegex(RuntimeError, 'unavailable or busy'):
                    await hub.execute('certificate')
                probe.assert_not_called()
        finally:
            allow_close.set()
            await release
            await hub.stop()
        self.assertIsNone(hub.live.task)
        self.assert_no_media_tasks()

    async def test_cancelled_hub_stop_finishes_listeners_then_media_once(self):
        hub = hub_module.Connect3Hub(SimpleNamespace(), SimpleNamespace(data={
            'host': '192.0.2.1', 'experimental_video': True}))
        await hub.start()
        listener_entered, allow_listener, worker_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        listener_calls = []

        async def close_listener():
            listener_calls.append(True)
            listener_entered.set()
            await allow_listener.wait()

        async def worker():
            try:
                await asyncio.Future()
            finally:
                worker_closed.set()

        hub.live.task = asyncio.create_task(worker(), name='welcomeeye-connect3-media')
        hub.live.consumers.add('viewer')
        hub.close_listeners.add(close_listener)
        await asyncio.sleep(0)
        stop = asyncio.create_task(hub.stop())
        second_stop = None
        try:
            await asyncio.wait_for(listener_entered.wait(), 1)
            stop.cancel()
            await asyncio.sleep(0)
            stop.cancel()
            await asyncio.sleep(0)
            self.assertFalse(stop.done())
            self.assertFalse(worker_closed.is_set())
            second_stop = asyncio.create_task(hub.stop())
            await asyncio.sleep(0)
            self.assertEqual(listener_calls, [True])
        finally:
            allow_listener.set()
            outcomes = await asyncio.gather(stop, return_exceptions=True)
            if second_stop is not None:
                await second_stop
            await hub.stop()
        self.assertIsInstance(outcomes[0], asyncio.CancelledError)
        self.assertTrue(hub._stop_task.done())
        self.assertTrue(worker_closed.is_set())
        self.assertEqual(listener_calls, [True])
        self.assertFalse(hub.consumers)
        self.assertIsNone(hub.live.task)
        self.assertFalse(hub.close_listeners)
        self.assert_no_media_tasks()

    async def test_repeat_cancel_waits_for_single_teardown_and_tcp_close(self):
        reader, writer, observation = asyncio.StreamReader(), Writer(), {}
        teardown_started, allow_teardown = asyncio.Event(), asyncio.Event()
        got_frame = asyncio.Event()

        async def drain():
            if len(writer.writes) > 1 and aes(writer.writes[-1][:32], decrypt=True)[0] == 7:
                teardown_started.set()
                await allow_teardown.wait()

        async def on_frame(frame):
            got_frame.set()

        writer.drain = AsyncMock(side_effect=drain)
        session = session_module.QVSession('192.0.2.1', 8443, 'a' * 64,
                                          KEY, 'SYNTHETIC_PASSWORD', observation)
        reader.feed_data(setup() + b''.join(control_response())
                         + b''.join(media_response(frame_bytes())))
        with patch.object(session_module, 'open_media_tls',
                          AsyncMock(return_value=(reader, writer))):
            task = asyncio.create_task(session.run(on_frame))
            try:
                await asyncio.wait_for(got_frame.wait(), 1)
                task.cancel()
                await asyncio.wait_for(teardown_started.wait(), 1)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertFalse(writer.closed)
            finally:
                allow_teardown.set()
                outcome = await asyncio.gather(task, return_exceptions=True)
                await session.close()
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        commands = [data[0] if index == 0 else aes(data[:32], decrypt=True)[0]
                    for index, data in enumerate(writer.writes)]
        self.assertEqual(commands, [0xA9, 1, 7])
        self.assertEqual(writer.close_count, 1)
        self.assertTrue(session._close_task.done())
        self.assertIsNone(session._read_task)
        self.assertIsNone(session._stream_key)
        self.assertIsNone(session._password)
        self.assert_no_media_tasks()

    async def test_repeat_cancel_drains_decode_thread_before_decoder_close(self):
        entered = threading.Event()
        finish = threading.Event()
        decode_finished = threading.Event()
        closed = threading.Event()
        closed_while_decoding = []
        sessions = []

        class Decoder:
            errors = 0

            def feed(self, packet):
                entered.set()
                if not finish.wait(2):
                    raise RuntimeError('synthetic test did not release decoder')
                decode_finished.set()
                return [SimpleNamespace(pts=1)], b'SYNTHETIC_JPEG'

            def close(self):
                closed_while_decoding.append(not decode_finished.is_set())
                closed.set()

        class Session:
            def __init__(self, *args):
                sessions.append(self)
                self.closed = False
                self.observation = args[-1]

            async def run(self, callback):
                await callback(SimpleNamespace(frame_type=1))
                await asyncio.Future()

            async def close(self):
                self.closed = True
                self.observation['tcp_closed'] = True

        async def read(*args, diagnostics=None, **kwargs):
            diagnostics['authentication_status'] = 'accepted'
            return cgi_module.StreamMaterial('SYNTHETIC_STREAM_KEY_OF_SUFFICIENT_LENGTH')

        hub = hub_module.Connect3Hub(SimpleNamespace(), SimpleNamespace(data={
            'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_PASSWORD',
            'experimental_video': True, 'certificate_sha256': 'a' * 64}))
        callback = Mock()
        hub.frame_listeners.add(callback)
        with patch.object(live_module, 'QVSession', Session), \
                patch.object(live_module, 'VideoDecoder', Decoder), \
                patch.object(live_module, 'read_stream_material', AsyncMock(side_effect=read)):
            await hub.start()
            task = asyncio.create_task(hub.acquire('viewer'))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                worker = hub.live.task
                task.cancel()
                # Let acquisition cleanup request cancellation of the worker.
                for _ in range(6):
                    await asyncio.sleep(0)
                task.cancel()
                worker.cancel()
                for _ in range(3):
                    await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertFalse(worker.done())
                self.assertFalse(closed.is_set())
            finally:
                finish.set()
                outcome = await asyncio.gather(task, return_exceptions=True)
                await hub.stop()
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertTrue(decode_finished.is_set())
        self.assertTrue(closed.is_set())
        self.assertEqual(closed_while_decoding, [False])
        self.assertTrue(all(session.closed for session in sessions))
        callback.assert_not_called()
        self.assertFalse(hub.consumers)
        self.assertIsNone(hub.live.task)
        self.assert_no_media_tasks()


if __name__ == '__main__':
    unittest.main()
