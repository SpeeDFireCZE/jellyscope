"""Obnova jedné položky a seriálu, nedávné změny a jejich revize.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from .. import db
from ..i18n import translate as _t
from ..jellyfin import JellyfinClient, JellyfinError, extract_streams, extract_tech_from_item
from .beh import (
    _clear_progress,
    _clear_stop,
    _scan_lock,
    _start_progress,
    finish_task_log,
    start_task_log,
)
from .slouceni import do_archivu, dvojnici_novych, prenes_historii
from .synchronizace import _radek_polozky, _sync_libraries, _sync_users, _uloz_nove_polozky
from .technika import run_tech_scan
from .zapis import _write_items, _zapomen_obrazky, save_streams

log = logging.getLogger("jellyscope.scanner")


# O kolik minut se pri rychle synchronizaci vratime pred posledni znamy
# titul. Puvodne pet minut - pojistka proti propadnuti na hranici. Den je
# tu kvuli tomu, co Jellyfin dela behem skenu: dil, ktery uz jsme ulozili,
# muze jeste zaradit k serialu nebo ho zalozit znovu pod novym id - se
# stejnym datem pridani. S peti minutami se k nemu rychla synchronizace
# uz nikdy nevratila a v Nedavno pridanych stal mimo svuj serial, dokud
# ho neprepsala plna synchronizace. Viz `_reviduj_nedavne()`.
RECENT_OVERLAP_MINUTES = 24 * 60

# Kolik nedavnych polozek se v jednom behu nanejvys overuje podle id
# (ty, ktere Jellyfin v tomhle behu neposlal). Strop proti dni, kdy se do
# knihovny naleje cela nova sbirka.
REVIZE_STROP = 500


def _posledni_pridano() -> str | None:
    """Datum posledniho titulu, ktery uz v knihovne mame.

    Odtud se rychla synchronizace odrazi: co je novejsi, jeste neznáme.
    Vraci None, kdyz je knihovna prazdna - pak se vezmou proste nejnovejsi
    polozky, kolik se jich vejde do stropu.

    Odecita se den (`RECENT_OVERLAP_MINUTES`). Projit tentyz titul
    podruhe nic nestoji - zapisuje se pres ON CONFLICT - a prave tim se
    opravi dil, ktery Jellyfin pri prvnim pruchodu jeste nemel zarazeny
    k serialu.
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


def _zprava_nedavnych(od: str | None, pridano: int, videno: int,
                      nahrazeno: int = 0, znovu: int = 0) -> str:
    """Zpráva do logu úlohy „Nově přidané tituly".

    Věty o archivu končí tečkou, začátek zprávy ne („Nic nového
    (zkontrolováno 40)"). Dřív se lepily přes ". " a když se naráz
    archivovalo z obou důvodů, stály v logu dvě tečky za sebou.
    """
    if not od:
        zprava = _t("{n} nejnovějších titulů (knihovna byla prázdná)").format(n=pridano)
    elif pridano:
        zprava = _t("{n} nových titulů (zkontrolováno {celkem})").format(
            n=pridano, celkem=videno)
    else:
        # Nula je uplne bezny vysledek - uloha bezi kazdych par minut.
        # Musi se tak i tvarit, jinak clovek marne hleda, co pribylo.
        zprava = _t("Nic nového (zkontrolováno {celkem})").format(celkem=videno)
    vety = []
    # Kdyz slo o vymenene soubory, ma to byt videt v logu ulohy - jinak je
    # "3 nove tituly" matouci: nove nejsou, jen jinak velke.
    if od and pridano and nahrazeno:
        vety.append(_t(
            "{n} z nich nahradilo starší soubor. Ty jsou v archivu a jejich historie je "
            "připojená.").format(n=nahrazeno))
    if znovu:
        vety.append(_t(
            "{n} dříve uložených položek Jellyfin mezitím založil znovu, staré jsou v "
            "archivu.").format(n=znovu))
    if not vety:
        return zprava
    return zprava.rstrip(".") + ". " + " ".join(vety)


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
        poslane: set[str] = set()
        srovnani: dict[str, Any] = {}
        revize: dict[str, int] = {}

        try:
            async with JellyfinClient(*db.jellyfin_connection()) as client:
                # Uzivatele taky - je to jedno volani a diky nemu zmizi
                # ucet odebraneho cloveka do par minut, ne az po nocni
                # plne synchronizaci.
                await _sync_users(client)
                knihovny = await _sync_libraries(client)
                use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"
                _start_progress("recent", 0)

                for knihovna in knihovny:
                    polozky = await client.recent_items(
                        od, strop=max_items, parent_id=knihovna["id"])
                    videno += len(polozky)
                    poslane.update(str(i["Id"]) for i in polozky if i.get("Id"))
                    nova_id.extend(await _uloz_nove_polozky(
                        polozky, knihovna["id"], use_jellyfin_tech, client))

                # Co jsme za ten den ulozili a Jellyfin ted neposlal, se
                # overi podle id - jinak by tam visel dil z doby, kdy ho
                # Jellyfin jeste nemel zarazeny k serialu.
                if od:
                    revize = await _reviduj_nedavne(client, od, poslane,
                                                    use_jellyfin_tech)

                # Nove tituly casto nejsou nove tituly, ale nove SOUBORY
                # tehoz titulu. Srovnat se to musi hned - jinak v
                # knihovne stoji tentyz film dvakrat az do plne
                # synchronizace, a to je pres noc.
                #
                # Uvnitr `async with`: potrebuje se Jellyfinu zeptat,
                # jestli stary ItemId jeste zna.
                if nova_id:
                    srovnani = await srovnej_po_novych(client, nova_id)

                # Archivovane revizi, ke kterym se nenasel naslednik se
                # stejnou cestou, mohly mit historii. Narovnani ji zkusi
                # pripojit podle serialu a cisla dilu - pokud uz ho
                # nespustilo srovnani o par radku vys.
                if revize.get("bez_naslednika") and not srovnani.get("narovnano"):
                    from .. import importers   # az tady: importers sahaji na scanner

                    await importers.narovnej_data()

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
        zprava = _zprava_nedavnych(od, pridano, videno,
                                   nahrazeno=srovnani.get("archivovano", 0),
                                   znovu=revize.get("archivovano", 0))
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


async def _reviduj_nedavne(client: JellyfinClient, od: str, poslane: set[str],
                           use_jellyfin_tech: bool) -> dict[str, int]:
    """Nedávno uložené položky, které Jellyfin v tomhle běhu neposlal.

    Rychla synchronizace vidi jen to, co Jellyfin posle - a behem skenu
    se polozky meni pod rukama. Dil se nejdriv objevi jeste nezarazeny
    k serialu, nebo ho Jellyfin pri zarazeni zalozi znovu pod novym id.
    Ten prvni zaznam pak u nas zustal: v Nedavno pridanych stal jako
    samostatny dil vedle serialu, do ktereho patri, a uklidila ho az
    plna synchronizace (ta archivuje vsechno, co nevidela).

    Tady se dela totez, jen pro posledni den: kazda polozka pridana od
    `od`, ktera v tomhle behu neprisla, se overi podle id.

    * Jellyfin ji nezna -> do archivu. Presne to by s ni udelala plna
      synchronizace; historie se pak pripoji k zivemu dilu narovnanim.
    * Jellyfin ji zna (jen ji neposlal, treba kvuli stropu) -> zapise
      se znovu, se vsim, co o ni ted vi.

    Nic se tu nemaze a nic se neslucuje podle odhadu - rozhoduje jen to,
    co rekne Jellyfin o konkretnim id.
    """
    radky = await asyncio.to_thread(
        db.query_all,
        "SELECT id, library_id FROM items"
        " WHERE is_missing = 0 AND date_created IS NOT NULL"
        "   AND REPLACE(date_created, 'T', ' ') >= ?"
        " ORDER BY date_created DESC",
        (od,))
    kandidati = {str(r["id"]): r["library_id"] for r in radky
                 if str(r["id"]) not in poslane}
    if not kandidati:
        return {"overeno": 0, "archivovano": 0, "obnoveno": 0}
    ids = list(kandidati)[:REVIZE_STROP]

    zname = {str(i["Id"]): i for i in await client.items_by_ids(ids) if i.get("Id")}

    # Zname se zapisou znovu po knihovnach - `library_id` Jellyfin
    # v odpovedi nenese, bere se ten ulozeny.
    podle_knihovny: dict[Any, list[dict[str, Any]]] = {}
    for item_id, item in zname.items():
        podle_knihovny.setdefault(kandidati[item_id], []).append(item)
    for library_id, polozky in podle_knihovny.items():
        await _uloz_nove_polozky(polozky, library_id, use_jellyfin_tech, client)

    nezname = [i for i in ids if i not in zname]
    archivovano = await asyncio.to_thread(do_archivu, nezname)
    par = await asyncio.to_thread(_naslednici_podle_cesty, nezname)
    preneseno = await asyncio.to_thread(prenes_historii, par)

    if archivovano:
        log.info("rychla synchronizace: %s nedavnych polozek Jellyfin uz nezna"
                 " - jsou v archivu", archivovano)
    return {"overeno": len(ids), "archivovano": archivovano,
            "obnoveno": len(zname), "historie": preneseno,
            "bez_naslednika": len(nezname) - len(par)}


def _naslednici_podle_cesty(stara_id: list[str]) -> dict[str, str]:
    """Živá položka se stejnou cestou k souboru - {staré id: nové id}.

    Kdyz Jellyfin dil zalozi znovu, stary zaznam casto nema vyplneny
    serial (proto ho zakladal znovu), takze narovnani podle "serial +
    cislo dilu" ho nespáruje. Cesta je ale stejna - je to porad tentyz
    soubor - a na tu je spoleh: dva ruzne tituly v jednom souboru
    nelezi.
    """
    if not stara_id:
        return {}
    par: dict[str, str] = {}
    for stare in stara_id:
        radek = db.query_one(
            "SELECT n.id FROM items s JOIN items n"
            "  ON n.path = s.path AND n.id <> s.id AND n.is_missing = 0"
            " WHERE s.id = ? AND s.path IS NOT NULL AND s.path <> ''"
            # COALESCE: PostgreSQL by NULL pri DESC dal na zacatek, SQLite
            # na konec - a LIMIT 1 by vybral pokazde jiny radek.
            " ORDER BY COALESCE(n.date_created, '') DESC LIMIT 1", (stare,))
        if radek:
            par[stare] = str(radek["id"])
    return par


async def srovnej_po_novych(client: JellyfinClient,
                            nova_id: list[str]) -> dict[str, Any]:
    """Po nových titulech srovná knihovnu, ať v ní nestojí dvojníci.

    Tři kroky a každý je tu z jiného důvodu:

    1. **najít dvojníky** - starší položky, které vypadají jako totéž,
    2. **zeptat se Jellyfinu**, jestli je ještě zná. Tohle je celé
       rozhodnutí: co Jellyfin zná, je druhá kopie (4K vedle 1080p) a
       slučovat se nesmí; co nezná, je nahrazený soubor,
    3. **narovnat data** - připojit historii archivované položky k té
       živé. Na to už je hotová úloha, tak se zavolá, ne opíše.

    Bez kroku 2 by se z „mám film ve dvou kvalitách" stal jeden film
    a půlka historie by zmizela.
    """
    from .. import importers          # az tady: importers sahaji na scanner

    podezrele = await asyncio.to_thread(dvojnici_novych, nova_id)
    if not podezrele:
        return {"dvojniku": 0, "archivovano": 0, "narovnano": None}

    kandidati = sorted({stary for seznam in podezrele.values()
                        for stary in seznam})
    zive = await client.items_by_ids(kandidati)
    zname = {str(i["Id"]) for i in zive if i.get("Id")}
    nahrazene = [stary for stary in kandidati if stary not in zname]

    archivovano = await asyncio.to_thread(do_archivu, nahrazene)
    if archivovano:
        log.info("po novych titulech: %s nahrazenych souboru slo do archivu",
                 archivovano)

    # Historii prenaset musime sami: narovnani dat umi pripojit dil
    # serialu z archivu (podle cisla rady a dilu), ale u filmu bez
    # tmdb id nema podle ceho poznat, ze jde o tentyz titul. My to vime -
    # prave jsme se Jellyfinu zeptali.
    par = {stary: novy for novy, seznam in podezrele.items()
           for stary in seznam if stary in set(nahrazene)}
    preneseno = await asyncio.to_thread(prenes_historii, par)

    # A pak uz bezne narovnani: dily serialu, osirele zaznamy, duplicity.
    narovnano = await importers.narovnej_data() if archivovano else None
    return {"dvojniku": len(kandidati), "archivovano": archivovano,
            "historie": preneseno, "narovnano": narovnano}
