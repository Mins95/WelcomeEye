"""Explicit output buttons; no inferred physical gate position."""
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeOpenButton(entry.runtime_data, output) for output in (0, 1)])


class WelcomeEyeOpenButton(WelcomeEyeEntity, ButtonEntity):
    def __init__(self, hub, output):
        super().__init__(hub, f'open_output_{output+1}')
        self.output = output
        self._attr_name = 'Ouvrir la gâche' if output == 0 else 'Ouvrir le portail'
        self._attr_icon = 'mdi:gate-open' if output else 'mdi:door-open'

    @property
    def available(self):
        return not self.hub.stopped

    async def async_press(self):
        try:
            await self.hass.async_add_executor_job(self.hub.control.unlock, self.output)
        except TimeoutError as exc:
            raise HomeAssistantError('Aucune confirmation reçue. Vérifiez sur place avant de répéter la commande.') from exc
        except (OSError, ValueError, RuntimeError) as exc:
            if isinstance(exc, OSError):
                detail = type(exc).__name__
            else:
                detail = f'{type(exc).__name__}: {exc}'
            raise HomeAssistantError(
                f'Impossible de confirmer l’ouverture WelcomeEye ({detail})'
            ) from exc
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc
