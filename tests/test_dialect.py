r"""Test překladu SQL do PostgreSQL.

Překlad je nejrizikovější kus celého projektu: běží na každém dotazu a
chyba v něm se projeví až za běhu, u konkrétní stránky. Proto má vlastní
test, který kontroluje jednotlivé vzory i skutečné dotazy z aplikace.

Co tenhle test **neumí**: ověřit, že přeložený dotaz PostgreSQL skutečně
přijme. K tomu je potřeba běžící server. Kontroluje se tvar, ne provedení.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_dialect.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import dialect  # noqa: E402

failures = 0


def check(ok: bool, message: str) -> None:
    global failures
    print(f"{'OK     ' if ok else 'CHYBA  '}{message}")
    if not ok:
        failures += 1


def pg(sql: str) -> str:
    return dialect.translate(sql, dialect.POSTGRES)


print("--- SQLite zůstává beze změny ---")
for sql in [
    "SELECT * FROM items WHERE id = ?",
    "SELECT datetime('now', ?) FROM playback",
    "SELECT name FROM items WHERE name LIKE ?",
]:
    check(dialect.translate(sql, dialect.SQLITE) == sql, f"beze změny: {sql[:40]}")

print("--- zástupné znaky ---")
check(pg("SELECT * FROM items WHERE id = ?") == "SELECT * FROM items WHERE id = %s",
      "otazník na %s")
check("?" not in pg("SELECT ? , ? , ?"), "všechny otazníky přeloženy")

# Otazník uvnitř textu se přepsat nesmí - je to data, ne zástupný znak.
translated = pg("SELECT * FROM items WHERE name = 'kdo?' AND id = ?")
check("'kdo?'" in translated and translated.endswith("= %s"),
      f"otazník uvnitř textu zůstal: {translated}")

print("--- procenta ---")
translated = pg("SELECT * FROM items WHERE audio_languages NOT LIKE '%cs%'")
check("'%%cs%%'" in translated, f"procenta zdvojena: {translated}")
check(translated.count("%s") == 0, "žádný falešný zástupný znak nevznikl")

translated = pg("SELECT * FROM items WHERE name LIKE ? AND path NOT LIKE '%tmp%'")
check("%s" in translated and "'%%tmp%%'" in translated,
      f"parametr i literál zároveň: {translated}")

print("--- čas ---")
translated = pg("WHERE started_at >= datetime('now', ?)")
check("to_char(" in translated, "datetime se převádí zpět na text")
check("::interval" in translated, "parametr se čte jako interval")
check("%s" in translated, "parametr zůstal parametrem")
# Sloupce jsou textové. Kdyby překlad vrátil timestamp, PostgreSQL by
# porovnání text >= timestamp odmítl.
check("'YYYY-MM-DD HH24:MI:SS'" in translated, "výsledek má náš formát času")

translated = pg("WHERE substr(date_created, 1, 10) < date('now', '-60 days')")
check("interval '-60 days'" in translated, f"pevný interval: {translated}")
check("'YYYY-MM-DD'" in translated, "u date() stačí datum bez času")

translated = pg("SELECT (julianday('now') - julianday(?)) * 24 * 60 AS minutes")
check("EXTRACT(EPOCH FROM" in translated and "/ 60" in translated,
      f"julianday na minuty: {translated}")
check("julianday" not in translated, "julianday nikde nezůstal")

print("--- místní čas ---")
translated = pg("SELECT date(started_at, 'localtime') AS day")
check("::date" in translated and "current_setting('TIMEZONE')" in translated,
      f"date(x,'localtime'): {translated}")

translated = pg("SELECT CAST(strftime('%H', started_at, 'localtime') AS INTEGER)")
check("EXTRACT(HOUR FROM" in translated, f"hodina: {translated}")
check("strftime" not in translated, "strftime nikde nezůstal")

translated = pg("SELECT CAST(strftime('%w', p.started_at, 'localtime') AS INTEGER)")
check("EXTRACT(DOW FROM" in translated, "den v týdnu")
check("p.started_at" in translated, "název sloupce s tečkou přežil")

print("--- LIKE a ILIKE ---")
check("ILIKE" in pg("WHERE name LIKE ?"), "LIKE na ILIKE")
check("NOT ILIKE" in pg("WHERE name NOT LIKE ?"), "NOT LIKE na NOT ILIKE")
# Uvnitř textu se slovo LIKE přepsat nesmí.
check("'I LIKE IT'" in pg("SELECT 'I LIKE IT' AS x"), "LIKE uvnitř textu zůstal")

print("--- pomocníci pro nepřenosné konstrukce ---")
check(dialect.group_concat(dialect.SQLITE, "path", "','") == "GROUP_CONCAT(path, ',')",
      "GROUP_CONCAT pro SQLite")
check(dialect.group_concat(dialect.POSTGRES, "path", "','") == "string_agg(path, ',')",
      "string_agg pro PostgreSQL")
check(dialect.greatest(dialect.SQLITE, "a", "b") == "MAX(a, b)", "MAX pro SQLite")
check(dialect.greatest(dialect.POSTGRES, "a", "b") == "GREATEST(a, b)",
      "GREATEST pro PostgreSQL")

print("--- skutečné dotazy z aplikace ---")
# Projdeme dotazy tak, jak je aplikace opravdu posílá, a ověříme, že
# v překladu nezůstal žádný tvar, kterému by PostgreSQL nerozuměl.
from jellyscope import db  # noqa: E402

REAL_QUERIES = [
    """
    SELECT COUNT(*) AS plays, COALESCE(SUM(watched_seconds), 0) AS watched_seconds
    FROM playback
    WHERE started_at >= datetime('now', ?) AND watched_seconds > 0
    """,
    """
    SELECT date(started_at, 'localtime') AS day, SUM(watched_seconds) / 3600.0 AS hours
    FROM playback WHERE started_at >= datetime('now', ?) GROUP BY day ORDER BY day
    """,
    """
    SELECT CAST(strftime('%w', started_at, 'localtime') AS INTEGER) AS weekday,
           CAST(strftime('%H', started_at, 'localtime') AS INTEGER) AS hour
    FROM playback WHERE started_at >= datetime('now', ?)
    """,
    """
    SELECT p.* FROM playback p LEFT JOIN items i ON i.id = p.item_id
    WHERE p.watched_seconds > 0 AND (p.item_name LIKE ? OR p.series_name LIKE ?)
    ORDER BY p.started_at DESC LIMIT ? OFFSET ?
    """,
    """
    SELECT COUNT(*) FROM items
    WHERE is_missing = 0 AND audio_languages NOT LIKE '%cs%'
    """,
    "SELECT (julianday('now') - julianday(?)) * 24 * 60",
    # Denní snímek knihovny: zápis přes ON CONFLICT i čtení za období.
    """
    INSERT INTO library_snapshot
        (den, polozek, filmu, epizod, velikost, uhd, hdr,
         bez_technik, volne_misto, zapsano_v)
    VALUES (?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT (den) DO UPDATE SET
        polozek = excluded.polozek, velikost = excluded.velikost
    """,
    """
    SELECT den, polozek, velikost, volne_misto FROM library_snapshot
    WHERE den >= ? AND den <= ? ORDER BY den
    """,
    # Podminka "tohle je prepocet" - hleda podle zacatku nazvu, takze nese
    # procento uvnitr retezce. Viz stats.je_transcode().
    """
    SELECT COALESCE(SUM(CASE WHEN play_method LIKE 'Transcode%'
                        THEN watched_seconds ELSE 0 END), 0) AS transcoded
    FROM playback WHERE started_at >= ? AND started_at < ?
    """,
    # Dopoctena minulost knihovny - viz stats._dopoctena_krivka().
    """
    SELECT substr(date_created, 1, 10) AS vznik,
           CASE WHEN is_missing = 1 THEN substr(synced_at, 1, 10) END AS naposledy,
           COALESCE(size_bytes, 0) AS velikost,
           type
      FROM items
     WHERE date_created IS NOT NULL AND date_created != ''
    """,
]

BANNED = ["datetime('now'", "julianday", "strftime", "'localtime'",
          "GROUP_CONCAT", "INSERT OR "]

for index, query in enumerate(REAL_QUERIES):
    translated = pg(query)
    leftovers = [word for word in BANNED if word in translated]
    check(not leftovers, f"dotaz {index + 1}: nezůstalo nic nepřenosného {leftovers}")
    check("?" not in translated, f"dotaz {index + 1}: žádný otazník nezůstal")

print("--- nastavení připojení ---")
config = dialect.DatabaseConfig(kind="postgres", host="db", port=5433,
                                database="js", user="u", password="tajne")
check(config.is_postgres, "rozpozná PostgreSQL")
check("tajne" not in config.describe(), f"popis neobsahuje heslo: {config.describe()}")
check("tajne" not in str(config.to_dict(include_password=False)),
      "export bez hesla ho opravdu neobsahuje")

restored = dialect.DatabaseConfig.from_dict(config.to_dict())
check(restored.to_dict() == config.to_dict(), "uložení a načtení dá totéž")

# Neznámý druh databáze musí spadnout na SQLite, ne rozbít start.
odd = dialect.DatabaseConfig.from_dict({"kind": "oracle"})
check(odd.kind == dialect.SQLITE, "neznámý druh databáze -> SQLite")


print("--- hláška při chybějícím psycopg ---")
# Dřív radila `pip install "psycopg[binary,pool]"`, takže to vypadalo,
# že si aplikace vynucuje zásobník spojení - i když ho měl uživatel
# vypnutý. A holé `pip` by instalovalo mimo virtuální prostředí.
import builtins  # noqa: E402

_real_import = builtins.__import__


def _bez_psycopg(name, *args, **kwargs):
    if name.startswith("psycopg"):
        raise ImportError("simulovaně chybí")
    return _real_import(name, *args, **kwargs)


bez_pool = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="x", database="d",
                                  user="u", password="p", use_pool=False)
builtins.__import__ = _bez_psycopg
try:
    db._open_raw(bez_pool)
    hlaska = ""
except RuntimeError as exc:
    hlaska = str(exc)
except Exception as exc:  # noqa: BLE001
    hlaska = f"jiná výjimka: {type(exc).__name__}"
finally:
    builtins.__import__ = _real_import

check("psycopg[binary]\"" in hlaska,
      "hláška radí základní psycopg[binary], ne rovnou doplněk pool")
check("psycopg[binary,pool]" not in hlaska,
      "hláška si nevynucuje zásobník, když je vypnutý")
check(sys.executable in hlaska,
      "hláška ukazuje python z virtuálního prostředí, ne obecné 'pip'")
check("-m pip install" in hlaska, "hláška používá python -m pip")


print("--- volba zásobníku se ukládá ---")
# Zásobník jde vypnout. Musí to přežít uložení do souboru i načtení zpátky,
# jinak by se po restartu sám zase zapnul.
off = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", database="js",
                             user="u", password="p", use_pool=False)
check(off.to_dict()["use_pool"] is False, "vypnutý zásobník je v exportu")
check(dialect.DatabaseConfig.from_dict(off.to_dict()).use_pool is False,
      "vypnutý zásobník přežije uložení a načtení")

# Starý soubor tenhle klíč nemá - tam musí platit zapnuto.
check(dialect.DatabaseConfig.from_dict({"kind": "postgres"}).use_pool is True,
      "starý soubor bez klíče = zapnuto")

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    dialect.save_config(base, off)
    loaded = dialect.load_config(base, "data/jellyscope.db")
    check(loaded.use_pool is False, "volba přežije zápis na disk a načtení")
    check(loaded.host == "db", "zbytek nastavení se cestou neztratil")


print("--- zásobník spojení ---")
# Zásobník je jen pro PostgreSQL. U SQLite se ho nesmí ani dotknout -
# otevřít soubor je levné a sdílet jedno spojení mezi vlákny by u sqlite3
# naopak dělalo potíže.
from jellyscope import db  # noqa: E402

sqlite_config = dialect.DatabaseConfig(kind=dialect.SQLITE, path="data/x.sqlite3")
check(db._get_pool.__module__ == "jellyscope.db", "zásobník je v db.py")

with db.connect(sqlite_config) as conn:
    conn.execute("SELECT 1")
check(db._pool is None, "SQLite zásobník vůbec nevytvoří")

# Zavření prázdného zásobníku nesmí spadnout - volá se při každém vypnutí,
# i když se na PostgreSQL nikdy nesáhlo.
db.close_pool()
check(db._pool is None, "close_pool() na prázdném zásobníku projde")

# Když psycopg_pool chybí, _get_pool vrátí None a aplikace jede postaru.
# Simulujeme to tím, že import knihovny dočasně znemožníme.
import builtins  # noqa: E402

real_import = builtins.__import__


def _blocked(name, *args, **kwargs):
    if name.startswith("psycopg"):
        raise ImportError("simulovaně chybí")
    return real_import(name, *args, **kwargs)


pg_config = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", port=5432,
                                   database="js", user="u", password="p")
builtins.__import__ = _blocked
try:
    check(db._get_pool(pg_config) is None, "bez psycopg_pool vrátí None místo pádu")
finally:
    builtins.__import__ = real_import
    db.close_pool()

# Zásobník patří ke konkrétnímu serveru. Změna cíle ho musí zahodit,
# jinak by se nová nastavení dotazovala staré databáze.
other_config = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="jiny", port=5432,
                                      database="js", user="u", password="p")
check(db._pool_identity(pg_config) != db._pool_identity(other_config),
      "jiný server = jiná totožnost zásobníku")
check(db._pool_identity(pg_config) == db._pool_identity(
          dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", port=5432,
                                 database="js", user="u", password="p")),
      "stejný server = stejná totožnost zásobníku")

# Když je psycopg_pool k dispozici, zkusíme zásobník opravdu postavit.
# Server k tomu není potřeba - spojení se navazují až na pozadí. Ověřujeme
# tím, že argumenty, které mu předáváme, knihovna doopravdy přijme.
try:
    import psycopg_pool  # noqa: F401
except ImportError:
    print("PRESKOCENO  psycopg_pool není nainstalované")
else:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pool = db._get_pool(pg_config)
        check(pool is not None, "zásobník se postavil")
        check(pool.max_size == db.POOL_MAX_SIZE, f"strop je {db.POOL_MAX_SIZE}")
        check(db._get_pool(pg_config) is pool, "druhé volání vrátí tentýž zásobník")
        check(db._get_pool(other_config) is not pool, "jiný server = nový zásobník")
        db.close_pool()
        check(db._pool is None, "close_pool() zásobník uklidil")

        # Vypnutá volba znamená "žádný zásobník", i když knihovna je.
        check(db._get_pool(off) is None, "vypnutá volba zásobník nepostaví")
        check(db._pool is None, "vypnutá volba po sobě nic nenechá")

# ---------------------------------------------------------------------------
# Procenta v dotazu bez parametrů
# ---------------------------------------------------------------------------
#
# Překlad zdvojuje procenta (`LIKE '%cs%'` -> `LIKE '%%cs%%'`), protože
# psycopg čte `%` jako začátek zástupného znaku. Zpátky na jedno procento to
# složí až psycopg - jenže to dělá jen tehdy, když nějaké parametry dostane.
#
# Dotaz bez parametrů proto musí dostat prázdnou n-tici, ne None. S None by
# odešel se zdvojenými procenty a `LIKE '%%cs%%'` by hledal text "%cs%"
# doslova - filtr by tiše přestal filtrovat.

print()
print("--- procenta v dotazu bez parametrů ---")


class FalesnyKurzor:
    def __init__(self):
        self.videl = None

    def execute(self, query, params=None):
        self.videl = (query, params)
        return self


class FalesneSpojeni:
    def __init__(self):
        self.kurzor = FalesnyKurzor()

    def cursor(self):
        return self.kurzor


syrove = FalesneSpojeni()
spojeni = db.Connection(syrove, dialect.POSTGRES)
spojeni.execute("SELECT COUNT(*) FROM items WHERE audio_languages NOT LIKE '%cs%'")
query, params = syrove.kurzor.videl

check(params is not None, "dotaz bez parametrů dostane n-tici, ne None")
check(params == (), f"a je prázdná: {params!r}")

# A teď doopravdy: co by ze zadaného dotazu poskládalo psycopg?
try:
    from psycopg._queries import PostgresQuery
    from psycopg.adapt import Transformer
except ImportError:
    print("PRESKOCENO  psycopg není nainstalované")
else:
    hotovy = PostgresQuery(Transformer())
    hotovy.convert(query, params)
    odeslane = hotovy.query.decode()
    check("'%cs%'" in odeslane, f"na server jde jedno procento: {odeslane}")
    check("%%" not in odeslane, "žádné zdvojené procento nezůstalo")

# ---------------------------------------------------------------------------
# Tiky se do čtyřbajtového INTEGER nevejdou
# ---------------------------------------------------------------------------
#
# Jellyfin měří čas ve "ticích" po 100 nanosekundách, takže čtvrthodinový
# díl má přes 9 miliard. V SQLite je to jedno (velikost se řídí hodnotou),
# ale PostgreSQL má INTEGER čtyřbajtový a spadne na "integer out of range".
#
# Chyba se projeví až za běhu a jen u jedné z databází - přesně ta, kterou
# vývoj na SQLite nikdy neukáže. Proto se hlídá tady.

print()
print("--- sloupce s tiky musí být BIGINT ---")
import re  # noqa: E402

schema_pg = (PROJECT / "jellyscope" / "schema_postgres.sql").read_text(encoding="utf-8")
spatne = [
    radek.strip()
    for radek in schema_pg.splitlines()
    if re.search(r"^\s*\w*ticks\w*\s+", radek) and "BIGINT" not in radek.upper()
]
check(not spatne, f"v schema_postgres.sql; chybné: {spatne}")

nalezeno = len(re.findall(r"(?mi)^\s*\w*ticks\w*\s+BIGINT", schema_pg))
check(nalezeno >= 3, f"a je jich tam dost ({nalezeno})")

# Totéž platí pro bajty. INTEGER je v PostgreSQL čtyřbajtový, takže se do
# něj vejde 2,1 GB - knihovna ani volné místo se tam nevejdou. SQLite tuhle
# past nemá (INTEGER je tam osmibajtový), takže se pozná až na Postgresu.
BAJTOVE = ("size_bytes", "velikost", "volne_misto", "bitrate")
spatne_bajty = [
    radek.strip()
    for radek in schema_pg.splitlines()
    if re.match(r"^\s*(" + "|".join(BAJTOVE) + r")\s+", radek)
    and "BIGINT" not in radek.upper()
]
check(not spatne_bajty, f"bajtové sloupce jsou BIGINT; chybné: {spatne_bajty}")
check(len([r for r in schema_pg.splitlines()
           if re.match(r"^\s*(" + "|".join(BAJTOVE) + r")\s+", r)]) >= 6,
      "a hledá se jich dost")

spatne_migrace = [
    f"{tabulka}.{sloupec}"
    for tabulka, sloupce in db.MIGRATIONS.items()
    for sloupec, typ in sloupce.items()
    if "ticks" in sloupec and "BIGINT" not in typ.upper()
]
check(not spatne_migrace, f"i v migracích; chybné: {spatne_migrace}")

# Sloupec uz jednou zalozeny se migraci neopravi - na to je TYPE_FIXES.
check(("playback", "media_runtime_ticks") in db.TYPE_FIXES,
      "existující sloupec se opraví přes TYPE_FIXES")
check(all("BIGINT" in typ.upper() or "TEXT" in typ.upper()
          for typ in db.TYPE_FIXES.values()),
      "a opravy míří na rozumné typy")

# ---------------------------------------------------------------------------
# PostgreSQL chce ve WHEN pravdivostní hodnotu, ne číslo
# ---------------------------------------------------------------------------
#
# `CASE WHEN ? THEN ...` s parametrem 1/0 na SQLite funguje (nenula = pravda),
# ale PostgreSQL odmítne: "argument of CASE/WHEN must be type boolean, not
# type smallint". Zase past, kterou vývoj na SQLite neukáže.
#
# Řešení není psát `? = 1`, ale rozhodnout se v Pythonu a do SQL poslat
# rovnou výslednou hodnotu - SQL má ukládat, ne rozhodovat.

print()
print("--- CASE WHEN s parametrem se nepoužívá ---")
import ast as _ast  # noqa: E402

nalezene = []
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    zdroj = soubor.read_text(encoding="utf-8")
    for uzel in _ast.walk(_ast.parse(zdroj)):
        if isinstance(uzel, _ast.Constant) and isinstance(uzel.value, str):
            text = uzel.value
        elif isinstance(uzel, _ast.JoinedStr):
            text = "".join(c.value if isinstance(c, _ast.Constant) else "{X}"
                           for c in uzel.values)
        else:
            continue
        # Komentář o tom psát smíme; jde o skutečný dotaz.
        bez_komentaru = "\n".join(
            radek for radek in text.splitlines() if not radek.strip().startswith("--"))
        if re.search(r"CASE\s+WHEN\s*\?", bez_komentaru, re.I):
            nalezene.append(f"{soubor.name}:{uzel.lineno}")

check(not nalezene, f"nikde v projektu; nalezeno: {nalezene}")

# Kontrola sama sebe - kdyby hledání přestalo hledat, mlčelo by.
vzorek = "UPDATE t SET a = CASE WHEN ? THEN ? ELSE a END"
check(bool(re.search(r"CASE\s+WHEN\s*\?", vzorek, re.I)),
      "kontrola takový dotaz opravdu pozná")

print()
print("--- `IS ?` se nepoužívá ---")
#
# SQLite bere `sloupec IS ?` jako porovnání, které zvládne i NULL.
# PostgreSQL zná jen `IS NULL` / `IS NOT NULL`, takže z `IS %s` je
# syntaktická chyba - a vývoj na SQLite ji neukáže.
#
# Skutečný případ: rozvržení vlastního přehledu se čte při každém
# požadavku (kvůli záložce v menu), takže to na PostgreSQL neshodilo
# jednu stránku, ale celou aplikaci.
#
# Řešení je rozhodnout se v Pythonu: "account_id IS NULL" bez parametru,
# nebo "account_id = ?" s ním.
nalezene_is = []
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    zdroj = soubor.read_text(encoding="utf-8")
    for uzel in _ast.walk(_ast.parse(zdroj)):
        if isinstance(uzel, _ast.Constant) and isinstance(uzel.value, str):
            text = uzel.value
        elif isinstance(uzel, _ast.JoinedStr):
            text = "".join(c.value if isinstance(c, _ast.Constant) else "{X}"
                           for c in uzel.values)
        else:
            continue
        # Docstring o tom psát smíme; jde o skutečný dotaz.
        if "SELECT" not in text.upper() and "DELETE" not in text.upper()                 and "UPDATE" not in text.upper():
            continue
        if re.search(r"\bIS\s+\?", text, re.I):
            nalezene_is.append(f"{soubor.name}:{uzel.lineno}")

check(not nalezene_is, f"nikde v projektu; nalezeno: {nalezene_is}")
check(bool(re.search(r"\bIS\s+\?", "DELETE FROM t WHERE a IS ?", re.I)),
      "kontrola takový dotaz opravdu pozná")

print()
print("--- řádek se nečte podle pořadí ---")
#
# PostgreSQL vraci radky jako slovniky, SQLite jako `sqlite3.Row`, ze
# ktereho jde cist obojim zpusobem. `radek[0]` tedy pri vyvoji funguje
# a u uzivatele s PostgreSQL spadne na `KeyError: 0`.
#
# Skutecny pripad: prenos jednoho nastaveni pri startu. Aplikace kvuli
# nemu vubec nenabehla - ani ne stranka, cely proces.


def _cteni_podle_poradi(zdroj: str) -> list[int]:
    """Radky, kde se ctou data z fetchone() podle poradi."""
    nalezene = []
    for uzel in _ast.walk(_ast.parse(zdroj)):
        # Jen v ramci jedne funkce - stejne jmeno jinde neni totez.
        if not isinstance(uzel, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        z_radku = set()
        for pod in _ast.walk(uzel):
            if (isinstance(pod, _ast.Assign) and isinstance(pod.value, _ast.Call)
                    and isinstance(pod.value.func, _ast.Attribute)
                    and pod.value.func.attr == "fetchone"):
                z_radku.update(cil.id for cil in pod.targets
                               if isinstance(cil, _ast.Name))
        for pod in _ast.walk(uzel):
            if not (isinstance(pod, _ast.Subscript)
                    and isinstance(pod.slice, _ast.Constant)
                    and isinstance(pod.slice.value, int)):
                continue
            primo = (isinstance(pod.value, _ast.Call)
                     and isinstance(pod.value.func, _ast.Attribute)
                     and pod.value.func.attr == "fetchone")
            pres_jmeno = (isinstance(pod.value, _ast.Name)
                          and pod.value.id in z_radku)
            if primo or pres_jmeno:
                nalezene.append(pod.lineno)
    return sorted(set(nalezene))


podle_poradi = []
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    for radek in _cteni_podle_poradi(soubor.read_text(encoding="utf-8")):
        podle_poradi.append(f"{soubor.name}:{radek}")

check(not podle_poradi, f"nikde v projektu; nalezeno: {podle_poradi}")
SPATNE = """def f(c):
    radek = c.fetchone()
    return radek[0]
"""
DOBRE = """def f(c):
    radek = c.fetchone()
    return radek["value"]
"""

# Kontrola sama sebe - kdyby hledání přestalo hledat, mlčelo by.
check(_cteni_podle_poradi(SPATNE) == [3],
      "kontrola takový zápis opravdu pozná")
check(_cteni_podle_poradi(DOBRE) == [],
      "a čtení podle jména nechá být")

# ---------------------------------------------------------------------------
# Zápisy sběrače: sedí počty parametrů a projdou přes psycopg?
# ---------------------------------------------------------------------------
#
# INSERT sběrače má přes třicet sloupců a při každém rozšíření se přidává
# ručně na tři místa (seznam sloupců, řada otazníků, n-tice hodnot). Když
# se jedno z nich zapomene, SQLite i PostgreSQL to poznají - ale až za
# běhu, tedy u uživatele. Tady se to pozná hned.

print()
print("--- zápisy sběrače ---")

zdroj_collector = (PROJECT / "jellyscope" / "collector.py").read_text(encoding="utf-8")
zapisu = 0
for uzel in _ast.walk(_ast.parse(zdroj_collector)):
    if not (isinstance(uzel, _ast.Call) and getattr(uzel.func, "attr", "") == "execute"):
        continue
    if not uzel.args or not isinstance(uzel.args[0], _ast.Constant):
        continue
    sql = uzel.args[0].value
    if not re.search(r"\b(INSERT|UPDATE)\b", sql, re.I):
        continue
    if len(uzel.args) < 2 or not isinstance(uzel.args[1], _ast.Tuple):
        continue          # dotaz bez parametrů

    zapisu += 1
    otazniku = sql.count("?")
    hodnot = len(uzel.args[1].elts)
    druh = "INSERT" if re.search(r"\bINSERT\b", sql, re.I) else "UPDATE"
    check(otazniku == hodnot,
          f"{druh} na řádku {uzel.lineno}: {otazniku} zástupných znaků, {hodnot} hodnot")

    # A jestli přeložený tvar psycopg vůbec přijme - tam se pozná
    # špatně uzávorkovaný překlad nebo zapomenuté procento.
    try:
        from psycopg._queries import PostgresQuery
        from psycopg.adapt import Transformer
    except ImportError:
        continue
    hotovy = PostgresQuery(Transformer())
    try:
        hotovy.convert(dialect.translate(sql, dialect.POSTGRES),
                       tuple([None] * otazniku))
        prosel = True
    except Exception as chyba:            # noqa: BLE001
        prosel = False
        print("       ", chyba)
    check(prosel, f"{druh} na řádku {uzel.lineno} projde přes psycopg")

check(zapisu >= 2, f"našly se oba zápisy sběrače ({zapisu})")


# ---------------------------------------------------------------------------
# Sestupné řazení podle sloupce, který smí být NULL
# ---------------------------------------------------------------------------
# SQLite považuje NULL za nejmenší hodnotu, takže u DESC skončí nakonec.
# PostgreSQL má u DESC výchozí NULLS FIRST a dá je na začátek. U dotazu,
# který bere JEDEN řádek, to není kosmetika - vrátí se jiný řádek. Přesně
# takhle by se volné místo měřilo na disku prvního nezměřeného souboru.

def _nullovatelne_sloupce() -> set[str]:
    """Sloupce ze schématu, které smí být NULL."""
    text = (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8")
    text = re.sub(r"--[^\n]*", "", text)          # komentáře pryč
    volne: set[str] = set()
    for telo in re.findall(r"CREATE TABLE[^(]*\((.*?)\n\);", text, re.S):
        for radek in telo.split("\n"):
            radek = radek.strip().rstrip(",")
            m = re.match(r"^([a-z_][a-z0-9_]*)\s+(TEXT|INTEGER|REAL|BLOB|BIGINT)",
                         radek, re.I)
            if m and not re.search(r"NOT NULL|PRIMARY KEY", radek, re.I):
                volne.add(m.group(1).lower())
    return volne


def _terminy(vyraz: str) -> list[str]:
    """Rozdělí ORDER BY na termíny; čárky uvnitř závorek nedělí."""
    terminy: list[str] = []
    hloubka = 0
    kus = ""
    for znak in vyraz:
        if znak == "(":
            hloubka += 1
        elif znak == ")":
            hloubka -= 1
        if znak == "," and hloubka == 0:
            terminy.append(kus)
            kus = ""
        else:
            kus += znak
    terminy.append(kus)
    return [t.strip() for t in terminy if t.strip()]


def _sql_z_volani(uzel: ast.Call) -> str:
    """Text dotazu z prvního argumentu - i když je poskládaný z kusů."""
    kusy: list[str] = []
    for arg in uzel.args[:1]:
        for n in ast.walk(arg):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                kusy.append(n.value)
    return " ".join(kusy)


def _rizikova_razeni(zdroj: str) -> list[str]:
    """Dotazy na jeden řádek řazené sestupně podle nullovatelného sloupce."""
    volne = _nullovatelne_sloupce()
    nalezy: list[str] = []
    for uzel in ast.walk(ast.parse(zdroj)):
        if not isinstance(uzel, ast.Call):
            continue
        jmeno = (uzel.func.attr if isinstance(uzel.func, ast.Attribute)
                 else getattr(uzel.func, "id", ""))
        if jmeno not in ("query_one", "query_value"):
            continue
        sql = _sql_z_volani(uzel)
        for vyraz in re.findall(r"ORDER BY\s+(.+?)(?:\s+LIMIT\b|$)", sql, re.I | re.S):
            for termin in _terminy(vyraz):
                if not re.search(r"\bDESC\b", termin, re.I):
                    continue
                if re.search(r"COALESCE|NULLS", termin, re.I):
                    continue
                sloupec = re.sub(r"\bDESC\b", "", termin, flags=re.I).strip()
                sloupec = sloupec.split(".")[-1].strip().lower()
                if sloupec in volne:
                    nalezy.append(f"řádek {uzel.lineno}: ORDER BY {termin.strip()}")
    return nalezy


print()
print('--- podmínka „tohle je přepočet“ projde překladem ---')
# Nese procento uvnitř řetězce. Kdyby se nezdvojilo, psycopg by ho četl
# jako začátek zástupného znaku a dotaz by spadl - a spadl by až na
# PostgreSQL, na SQLite se nic nestane.
from jellyscope import stats as _stats  # noqa: E402

for sloupec in ("play_method", "p.play_method"):
    prelozene = pg(f"SELECT 1 WHERE {_stats.je_transcode(sloupec)}")
    check("'Transcode%%'" in prelozene,
          f"procento je zdvojené ({sloupec}): {prelozene[-24:]}")
    check("ILIKE" in prelozene, f"a LIKE se přeložilo na ILIKE ({sloupec})")

# Past: kdyby se podmínka psala napevno, tenhle test by nic nehlídal.
check(_stats.je_transcode().startswith("play_method LIKE 'Transcode"),
      "podmínka má tvar, na který test spoléhá")
# A hlavně: nikde se transcode nesmí hledat přesnou shodou. Graf doručení
# ho hledá podle začátku (kvůli importu "Transcode (v:h264 a:direct)")
# a druhé pravidlo by znamenalo dvě různá čísla na jedné stránce.
presna_shoda = []
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    for cislo, radek in enumerate(soubor.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"play_method\s*=\s*'Transcode'", radek):
            presna_shoda.append(f"{soubor.name}:{cislo}")
check(not presna_shoda, f"nikde se nehledá přesnou shodou: {presna_shoda}")

print()
print("--- jeden řádek se nebere podle sloupce, který smí být NULL ---")
spatne: list[str] = []
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    for nalez in _rizikova_razeni(soubor.read_text(encoding="utf-8")):
        spatne.append(f"{soubor.name} {nalez}")
check(not spatne, "žádné rizikové řazení " + (", ".join(spatne) or "(čisto)"))

# Past: bez tohohle by test prošel i nad rozbitým kódem.
past = _rizikova_razeni(
    'db.query_one("SELECT path FROM items ORDER BY size_bytes DESC")')
check(len(past) == 1, f"a chybu umí najít ({len(past)})")
cisto = _rizikova_razeni(
    'db.query_one("SELECT path FROM items ORDER BY COALESCE(size_bytes, 0) DESC")')
check(not cisto, "COALESCE projde")
klic = _rizikova_razeni('db.query_one("SELECT id FROM items ORDER BY id DESC")')
check(not klic, "sloupec, který NULL být nesmí, projde taky")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
