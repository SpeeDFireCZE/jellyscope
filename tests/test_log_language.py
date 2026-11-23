# -*- coding: utf-8 -*-
r"""The log file is always English; the Log page shows it in the reader's language.

The user (21. 11. 2026): the log is always written in English, and whoever has Czech set sees it
in Czech. The file is what is downloaded and pasted into a bug report, so it stays English; the
Log page recognises each line by its English template (translations/log/en.json), takes the
values out of it and puts them into the reader's translation. A name from the interface
(`i18n.UiName`) and an error from the interface among the values are translated too; data
(a status "error", a name) is not. Lines of other libraries stay as they are.

Run:
    .venv/bin/python tests/test_log_language.py
"""
from __future__ import annotations

import ast
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
HOME = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=HOME, DATABASE_PATH=str(Path(HOME) / "t.db"), SECRET_KEY="log-language",
                  JELLYFIN_URL="http://127.0.0.1:1", JELLYFIN_API_KEY="log-language-key-1234567890",
                  JELLYSCOPE_DEMO="0")

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, applog, db, i18n, jellyfin, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")
applog.setup()
PREFIX = "2026-11-21 10:00:00,000 INFO    jellyscope.collector: "

print("--- the file is English ---")
logging.getLogger("jellyscope.tasks").info("scheduled task: %s", i18n.UiName("Library sync"))
logging.getLogger("jellyscope.collector").warning(
    "collector: %s", jellyfin.JellyfinError("Could not connect to Jellyfin: {error}", error="refused"))
logging.getLogger("jellyscope.access").info("account %s: rights %s", "adam", "error")
for handler in logging.getLogger().handlers:
    handler.flush()
text = applog.log_path().read_text(encoding="utf-8")
check("scheduled task: Library sync" in text, "a task's name is written in English")
check("collector: Could not connect to Jellyfin: refused" in text, "an error in English")
check("naplánovaná" not in text and "Nepodařilo" not in text, "nothing Czech in the file, though the server is Czech")
check(not hasattr(applog, "set_language"), "the log has no language of its own any more")

print()
print("--- the Log page reads it in the reader's language ---")
cs = "\n".join(line["text"] for line in applog.read_lines(limit=50, language="cs")["lines"])
check(i18n.translate_log("scheduled task: %s", "cs") % i18n.translate("Library sync", "cs") in cs,
      "a name from the interface is translated (i18n.UiName)")
check(i18n.translate_log("collector: %s", "cs") % "Nepodařilo se spojit s Jellyfinem: refused" in cs,
      "an error from the interface among the values is translated")
check(i18n.translate_log("account %s: rights %s", "cs") % ("adam", "error") in cs,
      "data among the values stays (the status 'error' is no sentence)")
en = "\n".join(line["text"] for line in applog.read_lines(limit=50, language="en")["lines"])
check("scheduled task: Library sync" in en and "collector: Could not connect to Jellyfin: refused" in en,
      "in English the lines are as written")
foreign = "2026-11-21 10:00:00,000 INFO    uvicorn.error: Started server process [42]"
check(applog.translate_line(foreign, "cs") == foreign, "a line of another library as it is")
check(applog.translate_line("Traceback (most recent call last):", "cs") == "Traceback (most recent call last):",
      "a line of a traceback as it is")

print()
print("--- every template is recognised ---")
SAMPLE = {"%s": "alpha", "%r": "'beta'", "%d": "7", "%02d": "05"}
wrong = []
for template in i18n.LOG_SOURCE:
    message = template.replace("%%", "\0")
    for placeholder in ("%02d", "%s", "%r", "%d"):
        message = message.replace(placeholder, SAMPLE[placeholder])
    message = message.replace("\0", "%")
    found = applog.match_template(message)
    fixed = applog.fixed_text(template)
    if not fixed:
        continue
    if found is None or (found[0] != template and len(applog.fixed_text(found[0])) <= len(fixed)):
        wrong.append(template)
check(not wrong, f"each template is recognised in a line made from it ({len(wrong)}: {wrong[:3]})")
check(applog.match_template("anything at all") is None, "a template without fixed text never matches")
odd = "a: b % c\\n d"
check(applog.translate_line(PREFIX + "collector: " + odd, "cs") == PREFIX + i18n.translate_log("collector: %s", "cs") % odd,
      "a value with colons and per cents is taken whole")

print()
print("--- data among the values stays data ---")
# The final review found "The Lord of the Rings" read as "The Lord z the Rings": a value is translated only where
# the code puts a name from the interface (UI_NAME_VALUES) or a caught error (ERROR_VALUES).
for title in ("The Lord of the Rings", "Disc Golf Pro", "Device 2", "over the top", "Recently added"):
    line = PREFIX + f"account {title}: rights Viewer 3"
    check(applog.translate_line(line, "cs") == PREFIX + i18n.translate_log("account %s: rights %s", "cs") % (title, "Viewer 3"),
          f"{title!r} stays as it is")

print()
print("--- a template with named placeholders ---")
named = "import: matched %(by_tmdb)s by tmdb, %(by_episode)s by episode number, %(by_name)s by name (%(rows)s rows)"
line = PREFIX + "import: matched 3 by tmdb, 2 by episode number, 1 by name (6 rows)"
expected = PREFIX + i18n.translate_log(named, "cs") % {"by_tmdb": "3", "by_episode": "2", "by_name": "1", "rows": "6"}
check(applog.translate_line(line, "cs") == expected, f"it is recognised and translated ({applog.translate_line(line, 'cs')[len(PREFIX):][:60]!r})")

print()
print("--- the template index under concurrent first loads ---")
# The index used to be emptied and filled again in place: a reader between the two saw no "buckets" (KeyError,
# a 500 on the Log page). A slow update() opens that moment wide; a rebuild that swaps the whole index at once
# never shows a half-built one.
import threading  # noqa: E402


class SlowIndex(dict):
    """Reading the buckets and refilling take their time - the moment between them is where a reader got hurt."""

    def __getitem__(self, key):  # type: ignore[override]
        if key == "buckets":
            time.sleep(0.2)
        return super().__getitem__(key)

    def update(self, *args, **kwargs):  # type: ignore[override]
        time.sleep(0.4)
        super().update(*args, **kwargs)


applog._TEMPLATE_INDEX = SlowIndex(applog._log_index())
original = i18n.LOG_SOURCE
errors: list[BaseException] = []


def reader() -> None:
    try:
        applog.match_template("collector: x")       # holds the index, then reads its buckets slowly
    except BaseException as error:  # noqa: BLE001 - any failure counts
        errors.append(error)


def rebuilder() -> None:
    time.sleep(0.05)
    i18n.LOG_SOURCE = dict(original)               # a changed dictionary: the next call rebuilds
    applog.match_template("collector: y")


threads = [threading.Thread(target=reader), threading.Thread(target=rebuilder)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
i18n.LOG_SOURCE = original
applog._TEMPLATE_INDEX = {}                        # a plain index again for the rest of the test
check(not errors, f"no reader sees a half-built index ({errors[:1]})")

print()
print("--- secrets ---")
logging.getLogger("jellyscope.collector").warning("collector: %s", "key " + "log-language-key-1234567890")
for handler in logging.getLogger().handlers:
    handler.flush()
shown = "".join(line["text"] for line in applog.read_lines(limit=50, language="cs")["lines"])
check("log-language-key-1234567890" not in shown, "the API key is masked before translating")

print()
print("--- 2000 lines in time ---")
for index in range(2000):
    logging.getLogger("jellyscope.collector").warning("collector: %s", f"line {index}")
for handler in logging.getLogger().handlers:
    handler.flush()
start = time.perf_counter()
listing = applog.read_lines(limit=2000, language="cs")
spent = time.perf_counter() - start
check(len(listing["lines"]) == 2000 and spent < 1.0, f"2000 lines are translated in under a second ({spent:.2f} s)")
# Realistic: the collector writes its long error at every attempt (an httpx error with an address, ~300 characters).
long_error = "Could not connect to Jellyfin: " + "All connection attempts failed: http://jellyfin.local:8096/Sessions?" + "x" * 230
for index in range(1000):
    logging.getLogger("jellyscope.collector").warning("collector: %s", long_error)
    logging.getLogger("jellyscope.collector").warning("collector: %s", f"{index} " + "y: z " * 60)
for handler in logging.getLogger().handlers:
    handler.flush()
start = time.perf_counter()
listing = applog.read_lines(limit=2000, language="cs")
spent = time.perf_counter() - start
check(spent < 1.0, f"2000 lines with long error values in under a second ({spent:.2f} s)")
check(any("Nepodařilo se spojit s Jellyfinem" in line["text"] for line in listing["lines"]), "and the error is still translated")

print()
print("--- the page and the download ---")
for name, language in (("eng_admin", "en"), ("cz_admin", "cs")):
    accounts.create(name, "log-language-pw", is_admin=True)
    accounts.set_language(accounts.get_by_name(name)["id"], language)


def client(user: str) -> TestClient:
    c = TestClient(web.app)
    c.post("/login", data={"username": user, "password": "log-language-pw", "method": "local"})
    return c


logging.getLogger("jellyscope.tasks").info("scheduled task: %s", i18n.UiName("Library sync"))   # at the end again
for handler in logging.getLogger().handlers:
    handler.flush()
czech_line = i18n.translate_log("scheduled task: %s", "cs") % i18n.translate("Library sync", "cs")
check(czech_line in client("cz_admin").get("/settings?section=log").text, "the Czech account reads the log in Czech")
english_page = client("eng_admin").get("/settings?section=log").text
check("scheduled task: Library sync" in english_page and czech_line not in english_page, "the English one in English")
download = client("cz_admin").get("/settings/log/download", params={"file": applog.log_path().name})
check(download.status_code == 200 and "scheduled task: Library sync" in download.text, "the download is the English file")

print()
print("--- the names from the interface among the values ---")
# Every log call with an i18n.UiName(...) argument has its template and positions in applog.UI_NAME_VALUES.
found: dict[str, tuple[int, ...]] = {}
for path in sorted((PROJECT / "jellyscope").rglob("*.py")):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)):
            continue
        positions = tuple(index for index, arg in enumerate(node.args[1:])
                          if isinstance(arg, ast.Call) and ast.unparse(arg.func).endswith("UiName"))
        if positions:
            found[node.args[0].value] = positions
check(found == applog.UI_NAME_VALUES, f"UI_NAME_VALUES matches the code ({found} vs {applog.UI_NAME_VALUES})")
# Every log call inside `except ... as NAME` that hands NAME (or str(NAME)) on has its template and positions in
# applog.ERROR_VALUES - only there is a value read as a message of the interface.
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
caught: dict[str, tuple[int, ...]] = {}
for path in sorted((PROJECT / "jellyscope").rglob("*.py")):
    for handler in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name):
            continue
        for node in ast.walk(handler):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS
                    and "log" in ast.unparse(node.func.value).lower() and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                positions = tuple(index for index, arg in enumerate(node.args[1:])
                                  if (isinstance(arg, ast.Name) and arg.id == handler.name)
                                  or ast.unparse(arg) == f"str({handler.name})")
                if positions:
                    caught[node.args[0].value] = positions
check(caught == applog.ERROR_VALUES,
      f"ERROR_VALUES matches the code ({sorted(set(caught.items()) ^ set(applog.ERROR_VALUES.items()))[:3]})")

print()
print("--- the setting is gone ---")
check("log_language" not in db.DEFAULT_SETTINGS and "log_language" in db.DROPPED_SETTINGS, "the setting is dropped")
db.set_setting("log_language", "cs")
db.init_db()
check(db.get_setting("log_language", "") == "", "a stored one disappears at start")
general = client("cz_admin").get("/settings?section=general").text
check('name="log_language"' not in general, "Settings → General has no log language")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
