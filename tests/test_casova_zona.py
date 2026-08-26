# -*- coding: utf-8 -*-
r"""Aplikace má vlastní časovou zónu, nezávislou na stroji.

Dřív se čas vypisoval podle zóny systému. Server běžící v UTC tak
posouval večerní špičku v grafech o dvě hodiny proti tomu, co člověk
zažil - a nešlo s tím nic dělat bez zásahu do stroje nebo kontejneru.

Zóna je teď v Nastavení → Obecné a platí pro **všechny** výpisy času:
historii, popisky grafů i časy úloh. Prázdná hodnota znamená „podle
systému", tedy původní chování.

Rozdělení na dny a hodiny (denní graf, teplotní mapa) dělá SQL - to čte
zónu procesu, ne naši proměnnou, a proto se zóna při startu nastavuje
i do prostředí. Na Windows to nejde a je to v pořádku: výpis času je
správný tak jako tak.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_casova_zona.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "zona.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, formatting  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

# Cas ulozeny v databazi je vzdycky UTC.
ULOZENO = "2026-08-26 08:33:26"

print("--- tentýž údaj v různých zónách ---")
db.set_setting("app_timezone", "UTC")
check(formatting.datetime_human(ULOZENO) == "26.08.2026 08:33",
      f"UTC: {formatting.datetime_human(ULOZENO)}")

db.set_setting("app_timezone", "Europe/Prague")
check(formatting.datetime_human(ULOZENO) == "26.08.2026 10:33",
      f"Praha (letní čas, +2): {formatting.datetime_human(ULOZENO)}")

db.set_setting("app_timezone", "America/New_York")
check(formatting.datetime_human(ULOZENO) == "26.08.2026 04:33",
      f"New York: {formatting.datetime_human(ULOZENO)}")

print()
print("--- letní a zimní čas se počítá zvlášť ---")
# Proto se uklada JMENO zony, ne posun v hodinach: v lednu je Praha +1,
# v srpnu +2. Ulozeny posun by pul roku lhal.
db.set_setting("app_timezone", "Europe/Prague")
check(formatting.datetime_human("2026-01-15 08:00:00") == "15.01.2026 09:00",
      f"leden +1: {formatting.datetime_human('2026-01-15 08:00:00')}")
check(formatting.datetime_human("2026-07-15 08:00:00") == "15.07.2026 10:00",
      f"červenec +2: {formatting.datetime_human('2026-07-15 08:00:00')}")

print()
print("--- graf sítě mluví stejným časem jako historie ---")
from datetime import datetime, timedelta, timezone  # noqa: E402

from jellyscope import stats  # noqa: E402

zacatek = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
    hour=18, minute=0, second=0, microsecond=0)
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, bitrate,"
        " is_paused, is_active, play_method, remote_address, client, device_name)"
        " VALUES ('s1','u1','Pepa','film','Duna',?,?,?,3600,0,20000000,0,0,'DirectPlay',"
        "'10.0.0.5','Web','Chrome')",
        (zacatek.strftime(db.TIME_FORMAT),
         (zacatek + timedelta(hours=1)).strftime(db.TIME_FORMAT),
         (zacatek + timedelta(hours=1)).strftime(db.TIME_FORMAT)))

for zona, ocekavana_hodina in (("UTC", 18), ("Europe/Prague", 20), ("America/New_York", 14)):
    db.set_setting("app_timezone", zona)
    krivka = stats.bandwidth_prubeh(7, bodu=30)
    popisek = krivka[0]["popisek"] if krivka else ""
    historie = formatting.datetime_human(zacatek.strftime(db.TIME_FORMAT))
    sedi = popisek.split()[-1].startswith(f"{ocekavana_hodina:02d}:")
    check(sedi, f"{zona}: graf {popisek}, historie {historie}")

print()
print("--- neznámá zóna nespadne, jen se nepoužije ---")
db.set_setting("app_timezone", "Neexistuje/Mesto")
check(formatting.zona() is None, "zóna se neuzná")
check(formatting.datetime_human(ULOZENO) != "", "a výpis času pořád funguje")

print()
print("--- do nastavení se uloží jen zóna, kterou Python zná ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

db.set_setting("app_timezone", "Europe/Prague")
client = TestClient(web.app)
client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
            follow_redirects=False)
client.post("/settings/language",
            data={"ui_language": "cs", "log_language": "cs",
                  "app_timezone": "Neexistuje/Mesto"}, follow_redirects=False)
check(db.get_setting("app_timezone", "") == "Europe/Prague",
      "překlep původní hodnotu nepřepíše")
client.post("/settings/language",
            data={"ui_language": "cs", "log_language": "cs",
                  "app_timezone": "Asia/Tokyo"}, follow_redirects=False)
check(db.get_setting("app_timezone", "") == "Asia/Tokyo", "platná zóna se uloží")
client.post("/settings/language",
            data={"ui_language": "cs", "log_language": "cs", "app_timezone": ""},
            follow_redirects=False)
check(db.get_setting("app_timezone", "") == "", "prázdné pole vrátí systémovou zónu")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
