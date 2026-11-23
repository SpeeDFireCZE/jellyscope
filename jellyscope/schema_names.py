"""The permanent map of the database's old (Czech) names to the new (English) ones.

The database used Czech names for tables, columns, setting keys and stored values until 1.9.0.
`jellyscope.conversion` renames an old database by this map at start - a database of 1.8.x, a
development database, or an old backup restored a year from now - so the map stays here for
good. Until 1.9.0 is released it may change freely (nothing released converts by it); after
that, entries are only added.

* `TABLES` - a table's new name, its columns and indexes (a table whose name stays has
  `old == new` and lists only the columns or indexes that change).
* `SETTING_KEYS` - a setting's old key → the new one.
* `VALUES` - stored values: (table, column) as named after the renames → {old value: new}.

The steps of the spec docs/superpowers/specs/2026-11-21-database-english-design.md fill it group
by group; `DONE` lists the groups whose old names must be gone from the code (a guard in
tests/test_english_code.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableRename:
    """One table: its old and new name, the columns and indexes that change."""

    old: str
    new: str
    columns: dict[str, str] = field(default_factory=dict)
    indexes: dict[str, str] = field(default_factory=dict)


# Music (step 1, 21. 11. 2026). The columns are named alike in the three tables where they are.
_MUSIC_COLUMNS = {
    "nazev": "name", "zanry": "genres", "obrazek_tag": "image_tag", "obal_tag": "cover_tag",
    "interpret_id": "artist_id", "interpret": "artist", "interpreti": "artists", "rok": "year",
    "cislo": "track_number", "delka_ticks": "runtime_ticks", "kontejner": "container", "kodek": "codec",
    "vzorkovani": "sample_rate", "hloubka": "bit_depth", "kanaly": "channels", "velikost": "size_bytes",
    "cesta": "path", "pridano": "added_at", "knihovna_id": "library_id", "synchronizovano": "synced_at",
    "chybi": "missing",
}

# The music cards (cards_music.py): "hudba_<Czech name>" -> "music_<the template's English name>".
_MUSIC_CARDS = {
    "alba": "albums", "aplikace": "clients", "cisla": "numbers", "formaty": "formats", "interpreti": "artists",
    "kdy": "when", "knihovna": "library", "kvalita": "quality", "nejvetsi": "largest", "neposlouchane": "unplayed",
    "nove": "new", "po_dnech": "by_day", "posluchaci": "listeners", "roky": "years", "sit": "network",
    "skladby": "tracks", "zanry": "genres", "zanry_knihovny": "library_genres", "zpusob": "method",
}

# Sonarr and Radarr (step 2): their tables have the same shapes (Radarr's events have no episode, its
# titles no TVDB nor Jellyfin id - a column missing from a table is skipped).
_ARR_EVENT_COLUMNS = {
    "typ": "event_type", "datum": "happened_at", "titul": "title", "epizoda": "episode", "polozka": "item_id",
    "titul_id": "title_id", "kvalita": "quality", "rozliseni": "resolution", "velikost": "size_bytes", "klient": "client",
    "protokol": "protocol", "zdroj": "source", "duvod": "reason", "vydano": "released_at", "cf_skore": "cf_score",
}
_ARR_TITLE_COLUMNS = {"titul_id": "title_id", "nazev": "name", "pridano": "added_at", "synchronizovano": "synced_at"}

# The cards of Sonarr and Radarr (cards_sonarr.py, cards_radarr.py): "arr_<app>_<Czech name>" -> "arr_<app>_<name>".
_ARR_CARDS = {
    "zive_cisla": "live_numbers", "stahuje_se": "downloading", "fronta": "queue", "kalendar": "calendar", "zdravi": "health",
    "cisla": "numbers", "po_dnech": "by_day", "kdy": "when", "nejlepsi_dny": "best_days", "nejvetsi": "largest",
    "stahovani": "downloads", "po_vydani": "after_release", "indexery": "indexers", "skupiny": "groups", "klienti": "clients",
    "protokoly": "protocols", "kvality": "qualities", "rozliseni": "resolutions", "upgrady": "upgrades", "jak_vse": "how_all",
    "jak_upgrady": "how_upgrades", "prechody": "transitions", "upgradovane": "upgraded",
}

# Bazarr (step 2).
_BAZARR_COLUMNS = {
    "otisk": "fingerprint", "druh": "kind", "titul": "title", "epizoda": "episode", "titul_id": "title_id",
    "polozka": "item_id", "akce": "action", "jazyk": "language", "vynucene": "forced", "poskytovatel": "provider",
    "skore": "score", "datum": "happened_at", "datum_presne": "time_exact", "synchronizovano": "synced_at",
}
_BAZARR_CARDS = {
    "zive_cisla": "live_numbers", "chybi_jazyky": "missing_languages", "poskytovatele_ted": "providers_now",
    "zdravi": "health", "cisla": "numbers", "po_dnech": "by_day", "kdy": "when", "jak": "how", "jazyky": "languages",
    "poskytovatele": "providers", "skore": "score", "upgrady": "upgrades", "menene": "changed", "rychlost": "speed",
}

# Prowlarr (step 2): the hourly sums, the grabbed releases, the names of the indexers.
_PROWLARR_CARDS = {
    "zive_cisla": "live_numbers", "indexery_ted": "indexers_now", "zdravi": "health", "cisla": "numbers",
    "po_dnech": "by_day", "kdy": "when", "kdo": "who", "indexery": "indexers", "odezva": "response_time",
    "selhani": "failures", "vyzadana_cisla": "grabbed_numbers", "vyzadana_indexery": "grabbed_indexers",
    "vyzadana_posledni": "grabbed_latest",
}

# Seerr (step 3): the requests and the cards. "seerr_kdo" is a card key and also a permission area (who requested);
# the card is renamed here, the area waits for the step of the areas (5).
_SEERR_CARDS = {
    "cekajici": "pending", "posledni": "latest", "cisla": "numbers", "po_dnech": "by_day", "kdy": "when",
    "stavy": "states", "kdo": "who", "rychlost": "speed", "divani": "watching", "nikdo": "nobody",
}

# Step 4: the columns of the playback that the bin of forgotten viewers has too.
_PLAYBACK_COLUMNS = {"transcode_fps_soucet": "transcode_fps_sum", "transcode_fps_vzorku": "transcode_fps_samples"}

TABLES: tuple[TableRename, ...] = (
    TableRename("hudba_interpret", "music_artist", columns=_MUSIC_COLUMNS),
    TableRename("hudba_album", "music_album", columns=_MUSIC_COLUMNS,
                indexes={"idx_hudba_album_interpret": "idx_music_album_artist"}),
    TableRename("hudba_skladba", "music_track", columns=_MUSIC_COLUMNS,
                indexes={"idx_hudba_skladba_album": "idx_music_track_album",
                         "idx_hudba_skladba_interpret": "idx_music_track_artist",
                         "idx_hudba_skladba_knihovna": "idx_music_track_library",
                         "idx_hudba_skladba_chybi": "idx_music_track_missing"}),
    *(TableRename(f"{app}_udalost", f"{app}_event", columns=_ARR_EVENT_COLUMNS,
                  indexes={f"idx_{app}_udalost_typ": f"idx_{app}_event_type", f"idx_{app}_udalost_download": f"idx_{app}_event_download",
                           f"idx_{app}_udalost_polozka": f"idx_{app}_event_item"})
      for app in ("sonarr", "radarr")),
    *(TableRename(f"{app}_titul", f"{app}_title", columns=_ARR_TITLE_COLUMNS) for app in ("sonarr", "radarr")),
    TableRename("bazarr_udalost", "bazarr_event", columns=_BAZARR_COLUMNS,
                indexes={"idx_bazarr_udalost_datum": "idx_bazarr_event_time", "idx_bazarr_udalost_polozka": "idx_bazarr_event_item",
                         "idx_bazarr_udalost_nepresne": "idx_bazarr_event_inexact"}),
    TableRename("prowlarr_hodina", "prowlarr_hour",
                columns={"hodina": "hour", "aplikace": "app", "druh": "kind", "pocet": "event_count", "selhalo": "failed_count",
                         "odezva_soucet": "response_sum", "odezva_pocet": "response_count", "s_vysledkem": "with_results"}),
    TableRename("prowlarr_vyzadane", "prowlarr_grab",
                columns={"datum": "happened_at", "aplikace": "app", "titul": "title", "zpusob": "method", "uspesne": "succeeded",
                         "odezva": "response", "klient": "client"},
                indexes={"idx_prowlarr_vyzadane_datum": "idx_prowlarr_grab_time"}),
    TableRename("seerr_zadost", "seerr_request",
                columns={"typ": "media_type", "titul": "title", "serii": "seasons", "je_4k": "is_4k", "automaticka": "auto_request",
                         "stav": "status", "stav_titulu": "media_status", "zadal_seerr_id": "requester_seerr_id",
                         "zadal_jmeno": "requester_name", "zadal_jellyfin_id": "requester_jellyfin_id",
                         "vyridil_jmeno": "handled_by_name", "vytvoreno": "created_at", "upraveno": "updated_at",
                         "dostupne": "available_at", "videno_cekat": "seen_pending", "schvaleno_v": "approved_at",
                         "zamitnuto_v": "declined_at", "smazano_v": "deleted_at", "synchronizovano": "synced_at",
                         "plakat": "poster"},
                indexes={"idx_seerr_zadost_vytvoreno": "idx_seerr_request_created", "idx_seerr_zadost_zadal": "idx_seerr_request_requester",
                         "idx_seerr_zadost_tmdb": "idx_seerr_request_tmdb"}),
    TableRename("prowlarr_indexer", "prowlarr_indexer",
                columns={"nazev": "name", "protokol": "protocol", "soukromi": "privacy", "zapnuty": "enabled", "odebran": "removed",
                         "videno": "seen_at"}),
    # Step 4 (22. 11. 2026): the core - layouts of dashboards, accounts, API keys, the detailed map.
    TableRename("dashboard_layout", "dashboard_layout", columns={"sekce": "card_key", "poradi": "position", "sirka": "width"}),
    TableRename("accounts", "accounts",
                columns={"pristup": "permissions", "smi_klic": "may_have_key", "udalosti_pohyb": "events_motion"}),
    TableRename("api_tokens", "api_tokens", columns={"ukazka": "preview", "prava": "permissions", "stav": "status"}),
    TableRename("mapa_dlazdice", "detailed_map_tile", columns={"podrobnost": "detail_level", "obsah": "content"}),
    # The daily snapshots of the library and of its disks. The disk's fingerprint is computed from its kind and
    # source (values, step 5), not from the column names - the forecast goes on over the converted days.
    TableRename("library_snapshot", "library_snapshot",
                columns={"den": "day", "polozek": "item_count", "filmu": "movie_count", "epizod": "episode_count",
                         "velikost": "size_bytes", "bez_technik": "without_tech", "volne_misto": "free_space",
                         "zapsano_v": "written_at"}),
    TableRename("library_disk_snapshot", "library_disk_snapshot",
                columns={"den": "day", "nazev": "name", "druh": "kind", "zdroj": "source", "celkem": "total", "volne": "free",
                         "knihovna": "library", "mimo": "outside", "otisk": "fingerprint", "zapsano_v": "written_at"}),
    # The library's items, their versions (files), the playback, the bin of forgotten viewers and the forgettings.
    # The bin has the playback's columns (it is made from its shape) plus the forgetting it belongs to.
    TableRename("items", "items", columns={"hlavni_id": "main_id"}, indexes={"idx_items_hlavni": "idx_items_main"}),
    TableRename("item_versions", "item_versions", columns={"poradi": "position", "nazev": "name"}),
    TableRename("playback", "playback", columns=_PLAYBACK_COLUMNS,
                indexes={"idx_playback_item_cas": "idx_playback_item_time", "idx_playback_cas_item": "idx_playback_time_item"}),
    TableRename("playback_kos", "playback_bin", columns={**_PLAYBACK_COLUMNS, "udalost": "event_id"},
                indexes={"idx_kos_udalost": "idx_playback_bin_event"}),
    TableRename("zapomenuti", "forgettings",
                columns={"jmeno": "name", "relaci": "session_count", "zapomenuto_v": "forgotten_at", "zaloha": "backup",
                         "stav": "status"}),
)

# Step 5 (22. 11. 2026): the permission areas and the groups (access.AREAS, access.VIEWER/READER). An area's
# key is stored in the groups' settings, in the JSON of an account's own permissions and of a root API key's.
_AREAS = {"dashboard": "dashboard", "prehled": "overview", "srovnani": "comparison", "prave_hraje": "now_playing",
          "zebricky": "charts", "jazyky": "languages", "hudba": "music", "hudba_kdo": "music_who", "arr": "arr",
          "seerr": "seerr", "seerr_kdo": "seerr_who", "knihovna": "library", "knihovna_kdo": "library_who",
          "sit": "network", "uzivatele": "users"}
_GROUPS = {"divak": "viewer", "ctenar": "reader"}
# The cards of the core (cards.ALL_CARDS) and the widths of cards in the layouts of dashboards.
_CORE_CARDS = {"prave_se_hraje": "now_playing", "nedavno_pridane": "recently_added", "souhrn": "summary", "dlazdice": "tiles", "sledovanost_po_dnech": "watching_by_day", "nejsledovanejsi": "most_watched", "nejaktivnejsi_uzivatele": "most_active_users", "doruceni": "delivery", "prehravace": "players", "kdy_se_sleduje": "when_watched", "zivy_tok": "live_traffic", "spicky_po_dnech": "peaks_by_day", "kdo_streamoval": "who_streamed", "podle_prehravace": "by_player", "odkud": "origin", "pomer_jazyku": "language_share", "dabing": "dubbing", "titulky": "subtitles", "jazyky_knihovny": "library_languages", "knihovna_celkem": "library_total", "disky": "disks", "rust_knihovny": "library_growth", "kodeky": "codecs", "rozliseni": "resolutions", "dynamicky_rozsah": "dynamic_range", "zj_nevyuzite": "insight_unused", "zj_transcodovane": "insight_transcoded", "zj_duvody": "insight_reasons", "zj_rychlost": "insight_speed", "zj_kvalita": "insight_quality", "zj_velke": "insight_large", "zj_knihovny": "insight_libraries", "zj_duplicity": "insight_duplicates", "zj_odlozene": "insight_postponed", "kdo_jazyk": "who_language", "kombinace": "combinations", "chybi_stopa": "missing_track", "uzivatele_graf": "users_chart", "uzivatele_tabulka": "users_table", "knihovny": "libraries", "sit_cisla": "network_numbers", "mapa": "map"}
_WIDTHS = {"tretina": "third", "pul": "half", "cela": "full"}
# The settings holding the state of a service's last sync (JSON, services.write_state).
_STATE_KEYS = ("arr_sonarr_state", "arr_radarr_state", "arr_bazarr_state", "arr_prowlarr_state", "seerr_state", "music_state")
# The notifications (notifications.py): the events, the messages a change of state sends, the parts of an own text.
_EVENTS = {"sberac": "collector", "misto": "space", "souhrn": "summary"}
_MESSAGES = {"sberac_porucha": "collector_down", "sberac_obnova": "collector_back", "misto_porucha": "space_low",
             "misto_obnova": "space_ok", "souhrn": "summary"}

SETTING_KEYS: dict[str, str] = {
    # Step 2: the switch of a connected service (services.py builds `<prefix>_enabled` for the *Arr apps and Seerr).
    **{f"{prefix}_zapnuto": f"{prefix}_enabled"
       for prefix in ("arr_sonarr", "arr_radarr", "arr_bazarr", "arr_prowlarr", "seerr")},
    "task_hudba_enabled": "task_music_enabled", "task_hudba_last_auto": "task_music_last_auto",
    "hudba_sync_minutes": "music_sync_minutes", "hudba_stav": "music_state", "hudba_cely_pruchod": "music_full_pass",
    "hudba_zmizele_knihovny": "music_vanished_libraries", "ui_max_rows_hudba": "ui_max_rows_music",
    "ui_max_rows_hudba_mobile": "ui_max_rows_music_mobile",
    # Sonarr and Radarr: the sync's state and mark, the mark of the title list and its version.
    **{f"arr_{app}_{old}": f"arr_{app}_{new}" for app in ("sonarr", "radarr")
       for old, new in (("stav", "state"), ("synchronizace", "last_sync"), ("tituly", "titles"), ("tituly_verze", "titles_version"))},
    "arr_bazarr_stav": "arr_bazarr_state", "arr_bazarr_synchronizace": "arr_bazarr_last_sync",
    # Prowlarr: the state, the time and the number of the newest counted event and how many are counted. They are
    # renamed in the conversion's transaction with the sums they describe - nothing is counted twice.
    "arr_prowlarr_stav": "arr_prowlarr_state", "arr_prowlarr_synchronizace": "arr_prowlarr_last_event_at",
    "arr_prowlarr_posledni_id": "arr_prowlarr_last_id", "arr_prowlarr_pocet": "arr_prowlarr_counted",
    # Seerr (step 3).
    "seerr_stav": "seerr_state", "seerr_synchronizace": "seerr_last_sync", "seerr_obrazky": "seerr_images",
    "seerr_plakaty_znovu": "seerr_posters_retry",
    # Step 5: what a group sees, whether names are hidden from it, whether it may make own API keys.
    **{f"{who}_vidi_{area}": f"{new_who}_sees_{new_area}" for who, new_who in _GROUPS.items() for area, new_area in _AREAS.items()},
    **{f"{who}_anonymizace": f"{new_who}_anonymise" for who, new_who in _GROUPS.items()},
    **{f"{who}_smi_klic": f"{new_who}_may_have_key" for who, new_who in _GROUPS.items()},
    # Step 5: the disks, the library folders from Jellyfin, the detailed map and its base layer.
    "jf_slozky_knihoven": "jf_library_folders", "jf_slozky_knihoven_cas": "jf_library_folders_at",
    "disky_kapacita": "disks_capacity", "disky_virtualni": "disks_virtual", "disky_prevedeno": "disks_converted",
    "disky_stejne": "disks_same", "disky_ruzne": "disks_different", "volne_misto_zdroj": "free_space_source",
    **{f"mapa_podrobna_{old}": f"detailed_map_{new}"
       for old, new in (("kdy", "at"), ("verze", "version"), ("format", "format"), ("dlazdic", "tiles"), ("bajtu", "bytes"))},
    "ui_map_podklad": "ui_map_base",
    # Step 5: the notifications - the SMTP fields (the password stays a secret: db.SECRET_SETTINGS), the events,
    # their last states, the own texts of the messages, the times; the task of notifications.
    **{f"notify_smtp_{old}": f"notify_smtp_{new}"
       for old, new in (("uzivatel", "user"), ("heslo", "password"), ("odesilatel", "sender"), ("komu", "recipient"))},
    **{f"notify_event_{old}": f"notify_event_{new}" for old, new in _EVENTS.items()},
    **{f"notify_state_{old}": f"notify_state_{new}" for old, new in _EVENTS.items() if old != "souhrn"},
    **{f"notify_text_{old}_{part}": f"notify_text_{new}_{new_part}"
       for old, new in _MESSAGES.items() for part, new_part in (("predmet", "subject"), ("text", "body"))},
    "notify_ticho_minut": "notify_quiet_minutes", "notify_misto_dnu": "notify_space_days",
    "notify_sberac_chyba_od": "notify_collector_error_since", "notify_souhrn_den": "notify_summary_day",
    "notify_souhrn_cas": "notify_summary_time", "notify_souhrn_odeslan": "notify_summary_sent",
    "task_notifikace_enabled": "task_notifications_enabled", "task_notifikace_last_auto": "task_notifications_last_auto",
    "notifikace_minuty": "notifications_minutes",
    # Step 5: the log rotation, the look of the pages, the dashboard's name, the developer's date of a holiday.
    "logrotate_dni": "logrotate_days", "logrotate_pocet": "logrotate_count", "_logrotate_naposledy": "_logrotate_last",
    "ui_cas_presne": "ui_exact_time", "ui_udalosti": "ui_events", "dashboard_nazev": "dashboard_name",
    "vyvoj_udalost_datum": "dev_event_date",
}

VALUES: dict[tuple[str, str], dict[str, str]] = {
    ("scan_log", "kind"): {"hudba": "music", "notifikace": "notifications", "mapa": "map"},
    ("forgettings", "status"): {"kos": "bin", "zaloha": "backup"},
    # Where a stream's language comes from (scanner/stream_languages.py; step 6 found it): the file name, nothing found.
    ("item_streams", "language_source"): {"nazev": "name", "neznamy": "unknown"},
    # The kind of a Bazarr event. The fingerprint (the primary key) was computed from the old word and goes on
    # being so (arr.bazarr.sync.FINGERPRINT_KIND) - otherwise every event would be stored again by the next sync.
    ("bazarr_event", "kind"): {"dil": "episode", "film": "movie"},
    ("prowlarr_hour", "kind"): {"dotaz": "query"},                  # rss and auth stay
    ("prowlarr_grab", "method"): {"presmerovani": "redirect"},      # proxy stays
    ("api_tokens", "status"): {"aktivni": "active", "vypnuty": "disabled"},
    # The disks' snapshots. Their fingerprints were computed from these words and still are (scanner.disks).
    ("library_disk_snapshot", "kind"): {"fyzicky": "physical", "virtualni": "virtual", "jedno": "single"},
    ("library_disk_snapshot", "source"): {"rucne": "manual"},                     # jellyfin and disk stay
    ("library_disk_snapshot", "disk"): {"knihovna": "library"},                   # Jellyfin 10.x's one disk
    # dashboard_layout.sekce is renamed by the step of the core tables (step 4); its key here follows then.
    ("dashboard_layout", "width"): _WIDTHS,
    ("dashboard_layout", "card_key"): {
        **_CORE_CARDS,
        **{f"hudba_{old}": f"music_{new}" for old, new in _MUSIC_CARDS.items()},
        **{f"arr_{app}_{old}": f"arr_{app}_{new}" for app in ("sonarr", "radarr") for old, new in _ARR_CARDS.items()},
        **{f"bazarr_{old}": f"bazarr_{new}" for old, new in _BAZARR_CARDS.items()},
        **{f"prowlarr_{old}": f"prowlarr_{new}" for old, new in _PROWLARR_CARDS.items()},
        **{f"seerr_{old}": f"seerr_{new}" for old, new in _SEERR_CARDS.items()},
    },
}

# JSON inside a cell (step 5): the keys of its objects at any depth, renamed by JSON_KEYS[target], and the
# values under a key (a string, or the strings of a list) by JSON_VALUES[(target, the key's new name)]. A
# target is (table, column) or ("settings", key) - by their new names. What is no JSON stays; an object that
# has the old and the new key already stays untouched (no value is lost).
JSON_KEYS: dict[tuple[str, str], dict[str, str]] = {
    # An account's own permissions (access.pack_permissions) and a root API key's (api.pack_key_permissions).
    ("accounts", "permissions"): {"oblasti": "areas", "anonymizace": "anonymise"},
    ("api_tokens", "permissions"): {"oblasti": "areas", "anonymizace": "anonymise", "historie": "history"},
    # The library folders from Jellyfin, the disks' capacities and the virtual disks (scanner.disks).
    ("settings", "jf_library_folders"): {"knihovna": "library", "cesta": "path", "zarizeni": "device", "volne": "free",
                                         "obsazeno": "used", "typ": "type"},
    ("settings", "disks_capacity"): {"zdroj": "source", "kapacita": "capacity"},
    ("settings", "disks_virtual"): {"nazev": "name", "cesty": "paths", "cesta": "path", "kapacita": "capacity"},
    # The state of the last sync of a service (services.write_state): *Arr, Seerr, music.
    **{("settings", key): {"kdy": "at", "vysledek": "result", "novych": "new_count", "chyba": "error", "sablona": "template",
                           "hodnoty": "values"} for key in _STATE_KEYS},
    # The layers of a tile of the detailed map (converted, not downloaded again).
    ("detailed_map_tile", "content"): {"pevnina": "land", "jezera": "lakes", "mesta": "cities", "reky": "rivers",
                                       "hranice": "borders", "kraje": "regions"},
}
JSON_VALUES: dict[tuple[tuple[str, str], str], dict[str, str]] = {
    (("accounts", "permissions"), "areas"): {old: new for old, new in _AREAS.items() if old != new},
    (("api_tokens", "permissions"), "areas"): {old: new for old, new in _AREAS.items() if old != new},
    (("settings", "disks_capacity"), "source"): {"rucne": "manual"},
    **{(("settings", key), "result"): {"chyba": "error"} for key in _STATE_KEYS},
}

# The plain value of one setting (no JSON), by the setting's new key: only that setting's value is mapped.
SETTING_VALUES: dict[str, dict[str, str]] = {
    "free_space_source": {"rucne": "manual"},
    "notify_state_collector": {"spatne": "bad"},
    "notify_state_space": {"spatne": "bad"},
    "ui_skin": {"novy": "new", "klasicky": "classic"},
    "ui_map_base": {"kresleny": "drawn"},
}

# Tables read by nothing any more, dropped by the conversion (their rows are in the backup made before it).
ORPHANED_TABLES: tuple[str, ...] = ("api_pouziti",)

# The one old name a database of 1.9.0 keeps on purpose: a view named like the bin of 1.8.x. A start of 1.8.x
# adds columns to its bin and an index on it - on a view both end with an error, on SQLite and PostgreSQL, so
# 1.8.x does not run on a converted (or new) database, where it would quietly open what the new names guard
# (a restricted or disabled API key, a reader's rights). 1.9.0 never reads it; backups do not carry it
# (the own export saves tables), a restore drops it and init_db makes it again (db._lock_against_1_8).
LOCK_VIEW = "playback_kos"

DONE: tuple[str, ...] = ("music", "sonarr", "radarr", "bazarr", "prowlarr", "seerr",
                         "dashboard_layout", "accounts", "api_tokens", "detailed_map_tile",
                         "library_snapshot", "library_disk_snapshot", "items", "item_versions", "playback",
                         "forgettings", "_sees_", "_anonymise", "_may_have_key", "music_who", "seerr_who", "library_who",
                         "now_playing", "insight_", "recently_added", "watching_by_day", "most_watched",
                         "most_active_users", "when_watched", "live_traffic", "peaks_by_day", "who_streamed", "by_player",
                         "language_share", "library_languages", "library_total", "library_growth", "dynamic_range",
                         "who_language", "missing_track", "users_chart", "users_table", "network_numbers",
                         "disks_", "jf_library_folders", "free_space_source", "detailed_map_", "ui_map_base",
                         "notify_", "task_notifications", "notifications_minutes", "logrotate_", "ui_exact_time",
                         "ui_events", "dashboard_name", "dev_event_date")
