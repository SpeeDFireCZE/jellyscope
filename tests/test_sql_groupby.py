# -*- coding: utf-8 -*-
"""Agregační dotazy musí projít i na PostgreSQL.

SQLite má úlevu, kterou skoro nikdo nezná: v dotazu s `GROUP BY` smí být
ve výběru i sloupec, který se neseskupuje ani neagreguje. Hodnotu si pak
vezme z **libovolného** řádku skupiny. PostgreSQL to odmítne:

    ERROR: column "i.audio_languages" must appear in the GROUP BY clause
           or be used in an aggregate function

Takový dotaz tedy na SQLite roky funguje a v okamžiku, kdy někdo přepne
databázi na PostgreSQL, spadne celá stránka na "internal server error" —
přesně tohle se stalo záložce Jazyky.

Zkontrolovat se to dá jen čtením dotazů, ne jejich spuštěním: bez
PostgreSQL serveru se ta chyba nikdy neprojeví. Proto tenhle test.

Nehledá obecně platné SQL — hledá jednu konkrétní past, o které víme,
že se do projektu vloudí snadno a projeví se pozdě.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


AGREGACE = re.compile(
    r"\b(SUM|COUNT|AVG|MIN|MAX|TOTAL|GROUP_CONCAT|STRING_AGG)\s*\(", re.I)

# Slova, která ve výběru nejsou odkaz na sloupec.
KLICOVA = {
    "case", "when", "then", "else", "end", "as", "and", "or", "not", "null",
    "is", "in", "like", "cast", "integer", "real", "text", "distinct", "on",
    "coalesce", "select", "from", "where", "group", "by", "order", "limit",
    "desc", "asc", "over", "partition", "join", "left", "inner", "outer",
}


def rozdel_cárkami(text: str) -> list[str]:
    """Rozdělí seznam na položky - čárky uvnitř závorek se nepočítají."""
    kusy: list[str] = []
    hloubka = 0
    akt = ""
    for znak in text:
        if znak == "(":
            hloubka += 1
        elif znak == ")":
            hloubka -= 1
        if znak == "," and hloubka == 0:
            kusy.append(akt)
            akt = ""
        else:
            akt += znak
    if akt.strip():
        kusy.append(akt)
    return [k.strip() for k in kusy if k.strip()]


def alias_a_vyraz(polozka: str) -> tuple[str, str]:
    """Oddělí `výraz AS alias`. Hlídá AS uvnitř závorek (CAST(x AS INTEGER))."""
    hloubka = 0
    for shoda in re.finditer(r"\s+AS\s+", polozka, re.I):
        hloubka = (polozka[:shoda.start()].count("(")
                   - polozka[:shoda.start()].count(")"))
        if hloubka == 0:
            return polozka[:shoda.start()].strip(), polozka[shoda.end():].strip()
    return polozka.strip(), ""


def sloupce(vyraz: str) -> set[str]:
    """Odkazy na sloupce ve výrazu - bez klíčových slov, čísel a řetězců."""
    bez_retezcu = re.sub(r"'[^']*'", " ", vyraz)
    nalezene = re.findall(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?",
                          bez_retezcu)
    return {n for n in nalezene
            if n.lower() not in KLICOVA and not re.match(r"^\d", n)}


def problemy_v_dotazu(sql: str) -> list[str]:
    """Vrátí položky výběru, které by PostgreSQL odmítl."""
    jednoradkove = " ".join(re.sub(r"--[^\n]*", " ", sql).split())
    if not re.search(r"\bGROUP BY\b", jednoradkove, re.I):
        return []

    vyber = re.search(r"\bSELECT\b(.*?)\bFROM\b", jednoradkove, re.I)
    skupina = re.search(r"\bGROUP BY\b(.*?)(?:\bORDER BY\b|\bHAVING\b|\bLIMIT\b|$)",
                        jednoradkove, re.I)
    if not vyber or not skupina:
        return []

    seskupene: set[str] = set()
    for polozka in rozdel_cárkami(skupina.group(1)):
        seskupene.add(polozka.strip().lower())
        seskupene |= {s.lower() for s in sloupce(polozka)}

    nalezene: list[str] = []
    for polozka in rozdel_cárkami(vyber.group(1)):
        vyraz, alias = alias_a_vyraz(polozka)

        if AGREGACE.search(vyraz):
            continue                       # agregace je vždycky v pořádku
        if re.search(r"\bSELECT\b", vyraz, re.I):
            continue                       # poddotaz má vlastní pravidla
        if alias and alias.lower() in seskupene:
            continue                       # seskupuje se podle aliasu
        if vyraz.lower() in seskupene:
            continue                       # seskupuje se podle celého výrazu

        # Zbytek je v pořádku jen tehdy, když každý sloupec, na který
        # sahá, je sám seskupený (třeba `i.runtime_ticks / 600.0`).
        chybejici = {s for s in sloupce(vyraz) if s.lower() not in seskupene}
        if chybejici:
            nalezene.append(f"{polozka.strip()}   (chybí: {', '.join(sorted(chybejici))})")

    return nalezene


print("--- kontrola sama sebe ---")
# Kdyby kontrola přestala hledat, mlčela by a tvářila se, že je vše v pořádku.
SPATNY = """
    SELECT COALESCE(i.series_name, i.name) AS label, i.audio_languages,
           SUM(p.watched_seconds) AS hours
      FROM playback p JOIN items i ON i.id = p.item_id
     GROUP BY label
"""
DOBRY = """
    SELECT COALESCE(i.series_name, i.name) AS label,
           MAX(i.audio_languages) AS audio_languages,
           SUM(p.watched_seconds) AS hours
      FROM playback p JOIN items i ON i.id = p.item_id
     GROUP BY label
"""
check(len(problemy_v_dotazu(SPATNY)) == 1, "holý sloupec kontrola najde")
check(problemy_v_dotazu(DOBRY) == [], "stejný dotaz s MAX() už projde")
check(problemy_v_dotazu("SELECT a, b FROM t") == [], "dotaz bez GROUP BY se neřeší")
check(problemy_v_dotazu(
    "SELECT CAST(strftime('%H', x) AS INTEGER) AS hour, COUNT(*) AS n"
    " FROM t GROUP BY hour") == [],
    "AS uvnitř CAST() nezmate hledání aliasu")


MARKER = "__VYRAZ__"


def text_dotazu(uzel: ast.AST) -> str | None:
    """Text SQL z uzlu — obyčejný řetězec i f-string.

    Část dotazů se skládá f-stringem, protože se v nich liší funkce podle
    dialektu (`group_concat_distinct`). Kdyby se f-stringy přeskakovaly,
    kontrola by mlčky vynechala právě ty nejzajímavější dotazy — mimo jiné
    ten, kvůli kterému tenhle test vznikl. Dosazované výrazy nahradíme
    značkou; položky, které ji obsahují, se pak posoudit nedají, ale
    zbytek dotazu se zkontroluje normálně.
    """
    if isinstance(uzel, ast.Constant) and isinstance(uzel.value, str):
        return uzel.value
    if isinstance(uzel, ast.JoinedStr):
        kusy = []
        for cast in uzel.values:
            if isinstance(cast, ast.Constant) and isinstance(cast.value, str):
                kusy.append(cast.value)
            else:
                kusy.append(MARKER)
        return "".join(kusy)
    return None


print()
print("--- všechny dotazy v projektu ---")
celkem = 0
dosazovane = 0
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    strom = ast.parse(soubor.read_text(encoding="utf-8"))
    for uzel in ast.walk(strom):
        sql = text_dotazu(uzel)
        if sql is None or "GROUP BY" not in sql.upper():
            continue
        celkem += 1
        if MARKER in sql:
            dosazovane += 1
        # Položky s dosazeným výrazem posoudit nejdou - nevíme, co v nich
        # bude. Ostatní ano.
        nalezene = [p for p in problemy_v_dotazu(sql) if MARKER not in p]
        for popis in nalezene:
            print(f"       {soubor.name}:{uzel.lineno}  {popis}")
        check(not nalezene, f"{soubor.name}:{uzel.lineno}")

check(celkem >= 5, f"našlo se dost agregačních dotazů ({celkem})")
check(dosazovane >= 1, f"kontrolují se i skládané f-stringem ({dosazovane})")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
