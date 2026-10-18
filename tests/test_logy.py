# -*- coding: utf-8 -*-
r"""Logy: co se do nich píše, log API, stažení a rotace.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_logy.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "logy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import collector, db  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


class Zachyt(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.zpravy: list[str] = []

    def emit(self, zaznam: logging.LogRecord) -> None:
        self.zpravy.append(zaznam.getMessage())


db.init_db()
zachyt = Zachyt()
logging.getLogger("jellyscope.db").addHandler(zachyt)
logging.getLogger("jellyscope.db").setLevel(logging.INFO)

print("--- provozní záznamy se nelogují ---")
# Sběrač přepisuje čas posledního dotazu při každém dotazu na Jellyfin,
# klidně každé tři vteřiny. V logu by po tom nezůstalo nic než šum.
for kolo in range(3):
    db.set_setting(collector.LAST_POLL_KEY, f"2026-10-18 10:00:0{kolo}")
    db.set_setting(collector.STATUS_KEY, "ok" if kolo % 2 else "error")
check(not [z for z in zachyt.zpravy if "_collector" in z],
      f"stav sběrače v logu není ({len(zachyt.zpravy)} hlášek)")
db.set_setting("poll_interval", "7")
check(any("poll_interval" in z for z in zachyt.zpravy), "běžné nastavení se loguje dál")

import asyncio  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, applog, tasks, web  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
ctenar_id = accounts.create("ctenar", "ctenarheslo")
db.set_setting("ctenar_smi_klic", "1")


def prihlas(jmeno: str, heslo: str) -> TestClient:
    klient = TestClient(web.app)
    klient.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
                follow_redirects=False)
    return klient


anonym = TestClient(web.app)
spravce = prihlas("spravce", "dlouheheslo")
ctenar = prihlas("ctenar", "ctenarheslo")


def zavolej(token: str, cesta: str = "/api/v1/summary"):
    return anonym.get(cesta, headers={"Authorization": f"Bearer {token}"})


SLOZKA = applog.log_dir()

print()
print("--- log API je soubor ---")
serveru = api.vytvor("Grafana")
ctenare = api.vytvor("Homepage | doma", ctenar_id)
zavolej(serveru["token"], "/api/v1/summary?days=7")
zavolej(ctenare["token"])
zavolej("js_neplatny")
radky = applog.read_api(100)["radky"]
check(len(radky) == 2, f"zapíše se každý dotaz s existujícím klíčem, neplatný ne ({len(radky)})")
check(bool(radky) and radky[0]["klic_id"] == ctenare["id"] and radky[0]["vlastnik"] == "ctenar"
      and radky[0]["klic"] == "Homepage / doma",
      "nejnovější nahoře, s klíčem a vlastníkem (svislítko ze jména pryč)")
check(len(radky) == 2 and radky[1]["vlastnik"] == "" and radky[1]["vysledek"] == 200
      and radky[1]["cesta"] == "/api/v1/summary?days=7" and radky[1]["ip"] == "testclient",
      "klíč serveru: bez vlastníka, s adresou i IP")
api.vypni(ctenare["id"])
zavolej(ctenare["token"])
api.zapni(ctenare["id"])
check(applog.read_api(100)["radky"][0]["vysledek"] == 403, "zamítnutý dotaz se zapíše taky")
check(all(s["name"] != applog.API_NAME for s in applog.available_files()),
      "v přepínači logu aplikace api.log není (má vlastní pohled)")
check(not (SLOZKA / applog.OWN_NAME).exists()
      or "/api/v1/summary" not in (SLOZKA / applog.OWN_NAME).read_text(encoding="utf-8"),
      "do logu aplikace se dotazy nepíšou")

print()
print("--- filtry logu API ---")
check({r["vlastnik"] for r in applog.read_api(100, ucet="ctenar")["radky"]} == {"ctenar"},
      "podle uživatele")
check({r["vlastnik"] for r in applog.read_api(100, ucet="-")["radky"]} == {""},
      "„-\" = klíč serveru")
check({r["klic_id"] for r in applog.read_api(100, klic=str(serveru["id"]))["radky"]}
      == {serveru["id"]}, "podle klíče")
check(len(applog.read_api(100, ip="testcl")["radky"]) == 3
      and not applog.read_api(100, ip="10.0.0.")["radky"], "podle části IP adresy")
check(applog.read_api(100, klic="nesmysl")["radky"] != [], "nesmysl v klíči = bez filtru")

print()
print("--- Nastavení → Log ---")
stranka = spravce.get("/settings?section=log").text
check('href="/settings?section=log&amp;log_druh=api"' in stranka, "přepínač na log API")
stranka = spravce.get("/settings?section=log&log_druh=api&log_ucet=ctenar").text
check("Homepage / doma" in stranka and "/api/v1/summary?days=7" not in stranka,
      "filtr podle uživatele ukáže jen jeho dotazy")
stranka = spravce.get(f"/settings?section=log&log_druh=api&log_klic={serveru['id']}").text
check("/api/v1/summary?days=7" in stranka, "filtr podle klíče")
check(ctenar.get("/settings?section=log&log_druh=api", follow_redirects=False).status_code == 403,
      "čtenář do logu nesmí")
odkaz = spravce.get("/settings?section=api").text
check(f"log_klic={serveru['id']}" in odkaz, "Nastavení → API odkazuje na log API klíče")

print()
print("--- stažení ---")
applog.setup()
logging.getLogger("jellyscope.test").warning("pokus password=tajne123 konec")
odpoved = spravce.get(f"/settings/log/stahnout?soubor={applog.OWN_NAME}")
check(odpoved.status_code == 200 and "attachment" in odpoved.headers.get("content-disposition", "")
      and "pokus password=***" in odpoved.text and "tajne123" not in odpoved.text,
      "log aplikace se stáhne celý, s heslem nahrazeným hvězdičkami")
odpoved = spravce.get(f"/settings/log/stahnout?soubor={applog.API_NAME}")
check(odpoved.status_code == 200 and "Homepage / doma" in odpoved.text, "log API taky")
for zle in ("../.env", "..\\logy.db", "logy.db", "api.log.x", "/etc/passwd", ""):
    check(spravce.get("/settings/log/stahnout", params={"soubor": zle}).status_code == 404,
          f"mimo složku logů nic: {zle!r}")
check(ctenar.get(f"/settings/log/stahnout?soubor={applog.API_NAME}",
                 follow_redirects=False).status_code == 403, "čtenář nestáhne nic")

print()
print("--- rotace ---")
pred = (SLOZKA / applog.API_NAME).read_text(encoding="utf-8")
vysledek = applog.rotuj(2)
check(sorted(vysledek["otoceno"]) == sorted(applog.VLASTNI) and not vysledek["chyby"],
      f"otočí log aplikace i API ({vysledek})")
check((SLOZKA / f"{applog.API_NAME}.1").read_text(encoding="utf-8") == pred
      and (SLOZKA / applog.API_NAME).stat().st_size == 0,
      "dosavadní log je teď .1, nový začíná prázdný")
zavolej(serveru["token"], "/api/v1/summary?po=rotaci")
check("po=rotaci" in (SLOZKA / applog.API_NAME).read_text(encoding="utf-8"),
      "zapisuje se dál do nového souboru")
applog.rotuj(2)
applog.rotuj(2)
check([z["cislo"] for z in applog.zalohy(applog.API_NAME)] == [1, 2],
      "drží se jen nastavený počet starých logů")
applog.rotuj(1)
check([z["cislo"] for z in applog.zalohy(applog.API_NAME)] == [1], "menší počet smaže přebytečné")
check(spravce.get(f"/settings/log/stahnout?soubor={applog.API_NAME}.1").status_code == 200,
      "starší log jde stáhnout")
check(f"{applog.API_NAME}.1" in spravce.get("/settings?section=log&log_druh=api").text,
      "starší logy jsou v Logu ke stažení")

print()
print("--- úloha Rotace logů ---")
uloha = tasks.TASKS.get("logrotate")
check(uloha is not None and uloha.je_denni and tasks.is_enabled(uloha),
      "je mezi úlohami, denní a ve výchozím stavu zapnutá")
db.set_setting(applog.DNI_KLIC, "7")
db.set_setting(applog.POSLEDNI_KLIC, date.today().isoformat())
check(asyncio.run(uloha.runner()).get("preskoceno") is True,
      "naplánovaný běh: od poslední rotace neuběhlo 7 dní, nic se neotočí")
check(tasks.status(uloha)["dalsi_datum"] == (date.today() + timedelta(days=7)).strftime("%d.%m."),
      "Další běh ukáže den příští rotace")
db.set_setting(applog.POSLEDNI_KLIC, (date.today() - timedelta(days=7)).isoformat())
check(asyncio.run(uloha.runner()).get("status") == "ok"
      and db.get_setting(applog.POSLEDNI_KLIC) == date.today().isoformat(),
      "po 7 dnech se otočí a zapamatuje si dnešek")
pred = (SLOZKA / applog.API_NAME).read_text(encoding="utf-8")
zavolej(serveru["token"], "/api/v1/summary?rucne=1")
check(asyncio.run(tasks.run_now("logrotate")).get("status") == "ok"
      and "rucne=1" in (SLOZKA / f"{applog.API_NAME}.1").read_text(encoding="utf-8"),
      "ruční Spustit hned otočí hned, i když dnes už rotovalo")
spravce.post("/settings/tasks", data={"enabled_logrotate": "1", "logrotate_dni": "0",
                                      "logrotate_pocet": "999"})
check(db.get_setting(applog.DNI_KLIC) == "1" and db.get_setting(applog.POCET_KLIC) == "100",
      "uložení: dny i počet se oříznou do mezí")
stranka = spravce.get("/settings?section=tasks").text
check('name="logrotate_dni"' in stranka and 'name="logrotate_pocet"' in stranka,
      "v Úlohách jsou obě pole")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
