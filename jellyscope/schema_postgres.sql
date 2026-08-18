-- ---------------------------------------------------------------------------
-- Jellyscope - schema pro PostgreSQL
--
-- Zrcadlo souboru schema.sql. Nazvy tabulek i sloupcu jsou zamerne uplne
-- stejne - jen tam, kde se databaze skutecne lisi, je jina syntaxe:
--
--   INTEGER PRIMARY KEY AUTOINCREMENT  ->  BIGSERIAL PRIMARY KEY
--   COLLATE NOCASE                     ->  jedinecny index pres LOWER(...)
--   TEXT                               ->  TEXT (stejne)
--
-- Casy ukladame jako TEXT ve tvaru "2026-08-12 09:47:00" v obou databazich.
-- Vypada to jako promarnena prilezitost (PostgreSQL ma typ timestamp), ale
-- je za tim zamer: jeden tvar dat znamena jeden tvar dotazu. Prevod na
-- casovy typ se dela az v dotazu tam, kde je potreba pocitat.
--
-- Cisla 0/1 misto BOOLEAN maji stejny duvod - aby `is_admin = 1` fungovalo
-- v obou databazich stejne.
-- ---------------------------------------------------------------------------


CREATE TABLE IF NOT EXISTS libraries (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    collection_type TEXT,
    paths           TEXT,
    synced_at       TEXT
);


CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    is_administrator INTEGER NOT NULL DEFAULT 0,
    is_disabled      INTEGER NOT NULL DEFAULT 0,
    last_activity    TEXT,
    synced_at        TEXT
);


CREATE TABLE IF NOT EXISTS items (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    type                TEXT NOT NULL,
    library_id          TEXT,
    series_id           TEXT,
    series_name         TEXT,
    season_name         TEXT,
    index_number        INTEGER,
    parent_index_number INTEGER,
    production_year     INTEGER,
    runtime_ticks       BIGINT,
    date_created        TEXT,
    path                TEXT,
    tmdb_id             TEXT,

    -- Zanry oddelene svislitkem ("Akcni|Sci-Fi"). Svislitko schvalne,
    -- ne carka: nazvy zanru ji obcas obsahuji.
    genres              TEXT,

    container           TEXT,
    video_codec         TEXT,
    audio_codec         TEXT,
    audio_channels      INTEGER,
    width               INTEGER,
    height              INTEGER,
    bitrate             BIGINT,
    size_bytes          BIGINT,
    video_range         TEXT,
    tech_source         TEXT,
    tech_updated_at     TEXT,
    tech_error          TEXT,

    audio_languages     TEXT,
    subtitle_languages  TEXT,
    default_audio_language TEXT,

    synced_at           TEXT,
    is_missing          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_items_library ON items (library_id);
CREATE INDEX IF NOT EXISTS idx_items_type    ON items (type);
CREATE INDEX IF NOT EXISTS idx_items_series  ON items (series_id);
CREATE INDEX IF NOT EXISTS idx_items_tmdb    ON items (tmdb_id);


CREATE TABLE IF NOT EXISTS item_streams (
    item_id       TEXT NOT NULL,
    stream_index  INTEGER NOT NULL,
    type          TEXT NOT NULL,
    codec         TEXT,
    language      TEXT,
    title         TEXT,
    channels      INTEGER,
    channel_layout TEXT,
    width         INTEGER,
    height        INTEGER,
    bitrate       BIGINT,
    is_default    INTEGER NOT NULL DEFAULT 0,
    is_forced     INTEGER NOT NULL DEFAULT 0,
    is_external   INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (item_id, stream_index),
    FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_streams_type ON item_streams (type);
CREATE INDEX IF NOT EXISTS idx_streams_lang ON item_streams (language);


CREATE TABLE IF NOT EXISTS playback (
    id               BIGSERIAL PRIMARY KEY,
    session_key      TEXT NOT NULL,

    user_id          TEXT,
    user_name        TEXT,

    item_id          TEXT,
    item_name        TEXT,
    item_type        TEXT,
    series_name      TEXT,
    library_id       TEXT,

    client           TEXT,
    device_name      TEXT,
    device_id        TEXT,
    remote_address   TEXT,

    play_method      TEXT,
    transcode_reasons TEXT,
    video_codec      TEXT,
    audio_codec      TEXT,
    bitrate          BIGINT,

    audio_language    TEXT,
    subtitle_language TEXT,

    started_at       TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    ended_at         TEXT,
    watched_seconds  BIGINT NOT NULL DEFAULT 0,
    paused_seconds   BIGINT NOT NULL DEFAULT 0,
    position_ticks   BIGINT,
    is_paused        INTEGER NOT NULL DEFAULT 0,

    -- Rozmery obrazu, ktery relace prave hraje. Bereme je primo z relace,
    -- ne z tabulky items: u epizody, kterou jsme jeste nesynchronizovali,
    -- by jinak rozliseni chybelo uplne.
    -- Jazyk, ktery hraje prave ted. Vedle audio_language, ktery drzi
    -- ten prvni zjisteny - kazdy odpovida na jinou otazku.
    -- Delka poradu, jak ji hlasi sama relace (viz media_runtime_ticks
    -- v collector.py). Bez ni by u nesynchronizovane polozky nesel
    -- spocitat postup prehravani.
    media_runtime_ticks       BIGINT,
    current_audio_language    TEXT,
    current_subtitle_language TEXT,
    -- Od kdy hraje soucasna kombinace stop a jestli uz se zapocitala
    -- do statistik (viz collector.MIN_LANGUAGE_SECONDS).
    language_since            TEXT,
    language_confirmed        INTEGER NOT NULL DEFAULT 0,

    video_width      INTEGER,
    video_height     INTEGER,
    is_active        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_playback_active  ON playback (is_active);
CREATE INDEX IF NOT EXISTS idx_playback_started ON playback (started_at);
CREATE INDEX IF NOT EXISTS idx_playback_user    ON playback (user_id);
CREATE INDEX IF NOT EXISTS idx_playback_item    ON playback (item_id);

-- Castecny jedinecny index: jedna relace muze mit nejvyse jedno bezici
-- prehravani. PostgreSQL i SQLite umi "index jen nad castí radku" stejne.
CREATE UNIQUE INDEX IF NOT EXISTS idx_playback_session_active
    ON playback (session_key) WHERE is_active = 1;


CREATE TABLE IF NOT EXISTS accounts (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login    TEXT
);

-- Nahrada za "COLLATE NOCASE" ze SQLite: jedinecnost se hlida nad malymi
-- pismeny, takze "Petr" a "petr" je tentyz ucet.
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username
    ON accounts (LOWER(username));


CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);


CREATE TABLE IF NOT EXISTS scan_log (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    items_total   INTEGER DEFAULT 0,
    items_ok      INTEGER DEFAULT 0,
    items_failed  INTEGER DEFAULT 0,
    status        TEXT,
    message       TEXT
);

-- Blokace prihlasovani. Kdo hada hesla, dostane pauzu - a kdyz nepresta,
-- delsi. Trva to i pres restart, jinak by stacilo pockat na aktualizaci.
CREATE TABLE IF NOT EXISTS login_blocks (
    ip            TEXT PRIMARY KEY,
    level         INTEGER NOT NULL DEFAULT 0,  -- kolikata blokace v rade
    blocked_until TEXT,                        -- do kdy (UTC); NULL = trvale
    permanent     INTEGER NOT NULL DEFAULT 0,
    failures      INTEGER NOT NULL DEFAULT 0,  -- kolik pokusu celkem
    last_failure  TEXT,
    note          TEXT
);

-- Podle druhu ulohy se ptame na kazde /health (kazdych deset vterin
-- z kazde otevrene karty). Bez indexu to znamena projit celou tabulku.
CREATE INDEX IF NOT EXISTS idx_scan_log_kind ON scan_log (kind, id);
