"""Synthetic writers only: no packet builder or physical device is invoked."""
import asyncio
from dataclasses import dataclass, field
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_transport_lifecycle import load_source, ProtocolError

v1 = load_source('v1_control', dict(asyncio=asyncio, dataclass=dataclass, field=field,
    threading=threading, time=time, ProtocolError=ProtocolError))


class SingleShotTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, error=None):
        session = SimpleNamespace(closed=threading.Event(), info=SimpleNamespace(uid='fixture'),
                                  send_packet=Mock(side_effect=error))
        controller = SimpleNamespace(closed=threading.Event(), entry=SimpleNamespace(unique_id='fixture'),
            request_send_attempt_count=0, request_sent_count=0)
        output = v1.V1MediaOutput(controller)
        future = asyncio.get_running_loop().create_future()
        output.pending = v1.PendingOutput(session, b'SYNTHETIC-NOT-A-COMMAND', future, time.monotonic()+10)
        return output, session, controller, future

    async def test_repeated_worker_iterations_attempt_once(self):
        output, session, controller, future = self.fixture()
        for _ in range(10):
            output.send_pending(session)
        session.send_packet.assert_called_once()
        self.assertEqual(controller.request_send_attempt_count, 1)
        self.assertEqual(output.pending.packet, b'')
        future.cancel()

    async def test_send_error_or_reconnect_cannot_replay(self):
        for error in (TimeoutError(), BrokenPipeError(), ConnectionResetError()):
            output, session, controller, future = self.fixture(error)
            output.send_pending(session)
            output.send_pending(session)
            replacement = SimpleNamespace(send_packet=Mock())
            output.send_pending(replacement)
            await asyncio.sleep(0)
            self.assertIs(future.exception(), error)
            self.assertEqual(controller.request_send_attempt_count, 1)
            session.send_packet.assert_called_once()
            replacement.send_packet.assert_not_called()

    async def test_cancel_before_claim_sends_nothing(self):
        output, session, controller, future = self.fixture()
        controller.closed.set()
        output.send_pending(session)
        output.send_pending(session)
        await asyncio.sleep(0)
        self.assertIsInstance(future.exception(), TimeoutError)
        session.send_packet.assert_not_called()
        self.assertEqual(controller.request_send_attempt_count, 0)
