from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class WelcomeEyeEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub, key):
        self.hub = hub
        uid = hub.entry.unique_id
        self._attr_unique_id = f'{uid}_{key}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, uid)},
            manufacturer='Philips', model='WelcomeEye Connect 2', name=hub.entry.title)

    @property
    def available(self):
        return self.hub.connected

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.subscribe(self.async_write_ha_state))
