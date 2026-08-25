"""Podpora dvou databází: SQLite a PostgreSQL.

Aplikace je psaná v **SQLite dialektu** – to je zdrojový tvar, stejně jako
je čeština zdrojový jazyk rozhraní. Když běží nad PostgreSQL, projde každý
dotaz cestou přes `translate()` a přeloží se.

## Proč překlad a ne psát oba dialekty ručně

V projektu je přes sto SQL dotazů. Psát je dvakrát znamená dvakrát tolik
míst, kde může vzniknout chyba, a jistotu, že se jednou opraví jen jedna
verze. Překlad drží **jeden zdroj pravdy**.

Aby to bylo bezpečné, musí platit jedno: překládá se **uzavřená množina**
vzorů, které sami píšeme. Není to obecný převodník SQL a nikdy nebude –
je to slovník pro několik konkrétních konstrukcí, o kterých víme, že je
používáme.

Konstrukce, které se v projektu **nepoužívají**, protože přenositelné
nejsou: `GROUP_CONCAT` (voláme přes `group_concat()` níže), `MAX(a, b)`
jako běžná funkce (přes `greatest()`), alias sloupce v `HAVING`
a `INSERT OR REPLACE` (píšeme `ON CONFLICT`, kterému rozumí obojí).

## Co se překládá

| SQLite                              | PostgreSQL                          |
|-------------------------------------|-------------------------------------|
| `?`                                 | `%s`                                |
| `datetime('now', ?)`                | `now() + interval`                  |
| `date(x, 'localtime')`              | převod časové zóny + `::date`       |
| `strftime('%H', x, 'localtime')`    | `EXTRACT(HOUR FROM …)`              |
| `julianday(a) - julianday(b)`       | `EXTRACT(EPOCH FROM …)`             |
| `LIKE`                              | `ILIKE` (SQLite hledá bez ohledu na velikost písmen) |
| `char(10)`                          | `chr(10)`                           |
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

SQLITE = "sqlite"
POSTGRES = "postgres"


# ---------------------------------------------------------------------------
# Nastavení databáze
#
# Tohle jediné nastavení nemůže být v databázi - potřebujeme ho, abychom
# se k ní vůbec připojili. Klasický problém slepice a vejce. Řeší se
# malým souborem vedle databáze, který zapisuje formulář v Nastavení.
# ---------------------------------------------------------------------------

@dataclass
class DatabaseConfig:
    kind: str = SQLITE
    # SQLite
    path: str = "data/jellyscope.db"
    # PostgreSQL
    host: str = "localhost"
    port: int = 5432
    database: str = "jellyscope"
    user: str = "jellyscope"
    password: str = field(default="", repr=False)
    # Zásobník spojení. Zapnutý je rychlejší, ale potřebuje navíc knihovnu
    # psycopg_pool - a ta se ne vždycky dá doinstalovat. Kdo ji nemá,
    # tohle vypne a aplikace si spojení navazuje po jednom jako dřív.
    use_pool: bool = True

    @property
    def is_postgres(self) -> bool:
        return self.kind == POSTGRES

    def describe(self) -> str:
        """Popis pro uživatele - nikdy neobsahuje heslo."""
        if self.is_postgres:
            return f"PostgreSQL {self.user}@{self.host}:{self.port}/{self.database}"
        return f"SQLite {self.path}"

    def to_dict(self, include_password: bool = True) -> dict[str, Any]:
        data = {
            "kind": self.kind,
            "path": self.path,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "use_pool": self.use_pool,
        }
        if include_password:
            data["password"] = self.password
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatabaseConfig":
        kind = str(data.get("kind", SQLITE)).lower()
        return cls(
            kind=kind if kind in (SQLITE, POSTGRES) else SQLITE,
            path=str(data.get("path") or "data/jellyscope.db"),
            host=str(data.get("host") or "localhost"),
            port=int(data.get("port") or 5432),
            database=str(data.get("database") or "jellyscope"),
            user=str(data.get("user") or "jellyscope"),
            password=str(data.get("password") or ""),
            # Starý soubor tenhle klíč nemá - tam platí zapnuto. Kdyby
            # knihovna chyběla, aplikace stejně spadne zpátky na spojení
            # po jednom, takže výchozí "zapnuto" nikomu nic nerozbije.
            use_pool=bool(data.get("use_pool", True)),
        )


def config_path(base_dir: Path) -> Path:
    return base_dir / "data" / "database.json"


def load_config(base_dir: Path, fallback_sqlite_path: str) -> DatabaseConfig:
    """Načte nastavení databáze ze souboru; když není, použije SQLite."""
    path = config_path(base_dir)
    if path.exists():
        try:
            return DatabaseConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            # Poškozený soubor nesmí znamenat, že aplikace nenastartuje.
            # Spadneme zpátky na SQLite, kde data skoro jistě jsou.
            pass
    return DatabaseConfig(kind=SQLITE, path=fallback_sqlite_path)


def save_config(base_dir: Path, config: DatabaseConfig) -> None:
    """Uloží výběr databáze do `data/database.json`.

    Soubor dostane práva 600, protože u PostgreSQL je v něm **heslo
    k databázi**, a to čitelně - jinak by se s ním aplikace nepřihlásila.
    Stejná úvaha jako u `data/secret_key` v config.py: co je citlivé, ať
    si přečte jen vlastník. Na Windows to `chmod` neumí, tam soubor chrání
    přístup ke složce.
    """
    path = config_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Pomocníky pro psaní přenositelného SQL
# ---------------------------------------------------------------------------

def group_concat(dialect: str, expression: str, separator: str = "','") -> str:
    """Spojení hodnot ve skupině do jednoho řetězce.

    SQLite tomu říká GROUP_CONCAT, PostgreSQL string_agg a liší se
    i v pořadí argumentů. Nechceme to překládat regulárním výrazem –
    výraz uvnitř může obsahovat další závorky a to se hlídá špatně.
    Proto se tahle funkce volá při skládání dotazu.
    """
    if dialect == POSTGRES:
        return f"string_agg({expression}, {separator})"
    return f"GROUP_CONCAT({expression}, {separator})"


def group_concat_distinct(dialect: str, expression: str) -> str:
    if dialect == POSTGRES:
        return f"string_agg(DISTINCT {expression}, ',')"
    return f"GROUP_CONCAT(DISTINCT {expression})"


def greatest(dialect: str, first: str, second: str) -> str:
    """Větší ze dvou hodnot.

    SQLite používá dvouargumentové MAX(), PostgreSQL GREATEST().
    Překládat to nejde: `MAX` je v obou zároveň agregační funkce
    a rozlišit je podle počtu argumentů by šlo jen s parserem.
    """
    if dialect == POSTGRES:
        return f"GREATEST({first}, {second})"
    return f"MAX({first}, {second})"


# ---------------------------------------------------------------------------
# Překlad SQL
# ---------------------------------------------------------------------------

# Převod textového sloupce na místní čas. Časy ukládáme jako text v UTC,
# takže se musí nejdřív přetypovat a pak posunout do zóny serveru.
def _local(column: str) -> str:
    return (f"(({column})::timestamp AT TIME ZONE 'UTC' "
            f"AT TIME ZONE current_setting('TIMEZONE'))")


_COLUMN = r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"

# Formát, ve kterém ukládáme čas. Výsledek převodu musí být zase **text**,
# protože sloupce jsou textové. Kdyby překlad vrátil `timestamp`, PostgreSQL
# by porovnání `text >= timestamp` odmítl - na rozdíl od SQLite, které si
# typy domýšlí.
_TS = "'YYYY-MM-DD HH24:MI:SS'"

# Tato pravidla se aplikují na CELÝ dotaz, ne jen mimo řetězcové literály.
# Musí to tak být: vzor `datetime('now', ?)` sám obsahuje literál `'now'`,
# takže by ho hledání "jen mimo literály" nikdy nenašlo. Je to bezpečné,
# protože tyhle konkrétní tvary uvnitř dat nikdy nepíšeme.
_FUNCTION_RULES: list[tuple[re.Pattern[str], Any]] = [
    # datetime('now', ?) -> aktuální UTC čas posunutý o interval, jako text
    (re.compile(r"datetime\(\s*'now'\s*,\s*\?\s*\)"),
     lambda m: f"to_char((now() AT TIME ZONE 'UTC') + (?)::interval, {_TS})"),

    # date('now', '-60 days') -> totéž, ale jen datum a s pevným intervalem
    (re.compile(r"\bdate\(\s*'now'\s*,\s*'([^']*)'\s*\)"),
     lambda m: ("to_char((now() AT TIME ZONE 'UTC') + interval "
                f"'{m.group(1)}', 'YYYY-MM-DD')")),

    # (julianday('now') - julianday(?)) * 24 * 60  -> minuty
    (re.compile(r"\(\s*julianday\(\s*'now'\s*\)\s*-\s*julianday\(\s*\?\s*\)\s*\)"
                r"\s*\*\s*24\s*\*\s*60"),
     lambda m: "(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - (?)::timestamp)) / 60)"),

    # strftime('%w' / '%H', sloupec, 'localtime')
    (re.compile(r"strftime\(\s*'%w'\s*,\s*" + _COLUMN + r"\s*,\s*'localtime'\s*\)"),
     lambda m: f"EXTRACT(DOW FROM {_local(m.group(1))})"),
    (re.compile(r"strftime\(\s*'%H'\s*,\s*" + _COLUMN + r"\s*,\s*'localtime'\s*\)"),
     lambda m: f"EXTRACT(HOUR FROM {_local(m.group(1))})"),

    # date(sloupec, 'localtime')
    (re.compile(r"\bdate\(\s*" + _COLUMN + r"\s*,\s*'localtime'\s*\)"),
     lambda m: f"{_local(m.group(1))}::date"),

    (re.compile(r"\bchar\(\s*10\s*\)"), lambda m: "chr(10)"),
]

# Tohle je naopak jediné slovo bez uvozovek, takže se hledá jen mimo
# literály - aby se nepřepsalo uvnitř textu, kdyby tam někdy bylo.
#
# Proč vůbec: SQLite hledá přes LIKE bez ohledu na velikost písmen,
# PostgreSQL ne. Aby se vyhledávání chovalo stejně, používáme tam ILIKE.
_LIKE = re.compile(r"\bLIKE\b")


def _split_literals(sql: str) -> Iterator[tuple[bool, str]]:
    """Rozdělí SQL na části mimo a uvnitř textových literálů.

    Bez tohohle rozdělení by se nahrazování dotklo i textu uvnitř
    uvozovek – a z `'%cs%'` by se stal nesmysl. Zdvojená uvozovka
    uvnitř literálu (`''`) je v SQL escape, takže se přeskočí.
    """
    index = 0
    length = len(sql)
    while index < length:
        start = sql.find("'", index)
        if start == -1:
            yield False, sql[index:]
            return

        yield False, sql[index:start]

        end = start + 1
        while end < length:
            if sql[end] == "'":
                if end + 1 < length and sql[end + 1] == "'":
                    end += 2
                    continue
                break
            end += 1

        yield True, sql[start:end + 1]
        index = end + 1


def translate(sql: str, dialect: str) -> str:
    """Přeloží dotaz z našeho SQLite dialektu do cílové databáze.

    Pořadí kroků není libovolné a stojí za to ho číst pozorně – dvakrát
    se tu dá udělat chyba, která se projeví až za běhu.
    """
    if dialect != POSTGRES:
        return sql

    # 1. Funkce. Na celý dotaz, protože vzory samy obsahují uvozovky.
    for pattern, replacement in _FUNCTION_RULES:
        sql = pattern.sub(replacement, sql)

    # 2. LIKE -> ILIKE, ale jen mimo řetězcové literály.
    sql = "".join(
        chunk if is_literal else _LIKE.sub("ILIKE", chunk)
        for is_literal, chunk in _split_literals(sql)
    )

    # 3. Procenta. psycopg čte `%` jako začátek zástupného znaku, takže
    #    každé skutečné procento (třeba v `LIKE '%cs%'`) musí být zdvojené.
    #    MUSÍ se to udělat dřív, než z otazníků vyrobíme `%s` - jinak
    #    bychom zdvojili i ta nová.
    sql = sql.replace("%", "%%")

    # 4. Otazníky na %s - zase jen mimo literály, aby se nepřepsal
    #    otazník uvnitř textu.
    return "".join(
        chunk if is_literal else chunk.replace("?", "%s")
        for is_literal, chunk in _split_literals(sql)
    )
