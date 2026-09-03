"""Čtení logů pro rozhraní - a jeden vlastní soubor, aby vždycky nějaký byl.

Správce, který chtěl vědět, proč mu selhala synchronizace, musel dosud na
server přes SSH. Tenhle modul zpřístupní log rovnou v Nastavení.

## Odkud se log bere

Ve složce `data/logs/` bývá to, co zachytil správce procesů:

  * **supervisord** tam píše `out.log` (standardní výstup) a `err.log`
    (chybový) - hlášky logování jdou do druhého jmenovaného,
  * **systemd** tam nepíše nic, ten posílá všechno do journalu.

Prohlížeč proto nabídne všechny soubory, které ve složce najde, a k tomu
`jellyscope.log`, který si aplikace píše sama. Ten je tu právě kvůli
systemd instalacím: bez něj by u nich sekce zůstala prázdná a odkazovala
na `journalctl`, ke kterému se z prohlížeče stejně nedostaneme.

Výpis na standardní výstup zůstává beze změny, takže kdo je zvyklý na
`journalctl -u jellyscope -f`, o nic nepřijde.

## Tajemství se do prohlížeče nedostanou

Log je jediné místo v aplikaci, kde by se mohl objevit API klíč nebo heslo
k databázi - do hlášky o chybě se dostane leccos. Než se řádek pošle do
stránky, projde `_zamaskuj()`, která známé tajné hodnoty nahradí hvězdičkami.
Maskuje se **při čtení**, ne při zápisu: kdyby se maskovalo při zápisu,
musel by se pro každý řádek číst aktuální klíč z databáze - a zápis do logu
při čtení z databáze je cesta k nekonečné smyčce.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import i18n
from .config import BASE_DIR

log = logging.getLogger("jellyscope.applog")

# Jeden soubor, po naplnění se přejmenuje a začne nový. Bez stropu by log
# na dlouho běžícím serveru v tichosti zaplnil disk - a záloha, která se
# nevejde na disk, nadělá víc škody než užitku.
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

# Kolik řádků se dá zobrazit najednou. Strop je tu proto, že soubor může mít
# dva megabajty a poslat je všechny do stránky by ji jen zahltilo.
MAX_LINES = 2000
DEFAULT_LINES = 200

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_handler: RotatingFileHandler | None = None


# Vlastní soubor aplikace. Ostatní (out.log, err.log) zakládá správce
# procesů, ty jen čteme.
OWN_NAME = "jellyscope.log"

# Popisky, ať je z rozhraní poznat, čí který soubor je. Kdo nasazoval
# přes supervisord, jinak neví, proč jsou soubory tři.
POPISKY = {
    OWN_NAME: "zapisuje Jellyscope",
    "out.log": "standardní výstup (supervisord)",
    "err.log": "chybový výstup (supervisord)",
}


def log_dir() -> Path:
    """Složka s logy. Řídí se JELLYSCOPE_HOME jako všechno ostatní."""
    return BASE_DIR / "data" / "logs"


def log_path() -> Path:
    """Soubor, který si píše aplikace sama."""
    return log_dir() / OWN_NAME


def available_files() -> list[dict[str, Any]]:
    """Logy, které ve složce opravdu leží - podklad pro přepínač.

    Vypisujeme jen soubory, ne odrolované zálohy (`out.log.1`): těch bývá
    pět a v přepínači by přebíjely to podstatné.
    """
    slozka = log_dir()
    if not slozka.is_dir():
        return []

    nalezene = []
    try:
        for cesta in sorted(slozka.glob("*.log")):
            try:
                velikost = cesta.stat().st_size
            except OSError:
                continue
            nalezene.append({
                "name": cesta.name,
                "label": POPISKY.get(cesta.name, ""),
                "size_bytes": velikost,
            })
    except OSError:
        return []

    # Vlastní soubor napřed - má nejčitelnější formát a je vždycky po ruce.
    nalezene.sort(key=lambda s: (s["name"] != OWN_NAME, s["name"]))
    return nalezene


def _bezpecna_cesta(name: str) -> Path | None:
    """Přeloží jméno z formuláře na soubor ve složce s logy.

    Jméno přichází z prohlížeče, takže se s ním nesmí zacházet jako
    s cestou - `../../.env` je taky "jméno". Proto se nebere nic než
    holé jméno souboru a výsledek se ještě ověří proti seznamu toho,
    co ve složce doopravdy je.
    """
    if not name:
        return None
    if name != Path(name).name or not name.endswith(".log"):
        return None
    cesta = log_dir() / name
    if not cesta.is_file():
        return None
    return cesta



# ---------------------------------------------------------------------------
# Jazyk logu
# ---------------------------------------------------------------------------
#
# Log je psany cesky, protoze cesky je cely program. Kdo chce anglicky,
# prepne si to v Nastaveni - hlasky se prelozi az pri zapisu do souboru
# (viz i18n.LOG_EN). Volani `log.info(...)` ve zdrojacich zustavaji ceska,
# takze pri psani nove hlasky nikdo nemusi myslet na preklad; neprelozena
# hlaska se proste zapise cesky.
#
# Jazyk se drzi v promenne modulu, ne aby se cetl z databaze u kazdeho
# radku logu: logovat se muze i behem startu (kdy databaze jeste nemusi
# byt otevrena) a uvnitr samotne prace s databazi - a dotaz do databaze
# spustený z logovani je nejkratsi cesta k nekonecne smycce.
_jazyk_logu = "cs"


def nastav_jazyk(kod: str) -> None:
    """Rekne logu, v jakem jazyce se ma zapisovat. Vola se pri startu
    a pri zmene v Nastaveni."""
    global _jazyk_logu
    _jazyk_logu = kod if kod in i18n.TRANSLATIONS or kod == "cs" else "cs"


class _PrekladHlasek(logging.Filter):
    """Prelozi hlasku, kdyz je zvoleny cizojazycny log.

    Meni se `record.msg`, tedy predpis PRED doplnenim hodnot - proto musi
    mit preklad tytez zastupne znaky (%s, %d) ve stejnem poradi. Hlida to
    test, at se na to nezapomene.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if _jazyk_logu == "cs" or not isinstance(record.msg, str):
            return True
        preklad = i18n.LOG_EN.get(record.msg)
        if not preklad:
            return True

        record.msg = preklad
        # A doplněné hodnoty taky. Do hlášky se dostávají i české názvy
        # z rozhraní ("Nově přidané tituly"), takže by anglický log psal
        # "scheduled task: Nově přidané tituly". Překládá se přes týž
        # slovník jako rozhraní - co v něm není, zůstává, jak bylo.
        #
        # Jen u hlášek, které slovník zná: v cizí hlášce (třeba
        # z uvicornu) mohou být hodnoty čehokoliv a překládat je naslepo
        # by znamenalo měnit data, ne popis.
        if isinstance(record.args, tuple):
            record.args = tuple(
                i18n.EN.get(hodnota, hodnota) if isinstance(hodnota, str) else hodnota
                for hodnota in record.args
            )
        return True


def setup() -> Path | None:
    """Připojí zápis do souboru. Volá se jednou při startu aplikace.

    Když se soubor založit nepodaří (jen ke čtení, chybí práva), aplikace
    kvůli tomu nesmí spadnout - logování je pomůcka, ne podmínka běhu.
    Řekneme to nahlas do ostatních logů a jede se dál.
    """
    global _handler
    if _handler is not None:
        return Path(_handler.baseFilename)

    cesta = log_path()
    try:
        cesta.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            cesta, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    except OSError as exc:
        log.warning("log do souboru se nepodařilo založit (%s): %s", cesta, exc)
        return None

    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(logging.INFO)
    handler.addFilter(_PrekladHlasek())

    # Věsíme se na kořenový logger, ne jen na "jellyscope". Díky tomu se
    # do souboru dostanou i hlášky uvicornu - a právě ty říkají, proč se
    # server nezvedl.
    logging.getLogger().addHandler(handler)

    # Bez tohohle by v souboru byla jen varování a chyby. Výchozí práh
    # loggeru je WARNING, takže hlášky typu "synchronizace hotova, 12 nových"
    # by se nikam nedostaly - a právě ty člověk v logu hledá nejčastěji.
    #
    # Práh zvedáme jen našemu loggeru, ne kořenovému: httpx a spol. logují
    # na INFO každý jednotlivý požadavek a log by se jimi zaplavil.
    logging.getLogger("jellyscope").setLevel(logging.INFO)

    # HTTP klient loguje každý požadavek včetně CELÉ adresy - a v adrese
    # je u Telegramu token bota a u Discordu celý webhook. Za běžného
    # provozu se ta hláška zahodí (kořenový logger je na WARNING), jenže
    # stačí spustit server s --log-level debug a token je v souboru.
    # Práh se proto nastavuje natvrdo, místo spoléhání na výchozí hodnotu.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _handler = handler
    return cesta


def _tajemstvi() -> list[str]:
    """Hodnoty, které se ve výpisu nesmějí objevit.

    Čte se to při každém zobrazení logu, ne do zásoby: klíč se dá
    v Nastavení kdykoliv změnit a maskovat starou hodnotu by bylo k ničemu.
    """
    from . import db

    hodnoty: list[str] = []
    try:
        _, api_klic = db.jellyfin_connection()
        if api_klic:
            hodnoty.append(api_klic)
        databaze = db.database_config()
        if getattr(databaze, "password", ""):
            hodnoty.append(databaze.password)
        # Přístupové údaje k upozorněním. Token Telegramu je součástí
        # adresy, takže se do hlášky dostane i bez toho, aby ho tam někdo
        # psal - viz notifikace.bez_tajemstvi().
        from . import notifikace

        hodnoty += [db.get_setting(klic, "") for klic in notifikace.TAJNA]
    except Exception:  # noqa: BLE001
        # Nedostupná databáze nesmí zabránit zobrazení logu - právě v něm
        # bude nejspíš napsané, proč nejde.
        pass
    return [h for h in hodnoty if h and len(h) >= 4]


# Klíč se v hlášce může objevit i v podobě "api_key=xxx" nebo
# "X-Emby-Token: xxx", tedy s hodnotou, kterou v nastavení nemáme
# (třeba překlep při zadávání). Proto ještě obecný vzor.
_VZORY = [
    re.compile(r"(api[_-]?key\s*[=:]\s*)(\S+)", re.I),
    re.compile(r"(token\s*[=:\"']+\s*)([^\s\"'&]+)", re.I),
    re.compile(r"(password\s*[=:]\s*)(\S+)", re.I),
    # Heslo uvnitř připojovacího řetězce: postgres://uzivatel:heslo@stroj
    re.compile(r"(://[^:/\s]+:)([^@\s]+)(@)"),
]


def _zamaskuj(text: str, tajemstvi: list[str]) -> str:
    for hodnota in tajemstvi:
        text = text.replace(hodnota, "***")
    for vzor in _VZORY:
        if vzor.groups >= 3:
            text = vzor.sub(r"\1***\3", text)
        else:
            text = vzor.sub(r"\1***", text)
    return text


def _uroven(radek: str) -> str:
    """Z řádku vytáhne úroveň, aby se dala ve výpisu obarvit a filtrovat."""
    for uroven in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
        if f" {uroven} " in radek or f" {uroven:<7} " in radek:
            return uroven
    return ""


def read_lines(name: str = "", limit: int = DEFAULT_LINES,
               level: str = "") -> dict[str, Any]:
    """Posledních `limit` řádků zvoleného logu, připravených k zobrazení.

    Čteme od konce souboru: log má smysl číst pozpátku, poslední řádek je
    ten, kvůli kterému se sem člověk dívá. Celý soubor přitom do paměti
    nenačítáme - u dvoumegabajtového souboru by to bylo zbytečné plýtvání.
    """
    limit = max(10, min(MAX_LINES, limit))
    soubory = available_files()

    if not name and soubory:
        name = soubory[0]["name"]
    cesta = _bezpecna_cesta(name)

    if cesta is None:
        return {"lines": [], "name": name, "files": soubory,
                "path": str(log_dir()), "exists": False,
                "size_bytes": 0, "truncated": False}

    try:
        radky = _tail(cesta, limit)
        velikost = cesta.stat().st_size
    except OSError as exc:
        return {"lines": [{"text": f"Log se nepodařilo přečíst: {exc}",
                           "level": "ERROR"}],
                "name": name, "files": soubory, "path": str(cesta),
                "exists": True, "size_bytes": 0, "truncated": False}

    tajemstvi = _tajemstvi()
    vysledek = []
    for radek in radky:
        uroven = _uroven(radek)
        if level and uroven != level:
            continue
        vysledek.append({"text": _zamaskuj(radek.rstrip("\n"), tajemstvi),
                         "level": uroven})

    return {
        "lines": vysledek,
        "name": name,
        "files": soubory,
        "path": str(cesta),
        "exists": True,
        "size_bytes": velikost,
        "truncated": len(radky) >= limit,
    }


def _tail(cesta: Path, limit: int) -> list[str]:
    """Posledních `limit` řádků souboru, bez načtení celého do paměti.

    Čte se po blocích od konce, dokud jich není dost. Prostší zápis
    `soubor.readlines()[-limit:]` by u velkého logu natáhl do paměti
    všechno, jen aby z toho vzal poslední kousek.
    """
    blok = 8192
    with cesta.open("rb") as soubor:
        soubor.seek(0, 2)
        konec = soubor.tell()
        data = b""
        while konec > 0 and data.count(b"\n") <= limit:
            krok = min(blok, konec)
            konec -= krok
            soubor.seek(konec)
            data = soubor.read(krok) + data

    text = data.decode("utf-8", errors="replace")
    radky = text.splitlines()

    # Když jsme četli od konce a nedošli na začátek souboru, první řádek je
    # utržený v půlce - blok skončil, kde skončil. Takový řádek zahodíme:
    # půlka hlášky mate víc, než kdyby tam nebyla.
    if konec > 0 and radky:
        radky = radky[1:]
    return radky[-limit:]


def levels() -> list[str]:
    """Úrovně nabízené ve filtru - od nejzávažnější."""
    return ["ERROR", "WARNING", "INFO"]
