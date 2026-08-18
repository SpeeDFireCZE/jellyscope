"""Ukazkovy rezim - spusti Jellyscope s vymyslenymi daty.

Na co to je: proklikat si aplikaci driv, nez ji napojis na svuj Jellyfin.
Nepotrebuje API klic, nepotrebuje soubor .env, nepotrebuje nic.

    python demo.py

Pak otevri http://127.0.0.1:8098

Vsechno se uklada do data/demo.db. Chces-li zacit nanovo, smaz ten soubor.
Tvoje skutecna data (data/jellyscope.db) tim nijak nezmenis.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Nastaveni prostredi MUSI byt driv nez import aplikace - konfigurace se
# nacita pri importu a pak uz se nemeni.
os.environ["DATABASE_PATH"] = "data/demo.db"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"   # zamerne nefunkcni
os.environ["JELLYFIN_API_KEY"] = "ukazkovy-rezim-bez-serveru"
os.environ["SECRET_KEY"] = "ukazkovy-rezim"
# Ukazkovy rezim: sberac se nespousti (nema se koho ptat)
# a vymyslene "prave se hraje" tak na Prehledu zustane videt.
os.environ["JELLYSCOPE_DEMO"] = "1"
os.environ["HOST"] = "127.0.0.1"
os.environ["PORT"] = "8098"

import uvicorn  # noqa: E402

from jellyscope import db, demodata  # noqa: E402


def main() -> None:
    db.init_db()

    if demodata.already_seeded():
        print("Ukazkova data uz v databazi jsou, jen spoustim server.")
    else:
        print("Pripravuji vymyslena data...")
        counts = demodata.seed()
        print(f"  {counts['items']} titulu, {counts['plays']} prehravani, "
              f"{counts['users']} uzivatelu")

    demodata.ensure_demo_account()

    print()
    print("=" * 58)
    print("  UKAZKOVY REZIM - data jsou vymyslena")
    print()
    print("  Otevri v prohlizeci:  http://127.0.0.1:8098")
    print()
    print(f"  Prihlaseni:  {demodata.DEMO_USERNAME} / {demodata.DEMO_PASSWORD}")
    print()
    print("  Ukoncis stiskem Ctrl+C")
    print("=" * 58)
    print()

    # V ukazkovem rezimu nema smysl obtezovat nedostupny Jellyfin
    # dotazy kazdych deset sekund.
    db.set_setting("poll_interval", "300")
    # A uz vubec nema smysl poustet naplanovane ulohy - nemaji kam sahnout.
    db.set_setting("task_sync_enabled", "0")
    db.set_setting("task_recent_enabled", "0")

    uvicorn.run("jellyscope.web:app", host="127.0.0.1", port=8098, log_level="warning")


if __name__ == "__main__":
    main()
