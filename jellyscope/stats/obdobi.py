"""Časová okna statistik: období, jeho meze a předchozí období ke srovnání.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Any

from .. import db, formatting


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
    # Cela historie (obdobi_cela_historie): pocita se jako "poslednich N
    # dni" od prvniho prehrani, jen se jinak jmenuje.
    cela_historie: bool = False

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
# "Prakticky vsechno" pro funkce, ktere davaji `limit` do SQL (`LIMIT ?`).
# API filtruje nad celym seznamem, takze ho potrebuje cely; cislo se vejde
# do int64 v SQLite i PostgreSQL i po `limit * 6` v recently_added().
BEZ_LIMITU = 1_000_000_000
# Odkdy muze historie vubec sahat. Jellyfin vznikl v roce 2018, Emby 2013
# - starsi prehrani je chyba importu (Playback Reporting umi poslat
# DateTime.MinValue, tedy 0001-01-01) a o zacatku historie nerozhoduje.
NEJSTARSI_HISTORIE = "2000-01-01 00:00:00"


def obdobi_dnu(days: int) -> Obdobi:
    """Poslednich N dni. Konec je otevreny - viz KONEC_CASU."""
    dny = max(1, int(days))
    zacatek = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=dny)
    return Obdobi(od=zacatek.strftime(db.TIME_FORMAT), do=KONEC_CASU, dny=dny)


def obdobi_cela_historie() -> Obdobi:
    """Celá historie: od dne prvního přehrání do teď, bez časového omezení.

    Počítá se jako „posledních N dní", kde N sahá k prvnímu přehrání:
    konec je otevřený (KONEC_CASU), takže zapamatovaná volba zahrne i to,
    co přibude zítra, a délka, předchozí okno i kalendář grafu fungují
    stejně jako u ostatních období. Bez historie je to dnešek.

    Začátek je prvni_zaznam(), ne holé MIN: nesmyslné datum z importu by
    jinak přeteklo při převodu do zóny a zapamatovaná volba by shodila
    každou stránku.
    """
    zona = formatting.zona()
    dnes = datetime.now(zona).replace(tzinfo=None)
    prvni = prvni_zaznam()
    try:
        zacatek = (datetime.strptime(str(prvni)[:19], db.TIME_FORMAT)
                   .replace(tzinfo=UTC).astimezone(zona).replace(tzinfo=None))
    except (TypeError, ValueError):
        zacatek = dnes
    den = min(zacatek, dnes).replace(hour=0, minute=0, second=0, microsecond=0)
    zitra = dnes.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return Obdobi(
        od=den.replace(tzinfo=zona).astimezone(UTC).strftime(db.TIME_FORMAT),
        do=KONEC_CASU, dny=(dnes.date() - den.date()).days + 1,
        od_mistni=den.strftime(db.TIME_FORMAT), do_mistni=zitra.strftime(db.TIME_FORMAT),
        cela_historie=True)


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
        return cas.replace(tzinfo=zona).astimezone(UTC).strftime(db.TIME_FORMAT)

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
    try:
        return obdobi_od_do(
            datetime.fromtimestamp(zacatek, zona).strftime(tvar),
            datetime.fromtimestamp(konec, zona).strftime(tvar),
        )
    except (OverflowError, OSError, ValueError):
        # Okamzik mimo rozsah kalendare. Chodi z adresy, takze staci
        # podstrcit odkaz s `od_ts=-1e308` a stranka spadne na 500 -
        # a `0` shodi Windows uz na prevodu do mistni zony. Nesmyslne
        # obdobi neni chyba serveru; proste zadne obdobi neni.
        return None


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
        return (cas.replace(tzinfo=UTC).astimezone(zona)
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

    Zaznam starsi nez NEJSTARSI_HISTORIE se nepocita - viz tam.
    """
    return str(db.query_value(
        "SELECT MIN(started_at) FROM playback"
        " WHERE watched_seconds > 0 AND started_at >= ?",
        (NEJSTARSI_HISTORIE,), default="") or "")


def lze_srovnat(zadani: Any) -> bool:
    """Ma predchozi obdobi vubec data, se kterymi jde srovnavat?"""
    zacatek = prvni_zaznam()
    if not zacatek:
        return False
    return predchozi(zadani).od >= zacatek


def je_transcode(sloupec: str = "play_method") -> str:
    """Podminka "tohle je prepocet" - jedno misto pro vsechny dotazy.

    Hleda se podle ZACATKU nazvu, ne presnou shodou. Importovana historie
    (Playback Reporting) nese podrobnejsi hodnoty - treba
    "Transcode (v:h264 a:direct)" - a pri presne shode by propadly.

    Az na tuhle funkci se to pocitalo dvakrat ruzne: graf doruceni je
    podle zacatku uz davno (viz `_role`), kdezto podil transcode presnou
    shodou. Na jedne strance tak mohlo stat "Transcode: 1 h" a hned vedle
    "Podil transcode: 0 %".
    """
    return f"{sloupec} LIKE 'Transcode%'"


def _mistni_den(mistni: str, utc: str, konec: bool = False):
    """Datum meze v zone aplikace.

    Horni mez je vzdycky vylucna (pulnoc dne za poslednim), takze se
    u konce vraci o vterinu zpatky - jinak by kalendar mel o den navic.
    """
    if mistni:
        cas = datetime.strptime(mistni, db.TIME_FORMAT)
    else:
        cas = (datetime.strptime(utc, db.TIME_FORMAT)
               .replace(tzinfo=UTC).astimezone(formatting.zona())
               .replace(tzinfo=None))
    return (cas - timedelta(seconds=1)).date() if konec else cas.date()
