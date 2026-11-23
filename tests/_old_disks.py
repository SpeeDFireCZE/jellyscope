# -*- coding: utf-8 -*-
"""Disks, library folders and the detailed map in the shape the code before step 5 (and 1.8.x) stores them.

Spelled out here - Czech keys of the JSON settings, the stored kinds and sources of the disks' snapshots,
the layers of the map's tiles - so that a key the map of the conversion forgot shows. Used by
tests/test_conversion_values.py; its expectations were measured on that code over this very state.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

GB, TB = 1024 ** 3, 1024 ** 4
FOLDERS = [
    {"knihovna": "Filmy", "cesta": "/media/filmy", "zarizeni": "dev-a", "volne": 500 * GB, "obsazeno": 500 * GB, "typ": "Fixed"},
    {"knihovna": "Serialy", "cesta": "/media/serialy", "zarizeni": "dev-b", "volne": 300 * GB, "obsazeno": 700 * GB, "typ": "Fixed"},
]
CAPACITIES = {"dev-b": {"zdroj": "rucne", "kapacita": 2 * TB}}
VIRTUAL = [{"id": "v1", "nazev": "Cloud", "cesty": ["/media/serialy/cloud"], "kapacita": 1 * TB}]
TILE = {"pevnina": "M0 0L9 9", "jezera": "M1 1", "mesta": "", "reky": "M2 2", "hranice": "M3 3", "kraje": ""}


def _sql(db: Any, statement: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(statement, params)
        conn.commit()


def _setting(db: Any, key: str, value: str) -> None:
    _sql(db, "DELETE FROM settings WHERE key = ?", (key,))
    _sql(db, "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def write_old_settings(db: Any, map_format: str) -> None:
    """The disks' settings, the folders from Jellyfin, the downloaded map and its base layer."""
    _setting(db, "jf_slozky_knihoven", json.dumps(FOLDERS, ensure_ascii=False))
    _setting(db, "jf_slozky_knihoven_cas", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    _setting(db, "disky_prevedeno", "1")
    _setting(db, "disky_kapacita", json.dumps(CAPACITIES))
    _setting(db, "disky_virtualni", json.dumps(VIRTUAL, ensure_ascii=False))
    _setting(db, "disky_ruzne", json.dumps([["dev-a", "dev-b"]]))
    _setting(db, "disky_stejne", json.dumps([]))
    _setting(db, "volne_misto_zdroj", "rucne")
    _setting(db, "mapa_podrobna_kdy", "2026-11-20T10:00:00+00:00")
    _setting(db, "mapa_podrobna_verze", "v5.1.2")
    _setting(db, "mapa_podrobna_format", map_format)
    _setting(db, "mapa_podrobna_dlazdic", "1")
    _setting(db, "mapa_podrobna_bajtu", str(len(json.dumps(TILE))))
    _setting(db, "ui_map_podklad", "osm")
    _sql(db, "DELETE FROM detailed_map_tile")
    _sql(db, "INSERT INTO detailed_map_tile (detail_level, x, y, content) VALUES (0, 0, 0, ?)", (json.dumps(TILE),))


def write_items(db: Any) -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    for item_id, path, size in (("f1", "/media/filmy/a.mkv", 40 * GB), ("s1", "/media/serialy/b.mkv", 60 * GB),
                                ("c1", "/media/serialy/cloud/c.mkv", 100 * GB)):
        _sql(db, "INSERT INTO items (id, name, type, path, size_bytes, is_missing, date_created, synced_at)"
                 " VALUES (?, ?, 'Movie', ?, ?, 0, ?, ?)", (item_id, item_id, path, size, created, created))


def write_old_snapshots(db: Any, days: list[str], prints: dict[str, str]) -> None:
    """Two days of snapshots of every disk in the old words (fyzicky/virtualni, rucne), with the fingerprints the
    code before step 5 computes (`prints`); the free space shrinks between them, so there is a pace."""
    for number, day in enumerate(days):
        shrink = number * 20 * GB
        for disk, name, kind, source, total, free, library, outside in (
                ("dev-a", "Filmy", "fyzicky", "jellyfin", 1000 * GB, 500 * GB - shrink, 40 * GB, 460 * GB + shrink),
                ("dev-b", "Serialy", "fyzicky", "rucne", 2 * TB, 2 * TB - 60 * GB - shrink, 60 * GB + shrink, None),
                ("virt:v1", "Cloud", "virtualni", "rucne", 1 * TB, 1 * TB - 100 * GB - shrink, 100 * GB + shrink, None)):
            _sql(db, "INSERT INTO library_disk_snapshot (day, disk, name, kind, source, total, free, library, outside,"
                     " fingerprint, written_at, kinds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (day, disk, name, kind, source, total, free, library, outside, prints[disk], day + " 03:00:00", "{}"))
    # An old day of Jellyfin 10.x's one row (the disk key "knihovna", the kind "jedno").
    _sql(db, "INSERT INTO library_disk_snapshot (day, disk, name, kind, source, total, free, library, outside,"
             " fingerprint, written_at, kinds) VALUES ('2026-01-01', 'knihovna', 'Library', 'jedno', 'rucne', NULL, NULL,"
             " 100, NULL, 'x', '2026-01-01 03:00:00', '{}')")
