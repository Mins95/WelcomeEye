# Connect 2 R002 — setup

**Video, sound, microphone, strike/gate commands and cloud doorbells are validated on R002.** Manual photos and optional automatic ring photos are available in 0.4.5. The public hardware reports are in [issue #7](https://github.com/Mins95/WelcomeEye/issues/7). Docker bridge networking was also reported working; host networking is not a universal requirement.

## Connection

Update to **0.4.5 stable**, restart HA and keep the existing entry. Use **Reconfigure** to change settings without losing entity IDs, passwords or certificate approvals.

For a new R002 entry, select Connect 2 R002 and enter its local IP and connection password. Manual IP setup works across routed VLANs without mandatory broadcast discovery. HTTPS CGI normally uses 443; media uses QV TCP 34567. The certificate is inspected automatically and approved in HA. TCP media has no TLS; CGI remains verified HTTPS, and limited TCP protections require explicit consent.

Choose each installed output in **Opening commands**, with its separate opening code. Saving never operates an output. One click makes at most one physical attempt; never automatically repeat a command with an ambiguous result. Enable a second outdoor panel only when installed.

Older entries retain their existing setup route and pins. Blank secret fields keep saved values. Do not delete/recreate an entry to change its IP or credentials.

## Doorbells and photos

Configure **Cloud doorbell notifications** using the WelcomeEye app account and matching account device. This is separate from local credentials. [Cloud setup](qv-cloud-doorbell.md).

Open the desired live stream before using **Photo**. Saved images appear in **Media → WelcomeEye** and the optional photo sidebar. **Ring image capture** is OFF by default; enable it deliberately for automatic visitor photos. It can interrupt the monitor's call/ringing when it acquires media. The delay is adjustable from 0 to 120 seconds, default 4. [Photos and retention](captures.md).

Use the [WelcomeEye card](card.md) for audio, microphone, controls and source selection. Close it before opening the Philips player. Actual image resolution depends on the device's stream; the integration does not offer an SD/HD selector or manufacture HD detail.

## Diagnostics

Certificate/access actions inspect only the configured device. QV discovery and TCP 8765 probes remain optional support actions, not installation requirements. The local live-session observation does not provide standby bell detection; normal R002 ringing uses the cloud route. No general network scan, background video or physical-command replay is introduced.

[Installation](installation.md) · [Release notes and rollback](release-045.md) · [Protocol evidence](r002-apk-analysis.md)
