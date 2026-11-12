# WelcomeEye 0.4.8 — stable

Stable promotion requested by the maintainer on 12 November 2026. Existing
entries, entity IDs, credentials, certificate approvals, opening permissions,
saved photos and dashboard cards are retained. No entry recreation is needed.

## Changes

- **Cloud recovery:** Connect 3/R002 retry temporary connection failures with
  progressively spaced attempts. Disabling cloud or unloading the integration
  cancels recovery. Authentication, TLS, configuration and invalid-protocol
  failures remain explicit errors requiring correction. Reconnection does not
  generate a ring or replay an opening command.
- **Protected V1/R001 connections:** the last successfully authenticated endpoint
  is retained privately across HA restarts. It is a connection hint, not a trust
  approval: configuration matching and the existing login/protection checks still
  apply. Invalid or failed hints fall back to bounded discovery. Older unprotected
  V1 connections keep their explicit approval and fresh-discovery requirements.
- **Discovery diagnostics:** distinguish requests that could not be sent, missing
  replies and unrelated or rejected datagrams, without exporting private endpoints,
  device identifiers or raw network messages.
- **Fresh photos:** wait **three seconds after the first decoded video frame** of
  each new session, then use a newly received JPEG. This covers supported V1,
  R001, R002 and Connect 3 sources, including secondary channels. An established
  stream keeps its normal photo cadence. Live video and sound remain immediate;
  enabling sound does not enable the microphone.

The republished beta.2 tested a four-second startup wait. The maintainer reported
no improvement and requested a return to three seconds for this stable release.
The wait is separate from the configured delay after a doorbell press. It does
not reject legitimate monochrome night images or guarantee lighting readiness
on every firmware.

## Install or update

1. Select **v0.4.8** in HACS/Marketplace and restart Home Assistant.
2. Reload the dashboard or Companion frontend and reopen any card popup.
3. Keep existing entries, cameras and cards. Existing settings remain in effect.
4. If resources are managed in YAML, update the existing JavaScript module to
   `/welcomeeye_local/welcomeeye-card.js?v=0.4.8`; do not add a duplicate.

[Installation guide](installation.md) · [Settings](settings.md) · [Card](card.md)
· [Photos](captures.md)

The optional saved-photo notification blueprint is unchanged and imported
separately. Existing automations remain valid. Follow the
[blueprint instructions](release-047.md#import-the-photo-notification-blueprint)
if it is not already installed.

## Validation and limits

Publication requires successful validation for the exact current-main commit:
Python/PyAV tests, frontend/browser checks, real HA runtimes with simulated
networking, HACS, Hassfest and reproducible packaging. The release workflow
downloads the uploaded archive and verifies it before publishing. Automated tests
use synthetic media and local peers, never a user's intercom or physical relays.
The release's [GitHub Actions runs](https://github.com/Mins95/WelcomeEye/actions)
provide the software validation results.

Existing hardware confirmations remain applicable. Stable promotion is not a
new hardware attestation for every firmware, recovery condition or night-light
transition.

- Acquiring video for a photo or live view can still interrupt monitor ringing.
  The startup wait does not fix this known limitation.
- Primary V1/R001 HomeKit previews can acquire video. QV and secondary previews
  remain passive and do not open a new session for a thumbnail. No permanent
  background stream is added.
- Connect 3/R002 doorbells retain their supported cloud route. Native monitor-photo
  retrieval, autonomous local bell detection and badge-passage identification are
  not added.
- Physical output routing, explicit permissions and one-attempt/no-replay
  protections remain unchanged.

## Rollback

Select the previous stable **v0.4.7** in HACS/Marketplace, restart HA and reload
the frontend. Keep the existing entries, credentials and saved photos. If you
manage resources in YAML, use the matching `?v=0.4.7` resource URL.

Version 0.4.7 does not include the new cloud recovery or photo stabilization
wait and ignores the private endpoint hint. No storage migration is required.
Earlier releases and their published archives remain unchanged. Downgrading
does not restore photos previously deleted manually or by retention.
