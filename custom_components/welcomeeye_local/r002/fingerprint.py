"""Conservative setup signature: two TCP connects and TLS only, no app messages."""
import asyncio
from ipaddress import IPv4Address
import logging
import socket
import ssl
import time

from .transport import close_writer

_LOGGER = logging.getLogger(__name__)


def _tls_context():
    # Self-signed identity is a clue, NEVER authentication. No HTTP/credentials.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _certificate_signature(der):
    from .certificate import metadata
    return metadata(der)


async def check_certificate(host):
    """Explicit TLS-only recheck; no UDP, 8765 message, HTTP or identity mutation."""
    address = str(IPv4Address(host))
    started = time.monotonic()
    details = {'tcp_443_reachable': False, 'tls_443_handshake_ok': False,
        'tls_certificate_cn': None, 'tls_certificate_issuer_cn': None,
        'certificate_metadata_status': 'not_received', 'certificate_serial_status': 'unknown',
        'certificate_parser': 'bounded_der_metadata_v1', 'certificate_parse_error_type': None,
        'certificate_trust_authenticated': False, 'certificate_check_error_type': None}
    writer = None
    try:
        async with asyncio.timeout(3.0):
            _reader, writer = await asyncio.open_connection(address, 443, family=socket.AF_INET)
            details['tcp_443_reachable'] = True
            context = await asyncio.to_thread(_tls_context)
            await writer.start_tls(context, server_hostname=address, ssl_handshake_timeout=2.0)
            details['tls_443_handshake_ok'] = True
            der = writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
            try:
                details.update(await asyncio.to_thread(_certificate_signature, der))
            except ValueError:
                details.update(certificate_metadata_status='invalid',
                    certificate_parse_error_type='CertificateMetadataError')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        details['certificate_check_error_type'] = type(exc).__name__
    finally:
        if writer is not None:
            await close_writer(writer)
    details['certificate_check_elapsed_ms'] = round((time.monotonic()-started)*1000)
    _LOGGER.debug('r002.fingerprint.tls_signature metadata_status=%s matched=%s',
                  details['certificate_metadata_status'], details['tls_certificate_cn'] == 'eziotest')
    return details


async def fingerprint(host, discovery_probe_count):
    address = str(IPv4Address(host))
    started = time.monotonic()
    details = {
        'detected': False, 'detection_confidence': 'unknown',
        'detection_source': 'udp_timeout_tcp_tls',
        'identity_source': 'provisional_host_hash',
        'legacy_udp1500_response': False, 'legacy_udp1500_probe_count': discovery_probe_count,
        'tcp_8765_reachable': False, 'tcp_443_reachable': False,
        'tcp_6987_reachable': None, 'tcp_34567_reachable': None,
        'tls_443_handshake_ok': False, 'tls_certificate_cn': None,
        'tls_certificate_issuer_cn': None, 'fingerprint_runs': 1,
        'fingerprint_elapsed_ms': 0, 'fingerprint_last_error_type': None,
    }
    _LOGGER.debug('r002.fingerprint.start')
    _LOGGER.debug('r002.fingerprint.legacy_discovery_timeout probes=%d', discovery_probe_count)
    writer = None
    try:
        async with asyncio.timeout(3.0):
            _reader, writer = await asyncio.open_connection(address, 8765, family=socket.AF_INET)
            details['tcp_8765_reachable'] = True
            _LOGGER.debug('r002.fingerprint.port_reachable port=8765')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        details['fingerprint_last_error_type'] = type(exc).__name__
    finally:
        if writer is not None:
            await close_writer(writer)
    details.update(await check_certificate(host))
    details['fingerprint_last_error_type'] = (details['certificate_check_error_type']
        or details['certificate_parse_error_type'] or details['fingerprint_last_error_type'])
    details['detected'] = bool(details['tcp_8765_reachable'] and
        details['tls_443_handshake_ok'] and details['tls_certificate_cn'] == 'eziotest')
    if details['detected']:
        details['detection_confidence'] = 'probable'
    details['fingerprint_elapsed_ms'] = round((time.monotonic() - started) * 1000)
    _LOGGER.debug('r002.fingerprint.result detected=%s confidence=%s',
                  details['detected'], details['detection_confidence'])
    return details
