"""Explicit output buttons; no inferred physical gate position."""
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = [WelcomeEyeOpenButton(hub, output)
        for output, supported in ((0, hub.capabilities.strike), (1, hub.capabilities.gate))
        if supported]
    if hub.capabilities.connect3_read and (hub.capabilities.strike or hub.capabilities.gate):
        entities.extend(WelcomeEyeSecondOutputButton(hub, target)
            for target in ('strike_2', 'gate_2') if hub.control.target_enabled(target))
    async_add_entities(entities)


class WelcomeEyeOpenButton(WelcomeEyeEntity, ButtonEntity):
    def __init__(self, hub, output):
        super().__init__(hub, f'open_output_{output+1}')
        self.output = output
        self._attr_name = 'Ouvrir la gâche' if output == 0 else 'Ouvrir le portail'
        self._attr_icon = 'mdi:gate-open' if output else 'mdi:door-open'

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def extra_state_attributes(self):
        return {'welcomeeye_output_target': 'strike_1' if self.output == 0 else 'gate_1',
                'welcomeeye_output_channel': 1, 'welcomeeye_output_number': self.output + 1}

    async def async_press(self):
        try:
            if (self.hub.capabilities.connect3_read
                    or getattr(self.hub.capabilities, 'r002_qv_read', False)):
                await self.hub.control.unlock(self.output)
            else:
                await self.hass.async_add_executor_job(self.hub.control.unlock_for_ha, self.output)
        except Exception as exc:
            if getattr(exc, 'physical_request_uncertain', False):
                message = ('La commande a pu être envoyée. Vérifiez sur place avant de réessayer.')
            else:
                message = f'Impossible de confirmer l’ouverture WelcomeEye ({type(exc).__name__})'
            raise HomeAssistantError(message) from exc


class WelcomeEyeSecondOutputButton(WelcomeEyeEntity, ButtonEntity):
    """An explicitly enabled physical trial with a fixed channel and relay."""

    def __init__(self, hub, target):
        self.target = target
        self.output = 1 if target == 'strike_2' else 2
        super().__init__(hub, f'open_channel_2_output_{self.output}')
        self._attr_translation_key = f'{target}_trial'
        self._attr_icon = 'mdi:door-open' if self.output == 1 else 'mdi:gate-open'

    @property
    def available(self):
        return not self.hub.stopped and self.hub.control.target_enabled(self.target)

    @property
    def extra_state_attributes(self):
        return {'welcomeeye_output_target': self.target,
                'welcomeeye_output_channel': 2, 'welcomeeye_output_number': self.output,
                'validation_status': 'hardware_pending'}

    async def async_press(self):
        try:
            await self.hub.control.unlock_target(self.target)
        except Exception as exc:
            message = ('La commande a pu être envoyée. Vérifiez sur place avant de réessayer.'
                if getattr(exc, 'physical_request_uncertain', False)
                else f'Impossible de confirmer l’ouverture WelcomeEye ({type(exc).__name__})')
            raise HomeAssistantError(message) from exc
