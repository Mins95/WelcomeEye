"""Explicit QV LAN discovery observation, without login, decryption or media."""
import asyncio
from ipaddress import IPv4Address
import socket
import time

REQUEST = b'ASZENO.SEARCH.V4.1'
DESTINATION = ('255.255.255.255', 5000)
LISTEN_PORTS = (5001, 5003)
TIMEOUT = 3.0
CLOSE_TIMEOUT = 1.0
MAX_RESPONSES = 4
MAX_RESPONSE_BYTES = 2048
MAX_DATAGRAMS = 64


def safe_summary(result):
    """Unknown datagram bytes/fields are never retained by the hub."""
    return {key: result[key] for key in (
        'status', 'last_stage', 'last_error_type', 'collection_end_reason',
        'elapsed_ms', 'request_sent_count', 'datagrams_seen', 'ignored_datagrams',
        'matching_datagrams', 'recognized_prefixes', 'bytes_collected',
        'truncated_datagrams')}


class _Collection:
    def __init__(self, address, include_response):
        self.address = address
        self.include_response = include_response
        self.started = time.monotonic()
        self.active = False
        self.done = asyncio.get_running_loop().create_future()
        self.responses = []
        self.result = dict(status='failed', last_stage='binding', last_error_type=None,
            collection_end_reason=None, request_sent_count=0, datagrams_seen=0,
            ignored_datagrams=0, matching_datagrams=0, recognized_prefixes=0,
            bytes_collected=0, truncated_datagrams=0)

    def finish(self, reason):
        if not self.done.done():
            self.result['collection_end_reason'] = reason
            self.done.set_result(None)

    def receive(self, data, addr, local_port):
        if not self.active or self.done.done():
            return
        result = self.result
        result['datagrams_seen'] += 1
        if addr[0] != self.address:
            # Discard other devices' bytes and addresses immediately.
            result['ignored_datagrams'] += 1
        else:
            clipped = data[:MAX_RESPONSE_BYTES]
            variant = ('v4.1' if data.startswith(b'ASZENO.SEARCH.V4.1') else
                       'v4' if data.startswith(b'ASZENO.SEARCH.V4') else 'unknown')
            truncated = len(data) > MAX_RESPONSE_BYTES
            result['matching_datagrams'] += 1
            result['recognized_prefixes'] += int(variant != 'unknown')
            result['bytes_collected'] += len(clipped)
            result['truncated_datagrams'] += int(truncated)
            item = dict(local_port=local_port, prefix_variant=variant,
                datagram_bytes_received=len(data), bytes_collected=len(clipped),
                truncated=truncated, received_after_ms=round((time.monotonic()-self.started)*1000))
            if self.include_response:
                item['response_hex'] = clipped.hex()
            self.responses.append(item)
            if len(self.responses) >= MAX_RESPONSES:
                self.finish('response_limit')
        if result['datagrams_seen'] >= MAX_DATAGRAMS:
            self.finish('datagram_limit')

    def error(self, exc):
        if self.active and not self.done.done():
            self.result.update(status='failed', last_error_type=type(exc).__name__)
            self.finish('network_error')


class _Listener(asyncio.DatagramProtocol):
    def __init__(self, collection, port):
        self.collection, self.port = collection, port
        self.closed = asyncio.get_running_loop().create_future()

    def datagram_received(self, data, addr):
        self.collection.receive(data, addr, self.port)

    def error_received(self, exc):
        self.collection.error(exc)

    def connection_lost(self, exc):
        if self.collection.active:
            self.collection.error(exc or ConnectionError())
        if not self.closed.done():
            self.closed.set_result(None)


async def _open_listener(collection, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # No reuse: a busy local port must fail before any broadcast is sent.
        sock.bind(('0.0.0.0', port))
        return await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _Listener(collection, port), sock=sock)
    except BaseException:
        sock.close()
        raise


async def discover_qv(host, *, include_response=False):
    """One broadcast, two app-derived receive ports, bounded explicit observation.

    Responses are restricted to the configured IPv4 source, not authenticated.
    No inference about R002 support, metadata contents or media availability.
    """
    address = IPv4Address(host)
    if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
        raise ValueError('A unicast device IPv4 address is required')
    collection = _Collection(str(address), include_response)
    endpoints = []
    result = collection.result
    deadline = asyncio.timeout(TIMEOUT)
    try:
        async with deadline:
            for port in LISTEN_PORTS:
                endpoints.append(await _open_listener(collection, port))
            result['last_stage'] = 'sending'
            # The app sends through its 5003 listener. Never retry or send V1/V3.
            endpoints[1][0].sendto(REQUEST, DESTINATION)
            result.update(request_sent_count=1, last_stage='collecting', status='observed')
            collection.active = True
            await collection.done
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if isinstance(exc, TimeoutError) and deadline.expired():
            # A receive/error callback can finish in the same event-loop turn
            # as the deadline. Preserve that already-observed end condition.
            if result['collection_end_reason'] is None:
                result['collection_end_reason'] = 'deadline'
                if result['last_stage'] != 'collecting':
                    result['last_error_type'] = 'TimeoutError'
        else:
            result.update(status='failed', last_error_type=type(exc).__name__,
                          collection_end_reason='network_error')
    finally:
        collection.active = False
        if not collection.done.done():
            collection.done.cancel()
        for transport, _ in endpoints:
            transport.close()
        if endpoints:
            try:
                async with asyncio.timeout(CLOSE_TIMEOUT):
                    await asyncio.gather(*(protocol.closed for _, protocol in endpoints))
            except TimeoutError:
                for transport, _ in endpoints:
                    transport.abort()
        result['elapsed_ms'] = round((time.monotonic()-collection.started)*1000)
    return {**result, 'protocol': 'qv_lan_discovery', 'responses': collection.responses,
            'device_authenticated': False, 'metadata_decoded': False}
