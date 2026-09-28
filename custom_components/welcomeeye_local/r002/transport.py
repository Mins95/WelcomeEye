"""One explicit request per connection; strict deadline, no retries or payload logs."""
import asyncio
from dataclasses import asdict
from hashlib import sha256
from ipaddress import IPv4Address
import logging
import socket
import time

from .protocol import HEADER, R002ProtocolError, inspect_prefix, parse_header, request

_LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT = 3.0
CLOSE_TIMEOUT = 1.0


def new_counters():
    return dict.fromkeys(('connects', 'disconnects', 'remote_eof', 'timeouts',
        'bytes_rx', 'bytes_tx', 'malformed_headers', 'oversized_frames',
        'unexpected_types', 'unexpected_versions'), 0)


async def close_writer(writer):
    writer.close()
    try:
        async with asyncio.timeout(CLOSE_TIMEOUT):
            await writer.wait_closed()
    except (OSError, TimeoutError):
        writer.transport.abort()


async def probe_one(host, message_type, counters, *, include_header=False):
    """One existing read, optionally returning its prefix to the explicit caller."""
    packet = request(message_type)  # Validate BEFORE opening any socket.
    address = str(IPv4Address(host))
    started = time.monotonic()
    writer = None
    stage = 'connecting'
    result = {'requested_type': message_type, 'status': 'failed', 'last_error_type': None,
              'header_valid': None, 'header_validation_errors': []}
    prefix = None

    async def read_exact(reader, size):
        try:
            data = await reader.readexactly(size)
        except asyncio.IncompleteReadError as exc:
            counters['bytes_rx'] += len(exc.partial)
            raise
        counters['bytes_rx'] += len(data)
        return data

    try:
        _LOGGER.debug('r002.transport.connecting type=%d', message_type)
        async with asyncio.timeout(REQUEST_TIMEOUT):
            reader, writer = await asyncio.open_connection(address, 8765, family=socket.AF_INET)
            counters['connects'] += 1
            _LOGGER.debug('r002.transport.connected type=%d', message_type)
            stage = 'sending'
            counters['bytes_tx'] += len(packet)
            writer.write(packet)
            await writer.drain()
            stage = 'response_header'
            try:
                prefix = await read_exact(reader, HEADER.size)
            except asyncio.IncompleteReadError as exc:
                prefix = exc.partial  # Never pad a short prefix or read again.
                raise
            candidate, errors = inspect_prefix(prefix, message_type)
            result.update(header_valid=not errors, header_validation_errors=errors)
            header = parse_header(prefix, message_type)
            _LOGGER.debug('r002.transport.response_header requested_type=%d valid=true', message_type)
            stage = 'response_body'
            body = await read_exact(reader, header.declared_length)
            result.update(asdict(header), received_length=len(body),
                          nonzero_bytes=sum(byte != 0 for byte in body),
                          body_sha256=sha256(body).hexdigest(), status='ok')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        result['last_error_type'] = type(exc).__name__
        if isinstance(exc, asyncio.IncompleteReadError):
            counters['remote_eof'] += 1
            _LOGGER.debug('r002.transport.remote_eof type=%d', message_type)
        elif isinstance(exc, TimeoutError):
            counters['timeouts'] += 1
            _LOGGER.debug('r002.transport.timeout type=%d', message_type)
        elif isinstance(exc, R002ProtocolError):
            counters[exc.counter] += 1
        _LOGGER.debug('r002.probe.failed type=%d stage=%s error_type=%s', message_type, stage, type(exc).__name__)
    finally:
        if writer is not None:
            try:
                await close_writer(writer)
            finally:
                counters['disconnects'] += 1
                _LOGGER.debug('r002.transport.closed type=%d', message_type)
    result.update(elapsed_ms=round((time.monotonic() - started) * 1000), last_stage=stage)
    if prefix is not None:
        candidate, errors = inspect_prefix(prefix, message_type)
        result.update(prefix_bytes_received=len(prefix), header_valid=not errors,
                      header_validation_errors=errors)
        if include_header:
            result.update(raw_header_hex=prefix.hex(), decoded_candidate=candidate)
    elif include_header:
        result.update(raw_header_hex='', prefix_bytes_received=0, decoded_candidate=None)
    return result
