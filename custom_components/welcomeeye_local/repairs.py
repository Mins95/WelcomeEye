"""Connect 3 TLS Repairs route back to the existing approval config flow."""
import voluptuous as vol

from homeassistant.components import repairs as repairs_platform
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.helpers import issue_registry as ir

from .capabilities import DeviceVariant, variant_for
from .const import DOMAIN

TLS_ISSUE_PREFIX = 'connect3_tls_trust_'
TLS_REASONS = frozenset(('certificate_changed', 'system_ca_failed', 'endpoint_changed', 'certificate_expired'))
TLS_ENDPOINTS = frozenset(('cgi', 'media', 'both'))
_FLOW_TYPE = getattr(repairs_platform, 'FlowType', None)


def tls_issue_id(entry_id):
    """Use only the HA entry ID, never a host, UID, certificate or pin."""
    return f'{TLS_ISSUE_PREFIX}{entry_id}'


def async_report_tls_issue(hass, entry_id, reason, endpoint):
    if reason not in TLS_REASONS or endpoint not in TLS_ENDPOINTS:
        raise ValueError('Unsupported Connect 3 TLS issue')
    ir.async_create_issue(hass, DOMAIN, tls_issue_id(entry_id),
        is_fixable=True, is_persistent=True, severity=ir.IssueSeverity.ERROR,
        translation_key=f'connect3_tls_{reason}',
        translation_placeholders={'config_entry': entry_id},
        data={'entry_id': entry_id, 'reason': reason, 'endpoint': endpoint})


def async_get_tls_issue(hass, entry_id):
    """Restore the fail-closed state after a reload, including ignored issues."""
    issue = ir.async_get(hass).async_get_issue(DOMAIN, tls_issue_id(entry_id))
    if issue is None or not issue.data:
        return None
    reason, endpoint = issue.data.get('reason'), issue.data.get('endpoint')
    if reason not in TLS_REASONS or endpoint not in TLS_ENDPOINTS:
        return None
    return {'reason': reason, 'endpoint': endpoint}


def async_clear_tls_issue(hass, entry_id):
    """Called only after config flow validates and saves an approved replacement."""
    ir.async_delete_issue(hass, DOMAIN, tls_issue_id(entry_id))


class Connect3TLSRepairFlow(RepairsFlow):
    """Let the owner inspect and explicitly approve through normal reconfigure."""

    async def async_step_init(self, user_input=None):
        # Repairs supplies issue_id as init data, not a user's confirmation.
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        entry_id = self.data.get('entry_id') if self.data else None
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        try:
            is_connect3 = entry is not None and variant_for(entry.data) == DeviceVariant.CONNECT3
        except ValueError:
            is_connect3 = False
        if not is_connect3:
            return self.async_abort(reason='entry_not_found')
        if user_input is not None:
            if _FLOW_TYPE is None:
                # HA before 2026.9 cannot hand a Repairs dialog to config flow.
                # Give the existing entry's settings link instead of creating
                # an invisible flow or prematurely resolving the issue.
                return self.async_abort(reason='reconfigure_legacy',
                    description_placeholders={'config_entry': entry_id})
            result = await self.hass.config_entries.flow.async_init(DOMAIN,
                context={'source': SOURCE_RECONFIGURE, 'entry_id': entry_id})
            if 'flow_id' not in result:
                return self.async_abort(reason='reconfigure')
            # Abort preserves the issue until config flow has validated approval.
            return self.async_abort(reason='reconfigure', next_flow=(_FLOW_TYPE.CONFIG_FLOW, result['flow_id']))
        return self.async_show_form(step_id='confirm', data_schema=vol.Schema({}),
                                    description_placeholders={'config_entry': entry_id})


async def async_create_fix_flow(hass, issue_id, data):
    if issue_id.startswith(TLS_ISSUE_PREFIX):
        return Connect3TLSRepairFlow()
    return None
