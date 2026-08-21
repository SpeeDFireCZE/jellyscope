# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org):
the middle number goes up when something new arrives, the last one when
something only gets fixed.

The database migrates itself on start — upgrading is `git pull` and a restart.

## 1.1.0

### Added

- **Interface section in Settings.** Two things that used to be constants in the
  code are now yours to set: how many playbacks the Overview lists before the
  rest folds away, and how many people the language statistics show. Anything
  outside 1–50 is clamped on read, so a stray value cannot break the page.
- **Long lists fold into a dialog.** Above the threshold, *Now playing* and the
  language bars hide behind a button that sits exactly where the first stream
  would be, so the card keeps its place and nothing below it jumps around. Both
  dialogs have a filter for finding one person in a long list.
- **Reload the metadata of a whole series.** Fixing a wrongly identified series
  in Jellyfin changes every episode at once; there is no sense in clicking
  through fifty of them. Cached images of the affected episodes are dropped too,
  so the corrected poster appears straight away.
- **The library page shows the total size of all libraries together.**
- **The transcode badge says what is actually being re-encoded.** Hovering it
  reports video, audio, burned-in subtitles and hardware acceleration
  separately — re-encoding video costs many times what re-encoding audio does,
  and "transcode" alone never said which one was happening. Jellyfin reports it
  in `TranscodingInfo`; older records fall back to comparing the source and
  target codecs.
- **The map can be zoomed by clicking**, with + / − / reset buttons over its
  corner. Alt-click zooms out, dragging pans, double-click returns the whole
  world.
- **Demo mode plays from public addresses too**, so the map on the Network page
  has something to show before you connect a real Jellyfin.

### Changed

- **Area charts are drawn as a smooth curve.** Thirty days of daily hours in
  straight segments was a sawtooth. The curve is monotone cubic, not a free
  spline: a free spline overshoots between points, so the chart would show a
  peak that never happened and dip below zero on a quiet day.
- **Chart fills fade instead of sitting flat.** The old 22 % wash turned every
  overlap of two series into a third, muddy colour and put the heaviest ink
  along the baseline, where there is no information. Grid lines are dotted now,
  with only the zero axis solid.
- **Horizontal bars** run a gradient towards the value and sit in a lighter
  track; **donut segments** get the same 2px gap the stacked bar already had.
- **The value tooltip is a card in the page colours**, with the day as a heading
  and one line per series in that series' colour — where two lines cross, the
  colour is the only thing that says which number belongs to which. Tooltips
  that carry several facts (the transcode badge) break onto separate lines.
- **The map no longer takes the scroll wheel by default.** Anyone scrolling past
  the Network page used to get stuck at it: the page stopped moving and the map
  zoomed instead. Zooming by clicking is the default; the wheel is still
  available and is chosen in Settings → Interface.

### Fixed

- **Posters kept showing the old image.** Once a file landed in the image cache
  it was never asked about again, so fixing a wrongly matched poster in Jellyfin
  changed nothing here. Jellyfin's `ImageTags` fingerprint is now stored with the
  item and the stale files are dropped when it changes.
- **Episodes haunted the archive.** When a file was replaced, Jellyfin created a
  new item with a new id and the old one dropped into the archive — sometimes
  twice — next to the live episode it belonged to. Merging on sync compares
  `tmdb_id`, which older records do not have; *Straighten the data* now also
  matches on series plus season and episode number and moves the playback
  history to the live item.
- **The Network chart offered "Mbit/s: 12,4 h"** — it appended the default unit
  to a series that already carried its own.

### Internal

- Dead code removed and seven pieces of copy-paste folded into one place each.
  Two of them had already drifted apart: one of the three import routes was
  missing a sentence the other two had, and the list of titles without a
  language and its own count each spelled the filter out separately.
- New tests cover the series archive, the bounds of the smooth curve, tooltip
  escaping and the Interface settings.

## 1.0.0

First public release.
