# -*- coding: utf-8 -*-
r"""Files with a secret are private from the first moment (CodeQL py/clear-text-storage, 23. 11. 2026).

`data/database.json` holds the PostgreSQL password and `data/secret_key` signs the sign-in cookie. Both were
written with the default permissions (usually 644: readable by everyone on the machine) and narrowed with
`chmod(0o600)` only after the write - a moment when they are readable, and where `chmod` does nothing (it fails,
Windows) they stayed so. The test makes `chmod` fail and watches:

A) database.json is born with permissions 600 and holds the configuration;
B) the generated secret_key too;
C) a file that existed with wider permissions is narrowed when written again.

Run:
    .venv/bin/python tests/test_private_files.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

HOME = Path(tempfile.mkdtemp())
os.environ.update(JELLYSCOPE_HOME=str(HOME), SECRET_KEY="")

from jellyscope import config, dialect  # noqa: E402

failures = 0


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


if os.name != "posix":
    print("permissions are POSIX only - skipped")
    print("DONE - failures: 0")
    sys.exit(0)

os.umask(0o022)                      # the usual default: new files 644
real_chmod = Path.chmod


def failing_chmod(self: Path, *args: Any, **kwargs: Any) -> None:
    raise OSError("chmod does nothing here")


print("--- A, B) born private, even when chmod fails ---")
Path.chmod = failing_chmod           # type: ignore[method-assign]
try:
    settings = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", database="jellyscope", user="jelly",
                                      password="test-password-not-real")
    dialect.save_config(HOME, settings)
    saved = dialect.config_path(HOME)
    check(mode(saved) == 0o600, f"database.json: 600 from the start ({oct(mode(saved))})")
    check(json.loads(saved.read_text(encoding="utf-8")).get("host") == "db", "and it holds the configuration")
    key = config._generated_secret_key()
    key_file = config.BASE_DIR / "data" / "secret_key"
    check(len(key) >= 32 and mode(key_file) == 0o600, f"secret_key: 600 from the start ({oct(mode(key_file))})")
finally:
    Path.chmod = real_chmod          # type: ignore[method-assign]

print()
print("--- C) an older, wider file is narrowed ---")
os.chmod(saved, 0o644)
dialect.save_config(HOME, settings)
check(mode(saved) == 0o600, f"database.json written again: 600 ({oct(mode(saved))})")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
