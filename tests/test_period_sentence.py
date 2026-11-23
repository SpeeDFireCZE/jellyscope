# -*- coding: utf-8 -*-
r"""The sentence about the chosen period makes sense for a year and for a custom range too.

On the Overview, by Users and in a user's detail the sentence was glued together from the piece
"za posledních" and the period's label. For "7 dnů" it fitted, for a year it came out as
"Co se na serveru dělo za posledních rok." (in English "over the last 1 year")
and for a custom range "Kdo kolik sleduje za posledních ." -
there is no label for a range, so a full stop was left.

Now these are whole sentences with three forms, the same as with the library:
the chosen period, the last year, the last {period}.

Run:
    .venv/bin/python tests/test_period_sentence.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obdobi_veta.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from _package import sample_movie  # noqa: E402
from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
sample_movie()  # the Movies & series section exists only with a movie (navigation.has_video)
accounts.create("admin", "longpassword", is_admin=True)
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u1', 'Jana')")
    conn.commit()

client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})


def text(address: str) -> str:
    """The page's visible text, spaces merged."""
    html = client.get(address).text
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


RANGE = "since=2026-08-01&until=2026-08-20"
PAGES = {"the Overview": "/", "Users": "/users", "a user's detail": "/users/u1"}

# What must not appear on the page - glued pieces that make no sense.
WRONG = {
    "cs": ["posledních rok", "posledních .", "posledních ·", "posledních 7 dnů 30"],
    "en": ["last 1 year", "last .", "last ·"],
    "es": ["periodo: 1 año", "periodo: .", "últimos 1 año"],
}
# And what must be there.
RIGHT = {
    "cs": {"year": "poslední rok", "30": "posledních 30 dnů", "range": "vybraném období"},
    "en": {"year": "last year", "30": "last 30 days", "range": "selected period"},
    "es": {"year": "último año", "30": "últimos 30 días", "range": "periodo elegido"},
}

for language in ("cs", "en", "es"):
    db.set_setting("ui_language", language)
    db.forget_settings()
    print()
    print(f"--- {language} ---")
    for name, address in PAGES.items():
        joiner = "&" if "?" in address else "?"
        for period, query in (("year", "days=365"), ("30", "days=30"), ("range", RANGE)):
            page = text(address + joiner + query)
            wrong = [s for s in WRONG[language] if s in page]
            check(not wrong and RIGHT[language][period] in page,
                  f"{name}, {period}: “{RIGHT[language][period]}” {wrong or ''}")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
