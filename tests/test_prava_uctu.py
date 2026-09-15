# -*- coding: utf-8 -*-
r"""Vlastní práva jednoho účtu přebijí nastavení skupiny.

Skupina (čtenářské účty, diváci z Jellyfinu) je výchozí. Když správce
u jednoho účtu zapne „vlastní", platí pro něj jen to, co je zaškrtnuté
u něj - a skupina se pro něj přestane číst úplně. Návrat ke skupině
vlastní strom zahodí.

Hlídá se tu i to, že menu ukazuje jen odkazy, které jdou otevřít, a že
rozbitý JSON u účtu člověku nezavře všechno, ale spadne na skupinu.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_prava_uctu.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prava.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, pristup, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo", is_admin=False)
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u-petr', 'PetrZQX', 0, 0)")
    conn.commit()

# Skupina ctenaru: sit vypnuta, prehled zapnuty, bez anonymizace.
db.set_setting("ctenar_vidi_sit", "0")
db.set_setting("ctenar_vidi_prehled", "1")
db.set_setting("ctenar_anonymizace", "0")
db.forget_settings()


def prihlas(jmeno: str) -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": jmeno, "password": "dlouheheslo",
                           "zpusob": "mistni"})
    return k


def stav(k: TestClient, cesta: str) -> int:
    return k.get(cesta, follow_redirects=False).status_code


def menu(k: TestClient) -> str:
    html = k.get("/insights").text
    zacatek = html.index('<nav class="nav">')
    return html[zacatek:html.index("</nav>", zacatek)]


ctenar_id = accounts.get_by_name("ctenar")["id"]

print("--- bez vlastních práv platí skupina ---")
ctenar = prihlas("ctenar")
check(stav(ctenar, "/") == 200, "přehled otevře (skupina ho má)")
check(stav(ctenar, "/network") == 403, "síť ne (skupina ji nemá)")
check('href="/network"' not in menu(ctenar), "a v menu Síť není")
check('href="/"' in menu(ctenar), "Přehled v menu je")
check('href="/settings"' not in menu(ctenar), "Nastavení v menu čtenář nemá")
check("PetrZQX" in ctenar.get("/users").text, "jména vidí (skupina neanonymizuje)")

print()
print("--- správce dá účtu vlastní práva: síť ano, přehled ne, anonymizace ---")
spravce = prihlas("spravce")
odpoved = spravce.post("/settings/accounts/prava", data={
    "account_id": str(ctenar_id), "rezim": "vlastni",
    "vidi_sit": "on", "vidi_uzivatele": "on", "vidi_zebricky": "on",
    "anonymizace": "on",
}, follow_redirects=False)
check(odpoved.status_code == 303, f"uložení prošlo ({odpoved.status_code})")
ucet = accounts.get(ctenar_id)
check(pristup.vlastni_prava(ucet) is not None, "účet má vlastní práva")

check(stav(ctenar, "/network") == 200, "síť teď otevře")
check(stav(ctenar, "/") == 403, "přehled už ne - vlastní práva ho nemají")
check('href="/network"' in menu(ctenar), "Síť se v menu objevila")
check('href="/"' not in menu(ctenar), "Přehled z menu zmizel")
seznam = ctenar.get("/users").text
check("PetrZQX" not in seznam and "Divák" in seznam,
      "jména jsou schovaná (vlastní anonymizace)")

print()
print("--- změna skupiny se účtu s vlastními právy netýká ---")
db.set_setting("ctenar_vidi_sit", "1")
db.set_setting("ctenar_vidi_prehled", "0")
db.forget_settings()
check(stav(ctenar, "/network") == 200 and stav(ctenar, "/") == 403,
      "účet se chová pořád podle svého")
jiny = accounts.create("druhy", "dlouheheslo", is_admin=False)
druhy = prihlas("druhy")
check(stav(druhy, "/") == 403 and stav(druhy, "/network") == 200,
      "zatímco účet bez vlastních práv jde se skupinou")

print()
print("--- návrat ke skupině ---")
spravce.post("/settings/accounts/prava", data={
    "account_id": str(ctenar_id), "rezim": "skupina",
    "vidi_prehled": "on",     # zaskrtnute se ignoruje - rezim je skupina
})
check(pristup.vlastni_prava(accounts.get(ctenar_id)) is None,
      "vlastní práva jsou pryč")
check(stav(ctenar, "/") == 403 and stav(ctenar, "/network") == 200,
      "a platí zase skupina (i její mezitím změněný stav)")

print()
print("--- kdo nesmí co ---")
spravce_id = accounts.get_by_name("spravce")["id"]
spravce.post("/settings/accounts/prava", data={
    "account_id": str(spravce_id), "rezim": "vlastni"})
check(pristup.vlastni_prava(accounts.get(spravce_id)) is None,
      "správci se vlastní práva nezapíšou - vidí všechno tak jako tak")
odpoved = ctenar.post("/settings/accounts/prava", data={
    "account_id": str(ctenar_id), "rezim": "vlastni", "vidi_prehled": "on"},
    follow_redirects=False)
check(odpoved.status_code == 403, f"čtenář si práva sám nenastaví ({odpoved.status_code})")
check(pristup.vlastni_prava(accounts.get(ctenar_id)) is None, "a nic se nezapsalo")

print()
print("--- rozbitý záznam = skupina, ne zavřené dveře ---")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET pristup = '{nesmysl' WHERE id = ?",
                 (ctenar_id,))
    conn.commit()
check(pristup.vlastni_prava(accounts.get(ctenar_id)) is None,
      "rozbitý JSON se bere jako „žádná vlastní práva“")
check(stav(ctenar, "/network") == 200, "a člověk se chová podle skupiny")

print()
print("--- strom v Nastavení ukazuje, co pro účet platí ---")
accounts.uloz_prava(ctenar_id, pristup.zabal_prava(["jazyky"], False))
stranka = spravce.get("/settings?section=accounts").text
check(f'id="okno-prava-{ctenar_id}"' in stranka, "účet má okno s právy")
check(f'id="okno-prava-{spravce_id}"' not in stranka, "správce ne")
check("vlastní práva" in stranka, "u účtu svítí odznak „vlastní práva“")
zacatek = stranka.index(f'id="okno-prava-{ctenar_id}"')
okno = stranka[zacatek:stranka.index("</dialog>", zacatek)]


def zaskrtnuto(html: str, jmeno: str) -> bool:
    """Je zaškrtávátko s tímhle jménem zaškrtnuté?"""
    shoda = re.search(r'<input[^>]*name="' + jmeno + r'"[^>]*>', html)
    return bool(shoda and "checked" in shoda.group(0))


check(zaskrtnuto(okno, "vidi_jazyky"), "zaškrtnuté je to, co má účet vlastní")
check(not zaskrtnuto(okno, "vidi_sit"), "a nic z toho, co skupina má a účet ne")
check(bool(re.search(r'<input[^>]*value="vlastni"[^>]*checked', okno)),
      "přepínač stojí na „vlastní“")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
