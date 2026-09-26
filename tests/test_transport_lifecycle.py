"""Offline transport/cancellation regressions; no device or output commands."""
import ast
from ipaddress import IPv4Address
from load_integration import CAP_IMPORTS
import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import socket
import struct
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local'


class ProtocolError(ValueError):
    pass


def load_source(name, namespace):
    """Exercise production definitions without importing HA/PyAV on the host."""
    path = SOURCE / f'{name}.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body
                 if not isinstance(node, (ast.Import, ast.ImportFrom))]
    module = ModuleType(f'_transport_test_{name}')
    sys.modules[module.__name__] = module
    module.__dict__.update(namespace)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


client = load_source('client', dict(socket=socket, struct=struct, time=time,
                                  threading=threading, ProtocolError=ProtocolError,
                                  parse_tlvs=lambda data: [(57, data)]))
talkback = load_source('talkback', dict(asyncio=asyncio, dataclass=dataclass,
                                     struct=struct, time=time, ProtocolError=ProtocolError))


class SocketFixture:
    def __init__(self, *reads):
        self.reads = list(reads)
        self.timeout = 2
        self.sent = []

    def recv(self, size):
        result = self.reads.pop(0)
        if isinstance(result, Exception):
            raise result
        assert len(result) <= size
        return result

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, packet):
        self.sent.append(packet)

    def shutdown(self, how):
        pass

    def close(self):
        pass


def session_for(*reads):
    session = client.Session('192.0.2.1', 'test', 'test')
    session.sock = SocketFixture(*reads)
    session.last_keepalive = time.monotonic()
    return session


class FramingTests(unittest.TestCase):
    def test_discovery_rejects_non_ipv4_before_opening_socket(self):
        for host in ('127.1', '::1', 'host.invalid', '192.0.2.999', '192.0.2.1\x00'):
            with self.subTest(host=host), patch.object(client.socket, 'socket') as make_socket:
                with self.assertRaises((ValueError, OSError)):
                    client.discover(host)
                make_socket.assert_not_called()

    def test_clean_idle_header_can_resume(self):
        session = session_for(TimeoutError(), struct.pack('>I', 5), bytes(4) + b'x')
        with self.assertRaises(TimeoutError):
            session.read()
        self.assertEqual(session.read(), [(57, b'x')])

    def test_partial_length_timeout_is_fatal(self):
        session = session_for(b'\x00\x00', TimeoutError())
        with self.assertRaisesRegex(ProtocolError, 'Incomplete OWSP'):
            session.read()

    def test_payload_timeout_without_bytes_is_fatal(self):
        session = session_for(struct.pack('>I', 5), TimeoutError())
        with self.assertRaisesRegex(ProtocolError, 'Incomplete OWSP'):
            session.read()

    def test_partial_payload_timeout_is_fatal(self):
        session = session_for(struct.pack('>I', 5), bytes(2), TimeoutError())
        with self.assertRaisesRegex(ProtocolError, 'Incomplete OWSP'):
            session.read()

    def test_zero_padding_then_idle_can_resume(self):
        session = session_for(bytes(4), TimeoutError(), struct.pack('>I', 5), bytes(4) + b'x')
        with self.assertRaises(TimeoutError):
            session.read()
        self.assertEqual(session.zero_frame_count, 1)
        self.assertEqual(session.read(), [(57, b'x')])

    def test_login_preserves_bytes_across_timeout(self):
        session = session_for(b'\x00\x00', TimeoutError(), b'\x00\x05', bytes(4) + b'x')
        session._login_deadline = time.monotonic() + 20
        self.assertEqual(session.read(), [(57, b'x')])
        self.assertEqual(session.login_timeout_count, 1)

    def test_v1_retains_partial_bytes(self):
        session = session_for(b'\x00\x00', TimeoutError(), b'\x00\x05', bytes(4) + b'x')
        # Inspect the V1 exact reader directly without importing its media parser.
        session.enable_v1_video_receive()
        session._v1_reading_header = True
        session._v1_read_started = time.monotonic()
        self.assertEqual(session._exact(4), struct.pack('>I', 5))
        self.assertFalse(session._v1_read_failed)

    def test_closed_socket_reports_transport_error(self):
        session = session_for()
        session.close()
        with self.assertRaises(ConnectionAbortedError):
            session._exact(4)
        with self.assertRaises(ConnectionAbortedError):
            session.send_packet(b'fixture')


class MicrophoneSession:
    def __init__(self):
        self.closed = threading.Event()
        self.talk_enabled = False
        self.start_entered = threading.Event()
        self.start_release = threading.Event()
        self.stop_entered = threading.Event()
        self.stop_release = threading.Event()
        self.stop_release.set()
        self.operations = []

    def start_talk(self):
        self.start_entered.set()
        if not self.start_release.wait(5):
            raise TimeoutError('test start barrier')
        self.operations.append('start')

    def stop_talk(self):
        self.stop_entered.set()
        if not self.stop_release.wait(5):
            raise TimeoutError('test stop barrier')
        self.operations.append('stop')


class TalkbackCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def barrier(self, event):
        self.assertTrue(await asyncio.to_thread(event.wait, 2))

    def create_talkback(self):
        session = MicrophoneSession()
        self.addCleanup(session.start_release.set)
        self.addCleanup(session.stop_release.set)
        return talkback.Talkback(SimpleNamespace(session=session, connected=True)), session

    async def test_cancelled_start_finishes_before_stop(self):
        talk, session = self.create_talkback()
        task = asyncio.create_task(talk.start(object()))
        await self.barrier(session.start_entered)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(session.stop_entered.is_set())
        session.start_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(session.operations, ['start', 'stop'])
        self.assertIsNone(talk.owner)
        self.assertIsNone(talk.reply)
        self.assertFalse(talk.active)

    async def test_repeated_cancellation_drains_stop_and_clears_owner(self):
        talk, session = self.create_talkback()
        session.stop_release.clear()
        task = asyncio.create_task(talk.start(object()))
        await self.barrier(session.start_entered)
        task.cancel()
        session.start_release.set()
        await self.barrier(session.stop_entered)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        session.stop_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(session.operations, ['start', 'stop'])
        self.assertIsNone(talk.owner)
        self.assertIsNone(talk.session)
        self.assertIsNone(talk.reply)
        self.assertIsNone(talk.encoder)
        self.assertEqual(talk.diagnostics['state'], 'off')

    async def test_cancelled_stop_waits_for_write_and_clears_state(self):
        talk, session = self.create_talkback()
        owner = object()
        talk.owner, talk.session, talk.active = owner, session, True
        talk.reply = asyncio.get_running_loop().create_future()
        session.talk_enabled = True
        session.stop_release.clear()
        task = asyncio.create_task(talk.stop(owner))
        await self.barrier(session.stop_entered)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertFalse(session.talk_enabled)
        session.stop_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(talk.owner)
        self.assertIsNone(talk.reply)

    async def test_stop_failure_retains_only_error_type(self):
        talk, session = self.create_talkback()
        talk.owner, talk.session, talk.active = object(), session, True
        session.stop_talk = Mock(side_effect=OSError('sensitive fixture endpoint'))
        await talk.stop(talk.owner)
        self.assertIsNone(talk.owner)
        self.assertEqual(talk.diagnostics['cleanup_error_type'], 'OSError')
        self.assertNotIn('sensitive', str(talk.diagnostics))


class ConfigFlowBase:
    def _async_current_entries(self):
        return []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def async_show_form(self, **kwargs):
        return kwargs


config_flow = load_source('config_flow', dict(
    config_entries=SimpleNamespace(ConfigFlow=ConfigFlowBase),
    IPv4Address=IPv4Address, DiscoveryTimeout=client.DiscoveryTimeout, **CAP_IMPORTS,
    DOMAIN='welcomeeye_local', DEFAULT_NAME='WelcomeEye',
    AuthenticationError=client.AuthenticationError, ProtocolError=ProtocolError,
    validate_connection=Mock(),
))
config_flow.schema = lambda defaults: defaults


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_error_classes_are_distinct(self):
        for error, expected in (
            (client.AuthenticationError(), 'invalid_auth'),
            (ProtocolError('Unsupported discovery reply'), 'unsupported_device'),
            (TimeoutError(), 'cannot_connect'),
            (OSError(), 'cannot_connect'),
            (ValueError(), 'cannot_connect'),
            (RuntimeError(), 'cannot_connect'),
        ):
            with self.subTest(error=type(error).__name__):
                flow = config_flow.WelcomeEyeConfigFlow()
                flow.hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=error))
                result = await flow.async_step_user(dict(host='192.0.2.1', username='test', password='test'))
                self.assertEqual(result['errors'], {'base': expected})

    async def test_validation_closes_socket_after_auth_failure(self):
        session = Mock()
        session.connect.side_effect = client.AuthenticationError()
        with patch.object(client, 'Session', return_value=session):
            with self.assertRaises(client.AuthenticationError):
                client.validate_connection('192.0.2.1', 'test', 'test')
        session.close.assert_called_once()


control = load_source('control', dict(**CAP_IMPORTS,
    threading=threading, time=time, asyncio=asyncio, logging=logging,
    ProtocolError=ProtocolError, V1MediaOutput=lambda controller: Mock(),
))


class ControlShutdownTests(unittest.TestCase):
    """Only exercise pre-command teardown; never build or send output packets."""
    def make_controller(self):
        return control.DeviceController(SimpleNamespace(
            entry=SimpleNamespace(data={}, unique_id='fixture'),
            connected=False, device_model='WelcomeEye Connect 2', variant=CAP_IMPORTS['DeviceVariant'].R001,
        ))

    def test_unload_during_video_setup_does_not_open_fallback(self):
        controller = self.make_controller()

        def interrupted_video(data):
            controller.close()
            raise ConnectionAbortedError('Fixture video setup interrupted')

        controller._video_session = Mock(side_effect=interrupted_video)
        controller._control_session = Mock()
        with self.assertRaises(ConnectionAbortedError):
            controller.unlock(0)
        controller._control_session.assert_not_called()
        self.assertEqual(controller.request_send_attempt_count, 0)
        self.assertFalse(controller.physical_result_uncertain)
        self.assertFalse(controller.lock.locked())

    def test_session_published_during_close_never_connects(self):
        for method in ('_control_session', '_video_session'):
            with self.subTest(method=method):
                controller = self.make_controller()
                session = Mock()

                def create_session(*args, **kwargs):
                    # Simulate shutdown after helper selection, before publish.
                    controller.close()
                    return session

                with patch.object(control, 'Session', create_session, create=True):
                    with self.assertRaises(ProtocolError):
                        getattr(controller, method)({'host': '192.0.2.1', 'username': 'test', 'password': 'test'})
                session.connect.assert_not_called()
                session.close.assert_called_once()
                self.assertEqual(controller.request_send_attempt_count, 0)


if __name__ == '__main__':
    unittest.main()
