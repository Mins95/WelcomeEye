# 0.4.7-beta.2 options

This guide covers the additions in **0.4.7-beta.2**, an optional pre-release.
Stable remains **0.4.6**. Existing entries, credentials and physical-command
permissions are retained. See [installation, quick checks and rollback](release-047-beta2.md)
before updating; new diagnostics and notification automations require deliberate setup.

## Standard doorbell events

The existing binary ring sensors and `welcomeeye_local.ring` event remain
available. New standard Home Assistant doorbell event entities make the same
accepted presses available to the visual automation editor and the
[`doorbell.rang` trigger](https://www.home-assistant.io/integrations/doorbell/).

Connect 3/R002 use separate event entities for configured, supported channels.
Legacy V1/R001 retain a single event entity where the original report does not
establish a physical channel mapping. A reported channel is metadata, not a
claim that another panel is present. No event is replayed when Home Assistant
starts or an entity is reloaded.

An event entity records an occurrence. It is not a call-in-progress sensor and
does not answer the intercom, play audio or start a camera.

## Device information and SD card

In **Developer tools → Actions**, an administrator can run
`welcomeeye_local.read_device_info` against the Connect 3/R002 status sensor.
This uses the saved IP, credentials and approved HTTPS trust. It does not query
the device during installation or startup, start media, format a card, change
settings or contact the cloud.

Only selected model/firmware, declared capability and storage fields are
retained. Identifiers, keys, arbitrary response content and addresses are not
exported. Declared SDK capabilities never enable a microphone, relay or other
feature automatically. Firmware-specific support still needs hardware checks.

The read is bounded, with no retries. It uses device status first and may read
the SD-specific endpoint only when the response explicitly announces SD support
but omits storage information. Unsupported or malformed responses do not become
an apparently healthy device or an empty card.

```yaml
action: welcomeeye_local.read_device_info
target:
  entity_id: sensor.your_welcomeeye_status
data: {}
```

Select the real status sensor from Home Assistant rather than using this example
literally. **Reported firmware** and **SD card status** are diagnostic sensors;
their values are unknown before a successful read. A reconfiguration invalidates
the previous snapshot, and reloading the integration clears it.

SD capacity values keep the device's reported units unless their meaning is
established. A used percentage can be calculated from a coherent total/free
pair without assuming bytes or gigabytes. These are diagnostics, not a storage
control interface. The local cache is not a background monitor.

V1/R001 are not sent QV CGI requests: the inspected legacy API does not establish
these remote reads. Their existing media and doorbell behavior is preserved.

## Browser microphone processing

The card offers preferences for echo cancellation, noise suppression and
automatic gain control. They apply to the browser microphone capture, not to
the intercom's speaker or ringtone. Existing processing remains the default.

Microphone permission and the explicit Microphone button are still required.
Listening never enables transmission. Preferences are requested from the
browser; unsupported processing may be ignored. They take effect on the next
explicit microphone start rather than restarting a conversation automatically.

Open the dashboard card editor to change these options. YAML is also supported:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
echo_cancellation: true
noise_suppression: true
auto_gain_control: true
show_stream_stats: false
```

All three microphone preferences default to `true`. `show_stream_stats` defaults
to `false`; enable it to make the optional quality panel available. Saving these
settings starts neither video nor microphone. Other card options and custom
entrance/output names are preserved.

## Saved photos and stream quality

Photo enlargement, navigation and zoom use the saved images already available
to the card/library. They do not take a photo or acquire device media. Keyboard
and touch controls retain a way to close the viewer and return to the invoking
control. Zoom ranges from 100% to 400%, with pinch/pan, Ctrl+wheel, double-click
and `+` / `-` / `0` controls. Use previous/next or the arrow keys to navigate,
and Escape to close.

The optional quality panel reads statistics from an already active WebRTC peer.
It does not open another stream or keep one alive. Measurements reset on close
or source change; unsupported statistics are unavailable, not zero. Network RTT
is not the total delay from camera capture to display. Only selected numeric
measurements are displayed, excluding SDP, network addresses and tokens.

With the option enabled, open the quality panel during a live WebRTC view. Only
the active peer is sampled while that panel is open; HLS does not pretend to
provide WebRTC statistics. Sampling runs every two seconds. A request that
exceeds four seconds suspends sampling until the panel is reopened.

## Photo notifications

The supplied blueprint reacts to a successful saved ring-photo event. It uses
that photo and a dashboard link, without a second capture. Automatic ring
capture must already be enabled if this behavior is wanted; the blueprint does
not enable it. It retains the known limitation that acquiring the original
photo can interrupt the physical monitor's ringing.

Notification delivery and attachment retrieval need Companion validation on
Android/iOS. This is independent of whether the device delivered a ring event.
The integration update does not install or enable a notification automation.

The optional blueprint requires **Home Assistant 2026.7 or newer**:
[`ring_photo_notification.yaml`](../blueprints/automation/welcomeeye_local/ring_photo_notification.yaml).
Follow the [GitHub import instructions](release-047-beta2.md#import-the-photo-notification-blueprint)
to use the beta 2 version. Alternatively, place the file under
`config/blueprints/automation/welcomeeye_local/` and reload automations.
Create an automation from **WelcomeEye - Saved doorbell photo notification**.
Select the device and its **Last doorbell image** entity, enter the real
`notify.mobile_app_...` action and the dashboard view path. Create a separate
automation for another outdoor panel. This is not installed automatically by
the integration package.

The attachment uses the unique authenticated `/media/` JPEG saved for that
press. It does not use the changing latest-photo URL. The blueprint checks the
device, entry, image, ring sequence and age before notifying; missing save
identifiers, failed saves and stale events are ignored. Retention or manual deletion may
make an older notification's attachment unavailable.
