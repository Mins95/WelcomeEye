# 0.4.8-beta.1 — Network recovery

This prerelease improves recovery and diagnostics. Existing entries, camera IDs,
passwords, TLS approvals, opening permissions and dashboard settings are retained.
Stable **0.4.7** remains available.

## Changes

### Cloud doorbells: Connect 3 and Connect 2 R002

Temporary network failures no longer permanently exhaust the three quick
connection attempts. Subsequent attempts become progressively less frequent.
Each individual connection remains bounded and unload cancels pending retries.
Disabling cloud stops recovery; the feature never enrolls an unconfigured account.

Rejected credentials, invalid configuration, TLS verification failures and
malformed protocol responses require intervention. They are not treated as an
ordinary Internet outage. Existing notification filtering and deduplication
remain in force: reconnecting never creates a synthetic ring or replays an
opening command.

### Local connections: protected Connect V1 and Connect 2 R001

The integration privately remembers a successfully authenticated endpoint, so
a Home Assistant restart does not always depend on a new UDP discovery response.
The saved record is a connection hint, not a new trust approval. It must match
the configured device and connection settings. Every new session still performs
the existing login and protection checks.

Invalid, changed or expired records are ignored. A failed transport hint can
trigger one bounded discovery refresh before login. An authentication refusal
never triggers automatic credential retries. Unprotected older V1 connections
do not gain persistent endpoint reuse.

### Troubleshooting

Discovery diagnostics distinguish requests that could not be sent, no received
response, unrelated datagrams and malformed/rejected device replies. Stored
endpoints, passwords, device IDs and raw network messages are excluded from
diagnostic exports.

## Install and test

1. Enable prereleases for Philips WelcomeEye in HACS/Marketplace, select
   **v0.4.8-beta.1**, install and restart Home Assistant. Keep the existing entry.
2. On V1/R001, open and close the camera once to establish a successful endpoint,
   restart HA, and check another normal video opening. Confirm Philips regains
   control after closing HA video.
3. On Connect 3/R002 with cloud already enabled, check **Cloud connection** and
   **Doorbell subscription**. If a network outage occurs, allow the scheduled
   recovery to complete; do not repeatedly reload the entry.
4. Once reconnected, press one outdoor bell and download diagnostics whether or
   not it is detected. For two panels, test them separately.

No new physical opening test is required. Keep automatic photos disabled while
checking ring reception: acquiring media for a photo can still interrupt the
monitor's call. Capture timing and that known limitation are unchanged.

## Validation and limits

Automated checks use simulated peers and temporary storage, never a real
intercom. The release requires successful Python/frontend tests, actual HA
runtime checks with mocked networking, HACS, Hassfest and package validation.
The published release links the exact successful CI run. Recovery on the
user's network still needs confirmation; tests are not a hardware attestation.

This version adds neither badge-passage identification nor an alternative UDT
media transport. Existing video, sound, microphone and physical routing are
preserved.

## Rollback

Select **v0.4.7** in HACS/Marketplace and restart HA. Keep the existing entries and
credentials. The older version ignores the new private discovery hint; it does
not need a storage migration or deletion of saved photos.
