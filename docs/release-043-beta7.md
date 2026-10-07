# 0.4.3-beta.7 — experimental Connect 3 live video

> Historical record: see the [current 0.4.3 guide](../README.md) for support, setup and known limitations.

Base: `v0.4.3-beta.6`, `9ef0c439bd2c53578a5a2ebda7702ab6d470b90a`.
Branch: `feature/043-connect3-video-beta7`. Stable remains **0.4.2**.

## What is available

Connect 3 now has an explicitly enabled camera for a first live-video test in
the existing WelcomeEye card. The implementation obtains a temporary stream
key through the already validated CGI path, verifies TLS on the media endpoint,
negotiates QV setup/play, decodes H264 and sends decoded frames through WebRTC.
No proprietary library is installed in HA. See the [APK/native evidence](connect3-beta7-video-evidence.md).

The existing entry, identity, password and CGI pin are retained. The new setting
defaults OFF. Setup, reload, diagnostics, camera thumbnails and browsing saved
photos never open a media session. Explicit simultaneous viewers share one
worker; closing the last viewer sends teardown and closes TCP. No protocol
fallback, background stream or automatic reconnection is added.

The camera advertises video only. Connect 3 audio, microphone, strike, gate,
snapshot and ring functions remain unavailable in both capabilities and APIs.
The two output paths found in the APK still lack a complete proven button/output
mapping and are not exposed. Do not test physical controls in this sequence.

Beta.6's Media source, existing photos and authenticated paths are unchanged.
Legacy V1/Connect 2, R002 and the exact aiortc/native CRC32C dependency are retained.

## Evidence and limits

- **Hardware:** the tester confirmed beta.4 discovery and beta.5 pinned CGI
  authentication/key retrieval on IDS94E6SW, using its local 6–16 digit device
  password. The sanitized JSON records HTTP 200, one request and 229 ms.
- **Reconstructed and implemented:** setup A9, live play 01, channel 1/stream 1,
  negotiated AES/SHA framing, bounded media assembly and H264 decoding,
  keepalive and teardown. Evidence includes Java, JNI and exact ARM64 addresses.
- **Software:** synthetic independent wire fixtures, actual separate loopback
  HTTPS/TLS servers, H264 decoding, three reopen/share/release cycles, malformed
  packets, wrong pins, refusal, EOF, timeouts, repeated cancellation, privacy,
  permissions and regressions. Actual HA runtime tools additionally exercise
  the camera and WebRTC with synthetic frames and mocked device I/O.
- **Still pending on hardware:** media TLS on 8443, actual setup modes and play
  acceptance, moving video and release/reopen behavior. The APK can load a TLS
  client certificate; whether this device requires one is unknown. No APK
  private key is copied or used. An explicit failure is preferable to a guessed
  protocol or weakened certificate validation.

The integration accepts the documented H264 codec identifiers; another codec,
unknown mode, or non-aligned encrypted region produces a bounded failure.
Initial QV setup is limited to 20 seconds, media acquisition to 35 seconds plus
cleanup, inactivity to 20 seconds, each write to 2 seconds and TCP close to 1
second. Keepalive is 10 seconds as in the app. The shorter inactivity deadline
is an integration policy; the native app uses 60 seconds. One control body is
bounded by its 16-bit length; one media packet by 1 MiB and assembled frame by
4 MiB. There is one reader and no physical command in this media path.

## First hardware test

1. In HACS, download **0.4.3-beta.7**, restart HA and reload the frontend.
   Keep the existing Connect 3 entry; do not delete or recreate it.
2. Close the Philips Door Connect app and other players. In the Connect 3
   integration menu choose **Reconfigure**, enable **Experimental live video**
   and keep the saved password/pin. The announced TLS media port defaults to
   **8443**. Saving this setting does not open video.
3. Add/select the new Connect 3 camera in the WelcomeEye card:

   ```yaml
   type: custom:welcomeeye-card
   entity: camera.YOUR_CONNECT3_CAMERA
   ```

4. Open the direct for about **15 seconds**. Confirm that the picture moves,
   then close it using the card's **X**. Open it a second time for 15 seconds
   and close it again. No microphone, gate or strike test.
5. Check that the Philips app can then open its own video. Download integration
   diagnostics and report whether each opening moved, its approximate delay,
   and whether the official app resumed. If the first attempt fails, return
   its diagnostics rather than trying other ports, channels or credentials.

The targeted question is: **does the independently verified media endpoint
accept the APK's setup-first LAN path and return decodable H264 on wire 1/1?**
The only application messages are setup, play, bounded keepalives and teardown.
No scan, fuzzing or protocol variation is part of the test.

Useful safe fields are under `connect3.media`: stage/failed_at_stage,
streamkey_received, media_tls_verified, setup_result/setup_accepted,
encryption_mode/sha_mode, play_result/play_accepted, media_packets,
decoded_frames, first_frame_elapsed_ms, decode_errors, teardown_sent,
tcp_closed and error type/reason. The access result remains separate from
media failure. `media_available` means implemented and enabled;
`media_received` means at least one decoded frame; `hardware_validated` remains
false until a hardware report has actually been reviewed.

## If the media certificate differs

The CGI certificate is not assumed to match 8443. Its saved pin is reused only
after comparison with the actual media certificate. A mismatch stops before
the media login. There is no cleartext downgrade.

The optional action below inspects the configured media endpoint without
sending a QV login or password. It requires an identified administrator with
control permission on the status sensor:

```yaml
action: welcomeeye_local.connect3_check_media_certificate
target:
  entity_id: sensor.YOUR_CONNECT3_STATUS
data:
  include_details: true
```

Compare its SHA256 fingerprint independently with the device's media endpoint,
as was done for CGI. Only then enter it in **Media TLS certificate SHA256** in
Reconfigure. Inspection itself does not establish trust or save the pin.
Do not publish credentials, certificate material or private endpoints.

## Separate optional diagnostics

[Section 7 bis tools](experimental-diagnostics-beta7.md) are independent:
private query 469/reply 470 and optional camera channel18/selector0x12 apply
only to the legacy family, with admin/control permission, explicit confirmation,
idle-device checks and no retry. UDT analysis is offline because no actionable
endpoint/exchange is proven. No target was designated for these new diagnostics;
**no real-device diagnostic or physical output was run during development**.

## Rollback

Close the card with X. In HACS choose **Redownload → 0.4.3-beta.6**, restart HA
and reload the frontend. Keep the config entry and saved photos. Beta.6 retains
the CGI/diagnostic and Media features but does not expose Connect 3 live video.
No monitor reset or firmware change is involved.
