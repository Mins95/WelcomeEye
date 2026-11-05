# WelcomeEye 0.4.6 — stable

Released 5 November 2026. Existing entries, camera/entity IDs, local credentials,
certificate approvals, enabled outputs and custom dashboard labels are preserved.

## What changes

- **Card appearance:** choose WelcomeEye colors or the active Home Assistant theme,
  independently from Full/Compact layout. Edit the WelcomeEye card itself, including
  when nested in a stack or popup. [Graphical editor and YAML](card.md#change-the-theme-or-use-compact-mode).
- **Saved photos:** dated background preview and an inline history of up to 12
  saved photos per entrance. Reading saved files never opens device media.
  The inactive native video surface no longer covers photos on Android/WebView
  displays such as Tap Scheduler. Deletion remains in the administrator library.
- **Playback:** optional autoplay starts muted with the microphone off; automatic
  closing offers 30, 60 or 120 seconds. Both are disabled by default. Listening
  never enables the microphone; it always requires its own action.
- **Cloud status:** visible receiver and subscription entities on all cloud models.
- **Older Connect V1 / DES9900VDP:** support for its legacy local protocol after
  explicit device-specific approval, plus firmware and login diagnostics.
- **Presentation:** current guides and interface use normal product labels while
  retaining security prompts and precise descriptions of unsupported functions.

## Install or update

1. Download **v0.4.6** in HACS and restart Home Assistant.
2. Fully reload the dashboard or Companion frontend. Close and reopen any popup.
3. Keep existing integration entries and cards. Use Reconfigure to change device
   options; use the card editor for appearance, layout and button names.
4. With YAML-managed resources, update the existing module URL to
   `/welcomeeye_local/welcomeeye-card.js?v=0.4.6`. Do not add a duplicate resource.

[Full installation guide](installation.md) · [Settings](settings.md) · [Card](card.md)

## Cloud status

The device page's Diagnostic section shows **Cloud connection** and **Doorbell
subscription** for V1, R002 and Connect 3. Connectivity is independent of the
last subscription result: Confirmed does not guarantee delivery of every press.
With cloud disabled, the entities remain visible as Disconnected/Disabled and no
registration is started. Updates come from existing callbacks, without extra polling.

V1 enrollment does not request an app-account login. R002/Connect 3 use the official
app account and the selected device. [Cloud settings](settings.md#cloud-doorbell-notifications)
and [troubleshooting](troubleshooting.md#doorbell-sensor-does-not-react).

## Older V1 and firmware information

Setup recognizes the older unprotected V1 protocol and asks for approval before
sending its local password. Credentials, media and commands are unencrypted on
this connection. Approval applies only to the configured address/device identifier;
it does not independently authenticate the hardware. Protected devices never
fall back to it automatically. [Setup details](installation.md#older-connect-v1-devices).

One read-only firmware query runs during approved legacy setup, with a two-second
and 16-frame bound. It opens no video and operates no relay. Diagnostics retain
the returned `AppCom`, `SolCom`, `ReleaseTime` and `HDVersion` fields, if available,
with an explicit status when no version could be read. It is not a periodic poll.

## Field evidence and publication checks

The maintainer requested stable promotion. An older V1 / DES9900VDP owner
[reports successful operation](https://github.com/Mins95/WelcomeEye/issues/14#issuecomment-6613467209)
with the implementation included here. This is an overall user report, not a
new function-by-function hardware checklist or a supplied firmware diagnostic.
The existing validated model support is retained in the [compatibility table](../README.md#-supported-devices).

Publication requires the full Validate workflow to pass for the exact main commit:
Python, frontend/browser, simulated Home Assistant runtime, certificate compatibility,
HACS, Hassfest and reproducible packaging. Automated checks use simulated devices;
they do not operate physical relays. The release workflow verifies the downloaded
archive checksum before publishing. [Validation workflow](https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml).

## Known limits

- Acquiring media for a fresh photo, thumbnail or live view can stop an ongoing
  indoor/outdoor ringing cycle. Official Philips app notifications continue.
  The configurable photo delay does not guarantee avoiding this interruption.
- Saved-photo preview/history only read stored files; autoplay deliberately opens
  a real stream and can occupy the connection needed by Philips.
- QV TCP and the older V1 protocol retain their explicit security warnings.
  CGI certificate checks, fixed opening targets and one-attempt/no-replay controls
  are unchanged. [Security](../SECURITY.md).
- Native monitor-photo retrieval and independent local Connect 3/R002 bell
  detection remain unavailable; the supported bell route on these models is cloud.

## Rollback

Take a Home Assistant backup first. The previous stable **v0.4.5** remains available
in HACS: select it, restart HA and reload the frontend without deleting entries.
It lacks the new card features, visible cloud-status entities and older unprotected
V1 support. Those older V1 devices will again fail setup/connection on that version.

The [immediately preceding build](https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.6-beta.3)
also remains available with the same media/protocol implementation. Restoring an
older integration version does not restore photos already deleted or removed by retention.
