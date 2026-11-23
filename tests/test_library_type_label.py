# -*- coding: utf-8 -*-
r"""The type of a library is translated once, when the page is drawn.

The Libraries card used to get the label already translated in Python and
translated it again in the template. With Czech keys the second pass found
nothing; with English keys a translation can be a key of its own - the Spanish
"Series" (TV shows) is also the English key of one series ("Serie") - so the
card said "Serie" for a library of series.

Run:
    .venv/bin/python tests/test_library_type_label.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
HOME = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=HOME, DATABASE_PATH=str(Path(HOME) / "l.db"), SECRET_KEY="library-type",
                  JELLYFIN_URL="http://127.0.0.1:1", JELLYFIN_API_KEY="x", JELLYSCOPE_DEMO="0")

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("type_admin", "library-type-pw", is_admin=True)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, missing_runs) VALUES ('shows', 'Shows', 'tvshows', 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type, missing_runs) VALUES ('films', 'Films', 'movies', 0)")

expected = {"cs": "Seriály", "en": "TV shows", "es": "Series", "de": "Serien"}
for language, label in expected.items():
    db.set_setting("ui_language", language)
    client = TestClient(web.app)
    client.post("/login", data={"username": "type_admin", "password": "library-type-pw", "method": "local"})
    text = client.get("/library").text
    cells = re.findall(r'<span class="muted">[^<]*</span><span>([^<]*)</span>', text)
    check(label in cells, f"{language}: a library of series is {label!r} ({cells})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
