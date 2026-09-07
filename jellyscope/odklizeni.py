# -*- coding: utf-8 -*-
"""Odklízení historie: co se po čase smaže a jak zapomenout jednoho diváka.

U nástroje, který si pamatuje, kdo co kdy viděl, to dřív nebo později
někdo bude chtít — ať už kvůli sobě, kvůli lidem na svém serveru, nebo
kvůli pravidlům, která ho k tomu zavazují.

Tři věci, na kterých to tady stojí:

* **Ve výchozím stavu je to vypnuté.** Mazání dat nesmí nikdy začít samo
  od sebe: kdo o něj nepožádal, nesmí o nic přijít jen tím, že
  aktualizoval. Zapíná se v Nastavení a spolu s tím se říká, jak dlouhá
  historie se nechává.

* **Než se maže, řekne se kolik.** Nastavení ukazuje, kolik relací by
  současná hranice odklidila, a stránka to napíše dřív, než se uloží.
  „Smazalo se 40 000 přehrávání" je špatná chvíle na překvapení.

* **Živé přehrávání se nemaže.** Relace, která právě běží, se do hranice
  nepočítá, i kdyby začala předevčírem — sběrač ji má rozdělanou a smazat
  ji pod ním znamená ji vzápětí založit znovu, jen bez začátku.

Zapomenutí diváka je jednorázová věc a je to totéž mazání, jen vybrané
podle uživatele místo podle data. Samotný účet je v Jellyfinu, ne tady:
příští synchronizace ho zase uvidí a založí — bez historie, kterou jsme
zapomněli.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

log = logging.getLogger("jellyscope.odklizeni")

# Jak dlouho se historie nechává. Meze jsou schvalne siroke, ale ne
# bezedne: mene nez mesic uz neni historie, vic nez deset let je totez
# co vypnuto.
RETENCE_KLIC = "history_retention_days"
MIN_DNU = 30
MAX_DNU = 3650
VYCHOZI_DNU = 365


def retence_dnu() -> int:
    """Kolik dní historie se nechává."""
    return db.get_int_setting(RETENCE_KLIC, MIN_DNU, MAX_DNU, VYCHOZI_DNU)


def zapnuto() -> bool:
    """Maže se vůbec? Bez tohohle se nesmí smazat nic."""
    return db.get_setting("task_purge_enabled", "0") == "1"


def hranice(dnu: int | None = None) -> str:
    """Datum, pod které se relace už nenechávají (UTC, tvar databáze)."""
    dnu = retence_dnu() if dnu is None else dnu
    return (datetime.now(timezone.utc) - timedelta(days=dnu)).strftime(db.TIME_FORMAT)


def prehled(dnu: int | None = None) -> dict[str, Any]:
    """Co by dané nastavení odklidilo - bez toho, aby to udělalo.

    Kreslí se v Nastavení pod polem s počtem dní. Číslo, které si člověk
    může přečíst dřív, než klikne, je celý rozdíl mezi „nastavením" a
    „překvapením".
    """
    mez = hranice(dnu)
    radek = db.query_one(
        "SELECT COUNT(*) AS pocet, MIN(started_at) AS nejstarsi"
        " FROM playback WHERE started_at < ? AND is_active = 0",
        (mez,),
    ) or {}
    celkem = db.query_one("SELECT COUNT(*) AS pocet FROM playback") or {}
    nejstarsi_vse = db.query_one(
        "SELECT MIN(started_at) AS nejstarsi FROM playback") or {}
    return {
        "hranice": mez,
        "odejde": int(radek.get("pocet") or 0),
        "nejstarsi_odchazi": radek.get("nejstarsi") or "",
        "celkem": int(celkem.get("pocet") or 0),
        "nejstarsi": nejstarsi_vse.get("nejstarsi") or "",
    }


def smaz_stare(dnu: int | None = None) -> dict[str, Any]:
    """Smaže relace starší než hranice. Vrátí, kolik jich bylo.

    `is_active = 0` je podmínka, ne opatrnost navíc: relace, která teď
    hraje, patří sběrači a smazat ji znamená ji za deset vteřin založit
    znovu, jen bez začátku.
    """
    dnu = retence_dnu() if dnu is None else dnu
    mez = hranice(dnu)
    with db.connect() as conn:
        pred = conn.execute(
            "SELECT COUNT(*) AS pocet FROM playback"
            " WHERE started_at < ? AND is_active = 0", (mez,)).fetchone()
        kolik = int((pred or {"pocet": 0})["pocet"] or 0)
        if kolik:
            conn.execute(
                "DELETE FROM playback WHERE started_at < ? AND is_active = 0",
                (mez,))
        conn.commit()

    if kolik:
        log.info("Odklizeno %s prehravani starsich nez %s dni", kolik, dnu)
    return {"smazano": kolik, "hranice": mez, "dnu": dnu}


def zapomen_uzivatele(user_id: str) -> dict[str, Any]:
    """Smaže všechno, co je o jednom divákovi zaznamenané.

    Účet samotný je v Jellyfinu - ten odsud smazat nejde a nemá se o to
    ani pokoušet. Příští synchronizace ho tedy zase uvidí; historie,
    kterou jsme o něm měli, se ale nevrátí.
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return {"smazano": 0, "jmeno": ""}

    radek = db.query_one(
        "SELECT name FROM users WHERE id = ?", (user_id,)) or {}
    jmeno = radek.get("name") or ""

    with db.connect() as conn:
        pocet = conn.execute(
            "SELECT COUNT(*) AS pocet FROM playback WHERE user_id = ?",
            (user_id,)).fetchone()
        kolik = int((pocet or {"pocet": 0})["pocet"] or 0)
        conn.execute("DELETE FROM playback WHERE user_id = ?", (user_id,))
        conn.commit()

    log.info("Zapomenut divak %s: smazano %s prehravani", jmeno or user_id, kolik)
    return {"smazano": kolik, "jmeno": jmeno}


def divaci() -> list[dict[str, Any]]:
    """Kdo v historii je - pro výběr v Nastavení.

    Ptáme se historie, ne tabulky uživatelů: zapomenout se dá jen to, co
    je zapsané, a divák, po kterém nic nezůstalo, do nabídky nepatří.
    """
    return db.query_all(
        """
        SELECT p.user_id                AS user_id,
               COALESCE(MAX(u.name), MAX(p.user_name)) AS jmeno,
               COUNT(*)                 AS relaci,
               MIN(p.started_at)        AS od,
               MAX(p.started_at)        AS do
          FROM playback p
          LEFT JOIN users u ON u.id = p.user_id
         WHERE p.user_id IS NOT NULL
         GROUP BY p.user_id
         ORDER BY COUNT(*) DESC
        """
    )
