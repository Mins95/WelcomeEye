"""Exercise the real V1 worker through a decoder failure while control is pending."""
import asyncio
import importlib
import struct
import sys
import time
import types
import unittest
from unittest.mock import patch

from test_beta2_v1_video import hardware_wire
from test_beta5_v1_control import PACKAGE, inspect_request, protected, response
from test_v1_video_receive import Clock, make_session, media, synthetic_h264


class ArmedDecoder:
    def __init__(self, delegate, state):
        self.delegate = delegate
        self.state = state

    def parse(self, body):
        return self.delegate.parse(body)

    def decode(self, part):
        if self.state["armed"] and not self.state["failed"]:
            self.state["failed"] = True
            raise media.av.InvalidDataError(1094995529, "Invalid data")
        return self.delegate.decode(part)


class WorkerResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_keeps_same_session_and_confirms_after_decode_error(self):
        helpers = types.ModuleType("homeassistant.helpers")
        helpers.device_registry = types.ModuleType("homeassistant.helpers.device_registry")
        with patch.dict(
            sys.modules,
            {
                "homeassistant": types.ModuleType("homeassistant"),
                "homeassistant.helpers": helpers,
                "homeassistant.helpers.device_registry": helpers.device_registry,
            },
        ):
            hub_module = importlib.import_module(PACKAGE + ".hub")

        entry = types.SimpleNamespace(
            unique_id="TESTUID000",
            data={
                "host": "unused",
                "username": "unused",
                "password": "test-code",
                "detected_model": "WelcomeEye Connect V1",
            },
        )
        hub = hub_module.WelcomeEyeHub(types.SimpleNamespace(), entry)
        hub.stopped = False
        hub._observe_device_model = lambda *args: None

        fmt = bytearray(40)
        fmt[4:8] = b"H264"
        struct.pack_into("<HH", fmt, 12, 352, 288)
        fmt[16] = 20
        struct.pack_into("<I", fmt, 20, 8000)
        struct.pack_into("<HH", fmt, 28, 0x7A19, 1)

        initial = synthetic_h264()
        recovery = synthetic_h264()
        initial_wire = b"".join(hardware_wire(kind, body) for kind, body in initial)
        session = make_session([initial_wire], Clock(), False)
        session.info = types.SimpleNamespace(uid="TESTUID000")
        session.encryption_profile = 0x060A0401
        session.device_now = lambda: 1234
        session.last_keepalive = time.monotonic()
        session.connect = lambda: [(203, bytes(fmt))]
        session.send_start_av = lambda: None
        session.send_stop_av = lambda: None
        session.send_manufacturer = lambda _data: None

        sock = session.sock
        original_recv = sock.recv
        control_packets = []
        state = {"armed": False, "failed": False}

        def recv(size):
            if not sock.chunks:
                time.sleep(0.002)
                sock.chunks.append(protected.owsp(protected.tlv(57, bytes(4))))
            return original_recv(size)

        def sendall(packet):
            kind, _body = protected.parse_tlvs(packet[8:])[0]
            if kind == 505:
                control_packets.append(packet)
                state["armed"] = True
                # The first P image after the command trips the simulated PyAV
                # failure. A fresh I image follows on the same TCP session, then
                # the normal 506 confirmation. No second 505 is ever injected.
                sock.chunks.append(hardware_wire(101, initial[1][1]))
                sock.chunks.append(hardware_wire(100, recovery[0][1]))
                sock.chunks.append(hardware_wire(101, recovery[1][1]))
                sock.chunks.append(protected.owsp(protected.tlv(506, response())))
            elif kind != 49:
                raise AssertionError("unexpected socket command")

        sock.recv = recv
        sock.sendall = sendall

        original_pipeline = hub_module.MediaPipeline

        class TestPipeline(original_pipeline):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.decoder = ArmedDecoder(self.decoder, state)

        viewer = object()
        with (
            patch.object(hub_module, "Session", return_value=session) as factory,
            patch.object(hub_module, "MediaPipeline", TestPipeline),
        ):
            try:
                await hub.acquire(viewer)
                await asyncio.to_thread(hub.control.unlock, 0)
                self.assertTrue(state["failed"])
                self.assertEqual(len(control_packets), 1)
                self.assertEqual(inspect_request(control_packets[0])[-3:], (0, 1, bytes(2)))
                self.assertEqual(factory.call_count, 1)
                self.assertIs(hub.session, session)
                self.assertTrue(hub.thread.is_alive())
                self.assertEqual(hub.control.request_send_attempt_count, 1)
                self.assertEqual(hub.control.request_sent_count, 1)
                self.assertEqual(hub.control.response_count, 1)
                self.assertEqual(hub.control.last_result, 1)
            finally:
                await hub.release(viewer)

        self.assertEqual(len(control_packets), 1)
        self.assertTrue(session.closed.is_set())
        self.assertIsNone(hub.thread)


if __name__ == "__main__":
    unittest.main()
