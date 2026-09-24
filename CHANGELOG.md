# Changelog

All notable changes to this project will be documented in this file.

## 0.4.2 - 2026-09-24

- Promote the corrected `0.4.2-beta.4` implementation to stable at the maintainer's request. Integration files change only their version; device protocols and media behavior are unchanged.
- Automatic ring photos remain **experimental and opt-in**: enable **Capture sur sonnerie / Ring image capture**. One fresh capture starts at or after T+4, updates **Last ring** and saves to authenticated HA **Media → WelcomeEye**. The latest Connect 2 field check confirmed the image and its saved JPEG after enabling the switch.
- Include the **Photo** card button, separate manual snapshot entity, five controls on one row and automatic dashboard resource registration. Restart HA and fully reload the frontend after updating. YAML-managed resources remain manual.
- Preserve validated Connect 2 features and V1 video, microphone, strike and gate support. **Local V1 doorbell remains unsupported.** Native monitor-photo loss after prior video use remains under investigation.
- CRC32C runtime is unchanged: the native musl packaging patch is prepared/tested upstream work, not installed by this release; the Python-backend warning can still appear.
- Update stable badges, installation instructions and publication checks. See the [promotion evidence and limits](docs/stable-042.md).

## 0.4.2-beta.4 - 2026-09-24

- Add a persistent **Capture sur sonnerie / Ring image capture** switch, **OFF by default**, including upgrades without a saved preference. OFF leaves supported ring events and the five-second binary-sensor pulse intact and schedules no photo. ON retains T+4, one acquisition, no photo retry loop and the shared media session.
- Consolidate the fresh-image backend and add `welcomeeye_local.capture_snapshot` with HA entity permissions and optional response data. Manual captures update a separate `image.<device>_last_snapshot`; automatic captures retain `image.<device>_last_ring`. Failure does not replace a previous successful image with stale data.
- Save captures into the configured private HA Media directory, with local-time names, an opaque device folder, collision protection and atomic publication outside the event loop. No `/config/www`, public URL or surprise retention. A Media failure preserves the in-memory image and exposes a sanitized `save_error`.
- Add a Photo control to the card; all five controls stay on one row, including on narrow cards. Photo preserves an existing live viewer, sound and microphone. Automatic capture remains controlled by its separate HA switch. Distinguish confirmed save, storage failure and permission failure. Photos are stored under **WelcomeEye** in Home Assistant's local Media directory.
- Fix frontend lifecycle defects: pending opens, canceled ICE/HLS waits, stale callbacks, microphone attach/detach races, failed DataChannel cleanup, disconnected controls and fullscreen errors. Keep WebRTC/HLS, background cleanup and single-attempt physical-output behavior; no automatic output replay.
- Mark **V1 local doorbell unsupported** after hardware trials found no usable local ring event despite an authenticated, stable `0/3/0` listener. Disable that unsuccessful listener by default. A cloud path is possible but unproven. Retain the validated V1 media, microphone, strike and gate paths. The tester confirmed physical V1 gate operation on 2026-09-24; this does not claim new beta.4 hardware tests.
- Document the DES9901VDP firmware variant without UDP 1500 as unsupported and separate from this work. CRC32C runtime/packaging is unchanged: no warning suppression, native binary or monkey patch.
- Add Python capture/storage/persistence/lifecycle regressions and dependency-free Node frontend tests. Software results belong to the [beta.4 audit](docs/audit-beta4.md); earlier hardware and historical test counts are identified separately. **Prerelease for testing: new beta.4 physical validation is pending.** No physical output is triggered by automated tests. Stable remains `0.4.1`.
- **2026-09-24 replacement explicitly requested by the maintainer:** replace the already published beta.4 tag and ZIP with this single-row card correction. Existing beta.4 installations must use HACS **Redownload / Retélécharger**, then restart HA and fully reload the frontend. Other published tags remain unchanged.

## 0.4.2-beta.3 - 2026-09-23

### Snapshots expérimentaux

- J'ai ajouté une image automatique après chaque sonnerie reconnue : l'événement de sonnerie reste immédiat, puis une seule acquisition de photo fraîche démarre à partir de T+4 secondes, via la session média partagée.
- La dernière image est disponible dans `image.<device>_last_ring` et annoncée par `welcomeeye_local.ring_image`. Elle reste en mémoire : aucun fichier automatique dans `/config/www`, aucune URL publique. Une erreur de capture ne republie pas une ancienne photo comme nouvelle.
- Deux essais consécutifs au repos sont validés sur Connect 2 : le moniteur conserve sa photo native et HA obtient sa propre nouvelle image. **Les snapshots restent expérimentaux.** Après utilisation puis fermeture de la vidéo HA, une photo native manquante a été observée, y compris avec le code précédent sans snapshot automatique. Ce cas reste à corriger ; les sonneries vidéo ouverte et la validation V1 restent à réaliser.
- La récupération directe de la photo native du moniteur n'est pas résolue : cette bêta prend une nouvelle photo locale. Les snapshots manuels attendent également une nouvelle image sans retourner une ancienne JPEG du cache.

### CRC32C : correctif natif préparé et validé, déploiement upstream nécessaire

- J'ai identifié la cause du warning sur les runtimes HA musl et préparé un correctif du builder Home Assistant pour produire une wheel native, sans réutiliser une wheel Python en cache. Il est validé en CI sur x86_64 et aarch64 avec CRC32C, SCTP/DataChannel, audio et vidéo synthétiques.
- **Ce correctif de packaging n'est pas encore déployé par Home Assistant. Cette bêta ne remplace pas le backend CRC installé : le warning peut donc toujours apparaître.** Le dernier contrôle du HA utilisé pour les essais confirme encore `google-crc32c 1.8.0`, backend Python, extension native absente.
- Ajout de diagnostics sur le backend, la version et l'extension réellement disponibles. Aucun masquage du warning, pin inefficace, binaire embarqué ou monkey patch. Le patch et les propositions upstream sont prêts, mais non soumis.

### Installation et validation

- Prérelease uniquement ; `0.4.1` reste stable. Sélectionner `0.4.2-beta.3` dans HACS, redémarrer HA puis recharger complètement le navigateur ou l'application Companion.
- 71 tests logiciels, compilation Python 3.12/3.14, HACS, Hassfest et API ImageEntity réelle validés pour la candidate. Les commandes physiques restent à envoi unique, sans retry automatique. Aucun changement du protocole d'ouverture, du chiffrement ou de la fermeture des sessions dans cette bêta.

## 0.4.2-beta.2 - 2026-09-16

- Re-enable the existing Connect 2 local doorbell listener on identified WelcomeEye Connect V1 / DES9900VDP devices for field testing. The V1 uses the same authenticated `0/3/0` listener, keepalives and existing `510 -> 14854 / reportAlarm` decoder; no new subscription command, alarm mapping or physical-output packet is introduced.
- Mark V1 local doorbell support as **in development**. Receipt of a real V1 doorbell press is not yet hardware-validated and will be checked on the tester's development Home Assistant instance before any stable promotion.
- Keep the `Sonnette` binary sensor active for five seconds after each distinct decoded ring on both Connect 2 and V1. A second distinct ring restarts the five-second display window; duplicate deliveries remain deduplicated.
- Preserve immediate `welcomeeye_local.ring` events for each distinct ring. If the listener disconnects during an already-received pulse, the visible five-second state is retained until expiry without masking idle listener availability afterward.
- Add privacy-safe trial attributes and 17 targeted offline tests for listener reuse, the five-second pulse, duplicate/non-ring filtering, shutdown and session ownership. Python 3.12/3.14, Hassfest and HACS validation passed on the trial branch before publication.
- No video, microphone/talkback, encryption or physical-output command behavior is changed. Door/gate commands remain single-shot and are never automatically retried.

## 0.4.2-beta.1 - 2026-09-16

- Automatically create the WelcomeEye JavaScript module in Home Assistant dashboard resources and update its version at startup. Load saved resources first so an existing manual entry is reused.
- Keep one resource for the bundled card, correct its module type and remove only duplicates of the same relative WelcomeEye URL. Preserve other cards and external URLs.
- Declare the Lovelace startup dependency. Resource registration failure does not stop the integration; the existing frontend loader remains available. YAML-managed resources are not modified and may still need manual configuration.
- Add isolated resource migration tests, including restart, upgrade, concurrent registration, duplicate cleanup and failure handling. No video, audio, microphone, doorbell or physical-command protocol changes.
- Prerelease; 0.4.1 remains the latest stable version. Restart HA after updating, then fully reload the browser or Companion app frontend.

## 0.4.1 - 2026-09-16

- Promote the tested 0.4.1-beta.4 runtime to stable without changing the device protocol or command behavior.
- Connect V1 live video and physical door strike confirmed by the tester. V1 gate and microphone remain unverified; local V1 doorbell remains disabled. Connect 2 support is retained.
- Includes the V1 startup/JPEG fixes, session cleanup, H264 recovery and WebRTC/HLS fallback improvements from the 0.4.1 prereleases.
- Document mandatory dashboard resource registration: `/welcomeeye_local/welcomeeye-card.js?v=0.4.1`, type JavaScript module, followed by a full frontend reload.
- Validation inherited from beta.4: 198 tests and 3 subtests passed, plus 45 targeted simulated command checks. No automatic output retry.

## 0.4.1-beta.4 - 2026-09-16

- Fix two reproduced V1 video failures: omit the optional stream-mode query that caused remote TCP closure on the tester's device, and isolate JPEG conversion in a reusable single-threaded context before handing frames to WebRTC.
- Hardware check on the tester's V1: three consecutive live sessions with an advancing clock, including over two minutes continuously; clean HA shutdown after each, no H264 errors.
- Add a privacy-safe media-worker stack location to diagnostics. Preserve profile 16/1/2, Start/Stop AV, command payloads, encryption and output mapping.
- Strike/gate commands reviewed and tested with simulated sockets only: original media session, at most one TLV 505 attempt per action, no automatic replay. Physical outputs and V1 microphone remain unverified.
- Validation: 198 tests and 3 subtests passed. Prerelease; 0.4.0 remains the stable Connect 2 release.

## 0.4.1-beta.3 - 2026-09-16

- Recover Connect 2 H264 decoding after an invalid packet using session-local SPS/PPS at the next real IDR, including when that IDR does not repeat the parameters. Keep the invalid-packet warning visible.
- Cancel pending STUN transactions before closing WelcomeEye ICE transports and on transport loss. Scope the fix to this integration's peer connections; add a cancellation counter and SDP candidate type/protocol diagnostics without addresses.
- Fall back to Home Assistant's HLS player when the card's WebRTC connection fails or times out. The microphone is unavailable in fallback mode; explicit strike/gate buttons remain available. Closing the card removes the player; HA controls its HLS source idle timeout.
- No device protocol, profile, encryption or physical-command changes, and no automatic output retry. V1 remains experimental; its remote TCP disconnection is not resolved by this release. Field testing of these fixes is still required.

## 0.4.1-beta.2 - 2026-09-15

- Experimental V1 live-video fix; 0.4.0 remains stable for Connect 2.
- Preserve the same active V1 session across empty two-second receive polls, within the existing ten-second live idle budget. A three-second gap followed by video previously closed the session after two seconds; new decoded frames now resume without reconnecting.
- Distinguish a clean receive timeout from keepalive write failure. EOF, reset, partial-packet failure and prolonged silence still terminate the session. No new connection or output retry.
- Preserve the last session that received video across subsequent discovery failures, with per-session received/decoded frame counters, transport state, V1 receiver rejection counters and relative timing. No private addresses, identifiers or raw payloads are exported.
- Keep V1 profile 16/1/2, H264, codecs, Start/Stop AV, encryption and physical-command builders unchanged. A command queued during the simulated pause is attempted exactly once on its original session.
- The tester's frozen image and reported ConnectionError are not yet physically resolved or explained by this timeout fix. V1 remains experimental; microphone and physical outputs still require field validation.

## 0.4.1-beta.1 - 2026-09-15

- Experimental V1 diagnostic release; 0.4.0 remains the stable release for Connect 2.
- Revoke media availability before waiting for worker shutdown. Reject acquisition of a worker whose stop is already requested; never create a second worker or replay an output command.
- Make repeated release of an absent consumer lease a no-op, preserving the original acquisition failure.
- Continue WebRTC track, media and peer cleanup after a microphone cleanup exception.
- Preserve the initial microphone error separately from cleanup errors. Distinguish transport failure, timeout, device refusal and an explicitly unsupported format in the card message.
- Add payload-free diagnostics for worker stage, stop request, shutdown duration/failure stage, WebRTC cleanup phase, live transport framing and the actual TLV 332 format response.
- Validation: 177 tests and 3 subtests passed. The original hardware stall remains unproven; V1 microphone and physical output operation still require field validation. No profile, H264, encryption or physical-command packet changes; no automatic TLV 505 retry.

## 0.4.0 - 2026-09-15

- Promote 0.4.0-beta.1 to **stable for Philips WelcomeEye Connect 2**, following the owner's confirmation of microphone operation on real hardware.
- Include the bundled intercom card with WebRTC video, speaker, microphone toggle, strike and gate controls. Keep the screenshot and working Connect 2 YAML in the configuration documentation.
- Require HTTPS with a trusted certificate and microphone permission for browser/Companion dashboard microphone access; local HTTP does not provide a secure context.
- **Connect V1 / DES9900VDP remains experimental.** V1 microphone, physical output operation and intermittent connection failures still need field validation; local V1 doorbell remains disabled.
- Release promotion only: runtime changes are limited to version metadata. Media profiles, protocol, encryption, session handling and single-shot output safety are unchanged from the published beta.

## 0.4.0-beta.1 - 2026-09-15

- Add the bundled WelcomeEye intercom card: explicit WebRTC video opening/closing, speaker and microphone toggles, strike/gate buttons using the existing HA button services and permissions.
- Implement the APK LT microphone path: protected 331 start/stop, negotiated 332 response, raw 97/98 G.711 frames on the original media connection. Support confirmed 8 kHz mono A-law / mu-law formats; reject unsupported formats.
- Consume the inbound WebRTC audio track, resample and encode it, serialize socket writes, enforce one microphone owner, and stop on cancellation, session closure, lost control channel or expired heartbeat. Never persist microphone audio.
- Invalidate failed pre-authentication discovery observations on transport failure. Preserve the existing single TCP-refusal recovery; introduce no generic connection or output retry. Make the login receive deadline effective inside partial/padding reads.
- Respect the APK's two-second V1 reopen delay after stopping a session. Preserve profile 16/1/2, H264, downstream audio, ring handling, output mapping, encryption and Start/Stop AV packet builders.
- Expose redacted connection phases, endpoint source, invalidation reason, acquisition timings and microphone counters. Align manifest and diagnostics version.
- Keep RC2's regular HA HLS camera available; the new interactive card uses its own authenticated WebRTC signaling.
- Connect 2 microphone operation confirmed on real hardware by the device owner on 2026-09-15. Add the owner's intercom screenshot and working card YAML to the configuration documentation.
- Experimental prerelease. V1 microphone and output operation and the tester's intermittent discovery failure still require field validation. No physical output was triggered during development.

## 0.3.1-beta.9 - 2026-09-15

- Route the Home Assistant camera frontend through the existing `stream_source()` / Stream-HLS path instead of advertising the integration's native WebRTC handler.
- This transport change follows a real enterprise-Wi-Fi field test: beta 8 media/H.264 stayed healthy while WebRTC remained stuck in ICE `checking` with STUN available and no TURN; disabling native WebRTC immediately made the same WelcomeEye stream work through Home Assistant's HTTP stream path.
- No TURN service, extra container, external relay, firewall change or additional installation is required for this beta.
- Keep the complete native WebRTC implementation in the codebase but dormant so it can be re-enabled later when an automatic, proven transport-selection path is available. Beta 9 intentionally favors compatibility over lowest-latency viewing.
- Keep the existing loopback MPEG-TS proxy, bounded buffers, media lifecycle and `http_stream_release` cleanup unchanged. Initial HLS buffering can take a few seconds before playback stabilizes.
- Add diagnostics identifying `frontend_transport=home_assistant_stream` and `native_webrtc_advertised=false` so field reports clearly distinguish beta 9 from the beta 8 ICE path.
- Keep Connect 2 media/control/doorbell behavior and every V1 protocol, output-safety, 5009/5005 teardown, H.264 recovery and doorbell-standby rule unchanged.
- Add beta 9 regression checks for the disabled native-WebRTC advertisement, retained Stream source/MPEG-TS lifecycle and manifest/diagnostics version alignment.

## 0.3.1-beta.8 - 2026-09-15

- Close PyAV containers and HTTP writers in finally blocks; make pipeline close idempotent. Continue shutdown cleanup after failures and report them, including partial startup cleanup.
- Guard WebRTC negotiation and callbacks against removed/replaced viewers; retain cleanup tasks across cancellation, await timeout/offer tasks on unload, bound PC close, and remove listeners.
- Preserve uncertain physical-output messages across the Home Assistant executor. One 505 attempt per accepted action, no replay/reconnect of an output, and the three-second cooldown remain unchanged.
- Add worker exit stage/reason, pending-output-at-exit, Stop AV/session stop/TCP state, retained codec counters, and separate WebRTC/ICE/STUN/TURN diagnostics. Arbitrary exception text is excluded from downloads.
- Add the native zero-payload session-stop TLV 5005 to authenticated V1 media teardown, after existing 5009 and before TCP close. Native analysis did not demonstrate a mandatory 5010 wait. Physical release/busy resolution remains unverified.
- Recover V1 H264 after decoder reset when the next IDR omits SPS/PPS, using bounded cached parameters. Normal Connect 2 decoding and all media framing remain unchanged.
- Test the actual aioice mDNS SRV packet path after the existing beta7 preload; keep dynamic DNS loading enabled. Add Python 3.12/3.14 compilation, real aiortc/PyAV, lifecycle, delayed-reply and stress coverage.
- V1 local doorbell remains standby; no talkback/UI development or deployment is included.
- Final validation: **135 tests plus 3 subtests** pass on Python **3.12.14** and **3.14.7**; compilation, HACS and Hassfest are green. Physical V1 relay/busy-state checks and real TURN relay traversal remain pending.

## 0.3.1-beta.7 - 2026-09-14

- Preload dnspython handlers for the exact mDNS cache-flush class (`IN | 0x8000`, class 32769) in Home Assistant's executor before WebRTC platform setup, preventing the `CLASS32769.SRV` / `ANY.SRV` dynamic-import warnings observed on Home Assistant 2026.9.x / Python 3.14.
- Keep dnspython dynamic loading enabled globally; beta 7 warms the exact classes instead of disabling runtime loading as a process-wide workaround.
- Treat PyAV `InvalidDataError` from an H.264 access unit as recoverable media corruption: drop that access unit, recreate only the H.264 decoder, discard dependent P frames, and resume from a later V1 I/key frame without closing the TCP/media session.
- Do not hide unrelated failures: unexpected decoder/application exceptions still propagate normally.
- Preserve the beta 5/6 V1 output architecture and safety rules. TLV 505 remains single-shot on the existing `16/1/2` media session; decoder recovery does not open a second reader, transfer a pending command, or send another output request.
- While a sent TLV 505 is waiting for TLV 506, treat only a completely clean V1 OWSP-header timeout as idle time on that exact authenticated session. The guard is disabled as soon as the command resolves or fails; normal V1 and Connect 2 timeout behavior is unchanged outside that window.
- Add tester-specific regressions in which one TLV 505 is followed by either an H.264 decoder failure or a clean media-idle interval, then a later TLV 506 is accepted on the same session with no second TLV 505.
- Add an actual V1 hub-worker regression with real PyAV decoding: after the command send, inject a decoder failure, recover from a fresh keyframe and accept a later TLV 506 without replacing the session.
- Keep beta 6 stale-discovery recovery unchanged. If the original session genuinely closes after TLV 505 was sent, the command remains uncertain and is never replayed on a replacement session.
- Validate the final candidate with **101 tests plus 3 subtests** on both Python **3.12** and **3.14**, using PyAV 17.0.1 and dnspython 2.8.0. HACS and Hassfest are green on the stability branch.
- Connect 2 behavior and the V1 doorbell standby state remain unchanged. Physical V1 strike/gate actuation and post-command video continuity still require real-hardware confirmation.
- See `docs/v1-stability-beta7.md` for the evidence, test matrix and hardware validation procedure.

## 0.3.1-beta.6 - 2026-09-14

- Refresh a stale cached discovery endpoint once when the advertised WelcomeEye TCP port actively refuses a connection. The cache is invalidated, discovery is repeated once, and the TCP connection is retried before login.
- Keep physical output safety unchanged: the discovery/TCP recovery happens before login and before any TLV 505 output packet can be built or sent. Door-strike and gate commands remain single-shot and are never automatically retried.
- Add privacy-safe counters for discovery cache invalidations and connection-refused rediscoveries.
- Preload dnspython record handlers through Home Assistant's executor before platform setup so WebRTC/mDNS does not dynamically import DNS record classes on the event loop.
- Align downloadable diagnostics and the manifest on `0.3.1-beta.6`.
- Keep the hardware-validated V1 video path, beta 5 live-channel `16/1/2` output routing, Connect 2 behavior and the V1 doorbell standby state unchanged.
- Continue the beta 6 V1 local-doorbell investigation in `docs/v1-doorbell-beta6.md`; the presence of TLV 510/OWSP parsing support in the LT stack does not yet demonstrate a complete local V1 doorbell path.
- Add offline regression coverage for stale-discovery recovery, bounded retry behavior and startup DNS preloading. HACS and Hassfest validations are green on the beta 6 branch.

## 0.3.1-beta.5 - 2026-09-14

- Match the official protected V1 output path: the encrypted TLV 505 request is now sent on the active **16/1/2 live media channel** instead of a dedicated `0/3/0` control session.
- Route V1 output 0 (door strike) and output 1 (gate) through the existing media worker. Reuse active viewers, borrow the normal media lifecycle when no viewer is present, and keep the existing Stop AV cleanup.
- Keep the media worker as the only reader/writer for its socket; TLV 506 confirmation is observed by that worker rather than by a second socket reader.
- Keep one physical send attempt per accepted action, the three-second cooldown, and no automatic retry after timeout, partial send, decode failure or uncertain confirmation. A command is never transferred to a reconnected session.
- Treat a positive TLV 506 acknowledgement as protocol confirmation only, not proof that the physical relay operated.
- Keep Connect 2 command routing and the hardware-validated V1 video path unchanged.
- Keep V1 doorbell detection on standby. The official LT application exposes a cloud push-registration path, but no reliable local V1 subscription sequence is shipped in this release. Connect 2 local alarm handling remains unchanged.
- Keep diagnostics compact and privacy-safe; no credentials, device identifiers, private addresses, raw packets, cloud push tokens, media payloads or alarm payloads are exported.
- Validate the final candidate with **86 tests plus 3 subtests**, including the actual V1 media worker with real PyAV decoding while a single output request is issued on the same simulated session. HACS and Hassfest are green on final candidate commit `a5fe208`.
- Physical V1 strike/gate actuation, video continuity during the command and hand-back to the official application remain to be confirmed on real hardware.
- See `docs/v1-control-events-beta5.md` for the technical evidence and remaining manual checks.

## 0.3.1-beta.2 - 2026-09-13

Connect V1 fixes derived directly from the first beta 1 hardware diagnostics.

- Accept the real V1 terminal-video framing observed on hardware: TLV 99 may use a 12-byte metadata record and terminal TLV 100/101 may declare a short length (observed value: 1) while the complete Annex-B H.264 image occupies the remainder of the already-complete OWSP packet.
- Consume that terminal OWSP remainder only when the same packet first contains a recognized 12- or 16-byte V1 video metadata record, the remaining payload is within the 1-MiB media bound and begins as Annex-B H.264. No arbitrary prefix/header bytes are stripped.
- Keep the 16-byte metadata sequence/size validation when available; treat the observed 12-byte metadata form as opaque rather than inventing undocumented fields.
- Keep fragment TLVs 103/106/107/108 explicitly unsupported and discarded; no speculative reassembly was added.
- Add real-PyAV regression coverage reproducing the hardware shape (12-byte TLV 99 + short terminal TLV 100/101 + Annex-B OWSP remainder) and verify the complete I/P sequence reaches JPEG decoding.
- For V1 output control, keep the existing ring-listener pause/release barrier and add a bounded 1-second hardware-settle interval after confirmed listener release before opening the one-shot control session.
- The settle interval is not a retry: the door-strike/gate request is still sent at most once per accepted action. If the control session cannot be opened, `request_send_attempt_count` and `request_sent_count` remain zero.
- Add privacy-safe diagnostics for the V1 settle wait count/requested/elapsed duration and correct the internal diagnostic version to `0.3.1-beta.2`.
- Connect 2 media bytes, output behavior and the shared Home Assistant WebRTC path remain unchanged.
- Microphone / two-way audio remains **not validated** on hardware.
- Validate with **76 tests plus 3 subtests**, including real PyAV JPEG decoding, V1 transport/routing, V1 ring/control coordination and Connect 2 regressions. HACS and Hassfest validations are green.
- Physical validation on the real V1 is still required before considering the video and output-session fixes confirmed.

## 0.3.1-beta.1 - 2026-09-13

Connect V1 coordination, bounded OWSP reception and complete-video routing candidate.

- Serialize ring-session publication and control pauses: an output session can start only after the ring worker has closed and abandoned its session. An already disconnected listener yields immediately.
- Resume the listener from command cleanup after success, send/confirmation failures or pause timeout; keep the command mutex releasable even if diagnostics or cleanup fails.
- Prevent duplicate ring workers and stale connected-state callbacks during intentional pauses. Refused authentication and UID mismatches remain terminal until integration reload.
- Fail safely if socket release cannot be confirmed: do not send an output command or reconnect alongside an uncertain control session.
- Add count/boolean diagnostics for pause, release, resume, actual reconnection, active sessions and keepalives. Count attempted sends separately from completed sends.
- Retain beta 13 zero-padding/idle-timeout handling, beta 14 discovery caching and V1 Start/Stop AV.
- Add V1-only structural video diagnostics before TLV parsing, including incomplete OWSP reads, lengths, bounded H.264 signature positions and native terminal-boundary comparisons. No media payloads, raw timestamps or identifiers are exported.
- Do not introduce a speculative TLV 97 video parser: the examined native SDK uses 97 as eight-byte audio metadata. Connect 2 video decoding and output session selection remain unchanged.
- On authenticated V1 media sessions only, retain partial OWSP bytes across intermediate socket timeouts, maintain keepalives, and enforce 6-second inactivity / 20-second total / 1-MiB bounds. Never parse incomplete payloads or reuse a reader after a failed partial read.
- Route V1 video metadata separately from complete 100/101 images. Match the native terminal OWSP boundary only when same-packet metadata corroborates its size; require fresh metadata and exact image size before passing unchanged Annex-B bytes to the existing pipeline.
- Preserve native I/P flags and sequence handling; do not promote audio metadata, unknown TLVs or isolated video fragments to H.264. Fragment reassembly remains unsupported pending sufficient validation evidence.
- Validate with 71 offline tests, including the original 38, actual PyAV decoding to JPEG through the V1 worker, slow reception, byte-identical Connect 2 video/audio inputs, and malformed/incomplete data. See `docs/beta15-v1-video-fix.md` for evidence, limits and the pending hardware protocol.
- Physical output commands are still sent at most once per accepted user action, with the existing three-second cooldown. Hardware validation is pending; nothing is deployed by these tests.

## 0.3.0-beta.14 - 2026-09-13

Connect V1 output-control priority over the persistent doorbell listener.

- The V1 doorbell listener now temporarily releases its authenticated `0/3/0` control session before an explicit door/gate command.
- Output control waits for the listener to acknowledge that it has yielded the channel, opens its one-shot control session, sends the requested unlock packet exactly once, then closes that session before the doorbell listener reconnects.
- The persistent listener reconnects automatically after control completes, including when the control attempt fails before sending a command.
- Added privacy-safe diagnostics for control-triggered listener pauses and pause timeouts.
- No unlock or gate command is automatically retried.
- Video transport/decoding is unchanged from beta 13; beta 12 Stop AV and discovery caching are retained.

## 0.3.0-beta.13 - 2026-09-13

Connect V1 doorbell listener stability fix.

- Keep the authenticated V1 doorbell control session alive when `Session.read()` times out after consuming native zero-length OWSP padding frames.
- Treat those zero-padding timeouts as idle activity instead of a broken connection, so the listener can reach its normal keepalive cycle instead of reconnecting every few seconds.
- Preserve the existing 35-second liveness guard for a genuinely silent/dead connection.
- Added privacy-safe diagnostics for listener timeouts and timeouts that followed zero-padding activity.
- Retains beta 12 Stop AV/discovery-cache behavior and all existing no-retry safety rules for unlock/gate commands.

## 0.3.0-beta.12 - 2026-09-13

Graceful Connect V1 media teardown and discovery reuse.

- Reverse-engineered the native `stopGetVideoStream()` path in `libglnkio.so` and added protected **Stop AV** TLV **5009** / response **5010**.
- Any V1 media session that successfully sent Start AV now sends Stop AV before its TCP connection is closed, including failed WebRTC/media startups. This prevents the V1 from remaining busy after a failed preview.
- Media shutdown now wakes a blocked reader without destroying the socket write side, giving the worker a chance to send Stop AV cleanly.
- Successful UDP discovery results are cached for the Home Assistant process and shared by media, doorbell and output-control sessions. Ring/media/control workers no longer rediscover the same configured device for every connection.
- Discovery is serialized so concurrent workers cannot send overlapping UDP discovery bursts to a fragile V1.
- Added privacy-safe Stop AV and discovery cache/request diagnostics.
- No unlock/gate command is retried automatically.

## 0.3.0-beta.10 - 2026-09-13

Native Connect V1 / LT Start AV implementation.

- Reverse-engineered `libglnkio.so` `DataChannelIOCtrl::startGetVideoStream()` and implemented the protected native **Start AV** request TLV **5007** and response TLV **5008**.
- The V1 Start AV request now reproduces the SDK's 60-byte protected payload: three LT profile nonces, AES-128-CFB encryption of device time plus `channel/stream/mode/reserved`, RC4 with the device UID, and OWSP framing.
- Added validation and decoding of the protected 5008 response, including the native device-time/result fields and LT nonce checks.
- Identified Connect V1 devices now send Start AV on the APK-confirmed `16/1/2` live profile before waiting for video, while retaining the stream-mode query used by the Java LT player.
- Removed the beta 9 preview-time I-frame request: APK analysis shows `needIFrame` belongs to video recording rather than normal live preview.
- Added privacy-safe diagnostics for Start AV request/response counts, decode failures and result code. Raw protocol/media payloads remain excluded.
- Connect 2 media handling and beta 8 Home Assistant ICE/STUN/TURN WebRTC behavior are unchanged.

## 0.3.0-beta.9 - 2026-09-12

Targeted Connect V1 / LT protocol compatibility pass.

- Re-analyzed the supplied APK's dedicated `QvLtPlayerCore` path and confirmed the legacy live profile is logical channel 1 mapped to wire channel 16, stream 1, mode 2.
- Reproduced the APK's immediate post-authorization `sendManuData(initTCGetBitStrMode())` request (`01 04 03 00`) for identified V1 devices.
- Added one media-only I-frame request (`01 04 0B 00`) after a V1 announces its 352×288 H.264 format. No door/gate command is sent or retried.
- Once a V1 is recognized, stop cycling speculative media profiles and keep the APK-confirmed 16/1/2 path for a longer startup window.
- Inspect every non-audio top-level TLV for structural H.264 and allow an unknown V1 TLV to become video only when it contains valid H.264 framing.
- Added privacy-safe counters for every top-level media TLV and all H.264-bearing TLV IDs.
- Added V1 manufacturer-query diagnostics including response command/subcommand and stream-mode mapping without retaining manufacturer payloads.
- Added transport framing diagnostics for media and doorbell sessions: read/keepalive counts, invalid big-/little-endian length interpretations and whether the invalid frame followed a keepalive. Raw frame bytes are never retained or exported.

## 0.3.0-beta.8 - 2026-09-12

Remote/mobile WebRTC connectivity fix.

- Fixed server-side WebRTC explicitly disabling all ICE servers with `iceServers=[]`, which could leave remote iPhone/Safari viewers stuck in `connecting` while LAN viewers worked.
- WebRTC now reuses Home Assistant's current `web_rtc` ICE configuration for every viewer, including the default Home Assistant STUN servers and any user- or integration-provided TURN relay.
- ICE server information is refreshed for every new viewer so short-lived relay credentials can be used without being cached by the integration.
- Increased the unconnected viewer grace period from 25 to 45 seconds and the initial WebRTC negotiation timeout from 22 to 30 seconds for mobile networks.
- Added privacy-safe ICE diagnostics: ICE connection/gathering/signaling state, STUN/TURN availability, and local/remote candidate types and protocols.
- ICE candidate addresses, ports, server URLs and TURN credentials are never included in downloadable diagnostics.
- WelcomeEye media decoding, output controls, doorbell support and beta 7 V1 compatibility logic are unchanged.

## 0.3.0-beta.7 - 2026-09-12

Legacy media, doorbell and device-model compatibility update.

- Added privacy-safe H.264 detection on media TLVs 97/99/100/101, including Annex-B and AVCC framing diagnostics.
- Legacy TLVs 97/99 can now be promoted to video when their payload is structurally identified as H.264; Connect 2 TLVs 100/101 remain supported unchanged.
- Added diagnostics for H.264 NAL types, IDR detection, framing, TLV 97/99 counters and the last announced stream format.
- Doorbell listener now tolerates legacy media TLVs instead of disconnecting.
- Added vendor-confirmed ring/call alarm types 7 (doorbell), 19 (call) and 47 (call from), while retaining type 14 observed on Connect 2.
- Doorbell diagnostics now include exact safe error message/stage, top-level and inner TLV counters, alarm-type counters and decode failures.
- Added automatic device-model recognition: 352×288 legacy media is identified as **WelcomeEye Connect V1** and the validated 720×576 signature as **WelcomeEye Connect 2**. The detected model is persisted and updates Home Assistant's device registry.
- Media payloads, alarm payloads, credentials, UID, private IP, SDP and ICE candidate values remain excluded from diagnostics.

## 0.3.0-beta.6 - 2026-09-12

Doorbell support and experimental multi-profile media compatibility update.

- Added local doorbell/ring detection through a dedicated control-session listener.
- Added the **Sonnette** binary sensor and `welcomeeye_local.ring` Home Assistant event.
- Added automatic media-profile probing for devices that return TLV 203 but no usable video TLV 100/101.
- Added compatibility profiles derived from the WelcomeEye/Qv SDK while keeping the validated Connect 2 profile first.
- A working media profile is preferred for subsequent sessions.
- Added `selected_media_profile`, current profile, profile attempts and video packet counts to downloadable diagnostics.
- Added safe protocol error details to media diagnostics and output-control errors.
- Media-profile probing never sends door/gate output commands.
- Intended to test video support on WelcomeEye Connect V1 / DES9900VDP.

## 0.3.0-beta.5 - 2026-09-11

WebRTC and media diagnostics update for compatibility testing.

- Added privacy-safe WebRTC stage tracking from offer receipt through answer delivery and peer connection state changes.
- Added the WebRTC failure stage and exception type to downloadable diagnostics.
- Added requested and created media-track summaries without storing SDP or ICE candidate values.
- Added counters for WelcomeEye media TLVs 203, 98, 100 and 101.
- Intended to help diagnose WelcomeEye Connect V1 compatibility where output control works but live video fails.

## 0.3.0-beta.4 - 2026-09-11

Support diagnostics update.

- Expanded downloadable diagnostics with runtime media, worker/session, stream format, buffer and control state.
- Diagnostics explicitly omit private connection data and media payloads.

## 0.3.0-beta.3 - 2026-09-11

Branding update.

- Added Philips WelcomeEye app-style branding assets and README logo.

## 0.3.0-beta.2 - 2026-09-11

Second public beta with local output controls.

- Added **Ouvrir la gâche** (output 1) and **Ouvrir le portail** (output 2).
- Added temporary media-session warmup before output commands.
- Output commands are sent exactly once and never automatically retried.

## 0.2.0-beta.1 - 2026-09-10

First public beta.

- Direct local connection to Philips WelcomeEye Connect 2.
- On-demand H.264 video and G.711 A-law audio handling.
- Home Assistant camera streaming and native WebRTC viewing.
- Configuration and reauthentication through the Home Assistant UI.
