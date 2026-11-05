# Security

Please do not publish device passwords, device identifiers, packet captures, or
private network details in public issues.

If you believe you have found a security issue, contact the repository owner
privately through GitHub rather than opening a public issue containing secrets.

The integration stores device credentials in the Home Assistant config entry
and uses them to authenticate directly to the device on the local network.
Video, audio, microphone and strike/gate controls use local protocols.

Older V1 devices can require an unencrypted LT connection. Explicit approval is
required before the local password is sent; it is bound to the configured address
and device identifier. Credentials, media and commands on that connection are
not encrypted, and discovery cannot authenticate the device. Use a trusted local
network. Existing protected entries never fall back to this mode automatically.

Connect 3/R002 CGI authentication uses verified HTTPS. Approved QV TCP media
has different protections from TLS: it does not provide TLS-equivalent peer
authentication, message integrity or replay protection. Certificate exceptions
and TCP permissions remain explicit and scoped to the configured device.

Optional Connect 3/R002 cloud notifications use the official app account and a
separate HA registration. Account credentials, receiver tokens and replay state
are stored privately, not in public entity attributes or diagnostics.

V1 cloud doorbell notifications are optional and OFF by default. Enabling them
registers a separate Home Assistant receiver with the manufacturer's LT push
service and Google/Firebase, sharing the device UID and HA receiver identity/token.
It does not send the local unlock code or require a Philips account/password or
phone token. Receiver credentials are kept in Home Assistant's private `.storage`,
outside entity attributes, events, logs and diagnostics. Private storage is not
encryption at rest; treat Home Assistant backups as sensitive.
