"""One conservative support matrix; protocol identity is not a media capability."""
from dataclasses import asdict, dataclass
from enum import StrEnum


class ProtocolFamily(StrEnum):
    LEGACY = 'legacy_owsp'
    R002 = 'r002_experimental'


class DeviceVariant(StrEnum):
    R001 = 'connect2_r001'
    V1 = 'connect_v1'
    R002 = 'connect2_r002'
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
    ring_image_capture: bool = False
    manual_snapshot: bool = False
    last_ring_image: bool = False
    last_snapshot: bool = False
    video_session_diagnostic: bool = False
    r002_probe: bool = False

    def as_dict(self):
        return asdict(self)


_LEGACY = dict(camera=True, live_media=True, downstream_audio=True, talkback=True,
               strike=True, gate=True, manual_snapshot=True, last_snapshot=True,
               video_session_diagnostic=True)
MATRIX = {
    DeviceVariant.R001: DeviceCapabilities(**_LEGACY, local_ring=True,
        ring_image_capture=True, last_ring_image=True),
    DeviceVariant.V1: DeviceCapabilities(**_LEGACY),
    DeviceVariant.LEGACY_UNKNOWN: DeviceCapabilities(**_LEGACY),
    DeviceVariant.R002: DeviceCapabilities(r002_probe=True),
}


def variant_for(data, model=None):
    """Recognize persisted identity; resolution inference is legacy-only."""
    if data.get('protocol_family') == ProtocolFamily.R002:
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
    return ProtocolFamily.R002 if variant == DeviceVariant.R002 else ProtocolFamily.LEGACY


# Exact historical unique-id suffixes AND entity domains. Never match names.
ENTITY_CAPABILITIES = {
    ('camera', 'camera'): 'camera',
    ('binary_sensor', 'connection'): 'video_session_diagnostic',
    ('binary_sensor', 'ring'): 'local_ring',
    ('sensor', 'resolution'): 'video_session_diagnostic',
    ('sensor', 'fps'): 'video_session_diagnostic',
    ('button', 'open_output_1'): 'strike',
    ('button', 'open_output_2'): 'gate',
    ('image', 'last_ring'): 'last_ring_image',
    ('image', 'last_snapshot'): 'last_snapshot',
    ('switch', 'ring_image_capture'): 'ring_image_capture',
    ('sensor', 'protocol_status'): 'r002_probe',
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
