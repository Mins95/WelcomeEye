from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeConnection(entry.runtime_data), WelcomeEyeRing(entry.runtime_data)])


class WelcomeEyeRing(WelcomeEyeEntity, BinarySensorEntity):
    _attr_name = 'Sonnette'
    _attr_icon = 'mdi:doorbell'

    def __init__(self, hub):
        super().__init__(hub, 'ring')

    @property
    def available(self):
        return self.hub.ring_connected

    @property
    def is_on(self):
        return self.hub.ringing


class WelcomeEyeConnection(WelcomeEyeEntity, BinarySensorEntity):
    _attr_name = 'Session vidéo'
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub):
        super().__init__(hub, 'connection')

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.hub.connected
