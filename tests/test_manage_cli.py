r"""manage.py speaks English; the old Czech commands still work for a while.

The commands of manage.py are typed by people (from the README, from their
own notes), so they are an interface like an address: English in the final
version (decision 5 of the English-code spec), the old Czech ones kept as
aliases until TODO item 0 removes them, as with the old page addresses.

Run:
    .venv/bin/python tests/test_manage_cli.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.environ["JELLYSCOPE_HOME"] = tempfile.mkdtemp()

import manage  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


parser = manage.build_parser()
for words, func, admin in ((["accounts"], manage.cmd_list, None), (["add", "jana"], manage.cmd_add, False),
                           (["add", "petr", "--admin"], manage.cmd_add, True), (["password", "petr"], manage.cmd_password, None),
                           (["delete", "jana"], manage.cmd_delete, None)):
    args = parser.parse_args(words)
    check(args.func is func and (admin is None or args.admin is admin), f"manage.py {' '.join(words)}")
for words, func in ((["ucty"], manage.cmd_list), (["pridat", "petr", "--spravce"], manage.cmd_add),
                    (["heslo", "petr"], manage.cmd_password), (["smazat", "jana"], manage.cmd_delete)):
    args = parser.parse_args(words)
    check(args.func is func and (func is not manage.cmd_add or args.admin), f"the old command still works: {' '.join(words)}")

readme = (PROJECT / "README.md").read_text(encoding="utf-8")
block = readme[readme.index("## Managing accounts from the command line"):][:900]
check(all(f"manage.py {c}" in block for c in ("accounts", "add", "password", "delete")) and "--admin" in block
      and not any(f"manage.py {c}" in block for c in ("ucty", "pridat", "heslo", "smazat")),
      "the README shows the English commands only")

OLD = re.compile(r"manage\.py\s+(?:ucty|pridat|heslo|smazat)\b|--spravce\b")
sources = [p for p in (PROJECT / "jellyscope").rglob("*") if p.suffix in (".py", ".html", ".js", ".json")]
sources += [PROJECT / "README.md", PROJECT / "JAK-TO-FUNGUJE.md", *sorted((PROJECT / "deploy").glob("*.md"))]
written = [f"{p.relative_to(PROJECT)}:{t.count(chr(10), 0, m.start()) + 1}" for p in sources if p.is_file()
           for t in [p.read_text(encoding="utf-8")] for m in OLD.finditer(t)]
check(not written and bool(OLD.search('manage.py heslo <em>x</em> --spravce')),
      f"no old command is written in the app or the documentation ({written[:4]})")

print(f"\n{'DONE - failures: 0' if not failures else f'DONE - failures: {failures}'}")
sys.exit(1 if failures else 0)
