"""Synchronizace knihovny a technicka analyza souboru.

Dve ulohy, ktere se poustej rucne z Nastaveni nebo obcas samy:

1. `sync_library()` - stahne z Jellyfinu seznam uzivatelu, knihoven a polozek.
2. `run_tech_scan()` - doplni k polozkam technicke udaje (kodek, bitrate, ...).

U druhe ulohy si uzivatel v nastaveni vybira zdroj:
  * "jellyfin" - udaje, ktere uz zna Jellyfin. Rychle, funguje vzdy, mene presne.
  * "ffprobe"  - cteni souboru na disku. Presne, ale potrebuje pristup k souborum.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# `stats` a `formatting` kvuli dennimu snimku knihovny (zapis_snimek).
# Ani jeden si netahne scanner zpatky, takze kruh nevznikne - na rozdil
# od `tasks`, ktere se proto importuje az uvnitr funkce.
from . import db, formatting, languages, probe, stats
from .i18n import translate as _t
from .config import load_config
from .jellyfin import (JellyfinClient, JellyfinError, extract_streams,
                       extract_tech_from_item, video_range_of)

log = logging.getLogger("jellyscope.scanner")

# Druhy polozek, ktere si Jellyscope vede v knihovne.
#
# Jsou to prave ty, ktere odpovidaji jednomu souboru na disku - film
# a dil serialu. Serial ani rada zadny soubor nemaji, takze by u nich
# nebylo co merit: velikost, kodek, bitrate, nic.
#
# Tenhle seznam **musi sedet s tim, na co se ptame Jellyfinu**
# (IncludeItemTypes v jellyfin.py). Kdyz se do tabulky dostane polozka
# jineho druhu, synchronizace ji uz nikdy neuvidi - a `_mark_missing()`
# ji pri kazdem behu oznaci za zmizelou. V knihovne pak strasi
# "archivovany" serial, ktery nikdo nesmazal a ktery v Jellyfinu je.
SPRAVOVANE_TYPY = ("Movie", "Episode")

# Zamek, ktery zajisti, ze nebezi dva scany naraz. Bez nej by dve soucasne
# spustene analyzy zbytecne zatezovaly disk a prepisovaly si vysledky.
_scan_lock = asyncio.Lock()


def is_scan_running() -> bool:
    return _scan_lock.locked()


# ---------------------------------------------------------------------------
# Zastaveni bezici ulohy
# ---------------------------------------------------------------------------
#
# Uloha se **neprerusuje uprostred prace**. Nastavi se jen priznak a smycka
# si ho vsimne, az dodela rozdelanou polozku - teprve pak skonci.
#
# Proc takhle a ne task.cancel(): tvrde preruseni by mohlo prijit uprostred
# zapisu do databaze a nechat po sobe polovicni davku nebo neuzavrenou
# transakci. Takhle se skonci vzdycky na miste, kde je databaze v poradku.
# Uzivatel na to pocka nanejvys par sekund.
#
# Priznak je obycejna promenna modulu, stejne jako `_scan_lock` - obojí
# plati pro jeden bezici proces aplikace. Jellyscope bezi v jednom procesu
# (viz run.py), takze to staci; pri vice workerech by tohle prestalo platit
# stejne jako zamek sam.
_stop_requested = False


def request_stop() -> bool:
    """Poprosi bezici ulohu, aby skoncila. Vraci, jestli vubec neco bezi."""
    global _stop_requested
    if not is_scan_running():
        return False
    _stop_requested = True
    log.info("uloha dostala pokyn k zastaveni")
    return True


def stop_requested() -> bool:
    """Ceka bezici uloha na ukonceni?

    Podminka `is_scan_running()` tam neni navic. Priznak patri **te uloze,
    ktere byl nastaven** - kdyz skonci, uz nikomu nic nerika. Bez toho by
    Nastaveni po kazdem zastaveni navzdy hlasilo "zastavuji" a v testech
    by priznak pretekal z jedne ulohy do druhe.

    Uklidit ho pri odchodu z ulohy by znamenalo obalit celé telo funkce
    blokem try/finally kvuli jedne promenne. Takhle plati totez a je to
    videt na jednom miste.
    """
    return _stop_requested and is_scan_running()


# ---------------------------------------------------------------------------
# Prubeh bezici ulohy
# ---------------------------------------------------------------------------
#
# Synchronizace velke knihovny trva minuty a do ted o sobe nedavala vedet -
# uzivatel koukal na "uloha bezi" a nemel jak poznat, jestli je na zacatku
# nebo skoro hotova.
#
# Celkovy pocet zjistime predem: Jellyfin ho posila u kazde stranky jako
# `TotalRecordCount`, takze staci poprosit o stranku o velikosti nula. Pak
# uz jen pricitame, co je hotove.
#
# Drzime to v pameti, ne v databazi: je to udaj o **prave bezicim** procesu,
# ktery po skonceni nikoho nezajima. Zapisovat ho po kazde davce do databaze
# by znamenalo zapis navic bez uzitku.
# O kolik minut se pri rychle synchronizaci vratime pred posledni znamy
# titul. Pojistka proti tomu, aby na hranici nekdo nepropadl.
RECENT_OVERLAP_MINUTES = 5

_progress: dict[str, Any] = {"kind": None, "done": 0, "total": 0}


def progress() -> dict[str, Any]:
    """Kolik prace uz je hotovo. Prazdne, kdyz nic nebezi."""
    if not is_scan_running() or not _progress["kind"]:
        return {}
    hotovo, celkem = _progress["done"], _progress["total"]
    return {
        "kind": _progress["kind"],
        "done": hotovo,
        "total": celkem,
        # Procenta pocitame tady, at je sablona nemusi. A jen kdyz celkovy
        # pocet vubec zname - jinak by "0 %" lhalo o tom, ze nic nebezi.
        "percent": round(hotovo / celkem * 100) if celkem else None,
    }


def _start_progress(kind: str, total: int = 0) -> None:
    _progress.update(kind=kind, done=0, total=max(0, int(total or 0)))


def _add_progress(kolik: int) -> None:
    _progress["done"] += kolik


def _clear_progress() -> None:
    _progress.update(kind=None, done=0, total=0)


def _clear_stop() -> None:
    """Zahodi priznak. Vola se na zacatku kazde ulohy.

    Bez toho by pozdeji spustena uloha nasla priznak z te predchozi
    a hned by se ukoncila.
    """
    global _stop_requested
    _stop_requested = False


# ---------------------------------------------------------------------------
# Zaznam o prubehu scanu (aby bylo v UI videt, co se deje)
# ---------------------------------------------------------------------------

def start_task_log(kind: str) -> int:
    with db.connect() as conn:
        novy = conn.insert_returning_id(
            "INSERT INTO scan_log (kind, started_at, status) VALUES (?, ?, 'running')",
            (kind, db.utcnow()),
        )
        _proredit_zaznamy(conn, kind)
        return novy


def otisky() -> dict[str, str]:
    """Oba otisky naraz - knihovna i dobehle ulohy.

    Jeden dotaz, protoze se oba ptaji na tytez dve tabulky a chodi
    spolecne: sahá po nich **kazde volani /health**, na ktere se prohlizec
    pta kazdych deset vterin z kazde otevrene karty. Ve dvou dotazech to
    znamenalo dve spojeni do databaze misto jednoho - a rezie spojeni je
    tady vetsi nez prace samotna.

    `SELECT (poddotaz), (poddotaz)` bez FROM rozumi SQLite i PostgreSQL.
    """
    row = db.query_one(
        """
        SELECT (SELECT MAX(finished_at) FROM scan_log
                 WHERE kind IN ('library', 'recent'))      AS cas_knihovny,
               (SELECT COUNT(*) FROM items)                AS polozek,
               (SELECT MAX(id) FROM scan_log
                 WHERE finished_at IS NOT NULL)            AS uloh
        """
    ) or {}
    return {
        "library": f"{row.get('cas_knihovny') or ''}:{row.get('polozek') or 0}",
        "tasks": str(row.get("uloh") or 0),
    }


def library_version() -> str:
    """Otisk toho, jak knihovna vypada ted.

    Slouzi k jedine veci: prohlizec si ho zapamatuje pri nacteni stranky
    a kdyz se pozdeji zmeni, vi, ze ma znovu nacist pas nedavno pridanych.
    Bez toho by musel bud obnovovat naslepo porad dokola, nebo cekat, az
    stranku obnovi clovek sam - a nove pridany film by mu na uz otevrene
    strance nikdy nenaskocil.

    Skladame ho z posledni dokoncene ulohy a poctu titulu. Samotny cas
    nestaci: uloha, ktera nic nenasla, taky dobehne, a prekreslovat kvuli
    ni nema smysl.
    """
    return otisky()["library"]


def tasks_version() -> str:
    """Otisk toho, kolik uloh uz dobehlo.

    Stranka si ho zapamatuje pri nacteni a kdyz se zmeni, vi, ze nejaka
    uloha mezitim skoncila - a muze se obnovit.

    Proc ne "sleduj, jestli prave bezi uloha, a pockej az prestane":
    protoze rychla synchronizace nad malou knihovnou trva par vterin.
    Kdyz se stranka pta jednou za deset, cely beh se do mezery mezi dvema
    dotazy pohodlne vejde - uloha probehne, stranka o tom nikdy nevi
    a prouzek "uloha bezi" na ni zustane viset. Presne to se stalo.

    `MAX(id)` staci: cislo radku roste s kazdou dalsi ulohou, takze se
    nemusime spolehat na cas (dve ulohy muzou skoncit ve stejne vterine).
    """
    return otisky()["tasks"]


def finish_task_log(
    scan_id: int,
    status: str,
    total: int = 0,
    ok: int = 0,
    failed: int = 0,
    message: str = "",
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE scan_log
               SET finished_at = ?, status = ?, items_total = ?,
                   items_ok = ?, items_failed = ?, message = ?
             WHERE id = ?
            """,
            (db.utcnow(), status, total, ok, failed, message[:500], scan_id),
        )


# Kolik zaznamu o jednom druhu ulohy si necháváme.
#
# Rychla synchronizace bezi kazdych patnact minut, takze do tabulky
# pribyva pres devadesat radku denne - a nikdy se nic nemazalo. Za rok
# by jich bylo pres tricet tisic a kazdy dotaz na /health (prohlizec se
# pta kazdych deset vterin z kazde otevrene karty) by je musel projit.
#
# Tri sta staci: v Nastaveni se ukazuje posledni beh kazdeho druhu,
# zbytek je historie pro pripad, ze by se neco vysetrovalo.
ZAZNAMU_NA_DRUH = 300


def _proredit_zaznamy(conn: Any, kind: str) -> None:
    """Necha jen poslednich `ZAZNAMU_NA_DRUH` zaznamu daneho druhu.

    Hranici hledame dotazem, ne poctem: `DELETE ... LIMIT` umi SQLite,
    ale PostgreSQL ne, a tenhle tvar rozumi obema.
    """
    row = conn.execute(
        "SELECT id FROM scan_log WHERE kind = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
        (kind, ZAZNAMU_NA_DRUH - 1),
    ).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM scan_log WHERE kind = ? AND id < ?",
                 (kind, row["id"]))


def last_scan(kind: str) -> dict[str, Any] | None:
    return db.query_one(
        "SELECT * FROM scan_log WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
    )


# ---------------------------------------------------------------------------
# 1. Synchronizace knihovny
# ---------------------------------------------------------------------------

def _posledni_pridano() -> str | None:
    """Datum posledniho titulu, ktery uz v knihovne mame.

    Odtud se rychla synchronizace odrazi: co je novejsi, jeste neznáme.
    Vraci None, kdyz je knihovna prazdna - pak se vezmou proste nejnovejsi
    polozky, kolik se jich vejde do stropu.

    Odecitame par minut navic. Neni to pro parádu: kdyz Jellyfin prida vic
    souboru behem jedne vteriny, hranice by mohla nekterý z nich preskocit.
    Projit tentyz titul podruhe nic nestoji - zapisuje se pres ON CONFLICT.
    """
    nejnovejsi = db.query_value(
        "SELECT MAX(date_created) FROM items WHERE date_created IS NOT NULL")
    if not nejnovejsi:
        return None

    # Jellyfin posila cas s T a se zlomky sekund; my ho ukladame tak, jak
    # prisel. Pro porovnavani ho srovname do naseho tvaru.
    text = str(nejnovejsi).replace("T", " ")[:19]
    try:
        kdy = datetime.strptime(text, db.TIME_FORMAT)
    except ValueError:
        return None
    return (kdy - timedelta(minutes=RECENT_OVERLAP_MINUTES)).strftime(db.TIME_FORMAT)


async def refresh_item(item_id: str) -> dict[str, Any]:
    """Stahne z Jellyfinu znovu jednu jedinou polozku.

    K cemu to je: v Jellyfinu opravis metadata jednoho filmu (spatny rok,
    prehozeny nazev, chybejici jazyk stopy) a chces to hned videt i tady,
    aniz bys kvuli tomu poustel synchronizaci cele knihovny.

    Delame presne to, co synchronizace - jen pro jedno id: znovu se
    prectou udaje, prepisou se stopy a pri zdroji "ffprobe" se soubor
    rovnou premeri. Jellyfin se pritom jen **cte** (GET /Items).

    Zamek si nebereme: je to jeden dotaz a jeden zapis. Kdyz zrovna bezi
    velka synchronizace, prekazet si nebudou - tahle polozka se nanejvys
    zapise dvakrat po sobe stejne.
    """
    ulozena = db.query_one("SELECT * FROM items WHERE id = ?", (item_id,))
    if ulozena is None:
        return {"status": "error", "message": "Taková položka v databázi není."}

    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            nalezene = await client.items_by_ids([item_id])
    except JellyfinError as exc:
        return {"status": "error", "message": str(exc)}

    if not nalezene:
        # Polozka v Jellyfinu uz neni. Nemazeme ji - historie prehravani
        # na ni odkazuje -, jen to rekneme nahlas.
        return {"status": "error",
                "message": "Jellyfin tuhle položku už nezná. "
                           "Zmizela z knihovny, nebo se změnilo její ItemId."}

    item = nalezene[0]
    use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"
    tech = extract_tech_from_item(item) if use_jellyfin_tech else {}

    # Knihovnu bereme z ulozeneho zaznamu: Jellyfin ji v odpovedi
    # neposila a prepsat ji prazdnou by polozku vyradilo z prehledu.
    radek = _radek_polozky(item, ulozena["library_id"], tech, db.utcnow())
    await asyncio.to_thread(_write_items, [radek], not use_jellyfin_tech)

    if use_jellyfin_tech:
        stopy = extract_streams(item)
        if stopy:
            await asyncio.to_thread(save_streams, item_id, stopy)

    vysledek = {"status": "ok", "name": item.get("Name") or ulozena["name"]}

    if not use_jellyfin_tech:
        # Pri zdroji ffprobe se technicka data z Jellyfinu neberou, takze
        # by po obnoveni zustala stara. Zmerime rovnou - je to jeden soubor.
        tech_vysledek = await run_tech_scan(only_missing=False, item_ids=[item_id])
        vysledek["tech"] = tech_vysledek

    return vysledek


async def refresh_series(series_id: str) -> dict[str, Any]:
    """Stahne z Jellyfinu znovu vsechny dily jednoho serialu.

    K cemu to je: kdyz se v Jellyfinu opravi spatne urceny serial, zmeni
    se rovnou u vsech dilu - nazvy, cisla i plakaty. Obnovovat je po
    jednom by znamenalo klikat padesatkrat.

    Obrazky: u kazdeho dilu se porovna otisk (`ImageTags`) a kdyz se
    lisi, smaze se jeho obrazek z mezipameti - jinak by Jellyscope dal
    ukazoval ten spatny. Plakat samotneho serialu se zapomene vzdycky:
    polozku pro nej si nevedeme, takze neni s cim porovnavat, a je to
    presne ten obrazek, kvuli kteremu se sem clovek prisel podivat.

    Do Jellyfinu se jen cte (GET /Items).
    """
    dily = db.query_all(
        "SELECT id, library_id FROM items WHERE series_id = ?", (series_id,))
    if not dily:
        return {"status": "error", "message": "Takový seriál v databázi není."}

    knihovna = next((d["library_id"] for d in dily if d["library_id"]), None)
    ids = [str(d["id"]) for d in dily]
    use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"

    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            nalezene = await client.items_by_ids(ids)
    except JellyfinError as exc:
        return {"status": "error", "message": str(exc)}

    if not nalezene:
        return {"status": "error",
                "message": "Jellyfin žádný z dílů tohohle seriálu už nezná."}

    radky = []
    stopy: list[tuple[str, list[dict[str, Any]]]] = []
    now = db.utcnow()
    for item in nalezene:
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        radky.append(_radek_polozky(item, knihovna, tech, now))
        if use_jellyfin_tech:
            nalezene_stopy = extract_streams(item)
            if nalezene_stopy:
                stopy.append((str(item.get("Id")), nalezene_stopy))

    # _write_items si samo porovna otisky obrazku a ty zmenene zapomene.
    await asyncio.to_thread(_write_items, radky, not use_jellyfin_tech)
    for item_id, nalezene_stopy in stopy:
        await asyncio.to_thread(save_streams, item_id, nalezene_stopy)

    # Plakat serialu: polozku pro nej nemame, takze otisk neni s cim
    # porovnat - zapomeneme ho natvrdo. Priste se stahne znovu.
    _zapomen_obrazky([series_id])

    vysledek: dict[str, Any] = {
        "status": "ok",
        "dilu": len(nalezene),
        "name": next((i.get("SeriesName") for i in nalezene if i.get("SeriesName")),
                     ""),
    }
    if not use_jellyfin_tech:
        # Pri zdroji ffprobe by technicka data z Jellyfinu zustala stara.
        vysledek["tech"] = await run_tech_scan(
            only_missing=False, item_ids=[str(i.get("Id")) for i in nalezene])
    log.info("obnoven serial %s: %s dilu", series_id, len(nalezene))
    return vysledek


async def sync_recent(max_items: int = 2000) -> dict[str, Any]:
    """Rychla synchronizace: jen tituly, ktere v knihovne jeste nemame.

    Proc vedle plne synchronizace jeste tahle:

    Plna synchronizace projde **celou** knihovnu. U desitek tisic polozek
    to znamena stovky volani do Jellyfinu a nekolik minut prace, takze se
    poustí jednou za nekolik hodin - a nove pribyly film se v Jellyscope
    objevi klidne az za pul dne. Tahle uloha se diva jen na to, co pribylo,
    a da se poustet kazdych par minut.

    **Hranici urcuje posledni titul, ktery uz mame** (viz
    `_posledni_pridano()`), ne hodiny. Puvodne to bylo casove okno odvozene
    od intervalu ulohy - jenze pak zalezelo na tom, jestli uloha bezela
    podle planu. Kdyz aplikace stala pul dne, okno bylo kratsi nez vypadek
    a tituly z te doby propadly az do plne synchronizace. Takhle se nic
    nepreskoci, at uloha bezi jakkoli nepravidelne.

    **Prochazi se po knihovnach.** Jellyfin v odpovedi neposila, do ktere
    knihovny polozka patri, takze kdyz se ptame na vsechno najednou, nemame
    ji kam zaradit. Volani navic je par - knihoven byva jednotky.

    **_mark_missing() se tu NEVOLA.** Ta funkce oznaci za zmizele vsechno,
    co beh nevidel - a tenhle beh vidi jen hrstku nejnovejsich polozek.
    Zbytek knihovny by zmizel do archivu. Uklid smazanych titulu proto
    zustava u plne synchronizace, ktera opravdu projde vsechno.
    """
    if _scan_lock.locked():
        return {"status": "busy", "message": "Jiná úloha už běží."}

    od = _posledni_pridano()

    async with _scan_lock:
        _clear_stop()
        scan_id = start_task_log("recent")
        videno = 0
        nova_id: list[str] = []

        try:
            async with JellyfinClient(*db.jellyfin_connection()) as client:
                knihovny = await _sync_libraries(client)
                use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"
                _start_progress("recent", 0)

                for knihovna in knihovny:
                    polozky = await client.recent_items(
                        od, strop=max_items, parent_id=knihovna["id"])
                    videno += len(polozky)
                    nova_id.extend(await _uloz_nove_polozky(
                        polozky, knihovna["id"], use_jellyfin_tech, client))

        except JellyfinError as exc:
            _clear_progress()
            finish_task_log(scan_id, "error", message=str(exc))
            return {"status": "error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            _clear_progress()
            log.exception("rychla synchronizace selhala")
            finish_task_log(scan_id, "error", message=str(exc))
            return {"status": "error", "message": f"Neočekávaná chyba: {exc}"}

        _clear_progress()
        pridano = len(nova_id)
        if not od:
            zprava = _t("{n} nejnovějších titulů (knihovna byla prázdná)").format(n=pridano)
        elif pridano:
            zprava = _t("{n} nových titulů (zkontrolováno {celkem})").format(
                n=pridano, celkem=videno)
        else:
            # Nula je uplne bezny vysledek - uloha bezi kazdych par minut.
            # Musi se tak i tvarit, jinak clovek marne hleda, co pribylo.
            zprava = _t("Nic nového (zkontrolováno {celkem})").format(celkem=videno)
        finish_task_log(scan_id, "done", total=videno, ok=pridano, message=zprava)

    # Az za zamkem: technicka analyza si ho bere sama.
    #
    # Pri zdroji dat "ffprobe" se z Jellyfinu technicke udaje schvalne
    # neberou, takze novy titul zustal uplne prazdny - bez kontejneru,
    # rozliseni i velikosti - a cekal az na denni ulohu. Ted se rovnou
    # zmeri, a jen ten prave pridany: jde o par souboru, ne o celou
    # knihovnu.
    tech = {}
    if nova_id and db.get_setting("tech_source") == "ffprobe":
        tech = await run_tech_scan(only_missing=True, item_ids=nova_id)
        if tech.get("ok"):
            log.info("rychla synchronizace: zmereno %s novych souboru", tech["ok"])

    return {"status": "ok", "items": len(nova_id), "checked": videno,
            "since": od, "tech": tech}


async def sync_library() -> dict[str, Any]:
    """Stahne z Jellyfinu uzivatele, knihovny a vsechny polozky."""
    if _scan_lock.locked():
        return {"status": "busy", "message": "Jiná úloha už běží."}

    async with _scan_lock:
        _clear_stop()
        scan_id = start_task_log("library")
        config = load_config()
        started_at = db.utcnow()
        counts = {"users": 0, "libraries": 0, "items": 0}
        zastaveno = False

        try:
            async with JellyfinClient(*db.jellyfin_connection()) as client:
                counts["users"] = await _sync_users(client)
                libraries = await _sync_libraries(client)
                counts["libraries"] = len(libraries)

                use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"

                # Nejdriv se zeptame, kolik toho bude - jedno rychle volani
                # na knihovnu. Bez toho by ukazatel prubehu nemel k cemu
                # pocitat procenta.
                #
                # Kdyz se to nepovede, synchronizace bezi dal. Je to udaj
                # navic pro ukazatel prubehu, ne podminka prace - shodit
                # kvuli nemu cely scan by bylo neumerne.
                celkem = 0
                try:
                    for library in libraries:
                        celkem += await client.item_count(parent_id=library["id"])
                except Exception as exc:      # noqa: BLE001
                    log.warning("pocet polozek se nepodarilo zjistit: %s", exc)
                    celkem = 0
                _start_progress("library", celkem)

                for library in libraries:
                    counts["items"] += await _sync_items_of_library(
                        client, library, use_jellyfin_tech
                    )
                    if stop_requested():
                        zastaveno = True
                        break

                # Polozky, ktere jsme v tomhle behu nevideli, uz v Jellyfinu
                # nejsou. Nemazeme je - historie prehravani na ne odkazuje -
                # jen si je oznacime.
                #
                # Po zastaveni se tenhle krok MUSI vynechat. Zbytek knihovny
                # jsme totiz jeste nestihli projit, takze bychom "nevidene"
                # oznacili i tituly, ktere v Jellyfinu normalne jsou -
                # zmizely by z knihovny a skoncily v archivu. Nedokoncena
                # synchronizace radeji nezmeni nic, nez aby lhala.
                if not zastaveno:
                    _mark_missing(started_at)

                # Kolik zbyva mista - podle SAMOTNEHO Jellyfinu. Ptame se
                # tady, dokud je klient otevreny: snimek se pise az potom
                # a je synchronni. Kdyz to nevyjde (starsi Jellyfin ten
                # endpoint nema), jde se dal - je to udaj navic, ne
                # podminka prace.
                try:
                    zapamatuj_misto_z_jellyfinu(await client.storage())
                except Exception as exc:      # noqa: BLE001
                    log.info("misto z Jellyfinu se nepodarilo zjistit: %s", exc)

            # Ted uz zname uzivatele i polozky - srovname s nimi historii,
            # ktera se naimportovala driv, nez bylo pripojeni k Jellyfinu.
            # Takovy import ma v historii "?" misto jmen, nezna typ polozky
            # a jeho ItemId nemusi odpovidat zadnemu titulu v knihovne.
            #
            # Volame cele link_imported_history(), ne jen doplneni udaju:
            # ono se postara i o dohledani podle tmdb ID, ktere v dobe
            # importu nebylo jak udelat (Jellyfin jeste nebyl pripojeny).
            #
            # Import az tady, ne nahore: importers uz importuje scanner
            # a kruhovy import by aplikaci pri startu polozil.
            from . import importers
            try:
                await importers.link_imported_history()
            except Exception as exc:      # noqa: BLE001
                # Knihovna uz je stazena a ulozena. Kdyz se srovnani
                # historie nepovede, je to skoda - ne duvod hlasit celou
                # synchronizaci jako neuspesnou. Zkusi se pri te dalsi.
                log.warning("srovnani prevzate historie se nepovedlo: %s", exc)

        except JellyfinError as exc:
            _clear_progress()
            finish_task_log(scan_id, "error", message=str(exc))
            return {"status": "error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            log.exception("synchronizace knihovny selhala")
            _clear_progress()
            finish_task_log(scan_id, "error", message=str(exc))
            return {"status": "error", "message": f"Neočekávaná chyba: {exc}"}

        _clear_progress()
        popis = f"{counts['libraries']} knihoven, {counts['users']} uživatelů"
        if zastaveno:
            finish_task_log(
                scan_id, "stopped", total=counts["items"], ok=counts["items"],
                message=(f"Zastaveno na tvůj pokyn. Stihlo se {counts['items']} položek "
                         f"({popis}). Co se stáhlo, je uložené; zbytek zůstal beze změny."),
            )
            return {"status": "stopped", **counts}

        # Snimek knihovny se zapisuje az sem: po uspesne synchronizaci,
        # kdy je stav uplny. Po zastavene nebo spadle by zachytil pulku
        # knihovny a v grafu rustu by z toho byl propad, ktery se nestal.
        await asyncio.to_thread(zapis_snimek)

        finish_task_log(
            scan_id, "done", total=counts["items"], ok=counts["items"], message=popis,
        )
        return {"status": "ok", **counts}


# ---------------------------------------------------------------------------
# Kolik zbyva mista
# ---------------------------------------------------------------------------
#
# Tri zdroje, v tomhle poradi - a stranka pak rekne, ktery to byl:
#
#   1. RUCNE ZADANA kapacita. Kdyz knihovna lezi v cloudu nebo na sdilenem
#      ulozisti, nepozna velikost ani Jellyfin. Spravce ji vi a muze ji
#      napsat; jeho cislo prebiji vsechno ostatni.
#   2. JELLYFIN. Sedi u tech souboru, takze se ptame jeho.
#   3. DISK POD APLIKACI. Puvodni zpusob. Plati jen tehdy, kdyz data lezi
#      na temze stroji - jinak merime uplne cizi disk, a to bylo spatne.

KAPACITA_KLIC = "library_capacity_bytes"      # rucne zadana, v bajtech
JF_VOLNE_KLIC = "jellyfin_free_bytes"         # z posledni synchronizace
JF_CELKEM_KLIC = "jellyfin_total_bytes"
ZDROJ_KLIC = "volne_misto_zdroj"              # 'rucne' / 'jellyfin' / 'disk'


def _cislo(hodnota: Any) -> int | None:
    """Cislo z odpovedi Jellyfinu. None u vseho, co cislo neni."""
    try:
        cislo = int(float(hodnota))
    except (TypeError, ValueError):
        return None
    return cislo if cislo > 0 else None


def misto_z_jellyfinu(odpoved: Any, cesta: str = "") -> dict[str, int]:
    """Volne a celkove misto z odpovedi /System/Storage.

    Nazvy poli se mezi verzemi Jellyfinu lisily, takze se berou vsechny
    obvykle podoby. Kdyz nesedi nic, vratime prazdno - hadat cislo, ze
    ktereho se pak pocita "misto dojde za X dnu", by bylo horsi nez
    priznat, ze ho neznáme.

    `cesta` je slozka knihovny. Kdyz ji mezi slozkami najdeme, plati ta;
    jinak se vezme ta s nejmensim volnym mistem, protoze prvni dojde.
    """
    if not isinstance(odpoved, dict):
        return {}

    slozky: list[dict[str, Any]] = []
    for klic in ("Folders", "folders", "StorageFolders", "Items"):
        hodnota = odpoved.get(klic)
        if isinstance(hodnota, list):
            slozky = [s for s in hodnota if isinstance(s, dict)]
            break
    if not slozky:
        # Nekdy prijde rovnou jedna slozka, ne seznam.
        slozky = [odpoved]

    def pole(slozka: dict[str, Any], *jmena: str) -> int | None:
        for jmeno in jmena:
            cislo = _cislo(slozka.get(jmeno))
            if cislo is not None:
                return cislo
        return None

    zmerene = []
    for slozka in slozky:
        volne = pole(slozka, "FreeSpace", "freeSpace", "FreeSpaceBytes")
        celkem = pole(slozka, "TotalSpace", "totalSpace", "TotalSpaceBytes")
        if volne is None and celkem is None:
            continue
        zmerene.append({
            "cesta": str(slozka.get("Path") or slozka.get("path") or ""),
            "volne": volne, "celkem": celkem,
        })
    if not zmerene:
        return {}

    cesta = (cesta or "").replace("\\", "/").rstrip("/").lower()
    if cesta:
        for polozka in zmerene:
            jina = polozka["cesta"].replace("\\", "/").rstrip("/").lower()
            if jina and (cesta.startswith(jina) or jina.startswith(cesta)):
                return {k: v for k, v in polozka.items()
                        if k != "cesta" and v is not None}

    # Zadna slozka nesedi na knihovnu - bereme tu nejtesnejsi, protoze
    # misto dojde na ni.
    s_volnym = [p for p in zmerene if p["volne"] is not None]
    nejtesnejsi = min(s_volnym, key=lambda p: p["volne"]) if s_volnym else zmerene[0]
    return {k: v for k, v in nejtesnejsi.items() if k != "cesta" and v is not None}


def zapamatuj_misto_z_jellyfinu(odpoved: Any) -> dict[str, int]:
    """Ulozi, co Jellyfin rekl o mistu. Vola se pri synchronizaci.

    Uklada se do nastaveni, ne do snimku: snimek se pise az potom
    a `zapis_snimek()` je synchronni - na Jellyfin uz se odtud ptat
    nemuze.
    """
    radek = db.query_one(
        "SELECT path FROM items"
        " WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    cesta = str((radek or {}).get("path") or "")

    misto = misto_z_jellyfinu(odpoved, cesta)
    db.set_setting(JF_VOLNE_KLIC, str(misto.get("volne", "")))
    db.set_setting(JF_CELKEM_KLIC, str(misto.get("celkem", "")))
    return misto


def _volne_z_kapacity() -> int | None:
    """Volne misto z rucne zadane kapacity: kapacita minus velikost knihovny.

    Je to odhad a stranka to rika: predpoklada, ze na tom ulozisti nic
    jineho nelezi. Spravce ale zna cislo, ktere jinak nezna nikdo -
    u knihovny v cloudu je to jedina cesta, jak neco rict.
    """
    kapacita = db.get_int_setting(KAPACITA_KLIC, 0, 10 ** 18, 0)
    if kapacita <= 0:
        return None
    velikost = int(db.query_value(
        "SELECT COALESCE(SUM(COALESCE(size_bytes, 0)), 0) FROM items"
        " WHERE is_missing = 0", default=0) or 0)
    return max(0, kapacita - velikost)


def _volne_misto_knihovny() -> int | None:
    """Volne misto tam, kde knihovna lezi. None, kdyz ho nezname.

    Poradi zdroju viz komentar na zacatku teto sekce.
    """
    rucne = _volne_z_kapacity()
    if rucne is not None:
        db.set_setting(ZDROJ_KLIC, "rucne")
        return rucne

    z_jellyfinu = db.get_int_setting(JF_VOLNE_KLIC, 0, 10 ** 18, 0)
    if z_jellyfinu > 0:
        db.set_setting(ZDROJ_KLIC, "jellyfin")
        return z_jellyfinu

    # `tasks` az tady: samo si tahne scanner, takze nahore by z toho byl kruh.
    from . import tasks

    radek = db.query_one(
        "SELECT path FROM items"
        " WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    if not radek or not radek.get("path"):
        db.set_setting(ZDROJ_KLIC, "")
        return None

    try:
        mappings = json.loads(db.get_setting("path_mappings", "") or "[]")
        if not isinstance(mappings, list):
            mappings = []
    except ValueError:
        mappings = []

    cesta = Path(probe.apply_path_mappings(str(radek["path"]), mappings))
    # Slozka souboru, ne soubor sam - `disk_usage` chce adresar.
    volne = tasks.free_space(str(cesta.parent))
    db.set_setting(ZDROJ_KLIC, "disk" if volne is not None else "")
    return volne


def zapis_snimek() -> dict[str, Any] | None:
    """Ulozi dnesni stav knihovny - jeden radek na den.

    Tyz den se prepisuje: platí posledni znamy stav dne, ne prvni. Kdyz
    se behem dne neco doanalyzuje nebo prida, ma snimek ukazat to.

    Den se bere v ZONE APLIKACE, aby řádky sedely s tim, co je videt
    v grafech - ne v UTC, kde by se vecerni synchronizace zapsala uz
    na zitrek.
    """
    souhrn = db.query_one(
        f"""
        SELECT COUNT(*)                        AS polozek,
               SUM(CASE WHEN type = 'Movie'    THEN 1 ELSE 0 END) AS filmu,
               SUM(CASE WHEN type = 'Episode'  THEN 1 ELSE 0 END) AS epizod,
               COALESCE(SUM(COALESCE(size_bytes, 0)), 0)          AS velikost,
               SUM(CASE WHEN {stats.RESOLUTION_CASE} = '4K' THEN 1 ELSE 0 END) AS uhd,
               SUM(CASE WHEN {stats.ROZSAH_CASE} IN ('HDR', 'DOVI') THEN 1 ELSE 0 END) AS hdr,
               SUM(CASE WHEN tech_source IS NULL THEN 1 ELSE 0 END) AS bez_technik
          FROM items
         WHERE is_missing = 0
        """
    )
    if not souhrn or not souhrn.get("polozek"):
        return None          # prazdna knihovna: neni co zaznamenat

    den = datetime.now(formatting.zona()).strftime("%Y-%m-%d")
    radek = {
        "den": den,
        "polozek": int(souhrn["polozek"] or 0),
        "filmu": int(souhrn["filmu"] or 0),
        "epizod": int(souhrn["epizod"] or 0),
        "velikost": int(souhrn["velikost"] or 0),
        "uhd": int(souhrn["uhd"] or 0),
        "hdr": int(souhrn["hdr"] or 0),
        "bez_technik": int(souhrn["bez_technik"] or 0),
        "volne_misto": _volne_misto_knihovny(),
        "zapsano_v": db.utcnow(),
    }

    # Hodnoty se vyjmenovavaji, ne berou z `radek.values()`: spolehat se
    # na poradi klicu ve slovniku je past, ktera se pri pridani sloupce
    # projevi tim, ze se cisla prohodí - a nikdo si toho nevsimne.
    sloupce = ("den", "polozek", "filmu", "epizod", "velikost", "uhd", "hdr",
               "bez_technik", "volne_misto", "zapsano_v")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO library_snapshot
                (den, polozek, filmu, epizod, velikost, uhd, hdr,
                 bez_technik, volne_misto, zapsano_v)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (den) DO UPDATE SET
                polozek     = excluded.polozek,
                filmu       = excluded.filmu,
                epizod      = excluded.epizod,
                velikost    = excluded.velikost,
                uhd         = excluded.uhd,
                hdr         = excluded.hdr,
                bez_technik = excluded.bez_technik,
                volne_misto = excluded.volne_misto,
                zapsano_v   = excluded.zapsano_v
            """,
            tuple(radek[jmeno] for jmeno in sloupce),
        )
        conn.commit()

    log.info("snimek knihovny %s: %s polozek, %s bajtu",
             den, radek["polozek"], radek["velikost"])
    return radek


async def _sync_users(client: JellyfinClient) -> int:
    users = await client.users()
    now = db.utcnow()
    with db.connect() as conn:
        for user in users:
            policy = user.get("Policy") or {}
            conn.execute(
                """
                INSERT INTO users (id, name, is_administrator, is_disabled, last_activity, synced_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    is_administrator = excluded.is_administrator,
                    is_disabled = excluded.is_disabled,
                    last_activity = excluded.last_activity,
                    synced_at = excluded.synced_at
                """,
                (
                    user.get("Id"),
                    user.get("Name") or "?",
                    1 if policy.get("IsAdministrator") else 0,
                    1 if policy.get("IsDisabled") else 0,
                    user.get("LastActivityDate"),
                    now,
                ),
            )
    return len(users)


async def _sync_libraries(client: JellyfinClient) -> list[dict[str, Any]]:
    folders = await client.virtual_folders()
    now = db.utcnow()
    result = []

    with db.connect() as conn:
        for folder in folders:
            collection_type = folder.get("CollectionType")
            # Hudbu a fotky zatim vynechavame - statistiky jsou stavene
            # na video obsah.
            if collection_type not in (None, "movies", "tvshows", "homevideos", "mixed"):
                continue

            library = {
                "id": folder.get("ItemId"),
                "name": folder.get("Name") or "?",
                "collection_type": collection_type,
            }
            if not library["id"]:
                continue

            conn.execute(
                """
                INSERT INTO libraries (id, name, collection_type, paths, synced_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    collection_type = excluded.collection_type,
                    paths = excluded.paths,
                    synced_at = excluded.synced_at
                """,
                (
                    library["id"],
                    library["name"],
                    collection_type,
                    json.dumps(folder.get("Locations") or []),
                    now,
                ),
            )
            result.append(library)

    return result


def tmdb_id_of(item: dict[str, Any]) -> str | None:
    """Vytahne z polozky identifikator TMDB.

    Jellyfin je posila ve slovniku ProviderIds, jenze klice pise ruzne
    podle verze a typu polozky: "Tmdb", "TmdbId", u serialu obcas "tmdb".
    Porovnavame proto malymi pismeny, at na tom nezalezi.

    U epizod bereme id **serialu** (`SeriesProviderIds`), kdyz vlastni
    nema - epizoda sama v TMDB casto zadne id nema a bez toho by se
    slucovani u serialu nechytlo.

    POZOR: u epizody tohle id NEIDENTIFIKUJE dil, ale cely serial - vsechny
    dily Kancelare maji stejne. Kdo podle nej chce poznat jednu polozku,
    musi pouzit `identita_polozky()`.
    """
    for source in (item.get("ProviderIds"), item.get("SeriesProviderIds")):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if str(key).lower() in ("tmdb", "tmdbid") and value:
                return str(value)
    return None


def identita_polozky(item: dict[str, Any]) -> tuple[str, int, int] | None:
    """Podle ceho poznat, ze dva ruzne soubory jsou tentyz poradek.

    Slouzi ke slucovani: prekodujes soubor, Jellyfin zalozi novou polozku
    s novym ItemId a stara zmizi. Historii prehravani chceme prenest.

    U filmu na to staci tmdb_id. **U epizody ne** - a byla to skarada
    chyba: `tmdb_id_of()` u epizody vraci id SERIALU, takze vsech dvacet
    dilu Kancelare melo stejne. Slucovani pak povazovalo kazde dva dily
    za tentyz soubor a pri kazdem skenu slilo historii vsech dilu na
    jediny. Ve statistikach to vypadalo, ze divak videl jednu epizodu
    dvacetkrat - a "sledovanych titulu" bylo dvacetkrat min, nez melo.

    Dil proto identifikujeme trojici: serial + rada + cislo dilu.
    Kdyz cislo dilu neznáme, vracime None a neslucujeme radsi vubec -
    spatne slouceni se uz nijak nevrati zpatky.
    """
    tmdb = tmdb_id_of(item)
    if not tmdb:
        return None

    if str(item.get("Type") or "") != "Episode":
        # Film: cislo rady ani dilu nema, do porovnani dosadime -1.
        return (tmdb, -1, -1)

    rada = item.get("ParentIndexNumber")
    dil = item.get("IndexNumber")
    if rada is None or dil is None:
        return None
    try:
        return (tmdb, int(rada), int(dil))
    except (TypeError, ValueError):
        return None


def _radek_polozky(item: dict[str, Any], library_id: Any,
                   tech: dict[str, Any], now: str) -> tuple[Any, ...]:
    """Poskladá jeden řádek tabulky `items` z odpovědi Jellyfinu.

    Vlastní funkce, protože ji potřebují dvě různé úlohy - plná
    synchronizace i ta rychlá nad nově přidanými. Kdyby to byly dvě kopie,
    přidání sloupce by se jednou zapomnělo a jedna z cest by tiše
    ukládala míň.
    """
    return (
        item.get("Id"),
        item.get("Name") or "?",
        item.get("Type") or "?",
        library_id,
        item.get("SeriesId"),
        item.get("SeriesName"),
        item.get("SeasonName"),
        item.get("IndexNumber"),
        item.get("ParentIndexNumber"),
        item.get("ProductionYear"),
        item.get("RunTimeTicks"),
        item.get("DateCreated"),
        item.get("Path"),
        tmdb_id_of(item),
        # Zanry spojime svislitkem - carka se v nazvech zanru objevuje.
        "|".join(str(z).strip() for z in (item.get("Genres") or []) if str(z).strip())
            or None,
        tech.get("container"),
        tech.get("video_codec"),
        tech.get("audio_codec"),
        tech.get("audio_channels"),
        tech.get("width"),
        tech.get("height"),
        tech.get("bitrate"),
        tech.get("size_bytes"),
        tech.get("video_range"),
        # Co o rozsahu rika Jellyfin. Uklada se i v rezimu ffprobe, kde
        # se zbytek technickych udaju z Jellyfinu nebere: Dolby Vision
        # v Matrosce umi cist az ffmpeg 5, takze starsi ffprobe hlasi jen
        # "HDR" - a Jellyfin je pak jediny, kdo o DV vi.
        video_range_of(item),
        tech.get("audio_languages"),
        tech.get("subtitle_languages"),
        tech.get("default_audio_language"),
        "jellyfin" if tech else None,
        now if tech else None,
        # Otisk plakatu serialu, ke kteremu dil patri. U filmu prazdny.
        item.get("SeriesPrimaryImageTag"),
        # Otisk plakatu. Kdyz ho Jellyfin nehlasi, zustane prazdny -
        # obrazek se pak chova jako driv, jen bez rozpoznani zmeny.
        (item.get("ImageTags") or {}).get("Primary"),
        now,
    )


def _ktere_uz_zname(ids: list[str]) -> set[str]:
    """Ktera z techto ID uz v tabulce items jsou.

    Ptame se po davkach: SQL ma strop na pocet parametru v jednom dotazu
    (u SQLite jich byva 999) a rychla synchronizace muze pri prvnim behu
    prijit s tisici polozkami najednou.
    """
    znama: set[str] = set()
    for start in range(0, len(ids), 400):
        davka = ids[start:start + 400]
        if not davka:
            continue
        otazniky = ",".join("?" for _ in davka)
        for radek in db.query_all(
                f"SELECT id FROM items WHERE id IN ({otazniky})", tuple(davka)):
            znama.add(str(radek["id"]))
    return znama


async def _uloz_nove_polozky(polozky: list[dict[str, Any]], library_id: Any,
                             use_jellyfin_tech: bool,
                             client: "JellyfinClient | None" = None) -> list[str]:
    """Uloží položky jedné knihovny. Používá rychlá synchronizace.

    `library_id` se predava zvenci, protoze Jellyfin ho v odpovedi nenese.
    Drive se bralo z uz ulozeneho zaznamu - jenze u NOVEHO titulu zadny
    neexistuje, takze zustalo prazdne. A pri dalsim behu se prazdna
    hodnota jen zkopirovala, takze se to nikdy nespravilo samo.

    **Zapisuji se vsechny, ale vraci se jen id tech opravdu novych.**
    Hranice pro stahovani se schvalne posouva o par minut zpatky (viz
    `_posledni_pridano()`), takze se nejnovejsi uz znamy titul stahne
    znovu - a driv se zapocital jako novy. Vysledek pak nikdy neukazal
    nulu, i kdyz od minule nic nepribylo. Prepsat uz znamy titul ma pritom
    smysl: prave tim se opravi zaznam, ktery Jellyfin pri prvnim pruchodu
    jeste nemel zaradeny do knihovny.
    """
    if not polozky:
        return []

    now = db.utcnow()
    znama = _ktere_uz_zname([str(i["Id"]) for i in polozky if i.get("Id")])
    nova_id = [str(i["Id"]) for i in polozky
               if i.get("Id") and str(i["Id"]) not in znama]

    radky: list[tuple[Any, ...]] = []
    stopy: list[tuple[str, list[dict[str, Any]]]] = []
    tmdb_dvojice: list[tuple[tuple[str, int, int], str]] = []

    for item in polozky:
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        if use_jellyfin_tech:
            streams = extract_streams(item)
            if streams:
                stopy.append((item.get("Id"), streams))

        identita = identita_polozky(item)
        if identita and item.get("Id"):
            tmdb_dvojice.append((identita, str(item["Id"])))

        radky.append(_radek_polozky(item, library_id, tech, now))
        _add_progress(1)

    # Rychla synchronizace vidi jen novinky, takze podle `synced_at` nepozna,
    # co v knihovne porad je - vsechno ostatni ma razitko z minuleho behu.
    # Kdyz tedy slucovani na nejakou ulozenou polozku ukazuje, zeptame se
    # Jellyfinu primo, jestli tam jeste je. Kdyz ano, jde o druhou kopii
    # tehoz dilu a slucovat se nesmi.
    chranena: set[str] = set()
    if client is not None:
        kandidati = await asyncio.to_thread(
            _stare_ke_slouceni, tmdb_dvojice,
            {str(i["Id"]) for i in polozky if i.get("Id")},
        )
        if kandidati:
            zive = await client.items_by_ids(list(kandidati))
            chranena = {str(i["Id"]) for i in zive if i.get("Id")}

    await asyncio.to_thread(_write_batch, radky, stopy, tmdb_dvojice,
                            not use_jellyfin_tech, None, chranena)
    return nova_id


async def _sync_items_of_library(
    client: JellyfinClient,
    library: dict[str, Any],
    use_jellyfin_tech: bool,
) -> int:
    """Nacte a ulozi vsechny polozky jedne knihovny."""
    now = db.utcnow()
    count = 0
    batch: list[tuple[Any, ...]] = []
    stream_batch: list[tuple[str, list[dict[str, Any]]]] = []
    # Dvojice (tmdb_id, nove ItemId) pro slucovani prekodovanych souboru.
    seen_tmdb: list[tuple[tuple[str, int, int], str]] = []

    async for item in client.iter_items(parent_id=library["id"]):
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        if use_jellyfin_tech:
            streams = extract_streams(item)
            if streams:
                stream_batch.append((item.get("Id"), streams))

        identita = identita_polozky(item)
        if identita and item.get("Id"):
            seen_tmdb.append((identita, str(item["Id"])))

        batch.append(_radek_polozky(item, library["id"], tech, now))
        count += 1

        # Zapisujeme po davkach. Jeden INSERT na polozku by u velke knihovny
        # znamenal desetitisice samostatnych transakci a scan by se vlekl.
        if len(batch) >= 200:
            # Zapis jde do vlakna. Bez toho by synchronizace velke knihovny
            # drzela smycku udalosti a cely web by po dobu scanu neodpovidal.
            await asyncio.to_thread(
                _write_batch, list(batch), list(stream_batch),
                list(seen_tmdb), not use_jellyfin_tech, now
            )
            _add_progress(len(batch))
            batch.clear()
            stream_batch.clear()
            seen_tmdb.clear()

        # Zastavujeme az tady - rozdelana polozka je hotova a zapsana.
        # U velke knihovny je tohle jedine misto, kde se da skoncit vcas;
        # kontrola mezi knihovnami by u jedne velke znamenala cekat
        # klidne desitky minut.
        if stop_requested():
            break

    if batch or stream_batch or seen_tmdb:
        await asyncio.to_thread(
            _write_batch, list(batch), list(stream_batch),
            list(seen_tmdb), not use_jellyfin_tech, now
        )
        _add_progress(len(batch))

    return count


def _stare_ke_slouceni(
    pairs: list[tuple[tuple[str, int, int], str]],
    chranena: set[str],
    videno_od: str | None = None,
) -> dict[str, str]:
    """Ktere ulozene polozky by slucovani zabralo. Vraci {stare id: nove id}.

    Jen ctenim - slouzi k tomu, aby se dalo predem overit, jestli ta
    "stara" polozka v Jellyfinu opravdu uz neni. Viz `_merge_by_tmdb`.
    """
    if not pairs:
        return {}

    nalezene: dict[str, str] = {}
    with db.connect() as conn:
        for (tmdb, rada, dil), new_id in pairs:
            podminka = ""
            parametry: list[Any] = [tmdb, rada, dil, new_id]
            if videno_od:
                # Polozku, kterou Jellyfin v tomhle behu poslal, slucovat
                # nesmime - ta zjevne existuje dal.
                podminka = " AND (i.synced_at IS NULL OR i.synced_at < ?)"
                parametry.append(videno_od)

            row = conn.execute(
                f"""
                SELECT i.id
                  FROM items i
             LEFT JOIN playback p ON p.item_id = i.id
                 WHERE i.tmdb_id = ?
                   AND COALESCE(i.parent_index_number, -1) = ?
                   AND COALESCE(i.index_number, -1) = ?
                   AND i.id != ?{podminka}
              GROUP BY i.id
              ORDER BY COALESCE(SUM(p.watched_seconds), 0) DESC
                 LIMIT 1
                """,
                tuple(parametry),
            ).fetchone()
            if row is not None and str(row["id"]) not in chranena:
                nalezene[str(row["id"])] = new_id
    return nalezene


def _merge_by_tmdb(pairs: list[tuple[tuple[str, int, int], str]],
                   chranena: set[str] | None = None,
                   videno_od: str | None = None) -> int:
    """Preveze historii ze stare polozky na novou, kdyz jde o tentyz poradek.

    Situace, kterou to resi: prekodujes film do HEVC a nahradis puvodni
    soubor. Jellyfin to nepozna jako zmenu - zalozi **novou polozku
    s novym ItemId** a stara zmizi. Bez tehle funkce by se historie
    prehravani rozpadla na dva tituly, z nichz jeden by skoncil v archivu.

    **Slucuje se jen tehdy, kdyz ta stara polozka opravdu zmizela.**
    Tim se lisi vymena souboru od druhe kopie tehoz dilu: kdyz mas
    epizodu v knihovne dvakrat (jina kvalita, zbyla stara verze), jsou
    v Jellyfinu obe - a slouceni by jednu z nich smazalo. Pri dalsim
    scanu by se vratila a smazala tu druhou; polozky by se stridaly
    a odkazy na ne prestavaly platit. Proto:
      * `chranena` = id, o kterych vime, ze v Jellyfinu jsou (prave
        zapisovana davka, u rychle synchronizace i overeni doptanim),
      * `videno_od` = zacatek behu; polozka se `synced_at` z tohohle behu
        prisla z Jellyfinu prave ted, takze se take nesluci.

    Poznavacim znamenim je `identita_polozky()`: u filmu tmdb_id, u epizody
    tmdb_id serialu spolu s cislem rady a dilu. Samotne tmdb_id na epizodu
    nestaci - vsechny dily serialu ho maji stejne, takze by se slucovaly
    navzajem. Viz komentar u `identita_polozky()`.

    Postup je opatrny a v tomhle poradi:
      1. stopy stare polozky smazeme - k novemu souboru stejne nepatri
         a cizi klic by nam nedovolil zmenit id, dokud existuji,
      2. historii prehravani prepiseme na nove id,
      3. teprve pak zmenime id polozky,
      4. a technicka data smazeme - popisuji stary soubor, ktery uz
         neexistuje. Datum pridani a zbytek zustavaji.

    Vraci pocet slouceni.
    """
    # Stara polozka = stejna identita, ale jine ItemId. Kdyz jich je vic
    # (napr. film byl v knihovne dvakrat), bere se ta, ktera ma odsledovaneho
    # nejvic - o tu prijit nechceme. Vyber resi `_stare_ke_slouceni()`.
    dvojice = _stare_ke_slouceni(pairs, chranena or set(), videno_od)
    if not dvojice:
        return 0

    merged = 0
    with db.connect() as conn:
        for old_id, new_id in dvojice.items():
            # Kdyz uz nova polozka v databazi je, nesmime na ni stare id
            # prepsat - vzniklo by duplicitni id. V tom pripade jen
            # preneseme historii a starou polozku zahodime.
            exists = conn.execute(
                "SELECT 1 FROM items WHERE id = ?", (new_id,)
            ).fetchone()

            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (old_id,))
            conn.execute(
                "UPDATE playback SET item_id = ? WHERE item_id = ?", (new_id, old_id)
            )

            if exists:
                conn.execute("DELETE FROM items WHERE id = ?", (old_id,))
            else:
                conn.execute(
                    "UPDATE items SET id = ?, is_missing = 0 WHERE id = ?",
                    (new_id, old_id),
                )
                # A technicka data pryc. Prejmenovanim polozky by jinak
                # zustala viset na novem souboru - jenze popisuji ten
                # STARY, ktery uz na disku neni. Slucujeme prave proto,
                # ze se soubor vymenil.
                #
                # Nejhorsi na tom bylo `tech_source`: analyza souboru
                # bere jen polozky, ktere zadna data nemaji, takze tuhle
                # navzdy preskakovala. Na detailu pak svitilo rozliseni
                # puvodniho souboru (nebo prazdno) a pomohlo jen rucni
                # "Nacist metadata znovu".
                conn.execute(
                    """
                    UPDATE items
                       SET container = NULL, video_codec = NULL, audio_codec = NULL,
                           audio_channels = NULL, width = NULL, height = NULL,
                           bitrate = NULL, size_bytes = NULL, video_range = NULL,
                           video_range_reported = NULL,
                           audio_languages = NULL, subtitle_languages = NULL,
                           default_audio_language = NULL,
                           audio_from_name = NULL, subtitle_from_name = NULL,
                           tech_source = NULL, tech_updated_at = NULL, tech_error = NULL
                     WHERE id = ?
                    """,
                    (new_id,),
                )

            log.info("slouceno: %s -> %s", old_id, new_id)
            merged += 1

    return merged


def _write_batch(
    items: list[tuple[Any, ...]],
    streams: list[tuple[str, list[dict[str, Any]]]],
    tmdb_pairs: list[tuple[tuple[str, int, int], str]],
    keep_existing_tech: bool,
    videno_od: str | None = None,
    chranena: set[str] | None = None,
) -> None:
    """Zapis jedne davky. Bezi ve vlakne, ne na smycce udalosti."""
    # Slucovani musi byt PRED zapisem polozek. Kdyby se novy zaznam vlozil
    # driv, mel by prazdnou historii a stara polozka by uz jen cekala na
    # oznaceni "chybi" - presne to, cemu se chceme vyhnout.
    #
    # Polozky z teto davky jsou zjevne zive - Jellyfin je poslal pred
    # chvili -, takze se do slucovani davaji jako chranene.
    zive = set(chranena or set())
    zive.update(str(radek[0]) for radek in items if radek[0])
    _merge_by_tmdb(tmdb_pairs, chranena=zive, videno_od=videno_od)

    if items:
        _write_items(items, keep_existing_tech=keep_existing_tech)
    # Stopy az po polozkach - odkazuji se na ne cizim klicem, takze
    # polozka musi v databazi uz existovat.
    for item_id, item_streams in streams:
        save_streams(item_id, item_streams)


def _write_items(rows: list[tuple[Any, ...]], keep_existing_tech: bool) -> None:
    """Zapise davku polozek.

    `keep_existing_tech` resi jemny, ale dulezity detail: kdyz je zdrojem
    technickych dat ffprobe, nesmi nam bezna synchronizace knihovny prepsat
    drive namerene hodnoty prazdnymi. COALESCE(?, sloupec) znamena
    "vezmi novou hodnotu, a kdyz je NULL, nech puvodni".
    """
    if keep_existing_tech:
        tech_update = """
                    container       = COALESCE(excluded.container, items.container),
                    video_codec     = COALESCE(excluded.video_codec, items.video_codec),
                    audio_codec     = COALESCE(excluded.audio_codec, items.audio_codec),
                    audio_channels  = COALESCE(excluded.audio_channels, items.audio_channels),
                    width           = COALESCE(excluded.width, items.width),
                    height          = COALESCE(excluded.height, items.height),
                    bitrate         = COALESCE(excluded.bitrate, items.bitrate),
                    size_bytes      = COALESCE(excluded.size_bytes, items.size_bytes),
                    video_range     = COALESCE(excluded.video_range, items.video_range),
                    video_range_reported = excluded.video_range_reported,
                    audio_languages = COALESCE(excluded.audio_languages, items.audio_languages),
                    subtitle_languages = COALESCE(excluded.subtitle_languages, items.subtitle_languages),
                    default_audio_language = COALESCE(excluded.default_audio_language,
                                                      items.default_audio_language),
                    tech_source     = COALESCE(excluded.tech_source, items.tech_source),
                    tech_updated_at = COALESCE(excluded.tech_updated_at, items.tech_updated_at),
        """
    else:
        tech_update = """
                    container       = excluded.container,
                    video_codec     = excluded.video_codec,
                    audio_codec     = excluded.audio_codec,
                    audio_channels  = excluded.audio_channels,
                    width           = excluded.width,
                    height          = excluded.height,
                    bitrate         = excluded.bitrate,
                    size_bytes      = excluded.size_bytes,
                    video_range     = excluded.video_range,
                    video_range_reported = excluded.video_range_reported,
                    audio_languages = excluded.audio_languages,
                    subtitle_languages = excluded.subtitle_languages,
                    default_audio_language = excluded.default_audio_language,
                    tech_source     = excluded.tech_source,
                    tech_updated_at = excluded.tech_updated_at,
                    tech_error      = NULL,
        """

    sql = f"""
        INSERT INTO items (
            id, name, type, library_id, series_id, series_name, season_name,
            index_number, parent_index_number, production_year, runtime_ticks,
            date_created, path, tmdb_id, genres, container, video_codec, audio_codec,
            audio_channels, width, height, bitrate, size_bytes, video_range,
            video_range_reported,
            audio_languages, subtitle_languages, default_audio_language,
            tech_source, tech_updated_at, series_image_tag, image_tag, synced_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            library_id = excluded.library_id,
            series_id = excluded.series_id,
            series_name = excluded.series_name,
            genres = excluded.genres,
            season_name = excluded.season_name,
            index_number = excluded.index_number,
            parent_index_number = excluded.parent_index_number,
            production_year = excluded.production_year,
            runtime_ticks = excluded.runtime_ticks,
            date_created = excluded.date_created,
            path = excluded.path,
            -- COALESCE: kdyz Jellyfin tmdb_id zrovna neposle (nedohledane
            -- metadata), nesmime uz ulozene prepsat na NULL - prisli bychom
            -- o jedinou vec, podle ktere umime polozky slucovat.
            tmdb_id = COALESCE(excluded.tmdb_id, items.tmdb_id),
            {tech_update}
            image_tag = excluded.image_tag,
            series_image_tag = excluded.series_image_tag,
            synced_at = excluded.synced_at,
            is_missing = 0
    """

    # Ktere polozky menily obrazek? Musime se zeptat PRED zapisem - potom
    # uz je v databazi novy otisk a rozdil by nebyl poznat.
    zmenene = _polozky_s_jinym_obrazkem(rows)

    with db.connect() as conn:
        conn.executemany(sql, rows)

    # Az po zapisu: kdyby zapis selhal, nemazali bychom obrazky k nicemu.
    if zmenene:
        _zapomen_obrazky(zmenene)


# V radku polozky (viz _radek_polozky) je otisk predposledni a id prvni.
_SLOUPEC_ID = 0
_SLOUPEC_SERIAL = 4
_SLOUPEC_OTISK_SERIALU = -3
_SLOUPEC_OTISK = -2


def _polozky_s_jinym_obrazkem(rows: list[tuple[Any, ...]]) -> list[str]:
    """Ktere obrazky uz neplati - id polozek i id serialu.

    Otisk (`ImageTags.Primary`) hlasi Jellyfin u kazde polozky. Kdyz se
    zmeni, znamena to, ze plakat je jiny - typicky proto, ze nekdo
    v Jellyfinu opravil spatne urcenou polozku.

    U serialu je to o krok slozitejsi: polozku pro nej nemame, plakat se
    ale stahuje pod jeho id. Jellyfin proto u kazde epizody hlasi jeste
    `SeriesPrimaryImageTag` - otisk plakatu jejiho serialu. Kdyz se
    zmeni, vracime **id serialu**, at se zahodi i jeho obrazek.
    """
    podle_id = {str(radek[_SLOUPEC_ID]): radek[_SLOUPEC_OTISK]
                for radek in rows if radek[_SLOUPEC_ID]}
    serialy = {str(radek[_SLOUPEC_SERIAL]): radek[_SLOUPEC_OTISK_SERIALU]
               for radek in rows if radek[_SLOUPEC_SERIAL]}
    if not podle_id:
        return []

    zmenene: list[str] = []
    ids = list(podle_id)
    with db.connect() as conn:
        for zacatek in range(0, len(ids), 300):
            davka = ids[zacatek:zacatek + 300]
            otazniky = ",".join("?" for _ in davka)
            for radek in conn.execute(
                    f"SELECT id, image_tag FROM items WHERE id IN ({otazniky})",
                    tuple(davka)).fetchall():
                stary_otisk = radek["image_tag"]
                novy_otisk = podle_id.get(str(radek["id"]))
                if stary_otisk and novy_otisk and stary_otisk != novy_otisk:
                    zmenene.append(str(radek["id"]))

        # Serialy: staci se zeptat jedne epizody z kazdeho - otisk plakatu
        # serialu maji vsechny stejny.
        for serial, novy_otisk in serialy.items():
            if not novy_otisk:
                continue
            radek = conn.execute(
                "SELECT series_image_tag FROM items "
                " WHERE series_id = ? AND series_image_tag IS NOT NULL LIMIT 1",
                (serial,)).fetchone()
            stary_otisk = radek["series_image_tag"] if radek else None
            # Zadny ulozeny otisk znamena bud serial, ktery jsme jeste
            # nevideli (a v mezipameti tedy nic nemuze byt), nebo
            # databazi z doby pred timhle sloupcem - a tam lezi plakat,
            # o kterem nevime, jestli jeste plati. Presne kvuli nemu
            # sloupec vznikl, takze ho jednou zahodime; priste uz je
            # co porovnavat.
            if stary_otisk != novy_otisk:
                zmenene.append(serial)
    return zmenene


def _zapomen_obrazky(ids: list[str]) -> int:
    """Smaze obrazky techto polozek z mezipameti.

    Mezipamet je slozka `data/imagecache` a jmeno souboru zacina id
    polozky. Mazeme vsechny druhy i velikosti - kdyz se zmenil plakat,
    nema smysl verit ani nahledu na pozadi.

    Neni to jen uklid: dokud tam soubor lezi, servirujeme stary obrazek
    a uzivatel nema jak se dobrat noveho.
    """
    from . import config

    slozka = config.load_config().database_path.parent / "imagecache"
    if not slozka.is_dir():
        return 0

    smazano = 0
    for item_id in ids:
        for soubor in slozka.glob(f"{item_id}-*"):
            try:
                soubor.unlink()
                smazano += 1
            except OSError:  # noqa: PERF203 - jeden zamceny soubor nesmi zastavit zbytek
                continue
    if smazano:
        log.info("zapomenuto %s obrazku, ktere uz v Jellyfinu neplati", smazano)
    return smazano


def save_streams(item_id: str, streams: list[dict[str, Any]]) -> None:
    """Prepise stopy jedne polozky.

    Nejdriv smazat, pak vlozit. Kdybychom jen vkladali, zustaly by po
    prekodovani souboru viset stopy, ktere uz v nem nejsou.
    """
    if not streams:
        return

    with db.connect() as conn:
        conn.execute("DELETE FROM item_streams WHERE item_id = ?", (item_id,))
        # Cerstve zmereny soubor prebiji odhad z nazvu: kdyby zustal,
        # pricital by se do souhrnu i potom, co uz je jazyk znamy.
        conn.execute("UPDATE items SET audio_from_name = NULL, "
                     "subtitle_from_name = NULL WHERE id = ?", (item_id,))
        # ON CONFLICT ... DO UPDATE misto SQLite konstrukce INSERT OR REPLACE -
        # rozumi mu SQLite i PostgreSQL. Navic je z nej videt, co presne se
        # pri konfliktu stane, coz u "REPLACE" nikdy nebylo jasne.
        conn.executemany(
            """
            INSERT INTO item_streams (
                item_id, stream_index, type, codec, language, language_source,
                title, channels, channel_layout, width, height, bitrate,
                is_default, is_forced, is_external
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (item_id, stream_index) DO UPDATE SET
                type = excluded.type,
                codec = excluded.codec,
                language = excluded.language,
                language_source = excluded.language_source,
                title = excluded.title,
                channels = excluded.channels,
                channel_layout = excluded.channel_layout,
                width = excluded.width,
                height = excluded.height,
                bitrate = excluded.bitrate,
                is_default = excluded.is_default,
                is_forced = excluded.is_forced,
                is_external = excluded.is_external
            """,
            [
                (
                    item_id, s.get("stream_index"), s.get("type"), s.get("codec"),
                    s.get("language"), s.get("language_source"), s.get("title"),
                    s.get("channels"), s.get("channel_layout"), s.get("width"),
                    s.get("height"), s.get("bitrate"), s.get("is_default", 0),
                    s.get("is_forced", 0), s.get("is_external", 0),
                )
                for s in streams
            ],
        )


def _mark_missing(started_at: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET is_missing = 1 WHERE synced_at < ?", (started_at,)
        )


def slouc_archiv_do_zivych() -> int:
    """Dil z archivu, ktery v knihovne existuje znovu, pripoji k tomu zivemu.

    Co se stalo: soubor dilu se v Jellyfinu nahradil (jina kvalita, novy
    rip, presun mezi slozkami). Jellyfin nezalozil zmenu, ale **novou
    polozku s novym ItemId** - a ta stara pri dalsi synchronizaci spadla
    do archivu. V detailu serialu pak stalo "v archivu je navic 3 dilu",
    prestoze ty dily v knihovne normalne jsou.

    Proc to nezachytilo slucovani pri synchronizaci (`_merge_by_tmdb`):
    to porovnava ulozene `tmdb_id`, a starsi zaznamy (typicky z importu
    historie nebo z doby pred timhle sloupcem) zadne nemaji. Bez nej
    dvojici nema podle ceho najit.

    Tady se identita bere odjinud - **serial plus cislo rady a dilu**.
    Serial se pozna podle id, a kdyz to nesedi, podle jmena: kdyz nekdo
    v Jellyfinu smaze a znovu prida cely adresar serialu, dostanou dily
    nova series_id a podle nich by se stara polozka nenasla nikdy.
    Slucuje se jen archivovana polozka do zive, nikdy naopak: archivovana
    v Jellyfinu prokazatelne neni, takze o nic neprijdeme. Kdyz je zivych
    kopii vic (4K i 1080p vedle sebe), historie pripadne te odsledovanejsi.

    Historie prehravani se prepise na novou polozku - presne to je duvod,
    proc se to dela: aby "kolikrat jsem to videl" neskoncilo na zaznamu,
    ktery uz nikdo neuvidi.

    Vraci pocet slouceni.
    """
    # Dva dotazy a parovani v Pythonu, ne jeden vnoreny dotaz: v SQL by
    # to byl poddotaz s vlastnim GROUP BY uvnitr, a takovy se cte hur,
    # nez kolik usetri.
    #
    # Zive dily si vedeme pod dvema klici zaroven:
    #   podle id serialu   - normalni pripad, vymenil se soubor jednoho dilu,
    #   podle nazvu serialu - kdyz Jellyfin zalozil cely serial znovu
    #                         (smazany a znovu pridany adresar), takze dily
    #                         maji jine series_id a podle nej by se nenasly.
    zive: dict[tuple[str, int, int], tuple[str, float, Any]] = {}
    podle_nazvu: dict[tuple[str, int, int], tuple[str, float, Any]] = {}
    for radek in db.query_all(
        """
        SELECT i.id, i.series_id, i.series_name, i.tmdb_id,
               i.parent_index_number, i.index_number,
               COALESCE(SUM(p.watched_seconds), 0) AS videno
          FROM items i
     LEFT JOIN playback p ON p.item_id = i.id
         WHERE i.is_missing = 0
           AND i.series_id IS NOT NULL
           AND i.parent_index_number IS NOT NULL
           AND i.index_number IS NOT NULL
      GROUP BY i.id, i.series_id, i.series_name, i.tmdb_id,
               i.parent_index_number, i.index_number
        """
    ):
        rada, dil = int(radek["parent_index_number"]), int(radek["index_number"])
        videno = float(radek["videno"] or 0)
        # Kdyz je zivych kopii vic (4K i 1080p vedle sebe), bere se ta
        # odsledovanejsi - o tu prijit nechceme.
        for kam, klic in (
            (zive, (str(radek["series_id"]), rada, dil)),
            (podle_nazvu, (str(radek["series_name"] or "").strip().lower(), rada, dil)),
        ):
            if not klic[0]:
                continue
            if klic not in kam or videno > kam[klic][1]:
                kam[klic] = (str(radek["id"]), videno, radek["tmdb_id"])

    # Serialy, ktere pod svym id porad zijou. Jejich archivovane dily
    # se podle jmena parovat NESMI: kdyz serial v Jellyfinu je a jeden
    # jeho dil chybi, pak ten dil opravdu chybi - a shoda jmena by
    # historii poslala k nekomu jinemu (dva serialy se muzou jmenovat
    # stejne).
    zive_serialy = {
        str(radek["series_id"]) for radek in db.query_all(
            "SELECT DISTINCT series_id FROM items "
            " WHERE is_missing = 0 AND series_id IS NOT NULL")
    }

    pary: list[tuple[str, str]] = []
    for radek in db.query_all(
        """
        SELECT id, series_id, series_name, tmdb_id,
               parent_index_number, index_number
          FROM items
         WHERE is_missing = 1
           AND parent_index_number IS NOT NULL
           AND index_number IS NOT NULL
        """
    ):
        rada, dil = int(radek["parent_index_number"]), int(radek["index_number"])
        serial = str(radek["series_id"] or "")
        # Nejdriv podle id serialu; teprve kdyz serial pod tim id uz vubec
        # neexistuje, zkusime jmeno. Poradi je dulezite: id je jistota,
        # jmeno je jenom shoda.
        nalezeny = zive.get((serial, rada, dil))
        if not nalezeny and serial not in zive_serialy:
            kandidat = podle_nazvu.get(
                (str(radek["series_name"] or "").strip().lower(), rada, dil))
            # Kdyz obe strany znaji tmdb_id a lisi se, je to prokazatelne
            # jiny serial - tim se odlisi "Kancelar (US)" od "Kancelar (UK)".
            # Kdyz ho jedna z nich nema, rozhoduje jmeno a cislo dilu;
            # nic lepsiho o tech zaznamech nevime.
            stare_tmdb, nove_tmdb = radek["tmdb_id"], (kandidat or (None,) * 3)[2]
            if kandidat and not (stare_tmdb and nove_tmdb
                                 and str(stare_tmdb) != str(nove_tmdb)):
                nalezeny = kandidat
        if nalezeny and nalezeny[0] != str(radek["id"]):
            pary.append((str(radek["id"]), nalezeny[0]))

    if not pary:
        return 0

    with db.connect() as conn:
        for stare, nove in pary:
            # Poradi je dane cizim klicem: stopy odkazuji na polozku,
            # takze musi pryc driv, nez se polozka smaze.
            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (stare,))
            conn.execute("UPDATE playback SET item_id = ? WHERE item_id = ?",
                         (nove, stare))
            conn.execute("DELETE FROM items WHERE id = ?", (stare,))
            log.info("z archivu slouceno: %s -> %s", stare, nove)

    log.info("archiv: slouceno %s dilu, ktere v knihovne existuji znovu",
             len(pary))
    return len(pary)


def uklid_fantomu() -> int:
    """Vyhodi z knihovny polozky druhu, ktery synchronizace nezna.

    Naprava po chybe: `importers.zaloz_z_jellyfinu()` zakladala polozku
    z ceho koliv, co Jellyfin vratil - vcetne celeho SERIALU, kdyz zaznam
    historie visel na jeho id. Synchronizace se pta jen na filmy a dily,
    takze takovou polozku nikdy neuvidela a `_mark_missing()` ji pri
    kazdem behu poslala do archivu. V knihovne pak stal "archivovany"
    serial, ktery v Jellyfinu je - a vedle nej ten spravny, poskladany
    z dilu.

    Smazat je spravne reseni, ne jen prepnout priznak: takova polozka
    nikdy nemela vzniknout. Historie se tim neztraci - zaznamy zustavaji
    a vrati se mezi osirele, coz je pravda o nich rikala uz predtim.

    Vola se pri startu. Je to levny dotaz na indexovany sloupec a bezna
    databaze nema co uklizet, takze se nic nezdrzi.
    """
    otazniky = ",".join("?" for _ in SPRAVOVANE_TYPY)
    with db.connect() as conn:
        fantomy = [str(r["id"]) for r in conn.execute(
            f"SELECT id FROM items WHERE type NOT IN ({otazniky})",
            tuple(SPRAVOVANE_TYPY)).fetchall()]
        if not fantomy:
            return 0
        for zacatek in range(0, len(fantomy), 200):
            davka = fantomy[zacatek:zacatek + 200]
            znaky = ",".join("?" for _ in davka)
            conn.execute(
                f"DELETE FROM item_streams WHERE item_id IN ({znaky})", tuple(davka))
            conn.execute(f"DELETE FROM items WHERE id IN ({znaky})", tuple(davka))

    log.info("uklizeno %s polozek, ktere do knihovny nepatri (serialy a rady)",
             len(fantomy))
    return len(fantomy)


# ---------------------------------------------------------------------------
# 2. Technicka analyza pres ffprobe
# ---------------------------------------------------------------------------

async def run_tech_scan(only_missing: bool = True, limit: int = 0,
                        item_ids: list[str] | None = None,
                        library_id: str | None = None) -> dict[str, Any]:
    """Projde polozky a doplni technicke udaje pomoci ffprobe.

    `only_missing=True` znamena "jen ty, ktere jeste nemaji ffprobe data".
    Diky tomu se dlouhy scan da poustet po castech a nezacina vzdy od nuly.

    `item_ids` omezi beh na vyjmenovane polozky. Pouziva to rychla
    synchronizace: nove pridany titul tak dostane technicka data hned,
    misto aby na ne cekal az do dalsiho behu denni ulohy.

    `library_id` omezi beh na jednu knihovnu - to pouziva tlacitko primo
    u hlasky "nekolik souboru nema technicka data" na detailu knihovny.
    """
    if _scan_lock.locked():
        return {"status": "busy", "message": "Jiná úloha už běží."}

    async with _scan_lock:
        _clear_stop()
        settings = db.get_settings()
        ffprobe_bin = probe.find_ffprobe(settings.get("ffprobe_path", ""))
        if not ffprobe_bin:
            return {
                "status": "error",
                "message": (
                    "ffprobe se nepodařilo najít. Nainstaluj ffmpeg, nebo v Nastavení "
                    "vyplň plnou cestu k ffprobe.exe."
                ),
            }

        try:
            mappings = json.loads(settings.get("path_mappings") or "[]")
            if not isinstance(mappings, list):
                mappings = []
        except ValueError:
            mappings = []

        concurrency = db.get_int_setting("ffprobe_concurrency", 1, 16, 3)

        where = "WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        if only_missing:
            where += " AND (tech_source IS NULL OR tech_source != 'ffprobe')"

        parametry: list[Any] = []
        if library_id:
            where += " AND library_id = ?"
            parametry.append(library_id)

        if item_ids is not None:
            if not item_ids:
                return {"status": "ok", "total": 0, "ok": 0, "failed": 0}
            # Otazniky, ne slepeny seznam - id prichazi z odpovedi Jellyfinu
            # a do SQL se hodnoty nikdy nevkladaji primo.
            otazniky = ",".join("?" for _ in item_ids)
            where += f" AND id IN ({otazniky})"
            parametry.extend(item_ids)

        sql = f"SELECT id, path FROM items {where} ORDER BY name"
        if limit > 0:
            sql += f" LIMIT {int(limit)}"

        targets = db.query_all(sql, tuple(parametry))
        scan_id = start_task_log("tech")

        if not targets:
            finish_task_log(scan_id, "done", message=_t("Není co analyzovat."))
            return {"status": "ok", "total": 0, "ok": 0, "failed": 0}

        # Semafor omezi, kolik ffprobe procesu bezi soucasne. Bez nej by se
        # u velke knihovny spustily tisice procesu naraz a stroj by stal.
        _start_progress("tech", len(targets))
        semaphore = asyncio.Semaphore(concurrency)
        results = {"ok": 0, "failed": 0, "skipped": 0}
        results_lock = asyncio.Lock()
        # Položky, u kterých soubor aspoň u jedné stopy jazyk neuvádí.
        # Na ty se pak doptáme Jellyfinu - viz doplnit_jazyky_z_jellyfinu().
        bez_jazyka: set[str] = set()

        async def analyse(row: dict[str, Any]) -> None:
            async with semaphore:
                # Kontrola az tady, za semaforem: uloh je nachystano tolik,
                # kolik je souboru, ale soucasne jich bezi jen par. Ty
                # cekajici se po zastaveni jen tise preskoci, zatimco
                # rozpracovane doprobihaji. Zadny soubor tak neskonci
                # analyzovany napul.
                if stop_requested():
                    async with results_lock:
                        results["skipped"] += 1
                    return

                # Jeden soubor = jeden krok. Drive se `_add_progress(1)`
                # volalo na trech mistech uvnitr teto funkce (po prevzeti
                # semaforu, po analyze a po zapisu), takze hotovy soubor
                # posunul ukazatel o tri - a ten pak ukazoval treba 135 %.
                # `finally` navic zajisti, ze se krok zapocita i u souboru,
                # ktery skoncil chybou.
                try:
                    local_path = probe.apply_path_mappings(row["path"], mappings)
                    try:
                        tech = await probe.probe_file(local_path, ffprobe_bin)
                    except probe.ProbeError as exc:
                        await asyncio.to_thread(_save_tech_error, row["id"], str(exc))
                        async with results_lock:
                            results["failed"] += 1
                        return

                    # Take zapis do vlakna - u velke knihovny je tenhle blok
                    # v jedne smycce tisickrat a kazdy zapis by jinak na chvili
                    # zastavil obsluhu webu.
                    await asyncio.to_thread(_save_probe_result, row["id"], tech)
                    async with results_lock:
                        results["ok"] += 1
                        if _chybi_jazyk(tech.get("streams") or []):
                            bez_jazyka.add(row["id"])
                finally:
                    _add_progress(1)

        await asyncio.gather(*(analyse(row) for row in targets))
        _clear_progress()

        # Až po analýze, ne během ní: doptat se dá na padesát položek
        # jedním dotazem, kdežto uprostřed smyčky by to byl jeden dotaz
        # na soubor. A když Jellyfin zrovna neodpovídá, je to jen chybějící
        # doplněk - změřená data už jsou uložená, takže se to nesmí počítat
        # jako neúspěch celé analýzy.
        # Zpětně i na to, co je změřené z dřívějška. Bez tohohle by doplnění
        # platilo jen pro nově analyzované soubory - tedy skoro pro nic,
        # protože knihovna je obvykle změřená celá už dávno. Ptáme se jen
        # na stopy bez značky o původu, takže na tytéž se podruhé
        # nedoptáváme (viz _doplnit_jazyky_polozky).
        podminka = ("language = ? AND language_source IS NULL "
                    "AND type IN ('Audio', 'Subtitle')")
        parametry_zpetne: list[Any] = [languages.UNKNOWN]
        # Stejný rozsah jako u analýzy: obnova jedné položky nesmí spustit
        # doptávání na celou knihovnu.
        if library_id:
            podminka += (" AND item_id IN (SELECT id FROM items WHERE library_id = ?)")
            parametry_zpetne.append(library_id)
        if item_ids:
            otazniky = ",".join("?" for _ in item_ids)
            podminka += f" AND item_id IN ({otazniky})"
            parametry_zpetne.extend(item_ids)

        for radek in db.query_all(
                f"SELECT DISTINCT item_id FROM item_streams WHERE {podminka}",
                tuple(parametry_zpetne)):
            bez_jazyka.add(radek["item_id"])

        doplneno = 0
        if bez_jazyka and not stop_requested():
            try:
                doplneno = await doplnit_jazyky_z_jellyfinu(sorted(bez_jazyka))
            except JellyfinError as exc:
                log.warning("jazyky se z Jellyfinu doplnit nepodarilo: %s", exc)

        # Co nezná ani Jellyfin, zkusíme uhodnout z názvu souboru. Až
        # teď a jen na tom, co zbylo: název je odhad, kdežto soubor
        # a knihovna jsou údaj.
        zbyle = set(kandidati_na_jazyk_z_nazvu(library_id, item_ids))
        z_nazvu = 0
        if zbyle and not stop_requested():
            z_nazvu = await asyncio.to_thread(doplnit_jazyky_z_nazvu, sorted(zbyle))

        # Dynamicky rozsah podle Jellyfinu. Zapisuje ho sice uz
        # synchronizace knihovny, jenze technicka data si clovek spojuje
        # s TOUHLE ulohou - spustil "Analyzu souboru", videl, ze se nic
        # nezmenilo, a nemel duvod tusit, ze chybejici Dolby Vision
        # doplni az jina uloha.
        rozsahy = 0
        if not stop_requested():
            chybejici = kandidati_na_rozsah_z_jellyfinu(library_id, item_ids)
            if chybejici:
                try:
                    rozsahy = await doplnit_rozsah_z_jellyfinu(chybejici)
                except JellyfinError as exc:
                    log.warning("rozsah se z Jellyfinu doplnit nepodarilo: %s", exc)

        doplnek = (_t(", jazyk doplněn z Jellyfinu u {n} stop").format(n=doplneno)
                   if doplneno else "")
        if z_nazvu:
            doplnek += _t(", odhadnut z názvu u {n} titulů").format(n=z_nazvu)
        if rozsahy:
            doplnek += _t(", rozsah z Jellyfinu u {n} titulů").format(n=rozsahy)

        if results["skipped"]:
            finish_task_log(
                scan_id, "stopped",
                total=len(targets), ok=results["ok"], failed=results["failed"],
                message=(f"Zastaveno na tvůj pokyn. Zbylo {results['skipped']} souborů - "
                         f"příští analýza na ně naváže. ffprobe: {ffprobe_bin}"
                         f"{doplnek}"),
            )
            return {"status": "stopped", "total": len(targets), **results}

        finish_task_log(
            scan_id, "done",
            total=len(targets), ok=results["ok"], failed=results["failed"],
            message=f"ffprobe: {ffprobe_bin}{doplnek}",
        )
        return {"status": "ok", "total": len(targets), "doplneno": doplneno,
                "z_nazvu": z_nazvu, **results}


def _chybi_jazyk(streams: list[dict[str, Any]]) -> bool:
    """Má aspoň jedna zvuková stopa nebo titulky "neuvedeno"?

    Video se neřeší: u obrazu jazyk nikdo nečeká a "neuvedeno" tam není
    chybějící údaj, ale správná odpověď.
    """
    return any(s.get("type") in ("Audio", "Subtitle")
               and s.get("language") == languages.UNKNOWN
               for s in streams)


def kandidati_na_rozsah_z_jellyfinu(library_id: str | None = None,
                                    item_ids: list[str] | None = None) -> list[str]:
    """Polozky, u kterych jeste nevime, co o rozsahu rika Jellyfin.

    Jen ty s prazdnym udajem: jinak by kazda analyza tahala z Jellyfinu
    celou knihovnu znovu, prestoze se ta hodnota meni jen pri prekodovani
    souboru - a to zachyti synchronizace.
    """
    kde = ["is_missing = 0", "video_range_reported IS NULL"]
    hodnoty: list[Any] = []
    if library_id:
        kde.append("library_id = ?")
        hodnoty.append(library_id)
    if item_ids:
        otazniky = ",".join("?" for _ in item_ids)
        kde.append(f"id IN ({otazniky})")
        hodnoty.extend(item_ids)
    return [radek["id"] for radek in db.query_all(
        f"SELECT id FROM items WHERE {' AND '.join(kde)}", tuple(hodnoty))]


async def doplnit_rozsah_z_jellyfinu(item_ids: list[str]) -> int:
    """Zapise k polozkam dynamicky rozsah tak, jak ho vidi Jellyfin.

    Vedle toho, co zmeril ffprobe. Dolby Vision v Matrosce umi cist az
    ffmpeg 5, takze starsi ffprobe hlasi jen "HDR" - a Jellyfin je pak
    jediny, kdo o DV vi. Viz stats.ROZSAH_CASE.

    Vraci pocet polozek, u kterych Jellyfin nejaky rozsah rekl.
    """
    if not item_ids:
        return 0

    async with JellyfinClient(*db.jellyfin_connection()) as client:
        polozky = await client.items_by_ids(list(item_ids))

    zmeny = [(video_range_of(item), item.get("Id"))
             for item in polozky if item.get("Id")]
    if not zmeny:
        return 0

    def _uloz() -> None:
        with db.connect() as conn:
            conn.executemany(
                "UPDATE items SET video_range_reported = ? WHERE id = ?", zmeny)

    await asyncio.to_thread(_uloz)
    nalezeno = sum(1 for rozsah, _ in zmeny if rozsah)
    if nalezeno:
        log.info("rozsah z Jellyfinu doplnen u %s polozek", nalezeno)
    return nalezeno


async def doplnit_jazyky_z_jellyfinu(item_ids: list[str]) -> int:
    """U stop, kde soubor jazyk neuvádí, se zeptáme Jellyfinu.

    ffprobe čte jen to, co je v souboru - a v mnoha souborech u stopy
    prostě žádný jazyk zapsaný není. Jellyfin ho přitom často zná: dopočítá
    si ho z názvu souboru, ze složky nebo z metadat, která si vede sám.
    Výsledek pak vypadá jako chyba Jellyscope ("Neuvedeno" u stopy, kterou
    Jellyfin hlásí jako češtinu), přestože oba nástroje mají pravdu - jen
    se každý dívá jinam.

    Doplňujeme proto **jen mezery**. Co ffprobe přečetl, zůstává; jazyk
    z Jellyfinu se dosadí tam, kde je "neuvedeno", a zapíše se k němu, že
    není ze souboru (`language_source`).

    Vrací počet stop, kterým se jazyk doplnil.
    """
    if not item_ids:
        return 0

    async with JellyfinClient(*db.jellyfin_connection()) as client:
        polozky = await client.items_by_ids(list(item_ids))

    doplneno = 0
    for item in polozky:
        doplneno += await asyncio.to_thread(_doplnit_jazyky_polozky, item)
    if doplneno:
        log.info("jazyk doplnen z Jellyfinu u %s stop", doplneno)
    return doplneno


def _doplnit_jazyky_polozky(item: dict[str, Any]) -> int:
    """Jedna položka: spáruje uložené stopy s těmi z Jellyfinu."""
    item_id = item.get("Id")
    if not item_id:
        return 0

    jejich = extract_streams(item)
    if not jejich:
        return 0

    nase = db.query_all(
        "SELECT stream_index, type, language FROM item_streams "
        "WHERE item_id = ? ORDER BY stream_index",
        (item_id,))
    if not nase:
        return 0

    zmeny = []
    for radek, jejich_stopa in _sparuj_stopy(nase, jejich):
        if radek["language"] != languages.UNKNOWN:
            continue          # soubor jazyk uvádí - do toho nesaháme
        jazyk = languages.normalize(jejich_stopa.get("language"))
        if jazyk == languages.UNKNOWN:
            continue          # Jellyfin ho taky nezná, není co doplnit
        zmeny.append((jazyk, item_id, radek["stream_index"]))

    with db.connect() as conn:
        if zmeny:
            conn.executemany(
                "UPDATE item_streams SET language = ?, language_source = 'jellyfin' "
                "WHERE item_id = ? AND stream_index = ?",
                zmeny)
        # Co zůstalo neuvedené, dostane značku "ptali jsme se, neví se".
        #
        # Bez ní by se aplikace ptala na tytéž stopy při každé analýze
        # znovu - a odpověď by byla pokaždé stejná. Značka zmizí sama,
        # jakmile se soubor změří znovu (stopy se přepisují), takže po
        # opravě metadat v Jellyfinu se doptáme zase.
        conn.execute(
            "UPDATE item_streams SET language_source = 'neznamy' "
            " WHERE item_id = ? AND language = ? AND language_source IS NULL "
            "   AND type IN ('Audio', 'Subtitle')",
            (item_id, languages.UNKNOWN))

    if zmeny:
        _prepocitej_jazyky_polozky(item_id)
    return len(zmeny)


def kandidati_na_jazyk_z_nazvu(library_id: str | None = None,
                               item_ids: list[str] | None = None) -> list[str]:
    """Polozky, u kterych ma smysl zkusit jazyk odhadnout z nazvu souboru.

    Dve skupiny, a obe se daly prehlednout:

    * **Stopy, se kterymi nepomohl Jellyfin.** Ten krok si je oznaci jako
      "neznamy", aby se na ne priste neptal znovu. Kdyz se pak hledaly
      stopy BEZ oznaceni, byl seznam vzdycky prazdny a nazev souboru se
      nepouzil vubec - fungovalo to jen ve chvili, kdy Jellyfin
      neodpovedel. Proto se tu berou obe podoby.

    * **Polozky, ktere nemaji ani stopu.** U nich se analyza souboru
      nikdy nepovedla - typicky knihovna, ke ktere kontejner nema
      pristup. Nazev je pak jediny zdroj, jaky existuje; bez toho by
      takove soubory zustaly v "Souborech bez urceneho jazyka" navzdy.

    `library_id` a `item_ids` drzi stejny rozsah jako analyza: obnova
    jedne polozky nesmi spustit prochazeni cele knihovny.
    """
    kde_stop = ["language = ?",
                "(language_source IS NULL OR language_source = 'neznamy')",
                "type IN ('Audio', 'Subtitle')"]
    hodnoty_stop: list[Any] = [languages.UNKNOWN]
    # `audio_from_name IS NULL` hlida, at se to nepocita dokola u toho,
    # co uz nazev jednou vydal.
    kde_polozek = ["is_missing = 0", "path IS NOT NULL", "path <> ''",
                   "audio_from_name IS NULL",
                   "(audio_languages IS NULL OR audio_languages = ''"
                   " OR audio_languages = ?)"]
    hodnoty_polozek: list[Any] = [languages.UNKNOWN]

    if library_id:
        kde_stop.append("item_id IN (SELECT id FROM items WHERE library_id = ?)")
        hodnoty_stop.append(library_id)
        kde_polozek.append("library_id = ?")
        hodnoty_polozek.append(library_id)
    if item_ids:
        otazniky = ",".join("?" for _ in item_ids)
        kde_stop.append(f"item_id IN ({otazniky})")
        hodnoty_stop.extend(item_ids)
        kde_polozek.append(f"id IN ({otazniky})")
        hodnoty_polozek.extend(item_ids)

    nalezene = {radek["item_id"] for radek in db.query_all(
        f"SELECT DISTINCT item_id FROM item_streams "
        f"WHERE {' AND '.join(kde_stop)}", tuple(hodnoty_stop))}
    nalezene |= {radek["id"] for radek in db.query_all(
        f"SELECT id FROM items WHERE {' AND '.join(kde_polozek)}",
        tuple(hodnoty_polozek))}
    return sorted(nalezene)


def doplnit_jazyky_z_nazvu(item_ids: list[str]) -> int:
    """Poslední záchrana: jazyk podle názvu souboru.

    Když ho nezná soubor ani Jellyfin, zbývá to, co si do názvu napsal
    člověk - "Duna.2021.CZ.SK.EN.1080p.mkv". Hledá se celý úsek mezi
    oddělovači, takže "Czechacek" ani "enigma" neprojdou; podrobnosti
    v languages.z_nazvu().

    Název ale říká jen to, KTERÉ jazyky v souboru jsou, ne která stopa
    je která. Rozdělujeme je proto po řadě a označíme jako odhad z názvu -
    množina jazyků (a tím i statistiky) sedí, pořadí je odhad.

    Vrací počet **položek**, kterým název pomohl - ne stop. U jedné
    položky se totiž jazyk může dostat ke stopám, u druhé jen do souhrnu,
    a "tři stopy a dva souhrny" by se sčítat nedalo.
    """
    doplneno = 0
    for item_id in item_ids:
        radek = db.query_one("SELECT id, path FROM items WHERE id = ?", (item_id,))
        if radek is None or not radek["path"]:
            continue
        if _doplnit_z_nazvu_polozky(radek["id"], radek["path"]):
            doplneno += 1
    if doplneno:
        log.info("jazyk odhadnut z nazvu souboru u %s polozek", doplneno)
    return doplneno


def _doplnit_z_nazvu_polozky(item_id: str, cesta: str) -> bool:
    nalezene = languages.z_nazvu(cesta)
    if not nalezene["zvuk"] and not nalezene["titulky"]:
        return False

    stopy = db.query_all(
        "SELECT stream_index, type, language FROM item_streams "
        "WHERE item_id = ? ORDER BY stream_index", (item_id,))

    zmeny: list[tuple[str, str, int]] = []
    do_souhrnu: dict[str, str] = {}
    for druh, klic, sloupec in (("Audio", "zvuk", "audio_from_name"),
                                ("Subtitle", "titulky", "subtitle_from_name")):
        prirazeni, hotovo = _rozdelit_podle_nazvu(
            item_id, [s for s in stopy if s["type"] == druh], nalezene[klic])
        zmeny.extend(prirazeni)
        if not hotovo and nalezene[klic]:
            # Ke stopám to přiřadit nejde, ale že ten jazyk v souboru je,
            # víme. Do souhrnu položky s tím - odtud čte statistika, a ta
            # se ptá "které jazyky tam jsou", ne "která stopa je která".
            do_souhrnu[sloupec] = languages.pack(nalezene[klic])

    if not zmeny and not do_souhrnu:
        return False

    with db.connect() as conn:
        if zmeny:
            conn.executemany(
                "UPDATE item_streams SET language = ?, language_source = 'nazev' "
                "WHERE item_id = ? AND stream_index = ?",
                zmeny)
        for sloupec, hodnota in do_souhrnu.items():
            conn.execute(f"UPDATE items SET {sloupec} = ? WHERE id = ?",
                         (hodnota, item_id))
    _prepocitej_jazyky_polozky(item_id)
    return True


def _rozdelit_podle_nazvu(item_id: str, stopy: list[dict[str, Any]],
                          z_nazvu: list[str]) -> tuple[list[tuple[str, str, int]], bool]:
    """Které stopě přiřadit který jazyk z názvu - nebo raději žádné.

    Vrací dvojici (změny, přiřazeno_všechno).

    Přiřazujeme jen tehdy, když **zbývá tolik neznámých stop, kolik
    jazyků název přidává navíc**. Typický soubor má český dabing
    a původní zvuk, ale v názvu je jen "CZ" - jedna značka na dvě
    neznámé stopy. Která z nich je česká, se hádat nedá.

    Dvě podoby té samé úvahy stojí za rozlišení:

    * Název jmenuje víc jazyků, než kolik je stop **v celém pořadí** -
      pak sedí i pozice a doplní se všechny (viz níže).
    * Jazyky, které v souboru **už známe**, se z názvu odečtou: u stop
      [angličtina, neznámá] a názvu "CZ.EN" zbývá jediný nový jazyk na
      jedinou neznámou stopu, takže je jasné, co kam patří.

    Když ani to nevyjde, vrátíme prázdno a `False` - a volající pak jazyky
    zapíše aspoň do souhrnu položky, kde nic o pořadí netvrdí.
    """
    if not z_nazvu or not stopy:
        return [], False

    neznama = [s for s in stopy if s["language"] == languages.UNKNOWN]
    if not neznama:
        return [], True          # není co doplňovat, vše už jazyk má

    # Nejdřív celé pořadí: název jmenuje přesně tolik jazyků, kolik je
    # stop. Pak se dá věřit i pozicím - a co už víme, to musí souhlasit.
    if len(stopy) == len(z_nazvu):
        zmeny = []
        for stopa, jazyk in zip(stopy, z_nazvu):
            if stopa["language"] == languages.UNKNOWN:
                zmeny.append((jazyk, item_id, stopa["stream_index"]))
            elif stopa["language"] != jazyk:
                zmeny = []       # pořadí nesedí
                break
        if zmeny:
            return zmeny, True

    # Jinak zkusíme jazyky, které v souboru zatím nejsou.
    znama = {s["language"] for s in stopy if s["language"] != languages.UNKNOWN}
    nove = [j for j in z_nazvu if j not in znama]
    if len(nove) == len(neznama):
        return [(jazyk, item_id, stopa["stream_index"])
                for stopa, jazyk in zip(neznama, nove)], True

    return [], False


def _sparuj_stopy(nase: list[dict[str, Any]],
                  jejich: list[dict[str, Any]]) -> list[tuple[dict, dict]]:
    """Které naše stopě odpovídá která z Jellyfinu.

    Párujeme podle **pořadí v rámci druhu** (první zvuková k první
    zvukové), ne podle čísla stopy: Jellyfin čísluje i externí titulky,
    které v souboru nejsou, takže se čísla rozejdou.

    Když počty nesedí, nepárujeme nic. Špatně doplněný jazyk je horší než
    žádný - u "neuvedeno" je aspoň vidět, že se neví.
    """
    pary = []
    for druh in ("Audio", "Subtitle"):
        moje = [s for s in nase if s["type"] == druh]
        cizi = [s for s in jejich
                if s.get("type") == druh and not s.get("is_external")]
        if not moje or len(moje) != len(cizi):
            continue
        pary.extend(zip(moje, cizi))
    return pary


def _prepocitej_jazyky_polozky(item_id: str) -> None:
    """Souhrn jazyků u položky podle toho, co je teď ve stopách.

    Sloupce `audio_languages` a spol. jsou přepis stop do jednoho řetězce
    kvůli filtrům a statistice. Když se jazyk stopy změní a souhrn ne,
    detail položky ukazuje češtinu, ale filtr "české" ji nenajde.
    """
    stopy = db.query_all(
        "SELECT type, language FROM item_streams WHERE item_id = ? "
        "ORDER BY stream_index", (item_id,))
    zvuk = [s["language"] for s in stopy if s["type"] == "Audio"]
    titulky = [s["language"] for s in stopy if s["type"] == "Subtitle"]

    # A k tomu jazyky, které slíbil název souboru a nešly přiřadit ke
    # konkrétní stopě. Bez nich by je statistika neviděla - a přitom to
    # je jediné, co o tom souboru víme.
    polozka = db.query_one(
        "SELECT audio_from_name, subtitle_from_name FROM items WHERE id = ?",
        (item_id,))
    if polozka:
        zvuk += languages.unpack(polozka["audio_from_name"] or "")
        titulky += languages.unpack(polozka["subtitle_from_name"] or "")

    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET audio_languages = ?, subtitle_languages = ?, "
            "default_audio_language = ? WHERE id = ?",
            (languages.pack(zvuk), languages.pack(titulky),
             # Stejná úvaha jako v probe._summarise: bere se první zvuková
             # stopa, ne ta označená jako výchozí.
             languages.normalize(zvuk[0]) if zvuk else None,
             item_id))


def _save_probe_result(item_id: str, tech: dict[str, Any]) -> None:
    """Uloží výsledek jedné analýzy. Běží ve vlákně."""
    _save_tech(item_id, tech)
    save_streams(item_id, tech.get("streams") or [])


def _save_tech(item_id: str, tech: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE items
               SET container = ?, video_codec = ?, audio_codec = ?, audio_channels = ?,
                   width = ?, height = ?, bitrate = ?, size_bytes = ?, video_range = ?,
                   audio_languages = ?, subtitle_languages = ?, default_audio_language = ?,
                   tech_source = 'ffprobe', tech_updated_at = ?, tech_error = NULL
             WHERE id = ?
            """,
            (
                tech.get("container"), tech.get("video_codec"), tech.get("audio_codec"),
                tech.get("audio_channels"), tech.get("width"), tech.get("height"),
                tech.get("bitrate"), tech.get("size_bytes"), tech.get("video_range"),
                tech.get("audio_languages"), tech.get("subtitle_languages"),
                tech.get("default_audio_language"),
                db.utcnow(), item_id,
            ),
        )


def _save_tech_error(item_id: str, message: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET tech_error = ?, tech_updated_at = ? WHERE id = ?",
            (message[:300], db.utcnow(), item_id),
        )


# Planovani techto uloh uz neni tady, ale v tasks.py - spolecne pro vsechny
# naplanovane cinnosti. Tenhle soubor umi ulohy udelat; kdy se maji delat,
# rozhoduje nekdo jiny. Je to same rozdeleni jako "co" versus "kdy" u budiku.
