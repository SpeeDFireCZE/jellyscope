r"""A settings form sent back as the browser sends it changes nothing.

Every field of a form has two sides: the name the template writes and the
name the route reads. When only one side is renamed, nothing fails loudly -
the route reads an empty value or its default and saves that: a typed-in
e-mail address disappears, the summary moves to Monday, a switch turns off.
The test the route itself has (POST with the names it knows) cannot see
that, because it sends the route's names, not the page's.

So the pages are rendered over settings set away from every default, each
form that only saves settings is read out of the HTML exactly as a browser
would send it (ticked boxes, the chosen radio and option, fields joined to
the form by form="…", the first named submit button), posted back, and the
settings must stay as they were.

Run:
    .venv/bin/python tests/test_form_round_trip.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "round_trip.db")
os.environ["SECRET_KEY"] = "test-signing-key-that-is-long-enough-for-it"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, db, notifications, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# The forms that only save settings - nothing is started, tested, deleted or downloaded.
SAVING_FORMS = {
    "/settings/interface", "/settings/language", "/settings/notifications", "/settings/notifications/events",
    "/settings/notifications/texts", "/settings/tasks", "/settings/accounts/keys", "/settings/readers",
    "/settings/viewers", "/settings",
}
# Left out, and why: /settings/connection and /settings/arr/<app>, /settings/seerr test a connection;
# /settings/database switches the database; the rest start, delete, restore or download something.
# Settings away from every default, so a field the route no longer finds shows as a change.
AWAY_FROM_DEFAULTS = {
    "ui_max_streams": "7", "ui_max_viewers": "9", "ui_max_rows": "13", "ui_max_rows_mobile": "4",
    "ui_max_rows_arr": "11", "ui_max_rows_arr_mobile": "3", "ui_max_rows_seerr": "12", "ui_max_rows_seerr_mobile": "6",
    "ui_max_rows_music": "14", "ui_max_rows_music_mobile": "2", "ui_map_zoom": "wheel", "ui_map_podklad": "osm",
    "ui_skin": "classic", "ui_exact_time": "1", "ui_events": "0", "dashboard_name": "Rodina",
    "ui_dashboard": "1",
    "notify_smtp_enabled": "1", "notify_smtp_host": "smtp.example.test", "notify_smtp_port": "2525",
    "notify_smtp_user": "sender-user", "notify_smtp_sender": "from@example.test",
    "notify_smtp_recipient": "to@example.test", "notify_smtp_tls": "0", "notify_telegram_chat": "12345",
    "notify_event_space": "0", "notify_event_collector": "1", "notify_event_summary": "1",
    "notify_space_days": "21", "notify_summary_day": "3", "notify_summary_time": "07:15", "notify_quiet_minutes": "45",
    notifications.text_key("collector_down", "subject"): "Collector stopped",
    notifications.text_key("collector_down", "body"): "It has been quiet for {minutes} min",
    "logrotate_days": "9", "logrotate_count": "4",
    "poll_interval": "17", "tech_source": "ffprobe", "ffprobe_concurrency": "5",
}
# A tick box shows a lost field only from "1" (an unticked box sends nothing, like a lost one), a
# text field only when filled - so the forms go round twice: once as above, once with every 0/1
# flipped and every area of both groups ticked, then unticked.
# Saved again on every save, whatever the form says (a time stamp, a conversion mark).
NOT_COMPARED: set[str] = set()


class Forms(HTMLParser):
    """The POST forms of a page and what a browser would send for each: {action: [(name, value)]}."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []          # {"action", "id", "fields": [(name, value)], "buttons": [...]}
        self.by_id: dict[str, dict] = {}
        self.current: dict | None = None
        self.orphans: list[tuple[str, tuple[str, str]]] = []     # (form id, field) outside their form
        self.select: tuple[str, str | None, list[tuple[str, bool]]] | None = None
        self.textarea: tuple[str, str | None, list[str]] | None = None

    def _owner(self, attrs: dict) -> dict | None:
        if attrs.get("form"):
            return None
        return self.current

    def _add(self, attrs: dict, field: tuple[str, str]) -> None:
        owner = self._owner(attrs)
        if owner is not None:
            owner["fields"].append(field)
        elif attrs.get("form"):
            self.orphans.append((attrs["form"], field))

    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        attrs = {k: (v if v is not None else "") for k, v in attrs_list}
        if tag == "form" and attrs.get("method", "").lower() == "post":
            self.current = {"action": attrs.get("action", ""), "id": attrs.get("id", ""), "fields": [], "buttons": []}
            self.forms.append(self.current)
            if self.current["id"]:
                self.by_id[self.current["id"]] = self.current
        elif tag == "input" and attrs.get("name") and "disabled" not in attrs:
            kind = attrs.get("type", "text").lower()
            if kind in ("checkbox", "radio"):
                if "checked" in attrs:
                    self._add(attrs, (attrs["name"], attrs.get("value", "on")))
            elif kind == "submit":
                owner = self._owner(attrs)
                if owner is not None:
                    owner["buttons"].append((attrs["name"], attrs.get("value", "")))
            elif kind not in ("file", "button", "image", "reset"):
                self._add(attrs, (attrs["name"], attrs.get("value", "")))
        elif tag == "button" and attrs.get("name") and attrs.get("type", "submit") == "submit":
            owner = self._owner(attrs)
            if owner is not None:
                owner["buttons"].append((attrs["name"], attrs.get("value", "")))
        elif tag == "select" and attrs.get("name"):
            self.select = (attrs["name"], attrs.get("form"), [])
        elif tag == "option" and self.select is not None:
            self.select[2].append((attrs.get("value", ""), "selected" in attrs))
        elif tag == "textarea" and attrs.get("name"):
            self.textarea = (attrs["name"], attrs.get("form"), [])

    def handle_data(self, data: str) -> None:
        if self.textarea is not None:
            self.textarea[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.current = None
        elif tag == "select" and self.select is not None:
            name, form, options = self.select
            chosen = [v for v, selected in options if selected] or [v for v, _ in options[:1]]
            for value in chosen:
                self._add({"form": form or ""}, (name, value))
            self.select = None
        elif tag == "textarea" and self.textarea is not None:
            name, form, parts = self.textarea
            self._add({"form": form or ""}, (name, "".join(parts).lstrip("\n")))
            self.textarea = None

    def sent(self) -> dict[str, list[tuple[str, str]]]:
        for form_id, field in self.orphans:
            if form_id in self.by_id:
                self.by_id[form_id]["fields"].append(field)
        out: dict[str, list[tuple[str, str]]] = {}
        for form in self.forms:
            out.setdefault(form["action"], form["fields"] + form["buttons"][:1])
        return out


def settings() -> dict[str, str]:
    return {r["key"]: r["value"] for r in db.query_all("SELECT key, value FROM settings")}


def seeds(flipped: bool) -> dict[str, str]:
    out = {k: ({"0": "1", "1": "0"}.get(v, v) if flipped else v) for k, v in AWAY_FROM_DEFAULTS.items()}
    for group in (access.READER, access.VIEWER):
        out[f"{group}_anonymise"] = "1" if flipped else "0"
        out[access.MAY_HAVE_KEY_SETTING.format(who=group)] = "1" if flipped else "0"
        for area in access.AREAS:
            out[access.area_key(group, area)] = "1" if flipped else "0"
    return out


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword"})


def round_trip(flipped: bool) -> set[str]:
    for key, value in seeds(flipped).items():
        db.set_setting(key, value)
    db.forget_settings()
    seen: set[str] = set()
    for section in ("interface", "notifications", "tasks", "accounts", "jellyfin", "general", "data"):
        page = client.get(f"/settings?section={section}")
        check(page.status_code == 200, f"/settings?section={section} opens")
        parser = Forms()
        parser.feed(page.text)
        for action, fields in parser.sent().items():
            if action not in SAVING_FORMS or action in seen:
                continue
            seen.add(action)
            before = settings()
            data: dict[str, list[str]] = {}
            for name, value in fields:
                data.setdefault(name, []).append(value)
            answer = client.post(action, data=data, follow_redirects=False)
            db.forget_settings()
            after = settings()
            # A stored value that changed. A key saved for the first time takes the default, which
            # is what holds when it is missing - no change for the person using it.
            changed = sorted(k for k in before if before[k] != after.get(k) and k not in NOT_COMPARED)
            check(answer.status_code in (200, 303) and not changed,
                  f"{action}: {len(fields)} fields back, nothing changed "
                  f"({answer.status_code}; {[(k, before.get(k), after.get(k)) for k in changed][:4]})")
    return seen


for flipped in (False, True):
    print(f"--- every saving form, sent back as the page writes it ({'0/1 flipped' if flipped else 'as seeded'}) ---")
    seen = round_trip(flipped)
    check(seen == SAVING_FORMS, f"every saving form was found on a page ({sorted(SAVING_FORMS - seen)})")

print("--- the message texts form: the fields and the stored keys by the message's key ---")
client.post("/settings/notifications/texts", data={"text_collector_down_subject": "Subject by its new name",
                                                   "text_collector_down_text": "Text {minutes}"})
db.forget_settings()
check(db.get_setting(notifications.text_key("collector_down", "subject"), "") == "Subject by its new name"
      and db.get_setting(notifications.text_key("collector_down", "body"), "") == "Text {minutes}",
      "text_<message>_subject and _text are saved as the message's subject and body")

print(f"\n{'DONE - failures: 0' if not failures else f'DONE - failures: {failures}'}")
sys.exit(1 if failures else 0)
