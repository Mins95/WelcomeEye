from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    if hub.capabilities.connect3_read:
        async_add_entities([WelcomeEyeConnect3Status(hub)])
    elif hub.capabilities.r002_probe:
        async_add_entities([WelcomeEyeProtocolStatus(hub)])
    elif hub.capabilities.video_session_diagnostic:
        async_add_entities([WelcomeEyeSensor(hub, key, name)
            for key, name in [('resolution', 'Résolution vidéo'), ('fps', 'Cadence vidéo')]])


class WelcomeEyeConnect3Status(WelcomeEyeEntity, SensorEntity):
    _attr_translation_key = 'connect3_status'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = 'mdi:lan-pending'

    def __init__(self, hub):
        super().__init__(hub, 'connect3_status')

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def native_value(self):
        return self.hub.status

    @property
    def extra_state_attributes(self):
        return {'protocol_family': self.hub.protocol_family.value,
                'model_source': 'user_declared', 'hardware_validated': False,
                'media_available': self.hub.capabilities.live_media,
                'video_received': self.hub.live.observation.get('decoded_frames', 0) > 0}

    async def async_connect3_read(self, operation, **kwargs):
        if not self.hub.capabilities.connect3_read:
            raise HomeAssistantError('Connect 3 read unavailable')
        try:
            return await self.hub.execute(operation, **kwargs)
        except (ValueError, RuntimeError):
            raise HomeAssistantError('Connect 3 operation unavailable or busy') from None

    async def async_connect3_observe_doorbell(self, operation, duration=90):
        try:
            return self.hub.doorbell.execute(operation, duration)
        except (ValueError, RuntimeError) as exc:
            # Reasons here are fixed local messages, never network exceptions.
            raise HomeAssistantError(str(exc)) from None


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

    async def async_r002_probe(self, types, *, include_header=False, include_response=False):
        if not self.hub.capabilities.r002_probe:
            raise HomeAssistantError('R002 investigation is unavailable for this device')
        try:
            return await self.hub.probe(types, include_header=include_header, include_response=include_response)
        except (ValueError, RuntimeError) as exc:
            raise HomeAssistantError(f'R002 probe unavailable ({type(exc).__name__})') from exc

    async def async_r002_check_certificate(self):
        try:
            return await self.hub.check_certificate()
        except (ValueError, RuntimeError) as exc:
            raise HomeAssistantError(f'R002 certificate check unavailable ({type(exc).__name__})') from exc

    async def async_r002_discover_qv(self, *, include_response=False, include_details=False):
        try:
            return await self.hub.discover_qv(include_response=include_response,
                                            include_details=include_details)
        except (ValueError, RuntimeError) as exc:
            raise HomeAssistantError(f'QV discovery unavailable ({type(exc).__name__})') from exc


    async def async_r002_qv_read(self, operation, *, include_details=False):
        if not getattr(self.hub.capabilities, 'r002_qv_read', False):
            raise HomeAssistantError('R002 QV read unavailable')
        try:
            return await self.hub.execute(operation, include_details=include_details)
        except (ValueError, RuntimeError):
            raise HomeAssistantError('R002 QV operation unavailable or busy') from None

    async def async_r002_observe_doorbell(self, operation, duration=90):
        if not getattr(self.hub.capabilities, 'r002_qv_read', False):
            raise HomeAssistantError('R002 QV observation unavailable')
        try:
            return self.hub.doorbell.execute(operation, duration)
        except (ValueError, RuntimeError):
            raise HomeAssistantError('R002 QV observation unavailable or busy') from None


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
