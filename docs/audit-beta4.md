# Consolidation audit — 0.4.2-beta.4

## Starting points verified on 2026-09-24

```text
c933b4c  main / v0.4.2-beta.2
  └─ af38fda  fresh snapshot preparation
      └─ 895e7d2  hardware-revalidated fresh snapshot baseline
          └─ 7692511 … 54a230d … 5fd201e …
              └─ 5206009  passive ring/CRC investigation checkpoint
                  └─ dfb0871  one shared snapshot at T+4
                      └─ de36fc1  idle-ring hardware observations
                          └─ 9278663  v0.4.2-beta.3
                              └─ beta.4 consolidation
```

The initial `main` was `c933b4c866aa0fb3e00ab9fa27229927934ab9d0`, not
the published beta.3. Beta.3 targets
`927866341bb885a5ab2348c454d7358f3813f495` and is a prerelease with a HACS ZIP.
Its existing ZIP digest is
`46c4808dfb30716ab2ccfafa4d8cd685db6427cba378f2cf958d539990bce091`.
Baseline `895e7d2d493f396913d5f3a119d4387ccabfafbb` and investigation
`520600944cc494524029a4f30cc183b8129d6bbe` are ancestors of beta.3.

Main already contained the hardware-validated V1 media/microphone and strike
paths, Connect 2 controls, resource registration/migration, and five-second
ring pulse. It did not contain the generation-based fresh snapshot backend,
ring ImageEntity, delayed capture, or CRC packaging diagnostics. The candidate
starts from beta.3 so none of these later validated fixes is dropped.

Before edits, the main tests passed when invoked as configured in its workflow
(11 resource tests and 17 doorbell tests); all 71 beta.3 tests also passed.
The initial generic unittest discovery exposed a test runner bug: the doorbell
test interpreted `discover` as a filesystem path. It now supports normal
discovery as well as the historical standalone invocation.

## Investigation code deliberately excluded from active behavior

- No native monitor-photo request, completion-signal probe, speculative CGI,
  alternate port, transport or new protocol mapping is introduced. The unused
  native-photo provider branch is removed from the ring capture coordinator.
- One fresh shared-media acquisition starts at T+4. Automatic capture is now
  explicitly enabled by the user and defaults to OFF, including upgrades.
- The V1 0/3/0 listener is not started for a known V1; discovery by an initial
  media session also closes an already-running investigation listener. No local
  V1 doorbell support is claimed. Cloud signaling remains possible, unproven.
- Passive metadata tracing remains bounded and disabled by default. Historical
  test evidence and the upstream CRC32C patch are retained as documentation;
  no private wheel, warning suppression or ineffective package pin is used.
- Existing validated media profiles, V1 Start/Stop AV, encryption and physical
  command packets remain in place. Physical commands are never automatic tests.

## Review coverage and confirmed corrections

| Area | Reviewed files | Result |
| --- | --- | --- |
| Setup/configuration | `__init__.py`, `config_flow.py`, `entity.py` | Distinguish unsupported protocol from connection/auth errors; register camera service and switch platform; persist switch preference in config entry options. |
| Shared worker/snapshots | `hub.py`, `snapshot.py`, `media.py`, `camera.py` | Cancelled release now drains worker teardown before unlocking; hub stop drains owned cleanup; late ring callbacks are ignored after stop; generation freshness and existing live/microphone leases retained. |
| Transport/outputs | `client.py`, `control.py`, `v1_control.py`, `protocol.py`, `protected.py` | A partial-frame timeout is fatal instead of resuming at the wrong framing boundary; clean idle reads still resume. Socket references survive concurrent close safely. Unload during Connect 2 setup cannot start a new fallback control session. Single send/no replay preserved. |
| RTC/audio | `rtc.py`, `player.py`, `talkback.py`, `ice_cleanup.py`, `lifecycle.py` | Cancelled executor start-talk cannot be overtaken by stop-talk. Stop drains work and clears ownership/codec state. Existing READ/CONTROL separation and ICE cleanup retained. |
| Ring/entities | `ring.py`, `ring_trace.py`, `ring_image.py`, `image.py`, `switch.py`, `binary_sensor.py`, `sensor.py`, `button.py` | Default OFF, cancel pending timer on disable/unload, reject stale results, distinct memory-backed images. V1 listener status explicitly unsupported. |
| Persistence | `manual_snapshot.py`, `media_storage.py`, `services.py`, `services.yaml` | Validated bounded JPEGs; HA media_dirs and timezone; exclusive atomic publication without overwrites; drain cancellation; preserve image on disk failure; no public URL or secret in generated names. |
| Frontend | `frontend/welcomeeye-card.js`, `card_resource.py` | Fresh photo control and five controls kept on one compact row; missing entity handling; duplicate open/late completion guards; cancellable ICE wait; robust DataChannel/fullscreen/HLS cleanup; serialize microphone replacement; duplicate resource evaluation safe. Automatic capture is controlled only by its HA switch, as requested. |
| Diagnostics | `diagnostics.py`, `crc32c_diagnostics.py`, `v1_video.py`, `v1_video_diagnostics.py` | Snapshot/ring/save counters and relative filenames; no image bytes, filesystem paths, credentials, UID/IP, SDP or ICE candidates exported. |
| Distribution | workflows, release builder, manifest, translations, README/docs | Full Python/JS suites and HA runtime checks; reproducible HACS ZIP and SHA256; release only after validation of the current main commit. Published tags/assets are preserved by default; the explicitly authorized beta.4 replacement is the scoped exception recorded below. |

Tests simulate threads, cancellations, media frames, failures, permissions and
timers without contacting an intercom or sending a physical command. JPEG and
storage tests use real Pillow/filesystem operations. A separate CI runtime check
uses Home Assistant 2026.9.3 APIs. These checks are not hardware validation.

## Hardware status and publication decision

The pre-existing hardware evidence remains valid for its recorded revisions.
The tester confirmed physical V1 gate operation on 2026-09-24; the current
support tables now reflect that confirmation of the existing control path.
No beta.4 hardware test is claimed by this audit. The maintainer explicitly
requested publishing the beta after software checks so it can be installed and
tested before returning on site. Beta.4 is a **prerelease**, not 0.4.2 stable.

Use the [hardware test checklist](hardware-beta4.md) for manual snapshots with video
closed/open and microphone active, Media filenames/timezone, OFF and ON ringing,
T+4 delay, native monitor-photo preservation, subsequent video/microphone and
Philips live recovery over multiple cycles without a power-cycle. An actual
human must perform and confirm the ring and physical-output observations.

Existing unresolved observation: the monitor's native photo could be absent
after closing HA video, including on the preceding baseline without automatic
capture. This release does not claim that issue is physically fixed.

## Explicitly authorized replacement of beta.4

After the initial publication, the maintainer requested all five card controls
on the same row. After being informed of the original immutable-tag rule, the
maintainer explicitly authorized replacing **this same `v0.4.2-beta.4` tag and
ZIP**. This exception does not authorize replacing any other tag or stable
release. The application-code change is limited to compact five-column CSS;
Photo behavior, the separate HA capture switch and the existing Media storage
backend are unchanged. The simulated card was checked at 280 and 360 px with
one control row and no horizontal overflow.

The initial tag pointed to `ef599f64e4917c293deed38d1a2c16022c3e751b`; its
223530-byte ZIP had SHA256
`4344443d21d5a9d4b5894c12ff956b8935ed96134088d0047c21af89775e0de2`.
The replacement ZIP is 223556 bytes with SHA256
`038e61976ab1c33e8fb0b7b2f5b4a233f8234d2227a6a55540919a54ce6057ab`.
The 12 targeted release-notes synchronization/guard tests are separate from the
21 frontend tests. Final replacement validation and commit references are
recorded in the release workflow and delivery report.

Read-only inspection of an existing user-created manual snapshot at 17:36
showed `capture_status=ready`, `source=fresh_snapshot`, a relative filename under
`WelcomeEye/`, a matching local Media Source reference and null `save_error`.
This confirms that user's stored capture without triggering another image or
publishing its private device-folder digest or access token. It does not
validate the remaining video/microphone/ring hardware scenarios. Because the
version string stays the same, existing HACS users must **redownload beta.4**,
restart HA and fully reload the frontend to receive the replacement.

## Rollback

Select the previous prerelease in HACS, or restore the saved integration folder,
then restart Home Assistant and fully reload the frontend. Media files are
user-owned and remain intact; the integration performs no surprise retention.
For the earlier hardware-revalidated code baseline, use commit `895e7d2`.
Beta.3 enabled automatic ring capture by default; rolling back to it removes
beta.4's OFF-by-default protection and switch. Prefer a new tag for future
changes; the explicit beta.4 replacement authorization above is limited to
that release.
