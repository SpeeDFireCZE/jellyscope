# -*- coding: utf-8 -*-
r"""The documentation against reality.

Documentation ages quietly. Nothing falls over, the tests are green and the README still
claims there are four tasks and the demo runs on 8097 — only that has not held for a year.
This test watches exactly the sentences that can be checked by a machine:

* **the number and names of the scheduled tasks** against `tasks.TASKS`,
* **the demo's port** against what `demo.py` sets,
* **the Settings sections** the documentation refers to, against what
  they are really called,
* **the files in "Project structure"** against what lies in the package,
* **links to files in the repository** (`deploy/...`, `docs/...`) against
  what exists.

What cannot be checked: whether the text is true. Nobody does that for anybody.

Run:
    .venv/bin/python tests/test_documentation.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "dokumentace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import tasks, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


DOCUMENTS = [path for path in
             (PROJECT / "README.md", PROJECT / "DEPLOY.md",
              PROJECT / "CONTRIBUTING.md", PROJECT / "SECURITY.md")
             if path.is_file()]
check(bool(DOCUMENTS), f"the documents were found ({[d.name for d in DOCUMENTS]})")

TEXTS = {path.name: path.read_text(encoding="utf-8") for path in DOCUMENTS}

print()
print("--- how many scheduled tasks there are ---")
# The number of tasks is the most treacherous sentence in the whole README: a task is added and nobody
# remembers it is written out in the text too.
NUMERALS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
            6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
            11: "eleven", 12: "twelve"}
count = len(tasks.TASKS)
right = NUMERALS[count]
for name, text in TEXTS.items():
    found = re.findall(r"has (\w+) tasks", text)
    for hit in found:
        check(hit == right,
              f"{name}: the number of tasks matches (it writes {hit!r}, there are {count})")

print()
print("--- the names of the tasks the documentation writes out ---")
# When a task is renamed, the table in the README stays behind.
from jellyscope.i18n import EN  # noqa: E402

for task in tasks.TASKS.values():
    english = EN.get(task.name, task.name)
    where = [name for name, text in TEXTS.items() if english in text]
    check(bool(where), f"{english!r} is in the documentation ({where or 'nowhere'})")

print()
print("--- the demo's port ---")
demo_source = (PROJECT / "demo.py").read_text(encoding="utf-8")
port = re.search(r'setdefault\("PORT", "(\d+)"\)', demo_source)
check(port is not None, "demo.py sets its port")
if port:
    number = port.group(1)
    for name, text in TEXTS.items():
        # Sentences that speak about the demo and an address at the same time.
        for line in text.splitlines():
            if "demo" in line.lower() and "127.0.0.1:" in line:
                check(f"127.0.0.1:{number}" in line,
                      f"{name}: the demo's address matches", line.strip()[:60])

print()
print("--- the Settings sections referred to ---")
# "Settings → Something" must be a section that exists. The translation is taken
# from i18n, so it is also watched that a section does not move elsewhere.
section_names = {EN.get(title, title) for _key, title, _admin in web.SETTINGS_SECTIONS}
# The names of the cards inside sections - the documentation refers to them too.
#
# The Settings sections are split into `templates/settings/`, so the search
# goes deep. Reading only `settings.html` would mean that after the template
# was split the test would quietly stop seeing the cards and start reporting that
# the documentation refers somewhere it in fact refers to correctly.
for template in (PROJECT / "jellyscope" / "templates").rglob("*.html"):
    for hit in re.findall(r'<h2>\{\{ _\("([^"]+)"\) \}\}</h2>',
                          template.read_text(encoding="utf-8")):
        section_names.add(EN.get(hit, hit))

bad = []
for name, text in TEXTS.items():
    # A line break in the middle of a name ("Settings -> Data\ncollection") is not
    # another section - so the text is first glued into one line.
    one_line = " ".join(text.split())
    for hit in re.findall(r"Settings → ([A-Z][A-Za-z ]+?)(?:\*|,|\.|:|\)|<| →|$)",
                          one_line):
        target = hit.strip()
        if target and target not in section_names:
            bad.append(f"{name}: {target!r}")
check(not bad, f"all the links point at an existing section ({bad})")

print()
print("--- the files written out in the project structure ---")
readme = TEXTS.get("README.md", "")
# A row of the tree: the indentation by four characters ("│   " or spaces) gives
# the depth. Depth 0-1 are the root and jellyscope/, depth 2 are files
# inside the package on the row above them (say web/__init__.py under "web/"),
# depth 3 the files of a subpackage (web/settings/page.py under "settings/").
mentioned: set[str] = set()
nested: dict[str, set[str]] = {}
folder = subfolder = None
for line in readme.splitlines():
    match = re.match(r"^((?:│   |    )*)[├└]──\s+(\S+)", line)
    if not match:
        continue
    depth, name = len(match.group(1)) // 4, match.group(2)
    if depth <= 1:
        folder = name[:-1] if name.endswith("/") else None
        subfolder = None
        if re.fullmatch(r"\w+\.py", name):
            mentioned.add(name)
    elif depth == 2 and folder:
        subfolder = name[:-1] if name.endswith("/") else None
        if re.fullmatch(r"\w+\.py", name):
            nested.setdefault(folder, set()).add(name)
    elif depth == 3 and folder and subfolder and re.fullmatch(r"\w+\.py", name):
        nested.setdefault(f"{folder}/{subfolder}", set()).add(name)
actual = {p.name for p in (PROJECT / "jellyscope").glob("*.py")  # top level only: the tree in the README
          if p.name != "__init__.py"}
# The tree writes packages (jellyscope/stats/) out as a folder - "stats/".
packages = {p.parent.name for p in (PROJECT / "jellyscope").glob("*/__init__.py")}
mentioned_packages = set(re.findall(r"[├└]──\s+(\w+)/", readme))
check(packages <= mentioned_packages, f"the structure knows all the packages (missing: {sorted(packages - mentioned_packages)})")
missing = sorted(actual - mentioned)
extra = sorted(mentioned - actual - {"run.py", "demo.py", "manage.py"})
extra += sorted(f"{s}/{j}" for s, file_names in nested.items() for j in file_names
                if not (PROJECT / "jellyscope" / s / j).is_file())
check(not missing, f"the structure knows all the modules (missing: {missing})")
check(not extra, f"and does not write out non-existent ones (extra: {extra})")

print()
print("--- links to files in the repository ---")
missing_files = []
for name, text in TEXTS.items():
    for link in re.findall(r"`(deploy/[\w.\-]+|docs/[\w./\-]+)`", text):
        if not (PROJECT / link).exists():
            missing_files.append(f"{name}: {link}")
    for link in re.findall(r"\]\((docs/[\w./\-]+)\)", text):
        if not (PROJECT / link).exists():
            missing_files.append(f"{name}: {link}")
check(not missing_files, f"the linked files exist ({missing_files})")

# The guide to the code (dev only) links to the sources: a link to a renamed file
# would send the reader to a page that does not exist. In prod the file is not there - the check is skipped.
guide = PROJECT / "JAK-TO-FUNGUJE.md"
if guide.is_file():
    guide_text = guide.read_text(encoding="utf-8")
    links = re.findall(r"\]\((jellyscope/[\w./\-]+)\)", guide_text)
    mentions = re.findall(r"`(web/[\w/]+\.py)`", guide_text)
    dead = sorted({o for o in links if not (PROJECT / o).exists()}
                  | {z for z in mentions if not (PROJECT / "jellyscope" / z).exists()})
    check(len(links) > 5 and not dead, f"JAK-TO-FUNGUJE.md: the links to the sources lead to existing files ({dead})")
else:
    print("       JAK-TO-FUNGUJE.md is not here (prod, demo) - checking its links is skipped")

# A trap: without this check the test would pass even over a made-up link.
check(not (PROJECT / "deploy/neexistuje.sh").exists(),
      "and the check recognises a file that is not there")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
