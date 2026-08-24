# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org):
the middle number goes up when something new arrives, the last one when
something only gets fixed.

The database migrates itself on start — upgrading is `git pull` and a restart.

## 1.2.0

### Added

- **Updating from the browser.** The version indicator in the sidebar used
  to be a small badge linking to GitHub — the one thing down there worth
  looking at, dressed as a footnote. It is now a button that opens a dialog
  with the release notes and, for an admin, an **Update and restart**
  button. The update does what `deploy/update.sh` does minus its last step:
  `git pull`, install any new dependencies, then replace its own process the
  same way the restart button in Settings does — no service manager
  involved. Nothing restarts if the update fails, so a broken pull leaves
  the old version running, and it refuses outright when the folder has local
  changes, when the app did not come from git, or in demo mode.
- **Runtimes.** A film and an episode say how long they are, the episode
  list of a series has a length column, and the series adds its episodes up
  into a total.
- **A demo that cannot be broken.** In demo mode every request that would
  write is answered with a note instead of doing the work — the buttons stay
  visible, because a demo is there to show what the app can do. Only signing
  in, the interface settings and the language switch still work. It is one
  check in one place rather than a rule repeated in forty routes, since the
  route somebody forgets is exactly the one a visitor finds.

### Fixed

- **One series could empty the Overview.** After a wrongly identified series
  was fixed in Jellyfin, every episode was written again and got today's
  date — and *Recently added* kept nothing but that one series. It took a
  fixed number of the newest **rows** and grouped them afterwards, so two
  hundred episodes filled the window on their own. It now picks the newest
  **groups** first and fetches their rows after, which makes the size of a
  series irrelevant.
- **Fifteen hours of a twenty-eight minute episode.** Playback Reporting and
  Jellystat both measure on the clock — from the start of playback to the
  end of the session — so falling asleep with the player open is reported as
  fifteen hours, and the import took that at face value. *Straighten the
  data* now shortens imported records that ran past 1.5× the title's own
  runtime: enough slack for seeking back, not enough for a television nobody
  turned off. Nothing is discarded — the excess moves into the paused time,
  so watched plus paused still adds up to the span between start and end.
  Records the collector gathered are left alone; their increment is already
  capped while measuring.

## 1.1.1

### Fixed

- **A series poster could not be refreshed.** The image cache decides what
  is stale from Jellyfin's `ImageTags` fingerprint, which is stored with the
  item — but a series has no item of its own; only its episodes do, while the
  poster is fetched under the series id. Nothing ever fingerprinted it, so a
  poster corrected in Jellyfin never reached the screen and a library scan
  changed nothing. Jellyfin reports `SeriesPrimaryImageTag` on every episode,
  and that is now stored with it: a changed fingerprint drops the cached
  files, and it travels in the image URL as well, so the browser cannot serve
  its own copy from before either. The first sync after this upgrade drops the
  cached series posters once — which is what repairs the ones that are stale
  today.
- **The archive did not merge when a whole series was added to Jellyfin
  again.** Archived episodes are matched to their live twin by series id plus
  season and episode number. Delete and re-add the series' folder and every
  episode gets a new series id, so the old rows never find their twin and stay
  in the archive for good. The series name now serves as a fallback — but only
  when nothing live is left under the old series id, and never when both sides
  know a `tmdb_id` and the two differ. Two series can share a name.

### Changed

- **"Straighten the data" says what it actually does.** Its description
  promised only the history, so nobody expected it to be the thing that brings
  episodes back from the archive — and a library sync, which is what people
  reach for instead, does not do it.

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
