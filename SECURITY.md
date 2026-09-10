# Security

Please do not publish device passwords, device identifiers, packet captures, or
private network details in public issues.

If you believe you have found a security issue, contact the repository owner
privately through GitHub rather than opening a public issue containing secrets.

The integration stores the device credentials in the Home Assistant config
entry and uses them only to authenticate directly to the device on the local
network. Runtime communication does not use a vendor cloud API.
