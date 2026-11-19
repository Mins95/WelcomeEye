# Cloud doorbells — Connect 3 and Connect 2 R002

**0.4.6 stable supports cloud doorbells on both families.** Actual delivery is confirmed on R002 and Connect 3 A331. Home Assistant has its own notification registration; the official app and indoor monitors continue to ring. Media and opening commands remain local.

## Setup

1. Update to 0.4.6, restart HA and keep existing entries.
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

**From 0.4.8-beta.1:** temporary connection failures are retried with progressively
longer delays instead of stopping after three attempts. Authentication,
configuration, TLS verification and protocol errors still require intervention.
The diagnostics show the recovery state and next delay. Disabling cloud or
unloading the entry cancels scheduled retries. [Beta checks and rollback](release-048-beta1.md).

To return to the previous stable release, install 0.4.5 through HACS and restart HA; keep entries and credentials. It retains the cloud delivery fixes but lacks the new card options and visible subscription status. See [rollback](release-046.md#rollback).

## Check whether a visitor thumbnail is available

Close the video in Home Assistant, press one outdoor bell and wait for the
doorbell sensor. Download diagnostics for that device. In
`accepted_call_thumbnails`, find the entrance number and compare `present`,
`empty` and `absent` with the counts before the press. Each accepted, fresh
call increments exactly one counter. The same check works independently on
Connect 3 and Connect 2 R002. Diagnostics retain neither the address nor the
contents of a visitor image. For a nonempty field, `last_thumbnail_origin_class`
reports only whether the resource uses HTTPS and whether the hostname has the
vendor's domain suffix. The two available A331 push traces contained a
`RecordSubResUrl` string without its value or origin. The presence of a
nonempty resource on that hardware and on R002 still needs confirmation.

Door Connect's `PushCallNotificationHelper` can display the optional
`RecordSubResUrl` attached to that call. The available app traces identify
no fixed image origin for these two firmwares. A `present` result establishes
that the provider offered a resource. Its availability from HA and its
association with the visitor still require a device test.

The monitor album uses a separate path. `PlaybackModel.getRecordList` calls
`QvDeviceSDK.getRecord` in device mode. The selected API depends on the
protocol and device capability 89. The search includes a channel and time
window. Afterwards `getPictureFile` uses the file returned by that search
with `QvFileCore.startDownload`. The available results do not establish a
unique match between a returned monitor file and a cloud call on these
firmwares. An unrelated earlier image must never be attached to a new visit.

## Privacy and transport

Account credentials, selected device identity and FCM registration stay in HA's private configuration/storage. They are excluded from entity attributes and diagnostics. Only authorized outdoor calls are considered.

HA rechecks its own Android routing token after seven days, following the
Firebase Messaging cache lifetime in the apps. It keeps the same installation,
saves the refreshed credentials and verifies its vendor subscription.
`fcm.routing_token_status` reports whether a token was cached, registered,
reconfirmed or rotated.

Shared Connect 3 and R002 accounts need the alarm permission and a channel grant. A whole-device share can supply the channel count in place of a channel list. A restricted share with a missing list grants no channels. Changes observed on a device-list refresh apply to new notifications and history requests.

The credential-free directory at `sh2.qvcloud.net:443` uses the APK's dedicated public vendor CA, with chain and date verification. Its certificate has no DNS name, so this narrowly scoped policy authenticates the vendor authority, not a DNS-name match. Account and notification services use normal HTTPS certificate and hostname verification. No password is sent to the directory; no global TLS policy is changed.

If the runtime adds another CA to this dedicated context, discovery stops before sending the directory request and reports `directory_trust_store_mismatch`.
