r"""Divák je v souhrnech jednou - podle id, se současným jménem.

V historii zůstává jméno z doby přehrání (refresh_playback_metadata ho
schválně nepřepisuje). Souhrn seskupený podle jména tak rozdělil
přejmenovaného diváka na dva sloupce s rozdělenými hodinami - a naopak
slil dva různé lidi se stejným jménem do jednoho. Hlídá se tu každý
souhrn po divácích:

* přejmenovaný divák = jeden řádek se současným jménem (z `users`);
* dva lidé se stejným jménem = dva řádky, každý se svým id;
* řádky bez user_id (import bez UserId) se dělí podle jména.

Spuštění:
    .venv/bin/python tests/test_divak_jednou.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "divak_jednou.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

import re  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, langstats, stats  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


db.init_db()
pred_dnem = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None).strftime(db.TIME_FORMAT)
HRANI = [  # klíč, user_id, jméno v historii, sekundy
    ("jana-stara", "u-jana", "JanaStara", 1800),   # přejmenovaná: dvě jména, jeden člověk
    ("jana-nova", "u-jana", "Jana", 3600),
    ("petr-1", "u-petr", "Petr", 2400),            # dva různí Petrové
    ("petr-2", "u-petr2", "Petr", 1200),
    ("import-a", None, "ImportA", 600),            # import bez UserId
    ("import-b", None, "ImportB", 900),
]
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, is_missing)"
                 " VALUES ('f1', 'Film', 'Movie', 'filmy', 0)")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-jana', 'Jana', 0, 0), ('u-petr', 'Petr', 0, 0), ('u-petr2', 'Petr', 0, 0)")
    for klic, uid, jmeno, sekundy in HRANI:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active,"
            " bitrate, audio_language)"
            " VALUES (?, ?, ?, 'f1', 'Film', 'Movie', 'filmy', ?, ?, ?, ?, 0, 0, 8000000, 'cze')",
            (klic, uid, jmeno, pred_dnem, pred_dnem, pred_dnem, sekundy))
    conn.commit()

OCEKAVANO = {  # (user_id, jméno) -> hodiny
    ("u-jana", "Jana"): 1.5, ("u-petr", "Petr"): 2400 / 3600, ("u-petr2", "Petr"): 1200 / 3600,
    (None, "ImportA"): 600 / 3600, (None, "ImportB"): 900 / 3600,
}


def porovnej(nazev: str, radky: list[tuple]) -> None:
    dostal = sorted((((uid, jmeno), round(hodiny, 4)) for uid, jmeno, hodiny in radky), key=str)
    cekam = sorted(((klic, round(hodiny, 4)) for klic, hodiny in OCEKAVANO.items()), key=str)
    check(dostal == cekam, f"{nazev}: každý divák jednou, se současným jménem ({dostal})")


print("--- souhrny po divácích ---")
porovnej("stats.top_users",
         [(r["user_id"], r["label"], float(r["hours"])) for r in stats.top_users(30, limit=20)])
porovnej("stats.library_activity",
         [(r["user_id"], r["label"], float(r["hours"]))
          for r in stats.library_activity("filmy", 30)["top_users"]])
porovnej("langstats.languages_by_user",
         [(p["user_id"], p["name"], p["total"]) for p in langstats.languages_by_user(30, {})])
jana = next((p for p in langstats.languages_by_user(30, {}) if p["user_id"] == "u-jana"), None)
check(jana is not None and len(jana["segments"]) == 1,
      f"jazyky: přejmenovaná má češtinu jednou, ne dvakrát ({jana and len(jana['segments'])})")
porovnej("stats.bandwidth_podle (diváci)",
         [(r["user_id"], r["label"], r["bajtu"] * 8 / 8000000 / 3600)
          for r in stats.bandwidth_podle(30, "user_name")])

print()
print("--- divák bez id nemá odkaz do prázdna ---")
# Správce smí na každou stránku diváka - ale importované jméno bez
# UserId žádnou stránku nemá. Odkaz /users/None vede na 404.
accounts.create("spravce", "dlouheheslo", is_admin=True)
k = TestClient(app)
k.post("/login", data={"username": "spravce", "password": "dlouheheslo", "zpusob": "mistni"})
for adresa in ("/", "/history", "/item/f1", "/library/filmy?tab=activity", "/languages", "/network"):
    r = k.get(adresa)
    spatne = sorted(set(re.findall(r'href="(/users/(?:None|)?)"', r.text)))
    check(r.status_code == 200 and not spatne, f"{adresa}: {r.status_code}, odkazy bez diváka {spatne}")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
