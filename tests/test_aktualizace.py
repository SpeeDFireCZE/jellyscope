# -*- coding: utf-8 -*-
r"""Aktualizace z prohlížeče: ukazatel, okno s popisem změn, tlačítko.

Dokud se aktualizovalo jen přes `deploy/update.sh`, byl ukazatel nové
verze odznáček odkazující na GitHub. Teď otevírá okno s popisem změn
a tlačítkem, které novou verzi stáhne a aplikaci restartuje.

Dvě věci se tu hlídají obzvlášť:

* **Popis změn je cizí text.** Přišel z GitHubu, takže projde
  escapováním dřív, než se z markdownu poskládá HTML.
* **Aktualizovat smí jen správce** a jen tam, kde je z čeho - tedy
  v instalaci staženě z gitu.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_aktualizace.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "aktualizace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, updates  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo", is_admin=False)

print("--- popis změn z markdownu ---")
html = updates.poznamky_html(
    "### Fixed\n\n"
    "- **Plakát** se nedal obnovit, viz `ImageTags`\n"
    "  a pokračování na dalším řádku.\n"
    "- Druhá odrážka\n\n"
    "Odstavec na konec.")
check("<h4>Fixed</h4>" in html, "nadpis se poskládá")
check(html.count("<li>") == 2, f"odrážky taky ({html.count('<li>')})")
check("dalším řádku" in html.split("</li>")[0],
      "pokračování odrážky zůstane v ní")
check("<strong>Plakát</strong>" in html and "<code>ImageTags</code>" in html,
      "tučné písmo a kód se přeloží")
check("<p>Odstavec na konec.</p>" in html, "a odstavec je odstavec")

print()
print("--- cizí značka se do stránky nedostane ---")
utok = updates.poznamky_html("- <img src=x onerror=alert(1)> a <b>tučně</b>")
check("<img" not in utok and "&lt;img" in utok, "značka se vypíše jako text")
check("<b>" not in utok and "&lt;b&gt;" in utok, "a to i ta neškodná")

print()
print("--- stav pro šablonu ---")
db.set_setting(updates.NALEZENA_VERZE, "99.0.0")
db.set_setting(updates.NALEZENE_POZNAMKY, "### Nové\n\n- Něco")
stav = updates.stav()
check(stav["je_novejsi"] is True, "nová verze se pozná")
check("<h4>Nové</h4>" in stav["poznamky"], "poznámky jdou do šablony jako HTML")
check(stav["lze_aktualizovat"] is True,
      "vývojová složka je git repozitář, takže aktualizovat jde")

print()
print("--- co vidí kdo ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/").text
    check('data-okno="okno-aktualizace"' in stranka,
          "ukazatel otevírá okno, ne odkaz na GitHub")
    check('id="okno-aktualizace"' in stranka, "okno je na stránce")
    check('action="/settings/update"' in stranka, "a v něm tlačítko Aktualizovat")
    check("Něco" in stranka, "i popis změn")

with TestClient(app) as client:
    client.post("/login", data={"username": "ctenar", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/").text
    check('id="okno-aktualizace"' in stranka, "čtenář okno vidí")
    check('action="/settings/update"' not in stranka,
          "ale tlačítko Aktualizovat ne")
    odpoved = client.post("/settings/update", follow_redirects=False)
    check(odpoved.status_code in (302, 303, 401, 403),
          f"a routa ho nepustí ({odpoved.status_code})")
    check(db.get_setting(updates.NALEZENA_VERZE) == "99.0.0",
          "nic se přitom nespustilo")

print()
print("--- kde aktualizovat nejde a proč ---")
# "Nejde to" bez důvodu pošle člověka hledat chybu u sebe. Každý z těch
# případů má jinou správnou odpověď, tak ji řekneme rovnou.
from jellyscope import config  # noqa: E402

os.environ["JELLYSCOPE_DOCKER"] = "1"
config.load_config(reload=True)
check(updates.lze_aktualizovat() is False, "v kontejneru se z prohlížeče neaktualizuje")
duvod = updates.duvod_bez_aktualizace()
check("docker compose" in duvod, f"a řekne se, čím místo toho ({duvod})")

import asyncio  # noqa: E402

vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "error" and "docker compose" in vysledek["message"],
      "pokus o update skončí hláškou, ne chybou")
check(updates.stav()["duvod_bez_aktualizace"] == duvod,
      "a šablona ten důvod dostane")
os.environ.pop("JELLYSCOPE_DOCKER")
config.load_config(reload=True)
check(updates.duvod_bez_aktualizace() == "", "mimo kontejner nic nebrání")

print()
print("--- čekárna po aktualizaci ---")
# Šablony se čtou ze souboru při každém požadavku, kód aplikace bydlí
# v paměti procesu. Mezi `git pull` a restartem tedy běží STARÝ KÓD nad
# NOVÝMI ŠABLONAMI - a nová šablona, která chce proměnnou, o které starý
# kód neví, spadne na Internal Server Error. Přesně to dělalo
# přesměrování na /?wait=restart.
#
# Odpověď na aktualizaci je proto stránka složená v Pythonu: žádná
# šablona, žádný kontext, nic, co by se s verzí mohlo rozejít.
from jellyscope import web  # noqa: E402

cekarna = web._stranka_aktualizace()
check("<!doctype html>" in cekarna.lower(), "je to celá stránka")
check("{{" not in cekarna and "{%" not in cekarna,
      "žádná šablona - nic, co by starý kód nedokázal vykreslit")
check("/health" in cekarna and "started_at" in cekarna,
      "čeká na nový proces podle /health")
check("location.href" in cekarna, "a pak pustí člověka dál")

zdroj = (PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8")
routa = zdroj[zdroj.index("async def settings_update"):]
routa = routa[:routa.index("def _stranka_aktualizace")]
check("wait=restart" not in routa,
      "routa už nepřesměrovává na stránku aplikace")
check("_naplanuj_restart()" in routa, "ale restart pořád naplánuje")

print()
print("--- v ukázkovém režimu se neaktualizuje ---")
# Ukázka běží z git složky, ale aktualizovat cizí instalaci by bylo
# překvapení - v demu se nesahá na nic.
os.environ["JELLYSCOPE_DEMO"] = "1"
from jellyscope import config  # noqa: E402

config.load_config(reload=True)
check(updates.lze_aktualizovat() is False, "demo aktualizovat nejde")
os.environ.pop("JELLYSCOPE_DEMO")
config.load_config(reload=True)
check(updates.lze_aktualizovat() is True, "a mimo demo zase jde")

print()
print("--- hlídání verze je úloha, ne nastavení v Obecném ---")
from jellyscope import tasks  # noqa: E402

check("updates" in tasks.TASKS, "úloha „Kontrola aktualizací“ je v rozvrhu")
uloha = tasks.TASKS["updates"]
check(uloha.je_denni and uloha.time_setting == "task_updates_time",
      "plánuje se na čas jako ostatní denní úlohy")
check(updates.ZAPNUTO == uloha.enabled_setting,
      "zapíná se týmž klíčem jako úloha - dvě místa by byla past")

# Kdo mel hlidani zapnute pod puvodnim klicem, ma ho mit zapnute i po
# aktualizaci. Jinak by mu tise prestalo fungovat a nemel by jak poznat
# proc.
with db.connect() as conn:
    conn.execute("INSERT INTO settings (key, value)"
                 " VALUES ('update_check_enabled', '1')"
                 " ON CONFLICT (key) DO UPDATE SET value = '1'")
    conn.commit()
db.forget_settings()
db.init_db()                      # to, co se stane při startu nové verze
db.forget_settings()
check(tasks.is_enabled(uloha), "zapnuté hlídání se přenese do úlohy")
check(db.get_setting("update_check_enabled", "") == "",
      "a starý klíč se uklidí, ať v databázi neplete")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
