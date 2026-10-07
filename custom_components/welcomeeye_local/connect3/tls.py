"""Media transports; TLS trust remains mandatory unless R002 selects TCP."""
import asyncio
from hashlib import sha256
from hmac import compare_digest
from ipaddress import IPv4Address
import re
import socket
import ssl

from ..r002.certificate import MAX_CERTIFICATE_SIZE
from ..r002.transport import close_writer

CONNECT_TIMEOUT = 5.0
R002_TCP_PORT = 34567


class MediaTLSFailure(ConnectionError):
    """Only fixed reason labels; no endpoint, certificate or credential."""


def _context(pin):
    if not pin:
        return ssl.create_default_context()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def open_media_tls(host, port, pin, observation):
    """A CGI pin is usable only if this endpoint's actual DER hash matches it."""
    address = IPv4Address(host)
    if (address.is_multicast or address.is_unspecified or int(address) == 0xffffffff
            or type(port) is not int or not 1 <= port <= 65535
            or (pin and not re.fullmatch(r'[a-fA-F0-9]{64}', pin))):
        raise MediaTLSFailure('invalid_media_endpoint')
    writer = None
    observation.update(stage='media_tcp_connect', media_tls_verified=False,
                       media_tls_policy='certificate_pin' if pin else 'system_ca')
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            context = await asyncio.to_thread(_context, pin)
            reader, writer = await asyncio.open_connection(str(address), port,
                family=socket.AF_INET, limit=65536)
            observation['stage'] = 'media_tls_handshake'
            await writer.start_tls(context, server_hostname=str(address),
                                   ssl_handshake_timeout=3.0)
            peer = writer.get_extra_info('ssl_object')
            der = peer.getpeercert(binary_form=True) if peer is not None else None
            if type(der) is not bytes or not 0 < len(der) <= MAX_CERTIFICATE_SIZE:
                raise MediaTLSFailure('missing_media_certificate')
            if pin and not compare_digest(sha256(der).digest(), bytes.fromhex(pin)):
                raise MediaTLSFailure('media_certificate_pin_mismatch')
            observation.update(stage='media_tls_verified', media_tls_verified=True)
            return reader, writer
    except BaseException:
        if writer is not None:
            await close_writer(writer)
        raise


async def open_r002_media_tcp(host, port, observation):
    """Open only the APK's explicitly selected R002 no-TLS media endpoint.

    The hub must verify fresh matching R002 discovery before choosing this
    policy. This is never a recovery path for an unsuccessful TLS connection.
    CGI authentication still uses independently verified HTTPS.
    """
    try:
        address = IPv4Address(host)
    except (TypeError, ValueError):
        raise MediaTLSFailure('invalid_media_endpoint') from None
    if (address.is_multicast or address.is_unspecified or int(address) == 0xffffffff
            or type(port) is not int or port != R002_TCP_PORT):
        raise MediaTLSFailure('invalid_r002_media_endpoint')
    observation.update(stage='media_tcp_connect', media_transport='r002_tcp',
                       media_tls_verified=False, media_tls_policy='r002_apk_plain_tcp')
    async with asyncio.timeout(CONNECT_TIMEOUT):
        reader, writer = await asyncio.open_connection(str(address), port,
            family=socket.AF_INET, limit=65536)
    observation['stage'] = 'media_tcp_connected'
    return reader, writer
