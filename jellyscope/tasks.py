"""Naplanovane ulohy.

Tri veci, ktere se maji delat samy, ale musi jit spustit i rucne a uplne
vypnout:

  * **sync**   - synchronizace knihovny z Jellyfinu; pri zdroji dat
                 'ffprobe' na ni rovnou navaze analyza souboru
  * **recent** - jen nove pridane tituly; rychla, da se poustet casto
  * **backup** - zaloha databaze do zvolene slozky

Vsechny maji stejny tvar, popsany tridou `Task`. Diky tomu staci napsat
planovac jednou a pridani dalsi ulohy je otazka peti radku, ne dalsi
kopie cele smycky.

## Proc je analyza souboru soucasti synchronizace

Drive to byla samostatna uloha s vlastnim zaskrtavatkem. Bylo z toho vic
skody nez uzitku:

  * ta uloha se **neptala na zvoleny zdroj dat**. Kdyz mel clovek ve
    Sberu dat "Jen Jellyfin API" a uloha zustala zapnuta, ffprobe se
    stejne rozjel a prepsal udaje z Jellyfinu - presny opak toho,
    co si uzivatel nastavil.
  * a naopak: se zdrojem 'ffprobe' a vypnutou ulohou zustaly tituly
    z plne synchronizace uplne bez technickych dat, protoze se z
    Jellyfinu uz neberou.

Dve zaskrtavatka tedy tise rozhodovala o tom, jestli v knihovne data
jsou, nebo neni nic. Ted je zaskrtavatko jedno ("synchronizuj knihovnu")
a **jak** se to udela, plyne ze Sberu dat. Rucni analyza zustava tam,
kde byla - v Nastaveni u volby zdroje.

## Jak planovani funguje

Neni tu zadny cron. Na pozadi bezi jedna smycka, ktera se **jednou za
minutu** podiva, jestli uz nejaka uloha dozrala - a kdyz ano, spusti ji.
Pro ulohy, ktere bezi jednou za hodiny, to bohate staci a nemusi se kvuli
tomu instalovat nic dalsiho.

Dozrat muze uloha dvema zpusoby a kazdy se hodi na neco jineho:

  * **kazdy den v urcity cas** (synchronizace knihovny, zaloha) - to jsou
    ulohy, ktere maji bezet v noci, kdyz se nikdo nediva. Kdyby se
    pocitaly od posledniho behu, kazde rucni spusteni by cely rozvrh
    posunulo a uloha by se casem "protocila" do odpoledne.
  * **kazdych N minut** (nove pridane tituly) - tam na presnem case
    nezalezi, jde o to ptat se casto.

Denni cas se porovnava v **mistnim** case: kdo napise 3:30, mysli tim
pul ctvrte u sebe doma, ne v UTC.

Kazdy zpusob se pta na neco jineho:

  * intervalova uloha se diva do `scan_log`, tedy "kdy uloha naposledy
    bezela" - at uz ji spustil planovac, nebo clovek.
  * denni uloha se diva na vlastni razitko `task_<klic>_last_auto`, tedy
    "kdy ji naposledy spustil planovac". Rucni spusteni se do nej
    nezapisuje schvalne: kdyz si clovek v poledne klikne na "spustit
    ted", nocni beh ve 3:30 se ma stejne odehrat.

Obojí prezije restart - jedno je v tabulce, druhe v nastaveni.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import db, dialect, scanner
from .config import BASE_DIR

log = logging.getLogger("jellyscope.tasks")

# Jak casto se planovac probouzi a kontroluje, jestli neco nedozralo.
TICK_SECONDS = 60


@dataclass(frozen=True)
class Task:
    """Popis jedne naplanovane ulohy."""

    key: str                                   # 'sync' / 'recent' / 'backup'
    name: str                                  # co se ukaze uzivateli
    description: str
    runner: Callable[[], Awaitable[dict[str, Any]]]
    log_kind: str                              # pod jakym nazvem se zapisuje do scan_log

    # Uloha je bud denni (klic s casem "HH:MM"), nebo intervalova
    # (klic s poctem minut). Vyplneny je vzdy prave jeden pár.
    time_setting: str = ""
    default_time: str = ""
    interval_setting: str = ""
    default_minutes: int = 0

    @property
    def je_denni(self) -> bool:
        return bool(self.time_setting)

    @property
    def enabled_setting(self) -> str:
        return f"task_{self.key}_enabled"


async def _run_sync() -> dict[str, Any]:
    """Synchronizace knihovny - a pri zdroji 'ffprobe' rovnou i zmereni.

    Poradi je dane: nejdriv se musi vedet, ze titul existuje a kde lezi
    jeho soubor, teprve pak se da zmerit. Analyza jde jen po tech, ktere
    data jeste nemaji (`only_missing`), takze po prvnim behu uz je
    kratka - a kdyz neni co merit, skonci hlaskou a nic nedela.

    Analyza az ZA synchronizaci, ne uvnitr: obe si berou tyz zamek, takze
    by se uvnitr zablokovaly. Ze stejneho duvodu to takhle dela i rychla
    synchronizace, viz scanner.sync_recent().
    """
    vysledek = await scanner.sync_library()

    if vysledek.get("status") != "ok":
        # Nedokoncena synchronizace (chyba, nebo zastaveno na pokyn) -
        # merit neuplny seznam nema smysl a u "zastaveno" by to navic
        # bylo proti tomu, co clovek chtel.
        return vysledek

    if db.get_setting("tech_source") != "ffprobe":
        return vysledek

    tech = await scanner.run_tech_scan(only_missing=True)
    vysledek["tech"] = tech
    if tech.get("ok"):
        log.info("synchronizace: zmereno %s souboru", tech["ok"])
    return vysledek


async def _run_recent() -> dict[str, Any]:
    # Hranici si uloha urci sama z posledniho znameho titulu -
    # viz scanner.sync_recent().
    return await scanner.sync_recent()


async def _run_backup() -> dict[str, Any]:
    return await backup_database()


TASKS: dict[str, Task] = {
    task.key: task
    for task in [
        Task(
            key="sync",
            name="Synchronizace knihovny",
            description=(
                "Stáhne z Jellyfinu seznam uživatelů, knihoven a titulů. "
                "Je-li ve Sběru dat zvolený ffprobe, naváže na ni analýza "
                "souborů, které technická data ještě nemají."
            ),
            time_setting="library_sync_time",
            default_time="03:30",
            runner=_run_sync,
            log_kind="library",
        ),
        Task(
            key="recent",
            name="Nově přidané tituly",
            description=(
                "Stáhne jen tituly, které v knihovně ještě nejsou - "
                "podle data posledního přidaného. Jellyfin skoro nezatíží, "
                "takže plná synchronizace může běžet mnohem řidčeji. "
                "Při zdroji dat 'ffprobe' rovnou změří i nově přidané "
                "soubory, aby na technická data nečekaly do další analýzy."
            ),
            interval_setting="recent_sync_minutes",
            default_minutes=15,
            runner=_run_recent,
            log_kind="recent",
        ),
        Task(
            key="backup",
            name="Záloha databáze",
            description="Uloží kopii databáze do zvolené složky a smaže přebytečné starší zálohy.",
            time_setting="task_backup_time",
            default_time="04:30",
            runner=_run_backup,
            log_kind="backup",
        ),
    ]
}


# ---------------------------------------------------------------------------
# Zaloha databaze
# ---------------------------------------------------------------------------

async def backup_database() -> dict[str, Any]:
    """Ulozi kopii databaze do slozky z nastaveni.

    Kazda databaze se zalohuje vlastnim nastrojem - SQLite vestavenou
    funkci `backup()`, PostgreSQL programem `pg_dump`. Obojí resi
    konzistentni snimek za behu, coz obycejne kopirovani souboru neumi.
    """
    target = db.get_setting("backup_path", "").strip()
    if not target:
        return {"status": "error", "message": "Není nastavená cesta pro zálohy."}

    target_dir = Path(target)
    scan_id = scanner.start_task_log("backup")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        scanner.finish_task_log(scan_id, "error", message=str(exc))
        return {"status": "error", "message": f"Složku nelze vytvořit: {exc}"}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    database = db.database_config()

    # Kazda databaze se zalohuje po svem. SQLite ma zalohovani vestavene,
    # PostgreSQL na to ma vlastni nastroj pg_dump.
    if database.is_postgres:
        destination = target_dir / f"jellyscope-{stamp}.sql"
        runner = lambda: _dump_postgres(database, destination)  # noqa: E731
    else:
        destination = target_dir / f"jellyscope-{stamp}.db"
        runner = lambda: _backup_sqlite(database, destination)  # noqa: E731

    try:
        size = await asyncio.to_thread(runner)
    except Exception as exc:  # noqa: BLE001
        log.exception("zaloha selhala")
        scanner.finish_task_log(scan_id, "error", message=str(exc))
        return {"status": "error", "message": f"Záloha selhala: {exc}"}

    removed = _prune_backups(target_dir)

    scanner.finish_task_log(
        scan_id, "done", total=1, ok=1,
        message=f"{destination.name} ({size / 1024 / 1024:.1f} MB)"
                + (f", smazáno starších: {removed}" if removed else ""),
    )
    return {"status": "ok", "file": str(destination), "size": size, "removed": removed}


def _backup_sqlite(config: Any, destination: Path) -> int:
    """Záloha SQLite přes vestavěnou funkci `backup()`.

    Ne obyčejné kopírování souboru: když se do databáze právě zapisuje,
    kopie bajtů může zachytit rozdělanou transakci a vznikne poškozený
    soubor. Vestavěná záloha vytvoří konzistentní snímek i za běhu.
    """
    path = Path(config.path)
    if not path.is_absolute():
        path = BASE_DIR / path

    source = sqlite3.connect(path)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return destination.stat().st_size


# Kde na jednotlivých systémech leží klientské nástroje PostgreSQL.
#
# Na Debianu a Ubuntu můžou být nainstalované **vedle sebe ve víc
# verzích** a v PATH bývá ta nejstarší. Právě proto se stane, že na
# Ubuntu 20.04 (klient 12) selže záloha serveru 17 hláškou
# "aborting because of server version mismatch": pg_dump umí zálohovat
# server stejné nebo starší verze, nikdy novější.
PG_BIN_VZORY = (
    "/usr/lib/postgresql/*/bin/pg_dump",     # Debian, Ubuntu
    "/usr/pgsql-*/bin/pg_dump",              # RHEL, Rocky, Alma
    "/opt/homebrew/opt/postgresql@*/bin/pg_dump",
    "/usr/local/opt/postgresql@*/bin/pg_dump",
)


def _verze_nastroje(cesta: str) -> int:
    """Hlavní číslo verze pg_dump. Vrací 0, když se nedá zjistit."""
    try:
        vysledek = subprocess.run([cesta, "--version"], capture_output=True,
                                  timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0
    text = vysledek.stdout.decode("utf-8", errors="replace")
    shoda = re.search(r"(\d+)(?:\.\d+)?", text)
    return int(shoda.group(1)) if shoda else 0


def dostupne_pg_dumpy(nastavena_cesta: str = "") -> list[tuple[int, str]]:
    """Všechny pg_dump, které na stroji jsou - od nejnovějšího.

    Ručně nastavená cesta má přednost před vším ostatním; kdo si ji
    vyplní, ví, co chce.
    """
    nalezene: dict[str, int] = {}

    if nastavena_cesta:
        cesta = Path(nastavena_cesta)
        if cesta.is_dir():
            cesta = cesta / "pg_dump"
        if cesta.is_file():
            return [(_verze_nastroje(str(cesta)), str(cesta))]

    v_ceste = shutil.which("pg_dump")
    if v_ceste:
        nalezene[v_ceste] = _verze_nastroje(v_ceste)

    for vzor in PG_BIN_VZORY:
        for nalez in glob.glob(vzor):
            if nalez not in nalezene and os.access(nalez, os.X_OK):
                nalezene[nalez] = _verze_nastroje(nalez)

    return sorted(((verze, cesta) for cesta, verze in nalezene.items()),
                  reverse=True)


def server_version() -> int:
    """Hlavní verze PostgreSQL serveru. 0 = nevíme (nebo je to SQLite)."""
    return _verze_serveru(None)


def _verze_serveru(config: Any) -> int:
    """Hlavní verze serveru, ke kterému se připojujeme. 0 = nevíme."""
    try:
        cislo = db.query_value("SHOW server_version_num", default=0)
        return int(cislo) // 10000 if cislo else 0
    except Exception:  # noqa: BLE001
        return 0


def _vyber_pg_dump(config: Any) -> str:
    """Vybere pg_dump, který tenhle server zvládne - nebo poradí."""
    kandidati = dostupne_pg_dumpy(db.get_setting("pg_dump_path", "").strip())
    server = _verze_serveru(config)

    if not kandidati:
        raise RuntimeError(
            "pg_dump se nepodařilo najít. Je součástí klientských nástrojů "
            "PostgreSQL - nainstaluj je, nebo zálohuj databázi vlastním "
            "postupem a tuhle úlohu vypni."
        )

    # Nejnovější, který na server stačí. Když verzi serveru neznáme,
    # vezmeme prostě nejnovější dostupný - je to nejlepší odhad.
    for verze, cesta in kandidati:
        if not server or verze >= server:
            return cesta

    nejlepsi, kde = kandidati[0]
    raise RuntimeError(
        f"Nainstalovaný pg_dump je verze {nejlepsi}, ale server má verzi "
        f"{server}. pg_dump umí zálohovat jen server stejné nebo starší "
        f"verze. Doinstaluj klienta {server} - na Debianu a Ubuntu:\n"
        f"    sudo apt install -y postgresql-client-{server}\n"
        f"(potřebuje repozitář PGDG: https://www.postgresql.org/download/)\n"
        f"Nainstalované verze: "
        + ", ".join(f"{v} ({c})" for v, c in kandidati)
        + f". Cestu ke správnému pg_dump jde vyplnit i v Nastavení → Úlohy."
    )


# Tabulky, ze kterych se sklada zaloha. Poradi neni nahodne: `item_streams`
# se odkazuje na `items` cizim klicem, takze pri obnove musi byt polozky
# uz na svem miste.
ZALOHOVANE_TABULKY = ("libraries", "users", "items", "item_streams",
                      "accounts", "settings", "scan_log", "playback")


def _sql_hodnota(hodnota: Any) -> str:
    """Jedna hodnota zapsana tak, aby ji PostgreSQL prijal zpatky.

    Retezce se uzaviraji do apostrofu a apostrof uvnitr se zdvoji - to je
    standardni zapis a s vychozim `standard_conforming_strings` (zapnute
    od verze 9.1) plati i pro zpetna lomitka, ktera tak zustavaji obycejnym
    znakem. Nic jineho nez texty, cisla a NULL v nasem schematu neni.
    """
    if hodnota is None:
        return "NULL"
    if isinstance(hodnota, bool):
        return "TRUE" if hodnota else "FALSE"
    if isinstance(hodnota, (int, float)):
        return repr(hodnota)
    text = str(hodnota).replace("'", "''")
    # Znak NUL v textu PostgreSQL neprijme ani ve stringu - zahodime ho.
    return "'" + text.replace("\x00", "") + "'"


def _dump_vlastni(destination: Path) -> int:
    """Zaloha PostgreSQL vlastnimi silami, kdyz pg_dump neni k dispozici.

    `pg_dump` je lepsi nastroj a zustava prvni volbou. Jenze se k nemu
    nejde vzdycky dostat: umi zalohovat jen server stejne nebo starsi
    verze, a na starsim systemu se novejsi klient nemusi dat doinstalovat
    vubec - repozitar PGDG pro Ubuntu 20.04 uz neexistuje, protoze ta
    verze skoncila podporu.

    Odmitnout v takove situaci zalohovat by bylo to nejhorsi reseni.
    Jellyscope zna svoje tabulky - zalozil je sam podle `schema.sql` - a
    dokaze si je vyexportovat: schema plus data jako obycejne INSERTy.
    Vysledek se obnovi prostym `psql -f`.

    Co tahle zaloha **neobsahuje**: nic, co do databaze pridal nekdo jiny
    (cizi tabulky, pohledy, funkce, opravneni). Pro databazi, kterou
    Jellyscope pouziva sam pro sebe, je to uplna zaloha; kdyz v ni mas
    i neco sveho, pouzij pg_dump.
    """
    from . import db as _db          # az tady, at nevznikne kruhovy import

    radku = 0
    with destination.open("w", encoding="utf-8") as soubor:
        soubor.write(
            "-- Záloha Jellyscope (vlastní export)\n"
            f"-- vytvořeno: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            "--\n"
            "-- Obnova:  psql -U <uživatel> -d <databáze> -f tento-soubor.sql\n"
            "--\n"
            "-- Obsahuje schéma a data tabulek Jellyscope. Cizí tabulky,\n"
            "-- pohledy ani oprávnění v něm nejsou - na to je pg_dump.\n\n"
            "BEGIN;\n\n"
        )

        # Schema bereme z tehoz souboru, ze ktereho se databaze zaklada,
        # takze se zaloha nemuze rozejit se skutecnym tvarem tabulek.
        schema = _db.SCHEMA_SQLITE.read_text(encoding="utf-8")
        soubor.write(dialect.translate(schema, dialect.POSTGRES))
        soubor.write("\n\n")

        for tabulka in ZALOHOVANE_TABULKY:
            radky = _db.query_all(f"SELECT * FROM {tabulka}")
            if not radky:
                continue
            sloupce = list(radky[0].keys())
            soubor.write(f"-- {tabulka}: {len(radky)} řádků\n")
            for radek in radky:
                hodnoty = ", ".join(_sql_hodnota(radek[s]) for s in sloupce)
                soubor.write(
                    f"INSERT INTO {tabulka} ({', '.join(sloupce)})"
                    f" VALUES ({hodnoty}) ON CONFLICT DO NOTHING;\n"
                )
            soubor.write("\n")
            radku += len(radky)

        soubor.write("COMMIT;\n")

    log.info("vlastní záloha PostgreSQL: %d řádků", radku)
    return destination.stat().st_size


def _dump_postgres(config: Any, destination: Path) -> int:
    """Záloha PostgreSQL přes `pg_dump`.

    Vlastní kopírování tabulek bychom sice napsat mohli, ale byla by to
    horší záloha než ta, kterou umí databáze sama - `pg_dump` řeší
    konzistentní snímek, pořadí závislostí i indexy. Když nástroj chybí,
    řekneme to nahlas místo abychom vyrobili neúplnou zálohu a tvářili
    se, že je hotovo.

    Který pg_dump se použije, vybírá `_vyber_pg_dump()` - na tom záleží
    víc, než by člověk čekal, viz komentář u PG_BIN_VZORY.
    """
    # Když se použitelný pg_dump nenajde, nezálohovat vůbec by bylo to
    # nejhorší řešení - viz `_dump_vlastni()`.
    try:
        tool = _vyber_pg_dump(config)
    except RuntimeError as exc:
        log.warning("pg_dump nelze použít (%s), zálohuje se vlastním exportem", exc)
        return _dump_vlastni(destination)

    environment = dict(os.environ)
    # Heslo se predava promennou prostredi, ne v prikazove radce -
    # argumenty procesu jsou na stroji viditelne pro kazdeho.
    if config.password:
        environment["PGPASSWORD"] = config.password

    # Soubor si otevře sám pg_dump (přepínač -f), místo abychom mu
    # podstrkovali svůj popisovač přes přesměrování výstupu.
    #
    # Přesměrování tu původně bylo a v některých prostředích selhávalo
    # na "could not write to output file: Bad file descriptor". Proces,
    # který dostane cizí popisovač, s ním nemusí umět pracovat - obzvlášť
    # když aplikace běží pod správcem služeb, který si se standardními
    # výstupy dělá svoje. S `-f` je za soubor zodpovědný pg_dump a případná
    # chyba je jeho vlastní, srozumitelná ("could not open output file
    # ...: Permission denied").
    result = subprocess.run(
        [tool, "-h", config.host, "-p", str(config.port),
         "-U", config.user, "-d", config.database, "--no-password",
         "-f", str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=1800,
        check=False,
    )

    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace").strip()[:300]
            or f"pg_dump skončil s kódem {result.returncode}"
        )

    # Prázdný soubor je horší než žádný: tvářil by se jako hotová záloha.
    if not destination.exists() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError("pg_dump skončil bez chyby, ale soubor je prázdný.")

    return destination.stat().st_size


def _prune_backups(directory: Path) -> int:
    """Necha jen posledních N zaloh, starsi smaze.

    Bez tohohle by slozka se zalohami rostla donekonecna, az by zaplnila
    disk - a zaloha, ktera zaplni disk, nadela vic skody nez uzitku.
    """
    keep = db.get_int_setting("backup_keep", 1, 365, 7)
    try:
        files = sorted(
            list(directory.glob("jellyscope-*.db")) + list(directory.glob("jellyscope-*.sql")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return 0

    removed = 0
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def list_backups() -> list[dict[str, Any]]:
    """Zalohy, ktere ted ve slozce lezi - aby bylo v UI videt, ze fungují."""
    target = db.get_setting("backup_path", "").strip()
    if not target:
        return []

    directory = Path(target)
    if not directory.is_dir():
        return []

    result = []
    try:
        for path in sorted(list(directory.glob("jellyscope-*.db")) + list(directory.glob("jellyscope-*.sql")),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            stat = path.stat()
            result.append({
                "name": path.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).strftime(db.TIME_FORMAT),
            })
    except OSError:
        return []
    return result


def backup_file(name: str) -> Path | None:
    """Přeloží jméno zálohy na soubor ve složce se zálohami.

    Jméno přichází z prohlížeče, takže se s ním nesmí zacházet jako
    s cestou - `../../.env` je taky "jméno". Bere se proto jen holé jméno,
    musí sedět na tvar, jaký zálohy mají, a výsledek se ještě ověří proti
    tomu, co ve složce doopravdy leží. Stejná opatrnost jako u prohlížeče
    logu.
    """
    target = db.get_setting("backup_path", "").strip()
    if not target or not name:
        return None
    if name != Path(name).name:
        return None
    if not name.startswith("jellyscope-") or not name.endswith((".db", ".sql")):
        return None

    cesta = Path(target) / name
    return cesta if cesta.is_file() else None


# Podle čeho se pozná záloha, kterou si Jellyscope vyrobil sám
# (viz `_dump_vlastni`). Takovou umí i obnovit; výstup z pg_dump ne.
VLASTNI_ZALOHA_ZNACKA = "-- Záloha Jellyscope"


def restore_backup(name: str) -> dict[str, Any]:
    """Obnoví databázi ze zálohy. Přepíše všechno, co v ní teď je.

    Než se cokoliv přepíše, vyrobí se **záloha současného stavu**. Obnova
    je jediná operace v aplikaci, která umí smazat všechno najednou -
    a člověk, který si splete řádek v seznamu, by o to jinak přišel.

    Co se dá obnovit:

      * **SQLite** - soubor se prostě vrátí na místo databáze.
      * **PostgreSQL, vlastní export** - je to obyčejné SQL, které jsme
        sami napsali, takže ho umíme i spustit.
      * **PostgreSQL, výstup z pg_dump** - ten obsahuje bloky COPY
        a další věci, které se bezpečně "přehrát" po řádcích nedají.
        Na to je `psql`; když je po ruce, použije se, jinak řekneme,
        jakým příkazem to udělat ručně.

    Po obnově je potřeba aplikaci restartovat: v paměti má nastavení
    a otevřená spojení do databáze, která už neplatí.
    """
    zdroj = backup_file(name)
    if zdroj is None:
        return {"status": "error", "message": "Takovou zálohu se nepodařilo najít."}

    config = db.database_config()
    if config.is_postgres and zdroj.suffix == ".db":
        return {"status": "error",
                "message": "Tahle záloha je ze SQLite, ale běžíš na PostgreSQL."}
    if not config.is_postgres and zdroj.suffix == ".sql":
        return {"status": "error",
                "message": "Tahle záloha je z PostgreSQL, ale běžíš na SQLite."}

    # Pojistka: co je teď, se nejdřív uloží.
    try:
        pojistka = _zaloha_pred_obnovou(config)
    except Exception as exc:  # noqa: BLE001
        log.exception("zalohu pred obnovou se nepodarilo vyrobit")
        return {"status": "error",
                "message": f"Nejdřív se nepodařilo zazálohovat současný stav: {exc}. "
                           f"Obnova se proto nespustila."}

    try:
        if config.is_postgres:
            _obnov_postgres(config, zdroj)
        else:
            _obnov_sqlite(config, zdroj)
    except Exception as exc:  # noqa: BLE001
        log.exception("obnova zalohy selhala")
        return {"status": "error",
                "message": f"Obnova selhala: {exc}. Současný stav zůstal "
                           f"zazálohovaný v {pojistka.name}."}

    log.info("databaze obnovena ze zalohy %s", name)
    return {"status": "ok", "file": name, "safety": pojistka.name}


def _zaloha_pred_obnovou(config: Any) -> Path:
    """Uloží současný stav, než ho obnova přepíše."""
    slozka = Path(db.get_setting("backup_path", "").strip())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if config.is_postgres:
        cil = slozka / f"jellyscope-{stamp}-pred-obnovou.sql"
        _dump_postgres(config, cil)
    else:
        cil = slozka / f"jellyscope-{stamp}-pred-obnovou.db"
        _backup_sqlite(config, cil)
    return cil


def _obnov_sqlite(config: Any, zdroj: Path) -> None:
    """Vrátí soubor databáze na místo.

    Napřed se ověří, že je to opravdu databáze SQLite a že v ní jsou naše
    tabulky. Přepsat běžící databázi cizím souborem by aplikaci rozbilo
    a vrátit by to šlo jen ručně.
    """
    spojeni = sqlite3.connect(f"file:{zdroj}?mode=ro", uri=True)
    try:
        tabulky = {r[0] for r in spojeni.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        spojeni.close()
    if "playback" not in tabulky or "items" not in tabulky:
        raise RuntimeError("V souboru nejsou tabulky Jellyscope - není to jeho záloha.")

    cil = Path(config.path)
    if not cil.is_absolute():
        cil = BASE_DIR / cil

    db.close_pool()
    shutil.copyfile(zdroj, cil)
    # Deník a sdílená paměť patří k původnímu souboru; kdyby zůstaly,
    # SQLite by z nich dopsala změny, které k obnovené databázi nepatří.
    for pripona in ("-wal", "-shm"):
        Path(str(cil) + pripona).unlink(missing_ok=True)


def _obnov_postgres(config: Any, zdroj: Path) -> None:
    """Přehraje zálohu do PostgreSQL."""
    zacatek = zdroj.read_text(encoding="utf-8", errors="replace")[:200]

    if VLASTNI_ZALOHA_ZNACKA in zacatek:
        # Vlastni export je obycejne SQL, ktere jsme sami napsali - jeho
        # tvar znama, takze ho muzeme spustit primo. Napred se ale musi
        # uklidit soucasny obsah, jinak by se INSERTy potkaly s tim, co uz
        # v tabulkach je (dump ma ON CONFLICT DO NOTHING).
        obsah = zdroj.read_text(encoding="utf-8")
        with db.connect() as conn:
            for tabulka in reversed(ZALOHOVANE_TABULKY):
                conn.execute(f"DELETE FROM {tabulka}")
            conn.execute(obsah)
        db.close_pool()
        return

    # Vystup z pg_dump - na ten je potreba psql.
    nastroj = _najdi_psql()
    if not nastroj:
        raise RuntimeError(
            "Tahle záloha je z pg_dump a na její obnovu je potřeba psql, "
            "který se na stroji nenašel. Obnov ji ručně:\\n"
            f"    psql -h {config.host} -p {config.port} -U {config.user} "
            f"-d {config.database} -f {zdroj}"
        )

    prostredi = dict(os.environ)
    if config.password:
        prostredi["PGPASSWORD"] = config.password

    vysledek = subprocess.run(
        [nastroj, "-h", config.host, "-p", str(config.port), "-U", config.user,
         "-d", config.database, "--no-password", "-v", "ON_ERROR_STOP=1",
         "-f", str(zdroj)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=prostredi, timeout=3600, check=False,
    )
    if vysledek.returncode != 0:
        raise RuntimeError(
            vysledek.stderr.decode("utf-8", errors="replace").strip()[:300]
            or f"psql skončil s kódem {vysledek.returncode}")
    db.close_pool()


def _najdi_psql() -> str | None:
    """psql, pokud je na stroji - hledá se stejně jako pg_dump."""
    kandidati = [shutil.which("psql")]
    for vzor in PG_BIN_VZORY:
        kandidati.extend(glob.glob(vzor.replace("pg_dump", "psql")))
    nalezene = [c for c in kandidati if c and os.access(c, os.X_OK)]
    if not nalezene:
        return None
    # Nejnovejsi verze - stejna uvaha jako u pg_dump.
    return max(nalezene, key=_verze_nastroje)


def delete_backup(name: str) -> bool:
    """Smaže jednu zálohu. Vrací, jestli se to povedlo."""
    cesta = backup_file(name)
    if cesta is None:
        return False
    try:
        cesta.unlink()
    except OSError as exc:
        log.warning("zalohu %s se nepodarilo smazat: %s", name, exc)
        return False
    log.info("zaloha smazana: %s", name)
    return True


def free_space(path_text: str) -> int | None:
    """Volne misto ve slozce - hodi se rict predem, nez zaloha selze."""
    try:
        return shutil.disk_usage(path_text).free
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Planovac
# ---------------------------------------------------------------------------

def is_enabled(task: Task) -> bool:
    return db.get_setting(task.enabled_setting, "0") == "1"


def interval_minutes(task: Task) -> int:
    if task.je_denni:
        return 0
    return db.get_int_setting(task.interval_setting, 0, 10080, task.default_minutes)


def denni_cas(task: Task) -> str:
    """Cas denni ulohy jako "HH:MM". Nesmysl v nastaveni nahradi vychozi."""
    if not task.je_denni:
        return ""
    return platny_cas(db.get_setting(task.time_setting, task.default_time),
                       task.default_time)


def platny_cas(text: str, nahrada: str) -> str:
    """Ohlida tvar "HH:MM". Do planovace se nesmi dostat nic jineho."""
    shoda = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    if shoda:
        hodina, minuta = int(shoda.group(1)), int(shoda.group(2))
        if 0 <= hodina < 24 and 0 <= minuta < 60:
            return f"{hodina:02d}:{minuta:02d}"
    return nahrada


def volby_casu(task: Task) -> dict[str, Any]:
    """Cas rozdeleny na hodinu a minutu - do dvou poli ve formulari.

    Proc dve pole a ne `<input type="time">` nebo dva `<select>`: obojí
    si otevira vlastni vyskakovaci okno, ktere kresli prohlizec sam.
    CSS na nej nedosahne a smer, kterym se rozbali, si urcuje podle
    mista na obrazovce - u spodniho radku tabulky vyjede nahoru. Dve
    cisla se neotviraji vubec a vypadaji jako zbytek formulare.
    """
    hodina, minuta = denni_cas(task).split(":")
    return {"hodina": hodina, "minuta": minuta}


def status(task: Task) -> dict[str, Any]:
    """Vsechno, co o uloze potrebuje vedet stranka Nastaveni."""
    last = scanner.last_scan(task.log_kind)
    zbyva = _minutes_until_due(task, last)
    radek = {
        "task": task,
        "enabled": is_enabled(task),
        "daily": task.je_denni,
        "time": denni_cas(task),
        "minutes": interval_minutes(task),
        "last": last,
        "due_in": zbyva,
        # U denni ulohy se misto "za 12,3 h" ukaze rovnou termin - to je
        # cislo, ktere clovek do nastaveni napsal, takze se v nem pozna.
        "next_is_today": (task.je_denni and zbyva is not None
                          and datetime.now() < _dnesni_cil(task, datetime.now())),
    }
    if task.je_denni:
        radek.update(volby_casu(task))
    return radek


def all_statuses() -> list[dict[str, Any]]:
    return [status(task) for task in TASKS.values()]


def _mistni_cas(utc_text: str) -> datetime | None:
    """Cas z databaze (UTC) prevedeny na mistni. None, kdyz se neda precist.

    Do databaze se casy ukladaji vzdycky v UTC - viz db.utcnow(). Denni
    cas ulohy ale zadava clovek v tom case, ve kterem zije, takze se obojí
    musi potkat na jedne strane. Prevadime na mistni, protoze "kazdy den
    ve 3:30" ma znamenat 3:30 na hodinach na zdi.
    """
    try:
        naive = datetime.strptime(
            str(utc_text).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _dnesni_cil(task: Task, ted: datetime) -> datetime:
    """Dnesni termin denni ulohy v mistnim case."""
    hodina, minuta = (int(cast) for cast in denni_cas(task).split(":"))
    return ted.replace(hour=hodina, minute=minuta, second=0, microsecond=0)


def _minutes_until_due(task: Task, last: dict[str, Any] | None) -> float | None:
    """Za kolik minut uloha pobezi. None = nikdy (vypnuta)."""
    if not is_enabled(task):
        return None

    if task.je_denni:
        ted = datetime.now()
        cil = _dnesni_cil(task, ted)
        if _je_denni_uloha_na_rade(task, ted, cil):
            return 0.0
        # Uz probehla (nebo je teprve pred nami) - do dalsiho terminu.
        zbyva = cil if ted < cil else cil + timedelta(days=1)
        return max(0.0, (zbyva - ted).total_seconds() / 60)

    minutes = interval_minutes(task)
    if minutes <= 0:
        return None
    if last is None:
        return 0.0
    return max(0.0, minutes - _uplynulo_minut(last))


def _uplynulo_minut(last: dict[str, Any]) -> float:
    """Kolik minut ubehlo od posledniho behu."""
    mistni = _mistni_cas(last["started_at"])
    if mistni is None:
        return 0.0
    return (datetime.now() - mistni).total_seconds() / 60


def posledni_automaticky_beh(task: Task) -> datetime | None:
    """Kdy tuhle ulohu naposledy spustil planovac (mistni cas).

    Zamerne se nekouka do `scan_log`: tam jsou i **rucni** spusteni
    a podle nich se rozvrh ridit nesmi. Kdyz si clovek v poledne klikne
    na "spustit ted", nocni beh ve 3:30 se ma stejne odehrat - jednou uz
    proto, ze mezi polednem a ranem se knihovna zase zmeni.
    """
    text = db.get_setting(_klic_posledniho_behu(task), "")
    return _mistni_cas(text) if text else None


def _klic_posledniho_behu(task: Task) -> str:
    return f"task_{task.key}_last_auto"


def _poznamenej_automaticky_beh(task: Task) -> None:
    """Zapise, ze uloha prave startuje z planovace.

    Zapisuje se PRED spustenim, ne po nem: uloha bezi klidne deset minut
    a planovac se probouzi kazdou minutu - kdyby razitko prislo az na
    konci, spustil by ji mezitim znovu.
    """
    db.set_setting(_klic_posledniho_behu(task), db.utcnow())


def _je_denni_uloha_na_rade(task: Task, ted: datetime, cil: datetime) -> bool:
    """Ma denni uloha bezet prave ted?

    Podminka je jednoducha: dnesni termin uz nastal a planovac ho jeste
    neodbavil. Diky tomu:
      * rucni spusteni na rozvrh **vubec nesaha** - ani ho neposune, ani
        dnesni beh nezrusi,
      * kdyz aplikace ve 3:30 zrovna nebezela, uloha se dozene pri startu
        misto aby se cely den preskocila.
    """
    if ted < cil:
        return False
    posledni = posledni_automaticky_beh(task)
    return posledni is None or posledni < cil


def _is_due(task: Task) -> bool:
    if not is_enabled(task):
        return False

    if task.je_denni:
        ted = datetime.now()
        return _je_denni_uloha_na_rade(task, ted, _dnesni_cil(task, ted))

    last = scanner.last_scan(task.log_kind)
    minutes = interval_minutes(task)
    if minutes <= 0:
        return False
    if last is None:
        return True
    return _uplynulo_minut(last) >= minutes


async def run_now(key: str) -> dict[str, Any]:
    """Rucni spusteni ulohy z Nastaveni."""
    task = TASKS.get(key)
    if task is None:
        return {"status": "error", "message": "Neznámá úloha."}
    return await task.runner()


async def run_scheduler() -> None:
    """Smycka na pozadi. Jednou za minutu zkontroluje, co uz dozralo.

    Nesmi nikdy spadnout - kdyby umrela, ulohy by se tise prestaly poustet
    a nikdo by si toho nevsiml. Proto to siroke `except`.
    """
    await asyncio.sleep(25)  # nech aplikaci nastartovat

    while True:
        try:
            for task in TASKS.values():
                # Uloha, o ktere jeste nic nevime (cerstva instalace nebo
                # prave nasazena zmena), se **nedohani**. Jen si poznamename
                # "od ted pocitame" a prvni beh prijde v jeji cas.
                #
                # Bez toho se po nasazení v pul ctvrte odpoledne rozjela
                # nocni synchronizace hned: dnesni termin uz nastal a zadny
                # zaznam o behu neexistoval, takze to vypadalo jako
                # zameskany termin. Doháněj se ma jen to, o cem vime, ze
                # to melo probehnout - ne prvni seznameni.
                if task.je_denni and posledni_automaticky_beh(task) is None:
                    _poznamenej_automaticky_beh(task)
                    log.info("uloha %s: zacinam pocitat rozvrh od ted, "
                             "prvni beh v %s", task.key, denni_cas(task))
                    continue

                if _is_due(task):
                    log.info("naplanovana uloha: %s", task.name)
                    if task.je_denni:
                        _poznamenej_automaticky_beh(task)
                    result = await task.runner()
                    log.info("uloha %s skoncila: %s", task.key, result.get("status"))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("planovac uloh selhal")

        await asyncio.sleep(TICK_SECONDS)
