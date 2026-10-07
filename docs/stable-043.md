# 0.4.3 — stable promotion

## Scope

The owner authorized promotion of the latest beta on 2026-10-07. The runtime baseline is **v0.4.3-beta.13**, commit `aad3591322e79df8dc6c88e53fae38335a328cef`.

Integration files retain that behavior; only `manifest.json` and `const.py` change the version to `0.4.3`. Uncommitted snapshot experiments in the development checkout are excluded. The native CRC32C dependency, physical-output single-shot rules and media protocols are unchanged.

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

Before publication, the exact promotion commit must pass **Validate** on `main`: Python 3.12/3.14 regression tests and package build, Node frontend tests, HACS, Hassfest, certificate parser versions and actual HA 2026.7.3/2026.9.3 API/runtime checks with synthetic device boundaries. Those checks validate software behavior and never actuate a real output.

The Release workflow independently checks successful CI for that exact current `main` commit, explicit stable promotion and this evidence record. It creates a new immutable `v0.4.3` tag, verifies downloaded ZIP/checksum assets and then publishes it as the latest stable. Published run results are available in [GitHub Actions](https://github.com/Mins95/WelcomeEye/actions).

## Upgrade and rollback

Install **0.4.3** in HACS, restart HA and fully reload the frontend. Keep entries, passwords, certificate pins, capture preferences and existing Media files. Resource version: `/welcomeeye_local/welcomeeye-card.js?v=0.4.3`.

For an equivalent runtime rollback, select **0.4.3-beta.13** in HACS and restart. For older stable **0.4.2**, first disable V1 cloud notifications and remove experimental Connect 3/R002 entries unsupported by that release. Existing private photos are not deleted by an integration downgrade. The v0.4.2 assets remain intact, including the CRC32C aiortc wheel used by 0.4.3.
