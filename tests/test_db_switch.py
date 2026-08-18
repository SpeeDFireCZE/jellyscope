# -*- coding: utf-8 -*-
"""Přepnutí databáze v Nastavení a přenos dat.

Dvě chyby, ze kterých tenhle test vznikl:

1. Po uložení PostgreSQL se formulář přepnul zpátky na SQLite. Nastavení
   se přitom uložilo správně — jen se do formuláře dávala **běžící**
   konfigurace, která je v paměti zakešovaná a mění se až restartem.

2. Přenos dat končil holým „Internal Server Error". Zakládání schématu
   v cílové databázi bylo mimo `try`, takže výjimka prošla až do webu.
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
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "switch.db")

from jellyscope import db, dbmigrate, dialect  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


base = Path(_tmp)

print("--- uložená vs. běžící konfigurace ---")
# Aplikace běží na SQLite...
sqlite_cfg = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(base / "bezici.db"))
dialect.save_config(base, sqlite_cfg)
bezici = dialect.load_config(base, "data/jellyscope.db")
check(bezici.kind == dialect.SQLITE, "výchozí stav je SQLite")

# ...a uživatel uloží PostgreSQL.
pg_cfg = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", port=5432,
                                database="js", user="u", password="p")
dialect.save_config(base, pg_cfg)

ulozena = dialect.load_config(base, "data/jellyscope.db")
check(ulozena.kind == dialect.POSTGRES,
      f"uložená konfigurace je PostgreSQL (je {ulozena.kind})")
check(ulozena.host == "db" and ulozena.database == "js",
      "uložily se i údaje o serveru")

# Tohle je jádro té chyby: `db.database_config()` drží konfiguraci
# v paměti, takže po uložení pořád vrací tu starou. Do formuláře proto
# nesmí - formulář má ukazovat, co je uložené.
check(ulozena.to_dict() != bezici.to_dict(),
      "uložená a běžící se do restartu liší (a stránka to musí umět rozlišit)")


print()
print("--- přenos dat: chyby místo Internal Server Error ---")
# Zdroj i cíl stejné - má se ošetřit, ne spadnout.
stejna = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(base / "a.db"))
vysledek = dbmigrate.copy_all(stejna, stejna)
check(vysledek["status"] == "error", "stejný zdroj i cíl skončí chybou")
check("tatáž databáze" in vysledek["message"], f"a vysvětlí proč: {vysledek['message']}")

# Nedostupný PostgreSQL jako cíl: dřív tady vyletěla výjimka ze
# zakládání schématu a uživatel dostal 500.
zdroj = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(base / "zdroj.db"))
db.init_db(zdroj)
cil = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="127.0.0.1", port=1,
                             database="neexistuje", user="u", password="p",
                             use_pool=False)
try:
    vysledek = dbmigrate.copy_all(zdroj, cil)
    spadlo = False
except Exception as exc:  # noqa: BLE001
    vysledek = {"status": f"VÝJIMKA {type(exc).__name__}", "message": str(exc)}
    spadlo = True

check(not spadlo, f"nedostupný cíl nevyhodí výjimku (dostal jsem {vysledek['status']})")
check(vysledek.get("status") == "error", "vrátí se chybový stav")
check(bool(vysledek.get("message")), f"s vysvětlením: {vysledek.get('message', '')[:90]}")


print()
print("--- rozepsané nastavení přežije test spojení ---")
# Tlačítko "Otestovat spojení" nic neukládá. Bez podržení rozepsaných
# hodnot by se formulář po každém testu vrátil k uloženému nastavení
# a uživatel by server, port, uživatele i heslo vyplňoval znovu.
from jellyscope import web  # noqa: E402

ucet = {"id": 1, "username": "test", "is_admin": 1}
web._draft_clear(ucet, "database")
check(web._draft_read(ucet, "database") is None, "bez rozepsaného se nic nevrací")

rozepsane = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="muj-server",
                                   port=5433, database="js", user="tomas",
                                   password="tajne")
web._draft_save(ucet, "database", rozepsane)
nacteno = web._draft_read(ucet, "database")
check(nacteno is not None, "rozepsané se vrátí")
check(nacteno.host == "muj-server" and nacteno.port == 5433,
      "včetně serveru a portu")
check(nacteno.password == "tajne", "a hesla, aby se nemuselo psát znovu")

# Heslo nesmí skončit v session - ta je u nás podepsaná cookie, kterou
# si prohlížeč nese s sebou.
check("_DB_DRAFT" in (PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8"),
      "rozepsané se drží v paměti procesu")
check("request.session[\"db_draft\"]" not in
      (PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8"),
      "rozepsané NEJDE do session (byla by to cookie s heslem)")

# Po druhém účtu se nesmí míchat.
jiny = {"id": 2, "username": "jiny", "is_admin": 1}
check(web._draft_read(jiny, "database") is None, "cizí účet cizí rozepsané nevidí")

# Sekce se navzájem nepletou - Jellyfin a databáze mají vlastní zásuvku.
web._draft_save(ucet, "jellyfin", {"url": "http://x", "api_key": "k"})
check(web._draft_read(ucet, "database") is not None,
      "uložení pro Jellyfin nepřepsalo rozepsanou databázi")
check(web._draft_read(ucet, "jellyfin")["url"] == "http://x",
      "a Jellyfin má svoje")

web._draft_clear(ucet, "database")
check(web._draft_read(ucet, "database") is None, "po uložení se rozepsané zahodí")
check(web._draft_read(ucet, "jellyfin") is not None,
      "zahození databáze se nedotklo Jellyfinu")
web._draft_clear(ucet, "jellyfin")


print()
print("--- rada u chybějících práv ---")
# Nejčastější zádrhel na PostgreSQL 15+: účet se připojí, ale nesmí
# zakládat tabulky. Hláška na to musí upozornit, ne jen zopakovat
# hlášku z databáze.
zdrojak = (PROJECT / "jellyscope" / "dbmigrate.py").read_text(encoding="utf-8")
check("permission denied" in zdrojak, "kód pozná chybu o právech")
check("GRANT CREATE ON SCHEMA public" in zdrojak,
      "a poradí přesně, co spustit")

# Zakládání schématu musí být uvnitř try - jinak se výjimka dostane
# až do webu jako Internal Server Error.
radky = zdrojak.splitlines()
kde_init = next(i for i, r in enumerate(radky) if "db.init_db(target)" in r)
predchozi = [r.strip() for r in radky[max(0, kde_init - 5):kde_init]]
check(any(r.startswith("try:") for r in predchozi),
      f"init_db(target) je uvnitř try (okolí: {predchozi})")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
