# -*- coding: utf-8 -*-
"""Security checks that must not get lost.

Each of them answers a hole that really was in the app:

1. **The sessions' signing key.** When `SECRET_KEY` was not filled in, a fixed
   fallback value written in the source was used. Whoever knows it signs
   a cookie with somebody else's account and is inside as the administrator — without a password.

2. **The image proxy.** The id from the address was sent to Jellyfin as it
   came. It is put together into the path `/Items/<id>/Images/<kind>`, so
   a question mark or a slash in it changes what our server asks Jellyfin for —
   and it asks with our API key, that is with the administrator's rights.

3. **Guessing passwords.** Signing in could be tried for ever. Hashing is
   slow on purpose, but a dictionary attack overnight would work — and every
   attempt costs a quarter of a second of the processor, so it can flood the server too.

4. **/health without signing in** listed the size of the library and how much
   was playing right now.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bezpecnost.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
# As if a proxy stood in front of the app. Exactly in this setup the
# X-Forwarded-For header used to be trusted regardless of who sent it.
os.environ["FORWARDED_ALLOW_IPS"] = "127.0.0.1"

from jellyscope import accounts, config, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass", is_admin=False)


print("--- the sessions' signing key ---")
# Without SECRET_KEY a random one must be made and stored, not a fixed
# value taken from the source.
home = Path(tempfile.mkdtemp())
original_base = config.BASE_DIR
config.BASE_DIR = home
try:
    key = config._generated_secret_key()
    check(len(key) >= 32, f"the key made is long enough ({len(key)})")
    check(key != "nezabezpeceny-vychozi-klic", "it is not the fixed value from the source")
    check((home / "data" / "secret_key").is_file(), "it was stored on disk")
    check(config._generated_secret_key() == key,
          "after a restart it is the same - signing in does not expire")

    second_home = Path(tempfile.mkdtemp())
    config.BASE_DIR = second_home
    check(config._generated_secret_key() != key,
          "another installation gets another key")
finally:
    config.BASE_DIR = original_base

source = (PROJECT / "jellyscope" / "config.py").read_text(encoding="utf-8")
check('"nezabezpeceny-vychozi-klic"' not in source.split("def _generated_secret_key")[0],
      "no fixed key is left in the code")

# A SECRET_KEY from .env was taken in any length: a short word can be found from any cookie's signature offline,
# and with it a cookie of a Jellyfin administrator's account signed. It is still used (refusing to start would
# lock everybody out), but the log says so at every start.
import dataclasses  # noqa: E402
import logging  # noqa: E402


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


config_log = logging.getLogger("jellyscope.config")
records = _Records()
config_log.addHandler(records)
long_key = os.environ["SECRET_KEY"]
os.environ["SECRET_KEY"] = "kratkyklic"
try:
    short = config.load_config(reload=True)
finally:
    os.environ["SECRET_KEY"] = long_key
    config.load_config(reload=True)
check(short.secret_key == "kratkyklic", "a short SECRET_KEY from .env is still used - the start is not refused")
warned = hasattr(config, "warn_about_short_secret_key") and config.warn_about_short_secret_key(short)
said = [r for r in records.records if r.levelno == logging.WARNING and "SECRET_KEY" in r.getMessage()]
check(warned and len(said) == 1 and "kratkyklic" not in said[0].getMessage(),
      f"but the log warns about it, without the key itself ({[r.getMessage()[:60] for r in said]})")
records.records.clear()
check(hasattr(config, "warn_about_short_secret_key")
      and not config.warn_about_short_secret_key(dataclasses.replace(short, secret_key="x" * 32)) and not records.records,
      "32 characters and more: no warning")
config_log.removeHandler(records)
# The order of two calls in start.py said nothing about the file. A real start (the app's lifespan) with a short key
# in a home of its own, in a process of its own (its config and log handlers are not this test's), and its log file
# read afterwards: the warning is in it, the key is not.
import subprocess  # noqa: E402

start_home = tempfile.mkdtemp()
START = ("from fastapi.testclient import TestClient\nfrom jellyscope import applog, web\n"
         "with TestClient(web.app):\n    pass\nprint(applog.log_path())\n")
started = subprocess.run([sys.executable, "-c", START], cwd=PROJECT, capture_output=True, text=True, timeout=300,
                         env={**os.environ, "JELLYSCOPE_HOME": start_home, "SECRET_KEY": "kratkyklic",
                              "DATABASE_PATH": str(Path(start_home) / "start.db")})
start_log = Path((started.stdout.strip().splitlines() or ["-"])[-1])
start_text = start_log.read_text(encoding="utf-8") if start_log.is_file() else ""
check(started.returncode == 0 and start_home in str(start_log)
      and any("SECRET_KEY" in line and "WARNING" in line for line in start_text.splitlines()) and "kratkyklic" not in start_text,
      f"the app's start writes the warning into its log file, without the key ({started.returncode}, "
      f"{[line[:80] for line in start_text.splitlines() if 'SECRET_KEY' in line] or started.stderr[-200:]})")


print()
print("--- what the cookie must not carry waits in the server's memory ---")
# The session cookie is signed, not encrypted: the secret of a Quick Connect code and a freshly made API key sat in
# it readable (audit SA-11). Now they wait in the memory of the process under a random handle with a time limit,
# and the memory cannot grow without limit (tests/test_quick_connect.py and test_api_keys.py check the flows).
from jellyscope.web import base as web_base  # noqa: E402

web_base._held_secrets.clear()
first = web_base.hold_secret("the-first-secret", 60)
other = web_base.hold_secret("the-other-secret", 60)
check(first != other and len(first) >= 32 and "secret" not in first, f"every value gets a random handle of its own ({first})")
check(web_base.peek_secret(first) == "the-first-secret" and web_base.peek_secret(first) == "the-first-secret",
      "peek reads a value and keeps it (the Quick Connect secret, asked about every second)")
check(web_base.take_secret(other) == "the-other-secret" and web_base.take_secret(other) is None,
      "take reads a value once and forgets it (a fresh key, shown once)")
web_base.forget_secret(first)
check(web_base.peek_secret(first) is None, "forget drops a value")
expired = web_base.hold_secret("an-old-secret", -1)
check(web_base.peek_secret(expired) is None and web_base.take_secret(expired) is None and expired not in web_base._held_secrets,
      "a value past its time limit is gone")
try:
    odd = [web_base.peek_secret(h) for h in (None, "", "no-such-handle", 7, ["a"], {"a": 1})]
    odd += [web_base.take_secret(h) for h in (None, ["a"])]
    web_base.forget_secret(["a"])
except Exception as error:      # noqa: BLE001 - any error at all is the failure
    odd = [f"{type(error).__name__}: {error}"]
check(odd == [None] * 8, f"an unknown or odd handle (from a session of another version) is no error ({odd})")
web_base._held_secrets.clear()
handles = [web_base.hold_secret(f"value-{n}", 60) for n in range(web_base.HELD_SECRETS_MAX + 50)]
check(len(web_base._held_secrets) == web_base.HELD_SECRETS_MAX, f"the memory has a ceiling ({len(web_base._held_secrets)})")
check(web_base.peek_secret(handles[0]) is None
      and web_base.peek_secret(handles[-1]) == f"value-{web_base.HELD_SECRETS_MAX + 49}",
      "past it the oldest values go first, the newest stays")
web_base._held_secrets.clear()
import ast  # noqa: E402

STORE = {"hold_secret", "peek_secret", "take_secret", "forget_secret"}
store_names = {n.id for f in ast.walk(ast.parse((PROJECT / "jellyscope" / "web" / "base.py").read_text(encoding="utf-8")))
               if isinstance(f, ast.FunctionDef) and f.name in STORE for n in ast.walk(f) if isinstance(n, ast.Name)}
check(len(store_names) > 5 and not store_names & {"db", "log", "logging", "applog", "open", "Path", "web_sessions"},
      f"the store writes nowhere - no database, no file, no log ({sorted(store_names)})")


print()
print("--- the image proxy must not reach for anything but an image ---")
calls: list[str] = []


class FakeClient:
    """Writes down which address it would ask Jellyfin for."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def image_bytes(self, item_id: str, kind: str = "Primary",
                          max_width: int = 400) -> None:
        import httpx
        calls.append(str(httpx.URL("http://jellyfin:8096").join(
            f"/Items/{item_id}/Images/{kind}")))
        return None


import jellyscope.web as web  # noqa: E402
from jellyscope import images  # noqa: E402

# The images for the /image route (web/library.py) are fetched by the image store
# (jellyscope/images.py) - the stand-in must be where the client is read.
images.JellyfinClient = FakeClient  # type: ignore[assignment,misc]

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(web.app)
client.post("/login", data={"username": "reader", "password": "readerpass"})
# Only the images of ids Jellyscope knows are downloaded (tests/test_images.py, section G).
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type) VALUES ('abc123def', 'Film', 'Movie')")

# A clean request must pass.
calls.clear()
client.get("/image/abc123def")
check(calls == ["http://jellyfin:8096/Items/abc123def/Images/Primary"],
      f"an ordinary id asks for the image ({calls})")

# And these must not get to Jellyfin at all.
for written, label in [
    ("x%3Fapi_key%3Dcizi", "a question mark (slipped-in query parameters)"),
    ("..%2F..%2FSessions%3F", "a path out of /Items"),
    ("x%2F..%2FUsers", "a slash in the middle"),
    ("x%23kotva", "a hash"),
]:
    calls.clear()
    response = client.get(f"/image/{written}")
    check(response.status_code == 404 and not calls,
          f"{label}: refused ({response.status_code}), nothing to Jellyfin ({calls})")

# The image's kind and the width are taken from a fixed list, not from the address.
calls.clear()
client.get("/image/abc123def?kind=../../Users&w=99999")
check(calls == ["http://jellyfin:8096/Items/abc123def/Images/Primary"],
      f"an unknown kind falls back to Primary ({calls})")


print()
print("--- guessing passwords has a brake ---")
braked = TestClient(web.app)
states = []
for attempt in range(accounts.ATTEMPTS_BEFORE_BLOCK + 3):
    states.append(braked.post(
        "/login", data={"username": "admin", "password": f"wrong{attempt}"},
        follow_redirects=False).status_code)

check(states[0] == 401, f"the first attempt is an ordinary refusal ({states[0]})")
check(429 in states, f"after a few attempts the brake comes ({states})")
check(states.count(429) >= 3,
      f"and holds, not letting every other one through ({states.count(429)}x)")

# While the block holds, the right password does not get around it - otherwise it would be
# enough to keep guessing and try the last attempt with the right password.
sign_in = braked.post("/login",
                      data={"username": "admin", "password": "longpassword"},
                      follow_redirects=False)
check(sign_in.status_code == 429,
      f"the right password waits too, while the block lasts ({sign_in.status_code})")

# After unblocking it signs in normally - and leaves no record behind,
# so the next typo starts from the first level again.
for address in [r["ip"] for r in accounts.list_blocks()]:
    accounts.unblock(address)
sign_in = braked.post("/login",
                      data={"username": "admin", "password": "longpassword"},
                      follow_redirects=False)
check(sign_in.status_code == 303,
      f"after unblocking the right password passes ({sign_in.status_code})")
check(not accounts.list_blocks(),
      "and unblocking left no record behind")

# A forged header must not get around the brake.
#
# Behind a proxy the real address is filled in from X-Forwarded-For - but that
# header can be sent by an attacker too. While the app trusted it on its own,
# it was enough to write a different number into it every time and each password attempt
# counted separately: the brake formally existed and never kicked in. Now the address is taken
# from request.client, where uvicorn rewrites it from the header - and only when
# somebody from the list of trusted proxies sent it.
states = []
for attempt in range(accounts.ATTEMPTS_BEFORE_BLOCK + 3):
    states.append(braked.post(
        "/login", data={"username": "admin", "password": f"wrong{attempt}"},
        headers={"X-Forwarded-For": f"203.0.113.{attempt}"},
        follow_redirects=False).status_code)
check(429 in states, f"a different address in the header every time does not get around the brake ({states})")
check(len(accounts.list_blocks()) == 1,
      f"and it makes one block, not {accounts.ATTEMPTS_BEFORE_BLOCK} different "
      f"({[r['ip'] for r in accounts.list_blocks()]})")
for address in [r["ip"] for r in accounts.list_blocks()]:
    accounts.unblock(address)

# A sign-in of one's own account does not wipe the failures aimed at another name. It used to forget every
# failure of the address: whoever had any account guessed the administrator's password seven times, signed in
# as themselves, and again - for ever, without a block (audit SA-2).
accounts._attempts.clear()
guesser = TestClient(web.app)
for attempt in range(accounts.ATTEMPTS_BEFORE_BLOCK - 2):
    guesser.post("/login", data={"username": "admin", "password": f"guess{attempt}"}, follow_redirects=False)
own = guesser.post("/login", data={"username": "reader", "password": "readerpass"}, follow_redirects=False)
states = [guesser.post("/login", data={"username": "admin", "password": f"again{attempt}"},
                       follow_redirects=False).status_code for attempt in range(2)]
check(own.status_code == 303 and states == [401, 429],
      f"the reader's own sign-in leaves the guesses at the administrator counted ({own.status_code}, {states})")
# Nor the level of the block: the next block of the address is longer, whoever signs in meanwhile.
with db.connect() as conn:
    conn.execute("UPDATE login_blocks SET blocked_until = ?", ("2000-01-01 00:00:00",))
own = guesser.post("/login", data={"username": "reader", "password": "readerpass"}, follow_redirects=False)
check(own.status_code == 303 and [r["level"] for r in accounts.list_blocks()] == [1],
      f"and it does not delete the block's level ({own.status_code}, {accounts.list_blocks()})")
for address in [r["ip"] for r in accounts.list_blocks()]:
    accounts.unblock(address)

# A burst sent at once gets no more guesses than a series. The brake checked first and counted only after the
# answer: while the first requests waited for Jellyfin (or for the hash), all the others passed the check, every
# one of them got its guess, and one burst climbed to a permanent block (audit SS-3).
import asyncio  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402

from jellyscope import access, jellyfin  # noqa: E402

db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", "burst-test-key")
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
guessed: list[str] = []


async def slow_jellyfin(self: Any, username: str, password: str) -> None:
    guessed.append(password)
    await asyncio.sleep(0.05)
    return None


def slow_hash(password: str, stored: str) -> bool:
    guessed.append(password)
    time.sleep(0.05)
    return False


async def burst(address: str, path: str, data: dict[str, str], signed_in: bool = False) -> list[int]:
    transport = httpx.ASGITransport(app=web.app, client=(address, 4000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as parallel:
        if signed_in:
            await parallel.post("/login", data={"username": "reader", "password": "readerpass"})
            accounts.verify_password = slow_hash  # type: ignore[assignment]
        answers = await asyncio.gather(*(parallel.post(path, data={**data, "password": f"burst{index}"})
                                         for index in range(40)))
    return [answer.status_code for answer in answers]


def level_of(address: str) -> tuple[int, bool] | None:
    rows = [r for r in accounts.list_blocks() if r["ip"] == address]
    return (int(rows[0]["level"]), rows[0]["permanent"]) if rows else None


original_verify_login, original_verify_password = jellyfin.JellyfinClient.verify_login, accounts.verify_password
jellyfin.JellyfinClient.verify_login = slow_jellyfin  # type: ignore[method-assign]
try:
    # A name without a local account goes to Jellyfin (one Sign in button, 27. 11. 2026).
    statuses = asyncio.run(burst("198.51.100.7", "/login", {"username": "jf-guesser"}))
    check(len(guessed) == accounts.ATTEMPTS_BEFORE_BLOCK and statuses.count(429) == 40 - len(guessed),
          f"40 sign-ins at once through Jellyfin: {len(guessed)} reach Jellyfin as in a series, the rest wait "
          f"({statuses.count(429)}x 429)")
    check(level_of("198.51.100.7") == (1, False),
          f"and one burst raises the level once ({level_of('198.51.100.7')})")
    guessed.clear()
    asyncio.run(burst("198.51.100.8", "/account/password", {"current_password": "", "password_again": "x"},
                      signed_in=True))
    check(len(guessed) == accounts.ATTEMPTS_BEFORE_BLOCK,
          f"40 password changes at once: {len(guessed)} current passwords checked, as in a series")
    check(level_of("198.51.100.8") == (1, False), f"and the address is blocked once ({level_of('198.51.100.8')})")
finally:
    jellyfin.JellyfinClient.verify_login = original_verify_login  # type: ignore[method-assign]
    accounts.verify_password = original_verify_password  # type: ignore[assignment]
    db.set_setting(access.LOGIN_KEY, "0")
    db.forget_settings()
# The Jellyfin door refuses an empty password before asking Jellyfin (audit SA-7). Jellyfin lets a user without
# a password in with an empty one, and the question comes from Jellyscope's own address - Jellyfin's limits on
# remote access do not hold here. A profile without a password (a child's, often the owner's) opened Jellyscope
# to anybody who knew the name. It is a wrong password like any other, counted by the brake.
import re  # noqa: E402

from jellyscope import i18n  # noqa: E402

asked_jellyfin: list[str] = []


async def jellyfin_without_passwords(self: Any, username: str, password: str) -> dict[str, Any]:
    asked_jellyfin.append(password)
    return {"id": "f00df00df00df00df00df00df00df00d", "name": username, "is_admin": True, "is_disabled": False}


db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
accounts._attempts.clear()
jellyfin.JellyfinClient.verify_login = jellyfin_without_passwords  # type: ignore[method-assign]
empty_door = TestClient(web.app)
try:
    # "owner" has no local account, so it goes to Jellyfin; a wrong password of a local account is the comparison.
    empty_answers = [empty_door.post("/login", data={"username": "owner", **password},
                                     follow_redirects=False) for password in ({}, {"password": ""})]
    wrong_answer = TestClient(web.app).post("/login", data={"username": "reader", "password": "wrong-one"},
                                            follow_redirects=False)
finally:
    jellyfin.JellyfinClient.verify_login = original_verify_login  # type: ignore[method-assign]
    db.set_setting(access.LOGIN_KEY, "0")
    db.forget_settings()
check(not asked_jellyfin and [a.status_code for a in empty_answers] == [401, 401]
      and accounts.by_jellyfin_id("f00df00df00df00df00df00df00df00d") is None,
      f"an empty password through Jellyfin: refused before Jellyfin is asked ({[a.status_code for a in empty_answers]}, {asked_jellyfin})")
error_of = re.compile(r'<div class="flash error"><span class="dot"></span><span>(.*?)</span>', re.S)
check([error_of.findall(a.text) for a in empty_answers] == [error_of.findall(wrong_answer.text)] * 2
      and len(error_of.findall(wrong_answer.text)) == 1, "with the answer of any wrong name or password")
check(len(accounts._attempts.get("testclient", [])) == 3, "and each is counted by the brake like any failed attempt")
accounts._attempts.clear()
settings_admin = TestClient(web.app)
settings_admin.post("/login", data={"username": "admin", "password": "longpassword"})
jellyfin_settings = settings_admin.get("/settings?section=jellyfin").text
check(i18n.translate("A Jellyfin user without a password cannot sign in here with a password. A code (Quick Connect) "
                     "still works when Jellyfin allows it.") in jellyfin_settings,
      "Settings → Jellyfin says so by the switch of Jellyfin sign-in, a code only when Jellyfin allows it")
check("only with a code" not in jellyfin_settings, "no promise of a code as the one way in (Quick Connect may be off)")

# In a series the rule stays: seven wrong passwords and the right one sign in, the eighth wrong one blocks.
accounts._attempts.clear()
for attempt in range(accounts.ATTEMPTS_BEFORE_BLOCK - 1):
    guesser.post("/login", data={"username": "admin", "password": f"series{attempt}"}, follow_redirects=False)
right = guesser.post("/login", data={"username": "admin", "password": "longpassword"}, follow_redirects=False)
check(right.status_code == 303, f"seven wrong passwords and then the right one sign in ({right.status_code})")
# The hash of a local sign-in is computed in a thread. On the event loop its 600 000 iterations froze every other
# request, /health and the collector for a quarter of a second per attempt (audit SD-3).
on_loop: list[bool] = []
original_authenticate = accounts.authenticate


def probing_authenticate(username: str, password: str) -> dict[str, Any] | None:
    try:
        asyncio.get_running_loop()
        on_loop.append(True)
    except RuntimeError:
        on_loop.append(False)
    return original_authenticate(username, password)


accounts.authenticate = probing_authenticate  # type: ignore[assignment]
try:
    guesser.post("/login", data={"username": "reader", "password": "readerpass"}, follow_redirects=False)
finally:
    accounts.authenticate = original_authenticate  # type: ignore[assignment]
check(on_loop == [False], f"a local sign-in hashes off the event loop ({'on it' if any(on_loop) else on_loop})")
# Slow guessing is caught too. Only the last minute used to count, so seven guesses a minute - ten thousand a
# day from one address - never made a block (audit QW-1).
import logging  # noqa: E402


class Clock:
    """time.monotonic of the brake, moved by hand."""
    now = 1_000_000.0

    def monotonic(self) -> float:
        return self.now


clock, real_time = Clock(), accounts.time
accounts.time = clock  # type: ignore[assignment]
try:
    accounts._attempts.clear()
    tries, block = 0, None
    while block is None and tries < 7 * 60:
        clock.now += 60 / 7
        tries += 1
        block = accounts.count_failure("192.0.2.50", "admin")
    check(block is not None and block["level"] == 1,
          f"seven guesses a minute are blocked too ({'after ' + str(tries) if block else 'never in ' + str(tries)})")
    # Five thousand other addresses do not wipe the count of one more (it used to empty the whole memory).
    accounts.unblock("192.0.2.50")
    for index in range(5000):
        accounts.count_failure(f"10.{index // 250}.{index % 250}.1")
    for _ in range(accounts.ATTEMPTS_BEFORE_BLOCK - 1):
        accounts.count_failure("192.0.2.51")
    accounts.count_failure("10.250.0.1")
    check(accounts.count_failure("192.0.2.51") is not None,
          "a burst from thousands of addresses does not reset the count of another one")
finally:
    accounts.time = real_time  # type: ignore[assignment]
    accounts._attempts.clear()
    for address in [r["ip"] for r in accounts.list_blocks()]:
        accounts.unblock(address)

# A failed sign-in is written to the log, with the address and the door - not the name, nor the password.
caught_records: list[logging.LogRecord] = []
recorder = logging.Handler()
recorder.emit = caught_records.append  # type: ignore[method-assign]
web_log = logging.getLogger("jellyscope.web")
web_log.addHandler(recorder)
try:
    guesser.post("/login", data={"username": "admin", "password": "logged-guess", "method": "local\nforged"},
                 follow_redirects=False)
finally:
    web_log.removeHandler(recorder)
failed_lines = [r.getMessage() for r in caught_records if r.msg == "sign-in failed from %s (%s)"]
check(failed_lines == ["sign-in failed from testclient (local)"],
      f"a failed sign-in is in the log with the address and the door ({failed_lines})")
check(not any("logged-guess" in r.getMessage() for r in caught_records), "and the password is not")
accounts._attempts.clear()
# Whatever path counts a failure during a running block, the level does not climb with it.
for _ in range(accounts.ATTEMPTS_BEFORE_BLOCK * 2):
    accounts.count_failure("198.51.100.7", "admin")
check(level_of("198.51.100.7") == (1, False),
      f"failures during a running block do not raise its level ({level_of('198.51.100.7')})")
for address in [r["ip"] for r in accounts.list_blocks()]:
    accounts.unblock(address)
accounts._attempts.clear()


# IPv6: one host owns a whole /64 and takes a new address for every few guesses. The brake counts the network,
# an IPv4 address written as IPv6 as the IPv4 one (audit SF-13).
for index in range(accounts.ATTEMPTS_BEFORE_BLOCK - 1):
    accounts.count_failure(f"2001:db8:1:2::{index + 1:x}", "admin")
block = accounts.count_failure("2001:db8:1:2:ffff:ffff:ffff:fffe", "admin")
check(bool(block) and [r["ip"] for r in accounts.list_blocks()] == ["2001:db8:1:2::/64"],
      f"eight addresses of one IPv6 /64 make one block of the network ({[r['ip'] for r in accounts.list_blocks()]})")
check(accounts.block_remaining("2001:db8:1:2:abcd::9") > 0 and accounts.block_remaining("2001:db8:1:3::1") == 0,
      "another address of the network waits too, the neighbouring network does not")
check(accounts.unblock("2001:db8:1:2::77") and not accounts.list_blocks(), "any address of the network lifts it")
for _ in range(accounts.ATTEMPTS_BEFORE_BLOCK):
    accounts.count_failure("::ffff:192.0.2.60", "admin")
check(accounts.block_remaining("192.0.2.60") > 0, "an IPv4-mapped address counts as the IPv4 one")
accounts.unblock("192.0.2.60")
accounts._attempts.clear()

# The brake keeps no typed name: a name is as long as the form lets it be (a megabyte), and whoever is not signed in
# filled the memory with them - a few addresses, a few attempts each, never a block (the review of the audit fixes).
accounts.begin_attempt("198.51.100.77", "x" * 1_000_000)
kept = accounts._attempts["198.51.100.77"][-1][1]
check(kept is not None and len(kept) <= 64, f"an attempt keeps a short mark of the name, not the name ({len(kept or '')} characters)")
accounts._attempts.clear()
# The mark holds the door too: a local account and a user of Jellyfin may share a name, and a success at one door
# must not forget the guesses at the other.
for attempt in range(3):
    accounts.begin_attempt("198.51.100.78", "jellyfin:petr")
accounts.begin_attempt("198.51.100.78", "local:petr")
accounts.forget_failures("198.51.100.78", "local:petr")
check(len(accounts._attempts.get("198.51.100.78", [])) == 3, "a success at one door leaves the guesses at the other door counted")
accounts._attempts.clear()
door = (PROJECT / "jellyscope" / "web" / "login.py").read_text(encoding="utf-8")
check(door.count("_brake_name(door, username)") == 2 and 'accounts.begin_attempt(address, username)' not in door,
      "the sign-in counts and forgets with the door in the name")

# Blocks stored by an older version under the whole address still hold after the upgrade: the brake now counts an
# IPv6 address as its /64 and an IPv4-mapped one as IPv4, and rows under the old keys were found by nothing.
with db.connect() as conn:
    conn.execute("DELETE FROM login_blocks")
    conn.execute("INSERT INTO login_blocks (ip, level, blocked_until, permanent, last_failure) VALUES ('2001:db8:9:9::5', 2, NULL, 1, ?)", (db.utcnow(),))
    conn.execute("INSERT INTO login_blocks (ip, level, blocked_until, permanent, last_failure) VALUES ('2001:db8:9:9::6', 4, NULL, 0, ?)", (db.utcnow(),))
    conn.execute("INSERT INTO login_blocks (ip, level, blocked_until, permanent, last_failure) VALUES ('::ffff:192.0.2.61', 1, NULL, 1, ?)", (db.utcnow(),))
    conn.commit()
converted = accounts.convert_block_keys()
rows = {r["ip"]: (r["level"], bool(r["permanent"])) for r in accounts.list_blocks()}
check(converted == 3 and rows == {"2001:db8:9:9::/64": (4, True), "192.0.2.61": (1, True)},
      f"old rows move under today's keys, two of one network join (the higher level, permanent stays) ({converted}, {rows})")
check(accounts.block_remaining("2001:db8:9:9::5") != 0 and accounts.block_remaining("192.0.2.61") != 0 and accounts.convert_block_keys() == 0,
      "the old permanent blocks hold again, and a second run changes nothing")
check(accounts.unblock("2001:db8:9:9::1") and accounts.unblock("192.0.2.61") and not accounts.list_blocks(), "and they can be lifted")

# A local password an older version let the administrator give to an account from Jellyfin: the account belongs to
# Jellyfin, such a password could no longer be changed or removed and opened the account past Jellyfin for ever.
with db.connect() as conn:
    conn.execute("INSERT INTO accounts (username, password_hash, is_admin, created_at, jellyfin_user_id)"
                 " VALUES ('olda', ?, 1, ?, 'u-olda-stale')", (accounts.hash_password("oldlocal1"), db.utcnow()))
    conn.commit()
db.set_setting(accounts.JELLYFIN_PASSWORDS_KEY, "")
check(accounts.authenticate("olda", "oldlocal1") is not None, "(before the start's conversion the old password still opens the account)")
cleared = accounts.clear_jellyfin_passwords()
check(cleared == 1 and accounts.authenticate("olda", "oldlocal1") is None and accounts.authenticate("admin", "longpassword") is not None,
      f"the start clears the local passwords of accounts from Jellyfin once; local accounts keep theirs ({cleared})")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET password_hash = ? WHERE username = 'olda'", (accounts.hash_password("oldlocal1"),))
    conn.commit()
check(accounts.clear_jellyfin_passwords() == 0 and accounts.authenticate("olda", "oldlocal1") is not None,
      "only once per database (tests make viewers out of local accounts after it)")
with db.connect() as conn:
    conn.execute("DELETE FROM accounts WHERE username = 'olda'")
    conn.commit()

# The time of the answer does not tell which names have an account from Jellyfin. An unknown name had its hash
# computed for nothing, but an account from Jellyfin has no password here and was refused at once (audit SA-10).
import hashlib  # noqa: E402
import types  # noqa: E402

with db.connect() as conn:
    conn.execute("INSERT INTO accounts (username, password_hash, is_admin, created_at, jellyfin_user_id)"
                 " VALUES ('mirek', '', 0, ?, 'u-mirek-timing')", (db.utcnow(),))
hashes: list[int] = []
accounts.hashlib = types.SimpleNamespace(  # type: ignore[assignment]
    pbkdf2_hmac=lambda *a, **k: hashes.append(1) or hashlib.pbkdf2_hmac(*a, **k))
try:
    counted = {}
    for label, name in (("an unknown name", "nobody-here"), ("a local account", "reader"),
                        ("an account from Jellyfin", "mirek")):
        hashes.clear()
        accounts.authenticate(name, "a-wrong-password")
        counted[label] = len(hashes)
finally:
    accounts.hashlib = hashlib  # type: ignore[assignment]
check(set(counted.values()) == {1}, f"a refused local sign-in computes one hash whoever the name is ({counted})")

# Blocks escalate: each next one in a row lasts longer, the fourth is permanent already.
accounts.unblock("10.0.0.9")
levels = []
for _ in range(len(accounts.BLOCK_STEPS) + 1):
    for _ in range(accounts.ATTEMPTS_BEFORE_BLOCK - 1):
        ignored = accounts.count_failure("10.0.0.9")
    block = accounts.count_failure("10.0.0.9")
    levels.append("permanent" if block["permanent"] else block["seconds"])
    # The unlocking time cannot be hurried, so we just cancel the running pause - the level
    # stays, because the `level` column decides it.
    with db.connect() as conn:
        conn.execute("UPDATE login_blocks SET blocked_until = ? WHERE ip = ?",
                     ("2000-01-01 00:00:00", "10.0.0.9"))

check(levels == [60, 120, 300, 900, "permanent"],
      f"each next block is longer, the fifth permanent ({levels})")
check(accounts.block_remaining("10.0.0.9") == -1, "a permanent block does not dissolve")

listing = {r["ip"]: r for r in accounts.list_blocks()}
check("10.0.0.9" in listing, "the block is seen in the list for Settings")
check(listing["10.0.0.9"]["permanent"] is True, "and it is marked as permanent")

check(accounts.unblock("10.0.0.9"), "the administrator can cancel it")
check(accounts.block_remaining("10.0.0.9") == 0, "after unblocking one can try at once")
check(not accounts.unblock("10.0.0.9"), "the second time there is nothing to cancel")

# A block survives a restart of the app - otherwise it would be enough to wait for an update.
accounts._block("10.0.0.10")
accounts._attempts.clear()
check(accounts.block_remaining("10.0.0.10") > 0,
      "the block is in the database, not only in the process's memory")
accounts.unblock("10.0.0.10")


print()
print("--- controlling blocks is for the administrator only ---")
response = client.post("/settings/blocks/unblock", data={"ip": "1.2.3.4"},
                       follow_redirects=False)
check(response.status_code == 403, f"a reader may not unblock ({response.status_code})")
# All of Settings is for the administrator, so a reader does not get to the section with
# the blocks already at the door - does not even see the list of addresses.
check(client.get("/settings?section=blocks").status_code == 403,
      "and does not even get the section")


print()
print("--- /health without signing in gives away no content ---")
anonymous = TestClient(web.app)
public = anonymous.get("/health").json()
check(set(public) == {"status", "started_at"},
      f"a signed-out visitor sees only the state and the start time ({sorted(public)})")
check(public["status"] == "ok", "monitoring can tell the app is alive")

signed_in = client.get("/health").json()
check("library_version" in signed_in and "active_sessions" in signed_in,
      f"a signed-in one sees everything ({sorted(signed_in)})")


print()
print("--- the app's description for machines (OpenAPI) is not out ---")
# Otherwise FastAPI would hand anybody a list of all addresses with the descriptions of the
# routes at /openapi.json - a map of the app including the administrator's actions. Jellyscope has
# its own documentation of the read-only API (Settings -> API), nobody needs the machine description.
for address in ("/openapi.json", "/docs", "/redoc"):
    response = anonymous.get(address, follow_redirects=False)
    check(response.status_code == 404 and '"openapi"' not in response.text and "swagger" not in response.text.lower(),
          f"{address}: a signed-out visitor gets 404, not the app's description ({response.status_code}, {len(response.content)} B)")
    response = client.get(address, follow_redirects=False)
    check(response.status_code in (403, 404) and '"openapi"' not in response.text,
          f"{address}: a signed-in one does not get it either ({response.status_code})")
check(web.app.openapi_url is None and web.app.docs_url is None and web.app.redoc_url is None,
      f"the app hands out neither the machine description nor its viewers ({web.app.openapi_url}, {web.app.docs_url}, {web.app.redoc_url})")

print()
print("--- without signing in only the listed addresses ---")
# A page is protected by the guard at the route (Depends), not by the middleware: the middleware
# lets a signed-out visitor on, because the sign-in page must be seen. A route without a guard - or an
# address a library adds on its own, like /openapi.json - is therefore open to anybody. Here is their list.
from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from jellyscope.legacy_urls import LEGACY_URLS  # noqa: E402

sys.path.insert(0, str(PROJECT / "tests"))
from _package import flat_routes  # noqa: E402

GUARDS = {"require_login", "require_admin", "verify_token", "_admin_only"}
PUBLIC = ({"/setup", "/login", "/login/quick", "/login/quick/status", "/logout", "/health"}
          # Old addresses only redirect to the new ones (those have their guard already).
          | {old for old, _new, _methods in LEGACY_URLS})


def without_guard(application: Any) -> set[str]:
    """Addresses without a sign-in guard: routes without one and everything that is not a route of the app (except /static)."""
    found: set[str] = set()
    for route in flat_routes(application.router.routes):
        if not isinstance(route, APIRoute):
            if getattr(route, "path", "") != "/static":
                found.add(getattr(route, "path", "?"))
            continue
        dependencies, queue = set(), [route.dependant]
        while queue:
            dependency = queue.pop()
            queue += dependency.dependencies
            if dependency.call is not None:
                dependencies.add(getattr(dependency.call, "__name__", ""))
        if not dependencies & GUARDS:
            found.add(route.path)
    return found


open_addresses = without_guard(web.app)
check(open_addresses == PUBLIC,
      f"without a guard are only signing in, the first start, /health and the redirects of old addresses "
      f"(extra {sorted(open_addresses - PUBLIC)}, missing {sorted(PUBLIC - open_addresses)})")
# A trap: an app created the way it used to be (without openapi_url=None) must be recognised.
check("/openapi.json" in without_guard(FastAPI(docs_url=None, redoc_url=None)),
      "and the check recognises an address a library adds on its own (/openapi.json)")


def guards_of(route: APIRoute) -> set[str]:
    """The names of every dependency of a route, the router's ones included."""
    names, queue = set(), [route.dependant]
    while queue:
        dependency = queue.pop()
        queue += dependency.dependencies
        if dependency.call is not None:
            names.add(getattr(dependency.call, "__name__", ""))
    return names


# What changes something is the administrator's: the middleware holds only a viewer to VIEWER_MAY_POST, a reader is
# held by the route's guard alone. A writing route with just require_login under an area a reader opens would be
# theirs. The only writing routes without an administrator's guard: the sign-in and one's own account.
ADMIN_GUARDS = {"require_admin", "_admin_only"}
api_routes = [r for r in flat_routes(web.app.router.routes) if isinstance(r, APIRoute)]
writes_for_all = {r.path for r in api_routes if set(r.methods or ()) - {"GET", "HEAD"} and not guards_of(r) & ADMIN_GUARDS}
expected = {"/setup", "/login", "/login/quick"} | set(web.VIEWER_MAY_POST)
check(writes_for_all == expected,
      f"every writing route but the sign-in and one's own account has an administrator's guard "
      f"(extra {sorted(writes_for_all - expected)}, missing {sorted(expected - writes_for_all)})")
settings_page = [r for r in api_routes if r.path == "/settings" and "GET" in (r.methods or ())]
check(len(settings_page) == 1 and guards_of(settings_page[0]) & ADMIN_GUARDS,
      "Settings (with every account and its rights) is guarded for the administrator by the route itself too")

print()
print("--- what must not get into a page ---")
# Jellyfin's API key is a password to somebody else's server. It does not belong in HTML, not even by mistake.
db.set_setting("jellyfin_api_key", "TAJNY-KLIC-1234567890")
admin_client = TestClient(web.app)
admin_client.post("/login", data={"username": "admin", "password": "longpassword"})
for path in ["/settings?section=jellyfin", "/settings?section=data", "/"]:
    content = admin_client.get(path).text
    check("TAJNY-KLIC-1234567890" not in content, f"{path}: the key is not there")

# A title's name goes into a chart as text, not as HTML.
from jellyscope import charts  # noqa: E402

attack = '<img src=x onerror=alert(1)>'
chart = charts.hbar_chart([{"label": attack, "value": 5}], "label", "value")
check(attack not in chart and "&lt;img" in chart,
      "a name with an HTML tag is written out as text in a chart")

# The same for the tooltip with the values. It comes into the page as JSON in an attribute
# and the browser puts nodes together from it through textContent - so not even a movie's name
# with <img onerror=...> has a way of becoming a tag.
tooltip = charts.area_chart_multi(
    [{"d": "2026-08-11", "v": 1}], "d", [{"key": "v", "label": attack, "slot": 1}])
check(attack not in tooltip and "&lt;img" in tooltip,
      "a name with an HTML tag is escaped in the tooltip too")

# Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
base = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
        + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
handler = base[base.index("function fillTooltip"):]
handler = handler[:handler.index("document.addEventListener")]
check("innerHTML" not in handler,
      "the tooltip is put together from nodes, not by inserting HTML")
check("JSON.parse" in handler and "textContent" in handler,
      "the values are read as data and written as text")


print()
print("--- the response headers ---")
headers = admin_client.get("/").headers
for name, value in [("X-Content-Type-Options", "nosniff"),
                    ("X-Frame-Options", "SAMEORIGIN"),
                    ("Referrer-Policy", "same-origin")]:
    check(headers.get(name) == value,
          f"{name}: {headers.get(name)!r}")

# The app needs none of these, so not even a piece of a page that got in here by
# mistake can ask for them.
rules = headers.get("Permissions-Policy", "")
check(all(f"{what}=()" in rules
          for what in ("camera", "microphone", "geolocation")),
      f"Permissions-Policy forbids the camera, the microphone and the location ({rules!r})")

# HSTS only over HTTPS: the browser remembers it for a long time, so switched on on a
# server without a certificate it would lock people out.
check("Strict-Transport-Security" not in headers,
      "HSTS is not sent without HTTPS")

# A page behind the sign-in must not be kept: on a shared computer the Back button after signing out would show it
# again from the browser's memory (history with names and addresses, the accounts in Settings).
for path in ("/history", "/history/new", "/settings?section=accounts", "/api/now-playing", "/partials/now-playing"):
    value = admin_client.get(path).headers.get("Cache-Control")
    check(value == "no-store", f"{path} signed in: Cache-Control {value!r}")
value = admin_client.get("/static/style.css").headers.get("Cache-Control")
check(value != "no-store", f"the app's files are kept as before ({value!r})")
value = TestClient(web.app).get("/login").headers.get("Cache-Control")
check(value != "no-store", f"and so is the sign-in page without an account ({value!r})")

print()
print("--- the cards of an open page are drawn by the rights of who asks ---")
# /partials/cards draws any card of the registry by its key (web/dashboards.py). The address is open to every
# account (access.ALWAYS), so the rights are the route's own, card by card: a card of an area the account does not
# see is missing exactly as a key that does not exist - the answer does not tell what is there.
from jellyscope import access  # noqa: E402

asked = "/partials/cards?keys=" + ",".join(("summary", "map", "<script>alert(1)</script>", "no_such_card"))
signed_out = TestClient(web.app).get(asked, follow_redirects=False)
check(signed_out.status_code == 303 and "card-slot" not in signed_out.text,
      f"signed out: no card, only the way to sign in ({signed_out.status_code})")
answer = admin_client.get(asked)
check(answer.status_code == 200 and answer.headers.get("Cache-Control") == "no-store"
      and 'data-card-key="map"' in answer.text and "<script>alert" not in answer.text and "alert(1)" not in answer.text,
      "signed in: not kept by the browser, and a key from the address is never written back")
# Not only the keys: every value of the address the route reads (the cards' choices, the period) is checked against
# what it may be - a mark put into all of them is nowhere in the answer, neither as it came nor escaped.
from urllib.parse import urlencode  # noqa: E402

MARK = "QXfromTheAddress"
hostile = f'"><script>{MARK}</script>'
everywhere = "/partials/cards?" + urlencode({
    "keys": ",".join(("summary", "watching_by_day", "most_watched", "library_growth", "live_traffic", hostile)),
    "kind": hostile, "top_kind": hostile, "growth_disk": hostile, "since": hostile, "until": hostile, "in_dashboard": "1"})
answer = admin_client.get(everywhere, headers={"X-Requested-With": "fetch"})
check(answer.status_code == 200 and 'data-card-key="watching_by_day"' in answer.text and MARK not in answer.text,
      f"a value from the address in every parameter the route reads is written back nowhere ({answer.status_code},"
      f" {answer.text.count(MARK)} times)")
reader_id = accounts.get_by_name("reader")["id"]
with db.connect() as conn:
    conn.execute("UPDATE accounts SET permissions = ? WHERE id = ?",
                 (access.pack_permissions([a.key for a in access.AREAS if a.key != "network"], False), reader_id))
    conn.commit()
db.forget_settings()
reader_answer = client.get(asked)
check(reader_answer.status_code == 200 and 'data-card-key="summary"' in reader_answer.text
      and 'data-card-key="map"' not in reader_answer.text
      and client.get("/partials/cards?keys=map").text == client.get("/partials/cards?keys=no_such_card").text,
      "a reader without Network gets the other cards, the map not - the same answer as for an unknown key")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET permissions = NULL WHERE id = ?", (reader_id,))
    conn.commit()
db.forget_settings()

print()
print("--- the collector's error text is the administrator's ---")
# It can carry an internal address, a path in Jellyfin's API and a piece of a proxy's error page - the texts of
# other applications are the administrator's elsewhere too (cards_seerr, the *Arr cards).
from jellyscope import collector, jellyfin  # noqa: E402

collector.store_error(jellyfin.JellyfinError("Jellyfin returned error {status} on {path}: {body}", status=502,
                                             path="/Sessions", body="InternalGateQX 10.0.0.2"))
reader_panel = client.get("/account").text
check("InternalGateQX" in admin_client.get("/account").text, "the administrator reads the error in the side panel")
check("InternalGateQX" not in reader_panel and "badge critical" in reader_panel,
      "a reader sees that the collector failed, not its text")
collector.clear_error()
db.set_setting(collector.STATUS_KEY, "unknown")

print()
print("--- where a page may load from and where it may send ---")
# Jellyscope has not a single foreign source: no CDN, no font from the internet,
# we lead the images through our own server. The rule can therefore read "only from
# here" - and above all: should a foreign script get into a page through a mistake,
# this takes away what matters to it. The data has nowhere to be sent (connect-src),
# a form has nowhere to redirect (form-action) and further code has nowhere to be
# downloaded from (script-src without a foreign source).
csp = headers.get("Content-Security-Policy", "")
for rule in ("default-src 'self'", "img-src 'self'", "connect-src 'self'",
             "form-action 'self'", "frame-ancestors 'self'",
             "base-uri 'self'", "object-src 'none'"):
    check(rule in csp, rule)
# 'unsafe-eval' is needed nowhere - and should it appear, CSP would stop
# being a defence against inline code.
check("unsafe-eval" not in csp, "eval is allowed nowhere")
check(headers.get("Content-Security-Policy") ==
      admin_client.get("/settings").headers.get("Content-Security-Policy"),
      "the same header on every page")

print()
print("--- the ceiling on an uploaded file holds while reading ---")
# The ceiling is here because of memory. When a file is read whole first and only
# then measured, it is a defence that comes after the damage: a gigabyte file
# brings the server down before it can say it is too big.
import asyncio  # noqa: E402

from jellyscope.web import settings as web_settings  # noqa: E402


class EndlessFile:
    """Pretends to be an uploaded file that never ends."""

    def __init__(self) -> None:
        self.read_so_far = 0

    async def read(self, size: int = -1) -> bytes:
        # Without a ceiling this would run until memory ran out.
        how_much = size if size > 0 else 4 * 1024 * 1024
        self.read_so_far += how_much
        return b"x" * how_much


class FakeRequest:
    """Only as much of a Request as the message needs."""

    def __init__(self) -> None:
        self.session: dict = {}


file = EndlessFile()
result = asyncio.run(web_settings._load_backup(FakeRequest(), file))
ceiling = web_settings.MAX_UPLOAD_MB * 1024 * 1024
check(result is None, "a file too big is refused")
check(file.read_so_far <= ceiling + 2 * 1024 * 1024,
      f"and reading stopped right past the ceiling ({file.read_so_far // 1024 // 1024} MB "
      f"with a ceiling of {web_settings.MAX_UPLOAD_MB} MB)")


print()
print("--- a request's body has a ceiling before the sign-in check ---")
# Nothing limited a body: every plain form is read by the check of old field names (a dependency of every route)
# and an upload by FastAPI before the routes' guards - so anybody who reached the port held a gigabyte in memory
# or on the disk with a POST to /login before the answer "sign in first".
import inspect  # noqa: E402

import fastapi.params  # noqa: E402
from jellyscope.web import body_limit  # noqa: E402

sys.path.insert(0, str(PROJECT / "tests"))
from _package import flat_routes  # noqa: E402

CEILING = body_limit.MAX_BODY_BYTES
too_much = b"x" * (3 * CEILING)
# Many fields, each under Starlette's own ceiling of one field (1 MB): together three times the form ceiling.
many_fields = b"&".join(b"f%d=" % i + b"x" * (CEILING // 2) for i in range(6))


async def _drive(path: str, chunks: list[bytes], content_type: str, cookie: str = "") -> tuple[int, int, str]:
    """A POST straight into the app, the body in pieces without Content-Length (chunked): (status, bytes read, Location)."""
    pending, read, sent = list(chunks), 0, []
    finished = asyncio.Event()

    async def receive() -> dict:
        nonlocal read
        if pending:
            chunk = pending.pop(0)
            read += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            finished.set()

    headers = [(b"host", b"testserver"), (b"content-type", content_type.encode())]
    if cookie:
        headers.append((b"cookie", f"jellyscope_session={cookie}".encode()))
    await web.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                   "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                   "headers": headers, "client": ("127.0.0.1", 50000), "server": ("testserver", 80)}, receive, send)
    start = next((m for m in sent if m["type"] == "http.response.start"), {"status": 0, "headers": []})
    location = dict(start["headers"]).get(b"location", b"").decode()
    return start["status"], read, location


def drive(path: str, content_type: str = "application/x-www-form-urlencoded", cookie: str = "",
          body: bytes = many_fields) -> tuple[int, int, str]:
    return asyncio.run(_drive(path, [body[i:i + 65536] for i in range(0, len(body), 65536)], content_type, cookie))


check(CEILING <= 2 * 1024 ** 2, f"the ceiling of a form is small ({CEILING} B)")
anonymous_client = TestClient(web.app)
answer = anonymous_client.post("/login", content=many_fields,
                               headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)
check(answer.status_code == 413, f"a form over the ceiling to /login: 413 by its declared length ({answer.status_code})")
status, read, _ = drive("/settings/accounts/create")
check(status == 413 and read <= CEILING + 65536,
      f"sent in pieces without a length: 413 and read only up to the ceiling ({status}, {read} of {len(many_fields)} B)")
upload = {"backup": ("backup.json", too_much, "application/json")}
answer = anonymous_client.post("/settings/import/jellystat", files=upload, follow_redirects=False)
check(answer.status_code == 413, f"an upload without signing in: 413, not stored first ({answer.status_code})")
boundary = "jellyscopeboundary"
multipart = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"backup\"; filename=\"b.json\"\r\n"
             f"Content-Type: application/json\r\n\r\n").encode() + too_much + f"\r\n--{boundary}--\r\n".encode()
status, read, _ = drive("/login", f"multipart/form-data; boundary={boundary}", body=multipart)
check(status == 413 and read <= CEILING + 65536, f"an upload in pieces to /login: 413 at the ceiling ({status}, {read} B)")
answer = anonymous_client.post("/login", data={"username": "admin", "password": "wrong-password"},
                               follow_redirects=False)
check(answer.status_code == 401, f"an ordinary form passes as before ({answer.status_code})")
accounts.unblock("testclient")

# The administrator's history import keeps the upload's own ceiling (MAX_UPLOAD_MB).
session_cookie = admin_client.cookies.get("jellyscope_session") or ""
answer = admin_client.post("/settings/import/jellystat", files=upload, follow_redirects=False)
check(answer.status_code == 303 and answer.headers.get("location") == "/settings?section=import",
      f"the administrator's upload over the form ceiling reaches the import ({answer.status_code})")
status, read, location = drive("/settings/import/jellystat", f"multipart/form-data; boundary={boundary}",
                               session_cookie, multipart)
check(status == 303 and read == len(multipart), f"and so does one sent in pieces ({status}, {read} B)")
upload_ceiling = body_limit.MAX_UPLOAD_MB
body_limit.MAX_UPLOAD_MB = 1
try:
    answer = admin_client.post("/settings/import/jellystat", files=upload, follow_redirects=False)
    page = admin_client.get("/settings?section=import").text if answer.status_code == 303 else ""
    status, read, location = drive("/settings/import/jellystat", f"multipart/form-data; boundary={boundary}",
                                   session_cookie, multipart)
finally:
    body_limit.MAX_UPLOAD_MB = upload_ceiling
check(answer.status_code == 303 and "1 MB" in page,
      f"over the upload's ceiling: back to Import with the reason, as before ({answer.status_code})")
check(status == 303 and location == "/settings?section=import" and read < len(multipart),
      f"in pieces too, and the rest is not read ({status}, {read} of {len(multipart)} B)")

uploads = {route.path for route in flat_routes(web.app.routes) if hasattr(route, "endpoint")
           and any(isinstance(p.default, fastapi.params.File) for p in inspect.signature(route.endpoint).parameters.values())}
check(uploads == set(body_limit.UPLOAD_PATHS),
      f"the routes with an uploaded file are exactly the ones with the upload's ceiling ({sorted(uploads)})")

print()
print("--- a translation by a stranger must not run anything ---")
# Since translating through Weblate is possible (see TRANSLATING.md), the interface's
# texts are written by strangers. Charts go into the page through `| safe`,
# so a translated sentence in them is HTML - and a tag in a translation would be
# runnable code. Exactly this happened in the heatmap: the day's abbreviation
# went in without escaping.
from jellyscope import charts, i18n, updates  # noqa: E402

ATTACK = "</div><script>alert(1)</script>"
original = {}
# Planted in the Czech translation: English is the source and has no table of its own.
for key in [charts.DAY_NAMES[0], "No data yet", "Drag across the chart to pick a range."]:
    original[key] = i18n.TRANSLATIONS["cs"].get(key)
    i18n.TRANSLATIONS["cs"][key] = ATTACK

original_language = db.get_setting("ui_language", "cs")
db.set_setting("ui_language", "cs")
db.forget_settings()
try:
    rendered = {
        "heatmap": charts.heatmap([[1] * 24 for _ in range(7)]),
        "an empty chart": charts.hbar_chart([], "label", "value"),
        "an area chart": charts.area_chart_multi(
            [{"day": "2026-09-01", "gb": 1}, {"day": "2026-09-02", "gb": 2}],
            "den", [{"key": "gb", "label": "GB"}], selectable=True),
    }
    for name, html in rendered.items():
        check("<script>" not in html, f"{name}: a tag from a translation does not run")
        check("&lt;script&gt;" in html or ATTACK not in html,
              f"{name}: the text is shown as text")
finally:
    for key, value in original.items():
        if value is None:
            i18n.TRANSLATIONS["cs"].pop(key, None)
        else:
            i18n.TRANSLATIONS["cs"][key] = value
    db.set_setting("ui_language", original_language)
    db.forget_settings()

# A trap: should escaping get lost somewhere, this attack gets through. A proof
# that the test can fail - the dangerous string really is dangerous.
check("<script>" in ATTACK, "and the attacking string really is runnable code")

print()
print("--- the waiting page after an update and a translation with an apostrophe ---")
# The waiting page is put together in Python (without a template, see _update_page),
# so Jinja's autoescape does not protect it. A translation from Weblate like "It's taking
# longer" would cause a SyntaxError in a JS string in apostrophes: the function zkus()
# would not come into being and the waiting page would stay at "Updating..." for ever after an update.
# And a tag in a translation would be rendered as HTML.
import json as _json  # noqa: E402
import re  # noqa: E402

from jellyscope.web import settings as _wn  # noqa: E402

ATTACK_APOSTROPHE = "It's </p><img src=x onerror=alert(1)>"
waiting_keys = ["Updating…", "This is taking longer than it should.", "Try again",
                "The new version is downloaded. The app is restarting now. As soon as it is up, you will be let through."]
original = {k: i18n.TRANSLATIONS["cs"].get(k) for k in waiting_keys}
for key in waiting_keys:
    i18n.TRANSLATIONS["cs"][key] = ATTACK_APOSTROPHE
original_language = db.get_setting("ui_language", "cs")
db.set_setting("ui_language", "cs")
db.forget_settings()
try:
    waiting_page = _wn._update_page("testovaci-nonce")
finally:
    for key, value in original.items():
        if value is None:
            i18n.TRANSLATIONS["cs"].pop(key, None)
        else:
            i18n.TRANSLATIONS["cs"][key] = value
    db.set_setting("ui_language", original_language)
    db.forget_settings()
check("<img" not in waiting_page, "a tag from a translation is not rendered in the waiting page")
expression = re.search(r"innerHTML\s*=\s*(.+?);\s*\n", waiting_page, re.S)
try:
    js_value = _json.loads(expression.group(1)) if expression else None
except ValueError:
    js_value = None
check(js_value is not None and "<img" not in js_value,
      "the text for a slow restart is a valid JS string, with an apostrophe too")

print()
print("--- texts in JavaScript go through tojson ---")
# The same in the templates. In <script> autoescape makes &#39; of an apostrophe, which
# then shows literally. In an attribute onsubmit="confirm('...')" it is worse:
# the browser turns &#39; back into an apostrophe before running it, the JS ends with an error
# and the form is sent WITHOUT the confirmation - the deletion of an account or a backup, say.
# Translations come from Weblate straight to the users (an update of the
# translations), without a release and without a check.
def _js_expressions(text: str) -> list[str]:
    expressions = []
    # re.I: the tag's letter case does not matter, and the end tag may have a space
    # or attributes (</script >) - both are watched by CodeQL py/bad-tag-filter.
    for block in re.findall(r"<script\b[^>]*>(.*?)</script\b[^>]*>", text, re.S | re.I):
        expressions += re.findall(r"\{\{(.*?)\}\}", block, re.S)
    for start in re.finditer(r"\son[a-z]+=([\"'])", text):
        quote, i, value = start.group(1), start.end(), ""
        while i < len(text) and text[i] != quote:
            if text.startswith("{{", i):
                end = text.find("}}", i)
                value += text[i:end + 2]
                i = end + 2
            else:
                i += 1
        expressions += re.findall(r"\{\{(.*?)\}\}", value, re.S)
    return expressions


without_tojson = []
for template in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html")):
    for expression in _js_expressions(template.read_text(encoding="utf-8")):
        if "_(" in expression and "tojson" not in expression:
            without_tojson.append(f"{template.name}: {expression.strip()[:50]}")
check(not without_tojson, f"no translation in JS without tojson ({len(without_tojson)}: {without_tojson[:4]})")
# A trap: the search must find a translation in an attribute and in a script.
check(len(_js_expressions('<form onsubmit="return confirm(\'{{ _("a") }}\')">'
                          '<script>x = "{{ _("b") }}";</script>')) == 2,
      "and the search finds a translation in an attribute and in a script")

print()
print("--- an attack on all texts that go into a page without escaping ---")
# The previous section watches one place we already know about. This one asks the
# other way round: when runnable code is put into **every** text that goes into a
# chart, does code come out of it anywhere?
#
# The texts come from three sides and none of them is ours: translations are written by people through
# Weblate, names and titles are sent by Jellyfin, the release notes come
# from GitHub. All of them end in a chart that goes into the template through
# `| safe` - that is without the safety net Jinja otherwise holds.
from html.parser import HTMLParser  # noqa: E402


class _Parser(HTMLParser):
    """A part-time browser: what is a tag and what are its attributes.

    Searching for an attack in HTML as a string is not enough. `" onmouseover="alert(1)`
    stays visible in the text after escaping (`&quot; onmouseover=&quot;…`),
    but there it is a harmless sentence, not an event handler. The difference is told only by
    whoever takes the HTML apart into tags - so we do that too.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "iframe", "object", "embed"):
            self.found.append(f"<{tag}>")
        for name, value in attrs:
            name = (name or "").lower()
            value = (value or "").strip().lower()
            if name.startswith("on"):      # an event handler = code
                self.found.append(f"<{tag} {name}=>")
            if name in ("href", "src", "xlink:href", "action", "formaction"):
                clean = value.replace("\t", "").replace("\n", "")
                if clean.startswith(("javascript:", "data:text/html",
                                     "vbscript:")):
                    self.found.append(f"<{tag} {name}={clean[:24]}>")

    handle_startendtag = handle_starttag


def _runnable(html: str) -> list[str]:
    """The places where a text became code. An empty list = clean."""
    parser = _Parser()
    parser.feed(f"<div>{html}</div>")
    parser.close()
    return parser.found


# Four shapes of an attack. Each defeats another defence: a tag passes where there is
# no escaping; a quote escapes from an attribute; `javascript:` waits for a link;
# and closing tags break SVG, where other rules hold than in HTML.
_ATTACKS = {
    "a tag": "<script>alert(1)</script>",
    "escaping from an attribute": '" onmouseover="alert(1)',
    "an address with a script": "javascript:alert(1)",
    "breaking SVG": "</text></svg><script>alert(1)</script>",
}

# Translations really used in the charts.
_TRANSLATIONS = [charts.DAY_NAMES[0], "No data yet",
                 "Drag across the chart to pick a range.", "days", "Genre share",
                 "Where they watch from"]


def _charts(attack: str) -> dict[str, str]:
    """Every chart with the attack in all the texts it takes.

    Not only in the label - in the id too (it goes into a link), in a country's code, in a
    place's name and **in the colour**. The colour ends in the `style` attribute, so a quote
    in it is the same hole as a tag in a label.
    """
    rows = [{"label": attack, "value": 5, "item_count": 5, "hours": 5,
             "id": attack, "user_id": attack, "percent": 50, "gb": 5,
             "code": attack, "slot": 1, "color": attack},
            {"label": "druhý", "value": 3, "item_count": 3, "hours": 3,
             "id": "x", "user_id": "x", "percent": 50, "gb": 3,
             "code": "cs", "slot": 2, "color": "var(--accent)"}]
    points = [{"lat": 50.0, "lon": 14.4, "seconds": 60, "plays": 1, "people": 1,
               "misto": attack, "country": attack, "city": attack, "description": attack,
               "viewers": frozenset({"u1"})}]
    return {
        "hbar_chart": charts.hbar_chart(rows, "label", "value"),
        "hbar_chart with a link": charts.hbar_chart(
            rows, "label", "value", link_prefix="/users/",
            link_key="user_id"),
        "legend": charts.legend(rows),
        "stacked_bar": charts.stacked_bar(rows),
        "donut_chart": charts.donut_chart(rows),
        "heatmap": charts.heatmap([[1] * 24 for _ in range(7)]),
        "sparkline": charts.sparkline([{"day": attack, "hours": 1},
                                       {"day": "x", "hours": 2}]),
        "area_chart_multi": charts.area_chart_multi(
            [{"day": attack, "gb": 1}, {"day": "x", "gb": 2}], "day",
            [{"key": "gb", "label": attack, "color": attack}], selectable=True),
        "world_map": charts.world_map(points),
        "release notes": updates.notes_html(attack),
    }


def _try_attacks() -> list[str]:
    """Renders everything with everything and returns the list of places where code came out."""
    found: list[str] = []
    backup = {k: i18n.TRANSLATIONS["cs"].get(k) for k in _TRANSLATIONS}
    language = db.get_setting("ui_language", "cs")
    try:
        for label, attack in _ATTACKS.items():
            for key in _TRANSLATIONS:
                i18n.TRANSLATIONS["cs"][key] = attack
            db.set_setting("ui_language", "cs")
            db.forget_settings()
            for name, html in _charts(attack).items():
                for place in _runnable(html):
                    found.append(f"{name} / {label}: {place}")
    finally:
        for key, value in backup.items():
            if value is None:
                i18n.TRANSLATIONS["cs"].pop(key, None)
            else:
                i18n.TRANSLATIONS["cs"][key] = value
        db.set_setting("ui_language", language)
        db.forget_settings()
    return found


found = _try_attacks()
for item in found:
    print("       ", item)
check(not found, f"out of {len(_ATTACKS)} attacks × 10 charts no code gets out")

# And can that test fail at all? Zero findings means either a clean app or
# a blind test - the only way to tell them apart is to switch the defence off for a while.
# If the test does not squeal even at a hole we made in it ourselves,
# its silence means nothing.
_e_backup, _colour_backup = charts._e, charts._colour
try:
    charts._e = lambda value: str(value or "")
    charts._colour = lambda value: str(value or "")
    blind = _try_attacks()
finally:
    charts._e, charts._colour = _e_backup, _colour_backup
check(len(blind) > 10, "and without escaping code gets out (the test can fail)")

print()
print("--- the markdown of release notes (a foreign text from GitHub) ---")
notes_attacks = {
    # Without brackets in the address: a ( or ) is no link at all, so it would not test the scheme check.
    "javascript:": "[x](javascript:alert%281%29)", "JaVaScRiPt:": "[x](JaVaScRiPt:void0)",
    "data:": "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "an image with javascript:": "![x](javascript:void0)",
    "javascript: with brackets": "[x](javascript:alert(1))",
    # No space or bracket either: with them it is no address at all and the quote would not be tested.
    "a quote out of the address": '[x](https://a.cz/"onmouseover="location=name)',
    "a quote out of a bare address": 'https://a.cz/"onmouseover="location=name',
    "a quote out of an address in <>": '<https://a.cz/"onmouseover="location=name>',
    "a tag in the text, the address and the link's text": "<script>a</script> [<script>b</script>](https://a.cz/<script>c</script>)",
    "a link in a link": "[a [b](https://x.cz)](https://y.cz)",
}
notes_found = {name: updates.notes_html(text) for name, text in notes_attacks.items()}
check(not any(_runnable(html) for html in notes_found.values()),
      f"no attack through the markdown gets code into the page ({[n for n, h in notes_found.items() if _runnable(h)]})")
check(all("href" not in notes_found[n] for n in ("javascript:", "JaVaScRiPt:", "data:", "an image with javascript:",
                                                 "javascript: with brackets")),
      "an address without http(s) is no link")
link_attributes = [a for html in notes_found.values() for a in re.findall(r"<a ([^>]*)>", html)]
check(link_attributes and all(re.fullmatch(r'href="[^"<>]*" target="_blank" rel="noopener noreferrer"', a)
                              for a in link_attributes),
      f"a link has only href, target and rel - a quote does not leave href ({link_attributes})")
check(not re.search(r"<a [^>]*>(?:(?!</a>).)*<a ", notes_found["a link in a link"], re.S),
      f"no link inside a link ({notes_found['a link in a link']})")
check("<script>" not in "".join(notes_found.values()), "a tag stays text everywhere")

print()
print("--- the demo cannot lead off to a foreign server ---")
# The demo mode sends a person back where they came from - by the `referer`
# header. That comes from the visitor, so it cannot be trusted: when the whole
# address is taken from it, the app is an open redirector. A link looks as if
# it leads to jellyscope.cz, and ends elsewhere - exactly how passwords
# are collected.
#
# There used to be a check here, but it was done by a prefix ("starts with our
# address"), and `jellyscope.cz.utocnik.cz` passes that too.
from jellyscope.web import _referer_path  # noqa: E402

FOREIGN = [
    "http://jellyscope.cz.utocnik.cz/past",   # our name as a prefix
    "https://utocnik.cz/",
    "//utocnik.cz/past",                      # the browser reads it as an address
    "https://utocnik.cz/?x=http://jellyscope.cz",
    "javascript:alert(1)",
    "http://utocnik.cz",
]
for address in FOREIGN:
    target = _referer_path(address)
    check(target.startswith("/") and not target.startswith("//"),
          f"{address[:36]!r} -> {target!r} stays with us")
    check("utocnik.cz" not in target.split("?")[0],
          f"{address[:36]!r} does not lead elsewhere")

# And it still sends a person back where they were - otherwise the safeguard would be useless.
for address, expected in (("http://jellyscope.cz/settings?section=api",
                           "/settings?section=api"),
                          ("http://jellyscope.cz/library", "/library"),
                          ("http://jellyscope.cz/", "/"),
                          (None, "/"),
                          ("", "/")):
    check(_referer_path(address) == expected,
          f"{str(address)[:38]!r} -> {expected!r}")


print()
print("--- neither a password nor somebody else's address gets into the log ---")
# The log is passed on when something goes wrong - and so it gets to people it was
# not written for. A setting's value is therefore written into it only where it is
# without risk, and the list is **reversed**: it allows, it does not forbid.
# A denylist would have to know beforehand every secret that will ever come, and the one
# forgotten would be written out whole.
import io  # noqa: E402
import logging  # noqa: E402

SENSITIVE = {
    "jellyfin_api_key": "TAJNY-KLIC-123",
    "notify_smtp_heslo": "secretpass",
    "notify_telegram_token": "1234:ABCDEF",
    "notify_discord_webhook": "https://discord.com/api/webhooks/1/tajne",
    # Not secrets, but somebody else's data: an address and an account's name.
    "notify_smtp_komu": "petr@example.org",
    "notify_smtp_uzivatel": "petr.novak",
    "notify_smtp_odesilatel": "jellyscope@example.org",
    # A setting that does not exist yet - it must keep silent too.
    "notify_pushover_klic": "budouci-tajemstvi",
}

caught = io.StringIO()
listener = logging.StreamHandler(caught)
listener.setFormatter(logging.Formatter("%(message)s"))
logger = logging.getLogger("jellyscope.db")
logger.addHandler(listener)
original_level = logger.level
logger.setLevel(logging.INFO)
try:
    for key, value in SENSITIVE.items():
        db.set_setting(key, value)
    # And something harmless, so it is seen the log did not go mute entirely.
    db.set_setting("poll_interval", "42")
finally:
    logger.removeHandler(listener)
    logger.setLevel(original_level)

written = caught.getvalue()
for key, value in SENSITIVE.items():
    check(value not in written, f"{key}: the value is not in the log")
    check(key in written, f"{key}: but the change is recorded")
check("poll_interval: 10 -> 42" in written,
      "a harmless setting is written out with its value")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
