-- ---------------------------------------------------------------------------
-- Jellyscope - schema databaze
--
-- Cely tvar dat je popsany tady, na jednom miste, obycejnym SQL. Kdyz budes
-- za pul roku premyslet "co ta aplikace vlastne uklada", odpoved je v tomhle
-- souboru a nikde jinde.
--
-- Pouzivame SQLite: databaze je jeden soubor na disku, nic se neinstaluje.
-- ---------------------------------------------------------------------------


-- Knihovny z Jellyfinu (Filmy, Serialy, Hudba, ...)
CREATE TABLE IF NOT EXISTS libraries (
    id              TEXT PRIMARY KEY,   -- ItemId knihovny v Jellyfinu
    name            TEXT NOT NULL,
    collection_type TEXT,               -- movies / tvshows / music / ...
    paths           TEXT,               -- JSON seznam cest na disku
    synced_at       TEXT
);


-- Uzivatele Jellyfinu
CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    is_administrator INTEGER NOT NULL DEFAULT 0,
    is_disabled      INTEGER NOT NULL DEFAULT 0,
    last_activity    TEXT,
    synced_at        TEXT
);


-- Polozky knihovny (filmy a epizody).
--
-- Sloupce jsou dve skupiny:
--   1) popisna data z Jellyfinu (nazev, rok, serial, cesta k souboru)
--   2) technicka data (kodek, rozliseni, bitrate, velikost)
--
-- Skupina 2 muze pochazet ze dvou zdroju - z Jellyfin API, nebo z ffprobe.
-- Ktery zdroj to byl, si pamatujeme ve sloupci tech_source, aby bylo v UI
-- videt, jak spolehlivy ten udaj je.
CREATE TABLE IF NOT EXISTS items (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    type                TEXT NOT NULL,      -- Movie / Episode
    library_id          TEXT,
    series_id           TEXT,
    series_name         TEXT,
    season_name         TEXT,
    index_number        INTEGER,            -- cislo epizody
    parent_index_number INTEGER,            -- cislo serie
    production_year     INTEGER,
    runtime_ticks       INTEGER,            -- delka; 10 000 000 ticku = 1 sekunda
    date_created        TEXT,
    path                TEXT,               -- cesta k souboru tak, jak ji vidi Jellyfin

    -- Identifikator z The Movie Database. Na rozdil od `id` (ItemId
    -- z Jellyfinu) prezije prekodovani nebo prejmenovani souboru - Jellyfin
    -- v takovem pripade zalozi novou polozku s novym ItemId, ale tmdb_id
    -- zustane stejne. Diky tomu poznáme, ze jde o tentyz film, a historii
    -- prehravani si udrzime. Viz scanner._merge_by_tmdb().
    tmdb_id             TEXT,

    -- Zanry oddelene svislitkem ("Akcni|Sci-Fi"). Svislitko schvalne,
    -- ne carka: nazvy zanru ji obcas obsahuji.
    genres              TEXT,

    -- technicka data
    container           TEXT,
    video_codec         TEXT,
    audio_codec         TEXT,
    audio_channels      INTEGER,
    width               INTEGER,
    height              INTEGER,
    bitrate             INTEGER,            -- bit/s
    size_bytes          INTEGER,
    video_range         TEXT,               -- SDR / HDR / DV (zmereno)
    video_range_reported TEXT,              -- totez podle Jellyfinu
    tech_source         TEXT,               -- 'jellyfin' nebo 'ffprobe'
    tech_updated_at     TEXT,
    tech_error          TEXT,               -- proc se analyza nepovedla

    -- jazyky, sjednocene na dvoupismenne kody a ulozene jako "cs,en,de"
    audio_languages     TEXT,
    subtitle_languages  TEXT,
    default_audio_language TEXT,
    -- Jazyky, ktere slibuje NAZEV SOUBORU, ale nejde je priradit ke
    -- konkretni stope (nazev jich jmenuje min, nez kolik je stop bez
    -- jazyka). Do souhrnu vyse se pricitaji, at je statistika zna;
    -- u stop zustava "neuvedeno", protoze kterou z nich to je, nevime.
    audio_from_name     TEXT,
    subtitle_from_name  TEXT,            -- prvni zvukova stopa v souboru

    synced_at           TEXT,
    -- 1 = uz v Jellyfinu neni. Polozku nemazeme - historie prehravani
    -- na ni odkazuje - jen ji schovame do archivu. Viz stats.library_items().
    is_missing          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_items_library ON items (library_id);
CREATE INDEX IF NOT EXISTS idx_items_type    ON items (type);
CREATE INDEX IF NOT EXISTS idx_items_series  ON items (series_id);
CREATE INDEX IF NOT EXISTS idx_items_tmdb    ON items (tmdb_id);


-- Zaznamy o prehravani.
--
-- Tohle je srdce cele aplikace. Jellyfin si historii sledovani nepamatuje,
-- takze si ji musime postavit sami: kazdych par sekund se zeptame, co se
-- prave hraje, a z tech snimku poskladame souvisle zaznamy.
CREATE TABLE IF NOT EXISTS playback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key      TEXT NOT NULL,       -- id relace + id polozky, nase "kdo co hraje"

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
    video_codec      TEXT,                -- kodek, ktery skutecne tekl k prehravaci
    audio_codec      TEXT,
    bitrate          INTEGER,

    -- jazyk stopy, kterou si divak skutecne pustil (ne jen ktera byla k dispozici)
    audio_language    TEXT,
    subtitle_language TEXT,

    started_at       TEXT NOT NULL,       -- ISO 8601 v UTC
    last_seen_at     TEXT NOT NULL,
    ended_at         TEXT,
    watched_seconds  INTEGER NOT NULL DEFAULT 0,  -- cas bez pauz
    paused_seconds   INTEGER NOT NULL DEFAULT 0,
    position_ticks   INTEGER,
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

    -- Co presne se pri prepoctu deje. Samotne "transcode" totiz nerika
    -- skoro nic: server muze prepocitavat obraz (drahe), jen zvuk (levne),
    -- nebo do obrazu vypalovat titulky. Jellyfin to hlasi v TranscodingInfo
    -- a bez techto sloupcu bychom to museli hadat z kodeku.
    transcode_video_direct INTEGER,   -- 1 = obraz jde beze zmeny
    transcode_audio_direct INTEGER,   -- 1 = zvuk jde beze zmeny
    transcode_hw           TEXT,      -- qsv, nvenc, ... nebo prazdne = procesor
    video_width      INTEGER,
    video_height     INTEGER,
    is_active        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_playback_active  ON playback (is_active);
CREATE INDEX IF NOT EXISTS idx_playback_started ON playback (started_at);
CREATE INDEX IF NOT EXISTS idx_playback_user    ON playback (user_id);
CREATE INDEX IF NOT EXISTS idx_playback_item    ON playback (item_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_playback_session_active
    ON playback (session_key) WHERE is_active = 1;


-- Jednotlive stopy souboru (video, zvuk, titulky).
--
-- Proc zvlastni tabulka a ne dalsi sloupce v `items`: jeden film ma jednu
-- video stopu, ale klidne pet zvukovych a osm titulkovych. Do sloupcu se
-- to nevejde a "audio_codec_2", "audio_codec_3" je slepa ulicka.
--
-- Pravidlo: kdyz jich muze byt vic nez jedna, patri do vlastni tabulky.
-- Tomuhle se rika vztah 1:N a je to nejcastejsi tvar dat vubec.
--
-- Sloupce v `items` (audio_languages, ...) zustavaji - jsou to predpocitane
-- souhrny pro rychle statistiky. Tady je zdroj pravdy, tam pohodlny vytah.
CREATE TABLE IF NOT EXISTS item_streams (
    item_id       TEXT NOT NULL,
    stream_index  INTEGER NOT NULL,
    type          TEXT NOT NULL,      -- Video / Audio / Subtitle
    codec         TEXT,
    language      TEXT,               -- uz sjednoceny dvoupismenny kod
    -- Odkud jazyk je. Prazdne = ze zdroje technickych dat (soubor nebo
    -- Jellyfin). 'jellyfin' = soubor jazyk neuvadel a doplnili jsme ho
    -- z knihovny - at je pozdeji poznat, ze to neni udaj ze souboru.
    language_source TEXT,
    title         TEXT,               -- popisek stopy z Jellyfinu
    channels      INTEGER,
    channel_layout TEXT,
    width         INTEGER,
    height        INTEGER,
    bitrate       INTEGER,
    is_default    INTEGER NOT NULL DEFAULT 0,
    is_forced     INTEGER NOT NULL DEFAULT 0,
    is_external   INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (item_id, stream_index),
    -- ON DELETE CASCADE: kdyz zmizi polozka, zmizi i jeji stopy.
    -- Bez toho by v databazi zustavaly stopy bez souboru.
    FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_streams_type ON item_streams (type);
CREATE INDEX IF NOT EXISTS idx_streams_lang ON item_streams (language);


-- Ucty do Jellyscope.
--
-- Nezamenovat s tabulkou `users` - ta popisuje uzivatele Jellyfinu, ktere
-- jen ctem. Tady jsou ucty, kterymi se nekdo prihlasuje do teto aplikace.
--
-- COLLATE NOCASE u jmena znamena, ze "Petr" a "petr" je tentyz ucet.
-- Bez toho by si dva lide mohli zalozit jmena, ktera nejdou rozlisit.
-- Vlastni prehled: ktere sekce a v jakem poradi.
--
-- `account_id` je pripravene na pozdejsi uzivatelska nastaveni. Dnes je
-- vsude NULL = spolecne rozvrzeni serveru; az bude cim ho prebit, pribudou
-- radky s konkretnim uctem. Viz sekce.nacti_rozvrzeni().
CREATE TABLE IF NOT EXISTS dashboard_layout (
    account_id  INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    sekce       TEXT    NOT NULL,
    poradi      INTEGER NOT NULL,
    sirka       TEXT                -- tretina / pul / cela; prazdne = jak to ma sekce v registru
);

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,      -- otisk hesla, nikdy heslo samotne
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login    TEXT
);


-- Nastaveni, ktera se daji menit z webu (na rozdil od .env, ktere se meni rucne).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);


-- Zaznam o probehlych scanech - aby bylo v UI videt, kdy se naposled co delo.
CREATE TABLE IF NOT EXISTS scan_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,     -- 'library' / 'tech'
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    items_total   INTEGER DEFAULT 0,
    items_ok      INTEGER DEFAULT 0,
    items_failed  INTEGER DEFAULT 0,
    status        TEXT,              -- running / done / error
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
