# Connect 3 — 0.4.3

Live video, downstream sound, physical strike and gate have been validated on
IDS94E6SW / `V401.R001.A350.00.G0123.B025`. The microphone is implemented but
physical confirmation is still pending in the [available tester report](https://github.com/Mins95/WelcomeEye/issues/1#issuecomment-6013167372).
Standby doorbell detection and photos are not available.

## Setup and card

For **0.4.4-beta.9**, use the [automatic connection setup](connect3-auto-tls.md):
IP, local password and any required certificate/transport approvals.
A331 TCP video, sound, microphone and outputs have now been hardware-confirmed
on channel 1. Channel 2 video was confirmed with beta.8; beta.9 adds its audio
for validation in the same card. Its microphone and outputs remain unavailable.
The manual certificate steps below describe stable **0.4.3** only.

1. Install **0.4.3** and restart HA. Keep an existing Connect 3 entry/password/pins.
   For a new entry, select Connect 3 and use the device's local connection password,
   not the Philips account password. It is shown/configured on the indoor monitor.
2. HTTPS uses normal certificate trust or an explicitly verified SHA256 pin.
   The administrator actions `connect3_check_certificate` and
   `connect3_check_media_certificate` inspect CGI/media certificates without
   credentials. `include_details: true` returns a fingerprint only in the action
   response. Verify it independently before saving it in Reconfigure.
3. Enable **Experimental live video**. For opening buttons, separately enable
   **Experimental openings** and enter the Philips **Opening code**.
4. Add this custom card with your actual camera entity:

   ```yaml
   type: custom:welcomeeye-card
   entity: camera.YOUR_CONNECT3_CAMERA
   ```

Microphone and opening buttons belong to this card, not the standard HA camera
dialog. The dashboard resource is normally registered automatically; the manual
JavaScript-module fallback is `/welcomeeye_local/welcomeeye-card.js?v=0.4.3`.
Fully reload the frontend. Microphone requires trusted HTTPS and permission.
Close the viewer with its cross before reopening Philips.

## Established hardware baseline

The owner-supplied beta.9 report records three successful live-video openings
on IDS94E6SW / firmware `V401.R001.A350.00.G0123.B025`, with the Philips app
working afterwards. The attached diagnostic describes the last session:
307 decoded frames, zero decode errors, 601 accepted media packets, first
frame in 1648 ms, teardown sent, TCP closed and no remaining consumer/worker.
Its offset 0 inside a 32-byte extension confirms the layout corrected in beta.9.
The later beta.10 report additionally confirms audible downstream A-law audio
at 8000 Hz mono and physical strike/gate actuation. It did not test the microphone
and did not establish doorbell detection. Protocol counts alone are not physical
validation.

## Audio received from the intercom

The existing QV reader supplies both video and audio. No second live session
or extra network request is created. The decoder uses the actual frame's codec,
sample rate and channel count, and supports G.711 A-law/mu-law, signed 16-bit
little-endian PCM and direct AAC. AAC is not given an invented header/config.
Unsupported codecs or malformed audio are recorded without terminating video.
See [native audio evidence](connect3-audio-evidence.md).

The WelcomeEye card's sound button becomes available with experimental live
video. It remains necessary to unmute the player. Decoded/queued frame counters
prove processing, not that sound was physically heard by the tester.

## Diagnostics for the next test

- Audio: received packets/bytes, codec/rate/channels, decoded frames/samples,
  unsupported formats, decode errors and fixed failure reasons.
- Media: frame-type/codec counts and up to 32 format/length samples, already
  received control-command counts, first audio/video timings, cleanup status.
- Previous sessions: up to three completed observations, so reopening does not
  erase the immediately preceding failure.
- WebRTC: requested/created tracks, frames queued for audio/video, browser audio
  received, microphone start/stop requests and error type.
- Microphone and openings: separate operation/acknowledgement/cleanup diagnostics;
  a protocol acknowledgement never asserts physical audibility or actuation.

Only format facts, counters, relative durations and fixed reasons are retained.
No audio samples, video payloads, raw commands, opening codes, credentials,
device timestamps, addresses or SDP are added to these diagnostics.

## Doorbell test available to the tester

`welcomeeye_local.connect3_observe_doorbell` observes the **existing live video
session** for 90 seconds (configurable 30–120). It opens no connection, acquires
no media consumer and sends no command. Start is refused if video is closed.
Closing video ends observation; there is no hidden listener keeping it open.

1. Close Philips, open Connect 3 video in HA and keep it open.
2. In Developer Tools > Actions, select the real Connect 3 diagnostic sensor:

   ```yaml
   action: welcomeeye_local.connect3_observe_doorbell
   target:
     entity_id: sensor.YOUR_CONNECT3_STATUS
   data:
     operation: start
     duration: 90
   ```

3. Confirm the response says `observation.status: observing`, then press the
   physical bell once. Run the same action with `operation: mark` immediately
   afterwards. This is a tester marker, **not a detected ring**.
4. Let the monitor finish its cycle. Run `operation: stop` (or `status` after
   the automatic deadline), copy its response and download integration diagnostics.
   Report whether the monitor and Philips notification actually rang.
5. Close HA video and confirm Philips can reopen video.

The service requires an identified administrator with control permission.
It retains only up to 128 control events, command/order counts, parameter lengths
and monotonic times relative to the observation start, plus at most five markers.
No raw payload, device identifiers, event text or network timestamp is retained.
The report remains in `doorbell.observation` until another explicit test starts.

APK evidence: `QvPlayerCore.u`, classes2 DEX code item `0x28b2e0`, switch order
23 calls `onOtherDoorBellCall(byte0, UTF8 tail)` at `0x28b3cc`. Its default
implementation is empty, so a received FE/order23 is a **candidate**, not proof
of this physical doorbell. Order1 calls `ReceiveHangUpData`, classes4 `0x21bdf0`.
Only the order and minimum parameter length are observed; both parameter values
are deliberately discarded. No inferred `welcomeeye_local.ring` is emitted.

This experiment investigates notifications **during live video**, which may
affect the monitor's normal call behavior. It does not test notifications with
all players closed and cannot prove their absence. A reader-closed subscription
still requires a separately established SDK login/alarm transport. No speculative
subscription, port scan, or cloud notification integration is introduced.

## Microphone test and deliberate opening controls

Keep the working entry/password/certificate pins. First test moving video and
unmute sound, then download diagnostics if sound fails. Test the microphone
from the WelcomeEye card over HTTPS: enable, speak, disable, verify video
continues, close the player and check Philips can open it afterwards.

Opening tests require the separate experimental-output option and the owner's
explicit opening code. They must be deliberate manual button presses. A send
with an uncertain result is never automatically retried. Download diagnostics
before any new attempt. No physical output was exercised during development.

In integration configuration, enable **Experimental openings** and supply the
**Opening code** used by Philips (it is not automatically copied from authCode).
The existing Gâche/Portail buttons then become available in HA and the WelcomeEye
card. Test only installed outputs, one deliberate click at a time, and report
the actual movement separately from HA's acknowledgement. Do not repeat an
uncertain command until the physical result and fresh diagnostics are checked.

For an equivalent runtime rollback, reinstall beta.13 and restart HA. To disable
the experimental controls, turn the video/output options OFF. Keep credentials
and certificate pins; do not delete the entry to repeat a test.
