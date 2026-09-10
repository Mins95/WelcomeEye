from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeSensor(entry.runtime_data, key, name)
        for key, name in [('resolution', 'Résolution vidéo'), ('fps', 'Cadence vidéo')]])


class WelcomeEyeSensor(WelcomeEyeEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub, key, name):
        super().__init__(hub, key)
        self.key = key
        self._attr_name = name
        if key == 'fps':
            self._attr_native_unit_of_measurement = 'fps'
            self._attr_icon = 'mdi:filmstrip'
        else:
            self._attr_icon = 'mdi:video'

    @property
    def native_value(self):
        fmt = self.hub.format
        if not fmt:
            return None
        return f'{fmt.width} × {fmt.height}' if self.key == 'resolution' else fmt.fps
