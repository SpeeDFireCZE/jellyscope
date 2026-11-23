# -*- coding: utf-8 -*-
"""Jellyscope only reads from Jellyfin.

This test guards the promise the whole application stands on: **Jellyfin is
never changed**. The statistics can be computed without writing, so there is
no reason to touch somebody else's server — and should a write ever get into
the code, it is to fail a test, not break somebody's library.

Three things are checked:
  1. the client has no HTTP method other than GET,
  2. every address that appears in the code is on the list of reading ones,
  3. SQL sent to the Playback Reporting plugin passes the `read_only` safeguard.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope.importers import PBR_QUERY, ImportError_, read_only  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


PACKAGE = PROJECT / "jellyscope"
# scanner (3c) and importers (3d) are packages - they are read whole, file by file.
FILES = ("jellyfin.py", *sorted(f"scanner/{p.name}" for p in (PACKAGE / "scanner").glob("*.py")),
         "collector.py", *sorted(f"importers/{p.name}" for p in (PACKAGE / "importers").glob("*.py")))


def code(name: str) -> str:
    """The source without comments - it is about what is called, not what is described."""
    text = (PACKAGE / name).read_text(encoding="utf-8")
    return "\n".join(
        "" if line.strip().startswith("#") else line
        for line in text.splitlines()
    )


print("--- the client can do only GET (apart from signing in) ---")
client = code("jellyfin.py")
for method in ("put", "delete", "patch"):
    check(f"_client.{method}(" not in client,
          f"jellyfin.py does not call _client.{method}()")
# POST is only in signing in: checking a password, asking for a Quick Connect
# code, exchanging a confirmed code for an identity and revoking the token that
# came of it. Nothing else is changed with this client - see below.
check(client.count("_client.post(") == 4,
      f"POST only four times, all for signing in "
      f"({client.count('_client.post(')}x)")
check(client.count("async def _get") == 1, "there is a single sending function")
# Images are downloaded in pieces (a size ceiling) through _client.stream().
# There the method is written as text - were anything other than GET to stand
# in it, the put/delete/patch check above would not see it.
stream_methods = re.findall(r'_client\.stream\(\s*"(\w+)"', client)
check(bool(stream_methods) and set(stream_methods) == {"GET"},
      f"downloading images goes through GET ({stream_methods})")
check("_client.request(" not in client, "jellyfin.py does not call the generic _client.request()")


print()
print("--- addresses that appear in the code ---")
# We collect ALL text addresses, not only those written right into _get():
# some are put together into a variable (path = f"/Users/{id}/Items") and a
# check looking only at the calls would miss them.
READING_ADDRESSES = {
    "/System/Info",                     # the server version, the connection check
    "/Users",                           # the list of users
    "/Sessions",                        # what is playing right now
    "/Library/VirtualFolders",          # the list of libraries
    # How much space is left where the data REALLY lie. `disk_usage` measures
    # the disk under Jellyscope, but the library tends to be elsewhere. An older
    # Jellyfin does not have the endpoint and returns 404 - that is taken as
    # "I do not know", not as an error.
    "/System/Info/Storage",             # the name since Jellyfin 12 (verified on 12.1.0)
    "/System/Storage",
    "/Items",                           # the library's items
    "/Users/{user_id}/Items",           # the items through one user's eyes
    "/Items/{item_id}/Images/{kind}",   # a poster
    "/UserImage",                       # a viewer's profile image (My account)
    # The query interface of the Playback Reporting plugin. The only place
    # where POST is sent - the SQL goes in the request body and read_only() guards it.
    "/user_usage_stats/submit_custom_query",
    # Signing in with a Jellyfin password - and cleaning up right after it. The
    # explanation is at CHANGING_ADDRESSES below.
    "/Users/AuthenticateByName",
    "/Sessions/Logout",
    # Signing in with a code (Quick Connect). Jellyscope asks for a code, the
    # person confirms it in their own Jellyfin - the password does not get here
    # at all. The token that comes of it is revoked the same as with a password.
    "/QuickConnect/Enabled",
    "/QuickConnect/Initiate",
    "/QuickConnect/Connect",
    "/Users/AuthenticateWithQuickConnect",
}

found: set[str] = set()
for path in FILES:
    for address in re.findall(r'f?"(/[A-Za-z][^"\s]*)"', code(path)):
        found.add(address.split("?")[0])

extra = sorted(found - READING_ADDRESSES)
check(not extra, f"no unknown address (extra: {extra})")
print(f"       addresses found: {sorted(found)}")

# Addresses that control or change Jellyfin. Not one may appear even as text -
# even if nobody calls it today.
CHANGING_ADDRESSES = (
    "/Playing", "/Library/Refresh", "/ScheduledTasks/Running",
    "/System/Restart", "/System/Shutdown", "/Users/New",
    "/Plugins/", "/Packages/", "/Items/Delete", "/Startup/",
)
all_code = "\n".join(code(path) for path in FILES)
for address in CHANGING_ADDRESSES:
    check(address not in all_code, f"appears nowhere: {address}")

# Six exceptions, all only for signing in with a Jellyfin account.
#
# `AuthenticateByName` is no intervention in the library nor in the settings -
# it is the question "does this password match?", asked on behalf of a person
# at the moment they typed it into our sign-in window themselves. But Jellyfin
# issues an access token for it, and that is an intervention: a valid key to
# somebody else's account that would stay hanging after us. So it is revoked
# at once through `/Sessions/Logout`. Verified on 10.11.11 and 12.0.0: after
# signing in no device nor key is added.
#
# Both addresses may be **only in that one function** - elsewhere it is no
# longer signing in and this reasoning does not apply to them.
start = client.index("async def verify_login")
end = client.index("async def items_page")
signing_in = client[start:end]
#
# Quick Connect is the same, only the direction is reversed: Jellyscope asks
# for a code, the person confirms it in their already signed-in Jellyfin and
# only then it is exchanged for an identity. `Enabled` and `Connect` only ask,
# `Initiate` creates a code and `AuthenticateWithQuickConnect` returns a
# token - it is revoked at once, as with a password. None of them touches the
# library or the settings.
for address in ("/Users/AuthenticateByName", "/Sessions/Logout",
                "/QuickConnect/Enabled", "/QuickConnect/Initiate",
                "/QuickConnect/Connect",
                "/Users/AuthenticateWithQuickConnect"):
    check(all_code.count(address) == 1,
          f"{address} is in the code only once ({all_code.count(address)}x)")
    check(address in signing_in, f"{address} is only in signing in")
check("_revoke_token" in signing_in, "and the token vanishes right after checking the password")
# And above all: all the client's POSTs lie exactly in this stretch. Were one
# ever added elsewhere, it is no longer signing in and this reasoning does not apply.
check(signing_in.count("_client.post(") == client.count("_client.post("),
      "no POST of the client lies outside signing in")


print()
print("--- the Playback Reporting plugin: only SELECT ---")
# This is the only place with POST. POST by itself changes nothing - what
# is sent in it does. Hence the safeguard.
importer_code = "\n".join(code(f"importers/{p.name}") for p in sorted((PACKAGE / "importers").glob("*.py")))
posts = re.findall(r"_client\.post\(", importer_code)
check(len(posts) == 2, f"POST is only in two places (there are {len(posts)})")
check(importer_code.count("read_only(") >= 3,
      "both calls go through the read_only() safeguard")

check(read_only("SELECT * FROM PlaybackActivity") == "SELECT * FROM PlaybackActivity",
      "SELECT passes")
check(read_only("  select  a  from  b  ;") == "select a from b",
      "needless spaces and the final semicolon are evened out")

for bad in [
    "DELETE FROM PlaybackActivity",
    "DROP TABLE PlaybackActivity",
    "UPDATE PlaybackActivity SET x = 1",
    "INSERT INTO PlaybackActivity VALUES (1)",
    "SELECT 1; DROP TABLE PlaybackActivity",
    "SELECT 1; DELETE FROM x",
    "PRAGMA journal_mode = WAL",
    "ATTACH DATABASE '/etc/passwd' AS p",
    "VACUUM",
    "CREATE TABLE x (a int)",
    "REPLACE INTO x VALUES (1)",
]:
    try:
        read_only(bad)
        passed = True
    except ImportError_:
        passed = False
    check(not passed, f"refuses: {bad[:45]}")

try:
    read_only(PBR_QUERY)
    query_ok = True
except ImportError_ as exc:
    query_ok = False
    print("       ", exc)
check(query_ok, "the real query to the plugin passes the safeguard")


print()
print("--- writing aims only at our own database ---")
check("httpx" not in code("db.py"), "db.py does not talk over the network at all")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
