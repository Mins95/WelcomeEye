# 0.4.6-beta.1 — card appearance and saved-photo history

This prerelease adds card presentation and saved-photo browsing on top of 0.4.5. The stable 0.4.5 tag and package are unchanged. Existing entries, credentials, entity IDs, opening approvals and dashboards are retained.

## Install

1. Back up Home Assistant and your dashboard configuration.
2. Open Philips WelcomeEye in HACS. Enable prerelease versions in its version/download dialog and select **v0.4.6-beta.1**.
3. Download, restart Home Assistant, then fully reload the dashboard/Companion frontend. Keep existing entries and cards.
4. Leave autoplay OFF for your first check.

Manual alternative: extract the release's `welcomeeye_local.zip` directly into `<config>/custom_components/welcomeeye_local/`, with `manifest.json` at that folder's root, then restart HA.

## Change the theme and layout

**Edit dashboard → Edit the WelcomeEye card → Appearance / Apparence**:

- **WelcomeEye:** original teal appearance.
- **Home Assistant theme / Thème Home Assistant:** colors inherited from your active HA theme; no additional theme is installed.

**Layout / Présentation → Compact / Compacte** makes the card more compact. Choose Full / Complète for the default layout. Save the card and dashboard.

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
layout: compact
appearance: theme
show_last_photo: true
show_history: true
autoplay: false
auto_close: 0
```

Replace `camera.your_camera` with your actual entity. For a stack, edit the nested WelcomeEye card. For a JavaScript popup, add `layout: "compact", appearance: "theme",` inside its `await popup({...})` content object, then close and reopen it. [Full instructions and popup example](card.md#change-the-theme-or-use-compact-mode).

## What changes

- A dated saved-photo background and History button show up to 12 photos for the selected entrance. Thumbnails enlarge on click. This only reads stored files; it does not open the camera.
- Sound and Microphone stay independent. Listening never enables the microphone.
- Optional autoplay starts only when the card is visible, muted and with microphone OFF. It never runs in the editor preview.
- Optional automatic closing is disabled by default, or can be set to 30, 60 or 120 seconds after live connection.
- All configured opening controls keep their exact physical targets, regardless of the selected camera.
- Secondary control names lose old generated “essai/trial/prueba” wording. Entity IDs may retain historical suffixes to preserve automations. User-customized names are not overwritten.
- Hide the empty native video surface that could cover the saved photo with a large play icon on tablet WebViews/Tap Scheduler. The new visibility lifecycle is covered in simulated browser tests; confirmation on the affected tablet is still needed.

## Short validation checklist

1. Confirm the saved image and History load before opening video. Try Full/Compact and both appearances; save and refresh to check persistence.
2. Open live video, enable Sound only and verify the microphone remains OFF. Enable/stop the microphone separately if wanted.
3. With two outdoor panels, switch source and verify its image/history; the previous microphone must stop.
4. Set automatic closing to 30 seconds if wanted, then confirm the player closes and Philips can regain access. Reset it to disabled if unwanted.
5. Report model, firmware, browser/Companion, selected options and fresh integration diagnostics for any failure. Do not share credentials or private photographs.

## Validation scope and limitations

The repository checks cover Python, frontend/browser behavior, simulated HA runtimes, HACS, Hassfest and packaging. Network tests use simulated equipment. The previous local card build's saved-photo preview/history were also checked on the maintainer's HA dashboard. That is not a new physical validation of every model or client; beta feedback is still needed for playback and browser variations.

Autoplay opens an actual device session and can interrupt monitor ringing or occupy the Philips connection. Existing manual/automatic capture behavior is unchanged. The stored-photo preview/history do not have that acquisition side effect. HLS upstream session release remains managed by HA. Microphone use requires trusted HTTPS and browser permission.

## Return to stable

Restore your HA backup, or select **v0.4.5** in HACS and restart HA, then fully reload the frontend. The new presentation/history options will no longer be available. Remove those optional card fields if editing with the older editor. Keep your integration entries and camera IDs; no photo-storage migration is performed.
