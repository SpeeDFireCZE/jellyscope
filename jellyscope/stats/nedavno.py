"""Nedávno přidané tituly a dávky dílů.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db


# Nejnovejsi skupiny (serial, nebo film sam za sebe) - prvni krok
# recently_added(). Jeden text pro oba dotazy, ktere ho potrebuji.
_NEJNOVEJSI_SKUPINY = """
        SELECT COALESCE(i.series_id, i.id) AS klic,
               MAX(i.date_created)         AS pridano
          FROM items i
         WHERE i.is_missing = 0
           AND i.date_created IS NOT NULL
      GROUP BY COALESCE(i.series_id, i.id)
      -- Pri stejnem datu rozhoduje klic, stejne jako ve vnejsim dotazu
      -- (i.id) - jinak by limit vybral jine tituly nez cely seznam.
      ORDER BY MAX(i.date_created) DESC, COALESCE(i.series_id, i.id)
         LIMIT ?
"""


def recently_added(limit: int = 18) -> list[dict[str, Any]]:
    """Naposledy pridane tituly.

    Serial se ma objevit jednou, ne desetkrat za kazdou epizodu.

    Vybira se ve dvou krocich a ma to dobry duvod. Drive se vzal pevny
    pocet nejnovejsich RADKU (limit * 6) a teprve ty se seskupily. Jenze
    kdyz se v Jellyfinu znovu zalozi serial o dvou stech dilech, dostanou
    vsechny dily dnesni datum - a tech sto osm radku je rovnou cely
    zaplni. Ve vypisu pak zbyl jediny serial a vsechno ostatni zmizelo,
    aniz by se kdy vratilo.

    Proto se nejdriv vyberou nejnovejsi SKUPINY (serial nebo film) a az
    potom se k nim dotahnou radky. Kolik dilu ma ktery serial, tim
    prestane hrat roli.
    """
    skupiny_radky = db.query_all(_NEJNOVEJSI_SKUPINY, (limit,))
    if not skupiny_radky:
        return []

    # Starsi nez tohle uz nemuze patrit do zadne z davek, ktere budeme
    # vypisovat - viz _ve_stejne_davce(). Bez toho omezeni by serial
    # o peti stovkach dilu dotahl vsechny.
    nejstarsi = min(str(radek["pridano"]) for radek in skupiny_radky)
    hranice = _o_hodin_zpet(nejstarsi, DAVKA_HODIN)

    # Skupiny se do druheho dotazu davaji poddotazem, ne seznamem klicu
    # `IN (?, ?, ...)`: API si s filtrem rika o celou knihovnu a SQLite
    # snese nejvys 32 766 parametru - nad tolika filmy dotaz spadl.
    rows = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_id, i.series_name,
               i.production_year, i.date_created, i.height, i.size_bytes,
               i.library_id, i.parent_index_number, i.index_number,
               i.image_tag, i.series_image_tag, i.path, i.tmdb_id,
               l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.is_missing = 0
          AND i.date_created IS NOT NULL
          AND i.date_created >= ?
          AND COALESCE(i.series_id, i.id) IN (
              SELECT klic FROM ({_NEJNOVEJSI_SKUPINY}) vybrane)
        -- Remizu mezi skupinami rozhoduje klic skupiny jako ve vnitrnim
        -- dotazu - jinak by serial a film pridane naraz sly s limitem
        -- v jinem poradi nez bez nej.
        ORDER BY i.date_created DESC, COALESCE(i.series_id, i.id), i.id
        """,
        (hranice, limit),
    )

    poradi: list[str] = []
    skupiny: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        # Klic skupiny: serial, nebo film sam za sebe.
        key = str(row["series_id"] or row["id"])
        if key not in skupiny:
            if len(skupiny) >= limit:
                continue
            poradi.append(key)
            skupiny[key] = []
        skupiny[key].append(row)

    result: list[dict[str, Any]] = []
    for key in poradi:
        dily = skupiny[key]
        nejnovejsi = dily[0]
        is_episode = bool(nejnovejsi["series_id"])

        # Kolik dilu prislo "v jedne davce". Cela sezona nedorazi v jednu
        # vterinu, ale ani se nerozlozi do tydne - okno jednoho dne od
        # nejnovejsiho dilu je poctivy odhad. Bez nej by se ve vypisu
        # objevily i dily pridane pred mesicem, jen proto, ze u nich uz
        # dlouho zadny dalsi nepribyl.
        davka = [d for d in dily if _ve_stejne_davce(nejnovejsi["date_created"],
                                                     d["date_created"])]
        davka = _serad_dily(davka)

        result.append({
            **nejnovejsi,
            "title": nejnovejsi["series_name"] if is_episode else nejnovejsi["name"],
            # Odkaz vede na konkretni polozku - u serialu na tu nejnovejsi
            # epizodu, coz je presne to, co uzivatele zajima.
            "poster_id": nejnovejsi["series_id"] or nejnovejsi["id"],
            # Otisk toho plakatu. Bez nej drzi prohlizec starou verzi
            # obrazku i po tom, co ji smazeme z mezipameti - adresa je
            # totiz porad tatáž.
            "poster_tag": (nejnovejsi["series_image_tag"] if nejnovejsi["series_id"]
                           else nejnovejsi["image_tag"]),
            "is_episode": is_episode,
            # Ostatni dily te davky. Kdyz prisel jen jeden, seznam je
            # prazdny a karta se chova jako driv - proklik rovnou na nej.
            "episodes": davka if is_episode and len(davka) > 1 else [],
            "series_url": f"/series/{nejnovejsi['series_id']}" if is_episode else None,
        })

    return result


def _serad_dily(davka: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serad davku od prvniho dilu k poslednimu a zahod duplicity.

    Razeni: seznam se cte jako seznam dilu, a ten se cte odshora dolu -
    S01E01 nahore. Podle data pridani to nesedi; Jellyfin nacte soubory
    v poradi, v jakem je najde na disku, takze davka prijde zamichana.

    Duplicity: tentyz dil se ve vypisu objevil dvakrat. Muze za to
    knihovna, kde je epizoda ve dvou souborech (ruzna kvalita, zbyla
    stara verze) - v Jellyfinu jsou to dve polozky s vlastnim ItemId
    a obe do davky doopravdy patri. Ve vypisu novinek je ale takovy
    radek dvakrat jen matouci, takze necháme ten, ktery uz ma zmerena
    technicka data (a pri shode novejsi z nich).
    """
    def poradi(d: dict[str, Any]) -> tuple[Any, ...]:
        # None az na konec: dil bez cisla se nema vecpat pred S01E01.
        rada, cislo = d["parent_index_number"], d["index_number"]
        return (rada is None, rada or 0, cislo is None, cislo or 0,
                str(d["name"] or ""))

    nejlepsi: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for d in davka:
        klic = (d["series_id"], d["parent_index_number"], d["index_number"])
        if klic[1] is None and klic[2] is None:
            # Dil bez cisel se s nicim slucovat nesmi - bez cisla neni
            # jak poznat, jestli jde o tentyz dil, nebo o jiny.
            klic = (d["id"], None, None)
        stavajici = nejlepsi.get(klic)
        if stavajici is None or _prednost_dilu(d) > _prednost_dilu(stavajici):
            nejlepsi[klic] = d

    return sorted(nejlepsi.values(), key=poradi)


def _prednost_dilu(d: dict[str, Any]) -> tuple[int, str]:
    """Ktera ze dvou polozek tehoz dilu se ma ukazat."""
    ma_data = 1 if (d.get("height") or d.get("size_bytes")) else 0
    return (ma_data, str(d["date_created"] or ""))


# Jak daleko od sebe smi byt dily, aby se povazovaly za jednu davku.
DAVKA_HODIN = 24


def _o_hodin_zpet(cas: Any, hodin: int) -> str:
    """Cas posunuty o zadany pocet hodin zpet, ve tvaru z databaze.

    Pouziva se jako spodni hranice dotazu. Kdyz se cas neda precist,
    vraci se prazdny retezec - ten je v porovnani mensi nez cokoliv,
    takze se radeji nevyfiltruje nic.
    """
    # OverflowError: Jellyfin umí poslat DateCreated 0001-01-01 (MinValue)
    # a o den dřív už žádné datum není.
    try:
        moment = datetime.strptime(str(cas).replace("T", " ")[:19], db.TIME_FORMAT)
        return (moment - timedelta(hours=hodin)).strftime(db.TIME_FORMAT)
    except (TypeError, ValueError, OverflowError):
        return ""


def _ve_stejne_davce(nejnovejsi: Any, zkoumany: Any) -> bool:
    """Prisel tenhle dil spolu s tim nejnovejsim?"""
    try:
        a = datetime.strptime(str(nejnovejsi).replace("T", " ")[:19], db.TIME_FORMAT)
        b = datetime.strptime(str(zkoumany).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return False
    return abs((a - b).total_seconds()) <= DAVKA_HODIN * 3600
