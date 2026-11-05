# 0.4.7-beta.2 — doorbell automations, saved photos and reliability

This optional pre-release extends **0.4.7-beta.1**. Stable remains **0.4.6**.
Existing entries, entity IDs, credentials, certificate approvals, opening
permissions and dashboard names are retained.

The [auxiliary-camera feature from beta 1](release-047-beta1.md) is preserved,
with thanks to [@rpouetpouet](https://github.com/rpouetpouet) for
[PR #16](https://github.com/Mins95/WelcomeEye/pull/16) and the reported V1 video
and snapshot results. Those earlier observations do not establish hardware
validation of every new beta 2 feature.

## Install or update

1. Back up Home Assistant. In HACS, open **Philips WelcomeEye**, enable
   pre-release versions and select **v0.4.7-beta.2** for download. If needed,
   add `https://github.com/Mins95/WelcomeEye` as a custom **Integration** repository.
2. Restart Home Assistant and fully reload the dashboard or Companion frontend.
   Close and reopen any popup containing the WelcomeEye card.
3. Keep the existing integration entry and card. Device options remain under
   **Reconfigure**; microphone preferences and the optional quality panel are
   in the card editor.
4. For YAML-managed dashboard resources, update the existing JavaScript module
   to `/welcomeeye_local/welcomeeye-card.js?v=0.4.7-beta.2`. Do not add a duplicate.

The integration's declared Home Assistant minimum remains **2025.10.0**.
The separate notification blueprint requires **2026.7.0 or newer**. Newer
Home Assistant automation editors provide the standard doorbell trigger; older
versions retain the existing bus events and binary sensors.

[Full installation guide](installation.md) · [Beta options](development-options.md)

## What changes

- **Standard doorbell events:** HA event entities expose accepted ring events
  alongside the existing binary sensors and `welcomeeye_local.ring` bus event.
  Connect 3/R002 use the configured supported channels. Legacy V1/R001 keep one
  event entity without inventing a physical mapping for reported channel codes.
  Startup, reload and connection-status changes do not create a new ring.
- **Saved-photo viewing:** enlarge existing photos, navigate between them and
  zoom from 100% to 400% with keyboard, mouse or touch controls. Browsing stored
  photos does not acquire device media.
- **Microphone preferences:** choose browser echo cancellation, noise suppression
  and automatic gain control. All remain on by default. Changes apply on the
  next deliberate microphone start; they do not alter device volumes or call duration.
- **Optional stream quality:** display selected numeric statistics from an
  already active WebRTC connection while the quality panel is open. The panel
  is off by default, starts no stream and displays no SDP, address or token.
- **Saved-photo notifications:** an optional Companion blueprint attaches the
  private file saved for a ring and provides a dashboard link. It does not take
  another photo or operate a strike/gate.
- **Administrator reads for Connect 3/R002:** bounded [cloud history](cloud-history.md),
  [configured badge catalogue](badge-catalog.md), and
  [device/SD information](development-options.md#device-information-and-sd-card).
  These are explicit reads, without background polling or device writes.
- **Reliability:** HA 2026.11 entity renaming retains active viewers and internal
  references; player cancellation, WebRTC subscription ownership and shared
  local-media cleanup are tightened. [Technical details](development-hardening.md).

## Quick check without opening commands

1. Confirm the downloaded version in HACS and check that the existing device,
   camera entities, custom names and opening permissions are still present.
2. Open an already saved photo, try zoom/navigation and close the viewer.
   Change a card presentation or microphone-processing preference and confirm
   it survives reopening the editor. These checks need no new media session.
3. If you choose to check doorbell reception, press the physical bell and inspect
   the new event entity and existing ring sensor. Automatic capture still obeys
   its existing switch; turn it off first if preserving monitor ringing is the
   priority. No strike or gate test is required.
4. Inspect logs and download diagnostics if something fails. Report the model,
   firmware, HA version and the exact feature tested. Read responses may contain
   private information; review them before sharing.

Installation adds no new automatic physical command or diagnostic polling.
Existing autoplay, capture settings and user-created automations still apply.
Testing live video, microphone or recording remains a separate deliberate action.

## Import the photo notification blueprint

After the beta 2 tag is published, open **Settings → Automations & scenes →
Blueprints → Import blueprint**. Paste this version-specific GitHub file address,
select **Preview**, then **Import blueprint**:

```text
https://github.com/Mins95/WelcomeEye/blob/v0.4.7-beta.2/blueprints/automation/welcomeeye_local/ring_photo_notification.yaml
```

This uses Home Assistant's supported [GitHub blueprint import](https://www.home-assistant.io/docs/automation/using_blueprints/#importing-a-blueprint).
The blueprint is separate from the HACS integration archive. For manual setup,
copy the [YAML file](../blueprints/automation/welcomeeye_local/ring_photo_notification.yaml)
into `config/blueprints/automation/welcomeeye_local/` and reload automations.

Create an automation from **WelcomeEye - Saved doorbell photo notification**.
Select the WelcomeEye device and its **Last doorbell image** for the desired
channel. Enter the exact `notify.mobile_app_...` action for your phone and a
relative dashboard view path such as `/lovelace/welcomeeye`. Create a separate
automation for another channel. Configure automatic ring capture separately if
you want saved-photo notifications; the blueprint does not enable it.

The blueprint checks the entry, device, image, channel, ring sequence and photo
age. Failed saves and stale events are ignored. The attachment is the unique
authenticated `/media/` JPEG for that press, so a later photo cannot replace it.
The phone must be able to reach Home Assistant. Retention or manual deletion
can make an older attachment unavailable. Android/iOS delivery and attachment
display still need confirmation on the receiving phone.

## Validation and hardware limits

The maintainer authorized publication as a beta. Publication remains gated on
the **Validate** workflow passing for the exact release commit; this document
does not claim a CI run has passed. See the [workflow](https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml)
and [recorded validation boundaries](development-hardening.md#validation-boundaries).
Automated fixtures do not contact a physical intercom or operate its relays.

- Acquiring media for a photo, thumbnail, live view or recording can interrupt
  the indoor/outdoor monitor's ringing. Official Philips app notifications
  continue. A longer capture delay does not guarantee avoiding interruption.
- New CGI reads and cloud-history fields remain firmware/service dependent.
  Device-declared capabilities are diagnostic information, not hardware proof
  and never an automatic grant of microphone or relay support.
- Badge identifiers/names require an explicit detailed read and are excluded
  from diagnostic exports. A configured badge has no verified association with
  an opening event or the person who presented it.
- Connect 3/R002 still use the optional cloud connection for supported doorbell
  events. Local independent bell detection and native monitor-photo retrieval
  are not added. Auxiliary cameras do not gain microphone or opening controls.
- Microphone processing depends on browser support. The card still requires
  trusted HTTPS, permission and an explicit microphone action. Native HA camera
  talkback is not enabled by this beta.

## Rollback

Disable automations that depend on beta 2 event entities or the imported photo
blueprint before downgrading. HACS does not remove imported blueprints or their
automations when the integration version changes.

In HACS, select **v0.4.7-beta.1** to retain auxiliary-camera selection, or
**v0.4.6** to return to stable. Restart HA and fully reload the frontend; keep
existing entries and cards. Update any YAML-managed resource URL to the chosen
version. Older versions do not provide beta 2 event entities, diagnostic reads
or card features.

Stable **0.4.6** ignores the auxiliary-input selector and uses input 2 for its
second source. Disable that source if only an input-3 auxiliary camera is
connected. [Beta 1 rollback details](release-047-beta1.md#return-to-stable).
Restore the pre-update backup to recover all earlier settings exactly.
Downgrading does not recover photos already deleted manually or by retention.
