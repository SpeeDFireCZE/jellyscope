"""Diváci: souhrny po divácích, tabulka diváků, detail diváka.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from typing import Any

from .. import db
from ..i18n import translate as _t
from .obdobi import _meze, je_transcode


# Souhrn po divácích: jeden řádek na diváka, ne na jméno. V historii
# zůstává jméno z doby přehrání (refresh_playback_metadata ho schválně
# nepřepisuje), takže skupina podle jména rozdělila přejmenovaného diváka
# na dva sloupce - a slila dva různé lidi se stejným jménem. Současné
# jméno je v `users`; řádky bez user_id (import bez UserId) se dělí
# podle jména, jinak by je NULL slilo do jednoho.
#
# Dotaz musí mít `FROM playback p LEFT JOIN users u ON u.id = p.user_id`.
# Řazení `DIVAK_PORADI` nemá NULL v klíči: SQLite ho dává nahoru,
# PostgreSQL dolů, a na hranici LIMIT by každá vrátila jiné řádky.
DIVAK_JMENO = "COALESCE(MAX(u.name), MAX(p.user_name))"
DIVAK_SKUPINA = "p.user_id, CASE WHEN p.user_id IS NULL THEN p.user_name END"
DIVAK_PORADI = "p.user_id IS NULL, p.user_id"


def top_users(days: int, limit: int = 8) -> list[dict[str, Any]]:
    return db.query_all(
        f"""
        SELECT {DIVAK_JMENO}                   AS label,
               p.user_id,
               SUM(p.watched_seconds) / 3600.0 AS hours,
               COUNT(*)                        AS plays
        FROM playback p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.started_at >= ? AND p.started_at < ?
          AND p.watched_seconds > 0
          AND p.user_name IS NOT NULL
        GROUP BY {DIVAK_SKUPINA}
        ORDER BY hours DESC, {DIVAK_PORADI}, label
        LIMIT ?
        """,
        (*_meze(days), limit),
    )


# ---------------------------------------------------------------------------
# Uzivatele
# ---------------------------------------------------------------------------

def user_table(days: int) -> list[dict[str, Any]]:
    return db.query_all(
        f"""
        SELECT u.id,
               u.name,
               u.is_administrator,
               COALESCE(SUM(p.watched_seconds), 0) / 3600.0 AS hours,
               COUNT(p.id)                                  AS plays,
               COUNT(DISTINCT p.item_id)                    AS item_count,
               MAX(p.started_at)                            AS last_seen,
               COALESCE(SUM(CASE WHEN {je_transcode('p.play_method')}
                                 THEN p.watched_seconds ELSE 0 END), 0) / 3600.0
                                                            AS transcoded_hours
        FROM users u
        LEFT JOIN playback p
               ON p.user_id = u.id
              AND p.started_at >= ? AND p.started_at < ?
              AND p.watched_seconds > 0
        GROUP BY u.id, u.name, u.is_administrator
        ORDER BY hours DESC, u.name
        """,
        (*_meze(days),),
    )


def _s_prokliknutim(rows: list[dict[str, Any]],
                    zastupci: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ke slucovanym radkum doplni id polozky, na kterou se da prokliknout.

    `zastupci` jsou serazeni od nejsledovanejsiho, takze prvni nalez pro
    dany nazev je ten spravny.
    """
    nejlepsi: dict[str, str] = {}
    for row in zastupci:
        nejlepsi.setdefault(str(row["label"]), str(row["item_id"]))
    for row in rows:
        row["item_id"] = nejlepsi.get(str(row["label"]))
    return rows


def user_genres(user_id: str, days: int, limit: int = 8) -> list[dict[str, Any]]:
    """Zanry, ktere uzivatel sleduje - podle odsledovaneho casu.

    Jeden titul ma zanru vic ("Akcni, Sci-Fi, Dobrodruzny"), takze se jeho
    cas zapocita do kazdeho z nich. Soucet proto neni roven celkovemu casu
    a procenta se nescitaji na sto - v UI je u toho poznamka.

    Rozdelit cas mezi zanry rovnym dilem by vypadalo poctiveji, ale nebylo
    by to o nic pravdivejsi: nikdo nevi, kolik z filmu bylo "akce" a kolik
    "sci-fi". Radeji rekneme nahlas, ze se titul pocita vickrat.
    """
    rows = db.query_all(
        """
        SELECT i.genres, SUM(p.watched_seconds) / 3600.0 AS hours
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.user_id = ? AND p.started_at >= ? AND p.started_at < ?
           AND p.watched_seconds > 0
           AND i.genres IS NOT NULL AND i.genres != ''
         GROUP BY i.genres
        """,
        (user_id, *_meze(days)),
    )

    soucty: dict[str, float] = {}
    for row in rows:
        hodin = float(row["hours"] or 0)
        for zanr in str(row["genres"]).split("|"):
            zanr = zanr.strip()
            if zanr:
                soucty[zanr] = soucty.get(zanr, 0.0) + hodin

    if not soucty:
        return []

    serazene = sorted(soucty.items(), key=lambda dvojice: -dvojice[1])
    celkem = sum(soucty.values())

    vysledek = [
        {"label": zanr, "hours": hodin, "value": hodin,
         "percent": hodin / celkem * 100 if celkem else 0.0}
        for zanr, hodin in serazene[:limit]
    ]
    # Zbytek do jednoho radku. Dlouhy seznam zanru, z nichz kazdy ma pul
    # hodiny, nikdo necte - a to hlavni v nem zanikne.
    zbytek = serazene[limit:]
    if zbytek:
        hodin = sum(h for _zanr, h in zbytek)
        vysledek.append({
            "label": _t("Ostatní"), "hours": hodin, "value": hodin,
            "percent": hodin / celkem * 100 if celkem else 0.0,
            "genres": len(zbytek),
        })
    return vysledek


def user_detail(user_id: str, days: int) -> dict[str, Any]:
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        return {}

    return {
        "user": user,
        "totals": db.query_one(
            """
            SELECT COUNT(*) AS plays,
                   COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
                   COUNT(DISTINCT item_id) AS item_count
            FROM playback
            WHERE user_id = ? AND started_at >= ? AND started_at < ? AND watched_seconds > 0
            """,
            (user_id, *_meze(days)),
        ) or {},
        # `item_id` je tu kvuli prokliku do knihovny. U serialu se radky
        # slucuji podle nazvu, takze zadne jedno spravne id neexistuje -
        # vezmeme to nejsledovanejsi, coz je i to, ktere clovek hleda.
        "top_items": _s_prokliknutim(
            db.query_all(
                """
                SELECT COALESCE(series_name, item_name) AS label,
                       SUM(watched_seconds) / 3600.0 AS hours,
                       COUNT(*) AS plays
                FROM playback
                WHERE user_id = ? AND started_at >= ? AND started_at < ?
                  AND watched_seconds > 0
                GROUP BY label
                ORDER BY hours DESC
                LIMIT 10
                """,
                (user_id, *_meze(days)),
            ),
            db.query_all(
                """
                SELECT COALESCE(series_name, item_name) AS label, item_id,
                       SUM(watched_seconds) AS total
                FROM playback
                WHERE user_id = ? AND started_at >= ? AND started_at < ?
                  AND watched_seconds > 0 AND item_id IS NOT NULL
                GROUP BY label, item_id
                ORDER BY total DESC
                """,
                (user_id, *_meze(days)),
            ),
        ),
        "genres": user_genres(user_id, days),
        # Zařízení i pod svým jménem a s divákem: podle toho anonymizace
        # pozná, že popisek je zařízení tohohle člověka (pristup.ZARIZENI).
        "devices": [{**r, "device_name": r["label"], "user_id": user_id} for r in db.query_all(
            """
            SELECT COALESCE(device_name, 'nezname') AS label,
                   COALESCE(client, '') AS client,
                   SUM(watched_seconds) / 3600.0 AS hours
            FROM playback
            WHERE user_id = ? AND started_at >= ? AND started_at < ? AND watched_seconds > 0
            GROUP BY label, client
            ORDER BY hours DESC
            LIMIT 10
            """,
            (user_id, *_meze(days)),
        )],
    }
