from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    if hub.capabilities.r002_probe:
        async_add_entities([WelcomeEyeProtocolStatus(hub)])
    elif hub.capabilities.video_session_diagnostic:
        async_add_entities([WelcomeEyeSensor(hub, key, name)
            for key, name in [('resolution', 'Résolution vidéo'), ('fps', 'Cadence vidéo')]])


class WelcomeEyeProtocolStatus(WelcomeEyeEntity, SensorEntity):
    _attr_translation_key = 'protocol_status'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = 'mdi:lan-pending'

    def __init__(self, hub):
        super().__init__(hub, 'protocol_status')

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def native_value(self):
        return self.hub.status

    @property
    def extra_state_attributes(self):
        return {'protocol_family': self.hub.protocol_family.value,
                'detection_confidence': self.hub.detection_confidence,
                'last_probe_at': self.hub.last_probe_at.isoformat() if self.hub.last_probe_at else None,
                'last_probe_status': self.hub.last_probe_status,
                'last_error_type': self.hub.last_error_type}

    async def async_r002_probe(self, types):
        if not self.hub.capabilities.r002_probe:
            raise HomeAssistantError('R002 investigation is unavailable for this device')
        try:
            return await self.hub.probe(types)
        except (ValueError, RuntimeError) as exc:
            raise HomeAssistantError(f'R002 probe unavailable ({type(exc).__name__})') from exc


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
