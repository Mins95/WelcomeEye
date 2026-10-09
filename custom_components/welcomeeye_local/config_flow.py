"""Validate device credentials before creating a configuration entry."""
import asyncio
from datetime import datetime
import voluptuous as vol
from hashlib import sha256
from ipaddress import IPv4Address
import re
from uuid import uuid4

from homeassistant import config_entries
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .client import AuthenticationError, DiscoveryTimeout, validate_connection
from .capabilities import DeviceVariant, ProtocolFamily, family_for, variant_for
from .connect3.cgi import encode_auth_code
from .connect3.credentials import CredentialImportError, parse_installation_qr
from .connect3.trust import (EndpointTrust, TrustInspection, date_exception_record,
                            key_exception_record, inspect_trust, trust_endpoint_matches)
from .connect3.onboarding import discover_candidates, probe_tcp_setup
from .connect3.channels import channel2_enabled
from .repairs import async_clear_tls_issue
from .r002.fingerprint import fingerprint
from .const import DOMAIN, DEFAULT_NAME
from .protected import ProtocolError

TLS_FAILURE_REASONS = frozenset((
    'certificate_expired', 'certificate_not_yet_valid', 'certificate_timeout',
    'certificate_network_error', 'certificate_connection_refused',
    'certificate_network_unreachable', 'certificate_connection_reset',
    'certificate_malformed', 'certificate_invalid_signature',
    'certificate_unsupported_algorithm', 'certificate_unknown_critical_extension',
    'certificate_non_positive_serial', 'certificate_tls_error',
    'certificate_weak_key', 'certificate_unsupported_key',
    'certificate_invalid_validity', 'certificate_invalid_public_key',
    'certificate_context_error',
))

TLS_FAILURE_STAGES = frozenset((
    'not_started', 'ca_context', 'ca_handshake', 'inspection_context',
    'inspection_handshake', 'certificate_receive', 'certificate_metadata',
    'certificate_validity', 'certificate_public_key', 'certificate_key_policy',
    'certificate_pin', 'complete',
))

SECOND_OUTPUT_TRIALS = ('channel2_strike_trial_enabled', 'channel2_gate_trial_enabled')


def schema(defaults=None):
    defaults = defaults or {}
    host = (
        vol.Required('host', default=defaults['host'])
        if defaults.get('host')
        else vol.Required('host')
    )
    return vol.Schema({
        host: str,
        vol.Required('username', default=defaults.get('username', 'admin')): str,
        vol.Required('password'): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required('second_channel_enabled', default=defaults.get('second_channel_enabled', False)): bool,
    })


class WelcomeEyeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id='user', menu_options=['legacy', 'connect3', 'connect3_discover'])

    async def async_step_legacy(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                if type(user_input.get('second_channel_enabled', False)) is not bool:
                    raise ValueError('invalid_second_channel')
                user_input = {**user_input, 'host': str(IPv4Address(user_input['host']))}
                for entry in self._async_current_entries():
                    if entry.data.get('host') == user_input['host']:
                        return self.async_abort(reason='already_configured')
                uid = await self.hass.async_add_executor_job(validate_connection,
                    user_input['host'], user_input['username'], user_input['password'])
            except DiscoveryTimeout as exc:
                observed = await fingerprint(user_input['host'], exc.probe_count)
                if observed['detected']:
                    # R002 credentials must be entered explicitly in its own reconfiguration.
                    self._r002_pending = {'host': user_input['host'], 'fingerprint': observed}
                    return await self.async_step_r002_confirm()
                errors['base'] = 'cannot_connect'
            except AuthenticationError:
                errors['base'] = 'invalid_auth'
            except ProtocolError:
                errors['base'] = 'unsupported_device'
            except (ValueError, OSError):
                errors['base'] = 'cannot_connect'
            except Exception:
                errors['base'] = 'cannot_connect'
            else:
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_configured(updates={'host': user_input['host']})
                return self.async_create_entry(title=DEFAULT_NAME, data={
                    **user_input, 'uid': uid, 'protocol_family': ProtocolFamily.LEGACY,
                })
        return self.async_show_form(step_id='legacy', data_schema=schema(user_input), errors=errors)

    async def async_step_connect3(self, user_input=None):
        return await self._connect3_form(user_input)

    async def async_step_connect3_discover(self, user_input=None):
        """Optional local lookup; a manual IP remains available immediately."""
        if not hasattr(self, '_connect3_discovered_hosts'):
            self._connect3_discovered_hosts = await discover_candidates()
        errors = {}
        if user_input is not None:
            selected = user_input.get('host')
            if selected == 'manual':
                return await self.async_step_connect3()
            if selected in self._connect3_discovered_hosts:
                self._connect3_discovered_host = selected
                return await self.async_step_connect3()
            errors['base'] = 'invalid_connect3_config'
        elif not self._connect3_discovered_hosts:
            errors['base'] = 'connect3_discovery_empty'
        choices = [selector.SelectOptionDict(value=host, label=host)
                   for host in self._connect3_discovered_hosts]
        choices.append(selector.SelectOptionDict(value='manual', label='Manual IP'))
        return self.async_show_form(step_id='connect3_discover', errors=errors, data_schema=vol.Schema({
            vol.Required('host', default='manual' if not self._connect3_discovered_hosts
                         else self._connect3_discovered_hosts[0]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, translation_key='connect3_discovered_host'))}))

    async def _connect3_form(self, user_input, entry=None):
        errors = {}
        verification = None
        defaults = dict(entry.data) if entry else {}
        if not entry and getattr(self, '_connect3_discovered_host', None):
            defaults['host'] = self._connect3_discovered_host
        if user_input is not None:
            self._discard_connect3_pending()
            # Sections are presentation only. Preserve older submitted field
            # names for callers, without ever putting a secret in a form default.
            # Explicit legacy root fields take priority over defaults that HA
            # may insert into an otherwise partially submitted section.
            user_input = {**user_input.get('advanced', {}), **user_input}
            try:
                address = IPv4Address(user_input['host'])
                if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
                    raise ValueError
                host = str(address)
                if any(other is not entry and other.data.get('host') == host
                       for other in self._async_current_entries()):
                    return self.async_abort(reason='already_configured')
                updates = {'host': host, 'cgi_port': user_input.get('cgi_port', defaults.get('cgi_port', 443))}
                if type(updates['cgi_port']) is not int or not 1 <= updates['cgi_port'] <= 65535:
                    raise ValueError
                updates['media_port'] = user_input.get('media_port', defaults.get('media_port', 8443))
                if type(updates['media_port']) is not int or not 1 <= updates['media_port'] <= 65535:
                    raise ValueError
                # Keep the TLS port when switching transports so returning to
                # TLS preserves the owner's previous endpoint. TCP is fixed.
                selected_transport = user_input.get('media_transport', defaults.get('media_transport', 'auto' if not entry else 'tls'))
                automatic = selected_transport == 'auto'
                updates['media_transport'] = 'tls' if automatic else selected_transport
                if updates['media_transport'] not in ('tls', 'connect3_tcp'):
                    raise ValueError
                media_tls = updates['media_transport'] == 'tls'
                updates['experimental_video'] = user_input.get(
                    'experimental_video', defaults.get('experimental_video', not entry))
                if type(updates['experimental_video']) is not bool:
                    raise ValueError
                updates['experimental_outputs'] = user_input.get(
                    'experimental_outputs', defaults.get('experimental_outputs', False))
                if type(updates['experimental_outputs']) is not bool:
                    raise ValueError
                updates['experimental_tcp_controls'] = user_input.get(
                    'experimental_tcp_controls', defaults.get('experimental_tcp_controls', False))
                if type(updates['experimental_tcp_controls']) is not bool:
                    raise ValueError
                if 'second_channel_enabled' in user_input or not entry:
                    updates['second_channel_enabled'] = user_input.get('second_channel_enabled', False)
                    if type(updates['second_channel_enabled']) is not bool:
                        raise ValueError
                for field in SECOND_OUTPUT_TRIALS:
                    if field in user_input:
                        if type(user_input[field]) is not bool:
                            raise ValueError
                        updates[field] = user_input[field]
                # A blank field keeps the existing secret; removing credentials
                # is an explicit checkbox. Never prefill a secret in forms.
                if user_input.get('clear_credentials'):
                    updates['auth_code'] = ''
                    updates['certificate_sha256'] = ''
                    updates['credential_device_uid'] = ''
                    updates['credential_source'] = ''
                    updates['media_certificate_sha256'] = ''
                    updates['experimental_video'] = False
                    updates['experimental_outputs'] = False
                    updates['experimental_tcp_controls'] = False
                    updates['second_channel_enabled'] = False
                    updates['opening_code'] = ''
                    for field in SECOND_OUTPUT_TRIALS:
                        if field in defaults or field in updates:
                            updates[field] = False
                    updates['trust_endpoint'] = None
                    updates['tls_certificate_expires'] = {}
                    updates['tls_certificate_date_exceptions'] = {}
                    updates['tls_certificate_key_exceptions'] = {}
                    updates['media_tcp_approved'] = False
                else:
                    if user_input.get('opening_code'):
                        encode_auth_code(user_input['opening_code'])
                        updates['opening_code'] = user_input['opening_code']
                    if ((media_tls or updates['experimental_tcp_controls']) and updates['experimental_outputs']
                            and not updates.get('opening_code', defaults.get('opening_code'))):
                        raise ValueError('opening_code_required')
                    if user_input.get('installation_qr'):
                        if user_input.get('auth_code'):
                            raise CredentialImportError('ambiguous_credential_input')
                        credential = parse_installation_qr(user_input['installation_qr'])
                        if (defaults.get('credential_device_uid') and
                                defaults['credential_device_uid'] != credential.uid):
                            raise CredentialImportError('credential_device_mismatch')
                        updates.update(auth_code=credential.auth_code,
                            credential_device_uid=credential.uid, credential_source=credential.format)
                    elif user_input.get('auth_code'):
                        encode_auth_code(user_input['auth_code'])
                        updates['auth_code'] = user_input['auth_code']
                        updates['credential_device_uid'] = ''
                        updates['credential_source'] = 'manual'
                    for field in ('certificate_sha256', 'media_certificate_sha256'):
                        if user_input.get(field):
                            pin = user_input[field].replace(':', '').lower()
                            if not re.fullmatch('[a-f0-9]{64}', pin):
                                raise ValueError
                            updates[field] = pin
                if not entry and not updates.get('auth_code'):
                    raise ValueError('local_password_required')
                if user_input.get('clear_credentials'):
                    return await self._finish_connect3(updates, entry)
                # A second-camera choice changes no endpoint or trust. Keep
                # saved trust and any active TLS repair intact, even offline.
                # An unchanged form still rechecks TLS for the repair flow.
                unchanged_defaults = {'cgi_port': 443, 'media_port': 8443,
                    'media_transport': 'tls', 'experimental_video': False,
                    'experimental_outputs': False, 'experimental_tcp_controls': False}
                if (entry and not automatic and 'second_channel_enabled' in updates
                        and updates['second_channel_enabled'] != channel2_enabled(defaults)
                        and not any(user_input.get(key) for key in (
                            'auth_code', 'opening_code', 'installation_qr',
                            'certificate_sha256', 'media_certificate_sha256'))
                        and all(value == defaults.get(key, unchanged_defaults.get(key))
                            for key, value in updates.items() if key != 'second_channel_enabled')):
                    return self.async_update_reload_and_abort(entry, data_updates={
                        'second_channel_enabled': updates['second_channel_enabled'],
                        **({field: False for field in SECOND_OUTPUT_TRIALS if field in defaults}
                           if not updates['second_channel_enabled'] else {})})
                # Manual setup is unicast. Only QR-bound credentials keep the
                # existing runtime discovery identity check; no UDP is needed here.
                cgi_pin = updates.get('certificate_sha256', defaults.get('certificate_sha256', ''))
                media_pin = updates.get('media_certificate_sha256', defaults.get('media_certificate_sha256', '')) or cgi_pin
                inspection = await inspect_trust(host, updates['cgi_port'], updates['media_port'],
                    cgi_pin=cgi_pin, media_pin=media_pin, media_tls=media_tls,
                    **({'date_exceptions': defaults['tls_certificate_date_exceptions']}
                       if defaults.get('tls_certificate_date_exceptions') else {}),
                    **({'key_exceptions': defaults['tls_certificate_key_exceptions']}
                       if defaults.get('tls_certificate_key_exceptions') else {}))
                detected_tcp = False
                # A refused standard TLS socket is the only automatic TCP
                # candidate. Certificate failures, timeouts and custom ports
                # never cause a downgrade or any alternate-port probe.
                if (automatic and updates['media_port'] == 8443
                        and inspection.cgi.status in ('system_ca', 'pinned', 'candidate')
                        and inspection.media.status == 'failed'
                        and inspection.media.reason == 'certificate_connection_refused'):
                    if await probe_tcp_setup(host):
                        detected_tcp = True
                        updates['media_transport'] = 'connect3_tcp'
                        media_tls = False
                        inspection = TrustInspection(inspection.cgi, EndpointTrust('not_applicable'))
                # New users consent to microphone together with TCP transport;
                # existing disabled controls are preserved until explicit input.
                if not entry and not media_tls and 'experimental_tcp_controls' not in user_input:
                    updates['experimental_tcp_controls'] = updates['experimental_video']
                    if (updates['experimental_outputs']
                            and not updates.get('opening_code', defaults.get('opening_code'))):
                        raise ValueError('opening_code_required')
                if inspection.failed:
                    errors['base'] = self._connect3_tls_error(inspection)
                    verification = self._connect3_verification_details(inspection)
                else:
                    endpoint_changed = bool(entry and any(updates[key] != defaults.get(key, fallback)
                        for key, fallback in (('host', None), ('cgi_port', 443), ('media_port', 8443),
                                              ('media_transport', 'tls'))))
                    pin_changed = bool(entry and any(updates.get(key, defaults.get(key, '')) != defaults.get(key, '')
                        for key in ('certificate_sha256', 'media_certificate_sha256')))
                    self._connect3_pending = updates
                    self._connect3_pending_entry = entry
                    self._connect3_previous = defaults
                    self._connect3_inspection = inspection
                    self._connect3_detected_tcp = detected_tcp
                    self._connect3_changed = endpoint_changed or pin_changed or bool(
                        entry and not trust_endpoint_matches(defaults)) or any(
                        item.status == 'pin_mismatch' for item in (inspection.cgi, inspection.media))
                    self._connect3_tcp_confirmation = not media_tls and (
                        defaults.get('media_transport', 'tls') != 'connect3_tcp'
                        or defaults.get('media_tcp_approved') is not True
                        or (updates['experimental_tcp_controls']
                            and defaults.get('experimental_tcp_controls') is not True)
                        or self._connect3_changed)
                    if inspection.requires_approval or self._connect3_changed or self._connect3_tcp_confirmation:
                        return await self.async_step_connect3_tls_confirm()
                    return await self._accept_connect3_tls(inspection)
            except CredentialImportError:
                errors['base'] = 'invalid_connect3_qr'
            except (ValueError, TypeError) as exc:
                errors['base'] = ('connect3_opening_code_required' if str(exc) == 'opening_code_required'
                                  else 'connect3_local_password_required' if str(exc) == 'local_password_required'
                                  else 'invalid_connect3_config')
            # Preserve only valid non-secret selections when displaying an
            # error. A failed TCP setup must not silently reset the selector to
            # TLS and make the next submit probe a different media endpoint.
            mode = user_input.get('media_transport')
            if mode in ('tls', 'connect3_tcp'):
                defaults['media_transport'] = mode
            for key in ('cgi_port', 'media_port'):
                value = user_input.get(key)
                if type(value) is int and 1 <= value <= 65535:
                    defaults[key] = value
            for key in ('experimental_video', 'experimental_outputs', 'experimental_tcp_controls',
                        'second_channel_enabled', *SECOND_OUTPUT_TRIALS):
                value = user_input.get(key)
                if type(value) is bool:
                    defaults[key] = value
            try:
                address = IPv4Address(user_input.get('host'))
                if not (address.is_multicast or address.is_unspecified or int(address) == 0xffffffff):
                    defaults['host'] = str(address)
            except (ValueError, TypeError):
                pass
        fields = {
            vol.Required('host', **({'default': defaults['host']} if defaults.get('host') else {})): str,
            vol.Optional('auth_code'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('experimental_video', default=defaults.get('experimental_video', not entry)): bool,
            vol.Optional('experimental_outputs', default=defaults.get('experimental_outputs', False)): bool,
            vol.Optional('second_channel_enabled', default=channel2_enabled(defaults)): bool,
            vol.Optional('opening_code'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }
        advanced = {
            vol.Optional('media_transport', default=defaults.get('media_transport', 'auto' if not entry else 'tls')): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    selector.SelectOptionDict(value='auto', label='Automatic'),
                    selector.SelectOptionDict(value='tls', label='TLS'),
                    selector.SelectOptionDict(value='connect3_tcp', label='QV TCP 34567'),
                ], translation_key='connect3_media_transport')),
            vol.Optional('cgi_port', default=defaults.get('cgi_port', 443)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional('media_port', default=defaults.get('media_port', 8443)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional('installation_qr'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('certificate_sha256'): str,
            vol.Optional('media_certificate_sha256'): str,
        }
        if entry:
            advanced[vol.Optional('clear_credentials', default=False)] = bool
            advanced[vol.Optional('experimental_tcp_controls', default=defaults.get('experimental_tcp_controls', False))] = bool
        if channel2_enabled(defaults):
            for field in SECOND_OUTPUT_TRIALS:
                advanced[vol.Optional(field, default=defaults.get(field, False) is True)] = bool
        fields[vol.Optional('advanced')] = section(vol.Schema(advanced), {'collapsed': True})
        if verification is not None:
            # Available even before entry creation. No certificate, fingerprint,
            # address or secret is included or saved to entry/options/storage.
            fields[vol.Optional('verification_details')] = section(vol.Schema({
                vol.Optional(key, default=value): selector.TextSelector(
                    selector.TextSelectorConfig(read_only=True))
                for key, value in verification.items()
            }), {'collapsed': True})
        return self.async_show_form(step_id='connect3_reconfigure' if entry else 'connect3',
                                    # Accept older root-level advanced fields;
                                    # only explicitly handled keys are persisted.
                                    data_schema=vol.Schema(fields, extra=vol.ALLOW_EXTRA), errors=errors)

    @staticmethod
    def _connect3_verification_details(inspection):
        endpoint, result = next((name, value) for name, value in
            (('cgi', inspection.cgi), ('media', inspection.media)) if value.status == 'failed')
        serial = result.serial_status
        kind = getattr(result, 'key_type', 'unknown')
        bits = getattr(result, 'key_bits', None)
        stage = getattr(result, 'failure_stage', 'unknown')
        details = {'endpoint': endpoint, 'status': 'failed',
            'error_reason': result.reason if result.reason in TLS_FAILURE_REASONS else 'certificate_validation_failed',
            'failure_stage': stage if stage in TLS_FAILURE_STAGES else 'unknown',
            'serial_status': serial if serial in ('positive', 'non_positive') else 'unknown',
            'key_type': kind if kind in ('rsa', 'ec', 'ed25519', 'ed448') else 'unknown',
            'key_bits': str(bits) if type(bits) is int and 1 <= bits <= 65536 else 'unknown'}
        # Show only normalized, parsed UTC dates from this inspection. They are
        # useful for an inverted validity interval, but arbitrary certificate
        # text must not escape into the form. This section is never persisted.
        for name in ('not_valid_before', 'not_valid_after'):
            value = getattr(result, name, None)
            if type(value) is str and re.fullmatch(
                    r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00', value):
                try:
                    datetime.fromisoformat(value)
                except ValueError:
                    continue
                details[name] = value
        return details

    @staticmethod
    def _connect3_tls_error(inspection):
        reasons = {item.reason for item in (inspection.cgi, inspection.media) if item.status == 'failed'}
        if reasons & {'certificate_expired', 'certificate_not_yet_valid'}:
            return 'connect3_certificate_expired'
        if 'certificate_invalid_validity' in reasons:
            return 'connect3_certificate_invalid_validity'
        if 'certificate_weak_key' in reasons:
            return 'connect3_certificate_weak_key'
        if reasons & {'certificate_timeout', 'certificate_network_error',
                      'certificate_connection_refused', 'certificate_network_unreachable',
                      'certificate_connection_reset'}:
            return 'connect3_device_unreachable'
        if reasons & {'certificate_malformed', 'certificate_invalid_signature',
                      'certificate_unsupported_algorithm', 'certificate_unknown_critical_extension',
                      'certificate_non_positive_serial', 'certificate_unsupported_key',
                      'certificate_invalid_validity', 'certificate_invalid_public_key'}:
            return 'connect3_invalid_certificate'
        return 'connect3_tls_failed'

    async def async_step_connect3_tls_confirm(self, user_input=None):
        """Approve observed TLS certificates and, separately, opt in to TCP."""
        if not getattr(self, '_connect3_pending', None):
            return self.async_abort(reason='connect3_tls_no_pending')
        errors = {}
        current = None
        if user_input is not None:
            if user_input.get('trust') is not True:
                self._discard_connect3_pending()
                return self.async_abort(reason='connect3_tls_declined')
            updates = self._connect3_pending
            previous = self._connect3_inspection
            zero_duration = any(item.validity_status == 'zero_duration'
                                for item in (previous.cgi, previous.media))
            legacy_key = any(key_exception_record(item) is not None
                             for item in (previous.cgi, previous.media))
            if zero_duration and user_input.get('accept_zero_duration') is not True:
                errors['base'] = 'connect3_zero_duration_approval_required'
            elif legacy_key and user_input.get('accept_legacy_key') is not True:
                errors['base'] = 'connect3_legacy_key_approval_required'
            else:
                # Exceptions require separate explicit consent and bind only
                # the displayed leaf. Never approve a replacement on recheck.
                date_exceptions = {}
                key_exceptions = {}
                if zero_duration:
                    for endpoint, item in (('cgi', previous.cgi), ('media', previous.media)):
                        record = date_exception_record(item)
                        if record is not None:
                            date_exceptions[endpoint] = record
                if legacy_key:
                    for endpoint, item in (('cgi', previous.cgi), ('media', previous.media)):
                        record = key_exception_record(item)
                        if record is not None:
                            key_exceptions[endpoint] = record
                # Recheck the exact certificates the user saw. A change while
                # the dialog was open must never approve an unseen replacement.
                try:
                    current = await inspect_trust(updates['host'], updates['cgi_port'], updates['media_port'],
                        cgi_pin=previous.cgi.fingerprint, media_pin=previous.media.fingerprint,
                        media_tls=updates['media_transport'] == 'tls',
                        **({'date_exceptions': date_exceptions} if date_exceptions else {}),
                        **({'key_exceptions': key_exceptions} if key_exceptions else {}))
                except asyncio.CancelledError:
                    self._discard_connect3_pending()
                    raise
                if current.failed:
                    errors['base'] = self._connect3_tls_error(current)
                elif current.requires_approval:
                    self._connect3_inspection = current
                    self._connect3_changed = True
                    errors['base'] = 'connect3_certificate_changed'
                elif getattr(self, '_connect3_detected_tcp', False):
                    try:
                        detected = await probe_tcp_setup(updates['host'])
                    except asyncio.CancelledError:
                        self._discard_connect3_pending()
                        raise
                    if not detected:
                        errors['base'] = 'connect3_tcp_not_detected'
                    else:
                        return await self._accept_connect3_tls(current)
                else:
                    return await self._accept_connect3_tls(current)
        inspection = self._connect3_inspection
        details = {}
        fingerprints = [('cgi_fingerprint', inspection.cgi.fingerprint)]
        if inspection.media.status != 'not_applicable':
            fingerprints.append(('media_fingerprint', inspection.media.fingerprint))
        for key, value in fingerprints:
            details[vol.Optional(key, default=value)] = selector.TextSelector(
                selector.TextSelectorConfig(read_only=True))
        for endpoint, item in (('cgi', inspection.cgi), ('media', inspection.media)):
            key_record = key_exception_record(item)
            if key_record is not None:
                for name in ('key_type', 'key_bits'):
                    details[vol.Optional(f'{endpoint}_{name}', default=str(key_record[name]))] = selector.TextSelector(
                        selector.TextSelectorConfig(read_only=True))
            record = date_exception_record(item)
            if record is not None:
                for name in ('not_valid_before', 'not_valid_after'):
                    details[vol.Optional(f'{endpoint}_{name}', default=record[name])] = selector.TextSelector(
                        selector.TextSelectorConfig(read_only=True))
        tcp = self._connect3_pending['media_transport'] == 'connect3_tcp'
        step_id = ('connect3_tcp_tls_confirm' if inspection.requires_approval or self._connect3_changed
                   else 'connect3_tcp_confirm') if tcp else (
                       'connect3_tls_changed' if self._connect3_changed else 'connect3_tls_confirm')
        fields = {vol.Required('trust', default=False): bool,
                vol.Optional('certificate_details'): section(vol.Schema(details), {'collapsed': True})}
        if any(item.validity_status == 'zero_duration' for item in (inspection.cgi, inspection.media)):
            fields[vol.Required('accept_zero_duration', default=False)] = bool
        if any(key_exception_record(item) is not None for item in (inspection.cgi, inspection.media)):
            fields[vol.Required('accept_legacy_key', default=False)] = bool
        if current is not None and current.failed:
            verification = self._connect3_verification_details(current)
            fields[vol.Optional('verification_details')] = section(vol.Schema({
                vol.Optional(key, default=value): selector.TextSelector(
                    selector.TextSelectorConfig(read_only=True))
                for key, value in verification.items()
            }), {'collapsed': True})
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(fields),
            errors=errors)

    async def async_step_connect3_tls_changed(self, user_input=None):
        return await self.async_step_connect3_tls_confirm(user_input)

    async def async_step_connect3_tcp_confirm(self, user_input=None):
        return await self.async_step_connect3_tls_confirm(user_input)

    async def async_step_connect3_tcp_tls_confirm(self, user_input=None):
        return await self.async_step_connect3_tls_confirm(user_input)

    async def _accept_connect3_tls(self, inspection):
        updates, entry = self._connect3_pending, self._connect3_pending_entry
        # Do not clobber another reconfiguration completed while this dialog
        # was open; identities, options and entities are left intact.
        if entry and dict(entry.data) != self._connect3_previous:
            self._discard_connect3_pending()
            return self.async_abort(reason='connect3_config_changed')
        tls = updates['media_transport'] == 'tls'
        expires = {'cgi': inspection.cgi.not_valid_after}
        if tls:
            updates['media_certificate_sha256'] = inspection.media.fingerprint
            expires['media'] = inspection.media.not_valid_after
        previous_exceptions = self._connect3_previous.get('tls_certificate_date_exceptions')
        date_exceptions = dict(previous_exceptions) if type(previous_exceptions) is dict else {}
        previous_key_exceptions = self._connect3_previous.get('tls_certificate_key_exceptions')
        key_exceptions = dict(previous_key_exceptions) if type(previous_key_exceptions) is dict else {}
        for endpoint, item in (('cgi', inspection.cgi), ('media', inspection.media)):
            if endpoint == 'media' and not tls:
                continue
            date_exceptions.pop(endpoint, None)
            record = date_exception_record(item)
            if record is not None:
                date_exceptions[endpoint] = record
            key_exceptions.pop(endpoint, None)
            key_record = key_exception_record(item)
            if key_record is not None:
                key_exceptions[endpoint] = key_record
        # Inactive TLS media pins remain private and unchanged in entry.data.
        updates.update(certificate_sha256=inspection.cgi.fingerprint,
            media_tcp_approved=not tls,
            trust_endpoint={'host': updates['host'], 'cgi_port': updates['cgi_port'],
                            'media_port': updates['media_port'] if tls else 34567,
                            'media_transport': updates['media_transport']},
            tls_certificate_expires=expires,
            tls_certificate_date_exceptions=date_exceptions,
            tls_certificate_key_exceptions=key_exceptions)
        self._discard_connect3_pending()
        return await self._finish_connect3(updates, entry)

    def _discard_connect3_pending(self):
        self._connect3_pending = None
        self._connect3_pending_entry = None
        self._connect3_previous = None
        self._connect3_inspection = None
        self._connect3_tcp_confirmation = False
        self._connect3_detected_tcp = False
        self._connect3_output_pending = None

    async def _finish_connect3(self, updates, entry):
        previous = dict(entry.data) if entry else {}
        merged = {**previous, **updates}
        eligible = (channel2_enabled(merged) and merged.get('experimental_video') is True
            and merged.get('experimental_outputs') is True and bool(merged.get('opening_code'))
            and (merged.get('media_transport', 'tls') == 'tls'
                 or merged.get('experimental_tcp_controls') is True))
        changed_target = any(merged.get(key, fallback) != previous.get(key, fallback)
            for key, fallback in (('host', None), ('cgi_port', 443), ('media_port', 8443),
                ('media_transport', 'tls'), ('certificate_sha256', ''),
                ('media_certificate_sha256', ''), ('auth_code', ''), ('opening_code', '')))
        requested = []
        for field in SECOND_OUTPUT_TRIALS:
            if not eligible:
                if field in merged:
                    updates[field] = False
            elif merged.get(field) is True and (previous.get(field) is not True or changed_target):
                requested.append(field)
        if requested:
            # A separate explicit confirmation names each relay. Saving this
            # preference never sends a physical command or opens a stream.
            self._connect3_output_pending = (dict(updates), entry, previous, tuple(requested))
            return await self.async_step_connect3_output_trials()
        return await self._persist_connect3(updates, entry)

    async def async_step_connect3_output_trials(self, user_input=None):
        pending = getattr(self, '_connect3_output_pending', None)
        if pending is None:
            return self.async_abort(reason='connect3_tls_no_pending')
        updates, entry, previous, requested = pending
        if entry and dict(entry.data) != previous:
            self._connect3_output_pending = None
            return self.async_abort(reason='connect3_config_changed')
        if user_input is not None:
            if not all(user_input.get(field) is True for field in requested):
                self._connect3_output_pending = None
                return self.async_abort(reason='connect3_output_trial_declined')
            self._connect3_output_pending = None
            return await self._persist_connect3(updates, entry)
        return self.async_show_form(step_id='connect3_output_trials', data_schema=vol.Schema({
            vol.Required(field, default=False): bool for field in requested}))

    async def _persist_connect3(self, updates, entry):
        if any(other is not entry and other.data.get('host') == updates['host']
               for other in self._async_current_entries()):
            return self.async_abort(reason='already_configured')
        if entry:
            async_clear_tls_issue(self.hass, entry.entry_id)
            return self.async_update_reload_and_abort(entry, data_updates=updates)
        identity = 'connect3-' + uuid4().hex
        await self.async_set_unique_id(identity)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title='WelcomeEye Connect 3', data={
            **updates, 'protocol_family': ProtocolFamily.CONNECT3,
            'device_variant': DeviceVariant.CONNECT3, 'identity_source': 'provisional_random'})

    async def async_step_connect3_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        if entry.data.get('protocol_family') != ProtocolFamily.CONNECT3:
            return self.async_abort(reason='reconfigure_not_supported')
        try:
            variant_for(entry.data)
        except ValueError:
            return self.async_abort(reason='unsupported_family')
        return await self._connect3_form(user_input, entry)

    async def async_step_r002_confirm(self, user_input=None):
        if user_input is not None:
            if not user_input.get('confirm', False):
                return self.async_abort(reason='experimental_declined')
            data = self._r002_pending
            # Provisional config identity only, not a hardware UID. Retained on reconfigure.
            identity = 'r002-' + sha256(data['host'].encode('ascii')).hexdigest()[:16]
            await self.async_set_unique_id(identity)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title='WelcomeEye R002 (experimental)', data={
                **data, 'protocol_family': ProtocolFamily.R002,
                'device_variant': DeviceVariant.R002, 'identity_source': 'provisional_host_hash',
            })
        return self.async_show_form(step_id='r002_confirm', data_schema=vol.Schema({
            vol.Required('confirm', default=False): bool,
        }))

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        try:
            variant = variant_for(entry.data)
        except ValueError:
            return self.async_abort(reason='unsupported_family')
        if variant == DeviceVariant.V1:
            return await self.async_step_v1_cloud_reconfigure(user_input)
        if variant in (DeviceVariant.R001, DeviceVariant.LEGACY_UNKNOWN):
            return await self.async_step_legacy_reconfigure(user_input)
        if entry.data.get('protocol_family') == ProtocolFamily.CONNECT3:
            return await self.async_step_connect3_reconfigure(user_input)
        if entry.data.get('protocol_family') != ProtocolFamily.R002:
            return self.async_abort(reason='reconfigure_not_supported')
        errors = {}
        if user_input is not None:
            try:
                address = IPv4Address(user_input['host'])
                if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
                    raise ValueError
                host = str(address)
                if any(other.entry_id != entry.entry_id and other.data.get('host') == host
                       for other in self._async_current_entries()):
                    return self.async_abort(reason='already_configured')
                updates = {'host': host}
                for field in ('experimental_video', 'experimental_outputs'):
                    updates[field] = user_input.get(field, entry.data.get(field, False))
                    if type(updates[field]) is not bool:
                        raise ValueError
                if user_input.get('clear_credentials'):
                    updates.update(auth_code='', opening_code='', certificate_sha256='',
                                   experimental_video=False, experimental_outputs=False)
                else:
                    for field in ('auth_code', 'opening_code'):
                        if user_input.get(field):
                            encode_auth_code(user_input[field])
                            updates[field] = user_input[field]
                    if user_input.get('certificate_sha256'):
                        pin = user_input['certificate_sha256'].replace(':', '').lower()
                        if not re.fullmatch('[a-f0-9]{64}', pin):
                            raise ValueError
                        updates['certificate_sha256'] = pin
                    if updates['experimental_outputs'] and not updates.get(
                            'opening_code', entry.data.get('opening_code')):
                        raise ValueError('opening_code_required')
            except (ValueError, TypeError) as exc:
                errors['base'] = ('r002_opening_code_required' if str(exc) == 'opening_code_required'
                                  else 'invalid_r002_config')
            else:
                # Preserve the R002 family and provisional identity. No discovery,
                # authentication or Connect 3 QR parsing runs during configuration.
                return self.async_update_reload_and_abort(entry, data_updates=updates)
        return self.async_show_form(step_id='reconfigure', data_schema=vol.Schema({
            vol.Required('host', default=entry.data['host']): str,
            vol.Optional('auth_code'): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('certificate_sha256'): str,
            vol.Optional('experimental_video', default=entry.data.get('experimental_video', False)): bool,
            vol.Optional('experimental_outputs', default=entry.data.get('experimental_outputs', False)): bool,
            vol.Optional('opening_code'): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('clear_credentials', default=False): bool,
        }), errors=errors)

    async def async_step_legacy_reconfigure(self, user_input=None):
        """Change only the owner's second-panel choice, without device I/O."""
        entry = self._get_reconfigure_entry()
        try:
            if variant_for(entry.data) not in (DeviceVariant.R001, DeviceVariant.LEGACY_UNKNOWN):
                return self.async_abort(reason='reconfigure_not_supported')
        except ValueError:
            return self.async_abort(reason='unsupported_family')
        errors = {}
        if user_input is not None:
            enabled = user_input.get('second_channel_enabled', entry.data.get('second_channel_enabled', False))
            if type(enabled) is not bool:
                errors['base'] = 'invalid_legacy_config'
            else:
                return self.async_update_reload_and_abort(entry, data_updates={
                    'second_channel_enabled': enabled})
        return self.async_show_form(step_id='legacy_reconfigure', data_schema=vol.Schema({
            vol.Required('second_channel_enabled', default=entry.data.get('second_channel_enabled', False) is True): bool,
        }), errors=errors)

    async def async_step_v1_cloud_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        try:
            if variant_for(entry.data) != DeviceVariant.V1:
                return self.async_abort(reason='reconfigure_not_supported')
        except ValueError:
            return self.async_abort(reason='unsupported_family')
        errors = {}
        if user_input is not None:
            enabled = user_input.get('v1_cloud_doorbell_enabled', entry.data.get('v1_cloud_doorbell_enabled', False))
            second = user_input.get('second_channel_enabled', entry.data.get('second_channel_enabled', False))
            if type(enabled) is not bool or type(second) is not bool:
                errors['base'] = 'invalid_v1_cloud_config'
            else:
                updates = {'v1_cloud_doorbell_enabled': enabled}
                if 'second_channel_enabled' in user_input:
                    updates['second_channel_enabled'] = second
                return self.async_update_reload_and_abort(entry, data_updates=updates)
        return self.async_show_form(step_id='v1_cloud_reconfigure', data_schema=vol.Schema({
            vol.Required('v1_cloud_doorbell_enabled', default=entry.data.get(
                'v1_cloud_doorbell_enabled', False) is True): bool,
            vol.Required('second_channel_enabled', default=entry.data.get('second_channel_enabled', False) is True): bool,
        }), errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        try:
            if family_for(variant_for(entry.data)) != ProtocolFamily.LEGACY:
                return self.async_abort(reason='reauth_not_supported')
        except ValueError:
            return self.async_abort(reason='unsupported_family')
        errors = {}
        if user_input is not None:
            try:
                uid = await self.hass.async_add_executor_job(validate_connection,
                    entry.data['host'], entry.data['username'], user_input['password'])
                if uid != entry.unique_id:
                    return self.async_abort(reason='wrong_device')
            except AuthenticationError:
                errors['base'] = 'invalid_auth'
            except Exception:
                errors['base'] = 'cannot_connect'
            else:
                return self.async_update_reload_and_abort(entry,
                    data_updates={'password': user_input['password']})
        return self.async_show_form(step_id='reauth_confirm', data_schema=vol.Schema({
            vol.Required('password'): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }), errors=errors)
