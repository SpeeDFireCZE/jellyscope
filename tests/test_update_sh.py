# -*- coding: utf-8 -*-
r"""deploy/update.sh doopravdy spuštěný, ne jen přečtený.

Statické kontroly v test_deploy.py hlídají, že ve skriptu něco JE. Tady
se skript pustí nad vlastním malým "serverem" (holé git repo) a klonem
instalace, aby se ukázalo, co doopravdy UDĚLÁ:

* obyčejná aktualizace doběhne i bez databáze a bez starých záloh;
* aktualizace, která přinese nový update.sh, pokračuje novým skriptem,
  ten už nestahuje znovu a ví, ze které verze se přišlo;
* nová verze, která chce novější Python, se vůbec nestáhne;
* nesmysl v JELLYSCOPE_UPDATE_PRED se nepoužije.

Místo .venv/bin/python je malý shim: pip jen předstírá, zbytek jde na
Python, který pouští tenhle test. Restart se nekoná (žádná služba) -
skript to ohlásí a skončí s nulou.

Bez bashe a gitu se test přeskočí.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_update_sh.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


BASH = shutil.which("bash")
GIT = shutil.which("git")
if not BASH or not GIT:
    print("PRESKOCENO  chybí bash nebo git")
    print()
    print("HOTOVO - chyb: 0")
    sys.exit(0)

# Vlastni identita a LF: test nesmi brat jmeno ani e-mail z globalni
# konfigurace a core.autocrlf z Windows by skriptu pridal CR na konce radku.
GIT_C = ["-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main"]

koren = Path(tempfile.mkdtemp())
server = koren / "server.git"
autor = koren / "autor"
app = koren / "app"


def git(*argy: str, kde: Path | None = None) -> str:
    prikaz = [GIT, *GIT_C] + (["-C", str(kde)] if kde else []) + list(argy)
    return subprocess.run(prikaz, capture_output=True, text=True, check=True).stdout.strip()


def vydej(zprava: str) -> None:
    git("add", "-A", kde=autor)
    git("commit", "--quiet", "-m", zprava, kde=autor)
    git("push", "--quiet", kde=autor)


def hlava() -> str:
    return git("rev-parse", "--short", "HEAD", kde=app)


def aktualizuj(**prostredi: str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JELLYSCOPE_UPDATE_")}
    env.update(prostredi)
    vysledek = subprocess.run([BASH, str(app / "deploy" / "update.sh")], env=env,
                              capture_output=True, timeout=120)
    return vysledek.returncode, vysledek.stdout.decode("utf-8", "replace") + \
        vysledek.stderr.decode("utf-8", "replace")


git("init", "--quiet", "--bare", str(server))
git("clone", "--quiet", str(server), str(autor))
(autor / "deploy").mkdir()
for jmeno in ("update.sh", "python-minimum"):
    (autor / "deploy" / jmeno).write_bytes((PROJECT / "deploy" / jmeno).read_bytes())
(autor / "run.py").write_bytes(b"print('app')\n")
(autor / "requirements.txt").write_bytes(b"")
(autor / "README").write_bytes(b"v1\n")
git("add", "-A", kde=autor)
git("commit", "--quiet", "-m", "v1", kde=autor)
git("push", "--quiet", "-u", "origin", "main", kde=autor)

# `clone -c` zapise volbu do konfigurace klonu - plati i pro git, ktery
# pak spousti sam update.sh.
git("clone", "--quiet", "-c", "core.autocrlf=false", str(server), str(app))
shim = app / ".venv" / "bin" / "python"
shim.parent.mkdir(parents=True)
shim.write_bytes(
    ("#!/bin/bash\n"
     'if [[ "$1" == "-m" && "$2" == "pip" ]]; then exit 0; fi\n'
     f'exec "{Path(sys.executable).as_posix()}" "$@"\n').encode("utf-8"))
shim.chmod(0o755)

print("--- obyčejná aktualizace, bez databáze a bez starých záloh ---")
(autor / "README").write_bytes(b"v2\n")
vydej("v2")
pred = hlava()
kod, vystup = aktualizuj()
po = hlava()
check(kod == 0 and "Dependencies" in vystup,
      f"doběhne až do konce (kód {kod}, poslední řádky: {vystup.strip().splitlines()[-2:]})")
check(pred != po and f"{pred} → {po}" in vystup, f"stáhne novou verzi ({pred} → {po})")

print("--- aktualizace přinese nový update.sh ---")
with (autor / "deploy" / "update.sh").open("ab") as soubor:
    soubor.write(b"\n# changed in v3\n")
(autor / "README").write_bytes(b"v3\n")
vydej("v3")
pred = hlava()
kod, vystup = aktualizuj()
po = hlava()
druha_cast = vystup.split("continuing with the new version", 1)[-1]
check(kod == 0 and "continuing with the new version" in vystup,
      f"pokračuje novou verzí skriptu (kód {kod})")
check(f"pulled by the previous version of this script: {pred} → {po}" in druha_cast,
      f"nový skript ví, ze které verze se přišlo ({pred} → {po})")
check("Fast-forward" not in druha_cast and vystup.count("Database backup") == 2
      and "done a moment ago" in druha_cast,
      "nový skript nestahuje znovu a nezálohuje podruhé")
check((app / "README").read_bytes() == b"v3\n", "v instalaci je nová verze")

print("--- nová verze chce novější Python ---")
(autor / "deploy" / "python-minimum").write_bytes(b"3.99\n")
(autor / "README").write_bytes(b"v4\n")
vydej("v4")
pred = hlava()
kod, vystup = aktualizuj()
check(kod != 0 and "needs 3.99+" in vystup and "Nothing has been pulled" in vystup,
      f"skončí s vysvětlením (kód {kod})")
check(hlava() == pred and (app / "README").read_bytes() == b"v3\n",
      "a nic nestáhne - aplikace běží dál na tom, co má")

print("--- nesmysl v JELLYSCOPE_UPDATE_PRED ---")
(autor / "deploy" / "python-minimum").write_bytes(b"3.11\n")
vydej("v5")
kod, vystup = aktualizuj(JELLYSCOPE_UPDATE_ZNOVU="1", JELLYSCOPE_UPDATE_PRED="x; touch pwned")
check(kod == 0 and f"pulled by the previous version of this script: {hlava()} → {hlava()}" in vystup
      and not (app / "pwned").exists() and not Path("pwned").exists(),
      "nepoužije se, za výchozí verzi se vezme ta v instalaci")

print("--- úklid starých záloh ---")
zalohy = app / "data" / "before-update"
zalohy.mkdir(parents=True, exist_ok=True)
for k in range(12):
    soubor = zalohy / f"jellyscope-{k:02d}.db"
    soubor.write_bytes(b"x")
    os.utime(soubor, (1_700_000_000 + k * 60,) * 2)
kod, vystup = aktualizuj()
zbylo = sorted(p.name for p in zalohy.iterdir())
check(kod == 0 and len(zbylo) == 10 and "jellyscope-00.db" not in zbylo
      and "jellyscope-01.db" not in zbylo,
      f"nechá deset nejnovějších (kód {kod}, zbylo {len(zbylo)})")

shutil.rmtree(koren, ignore_errors=True)
print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
