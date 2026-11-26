# -*- coding: utf-8 -*-
r"""Converting the database (Settings → Database): everything is carried over.

The list of tables to convert was written by hand and nobody added the new
tables to it - the conversion SQLite -> PostgreSQL quietly left out the API
keys, the dashboards, the file versions, the library's daily snapshots, the
address blocks and the forgotten viewers. Guarded here:

* the conversion knows every table of the schema, in an order they can be filled in;
* a real conversion carries the rows of all tables over (the bin of the forgotten too);
* after converting to PostgreSQL new rows can be created (the id counters moved).

Without PostgreSQL it converts into a second SQLite; through
.superpowers/tools/test_on_pg.py into PostgreSQL, that is the direction that matters.

Run:
    .venv/bin/python tests/test_database_conversion.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cil.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import api, db, dbmigrate, dialect  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- the list of tables ---")
for name in ("schema.sql", "schema_postgres.sql"):
    text = (PROJECT / "jellyscope" / name).read_text(encoding="utf-8")
    in_schema = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", text))
    check(set(dbmigrate.TABLES) == in_schema,
          f"{name}: the conversion knows all tables (missing {sorted(in_schema - set(dbmigrate.TABLES))})")
schema = (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8")
wrong_order = []
for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, re.S):
    for target in re.findall(r"REFERENCES (\w+)", body):
        if dbmigrate.TABLES.index(target) > dbmigrate.TABLES.index(table):
            wrong_order.append(f"{table} -> {target}")
check(not wrong_order, f"a table with a foreign key comes after the one it refers to ({wrong_order})")

print("--- a real conversion ---")
source = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(Path(_tmp) / "zdroj.db"))
db.init_db(source)
T = "2026-10-20 10:00:00"
with db.connect(source) as conn:
    for sql, values in (
        ("INSERT INTO settings (key, value) VALUES (?, ?)", ("prevod_test", "1")),
        ("INSERT INTO accounts (id, username, password_hash, is_admin, created_at)"
         " VALUES (7, 'admin', 'x', 1, ?)", (T,)),
        ("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')", ()),
        # Every Jellyfin library is stored (16. 11. 2026), with its count of runs it was missing from.
        ("INSERT INTO libraries (id, name, collection_type, missing_runs) VALUES ('lib-m', 'Hudba', 'music', 1)", ()),
        ("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES ('u1', 'Jana', 0, 0)", ()),
        ("INSERT INTO items (id, name, type, library_id, tech_source, is_missing)"
         " VALUES ('i1', 'Duna', 'Movie', 'lib1', 'jellyfin', 0)", ()),
        ("INSERT INTO item_versions (item_id, source_id, position) VALUES ('i1', 's1', 0)", ()),
        ("INSERT INTO item_streams (item_id, stream_index, type) VALUES ('i1', 0, 'Video')", ()),
        ("INSERT INTO playback (id, session_key, user_id, item_id, started_at, last_seen_at)"
         " VALUES (40, 's1', 'u1', 'i1', ?, ?)", (T, T)),
        ("INSERT INTO scan_log (id, kind, started_at) VALUES (9, 'library', ?)", (T,)),
        ("INSERT INTO dashboard_layout (account_id, card_key, position) VALUES (7, 'mapa', 0)", ()),
        ("INSERT INTO library_snapshot (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr,"
         " without_tech, written_at, kinds) VALUES ('2026-10-20', 1, 1, 0, 5, 0, 0, 0, ?,"
         " '{\"music\": {\"items\": 2, \"size\": 3}}')", (T,)),
        ("INSERT INTO api_tokens (id, name, token_hash, preview, created_at, account_id)"
         " VALUES (12, 'Grafana', 'otisk', 'js_abc', ?, 7)", (T,)),
        ("INSERT INTO login_blocks (ip, level) VALUES ('203.0.113.9', 1)", ()),
        # A sign-in session goes along: switching the database does not sign anybody out.
        ("INSERT INTO web_sessions (token_hash, account_id, created_at, last_used_at) VALUES ('hash', 7, ?, ?)", (T, T)),
        ("INSERT INTO forgettings (id, user_id, name, session_count, forgotten_at)"
         " VALUES (3, 'u9', 'Petr', 1, ?)", (T,)),
        ("INSERT INTO detailed_map_tile (detail_level, x, y, content) VALUES (1, 2, 3, '{}')", ()),
        # Sizes over 2 GB: INTEGER in PostgreSQL would not hold them.
        ("INSERT INTO library_disk_snapshot (day, disk, name, kind, source, total, free, library,"
         " outside, written_at, kinds) VALUES ('2026-08-01', 'dev-a', 'Filmy', 'fyzicky', 'jellyfin',"
         " 5000000000000, 120000000000, 3000000000000, 1500000000000, ?, '{\"music\": 2000000000}')", (T,)),
        # Sonarr's and Radarr's history: a release size over 2 GB (BIGINT in PostgreSQL).
        ("INSERT INTO sonarr_event (arr_id, event_type, happened_at, title, episode, item_id, title_id, quality,"
         " resolution, size_bytes, indexer, release_group, client, protocol, source, reason, download_id,"
         " released_at, cf_score) VALUES (7, 'grabbed', ?, 'Seriál', 'S01E02', 56, 1, 'WEBDL-1080p',"
         " 1080, 4294967296, 'indexer', 'NTb', 'SABnzbd', 'usenet', 'Rss', '', 'SAB_1', ?, 0)", (T, T)),
        ("INSERT INTO radarr_event (arr_id, event_type, happened_at, title, item_id, title_id, quality, resolution, size_bytes,"
         " download_id, released_at) VALUES (7, 'downloadFolderImported', ?, 'Film', 3, 3, 'Bluray-2160p', 2160, 64424509440,"
         " 'SAB_2', ?)", (T, T)),
        # A Sonarr series and a Radarr movie (links from the cards into the library).
        ("INSERT INTO sonarr_title (title_id, name, tmdb_id, tvdb_id, imdb_id, jellyfin_id, synced_at)"
         " VALUES (1, 'Seriál', '1399', '121361', 'tt0944947', 'feca7a7c96c254bb6ed6a3c87574ce63', ?)", (T,)),
        ("INSERT INTO radarr_title (title_id, name, tmdb_id, imdb_id, synced_at) VALUES (3, 'Film', '603', 'tt0133093', ?)", (T,)),
        # Lidarr's history (an album's size over 2 GB: BIGINT in PostgreSQL) and an album paired with the music library.
        ("INSERT INTO lidarr_event (arr_id, event_type, happened_at, title, artist, item_id, title_id, artist_id, quality,"
         " size_bytes, indexer, release_group, client, protocol, source, reason, download_id, released_at, cf_score)"
         " VALUES (9, 'grabbed', ?, 'Album', 'Interpret', NULL, 11, 1, 'FLAC', 4294967296, 'indexer', 'GRP', 'SABnzbd',"
         " 'usenet', 'Rss', '', 'SAB_3', ?, 0)", (T, T)),
        ("INSERT INTO lidarr_title (title_id, name, artist, artist_id, mb_release_group, mb_releases, mb_artist, track_count,"
         " released_at, jellyfin_id, jellyfin_artist_id, synced_at) VALUES (11, 'Album', 'Interpret', 1,"
         " 'b1111111-0000-4000-8000-000000000011', 'c1111111-0000-4000-8000-000000000111|c1111111-0000-4000-8000-000000000112',"
         " 'a1111111-1111-4111-8111-111111111111', 3, ?, 'jf-album', 'jf-artist', ?)", (T, T)),
        # Readarr's history (a book's size over 2 GB: BIGINT in PostgreSQL) and a book paired with the books library.
        ("INSERT INTO readarr_event (arr_id, event_type, happened_at, title, author, item_id, title_id, author_id, quality,"
         " size_bytes, indexer, release_group, client, protocol, source, reason, download_id, released_at, cf_score)"
         " VALUES (9, 'bookFileImported', ?, 'Kniha', 'Autor', 1102, 11, 1, 'EPUB', 4294967296, 'indexer', 'GRP', 'SABnzbd main',"
         " 'usenet', 'Rss', '', 'SAB_3', ?, 0)", (T, T)),
        ("INSERT INTO readarr_title (title_id, name, author, author_id, foreign_book_id, isbns, asins, is_ebook, released_at,"
         " added_at, jellyfin_id, synced_at) VALUES (11, 'Kniha', 'Autor', 1, '200011', '9788074322556|9788020417411',"
         " 'B00A000001', 1, ?, ?, 'jf-book', ?)", (T, T, T)),
        # A request from Seerr.
        ("INSERT INTO seerr_request (id, media_type, tmdb_id, tvdb_id, jellyfin_id, title, seasons, is_4k, auto_request, status,"
         " media_status, requester_seerr_id, requester_name, requester_jellyfin_id, handled_by_name, created_at, updated_at, available_at,"
         " seen_pending, approved_at, declined_at, deleted_at, synced_at, poster) VALUES (3, 'tv', '70523', '334824',"
         " 'a427aea771c45a7d71be634b62b36e34', 'Dark', 1, 0, 0, 5, 5, 4, 'jana', '34d3f8d3', 'admin', ?, ?, ?, 1, ?,"
         " NULL, NULL, ?, '/wiiyc3m6HKaQhFBYeuM7ySCNQsG.jpg')", (T, T, T, T, T)),
        # A subtitle event from Bazarr (the score is a decimal number).
        ("INSERT INTO bazarr_event (fingerprint, kind, title, episode, title_id, item_id, action, language, hi, forced, provider,"
         " score, happened_at, time_exact, synced_at) VALUES ('0a1b2c3d4e5f60718293a4b5c6d7e8f901234567', 'episode', 'Dark',"
         " 'S01E01', 3, 146, 1, 'cs', 0, 0, 'gestdown', 86.67, ?, 1, ?)", (T, T)),
        # Music: an artist, an album and a track (the size and the length are big numbers).
        ("INSERT INTO music_artist (id, name, genres, musicbrainz_id, image_tag, synced_at, missing)"
         " VALUES ('5edbf9f21d1332f81110de015a768c16', 'Pixel Harbor', 'Synthwave', '', '', ?, 0)", (T,)),
        ("INSERT INTO music_album (id, name, artist_id, artist, year, genres, musicbrainz_id, cover_tag, added_at, library_id,"
         " synced_at, missing) VALUES ('a1b2', 'Synth Tide', '5edbf9f21d1332f81110de015a768c16', 'Pixel Harbor', 2023,"
         " 'Synthwave', '11111111-1111-4111-8111-111111111111', '0b10a31f', ?, 'knih', ?, 0)", (T, T)),
        ("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, artists, track_number, disk, runtime_ticks, year,"
         " genres, container, codec, bitrate, sample_rate, bit_depth, channels, size_bytes, path, added_at, library_id, synced_at,"
         " missing) VALUES ('s1', 'Neon Pier', 'a1b2', 'Synth Tide', '5edbf9f21d1332f81110de015a768c16', 'Pixel Harbor',"
         " 'Pixel Harbor', 1, 1, 4004571390, 2023, 'Synthwave', 'flac', 'flac', 1411200, 96000, 24, 2, 5368709120,"
         " '/media/music/Pixel Harbor/Synth Tide/01 Neon Pier.flac', ?, 'knih', ?, 0)", (T, T)),
        # Books: an audiobook (the size and the length are big numbers).
        ("INSERT INTO book (id, kind, name, title, author, series, year, genres, format, size_bytes, runtime_ticks, path, isbn,"
         " provider_ids, cover_tag, added_at, library_id, synced_at, missing) VALUES ('b1', 'audiobook', 'Hobit', 'Hobit',"
         " 'J. R. R. Tolkien', 'Stredozem', 1937, 'Fantasy', 'm4b', 5368709120, 396000000000, '/media/audiobooks/Hobit.m4b',"
         " '', '{\"OpenLibrary\": \"OL123M\"}', '', ?, 'knih', ?, 0)", (T, T)),
        # Prowlarr: an hourly sum of queries, a grabbed release, an indexer's name.
        ("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
         " VALUES ('2026-11-15 06:00:00', 1, 'Sonarr', 'query', 12, 2, 3400, 10, 7)", ()),
        ("INSERT INTO prowlarr_grab (id, happened_at, indexer_id, app, title, method, succeeded, response, client)"
         " VALUES (23, ?, 1, 'Radarr', 'Film.2024.1080p.WEB-DL', 'proxy', 1, 41, '')", (T,)),
        ("INSERT INTO prowlarr_indexer (id, name, protocol, privacy, enabled, removed, seen_at)"
         " VALUES (1, 'arr-test', 'usenet', 'private', 1, 0, ?)", (T,)),
        # A sample of the indexers' status: the sample's own row and an indexer left out at that moment.
        ("INSERT INTO prowlarr_sample (sampled_at, indexer_id, enabled_ids, interval_seconds) VALUES (?, 0, ',1,3,', 3600)", (T,)),
        ("INSERT INTO prowlarr_sample (sampled_at, indexer_id, failing_since, skipped_until, last_failure)"
         " VALUES (?, 1, '2026-11-15 06:33:00', '2026-11-15 06:38:34', '2026-11-15 06:37:34')", (T,)),
    ):
        conn.execute(sql, values)
    db._ensure_playback_bin(conn)
    conn.execute(f"INSERT INTO {db.PLAYBACK_BIN} (id, session_key, user_id, item_id, started_at,"
                 " last_seen_at, event_id) VALUES (41, 's2', 'u9', 'i1', ?, ?, 3)", (T, T))
    conn.commit()

target = db.database_config()
print("target:", target.kind)
result = dbmigrate.copy_all(source, target)
check(result.get("status") == "ok", f"the conversion runs ({result.get('message', '')[:120]})")


def row_count(config, table: str) -> int:
    with db.connect(config) as conn:
        if not db.columns_of(conn, table):
            return -1
        return int(dict(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone())["n"])


missing = [t for t in list(dbmigrate.TABLES) + [db.PLAYBACK_BIN]
           if row_count(source, t) != row_count(target, t) or row_count(target, t) < 1]
check(not missing, f"all tables carried over with their rows (not matching: {missing})")
check(db.query_value("SELECT account_id FROM api_tokens WHERE id = 12", default=None) == 7,
      "the API key stayed with its account")
title_row = db.query_one("SELECT name, tvdb_id, jellyfin_id FROM sonarr_title WHERE title_id = 1") or {}
check((title_row.get("name"), title_row.get("tvdb_id"), title_row.get("jellyfin_id")) == ("Seriál", "121361", "feca7a7c96c254bb6ed6a3c87574ce63"),
      f"an *Arr app's title was converted with the series' id in Jellyfin too ({title_row})")
subtitles = db.query_one("SELECT title, language, score, time_exact FROM bazarr_event WHERE kind = 'episode'") or {}
check((subtitles.get("title"), subtitles.get("language"), subtitles.get("time_exact")) == ("Dark", "cs", 1)
      and abs(float(subtitles.get("score") or 0) - 86.67) < 1e-6, f"a subtitle event from Bazarr was converted with its score too ({subtitles})")
hour = db.query_one("SELECT event_count, failed_count, response_sum, response_count, with_results FROM prowlarr_hour"
                    " WHERE hour = '2026-11-15 06:00:00' AND indexer_id = 1 AND app = 'Sonarr' AND kind = 'query'") or {}
release = db.query_one("SELECT title, method, response FROM prowlarr_grab WHERE id = 23") or {}
indexer = db.query_one("SELECT name, protocol, removed FROM prowlarr_indexer WHERE id = 1") or {}
check(tuple(hour.values()) == (12, 2, 3400, 10, 7) and tuple(release.values()) == ("Film.2024.1080p.WEB-DL", "proxy", 41)
      and tuple(indexer.values()) == ("arr-test", "usenet", 0), f"Prowlarr's sums, grabbed release and indexer were converted ({hour}, {release}, {indexer})")
request = db.query_one("SELECT title, tvdb_id, poster, jellyfin_id FROM seerr_request WHERE id = 3") or {}
check((request.get("title"), request.get("tvdb_id"), request.get("poster")) == ("Dark", "334824", "/wiiyc3m6HKaQhFBYeuM7ySCNQsG.jpg")
      and request.get("jellyfin_id") == "a427aea771c45a7d71be634b62b36e34",
      f"a Seerr request was converted with the TVDB id and the poster's path too ({request})")

music_library = db.query_one("SELECT name, collection_type, missing_runs FROM libraries WHERE id = 'lib-m'") or {}
check(tuple(music_library.values()) == ("Hudba", "music", 1), f"a music library was converted with its count of runs without it ({music_library})")

new_key = api.create_token("Po prevodu")
check(new_key["id"] > 12, f"a new API key after the conversion gets a free id ({new_key['id']})")
with db.connect() as conn:
    conn.execute("INSERT INTO forgettings (user_id, name, session_count, forgotten_at)"
                 " VALUES ('u8', 'Ota', 1, ?)", (T,))
    conn.commit()
check(db.query_value("SELECT MAX(id) FROM forgettings") > 3, "the other id counters are moved too")

print("--- a conversion that fails ---")
# An API key of an account that no longer exists (an old SQLite without
# checking foreign keys allowed it). The message must say at which table it
# failed - "FOREIGN KEY constraint failed" alone does not say where to look.
with db.connect(source) as conn:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO api_tokens (id, name, token_hash, preview, created_at, account_id)"
                 " VALUES (13, 'Sirotek', 'otisk2', 'js_def', ?, 999)", (T,))
    conn.commit()
result = dbmigrate.copy_all(source, target)
check(result.get("status") == "error" and result.get("table") == "api_tokens"
      and "api_tokens" in result.get("message", ""),
      f"the error names the table ({result.get('table')}: {result.get('message', '')[:120]})")
check(db.query_value("SELECT COUNT(*) FROM api_tokens WHERE id = 12") == 1,
      "a failed conversion deletes nothing in the target")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
