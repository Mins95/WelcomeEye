# WelcomeEye 0.4.5-rc.2 — photo preferences

Install the RC through HACS and restart Home Assistant. Keep existing entries.

Open **Settings → Devices & services → Philips WelcomeEye → Reconfigure → Photo management**:

- **Show photo library in the sidebar**: ON by default. Disable it to hide the WelcomeEye photo panel. For multiple devices, disable it on every loaded entry to hide the shared panel. Saved files, Media browsing, capture and retention are unchanged.
- **Automatic ring photo delay**: 0–120 seconds after HA detects the ring; default **4 seconds**. The same setting applies to both outdoor panels of that device. It only takes effect when automatic ring capture is supported and enabled; it never enables capture by itself.

Both settings are available for V1, Connect 2 R001, R002 and Connect 3, and persist after reload/restart. No credentials or certificate approval need to be entered again for these changes.

The known ringing limitation remains: opening the media stream for a photo can interrupt the indoor monitor call/ringing. A longer delay may postpone this until after the ringing ends, but does not guarantee it, and the visitor may have left. Manual captures are unchanged.

Validation: simulated clocks cover default/zero/long delays, cancellation and shared-session capture; isolated photo libraries cover visibility, multiple entries and unload/reload. The required CI additionally checks real Home Assistant forms/APIs with simulated devices, Python, frontend, HACS, Hassfest and packaging. No physical device command is used in automated tests.

Rollback: select **v0.4.5-rc.1** in HACS and restart Home Assistant. RC1 does not apply these two new preferences: its library is visible and its capture delay is 4 seconds. Saved images and existing device configuration remain. Stable **v0.4.4** is unchanged.
