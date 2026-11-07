"""Doplnění jazyků stop z Jellyfinu a z názvů souborů.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import db, languages
from ..jellyfin import JellyfinClient, extract_streams

log = logging.getLogger("jellyscope.scanner")


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
