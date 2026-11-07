"""Společné pomocníky importu: čas, klíče záznamů, pojistka „do Jellyfinu jen čteme", sestavení a zápis řádku, rozbor názvu epizody.

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db, languages


class ImportError_(RuntimeError):
    """Chyba importu, kterou ma smysl ukazat uzivateli."""


# ---------------------------------------------------------------------------
# Spolecne pomocniky
# ---------------------------------------------------------------------------

def _parse_time(raw: Any) -> str | None:
    """Prevede cas z ciziho formatu na nas jednotny tvar.

    Kazdy nastroj zapisuje cas trochu jinak. Prevod na jeden tvar hned
    na vstupu je stejny princip jako u jazykovych kodu - normalizace.
    """
    if raw is None:
        return None

    text = str(raw).strip()
    if not text:
        return None

    candidate = text.replace("Z", "+00:00").replace("T", " ")
    if "." in candidate:
        candidate = candidate.split(".", 1)[0]
    if "+" in candidate[10:]:
        candidate = candidate[:10] + candidate[10:].split("+", 1)[0]

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(candidate.strip(), pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=UTC).strftime(db.TIME_FORMAT)

    return None


def _existing_keys(prefix: str) -> set[str]:
    """Klice, ktere uz z tohoto zdroje mame - aby se nic nezdvojilo."""
    rows = db.query_all(
        "SELECT session_key FROM playback WHERE session_key LIKE ?", (f"{prefix}%",)
    )
    return {row["session_key"] for row in rows}


def _normalizuj_id(hodnota: Any) -> str:
    """Sjednoti tvar identifikatoru z Jellyfinu.

    Tentyz uzivatel muze byt zapsany jako "a1b2c3d4e5f6..." nebo
    "a1b2c3d4-e5f6-...". Je to jedno a totez cislo, jen jinak napsane:
    Jellyfin ho ve svem API posila bez pomlcek, kdezto plugin Playback
    Reporting i Jellystat ho casto ukladaji s pomlckami.

    Kdybychom to neresili, `users.id = playback.user_id` by nesedlo a
    v historii by navzdy zustaly otazniky misto jmen.
    """
    return str(hodnota or "").replace("-", "").lower()


def _insert(rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO playback (
                session_key, user_id, user_name, item_id, item_name, item_type,
                series_name, library_id, client, device_name, play_method,
                audio_language, started_at, last_seen_at, ended_at,
                watched_seconds, paused_seconds, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
            """,
            rows,
        )
    return len(rows)


# Jak se muze jmenovat sloupec s jazykem stopy. Zakladni tabulka pluginu
# ho nema, ale nektere verze a vlastni upravy ano - a kdyz uz ten udaj
# dorazi, byla by skoda ho zahodit. Porovnava se malymi pismeny bez
# podtrzitek, at na zapisu nezalezi.
JAZYKOVE_SLOUPCE = ("audiolanguage", "audiostreamlanguage", "language",
                    "audiolang", "audio")


def _jazyk_ze_zdroje(data: dict[str, Any]) -> str | None:
    """Vytahne jazyk stopy z radku, pokud ho zdroj vubec posila."""
    for klic, hodnota in data.items():
        srovnany = str(klic).lower().replace("_", "").replace(" ", "")
        if srovnany in JAZYKOVE_SLOUPCE and hodnota:
            kod = languages.normalize(str(hodnota))
            if kod and kod != languages.UNKNOWN:
                return kod
    return None


def _build_row(
    key: str,
    user_id: str | None,
    item_id: str | None,
    started_at: str,
    duration: int,
    item_type: str | None,
    item_name: str | None,
    client: str | None,
    device: str | None,
    method: str | None,
    users: dict[str, str],
    items: dict[str, dict[str, Any]],
    user_name: str | None = None,
    audio_language: str | None = None,
    series_name: str | None = None,
) -> tuple[Any, ...]:
    """Poskladá jeden radek historie ve tvaru nasi tabulky.

    Poradi, v jakem hledame jmeno uzivatele a typ polozky, neni nahodne:
    nase vlastni tabulky jsou nejspolehlivejsi, pak to, co poslal zdroj,
    a teprve nakonec "?". Diky tomu se da importovat i drive, nez se
    aplikace vubec pripoji k Jellyfinu - jmena se doplni pozdeji,
    viz refresh_playback_metadata().
    """
    item_row = items.get(item_id or "") or {}
    ended = (
        datetime.strptime(started_at, db.TIME_FORMAT) + timedelta(seconds=duration)
    ).strftime(db.TIME_FORMAT)

    return (
        key,
        user_id,
        users.get(user_id or "") or user_name or "?",
        item_id,
        item_row.get("name") or item_name or "?",
        item_row.get("type") or item_type,
        # Jmeno serialu: nase knihovna je merodajna, ale kdyz o te polozce
        # jeste nic nevime (typicky import drive, nez probehla prvni
        # synchronizace), vezme se to, co poslal zdroj.
        item_row.get("series_name") or series_name,
        item_row.get("library_id"),
        client,
        device,
        method,
        # Jazyk, ktery zdroj poslal, ma prednost - je to udaj o tom, co
        # divak SKUTECNE poslouchal. Nektere verze pluginu Playback
        # Reporting ho ukladaji; vetsina zdroju ne.
        #
        # Kdyz ho zdroj nema, zbyva vychozi stopa souboru - a kdyz nezname
        # ani tu, zustane prazdny. Zamerne nic nedomyslime: vymysleny udaj
        # je horsi nez zadny.
        audio_language or item_row.get("default_audio_language"),
        started_at,
        ended,
        ended,
        duration,
    )


# ---------------------------------------------------------------------------
# Pojistka: do Jellyfinu jen čteme
# ---------------------------------------------------------------------------
#
# Jellyscope Jellyfin **nikdy nemění**. Všechno ostatní jsou obyčejné GET
# dotazy, u kterých to platí samo. Jediná výjimka je tenhle plugin: jeho
# dotazovací rozhraní je POST, protože se mu SQL posílá v těle požadavku.
#
# POST samo o sobě nic nemění - mění to, co se v něm pošle. Aby se sem
# omylem nedostal jiný příkaz než čtení (třeba při pozdější úpravě),
# projde každý dotaz tímhle sítem. Radši ať import odmítne pracovat,
# než aby zapsal do cizí databáze.

ZAKAZANA_SLOVA = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT",
)


def jen_cteni(sql: str) -> str:
    """Propustí jen jeden dotaz SELECT. Cokoliv jiného odmítne."""
    ocistene = " ".join(sql.split()).rstrip(";").strip()

    if not ocistene.upper().startswith("SELECT"):
        raise ImportError_(
            "Vnitřní pojistka: do Jellyfinu se smí posílat jen SELECT."
        )
    # Středník uprostřed by znamenal víc příkazů za sebou.
    if ";" in ocistene:
        raise ImportError_(
            "Vnitřní pojistka: dotaz nesmí obsahovat víc příkazů."
        )

    slova = set(ocistene.upper().replace("(", " ").replace(")", " ").split())
    nalezene = sorted(slova & set(ZAKAZANA_SLOVA))
    if nalezene:
        raise ImportError_(
            f"Vnitřní pojistka: dotaz obsahuje zápisové příkazy {nalezene}."
        )
    return ocistene


# ---------------------------------------------------------------------------
# Rozbor nazvu epizody
# ---------------------------------------------------------------------------
#
# Jak Jellystat i Playback Reporting skládají název epizody:
#
#     "Seal Team 6 - s02e07 - 7. epizoda"
#     "Nadace - s02e10 - Mýty o stvoření"
#
# Je v tom všechno, co potřebujeme k jednoznačnému určení dílu: seriál,
# řada i číslo. Hledat v knihovně **celý ten řetězec** nemá smysl - tam
# se ten díl jmenuje jen "7. epizoda".
_NAZEV_EPIZODY = re.compile(
    r"^(?P<serial>.+?)\s*[-–]\s*s(?P<rada>\d{1,3})e(?P<dil>\d{1,4})"
    r"(?:\s*[-–]\s*(?P<nazev>.*))?$",
    re.I,
)


def rozbor_nazvu(nazev: Any) -> dict[str, Any] | None:
    """Ze složeného názvu vytáhne seriál, řadu, díl a název dílu."""
    if not nazev:
        return None
    shoda = _NAZEV_EPIZODY.match(str(nazev).strip())
    if not shoda:
        return None
    serial = shoda.group("serial").strip(" -–")
    if not serial:
        return None
    return {
        "serial": serial,
        "rada": int(shoda.group("rada")),
        "dil": int(shoda.group("dil")),
        "nazev": (shoda.group("nazev") or "").strip(),
    }
