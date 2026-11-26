-- ---------------------------------------------------------------------------
-- Jellyscope - the database schema
--
-- The whole shape of the data is described here, in one place, in plain SQL. When
-- in half a year you wonder "what does the application actually store", the answer
-- is in this file and nowhere else.
--
-- SQLite: the database is one file on the disk, nothing is installed.
-- ---------------------------------------------------------------------------


-- The libraries of Jellyfin (Movies, Shows, Music, ...)
CREATE TABLE IF NOT EXISTS libraries (
    id              TEXT PRIMARY KEY,   -- the ItemId of the library in Jellyfin
    name            TEXT NOT NULL,
    collection_type TEXT,               -- movies / tvshows / music / ...
    paths           TEXT,               -- a JSON list of paths on the disk
    synced_at       TEXT,
    -- Runs of the library sync in a row without this library (0 = Jellyfin returned it).
    -- The Library page hides it from 2 on: one empty answer (Jellyfin starting) hides nothing.
    missing_runs    INTEGER NOT NULL DEFAULT 0
);


-- The users of Jellyfin
CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    is_administrator INTEGER NOT NULL DEFAULT 0,
    is_disabled      INTEGER NOT NULL DEFAULT 0,
    last_activity    TEXT,
    synced_at        TEXT
);


-- The items of the library (movies and episodes).
--
-- The columns are two groups:
--   1) descriptive data from Jellyfin (name, year, series, path to the file)
--   2) technical data (codec, resolution, bitrate, size)
--
-- Group 2 can come from two sources - the Jellyfin API or ffprobe. Which
-- source it was is remembered in the column tech_source, so the interface
-- shows how reliable the value is.
CREATE TABLE IF NOT EXISTS items (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    type                TEXT NOT NULL,      -- Movie / Episode
    library_id          TEXT,
    series_id           TEXT,
    series_name         TEXT,
    season_name         TEXT,
    index_number        INTEGER,            -- the number of the episode
    parent_index_number INTEGER,            -- the number of the season
    production_year     INTEGER,
    runtime_ticks       INTEGER,            -- the length; 10 000 000 ticks = 1 second
    date_created        TEXT,
    path                TEXT,               -- the path to the file as Jellyfin sees it

    -- The identifier from The Movie Database. Unlike `id` (the ItemId
    -- from Jellyfin) it survives transcoding or renaming the file - Jellyfin
    -- then creates a new item with a new ItemId, but `tmdb_id` stays the
    -- same. Thanks to it the same movie is recognised and its history of
    -- playback is kept. See scanner._merge_by_tmdb().
    tmdb_id             TEXT,

    -- Genres separated by a bar ("Action|Sci-Fi"). A bar on purpose, not a
    -- comma: the names of genres contain one now and then.
    genres              TEXT,

    -- technical data
    container           TEXT,
    video_codec         TEXT,
    audio_codec         TEXT,
    audio_channels      INTEGER,
    width               INTEGER,
    height              INTEGER,
    bitrate             INTEGER,            -- bit/s
    size_bytes          INTEGER,
    video_range         TEXT,               -- SDR / HDR / DV (measured)
    video_range_reported TEXT,              -- the same by Jellyfin
    tech_source         TEXT,               -- jellyfin or ffprobe
    tech_updated_at     TEXT,
    tech_error          TEXT,               -- why the analysis failed
    tech_error_values   TEXT,               -- the values of tech_error (a key) as JSON, i18n.message_parts

    -- languages, unified to two-letter codes and stored as "cs,en,de"
    audio_languages     TEXT,
    subtitle_languages  TEXT,
    default_audio_language TEXT,
    -- Languages the NAME OF THE FILE promises but that cannot be given to a
    -- particular track (the name names fewer of them than there are tracks
    -- without a language). They are added to the summaries above, so the
    -- statistics know them; the tracks stay "not stated", since which one it is is not known.
    audio_from_name     TEXT,
    subtitle_from_name  TEXT,            -- the first audio track in the file

    synced_at           TEXT,
    -- 1 = not in Jellyfin any more. The item is not deleted - the history of
    -- playback refers to it - it is only hidden in the archive. See stats.library_items().
    is_missing          INTEGER NOT NULL DEFAULT 0,
    -- A copy of the same movie in one library (two files in two folders are two
    -- items in Jellyfin): the secondary copy has the id of the main one, else empty.
    -- The lists of titles group by it. See scanner/copies.py.
    main_id           TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_library ON items (library_id);
CREATE INDEX IF NOT EXISTS idx_items_type    ON items (type);
CREATE INDEX IF NOT EXISTS idx_items_series  ON items (series_id);
CREATE INDEX IF NOT EXISTS idx_items_tmdb    ON items (tmdb_id);
CREATE INDEX IF NOT EXISTS idx_items_main  ON items (main_id);


-- The versions of a title: 4K beside 1080p, a remux beside a transcoded one.
--
-- Jellyfin does not make two items of two files of the same movie in a folder -
-- it makes one with two `MediaSources`. The item holds the data of the first
-- (the statistics count by it, so the numbers do not double), the others
-- lie here. Without it the library would say 1080p although a 4K one is beside it.
CREATE TABLE IF NOT EXISTS item_versions (
    item_id         TEXT NOT NULL,
    source_id       TEXT NOT NULL,      -- MediaSources[].Id from Jellyfin
    position        INTEGER NOT NULL DEFAULT 0,
    name            TEXT,               -- "2160p", "1080p" - as Jellyfin calls it
    path            TEXT,
    container       TEXT,
    video_codec     TEXT,
    audio_codec     TEXT,
    audio_channels  INTEGER,
    width           INTEGER,
    height          INTEGER,
    bitrate         BIGINT,
    size_bytes      BIGINT,
    video_range     TEXT,
    runtime_ticks   BIGINT,
    audio_languages    TEXT,
    subtitle_languages TEXT,
    synced_at       TEXT,
    PRIMARY KEY (item_id, source_id),
    FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
);


-- The records of playback.
--
-- The heart of the whole application. Jellyfin does not remember a history of
-- watching, so it is built here: every few seconds Jellyscope asks what is
-- playing right now, and joins those snapshots into continuous records.
CREATE TABLE IF NOT EXISTS playback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key      TEXT NOT NULL,       -- the id of the session + the id of the item, our "who plays what"

    user_id          TEXT,
    user_name        TEXT,

    item_id          TEXT,
    item_name        TEXT,
    item_type        TEXT,
    series_name      TEXT,
    library_id       TEXT,

    client           TEXT,                -- Jellyfin Web, Findroid, ...
    device_name      TEXT,
    device_id        TEXT,
    remote_address   TEXT,

    play_method      TEXT,                -- DirectPlay / DirectStream / Transcode
    transcode_reasons TEXT,
    video_codec      TEXT,                -- the codec that really flowed to the player
    audio_codec      TEXT,
    bitrate          INTEGER,

    -- the language of the track the viewer really played (not only which ones were there)
    audio_language    TEXT,
    subtitle_language TEXT,

    started_at       TEXT NOT NULL,       -- ISO 8601 in UTC
    last_seen_at     TEXT NOT NULL,
    ended_at         TEXT,
    watched_seconds  INTEGER NOT NULL DEFAULT 0,  -- the time without pauses
    paused_seconds   INTEGER NOT NULL DEFAULT 0,
    position_ticks   INTEGER,
    is_paused        INTEGER NOT NULL DEFAULT 0,

    -- The dimensions of the picture the session plays now. Taken straight from the
    -- session, not from the table items: for an episode not synced yet the
    -- resolution would be missing altogether.
    -- The language playing right now. Beside audio_language, which holds the
    -- first one found - each answers another question.
    -- The length of the programme as the session itself reports it (see
    -- media_runtime_ticks in collector.py). Without it the progress of an item
    -- not synced yet could not be computed.
    media_runtime_ticks       BIGINT,
    current_audio_language    TEXT,
    current_subtitle_language TEXT,
    -- Since when the current combination of tracks plays and whether it was
    -- counted into the statistics yet (see collector.MIN_LANGUAGE_SECONDS).
    language_since            TEXT,
    language_confirmed        INTEGER NOT NULL DEFAULT 0,

    -- What exactly happens in a transcode. "Transcode" alone says almost
    -- nothing: the server may convert the picture (dear), only the sound (cheap),
    -- or burn subtitles into the picture. Jellyfin reports it in TranscodingInfo
    -- and without these columns it would have to be guessed from the codecs.
    transcode_video_direct INTEGER,   -- 1 = the picture goes unchanged
    transcode_audio_direct INTEGER,   -- 1 = the sound goes unchanged
    transcode_hw           TEXT,      -- qsv, nvenc, ... or empty = the processor
    -- How fast the transcode runs (frames per second, rounded). Now - and the
    -- average over the whole play as a sum and a count of samples. Beside it the
    -- frame rate of the video itself: a transcode slower than the video means
    -- the picture stutters.
    transcode_fps          INTEGER,
    transcode_fps_sum   INTEGER NOT NULL DEFAULT 0,
    transcode_fps_samples   INTEGER NOT NULL DEFAULT 0,
    video_fps              INTEGER,
    video_width      INTEGER,
    video_height     INTEGER,
    is_active        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_playback_active  ON playback (is_active);
CREATE INDEX IF NOT EXISTS idx_playback_started ON playback (started_at);
CREATE INDEX IF NOT EXISTS idx_playback_user    ON playback (user_id);
-- idx_playback_item (item_id) was only a prefix of the covering idx_playback_item_time:
-- it did not help reading and slowed every write into playback.
DROP INDEX IF EXISTS idx_playback_item;
-- Covering indexes for the summaries "how many times and when last played" (least-played,
-- unused space, Insights). They hold everything those queries read, so for
-- 150 000 plays the table is not touched: on 40 000 files 0.5 s ->
-- 0.05 s. One for queries by titles, the other for queries over a period.
CREATE INDEX IF NOT EXISTS idx_playback_item_time ON playback (item_id, started_at, watched_seconds);
CREATE INDEX IF NOT EXISTS idx_playback_time_item ON playback (started_at, item_id, watched_seconds);
CREATE UNIQUE INDEX IF NOT EXISTS idx_playback_session_active
    ON playback (session_key) WHERE is_active = 1;


-- The single tracks of a file (video, audio, subtitles).
--
-- Why a table of its own and not more columns in `items`: one movie has one
-- video track, but easily five audio and eight subtitle ones. Columns cannot
-- hold that, and "audio_codec_2", "audio_codec_3" is a dead end.
--
-- The rule: when there can be more than one, they belong in a table of their own.
-- This is called a 1:N relation and it is the commonest shape of data at all.
--
-- The columns in `items` (audio_languages, ...) stay - they are precomputed
-- summaries for fast statistics. Here is the source of truth, there a handy extract.
CREATE TABLE IF NOT EXISTS item_streams (
    item_id       TEXT NOT NULL,
    stream_index  INTEGER NOT NULL,
    type          TEXT NOT NULL,      -- Video / Audio / Subtitle
    codec         TEXT,
    language      TEXT,               -- the unified two-letter code already
    -- Where the language comes from. Empty = from the source of technical data
    -- (the file or Jellyfin). jellyfin = the file did not state the language and it
    -- was filled in from the library - so it shows later it is not from the file.
    language_source TEXT,
    title         TEXT,               -- the label of the track from Jellyfin
    channels      INTEGER,
    channel_layout TEXT,
    width         INTEGER,
    height        INTEGER,
    bitrate       INTEGER,
    is_default    INTEGER NOT NULL DEFAULT 0,
    is_forced     INTEGER NOT NULL DEFAULT 0,
    is_external   INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (item_id, stream_index),
    -- ON DELETE CASCADE: when an item goes, its tracks go too.
    -- Without it tracks without a file would stay in the database.
    FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_streams_type ON item_streams (type);
CREATE INDEX IF NOT EXISTS idx_streams_lang ON item_streams (language);


-- The accounts of Jellyscope.
--
-- Not to be confused with the table `users` - that describes the users of Jellyfin,
-- which are only read. Here are the accounts people sign in to this application with.
--
-- COLLATE NOCASE on the name means "Petr" and "petr" is the same account.
-- Without it two people could make names that cannot be told apart.
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,      -- the fingerprint of the password, never the password itself
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login    TEXT
);


-- A custom dashboard: which cards, in what order and how wide.
--
-- `account_id` NULL = the shared layout of the server; an account id = the
-- own layout of that account, which beats the shared one.
-- See cards.load_layout().
CREATE TABLE IF NOT EXISTS dashboard_layout (
    account_id  INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    card_key    TEXT    NOT NULL,   -- the card in the registry (cards.py)
    position    INTEGER NOT NULL,
    width       TEXT                -- third / half / full; empty = as the card has it in the registry
);


-- A daily snapshot of the library.
--
-- Otherwise Jellyscope remembers only the CURRENT state: the size, the count of
-- items, the share of codecs. Only playback has a history, so the question how
-- the library grows had nothing to be answered from. One row a day is enough -
-- the library does not change during a day so much that measuring more often
-- would pay.
--
-- Written after a successful sync (see scanner.write_snapshot), so no other task
-- is needed for it. The same day is overwritten: the last known state of the day
-- holds, not the first.
CREATE TABLE IF NOT EXISTS library_snapshot (
    day            TEXT PRIMARY KEY,   -- YYYY-MM-DD in the zone of the application
    item_count     INTEGER NOT NULL,
    movie_count    INTEGER NOT NULL,
    episode_count  INTEGER NOT NULL,
    size_bytes     INTEGER NOT NULL,   -- bytes, the sum over all libraries
    uhd            INTEGER NOT NULL,   -- how many are 4K
    hdr            INTEGER NOT NULL,   -- HDR and Dolby Vision together
    without_tech   INTEGER NOT NULL,   -- items without technical data
    free_space     INTEGER,            -- on the disk with the library; NULL = not known
    written_at     TEXT NOT NULL,
    kinds          TEXT                -- parts by content kind, JSON; NULL = written before (video only)
);


-- The disks of the library day by day (the Disks card, the forecast "the space
-- runs out in"): one row per disk and day, the same day is overwritten. `disk` is
-- the DeviceId from Jellyfin, "virt:<id>" for a virtual disk, "library" for an old
-- Jellyfin without the disks broken down. `outside` = data outside Jellyfin, NULL
-- where it cannot be measured (a manual capacity, a virtual disk).
CREATE TABLE IF NOT EXISTS library_disk_snapshot (
    day         TEXT NOT NULL,          -- YYYY-MM-DD in the zone of the application
    disk        TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- physical / virtual / single
    source      TEXT NOT NULL,          -- jellyfin / manual / disk / empty
    total       INTEGER,
    free        INTEGER,
    library     INTEGER NOT NULL,
    outside     INTEGER,
    fingerprint TEXT NOT NULL DEFAULT '',   -- of the disk settings: the pace is counted only over the same one
    written_at  TEXT NOT NULL,
    kinds       TEXT,                       -- bytes by content kind, JSON; NULL = written before (video only)
    PRIMARY KEY (day, disk)
);


-- Settings that can be changed on the web (unlike .env, which is changed by hand).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);


-- A record of the scans that ran - so the interface shows when what last happened.
CREATE TABLE IF NOT EXISTS scan_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,     -- library / tech / music
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    items_total   INTEGER DEFAULT 0,
    items_ok      INTEGER DEFAULT 0,
    items_failed  INTEGER DEFAULT 0,
    status        TEXT,              -- running / done / error
    message       TEXT,
    message_values TEXT             -- the values of `message` (a key) as JSON, i18n.message_parts
);

-- Blocked sign-ins (login_blocks below): whoever guesses passwords gets a pause, and a longer
-- one when they go on. It holds over a restart, else waiting for an update would do.
-- Tokens of the reading API (jellyscope/api.py).
--
-- The FINGERPRINT is stored, not the token - whoever reads the database gets no access.
-- `preview` is the first few characters, so two tokens can be told apart in Settings;
-- nothing can be guessed from it. `permissions` and `status` come from db.MIGRATIONS.
CREATE TABLE IF NOT EXISTS api_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    preview      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_used_at TEXT
);


-- The detailed map (Natural Earth 1:10M) cut into tiles - see
-- detailed_map.py. The content is JSON with SVG paths by layers. Text, not
-- binary: pg_dump would write binary data into a backup as hex, twice as long.
CREATE TABLE IF NOT EXISTS detailed_map_tile (
    detail_level INTEGER NOT NULL,
    x            INTEGER NOT NULL,
    y            INTEGER NOT NULL,
    content      TEXT NOT NULL,
    PRIMARY KEY (detail_level, x, y)
);


-- Forgotten viewers: one row per click on "Yes, forget".
--
-- Kept for bringing back: while the rows are in the bin, the step can be undone
-- with one button; then only from the backup that is made before deleting,
-- always. When that is gone too, there is nothing to offer and the row is dropped.
CREATE TABLE IF NOT EXISTS forgettings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    session_count INTEGER NOT NULL,       -- how many plays went
    forgotten_at  TEXT    NOT NULL,       -- UTC
    backup        TEXT    NOT NULL DEFAULT '',   -- the file of the backup before deleting
    status        TEXT    NOT NULL DEFAULT 'bin' -- bin (the rows in the bin) | backup (only in the backup)
);


CREATE TABLE IF NOT EXISTS login_blocks (
    ip            TEXT PRIMARY KEY,
    level         INTEGER NOT NULL DEFAULT 0,  -- which block in a row
    blocked_until TEXT,                        -- until when (UTC); NULL = for good
    permanent     INTEGER NOT NULL DEFAULT 0,
    failures      INTEGER NOT NULL DEFAULT 0,  -- how many attempts in all
    last_failure  TEXT,
    note          TEXT
);

-- Sign-in sessions (jellyscope/web_sessions.py): one row per sign-in of a browser. The session cookie
-- carries a random token, the row only its hash - whoever reads the database signs in with nothing.
-- A page opens only while its row exists, so signing out (deleting it) ends every copy of the cookie.
-- A row unused for the cookie's life (14 days) is over and is cleaned up.
CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash    TEXT PRIMARY KEY,             -- sha256 of the token in the cookie
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL,                -- UTC, the sign-in
    last_used_at  TEXT NOT NULL                 -- UTC, moved at most every few minutes
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_account ON web_sessions (account_id);

-- Every /health asks by the kind of a task (every ten seconds from every
-- open tab). Without an index that means walking the whole table.
CREATE INDEX IF NOT EXISTS idx_scan_log_kind ON scan_log (kind, id);

-- The history of Sonarr: what was grabbed, imported into the library or deleted, and when.
-- The task "*Arr sync" downloads it (jellyscope/arr/sonarr/sync.py) from
-- /api/v3/history/since; it is a copy of the history in Sonarr, so it can be deleted
-- at any time (it is downloaded again). Every app of the *Arr page has tables of
-- its own - until 15. 11. 2026 Sonarr and Radarr had two shared ones (a database
-- in that shape is converted at start, see db._convert_arr_tables). The names
-- were Czech until 1.9.0 (converted by jellyscope/schema_names.py).
CREATE TABLE IF NOT EXISTS sonarr_event (
    arr_id        INTEGER PRIMARY KEY,        -- id of the history record in Sonarr
    event_type    TEXT NOT NULL,              -- eventType: grabbed, downloadFolderImported...
    happened_at   TEXT NOT NULL,              -- UTC, db.TIME_FORMAT
    title         TEXT NOT NULL DEFAULT '',   -- the series (deleted: the name of the release)
    episode       TEXT NOT NULL DEFAULT '',   -- S01E02
    item_id       INTEGER,                    -- episodeId: pairs the upgrades
    title_id      INTEGER,                    -- seriesId
    quality       TEXT NOT NULL DEFAULT '',   -- WEBDL-1080p...
    resolution    INTEGER,                    -- 720, 1080, 2160
    size_bytes    INTEGER,                    -- bytes (the release when grabbed, the file when imported)
    indexer       TEXT NOT NULL DEFAULT '',
    release_group TEXT NOT NULL DEFAULT '',
    client        TEXT NOT NULL DEFAULT '',   -- the download client
    protocol      TEXT NOT NULL DEFAULT '',   -- usenet / torrent
    source        TEXT NOT NULL DEFAULT '',   -- Rss, Search, UserInvokedSearch, InteractiveSearch, ReleasePush
    reason        TEXT NOT NULL DEFAULT '',   -- a deleted file: Upgrade, Manual, MissingFromDisk...
    download_id   TEXT,                       -- pairs a grab with its import
    released_at   TEXT,                       -- when the episode aired (UTC)
    cf_score      INTEGER                     -- the score of the custom formats
);
CREATE INDEX IF NOT EXISTS idx_sonarr_event_type ON sonarr_event (event_type, happened_at);
CREATE INDEX IF NOT EXISTS idx_sonarr_event_download ON sonarr_event (download_id);
CREATE INDEX IF NOT EXISTS idx_sonarr_event_item ON sonarr_event (item_id, happened_at);

-- The series of Sonarr (jellyscope/arr/sonarr/titles.py) with the ids the title
-- databases know them by. The history carries only the id of the series in Sonarr and
-- its name; by the row here its counterpart in the library is found (a link from
-- the cards): through the id of the series in Jellyfin. Paired by id, not by name -
-- Jellyfin has that in the language of the metadata. A copy of the list in Sonarr, can
-- be deleted at any time.
CREATE TABLE IF NOT EXISTS sonarr_title (
    title_id        INTEGER PRIMARY KEY,        -- seriesId (sonarr_event.title_id)
    name            TEXT NOT NULL DEFAULT '',
    tmdb_id         TEXT NOT NULL DEFAULT '',   -- digits as text
    tvdb_id         TEXT NOT NULL DEFAULT '',
    imdb_id         TEXT NOT NULL DEFAULT '',   -- tt1234567
    jellyfin_id     TEXT NOT NULL DEFAULT '',   -- the id of the series in Jellyfin (items.series_id), without dashes
    added_at        TEXT,                       -- when the series came into Sonarr (UTC); NULL = Sonarr did not say
    synced_at       TEXT                        -- when Jellyscope last saw it in Sonarr
);

-- The history of Radarr (jellyscope/arr/radarr/sync.py): the same as for Sonarr, for
-- movies. A movie has no episode, so there is no column `episode` here.
CREATE TABLE IF NOT EXISTS radarr_event (
    arr_id        INTEGER PRIMARY KEY,        -- id of the history record in Radarr
    event_type    TEXT NOT NULL,              -- eventType: grabbed, downloadFolderImported...
    happened_at   TEXT NOT NULL,              -- UTC, db.TIME_FORMAT
    title         TEXT NOT NULL DEFAULT '',   -- the movie (deleted: the name of the release)
    item_id       INTEGER,                    -- movieId: pairs the upgrades
    title_id      INTEGER,                    -- movieId
    quality       TEXT NOT NULL DEFAULT '',   -- WEBDL-1080p...
    resolution    INTEGER,                    -- 720, 1080, 2160
    size_bytes    INTEGER,                    -- bytes (the release when grabbed, the file when imported)
    indexer       TEXT NOT NULL DEFAULT '',
    release_group TEXT NOT NULL DEFAULT '',
    client        TEXT NOT NULL DEFAULT '',   -- the download client
    protocol      TEXT NOT NULL DEFAULT '',   -- usenet / torrent
    source        TEXT NOT NULL DEFAULT '',   -- Rss, Search, UserInvokedSearch, InteractiveSearch, ReleasePush
    reason        TEXT NOT NULL DEFAULT '',   -- a deleted file: Upgrade, Manual, MissingFromDisk...
    download_id   TEXT,                       -- pairs a grab with its import
    released_at   TEXT,                       -- when the movie came out (UTC): the earlier of the digital and the physical release
    cf_score      INTEGER                     -- the score of the custom formats
);
CREATE INDEX IF NOT EXISTS idx_radarr_event_type ON radarr_event (event_type, happened_at);
CREATE INDEX IF NOT EXISTS idx_radarr_event_download ON radarr_event (download_id);
CREATE INDEX IF NOT EXISTS idx_radarr_event_item ON radarr_event (item_id, happened_at);

-- The movies of Radarr (jellyscope/arr/radarr/titles.py) with the ids of the title
-- databases. A movie is found in the library through its TMDB id (items.tmdb_id)
-- when read - the id from Jellyfin is not stored for movies and a movie has no TVDB id.
CREATE TABLE IF NOT EXISTS radarr_title (
    title_id        INTEGER PRIMARY KEY,        -- movieId (radarr_event.title_id)
    name            TEXT NOT NULL DEFAULT '',
    tmdb_id         TEXT NOT NULL DEFAULT '',   -- like items.tmdb_id (digits as text)
    imdb_id         TEXT NOT NULL DEFAULT '',   -- tt1234567
    added_at        TEXT,                       -- when the movie came into Radarr (UTC); NULL = Radarr did not say
    synced_at       TEXT                        -- when Jellyscope last saw it in Radarr
);

-- The history of Lidarr (jellyscope/arr/lidarr/sync.py): the same as for Sonarr, for music. A
-- grab is one record per album, an import one per track (`item_id` = trackId pairs the
-- upgrades of a track); `title` is the album and `artist` its artist (a deleted album: the
-- name of the release, no artist). The history has no resolution - the quality is the format
-- (FLAC, MP3-320...).
CREATE TABLE IF NOT EXISTS lidarr_event (
    arr_id        INTEGER PRIMARY KEY,        -- id of the history record in Lidarr
    event_type    TEXT NOT NULL,              -- eventType: grabbed, trackFileImported, downloadImported...
    happened_at   TEXT NOT NULL,              -- UTC, db.TIME_FORMAT
    title         TEXT NOT NULL DEFAULT '',   -- the album (deleted: the name of the release)
    artist        TEXT NOT NULL DEFAULT '',   -- the artist's name
    item_id       INTEGER,                    -- trackId: pairs the upgrades (NULL for an album's record)
    title_id      INTEGER,                    -- albumId
    artist_id     INTEGER,                    -- artistId
    quality       TEXT NOT NULL DEFAULT '',   -- FLAC, FLAC 24bit, MP3-320, AAC-256...
    size_bytes    INTEGER,                    -- bytes (the release when grabbed, the file when imported)
    indexer       TEXT NOT NULL DEFAULT '',
    release_group TEXT NOT NULL DEFAULT '',
    client        TEXT NOT NULL DEFAULT '',   -- the download client
    protocol      TEXT NOT NULL DEFAULT '',   -- usenet / torrent
    source        TEXT NOT NULL DEFAULT '',   -- Rss, Search, UserInvokedSearch, InteractiveSearch, ReleasePush
    reason        TEXT NOT NULL DEFAULT '',   -- a deleted file: Upgrade, Manual, MissingFromDisk...
    download_id   TEXT,                       -- pairs a grab with its imports (NULL = a manual import)
    released_at   TEXT,                       -- when the album came out (UTC)
    cf_score      INTEGER                     -- the score of the custom formats
);
CREATE INDEX IF NOT EXISTS idx_lidarr_event_type ON lidarr_event (event_type, happened_at);
CREATE INDEX IF NOT EXISTS idx_lidarr_event_download ON lidarr_event (download_id);
CREATE INDEX IF NOT EXISTS idx_lidarr_event_item ON lidarr_event (item_id, happened_at);

-- The albums of Lidarr (jellyscope/arr/lidarr/titles.py) with the MusicBrainz ids and their
-- counterparts in the music library (music_album, music_artist): by a MusicBrainz release id,
-- else by the name of the album and its artist. A copy of the list in Lidarr, can be deleted
-- at any time.
CREATE TABLE IF NOT EXISTS lidarr_title (
    title_id           INTEGER PRIMARY KEY,        -- albumId (lidarr_event.title_id)
    name               TEXT NOT NULL DEFAULT '',
    artist             TEXT NOT NULL DEFAULT '',   -- the artist's name
    artist_id          INTEGER,                    -- artistId (lidarr_event.artist_id)
    mb_release_group   TEXT NOT NULL DEFAULT '',   -- foreignAlbumId: the MusicBrainz release group
    mb_releases        TEXT NOT NULL DEFAULT '',   -- the MusicBrainz ids of the releases, separated by a bar
    mb_artist          TEXT NOT NULL DEFAULT '',   -- foreignArtistId: the MusicBrainz artist
    track_count        INTEGER NOT NULL DEFAULT 0,
    released_at        TEXT,                       -- when the album came out (UTC)
    added_at           TEXT,                       -- when the artist came into Lidarr (UTC); NULL = Lidarr did not say
    jellyfin_id        TEXT NOT NULL DEFAULT '',   -- music_album.id, without dashes; empty = not in the library
    jellyfin_artist_id TEXT NOT NULL DEFAULT '',   -- music_artist.id; empty = not in the library
    synced_at          TEXT                        -- when Jellyscope last saw it in Lidarr
);

-- The history of Readarr (jellyscope/arr/readarr/sync.py; Chaptarr and Bookshelf answer the same API):
-- the same as for Lidarr, for books. A grab is one record per book, an import one per book file
-- (`item_id` = fileId of the file); `title` is the book and `author` its author (a deleted book: the
-- name of the release). The history has no resolution - the quality is the format (EPUB, AZW3, MP3...).
-- An upgrade is paired by the book (the new files have other ids), a manual import by the book and day.
CREATE TABLE IF NOT EXISTS readarr_event (
    arr_id        INTEGER PRIMARY KEY,        -- id of the history record in Readarr
    event_type    TEXT NOT NULL,              -- eventType: grabbed, bookFileImported, downloadFailed...
    happened_at   TEXT NOT NULL,              -- UTC, db.TIME_FORMAT
    title         TEXT NOT NULL DEFAULT '',   -- the book (deleted: the name of the release)
    author        TEXT NOT NULL DEFAULT '',   -- the author's name
    item_id       INTEGER,                    -- fileId of an import (NULL for the other events)
    title_id      INTEGER,                    -- bookId
    author_id     INTEGER,                    -- authorId
    quality       TEXT NOT NULL DEFAULT '',   -- EPUB, AZW3, PDF, MP3...
    size_bytes    INTEGER,                    -- bytes (the release when grabbed, the file when imported)
    indexer       TEXT NOT NULL DEFAULT '',
    release_group TEXT NOT NULL DEFAULT '',
    client        TEXT NOT NULL DEFAULT '',   -- the download client (an import: the instance's name, a grab: its type)
    protocol      TEXT NOT NULL DEFAULT '',   -- usenet / torrent
    source        TEXT NOT NULL DEFAULT '',   -- Rss, Search, UserInvokedSearch, InteractiveSearch, ReleasePush
    reason        TEXT NOT NULL DEFAULT '',   -- a deleted file: Upgrade, Manual, MissingFromDisk...
    download_id   TEXT,                       -- pairs a grab with its imports (NULL = a manual import)
    released_at   TEXT,                       -- when the book came out (UTC)
    cf_score      INTEGER                     -- the score of the custom formats
);
CREATE INDEX IF NOT EXISTS idx_readarr_event_type ON readarr_event (event_type, happened_at);
CREATE INDEX IF NOT EXISTS idx_readarr_event_download ON readarr_event (download_id);
CREATE INDEX IF NOT EXISTS idx_readarr_event_item ON readarr_event (item_id, happened_at);

-- The books of Readarr (jellyscope/arr/readarr/titles.py) with the ISBNs of their editions and their
-- counterparts in the books library (book): by an ISBN-13 of any edition, else by the title and the
-- author folded like the books section folds them. A copy of the list in Readarr, can be deleted at
-- any time.
CREATE TABLE IF NOT EXISTS readarr_title (
    title_id           INTEGER PRIMARY KEY,        -- bookId (readarr_event.title_id)
    name               TEXT NOT NULL DEFAULT '',
    author             TEXT NOT NULL DEFAULT '',   -- the author's name
    author_id          INTEGER,                    -- authorId (readarr_event.author_id)
    foreign_book_id    TEXT NOT NULL DEFAULT '',   -- foreignBookId: the id in the metadata source
    isbns              TEXT NOT NULL DEFAULT '',   -- the ISBN-13 of the editions, separated by a bar
    asins              TEXT NOT NULL DEFAULT '',   -- the ASINs of the editions, separated by a bar
    is_ebook           INTEGER NOT NULL DEFAULT 0, -- 1 = an edition is an e-book
    released_at        TEXT,                       -- when the book came out (UTC)
    added_at           TEXT,                       -- when the book came into Readarr (UTC); NULL = Readarr did not say
    jellyfin_id        TEXT NOT NULL DEFAULT '',   -- book.id; empty = not in the library
    synced_at          TEXT                        -- when Jellyscope last saw it in Readarr
);

-- Requests from Seerr (jellyscope/seerr/sync.py): who asked for what and when, how the request was
-- handled and when the title came into the library. The task "Seerr sync" downloads them whole; a
-- request deleted in Seerr stays here, marked. The names were Czech until 1.9.0 (converted by
-- jellyscope/schema_names.py).
CREATE TABLE IF NOT EXISTS seerr_request (
    id                    INTEGER PRIMARY KEY,         -- the id of the request in Seerr
    media_type            TEXT NOT NULL,               -- movie / tv
    tmdb_id               TEXT NOT NULL DEFAULT '',    -- like items.tmdb_id (text)
    jellyfin_id           TEXT NOT NULL DEFAULT '',    -- media.jellyfinMediaId: items.id of a movie / series_id of the episodes
    tvdb_id               TEXT NOT NULL DEFAULT '',    -- media.tvdbId (text, empty = none): a series is looked up in Sonarr by it
    title                 TEXT NOT NULL DEFAULT '',    -- from the library or from Seerr, empty = not known yet
    seasons               INTEGER,                     -- how many seasons the request has, NULL for a movie
    is_4k                 INTEGER NOT NULL DEFAULT 0,
    auto_request          INTEGER NOT NULL DEFAULT 0,  -- from a watchlist (isAutoRequest)
    status                INTEGER NOT NULL,            -- 1 pending, 2 approved, 3 declined, 4 failed, 5 done
    media_status          INTEGER,                     -- media.status (status4k for 4K): 4 partially, 5 available...
    requester_seerr_id    INTEGER,
    requester_name        TEXT NOT NULL DEFAULT '',    -- the name in Seerr
    requester_jellyfin_id TEXT NOT NULL DEFAULT '',    -- lower case, without dashes
    handled_by_name       TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL,               -- UTC, db.TIME_FORMAT
    updated_at            TEXT,
    available_at          TEXT,                        -- when the title came into the library (only 4/5, otherwise NULL)
    seen_pending          INTEGER NOT NULL DEFAULT 0,  -- Jellyscope saw it in the state pending
    approved_at           TEXT,                        -- the time of the approval (only after pending)
    declined_at           TEXT,                        -- the time of the decline (only after pending)
    deleted_at            TEXT,                        -- when it vanished from Seerr, NULL = it exists
    synced_at             TEXT,                        -- when Jellyscope last saw it
    poster                TEXT NOT NULL DEFAULT ''     -- the path of the poster from Seerr (posterPath); empty = not looked up, a dash = it has none
);
CREATE INDEX IF NOT EXISTS idx_seerr_request_created ON seerr_request (created_at);
CREATE INDEX IF NOT EXISTS idx_seerr_request_requester ON seerr_request (requester_jellyfin_id, created_at);
CREATE INDEX IF NOT EXISTS idx_seerr_request_tmdb ON seerr_request (tmdb_id, media_type);

-- The subtitle history of Bazarr (jellyscope/arr/bazarr/sync.py): what happened to the subtitles
-- of which episode or movie - downloaded, replaced by better ones, uploaded, deleted - in which
-- language, from which provider and with what match. A copy of the history of Bazarr, can be
-- deleted at any time. Bazarr records have no number: a row is known by its fingerprint. The names
-- were Czech until 1.9.0 (converted by jellyscope/schema_names.py).
CREATE TABLE IF NOT EXISTS bazarr_event (
    fingerprint     TEXT PRIMARY KEY,           -- SHA-1 of what the record is known by (see sync.fingerprint)
    kind            TEXT NOT NULL,              -- episode / movie
    title           TEXT NOT NULL DEFAULT '',   -- the name of the series or the movie
    episode         TEXT NOT NULL DEFAULT '',   -- S01E02, empty for a movie
    title_id        INTEGER,                    -- the id of the series in Sonarr / of the movie in Radarr (like title_id in their histories)
    item_id         INTEGER,                    -- the id of the episode in Sonarr / of the movie in Radarr (like item_id in their histories)
    action          INTEGER NOT NULL,           -- 0 deleted, 1 downloaded, 2 manual, 3 upgrade, 4 uploaded, 5 synced, 6 translated, 7 extracted
    language        TEXT NOT NULL DEFAULT '',   -- the language code of the subtitles (cs, en)
    hi              INTEGER NOT NULL DEFAULT 0, -- subtitles for the hearing impaired
    forced          INTEGER NOT NULL DEFAULT 0, -- forced subtitles (only the foreign-language parts)
    provider        TEXT NOT NULL DEFAULT '',   -- where they come from; manual = uploaded by hand
    score           REAL,                        -- the match in per cent; NULL = Bazarr did not give it
    happened_at     TEXT,                       -- the time of the event (UTC); NULL = the time could not be read
    time_exact      INTEGER NOT NULL DEFAULT 1, -- 1 = the time from Bazarr, 0 = the time Jellyscope first saw the record
    synced_at       TEXT                        -- when Jellyscope last saw it in Bazarr
);
CREATE INDEX IF NOT EXISTS idx_bazarr_event_time ON bazarr_event (happened_at);
CREATE INDEX IF NOT EXISTS idx_bazarr_event_item ON bazarr_event (kind, item_id, language, happened_at);
-- A partial index: records without an exact time are usually none and Settings → *Arr asks for them on every view.
CREATE INDEX IF NOT EXISTS idx_bazarr_event_inexact ON bazarr_event (time_exact) WHERE time_exact = 0;

-- Prowlarr (jellyscope/arr/prowlarr/sync.py). There are tens of thousands of queries to the indexers
-- a day, so each is not stored on its own: the history of Prowlarr is summed up by hours, indexers,
-- apps and kinds. A sum cannot be rewritten as a row - what is added once stays; which events are
-- counted already the settings arr_prowlarr_last_id and arr_prowlarr_last_event_at tell (the number and
-- the time of the newest counted event; written in the same transaction as the sums). The names were
-- Czech until 1.9.0 (converted by jellyscope/schema_names.py).
CREATE TABLE IF NOT EXISTS prowlarr_hour (
    hour           TEXT NOT NULL,              -- the start of the hour in UTC (YYYY-MM-DD HH:00:00)
    indexer_id     INTEGER NOT NULL,           -- the number of the indexer in Prowlarr (0 = unknown)
    app            TEXT NOT NULL DEFAULT '',   -- who asked (Sonarr, Radarr, Prowlarr...); empty = unknown
    kind           TEXT NOT NULL,              -- query (a search) / rss / auth (a login to the indexer)
    event_count    INTEGER NOT NULL DEFAULT 0, -- how many events
    failed_count   INTEGER NOT NULL DEFAULT 0, -- how many of them failed
    response_sum   INTEGER NOT NULL DEFAULT 0, -- the sum of the response times in ms - only successful queries outside the cache of Prowlarr
    response_count INTEGER NOT NULL DEFAULT 0, -- how many response times are in the sum; the average = the share
    with_results   INTEGER NOT NULL DEFAULT 0, -- how many successful queries returned at least one release
    PRIMARY KEY (hour, indexer_id, app, kind)
);

-- The releases the apps grabbed through Prowlarr (downloaded from an indexer) - one by one, they are few.
-- The key is the number of the event with its time: after Prowlarr is reinstalled the numbers start at 1
-- again and the number alone would overwrite an old row.
CREATE TABLE IF NOT EXISTS prowlarr_grab (
    id          INTEGER NOT NULL,              -- the number of the event in the history of Prowlarr
    happened_at TEXT NOT NULL,                 -- UTC, db.TIME_FORMAT
    indexer_id  INTEGER NOT NULL DEFAULT 0,    -- the number of the indexer in Prowlarr (0 = unknown)
    app         TEXT NOT NULL DEFAULT '',      -- who took the release
    title       TEXT NOT NULL DEFAULT '',      -- the name of the release
    method      TEXT NOT NULL DEFAULT '',      -- proxy = Prowlarr downloaded the file itself and knows the result, redirect = it only redirected
    succeeded   INTEGER NOT NULL DEFAULT 1,    -- 1 / 0; always 1 for a redirect and says nothing
    response    INTEGER,                       -- ms until the indexer gave the file; NULL = unknown (always for a redirect)
    client      TEXT NOT NULL DEFAULT '',      -- the download client, when Prowlarr knows it
    PRIMARY KEY (id, happened_at)
);
CREATE INDEX IF NOT EXISTS idx_prowlarr_grab_time ON prowlarr_grab (happened_at);

-- The names of the indexers for the numbers of the two tables above. An indexer deleted in Prowlarr
-- stays here (removed = 1), so the old numbers are not left without a name.
CREATE TABLE IF NOT EXISTS prowlarr_indexer (
    id       INTEGER PRIMARY KEY,             -- the number of the indexer in Prowlarr
    name     TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT '',        -- usenet / torrent
    privacy  TEXT NOT NULL DEFAULT '',        -- public / semiPrivate / private
    enabled  INTEGER NOT NULL DEFAULT 1,
    removed  INTEGER NOT NULL DEFAULT 0,      -- 1 = no longer in Prowlarr
    seen_at  TEXT                             -- when Jellyscope last saw it in Prowlarr
);

-- Samples of the indexers' status (jellyscope/arr/prowlarr/samples.py). A block is silent in the history (an
-- indexer that refuses connections writes nothing there, Prowlarr only leaves it out for a while), so Jellyscope
-- asks now and then who is left out and keeps the answer. Every sample has a row of its own (indexer_id 0) and a
-- row for every indexer left out at that moment: without the first, "nobody left out" could not be told from
-- "not measured". The sample's own row says which indexers were there and enabled at that moment and how often
-- samples came then: an indexer's time is measured only while it was there, and a gap is judged by the interval
-- of its time, not by today's setting. A sample of the background loop (watch.py) also says until when the loop saw
-- the same state at every round (`measured_until`): the loop stores a new sample only when the state changes, and
-- every ten minutes. The indexers' numbers are those of the tables above (negative = set aside after a renumbering).
CREATE TABLE IF NOT EXISTS prowlarr_sample (
    sampled_at       TEXT NOT NULL,           -- UTC, db.TIME_FORMAT: when the sample was taken
    indexer_id       INTEGER NOT NULL,        -- the indexer left out; 0 = the row of the sample itself
    failing_since    TEXT,                    -- UTC: since when it fails (Prowlarr's initialFailure)
    skipped_until    TEXT,                    -- UTC: until when Prowlarr leaves it out (disabledTill)
    last_failure     TEXT,                    -- UTC: its most recent failure (mostRecentFailure)
    enabled_ids      TEXT,                    -- the sample's own row: the indexers there and enabled, ",1,3,7,"
    interval_seconds INTEGER,                 -- the sample's own row: the usual time between samples then
    measured_until   TEXT,                    -- the loop's sample's own row: UTC, its last round; NULL = the task's or a page's
    PRIMARY KEY (sampled_at, indexer_id)
);

-- Music (jellyscope/music/sync.py): the artists, albums and tracks of the music libraries
-- of Jellyfin. The id is the id of the item in Jellyfin. A row gone from Jellyfin stays with `missing = 1`:
-- the history of listening (playback, item_type Audio, item_id = the id of the track) and the charts
-- would lose the names otherwise. The listening itself is in the table playback, these do not repeat it.
CREATE TABLE IF NOT EXISTS music_artist (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    genres          TEXT NOT NULL DEFAULT '',   -- separated by a bar, as with movies
    musicbrainz_id  TEXT NOT NULL DEFAULT '',
    image_tag       TEXT NOT NULL DEFAULT '',   -- the fingerprint of the image in Jellyfin (empty = no image)
    synced_at       TEXT,                       -- when Jellyscope last saw it in Jellyfin
    missing         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS music_album (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    artist_id       TEXT,                       -- the artist of the album ("Various Artists" for compilations)
    artist          TEXT NOT NULL DEFAULT '',
    year            INTEGER,
    genres          TEXT NOT NULL DEFAULT '',
    musicbrainz_id  TEXT NOT NULL DEFAULT '',
    cover_tag       TEXT NOT NULL DEFAULT '',
    added_at        TEXT,                       -- when the album came into Jellyfin (UTC)
    library_id      TEXT,
    synced_at       TEXT,
    missing         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS music_track (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    album_id        TEXT,
    album           TEXT NOT NULL DEFAULT '',
    artist_id       TEXT,                       -- the first artist of the track (for the charts)
    artist          TEXT NOT NULL DEFAULT '',
    artists         TEXT NOT NULL DEFAULT '',   -- all artists of the track, separated by a bar
    track_number    INTEGER,                    -- the number of the track on the disc
    disk            INTEGER,
    runtime_ticks   INTEGER,                     -- 10 000 000 ticks = 1 second
    year            INTEGER,
    genres          TEXT NOT NULL DEFAULT '',
    container       TEXT NOT NULL DEFAULT '',   -- as Jellyfin reports it ("mov,mp4,m4a,..." for m4a)
    codec           TEXT NOT NULL DEFAULT '',   -- the codec of the audio track: flac, mp3, aac... (the format by it)
    bitrate         INTEGER,                    -- bit/s
    sample_rate     INTEGER,                    -- Hz
    bit_depth       INTEGER,                    -- bits per sample (lossy formats have none)
    channels        INTEGER,
    size_bytes      INTEGER,                     -- bytes
    path            TEXT,                        -- file path (MediaSources); NULL = not asked yet, '' = none given
    added_at        TEXT,
    library_id      TEXT,
    synced_at       TEXT,
    missing         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_music_track_album ON music_track (album_id);
CREATE INDEX IF NOT EXISTS idx_music_track_artist ON music_track (artist_id);
CREATE INDEX IF NOT EXISTS idx_music_track_library ON music_track (library_id);
CREATE INDEX IF NOT EXISTS idx_music_album_artist ON music_album (artist_id);
-- "Is there music?" (the menu on every page): only the tracks that are in Jellyfin.
CREATE INDEX IF NOT EXISTS idx_music_track_missing ON music_track (missing);

-- Books (jellyscope/books/sync.py): the e-books (`Book`) and audiobooks (`AudioBook`) of the books libraries
-- of Jellyfin. The id is the id of the item in Jellyfin. A row gone from Jellyfin stays with `missing = 1`:
-- the history (playback, item_type AudioBook / Book, item_id = the id of the item) and the charts would lose the
-- names otherwise. A multi-file audiobook is several rows with the same `title` and `author`.
CREATE TABLE IF NOT EXISTS book (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL DEFAULT 'book',   -- 'book' (e-book) | 'audiobook'
    name            TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',   -- the work: an audiobook's Album when it has one, else the name
    author          TEXT NOT NULL DEFAULT '',   -- People Author, else AlbumArtist, else the first of Artists
    title_key       TEXT NOT NULL DEFAULT '',   -- title and author folded (books.book_title_key): the parts of one audiobook share it
    series          TEXT NOT NULL DEFAULT '',
    year            INTEGER,
    genres          TEXT NOT NULL DEFAULT '',   -- separated by a bar
    format          TEXT NOT NULL DEFAULT '',   -- the file extension in lower case (epub, pdf, m4b, mp3...)
    size_bytes      INTEGER,
    runtime_ticks   INTEGER,                     -- audiobooks only; 10 000 000 ticks = 1 second
    path            TEXT,                       -- file path (MediaSources); '' = none given
    isbn            TEXT NOT NULL DEFAULT '',
    provider_ids    TEXT NOT NULL DEFAULT '',   -- ProviderIds as JSON (for Readarr and the like)
    cover_tag       TEXT NOT NULL DEFAULT '',
    added_at        TEXT,                       -- when the item came into Jellyfin (UTC)
    library_id      TEXT,
    synced_at       TEXT,
    missing         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_book_library ON book (library_id);
CREATE INDEX IF NOT EXISTS idx_book_author ON book (author);
CREATE INDEX IF NOT EXISTS idx_book_title_key ON book (title_key);
-- "Are there books?" (the menu on every page): only the items that are in Jellyfin.
CREATE INDEX IF NOT EXISTS idx_book_missing ON book (missing);
