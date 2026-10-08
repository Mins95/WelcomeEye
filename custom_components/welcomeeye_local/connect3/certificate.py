"""Explicit CGI TLS inspection, with no HTTP, credential or media message."""
import asyncio
import errno
from hashlib import sha256
from ipaddress import IPv4Address
import socket
import ssl
import time

from ..r002.certificate import MAX_CERTIFICATE_SIZE, metadata
from ..r002.transport import close_writer

TIMEOUT = 3.0


def failure_reason(exc):
    """Categorize locally, never returning exception messages or raw errno."""
    if isinstance(exc, TimeoutError):
        return 'timeout'
    if isinstance(exc, ssl.SSLError):
        return 'tls_handshake_failed'
    if isinstance(exc, ValueError):
        return 'certificate_invalid'
    codes = {getattr(exc, 'errno', None), getattr(exc, 'winerror', None)}
    if isinstance(exc, ConnectionRefusedError) or codes & {errno.ECONNREFUSED, 61, 111, 10061}:
        return 'connection_refused'
    if codes & {errno.ENETUNREACH, errno.EHOSTUNREACH, 10050, 10051, 10064, 10065}:
        return 'network_unreachable'
    if codes & {errno.ETIMEDOUT, 60, 110, 10060}:
        return 'timeout'
    if isinstance(exc, ConnectionResetError) or codes & {errno.ECONNRESET, 54, 104, 10054}:
        return 'connection_reset'
    return 'network_error'


def _inspection_context():
    # Inspection only, never used by authenticated CGI. Local context only.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def inspect_certificate(host, *, port=443, include_details=False):
    address = IPv4Address(host)
    if (address.is_multicast or address.is_unspecified or int(address) == 0xffffffff
            or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('Invalid certificate endpoint')
    result = dict(status='failed', last_stage='tcp_connect', tcp_connected=False,
                  tls_handshake_ok=False, certificate_metadata_status='not_received',
                  certificate_trust_authenticated=False, certificate_pin_saved=False,
                  certificate_port=port, last_error_type=None, last_error_reason=None)
    writer = None
    started = time.monotonic()
    try:
        async with asyncio.timeout(TIMEOUT):
            _, writer = await asyncio.open_connection(str(address), port, family=socket.AF_INET)
            result.update(tcp_connected=True, last_stage='tls_handshake')
            context = await asyncio.to_thread(_inspection_context)
            await writer.start_tls(context, server_hostname=str(address), ssl_handshake_timeout=2.0)
            result.update(tls_handshake_ok=True, last_stage='certificate_metadata')
            ssl_object = writer.get_extra_info('ssl_object')
            if ssl_object is None:
                raise ValueError('Missing TLS certificate')
            der = ssl_object.getpeercert(binary_form=True)
            if type(der) is not bytes or not 0 < len(der) <= MAX_CERTIFICATE_SIZE:
                raise ValueError('Invalid certificate size')
            # Bounded existing DER parser returns only known labels/sign status.
            result.update(await asyncio.to_thread(metadata, der))
            result['status'] = 'observed'
            if include_details:
                result['certificate_sha256'] = sha256(der).hexdigest()
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError) as exc:
        result['last_error_reason'] = failure_reason(exc)
        result['last_error_type'] = ('TimeoutError' if isinstance(exc, TimeoutError)
            else 'TLSHandshakeError' if isinstance(exc, ssl.SSLError)
            else 'CertificateMetadataError' if isinstance(exc, ValueError) else 'NetworkError')
        if isinstance(exc, ValueError):
            result['certificate_metadata_status'] = 'invalid'
    finally:
        if writer is not None:
            await close_writer(writer)
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
    return result
