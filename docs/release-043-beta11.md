# 0.4.3-beta.11 — R002 QV candidate

Prepared for testing on **Connect 2 R002 / IDS9417AW**. Stable remains 0.4.2.
The real QV discovery response has been decoded offline. Authentication,
video, sound, microphone and openings are **not yet hardware-validated on R002**.
Connect 3 beta.10 has tester-confirmed video, downstream sound and both outputs;
that result does not validate another firmware.

## What changes

The existing R002 entry can now enable experimental QV media and controls,
without deleting it or pretending it is Connect 3. Each new media acquisition
first repeats the APK's bounded UDP discovery. This candidate accepts only the
observed IDS9417AW / CGI443 / stream0 / TLS-media0 / one-channel shape. Other
shapes fail with an explicit diagnostic before credentials are sent.

After a trusted or explicitly pinned HTTPS `get.device.streamkey` response,
media uses the APK's no-TLS path on TCP34567. This is an explicit transport
selection, **not a retry after a failed TLS connection**. CGI remains HTTPS.
QV negotiates its own packet encryption; TCP transport itself is not TLS.
No port scan, cloud request, speculative CGI or automatic UDT attempt is added.
The existing 8765 diagnostic framing is unchanged.

Video and downstream audio share one reader/session. Microphone uses the
separate audio socket implemented by the APK and inherits that session's
selected endpoint/material. Outputs are opt-in, use a separate opening code,
and make at most one physical write attempt per explicit button action.
There is no output retry or replay after failure.

## Configure the existing entry

Close Philips and HA video. Install the candidate package and restart HA.
Keep the existing R002 entry. All new media/output options default OFF.

1. In Developer Tools → Actions, as an administrator, inspect the CGI
   certificate (one discovery then TLS inspection; no credential is sent):

   ```yaml
   action: welcomeeye_local.r002_check_qv_certificate
   target:
     entity_id: sensor.YOUR_R002_PROTOCOL_STATUS
   data:
     include_details: true
   ```

   Review the returned SHA256 locally. Compare it with a fingerprint obtained
   through your trusted local connection before choosing to trust it. This
   inspection does not automatically save or authenticate the certificate.

2. Reconfigure the existing integration entry. Enter the **local connection
   password** used by Philips and the certificate SHA256 you chose to trust.
   Do not use a cloud-account password or publish either value. Existing
   saved secrets remain when the corresponding fields are left blank.

3. With players closed, run **once**:

   ```yaml
   action: welcomeeye_local.r002_check_access
   target:
     entity_id: sensor.YOUR_R002_PROTOCOL_STATUS
   data: {}
   ```

   Expected success: `status: ok`, `streamkey_received: true`,
   `device_authenticated: true`. The key is never returned. On failure, stop
   here and download diagnostics; do not guess passwords or repeat attempts.

4. Enable experimental R002 live video in reconfiguration. Add the WelcomeEye
   card using the actual camera entity from this entry:

   ```yaml
   type: custom:welcomeeye-card
   entity: camera.YOUR_R002_CAMERA
   ```

   Microphone and opening controls are in this card, not HA's generic camera
   dialog. The resource is `/welcomeeye_local/welcomeeye-card.js` (JavaScript
   module); it is normally registered automatically. Check dashboard Resources
   if a stale frontend reports an unknown card. Microphone requires HTTPS.

## Tests, in order

- Open video once and unmute sound. If it fails, collect diagnostics before
  another attempt. If it works, close/reopen three times and check Philips
  can resume after closing HA.
- From the WelcomeEye card, enable microphone, speak, disable it, and verify
  that transmission stops while video continues. Report physical audibility.
- Only after video works, enable experimental outputs and enter the separate
  Philips opening code. Test only installed outputs, one deliberate click per
  output. Report physical movement separately from the protocol result. Never
  repeat an uncertain action without checking on site.
- For a doorbell experiment, keep video visible in one window and use another
  visible window for the following action. It observes the existing reader;
  it opens no additional video and emits no inferred ring event:

  ```yaml
  action: welcomeeye_local.r002_observe_doorbell
  target:
    entity_id: sensor.YOUR_R002_PROTOCOL_STATUS
  data:
    operation: start
    duration: 90
  ```

  Press the physical bell once, then run the action with `operation: mark`.
  Let the monitor finish; retrieve `operation: stop` (or `status` after the
  deadline). Send that **final response**, plus whether the monitor rang and
  created its native photo. Closing video ends observation. This is not yet
  a standby doorbell listener, and video itself may affect normal call behavior.

Download fresh integration diagnostics after testing and report video, sound,
microphone, strike, gate, bell and Philips reopening separately. Diagnostics
contain stages, counters, format facts and fixed errors, not credentials,
UIDs, raw media or opening codes. Review all files before public sharing.
Explicit action responses may appear in HA script traces; raw discovery
responses must not be posted publicly.

## Rollback

Close the HA player, disable experimental outputs/video, reinstall
**0.4.3-beta.10**, and restart HA. Keep the entry. R002 returns to its
diagnostic-only behavior. Main, stable 0.4.2 and their assets are unchanged.

## Software validation and APK audit

The implementation commit `e6a2436fa1a554fb994d8564efc1f1658dfaa747` passed
[Validate run 37575019998](https://github.com/Mins95/WelcomeEye/actions/runs/37575019998):
Python 3.12/3.14, 562 offline tests (two conditional skips), 24 frontend tests,
Hassfest, HACS and reproducible packaging. Actual Home Assistant containers
2026.7.3 and 2026.9.3 passed service permissions, reconfiguration/identity,
synthetic QV discovery/CGI boundaries and three bidirectional WebRTC cycles.
Those cycles cover downstream PCMA/AAC, microphone stop/restart, one shared
video session, single-shot synthetic output actions and complete cleanup.
No real intercom was contacted; these are software results, not R002 hardware
validation.

[R002 APK evidence](r002-beta11-apk-evidence.md) explains the discovery-derived
transport selection and why QV KCP negotiation is distinct from legacy SCT/UDT.
[Renewed V1 doorbell audit](v1-doorbell-reinvestigation-beta11.md) traces the
local alarm parser, stream-7 long-connection option and five-second native
keepalive. No new V1 alarm subscription is proven, and V1 behavior is unchanged.
