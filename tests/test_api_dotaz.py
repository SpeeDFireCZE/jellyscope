# -*- coding: utf-8 -*-
r"""Filtry, řazení a limit v API - čtení dotazu a práce se seznamem.

`api_dotaz` nic neví o databázi ani o FastAPI: dostane parametry z adresy
a popis seznamů (`{seznam: {pole: typ}}`) a buď vrátí přečtený dotaz,
nebo vyhodí `ChybaApi` s kódem chyby. Proto se tu testuje bez aplikace.

Hlídá hlavně to, kvůli čemu vznikl: nesmysl v dotazu nesmí nikdy tiše
projít a vrátit data, jako by byl v pořádku.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_api_dotaz.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import api_dotaz  # noqa: E402
from jellyscope.api_dotaz import ChybaApi, Razeni, nacti  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


def k(fn):
    """Kód chyby, kterou `fn` vyhodí, nebo None, když projde."""
    try:
        fn()
    except ChybaApi as chyba:
        return chyba.kod
    return None


def chyba_z(fn) -> ChybaApi | None:
    try:
        fn()
    except ChybaApi as chyba:
        return chyba
    return None


S = {"items": {"path": "text", "plays": "number", "added_at": "time",
               "is_series": "bool", "tmdb_id": "text"}}


def jeden(parametry, znama=frozenset({"limit"}), seznamy=None):
    return nacti(list(parametry), set(znama), S if seznamy is None else seznamy)


print("--- čtení ---")
d = jeden([("path[starts_with]", "/media/xy"), ("sort", "plays,-added_at"), ("limit", "5")])
f = d.filtry[0] if d.filtry else None
check(len(d.filtry) == 1 and f.seznam == "items" and f.pole == "path"
      and f.operator == "starts_with" and f.hodnota == "/media/xy" and f.typ == "text",
      "filtr se přečte i se seznamem a typem")
check([(r.pole, r.sestupne) for r in d.razeni] == [("plays", False), ("added_at", True)],
      "řazení: bez mínusu vzestupně, s mínusem sestupně")
check(d.hodnoty == {"limit": "5"}, "obyčejné parametry zůstanou jako text")
check(d.pro("items") == (d.filtry, d.razeni) and d.ma_filtr("items")
      and not d.ma_filtr("jiny"), "pro() a ma_filtr() podle seznamu")

for spatne in ("filtr", "day"):
    chyba = chyba_z(lambda: jeden([(spatne, "x")]))
    check(chyba is not None and chyba.kod == "unknown_parameter"
          and "limit" in (chyba.povolene or []) and "sort" in (chyba.povolene or []),
          f"neznámý parametr {spatne!r} -> unknown_parameter s výčtem")
check(k(lambda: jeden([("path[x", "1")])) == "unknown_parameter",
      "pokažené závorky -> unknown_parameter")
check(k(lambda: jeden([("limit", "5"), ("limit", "6")])) == "duplicate_parameter",
      "parametr dvakrát -> duplicate_parameter")
check(k(lambda: jeden([("sort", "plays"), ("sort", "path")])) == "duplicate_parameter",
      "sort dvakrát -> duplicate_parameter")
check(k(lambda: jeden([("sort", "plays,plays")])) == "duplicate_parameter",
      "stejné pole dvakrát v sort -> duplicate_parameter")

chyba = chyba_z(lambda: jeden([("patth[contains]", "x")]))
check(chyba is not None and chyba.kod == "unknown_field"
      and sorted(chyba.povolene or []) == sorted(S["items"]),
      "neznámé pole -> unknown_field s poli seznamu")
check(k(lambda: jeden([("sort", "-patth")])) == "unknown_field", "neznámé pole v sort")
check(k(lambda: jeden([("path[like]", "x")])) == "unknown_operator", "neznámý operátor")
check(k(lambda: jeden([("plays[contains]", "1")])) == "operator_not_allowed",
      "contains na čísle -> operator_not_allowed")
check(k(lambda: jeden([("tmdb_id[gt]", "5")])) == "operator_not_allowed",
      "text, který vypadá jako číslo, je pořád text (gt nejde)")
d = jeden([("tmdb_id[equals]", "1001")])
check(d.filtry[0].hodnota == "1001", "tmdb_id[equals]=1001 projde jako text")

for klic, hodnota in (("plays[gt]", "hodně"), ("plays[gt]", "nan"), ("plays[gt]", "inf"),
                      ("added_at[gt]", "2026-13-01"), ("added_at[gt]", "2026-10-01T10:00"),
                      ("is_series[equals]", "ano"), ("path[contains]", ""),
                      ("path[contains]", "x" * 201), ("path[empty]", "možná")):
    check(k(lambda: jeden([(klic, hodnota)])) == "invalid_value",
          f"{klic}={hodnota[:12]!r} -> invalid_value")
check(jeden([("path[contains]", "x" * 200)]).filtry[0].hodnota == "x" * 200,
      "200 znaků ještě projde")
# NUL v textu PostgreSQL nepřijme ani jako parametr - dotaz by spadl na 500.
check(k(lambda: jeden([("path[contains]", "a\x00b")])) == "invalid_value",
      "NUL v hodnotě -> invalid_value")
check(k(lambda: jeden([("path[in]", "a,\x00")])) == "invalid_value", "i v in")
check(jeden([("added_at[gte]", "2026-10-01")]).filtry[0].hodnota == "2026-10-01 00:00:00",
      "samotné datum = půlnoc toho dne")
check(jeden([("added_at[lt]", "2026-10-01 18:30:00")]).filtry[0].hodnota
      == "2026-10-01 18:30:00", "datum s časem zůstane")
check(jeden([("is_series[equals]", "TRUE")]).filtry[0].hodnota is True,
      "true/false bez ohledu na velikost písmen")
check(jeden([("path[empty]", "false")]).filtry[0].hodnota is False, "empty bere true/false")
check(jeden([("plays[gte]", "1.5")]).filtry[0].hodnota == 1.5, "desetinné číslo")

check(jeden([("plays[in]", "1,2")]).filtry[0].hodnota == (1.0, 2.0),
      "in: čísla oddělená čárkou")
check(jeden([("path[in]", "a,b")]).filtry[0].hodnota == ("a", "b"), "in: texty")
check(k(lambda: jeden([("plays[in]", "1,x")])) == "invalid_value", "in: každá hodnota podle typu")
# Mezera za čárkou by text potichu nenašla („ a2" není „a2") - výsledek
# by se zúžil, aniž by to kdo poznal. Proto 400, ne tiché oříznutí.
for hodnota in ("a, b", "a ,b", " a,b", "1, 2"):
    pole = "plays[in]" if hodnota[0].isdigit() else "path[in]"
    check(k(lambda: jeden([(pole, hodnota)])) == "invalid_value",
          f"in s mezerou kolem hodnoty ({hodnota!r}) -> invalid_value")
check(k(lambda: jeden([("plays[in]", ",".join(["1"] * 51))])) == "too_many", "in: 51 hodnot")
check(len(jeden([("plays[in]", ",".join(["1"] * 50))]).filtry[0].hodnota) == 50,
      "in: 50 hodnot projde")
check(k(lambda: jeden([("plays[gte]", "1")] * 21)) == "too_many", "21 filtrů -> too_many")
check(len(jeden([("plays[gte]", "1")] * 20).filtry) == 20, "20 filtrů projde (i stejné)")
check(k(lambda: jeden([("sort", "plays,path,added_at,tmdb_id")])) == "too_many",
      "4 pole v sort -> too_many")
check(k(lambda: jeden([("sort", "")])) == "invalid_value", "prázdný sort -> invalid_value")
check(k(lambda: jeden([("sort", "plays,,path")])) == "invalid_value", "prázdné pole v sort")

print()
print("--- víc seznamů ---")
VIC = {"a": {"title": "text", "attempts": "number"}, "b": {"title": "text"}}
chyba = chyba_z(lambda: nacti([("title[contains]", "x")], set(), VIC))
check(chyba is not None and chyba.kod == "unknown_list" and chyba.povolene == ["a", "b"],
      "pole bez předpony -> unknown_list s výčtem seznamů")
check(k(lambda: nacti([("c.title[contains]", "x")], set(), VIC)) == "unknown_list",
      "neznámá předpona -> unknown_list")
check(k(lambda: nacti([("sort", "attempts")], set(), VIC)) == "unknown_list",
      "sort bez předpony -> unknown_list")
d = nacti([("a.title[contains]", "x"), ("sort", "-a.attempts")], set(), VIC)
check(d.filtry[0].seznam == "a" and d.ma_filtr("a") and not d.ma_filtr("b"),
      "a.title[contains] míří jen na seznam a")
check(d.razeni == [Razeni("a", "attempts", True, "number")],
      "sort=-a.attempts: mínus před celým jménem")
check(k(lambda: nacti([("b.attempts[gt]", "1")], set(), VIC)) == "unknown_field",
      "pole, které má jiný seznam -> unknown_field")

check(jeden([("items.path[contains]", "x")]).filtry[0].pole == "path",
      "jeden seznam: vlastní předpona projde")
check(k(lambda: jeden([("jiny.path[contains]", "x")])) == "unknown_list",
      "jeden seznam: cizí předpona -> unknown_list")

print()
print("--- adresa bez seznamu ---")
check(k(lambda: nacti([("plays[gt]", "1")], {"days"}, {})) == "not_filterable",
      "filtr na objektu -> not_filterable")
check(k(lambda: nacti([("sort", "plays")], {"days"}, {})) == "not_filterable",
      "sort na objektu -> not_filterable")
check(nacti([("days", "7")], {"days"}, {}).hodnoty == {"days": "7"}, "obyčejný parametr projde")
chyba = chyba_z(lambda: nacti([("limit", "5")], {"days"}, {}))
check(chyba is not None and chyba.kod == "unknown_parameter"
      and "sort" not in (chyba.povolene or []),
      "bez seznamu není sort ani limit mezi povolenými")

print()
print("--- přísné převody ---")


def dotaz(**hodnoty):
    return nacti(list(hodnoty.items()), set(hodnoty), {})


for spatne in ("abc", "", "0", "3651", "1e400", "1.5", "-1", " 7"):
    check(k(lambda: api_dotaz.cele_cislo(dotaz(days=spatne), "days", 30, 1, 3650))
          == "invalid_value", f"days={spatne!r} -> invalid_value")
check(api_dotaz.cele_cislo(dotaz(days="7"), "days", 30, 1, 3650) == 7, "days=7 -> 7")
check(api_dotaz.cele_cislo(dotaz(), "days", 30, 1, 3650) == 30, "chybí -> výchozí")
check(api_dotaz.cele_cislo(dotaz(), "limit", None, 1, 500) is None, "výchozí může být None")
check(api_dotaz.volba(dotaz(kind="MOVIES"), "kind", "both", ("both", "movies", "series"))
      == "movies", "volba bez ohledu na velikost písmen")
check(k(lambda: api_dotaz.volba(dotaz(kind="filmy"), "kind", "both",
                                ("both", "movies", "series"))) == "invalid_value",
      "neznámá volba -> invalid_value")
check(api_dotaz.volba(dotaz(), "kind", "both", ("both",)) == "both", "volba: chybí -> výchozí")
check(api_dotaz.cislo(dotaz(min_size_mb="10.5"), "min_size_mb", 0, 0, 1e7) == 10.5, "cislo 10.5")
for spatne in ("-5", "x", "nan", ""):
    check(k(lambda: api_dotaz.cislo(dotaz(min_size_mb=spatne), "min_size_mb", 0, 0, 1e7))
          == "invalid_value", f"min_size_mb={spatne!r} -> invalid_value")
check(api_dotaz.identifikator(dotaz(user="abc-DEF_12"), "user") == "abc-DEF_12",
      "identifikátor projde")
check(api_dotaz.identifikator(dotaz(), "user") is None, "identifikátor: chybí -> None")
for spatne in ("a" * 65, "a;b", "", "a b"):
    check(k(lambda: api_dotaz.identifikator(dotaz(user=spatne), "user")) == "invalid_value",
          f"user={spatne[:10]!r} -> invalid_value")
chyba = chyba_z(lambda: api_dotaz.cele_cislo(dotaz(days="x"), "days", 30, 1, 3650))
check(chyba is not None and chyba.parametr == "days" and chyba.status == 400,
      "chyba převodu nese jméno parametru a status 400")

print()
print("--- tělo chyby ---")
telo = ChybaApi(400, "unknown_field", "Unknown field.").telo()
check(telo == {"detail": "Unknown field.", "error": "unknown_field"},
      "bez parametru jen detail a error")
telo = ChybaApi(400, "unknown_field", "Unknown.", "x[eq]", ["a"]).telo()
check(telo.get("parameter") == "x[eq]" and telo.get("allowed") == ["a"],
      "s parametrem i výčtem")
chyba = ChybaApi(401, "missing_token", "No token.", hlavicky={"WWW-Authenticate": "Bearer"})
check(chyba.status == 401 and chyba.hlavicky == {"WWW-Authenticate": "Bearer"},
      "status a hlavičky zůstanou na výjimce")
check(api_dotaz.chyba("invalid_value", "Bad.").status == 400, "chyba() je 400")

print()
print("--- filtry ---")
T = {"radky": {"title": "text", "path": "text", "plays": "number",
               "added_at": "time", "is_series": "bool"}}
R = [
    {"id": 1, "title": "Amélie", "path": "/media/Filmy HD/Amélie.mkv", "plays": 3,
     "added_at": "2026-10-01T12:00:00.0000000Z", "is_series": False},
    {"id": 2, "title": "Duna", "path": "D:\\Filmy\\Duna.mkv", "plays": 0,
     "added_at": "2026-09-30 23:59:59", "is_series": False},
    {"id": 3, "title": None, "path": "/media/Tom & Jerry/x.mkv", "plays": None,
     "added_at": "2026-10-01", "is_series": True},
    {"id": 4, "title": "", "path": None, "plays": 1, "added_at": "včera", "is_series": None},
    {"id": 5, "title": "Kancl", "path": "/media/serialy/Kancl", "plays": 5,
     "added_at": None, "is_series": True},
]


def ids(parametry, radky=None, limit=None, popis=None):
    d = nacti(list(parametry), set(), popis or T)
    seznam = next(iter(popis or T))
    vysledek = api_dotaz.pouzij(radky if radky is not None else R, *d.pro(seznam), limit)
    return [r["id"] for r in vysledek]


for parametry, cekane in (
        ([("title[equals]", "AMÉLIE")], [1]),
        ([("title[equals]", "amélie")], [1]),
        ([("title[not]", "amélie")], [2, 3, 4, 5]),
        ([("path[contains]", "filmy")], [1, 2]),
        ([("path[not_contains]", "filmy")], [3, 4, 5]),
        ([("path[starts_with]", "/media/")], [1, 3, 5]),
        ([("path[ends_with]", ".MKV")], [1, 2, 3]),
        ([("title[in]", "duna,KANCL")], [2, 5]),
        ([("title[empty]", "true")], [3, 4]),
        ([("title[empty]", "false")], [1, 2, 5]),
        ([("plays[equals]", "0")], [2]),
        ([("plays[not]", "0")], [1, 3, 4, 5]),
        ([("plays[gt]", "1")], [1, 5]),
        ([("plays[gte]", "1")], [1, 4, 5]),
        ([("plays[lt]", "1")], [2]),
        ([("plays[lte]", "1")], [2, 4]),
        ([("plays[in]", "0,5")], [2, 5]),
        ([("plays[empty]", "true")], [3]),
        ([("added_at[gte]", "2026-10-01")], [1, 3]),
        ([("added_at[lt]", "2026-10-01")], [2]),
        ([("added_at[gt]", "2026-10-01 11:59:59")], [1]),
        ([("added_at[empty]", "true")], [4, 5]),
        ([("is_series[equals]", "true")], [3, 5]),
        ([("is_series[not]", "true")], [1, 2, 4]),
        ([("path[equals]", "/media/Filmy HD/Amélie.mkv")], [1]),
        ([("path[starts_with]", "D:\\Filmy")], [2]),
        ([("path[contains]", "Tom & Jerry")], [3]),
        ([("plays[gte]", "1"), ("plays[gte]", "3")], [1, 5]),
        ([("plays[gte]", "1"), ("plays[lte]", "3")], [1, 4]),
):
    popis = "&".join(f"{a}={b}" for a, b in parametry)
    check(ids(parametry) == cekane, f"{popis} -> {cekane} ({ids(parametry)})")

print()
print("--- řazení a limit ---")
check(ids([("sort", "plays")]) == [2, 4, 1, 5, 3], "vzestupně, prázdné na konci")
check(ids([("sort", "-plays")]) == [5, 1, 4, 2, 3], "sestupně, prázdné pořád na konci")
check(ids([("sort", "title")]) == [1, 2, 5, 3, 4],
      "text bez ohledu na velikost písmen; null i prázdný text na konci")
check(ids([("sort", "added_at")]) == [2, 3, 1, 4, 5],
      "čas v různých tvarech se řadí správně, nečitelný jako prázdný")
check(ids([]) == [1, 2, 3, 4, 5], "bez sort pořadí beze změny")

SHODA = [{"id": i, "plays": p, "added_at": a, "title": "", "path": "", "is_series": False}
         for i, p, a in ((1, 1, "2026-03-01"), (2, 0, "2026-05-01"), (3, 1, "2026-01-01"),
                         (4, 0, "2026-02-01"), (5, 0, "2026-02-01"))]
check(ids([("sort", "plays,added_at")], SHODA) == [4, 5, 2, 3, 1],
      "dvě pole: podle přehrání, při shodě podle data; při úplné shodě pořadí vstupu")
check(ids([("sort", "-plays,-added_at")], SHODA) == [1, 3, 2, 4, 5],
      "dvě pole sestupně, stabilní i tady")

# Příklad ze spec: hledané soubory jsou na konci vstupu - kdyby se limit
# použil dřív než filtr, nevrátilo by se nic.
DLOUHY = [{"id": i, "path": f"/media/jine/{i}.mkv", "plays": i % 4, "title": "",
           "added_at": f"2026-01-{1 + i % 28:02d}", "is_series": False} for i in range(27)]
DLOUHY += [{"id": 100, "path": "/media/xy/a.mkv", "plays": 1, "added_at": "2025-01-01",
            "title": "", "is_series": False},
           {"id": 101, "path": "/media/xy/b.mkv", "plays": 0, "added_at": "2025-06-01",
            "title": "", "is_series": False},
           {"id": 102, "path": "/media/XY/c.mkv", "plays": 0, "added_at": "2025-02-01",
            "title": "", "is_series": False}]
puvodni = [dict(r) for r in DLOUHY]
check(ids([("path[starts_with]", "/media/xy"), ("sort", "plays,added_at")], DLOUHY, 5)
      == [102, 101, 100],
      "5 nejméně hraných z /media/xy od nejstaršího: všechny 3, ne 0")
check(DLOUHY == puvodni, "vstupní seznam zůstal nezměněný")
check(len(ids([], DLOUHY, None)) == 30, "limit None vrátí vše")
check(ids([("sort", "-plays")], DLOUHY, 2) == [3, 7], "limit až po řazení")

print()
print("--- nápověda pro rozcestník ---")
napoveda = api_dotaz.napoveda()
check(napoveda.get("operators") == {k: list(v) for k, v in api_dotaz.OPERATORY.items()},
      "operátory podle typu")
check("sort=plays,added_at" in napoveda.get("example", ""), "příklad ze spec")
check(napoveda.get("limits") == {"filters": 20, "sort_fields": 3, "in_values": 50,
                                 "text_length": 200}, "stropy")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
