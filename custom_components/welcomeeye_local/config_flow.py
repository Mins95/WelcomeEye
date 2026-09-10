"""Validate device credentials before creating a configuration entry."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .client import AuthenticationError, validate_connection
from .const import DOMAIN, DEFAULT_NAME


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
                uid = await self.hass.async_add_executor_job(validate_connection,
                    user_input['host'], user_input['username'], user_input['password'])
            except AuthenticationError:
                errors['base'] = 'invalid_auth'
            except (ValueError, OSError):
                errors['base'] = 'cannot_connect'
            except Exception:
                errors['base'] = 'unsupported_device'
            else:
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_configured(updates={'host': user_input['host']})
                return self.async_create_entry(title=DEFAULT_NAME, data={**user_input, 'uid': uid})
        return self.async_show_form(step_id='user', data_schema=schema(user_input), errors=errors)

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
