"""Owner-requested, credential-free Connect 3 endpoint selection.

The optional LAN lookup uses the existing app-derived QV query once. Its
results only suggest an IPv4 address; they never establish trust or support.
The TCP check sends one public QV SETUP and no login, PLAY or output command.
"""
import asyncio
from ipaddress import IPv4Address
import socket

from ..r002 import qv_discovery as discovery
from ..r002.transport import close_writer
from ..snapshot import _finish_task
from .discovery import DiscoveryDecodeError, decode_datagram
from .protocol import MediaProtocolError, build_setup_request, parse_setup_response

TCP_PORT = 34567
TCP_TIMEOUT = 3.0


def _address(host):
    address = IPv4Address(host)
    if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
        raise ValueError('unicast_address_required')
    return str(address)


async def probe_tcp_setup(host):
    """At most one fixed-port, 32-byte exchange; no secret argument exists."""
    address = _address(host)
    writer = None
    try:
        async with asyncio.timeout(TCP_TIMEOUT):
            reader, writer = await asyncio.open_connection(address, TCP_PORT, family=socket.AF_INET)
            writer.write(build_setup_request())
            await writer.drain()
            setup = parse_setup_response(await reader.readexactly(32))
            return setup.result == 0 and (setup.encryption_mode, setup.sha_mode) == (2, 1)
    except (OSError, TimeoutError, asyncio.IncompleteReadError, MediaProtocolError):
        return False
    finally:
        if writer is not None:
            await _finish_task(asyncio.create_task(close_writer(writer)), cancel_on_cancel=False)


class _Candidates(discovery._Collection):
    """Reuse listener lifecycle while retaining only validated candidate IPs."""
    def __init__(self):
        super().__init__('', False)
        self.hosts = set()

    def receive(self, data, addr, local_port):
        if not self.active or self.done.done():
            return
        self.result['datagrams_seen'] += 1
        try:
            source = _address(addr[0])
            record = decode_datagram(data)
            if record.address == source and record.device_type == 'IDS94E6SW':
                self.hosts.add(source)
        except (ValueError, DiscoveryDecodeError):
            pass
        if len(self.hosts) >= discovery.MAX_RESPONSES:
            self.finish('response_limit')
        elif self.result['datagrams_seen'] >= discovery.MAX_DATAGRAMS:
            self.finish('datagram_limit')


async def discover_candidates():
    """One optional bounded broadcast; manual/VLAN setup never calls this."""
    collection = _Candidates()
    endpoints = []
    try:
        async with asyncio.timeout(discovery.TIMEOUT):
            for port in discovery.LISTEN_PORTS:
                endpoints.append(await discovery._open_listener(collection, port))
            endpoints[1][0].sendto(discovery.REQUEST, discovery.DESTINATION)
            collection.active = True
            await collection.done
    except (OSError, TimeoutError):
        pass
    finally:
        collection.active = False
        if not collection.done.done():
            collection.done.cancel()
        async def close():
            for transport, _ in endpoints:
                transport.close()
            if endpoints:
                try:
                    async with asyncio.timeout(discovery.CLOSE_TIMEOUT):
                        await asyncio.gather(*(protocol.closed for _, protocol in endpoints))
                except TimeoutError:
                    for transport, _ in endpoints:
                        transport.abort()
        await _finish_task(asyncio.create_task(close()), cancel_on_cancel=False)
    return sorted(collection.hosts, key=IPv4Address)
