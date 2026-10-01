Hi @dirksleegers-web,

I've added a separate Connect 3 experimental entry in [0.4.3-beta.4](https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.3-beta.4). Let's start with discovery only — video and intercom controls aren't available yet.

1. In HACS, enable beta versions, redownload **0.4.3-beta.4** and restart Home Assistant.
2. Add **Philips WelcomeEye**, choose **WelcomeEye Connect 3 / Philips Door Connect — experimental**, enter the intercom's IP and confirm. Leave the optional credentials blank.
3. Keep the Door Connect app and video players closed. HA and the intercom should be on the same LAN. No need to ring the bell.
4. In **Developer tools → Actions**, run this **once**, replacing the entity with the diagnostic sensor created for your Connect 3:

```yaml
action: welcomeeye_local.connect3_discover
target:
  entity_id: sensor.YOUR_CONNECT3_STATUS
data:
  include_details: true
```

Please send back the **action response**, the integration's **downloaded diagnostics**, your **HA version / installation type** and **intercom firmware version**. If setup fails, send the exact error instead.

Review the response before posting and remove any personal identifiers. Don't share passwords, authCode or tokens. A missing reply is useful feedback too.

Thanks!
