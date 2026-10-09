"""Native HA config/Repairs flows, loopback certificates and persistence.

Run in the supported HA Core images. The manifest's existing aiortc dependency
is installed with HA's package helper; no new dependency is introduced. All
device TCP, UDP, CGI, media and output paths are forbidden. Only the explicitly
created local TLS fixture can receive certificate-inspection connections.
"""
import asyncio
from base64 import b64encode
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID, ObjectIdentifier

from homeassistant import config_entries
from homeassistant.components.repairs import RepairsFlowManager
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package

DOMAIN = 'welcomeeye_local'
AUTH = 'SYNTHETIC_TLS_LOCAL_PASSWORD'
OPENING = 'SYNTHETIC_TLS_OPENING_CODE'
CGI_PIN, MEDIA_PIN = 'a' * 64, 'b' * 64
NEW_CGI_PIN, NEW_MEDIA_PIN = 'c' * 64, 'd' * 64
OBSERVED_DATE = '1969-12-31T16:00:27+00:00'
INPUT = {'host': '192.0.2.10', 'auth_code': AUTH,
         'experimental_video': True, 'experimental_outputs': True, 'opening_code': OPENING}


def serialize_form(result):
    """Use this HA version's native HTTP view serializer, including Probatio."""
    return FlowManagerResourceView(None)._prepare_result_json(result)['data_schema']


def field(fields, name):
    return next(item for item in fields if item['name'] == name)


async def initialize_hass(directory):
    hass = HomeAssistant(directory)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await ir.async_load(hass)
    await hass.config_entries.async_initialize()
    if hasattr(dr, 'async_setup'):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    return hass


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        assert await asyncio.to_thread(install_package, manifest['requirements'][0], **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        package = 'custom_components.welcomeeye_local'
        integration = importlib.import_module(package)
        config = importlib.import_module(package + '.config_flow')
        trust = importlib.import_module(package + '.connect3.trust')
        hub_module = importlib.import_module(package + '.connect3.hub')
        live = importlib.import_module(package + '.connect3.live')
        cgi = importlib.import_module(package + '.connect3.cgi')
        discovery = importlib.import_module(package + '.connect3.discovery')
        qv = importlib.import_module(package + '.r002.qv_discovery')
        repairs = importlib.import_module(package + '.repairs')
        diagnostics = importlib.import_module(package + '.diagnostics')

        def inspection(status='candidate', *, cgi_pin=CGI_PIN, media_pin=MEDIA_PIN,
                       media_status=None, reason=None):
            return trust.TrustInspection(
                trust.EndpointTrust(status, cgi_pin, reason, validity_status='valid',
                    identity_status='ip_match', not_valid_after='2099-01-01T00:00:00+00:00'),
                trust.EndpointTrust(media_status or status, '' if media_status == 'not_applicable' else media_pin,
                    reason, validity_status='valid', identity_status='ip_match',
                    not_valid_after=None if media_status == 'not_applicable' else '2099-01-02T00:00:00+00:00'))

        def verification_failure(endpoint='cgi', *, tcp=False, reason='certificate_weak_key',
                                 serial='positive', key_type='rsa', key_bits=1024,
                                 stage='certificate_key_policy'):
            failed = trust.EndpointTrust('failed', 'f' * 64, reason,
                serial_status=serial, key_type=key_type, key_bits=key_bits, failure_stage=stage)
            accepted = inspection('system_ca').cgi
            return (trust.TrustInspection(accepted, failed) if endpoint == 'media'
                    else trust.TrustInspection(failed, trust.EndpointTrust('not_applicable') if tcp else accepted))

        hass = await initialize_hass(temporary)
        hubs = []
        real_open_connection = asyncio.open_connection
        with ExitStack() as stack:
            # Flow/platform discovery is isolated; the real HA manager still
            # validates schemas, creates ConfigEntries and processes results.
            async def create_config_flow(handler, *, context=None, data=None):
                assert handler == DOMAIN
                flow = config.WelcomeEyeConfigFlow()
                flow.init_step = context['source']
                return flow
            stack.enter_context(patch.object(hass.config_entries.flow, 'async_create_flow', side_effect=create_config_flow))
            if hasattr(config_entries, '_support_single_config_entry_only'):
                stack.enter_context(patch.object(config_entries, '_support_single_config_entry_only', AsyncMock(return_value=False)))
            stack.enter_context(patch.object(hass.config_entries, 'async_setup', AsyncMock(return_value=True)))
            stack.enter_context(patch.object(hass.config_entries, 'async_reload', AsyncMock(return_value=True)))
            forbidden = 'Device networking or physical operation forbidden in TLS flow verification'
            stack.enter_context(patch.object(asyncio, 'open_connection', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(socket, 'create_connection', Mock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(qv, '_open_listener', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(discovery, 'discover', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(hub_module, 'discover', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(live, 'discover', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(cgi, '_post', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(live, 'read_stream_material', AsyncMock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(live, 'QVSession', Mock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(config, 'validate_connection', Mock(side_effect=AssertionError(forbidden))))
            stack.enter_context(patch.object(config, 'fingerprint', AsyncMock(side_effect=AssertionError(forbidden))))

            async def new_form():
                menu = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'})
                assert menu['type'] == 'menu' and 'connect3' in menu['menu_options']
                form = await hass.config_entries.flow.async_configure(menu['flow_id'], {'next_step_id': 'connect3'})
                assert form['step_id'] == 'connect3'
                schema = serialize_form(form)
                assert {item['name'] for item in schema} == {
                    'host', 'auth_code', 'experimental_video', 'second_channel_enabled',
                    'experimental_outputs', 'opening_code', 'advanced'}
                assert field(schema, 'experimental_video')['default'] is True
                assert field(schema, 'second_channel_enabled')['default'] is False
                advanced = field(schema, 'advanced')
                assert advanced['type'] == 'expandable' and advanced['expanded'] is False
                selected = field(advanced['schema'], 'media_transport')
                assert selected['default'] == 'auto'
                assert {option['value'] for option in selected['selector']['select']['options']} == {'auto', 'tls', 'connect3_tcp'}
                assert {'cgi_port', 'media_port', 'installation_qr', 'certificate_sha256',
                        'media_certificate_sha256'} <= {item['name'] for item in advanced['schema']}
                assert field(schema, 'auth_code')['selector']['text']['type'] == 'password'
                return form

            def assert_confirmation(result, *, changed=False, tcp=False, zero_duration=False, legacy_key=False):
                expected = ('connect3_tcp_tls_confirm' if changed else 'connect3_tcp_confirm') if tcp else (
                    'connect3_tls_changed' if changed else 'connect3_tls_confirm')
                assert result['step_id'] == expected
                schema = serialize_form(result)
                assert field(schema, 'trust')['default'] is False
                details = field(schema, 'certificate_details')
                assert details['type'] == 'expandable' and details['expanded'] is False
                names = ['cgi_fingerprint'] if tcp else ['cgi_fingerprint', 'media_fingerprint']
                if zero_duration:
                    acknowledgement = field(schema, 'accept_zero_duration')
                    assert acknowledgement['default'] is False
                    assert acknowledgement['required'] is True
                    for endpoint in (('cgi',) if tcp else ('cgi', 'media')):
                        names += [endpoint + '_not_valid_before', endpoint + '_not_valid_after']
                else:
                    assert 'accept_zero_duration' not in {item['name'] for item in schema}
                if legacy_key:
                    acknowledgement = field(schema, 'accept_legacy_key')
                    assert acknowledgement['default'] is False and acknowledgement['required'] is True
                    for endpoint in (('cgi',) if tcp else ('cgi', 'media')):
                        names += [endpoint + '_key_type', endpoint + '_key_bits']
                else:
                    assert 'accept_legacy_key' not in {item['name'] for item in schema}
                assert {item['name'] for item in details['schema']} == set(names)
                for name in names:
                    assert field(details['schema'], name)['selector']['text']['read_only'] is True
                    if name.endswith(('_not_valid_before', '_not_valid_after')):
                        assert field(details['schema'], name)['default'] == OBSERVED_DATE
                    if name.endswith('_key_type'):
                        assert field(details['schema'], name)['default'] == 'rsa'
                    if name.endswith('_key_bits'):
                        assert field(details['schema'], name)['default'] == '1024'
                assert AUTH not in json.dumps(schema) and OPENING not in json.dumps(schema)

            def assert_verification_details(result, endpoint, *, reason='certificate_weak_key',
                                            serial='positive', key_type='rsa', key_bits='1024',
                                            stage='certificate_key_policy', dates=None):
                schema = serialize_form(result)
                section = field(schema, 'verification_details')
                assert section['type'] == 'expandable' and section['expanded'] is False
                expected = {'endpoint': endpoint, 'status': 'failed', 'error_reason': reason,
                            'failure_stage': stage, 'serial_status': serial,
                            'key_type': key_type, 'key_bits': key_bits}
                expected.update(dates or {})
                assert {item['name'] for item in section['schema']} == expected.keys()
                for name, value in expected.items():
                    item = field(section['schema'], name)
                    assert item['default'] == value
                    assert item['selector']['text']['read_only'] is True
                exported = json.dumps(section)
                for private in (AUTH, OPENING, CGI_PIN, MEDIA_PIN, 'f' * 64,
                                '192.0.2.', 'PRIVATE', 'credential_device_uid', 'certificate_sha256'):
                    assert private not in exported, 'Private material exposed in verification section'
                assert AUTH not in json.dumps(schema) and OPENING not in json.dumps(schema)
                for name in ('auth_code', 'opening_code'):
                    assert 'default' not in field(schema, name)
                if result['step_id'] in ('connect3', 'connect3_reconfigure'):
                    advanced = field(schema, 'advanced')['schema']
                    for name in ('installation_qr', 'certificate_sha256', 'media_certificate_sha256'):
                        assert 'default' not in field(advanced, name)
                return schema

            # Exercise actual certificate bytes through the native flow, not
            # just synthetic outcomes. This legacy-shaped leaf is readable,
            # current and strong, but is deliberately not a CA identity proof.
            # A pin authenticates its exact DER bytes; no fixture or peer is
            # rewritten by the integration.
            async def verify_loopback_certificates(*, zero_duration=False, legacy_key=False):
                fixture_host = '127.0.0.3' if legacy_key else '127.0.0.2' if zero_duration else '127.0.0.1'
                fixture_key_bits = 1024 if legacy_key else 2048
                client_security_level = ssl.create_default_context().security_level
                assert trust._inspection_context().security_level == client_security_level
                assert aiohttp.connector._SSL_CONTEXT_UNVERIFIED.security_level == client_security_level
                servers, handlers, received, ports = [], set(), [], set()
                tls_versions = []
                fingerprints = []
                loop = asyncio.get_running_loop()
                previous_handler = loop.get_exception_handler()
                def expected_handshake_error(loop, context):
                    if isinstance(context.get('exception'), (ssl.SSLError, ConnectionResetError)):
                        return
                    if previous_handler is None:
                        loop.default_exception_handler(context)
                    else:
                        previous_handler(loop, context)
                loop.set_exception_handler(expected_handshake_error)
                try:
                    for number in (1, 2):
                        key = rsa.generate_private_key(public_exponent=65537, key_size=fixture_key_bits)
                        now = datetime.now(timezone.utc)
                        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                            'eZiOtEST' if number == 1 else 'synthetic legacy intercom')])
                        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                            .public_key(key.public_key()).serial_number(0x010203)
                            .not_valid_before(now - timedelta(days=1))
                            .not_valid_after(now + timedelta(days=30))
                            .add_extension(x509.UnrecognizedExtension(ObjectIdentifier('1.2.3.4.5'),
                                b'SYNTHETIC_OPAQUE_VENDOR_EXTENSION'), critical=True)
                            .sign(key, hashes.SHA256()))
                        der = cert.public_bytes(serialization.Encoding.DER)
                        # Rebuild and sign only our generated fixture. Preserve
                        # a real signature while modelling a zero serial and,
                        # optionally, the exact observed equal UTC date bounds.
                        root_der = trust.der_reader._tree(der)
                        cert_fields = list(root_der.children[0].children)
                        position = 1 if cert_fields[0].tag == 0xa0 else 0
                        encoded = lambda tag, value: trust._encoded(SimpleNamespace(tag=tag, value=value))
                        values = [trust._encoded(node) for node in cert_fields]
                        values[position] = encoded(2, b'\x00')
                        if zero_duration:
                            moment = encoded(23, b'691231160027Z')
                            values[position + 3] = encoded(0x30, moment + moment)
                        tbs = encoded(0x30, b''.join(values))
                        signature = key.sign(tbs, padding.PKCS1v15(), hashes.SHA256())
                        key.public_key().verify(signature, tbs, padding.PKCS1v15(), hashes.SHA256())
                        der = encoded(0x30, tbs + trust._encoded(root_der.children[1])
                            + encoded(3, b'\x00' + signature))
                        fingerprints.append(sha256(der).hexdigest())
                        cert_file = Path(temporary) / f'loopback-legacy-{number}.pem'
                        key_file = Path(temporary) / f'loopback-key-{number}.pem'
                        encoded = b64encode(der)
                        cert_file.write_bytes(b'-----BEGIN CERTIFICATE-----\n'
                            + b'\n'.join(encoded[index:index + 64] for index in range(0, len(encoded), 64))
                            + b'\n-----END CERTIFICATE-----\n')
                        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        if legacy_key:
                            # This local server alone must load its weak key.
                            # Client CA, inspection and Fingerprint contexts are
                            # not lowered or patched by the fixture/integration.
                            context.set_ciphers('DEFAULT:@SECLEVEL=1')
                            context.minimum_version = ssl.TLSVersion.TLSv1_2
                            context.maximum_version = ssl.TLSVersion.TLSv1_2
                        context.load_cert_chain(cert_file, key_file)
                        async def serve(reader, writer):
                            task = asyncio.current_task()
                            handlers.add(task)
                            try:
                                peer = writer.get_extra_info('ssl_object')
                                version = peer.version() if peer is not None else None
                                tls_versions.append(version)
                                if legacy_key:
                                    assert version == 'TLSv1.2'
                                # Assert negotiated TLS before application reads.
                                received.append(await reader.read(65536))
                            finally:
                                try:
                                    await trust.close_writer(writer)
                                finally:
                                    handlers.discard(task)
                        server = await asyncio.start_server(serve, fixture_host, 0, ssl=context)
                        servers.append(server)
                        ports.add(server.sockets[0].getsockname()[1])
                    first_port, second_port = [server.sockets[0].getsockname()[1] for server in servers]
                    async def local_connection(host, port, *args, **kwargs):
                        assert host == fixture_host and port in ports, forbidden
                        return await real_open_connection(host, port, *args, **kwargs)
                    with patch.object(asyncio, 'open_connection', AsyncMock(side_effect=local_connection)) as connect:
                        form = await new_form()
                        pending = await hass.config_entries.flow.async_configure(form['flow_id'], {
                            'host': fixture_host, 'auth_code': AUTH, 'experimental_video': True,
                            'advanced': {'cgi_port': first_port, 'media_port': first_port}})
                        assert_confirmation(pending, zero_duration=zero_duration, legacy_key=legacy_key)
                        inspected = hass.config_entries.flow._progress[pending['flow_id']]._connect3_inspection
                        assert inspected.cgi.status == inspected.media.status == 'candidate'
                        assert inspected.cgi.serial_status == 'non_positive'
                        assert inspected.cgi.key_type == 'rsa' and inspected.cgi.key_bits == fixture_key_bits
                        assert not inspected.cgi.system_trusted and inspected.cgi.failure_stage == 'complete'
                        assert inspected.cgi.fingerprint == fingerprints[0]
                        if zero_duration:
                            assert inspected.cgi.validity_status == inspected.media.validity_status == 'zero_duration'
                            assert inspected.cgi.not_valid_before == inspected.cgi.not_valid_after == OBSERVED_DATE
                            before_calls, before_entries = connect.await_count, len(hass.config_entries.async_entries(DOMAIN))
                            missing_cases = [
                                ({'trust': True}, 'connect3_zero_duration_approval_required'),
                                ({'trust': True, 'accept_zero_duration': False}, 'connect3_zero_duration_approval_required'),
                            ]
                            if legacy_key:
                                missing_cases += [
                                    ({'trust': True, 'accept_zero_duration': True}, 'connect3_legacy_key_approval_required'),
                                    ({'trust': True, 'accept_zero_duration': True, 'accept_legacy_key': False},
                                     'connect3_legacy_key_approval_required'),
                                    ({'trust': True, 'accept_legacy_key': True}, 'connect3_zero_duration_approval_required'),
                                ]
                            for approval, error in missing_cases:
                                missing_ack = await hass.config_entries.flow.async_configure(pending['flow_id'], approval)
                                assert missing_ack['errors']['base'] == error
                                assert_confirmation(missing_ack, zero_duration=True, legacy_key=legacy_key)
                                assert connect.await_count == before_calls
                                assert len(hass.config_entries.async_entries(DOMAIN)) == before_entries
                            decline_consent = {'trust': False, 'accept_zero_duration': True}
                            if legacy_key:
                                decline_consent['accept_legacy_key'] = True
                            declined = await hass.config_entries.flow.async_configure(pending['flow_id'], decline_consent)
                            assert declined['reason'] == 'connect3_tls_declined'
                            assert connect.await_count == before_calls
                            assert len(hass.config_entries.async_entries(DOMAIN)) == before_entries
                            form = await new_form()
                            pending = await hass.config_entries.flow.async_configure(form['flow_id'], {
                                'host': fixture_host, 'auth_code': AUTH, 'experimental_video': True,
                                'advanced': {'cgi_port': first_port, 'media_port': first_port}})
                            assert_confirmation(pending, zero_duration=True, legacy_key=legacy_key)
                        approval = {'trust': True}
                        if zero_duration:
                            approval['accept_zero_duration'] = True
                        if legacy_key:
                            approval['accept_legacy_key'] = True
                        created = await hass.config_entries.flow.async_configure(pending['flow_id'], approval)
                        assert created['type'] == 'create_entry'
                        local_entry = created['result']
                        retained = local_entry.entry_id, local_entry.unique_id
                        assert local_entry.data['certificate_sha256'] == fingerprints[0]
                        assert local_entry.data['media_certificate_sha256'] == fingerprints[0]
                        assert 'accept_zero_duration' not in local_entry.data
                        assert 'accept_legacy_key' not in local_entry.data
                        if legacy_key:
                            key_record = {'policy': 'rsa1024_v1', 'certificate_sha256': fingerprints[0],
                                'key_type': 'rsa', 'key_bits': 1024}
                            assert local_entry.data['tls_certificate_key_exceptions'] == {'cgi': key_record, 'media': key_record}
                        else:
                            assert not local_entry.data.get('tls_certificate_key_exceptions')
                        if zero_duration:
                            record = {'policy': 'zero_duration_v1', 'certificate_sha256': fingerprints[0],
                                'not_valid_before': OBSERVED_DATE, 'not_valid_after': OBSERVED_DATE}
                            assert local_entry.data['tls_certificate_date_exceptions'] == {'cgi': record, 'media': record}
                            same_form = await hass.config_entries.flow.async_init(DOMAIN,
                                context={'source': 'reconfigure', 'entry_id': local_entry.entry_id})
                            same = await hass.config_entries.flow.async_configure(same_form['flow_id'], {'host': fixture_host})
                            assert same['reason'] == 'reconfigure_successful', 'Matching exception records prompted again'
                        else:
                            assert not local_entry.data.get('tls_certificate_date_exceptions')
                        original = dict(local_entry.data)
                        for approval in (False, True):
                            reconfigure = await hass.config_entries.flow.async_init(DOMAIN,
                                context={'source': 'reconfigure', 'entry_id': local_entry.entry_id})
                            pending = await hass.config_entries.flow.async_configure(reconfigure['flow_id'], {
                                'host': fixture_host, 'media_transport': 'connect3_tcp',
                                'advanced': {'cgi_port': second_port}})
                            assert_confirmation(pending, changed=True, tcp=True, zero_duration=zero_duration, legacy_key=legacy_key)
                            inspected = hass.config_entries.flow._progress[pending['flow_id']]._connect3_inspection
                            assert inspected.cgi.status == 'pin_mismatch'
                            assert inspected.cgi.serial_status == 'non_positive'
                            assert inspected.media.status == 'not_applicable'
                            assert inspected.cgi.fingerprint == fingerprints[1]
                            assert dict(local_entry.data) == original
                            consent = {'trust': approval}
                            if zero_duration:
                                consent['accept_zero_duration'] = True
                            if legacy_key:
                                consent['accept_legacy_key'] = True
                            finished = await hass.config_entries.flow.async_configure(pending['flow_id'], consent)
                            assert finished['reason'] == ('reconfigure_successful' if approval else 'connect3_tls_declined')
                            if not approval:
                                assert dict(local_entry.data) == original
                        assert (local_entry.entry_id, local_entry.unique_id) == retained
                        assert local_entry.data['certificate_sha256'] == fingerprints[1]
                        assert local_entry.data['media_certificate_sha256'] == fingerprints[0]
                        assert local_entry.data['media_port'] == first_port
                        assert local_entry.data['media_tcp_approved'] is True
                        assert trust.trust_endpoint_matches(local_entry.data)
                        if legacy_key:
                            records = local_entry.data['tls_certificate_key_exceptions']
                            assert records['cgi'] == {'policy': 'rsa1024_v1', 'certificate_sha256': fingerprints[1],
                                'key_type': 'rsa', 'key_bits': 1024}
                            assert records['media'] == {'policy': 'rsa1024_v1', 'certificate_sha256': fingerprints[0],
                                'key_type': 'rsa', 'key_bits': 1024}
                        if zero_duration:
                            records = local_entry.data['tls_certificate_date_exceptions']
                            assert records['cgi'] == {'policy': 'zero_duration_v1', 'certificate_sha256': fingerprints[1],
                                'not_valid_before': OBSERVED_DATE, 'not_valid_after': OBSERVED_DATE}
                            assert records['media'] == {'policy': 'zero_duration_v1', 'certificate_sha256': fingerprints[0],
                                'not_valid_before': OBSERVED_DATE, 'not_valid_after': OBSERVED_DATE}
                            approved_hub = hub_module.Connect3Hub(hass, local_entry)
                            await approved_hub.start()
                            approved_hub.check_tls_trust()
                            local_entry.runtime_data = approved_hub
                            exported = json.dumps(await diagnostics.async_get_config_entry_diagnostics(hass, local_entry))
                            for private in (fingerprints[0], fingerprints[1], OBSERVED_DATE,
                                            'tls_certificate_date_exceptions', 'zero_duration_v1',
                                            'tls_certificate_key_exceptions', 'rsa1024_v1'):
                                assert private not in exported, 'Private date exception exposed in diagnostics'
                            await approved_hub.stop()
                        assert all(call.args[0] == fixture_host and call.args[1] in ports
                                   for call in connect.await_args_list)
                    await asyncio.sleep(0)
                    assert received and all(data == b'' for data in received), 'Inspection sent application data'
                    if legacy_key:
                        assert tls_versions and set(tls_versions) == {'TLSv1.2'}
                    assert trust._inspection_context().security_level == client_security_level
                    assert aiohttp.connector._SSL_CONTEXT_UNVERIFIED.security_level == client_security_level
                    return local_entry
                finally:
                    for server in servers:
                        server.close()
                        await server.wait_closed()
                    owned = tuple(handlers)
                    for task in owned:
                        task.cancel()
                    await asyncio.gather(*owned, return_exceptions=True)
                    loop.set_exception_handler(previous_handler)

            await verify_loopback_certificates()
            zero_date_entry = await verify_loopback_certificates(zero_duration=True)
            zero_date_entry_id, zero_date_uid = zero_date_entry.entry_id, zero_date_entry.unique_id
            zero_date_records = json.loads(json.dumps(zero_date_entry.data['tls_certificate_date_exceptions']))
            legacy_key_entry = await verify_loopback_certificates(zero_duration=True, legacy_key=True)
            legacy_key_entry_id, legacy_key_uid = legacy_key_entry.entry_id, legacy_key_entry.unique_id
            legacy_key_records = json.loads(json.dumps(legacy_key_entry.data['tls_certificate_key_exceptions']))
            legacy_date_records = json.loads(json.dumps(legacy_key_entry.data['tls_certificate_date_exceptions']))

            # First-use refusal creates no entry and drops the pending secrets.
            first = await new_form()
            count = len(hass.config_entries.async_entries(DOMAIN))
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection())) as inspect:
                pending = await hass.config_entries.flow.async_configure(first['flow_id'], INPUT)
                assert_confirmation(pending)
                flow = hass.config_entries.flow._progress[pending['flow_id']]
                declined = await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': False})
                assert declined['reason'] == 'connect3_tls_declined'
                assert flow._connect3_pending is None and flow._connect3_inspection is None
                inspect.assert_awaited_once_with('192.0.2.10', 443, 8443, cgi_pin='', media_pin='', media_tls=True)
            assert len(hass.config_entries.async_entries(DOMAIN)) == count

            # Normal system trust needs no confirmation; private pins persist.
            ca_form = await new_form()
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection('system_ca'))):
                created = await hass.config_entries.flow.async_configure(ca_form['flow_id'], INPUT)
            assert created['type'] == 'create_entry'
            entry = created['result']
            assert hass.config_entries.async_get_entry(entry.entry_id) is entry
            assert entry.data['certificate_sha256'] == CGI_PIN
            assert entry.data['media_certificate_sha256'] == MEDIA_PIN
            assert entry.data['trust_endpoint'] == {'host': '192.0.2.10', 'cgi_port': 443,
                                                   'media_port': 8443, 'media_transport': 'tls'}
            assert entry.data['auth_code'] == AUTH and trust.trust_endpoint_matches(entry.data)
            retained_id, retained_uid = entry.entry_id, entry.unique_id
            entity = er.async_get(hass).async_get_or_create('sensor', DOMAIN,
                f'{entry.unique_id}_connect3_status', config_entry=entry)

            # Independent self-signed certificates require one explicit decision
            # and the exact displayed certificates are rechecked before saving.
            tofu_form = await new_form()
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[inspection(), inspection('pinned')])) as inspect:
                pending = await hass.config_entries.flow.async_configure(tofu_form['flow_id'], {**INPUT, 'host': '192.0.2.11'})
                assert_confirmation(pending)
                tofu = await hass.config_entries.flow.async_configure(pending['flow_id'], {
                    'trust': True, 'certificate_details': {'cgi_fingerprint': 'e' * 64, 'media_fingerprint': 'f' * 64}})
                assert tofu['type'] == 'create_entry'
                assert tofu['result'].data['certificate_sha256'] == CGI_PIN
                assert tofu['result'].data['media_certificate_sha256'] == MEDIA_PIN
                assert inspect.await_args.kwargs == {'cgi_pin': CGI_PIN, 'media_pin': MEDIA_PIN, 'media_tls': True}

            # A new TCP entry needs separate explicit consent even when CGI is
            # CA-trusted; no TLS certificate request is made to the media port.
            tcp_input = {**INPUT, 'host': '192.0.2.15', 'media_transport': 'connect3_tcp',
                         'advanced': {'media_port': 9443}}
            tcp_first = await new_form()
            before = len(hass.config_entries.async_entries(DOMAIN))
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection(
                    'system_ca', media_status='not_applicable'))) as inspect:
                tcp_pending = await hass.config_entries.flow.async_configure(tcp_first['flow_id'], tcp_input)
                assert_confirmation(tcp_pending, tcp=True)
                assert inspect.await_args.kwargs['media_tls'] is False
                declined = await hass.config_entries.flow.async_configure(tcp_pending['flow_id'], {'trust': False})
                assert declined['reason'] == 'connect3_tls_declined'
            assert len(hass.config_entries.async_entries(DOMAIN)) == before
            tcp_first = await new_form()
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('system_ca', media_status='not_applicable'),
                    inspection('pinned', media_status='not_applicable')])) as inspect:
                tcp_pending = await hass.config_entries.flow.async_configure(tcp_first['flow_id'], tcp_input)
                assert_confirmation(tcp_pending, tcp=True)
                tcp_created = await hass.config_entries.flow.async_configure(tcp_pending['flow_id'], {'trust': True})
            assert tcp_created['type'] == 'create_entry'
            tcp_entry = tcp_created['result']
            assert tcp_entry.data['media_tcp_approved'] is True and tcp_entry.data['media_port'] == 9443
            assert tcp_entry.data['trust_endpoint']['media_port'] == 34567
            assert set(tcp_entry.data['tls_certificate_expires']) == {'cgi'}
            assert all(call.kwargs['media_tls'] is False for call in inspect.await_args_list)

            # Automatic onboarding may propose TCP only after a refused
            # standard TLS endpoint and one credential-free SETUP. Saving
            # still requires explicit consent and a fresh bound recheck.
            auto_form = await new_form()
            before = len(hass.config_entries.async_entries(DOMAIN))
            refused = trust.TrustInspection(inspection('system_ca').cgi,
                trust.EndpointTrust('failed', reason='certificate_connection_refused'))
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    refused, inspection('pinned', media_status='not_applicable')])) as inspect, \
                    patch.object(config, 'probe_tcp_setup', AsyncMock(return_value=True)) as probe:
                pending = await hass.config_entries.flow.async_configure(auto_form['flow_id'],
                    {'host': '192.0.2.19', 'auth_code': AUTH, 'second_channel_enabled': True})
                assert_confirmation(pending, tcp=True)
                assert len(hass.config_entries.async_entries(DOMAIN)) == before
                probe.assert_awaited_once_with('192.0.2.19')
                created = await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': True})
                assert probe.await_count == 2
                assert inspect.await_args_list[0].kwargs['media_tls'] is True
                assert inspect.await_args_list[1].kwargs['media_tls'] is False
            assert created['result'].data['media_tcp_approved'] is True
            assert created['result'].data['experimental_video'] is True
            assert created['result'].data['experimental_tcp_controls'] is True
            assert created['result'].data['experimental_outputs'] is False
            assert created['result'].data['second_channel_enabled'] is True

            # Bad certificate/time/network results never offer approval or save
            # the successful CGI endpoint while the media endpoint failed.
            for reason in ('certificate_expired', 'certificate_malformed', 'certificate_connection_refused'):
                failed_form = await new_form()
                before = len(hass.config_entries.async_entries(DOMAIN))
                with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection(
                        'system_ca', media_status='failed', reason=reason))):
                    failed = await hass.config_entries.flow.async_configure(failed_form['flow_id'],
                        {**INPUT, 'host': '192.0.2.12', 'media_transport': 'tls'})
                assert failed['step_id'] == 'connect3' and failed['errors']['base']
                assert len(hass.config_entries.async_entries(DOMAIN)) == before
                hass.config_entries.flow.async_abort(failed['flow_id'])

            # Certificate failures are inspectable before any entry exists.
            # The failed endpoint and bounded metadata are shown read-only;
            # no trust control, partial pin or submitted secret is retained.
            for endpoint in ('cgi', 'media'):
                failed_form = await new_form()
                before = len(hass.config_entries.async_entries(DOMAIN))
                with patch.object(config, 'inspect_trust', AsyncMock(return_value=verification_failure(endpoint))):
                    failed = await hass.config_entries.flow.async_configure(failed_form['flow_id'],
                        {**INPUT, 'host': '192.0.2.16'})
                assert failed['errors']['base'] == 'connect3_certificate_weak_key'
                schema = assert_verification_details(failed, endpoint)
                assert field(schema, 'host')['default'] == '192.0.2.16'
                assert 'trust' not in {item['name'] for item in schema}
                assert len(hass.config_entries.async_entries(DOMAIN)) == before
                hass.config_entries.flow.async_abort(failed['flow_id'])

            # A readable but inverted date interval remains a rejection. Show
            # its parsed UTC bounds without saving an entry or using credentials.
            invalid_dates = {'not_valid_before': '2049-01-01T00:00:00+00:00',
                             'not_valid_after': '1950-01-01T00:00:00+00:00'}
            invalid_date_form = await new_form()
            before = len(hass.config_entries.async_entries(DOMAIN))
            saved_entries = hass.config_entries._data_to_save()
            date_failure = trust.TrustInspection(trust.EndpointTrust('failed',
                reason='certificate_invalid_validity', serial_status='non_positive',
                failure_stage='certificate_validity', **invalid_dates),
                trust.EndpointTrust('not_applicable'))
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=date_failure)):
                failed = await hass.config_entries.flow.async_configure(invalid_date_form['flow_id'],
                    {**INPUT, 'host': '192.0.2.18', 'media_transport': 'connect3_tcp'})
            assert failed['step_id'] == 'connect3'
            assert failed['errors']['base'] == 'connect3_certificate_invalid_validity'
            schema = assert_verification_details(failed, 'cgi',
                reason='certificate_invalid_validity', serial='non_positive',
                key_type='unknown', key_bits='unknown', stage='certificate_validity', dates=invalid_dates)
            assert field(field(schema, 'advanced')['schema'], 'media_transport')['default'] == 'connect3_tcp'
            assert 'trust' not in {item['name'] for item in schema}
            assert len(hass.config_entries.async_entries(DOMAIN)) == before
            assert hass.config_entries._data_to_save() == saved_entries
            failed_flow = hass.config_entries.flow._progress[failed['flow_id']]
            assert getattr(failed_flow, '_connect3_pending', None) is None
            cgi._post.assert_not_awaited()
            live.read_stream_material.assert_not_awaited()
            live.QVSession.assert_not_called()
            asyncio.open_connection.assert_not_awaited()
            hass.config_entries.flow.async_abort(failed['flow_id'])

            # Unknown raw labels and mistyped key size never escape the private
            # inspector. An injected UI report cannot override its outcome.
            sanitized_form = await new_form()
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=verification_failure(
                    reason='PRIVATE_RAW_REASON_UID', serial='PRIVATE_SERIAL', key_type='PRIVATE_OWNER',
                    key_bits=True, stage='PRIVATE_RAW_STAGE_UID'))):
                sanitized = await hass.config_entries.flow.async_configure(sanitized_form['flow_id'],
                    {**INPUT, 'host': '192.0.2.16'})
            assert_verification_details(sanitized, 'cgi', reason='certificate_validation_failed',
                                        serial='unknown', key_type='unknown', key_bits='unknown', stage='unknown')
            hass.config_entries.flow.async_abort(sanitized['flow_id'])
            failed_form = await new_form()
            failed_inputs = {**INPUT, 'host': '192.0.2.16', 'media_transport': 'connect3_tcp'}
            before = len(hass.config_entries.async_entries(DOMAIN))
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=verification_failure(tcp=True))) as inspect:
                failed = await hass.config_entries.flow.async_configure(failed_form['flow_id'], failed_inputs)
                schema = assert_verification_details(failed, 'cgi')
                assert field(field(schema, 'advanced')['schema'], 'media_transport')['default'] == 'connect3_tcp'
                retry = await hass.config_entries.flow.async_configure(failed['flow_id'], {
                    **failed_inputs, 'verification_details': {'status': 'accepted', 'key_bits': '4096'}})
                assert retry['errors']['base'] == 'connect3_certificate_weak_key'
                assert_verification_details(retry, 'cgi')
                assert len(hass.config_entries.async_entries(DOMAIN)) == before
                assert all(call.kwargs['media_tls'] is False for call in inspect.await_args_list)
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection('system_ca', media_status='not_applicable'))):
                pending = await hass.config_entries.flow.async_configure(retry['flow_id'], failed_inputs)
            assert pending['step_id'] == 'connect3_tcp_confirm'
            assert 'verification_details' not in {item['name'] for item in serialize_form(pending)}
            declined = await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': False})
            assert declined['reason'] == 'connect3_tls_declined'
            assert len(hass.config_entries.async_entries(DOMAIN)) == before

            # A failed reconfiguration never rewrites the current entry,
            # its options or identity just to show verification information.
            hass.config_entries.async_update_entry(entry, options={'existing_option': True})
            original_data, original_options = dict(entry.data), dict(entry.options)
            reconfigure = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=verification_failure('media'))):
                failed = await hass.config_entries.flow.async_configure(reconfigure['flow_id'], {'host': entry.data['host']})
            assert_verification_details(failed, 'media')
            assert dict(entry.data) == original_data and dict(entry.options) == original_options
            assert entry.entry_id == retained_id and entry.unique_id == retained_uid
            hass.config_entries.flow.async_abort(failed['flow_id'])

            # Cancel after a candidate was shown, while confirmation is being
            # rechecked. The flow drops private references and can be removed
            # without a created entry or an owned inspection worker left behind.
            cancel_form = await new_form()
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection())):
                pending = await hass.config_entries.flow.async_configure(cancel_form['flow_id'],
                    {**INPUT, 'host': '192.0.2.17'})
            pending_flow = hass.config_entries.flow._progress[pending['flow_id']]
            entered = asyncio.Event()
            async def blocked_inspection(*args, **kwargs):
                entered.set()
                await asyncio.Future()
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=blocked_inspection)):
                worker = asyncio.create_task(hass.config_entries.flow.async_configure(
                    pending['flow_id'], {'trust': True}), name='connect3-verifier-canceled-confirmation')
                await entered.wait()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError('Confirmation cancellation did not propagate')
            for name in ('_connect3_pending', '_connect3_pending_entry', '_connect3_previous', '_connect3_inspection'):
                assert getattr(pending_flow, name) is None
            hass.config_entries.flow.async_abort(pending['flow_id'])
            assert pending['flow_id'] not in {item['flow_id'] for item in hass.config_entries.flow.async_progress()}
            assert worker.done() and worker.cancelled()
            assert len(hass.config_entries.async_entries(DOMAIN)) == before

            hub = hub_module.Connect3Hub(hass, entry)
            hubs.append(hub)
            await hub.start()
            entry.runtime_data = hub
            mismatch = aiohttp.ServerFingerprintMismatch(bytes.fromhex(CGI_PIN), bytes.fromhex(NEW_CGI_PIN), '192.0.2.10', 443)
            with patch.object(hub_module, 'read_device', AsyncMock(side_effect=mismatch)) as read:
                assert (await hub.execute('access'))['reason'] == 'tls_reapproval_required'
                assert (await hub.execute('access'))['reason'] == 'tls_reapproval_required'
                read.assert_awaited_once()
            issue_id = repairs.tls_issue_id(entry.entry_id)
            registry = ir.async_get(hass)
            issue = registry.async_get_issue(DOMAIN, issue_id)
            assert issue and issue.is_fixable and issue.is_persistent and issue.severity == ir.IssueSeverity.ERROR
            assert issue.translation_placeholders == {'config_entry': entry.entry_id}
            assert entry.data['certificate_sha256'] == CGI_PIN

            # Changing only the second panel is safe offline and must neither
            # rewrite private trust metadata nor dismiss an existing repair.
            previous = dict(entry.data)
            for enabled in (True, False):
                option_form = await hass.config_entries.flow.async_init(DOMAIN,
                    context={'source': 'reconfigure', 'entry_id': entry.entry_id})
                with patch.object(config, 'inspect_trust', AsyncMock(side_effect=AssertionError(forbidden))) as inspect:
                    saved = await hass.config_entries.flow.async_configure(option_form['flow_id'],
                        {'host': entry.data['host'], 'second_channel_enabled': enabled})
                    inspect.assert_not_called()
                assert saved['reason'] == 'reconfigure_successful'
                assert dict(entry.data) == {**previous, 'second_channel_enabled': enabled}
                assert registry.async_get_issue(DOMAIN, issue_id) is issue
                assert entry.entry_id == retained_id and entry.unique_id == retained_uid
            exported = json.dumps(await diagnostics.async_get_config_entry_diagnostics(hass, entry))
            public_issue = json.dumps({'data': issue.data, 'placeholders': issue.translation_placeholders})
            for private in (AUTH, OPENING, CGI_PIN, MEDIA_PIN, NEW_CGI_PIN, '192.0.2.10'):
                assert private not in exported and private not in public_issue, 'TLS material exposed'

            # Real Repairs manager, factory isolated only from platform loading.
            repair_manager = RepairsFlowManager(hass)
            async def create_repair_flow(handler, *, context=None, data=None):
                assert handler == DOMAIN
                repair_issue_id = (context or {}).get('issue_id') or (data or {}).get('issue_id')
                current_issue = registry.async_get_issue(handler, repair_issue_id)
                assert current_issue and current_issue.is_fixable
                repair = await repairs.async_create_fix_flow(hass, repair_issue_id, current_issue.data)
                repair.issue_id, repair.data = repair_issue_id, current_issue.data
                return repair
            with patch.object(repair_manager, 'async_create_flow', side_effect=create_repair_flow):
                # HA 2026.10 moved the issue ID from init data into context.
                repair_init = ({'context': {'issue_id': issue_id}}
                    if hasattr(repairs.repairs_platform, 'RepairsFlowContext')
                    else {'data': {'issue_id': issue_id}})
                repair_form = await repair_manager.async_init(DOMAIN, **repair_init)
                assert repair_form['step_id'] == 'confirm'
                serialize_form(repair_form)
                before_progress = len(hass.config_entries.flow.async_progress())
                routed = await repair_manager.async_configure(repair_form['flow_id'], {})
            assert registry.async_get_issue(DOMAIN, issue_id) is not None
            if repairs._FLOW_TYPE is None:
                assert routed['reason'] == 'reconfigure_legacy' and 'next_flow' not in routed
                assert len(hass.config_entries.flow.async_progress()) == before_progress
                change_form = await hass.config_entries.flow.async_init(DOMAIN,
                    context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            else:
                assert routed['next_flow'][0] == repairs._FLOW_TYPE.CONFIG_FLOW
                change_form = await hass.config_entries.flow.async_configure(routed['next_flow'][1])
            assert change_form['step_id'] == 'connect3_reconfigure'
            assert AUTH not in json.dumps(serialize_form(change_form))
            original = dict(entry.data)
            with patch.object(config, 'inspect_trust', AsyncMock(return_value=inspection('pin_mismatch', cgi_pin=NEW_CGI_PIN))):
                changed = await hass.config_entries.flow.async_configure(change_form['flow_id'], {'host': entry.data['host']})
                assert_confirmation(changed, changed=True)
                declined = await hass.config_entries.flow.async_configure(changed['flow_id'], {'trust': False})
                assert declined['reason'] == 'connect3_tls_declined'
            assert dict(entry.data) == original and registry.async_get_issue(DOMAIN, issue_id) is not None

            # Reapproval keeps HA identities/entities and resolves the issue only
            # after current, explicitly selected replacement pins are rechecked.
            changed_form = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pin_mismatch', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN)])):
                pending = await hass.config_entries.flow.async_configure(changed_form['flow_id'], {'host': entry.data['host']})
                assert_confirmation(pending, changed=True)
                assert dict(entry.data) == original
                approved = await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': True})
            assert approved['reason'] == 'reconfigure_successful'
            assert entry.entry_id == retained_id and entry.unique_id == retained_uid
            assert er.async_get(hass).async_get(entity.entity_id) is entity
            assert entry.data['certificate_sha256'] == NEW_CGI_PIN
            assert entry.data['media_certificate_sha256'] == NEW_MEDIA_PIN
            assert entry.data['auth_code'] == AUTH and registry.async_get_issue(DOMAIN, issue_id) is None

            # Same pin on a changed endpoint still requires explicit approval.
            endpoint_form = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN)])):
                pending = await hass.config_entries.flow.async_configure(endpoint_form['flow_id'],
                    {'host': '192.0.2.13', 'advanced': {'cgi_port': 444, 'media_port': 8444}})
                assert_confirmation(pending, changed=True)
                assert entry.data['host'] == '192.0.2.10'
                await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': True})
            assert entry.entry_id == retained_id and entry.unique_id == retained_uid
            assert trust.trust_endpoint_matches(entry.data)

            # An external data edit cannot move existing approval to another
            # endpoint. Guard before I/O and require the changed trust dialog
            # even when reconfigure submits that current host unchanged.
            hass.config_entries.async_update_entry(entry, data={**entry.data, 'host': '192.0.2.14'})
            endpoint_hub = hub_module.Connect3Hub(hass, entry)
            hubs.append(endpoint_hub)
            await endpoint_hub.start()
            with patch.object(hub_module, 'read_device', AsyncMock(side_effect=AssertionError(forbidden))) as read:
                assert (await endpoint_hub.execute('access'))['reason'] == 'tls_reapproval_required'
                read.assert_not_called()
            assert registry.async_get_issue(DOMAIN, issue_id).data['reason'] == 'endpoint_changed'
            external_form = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=NEW_MEDIA_PIN)])):
                pending = await hass.config_entries.flow.async_configure(external_form['flow_id'], {'host': entry.data['host']})
                assert_confirmation(pending, changed=True)
                assert entry.data['trust_endpoint']['host'] == '192.0.2.13'
                await hass.config_entries.flow.async_configure(pending['flow_id'], {'trust': True})
            assert trust.trust_endpoint_matches(entry.data)
            assert registry.async_get_issue(DOMAIN, issue_id) is None

            # TLS -> TCP -> TLS preserves registry identities and inactive TLS
            # configuration. Real platform setup never creates TCP controls.
            tls_port, tls_pin = entry.data['media_port'], entry.data['media_certificate_sha256']
            entity_registry = er.async_get(hass)
            output_entities = [entity_registry.async_get_or_create('button', DOMAIN,
                f'{entry.unique_id}_open_output_{number}', config_entry=entry)
                for number in (1, 2)]
            switch_form = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            for secret in (AUTH, OPENING, tls_pin):
                assert secret not in json.dumps(serialize_form(switch_form))
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_status='not_applicable'),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_status='not_applicable')])):
                tcp_pending = await hass.config_entries.flow.async_configure(switch_form['flow_id'],
                    {'host': entry.data['host'], 'media_transport': 'connect3_tcp'})
                assert_confirmation(tcp_pending, changed=True, tcp=True)
                assert entry.data['media_transport'] == 'tls'
                await hass.config_entries.flow.async_configure(tcp_pending['flow_id'], {'trust': True})
            assert entry.data['media_port'] == tls_port and entry.data['media_certificate_sha256'] == tls_pin
            assert entry.data['experimental_outputs'] is True and entry.data['opening_code'] == OPENING
            assert set(entry.data['tls_certificate_expires']) == {'cgi'}
            assert entry.entry_id == retained_id and entry.unique_id == retained_uid
            created_entities = []
            async def forward(config_entry, platforms):
                for platform in platforms:
                    module = importlib.import_module(f'{package}.{platform.value}')
                    await module.async_setup_entry(hass, config_entry, created_entities.extend)
            with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward):
                assert await integration.async_setup_entry(hass, entry)
            tcp_hub = entry.runtime_data
            hubs.append(tcp_hub)
            assert tcp_hub.capabilities.camera and tcp_hub.capabilities.downstream_audio
            assert not tcp_hub.capabilities.talkback and not tcp_hub.capabilities.strike and not tcp_hub.capabilities.gate
            assert {type(item).__name__ for item in created_entities} == {
                'WelcomeEyeConnect3Camera', 'WelcomeEyeConnect3Status'}
            assert all(entity_registry.async_get(item.entity_id) is item for item in output_entities)
            await tcp_hub.stop()
            switch_back = await hass.config_entries.flow.async_init(DOMAIN,
                context={'source': 'reconfigure', 'entry_id': entry.entry_id})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=[
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=tls_pin),
                    inspection('pinned', cgi_pin=NEW_CGI_PIN, media_pin=tls_pin)])):
                tls_pending = await hass.config_entries.flow.async_configure(switch_back['flow_id'],
                    {'host': entry.data['host'], 'media_transport': 'tls'})
                assert_confirmation(tls_pending, changed=True)
                await hass.config_entries.flow.async_configure(tls_pending['flow_id'], {'trust': True})
            assert entry.data['media_port'] == tls_port and entry.data['media_certificate_sha256'] == tls_pin
            assert not entry.data['media_tcp_approved'] and trust.trust_endpoint_matches(entry.data)
            created_entities.clear()
            with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward):
                assert await integration.async_setup_entry(hass, entry)
            tls_hub = entry.runtime_data
            hubs.append(tls_hub)
            assert tls_hub.capabilities.talkback and tls_hub.capabilities.strike and tls_hub.capabilities.gate
            assert sum(type(item).__name__ == 'WelcomeEyeOpenButton' for item in created_entities) == 2
            assert all(entity_registry.async_get(item.entity_id) is item for item in output_entities)
            assert entry.entry_id == retained_id and entry.unique_id == retained_uid

            # Config entry private storage and the persistent issue survive a
            # native HA reload. A dismissed warning cannot restore device I/O.
            repairs.async_report_tls_issue(hass, entry.entry_id, 'certificate_changed', 'media')
            await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
            await registry._store.async_save(registry._data_to_save())
            await hass.async_block_till_done()
            for owned in hubs:
                await owned.stop()
            await hass.async_stop(force=True)

            restarted = await initialize_hass(temporary)
            legacy_restored_entry = restarted.config_entries.async_get_entry(legacy_key_entry_id)
            assert legacy_restored_entry and legacy_restored_entry.unique_id == legacy_key_uid
            assert legacy_restored_entry.data['tls_certificate_key_exceptions'] == legacy_key_records
            assert legacy_restored_entry.data['tls_certificate_date_exceptions'] == legacy_date_records
            assert trust.trust_endpoint_matches(legacy_restored_entry.data)
            legacy_restored = hub_module.Connect3Hub(restarted, legacy_restored_entry)
            await legacy_restored.start()
            legacy_restored.check_tls_trust()
            await legacy_restored.stop()
            zero_restored_entry = restarted.config_entries.async_get_entry(zero_date_entry_id)
            assert zero_restored_entry and zero_restored_entry.unique_id == zero_date_uid
            assert zero_restored_entry.data['tls_certificate_date_exceptions'] == zero_date_records
            assert trust.trust_endpoint_matches(zero_restored_entry.data)
            zero_restored = hub_module.Connect3Hub(restarted, zero_restored_entry)
            await zero_restored.start()
            zero_restored.check_tls_trust()
            await zero_restored.stop()
            restored_entry = restarted.config_entries.async_get_entry(retained_id)
            assert restored_entry and restored_entry.unique_id == retained_uid
            assert restored_entry.data['certificate_sha256'] == NEW_CGI_PIN
            assert restored_entry.data['media_certificate_sha256'] == NEW_MEDIA_PIN
            assert restored_entry.data['auth_code'] == AUTH and trust.trust_endpoint_matches(restored_entry.data)
            restored = hub_module.Connect3Hub(restarted, restored_entry)
            await restored.start()
            try:
                restored.check_tls_trust()
            except cgi.CGIError as exc:
                assert str(exc) == 'tls_reapproval_required'
            else:
                raise AssertionError('Persistent trust failure was cleared by restart')
            await integration.async_remove_entry(restarted, restored_entry)
            assert ir.async_get(restarted).async_get_issue(DOMAIN, repairs.tls_issue_id(retained_id)) is None
            await restored.stop()
            await restarted.async_stop(force=True)
        print('Connect 3 actual HA: TLS/TCP explicit selection, TCP audio + explicit controls capabilities, preserved identities, private pins, Repairs, persistence PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
