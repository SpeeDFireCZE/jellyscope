# -*- coding: utf-8 -*-
r"""The comparison of Books: two periods side by side, a mirror of Music's comparison.

stats.books_comparison() gives the shape of stats.music_comparison() (groups of rows with the difference, the genres
with their shares, the charts of both periods), /books/compare draws it with the shared macros (templates/_compare.html).

Run:
    .venv/bin/python tests/test_books_comparison.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "books-comparison.db")
os.environ["SECRET_KEY"] = "books-comparison-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _apps import login  # noqa: E402
from _books import BOOK_READERS, fill_books  # noqa: E402

from jellyscope import access, accounts, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "en")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")
DEFAULTS = [area.key for area in access.AREAS if area.default]

# Period A = the last 7 days (hours ago 2, 3, 5, 26), period B = the 7 days before them (9 and 10 days ago).
LISTENS = (
    ("u-jana", "Kapitola 1", 1800, 2, "Finamp", "DirectPlay", None),               # audiobook "Cesta na jih"
    ("u-jana", "Kapitola 2", 1800, 3, "Finamp", "DirectPlay", None),               # the same work (two parts)
    ("u-jana", "Duna", 900, 5, "Jellyfin Web", "DirectPlay", None),                # e-book
    ("u-petr", "Hobit", 3600, 26, "Jellyfin Web", "Transcode", "AudioCodecNotSupported"),
    ("u-eva", "Hobit", 2400, 24 * 9, "Symfonium", "DirectStream", None),
    ("u-eva", "Staré pověsti", 600, 24 * 10, "Jellyfin Web", "DirectPlay", None),  # e-book without a genre
)
ids = fill_books(LISTENS)

print("--- the computation: the shape of the video comparison ---")
first = stats.period_of_days(7)
second = stats.previous_period(first)
c = stats.books_comparison(first, second)
groups = {group["heading"]: group for group in c["groups"]}
check(list(groups) == ["Reading and listening", "How it is played", "Library", "Which genre was read"],
      f"the groups: reading and listening, how it is played, the library, the genres ({list(groups)})")
rows = {row["label"]: row for row in groups["Reading and listening"]["rows"]}
check(list(rows) == ["Audiobooks listened", "E-books read", "Daily average", "Readers", "Works", "Authors"],
      f"the rows of reading and listening ({list(rows)})")
# A listened 1800 + 1800 + 3600 = 7200 s = 2 h; B 2400 s. Read: A 900 s (Duna), B 600 s (Stare povesti).
check(abs(rows["Audiobooks listened"]["a"] - 2.0) < 1e-9 and abs(rows["Audiobooks listened"]["b"] - 2400 / 3600) < 1e-9
      and abs(rows["Audiobooks listened"]["difference"] - (2.0 - 2400 / 3600)) < 1e-9
      and rows["Audiobooks listened"]["form"] == "hours",
      f"audiobooks listened: hours of A and B and the difference ({rows['Audiobooks listened']})")
check(abs(rows["E-books read"]["a"] - 900 / 3600) < 1e-9 and abs(rows["E-books read"]["b"] - 600 / 3600) < 1e-9,
      f"e-books read: the time of reading ({rows['E-books read']['a']}/{rows['E-books read']['b']})")
# Daily average: A (7200 + 900) / 3600 h over 7 days, B (2400 + 600) / 3600 h over 7 days.
check(abs(rows["Daily average"]["a"] - 8100 / 3600 / 7) < 1e-9 and abs(rows["Daily average"]["b"] - 3000 / 3600 / 7) < 1e-9,
      f"daily average of the two together ({rows['Daily average']['a']}/{rows['Daily average']['b']})")
# Readers A: jana, petr = 2, B: eva = 1. Works A: Cesta na jih (two parts = one), Duna, Hobit = 3, B: Hobit, Stare povesti = 2.
# Authors A: Vypravec, Herbert, Tolkien = 3, B: Tolkien, Jirasek = 2.
check((rows["Readers"]["a"], rows["Readers"]["b"]) == (2, 1) and (rows["Works"]["a"], rows["Works"]["b"]) == (3, 2)
      and (rows["Authors"]["a"], rows["Authors"]["b"]) == (3, 2),
      f"readers, works (a multi-part audiobook is one) and authors ({rows['Readers']['a']}/{rows['Readers']['b']},"
      f" {rows['Works']['a']}/{rows['Works']['b']}, {rows['Authors']['a']}/{rows['Authors']['b']})")
how = {row["label"]: row for row in groups["How it is played"]["rows"]}
# A: direct 1800 + 1800 = 3600 s, transcoded 3600 s -> 50 %; B: direct 2400 s (DirectStream), 0 %. The e-books are not played.
check(abs(how["Direct play"]["a"] - 1.0) < 1e-9 and abs(how["Transcodes"]["a"] - 1.0) < 1e-9
      and abs(how["Transcode share"]["a"] - 50.0) < 1e-9 and how["Transcode share"]["b"] == 0
      and how["Transcode share"]["relative"] is None and "Sent over the network" not in how,
      f"direct, transcode and the share in percent, without a relative change ({how})")
genres = groups["Which genre was read"]
fantasy = next((r for r in genres["rows"] if r["label"] == "Fantasy"), {})
# Fantasy: A 3600 s of the period's 8100 s, B 2400 s of its 3000 s (the e-book without a genre counts for the total).
check(genres.get("with_share") is True and abs(fantasy.get("share_a", 0) - 3600 / 8100 * 100) < 1e-9
      and abs(fantasy.get("share_b", 0) - 80.0) < 1e-9 and all("share_a" in r for r in genres["rows"]),
      f"the genres with their shares ({fantasy})")
top_a = c["a"]["top"]
check([r["label"] for r in top_a] == ["Cesta na jih", "Hobit", "Duna"] and abs(top_a[0]["hours"] - 1.0) < 1e-9
      and all(r["detail_url"].startswith("/book/") and len(r["detail_url"]) > 6 for r in top_a),
      f"the most read books of A, by hours, to the work's page ({top_a})")
check([r["label"] for r in c["b"]["top"]] == ["Hobit", "Staré pověsti"], f"the most read books of B ({c['b']['top']})")
check([(r["label"], r["hours"]) for r in c["a"]["users"]] == [(BOOK_READERS["u-jana"], 4500 / 3600),
                                                              (BOOK_READERS["u-petr"], 1.0)]
      and all(r["user_id"] for r in c["a"]["users"]),
      "the most active readers of a period carry their id (anonymising knows them by it)")
check(c["has_data"] and c["same_length"] and not c["overlap"], "data, the same length, no overlap")
empty = stats.books_comparison(stats.period_between("2001-01-01", "2001-01-31"),
                               stats.period_between("2001-02-01", "2001-02-28"))
check(empty["has_data"] is False and "Which genre was read" not in [g["heading"] for g in empty["groups"]],
      "two periods without reading have nothing to compare, and no empty genre table")

print()
print("--- the library: books added in the period ---")
# 18.-19. 11.: Algoritmy bez vzorcu (12 400 000 B) + three files of Cesta na jih (19.2 + 24 + 28.8 MB = 72 000 000 B,
# one work) = 2 works, 84 400 000 B. 1.-10. 11.: Hobit 5. 11. (410 000 000 B) + Pribehy z Trolli hory 10. 11. (48 000 000 B) = 2 works, 458 000 000 B.
lib = stats.books_comparison(stats.period_between("2026-11-18", "2026-11-19"),
                             stats.period_between("2026-11-01", "2026-11-10"))
added = {row["label"]: row for row in next(g for g in lib["groups"] if g["heading"] == "Library")["rows"]}
check(list(added) == ["Works added", "Data added"] and (added["Works added"]["a"], added["Works added"]["b"]) == (2, 2)
      and (added["Data added"]["a"], added["Data added"]["b"]) == (84_400_000, 458_000_000)
      and added["Data added"]["form"] == "bytes",
      f"works and size added: a multi-file audiobook is one work ({added})")

print()
print("--- the page ---")
admin = login("admin")
page = admin.get("/books/compare?a_days=7&b_since=" + second.since[:10] + "&b_until=" + second.until[:10])
s = page.text
check(page.status_code == 200 and "<h2>Reading and listening</h2>" in s and 'class="compare"' in s and "delta up" in s,
      f"the table of the groups with the difference ({page.status_code})")
check(all(name in s for name in (BOOK_READERS["u-jana"], BOOK_READERS["u-petr"])) and 'href="/book/' in s,
      "the administrator sees the readers and the books lead to their pages")
menu = re.findall(r'href="(/books/[a-z]+)"', s)
check(menu.index("/books/overview") < menu.index("/books/compare") < menu.index("/books/insights"),
      f"Books' menu: the comparison between the overview and the insights ({menu})")

accounts.save_permissions(reader_account["id"], access.pack_permissions(DEFAULTS + ["books_comparison"], False))
db.forget_settings()
reader = login("reader")
r = reader.get("/books/compare?a_days=7")
check(r.status_code == 200 and not any(name in r.text for name in BOOK_READERS.values()) and "/users/" not in r.text
      and "Most active readers" not in r.text,
      "with the comparison but without “…who read and listened”: not a single reader's name")
accounts.save_permissions(reader_account["id"], access.pack_permissions(DEFAULTS, False))
db.forget_settings()
check(reader.get("/books/compare", follow_redirects=False).status_code == 403,
      "without the comparison's area: 403 (the comparison is not on by default)")

fresh = login("admin")
fresh.get("/books/overview?days=0")                    # the whole history chosen on the overview
whole = fresh.get("/books/compare")
check(whole.status_code == 200 and re.search(r'Period A<br>\s*<span class="muted">30 days</span>', whole.text),
      "the whole history has no window before it: period A is the default 30 days")
nothing = admin.get("/books/compare?a_since=2001-01-01&a_until=2001-01-31&b_since=2001-02-01&b_until=2001-02-28")
check("There is nothing to compare in either period" in nothing.text and "class=\"compare\"" not in nothing.text,
      "no reading in either period: it says so instead of an empty table")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
