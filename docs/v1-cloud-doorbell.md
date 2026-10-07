# Experimental V1 cloud doorbell and ring photos

**0.4.3-beta.13** corrects notification timestamps and adds automatic V1 ring
photos. Stable remains **0.4.2**. Cloud notifications are optional and OFF by
default. **Accepted V1 cloud ring delivery, automatic photos and coexistence
with the phone and monitor's native photo remain unvalidated on hardware.**
[Release instructions](release-043-beta13.md).

## Observed result and timestamp correction

The beta.12 report received on 7 October shows one FCM message delivered and
acknowledged, then rejected: `messages_received=1`, `stale_messages=1`,
`rings_received=0`. Registration and subscription were accepted, and the phone
still notified the tester. The report cannot establish the rejected message's
age or whether its LT event was a ring for this device.

Beta.13 passes downstream MCS `sent` through as epoch milliseconds instead of
multiplying by 1,000. This follows [microG's receiving path](https://github.com/microg/GmsCore/blob/8f18fbe0bf3184097e39adc2bc38216b5a9bb76f/play-services-core/src/main/java/org/microg/gms/gcm/McsService.java#L534)
and [Firebase RemoteMessage's timestamp definition](https://github.com/firebase/firebase-android-sdk/blob/3f01b025b5b651d084902c96168fea89bdb0f50d/firebase-messaging/src/main/java/com/google/firebase/messaging/RemoteMessage.java#L171).
Reserved `google.sent_time` is used when present and consistent. Missing,
invalid or conflicting times never fall back to reception time or the
unqualified local LT date. The age limit remains 120 seconds, with 30 seconds
of future tolerance. Previously acknowledged messages are not replayed.

## Enable notifications and photos

Install **0.4.3-beta.13** in HACS, restart HA and keep the existing entry. For an
identified Connect V1 / DES9900VDP, select **Reconfigure → Enable V1 cloud doorbell
notifications (experimental)**. Keep it ON if already enabled. No Philips
account/password or phone token is requested; the local unlock code is not sent.
Upgrading an entry with cloud OFF does not register it.

HA creates a separate installation and FCM receiver, then associates its token
and the device UID through the manufacturer's LT push service. That service
and Google/Firebase require Internet access; no incoming port is needed.
Video, sound, microphone and door/gate controls keep their local protocols.
No local V1 bell listener or physical output command is added.

A valid, recent own-device LT14 notification triggers the five-second ring
sensor and immediate `welcomeeye_local.ring` event with `source: cloud`.
LT26 and unrelated, stale, malformed or duplicate messages cannot trigger a ring
or photo. The sensor remains unavailable until the subscription and receiver
are ready; readiness alone does not prove delivery.

On a cloud-enabled V1, **Ring image capture / Capture sur sonnerie** and **Last
ring** are available. Capture defaults ON unless a saved OFF preference exists.
An accepted ring schedules one fresh local snapshot from **T+4 seconds after HA
accepts the event**, reusing the shared media session or temporarily acquiring
and releasing it. A newer ring supersedes an unfinished older capture. OFF
prevents photo work and preserves the ring event.

A successful capture updates `image.<device>_last_ring`, attempts an authenticated
save in **Media → WelcomeEye**, and emits `welcomeeye_local.ring_image`. A save
failure leaves the fresh in-memory image available; an acquisition failure
keeps the previous image. This is a new HA photo, not a download of the monitor's
native photo. [Capture behavior and storage](captures.md).

## Lifecycle and privacy

Receiver credentials stay in HA private `.storage`, outside entity attributes,
events, logs and diagnostics. Private storage is not encryption at rest; treat
backups as sensitive. Deduplication retains bounded hashes, not notification text.
The APK evidence is the accountless registration in `LtAlarmManager`, installation
identity in `QvCompatLtManager`, encoding in `LtEncrypt`, and LT14 parsing in
`QvFcmPushService`. No Philips user account is required by this route.

Connection attempts are limited to three per entry load, with bounded setup and
HTTP calls. After `failed_reload_required`, restore connectivity and reload the
entry. Disable/unload stops callbacks and attempts to disable only this HA
subscription. Failed cleanup retains private intent; an explicitly disabled
entry retries it on reload without creating an FCM receiver. This does not prove
remote data deletion or guarantee the provider's multi-client behavior.

Diagnostics distinguish missing, invalid, future and expired timestamps, then
malformed, unrelated, duplicate and accepted messages. They expose counters,
fixed reasons, presence flags and timestamp digit count, without raw timestamps,
UIDs, tokens or payloads. Capture diagnostics distinguish acquisition and Media
save outcomes. `delivery_observed` requires an accepted ring.

## Validation and tester procedure

Local offline validation: **720 Python tests collected, 718 passed, 2 skipped**;
**24 frontend tests passed**. Coverage includes timestamp units/rejections,
FCM/protocol framing, cleanup, shared V1 capture and authenticated Media access.
These results do not establish hardware delivery. Consult the release revision's
[Validate run](https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml)
for CI results; no new CI result is inferred here.

Keep cloud and the capture switch ON, close HA/Philips players, wait for receiver
readiness and ring once. Check HA's ring, the phone notification, the monitor's
native photo, HA's Last ring image and saved Media file, then download fresh
diagnostics. Do not test outputs for this change. See the [short release procedure](release-043-beta13.md#tester-procedure).

Rollback to **0.4.3-beta.12** keeps the same entry and may keep cloud ON; optionally
turn the capture switch OFF first. Beta.12 retains the timestamp bug and has no
V1 automatic ring capture. Restart HA after redownloading it in HACS.
