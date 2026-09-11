# -*- coding: utf-8 -*-
r"""Čtecí API: pustí dovnitř jen token a nic tudy nejde změnit.

API je jediná cesta do Jellyscope, která nevede přes přihlášení. Proto
tenhle test hlídá hlavně to, co se stát NESMÍ:

* **Bez tokenu se nedozví nikdo nic.** Ani „kolik je uživatelů", ani jak
  se jmenuje knihovna.
* **Token se nepředává v adrese.** Adresa se objeví v logu proxy,
  v historii prohlížeče a v odkazu, který někdo pošle dál - a token by
  s ní. Kdo ho zkusí poslat jako `?token=`, dostane 401 jako každý jiný.
* **V databázi je otisk, ne token.** Kdo si přečte databázi (třeba
  ze zálohy), přístup tím nezíská.
* **Nic se přes API nemění.** Pod `/api/` jsou samá GET; POST musí
  skončit na 405, ne na „metoda se ignoruje a stránka se načte".
* **Zneplatněný token přestane platit hned**, a ostatních se to nedotkne.

Spuštění:
    .\\.venv\\Scripts\\python.exe tests\\test_api.py
"""
from __future__ import annotations

import os
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import api, db  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


db.init_db()

# Trocha dat, at maji odpovedi co ukazat.
zacatek = datetime.now(timezone.utc) - timedelta(days=2)
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, tech_source, is_missing)"
        " VALUES ('i1', 'Duna', 'Movie', ?, 'jellyfin', 0)", (1024 ** 3,))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id,"
        " item_name, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method)"
        " VALUES ('s1','u1','Jana','i1','Duna',?,?,3600,0,0,1,'Transcode (v)')",
        (zacatek.strftime(db.TIME_FORMAT), zacatek.strftime(db.TIME_FORMAT)))
    conn.commit()

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

klient = TestClient(app)

print("--- bez tokenu se nedozví nikdo nic ---")
for cesta in ("/api/v1/", "/api/v1/summary", "/api/v1/now-playing",
              "/api/v1/library"):
    odpoved = klient.get(cesta)
    check(odpoved.status_code == 401, f"{cesta} -> 401 ({odpoved.status_code})")
check("Bearer" in klient.get("/api/v1/summary").headers.get("www-authenticate", ""),
      "a odpověď řekne, čím se prokázat")

print()
print("--- s tokenem ano ---")
vyrobeny = api.vytvor("Grafana")
TOKEN = vyrobeny["token"]
hlavicka = {"Authorization": f"Bearer {TOKEN}"}

odpoved = klient.get("/api/v1/summary?days=30", headers=hlavicka)
check(odpoved.status_code == 200, f"souhrn projde ({odpoved.status_code})")
data = odpoved.json()
check(data["plays"] == 1, f"a nese čísla ({data['plays']} přehrávání)")
check(data["watched_hours"] == 1.0, f"v hodinách ({data['watched_hours']})")
check(data["active_now"] == 1, "i počet právě běžících")
check(data["transcode_share_percent"] == 100.0,
      f"a podíl přepočtů ({data['transcode_share_percent']} %)")

hraje = klient.get("/api/v1/now-playing", headers=hlavicka).json()
check(hraje["count"] == 1, f"právě se hraje: {hraje['count']}")
check(hraje["sessions"][0]["user"] == "Jana", "se jménem diváka")
check(hraje["sessions"][0]["is_transcode"] is True, "a poznáním přepočtu")

knihovna = klient.get("/api/v1/library", headers=hlavicka).json()
check(knihovna["items"] == 1 and knihovna["size_bytes"] == 1024 ** 3,
      f"knihovna: {knihovna['items']} položek, {knihovna['size_human']}")

rozcestnik = klient.get("/api/v1/", headers=hlavicka).json()
check("/api/v1/summary" in rozcestnik["endpoints"], "rozcestník vypíše adresy")

print()
print("--- token patří do hlavičky, ne do adresy ---")
# Adresa se objevi v logu proxy i v historii prohlizece. Kdyby API token
# z adresy prijalo, bylo by to nejpohodlnejsi a nejhorsi reseni zaroven.
check(klient.get(f"/api/v1/summary?token={TOKEN}").status_code == 401,
      "token v adrese neplatí")
check(klient.get("/api/v1/summary",
                 headers={"Authorization": TOKEN}).status_code == 401,
      "ani bez slova Bearer")
check(klient.get("/api/v1/summary",
                 headers={"Authorization": "Bearer js_takovy_neexistuje"}).status_code == 401,
      "vymyšlený token taky ne")

print()
print("--- v databázi je otisk, ne token ---")
ulozene = db.query_all("SELECT token_hash, ukazka FROM api_tokens")
check(all(TOKEN not in str(r["token_hash"]) for r in ulozene),
      "token samotný v databázi není")
check(ulozene[0]["ukazka"] == TOKEN[:5],
      f"jen prvních pět znaků ({ulozene[0]['ukazka']})")
check(len(ulozene[0]["ukazka"]) == api.UKAZKA_ZNAKU,
      f"a ani o znak víc ({len(ulozene[0]['ukazka'])})")
check(len(TOKEN) > 40, f"a je dost dlouhý na to, aby se nedal uhodnout ({len(TOKEN)})")

print()
print("--- přes API nejde nic změnit ---")
for cesta in ("/api/v1/summary", "/api/v1/library", "/api/v1/now-playing"):
    odpoved = klient.post(cesta, headers=hlavicka, json={"neco": 1})
    check(odpoved.status_code == 405,
          f"POST {cesta} -> 405 ({odpoved.status_code})")
    odpoved = klient.delete(cesta, headers=hlavicka)
    check(odpoved.status_code == 405,
          f"DELETE {cesta} -> 405 ({odpoved.status_code})")

print()
print("--- naposledy použit ---")
radek = db.query_one("SELECT last_used_at FROM api_tokens LIMIT 1")
check(bool(radek["last_used_at"]), "použití se zapsalo")

print()
print("--- zneplatnění platí hned a jen pro ten jeden ---")
druhy = api.vytvor("Homepage")
check(len(api.seznam()) == 2, f"tokeny jsou dva ({len(api.seznam())})")

zruseny = api.zrus(db.query_value("SELECT id FROM api_tokens WHERE name = 'Grafana'"))
check(zruseny == "Grafana", f"zrušil se ten správný ({zruseny})")
check(klient.get("/api/v1/summary", headers=hlavicka).status_code == 401,
      "zrušený token přestal platit")
check(klient.get("/api/v1/summary",
                 headers={"Authorization": f"Bearer {druhy['token']}"}
                 ).status_code == 200,
      "druhý token platí dál")

print()
print("--- nesmysl v ?days= nesmí nic rozbít ---")
# `days` prichazi z adresy, tedy od kohokoliv, a konci v dotazu do
# databaze. Nemusi to byt cislo a nemusi to byt rozumne cislo: prazdno,
# pismena, zaporna hodnota, kus SQL. Vsechno se ma tise srovnat na
# rozumny pocet dnu - ne spadnout a ne odejit do dotazu.
for zlobivy in ("999999999999999999999", "-1", "abc", "", "1;DROP TABLE playback",
                "1 OR 1=1", "0", "1e400", "../../etc/passwd", "NaN"):
    odpoved = klient.get(f"/api/v1/summary?days={zlobivy}",
                         headers={"Authorization": f"Bearer {druhy['token']}"})
    check(odpoved.status_code == 200,
          f"days={zlobivy[:24]!r} projde ({odpoved.status_code})")
    if odpoved.status_code == 200:
        dnu = odpoved.json().get("days")
        check(isinstance(dnu, int) and 1 <= dnu <= 3650,
              f"days={zlobivy[:24]!r} se srovnalo na {dnu}")

# Tabulka, kterou se utok pokousel zahodit, tam porad je.
check(db.query_value("SELECT COUNT(*) FROM playback") is not None,
      "a tabulka playback útok přežila")

print()
print("--- klíče spravuje jen správce, a to ve své sekci ---")
import re  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo", is_admin=False)

with TestClient(app) as ctenar:
    ctenar.post("/login", data={"username": "ctenar", "password": "dlouheheslo"},
                follow_redirects=False)
    # Nastaveni je jen pro spravce, takze se ctenar nedostane ani
    # k sekci API - a o klicich se nedozvi nic.
    stranka = ctenar.get("/settings?section=api")
    check(stranka.status_code == 403, "čtenář se do Nastavení nedostane")
    check("okno-api-dokumentace" not in stranka.text,
          "a sekci API nevidí")
    check("Vyrobit klíč" not in stranka.text, "ani tlačítko na výrobu klíče")
    odpoved = ctenar.post("/settings/api/token", data={"name": "podvrh"},
                          follow_redirects=False)
    check(odpoved.status_code == 403, f"a vyrobit klíč nesmí ({odpoved.status_code})")

with TestClient(app) as spravce:
    spravce.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                 follow_redirects=False)
    stranka = spravce.get("/settings?section=api")
    check(stranka.status_code == 200, "správce sekci API vidí")
    check("okno-api-dokumentace" in stranka.text,
          "a je v ní tlačítko s dokumentací")
    check("/api/v1/summary" in stranka.text, "která vypisuje adresy")

    pred = len(api.seznam())
    odpoved = spravce.post("/settings/api/token", data={"name": "Ze stránky"},
                           follow_redirects=False)
    check(odpoved.status_code == 303, "vyrobení přesměruje")
    check(len(api.seznam()) == pred + 1, "a klíč přibyl")

    # Klic se ukazuje JEDNOU. Tohle je ta nejdulezitejsi vlastnost cele
    # obrazovky: podruhe uz ho stranka nema odkud vzit, protoze ulozeny
    # je jen otisk.
    stranka = spravce.get("/settings?section=api").text
    cely = re.findall(r"js_[A-Za-z0-9_-]{20,}", stranka)
    check(len(cely) == 1, f"klíč se jednou ukáže celý ({len(cely)}x)")

    znovu = spravce.get("/settings?section=api").text
    check(not re.findall(r"js_[A-Za-z0-9_-]{20,}", znovu),
          "při dalším načtení už tam celý není")
    check(cely[0][:5] in znovu, "zbyde z něj prvních pět znaků")
    check("*" * 24 in znovu, "a zbytek jsou hvězdičky")

    # A jinde na strankach uz vubec.
    for cesta in ("/settings?section=accounts", "/settings?section=general", "/"):
        check(not re.findall(r"js_[A-Za-z0-9_-]{20,}", spravce.get(cesta).text),
              f"celý klíč není ani na {cesta}")

    # Zneplatneni ze stranky.
    posledni = api.seznam()[0]
    odpoved = spravce.post("/settings/api/token/zrusit",
                           data={"token_id": posledni["id"]},
                           follow_redirects=False)
    check(odpoved.status_code == 303, "zneplatnění přesměruje")
    check(all(r["id"] != posledni["id"] for r in api.seznam()),
          "a klíč je pryč ze seznamu")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
