# -*- coding: utf-8 -*-
r"""Se skrytými jmény se divák pozná jen podle náhradního id.

Stránky i API ukazují účtu se skrytými jmény místo diváka „Divák 2"
a místo jeho id `skryto-2`. Kdo ale zná skutečné id z Jellyfinu (je třeba
v adrese profilu), ten se dřív zeptal přímo na ně - `?user=<id>`,
`/users/<id>` - a hned věděl, že „Divák 2" je Petr. A nabídka diváků na
Historii rovnou vypsala skutečná jména i id. Naopak náhradní id, které
stránky samy ukazují, nefungovalo: odkaz na diváka vedl na 404.

Hlídá se tu:

* náhradní id se přeloží zpátky na diváka (stránka diváka, filtr Historie,
  `?user=` v API);
* skutečné id cizího diváka nenajde nic;
* nabídka na Historii nemá skutečná jména ani id a není podle jména
  seřazená (pořadí by jména prozradilo);
* bez skrývání jmen (správce, otevřený klíč) se nic nemění.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_anonymizace_id.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "anonym_id.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, db, pristup  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


# Id a jmena schvalne v opacnem poradi: podle id je prvni Zdena, podle
# jmena Adam. Nabidka serazena podle jmena by tak prozradila, kdo je kdo.
ZDENA, ADAM = "a1aaaaaa", "b2bbbbbb"
db.init_db()
kdy = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES (?, 'Zdena', 0, 0), (?, 'Adam', 0, 0)", (ZDENA, ADAM))
    conn.execute("INSERT INTO items (id, name, type, library_id, tech_source, is_missing)"
                 " VALUES ('i1', 'Duna', 'Movie', 'lib1', 'jellyfin', 0)")
    for k, (kdo, jmeno) in enumerate(((ZDENA, "Zdena"), (ADAM, "Adam"), (ADAM, "Adam"))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, 3600, 0, 0, 0, 'DirectPlay', 'Web')",
            (f"s{k}", kdo, jmeno, kdy, kdy))
    conn.commit()

NAHRADA = {skutecne: pristup.anonymizuj([{"user_id": skutecne}], "")[0]["user_id"]
           for skutecne in (ZDENA, ADAM)}
print("náhradní id:", NAHRADA)
db.set_setting("ctenar_anonymizace", "1")
db.forget_settings()

print("--- pravidlo ---")
ctenar_ucet = {"username": "x", "is_admin": False}
check(pristup.divak_z_adresy(NAHRADA[ADAM], ctenar_ucet) == ADAM,
      "náhradní id se přeloží zpátky na diváka")
check(pristup.divak_z_adresy(ADAM, ctenar_ucet) not in (ADAM, None, ""),
      "skutečné cizí id nevede k nikomu (ani k „všem“)")
for nesmysl in ("skryto-0", "skryto-99", "skryto-?", "skryto-1x"):
    check(pristup.divak_z_adresy(nesmysl, ctenar_ucet) not in (ADAM, ZDENA, None, ""),
          f"{nesmysl}: nevede k nikomu")
# Divak z Jellyfinu ma jmena skryta uz z vychoziho nastaveni.
divak_ucet = {"username": "adam", "is_admin": False, "jellyfin_user_id": ADAM}
check(pristup.divak_z_adresy(ADAM, divak_ucet) == ADAM,
      "vlastní skutečné id zůstává (divák o sobě ví)")
check(pristup.divak_z_adresy(ADAM, {"username": "s", "is_admin": True}) == ADAM,
      "bez skrývání jmen (správce) se id nemění")
check(pristup.divak_z_adresy(None, ctenar_ucet) is None
      and pristup.divak_z_adresy("", ctenar_ucet) == "",
      "bez id zůstává bez id")

print("--- stránky čtenáře se skrytými jmény ---")
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo")


def prihlas(jmeno: str, heslo: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
           follow_redirects=False)
    return k


ctenar = prihlas("ctenar", "ctenarheslo")
odkazy = sorted(set(re.findall(r'href="/users/([^"?/]+)', ctenar.get("/users").text)))
check(odkazy == sorted(NAHRADA.values()), f"seznam diváků odkazuje na náhradní id ({odkazy})")
for odkaz in odkazy:
    stranka = ctenar.get(f"/users/{odkaz}")
    check(stranka.status_code == 200 and "Adam" not in stranka.text and "Zdena" not in stranka.text,
          f"/users/{odkaz} se otevře ({stranka.status_code}) a jméno neukáže")
check(ctenar.get(f"/users/{ADAM}").status_code == 404, "/users/<skutečné id> nenajde nic")

historie = ctenar.get("/history").text
nabidka = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>',
                     historie.split('name="user_id"', 1)[-1].split("</select>", 1)[0])
check(nabidka and not {"Adam", "Zdena"} & {jmeno.strip() for _, jmeno in nabidka}
      and not {ADAM, ZDENA} & {hodnota for hodnota, _ in nabidka},
      f"nabídka diváků na Historii bez skutečných jmen a id ({nabidka})")
cisla = [jmeno.strip() for hodnota, jmeno in nabidka if hodnota]
check(cisla == sorted(cisla), f"nabídka je seřazená podle čísla, ne podle skutečného jména ({cisla})")


def radky_historie(text: str) -> list[str]:
    tabulka = text.split('name="user_id"', 1)[0]
    return re.findall(r'href="/users/([^"?/]+)"', tabulka)


jen_adam = ctenar.get(f"/history?user_id={NAHRADA[ADAM]}").text
check(radky_historie(jen_adam) == [NAHRADA[ADAM]] * 2,
      f"filtr Historie podle náhradního id najde jeho přehrání ({radky_historie(jen_adam)})")
check(re.search(rf'<option value="{NAHRADA[ADAM]}"\s+selected', jen_adam) is not None,
      "a v nabídce je vybraný")
skutecne = ctenar.get(f"/history?user_id={ADAM}").text
check(radky_historie(skutecne) == [] and "Adam" not in skutecne,
      f"filtr podle skutečného id nenajde nic ({radky_historie(skutecne)})")

print("--- správce (bez skrývání) ---")
spravce = prihlas("spravce", "dlouheheslo")
check(spravce.get(f"/users/{ADAM}").status_code == 200, "správce otevře diváka podle skutečného id")
check(f'<option value="{ADAM}"' in spravce.get("/history").text,
      "správce má v nabídce skutečná id")

print("--- API ---")
klic = api.vytvor("Widget", None, api.zabal_prava_klice([], True, True))["token"]
H = {"Authorization": f"Bearer {klic}"}
anonym = TestClient(app)


def kdo_v_historii(adresa: str, hlavicky: dict[str, str]) -> list[str]:
    return [p["user_id"] for p in anonym.get(adresa, headers=hlavicky).json().get("playbacks", [])]


check(kdo_v_historii(f"/api/v1/history?user={NAHRADA[ADAM]}", H) == [NAHRADA[ADAM]] * 2,
      "API ?user=<náhradní id> vrátí jeho přehrání")
check(kdo_v_historii(f"/api/v1/history?user={ADAM}", H) == [],
      "API ?user=<skutečné id> nenajde nic")
otevreny = {"Authorization": f"Bearer {api.vytvor('Grafana')['token']}"}
check(kdo_v_historii(f"/api/v1/history?user={ADAM}", otevreny) == [ADAM] * 2,
      "klíč bez skrývání jmen hledá podle skutečného id dál")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
