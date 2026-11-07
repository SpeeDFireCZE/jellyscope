"""Duplicity mezi dvěma zdroji importu a díly u špatného seriálu.

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db
from .shoda import _epocha
from .zaklad import _normalizuj_id

log = logging.getLogger("jellyscope.importers")


# ---------------------------------------------------------------------------
# Duplicity mezi dvema zdroji importu
# ---------------------------------------------------------------------------
#
# `duplicate_playback_groups()` (v uklid.py) hleda **prekryv v case**. Na duplicity
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


def _existuje_import() -> bool:
    """Je v historii aspon jeden radek z importu?

    Levna otazka misto drahe odpovedi. Skupina se totiz zapocita jen
    tehdy, kdyz je v ni aspon jeden importovany zaznam - kdo nikdy nic
    neimportoval, dostane vzdycky prazdny vysledek, jen az po pruchodu
    celou historii.

    Zmereno na 150 000 radcich: tenhle dotaz 100 ms, cely pruchod
    2 943 ms. Kdo import ma, plati navic tech 100 ms a pocita se dal.
    """
    return bool(db.query_value(
        "SELECT 1 FROM playback WHERE session_key LIKE 'import:%' LIMIT 1"))


def import_duplicate_groups() -> list[list[dict[str, Any]]]:
    """Skupiny zaznamu, ktere popisuji tutez podivanou ve dvou zdrojich."""
    if not _existuje_import():
        return []

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
