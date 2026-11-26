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
print("--- the weekly summary covers the week since the last one: 7 x 24 hours ---")
# Not the statistics' "last 7 days", which count whole calendar days since 25. 11. 2026: a summary sent on Monday at
# nine reports the week since the previous Monday at nine, so two summaries in a row leave no hour out.
from jellyscope import formatting  # noqa: E402

real_now = datetime.now(timezone.utc).replace(tzinfo=None)
first_midnight = (datetime.combine(datetime.now(formatting.zone()).date() - timedelta(days=6), datetime.min.time())
                  .replace(tzinfo=formatting.zone()).astimezone(timezone.utc).replace(tzinfo=None))
rolling_start = real_now - timedelta(days=7)
if rolling_start < first_midnight:
    played = (rolling_start + (first_midnight - rolling_start) / 2).strftime(db.TIME_FORMAT)
    with db.connect() as conn:
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
                     " started_at, last_seen_at, watched_seconds, is_active)"
                     " VALUES ('week-gap', 'u1', 'Petr', 'film-1', 'Duna', 'Movie', ?, ?, 18000, 0)",
                     (played, played))
    summary = notifications._weekly_summary()
    check(formatting.hours_human(5) in summary,
          "a play six and a half days ago, on the day before the statistics' seven, is in the summary",
          f"({played}: {summary.splitlines()[0]})")
    with db.connect() as conn:
        conn.execute("DELETE FROM playback WHERE session_key = 'week-gap'")
else:
    check(True, "the week's gap before the seven whole days is empty right now (just before midnight)")

print()
print("--- a message that could not be sent is tried again ---")
# The same outage that stops the collector often stops the sending too. The new state used to be stored before
# the sending, whose result was not looked at: the alarm was lost for good and the task said "Sent".
db.set_setting(notifications._state_key("collector"), "ok")
db.forget_settings()
failing.update(notifications.CHANNELS)
last_poll(120)
sent.clear()
outcome = asyncio.run(notifications.check())
check(not sent and db.get_setting(notifications._state_key("collector"), "ok") == "ok",
      "every channel failed: the new state is not stored")
check(outcome.get("status") == "error" and "failed" in outcome.get("message", "")
      and not outcome.get("message", "").startswith("Sent"),
      f"and the task does not claim it was sent ({outcome})")
failing.clear()
outcome = asyncio.run(notifications.check())
check(len(sent) == 2 and outcome.get("status") == "ok", f"the next run delivers the alarm ({len(sent)}x, {outcome})")
sent.clear()
asyncio.run(notifications.check())
check(not sent, "and then it is quiet")
last_poll(1)
asyncio.run(notifications.check())
sent.clear()

db.set_setting(notifications.event_key("summary"), "1")
db.set_setting("notify_summary_sent", "")
db.forget_settings()
failing.update(notifications.CHANNELS)
asyncio.run(notifications.check(now=monday))
check(not sent and db.get_setting("notify_summary_sent", "") == "",
      "the weekly summary that could not be sent is not marked as sent")
failing.clear()
asyncio.run(notifications.check(now=monday.replace(hour=11)))
check(any("summary" in p.lower() for _k, p, _t in sent), "so a later run that day sends it")
db.set_setting(notifications.event_key("summary"), "0")
db.forget_settings()
sent.clear()

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

# The masking replaces the stored value - but httpx writes the address into an error's text as it normalised it:
# the host in lower case, without :443, an invisible character copied along percent-encoded. Masking by the stored
# value then misses and the whole webhook went into the message, the settings and the log. A real failing
# answer (404) through the real senders, only the network is substituted.
import httpx  # noqa: E402

_real_client = httpx.AsyncClient


class _AnswersNotFound(_real_client):
    def __init__(self, *arguments, **named) -> None:
        named["transport"] = httpx.MockTransport(lambda request: httpx.Response(404, text="Unknown Webhook"))
        super().__init__(*arguments, **named)


HIDDEN = TOKEN[5:]
cases = [("discord", "webhook", f"https://Discord.com/api/webhooks/1/{TOKEN}", "a capital letter in the host"),
         ("discord", "webhook", f"https://discord.com:443/api/webhooks/1/{TOKEN}", "the port :443 written out"),
         ("discord", "webhook", f"https://discord.com/api/webhooks/1/{TOKEN}\u200b", "an invisible character at the end"),
         ("telegram", "token", f"999:{TOKEN[:5]}\u200b{TOKEN[5:]}", "Telegram: an invisible character inside")]
real_senders = {"discord": notifications._send_discord, "telegram": notifications._send_telegram}
httpx.AsyncClient = _AnswersNotFound
try:
    for channel, field, value, label in cases:
        db.set_setting(notifications.setting_key(channel, field), value)
        db.forget_settings()
        notifications.SENDERS[channel] = real_senders[channel]
        outcome = asyncio.run(notifications.send("p", "t", only_channel=channel))
        notifications.SENDERS[channel] = _fake(channel)
        error = outcome[0]["error"] if outcome else ""
        stored = db.get_setting("notify_last_error", "")
        logged = applog.log_path().read_text(encoding="utf-8") if applog.log_path().exists() else ""
        check("404" in error and HIDDEN not in error, f"{label}: the error says 404 without the secret ({error[:90]!r})")
        check(HIDDEN not in stored and HIDDEN not in logged, f"{label}: nor in the stored error and the log")
finally:
    httpx.AsyncClient = _real_client

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
print("--- e-mail checks the mail server's certificate ---")
# smtplib without a context of its own takes ssl._create_stdlib_context(): no certificate check, no host name
# check. Whoever sat on the way offered their own certificate and got the AUTH with the mailbox's password.
# The network is substituted: what is measured is the context the connection gets.
import smtplib  # noqa: E402
import ssl  # noqa: E402

from jellyscope import i18n  # noqa: E402

connections: list[tuple[int, str, object]] = []
self_signed = False             # the fake server answers with a certificate no verifying client accepts


def _refuse_if_verified(context: object) -> None:
    if self_signed and isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED:
        error = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        error.verify_message = "self-signed certificate"
        raise error


class _FakeSMTP:
    def __init__(self, host: str = "", port: int = 0, *arguments: object, context: object = None,
                 **named: object) -> None:
        self.port = port
        if port == 465:
            connections.append((port, "connect", context))
            _refuse_if_verified(context)

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self, context: object = None, **named: object) -> None:
        connections.append((self.port, "starttls", context))
        _refuse_if_verified(context)

    def login(self, user: str, password: str) -> None:
        pass

    def send_message(self, email: object) -> None:
        pass


def verifies(context: object) -> bool:
    return (isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
            and context.check_hostname)


real_smtp, real_smtp_ssl = smtplib.SMTP, smtplib.SMTP_SSL
smtplib.SMTP, smtplib.SMTP_SSL = _FakeSMTP, _FakeSMTP
try:
    for key, value in (("host", "smtp.doma.cz"), ("recipient", "ja@doma.cz"), ("user", "jana"),
                       ("password", "heslo-k-posté"), ("tls", "1"), ("verify", "")):
        db.set_setting(notifications.setting_key("smtp", key), value)
    for port in ("465", "587"):
        db.set_setting(notifications.setting_key("smtp", "port"), port)
        db.forget_settings()
        connections.clear()
        notifications._send_smtp_blocking("p", "t")
        check(len(connections) == 1 and verifies(connections[0][2]),
              f"port {port}: the connection checks the certificate and the host name ({connections})")
    db.set_setting(notifications.setting_key("smtp", "verify"), "0")
    for port in ("465", "587"):
        db.set_setting(notifications.setting_key("smtp", "port"), port)
        db.forget_settings()
        connections.clear()
        notifications._send_smtp_blocking("p", "t")
        check(len(connections) == 1 and isinstance(connections[0][2], ssl.SSLContext)
              and connections[0][2].verify_mode == ssl.CERT_NONE,
              f"port {port}: with the check switched off a self-signed certificate is let through ({connections})")
    # A failed check says what to do about it: the checkbox by the name the settings show.
    db.set_setting(notifications.setting_key("smtp", "verify"), "1")
    self_signed = True
    label = i18n.translate("verify the server's certificate", i18n.server_language())
    notifications.SENDERS["smtp"] = notifications._send_smtp
    for port in ("465", "587"):
        db.set_setting(notifications.setting_key("smtp", "port"), port)
        db.forget_settings()
        outcome = asyncio.run(notifications.send("p", "t", only_channel="smtp"))
        error = outcome[0]["error"] if outcome else ""
        check(bool(outcome) and not outcome[0]["ok"] and label in error and "self-signed certificate" in error,
              f"port {port}: a refused certificate names the checkbox ({error!r})")
    self_signed = False
finally:
    smtplib.SMTP, smtplib.SMTP_SSL = real_smtp, real_smtp_ssl
    notifications.SENDERS["smtp"] = _fake("smtp")

print()
print("--- the real handshake: a self-signed certificate is refused, unless the check is off ---")
# The checks above replace smtplib whole, so they see the context handed over, not what the handshake does with
# it. Here a mail server on this machine (an ephemeral port) offers STARTTLS with a self-signed certificate.
import shutil  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402


def self_signed_certificate(folder: Path) -> tuple[Path, Path] | None:
    tool = shutil.which("openssl")
    if not tool:
        return None
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run([tool, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, capture_output=True, timeout=60)
    return cert, key


def starttls_server(cert: Path, key: Path) -> tuple[int, list[str], threading.Thread]:
    """One connection: the greeting, EHLO with STARTTLS, the handshake - and what came of it in `seen`."""
    listener = socket.create_server(("127.0.0.1", 0))
    seen: list[str] = []

    def serve() -> None:
        connection, _ = listener.accept()
        listener.close()
        connection.settimeout(10)
        try:
            connection.sendall(b"220 localhost ESMTP\r\n")
            connection.recv(1024)                                   # EHLO
            connection.sendall(b"250-localhost\r\n250 STARTTLS\r\n")
            connection.recv(1024)                                   # STARTTLS
            connection.sendall(b"220 ready\r\n")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            try:
                encrypted = context.wrap_socket(connection, server_side=True)
            except (ssl.SSLError, OSError) as error:
                seen.append(f"refused: {error}")
                return
            seen.append("handshake")
            encrypted.recv(1024)                                    # EHLO again, over TLS
            encrypted.sendall(b"250 localhost\r\n")                 # no AUTH: the login after it fails
            encrypted.close()
        finally:
            connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener.getsockname()[1], seen, thread


certificate = self_signed_certificate(Path(_tmp))
if certificate is None:
    print("SKIP   no openssl to make a certificate with")
else:
    for key, value in (("host", "localhost"), ("tls", "1"), ("user", "jana")):
        db.set_setting(notifications.setting_key("smtp", key), value)
    for verify, label in (("1", "the check on: the handshake is refused"), ("0", "the check off: it goes through")):
        port, seen, thread = starttls_server(*certificate)
        db.set_setting(notifications.setting_key("smtp", "port"), str(port))
        db.set_setting(notifications.setting_key("smtp", "verify"), verify)
        db.forget_settings()
        try:
            notifications._send_smtp_blocking("p", "t")
            outcome = "sent"
        except i18n.TranslatableError as error:
            outcome = f"certificate: {error.translated('en')}"
        except (smtplib.SMTPException, OSError) as error:
            outcome = f"after the handshake: {type(error).__name__}"
        thread.join(15)
        if verify == "1":
            check(outcome.startswith("certificate:") and seen and seen[0].startswith("refused"),
                  f"{label} and the error names the checkbox ({outcome[:90]}, {seen})")
        else:
            check(seen == ["handshake"] and outcome.startswith("after the handshake"),
                  f"{label} ({outcome}, {seen})")
    db.set_setting(notifications.setting_key("smtp", "host"), "smtp.doma.cz")
    db.set_setting(notifications.setting_key("smtp", "port"), "587")
    db.set_setting(notifications.setting_key("smtp", "verify"), "1")
    db.forget_settings()

print()
print("--- an SMTP set up before the check keeps sending, once ---")
# Upgrading must not silence the alarms of a home mail server with its own certificate: an installation that
# has SMTP set up keeps "do not verify", once; a new one (or SMTP filled in later) verifies.
convert = getattr(notifications, "convert_smtp_verification", None)
check(convert is not None, "the conversion exists")
if convert is not None:
    verify_key = notifications.setting_key("smtp", "verify")
    db.set_setting(notifications.SMTP_VERIFY_CONVERSION_KEY, "")
    db.set_setting(verify_key, "")
    db.forget_settings()
    check(convert() is True and db.get_setting(verify_key, "") == "0",
          f"SMTP set up before: the certificate stays unverified ({db.get_setting(verify_key, '')!r})")
    db.set_setting(verify_key, "1")
    check(convert() is False and db.get_setting(verify_key, "") == "1",
          "the second start leaves the choice alone")
    host = db.get_setting(notifications.setting_key("smtp", "host"), "")
    db.set_setting(notifications.SMTP_VERIFY_CONVERSION_KEY, "")
    db.set_setting(verify_key, "")
    db.set_setting(notifications.setting_key("smtp", "host"), "")
    db.forget_settings()
    convert()
    db.set_setting(notifications.setting_key("smtp", "host"), host)
    db.forget_settings()
    check(db.get_setting(verify_key, "") == "" and convert() is False,
          "without SMTP nothing is stored: SMTP filled in later verifies")
    # For the page below: the start (lifespan) runs the conversion over this SMTP again.
    db.set_setting(notifications.SMTP_VERIFY_CONVERSION_KEY, "")
    db.set_setting(verify_key, "")
    db.forget_settings()

print()
print("--- the page and saving ---")
from fastapi.testclient import TestClient  # noqa: E402
from markupsafe import escape  # noqa: E402

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

    # The start converted the SMTP set up above: it keeps sending, and the page says the certificate is unchecked.
    db.forget_settings()
    hint = str(escape(i18n.translate("The server's certificate is not checked: anyone on the way to the mail "
                                     "server could read the password and the messages.", i18n.server_language())))
    check(db.get_setting(notifications.setting_key("smtp", "verify"), "") == "0",
          "the start keeps an SMTP set up before the check unverified")
    flat = " ".join(html.split())
    check('name="smtp_verify"' in flat and 'name="smtp_verify" value="1" checked' not in flat,
          "the checkbox is there, unticked")
    check(hint in html, "and the page says the certificate is not checked")
    client.post("/settings/notifications", follow_redirects=False, data={
        "smtp_enabled": "1", "smtp_host": "smtp.doma.cz", "smtp_port": "587", "smtp_to": "ja@doma.cz",
        "smtp_tls": "1", "smtp_verify": "1"})
    db.forget_settings()
    page = client.get("/settings?section=notifications").text
    check(db.get_setting(notifications.setting_key("smtp", "verify"), "") == "1"
          and 'name="smtp_verify" value="1" checked' in " ".join(page.split()) and hint not in page,
          "a ticked checkbox is saved, the hint goes away")

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

    # A stored secret can be forgotten. An empty field keeps it, so a leaked webhook or a password no longer
    # wanted used to stay in the settings and in every backup for good.
    secret_settings = {"smtp_password": notifications.setting_key("smtp", "password"),
                       "discord_webhook": notifications.setting_key("discord", "webhook"),
                       "telegram_token": notifications.setting_key("telegram", "token")}

    def stored_secrets() -> dict[str, str]:
        db.forget_settings()
        return {name: db.get_setting(key, "") for name, key in secret_settings.items()}

    db.set_setting(secret_settings["discord_webhook"], "https://discord.com/api/webhooks/1/tajne")
    db.set_setting(secret_settings["telegram_token"], "1:tajne")
    db.forget_settings()
    flat = " ".join(client.get("/settings?section=notifications").text.split())
    for secret in secret_settings:
        check(f'form="forget-{secret}"' in flat and f'id="forget-{secret}"' in flat
              and 'action="/settings/notifications/forget"' in flat,
              f"a stored {secret} has a Forget button next to its field")
    answer = client.post("/settings/notifications/forget", data={"secret": "smtp_password"})
    check(answer.status_code == 200 and stored_secrets() == {"smtp_password": "", "discord_webhook":
          "https://discord.com/api/webhooks/1/tajne", "telegram_token": "1:tajne"},
          f"Forget removes the stored SMTP password and nothing else ({answer.status_code}, {stored_secrets()})")
    check(str(escape(i18n.translate("Stored e-mail password forgotten.", i18n.server_language()))) in answer.text,
          "and a message says what was forgotten")
    check('form="forget-smtp_password"' not in answer.text and 'form="forget-discord_webhook"' in answer.text,
          "nothing stored, no Forget button")
    for secret in ("discord_webhook", "telegram_token"):
        client.post("/settings/notifications/forget", data={"secret": secret}, follow_redirects=False)
    check(stored_secrets() == {"smtp_password": "", "discord_webhook": "", "telegram_token": ""},
          f"the Discord webhook and the Telegram token are forgotten the same way ({stored_secrets()})")
    db.set_setting(secret_settings["telegram_token"], "1:tajne")
    db.forget_settings()
    host_before = db.get_setting(notifications.setting_key("smtp", "host"), "")
    client.post("/settings/notifications/forget", data={"secret": "smtp_host"}, follow_redirects=False)
    client.post("/settings/notifications/forget", data={"secret": "notify_telegram_token"}, follow_redirects=False)
    check(stored_secrets()["telegram_token"] == "1:tajne" and host_before
          and db.get_setting(notifications.setting_key("smtp", "host"), "") == host_before,
          "a name outside the list forgets nothing")
    # Signed out and signed in without the administrator's rights: only an anonymous request showed that a sign-in
    # is needed, the route with require_login instead of require_admin would have passed.
    accounts.create("reader", "longpassword")
    reader = TestClient(app)
    reader.post("/login", data={"username": "reader", "password": "longpassword"}, follow_redirects=False)
    for who, other in (("signed out", TestClient(app)), ("a signed-in reader", reader)):
        answer = other.post("/settings/notifications/forget", data={"secret": "telegram_token"}, follow_redirects=False)
        check(stored_secrets()["telegram_token"] == "1:tajne" and answer.status_code in (303, 403),
              f"only an admin can forget: {who} forgets nothing ({answer.status_code})")
    check(reader.get("/account", follow_redirects=False).status_code == 200, "the reader really is signed in")
    db.set_setting(secret_settings["smtp_password"], "nove")
    db.set_setting(secret_settings["discord_webhook"], "https://discord.com/api/webhooks/1/tajne")
    db.forget_settings()

    # An emptied hour or minute of the summary keeps the saved time, as the tasks' daily time does - it used
    # to become 09:00 (FastAPI gives an empty field the parameter's default).
    db.set_setting("notify_summary_time", "07:15")
    db.forget_settings()
    client.post("/settings/notifications/events", follow_redirects=False, data={
        "event_summary": "1", "summary_day": "2", "summary_hour": "", "summary_minute": ""})
    db.forget_settings()
    check(db.get_setting("notify_summary_time", "") == "07:15",
          f"an empty summary time keeps the saved one ({db.get_setting('notify_summary_time', '')})")
    client.post("/settings/notifications/events", follow_redirects=False, data={
        "event_summary": "1", "summary_day": "2", "summary_hour": "", "summary_minute": "40"})
    db.forget_settings()
    check(db.get_setting("notify_summary_time", "") == "07:40",
          f"only the filled-in part changes ({db.get_setting('notify_summary_time', '')})")

    # The same for the other numbers of both forms: a cleared or missing field turned the silence and the days
    # into the defaults (30 and 14), moved the summary to Monday and the SMTP port to 587.
    numbers = {"notify_quiet_minutes": "240", "notify_space_days": "60", "notify_summary_day": "4",
               notifications.setting_key("smtp", "port"): "2525"}

    def saved_numbers() -> dict[str, str]:
        db.forget_settings()
        return {key: db.get_setting(key, "") for key in numbers}

    for sent_fields in ({"silence_minutes": "", "free_space_days": "", "summary_day": "", "smtp_port": ""}, {}):
        for key, value in numbers.items():
            db.set_setting(key, value)
        db.forget_settings()
        events = {k: v for k, v in sent_fields.items() if k != "smtp_port"}
        client.post("/settings/notifications/events", follow_redirects=False,
                    data={"event_summary": "1", "summary_hour": "7", "summary_minute": "40", **events})
        channels = {k: v for k, v in sent_fields.items() if k == "smtp_port"}
        client.post("/settings/notifications", follow_redirects=False,
                    data={"smtp_enabled": "1", "smtp_host": "jiny.server", "smtp_to": "ja@doma.cz", **channels})
        check(saved_numbers() == numbers,
              f"a {'cleared' if sent_fields else 'missing'} number keeps the saved value ({saved_numbers()})")
    client.post("/settings/notifications/events", follow_redirects=False, data={
        "event_summary": "1", "silence_minutes": "90", "free_space_days": "7", "summary_day": "1",
        "summary_hour": "7", "summary_minute": "40"})
    client.post("/settings/notifications", follow_redirects=False, data={
        "smtp_enabled": "1", "smtp_host": "jiny.server", "smtp_to": "ja@doma.cz", "smtp_port": "465"})
    check(saved_numbers() == {"notify_quiet_minutes": "90", "notify_space_days": "7", "notify_summary_day": "1",
                              notifications.setting_key("smtp", "port"): "465"},
          f"a filled-in number is saved ({saved_numbers()})")

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
