"""Zjisteni - to, kvuli cemu Jellyscope vznikl.

Jellystat vi, **co se sleduje**. MediaLyze vi, **co ty soubory jsou**.
Ani jeden z nich nevi obojí naráz, a proto neumi odpovedet na otazky,
ktere clovek ve skutecnosti ma:

    "Kolik terabajtu mi zabira obsah, na ktery se za rok nikdo nepodival?"
    "Ktery soubor nuti server nejcasteji prepocitavat video?"
    "Na co se divam nejvic - a mam to vubec v poradne kvalite?"
    "Ktery 4K film, co zere 60 GB, jsem videl jednou a uz nikdy?"

Kazda funkce v tomhle souboru spoji dve tabulky - `playback` (chovani)
a `items` (technicky stav souboru) - a z toho spojeni vypadne odpoved.
"""

from __future__ import annotations

from typing import Any

from . import db, dialect, stats

# Kolik sekund musi prehravani trvat, aby se pocitalo. Pod tuhle hranici
# to byva "omylem kliknul a zavrel" - a takove zaznamy by cisla zkreslovaly.
MIN_PLAY_SECONDS = 120

# Polozky pridane nedavno jeste nema smysl oznacovat za nesledovane.
NEW_ITEM_GRACE_DAYS = 60


def dead_storage(limit: int = 25, days: int = 365) -> dict[str, Any]:
    """Soubory, na ktere se za zvolene obdobi nikdo nepodival.

    Nejcennejsi jedine cislo, ktere tahle aplikace umi vyrobit: kolik
    mista zabira obsah, ktery nikoho nezajima.
    """
    common_where = f"""
        WHERE i.is_missing = 0
          AND i.size_bytes IS NOT NULL
          AND (i.date_created IS NULL
               OR substr(i.date_created, 1, 10) < date('now', '-{NEW_ITEM_GRACE_DAYS} days'))
          AND NOT EXISTS (
              SELECT 1 FROM playback p
               WHERE p.item_id = i.id
                 AND p.watched_seconds >= {MIN_PLAY_SECONDS}
                 AND p.started_at >= datetime('now', ?)
          )
    """

    totals = db.query_one(
        f"""
        SELECT COUNT(*) AS item_count, COALESCE(SUM(i.size_bytes), 0) AS size_bytes
        FROM items i
        {common_where}
        """,
        (f"-{days} days",),
    ) or {}

    rows = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_name, i.production_year,
               i.size_bytes, i.height, i.width, i.video_codec, i.bitrate,
               i.date_created, l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        {common_where}
        ORDER BY i.size_bytes DESC
        LIMIT ?
        """,
        (f"-{days} days", limit),
    )

    library_total = db.query_value(
        "SELECT COALESCE(SUM(size_bytes), 0) FROM items WHERE is_missing = 0"
    )
    dead_bytes = totals.get("size_bytes") or 0

    return {
        # Klic se zamerne nejmenuje "items" - v sablone by se stretl
        # s metodou slovniku .items() a misto cisla by se tise zobrazila
        # pomlcka. Viz kapitola 9 v JAK-TO-FUNGUJE.md.
        "item_count": totals.get("item_count") or 0,
        "size_bytes": dead_bytes,
        "library_bytes": library_total,
        "share": (dead_bytes / library_total * 100) if library_total else 0.0,
        "rows": rows,
        "days": days,
    }


def transcode_offenders(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Soubory, ktere server nejcasteji prepocitava.

    Transcode = server musi video za behu prekodovat, protoze ho prehravac
    neumi prehrat primo. Stoji to procesor a u slabsiho serveru to znamena
    sekajici obraz. Kdyz vis, ktere soubory to zpusobuji, muzes je prevest
    do vstricnejsiho formatu - a problem zmizi.
    """
    reasons_sql = dialect.group_concat_distinct(db.current_dialect(),
                                                "p.transcode_reasons")
    # Skupiny skládá `stats.klic_titulu()` - stejně jako u nejsledovanějších
    # titulů. Dřív se seskupovalo přímo v SQL podle názvu a seriál se rozpadl
    # na jednotlivé díly všude, kde `series_name` chybělo (import, starší
    # záznamy). Sjednocené je to schválně: dvě různá pravidla pro totéž by
    # se časem rozešla a jeden přehled by ukazoval něco jiného než druhý.
    radky = db.query_all(
        f"""
        SELECT {stats.SKUPINA_TITULU_SLOUPCE},
               COUNT(*)                       AS transcodes,
               SUM(p.watched_seconds)         AS seconds,
               {reasons_sql}                  AS reasons,
               MAX(i.video_codec)             AS video_codec,
               MAX(i.audio_codec)             AS audio_codec,
               MAX(i.height)                  AS height,
               MAX(i.width)                   AS width,
               MAX(i.bitrate)                 AS bitrate
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE p.play_method = 'Transcode'
          AND p.started_at >= datetime('now', ?)
          AND p.watched_seconds >= ?
    GROUP BY {stats.SKUPINA_TITULU_KLIC}
        """,
        (f"-{max(1, days)} days", MIN_PLAY_SECONDS),
    )

    # Technické údaje a důvody se opisují od nejčastěji překódovaného dílu
    # skupiny - u seriálu jsou stejně u všech dílů prakticky totožné.
    tituly = stats._slouc_tituly(
        radky, stats.KIND_BOTH, limit,
        {"transcodes": "transcodes", "seconds": "seconds"},
        prenest=("reasons", "video_codec", "audio_codec", "height",
                 "width", "bitrate"),
        # Soubor, který v knihovně není, nejde převést do vstřícnějšího
        # formátu - a přesně to je smysl tohohle seznamu.
        skryt_nezarazene=True,
    )
    for titul in tituly:
        titul["hours"] = titul["seconds"] / 3600.0
        titul["transcodes"] = int(titul["transcodes"])
    return tituly


def transcode_reasons(days: int) -> list[dict[str, Any]]:
    """Proc se vlastne prepocitava. Jellyfin duvod hlasi - jen ho nikdo necte."""
    rows = db.query_all(
        """
        SELECT transcode_reasons AS reasons, COUNT(*) AS plays
        FROM playback
        WHERE play_method = 'Transcode'
          AND transcode_reasons IS NOT NULL AND transcode_reasons != ''
          AND started_at >= datetime('now', ?)
        GROUP BY transcode_reasons
        """,
        (f"-{max(1, days)} days",),
    )

    # Jellyfin muze poslat vic duvodu naraz ("VideoCodecNotSupported,
    # AudioCodecNotSupported"). Rozdelime je a secteme kazdy zvlast.
    counter: dict[str, int] = {}
    for row in rows:
        for reason in str(row["reasons"]).split(","):
            reason = reason.strip()
            if reason:
                counter[reason] = counter.get(reason, 0) + int(row["plays"])

    return [
        {"label": label, "plays": plays}
        for label, plays in sorted(counter.items(), key=lambda pair: -pair[1])
    ]


def upgrade_candidates(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Hodne sledovane, ale ve slabe kvalite.

    Presne ta otazka, na kterou zadny z obou vzoru neumi odpovedet:
    "na co se divam nejvic - a stoji to za to?"

    ## Co je "slaba kvalita"

    Rozliseni **pod 1080p**, nebo nizky datovy tok. A pod 1080p se pozna
    z obou stran obrazu, ne jen z vysky - stejne jako `RESOLUTION_CASE`
    v stats.py.

    Drive tu stalo jen `i.height < 1000`, coz oznacilo za slabou kvalitu
    kazdy sirokouhly film: bezne 1080p vydani v pomeru scope ma rozmery
    1920x800, takze vyska je pod hranici, i kdyz je to plnohodnotne 1080p.
    Sirka je proto rozhodujici.

    A do sablony se sirka nedostavala vubec, takze i sloupec "Rozliseni"
    ukazoval neco jineho, nez co soubor doopravdy je.
    """
    radky = db.query_all(
        f"""
        SELECT {stats.SKUPINA_TITULU_SLOUPCE},
               SUM(p.watched_seconds)   AS seconds,
               COUNT(*)                 AS plays,
               MAX(i.height)            AS height,
               MAX(i.width)             AS width,
               MAX(i.bitrate)           AS bitrate,
               MAX(i.video_codec)       AS video_codec,
               MAX(i.size_bytes)        AS size_bytes
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= datetime('now', ?)
          AND p.watched_seconds >= ?
          AND i.is_missing = 0
          AND (
                (
                     COALESCE(i.width, 0)  < 1800
                 AND COALESCE(i.height, 0) < 1000
                 AND (i.width IS NOT NULL OR i.height IS NOT NULL)
                )
             OR (i.bitrate IS NOT NULL AND i.bitrate < 3000000)
          )
     GROUP BY {stats.SKUPINA_TITULU_KLIC}
        """,
        (f"-{max(1, days)} days", MIN_PLAY_SECONDS),
    )

    tituly = stats._slouc_tituly(
        radky, stats.KIND_BOTH, limit,
        # Velikost se scita: u serialu je zajimavy soucet za vsechny dily.
        {"seconds": "seconds", "plays": "plays", "size_bytes": "size_bytes"},
        prenest=("height", "width", "bitrate", "video_codec"),
        skryt_nezarazene=True,
    )
    for titul in tituly:
        titul["hours"] = titul["seconds"] / 3600.0
        titul["plays"] = int(titul["plays"])
    return tituly


def oversized_rarely_watched(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Velke soubory s malym vyuzitim - kandidati na zmenseni.

    Merime "gigabajty na jednu odsledovanou hodinu". Cim vyssi cislo,
    tim horsi pomer mista k uzitku.
    """
    # Delitel nesmi byt nula, jinak by u nesledovaneho titulu vysel nekonecny
    # pomer. Ctvrthodina jako spodni hranice je dohodnuta mez, ne mereni.
    watched_hours = dialect.greatest(
        db.current_dialect(), "COALESCE(SUM(p.watched_seconds), 0) / 3600.0", "0.25"
    )

    # Pozor na HAVING: alias sloupce (`plays`) v nem PostgreSQL nedovoluje,
    # i kdyz SQLite ano. Proto se vyraz opakuje cely.
    return db.query_all(
        f"""
        SELECT i.id,
               COALESCE(i.series_name, i.name) AS label,
               i.height,
               i.width,
               i.bitrate,
               i.video_codec,
               i.size_bytes,
               COALESCE(SUM(p.watched_seconds), 0) / 3600.0 AS hours,
               COUNT(p.id) AS plays,
               i.size_bytes / 1073741824.0 / {watched_hours} AS gb_per_hour
        FROM items i
        LEFT JOIN playback p
               ON p.item_id = i.id
              AND p.watched_seconds >= ?
              AND p.started_at >= datetime('now', ?)
        WHERE i.is_missing = 0
          AND i.size_bytes IS NOT NULL
          AND i.size_bytes > 2147483648      -- vetsi nez 2 GB
        GROUP BY i.id, i.series_name, i.name, i.height, i.width, i.bitrate,
                 i.video_codec, i.size_bytes
        HAVING COUNT(p.id) <= 2
        ORDER BY gb_per_hour DESC
        LIMIT ?
        """,
        (MIN_PLAY_SECONDS, f"-{max(1, days)} days", limit),
    )


def storage_efficiency(days: int) -> list[dict[str, Any]]:
    """Za kazdou knihovnu: kolik mista zabira a kolik se z ni skutecne sleduje.

    Ke ktere knihovne prehravani patri, se **odvozuje z polozky**
    (`i.library_id`), ne ze sloupce v zaznamu prehravani. Ten je jen
    zaloha pro pripad, ze uz polozka v knihovne neni.

    Drive se cetl jen `p.library_id` - a ten je u velke casti zaznamu
    prazdny: importovana historie knihovnu nezna vubec a vlastni sber ji
    nevyplni u polozky, kterou jsme jeste nesynchronizovali. Odsledovany
    cas i pocet sledovanych titulu proto vychazely mnohem niz, nez ve
    skutecnosti byly, zatimco obsah a velikost (ty se ctou z `items`)
    sedely. Presne ten rozpor, ktery slo videt v tabulce.
    """
    return db.query_all(
        """
        SELECT l.name                                       AS label,
               COUNT(i.id)                                  AS item_count,
               COALESCE(SUM(i.size_bytes), 0)               AS size_bytes,
               (SELECT COALESCE(SUM(p.watched_seconds), 0) / 3600.0
                  FROM playback p
             LEFT JOIN items pi ON pi.id = p.item_id
                 WHERE COALESCE(pi.library_id, p.library_id) = l.id
                   AND p.started_at >= datetime('now', ?)) AS hours,
               -- Jen položky, které v knihovně **opravdu jsou**. Celkový
               -- počet výš je taky jen z živých (is_missing = 0), takže
               -- bez téhle podmínky by se počítalo jablko ku hrušce:
               -- sledované včetně archivu proti celkovému bez archivu.
               -- U seriálu, ze kterého část dílů mezitím zmizela, tak
               -- vycházelo využití přes 100 %.
               (SELECT COUNT(DISTINCT p.item_id)
                  FROM playback p
                  JOIN items pi ON pi.id = p.item_id
                 WHERE pi.library_id = l.id
                   AND pi.is_missing = 0
                   AND p.watched_seconds >= ?
                   AND p.started_at >= datetime('now', ?)) AS watched_items
        FROM libraries l
        LEFT JOIN items i ON i.library_id = l.id AND i.is_missing = 0
        GROUP BY l.id, l.name
        ORDER BY size_bytes DESC
        """,
        (f"-{max(1, days)} days", MIN_PLAY_SECONDS, f"-{max(1, days)} days"),
    )


def duplicate_candidates(limit: int = 30) -> list[dict[str, Any]]:
    """Filmy, ktere vypadaji, ze je mas dvakrat.

    Slovo "kandidati" je tu zamerne. Shoda nazvu a roku neni dukaz -
    muze jit o rezisersky sestrih vedle kinoverze. Aplikace proto nic
    nemaze a nic nenavrhuje smazat, jen ukaze, kam se podivat.
    """
    current = db.current_dialect()
    # Rozliseni se sklada stejnym pravidlem jako vsude jinde - podle sirky
    # i vysky (viz stats.RESOLUTION_CASE). Drive tu stalo prosté
    # `height || 'p'`, takze 4K film v pomeru scope (3840x1608) se v popisku
    # varianty ukazal jako "hevc / 1608p".
    variants = dialect.group_concat(
        current,
        "COALESCE(video_codec, '?') || ' / ' || " + stats.RESOLUTION_CASE,
        "' | '",
    )
    paths = dialect.group_concat(current, "path", "char(10)")

    return db.query_all(
        f"""
        SELECT LOWER(TRIM(name))                AS key_name,
               MAX(name)                        AS label,
               production_year,
               COUNT(*)                         AS copies,
               SUM(size_bytes)                  AS size_bytes,
               {variants}                       AS variants,
               {paths}                          AS paths
        FROM items
        WHERE is_missing = 0
          AND type = 'Movie'
          AND name IS NOT NULL
        GROUP BY LOWER(TRIM(name)), production_year
        HAVING COUNT(*) > 1
        ORDER BY size_bytes DESC
        LIMIT ?
        """,
        (limit,),
    )


def never_finished(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Zacate, ale nedokoncene.

    Porovnavame, kam se prehravani dostalo, s celkovou delkou polozky.
    Pod 15 % delky bereme jako "zkusil a nechal toho".
    """
    return db.query_all(
        """
        SELECT COALESCE(p.series_name, p.item_name) AS label,
               COUNT(*) AS attempts,
               MAX(p.position_ticks * 100.0 / i.runtime_ticks) AS best_percent,
               i.runtime_ticks / 600000000.0 AS runtime_minutes
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= datetime('now', ?)
          AND p.watched_seconds >= ?
          AND i.runtime_ticks > 0
          AND p.position_ticks IS NOT NULL
        GROUP BY COALESCE(p.series_name, p.item_name), i.runtime_ticks
        HAVING MAX(p.position_ticks * 100.0 / i.runtime_ticks) < 15
        ORDER BY attempts DESC, best_percent ASC
        LIMIT ?
        """,
        (f"-{max(1, days)} days", MIN_PLAY_SECONDS, limit),
    )
