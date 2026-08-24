# -*- coding: utf-8 -*-
r"""Čekárna po restartu pozná, že se proces vyměnil.

Tohle už jednou nefungovalo a stálo to člověka zaseknutou stránku:
čekárna si výchozí stav zapisovala až z **první odpovědi** /health. Jenže
restart přijde do vteřiny a první dotaz až za dvě - takže ta odpověď už
patřila novému procesu, čekárna si ho zapsala jako "od čeho čekám"
a čekala na změnu, která už nikdy nepřišla. Aplikace přitom běžela.

Od čeho se čeká, proto musí být zapsané ve stránce ze serveru - stejný
důvod, proč tam je `tasks_version` u čekání na úlohu.

Signály jsou dva a každý kryje slabinu toho druhého:

* `version` je přímo ta otázka ("běží už nová verze?"), ale hlásí se jen
  přihlášenému,
* `started_at` odpoví komukoliv, jen říká míň ("něco se restartovalo").

Spuštění:
    .\.venv\Scripts\python.exe tests\test_cekarna.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cekarna.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

client = TestClient(web.app)

print("--- /health: verze jen přihlášenému ---")
verejne = client.get("/health").json()
check("started_at" in verejne, "start procesu hlásí každému")
check("version" not in verejne, "verzi ne")

client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
            follow_redirects=False)
prihlasene = client.get("/health").json()
check(prihlasene.get("version") == web.__version__,
      f"přihlášenému ano ({prihlasene.get('version')})")

print()
print("--- čekárna po aktualizaci ---")
stranka = web._stranka_aktualizace()
check(f"var puvodni = {int(web.STARTED_AT)};" in stranka,
      "start procesu je zapsaný ve stránce")
check(f'var verze = "{web.__version__}";' in stranka, "a verze taky")
# Tohle je ta chyba, kvůli které test vznikl: výchozí stav se NESMÍ brát
# z odpovědi, která už může patřit novému procesu.
check("puvodni === null" not in stranka,
      "výchozí stav se nebere z první odpovědi")
check(stranka.count("location.href") == 2,
      "pustí dál při změně startu i verze")
# Kdyby se proces nezvedl vůbec, stránka to po pár minutách řekne -
# místo aby se točila donekonečna.
check('id="pozn"' in stranka, "a když se nezvedne, ozve se")

print()
print("--- čekárna po ručním restartu ---")
odpoved = client.get("/settings?section=general&wait=restart")
html = odpoved.text
nalez = re.search(r'data-beh-od="(\d+)"', html)
check(nalez is not None, "stránka nese start procesu")
if nalez:
    check(int(nalez.group(1)) == int(web.STARTED_AT),
          f"a je to ten běžící ({nalez.group(1)})")
check('data-wait-for="restart"' in html, "a ví, na co čeká")

# Bez `?wait=` se nečeká na nic, takže tam ta značka nemá co dělat.
bez_cekani = client.get("/settings?section=general").text
check("data-wait-for" not in bez_cekani, "bez čekání se nic nehlídá")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
