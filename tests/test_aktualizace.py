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
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
