# -*- coding: utf-8 -*-
r"""Is everything still saved after the split into cards?

A field reports to the form by the attribute `form="tasks"`, not by lying
in it. Were one forgotten, the browser does not send it and it quietly
stops working - the form is saved, only that one value does not change.
That is why it is tried by sending, not by a look into the template.

Run:
    .venv/bin/python tests/test_card_settings.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
_t = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=_t, DATABASE_PATH=os.path.join(_t, "u.db"),
                  SECRET_KEY="dost-dlouhy-testovaci-klic-na-podpisy")

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
c = TestClient(web.app)
c.post("/login", data={"username": "admin", "password": "longpassword"})

html = c.get("/settings?section=tasks").text

# Which fields really report to the form? We take them from the page, not
# from a list in the head - that one would lie together with the template.
FIELDS = set(re.findall(r'name="([a-z_0-9]+)"[^>]*form="tasks"', html))
FIELDS |= set(re.findall(r'form="tasks"[^>]*name="([a-z_0-9]+)"', html))
print("Tasks: fields reporting to the form:", len(FIELDS))

REQUIRED = {"history_retention_days", "backup_path", "backup_keep"}
missing = REQUIRED - FIELDS
print("  missing in the form:", sorted(missing) or "nothing")

response = c.post("/settings/tasks", data={
    "history_retention_days": "123",
    "backup_path": "/tmp/zalohy-test",
    "backup_keep": "9",
    "enabled_purge": "1",
}, follow_redirects=False)
db.forget_settings()
saved = {k: db.get_setting(k, "") for k in
         ("history_retention_days", "backup_path", "backup_keep")}
print("  sending:", response.status_code, saved)

problems = []
if missing:
    problems.append(f"fields outside the tasks form: {sorted(missing)}")
if saved != {"history_retention_days": "123",
             "backup_path": "/tmp/zalohy-test", "backup_keep": "9"}:
    problems.append(f"the tasks were not saved: {saved}")

# --- Data collection and Interface -----------------------------------------------
# Here the form wraps the cards, so it is enough to check that no field
# stayed outside - and that it still saves.
for section, action, probe in (
    ("data", "/settings",
     {"tech_source": "ffprobe", "poll_interval": "25",
      "ffprobe_concurrency": "3"}),
    ("interface", "/settings/interface",
     {"ui_max_streams": "7", "ui_max_viewers": "9"}),
):
    page = c.get(f"/settings?section={section}").text
    start = page.find(f'<form method="post" action="{action}"')
    end = page.find("</form>", start)
    if start < 0 or end < 0:
        problems.append(f"{section}: the form was not found")
        continue
    inside = set(re.findall(r'name="([a-z_0-9]+)"', page[start:end]))
    # A field in its OWN form (another address, say the development date of the
    # decoration in dev) is sent by itself - "outside" is only a field that is
    # in no form and would never be sent.
    in_others = set()
    for other_form in re.findall(r"<form\b.*?</form>", page, flags=re.S):
        if f'action="{action}"' not in other_form.split(">", 1)[0]:
            in_others |= set(re.findall(r'name="([a-z_0-9]+)"', other_form))
    # `kind` and `viewport` are meta tags from the base template.
    outside = (set(re.findall(r'name="([a-z_0-9]+)"', page))
               - inside - in_others - {"section", "kind", "viewport"})
    print(f"{section}: fields in the form {len(inside)}, outside it {sorted(outside)}")
    if outside:
        problems.append(f"{section}: fields outside the form: {sorted(outside)}")

    response = c.post(action, data=probe, follow_redirects=False)
    db.forget_settings()
    got = {k: db.get_setting(k, "") for k in probe}
    print(f"  sending: {response.status_code} {got}")
    if got != probe:
        problems.append(f"{section} was not saved: {got} != {probe}")

print()
if problems:
    for problem in problems:
        print("FAIL  ", problem)
else:
    print("OK     every card saves, no field stayed outside the form")
print()
print("DONE - failures:", len(problems))
sys.exit(1 if problems else 0)
