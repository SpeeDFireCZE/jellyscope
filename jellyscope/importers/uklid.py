"""Úklid duplicit ve vlastní historii a zkrácení nesmyslných délek.

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

# Kolikanasobek delky poradu jeste bereme jako sledovani.
#
# Prehravac spusteny na dvojnasobek delky dilu je porad predstavitelny
# (clovek se vrati a pusti si scenu znovu), tricetinasobek uz ne - to je
# televize, kterou nikdo nevypnul. Polovina navic je kompromis: nechava
# prostor na previjeni a pritom zachyti prave ty pres noc.
STROP_NASOBEK = 1.5


def zkrat_nesmyslne_delky() -> dict[str, int]:
    """Prevzatou historii, kde "sledovani" trva nasobky delky poradu.

    Playback Reporting i Jellystat meri cas **na hodinach**: od spusteni
    po konec relace. Kdyz clovek usne u dilu a prehravac zustane otevreny
    do rana, hlasi zdroj patnact hodin - a Jellyscope to prebral tak, jak
    to prislo. Ve statistikach pak jeden dil prebil cely mesic.

    Poznat to jde jedine podle delky poradu: kdyz je odsledovany cas
    nasobkem toho, co ma titul minutaze, nesledovalo se - jen bezel
    prehravac.

    **Nic se nezahazuje.** Prebytek se preleje do `paused_seconds`, takze
    soucet obou porad odpovida rozmezi od zacatku do konce relace; meni
    se jen to, co se pocita jako sledovani. Casy zacatku a konce zustavaji
    presne takove, jake byly - kdyz bude potreba se k tomu vratit, je
    z ceho.

    Tyka se to **jen prevzate historie** (session_key zacina "import:").
    Co nasbiral collector, ma prirustek omezeny uz pri mereni - viz
    collector.poll_once() a jeho max_gap_seconds.
    """
    adepti = db.query_all(
        """
        SELECT p.id, p.watched_seconds, p.paused_seconds, i.runtime_ticks
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.session_key LIKE 'import:%'
           AND i.runtime_ticks IS NOT NULL
           AND i.runtime_ticks > 0
           AND p.watched_seconds > 0
        """
    )

    zmenene: list[tuple[int, int, int]] = []
    for radek in adepti:
        delka = float(radek["runtime_ticks"]) / 10_000_000
        strop = int(delka * STROP_NASOBEK)
        odsledovano = int(radek["watched_seconds"] or 0)
        if strop <= 0 or odsledovano <= strop:
            continue
        prebytek = odsledovano - strop
        zmenene.append((strop, int(radek["paused_seconds"] or 0) + prebytek,
                        int(radek["id"])))

    if not zmenene:
        return {"rows": 0, "seconds": 0}

    with db.connect() as conn:
        for strop, pauza, radek_id in zmenene:
            conn.execute(
                "UPDATE playback SET watched_seconds = ?, paused_seconds = ? "
                " WHERE id = ?",
                (strop, pauza, radek_id))

    presunuto = sum(pauza for _, pauza, _ in zmenene)
    log.info("zkraceno %s prevzatych zaznamu, ktere bezely dele nez porad",
             len(zmenene))
    return {"rows": len(zmenene), "seconds": presunuto}


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
