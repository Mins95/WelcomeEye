# 0.4.7 reliability and Home Assistant compatibility

This page documents the reliability changes included in **0.4.7 stable**.
Existing entries, dashboard configuration
and stored permissions are retained when updating. Hardware validation is
separate from the synthetic software checks below. See the [stable installation
and rollback guide](release-047.md) for the release workflow.

## Card and media lifecycle

- Opening the player has a bounded wait for its settings. Closing, removing or
  hiding the card cancels that wait; a late answer cannot restart the player.
- A WebRTC offer belongs to one player connection. Home Assistant reconnection
  must not resubscribe that old offer and acquire a new device session.
- Autoplay uses the latest visibility observation, ignores callbacks from an
  old mount and remains opt-in. Saved-photo browsing does not acquire media.
- QV sessions have one owner, connector and reader. Duplicate starts are
  rejected; a connection completing during closure is released before setup.
  Closed sessions reject queued writes, apart from their single teardown.
- A replacement local HA/HomeKit reader reserves its shared media lease while
  the previous encoder drains, avoiding an unnecessary source restart. Leaving
  during that wait cancels the acquisition and releases only that reader.
- Sound does not enable the microphone. Channel switching, physical-command
  routing, opening permissions and capture timing retain their existing rules.

The transferable ideas from [WebRTC Camera](https://github.com/AlexxIT/WebRTC)
and [go2rtc](https://github.com/AlexxIT/go2rtc) concern on-demand lifecycle,
sharing and consumer cleanup. No dependency or permanently running restreamer
is added. Our existing media owners remain responsible for device access.
No third-party implementation was copied for these changes.

## Home Assistant 2026.11

The new [entity rename lifecycle](https://contributors.home-assistant.io/blog/2026/10/12/new-entity-lifecycle-hooks/)
allows compatible entities to remain loaded when their entity ID changes.
WelcomeEye opts in without discarding camera viewers, saved photos or capture
preferences, and refreshes internal photo/switch references to the new ID.
Older supported Core versions retain their original lifecycle.

The isolated validation matrix includes Core 2026.11.0. The minimum Home
Assistant version and pinned media dependencies are not changed by this work.
Cloud bootstrap SSL context construction runs outside the event loop, following
[Home Assistant's blocking-operation guidance](https://contributors.home-assistant.io/core/internals/blocking-operations/).

The new native camera talkback feature is not advertised yet: WelcomeEye still
needs its explicit microphone controls, consent and session cleanup. Advertising
support without connecting those lifecycles would be misleading.

## Targeted APK findings

Two independent read paths are available for deliberate administrator actions:

- [Cloud history](cloud-history.md): bounded reading of records already stored
  by the vendor, without replaying a ring or modifying history.
- [Badge catalogue](badge-catalog.md): configured badge identifiers and names,
  when the device supports the SDK's read-only CGI command.

An RF-card opening method in history does not by itself identify a badge or
its owner. No unverified field is used to infer that association.
The generic CGI opening command also remains unused: its presence in an APK
does not establish the per-firmware encoding or physical relay mapping.

The older GooLink/UDT implementations and notification research do not establish
a safe replacement for validated transports. No protocol fallback, extra cloud
subscription, network scan or speculative physical command is introduced.

## Validation boundaries

The regression fixtures use synthetic certificates, in-memory transports and
loopback servers. Browser fixtures mock Home Assistant and prevent device
requests. Real Core checks run in isolated CI containers with simulated network
boundaries. A local test run does not imply that GitHub Actions or hardware
validation has run.

Local check results on 5 November 2026:

- Python: 1,772 tests, 1,766 passed, 5 skipped, 1 existing environment-specific
  failure. The Codex Python runtime injects a rejected emSign CA into new SSL
  contexts; the test asserting exactly one listed vendor CA therefore counts
  two entries. The TLS policy and assertion were not relaxed to hide this.
- Frontend: 87 passed. Native Edge browser fixtures: 38 passed, 1 skipped.
- Compilation, reproducible HACS archive construction and whitespace checks
  passed for the 0.4.7-beta.2 candidate.
- Actual Core 2026.11 container checks, Hassfest, HACS remote validation and
  GitHub Actions cannot be run locally. Publication is gated on the required
  remote Validate workflow for the exact candidate commit. No user's
  installation or intercom was contacted for these tests.

## Short clips through Home Assistant

WelcomeEye already exposes the standard camera `STREAM` feature and a local
on-demand source. Home Assistant's existing
[`camera.record`](https://www.home-assistant.io/actions/camera.record/) action
can use that path; no extra recording service or go2rtc installation is needed.
The path has been checked in the code, but end-to-end recording in Home Assistant
and on each device remains to validate.

For a later voluntary test, target a single camera and use a writable configured
media directory. `/media` below is only the usual example; use your actual media
path. Keep preload disabled and use `lookback: 0`:

```yaml
action: camera.record
target:
  entity_id: camera.your_welcomeeye
data:
  filename: "/media/welcomeeye_{{ now().strftime('%Y%m%d_%H%M%S_%f') }}.mp4"
  duration: 10
  lookback: 0
```

This is an explicit recording request and acquires media. It is not run by
installation, browsing or this development work. Use an entity target, not the
whole device, to avoid starting both outdoor panels. Do not enable preloading
to obtain lookback: it would hold the intercom stream open.

HA controls the recording duration, which is approximate rather than a strict
file-size limit. Source release occurs when HA/FFmpeg closes its reader and no
other reader remains; it is not guaranteed at the exact requested second.
MP4 recordings are separate from WelcomeEye's JPEG library and its retention
rules. Opening media for a clip retains the known monitor-ringing interruption.
An unattended recording feature would also need a storage quota and lifecycle
validation before being enabled by this integration.
