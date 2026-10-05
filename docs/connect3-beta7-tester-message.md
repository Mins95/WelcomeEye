Hi! [0.4.3-beta.7](https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.3-beta.7) now includes experimental Connect 3 live video, following your successful local authentication test.

1. Update in HACS and restart HA. **Keep your existing entry, password and certificate pin.**
2. Close Philips Door Connect. Reconfigure the Connect 3 integration and enable **Experimental live video**.
3. Select the new camera in the WelcomeEye card. Open video for **15 seconds**, close it with **X**, then repeat once.
4. Check that the picture moves and that the Philips app can open video afterwards.

Please send the integration diagnostics and tell me whether each opening worked, the approximate startup delay, and whether Philips resumed. If the first attempt fails, send its diagnostics before trying anything else. **No microphone or gate/strike tests yet.**

This path has passed software tests; your device will provide the first hardware video validation. [Detailed steps and rollback to beta.6](https://github.com/Mins95/WelcomeEye/blob/v0.4.3-beta.7/docs/release-043-beta7.md).
