"""Práce s databází.

Aplikace umí běžet nad **SQLite** (výchozí, nic se neinstaluje) i nad
**PostgreSQL** (když už ho doma máš). Zbytek programu o tom ale neví –
píše se jeden dotaz v SQLite dialektu a `dialect.translate()` ho podle
potřeby přeloží. Viz [dialect.py](dialect.py).

Záměrně tu není žádné ORM (knihovna, která píše SQL za tebe). Jellyscope
je z devadesáti procent "polož databázi chytrou otázku a nakresli odpověď" –
a to je přesně to, v čem je SQL dobré. Když se ho naučíš tady, budeš ho
umět všude.

Celý modul stojí na jedné funkci: `connect()`. Otevře spojení, dá ti ho,
a po skončení bloku ho sám zavře a uloží změny (nebo je při chybě vrátí
zpět).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import dialect
from .config import BASE_DIR, load_config

log = logging.getLogger("jellyscope.db")

SCHEMA_SQLITE = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_POSTGRES = Path(__file__).resolve().parent / "schema_postgres.sql"

# Jednotný formát času v celé aplikaci (viz utcnow níže).
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

DEFAULT_SETTINGS: dict[str, str] = {
    # --- pripojeni k Jellyfinu -------------------------------------------
    # Drive bylo v .env. Ted se nastavuje z webu, aby uzivatel nemusel
    # sahat do souboru a restartovat. Hodnoty z .env slouzi uz jen jako
    # prvni naplneni pri uplne prvnim startu (viz seed_from_env).
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    # Jazyk rozhrani: 'cs' nebo 'en'.
    "ui_language": "cs",
    # Jazyk logu. Zvlast od rozhrani schvalne: log cte casto nekdo jiny
    # (nebo se posila do issue), takze tam muze davat smysl jiny jazyk.
    "log_language": "cs",
    # 'jellyfin' = technicka data ber z Jellyfin API (funguje vzdy, mene detailu)
    # 'ffprobe'  = analyzuj soubory primo na disku (presnejsi, potrebuje pristup)
    "tech_source": "jellyfin",
    # Jak casto se ptat Jellyfinu, co se prave hraje (v sekundach).
    "poll_interval": "10",
    # Kdy se ma kazdy den synchronizovat knihovna (mistni cas "HH:MM").
    #
    # Cas, ne interval: je to nocni uloha, ktera projde celou knihovnu.
    # Kdyby se pocitala od posledniho behu, kazde rucni spusteni by rozvrh
    # posunulo a uloha by se casem protocila do odpoledne, kdy se lidi
    # divaji. Viz tasks.py.
    "library_sync_time": "03:30",
    # Jak casto hledat nove pridane tituly. Uloha stoji par volani do
    # Jellyfinu (jedno na knihovnu), takze muze bezet casto.
    #
    # Hranici "co je nove" si urcuje sama podle posledniho znameho titulu,
    # ne podle tohohle intervalu - viz scanner.sync_recent(). Interval rika
    # jen JAK CASTO se ptat, ne JAK DALEKO zpet se divat.
    "recent_sync_minutes": "15",
    "task_recent_enabled": "1",
    # Cesta k programu ffprobe. Prazdne = hledej v PATH.
    "ffprobe_path": "",
    # Prepis cest, kdyz Jellyfin vidi soubory jinde nez Jellyscope.
    "path_mappings": "[]",
    # Kolik souboru analyzovat najednou.
    "ffprobe_concurrency": "3",

    # --- naplanovane ulohy (viz tasks.py) ---------------------------------
    "task_sync_enabled": "1",
    "task_backup_enabled": "0",
    # Zaloha je taky nocni uloha - stejny duvod jako u synchronizace.
    # Kousek za ni, at si nelezou do zamku.
    # Hlidani nove verze je vychozi VYPNUTE: je to odchozi spojeni
    # a o tom rozhoduje ten, kdo server provozuje. Viz updates.py.
    # Kolik polozek karta vypise rovnou, nez zbytek schova do okna.
    # Vyssi cislo = vic videt bez klikani, ale karta roste a odsouva
    # vsechno pod sebou. Viz web._context() a sablony _now_playing.html
    # a languages.html.
    "ui_max_streams": "10",
    "ui_max_viewers": "10",
    # Jak se priblizuje mapa na strance Sit. "click" schvalne jako
    # vychozi: kolecko nad mapou by jinak zastavilo rolovani stranky
    # a clovek by u mapy uvizl. Viz web._stropy() a base.html.
    "ui_map_zoom": "click",
    "update_check_enabled": "0",
    "task_tidy_enabled": "1",
    # Mezi synchronizaci (03:30) a zalohou (04:30): narovnani pracuje
    # s tim, co synchronizace prave stahla, a zaloha uz ma ulozit
    # srovnana data.
    "task_tidy_time": "04:00",
    "task_backup_time": "04:30",
    "backup_path": "",
    "backup_keep": "7",
    # Prazdne = Jellyscope si pg_dump najde sam a vybere verzi,
    # ktera na server staci. Viz tasks._vyber_pg_dump().
    "pg_dump_path": "",
}


# Nastaveni, ktera uz aplikace nepouziva.
#
# Nova hodnota se pri startu doplni sama (viz init_db), ale ta stara
# v tabulce zustane lezet navzdy - a kdo se do databaze podiva, marne
# hleda, proc se podle ni nic neridi. Proto se pri startu smazou.
#
# Do seznamu patri klic teprve tehdy, kdyz uz ho nikde v kodu neni.
ZRUSENA_NASTAVENI = (
    # Analyza souboru prestala byt samostatnou ulohou - je soucasti
    # synchronizace a ridi se zdrojem dat. Viz tasks.py.
    "task_tech_enabled",
    "task_tech_minutes",
    # Synchronizace knihovny a zaloha se planuji na cas, ne na interval.
    "library_sync_minutes",
    "task_backup_minutes",
)


def utcnow() -> str:
    """Aktuální čas v UTC jako text.

    Čas ukládáme vždy v UTC a vždy ve stejném formátu. Jakmile začneš míchat
    časové zóny nebo formáty, řazení podle času přestane dávat smysl a hledáš
    to týden.

    Formát "2026-08-12 09:47:00" (s mezerou, ne s T) není náhodný – rozumí
    mu SQLite i PostgreSQL, když se text přetypuje na časový údaj.
    """
    return datetime.now(timezone.utc).strftime(TIME_FORMAT)


# ---------------------------------------------------------------------------
# Nastavení připojení
# ---------------------------------------------------------------------------

_db_config: dialect.DatabaseConfig | None = None


def database_config(reload: bool = False) -> dialect.DatabaseConfig:
    """Které databáze se držíme. Načte se jednou při startu."""
    global _db_config
    if _db_config is None or reload:
        _db_config = dialect.load_config(
            BASE_DIR, str(load_config().database_path)
        )
    return _db_config


def current_dialect() -> str:
    return dialect.POSTGRES if database_config().is_postgres else dialect.SQLITE


# ---------------------------------------------------------------------------
# Společné rozhraní nad oběma databázemi
# ---------------------------------------------------------------------------

class Connection:
    """Obálka, která se navenek chová stejně pro SQLite i PostgreSQL.

    Dělá tři věci:
      1. překládá SQL do dialektu cílové databáze,
      2. sjednocuje rozhraní (psycopg nemá `executemany` na spojení),
      3. vrací řádky tak, aby šly číst podle jména sloupce.
    """

    def __init__(self, raw: Any, kind: str) -> None:
        self._raw = raw
        self.kind = kind

    @property
    def is_postgres(self) -> bool:
        return self.kind == dialect.POSTGRES

    def execute(self, sql: str, params: Any = ()) -> Any:
        query = dialect.translate(sql, self.kind)
        if self.is_postgres:
            cursor = self._raw.cursor()
            # Prazdna n-tice, ne None - i kdyz dotaz zadne parametry nema.
            #
            # Preklad zdvojuje procenta (`LIKE '%cs%'` -> `LIKE '%%cs%%'`),
            # protoze psycopg cte `%` jako zacatek zastupneho znaku. Zpatky
            # na jedno procento je slozi az psycopg - jenze to dela jen
            # tehdy, kdyz nejake parametry dostane. S `None` by dotaz odesel
            # se zdvojenymi procenty a hledal by text "%cs%" doslova.
            cursor.execute(query, tuple(params))
            return cursor
        return self._raw.execute(query, params)

    def executemany(self, sql: str, rows: Any) -> Any:
        query = dialect.translate(sql, self.kind)
        if self.is_postgres:
            cursor = self._raw.cursor()
            cursor.executemany(query, [tuple(r) for r in rows])
            return cursor
        return self._raw.executemany(query, rows)

    def executescript(self, script: str) -> None:
        """Spustí celý soubor se schématem."""
        if self.is_postgres:
            # psycopg zvládne víc příkazů v jednom volání, když nejsou
            # žádné parametry - a schéma žádné nemá.
            self._raw.cursor().execute(script)
        else:
            self._raw.executescript(script)

    def insert_returning_id(self, sql: str, params: Any = ()) -> int:
        """Vloží řádek a vrátí jeho nové id.

        SQLite na to má `cursor.lastrowid`, PostgreSQL `RETURNING id`.
        Rozdíl schováme sem, aby o něm zbytek programu nemusel vědět.
        """
        if self.is_postgres:
            cursor = self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
            row = cursor.fetchone()
            if row is None:
                return 0
            return int(row["id"] if isinstance(row, dict) else row[0])

        cursor = self.execute(sql, params)
        return int(cursor.lastrowid or 0)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def table_columns(self, table: str) -> set[str]:
        """Názvy sloupců tabulky. Používá se při migracích."""
        if self.is_postgres:
            cursor = self.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = ?",
                (table,),
            )
            return {row["column_name"] if isinstance(row, dict) else row[0]
                    for row in cursor.fetchall()}

        rows = self._raw.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}


# ---------------------------------------------------------------------------
# Zásobník spojení (jen PostgreSQL)
# ---------------------------------------------------------------------------
#
# SQLite je soubor na disku - otevřít ho stojí zlomek milisekundy, takže se
# otevírá a zavírá pokaždé znovu a je to v pořádku.
#
# PostgreSQL je server. Každé spojení znamená TCP handshake, přihlášení a na
# straně serveru nový proces - na localhostu jednotky milisekund, po síti
# klidně desítky. Jedna stránka Jellyscope si o data řekne asi dvanáctkrát,
# takže by se tahle cena zaplatila dvanáctkrát za každé načtení.
#
# Zásobník (pool) drží hrstku spojení otevřených a půjčuje je. Cena za
# navázání se zaplatí jednou při startu, ne při každém dotazu.
#
# Co to stojí: každé nečinné spojení drží na serveru proces (řádově jednotky
# MB paměti). Proto min_size = 1 - v klidu visí jediné, zbytek se navazuje
# jen když je opravdu potřeba, a po chvíli nečinnosti zase zmizí.

_pool: Any = None
_pool_key: tuple | None = None

# Strop je 8: sběrač na pozadí + několik souběžných požadavků z webu.
# Vyšší číslo by nepomohlo - požadavky stejně obsluhuje omezený počet
# vláken - a jen by zbytečně drželo procesy na databázovém serveru.
POOL_MAX_SIZE = 8


def _pool_identity(config: dialect.DatabaseConfig) -> tuple:
    """Podle čeho poznáme, že zásobník patří k jinému serveru než teď."""
    return (config.host, config.port, config.database, config.user, config.password)


def close_pool() -> None:
    """Zavře zásobník. Volá se při vypnutí aplikace a před restartem.

    Bez tohoto by po `os.execv` zůstala viset spojení, o kterých už nový
    proces neví, a PostgreSQL by je držel až do vlastního časového limitu.
    """
    global _pool, _pool_key
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001 - při vypínání nás chyba nezajímá
            pass
    _pool = None
    _pool_key = None


def _get_pool(config: dialect.DatabaseConfig) -> Any:
    """Vrátí zásobník pro dané připojení, nebo None.

    None znamená "jeď postaru, spojení po spojení". Stane se to ve dvou
    případech a ani jeden není chyba:

      * uživatel si zásobník v Nastavení vypnul,
      * knihovna psycopg_pool není k dispozici a nejde doinstalovat.

    V obou případech aplikace funguje dál, jen si ke každému dotazu
    navazuje nové spojení. Na stejné síti je ten rozdíl malý, po WAN
    znatelný - ale fungovat musí vždycky.
    """
    global _pool, _pool_key

    if not config.use_pool:
        close_pool()
        return None

    identity = _pool_identity(config)
    if _pool is not None and _pool_key == identity:
        return _pool

    close_pool()

    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError:
        return None

    pool = ConnectionPool(
        conninfo="",
        kwargs={
            "host": config.host,
            "port": config.port,
            "dbname": config.database,
            "user": config.user,
            "password": config.password,
            "row_factory": dict_row,
            "connect_timeout": 10,
        },
        min_size=1,
        max_size=POOL_MAX_SIZE,
        # Když jsou všechna spojení půjčená, čekej nejvýš 10 s a pak chybu.
        # Bez limitu by se požadavek zasekl navždy.
        timeout=10.0,
        max_idle=300.0,
        open=True,
        name="jellyscope",
    )
    _pool, _pool_key = pool, identity
    return pool


def _open_raw(config: dialect.DatabaseConfig) -> Any:
    if config.is_postgres:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - závisí na instalaci
            # Dvě věci, na kterých se tahle hláška dřív lámala:
            #
            # 1. Radila `psycopg[binary,pool]`, takže to vypadalo, že si
            #    vynucuje zásobník spojení - i když ho měl uživatel vypnutý.
            #    Zásobník je doplněk navíc; bez psycopg se k PostgreSQL
            #    nepřipojíš vůbec, ať máš zásobník zapnutý, nebo ne.
            #
            # 2. Radila holé `pip`, jenže aplikace běží ve virtuálním
            #    prostředí. Do systémového pythonu by se instalovalo
            #    zbytečně (a na novějších Ubuntu by to rovnou odmítl).
            #    sys.executable je ten správný python - ten, kterým to
            #    zrovna běží.
            raise RuntimeError(
                "Pro PostgreSQL je potřeba knihovna psycopg. Nainstaluj ji "
                "do stejného prostředí, ve kterém běží aplikace:\n"
                f'    {sys.executable} -m pip install "psycopg[binary]"\n'
                "Potom aplikaci restartuj. (Connection pool s tím nesouvisí "
                "- ten se zapíná zvlášť a přidává doplněk \"pool\".)"
            ) from exc

        spojeni = psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=config.password,
            row_factory=dict_row,
            connect_timeout=10,
        )

        # Casova zona spojeni musi byt tataz, v jake pocita aplikace.
        #
        # Bez toho se "mistni cas" pocita na DVOU mistech ruzne:
        #   * v SQL (date(x, 'localtime')) podle zony databazoveho serveru,
        #   * pri vypisu casu v Pythonu podle zony serveru s aplikaci.
        #
        # Kdyz se lisi - a lisi se casto, PostgreSQL bezi obvykle v UTC -
        # den v grafu nekonci tam, kde ho konci vypis. Proklik na den pak
        # ukaze i zaznamy, ktere podle zobrazeneho casu patri jinam.
        #
        # TZ nastavuji konfigurace sluzeb v deploy/ (Europe/Prague).
        zona = os.environ.get("TZ", "").strip()
        if zona:
            try:
                with spojeni.cursor() as kurzor:
                    # Nazev zony nejde predat jako parametr, proto ho pred
                    # vlozenim overime - do SQL smi jen to, co vypada jako
                    # nazev zony.
                    if re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", zona):
                        kurzor.execute(f"SET TIME ZONE '{zona}'")
                spojeni.commit()
            except Exception:  # noqa: BLE001 - spatna zona nesmi shodit start
                log.warning("casovou zonu %r se nepodarilo nastavit", zona)

        return spojeni

    path = Path(config.path)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, timeout=15.0)
    # row_factory nám zařídí, že výsledky lezou jako slovníky (row["name"])
    # místo anonymních n-tic (row[3]). Kód je pak čitelný.
    connection.row_factory = sqlite3.Row
    # WAL = Write-Ahead Logging. Umožní, aby jeden proces psal a jiný zároveň
    # četl. Přesně náš případ: sběrač na pozadí zapisuje, web čte.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def connect(config: dialect.DatabaseConfig | None = None) -> Iterator[Connection]:
    """Otevře spojení s databází jako "context manager" (používá se s `with`).

        with db.connect() as conn:
            conn.execute("INSERT ...")
        # tady už je změna uložená a spojení zavřené

    Když uvnitř bloku nastane chyba, změny se vrátí zpět (rollback) - databáze
    tedy nikdy nezůstane v půlce rozdělané operace.
    """
    config = config or database_config()

    # U PostgreSQL si spojení půjčíme ze zásobníku a na konci ho vrátíme
    # zpátky - nezavíráme ho. `pool.connection()` sám udělá rollback, když
    # blok skončí výjimkou, a spojení uklidí, než ho půjčí dalšímu.
    pool = _get_pool(config) if config.is_postgres else None
    if pool is not None:
        with pool.connection() as raw:
            connection = Connection(raw, config.kind)
            yield connection
            connection.commit()
        return

    connection = Connection(_open_raw(config), config.kind)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def psycopg_available() -> bool:
    """Je ovladač PostgreSQL k dispozici?

    Používá to Nastavení: když ovladač je (což po instalaci skriptem
    obvykle je), nemá smysl uživateli ukazovat návod, jak ho doinstalovat.
    Rada, kterou nepotřebuješ, jen zabírá místo a mate.
    """
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


def pool_available() -> bool:
    """Je knihovna pro zásobník spojení k dispozici?"""
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        return False
    return True


def test_connection(config: dialect.DatabaseConfig) -> tuple[bool, str]:
    """Zkusí se připojit. Vrací (povedlo se, hláška pro uživatele).

    Spojení se tu navazuje **napřímo**, mimo zásobník. Testujeme totiž
    dostupnost serveru, ne zásobník - a kdyby si test spojení půjčoval,
    založil by zásobník na server, na který aplikace zatím není přepnutá.
    """
    raw = None
    try:
        raw = _open_raw(config)
        Connection(raw, config.kind).execute("SELECT 1 AS ok").fetchone()
    except Exception as exc:  # noqa: BLE001 - hlášku ukazujeme uživateli
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if raw is not None:
            try:
                raw.close()
            except Exception:  # noqa: BLE001
                pass

    # Zapnutý zásobník bez knihovny není chyba, ale uživatel to má vědět -
    # jinak by čekal zrychlení, které nepřijde.
    note = ""
    if config.is_postgres and config.use_pool and not pool_available():
        note = (" – connection pool ale zapnout nejde, chybí knihovna"
                " psycopg_pool. Aplikace pojede dál, jen si bude spojení"
                " navazovat po jednom.")
    return True, f"Spojení funguje: {config.describe()}{note}"


# ---------------------------------------------------------------------------
# Vytvoření a údržba schématu
# ---------------------------------------------------------------------------

# Sloupce, které v projektu přibyly až dodatečně.
#
# Proč to je potřeba: `CREATE TABLE IF NOT EXISTS` nový sloupec do už
# existující tabulky nepřidá - příkaz se prostě přeskočí. Kdo aplikaci
# spustil dřív, měl by po aktualizaci starou tabulku a aplikace by hlásila
# "no such column". Proto při každém startu ověříme, že všechny očekávané
# sloupce existují, a chybějící doplníme.
#
# Tomuhle se říká **migrace databáze**. Velké projekty na to mají celé
# knihovny; pro nás stačí tenhle slovník.
MIGRATIONS: dict[str, dict[str, str]] = {
    "items": {
        # Otisk obrazku z Jellyfinu (ImageTags.Primary). Jiny obrazek =
        # jiny otisk, takze podle nej pozname, ze uz je v mezipameti
        # neplatny. Viz web.item_image() a scanner._zapomen_obrazky().
        "image_tag": "TEXT",
        # Otisk plakatu SERIALU (Jellyfin ho hlasi u kazde epizody jako
        # SeriesPrimaryImageTag). Polozku pro serial nemame, takze bez
        # nej nebylo u jeho plakatu co porovnat - a jednou stazeny
        # obrazek tam zustal navzdy.
        "series_image_tag": "TEXT",
        "audio_languages": "TEXT",
        "subtitle_languages": "TEXT",
        "default_audio_language": "TEXT",
        # Identifikátor z TMDB. Přežije překódování souboru, na rozdíl
        # od ItemId z Jellyfinu - viz scanner._merge_by_tmdb().
        "tmdb_id": "TEXT",
        # Zanry z Jellyfinu, oddelene svislitkem. Pouzivaji se v detailu
        # uzivatele - "co ten clovek vlastne sleduje".
        "genres": "TEXT",
    },
    "playback": {
        "audio_language": "TEXT",
        "subtitle_language": "TEXT",
        # Bezi, nebo je pozastavene? Prida se za behu i do
        # starsich databazi - viz migrace nize.
        "is_paused": "INTEGER NOT NULL DEFAULT 0",
        # Rozmery obrazu, ktery relace prave hraje. Drive se braly z tabulky
        # `items`, takze u epizody, kterou jsme jeste nesynchronizovali,
        # chybelo rozliseni uplne. Relace to vi sama.
        "video_width": "INTEGER",
        "video_height": "INTEGER",
        # Jazyk, ktery hraje PRAVE TED. Vedle nej stoji `audio_language`,
        # ktery drzi ten prvni zjisteny. Kazdy odpovida na jinou otazku -
        # viz collector._store_sessions().
        # Delka poradu, jak ji hlasi sama relace. Drive se brala jen
        # z tabulky `items`, takze u epizody, kterou jsme jeste
        # nesynchronizovali, nesel spocitat postup - a ukazatel chybel.
        # BIGINT, ne INTEGER: jeden tik je 100 nanosekund, takze
        # ctvrthodinovy dil ma pres 9 miliard - do 4bajtoveho INTEGER
        # se v PostgreSQL nevejde ("integer out of range").
        "media_runtime_ticks": "BIGINT",
        "current_audio_language": "TEXT",
        "current_subtitle_language": "TEXT",
        # Od kdy hraje soucasna kombinace stop a jestli uz se zapocitala
        # do statistik. Viz collector.MIN_LANGUAGE_SECONDS.
        "language_since": "TEXT",
        "language_confirmed": "INTEGER NOT NULL DEFAULT 0",
        # Co presne se pri prepoctu deje - viz collector._describe_stream().
        # Samotne "transcode" nerika, jestli server prepocitava obraz
        # (drahe), jen zvuk (levne), nebo vypaluje titulky.
        "transcode_video_direct": "INTEGER",
        "transcode_audio_direct": "INTEGER",
        "transcode_hw": "TEXT",
    },
}


# Sloupce, u kterých se ukázalo, že původní typ nestačí.
#
# Migrace výše umí sloupec jen **přidat**. Když se pak zjistí, že měl mít
# jiný typ, tabulkám, které už vznikly, to nepomůže - a chyba se projeví
# až za běhu ("integer out of range").
#
# Týká se to jen PostgreSQL: tam je INTEGER čtyřbajtový. SQLite si velikost
# určuje podle hodnoty, takže tam přetéct nemá co.
TYPE_FIXES: dict[tuple[str, str], str] = {
    # Jeden tik je 100 nanosekund, takže čtvrthodinový díl má přes
    # 9 miliard - do čtyřbajtového INTEGER se nevejde.
    ("playback", "media_runtime_ticks"): "BIGINT",
}


def _migrate(conn: Connection) -> list[str]:
    """Doplní sloupce, které v databázi ještě nejsou. Vrátí, co přidal."""
    added: list[str] = []
    for table, columns in MIGRATIONS.items():
        existing = conn.table_columns(table)
        if not existing:
            continue  # tabulka ještě neexistuje, vytvoří ji schéma
        for column, column_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                added.append(f"{table}.{column}")

    if conn.is_postgres:
        for (table, column), novy_typ in TYPE_FIXES.items():
            if column in conn.table_columns(table):
                # Idempotentní: když už je typ správný, PostgreSQL nic
                # nedělá. Proto se to nemusí nijak evidovat.
                conn.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {novy_typ}"
                )
    return added


def _je_index(prikaz: str) -> bool:
    """Je tenhle příkaz zakládání indexu? Úvodní komentáře přeskočíme."""
    for radek in prikaz.splitlines():
        ocisteny = radek.strip()
        if not ocisteny or ocisteny.startswith("--"):
            continue
        return ocisteny.upper().startswith(("CREATE INDEX", "CREATE UNIQUE INDEX"))
    return False


def _oddel_indexy(script: str) -> tuple[str, str]:
    """Rozdělí schéma na dvě části: tabulky a indexy.

    Proč: index se odkazuje na sloupec, a `CREATE TABLE IF NOT EXISTS`
    do už existující tabulky nový sloupec nepřidá. Kdo aplikaci spustil
    dřív, měl by proto starou tabulku bez `tmdb_id`, ale schéma by hned
    zkusilo založit `idx_items_tmdb` - a celý start by spadl na
    "no such column: tmdb_id".

    Správné pořadí je tedy: **tabulky, pak migrace, teprve pak indexy.**
    Rozdělení je řádkové - příkaz končí řádkem zakončeným středníkem.
    Na to nám tady stačí; složitější SQL (třeba spouště s vnořeným
    `BEGIN ... END;`) schéma nemá.
    """
    tabulky: list[str] = []
    indexy: list[str] = []
    prikaz: list[str] = []

    for radek in script.splitlines(keepends=True):
        prikaz.append(radek)
        if radek.rstrip().endswith(";"):
            text = "".join(prikaz)
            (indexy if _je_index(text) else tabulky).append(text)
            prikaz = []

    if prikaz:
        # Zbytek bez středníku - typicky komentář na konci souboru.
        tabulky.append("".join(prikaz))

    return "".join(tabulky), "".join(indexy)


def init_db(config: dialect.DatabaseConfig | None = None) -> list[str]:
    """Vytvoří tabulky, doplní chybějící sloupce a nastaví výchozí hodnoty."""
    config = config or database_config()
    schema_file = SCHEMA_POSTGRES if config.is_postgres else SCHEMA_SQLITE
    tabulky, indexy = _oddel_indexy(schema_file.read_text(encoding="utf-8"))

    with connect(config) as conn:
        conn.executescript(tabulky)
        added = _migrate(conn)
        if indexy.strip():
            conn.executescript(indexy)
        for key, value in DEFAULT_SETTINGS.items():
            # ON CONFLICT DO NOTHING rozumí obě databáze - na rozdíl od
            # SQLite konstrukce INSERT OR IGNORE, která je jen její.
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO NOTHING",
                (key, value),
            )
        for key in ZRUSENA_NASTAVENI:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    forget_settings()   # schema se mohlo zmenit

    # První naplnění z .env děláme jen pro databázi, kterou aplikace
    # doopravdy používá.
    #
    # `init_db()` se totiž volá i na **cizí** databázi - když se v Nastavení
    # přenášejí data jinam, zakládá se tam schéma stejnou funkcí. Jenže
    # seed_from_env() čte i zapisuje přes výchozí spojení, takže by sahalo
    # do databáze, o kterou tady vůbec nejde (a při přenosu do ještě
    # nezaložené databáze by rovnou spadlo).
    if config is database_config():
        seed_from_env()
    return added


# ---------------------------------------------------------------------------
# Nastavení
# ---------------------------------------------------------------------------

# Nastavení se čte hodně často - a dřív to znamenalo pokaždé nové spojení
# do databáze. Nejhůř to bilo do očí u překladu: každé `_("text")` v šabloně
# volalo `get_setting("ui_language")`, takže jedna stránka otevřela přes sto
# spojení a strávila tím většinu svého času.
#
# Proto se celá tabulka drží v paměti. Je malá (pár desítek řádků) a mění
# se zřídka.
#
# Platnost je omezená na pár vteřin. Při zápisu se paměť zahodí hned, takže
# ve vlastním procesu je změna okamžitá; krátká platnost je jen pojistka
# pro případ, že by aplikace jednou běžela ve víc procesech nad společným
# PostgreSQL a nastavení změnil ten druhý.
_settings_cache: dict[str, str] | None = None
_settings_cached_at: float = 0.0
SETTINGS_CACHE_SECONDS = 5.0


def _stored_settings() -> dict[str, str]:
    """Co je opravdu v tabulce settings (bez výchozích hodnot)."""
    global _settings_cache, _settings_cached_at

    now = time.monotonic()
    if _settings_cache is not None and (now - _settings_cached_at) < SETTINGS_CACHE_SECONDS:
        return _settings_cache

    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()

    _settings_cache = {row["key"]: row["value"] for row in rows}
    _settings_cached_at = now
    return _settings_cache


def forget_settings() -> None:
    """Zahodí zapamatovaná nastavení. Volá se po každém zápisu."""
    global _settings_cache
    _settings_cache = None


def get_setting(key: str, default: str | None = None) -> str:
    stored = _stored_settings()
    if key in stored and stored[key] is not None:
        return stored[key]
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, "")


# Nastavení, která se nikdy nesmí dostat do stránky.
#
# API klíč k Jellyfinu je heslo: kdo ho má, může s cizím serverem dělat
# cokoliv, co umí jeho vlastník. Do prohlížeče proto nepatří ani omylem.
TAJNA_NASTAVENI = ("jellyfin_api_key",)


def get_settings() -> dict[str, str]:
    """Všechna nastavení najednou - výchozí hodnoty přebité tím, co je v DB."""
    values = dict(DEFAULT_SETTINGS)
    for key, value in _stored_settings().items():
        values[key] = value if value is not None else ""
    return values


def get_public_settings() -> dict[str, str]:
    """Nastavení bez tajemství - tohle se smí předat šabloně.

    Šablony dnes API klíč nikde nevypisují, ale celý slovník se jim
    předával včetně něj. Stačilo by jedno `{{ settings.jellyfin_api_key }}`
    (třeba při ladění) a klíč by skončil ve zdroji stránky. Levnější je
    ho sem vůbec nepustit než hlídat, že ho nikdo nevypíše.
    """
    values = get_settings()
    for klic in TAJNA_NASTAVENI:
        values.pop(klic, None)
    return values


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
    forget_settings()


def seed_from_env() -> None:
    """První naplnění připojení k Jellyfinu z .env.

    Dřív se adresa a klíč četly z .env při každém startu. Teď žijí
    v databázi a mění se z webu - ale kdo aplikaci už používal, má je
    pořád jen v .env. Proto je při prvním startu jednou překopírujeme.

    Podmínka "jen když je hodnota v databázi prázdná" je důležitá: bez ní
    by .env při každém startu přepsalo to, co uživatel nastavil na webu.
    """
    import os

    for setting_key, env_key in (("jellyfin_url", "JELLYFIN_URL"),
                                 ("jellyfin_api_key", "JELLYFIN_API_KEY")):
        if get_setting(setting_key, "").strip():
            continue
        value = os.environ.get(env_key, "").strip().rstrip("/")
        if value and not value.startswith("sem-vloz"):
            set_setting(setting_key, value)


def jellyfin_connection() -> tuple[str, str]:
    """Adresa a API klíč Jellyfinu - jediné místo, odkud je brát."""
    return (
        get_setting("jellyfin_url", "").strip().rstrip("/"),
        get_setting("jellyfin_api_key", "").strip(),
    )


def get_int_setting(key: str, minimum: int, maximum: int, fallback: int) -> int:
    """Číslo z nastavení, oříznuté do rozumných mezí.

    Uživatel může do formuláře napsat cokoliv. Nikdy nedůvěřuj vstupu -
    ani vlastnímu.
    """
    try:
        value = int(float(get_setting(key)))
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, value))


# ---------------------------------------------------------------------------
# Malí pomocníci, ať se pak v kódu neopakuje totéž dokola
# ---------------------------------------------------------------------------

def query_all(sql: str, params: Any = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def query_one(sql: str, params: Any = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def query_value(sql: str, params: Any = (), default: Any = 0) -> Any:
    """První sloupec prvního řádku - typicky pro `SELECT COUNT(*) ...`."""
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return default

    # PostgreSQL vrací slovník, SQLite řádek, ze kterého jde číst i podle
    # pořadí. Bereme první hodnotu bez ohledu na to, jak se sloupec jmenuje.
    value = next(iter(dict(row).values()))
    return default if value is None else value
