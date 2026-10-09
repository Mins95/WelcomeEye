"""Explicit synthetic TCP openings; no physical device or activation claim."""
import asyncio
from hashlib import sha256
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_connect3_control as control_tests
from load_integration import load
from test_connect3_media_protocol import KEY, MATERIAL, aes, control_response
from test_connect3_media_session import Writer, setup

c = load('connect3.control')
p = load('connect3.protocol')
s = load('connect3.session')


class TCPSharedOpeningTests(control_tests.SharedOpeningTests):
    """Run the full existing single-shot/ACK/cancel race suite over strict TCP."""
    async def asyncSetUp(self):
        self.reader, self.writer, self.obs = asyncio.StreamReader(), Writer(), {}
        self.session = s.QVSession('192.0.2.1', 34567, '', KEY, 'SYNTHETIC_AUTH', self.obs,
            transport='connect3_tcp', cgi_verified=True, tcp_outputs_enabled=True)
        self.open = patch.object(s, 'open_connect3_media_tcp', AsyncMock(return_value=(self.reader, self.writer)))
        self.connect = self.open.start()
        self.reader.feed_data(setup() + b''.join(control_response()))
        self.worker = asyncio.create_task(self.session.run(AsyncMock()), name='synthetic-media-worker')
        async with asyncio.timeout(5):
            while not self.obs.get('play_accepted'):
                await asyncio.sleep(0)
        self.hub = SimpleNamespace(stopped=False, live=SimpleNamespace(session=self.session),
            capabilities=SimpleNamespace(strike=True, gate=True, r002_qv_read=False),
            entry=SimpleNamespace(data={'experimental_video': True, 'experimental_outputs': True,
                'experimental_tcp_controls': True, 'media_transport': 'connect3_tcp',
                'opening_code': control_tests.CODE, 'auth_code': 'DIFFERENT_AUTH'}))
        self.consumers = {'viewer'}
        async def acquire(owner):
            self.consumers.add(owner)
        async def release(owner, **kwargs):
            self.consumers.discard(owner)
        self.hub.acquire, self.hub.release = AsyncMock(side_effect=acquire), AsyncMock(side_effect=release)
        self.controller = c.Connect3OutputController(self.hub)

    async def test_new_opt_in_and_selected_capability_are_literal_true_before_acquire(self):
        for value in (None, False, 1, 'true'):
            self.hub.entry.data['experimental_tcp_controls'] = value
            with self.assertRaisesRegex(c.OutputFailure, '^connect3_tcp_outputs_disabled$'):
                await self.controller.unlock(0)
        self.hub.entry.data['experimental_tcp_controls'] = True
        for capability in ('strike', 'gate'):
            for value in (False, 1, 'true'):
                setattr(self.hub.capabilities, capability, value)
                with self.assertRaisesRegex(c.OutputFailure, '^connect3_tcp_outputs_disabled$'):
                    await self.controller.unlock(0 if capability == 'strike' else 1)
            setattr(self.hub.capabilities, capability, True)
        self.hub.acquire.assert_not_awaited()
        self.assertEqual(self.output_writes(), [])

    async def test_session_opt_in_is_independent_and_defaults_fail_closed(self):
        for value in (False, None, 1, 'true'):
            current = s.QVSession('192.0.2.1', 34567, '', KEY, 'SYNTHETIC_AUTH', {},
                transport='connect3_tcp', cgi_verified=True, tcp_outputs_enabled=value)
            with patch.object(s, 'build_unlock_request') as build:
                with self.assertRaisesRegex(c.OutputFailure, '^connect3_tcp_outputs_disabled$'):
                    await current.execute_output(1, control_tests.CODE)
            build.assert_not_called()
            await current.close()
        self.assertEqual(self.output_writes(), [])

    async def test_security_profile_is_checked_before_building_any_opening_code(self):
        for mode, digest in ((0, 1), (1, 1), (2, 0)):
            previous = self.session._material
            self.session._material = p.CipherMaterial(KEY, mode, digest)
            try:
                with patch.object(s, 'build_unlock_request') as build:
                    with self.assertRaisesRegex(p.MediaProtocolError, '^connect3_tcp_unsafe_crypto_mode$'):
                        await self.session.execute_output(1, 'NEVER_ENCODE_OPENING_CODE')
                build.assert_not_called()
            finally:
                self.session._material = previous
        current = s.QVSession('192.0.2.1', 34567, '', KEY, 'SYNTHETIC_AUTH', {},
            transport='connect3_tcp', cgi_verified=True, tcp_outputs_enabled=True)
        with self.assertRaisesRegex(p.MediaProtocolError, '^connect3_tcp_setup_only$'):
            current._check_connect3_tcp_write(b'\xa9' + bytes(31), physical=True)
        await current.close()
        self.assertEqual(self.output_writes(), [])

    async def test_tcp_physical_write_boundary_accepts_only_native_order4_layout(self):
        valid = c.build_unlock_request(MATERIAL, channel=1, output=1,
            opening_code=control_tests.CODE, timestamp_seconds=1)
        self.session._check_connect3_tcp_write(valid, physical=True)
        with self.assertRaises(p.MediaProtocolError):
            self.session._check_connect3_tcp_write(valid)  # FE is never a generic command.
        for command, order in ((0, 0), (1, 0), (7, 0), (0xFE, 3), (0xFE, 5), (0xA2, 4)):
            header = bytearray(aes(valid[:32], decrypt=True))
            header[0], header[13] = command, order
            parameters = aes(valid[32:], decrypt=True)[:80]
            wire = p._encode_command(header, parameters, MATERIAL)
            with self.assertRaises(p.MediaProtocolError):
                self.session._check_connect3_tcp_write(wire, physical=True)
        header = bytearray(aes(valid[:32], decrypt=True))
        parameters = bytearray(aes(valid[32:], decrypt=True)[:80])
        for position, value in ((0, 0), (0, 3), (1, 1), (2, 0), (3, 0), (4, 1), (16, 0)):
            malformed = bytearray(parameters)
            malformed[position] = value
            with self.assertRaises(p.MediaProtocolError):
                self.session._check_connect3_tcp_write(p._encode_command(header, bytes(malformed), MATERIAL),
                                                      physical=True)
        for mode, digest in ((0, 1), (1, 1), (2, 0)):
            previous = self.session._material
            self.session._material = p.CipherMaterial(KEY, mode, digest)
            try:
                with self.assertRaisesRegex(p.MediaProtocolError, '^connect3_tcp_unsafe_crypto_mode$'):
                    self.session._check_connect3_tcp_write(valid, physical=True)
            finally:
                self.session._material = previous
        self.assertEqual(self.output_writes(), [])

    async def test_duplicate_ack_is_consumed_once_without_followup_write(self):
        task = asyncio.create_task(self.controller.unlock(0))
        await self.wait_write()
        self.reader.feed_data(control_tests.unlock_reply() * 2)
        await task
        async with asyncio.timeout(5):
            while self.obs['control_command_counts'].get('254', 0) < 2:
                await asyncio.sleep(0)
        self.assertEqual(self.session.output_diagnostics()['response_count'], 1)
        self.assertEqual(len(self.output_writes()), 1)
        self.assertEqual(self.consumers, {'viewer'})
        self.connect.assert_awaited_once()
        wire = self.output_writes()[0]
        decoded = control_tests.packet_from_wire(wire)
        self.assertEqual(decoded.header.plaintext[13], 4)
        self.assertEqual(decoded.parameters[:4], b'\1\0\1\1')
        for secret in (control_tests.CODE.encode(), sha256(control_tests.CODE.encode()).hexdigest().encode()):
            self.assertNotIn(secret, wire)
        self.assertNotIn(control_tests.CODE, json.dumps(self.obs))
