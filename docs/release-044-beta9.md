# 0.4.4-beta.9 — one card and two outdoor sources

1. Install the beta through HACS, restart Home Assistant and reload the dashboard. Keep the existing entries and credentials.
2. In the working V1, Connect 2 R001 or Connect 3 entry, use **Reconfigure → Second outdoor panel** if two sources are installed. The beta.8 secondary camera keeps its registry ID and custom name. An automatically created beta.8 test camera alone is not evidence of a second source, so this option is needed when no successful observation was saved.
3. Use your existing WelcomeEye card. Open **Entry 1**, then switch to **Entry 2**, unmute, and return to **Entry 1**. Source labels can be changed in the card editor. No second camera ID is required in YAML.
4. Confirm which panel appears and whether its sound is audible. Close the player, check that Philips resumes, then download the integration diagnostics. Report any error or sound from the wrong panel.

Channel 1 keeps its existing microphone and enabled outputs. When shown from the secondary view, output labels explicitly identify **Entry 1**; a click closes the secondary stream before sending that single command. Do not use physical controls just to test a source change.

Channel 2 video is hardware-confirmed on the reported A331 installation. Its audio is implemented and tested with synthetic media; audible hardware validation is pending. Its microphone and outputs remain disabled. No 60-second limit remains.

V1 and Connect 2 R001 use their APK-derived secondary selector through the existing media worker. Their second-source video/audio still need a hardware test. R002 remains under investigation. A second indoor monitor is not automatically treated as a second outdoor panel.

Automatic photos remain tied to the primary source. With the second-panel option enabled, an identified secondary ring is not replaced by a photo from the primary camera. If HA's HLS fallback is still releasing its stream, the card asks you to close other players before selecting another source.

Diagnostics distinguish each source, selected transport, observed channels, audio, microphone routing, output targets and cleanup. Do not share passwords, QR contents or private device identifiers.

**Rollback:** reinstall **v0.4.4-beta.8** via HACS and restart, keeping the entry. Channel 1 stays configured; channel 2 reverts to the temporary video-only test. Reload the dashboard to restore its matching card code. Stable **v0.4.3** is unchanged and does not support the newer TCP setup.
