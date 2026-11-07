"""Založení chybějících položek z Jellyfinu (díly pod seriálem, knihovny podle cest).

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import json
import logging
from typing import Any

from .. import db, scanner
from ..jellyfin import extract_streams as jellyfin_streams, extract_tech_from_item as jellyfin_tech
from .zaklad import rozbor_nazvu

log = logging.getLogger("jellyscope.importers")


def zaloz_z_jellyfinu(nalezene: list[dict[str, Any]]) -> dict[str, int]:
    """Z odpovedi Jellyfinu udela to, co osirelym zaznamum chybi.

    Dva kroky na kazdou polozku:

      1. **Tentyz dil uz v knihovne mame, jen pod jinym ItemId** (soubor
         se prekodoval, knihovna se prescanovala) - zaznamy se prepisou
         na nej a ziskaji tim proklik na spravny detail.
      2. **V knihovne neni, ale Jellyfin ho zna - zalozime ho.** Tohle je
         to podstatne: id v prevzate historii **je** to Jellyfin id, takze
         jakmile polozka existuje, zaznamy na ni ukazuji samy od sebe.
         A s polozkou prijde tmdb_id, serial, cisla dilu i technicka data,
         takze zacne fungovat vsechno ostatni - slucovani podle tmdb,
         zarazeni pod serial, statistiky, proklik.

    Polozka bez knihovny neni tragedie: pri prvni plne synchronizaci se
    doplni, protoze ta uz vi, ze ktere knihovny polozka prisla. A kdyz
    v Jellyfinu mezitim zmizi, oznaci se jako archivovana - stejne jako
    kterakoliv jina.

    **Zaklada se jen film nebo dil** (`scanner.SPRAVOVANE_TYPY`). Kdyz
    zaznam visi na id SERIALU - a to se u prevzate historie stava -
    Jellyfin odpovi polozkou druhu "Series". Takovou zalozit nesmime:
    synchronizace se pta jen na filmy a dily, tuhle polozku by uz nikdy
    nevidela a `_mark_missing()` by ji pri kazdem behu poslala do
    archivu. V knihovne by pak strasil "archivovany" serial, ktery
    v Jellyfinu normalne je (a vedle nej ten spravny, poskladany z dilu).

    Misto zakladani se ze serialu vezme aspon jeho **jmeno** a doplni se
    do zaznamu historie. Tim se dil prestane tvarit jako samostatny film
    a v prehledech se slouci pod svuj serial - i kdyz na konkretni dil
    navazat nejde.
    """
    knihovny = _knihovny_podle_cest()
    use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"

    navazano = zalozeno = doplneno = radku = 0
    for item in nalezene:
        item_id = str(item.get("Id") or "")
        if not item_id:
            continue

        # Kolik zaznamu na to id jeste visi. Kdyz zadny, neni co resit -
        # Jellyfin muze vratit i polozku, kterou mezitim navazal nekdo
        # jiny, a zakladat ji kvuli nicemu by bylo horsi nez nedelat nic.
        ceka = int(db.query_value(
            "SELECT COUNT(*) FROM playback WHERE item_id = ?", (item_id,),
            default=0) or 0)
        if not ceka:
            continue
        if db.query_one("SELECT id FROM items WHERE id = ?", (item_id,)):
            continue

        # Serial nebo rada: nemaji soubor, synchronizace je nezna a jako
        # polozka knihovny by skoncily v archivu. Viz docstring.
        if str(item.get("Type") or "") not in scanner.SPRAVOVANE_TYPY:
            vysledek = _dily_pod_serialem(item_id, item)
            navazano += vysledek["navazano"]
            doplneno += vysledek["doplneno"]
            radku += vysledek["radku"]
            continue

        cil = _dil_v_knihovne(item)
        if cil:
            with db.connect() as conn:
                cursor = conn.execute(
                    """UPDATE playback SET item_id = ?, item_name = ?,
                                           series_name = ?
                        WHERE item_id = ?""",
                    (cil["id"], cil["name"], cil["series_name"], item_id),
                )
            if cursor.rowcount:
                navazano += 1
                radku += cursor.rowcount
            continue

        tech = jellyfin_tech(item) if use_jellyfin_tech else {}
        radek = scanner._radek_polozky(
            item, _knihovna_pro(item, knihovny), tech, db.utcnow())
        scanner._write_items([radek], keep_existing_tech=not use_jellyfin_tech)
        if use_jellyfin_tech:
            stopy = jellyfin_streams(item)
            if stopy:
                scanner.save_streams(item_id, stopy)

        zalozeno += 1
        radku += ceka

    return {"navazano": navazano, "zalozeno": zalozeno,
            "doplneno": doplneno, "radku": radku}


def _dily_pod_serialem(series_id: str, item: dict[str, Any]) -> dict[str, int]:
    """Zaznamy visici na id SERIALU zkusi zaradit ke konkretnim dilum.

    Tohle je ta cast, kvuli ktere ma dohledani smysl i u serialu. Zaznam
    z prevzate historie casto nese id serialu (ne dilu) a k tomu nazev
    typu "Kancelar - S02E05 - Nakup" nebo "5. dil". Samotny nazev je
    k nicemu - "5. dil" ma kazdy serial - ale **ted uz vime, o ktery
    serial jde**, takze se hleda jen mezi jeho dily. A tam uz to obvykle
    vyjde jednoznacne.

    Postupuje se po JEDNOTLIVYCH radcich, ne hromadne pres item_id: pod
    jednim id serialu visi ruzne dily, kazdy s jinym nazvem.

    Kdyz dil urcit nejde, zbyde aspon jmeno serialu - i to je zlepseni,
    protoze zaznam se tim v prehledech prestane tvarit jako samostatny
    film a slouci se pod svuj serial.
    """
    jmeno_serialu = str(item.get("Name") or "").strip()

    # Dily toho serialu, jak je znama knihovna. Bereme i archivovane -
    # historie na ne odkazovat smi, jen uz nejdou prehrat.
    epizody = db.query_all(
        """
        SELECT id, name, parent_index_number AS rada, index_number AS dil
          FROM items
         WHERE type = 'Episode' AND series_id = ?
        """,
        (series_id,),
    )
    podle_cisla: dict[tuple[int, int], dict[str, Any]] = {}
    podle_nazvu: dict[str, list[dict[str, Any]]] = {}
    for radek in epizody:
        if radek["rada"] is not None and radek["dil"] is not None:
            podle_cisla.setdefault((int(radek["rada"]), int(radek["dil"])), radek)
        klic = str(radek["name"] or "").strip().lower()
        if klic:
            podle_nazvu.setdefault(klic, []).append(radek)

    zaznamy = db.query_all(
        "SELECT id, item_name FROM playback WHERE item_id = ?", (series_id,))

    navazano = doplneno = radku = 0
    with db.connect() as conn:
        for zaznam in zaznamy:
            nazev = str(zaznam["item_name"] or "").strip()
            cil = None

            rozbor = rozbor_nazvu(nazev)
            if rozbor:
                cil = podle_cisla.get((rozbor["rada"], rozbor["dil"]))
            if cil is None:
                shody = podle_nazvu.get(nazev.lower(), [])
                # Vic dilu stejneho jmena v jednom serialu se stava
                # (dvoudilne epizody). Hadat nesmime - spatne prirazena
                # historie je horsi nez neprirazena.
                cil = shody[0] if len(shody) == 1 else None

            if cil is not None:
                conn.execute(
                    """UPDATE playback SET item_id = ?, item_name = ?,
                                           series_name = ?, item_type = 'Episode'
                        WHERE id = ?""",
                    (cil["id"], cil["name"], jmeno_serialu or None, zaznam["id"]),
                )
                navazano += 1
                radku += 1
            elif jmeno_serialu:
                # Dil neurcime, ale serial ano.
                cursor = conn.execute(
                    """UPDATE playback SET series_name = ?
                        WHERE id = ? AND (series_name IS NULL OR series_name = ''
                                          OR series_name != ?)""",
                    (jmeno_serialu, zaznam["id"], jmeno_serialu),
                )
                if cursor.rowcount:
                    doplneno += 1
                    radku += cursor.rowcount

    if navazano or doplneno:
        log.info("serial %s: navazano %s dilu, u %s zbylo jen jmeno serialu",
                 jmeno_serialu or series_id, navazano, doplneno)
    return {"navazano": navazano, "doplneno": doplneno, "radku": radku}


def _knihovny_podle_cest() -> list[tuple[str, str]]:
    """Dvojice (cesta na disku, id knihovny), od nejdelsi cesty.

    Jellyfin u polozky neposila, do ktere knihovny patri - posila cestu
    k souboru. Knihovny svoje cesty znaji, takze se to da priradit podle
    zacatku cesty. Od nejdelsi proto, aby u dvou knihoven, kde je jedna
    podslozkou druhe, vyhrala ta konkretnejsi.
    """
    dvojice: list[tuple[str, str]] = []
    for radek in db.query_all("SELECT id, paths FROM libraries"):
        try:
            cesty = json.loads(radek["paths"] or "[]")
        except ValueError:
            continue
        for cesta in cesty if isinstance(cesty, list) else []:
            if cesta:
                dvojice.append((str(cesta).rstrip("/\\"), str(radek["id"])))
    return sorted(dvojice, key=lambda d: len(d[0]), reverse=True)


def _knihovna_pro(item: dict[str, Any],
                  knihovny: list[tuple[str, str]]) -> str | None:
    """Do ktere knihovny polozka patri. None, kdyz to nejde poznat.

    Prazdna knihovna neni tragedie: pri prvni plne synchronizaci se
    doplni, protoze ta uz vi, ze ktere knihovny polozka prisla.
    """
    cesta = str(item.get("Path") or "")
    for zacatek, library_id in knihovny:
        if zacatek and cesta.startswith(zacatek):
            return library_id
    return None


def _dil_v_knihovne(item: dict[str, Any]) -> dict[str, Any] | None:
    """Najde v knihovne tentyz dil, jaky Jellyfin prave popsal.

    Nejdriv podle identity (tmdb serialu + rada + dil) - to je totez
    meritko, jakym se poznava prekodovany soubor. Kdyz tmdb chybi,
    zkusi se id serialu a cisla. Kdyz vyjde vic nez jeden, nesahame na
    to: spatne prirazena historie je horsi nez ta osirela.
    """
    rada = item.get("ParentIndexNumber")
    dil = item.get("IndexNumber")
    if rada is None or dil is None:
        return None

    identita = scanner.identita_polozky(item)
    if identita:
        tmdb, r, d = identita
        shody = db.query_all(
            """SELECT id, name, series_name FROM items
                WHERE tmdb_id = ? AND COALESCE(parent_index_number, -1) = ?
                  AND COALESCE(index_number, -1) = ? LIMIT 2""",
            (tmdb, r, d),
        )
        if len(shody) == 1:
            return shody[0]

    serial = item.get("SeriesId")
    if serial:
        shody = db.query_all(
            """SELECT id, name, series_name FROM items
                WHERE series_id = ? AND parent_index_number = ?
                  AND index_number = ? LIMIT 2""",
            (serial, rada, dil),
        )
        if len(shody) == 1:
            return shody[0]

    nazev_serialu = item.get("SeriesName")
    if nazev_serialu:
        shody = db.query_all(
            """SELECT id, name, series_name FROM items
                WHERE LOWER(series_name) = LOWER(?) AND parent_index_number = ?
                  AND index_number = ? LIMIT 2""",
            (nazev_serialu, rada, dil),
        )
        if len(shody) == 1:
            return shody[0]
    return None
