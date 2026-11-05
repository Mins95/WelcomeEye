"""Standard HA doorbell events from the existing validated ring bus."""
from collections.abc import Mapping

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback

from .entity import WelcomeEyeEntity

RING_EVENT_TYPE = 'ring'  # Standard value; also supported before the enum existed.


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = []
    if hub.capabilities.doorbell:
        if hasattr(hub, 'qv_cloud'):
            if hub.capabilities.cloud_ring:
                entities.append(WelcomeEyeDoorbellEvent(hub, 1))
                if hub.secondary_channel_enabled:
                    entities.append(WelcomeEyeDoorbellEvent(hub, 2))
        else:
            entities.append(WelcomeEyeDoorbellEvent(hub))
    async_add_entities(entities)


class WelcomeEyeDoorbellEvent(WelcomeEyeEntity, EventEntity):
    """Observe accepted rings without opening media or starting a receiver.

    Legacy protocol channel codes are retained as reported_channel; they are
    not a verified physical channel mapping. QV supplies verified channels 1/2.
    """

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [RING_EVENT_TYPE]

    def __init__(self, hub, channel=None):
        suffix = '_channel_2' if channel == 2 else ''
        super().__init__(hub, f'doorbell_event{suffix}')
        self._attr_translation_key = f'doorbell{suffix}'
        self.channel = channel
        self._listening = False
        self._last_ring_sequence = self._current_sequence()

    def _current_sequence(self):
        value = getattr(self.hub, '_cloud_ring_sequence' if self.channel else 'ring_count', 0)
        return value if type(value) is int and value >= 0 else 0

    @property
    def available(self):
        if self.hub.stopped or not self.hub.capabilities.doorbell:
            return False
        if self.channel is not None:
            return bool(self.hub.capabilities.cloud_ring and self.hub.qv_cloud
                        and self.hub.qv_cloud.connected)
        return bool((self.hub.capabilities.local_ring
                     and (self.hub.ring_connected or self.hub.ringing))
                    or (self.hub.capabilities.cloud_ring
                        and self.hub.v1_cloud and self.hub.v1_cloud.connected))

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Baseline the active receiver, never replay restored state or old bus
        # events after reload/re-enable. RestoreEntity retains the old timestamp.
        self._last_ring_sequence = self._current_sequence()
        self._listening = True
        unsubscribe = self.hass.bus.async_listen('welcomeeye_local.ring', self._async_ring)

        @callback
        def remove_listener():
            self._listening = False
            unsubscribe()

        self.async_on_remove(remove_listener)

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep the existing subscription and last ring on HA 2026.11 rename."""
        super().async_entity_id_changed(old_entity_id)

    @callback
    def _async_ring(self, event):
        data = event.data
        if (not self._listening or self.hub.stopped or not isinstance(data, Mapping)
                or data.get('entry_id') != self.hub.entry.entry_id):
            return
        sequence, channel = data.get('ring_sequence'), data.get('channel')
        if (type(sequence) is not int or sequence <= self._last_ring_sequence
                or sequence > self._current_sequence() or type(channel) is not int):
            return
        caps = self.hub.capabilities
        if self.channel is not None:
            if (not caps.cloud_ring or data.get('source') != 'cloud_qv'
                    or channel != self.channel
                    or (channel == 2 and not self.hub.secondary_channel_enabled)):
                return
            source = 'cloud_qv'
        elif 'source' not in data and caps.local_ring and 0 <= channel <= 255:
            source = 'local'
        elif data.get('source') == 'cloud' and caps.cloud_ring and 1 <= channel <= 256:
            source = 'cloud'
        else:
            return
        attributes = {'ring_sequence': sequence, 'reported_channel': channel, 'source': source}
        if self.channel is not None:
            attributes['channel'] = self.channel
        self._last_ring_sequence = sequence
        self._trigger_event(RING_EVENT_TYPE, attributes)
        self.async_write_ha_state()
