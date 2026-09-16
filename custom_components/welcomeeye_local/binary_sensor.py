from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from .entity import WelcomeEyeEntity
from .const import RING_HOLD_SECONDS


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeConnection(entry.runtime_data), WelcomeEyeRing(entry.runtime_data)])


class WelcomeEyeRing(WelcomeEyeEntity, BinarySensorEntity):
    _attr_name = 'Sonnette'
    _attr_icon = 'mdi:doorbell'

    def __init__(self, hub):
        super().__init__(hub, 'ring')

    @property
    def available(self):
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
            "listener_mode": (
                "experimental_connect2_path_on_v1"
                if self.hub.device_model == "WelcomeEye Connect V1"
                else "connect2_path"
            ),
        }


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
