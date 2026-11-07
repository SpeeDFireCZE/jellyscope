"""Stav a růst knihovny v čase (snímky, dopočtená křivka).

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .. import db, formatting
from .obdobi import _mistni_den, _obdobi
from .technika import RESOLUTION_CASE, ROZSAH_CASE


def _dopoctena_krivka(prvni: str, posledni: str) -> list[dict[str, Any]]:
    """Velikost knihovny den po dni, dopoctena z toho, co uz v datech je.

    Denni snimky zacinaji az u verze, ktera je umi zapisovat. Minulost se
    ale dopocitat DA, protoze dva sloupce ji nesou:

    * `date_created` - kdy polozka vznikla,
    * `synced_at` u polozky v archivu (`is_missing = 1`) - posledni
      synchronizace, ktera ji jeste videla. Polozky se nemazou, jen
      schovavaji, takze i ubytek je videt.

    Polozka se tedy do knihovny pocita od sveho vzniku do dne, kdy ji
    aplikace naposledy videla.

    **Ceho se dopocet nikdy nedobere** (proto snimky zustavaji):

    * Minulost popisuje DNESNIMI velikostmi. Film prekodovany z 30 GB na
      10 GB vyjde, jako by byl 10GB odjakziva - snimek z toho dne rika
      30 GB a ma pravdu.
    * Prekodovani zaklada v Jellyfinu novou polozku s novym datem (proto
      je v `items` vubec `tmdb_id`), takze se cte jako "pribylo dnes".
    * Co bylo smazane pred prvni synchronizaci, v datech neni vubec.
    * Volne misto se dopocitat neda - to je jen ve snimcich.

    Den se bere z `date_created`, ktere je v UTC, kdezto snimky jsou
    v zone aplikace. U krivky pres mesice je to posun o hodiny a nestoji
    za to ho resit; u presnosti na den by stalo.
    """
    radky = db.query_all(
        """
        SELECT substr(date_created, 1, 10) AS vznik,
               CASE WHEN is_missing = 1 THEN substr(synced_at, 1, 10) END AS naposledy,
               is_missing,
               COALESCE(size_bytes, 0) AS velikost,
               type
          FROM items
         WHERE date_created IS NOT NULL AND date_created != ''
        """
    )
    if not radky:
        return []

    # Zmeny se posbiraji do jednoho slovniku a pak se jednou projdou -
    # jeden dotaz na kazdy den by pri roce znamenal 365 dotazu.
    zmeny: dict[str, dict[str, int]] = {}

    def zmena(den: str, polozek: int, velikost: int, filmu: int, epizod: int) -> None:
        z = zmeny.setdefault(den, {"polozek": 0, "velikost": 0,
                                   "filmu": 0, "epizod": 0})
        z["polozek"] += polozek
        z["velikost"] += velikost
        z["filmu"] += filmu
        z["epizod"] += epizod

    for radek in radky:
        vznik = str(radek["vznik"] or "")
        if len(vznik) != 10:
            continue

        # Polozka, ktera uz v knihovne NENI a nevime kdy odesla, se
        # nepocita vubec. Jinak by v krivce zustala navzdy a posledni bod
        # by byl vyssi nez "Velikost celkem" hned vedle - graf by si
        # protirecil s cislem na teze strance. Podhodnotit minulost je
        # mensi zlo nez prepsat pritomnost.
        naposledy = str(radek["naposledy"] or "")
        if int(radek["is_missing"] or 0) and len(naposledy) != 10:
            continue

        velikost = int(radek["velikost"] or 0)
        film = 1 if str(radek["type"] or "") == "Movie" else 0
        epizoda = 1 if str(radek["type"] or "") == "Episode" else 0
        zmena(vznik, 1, velikost, film, epizoda)

        if len(naposledy) == 10:
            # Ubyde TIM dnem, ne az nasledujicim. `synced_at` je posledni
            # synchronizace, ktera polozku jeste videla - ted uz v knihovne
            # neni. Kdyby se odecetla az zitra, polozka smazana dnes by se
            # do dnesniho bodu jeste zapocitala a konec krivky by byl vyssi
            # nez "Velikost celkem" hned vedle.
            zmena(naposledy, -1, -velikost, -film, -epizoda)

    # Prubezny soucet od nejstarsi zmeny az po konec obdobi.
    krivka: list[dict[str, Any]] = []
    stav = {"polozek": 0, "velikost": 0, "filmu": 0, "epizod": 0}
    den = date.fromisoformat(min(zmeny))
    konec_dne = date.fromisoformat(posledni)
    zacatek_dne = date.fromisoformat(prvni)
    while den <= konec_dne:
        klic = den.isoformat()
        if klic in zmeny:
            for jmeno, hodnota in zmeny[klic].items():
                stav[jmeno] += hodnota
        if den >= zacatek_dne:
            krivka.append({"den": klic, "polozek": stav["polozek"],
                           "filmu": stav["filmu"], "epizod": stav["epizod"],
                           "velikost": stav["velikost"], "uhd": None, "hdr": None,
                           "bez_technik": None, "volne_misto": None,
                           "dopocteno": True})
        den += timedelta(days=1)
    return krivka


def stav_knihovny() -> dict[str, int]:
    """Jak knihovna vypadá teď - tytéž součty, jaké zapisuje denní snímek.

    Jedna funkce, protože se ptají tři místa: dlaždice „Velikost celkem",
    zápis snímku po synchronizaci a konec křivky růstu. Kdyby si každé
    počítalo po svém, dřív nebo později se rozejdou - a hádat se pak dá
    jen o tom, které z těch čísel lže.
    """
    radek = db.query_one(
        f"""
        SELECT COUNT(*)                        AS polozek,
               SUM(CASE WHEN type = 'Movie'    THEN 1 ELSE 0 END) AS filmu,
               SUM(CASE WHEN type = 'Episode'  THEN 1 ELSE 0 END) AS epizod,
               COALESCE(SUM(COALESCE(size_bytes, 0)), 0)          AS velikost,
               SUM(CASE WHEN {RESOLUTION_CASE} = '4K' THEN 1 ELSE 0 END) AS uhd,
               SUM(CASE WHEN {ROZSAH_CASE} IN ('HDR', 'DOVI') THEN 1 ELSE 0 END) AS hdr,
               SUM(CASE WHEN tech_source IS NULL THEN 1 ELSE 0 END) AS bez_technik
          FROM items
         WHERE is_missing = 0
        """
    ) or {}
    return {jmeno: int(radek.get(jmeno) or 0)
            for jmeno in ("polozek", "filmu", "epizod", "velikost",
                          "uhd", "hdr", "bez_technik")}


def snimky(days: Any = 90) -> list[dict[str, Any]]:
    """Velikost knihovny den po dni - podklad pro krivku rustu.

    Slozene ze dvou zdroju, a stranka ma vedet, ktery je ktery:

    * **denni snimky** od chvile, kdy se zacaly zapisovat - to, co se ten
      den skutecne zmerilo, vcetne volneho mista;
    * **dopoctena minulost** pro dobu pred prvnim snimkem, viz
      `_dopoctena_krivka()`. Bez ni by krivka zacinala dnem instalace
      a otazka "o kolik knihovna narostla za rok" nemela odpoved.

    Snimek ma prednost vzdycky, kdyz pro dany den existuje: je to mereni,
    ne odhad.
    """
    obdobi = _obdobi(days)
    prvni = _mistni_den(obdobi.od_mistni, obdobi.od).isoformat()
    posledni = _mistni_den(obdobi.do_mistni, obdobi.do, konec=True).isoformat() \
        if not obdobi.relativni else datetime.now(formatting.zona()).date().isoformat()

    zmerene = db.query_all(
        """
        SELECT den, polozek, filmu, epizod, velikost, uhd, hdr,
               bez_technik, volne_misto
          FROM library_snapshot
         WHERE den >= ? AND den <= ?
         ORDER BY den
        """,
        (prvni, posledni),
    )
    for radek in zmerene:
        radek["dopocteno"] = False

    # Dopocet konci tam, kde zacina prvni snimek VUBEC - ne prvni snimek
    # v obdobi. Kdyby se bral az ten v obdobi, u obdobi zacinajiciho po
    # instalaci by se minulost dopocitala znovu pres dobu, o ktere mame
    # mereni.
    od_kdy_merime = db.query_value(
        "SELECT MIN(den) FROM library_snapshot", default=None)

    hranice = str(od_kdy_merime or "")
    if hranice and hranice <= prvni:
        return zmerene           # cele obdobi mame zmerene

    cela = _dopoctena_krivka(prvni, posledni)
    dopoctene = [radek for radek in cela if not hranice or radek["den"] < hranice]
    if not dopoctene:
        return zmerene

    # Dopocet se POSADI na prvni zmerenou hodnotu.
    #
    # Tvar minulosti (kdy co pribylo) z dat vycteme spolehlive, ale jeji
    # VYSKA je systematicky posunuta: dopocet popisuje minulost dnesnimi
    # velikostmi, kdezto snimek zna tu tehdejsi. Bez posazeni je na spoji
    # schod - a "knihovna pres noc prisla o 200 GB" je tvrzeni o konkretnim
    # dni, ktere je proste nepravdive. Posun je jedno cislo pro celou
    # dopoctenou cast, takze se tvar nemeni.
    prvni_zmereny = next((r for r in zmerene if r["den"] >= hranice), None)
    if prvni_zmereny:
        ve_spoji = next((r for r in cela if r["den"] == prvni_zmereny["den"]), None)
        if ve_spoji:
            for jmeno in ("velikost", "polozek", "filmu", "epizod"):
                posun = int(prvni_zmereny.get(jmeno) or 0) - int(ve_spoji.get(jmeno) or 0)
                if not posun:
                    continue
                for radek in dopoctene:
                    # Zaporna knihovna neexistuje; u velkeho posunu se
                    # nejstarsi dny opreou o nulu.
                    radek[jmeno] = max(0, int(radek.get(jmeno) or 0) + posun)

    # Krivka musi navazovat: posledni dopocteny den a prvni zmereny den
    # jsou vedle sebe, takze se cara neprerusi.
    return dopoctene + zmerene


def _volne_misto_ted(ze_snimku: Any) -> int | None:
    """Kolik mista zbyva TED, ne kolik ho bylo pri posledni synchronizaci.

    Snimek nese volne misto z okamziku, kdy se zapsal - a ten byva
    v noci. Kdyz se mezitim v nastaveni zada kapacita uloziste rucne,
    musi se odhad "misto dojde za X dnu" prepocitat hned; cekat na dalsi
    synchronizaci by znamenalo, ze zadana hodnota jako by nic neudelala.

    `scanner` az tady: sam si tahne stats, takze nahore by z toho byl kruh.
    """
    try:
        from .. import scanner

        ted = scanner._volne_misto_knihovny()
    except Exception:                     # noqa: BLE001
        # Zjistovani mista sahá na disk a na nastaveni - kdyz se to
        # nepovede, plati posledni znama hodnota ze snimku.
        return ze_snimku
    return ted if ted is not None else ze_snimku


def rust_knihovny(days: Any = 90) -> dict[str, Any]:
    """Kam knihovna spěje: o kolik za obdobi narostla a co z toho plyne.

    Odhad umi dve podoby, protoze volne misto zname jen tam, kam aplikace
    na soubory vidi:

    * zname-li ho, odpovida na "za jak dlouho dojde";
    * jinak rekne, jak velka knihovna bude za rok.

    Slibovat prvni tam, kde plati jen druhe, by bylo horsi nez mlcet.
    """
    radky = snimky(days)
    if len(radky) < 2:
        # Jeden bod neni rust. Do te doby se hlasi, kolik dni uz mame -
        # at je videt, ze se neco deje a jen to jeste nema co rict.
        return {"dost_dat": False, "dnu": len(radky),
                "velikost": radky[-1]["velikost"] if radky else 0}

    prvni, posledni = radky[0], radky[-1]
    dnu = max(1, (date.fromisoformat(posledni["den"])
                  - date.fromisoformat(prvni["den"])).days)
    prirustek = int(posledni["velikost"]) - int(prvni["velikost"])
    denne = prirustek / dnu

    vysledek: dict[str, Any] = {
        "dost_dat": True,
        "dnu": dnu,
        "velikost": int(posledni["velikost"]),
        "prirustek": prirustek,
        "denne": denne,
        "polozek": int(posledni["polozek"]) - int(prvni["polozek"]),
        "volne_misto": _volne_misto_ted(posledni.get("volne_misto")),
        "za_rok": None,
        "dnu_do_konce": None,
        # Stoji cast obdobi na dopoctu misto na mereni? Stranka to ma
        # rict - dopocet popisuje minulost dnesnimi velikostmi, takze
        # cislo je odhad, i kdyz vypada stejne presne jako ostatni.
        "dopocteno": any(radek.get("dopocteno") for radek in radky),
        "dopocteno_do": next((radek["den"] for radek in reversed(radky)
                              if radek.get("dopocteno")), ""),
    }

    # Klesajici nebo stojici knihovna zadny odhad nepotrebuje - a delit
    # nulou uz vubec ne.
    if denne <= 0:
        return vysledek

    vysledek["za_rok"] = int(posledni["velikost"] + denne * 365)
    # Tatáž hodnota jako o kus výš, ne znovu ze snímku: jinak by se
    # ručně zadaná kapacita projevila na řádku „volného místa", ale
    # odhad „místo dojde za" by dál počítal s tím, co bylo v noci.
    volne = vysledek["volne_misto"]
    if volne:
        vysledek["dnu_do_konce"] = int(volne / denne)
    return vysledek
