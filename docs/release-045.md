# WelcomeEye 0.4.5 stable

Released 1 November 2026, based on **0.4.5-rc.2**. This promotion retains the RC's protocols, stored configuration and entity identities. It refreshes user-facing labels and the documentation without enabling previously disabled functions.

## Included since 0.4.4

- Fixed Connect 3/R002 cloud notification enrollment, Android receiver handling and QV call parsing. Fresh calls produce a five-second HA sensor pulse and per-channel automation event. Real reception is confirmed on R002 and Connect 3.
- R002 audio/credential handling and media continuity fixes; local on-demand video/audio for standard HA camera consumers, including HomeKit Bridge.
- Manual photos for R002 and optional automatic ring photos for Connect 3/R002, with separate per-channel images/storage. QV automatic photos remain OFF by default; V1/R001 defaults and saved preferences are preserved.
- Photo library selection/deletion and empty-folder cleanup; optional shared sidebar visibility. Photos stay accessible in Media when hidden.
- Capture delay configurable from 0 to 120 seconds, default 4; unlimited/default or 7/30/90-day retention.
- Stable labels in French, English and Spanish. Existing internal configuration/service identifiers remain for compatibility.

## Compatibility and limitations

Connect V1, Connect 2 R001/R002 and Connect 3 keep their existing media and opening paths. **Connect 3 is validated**, with the model/firmware profiles in the [compatibility table](../README.md#-supported-devices). The maintainers' stable promotion includes the reported hardware results, not a new physical test run by software CI.

R002 ring/photo feedback: [automatic capture](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-6475802751), [cloud ringing without capture](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-6471926126), and [Docker bridge feedback](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-6529060920). Connect 3 cloud reception was checked in private support diagnostics: accepted calls increased from one to two, with no rejection or callback error; the tester reported normal monitor and Door Connect ringing. No private diagnostic, identity or address is published here.

Automatic photos may interrupt the monitor's ongoing call/ringing when media is acquired. Official app notifications continue. Delaying acquisition can move the interruption but does not guarantee avoiding it. Native monitor-photo retrieval and local standby ringing on Connect 3/R002 remain unavailable. Actual stream quality depends on hardware; there is no SD/HD selector, automatic card playback or video-recording feature in this release.

TCP media is not TLS-equivalent. Certificate exceptions, transport consent and physical-output authorization remain explicit and scoped. One click sends at most one opening command, with no automatic replay. No camera or relay is started simply by updating.

## Upgrade and rollback

1. Update to **0.4.5** in HACS and restart Home Assistant.
2. Reload the browser/Companion frontend. YAML-managed resources use `/welcomeeye_local/welcomeeye-card.js?v=0.4.5`.
3. Keep all existing entries, passwords, pins, permissions and dashboards. New features are configured through **Reconfigure**; no deletion/recreation is needed.
4. For doorbell automations, observe each installed entrance sensor separately. Sound, notification delivery and opening a dashboard are automation choices. Leave automatic photos OFF if preserving the full monitor ring cycle matters.

For rollback, choose **0.4.5-rc.2** in HACS and restart HA; it shares the same runtime behavior and settings. Before reverting to **0.4.4**, disable Connect 3/R002 cloud notifications, as its receiver predates these fixes. Older versions omit newer photo/export settings. Saved Media files remain on disk. Existing releases are retained.

## Validation

Publication requires the **Validate** workflow to succeed for the exact main commit, including Python/PyAV matrices, frontend/browser checks, simulated-network Home Assistant runtimes, HACS, Hassfest and reproducible packaging. The release workflow checks the uploaded archive digest before publication. Automated checks use simulated devices and do not operate physical relays.
