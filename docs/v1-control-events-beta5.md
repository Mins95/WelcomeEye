# WelcomeEye Connect V1 — beta 5 technical note

Release: **v0.3.1-beta.5**

Base: `v0.3.1-beta.4`  
Implementation commit: `d06f5aa85f9d1b39098934b7ecde3cd1ff356887`  
Final candidate commit: `a5fe208ffe2c0808161176be766d65a07088c54a`

This document is the current technical reference for the beta 5 V1 changes. The V1 video path was already validated on real hardware and is intentionally preserved.

## V1 output-control finding

The official LT application performs its protected output request on the active preview channel. For the V1 path examined here, that session maps to **channel 16 / stream 1 / mode 2**. Beta 4 instead used a dedicated `0/3/0` session.

The request format already matched the existing TLV 505 implementation. Beta 5 therefore changes the **session context**, not the request format.

A positive TLV 506 reply is treated as protocol acknowledgement only; physical relay movement remains a separate hardware observation.

## Beta 5 implementation

`v1_control.py` routes V1 requests through the existing media lifecycle. It reuses the current `16/1/2` session when one exists, or borrows the normal temporary media lifecycle when needed. The media worker remains the only reader/writer for its socket and observes the corresponding TLV 506 in its normal receive loop.

The request is bound to the session on which it was accepted. It is not moved to a replacement session after reconnect.

Safety properties are intentionally strict: one accepted action permits one send attempt, the three-second cooldown remains, failures before send produce no request, and timeout, partial send, invalid confirmation or uncertain state never cause an automatic retry.

Connect 2 keeps its existing output-control path unchanged.

## Video coexistence

The V1 Start AV/Stop AV path, framing logic and decoder are unchanged. The regression suite executes the real hub worker with real PyAV decoding while one V1 control request is issued on the same simulated media session. Video continues to decode and the request is emitted only once.

## V1 doorbell status

The official LT application contains a cloud push-registration path, but no reliable local V1 subscription sequence has yet been demonstrated.

The previous local V1 listener on `0/3/0` authenticated and remained alive, but hardware diagnostics showed only top-level TLVs `40`, `57`, `70` and `502`. It did not receive the local alarm path used by Connect 2.

Beta 5 therefore keeps the V1 persistent doorbell listener on standby. This does **not** prove that every V1 firmware is cloud-only; it means the local path is not sufficiently established to ship.

Connect 2 local doorbell handling remains unchanged through its existing `TLV 510 → OWSP → 14854/reportAlarm` path.

The Home Assistant integration itself does not register with the vendor push service and remains local at runtime.

## Validation

The final beta 5 candidate passes:

- **86 pytest tests + 3 subtests**;
- HACS validation;
- Home Assistant Hassfest validation.

Final candidate validation run: `https://github.com/Mins95/WelcomeEye/actions/runs/34806187727`

Coverage includes both V1 outputs, one-send behavior, zero sends before validation, session identity/profile checks, timeout without resend, send failures, invalid/negative confirmations, concurrency, cooldown, shutdown, reconnection isolation, stale-confirmation rejection, cleanup behavior, actual-media-worker/PyAV coexistence, Connect 2 regressions and V1 standby doorbell behavior.

## Remaining hardware validation

The software path is validated, but V1 strike/gate actuation still requires real-hardware confirmation. Video continuity during the command and clean hand-back to the official application after the media session closes also remain to be confirmed.

## Privacy

Diagnostics remain compact and exclude credentials, device identifiers, private addresses, raw control data, cloud notification identifiers, raw media/alarm payloads, SDP, ICE candidate values and TURN credentials.

For historical V1 research notes, see [Documentation index](README.md).
