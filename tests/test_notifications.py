# -*- coding: utf-8 -*-
r"""Notifications: channels, events, and keeping the watchman from becoming an alarm clock.

Jellyscope is passive. The collector asks Jellyfin every few seconds, and when
its token expires, the app keeps running and the history stops **quietly** –
you find out three weeks later by a hole in the chart. That is why this thing came about.

Four places where it can be done wrong, and that is why they are measured:

* **A message only on a change.** Were it to come at every run of the task, the
  watchman would be an alarm clock after fifteen minutes and would be switched off the very first day.
  And when it is fixed, a message about that must come too – without it a person does not know
  whether there is still something to deal with.
* **A failure of one channel must not bring the others down.** When SMTP does not work,
  the message is still to reach Discord.
* **Secrets must not get into the page.** The password, the webhook and the token are
  access details like the key to Jellyfin.
* **An empty password field means "leave it as it was".** The stored value is not written
  into the form, so otherwise every save would delete it.

Run:
    .venv/bin/python tests/test_notifications.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "notifikace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import applog, collector, db, notifications  # noqa: E402

# The loggers' thresholds are set by applog - without it the check below would measure
# Python's default state, not what holds in the app.
applog.setup()

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


db.init_db()

# The sent messages are collected here instead of the network.
sent: list[tuple[str, str, str]] = []
failing: set[str] = set()


def _fake(channel: str):
    async def deliver(subject: str, text: str) -> None:
        if channel in failing:
            raise RuntimeError(f"{channel} nefunguje")
        sent.append((channel, subject, text))
    return deliver


notifications.SENDERS = {k: _fake(k) for k in notifications.CHANNELS}


def set_up_channel(channel: str) -> None:
    db.set_setting(notifications.setting_key(channel, "enabled"), "1")
    if channel == "smtp":
        db.set_setting(notifications.setting_key("smtp", "host"), "smtp.doma.cz")
        db.set_setting(notifications.setting_key("smtp", "recipient"), "ja@doma.cz")
    elif channel == "discord":
        db.set_setting(notifications.setting_key("discord", "webhook"),
                       "https://discord.com/api/webhooks/1/tajne")
    elif channel == "telegram":
        db.set_setting(notifications.setting_key("telegram", "token"), "1:tajne")
        db.set_setting(notifications.setting_key("telegram", "chat"), "42")
    db.forget_settings()


print("--- a channel is recognised by what is filled in ---")
check(not notifications.enabled_channels(), "without settings there is no way to send")
db.set_setting(notifications.setting_key("discord", "enabled"), "1")
db.forget_settings()
check(not notifications.enabled_channels(),
      "a switched-on but unfilled channel does not count")
set_up_channel("discord")
check(notifications.enabled_channels() == ["discord"], "a filled-in one does")

print()
print("--- a failure of one channel does not bring the others down ---")
set_up_channel("smtp")
failing.add("smtp")
sent.clear()
results = asyncio.run(notifications.send("předmět", "text"))
check(len(results) == 2, f"both channels were tried ({len(results)})")
check([v["channel"] for v in results if v["ok"]] == ["discord"],
      "Discord passed, even though SMTP failed")
check(any(v["error"] for v in results if not v["ok"]),
      "and the failed one has the reason too")
check(db.get_setting("notify_last_error", "").startswith("smtp:"),
      "the reason is stored, so the settings show it")
failing.clear()

print()
print("--- the collector: only a change is reported ---")
db.set_setting(notifications.event_key("collector"), "1")
db.forget_settings()

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def last_poll(minutes_ago: float) -> None:
    db.set_setting(collector.STATUS_KEY, "ok")
    db.set_setting(collector.ERROR_KEY, "")
    db.set_setting(collector.LAST_POLL_KEY,
                   (NOW - timedelta(minutes=minutes_ago)).strftime(notifications.TIME_FORMAT))
    db.forget_settings()


last_poll(1)
sent.clear()
asyncio.run(notifications.check())
check(not sent, "while the collector collects, nothing is sent")

last_poll(120)                 # two hours of silence
sent.clear()
asyncio.run(notifications.check())
check(len(sent) == 2, f"after a long silence a message comes ({len(sent)}x)")
check("120" in sent[0][2] or "119" in sent[0][2],
      f"and it says how long it has been silent ({sent[0][2]!r})")

# This is the main trap: the second run must NOT send any more.
sent.clear()
asyncio.run(notifications.check())
check(not sent, "the same is not sent a second time - otherwise the watchman would be an alarm clock")

last_poll(1)
sent.clear()
asyncio.run(notifications.check())
check(len(sent) == 2, "and when it is fixed, a message about it comes")
check("zase sbírá" in sent[0][1] or "collecting again" in sent[0][1],
      f"which says so in the subject ({sent[0][1]!r})")
# This was the bug: the text of the message about the recovery said "sberac nesbira"
# and a person read it as another alarm.
check("nesbírá" not in sent[0][2] and "not collecting" not in sent[0][2],
      f"and the text does not claim it is still broken ({sent[0][2]!r})")

sent.clear()
asyncio.run(notifications.check())
check(not sent, "and then it is quiet again")

print()
print("--- own wording of the messages ---")
db.set_setting(notifications.text_key("collector_down", "subject"), "POZOR na serveru")
db.set_setting(notifications.text_key("collector_down", "body"),
               "Koukni na Jellyfin: {detail} {unknown_kind}")
db.set_setting(notifications.text_key("collector_back", "body"), "")   # empty = the default
db.forget_settings()
last_poll(120)
sent.clear()
asyncio.run(notifications.check())
check(sent and sent[0][1] == "POZOR na serveru",
      f"an own subject ({sent[0][1]!r})")
check(sent and sent[0][2].startswith("Koukni na Jellyfin: ")
      and ("120" in sent[0][2] or "119" in sent[0][2]),
      f"an own text with {{detail}} filled in ({sent[0][2]!r})")
check(sent and "{unknown_kind}" in sent[0][2],
      "an unknown curly bracket stays as it is - it does not bring the sending down")
last_poll(1)
sent.clear()
asyncio.run(notifications.check())
check(sent and sent[0][2] == notifications.message_text("collector_back")[1]
      and sent[0][2], f"an empty field = the default text ({sent[0][2]!r})")
# The collector message's variables: how long it is silent, the silence's threshold and the error's text.
db.set_setting(notifications._state_key("collector"), "ok")
db.set_setting("notify_quiet_minutes", "30")
db.set_setting(notifications.text_key("collector_down", "body"),
               "Ticho {minutes} min, limit {threshold} min, chyba {error}")
db.forget_settings()
last_poll(120)
sent.clear()
asyncio.run(notifications.check())
check(sent and sent[0][2] in ("Ticho 120 min, limit 30 min, chyba –", "Ticho 119 min, limit 30 min, chyba –"),
      f"the variables {{minutes}} {{threshold}} {{error}} in the collector's message ({sent[0][2] if sent else None!r})")
last_poll(1)
asyncio.run(notifications.check())
db.set_setting(notifications.text_key("collector_down", "body"), "")
db.set_setting(notifications.text_key("collector_down", "subject"),
               "Porucha: {detail}")
db.forget_settings()
subject, _ = notifications.message_text("collector_down",
                                        detail="Jellyfin odmítl klíč\r\nBcc: cizi@example.com")
check("\n" not in subject and "\r" not in subject,
      f"the subject is always one line - otherwise the e-mail would not send the alarm ({subject!r})")
db.set_setting(notifications.text_key("collector_down", "subject"), "")
db.set_setting(notifications.text_key("collector_down", "body"), "")
db.forget_settings()

print()
print("--- a switched-off event is not dealt with ---")
db.set_setting(notifications.event_key("collector"), "0")
db.forget_settings()
last_poll(500)
sent.clear()
asyncio.run(notifications.check())
check(not sent, "a switched-off notification is silent, even when things are bad")
db.set_setting(notifications.event_key("collector"), "1")
db.set_setting(notifications._state_key("collector"), "ok")
db.forget_settings()

print()
print("--- the demo mode is not a fault ---")
db.set_setting(collector.STATUS_KEY, "demo")
db.forget_settings()
stalled, _reason = notifications._collector_stalled()
check(not stalled, "in the demo mode nothing is collected on purpose")
last_poll(1)

print()
print("--- a collector error: it speaks up only after the set silence ---")
# The user, 12. 11.: "notifikace přijde ihned, ne po časovém rozsahu" -
# a collector error (Jellyfin restarting, a network outage) was reported at once.


def collector_error(text: str) -> None:
    db.set_setting(collector.STATUS_KEY, "error")
    db.set_setting(collector.ERROR_KEY, text)
    db.forget_settings()


db.set_setting("notify_quiet_minutes", "30")
last_poll(1)
collector_error("Jellyfin neodpovídá (timeout)")
stalled, _reason = notifications._collector_stalled()
check(not stalled, "an error a minute after the last successful query is not a fault yet")
last_poll(40)
collector_error("Jellyfin neodpovídá (timeout)")
stalled, reason = notifications._collector_stalled()
check(stalled and "timeout" in reason, f"after the silence it is - and the reason is the error's text ({reason!r})")
db.set_setting(collector.LAST_POLL_KEY, "")
collector_error("Jellyfin odmítl klíč")
stalled, _reason = notifications._collector_stalled()
check(not stalled, "without a single successful query the silence counts from the first error - that is now")
db.set_setting(notifications.ERROR_SINCE_KEY, (NOW - timedelta(minutes=40)).strftime(notifications.TIME_FORMAT))
db.forget_settings()
stalled, _reason = notifications._collector_stalled()
check(stalled, "an error lasting over the silence is a fault even without a successful query")
last_poll(1)
notifications._collector_stalled()
check(db.get_setting(notifications.ERROR_SINCE_KEY, "") == "", "when the collector collects again, the error's start is forgotten")

print()
print("--- the weekly summary: once a week, not all the time ---")
db.set_setting(notifications.event_key("summary"), "1")
db.set_setting("notify_summary_day", "0")        # Monday
db.set_setting("notify_summary_time", "09:00")
db.set_setting("notify_summary_sent", "")
db.forget_settings()

monday = datetime(2026, 9, 7, 10, 0)           # Monday, after nine
check(monday.weekday() == 0, "the check date is a Monday")
sent.clear()
asyncio.run(notifications.check(now=monday))
check(any("summary" in p.lower() or "summary" in p.lower() for _k, p, _t in sent),
      f"on Monday morning the summary comes ({[p for _k, p, _t in sent]})")

sent.clear()
asyncio.run(notifications.check(now=monday.replace(hour=18)))
check(not sent, "not a second time the same day")

sent.clear()
asyncio.run(notifications.check(now=datetime(2026, 9, 8, 10, 0)))
check(not sent, "and not on Tuesday either")

# Before a fresh Monday it is sent again.
sent.clear()
asyncio.run(notifications.check(now=datetime(2026, 9, 14, 10, 0)))
check(bool(sent), "the next Monday it is")
db.set_setting(notifications.event_key("summary"), "0")
db.forget_settings()

print()
print("--- secrets do not leak in an error's text ---")
# A real finding of the security check: an error from the HTTP client carries
# the WHOLE address in it. With Telegram the bot's token is in the address, with Discord
# the webhook's address is itself the secret - and that message is STORED and SHOWN
# in the settings, so the token shone on the screen.
TOKEN = "SUPER-TAJNY-TOKEN-12345"
db.set_setting(notifications.setting_key("telegram", "token"), f"999:{TOKEN}")
db.set_setting(notifications.setting_key("discord", "webhook"),
               f"https://discord.com/api/webhooks/1/{TOKEN}")
db.forget_settings()

message = (f"Client error '401 Unauthorized' for url "
           f"'https://api.telegram.org/bot999:{TOKEN}/sendMessage'")
check(TOKEN not in notifications.without_secrets(message),
      f"the token from the address is masked ({notifications.without_secrets(message)[-46:]})")
check("401 Unauthorized" in notifications.without_secrets(message),
      "but the rest of the message stays - otherwise it would be useless")
check(TOKEN not in notifications.without_secrets(
          f"connect to https://discord.com/api/webhooks/1/{TOKEN} failed"),
      "and the Discord webhook too")

# And the same at a real failure, not only in the helper function.
failing.add("telegram")
db.set_setting(notifications.setting_key("telegram", "enabled"), "1")
db.set_setting(notifications.setting_key("telegram", "chat"), "42")
db.forget_settings()


async def _fails_with_address(subject: str, text: str) -> None:
    raise RuntimeError(message)


original = notifications.SENDERS["telegram"]
notifications.SENDERS["telegram"] = _fails_with_address
asyncio.run(notifications.send("p", "t", only_channel="telegram"))
notifications.SENDERS["telegram"] = original
failing.clear()
check(TOKEN not in db.get_setting("notify_last_error", ""),
      f"the stored message does not contain the token ({db.get_setting('notify_last_error', '')[-40:]})")

# The HTTP client logs every request including the address. In ordinary use it is
# thrown away, but it is enough to start the server with --log-level debug.
import logging  # noqa: E402

for name in ("httpx", "httpcore"):
    check(logging.getLogger(name).level >= logging.WARNING,
          f"the logger {name} has a threshold of at least WARNING"
          f" ({logging.getLevelName(logging.getLogger(name).level)})")

print()
print("--- mentions in Discord are not evaluated ---")
# The names of titles from the library go into the message. A movie named "@everyone"
# is a file's name, not an order to ping the whole channel.
source = (PROJECT / "jellyscope" / "notifications.py").read_text(encoding="utf-8")
check('"allowed_mentions": {"parse": []}' in source,
      "the message to Discord switches mentions off")

print()
print("--- secrets do not get into the page ---")
public = db.get_public_settings()
secret_keys = [k for k in notifications.SECRET_KEYS if k in public]
check(not secret_keys, f"neither the password, the webhook nor the token is in the public settings: {secret_keys}")
check(db.get_setting(notifications.setting_key("discord", "webhook"), "") != "",
      "but they are stored")

print()
print("--- the page and saving ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    response = client.get("/settings?section=notifications")
    check(response.status_code == 200, "the Notifications section loads")
    html = response.text
    check("tajne" not in html,
          "and the stored webhook is not written into it even by mistake")
    for field in ("smtp_host", "discord_webhook", "telegram_token",
                  "event_collector", "event_free_space", "event_summary"):
        check(f'name="{field}"' in html, f"the form has the field {field}")

    # An empty password must not delete the stored one.
    db.set_setting(notifications.setting_key("smtp", "password"), "puvodni")
    db.forget_settings()
    client.post("/settings/notifications", follow_redirects=False, data={
        "smtp_enabled": "1", "smtp_host": "jiny.server", "smtp_port": "465",
        "smtp_to": "ja@doma.cz", "smtp_password": ""})
    db.forget_settings()
    check(db.get_setting(notifications.setting_key("smtp", "password"), "") == "puvodni",
          "an empty password field leaves it be")
    check(db.get_setting(notifications.setting_key("smtp", "host"), "") == "jiny.server",
          "and the other fields are saved")

    client.post("/settings/notifications", follow_redirects=False, data={
        "smtp_enabled": "1", "smtp_host": "jiny.server", "smtp_to": "ja@doma.cz",
        "smtp_password": "nove"})
    db.forget_settings()
    check(db.get_setting(notifications.setting_key("smtp", "password"), "") == "nove",
          "a filled-in field overwrites it")

    # A nonsense channel does not get into sending.
    sent.clear()
    client.post("/settings/notifications/test", data={"channel": "../../etc"},
                follow_redirects=False)
    check(not sent, "an unknown channel is not sent")

    sent.clear()
    client.post("/settings/notifications/test", data={"channel": "discord"},
                follow_redirects=False)
    check(len(sent) == 1 and sent[0][0] == "discord",
          f"a test message goes only through that one channel ({sent})")

print()
print("--- the task ---")
from jellyscope import tasks  # noqa: E402

check("notifications" in tasks.TASKS, "the check has a task of its own")
check(not tasks.TASKS["notifications"].is_daily,
      "and it runs at an interval - a fault is to be known today, not tomorrow morning")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
