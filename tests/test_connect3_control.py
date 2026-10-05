"""SYNTHETIC opening frames and shared-session races; no physical device.

Expected wire bytes are encoded independently with struct/hashlib/AES, not
recorded hardware responses. These tests do not prove physical activation.
"""
import asyncio
from hashlib import sha256
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from load_integration import load
from test_connect3_media_protocol import KEY, MATERIAL, aes, control_response
from test_connect3_media_session import Writer, setup

c = load('connect3.control')
p = load('connect3.protocol')
s = load('connect3.session')
CODE = '123456'  # Explicit synthetic opening code, not a device credential.


def unlock_reply(parameters=b'\0\0', *, order=4, bad_sha=False):
    header = bytearray(32)
    header[0], header[13] = 0xFE, order
    size = (len(parameters) + 32 + 15) // 16 * 16
    struct.pack_into('<HH', header, 9, size, len(parameters))
    digest = bytes(32) if bad_sha else sha256(bytes(header) + parameters).digest()
    body = parameters + digest + bytes(size - len(parameters) - 32)
    return aes(bytes(header)) + aes(body)


def packet_from_wire(wire):
    return p.decode_packet(p.decode_packet_header(wire[:32], MATERIAL), wire[32:], MATERIAL)


class OpeningProtocolTests(unittest.TestCase):
    def test_independent_native_layout_and_distinct_code(self):
        for output in (1, 2):
            wire = c.build_unlock_request(MATERIAL, channel=1, output=output,
                opening_code=CODE, timestamp_seconds=1700000000)
            header, body = aes(wire[:32], decrypt=True), aes(wire[32:], decrypt=True)
            params = bytes((output, 0, 1, 1)) + bytes(12) + sha256(CODE.encode()).hexdigest().encode()
            expected = bytearray(32)
            expected[0], expected[13] = 0xFE, 4
            struct.pack_into('<Q', expected, 1, 1700000000)
            struct.pack_into('<HH', expected, 9, 112, 80)
            self.assertEqual(header, bytes(expected))
            self.assertEqual(body, params + sha256(header + params).digest())
            self.assertEqual(len(wire), 144)
            self.assertNotIn(CODE.encode(), body)

    def test_same_native_modes_and_explicit_long_code_passthrough(self):
        code = 'SYNTHETIC_' * 8
        for mode in (0, 1, 2):
            for sha_mode in (0, 1):
                material = p.CipherMaterial(KEY, mode, sha_mode)
                wire = c.build_unlock_request(material, channel=2, output=2,
                    opening_code=code, timestamp_seconds=1)
                header = p.decode_packet_header(wire[:32], material)
                packet = p.decode_packet(header, wire[32:], material)
                self.assertEqual(packet.parameters, b'\2\0\2\1' + bytes(12) + code.encode())
                self.assertEqual(header.plaintext[13], 4)

    def test_only_verified_order_and_native_return_mapping(self):
        for parameters, result in ((b'\0\0', 0), (b'\0\2', 0),
                                   (b'\1\2', -10029), (b'\1\1', -1)):
            response = c.parse_unlock_response(packet_from_wire(unlock_reply(parameters)))
            self.assertEqual(response.result, result)
            self.assertEqual(response.accepted, result == 0)
        self.assertIsNone(c.parse_unlock_response(packet_from_wire(unlock_reply(order=3))))
        self.assertIsNone(c.parse_unlock_response(packet_from_wire(b''.join(control_response()))))
        for params in (b'', b'\0'):
            with self.assertRaisesRegex(c.OutputFailure, '^invalid_output_response$'):
                c.parse_unlock_response(packet_from_wire(unlock_reply(params)))

    def test_invalid_inputs_fail_before_build_without_secret(self):
        defaults = dict(channel=1, output=1, opening_code=CODE, timestamp_seconds=1)
        for override in ({'output': 0}, {'output': 3}, {'output': True}, {'channel': 0},
                         {'channel': 256}, {'channel': True}, {'opening_code': ''},
                         {'opening_code': 'SECRET\0CODE'}, {'opening_code': 's' * 257},
                         {'timestamp_seconds': -1}):
            with self.subTest(field=list(override)[0]), self.assertRaises((c.OutputFailure, p.MediaProtocolError)) as caught:
                c.build_unlock_request(MATERIAL, **{**defaults, **override})
            self.assertNotIn('SECRET', str(caught.exception))

    def test_sha_integrity_is_not_bypassed(self):
        with self.assertRaisesRegex(p.MediaProtocolError, '^control_sha_mismatch$'):
            packet_from_wire(unlock_reply(bad_sha=True))


class SharedOpeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader, self.writer, self.obs = asyncio.StreamReader(), Writer(), {}
        self.session = s.QVSession('192.0.2.1', 8443, 'a' * 64, KEY, 'SYNTHETIC_AUTH', self.obs)
        self.open = patch.object(s, 'open_media_tls', AsyncMock(return_value=(self.reader, self.writer)))
        self.connect = self.open.start()
        self.reader.feed_data(setup() + b''.join(control_response()))
        self.worker = asyncio.create_task(self.session.run(AsyncMock()), name='synthetic-media-worker')
        async with asyncio.timeout(1):
            while not self.obs.get('play_accepted'):
                await asyncio.sleep(0)
        self.hub = SimpleNamespace(stopped=False, live=SimpleNamespace(session=self.session),
            entry=SimpleNamespace(data={'experimental_video': True, 'experimental_outputs': True,
                                        'opening_code': CODE, 'auth_code': 'DIFFERENT_AUTH'}))
        self.consumers = {'viewer'}
        async def acquire(owner):
            self.consumers.add(owner)
        async def release(owner, **kwargs):
            self.consumers.discard(owner)
        self.hub.acquire, self.hub.release = AsyncMock(side_effect=acquire), AsyncMock(side_effect=release)
        self.controller = c.Connect3OutputController(self.hub)

    async def asyncTearDown(self):
        await self.controller.close()
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        await self.session.close()
        self.open.stop()
        self.assertEqual(self.writer.close_count, 1)
        self.assertIsNone(self.session._read_task)
        self.assertIsNone(self.session._output_future)
        pending = [task.get_name() for task in asyncio.all_tasks()
                   if task is not asyncio.current_task() and not task.done()
                   and task.get_name().startswith(('welcomeeye-qv-', 'welcomeeye-connect3-'))]
        self.assertEqual(pending, [])

    def output_writes(self):
        return [wire for wire in self.writer.writes[1:] if aes(wire[:32], decrypt=True)[0] == 0xFE]

    async def wait_write(self, count=1):
        async with asyncio.timeout(1):
            while len(self.output_writes()) < count:
                await asyncio.sleep(0)

    async def test_ha_mapping_existing_session_only_ack_and_media_survives(self):
        for output in (0, 1):
            task = asyncio.create_task(self.controller.unlock(output))
            await self.wait_write(output + 1)
            packet = packet_from_wire(self.output_writes()[-1])
            self.assertEqual(packet.parameters[:4], bytes((output + 1, 0, 1, 1)))
            self.assertTrue(packet.parameters[16:].startswith(sha256(CODE.encode()).hexdigest().encode()))
            self.reader.feed_data(unlock_reply())
            await task
        self.connect.assert_awaited_once()
        self.assertEqual(self.consumers, {'viewer'})
        self.assertFalse(self.worker.done())
        self.assertEqual(self.controller.diagnostics()['request_send_attempt_count'], 2)
        self.assertEqual(self.controller.diagnostics()['response_count'], 2)
        self.assertFalse(self.controller.diagnostics()['physical_activation_verified'])
        serialized = json.dumps([self.obs, self.controller.diagnostics()])
        for secret in (CODE, sha256(CODE.encode()).hexdigest(), KEY, '192.0.2.1', 'DIFFERENT_AUTH'):
            self.assertNotIn(secret, serialized)

    async def test_one_pending_and_unrelated_order_cannot_confirm(self):
        task = asyncio.create_task(self.controller.unlock(0))
        await self.wait_write()
        with self.assertRaisesRegex(c.OutputFailure, '^output_busy$'):
            await self.controller.unlock(1)
        with self.assertRaisesRegex(c.OutputFailure, '^output_busy$'):
            await self.session.execute_output(2, CODE)
        self.reader.feed_data(unlock_reply(order=3))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.reader.feed_data(unlock_reply())
        await task
        self.assertEqual(len(self.output_writes()), 1)

    async def test_early_reply_before_write_is_not_saved(self):
        await self.session._write_lock.acquire()
        task = asyncio.create_task(self.controller.unlock(0))
        try:
            async with asyncio.timeout(1):
                while self.session._output_future is None:
                    await asyncio.sleep(0)
            self.reader.feed_data(unlock_reply())
            async with asyncio.timeout(1):
                while self.obs['control_command_counts'].get('254', 0) < 1:
                    await asyncio.sleep(0)
            self.assertEqual(self.session.output_diagnostics()['response_count'], 0)
        finally:
            self.session._write_lock.release()
        await self.wait_write()
        self.assertFalse(task.done())
        self.reader.feed_data(unlock_reply())
        await task
        self.assertEqual(self.session.output_diagnostics()['response_count'], 1)

    async def test_invalid_sha_is_not_a_confirmation_and_never_replays(self):
        task = asyncio.create_task(self.controller.unlock(0))
        await self.wait_write()
        self.reader.feed_data(unlock_reply(bad_sha=True))
        with self.assertRaisesRegex(c.OutputFailure, '^output_session_closed$') as caught:
            await task
        self.assertTrue(caught.exception.physical_request_uncertain)
        self.assertEqual(self.session.output_diagnostics()['response_count'], 0)
        self.assertEqual(len(self.output_writes()), 1)

    async def test_drain_timeout_attempted_once_and_next_action_blocked(self):
        async def blocked_drain():
            await asyncio.Future()
        self.writer.drain.side_effect = blocked_drain
        with patch.object(s, 'WRITE_TIMEOUT', .005):
            with self.assertRaisesRegex(c.OutputFailure, '^output_send_timeout$') as caught:
                await self.controller.unlock(0)
        self.writer.drain.side_effect = None
        self.assertTrue(caught.exception.physical_request_uncertain)
        with self.assertRaisesRegex(c.OutputFailure, '^output_session_uncertain$'):
            await self.controller.unlock(1)
        self.assertEqual(len(self.output_writes()), 1)

    async def test_timeout_late_ack_cannot_be_used_by_next_action(self):
        with patch.object(s, 'UNLOCK_TIMEOUT', .005):
            with self.assertRaisesRegex(c.OutputFailure, '^output_confirmation_timeout$') as caught:
                await self.controller.unlock(0)
        self.assertTrue(caught.exception.physical_request_uncertain)
        self.reader.feed_data(unlock_reply())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(c.OutputFailure, '^output_session_uncertain$'):
            await self.controller.unlock(1)
        self.assertEqual(len(self.output_writes()), 1)
        self.assertEqual(self.controller.diagnostics()['response_count'], 0)
        self.assertTrue(self.session.output_diagnostics()['session_blocked'])
        self.assertEqual(self.consumers, {'viewer'})

    async def test_device_rejection_is_confirmed_without_automatic_replay(self):
        task = asyncio.create_task(self.controller.unlock(0))
        await self.wait_write()
        self.reader.feed_data(unlock_reply(b'\1\2'))
        with self.assertRaisesRegex(c.OutputFailure, '^device_rejected$') as caught:
            await task
        self.assertFalse(caught.exception.physical_request_uncertain)
        self.assertEqual(len(self.output_writes()), 1)
        self.assertEqual(self.controller.diagnostics()['last_result'], -10029)
        self.assertFalse(self.session.output_diagnostics()['session_blocked'])

    async def test_drain_failure_after_write_blocks_session(self):
        self.writer.drain.side_effect = OSError('PRIVATE_NETWORK_EXCEPTION')
        with self.assertRaisesRegex(c.OutputFailure, '^output_send_failed$') as caught:
            await self.controller.unlock(0)
        self.assertTrue(caught.exception.physical_request_uncertain)
        with self.assertRaisesRegex(c.OutputFailure, '^output_session_uncertain$'):
            await self.controller.unlock(0)
        self.assertEqual(len(self.output_writes()), 1)
        self.assertEqual(self.controller.diagnostics()['request_sent_count'], 0)
        self.assertNotIn('PRIVATE', repr(self.controller.diagnostics()))
        self.writer.drain.side_effect = None

    async def test_partial_write_failure_is_attempted_not_replayed(self):
        original = self.writer.write
        def write(data):
            original(data)
            if aes(data[:32], decrypt=True)[0] == 0xFE:
                raise OSError('PRIVATE_PARTIAL_WRITE')
        self.writer.write = write
        with self.assertRaises(c.OutputFailure) as caught:
            await self.controller.unlock(0)
        self.assertTrue(caught.exception.physical_request_uncertain)
        self.assertEqual(self.session.output_diagnostics()['request_send_attempt_count'], 1)
        self.assertEqual(len(self.output_writes()), 1)

    async def test_eof_during_pending_resolves_and_releases(self):
        task = asyncio.create_task(self.controller.unlock(0))
        await self.wait_write()
        self.reader.feed_eof()
        with self.assertRaisesRegex(c.OutputFailure, '^output_session_closed$') as caught:
            await asyncio.wait_for(task, 1)
        self.assertTrue(caught.exception.physical_request_uncertain)
        self.assertEqual(len(self.output_writes()), 1)
        self.assertEqual(self.consumers, {'viewer'})

    async def test_malformed_ack_blocks_future_commands(self):
        task = asyncio.create_task(self.controller.unlock(0))
        await self.wait_write()
        self.reader.feed_data(unlock_reply(b'\0'))
        with self.assertRaisesRegex(c.OutputFailure, '^invalid_output_response$') as caught:
            await task
        self.assertTrue(caught.exception.physical_request_uncertain)
        with self.assertRaisesRegex(c.OutputFailure, '^output_session_uncertain$'):
            await self.controller.unlock(1)
        self.assertEqual(len(self.output_writes()), 1)

    async def test_close_cancels_pending_and_drains_lease(self):
        task = asyncio.create_task(self.controller.unlock(0))
        await self.wait_write()
        await self.controller.close()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await task
        self.assertTrue(caught.exception.physical_request_uncertain)
        self.assertEqual(len(self.output_writes()), 1)
        self.assertEqual(self.consumers, {'viewer'})
        self.assertTrue(self.controller.diagnostics()['physical_request_uncertain'])

    async def test_pre_attempt_cancel_does_not_send_or_mark_uncertain(self):
        await self.session._write_lock.acquire()
        task = asyncio.create_task(self.controller.unlock(0))
        try:
            async with asyncio.timeout(1):
                while self.session._output_future is None:
                    await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError) as caught:
                await task
        finally:
            self.session._write_lock.release()
        self.assertFalse(caught.exception.physical_request_uncertain)
        self.assertEqual(self.output_writes(), [])
        self.assertFalse(self.session.output_diagnostics()['session_blocked'])
        self.assertEqual(self.consumers, {'viewer'})

    async def test_close_while_waiting_write_lock_never_attempts_output(self):
        await self.session._write_lock.acquire()
        task = asyncio.create_task(self.controller.unlock(0))
        closing = None
        try:
            async with asyncio.timeout(1):
                while self.session._output_future is None:
                    await asyncio.sleep(0)
            closing = asyncio.create_task(self.session.close())
            async with asyncio.timeout(1):
                while not self.session._output_future.done():
                    await asyncio.sleep(0)
        finally:
            self.session._write_lock.release()
        with self.assertRaisesRegex(c.OutputFailure, '^output_session_closed$') as caught:
            await task
        await closing
        self.assertFalse(caught.exception.physical_request_uncertain)
        self.assertEqual(self.output_writes(), [])
        self.assertEqual(self.consumers, {'viewer'})

    async def test_no_permission_flag_or_code_fails_without_media_acquire(self):
        for change in ({'experimental_outputs': False}, {'experimental_video': False}, {'opening_code': ''}):
            previous = dict(self.hub.entry.data)
            self.hub.entry.data.update(change)
            with self.assertRaisesRegex(c.OutputFailure, '^output_not_enabled$'):
                await self.controller.unlock(0)
            self.hub.entry.data = previous
        self.hub.acquire.assert_not_awaited()
        self.assertEqual(self.output_writes(), [])


if __name__ == '__main__':
    unittest.main()
