"""Explicit CGI TLS inspection, with no HTTP, credential or media message."""
import asyncio
from hashlib import sha256
from ipaddress import IPv4Address
import socket
import ssl
import time

from ..r002.certificate import MAX_CERTIFICATE_SIZE, metadata
from ..r002.transport import close_writer

TIMEOUT = 3.0


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
                  last_error_type=None)
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
