# 0.4.3-beta.8

Base: beta.7 (`47aeea7d4b646f937a7007ef62e735b18aa54ca8`).
Branch: `feature/043-legacy-udt-beta8`. Stable remains **0.4.2**.

- Experimental ring capture defaults **ON** for compatible Connect 2 R001
  entries without a saved preference. A saved OFF remains OFF. The existing
  T+4 s, single-attempt shared-media snapshot and private Media storage are
  unchanged. V1, R002 and Connect 3 do not gain ring-capture capabilities.
- Add an explicit, bounded SCT/UDT handshake diagnostic reconstructed from the
  original APK. It requires an administrator, an idle legacy entry, observed
  missing/unusable UDP 1500 discovery and an independently established UDP
  endpoint. No scan, cloud, authentication, media or output command is sent.
- Retain the firmware 469/470 and additional-camera 18/0x12 diagnostics, with
  stronger native evidence. The firmware query was observed successfully on
  the owner's Connect 2; optional camera source mapping remains unvalidated.

[Diagnostic actions, exact evidence and limits](experimental-diagnostics-beta8.md).
Connect 3 video and its activation procedure remain those of
[beta.7](release-043-beta7.md); its hardware video validation is still pending.
The CRC32C dependency, physical single-shot controls and existing photos are
unchanged.

## Validation

The offline suite covers the native-layout handshake, synthetic UDP peers,
rejected packets, bounded timeout, cancellation, privacy, permissions and the
new capture default. Full Python/frontend and actual-HA checks run in CI.
Synthetic UDP success is not a hardware UDT result.

During preparation, the owner rang the real Connect 2 using the installed
beta.7 with capture already ON. The monitor retained its native photo; HA
created and saved a new 720 × 576 JPEG, emitted `welcomeeye_local.ring_image`
and released its temporary media worker. This revalidates the unchanged capture
mechanism, not a beta.8 deployment or the default migration itself.

The separate firmware query used one private 509 request, correlated reply 470
in 172 ms, sent session stop and closed TCP. The HA entry was cleanly disabled
for the isolated experiment and re-enabled afterwards. No physical output was
operated. UDT was not tried on this device: its legacy discovery works, so the
explicit absence condition does not apply.

## Installation and rollback

HACS → WelcomeEye → Redownload → **0.4.3-beta.8**, restart HA and reload the
frontend. Keep the existing entry and saved photos. Check the capture switch:
only entries with no saved preference adopt ON; an explicit OFF is respected.

To roll back, close the player, redownload **0.4.3-beta.7** and restart HA.
Entries without a saved capture preference return to beta.7's OFF default;
saved ON/OFF values and private photos are retained. No monitor reset is needed.
