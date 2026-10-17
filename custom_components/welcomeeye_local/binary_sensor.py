from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from .entity import WelcomeEyeEntity
from .const import RING_HOLD_SECONDS


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = []
    if hub.capabilities.video_session_diagnostic:
        entities.append(WelcomeEyeConnection(hub))
    if hub.capabilities.doorbell:
        if hasattr(hub, 'qv_cloud'):
            entities.append(WelcomeEyeCloudChannelRing(hub, 1))
            if hub.secondary_channel_enabled:
                entities.append(WelcomeEyeCloudChannelRing(hub, 2))
        else:
            entities.append(WelcomeEyeRing(hub))
    async_add_entities(entities)


class WelcomeEyeRing(WelcomeEyeEntity, BinarySensorEntity):
    _attr_translation_key = 'doorbell'
    _attr_icon = 'mdi:doorbell'

    def __init__(self, hub):
        super().__init__(hub, 'ring')

    @property
    def available(self):
        if self.hub.capabilities.cloud_ring:
            cloud = getattr(self.hub, 'v1_cloud', None)
            return not self.hub.stopped and cloud is not None and cloud.connected
        # Keep a received ring visible if the listener disconnects during
        # the pulse. Once it expires, report connectivity normally again.
        return not self.hub.stopped and (self.hub.ring_connected or self.hub.ringing)

    @property
    def is_on(self):
        return self.hub.ringing

    @property
    def extra_state_attributes(self):
        return {
            "ring_hold_seconds": RING_HOLD_SECONDS,
            "listener_mode": "v1_cloud" if self.hub.capabilities.cloud_ring else "connect2_path",
        }


class WelcomeEyeConnection(WelcomeEyeEntity, BinarySensorEntity):
    _attr_translation_key = 'video_session'
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


class WelcomeEyeCloudChannelRing(WelcomeEyeEntity, BinarySensorEntity):
    """Separate channel pulses; no media or capture is acquired by this entity."""
    _attr_icon = 'mdi:doorbell'

    def __init__(self, hub, channel):
        super().__init__(hub, 'ring' if channel == 1 else 'ring_channel_2')
        self.channel = channel
        self._attr_translation_key = 'doorbell' if channel == 1 else 'doorbell_channel_2'

    @property
    def available(self):
        return bool(not self.hub.stopped and self.hub.qv_cloud
                    and (self.hub.qv_cloud.connected or self.is_on))

    @property
    def is_on(self):
        return self.hub.channel_ringing(self.channel)

    @property
    def extra_state_attributes(self):
        return {'channel': self.channel, 'listener_mode': 'qv_cloud',
                'ring_hold_seconds': RING_HOLD_SECONDS}
