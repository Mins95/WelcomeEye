"""Synthetic LT selected-session microphone; no actual intercom traffic."""
import asyncio
import json
import math
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import av

from load_integration import load
import test_legacy_multichannel as fixture
import test_legacy_second_channel_flow as flow_fixture

client, talk, wire = load('client'), load('talkback'), load('protected')
OPTION = 'experimental_channel2_microphone'


class LegacyMicrophoneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = SimpleNamespace(entry_id='synthetic-entry', data={
            'host': '192.0.2.1', 'username': 'SYNTHETIC_USER', 'password': 'SYNTHETIC_PASSWORD',
            'device_variant': 'connect2_r001', 'channel': 16,
            'second_channel_enabled': True, OPTION: True})
        self.hub = fixture.Hub(SimpleNamespace(config_entries=SimpleNamespace(
            async_update_entry=Mock())), self.entry)
        self.hub.stopped = False
        self.secondary = self.hub.channel2
        self.sessions, self.packets, self.starts = [], [], []
        self.reply = True
        self.frame_ready = asyncio.Event()

        def worker(generation, stop):
            name, channel, stream, mode = self.hub._profile_order()[0]
            self.hub.current_profile = dict(name=name, channel=channel, stream=stream, mode=mode)
            session = client.Session('192.0.2.1', 'SYNTHETIC_USER', 'SYNTHETIC_PASSWORD', channel)
            session.info = SimpleNamespace(uid='SYNTHETIC_UID')
            session.encryption_profile = 0x01020301
            session.device_time, session.clock_received = 123, time.monotonic()
            session.connection_stage = 'authenticated'
            session.sock = SimpleNamespace(sendall=lambda data: self.packets.append(data),
                                           shutdown=lambda how: None)
            original = session.start_talk
            def start():
                original()
                self.starts.append(channel)
                if self.reply:
                    body = bytearray(20)
                    struct.pack_into('<H', body, 0, 1)
                    struct.pack_into('<I', body, 4, 8000)
                    struct.pack_into('<HH', body, 12, 31257, 1)
                    struct.pack_into('<H', body, 18, 16)
                    self.hub.talkback.observe(session, [(332, bytes(body))])
            session.start_talk = start
            self.sessions.append(session)
            self.hub.session = session
            for cb, args in ((self.hub._state, (True,)),
                    (self.hub._frame, ('video', 'SYNTHETIC_FRAME')),
                    (self.frame_ready.set, ())):
                self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, cb, *args)
            try:
                stop.wait()
            finally:
                self.hub.talkback.media_closed(session)
                session.closed.set()
        self.hub._worker = worker

    async def asyncTearDown(self):
        await self.hub.stop()
        await asyncio.sleep(0)
        self.assertFalse(self.hub.talkback.active)
        self.assertFalse(self.secondary.consumers)

    async def acquire(self):
        await self.secondary.acquire('viewer')
        await asyncio.wait_for(self.frame_ready.wait(), 2)

    def audio(self):
        frame = av.AudioFrame(format='s16', layout='mono', samples=320)
        frame.sample_rate = 8000
        frame.planes[0].update(struct.pack('<320h', *(int(3000 * math.sin(2 * math.pi * 440 * i / 8000))
            for i in range(320))))
        return frame

    async def test_actual_builders_use_same_video17_session_audio18_for_both_models(self):
        for variant in ('connect2_r001', 'connect_v1'):
            self.entry.data['device_variant'] = variant
            await self.acquire()
            session = self.hub.session
            owner = object()
            await self.secondary.talkback.start(owner)
            await self.secondary.talkback.feed(owner, self.audio())
            self.assertIs(self.hub.talkback.session, session)
            self.assertTrue(self.secondary.talkback.active)
            parts = list(wire.parse_tlvs(self.packets[-1][8:]))
            self.assertEqual([kind for kind, _ in parts], [97, 98])
            self.assertEqual(parts[0][1], struct.pack('<B3xI', 18, 0))
            self.assertEqual(len(parts[1][1]), 320)
            self.assertEqual(self.starts[-1], 17)
            self.assertEqual(self.secondary.talkback.diagnostics['wire_audio_channel'], 18)
            self.assertFalse(self.secondary.talkback.diagnostics['physically_verified'])
            self.assertNotIn('SYNTHETIC', json.dumps(self.secondary.diagnostics()))
            await self.secondary.talkback.stop(owner)
            await self.secondary.release('viewer')
            self.hub.talkback.last_stop = 0
        self.assertEqual(len(self.sessions), 2)

    async def test_option_main_source_and_unknown_variant_cannot_borrow_channel2(self):
        await self.acquire()
        before = len(self.packets)
        with self.assertRaises(talk.ProtocolError):
            await self.hub.talkback.start(object())
        for value in (False, 1, 'true', None):
            self.entry.data[OPTION] = value
            self.assertFalse(self.secondary.capabilities.talkback)
            with self.assertRaises(talk.ProtocolError):
                await self.secondary.talkback.start(object())
        self.entry.data[OPTION] = True
        self.entry.data['device_variant'] = 'legacy_unknown'
        self.assertFalse(self.secondary.capabilities.talkback)
        with self.assertRaises(talk.ProtocolError):
            await self.secondary.talkback.start(object())
        self.assertEqual(len(self.packets), before)

    async def test_context_revocation_never_sends_audio_to_wrong_session(self):
        for change in ('option', 'profile', 'credentials', 'session', 'channel'):
            await self.acquire()
            session = self.hub.session
            owner = object()
            await self.secondary.talkback.start(owner)
            saved = dict(self.entry.data)
            if change == 'option': self.entry.data[OPTION] = False
            if change == 'profile': self.entry.data['host'] = '192.0.2.2'
            if change == 'credentials': self.entry.data['password'] = 'SYNTHETIC_CHANGED'
            if change == 'session': self.hub.session = SimpleNamespace(closed=threading.Event())
            if change == 'channel': self.hub._active_media_channel = 1
            await self.secondary.talkback.feed(owner, self.audio())
            self.assertFalse(self.hub.talkback.active)
            self.assertIsNone(self.hub.talkback.owner)
            self.assertFalse(any(kind == 97 for kind, _ in wire.parse_tlvs(self.packets[-1][8:])))
            self.entry.data = saved
            self.hub.session = session
            self.hub._active_media_channel = 2
            await self.secondary.release('viewer')
            self.hub.talkback.last_stop = 0

    async def test_cancel_pending_start_drains_stop_and_no_orphan_owner(self):
        await self.acquire()
        self.reply = False
        task = asyncio.create_task(self.secondary.talkback.start(object()))
        async with asyncio.timeout(2):
            while not self.starts:
                await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNone(self.hub.talkback.owner)
        self.assertFalse(self.hub.session.talk_requested)
        self.assertFalse(self.hub.session.talk_enabled)

    async def test_write_lock_wait_rechecks_revoked_context_before_start(self):
        await self.acquire()
        session = self.hub.session
        waiting = threading.Event()
        original = self.hub.talkback._start_in_context
        def queued(*args):
            waiting.set()
            original(*args)
        self.hub.talkback._start_in_context = queued
        session.write_lock.acquire()
        task = asyncio.create_task(self.secondary.talkback.start(object()))
        try:
            self.assertTrue(await asyncio.to_thread(waiting.wait, 2))
            self.entry.data[OPTION] = False
        finally:
            session.write_lock.release()
        with self.assertRaises(talk.ProtocolError): await task
        self.assertFalse(self.starts)
        self.assertFalse(self.packets)
        self.assertIsNone(self.hub.talkback.owner)

    async def test_media_release_stops_talk_then_main_can_reopen(self):
        await self.acquire()
        await self.secondary.talkback.start(object())
        old_session = self.hub.session
        await self.secondary.release('viewer')
        await asyncio.sleep(0)
        self.assertTrue(old_session.closed.is_set())
        self.assertFalse(old_session.talk_requested)
        self.assertIsNone(self.hub.talkback.owner)
        self.assertFalse(self.secondary.talkback.active)
        await self.hub.acquire('main')
        self.assertEqual(self.hub.session.channel, 16)
        await self.hub.release('main')


class LegacyMicrophoneFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_choice_preserves_cloud_identity_outputs_and_reconfigure(self):
        for variant in ('connect2_r001', 'connect_v1'):
            instance, entry = flow_fixture.LegacySecondChannelFlowTests().configured(variant,
                second_channel_enabled=True)
            form = await instance.async_step_reconfigure()
            self.assertIn(OPTION, form['data_schema'])
            previous = dict(entry.data)
            result = await instance.async_step_reconfigure({OPTION: True})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertEqual(entry.data, {**previous, OPTION: True})
            await instance.async_step_reconfigure({})
            self.assertTrue(entry.data[OPTION])
            self.assertEqual(entry.unique_id, 'retained')
            for bad in (1, None, 'true'):
                result = await instance.async_step_reconfigure({OPTION: bad})
                self.assertEqual(result['type'], 'form')
                self.assertTrue(entry.data[OPTION])
            await instance.async_step_reconfigure({'second_channel_enabled': False})
            self.assertFalse(entry.data[OPTION])
            instance.hass.async_add_executor_job.assert_not_called()

    async def test_unknown_variant_never_gains_opt_in(self):
        instance, entry = flow_fixture.LegacySecondChannelFlowTests().configured('legacy_unknown',
            second_channel_enabled=True)
        form = await instance.async_step_reconfigure()
        self.assertNotIn(OPTION, form['data_schema'])
        result = await instance.async_step_reconfigure({OPTION: True})
        self.assertEqual(result['type'], 'form')
        self.assertNotIn(OPTION, entry.data)


if __name__ == '__main__':
    unittest.main()
