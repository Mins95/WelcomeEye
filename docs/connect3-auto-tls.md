# Connect 3 — quick setup (0.4.4)

1. Install stable **0.4.4** through HACS, restart HA, then choose **Philips WelcomeEye → WelcomeEye Connect 3**. Keep an existing entry and use **Reconfigure** instead of deleting it.
2. Enter its **local IP address** and **local connection password**. Optional local discovery can help find the device; manual IP setup works across routed VLANs without UDP broadcast.
3. Leave connection selection automatic. HA inspects the necessary endpoints without sending credentials. If the local certificate is unknown, approve it in HA after checking that this is your device. No console, PEM file or copied fingerprint is needed.
4. If TLS media is refused and the fixed QV TCP endpoint answers the expected negotiation, HA proposes TCP with an explicit security confirmation. It never silently changes a working connection. Advanced options retain manual ports and transport selection.
5. Authorize opening controls only if wanted, supply their **separate opening code**, and select each installed output in **Opening commands**. Enable **Second outdoor panel** if installed and not already known; secondary relays require their own target approval. Save, then add the **WelcomeEye** card from HA's card picker.

Use your actual camera entity; existing cards keep working:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
name: WelcomeEye
```

The card obtains associated sources from the integration, not guessed entity names. With one source there is no selector; with a configured second source it offers **Entry 1 / Entry 2**. Labels are editable in the graphical editor. Source changes close the previous stream and microphone first. Initial playback is muted.

HA-managed card resources update automatically. For YAML-managed resources, use `/welcomeeye_local/welcomeeye-card.js?v=0.4.4` as the module URL and reload the frontend.

## What is confirmed

| Function | Entry 1 | Entry 2 |
| --- | --- | --- |
| A331 TCP video | Hardware confirmed | Hardware confirmed in beta.8 |
| A331 sound | Hardware confirmed | Available; audible confirmation remains installation-specific |
| A331 microphone | Hardware confirmed | Separate opt-in; physical speaker destination to confirm |
| A331 strike/gate | Hardware confirmed | Separate physical-target approval |
| A350 TLS video, sound and outputs | Hardware confirmed | Requires a separate multichannel test |
| Cloud doorbell | Optional; field delivery to confirm | Optional; panel mapping to confirm |
| Manual photos | Available with live video | Available with live video |

Each authorized output remains available under both camera views and retains its own physical target. Secondary outputs require separate approval. Switching sources sends no opening command.

For optional account-based alerts, follow [Cloud doorbell notifications](qv-cloud-doorbell.md). Native local bell detection remains unconfirmed; a cloud notification never opens video or takes a photo. Manual photos use the open live stream. Administrators can browse and remove saved captures in **Photos WelcomeEye**; see [photos and retention](captures.md).

QV discovery's `channels` field is only an advertised hint: it is not reliable proof of accessible outdoor sources. The fallback option does not claim confirmation. A successful, deliberately opened stream records its usable QV selector privately for the same endpoint and pins. HA never opens video at startup just to detect a second panel. A beta.8 test entity alone is not a saved successful result.

## Trust and existing installations

- Unknown certificates require **trust on first use**. This records your choice; it is not independent proof of device identity. System trust plus server identity can be accepted automatically.
- CGI HTTPS and TLS media have independent certificate checks. TCP media has no TLS certificate; CGI remains verified HTTPS. QV credential encryption does not provide TLS-equivalent peer authentication, integrity or replay protection. Microphone data on this legacy TCP path is only partly encrypted.
- Certain firmware certificates need separate, explicit approval for identical validity dates or an RSA 1024-bit key. Each exception is bound to the exact certificate and endpoint, never a global relaxation.
- A changed certificate or endpoint blocks credentials and raises a **Repairs** issue for reapproval. No password is sent during certificate/transport inspection. Local connection setup uses no HTTP fallback or cloud lookup; optional cloud doorbells are configured separately.
- Existing passwords, opening codes, QR bindings, pins, camera IDs and registry names are preserved. Blank secret fields keep existing values. Upgrading alone does not change transport or enable controls previously disabled.
- QR import remains in Advanced options and retains its existing identity consistency check; manual IP/password setup does not depend on broadcast.

Advanced certificate/access actions remain available for support. They are not installation requirements. For a refused port, trusting its certificate cannot fix network reachability; Advanced settings remain available for unusual firmware or firewall rules.

[0.4.4 notes and rollback](release-044.md). Tests use simulated devices; no physical output is exercised automatically.
