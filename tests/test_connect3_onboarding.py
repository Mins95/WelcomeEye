"""Credential-free synthetic onboarding boundaries; no sockets reach a device."""
import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_connect3_discovery import synthetic_packet, synthetic_record
from test_r002_qv_discovery import FakeNetwork

module = load('connect3.onboarding')


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_tcp_setup_is_one_public_request_on_only_fixed_port(self):
        reader = asyncio.StreamReader()
        reader.feed_data(bytes([0xa9]) + bytes(9) + b'\2\1' + bytes(20))
        writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connect:
            self.assertTrue(await module.probe_tcp_setup('192.0.2.1'))
        connect.assert_awaited_once_with('192.0.2.1', 34567, family=socket.AF_INET)
        writer.write.assert_called_once_with(b'\xa9' + bytes(31))
        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()

    async def test_tcp_rejected_weak_or_malformed_setup_is_not_retried(self):
        for raw in (bytes(32), b'\xa9' + bytes(31), b'\xa9' + bytes(8) + b'\1\2\1' + bytes(20)):
            reader = asyncio.StreamReader()
            reader.feed_data(raw)
            writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
            with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connect:
                self.assertFalse(await module.probe_tcp_setup('192.0.2.1'))
            connect.assert_awaited_once()
            writer.write.assert_called_once()
            writer.close.assert_called_once()

    async def test_tcp_timeout_and_cancel_both_close_without_play(self):
        for cancel in (False, True):
            reader = asyncio.StreamReader()
            written = asyncio.Event()
            writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
            writer.write.side_effect = lambda _: written.set()
            with patch.object(module.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))), \
                    patch.object(module, 'TCP_TIMEOUT', 1 if cancel else .01):
                task = asyncio.create_task(module.probe_tcp_setup('192.0.2.1'))
                await written.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertFalse(await task)
            writer.write.assert_called_once_with(b'\xa9' + bytes(31))
            writer.close.assert_called_once()

    async def test_invalid_address_never_opens_tcp(self):
        with patch.object(module.asyncio, 'open_connection', AsyncMock()) as connect:
            for address in ('bad', '0.0.0.0', '224.0.0.1', '255.255.255.255'):
                with self.assertRaises(ValueError):
                    await module.probe_tcp_setup(address)
        connect.assert_not_called()

    @staticmethod
    def packet(last=1, model='IDS94E6SW'):
        record = synthetic_record()
        record[0x64:0x68] = bytes((192, 0, 2, last))
        record[0x188:0x19c] = model.encode().ljust(20, b'\0')
        return synthetic_packet(record)

    async def test_optional_discovery_one_query_only_valid_model_and_matching_source(self):
        events = [(self.packet(1), ('192.0.2.1', 5000), 5003),
                  (self.packet(1), ('192.0.2.1', 5000), 5001),
                  (self.packet(2), ('192.0.2.99', 5000), 5003),
                  (self.packet(3, 'IDS9417AW'), ('192.0.2.3', 5000), 5003),
                  (b'PRIVATE_INVALID', ('192.0.2.4', 5000), 5003)]
        network = FakeNetwork(events, module=module.discovery)
        with patch.object(module.discovery, '_open_listener', side_effect=network.open), \
                patch.object(module.discovery, 'TIMEOUT', .02):
            self.assertEqual(await module.discover_candidates(), ['192.0.2.1'])
        self.assertEqual(network.sent, [(5003, b'ASZENO.SEARCH.V4.1', ('255.255.255.255', 5000))])
        network.check_closed(self)
        self.assertEqual(network.endpoints[0][1].collection.responses, [])

    async def test_discovery_bound_and_cancel_cleanup(self):
        events = [(self.packet(index), (f'192.0.2.{index}', 5000), 5003) for index in range(1, 10)]
        network = FakeNetwork(events, module=module.discovery)
        with patch.object(module.discovery, '_open_listener', side_effect=network.open):
            self.assertEqual(await module.discover_candidates(), [f'192.0.2.{index}' for index in range(1, 5)])
        network.check_closed(self)
        network = FakeNetwork(module=module.discovery)
        with patch.object(module.discovery, '_open_listener', side_effect=network.open):
            task = asyncio.create_task(module.discover_candidates())
            await network.sending.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        network.check_closed(self)


if __name__ == '__main__':
    unittest.main()
