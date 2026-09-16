# V1 doorbell trial: reuse the Connect 2 listener

**Experimental branch only; not a published release or a claim of V1 doorbell support.**
Base: `main` at `28ccc50537bc544504f979749c2013e5415b9ec8`, based on the
`0.4.2-beta.1` runtime. Existing releases, tags and the development HA are untouched.
The manifest version is deliberately unchanged until a separate release is requested.
Before publishing, assign a new version and add its CHANGELOG entry; never replace
an existing tag or ZIP with this experimental code.

## Scope

The V1 now keeps the same `RingListener` constructed by `WelcomeEyeHub` as the
Connect 2, instead of replacing it with the no-network standby listener.
`ring.py` is unchanged: one dedicated authenticated `0/3/0` connection,
existing keepalives and decoder `510 -> inner OWSP -> 14854 / reportAlarm`.
There is no new subscription command, channel variant or guessed alarm mapping.
An authenticated listener is not evidence that a physical V1 ring is delivered.

The media session remains `16/1/2`, with its own existing reader. This trial
intentionally tries the Connect 2 coexistence model: no new automatic pause or
socket-sharing mechanism is added. Coexistence of those two connections on the
V1 is a field-test question, not a validated property of this candidate.
No microphone, video, encryption or physical-output code is changed. The listener
cannot trigger a door/gate action. Physical output commands retain their
single-attempt behavior; no test automatically exercises an output.

## Five-second sensor pulse on both models

A decoded, distinct ring sets `Sonnette` to `on` immediately, then returns it to
`off` five seconds after the most recent distinct ring. Repeated delivery of the
same alarm is still deduplicated and does not prolong the pulse. Every distinct
ring still emits the existing `welcomeeye_local.ring` event immediately.
A state-triggered automation sees one `off -> on` transition during an extended
pulse; use the existing event when every distinct appui must be handled separately.

If the listener disconnects during an already received pulse, that pulse remains
visible for its remaining duration. At expiry, a disconnected listener makes the
sensor unavailable; it does not falsely report connectivity. Integration shutdown
cancels the pulse immediately. No persistent timer or blocking sleep is used.

Safe sensor attributes identify the trial: `ring_hold_seconds: 5.0`, and on V1,
`listener_mode: experimental_connect2_path_on_v1`.

## Manual development-HA test order

1. With the owner's approval, install this branch on the tester's development HA
   and restart it. Check the two sensor attributes above. Keep the video and the
   official app closed initially. Download an idle diagnostic.
2. Press the physical doorbell a few times, separated by more than five seconds.
   Check `Sonnette`, `welcomeeye_local.ring`, and the diagnostic `doorbell` counters.
   Then try two distinct presses less than five seconds apart: a detected second
   ring should extend the visible pulse without an intermediate `off`.
3. Only after the idle observation, test one video opening/closing cycle, then
   microphone use, and compare the ring/media connection states and cleanup.
   Stop the trial on new busy, reconnect-loop or media-loss symptoms. Do not
   launch a competing probe while this listener owns its connection.
4. Test the strike separately only on an explicit user request and with an
   observer at the device. Never retry an uncertain physical command automatically.
5. If no ring reaches HA, compare `connection_attempts`, `top_level_tlv_counts`,
   `inner_tlv_counts`, `alarm_type_counts`, `decode_failures` and `transport_framing`
   before/after physical presses. Codex can then instrument the existing reader
   or run an isolated bounded probe after stopping the integration's listener.
   Listening only to HA events cannot reveal packets the decoder does not emit.

Do not export IPs, credentials, UID, raw packets, timestamps from alarm payloads,
SDP/ICE values or recorded media. Keepalives and login messages are not rings.
To roll back, reinstall the previously used published version and restart HA.

## Targeted offline validation

Run `python tests/test_doorbell_trial.py .` from the checkout root.
The 17 targeted tests use the actual source ASTs with simulated HA/socket and
private-decryption/TLV boundaries. They cover setup on both models, unchanged
listener profile, pulse expiry/retrigger, duplicate/non-ring/malformed messages,
disconnection visibility, listener/session ownership and shutdown cancellation.
They do not test the full crypto/media stack or demonstrate hardware coexistence.
The pre-existing full runtime regression suite has not been rerun here.
