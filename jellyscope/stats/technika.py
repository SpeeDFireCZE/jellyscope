"""Technika knihovny: rozlišení, kodeky, dynamický rozsah.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from typing import Any

from .. import db, formatting


# Prevod vysky obrazu na skupinu rozliseni. Pouziva se na vic mistech,
# takze je to konstanta, ne kopie na peti radcich.
RESOLUTION_CASE = """
    CASE
        WHEN height IS NULL AND width IS NULL      THEN 'nezname'
        WHEN COALESCE(width, 0)  >= 3400
          OR COALESCE(height, 0) >= 2000           THEN '4K'
        WHEN COALESCE(width, 0)  >= 1800
          OR COALESCE(height, 0) >= 1000           THEN '1080p'
        WHEN COALESCE(width, 0)  >= 1200
          OR COALESCE(height, 0) >= 700            THEN '720p'
        WHEN COALESCE(width, 0)  >= 900
          OR COALESCE(height, 0) >= 500            THEN '576p'
        ELSE 'SD'
    END
"""

# Poradi, v jakem se skupiny rozliseni maji zobrazovat (od nejvyssiho).
RESOLUTION_ORDER = ["4K", "1080p", "720p", "576p", "SD", "nezname"]


# ---------------------------------------------------------------------------
# Knihovna (technicka cast - to, co umi MediaLyze)
# ---------------------------------------------------------------------------

def tech_coverage() -> dict[str, Any]:
    """Kolik polozek ma technicka data a odkud pochazeji."""
    return db.query_one(
        """
        SELECT COUNT(*) AS total,
               -- Velikost vsech knihoven dohromady. COALESCE proto, ze
               -- u polozky bez technickych dat je NULL - a NULL by celym
               -- souctem propadl az na prazdno.
               SUM(COALESCE(size_bytes, 0)) AS size_bytes,
               SUM(CASE WHEN tech_source = 'ffprobe'  THEN 1 ELSE 0 END) AS from_ffprobe,
               SUM(CASE WHEN tech_source = 'jellyfin' THEN 1 ELSE 0 END) AS from_jellyfin,
               SUM(CASE WHEN tech_source IS NULL      THEN 1 ELSE 0 END) AS missing,
               SUM(CASE WHEN tech_error IS NOT NULL   THEN 1 ELSE 0 END) AS errors,
               -- Kolik polozek do souctu prispiva nulou. Bez tohohle cisla
               -- vypada "Velikost celkem" jako cela pravda i ve chvili, kdy
               -- se cast knihovny prave ceka na analyzu - typicky po vymene
               -- souboru, kdy se technicka data schvalne zahodila (viz
               -- scanner.slouc_archiv_do_zivych).
               SUM(CASE WHEN size_bytes IS NULL OR size_bytes = 0
                        THEN 1 ELSE 0 END)            AS bez_velikosti
        FROM items
        WHERE is_missing = 0
        """
    ) or {}


def codec_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0 AND video_codec IS NOT NULL"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    return db.query_all(
        f"""
        SELECT UPPER(video_codec)               AS label,
               COUNT(*)                         AS item_count,
               COALESCE(SUM(size_bytes), 0)     AS size_bytes
        FROM items {where}
        GROUP BY UPPER(video_codec)
        ORDER BY item_count DESC
        """,
        tuple(params),
    )


def resolution_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    rows = db.query_all(
        f"""
        SELECT {RESOLUTION_CASE} AS label,
               COUNT(*) AS item_count,
               COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM items {where}
        GROUP BY label
        """,
        tuple(params),
    )
    # Seradime podle sve vlastni logiky (4K, 1080p, ...), ne abecedne.
    order = {label: index for index, label in enumerate(RESOLUTION_ORDER)}
    rows.sort(key=lambda row: order.get(row["label"], 99))
    return rows


# Dynamicky rozsah tak, jak ho ukazujeme.
#
# Dolby Vision je POZITIVNI NALEZ: kdyz ho ohlasi kterykoli ze zdroju,
# plati. Zadny z nich ho totiz nehlasi omylem - zato ho oba umi minout.
# Konkretne ffprobe pred verzi 5 neumi cist DV z Matrosky, takze o filmu,
# ktery Jellyfin popisuje jako "Dolby Vision Profile 8.1", rekne jen
# "HDR" - a treti sloupec prehledu by zustal prazdny, prestoze knihovna
# DV obsahuje.
#
# Jinak plati zmereny udaj; kdyz chybi, bere se hlaseny.
ROZSAH_CASE = """
    CASE WHEN video_range = 'DOVI' OR video_range_reported = 'DOVI' THEN 'DOVI'
         ELSE COALESCE(video_range, video_range_reported) END
"""


def rozsah_polozky(polozka: dict[str, Any] | None) -> str | None:
    """Totez pro jednu polozku - pro detail, kde neni SQL po ruce."""
    if not polozka:
        return None
    zmereny = (polozka.get("video_range") or "").strip().upper()
    hlaseny = (polozka.get("video_range_reported") or "").strip().upper()
    if "DOVI" in (zmereny, hlaseny):
        return "DOVI"
    return zmereny or hlaseny or None


def video_range_breakdown() -> list[dict[str, Any]]:
    radky = db.query_all(
        f"""
        SELECT {ROZSAH_CASE} AS rozsah,
               COUNT(*) AS item_count
        FROM items
        WHERE is_missing = 0 AND tech_source IS NOT NULL
        GROUP BY rozsah
        ORDER BY item_count DESC
        """
    )
    # Vic zapisu muze skoncit pod jednim popiskem (prazdny retezec i NULL
    # jsou obojí "neznamé"), takze se secte, co k sobe patri - jinak by
    # graf mel dva stejne pojmenovane sloupce.
    souhrn: dict[str, int] = {}
    for radek in radky:
        popis = formatting.video_range_human(radek["rozsah"])
        souhrn[popis] = souhrn.get(popis, 0) + int(radek["item_count"] or 0)
    return [{"label": popis, "item_count": pocet}
            for popis, pocet in sorted(souhrn.items(), key=lambda d: -d[1])]
