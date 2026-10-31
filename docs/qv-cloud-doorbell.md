# Cloud doorbells — Connect 3 and Connect 2 R002

**0.4.5 stable supports cloud doorbells on both families.** Actual delivery is confirmed on R002 and Connect 3 A331. Home Assistant has its own notification registration; the official app and indoor monitors continue to ring. Media and opening commands remain local.

## Setup

1. Update to 0.4.5, restart HA and keep existing entries.
2. Open **Reconfigure → Cloud doorbell notifications → Configure**.
3. Enter the official app account: **Philips Door Connect for Connect 3**, **WelcomeEye for R002**. This is neither the local connection password nor the opening code.
4. Select the account device matching this HA entry and confirm. A local QR/password alone does not provide access to the cloud account.
5. Enable **Second outdoor panel** if installed. Keep automatic photos OFF while checking normal ringing.

HA does not replace the device's association or notification token in the Philips app.

## What happens when someone rings

- The corresponding **Doorbell** binary sensor becomes active for five seconds.
- `welcomeeye_local.ring` is emitted with `source: cloud_qv`, `channel: 1` or `2`, the entry ID and a ring sequence.
- No audible alert, notification, dashboard opening or camera selection is automatic. Use the sensor/event in an automation for those actions.
- **Ring image capture** is OFF by default on Connect 3/R002. If explicitly enabled, it captures after the configured delay and saves in **Media → WelcomeEye**. Opening media for that photo can interrupt the monitor's ringing. [Photo settings and events](captures.md).

Check the two entrance sensors separately when creating automations: press one bell, let its cycle finish, then press the other. Download diagnostics if a sensor does not react. `rings_received`, `events.accepted` and `last_message_disposition: ring_emitted` confirm reception and event dispatch; they are aggregate counters, not a per-entrance test record. Stale messages, duplicates and notifications for other devices are ignored.

The native local alarm action is a separate advanced diagnostic. It has not established a usable standby bell event and is unnecessary for cloud delivery. Generic heartbeat messages never become HA rings.

## Disable and rollback

Choose **Reconfigure → Cloud doorbell notifications → Disable** to remove HA's own subscriptions. If the provider is unavailable, cleanup remains pending; reload the entry to retry. Local features remain available without cloud access. After three connection failures, reload the entry instead of expecting indefinite retries.

To return to 0.4.5-rc.2, install that version through HACS and restart HA; keep entries and credentials. Before reverting to 0.4.4, disable cloud notifications: its older receiver predates the delivery fixes. See [rollback](release-045.md).

## Privacy and transport

Account credentials, selected device identity and FCM registration stay in HA's private configuration/storage. They are excluded from entity attributes and diagnostics. Only authorized outdoor calls are considered.

The credential-free directory at `sh2.qvcloud.net:443` uses the APK's dedicated public vendor CA, with chain and date verification. Its certificate has no DNS name, so this narrowly scoped policy authenticates the vendor authority, not a DNS-name match. Account and notification services use normal HTTPS certificate and hostname verification. No password is sent to the directory; no global TLS policy is changed.
