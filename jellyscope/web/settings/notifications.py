"""Notifications: channels, events, texts, the test message.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import db, i18n, notifications, tasks
from ..base import _clamp, _flash, require_admin
from .common import _save_secret

router = APIRouter()


@router.post("/settings/notifications")
def settings_notifications(
    request: Request,
    smtp_enabled: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_to: str = Form(""),
    smtp_tls: str = Form(""),
    discord_enabled: str = Form(""),
    discord_webhook: str = Form(""),
    telegram_enabled: str = Form(""),
    telegram_token: str = Form(""),
    telegram_chat: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """The settings of the channels notifications go through."""
    from ... import notifications

    db.set_setting(notifications.setting_key("smtp", "enabled"), "1" if smtp_enabled else "0")
    db.set_setting(notifications.setting_key("smtp", "host"), smtp_host.strip())
    db.set_setting(notifications.setting_key("smtp", "port"),
                   _clamp(smtp_port, 1, 65535, 587))
    db.set_setting(notifications.setting_key("smtp", "user"), smtp_user.strip())
    _save_secret(notifications.setting_key("smtp", "password"), smtp_password)
    db.set_setting(notifications.setting_key("smtp", "sender"), smtp_from.strip())
    db.set_setting(notifications.setting_key("smtp", "recipient"), smtp_to.strip())
    db.set_setting(notifications.setting_key("smtp", "tls"), "1" if smtp_tls else "0")

    db.set_setting(notifications.setting_key("discord", "enabled"), "1" if discord_enabled else "0")
    _save_secret(notifications.setting_key("discord", "webhook"), discord_webhook)

    db.set_setting(notifications.setting_key("telegram", "enabled"),
                   "1" if telegram_enabled else "0")
    _save_secret(notifications.setting_key("telegram", "token"), telegram_token)
    db.set_setting(notifications.setting_key("telegram", "chat"), telegram_chat.strip())

    db.forget_settings()
    # A channel switched on with values not filled in is a silent trap: the settings are
    # saved, nothing reports an error - and a person relies on notifications that never
    # come. It is said at once, not only at the first alarm.
    unfinished = [k for k in notifications.CHANNELS
                  if notifications.channel_enabled(k)
                  and not notifications.channel_configured(k)]
    if unfinished:
        _flash(request,
               "Channels saved, but these are switched on and not filled in: "
               "{channels}. Until you do, nothing is sent through them.", "warning",
               channels=", ".join(notifications.NAMES.get(k, k) for k in unfinished))
    else:
        _flash(request, "Channels saved.", "success")
    return RedirectResponse("/settings?section=notifications", status_code=303)


@router.post("/settings/notifications/events")
def settings_notification_events(
    request: Request,
    event_collector: str = Form(""),
    event_free_space: str = Form(""),
    event_summary: str = Form(""),
    silence_minutes: str = Form(""),
    free_space_days: str = Form(""),
    summary_day: str = Form("0"),
    summary_hour: str = Form("9"),
    summary_minute: str = Form("0"),
    account: dict[str, Any] = Depends(require_admin),
):
    """What to notify of and when."""
    from ... import notifications

    for event, value in (("collector", event_collector), ("space", event_free_space),
                         ("summary", event_summary)):
        db.set_setting(notifications.event_key(event), "1" if value else "0")

    db.set_setting("notify_quiet_minutes",
                   _clamp(silence_minutes, 5, 1440, notifications.DEFAULT_QUIET_MINUTES))
    db.set_setting("notify_space_days",
                   _clamp(free_space_days, 1, 365, notifications.DEFAULT_SPACE_DAYS))
    db.set_setting("notify_summary_day", _clamp(summary_day, 0, 6, 0))
    # The hour and the minute come from two fields; `_clamp` guards the range and pads
    # with a zero, `tasks.valid_time` is the last safeguard - nothing but "HH:MM"
    # may get into the schedule.
    time_text = (f"{int(_clamp(summary_hour, 0, 23, 9)):02d}"
                 f":{int(_clamp(summary_minute, 0, 59, 0)):02d}")
    db.set_setting("notify_summary_time", tasks.valid_time(time_text, "09:00"))

    db.forget_settings()
    _flash(request, "Saved.", "success")
    return RedirectResponse("/settings?section=notifications", status_code=303)


@router.post("/settings/notifications/texts")
async def settings_notification_texts(
    request: Request,
    account: dict[str, Any] = Depends(require_admin),
):
    """Custom wording of the messages. An empty field = the default text.

    It is read from the form directly, because messages keep coming and each has two
    fields; signing for all of them separately means forgetting one.
    A text equal to the default is saved as empty - otherwise the old one would
    stay after a change of the app's language, though nobody rewrote it.
    """
    form = await request.form()
    for message in notifications.MESSAGES:
        # The field text_<message>_subject / _text, the stored part subject / body.
        for field, part, default in (("subject", "subject", message.subject), ("text", "body", message.text)):
            if part == "body" and not message.text:
                continue
            value = str(form.get(f"text_{message.key}_{field}") or "").strip()
            value = value[:notifications.MAX_TEXT_LENGTH]
            if value in (default, i18n.translate(default)):
                value = ""
            db.set_setting(notifications.text_key(message.key, part), value)

    db.forget_settings()
    _flash(request, "Message wording saved. An empty field means the default wording.",
           "success")
    return RedirectResponse("/settings?section=notifications#texts", status_code=303)


@router.post("/settings/notifications/test")
async def settings_notification_test(
    request: Request,
    channel: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """A test message through one channel.

    The channel is compared against a fixed list - nothing else may get from
    the form into sending.
    """
    from ... import notifications

    if channel not in notifications.CHANNELS:
        _flash(request, "Unknown channel.", "error")
        return RedirectResponse("/settings?section=notifications", status_code=303)

    result = await notifications.send_test(channel)
    if result.get("ok"):
        _flash(request, "Test message sent.", "success")
    else:
        _flash(request, "Could not send: {error}", "error",
               error=result.get("error") or "?")
    return RedirectResponse("/settings?section=notifications", status_code=303)
