# -*- coding: utf-8 -*-
"""Sekce, ze kterých si jde poskládat vlastní přehled.

Každá stránka aplikace si dnes svá data spočítá v routě a šablona je
vykreslí v pevném pořadí. Vlastní přehled potřebuje něco jiného: skládat
se dá jen to, co umí stát samo. Sekce je proto **dvojice** - kus šablony
a funkce, která mu obstará data - zapsaná pod svým klíčem.

Dvě věci, na kterých to celé stojí:

* **Šablonky jsou tytéž, které používají původní stránky.** Kdyby vznikla
  kopie, jednu z nich by časem někdo upravil a druhou zapomněl - a dvě
  místa v aplikaci by tvrdila každé něco jiného.
* **Počítá se jen to, co je poskládané.** Přehled dnes spočítá deset
  sekcí každému bez ohledu na to, jestli se na ně dívá; tady si člověk
  vybere a zbytek se ani nespustí.

Přidat sekci znamená doplnit jednu položku do `SEZNAM` - nic víc.
"""
from __future__ import annotations

import sys
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from . import db, formatting, geoip, i18n, insights, langstats, mapa_podrobna, pristup, stats
from .i18n import translate as _t

# Paměť jednoho data_pro(): karty, které potřebují totéž (tabulka uživatelů
# pro graf i tabulku, barvy jazyků pro čtyři jazykové karty), to spočítají
# jednou. Kontextová proměnná, ne globální slovník: data_pro běží ve vláknech
# souběžných požadavků a každý potřebuje svou.
_pamet: ContextVar[dict[Any, Any] | None] = ContextVar("sekce_pamet", default=None)


def _jednou(funkce: Callable[..., Any], *args: Any) -> Any:
    """`funkce(*args)` - v rámci jednoho data_pro() spočítané jen jednou.

    Výsledek se pak sdílí, takže se nesmí měnit na místě. Nemění se:
    data_pro ho jen přisype do kontextu a anonymizace dělá kopie. Mimo
    data_pro, nebo s argumentem, který nejde použít jako klíč, se funkce
    prostě zavolá.
    """
    pamet = _pamet.get()
    klic = (funkce, args)
    try:
        hash(klic)
    except TypeError:
        pamet = None
    if pamet is None:
        return funkce(*args)
    if klic not in pamet:
        pamet[klic] = funkce(*args)
    return pamet[klic]


def _barvy() -> dict[str, Any]:
    """Barvy jazyků - stejné pro všechny jazykové karty."""
    return _jednou(langstats.colour_map)


def _sledovane_jazyky(obdobi: Any) -> Any:
    return langstats.watched_languages(obdobi, _barvy())


def stav_geoip() -> dict[str, Any]:
    """Co mapa potřebuje a jestli to má - pro stránku Síť i kartu v přehledu.

    Knihovna maxminddb (nepovinná), databáze GeoLite2 (stahuje se na
    kliknutí), její velikost a stáří. A cesta k pythonu, kterým aplikace
    běží: návod na doinstalování pak ukáže příkaz, který jde zkopírovat,
    ne obecné "pip install", které by ve virtuálním prostředí minulo.
    """
    return {
        "knihovna": geoip.knihovna_je(),
        "databaze": geoip.je_k_dispozici(),
        "bajtu": geoip.velikost_databaze(),
        "stari": geoip.stari_databaze(),
        "python": sys.executable,
    }


def data_rustu(obdobi: Any) -> dict[str, Any]:
    """Podklad pro křivku růstu knihovny.

    Graf kreslí čísla, ne bajty, takže se ke každému snímku dopočítá
    velikost v gigabajtech. Dělá se to tady, ne v SQL: v databázi má
    zůstat to, co se změřilo, ne převod kvůli jednomu grafu.

    Křivka se rozpadá na dvě série, aby šlo poznat, co je měření a co
    dopočet z data vzniku položek (viz stats.snimky). Kde série neplatí,
    je `None`, ne nula - nula by nakreslila propad k ose, který se nestal.

    Poslední dopočtený den nese hodnotu i v měřené sérii, takže na sebe
    obě čáry navazují a mezi nimi není díra.
    """
    radky = stats.snimky(obdobi)

    # Jednotka se vybírá podle velikosti knihovny, ne napevno.
    #
    # Graf kreslil vždycky gigabajty, kdežto dlaždice vedle něj píše
    # „12,8 TB". U dvanáctiterabajtové knihovny tak na ose stálo 13 101
    # a v dlaždici 12,8 - jedno a totéž číslo, jen jinou jednotkou, což
    # se čte jako „graf ukazuje víc". Teď mluví obojí stejně.
    nejvic = max((int(r.get("velikost") or 0) for r in radky), default=0)
    if nejvic >= 1024 ** 4:
        delitel, jednotka, mist = 1024 ** 4, "TB", 2
    else:
        delitel, jednotka, mist = 1024 ** 3, "GB", 2

    prvni_zmereny = next((i for i, r in enumerate(radky)
                          if not r.get("dopocteno")), None)
    for index, radek in enumerate(radky):
        hodnota = round(int(radek.get("velikost") or 0) / delitel, mist)
        radek["gb"] = hodnota
        dopocteno = bool(radek.get("dopocteno"))
        radek["gb_dopocteno"] = hodnota if dopocteno else None
        radek["gb_zmereno"] = None if dopocteno else hodnota
        # Spoj: poslední dopočtený den patří i měřené čáře, jinak by mezi
        # nimi byla mezera přes celý den.
        if prvni_zmereny is not None and index == prvni_zmereny - 1:
            radek["gb_zmereno"] = hodnota
    return {"snimky": radky, "rust": stats.rust_knihovny(obdobi),
            "jednotka_rustu": jednotka}


def souhrn_obdobi(obdobi: Any) -> dict[str, Any]:
    """Cisla hlavni karty Prehledu: souhrn, zmeny a proc se nedá srovnat.

    Jedno misto, protoze totez potrebuje Prehled i sekce vlastniho
    prehledu. Kdyby se to pocitalo dvakrat, jedna stranka by casem
    ukazovala jina procenta nez druha.
    """
    soucasny = stats.overview(obdobi)
    predchozi = stats.previous_overview(obdobi)
    # Srovnavat se da jen s obdobim, ze ktereho mame data. Kdyz saha dal,
    # nez kam nase historie, neni to srovnani dvou obdobi, ale obdobi
    # s prazdnem - a procenta z toho vyjdou libovolne velka. (Skutecny
    # pripad: filtr "letos" ukazal 1 619 572 %.)
    srovnatelne = stats.lze_srovnat(obdobi)

    def zmena(ted: Any, drive: Any) -> float | None:
        if not srovnatelne:
            return None
        try:
            nyni, dric = float(ted or 0), float(drive or 0)
        except (TypeError, ValueError):
            return None
        if dric <= 0:
            return None
        return (nyni - dric) / dric * 100

    # Kdyz srovnat nejde, rekneme proc - misto tise chybejici sipky.
    poznamka = ""
    if not srovnatelne:
        odkdy = stats.prvni_zaznam()
        poznamka = (_t("nemáme data za předchozí období, historie začíná {datum}")
                    .format(datum=formatting.cesky_datum(odkdy[:10])) if odkdy
                    else _t("zatím není co srovnávat"))

    return {
        "overview": soucasny,
        "deltas": {
            "watched": zmena(soucasny.get("watched_seconds"),
                             predchozi.get("watched_seconds")),
            "plays": zmena(soucasny.get("plays"), predchozi.get("plays")),
        },
        "poznamka_srovnani": poznamka,
    }


def _data_mapy(obdobi: Any) -> dict[str, Any]:
    """Data karty Mapa. Místa se spočítají jednou - pro mapu i tabulku zemí;
    u velké historie je to nejdražší dotaz stránky Síť."""
    vse = stats.mista(obdobi)
    return {"hotspoty": stats.hotspoty(obdobi, predpocitana=vse),
            "zeme": stats.zeme_divaku(obdobi, predpocitana=vse),
            "geo": stav_geoip(), "podrobna": mapa_podrobna.stav(),
            "podrobna_chyba": mapa_podrobna.posledni_chyba()}


@dataclass(frozen=True)
class Sekce:
    """Jedna přeskládatelná sekce.

    `data` dostane zvolené období a vrátí slovník, který jde rovnou
    přisypat do kontextu šablony. Klíče musí být napříč sekcemi různé -
    díky tomu se šablonky nemusí měnit a fungují na obou místech.
    """

    klic: str
    nazev: str
    popis: str
    sablona: str
    data: Callable[[Any], dict[str, Any]]
    # Reaguje sekce na filtr období nahoře? "Právě se hraje" ani
    # "Nedávno přidané" se ho netýkají - je to stav, ne období.
    obdobi: bool = True
    # Jak široký panel: třetina, půlka, celá šířka. V registru je to, co
    # sekci sluší; člověk si to při úpravě přehledu může přepnout.
    sirka: str = "cela"
    # Obalit vykreslenou šablonku kartou? Většina šablonek si kartu kreslí
    # sama; pár jich je psaných jako *vnitřek* karty, protože se na své
    # původní stránce vkládají do už otevřeného <div class="card">.
    obal_karta: bool = False
    # Oblast stromu práv (pristup.OBLASTI), pod kterou karta patří - ta
    # stránky, ze které pochází. Kdo oblast nevidí, kartu nedostane ani
    # v přehledu (viz smi). Povinná a jen pojmenovaná: karta bez ní je
    # chyba už při startu, ne tiše otevřená díra.
    oblast: str = field(kw_only=True)
    # Má karta tlačítko „zobrazit celou tabulku"? Pak přehled potřebuje
    # společné okno okno_tabulka() (viz _macros.html), jako stránka Zjištění.
    okno_tabulka: bool = field(default=False, kw_only=True)


SEZNAM: tuple[Sekce, ...] = (
    Sekce("prave_se_hraje", "Právě se hraje",
          "Kdo se zrovna dívá a na čem. Stejná karta jako na Přehledu.",
          "_now_playing.html", lambda o: {"active": stats.active_sessions()},
          obdobi=False,
          oblast="prave_hraje"),
    Sekce("nedavno_pridane", "Nedávno přidané",
          "Pás plakátů toho, co do knihovny přibylo naposledy.",
          "_recently_added.html", lambda o: {"recent": stats.recently_added()},
          obdobi=False,
          oblast="knihovna"),
    Sekce("souhrn", "Celkem odsledováno",
          "Hlavní číslo období, změna proti minulému a křivka vedle něj.",
          "_sekce_souhrn.html",
          lambda o: dict(_jednou(souhrn_obdobi, o),
                         daily=_jednou(stats.daily_activity_split, o)),
          oblast="prehled"),
    Sekce("dlazdice", "Spuštění, uživatelé, tituly, transcode",
          "Řada dlaždic s hlavními čísly období.",
          "_sekce_dlazdice.html", lambda o: _jednou(souhrn_obdobi, o),
          oblast="prehled"),
    Sekce("sledovanost_po_dnech", "Sledovanost po dnech",
          "Křivka filmů a seriálů den po dni, s tabulkou pod ní.",
          "_daily_card.html",
          lambda o: {"daily": _jednou(stats.daily_activity_split, o),
                     "ostatni": stats.rozpad_ostatnich(o)},
          oblast="prehled"),
    Sekce("nejsledovanejsi", "Nejsledovanější tituly",
          "Žebříček filmů a seriálů za zvolené období.",
          "_top_items.html",
          lambda o: {"top_items": stats.top_items(o, kind="both"),
                     "top_kind": "both"}, sirka="pul", obal_karta=True,
          oblast="prehled"),
    Sekce("nejaktivnejsi_uzivatele", "Nejaktivnější uživatelé",
          "Kdo za zvolené období odsledoval nejvíc hodin.",
          "_sekce_uzivatele.html", lambda o: {"top_users": stats.top_users(o)},
          sirka="pul",
          oblast="prehled"),
    Sekce("doruceni", "Jak server obsah doručuje",
          "Přímé přehrávání, remux, transcode a co to stojí server.",
          "_sekce_doruceni.html",
          lambda o: {"methods": stats.play_method_breakdown(o)}, sirka="pul",
          oblast="prehled"),
    Sekce("prehravace", "Přehrávače",
          "Z čeho se lidé dívají: aplikace, prohlížeč, televize.",
          "_sekce_prehravace.html",
          lambda o: {"clients": stats.client_breakdown(o)}, sirka="pul",
          oblast="prehled"),
    Sekce("kdy_se_sleduje", "Kdy se sleduje",
          "Mřížka dnů a hodin: kdy je server nejvytíženější.",
          "_sekce_heatmapa.html",
          lambda o: {"heatmap": stats.hourly_heatmap(o)},
          oblast="prehled"),
    Sekce("zivy_tok", "Právě teče",
          "Souběžný tok v čase, obnovuje se sám.",
          "_sit_zive.html",
          lambda o: {"ted": stats.tok_ted(), "zive": stats.bandwidth_zive(o)},
          oblast="sit"),
    Sekce("spicky_po_dnech", "Špička po dnech",
          "Nejvyšší souběžný tok každého dne.",
          "_sekce_spicky.html",
          lambda o: {"denni_spicky": stats.bandwidth_denni_spicky(o)},
          oblast="sit"),
    Sekce("kdo_streamoval", "Kdo nejvíc streamoval",
          "Přenesená data podle uživatele.",
          "_sekce_kdo_streamoval.html",
          lambda o: {"podle_uzivatele": stats.bandwidth_podle(o, "user_name")},
          sirka="pul",
          oblast="sit"),
    Sekce("podle_prehravace", "Přenos podle přehrávače",
          "Kolik dat proteklo přes kterou aplikaci.",
          "_sekce_podle_prehravace.html",
          lambda o: {"podle_klienta": stats.bandwidth_podle(o, "client")},
          sirka="pul",
          oblast="sit"),
    Sekce("odkud", "Odkud se dívají",
          "Adresy, ze kterých se streamuje: domácí síť, nebo zvenku.",
          "_sekce_odkud.html",
          lambda o: {"odkud": stats.odkud_se_divaji(o)},
          oblast="sit"),
    Sekce("pomer_jazyku", "Poměr jazyků",
          "V jakém jazyce se na serveru sleduje.",
          "_sekce_pomer_jazyku.html",
          lambda o: {"watched": _jednou(_sledovane_jazyky, o)}, sirka="pul",
          oblast="jazyky"),
    Sekce("dabing", "Dabing, nebo originál?",
          "Kolik se sleduje v dabingu a kolik v původním znění.",
          "_sekce_dabing.html",
          lambda o: {"dubbing": langstats.dubbed_vs_original(
              o, _jednou(langstats.preferred_language))}, sirka="pul",
          oblast="jazyky"),
    Sekce("titulky", "Titulky",
          "Jak často a v jakém jazyce se zapínají titulky.",
          "_sekce_titulky.html",
          lambda o: {"subtitles": langstats.subtitle_usage(o)}, sirka="pul",
          oblast="jazyky"),
    Sekce("jazyky_knihovny", "Jazyky v knihovně",
          "Co je v knihovně k dispozici, nezávisle na tom, co se sleduje.",
          "_sekce_jazyky_knihovny.html",
          lambda o: {"library": langstats.library_languages(_barvy())},
          obdobi=False, sirka="pul",
          oblast="jazyky"),
    Sekce("knihovna_celkem", "Celkem za všechny knihovny",
          "Položky, velikost a odkud jsou technická data. Stav, ne období.",
          "_sekce_knihovna_celkem.html",
          # Technická data jako na stránce Knihovna - `langstats.coverage`
          # je pokrytí jazyků pro stránku Jazyky, jen pod stejným jménem.
          lambda o: {"coverage": stats.tech_coverage()}, obdobi=False,
          oblast="knihovna"),
    Sekce("rust_knihovny", "Růst knihovny",
          "Velikost knihovny den po dni a kam to spěje.",
          "_sekce_rust.html", lambda o: data_rustu(o),
          oblast="knihovna"),
    Sekce("kodeky", "Kodeky",
          "Čím je knihovna zakódovaná. Období neřeší, je to stav knihovny.",
          "_sekce_kodeky.html", lambda o: {"codecs": stats.codec_breakdown()},
          obdobi=False, sirka="pul",
          oblast="knihovna"),
    Sekce("rozliseni", "Rozlišení",
          "Kolik je v knihovně 4K, kolik Full HD a kolik zbytku.",
          "_sekce_rozliseni.html",
          lambda o: {"resolutions": stats.resolution_breakdown()},
          obdobi=False, sirka="pul",
          oblast="knihovna"),
    Sekce("dynamicky_rozsah", "Dynamický rozsah",
          "SDR, HDR a Dolby Vision v knihovně.",
          "_sekce_rozsah.html", lambda o: {"ranges": stats.video_range_breakdown()},
          obdobi=False, sirka="pul",
          oblast="knihovna"),

    # Zjištění. Data z týchž funkcí a se stejným počtem řádků jako stránka
    # Zjištění (insights.RADKU_ZJISTENI); kolik se jich ukáže rovnou, řídí
    # Nastavení → Rozhraní → Dlouhé seznamy, zbytek je v okně.
    Sekce("zj_nevyuzite", "Místo, které nikdo nevyužívá",
          "Soubory, které za období nikdo nepustil, a kolik místa zabírají.",
          "_sekce_zj_nevyuzite.html",
          lambda o: {"dead": insights.dead_storage(insights.RADKU_ZJISTENI, days=o)},
          okno_tabulka=True, oblast="zebricky"),
    Sekce("zj_transcodovane", "Nejčastěji transcodované soubory",
          "Co server přepočítává nejčastěji, i s kodekem a rozlišením.",
          "_sekce_zj_transcodovane.html",
          lambda o: {"transcodes": insights.transcode_offenders(o, insights.RADKU_ZJISTENI)},
          sirka="pul", okno_tabulka=True, oblast="zebricky"),
    Sekce("zj_duvody", "Proč se transcoduje",
          "Z jakých důvodů server přepočítává: kodek, bitrate, titulky.",
          "_sekce_zj_duvody.html",
          lambda o: {"reasons": insights.transcode_reasons(o)},
          sirka="pul", oblast="zebricky"),
    Sekce("zj_rychlost", "Rychlost převodu",
          "Stíhá server převádět? Průměr, nejpomalejší a nejrychlejší převody.",
          "_sekce_zj_rychlost.html",
          lambda o: {"rychlost": insights.rychlost_prevodu(o)},
          oblast="zebricky"),
    Sekce("zj_kvalita", "Díváš se na to hodně, ale ve slabé kvalitě",
          "Často sledované tituly v nízkém rozlišení: kandidáti na lepší verzi.",
          "_sekce_zj_kvalita.html",
          lambda o: {"upgrades": insights.upgrade_candidates(o, insights.RADKU_ZJISTENI)},
          sirka="pul", okno_tabulka=True, oblast="zebricky"),
    Sekce("zj_velke", "Zabírá hodně, sledováno skoro vůbec",
          "Velké soubory, na které se skoro nikdo nedívá.",
          "_sekce_zj_velke.html",
          lambda o: {"oversized": insights.oversized_rarely_watched(
              o, insights.RADKU_ZJISTENI)},
          sirka="pul", okno_tabulka=True, oblast="zebricky"),
    Sekce("zj_knihovny", "Využití knihoven",
          "Kolik místa která knihovna zabírá proti tomu, kolik se z ní sleduje.",
          "_sekce_zj_knihovny.html",
          lambda o: {"efficiency": insights.storage_efficiency(o)},
          oblast="zebricky"),
    Sekce("zj_duplicity", "Možné duplicity",
          "Tituly, které jsou v knihovně nejspíš víckrát. Stav, ne období.",
          "_sekce_zj_duplicity.html",
          lambda o: {"duplicates": insights.duplicate_candidates(insights.RADKU_ZJISTENI)},
          obdobi=False, sirka="pul", okno_tabulka=True, oblast="zebricky"),
    Sekce("zj_odlozene", "Začaté a odložené",
          "Co lidé začali sledovat a nedokoukali.",
          "_sekce_zj_odlozene.html",
          lambda o: {"abandoned": insights.never_finished(o, insights.RADKU_ZJISTENI)},
          sirka="pul", okno_tabulka=True, oblast="zebricky"),

    # Jazyky - doplněk karet výš (poměr, dabing, titulky, jazyky knihovny).
    Sekce("kdo_jazyk", "Kdo v jakém jazyce sleduje",
          "Jazyk zvuku a titulků po jednotlivých divácích.",
          "_sekce_kdo_jazyk.html",
          # `watched` kvůli legendě - totéž, co bere karta Poměr jazyků.
          lambda o: {"by_user": langstats.languages_by_user(o, _barvy()),
                     "watched": _jednou(_sledovane_jazyky, o)},
          oblast="jazyky"),
    Sekce("kombinace", "Kombinace stop",
          "Které kombinace zvuku a titulků v knihovně jsou. Stav, ne období.",
          "_sekce_kombinace.html",
          lambda o: {"combinations": langstats.language_combinations(),
                     "all_combinations": langstats.vsechny_kombinace(),
                     "undefined_items": langstats.undefined_language_items()},
          obdobi=False, sirka="pul", oblast="jazyky"),
    Sekce("chybi_stopa", "Sledujete to, ale stopa chybí",
          "Sledované tituly, kterým chybí zvuková stopa v hlavním jazyce.",
          "_sekce_chybi_stopa.html",
          lambda o: {"missing_preferred": langstats.missing_preferred(
              o, _jednou(langstats.preferred_language))},
          oblast="jazyky"),

    # Uživatelé. Obě karty berou tutéž tabulku jako stránka Uživatelé.
    Sekce("uzivatele_graf", "Sledovanost podle uživatele",
          "Kolik hodin kdo za období odsledoval, jako graf.",
          "_sekce_uzivatele_graf.html",
          lambda o: {"rows": _jednou(stats.user_table, o)}, oblast="uzivatele"),
    Sekce("uzivatele_tabulka", "Uživatelé",
          "Všichni uživatelé: hodiny, spuštění, tituly, přepočty, naposledy.",
          "_sekce_uzivatele_tabulka.html",
          lambda o: {"rows": _jednou(stats.user_table, o)}, oblast="uzivatele"),

    # Knihovna a Síť.
    Sekce("knihovny", "Knihovny",
          "Dlaždice knihoven: počet položek a velikost, s odkazem na detail.",
          "_sekce_knihovny.html", lambda o: {"libraries": stats.library_cards()},
          obdobi=False, oblast="knihovna"),
    Sekce("sit_cisla", "Čísla sítě",
          "Špička, přeneseno celkem, podíl překódovaných a průměrný tok.",
          "_sekce_sit_cisla.html",
          lambda o: {"sit_prehled": stats.bandwidth_prehled(o)}, oblast="sit"),
    Sekce("mapa", "Mapa",
          "Odkud se dívá z veřejných adres: na mapě a po zemích.",
          "_sekce_mapa.html",
          lambda o: _data_mapy(o),
          oblast="sit"),
)

PODLE_KLICE: dict[str, Sekce] = {s.klic: s for s in SEZNAM}

# Šířky panelu. Mřížka má šest sloupců, aby vyšly třetiny i půlky;
# značka je to, co stojí na přepínači šířky v okně s rozvržením.
SIRKY: dict[str, str] = {"tretina": "⅓", "pul": "½", "cela": "1"}

ZAPNUTO = "ui_dashboard"


def _podminka_uctu(account_id: int | None) -> tuple[str, list[Any]]:
    """Podminka na vlastnika rozvrzeni - spolecne (NULL), nebo konkretni ucet.

    Nedá se napsat `account_id IS ?`. SQLite to bere jako porovnani, ktere
    zvlada i NULL, ale PostgreSQL zna jen `IS NULL` - a `IS %s` je pro nej
    syntakticka chyba. Protoze rozvrzeni cte kazdy pozadavek (kvuli
    zalozce v menu), shodilo to na PostgreSQL celou aplikaci, ne jen tuhle
    stranku.
    """
    if account_id is None:
        return "account_id IS NULL", []
    return "account_id = ?", [account_id]


def smi(karta: Sekce, account: dict[str, Any] | None) -> bool:
    """Smí účet vidět tuhle kartu? Podle oblasti, jako stránku, odkud je.

    Správce vidí všechno (pristup.vidi to ví sám). Ostatní podle stromu
    práv - vlastního, nebo své skupiny. Bez toho by přehled byl zadní
    vrátka: kdo smí na přehled, viděl by v něm i kartu ze stránky, kam
    nesmí (třeba IP adresy ze Sítě).
    """
    return pristup.vidi(pristup.role(account), karta.oblast, account)


def povolene(rozvrzeni: list[Sekce], account: dict[str, Any] | None) -> list[Sekce]:
    """Jen karty, které účet smí - ve stejném pořadí."""
    return [karta for karta in rozvrzeni if smi(karta, account)]


def je_zapnuty() -> bool:
    """Je vlastní přehled zapnutý v nastavení?"""
    return (db.get_setting(ZAPNUTO, "0") or "0").strip() == "1"


def vidi_prehled(account: dict[str, Any] | None) -> bool:
    """Má účet v přehledu pro všechny co vidět?

    Zapnutý, smí na něj (oblast přehledu) a podle svých práv v něm vidí
    aspoň jednu kartu. Jedno místo pro menu, přistání po přihlášení
    i samotnou stránku: tři kopie téže podmínky se dvakrát rozešly.
    Prázdná stránka „ještě v něm nic není" by lhala, „sem nemáš přístup"
    hned po přihlášení je nejhorší možné uvítání.
    """
    return (je_zapnuty()
            and pristup.smi_cestu(pristup.role(account), "/dashboard", account)
            and bool(povolene(nacti_rozvrzeni(), account)))


# Název přehledu pro všechny. Zadává ho správce v Nastavení → Rozhraní a
# ukáže se v menu i jako nadpis stránky - „Rodina", „Kino", podle toho,
# k čemu ji poskládal. Nepřekládá se: je to jeho text, ne věta aplikace.
NAZEV_KLIC = "dashboard_nazev"
NAZEV_MAX = 40


class NeplatnyNazev(ValueError):
    """Název, který nejde použít. `veta` je česká věta = klíč překladu,
    `hodnoty` se do ní dosadí (viz web_zaklad._flash)."""

    def __init__(self, veta: str, **hodnoty: Any) -> None:
        super().__init__(veta)
        self.veta = veta
        self.hodnoty = hodnoty


def nazev() -> str:
    """Uložený název přehledu pro všechny, nebo prázdno."""
    return (db.get_setting(NAZEV_KLIC, "") or "").strip()


def over_nazev(text: str) -> str:
    """Vyčištěný název, nebo NeplatnyNazev.

    Mezery na okrajích pryč, víc mezer za sebou jako jedna. Délka v
    znacích (ne bajtech), aby se vešel do menu. A nesmí se plést se
    stránkami, které v menu už jsou - „Přehled" a „Můj přehled" v žádném
    jazyce rozhraní: kdo přepne jazyk, viděl by dvě stejné položky.
    """
    cisty = " ".join((text or "").split())
    if not cisty:
        raise NeplatnyNazev(
            "Vlastní přehled potřebuje název stránky. Ukáže se v menu i jako nadpis.")
    if len(cisty) > NAZEV_MAX:
        raise NeplatnyNazev("Název stránky může mít nejvýš {max} znaků.", max=NAZEV_MAX)
    obsazene = {i18n.translate(veta, jazyk).casefold()
                for veta in ("Přehled", "Můj přehled") for jazyk in i18n.LANGUAGES}
    if cisty.casefold() in obsazene:
        raise NeplatnyNazev("Název „{nazev}“ už má jiná stránka v menu. Vyber jiný.",
                            nazev=cisty)
    return cisty


def doplnit_nazev() -> bool:
    """Stávající instalace: zapnutý přehled bez názvu dostane „Vlastní přehled".

    Tak se mu říkalo do teď, takže se v menu nic nezmění. Volá se při
    startu; podruhé nic nedělá a název, který si správce dal, nepřepíše.
    Vypnutý přehled zůstane bez názvu - dostane ho, až ho někdo zapne.
    """
    if not je_zapnuty() or nazev():
        return False
    db.set_setting(NAZEV_KLIC, i18n.translate("Vlastní přehled", i18n.jazyk_serveru()))
    return True


def nacti_rozvrzeni(account_id: int | None = None) -> list[Sekce]:
    """Poskládaný přehled. Neznámé klíče se tiše přeskočí.

    Bez `account_id` je to přehled pro všechny (`account_id IS NULL`),
    s ním Můj přehled toho účtu - a jen ten: prázdný Můj přehled je
    prázdný, nepůjčuje si rozvržení přehledu pro všechny. Je to jiná
    stránka s jiným účelem, a kdo si ji ještě nesložil, má vidět výzvu,
    ne cizí výběr, který by si pak omylem uložil jako svůj.
    """
    kde, hodnoty = _podminka_uctu(account_id)
    radky = db.query_all(
        f"SELECT sekce, sirka FROM dashboard_layout"
        f" WHERE {kde} ORDER BY poradi", tuple(hodnoty))

    rozvrzeni: list[Sekce] = []
    for radek in radky:
        sekce = PODLE_KLICE.get(radek["sekce"])
        if sekce is None:
            continue
        # Uložená šířka přebíjí tu z registru; neznámou ignorujeme,
        # ať se stránka nerozbije kvůli hodnotě z jiné verze.
        sirka = (radek.get("sirka") or "").strip()
        rozvrzeni.append(replace(sekce, sirka=sirka)
                         if sirka in SIRKY else sekce)
    return rozvrzeni


def rozdel_zadani(zadani: str) -> list[tuple[str, str]]:
    """Rozebere zápis z formuláře: "klic:sirka,klic:sirka".

    Šířka je nepovinná - bez ní platí ta z registru.
    """
    polozky: list[tuple[str, str]] = []
    for kus in (zadani or "").split(","):
        kus = kus.strip()
        if not kus:
            continue
        klic, _, sirka = kus.partition(":")
        polozky.append((klic.strip(), sirka.strip()))
    return polozky


def uloz_rozvrzeni(polozky: Any, account_id: int | None = None) -> list[str]:
    """Uloží pořadí sekcí i jejich šířky. Vrátí to, co se opravdu uložilo.

    Bere seznam klíčů, dvojic (klíč, šířka), nebo rovnou zápis
    z formuláře. Neznámé klíče, neznámé šířky a duplicity se zahodí -
    do databáze patří jen to, co jde vykreslit. Jinak by stačilo sekci
    z registru odebrat a stránka by spadla na něčem, co si tam někdo dal
    před půl rokem.
    """
    if isinstance(polozky, str):
        polozky = rozdel_zadani(polozky)

    ulozene: list[tuple[str, str]] = []
    videne: set[str] = set()
    for polozka in polozky:
        klic, sirka = polozka if isinstance(polozka, tuple) else (polozka, "")
        if klic in PODLE_KLICE and klic not in videne:
            videne.add(klic)
            ulozene.append((klic, sirka if sirka in SIRKY else ""))

    kde, hodnoty = _podminka_uctu(account_id)
    with db.connect() as conn:
        conn.execute(f"DELETE FROM dashboard_layout WHERE {kde}", tuple(hodnoty))
        conn.executemany(
            "INSERT INTO dashboard_layout (account_id, sekce, poradi, sirka)"
            " VALUES (?, ?, ?, ?)",
            [(account_id, klic, poradi, sirka or None)
             for poradi, (klic, sirka) in enumerate(ulozene)])
        conn.commit()
    return [klic for klic, _ in ulozene]


def data_pro(rozvrzeni: list[Sekce], obdobi: Any) -> dict[str, Any]:
    """Data všech poskládaných sekcí v jednom slovníku.

    Klíče se napříč sekcemi neopakují, takže se dají přisypat do kontextu
    šablony rovnou - a šablonky pak fungují stejně tady jako na svých
    původních stránkách. Co potřebuje víc karet, se spočítá jednou
    (`_jednou`).
    """
    token = _pamet.set({})
    try:
        kontext: dict[str, Any] = {}
        for sekce in rozvrzeni:
            kontext.update(sekce.data(obdobi))
        return kontext
    finally:
        _pamet.reset(token)


def data_oblasti(oblast: str, obdobi: Any) -> dict[str, Any]:
    """Data všech karet jedné oblasti - pro stránku, ze které karty jsou.

    Stránka (Zjištění, Síť) si je nebere zvlášť: dvě kopie téhož výpočtu
    se rozešly a karta pak v přehledu ukazovala jiná čísla než na stránce.
    """
    return data_pro([karta for karta in SEZNAM if karta.oblast == oblast], obdobi)
