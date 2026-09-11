# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org):
the middle number goes up for a **large** release — several new features
together, a reworked interface, something you would notice at a glance.
The last one goes up for a fix or a small addition, and one useful
feature on its own usually counts as that: an existing capability
growing, rather than a new one arriving.

The database migrates itself on start — upgrading is `git pull` and a restart.

## 1.7.0

People with a Jellyfin account can sign in and see their own statistics,
the admin decides what that means, and a title that lies on the disk
twice finally shows both files.

### Added

- **Signing in with a Jellyfin account.** Anybody with an account on the
  server can be let in - **only when the admin switches it on**
  (Settings -> Jellyfin -> Allow Jellyfin sign-in). It is off until then;
  opening the household's statistics is a decision, not something an
  update makes for you. The password is never stored here, only passed
  to Jellyfin to check, and the access token that check produces is
  revoked straight away. Whoever is an administrator in Jellyfin is an
  administrator here, and rights taken away there stop working here at
  the next sign-in.

- **Signing in with a code (Quick Connect).** The same thing from the
  other side: instead of typing a Jellyfin password into our page,
  Jellyscope shows a six-digit code and you approve it in a Jellyfin you
  are already signed in to - no password passes through this application
  at all. The browser gets the code, which on its own lets nobody in;
  what finishes the sign-in stays on the server. It needs Quick Connect
  enabled in the Jellyfin Dashboard, and when it is off the page says so
  instead of failing quietly.

- **A tree of what a viewer sees.** Their own statistics and their own
  history are always there - that is what they signed in for - and
  everything else is a tick box: insights and title charts, the library,
  the server overview, period comparison, now playing, languages, the
  viewer list, the network page. One server is a family's and hides
  nothing; another is for people who should not see each other. Local
  reader accounts have the same tree, each set on its own.

- **Anonymising.** On the pages that are open, somebody else's name
  reads `Viewer 3` and an IP address a dash. The number is stable, so
  rows can still be compared - it just does not lead to a person. Nobody
  is ever anonymised to themselves.

- **Every file of a title.** Two files of one film (4K next to 1080p)
  are one item in Jellyfin, and only the first one used to be kept: the
  library said 1080p while 4K lay beside it, and nothing in the detail
  admitted the second file existed. All of them are stored now and the
  detail switches between them. The numbers of the item itself stay with
  the one Jellyfin treats as the main file, so the library is not
  counted twice.

### Changed

- **A replaced file settles itself.** Re-encoding a film gives it a new
  id in Jellyfin, and the quick sync used to add it as a second title
  with an empty history while the old one stayed behind as a phantom.
  New items are now matched against what we already have - by tmdb id
  first, by name and year (or series and numbers) second - and when
  Jellyfin no longer knows the old id, the history moves over and the
  old row is archived. A genuine second copy is left alone.

- **Settings is for administrators only.** A reader account used to open
  it and could reach the API keys and the Jellyfin key through it. Until
  the account page exists, a reader's password is changed by an admin
  (Settings -> Accounts) or from the command line
  (`manage.py heslo <name>`).

- **Tidying tasks keep the versions.** Merging, archiving and the
  clean-up of foreign types delete or rename items; versions hang off an
  item by a foreign key and now go with it. An empty list of versions
  means "Jellyfin did not send any this time", not "the files are gone",
  so it no longer wipes anything.

- **Checkboxes and radios are round, filled with the same gradient as
  the buttons, and the cursor over them is a hand.** They were the
  browser's default ones - the only place in the app that still looked
  borrowed.

- **Two buttons on the sign-in page** instead of one clever one: you
  know which password you are typing, and a password to a local account
  never travels to Jellyfin. The Jellyfin buttons appear only when the
  admin has switched that sign-in on.

- **A warning when the connection to Jellyfin is in the clear.** Plain
  `http` to another machine is named in Settings; `https` and
  `localhost` say nothing, because there is nothing to warn about.

### Fixed

- **Switching the title charts stopped reloading the whole page.** With
  your own dates the small request behind the filter failed every time,
  and the page quietly fell back to loading itself again - so the filter
  worked, just slowly and with a jump to the top, which is exactly what
  that request exists to avoid.

## 1.6.9

Forgetting a viewer can be taken back, and Settings stops jumping to the
top after every click.

### Added

- **A forgotten viewer goes to a bin.** Deleting a viewer's history was
  final the moment the button was clicked, and that button sits right
  next to a dropdown - one wrong pick and the history was gone. The rows
  move to a bin instead: the viewer disappears from the statistics
  immediately, which is the point of forgetting, but a **Restore**
  button beside *Forget* brings the history back until the nightly
  clean-up empties the bin for good and rewrites the file. After that
  the same dialog names the backup taken just before the deletion, so
  nobody has to guess which file to restore from; when that backup is
  pruned, the entry disappears with it.

  The bin is built from the shape of the history table and missing
  columns are added at every start, so a column added later cannot make
  it silently lose part of a restored row.

- **A warning when a notification channel is switched on but empty.** It
  used to save without a word, so somebody could wait for alerts that
  had no way out. Saving now names the channels that are on and unfilled.

### Changed

- **Backups taken before a deletion have their own queue.** "Keep N
  backups" counted both kinds together, so forgetting three viewers in a
  day produced three extra backups that pushed out the nightly ones -
  the very backups the setting exists to keep. Each kind is now pruned
  on its own.

- **Settings stays where you are.** The page is long and every action
  reloads it, so each save ended with hunting for the place you were
  reading. All 44 buttons that reload the page now keep the scroll
  position, and so do the four places where a script loads the page -
  including a manually started task, which reloads itself minutes after
  the click.

### Fixed

- **The notification cards popped up an empty box.** Saving either card
  showed a message with no words in it, which read as an error nobody
  explained: those messages were written into the session as plain text,
  while the page reads a message and a level off a dict. They go through
  the usual helper now, and a test forbids the shortcut that caused it.

- **An untranslated string from Weblate showed as nothing.** Weblate
  writes untranslated keys as an empty string, and the loader took that
  for a translation - so a page in that language would have shown blanks
  where it should fall back to English. Empty values are skipped at load
  time, for every language file.

- **The clean-up of rows that do not belong in the library was too
  narrow.** 1.6.8 limited it to a list of container types, so a row of
  some other foreign type - a music video or trailer from an old import
  - would have stayed for good, archived afresh every night. It removes
  anything the sync cannot write again, with the one exception it must
  keep: a freshly added file Jellyfin still reports as `Video`.

## 1.6.8

### Fixed

- **On Jellyfin 12, movies in a collection went missing and the
  collection took their place.** Jellyfin 12 answers the library query
  (`Recursive` with `IncludeItemTypes=Movie,Episode`) by hiding movies
  behind their collection: instead of three films it returns one item of
  type `BoxSet` - despite the type filter. Jellyscope stored that item,
  so the movie library showed a collection and the films in it, with
  their watch history, were gone. Reproduced on Jellyfin 12.0.0 and
  fixed on three levels: every item query now sends
  `CollapseBoxSetItems=false` (verified: five films, no collection; no
  effect on 10.x); the sync never writes a container - collection,
  series, season, folder, playlist - whatever the server sends; and the
  clean-up of such rows runs after every full sync, so collections that
  already got into the database disappear on the first sync after
  updating, no restart needed. A freshly added file that Jellyfin still
  reports as `Video` is stored as before and corrects itself once
  Jellyfin sorts it.

## 1.6.7

### Added

- **A database backup before anything is deleted.** Both paths that
  delete on purpose - the nightly history clean-up and *Forget a viewer*
  in Settings - now begin with a backup, and if the backup fails nothing
  is deleted; the task log or the flash says why. A wrong retention
  limit or a click on the wrong name has a way back. With no backup
  folder set the copy goes to `data/zalohy` next to the database, and
  nothing is backed up when nothing would be deleted. The file is named
  `jellyscope-<time>-pred-mazanim.db`, so it is told apart from the
  nightly ones while pruning and restore treat it like any other.

  On PostgreSQL the backup is `pg_dump`, or Jellyscope's own export
  where `pg_dump` is missing or too old for the server - the same two
  paths the nightly backup task already had. Verified against a real
  PostgreSQL 16: backup, deletion, the deferred `VACUUM FULL`, and a
  restore of both backup kinds into a clean database.

- **Translations merged on GitHub are noticed.** Weblate merges land on
  `main` without a release, so a version check never saw them. The
  update check now also asks GitHub what changed under
  `jellyscope/translations/` since the running commit, and reports it
  as *New translations* in the footer and in Settings. An unknown commit
  (no git, a detached copy) means "don't know", not zero.

- **One *Check for updates* button in Settings.** The Version card had
  a link to GitHub and the advice to run a script. It has one button now;
  the reply says what was found - a new release, new translations, both,
  or nothing - and one row below it opens the same window as the footer,
  with the release notes and the one-click update.

### Changed

- **Forgetting a viewer no longer freezes the application.** After the
  rows are deleted the database file is rewritten so no trace of them
  remains (see 1.6.5). That rewrite locks SQLite for roughly a minute
  per 5 GB, and it used to run the moment the button was clicked. The
  click now only deletes; the rewrite is noted and done at the time set
  for the history clean-up task - the first occurrence after the
  request, whether or not that task is switched on. A nightly clean-up
  that rewrites anyway clears the note. Until then a trace of the deleted
  rows stays in the file, and the page and the flash say so.

### Fixed

- **The PostgreSQL fallback backup could not be restored.** The export
  Jellyscope writes when `pg_dump` is unavailable carried the SQLite
  schema (`AUTOINCREMENT`), which PostgreSQL rejects - so neither the
  in-app restore nor `psql -f` could read it - and it covered 8 tables
  of 12, leaving out library snapshots, API keys, login blocks and the
  dashboard layout. It now writes the PostgreSQL schema plus every
  column added by migration, backs up all tables, and the in-app
  restore runs the file straight through the driver instead of the
  query wrapper that rewrites question marks and percent signs in the
  data. Found by restoring on a real server; a test now keeps the table
  list in step with the schema.

- **"New translations" could have pulled unreleased code.** The update
  is a `git pull` of `main`. If `main` held a release whose CI had
  failed - tag pushed, no GitHub Release - the translation check still
  saw changed translation files and offered them, and the click would
  have pulled the rejected code with them. Translations are now
  reported only when `main` differs from the running commit in nothing
  but the translations folder, and the update itself re-checks after
  `git fetch` and refuses when more than translations would come down
  without a published release.

- **A phone could neither log out nor update.** Below 860 px the whole
  sidebar footer was hidden - version, licence, collector status, the
  signed-in account and *Log out* - and nothing stood in for it. The
  footer now sits at the bottom of the open menu.

- **Two backups within one second overwrote each other.** The name
  carries a timestamp to the second; a second backup in the same second
  now gets `-2`, `-3` appended instead of replacing the first.

## 1.6.6

Housekeeping: the same application, in fewer lines and with less waiting.
Nothing here changes what Jellyscope does.

### Changed

- **Settings opens in a fraction of the time.** On a history of 150 000
  plays the page took nearly seven seconds, while every other page took
  well under one. The six counts behind *Data tidy-up* were doing most of
  it, and the reason was duller than expected: the helper that turns a
  timestamp from the database into a number is called twice per row -
  three hundred thousand times per page - and it went through
  `datetime.strptime`, which re-reads the format string on every call.
  The format is fixed, so the value is computed from character positions
  instead: 4.8× faster.

  Cross-source duplicates are also no longer searched for when there is
  nothing to cross with. A group only counts if at least one of its rows
  came from an import, so an installation that never imported anything
  always got an empty answer - it just used to walk the whole history
  first.

  Measured back to back on one machine: `duplicate_playback_count`
  7 329 → 1 627 ms, `import_duplicate_count` 7 685 → 63 ms.

  The numbers stay live. Computing them nightly and showing a stored
  value was considered and turned down: they decide what somebody
  deletes, and a figure from yesterday is worse than a moment of waiting.

- **The Settings template is twelve files instead of one.** It was 2 246
  lines and twelve sections deep; finding a card meant scrolling past
  eleven that were not it. It is 98 lines of routing now, plus a file per
  section named after what it holds.

- **The routes are three modules instead of one.** `web.py` was 3 637
  lines. What every page needs - templates, context, flashes, the login
  guards - moved to `web_zaklad.py`, and the thirty-six Settings routes,
  a third of the file and sharing nothing with the pages that draw
  graphs, moved to `web_nastaveni.py`. `web.py` is 1 908 lines.

  Both splits are pure moves, and both were checked as such rather than
  read as such: the rendered HTML of all twelve sections, the route table
  and the responses of forty routes were captured before and after and
  compared. Nothing differs but the order in which `/openapi.json`
  describes the paths.

### Fixed

- **A setting's value reaches the log only where that is harmless.** The
  value used to be written unless the key name looked like a secret -
  which covered today's secrets and nothing else, so `notify_smtp_komu`
  and `notify_smtp_uzivatel` went in whole: an e-mail address and an
  account name, in a log whose purpose is to be forwarded when something
  breaks. The list is inverted now. A value is written only for settings
  known to be harmless, and anything else is recorded by name alone, so a
  new setting stays quiet until somebody decides it may be shown.

## 1.6.5

### Added

- **What changes the installation now leaves a line in the log.** Accounts
  could be created, deleted, promoted and have their password reset
  without a trace: `accounts.py` did not log at all. Neither did picking
  a database, deleting an item, or blocking an address by hand. When
  somebody later asks who deleted that account, there has to be somewhere
  to look.

  Settings are logged in one place — `db.set_setting()` — rather than in
  the twenty routes that write them, because the one route somebody
  forgets is the one they will need. Three rules keep it readable instead
  of noisy: a value that did not change is not logged, a secret is logged
  by name only (`setting notify_smtp_heslo changed`, never the value),
  and bookkeeping keys are skipped — the detected Jellyfin version is
  rewritten on every call to the server.

  The logging sits in the function that makes the change, not in the
  route. `create()` is also called by first-run setup and by the tests;
  a log line in the route would leave those paths silent.

- **Clearing out the history has its own section.** It used to be a
  subheading inside *Scheduled tasks*, a card that was doing three jobs
  at once. Deleting history is the only one of them that destroys data,
  so it stands on its own, with its own save button. Tasks and backups
  stay together — a backup *is* a task.

- **Forgetting a viewer asks in a window of its own.** The browser's
  `confirm()` can only show a bare sentence, so it had to ask twice - the
  second question existed only to name the viewer. A window of our own
  shows the name and the number of records at once, so one question says
  more than those two did.

  The button that opens it cannot submit anything: it is a plain button
  belonging to no form, and only the confirmation inside the window sends
  it. That is the part worth keeping. The confirmation used to be a named
  function that shared its name with the `<select>` beside it - an element
  with an `id` is a property of `window` and shadowed the function, so
  the call failed, the browser skipped the handler and did the default
  thing instead: it submitted. A viewer's history went without a single
  question. A broken guard that behaves like no guard is worse than none;
  now a broken script means nothing happens at all.

- **Data collection and Interface are split into cards too.** Both were
  one card with several subheadings — several different decisions under
  a single title. Each subheading is a card now, with its own save
  button, and the umbrella heading *Appearance, long lists and the map*
  is gone: after the split the cards name themselves.

### Fixed

- **Deleted history stayed readable in the database file.** SQLite's
  `DELETE` only drops the row from the page index; the bytes lie there
  until something overwrites them. "Forgotten" history could be read
  straight out of the file — which is how one viewer's history was
  recovered after being deleted by accident, and a hole every other day
  of the year. Both paths that delete on purpose now rewrite the
  database afterwards, and the test checks the file itself rather than
  asking the application: the name went from 236 occurrences to one, and
  that one is the account in Jellyfin's user list, which stays on
  purpose.

- **A day count of zero would have deleted everything.** Writing the
  test that deletion removes only what it should turned this up:
  `smaz_stare(dnu)` took its argument as given, so `0` put the boundary
  at *now* and took everything except the session in progress, a
  negative number put it in the future and took that too, and a large
  enough number crashed. Only the scheduled task calls it, and without
  an argument, so it could not happen — but that is luck, not a guard.
  The limit now sits where the number becomes a boundary, and it guards
  the dangerous values rather than the small ones: a five-day boundary
  written in code still works, because whoever wrote it meant it.

- **One action, two identical log lines.** Forgetting a viewer was logged
  by `odklizeni` and again by the route.

- **A message that could not be translated.** The confirmation after
  forgetting a viewer was assembled with an f-string, so it appeared in
  Czech in an English interface. The test that guards against exactly
  this could not see it: it read only plain string literals and stepped
  over f-strings without a word. It now reports them.

## 1.6.4

### Security

- **The demo could be turned into an open redirect.** When a button is
  pressed in the demo, the application answers with a notice and sends
  the visitor back where they came from, taking the address from the
  `Referer` header. That header comes from outside, and the check on it
  asked whether it *starts with* our own address - which
  `https://jellyscope.cz.example.org/` does. A link that looked like it
  led to the demo could land somewhere else entirely, which is how
  passwords get collected.

  Only an installation running in demo mode (`JELLYSCOPE_DEMO=1`) ever
  reached that code, so an ordinary install was never exposed.

  The host is no longer compared at all. Only the **path** is taken from
  the header, so the redirect stays on the same server whatever the
  header says - and it does not break behind a proxy, where the scheme
  and port differ from what the application sees. `//example.org/x`,
  which a browser reads as another server despite looking like a path, is
  refused as well.

  Found by reading the code, not by the scanner: CodeQL passed over it,
  presumably satisfied by the check that was there.

## 1.6.3

### Fixed

- **The bars in every chart had turned grey.** Codecs, dynamic range,
  watch time by user - all of them drew in the muted fallback colour
  instead of their own. It arrived with 1.6.2: colours started being
  checked before they were written into an attribute, and the check was
  handed the finished `linear-gradient(...)` that a bar is filled with.
  A gradient is not a colour, so it failed the check and fell back to
  grey - every bar in the application at once.

  The colour is now checked where it comes in, and the gradient is built
  from the checked value afterwards, which is the right way round: what
  the application composes for itself was never the part worth
  distrusting. The guard against a colour becoming code is untouched and
  still tested.

  There is a new test for the other half of the question. The security
  tests prove no chart emits anything executable; nothing proved a chart
  emits the colour it was supposed to, which is why this shipped. That
  test now exists, across every chart, and it was checked against the
  bug itself - with the fault put back, it fails.

## 1.6.2

### Added

- **Jellyfin 12 is supported, and 10.x still is.** Version 12 stopped
  reading the old authentication headers (`X-Emby-Token` and its
  relatives) and removed the `/emby/` and `/mediabrowser/` paths
  altogether. Jellyscope was never affected: it has always asked with
  `Authorization: MediaBrowser Token="..."`, which is what 12 wants and
  what every 10.x understands as well. The old headers were being sent
  alongside it, and they now go only to a server old enough to want them.

  The version comes from the server itself, out of `/System/Info`, and is
  re-read whenever the connection is saved or tested. Where that answer
  cannot be trusted - a proxy that rewrites it, a build that reports
  something odd - **Settings → Jellyfin** has a *server generation*
  dropdown to say it outright. The address and the key stay the same
  either way; only the shape of the questions changes. Switching the
  dropdown forgets the detected version, so that a manual choice and a
  stale detection cannot end up contradicting each other, and the page
  says so when the two disagree.

### Fixed

- **A colour from outside can no longer become code.** Charts are put
  into the page as HTML, without the escaping that protects everything
  else, and a colour was written into the `style` attribute exactly as it
  arrived. Today every colour comes from the application's own templates,
  so nothing was exploitable - but a single future chart taking its
  colour from the database or from a translation would have been, and
  that is not a thing to leave lying around. A colour now has to look
  like a colour to be used at all; anything else is drawn in the muted
  default.

  The security tests no longer check a couple of known places. They fire
  four kinds of attack into **every** text that enters a chart - labels,
  ids, country codes, place names, colours - across all ten charts, and
  read the result the way a browser would, by taking the HTML apart into
  tags and attributes rather than searching it for a string. The test
  also proves it can fail: it repeats the whole run with the escaping
  switched off and insists the holes reappear.

### Changed

- **Two rules that lived in two places now live in one.** The number of
  decimal places on a size was written once for the tiles and again for
  the charts - which is precisely why they drifted apart, the chart
  saying `29 TB` next to a tile saying `28.8 TB` about the same library.
  Reading the manually chosen Jellyfin generation had likewise grown a
  second copy beside the first.

## 1.6.1

### Fixed

- **An unfinished translation fell back to Czech.** Czech is the source
  language, so a sentence nobody had translated yet was shown in Czech -
  which helps a Czech and nobody else. Somebody who switches to German and
  finds the translation two thirds done now reads the rest in **English**,
  the language nearly everyone who got this far can read. Czech appears
  only where even the English is missing, which the tests do not allow.
  The application log falls back the same way; it gets forwarded to other
  people when something breaks, so it matters there even more.

  This lands before the first translations arrive on purpose: a language
  that is 70 % done should read as English for the rest, not as a language
  its readers cannot even guess at.

- **The translation files are indented the way Weblate writes them.** Four
  spaces, not two. It is a cosmetic difference with an uncosmetic effect:
  the first commit from a translation tool would have rewritten every line
  of the file, and the one sentence somebody actually translated would
  have been lost in a diff of 1 176 changed lines.

## 1.6.0

### Added

- **A read-only API, with keys of its own.** A source of numbers for
  Grafana, Homepage or a script of your own - not a second way into the
  data. Four addresses: an index, watching over a period, what is playing
  right now, and the library with its growth and free space. Everything
  under `/api/` is a `GET` and anything else is refused by the router
  itself, rather than by a rule somebody has to keep in mind.

  Keys are made in **Settings → API**, one per tool, each revocable on its
  own so a leak costs one dashboard rather than all of them. A key is
  shown **once**, when it is made: what is stored is a SHA-256 hash and
  the first five characters, so afterwards not even the page can read it
  back. It travels in the `Authorization` header and is refused in the
  query string - that is where it would end up in a proxy log, in the
  browser history and in a forwarded link. The documentation is behind a
  button in the corner of that section, with a `curl` line carrying the
  address of that particular installation.

- **Translations are files anybody can edit.** They used to be two Python
  dictionaries, which meant a translation could only be contributed by
  somebody willing to edit Python - and no translation tool could see them
  at all. They are JSON files now, one per language, with the Czech source
  beside them.

  **A new language is one file.** The list in Settings is built from what
  is in the folder, so nothing in the code has to change to add one, and a
  file that will not parse takes down only its own language rather than
  the application. Translating needs neither git nor Python:
  <https://translate.jellyscope.cz/>. See
  [TRANSLATING.md](TRANSLATING.md).

- **Clearing out the history.** A daily task deletes playbacks older than a
  limit set beside it, and Settings says how many rows that limit would
  remove before anything is saved. Off unless switched on, and it stays
  off across updates: deleting data must never start on its own. Playback
  that is running is never deleted. The same section can forget one
  viewer - everything recorded about them goes, and nobody else is
  touched.

### Fixed

- **The log was only ever translated into English.** It reached straight
  for the English dictionary, so any other language would have kept
  writing Czech even where a translation existed.

- **A 401 from the API did not say what to authenticate with.** The error
  handler was dropping an exception's headers, `WWW-Authenticate` among
  them.

## 1.5.3

### Fixed

- **The growth chart said 29 TB where the tile beside it said 28.8 TB.**
  The number in the bubble went through the formatter the charts use for
  hours, which drops the decimal above ten - right for "29 h", half a
  terabyte out for a library. The data was never wrong; it is accurate to
  about ten gigabytes, and the precision was lost only on the way to the
  screen. Sizes in TB, GB and MB now carry the same decimals as the tile,
  so the two numbers about the same thing read the same. The axis is
  unchanged: "29" on it is a mark on a scale, not a reading.

- **"Total size" said nothing about what it left out.** An item without
  technical data enters the sum as zero - which is exactly what happens
  after a file is replaced, when the technical data is deliberately
  discarded and waits for the next analysis. Until then the total is a
  lower bound, and the tile now says so: *"N items without a size are not
  counted in"*. The number that explains the gap is better than the gap.

### Changed

- **The library's current size is worked out in one place.** The tile, the
  daily snapshot and anything else asking "how big is the library right
  now" used to each carry their own copy of the query. They cannot drift
  apart any more.

## 1.5.2

### Changed

- **The licence is now AGPL-3.0.** Jellyscope stays free, and anyone who
  passes it on has to pass on the source with it - including when they run
  a modified version as a service over the network. That last part is why
  Affero and not plain GPL: this is a web application, and plain GPL would
  let someone host a closed fork without ever publishing anything. Selling
  it is still allowed; hiding the source is not.

  Versions up to and including 1.5.1 were released under MIT and stay that
  way - a licence already given cannot be taken back.

### Added

- **The number of streams shown on a phone is set separately.** A stream
  card takes the full width there, so four of them push everything else
  off the screen; the previous single setting had to be a compromise
  between a phone and a monitor. The server sends both and CSS picks,
  because the server cannot know what someone is looking at.

- **The capacity entered by hand is given in GB or TB.** A thirty-terabyte
  array was `30000` in a field that then read the number back the same way;
  now the unit sits beside the number and the value is stored in bytes
  either way. Settings saved before this keep their size - they are shown
  in terabytes when they are large enough to deserve it.

- **A public demo can run in Docker.** `docker compose` now passes
  `JELLYSCOPE_DEMO` to the container, and the demo data is prepared by
  whichever launcher starts the application - not only by `demo.py`. The
  image runs `run.py`, so a demo container used to come up empty *and*
  locked: nothing is saved in demo mode, so not even an administrator
  could be created in it. Off unless asked for; a normal installation
  notices nothing.

- **The history can be cleared out on a schedule.** A tool that remembers
  who watched what and when will sooner or later be asked to forget some
  of it. A daily task deletes playbacks older than a limit that is set
  alongside it, and Settings says how many rows that limit would remove
  before it is saved - "40,000 playbacks deleted" is a bad moment for a
  surprise. It is **off unless switched on**: deleting data must never
  start on its own, least of all because somebody updated. Playback that
  is running is never deleted; it belongs to the collector, which would
  only write it again a moment later, without its beginning.

- **One viewer can be forgotten.** Everything recorded about them goes,
  and nobody else is touched. The account itself lives in Jellyfin, so the
  next synchronisation sees it again - without the history.

- **How many viewers the language page shows is set separately for a
  phone.** The same split the now-playing card already had: a bar takes
  the full width there, and the legend and the table below it start a
  screen further down. Above the phone limit the rest hides behind a
  button that opens the same viewers in a window - on a wide screen every
  bar stays where it was.

### Fixed

- **A container had to be told, by hand, who owns its data folder.** The
  application runs as UID 10001; the folder on the host belongs to whoever
  created it, which on a first `docker compose up` is Docker itself, as
  root. So the first start of a fresh installation ended in an error that
  had to be fixed on the host - a `chown` in a place nobody was told to
  look. The container now corrects that owner itself, but only while the
  folder is **empty**, which is exactly the one Docker made seconds
  earlier: nothing in it can be overwritten, because there is nothing in
  it. A folder that already holds something is left alone even when the
  owner is wrong - those are somebody's files, and rewriting them behind
  their back is not the container's business. It starts as root for that
  one step and drops the privileges before the application starts, so the
  application itself still never runs as root.

- **A container that could not reach its data left nothing but a log
  entry.** The process ended, so the browser said "cannot connect" and the
  reason sat in `docker compose logs`, where nobody had been sent. The
  application now starts anyway and answers every address with one page:
  what happened, which folder it cannot write to, and the command that
  fixes it. It answers 503, so a health check still sees that something is
  wrong instead of reporting a healthy container with a broken
  application.

  A database that opens but cannot be written to counts as the same
  failure. SQLite opens a read-only file happily and gives way only on the
  first write - so the crash arrived two lines later, on a `PRAGMA`, past
  everything that was watching for it.

  The page says all of it in Czech and in English, the message itself
  included. It cannot be translated afterwards: which language to use is a
  setting, and settings live in the database that could not be opened.

- **A container that could not open its database said only "unable to open
  database file".** No path, no reason, no advice - and in Docker this is
  the most common first-start failure there is: the mounted folder belongs
  to root while the container runs as UID 10001. The message now names the
  file, says what is wrong with it, and inside a container adds the command
  that fixes it. Without a database the launcher stops instead of raising a
  stack trace from the depths of sqlite3.

- **The now-playing card was wider than the screen.** The grid asked for
  380px columns even where only 358 were available, so the card ran past
  the right edge and its border was cut off. Every fixed grid minimum in
  the stylesheet now says `min(X, 100%)` - the same trap had already been
  fixed in two other places, and this sweep covers the rest.

- **The estimate ignored a capacity entered by hand.** "Space runs out in
  N days" was worked out from the free space stored in the last snapshot,
  which is written during a sync - so a capacity typed into Settings did
  nothing until the next one ran. It uses the current value now, and the
  number changes as soon as it is saved.

- **The growth curve still read higher than the library size.** Both
  numbers were right; they were in different units. The chart plotted
  gigabytes ("13 101") while the tile beside it said terabytes ("12.8 TB"),
  so the curve looked like it ended a thousand times above the size it was
  supposed to match. The chart now picks its unit from the data - terabytes
  once the library passes one - so the end of the curve and the tile say
  the same thing.

- **The calendar was cut off on a phone.** In the custom-period window the
  "To" field sits in the second column, so its calendar started 202px in
  and ran 80px past the screen edge. The two date fields are stacked on a
  narrow screen, and the calendar never exceeds the width it has.

## 1.5.1

### Fixed

- **The application ran off the screen on a phone.** The item detail and
  the now-playing card pushed the page sideways, so it had to be scrolled
  or zoomed out. Both are the same trap: a grid item has `min-width: auto`
  and will not shrink below its own content, so a row of three tiles
  stretched a 358px column to 520px. The card grid also asked for 340px
  columns where only 288 were available. Measured at 320, 360 and 390 px -
  no page scrolls sideways now.

- **The growth curve ended higher than the library size beside it.** Items
  that are gone from the library but carry no last-seen date never left
  the reconstruction, so they were counted forever; and an item archived
  today was still counted in today's point, because it was removed the day
  *after* it was last seen. It is removed on that day now, and an item
  whose departure is unknown is left out - understating the past is a
  smaller wrong than misstating the present. The last point of the curve
  equals the library size to the byte.

- **The disk-space warning described only half the story.** It now says
  that where neither the application nor Jellyfin can see the storage, the
  capacity can be entered by hand in Data collection.

- **The time for the weekly summary drifted away from its label.** That
  picker is right-aligned for the tasks table; in a form it belongs under
  its own label.

### Changed

- **Settings sections are split into groups.** A card with ten fields in a
  row reads as one setting to anyone who does not read every word - it was
  not obvious that "Appearance" and "Time in charts" are separate things.
  Each group now has a heading in the accent colour with a rule above it.

## 1.5.0

### Added

- **The library remembers what it looked like yesterday.** Until now
  Jellyscope kept only the *current* state of the library - size, item
  count, the mix of codecs - and had history for playback alone. A daily
  snapshot is written after every successful library sync, and out of it
  comes a curve of how the library grows, how much arrived over a period,
  and where it is heading.

  The estimate speaks in two ways, because free space is only knowable
  where the application can see the files: "space runs out in N days" when
  it can, and "in a year it grows to X" when it cannot. Promising the
  first where only the second holds would be worse than saying nothing.

  A falling or flat library gets no estimate at all - and nothing divides
  by zero. It is on the Library page, which now carries the same period
  filter as every other page - including a custom range - and the choice
  made there holds across the application. In the custom overview the
  section follows the period chosen there.

  **The curve does not start on the day you upgrade.** Snapshots can only
  be written from now on, but the past is already in the data: an item
  carries the date it was created, and one that is gone from Jellyfin is
  archived rather than deleted, with the last sync that still saw it. The
  library before the first snapshot is reconstructed from those two, so
  "how much did it grow over the year" has an answer today.

  The reconstruction is drawn in its own colour and the chart says where
  it ends, because it is not a measurement: it describes the past with
  today's sizes, so a film re-encoded from 30 GB to 10 GB was always small
  in it, and anything deleted before the first sync is not in it at all.
  Its shape - when things arrived - is sound, so it is anchored to the
  first measured value; otherwise there would be a step at the joint,
  claiming the library lost half its size overnight.

- **"Other" on the track combinations card opens.** The card shows the
  four most common combinations and sums up the rest into a row that said
  a quarter of the library was somewhere else and gave no way to look.
  That row is now a link to a window with the complete list, plus a total,
  so it is clear what the percentages are counted from. They are counted
  from the same whole as on the card, so a share means the same thing in
  both places.

- **Two periods side by side.** "Compared to the previous period" has
  been on the Overview for a long time, but the previous period is not
  something you choose - it is the window immediately before the one you
  picked. It cannot answer "was August better than December?".

  The new Comparison tab takes any two periods and puts the same
  statistics next to each other: watching, how the server delivered the
  content, network, which language was watched in, how the library grew,
  and the top titles and users of each period. Two things it refuses to
  leave unsaid - that the periods overlap, so part of the playback is
  counted in both columns; and that they are not the same length, where a
  total will favour the longer one almost every time, which is why the
  daily average sits right under it.

  Languages carry their share next to the hours, because a share is the
  one honest number when the periods differ in length: a year holds more
  hours of everything than August, but the ratio of Czech to English does
  not move with it. A language present in only one of the periods is
  listed anyway - "in December they started watching in Slovak" is the
  answer people come here for.

  Library growth is compared from the daily snapshots, so it only works
  for periods where snapshots exist. Where they do not, the page says so
  instead of showing zeros: "nothing was added" and "we do not know" are
  different statements.

  A difference between two percentages is stated in percentage points:
  "from 12 % to 18 %" is +6 points, not +50 %.

- **Settings has a dropdown on a phone.** Eleven tabs wrapped into six
  rows - 207 px of links before the first setting was visible. The list
  now takes one row. Only the tabs the dropdown replaces are hidden; the
  three on a library detail have no dropdown and stay as they are.

- **A pass over the whole application on a phone.** Measured at 390 px
  in a real browser, not guessed at. Nothing overflowed sideways, but four
  things were wrong and are now fixed.

  The menu is behind a burger. Ten items were 794 px wide in a 390 px
  window, so two were visible and the rest needed a sideways scroll that
  nothing hinted at; the button now carries the name of the page you are
  on. It is a checkbox and a label rather than a script, so the menu opens
  even if the script does not run - it is the only way to the other pages.

  Chart axis labels were rendering at 4.6 px. An SVG has a fixed 760-unit
  viewBox and stretches to the width available, so the text shrinks with
  it; on a narrow screen the labels are now larger in viewBox units and
  come out at about 9 px.

  The Comparison table turned into a list of blocks. Four columns do not
  fit on 350 px, and the horizontal scroll pushed the Difference column
  off the screen - the one thing the page exists for.

  History rows were 120 px tall and a third of the table was visible.
  The columns that do not fit are hidden on a phone, which brought the
  rows down to 39 px.

- **Notifications.** Jellyscope is passive: you open it when something
  interests you. The collector, though, asks Jellyfin every few seconds,
  and when its token expires the application keeps running, the pages look
  normal and history quietly stops. You find out three weeks later from a
  hole in the chart - and that hole is mostly permanent, because an
  imported history carries neither the track language nor the bitrate.

  Settings has a Notifications section: first the channels (SMTP, Discord,
  Telegram), then what to speak up about. Each channel has a Try button,
  because otherwise the configuration is only ever verified by something
  going wrong - which is exactly the moment it has to work.

  Three things can be switched on, and no more: the collector runs but
  collects nothing; the disk is filling up (from the library growth, so
  only where the application can see the files); and a weekly summary,
  which is the one that is not a fault. There is deliberately no "a new
  version is out" - it is in the interface already, and as a message it is
  the kind of noise that makes people stop reading the channel, and then
  miss the first item.

  A message goes out **on change only**, including when it starts working
  again. A watchdog that repeats itself every fifteen minutes gets muted
  on the first day.

  These notifications only report on what happens inside Jellyscope: if
  Jellyscope is not running, neither are they. The page says so rather
  than letting you find out the hard way, and points at a watchdog like
  Uptime Kuma for the other question. Passwords, webhooks and tokens never
  travel back into the page, and an empty field leaves a stored one alone.

### Fixed

- **A bot token could end up on the screen.** An error from the HTTP
  client carries the whole address in it - and for Telegram the address
  contains the bot token, while for Discord the address *is* the webhook.
  That message is stored and shown in Settings, so a failed send put the
  token on the page. Error text is now masked against the stored secrets,
  and the HTTP client's own logger is pinned at WARNING: it logs every
  request with the full address, which is harmless until somebody starts
  the server with --log-level debug.

- **A file named "@everyone" no longer pings a Discord channel.** Titles
  from the library travel into the message, and a name is a name, not an
  instruction. Mentions are switched off for the whole message; the text
  itself is not trimmed.

- **Transcode was counted two different ways on the same page.** The
  delivery chart recognises a transcode by the start of the name, because
  imported history (Playback Reporting) writes values like "Transcode
  (v:h264 a:direct)". The transcode share, the per-user transcode hours
  and the transcode pages matched the name exactly, so for an imported
  history they could show "Transcode: 1 h" and "Transcode share: 0 %"
  right next to each other. There is now one condition for all of them.

- **Free space was measured on the wrong disk under PostgreSQL.** To find
  out how much room is left, Jellyscope looks at the largest file in the
  library and asks about the disk it sits on. SQLite sorts NULLs last on a
  descending sort, PostgreSQL puts them first - so on PostgreSQL the query
  returned the first file of unknown size instead of the largest one, and
  the estimate could describe a different disk entirely. Nothing crashed,
  which is what made it worth catching.

## 1.4.4

### Fixed

- **A link cannot crash the network page any more.** The range picked by
  dragging travels in the address, and an instant outside the calendar -
  `od_ts=-1e308`, or plain `0` on Windows - reached the conversion and
  raised. A nonsensical period is not a server error; it is simply no
  period.

### Changed

- **Two more response headers.** `Permissions-Policy` turns off the camera,
  microphone, location, payment and USB, none of which the application
  uses. `Strict-Transport-Security` is sent only where it already runs over
  HTTPS (`SECURE_COOKIES`), without `includeSubDomains` or `preload` -
  those are decisions about a whole domain, not about us.

## 1.4.3

### Fixed

- **Switching the custom overview on now leads somewhere.** The tab stays
  hidden while the overview is empty, which left an administrator who had
  just switched it on with no way in to build it. Administrators see the
  tab from the start, everyone else only once there is something in it -
  and switching it on goes straight to the page, because turning it on and
  filling it are two halves of the same act.

## 1.4.2

### Fixed

- **The application starts again on PostgreSQL.** The setting carried over
  in 1.4.1 was read from the row by position, and PostgreSQL hands rows
  back as dictionaries - so `KeyError: 0` stopped the process before it
  could serve anything. It is read by column name now, which both
  databases understand.

## 1.4.1

### Fixed

- **The custom overview no longer breaks the whole application on
  PostgreSQL.** Its layout query used `account_id IS ?`, which SQLite
  accepts as a null-safe comparison and PostgreSQL rejects as a syntax
  error - and because the layout is read on every request, for the tab in
  the menu, one bad query took every page down with it.

### Changed

- **Checking for a new version is a scheduled task.** Switching it on and
  picking the hour belong with everything else that runs on its own, so
  they moved to Settings › Tasks; General keeps the button that checks
  right now. Whoever had the check switched on keeps it on - the old
  setting is carried over on start.

## 1.4.0

### Added

- **Build your own overview.** Settings › Interface can switch on a tab
  where the statistics that already exist get arranged into a page of your
  own: click to add a section, drag to order it, save. Signing in then
  opens that page instead of the Overview.

  It is composed by an administrator and applies to the whole server, like
  every other setting - and the layout is stored so that per-account
  overviews can arrive later without moving any data. Until something is
  in it the tab stays hidden, and only the sections actually placed there
  get computed: the Overview works out ten of them for everyone, whether
  they are looked at or not.

  It is composed on a grid of tiles in a dialog, not by hauling the real
  panels around: a panel holding a chart is half a screen tall, so while
  dragging one there was no way to see where it was heading, and the whole
  layout never fit on one screen. On the grid it does. Each panel is a
  third, a half or the full width, and nothing shifts while a tile is
  being dragged - a bar marks where it will land, and the tile moves on
  release.

  Twenty-three sections to start with, from every page that had something
  worth reusing - including the Overview's headline figure and its row of
  tiles. The sections are the same templates their original pages use, so
  the two places cannot drift apart.

### Fixed

- **The file analysis fills in the dynamic range as well.** What Jellyfin
  says about the range - the only place Dolby Vision shows up when ffprobe
  is older than ffmpeg 5 - was written by the library sync alone. Running
  "Analyse files" therefore changed nothing about it, which is the exact
  opposite of what the task's name promises: people go there for technical
  data. It now asks Jellyfin for the titles whose range it does not know
  yet, once each.

## 1.3.5

### Fixed

- **Dolby Vision is recognised as Dolby Vision.** Jellyfin's `VideoRange`
  field knows only SDR and HDR, so a Dolby Vision file arrived as ordinary
  HDR - the "SDR / HDR / Dolby Vision" breakdown never had a third column.
  The profile is read from `VideoRangeType` now. (It counted towards HDR
  before and still does; it is simply no longer hidden inside it.)

- **Dolby Vision survives an older ffmpeg.** Reading DV out of a Matroska
  file needs ffmpeg 5; before that, ffprobe reports no side data, no codec
  tag and profile "Main 10" - nothing that says Dolby Vision - so a file
  Jellyfin describes as "Dolby Vision Profile 8.1" was measured as plain
  HDR. What Jellyfin says is now kept beside what ffprobe measured, and DV
  counts as found when either of them sees it: neither reports it by
  mistake, but both can miss it.

- **"Unknown" is no longer sold as a dynamic range.** Jellyfin can answer
  "Unknown" and that answer was stored verbatim, so the chart grew a column
  literally named Unknown next to the one for missing data. Both now read
  "unknown", and DOVI is written out as "Dolby Vision" instead of the
  internal shorthand.

- **The language from a filename is actually used.** The step that guesses
  from a name like "Film.2002.DVDRip.XviD.AC3-2.0.CZ.avi" ran on the
  tracks Jellyfin could not help with - but the step before it marks
  exactly those tracks as asked-and-unknown, and the search excluded
  anything marked. The list was therefore always empty, and the guess only
  ever happened when Jellyfin failed to answer at all.

- **Files with no analysed tracks get a chance too.** When the file itself
  was never readable - a library the container cannot reach - there were no
  tracks to look at, so those titles stayed in "Files with no language"
  forever, even when the name said "CZ" plainly.

- **Searching in "Files with no language" no longer errors out.** The
  listing joins libraries, which have a `name` column of their own, and the
  filter did not say which table it meant. Counting worked, listing did
  not - so the page broke the moment anyone typed into the search box.

## 1.3.4

### Added

- **Drag across a chart to pick a range.** Seeing a spike raises the next
  question by itself - what was that? Dragging over it sets the marked
  stretch as the page's period, so the tables below answer. The live
  network curve takes the exact stretch, down to the minute; charts that
  only know days round to whole days rather than pretend to be finer.

### Changed

- **Time in charts can be exact to the minute.** Above ten hours the
  decimals are dropped, so a day with 34.52 hours reports 35 - almost half
  an hour that was never there. Settings › Interface now offers hours and
  minutes ("34:31") instead, because "34.5 h" does not say half an hour to
  anyone either. Rounded stays the default, and axis labels stay round
  in both cases: there "40" is a mark on the scale, not a reading.

- **A tooltip reports the peak of the band it covers.** With a point every
  few minutes there are more points than hover targets, and a target used
  to show the value of the first point under it - so even a well-aimed
  mouse could read a neighbour's number instead of the spike. It also says
  how many streams made the peak up: three people, or one film in 4K.

- **The curve is less dense over long windows.** A point every five
  minutes made a week look like fur. Six hundred points at most: still
  five minutes over a day, a quarter of an hour over a week - a spike wide
  enough to hit.

- **A fixed period ends where it says it ends.** The live curve always ran
  up to "now", even when the filter asked for two days last week - so the
  chart showed something other than the switcher above it.

### Fixed

- **A period shorter than a day compares with the same length.** The
  comparison window was derived from the number of days, which a two-hour
  selection rounds up to one - so two hours were held against a whole
  previous day and every "vs previous period" arrow reported a collapse
  that never happened.

- **A single day still draws.** One point has no neighbour, so the line
  had no width and the chart looked empty even though the day had hours
  in it. It now spreads across the plot, which is what one point means.
  The sparkline under the headline number stays away instead of leaving
  a blank strip - one day is not a trend.

- **A custom period is read in the application's time zone.** "20 August"
  was taken as UTC, which in central European summer means from two in the
  morning to two in the morning the next day. The same slip moved the
  calendar of the day-by-day chart by a day, and a custom period shifted
  back every time you moved to another page.

## 1.3.3

### Changed

- **The live network curve draws what happened, not a smooth version of
  it.** Two things made it lie. One point stood for 56 minutes over a
  week, and since each point keeps the peak of its slice, a ten-minute
  stream was drawn as an hour of traffic. And the line was drawn as a
  smooth curve, which invented a gradual rise where a stream had simply
  started.

  A point is five minutes now (a thousand and a half of them at most), and
  the line is drawn as **steps**: it holds its value and jumps when
  something starts or ends — which is what concurrent throughput actually
  does. Quiet time reads as a flat zero rather than a slow descent to it.

  Measured on a ten-minute stream in a week-long window: sixteen minutes
  of chart instead of fifty-six.

- Hover targets in charts are capped at two hundred. At a point every five
  minutes there would be one and a half thousand of them, each carrying
  its own tooltip — nearly a megabyte of HTML for one card, and a mouse
  cannot hit a target narrower than a few pixels anyway.

### Fixed

- **A replaced file kept the technical data of the old one.** Swap a file
  in Jellyfin and it becomes a new item with a new id; Jellyscope
  recognises the title, merges the two and keeps the history — that part
  worked. But it merged by *renaming* the old row to the new id, so the
  codec, resolution, size and languages of the file that no longer exists
  came along with it.

  The damage was done by one of those columns: `tech_source`. The file
  analysis only takes items that have no technical data yet, so this one
  was skipped — during the quick sync, during the nightly run, forever.
  On the detail page it showed the old file's numbers, or nothing at all,
  and only *Reload the metadata* by hand fixed it.

  Merging now clears the technical data, because it describes a file that
  is gone. The quick sync measures the item in the same run, which is what
  it had been trying to do all along.

## 1.3.2

### Added

- **The Network page shows what is flowing right now.** A card with the
  current throughput, how many streams make it up and how many of those
  are transcoding, with the recent curve beside it. It refreshes itself,
  by the same mechanism the "now playing" card on the Overview uses —
  which is now shared rather than written twice.

  The curve follows the period chosen at the top of the page, never
  shorter than a day: an hour-long window said "nothing is flowing" on an
  evening when it had been flowing the whole time, just because the last
  episode had ended a minute ago. One point covers a minute over a day and
  an hour over a week; the card says which.

  Paused playbacks stay out of it — of the number and of the curve. A
  paused stream ends where it was last actually playing, so the time it
  did play stays in the curve while the pause adds nothing. And a playback
  that is running counts even when the collector last reported a while
  ago; without that the curve would fall to zero while the number next to
  it showed a full stream.

- **The users in "who streamed the most" lead to their detail.** The same
  people are clickable in the Overview league table, so on the Network
  page it looked broken rather than deliberate.

### Changed

- **The long chart became "peak by day".** One point per day, the highest
  concurrent throughput of that day. Over a month the old chart cut the
  period into 120 slices, so one point stood for five hours and the peak
  inside it — which looked random, because a single spike could be one
  hour out of six. The question "how much flows at once" belongs to the
  live card; the question "how busy was Tuesday" belongs here.

- **Transcoding variants in the delivery bar have their own colours.**
  Shading one colour was the previous attempt and it failed in practice:
  the segments are a few pixels wide and three shades of one brown are
  indistinguishable there. The first variant keeps the orange of its role,
  so it still reads as transcoding; the rest take colours from the same
  palette the language statistics use.

## 1.3.1

### Added

- **The application has its own time zone**, in Settings → General. Until
  now every time came out in the zone of the machine, so a server running
  in UTC moved the evening peak in the charts two hours away from the one
  people lived through — and nothing could be done about it without
  touching the host or the container.

  What is stored is the **name** of the zone, not an offset in hours: the
  offset changes through the year, and a stored "+2" would be wrong for
  half of it. An empty field means "whatever the server says", which is
  what it did before.

  The zone applies to every time at once, because there is one place that
  decides. Splitting into days and hours is done by SQL, which reads the
  zone of the *process*, so it is set into the environment at startup too
  — that part therefore needs a restart. On Windows a process cannot be
  moved to another zone at all; the times are still right, the split into
  days follows the machine, and the log says so.

### Fixed

- **The delivery chart left transcoding variants grey.** Imported history
  carries more detailed methods — `Transcode (v:h264 a:direct)` — and the
  colour was matched on the exact name, so anything detailed fell through
  to "unknown". The match now looks at the beginning of the name, and a
  second variant of the same kind is shaded rather than recoloured: the
  colour still says "transcoding", the shade tells the variants apart.

### Changed

- **The headline number sits above its curve again**, in the new
  appearance as well. Beside it the card was shorter but the curve
  narrower — and the curve is where the shape of the month shows. Width is
  worth more here than the height it saved.

- **The network chart says how long one slice is** (`one slice · 5 h`).
  Nothing is sampled on a fixed clock: the starts and ends of playbacks
  are walked through and each slice keeps its peak. Without that number
  the curve looked random — one spike can be a single hour out of six.

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
