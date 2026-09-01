"""Statistiky - vsechny otazky, ktere klademe databazi.

Kazda funkce tady dela jednu vec: polozi jeden SQL dotaz a vrati vysledek
jako obycejny seznam slovniku. Zadna funkce nic nekresli a nic nezapisuje.

Tohle rozdeleni ("odkud data" / "co s nimi" / "jak vypadaji") je ta nejlepsi
navykovka, kterou si z projektu muzes odnest. Kdyz se pak nekde objevi
spatne cislo, vis presne, ve kterem ze tri souboru hledat.

Poznamka k casu: cas ukladame v UTC, ale pro cloveka ho prevadime na mistni
cas modifikatorem 'localtime'. "Nejaktivnejsi hodina" musi odpovidat hodinam
na hodinach na zdi, ne v Greenwichi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, formatting
from .i18n import translate as _t

# Prevod vysky obrazu na skupinu rozliseni. Pouziva se na vic mistech,
# takze je to konstanta, ne kopie na peti radcich.
RESOLUTION_CASE = """
    CASE
        WHEN height IS NULL AND width IS NULL      THEN 'nezname'
        WHEN COALESCE(width, 0)  >= 3400
          OR COALESCE(height, 0) >= 2000           THEN '4K'
        WHEN COALESCE(width, 0)  >= 1800
          OR COALESCE(height, 0) >= 1000           THEN '1080p'
        WHEN COALESCE(width, 0)  >= 1200
          OR COALESCE(height, 0) >= 700            THEN '720p'
        WHEN COALESCE(width, 0)  >= 900
          OR COALESCE(height, 0) >= 500            THEN '576p'
        ELSE 'SD'
    END
"""

# Poradi, v jakem se skupiny rozliseni maji zobrazovat (od nejvyssiho).
RESOLUTION_ORDER = ["4K", "1080p", "720p", "576p", "SD", "nezname"]


@dataclass(frozen=True)
class Obdobi:
    """Okno, za ktere se statistiky pocitaji.

    Drive se predaval jen pocet dnu a kazdy dotaz si dosadil
    `datetime('now', '-30 days')`. Okno tim vzdycky koncilo ted, takze
    "cely loňsky prosinec" nesel vyjadrit vubec. Proto dvojice mezi.

    `od` je vcetne, `do` vylucne - dotaz je pak `>= od AND < do`
    a pulnoc nepatri do obou dnu zaroven.
    """

    od: str
    do: str
    dny: int
    # "Poslednich N dni" (relativni), nebo pevne rozmezi od-do?
    # Rozhoduje to o dvou vecech: jak se obdobi pojmenuje v prepinaci
    # a od ktereho dne zacina kalendar v grafu po dnech.
    relativni: bool = True
    # Tytez meze, ale v ZONE APLIKACE.
    #
    # `od`/`do` jsou v UTC, protoze v UTC je ulozena historie. Do formulare
    # a do popisku ale patri to, co clovek napsal - kdyz si vybere
    # 20. srpna, ma tam stat 20. srpna, ne 19. srpna 22:00.
    #
    # Obe dvojice se chovaji stejne: dolni mez VCETNE, horni VYLUCNE.
    # Zadne "nekdy vcetne, nekdy ne" - z toho vznikaji chyby o jeden den,
    # ktere se hledaji tezko, protoze pul roku (v zime) nejsou videt.
    od_mistni: str = ""
    do_mistni: str = ""
    # Zadal clovek cele dny, nebo presny cas? Rozhoduje to jen o tom, jak
    # se obdobi napise: "20.8.2026" u celeho dne, "20.8.2026 21:30" u useku
    # vybraneho tazenim v grafu.
    cely_den: bool = True

    @property
    def vlastni(self) -> bool:
        """Vybral si obdobi clovek sam?"""
        return not self.relativni


# Horni mez "posledních N dní". Schvalne daleka budoucnost, ne "ted":
# cas se uklada zaokrouhleny na vteriny, takze prehravani zapsane v teze
# vterine, ve ktere se ptame, by se do okna uz nevesio - a prave to je
# ten zaznam, ktery clovek prave ted hleda. Vlastni obdobi si horni mez
# urcuje samo.
KONEC_CASU = "9999-12-31 23:59:59"


def obdobi_dnu(days: int) -> Obdobi:
    """Poslednich N dni. Konec je otevreny - viz KONEC_CASU."""
    dny = max(1, int(days))
    zacatek = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dny)
    return Obdobi(od=zacatek.strftime(db.TIME_FORMAT), do=KONEC_CASU, dny=dny)


def _mistni_cas(text: Any) -> tuple[datetime, bool] | None:
    """Text z adresy na cas v zone aplikace. Vraci (cas, je_to_datum).

    Bereme dva tvary: "2026-08-20" (cely den) a "2026-08-20 21:30"
    (presny okamzik). Ten druhy pouziva vyber rozsahu tazenim v grafu -
    tam by zaokrouhleni na cely den zahodilo prave to, co clovek vybral.
    """
    surovy = str(text or "").strip().replace("T", " ")
    for tvar, je_datum in (("%Y-%m-%d %H:%M:%S", False),
                           ("%Y-%m-%d %H:%M", False),
                           ("%Y-%m-%d", True)):
        try:
            return datetime.strptime(surovy, tvar), je_datum
        except ValueError:
            continue
    return None


def obdobi_od_do(od: Any, do: Any) -> Obdobi | None:
    """Obdobi ze dvou mezi. None, kdyz nedavaji smysl.

    Meze prichazeji v ZONE APLIKACE - tak je clovek napsal a tak je vidi
    v grafu. Historie je ale ulozena v UTC, takze se prevadeji; bez toho
    "20. srpna" ve stredni Evrope v lete znamenalo 20. srpna od dvou rano
    do dvou rano nasledujiciho dne.

    U celeho dne je `do` posledni den, ktery clovek chce videt - do dotazu
    proto jde pulnoc dne nasledujiciho, jinak by z posledniho dne vypadlo
    vsechno po 00:00. U presneho casu se bere tak, jak prisel.
    """
    zacatek = _mistni_cas(od)
    konec = _mistni_cas(do)
    if zacatek is None or konec is None:
        return None

    zacatek_cas, _ = zacatek
    konec_cas, konec_je_datum = konec
    if konec_je_datum:
        konec_cas += timedelta(days=1)
    if konec_cas <= zacatek_cas:
        return None

    zona = formatting.zona()
    def _utc(cas: datetime) -> str:
        return cas.replace(tzinfo=zona).astimezone(timezone.utc).strftime(db.TIME_FORMAT)

    return Obdobi(od=_utc(zacatek_cas),
                  do=_utc(konec_cas),
                  dny=max(1, round((konec_cas - zacatek_cas).total_seconds() / 86400)),
                  relativni=False,
                  od_mistni=zacatek_cas.strftime(db.TIME_FORMAT),
                  do_mistni=konec_cas.strftime(db.TIME_FORMAT),
                  cely_den=zacatek[1] and konec_je_datum)


def obdobi_z_okamziku(od: Any, do: Any) -> Obdobi | None:
    """Obdobi ze dvou okamziku v sekundach od epochy.

    Takhle chodi vyber tazenim v grafu. Prohlizec posila cisla, ne text:
    kdyby prevadel sam, pouzil by zonu POCITACE, kdezto aplikace ma svou
    (Nastaveni -> Obecne). Prevod proto dela server.
    """
    try:
        zacatek = float(od)
        konec = float(do)
    except (TypeError, ValueError):
        return None
    if not (konec > zacatek):
        return None

    zona = formatting.zona()
    tvar = "%Y-%m-%d %H:%M"
    return obdobi_od_do(
        datetime.fromtimestamp(zacatek, zona).strftime(tvar),
        datetime.fromtimestamp(konec, zona).strftime(tvar),
    )


def _obdobi(zadani: Any) -> Obdobi:
    """Prijme cislo i hotove Obdobi. Diky tomu se nemusely menit signatury."""
    return zadani if isinstance(zadani, Obdobi) else obdobi_dnu(int(zadani or 30))


def _meze(zadani: Any) -> tuple[str, str]:
    """Dvojice pro dotaz: (od, do). Do parametru se rozbaluje hvezdickou."""
    obdobi = _obdobi(zadani)
    return obdobi.od, obdobi.do


def predchozi(zadani: Any) -> Obdobi:
    """Stejne dlouhe okno tesne pred zvolenym - kvuli srovnani.

    "Stejne dlouhe" se meri z mezi okna, ne z poctu dnu. U celych dnu
    vyjde oboji nastejno, u useku vybraneho tazenim v grafu uz ne:
    dvouhodinovy vyber ma `dny == 1`, takze by se poroval s celym
    predchozim dnem - a kazda sipka "oproti predchozimu obdobi" by
    ukazovala propad, ktery se nestal.
    """
    obdobi = _obdobi(zadani)
    try:
        konec = datetime.strptime(obdobi.od, db.TIME_FORMAT)
        # "Poslednich N dni" ma konec otevreny (KONEC_CASU), takze z mezi
        # se delka merit neda - u toho plati pocet dnu.
        delka = (timedelta(days=obdobi.dny) if obdobi.relativni
                 else datetime.strptime(obdobi.do, db.TIME_FORMAT) - konec)
    except ValueError:
        return obdobi
    zacatek = konec - delka
    # `relativni=False`: predchozi okno ma pevny konec (zacatek toho
    # zvoleneho), takze to uz neni "poslednich N dni".
    zona = formatting.zona()

    def _mistne(cas: datetime) -> str:
        return (cas.replace(tzinfo=timezone.utc).astimezone(zona)
                .strftime(db.TIME_FORMAT))

    return Obdobi(od=zacatek.strftime(db.TIME_FORMAT), do=obdobi.od,
                  dny=obdobi.dny, relativni=False,
                  od_mistni=_mistne(zacatek), do_mistni=_mistne(konec),
                  cely_den=obdobi.cely_den)


def prvni_zaznam() -> str:
    """Odkdy vubec mame historii. Prazdne, kdyz jeste nic nemame.

    Slouzi ke srovnani s predchozim obdobim: kdyz predchozi okno zacina
    driv, nez sahaji nase data, neni to srovnani dvou obdobi, ale obdobi
    s prazdnem. Procenta z toho vyjdou libovolne velka a nic nerikaji.
    """
    return str(db.query_value(
        "SELECT MIN(started_at) FROM playback WHERE watched_seconds > 0",
        default="") or "")


def lze_srovnat(zadani: Any) -> bool:
    """Ma predchozi obdobi vubec data, se kterymi jde srovnavat?"""
    zacatek = prvni_zaznam()
    if not zacatek:
        return False
    return predchozi(zadani).od >= zacatek


# ---------------------------------------------------------------------------
# Prehled (dashboard)
# ---------------------------------------------------------------------------

def overview(days: int) -> dict[str, Any]:
    """Hlavni cisla za zvolene obdobi."""
    row = db.query_one(
        """
        SELECT
            COUNT(*)                                        AS plays,
            COALESCE(SUM(watched_seconds), 0)               AS watched_seconds,
            COUNT(DISTINCT user_id)                         AS users,
            COUNT(DISTINCT item_id)                         AS item_count,
            COALESCE(SUM(CASE WHEN play_method = 'Transcode'
                              THEN watched_seconds ELSE 0 END), 0) AS transcoded_seconds
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        """,
        (*_meze(days),),
    ) or {}

    watched = row.get("watched_seconds") or 0
    transcoded = row.get("transcoded_seconds") or 0
    row["transcode_share"] = (transcoded / watched * 100) if watched else 0.0
    return row


def previous_overview(days: int) -> dict[str, Any]:
    """Stejna cisla za predchozi stejne dlouhe obdobi - kvuli srovnani.

    Bez teto funkce by stat "142 hodin" nerekl nic. Vedle "+18 % oproti
    minulemu obdobi" uz je z toho informace.
    """
    return db.query_one(
        """
        SELECT
            COUNT(*)                          AS plays,
            COALESCE(SUM(watched_seconds), 0) AS watched_seconds,
            COUNT(DISTINCT user_id)           AS users
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        """,
        _meze(predchozi(days)),
    ) or {}


def active_session_count() -> int:
    """Kolik se toho prave hraje. Jen cislo, ne cely seznam.

    Vlastni dotaz schvalne: cislo chce kazda stranka (odznak v postrannim
    menu) a k tomu /health, na ktere se prohlizec pta kazdych deset vterin
    z kazde otevrene karty. Pres `len(active_sessions())` to znamenalo
    pokazde spojit tri tabulky a vytahnout vsechny sloupce jen proto,
    aby se spocetly radky.
    """
    return int(db.query_value(
        "SELECT COUNT(*) FROM playback WHERE is_active = 1", default=0) or 0)


def popis_prepoctu(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Co presne server u tohohle prehravani prepocitava.

    Znacka "transcode" sama o sobe nerika skoro nic. Prepocet obrazu
    vytizi procesor nasobne vic nez prepocet zvuku - a kdyz clovek hleda,
    proc server topi, je to prvni vec, kterou potrebuje videt. Rozdil
    hlasi Jellyfin sam v `TranscodingInfo` (viz collector).

    Vraci seznam faktu, ne hotovou vetu: prekladat se ma v sablone, kde
    je jazyk prihlaseneho cloveka. Kazdy fakt je slovnik:

        {"co": "Obraz", "primo": False, "z": "hevc", "na": "h264"}
        {"co": "Titulky", "vypaluji": True}
        {"co": "Hardware", "text": "qsv"}

    U starsich zaznamu (a u importovane historie) priznak chybi. Tehdy
    se odvodi z kodeku: stejny kodek na obou stranach znamena, ze se
    stopa jen prebaluje. Presne to totiz "primy" znamena - a oba kodeky
    jsou vedle sebe videt, takze si to clovek muze overit.
    """
    if (row.get("play_method") or "") != "Transcode":
        return []

    fakta: list[dict[str, Any]] = []
    for co, primo, zdroj, cil in (
        ("Obraz", row.get("transcode_video_direct"),
         row.get("source_codec"), row.get("video_codec")),
        ("Zvuk", row.get("transcode_audio_direct"),
         row.get("source_audio_codec"), row.get("audio_codec")),
    ):
        if primo is None:
            if not zdroj or not cil:
                continue   # nemame co rict, radeji mlcime
            primo = str(zdroj).lower() == str(cil).lower()
        fakta.append({"co": co, "primo": bool(primo), "z": zdroj, "na": cil})

    # Titulky se do obrazu vypaluji - tedy prepocet obrazu, i kdyz je
    # kodek podporovany. Jellyfin to rekne jedine timhle duvodem.
    duvody = (row.get("transcode_reasons") or "").lower()
    if "subtitle" in duvody:
        fakta.append({"co": "Titulky", "vypaluji": True})

    if row.get("transcode_hw"):
        fakta.append({"co": "Hardware", "text": str(row["transcode_hw"])})

    return fakta


def active_sessions() -> list[dict[str, Any]]:
    """Co se prave ted hraje - vcetne toho, kde v poradi je.

    Postup prehravani dopocitavame tady, ne v sablone: je to vypocet
    a ten do sablony nepatri. Sablona jen zobrazuje.
    """
    rows = db.query_all(
        """
        SELECT p.*,
               -- Prednost ma delka z relace: u polozky, kterou jsme jeste
               -- nesynchronizovali, zadna v `items` neni - a bez ni by
               -- ukazatel postupu chybel.
               COALESCE(p.media_runtime_ticks, i.runtime_ticks) AS runtime_ticks,
               -- Prednost ma to, co hlasi sama relace. Do `items` sahame,
               -- jen kdyz relace rozmery neposlala (starsi zaznamy).
               COALESCE(p.video_height, i.height) AS height,
               COALESCE(p.video_width,  i.width)  AS width,
               i.video_codec  AS source_codec,
               i.audio_codec  AS source_audio_codec,
               i.image_tag,
               i.series_image_tag,
               i.series_id,
               i.season_name,
               i.index_number,
               i.parent_index_number,
               l.name AS library_name
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        LEFT JOIN libraries l ON l.id = p.library_id
        WHERE p.is_active = 1
        ORDER BY p.started_at
        """
    )

    for row in rows:
        runtime = row.get("runtime_ticks") or 0
        position = row.get("position_ticks") or 0

        row["runtime_seconds"] = runtime / 10_000_000 if runtime else 0
        row["position_seconds"] = position / 10_000_000 if position else 0
        row["remaining_seconds"] = max(
            0.0, row["runtime_seconds"] - row["position_seconds"]
        )
        # Bez znamé delky nema procento smysl - radeji nic nez vymysleny udaj.
        # Zaokrouhlujeme uz tady: procento jde primo do sirky pruhu ve
        # stylu, a "5.126498002663116%" v HTML nikomu neposlouzi.
        row["progress"] = (
            round(min(100.0, position / runtime * 100), 1)
            if runtime and position else None
        )
        # U epizody chceme plakat serialu, ne snimek z dilu - a k nemu
        # otisk toho spravneho plakatu, at se pri zmene neservíruje stary.
        row["poster_id"] = row.get("series_id") or row.get("item_id")
        row["poster_tag"] = (row.get("series_image_tag")
                             if row.get("series_id") else row.get("image_tag"))
        # Co presne se prepocitava - do bubliny u znacky "transcode".
        row["prepocet"] = popis_prepoctu(row)

    return rows


def recently_added(limit: int = 18) -> list[dict[str, Any]]:
    """Naposledy pridane tituly.

    Serial se ma objevit jednou, ne desetkrat za kazdou epizodu.

    Vybira se ve dvou krocich a ma to dobry duvod. Drive se vzal pevny
    pocet nejnovejsich RADKU (limit * 6) a teprve ty se seskupily. Jenze
    kdyz se v Jellyfinu znovu zalozi serial o dvou stech dilech, dostanou
    vsechny dily dnesni datum - a tech sto osm radku je rovnou cely
    zaplni. Ve vypisu pak zbyl jediny serial a vsechno ostatni zmizelo,
    aniz by se kdy vratilo.

    Proto se nejdriv vyberou nejnovejsi SKUPINY (serial nebo film) a az
    potom se k nim dotahnou radky. Kolik dilu ma ktery serial, tim
    prestane hrat roli.
    """
    skupiny_radky = db.query_all(
        """
        SELECT COALESCE(i.series_id, i.id) AS klic,
               MAX(i.date_created)         AS pridano
          FROM items i
         WHERE i.is_missing = 0
           AND i.date_created IS NOT NULL
      GROUP BY COALESCE(i.series_id, i.id)
      ORDER BY MAX(i.date_created) DESC
         LIMIT ?
        """,
        (limit,),
    )
    if not skupiny_radky:
        return []

    klice = [str(radek["klic"]) for radek in skupiny_radky]
    # Starsi nez tohle uz nemuze patrit do zadne z davek, ktere budeme
    # vypisovat - viz _ve_stejne_davce(). Bez toho omezeni by serial
    # o peti stovkach dilu dotahl vsechny.
    nejstarsi = min(str(radek["pridano"]) for radek in skupiny_radky)
    hranice = _o_hodin_zpet(nejstarsi, DAVKA_HODIN)

    otazniky = ",".join("?" for _ in klice)
    rows = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_id, i.series_name,
               i.production_year, i.date_created, i.height, i.size_bytes,
               i.library_id, i.parent_index_number, i.index_number,
               i.image_tag, i.series_image_tag,
               l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.is_missing = 0
          AND i.date_created IS NOT NULL
          AND i.date_created >= ?
          AND COALESCE(i.series_id, i.id) IN ({otazniky})
        ORDER BY i.date_created DESC
        """,
        tuple([hranice] + klice),
    )

    poradi: list[str] = []
    skupiny: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        # Klic skupiny: serial, nebo film sam za sebe.
        key = str(row["series_id"] or row["id"])
        if key not in skupiny:
            if len(skupiny) >= limit:
                continue
            poradi.append(key)
            skupiny[key] = []
        skupiny[key].append(row)

    result: list[dict[str, Any]] = []
    for key in poradi:
        dily = skupiny[key]
        nejnovejsi = dily[0]
        is_episode = bool(nejnovejsi["series_id"])

        # Kolik dilu prislo "v jedne davce". Cela sezona nedorazi v jednu
        # vterinu, ale ani se nerozlozi do tydne - okno jednoho dne od
        # nejnovejsiho dilu je poctivy odhad. Bez nej by se ve vypisu
        # objevily i dily pridane pred mesicem, jen proto, ze u nich uz
        # dlouho zadny dalsi nepribyl.
        davka = [d for d in dily if _ve_stejne_davce(nejnovejsi["date_created"],
                                                     d["date_created"])]
        davka = _serad_dily(davka)

        result.append({
            **nejnovejsi,
            "title": nejnovejsi["series_name"] if is_episode else nejnovejsi["name"],
            # Odkaz vede na konkretni polozku - u serialu na tu nejnovejsi
            # epizodu, coz je presne to, co uzivatele zajima.
            "poster_id": nejnovejsi["series_id"] or nejnovejsi["id"],
            # Otisk toho plakatu. Bez nej drzi prohlizec starou verzi
            # obrazku i po tom, co ji smazeme z mezipameti - adresa je
            # totiz porad tatáž.
            "poster_tag": (nejnovejsi["series_image_tag"] if nejnovejsi["series_id"]
                           else nejnovejsi["image_tag"]),
            "is_episode": is_episode,
            # Ostatni dily te davky. Kdyz prisel jen jeden, seznam je
            # prazdny a karta se chova jako driv - proklik rovnou na nej.
            "episodes": davka if is_episode and len(davka) > 1 else [],
            "series_url": f"/series/{nejnovejsi['series_id']}" if is_episode else None,
        })

    return result


def _serad_dily(davka: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serad davku od prvniho dilu k poslednimu a zahod duplicity.

    Razeni: seznam se cte jako seznam dilu, a ten se cte odshora dolu -
    S01E01 nahore. Podle data pridani to nesedi; Jellyfin nacte soubory
    v poradi, v jakem je najde na disku, takze davka prijde zamichana.

    Duplicity: tentyz dil se ve vypisu objevil dvakrat. Muze za to
    knihovna, kde je epizoda ve dvou souborech (ruzna kvalita, zbyla
    stara verze) - v Jellyfinu jsou to dve polozky s vlastnim ItemId
    a obe do davky doopravdy patri. Ve vypisu novinek je ale takovy
    radek dvakrat jen matouci, takze necháme ten, ktery uz ma zmerena
    technicka data (a pri shode novejsi z nich).
    """
    def poradi(d: dict[str, Any]) -> tuple[Any, ...]:
        # None az na konec: dil bez cisla se nema vecpat pred S01E01.
        rada, cislo = d["parent_index_number"], d["index_number"]
        return (rada is None, rada or 0, cislo is None, cislo or 0,
                str(d["name"] or ""))

    nejlepsi: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for d in davka:
        klic = (d["series_id"], d["parent_index_number"], d["index_number"])
        if klic[1] is None and klic[2] is None:
            # Dil bez cisel se s nicim slucovat nesmi - bez cisla neni
            # jak poznat, jestli jde o tentyz dil, nebo o jiny.
            klic = (d["id"], None, None)
        stavajici = nejlepsi.get(klic)
        if stavajici is None or _prednost_dilu(d) > _prednost_dilu(stavajici):
            nejlepsi[klic] = d

    return sorted(nejlepsi.values(), key=poradi)


def _prednost_dilu(d: dict[str, Any]) -> tuple[int, str]:
    """Ktera ze dvou polozek tehoz dilu se ma ukazat."""
    ma_data = 1 if (d.get("height") or d.get("size_bytes")) else 0
    return (ma_data, str(d["date_created"] or ""))


# Jak daleko od sebe smi byt dily, aby se povazovaly za jednu davku.
DAVKA_HODIN = 24


def _o_hodin_zpet(cas: Any, hodin: int) -> str:
    """Cas posunuty o zadany pocet hodin zpet, ve tvaru z databaze.

    Pouziva se jako spodni hranice dotazu. Kdyz se cas neda precist,
    vraci se prazdny retezec - ten je v porovnani mensi nez cokoliv,
    takze se radeji nevyfiltruje nic.
    """
    try:
        moment = datetime.strptime(str(cas).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return ""
    return (moment - timedelta(hours=hodin)).strftime(db.TIME_FORMAT)


def _ve_stejne_davce(nejnovejsi: Any, zkoumany: Any) -> bool:
    """Prisel tenhle dil spolu s tim nejnovejsim?"""
    try:
        a = datetime.strptime(str(nejnovejsi).replace("T", " ")[:19], db.TIME_FORMAT)
        b = datetime.strptime(str(zkoumany).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return False
    return abs((a - b).total_seconds()) <= DAVKA_HODIN * 3600


# Podle ceho poznáme film od serialu. Jellyfin typuje epizodu jako
# "Episode", film jako "Movie"; cokoliv jineho (koncert, domaci video,
# zive vysilani - a hlavne prevzata historie, u ktere typ vubec neznáme)
# padne do "ostatniho".
KIND_MOVIE = "movies"
KIND_SERIES = "series"
KIND_OTHER = "other"
KIND_BOTH = "both"
ALLOWED_KINDS = (KIND_BOTH, KIND_MOVIE, KIND_SERIES, KIND_OTHER)

# Co v historii znamena "serial". Krome dilu i samotny serial a rada:
#
# Prevzata historie obcas nese druh "Series" misto "Episode" - bud tak
# prisel primo z Jellystatu (viz importers._typ_polozky), nebo se do
# zaznamu zkopiroval z polozky pri sjednoceni nazvu. Rika to min nez
# "Episode": vime jen ze ktereho serialu se divalo, ne ktery dil.
# Porad je to ale serial, takze do "Ostatního" nepatri - tam clovek
# hleda koncerty a zive vysilani, ne hodiny sledovani serialu.
SERIALOVE_TYPY = ("Episode", "Series", "Season")
_ZNAME_TYPY = ("Movie",) + SERIALOVE_TYPY
_SEZNAM_SERIALOVYCH = ", ".join(f"'{typ}'" for typ in SERIALOVE_TYPY)
_SEZNAM_ZNAMYCH = ", ".join(f"'{typ}'" for typ in _ZNAME_TYPY)


def _kind_condition(kind: str, prefix: str = "") -> str:
    """Kus SQL, ktery omezi dotaz na jeden druh.

    `prefix` je nazev tabulky s teckou ("p."). Potrebuje ho dotaz, ktery
    spojuje playback s items - obe tabulky maji sloupec item_type.

    Delici cara je stejna jako v grafu (`daily_activity_split`). Musi byt:
    z tabulky pod grafem vede na kazdem dni proklik do historie i s tim
    filtrem, takze kdyby se rozesly, ukazal by seznam jina cisla, nez na
    ktera clovek prave kliknul.
    """
    if kind == KIND_MOVIE:
        return f" AND {prefix}item_type = 'Movie'"
    if kind == KIND_SERIES:
        return f" AND {prefix}item_type IN ({_SEZNAM_SERIALOVYCH})"
    if kind == KIND_OTHER:
        # IS NULL musi byt zvlast: v SQL neni NULL "ruzne od 'Movie'",
        # NULL neni ruzne od niceho. Bez teho by prevzata historie bez
        # typu vypadla i z "ostatniho" - a to je prave ta cast, kvuli
        # ktere tenhle filtr vznikl.
        return (f" AND ({prefix}item_type IS NULL OR {prefix}item_type = ''"
                f" OR {prefix}item_type NOT IN ({_SEZNAM_ZNAMYCH}))")
    return ""


# Jak se druhu polozky rika cesky. Klice jsou to, co posila Jellyfin.
#
# K cemu to je: v historii i v bublinach grafu chceme misto "TvChannel"
# videt "Živé vysílání". Co v seznamu neni, ukaze se tak, jak to prislo -
# lepsi nez schovat to pod "Ostatní", protoze prave ta surova hodnota je
# stopa, podle ktere se da dohledat, o co slo.
TYPY_POLOZEK = {
    "Movie": "Film",
    "Episode": "Díl seriálu",
    "Series": "Seriál",
    "Season": "Řada",
    "Audio": "Hudba",
    "MusicVideo": "Videoklip",
    "MusicAlbum": "Album",
    "Video": "Video",
    "Trailer": "Upoutávka",
    "TvChannel": "Živé vysílání",
    "LiveTvChannel": "Živé vysílání",
    "TvProgram": "Pořad",
    "Program": "Pořad",
    "Recording": "Nahrávka",
    "Book": "Kniha",
    "AudioBook": "Audiokniha",
    "Photo": "Fotka",
}

# Jak se v historii pojmenuje zaznam, u ktereho typ vubec neni. Typicky
# prevzata historie: Jellystat ani Playback Reporting druh polozky
# neposilaji, takze u nich zustane prazdny.
TYP_NEZNAMY = "Neznámý (z importu)"


def nazev_typu(item_type: Any) -> str:
    """Cesky nazev druhu polozky. Prazdna hodnota = prevzaty zaznam."""
    text = str(item_type or "").strip()
    if not text:
        return _t(TYP_NEZNAMY)
    prelozeny = TYPY_POLOZEK.get(text)
    return _t(prelozeny) if prelozeny else text


def rozpad_ostatnich(days: int) -> list[dict[str, Any]]:
    """Z ceho se sklada "Ostatní" - podle druhu, od nejvetsiho.

    Bez tohohle je "Ostatní" v grafu slepa skvrna: clovek vidi hodiny,
    ale nema jak zjistit, co to bylo. Pouziva se jako popisek pod grafem
    a v bubline u legendy.
    """
    rows = db.query_all(
        f"""
        SELECT item_type,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {_kind_condition(KIND_OTHER)}
        GROUP BY item_type
        ORDER BY SUM(watched_seconds) DESC
        """,
        (*_meze(days),),
    )
    # Slucujeme podle popisku, ne podle syrove hodnoty: prazdny retezec
    # a NULL jsou pro SQL dve ruzne skupiny, ale pro cloveka jedna a tataz
    # vec - zaznam, u ktereho druh neznáme. Bez toho by se pod grafem
    # objevilo "Neznámý (z importu)" dvakrat za sebou s jinymi cisly.
    slouceno: dict[str, dict[str, Any]] = {}
    for r in rows:
        popisek = nazev_typu(r["item_type"])
        polozka = slouceno.setdefault(
            popisek, {"item_type": r["item_type"], "label": popisek,
                      "hours": 0.0, "plays": 0})
        polozka["hours"] += float(r["hours"] or 0)
        polozka["plays"] += int(r["plays"] or 0)

    for polozka in slouceno.values():
        polozka["hours"] = round(polozka["hours"], 2)
    return sorted(slouceno.values(), key=lambda p: p["hours"], reverse=True)


def _mistni_den(mistni: str, utc: str, konec: bool = False):
    """Datum meze v zone aplikace.

    Horni mez je vzdycky vylucna (pulnoc dne za poslednim), takze se
    u konce vraci o vterinu zpatky - jinak by kalendar mel o den navic.
    """
    if mistni:
        cas = datetime.strptime(mistni, db.TIME_FORMAT)
    else:
        cas = (datetime.strptime(utc, db.TIME_FORMAT)
               .replace(tzinfo=timezone.utc).astimezone(formatting.zona())
               .replace(tzinfo=None))
    return (cas - timedelta(seconds=1)).date() if konec else cas.date()


def daily_activity_split(days: int) -> list[dict[str, Any]]:
    """Sledovanost po dnech rozpadla na filmy a serialy.

    Vraci jeden radek na den se ctyrmi cisly: hodiny a spusteni zvlast
    pro filmy a pro serialy. Soucet si udela sablona - kdyby se posilal
    predpocitany, musely by se pri prepnutí filtru tahat data znovu.

    Proc rozpad podle typu polozky a ne podle knihovny: knihovnu si kazdy
    pojmenuje jinak ("Filmy", "Movies", "4K"), typ polozky hlasi Jellyfin
    vzdycky stejne.
    """
    rows = db.query_all(
        """
        SELECT date(started_at, 'localtime') AS day,
               item_type,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY day, item_type
        ORDER BY day
        """,
        (*_meze(days),),
    )

    # "other" je treti pytlik na vsechno, co neni film ani epizoda:
    # koncerty, domaci videa - a hlavne importovana historie, ke ktere
    # jeste nemame polozku v knihovne, takze typ neznáme.
    #
    # Bez nej se takove zaznamy ztratily uplne: soucet se pocital jako
    # filmy + serialy, takze po importu do prazdne knihovny zustal graf
    # prazdny, i kdyz hodiny v souhrnu nad nim sedely.
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        entry = by_day.setdefault(
            str(row["day"]),
            {"movie_hours": 0.0, "movie_plays": 0, "series_hours": 0.0,
             "series_plays": 0, "other_hours": 0.0, "other_plays": 0},
        )
        prefix = ("movie" if row["item_type"] == "Movie"
                  else "series" if row["item_type"] in SERIALOVE_TYPY
                  else "other")
        entry[f"{prefix}_hours"] += float(row["hours"] or 0)
        entry[f"{prefix}_plays"] += int(row["plays"] or 0)

    # Kalendar kreslime cely, at je v grafu videt i den, kdy se nekoukalo.
    #
    # U "poslednich N dni" je poslednim dnem dnesek a prvnim ten N-ty
    # zpatky - tedy N radku, ne N+1. U vlastniho obdobi zacina a konci
    # tam, kde rekl clovek.
    okno = _obdobi(days)
    # Vsechna data tu jsou uz prepoctena do zony aplikace (viz 'localtime'
    # v dotazu vyse) - kalendar se proto musi stavet ze stejnych hodin.
    # Meze okna jsou v UTC; kdyby se z nich bral den primo, zacinal by
    # graf v lete o den driv, protoze pulnoc v Praze je 22:00 predchoziho
    # dne v UTC.
    dnes = datetime.now(formatting.zona()).date()
    if okno.relativni:
        prvni, posledni = dnes - timedelta(days=okno.dny - 1), dnes
    else:
        prvni = _mistni_den(okno.od_mistni, okno.od)
        posledni = min(dnes, _mistni_den(okno.do_mistni, okno.do, konec=True))
    calendar = [prvni + timedelta(days=offset)
                for offset in range(max(1, (posledni - prvni).days + 1))]

    result = []
    for day in calendar:
        entry = by_day.get(day.isoformat(), {})
        movie_hours = round(float(entry.get("movie_hours") or 0.0), 2)
        series_hours = round(float(entry.get("series_hours") or 0.0), 2)
        other_hours = round(float(entry.get("other_hours") or 0.0), 2)
        movie_plays = int(entry.get("movie_plays") or 0)
        series_plays = int(entry.get("series_plays") or 0)
        other_plays = int(entry.get("other_plays") or 0)
        result.append({
            "day": day.isoformat(),
            "movie_hours": movie_hours,
            "series_hours": series_hours,
            "other_hours": other_hours,
            "movie_plays": movie_plays,
            "series_plays": series_plays,
            "other_plays": other_plays,
            # Soucet vsech tri, at "Celkem" v tabulce odpovida skutecnosti
            # i tehdy, kdyz typ neznáme.
            "hours": round(movie_hours + series_hours + other_hours, 2),
            "plays": movie_plays + series_plays + other_plays,
        })
    return result


def top_users(days: int, limit: int = 8) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT user_name                     AS label,
               user_id,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          AND user_name IS NOT NULL
        GROUP BY user_id, user_name
        ORDER BY hours DESC
        LIMIT ?
        """,
        (*_meze(days), limit),
    )


# Název dílu tak, jak ho zapisuje plugin Playback Reporting a některé
# klienty: "Seriál - s01e01 - Název dílu". U takového záznamu nemáme
# `series_name` ani položku v knihovně, takže seriál se dá poznat jedině
# odtud - a bez toho se v přehledech rozpadl na jednotlivé díly.
_SERIAL_Z_NAZVU = re.compile(r"^(?P<serial>.+?)\s*[-–]\s*s\d{1,3}e\d{1,4}\b", re.I)


def serial_z_nazvu(nazev: Any) -> str | None:
    """Ze zápisu "Seriál - s01e01 - Díl" vytáhne název seriálu."""
    if not nazev:
        return None
    shoda = _SERIAL_Z_NAZVU.match(str(nazev).strip())
    if not shoda:
        return None
    serial = shoda.group("serial").strip(" -–")
    return serial or None


def _dily_podle_nazvu() -> dict[str, tuple[str, str]]:
    """Názvy epizod z knihovny -> id seriálu, ke kterému patří.

    Slouží k jediné věci: záznam historie, který nese jen jméno dílu
    ("Zvony") a k jehož položce už v knihovně nic nevede, se dá zařadit
    pod seriál, do kterého ten díl patří ("Hra o trůny"). Bez toho z něj
    v přehledu vznikl samostatný titul - film, který neexistuje.

    **Jen jednoznačné shody.** Názvy dílů se mezi seriály opakují
    ("Pilot", "Část 1"); u takového jména radši nehádáme, protože špatně
    přiřazená historie je horší než ta, o které víme, že je stranou.

    Klíče mají předponu `dil:`, aby se nemíchaly s názvy seriálů - seriál
    a jeho epizoda se můžou jmenovat stejně.
    """
    podle_jmena: dict[str, set[str]] = {}
    for radek in db.query_all(
        "SELECT name, series_id FROM items WHERE series_id IS NOT NULL"
        "  AND name IS NOT NULL AND name != ''"
    ):
        jmeno = str(radek["name"]).strip().lower()
        podle_jmena.setdefault(jmeno, set()).add(str(radek["series_id"]))

    jmena = _jmena_serialu()
    return {f"dil:{jmeno}": (next(iter(serialy)),
                             jmena.get(next(iter(serialy)), "?"))
            for jmeno, serialy in podle_jmena.items() if len(serialy) == 1}


def _jmena_serialu() -> dict[str, str]:
    """id seriálu -> jeho název. Pro popisek řádku."""
    return {str(r["series_id"]): str(r["name"]) for r in db.query_all(
        "SELECT series_id, MAX(series_name) AS name FROM items"
        " WHERE series_id IS NOT NULL AND series_name IS NOT NULL"
        " GROUP BY series_id")}


def klic_titulu(row: dict[str, Any],
                podle_nazvu: dict[str, str] | None = None) -> tuple[str, str, bool]:
    """Do jaké skupiny řádek historie patří: (klíč, popisek, je to seriál).

    Pořadí je od nejspolehlivějšího údaje k nejslabšímu:

      1. `series_id` z knihovny - jediné, co nezáleží na tom, jak se který
         záznam kdysi vyplnil,
      2. název seriálu (ze záznamu nebo z položky),
      3. název seriálu vyparsovaný z "Seriál - s01e01 - Díl",
      4. díl toho jména, který zná knihovna (viz `_dily_podle_nazvu()`),
      5. samotná položka - film, nebo díl, u kterého o seriálu nevíme nic.

    Body 3 a 4 tu jsou proto, že převzatá historie o seriálu neřekne nic.
    Playback Reporting ukládá název jako "Blue - s01e17 - Calypso"; jiné
    zdroje jen "Zvony". V přehledu se pak objevilo pět samostatných řádků
    místo jednoho "Blue", nebo díl "Zvony" jako film vedle "Hry o trůny".
    """
    if row.get("series_id"):
        return (f"serial:{row['series_id']}",
                str(row.get("series_name") or row.get("i_series_name")
                    or serial_z_nazvu(row.get("item_name")) or row.get("i_name") or "?"),
                True)

    # Položku v knihovně známe a **není to epizoda** - tím je hádání
    # skončené. Všechno pod tímhle řádkem jsou dohady z názvu a ty jsou
    # tu jen pro záznamy, u kterých se nedá zjistit nic jiného.
    #
    # Bez téhle zábrany se stalo tohle: film se jmenoval stejně jako díl
    # jiného seriálu (nebo jako seriál sám), takže spadl do jeho skupiny.
    # Skupina se pak jmenovala po tom druhém titulu, ale proklik vedl na
    # tenhle film - popisek říkal jedno a odkaz vedl jinam.
    #
    # Epizoda bez `series_id` je výjimka: tam hádání pomáhá, protože
    # k seriálu ji jinak nemáme jak přiřadit.
    if row.get("existujici_id") and str(row.get("i_type") or "") != "Episode":
        return (f"polozka:{row.get('item_id') or row['existujici_id']}",
                str(row.get("i_name") or row.get("item_name") or "?"), False)

    # Poslední pokus, ještě před vzdáním se: díl toho jména v knihovně
    # existuje a seriál zná. Záznam o něm neví nic a poznat ho z názvu
    # ("Zvony") nejde - ale knihovna ho zná. Bez tohohle z něj v přehledu
    # vznikl samostatný titul vedle seriálu, do kterého patří.
    # Viz `_dily_podle_nazvu()`.
    z_knihovny = (podle_nazvu or {}).get(
        "dil:" + str(row.get("item_name") or "").strip().lower())
    if z_knihovny:
        serial, jmeno = z_knihovny
        return (f"serial:{serial}", jmeno, True)

    nazev_serialu = (row.get("series_name") or row.get("i_series_name")
                     or serial_z_nazvu(row.get("item_name")))
    if nazev_serialu:
        srovnany = str(nazev_serialu).strip().lower()
        # Když ten samý seriál v knihovně známe, použijeme jeho id -
        # jinak by se rozpadl na dva řádky: jeden složený z importované
        # historie (kde je jen název) a druhý z vlastního sběru (kde už
        # je i id). Přesně to bylo vidět v přehledu.
        znamy = (podle_nazvu or {}).get(srovnany)
        if znamy:
            return (f"serial:{znamy}", str(nazev_serialu).strip(), True)

        # Klíč z názvu je slabší než z id, ale pořád sloučí to, co k sobě
        # patří. Porovnává se malými písmeny - "Blue" a "blue" je totéž.
        return (f"nazev:{srovnany}", str(nazev_serialu).strip(), True)

    # A ještě jedna možnost: název záznamu je rovnou jméno seriálu.
    # Stane se to u převzaté historie, která si epizodu nepamatuje,
    # nebo když Jellyfin zaznamená přehrávání na úrovni seriálu.
    jako_serial = (podle_nazvu or {}).get(
        str(row.get("item_name") or "").strip().lower())
    if jako_serial:
        return (f"serial:{jako_serial}", str(row["item_name"]).strip(), True)

    polozka = row.get("item_id") or row.get("item_name") or "?"
    return (f"polozka:{polozka}",
            str(row.get("i_name") or row.get("item_name") or "?"), False)


# Sloupce, ze kterých `klic_titulu()` skládá skupinu. Používají je oba
# dotazy níž (nejsledovanější tituly i překódované soubory), takže je
# lepší mít je na jednom místě než ve dvou kopiích, které se časem
# rozejdou.
#
# Seskupuje se přes SKUPINA_TITULU_KLIC, ne přes holé `p.item_id`: záznam
# bez id položky se v Jellyscope běžně nevyskytuje, ale kdyby se objevil,
# spadly by všechny takové řádky do jedné skupiny a vznikl by titul
# poskládaný z několika různých filmů.
SKUPINA_TITULU_KLIC = "COALESCE(p.item_id, p.item_name)"
SKUPINA_TITULU_SLOUPCE = """
        MAX(p.item_id)             AS item_id,
        MAX(p.item_name)           AS item_name,
        MAX(p.series_name)         AS series_name,
        MAX(i.id)                  AS existujici_id,
        MAX(i.series_id)           AS series_id,
        MAX(i.series_name)         AS i_series_name,
        MAX(i.name)                AS i_name,
        MAX(i.type)                AS i_type,
        MAX(i.is_missing)          AS is_missing
"""


def _slouc_tituly(radky: list[dict[str, Any]], kind: str, limit: int,
                  hodnoty: dict[str, str],
                  prenest: tuple[str, ...] = (),
                  skryt_nezarazene: bool = False) -> list[dict[str, Any]]:
    """Řádky za jednotlivé položky složí do titulů (seriál = jeden řádek).

    `hodnoty` říká, které sloupce se mají sečíst - u nejsledovanějších
    titulů jsou to hodiny a spuštění, u překódovaných počet transcodů.

    `prenest` jsou sloupce, které se nesčítají, ale opisují od nejsilnějšího
    dílu skupiny - kodek, rozlišení, důvod transcode. U seriálu jsou
    prakticky u všech dílů stejné, takže průměrovat je by nic nepřineslo.

    `skryt_nezarazene` vynechá tituly, ke kterým v knihovně **ani v archivu**
    nic nevede. Jsou to zbytky historie, u kterých se nedá zjistit, o co
    šlo: záznam nese jen "6. díl" nebo "Epizoda 6" a takový díl má v knihovně
    každý seriál, takže jednoznačná shoda neexistuje. Přiřadit je naslepo
    by znamenalo připsat sledování cizímu seriálu.

    V žebříčku nejsledovanějších takový řádek nic neříká - nejde
    prokliknout a tváří se jako film, který neexistuje. Kolik jich bylo,
    se vrací v `_skryto`, aby to stránka mohla napsat nahlas; odsledovaný
    čas se tím nikde neztrácí, Historie ho ukazuje dál.

    Slučuje se v Pythonu, ne v SQL: klíč skupiny umí spadnout až na
    rozbor názvu ("Seriál - s01e01 - Díl") a to se v SQL napsat srozumitelně
    nedá ani v jedné z obou databází.
    """
    # Nejdřív si projdeme řádky a zapamatujeme, které jméno seriálu patří
    # ke kterému id. Teprve pak se skládají skupiny - jinak by se stejný
    # seriál rozdělil podle toho, jestli ten který záznam id zná.
    podle_nazvu: dict[str, str] = {}
    for radek in radky:
        if not radek.get("series_id"):
            continue
        for jmeno in (radek.get("series_name"), radek.get("i_series_name"),
                      serial_z_nazvu(radek.get("item_name"))):
            if jmeno:
                podle_nazvu.setdefault(str(jmeno).strip().lower(),
                                       str(radek["series_id"]))

    # Doplníme názvy z knihovny, ne jen z řádků výsledku: seriál, ze
    # kterého je v období jen osiřelý záznam, by jinak v mapě chyběl
    # a neměl by se k čemu přiřadit.
    for serial, jmeno in _jmena_serialu().items():
        podle_nazvu.setdefault(jmeno.strip().lower(), serial)

    # A názvy dílů. Řeší případ, kdy záznam nese jen jméno epizody
    # ("Zvony") a k položce už nic nevede - v přehledu z něj jinak
    # vznikne samostatný "film" vedle seriálu, do kterého patří.
    podle_nazvu.update(_dily_podle_nazvu())

    skupiny: dict[str, dict[str, Any]] = {}

    for radek in radky:
        klic, popisek, je_serial = klic_titulu(radek, podle_nazvu)

        if kind == KIND_MOVIE and je_serial:
            continue
        if kind == KIND_SERIES and not je_serial:
            continue

        skupina = skupiny.get(klic)
        if skupina is None:
            skupina = {"group_key": klic, "label": popisek, "is_series": je_serial,
                       "_zastupci": [], "_nejsilnejsi": (-1.0, {})}
            for cil in hodnoty:
                skupina[cil] = 0.0
            skupiny[klic] = skupina
        elif je_serial and len(popisek) < len(skupina["label"]):
            # Kratší popisek je ten obecnější ("Blue" vs. "Blue - s01e17
            # - Calypso"), a o seriálu mluvíme jeho jménem.
            skupina["label"] = popisek

        vaha = float(radek.get(next(iter(hodnoty.values()))) or 0)
        for cil, zdroj in hodnoty.items():
            skupina[cil] += float(radek.get(zdroj) or 0)
        if vaha > skupina["_nejsilnejsi"][0]:
            skupina["_nejsilnejsi"] = (vaha, radek)

        # Zástupce pro proklik. Bere se i **archivovaná** položka, tedy
        # ta, která v knihovně byla a v Jellyfinu už není: sledování se
        # opravdu stalo, detail se otevře a je na něm vidět, že je titul
        # v archivu. Živá má přednost, proto to první číslo (0/1).
        #
        # Bez archivu by film, který jsi kdysi viděl a od té doby smazal,
        # spadl mezi "nezařazené" a `skryt_nezarazene` by ho vyhodil -
        # zmizela by platná statistika, ne odpad.
        if radek.get("existujici_id"):
            skupina["_zastupci"].append(
                (1 if radek.get("is_missing") else 0,
                 -float(radek.get(next(iter(hodnoty.values()))) or 0),
                 radek["existujici_id"], radek.get("series_id")))

    poradi = next(iter(hodnoty))
    serazene = sorted(skupiny.values(), key=lambda r: r[poradi], reverse=True)

    # Odkaz se skládá až tady, takže "nezařazený" se pozná až po něm.
    # Proto se ořezává na `limit` až nakonec - jinak by skryté řádky
    # ubraly místo těm, které se mají ukázat.
    for skupina in serazene:
        _, nejsilnejsi = skupina.pop("_nejsilnejsi")
        for sloupec in prenest:
            skupina[sloupec] = nejsilnejsi.get(sloupec)

        zastupci = skupina.pop("_zastupci")
        if not zastupci:
            # Žádný záznam téhle skupiny neukazuje na živou položku. Když
            # ale skupinu určil seriál z knihovny, odkaz máme i tak - klíč
            # nese jeho id. Nastane to u historie, kde k dílu už položka
            # nevede, ale sám seriál v knihovně pořád je.
            klic = str(skupina["group_key"])
            skupina["detail_url"] = ("/series/" + klic[len("serial:"):]
                                     if klic.startswith("serial:") else None)
            # Titul, ke kterému už v knihovně není vůbec nic, zůstane
            # bez odkazu - odkaz na chybovou stránku 404 je horší než žádný.
            continue
        # Živé napřed (0 < 1), v rámci toho nejsledovanější (váha je
        # záporná, takže obyčejné vzestupné řazení dá největší první).
        # Odkaz se řídí **klíčem skupiny**, ne nejsilnějším záznamem.
        #
        # Tohle byl nahlášený bug: skupina seriálu se jmenovala správně,
        # ale proklik vedl na cizí položku. Stane se to, když do skupiny
        # spadne záznam, který se k seriálu přiřadil podle názvu - ten
        # `series_id` sám nenese (proto se hádalo z názvu), takže z něj
        # vyšel odkaz na jeho vlastní položku. A protože se zástupce
        # vybírá podle odsledovaného času, stačilo, aby byl takový záznam
        # nejsilnější, a odkaz zamířil docela jinam než popisek.
        #
        # Klíč `serial:<id>` je přitom to nejspolehlivější, co o skupině
        # víme - id seriálu z knihovny. Když ho máme, odkaz nemá být
        # z čeho jiného.
        klic = str(skupina["group_key"])
        if klic.startswith("serial:"):
            skupina["detail_url"] = "/series/" + klic[len("serial:"):]
            continue

        zastupci.sort()
        _, _, polozka, serial = zastupci[0]
        skupina["detail_url"] = (f"/series/{serial}" if serial
                                 else f"/item/{polozka}")

    if not skryt_nezarazene:
        return serazene[:limit]

    zarazene = [s for s in serazene if s["detail_url"]]

    # Když by skrývání nenechalo vůbec nic, neskrývá se. Nastane to
    # v jediné situaci, zato důležité: historie se naimportuje DŘÍV, než
    # se aplikace napojí na Jellyfin. Knihovna je pak prázdná, takže žádný
    # titul nemá protějšek - a člověk by po importu koukal na prázdný
    # žebříček a myslel si, že se nic nenaimportovalo. Jakmile
    # synchronizace proběhne, tituly se navážou a skrývání začne dávat
    # smysl samo.
    if not zarazene:
        return serazene[:limit]

    skryto = len(serazene) - len(zarazene)
    vysledek = zarazene[:limit]
    if vysledek:
        vysledek[0]["_skryto"] = skryto
    return vysledek


def top_items(days: int, limit: int = 10,
              kind: str = KIND_BOTH) -> list[dict[str, Any]]:
    """Nejsledovanejsi tituly. Epizody stejneho serialu se scitaji.

    `kind` je "both" / "movies" / "series" - stejne tri moznosti jako
    u sledovanosti po dnech, at se clovek nemusi ucit dve ruzne ovladani.

    Jak se serial pozna a kam vede proklik, popisuje `klic_titulu()`
    a `_slouc_tituly()`.
    """
    radky = db.query_all(
        f"""
        SELECT {SKUPINA_TITULU_SLOUPCE},
               SUM(p.watched_seconds) AS seconds,
               COUNT(*)               AS plays
        FROM playback p
   LEFT JOIN items i ON i.id = p.item_id
       WHERE p.started_at >= ? AND p.started_at < ?
         AND p.watched_seconds > 0
    GROUP BY {SKUPINA_TITULU_KLIC}
        """,
        (*_meze(days),),
    )

    tituly = _slouc_tituly(radky, kind, limit,
                           {"seconds": "seconds", "plays": "plays"},
                           skryt_nezarazene=True)
    for titul in tituly:
        titul["hours"] = titul["seconds"] / 3600.0
        titul["plays"] = int(titul["plays"])
    return tituly


def play_method_breakdown(days: int) -> list[dict[str, Any]]:
    """Kolik casu se hralo primo a kolik se muselo prepocitavat."""
    rows = db.query_all(
        """
        SELECT COALESCE(play_method, 'nezname') AS method,
               SUM(watched_seconds) / 3600.0    AS hours
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY method
        ORDER BY hours DESC
        """,
        (*_meze(days),),
    )
    # Popisky prochazeji `_t()`, protoze se vypisuji uzivateli - jak
    # v legende, tak primo v pruhu grafu.
    labels = {
        "DirectPlay": _t("Přímé přehrávání"),
        "DirectStream": _t("Přebalení (direct stream)"),
        "Transcode": _t("Transcode"),
        "nezname": _t("Neznámé"),
    }
    # Barva podle toho, co to server stoji - ne podle poradi v seznamu.
    # Tyhle tri hodnoty nejsou libovolne kategorie, ale stupnice: prime
    # prehravani neni prace zadna, prebaleni skoro zadna, prepocet vytizi
    # procesor nebo grafickou kartu. Graf tim rika totez co odznak
    # u prehravani a jantarovy kus na konci je varovani.
    # Presne tytez role, jake nese odznak u prehravani - viz makro
    # `method_badge`. Kdyz se lisily (odznak oranzovy, graf jantarovy),
    # vypadalo to jako dve ruzne veci; pritom je to tentyz udaj jednou
    # jako stav a podruhe jako podil.
    # Role se hleda podle ZACATKU nazvu, ne presnou shodou. Importovana
    # historie (Playback Reporting) nese podrobnejsi hodnoty - treba
    # "Transcode (v:h264 a:direct)" - a ty by pri presne shode propadly
    # do "muted", takze by v grafu zesedly, prestoze jde o prepocet.
    def _role(metoda: str) -> str:
        m = (metoda or "").strip().lower()
        if m.startswith("transcode"):
            return "serious"
        if m.startswith("directstream") or m.startswith("remux"):
            return "info"
        if m.startswith("directplay") or m.startswith("direct play"):
            return "good"
        return "muted"

    # Vic variant prepoctu pod sebou by melo jednu barvu a nesly by
    # rozeznat. Prvni si nechava oranzovou role - at je na prvni pohled
    # videt, ze jde o prepocet -, dalsi berou barvy z palety serii, tedy
    # z te same, jakou pouziva "kdo v jakem jazyce sleduje".
    #
    # Odstinovani jedne barvy tu bylo drive a nefungovalo: casti byvaji
    # uzke par pixelu a tri odstiny hnede na takove plose splynou.
    DALSI_BARVY = ("var(--series-5)", "var(--series-7)", "var(--series-4)",
                   "var(--series-8)", "var(--series-2)", "var(--series-6)")
    poradi: dict[str, int] = {}
    for row in rows:
        row["label"] = labels.get(row["method"], row["method"])
        row["role"] = _role(row["method"])
        poradi[row["role"]] = poradi.get(row["role"], -1) + 1
        kolikaty = poradi[row["role"]]
        if kolikaty:
            row["barva"] = DALSI_BARVY[(kolikaty - 1) % len(DALSI_BARVY)]
        # Graf deleneho pruhu ocekava klic "value" - pripravime ho tady,
        # at sablona nemusi nic pocitat.
        row["value"] = row["hours"]
    return rows


def client_breakdown(days: int, limit: int = 8) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT COALESCE(client, 'nezname') AS label,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*) AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY label
        ORDER BY hours DESC
        LIMIT ?
        """,
        (*_meze(days), limit),
    )


def hourly_heatmap(days: int) -> list[list[float]]:
    """Mrizka 7 dnu x 24 hodin s poctem odsledovanych hodin.

    Vraci seznam sedmi radku (pondeli az nedele), kazdy o 24 hodnotach.
    """
    rows = db.query_all(
        """
        SELECT CAST(strftime('%w', started_at, 'localtime') AS INTEGER) AS weekday,
               CAST(strftime('%H', started_at, 'localtime') AS INTEGER) AS hour,
               SUM(watched_seconds) / 3600.0 AS hours
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY weekday, hour
        """,
        (*_meze(days),),
    )

    grid = [[0.0 for _ in range(24)] for _ in range(7)]
    for row in rows:
        # SQLite pocita tyden od nedele (0). My chceme od pondeli.
        weekday = (int(row["weekday"]) + 6) % 7
        grid[weekday][int(row["hour"])] += float(row["hours"] or 0)
    return grid


# Co vsechno jde v historii filtrovat. Hodnoty se vzdycky predavaji jako
# parametr - z adresy se do SQL nikdy nedostane nic jineho nez otaznik.
ZPUSOBY = ("DirectPlay", "DirectStream", "Transcode")


def _filtr_historie(
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_BOTH,
    od: str | None = None,
    do: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
) -> tuple[list[str], list[Any]]:
    """Slozi podminky a hodnoty pro dotaz do historie.

    Jedno misto pro seznam i pro pocitadlo. Kdyby si kazdy stavel
    podminky sam, staci pridat filtr do jednoho z nich - a strankovani
    zacne lhat, protoze celkovy pocet uz neodpovida vypisu.
    """
    where = ["p.watched_seconds > 0"]
    params: list[Any] = []

    if user_id:
        where.append("p.user_id = ?")
        params.append(user_id)

    if search:
        where.append("(p.item_name LIKE ? OR p.series_name LIKE ?)")
        # Znaky % kolem hledaneho textu znamenaji "kdekoliv uvnitr".
        # Text jde do dotazu jako parametr, nikdy se nelepi do SQL.
        params.extend([f"%{search}%", f"%{search}%"])

    # Jeden den je zvlastni pripad obdobi - prijde proklikem z tabulky
    # na Prehledu. Kdyz je vyplneny, obdobi se ignoruje.
    if day:
        where.append("date(p.started_at, 'localtime') = ?")
        params.append(day)
    else:
        if od:
            where.append("date(p.started_at, 'localtime') >= ?")
            params.append(od)
        if do:
            where.append("date(p.started_at, 'localtime') <= ?")
            params.append(do)

    podminka = _kind_condition(kind, "p.")
    if podminka:
        where.append(podminka.replace(" AND ", "", 1))

    if method in ZPUSOBY:
        where.append("p.play_method = ?")
        params.append(method)

    if client:
        where.append("p.client = ?")
        params.append(client)

    if language:
        # "und" znamena "nezjisteno" - a k tomu patri i prazdna hodnota,
        # jinak by se filtr minul s vetsinou zaznamu.
        if language == "und":
            where.append("(p.audio_language IS NULL OR p.audio_language = ''"
                         " OR p.audio_language = 'und')")
        else:
            where.append("p.audio_language = ?")
            params.append(language)

    return where, params


def history(
    limit: int = 50,
    offset: int = 0,
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_BOTH,
    od: str | None = None,
    do: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Surova historie prehravani, strankovana a filtrovana.

    Filtry se skladaji: uzivatel + druh + obdobi + zpusob prehrani +
    klient + jazyk naraz. Vsechny jsou v adrese, takze vysledek jde
    poslat odkazem a po obnoveni stranky zustane, co si clovek nastavil.
    """
    where, params = _filtr_historie(user_id, search, day, kind, od, do,
                                    method, client, language)
    params.extend([limit, offset])
    rows = db.query_all(
        f"""
        SELECT p.*,
               -- Prednost maji rozmery zaznamenane u prehravani: relace vi,
               -- co doopravdy teklo, kdezto `items` popisuje soubor tak, jak
               -- ho zname z posledni synchronizace.
               COALESCE(p.video_height, i.height) AS height,
               COALESCE(p.video_width,  i.width)  AS width,
               -- Kodeky souboru. Vedle nich stoji p.video_codec, tedy to,
               -- co skutecne teklo k prehravaci - a prave rozdil mezi
               -- nimi rika, co se prepocitavalo.
               i.video_codec AS source_codec,
               i.audio_codec AS source_audio_codec
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE {' AND '.join(where)}
        ORDER BY p.started_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    for row in rows:
        # Stejna bublina jako u "prave se hraje" - jednou napsany rozbor
        # slouzi obema mistum.
        row["prepocet"] = popis_prepoctu(row)
    return rows


def history_count(
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_BOTH,
    od: str | None = None,
    do: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
) -> int:
    """Kolik zaznamu filtru odpovida - podle toho se strankuje."""
    where, params = _filtr_historie(user_id, search, day, kind, od, do,
                                    method, client, language)
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE {' AND '.join(where)}
        """,
        tuple(params),
    ))


def hodnoty_filtru() -> dict[str, list[Any]]:
    """Cim vsim se da filtrovat - podle toho, co v historii doopravdy je.

    Nabizet vsechny mozne klienty a jazyky by znamenalo dlouhy seznam
    voleb, ktere u tebe stejne nic nenajdou.
    """
    klienti = [r["client"] for r in db.query_all(
        "SELECT client, COUNT(*) AS n FROM playback"
        " WHERE client IS NOT NULL AND client != ''"
        " GROUP BY client ORDER BY n DESC LIMIT 25") if r["client"]]

    jazyky = [r["audio_language"] for r in db.query_all(
        "SELECT audio_language, COUNT(*) AS n FROM playback"
        " WHERE audio_language IS NOT NULL AND audio_language != ''"
        " GROUP BY audio_language ORDER BY n DESC LIMIT 25")
        if r["audio_language"]]

    zpusoby = [r["play_method"] for r in db.query_all(
        "SELECT play_method, COUNT(*) AS n FROM playback"
        " WHERE play_method IS NOT NULL AND play_method != ''"
        " GROUP BY play_method ORDER BY n DESC") if r["play_method"]]

    return {"klienti": klienti, "jazyky": jazyky, "zpusoby": zpusoby}


# ---------------------------------------------------------------------------
# Uzivatele
# ---------------------------------------------------------------------------

def user_table(days: int) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT u.id,
               u.name,
               u.is_administrator,
               COALESCE(SUM(p.watched_seconds), 0) / 3600.0 AS hours,
               COUNT(p.id)                                  AS plays,
               COUNT(DISTINCT p.item_id)                    AS item_count,
               MAX(p.started_at)                            AS last_seen,
               COALESCE(SUM(CASE WHEN p.play_method = 'Transcode'
                                 THEN p.watched_seconds ELSE 0 END), 0) / 3600.0
                                                            AS transcoded_hours
        FROM users u
        LEFT JOIN playback p
               ON p.user_id = u.id
              AND p.started_at >= ? AND p.started_at < ?
              AND p.watched_seconds > 0
        GROUP BY u.id, u.name, u.is_administrator
        ORDER BY hours DESC, u.name
        """,
        (*_meze(days),),
    )


def _s_prokliknutim(rows: list[dict[str, Any]],
                    zastupci: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ke slucovanym radkum doplni id polozky, na kterou se da prokliknout.

    `zastupci` jsou serazeni od nejsledovanejsiho, takze prvni nalez pro
    dany nazev je ten spravny.
    """
    nejlepsi: dict[str, str] = {}
    for row in zastupci:
        nejlepsi.setdefault(str(row["label"]), str(row["item_id"]))
    for row in rows:
        row["item_id"] = nejlepsi.get(str(row["label"]))
    return rows


def user_genres(user_id: str, days: int, limit: int = 8) -> list[dict[str, Any]]:
    """Zanry, ktere uzivatel sleduje - podle odsledovaneho casu.

    Jeden titul ma zanru vic ("Akcni, Sci-Fi, Dobrodruzny"), takze se jeho
    cas zapocita do kazdeho z nich. Soucet proto neni roven celkovemu casu
    a procenta se nescitaji na sto - v UI je u toho poznamka.

    Rozdelit cas mezi zanry rovnym dilem by vypadalo poctiveji, ale nebylo
    by to o nic pravdivejsi: nikdo nevi, kolik z filmu bylo "akce" a kolik
    "sci-fi". Radeji rekneme nahlas, ze se titul pocita vickrat.
    """
    rows = db.query_all(
        """
        SELECT i.genres, SUM(p.watched_seconds) / 3600.0 AS hours
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.user_id = ? AND p.started_at >= ? AND p.started_at < ?
           AND p.watched_seconds > 0
           AND i.genres IS NOT NULL AND i.genres != ''
         GROUP BY i.genres
        """,
        (user_id, *_meze(days)),
    )

    soucty: dict[str, float] = {}
    for row in rows:
        hodin = float(row["hours"] or 0)
        for zanr in str(row["genres"]).split("|"):
            zanr = zanr.strip()
            if zanr:
                soucty[zanr] = soucty.get(zanr, 0.0) + hodin

    if not soucty:
        return []

    serazene = sorted(soucty.items(), key=lambda dvojice: -dvojice[1])
    celkem = sum(soucty.values())

    vysledek = [
        {"label": zanr, "hours": hodin, "value": hodin,
         "percent": hodin / celkem * 100 if celkem else 0.0}
        for zanr, hodin in serazene[:limit]
    ]
    # Zbytek do jednoho radku. Dlouhy seznam zanru, z nichz kazdy ma pul
    # hodiny, nikdo necte - a to hlavni v nem zanikne.
    zbytek = serazene[limit:]
    if zbytek:
        hodin = sum(h for _zanr, h in zbytek)
        vysledek.append({
            "label": _t("Ostatní"), "hours": hodin, "value": hodin,
            "percent": hodin / celkem * 100 if celkem else 0.0,
            "genres": len(zbytek),
        })
    return vysledek


def user_detail(user_id: str, days: int) -> dict[str, Any]:
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        return {}

    return {
        "user": user,
        "totals": db.query_one(
            """
            SELECT COUNT(*) AS plays,
                   COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
                   COUNT(DISTINCT item_id) AS item_count
            FROM playback
            WHERE user_id = ? AND started_at >= ? AND started_at < ? AND watched_seconds > 0
            """,
            (user_id, *_meze(days)),
        ) or {},
        # `item_id` je tu kvuli prokliku do knihovny. U serialu se radky
        # slucuji podle nazvu, takze zadne jedno spravne id neexistuje -
        # vezmeme to nejsledovanejsi, coz je i to, ktere clovek hleda.
        "top_items": _s_prokliknutim(
            db.query_all(
                """
                SELECT COALESCE(series_name, item_name) AS label,
                       SUM(watched_seconds) / 3600.0 AS hours,
                       COUNT(*) AS plays
                FROM playback
                WHERE user_id = ? AND started_at >= ? AND started_at < ?
                  AND watched_seconds > 0
                GROUP BY label
                ORDER BY hours DESC
                LIMIT 10
                """,
                (user_id, *_meze(days)),
            ),
            db.query_all(
                """
                SELECT COALESCE(series_name, item_name) AS label, item_id,
                       SUM(watched_seconds) AS total
                FROM playback
                WHERE user_id = ? AND started_at >= ? AND started_at < ?
                  AND watched_seconds > 0 AND item_id IS NOT NULL
                GROUP BY label, item_id
                ORDER BY total DESC
                """,
                (user_id, *_meze(days)),
            ),
        ),
        "genres": user_genres(user_id, days),
        "devices": db.query_all(
            """
            SELECT COALESCE(device_name, 'nezname') AS label,
                   COALESCE(client, '') AS client,
                   SUM(watched_seconds) / 3600.0 AS hours
            FROM playback
            WHERE user_id = ? AND started_at >= ? AND started_at < ? AND watched_seconds > 0
            GROUP BY label, client
            ORDER BY hours DESC
            LIMIT 10
            """,
            (user_id, *_meze(days)),
        ),
    }


# ---------------------------------------------------------------------------
# Knihovna (technicka cast - to, co umi MediaLyze)
# ---------------------------------------------------------------------------

def tech_coverage() -> dict[str, Any]:
    """Kolik polozek ma technicka data a odkud pochazeji."""
    return db.query_one(
        """
        SELECT COUNT(*) AS total,
               -- Velikost vsech knihoven dohromady. COALESCE proto, ze
               -- u polozky bez technickych dat je NULL - a NULL by celym
               -- souctem propadl az na prazdno.
               SUM(COALESCE(size_bytes, 0)) AS size_bytes,
               SUM(CASE WHEN tech_source = 'ffprobe'  THEN 1 ELSE 0 END) AS from_ffprobe,
               SUM(CASE WHEN tech_source = 'jellyfin' THEN 1 ELSE 0 END) AS from_jellyfin,
               SUM(CASE WHEN tech_source IS NULL      THEN 1 ELSE 0 END) AS missing,
               SUM(CASE WHEN tech_error IS NOT NULL   THEN 1 ELSE 0 END) AS errors
        FROM items
        WHERE is_missing = 0
        """
    ) or {}


def codec_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0 AND video_codec IS NOT NULL"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    return db.query_all(
        f"""
        SELECT UPPER(video_codec)               AS label,
               COUNT(*)                         AS item_count,
               COALESCE(SUM(size_bytes), 0)     AS size_bytes
        FROM items {where}
        GROUP BY UPPER(video_codec)
        ORDER BY item_count DESC
        """,
        tuple(params),
    )


def resolution_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    rows = db.query_all(
        f"""
        SELECT {RESOLUTION_CASE} AS label,
               COUNT(*) AS item_count,
               COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM items {where}
        GROUP BY label
        """,
        tuple(params),
    )
    # Seradime podle sve vlastni logiky (4K, 1080p, ...), ne abecedne.
    order = {label: index for index, label in enumerate(RESOLUTION_ORDER)}
    rows.sort(key=lambda row: order.get(row["label"], 99))
    return rows


# Dynamicky rozsah tak, jak ho ukazujeme.
#
# Dolby Vision je POZITIVNI NALEZ: kdyz ho ohlasi kterykoli ze zdroju,
# plati. Zadny z nich ho totiz nehlasi omylem - zato ho oba umi minout.
# Konkretne ffprobe pred verzi 5 neumi cist DV z Matrosky, takze o filmu,
# ktery Jellyfin popisuje jako "Dolby Vision Profile 8.1", rekne jen
# "HDR" - a treti sloupec prehledu by zustal prazdny, prestoze knihovna
# DV obsahuje.
#
# Jinak plati zmereny udaj; kdyz chybi, bere se hlaseny.
ROZSAH_CASE = """
    CASE WHEN video_range = 'DOVI' OR video_range_reported = 'DOVI' THEN 'DOVI'
         ELSE COALESCE(video_range, video_range_reported) END
"""


def rozsah_polozky(polozka: dict[str, Any] | None) -> str | None:
    """Totez pro jednu polozku - pro detail, kde neni SQL po ruce."""
    if not polozka:
        return None
    zmereny = (polozka.get("video_range") or "").strip().upper()
    hlaseny = (polozka.get("video_range_reported") or "").strip().upper()
    if "DOVI" in (zmereny, hlaseny):
        return "DOVI"
    return zmereny or hlaseny or None


def video_range_breakdown() -> list[dict[str, Any]]:
    radky = db.query_all(
        f"""
        SELECT {ROZSAH_CASE} AS rozsah,
               COUNT(*) AS item_count
        FROM items
        WHERE is_missing = 0 AND tech_source IS NOT NULL
        GROUP BY rozsah
        ORDER BY item_count DESC
        """
    )
    # Vic zapisu muze skoncit pod jednim popiskem (prazdny retezec i NULL
    # jsou obojí "neznamé"), takze se secte, co k sobe patri - jinak by
    # graf mel dva stejne pojmenovane sloupce.
    souhrn: dict[str, int] = {}
    for radek in radky:
        popis = formatting.video_range_human(radek["rozsah"])
        souhrn[popis] = souhrn.get(popis, 0) + int(radek["item_count"] or 0)
    return [{"label": popis, "item_count": pocet}
            for popis, pocet in sorted(souhrn.items(), key=lambda d: -d[1])]


# ---------------------------------------------------------------------------
# Knihovna po titulech, ne po dilech
# ---------------------------------------------------------------------------
#
# V tabulce `items` je kazda epizoda samostatny radek - tak je posila
# Jellyfin a tak je potrebujeme pro statistiky. V seznamu knihovny je to
# ale k nicemu: serial o peti radach zabere sto radku a mezi nimi zanikne
# vsechno ostatni.
#
# Seskupujeme proto podle serialu. Klic skupiny je `COALESCE(series_id, id)`,
# takze film (ktery series_id nema) tvori skupinu sam za sebe a stejny dotaz
# obslouzi obojí - zadne dva dotazy, ktere by se casem rozesly.
_SKUPINA = "COALESCE(i.series_id, i.id)"

# Sloupce skupiny. U filmu je skupina jednoprvkova, takze SUM i MAX vraci
# proste jeho vlastni hodnotu - proto nemusime rozlisovat.
_SKUPINA_SLOUPCE = f"""
        {_SKUPINA}                                   AS id,
        MAX(CASE WHEN i.series_id IS NULL THEN 0 ELSE 1 END) AS is_series,
        MAX(COALESCE(i.series_name, i.name))         AS name,
        MAX(i.type)                                  AS type,
        MAX(i.library_id)                            AS library_id,
        COUNT(*)                                     AS episode_count,
        COUNT(DISTINCT i.parent_index_number)        AS season_count,
        COALESCE(SUM(i.size_bytes), 0)               AS size_bytes,
        COALESCE(SUM(i.runtime_ticks), 0)            AS runtime_ticks,
        MAX(i.width)                                 AS width,
        MAX(i.height)                                AS height,
        MAX(i.bitrate)                               AS bitrate,
        MAX(i.video_codec)                           AS video_codec,
        MAX(i.production_year)                       AS production_year,
        MAX(i.date_created)                          AS date_created,
        MAX(i.tech_source)                           AS tech_source,
        -- Skupina je bud cela ziva, nebo cela archivovana - filtr je
        -- ve WHERE. Sablona to potrebuje kvuli znacce "Archivovano".
        MAX(i.is_missing)                            AS is_missing,
        COALESCE(SUM(COALESCE(p.plays, 0)), 0)       AS plays
"""

# Prehrani si spocitame jednim dotazem dopredu a pripojime. Poddotaz na
# kazdy radek by u seskupeni nesel pouzit a u velke knihovny by stejne
# znamenal tisice dotazu navic.
_PREHRANI = """
    LEFT JOIN (SELECT item_id, COUNT(*) AS plays
                 FROM playback WHERE watched_seconds > 60
                GROUP BY item_id) p ON p.item_id = i.id
"""


def _knihovna_filtr(library_id: str | None, search: str | None,
                    archived: bool) -> tuple[list[str], list[Any]]:
    where = ["i.is_missing = 1" if archived else "i.is_missing = 0"]
    params: list[Any] = []
    if library_id:
        where.append("i.library_id = ?")
        params.append(library_id)
    if search:
        where.append("(i.name LIKE ? OR i.series_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    return where, params


def library_rows(limit: int, offset: int, library_id: str | None = None,
                 search: str | None = None, sort: str = "size",
                 archived: bool = False) -> list[dict[str, Any]]:
    """Seznam knihovny: filmy jednotlive, serialy jako jeden radek."""
    where, params = _knihovna_filtr(library_id, search, archived)

    # Razeni se do SQL nesmi vlepit z uzivatelskeho vstupu primo. Pevny
    # seznam moznosti a bereme z nej.
    sort_options = {
        "size": "size_bytes DESC",
        "bitrate": "bitrate DESC",
        "name": "name ASC",
        "plays": "plays DESC",
        "resolution": "height DESC",
    }
    order_by = sort_options.get(sort, sort_options["size"])

    params.extend([limit, offset])
    return db.query_all(
        f"""
        SELECT {_SKUPINA_SLOUPCE}
        FROM items i
        {_PREHRANI}
        WHERE {' AND '.join(where)}
        GROUP BY {_SKUPINA}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def library_rows_count(library_id: str | None = None, search: str | None = None,
                       archived: bool = False) -> int:
    """Kolik radku seznam ma - tedy skupin, ne polozek."""
    where, params = _knihovna_filtr(library_id, search, archived)
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM (
            SELECT {_SKUPINA} AS id FROM items i
             WHERE {' AND '.join(where)}
             GROUP BY {_SKUPINA}
        ) skupiny
        """,
        tuple(params),
    ))


def series_detail(series_id: str) -> dict[str, Any]:
    """Serial rozdeleny na rady a v nich dily.

    Vraci prazdny slovnik, kdyz takovy serial neznáme - routa z toho
    udela 404.

    **Archivovane dily se nepocitaji mezi zive.** Archiv je seznam
    souboru, ktere v Jellyfinu uz nejsou - historie prehravani u nich
    zustava, ale titul sam zmizel. Drive je detail serialu vypisoval
    vedle zivych bez rozdilu, zatimco seznam knihovny je vynechaval;
    serial pak v Jellyfinu ukazoval sto dilu a tady sto dvacet, aniz
    by z toho slo poznat proc.

    Vypisujeme je proto zvlast a rekneme, kolik jich je.
    """
    vsechny = db.query_all(
        f"""
        SELECT i.*, l.name AS library_name, COALESCE(p.plays, 0) AS plays
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        {_PREHRANI}
        WHERE i.series_id = ?
        ORDER BY i.parent_index_number, i.index_number, i.name
        """,
        (series_id,),
    )
    if not vsechny:
        return {}

    dily = [d for d in vsechny if not d["is_missing"]]
    archivovane = [d for d in vsechny if d["is_missing"]]

    # Serial, ze ktereho zbyl uz jen archiv, porad ma smysl ukazat -
    # jinak by odkaz z historie skoncil na 404.
    if not dily:
        dily, archivovane = archivovane, []

    rady: list[dict[str, Any]] = []
    podle_rady: dict[Any, dict[str, Any]] = {}
    for dil in dily:
        cislo = dil["parent_index_number"]
        rada = podle_rady.get(cislo)
        if rada is None:
            rada = {
                "number": cislo,
                # Nazev rady mame jen u nekterych polozek; kdyz chybi,
                # slozime ho z cisla.
                "name": dil["season_name"] or (
                    f"{_t('Řada')} {cislo}" if cislo is not None else _t("Ostatní")),
                "episodes": [],
                "size_bytes": 0,
                "plays": 0,
            }
            podle_rady[cislo] = rada
            rady.append(rada)
        rada["episodes"].append(dil)
        rada["size_bytes"] += int(dil["size_bytes"] or 0)
        rada["plays"] += int(dil["plays"] or 0)

    prvni = dily[0]
    return {
        "id": series_id,
        "name": prvni["series_name"] or prvni["name"],
        # Otisk plakatu serialu. Jde do adresy obrazku, takze po zmene
        # plakatu v Jellyfinu se nacte nova adresa - a prohlizec nemuze
        # podstrcit tu svou uschovanou kopii.
        "image_tag": prvni["series_image_tag"],
        "library_id": prvni["library_id"],
        "seasons": rady,
        "episode_count": len(dily),
        "season_count": len(rady),
        "size_bytes": sum(int(d["size_bytes"] or 0) for d in dily),
        "runtime_ticks": sum(int(d["runtime_ticks"] or 0) for d in dily),
        "plays": sum(int(d["plays"] or 0) for d in dily),
        "missing": all(d["is_missing"] for d in dily),
        # Díly, které v Jellyfinu už nejsou. Vypisují se zvlášť, ať je
        # z rozdílu proti Jellyfinu hned jasné, čím je způsobený.
        "archived": archivovane,
        "archived_count": len(archivovane),
        # Souhrn, ktery se u serialu ukazuje nahore - totez, co u filmu.
        "library_name": prvni["library_name"],
        "path": _spolecna_slozka(d["path"] for d in dily),
        "pridano": min((d["date_created"] for d in dily if d["date_created"]),
                       default=None),
        "posledni_dil": max((d["date_created"] for d in dily if d["date_created"]),
                            default=None),
        "zmereno": max((d["tech_updated_at"] for d in dily
                        if "tech_updated_at" in d.keys() and d["tech_updated_at"]),
                       default=None),
    }


def _spolecna_slozka(cesty: Any) -> str:
    """Slozka, ve ktere serial lezi - spolecny zacatek cest jeho dilu.

    U filmu se ukazuje cesta k souboru. Serial zadny jeden soubor nema,
    takze se ukazuje slozka: vezme se nejdelsi spolecny zacatek cest
    vsech dilu a urizne se na posledni oddelovac. U bezne slozene
    knihovny z toho vyjde slozka serialu.
    """
    seznam = [str(c) for c in cesty if c]
    if not seznam:
        return ""
    spolecne = seznam[0]
    for cesta in seznam[1:]:
        # Znak po znaku, dokud si odpovidaji.
        i = 0
        while i < min(len(spolecne), len(cesta)) and spolecne[i] == cesta[i]:
            i += 1
        spolecne = spolecne[:i]
        if not spolecne:
            return ""
    rez = max(spolecne.rfind("/"), spolecne.rfind("\\"))
    return spolecne[:rez] if rez > 0 else spolecne


def library_cards() -> list[dict[str, Any]]:
    """Knihovny s cisly pro uvodni dlazdice.

    `poster_id` je polozka, jejiz obrazek se pouzije jako pozadi dlazdice.
    Knihovny samotne casto vlastni obrazek nemaji, tak vezmeme nejvetsi
    titul uvnitr - ten byva i vizualne vyrazny.

    U serialove knihovny bereme **serial**, ne epizodu. Nejvetsi polozka
    v takove knihovne je vzdycky nejaky dil - a epizoda v Jellyfinu
    obvykle zadny backdrop nema, ten patri serialu. Dlazdice serialu proto
    zustavala seda: obrazek se nenacetl a `onerror` ho odstranil.
    """
    rows = db.query_all(
        """
        SELECT l.id,
               l.name,
               l.collection_type,
               COUNT(i.id)                    AS item_count,
               COALESCE(SUM(i.size_bytes), 0) AS size_bytes,
               COALESCE(SUM(i.runtime_ticks), 0) / 36000000000.0 AS hours,
               (SELECT COALESCE(s.series_id, s.id) FROM items s
                 WHERE s.library_id = l.id AND s.is_missing = 0
                 ORDER BY s.size_bytes DESC LIMIT 1) AS poster_id
        FROM libraries l
        LEFT JOIN items i ON i.library_id = l.id AND i.is_missing = 0
        GROUP BY l.id, l.name, l.collection_type
        ORDER BY item_count DESC, l.name
        """
    )
    type_names = {
        "movies": _t("Filmy"),
        "tvshows": _t("Seriály"),
        "homevideos": _t("Domácí videa"),
        "mixed": _t("Smíšený obsah"),
    }
    for row in rows:
        row["type_label"] = type_names.get(row["collection_type"], _t("Ostatní"))
    return rows


def library(library_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM libraries WHERE id = ?", (library_id,))


def library_overview(library_id: str) -> dict[str, Any]:
    """Souhrnna cisla jedne knihovny."""
    return db.query_one(
        f"""
        SELECT COUNT(*)                       AS item_count,
               COALESCE(SUM(size_bytes), 0)   AS size_bytes,
               COALESCE(SUM(runtime_ticks), 0) / 36000000000.0 AS hours,
               COALESCE(AVG(bitrate), 0)      AS avg_bitrate,
               SUM(CASE WHEN {RESOLUTION_CASE} = '4K' THEN 1 ELSE 0 END) AS uhd_count,
               SUM(CASE WHEN {ROZSAH_CASE} IN ('HDR', 'DOVI') THEN 1 ELSE 0 END) AS hdr_count,
               SUM(CASE WHEN tech_source IS NULL THEN 1 ELSE 0 END) AS without_tech
        FROM items
        WHERE is_missing = 0 AND library_id = ?
        """,
        (library_id,),
    ) or {}


def library_activity(library_id: str, days: int = 90) -> dict[str, Any]:
    """Co se v teto knihovne sledovalo."""
    return {
        "totals": db.query_one(
            """
            SELECT COUNT(*) AS plays,
                   COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
                   COUNT(DISTINCT user_id) AS users,
                   COUNT(DISTINCT item_id) AS item_count
            FROM playback
            WHERE library_id = ? AND started_at >= ? AND started_at < ?
              AND watched_seconds > 0
            """,
            (library_id, *_meze(days)),
        ) or {},
        "top_items": db.query_all(
            """
            SELECT COALESCE(series_name, item_name) AS label,
                   SUM(watched_seconds) / 3600.0 AS hours,
                   COUNT(*) AS plays
            FROM playback
            WHERE library_id = ? AND started_at >= ? AND started_at < ?
              AND watched_seconds > 0
            GROUP BY label ORDER BY hours DESC LIMIT 10
            """,
            (library_id, *_meze(days)),
        ),
        "top_users": db.query_all(
            """
            SELECT user_name AS label, SUM(watched_seconds) / 3600.0 AS hours
            FROM playback
            WHERE library_id = ? AND started_at >= ? AND started_at < ?
              AND watched_seconds > 0 AND user_name IS NOT NULL
            GROUP BY label ORDER BY hours DESC LIMIT 10
            """,
            (library_id, *_meze(days)),
        ),
        "recent": db.query_all(
            """
            SELECT p.*, i.height
            FROM playback p
            LEFT JOIN items i ON i.id = p.item_id
            WHERE p.library_id = ? AND p.watched_seconds > 0
            ORDER BY p.started_at DESC LIMIT 25
            """,
            (library_id,),
        ),
    }


# ---------------------------------------------------------------------------
# Detail jedne polozky
# ---------------------------------------------------------------------------

def item(item_id: str) -> dict[str, Any] | None:
    return db.query_one(
        """
        SELECT i.*, l.name AS library_name, l.collection_type
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.id = ?
        """,
        (item_id,),
    )


def item_streams(item_id: str) -> dict[str, list[dict[str, Any]]]:
    """Stopy polozky rozdelene na video, zvuk a titulky."""
    rows = db.query_all(
        "SELECT * FROM item_streams WHERE item_id = ? ORDER BY type, stream_index",
        (item_id,),
    )
    grouped: dict[str, list[dict[str, Any]]] = {"Video": [], "Audio": [], "Subtitle": []}
    for row in rows:
        grouped.setdefault(row["type"], []).append(row)
    return grouped


def item_playback(item_id: str, limit: int = 25) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT * FROM playback
        WHERE item_id = ? AND watched_seconds > 0
        ORDER BY started_at DESC LIMIT ?
        """,
        (item_id, limit),
    )


def item_playback_summary(item_id: str) -> dict[str, Any]:
    return db.query_one(
        """
        SELECT COUNT(*) AS plays,
               COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
               COUNT(DISTINCT user_id) AS users,
               MAX(started_at) AS last_played
        FROM playback
        WHERE item_id = ? AND watched_seconds > 0
        """,
        (item_id,),
    ) or {}


def sibling_episodes(item_row: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    """Ostatni epizody tehoz serialu - navigace v detailu epizody."""
    if not item_row.get("series_id"):
        return []
    return db.query_all(
        """
        SELECT id, name, index_number, parent_index_number, size_bytes, height
        FROM items
        WHERE series_id = ? AND is_missing = 0
        ORDER BY parent_index_number, index_number
        LIMIT ?
        """,
        (item_row["series_id"], limit),
    )
def archived_count(library_id: str | None = None) -> int:
    """Kolik polozek je v archivu. Pouziva se na odznak u prepinace."""
    where = ["is_missing = 1"]
    params: list[Any] = []
    if library_id:
        where.append("library_id = ?")
        params.append(library_id)
    return int(db.query_value(
        f"SELECT COUNT(*) FROM items WHERE {' AND '.join(where)}", tuple(params)
    ))


def delete_item(item_id: str) -> dict[str, Any]:
    """Nenavratne smaze polozku i jeji historii prehravani.

    Tohle je jedina cesta, jak z Jellyscope neco doopravdy zmizi, a je
    zamerne jen rucni. Automaticky se polozky vzdycky jen archivuji -
    kdyz Jellyfin na chvili vypadne nebo se prepoji uloziste, prisel bys
    jinak o historii kvuli docasnemu vypadku.

    Vraci, co se smazalo, aby slo uzivateli rict, o co prisel.
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, series_name FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return {"status": "error", "message": "Položka neexistuje."}

        plays = int(conn.execute(
            "SELECT COUNT(*) AS c FROM playback WHERE item_id = ?", (item_id,)
        ).fetchone()["c"])

        # Stopy zmizi samy pres ON DELETE CASCADE, historii mazeme rucne -
        # cizi klic tam schvalne neni, aby vypadek Jellyfinu nikdy
        # nemohl smazat statistiky.
        conn.execute("DELETE FROM playback WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM item_streams WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

    return {
        "status": "ok",
        "name": row["name"],
        "series_name": row["series_name"],
        "plays": plays,
    }

# ---------------------------------------------------------------------------
# Sit - kolik toho teklo k prehravacum
#
# Cislo, se kterym se tu pracuje, je `playback.bitrate`: tok toho streamu
# v bitech za sekundu. Sberac ho bere z relace - kdyz server prekoduje,
# je to vysledny (prepocitany) tok, jinak bitrate zdrojoveho souboru.
#
# Je to **odhad podle deklarovaneho bitrate, ne mereni dratu.** Preskakovani
# v prehravaci a buffer znamenaji, ze skutecne prenesene bajty se lisi.
# Presne cislo umi jen reverzni proxy nebo pocitadla systemu - mimo
# Jellyfin. Rika to i stranka, at si to nikdo neplete s merenim.
#
# Pauzy se ale odecitaji: pocita se `watched_seconds`, tedy cas, kdy se
# doopravdy sledovalo. Pozastavene prehravani zadna data netahne, takze
# by ho graf jinak ukazoval jako beziciho - viz bandwidth_prubeh().
# ---------------------------------------------------------------------------

def _sitove_radky(days: int) -> list[dict[str, Any]]:
    """Prehravani s known bitrate za obdobi. Zaklad pro vsechno ostatni."""
    return db.query_all(
        """
        SELECT started_at, ended_at, last_seen_at, watched_seconds, bitrate,
               user_name, client, device_name, remote_address, play_method
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
           AND bitrate IS NOT NULL AND bitrate > 0
         ORDER BY started_at
        """,
        (*_meze(days),),
    )


def _sekundy(text: Any) -> float | None:
    """Cas z databaze na sekundy. None, kdyz se neda precist.

    `replace(tzinfo=utc)` neni kosmetika. V databazi je cas v UTC a bez
    zony; `datetime.timestamp()` ale takovy cas povazuje za MISTNI, takze
    vysledek byl posunuty o cely offset. Popisky pod grafem pak ukazovaly
    UTC, zatimco zbytek aplikace mistni cas - vecerni spicka tak v lete
    "nastavala" o hodinu driv, nez ve skutecnosti byla.

    Zbytek aplikace to dela stejne, viz formatting._parse_any().
    """
    try:
        cas = datetime.strptime(str(text).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return None
    return cas.replace(tzinfo=timezone.utc).timestamp()


def bandwidth_prubeh(days: int, bodu: int = 120) -> list[dict[str, Any]]:
    """Soubezny tok v case - kolik Mbit/s teklo ze serveru zaroven.

    Pocita se **prochazenim udalosti**, ne vzorkovanim: kazde prehravani
    prida svuj bitrate na zacatku a ubere ho na konci. Serazenim udalosti
    podle casu vznikne presna krivka souběžneho toku, at uz prehravani
    trvalo minutu nebo pet hodin.

    Do grafu se z ni bere maximum v kazdem useku - prumer by spicky
    zahladil, a prave spicka je to, co zajima: podle ni se dimenzuje
    linka.
    """
    radky = _sitove_radky(days)
    if not radky:
        return []

    udalosti: list[tuple[float, int]] = []
    for radek in radky:
        zacatek = _sekundy(radek["started_at"])
        konec = _sekundy(radek["ended_at"] or radek["last_seen_at"])
        if zacatek is None:
            continue
        if konec is None or konec <= zacatek:
            konec = zacatek + float(radek["watched_seconds"] or 0)
        # Pauza neteče. Bez tohohle omezení se bitrate rozprostřel přes
        # celý čas od začátku do konce - takže film pozastavený přes noc
        # držel v grafu plný tok do rána a stejně tak přehrávání, které
        # je pozastavené PRÁVĚ TEĎ (u něj `last_seen_at` běží dál).
        #
        # Kdy přesně se pauzovalo, databáze neví - ukládá se jen součet
        # `watched_seconds`. Bereme proto délku toku podle něj: tok trvá
        # tak dlouho, jak dlouho se doopravdy sledovalo. U pauzy uprostřed
        # je tím pádem posunutý dopředu, ale jeho **množství i výška**
        # sedí - a součet pod křivkou tak konečně odpovídá objemu dat,
        # který stránka ukazuje vedle.
        odsledovano = float(radek["watched_seconds"] or 0)
        if odsledovano > 0:
            konec = min(konec, zacatek + odsledovano)

        udalosti.append((zacatek, int(radek["bitrate"])))
        udalosti.append((konec, -int(radek["bitrate"])))

    if not udalosti:
        return []
    udalosti.sort()

    od, do = udalosti[0][0], udalosti[-1][0]
    krok = max((do - od) / max(1, bodu), 1.0)

    body: list[dict[str, Any]] = []
    soucet = 0
    hranice = od + krok
    vrchol = 0
    for cas, zmena in udalosti:
        while cas > hranice:
            body.append({"cas": hranice - krok / 2, "mbit": round(vrchol / 1e6, 2)})
            hranice += krok
            vrchol = soucet
        soucet += zmena
        vrchol = max(vrchol, soucet)
    body.append({"cas": hranice - krok / 2, "mbit": round(vrchol / 1e6, 2)})

    for bod in body:
        # Tataz zona jako u vypisu casu jinde - viz formatting.zona().
        # Osa grafu a sloupec "kdy" v historii musi rikat totez, jinak
        # clovek hleda tentyz zaznam na dvou ruznych casech.
        bod["popisek"] = datetime.fromtimestamp(
            bod["cas"], formatting.zona()).strftime("%d.%m. %H:%M")
        # Jak dlouhy usek jeden bod zastupuje. Grafu se lidi casto ptaji
        # "jak casto se to meri" - nemeri se vubec, prochazeji se zacatky
        # a konce prehravani a z kazdeho useku se bere spicka. Bez tohohle
        # udaje vypada krivka nahodne; s nim je videt, ze jeden vrchol
        # muze byt jedina hodina z sesti.
        bod["krok_minut"] = round(krok / 60)
    return body


def tok_ted() -> dict[str, Any]:
    """Kolik tece ze serveru PRAVE TED - z bezicich prehravani.

    Na rozdil od krivky niz se tady nic nedopocitava z historie: bere se
    to, co sberac videl pri poslednim dotazu. Pozastavena prehravani se
    nepocitaji, protoze pri pauze nic netece - ale pocitaji se zvlast,
    aby bylo videt, ze tam jsou.
    """
    radky = db.query_all(
        "SELECT bitrate, is_paused, play_method, user_name, item_name "
        "FROM playback WHERE is_active = 1")

    tekouci = [r for r in radky if not r["is_paused"] and (r["bitrate"] or 0) > 0]
    return {
        "mbit": round(sum(int(r["bitrate"]) for r in tekouci) / 1e6, 2),
        "streamu": len(tekouci),
        "pozastavenych": sum(1 for r in radky if r["is_paused"]),
        "bez_toku": sum(1 for r in radky
                        if not r["is_paused"] and not (r["bitrate"] or 0)),
        "prepoctu": sum(1 for r in tekouci
                        if str(r["play_method"] or "").lower().startswith("transcode")),
    }


# Nejkratsi okno zive krivky. Pod jeden den nema smysl klesat: hodinovy
# vyrez rekne "prave nic netece" i vecer, kdy se hralo celou dobu, jen
# zrovna dobehl posledni dil.
NEJKRATSI_ZIVE_OKNO = 24 * 3600


# Jak dlouhy usek zastupuje jeden bod zive krivky a kolik bodu nejvic.
#
# 180 bodu pres tyden znamenalo jeden bod na 56 minut - a protoze se
# z useku bere spicka, desetiminutovy stream se v grafu roztahl na
# hodinu. Peti minutam uz odpovida tvar krivky tomu, co se doopravdy
# delo; strop je kvuli velikosti obrazku.
USEK_ZIVE_SEKUND = 300
# Strop bodu. Puvodne 1500, jenze u tydne z toho byla srst: spicka jeden
# pixel siroka, kterou mys netrefi. Sest set bodu znamena u dne porad
# petiminutovy krok a u tydne ctvrthodinovy - to uz je videt jako sloupec,
# ne jako vlas. Delsi obdobi patri karte "Spicka po dnech".
NEJVIC_BODU_ZIVE = 600


def bandwidth_zive(days: Any = None, bodu: int | None = None) -> list[dict[str, Any]]:
    """Tok v case pro zive okno - podle zvoleneho obdobi, nejmene 24 hodin.

    Proc zvlast a ne jen jiny rozsah te krivky niz: ta bere prehravani
    podle toho, kdy ZACALA, takze stream spusteny predevcirem by do okna
    vubec nespadl, prestoze prave tece. Tady se hledaji prehravani, ktera
    se s oknem **prekryvaji**.

    `OR is_active = 1` v podmince neni pojistka navic. Bezici prehravani
    tece i tehdy, kdyz se sberac naposledy ozval pred delsi dobou - treba
    protoze Jellyfin chvili neodpovidal. Bez toho by krivka spadla na nulu,
    zatimco cislo vedle ni by ukazovalo plny tok.
    """
    ted = datetime.now(timezone.utc).timestamp()

    # Okno se ridi filtrem stranky. Cislo vedle krivky je porad "ted",
    # ale krivka ukazuje to, co si clovek vybral nahore - jinak by filtr
    # na te karte nedelal nic a pusobil rozbite.
    okno = NEJKRATSI_ZIVE_OKNO
    do_okna = ted
    if days is not None:
        obdobi = _obdobi(days)
        zacatek = _sekundy(obdobi.od)
        konec = min(_sekundy(obdobi.do) or ted, ted)
        if zacatek is not None and konec > zacatek:
            if obdobi.relativni:
                # "Poslednich N dni" konci ted a pod den se neklesa - viz
                # NEJKRATSI_ZIVE_OKNO.
                okno = max(NEJKRATSI_ZIVE_OKNO, konec - zacatek)
            else:
                # Pevne obdobi (vyklikane, nebo vybrane tazenim v grafu)
                # se bere PRESNE tak, jak zni - vcetne konce v minulosti.
                # Kdyby krivka porad koncila "ted", ukazovala by neco
                # jineho, nez rika prepinac nad ni - a druhy tah by pak
                # vybral uplne jiny cas, nez na ktery clovek ukazuje.
                okno, do_okna = konec - zacatek, konec
    od = do_okna - okno

    # Rozliseni podle delky okna, ne pevny pocet bodu.
    if bodu is None:
        bodu = int(min(NEJVIC_BODU_ZIVE, max(60, okno // USEK_ZIVE_SEKUND)))

    radky = db.query_all(
        """
        SELECT started_at, ended_at, last_seen_at, watched_seconds, bitrate,
               is_paused, is_active
          FROM playback
         WHERE bitrate IS NOT NULL AND bitrate > 0
           AND watched_seconds > 0
           AND (COALESCE(ended_at, last_seen_at) >= ? OR is_active = 1)
         ORDER BY started_at
        """,
        (datetime.fromtimestamp(od, timezone.utc).strftime(db.TIME_FORMAT),),
    )

    udalosti: list[tuple[float, int]] = []
    for radek in radky:
        zacatek = _sekundy(radek["started_at"])
        if zacatek is None:
            continue
        konec = _sekundy(radek["ended_at"] or radek["last_seen_at"]) or ted
        # Pozastavene prave ted netece - konec dame tam, kde se naposledy
        # hralo. U ostatnich plati tataz uvaha jako u dlouhe krivky:
        # tok trva tak dlouho, jak dlouho se doopravdy sledovalo.
        odsledovano = float(radek["watched_seconds"] or 0)
        if odsledovano > 0:
            konec = min(konec, zacatek + odsledovano)
        if radek["is_active"] and not radek["is_paused"]:
            # Bezici stream tece i ted, i kdyz se sberac ozval pred chvili.
            konec = max(konec, ted)
        if konec <= od:
            continue
        udalosti.append((max(zacatek, od), int(radek["bitrate"])))
        udalosti.append((konec, -int(radek["bitrate"])))

    krok = (do_okna - od) / max(1, bodu)
    body: list[dict[str, Any]] = []
    udalosti.sort()
    soucet = 0
    kolik = 0            # kolik streamu tece zaroven
    index = 0
    for i in range(bodu):
        hranice = od + (i + 1) * krok
        vrchol = soucet
        vrchol_kolik = kolik
        while index < len(udalosti) and udalosti[index][0] <= hranice:
            soucet += udalosti[index][1]
            kolik += 1 if udalosti[index][1] > 0 else -1
            if soucet > vrchol:
                vrchol = soucet
                vrchol_kolik = kolik
            index += 1
        stred = hranice - krok / 2
        # U kratkeho okna staci hodina a minuta; jakmile krivka prekroci
        # dva dny, musi byt videt i datum - jinak se streda a ctvrtek na
        # ose nedaji rozlisit.
        tvar = "%H:%M" if okno <= 48 * 3600 else "%d.%m. %H:%M"
        body.append({
            "cas": stred,
            "mbit": round(max(0, vrchol) / 1e6, 2),
            # Kolik streamu ten vrchol tvorilo. V bubline to odpovida na
            # druhou otazku, kterou clovek u spicky ma: bylo to hodne lidi,
            # nebo jeden film ve 4K?
            "streamu": max(0, vrchol_kolik),
            "popisek": datetime.fromtimestamp(stred, formatting.zona()).strftime(tvar),
            "krok_minut": round(krok / 60) or 1,
        })
    return body


def bandwidth_denni_spicky(days: int) -> list[dict[str, Any]]:
    """Nejvyssi soubezny tok kazdeho dne.

    Pres delsi obdobi nema smysl kreslit kazdou minutu - bodu by bylo
    vic nez pixelu a krivka by vypadala jako sum. Jeden den = jedno
    cislo, a to nejvyssi: podle spicky se dimenzuje linka, prumer by ji
    zahladil.
    """
    radky = _sitove_radky(days)
    if not radky:
        return []

    udalosti: list[tuple[float, int]] = []
    for radek in radky:
        zacatek = _sekundy(radek["started_at"])
        konec = _sekundy(radek["ended_at"] or radek["last_seen_at"])
        if zacatek is None:
            continue
        if konec is None or konec <= zacatek:
            konec = zacatek + float(radek["watched_seconds"] or 0)
        odsledovano = float(radek["watched_seconds"] or 0)
        if odsledovano > 0:
            konec = min(konec, zacatek + odsledovano)
        udalosti.append((zacatek, int(radek["bitrate"])))
        udalosti.append((konec, -int(radek["bitrate"])))

    if not udalosti:
        return []
    udalosti.sort()

    # Prochazime udalosti a u kazdeho dne si pamatujeme nejvyssi soucet.
    # Den se urcuje v ZONE APLIKACE - jinak by spicka z pulnoci padla do
    # jineho dne, nez ve kterem ji clovek zazil.
    zona = formatting.zona()
    spicky: dict[str, float] = {}
    soucet = 0
    for cas, zmena in udalosti:
        soucet += zmena
        den = datetime.fromtimestamp(cas, zona).strftime("%Y-%m-%d")
        spicky[den] = max(spicky.get(den, 0), soucet)

    return [{"den": den,
             "popisek": datetime.strptime(den, "%Y-%m-%d").strftime("%d.%m."),
             "mbit": round(hodnota / 1e6, 2)}
            for den, hodnota in sorted(spicky.items())]


def bandwidth_prehled(days: int) -> dict[str, Any]:
    """Spicka, objem dat a podil transcode. Cisla nad grafem."""
    radky = _sitove_radky(days)
    prubeh = bandwidth_prubeh(days, bodu=600)

    bajtu = 0.0
    bajtu_transcode = 0.0
    for radek in radky:
        objem = float(radek["bitrate"]) * float(radek["watched_seconds"] or 0) / 8.0
        bajtu += objem
        if str(radek["play_method"] or "") == "Transcode":
            bajtu_transcode += objem

    spicka = max(prubeh, key=lambda b: b["mbit"], default=None)
    return {
        "spicka_mbit": spicka["mbit"] if spicka else 0.0,
        "spicka_kdy": spicka["popisek"] if spicka else "",
        "bajtu": int(bajtu),
        "bajtu_transcode": int(bajtu_transcode),
        "podil_transcode": round(bajtu_transcode / bajtu * 100, 1) if bajtu else 0.0,
        "prehravani": len(radky),
        "prumer_mbit": (round(sum(float(r["bitrate"]) for r in radky)
                              / len(radky) / 1e6, 2) if radky else 0.0),
    }


def bandwidth_podle(days: int, sloupec: str, limit: int = 12) -> list[dict[str, Any]]:
    """Objem dat podle uzivatele, klienta nebo zarizeni - od nejvetsiho.

    `sloupec` se porovnava proti pevnemu seznamu, takze se z adresy nikdy
    nedostane nic do SQL.
    """
    povolene = {"user_name", "client", "device_name"}
    if sloupec not in povolene:
        sloupec = "user_name"

    rows = db.query_all(
        f"""
        SELECT COALESCE({sloupec}, '?')                        AS label,
               SUM(watched_seconds * bitrate) / 8.0            AS bajtu,
               MAX(bitrate)                                    AS spicka,
               COUNT(*)                                        AS plays,
               -- Id uzivatele kvuli prokliku na jeho detail. MIN, protoze
               -- PostgreSQL nedovoli sloupec mimo GROUP BY - a v jedne
               -- skupine je stejne pokazde tentyz clovek.
               MIN(user_id)                                    AS user_id
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
           AND bitrate IS NOT NULL AND bitrate > 0
         GROUP BY COALESCE({sloupec}, '?')
         ORDER BY SUM(watched_seconds * bitrate) DESC
         LIMIT ?
        """,
        (*_meze(days), limit),
    )
    # Graf dostava gigabajty, ne bajty: sloupec popsany "98 739 089 379 B"
    # se neda precist, a presnost na bajt tu k nicemu neni.
    return [{"label": r["label"], "bajtu": int(r["bajtu"] or 0),
             "gb": round(float(r["bajtu"] or 0) / 1e9, 1),
             "spicka_mbit": round(float(r["spicka"] or 0) / 1e6, 2),
             "plays": int(r["plays"] or 0),
             # Jen u lidi - proklik na "Chrome" nebo "Shield TV" nikam
             # nevede, takovou stranku nemame.
             "user_id": r["user_id"] if sloupec == "user_name" else None}
            for r in rows]


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


def hotspoty(days: int, limit: int = 60) -> list[dict[str, Any]]:
    """Odkud se divali - misto po miste, od nejaktivnejsiho.

    Umistuji se **jen verejne adresy**: ta z domaci site zadne misto
    neoznacuje. Kdyz databaze GeoLite2 chybi (nebo chybi knihovna, ktera
    ji umi cist), vraci se prazdny seznam a stranka mapu vubec neukaze -
    misto toho, aby ukazala prazdnou.

    Body na temze miste se slucuji: deset lidi z jednoho mesta ma byt
    jedna vetsi tecka, ne deset tecek pres sebe. Zaokrouhlujeme na
    desetinu stupne, coz je zhruba deset kilometru.
    """
    from . import geoip

    if not geoip.je_k_dispozici():
        return []

    mista: dict[tuple[float, float], dict[str, Any]] = {}
    for r in _provoz_podle_adresy(days):
        adresa = str(r["adresa"] or "")
        # Prazdna adresa je import (ten ji nenese), domaci nic neoznacuje.
        if not adresa or je_domaci(adresa):
            continue
        misto = geoip.najdi(adresa)
        if not misto:
            continue
        klic = (round(misto["lat"], 1), round(misto["lon"], 1))
        bod = mista.setdefault(klic, {
            "lat": misto["lat"], "lon": misto["lon"],
            "mesto": misto["mesto"], "zeme": misto["zeme"], "kod": misto["kod"],
            "plays": 0, "lidi": 0, "sekund": 0, "bajtu": 0.0, "adres": 0,
        })
        bod["plays"] += int(r["plays"] or 0)
        bod["lidi"] = max(bod["lidi"], int(r["lidi"] or 0))
        bod["sekund"] += int(r["sekund"] or 0)
        bod["bajtu"] += float(r["bajtu"] or 0)
        bod["adres"] += 1

    body = sorted(mista.values(), key=lambda b: b["sekund"], reverse=True)[:limit]
    for bod in body:
        bod["bajtu"] = int(bod["bajtu"])
        bod["popis"] = ", ".join(x for x in (bod["mesto"], bod["zeme"]) if x) or "?"
    return body


def zeme_divaku(days: int) -> list[dict[str, Any]]:
    """Totez jako hotspoty, jen secteno po zemich - pro tabulku vedle mapy."""
    podle_zeme: dict[str, dict[str, Any]] = {}
    for bod in hotspoty(days, limit=1000):
        zaznam = podle_zeme.setdefault(bod["zeme"] or "?", {
            "label": bod["zeme"] or "?", "kod": bod["kod"],
            "plays": 0, "sekund": 0, "bajtu": 0, "mist": 0,
        })
        zaznam["plays"] += bod["plays"]
        zaznam["sekund"] += bod["sekund"]
        zaznam["bajtu"] += bod["bajtu"]
        zaznam["mist"] += 1
    return sorted(podle_zeme.values(), key=lambda z: z["sekund"], reverse=True)

