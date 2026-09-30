"""SYNTHETIC UDP responses only; no broadcast or real device is contacted."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

qv = load('r002.qv_discovery')
hub_module = load('r002.hub')
PRIVATE = b'ASZENO.SEARCH.V4.1' + b'SYNTHETIC_PRIVATE_UID'


class FakeNetwork:
    def __init__(self, events=(), *, module=qv, bind_error=None, send_error=None, async_error=None):
        self.module = module
        self.events = events
        self.bind_error, self.send_error, self.async_error = bind_error, send_error, async_error
        self.ports, self.endpoints, self.sent = [], [], []
        self.sending = asyncio.Event()

    async def open(self, collection, port):
        self.ports.append(port)
        if self.bind_error == port:
            raise OSError('SYNTHETIC_BIND_ERROR_PRIVATE')
        protocol = self.module._Listener(collection, port)
        transport = Mock()
        transport.close.side_effect = lambda: protocol.connection_lost(None)
        def send(data, destination):
            self.sent.append((port, data, destination))
            self.sending.set()
            if self.send_error:
                raise self.send_error
            loop = asyncio.get_running_loop()
            for payload, address, receive_port in self.events:
                receiver = next(p for _, p in self.endpoints if p.port == receive_port)
                loop.call_soon(receiver.datagram_received, payload, address)
            if self.async_error:
                loop.call_soon(protocol.error_received, self.async_error)
        transport.sendto.side_effect = send
        self.endpoints.append((transport, protocol))
        return transport, protocol

    def check_closed(self, testcase):
        for transport, protocol in self.endpoints:
            transport.close.assert_called_once()
            testcase.assertTrue(protocol.closed.done())
            testcase.assertFalse(protocol.collection.active)


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, events=(), *, include_response=True, **kwargs):
        network = FakeNetwork(events, **kwargs)
        with patch.object(qv, '_open_listener', side_effect=network.open), patch.object(qv, 'TIMEOUT', .05):
            result = await qv.discover_qv('192.0.2.1', include_response=include_response)
        network.check_closed(self)
        self.assertNotIn('192.0.2.1', json.dumps(result))
        return result, network

    async def test_exact_single_broadcast_and_both_receive_ports(self):
        events = [(PRIVATE, ('192.0.2.1', 5000), port) for port in (5001, 5003)]
        result, network = await self.run_probe(events)
        self.assertEqual(network.ports, [5001, 5003])
        self.assertEqual(network.sent, [(5003, b'ASZENO.SEARCH.V4.1', ('255.255.255.255', 5000))])
        self.assertEqual(len(qv.REQUEST), 18)
        self.assertEqual(result['request_sent_count'], 1)
        self.assertEqual(result['matching_datagrams'], 2)
        self.assertEqual(result['recognized_prefixes'], 2)
        self.assertEqual([r['local_port'] for r in result['responses']], [5001, 5003])
        self.assertEqual(result['responses'][0]['response_hex'], PRIVATE.hex())
        self.assertFalse(result['metadata_decoded'])
        self.assertFalse(result['device_authenticated'])

    async def test_default_never_exports_raw(self):
        result, _ = await self.run_probe([(PRIVATE, ('192.0.2.1', 5000), 5003)], include_response=False)
        serialized = json.dumps(result)
        for secret in ('response_hex', PRIVATE.hex(), 'SYNTHETIC_PRIVATE_UID'):
            self.assertNotIn(secret, serialized)

    async def test_no_reply_is_an_observation_deadline_not_malformed(self):
        result, network = await self.run_probe()
        self.assertEqual(result['collection_end_reason'], 'deadline')
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['responses'], [])
        self.assertIsNone(result['last_error_type'])
        self.assertEqual(len(network.sent), 1)

    async def test_ignore_other_devices_without_retaining_their_payload(self):
        other = b'OTHER_DEVICE_PRIVATE'
        result, _ = await self.run_probe([(other, ('192.0.2.77', 5000), 5001),
                                        (PRIVATE, ('192.0.2.1', 9000), 5003)])
        self.assertEqual(result['ignored_datagrams'], 1)
        self.assertEqual(result['matching_datagrams'], 1)
        self.assertNotIn(other.hex(), json.dumps(result))
        self.assertNotIn('192.0.2.77', json.dumps(result))

    async def test_response_limit_stops_and_ignores_late_datagrams(self):
        result, network = await self.run_probe([(PRIVATE, ('192.0.2.1', 5000), 5003)]*10)
        self.assertEqual(result['collection_end_reason'], 'response_limit')
        self.assertEqual(len(result['responses']), 4)
        self.assertEqual(result['datagrams_seen'], 4)
        before = json.dumps(result)
        network.endpoints[0][1].datagram_received(b'LATE', ('192.0.2.1', 5000))
        self.assertEqual(json.dumps(result), before)

    async def test_unrelated_traffic_is_bounded(self):
        result, _ = await self.run_probe([(b'OTHER', ('192.0.2.2', 5000), 5003)]*100)
        self.assertEqual(result['collection_end_reason'], 'datagram_limit')
        self.assertEqual(result['datagrams_seen'], 64)
        self.assertEqual(result['responses'], [])

    async def test_oversized_datagram_is_explicitly_truncated(self):
        data = PRIVATE + bytes(3000)
        result, _ = await self.run_probe([(data, ('192.0.2.1', 5000), 5003)])
        item = result['responses'][0]
        self.assertTrue(item['truncated'])
        self.assertEqual(item['datagram_bytes_received'], len(data))
        self.assertEqual(item['bytes_collected'], 2048)
        self.assertEqual(item['response_hex'], data[:2048].hex())

    async def test_unknown_empty_and_old_prefix_are_observations_only(self):
        events = [(data, ('192.0.2.1', 5000), 5003) for data in (b'', b'unknown', b'ASZENO.SEARCH.V4'+bytes(8))]
        result, _ = await self.run_probe(events)
        self.assertEqual([r['prefix_variant'] for r in result['responses']], ['unknown', 'unknown', 'v4'])
        self.assertFalse(result['device_authenticated'])

    async def test_second_bind_failure_closes_first_without_sending(self):
        result, network = await self.run_probe(bind_error=5003)
        self.assertEqual(network.sent, [])
        self.assertEqual(result['last_stage'], 'binding')
        self.assertEqual(result['last_error_type'], 'OSError')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('SYNTHETIC_BIND_ERROR_PRIVATE', json.dumps(result))

    async def test_send_error_and_async_socket_error_are_not_retried(self):
        for kwargs in ({'send_error': OSError('PRIVATE')}, {'async_error': TimeoutError('PRIVATE')}):
            result, network = await self.run_probe(**kwargs)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['collection_end_reason'], 'network_error')
            self.assertEqual(len(network.sent), 1)
            self.assertNotIn('PRIVATE', json.dumps(result))

    async def test_absolute_deadline_includes_binding(self):
        network = FakeNetwork()
        async def slow_bind(collection, port):
            if port == 5003:
                await asyncio.Event().wait()
            return await network.open(collection, port)
        with patch.object(qv, '_open_listener', side_effect=slow_bind), patch.object(qv, 'TIMEOUT', .01):
            result = await qv.discover_qv('192.0.2.1')
        network.check_closed(self)
        self.assertEqual(result['last_stage'], 'binding')
        self.assertEqual(result['last_error_type'], 'TimeoutError')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(network.sent, [])

    async def test_only_one_network_deadline_and_bounded_close(self):
        self.assertEqual(qv.TIMEOUT, 3.0)
        with patch.object(qv.asyncio, 'timeout', wraps=asyncio.timeout) as timeout:
            await self.run_probe()
        self.assertEqual([call.args[0] for call in timeout.call_args_list], [.05, 1.0])

    async def test_invalid_target_rejected_before_any_socket(self):
        with patch.object(qv, '_open_listener') as opening:
            for host in ('not-a-host', '::1', '224.0.0.1', '0.0.0.0', '255.255.255.255'):
                with self.assertRaises(ValueError):
                    await qv.discover_qv(host)
            opening.assert_not_called()

    async def test_socket_binding_without_reuse_and_cancellation_closes_socket(self):
        collection = qv._Collection('192.0.2.1', True)
        sock = Mock()
        loop = asyncio.get_running_loop()
        with patch.object(qv.socket, 'socket', return_value=sock), patch.object(
                loop, 'create_datagram_endpoint', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await qv._open_listener(collection, 5003)
        sock.bind.assert_called_once_with(('0.0.0.0', 5003))
        sock.setblocking.assert_called_once_with(False)
        sock.setsockopt.assert_called_once_with(qv.socket.SOL_SOCKET, qv.socket.SO_BROADCAST, 1)
        sock.close.assert_called_once()
        collection.done.cancel()

    async def test_close_is_bounded_even_without_connection_lost_callback(self):
        network = FakeNetwork()
        async def open_without_close_callback(collection, port):
            transport, protocol = await network.open(collection, port)
            transport.close.side_effect = None
            return transport, protocol
        with patch.object(qv, '_open_listener', side_effect=open_without_close_callback), patch.object(
                qv, 'TIMEOUT', .01), patch.object(qv, 'CLOSE_TIMEOUT', .01):
            await qv.discover_qv('192.0.2.1')
        for transport, protocol in network.endpoints:
            transport.close.assert_called_once()
            transport.abort.assert_called_once()
            self.assertTrue(protocol.closed.done())
            self.assertFalse(protocol.collection.active)

    async def test_real_udp_api_loopback_only_with_ephemeral_ports(self):
        # No LAN/broadcast/device traffic: overrides exist only inside this test.
        class Server(asyncio.DatagramProtocol):
            def __init__(self):
                self.received = []
                self.closed = asyncio.get_running_loop().create_future()
            def connection_made(self, transport):
                self.transport = transport
            def datagram_received(self, data, addr):
                self.received.append(data)
                self.transport.sendto(PRIVATE, addr)
            def connection_lost(self, exc):
                self.closed.set_result(None)
        server = Server()
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: server, local_addr=('127.0.0.1', 0))
        try:
            with patch.object(qv, 'DESTINATION', transport.get_extra_info('sockname')), patch.object(
                    qv, 'LISTEN_PORTS', (0, 0)), patch.object(qv, 'TIMEOUT', .15):
                result = await qv.discover_qv('127.0.0.1', include_response=True)
            self.assertEqual(server.received, [b'ASZENO.SEARCH.V4.1'])
            self.assertEqual(result['responses'][0]['response_hex'], PRIVATE.hex())
            self.assertEqual(result['collection_end_reason'], 'deadline')
        finally:
            transport.close()
            await asyncio.wait_for(server.closed, 1)


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_privacy_and_no_startup_discovery(self):
        entry = SimpleNamespace(data={'host': '192.0.2.1'}, options={})
        hub = hub_module.R002InvestigationHub(None, entry)
        network = FakeNetwork([(PRIVATE, ('192.0.2.1', 5000), 5003)])
        with patch.object(qv, '_open_listener', side_effect=network.open) as opening, patch.object(qv, 'TIMEOUT', .01):
            await hub.start()
            opening.assert_not_called()
            with self.assertNoLogs('welcomeeye_offline_fixture.r002', 'DEBUG'):
                result = await hub.discover_qv(include_response=True)
        self.assertIn('response_hex', result['responses'][0])
        stored = json.dumps([hub.diagnostics(), hub._per_type, hub._qv_discovery, entry.options])
        for forbidden in (PRIVATE.hex(), 'response_hex', 'SYNTHETIC_PRIVATE_UID', '192.0.2.1', 'responses'):
            self.assertNotIn(forbidden, stored)
        self.assertEqual(hub.status, 'qv_observed')
        self.assertEqual(hub.qv_discovery_runs, 1)
        self.assertIsNone(hub._task)
        await hub.stop()

    async def test_unload_and_mutual_exclusion_with_all_investigation_actions(self):
        hub = hub_module.R002InvestigationHub(None, SimpleNamespace(data={'host': '192.0.2.1'}))
        network = FakeNetwork()
        await hub.start()
        callback = Mock()
        hub.subscribe(callback)
        with patch.object(qv, '_open_listener', side_effect=network.open):
            task = asyncio.create_task(hub.discover_qv(include_response=True))
            await network.sending.wait()
            for call in (hub.discover_qv, hub.probe, hub.check_certificate):
                with self.assertRaises(RuntimeError):
                    await call()
            await hub.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        network.check_closed(self)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(len(network.sent), 1)
        self.assertIsNone(hub._task)
        with self.assertRaises(RuntimeError):
            await hub.discover_qv()
