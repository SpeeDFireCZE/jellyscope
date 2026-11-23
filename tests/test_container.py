# -*- coding: utf-8 -*-
r"""The application tells it runs in a container and sets up the backups itself.

Without that the backup task ended at "Není nastavená cesta pro zálohy" -
and whoever filled the path in easily hit a place outside the mounted folder.
Such a backup is stored inside the container and vanishes with the next build,
while the task reports for months that it ran. That is the worst possible
state: a backup you think you have.

So in a container `backups/` next to the database is set at start, that is
inside /app/data - and that one is always mounted outside.

Run:
    .venv/bin/python tests/test_container.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "data" / "kontejner.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ.pop("JELLYSCOPE_DOCKER", None)

from jellyscope import config, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# The test itself can run in a container (development in code-server), so
# "outside a container" is pretended - just like "a foreign container" below.
real_container = config._in_container
config._in_container = lambda: False
config.load_config(reload=True)
db.init_db()

print("--- outside a container nothing is set ---")
# On one's own machine a sensible place for backups is another disk or the
# network, not a folder next to the database. Guessing it for the user would be
# worse than staying silent.
check(config.load_config().in_docker is False, "the application knows it is not in a container")
check(db.prefill_backup_folder() == "", "it set nothing")
check(db.get_setting("backup_path", "") == "", "and the path stayed empty")
config._in_container = real_container

print()
print("--- in a container it sets the path itself ---")
os.environ["JELLYSCOPE_DOCKER"] = "1"
config.load_config(reload=True)
check(config.load_config().in_docker is True, "told by the variable from the Dockerfile")
check(config.load_config().our_image is True, "and it is our image")

path = db.prefill_backup_folder()
expected = str(Path(_tmp) / "data" / "backups")
check(path == expected, f"next to the database, that is in the mounted folder ({path})")
check(db.get_setting("backup_path", "") == expected, "and it is stored in the settings")

print()
print("--- what a person sets we do not overwrite ---")
db.set_setting("backup_path", "/mnt/nas/jellyscope")
check(db.prefill_backup_folder() == "", "a filled-in path is left alone")
check(db.get_setting("backup_path", "") == "/mnt/nas/jellyscope", "unchanged")

print()
print("--- a container and OUR image are two different questions ---")
# Where backups may go is decided by "I am in some container" - anything
# outside the mounted folder vanishes on a rebuild, whoever built the image.
#
# Whether updating from git makes sense is decided by "I am from OUR image".
# In a foreign container (LXC, a foreign image) the application can be
# installed from git quite normally and `git pull` works for it - the message
# "rebuild the image" would send a person there to rebuild something they do not have.
from jellyscope import updates  # noqa: E402

os.environ["JELLYSCOPE_DOCKER"] = "1"
config.load_config(reload=True)
check(config.load_config().our_image is True, "our image is told by the variable")
check("docker compose" in updates.why_no_update(),
      "and updating is refused with instructions for the rebuild")
os.environ.pop("JELLYSCOPE_DOCKER")

# A foreign container: `/.dockerenv` exists, our variable does not.
original = config._in_container
config._in_container = lambda: True
config.load_config(reload=True)
check(config.load_config().in_docker is True, "a foreign container is told too")
check(config.load_config().our_image is False, "but it is not our image")
check(updates.why_no_update() == "",
      f"and updating from git is not forbidden ({updates.why_no_update()})")
# The backups do not make anything up in a foreign container either: we know
# of no mounted volume there, so `data/backups` is no clever default but an
# unrequested change of the settings.
db.set_setting("backup_path", "")
check(db.prefill_backup_folder() == "", "in a foreign container the path is not filled in")
config._in_container = original
config.load_config(reload=True)

print()
print("--- the Dockerfile really sets that variable ---")
# Were it to drop out, the application in a container would not know about
# itself and the backup would end where nobody finds it.
dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")
check("JELLYSCOPE_DOCKER=1" in dockerfile, "JELLYSCOPE_DOCKER=1 is in the image")

os.environ.pop("JELLYSCOPE_DOCKER", None)
config.load_config(reload=True)

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
