"""Narovnání dat (všechny opravy za sebou) a souhrn importu.

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, scanner
from ..jellyfin import JellyfinError
from .dvojice import merge_import_duplicates
from .osirele import (
    dohledej_osirele_v_jellyfinu,
    orphan_playback_count,
    relink_orphans,
    repair_episode_links,
    sjednot_nazvy,
)
from .propojeni import _link_by_episode_number
from .uklid import merge_duplicate_playback, zkrat_nesmyslne_delky

log = logging.getLogger("jellyscope.importers")


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
      5. **Zkraceni nesmyslnych delek** v prevzate historii: co beželo
         nasobky delky poradu, byl otevreny prehravac, ne sledovani.
      6. **Srovnani nazvu** podle knihovny: prejmenovany titul ma v celem
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

    # Nejdriv archiv: kdyz se dil vrati k zivemu zaznamu, maji dalsi kroky
    # o jednu polozku min, na kterou by mohly historii navazat.
    vysledek["archiv"] = scanner.slouc_archiv_do_zivych()
    vysledek["navazano"] = relink_orphans()
    vysledek["cislo_dilu"] = _link_by_episode_number()
    vysledek["vraceno"] = repair_episode_links()
    vysledek["duplicity"] = merge_duplicate_playback()
    vysledek["z_importu"] = merge_import_duplicates()
    vysledek["delky"] = zkrat_nesmyslne_delky()
    vysledek["nazvy"] = sjednot_nazvy()

    jf = vysledek["jellyfin"]
    casti: list[tuple[str, dict[str, Any]]] = []
    if vysledek["archiv"]:
        casti.append(("vráceno z archivu k živým dílům: {n}",
                      {"n": vysledek["archiv"]}))
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
    if vysledek["delky"]["rows"]:
        casti.append(("zkráceno přehrávání delších než pořad: {n}",
                      {"n": vysledek["delky"]["rows"]}))
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
