"""Real Session/worker/crypto/decoder with a fragmented in-memory device.

Only discovery, TCP creation and the login reply decoder are boundary stubs.
No real socket is opened and output requests never leave this process.
"""
import asyncio
from collections import deque
from contextlib import ExitStack
from fractions import Fraction
import importlib
import struct
import threading
import time
import types
from unittest.mock import patch

import av
import pytest

from beta8_helpers import entry, load_hub
from test_beta2_v1_video import hardware_wire
from test_beta5_v1_control import inspect_request, response
from test_control_coordination import PACKAGE

client = importlib.import_module(PACKAGE + '.client')
protected = importlib.import_module(PACKAGE + '.protected')
v1_control = importlib.import_module(PACKAGE + '.v1_control')


def generated_video(width, height):
    encoder = av.CodecContext.create('libx264', 'w')
    encoder.width, encoder.height = width, height
    encoder.pix_fmt, encoder.time_base = 'yuv420p', Fraction(1, 20)
    encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                       'x264-params': 'bframes=0:repeat-headers=1:keyint=4'}
    result = []
    for index in range(8):
        frame = av.VideoFrame(width, height, 'yuv420p')
        for p, plane in enumerate(frame.planes):
            plane.update(bytes([40 + index if p == 0 else 128]) * plane.buffer_size)
        frame.pts = index
        result.extend(encoder.encode(frame))
    result.extend(encoder.encode(None))
    return [(100 if p.is_keyframe else 101, bytes(p)) for p in result]


def av_response():
    a, b = (protected.profile_nonce(0x060A0401) for _ in range(2))
    body = struct.pack('<QHH', 1234, 1, 0)
    clear = struct.pack('<I', 1) + a
    clear += protected.aes_cfb(a[2:8] + bytes(10), b[4:13] + bytes(7), body) + b
    return protected.rc4(b'TESTUID000', clear)


class Device:
    def __init__(self, packets, v1=True, reply_delay=0, failure=None, stop_reply=False):
        self.packets, self.v1 = packets, v1
        self.reply_delay, self.failure, self.stop_reply = reply_delay, failure, stop_reply
        self.chunks, self.sent, self.events = deque(), [], []
        self.timeout = 2
        self.closed = self.interrupted = False
        self.command_sent = threading.Event()
        self.due = None
        self.reply_sent = False

    def settimeout(self, value): self.timeout = value
    def gettimeout(self): return self.timeout
    def shutdown(self, how): self.interrupted = True
    def close(self):
        self.closed = True
        self.events.append('CLOSED')

    def enqueue(self, kind, body):
        self.chunks.append(protected.owsp(protected.tlv(kind, body)))

    def sendall(self, packet):
        parts = protected.parse_tlvs(packet[8:])
        # Official protected login includes protocol selector 40 before 501.
        if parts[0][0] == 40:
            assert parts[0] == (40, b'\x05\x00\x00\x00')
            assert len(parts) == 2 and parts[1][0] == 501
        kind, body = parts[-1]
        self.sent.append(packet)
        self.events.append(kind)
        if kind == 501:
            self.enqueue(502, b'login-boundary')
            fmt = bytearray(40)
            fmt[4:8] = b'H264'
            struct.pack_into('<HH', fmt, 12, *( (352, 288) if self.v1 else (720, 576)))
            fmt[16] = 20
            struct.pack_into('<I', fmt, 20, 8000)
            struct.pack_into('<HH', fmt, 28, 0x7a19, 1)
            self.enqueue(203, bytes(fmt))
            for k, data in self.packets:
                if self.v1:
                    self.chunks.append(hardware_wire(k, data))
                else:
                    self.enqueue(k, data)
                self.enqueue(98, b'\xd5' * 160)
        elif kind == 5007:
            self.enqueue(5008, av_response())
        elif kind == 505:
            assert len([p for p in self.sent if p[8:10] == b'\xf9\x01']) == 1
            self.command_sent.set()
            self.due = time.monotonic() + self.reply_delay
        elif kind == 5009 and self.stop_reply:
            self.enqueue(5010, av_response())
        elif kind not in (49, 509, 5009, 5005):
            raise AssertionError(f'unexpected wire type {kind}')

    def recv(self, size):
        if self.interrupted:
            return b''
        if not self.chunks:
            time.sleep(.01)
            if self.command_sent.is_set() and not self.reply_sent:
                if self.failure == 'eof': return b''
                if self.failure == 'reset': raise ConnectionResetError()
                if self.failure == 'partial':
                    self.chunks.append(b'\x00\x00')
                    self.failure = 'partial_wait'
                elif self.failure == 'partial_wait':
                    raise TimeoutError()
                elif self.failure == 'absent' or time.monotonic() < self.due:
                    raise TimeoutError()
                else:
                    self.enqueue(506, response())
                    self.reply_sent = True
            else:
                self.enqueue(57, bytes(4))
        chunk = self.chunks.popleft()
        count = min(size, 73, len(chunk))
        if len(chunk) > count:
            self.chunks.appendleft(chunk[count:])
        return chunk[:count]


def harness(v1=True, **options):
    module = load_hub()
    hub = module.WelcomeEyeHub(types.SimpleNamespace(), entry(
        'WelcomeEye Connect V1' if v1 else 'WelcomeEye Connect 2'))
    hub.stopped = False
    hub._observe_device_model = lambda *args: None
    packets = generated_video(*( (352, 288) if v1 else (720, 576)))
    devices = []
    def connect(*args, **kwargs):
        device = Device(packets, v1, **options)
        devices.append(device)
        return device
    stack = ExitStack()
    stack.enter_context(patch.object(client, 'discover', return_value=types.SimpleNamespace(uid='TESTUID000', tcp_port=1)))
    stack.enter_context(patch.object(client.socket, 'create_connection', side_effect=connect))
    stack.enter_context(patch.object(client, 'decode_login_reply', return_value=(1, None, {'AppId': 0x060A0401}, 1234)))
    return hub, devices, stack


async def eventually(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.002)


@pytest.mark.parametrize('delay', [0, 1, 5, 9])
def test_v1_same_session_delayed_506_real_worker(delay):
    async def run():
        hub, devices, patches = harness(reply_delay=delay)
        with patches:
            viewer = object()
            try:
                await hub.acquire(viewer)
                await asyncio.to_thread(hub.control.unlock, 0)
                assert hub.control.last_result == 1
                assert hub.control.request_send_attempt_count == 1
                assert not hub.control.physical_result_uncertain
                assert len(devices) == 1
                assert hub.thread.is_alive()
                assert hub.lt_start_av_result == 1
                assert devices[0].events.index(5007) < devices[0].events.index(505)
                physical = next(p for p in devices[0].sent if p[8:10] == b'\xf9\x01')
                assert inspect_request(physical)[-3:] == (0, 1, bytes(2))
            finally:
                await hub.release(viewer)
            device = devices[0]
            assert device.events[-3:] == [5009, 5005, 'CLOSED']
            assert device.sent[-1].hex() == '00000008000000008d130000'
            assert hub.lifecycle['session_stop_sent']
            assert hub.lifecycle['v1_device_release_complete'] is None
            assert hub.thread is None and hub.session is None
            assert not hub.consumers and hub.control.v1_media.pending is None
    asyncio.run(run())


@pytest.mark.parametrize('failure,reason', [('absent', None), ('eof', 'remote_tcp_eof'), ('reset', 'connection_reset')])
def test_v1_uncertain_never_replayed(failure, reason):
    async def run():
        hub, devices, patches = harness(failure=failure)
        with patches, patch.object(v1_control, 'RESPONSE_TIMEOUT', .12):
            viewer = object()
            await hub.acquire(viewer)
            try:
                from welcomeeye_coordination_test.control import ControlFailure
                with pytest.raises(ControlFailure) as error:
                    await asyncio.to_thread(hub.control.unlock_for_ha, 1)
                assert error.value.physical_request_uncertain
                assert hub.control.physical_result_uncertain
                assert hub.control.request_send_attempt_count == 1
                if reason:
                    assert hub.lifecycle['worker_exit_reason'] == reason
                    assert hub.lifecycle['pending_output_sent']
                    assert not hub.lifecycle['pending_output_confirmation_seen']
            finally:
                await hub.release(viewer)
            assert len(devices) == 1
            assert devices[0].closed
            assert devices[0].events.count(505) == 1
            assert hub.thread is None and hub.session is None
            assert hub.control.v1_media.pending is None and not hub.consumers
    asyncio.run(run())


@pytest.mark.parametrize('shutdown', [False, True])
def test_release_and_ha_stop_during_pending_output(shutdown):
    async def run():
        hub, devices, patches = harness(reply_delay=.15)
        with patches:
            viewer = object()
            await hub.acquire(viewer)
            command = asyncio.create_task(asyncio.to_thread(hub.control.unlock_for_ha, 0))
            await eventually(lambda: devices[0].command_sent.is_set())
            if shutdown:
                await hub.stop(reason='home_assistant_stop')
                result = (await asyncio.gather(command, return_exceptions=True))[0]
                assert isinstance(result, Exception)
            else:
                await hub.release(viewer)
                assert hub.thread.is_alive()  # the output owns its own lease
                await command
            await eventually(lambda: hub.thread is None)
            assert devices[0].events.count(505) == 1
            assert devices[0].events[-3:] == [5009, 5005, 'CLOSED']
            assert not hub.consumers and hub.control.v1_media.pending is None
    asyncio.run(run())


@pytest.mark.parametrize('stop_reply', [False, True])
def test_stop_does_not_invent_5010_wait_or_device_release_confirmation(stop_reply):
    async def run():
        hub, devices, patches = harness(stop_reply=stop_reply)
        with patches:
            await hub.acquire('viewer')
            await hub.release('viewer')
            assert devices[0].events[-3:] == [5009, 5005, 'CLOSED']
            assert hub.lifecycle['stop_av_wait_duration_ms'] == 0
            assert hub.lifecycle['v1_device_release_complete'] is None
            assert hub.lifecycle['tcp_closed']
    asyncio.run(run())


@pytest.mark.parametrize('v1', [False, True])
def test_fifty_acquire_release_cycles_no_worker_socket_or_lease_left(v1):
    async def run():
        baseline = {t.ident for t in threading.enumerate() if t.name == 'welcomeeye-media'}
        hub, devices, patches = harness(v1=v1)
        with patches:
            for index in range(50):
                await hub.acquire(index)
                await eventually(lambda: hub.image is not None)
                await hub.release(index)
                assert hub.thread is None and hub.session is None
                assert not hub.consumers and not hub.buffer and not hub.queues
                assert devices[-1].closed
                assert devices[-1].events.count(505) == 0
                assert devices[-1].events.count(5005) == int(v1)
            await hub.stop()
        assert len(devices) == 50
        assert {t.ident for t in threading.enumerate() if t.name == 'welcomeeye-media'} == baseline
    asyncio.run(run())
