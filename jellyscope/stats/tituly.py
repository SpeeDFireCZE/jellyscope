"""Seskupení přehrání do titulů (seriál, film) a nejsledovanější tituly.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

import re
from typing import Any

from .. import db
from .druhy import KIND_BOTH, KIND_MOVIE, KIND_SERIES
from .obdobi import _meze


# Název dílu tak, jak ho zapisuje plugin Playback Reporting a některé
# klienty: "Seriál - s01e01 - Název dílu". U takového záznamu nemáme
# `series_name` ani položku v knihovně, takže seriál se dá poznat jedině
# odtud - a bez toho se v přehledech rozpadl na jednotlivé díly.
_SERIAL_Z_NAZVU = re.compile(r"^(?P<serial>.+?)\s*[-–]\s*s\d{1,3}e\d{1,4}\b", re.I)


def serial_z_nazvu(nazev: Any) -> str | None:
    """Ze zápisu "Seriál - s01e01 - Díl" vytáhne název seriálu."""
    if not nazev:
        return None
    shoda = _SERIAL_Z_NAZVU.match(str(nazev).strip())
    if not shoda:
        return None
    serial = shoda.group("serial").strip(" -–")
    return serial or None


def _dily_podle_nazvu() -> dict[str, tuple[str, str]]:
    """Názvy epizod z knihovny -> id seriálu, ke kterému patří.

    Slouží k jediné věci: záznam historie, který nese jen jméno dílu
    ("Zvony") a k jehož položce už v knihovně nic nevede, se dá zařadit
    pod seriál, do kterého ten díl patří ("Hra o trůny"). Bez toho z něj
    v přehledu vznikl samostatný titul - film, který neexistuje.

    **Jen jednoznačné shody.** Názvy dílů se mezi seriály opakují
    ("Pilot", "Část 1"); u takového jména radši nehádáme, protože špatně
    přiřazená historie je horší než ta, o které víme, že je stranou.

    Klíče mají předponu `dil:`, aby se nemíchaly s názvy seriálů - seriál
    a jeho epizoda se můžou jmenovat stejně.
    """
    podle_jmena: dict[str, set[str]] = {}
    for radek in db.query_all(
        "SELECT name, series_id FROM items WHERE series_id IS NOT NULL"
        "  AND name IS NOT NULL AND name != ''"
    ):
        jmeno = str(radek["name"]).strip().lower()
        podle_jmena.setdefault(jmeno, set()).add(str(radek["series_id"]))

    jmena = _jmena_serialu()
    return {f"dil:{jmeno}": (next(iter(serialy)),
                             jmena.get(next(iter(serialy)), "?"))
            for jmeno, serialy in podle_jmena.items() if len(serialy) == 1}


def _jmena_serialu() -> dict[str, str]:
    """id seriálu -> jeho název. Pro popisek řádku."""
    return {str(r["series_id"]): str(r["name"]) for r in db.query_all(
        "SELECT series_id, MAX(series_name) AS name FROM items"
        " WHERE series_id IS NOT NULL AND series_name IS NOT NULL"
        " GROUP BY series_id")}


def klic_titulu(row: dict[str, Any],
                podle_nazvu: dict[str, str] | None = None) -> tuple[str, str, bool]:
    """Do jaké skupiny řádek historie patří: (klíč, popisek, je to seriál).

    Pořadí je od nejspolehlivějšího údaje k nejslabšímu:

      1. `series_id` z knihovny - jediné, co nezáleží na tom, jak se který
         záznam kdysi vyplnil,
      2. název seriálu (ze záznamu nebo z položky),
      3. název seriálu vyparsovaný z "Seriál - s01e01 - Díl",
      4. díl toho jména, který zná knihovna (viz `_dily_podle_nazvu()`),
      5. samotná položka - film, nebo díl, u kterého o seriálu nevíme nic.

    Body 3 a 4 tu jsou proto, že převzatá historie o seriálu neřekne nic.
    Playback Reporting ukládá název jako "Blue - s01e17 - Calypso"; jiné
    zdroje jen "Zvony". V přehledu se pak objevilo pět samostatných řádků
    místo jednoho "Blue", nebo díl "Zvony" jako film vedle "Hry o trůny".
    """
    if row.get("series_id"):
        return (f"serial:{row['series_id']}",
                str(row.get("series_name") or row.get("i_series_name")
                    or serial_z_nazvu(row.get("item_name")) or row.get("i_name") or "?"),
                True)

    # Položku v knihovně známe a **není to epizoda** - tím je hádání
    # skončené. Všechno pod tímhle řádkem jsou dohady z názvu a ty jsou
    # tu jen pro záznamy, u kterých se nedá zjistit nic jiného.
    #
    # Bez téhle zábrany se stalo tohle: film se jmenoval stejně jako díl
    # jiného seriálu (nebo jako seriál sám), takže spadl do jeho skupiny.
    # Skupina se pak jmenovala po tom druhém titulu, ale proklik vedl na
    # tenhle film - popisek říkal jedno a odkaz vedl jinam.
    #
    # Epizoda bez `series_id` je výjimka: tam hádání pomáhá, protože
    # k seriálu ji jinak nemáme jak přiřadit.
    if row.get("existujici_id") and str(row.get("i_type") or "") != "Episode":
        return (f"polozka:{row.get('item_id') or row['existujici_id']}",
                str(row.get("i_name") or row.get("item_name") or "?"), False)

    # Poslední pokus, ještě před vzdáním se: díl toho jména v knihovně
    # existuje a seriál zná. Záznam o něm neví nic a poznat ho z názvu
    # ("Zvony") nejde - ale knihovna ho zná. Bez tohohle z něj v přehledu
    # vznikl samostatný titul vedle seriálu, do kterého patří.
    # Viz `_dily_podle_nazvu()`.
    z_knihovny = (podle_nazvu or {}).get(
        "dil:" + str(row.get("item_name") or "").strip().lower())
    if z_knihovny:
        serial, jmeno = z_knihovny
        return (f"serial:{serial}", jmeno, True)

    nazev_serialu = (row.get("series_name") or row.get("i_series_name")
                     or serial_z_nazvu(row.get("item_name")))
    if nazev_serialu:
        srovnany = str(nazev_serialu).strip().lower()
        # Když ten samý seriál v knihovně známe, použijeme jeho id -
        # jinak by se rozpadl na dva řádky: jeden složený z importované
        # historie (kde je jen název) a druhý z vlastního sběru (kde už
        # je i id). Přesně to bylo vidět v přehledu.
        znamy = (podle_nazvu or {}).get(srovnany)
        if znamy:
            return (f"serial:{znamy}", str(nazev_serialu).strip(), True)

        # Klíč z názvu je slabší než z id, ale pořád sloučí to, co k sobě
        # patří. Porovnává se malými písmeny - "Blue" a "blue" je totéž.
        return (f"nazev:{srovnany}", str(nazev_serialu).strip(), True)

    # A ještě jedna možnost: název záznamu je rovnou jméno seriálu.
    # Stane se to u převzaté historie, která si epizodu nepamatuje,
    # nebo když Jellyfin zaznamená přehrávání na úrovni seriálu.
    jako_serial = (podle_nazvu or {}).get(
        str(row.get("item_name") or "").strip().lower())
    if jako_serial:
        return (f"serial:{jako_serial}", str(row["item_name"]).strip(), True)

    polozka = row.get("item_id") or row.get("item_name") or "?"
    return (f"polozka:{polozka}",
            str(row.get("i_name") or row.get("item_name") or "?"), False)


# Sloupce, ze kterých `klic_titulu()` skládá skupinu. Používají je oba
# dotazy níž (nejsledovanější tituly i překódované soubory), takže je
# lepší mít je na jednom místě než ve dvou kopiích, které se časem
# rozejdou.
#
# Seskupuje se přes SKUPINA_TITULU_KLIC, ne přes holé `p.item_id`: záznam
# bez id položky se v Jellyscope běžně nevyskytuje, ale kdyby se objevil,
# spadly by všechny takové řádky do jedné skupiny a vznikl by titul
# poskládaný z několika různých filmů.
SKUPINA_TITULU_KLIC = "COALESCE(p.item_id, p.item_name)"
SKUPINA_TITULU_SLOUPCE = """
        MAX(p.item_id)             AS item_id,
        MAX(p.item_name)           AS item_name,
        MAX(p.series_name)         AS series_name,
        MAX(i.id)                  AS existujici_id,
        MAX(i.series_id)           AS series_id,
        MAX(i.series_name)         AS i_series_name,
        MAX(i.name)                AS i_name,
        MAX(i.type)                AS i_type,
        MAX(i.is_missing)          AS is_missing
"""


def _slouc_tituly(radky: list[dict[str, Any]], kind: str, limit: int,
                  hodnoty: dict[str, str],
                  prenest: tuple[str, ...] = (),
                  skryt_nezarazene: bool = False) -> list[dict[str, Any]]:
    """Řádky za jednotlivé položky složí do titulů (seriál = jeden řádek).

    `hodnoty` říká, které sloupce se mají sečíst - u nejsledovanějších
    titulů jsou to hodiny a spuštění, u překódovaných počet transcodů.

    `prenest` jsou sloupce, které se nesčítají, ale opisují od nejsilnějšího
    dílu skupiny - kodek, rozlišení, důvod transcode. U seriálu jsou
    prakticky u všech dílů stejné, takže průměrovat je by nic nepřineslo.

    `skryt_nezarazene` vynechá tituly, ke kterým v knihovně **ani v archivu**
    nic nevede. Jsou to zbytky historie, u kterých se nedá zjistit, o co
    šlo: záznam nese jen "6. díl" nebo "Epizoda 6" a takový díl má v knihovně
    každý seriál, takže jednoznačná shoda neexistuje. Přiřadit je naslepo
    by znamenalo připsat sledování cizímu seriálu.

    V žebříčku nejsledovanějších takový řádek nic neříká - nejde
    prokliknout a tváří se jako film, který neexistuje. Kolik jich bylo,
    se vrací v `_skryto`, aby to stránka mohla napsat nahlas; odsledovaný
    čas se tím nikde neztrácí, Historie ho ukazuje dál.

    Slučuje se v Pythonu, ne v SQL: klíč skupiny umí spadnout až na
    rozbor názvu ("Seriál - s01e01 - Díl") a to se v SQL napsat srozumitelně
    nedá ani v jedné z obou databází.
    """
    # Nejdřív si projdeme řádky a zapamatujeme, které jméno seriálu patří
    # ke kterému id. Teprve pak se skládají skupiny - jinak by se stejný
    # seriál rozdělil podle toho, jestli ten který záznam id zná.
    podle_nazvu: dict[str, str] = {}
    for radek in radky:
        if not radek.get("series_id"):
            continue
        for jmeno in (radek.get("series_name"), radek.get("i_series_name"),
                      serial_z_nazvu(radek.get("item_name"))):
            if jmeno:
                podle_nazvu.setdefault(str(jmeno).strip().lower(),
                                       str(radek["series_id"]))

    # Doplníme názvy z knihovny, ne jen z řádků výsledku: seriál, ze
    # kterého je v období jen osiřelý záznam, by jinak v mapě chyběl
    # a neměl by se k čemu přiřadit.
    for serial, jmeno in _jmena_serialu().items():
        podle_nazvu.setdefault(jmeno.strip().lower(), serial)

    # A názvy dílů. Řeší případ, kdy záznam nese jen jméno epizody
    # ("Zvony") a k položce už nic nevede - v přehledu z něj jinak
    # vznikne samostatný "film" vedle seriálu, do kterého patří.
    podle_nazvu.update(_dily_podle_nazvu())

    skupiny: dict[str, dict[str, Any]] = {}

    for radek in radky:
        klic, popisek, je_serial = klic_titulu(radek, podle_nazvu)

        if kind == KIND_MOVIE and je_serial:
            continue
        if kind == KIND_SERIES and not je_serial:
            continue

        skupina = skupiny.get(klic)
        if skupina is None:
            skupina = {"group_key": klic, "label": popisek, "is_series": je_serial,
                       "_zastupci": [], "_nejsilnejsi": (-1.0, {})}
            for cil in hodnoty:
                skupina[cil] = 0.0
            skupiny[klic] = skupina
        elif je_serial and len(popisek) < len(skupina["label"]):
            # Kratší popisek je ten obecnější ("Blue" vs. "Blue - s01e17
            # - Calypso"), a o seriálu mluvíme jeho jménem.
            skupina["label"] = popisek

        vaha = float(radek.get(next(iter(hodnoty.values()))) or 0)
        for cil, zdroj in hodnoty.items():
            skupina[cil] += float(radek.get(zdroj) or 0)
        if vaha > skupina["_nejsilnejsi"][0]:
            skupina["_nejsilnejsi"] = (vaha, radek)

        # Zástupce pro proklik. Bere se i **archivovaná** položka, tedy
        # ta, která v knihovně byla a v Jellyfinu už není: sledování se
        # opravdu stalo, detail se otevře a je na něm vidět, že je titul
        # v archivu. Živá má přednost, proto to první číslo (0/1).
        #
        # Bez archivu by film, který jsi kdysi viděl a od té doby smazal,
        # spadl mezi "nezařazené" a `skryt_nezarazene` by ho vyhodil -
        # zmizela by platná statistika, ne odpad.
        if radek.get("existujici_id"):
            skupina["_zastupci"].append(
                (1 if radek.get("is_missing") else 0,
                 -float(radek.get(next(iter(hodnoty.values()))) or 0),
                 radek["existujici_id"], radek.get("series_id")))

    poradi = next(iter(hodnoty))
    serazene = sorted(skupiny.values(), key=lambda r: r[poradi], reverse=True)

    # Odkaz se skládá až tady, takže "nezařazený" se pozná až po něm.
    # Proto se ořezává na `limit` až nakonec - jinak by skryté řádky
    # ubraly místo těm, které se mají ukázat.
    for skupina in serazene:
        _, nejsilnejsi = skupina.pop("_nejsilnejsi")
        for sloupec in prenest:
            skupina[sloupec] = nejsilnejsi.get(sloupec)

        zastupci = skupina.pop("_zastupci")
        if not zastupci:
            # Žádný záznam téhle skupiny neukazuje na živou položku. Když
            # ale skupinu určil seriál z knihovny, odkaz máme i tak - klíč
            # nese jeho id. Nastane to u historie, kde k dílu už položka
            # nevede, ale sám seriál v knihovně pořád je.
            klic = str(skupina["group_key"])
            skupina["detail_url"] = ("/series/" + klic[len("serial:"):]
                                     if klic.startswith("serial:") else None)
            # Titul, ke kterému už v knihovně není vůbec nic, zůstane
            # bez odkazu - odkaz na chybovou stránku 404 je horší než žádný.
            continue
        # Živé napřed (0 < 1), v rámci toho nejsledovanější (váha je
        # záporná, takže obyčejné vzestupné řazení dá největší první).
        # Odkaz se řídí **klíčem skupiny**, ne nejsilnějším záznamem.
        #
        # Tohle byl nahlášený bug: skupina seriálu se jmenovala správně,
        # ale proklik vedl na cizí položku. Stane se to, když do skupiny
        # spadne záznam, který se k seriálu přiřadil podle názvu - ten
        # `series_id` sám nenese (proto se hádalo z názvu), takže z něj
        # vyšel odkaz na jeho vlastní položku. A protože se zástupce
        # vybírá podle odsledovaného času, stačilo, aby byl takový záznam
        # nejsilnější, a odkaz zamířil docela jinam než popisek.
        #
        # Klíč `serial:<id>` je přitom to nejspolehlivější, co o skupině
        # víme - id seriálu z knihovny. Když ho máme, odkaz nemá být
        # z čeho jiného.
        klic = str(skupina["group_key"])
        if klic.startswith("serial:"):
            skupina["detail_url"] = "/series/" + klic[len("serial:"):]
            continue

        zastupci.sort()
        _, _, polozka, serial = zastupci[0]
        skupina["detail_url"] = (f"/series/{serial}" if serial
                                 else f"/item/{polozka}")

    if not skryt_nezarazene:
        return serazene[:limit]

    zarazene = [s for s in serazene if s["detail_url"]]

    # Když by skrývání nenechalo vůbec nic, neskrývá se. Nastane to
    # v jediné situaci, zato důležité: historie se naimportuje DŘÍV, než
    # se aplikace napojí na Jellyfin. Knihovna je pak prázdná, takže žádný
    # titul nemá protějšek - a člověk by po importu koukal na prázdný
    # žebříček a myslel si, že se nic nenaimportovalo. Jakmile
    # synchronizace proběhne, tituly se navážou a skrývání začne dávat
    # smysl samo.
    if not zarazene:
        return serazene[:limit]

    skryto = len(serazene) - len(zarazene)
    vysledek = zarazene[:limit]
    if vysledek:
        vysledek[0]["_skryto"] = skryto
    return vysledek


def top_items(days: int, limit: int = 10,
              kind: str = KIND_BOTH) -> list[dict[str, Any]]:
    """Nejsledovanejsi tituly. Epizody stejneho serialu se scitaji.

    `kind` je "both" / "movies" / "series" - stejne tri moznosti jako
    u sledovanosti po dnech, at se clovek nemusi ucit dve ruzne ovladani.

    Jak se serial pozna a kam vede proklik, popisuje `klic_titulu()`
    a `_slouc_tituly()`.
    """
    radky = db.query_all(
        f"""
        SELECT {SKUPINA_TITULU_SLOUPCE},
               SUM(p.watched_seconds) AS seconds,
               COUNT(*)               AS plays
        FROM playback p
   LEFT JOIN items i ON i.id = p.item_id
       WHERE p.started_at >= ? AND p.started_at < ?
         AND p.watched_seconds > 0
    GROUP BY {SKUPINA_TITULU_KLIC}
        """,
        (*_meze(days),),
    )

    tituly = _slouc_tituly(radky, kind, limit,
                           {"seconds": "seconds", "plays": "plays"},
                           skryt_nezarazene=True)
    for titul in tituly:
        titul["hours"] = titul["seconds"] / 3600.0
        titul["plays"] = int(titul["plays"])
    return tituly
