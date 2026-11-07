"""Záznamy bez položky: počty, zastaralé názvy, dohledání v Jellyfinu, rozbor a ruční přiřazení.

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db
from ..jellyfin import JellyfinClient, JellyfinError
from .dvojice import misplaced_episode_rows
from .propojeni import _link_by_episode_number, _link_by_name
from .zaklad import _normalizuj_id, rozbor_nazvu
from .zalozeni import zaloz_z_jellyfinu

log = logging.getLogger("jellyscope.importers")


def orphan_playback_count() -> int:
    """Kolik zaznamu historie ukazuje na polozku, ktera v knihovne neni.

    Takovy zaznam se v prehledech nema k cemu zaradit: nevi o serialu,
    nevi o knihovne a proklik z nej nikam nevede. Ve statistikach se pak
    objevi dil "Zvony" jako samostatny titul vedle "Hry o truny", misto
    aby se pod ni sloucil.
    """
    return db.query_value(
        """
        SELECT COUNT(*)
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != ''
           AND i.id IS NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
        """,
        default=0,
    ) or 0


def stale_name_rows() -> int:
    """Kolik zaznamu historie nese jiny nazev, nez ma polozka v knihovne.

    Nazev se u prehravani uklada **spolu se zaznamem** (denormalizovane),
    aby historie smazaneho titulu nezustala bezejmenna. Ma to ale rub:
    kdyz se titul pozdeji **prejmenuje**, stary zaznam si nese puvodni
    nazev navzdy.

    Nejcastejsi pripad neni prejmenovani z rozmaru, ale oprava spatne
    urcenych metadat: Jellyfin soubor identifikuje jako jiny film, ty se
    na nej v tom stavu podivas, pak metadata v Jellyfinu spravis - a v
    Jellyscope zustane u toho prehravani cizi nazev. Ve statistikach to
    vypada, jako by k jednomu id patril nazev druheho titulu, a proklik
    vede "nekam jinam", nez rika popisek.
    """
    return db.query_value(
        """
        SELECT COUNT(*)
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.item_name IS NOT NULL AND p.item_name != ''
           AND (p.item_name != i.name
                OR COALESCE(p.series_name, '') != COALESCE(i.series_name, ''))
        """,
        default=0,
    ) or 0


def sjednot_nazvy() -> dict[str, Any]:
    """Prepise nazvy v historii podle knihovny.

    Meni se **jen zaznamy, jejichz polozka v knihovne existuje**. U
    osirelych se nazev nechava, jak je - je to jedina informace o tom,
    co se prehravalo, a bez ni by z nich zbylo prazdne misto.

    Proc po jednotlivych polozkach a ne jednim UPDATE ... FROM: tuhle
    konstrukci zna SQLite az od verze 3.33 a v PostgreSQL se pise jinak.
    Nesedicich titulu je pritom hrstka, takze na tom nesejde.
    """
    kandidati = db.query_all(
        """
        SELECT p.item_id, i.name AS spravny, i.series_name AS spravny_serial,
               COUNT(*) AS radku
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.item_name IS NOT NULL AND p.item_name != ''
           AND (p.item_name != i.name
                OR COALESCE(p.series_name, '') != COALESCE(i.series_name, ''))
      GROUP BY p.item_id, i.name, i.series_name
        """
    )
    if not kandidati:
        return {"items": 0, "rows": 0}

    radku = 0
    with db.connect() as conn:
        for row in kandidati:
            cursor = conn.execute(
                "UPDATE playback SET item_name = ?, series_name = ?"
                " WHERE item_id = ?",
                (row["spravny"], row["spravny_serial"], row["item_id"]),
            )
            radku += cursor.rowcount or 0

    log.info("srovnano nazvu v historii: %s polozek, %s radku",
             len(kandidati), radku)
    return {"items": len(kandidati), "rows": radku}


def orphan_items_count() -> int:
    """Kolik RUZNYCH titulu je osirelych - ne kolik radku historie.

    Radku byva nekolikanasobne vic (jeden dil clovek pusti desetkrat),
    takze samotne cislo radku vypada hur, nez jaka je skutecnost.
    """
    return int(db.query_value(
        """
        SELECT COUNT(*) FROM (
            SELECT p.item_id
              FROM playback p
         LEFT JOIN items i ON i.id = p.item_id
             WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
               AND p.item_name IS NOT NULL AND p.item_name != ''
          GROUP BY p.item_id
        ) AS t
        """,
        default=0,
    ) or 0)


# Kolik osirelych id se najednou pta Jellyfinu. Vic nez tisic titulu
# osirele historie nebyva a jeden dotaz zvladne padesat, takze je to
# strop pro jistotu, ne pro vykon.
DOHLEDAT_NEJVYS = 1000


async def dohledej_osirele_v_jellyfinu() -> dict[str, Any]:
    """Osirele zaznamy zkusi navazat pomoci samotneho Jellyfinu.

    Proc to jde: identifikator v prevzate historii **je pravy Jellyfin
    ItemId**. Import ho zna, jen k nemu u nas nic nevede - typicky proto,
    ze Jellystat nese jen nazev dilu ("7. epizoda", "Pilot") a o serialu
    nerekne nic. Takovy nazev ma kazdy serial, takze parovani podle jmena
    ho odmita zaradit - a delá dobre.

    Jellyfin ale to id zna. Staci se zeptat (GET /Items?Ids=...) a je
    z toho serial, rada i cislo dilu - tedy presne to, co k jednoznacnemu
    zarazeni chybelo.

    Delaji se dve veci:
      1. **doplni se serial a nazev** do zaznamu historie. Uz tim prestane
         byt dil v prehledech samostatnym "filmem" a slouci se pod svuj
         serial,
      2. kdyz se v knihovne najde tentyz dil (podle tmdb + rada + dil,
         nebo podle id serialu a cisel), zaznam se na nej **navaze**.
         Tim ziska i proklik.

    Do Jellyfinu se jen cte. Kdyz uz tam titul neni, zaznam se nechava,
    jak byl - je to platna historie, jen k ni nic nevede.
    """
    kandidati = db.query_all(
        """
        SELECT p.item_id, MAX(p.item_name) AS item_name
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
      GROUP BY p.item_id
         LIMIT ?
        """,
        (DOHLEDAT_NEJVYS,),
    )
    if not kandidati:
        return {"status": "ok", "dotazano": 0, "nalezeno": 0,
                "navazano": 0, "zalozeno": 0, "doplneno": 0, "radku": 0}

    ids = [str(k["item_id"]) for k in kandidati]
    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            nalezene = await client.items_by_ids(ids)
    except JellyfinError as exc:
        return {"status": "error", "message": str(exc)}

    vysledek = zaloz_z_jellyfinu(nalezene)
    log.info("dohledano v Jellyfinu: %s z %s dotazanych, navazano %s,"
             " zalozeno %s, doplnen serial u %s",
             len(nalezene), len(ids), vysledek["navazano"],
             vysledek["zalozeno"], vysledek["doplneno"])
    return {"status": "ok", "dotazano": len(ids), "nalezeno": len(nalezene),
            **vysledek}


# Duvody, proc se zaznam nepodarilo zaradit. Poradi je od "da se s tim
# neco delat" k "uz opravdu nic" - v tom poradi se i vypisuji.
DUVOD_ID = "id"
DUVOD_JEDNA_SHODA = "jedna_shoda"
DUVOD_VIC_SHOD = "vic_shod"
DUVOD_SERIAL_JE = "serial_je"
DUVOD_SERIAL_NENI = "serial_neni"
DUVOD_ZNA_SERIAL = "zna_serial"
DUVOD_NIC = "nic"

# Co ktery duvod znamena a jestli se s tim da neco delat. Texty jsou
# tady, ne v sablone: patri k tomu rozdeleni, ne k jeho vykresleni.
DUVODY_POPIS: dict[str, dict[str, Any]] = {
    DUVOD_ID: {
        "nadpis": "Identifikátor sedí, jen jinak zapsaný",
        "popis": "Titul v knihovně je, jen se jeho id píše jinak (s pomlčkami nebo bez nich). "
                 "Spraví to Uklidit historii.",
        "resitelne": True,
    },
    DUVOD_JEDNA_SHODA: {
        "nadpis": "Název sedí přesně na jeden titul",
        "popis": "Mělo se navázat samo. Když to po úklidu zůstane, "
                 "je to chyba a stojí za nahlášení.",
        "resitelne": True,
    },
    DUVOD_VIC_SHOD: {
        "nadpis": "Název sedí na víc titulů",
        "popis": "Typicky „7. epizoda“ nebo „Pilot“: takový díl má každý seriál. Stroj hádat "
                 "nesmí, ty ale poznáš, kam patří. Zkus Dohledat v Jellyfinu, nebo ho přiřaď "
                 "ručně.",
        "resitelne": True,
    },
    DUVOD_SERIAL_JE: {
        "nadpis": "Seriál v knihovně je, díl nesedí",
        "popis": "Název nese seriál i číslo dílu, ale takový díl v knihovně není, protože se "
                 "číslování mezi zdroji rozchází. Ruční přiřazení to vyřeší.",
        "resitelne": True,
    },
    DUVOD_ZNA_SERIAL: {
        "nadpis": "Seriál známe, díl ne",
        "popis": "Záznam ví, ze kterého seriálu je, ale díl toho jména "
                 "v knihovně není.",
        "resitelne": True,
    },
    DUVOD_SERIAL_NENI: {
        "nadpis": "Seriál v knihovně není",
        "popis": "Seriál jsi nejspíš smazal. Historie zůstává platná, jen "
                 "k ní nic nevede.",
        "resitelne": False,
    },
    DUVOD_NIC: {
        "nadpis": "Není z čeho vyjít",
        "popis": "Zůstal jen název a ten se v knihovně nikde neopakuje. "
                 "Titul už v knihovně není.",
        "resitelne": False,
    },
}


# Kolik osirelych titulu se rozebira najednou. Vic uz neni seznam,
# ale vypis - a stranka by se stala necitelnou.
ROZBOR_NEJVYS = 500


def rozbor_osirelych(limit: int = ROZBOR_NEJVYS) -> list[dict[str, Any]]:
    """Osirele zaznamy i s duvodem, proc se nepovedlo je zaradit.

    Pocita se pokazde znovu, nikam se to neuklada. Ulozeny seznam by
    ukazoval stav po poslednim uklidu - tedy neco, co uz nemusi platit,
    protoze mezitim probehla synchronizace nebo dalsi import. Radeji
    o par dotazu vic a jistotu, ze se clovek diva na pravdu.

    Vraci jeden radek na titul (ne na zaznam historie): jeden dil clovek
    pusti desetkrat a v seznamu, kde ma neco poznat podle nazvu, je to
    desetkrat tentyz radek.
    """
    osirele = db.query_all(
        """
        SELECT p.item_id,
               MAX(p.item_name)   AS item_name,
               MAX(p.series_name) AS series_name,
               MAX(p.item_type)   AS item_type,
               COUNT(*)           AS radku,
               MIN(p.started_at)  AS od,
               MAX(p.started_at)  AS do
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
      GROUP BY p.item_id
      ORDER BY COUNT(*) DESC
         LIMIT ?
        """,
        (limit,),
    )
    if not osirele:
        return []

    # Knihovnu si nacteme jednou do pameti. Dotaz na kazdy titul zvlast
    # by pri par stech osirelych znamenal tisice dotazu.
    podle_nazvu: dict[str, list[dict[str, Any]]] = {}
    podle_id: set[str] = set()
    serialy: set[str] = set()
    for radek in db.query_all(
            "SELECT id, name, series_name FROM items WHERE name IS NOT NULL"):
        podle_nazvu.setdefault(str(radek["name"]).strip().lower(), []).append(radek)
        podle_id.add(_normalizuj_id(radek["id"]))
        if radek["series_name"]:
            serialy.add(str(radek["series_name"]).strip().lower())

    vysledek = []
    for row in osirele:
        nazev = str(row["item_name"] or "").strip()
        shody = podle_nazvu.get(nazev.lower(), [])
        rozbor = rozbor_nazvu(nazev)
        serial_ze_zaznamu = str(row["series_name"] or "").strip()

        if _normalizuj_id(row["item_id"]) in podle_id:
            duvod = DUVOD_ID
        elif len(shody) == 1:
            duvod = DUVOD_JEDNA_SHODA
        elif len(shody) > 1:
            duvod = DUVOD_VIC_SHOD
        elif rozbor and rozbor["serial"].lower() in serialy:
            duvod = DUVOD_SERIAL_JE
        elif rozbor:
            duvod = DUVOD_SERIAL_NENI
        elif serial_ze_zaznamu and serial_ze_zaznamu.lower() in serialy:
            duvod = DUVOD_ZNA_SERIAL
        else:
            duvod = DUVOD_NIC

        vysledek.append({**row, "duvod": duvod, "shod": len(shody),
                         "serial_z_nazvu": rozbor["serial"] if rozbor else None})
    return vysledek


def kandidati_pro_osireleho(hledat: str, limit: int = 25) -> list[dict[str, Any]]:
    """Polozky z knihovny, ze kterych se da vybrat pri rucnim prirazeni."""
    hledat = (hledat or "").strip()
    if len(hledat) < 2:
        return []
    vzor = f"%{hledat}%"
    return db.query_all(
        """
        SELECT i.id, i.name, i.series_name, i.type, i.parent_index_number,
               i.index_number, i.production_year, i.is_missing,
               l.name AS library_name
          FROM items i
     LEFT JOIN libraries l ON l.id = i.library_id
         WHERE i.name LIKE ? OR i.series_name LIKE ?
      ORDER BY i.is_missing, i.series_name, i.parent_index_number,
               i.index_number, i.name
         LIMIT ?
        """,
        (vzor, vzor, limit),
    )


def prirad_rucne(stare_id: str, cilove_id: str) -> dict[str, Any]:
    """Rucne prepise osirele zaznamy na vybranou polozku z knihovny.

    Posledni zachrana pro pripad, kdy stroj poznat nemuze, ale clovek
    ano - typicky u dilu, ktery se jmenuje "7. epizoda" a v knihovne
    jich takovych je patnact.
    """
    cil = db.query_one("SELECT id, name, series_name FROM items WHERE id = ?",
                       (cilove_id,))
    if cil is None:
        return {"status": "error", "message": "Taková položka v knihovně není."}

    with db.connect() as conn:
        cursor = conn.execute(
            """UPDATE playback SET item_id = ?, item_name = ?, series_name = ?
                WHERE item_id = ?""",
            (cil["id"], cil["name"], cil["series_name"], stare_id),
        )
    radku = cursor.rowcount or 0
    if not radku:
        return {"status": "error", "message": "Ty záznamy už nikde nejsou."}

    log.info("rucne prirazeno: %s -> %s (%s radku)", stare_id, cil["id"], radku)
    return {"status": "ok", "rows": radku, "name": cil["name"]}


def relink_orphans() -> dict[str, Any]:
    """Osirele zaznamy historie zkusi navazat na polozky podle nazvu.

    Pouziva stejne parovani jako import (`_link_by_name`) - jednoznacna
    shoda nazvu, jinak nic. Tady se ale hodi i mimo import: zaznamy
    osiri i tim, ze se soubor v Jellyfinu smaze a znovu prida s novym
    ItemId, nebo ze se historie prevzala driv, nez probehla prvni
    synchronizace knihovny.
    """
    # Nejdřív podle čísla dílu - to je jednoznačné. Teprve pak podle
    # holého názvu, kde se dá jen doufat, že je jedinečný.
    polozek, radku = _link_by_episode_number()
    jmenem_polozek, jmenem_radku = _link_by_name()
    polozek += jmenem_polozek
    radku += jmenem_radku

    if polozek:
        log.info("navazano %d polozek historie (%d radku)", polozek, radku)
    return {"status": "ok", "items": polozek, "rows": radku}


def repair_episode_links() -> dict[str, Any]:
    """Vrati slitou historii epizod zpatky k dilum, ke kterym patri."""
    opravy = misplaced_episode_rows()
    if not opravy:
        return {"status": "ok", "moved": 0}

    with db.connect() as conn:
        for oprava in opravy:
            conn.execute("UPDATE playback SET item_id = ? WHERE id = ?",
                         (oprava["spravny_dil"], oprava["id"]))

    log.info("vraceno %d zaznamu ke spravnym dilum", len(opravy))
    return {"status": "ok", "moved": len(opravy)}
