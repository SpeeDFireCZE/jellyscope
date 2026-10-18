# -*- coding: utf-8 -*-
r"""Věta o zvoleném období dává smysl i pro rok a pro vlastní rozmezí.

Na Přehledu, u Uživatelů a v detailu uživatele se věta lepila z kousku
„za posledních" a popisku období. U „7 dnů" to sedělo, u roku vyšlo
„Co se na serveru dělo za posledních rok." (anglicky „over the last
1 year") a u vlastního rozmezí „Kdo kolik sleduje za posledních ." -
popisek pro rozmezí neexistuje, takže zbyla tečka.

Teď jsou to celé věty se třemi podobami, stejně jako u knihovny:
vybrané období, poslední rok, posledních {obdobi}.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_veta_obdobi.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obdobi_veta.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u1', 'Jana')")
    conn.commit()

klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo", "zpusob": "mistni"})


def text(adresa: str) -> str:
    """Viditelný text stránky, mezery sloučené."""
    html = klient.get(adresa).text
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


ROZMEZI = "od=2026-08-01&do=2026-08-20"
STRANKY = {"Přehled": "/", "Uživatelé": "/users", "detail uživatele": "/users/u1"}

# Co se na strance objevit nesmi - slepene kusy, ktere nedavaji smysl.
SPATNE = {
    "cs": ["posledních rok", "posledních .", "posledních ·", "posledních 7 dnů 30"],
    "en": ["last 1 year", "last .", "last ·"],
    "es": ["periodo: 1 año", "periodo: .", "últimos 1 año"],
}
# A co tam byt musi.
SPRAVNE = {
    "cs": {"rok": "poslední rok", "30": "posledních 30 dnů", "rozmezí": "vybraném období"},
    "en": {"rok": "last year", "30": "last 30 days", "rozmezí": "selected period"},
    "es": {"rok": "último año", "30": "últimos 30 días", "rozmezí": "periodo elegido"},
}

for jazyk in ("cs", "en", "es"):
    db.set_setting("ui_language", jazyk)
    db.forget_settings()
    print()
    print(f"--- {jazyk} ---")
    for nazev, adresa in STRANKY.items():
        spoj = "&" if "?" in adresa else "?"
        for obdobi, dotaz in (("rok", "days=365"), ("30", "days=30"), ("rozmezí", ROZMEZI)):
            stranka = text(adresa + spoj + dotaz)
            spatne = [s for s in SPATNE[jazyk] if s in stranka]
            check(not spatne and SPRAVNE[jazyk][obdobi] in stranka,
                  f"{nazev}, {obdobi}: „{SPRAVNE[jazyk][obdobi]}“ {spatne or ''}")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
