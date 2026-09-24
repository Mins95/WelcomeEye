# Captures and Home Assistant Media — beta.4 prerelease

This describes the `0.4.2-beta.4` prerelease for testing. New beta.4 physical ring tests are pending. The [audit](audit-beta4.md) records software checks separately from inherited hardware evidence.

## Automatic photos are opt-in

`switch.<device>_ring_image_capture` is named **Ring image capture** / **Capture sur sonnerie**. It starts **OFF**, including when upgrading an existing installation without a saved setting. Changes are stored in the integration's Home Assistant config-entry options and survive reload/restart.

OFF affects photos only. On supported Connect 2 devices, `welcomeeye_local.ring` remains immediate and `binary_sensor.<device>_sonnette` retains its five-second pulse. No photo task or media acquisition is scheduled for a ring while OFF. Turning OFF also invalidates any unfinished automatic photo; it does not delete previously saved photos or the last successful image.

ON starts one fresh capture **at or after T+4 seconds** from the recognized ring. `RING_IMAGE_FALLBACK_DELAY` remains **4.0**: the monitor's native photo was observed around T+2 in prior hardware trials. There is no search for a “capture complete” signal, no photo retry loop, and at most one media acquisition per selected ring. An active video/media session is reused; otherwise the capture temporarily acquires and then releases the existing media worker. A newer ring supersedes an unfinished older ring capture rather than building an unbounded backlog.

The successful automatic capture updates `image.<device>_last_ring`, attempts a Media save, then emits `welcomeeye_local.ring_image`. A storage error preserves the successful JPEG in memory and is exposed as `save_error`; the event has no Media reference when saving failed. An acquisition failure keeps the preceding image and its `image_last_updated` unchanged.

This feature does **not** retrieve the monitor's stored/native photo. On V1 / DES9900VDP, no reliable local ring path was identified in the current trials, so enabling the switch does not make V1 doorbell capture supported. The unsuccessful V1 listener is disabled by default. A cloud path is possible but unproven.

## Manual fresh snapshots

The **Photo** button on the WelcomeEye card calls the same service as an automation:

```yaml
action: welcomeeye_local.capture_snapshot
target:
  entity_id: camera.your_welcomeeye
data:
  save_to_media: true
```

`save_to_media` defaults to `true`. Set it to `false` to update the in-memory image only. The target must be a WelcomeEye camera; normal Home Assistant entity permissions apply. The service records the image generation before acquiring the shared media worker, waits for a strictly newer decoded image, and always releases its lease. It never substitutes a cached JPEG for a fresh request. Concurrent manual requests for the same camera are rejected while a capture is in progress.

Manual captures update **`image.<device>_last_snapshot`**, leaving **`image.<device>_last_ring`** untouched. Neither ImageEntity performs network or disk I/O from its properties or `async_image()`; each returns the most recent successful JPEG held in memory. A new successful image changes `image_last_updated`, which lets Home Assistant refresh it. The in-memory images are cleared by a restart/reload; saved Media files remain on disk and are not automatically loaded back into these entities.

The action supports optional response data, keyed by the target camera entity ID. Each result includes:

| Field | Meaning |
| --- | --- |
| `saved` | Whether the Media write succeeded |
| `media_content_id` | Authenticated Media Source reference, or `null` |
| `filename` | Relative path within the selected Media directory, or `null` |
| `image_entity_id` | Associated last-snapshot ImageEntity, if enabled |
| `captured_at` | Capture timestamp in ISO format |
| `save_error` | Storage exception type, or `null`; no raw path/exception text |

A fresh-image timeout fails the action. A disk failure does **not** fail an otherwise successful in-memory capture: `saved` is false and `save_error` gives its type. `welcomeeye_local.snapshot_saved` is emitted only after a successful manual file save, with the response fields plus `entry_id` and `source`. No image bytes or absolute filesystem path are included in either capture event.

## Card controls

**Photo** works with the viewer open or closed. With live video open it does not close the viewer, mute sound or disable an active microphone. It displays **Photo enregistrée** only when the backend confirms the Media save. A save failure displays **Photo capturée · enregistrement dans Médias impossible** with the error type; an authorization error is reported separately.

Automatic capture is controlled separately by the **Ring image capture / Capture sur sonnerie** switch in Home Assistant. Add that switch to an ordinary HA entities/tile card if you want dashboard access. The WelcomeEye card contains the five controls for sound, microphone, strike, gate and Photo:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
```

Controls wrap into multiple rows on narrow cards. The existing sound, microphone, strike and gate actions remain available under their existing connection and permission conditions. One explicit physical-output action still causes at most one application attempt; timeout and reconnection never replay it.

## Media storage and retention

Home Assistant supports configured `homeassistant.media_dirs`; local Media is usually `/media`, and is protected by HA authentication, unlike `/config/www`. Media Source identifiers use `media-source://media_source/<media_dir>/<path>`. In Container installations, mount a persistent host volume at the configured Media directory. See the official [Media Source documentation](https://www.home-assistant.io/integrations/media_source/) and [Home Assistant core configuration](https://www.home-assistant.io/integrations/homeassistant/).

WelcomeEye resolves `hass.config.media_dirs`, preferring its `local` entry or otherwise the first configured directory. It does not blindly hardcode `/media` and never writes captures into `/config/www`. For example:

```yaml
homeassistant:
  media_dirs:
    local: /media
```

Files are organized under that directory:

```text
WelcomeEye/
  welcomeeye_<opaque-entry-digest>/
    2026-09-24/
      2026-09-24_16-42-31_ring_42.jpg
      2026-09-24_16-45-12_manual.jpg
      2026-09-24_16-45-12_manual_1.jpg
```

The folder suffix is a short hash of the HA config-entry ID. Device names, hardware UID, IP, username and credentials are never used in filenames. Dates and times use the Home Assistant configured timezone. Same-second collisions get a suffix without overwriting an earlier image. A typical reference is `media-source://media_source/local/WelcomeEye/welcomeeye_<opaque-entry-digest>/2026-09-24/2026-09-24_16-45-12_manual.jpg`.

JPEG validation is bounded to 10 MiB and 16 million pixels. Disk work runs in the executor. Bytes are written to a temporary file, flushed and fsynced, then published atomically through an exclusive hard link before temporary cleanup. The final filename never exposes partially written bytes. A filesystem without the required hard-link support produces a visible save error while keeping the in-memory capture. Stopped/superseded work may remove only its own unfinished, unpublished result during cleanup.

**Published captures persist with no automatic retention or deletion.** Disk consumption therefore grows with successful saves. Review the WelcomeEye folder, available disk space and backups, and choose your own archive/removal schedule. Any future integration retention policy should be explicit, optional and documented; beta.4 does not silently delete the user's saved photos.

## Hardware evidence and remaining checks

Earlier Connect 2 tests validated fresh manual snapshots and two consecutive idle-ring captures at T+4. The monitor's own photo survived those two rings. Native-photo loss after opening/closing HA video was also reproduced without automatic HA capture; that issue remains unresolved. These are inherited results, not beta.4 hardware validation. See the [historical beta.3 trial](ring-image-delayed-candidate.md).

Beta.4 still needs physical checks of OFF/ON rings, live-video ring capture, card capture with sound/micro active, restart persistence and saved Media retrieval on the target installation. V1 video, audio, microphone and strike are inherited validated paths; V1 gate remains separately unvalidated, and local V1 doorbell is unsupported. The newer DES9901VDP firmware without UDP 1500 is a separate unsupported variant. No speculative transport is added.
