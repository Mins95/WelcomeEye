# Experimental V1 cloud doorbell

Development branch: `feature/v1-cloud-doorbell`. Not a published release and not
yet validated with a real V1 notification.

This opt-in applies **only to Connect V1 / DES9900VDP doorbell events**. Camera,
sound, microphone and door/gate commands keep the local protocol. Connect 2
R001's local bell and the R002/Connect 3 implementations are unchanged. This
feature does not request video, snapshots, an additional device connection or
physical output. It does not restore a local V1 listener.

## Enable and use

On a V1 entry already identified by the integration, select **Reconfigure** and
enable **Experimental V1 cloud doorbell**. No Philips account/password or phone
token is requested. The local unlock code is not sent to the cloud. Existing
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

Local validation on Python 3.12.14: **675 tests collected, 673 passed, 2 skipped**
(Windows symlink permissions); **24 frontend tests passed**. This includes actual
protobuf framing, synthetic AES-GCM/AES128-GCM notifications, duplicate ACKs,
malformed-message isolation, enrollment HTTP simulations, token rotation,
bounded reconnects, cancellation and own-subscription cleanup. Compilation,
archive construction and `git diff --check` passed.

The workflow includes Python 3.12/3.14 and the new
`tools/verify_v1_cloud_runtime.py` against real HA containers with mocked cloud
transport. Those HA/3.14 CI runs have **not** been executed for this local branch.
No cloud enrollment, vendor registration or physical ring was tested.

1. Keep HA and Philips video players closed. Enable the V1 cloud option.
2. Check the cloud registration/subscription/connection states in diagnostics.
3. Ring once. Confirm both HA's bell event and the normal phone notification.
4. Repeat after a complete return to idle, then after an HA entry reload.
5. Disable the option. Confirm the phone still receives its normal notification
   and local HA video still works. Do not test strike/gate for this feature.

If registration fails, collect diagnostics before retrying. Do not send a phone
token, Google credentials, full private storage, or unlock codes. No live cloud
registration or hardware result is claimed by the offline tests.

Rollback: turn the V1 option OFF, then restore the previous integration files
and restart HA if needed. No release tag, stable build or `main` is changed by
this development branch.
