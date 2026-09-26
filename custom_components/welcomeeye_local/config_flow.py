"""Validate device credentials before creating a configuration entry."""
import voluptuous as vol
from hashlib import sha256
from ipaddress import IPv4Address

from homeassistant import config_entries
from homeassistant.helpers import selector

from .client import AuthenticationError, DiscoveryTimeout, validate_connection
from .capabilities import DeviceVariant, ProtocolFamily
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
                    # No authentication is known for R002. Do not persist unused credentials.
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
        return self.async_show_form(step_id='user', data_schema=schema(user_input), errors=errors)

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
        if entry.data.get('protocol_family') != ProtocolFamily.R002:
            return self.async_abort(reason='reconfigure_not_supported')
        errors = {}
        if user_input is not None:
            try:
                host = str(IPv4Address(user_input['host']))
                if any(other.entry_id != entry.entry_id and other.data.get('host') == host
                       for other in self._async_current_entries()):
                    return self.async_abort(reason='already_configured')
            except ValueError:
                errors['base'] = 'cannot_connect'
            else:
                # Keep the provisional identity unchanged; no discovery or app probe here.
                return self.async_update_reload_and_abort(entry, data_updates={'host': host})
        return self.async_show_form(step_id='reconfigure', data_schema=vol.Schema({
            vol.Required('host', default=entry.data['host']): str,
        }), errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
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
