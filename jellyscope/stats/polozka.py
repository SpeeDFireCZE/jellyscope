"""Detail jedné položky: verze, stopy, přehrání, sourozenci, smazání.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, formatting
from .prehravani import popis_prepoctu

# Jméno loggeru jako před rozdělením: je vidět v každém řádku logu.
log = logging.getLogger("jellyscope.stats")


# ---------------------------------------------------------------------------
# Detail jedne polozky
# ---------------------------------------------------------------------------

def item(item_id: str) -> dict[str, Any] | None:
    return db.query_one(
        """
        SELECT i.*, l.name AS library_name, l.collection_type
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.id = ?
        """,
        (item_id,),
    )


def verze_polozky(item_id: str) -> list[dict[str, Any]]:
    """Všechny soubory jednoho titulu - 4K vedle 1080p.

    Prázdný seznam znamená „jeden soubor" (nebo knihovnu, která se od
    zavedení verzí ještě nesynchronizovala). Detail se pak chová jako
    dřív a nic nepřepíná - jedna verze není z čeho vybírat.
    """
    return db.query_all(
        "SELECT source_id, poradi, nazev, path, container, video_codec,"
        " audio_codec, audio_channels, width, height, bitrate, size_bytes,"
        " video_range, runtime_ticks, audio_languages, subtitle_languages"
        " FROM item_versions WHERE item_id = ? ORDER BY poradi",
        (item_id,))


def item_streams(item_id: str) -> dict[str, list[dict[str, Any]]]:
    """Stopy polozky rozdelene na video, zvuk a titulky."""
    rows = db.query_all(
        "SELECT * FROM item_streams WHERE item_id = ? ORDER BY type, stream_index",
        (item_id,),
    )
    grouped: dict[str, list[dict[str, Any]]] = {"Video": [], "Audio": [], "Subtitle": []}
    for row in rows:
        grouped.setdefault(row["type"], []).append(row)
    return grouped


def item_playback(item_id: str, limit: int = 25) -> list[dict[str, Any]]:
    # Kodeky souboru kvuli bubline u znacky transcode - stejne jako
    # v Historii (viz popis_prepoctu).
    rows = db.query_all(
        """
        SELECT p.*, i.video_codec AS source_codec,
               i.audio_codec AS source_audio_codec
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE p.item_id = ? AND p.watched_seconds > 0
        ORDER BY p.started_at DESC, p.id DESC LIMIT ?
        """,
        (item_id, limit),
    )
    for row in rows:
        row["prepocet"] = popis_prepoctu(row)
    return rows


def item_playback_summary(item_id: str) -> dict[str, Any]:
    return db.query_one(
        """
        SELECT COUNT(*) AS plays,
               COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
               COUNT(DISTINCT user_id) AS users,
               MAX(started_at) AS last_played
        FROM playback
        WHERE item_id = ? AND watched_seconds > 0
        """,
        (item_id,),
    ) or {}


def sibling_episodes(item_row: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    """Ostatni epizody tehoz serialu - navigace v detailu epizody."""
    if not item_row.get("series_id"):
        return []
    return db.query_all(
        """
        SELECT id, name, index_number, parent_index_number, size_bytes, height
        FROM items
        WHERE series_id = ? AND is_missing = 0
        ORDER BY parent_index_number, index_number
        LIMIT ?
        """,
        (item_row["series_id"], limit),
    )


def delete_item(item_id: str) -> dict[str, Any]:
    """Nenavratne smaze polozku i jeji historii prehravani.

    Tohle je jedina cesta, jak z Jellyscope neco doopravdy zmizi, a je
    zamerne jen rucni. Automaticky se polozky vzdycky jen archivuji -
    kdyz Jellyfin na chvili vypadne nebo se prepoji uloziste, prisel bys
    jinak o historii kvuli docasnemu vypadku.

    Vraci, co se smazalo, aby slo uzivateli rict, o co prisel.
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, series_name FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return {"status": "error", "message": "Položka neexistuje."}
        plays = _smaz_polozku(conn, item_id)

    log.info("smazana polozka %s (%s prehravani)", row["name"], plays)
    return {
        "status": "ok",
        "name": row["name"],
        "series_name": row["series_name"],
        "plays": plays,
    }


def _smaz_polozku(conn: Any, item_id: str) -> int:
    """Smaže položku, její historii, stopy a verze. Vrací počet přehrání.

    Historie se maže ručně - cizí klíč tam schválně není, aby výpadek
    Jellyfinu nikdy nemohl smazat statistiky. Maže se i z koše
    zapomenutých diváků: kdyby se divák do noci vrátil, jeho přehrání
    smazaného titulu by se vrátila jako záznamy bez titulu.
    """
    plays = int(conn.execute(
        "SELECT COUNT(*) AS c FROM playback WHERE item_id = ?", (item_id,)
    ).fetchone()["c"])
    conn.execute("DELETE FROM playback WHERE item_id = ?", (item_id,))
    if db.sloupce(conn, db.KOS):           # koš vzniká až s prvním zapomenutím
        conn.execute(f"DELETE FROM {db.KOS} WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM item_streams WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM item_versions WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return plays


def nahled_polozky(row: dict[str, Any], sirka: int = 80) -> str:
    """Adresa malého plakátu cílového titulu v nabídkách přiřazení.

    Jen pro živé položky - položka z archivu už v Jellyfinu obrázek nemá.

    U dílu plakát seriálu, ne snímek z dílu: ten je na šířku a vedle
    názvu v řádku by vypadal jinak než plakáty filmů. Který díl to je,
    říká popis (S01E02). Otisk jde do adresy, ať prohlížeč nedrží starý
    obrázek.
    """
    if row.get("series_id"):
        kdo, otisk = row["series_id"], row.get("series_image_tag") or ""
    else:
        kdo, otisk = row["id"], row.get("image_tag") or ""
    return f"/image/{kdo}?kind=Primary&w={sirka}" + (f"&tag={otisk}" if otisk else "")


def jmeno_souboru(cesta: Any) -> str:
    """Jen jméno souboru z cesty - i z windowsové (D:\\media\\…).

    Celá cesta o disku serveru prozradí víc, než výběr potřebuje.
    """
    return str(cesta or "").replace("\\", "/").rsplit("/", 1)[-1]


def popis_polozky(row: dict[str, Any]) -> str:
    """Název titulu tak, jak se ukazuje v nabídkách: seriál, S01E02, díl."""
    if row.get("series_name"):
        kod = formatting.kod_dilu(row)
        cisla = f" {kod}" if kod else ""
        return f"{row['series_name']}{cisla} – {row.get('name') or '?'}"
    rok = f" ({row['production_year']})" if row.get("production_year") else ""
    return f"{row.get('name') or '?'}{rok}"
