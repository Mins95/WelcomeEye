# Troubleshooting

[Documentation](README.md) · [Installation](installation.md) · [Settings reference](settings.md)

Keep your existing integration entry while diagnosing a problem. Deleting it loses useful configuration and can change entity IDs. Do one deliberate test, then download diagnostics while the relevant session is still in the recent history.

## Card or editor does not load

1. After an integration update, restart HA completely, then fully reload the browser or Companion frontend.
2. Check that the card uses `type: custom:welcomeeye-card` and an existing WelcomeEye camera entity.
3. If you manage resources in YAML, check the single module entry shown in the [card guide](card.md#card-resource-and-updates). Do not load old and new copies together.
4. If custom labels are missing, edit the **WelcomeEye card** itself, not a surrounding grid or vertical-stack card.

## No second entrance or missing opening button

- Enable **Second outdoor panel** on the primary device entry. An extra indoor monitor alone is not a second outdoor camera.
- The card must use a camera from that entry. Association comes from integration metadata, not an entity-name suffix.
- Select each required output in **Reconfigure → Opening commands**. Secondary outputs also need their individual permissions; Connect 3/R002 require the separate opening code and applicable control authorization.
- Renaming a button does not enable it. A disabled output is intentionally absent from the card.
- Reload the dashboard after reconfiguration. If it remains missing, include the current integration version and diagnostics in your report.

## Video cannot start

Close Philips and other HA viewers first, then open one video. If it fails, use the diagnostic stage to distinguish the cause:

| Diagnostic / symptom | Next check |
| --- | --- |
| `auth_code_rejected`, failure at `stream_key` | The CGI endpoint rejected the local connection password. Check the correct primary device and credential; this is before media playback, not a video codec error. Do not substitute the cloud password or opening code. |
| `tcp_connect` / connection refused | Check the selected endpoint and HA-to-device reachability. On supported TCP firmware, use Automatic or explicitly approved QV TCP; changing TLS trust cannot open a closed port. |
| TLS/certificate failure | Review the result in Reconfigure or Repairs. A changed certificate needs approval; do not disable global SSL verification. |
| Setup/PLAY accepted, no decoded image | Download diagnostics immediately after one attempt. Report selected transport and requested channel; do not repeatedly open several players. |
| Frames decoded, browser shows no video | Check WebRTC/ICE diagnostics, browser/Companion version and whether the problem differs between local and remote access. |

For manual R002/Connect 3 connections, UDP discovery failing across VLANs does not prove the media endpoint is unreachable. Use the manual IP route; no new broad network scan is needed.

## No sound or microphone

Sound starts muted in the card: first open the video, then enable Sound. The microphone requires trusted HTTPS and permission in the browser or Companion app. Verify the selected panel and any secondary microphone/TCP consent. Do not infer microphone reception from the browser permission alone; listen at the intended outdoor panel.

For a report, distinguish **no sound from the panel** from **the visitor cannot hear my microphone**. They use different paths.

## Opening has no effect

Check the enabled target and separate opening code. A successful protocol response does not prove the physical relay moved, and does not indicate a gate position. If a result is uncertain, do not repeatedly press the button. Record which physical output reacted and share diagnostics without the opening code.

## Doorbell sensor does not react

- R001 uses local detection; V1 needs its optional cloud toggle; R002/Connect 3 need the official app account and matching device selection. [Model setup](installation.md#set-up-the-doorbell-and-photos).
- Close viewers and disable automatic photos while isolating ring reception from media acquisition.
- Press one outdoor bell, let the cycle finish, then check the corresponding sensor. Its active pulse lasts five seconds; a resting sensor afterwards is normal.
- Download diagnostics after the press. Accepted ring counters and `ring_emitted` indicate event delivery, not an automatic dashboard sound or popup.
- If cloud status reports `failed_reload_required`, restore connectivity and reload the integration. Connection attempts are bounded, not retried forever.

Do not use the separate native alarm diagnostic as a replacement for cloud enrollment. It is a support tool, not the normal Connect 3 bell route.

## Ringing stops when a photo is taken

This is the documented media-acquisition limitation. Opening video, refreshing a thumbnail or taking an automatic photo can stop the monitor's call; the Philips app's ringing continues. Disable **Ring image capture** to stop automatic photos. Existing viewers or thumbnail requests can still acquire media. A longer capture delay only delays the possible interruption. [Photo guide](captures.md).

## Photo or library is missing

- On Connect 3/R002, open the selected live video before a manual Photo request.
- For automatic photos, first verify ring detection and the separate capture switch. Changing the delay does not enable capture.
- The sidebar library is for administrators. It remains visible while any loaded device enables it; Media browsing remains available when it is hidden.
- Reloading HA clears the in-memory Last photo/Last ring images, but does not remove saved Media files.
- Check capture and save results separately: a fresh photo can exist in memory even if writing to storage failed.
- Retention and manual deletion remove files permanently. The library can also clean empty date folders. [Storage and management](captures.md).

## What to include in an issue

Download the entry's diagnostics from **Settings → Devices & services → Philips WelcomeEye → entry menu → Download diagnostics**. Include:

- Integration and HA version; HAOS/Container installation type.
- Model and firmware; one or two outdoor panels; transport when shown.
- The exact action, selected entrance and observed result.
- Whether Philips works after closing HA video; browser/Companion and local/remote access if relevant.
- Diagnostics captured immediately after the attempt, and the relevant error text if available.

Review attachments before posting. Do not share passwords, opening codes, QR codes, tokens, account identifiers or private visitor photos. Redact IP addresses and personal names in screenshots. Integration diagnostics omit secrets, but arbitrary application logs and screenshots may not.
