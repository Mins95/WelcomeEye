# Captures and Home Assistant Media

**Stable 0.4.3** supports fresh manual photos on Connect 2 R001 and V1 / DES9900VDP. Automatic ring photos are available on Connect 2 R001 and on V1 with [optional cloud doorbell notifications](v1-cloud-doorbell.md) enabled. V1 cloud rings and automatic photos have been physically validated. R002 and Connect 3 do not expose automatic ring capture.

## Automatic photos: default and known limitation

**Ring image capture / Capture sur sonnerie** (`switch.<device>_ring_image_capture`) defaults **ON** when no preference is saved, including after an upgrade. A saved **OFF** preference survives reloads and restarts. The V1 cloud option remains **OFF** by default; enabling it makes this capture switch available with the same ON default.

**Known limitation:** automatic capture starting at **T+4 seconds** can stop the native monitor call and outdoor-unit ringing around **five seconds after the press**. Smartphone notifications and ringing in the Philips app continue. Turn **Capture sur sonnerie OFF** to avoid automatic photo acquisition; HA ring detection and events remain active. Live players and camera-thumbnail requests can still acquire media during a call.

Each recognized ring emits `welcomeeye_local.ring` immediately and gives the ring sensor a five-second pulse. With capture ON, one fresh photo starts at or after T+4. For V1, T is when HA accepts the cloud ring, not the physical button press. The capture reuses the shared media session or temporarily acquires and releases it. A newer ring supersedes an unfinished older capture; there is no photo retry loop.

This is **HA's own fresh snapshot from the local media stream**, not the monitor's stored/native photo. A successful capture updates `image.<device>_last_ring`, attempts a save in **Media → WelcomeEye**, then emits `welcomeeye_local.ring_image`. If saving fails, the fresh in-memory image remains available and its `save_error` attribute gives the exception type. If acquisition fails, the previous image and timestamp remain. Turning OFF prevents pending captures and suppresses unfinished results; an acquisition already started may finish before releasing media. Saved photos remain.

## Manual Photo button and service

The card's **Photo** button works with the viewer open or closed. With live video open it preserves the viewer, sound and active microphone. It shows **Photo enregistrée** only after the Media save succeeds; a storage failure is reported separately from a capture failure.

Automations use the same service:

```yaml
action: welcomeeye_local.capture_snapshot
target:
  entity_id: camera.your_welcomeeye
data:
  save_to_media: true
```

`save_to_media` defaults to `true`; `false` updates only the in-memory image. The service waits for a newly decoded image rather than substituting a cached JPEG, then releases its media lease. Concurrent manual requests for the same camera are rejected. Normal HA entity permissions apply.

Manual photos update **`image.<device>_last_snapshot`** and leave **Last ring** untouched. Both image entities hold the latest successful JPEG in memory. Reload/restart clears these in-memory images; saved Media files remain on disk.

Optional service response data is keyed by camera entity ID:

| Field | Meaning |
| --- | --- |
| `saved` | Whether the Media write succeeded |
| `media_content_id` | Authenticated Media Source reference, or `null` |
| `filename` | Relative Media path, or `null` |
| `image_entity_id` | Last snapshot image entity, if enabled |
| `captured_at` | ISO capture timestamp |
| `save_error` | Storage exception type, or `null` |

A fresh-image timeout fails the action. A storage failure leaves a successful in-memory capture available. `welcomeeye_local.snapshot_saved` fires only after a successful manual save. Neither capture event includes image bytes or an absolute filesystem path.

## Card and storage

The integration automatically registers and updates its card resource when HA manages resources in the UI. For YAML resources, use the manual fallback:

```yaml
resources:
  - url: /welcomeeye_local/welcomeeye-card.js?v=0.4.3
    type: module
```

Reload the browser or Companion interface after upgrading. Add the card with:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
```

On Connect 2 R001 and V1, sound, microphone, strike, gate and Photo stay on one compact row. Add **Capture sur sonnerie** to an ordinary HA tile/entities card for dashboard access to the automatic-photo switch. See the [intercom guide](intercom-beta1.md) for microphone setup.

WelcomeEye uses configured `homeassistant.media_dirs`, preferring `local` or otherwise the first directory. It saves under `WelcomeEye/welcomeeye_<opaque-entry-digest>/<date>/`; names use the HA timezone and do not contain device names, UID, IP or credentials. Same-second captures receive unique filenames. Media is protected by HA authentication; captures are never placed in `/config/www`. Container installations need a persistent volume for the selected Media directory. See [HA Media Source](https://www.home-assistant.io/integrations/media_source/).

**Saved captures have no automatic retention or deletion.** Review disk usage and choose your own archive/removal schedule. Historical trials are recorded separately in [stable 0.4.2 evidence](stable-042.md) and the [beta.3 trial](ring-image-delayed-candidate.md).
