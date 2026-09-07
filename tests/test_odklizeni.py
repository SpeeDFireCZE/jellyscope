# -*- coding: utf-8 -*-
r"""Odklízení historie: co se smaže, co se nesmí smazat a co to řekne dopředu.

Mazání dat je jediná operace, kterou nejde vzít zpět, takže tenhle test
hlídá hlavně to, co se stát NESMÍ:

* **Bez zapnutí se nemaže nic.** Ani když je hranice nastavená a data
  jsou stará. Kdo o odklízení nepožádal, nesmí o nic přijít jen tím, že
  aktualizoval.
* **Právě běžící relace zůstane.** Patří sběrači, který ji má rozdělanou;
  smazat ji znamená ji za deset vteřin založit znovu, jen bez začátku.
* **Číslo v nastavení sedí s tím, co se doopravdy smaže.** Náhled, který
  ukazuje o tisíc míň, je horší než žádný.
* **Zapomenutí diváka se týká jen jeho.** Nikoho jiného se nedotkne.

Spuštění:
    .\\.venv\\Scripts\\python.exe tests\\test_odklizeni.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "odklizeni.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, odklizeni, tasks  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


def relace(cislo: int, pred_dny: int, uzivatel: str = "u1",
           aktivni: int = 0) -> None:
    zacatek = datetime.now(timezone.utc) - timedelta(days=pred_dny)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, started_at, last_seen_at, watched_seconds,"
            " paused_seconds, is_paused, is_active)"
            " VALUES (?,?,?,'film','Duna',?,?,3600,0,0,?)",
            (f"s{cislo}", uzivatel, f"Divák {uzivatel}",
             zacatek.strftime(db.TIME_FORMAT),
             zacatek.strftime(db.TIME_FORMAT), aktivni))
        conn.commit()


def kolik() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM playback"))


db.init_db()

print("--- výchozí stav: vypnuto ---")
# Tohle je ta nejdulezitejsi vlastnost cele funkce.
check(odklizeni.zapnuto() is False, "odklízení je po instalaci vypnuté")
check(odklizeni.retence_dnu() == 365, f"a hranice je rok ({odklizeni.retence_dnu()} dní)")
check(db.get_setting("task_purge_enabled", "?") == "0",
      "úloha samotná je vypnutá taky")

# Deset relaci: pet starych, ctyri cerstve, jedna stara ale ZIVA.
for cislo in range(1, 6):
    relace(cislo, pred_dny=400)
for cislo in range(6, 10):
    relace(cislo, pred_dny=10)
relace(10, pred_dny=500, aktivni=1)
check(kolik() == 10, f"v databázi je deset relací ({kolik()})")

print()
print("--- náhled řekne, co by odešlo ---")
prehled = odklizeni.prehled()
check(prehled["celkem"] == 10, f"počítá celou historii ({prehled['celkem']})")
check(prehled["odejde"] == 5,
      f"a že by při ročí hranici odešlo pět ({prehled['odejde']})")

# Nevakuovost: jina hranice musi dat jine cislo.
check(odklizeni.prehled(dnu=5)["odejde"] == 9,
      f"při pětidenní hranici devět ({odklizeni.prehled(dnu=5)['odejde']})")
check(odklizeni.prehled(dnu=3650)["odejde"] == 0,
      "a při desetileté nic")

print()
print("--- vypnuté odklízení nemaže ---")
vysledek = asyncio.run(tasks.TASKS["purge"].runner())
check(kolik() == 10, f"úloha při vypnutém odklízení nesmazala nic ({kolik()})")
check(vysledek.get("smazano") == 0, "a říká to i ve výsledku")

print()
print("--- zapnuté odklízení maže, ale jen staré a doběhlé ---")
db.set_setting("task_purge_enabled", "1")
db.forget_settings()
check(odklizeni.zapnuto() is True, "odklízení je zapnuté")

vysledek = asyncio.run(tasks.TASKS["purge"].runner())
check(vysledek["smazano"] == 5, f"smazalo pět relací ({vysledek['smazano']})")
check(kolik() == 5, f"a v databázi zbylo pět ({kolik()})")

zive = db.query_one("SELECT COUNT(*) AS pocet FROM playback WHERE is_active = 1")
check(int(zive["pocet"]) == 1,
      "půl roku stará ŽIVÁ relace zůstala - patří sběrači")

# Druhy beh uz nema co delat.
check(asyncio.run(tasks.TASKS["purge"].runner())["smazano"] == 0,
      "druhý běh nemá co mazat")

print()
print("--- zapomenutí diváka ---")
for cislo in range(20, 23):
    relace(cislo, pred_dny=10, uzivatel="u2")
check(kolik() == 8, f"přibyl druhý divák ({kolik()} relací)")

with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u2', 'Karel')")
    conn.commit()

vysledek = odklizeni.zapomen_uzivatele("u2")
check(vysledek["smazano"] == 3, f"smazaly se tři jeho relace ({vysledek['smazano']})")
check(vysledek["jmeno"] == "Karel", "a hláška zná jeho jméno")
check(kolik() == 5, f"ostatních se to nedotklo ({kolik()})")
check(db.query_one("SELECT name FROM users WHERE id = 'u2'") is not None,
      "účet zůstává - ten je v Jellyfinu, ne u nás")
check(odklizeni.zapomen_uzivatele("u2")["smazano"] == 0,
      "podruhé už není co zapomenout")
check(odklizeni.zapomen_uzivatele("")["smazano"] == 0,
      "prázdné id nesmaže nic")

print()
print("--- v nabídce jsou jen diváci, po kterých něco zůstalo ---")
seznam = odklizeni.divaci()
check([r["user_id"] for r in seznam] == ["u1"],
      f"zbyl jediný divák ({[r['user_id'] for r in seznam]})")

print()
print("--- stránka ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/settings?section=tasks")
    check(stranka.status_code == 200, "Úlohy se načtou")
    html = stranka.text
    check('name="history_retention_days"' in html, "je tam pole s hranicí")
    check("Odklízení historie" in html, "i nadpis skupiny")
    check('action="/settings/historie/zapomen"' in html,
          "a formulář na zapomenutí diváka")

    # Ulozeni hranice.
    client.post("/settings/tasks", follow_redirects=False, data={
        "backup_path": "", "backup_keep": "7", "pg_dump_path": "",
        "history_retention_days": "90"})
    db.forget_settings()
    check(odklizeni.retence_dnu() == 90,
          f"uložená hranice platí ({odklizeni.retence_dnu()})")

    # Nesmysl se srovna do mezi, ne ulozi.
    client.post("/settings/tasks", follow_redirects=False, data={
        "backup_path": "", "backup_keep": "7", "pg_dump_path": "",
        "history_retention_days": "2"})
    db.forget_settings()
    check(odklizeni.retence_dnu() == odklizeni.MIN_DNU,
          f"dva dny se srovnají na {odklizeni.MIN_DNU} ({odklizeni.retence_dnu()})")

    # Zapomenuti pres stranku.
    pred = kolik()
    odpoved = client.post("/settings/historie/zapomen", data={"user_id": "u1"},
                          follow_redirects=False)
    check(odpoved.status_code == 303, "zapomenutí přesměruje zpátky")
    check(kolik() == 0, f"a historie toho diváka je pryč ({pred} -> {kolik()})")

print()
print("--- v ukázkovém režimu se nemaže nic ---")
# Ukazka bezi na verejne adrese a prihlasovaci udaje jsou v prihlasovacim
# okne - dovnitr se dostane kdokoliv. Zamek je jedna middleware, ktera
# pousti jen cesty ze seznamu vyjimek; nova routa do nej spada sama, tak
# at to tak i zustane. (Ze zamek doopravdy zastavuje, hlida
# test_demo_zamek.py - ten si ukazkovy rezim zapina jeste pred importem
# aplikace, protoze konfigurace se cte prave tehdy.)
from jellyscope import web  # noqa: E402

check("/settings/historie/zapomen" not in web.DEMO_POVOLENO,
      "zapomenutí diváka není mezi výjimkami, které ukázka pouští")
check("/settings/tasks" not in web.DEMO_POVOLENO,
      "a ani ukládání úloh")
check(web.DEMO_POVOLENO == frozenset({"/login", "/logout"}),
      f"výjimky jsou jen přihlášení a odhlášení ({sorted(web.DEMO_POVOLENO)})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
