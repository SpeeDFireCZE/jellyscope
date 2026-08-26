# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org):
the middle number goes up when something new arrives, the last one when
something only gets fixed.

The database migrates itself on start — upgrading is `git pull` and a restart.

## 1.3.0

### Changed

- **The interface now wears Jellyfin's colours.** Blue `#00A4DC` and purple
  `#AA5CC3` — the pair from its logo — run through the whole app: the
  selected item in the menu, the chosen period, the progress of a playback,
  single-colour charts and the poster placeholder before an image arrives.
  The dark mode moved with them, from neutral black to a blue-violet
  ground.

  The straight logo colours could not be used for charts, though. Measured
  against colour blindness (deuteranopia and protanopia, difference in
  OKLab), blue and purple from the logo come out at 6.6 where the floor is
  8 — a person with the most common form would see one line. Pulling them
  apart by **lightness** instead of hue fixes it: a lighter blue and a
  deeper purple measure 17.9 and still read as Jellyfin.

  The whole series palette was rebuilt on that basis, and it came out
  better than the one it replaces: the closest neighbouring pair went from
  8.9 to 11.6, and the worst pair anywhere from 1.6 to 6.1 — under the old
  palette teal and pink were practically the same colour to a person with
  deuteranopia.

- **Colour now says what it means.** Green is direct play, orange
  transcoding, amber a warning — and the same colours appear in the
  badge on a playback and in the "how the server delivers content" bar,
  which used to disagree. The status colours were also picked for the dark
  mode for the first time; until now it borrowed the light-mode ones, so a
  dark green sat almost invisibly on a dark ground.

- **Cards say more with less.** The "now playing" card carries the stream
  rate in its top corner with the resolution and codec below it, states are
  filled pills while facts stay quiet, and the language and subtitles are
  shown even when the session reports only the track it started with.
  "Recently added" became a card like the rest, the headline number moved
  beside its curve instead of above it, and where two lines overlap the
  fill is lighter so the muddy third colour stops forming.

### Fixed

- **The bandwidth chart labelled its axis in UTC.** Times are stored
  without a zone, and `datetime.timestamp()` treats such a time as *local* —
  so the axis came out shifted by the whole offset while the rest of the
  app showed local time. In summer the evening peak therefore appeared an
  hour earlier than it happened. The same slip moved the "peak at" line
  above the chart.

### Added

- **A switch between the new appearance and the classic one** in Settings →
  Interface. The classic option restores not only the old colours but the
  old shapes: flat cards, badges with a dot, the headline number above the
  curve. It is entirely CSS — the charts draw through variables, so the
  application code knows nothing about which look is on, and a test keeps
  it that way.

## 1.2.11

### Fixed

- **The language from the file name almost never landed.** Reading the name
  worked; what came after it did not. A track only got a language when the
  name listed **exactly as many** languages as the file has audio tracks —
  and real names hardly ever do. The common file has a Czech dub and the
  original next to it while the name says only `CZ`: one tag, two unknown
  tracks, so nothing was filled in at all.

  Two things changed. What the file already knows is now subtracted from
  what the name promises: tracks `[English, unknown]` with a name saying
  `CZ.EN` leave one new language for one unknown track, so it is clear
  what goes where. And when even that does not resolve it — one tag, two
  unknown tracks — the languages are written to the **item** instead of to
  a track. The statistics read the item, so they stop saying "unknown",
  while the tracks keep saying it, because which of them is Czech is
  genuinely not known. The card says where it came from: *from the file
  name: Czech*.

- **Three shapes of name that were read wrong.** Lowercase `cz` after the
  year (`Film (2004) HD cz.avi`) — capitals are still required before it,
  where a title lives. A tag glued to the year (`Film-2003CZ.mp4`), which
  hid the boundary between title and tags, so nothing after it counted.
  And names whose only marker is the source (`DVDRip`, `TvRip`, `XviD`,
  `HD`), which now end the title the same way a year does.

  `C4U` at the end of a release name is still not a language, and lowercase
  `cz` **before** the year is still part of the title.

## 1.2.10

### Added

- **The file name as the last resort for a track's language.** When neither
  the file nor Jellyfin knows what language a track is in, many libraries
  still say it in the name — `Duna.2021.CZ.SK.EN.1080p.mkv`. Those tags are
  now read, and the whole thing rests on one rule: a **whole section between
  separators** has to match, never a run of letters. "Czechacek" and
  "enigma" therefore never pass, though one contains "cze" and the other
  "en".

  Two more guards sit behind it. Two-letter tags count only in capitals —
  lowercase "de", "es" and "ja" are ordinary words in film titles (Casa de
  Papel, Já, Olga Hepnarová). And full language names count only after the
  year or episode number, because before it they are usually the title: The
  Italian Job, Polish Wedding, Russian Doll.

  The name says *which* languages are in the file, not which track is which,
  so the tags are handed out in order and marked as a guess — the set of
  languages, and with it the statistics, is right; the order is an estimate.
  If the counts do not match, or a track whose language is already known
  contradicts the name, nothing is filled in at all.

### Fixed

- **A paused playback counted as if it were still streaming.** Nothing
  flows during a pause — the server sends nothing and the player asks for
  nothing — but the concurrent-bandwidth graph took the stream's bitrate
  and spread it across the whole span from start to end. A film paused
  overnight held a full 20 Mbit/s until morning, and so did a playback
  paused *right now*, because the last-seen timestamp keeps advancing
  while it sits there.

  Two numbers on the same page therefore disagreed: the area under the
  curve came out many times larger than the data volume beside it, which
  has always been counted from watched seconds.

  When the pause happened is not something the database knows — it keeps
  totals, not intervals. So the flow now lasts as long as the playback was
  actually watched. With a pause in the middle it sits earlier in the day
  than it really did, but its height and its amount are right, and the
  peak — the number a line is dimensioned by — stops being invented.

  The data volume, the per-user and per-device totals and the traffic by
  address were already counted from watched seconds and are unchanged. So
  are the language statistics: the collector only ever adds to the watched
  time while something is really playing.

## 1.2.9

### Added

- **When the file says nothing about a track's language, Jellyfin is asked.**
  ffprobe reads only what is written in the file, and in plenty of files no
  language is written at all — so a title showed three audio tracks as
  "Not stated" while Jellyfin listed the same file as Czech and twice
  Slovak. Both tools were right; they were just looking in different
  places.

  Only gaps are filled. What ffprobe read stays as it is, the language from
  Jellyfin goes in where "not stated" was, and the track is marked so the
  detail page can say the value did not come from the file. If the number
  of tracks does not match on the two sides, nothing is filled — a wrong
  language is worse than a missing one, because "not stated" at least shows
  that nobody knows.

  It also applies to what was measured earlier, not just to newly analysed
  files, so an already-scanned library gets its languages on the next run
  of the file analysis. A track nobody could name is marked as asked, so
  the same question is not sent again on every scan; a fresh measurement of
  the file clears that mark, which is what makes corrected metadata in
  Jellyfin take effect.

### Changed

- **uvicorn 0.52.3 → 0.52.4**, the bump Dependabot proposed and CI accepted.

## 1.2.8

### Security

- **The brake on password guessing could be walked around from outside.**
  Behind a reverse proxy the real client address arrives in the
  `X-Forwarded-For` header — and the app read that header itself, checking
  only whether a proxy was configured at all, never who had actually sent
  it. Anyone who could reach the app directly could therefore write a
  different address into that header on every attempt, and each try was
  counted separately. The brake existed and never engaged.

  The address now comes from `request.client` only. Behind a proxy uvicorn
  fills that in from the same header, but only when the request came from
  an address in `FORWARDED_ALLOW_IPS` — the check the app's own copy was
  missing. Measured on the old code: eleven wrong passwords, zero blocks.
  On the new one the block arrives on the eighth.

- **A Content-Security-Policy is now sent with every page.** Jellyscope
  loads nothing from anywhere else — no CDN, no web font, images come
  through our own server — so the policy can say "from here only" without
  breaking anything. Inline scripts still have to be allowed (the pages
  carry their own `<script>` blocks), so this does not stop injected code
  outright; it takes away what such code is for: no fetching more code, no
  sending data to another host, no redirecting a form elsewhere, no
  framing the page.

- **The size limit on uploaded backups now applies while reading.** The
  file was read into memory in full and measured afterwards — a limit that
  arrives after the damage. A large enough file could exhaust memory
  before the app got to say it was too big.

- **Two files that hold secrets were readable by anyone on the machine.**
  `data/secret_key` has been owner-only from the start; `data/database.json`
  and the backups were not. The first holds the **PostgreSQL password**, in
  clear text, because the app has to log in with it. The second is the whole
  database — including account password hashes and everybody's viewing
  history.

  Both now get mode 600 when written, the same as the key. On Windows
  `chmod` does nothing and the folder is what protects them, so a failing
  `chmod` is ignored rather than fatal — losing the ability to save your
  database settings would be the worse trade.

  Found by CodeQL (`py/clear-text-storage-sensitive-data`), and it was
  right: the care existed in one place and was missing two files over.

### Changed

- **Twenty-six translation keys existed twice.** A repeated key silently
  overrides the earlier one, so eight of them showed the wrong English: a
  device on the Network page had a "Last run" instead of "Last seen", a
  series read "at 3 seasons" instead of "in 3 seasons", database tables
  counted "Lines" instead of "Rows". Where one Czech word genuinely means
  two things, both meanings now have their own key.

- **Forty-three translations belonged to text that no longer exists** —
  leftovers of rewritten messages and removed buttons. They are gone, and
  a test now watches for duplicates, dead entries, keys used in templates
  but missing from the dictionary, and placeholders that differ between
  the two languages.

- **Duplicated code merged where merging helps:** one availability check
  for optional libraries instead of two, one path through a file import
  instead of two nearly identical routes, one sentence-builder for the
  numbers in an import summary, and one wrapper for the account commands
  in `manage.py`.

## 1.2.7

### Fixed

- **The page after an update from the browser never let you through.** It
  said "restarting — I'll let you through once it is up", and then sat
  there while the app had long been up on the new version.

  It waited for the process start time to change, but took its baseline
  from the *first* answer it got. The restart happens within a second and
  the first question is asked after two — so that answer already came from
  the new process. The page wrote it down as "what I am waiting away from"
  and waited for a change that had already happened.

  The starting point is now written into the page by the server that
  rendered it, and the page asks immediately rather than after a two-second
  pause. It also watches the **version**, which is the actual question
  ("is the new version running?") rather than a proxy for it — `/health`
  reports it to a signed-in browser. And if nothing comes up at all, after
  five minutes the page says so and offers a link, instead of spinning
  forever.

  The same baseline mistake was in the wait after the manual restart button
  in Settings; it is fixed there too.

## 1.2.6

### Added

- **`.env.example` now names every variable the app reads.** Three were
  missing: `JELLYSCOPE_DOCKER`, `JELLYSCOPE_DEMO` and `JELLYSCOPE_HOME`.
  They are written as prose rather than commented-out lines, because
  uncommenting them is not what you want — `JELLYSCOPE_DOCKER` in
  particular belongs to the image, and setting it by hand on an ordinary
  machine only turns off updating from the browser. A test compares the
  file against the source from now on, so the next new variable cannot
  arrive undocumented.

### Fixed

- **The backup folder was filled in on machines that run no container at
  all.** The same mistake as in 1.2.5, one layer down. Guessing the backup
  path asked "am I in any container?", and that question is answered partly
  by looking for `/.dockerenv` — a file a plain machine can end up carrying
  for reasons of its own. Where it did, the app quietly wrote a backup path
  nobody had asked for.

  It also had no business guessing inside somebody else's container: we know
  nothing about what is mounted there, so `data/backups` next to the database
  is not a sensible default, just an unrequested setting.

  Now only our own image fills the field in, where the compose file
  guarantees `/app/data` is a mount. Everywhere else the field stays empty
  and the app asks. If **Settings → Jobs and backups** shows a path you never
  typed, this is where it came from — clear it or point it where you want it.

  Which leaves the broad container check with a single job: the line in the
  startup log saying what the app believes about its surroundings. Nothing
  depends on it any more.

## 1.2.5

### Fixed

- **"In a container" and "from our image" were treated as one question.** They
  are not. Where the backups may go depends on being in a container at all —
  anything outside a mounted folder disappears on the next rebuild, no matter
  who built the image. Whether updating from git makes sense depends on being
  *our* image, where the app is part of a layer and a pull would live until the
  next rebuild and then quietly revert.

  Merging them meant an installation from git inside somebody else's container
  was told to rebuild an image it does not have. The refusal now looks only for
  `JELLYSCOPE_DOCKER=1`, which only our own Dockerfile sets.

### Changed

- The container check also recognises Podman (`/run/.containerenv`), and what
  it decided is written to the log at startup — so the next time somebody
  wonders why the app thinks it is in a container, the answer is in the log
  rather than in a guess.

## 1.2.4

### Fixed

- **"1 619 572 % vs the previous period."** Filtering *this year* reported
  exactly that. The arithmetic was right — the previous window of the same
  length fell into a time when Jellyscope was not running yet, so today was
  being compared with a few seconds of history. A percentage like that says
  nothing about today, only that there was almost nothing before.

  When the previous window starts earlier than the history reaches, the
  comparison is no longer shown; the tile says why, with the date the history
  begins. A silently missing arrow looks like a bug and sends people looking
  for a mistake they did not make. And where the data is there, the number
  changes shape as it grows: percent up to 1000 %, a multiplier up to 100×,
  words above that.
- **The hero number forgot to name a custom period.** It read "Total watched
  over" and then nothing, because the label table has no entry for a date
  range. It prints the range now.

## 1.2.3

### Fixed

- **Updating from the browser ended on Internal Server Error.** Templates are
  read from disk on every request while the code lives in the process's
  memory, so between the pull and the restart there is a moment of *old code
  over new templates* — and the redirect to `/?wait=restart` had to be rendered
  in exactly that moment. It is the trap `deploy/update.sh` has warned about in
  prose for months. The update now answers with a page assembled in Python: no
  template, no context, nothing that can drift with a version. It waits for the
  new process (`started_at` from `/health`) and only then lets you through.
- **The month arrows in the calendar closed it instead of moving.** Redrawing
  the panel removes the button that was just clicked, so by the time the click
  bubbled up to the "clicked outside" handler, `closest()` ran on a detached
  node and returned null — the calendar decided the click was somewhere else
  and shut. It is watched in the capture phase now, where the button is still
  in the document.

### Changed

- **In a container, the update button says what to do instead.** It never threw
  — the button is not rendered, since `.git` is not in the image — but the note
  beside it read "only where the app came from git", which is true and useless.
  The refusal now carries a reason, and in a container the reason is the answer:
  `git pull && docker compose up -d --build`. A container is refused even when
  `.git` did make it into the image: the pull would succeed, live until the next
  rebuild and then quietly revert, and a button that works and then undoes
  itself is worse than one that does not work.

## 1.2.2

### Added

- **A period you choose yourself.** The switcher above the statistics has a
  fifth option: it opens a dialog with two dates, three shortcuts for the
  questions people actually ask (this month, last month, this year) and a
  calendar that drops down when a field is clicked. The calendar is our own —
  `<input type="date">` looks different in every browser and sticks out in the
  middle of an otherwise matching dialog — and it is a suggestion, not the only
  way in: the date can be typed, in the Czech way or as `2026-08-01`. The same
  calendar is wired into the history filter, which had the same kind of field.

  Underneath it is not cosmetic. Every query asked only *since when*
  (`datetime('now', '-30 days')`), so the window always ended now and "last
  December" could not be expressed at all. All of them take both bounds now.
  For "last N days" the upper bound stays deliberately open: time is stored
  rounded to seconds, so a playback written in the same second we ask would
  fall outside a strict upper bound — and that is exactly the record somebody
  is looking at.

### Fixed

- **Backups from a container had two ways to disappoint.** `pg_dump` was not in
  the image, so a PostgreSQL backup fell back to the app's own export — it
  worked, but `pg_dump` handles a consistent snapshot, dependency order and
  indexes. And the backup folder had to be set by hand, where it was easy to
  pick a path outside the mounted folder: such a backup is written into the
  container and disappears with the next build, while the task keeps reporting
  success for months. The app now recognises it runs in a container and fills
  in `/app/data/backups` — on the host that is `./data/backups`. A path you
  chose yourself is never overwritten.
- **The demo swallowed the filters.** Its guard stopped every form, including
  the ones that only filter — the period above the statistics and the filter in
  the history. It now stops only what writes; a GET form changes the address,
  not the data, which is how the server middleware saw it all along.
- **A chip that is a button now behaves like one.** The period switcher gained
  a chip that is a `<button>` rather than an `<a>`, and a button gets no
  pointer from the browser — the same-looking chip beside it had a hand, this
  one did not.

## 1.2.1

### Added

- **Docker.** `Dockerfile`, `docker-compose.yml` and a section in
  [DEPLOY.md](DEPLOY.md). Everything is configured in the same `.env` the app
  already uses — compose reads that file itself, so changing the port is
  changing one line and starting again. Data lives in a folder on the host
  rather than a named volume, so a backup is a copy of a folder; the container
  runs as UID 10001 and not as root. `ffmpeg` is a build argument: leave it out
  and the image is about 250 MB smaller, with the technical data limited to
  what Jellyfin reports.
- **`maxminddb` comes with the installation.** It used to be optional, which
  was defensible — whoever does not want the map has no reason to install
  anything — but the effect was that the map stayed empty for everyone who did
  not read the note about it. It is small, it opens a local file and never asks
  the network. The GeoLite2 file itself is still a download on a button press;
  only the reader is part of the install now.

### Changed

- **The demo is locked all the way.** Even the language and the interface
  settings are refused now: whatever one visitor saves applies to the next one,
  so "harmless" was the wrong measure — shared is the right one. It also runs
  in English, because visitors come from anywhere and a Czech axis label tells
  them nothing.
- **A blocked action no longer moves the page.** It used to be answered with a
  redirect, which reloads the page and throws away whatever was typed; now the
  browser does not submit at all and a note slides in at the bottom. The
  middleware stays as the real guard — JavaScript can be turned off, it cannot.
- **The demo says how to get in.** Its login page shows the credentials: the
  password is the only way into a demo and there is nothing behind it, so
  hiding it only makes the visitor guess. Demo mode only.
- **The demo has eleven viewers** instead of four, so *Who watches in which
  language* passes the threshold and folds the rest into a dialog — with four
  it never did, and that fold is one of the things worth showing.
- `demo.py` takes `HOST` and `PORT` from the environment. The default stays
  `127.0.0.1`; in a container it is the opposite, since nobody reaches
  `127.0.0.1` inside it.

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
