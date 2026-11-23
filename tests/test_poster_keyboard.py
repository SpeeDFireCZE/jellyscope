# -*- coding: utf-8 -*-
r"""The poster and the name of the same title are one link for the keyboard.

With a poster in "Nově přidané" the image and the name under it both led to the same
title, and the same with the playback running right now. Nobody notices with a mouse,
but whoever controls the page by keyboard had to press Tab twice for every poster,
and a screen reader read only "link" without a name at the image,
because the image has no label (alt="" on purpose - the name is right under it).

The image stays clickable by mouse, it is only skipped at Tab (tabindex="-1")
and in the reader (aria-hidden="true"). The name stays an ordinary link.

Run:
    .venv/bin/python tests/test_poster_keyboard.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "plakaty.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, is_missing, date_created)"
                 " VALUES ('f1', 'Pelíšky', 'Movie', 'filmy', 0, ?)", (yesterday,))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, started_at, last_seen_at, watched_seconds, is_active, play_method)"
        " VALUES ('s1', 'u1', 'Jana', 'f1', 'Pelíšky', 'Movie', 'filmy', ?, ?, 60, 1, 'DirectPlay')",
        (yesterday, yesterday))
    conn.commit()

client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})


def links(html: str, css_class: str) -> list[str]:
    """The opening <a> tags with the given class."""
    return re.findall(r'<a\b[^>]*class="' + css_class + r'"[^>]*>', html, re.S)


print("--- Recently added ---")
strip = client.get("/partials/recently-added").text
images = links(strip, "poster-frame")
check(len(images) == 1, f"the poster is on the page ({len(images)})")
check(all('tabindex="-1"' in a and 'aria-hidden="true"' in a for a in images),
      "the image is skipped at Tab and in the reader")
names = links(strip, "poster-name")
check(len(names) == 1 and "tabindex" not in names[0] and "aria-hidden" not in names[0],
      "the name stays an ordinary link")

print()
print("--- Now playing ---")
card = client.get("/partials/now-playing").text
images = links(card, "session-poster")
check(len(images) >= 1, f"the playback's image is on the page ({len(images)})")
check(all('tabindex="-1"' in a and 'aria-hidden="true"' in a for a in images),
      "the image is skipped at Tab and in the reader")
check(re.search(r'<div class="session-title">\s*<a href="/item/f1"', card) is not None,
      "the playback's name stays an ordinary link")

print()
print("--- hovering over a poster enlarges it slightly ---")
# So that it is seen what a person aims at. It holds for the strip of Recently added and for
# the media grid in the library, for the mouse and the keyboard (the focus on the name), and whoever has
# animations switched off in the system gets the enlarging without an animation.
CSS = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")


def rule(selector_contains: str) -> str:
    """The selector of a rule with :hover and scale() that contains the given text."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        selector = m.group(1).split("*/")[-1]
        if selector_contains in selector and ":hover" in selector and "scale(" in m.group(2):
            return selector
    return ""


strip = rule(".poster-card")
check(":hover" in strip and ":focus-within" in strip, f"the strip: by mouse and keyboard ({strip.strip()[:70]!r})")
check(".has-episodes" not in strip or ":not(.has-episodes)" in strip,
      "a card with episodes does not enlarge (it widens on hover and shows the list of episodes)")
grid = rule(".media-card")
check(":hover" in grid and ":focus-visible" in grid, f"the media grid: by mouse and keyboard ({grid.strip()[:70]!r})")
# The strip scrolls sideways, so it cuts off everything that sticks out of it - without
# a reserve the enlarged poster would be cut off at the top and at the sides.
strip_body = CSS[CSS.index(".recent-card .poster-strip {"):CSS.index("}", CSS.index(".recent-card .poster-strip {"))]
top = re.search(r"padding:\s*(\d+)px\s+(\d+)px", strip_body)
check(top is not None and int(top.group(1)) >= 6 and int(top.group(2)) >= 6,
      f"the strip has a reserve of at least 6 px at the top and at the sides ({top.group(0) if top else '-'})")
calm = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", CSS, re.S)
check(any("poster" in block and "transition: none" in block for block in calm),
      "with animations switched off it enlarges without an animation")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
