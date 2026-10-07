# Experimental V1 cloud doorbell

Available in **0.4.3-beta.12**, as an opt-in experiment. Stable remains **0.4.2**.
Actual cloud delivery and coexistence with phone notifications have not been
validated on a real V1. [Release instructions](release-043-beta12.md).

## Working follow-up to beta.12 (not yet released)

The V1 report received on 7 October shows one FCM message delivered to our
callback and acknowledged, then rejected by the freshness filter:
`messages_received=1`, `stale_messages=1`, `rings_received=0`. Registration and
subscription were accepted; the phone still notified the tester. Beta.12 does
not retain the timestamp or the precise rejection reason, so this report cannot
prove the rejected message's age or that its LT event was a ring for this device.

The downlink conversion is corrected: MCS `sent` is passed through as epoch
milliseconds, instead of multiplying it by 1,000. This follows the receiving
path in [microG McsService](https://github.com/microg/GmsCore/blob/8f18fbe0bf3184097e39adc2bc38216b5a9bb76f/play-services-core/src/main/java/org/microg/gms/gcm/McsService.java#L534),
which assigns it directly to `google.sent_time`, and the
[Firebase RemoteMessage](https://github.com/firebase/firebase-android-sdk/blob/3f01b025b5b651d084902c96168fea89bdb0f50d/firebase-messaging/src/main/java/com/google/firebase/messaging/RemoteMessage.java#L171),
which defines that value in epoch milliseconds. A client-originated MCS send
path/comment is not a contract for server-to-client notifications. Reserved
`google.sent_time` metadata is used when present and consistent. Missing,
invalid or conflicting timestamps never fall back to reception time or the
timezone-less LT date. The 120-second age limit and 30-second future tolerance
remain unchanged. Real notification delivery still needs a fresh hardware test.
Previously acknowledged messages are not replayed; validation requires a new
physical ring after installing the follow-up.

Diagnostics now distinguish missing, invalid, future and expired timestamps,
and subsequent malformed, unrelated, duplicate or accepted messages. Only
reason enums, counters, presence flags and MCS timestamp digit count are exposed;
no timestamp, payload, identifier or notification text is retained in diagnostics.

The follow-up also enables the existing **Automatic ring capture** switch and
**Last ring** image for a V1 with cloud notifications enabled. A previously saved
OFF setting is respected; otherwise capture defaults to ON. An accepted cloud
ring fires immediately, then schedules one fresh local snapshot from T+4 seconds
after HA accepts the event. This is not a download of the monitor's native photo.
It reuses the shared media session and existing private Media storage, emits
`welcomeeye_local.ring_image` only after a new image is available, and ignores
superseded captures. Rejected messages cannot trigger a photo. No local V1 bell
listener or physical output command is added. V1 photo timing and coexistence
with the monitor's native photo remain experimental and require hardware testing.

The sections below describe the published beta.12 behavior; its release/tag and
assets remain unchanged.

This opt-in applies **only to Connect V1 / DES9900VDP doorbell events**. Camera,
sound, microphone and door/gate commands keep the local protocol. Connect 2
R001's local bell and the R002/Connect 3 implementations are unchanged. This
feature does not request video, snapshots, an additional device connection or
physical output. It does not restore a local V1 listener.

## Enable and use

Install **0.4.3-beta.12** in HACS and restart Home Assistant. On a V1 entry already
identified by the integration, open **Settings → Devices & services → WelcomeEye
→ Reconfigure** and enable **Enable V1 cloud doorbell notifications (experimental)**.
No Philips account/password or phone token is requested. The local unlock code
is not sent to the cloud. Existing
entries default to OFF; upgrading alone creates no cloud registration.

The implementation creates its own persistent installation identity and FCM
receiver, then associates its token with the configured device through the LT
provider. This sends the device UID and the new installation/token to the
manufacturer's push service. Google/Firebase and the manufacturer's service
must remain reachable. There is no incoming port to open.

When a valid, recent own-device LT event 14 arrives, the existing
`binary_sensor.<device>_ring` pulses and `welcomeeye_local.ring` fires with
`entry_id`, `channel`, `ring_sequence` and `source: cloud`. No photo acquisition
is attached to this V1 event. Automations can use the same event as local rings.
The sensor is unavailable until the vendor association and FCM connection are
ready. This readiness does **not** prove delivery of an actual ring.

## What the APK proves

The analyzed WelcomeEye 6.1.58.24 base APK SHA256 is
`ff205ff0d24527b912d5c227aa80f8a6500979393ec0cefa62e35082173d6b52`.

* `com/quvii/compathlt/alarm/LtAlarmManager.java`: no-account installation
  registration/status and own-installation disabling.
* `com/quvii/compathlt/QvCompatLtManager.java`: independent account derived from
  the push tag and installation ID.
* `com/quvii/compathlt/alarm/encode/LtEncrypt.java` and `LtConfig.java`: HTTPS
  `text/plain` carrying Base64(RC4(JSON)), encrypted-device push2u endpoints.
* `QvFcmPushService.java`: `message_content` contains pipe-separated device UID,
  channel, LT event and local timestamp. LT14 maps to the call event; LT26 is
  not another ring.

No reliable local V1 bell subscription has been demonstrated. Receiving phone
notifications does not imply the device exposes the same event over its local
media/control socket.

The FCM receiver uses the public Firebase app configuration and independent
GCM/FIS/FCM credentials. It uses `firebase-messaging==0.4.5`'s protobuf definitions
and constants, with cryptography/http-ece, bounded transport and sanitized errors.
This is a web-style registration, not an Android SDK instance. Acceptance of
that registration by the WelcomeEye Firebase project and delivery by LT both
still need a live test. No success is inferred from the APK alone.

## Lifecycle and privacy

Tokens and receiver keys are stored in HA private `.storage`, outside config
entry options, entity attributes, events, logs and diagnostics. Treat HA backups
as sensitive: private storage is not a promise of encryption at rest. Ring
deduplication stores bounded hashes; payloads and device names are discarded.
Unknown/old transport timestamps are rejected, using the FCM UTC timestamp,
not a guessed timezone for the device's date. Maximum accepted age is 120 s.

Connection attempts are limited to three per entry load, separated by 5 and 30
seconds after failures. HTTP and setup have deadlines; no physical command is
replayed. Once exhausted, diagnostics report `failed_reload_required`; reload
the entry after connectivity is restored. Local video remains usable.

Unload/disable invalidates callbacks, stops the receiver and makes a bounded
request to disable **only this HA installation**. If the provider is unavailable,
private cleanup intent is retained. An explicitly disabled V1 entry retries
only this pending cleanup on reload, without creating an FCM receiver. This does not
prove remote data deletion. A distinct HA identity is intended to preserve the
phone's subscription, but the provider's multi-client behavior remains unverified.

Diagnostics under `v1_cloud_doorbell` show registration, vendor subscription,
connection attempts, received/ignored/duplicate/stale messages, accepted rings
and sanitized error stages. No tokens, UID, notification text or raw packets are
exported. `delivery_observed` changes only after an accepted notification;
`phone_coexistence_verified` remains false until separately checked.

## First hardware validation, after software tests

Offline coverage exercises protobuf framing, synthetic AES-GCM/AES128-GCM
notifications, duplicate ACKs, malformed-message isolation, registration HTTP
simulations, token rotation, bounded reconnects, cancellation and cleanup of
the HA subscription. The validation workflow also covers Python 3.12/3.14,
frontend tests, packaging and actual HA APIs with mocked cloud transport.
Use the [Validate run](https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml)
for the published revision as the CI result. These checks do not contact the
real provider or establish hardware delivery.

1. Keep HA and Philips video players closed. Enable the V1 cloud option.
2. Check the cloud registration/subscription/connection states in diagnostics.
3. Ring once. Confirm both HA's bell event and the normal phone notification.
4. Repeat after a complete return to idle, then after an HA entry reload.
5. Disable the option. Confirm the phone still receives its normal notification
   and local HA video still works. Do not test strike/gate for this feature.

If registration fails, collect diagnostics before retrying. Do not send a phone
token, Google credentials, full private storage, or unlock codes. No live cloud
registration or hardware result is claimed by the offline tests.

Rollback: turn the V1 option OFF in **Reconfigure** while beta.12 is still
installed, then redownload **0.4.3-beta.11** in HACS and restart HA. Keep the
existing entry and local credentials. If diagnostics show `cleanup_pending`,
restore connectivity and reload the disabled entry before downgrading so it
can retry its own unsubscribe. Beta.11 retains local media and controls but
does not provide this cloud doorbell option.
