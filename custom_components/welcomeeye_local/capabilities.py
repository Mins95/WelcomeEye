"""One conservative support matrix; protocol identity is not a media capability."""
from dataclasses import asdict, dataclass, replace
from enum import StrEnum


class ProtocolFamily(StrEnum):
    LEGACY = 'legacy_owsp'
    R002 = 'r002_experimental'
    CONNECT3 = 'connect3_qv_experimental'


class DeviceVariant(StrEnum):
    R001 = 'connect2_r001'
    V1 = 'connect_v1'
    R002 = 'connect2_r002'
    CONNECT3 = 'connect3'
    LEGACY_UNKNOWN = 'legacy_unknown'


@dataclass(frozen=True)
class DeviceCapabilities:
    camera: bool = False
    live_media: bool = False
    downstream_audio: bool = False
    talkback: bool = False
    strike: bool = False
    gate: bool = False
    local_ring: bool = False
    cloud_ring: bool = False
    ring_image_capture: bool = False
    manual_snapshot: bool = False
    last_ring_image: bool = False
    last_snapshot: bool = False
    video_session_diagnostic: bool = False
    r002_probe: bool = False
    r002_qv_read: bool = False
    connect3_read: bool = False

    def as_dict(self):
        return asdict(self)

    @property
    def doorbell(self):
        return self.local_ring or self.cloud_ring

    def with_cloud_ring(self, enabled):
        """The V1 cloud opt-in unlocks the existing local snapshot consumer."""
        if self.cloud_ring == (enabled is True):
            return self
        return replace(self, cloud_ring=enabled is True,
                       ring_image_capture=self.local_ring or enabled is True,
                       last_ring_image=self.local_ring or enabled is True)


_LEGACY = dict(camera=True, live_media=True, downstream_audio=True, talkback=True,
               strike=True, gate=True, manual_snapshot=True, last_snapshot=True,
               video_session_diagnostic=True)
MATRIX = {
    DeviceVariant.R001: DeviceCapabilities(**_LEGACY, local_ring=True,
        ring_image_capture=True, last_ring_image=True),
    DeviceVariant.V1: DeviceCapabilities(**_LEGACY),
    DeviceVariant.LEGACY_UNKNOWN: DeviceCapabilities(**_LEGACY),
    DeviceVariant.R002: DeviceCapabilities(r002_probe=True, r002_qv_read=True),
    DeviceVariant.CONNECT3: DeviceCapabilities(connect3_read=True),
}


def connect3_capabilities(video_enabled=False, outputs_enabled=False, media_transport='tls',
                          tcp_controls_enabled=False):
    """Shared live video/audio requires owner opt-in; no hardware claim follows."""
    video = video_enabled is True and media_transport in ('tls', 'connect3_tcp')
    full_media = video and (media_transport == 'tls' or tcp_controls_enabled is True)
    return replace(MATRIX[DeviceVariant.CONNECT3], camera=video,
                   live_media=video, downstream_audio=video,
                   talkback=full_media,
                   strike=full_media and outputs_enabled is True,
                   gate=full_media and outputs_enabled is True)


def r002_capabilities(video_enabled=False, outputs_enabled=False):
    """Explicit QV trial; device identity and diagnostic services remain R002."""
    return replace(connect3_capabilities(video_enabled, outputs_enabled),
                   connect3_read=False, r002_probe=True, r002_qv_read=True)


def variant_for(data, model=None):
    """Recognize persisted identity; resolution inference is legacy-only."""
    family = data.get('protocol_family')
    if 'protocol_family' in data and family not in tuple(ProtocolFamily):
        raise ValueError('Unsupported protocol family')
    declared = data.get('device_variant')
    if 'device_variant' in data and declared not in tuple(DeviceVariant):
        raise ValueError('Unsupported device variant')
    if family == ProtocolFamily.CONNECT3:
        if declared not in (None, DeviceVariant.CONNECT3):
            raise ValueError('Conflicting device family')
        return DeviceVariant.CONNECT3
    if declared == DeviceVariant.CONNECT3:
        raise ValueError('Connect 3 requires its explicit QV family')
    if data.get('protocol_family') == ProtocolFamily.R002:
        if declared not in (None, DeviceVariant.R002):
            raise ValueError('Conflicting device family')
        return DeviceVariant.R002
    firmware = data.get('firmware', '')
    if isinstance(firmware, str) and firmware.startswith('V401.R002.'):
        return DeviceVariant.R002
    try:
        return DeviceVariant(data['device_variant'])
    except (KeyError, ValueError, TypeError):
        pass
    if model is None:
        model = data.get('detected_model')
    if model == 'WelcomeEye Connect V1':
        return DeviceVariant.V1
    if model == 'WelcomeEye Connect 2' or (
        isinstance(firmware, str) and firmware.startswith('V401.R001.')
    ):
        return DeviceVariant.R001
    # Existing entries were authenticated with OWSP. New entries reach this
    # case only after the same legacy validation succeeds.
    return DeviceVariant.LEGACY_UNKNOWN


def family_for(variant):
    variant = DeviceVariant(variant)  # Unknown variants never select legacy.
    return {DeviceVariant.R002: ProtocolFamily.R002,
            DeviceVariant.CONNECT3: ProtocolFamily.CONNECT3}.get(variant, ProtocolFamily.LEGACY)


# Exact historical unique-id suffixes AND entity domains. Never match names.
ENTITY_CAPABILITIES = {
    ('camera', 'camera'): 'camera',
    ('binary_sensor', 'connection'): 'video_session_diagnostic',
    ('binary_sensor', 'ring'): 'doorbell',
    ('sensor', 'resolution'): 'video_session_diagnostic',
    ('sensor', 'fps'): 'video_session_diagnostic',
    ('button', 'open_output_1'): 'strike',
    ('button', 'open_output_2'): 'gate',
    ('image', 'last_ring'): 'last_ring_image',
    ('image', 'last_snapshot'): 'last_snapshot',
    ('switch', 'ring_image_capture'): 'ring_image_capture',
    ('sensor', 'protocol_status'): 'r002_probe',
    ('sensor', 'connect3_status'): 'connect3_read',
}


def unsupported_entity_ids(entries, entry_id, unique_id, capabilities):
    """Pure registry selection, scoped to this entry and our known identifiers."""
    known = {(domain, f'{unique_id}_{suffix}'): capability
             for (domain, suffix), capability in ENTITY_CAPABILITIES.items()}
    for entity in entries:
        if entity.config_entry_id != entry_id or entity.platform != 'welcomeeye_local':
            continue
        capability = known.get((entity.domain, entity.unique_id))
        if capability is not None and not getattr(capabilities, capability):
            yield entity.entity_id
