# -*- coding: utf-8 -*-
r"""Ukázková data nestárnou: po dni se vyrobí znovu, posunutá k dnešku.

Data se generují k okamžiku seedu a veřejná ukázka běží týdny. Bez
obnovy se „posledních 30 dnů" den po dni vyprazdňovalo, až z grafů zbyla
čára. Teď se pamatuje, kdy seed proběhl, a po dni se data vyrobí znovu -
stejný tvar (pevné `random.seed`), jen k dnešku. Účet a nastavení ukázky
zůstávají.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_demo_obnova.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "1"

from jellyscope import accounts, config, db, demodata  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def pocet(tabulka: str) -> int:
    return int(db.query_value(f"SELECT COUNT(*) FROM {tabulka}"))


def nejnovejsi_prehravani() -> datetime:
    return datetime.strptime(
        str(db.query_value("SELECT MAX(started_at) FROM playback WHERE is_active = 0")),
        db.TIME_FORMAT).replace(tzinfo=timezone.utc)


db.init_db()

print("--- první seed ---")
prvni = demodata.pripravit(tichy=True)
check(prvni["items"] > 0 and prvni["plays"] > 0, f"data vznikla ({prvni})")
check(demodata.stari_hodin() is not None and demodata.stari_hodin() < 1,
      "a pamatuje se, kdy")
check(not demodata.je_zastarale(), "čerstvá data nejsou zastaralá")
znovu = demodata.pripravit(tichy=True)
check(znovu == {"items": 0, "plays": 0, "users": 0},
      "druhé spuštění týž den nic nevyrábí")
prehravani = pocet("playback")
titulu = pocet("items")
lidi = pocet("users")

print()
print("--- po třech dnech jsou data stará ---")
db.set_setting("ui_language", "en")
db.set_setting(demodata.SEED_KLIC,
               (datetime.now(timezone.utc) - timedelta(days=3)).strftime(db.TIME_FORMAT))
db.forget_settings()
# Predstirame, ze cas ubehl: posuneme vsechna prehravani o tri dny zpet -
# presne to, co s nimi udela skutecne plynouci cas.
with db.connect() as conn:
    conn.execute("UPDATE playback SET started_at = datetime(started_at, '-3 days')")
    conn.commit()
pred = nejnovejsi_prehravani()
check(demodata.je_zastarale(), "po třech dnech je to zastaralé")
check((datetime.now(timezone.utc) - pred) > timedelta(days=2),
      f"a nejnovější přehrávání je opravdu staré ({pred:%Y-%m-%d})")

obnoveno = demodata.pripravit(tichy=True)
check(obnoveno["plays"] > 0, "spuštění data vyrobí znovu")
check(pocet("playback") == prehravani and pocet("items") == titulu
      and pocet("users") == lidi,
      f"stejný tvar: {pocet('playback')} přehrávání, {pocet('items')} titulů, {pocet('users')} lidí")
po = nejnovejsi_prehravani()
check((datetime.now(timezone.utc) - po) < timedelta(days=1),
      f"a nejnovější přehrávání je zase dnes ({po:%Y-%m-%d})")
check(not demodata.je_zastarale(), "razítko je čerstvé")
check(accounts.get_by_name("demo") is not None, "účet demo přežil")
check(db.get_setting("ui_language", "") == "en", "nastavení přežilo")
check(pocet("accounts") == 1, "účty se nemnoží")

print()
print("--- stará ukázka bez razítka se obnoví taky ---")
db.set_setting(demodata.SEED_KLIC, "")
db.forget_settings()
check(demodata.je_zastarale(), "bez razítka = zastaralé (ukázka z doby před obnovou)")
check(demodata.pripravit(tichy=True)["plays"] > 0, "a vyrobí se znovu")

print()
print("--- na pozadí to hlídá úloha ukázky ---")
zdroj = (PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8")
check("demodata.obnovuj()" in zdroj, "web.py ji v ukázkovém režimu spouští")
check(hasattr(demodata, "obnovuj"), "a existuje")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
