"""Conservative R002 endpoint selection from one fresh, bounded QV discovery.

Discovery is not authenticated. It is a consistency gate before pinned/trusted
HTTPS, never a source of credentials or proof of device identity. Only the
reported IDS9417AW shape is enabled. No address, UID or packet is persisted.
"""
from ipaddress import IPv4Address

from ..connect3.cgi import CGIError
from ..connect3.discovery import DiscoveryDecodeError, decode_datagram
from .qv_discovery import discover_qv


async def resolve_endpoint(host, observation):
    observation.update(endpoint_source='fresh_qv_discovery',
                       discovery_model_matched=False, endpoint_profile=None)
    result = await discover_qv(host, include_response=True)
    observation['discovery_request_count'] = result.get('request_sent_count', 0)
    observation['discovery_datagrams_seen'] = result.get('datagrams_seen', 0)
    if result.get('status') != 'observed':
        raise CGIError('r002_discovery_failed')
    identities, profiles = set(), set()
    for response in result.get('responses', ()):
        if response.get('truncated'):
            raise CGIError('r002_discovery_truncated')
        try:
            record = decode_datagram(bytes.fromhex(response['response_hex']))
        except (DiscoveryDecodeError, ValueError, KeyError):
            raise CGIError('r002_discovery_decode_failed') from None
        if record.address != str(IPv4Address(host)):
            raise CGIError('r002_discovery_address_mismatch')
        identities.add((record.uid, record.device_type))
        profiles.add((record.cgi_port, record.stream_port, record.tls_media_port, record.channels))
    if not identities:
        raise CGIError('r002_discovery_not_observed')
    if len(identities) != 1 or len(profiles) != 1:
        raise CGIError('r002_discovery_ambiguous')
    if next(iter(identities))[1] != 'IDS9417AW':
        raise CGIError('r002_discovery_model_not_supported')
    observation['discovery_model_matched'] = True
    if profiles != {(443, 0, 0, 1)}:
        raise CGIError('r002_discovery_endpoint_not_supported')
    # WelcomeEye QvPlayerCore.java:914 selects SDKConst.QV_MEDIA_PORT (34567)
    # when supportTls() is false. This is not a TLS failure fallback or scan.
    observation.update(endpoint_profile='r002_qv_apk_tcp_34567',
                       media_transport='r002_tcp', media_tls_advertised=False,
                       udt_used=False)
    return {'cgi_port': 443, 'port': 34567, 'transport': 'r002_tcp'}
