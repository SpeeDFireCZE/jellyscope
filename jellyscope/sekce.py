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

from dataclasses import dataclass, replace
from typing import Any, Callable

from . import db, formatting, langstats, stats
from .i18n import translate as _t


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
        poznamka = (_t("nemáme data za předchozí období – historie začíná {datum}")
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


SEZNAM: tuple[Sekce, ...] = (
    Sekce("prave_se_hraje", "Právě se hraje",
          "Kdo se zrovna dívá a na čem - stejná karta jako na Přehledu.",
          "_now_playing.html", lambda o: {"active": stats.active_sessions()},
          obdobi=False),
    Sekce("nedavno_pridane", "Nedávno přidané",
          "Pás plakátů toho, co do knihovny přibylo naposledy.",
          "_recently_added.html", lambda o: {"recent": stats.recently_added()},
          obdobi=False),
    Sekce("souhrn", "Celkem odsledováno",
          "Hlavní číslo období, změna proti minulému a křivka vedle něj.",
          "_sekce_souhrn.html",
          lambda o: dict(souhrn_obdobi(o), daily=stats.daily_activity_split(o))),
    Sekce("dlazdice", "Spuštění, uživatelé, tituly, transcode",
          "Řada dlaždic s hlavními čísly období.",
          "_sekce_dlazdice.html", lambda o: souhrn_obdobi(o)),
    Sekce("sledovanost_po_dnech", "Sledovanost po dnech",
          "Křivka filmů a seriálů den po dni, s tabulkou pod ní.",
          "_daily_card.html",
          lambda o: {"daily": stats.daily_activity_split(o),
                     "ostatni": stats.rozpad_ostatnich(o)}),
    Sekce("nejsledovanejsi", "Nejsledovanější tituly",
          "Žebříček filmů a seriálů za zvolené období.",
          "_top_items.html",
          lambda o: {"top_items": stats.top_items(o, kind="both"),
                     "top_kind": "both"}, sirka="pul", obal_karta=True),
    Sekce("nejaktivnejsi_uzivatele", "Nejaktivnější uživatelé",
          "Kdo za zvolené období odsledoval nejvíc hodin.",
          "_sekce_uzivatele.html", lambda o: {"top_users": stats.top_users(o)},
          sirka="pul"),
    Sekce("doruceni", "Jak server obsah doručuje",
          "Přímé přehrávání, remux, transcode - a co to stojí server.",
          "_sekce_doruceni.html",
          lambda o: {"methods": stats.play_method_breakdown(o)}, sirka="pul"),
    Sekce("prehravace", "Přehrávače",
          "Z čeho se lidé dívají - aplikace, prohlížeč, televize.",
          "_sekce_prehravace.html",
          lambda o: {"clients": stats.client_breakdown(o)}, sirka="pul"),
    Sekce("kdy_se_sleduje", "Kdy se sleduje",
          "Mřížka dnů a hodin - kdy je server nejvytíženější.",
          "_sekce_heatmapa.html",
          lambda o: {"heatmap": stats.hourly_heatmap(o)}),
    Sekce("zivy_tok", "Právě teče",
          "Souběžný tok v čase, obnovuje se sám.",
          "_sit_zive.html",
          lambda o: {"ted": stats.tok_ted(), "zive": stats.bandwidth_zive(o)}),
    Sekce("spicky_po_dnech", "Špička po dnech",
          "Nejvyšší souběžný tok každého dne.",
          "_sekce_spicky.html",
          lambda o: {"denni_spicky": stats.bandwidth_denni_spicky(o)}),
    Sekce("kdo_streamoval", "Kdo nejvíc streamoval",
          "Přenesená data podle uživatele.",
          "_sekce_kdo_streamoval.html",
          lambda o: {"podle_uzivatele": stats.bandwidth_podle(o, "user_name")},
          sirka="pul"),
    Sekce("podle_prehravace", "Přenos podle přehrávače",
          "Kolik dat proteklo přes kterou aplikaci.",
          "_sekce_podle_prehravace.html",
          lambda o: {"podle_klienta": stats.bandwidth_podle(o, "client")},
          sirka="pul"),
    Sekce("odkud", "Odkud se dívají",
          "Adresy, ze kterých se streamuje - domácí síť, nebo zvenku.",
          "_sekce_odkud.html",
          lambda o: {"odkud": stats.odkud_se_divaji(o)}),
    Sekce("pomer_jazyku", "Poměr jazyků",
          "V jakém jazyce se na serveru sleduje.",
          "_sekce_pomer_jazyku.html",
          lambda o: {"watched": langstats.watched_languages(
              o, langstats.colour_map())}, sirka="pul"),
    Sekce("dabing", "Dabing, nebo originál?",
          "Kolik se sleduje v dabingu a kolik v původním znění.",
          "_sekce_dabing.html",
          lambda o: {"dubbing": langstats.dubbed_vs_original(
              o, langstats.preferred_language())}, sirka="pul"),
    Sekce("titulky", "Titulky",
          "Jak často a v jakém jazyce se zapínají titulky.",
          "_sekce_titulky.html",
          lambda o: {"subtitles": langstats.subtitle_usage(o)}, sirka="pul"),
    Sekce("jazyky_knihovny", "Jazyky v knihovně",
          "Co je v knihovně k dispozici - nezávisle na tom, co se sleduje.",
          "_sekce_jazyky_knihovny.html",
          lambda o: {"library": langstats.library_languages(
              langstats.colour_map())}, obdobi=False, sirka="pul"),
    Sekce("knihovna_celkem", "Celkem za všechny knihovny",
          "Položky, velikost a odkud jsou technická data. Stav, ne období.",
          "_sekce_knihovna_celkem.html",
          lambda o: {"coverage": langstats.coverage()}, obdobi=False),
    Sekce("kodeky", "Kodeky",
          "Čím je knihovna zakódovaná. Období neřeší - je to stav knihovny.",
          "_sekce_kodeky.html", lambda o: {"codecs": stats.codec_breakdown()},
          obdobi=False, sirka="pul"),
    Sekce("rozliseni", "Rozlišení",
          "Kolik je v knihovně 4K, kolik Full HD a kolik zbytku.",
          "_sekce_rozliseni.html",
          lambda o: {"resolutions": stats.resolution_breakdown()},
          obdobi=False, sirka="pul"),
    Sekce("dynamicky_rozsah", "Dynamický rozsah",
          "SDR, HDR a Dolby Vision v knihovně.",
          "_sekce_rozsah.html", lambda o: {"ranges": stats.video_range_breakdown()},
          obdobi=False, sirka="pul"),
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


def je_zapnuty() -> bool:
    """Je vlastní přehled zapnutý v nastavení?"""
    return (db.get_setting(ZAPNUTO, "0") or "0").strip() == "1"


def nacti_rozvrzeni(account_id: int | None = None) -> list[Sekce]:
    """Poskládaný přehled. Neznámé klíče se tiše přeskočí.

    `account_id` je připravené na později. Dnes se ukládá jen společné
    rozvržení (`account_id IS NULL`), protože **žádné nastavení v aplikaci
    zatím není uživatelské** - všechno je serverové. Až tahle část vznikne,
    přibudou řádky s konkrétním účtem a pravidlo bude znít "má-li člověk
    vlastní, platí ony; jinak společné". Žádná migrace dat.
    """
    kde, hodnoty = _podminka_uctu(account_id)
    radky = db.query_all(
        f"SELECT sekce, sirka FROM dashboard_layout"
        f" WHERE {kde} ORDER BY poradi", tuple(hodnoty))
    if not radky and account_id is not None:
        radky = db.query_all(
            "SELECT sekce, sirka FROM dashboard_layout"
            " WHERE account_id IS NULL ORDER BY poradi")

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
    původních stránkách.
    """
    kontext: dict[str, Any] = {}
    for sekce in rozvrzeni:
        kontext.update(sekce.data(obdobi))
    return kontext
