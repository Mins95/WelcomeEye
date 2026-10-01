# 0.4.3-beta.4 — Connect 3 read-only experimentation

New prerelease from `feature/043-connect3-experimental`, based on beta.3
`cf3e8cedb79ac72b5bad3b04bfbdaf5a9f3acc9c`. **This is not functional intercom
support for Connect 3 yet.** Main, stable 0.4.2, earlier releases and the CRC32C
dependency are preserved. No installation or real-device request was performed
while developing this release.

## What can be tested

| Function | Implemented / software-tested | Hardware validation | Limit |
| --- | --- | --- | --- |
| Separate manual Connect 3 entry | Yes | Pending | User-declared model, random provisional identity |
| QV UDP discovery + KDF + decryption | Yes; seven vectors independently compared to ARM64 math | Pending | Supported V4/V4.1 520-byte record in 528-byte CBC container; no authentication |
| HTTPS streamkey access check | Yes, synthetic replies | Pending | Requires owner's existing local authCode and valid TLS trust/pin; no key exposed |
| Stored picture history metadata | Yes, bounded pagination | Pending | Same credential requirement; no JPEG download or ring correlation |
| Stored photo download / ring image | No | No | Native authenticated file transport still incomplete |
| Video / downstream audio / microphone | No | No | Native session negotiation and media transport incomplete |
| Local doorbell with player closed | No | No | FCM is not proof of a LAN subscription |
| Strike / gate | Intentionally unavailable | No physical test | Mapping/authentication not established; no command path exists |

Existing V1, Connect 2 R001 and R002 capabilities remain unchanged. No Connect 3
camera is created and the WelcomeEye camera card is not a Connect 3 reader yet.
See [APK/native evidence and exact remaining work](connect3-analysis.md).

## Install and first test

1. HACS → Philips WelcomeEye → Redownload → enable prereleases and select
   **0.4.3-beta.4**, then restart HA. Manual installation: extract the release's
   `welcomeeye_local.zip` into `config/custom_components/welcomeeye_local`.
2. Add Philips WelcomeEye → **WelcomeEye Connect 3 / Philips Door Connect —
   experimental**. Enter its IPv4 address, leave optional credentials blank,
   and accept the experimental scope. Existing V1/V2 entries need no recreation.
3. Keep the official app/live readers closed and device idle. As an HA
   administrator, run **once** in Developer tools → Actions, replacing the
   entity with the new Connect 3 diagnostic sensor:

```yaml
action: welcomeeye_local.connect3_discover
target:
  entity_id: sensor.YOUR_CONNECT3_STATUS
data:
  include_details: true
```

This sends one UDP broadcast on 5000 and listens on 5001/5003 for three seconds.
HA and the device need the same broadcast network; Container port publishing
alone may not provide that. A missing reply is inconclusive. Nothing is sent
at setup, reload, startup or in the background. No second protocol is tried.

Copy the **displayed action response** from this execution. Alternatively use
one script action with `response_variable: connect3_result` and read its trace;
do not run both methods just to collect the response. Send the status, stage,
error codes, counts and decoded advertised ports/model text after reviewing
them. Download the integration's normal diagnostics too. Do not send authCode,
passwords, tokens, device UID, certificate or private HA configuration files.
Optional returned text can itself contain identifiers; HA traces may retain it.

## Optional authenticated reads (not needed for discovery)

The APK receives the device's `authCode` through its device authorization/bind
data. It is **not the cloud account password or opening code**. No verified
normal app-screen export or automatic local bootstrap/renewal exists yet.
Only owners who already possess their own authCode from authorized app data
should enter it using the integration's Reconfigure form. Otherwise stop after
discovery; do not guess a password. No cloud login is implemented.

Use the verified CGI HTTPS port. HTTPS keeps normal certificate verification;
an independently verified SHA256 certificate fingerprint can be supplied for
a self-signed certificate. The integration does not automatically trust a
certificate just because the device responded. Firmware requiring another
authentication branch or a client certificate may reject these reads.

An explicit, bounded access check:

```yaml
action: welcomeeye_local.connect3_check_access
target:
  entity_id: sensor.YOUR_CONNECT3_STATUS
```

If that succeeds, one stored-picture **metadata** search, with a real interval
of at most 24 hours expressed in monitor local time:

```yaml
action: welcomeeye_local.connect3_list_records
target:
  entity_id: sensor.YOUR_CONNECT3_STATUS
data:
  start: "2026-10-01 09:00:00"
  end: "2026-10-01 10:00:00"
  channel: 1
  include_details: false
```

Maximum one search-session request and four page reads within twelve seconds,
no retry. `history_complete: false` means pagination was capped. With explicit
`include_details: true`, the response includes private dates and filenames;
review before sharing. No file download, live capture or photo-cache substitute.

## Validation and rollback

Baseline local suite: 258 tests, one Windows-only skip. The expanded suite,
native KDF reproduction, Python compile, frontend, reproducible package and
exact-commit CI are release gates. CI runs Python 3.12/3.14, HACS, Hassfest,
frontend, existing capture APIs and actual HA 2026.7.3/2026.9.3 R002/Connect 3
services/permissions/config/reload with synthetic responses and mocked device
networking. Hardware validation is **pending for every Connect 3 function**.

To roll back: **disable the experimental Connect 3 entry first**, then HACS →
Redownload → **0.4.3-beta.3**, restart HA. Older releases do not know the new
family. Keep that entry disabled until returning to beta.4 or later. Existing
V1/V2/R002 entries retain their settings; no stable tag or old asset is replaced.

## Three focused follow-up sequences, only if needed

1. Device idle, readers closed: the single discovery action above. Resolve the
   encrypted response/container revision and advertised ports. No ringing.
2. Owner-authorized observation of official app start → direct → optional mic
   → close. Resolve the firmware-selected native connection branch, keys/IV
   assignment and close behavior; do not send account secrets or publish payloads.
3. Official app/monitor ring → history/photo, with no direct reader: identify
   the selected record/download path and reliable event correlation. This is a
   later explicit hardware sequence, not triggered by this beta's diagnostics.

No port scan, output operation or unsolicited hardware test is requested.
