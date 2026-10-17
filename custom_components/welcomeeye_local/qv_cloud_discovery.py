"""Bounded QV service directory lookup, without account or device credentials.

Door Connect QvP2PClientV2.createServiceUrl and CoreApplication select HTTPS
sh2.qvcloud.net:443. Native DevClientMst::OnResult (0x5199f8) sets GET
/mst/query and application/xml;charset=utf-8; ServerResource::BuildHlr
(0x51dc50) supplies its XML body. No guessed endpoint or HTTP fallback is used.
"""
import asyncio
import re
from xml.etree import ElementTree as ET

from .qv_cloud_tls import bootstrap_ssl_context
from .qv_cloud_protocol import (
    CONNECT3_PROFILE, R002_PROFILE, QvCloudError, QvCloudServices,
    make_client_id, validate_service_url,
)

BOOTSTRAP_URL = 'https://sh2.qvcloud.net/mst/query'
CONTENT_TYPE = 'application/xml;charset=utf-8'
MAX_DISCOVERY_BYTES = 65536
DISCOVERY_TIMEOUT = 15
SERVICE_TYPES = 'userapp,sec-userapp,alarmapp,sec-alarmapp,oauth2,openapi'
_CLIENT_ID = re.compile(r'003-(?:4123|4058)-[a-f0-9-]{36}', re.ASCII)
_REQUIRED = {'sec-userapp': 'auth_url', 'sec-alarmapp': 'alarm_url',
             'oauth2': 'oauth_url', 'openapi': 'openapi_url'}


def build_discovery_request(profile, client_id, region_id=0):
    """Request only account/alarm/push service locations for our installation."""
    if (profile not in (CONNECT3_PROFILE, R002_PROFILE)
            or type(region_id) is not int or not 0 <= region_id <= 65535
            or not isinstance(client_id, str) or not _CLIENT_ID.fullmatch(client_id)
            or not client_id.startswith(f'003-{profile.app_id}-')):
        raise QvCloudError('invalid_input')
    root = ET.Element('envelope')
    header = ET.SubElement(root, 'header')
    for key, value in (('flag', 'tdkcloud'), ('command', 'query-hlrv2'), ('seq', '1')):
        ET.SubElement(header, key).text = value
    content = ET.SubElement(root, 'content')
    for key, value in (('server-type', SERVICE_TYPES), ('oem', profile.oem_id),
                       ('devid', ''), ('public-ip', ''), ('client-id', client_id),
                       ('regionid', str(region_id)), ('version', '4456')):
        ET.SubElement(content, key).text = value
    return ET.tostring(root, encoding='utf-8', xml_declaration=True,
                       short_empty_elements=False)


def _one(parent, name):
    children = parent.findall(name)
    if len(children) != 1 or list(children[0]):
        raise QvCloudError('invalid_response')
    return (children[0].text or '').strip()


def _region(value):
    if not re.fullmatch(r'[0-9]{1,5}', value) or not 0 <= int(value) <= 65535:
        raise QvCloudError('invalid_response')
    return int(value)


def parse_discovery_response(payload):
    """Select the explicitly secure auth/alarm variants and HTTPS API services."""
    if not isinstance(payload, bytes):
        raise QvCloudError('invalid_response')
    if len(payload) > MAX_DISCOVERY_BYTES:
        raise QvCloudError('response_too_large')
    if b'<!' in payload or b'\x00' in payload:
        raise QvCloudError('invalid_response')
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, ValueError):
        raise QvCloudError('invalid_response') from None
    headers, contents = root.findall('header'), root.findall('content')
    if root.tag != 'envelope' or len(headers) != 1 or len(contents) != 1:
        raise QvCloudError('invalid_response')
    if _one(headers[0], 'result') != '0':
        raise QvCloudError('provider_rejected')
    if _one(headers[0], 'seq') != '1':
        raise QvCloudError('invalid_response')
    content = contents[0]
    region_id = _region(_one(content, 'client-regionid'))
    servers = content.findall('server')
    if len(servers) > 64:
        raise QvCloudError('invalid_response')
    selected = {}
    for server in servers:
        kind = _one(server, 'server-type')
        if kind not in _REQUIRED:
            continue
        if kind in selected:
            raise QvCloudError('invalid_response')
        if _one(server, 'query-result') != '1':
            raise QvCloudError('provider_rejected')
        # Each service can be hosted in a different provider region. Only the
        # directory's client-regionid is the account namespace selection.
        _region(_one(server, 'regionid'))
        selected[kind] = validate_service_url(_one(server, 'url'),
            alarm=(kind == 'sec-alarmapp'))
    if selected.keys() != _REQUIRED.keys():
        raise QvCloudError('invalid_service')
    return QvCloudServices(**{_REQUIRED[k]: v for k, v in selected.items()},
                           region_id=region_id)


async def async_resolve_services(profile, region_id=0, *, client_id, session):
    """One verified HTTPS request; no redirects, retries, account or device IDs."""
    body = build_discovery_request(profile, client_id, region_id)
    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT):
            async with session.request('GET', BOOTSTRAP_URL, data=body,
                    headers={'Content-Type': CONTENT_TYPE},
                    allow_redirects=False, ssl=bootstrap_ssl_context(BOOTSTRAP_URL)) as response:
                if response.status != 200:
                    raise QvCloudError('connection_failed')
                chunks, length = [], 0
                async for chunk in response.content.iter_chunked(8192):
                    length += len(chunk)
                    if length > MAX_DISCOVERY_BYTES:
                        raise QvCloudError('response_too_large')
                    chunks.append(chunk)
                return parse_discovery_response(b''.join(chunks))
    except QvCloudError:
        raise
    except TimeoutError:
        raise QvCloudError('request_timeout') from None
    except (OSError, ValueError):
        raise QvCloudError('connection_failed') from None


async def resolve_services(profile, installation_id, region_id=0):
    """Own a short-lived, cookie-free session for initial/region discovery."""
    import aiohttp
    client_id = make_client_id(installation_id, profile)
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False, timeout=aiohttp.ClientTimeout(total=DISCOVERY_TIMEOUT)) as session:
            return await async_resolve_services(profile, region_id,
                client_id=client_id, session=session)
    except aiohttp.ClientError:
        raise QvCloudError('connection_failed') from None
