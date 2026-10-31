# Photos, library and Home Assistant Media — 0.4.5

**Stable 0.4.5** provides the following capture paths:

| Model | Manual photo | Automatic ring photo |
| --- | --- | --- |
| Connect 2 R001 | Viewer open or closed | Local doorbell, capture switch enabled |
| Connect V1 / DES9900VDP | Viewer open or closed | [Optional V1 cloud](v1-cloud-doorbell.md), capture switch enabled |
| Connect 3 | Existing live stream required | Optional cloud doorbell, capture switch enabled |
| Connect 2 R002 | Existing live stream required | Optional cloud doorbell, capture switch enabled |

Cloud ring delivery is confirmed on V1, R002 and Connect 3. Photos use local media; native files stored by the monitor are not retrieved.

## Automatic photos: default and known limitation

**Ring image capture / Capture sur sonnerie** (`switch.<device>_ring_image_capture`) defaults **ON on V1/R001** and **OFF on Connect 3/R002** when no preference is saved. A saved **OFF** preference survives reloads and restarts. The V1 cloud option remains **OFF** by default; enabling it makes this capture switch available with the same ON default.

**Known limitation:** automatic capture starting at the default **T+4 seconds** can stop the native monitor call and outdoor-unit ringing around **five seconds after the press**. Smartphone notifications and ringing in the Philips app continue. Turn **Capture sur sonnerie OFF** to avoid automatic photo acquisition; HA ring detection and events remain active. Live players and camera-thumbnail requests can still acquire media during a call.

Each recognized ring emits `welcomeeye_local.ring` immediately and gives the ring sensor a five-second pulse. With capture ON, one fresh photo starts at or after T+4. For cloud notifications, T is when HA accepts the ring, not the physical button press. **Reconfigure → Photo management** sets this delay from **0 to 120 seconds**, default **4**. A longer delay does not guarantee uninterrupted ringing and the visitor may have left. The capture reuses the shared media session or temporarily acquires and releases it. A newer ring supersedes an unfinished older capture; there is no photo retry loop.

This is **HA's own fresh snapshot from the local media stream**, not the monitor's stored/native photo. A successful capture updates `image.<device>_last_ring`, attempts a save in **Media → WelcomeEye**, then emits `welcomeeye_local.ring_image`. If saving fails, the fresh in-memory image remains available and its `save_error` attribute gives the exception type. If acquisition fails, the previous image and timestamp remain. Turning OFF prevents pending captures and suppresses unfinished results; an acquisition already started may finish before releasing media. Saved photos remain.

## Manual Photo button and service

The card's **Photo** button works with the viewer open or closed on R001/V1.
On Connect 3/R002, first open the wanted panel's live stream. With live video open,
capture preserves the viewer, sound and active microphone. The card confirms
that the photo is saved only after the Media write succeeds; a storage failure
is reported separately from a capture failure.

Automations use the same service:

```yaml
action: welcomeeye_local.capture_snapshot
target:
  entity_id: camera.your_welcomeeye
data:
  save_to_media: true
```

`save_to_media` defaults to `true`; `false` updates only the in-memory image.
The service waits for a newly decoded image rather than substituting a cached
JPEG. R001/V1 release their temporary media lease afterwards; Connect 3/R002 uses
the live stream already open. Concurrent manual requests for the same camera
are rejected. Normal HA entity permissions apply.

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
  - url: /welcomeeye_local/welcomeeye-card.js?v=0.4.5
    type: module
```

Reload the browser or Companion interface after upgrading. Add the card with:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
```

The card keeps each selected opening command available under both camera views
with its fixed physical target. Photo uses the selected camera. Add
**Capture sur sonnerie** to an ordinary HA tile/entities card for dashboard
access to the automatic-photo switch on supported models. See the
[card guide](card.md) for microphone setup.

WelcomeEye uses configured `homeassistant.media_dirs`, preferring `local` or otherwise the first directory. It saves under `WelcomeEye/welcomeeye_<opaque-entry-digest>/<date>/`, with a channel suffix for separate sources where applicable; names use the HA timezone and do not contain device names, UID, IP or credentials. Same-second captures receive unique filenames. Media is protected by HA authentication; captures are never placed in `/config/www`. Container installations need a persistent volume for the selected Media directory. See [HA Media Source](https://www.home-assistant.io/integrations/media_source/).

## Photo library and retention

Administrators can open **Photos WelcomeEye** in the sidebar, choose a device
and day, and view its saved images or open them in Media. Delete a selection,
one day or all photos of that device, or clean up empty folders. Each operation
shows its scope and count for confirmation before permanent deletion. Other
devices' photos remain separate.

Under **Reconfigure → Photo management**, choose **Unlimited** (the default),
**7**, **30** or **90 days**. A finite value enables daily cleanup of that
device's older saved captures. It does not enable or disable captures or
change the known ringing side effect. Saved photos survive reloads and
restarts unless removed manually or by the selected retention policy.

**Show photo library in the sidebar** can be disabled for every model. This hides the administrator panel without deleting photos or removing **Media → WelcomeEye** access. With several entries, the shared panel stays visible while any loaded entry enables it.

The automatic delay setting applies to each supported source of the device. A busy incompatible session is not forcibly interrupted for a photo; failures retain the previous valid image. The five-second sensor pulse is independent of this delay.
