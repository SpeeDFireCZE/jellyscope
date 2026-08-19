"""Import historie z jinych nastroju.

Jellyscope zaznamenava prehravani az od chvile, kdy bezi. Kdyz uz nekde
historii mas, nema smysl zacinat od nuly - da se prevzit.

Podporujeme dva zdroje:

1. **Playback Reporting** - oficialni plugin Jellyfinu. Data drzi ve vlastni
   tabulce a nabizi je pres API, kteremu se da poslat SQL dotaz. Nemusime
   tedy sahat na zadny soubor; staci se zeptat Jellyfinu, jako u vseho
   ostatniho. (Stejnou cestou to resi i Jellystat.)

2. **Jellystat** - jeho zaloha se da vyexportovat do JSON. Soubor nahrajes
   ve formulari a my si z nej vytahneme tabulku prehravani.

## Jak se resi opakovany import

Duplicity se hlidaji **dvakrat**, protoze jedna pojistka nestaci.

**1. Klic zaznamu.** Kazdy prevzaty radek dostane klic, ktery ho popisuje
u zdroje - `import:pbr:<rowid>:<polozka>` nebo `import:jst:<id>:<polozka>`.
Pred vlozenim se overi, ze takovy klic uz v databazi neni. Diky tomu je
import **idempotentni**: tentyz soubor muzes nahrat desetkrat a nic se
nezdvoji.

**2. Shoda podle obsahu.** Klic ale plati jen v ramci jednoho zdroje.
Tataz historie prectena z Jellystatu a z Playback Reportingu ma dva ruzne
klice, takze by se cela zdvojila - a vlastni zaznamy sberace nemaji klic
z importu vubec. Proto se navic porovnava obsah: stejny uzivatel, stejna
polozka a **prekryvajici se cas** znamena tentyz zaznam.

Prekryv, a ne shoda casu zacatku - kazdy nastroj si zapisuje cas trochu
jinak a presna shoda by vetsinu duplicit propasla. Viz `_uz_tam_je()`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, languages, scanner
from .config import load_config
from .jellyfin import (JellyfinClient, JellyfinError,
                       extract_streams as jellyfin_streams,
                       extract_tech_from_item as jellyfin_tech)

log = logging.getLogger("jellyscope.importers")

# Dotaz do tabulky pluginu Playback Reporting.
PBR_QUERY = (
    "SELECT rowid, DateCreated, UserId, ItemId, ItemType, ItemName, "
    "PlaybackMethod, ClientName, DeviceName, PlayDuration "
    "FROM PlaybackActivity ORDER BY DateCreated"
)

# Zaloha bez `rowid`. Nektere verze pluginu ten sloupec ve svem dotazovaci
# nepovoli a cely dotaz odmitnou chybou 500, takze by import nesel spustit
# vubec. Bez rowid prijdeme jen o cast klice proti opakovanemu importu -
# a tu dnes zastoupi porovnani podle obsahu (viz _uz_tam_je).
PBR_QUERY_BEZ_ROWID = (
    "SELECT DateCreated, UserId, ItemId, ItemType, ItemName, "
    "PlaybackMethod, ClientName, DeviceName, PlayDuration "
    "FROM PlaybackActivity ORDER BY DateCreated"
)


class ImportError_(RuntimeError):
    """Chyba importu, kterou ma smysl ukazat uzivateli."""


# ---------------------------------------------------------------------------
# Spolecne pomocniky
# ---------------------------------------------------------------------------

def _parse_time(raw: Any) -> str | None:
    """Prevede cas z ciziho formatu na nas jednotny tvar.

    Kazdy nastroj zapisuje cas trochu jinak. Prevod na jeden tvar hned
    na vstupu je stejny princip jako u jazykovych kodu - normalizace.
    """
    if raw is None:
        return None

    text = str(raw).strip()
    if not text:
        return None

    candidate = text.replace("Z", "+00:00").replace("T", " ")
    if "." in candidate:
        candidate = candidate.split(".", 1)[0]
    if "+" in candidate[10:]:
        candidate = candidate[:10] + candidate[10:].split("+", 1)[0]

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(candidate.strip(), pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc).strftime(db.TIME_FORMAT)

    return None


def _existing_keys(prefix: str) -> set[str]:
    """Klice, ktere uz z tohoto zdroje mame - aby se nic nezdvojilo."""
    rows = db.query_all(
        "SELECT session_key FROM playback WHERE session_key LIKE ?", (f"{prefix}%",)
    )
    return {row["session_key"] for row in rows}


# ---------------------------------------------------------------------------
# Druha pojistka proti duplicitam: shoda podle obsahu
# ---------------------------------------------------------------------------
#
# Klic `import:pbr:<rowid>:<itemid>` ohlida opakovany import **tehoz zdroje**.
# Nestaci ale na dva pripady, ktere v praxi nastanou snadno:
#
#   1. Tataz historie se naimportuje z Jellystatu **i** z Playback Reportingu.
#      Klice maji jiny tvar (jst / pbr), takze o sobe nevi a vsechno se zdvoji.
#   2. Naimportuje se obdobi, ktere uz Jellyscope sam nasbiral. Vlastni zaznamy
#      maji uplne jiny session_key.
#
# Proto porovnavame i obsah. Za tentyz zaznam povazujeme prehravani, ktere ma
# stejneho uzivatele, stejnou polozku a **casove se prekryva**.
#
# Proc prekryv, a ne shodu casu zacatku: kazdy zdroj si zapisuje cas trochu
# jinak (zacatek prehravani vs. okamzik zapisu do tabulky), takze presna shoda
# by vetsinu duplicit propasla. Prekryv je odolnejsi a pritom bezpecny - tentyz
# film nemuze jeden clovek sledovat dvakrat zaroven.

def _epocha(cas: str | None) -> float | None:
    """Cas z databaze prevedeny na sekundy, aby se dal porovnavat."""
    if not cas:
        return None
    try:
        return datetime.strptime(str(cas), db.TIME_FORMAT).replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _index_prehravani() -> dict[tuple[str, str], list[tuple[float, float]]]:
    """Casove useky uz ulozenych prehravani, podle uzivatele a polozky."""
    index: dict[tuple[str, str], list[tuple[float, float]]] = {}

    for row in db.query_all(
        """
        SELECT user_id, item_id, started_at, ended_at, watched_seconds
          FROM playback
         WHERE user_id IS NOT NULL AND user_id != ''
           AND item_id IS NOT NULL AND item_id != ''
        """
    ):
        zacatek = _epocha(row["started_at"])
        if zacatek is None:
            continue
        konec = _epocha(row["ended_at"])
        if konec is None or konec <= zacatek:
            konec = zacatek + max(int(row["watched_seconds"] or 0), 1)

        klic = (_normalizuj_id(row["user_id"]), _normalizuj_id(row["item_id"]))
        index.setdefault(klic, []).append((zacatek, konec))

    return index


def _uz_tam_je(index: dict[tuple[str, str], list[tuple[float, float]]],
               user_id: Any, item_id: Any, started_at: str, duration: int) -> bool:
    """Mame uz tohle prehravani - trebas z jineho zdroje?

    Kdyz nevime, kdo to hral nebo co, radeji netvrdime nic: bez obou udaju
    by se dalo splest dve ruzna prehravani a prisli bychom o data. Klic
    `session_key` v takovem pripade porad plati.
    """
    uzivatel = _normalizuj_id(user_id)
    polozka = _normalizuj_id(item_id)
    if not uzivatel or not polozka:
        return False

    zacatek = _epocha(started_at)
    if zacatek is None:
        return False
    konec = zacatek + max(int(duration or 0), 1)

    for stary_zacatek, stary_konec in index.get((uzivatel, polozka), []):
        # Dotykajici se useky (konec == zacatek) prekryv nejsou - divak
        # si mohl pustit dalsi dil hned, jak dokoukal predchozi.
        if zacatek < stary_konec and stary_zacatek < konec:
            return True
    return False


def _zapamatuj(index: dict[tuple[str, str], list[tuple[float, float]]],
               user_id: Any, item_id: Any, started_at: str, duration: int) -> None:
    """Prida prave prijaty zaznam do indexu.

    Bez tohohle kroku by se nepoznaly duplicity **uvnitr jednoho souboru** -
    a zalohy je obcas obsahuji.
    """
    uzivatel = _normalizuj_id(user_id)
    polozka = _normalizuj_id(item_id)
    zacatek = _epocha(started_at)
    if not uzivatel or not polozka or zacatek is None:
        return
    index.setdefault((uzivatel, polozka), []).append(
        (zacatek, zacatek + max(int(duration or 0), 1)))


def _known_users() -> dict[str, str]:
    return {row["id"]: row["name"] for row in db.query_all("SELECT id, name FROM users")}


def _known_items() -> dict[str, dict[str, Any]]:
    return {
        row["id"]: row
        for row in db.query_all(
            "SELECT id, name, type, series_name, library_id, default_audio_language"
            " FROM items"
        )
    }


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


def _jellystat_item_type(record: dict[str, Any]) -> str | None:
    """Film, nebo epizoda? Jellystat to v zaloze neuvadi primo.

    Tabulka jf_playback_activity zadny sloupec s typem nema - zna ale
    nazev serialu a id epizody. Kdyz je vyplneny kterykoliv z nich, jde
    o epizodu; jinak film.

    Drive se sem omylem cetl `NowPlayingItemName`, tedy **nazev titulu**.
    Do sloupce item_type se pak ukladalo treba "Duna" a vsechno, co
    rozlisuje filmy od serialu, prestalo fungovat - graf sledovanosti
    po dnech zustal prazdny, i kdyz hodiny v souhrnu sedely.
    """
    explicitni = _pick(record, "ItemType", "item_type", "NowPlayingItemType", "Type")
    if explicitni in ("Movie", "Episode", "Series", "Audio"):
        return explicitni

    if _pick(record, "SeriesName", "seriesname", "series_name") \
            or _pick(record, "EpisodeId", "episodeid", "SeasonId", "seasonid"):
        return "Episode"
    return "Movie"


def _normalizuj_id(hodnota: Any) -> str:
    """Sjednoti tvar identifikatoru z Jellyfinu.

    Tentyz uzivatel muze byt zapsany jako "a1b2c3d4e5f6..." nebo
    "a1b2c3d4-e5f6-...". Je to jedno a totez cislo, jen jinak napsane:
    Jellyfin ho ve svem API posila bez pomlcek, kdezto plugin Playback
    Reporting i Jellystat ho casto ukladaji s pomlckami.

    Kdybychom to neresili, `users.id = playback.user_id` by nesedlo a
    v historii by navzdy zustaly otazniky misto jmen.
    """
    return str(hodnota or "").replace("-", "").lower()


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


def _link_by_name() -> tuple[int, int]:
    """Zaloha, kdyz Jellyfin stare ItemId uz nezna - shoda nazvu.

    Paruje jen jednoznacne shody. Kdyz mas v knihovne dva tituly stejneho
    jmena (rezisersky sestrih vedle kinoverze), zaznam radeji nechame
    nesparovany - spatne prirazena historie je horsi nez zadna.
    """
    kandidati = db.query_all(
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


def _insert(rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO playback (
                session_key, user_id, user_name, item_id, item_name, item_type,
                series_name, library_id, client, device_name, play_method,
                audio_language, started_at, last_seen_at, ended_at,
                watched_seconds, paused_seconds, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
            """,
            rows,
        )
    return len(rows)


# Jak se muze jmenovat sloupec s jazykem stopy. Zakladni tabulka pluginu
# ho nema, ale nektere verze a vlastni upravy ano - a kdyz uz ten udaj
# dorazi, byla by skoda ho zahodit. Porovnava se malymi pismeny bez
# podtrzitek, at na zapisu nezalezi.
JAZYKOVE_SLOUPCE = ("audiolanguage", "audiostreamlanguage", "language",
                    "audiolang", "audio")


def _jazyk_ze_zdroje(data: dict[str, Any]) -> str | None:
    """Vytahne jazyk stopy z radku, pokud ho zdroj vubec posila."""
    for klic, hodnota in data.items():
        srovnany = str(klic).lower().replace("_", "").replace(" ", "")
        if srovnany in JAZYKOVE_SLOUPCE and hodnota:
            kod = languages.normalize(str(hodnota))
            if kod and kod != languages.UNKNOWN:
                return kod
    return None


def _build_row(
    key: str,
    user_id: str | None,
    item_id: str | None,
    started_at: str,
    duration: int,
    item_type: str | None,
    item_name: str | None,
    client: str | None,
    device: str | None,
    method: str | None,
    users: dict[str, str],
    items: dict[str, dict[str, Any]],
    user_name: str | None = None,
    audio_language: str | None = None,
) -> tuple[Any, ...]:
    """Poskladá jeden radek historie ve tvaru nasi tabulky.

    Poradi, v jakem hledame jmeno uzivatele a typ polozky, neni nahodne:
    nase vlastni tabulky jsou nejspolehlivejsi, pak to, co poslal zdroj,
    a teprve nakonec "?". Diky tomu se da importovat i drive, nez se
    aplikace vubec pripoji k Jellyfinu - jmena se doplni pozdeji,
    viz refresh_playback_metadata().
    """
    item_row = items.get(item_id or "") or {}
    ended = (
        datetime.strptime(started_at, db.TIME_FORMAT) + timedelta(seconds=duration)
    ).strftime(db.TIME_FORMAT)

    return (
        key,
        user_id,
        users.get(user_id or "") or user_name or "?",
        item_id,
        item_row.get("name") or item_name or "?",
        item_row.get("type") or item_type,
        item_row.get("series_name"),
        item_row.get("library_id"),
        client,
        device,
        method,
        # Jazyk, ktery zdroj poslal, ma prednost - je to udaj o tom, co
        # divak SKUTECNE poslouchal. Nektere verze pluginu Playback
        # Reporting ho ukladaji; vetsina zdroju ne.
        #
        # Kdyz ho zdroj nema, zbyva vychozi stopa souboru - a kdyz nezname
        # ani tu, zustane prazdny. Zamerne nic nedomyslime: vymysleny udaj
        # je horsi nez zadny.
        audio_language or item_row.get("default_audio_language"),
        started_at,
        ended,
        ended,
        duration,
    )


# ---------------------------------------------------------------------------
# Pojistka: do Jellyfinu jen čteme
# ---------------------------------------------------------------------------
#
# Jellyscope Jellyfin **nikdy nemění**. Všechno ostatní jsou obyčejné GET
# dotazy, u kterých to platí samo. Jediná výjimka je tenhle plugin: jeho
# dotazovací rozhraní je POST, protože se mu SQL posílá v těle požadavku.
#
# POST samo o sobě nic nemění - mění to, co se v něm pošle. Aby se sem
# omylem nedostal jiný příkaz než čtení (třeba při pozdější úpravě),
# projde každý dotaz tímhle sítem. Radši ať import odmítne pracovat,
# než aby zapsal do cizí databáze.

ZAKAZANA_SLOVA = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT",
)


def jen_cteni(sql: str) -> str:
    """Propustí jen jeden dotaz SELECT. Cokoliv jiného odmítne."""
    ocistene = " ".join(sql.split()).rstrip(";").strip()

    if not ocistene.upper().startswith("SELECT"):
        raise ImportError_(
            "Vnitřní pojistka: do Jellyfinu se smí posílat jen SELECT."
        )
    # Středník uprostřed by znamenal víc příkazů za sebou.
    if ";" in ocistene:
        raise ImportError_(
            "Vnitřní pojistka: dotaz nesmí obsahovat víc příkazů."
        )

    slova = set(ocistene.upper().replace("(", " ").replace(")", " ").split())
    nalezene = sorted(slova & set(ZAKAZANA_SLOVA))
    if nalezene:
        raise ImportError_(
            f"Vnitřní pojistka: dotaz obsahuje zápisové příkazy {nalezene}."
        )
    return ocistene


# ---------------------------------------------------------------------------
# 1. Playback Reporting (plugin Jellyfinu)
# ---------------------------------------------------------------------------

async def playback_reporting_available() -> tuple[bool, str]:
    """Zjisti, jestli je plugin nainstalovany a odpovida."""
    config = load_config()
    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            response = await client._client.post(
                "/user_usage_stats/submit_custom_query",
                json={"CustomQueryString":
                          jen_cteni("SELECT COUNT(*) FROM PlaybackActivity"),
                      "ReplaceUserId": False},
            )
    except Exception as exc:  # noqa: BLE001
        return False, f"Nepodařilo se zeptat: {exc}"

    if response.status_code == 404:
        return False, "Plugin Playback Reporting v Jellyfinu není nainstalovaný."
    if response.status_code >= 400:
        # I tady vypisujeme telo odpovedi - "chyba 500" bez neho nikomu
        # neporadi, kde hledat.
        return False, (f"Plugin odpověděl chybou {response.status_code}: "
                       f"{(response.text or '').strip()[:300]}")

    try:
        results = (response.json() or {}).get("results") or []
        count = int(results[0][0]) if results and results[0] else 0
    except (ValueError, IndexError, TypeError):
        return True, "Plugin odpovídá, ale počet záznamů se nepodařilo přečíst."

    return True, f"Plugin nalezen, obsahuje {count} záznamů."


async def import_playback_reporting(min_seconds: int = 60) -> dict[str, Any]:
    """Prevezme historii z pluginu Playback Reporting."""
    config = load_config()
    scan_id = scanner.start_task_log("import")

    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            # Zkusime oba tvary dotazu. Prvni je bohatsi (obsahuje rowid,
            # ze ktereho skladame klic proti opakovanemu importu), druhy
            # projde i tam, kde plugin rowid odmita.
            posledni_chyba = ""
            payload = None

            for dotaz in (PBR_QUERY, PBR_QUERY_BEZ_ROWID):
                response = await client._client.post(
                    "/user_usage_stats/submit_custom_query",
                    json={"CustomQueryString": jen_cteni(dotaz), "ReplaceUserId": False},
                )
                if response.status_code == 404:
                    raise ImportError_(
                        "Plugin Playback Reporting není nainstalovaný. "
                        "Najdeš ho v Jellyfinu: Ovládací panel -> Pluginy -> Katalog."
                    )
                if response.status_code < 400:
                    payload = response.json() or {}
                    if dotaz is PBR_QUERY_BEZ_ROWID:
                        log.info("plugin odmitl rowid, pouzit zjednoduseny dotaz")
                    break

                # Telo odpovedi obsahuje, co se pluginu nelibilo. Bez nej
                # je "chyba 500" hlaska, se kterou se neda nic delat.
                posledni_chyba = (
                    f"{response.status_code}: {(response.text or '').strip()[:300]}"
                )
                log.warning("plugin odmitl dotaz (%s)", posledni_chyba)

            if payload is None:
                raise ImportError_(
                    f"Plugin Playback Reporting odpověděl chybou {posledni_chyba}\n\n"
                    "Nejčastější příčina je NESOULAD VERZÍ: plugin přeložený "
                    "proti staršímu Jellyfinu spadne dřív, než se ke svým "
                    "datům vůbec dostane, a vrátí jen \"Error processing "
                    "request\". Z Jellyscope to opravit nejde - endpoint "
                    "pluginu je pak mrtvý pro jakýkoliv dotaz.\n\n"
                    "Jistotu dá log Jellyfinu; hledej v něm "
                    "submit_custom_query. Stojí-li tam MissingMethodException, "
                    "je to přesně tenhle případ.\n\n"
                    "Co s tím:\n"
                    "  * nahraj data pluginu ze souboru - viz \"Nefunguje "
                    "import přes API?\" hned pod tímhle tlačítkem,\n"
                    "  * nebo přenes historii ze zálohy Jellystatu,\n"
                    "  * nebo počkej na verzi pluginu pro tvůj Jellyfin - pak "
                    "začne import přes API fungovat sám."
                )

    except (JellyfinError, ImportError_) as exc:
        scanner.finish_task_log(scan_id, "error", message=str(exc))
        return {"status": "error", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("import z Playback Reporting selhal")
        scanner.finish_task_log(scan_id, "error", message=str(exc))
        return {"status": "error", "message": f"Neočekávaná chyba: {exc}"}

    columns = [str(c).lower() for c in (payload.get("colums") or payload.get("columns") or [])]
    results = payload.get("results") or []

    return await _zpracuj_pbr(scan_id, columns, results, min_seconds,
                              "Playback Reporting")


# Poradi sloupcu v zaloze pluginu Playback Reporting. Odpovida tabulce
# PlaybackActivity - plugin ji do souboru vypisuje tak, jak je.
PBR_TSV_SLOUPCE = ["datecreated", "userid", "itemid", "itemtype", "itemname",
                   "playbackmethod", "clientname", "devicename", "playduration"]


def _tsv_sloupce(prvni_radek: list[str]) -> tuple[list[str], bool]:
    """Rozhodne, jestli je prvni radek hlavicka, nebo uz data.

    Plugin zalohu podle verze zapisuje obojím zpusobem. Pozname to podle
    obsahu: hlavicka nese nazvy sloupcu, datovy radek zacina datem.
    Hadat podle poctu sloupcu by nestacilo - ten je stejny.
    """
    male = [str(bunka).strip().lower() for bunka in prvni_radek]
    if "datecreated" in male or "userid" in male:
        return male, True

    # Bez hlavicky se drzime poradi z tabulky. Kdyz ma radek sloupcu vic
    # (nektere verze pridavaji na zacatek rowid), prebytek na zacatku
    # pojmenujeme, at se dvojice nerozjedou.
    navic = len(prvni_radek) - len(PBR_TSV_SLOUPCE)
    if navic > 0:
        return ["rowid"] * navic + PBR_TSV_SLOUPCE, False
    return PBR_TSV_SLOUPCE, False


async def import_playback_reporting_tsv(raw: bytes,
                                        min_seconds: int = 60) -> dict[str, Any]:
    """Prevezme historii ze zalohy pluginu Playback Reporting (soubor TSV).

    Zaloha pro pripad, kdy plugin pres API nefunguje. Stava se to
    u nesouladu verzi: plugin prelozeny proti starsimu Jellyfinu spadne
    na `MissingMethodException` jeste driv, nez se dostane ke svym datum,
    a vrati jen "Error processing request".

    Soubor si vyrobis primo v pluginu: **Jellyfin -> Ovladaci panel ->
    Playback Reporting -> Backup -> Save backup**. Vysledkem je obycejny
    textovy soubor, kde jsou hodnoty oddelene tabulatorem.

    Proc TSV a ne `playback_reporting.db`: zalohu si umi vyrobit sam
    plugin jednim kliknutim, kdezto k databazovemu souboru se clovek musi
    dostat pres SSH a jeste vedet, kde lezi. Do Jellyfinu se pritom
    nesaha ani v jednom pripade - cteme kopii, kterou nahrajes ty.

    Radky se zpracuji uplne stejne jako z API, vcetne klice proti
    duplicitam - kdyz se pak API opravi a spustis import znovu, nic se
    nezdvoji.
    """
    scan_id = scanner.start_task_log("import")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Zalohy z Windows byvaji v cp1250; a kdyz nesedi ani to, radeji
        # nahradime nectene znaky, nez abychom import odmitli kvuli
        # jedinemu pismenu v nazvu filmu.
        text = raw.decode("cp1250", errors="replace")

    radky = [radek for radek in text.splitlines() if radek.strip()]
    if not radky:
        scanner.finish_task_log(scan_id, "error", message="prazdny soubor")
        return {"status": "error", "message": "Soubor je prázdný."}

    rozdelene = [radek.split("\t") for radek in radky]

    # Soubor bez tabulatoru je skoro jiste neco jineho, nez si clovek mysli.
    # Rict to rovnou je lepsi nez naimportovat nula zaznamu bez vysvetleni.
    if max(len(r) for r in rozdelene) < 2:
        scanner.finish_task_log(scan_id, "error", message="neni to TSV")
        return {
            "status": "error",
            "message": ("V souboru nejsou tabulátory, takže to není záloha "
                        "Playback Reportingu. Vyrob ji v Jellyfinu: Ovládací "
                        "panel → Playback Reporting → Backup → Save backup."),
        }

    sloupce, ma_hlavicku = _tsv_sloupce(rozdelene[0])
    hodnoty = rozdelene[1:] if ma_hlavicku else rozdelene

    return await _zpracuj_pbr(scan_id, sloupce, hodnoty, min_seconds,
                              "Playback Reporting (záloha TSV)")


async def _zpracuj_pbr(scan_id: int, columns: list[str], results: list[Any],
                 min_seconds: int, zdroj: str) -> dict[str, Any]:
    """Spolecne zpracovani radku z Playback Reportingu.

    Jedna funkce pro obe cesty (API i soubor). Kdyby to byly dve kopie,
    oprava v jedne by se do druhe casem nedostala - a duplicity nebo
    spatne klice by si nikdo nevsiml, dokud by nekdo nepouzil zrovna
    tu druhou cestu.
    """
    users = _known_users()
    items = _known_items()
    existing = _existing_keys("import:pbr:")
    index_casu = _index_prehravani()

    rows: list[tuple[Any, ...]] = []
    skipped_short = 0
    skipped_duplicate = 0
    skipped_known = 0

    for record in results:
        data = dict(zip(columns, record)) if columns else {}
        started_at = _parse_time(data.get("datecreated"))
        if not started_at:
            continue

        try:
            duration = int(float(data.get("playduration") or 0))
        except (TypeError, ValueError):
            duration = 0
        if duration < min_seconds:
            skipped_short += 1
            continue

        # Klic zaznamu. `rowid` je nejlepsi, protoze ho plugin nemeni -
        # kdyz ho ale odmitl (viz PBR_QUERY_BEZ_ROWID), poskladame klic
        # z toho, co zaznam jednoznacne popisuje. Bez teto zalozky by
        # vsechny radky tehoz titulu dostaly stejny klic a naimportoval
        # by se z nich jediny.
        oznaceni = data.get("rowid")
        if oznaceni is None:
            oznaceni = f"{data.get('datecreated')}:{data.get('userid')}"
        key = f"import:pbr:{oznaceni}:{data.get('itemid')}"
        if key in existing:
            skipped_duplicate += 1
            continue
        existing.add(key)

        # Tentyz zaznam uz muze byt v databazi z Jellystatu nebo ze sberace.
        if _uz_tam_je(index_casu, data.get("userid"), data.get("itemid"),
                      started_at, duration):
            skipped_known += 1
            continue
        _zapamatuj(index_casu, data.get("userid"), data.get("itemid"),
                   started_at, duration)

        rows.append(_build_row(
            key=key,
            user_id=data.get("userid"),
            item_id=data.get("itemid"),
            started_at=started_at,
            duration=duration,
            item_type=data.get("itemtype"),
            item_name=data.get("itemname"),
            client=data.get("clientname"),
            device=data.get("devicename"),
            method=data.get("playbackmethod"),
            audio_language=_jazyk_ze_zdroje(data),
            users=users,
            items=items,
        ))

    imported = _insert(rows)

    # Import neposila tmdb ID - dohledame ho sami, at se prevzata historie
    # spoji se skutecnymi tituly v knihovne.
    linked = await link_imported_history()

    scanner.finish_task_log(
        scan_id, "done", total=len(results), ok=imported, failed=0,
        message=(f"{zdroj}: {imported} nových, "
                 f"{skipped_duplicate} již existovalo, "
                 f"{skipped_known} známých z jiného zdroje, "
                 f"{skipped_short} příliš krátkých"),
    )
    return {
        "status": "ok",
        "source": zdroj,
        "found": len(results),
        "imported": imported,
        "duplicate": skipped_duplicate,
        "known_elsewhere": skipped_known,
        "too_short": skipped_short,
        "linked": linked,
    }


# ---------------------------------------------------------------------------
# 2. Jellystat (JSON zaloha)
# ---------------------------------------------------------------------------

# Jellystat uklada prehravani do tabulky jf_playback_activity. V zaloze
# muze byt bud primo seznam, nebo slovnik s nazvy tabulek.
JELLYSTAT_TABLES = ("jf_playback_activity", "playback_activity", "jf_playback")


def _find_jellystat_rows(payload: Any) -> list[dict[str, Any]]:
    """Najde v JSON zaloze seznam prehravani.

    Jellystat menil format zalohy mezi verzemi, takze se nespolehame na
    jeden tvar a hledame vic moznosti. Kdyz nic nesedi, rekneme to nahlas
    misto abychom naimportovali prazdno a tvarili se, ze je hotovo.
    """
    if isinstance(payload, list):
        # Bud rovnou seznam zaznamu, nebo seznam tabulek.
        if payload and isinstance(payload[0], dict):
            if any(key in payload[0] for key in ("UserId", "userid", "NowPlayingItemId")):
                return payload
            for entry in payload:
                for name in JELLYSTAT_TABLES:
                    if name in entry and isinstance(entry[name], list):
                        return entry[name]
        return []

    if isinstance(payload, dict):
        for name in JELLYSTAT_TABLES:
            value = payload.get(name)
            if isinstance(value, list):
                return value
        # Nekdy je vse zabalene o uroven niz.
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found = _find_jellystat_rows(value)
                if found:
                    return found

    return []


def _pick(record: dict[str, Any], *names: str) -> Any:
    """Vrati prvni vyplnene pole z nekolika moznych nazvu."""
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


async def import_jellystat_json(raw: bytes, min_seconds: int = 60) -> dict[str, Any]:
    """Prevezme historii z JSON zalohy Jellystatu."""
    scan_id = scanner.start_task_log("import")

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        scanner.finish_task_log(scan_id, "error", message="neplatný JSON")
        return {"status": "error", "message": f"Soubor není platný JSON: {exc}"}

    records = _find_jellystat_rows(payload)
    if not records:
        scanner.finish_task_log(scan_id, "error", message="tabulka přehrávání nenalezena")
        return {
            "status": "error",
            "message": ("V souboru jsem nenašel tabulku přehrávání "
                        "(jf_playback_activity). Je to opravdu záloha Jellystatu?"),
        }

    users = _known_users()
    items = _known_items()
    existing = _existing_keys("import:jst:")
    index_casu = _index_prehravani()

    rows: list[tuple[Any, ...]] = []
    skipped_short = 0
    skipped_duplicate = 0
    skipped_known = 0

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue

        started_at = _parse_time(
            _pick(record, "ActivityDateInserted", "activity_date_inserted",
                  "DateCreated", "date_created", "LastActivityDate")
        )
        if not started_at:
            continue

        try:
            duration = int(float(_pick(record, "PlaybackDuration", "playback_duration",
                                       "PlayDuration", "duration") or 0))
        except (TypeError, ValueError):
            duration = 0
        if duration < min_seconds:
            skipped_short += 1
            continue

        item_id = _pick(record, "NowPlayingItemId", "nowplayingitemid", "ItemId", "EpisodeId")
        user_id = _pick(record, "UserId", "userid", "user_id")

        key = f"import:jst:{_pick(record, 'Id', 'id') or index}:{item_id}"
        if key in existing:
            skipped_duplicate += 1
            continue
        existing.add(key)

        # Tentyz zaznam uz muze byt v databazi z Playback Reportingu nebo
        # ze sberace.
        if _uz_tam_je(index_casu, user_id, item_id, started_at, duration):
            skipped_known += 1
            continue
        _zapamatuj(index_casu, user_id, item_id, started_at, duration)

        rows.append(_build_row(
            key=key,
            user_id=user_id,
            item_id=item_id,
            started_at=started_at,
            duration=duration,
            item_type=_jellystat_item_type(record),
            item_name=_pick(record, "NowPlayingItemName", "ItemName", "item_name"),
            client=_pick(record, "Client", "client"),
            device=_pick(record, "DeviceName", "device_name"),
            method=_pick(record, "PlayMethod", "play_method", "PlaybackMethod"),
            users=users,
            items=items,
            user_name=_pick(record, "UserName", "username", "user_name"),
        ))

    imported = _insert(rows)

    # Zaloha Jellystatu tmdb ID neobsahuje - dohledame ho sami.
    linked = await link_imported_history()

    scanner.finish_task_log(
        scan_id, "done", total=len(records), ok=imported, failed=0,
        message=(f"Jellystat: {imported} nových, {skipped_duplicate} již existovalo, "
                 f"{skipped_known} známých z jiného zdroje, "
                 f"{skipped_short} příliš krátkých"),
    )
    return {
        "status": "ok",
        "source": "Jellystat",
        "found": len(records),
        "imported": imported,
        "duplicate": skipped_duplicate,
        "known_elsewhere": skipped_known,
        "too_short": skipped_short,
        "linked": linked,
    }


# ---------------------------------------------------------------------------
# Uklid duplicit ve vlastni historii
# ---------------------------------------------------------------------------
#
# Duplicity resi tenhle modul uz u importu (viz `_uz_tam_je`). Vzniknout ale
# muzou i bez importu: kdyz proti jedne databazi bezi **dva sberace naraz**
# (typicky nedopatrenim - stara a nova verze aplikace se stejnym nastavenim),
# kazdy si zalozi vlastni radek pro tentyz film.
#
# Predchazi se tomu ve sberaci (viz collector._prevzit_cizi_relaci) - jenze
# to plati az od ted. Zaznamy, ktere uz v databazi lezi, je potreba uklidit
# jednorazove, a to dela tahle funkce.

def duplicate_playback_groups() -> list[list[dict[str, Any]]]:
    """Najde skupiny zaznamu, ktere popisuji tentyz sledovaci zazitek.

    ## Kdy jsou dva zaznamy tentyz zazitek

    Musi platit **vsechno** najednou:

      1. **stejny uzivatel** a **stejna polozka**,
      2. jejich casove useky se **prekryvaji**,
      3. **neodporuji si zarizenim** - kdyz obe znaji `device_id` a lisi
         se, jsou to dve ruzna prehravani,
      4. ani jeden nepochazi z importu.

    K bodu 2: prekryv, ne shoda casu zacatku. Dva sberace zapisuji kazdy
    o vterinu jinde, takze presna shoda by vetsinu duplicit propasla.
    Navazujici useky (konec == zacatek) prekryv nejsou - kdo dokoukal film
    a pustil si ho znovu, ma dva zaznamy a dva mu taky zustanou. A film
    zacaty vcera vecer a dokoukany dnes se neprekryva uz vubec.

    K bodu 3: jeden clovek muze tentyz film pustit na dvou zarizenich
    naraz - na televizi a na telefonu. Bez teto podminky by se takova dve
    prehravani slila v jedno, protoze podminky 1 a 2 splnuji. Zarizeni je
    to jedine, co je odlisi; dva sberace nad jednou relaci naopak hlasi
    zarizeni stejne.

    Importovane zaznamy vynechavame - ty uz maji vlastni ochranu podle
    klice (viz `_uz_tam_je`) a nechceme je michat s vlastnim sberem.
    """
    radky = db.query_all(
        """
        SELECT id, user_id, item_id, session_key, device_id, started_at,
               ended_at, watched_seconds, paused_seconds, is_active
          FROM playback
         WHERE user_id IS NOT NULL AND user_id != ''
           AND item_id IS NOT NULL AND item_id != ''
           AND session_key NOT LIKE 'import:%'
         ORDER BY started_at, id
        """
    )

    podle_polozky: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for radek in radky:
        zacatek = _epocha(radek["started_at"])
        if zacatek is None:
            continue
        konec = _epocha(radek["ended_at"])
        if konec is None or konec <= zacatek:
            konec = zacatek + max(int(radek["watched_seconds"] or 0), 1)
        radek["_od"], radek["_do"] = zacatek, konec
        klic = (_normalizuj_id(radek["user_id"]), _normalizuj_id(radek["item_id"]))
        podle_polozky.setdefault(klic, []).append(radek)

    def stejne_zarizeni(a: dict[str, Any], b: dict[str, Any]) -> bool:
        """Neodporuji si zarizenim? Nezname zarizeni nikomu neodporuje."""
        prvni = str(a.get("device_id") or "").strip()
        druhe = str(b.get("device_id") or "").strip()
        if not prvni or not druhe:
            return True
        return prvni == druhe

    skupiny: list[list[dict[str, Any]]] = []
    for zaznamy in podle_polozky.values():
        if len(zaznamy) < 2:
            continue
        zaznamy.sort(key=lambda r: r["_od"])

        # Klasicke slucovani intervalu: jedeme zleva doprava a co se
        # prekryva s rozdelanou skupinou, do ni patri. Zaznam z jineho
        # zarizeni do skupiny nepatri, i kdyz se casem prekryva.
        skupina = [zaznamy[0]]
        konec = zaznamy[0]["_do"]
        for zaznam in zaznamy[1:]:
            if (zaznam["_od"] < konec
                    and all(stejne_zarizeni(zaznam, uz) for uz in skupina)):
                skupina.append(zaznam)
                konec = max(konec, zaznam["_do"])
            else:
                if len(skupina) > 1:
                    skupiny.append(skupina)
                skupina = [zaznam]
                konec = zaznam["_do"]
        if len(skupina) > 1:
            skupiny.append(skupina)

    return skupiny


def merge_duplicate_playback() -> dict[str, Any]:
    """Sloucí duplicitní záznamy v historii. Vrací, co se stalo.

    Ze skupiny zustane jeden radek: ten s nejdelsim odsledovanym casem -
    tedy ten uplnejsi. Prevezme nejcasnejsi zacatek a nejpozdejsi konec
    skupiny, aby se neztratil rozsah. Ostatni se smazou.

    **Necita se.** Dva zaznamy o tomtez prehravani nejsou dve prehrani;
    souctem by vznikl divak, ktery film videl dvakrat za sebou.
    """
    skupiny = duplicate_playback_groups()
    if not skupiny:
        return {"status": "ok", "groups": 0, "removed": 0}

    smazano = 0
    with db.connect() as conn:
        for skupina in skupiny:
            # Nejuplnejsi zaznam si necháme; pri shode ten starsi (nizsi id),
            # aby byl vysledek stejny pri kazdem spusteni.
            skupina.sort(key=lambda r: (-int(r["watched_seconds"] or 0), int(r["id"])))
            hlavni, ostatni = skupina[0], skupina[1:]

            zacatky = [r["started_at"] for r in skupina if r["started_at"]]
            konce = [r["ended_at"] for r in skupina if r["ended_at"]]
            conn.execute(
                """UPDATE playback
                      SET started_at = ?,
                          ended_at = ?,
                          paused_seconds = ?,
                          is_active = ?
                    WHERE id = ?""",
                (min(zacatky) if zacatky else hlavni["started_at"],
                 max(konce) if konce else hlavni["ended_at"],
                 max(int(r["paused_seconds"] or 0) for r in skupina),
                 # Kdyz nektery ze zaznamu jeste bezel, bezi i slouceny -
                 # jinak by prave hrajici film zmizel z "Prave se hraje".
                 1 if any(int(r["is_active"] or 0) for r in skupina) else 0,
                 hlavni["id"]),
            )
            for zaznam in ostatni:
                conn.execute("DELETE FROM playback WHERE id = ?", (zaznam["id"],))
                smazano += 1

    log.info("slouceno %d duplicitnich skupin v historii, smazano %d radku",
             len(skupiny), smazano)
    return {"status": "ok", "groups": len(skupiny), "removed": smazano}


def duplicate_playback_count() -> int:
    """Kolik radku by uklid smazal - aby slo rict predem, jestli ma smysl."""
    return sum(len(skupina) - 1 for skupina in duplicate_playback_groups())


# ---------------------------------------------------------------------------
# Duplicity mezi dvema zdroji importu
# ---------------------------------------------------------------------------
#
# `duplicate_playback_groups()` vys hleda **prekryv v case**. Na duplicity
# ze dvou sberacu to staci, protoze oba popisuji tutez relaci a lisi se
# o vteriny. Mezi Jellystatem a Playback Reportingem ale prekryv nestaci:
# kazdy si zapisuje jiny okamzik (zacatek prehravani vs. zapis do tabulky),
# takze tytez dva a pul hodiny filmu vyjdou tu v 18:15, tu v 18:49 - a u
# nocniho koukani klidne az druhy den rano.
#
# Poznat se to ale da podle neceho jineho: **stejne dlouhe prehravani**.
# Kdyz tentyz clovek na temze zarizeni sledoval tentyz titul stejne dlouho
# (na par minut presne) a v ramci jednoho dne, jsou to dva zapisy o jedne
# vecerni podivane, ne dve podivane.
#
# Je to odhad, ne dukaz - proto se pouziva jen tam, kde je aspon jeden
# ze zaznamu z importu. Vlastni sber si duplicity nedela.

# O kolik se smi lisit odsledovany cas, aby to porad byl tentyz zazitek.
DELKA_TOLERANCE_S = 180

# Jak daleko od sebe smi byt zacatky.
ROZESTUP_MAX_S = 24 * 3600


def _titul_pro_srovnani(radek: dict[str, Any]) -> str:
    """Nazev, pod kterym se titul pozna napric zdroji.

    Prednost ma nazev z knihovny: kazdy zdroj si titul pojmenuje po svem
    ("Hra o truny - s08e05 - Zvony" vs. "Zvony"), ale jakmile oba zaznamy
    ukazuji na tutez polozku, jmenuje se stejne.
    """
    return str(radek.get("nazev_polozky") or radek.get("item_name") or "").strip().lower()


def import_duplicate_groups() -> list[list[dict[str, Any]]]:
    """Skupiny zaznamu, ktere popisuji tutez podivanou ve dvou zdrojich."""
    radky = db.query_all(
        """
        SELECT p.id, p.user_id, p.item_id, p.item_name, p.device_name,
               p.session_key, p.started_at, p.ended_at, p.watched_seconds,
               p.paused_seconds, p.audio_language, p.subtitle_language,
               p.library_id, p.series_name, p.is_active,
               i.name AS nazev_polozky
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.user_id IS NOT NULL AND p.user_id != ''
           AND p.watched_seconds > 0
         ORDER BY p.started_at, p.id
        """
    )

    skupiny: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for radek in radky:
        zacatek = _epocha(radek["started_at"])
        titul = _titul_pro_srovnani(radek)
        if zacatek is None or not titul:
            continue
        radek["_od"] = zacatek
        radek["_import"] = str(radek["session_key"] or "").startswith("import:")
        # Zarizeni schvalne NENI v klici skupiny - kdyz ho jeden ze zdroju
        # nehlasi, klic by se rozesel a duplicita by se nenasla. Porovnava
        # se az v `_patri_k_sobe()`, kde nezname zarizeni nikomu neodporuje.
        klic = (_normalizuj_id(radek["user_id"]), titul)
        skupiny.setdefault(klic, []).append(radek)

    vysledek: list[list[dict[str, Any]]] = []
    for zaznamy in skupiny.values():
        if len(zaznamy) < 2:
            continue
        zaznamy.sort(key=lambda r: r["_od"])

        rozdelane: list[dict[str, Any]] = []
        for zaznam in zaznamy:
            if rozdelane and _patri_k_sobe(rozdelane[-1], zaznam):
                rozdelane.append(zaznam)
                continue
            if len(rozdelane) > 1 and any(r["_import"] for r in rozdelane):
                vysledek.append(rozdelane)
            rozdelane = [zaznam]
        if len(rozdelane) > 1 and any(r["_import"] for r in rozdelane):
            vysledek.append(rozdelane)

    return vysledek


def _patri_k_sobe(prvni: dict[str, Any], druhy: dict[str, Any]) -> bool:
    """Popisuji tyhle dva zaznamy tutez podivanou?

    Aspon jeden musi byt z importu - vlastni sber duplicity nedela a dva
    poctive zaznamy o dvou ruznych vecerech by se nemely slucovat kvuli
    tomu, ze mel film shodou okolnosti stejnou delku.
    """
    if not (prvni["_import"] or druhy["_import"]):
        return False
    if abs(druhy["_od"] - prvni["_od"]) > ROZESTUP_MAX_S:
        return False

    # Zarizeni si nesmi odporovat. Tentyz film muze jeden clovek pustit
    # na televizi i na telefonu; nezname zarizeni nikomu neodporuje,
    # jinak by se nesparoval zaznam ze zdroje, ktery ho neposila.
    zar_a = str(prvni.get("device_name") or "").strip().lower()
    zar_b = str(druhy.get("device_name") or "").strip().lower()
    if zar_a and zar_b and zar_a != zar_b:
        return False

    delka_a = int(prvni["watched_seconds"] or 0)
    delka_b = int(druhy["watched_seconds"] or 0)
    return abs(delka_a - delka_b) <= DELKA_TOLERANCE_S


def _uplnost(radek: dict[str, Any]) -> tuple[int, ...]:
    """Jak bohaty zaznam to je - podle toho se vybira, ktery zustane."""
    return (
        0 if radek["_import"] else 1,          # vlastni sber napred
        1 if radek.get("audio_language") else 0,
        1 if radek.get("subtitle_language") else 0,
        1 if radek.get("library_id") else 0,
        1 if radek.get("series_name") else 0,
        int(radek.get("watched_seconds") or 0),
    )


def merge_import_duplicates() -> dict[str, Any]:
    """Sloucí zaznamy, ktere tutez podivanou popisuji dvakrat.

    Zustane ten nejbohatsi - typicky z vlastniho sberu, ktery zna jazyk
    a zpusob prehravani. Nescitaji se: dva zapisy o jednom vecernim filmu
    nejsou dve zhlednuti.

    Zacatek se bere nejcasnejsi ze skupiny; je blizsi tomu, kdy se opravdu
    zacalo hrat, nez okamzik, kdy si to ktery nastroj poznamenal.
    """
    skupiny = import_duplicate_groups()
    if not skupiny:
        return {"status": "ok", "groups": 0, "removed": 0}

    smazano = 0
    with db.connect() as conn:
        for skupina in skupiny:
            skupina.sort(key=_uplnost, reverse=True)
            hlavni, ostatni = skupina[0], skupina[1:]

            zacatky = [r["started_at"] for r in skupina if r["started_at"]]
            conn.execute(
                "UPDATE playback SET started_at = ?, watched_seconds = ? WHERE id = ?",
                (min(zacatky) if zacatky else hlavni["started_at"],
                 # Delsi z obou: kratsi zaznam byva ten, ktery nekdo
                 # zastavil driv, nez stihl zapsat konec.
                 max(int(r["watched_seconds"] or 0) for r in skupina),
                 hlavni["id"]),
            )
            for zaznam in ostatni:
                conn.execute("DELETE FROM playback WHERE id = ?", (zaznam["id"],))
                smazano += 1

    log.info("slouceno %d skupin duplicit z importu, smazano %d radku",
             len(skupiny), smazano)
    return {"status": "ok", "groups": len(skupiny), "removed": smazano}


def import_duplicate_count() -> int:
    """Kolik radku by slouceni napric zdroji smazalo."""
    return sum(len(skupina) - 1 for skupina in import_duplicate_groups())


def misplaced_episode_rows() -> list[dict[str, Any]]:
    """Zaznamy, ktere visi na jinem dilu, nez ktery divak videl.

    Naprava po chybe ve slucovani podle tmdb: to bralo u epizody id
    SERIALU (vsechny dily ho maji stejne), takze kazdy sken slil historii
    vsech dilu na jediny. Ve statistikach to vypadalo, ze divak videl jednu
    epizodu dvacetkrat.

    Opravit to jde proto, ze se v zaznamu zachoval **nazev dilu** tak, jak
    se hral. Kdyz sedi na jiny dil tehoz serialu nez ten, na kterem zaznam
    visi, patri tam - a jen tehdy, kdyz je takovy dil pravé jeden.
    Nejednoznacnou shodu radeji nechame byt: spatne prirazena historie je
    horsi nez ta, o ktere vime, ze je posunuta.
    """
    radky = db.query_all(
        """
        SELECT p.id, p.item_id, p.item_name,
               i.name       AS na_polozce,
               i.series_id  AS serial
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE i.series_id IS NOT NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
           AND p.item_name != i.name
        """
    )
    if not radky:
        return []

    # Dily podle serialu a nazvu. Nazev muze v jednom serialu vyjimecne
    # sedet na vic dilu ("Cast 1"); takove skupiny preskocime.
    podle_nazvu: dict[tuple[str, str], list[str]] = {}
    for dil in db.query_all(
        "SELECT id, name, series_id FROM items WHERE series_id IS NOT NULL"
    ):
        klic = (str(dil["series_id"]), str(dil["name"]).strip().lower())
        podle_nazvu.setdefault(klic, []).append(str(dil["id"]))

    opravy = []
    for radek in radky:
        klic = (str(radek["serial"]), str(radek["item_name"]).strip().lower())
        moznosti = podle_nazvu.get(klic, [])
        if len(moznosti) != 1 or moznosti[0] == str(radek["item_id"]):
            continue
        opravy.append({**radek, "spravny_dil": moznosti[0]})
    return opravy


# Jak Jellystat i Playback Reporting skládají název epizody:
#
#     "Seal Team 6 - s02e07 - 7. epizoda"
#     "Nadace - s02e10 - Mýty o stvoření"
#
# Je v tom všechno, co potřebujeme k jednoznačnému určení dílu: seriál,
# řada i číslo. Hledat v knihovně **celý ten řetězec** nemá smysl - tam
# se ten díl jmenuje jen "7. epizoda".
_NAZEV_EPIZODY = re.compile(
    r"^(?P<serial>.+?)\s*[-–]\s*s(?P<rada>\d{1,3})e(?P<dil>\d{1,4})"
    r"(?:\s*[-–]\s*(?P<nazev>.*))?$",
    re.I,
)


def rozbor_nazvu(nazev: Any) -> dict[str, Any] | None:
    """Ze složeného názvu vytáhne seriál, řadu, díl a název dílu."""
    if not nazev:
        return None
    shoda = _NAZEV_EPIZODY.match(str(nazev).strip())
    if not shoda:
        return None
    serial = shoda.group("serial").strip(" -–")
    if not serial:
        return None
    return {
        "serial": serial,
        "rada": int(shoda.group("rada")),
        "dil": int(shoda.group("dil")),
        "nazev": (shoda.group("nazev") or "").strip(),
    }


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
    kandidati = db.query_all(
        """
        SELECT p.item_id, MAX(p.item_name) AS item_name
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
      GROUP BY p.item_id
        """
    )

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
        "popis": "Titul v knihovně je - jen se jeho id píše jinak "
                 "(s pomlčkami / bez nich). Spraví to Uklidit historii.",
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
        "popis": "Typicky „7. epizoda“ nebo „Pilot“ - takový díl má každý "
                 "seriál. Stroj hádat nesmí, ale ty poznáš, kam to patří: "
                 "zkus Dohledat v Jellyfinu, nebo přiřaď ručně.",
        "resitelne": True,
    },
    DUVOD_SERIAL_JE: {
        "nadpis": "Seriál v knihovně je, díl nesedí",
        "popis": "Název nese seriál i číslo dílu, ale takový díl v knihovně "
                 "není - číslování se mezi zdroji rozchází. Ruční přiřazení "
                 "to vyřeší.",
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


async def narovnej_data() -> dict[str, Any]:
    """Srovna historii do poradku. Jedna akce, at nikdo nevybira poradi.

    Kroky jdou za sebou zamerne - kazdy stavi na tom predchozim:

      1. **Dohledani v Jellyfinu.** Identifikator v prevzate historii je
         pravy Jellyfin ItemId. Kdyz ho Jellyfin jeste zna, rekne serial
         i cislo dilu - a tim se zaznam da zaradit. Musi byt prvni: dava
         vazby, se kterymi pracuji vsechny dalsi kroky.
      2. **Navazani podle nazvu a cisla dilu** u toho, co Jellyfin uz
         nezna.
      3. **Vraceni na spravne dily** - naprava po chybe, kdy se cela
         historie serialu slila na jeden dil.
      4. **Slouceni duplicit** v ramci jednoho zdroje i napric zdroji.
      5. **Srovnani nazvu** podle knihovny: prejmenovany titul ma v celem
         prehledu jedno jmeno, ne dve.

    Pouziva to tlacitko v Nastaveni i naplanovana uloha - proto je to
    jedna funkce a ne dva skoro stejne kusy kodu.

    Vraci slovnik s pocty za kazdy krok; klic "casti" je uz hotovy seznam
    vet do hlasky.
    """
    vysledek: dict[str, Any] = {}

    # Dohledani potrebuje Jellyfin. Kdyz nebezi, neni to duvod zastavit
    # zbytek - ten se obejde bez site.
    try:
        vysledek["jellyfin"] = await dohledej_osirele_v_jellyfinu()
    except JellyfinError as exc:
        log.warning("dohledani v Jellyfinu se nepovedlo: %s", exc)
        vysledek["jellyfin"] = {"status": "error", "message": str(exc)}

    vysledek["navazano"] = relink_orphans()
    vysledek["cislo_dilu"] = _link_by_episode_number()
    vysledek["vraceno"] = repair_episode_links()
    vysledek["duplicity"] = merge_duplicate_playback()
    vysledek["z_importu"] = merge_import_duplicates()
    vysledek["nazvy"] = sjednot_nazvy()

    jf = vysledek["jellyfin"]
    casti: list[tuple[str, dict[str, Any]]] = []
    if jf.get("navazano"):
        casti.append(("z Jellyfinu zařazeno: {n} titulů", {"n": jf["navazano"]}))
    if jf.get("zalozeno"):
        casti.append(("doplněno do knihovny: {n} titulů", {"n": jf["zalozeno"]}))
    if jf.get("doplneno"):
        casti.append(("doplněn seriál u {n} titulů", {"n": jf["doplneno"]}))
    if vysledek["navazano"]["items"]:
        casti.append(("navázáno podle názvu: {n} záznamů",
                      {"n": vysledek["navazano"]["rows"]}))
    if vysledek["cislo_dilu"][1]:
        casti.append(("navázáno podle čísla dílu: {n} záznamů",
                      {"n": vysledek["cislo_dilu"][1]}))
    if vysledek["vraceno"]["moved"]:
        casti.append(("vráceno ke správným dílům: {n}",
                      {"n": vysledek["vraceno"]["moved"]}))
    if vysledek["duplicity"]["removed"]:
        casti.append(("sloučeno duplicit: {n}",
                      {"n": vysledek["duplicity"]["removed"]}))
    if vysledek["z_importu"]["removed"]:
        casti.append(("sloučeno napříč zdroji importu: {n}",
                      {"n": vysledek["z_importu"]["removed"]}))
    if vysledek["nazvy"]["rows"]:
        casti.append(("srovnáno názvů podle knihovny: {n}",
                      {"n": vysledek["nazvy"]["rows"]}))

    vysledek["casti"] = casti
    vysledek["zbyva"] = orphan_playback_count()
    log.info("narovnani dat: %s uprav, osirelych zbyva %s",
             len(casti), vysledek["zbyva"])
    return vysledek


def import_summary() -> dict[str, Any]:
    """Kolik zaznamu uz je z importu - aby bylo v Nastaveni videt, co se stalo."""
    return db.query_one(
        """
        SELECT
            SUM(CASE WHEN session_key LIKE 'import:pbr:%' THEN 1 ELSE 0 END) AS playback_reporting,
            SUM(CASE WHEN session_key LIKE 'import:jst:%' THEN 1 ELSE 0 END) AS jellystat,
            SUM(CASE WHEN session_key NOT LIKE 'import:%' THEN 1 ELSE 0 END) AS own,
            MIN(started_at) AS oldest
        FROM playback
        """
    ) or {}
