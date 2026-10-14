"""Actual HA alarm service with synthetic HTTPS and native TCP/TLS peers.

Prepare manifest dependencies in a separate process first. All addresses,
credentials and certificates are synthetic; device and cloud I/O are forbidden.
"""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import faulthandler
import gc
from hashlib import sha256
import importlib
import importlib.metadata
from ipaddress import IPv4Address
import json
from pathlib import Path
import socket
import ssl
import struct
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
import weakref
from xml.etree import ElementTree as ET

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.x509.oid import NameOID
from homeassistant import core as ha_core
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES

REQUEST = b'\xa9' + bytes(8) + b'\x64' + bytes(22)
RESPONSE = b'\xa9' + bytes(8) + b'\x00\x02\x01' + bytes(20)
SECRETS = ('SYNTHETIC_ALARM_AUTH', 'SYNTHETIC_ALARM_OPENING')
STREAM_KEY = '0123456789abcdef0123456789abcdef_SYNTHETIC_ALARM_KEY'
WEAK_SETUP = {
    'legacy_v1': b'\xbe' + bytes(31),
    'legacy_v2': b'\xa9' + bytes(8) + b'\x01' + bytes(22),
    'no_encryption': b'\xa9' + bytes(10) + b'\x01' + bytes(20),
    'no_checksum': b'\xa9' + bytes(9) + b'\x02' + bytes(21),
}


def control_crypt(data, *, decrypt=False):
    """Independent synthetic peer: native ASCII key and separate CBC resets."""
    cipher = Cipher(algorithms.AES(STREAM_KEY[:32].encode()), modes.CBC(b'0' * 16))
    operation = cipher.decryptor() if decrypt else cipher.encryptor()
    return operation.update(data) + operation.finalize()


def control_record(payload, *, corrupt_digest=False):
    length = (len(payload) + 47) // 16 * 16
    header = struct.pack('<QII', 1_800_000_000, length, len(payload)) + bytes(16)
    digest = sha256(header + payload).digest()
    if corrupt_digest:
        digest = bytes((digest[0] ^ 1,)) + digest[1:]
    body = payload + digest
    return control_crypt(header) + control_crypt(body + bytes(length - len(body)))


async def read_control_record(reader):
    header = control_crypt(await reader.readexactly(32), decrypt=True)
    epoch, length, original_length = struct.unpack_from('<QII', header)
    assert epoch > 0 and header[16:] == bytes(16)
    assert 0 < original_length <= 4096 and length == (original_length + 47) // 16 * 16
    body = control_crypt(await reader.readexactly(length), decrypt=True)
    payload = body[:original_length]
    assert body[original_length:original_length + 32] == sha256(header + payload).digest()
    assert not any(body[original_length + 32:])
    return payload


async def read_native_record(reader):
    """Independent legacy peer: bounded original packet inside verified TLS."""
    header = await reader.readexactly(32)
    length = struct.unpack_from('<I', header, 4)[0]
    assert length <= 4096
    return header + await reader.readexactly(length)


class HTTPSPeer:
    """Real pinned HTTPS; only the one proven stream-key CGI is accepted."""

    def __init__(self):
        self.requests = []
        self.errors = []
        self.tasks = set()
        self.writers = set()

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        try:
            async with asyncio.timeout(3):
                head = await reader.readuntil(b'\r\n\r\n')
                lines = head.decode('ascii').split('\r\n')
                assert lines[0] == 'POST /tdkcgi HTTP/1.1'
                headers = dict(line.lower().split(':', 1) for line in lines[1:] if line)
                length = int(headers['content-length'])
                assert 0 < length <= 4096
                body = await reader.readexactly(length)
                root = ET.fromstring(body)
                assert root.tag == 'envelope'
                assert {child.tag: child.text for child in root.find('header')} == {
                    'security': 'username', 'username': 'adminapp2',
                    'password': sha256(SECRETS[0].encode()).hexdigest(), 'passwordencode': '1'}
                assert [child.tag for child in root.find('body')] == ['command', 'content']
                assert root.findtext('body/command') == 'get.device.streamkey'
                assert len(root.find('body/content')) == 0
                assert writer.get_extra_info('ssl_object') is not None
                self.requests.append(body)
                response = (f'<envelope><body><error>0</error><content><key>{STREAM_KEY}</key>'
                    '</content></body></envelope>').encode()
                writer.write((f'HTTP/1.1 200 Synthetic\r\nContent-Length: {len(response)}\r\n'
                    'Content-Type: application/xml\r\nConnection: close\r\n\r\n').encode() + response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            # A failed certificate pin must close before the HTTP request.
            pass
        except Exception as exc:
            self.errors.append(type(exc).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass
            self.tasks.discard(task)
            self.writers.discard(writer)


def server_certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic loopback')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address('127.0.0.1'))]), False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = Path(directory) / 'loopback.pem', Path(directory) / 'loopback.key'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context, sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()


class LoopbackPeer:
    def __init__(self, mode):
        self.mode = mode
        self.received = asyncio.Event()
        self.closed = asyncio.Event()
        self.requests = []
        self.errors = []
        self.tasks = set()
        self.writers = set()

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        try:
            try:
                request = await reader.readexactly(32)
            except asyncio.IncompleteReadError as exc:
                self.requests.append(exc.partial)
                return
            self.requests.append(request)
            self.received.set()
            await self.exchange(reader, writer)
            # The bounded scenario has completed its exact allowed sequence.
            # Any further write before stop/deadline would violate it.
            extra = await reader.read()
            assert extra == b'', 'Unexpected bytes after bounded control exchange'
        except Exception as exc:
            self.errors.append(type(exc).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass
            self.writers.discard(writer)
            self.tasks.discard(task)
            self.closed.set()


    async def exchange(self, reader, writer):
        if self.mode == 'accepted':
            writer.write(RESPONSE)
            await writer.drain()
        elif self.mode in WEAK_SETUP:
            writer.write(WEAK_SETUP[self.mode])
            await writer.drain()
        elif self.mode == 'invalid':
            writer.write(b'\x00' + RESPONSE[1:])
            await writer.drain()
        elif self.mode == 'eof':
            writer.write(RESPONSE[:7])
            await writer.drain()
            writer.close()


class AuthenticatedPeer(LoopbackPeer):
    def __init__(self, mode):
        super().__init__(mode)
        self.native_requests = []
        self.notified = asyncio.Event()

    async def read_record(self, reader):
        return await read_control_record(reader)

    def encode_record(self, payload, *, corrupt_digest=False):
        return control_record(payload, corrupt_digest=corrupt_digest)

    async def exchange(self, reader, writer):
        writer.write(RESPONSE)
        await writer.drain()
        if self.mode == 'auth_wrong_cgi_pin':
            return
        await self.authenticated_exchange(reader, writer)

    async def authenticated_exchange(self, reader, writer):
        credential = b'adminapp2&&' + sha256(SECRETS[0].encode()).hexdigest().encode()
        for command in (0x0A, 0xA0):
            packet = await self.read_record(reader)
            header = bytearray(32)
            header[0], header[2], header[3], header[8] = command, 1, 0x50, 0xFF
            header[1] = 3
            header[9:24] = b'A0000G0000C0000'
            header[24], header[25], header[30], header[31] = 4, 1, 0xA1, 0xAA
            struct.pack_into('<I', header, 4, len(credential))
            assert packet == bytes(header) + credential, 'Unexpected native login layout'
            self.native_requests.append(packet)
        response = bytearray(32)
        response[0], response[10] = 0xB0, 1
        if self.mode == 'auth_login_timeout':
            return
        if self.mode == 'auth_login_rejected':
            response[8], response[9] = 1, 7
        encrypted = self.encode_record(bytes(response))
        if self.mode == 'auth_login_truncated':
            writer.write(encrypted[:17])
            await writer.drain()
            writer.close()
            return
        writer.write(encrypted)
        await writer.drain()
        if self.mode == 'auth_login_rejected':
            return
        for subtype in (*range(1, 14), 157, 159, 160, 161, *range(174, 191), 192):
            packet = await self.read_record(reader)
            expected = bytearray(32)
            expected[0], expected[8], expected[12] = 0x68, 2, subtype
            assert packet == bytes(expected), 'Unexpected alarm subscription layout'
            self.native_requests.append(packet)
        notification = bytearray(32)
        notification[0], notification[12], notification[13] = 0x69, 5, 1
        private_payload = b'SYNTHETIC_PRIVATE_NOTIFICATION_BODY'
        struct.pack_into('<I', notification, 4, len(private_payload))
        notification = bytes(notification) + private_payload
        # Native packets may straddle encrypted records. No payload contents
        # are allowed into HA diagnostics, and this is not a physical bell.
        writer.write(self.encode_record(notification, corrupt_digest=True) if self.mode == 'auth_bad_digest'
            else self.encode_record(notification[:13]) + self.encode_record(notification[13:]))
        await writer.drain()
        self.notified.set()


class LegacyTLSAuthenticatedPeer(AuthenticatedPeer):
    """One setup TLS socket, then one fresh TLS socket without another setup."""

    def __init__(self, mode):
        super().__init__(mode)
        self.connections = 0
        self.setup_closed = asyncio.Event()
        self.tls_connection_count = 0

    async def read_record(self, reader):
        return await read_native_record(reader)

    def encode_record(self, payload, *, corrupt_digest=False):
        assert not corrupt_digest, 'Legacy TLS has no native digest'
        return payload

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        self.connections += 1
        connection = self.connections
        try:
            assert writer.get_extra_info('ssl_object') is not None
            self.tls_connection_count += 1
            if connection == 1:
                request = await reader.readexactly(32)
                self.requests.append(request)
                assert request == REQUEST
                self.received.set()
                writer.write(WEAK_SETUP['legacy_v2'])
                await writer.drain()
                assert await reader.read() == b'', 'Credentials sent on legacy setup socket'
                self.setup_closed.set()
            else:
                assert connection == 2, 'More than one legacy reconnect'
                await self.setup_closed.wait()
                # read_native_record will require 0A first; another A9, AES
                # envelope or physical/media command fails the exact fixture.
                await self.authenticated_exchange(reader, writer)
                assert await reader.read() == b'', 'Unexpected write after legacy subscription'
        except Exception as exc:
            self.errors.append(type(exc).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass
            self.writers.discard(writer)
            self.tasks.discard(task)
            if connection == 2:
                self.closed.set()


async def main(root):
    assert importlib.metadata.version('aiortc') == '1.15.0+welcomeeye.crc2'
    assert importlib.metadata.version('firebase-messaging') == '0.4.5'
    sys.path.insert(0, str(root))
    package = 'custom_components.welcomeeye_local'
    modules = {name: importlib.import_module(package + ('.' + name if name else ''))
        for name in ('', 'connect3.hub', 'connect3.ring_listener', 'connect3.live',
            'connect3.cgi', 'connect3.discovery', 'r002.qv_discovery', 'sensor', 'services')}
    from homeassistant.components import sensor as ha_sensor
    with tempfile.TemporaryDirectory() as directory:
        context, pin = server_certificate(directory)
        original_hass = ha_core._hass.hass
        hass = HomeAssistant(directory)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        assert await ha_sensor.async_setup(hass, {})
        component = hass.data[ha_sensor.DATA_COMPONENT]
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('sensor', 'welcomeeye_local'): {}}
        user = SimpleNamespace(id='synthetic-alarm-admin', is_admin=True, allowed=True)
        user.permissions = SimpleNamespace(check_entity=lambda *_: user.allowed)
        async def get_user(_):
            return user
        original_auth = getattr(hass, 'auth', None)
        hass.auth = SimpleNamespace(async_get_user=get_user)
        events = []
        hass.bus.async_listen('welcomeeye_local.ring', lambda event: events.append(event))
        real_open = asyncio.open_connection
        real_post = modules['connect3.cgi']._post
        real_tls = modules['connect3.ring_listener'].open_media_tls
        summaries = []
        forbidden_calls = []

        async def forbidden(*args, **kwargs):
            forbidden_calls.append('async_boundary')
            raise AssertionError('Device, discovery, CGI or media I/O forbidden')

        def forbidden_sync(*args, **kwargs):
            forbidden_calls.append('sync_boundary')
            raise AssertionError('Device or media creation forbidden')

        async def unload_platforms(*args):
            return True

        with ExitStack() as guards:
            guards.enter_context(patch.object(socket, 'create_connection', new=forbidden_sync))
            guards.enter_context(patch.object(modules['r002.qv_discovery'], '_open_listener', new=forbidden))
            guards.enter_context(patch.object(modules['connect3.hub'], 'discover', new=forbidden))
            guards.enter_context(patch.object(modules['connect3.live'], 'discover', new=forbidden))
            guards.enter_context(patch.object(modules['connect3.live'], 'read_stream_material', new=forbidden))
            guards.enter_context(patch.object(modules['connect3.live'], 'QVSession', new=forbidden_sync))
            guards.enter_context(patch.object(hass.config_entries, 'async_unload_platforms', new=unload_platforms))
            for transport in ('tls', 'connect3_tcp'):
                for mode in ('accepted', 'stop', 'timeout', 'invalid', 'eof', 'unload', 'wrong_pin',
                             *WEAK_SETUP,
                             'auth_stop', 'auth_unload', 'auth_deadline', 'auth_wrong_cgi_pin',
                             'auth_login_rejected', 'auth_login_truncated', 'auth_login_timeout', 'auth_bad_digest',
                             'legacy_auth_stop', 'legacy_auth_unload'):
                    if mode == 'wrong_pin' and transport != 'tls':
                        continue
                    if mode.startswith('legacy_auth_') and transport != 'tls':
                        continue
                    legacy_tls = transport == 'tls' and (mode == 'legacy_v2' or mode.startswith('legacy_auth_'))
                    scenario = ('auth_deadline' if legacy_tls and mode == 'legacy_v2' else
                        mode.removeprefix('legacy_') if legacy_tls else mode)
                    authenticated = scenario.startswith('auth_')
                    peer = (LegacyTLSAuthenticatedPeer(scenario) if legacy_tls else
                        AuthenticatedPeer(scenario) if authenticated else LoopbackPeer(mode))
                    http = HTTPSPeer()
                    http_server = await asyncio.start_server(http.serve, '127.0.0.1', 0, ssl=context)
                    cgi_port = http_server.sockets[0].getsockname()[1]
                    cgi_observations = []
                    async def only_cgi(session, url, ssl, request, observation=None):
                        if not authenticated:
                            return await forbidden()
                        assert url == f'https://127.0.0.1:{cgi_port}/tdkcgi'
                        assert isinstance(ssl, aiohttp.Fingerprint)
                        assert ssl.fingerprint == bytes.fromhex('f'*64 if mode == 'auth_wrong_cgi_pin' else pin)
                        cgi_observations.append(observation)
                        return await real_post(session, url, ssl, request, observation)
                    server = await asyncio.start_server(peer.serve, '127.0.0.1', 0,
                        ssl=context if transport == 'tls' else None)
                    port = server.sockets[0].getsockname()[1]
                    candidate_port = port if transport == 'tls' else 34567
                    attempts = []
                    tls_observations = []
                    async def only_loopback(host, requested_port, **kwargs):
                        assert host == '127.0.0.1' and requested_port == candidate_port
                        attempts.append((host, requested_port))
                        # The production TCP policy still validates 34567; only
                        # the final socket is routed to this test-owned listener.
                        return await real_open(host, port, **kwargs)
                    async def only_tls(host, requested_port, certificate_sha256, observation):
                        assert transport == 'tls'
                        assert host == '127.0.0.1' and requested_port == candidate_port
                        assert certificate_sha256 == ('f'*64 if mode == 'wrong_pin' else pin)
                        tls_observations.append((host, requested_port, certificate_sha256, observation))
                        return await real_tls(host, requested_port, certificate_sha256, observation)
                    identity = f'synthetic-alarm-{transport}-{mode}'
                    data = dict(host='127.0.0.1', device_variant='connect3',
                        protocol_family='connect3_qv_experimental',
                        auth_code=SECRETS[0] if authenticated or mode in WEAK_SETUP else '',
                        opening_code=SECRETS[1], experimental_video=True, second_channel_enabled=True,
                        media_transport=transport, media_tcp_approved=transport == 'connect3_tcp',
                        media_port=candidate_port, cgi_port=cgi_port,
                        certificate_sha256='f'*64 if mode == 'auth_wrong_cgi_pin' else pin,
                        media_certificate_sha256='f'*64 if mode == 'wrong_pin' else pin,
                        trust_endpoint={'host': '127.0.0.1', 'cgi_port': cgi_port,
                            'media_port': candidate_port, 'media_transport': transport},
                        tls_certificate_expires={endpoint: (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
                            for endpoint in (('cgi', 'media') if transport == 'tls' else ('cgi',))})
                    entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                        title='Synthetic alarm control setup', unique_id=identity, data=data, options={},
                        source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
                    hass.config_entries._entries[entry.entry_id] = entry
                    hub = modules['connect3.hub'].Connect3Hub(hass, entry)
                    entry.runtime_data = hub
                    await hub.start()
                    sensor = modules['sensor'].WelcomeEyeConnect3Status(hub)
                    sensor.hass = hass
                    sensor.entity_id = registry.async_get_or_create('sensor', 'welcomeeye_local',
                        sensor.unique_id, config_entry=entry).entity_id
                    sensor.async_write_ha_state = lambda: None
                    component._platforms['sensor'].entities[sensor.entity_id] = sensor
                    component._entities[sensor.entity_id] = sensor
                    hass.data[DATA_DOMAIN_PLATFORM_ENTITIES][('sensor', 'welcomeeye_local')][sensor.entity_id] = sensor
                    modules['services'].async_setup_connect3_services(hass)

                    async def call(operation, *, duration=2, identified=True):
                        response = await hass.services.async_call('welcomeeye_local', 'connect3_listen_doorbell',
                            {'entity_id': sensor.entity_id, 'operation': operation, 'duration': duration},
                            blocking=True, return_response=True,
                            context=Context(user_id=user.id if identified else None))
                        return response[sensor.entity_id]

                    try:
                        with (patch.object(asyncio, 'open_connection', new=only_loopback),
                              patch.object(modules['connect3.cgi'], '_post', new=only_cgi),
                              patch.object(modules['connect3.ring_listener'], 'open_media_tls', new=only_tls)):
                            for identified, admin, allowed in ((False, True, True), (True, False, True), (True, True, False)):
                                user.is_admin, user.allowed = admin, allowed
                                try:
                                    await call('start', identified=identified)
                                except HomeAssistantError:
                                    pass
                                else:
                                    raise AssertionError('HA alarm permissions bypassed')
                            assert not attempts and hub.autonomous_doorbell.start_attempts == 0
                            user.is_admin = user.allowed = True
                            hub.live.consumers.add('synthetic-viewer')
                            busy = await call('start')
                            assert busy['last_error_reason'] == 'device_busy' and not attempts
                            hub.live.consumers.remove('synthetic-viewer')
                            initial = await call('start', duration=1 if scenario in ('timeout', 'auth_deadline', 'auth_login_timeout') else 2)
                            assert initial['probe_active'] and not initial['listening'], initial
                            if scenario in ('auth_stop', 'auth_unload', 'auth_deadline'):
                                async with asyncio.timeout(3):
                                    await peer.notified.wait()
                                    while not (await call('status'))['notification_candidates']:
                                        assert not peer.errors, peer.errors
                                        assert hub.autonomous_doorbell.active, hub.autonomous_doorbell.diagnostics()
                                        await asyncio.sleep(0)
                                listening = await call('status')
                                assert listening['listening'] and listening['listen_established'], listening
                            if scenario in ('stop', 'unload', 'auth_stop', 'auth_unload'):
                                async with asyncio.timeout(3):
                                    await peer.received.wait()
                                try:
                                    await call('start')
                                except HomeAssistantError:
                                    pass
                                else:
                                    raise AssertionError('Concurrent control probe accepted')
                                try:
                                    await hub.acquire(object())
                                except (ValueError, RuntimeError, ConnectionError):
                                    pass
                                else:
                                    raise AssertionError('Video acquired during control probe')
                                assert len(attempts) == (2 if legacy_tls else 1)
                                if scenario in ('stop', 'auth_stop'):
                                    await call('stop')
                                else:
                                    assert await modules[''].async_unload_entry(hass, entry)
                                    assert not hass.services.has_service('welcomeeye_local', 'connect3_listen_doorbell')
                            async with asyncio.timeout(4):
                                await hub.autonomous_doorbell.wait_closed()
                                await peer.closed.wait()
                            report = hub.autonomous_doorbell.diagnostics() if scenario in ('unload', 'auth_unload') else await call('status')
                            expected = ('blocked' if scenario in ('accepted', 'auth_login_rejected') or
                                (mode in WEAK_SETUP and not legacy_tls) else
                                'stopped' if scenario in ('stop', 'unload', 'auth_stop', 'auth_unload') else
                                'completed' if scenario == 'auth_deadline' else 'failed')
                            assert report['status'] == expected, report
                            if mode == 'accepted':
                                assert report['setup_accepted'] and report['protected_login_capability']
                                assert report['last_error_reason'] == 'local_auth_code_required'
                            elif mode in WEAK_SETUP and not legacy_tls:
                                assert not report['protected_login_capability']
                                reason = {'legacy_v1': 'native_control_legacy_variant_unsupported',
                                    'legacy_v2': 'native_control_legacy_tls_required'}.get(mode,
                                        'native_control_protection_insufficient')
                                assert report['last_error_reason'] == reason
                            elif mode == 'timeout':
                                assert report['last_error_reason'] == 'control_setup_timeout'
                            elif mode == 'invalid':
                                assert report['last_error_reason'] == 'control_setup_invalid'
                            elif mode == 'eof':
                                assert report['bytes_received'] == 7 and report['last_error_reason'] == 'control_setup_incomplete'
                            elif scenario in ('unload', 'auth_unload'):
                                assert report['integration_unloaded'] and report['close_reason'] == 'integration_unload'
                            elif mode == 'wrong_pin':
                                assert report['last_error_reason'] == 'tls_reapproval_required'
                                assert report['setup_request_count'] == 0
                            elif mode == 'auth_wrong_cgi_pin':
                                assert report['last_error_reason'] == 'tls_reapproval_required', report
                                assert not http.requests and len(cgi_observations) == 1
                                assert not cgi_observations[0]['tls_verified']
                            elif scenario == 'auth_deadline':
                                assert report['close_reason'] == 'deadline' and not report['listening']
                            elif mode == 'auth_login_rejected':
                                assert report['last_error_reason'] == 'native_login_rejected'
                                assert report['authentication_status'] == 'rejected' and report['native_login_result'] == 7
                                assert report['close_reason'] == 'authentication_rejected'
                            elif mode == 'auth_login_truncated':
                                assert report['last_error_reason'] == 'native_control_incomplete'
                                assert report['close_reason'] == 'peer_closed'
                            elif mode == 'auth_login_timeout':
                                assert report['last_error_reason'] == 'native_authentication_timeout'
                                assert report['close_reason'] == 'timeout'
                            elif mode == 'auth_bad_digest':
                                assert report['last_error_reason'] == 'control_digest_mismatch'
                                assert report['last_error_type'] == 'ProtocolError'
                            assert report['tls_verified'] is (transport == 'tls' and mode != 'wrong_pin')
                            connection_count = 2 if legacy_tls else 1
                            assert len(attempts) == report['connection_attempts'] == connection_count
                            assert all(attempt == ('127.0.0.1', candidate_port) for attempt in attempts)
                            assert len(tls_observations) == (connection_count if transport == 'tls' else 0)
                            if transport == 'tls' and mode != 'wrong_pin':
                                assert all(item[3]['media_tls_verified'] for item in tls_observations)
                            assert peer.requests == ([b''] if mode == 'wrong_pin' else [REQUEST]), peer.requests
                            if report['setup_response_received']:
                                variant = ('legacy_v2' if legacy_tls else mode if mode.startswith('legacy_') else 'modern_v3')
                                source = 'legacy_sdk_defaults' if variant.startswith('legacy_') else 'modern_advertised'
                                assert report['setup_variant'] == variant and report['crypto_mode_source'] == source
                            assert report['control_reconnect_performed'] is legacy_tls
                            if legacy_tls:
                                assert report['control_reconnect_required'] and not report['protected_login_capability']
                                assert peer.connections == peer.tls_connection_count == 2
                                assert peer.setup_closed.is_set()
                                assert report['setup_request_count'] == 1
                                assert report['control_login_profile'] == 'legacy_v2_verified_tls'
                                assert report['credential_protection'] == 'verified_tls'
                            assert not peer.errors and not peer.tasks and not peer.writers
                            assert not forbidden_calls, 'A forbidden boundary was attempted'
                            assert report['connection_closed'] and not report['probe_active']
                            if authenticated and mode != 'auth_wrong_cgi_pin':
                                assert report['credentials_sent'] and report['cgi_https_verified']
                                assert report['login_request_count'] == 2
                                if not legacy_tls:
                                    assert report['control_login_profile'] == 'modern_v3_native_envelope'
                                    assert report['credential_protection'] == 'native_aes256_sha256'
                                assert not report['firmware_subscription_confirmed']
                                assert len(http.requests) == len(cgi_observations) == 1
                                assert cgi_observations[0]['tls_verified']
                                assert cgi_observations[0]['request_sent_count'] == 1
                                assert cgi_observations[0]['authentication_status'] == 'accepted'
                                if scenario.startswith('auth_login_'):
                                    assert not report['listen_established']
                                    assert report['subscription_request_count'] == 0
                                    assert len(peer.native_requests) == 2
                                    assert report['control_records_received'] == (1 if mode == 'auth_login_rejected' else 0)
                                else:
                                    assert report['listen_established']
                                    assert report['native_checksum_verified'] is (not legacy_tls)
                                    assert report['subscription_request_count'] == 35
                                    assert report['control_peer_authenticated'] is (transport == 'tls')
                                    assert len(peer.native_requests) == 37
                                if scenario.startswith('auth_login_') or mode == 'auth_bad_digest':
                                    assert report['notification_candidates'] == report['notifications_observed'] == 0
                                    assert report['events'] == [] and report['command_counts'] == {}
                                    if mode == 'auth_bad_digest':
                                        assert peer.notified.is_set() and report['control_records_received'] == 1
                                else:
                                    assert report['notification_candidates'] == 1
                                    assert report['control_records_received'] == (2 if legacy_tls else 3)
                                    if legacy_tls:
                                        assert report['control_bytes_received'] == 64 + len(b'SYNTHETIC_PRIVATE_NOTIFICATION_BODY')
                                    assert report['command_counts'] == {'105': 1}
                                    assert len(report['events']) == 1
                                    notification = report['events'][0]
                                    assert notification['subtype'] == 254 and notification['callback_type'] == 354
                                    assert notification['structure_valid'] and notification['channel'] is None
                            else:
                                assert not report['credentials_sent'] and not report['listen_established']
                                assert report['login_request_count'] == report['subscription_request_count'] == 0
                                assert not http.requests
                                if mode != 'auth_wrong_cgi_pin':
                                    assert not cgi_observations
                            assert report['ring_events_emitted'] == 0 and not report['physical_ring_confirmed']
                            assert not report['video_session_created'] and not report['microphone_session_created']
                            assert not hub.consumers and hub.live.session_count == 0
                            assert not hub.channel2.consumers and hub.channel2.live.session_count == 0
                            raw = json.dumps(report)
                            assert all(secret not in raw for secret in (*SECRETS, STREAM_KEY, pin,
                                sha256(SECRETS[0].encode()).hexdigest(), '127.0.0.1',
                                'SYNTHETIC_PRIVATE_NOTIFICATION_BODY'))
                            assert not events
                            summaries.append({'transport': transport, 'case': mode, 'status': 'pass'})
                    finally:
                        await hub.stop()
                        component._platforms['sensor'].entities.pop(sensor.entity_id, None)
                        component._entities.pop(sensor.entity_id, None)
                        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES][('sensor', 'welcomeeye_local')].pop(sensor.entity_id, None)
                        server.close()
                        await server.wait_closed()
                        http_server.close()
                        await http_server.wait_closed()
                        for endpoint in (peer, http):
                            for writer in tuple(endpoint.writers):
                                writer.close()
                            if endpoint.tasks:
                                await asyncio.gather(*tuple(endpoint.tasks))
                            assert not endpoint.errors, endpoint.errors
                            assert not endpoint.writers and not endpoint.tasks
        if original_auth is None:
            del hass.auth
        else:
            hass.auth = original_auth
        await hass.async_stop(force=True)
        from homeassistant.helpers import group, issue_registry
        for getter in (dr.async_get, er.async_get, issue_registry.async_get, group.get_group_entities):
            if clear := getattr(getter, 'cache_clear', None):
                clear()
        assert ha_core._hass.hass is hass
        ha_core._hass.hass = original_hass
        print(json.dumps({'validation': 'actual_HA_native_alarm_loopback',
            'results': summaries, 'device_calls': 0, 'synthetic_credentials_only': True, 'ring_events_emitted': 0}))
        return weakref.ref(hass)


if __name__ == '__main__':
    faulthandler.enable()
    fixture = asyncio.run(main(Path(__file__).resolve().parents[1]))
    gc.collect()
    assert fixture() is None, 'Synthetic alarm HA fixture retained after shutdown'
    print('Alarm runtime: fixture cleanup complete', flush=True)
