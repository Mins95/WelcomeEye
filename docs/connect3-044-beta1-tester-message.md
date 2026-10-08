# Connect 3 — message for Yohan

```text
Hi Yohan! 0.4.4-beta.1 adds an experimental Connect 3 video mode on TCP 34567.

1. In HACS, show beta versions, use Redownload and choose 0.4.4-beta.1.
   Restart HA. Keep both existing entries/passwords; test only one entry and
   leave the other unchanged.
2. Close Philips. Before changing mode, run welcomeeye_local.connect3_check_access
   once on the chosen entry and confirm HTTPS 443 succeeds. If it fails,
   download diagnostics and stop there.
3. Open Reconfigure, select “QV TCP 34567
   (experimental)” and enable video. Keep HTTPS on port 443.
4. Read and accept the confirmation. An unknown HTTPS certificate is approved
   on that same screen; no console command or fingerprint entry is needed.
5. Try video once. If it opens, watch for 15 seconds. Download diagnostics
   immediately after success or failure, then close with X and check Philips
   can reopen. Do not repeat the attempt yet.

This mode is video only: no sound, microphone, strike or gate. It uses SDK
encryption but does not provide TLS-equivalent media authentication/integrity.
It still needs your hardware test.

Please send fresh integration diagnostics, your firmware/HA version, whether
the picture moves, startup time and whether Philips resumes. If the first
attempt fails, keep its diagnostics and stop. Do not share passwords.
```
