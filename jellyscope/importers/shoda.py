"""Druhá pojistka proti duplicitám: shoda podle obsahu (uživatel, položka, překryv času).

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import calendar
from datetime import UTC, datetime
from typing import Any

from .. import db
from .zaklad import _normalizuj_id


# ---------------------------------------------------------------------------
# Druha pojistka proti duplicitam: shoda podle obsahu
# ---------------------------------------------------------------------------
#
# Klic `import:pbr:<rowid>:<itemid>` ohlida opakovany import **tehoz zdroje**.
# Nestaci ale na dva pripady, ktere v praxi nastanou snadno:
#
#   1. Tataz historie se naimportuje z Jellystatu **i** z Playback Reportingu.
#      Klice maji jiny tvar (jst / pbr), takze o sobe nevi a vsechno se zdvoji.
#   2. Naimportuje se obdobi, ktere uz Jellyscope sam nasbiral. Vlastni zaznamy
#      maji uplne jiny session_key.
#
# Proto porovnavame i obsah. Za tentyz zaznam povazujeme prehravani, ktere ma
# stejneho uzivatele, stejnou polozku a **casove se prekryva**.
#
# Proc prekryv, a ne shodu casu zacatku: kazdy zdroj si zapisuje cas trochu
# jinak (zacatek prehravani vs. okamzik zapisu do tabulky), takze presna shoda
# by vetsinu duplicit propasla. Prekryv je odolnejsi a pritom bezpecny - tentyz
# film nemuze jeden clovek sledovat dvakrat zaroven.

def _epocha_pomalu(cas: str | None) -> float | None:
    """Poctivy prevod pres strptime. Zaloha pro cokoliv necekaneho."""
    try:
        return datetime.strptime(str(cas), db.TIME_FORMAT).replace(
            tzinfo=UTC).timestamp()
    except (TypeError, ValueError):
        return None


def _epocha(cas: str | None) -> float | None:
    """Cas z databaze prevedeny na sekundy, aby se dal porovnavat.

    Rychla cesta pro tvar, ktery v databazi opravdu je (`db.TIME_FORMAT`,
    tedy "2026-09-09 10:20:30"): spocita se z pozic znaku. `strptime` je
    pohodlny, ale rozebira formatovaci retezec znovu pri kazdem volani -
    a tahle funkce se pri jednom otevreni Nastaveni vola 300 000×, coz
    delalo 1,6 sekundy.

    Cokoliv jineho jde starou cestou. Neni to opatrnost navic: kdyby
    rychla cesta prijala neco, co strptime odmita (treba cas
    s milisekundami), zmenilo by se tim, ktere radky se povazuji za
    duplicitu - a to uz je zasah do dat, ne zrychleni.
    """
    if not cas:
        return None
    if (type(cas) is str and len(cas) == 19
            and cas[4] == "-" and cas[7] == "-" and cas[10] == " "
            and cas[13] == ":" and cas[16] == ":"):
        try:
            return float(calendar.timegm((
                int(cas[0:4]), int(cas[5:7]), int(cas[8:10]),
                int(cas[11:13]), int(cas[14:16]), int(cas[17:19]),
                0, 0, 0)))
        except ValueError:
            # Napr. "2026-13-45 99:99:99" - tvar sedi, hodnoty ne.
            return _epocha_pomalu(cas)
    return _epocha_pomalu(cas)


def _index_prehravani() -> dict[tuple[str, str], list[tuple[float, float]]]:
    """Casove useky uz ulozenych prehravani, podle uzivatele a polozky."""
    index: dict[tuple[str, str], list[tuple[float, float]]] = {}

    for row in db.query_all(
        """
        SELECT user_id, item_id, started_at, ended_at, watched_seconds
          FROM playback
         WHERE user_id IS NOT NULL AND user_id != ''
           AND item_id IS NOT NULL AND item_id != ''
        """
    ):
        zacatek = _epocha(row["started_at"])
        if zacatek is None:
            continue
        konec = _epocha(row["ended_at"])
        if konec is None or konec <= zacatek:
            konec = zacatek + max(int(row["watched_seconds"] or 0), 1)

        klic = (_normalizuj_id(row["user_id"]), _normalizuj_id(row["item_id"]))
        index.setdefault(klic, []).append((zacatek, konec))

    return index


def _uz_tam_je(index: dict[tuple[str, str], list[tuple[float, float]]],
               user_id: Any, item_id: Any, started_at: str, duration: int) -> bool:
    """Mame uz tohle prehravani - trebas z jineho zdroje?

    Kdyz nevime, kdo to hral nebo co, radeji netvrdime nic: bez obou udaju
    by se dalo splest dve ruzna prehravani a prisli bychom o data. Klic
    `session_key` v takovem pripade porad plati.
    """
    uzivatel = _normalizuj_id(user_id)
    polozka = _normalizuj_id(item_id)
    if not uzivatel or not polozka:
        return False

    zacatek = _epocha(started_at)
    if zacatek is None:
        return False
    konec = zacatek + max(int(duration or 0), 1)

    for stary_zacatek, stary_konec in index.get((uzivatel, polozka), []):
        # Dotykajici se useky (konec == zacatek) prekryv nejsou - divak
        # si mohl pustit dalsi dil hned, jak dokoukal predchozi.
        if zacatek < stary_konec and stary_zacatek < konec:
            return True
    return False


def _zapamatuj(index: dict[tuple[str, str], list[tuple[float, float]]],
               user_id: Any, item_id: Any, started_at: str, duration: int) -> None:
    """Prida prave prijaty zaznam do indexu.

    Bez tohohle kroku by se nepoznaly duplicity **uvnitr jednoho souboru** -
    a zalohy je obcas obsahuji.
    """
    uzivatel = _normalizuj_id(user_id)
    polozka = _normalizuj_id(item_id)
    zacatek = _epocha(started_at)
    if not uzivatel or not polozka or zacatek is None:
        return
    index.setdefault((uzivatel, polozka), []).append(
        (zacatek, zacatek + max(int(duration or 0), 1)))


def _known_users() -> dict[str, str]:
    return {row["id"]: row["name"] for row in db.query_all("SELECT id, name FROM users")}


def _known_items() -> dict[str, dict[str, Any]]:
    return {
        row["id"]: row
        for row in db.query_all(
            "SELECT id, name, type, series_name, library_id, default_audio_language"
            " FROM items"
        )
    }
