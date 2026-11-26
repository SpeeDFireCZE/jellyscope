"""Notifications: when something goes wrong, the application is to have somebody to tell.

Jellyscope is passive - you open it when something interests you. But the
collector asks Jellyfin every few seconds, and when its token expires or the
server's address changes, the application runs on, the pages look normal and
the history QUIETLY stops. You find out three weeks later by a hole in the
chart - and the hole is mostly forever by then: plays can be imported from
Playback Reporting, but the track's language and the bitrate are not in it.

**What this module cannot do:** tell that Jellyscope is not running. When the
container falls, nobody is there to send a message. "Is the application
running at all" belongs in Uptime Kuma or a similar watcher, not here.

So only three things where a message really says something:

* `sberac` (the collector) - runs, but does not collect,
* `misto` (space) - the disk is running out,
* `souhrn` (summary) - a weekly overview; the only one that is no failure.

On purpose there is NO "a new version is out" here. It is in the interface,
a task watches it, and as a message it is exactly the kind of noise for which
a person stops reading the channel - and then misses the first item.

Every message is sent ONLY ON A CHANGE of the state. Were it to come on every
run of the task, the watcher would be an alarm clock every ten minutes and it
would be turned off the very first day.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from . import db, i18n
from .i18n import translate as _t


def _ts(text: str) -> str:
    """A translation into the server's language - for everything that is sent or stored.

    Notifications reach people outside the application and the language of one
    account (My account) means nothing for them. Were a message put together in
    the language of whoever just clicked "Try", it would leave differently
    than the night's one. The same for the preview of the default wording in
    Settings: it is to show what is really sent. Only a message shown on the
    page stays in the account's language (`_t`).
    """
    return i18n.translate(text, i18n.server_language())

log = logging.getLogger(__name__)

# The channels that can send. The order is the order in the settings.
CHANNELS: tuple[str, ...] = ("smtp", "discord", "telegram")

# What the channels are called in messages. "smtp" is a protocol's name, not
# something a person would look for in the settings - there it is "E-mail".
NAMES: dict[str, str] = {"smtp": "E-mail", "discord": "Discord",
                         "telegram": "Telegram"}

# The events that can be notified about. Each can be turned off apart.
EVENTS: tuple[str, ...] = ("collector", "space", "summary")

# How many minutes without a successful query already mean "does not collect".
# The collector asks by seconds, so half an hour is certainty, not impatience -
# short network outages would otherwise send a message every time.
DEFAULT_QUIET_MINUTES = 30

# Below how many days until the disk is full to speak up.
DEFAULT_SPACE_DAYS = 14

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# The messages' texts
# ---------------------------------------------------------------------------
#
# Every message has a subject and a text. Both have a default form and the
# admin can rewrite it in Settings - an empty field means the default. The
# default texts go through translation only at the moment of sending, not when
# stored: whoever did not rewrite them gets them in the language the
# application has set right then.
#
# A failure and its end are TWO different messages. On recovery "zase to běží"
# used to be sent with the failure's name as the text - a person got
# "Jellyscope: zase to běží / sběrač nesbírá" and read it as another alarm.

class Message:
    def __init__(self, key: str, name: str, subject: str, text: str,
                 variables: tuple[tuple[str, str, str], ...] = ()) -> None:
        self.key = key                # e.g. "collector_down"
        self.name = name              # the label in Settings
        self.subject = subject        # the default subject (Czech, it is translated)
        self.text = text              # the default text
        self.variables = variables    # (name, what it fills in, an example) - see below


# The variables of a message's own wording: (name, what it fills in, an example).
#
# The names are English, not Czech: an own text then holds the same in every
# interface language and there is nothing to translate. The description and the
# example are translated in the Settings window. Every message has its own -
# `{threshold}` is the collector's quiet in minutes, for space the bound in days.
COLLECTOR_VARIABLES: tuple[tuple[str, str, str], ...] = (
    ("detail", "The whole sentence saying why the collector is not collecting: the error text, or how many minutes it has been silent.",
     "the last successful request to Jellyfin was 35 minutes ago"),
    ("minutes", "How many minutes the collector has brought nothing.", "35"),
    ("threshold", "The collector silence from the settings (minutes): nothing is reported sooner.", "30"),
    ("error", "The error text the collector reports (– when there is none).", "Jellyfin is not answering (timeout)"),
)
SPACE_VARIABLES: tuple[tuple[str, str, str], ...] = (
    ("detail", "The whole sentence saying in how many days space runs out and on which disk.",
     "at the current pace disk “Films” runs out of space in 12 days"),
    ("disk", "The disk that runs out of space first (its name from the Disks card).", "Movies"),
    ("days", "In how many days space runs out (– when the disk is not filling).", "12"),
    ("free", "How much is free on that disk now.", "480 GB"),
    ("per_day", "How much the disk fills up per day.", "5.0 GB"),
    ("threshold", "The threshold from the settings: report when space runs out sooner than in this many days.", "14"),
)


MESSAGES: tuple[Message, ...] = (
    Message("collector_down", "Collector not collecting",
            "Jellyscope: the collector is not collecting",
            "The collector is running but gets nothing from Jellyfin: {detail}",
            COLLECTOR_VARIABLES),
    Message("collector_back", "Collector collecting again",
            "Jellyscope: the collector is collecting again",
            "The collector is back and history is being recorded again."),
    Message("space_low", "Running out of space",
            "Jellyscope: running out of space",
            "{detail}",
            SPACE_VARIABLES),
    Message("space_ok", "Enough space again",
            "Jellyscope: enough space again",
            "The disk-full forecast is back above the threshold you set.",
            tuple(p for p in SPACE_VARIABLES if p[0] in ("disk", "days", "free"))),
    Message("summary", "Weekly summary",
            "Jellyscope: weekly summary",
            ""),
)

MESSAGES_BY_KEY = {m.key: m for m in MESSAGES}

# The ceiling of an own text's length. A message is a notice, not a letter.
MAX_TEXT_LENGTH = 500


def text_key(message: str, part: str) -> str:
    """The name of the setting with one message's own subject / text."""
    return f"notify_text_{message}_{part}"


def message_text(message_key: str, **values: Any) -> tuple[str, str]:
    """A message's subject and text - own, or default - with the values filled in.

    Only what the message knows is filled in (see *_VARIABLES). Were the admin
    to write curly brackets into the text for something else, `str.format`
    would fail - and that on sending an alarm, so at the worst moment. So it is
    replaced one by one and anything unknown stays as it is.
    """
    message = MESSAGES_BY_KEY[message_key]
    subject = db.get_setting(text_key(message_key, "subject"), "").strip() \
        or _ts(message.subject)
    text = db.get_setting(text_key(message_key, "body"), "").strip() \
        or (_ts(message.text) if message.text else "")
    for name, value in values.items():
        subject = subject.replace("{" + name + "}", str(value))
        text = text.replace("{" + name + "}", str(value))
    # The subject is one line. E-mail refuses a header with a line break (and
    # the alarm would not be sent), and `{detail}` - the collector's error text -
    # may well contain one. More lines would moreover be an opportunity to slip
    # another header into the message.
    subject = " ".join(subject.split())
    return subject, text


def texts_for_settings() -> list[dict[str, Any]]:
    """Messages for the Settings page: what is own and what would apply."""
    return [{
        "key": m.key,
        "name": m.name,
        "subject": db.get_setting(text_key(m.key, "subject"), ""),
        "text": db.get_setting(text_key(m.key, "body"), ""),
        "subject_default": _ts(m.subject),
        "text_default": _ts(m.text) if m.text else "",
        "has_text": bool(m.text),
        "variables": [{"name": name, "description": description, "example": example}
                     for name, description, example in m.variables],
    } for m in MESSAGES]


def setting_key(channel: str, name: str) -> str:
    """The name of the setting of one channel field."""
    return f"notify_{channel}_{name}"


def event_key(event: str) -> str:
    return f"notify_event_{event}"


def _state_key(event: str) -> str:
    """Where it is remembered how it ended last time - for reporting on a CHANGE."""
    return f"notify_state_{event}"


# Fields that must not get into a template nor into the log.
SECRET_KEYS = tuple(setting_key(channel, name) for channel, name in
                    (("smtp", "password"), ("discord", "webhook"), ("telegram", "token")))


def channel_enabled(channel: str) -> bool:
    return db.get_setting(setting_key(channel, "enabled"), "") == "1"


def _non_empty(channel: str, *fields: str) -> bool:
    return all(db.get_setting(setting_key(channel, name), "").strip() for name in fields)


def channel_configured(channel: str) -> bool:
    """Does the channel have filled in everything without which nothing can be sent?"""
    if channel == "smtp":
        return _non_empty("smtp", "host", "recipient")
    if channel == "discord":
        return _non_empty("discord", "webhook")
    if channel == "telegram":
        return _non_empty("telegram", "token", "chat")
    return False


def enabled_channels() -> list[str]:
    return [k for k in CHANNELS if channel_enabled(k) and channel_configured(k)]


def event_enabled(event: str) -> bool:
    return db.get_setting(event_key(event), "") == "1"


# Set once an SMTP set up before the certificate check was given "do not verify" (convert_smtp_verification).
SMTP_VERIFY_CONVERSION_KEY = "conversion_smtp_verify"


def convert_smtp_verification() -> bool:
    """One-off at start: an SMTP set up before the mail certificate was checked keeps sending without the check.

    Until 25. 11. 2026 the certificate of the mail server was not verified, so a home server with a self-signed
    one worked. Verifying from the upgrade on would silence its alarms without a word - so an installation with
    an SMTP host keeps "do not verify" (the settings say so) and the admin decides. A new installation, or SMTP
    filled in later, verifies. Once per database: after it the mark is set. True = "do not verify" was stored.
    """
    if db.get_setting(SMTP_VERIFY_CONVERSION_KEY, ""):
        return False
    stored = False
    verify_key = setting_key("smtp", "verify")
    if db.get_setting(setting_key("smtp", "host"), "").strip() and not db.get_setting(verify_key, ""):
        db.set_setting(verify_key, "0")
        stored = True
    db.set_setting(SMTP_VERIFY_CONVERSION_KEY, "1")
    return stored


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _raise_for_status(response: Any) -> None:
    """`raise_for_status` without the address in the error's text.

    httpx writes the address in as it normalised it (the host in lower case, without `:443`, an invisible
    character copied along percent-encoded), so masking by the stored value (`without_secrets`) missed it - and
    with Discord the address is the secret, with Telegram the token is in it. The status is enough to tell why.
    """
    import httpx

    if response.is_error:
        raise httpx.HTTPStatusError(f"{response.status_code} {response.reason_phrase}",
                                    request=response.request, response=response)


async def _send_discord(subject: str, text: str) -> None:
    import httpx

    webhook = db.get_setting(setting_key("discord", "webhook"), "").strip()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(webhook, json={
            "content": f"**{subject}**\n{text}",
            # Titles' names from the library go into the message. A movie named
            # "@everyone" would otherwise ping the whole channel - and it is a
            # file's name, not an instruction. Without this field Discord
            # processes mentions.
            "allowed_mentions": {"parse": []},
        })
        _raise_for_status(response)


async def _send_telegram(subject: str, text: str) -> None:
    import httpx

    token = db.get_setting(setting_key("telegram", "token"), "").strip()
    chat = db.get_setting(setting_key("telegram", "chat"), "").strip()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": f"{subject}\n\n{text}"})
        _raise_for_status(response)


# What a refused certificate of the mail server says - the checkbox by its name in the settings.
CERTIFICATE_ERROR = ("The mail server's certificate could not be verified ({error}). If the server uses a "
                     "self-signed certificate, untick “verify the server's certificate” in the e-mail settings.")


def _tls_context() -> Any:
    """The TLS context of the mail connection: the certificate and the host name checked, unless switched off.

    smtplib without a context of its own checks neither - whoever sits on the way offers their own certificate
    and gets the login with the mailbox's password. A home mail server with a self-signed certificate is let
    through only when the admin unticks the check in the settings.
    """
    import ssl

    context = ssl.create_default_context()
    if db.get_setting(setting_key("smtp", "verify"), "1") == "0":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _send_smtp_blocking(subject: str, text: str) -> None:
    """A function of its own, because smtplib is blocking - see _send_smtp()."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    host = db.get_setting(setting_key("smtp", "host"), "").strip()
    port = db.get_int_setting(setting_key("smtp", "port"), 1, 65535, 587)
    user = db.get_setting(setting_key("smtp", "user"), "").strip()
    password = db.get_setting(setting_key("smtp", "password"), "")
    sender = db.get_setting(setting_key("smtp", "sender"), "").strip() or user
    recipient = db.get_setting(setting_key("smtp", "recipient"), "").strip()
    tls = db.get_setting(setting_key("smtp", "tls"), "1") == "1"

    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = sender or recipient
    email["To"] = recipient
    email.set_content(text)

    try:
        # Port 465 is SMTPS (encrypted from the first byte), 587 is STARTTLS (an
        # unencrypted connection that is upgraded). Mixing them up means the
        # connection just hangs quietly until the timeout.
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=_tls_context()) as connection:
                if user:
                    connection.login(user, password)
                connection.send_message(email)
            return

        with smtplib.SMTP(host, port, timeout=20) as connection:
            if tls:
                connection.starttls(context=_tls_context())
            if user:
                connection.login(user, password)
            connection.send_message(email)
    except ssl.SSLCertVerificationError as error:
        # The error names the checkbox: a home server's own certificate is the usual cause.
        raise i18n.TranslatableError(CERTIFICATE_ERROR,
                                     error=getattr(error, "verify_message", None) or str(error)) from error


async def _send_smtp(subject: str, text: str) -> None:
    # smtplib is blocking and waiting for a foreign server takes time - in a
    # thread, so the whole server does not stop.
    await asyncio.to_thread(_send_smtp_blocking, subject, text)


SENDERS = {
    "smtp": _send_smtp,
    "discord": _send_discord,
    "telegram": _send_telegram,
}


def without_secrets(text: str) -> str:
    """Strikes everything that is a credential out of a text.

    An error from the HTTP client carries the WHOLE address in it - and with
    Telegram the bot's token is in the address, with Discord the webhook's
    address is itself the secret. That message is moreover stored and **shown
    in the settings**, so the token would shine on the screen.

    It is masked by the stored values, not by a pattern: a pattern would always
    miss something with names that change between services.
    """
    for secret_key in SECRET_KEYS:
        value = db.get_setting(secret_key, "").strip()
        # Short values are not masked: replacing three characters would make a
        # riddle of the message and would not help to keep it secret anyway.
        if len(value) >= 8:
            text = text.replace(value, "…")
        # A webhook is a whole address; in a message it tends to be without the scheme too.
        if value.startswith("https://") and len(value) > 16:
            text = text.replace(value[8:], "…")
    return text


async def send(subject: str, text: str,
               only_channel: str | None = None) -> list[dict[str, Any]]:
    """Sends a message through all enabled channels. Returns the result for each.

    A failure of one channel does not bring down the others: when SMTP does not
    work, the message is still to reach Discord. The error is returned as text,
    because the settings are to show WHY it did not work - "it failed" is of
    no use.
    """
    channels = [only_channel] if only_channel else enabled_channels()
    results: list[dict[str, Any]] = []
    for channel in channels:
        if channel not in SENDERS:
            continue
        try:
            await SENDERS[channel](subject, text)
            results.append({"channel": channel, "ok": True, "error": ""})
        except Exception as error:            # noqa: BLE001 - the network fails in various ways
            # The error's text goes through masking: the HTTP client puts the whole
            # address into it, and in it is Telegram's token and Discord's whole webhook.
            description = without_secrets(str(error))
            log.warning("could not send a notification through %s: %s: %s",
                        channel, type(error).__name__, description)
            # The log file is English; the error the settings show is in the server's language (a
            # refused mail certificate names the checkbox by the name the page gives it).
            results.append({"channel": channel, "ok": False,
                            "error": without_secrets(i18n.error_text(error, i18n.server_language()))})

    if results:
        db.set_setting("notify_last_send", db.utcnow())
        db.set_setting("notify_last_error",
                       "; ".join(f"{v['channel']}: {v['error']}"
                                 for v in results if not v["ok"]))
    return results


# ---------------------------------------------------------------------------
# What we watch
# ---------------------------------------------------------------------------

# When the collector first reported an error without ever having brought
# anything before (a fresh installation with a wrong key). Otherwise the quiet
# is measured from the last successful query.
ERROR_SINCE_KEY = "notify_collector_error_since"


def _time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), TIME_FORMAT) if text.strip() else None
    except ValueError:
        return None


def _collector_state() -> dict[str, Any]:
    """Does the collector not collect? {"bad", "reason", "values": {minutes, threshold, error}}.

    It is a failure when the collector brought nothing for longer than the set
    quiet - **even when it reports an error**. An error by itself is no
    failure: Jellyfin restarts, the network drops for a while, and the very
    next query passes. The error used to be reported at once and the quiet
    applied only to a collector silent without an error. The quiet is measured
    from the last successful query; when there was none, from the moment the
    error first appeared.
    """
    from . import collector

    quiet = db.get_int_setting("notify_quiet_minutes", 5, 1440, DEFAULT_QUIET_MINUTES)
    all_fine = {"bad": False, "reason": "", "values": {"minutes": "–", "threshold": str(quiet), "error": "–"}}
    status = db.get_setting(collector.STATUS_KEY, "unknown")
    if status == "demo":
        return all_fine            # the demo mode collects nothing on purpose

    now = datetime.now(UTC).replace(tzinfo=None)
    # The stored error (a key and its values) in the server's language, like the rest of a notification.
    error = collector.error_text(i18n.server_language()).strip() if status == "error" else ""
    if error:
        error_since = _time(db.get_setting(ERROR_SINCE_KEY, ""))
        if error_since is None:
            error_since = now
            db.set_setting(ERROR_SINCE_KEY, now.strftime(TIME_FORMAT))
    else:
        error_since = None
        if db.get_setting(ERROR_SINCE_KEY, ""):
            db.set_setting(ERROR_SINCE_KEY, "")

    since = _time(db.get_setting(collector.LAST_POLL_KEY, "")) or error_since
    if since is None:
        # Never anything yet and not even an error - that is no failure, that is a fresh installation.
        return all_fine
    minutes = int((now - since).total_seconds() / 60)
    values = {"minutes": str(minutes), "threshold": str(quiet), "error": error or "–"}
    if minutes < quiet:
        return {"bad": False, "reason": "", "values": values}
    reason = error or _ts("the last successful request to Jellyfin was {minutes} minutes ago").format(minutes=minutes)
    return {"bad": True, "reason": reason, "values": values}


def _collector_stalled() -> tuple[bool, str]:
    """Does the collector run, but has brought nothing for long? Returns (failing, reason). See _collector_state()."""
    state = _collector_state()
    return state["bad"], state["reason"]


def _space_state() -> dict[str, Any]:
    """What the space watch knows: the forecast of the library's growth and the values of the message's variables.

    Returns {"growth": stats.space_forecast(90), "threshold": days from the settings,
    "values": {disk, days, free, per_day, threshold}} - the values already as
    text, as they are filled into an own wording (see SPACE_VARIABLES). The pace
    is the one the forecast comes from: for a disk Jellyfin reports space about,
    the loss of free space, otherwise the library's growth.
    """
    from . import formatting, stats

    growth = stats.space_forecast(90)
    first_out = growth.get("first_out") or {}
    threshold = db.get_int_setting("notify_space_days", 1, 365, DEFAULT_SPACE_DAYS)
    days_left, free = growth.get("days_left"), growth.get("free_space")
    per_day = first_out["daily"] if first_out.get("days_left") is not None else growth.get("daily")
    return {"growth": growth, "threshold": threshold, "values": {
        "disk": str(growth.get("disk_name") or first_out.get("name") or ""),
        "days": str(int(days_left)) if days_left is not None else "–",
        "free": formatting.bytes_human(free) if free is not None else "–",
        "per_day": formatting.bytes_human(per_day) if per_day and per_day > 0 else "–",
        "threshold": str(threshold),
    }}


def _space_running_out(state: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Is the disk getting full? Only where the application sees the files."""
    state = state or _space_state()
    growth = state["growth"]
    remaining = growth.get("days_left")
    if remaining is None:
        # We do not know the free space, or the library does not grow - in both
        # cases there is nothing to forecast.
        return False, ""

    threshold = state["threshold"]
    if int(remaining) > threshold:
        return False, ""
    if growth.get("disk_name"):
        # More disks: say which one is running out of space - otherwise it would be searched for.
        return True, _ts("at the current rate the disk “{disk}” will run out of space in {days} days").format(
            disk=growth["disk_name"], days=int(remaining))
    return True, _ts("at the current rate the space runs out in {days} days").format(days=int(remaining))


def _weekly_summary() -> str:
    """The text of the weekly overview."""
    from . import formatting, stats

    # The week since the previous summary - 7 x 24 hours back from now, not the statistics' "last 7 days" (whole
    # calendar days, stats.period_of_days): sent on Monday at nine it would leave out last Monday from nine to
    # midnight, which the previous summary did not have yet either.
    week = stats.Period(since=(datetime.now(UTC) - timedelta(days=7)).strftime(db.TIME_FORMAT),
                        until=stats.END_OF_TIME, days=7)
    overview_data = stats.overview(week)
    hours = float(overview_data.get("watched_seconds") or 0) / 3600.0
    lines = [
        f"{_ts('Watched')}: {formatting.hours_human(hours)}",
        f"{_ts('Plays#starts')}: {formatting.number(overview_data.get('plays') or 0)}",
        f"{_ts('Viewers')}: {formatting.number(overview_data.get('users') or 0)}",
    ]

    titles = stats.top_items(week, limit=3)
    if titles:
        lines.append("")
        lines.append(_ts("Most watched titles") + ":")
        lines += [f"  {t['label']} - {formatting.hours_human(t['hours'])}"
                  for t in titles]

    people = stats.top_users(week, limit=3)
    if people:
        lines.append("")
        lines.append(_ts("Most active users") + ":")
        lines += [f"  {u['label']} - {formatting.hours_human(u['hours'])}"
                  for u in people]
    return "\n".join(lines)


def _delivered(results: list[dict[str, Any]]) -> bool:
    """Did at least one channel take the message? (send() returns a result per channel.)"""
    return any(result.get("ok") for result in results)


async def _report_change(event: str, failing: bool, reason: str, **values: Any) -> str:
    """Sends a message only when the state CHANGED. Returns "" (no change), "sent" or "failed".

    The new state is stored only once a channel took the message: the outage that stops the
    collector often stops the sending too, and the alarm would be lost for good. Until then the
    next run tries again.

    Without that the same sentence would come on every run of the task, a
    person would turn the notifications off and it would be of no use.

    That it got fixed is the same information as that it broke - without it a
    person does not know whether they are to go and deal with something. But it
    is ANOTHER message with its own subject and text (see MESSAGES), not
    "zase to běží" with the failure's name under it - that read as another alarm.
    """
    previous = db.get_setting(_state_key(event), "ok")
    current = "bad" if failing else "ok"
    if previous == current:
        return ""

    # `values` are the message's further variables (disk, days, minutes...), see *_VARIABLES.
    subject, text = message_text(CHANGE_MESSAGES[event][failing],
                                 detail=reason, **values)
    if not _delivered(await send(subject, text)):
        return "failed"
    db.set_setting(_state_key(event), current)
    return "sent"


# The message a change of an event's state sends: {event: {failing: message}}.
CHANGE_MESSAGES = {"collector": {True: "collector_down", False: "collector_back"},
                   "space": {True: "space_low", False: "space_ok"}}


def _summary_due(now: datetime) -> bool:
    """Is it the right day and hour - and was it not sent yet this week?"""
    weekday = db.get_int_setting("notify_summary_day", 0, 6, 0)     # 0 = Monday
    time_text = db.get_setting("notify_summary_time", "09:00").strip() or "09:00"
    try:
        hour, minute = (int(c) for c in time_text.split(":", 1))
    except ValueError:
        hour, minute = 9, 0

    if now.weekday() != weekday:
        return False
    if (now.hour, now.minute) < (hour, minute):
        return False

    # Once a week, not on every run of the task for the rest of the day.
    last = db.get_setting("notify_summary_sent", "").strip()
    return last != now.date().isoformat()


async def check(now: datetime | None = None) -> dict[str, Any]:
    """Goes through the watched things and sends what is needed. Run by the task."""
    if not enabled_channels():
        return {"status": "ok", "message": _ts("No channel is set up."), "task_log": "No channel is set up."}

    now = now or datetime.now()
    outcomes: dict[str, list[str]] = {"sent": [], "failed": []}

    if event_enabled("collector"):
        collector_state = _collector_state()
        outcome = await _report_change("collector", collector_state["bad"], collector_state["reason"],
                                       **collector_state["values"])
        if outcome:
            outcomes[outcome].append("collector")

    if event_enabled("space"):
        space_state = _space_state()
        failing, reason = _space_running_out(space_state)
        outcome = await _report_change("space", failing, reason, **space_state["values"])
        if outcome:
            outcomes[outcome].append("space")

    if event_enabled("summary") and _summary_due(now):
        subject, _text = message_text("summary")
        # Marked as sent only once a channel took it - otherwise a later run that day tries again.
        if _delivered(await send(subject, _weekly_summary())):
            db.set_setting("notify_summary_sent", now.date().isoformat())
            outcomes["sent"].append("summary")
        else:
            outcomes["failed"].append("summary")

    sent, failed = outcomes["sent"], outcomes["failed"]
    if not sent and not failed:
        return {"status": "ok", "message": _ts("Nothing new to report."), "task_log": "Nothing new to report."}
    # `task_log` is the same as a key and its values: the task log is read in the reader's language.
    task_log = i18n.join_messages(
        ([i18n.Message("Sent: {what}", what=", ".join(sent))] if sent else [])
        + ([i18n.Message("Sending failed: {what}", what=", ".join(failed))] if failed else []), ". ")
    return {"status": "error" if failed else "ok", "message": task_log.translated(i18n.server_language()),
            "task_log": task_log}


async def send_test(channel: str) -> dict[str, Any]:
    """A test message from the button in the settings.

    Without it the SMTP settings are checked only by something going wrong -
    and that is exactly the moment when it already has to work.
    """
    if not channel_configured(channel):
        return {"ok": False, "error": _t("The channel is not filled in.")}
    results = await send(
        _ts("Jellyscope: test message"),
        _ts("If you are reading this, notifications work."), only_channel=channel)
    return results[0] if results else {"ok": False, "error": "?"}


def state() -> dict[str, Any]:
    """What the Settings page needs about notifications."""
    return {
        "channels": [{"key": k,
                    "enabled": channel_enabled(k),
                    "configured": channel_configured(k)} for k in CHANNELS],
        "events": [{"key": u, "enabled": event_enabled(u)} for u in EVENTS],
        "last_sent": db.get_setting("notify_last_send", ""),
        "last_error": db.get_setting("notify_last_error", ""),
        "quiet_minutes": db.get_int_setting("notify_quiet_minutes", 5, 1440,
                                          DEFAULT_QUIET_MINUTES),
        "space_days": db.get_int_setting("notify_space_days", 1, 365,
                                        DEFAULT_SPACE_DAYS),
        "summary_day": db.get_int_setting("notify_summary_day", 0, 6, 0),
        "summary_time": db.get_setting("notify_summary_time", "09:00") or "09:00",
        "messages": texts_for_settings(),
    }
