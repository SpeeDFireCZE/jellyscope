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

# CHANGELOG se láme na ~72 znacích. Každý řádek odstavce byl vlastní <p>,
# takže se v okně Nová verze odstavec rozpadl na kousky s mezerami mezi
# nimi. Řádky jednoho odstavce patří k sobě - konec je až prázdný řádek,
# nadpis nebo odrážka.
zalomeny = updates.poznamky_html(
    "Prvni radek odstavce\na jeho pokracovani.\n\nDruhy **odstavec**\nzase dva radky.\n"
    "### Nadpis\nTreti odstavec\n- odrazka\nPo odrazce\n\nCtvrty.")
check("<p>Prvni radek odstavce a jeho pokracovani.</p>" in zalomeny,
      "zalomený odstavec je jeden <p>")
check("<p>Druhy <strong>odstavec</strong> zase dva radky.</p>" in zalomeny,
      "prázdný řádek odstavce odděluje, formátování platí přes celý")
check("<h4>Nadpis</h4><p>Treti odstavec</p><ul><li>odrazka Po odrazce</li></ul><p>Ctvrty.</p>"
      in zalomeny, "nadpis i odrážka odstavec ukončí; řádek za odrážkou ji dál pokračuje")
check(zalomeny.count("<p>") == 4, f"odstavců je tolik, kolik je v textu ({zalomeny.count('<p>')})")

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
from jellyscope import web_nastaveni  # noqa: E402

cekarna = web_nastaveni._stranka_aktualizace()
check("<!doctype html>" in cekarna.lower(), "je to celá stránka")
check("{{" not in cekarna and "{%" not in cekarna,
      "žádná šablona - nic, co by starý kód nedokázal vykreslit")
check("/health" in cekarna and "started_at" in cekarna,
      "čeká na nový proces podle /health")
check("location.href" in cekarna, "a pak pustí člověka dál")

zdroj = "\n".join(p.read_text(encoding="utf-8")
         for p in sorted((PROJECT / "jellyscope").glob("web*.py")))
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

# Prenos musi zvladnout i radek tak, jak ho vraci psycopg - tedy slovnik.
# SQLite vraci `sqlite3.Row`, ze ktereho jde cist i podle poradi, takze
# `radek[0]` se pri vyvoji tvari spravne a u cloveka s PostgreSQL shodi
# cely start na "KeyError: 0". Presne to se stalo.
import sqlite3 as _sqlite3  # noqa: E402


def _prenese(radek: object) -> bool:
    """Rozhodne migrace stejne pro oba tvary radku?"""
    return radek is not None and str(dict(radek).get("value")) == "1"


class _RadekPsycopg(dict):
    """Co vrátí psycopg: slovník, kde `radek[0]` je KeyError."""


check(_prenese(_RadekPsycopg(value="1")), "slovníkový řádek se přenese")
check(not _prenese(_RadekPsycopg(value="0")), "a vypnutý zůstane vypnutý")

_spojeni = _sqlite3.connect(":memory:")
_spojeni.row_factory = _sqlite3.Row
_radek = _spojeni.execute("SELECT '1' AS value").fetchone()
check(_prenese(_radek), "a řádek ze SQLite taky")
_spojeni.close()

print()
print("--- před stažením se ověří balíčky nové verze ---")
# 1.8.1 potřebuje Python 3.11. Tlačítko v 1.8.0 udělalo `git pull` a pak
# spadlo na pip s hláškou, ze které příčina vidět nebyla. Teď se balíčky
# nové verze nejdřív zkusí nainstalovat nanečisto (pip --dry-run) a když
# to nejde, nestáhne se nic a hláška řekne proč.
VOLANI: list[tuple] = []
PIP_ZKOUSKA = {"kod": 0, "vystup": ""}
GIT = {"requirements_stejne": False}
NOVE_REQUIREMENTS = "starlette==9.0.0\n"


def podvrzeny_git(*argumenty):
    async def beh():
        VOLANI.append(("git",) + argumenty)
        if argumenty[:2] == ("diff", "--quiet") and "requirements.txt" in argumenty:
            return {"kod": 0 if GIT["requirements_stejne"] else 1, "vystup": ""}
        return {"kod": 0, "vystup": "Already up to date."}
    return beh()


def podvrzeny_obsah(*argumenty):
    async def beh():
        VOLANI.append(("git-obsah",) + argumenty)
        return {"kod": 0, "vystup": NOVE_REQUIREMENTS}
    return beh()


def podvrzeny_spust(*prikaz):
    async def beh():
        VOLANI.append(prikaz)
        if "--dry-run" in prikaz:
            return dict(PIP_ZKOUSKA)
        return {"kod": 0, "vystup": ""}
    return beh()


puvodni = (updates._git, updates._git_obsah, updates._spust)
updates._git, updates._git_obsah, updates._spust = podvrzeny_git, podvrzeny_obsah, podvrzeny_spust
db.set_setting(updates.NALEZENA_VERZE, "99.0.0")
db.forget_settings()


def stahlo() -> bool:
    return any(v[:2] == ("git", "pull") for v in VOLANI)


def instalovalo() -> bool:
    return any("pip" in v and "install" in v and "--dry-run" not in v for v in VOLANI)


def zkousel() -> bool:
    return any("--dry-run" in v for v in VOLANI)


# Python, který neexistuje (3.99): číslo se pak na stránku nemůže dostat
# jinak než dosazením z výpisu pipu.
NOVY_PYTHON = ("ERROR: Ignored the following versions that require a different python "
               "version: 8.0.0 Requires-Python >=3.8; 9.0.0 Requires-Python >=3.99\n"
               "ERROR: Could not find a version that satisfies the requirement "
               "starlette==9.0.0 (from versions: 0.1.0, 1.7.0)\n"
               "ERROR: No matching distribution found for starlette==9.0.0")
PIP_ZKOUSKA.update(kod=1, vystup=NOVY_PYTHON)
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "error" and not stahlo() and not instalovalo(),
      f"starý Python: nic se nestáhne ani nenainstaluje ({vysledek['status']})")
check(vysledek.get("parametry", {}).get("potreba") == "3.99"
      and vysledek.get("parametry", {}).get("mame") == "%d.%d" % sys.version_info[:2]
      and "Python" in vysledek.get("message", ""),
      f"hláška řekne, jaký Python je potřeba a jaký běží ({vysledek.get('parametry')})")
check(any(v[:2] == ("git", "fetch") for v in VOLANI)
      and any(v[:2] == ("git-obsah", "show") and "requirements.txt" in v[-1] for v in VOLANI),
      "balíčky se berou z requirements.txt nové verze (fetch + show)")
zkouska = next((v for v in VOLANI if "--dry-run" in v), ())
check(bool(zkouska) and zkouska[0] == sys.executable,
      "zkouší se Pythonem, na kterém aplikace běží")

# Seznam „Ignored ..." pip skládá za všechny balíčky a bez jmen. Verze
# Pythonu se bere jen u verze, která opravdu selhala, a jen když je
# novější než ten, co běží - jinak by hláška radila nesmysl.
for vystup, popis in (
        ("ERROR: Ignored the following versions that require a different python version: "
         "2.0 Requires-Python >=3.99\nERROR: No matching distribution found for starlette==9.0.0",
         "Requires-Python patří jiné verzi"),
        ("ERROR: Ignored the following versions that require a different python version: "
         "9.0.0 Requires-Python >=3.8\nERROR: No matching distribution found for starlette==9.0.0",
         "požadovaný Python není novější než běžící")):
    PIP_ZKOUSKA.update(kod=1, vystup=vystup)
    VOLANI.clear()
    vysledek = asyncio.run(updates.aktualizuj())
    check(vysledek["status"] == "error" and not stahlo()
          and "potreba" not in vysledek.get("parametry", {})
          and "vystup" in vysledek.get("parametry", {}),
          f"{popis}: obecná hláška s výpisem pipu, ne rada o Pythonu")

PIP_ZKOUSKA.update(kod=1, vystup="ERROR: No matching distribution found for nesmysl==9")
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "error" and not stahlo()
      and "nesmysl==9" in vysledek.get("parametry", {}).get("vystup", ""),
      "jiný problém s balíčky: nic se nestáhne a hláška ukáže, co řekl pip")

PIP_ZKOUSKA.update(kod=2, vystup="no such option: --dry-run")
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "ok" and stahlo() and instalovalo(),
      "starý pip bez --dry-run: zkouška se přeskočí, aktualizace proběhne jako dřív")

# Neznámá volba v NOVÉM requirements.txt není starý pip - to se nestáhne.
PIP_ZKOUSKA.update(kod=2, vystup="ERROR: Invalid requirement\nno such option: --budouci-volba")
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "error" and not stahlo(),
      "neznámá volba v novém requirements.txt: nestáhne se")

PIP_ZKOUSKA.update(kod=0, vystup="Would install starlette-9.0.0")
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "ok" and stahlo() and instalovalo(),
      "balíčky jdou nainstalovat: stáhne se a nainstaluje")
i_zkouska = next((i for i, v in enumerate(VOLANI) if "--dry-run" in v), 10 ** 6)
i_pull = next((i for i, v in enumerate(VOLANI) if v[:2] == ("git", "pull")), -1)
check(i_zkouska < i_pull, "zkouška je PŘED stažením")
ostra = next((v for v in VOLANI if "pip" in v and "--dry-run" not in v), ())
check(bool(zkouska) and [x for x in zkouska if x not in ("--dry-run",) and not str(x).endswith("requirements.txt")]
      == [x for x in ostra if not str(x).endswith("requirements.txt")],
      "zkouška i ostrá instalace volají pip stejně (jen --dry-run navíc)")

# Beze změny requirements.txt (aktualizace jen překladů) se nezkouší nic.
GIT["requirements_stejne"] = True
PIP_ZKOUSKA.update(kod=1, vystup=NOVY_PYTHON)
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "ok" and not zkousel() and stahlo(),
      "requirements.txt beze změny: žádná zkouška, rovnou stažení")
GIT["requirements_stejne"] = False

# Hláška dojde až na stránku i s čísly verzí.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

PIP_ZKOUSKA.update(kod=1, vystup=NOVY_PYTHON)
klient = TestClient(app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo"})
stranka = klient.post("/settings/update").text
check("3.99" in stranka and "python3.99-venv" in stranka
      and "%d.%d" % sys.version_info[:2] in stranka,
      "na stránce je hláška s potřebnou i běžící verzí a příkazem pro ni")

updates._git, updates._git_obsah, updates._spust = puvodni
db.set_setting(updates.NALEZENA_VERZE, "")
db.forget_settings()

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
