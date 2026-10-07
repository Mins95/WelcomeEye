"""Validate device credentials before creating a configuration entry."""
import voluptuous as vol
from hashlib import sha256
from ipaddress import IPv4Address
import re
from uuid import uuid4

from homeassistant import config_entries
from homeassistant.helpers import selector

from .client import AuthenticationError, DiscoveryTimeout, validate_connection
from .capabilities import DeviceVariant, ProtocolFamily, family_for, variant_for
from .connect3.cgi import encode_auth_code
from .connect3.credentials import CredentialImportError, parse_installation_qr
from .r002.fingerprint import fingerprint
from .const import DOMAIN, DEFAULT_NAME
from .protected import ProtocolError


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
    })


class WelcomeEyeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id='user', menu_options=['legacy', 'connect3'])

    async def async_step_legacy(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
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

    async def _connect3_form(self, user_input, entry=None):
        errors = {}
        defaults = dict(entry.data) if entry else {}
        if user_input is not None:
            try:
                address = IPv4Address(user_input['host'])
                if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
                    raise ValueError
                host = str(address)
                if any(other is not entry and other.data.get('host') == host
                       for other in self._async_current_entries()):
                    return self.async_abort(reason='already_configured')
                if not entry and not user_input.get('confirm'):
                    return self.async_abort(reason='experimental_declined')
                updates = {'host': host, 'cgi_port': user_input.get('cgi_port', defaults.get('cgi_port', 443))}
                if type(updates['cgi_port']) is not int or not 1 <= updates['cgi_port'] <= 65535:
                    raise ValueError
                updates['media_port'] = user_input.get('media_port', defaults.get('media_port', 8443))
                if type(updates['media_port']) is not int or not 1 <= updates['media_port'] <= 65535:
                    raise ValueError
                updates['experimental_video'] = user_input.get(
                    'experimental_video', defaults.get('experimental_video', False))
                if type(updates['experimental_video']) is not bool:
                    raise ValueError
                updates['experimental_outputs'] = user_input.get(
                    'experimental_outputs', defaults.get('experimental_outputs', False))
                if type(updates['experimental_outputs']) is not bool:
                    raise ValueError
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
                    updates['opening_code'] = ''
                else:
                    if user_input.get('opening_code'):
                        encode_auth_code(user_input['opening_code'])
                        updates['opening_code'] = user_input['opening_code']
                    if updates['experimental_outputs'] and not updates.get('opening_code', defaults.get('opening_code')):
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
                if entry:
                    return self.async_update_reload_and_abort(entry, data_updates=updates)
                identity = 'connect3-' + uuid4().hex
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title='WelcomeEye Connect 3 (experimental)', data={
                    **updates, 'protocol_family': ProtocolFamily.CONNECT3,
                    'device_variant': DeviceVariant.CONNECT3, 'identity_source': 'provisional_random'})
            except CredentialImportError:
                errors['base'] = 'invalid_connect3_qr'
            except (ValueError, TypeError) as exc:
                errors['base'] = ('connect3_opening_code_required' if str(exc) == 'opening_code_required'
                                  else 'invalid_connect3_config')
        fields = {
            vol.Required('host', **({'default': defaults['host']} if defaults.get('host') else {})): str,
            vol.Optional('cgi_port', default=defaults.get('cgi_port', 443)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional('auth_code'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('installation_qr'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('certificate_sha256'): str,
            vol.Optional('experimental_video', default=defaults.get('experimental_video', False)): bool,
            vol.Optional('experimental_outputs', default=defaults.get('experimental_outputs', False)): bool,
            vol.Optional('opening_code'): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('media_port', default=defaults.get('media_port', 8443)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional('media_certificate_sha256'): str,
        }
        if entry:
            fields[vol.Optional('clear_credentials', default=False)] = bool
        else:
            fields[vol.Required('confirm', default=False)] = bool
        return self.async_show_form(step_id='connect3_reconfigure' if entry else 'connect3',
                                    data_schema=vol.Schema(fields), errors=errors)

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

    async def async_step_v1_cloud_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        try:
            if variant_for(entry.data) != DeviceVariant.V1:
                return self.async_abort(reason='reconfigure_not_supported')
        except ValueError:
            return self.async_abort(reason='unsupported_family')
        errors = {}
        if user_input is not None:
            enabled = user_input.get('v1_cloud_doorbell_enabled', False)
            if type(enabled) is not bool:
                errors['base'] = 'invalid_v1_cloud_config'
            else:
                return self.async_update_reload_and_abort(entry, data_updates={
                    'v1_cloud_doorbell_enabled': enabled,
                })
        return self.async_show_form(step_id='v1_cloud_reconfigure', data_schema=vol.Schema({
            vol.Required('v1_cloud_doorbell_enabled', default=entry.data.get(
                'v1_cloud_doorbell_enabled', False) is True): bool,
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
