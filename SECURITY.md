# Security

Please do not publish device passwords, device identifiers, packet captures, or
private network details in public issues.

If you believe you have found a security issue, contact the repository owner
privately through GitHub rather than opening a public issue containing secrets.

The integration stores device credentials in the Home Assistant config entry
and uses them to authenticate directly to the device on the local network.
Video, audio, microphone and strike/gate controls use local protocols.

V1 cloud doorbell notifications are optional and OFF by default. Enabling them
registers a separate Home Assistant receiver with the manufacturer's LT push
service and Google/Firebase, sharing the device UID and HA receiver identity/token.
It does not send the local unlock code or require a Philips account/password or
phone token. Receiver credentials are kept in Home Assistant's private `.storage`,
outside entity attributes, events, logs and diagnostics. Private storage is not
encryption at rest; treat Home Assistant backups as sensitive.
