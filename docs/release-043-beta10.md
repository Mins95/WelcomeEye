# 0.4.3-beta.10 — Connect 3 audio and controls

Prerelease for testing. Stable remains **0.4.2**. Connect 3 moving video was
confirmed by the beta.9 tester. **Audio, microphone and physical openings in
this release are software-tested, not yet hardware-confirmed.**

## Install and test

1. In HACS, select **0.4.3-beta.10**, restart Home Assistant and fully reload
   the WelcomeEye card. Keep the existing entry, local password, certificate
   pins and Experimental live video setting. Close Philips during HA tests.
2. Open video and unmute sound. Then enable the microphone over HTTPS, speak,
   disable it and verify that video continues. Close HA video and confirm
   Philips can reopen it.
3. For openings, enable **Enable experimental Connect 3 strike/gate controls**
   in the integration's reconfiguration form and enter the **Philips opening code**.
   It is separate from the connection password. Test each installed output
   with one deliberate click. Report actual movement, not just HA's message.
   An uncertain command is never automatically retried; inspect the physical
   result and collect diagnostics before any new attempt.
4. For the bell, follow the [bounded observation procedure](connect3-audio-controls.md#doorbell-test-available-to-the-tester).
   It requires **video already open** and an administrator. Start the observation,
   press the bell once, mark the press, then retrieve its result and diagnostics.
   This test observes live-session callbacks; it does **not** provide automatic
   doorbell detection with all players closed.

Return fresh integration diagnostics and a separate result for sound, microphone
on/off, strike, gate, physical bell and Philips reopening. Review files before
public sharing. No raw audio/video, opening code or credential is added to the
diagnostics. No real-device command was sent during development.

## Validation and rollback

Software coverage includes Python 3.12/3.14, frontend, HACS, Hassfest, packaging
and actual Home Assistant 2026.7.3/2026.9.3 services/permissions. Synthetic
WebRTC cycles exercise audio/video, microphone stop/restart, shared consumers
and cleanup; synthetic output tests enforce one send attempt per user action.
The Connect 3 protocol evidence and remaining limits are linked from the
[implementation notes](connect3-audio-controls.md).

Rollback: close the player, redownload **0.4.3-beta.9** in HACS and restart HA.
Do not delete the entry or change its password/certificate pins. Beta.9 retains
the previously confirmed video but does not expose these new controls.
