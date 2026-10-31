# Connect 3 — video, sound, microphone and controls

**Connect 3 is validated in 0.4.5 stable**, with TLS media on A350 and QV TCP on A331. Cloud doorbell reception is confirmed on A331. Two configured outdoor panels share one card; their images/audio stay separate. Keep the transport and approvals already working on your device.

Use the [automatic setup guide](connect3-auto-tls.md): local IP, local connection password, certificate approval and, when required, TCP consent. Configure each wanted opening separately with its opening code. Keep existing entries and entity IDs when updating.

The [WelcomeEye card](card.md) provides sound, microphone, source selection, openings and photos. Open HA over trusted HTTPS and grant microphone permission. Close the viewer before using Philips. A source change stops the old microphone and media first. The second microphone requires its own activation; check that speech reaches the selected speaker.

Every opening has a fixed channel and relay, independent of the displayed camera or custom label. No opening is triggered during setup, a camera switch or reconnection. One click means at most one attempt. [Four output routes](connect3-four-outputs.md).

Manual **Photo** uses the already open selected live stream. Automatic ring photos are a separate, OFF-by-default switch, with a configurable delay and the known monitor-ringing interruption. [Photos and storage](captures.md).

For ringing, use [Cloud doorbell notifications](qv-cloud-doorbell.md). HA updates a five-second sensor pulse and emits an event for automations; it does not automatically play sound or open/switch a camera. The optional native alarm diagnostic is not a working local standby bell detector.

[Installation](installation.md) · [0.4.5 release and rollback](release-045.md)
