# -*- coding: utf-8 -*-
"""Výběr správné verze pg_dump pro zálohu PostgreSQL.

Nahlášená chyba zněla:

    pg_dump: error: server version: 17.5; pg_dump version: 12.22
    pg_dump: error: aborting because of server version mismatch

`pg_dump` umí zálohovat server **stejné nebo starší** verze, nikdy
novější. Na Ubuntu 20.04 je v PATH klient 12, kdežto server běží na 17 -
a záloha proto tiše selhávala při každém pokusu.

Podstatné přitom je, že na Debianu a Ubuntu můžou být klienti nainstalovaní
**vedle sebe ve víc verzích** (`/usr/lib/postgresql/<verze>/bin/`) a v PATH
bývá zrovna ten nejstarší. Stačí se tedy podívat vedle, než se rovnou
vzdát.

Spusteni:
    .\\.venv\\Scripts\\python.exe tests\\test_zaloha_pgdump.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "zaloha.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

# Nástroje ani server tu nejsou - podstrčíme, co se má "najít".
puvodni_dumpy = tasks.dostupne_pg_dumpy
puvodni_server = tasks._verze_serveru


def nastav(nalezene: list[tuple[int, str]], server: int) -> None:
    tasks.dostupne_pg_dumpy = lambda cesta="": list(nalezene)   # type: ignore[assignment]
    tasks._verze_serveru = lambda config: server                # type: ignore[assignment]


print("--- vybere se nejnovější, který na server stačí ---")
nastav([(17, "/usr/lib/postgresql/17/bin/pg_dump"),
        (15, "/usr/lib/postgresql/15/bin/pg_dump"),
        (12, "/usr/bin/pg_dump")], server=15)
vybrany = tasks._vyber_pg_dump(None)
check(vybrany.endswith("/17/bin/pg_dump"),
      f"novější klient server 15 zvládne ({vybrany})")

nastav([(15, "/usr/lib/postgresql/15/bin/pg_dump"),
        (12, "/usr/bin/pg_dump")], server=15)
check(tasks._vyber_pg_dump(None).endswith("/15/bin/pg_dump"),
      "stejná verze samozřejmě taky")

# Tohle je jádro věci: v PATH je starý klient, ale vedle leží novější.
nastav([(17, "/usr/lib/postgresql/17/bin/pg_dump"),
        (12, "/usr/bin/pg_dump")], server=17)
check(tasks._vyber_pg_dump(None).endswith("/17/bin/pg_dump"),
      "v PATH je starý klient, ale vedle leží ten správný")


print()
print("--- když žádný nestačí, poradí co doinstalovat ---")
nastav([(12, "/usr/bin/pg_dump")], server=17)
try:
    tasks._vyber_pg_dump(None)
    check(False, "mělo to skončit chybou")
except RuntimeError as exc:
    hlaska = str(exc)
    check("12" in hlaska and "17" in hlaska,
          "hláška jmenuje obě verze")
    check("postgresql-client-17" in hlaska,
          f"a přesný příkaz k doinstalování ({hlaska.splitlines()[1].strip()})")
    check("/usr/bin/pg_dump" in hlaska,
          "i to, co je nainstalované teď")
    check("Nastavení" in hlaska, "a že cesta jde vyplnit ručně")


print()
print("--- bez jediného nástroje ---")
nastav([], server=17)
try:
    tasks._vyber_pg_dump(None)
    check(False, "mělo to skončit chybou")
except RuntimeError as exc:
    check("se nepodařilo najít" in str(exc), f"řekne se to česky ({exc})")


print()
print("--- neznámá verze serveru zálohu neblokuje ---")
# Když se verzi zjistit nepodaří (starý server, chybějící oprávnění),
# je lepší zkusit to nejnovějším dostupným než odmítnout zálohovat.
nastav([(15, "/usr/lib/postgresql/15/bin/pg_dump"),
        (12, "/usr/bin/pg_dump")], server=0)
check(tasks._vyber_pg_dump(None).endswith("/15/bin/pg_dump"),
      "vezme se prostě ten nejnovější")

tasks.dostupne_pg_dumpy = puvodni_dumpy
tasks._verze_serveru = puvodni_server


print()
print("--- ručně nastavená cesta má přednost ---")
falesny = Path(_tmp) / "pg_dump"
falesny.write_text("#!/bin/sh\necho 'pg_dump (PostgreSQL) 16.2'\n", encoding="utf-8")
nalezene = tasks.dostupne_pg_dumpy(str(falesny))
check(len(nalezene) == 1 and nalezene[0][1] == str(falesny),
      f"vyplněná cesta vytlačí hledání ({nalezene})")

# Složka místo souboru se domyslí - stejně jako u ffprobe.
ze_slozky = tasks.dostupne_pg_dumpy(_tmp)
check(len(ze_slozky) == 1 and ze_slozky[0][1] == str(falesny),
      f"a složka místo souboru se domyslí ({ze_slozky})")


print()
print("--- na SQLite se nic z toho neřeší ---")
check(tasks.server_version() == 0,
      "verze serveru je 0, takže se sekce v Nastavení ani neukáže")
check(not db.database_config().is_postgres, "a záloha jde vestavěnou funkcí")


print()
print("--- vlastní záloha, když pg_dump není k dispozici ---")
# Klient novější verze se nedá vždycky doinstalovat: repozitář PGDG pro
# Ubuntu 20.04 už neexistuje, protože ta verze skončila podporu. Odmítnout
# v takové situaci zálohovat by bylo to nejhorší řešení - Jellyscope svoje
# tabulky zná a umí si je vyexportovat sám.
from jellyscope import demodata  # noqa: E402

demodata.seed()
cil = Path(_tmp) / "vlastni.sql"
velikost = tasks._dump_vlastni(cil)
text = cil.read_text(encoding="utf-8")

check(velikost > 0 and cil.exists(), f"soubor vznikl ({velikost} B)")
check("CREATE TABLE" in text, "obsahuje schéma")
check(text.count("INSERT INTO") > 100, f"i data ({text.count('INSERT INTO')} INSERTů)")
check(text.startswith("-- Záloha Jellyscope"), "a hlavičku, ze které je jasné, co to je")
check("psql" in text, "s návodem na obnovu")
check(text.rstrip().endswith("COMMIT;"), "celé v jedné transakci")

# Pořadí tabulek musí ctít cizí klíče, jinak obnova spadne.
poradi = [text.index(f"INSERT INTO {t} ") for t in ("items", "item_streams")
          if f"INSERT INTO {t} " in text]
check(len(poradi) < 2 or poradi[0] < poradi[1],
      "položky se vkládají dřív než jejich stopy (cizí klíč)")

# Apostrof v názvu filmu nesmí zálohu rozbít.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, is_missing, synced_at)"
        " VALUES ('apostrof', ?, 'Movie', 0, ?)",
        ("Ohnivý oheň'; DROP TABLE items; --", db.utcnow()))
tasks._dump_vlastni(cil)
text = cil.read_text(encoding="utf-8")
check("''; DROP TABLE" in text,
      "apostrof se zdvojí, takže z něj nevznikne příkaz")
check(tasks._sql_hodnota(None) == "NULL", "prázdná hodnota je NULL")
check(tasks._sql_hodnota(42) == "42", "číslo se nezabaluje do apostrofů")
check(tasks._sql_hodnota("a'b") == "'a''b'", "a text ano")


print()
print("--- pg_dump si soubor otevře sám ---")
# Původně se pg_dump podstrkoval otevřený popisovač přes přesměrování
# výstupu a v některých prostředích to selhalo na
# "could not write to output file: Bad file descriptor". S přepínačem -f
# je za soubor zodpovědný pg_dump a chyba je pak jeho vlastní, srozumitelná.
import subprocess as _subprocess  # noqa: E402

zachycene: dict[str, object] = {}
_puvodni_run = _subprocess.run


def _falesny_run(prikaz, **kwargy):  # type: ignore[no-untyped-def]
    zachycene["prikaz"] = list(prikaz)
    zachycene["kwargy"] = kwargy
    cil = Path(prikaz[prikaz.index("-f") + 1])
    cil.write_text("-- dump" + chr(10), encoding="utf-8")

    class Vysledek:
        returncode = 0
        stdout = b""
        stderr = b""

    return Vysledek()


class _Config:
    host, port, user, database, password = "h", 5432, "u", "d", "tajne"


_subprocess.run = _falesny_run                          # type: ignore[assignment]
tasks.dostupne_pg_dumpy = lambda cesta="": [(17, "/usr/lib/postgresql/17/bin/pg_dump")]
tasks._verze_serveru = lambda config: 17                # type: ignore[assignment]
cil_pg = Path(_tmp) / "pgdump.sql"
velikost_pg = tasks._dump_postgres(_Config(), cil_pg)
_subprocess.run = _puvodni_run                          # type: ignore[assignment]
tasks.dostupne_pg_dumpy = puvodni_dumpy
tasks._verze_serveru = puvodni_server

prikaz = zachycene["prikaz"]
check("-f" in prikaz and prikaz[prikaz.index("-f") + 1] == str(cil_pg),
      f"soubor se předává přepínačem -f ({' '.join(prikaz[-2:])})")
check(zachycene["kwargy"].get("stdout") is _subprocess.PIPE,
      "výstup se nepřesměrovává do otevřeného souboru")
check(zachycene["kwargy"]["env"].get("PGPASSWORD") == "tajne",
      "heslo jde proměnnou prostředí, ne v příkazové řádce")
check("tajne" not in " ".join(prikaz), "a v argumentech procesu není")
check(velikost_pg > 0, f"velikost se vrátí ({velikost_pg} B)")


print()
print("--- ukazatel průběhu u analýzy souborů ---")
# Ukazatel se u jednoho souboru zvyšoval třikrát (po převzetí semaforu,
# po analýze a po zápisu), takže se dostal až na 300 % a v rozhraní
# svítilo třeba 135 %. Jeden soubor je jeden krok.
import asyncio  # noqa: E402

from jellyscope import probe, scanner  # noqa: E402

with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lp','L',?)",
                 (db.utcnow(),))
    for cislo in range(1, 11):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, path, is_missing, synced_at)
               VALUES (?, ?, 'Movie', 'lp', ?, 0, ?)""",
            (f"p{cislo}", f"Film {cislo}", f"/media/film{cislo}.mkv", db.utcnow()))


async def _falesny_probe(cesta: str, nastroj: str) -> dict[str, object]:
    # Dva soubory schválně selžou - i ty se musí započítat právě jednou.
    if cesta.endswith(("3.mkv", "7.mkv")):
        raise probe.ProbeError("soubor nenalezen")
    return {"container": "mkv", "video_codec": "h264", "width": 1920, "height": 1080}


probe.probe_file = _falesny_probe                       # type: ignore[assignment]
probe.find_ffprobe = lambda cesta="": "/usr/bin/ffprobe"  # type: ignore[assignment]

procenta: list[int] = []
_puvodni_krok = scanner._add_progress


def _sleduj(kolik: int) -> None:
    _puvodni_krok(kolik)
    stav = scanner.progress()
    if stav and stav.get("percent") is not None:
        procenta.append(stav["percent"])


scanner._add_progress = _sleduj                          # type: ignore[assignment]
vysledek = asyncio.run(scanner.run_tech_scan(only_missing=False))
scanner._add_progress = _puvodni_krok                    # type: ignore[assignment]

check(vysledek["total"] == 10, f"analyzovalo se deset souborů ({vysledek['total']})")
check(len(procenta) == 10,
      f"a ukazatel se posunul právě desetkrát ({len(procenta)})")
check(max(procenta) == 100, f"nejvýš na sto procent ({max(procenta)})")
check(vysledek["ok"] == 8 and vysledek["failed"] == 2,
      f"chybné soubory se počítají taky ({vysledek['ok']}/{vysledek['failed']})")


print()
print("--- stažení a smazání zálohy z Nastavení ---")
# Záloha, kterou nejde dostat ze stroje ven, je k ničemu - a stará
# záloha, která se nedá smazat, zabírá místo. Obojí musí jít z prohlížeče.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "tajneheslo1", is_admin=True)
accounts.create("ctenar", "ctenarheslo1", is_admin=False)

slozka = Path(_tmp) / "zalohy"
slozka.mkdir(exist_ok=True)
db.set_setting("backup_path", str(slozka))
(slozka / "jellyscope-20260817-100000.db").write_bytes(b"SQLite format 3")
(slozka / "jellyscope-20260817-110000.sql").write_text("-- dump", encoding="utf-8")
# Cizí soubor o patro výš - ten se ven dostat nesmí.
(Path(_tmp) / "tajne.txt").write_text("SECRET_KEY=nedostupne", encoding="utf-8")

with TestClient(app) as klient:
    klient.post("/login", data={"username": "spravce", "password": "tajneheslo1"})

    stranka = klient.get("/settings?section=tasks").text
    check("backup/download" in stranka and "backup/delete" in stranka,
          "u každé zálohy jsou tlačítka")

    odpoved = klient.get("/settings/backup/download",
                         params={"name": "jellyscope-20260817-110000.sql"})
    check(odpoved.status_code == 200, f"záloha se stáhne ({odpoved.status_code})")
    check("attachment" in odpoved.headers.get("content-disposition", ""),
          "a prohlížeč ji uloží, místo aby ji zobrazil")

    # Jméno z adresy není cesta - stejná past jako u prohlížeče logu.
    for pokus in ("../tajne.txt", "..\tajne.txt", "/etc/passwd",
                  "neexistuje.db", "x.db", ""):
        odpoved = klient.get("/settings/backup/download", params={"name": pokus})
        check(odpoved.status_code == 404, f"{pokus!r} se nestáhne")

    odpoved = klient.post("/settings/backup/delete",
                          data={"name": "jellyscope-20260817-100000.db"},
                          follow_redirects=False)
    check(odpoved.status_code == 303
          and not (slozka / "jellyscope-20260817-100000.db").exists(),
          "záloha se smaže")

    klient.post("/settings/backup/delete", data={"name": "../tajne.txt"},
                follow_redirects=False)
    check((Path(_tmp) / "tajne.txt").exists(),
          "cizí soubor smazat nejde")

    print()
    print("--- obnova zálohy ---")
    # Obnova je jediná operace, která umí smazat všechno najednou. Proto
    # si Jellyscope napřed uloží současný stav - kdo si splete řádek
    # v seznamu, o data nepřijde.
    import asyncio as _asyncio  # noqa: E402

    with db.connect() as conn:
        conn.execute("DELETE FROM items")
        for ident, nazev in (("obn-a", "Film A"), ("obn-b", "Film B")):
            conn.execute(
                "INSERT INTO items (id, name, type, is_missing, synced_at)"
                " VALUES (?, ?, 'Movie', 0, ?)", (ident, nazev, db.utcnow()))

    hotova = _asyncio.run(tasks.backup_database())
    check(hotova["status"] == "ok", f"záloha stavu vznikla ({hotova.get('message', '')})")
    jmeno_zalohy = Path(hotova["file"]).name

    # Stav se změní - a obnova ho má vrátit zpátky.
    with db.connect() as conn:
        conn.execute("DELETE FROM items")
        conn.execute(
            "INSERT INTO items (id, name, type, is_missing, synced_at)"
            " VALUES ('obn-c', 'Film C', 'Movie', 0, ?)", (db.utcnow(),))

    vysledek = tasks.restore_backup(jmeno_zalohy)
    check(vysledek["status"] == "ok", f"obnova proběhla ({vysledek})")
    nazvy = {r["name"] for r in db.query_all("SELECT name FROM items")}
    check(nazvy == {"Film A", "Film B"}, f"data jsou zpátky ({sorted(nazvy)})")
    check((slozka / vysledek["safety"]).exists(),
          f"a stav před obnovou zůstal uložený ({vysledek['safety']})")

    # Cizí soubor ani nesmysl obnovit nejde.
    for pokus in ("../tajne.txt", "neexistuje.db", ""):
        odmitnuto = tasks.restore_backup(pokus)
        check(odmitnuto["status"] == "error", f"{pokus!r} se neobnoví")

    # A záloha z jiné databáze taky ne - přepsat SQLite dumpem
    # z PostgreSQL by aplikaci rozbilo.
    (slozka / "jellyscope-20260101-000000.sql").write_text("-- dump", encoding="utf-8")
    spatna = tasks.restore_backup("jellyscope-20260101-000000.sql")
    check(spatna["status"] == "error" and "PostgreSQL" in spatna["message"],
          f"záloha z jiné databáze se odmítne ({spatna['message'][:60]})")

    print()
    print("--- a čtenář nesmí ani jedno ---")
    klient.post("/logout")
    klient.post("/login", data={"username": "ctenar", "password": "ctenarheslo1"})
    stazeni = klient.get("/settings/backup/download",
                         params={"name": "jellyscope-20260817-110000.sql"})
    smazani = klient.post("/settings/backup/delete",
                          data={"name": "jellyscope-20260817-110000.sql"},
                          follow_redirects=False)
    obnova = klient.post("/settings/backup/restore",
                         data={"name": "jellyscope-20260817-110000.sql"},
                         follow_redirects=False)
    check(stazeni.status_code == 403, f"stažení zamítnuto ({stazeni.status_code})")
    check(smazani.status_code == 403, f"mazání zamítnuto ({smazani.status_code})")
    check(obnova.status_code == 403, f"obnova zamítnuta ({obnova.status_code})")
    check((slozka / "jellyscope-20260817-110000.sql").exists(),
          "a záloha zůstala na místě")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
