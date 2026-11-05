from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .entity import WelcomeEyeEntity
from .capabilities import DeviceVariant
from .connect3.device_info import SD_STATES
from .cloud_status import SUBSCRIPTION_STATES, cloud_subscription_status, supports_cloud_status


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = []
    if hub.capabilities.connect3_read:
        entities.append(WelcomeEyeConnect3Status(hub))
    elif hub.capabilities.r002_probe:
        entities.append(WelcomeEyeProtocolStatus(hub))
    elif hub.capabilities.video_session_diagnostic:
        entities.extend(WelcomeEyeSensor(hub, key, name)
            for key, name in [('resolution', 'Résolution vidéo'), ('fps', 'Cadence vidéo')])
    if supports_cloud_status(hub):
        entities.append(WelcomeEyeCloudSubscription(hub))
    if getattr(hub, 'variant', None) in (DeviceVariant.CONNECT3, DeviceVariant.R002):
        entities.extend(WelcomeEyeDeviceInfoSensor(hub, key)
                        for key in ('reported_firmware', 'sd_card_status'))
    async_add_entities(entities)


class WelcomeEyeDeviceInfoSensor(WelcomeEyeEntity, SensorEntity):
    """Last explicit read, unknown before it; this entity never performs I/O."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub, key):
        super().__init__(hub, key)
        self.key = self._attr_translation_key = key
        self._attr_icon = 'mdi:chip' if key == 'reported_firmware' else 'mdi:micro-sd'
        if key == 'sd_card_status':
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(SD_STATES)

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def native_value(self):
        info = self.hub.device_info_snapshot
        if info is None:
            return None
        if self.key == 'reported_firmware':
            return info['firmware']
        return info['sd_card']['state'] if info['sd_card'] is not None else None

    @property
    def extra_state_attributes(self):
        info = self.hub.device_info_snapshot
        if info is None:
            return {}
        if self.key == 'reported_firmware':
            return {key: info[key] for key in ('model', 'firmware_release_date',
                'reported_capabilities', 'capabilities_source', 'reported_capabilities_verified', 'read_at')}
        return {'read_at': info['read_at'], **(info['sd_card'] or {})}


class WelcomeEyeCloudSubscription(WelcomeEyeEntity, SensorEntity):
    """Last known subscription result; does not claim delivery of every ring."""
    _attr_translation_key = 'cloud_subscription'
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(SUBSCRIPTION_STATES)
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = 'mdi:cloud-check-outline'

    def __init__(self, hub):
        super().__init__(hub, 'cloud_subscription')

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def native_value(self):
        return cloud_subscription_status(self.hub)


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

    async def async_connect3_observe_doorbell(self, operation, duration=90, *, channel=1):
        try:
            return self.hub.doorbell.execute(operation, duration, channel=channel)
        except (ValueError, RuntimeError) as exc:
            # Reasons here are fixed local messages, never network exceptions.
            raise HomeAssistantError(str(exc)) from None

    async def async_connect3_listen_doorbell(self, operation, duration=90):
        diagnostic = getattr(self.hub, 'autonomous_doorbell', None)
        if diagnostic is None:
            raise HomeAssistantError('Autonomous Connect 3 diagnostic unavailable for this device')
        try:
            result = diagnostic.execute(operation, duration)
            if operation == 'stop':
                await diagnostic.wait_closed()
                result = diagnostic.diagnostics()
            return result
        except ValueError as exc:
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
