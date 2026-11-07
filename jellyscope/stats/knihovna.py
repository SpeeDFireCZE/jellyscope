"""Knihovna po titulech: řádky, detail seriálu, dlaždice a aktivita knihovny.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from typing import Any

from .. import db
from ..i18n import translate as _t
from .divaci import DIVAK_JMENO, DIVAK_PORADI, DIVAK_SKUPINA
from .obdobi import _meze
from .prehravani import popis_prepoctu
from .technika import RESOLUTION_CASE, ROZSAH_CASE


# ---------------------------------------------------------------------------
# Knihovna po titulech, ne po dilech
# ---------------------------------------------------------------------------
#
# V tabulce `items` je kazda epizoda samostatny radek - tak je posila
# Jellyfin a tak je potrebujeme pro statistiky. V seznamu knihovny je to
# ale k nicemu: serial o peti radach zabere sto radku a mezi nimi zanikne
# vsechno ostatni.
#
# Seskupujeme proto podle serialu. Klic skupiny je `COALESCE(series_id, id)`,
# takze film (ktery series_id nema) tvori skupinu sam za sebe a stejny dotaz
# obslouzi obojí - zadne dva dotazy, ktere by se casem rozesly.
_SKUPINA = "COALESCE(i.series_id, i.id)"

# Sloupce skupiny. U filmu je skupina jednoprvkova, takze SUM i MAX vraci
# proste jeho vlastni hodnotu - proto nemusime rozlisovat.
_SKUPINA_SLOUPCE = f"""
        {_SKUPINA}                                   AS id,
        MAX(CASE WHEN i.series_id IS NULL THEN 0 ELSE 1 END) AS is_series,
        MAX(COALESCE(i.series_name, i.name))         AS name,
        MAX(i.type)                                  AS type,
        MAX(i.library_id)                            AS library_id,
        COUNT(*)                                     AS episode_count,
        COUNT(DISTINCT i.parent_index_number)        AS season_count,
        COALESCE(SUM(i.size_bytes), 0)               AS size_bytes,
        COALESCE(SUM(i.runtime_ticks), 0)            AS runtime_ticks,
        MAX(i.width)                                 AS width,
        MAX(i.height)                                AS height,
        MAX(i.bitrate)                               AS bitrate,
        MAX(i.video_codec)                           AS video_codec,
        MAX(i.production_year)                       AS production_year,
        MAX(i.date_created)                          AS date_created,
        MAX(i.tech_source)                           AS tech_source,
        -- Skupina je bud cela ziva, nebo cela archivovana - filtr je
        -- ve WHERE. Sablona to potrebuje kvuli znacce "Archivovano".
        MAX(i.is_missing)                            AS is_missing,
        COALESCE(SUM(COALESCE(p.plays, 0)), 0)       AS plays
"""

# Prehrani si spocitame jednim dotazem dopredu a pripojime. Poddotaz na
# kazdy radek by u seskupeni nesel pouzit a u velke knihovny by stejne
# znamenal tisice dotazu navic.
_PREHRANI = """
    LEFT JOIN (SELECT item_id, COUNT(*) AS plays
                 FROM playback WHERE watched_seconds > 60
                GROUP BY item_id) p ON p.item_id = i.id
"""


def _knihovna_filtr(library_id: str | None, search: str | None,
                    archived: bool) -> tuple[list[str], list[Any]]:
    where = ["i.is_missing = 1" if archived else "i.is_missing = 0"]
    params: list[Any] = []
    if library_id:
        where.append("i.library_id = ?")
        params.append(library_id)
    if search:
        where.append("(i.name LIKE ? OR i.series_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    return where, params


def library_rows(limit: int, offset: int, library_id: str | None = None,
                 search: str | None = None, sort: str = "size",
                 archived: bool = False) -> list[dict[str, Any]]:
    """Seznam knihovny: filmy jednotlive, serialy jako jeden radek."""
    where, params = _knihovna_filtr(library_id, search, archived)

    # Razeni se do SQL nesmi vlepit z uzivatelskeho vstupu primo. Pevny
    # seznam moznosti a bereme z nej.
    sort_options = {
        "size": "size_bytes DESC",
        "bitrate": "bitrate DESC",
        "name": "name ASC",
        "plays": "plays DESC",
        "resolution": "height DESC",
    }
    order_by = sort_options.get(sort, sort_options["size"])

    params.extend([limit, offset])
    return db.query_all(
        f"""
        SELECT {_SKUPINA_SLOUPCE}
        FROM items i
        {_PREHRANI}
        WHERE {' AND '.join(where)}
        GROUP BY {_SKUPINA}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def library_rows_count(library_id: str | None = None, search: str | None = None,
                       archived: bool = False) -> int:
    """Kolik radku seznam ma - tedy skupin, ne polozek."""
    where, params = _knihovna_filtr(library_id, search, archived)
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM (
            SELECT {_SKUPINA} AS id FROM items i
             WHERE {' AND '.join(where)}
             GROUP BY {_SKUPINA}
        ) skupiny
        """,
        tuple(params),
    ))


def series_detail(series_id: str) -> dict[str, Any]:
    """Serial rozdeleny na rady a v nich dily.

    Vraci prazdny slovnik, kdyz takovy serial neznáme - routa z toho
    udela 404.

    **Archivovane dily se nepocitaji mezi zive.** Archiv je seznam
    souboru, ktere v Jellyfinu uz nejsou - historie prehravani u nich
    zustava, ale titul sam zmizel. Drive je detail serialu vypisoval
    vedle zivych bez rozdilu, zatimco seznam knihovny je vynechaval;
    serial pak v Jellyfinu ukazoval sto dilu a tady sto dvacet, aniz
    by z toho slo poznat proc.

    Vypisujeme je proto zvlast a rekneme, kolik jich je.
    """
    vsechny = db.query_all(
        f"""
        SELECT i.*, l.name AS library_name, COALESCE(p.plays, 0) AS plays
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        {_PREHRANI}
        WHERE i.series_id = ?
        ORDER BY i.parent_index_number, i.index_number, i.name
        """,
        (series_id,),
    )
    if not vsechny:
        return {}

    dily = [d for d in vsechny if not d["is_missing"]]
    archivovane = [d for d in vsechny if d["is_missing"]]

    # Serial, ze ktereho zbyl uz jen archiv, porad ma smysl ukazat -
    # jinak by odkaz z historie skoncil na 404.
    if not dily:
        dily, archivovane = archivovane, []

    rady: list[dict[str, Any]] = []
    podle_rady: dict[Any, dict[str, Any]] = {}
    for dil in dily:
        cislo = dil["parent_index_number"]
        rada = podle_rady.get(cislo)
        if rada is None:
            rada = {
                "number": cislo,
                # Nazev rady mame jen u nekterych polozek; kdyz chybi,
                # slozime ho z cisla.
                "name": dil["season_name"] or (
                    f"{_t('Řada')} {cislo}" if cislo is not None else _t("Ostatní")),
                "episodes": [],
                "size_bytes": 0,
                "plays": 0,
            }
            podle_rady[cislo] = rada
            rady.append(rada)
        rada["episodes"].append(dil)
        rada["size_bytes"] += int(dil["size_bytes"] or 0)
        rada["plays"] += int(dil["plays"] or 0)

    prvni = dily[0]
    return {
        "id": series_id,
        "name": prvni["series_name"] or prvni["name"],
        # Otisk plakatu serialu. Jde do adresy obrazku, takze po zmene
        # plakatu v Jellyfinu se nacte nova adresa - a prohlizec nemuze
        # podstrcit tu svou uschovanou kopii.
        "image_tag": prvni["series_image_tag"],
        "library_id": prvni["library_id"],
        "seasons": rady,
        "episode_count": len(dily),
        "season_count": len(rady),
        "size_bytes": sum(int(d["size_bytes"] or 0) for d in dily),
        "runtime_ticks": sum(int(d["runtime_ticks"] or 0) for d in dily),
        "plays": sum(int(d["plays"] or 0) for d in dily),
        "missing": all(d["is_missing"] for d in dily),
        # Díly, které v Jellyfinu už nejsou. Vypisují se zvlášť, ať je
        # z rozdílu proti Jellyfinu hned jasné, čím je způsobený.
        "archived": archivovane,
        "archived_count": len(archivovane),
        # Souhrn, ktery se u serialu ukazuje nahore - totez, co u filmu.
        "library_name": prvni["library_name"],
        "path": _spolecna_slozka(d["path"] for d in dily),
        "pridano": min((d["date_created"] for d in dily if d["date_created"]),
                       default=None),
        "posledni_dil": max((d["date_created"] for d in dily if d["date_created"]),
                            default=None),
        "zmereno": max((d["tech_updated_at"] for d in dily
                        if "tech_updated_at" in d.keys() and d["tech_updated_at"]),
                       default=None),
    }


def _spolecna_slozka(cesty: Any) -> str:
    """Slozka, ve ktere serial lezi - spolecny zacatek cest jeho dilu.

    U filmu se ukazuje cesta k souboru. Serial zadny jeden soubor nema,
    takze se ukazuje slozka: vezme se nejdelsi spolecny zacatek cest
    vsech dilu a urizne se na posledni oddelovac. U bezne slozene
    knihovny z toho vyjde slozka serialu.
    """
    seznam = [str(c) for c in cesty if c]
    if not seznam:
        return ""
    spolecne = seznam[0]
    for cesta in seznam[1:]:
        # Znak po znaku, dokud si odpovidaji.
        i = 0
        while i < min(len(spolecne), len(cesta)) and spolecne[i] == cesta[i]:
            i += 1
        spolecne = spolecne[:i]
        if not spolecne:
            return ""
    rez = max(spolecne.rfind("/"), spolecne.rfind("\\"))
    return spolecne[:rez] if rez > 0 else spolecne


def library_cards() -> list[dict[str, Any]]:
    """Knihovny s cisly pro uvodni dlazdice.

    `poster_id` je polozka, jejiz obrazek se pouzije jako pozadi dlazdice.
    Knihovny samotne casto vlastni obrazek nemaji, tak vezmeme nejvetsi
    titul uvnitr - ten byva i vizualne vyrazny.

    U serialove knihovny bereme **serial**, ne epizodu. Nejvetsi polozka
    v takove knihovne je vzdycky nejaky dil - a epizoda v Jellyfinu
    obvykle zadny backdrop nema, ten patri serialu. Dlazdice serialu proto
    zustavala seda: obrazek se nenacetl a `onerror` ho odstranil.
    """
    rows = db.query_all(
        """
        SELECT l.id,
               l.name,
               l.collection_type,
               COUNT(i.id)                    AS item_count,
               COALESCE(SUM(i.size_bytes), 0) AS size_bytes,
               COALESCE(SUM(i.runtime_ticks), 0) / 36000000000.0 AS hours,
               (SELECT COALESCE(s.series_id, s.id) FROM items s
                 WHERE s.library_id = l.id AND s.is_missing = 0
                 ORDER BY s.size_bytes DESC LIMIT 1) AS poster_id
        FROM libraries l
        LEFT JOIN items i ON i.library_id = l.id AND i.is_missing = 0
        GROUP BY l.id, l.name, l.collection_type
        ORDER BY item_count DESC, l.name
        """
    )
    type_names = {
        "movies": _t("Filmy"),
        "tvshows": _t("Seriály"),
        "homevideos": _t("Domácí videa"),
        "mixed": _t("Smíšený obsah"),
    }
    for row in rows:
        row["type_label"] = type_names.get(row["collection_type"], _t("Ostatní"))
    return rows


def library(library_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM libraries WHERE id = ?", (library_id,))


def library_overview(library_id: str) -> dict[str, Any]:
    """Souhrnna cisla jedne knihovny."""
    return db.query_one(
        f"""
        SELECT COUNT(*)                       AS item_count,
               COALESCE(SUM(size_bytes), 0)   AS size_bytes,
               COALESCE(SUM(runtime_ticks), 0) / 36000000000.0 AS hours,
               COALESCE(AVG(bitrate), 0)      AS avg_bitrate,
               SUM(CASE WHEN {RESOLUTION_CASE} = '4K' THEN 1 ELSE 0 END) AS uhd_count,
               SUM(CASE WHEN {ROZSAH_CASE} IN ('HDR', 'DOVI') THEN 1 ELSE 0 END) AS hdr_count,
               SUM(CASE WHEN tech_source IS NULL THEN 1 ELSE 0 END) AS without_tech
        FROM items
        WHERE is_missing = 0 AND library_id = ?
        """,
        (library_id,),
    ) or {}


def library_activity(library_id: str, days: Any, s_divaky: bool = True) -> dict[str, Any]:
    """Co se v teto knihovne sledovalo za zvolene obdobi.

    `days` je pocet dnu nebo vlastni `Obdobi`, stejne jako jinde. Bez
    vychozi hodnoty schvalne: drive tu bylo napevno 90 a stranka tak
    tise pocitala za jine obdobi, nez mel clovek zvolene.
    Posledni prehravani jsou mimo obdobi - vzdycky poslednich 25.

    `s_divaky=False` (ucet bez „…vcetne kdo to sledoval") vynecha divaky
    i jejich posledni prehravani - dotazy se pak vubec nespusti.
    """
    nedavne = [] if not s_divaky else db.query_all(
        """
        SELECT p.*, i.height, i.video_codec AS source_codec,
               i.audio_codec AS source_audio_codec
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE p.library_id = ? AND p.watched_seconds > 0
        ORDER BY p.started_at DESC, p.id DESC LIMIT 25
        """,
        (library_id,),
    )
    for row in nedavne:
        row["prepocet"] = popis_prepoctu(row)
    return {
        "totals": db.query_one(
            """
            SELECT COUNT(*) AS plays,
                   COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
                   COUNT(DISTINCT user_id) AS users,
                   COUNT(DISTINCT item_id) AS item_count
            FROM playback
            WHERE library_id = ? AND started_at >= ? AND started_at < ?
              AND watched_seconds > 0
            """,
            (library_id, *_meze(days)),
        ) or {},
        "top_items": db.query_all(
            """
            SELECT COALESCE(series_name, item_name) AS label,
                   SUM(watched_seconds) / 3600.0 AS hours,
                   COUNT(*) AS plays
            FROM playback
            WHERE library_id = ? AND started_at >= ? AND started_at < ?
              AND watched_seconds > 0
            GROUP BY label ORDER BY hours DESC LIMIT 10
            """,
            (library_id, *_meze(days)),
        ),
        # S user_id: podle něj anonymizace pozná, že popisek je jméno
        # diváka (bez něj by graf ukázal skutečná jména i se skrytými).
        # Jeden sloupec na diváka - viz DIVAK_JMENO.
        "top_users": [] if not s_divaky else db.query_all(
            f"""
            SELECT {DIVAK_JMENO} AS label, p.user_id,
                   SUM(p.watched_seconds) / 3600.0 AS hours
            FROM playback p
            LEFT JOIN users u ON u.id = p.user_id
            WHERE p.library_id = ? AND p.started_at >= ? AND p.started_at < ?
              AND p.watched_seconds > 0 AND p.user_name IS NOT NULL
            GROUP BY {DIVAK_SKUPINA}
            ORDER BY hours DESC, {DIVAK_PORADI}, label LIMIT 10
            """,
            (library_id, *_meze(days)),
        ),
        "recent": nedavne,
    }
