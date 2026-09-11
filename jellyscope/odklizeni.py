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
from pathlib import Path
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

# Prepsani souboru po mazani (db.preskladat) trva umerne velikosti
# databaze - odhadem minutu na 5 GB - a SQLite na tu dobu drzi zamek na
# vsechno. Po nocnim odklizeni to nikomu nevadi; po kliknuti na "Ano,
# zapomenout" by se ale cela aplikace na tu minutu zasekla. Rucni
# zapomenuti proto jen smaze a prepis souboru si poznamena na noc.
# Hodnota je cas zadosti (UTC); prazdno = nic neceka.
ODLOZENE_KLIC = "db_compaction_pending"


def retence_dnu() -> int:
    """Kolik dní historie se nechává."""
    return db.get_int_setting(RETENCE_KLIC, MIN_DNU, MAX_DNU, VYCHOZI_DNU)


def zapnuto() -> bool:
    """Maže se vůbec? Bez tohohle se nesmí smazat nic."""
    return db.get_setting("task_purge_enabled", "0") == "1"


def v_mezich(dnu: Any) -> int:
    """Počet dní, se kterým se smí mazat.

    Hlídá se to nebezpečné, ne to malé. Nula a záporné číslo posunou
    hranici na „teď“ nebo do budoucnosti, takže by odešlo **všechno** -
    to projít nesmí, stejně jako nesmysl místo čísla. Kratší hranici, než
    dovolí nastavení, ale zakazovat nebudeme: kdo ji zadal rovnou v kódu,
    ví, co dělá.

    Rozmezí pro hodnotu z **nastavení** hlídá `retence_dnu()`. Tam jde
    o to, co smí zadat člověk ve formuláři, a to je jiná otázka.
    """
    try:
        dnu = int(dnu)
    except (TypeError, ValueError):
        return VYCHOZI_DNU
    if dnu < 1:
        return VYCHOZI_DNU
    return min(dnu, MAX_DNU)


def hranice(dnu: int | None = None) -> str:
    """Datum, pod které se relace už nenechávají (UTC, tvar databáze).

    Meze platí i pro předaný počet dní, ne jen pro ten z nastavení -
    tady se z čísla stává hranice, takže tady se musí hlídat.
    """
    dnu = retence_dnu() if dnu is None else v_mezich(dnu)
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
    dnu = retence_dnu() if dnu is None else v_mezich(dnu)
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
        # Az tady jsou data opravdu pryc - viz db.preskladat(). Pousti se
        # jen kdyz se neco smazalo: prepisuje cely soubor.
        if db.preskladat() and not kos_ma_radky():
            # Objednavka na prepis se ruzi jen tehdy, kdyz uz nema co
            # pokryvat. Kdyz v kosi neco je, musi zustat: podle ni se
            # pozna, kdy kos vysypat, a bez ni by v nem radky uvizly.
            db.set_setting(ODLOZENE_KLIC, "")
    return {"smazano": kolik, "hranice": mez, "dnu": dnu}


def zapomen_uzivatele(user_id: str, do_kose: bool = False,
                      zaloha: str = "") -> dict[str, Any]:
    """Smaže všechno, co je o jednom divákovi zaznamenané.

    Účet samotný je v Jellyfinu - ten odsud smazat nejde a nemá se o to
    ani pokoušet. Příští synchronizace ho tedy zase uvidí; historie,
    kterou jsme o něm měli, se ale nevrátí.

    `do_kose` rozhoduje o tom, jestli jde zásah do noci vzít zpět.
    `True` (tak to dělá tlačítko v Nastavení) řádky **přesune do koše**:
    ze statistik zmizí okamžitě, ale dají se vrátit, dokud je v noci
    nevysype `vysyp_kos()`. `False` je smaže rovnou a hned přepíše soubor
    databáze - to je cesta pro toho, kdo ví, co dělá, a nechce čekat.
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return {"smazano": 0, "jmeno": ""}

    radek = db.query_one(
        "SELECT name FROM users WHERE id = ?", (user_id,)) or {}
    jmeno = radek.get("name") or ""

    udalost = 0
    with db.connect() as conn:
        pocet = conn.execute(
            "SELECT COUNT(*) AS pocet FROM playback WHERE user_id = ?",
            (user_id,)).fetchone()
        kolik = int((pocet or {"pocet": 0})["pocet"] or 0)
        if kolik and do_kose:
            udalost = _zaloz_udalost(conn, user_id, jmeno, kolik, zaloha)
            spolecne = [s for s in db.sloupce(conn, "playback")
                        if s in set(db.sloupce(conn, db.KOS)) and s != "id"]
            vypis = ", ".join(spolecne)
            # Nejdriv kopie, teprve pak mazani - kdyby se cestou neco
            # pokazilo, at radky zustanou v playbacku, ne nikde.
            conn.execute(
                f"INSERT INTO {db.KOS} (udalost, {vypis})"
                f" SELECT ?, {vypis} FROM playback WHERE user_id = ?",
                (udalost, user_id))
        conn.execute("DELETE FROM playback WHERE user_id = ?", (user_id,))
        conn.commit()

    log.info("Zapomenut divak %s: smazano %s prehravani", jmeno or user_id, kolik)
    if kolik and do_kose:
        # V kosi radky jeste jsou, takze prepis souboru ted nema smysl -
        # udela se v noci, az se kos vysype.
        odloz_preskladani()
    elif kolik:
        # „Zapomen toho divaka" ma znamenat, ze je pryc - ne ze se jen
        # prestane hledat. Bez tohohle jeho zaznamy v souboru zustanou
        # lezet, dokud je neco neprepise, a daji se z nej precist.
        db.preskladat()
    return {"smazano": kolik, "jmeno": jmeno, "udalost": udalost}


# ---------------------------------------------------------------------------
# Koš: dokud se nevysype, dá se zapomenutí vzít zpět
# ---------------------------------------------------------------------------

V_KOSI = "kos"
JEN_ZALOHA = "zaloha"


def _zaloz_udalost(conn: Any, user_id: str, jmeno: str, kolik: int,
                   zaloha: str) -> int:
    """Zapíše jedno zapomenutí a vrátí jeho číslo."""
    conn.execute(
        "INSERT INTO zapomenuti (user_id, jmeno, relaci, zapomenuto_v,"
        " zaloha, stav) VALUES (?,?,?,?,?,?)",
        (user_id, jmeno or user_id, kolik, db.utcnow(), zaloha, V_KOSI))
    radek = conn.execute(
        "SELECT MAX(id) AS id FROM zapomenuti WHERE user_id = ?",
        (user_id,)).fetchone()
    return int(dict(radek or {}).get("id") or 0)


def vrat_z_kose(udalost: Any) -> dict[str, Any]:
    """Vrátí do historie to, co jedno zapomenutí odklidilo do koše.

    Vrací se celá skupina najednou - divák je buď zapomenutý, nebo není.
    Sloupec `id` se nekopíruje: nová čísla si přidělí databáze sama
    a nikdo se na ně neodkazuje.
    """
    try:
        cislo = int(udalost)
    except (TypeError, ValueError):
        return {"vraceno": 0, "jmeno": ""}

    with db.connect() as conn:
        radek = conn.execute(
            "SELECT jmeno, stav FROM zapomenuti WHERE id = ?",
            (cislo,)).fetchone()
        udaje = dict(radek or {})
        if not udaje or str(udaje.get("stav")) != V_KOSI:
            return {"vraceno": 0, "jmeno": str(udaje.get("jmeno") or "")}

        spolecne = [s for s in db.sloupce(conn, "playback")
                    if s in set(db.sloupce(conn, db.KOS)) and s != "id"]
        vypis = ", ".join(spolecne)
        pocet = conn.execute(
            f"SELECT COUNT(*) AS pocet FROM {db.KOS} WHERE udalost = ?",
            (cislo,)).fetchone()
        kolik = int(dict(pocet or {}).get("pocet") or 0)
        conn.execute(
            f"INSERT INTO playback ({vypis})"
            f" SELECT {vypis} FROM {db.KOS} WHERE udalost = ?", (cislo,))
        conn.execute(f"DELETE FROM {db.KOS} WHERE udalost = ?", (cislo,))
        conn.execute("DELETE FROM zapomenuti WHERE id = ?", (cislo,))
        zbyva = conn.execute(
            f"SELECT COUNT(*) AS pocet FROM {db.KOS}").fetchone()
        prazdno = int(dict(zbyva or {}).get("pocet") or 0) == 0
        conn.commit()

    jmeno = str(udaje.get("jmeno") or "")
    log.info("Vraceno z kose: %s, %s prehravani", jmeno, kolik)
    if prazdno:
        # Prepis souboru byl objednany kvuli mazani, ktere se prave
        # nekonalo. Na velke databazi je to minuta zamku - nema za co.
        db.set_setting(ODLOZENE_KLIC, "")
    return {"vraceno": kolik, "jmeno": jmeno}


def kos_ma_radky() -> bool:
    """Čeká v koši něco na vysypání?"""
    return bool(db.query_value(f"SELECT COUNT(*) FROM {db.KOS}", default=0))


def vysyp_kos() -> int:
    """Smaže, co je v koši. Vrací kolik řádků odešlo.

    Tohle je ta chvíle, kdy je zapomenutí opravdu zapomenutím. Volá se
    v noci, hned před přepsáním souboru - viz tasks.
    """
    with db.connect() as conn:
        pocet = conn.execute(
            f"SELECT COUNT(*) AS pocet FROM {db.KOS}").fetchone()
        kolik = int(dict(pocet or {}).get("pocet") or 0)
        conn.execute(f"DELETE FROM {db.KOS}")
        conn.execute(
            "UPDATE zapomenuti SET stav = ? WHERE stav = ?",
            (JEN_ZALOHA, V_KOSI))
        conn.commit()
    if kolik:
        log.info("Kos vysypan: %s prehravani je nadobro pryc", kolik)
    return kolik


def zapomenuti(zapomen_starsi_bez_zalohy: bool = True) -> list[dict[str, Any]]:
    """Co se zapomnělo a co se s tím ještě dá dělat.

    Tři stavy, a je mezi nimi rozdíl, který má člověk vidět:

    * **v koši** - jde vrátit jedním tlačítkem,
    * **jen v záloze** - koš se vysypal, ale soubor zálohy ještě je;
      vrátit se to dá, jen ručně, a stránka řekne jak,
    * **pryč** - záloha se mezitím smazala úklidem starších záloh.
      Nabízet návod k souboru, který neexistuje, by byl výsměch, tak
      takový řádek ze seznamu zmizí.
    """
    radky = db.query_all(
        "SELECT id, user_id, jmeno, relaci, zapomenuto_v, zaloha, stav"
        " FROM zapomenuti ORDER BY id DESC")
    vysledek: list[dict[str, Any]] = []
    zahodit: list[Any] = []
    for radek in radky:
        udaje = dict(radek)
        soubor = str(udaje.get("zaloha") or "")
        ma_zalohu = bool(soubor) and Path(soubor).exists()
        if str(udaje.get("stav")) != V_KOSI and not ma_zalohu:
            zahodit.append(udaje["id"])
            continue
        udaje["zaloha_soubor"] = Path(soubor).name if soubor else ""
        udaje["ma_zalohu"] = ma_zalohu
        udaje["v_kosi"] = str(udaje.get("stav")) == V_KOSI
        vysledek.append(udaje)

    if zahodit and zapomen_starsi_bez_zalohy:
        with db.connect() as conn:
            for cislo in zahodit:
                conn.execute("DELETE FROM zapomenuti WHERE id = ?", (cislo,))
            conn.commit()
    return vysledek


def odloz_preskladani() -> None:
    """Poznamená, že soubor databáze čeká na přepis (v noci)."""
    db.set_setting(ODLOZENE_KLIC, db.utcnow())
    log.info("Preskladani databaze odlozeno na nocni cas")


def preskladani_ceka() -> str:
    """Kdy někdo o odložený přepis požádal (UTC text), nebo prázdno."""
    return db.get_setting(ODLOZENE_KLIC, "") or ""


def dokonci_odlozene_preskladani() -> bool:
    """Udělá odložený přepis souboru. Vrací, jestli se povedl.

    Když ne, žádost zůstane - jen s novým časem, takže se zkusí zase
    příští noc, a ne každou minutu.
    """
    if db.preskladat():
        db.set_setting(ODLOZENE_KLIC, "")
        return True
    db.set_setting(ODLOZENE_KLIC, db.utcnow())
    return False


def pocet_relaci(user_id: str) -> int:
    """Kolik přehrávání o divákovi máme. Nula = není co zapomínat."""
    user_id = (user_id or "").strip()
    if not user_id:
        return 0
    return int(db.query_value(
        "SELECT COUNT(*) FROM playback WHERE user_id = ?", (user_id,),
        default=0) or 0)


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
