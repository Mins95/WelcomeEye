# Cloud history

Connect 3 and Connect 2 R002 can read recent records from their existing cloud
connection. In **Developer tools → Actions**, choose **WelcomeEye: Read cloud
history**, select the device's status sensor and run the action as an administrator.

```yaml
action: welcomeeye_local.cloud_history
target:
  entity_id: sensor.your_welcomeeye_status
data:
  limit: 30
```

Use the entity selected in Home Assistant, not this example's literal entity ID.
Cloud notifications must already be enabled and their registration established.
This action does not enrol another account or modify a subscription. It is not
available for V1's separate cloud protocol or local-only R001 installations.

The response lists the reported channel, time, event and opening method when
the vendor supplies them. `rf_card` means that the vendor reported a badge
opening; it does not identify the badge owner or prove a relay physically moved.
Unknown event/method codes remain unknown. Reported times retain the vendor's
format and are not silently converted to UTC.

The read is limited to 60 returned records, four pages and 30 seconds. Device
and authorized channel checks apply to every row, even if the server ignores
the filter. Duplicate records are suppressed within the request. The response
indicates whether the bounded read reached the end; it is not a permanent archive.

No background polling, live ring event, photo, video, opening command, history
deletion or read acknowledgement is triggered. Results are returned to the
administrator; diagnostic exports retain counts/status only. Badge identifiers,
names, media URLs and arbitrary vendor text are not exposed in diagnostics.

## Protocol evidence and remaining validation

The existing vendor APK extraction provides the wire contract:

- `AlarmRequestHelper.getQueryAlarmListReq` and `AlarmListQueryReqContent`:
  `client-query-recordlist`, `maxid`, `pageno`, `pagelinenum`, device filter.
- `MainMessageListPresenter`: initial cursor `0`, page `0`, then next page.
- `MainMessageListModel`: 15 rows per page.
- `AlarmListQueryResp`: `content/record/data` with `devid`, `chno`, `id`, `event`,
  `alarmstate`, `time`; cursor in `content/maxid`.
- `QvAlarmStatus`: event 63 is an unlock report; state 4 is application and state
  5 is RF card. No verified badge-number field has been established here.
- `AlarmInfoAdapter` uses `alarminfo=1/2` to select an opening-output icon.
  These values are exposed as `reported_output`, never as badge identifiers.

The software checks use synthetic records and local HTTPS servers. Actual cloud
history availability and per-firmware fields still require user validation.

The separate `set.device.opendoor` CGI command is not enabled by this change.
`QvDeviceApi.deviceUnlock` conditionally encodes the opening code according to
device capability 24. Its presence in the SDK alone does not prove support or
matching relay semantics on every firmware. Existing validated opening commands
remain unchanged; no speculative CGI fallback is introduced.
