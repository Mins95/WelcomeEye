# QV cloud protocol evidence for RC5

The Connect3 and Connect2 R002 implementation follows the QV account and alarm
APIs in the Door Connect and WelcomeEye APKs. It does not use the V1 LT account
protocol. This note records the wire contracts used by `qv_cloud_protocol.py`
and `qv_cloud_api.py`; the setup guide is [qv-cloud-doorbell.md](qv-cloud-doorbell.md).

## Branded account profiles

| Model | APK profile | App ID | OEM ID | Own client ID |
| --- | --- | --- | --- | --- |
| Connect3 | Door Connect | 4123 | `G0123,A0058,G0058` | `003-4123-<installation UUID>` |
| Connect2 R002 | WelcomeEye | 4058 | `A0058,G0058,G0079` | `003-4058-<installation UUID>` |

`ExportApp` supplies these application/OEM values. In both APKs,
`BuildConfig.AUTH_CODE = 2`, `SdkManager` and `QvCore.setAuthVersionCode` select
the user-auth version `v1.13`. The UUID is generated for this HA installation
and retained across restarts. It is not copied from a phone.

## Account authentication and authorized devices

`QvApi.userLogin`, `QvApi2.userLogin` and the WelcomeEye `AppInfo.setPassword`
establish SHA-256 UTF-8 password encoding. The APK additionally accepts an
already encoded 64-character internal value. The HA form accepts a normal
password and always hashes that input once.

`UserApi`, `DownChannelManager`, `HttpUserAuthManager` and
`QvAuthManager.handleDownChannelResponse` establish the separate HTTP channels:

1. GET `/auth/user;jus_duplex=down` starts the cookie-bound response channel.
2. POST `/auth/user;jus_duplex=up` sends an XML `login` request.
3. The matching XML response arrives on the down channel, identified by
   `header/seq`. A successful POST acknowledgement is not login success.

The APK marks its down channel initialized after reading a complete successful
body. HA permits the first POST once HTTP 200 headers and cookies have arrived,
so a server that holds that first body until login cannot deadlock startup.
Only the validated matching XML reply can authenticate the account. Non-200
initial responses never release a login request.

The XML header carries `flag=tdkcloud`, `version=v1.13`, the command, sequence,
session and own client profile. `LoginReqContent` carries `account`, hashed
`password`, `auth-type=0`, empty `auth-code` and `ip-region-id`.
`UserLoginResp` provides `content/account-id`; `UserAuthComResp` provides
`header/session/id`. Result zero is success. The APK's region redirect result
`100101000` carries `content/redirect-region-id`; HA permits one directory
resolution and retry for that explicit region.

`UserAuthRequestHelper` and `DevListReqContent` define `get-device-list` with
empty filter/owner and zero order/count/page. `DeviceListResp` defines inline
`content/device` rows with `id`, `name`, `model`, `channel-num`, `from-share`,
`is-hs-device` and optional `auth-chans/item/channel`. HA retains only the
identity, display metadata and authorized channel set. It ignores local
passwords, dynamic passwords and out-auth codes in this response.

QV channels are zero-based. HA channel 1 subscribes to QV channel 0; HA channel
2 subscribes to QV channel 1. The subscription target must still be present
in a freshly retrieved authorized device list at runtime. Compatibility
cloud types HS and LT are excluded from this path.

## Own push registration and subscription

`OAuthApi` and `QvOAuthManager` define GET `/qvoauthv2/token` with
`grant_type=password`, `client_id`, `client_type=3`, `oemid`, `appid`, `usr`,
hashed `pwd`, `region_id` and `client_flag=1`. The returned `access_token`
is sent in the OpenAPI header named `token`.

The push API supplies POST `/openapi-tdk/client/info` and
`/openapi-tdk/client/push/token`. The latter registers this installation's own
FCM token using `serverTypeToken: [{serverType: "fcm", pushToken: ...}]`.
`QvCommonOpenApiResp` defines integer `code=0` as acceptance.

`AlarmRequestHelper` and `AlarmLoginReqContent` define POST `/UserAlarm` with
command `client-login`. The content contains this HA client's ID, FCM token,
`tokentype=1`, client type 3, app/OEM values, language and timezone; the account
block contains the authenticated account ID and auth service hostname. The
alarm HTTP cookie jar is independent of the auth cookie jar.

`AlarmRequestHelper` and the device-alarm request content define command
`client-update-dev`, with the selected `devid`, zero-based `chno`, `ifshare`,
`ifpush` and `detail`. `QvAlarmStatus.TYPE_CALL=19` and
`setStatus(type, enabled)` uses bit `type - 1`, giving ring-only
`detail=262144`. The expanded detail strings describe excluded event types;
the implementation does not substitute them for this bitmask.
`QvDevice.getDeviceCloudTypeInfo` returns null for native QV devices, so the
optional `ifhsdev` field is omitted.

Disable updates the same own-client UID/channel with `ifpush=0` and
`detail=0`. It does not unregister a phone token, change device ownership or
log out every account session. A zero alarm result confirms request
acceptance; it does not prove that a later physical ring was delivered.

## Validation and evidence limits

The protocol and HTTP tests use synthetic credentials and responses. They
cover profile separation, password encoding, XML fields, own-client scope,
authorized channels, subscription/disable, malformed responses, bounded
response size, normal HTTPS verification and disabled redirects. The API
tests confirm that an up-channel acknowledgement cannot authenticate the
account, that the initial down body can wait for the first login POST, that
initial HTTP failures send no credentials, and that cancellation closes the
down channel.

The account-flow tests cover cancellation, restart, concurrent changes,
duplicate binding and private selection state. `verify_qv_cloud_runtime.py`
passes against actual Home Assistant 2026.10 with mocked cloud I/O: both model
families, JSON form serialization, separate channel pulses, no media session
acquisition, disable and unload including a queued callback.

No user credentials were submitted during development. These tests do not
establish live account login, live subscription acceptance or delivery from a
physical intercom. The public service-directory query and certificate checks
are recorded below separately from the synthetic account tests.

The local research captures under `work/alarm-subscription-20261015/` include
`rc5-password.txt`, `rc5-auth-inventory.json`, `rc5-session.txt`,
`rc5-transport.txt`, `rc5-devices.json`, `rc5-token-header.txt`,
`rc5-openapi-schema.json`, `push-auth-envelope.txt` and `push-alarm-settings.txt`.
They were extracted from the local APK DEX files with method/class names and
offsets; APK binaries, account data and tokens are not part of this package.

## Service directory and TLS evidence

Door Connect `CoreApplication` selects port 443 and the configured default
`sh2.qvcloud.net`; `QvP2PClientV2.createServiceUrl` constructs HTTPS. In
`libqv-p2p-v2.so`, `DevClientMst::OnResult` at `0x5199f8` selects GET
`/mst/query` with an XML body and `application/xml;charset=utf-8`.
`ServerResource::BuildHlr` at `0x51dc50` defines `query-hlrv2`, sequence 1,
the OEM profile, empty device/public-IP fields, the installation's own client
ID, region and SDK version `4456`. The resolver requests only `userapp`,
`sec-userapp`, `alarmapp`, `sec-alarmapp`, `oauth2` and `openapi`.

WelcomeEye/R002 independently confirms the same bootstrap route. Its
`ExportApp` sets `sh2.qvcloud.net`, `SdkManager` selects port 443, and
`QvP2PClientV2.createServiceUrl` constructs HTTPS. In the WelcomeEye 6.1.58.24
ARMv7 library, `DevClientMst::OnResult` at `0x32cf18` references `/mst/query`
at `0x32cf78`, the XML content type at `0x32cfb6`, and GET at `0x32cfbe`.
`ServerResource::BuildHlr` at `0x32f904` uses `query-hlrv2` and the secure
account/alarm service names. That library's SHA-256 is
`210402ce70a86a7d3ab5d25cecb8393b72d682ccd8576b02dfcb8a4458346d41`.
The common host/path is supported by both APKs; their app/OEM namespaces
remain distinct as listed above.

`DevClientMst::ParseHead` (`0x51a65c`), `ParseContent` (`0x51a7c0`) and
`ParseServer` (`0x51b260`) define the result, client region and service rows.
The envelope succeeds with `result=0`; an individual service succeeds with
`query-result=1`. HA selects the explicit `sec-userapp` and `sec-alarmapp`
rows and requires HTTPS for all four selected services. Requests are bounded
to 15 seconds and responses to 64 KiB, with no redirects or HTTP fallback.

The directory certificate has no DNS CN or SAN and is signed by the
Qualvision private CA in the APK's public `device.pem` bundle. Its trust is
therefore vendor-CA identity, not DNS-name identity. The bootstrap context
contains only that CA, requires a verified chain and valid certificate dates,
and requires TLS 1.2 or later. It is restricted to the exact
`https://sh2.qvcloud.net:443/mst/query` authority/path. It contains no system
roots, client certificates or private keys. It is never used for intercom
connections or account services.

The bundled public CA's SHA-256 fingerprint is
`cbc77b01d58308cc0fa8ee4af2e508af2cb3864e5a88628b0e7d0e474f4d7763`.
Its subject is `O=QUALVISION TECHNOLOGY CO.,LTD, C=CN, ST=ShangHai`; it is
valid from 2017-12-11 to 2037-12-06. Native `CTDKSsl::SslConnect`
(`0x660e64`) loads a configured CA and enables peer verification without DNS
matching. The native date-check bypass is deliberately not reproduced.

On 2026-10-17, one bounded directory request with a newly generated client ID
and no account or device credentials returned HTTP 200, region 1 and:

| Service | Authenticated directory result |
| --- | --- |
| Secure account | `https://shi-20-sec.qvcloud.net:443` |
| Secure alarm | `https://shi-19-sec.qvcloud.net:4443` |
| OAuth | `https://oauth2shi.qvcloud.net:443` |
| OpenAPI | `https://tdkopenapishi.qvcloud.net:443` |

Separate certificate-only handshakes to these returned authorities passed
normal public-CA and DNS-name verification, with a `*.qvcloud.net`
certificate issued by GlobalSign GCC R3 DV TLS CA 2020. Account, alarm and
OAuth/OpenAPI requests retain that normal HTTPS verification. Port 4443 is
allowed only for the alarm service. These host observations are evidence of
the directory response, not hardcoded regional service assignments.

The directory tests cover malformed and ambiguous results, bounds, profile
separation, secure service selection, cancellation and no redirects. The TLS
tests verify the single CA anchor, retained date verification, rejection of
other authorities/paths and rejection of a synthetic outsider certificate
before application data. The successful public lookup does not establish
account login, subscription acceptance or physical doorbell delivery.
