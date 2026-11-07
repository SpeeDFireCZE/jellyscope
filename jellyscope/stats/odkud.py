"""Odkud se dívají: domácí síť nebo internet, místa a země diváků.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from typing import Any

from .. import db
from .obdobi import _meze


# Adresy, ktere nevedou ven ze site. Nejde o bezpecnost, jen o rozliseni
# "z gauce" a "odjinud" - proto staci prefixy a neresime masky.
DOMACI_PREFIXY = ("10.", "192.168.", "127.", "172.16.", "172.17.", "172.18.",
                  "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                  "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                  "172.29.", "172.30.", "172.31.", "169.254.", "::1", "fe80:",
                  "fc", "fd")


def je_domaci(adresa: Any) -> bool:
    """Je ta adresa z domaci site?"""
    text = str(adresa or "").strip().lower()
    if not text:
        return False
    return text.startswith(DOMACI_PREFIXY)


def _provoz_podle_adresy(days: int) -> list[dict[str, Any]]:
    """Kolik toho teklo z jednotlive adresy - podklad pro tabulku i mapu.

    Obe stranky potrebuji totez secteni; drive to byly dva skoro stejne
    dotazy a lisily se jen tim, ze mapa uz v SQL vynechavala prazdnou
    adresu. Vynechat ji v Pythonu je totez a dotaz zbyva jeden.
    """
    return db.query_all(
        """
        SELECT remote_address                         AS adresa,
               SUM(watched_seconds)                   AS sekund,
               SUM(watched_seconds * COALESCE(bitrate, 0)) / 8.0 AS bajtu,
               COUNT(*)                               AS plays,
               COUNT(DISTINCT user_id)                AS lidi,
               MAX(started_at)                        AS naposledy
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
         GROUP BY remote_address
         ORDER BY SUM(watched_seconds) DESC
        """,
        (*_meze(days),),
    )


def odkud_se_divaji(days: int, limit: int = 20) -> dict[str, Any]:
    """Rozpad podle toho, odkud se prehravalo.

    Proc ne mapa: adresa v domaci siti (192.168.x.x) zadne misto na svete
    neoznacuje - je stejna v Praze i v Sydney. Zemepisne umistit jde jen
    verejnou adresu, a i to potrebuje offline databazi GeoIP. Rozdeleni
    "doma / z internetu" je proto to jedine, co jde spocitat poctive
    z toho, co mame.

    Importovana historie adresu nenese vubec - Jellystat ani Playback
    Reporting ji neposilaji. Takove zaznamy jsou "neznamo odkud".
    """
    rows = _provoz_podle_adresy(days)

    skupiny = {"doma": {"plays": 0, "bajtu": 0.0, "sekund": 0},
               "internet": {"plays": 0, "bajtu": 0.0, "sekund": 0},
               "neznamo": {"plays": 0, "bajtu": 0.0, "sekund": 0}}
    adresy = []
    for r in rows:
        if not (r["adresa"] or "").strip():
            kam = "neznamo"
        elif je_domaci(r["adresa"]):
            kam = "doma"
        else:
            kam = "internet"
        skupiny[kam]["plays"] += int(r["plays"] or 0)
        skupiny[kam]["bajtu"] += float(r["bajtu"] or 0)
        skupiny[kam]["sekund"] += int(r["sekund"] or 0)
        if kam != "neznamo" and len(adresy) < limit:
            adresy.append({
                "adresa": r["adresa"], "domaci": kam == "doma",
                "plays": int(r["plays"] or 0), "lidi": int(r["lidi"] or 0),
                "sekund": int(r["sekund"] or 0), "bajtu": int(r["bajtu"] or 0),
                "naposledy": r["naposledy"],
            })

    for hodnoty in skupiny.values():
        hodnoty["bajtu"] = int(hodnoty["bajtu"])
    return {"skupiny": skupiny, "adresy": adresy}


def mista(days: int) -> list[dict[str, Any]]:
    """Všechna místa za období, odkud se přehrávalo - bez limitu a bez řazení.

    Místo = jedny souřadnice z GeoIP. Nezaokrouhluje se: GeoLite2 dává
    městům souřadnice po čtvrtích (Praha jich má jedenáct) a zaokrouhlení
    na desetinu stupně z jednoho města dělalo několik teček se stejným
    jménem. Slévání podle přiblížení dělá až mapa.shluky().

    Umisťují se **jen veřejné adresy**: ta z domácí sítě žádné místo
    neoznačuje. Když databáze GeoLite2 chybí (nebo chybí knihovna, která
    ji umí číst), vrací se prázdný seznam a stránka mapu vůbec neukáže.

    Jeden dotaz po dvojicích adresa-divák: z něj jsou součty i skuteční
    lidé. Počet lidí se z COUNT(DISTINCT) po adresách poskládat nedá -
    člověk ze dvou adres by byl dva, tři lidé ze tří adres jeden.
    `divaci` je množina user_id jen pro Python (shluky ji sjednocují),
    do stránky se nedostane.
    """
    from .. import geoip

    if not geoip.je_k_dispozici():
        return []

    adresy: dict[str, dict[str, Any]] = {}
    for r in db.query_all(
        """
        SELECT remote_address                         AS adresa,
               user_id,
               SUM(watched_seconds)                   AS sekund,
               SUM(watched_seconds * COALESCE(bitrate, 0)) / 8.0 AS bajtu,
               COUNT(*)                               AS plays
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
           AND remote_address IS NOT NULL AND remote_address != ''
         GROUP BY remote_address, user_id
        """,
        (*_meze(days),),
    ):
        adresa = str(r["adresa"] or "")
        # Prazdna adresa je import (ten ji nenese), domaci nic neoznacuje.
        if not adresa or je_domaci(adresa):
            continue
        zaznam = adresy.setdefault(adresa, {"plays": 0, "sekund": 0, "bajtu": 0.0,
                                            "divaci": set()})
        zaznam["plays"] += int(r["plays"] or 0)
        zaznam["sekund"] += int(r["sekund"] or 0)
        zaznam["bajtu"] += float(r["bajtu"] or 0)
        if r["user_id"]:
            zaznam["divaci"].add(str(r["user_id"]))

    podle_souradnic: dict[tuple[float, float], dict[str, Any]] = {}
    for adresa, zaznam in adresy.items():
        misto = geoip.najdi(adresa)
        if not misto:
            continue
        klic = (float(misto["lat"]), float(misto["lon"]))
        bod = podle_souradnic.setdefault(klic, {
            "lat": klic[0], "lon": klic[1],
            "mesto": misto["mesto"], "zeme": misto["zeme"], "kod": misto["kod"],
            "plays": 0, "sekund": 0, "bajtu": 0.0, "adres": 0, "divaci": set(),
        })
        bod["plays"] += zaznam["plays"]
        bod["sekund"] += zaznam["sekund"]
        bod["bajtu"] += zaznam["bajtu"]
        bod["adres"] += 1
        bod["divaci"] |= zaznam["divaci"]

    vysledek = list(podle_souradnic.values())
    for bod in vysledek:
        bod["bajtu"] = int(bod["bajtu"])
        bod["divaci"] = frozenset(bod["divaci"])
        bod["lidi"] = len(bod["divaci"])
        bod["popis"] = ", ".join(x for x in (bod["mesto"], bod["zeme"]) if x) or "?"
    return vysledek


def hotspoty(days: int, limit: int = 500,
             predpocitana: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Nejaktivnějších `limit` míst pro mapu, od nejsledovanějšího.

    `predpocitana` jsou místa z mista() - karta Mapa je spočítá jednou pro
    mapu i tabulku zemí. Při shodě času rozhodují souřadnice, ať je pořadí
    pokaždé stejné a shluky na mapě neposkakují.
    """
    vse = predpocitana if predpocitana is not None else mista(days)
    return sorted(vse, key=lambda b: (-b["sekund"], b["lat"], b["lon"]))[:limit]


def zeme_divaku(days: int,
                predpocitana: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Totez jako hotspoty, jen secteno po zemich - pro tabulku vedle mapy.

    Sčítá se ze všech míst (mapa jich kreslí jen nejaktivnějších 500), a
    „míst" jsou různá **města**: Praha je jedno místo, i když má na mapě
    tečku za každou čtvrť.
    """
    podle_zeme: dict[str, dict[str, Any]] = {}
    for bod in (predpocitana if predpocitana is not None else mista(days)):
        zaznam = podle_zeme.setdefault(bod["zeme"] or "?", {
            "label": bod["zeme"] or "?", "kod": bod["kod"],
            "plays": 0, "sekund": 0, "bajtu": 0, "mesta": set(),
        })
        zaznam["plays"] += bod["plays"]
        zaznam["sekund"] += bod["sekund"]
        zaznam["bajtu"] += bod["bajtu"]
        zaznam["mesta"].add(bod["popis"])
    for zaznam in podle_zeme.values():
        zaznam["mist"] = len(zaznam.pop("mesta"))
    return sorted(podle_zeme.values(), key=lambda z: z["sekund"], reverse=True)
