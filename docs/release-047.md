# WelcomeEye 0.4.7 — stable

Released 7 November 2026, promoting **0.4.7-beta.3** at the maintainer's request.
The integration's functional code is unchanged from that release; only its
version metadata changes. Existing entries, entity IDs, passwords, certificate
approvals, enabled outputs and custom dashboard names are retained.

## What changes since 0.4.6

- **Auxiliary camera:** legacy V1 / Connect 2 R001 monitors can select input 3
  instead of the second outdoor panel on input 2. Thanks to
  [@rpouetpouet and PR #16](https://github.com/Mins95/WelcomeEye/pull/16).
  Auxiliary inputs provide video/photos, without microphone or opening controls.
- **Automations:** standard HA doorbell event entities complement the existing
  sensors and bus events. An optional blueprint sends the photo already saved
  for a ring, without taking another snapshot.
- **Card:** saved-photo zoom/navigation, browser microphone-processing options
  and an optional live stream quality panel. Sound and microphone stay separate.
- **Administrator diagnostics:** voluntary Connect 3/R002 cloud-history, badge
  catalogue and device/SD reads, without background polling or device writes.
- **Reliability:** HA 2026.11 entity renaming, player cancellation, shared media
  leases and QV cleanup are hardened. Legacy H.264 decoding recovers directly
  on a valid image after a corrupt image, with explicit recovery counters.

[Feature settings](development-options.md) · [Reliability details](development-hardening.md)

## Install or update

1. Select **v0.4.7** in HACS, install it and restart Home Assistant.
2. Fully reload the dashboard/Companion frontend and reopen any card popup.
3. Keep existing integration entries and cards. Use **Reconfigure** for device
   settings, or the card editor for appearance and microphone preferences.
4. If resources are managed in YAML, update the existing JavaScript module to
   `/welcomeeye_local/welcomeeye-card.js?v=0.4.7`; do not add a duplicate.

[Full installation guide](installation.md) · [Settings](settings.md) · [Card](card.md)

## Import the photo notification blueprint

The optional blueprint requires **Home Assistant 2026.7 or newer** and is
imported separately from the integration. Open **Settings → Automations &
scenes → Blueprints → Import blueprint**, then use:

```text
https://github.com/Mins95/WelcomeEye/blob/v0.4.7/blueprints/automation/welcomeeye_local/ring_photo_notification.yaml
```

Create an automation from **WelcomeEye - Saved doorbell photo notification**.
Select the device and its **Last doorbell image**, the exact
`notify.mobile_app_...` action and a relative dashboard path. Enable automatic
ring capture separately if wanted; importing the blueprint does not enable it.
Use a separate automation for each supported channel. The phone must be able
to access Home Assistant; photo retention/deletion can invalidate older links.

## Validation evidence and limits

The source beta passed all 16 jobs of
[Validate for b46a77e](https://github.com/Mins95/WelcomeEye/actions/runs/42205168280).
The stable promotion requires a new successful Validate run for its exact
current-main commit, including Python/PyAV matrices, frontend/browser tests,
isolated Home Assistant runtimes, HACS, Hassfest and reproducible packaging.
The release workflow downloads the uploaded archive and checks its digest
and contents before publishing. Tests do not contact a user's intercom or
operate a physical relay.

The [existing model confirmations](../README.md#-supported-devices) are retained.
The contributor confirmed auxiliary-camera video/photos and normal Philips
access after closing HA in [issue #15](https://github.com/Mins95/WelcomeEye/issues/15).
Stable promotion does not add a new hardware result for H.264 error recovery,
every firmware-specific diagnostic read or Companion attachment delivery.

- Acquiring media for a photo or live view can interrupt monitor ringing.
  The photo delay is configurable; official app notifications continue.
- Connect 3/R002 doorbells use their supported cloud route. Native monitor-photo
  retrieval and independent local bell detection are not added.
- A badge catalogue is not a passage detector or proof of the badge holder's
  identity. V1/R001 badge-catalogue reads remain unsupported.
- Explicit transport/certificate approvals, fixed physical targets and the
  single-attempt/no-replay rule for opening commands are unchanged.

## Rollback

Back up HA before updating. **v0.4.7-beta.3** remains available with the same
functional code. The previous stable **v0.4.6** also remains available in HACS.
Select the desired version, restart HA and reload the frontend; keep entries.
Use the matching version in any manually managed resource URL.

Before returning to 0.4.6, disable automations using the new event entities or
photo blueprint. Imported blueprints are not removed by HACS. That version
ignores the auxiliary-input selector and uses input 2: disable the second
source if only an input-3 camera is connected. Downgrading does not restore
photos already deleted manually or by retention.
