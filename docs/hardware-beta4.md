# Beta.4 hardware checklist

Status: **pending**. Software tests and previously recorded hardware results
do not complete this checklist. Record the actual tested commit, HA version,
device model, date and outcomes, without UID, IP, credentials or images of people.

The beta is published at the maintainer's request to enable these installation
and hardware checks. Do not send strike or gate commands as automated tests.

## Connect 2

1. Install beta.4, restart HA and fully reload the frontend.
2. Open video; verify fresh moving video.
3. Enable speaker audio and confirm sound from the intercom.
4. Enable the microphone explicitly; confirm sound at the intercom.
5. Close video; capture a manual photo and verify a fresh last-snapshot image.
6. Open video; capture a manual photo while sound and microphone are active.
7. Verify video/audio/microphone continue and no second device session opens.
8. Open the saved file in HA Media; confirm the local date/time in its name.
9. Verify the card Photo button reports the actual save result.
10. Set Capture sur sonnerie OFF; physically ring and confirm the immediate
    ring event / five-second sensor pulse, without a photo or Media file.
11. Set Capture sur sonnerie ON; physically ring and confirm one capture starts
    at T+4, with a new last-ring image and a ring file in Media.
12. Confirm the native monitor photo is still present.
13. Take a manual photo; confirm last-ring is unchanged.
14. Start a ring timer then turn capture OFF before T+4; confirm no capture.
15. Reload the integration and restart HA; confirm the switch preference persists.
16. Test a ring while live video and the microphone are active.
17. Confirm video, audio and microphone work again after captures and close.
18. Close HA live; confirm the Philips application can resume live viewing.
19. Repeat multiple open/close/capture/ring cycles without a power-cycle;
    inspect diagnostics for no residual consumer, viewer, timer or worker.

The prior missing-native-photo-after-HA-video observation remains a known
issue until these physical checks demonstrate otherwise. See
[captures](captures.md) for storage failures and retention behavior.

## Optional V1 regression checks

Verify video, incoming audio, microphone, fresh manual snapshot and Media save.
The automatic ring switch is unavailable and no persistent ring listener should
run. V1 local doorbell is unsupported. The tester confirmed physical V1 gate
operation on 2026-09-24; the beta.4 regression checks above remain pending.
Only a human explicitly authorizing an individual action may test the strike or gate.

## Recovery

Use HACS to select the previous version, restart HA and reload the frontend.
Keep Media files and any backup of the prior integration folder. Beta.3 has no
OFF-by-default switch, so its automatic ring behavior differs from beta.4.
Never roll back by moving an existing Git tag.
