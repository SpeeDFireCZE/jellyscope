# Jellyscope

[![tests](https://github.com/SpeeDFireCZE/jellyscope/actions/workflows/tests.yml/badge.svg)](https://github.com/SpeeDFireCZE/jellyscope/actions/workflows/tests.yml)
[![licence: AGPL-3.0](https://img.shields.io/badge/licence-AGPL--3.0-blue.svg)](LICENSE)
[![demo: jellyscope.cz](https://img.shields.io/badge/demo-jellyscope.cz-3987e5.svg)](https://jellyscope.cz)
[![translate: Weblate](https://img.shields.io/badge/translate-weblate-2eccaa.svg)](https://translate.jellyscope.cz/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

> [!NOTE]
> **Jellyfin 12 is supported**, and so is 10.x. Version 12 stopped
> accepting the old authentication headers and dropped the `/emby/` paths.
> Jellyscope has always used `Authorization: MediaBrowser Token="..."`,
> which 12 accepts and every older version understands. The server
> version is detected on its own, and *Settings → Jellyfin* can set it by
> hand for a server that behaves differently from what it reports.

Jellyfin statistics that **connect what people watch with the technical
state of the library**.

Inspired by two existing projects:

- **[Jellystat](https://github.com/CyferShepard/Jellystat)** knows *what is
  being watched*.
- **[MediaLyze](https://github.com/frederikemmer/MediaLyze)** knows *what
  those files are*.

Jellyscope copies neither. It implements both ideas independently in one
app, because together they answer questions that neither answers alone:

> "How many terabytes are taken up by content nobody has watched in a year?"
> "Which file makes the server transcode most often?"
> "What do I watch the most, and do I even have it in decent quality?"
> "Which 60 GB 4K film did I watch once and never again?"

---

## What it looks like

**Insights** is the page the whole project exists for. It puts behaviour
next to the technical data: how much space goes to things nobody watches,
which files make the server transcode, what you have in 4K and never
finished.

![Insights](docs/screenshots/insights.png)

**Overview** shows what is playing right now, what was added, and the
numbers for the chosen period.

![Overview](docs/screenshots/overview.png)

**Network** shows how much data left the server, when it peaked, and where
people watch from. Addresses from outside the home network are placed on a
map you can zoom into.

![Network](docs/screenshots/network.png)

**Comparison** puts any two periods side by side, with the same statistics
under each: watching, how the server delivered it, network, languages and
what the library gained or lost.

![Comparison](docs/screenshots/srovnani.png)

| Library | Languages |
|---|---|
| ![Library](docs/screenshots/library.png) | ![Languages](docs/screenshots/languages.png) |
| Codecs, resolutions and sizes of what you really have. | What language people watch in, and what the library offers. |

<sub>The screenshots are from the demo mode, so the data is made up and the
posters are empty. A real installation loads them from Jellyfin.</sub>

The colours are Jellyfin's own: blue `#00A4DC` and purple `#AA5CC3`. If you
preferred the earlier look, **Settings → Interface** switches back to it.
The classic appearance restores the old colours and the old shapes, and you
can switch again whenever you like.

---

## What it does

The first four pages make up the **Movies & series** section of the menu;
Music and Books have an Overview, a Comparison and Insights of their own
(see [The menu](#the-menu)).

| Page | Contents |
|---|---|
| **Overview** | Live playbacks, watched time, most active users and titles, transcode share, a "when do people watch" heatmap |
| **Comparison** | Any two periods side by side: watching, delivery, network, languages, library growth |
| **Insights** | Dead storage, most transcoded files, upgrade candidates, oversized files, possible duplicates |
| **Languages** | Language split in percent, who watches dubbed and who in the original, subtitles, titles with no track in the preferred language |
| **Music** | Laid out like Movies & series: an overview of listening (how much and when, who listens, the most played at a glance, how it is played), a comparison of two periods and insights (the full charts of albums, artists, tracks and genres, what nobody listens to); a page for every artist. The music library's cards are in the Library. Listening is not counted as watching |
| **Books** | Laid out like Music: an overview of reading and listening (how much and when, who reads, the most read at a glance, how audiobooks are played), a comparison of two periods and insights (the full charts of books, authors and genres, what nobody opens). The books library's cards are in the Library. Reading is not counted as watching |
| **\*Arr** | One page per connected app. Sonarr, Radarr, Lidarr and Readarr (or Chaptarr, Bookshelf): what is in the app and downloading right now, what was added, how fast, from where, upgrades. Bazarr: the subtitles. Prowlarr: the indexers |
| **Seerr** | Requests: what waits for approval, who requests, how fast requests are handled, and whether the requested titles get watched |
| **Library** | A tile for each Jellyfin library, then a detail with Overview / Media / Activity tabs, then the file detail; a music library is a grid of albums with an album page like a series, a books library a grid of covers with a page per book; the disks the libraries are on; day-by-day growth of the whole library |
| **Network** | Throughput over time, volume transferred, home network against internet, a map of where people watch from |
| **Users** | Watch time per user, devices, individual detail |
| **History** | Every recorded playback, with language and links to the user and the title |
| **Shared dashboard** | A tab the administrator names and builds for everyone from the cards the other pages are made of; off until switched on |
| **My overview** | The same kind of tab, which each account builds for itself on *My account* and nobody else sees |
| **Settings** | Jellyfin connection, Seerr, \*Arr, data collection, scheduled tasks and backups, notifications, history import, database, accounts, API, blocked logins, log, interface, general |

### The menu

The menu is split into **sections by the kind of content**, in this order:
**Movies & series** (Overview, Comparison, Insights, Languages), **Music**
(Overview, Comparison, Insights), **Books** (Overview, Comparison,
Insights), **\*Arr** (one item per connected app: Sonarr, Radarr, Lidarr,
Readarr - named Chaptarr or Bookshelf when that is the app that answers -
Bazarr, Prowlarr), **Seerr**, **Library**, **Network**, **Users**,
**History**, **Settings**. The shared dashboard and My overview stay above
the sections. Only the section of the open page is expanded; on a phone the
same tree is in the menu button, which says "Section · Page". Labels follow
the language of the account (or of the app).

A section shows only when Jellyfin has content of its kind (Movies & series
a movie or an episode, Music a track, Books a book) and the account may
open at least one of its pages; without content its pages answer 404 (an
artist's page stays, like an album's - other pages link to it). The tree
lives in one place, `jellyscope/navigation.py`.

Addresses are short and English, the section first: `/video/overview`,
`/video/compare`, `/video/insights`, `/video/languages`, `/music/overview`,
`/music/compare`, `/music/insights`, `/music/artist/<id>`,
`/books/overview`, `/books/compare`, `/books/insights`, an album at
`/album/<id>` and a book at `/book/<id>` (both in the Library); `/video`,
`/music` and `/books` open the section's first page the account may open.
`/` opens the default page (below), and the other addresses of 1.8.x
(`/insights`, `/top-items`, `/languages` and the other pages)
redirect to the new ones with their parameters, so old bookmarks keep
working.

**The default page** - what `/`, the logo and signing in open - is chosen
in *Settings → Interface → Default page*: the overview of a section with
content, or the shared dashboard while it is on. Movies & series by
default; an installation from before keeps its behaviour until the choice
is saved (the shared dashboard when it has something to show, otherwise
Movies & series). When the chosen page is not available, or the account
may not open it, the first available one opens in the order Movies &
series, Music, Books, shared dashboard, Library, History, My account.
Viewers from Jellyfin start on their own page.

### Library and file detail

`/library` shows **a tile per Jellyfin library** - every library Jellyfin
has, not just video. A video library has a poster, size and title count; a
music library its albums, tracks, size and length, a books library its
e-books, audiobooks, size and audio length; a library of a type
Jellyscope does not collect yet (photos, music videos…) a tile that
says so, without a link. A library Jellyfin stops returning is hidden after
two library syncs in a row (one empty answer while Jellyfin starts hides
nothing).

A **music library** opens as a grid of albums with covers, searchable by
album or artist and sorted by date added, name, artist, year or size; an
album opens its page, built like a series: the cover, the artist (a link to
their page in Music), tracks, discs, length, size and plays over the whole
history, *About the album*, the tracks by disc with format, bitrate, sample
rate, length, size and plays, and who listened (with *…including "who
listened"*).

A **books library** has the tabs Overview, Books and Activity like a music
library. The Books tab is a grid of covers, searchable by title, author or
series; the several files of one audiobook are one book. A book opens its
page (`/book/<id>`): the cover, the author, files, length, size, the time
listened and read over the whole history, and who read it (with *…including
"who read and listened"*). The Library page has a Books section next to the
music one.

Opening a video library gives three tabs:

- **Overview**: codecs, resolutions and languages in this library
- **Media**: a poster grid with file size, sorting and search
- **Activity**: what was watched from this library, by whom and when

A tile opens the **file detail**: container, path, size, video track,
**every audio track with language and channel count**, **every subtitle**
(including the *forced* and *external file* flags), the playback history of
that title, and for episodes a list of the other episodes.

A title that is on disk more than once is **one record**. Two files in one
folder are joined by Jellyfin itself (versions); two files of the same
movie in two folders of one library are two Jellyfin items, and Jellyscope
shows them as one tile ("2×"), one title in the rankings and one playback
history. The *File* card of the detail then has a switch between the files:
path, size and tracks follow the selected file, the rest belongs to the
title. The same movie in two **different** libraries stays a separate
record in each. File statistics (library size, codecs, resolutions) always
count every file.

Each title has a **Reload metadata** button. It asks Jellyfin about that
one title, which helps after you fix wrong metadata in Jellyfin and do not
want to wait for the nightly sync.

Images are downloaded from Jellyfin **through Jellyscope** and cached on
disk, so the Jellyfin address and API key never reach the browser.

### Two sources of technical data, picked in Settings

The choice only affects **file details** (codec, resolution, bitrate, size):

- **Jellyfin API only**: we take what Jellyfin reports. It works right
  away and needs nothing extra.
- **ffprobe + Jellyfin**: files are read directly from disk. More accurate,
  but it needs ffmpeg installed and access to the files.

**Playback statistics always come from Jellyfin.** ffprobe can read a file,
but it cannot tell who watched what. The list of titles, users and
libraries comes from Jellyfin either way.

### Languages

Which language people actually pick, per user and per title, including
"they had a choice and picked Czech anyway" against "there was nothing
else". Titles whose audio track has no language code at all get their own
page, so you can fix the files rather than guess.

### Music

Jellyfin's music libraries get their own section, **Music**, in the menu
right after Movies & series, as soon as Jellyfin has some music (an empty
music library is not enough; when the music library disappears from
Jellyfin, so does the page, and it comes back with the library). The
*Music sync* task (see [Scheduled tasks and
backups](#scheduled-tasks-and-backups)) downloads artists, albums and
tracks; listening itself is recorded by the collector like any other
playback.

**Listening is not watching.** A track played in Jellyfin never adds to
watched time, plays or titles on the Overview, Users, Comparison or the
viewer's page; it has a row of its own there (*Music listening*, with the
most played artist). History shows everything, with a *Music* filter; a
track leads to its row on its album's page.

**A play** is listening for at least 30 seconds, or at least half of the
track: a skipped track counts in the listening time, not in the plays.
"What nobody listens to" lists albums without a single play in the whole
history.

Music is laid out like Movies & series, page for page:

- **Overview**: the newly added albums, then the statistics of the period -
  hours, plays, listeners, artists and tracks, listening per day, the
  **listeners** (who listens how much and to which artist the most) beside
  the short chart **Most played** (the five albums with the most hours,
  linking to the whole charts), direct play against transcoding (and why),
  apps and devices, data sent over the network (an estimate from the
  bitrate) and a weekday × hour heatmap;
- **Comparison**: any two periods side by side, like the comparison of
  Movies & series - listening, how music is played, albums added to the
  library, the genres with their shares, and the most played albums and
  the most active listeners of both periods;
- **Insights**: the full charts of albums (with covers), artists, tracks and
  genres, and what nobody listens to;
- the music library (now, not in a period) is in the **Library**: its Music
  section and the tab of each music library show artists, albums, tracks,
  size and total length, formats by the audio codec, sound quality
  (lossless and hi-res - more than 16 bits or more than 48 kHz - against
  lossy below 128, 128-255 and from 256 kb/s), release decades, genres and
  the largest albums.

An artist's page shows the numbers of the period, the albums, the most
played tracks and who listened; an album opens its page in the Library (see
[Library and file detail](#library-and-file-detail)). Artists and albums
are links everywhere they appear. An artist or album that disappeared from
Jellyfin keeps its page and says so.

Every card is also offered in the [dashboards](#dashboards). The rights are
split like those of Movies & series: *Overview* (the overview and the
artists' pages) with *Comparison* and *…including "who listened"* below it,
and *Insights and charts* apart (on by default). The listeners' names need
*…including "who listened"* (off by default for Jellyfin viewers) and follow
anonymization.

### Books

Jellyfin's books libraries get their own section, **Books**, in the menu
right after Music, as soon as Jellyfin has some books (an empty books
library is not enough; it disappears with the library and comes back with
it). The *Books sync* task downloads e-books and audiobooks: author, format,
size, length and genres. Listening to an audiobook and reading an e-book in
Jellyfin's reader are recorded by the collector like any other playback.

**A work is a book, not a file.** An audiobook of many files (chapters,
discs) is one book: the files are put together by title and author, so the
charts count one audiobook once, with the length and size of all its files.

**Reading is not watching.** A book never adds to watched time, plays or
titles on the Overview, Users, Comparison or the viewer's page (an
audiobook is no longer counted as watching either); the Books section has
its own numbers. History shows everything, with a *Books* filter.

**What counts as reading.** The numbers count every book with some time
read or listened to. "What nobody opens" is stricter: a book counts as
opened when somebody has had it open for at least 30 seconds, and the card
lists the books without such a play in the whole history. The time is the
time really listened or read. An e-book counts through the *Book* playbacks
Jellyfin reports. If Jellyfin reports none (many readers do not), the cards
say so instead of showing zeros.

Books is laid out like Music, page for page:

- **Overview**: the newly added books, then the statistics of the period -
  hours listened, books read or listened to (any time counts), readers,
  authors, per day, the **readers** beside the short chart **Most read**,
  direct play against transcoding of audiobooks, apps and devices, and a
  weekday × hour heatmap;
- **Comparison**: any two periods side by side - reading and listening, how
  audiobooks are played, books added, the genres, the most read books and
  the most active readers of both periods;
- **Insights**: the full charts of books, authors and genres, and what
  nobody opens;
- the books library (now, not in a period) is in the **Library**: its Books
  section and the tab of each books library show e-books, audiobooks,
  authors, size, audio length, formats, years, genres and the largest ones.
  A book opens its page at `/book/<id>` (see [Library and file
  detail](#library-and-file-detail)). There is no author page.

Every card is also offered in the [dashboards](#dashboards). The rights are
the same tree as for Music: *Overview* with *Comparison* and *…including
"who read and listened"* below it, and *Insights and charts* apart (on by
default). The readers' names need *…including "who read and listened"*
(off by default for Jellyfin viewers) and follow anonymization.

### *Arr: Sonarr, Radarr, Lidarr, Readarr, Bazarr and Prowlarr

Connect Sonarr, Radarr, Lidarr and Readarr in *Settings → \*Arr* (address,
also with a path behind a reverse proxy, and the API key from the app's
*Settings → General → Security*). **Test connection** checks the address
really leads to that app. The *\*Arr sync* task then downloads the apps'
history every hour (the whole history the first time, then only what is
new) and keeps a copy in the database. Each connected app gets its own page
in the **\*Arr** section of the menu, laid out in five sections of small
cards, one question per card:

- **In the app right now**, live from the app and on top, because that is
  what gets stuck: tiles for the queue, missing and cutoff-unmet counts and
  warnings; queue items with an error or warning; what comes out in the
  next 7 days; the app's health warnings. The page waits for the app at
  most 3 seconds. The missing and cutoff-unmet counts (and Bazarr's missing
  subtitles by language) change slowly and a big library takes long to
  count them, so the *Arr sync counts them in the background and the tiles
  say when ("counted 20 min ago"). When the app does not answer, these
  cards say so and the rest, which comes from the database, stays.
  The **Downloading** card shows what the app is downloading at the
  moment: one block per title with its poster, state, a progress bar,
  downloaded of total, time left and the download client. A show with
  several downloads is one block without a progress bar of its own (an
  average over episodes says nothing); its poster or "show progress"
  opens a window with a progress bar per download, and a season pack
  counts once. A download the app could not match to a title is shown
  under its release name. Only this card refreshes itself, every 5 seconds
  while something is downloading and once a minute otherwise; the rest of
  the page does not move.
- **What was added**: tiles for the period (imported, new, upgrades, failed
  downloads), imports per day, a weekday × hour heatmap, best days and the
  largest imported files;
- **Speed**: how long a download took (grab → import) and how soon after
  release an episode or movie was in the library (median, 90th percentile).
  "After release" counts only what was being waited for: the title was
  already in the app when the episode or movie came out. A movie added
  thirty years after its release, or the old seasons of a show added
  later, are left out and only their number is shown;
- **Sources and quality**: indexers, release groups, download clients,
  usenet/torrent, quality and resolution - six small tables, each with
  its share and size;
- **Upgrades**: how often files are replaced by a better version, how
  downloads were triggered (automatically, a search started by a user, a
  manual release pick, pushed from outside) for everything and for
  upgrades, from which quality to which, and the most upgraded titles.

Long tables follow *Settings → Interface → Long lists*: the first rows in
the card, the whole table in a window. The \*Arr tables and the Seerr
table have their own numbers of rows there, shown once the app is
connected. Every card is also offered in the [dashboards](#dashboards),
separately for Sonarr, Radarr, Lidarr, Readarr, Bazarr and Prowlarr.

Jellyscope only reads from the apps, with one exception: while the
Downloading card is open and something is downloading, it asks the app to
refresh its downloads (the same command the app runs by itself every
minute; here with each refresh of the card, about every 5 seconds however
many people are watching), because the app would otherwise report
progress a minute old. It never cancels, pauses or starts a download, and
it never shows the app's own messages on this card (they can contain paths
and addresses; administrators have them on the queue card).

A title that is in the library is a link to its detail page. Titles are
matched by id, never by name (Jellyfin shows names in the metadata
language): Jellyscope keeps the app's list of titles with their TMDB,
TVDB and IMDb ids (fetched when the history brings a title it does not
know yet, otherwise once a day); a movie is found by its TMDB id, a show
by the ids Jellyfin holds for its series. A title the app is still
downloading has no link.

The API key is never shown back, and neither are credentials in the
address (`http://user:password@host` for a proxy): both are scrubbed from
every error text. Who sees the page is the *\*Arr* permission area (on by
default). The app's own texts - error details, health warnings, queue
messages, which carry internal addresses and paths - are shown to admins
only; everyone else sees that there is a warning, not what it says.
*Delete downloaded data* removes the copy (a backup is taken first); the
history stays in the app.

**Lidarr** (music) is the third app of the same kind: the same card in
*Settings → \*Arr* (address, by default `http://localhost:8686`; the API key
is in Lidarr's *Settings → General → Security*), the same task, permission
area and five sections of cards as Sonarr and Radarr. The unit is the
**album**: an album import is the track imports of one download, counted
once, with the number of tracks beside it. "How soon after release" follows
Sonarr's rule, with the date the artist was added. An album that is in the
music library is a link to its page (`/album/<id>`, which belongs to the
*Library* permission area), an artist to the artist's page in *Music*
(`/music/artist/<id>`, the *Music* area); without the right to that area
it is plain text, and so is anything Lidarr is only downloading. Unlike
Sonarr and Radarr, which match by id alone, an album is paired with the
library by any of its MusicBrainz release ids (against the library album's
id from Jellyfin), and failing that by album and artist name, ignoring case
and diacritics; an artist by its MusicBrainz id, else by name.

**Readarr** (books) is the fourth app of the same kind, and so are its forks
**Chaptarr** and **Bookshelf**, which answer to the same API: *Test
connection* accepts any of the three, and the app that answers gives its
name to the \*Arr menu item, the tab's heading and the Settings card
(Readarr until one has answered). The card in *Settings → \*Arr* (address,
by default `http://localhost:8787`; the API key is in the app's *Settings →
General → Security → API Key*), the task, the permission area and the five
sections of cards are Lidarr's. The unit is the **book**: a book import is
the file imports of one download (without a download id, of one book and
day), counted once, with the number of files beside it. An upgrade is
paired by the book, because a new edition's files have new ids. A book
that is in the books library is a link to its page (`/book/<id>`, the
*Library* area); the author is always plain text, and so is a book Readarr
is only downloading. Unlike Sonarr and Radarr, a book is paired with the
library by the ISBN-13 of every one of its editions (against the ISBN of
the library book), and failing that by title and author, ignoring case and
diacritics.

**Bazarr** (subtitles) is the fifth app of the section: the same card in
*Settings → \*Arr* (address, API key from Bazarr's *Settings → General →
Security*, **Test connection**, sync now, delete downloaded data), the
same task, the same permission area, its own page in the *\*Arr* section.
Bazarr alone is enough for the section to exist. The page has five
sections:

- **In the app right now**, live: episodes and movies without subtitles,
  how many titles wait for which language, the enabled providers and their
  state (throttled ones first, with the reason and when Bazarr retries),
  and Bazarr's warnings (texts for admins only - they contain paths);
- **What was downloaded**: subtitles obtained in the period - downloaded
  automatically, picked manually, replaced by better ones, uploaded,
  translated or extracted from the file; per day for episodes and movies,
  a weekday × hour heatmap and the split by method. Timing syncs and
  deletions are listed apart, they bring no subtitles;
- **Languages and providers**: count, share and average match per language
  (with hearing-impaired and forced subtitles) and per provider, including
  how many of a provider's subtitles an upgrade later replaced;
- **How good they are**: the match score Bazarr gave the subtitles (median
  and distribution), upgrades with the median improvement, and episodes or
  movies whose subtitles in one language were obtained more than once;
- **How fast they arrive** (only with Sonarr or Radarr connected): the time
  from the file's import to its first subtitles in each language, after
  every import (a file replaced by a better version counts again). Files
  that were in the library before Bazarr started are left out and only
  their number is shown: they got their subtitles in the first mass
  search, which says nothing about speed.

Bazarr's history has no ids and no "newer than" filter, and it reports
the time of an event only as text in the format of its system. Jellyscope
therefore reads the history in pages from the newest, tells records apart
by a fingerprint (what, which episode or movie, language, provider and
the time text exactly as Bazarr sent it: Bazarr stores local time
without a zone, so that text never changes, while its conversion to UTC
would after a change of Bazarr's time zone) and works the time format out
from the data. The first sync reads everything, later ones stop at the
first page with nothing new; a sync that did not finish after storing
something new makes the next one read everything again, so no gap stays.
If the format cannot be recognised, the card in Settings says so: such
records count only towards "whole history", newer ones by the time
Jellyscope first saw them. History that Bazarr no longer returns (deleted,
or the title removed from Sonarr) stays in Jellyscope. Titles are linked to
the library through Sonarr's and Radarr's ids, which Bazarr uses.

**Prowlarr** (indexers) is the sixth app of the section, connected the same
way (API key from Prowlarr's *Settings → General → Security*). It is a view
of the indexers, not of the library: what was added still shows on the
Sonarr, Radarr, Lidarr and Readarr pages. The page has four sections:

- **In the app right now**, live: how many indexers are enabled and how
  many are unavailable - the ones Prowlarr has temporarily disabled after
  errors (first in the list, with since when they fail and when Prowlarr
  tries again) and the ones it still names in its "indexers unavailable"
  warning after the block has run out (it tries those again on the next
  query; until then nobody knows whether they work, so they are not shown
  as OK) - then connected apps and Prowlarr's warnings (texts for admins
  only);
- **Queries**: searches and RSS queries of the period, how many failed and
  the average response time; per day, a weekday × hour grid of searches,
  and who asks: Sonarr, Radarr, or a manual search in Prowlarr itself;
- **Indexer performance**: per indexer the number of queries, the share
  of them that failed, the average response time, how many answered
  searches found anything and how many releases were grabbed (failed
  logins to an indexer are shown apart, they are not queries); response
  times as bars, failed queries per day;
- **Grabbed releases**: how many releases the apps took from the indexers,
  per indexer, and the latest ones with the result.

Prowlarr records every query, tens of thousands a day, so Jellyscope does
not store them one by one: it folds them into **sums per hour**, indexer,
calling app and kind. Only grabbed releases are stored individually. A sum
cannot be corrected like a row, so the sync counts every event exactly
once: events are told apart by their number, the history is read from the
newest event down to the last counted one, and the sums are written
together with the number of the last counted event in one transaction. An
interrupted sync leaves nothing behind, and two Jellyscope processes over
one database cannot both add the same events. If the history changes while
it is being read in a way that could skip something, the read is repeated;
a history that cannot be read reliably is an error, never a partial write.

Three things to know about the numbers:

- Prowlarr **keeps its history for a limited time** (30 days by default,
  *Settings → General → History Cleanup*). Jellyscope keeps what it has
  already counted, but what Prowlarr deleted before a sync could read it
  cannot be recovered: a Jellyscope that was off for longer than that has
  a gap. For the same reason *Delete downloaded data* brings back only
  what Prowlarr still has; the confirmation window says so.
- A **failed query** is one the indexer answered with an error. An indexer
  that does not answer at all leaves no event in Prowlarr's history; it
  is temporarily disabled and shows on the live card instead.
- With **usenet** indexers Prowlarr only redirects the app to the indexer,
  so it does not know whether the download succeeded or how long it took
  and records it as successful. Failures and download time of grabbed
  releases therefore come only from the releases Prowlarr downloaded
  itself (torrents); the cards say how many those were and show a dash
  where nothing is known.

After a reinstall of Prowlarr, or a restore of its older backup, its event
numbers start over. Jellyscope notices (an event newer in time than
everything counted, but with a lower number) and continues by time. The
indexer numbers start over too: an indexer whose number now belongs to one
with a different name keeps its name and its sums as a removed indexer, so
the history of one indexer never shows under the name of another. A
different Prowlarr at a new address is another matter: Settings warns when
the address changes while data are stored, so use *Delete downloaded data*
if it really is a different instance.

### Seerr: requests

Connect Seerr (the merged Overseerr/Jellyseerr; both work too) in
*Settings → Seerr*, right after Jellyfin: address (also with a path behind
a reverse proxy) and the API key from Seerr's *Settings → General*. **Test
connection** checks that the address really leads to Seerr. The *Seerr
sync* task then downloads all requests every hour; a request deleted in
Seerr stays in Jellyscope, marked. The **Seerr** page (in the menu once
Seerr is connected) is laid out in small cards, one question per card:

- **Waiting for approval**, live from Seerr: pending requests as blocks
  with a poster, the longest waiting first;
- **Latest requests**: a strip of posters, newest first, with the outcome
  on each poster (pending, approved, in the library, declined…) and the
  same rows as a table in a window;
- **Requests in the period**: tiles (requests, movies, shows, 4K, from
  watchlists), requests per day, a weekday × hour heatmap, how requests
  ended, **who requests** (approved / declined / in the library and how
  many of their own titles they watched; a requester who is a Jellyfin
  viewer links to their page) and **how fast requests are handled** (from
  request to the library and to approval; Seerr keeps no approval time,
  so Jellyscope catches it while syncing);
- **Requested and watched?**: for requested titles in the library, did the
  requester start them, only someone else, or nobody, with a strip of the
  titles nobody started.

**Posters.** A requested title that is in the library uses Jellyfin's
poster, the same cached file the rest of the app shows. A title that is
not there yet gets a provisional poster, and only from the apps Jellyscope
is connected to: Radarr (movies) or Sonarr (shows) for anything that
reached them, otherwise Seerr. Jellyscope itself makes no outbound
connection for images. Pending and declined requests never reach
Radarr or Sonarr, so only Seerr can provide their poster, and Seerr
serves images only with **Enable Image Caching** on (*Settings → General*
in Seerr; off by default). Jellyscope reads that state from Seerr after
each sync, after *Test connection* and after saving; *Settings → Seerr*
shows it in the *Request posters* row with a link to Seerr's settings.
Until it is on, such requests show a placeholder; once it is on, the
posters fill in by themselves, also for requests already stored. When the
title arrives in the library, Jellyfin's poster takes over and the
provisional file is deleted.

A requested title that is already in the library (poster and name) is a
link to its detail page; one that is not there yet has no link.

Every card is also offered in the [dashboards](#dashboards).

Who sees the page is the *Seerr* permission area (on by default); the
requesters' names need *…including "who requested"* below it (off by
default for Jellyfin viewers) and follow anonymization. The API key is
never shown back, and the detail of a connection error is shown to admins
only.

### Network

How much data left the server for the players, and when it peaked. The
throughput curve is not sampled: every playback adds its bitrate at its
start and takes it away at its end, so the curve follows the playbacks
exactly. Alongside it you get the volume transferred, the transcoded share,
the people and players that streamed the most, and a split of home network
against internet.

The numbers are an honest estimate, not a measurement of the wire:
seeking, buffering and pauses move the real numbers, and the page says so.

**The map** places public addresses with a GeoLite2 database: one file in
`data/`, downloaded with a button in Settings and queried offline. No
address of your viewers is ever sent anywhere, because the library that
reads that file (`maxminddb`, part of the installation) opens a local file
and never asks the network. Until the file is downloaded, the page explains
what is missing instead of showing an empty map. Addresses from the home
network are never placed, since `192.168.1.5` marks no spot on Earth.
Data © MaxMind, GeoLite2 (CC BY-SA 4.0).

A dot is one spot from GeoLite2, which for a larger city means one per
district, and its viewers are counted as people: one person watching from
two addresses is one. Zoomed out, nearby places merge into one dot
("Prague, Czechia and 2 more places"); zooming in splits them, down to
every single place, up to 1024×; with the detailed map or OpenStreetMap
under the dots it always goes all the way. A click zooms in twice on the
spot you clicked, so clicking a merged dot splits it.

What lies under the dots:

- **Drawn map** (default): land outlines and country borders built into
  the application.
- **Detailed map**: Natural Earth 1:10M with coastlines, lakes, rivers,
  regions and city outlines, shown from 8× zoom. An admin downloads it
  with a button on the Network page: about 75 MB from a fixed version
  (v5.1.2) of the Natural Earth repository on GitHub. It is cut into tiles
  and stored in the database, where it takes about 52 MB, so it is in
  every backup too, and each backup grows by that much. The processing
  takes a minute or two and briefly needs about 250 MB of memory. A button
  deletes it again. Natural Earth data are in the public domain.
- **OpenStreetMap** (*Settings → Interface → Map background*, off by
  default): a real map with streets. The browser of everyone who opens the
  map downloads the map images straight from `tile.openstreetmap.org`,
  which therefore sees their IP address, the address of your server and
  the area they are looking at. It learns nothing about plays or viewers.
  The page lets those images in only while the option is on. Map data ©
  OpenStreetMap contributors.

### Comparison of two periods

"August against December" rather than "compared to the previous period".
The page (`/video/compare`) has two independent pickers, each with its own
custom range, and the same statistics under each other: watching, how the
server delivered the content, network, which language people watched in,
and what the library gained or lost. Music and Books have a comparison of
their own (`/music/compare`, `/books/compare`).

The page says so when the two periods **overlap** or **differ in length**,
because a percentage between a week and a month compares nothing.

### Dashboards

Besides the fixed Overview there are two tabs you stack from cards. The
builder offers the cards of every statistics page, from the Insights
tables to the map, grouped under headings by the page or app they come
from. Only Comparison stays on its own page, because it needs two periods.
Each card is a template of its own (`_card_*.html`, registered in
`cards.py`) that both its page and the dashboards include, so the two
cannot drift apart.

- **The shared dashboard** (`/dashboard`) is built by the administrator
  for everyone. It needs a name before it can be switched on (*Settings →
  Interface*, up to 40 characters), and the name then shows in the menu
  and as the page heading. An installation that already had the dashboard
  keeps it under its old name, "Custom overview". It can be chosen as the
  [default page](#the-menu), the first page after signing in.
- **My overview** (`/account/dashboard`) is built by each account for
  itself on *My account*, with no permission needed. Nobody else sees it.

Setting up the shared dashboard takes three steps: name it and switch it
on, pick the cards (each with a width of a third, a half or the whole
row), and save.

![Building the shared dashboard: name it in Settings → Interface, pick the cards, save](docs/screenshots/dashboard.gif)

Cards follow access rights. The shared dashboard leaves out a card from an
area the account may not see, and its data is not even computed. My
overview keeps such a card in its place with a note that the permission is
missing, so it does not look broken, and the card comes back once the
administrator allows the area again. A tab with nothing in it for you
stays out of your menu; the administrator always has the shared one, to
build it.

The cards of Sonarr, Radarr, Lidarr, Readarr, Bazarr, Prowlarr and Seerr
exist once per app and carry the app's name in a dashboard ("Sonarr ·
Indexers"), so the same card of two apps can sit side by side. They are
offered only while the app is connected. When an app is disconnected, its
cards are not computed and the shared dashboard does not draw them; My
overview keeps them in place with a note, and the stored layout is kept,
so the cards are back once the app is connected again.

### Library growth, disks and free space

A daily snapshot of the library (size, title count) turns into a
**day-by-day curve** on the Library page, and the time before the first
snapshot is reconstructed from the dates titles were added. The
reconstruction is drawn in a different colour and the page says what it
is: we know when a title arrived, but its past is described with today's
sizes.

Music and books count in the size: hovering over a day says how much of it
is music or books, and for the days before music was counted the curve is
reconstructed from the dates tracks were added, so it does not jump. The
number of items stays movies and episodes.

Alongside it you see how much arrived over the period, the daily average,
and **how long the free space will last**. Free space comes from Jellyfin
itself, because the data usually sits on a different machine than
Jellyscope. The chart also shows what takes up the library disks besides
the library (downloads, backups) wherever Jellyfin reports the disk. With
two or more disks, *All* is the whole storage: the free space of every disk
together (two volumes that look like one disk count once until the admin
answers) and how long it lasts at the growth of the library's files on
those disks, and a tile **Fills first** names the disk whose space runs out
first (the free-space notification watches that disk). Chips above the
chart switch from *All* to one disk: the library's files on it day by day
(the past reconstructed from the files on that disk) and its own "space
runs out", the same number as the Disks card; until a disk has two daily
snapshots, it comes from the growth of the files on the disk.

**Disks** (a card on the Library page) shows which disk each library is on
(Jellyfin 12 reports the device of every library folder), with capacity,
free space, the library's files, the data outside Jellyfin and when space
runs out, per disk, so a separate disk for films and for shows is no
longer a blind spot. A disk's capacity comes from Jellyfin or is entered
by hand. A **virtual disk** (one or more paths and a capacity) covers
storage Jellyfin cannot tell apart, such as a cloud mount reporting
nonsense: files under any of its paths count towards it, not towards the
physical disk, and every file counts on exactly one disk (a path may also
sit above several library folders, one quota for all of them). Every disk
is set up in its own dialog (the Edit button at the end of its row), where
a physical disk can also get its own name instead of its libraries'. Two
volumes that look like one disk (Docker) are only flagged; the admin
decides, and can take the answer back. On older Jellyfin without the
per-folder report, one capacity entered by hand (in GB or TB) in *Settings
→ Data collection* applies as before.

Music and books count as the library's files on their disk, by the path of
each track or file. The first music sync after upgrading to 1.9.0 runs in
full to learn the paths, and until then music is still in "outside
Jellyfin". **Total size** on the Library page counts music and books too
("of which music …", "of which books …"), and so does `/api/v1/library`,
which gives the page's numbers (the number of items stays movies and
episodes); codecs, resolution and the other technical tiles stay video
only.

### Notifications

The application runs in the background and knows when something breaks,
but it needs somewhere to say so. *Settings → Notifications* has the
channels (**SMTP**, **Discord**, **Telegram**, each with a test button)
and three events that can be switched on and off separately:

- **the collector has stopped collecting**: nothing has been recorded for
  a while, so the history is quietly getting a hole
- **space is running out**, judged from the growth of the library and the
  free space that is known, per disk: the message names the disk that runs
  out first
- **a weekly summary**, on a day and at a time you choose

A message is only sent **when something changes**, including "it works
again", because people stop reading a watch that keeps repeating itself. A
problem and its end are two messages with their own wording. Every subject
and text can be rewritten on the same page; an empty field keeps the
default, which is shown in grey. `{detail}` in a text is replaced by the
reason, and the other values a message knows (`{minutes}`, `{disk}`,
`{days}`, `{free}` and a few more) are listed beside it.

The page states one limit itself: nothing can tell you that Jellyscope is
not running, because Jellyscope would be the one sending it. That is a job
for an uptime monitor.

### Clearing out the history

A tool that records who watched what and when will sooner or later be
asked to forget some of it. *Settings → Tasks and backups* has a daily
**Clearing out the history** task. It deletes playbacks older than a limit
set beside it, and the page says how many rows that limit would remove
**before** anything is saved.

It is **off unless switched on**, and it stays off across updates, because
deleting data must never start on its own. Playback that is running is
never deleted. It belongs to the collector, which would only write it again
a moment later without its beginning.

The same section can **forget one viewer**: everything recorded about them
goes, and nobody else is touched. The account itself lives in Jellyfin, so
the next synchronisation sees it again, just without the history.

### The archive

Titles that disappear from Jellyfin are not deleted. They go to the
library's **archive**, so their history does not turn into nameless rows.
When a file comes back under a new id, Jellyscope finds it by its tmdb id
and the history follows on its own.

That fails when Jellyfin identified a title wrongly (say as *Behind the
scenes*) and created it again after the fix, with a new name, a new id and
nothing to match on. An archived item can then be **assigned to another
title** by hand: its history and statistics move to the chosen title and
the archived item is removed. This works for one item from its detail, or
for many at once from the archive view, each with a search in the live
library and a small poster beside every target. The search takes words in
any order and understands season and episode: `Girls S01E06`, or `S06` for
a whole season. The bulk dialog first
shows a summary of what will move, and changes nothing until that summary
is confirmed.

What is not worth keeping can be **deleted** with its history: one title
from its detail, or many at once from the archive view. Tick the items
(each shows how many times it was played) or *Select all*, check the
summary and confirm. Only archived items of that library can go this way,
never a title that is still in the library. A database backup is taken
before every assignment or deletion.

### On a phone

Every page was measured at 320, 360 and 390 px, and none of them scrolls
sideways. The menu is a burger that names the section and the open page
("Section · Page") and holds the same tree of sections, the Settings
sections are a dropdown, and wide tables turn into blocks or hide their
secondary columns. How many live streams (or viewers in the language
statistics) are shown outright is set **separately for a phone**, where a
card takes the full width.

### Scheduled tasks and backups

**Settings → Tasks and backups** has twelve tasks:

| Task | When (by default) | What it does |
|---|---|---|
| Library sync | daily at a set time | Downloads users, libraries and titles. With ffprobe selected, an analysis of files without technical data follows. |
| Recently added titles | every N minutes | Only fetches what is not in the library yet. It barely touches Jellyfin, so it can run often. |
| Data tidy-up | daily at a set time | Asks Jellyfin about records that lead nowhere in the library, links them by name and episode number, merges duplicates and aligns names with the library. It deletes nothing. |
| Notifications | every N minutes | Checks whether the collector is collecting and whether space is running out, and sends the weekly summary on its day. |
| \*Arr sync | every N minutes (60 by default) | Downloads new history from the connected Sonarr, Radarr, Lidarr and Readarr, the subtitle history from Bazarr, and the indexer queries and grabbed releases from Prowlarr. Does nothing until an app is connected. |
| Music sync | every N minutes (60 by default) | Downloads artists, albums and tracks from Jellyfin's music libraries: format, size, length and genres. Each run reads only what was added; once a day it goes through everything and notices what disappeared. Does nothing without a music library. |
| Books sync | every N minutes (60 by default) | Downloads e-books and audiobooks from Jellyfin's books libraries: author, format, size and length. Each run reads only what was added; once a day it goes through everything and notices what disappeared. Does nothing without a books library. |
| Seerr sync | every N minutes (60 by default) | Downloads all requests from Seerr; a request deleted in Seerr stays in Jellyscope. Does nothing until Seerr is connected. |
| Check for updates | daily at a set time | Asks GitHub whether a newer release is out; the window of a new version shows the notes of every release since yours. It installs nothing, and it is off by default. |
| Clearing out the history | daily at a set time | Deletes playbacks older than the limit that is set. Off by default. |
| Database backup | daily at a set time | Saves a copy into the chosen folder and deletes surplus older ones. |
| Log rotation | daily at a set time | Every N days (7 by default) puts the application log and the API log aside and starts new ones. Keeps the set number of old ones (4 by default). |

The default times of the nightly tasks follow each other: the library sync
at 3:30, the tidy-up at 4:00 on what the sync has just fetched, the backup
at 4:30 with data that has already been straightened out, then clearing
out the history at 4:45 and the update check at 5:00. Log rotation runs
just after midnight.

The same page also holds **File analysis** (reading technical data with
ffprobe, with its coverage) and the manual **Data tidy-up** button.

Each task but Log rotation, Clearing out the history and Notifications can
run either **at a time every day** or **every N minutes or hours**: the table says which one it starts with, and
the Tasks page switches it. A task at a time of day keeps its time: a
manual run never changes the schedule, and a missed run (the machine was
off) is caught up after start. One switched to a time of day starts at that
time, not at once. An interval counts from the last run, manual ones
included, and has no lower limit (the library sync every minute, if you
want). Log rotation stays daily, since it turns the logs after N days, and
so does Clearing out the history: it makes a full backup before it deletes,
and on an interval those backups would push the kept ones out within hours.
Notifications stay every N minutes: they watch the collector and the free
space, and the weekly summary has its own day and time in Notifications.

**A task never runs twice at once.** The scheduler starts the due tasks one
after another and skips one that is running (a task at a time of day that
was started by hand at its time counts as done for the day). **Run now**
starts a task at once, beside any other, unless that same task is running
already; then it says so and starts nothing.

Backups can be downloaded, deleted and restored from the same page.
Restoring saves the current state first, so a misclick costs nothing.
Logs work the same way: **Settings → Log** shows the application log or
the API log, either can be downloaded whole, and the older ones that Log
rotation put aside wait there for download until it deletes them. The log
file is always written in English; the page shows it in your language.
SQLite backups use the built-in snapshot function rather than a file copy,
because a copy taken mid-write can be corrupt.

### Read-only API

Numbers for Grafana, Homepage or a script of your own. It only reads:
everything under `/api/` is a `GET`, and anything else is refused. The API
is a source of numbers, not a second way into the data.

**Settings → API** lists every key on the server. A key is shown
**once**, when it is made. What is stored is a hash and the first five
characters, so nobody, the page included, can read it back afterwards. If
you lose one, revoke it and make another; no other key is affected. You can
have one per tool, and the *last used* column shows which one nobody needs
any more.

**There are two kinds of keys.**

**A user key belongs to an account and can do exactly what that account
can.** Reader accounts and Jellyfin viewers make their own on the **My
account** page, once the admin allows it: **Settings → Accounts → Own API
keys** sets this for each group, and the Rights dialog of one account can
say yes or no for that account alone. Admins make theirs on My account
too. Such a key sees what its owner sees. An address in an area the owner
cannot open answers 403 (`/api/v1/` lists only the addresses the key may
use), other people's names and addresses are hidden when the owner has
anonymization on (`?user=` then takes the substitute id the API returns,
`skryto-3`), and a viewer's key gets only the viewer's own history:
asking for someone else's with `?user=` is a 403. A viewer's key also
stops while sign-in through Jellyfin is switched off, and any key of an
account from Jellyfin (an administrator's too) while its owner is disabled
in Jellyfin. Taking the permission away stops the key without deleting it,
and giving it back makes it work again. An admin's key can do everything
only while its owner is an admin.

**A root key belongs to no account and can do what the admin set for it.**
Admins make root keys in **Settings → API**, and choose its rights there:

- **Everything**: every address, the playback history and viewers' real
  names, like an admin, including areas that later versions add.
- **Only selected**: the same tree of areas as in an account's Rights
  dialog (only the areas that have an address in the API), plus two boxes.
  **Playback history** (`/api/v1/history`) starts unticked, so a key for a
  library widget does not learn who watched what unless you tick it.
  **Hide viewers' names and addresses** works as it does for an account.
  An area added in a later version stays off until you tick it.

The rights are checked on every request. Any admin can change them later
with the **Rights** button next to the key; the change applies from the
next request, and the application log records who changed them and what
they were before and after. A root key does not depend on the admin who
made it: when that admin loses the admin role or the account is deleted,
the key keeps working with its own rights.

Keys made before keys had owners are root keys with *everything*, so
Grafana and Homepage go on working after the update. The key table shows
them as *Root · everything*, and the API log as *root key*. Keys that
admins made in Settings → API in 1.8.0 belong to those admins and stay
user keys.

The admin can **disable** any key, which stops it but keeps it with its
log so it can be enabled again, or **revoke** it for good. The owner can
only revoke their own.

**Usage log.** Every request with an existing key is written to the API
log (`data/logs/api.log`): when, the result (200, 403 and so on), the IP
address, which key and whose, and the address with its parameters.
**Settings → Log → API log** shows it with filters by user, key and IP
address, and only the admin sees it. On Settings → API, the links in the
key table open it already filtered. How long it is kept is up to the Log
rotation task. A request with an unknown key is not written down.

Errors come back as JSON with `detail`, a sentence, and `error`, a code a
tool can check: 401 for a missing or unknown key (`missing_token`,
`invalid_token`), 403 for a disabled key, an owner without the permission
and an address outside the key's rights (`key_disabled`,
`key_not_allowed`, `area_not_allowed` and a few more), 404 for an unknown
title (`not_found`) and 400 for a malformed request (see *Filtering and
sorting* below).

The key travels in a header and never in the address, because the address
ends up in the proxy log, in the browser history and in the link somebody
forwards.

```bash
curl -H "Authorization: Bearer js_your_key" http://localhost:8097/api/v1/summary
```

| Address | What it returns |
|---|---|
| `GET /api/v1/` | The version and the list of addresses. |
| `GET /api/v1/summary?days=30` | Hours watched, plays, viewers, titles, transcode share, and how much is playing right now. |
| `GET /api/v1/now-playing` | What is playing: who, what, on what, transcoded or not, how far in. |
| `GET /api/v1/library` | Size of the library (music and books included), item counts (movies and episodes), free space (all disks together), growth over a period. |
| `GET /api/v1/libraries` | Every library on its own: items, size, hours of content. |
| `GET /api/v1/top-items?days=30&limit=10&kind=both` | Most watched titles; episodes of a series add up. `kind` is `both`, `movies` or `series`. |
| `GET /api/v1/least-played?days=365&limit=25&min_size_mb=0&order=size` | Files from the least played up, never played first, with the last time each was played. Within the same play count the largest come first; `order=oldest` or `order=newest` sorts them by date added instead. Without `order` nothing changes. |
| `GET /api/v1/unwatched?days=365&limit=25` | What nobody watched over the period and how much space it takes, also as a share of the library. |
| `GET /api/v1/users?days=30` | Viewers and their watching over the period: hours, plays, titles, transcoded hours. |
| `GET /api/v1/history?days=30&limit=50&kind=both` | Recent playbacks, newest first. No IP addresses; the API never hands those out. |
| `GET /api/v1/recently-added?limit=18` | What came into the library last. |
| `GET /api/v1/play-methods?days=30` | Direct play against transcoding, and the clients people watch with. |
| `GET /api/v1/insights?days=30&limit=15` | The Insights page in numbers: transcode offenders, upgrade candidates, oversized and rarely watched, never finished, and the conversion speed in fps (average, slowest, fastest, by hardware). |
| `GET /api/v1/bandwidth?days=30` | Data sent out, the peak, transcode share, home against internet, and by country (home against internet and the countries only with *…including where they watch from*, otherwise `null`). Without addresses. |
| `GET /api/v1/item/<id>` | One title: the file, its versions and who watched it. |
| `GET /api/v1/languages?days=30` | Audio languages by watched time, and how much was watched with and without subtitles, and in which. |
| `GET /api/v1/music/summary?days=30` | Music over the period: listening time, plays, listeners, tracks, albums, artists. |
| `GET /api/v1/music/top?days=30&kind=tracks` | The most listened tracks, albums or artists (`kind`). |
| `GET /api/v1/music/history?days=30&limit=50` | Recent listens, newest first. Needs the playback history and *…including who listened*. |
| `GET /api/v1/books/summary?days=30` | Books and audiobooks over the period: listening and reading time, plays, readers, works, authors. |
| `GET /api/v1/books/top?days=30&kind=books` | The most read books or authors (`kind`). |
| `GET /api/v1/books/history?days=30&limit=50` | Recent readings, newest first. Needs the playback history and *…including who read*. |
| `GET /api/v1/disks?days=30` | Every disk: capacity, free space, what the library takes, daily growth and when it fills up. |
| `GET /api/v1/arr` | The *Arr apps: which are connected, warnings, the last sync. |
| `GET /api/v1/arr/<app>/queue` | Live: what Sonarr, Radarr, Lidarr or Readarr is downloading, and the downloads that failed. |
| `GET /api/v1/arr/<app>/calendar?days=14` | Live: what comes out in the next days (Sonarr, Radarr, Lidarr, Readarr). |
| `GET /api/v1/arr/<app>/health` | Live: the app's health warnings. |
| `GET /api/v1/arr/<app>/stats?days=30` | The app over the period: downloads, how soon after release, upgrades, failures, missing, and each indexer's reliability; Bazarr's subtitles and providers; Prowlarr's queries. |
| `GET /api/v1/arr/prowlarr/indexers?days=30` | Every indexer's availability, outages and state right now. |
| `GET /api/v1/seerr/requests?days=30&status=waiting` | Requests, newest first, with their state. |
| `GET /api/v1/seerr/pending` | Live: the requests waiting for approval and how long they have waited. |
| `GET /api/v1/seerr/stats?days=30` | Requests over the period: counts by state, how soon approved and in the library, watched by the requester or not. |

**Which right opens which address.** **Settings → API** and the Rights
dialog list the addresses under each area of the tree, and the guide on My
account says for each address which area opens it. The *Arr and Seerr
addresses follow the same areas as their pages. `<app>` is `sonarr`,
`radarr`, `lidarr`, `readarr`, `bazarr` or `prowlarr`; an app that is not
connected, or an address the app does not have (a queue in Bazarr), is a
404 with `app_not_connected` or `not_available_for_app`. A live address
whose app does not answer is a 200 with `"available": false` and a
`reason`, never an old answer passed off as a new one. The apps' own
messages (health warnings, why a download failed) are only in an admin's
answers, as on the pages; other keys get the type without the text. The
requester of a Seerr request needs *…including who requested*, and every
name follows the key's anonymization. Times are UTC, durations seconds
and shares a fraction from 0 to 1.

Every item that is one file (least played, unwatched, recently added, one
title, and the oversized list in insights) carries its full `path` and
`tmdb_id`. For an episode `tmdb_id` is the series' TMDB id, since an
episode rarely has its own, and `season` and `episode` say which one it
is. Lists by title (top items, the other insights lists) have no single
file.

#### Filtering and sorting

Every list the index names under `lists` can be narrowed, sorted and cut
by the fields it already returns, so one address answers many questions.
The live lists of the *Arr apps and Seerr (a queue, a calendar, health,
the waiting requests) are short, as the app says them right now, and take
none:

```
GET /api/v1/least-played?path[starts_with]=/media/movies&sort=plays,added_at&limit=5
```

gives the five least played files under `/media/movies`, the oldest added
first when the play count is the same.

- **Filter:** `field[operator]=value`, one parameter per filter. All of
  them must match.
- **Sort:** `sort=field,-field`. A minus sorts descending; at most three
  fields; empty values always come last. Without `sort` a list keeps its
  usual order.
- **Limit:** `limit`, with the same defaults and caps as before.
- **Order:** filters first, then sorting, then the limit, always over the
  whole list, so the limit never cuts off what a filter is looking for.

| Field type | Operators | Value |
|---|---|---|
| text | `equals`, `not`, `contains`, `not_contains`, `starts_with`, `ends_with`, `in`, `empty` | any text up to 200 characters; case does not matter |
| number | `equals`, `not`, `gt`, `gte`, `lt`, `lte`, `in`, `empty` | `12` or `1.5` |
| time | `gt`, `gte`, `lt`, `lte`, `empty` | `2026-10-01` or `2026-10-01 18:30:00`, in UTC like the answer |
| bool | `equals`, `not` | `true` or `false` |

`in` takes values separated by commas (`type[in]=Movie,Episode`), and
`empty=true` finds fields without a value. `not` and `not_contains` also
match a field without a value; the other operators do not. A filter sees
what the key sees: names hidden from the key's owner cannot be found by
filtering for them either.

`/api/v1/insights` and `/api/v1/play-methods` return several lists, so a
field there carries the list name, and a list that no filter names comes
back unchanged. Called without filters, both answer as they always did.

```
GET /api/v1/insights?never_finished.best_percent[lt]=50&sort=-never_finished.attempts
GET /api/v1/play-methods?clients.hours[gte]=1
```

In `/api/v1/unwatched` a filter also narrows the totals (`count`,
`size_bytes`, `share_percent`), so `?path[starts_with]=/media/series`
says how much space the unwatched files in that folder take. `/api/v1/`
lists the fields and types of every list the key may use.
`/api/v1/summary`, `/api/v1/library` and `/api/v1/item/<id>` return one
object and take no filters.

**A malformed request is a 400, never default data.** An unknown
parameter, field, list or operator, a value of the wrong type, `days`
outside 1–3650, a `limit` over the cap, `order` together with `sort`:
each answers 400 with a code in `error` (`unknown_parameter`,
`unknown_field`, `unknown_list`, `unknown_operator`,
`operator_not_allowed`, `invalid_value`, `duplicate_parameter`,
`too_many`, `conflicting_parameters`, `not_filterable`), the `parameter`
it is about and, where it helps, the `allowed` values. Before, such a
request quietly got the default answer, which looked fine and was not
what had been asked for. The key is checked first, so without a valid key
the answer is 401 whatever the parameters say.

```json
{
  "detail": "Unknown field 'patth' in items. Fields: id, name, type, …",
  "error": "unknown_field",
  "parameter": "patth[contains]",
  "allowed": ["id", "name", "type", "…"]
}
```

Every answer has a fixed shape built from the same functions the pages
use, so a dashboard and the page it mirrors cannot drift apart. Row counts
are capped (500 at most, fewer where a page shows fewer, and a `limit`
over the cap is a 400), so a tool asking every few seconds cannot make the
server serialise the whole library each time.

```json
{
  "days": 30,
  "watched_hours": 647.0,
  "plays": 1066,
  "users": 11,
  "titles": 36,
  "transcode_share_percent": 29.6,
  "active_now": 1
}
```

`active_now` (streams playing right now) is there only for keys allowed
to see *Now playing*; other keys get the summary without it.

The answers carry no CORS headers, so another site cannot read them from a
browser; the API is for tools running on a server. The same documentation
is in the application itself, behind the button in *Settings → API*.

### History and its filter

The history list has a filter whose parts combine: user, kind (movies, TV
shows, music, books), a date range, play method, player and language at
once. All of it is in the address, so a filtered view can be sent as a
link.

### History import

Jellyscope only records playbacks while it runs. If you already have
history elsewhere, **Settings → History import** can take it over:

- **Playback Reporting**, a Jellyfin plugin. The data is read straight
  through Jellyfin, with no file to upload. A file upload is still there
  for when the plugin's API misbehaves.
- **Jellystat**: export a JSON backup and upload it.

Imports are **idempotent**: run one ten times and nothing is duplicated.

Imported data carries no audio language or transcode reason, because
neither tool stores them. Some Playback Reporting versions do record the
language, and those rows are counted.

Imported history often refers to titles by name only ("Episode 7"), which
matches nothing in particular. **Data tidy-up** (in *Settings → Tasks and
backups*) sorts that out in one action, and the daily task does it without
being asked:

1. Jellyfin is asked about the identifiers in the imported history. They
   are genuine, so it can name the series and the episode number the
   record is missing. A record hanging on a *series* id is narrowed down
   to one episode within that series.
2. What Jellyfin no longer knows is linked by name and episode number.
3. Duplicates are merged and names aligned with the library.

The steps always run in this order, which is why it is one button: the
lookup produces the links the rest works from.

Whatever is still left over is listed on **What could not be placed**,
grouped by reason, and can be assigned to a library title by hand.

### Accounts and signing in

The whole app is behind a login. On first open it asks you to create an
administrator account; more accounts are added in **Settings →
Accounts**.

Two roles:

- **administrator**: changes settings, runs tasks, manages accounts
- **reader**: sees statistics only, which suits most of a household.
  What reader accounts see can be narrowed down in Settings.

Passwords are stored as a hash (PBKDF2-SHA256, 600 000 iterations,
salted), never in readable form.

**One local administrator always stays.** An account from Jellyfin depends
on Jellyfin: when it is down, or the person is disabled or demoted there,
only an account of Jellyscope itself gets into Settings. So the last local
administrator can be neither deleted nor demoted, from Settings or from
`manage.py`. The rights of an account from Jellyfin are set in Jellyfin,
not here. An older installation that has no local administrator left gets
a warning in *Settings → Accounts*; create one there or with
`manage.py add <name> --admin`.

**My account.** The button with your name at the bottom of the menu
opens a page about your own account, available to everyone who is signed
in. A Jellyfin viewer sees their Jellyfin profile picture on it, which
Jellyscope does not store. There you build *My overview*, make your own
API keys if the administrator allows it, and pick
the interface language for your account alone; it takes precedence over
the server language in *Settings → General*, which stays the default for
everyone else. A local account can change its password there too, and
it asks for the current one first. Changing it signs that account out
everywhere else. A Jellyfin viewer changes the password in Jellyfin.

#### Signing in with a Jellyfin account

Anybody with a Jellyfin account can be let into Jellyscope with the same
username and password. Switch on **Enable Jellyfin sign-in** in
**Settings → Jellyfin → Viewer sign-in**. It stays off until you do,
because opening the household's statistics is your decision and an update
will not make it for you.

There is one **Sign in** button. A name that has a local account with a
password is checked here only, so that password never goes to Jellyfin,
and a wrong one is refused. Any other name is checked against Jellyfin.
When a local account and a Jellyfin user share a name, the local account
wins; the Jellyfin user signs in with a code (below).

The password is never stored here, only passed to Jellyfin to check, and
the access token that check produces is revoked straight away. A Jellyfin
administrator is an administrator here too: rights given or taken away in
Jellyfin follow here at the next users' sync (with every quick refresh, 15
minutes by default), a browser signed in before included. A person
disabled in Jellyfin cannot sign in, a browser signed in before is signed
out at the next sync, and their own API key stops. The account is not
deleted, so enabled again they are back with their settings. A password
changed in Jellyfin does not end a session already open here, since
Jellyscope never learns of it; to end one at once, delete the account in
**Settings → Accounts** (it is made again at the next sign-in).

Beside the switch is a tree of what a viewer sees. Their own statistics
and their own history are always visible, since that is what they signed
in for. Everything else is a tick box, grouped under the sections of the
menu: *Custom overview* (the shared dashboard; each of its cards still
needs its own area); *Movies & series* with the overview (period
comparison and now playing under it), insights and title charts,
languages; *Music* with its overview (comparison and who listened under
it) and insights and charts; *Books* in the same way (comparison and who
read under the overview, insights and charts apart); *\*Arr*; *Seerr*
with requests (and who requested); *Library* (and who watched);
*Network* traffic (and where people watch from: the addresses and the
map); *Users*. A family server can hide nothing, while a server
shared by people who should not see each other can hide a lot. Local
reader accounts have the same tree, set separately.

**Anonymising** goes with it: on the pages you do open, somebody else's
name reads `Viewer 3`, an IP address a dash, and the name of somebody
else's device (`Petr's Shield`) `Device 2` - the player stays. The
numbers are stable, so rows can still be compared, but they do not lead
to a person. Ids are replaced as well: links and the API show `skryto-3`
instead of the Jellyfin id, and addresses and filters take only that - the
real id of somebody else finds nothing, so knowing it from Jellyfin does
not tell who `Viewer 3` is. Nobody is ever anonymised to themselves.

A page a viewer cannot open answers with a short page saying why. The rule
is an allow-list, so a page nobody thought about is closed rather than
open. The menu shows only what the person can open.

**One account can override its group.** The trees in Settings apply to a
whole group: Jellyfin viewers or local reader accounts. The **Rights**
button next to an account in *Settings → Accounts* gives that one account
rights of its own, with the same tree and the same anonymising switch,
saved on the account. From then on the group settings are not read for it
at all, and switching back to *as the group* drops the account's own tree.
An account with its own rights carries a badge in the list, so nobody
wonders why a group change did not reach it.

**A viewer removed from Jellyfin is removed here.** The account created at
their first sign-in disappears at the next sync (the quick one runs every
few minutes), and an open session ends with it. Only the sign-in goes. The
viewer's history and every statistic stay exactly as they were; forgetting
a viewer's data is a separate, deliberate action in *Settings → Tasks and
backups*. An empty answer from Jellyfin deletes nothing, because that is a
connection problem and not an empty server. One local administrator
always stays (see above); only where none is left does the last
administrator from Jellyfin stay too.

#### Signing in with a code (Quick Connect)

Under the **Sign in** button is a second one: **Sign in with a code**. It
works the other way round from a password. Jellyscope shows a six-digit
code, you approve it in a Jellyfin you are already signed in to, and no
password is typed here at all. It needs Quick Connect switched on in the
Jellyfin Dashboard; when it is off, the page says so instead of failing
quietly.

The code alone lets nobody in. What finishes the sign-in is a secret that
stays in the session on the server and never reaches the browser, so a
code read over somebody's shoulder is worth nothing without the approval.
The token Jellyfin issues at the end is revoked straight away, the same as
with the password.

---

## Try it without Jellyfin

**[jellyscope.cz](https://jellyscope.cz)** is the whole app running on
made-up data. Sign in as `demo` / `demodemo` and click through everything.
Nothing there changes: every button that would write answers with a note
instead, so the demo is the same for the next visitor.

To run the same thing locally, with no server, no API key and nothing to
configure:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python demo.py
```

Open <http://127.0.0.1:8098> and sign in as `demo` / `demodemo`. It writes
into `data/demo.db`, so your real database (if you already have one) stays
untouched. It needs no `.env` and no API key, because `demo.py` sets
everything itself.

---

## Installation

### The installer (Linux)

On a Linux server one command does it:

```bash
sudo mkdir -p /opt/jellyscope
sudo chown "$USER" /opt/jellyscope
git clone https://github.com/SpeeDFireCZE/jellyscope.git /opt/jellyscope
cd /opt/jellyscope
bash deploy/install.sh
```

Another folder works too, since the installer fills the real paths into the
service configs it generates. Mind one thing: the systemd unit has
`ProtectHome=true`, so a service installed under `/home` cannot read its
own files. Either keep it out of `/home`, or delete that line from the
unit.

The script installs what is missing, creates the virtual environment,
writes `.env` with a generated key and prints how to hand the app over to
systemd or supervisord. The whole procedure (reverse proxy, HTTPS,
PostgreSQL, backups) is in **[DEPLOY.md](DEPLOY.md)**.

### Docker

```bash
git clone https://github.com/SpeeDFireCZE/jellyscope.git
cd jellyscope
cp .env.example .env
# SECRET_KEY is the one value the container refuses to start without:
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')|" .env
docker compose up -d
```

Then open <http://localhost:8097> and create the first account. Everything
is configured in the same `.env` the app uses without Docker. The data
folder is mounted from the host, so a backup is a copy of a folder.

The application inside runs as UID 10001 and never as root. When the data
folder is **empty**, the container sets its owner on the first start, so
there is nothing to do. A folder that already holds something is left
alone, and if the application cannot write into it, every page says which
folder it is and what to run. Details, including a reverse proxy and
HTTPS, are in **[DEPLOY.md](DEPLOY.md)**.

Changing the code means rebuilding the image, because `docker compose up
-d` alone reuses the one that is already built:

```bash
git pull && docker compose up -d --build
```

### By hand

Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py
```

Open **<http://127.0.0.1:8097>**. The app asks you to **create an
administrator account**. Then go to **Settings → Jellyfin**, fill in the
address and an API key (Dashboard → Advanced → API Keys), test the
connection and run **Synchronise library**.

A **Restart** button sits at the top right of Settings, for changes that
are only read at startup.

### Configuration

`.env` holds only what the app needs *before* it can open the database:

| Key | Meaning |
|---|---|
| `SECRET_KEY` | Signs login cookies. Leave it empty and the app generates one into `data/secret_key`. |
| `HOST`, `PORT` | Where to listen. Default `127.0.0.1:8097`. |
| `DATABASE_PATH` | SQLite file. Default `data/jellyscope.db`. |
| `SECURE_COOKIES` | Turn on behind an HTTPS proxy. |
| `FORWARDED_ALLOW_IPS` | The proxy's address, so the app sees real client addresses. In Docker it is the address the proxy connects from inside the Docker network, not `127.0.0.1`; see [DEPLOY.md](DEPLOY.md#reverse-proxy). |

Everything else (the Jellyfin connection, data source, tasks, language) is
configured **in the app** and stored in the database.

`data/secret_key` belongs in your backups. Without it, everyone has to sign
in again after a restore.

### Database: SQLite or PostgreSQL

**Settings → Database.** The default is **SQLite**: the whole database is
one file, there is nothing to install, and it is plenty for a household.

**PostgreSQL** makes sense when you already run one. It needs the
`psycopg` driver:

```bash
.venv/bin/python -m pip install -r requirements-postgres.txt
```

To switch, fill in the connection, then **Test connection**, **Copy data
into the selected database** (copies everything from the old database),
**Save and use after restart** and **Restart Jellyscope**.

Two safeguards: the settings are **not saved** until the connection works,
so you cannot lock yourself out, and the copy empties the target first, so
running it twice duplicates nothing.

The database settings are the one thing that does not live in the
database. They are in `data/database.json`, because they are what tells
the app how to connect.

### Updating to 1.9.0: English names in the database

Up to 1.8.x the database had Czech names of tables, columns and settings.
The first start of 1.9.0 renames them to English by itself, in one
transaction, on SQLite and PostgreSQL alike. There is nothing to do, and
an older database started later (a backup restored a year from now) is
converted the same way.

- **A backup comes first.** Before anything is renamed, the database is
  saved as it is into the backup folder:
  `jellyscope-<date>-before-conversion.db` (SQLite) or `.sql`
  (PostgreSQL). Without it nothing is converted. The usual pruning of
  backups leaves it alone; it is deleted after 30 days. Administrators see
  a stripe at the top of the pages with the file and the date it is kept
  until.
- **Checked before it counts.** Every table's rows and every setting are
  compared before and after. If anything differs, everything goes back
  and Jellyscope shows a page with the reason instead of starting.
- **A database of 1.9.0 is for 1.9.0 and newer.** 1.8.x does not know the
  new names, and running on them it would quietly lose what they guard
  (a disabled or restricted API key would let everything through again,
  a reader would see everything). So a database of 1.9.0, converted or
  new, carries a view named like the bin of 1.8.x (`playback_kos`, the one
  old name kept on purpose): a start of 1.8.x stops on it with an error
  and writes no row and no setting. On SQLite it leaves a few empty old
  tables and columns behind; the next start of 1.9.0 removes them (a
  backup comes first, as above).

**Going back to 1.8.x** means restoring that backup. What was recorded
after the conversion is lost.

1. Stop Jellyscope and put back the code of 1.8.x:
   `git reset --hard 1.8.4` (or the release you had).
2. *SQLite:* replace the database file (`data/jellyscope.db`, or the one
   in `DATABASE_PATH`) with the backup `…-before-conversion.db`, and delete
   `jellyscope.db-wal` and `jellyscope.db-shm` beside it if they are there.
   *PostgreSQL:* point 1.8.x at an empty database (`data/database.json`),
   start it, set the backup folder to the one with the file and restore
   the backup in **Settings → Tasks and backups**.
3. Start 1.8.x (in Docker, `docker compose up -d --build`, so the image is
   built from the old code).

`deploy/update.sh` makes a backup before it updates, too
(`data/before-update/`), and it holds the same state. On SQLite it goes
back the same way. On PostgreSQL it is the output of `pg_dump`, which
1.8.x cannot restore into its own tables: load it into an empty database
with `psql -d <database> -f <file>` before you start 1.8.x on it.

### Interface language

**Settings → General → Language and time** sets the language of the
whole server: English (the source language), Czech, German or Spanish. A
new installation starts in English; one updated from 1.8.x keeps the
language it had. Each account can pick its own on the **My account** page,
and the server language stays the default for everyone who has not.

**The Spanish and German translations have not been proofread by the
maintainer** (the German one comes from a contributor). If something reads
oddly or is simply wrong, a correction is welcome, through Weblate or a
pull request.

The application log file is always written in English, because it is often
sent with a bug report and English messages are easier to search for;
**Settings → Log** shows it in the reader's language.

A missing translation falls back to English, so you never get a blank
spot. That is what makes a half-finished translation usable, and why one
is welcome: **adding a language is one file** (the maintainer adds its
plural rule), not a change to the pages. The sentences live in
`jellyscope/translations/`, one JSON file per language, and the list in
Settings is built from whatever is in that folder; see
**[TRANSLATING.md](TRANSLATING.md)**.

Translating needs no git and no Python:
**<https://translate.jellyscope.cz/>** shows the English sentence with a
box for yours and sends the result to the repository on its own. A file
and a pull request work just as well.

### ffmpeg (optional)

Only needed for reading files locally. **`deploy/install.sh` installs it
for you**; the steps below are for when you skipped it with
`SKIP_FFMPEG=1` or installed by hand.

1. `sudo apt install ffmpeg`
2. In **Settings → Data collection**, switch the source to **ffprobe +
   Jellyfin** and fill in the path to `ffprobe` if it is not on `PATH`.
3. In **Settings → Tasks and backups → File analysis**, press
   **Analyse missing**.

---

## Important: history starts today

Jellyfin **does not keep** playback history. It can only tell you what is
playing right now, so Jellyscope asks every few seconds and builds the
history itself.

That means **there is no past data**. The Insights page becomes useful
after a few weeks of running, while the technical analysis of the library
works right away.

If you have history in Playback Reporting or Jellystat, import it; see
[History import](#history-import).

---

## Managing accounts from the command line

Mainly for when you cannot get into the app:

```bash
.venv/bin/python manage.py accounts        # list accounts
.venv/bin/python manage.py add jana        # add an account
.venv/bin/python manage.py add petr --admin
.venv/bin/python manage.py password petr   # change a password
.venv/bin/python manage.py delete jana
.venv/bin/python manage.py blocks          # list sign-in blocks
.venv/bin/python manage.py unblock 203.0.113.7   # lift one; --all lifts every one
```

A forgotten password cannot be read back from the hash, by design. Set a
new one with `manage.py password <name>`. The last local administrator
cannot be deleted here either. The commands of 1.8.x still work for now.

---

## Security

By default the app listens on `127.0.0.1` only, so it is reachable from
that machine alone. That is deliberate.

To open it up to your network:

1. Give everyone else **reader** accounts, not administrator ones.
2. Set `HOST=0.0.0.0`.
3. Put a reverse proxy with HTTPS in front of it, and set
   `SECURE_COOKIES=1` and `FORWARDED_ALLOW_IPS`.

What the app does on its own:

- everything is behind a login; without an account you get nowhere
- passwords are stored as PBKDF2-SHA256 hashes (600 000 iterations, salted)
- a failed login does not reveal whether the name or the password was wrong
- signing in discards the old session (session fixation)
- repeated failed logins block that address, each block longer than the
  last (1, 2, 5, 15 minutes, then permanent); administrators can lift a
  block in **Settings → Blocked logins**
- when `SECRET_KEY` is not set, a random one is generated and stored,
  never a fixed value from the source code
- permissions are enforced on the server, not by hiding buttons
- the Jellyfin API key never leaves the server, and item ids from the URL
  are validated before they are used in a request to Jellyfin; the keys of
  the \*Arr apps and Seerr are never shown back and are scrubbed from every
  error text
- no third-party JavaScript and no CDN; the pages load nothing from the
  internet - except the map tiles of OpenStreetMap, and only when the
  administrator has switched that basemap on
- every SQL query uses parameters, never string concatenation
- `ffprobe` runs without a shell, so a filename cannot become a command
- media files are only ever read; the app deletes and overwrites none of
  them
- the login cookie is signed and `SameSite=lax`; responses carry
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, a strict
  Content-Security-Policy (scripts only from the app itself), a
  Permissions-Policy, and HSTS when `SECURE_COOKIES` is on

---

## Project structure

```
jellyscope/
├── run.py                  launcher
├── demo.py                 demo mode with made-up data
├── manage.py               account management from the command line
├── requirements.txt        dependencies
├── requirements-postgres.txt  the PostgreSQL driver, pinned
├── constraints.txt         the exact versions of every indirect dependency
├── .env                    your secrets (not in git)
├── deploy/                 installer, service configs, proxy examples
├── tests/                  the test suite (needs no Jellyfin); every app of the *Arr page has its own
│                           tests (test_sonarr_*.py, test_radarr_*.py, …) and recorded responses
│                           (sonarr_responses/, radarr_responses/, …; seerr_, music_ and books_responses/ likewise)
├── data/                   database, logs, image cache (not in git)
└── jellyscope/
    ├── config.py           reads .env, generates the signing key
    ├── db.py               connection, migrations, settings cache
    ├── dialect.py          translates SQLite SQL to PostgreSQL
    ├── dbmigrate.py        copies data between the two databases
    ├── conversion.py       renames a database of 1.8.x to the English names at start
    ├── schema_names.py     the database's old (Czech) names and the new ones, for good
    ├── schema.sql          the shape of the database (schema_postgres.sql for PostgreSQL)
    ├── accounts.py         accounts, passwords, login blocks
    ├── web_sessions.py     sign-in sessions kept on the server, so signing out ends them
    ├── jellyfin.py         talking to Jellyfin
    ├── content_kinds.py    where files of content outside video live (music, books)
    ├── probe.py            ffprobe
    ├── languages.py        unifying language codes
    ├── collector.py        background playback collection
    ├── scanner/            library sync + file analysis, one file per area
    │   ├── __init__.py     the entry point: every scanner.X used elsewhere
    │   ├── task_state.py   running tasks: lock, stop, progress, task log
    │   ├── sync.py         the full library sync
    │   ├── refresh.py      one item, one series, recent changes
    │   ├── writing.py      writing items, versions, images, streams
    │   ├── merging.py      merging by TMDB, duplicates, history transfer
    │   ├── copies.py       copies of a movie in one library: which item stands for the record
    │   ├── tech_scan.py    file analysis (ffprobe or Jellyfin), video range
    │   ├── stream_languages.py  filling in stream languages
    │   ├── free_space.py   free space and the daily library snapshot
    │   ├── disks.py        library disks: device ids, virtual disks, files by disk
    │   ├── disk_snapshots.py  the daily disk snapshot and the library's free space
    │   ├── disk_settings.py  what the admin changes on the Disks card
    │   └── item_types.py   which item types Jellyscope keeps
    ├── tasks.py            scheduler and backups
    ├── images.py           the one image store: Jellyfin item images and provisional posters
    ├── purge.py            clearing out the history, forgetting a viewer
    ├── access.py           what a viewer signed in from Jellyfin sees
    ├── notifications.py    SMTP, Discord, Telegram
    ├── updates.py          asking GitHub about a newer release
    ├── release_notes.py    the release notes from markdown to HTML for the update window
    ├── arr/                the apps of the *Arr section, each in its own folder
    │   ├── __init__.py     describes the tree; imports nothing, so taking one app does not load the others
    │   ├── registry.py     the apps in one list: settings, connection test, sync and cards of each
    │   ├── sonarr/         Sonarr (series): API client, settings, history sync, live state
    │   │   ├── __init__.py     the entry point: every sonarr.X used elsewhere
    │   │   ├── client.py       the API v3 client; errors never contain the key
    │   │   ├── settings.py     address and key, "connected", connection test
    │   │   ├── sync.py         history -> the database (upsert, one-hour overlap)
    │   │   ├── live.py         the live state (queue, calendar, health; 3 s, cached a minute) plus the counts the sync stored
    │   │   ├── progress.py     live downloads: the queue grouped into series with progress, the refresh command only while downloading
    │   │   ├── posters.py      provisional posters for series that are still downloading, from Sonarr itself; posters for Seerr requests
    │   │   ├── titles.py       the series with their TVDB/TMDB/IMDb ids, matched to Jellyfin series (links into the library)
    │   │   ├── api_data.py     its statistics for the API (/api/v1/arr/sonarr/stats)
    │   │   └── demo.py         the demo's made-up Sonarr: the demo's series as Sonarr's API answers them (demo mode only)
    │   ├── radarr/         Radarr (movies): the same set of files for movies
    │   │   ├── __init__.py     the entry point: every radarr.X used elsewhere
    │   │   ├── client.py       the API v3 client; errors never contain the key
    │   │   ├── settings.py     address and key, "connected", connection test
    │   │   ├── sync.py         history -> the database (upsert, one-hour overlap)
    │   │   ├── live.py         the live state (queue, release calendar, health; 3 s, cached a minute) plus the counts the sync stored
    │   │   ├── progress.py     live downloads: the queue grouped into movies with progress, the refresh command only while downloading
    │   │   ├── posters.py      provisional posters for movies that are still downloading, from Radarr itself; posters for Seerr requests
    │   │   ├── titles.py       the movies with their TMDB/IMDb ids (links into the library)
    │   │   ├── api_data.py     its statistics for the API (/api/v1/arr/radarr/stats)
    │   │   └── demo.py         the demo's made-up Radarr: the demo's movies as Radarr's API answers them (demo mode only)
    │   ├── lidarr/         Lidarr (music): the same set of files for albums (client, settings, sync, live, progress, posters, titles,
    │   │                   api_data, demo)
    │   ├── readarr/        Readarr (books), also Chaptarr and Bookshelf: the same set of files for books; `settings.py` remembers
    │   │                   which of the three answers (the tab's subtitle)
    │   ├── bazarr/         Bazarr subtitles: API client, settings, history sync, live state
    │   │   ├── __init__.py     the entry point: every bazarr.X used elsewhere
    │   │   ├── client.py       the API client; reads only the fields that are used
    │   │   ├── settings.py     address and key, "connected", connection test
    │   │   ├── times.py        the time of history events: recognising the text format, converting to UTC
    │   │   ├── sync.py         subtitle history into bazarr_event: pages from the newest, records told apart by a fingerprint
    │   │   ├── live.py         the live state: providers, warnings (3 s, cached a minute); missing subtitles by language from the sync
    │   │   ├── api_data.py     its statistics for the API (/api/v1/arr/bazarr/stats)
    │   │   └── demo.py         the demo's made-up Bazarr: subtitles for the demo's series and movies (demo mode only)
    │   └── prowlarr/       Prowlarr indexers: API client, settings, history sync, live state, samples of the
    │                       indexers' status and the loop that measures it continuously (samples.py, watch.py)
    │       ├── __init__.py     the entry point: every prowlarr.X used elsewhere
    │       ├── client.py       the API client; query addresses and settings with keys are never read
    │       ├── settings.py     address and key, "connected", connection test
    │       ├── sync.py         history folded into hourly sums (prowlarr_hour), grabbed releases one by one; read from the newest event, written in one transaction
    │       ├── live.py         the live state: indexers and which of them Prowlarr skips right now, connected apps, warnings; cached a minute
    │       ├── api_data.py     its statistics and every indexer's availability for the API (/api/v1/arr/prowlarr/...)
    │       └── demo.py         the demo's made-up Prowlarr: the demo apps' indexers, queries and grabs, a schedule of outages (demo mode only)
    ├── books/              books: e-books and audiobooks of Jellyfin's books libraries
    │   ├── __init__.py     the entry point: every books.X used elsewhere
    │   └── sync.py         what was added every hour, everything once a day (what disappeared gets `missing`), history of imported listens paired
    ├── music/              music: artists, albums and tracks of Jellyfin's music libraries
    │   ├── __init__.py     the entry point: every music.X used elsewhere
    │   └── sync.py         what was added every hour, everything once a day (what disappeared gets `missing`), listens of imported history paired
    ├── seerr/              Seerr requests: API client, settings, request sync
    │   ├── __init__.py     the entry point: every seerr.X used elsewhere
    │   ├── client.py       the API v1 client
    │   ├── settings.py     address and key, "connected", connection test
    │   ├── sync.py         all requests -> seerr_request (status-change times, deleted in Seerr, titles)
    │   ├── live.py         the live "waiting for approval" list, cached a minute
    │   ├── latest.py       the live "latest requests", newest first, cached a minute
    │   ├── now.py          both live lists loaded together, kept under one cache entry
    │   ├── posters.py      provisional posters for titles not in Jellyfin: Radarr/Sonarr, then Seerr
    │   └── demo.py         the demo's made-up Seerr: the demo viewers' requests for its movies and series (demo mode only)
    ├── services.py         shared by the apps under arr/ and by seerr/: the client core (errors without the key, address check), the rules of the app settings
    ├── importers/          history import and its repairs, one file per area
    │   ├── __init__.py     the entry point: every importers.X used elsewhere
    │   ├── base.py         shared helpers, the read-only guard, building a row
    │   ├── matching.py     the second duplicate guard: matching by content
    │   ├── linking.py      linking imported history to items
    │   ├── creation.py     creating missing items from Jellyfin
    │   ├── pbr.py          import from Playback Reporting (API or TSV file)
    │   ├── jellystat.py    import from a Jellystat backup
    │   ├── cleanup.py      duplicates in our own history, absurd durations
    │   ├── duplicates.py   duplicates between two import sources
    │   ├── orphans.py      plays without an item: analysis, manual assignment
    │   └── tidy.py         tidying the data, the import summary
    ├── stats/              statistical SQL queries, one file per topic
    │                       (stats/sonarr.py, stats/radarr.py, stats/lidarr.py, stats/readarr.py: what was added, how fast, from where, upgrades - each app in its own file,
    │                        stats/shared.py: the calculations that know no app - time in the zone, quantile, pairing grabs with imports,
    │                        stats/seerr.py: the Seerr statistics over seerr_request,
    │                        stats/bazarr.py: the subtitle statistics over bazarr_event,
    │                        stats/music_listening.py, stats/music_library.py, stats/music_comparison.py: listening, the music library, two periods side by side,
    │                        stats/books_listening.py, stats/books_library.py, stats/books_comparison.py: the same for reading and listening to books,
    │                        stats/prowlarr.py: indexer queries from the hourly sums, grabbed releases)
    ├── insights.py         behaviour meets technique ← the core idea
    ├── langstats.py        language statistics
    ├── cards.py            the cards the dashboards are built from
    ├── cards_base.py       what a card is and what it has at hand: memo, account, live data
    ├── cards_sonarr.py     the Sonarr cards: live state, live downloads, what was added, speed, sources, upgrades
    ├── cards_radarr.py     the Radarr cards: the same set for movies
    ├── cards_lidarr.py     the Lidarr cards: the same set for music - the unit is the album (imports, upgrades, links into the Music library)
    ├── cards_readarr.py    the Readarr cards: the same set for books - the unit is the book (imports, upgrades, links into the Books library)
    ├── cards_seerr.py      the Seerr cards: requests with posters
    ├── cards_bazarr.py     the Bazarr cards: subtitles obtained, languages, providers, match, speed
    ├── cards_prowlarr.py   the Prowlarr cards: indexers now, queries, indexer performance, grabbed releases
    ├── cards_music.py      the Music cards: listening, rankings, the music library, how it is played
    ├── cards_books.py      the Books cards: listening and reading, rankings, the books library, how audiobooks are played
    ├── charts.py           hand-drawn SVG charts
    ├── worldmap.py         the map on the Network page (built-in outline, Mercator)
    ├── map_clusters.py     map dots merged per zoom level
    ├── detailed_map.py     the detailed offline map (Natural Earth tiles)
    ├── geometry.py         simplifying and cutting shapes into map tiles
    ├── geoip.py            the offline GeoLite2 lookup
    ├── formatting.py       numbers for humans
    ├── seasonal.py         a small surprise around some holidays
    ├── i18n.py             loads the translations, picks the language
    ├── translations/       one JSON file per language (+ log/)
    ├── applog.py           log file and its viewer
    ├── outage.py           the page shown when the database will not open
    ├── demodata.py         generator of made-up data for the demo
    ├── demo_world.py       the demo's world: ids in the apps' shapes, its library's catalogs, its clock, what the made-up apps share
    ├── demo_jellyfin.py    the demo's made-up Jellyfin: the series Sonarr's sync pairs with, the server's info
    ├── demo_switch.py      the demo's switch: in the demo mode the clients get made-up apps, not the network
    ├── demo_apps.py        the demo's made-up apps: registering, connecting and syncing them, deleting their data
    ├── api.py              the read-only API and its keys
    ├── api_endpoints.py    the one list of the API's addresses: area of rights, section, description, example
    ├── api_arr.py          the API's *Arr addresses: the apps, live queue, calendar, health, statistics
    ├── api_books.py        the API's books addresses: summary, charts, readings
    ├── api_library.py      the API's languages and disks
    ├── api_music.py        the API's music addresses: summary, charts, listens
    ├── api_seerr.py        the API's Seerr addresses: requests, live pending, statistics
    ├── api_query.py        filters, sorting and checks for API requests
    ├── legacy_urls.py      old page addresses and their new ones (each old one redirects)
    ├── navigation.py       the menu as data: sections, their pages, what an account sees
    ├── web/                the web app, one file per area of pages
    │   ├── __init__.py     the app: middleware, error pages, connecting the routes
    │   ├── start.py        what starts and stops with the server (lifespan)
    │   ├── body_limit.py   refuses an oversized request body before anything reads it
    │   ├── base.py         what every page needs: context, flashes, guards, CSP
    │   ├── periods.py      the period and kind choice shared by the pages
    │   ├── login.py        first run, sign-in, sign-out
    │   ├── my_account.py   my account
    │   ├── legacy.py       old page addresses redirect to the new ones; / opens the start page
    │   ├── video/          the Movies & series section (/video/...)
    │   │   ├── overview.py   the overview and its day card
    │   │   ├── compare.py    comparing two periods
    │   │   ├── insights.py   insights and the full top-titles ranking
    │   │   └── languages.py  languages, titles without a language, the preferred language
    │   ├── music/          the Music section (/music/...)
    │   │   ├── overview.py   the overview: listening, the short chart, how it is played
    │   │   ├── compare.py    two periods side by side
    │   │   ├── insights.py   the full charts and what nobody listens to
    │   │   ├── artist.py     an artist's page
    │   │   └── details.py    what the artist and album pages share
    │   ├── books/          the Books section (/books/...)
    │   │   ├── overview.py   the overview: reading and listening, the short chart, how it is played
    │   │   ├── compare.py    two periods side by side
    │   │   └── insights.py   the full charts and what nobody reads
    │   ├── album.py        an album's page in the Library (/album/<id>)
    │   ├── book.py         a book's page in the Library (/book/<id>)
    │   ├── music_library.py a music library in the Library: Overview, Albums, Activity
    │   ├── books_library.py a books library in the Library: Overview, Books, Activity
    │   ├── library_kinds.py libraries that are not video: their page and Library section
    │   ├── dashboards.py   custom dashboards (shared and My overview), shared partials
    │   ├── library.py      library, titles, items, archive, images
    │   ├── users.py        users
    │   ├── history.py      playback history
    │   ├── network.py      the Network page and its map
    │   ├── status.py       /health and /api/now-playing
    │   ├── disks.py        the Disks card's forms: capacity, virtual disks, same-disk answers
    │   ├── arr.py          the *Arr pages: one per connected app (Sonarr, Radarr, Lidarr, Readarr, Bazarr, Prowlarr)
    │   ├── seerr.py        the Seerr page: requests, handling, who, watched
    │   └── settings/       the Settings routes (all fifty-three), one file per area
    │       ├── __init__.py   the entry point: one router for all of Settings
    │       ├── page.py       the Settings page itself
    │       ├── accounts.py   accounts, permissions, viewers, blocks, API keys
    │       ├── connection.py connecting to Jellyfin and the database
    │       ├── notifications.py  notifications
    │       ├── interface.py  interface, update checks, language
    │       ├── updates.py    updating, restart, backups
    │       ├── tasks.py      sync, stop, scan, scheduled tasks, log
    │       ├── history.py    history import and tidying
    │       ├── arr.py        *Arr: save, test, sync now, delete downloaded data
    │       ├── seerr.py      Seerr: save, test, sync now, delete stored requests
    │       └── common.py     shared helpers
    ├── templates/          HTML templates
    └── static/             style.css, js/ (every script - the pages carry no inline JavaScript), map.js, events/, logo.svg
```

**The code is in English.** Since 1.9.0 the names in the code, the
comments, the database and the log file are English; only the translation
files carry other languages. The comments are long and explain *why* a
thing is done that way, usually by naming the bug that would happen
otherwise. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Versions and releases

The running version is in the bottom left corner and in *Settings →
General*. What changed in each version is in
**[CHANGELOG.md](CHANGELOG.md)**, and the release notes on GitHub are
taken from there.

Releases are tagged on GitHub as `1.2.3` (older ones as `v1.2.3`; both are
accepted). The tag has to match `__version__` in `jellyscope/__init__.py`,
and a workflow refuses to publish a release when it does not, because a
release nobody can identify is worse than none.

Jellyscope can watch for a new release: once a day it asks GitHub whether
a newer one is out and says so in the corner, and the window of a new
version shows the release notes of every release since yours. It installs
nothing by itself, and it is **off by default**. Apart from the apps you
connect yourself (Sonarr, Radarr, Lidarr, Readarr, Bazarr, Prowlarr,
Seerr), the downloads you start with a button (GeoLite2, the detailed map)
and the optional OpenStreetMap background of the map, it is the only
connection to anything other than Jellyfin, so that is your call. Updating
stays with `deploy/update.sh` (or the **Update and restart** button in that
window, outside Docker; see [DEPLOY.md](DEPLOY.md)).

---

## Common problems

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` after a restart | The service runs the system Python. Use the full path to `.venv/bin/python`. |
| Nothing answers on `:8097` | `HOST=127.0.0.1` and you are connecting from another machine. |
| Collector reports 401 | Wrong API key. Create a new one in Jellyfin. |
| Collector reports a connection error | Wrong Jellyfin address, or Jellyfin is not running. |
| Analysis fails on every file | Jellyscope cannot see the paths Jellyfin reports. Set up Path mapping. |
| Insights page is empty | Not enough history yet. Let it run for a few days. |
| Charts and library are both empty | The library sync has not run yet. |
| Everyone was signed out | The signing key changed; see `SECRET_KEY` in [Configuration](#configuration). |
| In Docker, every page says the database cannot be opened | The mounted `data/` folder belongs to somebody else. On the host: `sudo chown -R 10001:10001 ./data && docker compose restart`. Only that folder, never the whole project, or the next `git pull` stops working. |
| In Docker, a change to the code does nothing | The image was not rebuilt: `docker compose up -d --build`. |

---

## Tests

Plain scripts, no pytest. Each one sets up its own temporary database, so
they need no Jellyfin, no network and no ffmpeg:

```bash
for t in tests/test_*.py; do .venv/bin/python "$t" || break; done
```

Every file must end with `DONE - failures: 0`. Each one prints what it
checked, so a failure reads like a sentence rather than a stack trace. The
same loop runs in GitHub Actions on Python 3.11 and 3.13 for every push
and pull request.

> `test_dialect.py` checks the **shape** of translated queries, not their
> execution, which would need a running PostgreSQL server.

Sonarr, Radarr, Lidarr, Readarr, Bazarr, Prowlarr and Seerr are not started
either: their tests replay responses recorded from real instances
(`tests/<app>_responses/`). Every app has its own tests
(`tests/test_sonarr_*.py`, `tests/test_radarr_*.py`, …), so a change in one
app needs only that app's tests and code. What concerns several apps at
once is in `test_arr_registry.py`, and `test_arr_tree.py` guards that the
apps stay apart: no shared tables, templates, statistics or test helpers
between them.

---

## Contributing

Bug reports and pull requests are welcome; please open an issue first for
anything bigger than a bug fix. How to run the app, how to write a test
that can actually fail, and what falls outside the scope of the project
are in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

Translations are the easiest thing to contribute and need neither Python
nor git: **<https://translate.jellyscope.cz/>**. By hand, copy
`jellyscope/translations/en.json`, translate the values and open a pull
request. **[TRANSLATING.md](TRANSLATING.md)** has the details.

Found a security hole? Do not open an issue; see
**[SECURITY.md](SECURITY.md)**.

---

## Licence

[AGPL-3.0](LICENSE). You are free to use, change and pass it on, and
whoever does must pass on the source too, including when they run a
modified version as a service over the network. That last part is why it
is AGPL and not plain GPL: Jellyscope is a web application, and plain GPL
would let someone host a closed fork without ever publishing anything.

Versions up to and including 1.5.1 were released under MIT and stay that
way, since a licence already given cannot be taken back.

The code is written independently. Nothing was taken from Jellystat or
MediaLyze apart from the idea of what is worth measuring.
