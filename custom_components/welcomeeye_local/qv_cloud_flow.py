"""Explicit QV cloud enrollment, separate from local access and opening codes."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector

from .capabilities import DeviceVariant, variant_for
from .qv_cloud_enrollment import error_code, record


class QVCloudFlowMixin:
    """Keep enrollment transactional: no push subscription before final approval."""

    @staticmethod
    def _qv_cloud_schema():
        return {vol.Optional('qv_cloud_action', default='keep'): selector.SelectSelector(
            selector.SelectSelectorConfig(options=['keep', 'configure', 'disable'],
                                          translation_key='qv_cloud_action'))}

    def _read_qv_cloud_action(self, user_input):
        action = user_input.get('qv_cloud_action', 'keep')
        if action not in ('keep', 'configure', 'disable'):
            raise ValueError('invalid_cloud_action')
        self._qv_cloud_action = action

    async def _save_reconfiguration(self, entry, updates):
        if variant_for(entry.data) not in (DeviceVariant.CONNECT3, DeviceVariant.R002):
            return self.async_update_reload_and_abort(entry, data_updates=updates)
        action = getattr(self, '_qv_cloud_action', 'keep')
        if action == 'configure':
            self._qv_cloud_pending = (entry, dict(updates), deepcopy(dict(entry.data)))
            self._qv_cloud_devices = None
            self._qv_cloud_login = None
            self._record_qv_enrollment('not_started', 'not_started')
            return await self.async_step_qv_cloud_account()
        if action == 'disable':
            updates = {**updates, 'qv_cloud_enabled': False}
        return self.async_update_reload_and_abort(entry, data_updates=updates)

    def _qv_pending_valid(self):
        pending = getattr(self, '_qv_cloud_pending', None)
        return pending is not None and dict(pending[0].data) == pending[2]

    def _record_qv_enrollment(self, status, stage, code=None):
        self._qv_enrollment = record(self.hass, self._qv_cloud_pending[0].entry_id,
                                     status, stage, code)

    async def _qv_authorized_devices(self, email, password, installation_id, variant):
        from .qv_cloud_api import QvCloudClient
        from .qv_cloud_protocol import profile_for
        from .qv_cloud_discovery import resolve_services
        profile = profile_for(variant)
        self._record_qv_enrollment('running', 'service_discovery')
        services = await resolve_services(profile, installation_id)

        async def resolve_region(region_id):
            self._record_qv_enrollment('running', 'region_discovery')
            resolved = await resolve_services(profile, installation_id, region_id=region_id)
            self._record_qv_enrollment('running', 'account_login')
            return resolved

        client = QvCloudClient(services, installation_id, profile=profile,
                               resolver=resolve_region)
        try:
            self._record_qv_enrollment('running', 'account_login')
            await client.async_login(email, password)
            self._record_qv_enrollment('running', 'device_list')
            devices = await client.async_list_devices()
            return devices, client.services.region_id
        finally:
            await client.async_close()

    async def async_step_qv_cloud_account(self, user_input=None):
        if not self._qv_pending_valid():
            self._qv_cloud_login = None
            return self.async_abort(reason='connect3_config_changed')
        entry, _, previous = self._qv_cloud_pending
        saved = previous.get('qv_cloud_account', {})
        errors = {}
        if user_input is not None:
            self._qv_cloud_login = None
            self._qv_cloud_devices = None
            self._record_qv_enrollment('not_started', 'not_started')
            email = user_input.get('email', '')
            password = user_input.get('password') or saved.get('password', '')
            if saved.get('email') and email != saved['email']:
                errors['base'] = 'qv_cloud_account_changed'
            elif (not isinstance(email, str) or not 3 <= len(email) <= 254
                    or '@' not in email or email != email.strip()
                    or not isinstance(password, str) or not 1 <= len(password) <= 1024
                    or user_input.get('confirm_cloud') is not True):
                errors['base'] = 'qv_cloud_credentials_required'
            else:
                installation_id = saved.get('installation_id') or str(uuid4())
                try:
                    devices, region_id = await self._qv_authorized_devices(
                        email, password, installation_id, str(variant_for(entry.data)))
                except asyncio.CancelledError:
                    self._record_qv_enrollment('cancelled', self._qv_enrollment['stage'], 'cancelled')
                    raise
                except Exception as exc:
                    # Never include exception text: HTTP errors may contain tokens.
                    stage, code = self._qv_enrollment['stage'], error_code(exc)
                    self._record_qv_enrollment('failed', stage, code)
                    errors['base'] = ('qv_cloud_invalid_auth'
                        if stage == 'account_login' and code == 'authentication_failed'
                        else {'service_discovery': 'qv_cloud_discovery_failed',
                              'region_discovery': 'qv_cloud_discovery_failed',
                              'account_login': 'qv_cloud_login_failed',
                              'device_list': 'qv_cloud_device_list_failed'}.get(stage, 'qv_cloud_connect_failed'))
                else:
                    if not devices:
                        self._record_qv_enrollment('failed', 'device_list', 'no_devices')
                        errors['base'] = 'qv_cloud_no_devices'
                    else:
                        self._record_qv_enrollment('awaiting_device', 'device_selection')
                        self._qv_cloud_devices = devices
                        self._qv_cloud_login = dict(email=email, password=password,
                            installation_id=installation_id, region_id=region_id)
                        return await self.async_step_qv_cloud_device()
        return self.async_show_form(step_id='qv_cloud_account', errors=errors,
            description_placeholders={
                'cloud_stage': self._qv_enrollment['stage'],
                'cloud_error': self._qv_enrollment['error_code'] or '—'}
                if self._qv_enrollment['status'] == 'failed' else {},
            data_schema=vol.Schema({
                vol.Required('email', **({'default': saved['email']} if saved.get('email') else {})): str,
                vol.Optional('password'): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
                vol.Required('confirm_cloud', default=False): bool,
            }))

    async def async_step_qv_cloud_device(self, user_input=None):
        if not self._qv_pending_valid() or not getattr(self, '_qv_cloud_login', None):
            self._qv_cloud_login = None
            return self.async_abort(reason='connect3_config_changed')
        devices = self._qv_cloud_devices
        choices = [selector.SelectOptionDict(value=str(index), label=device.name or f'WelcomeEye {index + 1}')
                   for index, device in enumerate(devices)]
        errors = {}
        if user_input is not None:
            choice = user_input.get('device')
            if (choice not in {item['value'] for item in choices}
                    or user_input.get('confirm_device') is not True):
                errors['base'] = 'qv_cloud_device_confirmation'
            else:
                entry, updates, _ = self._qv_cloud_pending
                device = devices[int(choice)]
                account = {**self._qv_cloud_login, 'device': {'uid': device.uid}}
                # Don't allow one cloud device to ring two unrelated HA entries.
                bound_uid = updates.get('credential_device_uid', entry.data.get('credential_device_uid'))
                saved_uid = entry.data.get('qv_cloud_account', {}).get('device', {}).get('uid')
                if (bound_uid and bound_uid != device.uid) or (saved_uid and saved_uid != device.uid):
                    errors['base'] = 'qv_cloud_wrong_device'
                elif any(other.entry_id != entry.entry_id
                       and other.data.get('qv_cloud_enabled') is True
                       and other.data.get('qv_cloud_account', {}).get('device', {}).get('uid') == device.uid
                       for other in self._async_current_entries()):
                    errors['base'] = 'qv_cloud_already_bound'
                else:
                    self._record_qv_enrollment('configured', 'complete')
                    self._qv_cloud_pending = self._qv_cloud_login = self._qv_cloud_devices = None
                    return self.async_update_reload_and_abort(entry, data_updates={
                        **updates, 'qv_cloud_account': account, 'qv_cloud_enabled': True})
        return self.async_show_form(step_id='qv_cloud_device', errors=errors,
            data_schema=vol.Schema({vol.Required('device'): selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices)),
                vol.Required('confirm_device', default=False): bool}))
