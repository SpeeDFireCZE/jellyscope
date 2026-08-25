# -*- coding: utf-8 -*-
r"""Co je citlivé, ať si přečte jen vlastník.

Tři soubory na disku nesou něco, co nemá číst kdokoliv, kdo se na stroj
dostane:

* `data/secret_key` - kdo ho zná, podepíše si cookie jako správce,
* `data/database.json` - u PostgreSQL je v něm **heslo k databázi**,
  a čitelně; jinak by se s ním aplikace nepřihlásila,
* zálohy - celá databáze, tedy i otisky hesel účtů a historie sledování.

U prvního se to hlídalo od začátku, u zbylých dvou ne. Našlo to CodeQL
(`py/clear-text-storage-sensitive-data`) a mělo pravdu: opatrnost byla na
jednom místě a o kus dál chyběla.

Na Windows `chmod` práva nenastavuje - test proto sleduje, že si o ně
aplikace **řekne**, a na Linuxu navíc kontroluje, jak soubor doopravdy
dopadl. Ostrý provoz běží na Linuxu, vyvíjí se na Windows; test musí
platit na obojím, jinak si ho někdo vypne.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_prava_souboru.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "data" / "prava.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, dialect, tasks  # noqa: E402

failures = 0
POSIX = os.name != "nt"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# Na Windows chmod nic nenastaví, takže samotná práva by test neprokázala.
# Sledujeme proto volání - a na Linuxu se pak ptáme i souboru samotného.
volani: list[tuple[str, int]] = []
puvodni_chmod = Path.chmod


def sledovany_chmod(self, mode, *a, **kw):  # noqa: ANN001
    volani.append((self.name, mode))
    return puvodni_chmod(self, mode, *a, **kw)


Path.chmod = sledovany_chmod  # type: ignore[method-assign]

db.init_db()

print("--- database.json: je v něm heslo k databázi ---")
config = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="127.0.0.1", port=5432,
                                database="jellyscope", user="js",
                                password="tajne-heslo")
dialect.save_config(Path(_tmp), config)
soubor = dialect.config_path(Path(_tmp))
check(soubor.is_file(), "soubor vznikl")
check("tajne-heslo" in soubor.read_text(encoding="utf-8"),
      "heslo v něm opravdu je (proto ta práva)")
check(("database.json", 0o600) in volani, "aplikace si řekla o práva 600")
if POSIX:
    check(oct(soubor.stat().st_mode)[-3:] == "600",
          f"a soubor je má ({oct(soubor.stat().st_mode)[-3:]})")

print()
print("--- záloha: celá databáze v jednom souboru ---")
zalohy = Path(_tmp) / "zalohy"
db.set_setting("backup_path", str(zalohy))
volani.clear()
vysledek = asyncio.run(tasks._run_backup())
check(vysledek.get("status") == "ok", f"záloha proběhla ({vysledek.get('message', '')})")

if vysledek.get("status") == "ok":
    zaloha = Path(vysledek["file"])
    check(zaloha.is_file(), f"soubor je na disku ({zaloha.name})")
    check(any(jmeno == zaloha.name and mode == 0o600 for jmeno, mode in volani),
          "aplikace si řekla o práva 600")
    if POSIX:
        check(oct(zaloha.stat().st_mode)[-3:] == "600",
              f"a soubor je má ({oct(zaloha.stat().st_mode)[-3:]})")

print()
print("--- na Windows to nesmí spadnout ---")
# `chmod` tam práva nenastaví a u některých souborů umí i vyhodit chybu.
# Kdyby se neodchytila, neuložilo by se nastavení databáze vůbec.
def vybuchujici_chmod(self, mode, *a, **kw):  # noqa: ANN001
    raise OSError("chmod tu není")


Path.chmod = vybuchujici_chmod  # type: ignore[method-assign]
try:
    dialect.save_config(Path(_tmp), config)
    check(True, "uložení přežije chmod, který selže")
except OSError:
    check(False, "uložení přežije chmod, který selže")
finally:
    Path.chmod = puvodni_chmod  # type: ignore[method-assign]

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
