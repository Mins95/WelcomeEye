# Connect 3 — quick setup (0.4.4-beta.9)

1. Install the beta through HACS, restart HA, then choose **Philips WelcomeEye → WelcomeEye Connect 3**. Keep an existing entry and use **Reconfigure** instead of deleting it.
2. Enter its **local IP address** and **local connection password**. Optional local discovery can help find the device; manual IP setup works across routed VLANs without UDP broadcast.
3. Leave connection selection automatic. HA inspects the necessary endpoints without sending credentials. If the local certificate is unknown, approve it in HA after checking that this is your device. No console, PEM file or copied fingerprint is needed.
4. If TLS media is refused and the fixed QV TCP endpoint answers the expected negotiation, HA proposes TCP with an explicit security confirmation. It never silently changes a working connection. Advanced options retain manual ports and transport selection.
5. Enable opening controls only if wanted, and supply their **separate opening code**. Enable **Second outdoor panel** if installed and not already known. Save, then add the **WelcomeEye** card from HA's card picker.

Use your actual camera entity; existing cards keep working:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
name: WelcomeEye
```

The card obtains associated sources from the integration, not guessed entity names. With one source there is no selector; with a configured second source it offers **Entry 1 / Entry 2**. Labels are editable in the graphical editor. Source changes close the previous stream and microphone first. Initial playback is muted.

HA-managed card resources update automatically. For YAML-managed resources, use `/welcomeeye_local/welcomeeye-card.js?v=0.4.4-beta.9` as the module URL and reload the frontend.

## What is confirmed

| Function | Entry 1 | Entry 2 |
| --- | --- | --- |
| A331 TCP video | Hardware confirmed | Hardware confirmed in beta.8 |
| A331 sound | Hardware confirmed | Implemented; audible hardware test pending |
| A331 microphone | Hardware confirmed | Unavailable: target routing not established |
| A331 strike/gate | Hardware confirmed | Unavailable: physical mapping not established |
| A350 TLS video, sound and outputs | Hardware confirmed | Requires a separate multichannel test |
| Standby doorbell / photos | Under investigation | Under investigation |

Entry-1 output controls retain their target even while Entry 2 is selected; labels make that target explicit. They never become unverified Entry-2 controls. Switching sources sends no opening command.

QV discovery's `channels` field is only an advertised hint: it is not reliable proof of accessible outdoor sources. The fallback option does not claim confirmation. A successful, deliberately opened stream records its usable QV selector privately for the same endpoint and pins. HA never opens video at startup just to detect a second panel. A beta.8 test entity alone is not a saved successful result.

## Trust and existing installations

- Unknown certificates require **trust on first use**. This records your choice; it is not independent proof of device identity. System trust plus server identity can be accepted automatically.
- CGI HTTPS and TLS media have independent certificate checks. TCP media has no TLS certificate; CGI remains verified HTTPS. QV credential encryption does not provide TLS-equivalent peer authentication, integrity or replay protection. Microphone data on this legacy TCP path is only partly encrypted.
- Certain firmware certificates need separate, explicit approval for identical validity dates or an RSA 1024-bit key. Each exception is bound to the exact certificate and endpoint, never a global relaxation.
- A changed certificate or endpoint blocks credentials and raises a **Repairs** issue for reapproval. No password is sent during certificate/transport inspection. No HTTP fallback or cloud lookup is used.
- Existing passwords, opening codes, QR bindings, pins, camera IDs and registry names are preserved. Blank secret fields keep existing values. Upgrading alone does not change transport or enable controls previously disabled.
- QR import remains in Advanced options and retains its existing identity consistency check; manual IP/password setup does not depend on broadcast.

Advanced certificate/access actions remain available for support. They are not installation requirements. For a refused port, trusting its certificate cannot fix network reachability; Advanced settings remain available for unusual firmware or firewall rules.

[Short test and rollback](release-044-beta9.md). Tests use simulated devices; no physical output is exercised automatically.
