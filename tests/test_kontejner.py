# -*- coding: utf-8 -*-
r"""Aplikace pozná, že běží v kontejneru, a nastaví si zálohy sama.

Bez toho úloha zálohování skončila na „Není nastavená cesta pro zálohy" -
a kdo cestu vyplnil, snadno trefil místo mimo připojenou složku. Taková
záloha se uloží dovnitř kontejneru a s dalším buildem zmizí, přičemž
úloha měsíce hlásí, že proběhla. To je ten nejhorší možný stav: záloha,
o které si myslíš, že ji máš.

V kontejneru se proto při startu nastaví `backups/` vedle databáze, tedy
uvnitř /app/data - a ta se ven připojuje vždycky.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_kontejner.py
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
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


config.load_config(reload=True)
db.init_db()

print("--- mimo kontejner se nenastavuje nic ---")
# Na vlastním stroji je smysluplné místo pro zálohy jiný disk nebo síť,
# ne složka vedle databáze. Hádat to za uživatele by bylo horší než mlčet.
check(config.load_config().in_docker is False, "aplikace ví, že v kontejneru není")
check(db.predvyplnene_zalohy() == "", "nic nenastavila")
check(db.get_setting("backup_path", "") == "", "a cesta zůstala prázdná")

print()
print("--- v kontejneru si cestu nastaví ---")
os.environ["JELLYSCOPE_DOCKER"] = "1"
config.load_config(reload=True)
check(config.load_config().in_docker is True, "pozná se podle proměnné z Dockerfilu")
check(config.load_config().nas_obraz is True, "a je to náš obraz")

cesta = db.predvyplnene_zalohy()
ocekavane = str(Path(_tmp) / "data" / "backups")
check(cesta == ocekavane, f"vedle databáze, tedy v připojené složce ({cesta})")
check(db.get_setting("backup_path", "") == ocekavane, "a uloží se do nastavení")

print()
print("--- co si člověk nastaví, to mu nepřepisujeme ---")
db.set_setting("backup_path", "/mnt/nas/jellyscope")
check(db.predvyplnene_zalohy() == "", "vyplněnou cestu nechá být")
check(db.get_setting("backup_path", "") == "/mnt/nas/jellyscope", "beze změny")

print()
print("--- kontejner a NÁŠ obraz jsou dvě různé otázky ---")
# Kam se smí zálohovat, rozhoduje "jsem v nějakém kontejneru" - cokoliv
# mimo připojenou složku zmizí při přestavení, ať už obraz stavěl kdokoliv.
#
# Jestli má smysl aktualizovat z gitu, rozhoduje "jsem z NAŠEHO obrazu".
# V cizím kontejneru (LXC, cizí image) může být aplikace nainstalovaná
# úplně běžně z gitu a `git pull` jí funguje - hláška "přestav obraz" by
# tam posílala člověka přestavovat něco, co nemá.
from jellyscope import updates  # noqa: E402

os.environ["JELLYSCOPE_DOCKER"] = "1"
config.load_config(reload=True)
check(config.load_config().nas_obraz is True, "náš obraz se pozná podle proměnné")
check("docker compose" in updates.duvod_bez_aktualizace(),
      "a aktualizace se odmítne s návodem na přestavění")
os.environ.pop("JELLYSCOPE_DOCKER")

# Cizí kontejner: `/.dockerenv` existuje, naše proměnná ne.
puvodni = config._v_kontejneru
config._v_kontejneru = lambda: True
config.load_config(reload=True)
check(config.load_config().in_docker is True, "cizí kontejner se pozná taky")
check(config.load_config().nas_obraz is False, "ale náš obraz to není")
check(updates.duvod_bez_aktualizace() == "",
      f"a aktualizace z gitu se nezakazuje ({updates.duvod_bez_aktualizace()})")
# Ani zálohy si v cizím kontejneru nic nevymýšlejí: o žádném připojeném
# svazku tam nevíme, takže `data/backups` není chytrý výchozí stav, ale
# nevyžádaná změna nastavení.
db.set_setting("backup_path", "")
check(db.predvyplnene_zalohy() == "", "v cizím kontejneru se cesta nevyplňuje")
config._v_kontejneru = puvodni
config.load_config(reload=True)

print()
print("--- Dockerfile tu proměnnou opravdu nastavuje ---")
# Kdyby vypadla, aplikace by v kontejneru o sobě nevěděla a záloha by
# skončila tam, kde ji nikdo nenajde.
dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")
check("JELLYSCOPE_DOCKER=1" in dockerfile, "JELLYSCOPE_DOCKER=1 je v obrazu")

os.environ.pop("JELLYSCOPE_DOCKER", None)
config.load_config(reload=True)

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
