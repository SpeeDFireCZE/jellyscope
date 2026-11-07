"""Přiřazení importované historie k položkám: id, TMDB, název, číslo dílu.

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, scanner
from ..jellyfin import JellyfinClient, JellyfinError
from .zaklad import _normalizuj_id, rozbor_nazvu
from .zalozeni import zaloz_z_jellyfinu

log = logging.getLogger("jellyscope.importers")


# ---------------------------------------------------------------------------
# Dohledani polozek k importovane historii
# ---------------------------------------------------------------------------
#
# Ani Playback Reporting, ani Jellystat neposilaji tmdb ID. Posilaji ItemId
# z Jellyfinu - jenze ten se meni pokazde, kdyz soubor prekodujes nebo
# presunes. Importovana historie tak casto ukazuje na polozky, ktere uz
# v knihovne nejsou pod tim jmenem.
#
# Bez dohledani by takove zaznamy zustaly viset ve vzduchu: v historii
# by byly videt, ale v knihovne by k nim nic nevedlo a do statistik podle
# knihovny nebo kodeku by se nezapocitaly.
#
# Dohledavame ve dvou krocich, od nejspolehlivejsiho:
#
#   1. **Podle tmdb ID.** Zeptame se Jellyfinu na to stare ItemId. Kdyz
#      ho jeste zna, dostaneme ProviderIds a v nich tmdb. Pak uz jen
#      najdeme polozku se stejnym tmdb v nasi knihovne.
#
#   2. **Podle nazvu.** Kdyz Jellyfin id nezna (soubor je pryc uplne),
#      zkusime shodu nazvu. Parujeme jen tehdy, kdyz je vysledek
#      **jednoznacny** - u dvou stejne pojmenovanych titulu radeji
#      nespojime nic nez spatne.


def _sjednot_identifikatory() -> dict[str, int]:
    """Prepise identifikatory z importu do tvaru, ktery pouziva knihovna.

    Delame to jednorazovym prepisem v historii, ne chytrejsim porovnanim
    v kazdem dotazu. Duvod: takhle se to sjednoti jednou a **vsechny**
    ostatni dotazy v aplikaci uz muzou zustat obycejne. Kdybychom misto
    toho vsude porovnavali "az na pomlcky", museli bychom na to myslet
    v kazdem novem dotazu - a driv nebo pozdeji by se na to zapomnelo.

    Meni se jen zaznamy, ktere se **jinak nesparuji**. Kdyz identifikator
    v tabulce `users`/`items` sedi presne, nesahame na nej.
    """
    zmeny = {"user_id": 0, "item_id": 0}

    for tabulka, sloupec in (("users", "user_id"), ("items", "item_id")):
        with db.connect() as conn:
            spravne: dict[str, str] = {}
            sporne: set[str] = set()
            for row in conn.execute(f"SELECT id FROM {tabulka}").fetchall():
                klic = _normalizuj_id(row["id"])
                if not klic:
                    continue
                if klic in spravne and spravne[klic] != str(row["id"]):
                    # Dva ruzne zaznamy se stejnym normalizovanym tvarem.
                    # Nemelo by nastat - a kdyz ano, radeji nehadame.
                    sporne.add(klic)
                spravne[klic] = str(row["id"])

            if not spravne:
                continue

            nesparovane = conn.execute(
                f"""
                SELECT DISTINCT p.{sloupec} AS hodnota
                  FROM playback p
             LEFT JOIN {tabulka} t ON t.id = p.{sloupec}
                 WHERE p.{sloupec} IS NOT NULL AND p.{sloupec} != '' AND t.id IS NULL
                """
            ).fetchall()

            for row in nesparovane:
                stare = str(row["hodnota"])
                klic = _normalizuj_id(stare)
                nove = spravne.get(klic)
                if not nove or nove == stare or klic in sporne:
                    continue
                cursor = conn.execute(
                    f"UPDATE playback SET {sloupec} = ? WHERE {sloupec} = ?",
                    (nove, stare),
                )
                zmeny[sloupec] += cursor.rowcount or 0

    if any(zmeny.values()):
        log.info("sjednoceny identifikatory z importu: %s", zmeny)
    return zmeny


def refresh_playback_metadata() -> dict[str, int]:
    """Doplni do historie udaje, ktere v dobe importu jeste nebyly zname.

    Import se da spustit driv, nez se aplikace pripoji k Jellyfinu - tehdy
    jeste nezname jmena uzivatelu ani knihovny a v historii zustane "?".
    Tahle funkce to dorovna podle tabulek `users` a `items`, jakmile se
    naplni. Spousti se po kazdem importu i po synchronizaci knihovny,
    takze staci pripojit Jellyfin a stara data se srovnaji sama.

    Prepisujeme jen to, co chybi (nebo je "?"). Uz vyplnene udaje se
    nesahaji: v historii ma zustat, co bylo v okamziku prehravani -
    treba jmeno uzivatele, ktery se od te doby prejmenoval.
    """
    zmeny = {"user_names": 0, "item_types": 0, "libraries": 0, "series": 0}

    # Nejdriv srovnat tvar identifikatoru, jinak by se nize nesparovalo nic.
    _sjednot_identifikatory()

    with db.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE playback
               SET user_name = (SELECT u.name FROM users u WHERE u.id = playback.user_id)
             WHERE (user_name IS NULL OR user_name = '' OR user_name = '?')
               AND user_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM users u WHERE u.id = playback.user_id)
            """
        )
        zmeny["user_names"] = cursor.rowcount or 0

        # Typ polozky bereme z knihovny vzdycky, kdyz ho tam mame - zdroje
        # importu ho hlasi nespolehlive a nase tabulka je merodajna.
        cursor = conn.execute(
            """
            UPDATE playback
               SET item_type = (SELECT i.type FROM items i WHERE i.id = playback.item_id)
             WHERE item_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM items i WHERE i.id = playback.item_id
                             AND i.type IS NOT NULL
                             AND (playback.item_type IS NULL
                                  OR playback.item_type NOT IN ('Movie', 'Episode')
                                  OR playback.item_type != i.type))
            """
        )
        zmeny["item_types"] = cursor.rowcount or 0

        cursor = conn.execute(
            """
            UPDATE playback
               SET library_id = (SELECT i.library_id FROM items i WHERE i.id = playback.item_id)
             WHERE library_id IS NULL
               AND item_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM items i WHERE i.id = playback.item_id
                             AND i.library_id IS NOT NULL)
            """
        )
        zmeny["libraries"] = cursor.rowcount or 0

        cursor = conn.execute(
            """
            UPDATE playback
               SET series_name = (SELECT i.series_name FROM items i WHERE i.id = playback.item_id)
             WHERE series_name IS NULL
               AND item_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM items i WHERE i.id = playback.item_id
                             AND i.series_name IS NOT NULL)
            """
        )
        zmeny["series"] = cursor.rowcount or 0

    if any(zmeny.values()):
        log.info("historie doplnena: %s", zmeny)
    return zmeny


def _orphan_item_ids(limit: int = 2000) -> list[str]:
    """ItemId z historie, ke kterym v knihovne nic neni."""
    rows = db.query_all(
        """
        SELECT DISTINCT p.item_id
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL
           AND p.item_id != ''
           AND i.id IS NULL
         LIMIT ?
        """,
        (limit,),
    )
    return [str(row["item_id"]) for row in rows]


def _relink(old_id: str, new_id: str) -> int:
    """Prepise historii ze stareho ItemId na polozku, ktera v knihovne je."""
    with db.connect() as conn:
        cursor = conn.execute(
            "UPDATE playback SET item_id = ? WHERE item_id = ?", (new_id, old_id)
        )
        return cursor.rowcount or 0


def _link_by_tmdb(client_items: list[dict[str, Any]]) -> tuple[int, int]:
    """Sparuje podle identity polozky. Vraci (kolik polozek, kolik radku historie).

    Klicem je `scanner.identita_polozky()`, ne holé tmdb_id. U epizody je
    totiz tmdb_id spolecne celemu serialu - do slovniku by se z dvaceti
    dilu vesel jediny a historie vsech ostatnich by se prepsala na nej.
    Stejna past jako u `scanner._merge_by_tmdb()`.
    """
    polozek = radku = 0

    with db.connect() as conn:
        podle_identity = {
            (str(row["tmdb_id"]),
             int(row["parent_index_number"]) if row["parent_index_number"] is not None else -1,
             int(row["index_number"]) if row["index_number"] is not None else -1): str(row["id"])
            for row in conn.execute(
                "SELECT id, tmdb_id, parent_index_number, index_number"
                "  FROM items WHERE tmdb_id IS NOT NULL"
            ).fetchall()
        }

    for item in client_items:
        stare_id = str(item.get("Id") or "")
        identita = scanner.identita_polozky(item)
        if not stare_id or not identita:
            continue

        nove_id = podle_identity.get(identita)
        if not nove_id or nove_id == stare_id:
            continue

        radku += _relink(stare_id, nove_id)
        polozek += 1
        log.info("import: %s -> %s (podle identity %s)", stare_id, nove_id, identita)

    return polozek, radku


def _osirele_zaznamy() -> list[dict[str, Any]]:
    """Historie, ke ktere v knihovne nic nevede - jeden radek na titul.

    Vychozi bod obou zaloznich zpusobu navazani (podle nazvu i podle
    cisla dilu). Nazev bereme pres MAX(): zaznamu je vic a nazev se
    v nich muze lisit (titul se casem prejmenoval), ale pro hledani
    shody staci jeden.
    """
    return db.query_all(
        """
        SELECT p.item_id,
               MAX(p.item_name)   AS item_name,
               MAX(p.series_name) AS series_name
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
      GROUP BY p.item_id
        """
    )


def _link_by_name() -> tuple[int, int]:
    """Zaloha, kdyz Jellyfin stare ItemId uz nezna - shoda nazvu.

    Paruje jen jednoznacne shody. Kdyz mas v knihovne dva tituly stejneho
    jmena (rezisersky sestrih vedle kinoverze), zaznam radeji nechame
    nesparovany - spatne prirazena historie je horsi nez zadna.
    """
    kandidati = _osirele_zaznamy()

    polozek = radku = 0
    for row in kandidati:
        if row["series_name"]:
            shody = db.query_all(
                "SELECT id FROM items WHERE name = ? AND series_name = ? LIMIT 2",
                (row["item_name"], row["series_name"]),
            )
        else:
            # Bez znameho serialu hledame podle nazvu, at uz polozka
            # k nejakemu serialu patri nebo ne.
            #
            # Drive tu stalo `AND series_name IS NULL` a tim se odrizla
            # prave ta skupina, kvuli ktere tahle zaloha existuje:
            # osirely zaznam o EPIZODE. Ten serial nezna (jinak by se
            # sparoval driv), ale kazda epizoda v knihovne serial ma -
            # takze se shoda nenasla nikdy. V prehledu pak zustal dil
            # "Zvony" jako samostatny titul misto aby se zaradil pod
            # "Hra o truny".
            shody = db.query_all(
                "SELECT id FROM items WHERE name = ? LIMIT 2",
                (row["item_name"],),
            )

        if len(shody) != 1:
            continue  # nic, nebo vic nez jedna moznost - nehadame

        radku += _relink(str(row["item_id"]), str(shody[0]["id"]))
        polozek += 1

    return polozek, radku


async def link_imported_history() -> dict[str, Any]:
    """Dohleda polozky k importovane historii. Spousti se po kazdem importu.

    Nikdy nespadne tak, aby shodila import - kdyz Jellyfin neodpovida,
    prvni krok se preskoci a jede se rovnou na shodu podle nazvu.
    """
    # Nez zacneme cokoli dohledavat, srovname tvar identifikatoru - jinak
    # by se cela naimportovana historie tvarila jako "polozka neznama".
    _sjednot_identifikatory()

    orphans = _orphan_item_ids()
    if not orphans:
        # Neni co dohledavat - ale doplnit jmena a typy ma smysl vzdycky.
        # (Treba po prvni synchronizaci knihovny, kdy identifikatory sedi
        # a chybi jen jmena uzivatelu.)
        refresh_playback_metadata()
        return {"orphans": 0, "by_tmdb": 0, "by_name": 0, "rows": 0}

    tmdb_polozek = tmdb_radku = 0
    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            nalezene = await client.items_by_ids(orphans)
        tmdb_polozek, tmdb_radku = _link_by_tmdb(nalezene)
        # Co se nesparovalo s uz ulozenou polozkou, ale Jellyfin to zna,
        # rovnou zalozime. Bez toho zustala historie osirela, i kdyz
        # jsme o titulu vedeli uplne vsechno - jen jsme si to nikam
        # nezapsali.
        z_jellyfinu = zaloz_z_jellyfinu(nalezene)
        tmdb_polozek += z_jellyfinu["navazano"] + z_jellyfinu["zalozeno"]
        tmdb_radku += z_jellyfinu["radku"]
    except (JellyfinError, Exception) as exc:  # noqa: BLE001
        # Import uz probehl a data jsou ulozena. Kdyz se dohledani
        # nepovede, je to skoda, ne duvod hlasit chybu - zkusi se znovu
        # pri pristim importu.
        log.warning("dohledani podle tmdb se nepovedlo: %s", exc)

    # Podle cisla dilu napred - je to jednoznacne. Jellystat i Playback
    # Reporting davaji do nazvu "Serial - s02e07 - Nazev", takze tohle
    # posadi na misto vetsinu prevzate historie serialu.
    cislem_polozek, cislem_radku = _link_by_episode_number()
    name_polozek, name_radku = _link_by_name()

    # Az ted, kdyz historie ukazuje na spravne polozky, ma smysl z nich
    # prevzit typ, knihovnu a nazev serialu.
    refresh_playback_metadata()

    result = {
        "orphans": len(orphans),
        "by_tmdb": tmdb_polozek,
        "by_episode": cislem_polozek,
        "by_name": name_polozek,
        "rows": tmdb_radku + cislem_radku + name_radku,
    }
    if result["rows"]:
        log.info("import: dohledano %(by_tmdb)s podle tmdb, %(by_episode)s podle"
                 " cisla dilu, %(by_name)s podle nazvu (%(rows)s zaznamu)", result)
    return result


def _link_by_episode_number() -> tuple[int, int]:
    """Osiřelé záznamy naváže podle seriálu a čísla dílu.

    Tohle je ta nejsilnější stopa, kterou u převzaté historie máme -
    a dlouho zůstávala nevyužitá. Párování podle názvu hledalo v knihovně
    celý řetězec "Seal Team 6 - s02e07 - 7. epizoda", jenže tam se ten díl
    jmenuje prostě "7. epizoda". Shoda se proto nenašla nikdy.

    Seriál + řada + číslo dílu naopak určuje jeden konkrétní díl. Není to
    hádání podle jména: "7. epizoda" má každý seriál, ale "sedmý díl druhé
    řady Seal Teamu 6" je jen jeden.

    Když takových dílů vyjde víc (dvě kopie téhož), radši nesahat -
    špatně přiřazená historie je horší než ta, o které víme, že je stranou.
    """
    kandidati = _osirele_zaznamy()

    polozek = radku = 0
    for kandidat in kandidati:
        rozbor = rozbor_nazvu(kandidat["item_name"])
        if not rozbor:
            continue

        shody = db.query_all(
            """
            SELECT id FROM items
             WHERE LOWER(series_name) = LOWER(?)
               AND parent_index_number = ?
               AND index_number = ?
             LIMIT 2
            """,
            (rozbor["serial"], rozbor["rada"], rozbor["dil"]),
        )

        # Když číslo dílu nesedí, zkusíme ještě název epizody v rámci
        # téhož seriálu - někdy se číslování mezi zdroji rozchází.
        if len(shody) != 1 and rozbor["nazev"]:
            shody = db.query_all(
                "SELECT id FROM items WHERE LOWER(series_name) = LOWER(?)"
                "   AND LOWER(name) = LOWER(?) LIMIT 2",
                (rozbor["serial"], rozbor["nazev"]),
            )

        if len(shody) != 1:
            continue

        radku += _relink(str(kandidat["item_id"]), str(shody[0]["id"]))
        polozek += 1
        log.info("historie: %s -> %s (podle %s S%02dE%02d)",
                 kandidat["item_id"], shody[0]["id"], rozbor["serial"],
                 rozbor["rada"], rozbor["dil"])

    return polozek, radku
