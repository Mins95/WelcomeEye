# 0.4.3-beta.6 — WelcomeEye at the root of Media

Prerelease based on **0.4.3-beta.5**. Stable remains **0.4.2**.

## Install

In HACS, enable prereleases and select **0.4.3-beta.6**, or install the
`welcomeeye_local.zip` release asset. Restart Home Assistant, then reload the
browser or Companion app. Existing integration entries can be kept.

Open **Media / Médias → Sources multimédias**. A **WelcomeEye** tile appears at
the root, alongside Frigate and My media. Its icon is the WelcomeEye logo already
bundled with the integration, served by Home Assistant's local Brands API.

## Automatic migration of existing photos

Beta.5 already stores photos in the correct physical directory:

```text
<configured media directory>/WelcomeEye/
  welcomeeye_<opaque-entry-digest>/
    2026-10-04/
      2026-10-04_16-00-00_manual.jpg
      2026-10-04_16-01-00_ring_42.jpg
```

Beta.6 automatically adopts that directory as the new **WelcomeEye** source.
This is an **in-place migration**: existing images become visible through the
new tile immediately after restart, with no data copy, rename or deletion.
The files keep their contents and timestamps; old `media-source://media_source/`
references and the corresponding authenticated `/media/` URLs remain valid.
They also remain accessible under My media → WelcomeEye.

The source includes photos from all media directories still configured in HA.
With one directory, the tile opens the device folders directly. With multiple
directories, it first lists their configured names. Retained photos remain
available even if the device entry is disabled or removed. Device folders keep
their existing opaque names. Dates and photo filenames are sorted newest first.

New saved photos return a reference such as:

```text
media-source://welcomeeye_local/local/WelcomeEye/welcomeeye_<opaque-entry-digest>/2026-10-04/2026-10-04_16-00-00_manual.jpg
```

The save response and capture events keep the same fields. Only new
`media_content_id` values use the WelcomeEye domain. Automations can keep saved
older references; automations that explicitly inspect the domain string should
accept both `media_source` and `welcomeeye_local`.

Keep HA's existing `homeassistant.media_dirs` configuration and any persistent
Container volume mount. The integration continues preferring `local` for new
captures, or the first configured root if `local` is absent. A removed or
unmounted media directory cannot be recovered automatically; restore its
configuration/mount to expose its photos. An empty library still opens normally.

## Delivery and scope

The source browses only JPEGs under WelcomeEye. It does not expose hidden files,
unfinished writes, other media folders, non-image documents or linked paths.
Filesystem work runs outside the event loop. Photo delivery uses HA's existing
authenticated local Media route; no new public photo endpoint is added.
The existing local Media access policy applies, rather than camera entity
control permissions. There is no new per-device photo access policy.

Opening Media performs no intercom request or capture. Manual photos, the Photo
card button, opt-in ring captures, device protocols and physical controls retain
beta.5 behavior. This release does not download Connect 3 device-history photos
or add R002/Connect 3 media/output support. Their real-device authentication
validation remains pending. Published photos persist without automatic cleanup.

## Validation and first installation check

Software checks cover beta.5 file adoption through two source initializations,
unchanged file contents/timestamps, new capture references, multiple roots,
sorting, Unicode/escaped filenames, executor work and rejected paths.
Actual HA 2026.7.3/2026.9.3 checks cover the root tile, the shipped logo returned
by the Brands API, native browse/resolve APIs, authenticated local delivery and
old-reference compatibility. All device I/O remains mocked or absent.

On the installed HA, check that the WelcomeEye tile appears, open an existing
photo, take a manual Photo and find it under today's date. Confirm that a saved
old automation reference still opens the same photo. These installation checks
are pending until tested on the owner's instance; software checks do not claim
that its production Media screen has already changed.

## Rollback

Redownload **0.4.3-beta.5** through HACS and restart HA. Integration entries and
photo files remain usable. The WelcomeEye root tile disappears; photos continue
to be available under **My media → WelcomeEye**. New beta.6-domain references
need their domain changed from `welcomeeye_local` to `media_source` when used on
beta.5; keep the rest of their path. Saved pre-beta.6 references need no change.
