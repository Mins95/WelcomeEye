# WelcomeEye Connect V1 — beta 6 investigation

Status: development branch for `0.3.1-beta.6`. This document separates demonstrated behavior from hypotheses. The V1 doorbell remains disabled / on standby until a complete local event path is demonstrated.

## PROUVÉ

### Connect 2 local doorbell path

The validated Connect 2 path uses an authenticated local session on `0/3/0`. No explicit alarm-subscription command is required after login in the current implementation. The listener receives top-level TLV 510, opens the contained OWSP payload and handles inner `14854/reportAlarm` messages. Keepalives maintain the session.

### V1 local protocol capabilities

Reverse engineering of the LT SDK shows that its native protocol stack can decode top-level TLV 510 and parse the OWSP envelope carried inside it. This establishes parser capability only; it does **not** establish that a V1 WelcomeEye actually emits doorbell events through this path in the relevant runtime state.

In the examined LT player path, an inner `14854` reaches a generic `onIOCtrl` callback. The player code examined so far explicitly handles the unlock response `426`; a complete local V1 doorbell chain from device event to application callback has not yet been demonstrated.

The previous V1 persistent listener on `0/3/0` authenticated and stayed alive but only observed top-level TLVs such as 40, 57, 70 and 502. No reliable TLV 510 / `14854` ring event was observed on that session. Changing only an alarm-type constant is therefore not a demonstrated solution.

The official LT application also contains a cloud push registration and reception path using FCM. That proves a cloud notification path exists, but it does **not** prove that every V1 firmware lacks a parallel local mechanism.

### V1 video and output control retained from beta 5

The V1 video path remains the hardware-validated `16/1/2` live-media path. Beta 5 output control is preserved: encrypted TLV 505 is sent by the existing media worker on the active `16/1/2` session, with one physical send attempt per accepted user action and no automatic retry after an uncertain send or confirmation.

A positive TLV 506 remains protocol acknowledgement only and is not treated as proof that the physical relay moved.

## HYPOTHÈSES À TESTER

1. A V1 local doorbell event may ride the active `16/1/2` media session rather than the old dedicated `0/3/0` listener.
2. A different V1 channel / stream / mode may carry local events.
3. The official application may send a local activation or registration command after login or preview start before events are delivered.
4. FCM may be used for inactive / remote notification while an active local P2P session still exposes a private-data or generic callback.
5. A callback outside `QvLtPlayerCore` may consume generic local `onIOCtrl` / private-data events and translate them into UI or alarm events.

None of these hypotheses is considered implemented behavior until the complete device-to-callback chain is demonstrated.

## IMPASSES / NEGATIVE EVIDENCE

- Re-enabling the old V1 `0/3/0` listener without new evidence is not justified.
- The presence of a TLV 510 parser in the SDK is not evidence that the V1 emits TLV 510 on the old listener session.
- The cloud FCM path is not evidence that the V1 is cloud-only.
- Alarm type remapping alone is insufficient because the missing part is currently the demonstrated transport / callback path, not the label assigned after receipt.

## BETA 6 COMPATIBILITY FIXES

### Stale discovery endpoint recovery

A beta 5 hardware diagnostic showed a successful V1 media session followed by later media and strike attempts failing at TCP connect with `ConnectionRefusedError`, while discovery data had been reused from the in-process cache.

Beta 6 therefore keeps discovery caching but treats an actively refused cached TCP endpoint as stale. Before login, `Session.connect()` may perform **one** recovery cycle:

1. the refused cached discovery entry is invalidated;
2. one fresh UDP discovery is performed;
3. TCP connect is attempted once against the refreshed endpoint.

A second refusal is returned as an error. Other connection failures are not broadened into retry loops.

This recovery happens before authenticated login and before any physical TLV 505 output packet can be built or sent. It does not weaken the output safety invariant: one accepted strike/gate action still permits at most one physical send attempt, and a sent or uncertain command is never replayed.

Privacy-safe discovery diagnostics add `cache_invalidations` and `connection_refused_rediscoveries` counters. No IP address, port, UID or raw discovery packet is exported.

### dnspython / WebRTC preload

Before Home Assistant loads platforms that may create an `RTCPeerConnection`, beta 6 preloads dnspython record implementations through `hass.async_add_executor_job()` using:

```python
dns.rdata.load_all_types(disable_dynamic_load=False)
```

The import and dynamic type loading run in Home Assistant's executor rather than as part of WebRTC work on the event loop. Dynamic loading remains enabled for future record types.

## NEXT DOORBELL RESEARCH

Continue tracing all local LT callback consumers, not only `reportAlarm` string references. Priority targets include `onIOCtrl`, private-data callbacks, native/JNI callback registration, `GlnkChannel`, `DataChannel`, LT managers/services and any switch handling `510`, `14854`, alarm types 14/19/47 or related call/ring events.

If a V1 local event is demonstrated on the active media session, the existing media worker must remain the only socket reader. No second `recv()` thread may be introduced on that session.

If a dedicated session is demonstrated, use the official channel/stream/mode evidence rather than restoring `0/3/0` by assumption.

## VALIDATION RULES

- Connect 2 local doorbell behavior must remain unchanged.
- V1 doorbell remains standby until a local path is demonstrated.
- No false ring event may be generated from keepalive or known non-ring TLVs such as 40, 57, 70 or 502.
- Physical strike/gate commands remain single-shot and are never automatically retried.
- TCP rediscovery recovery is permitted only before login / physical output transmission.
- V1 video continuity and physical output behavior still require hardware confirmation on the beta 6 candidate.
