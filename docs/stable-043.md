# 0.4.3 — stable promotion

## Scope

The owner authorized promotion of the latest beta on 2026-10-07. The runtime baseline is **v0.4.3-beta.13**, commit `aad3591322e79df8dc6c88e53fae38335a328cef`.

Integration files retain that device behavior. Uncommitted snapshot experiments in the development checkout are excluded. The corrected publication updates the aiortc dependency for HA 2026.10/PyAV 19, retaining the native CRC32C backend. Physical-output single-shot rules and media protocols are unchanged.

## HA 2026.10 correction and republication

The initial 0.4.3 release was removed by the owner after a requirement installation
failure on HA 2026.10.0. Core requires `av==19.0.0`, while the old aiortc derivative
requires `av<18`; these ranges conflict. The replacement keeps aiortc 1.15.0 media
code and native CRC32C, using the crc2 build with validated PyAV 19 compatibility.
See [cause, build and validation](../tools/crc32c/AIORTC-DERIVATIVE.md).
[Native validation 37670791459](https://github.com/Mins95/WelcomeEye/actions/runs/37670791459)
passed on musl x86_64/aarch64 with HA 2026.9.3/2026.10.0 and on glibc PyAV 19,
including the old failure/new full-HA-install reproduction, upstream codec/SCTP
tests and three bidirectional WebRTC cycles per runtime.

The owner explicitly authorized correcting and restoring **the same 0.4.3**.
The old tag target was `6fc08aa8ba1694ceac034f633e608861f5d72a70`; the old ZIP
SHA256 was `aa8956ccf4a1b581ecdf6beb4af6355d9869f2b69288c36da9ca68dbd7786bd0`.
This is a scoped replacement, not a general permission to mutate stable tags.

## Hardware evidence

| Device | Confirmed | Remaining limits |
| --- | --- | --- |
| Connect 2 R001 | Video, downstream sound, microphone, strike/gate, local ring, fresh manual and automatic photos, Philips resumption after HA closes | Automatic media acquisition during ringing interrupts the monitor/outdoor chime around five seconds; native photo retrieval unresolved |
| Connect V1 / DES9900VDP | Video, sound, microphone, strike/gate; tester confirmed beta.13 cloud rings and automatic photos | Cloud required for rings; automatic photos can interrupt monitor ringing |
| Connect 3 / IDS94E6SW | Tester confirmed repeated moving video, sound, physical strike/gate and Philips resumption | Microphone implemented but physical confirmation pending in the available report; standby ring/photo not implemented |
| Connect 2 R002 | Discovery and diagnostic observations | Complete media/microphone/output operation remains under investigation |

Connect 3 evidence: [beta.9 moving video](https://github.com/Mins95/WelcomeEye/issues/1) and [beta.10 audio/output report](https://github.com/Mins95/WelcomeEye/issues/1#issuecomment-6013167372). Owner/tester V1 delivery and capture-interruption confirmations were supplied in the development conversation. No additional physical action is performed by this promotion.

The observed capture sequence is one fresh acquisition from **T+4 seconds**, followed by ringing interruption around five seconds. Smartphone notifications/ringing through the official app continue. The capture switch remains available; turning it OFF prevents scheduled ring photos but does not prevent media requests from a live player or camera thumbnail. No non-interruption guarantee is made.

## Software validation

Before publication, the exact corrected commit must pass **Validate** on `main`: Python 3.12/3.14 with PyAV 17/19, package build, Node frontend tests, HACS, Hassfest, certificate parser versions and actual HA 2026.7.3/2026.9.3/2026.10.0 API/runtime checks with synthetic device boundaries. Native dependency validation also runs clean musl x86_64/aarch64 images, upstream codec/SCTP tests and bidirectional WebRTC cycles. Those checks validate software behavior and never actuate a real output.

The normal Release workflow checks successful CI for the exact current `main` commit. This exceptional, owner-authorized republication additionally checks the old tag before replacing it, verifies the new downloaded ZIP/checksum assets and publishes 0.4.3 as the latest stable. Published run results are available in [GitHub Actions](https://github.com/Mins95/WelcomeEye/actions).

## Upgrade and rollback

Install **0.4.3** in HACS, restart HA and fully reload the frontend. If the initial 0.4.3 was already installed, use **Redownload** because its version number is unchanged. Keep entries, passwords, certificate pins, capture preferences and existing Media files. Resource version: `/welcomeeye_local/welcomeeye-card.js?v=0.4.3`.

Beta.13 has the same device behavior but the old dependency and is not a compatible rollback on HA 2026.10. For a full rollback there, also restore the preceding HA Core version. For older stable **0.4.2**, first disable V1 cloud notifications and remove experimental Connect 3/R002 entries unsupported by that release. Existing private photos are not deleted by an integration downgrade. Previously published v0.4.2 assets, including the crc1 wheel, remain intact.
