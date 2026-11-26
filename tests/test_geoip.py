# -*- coding: utf-8 -*-
r"""The GeoLite2 database for the map: a file that is no database brings nothing down.

The file comes from a mirror on GitHub (geoip.SOURCE_URL) and a click on Download replaces the old one. Guarded here:

* a file in data/ that is no MaxMind database (an HTML page from a captive portal, an empty file) is no error of
  the Network page - the map is an extra, the page shows without it and with the button to download it again;
* a download that is no GeoLite2 City database, or one over the size ceiling, is an error and the old database
  stays - otherwise the button that could fix it would be on a page that falls;
* a real database is downloaded and read straight away.

The test database is a tiny MaxMind DB written here (one node of the search tree, one record), so neither the
network nor the real 60 MB file is needed.

Run:
    .venv/bin/python tests/test_geoip.py
"""
from __future__ import annotations

import asyncio
import os
import struct
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "geoip.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, demodata, geoip  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def _encoded(value) -> bytes:
    """One value in the MaxMind DB data format: a text, a double, a map, an array or ("u16"|"u32"|"u64", number)."""
    if isinstance(value, str):
        raw = value.encode()
        return bytes([(2 << 5) | len(raw)]) + raw
    if isinstance(value, float):
        return bytes([(3 << 5) | 8]) + struct.pack(">d", value)
    if isinstance(value, dict):
        return bytes([(7 << 5) | len(value)]) + b"".join(_encoded(k) + _encoded(v) for k, v in value.items())
    if isinstance(value, list):
        return bytes([len(value), 11 - 7]) + b"".join(_encoded(v) for v in value)
    kind, number = value
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    if kind == "u64":
        return bytes([len(raw), 9 - 7]) + raw
    return bytes([((5 if kind == "u16" else 6) << 5) | len(raw)]) + raw


def mmdb(city: str, database_type: str = "GeoLite2-City") -> bytes:
    """A MaxMind DB with one node: addresses starting with bit 1 (203.0.113.1) are in `city`, the others nowhere."""
    record = _encoded({"city": {"names": {"en": city}}, "country": {"iso_code": "CZ", "names": {"en": "Czechia"}},
                       "location": {"latitude": 50.08, "longitude": 14.41}})
    node_count = 1
    tree = node_count.to_bytes(3, "big") + (node_count + 16).to_bytes(3, "big")
    metadata = _encoded({"node_count": ("u32", node_count), "record_size": ("u16", 24), "ip_version": ("u16", 4),
                         "database_type": database_type, "languages": ["en"],
                         "binary_format_major_version": ("u16", 2), "binary_format_minor_version": ("u16", 0),
                         "build_epoch": ("u64", 1700000000), "description": {"en": "test"}})
    return tree + b"\x00" * 16 + record + b"\xab\xcd\xefMaxMind.com" + metadata


db.init_db()
accounts.create("admin", "secretpass1", is_admin=True)
demodata.seed()
target = geoip.database_path()
target.parent.mkdir(parents=True, exist_ok=True)
check(geoip.library_installed(), "the maxminddb library is installed (otherwise the checks below test nothing)")


print("--- a file that is no database brings nothing down ---")
for content, label in ((b"<html><body>Sign in to the Wi-Fi</body></html>", "an HTML page"),
                       (b"", "an empty file"), (os.urandom(4096), "random bytes")):
    target.write_bytes(content)
    geoip.forget()
    try:
        place = geoip.lookup("203.0.113.1")
        outcome = repr(place)
    except Exception as error:   # noqa: BLE001 - which error it is, is what is checked
        place, outcome = "error", type(error).__name__
    check(place is None, f"{label}: looking an address up gives nothing, not an exception ({outcome})")
    with TestClient(app, raise_server_exceptions=False) as www:
        www.post("/login", data={"username": "admin", "password": "secretpass1"})
        page = www.get("/network")
    check(page.status_code == 200 and 'action="/network/geoip"' in page.text,
          f"{label}: the Network page opens, with the button to download it again ({page.status_code})")


print()
print("--- a download that is no database keeps the old one ---")
ANSWER: dict = {"content": b""}
_real_client = httpx.AsyncClient


class _Mirror(_real_client):
    """The mirror on GitHub, substituted: it answers 200 with ANSWER["content"]."""

    def __init__(self, *arguments, **named) -> None:
        named["transport"] = httpx.MockTransport(lambda request: httpx.Response(200, content=ANSWER["content"]))
        super().__init__(*arguments, **named)


def download(content: bytes) -> dict:
    ANSWER["content"] = content
    httpx.AsyncClient = _Mirror
    try:
        return asyncio.run(geoip.download())
    finally:
        httpx.AsyncClient = _real_client


good = mmdb("Prague")
target.write_bytes(good)
geoip.forget()
check((geoip.lookup("203.0.113.1") or {}).get("city") == "Prague", "the test database is read (Prague)")
partial = target.with_suffix(".stahuje")
for content, label in ((b"<html>Rate limit exceeded</html>", "an HTML page"), (b"", "an empty answer"),
                       (mmdb("Brno", "GeoLite2-Country"), "another kind of database (Country)")):
    result = download(content)
    check(result.get("status") == "error" and result.get("message"),
          f"{label}: the download is an error with a reason ({result})")
    check(target.read_bytes() == good and not partial.exists(),
          f"{label}: the old database stays and nothing half-written lies beside it")
    check((geoip.lookup("203.0.113.1") or {}).get("city") == "Prague", f"{label}: and it is still read")

ceiling = geoip.MAX_BYTES
geoip.MAX_BYTES = len(mmdb("Brno")) - 1
try:
    result = download(mmdb("Brno"))
finally:
    geoip.MAX_BYTES = ceiling
check(result.get("status") == "error" and target.read_bytes() == good and not partial.exists(),
      f"an answer over the size ceiling: an error and the old database stays ({result})")
check(200 * 1024 * 1024 >= geoip.MAX_BYTES > 100 * 1024 * 1024,
      f"the ceiling leaves room for the real file (about 65 MB) and not for a full disk ({geoip.MAX_BYTES})")


print()
print("--- a real database is downloaded and read straight away ---")
result = download(mmdb("Brno"))
check(result.get("status") == "ok" and result.get("byte_count") == len(mmdb("Brno")), f"the download is fine ({result})")
check((geoip.lookup("203.0.113.1") or {}).get("city") == "Brno", "and the new database answers at once (Brno)")


class _FormInParagraph(HTMLParser):
    """Forms that start while a <p> is open - the parser closes the <p> before them and the stray </p> makes an empty paragraph."""

    def __init__(self) -> None:
        super().__init__()
        self.open_paragraphs, self.forms = 0, []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "p":
            self.open_paragraphs += 1
        elif tag == "form" and self.open_paragraphs:
            self.forms.append(dict(attrs).get("action"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self.open_paragraphs:
            self.open_paragraphs -= 1


with TestClient(app, raise_server_exceptions=False) as www:
    www.post("/login", data={"username": "admin", "password": "secretpass1"})
    page = www.get("/network").text
parser = _FormInParagraph()
parser.feed(page)
check('action="/network/geoip"' in page and not parser.forms,
      f"the Network page with the database: the Update button is not a form inside a paragraph ({parser.forms})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
