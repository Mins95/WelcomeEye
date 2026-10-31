# WelcomeEye 0.4.5 — quick start

1. Install or update **Philips WelcomeEye 0.4.5** in HACS, restart Home Assistant, then reload the dashboard or Companion app. Keep existing entries.
2. For a new device: **Settings → Devices & services → Add integration → Philips WelcomeEye**. Choose your model and enter its local connection details. R002 and Connect 3 support manual IP setup across routed VLANs without mandatory broadcast discovery.
3. For Connect 3/R002 manual IP setup, certificates are retrieved automatically. Approve the device when prompted; no copied fingerprint or OpenSSL is needed. TCP media and certificate exceptions keep separate security confirmations.
4. Enable **Second outdoor panel** only when installed. Under **Opening commands**, choose each required strike/gate and supply its separate opening code. Saving does not operate a relay.
5. Add **WelcomeEye — Interphone** from the dashboard card picker and select your actual camera entity.

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
```

The card finds the associated second camera automatically. Rename both entrances, strikes and gates in the graphical editor. All enabled opening buttons retain their targets under both video views. Playback starts with a deliberate click; a ring does not automatically open or switch the card.

**Language:** Reconfigure → WelcomeEye language offers French, English and Spanish for the card. HA configuration forms follow your HA profile language.

**Doorbell:** Connect 2 R001 detects rings locally. V1 uses its optional cloud registration without an app-account login. Connect 3/R002 use **Reconfigure → Cloud doorbell notifications** with the official app account. The sensor pulses for five seconds; use it or `welcomeeye_local.ring` in automations. [Cloud setup](qv-cloud-doorbell.md).

**Photos:** manual photos are available on all supported families; Connect 3/R002 require the selected live stream already open. Automatic ring capture is a separate switch: ON by default on V1/R001, OFF on Connect 3/R002. Saved choices remain unchanged.

**Photo management:** choose the automatic capture delay (0–120 seconds, default 4), sidebar visibility and retention (unlimited, 7, 30 or 90 days) in Reconfigure. Photos remain in **Media → WelcomeEye** when the sidebar is hidden. With several entries, hide it on every device to hide the shared library.

**Known limitation:** acquiring a photo or opening video during a call can stop the indoor/outdoor ringing. Official app notifications continue. A longer capture delay does not guarantee avoiding this interruption.

Close HA video before opening Philips. Microphone access requires trusted HTTPS and browser/Companion permission.

[Compatibility](../README.md#-supported-devices) · [Photo library](captures.md) · [0.4.5 notes and rollback](release-045.md)
