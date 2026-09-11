# -*- coding: utf-8 -*-
r"""Zapomenutý divák jde do koše - do noci se dá vrátit, pak je pryč.

Mazání historie diváka je nevratné a klikne se na něj snadno vedle.
Proto se řádky nemažou rovnou: **přesunou se do koše**. Ze statistik
divák zmizí okamžitě (to je to, co se po zapomenutí čeká), ale dokud
v noci koš nevysype plánovač, stačí jedno tlačítko a je zpátky.

Co se tu ověřuje:

* po zapomenutí je divák pryč ze statistik, ale v koši,
* vrácení mu vrátí všechny řádky - a zruší objednaný přepis souboru,
  aby se kvůli mazání, které se nekonalo, nezamykala databáze,
* noční vysypání je konečné: řádky zmizí i z koše,
* pak už seznam nabízí jen zálohu, a když zmizí i ta, řádek ze seznamu
  zmizí taky,
* koš má tytéž sloupce jako `playback` - jinak by se při vracení část
  dat ztratila,
* `do_kose=False` (cesta pro toho, kdo nechce čekat) maže rovnou.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_kos_zapomenuti.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
DB = Path(_tmp) / "kos.db"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(DB)
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, odklizeni, tasks, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo"})
ZALOHY = Path(_tmp) / "zalohy"
db.set_setting("backup_path", str(ZALOHY))
db.forget_settings()


def naplnit(user_id: str, jmeno: str, kolik: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (user_id, jmeno, db.utcnow()))
        for poradi in range(kolik):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, is_active) VALUES (?,?,?,?,?,?,?,?,0)",
                (f"{user_id}-{poradi}", user_id, jmeno, f"i-{poradi}",
                 f"Titul {poradi}", "2024-01-01 10:00:00",
                 "2024-01-01 10:20:00", 1200))
        conn.commit()


def v_historii(user_id: str) -> int:
    return int(db.query_value(
        "SELECT COUNT(*) FROM playback WHERE user_id = ?", (user_id,)) or 0)


def v_kosi(user_id: str) -> int:
    return int(db.query_value(
        f"SELECT COUNT(*) FROM {db.KOS} WHERE user_id = ?", (user_id,)) or 0)


def zapomen(user_id: str) -> str:
    odpoved = klient.post("/settings/historie/zapomen",
                          data={"user_id": user_id}, follow_redirects=True)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", odpoved.text))


def kam_to_vrati(cesta: str, data: dict) -> str:
    return klient.post(cesta, data=data, follow_redirects=False).headers.get(
        "location", "")


print("--- koš má tvar historie ---")
with db.connect() as conn:
    sloupce_p = db.sloupce(conn, "playback")
    sloupce_k = db.sloupce(conn, db.KOS)
check(not [s for s in sloupce_p if s not in sloupce_k],
      f"koš má všechny sloupce historie ({len(sloupce_p)})")
check("udalost" in sloupce_k, "a navíc ví, ke kterému zapomenutí patří")

print()
print("--- zapomenutí: ze statistik hned, ale do koše ---")
naplnit("u-mirek", "Mirek", 40)
text = zapomen("u-mirek")
check(v_historii("u-mirek") == 0, "v historii po něm nic není")
check(v_kosi("u-mirek") == 40, f"ale v koši je všech 40 ({v_kosi('u-mirek')})")
check("jde vrátit zpět" in text, "hláška říká, že to jde vrátit")
check(odklizeni.preskladani_ceka() != "", "a přepis souboru je objednaný na noc")
seznam = odklizeni.zapomenuti()
check(len(seznam) == 1 and seznam[0]["v_kosi"] and seznam[0]["jmeno"] == "Mirek",
      f"seznam zapomenutých ho zná ({seznam})")

print()
print("--- seznam je za tlačítkem Obnovit, ne rozsypaný v kartě ---")
stranka = klient.get("/settings?section=tasks").text
check('data-okno="okno-obnovit"' in stranka, "tlačítko Obnovit je vidět")
check('<dialog id="okno-obnovit"' in stranka, "a otevírá okno se seznamem")
i_okno = stranka.index('<dialog id="okno-obnovit"')
check(stranka.index("Mirek", i_okno) > i_okno, "jméno diváka je uvnitř okna")
sablona = (PROJECT / "jellyscope" / "templates" / "nastaveni"
           / "tasks.html").read_text(encoding="utf-8")
i_tlacitko = sablona.index('data-okno="okno-obnovit"')
check('type="button"' in sablona[i_tlacitko - 200:i_tlacitko],
      "tlačítko nic neodesílá - jen otevírá okno")

print()
print("--- stránka zůstane u karty, neskočí nahoru ---")
# Po kliknuti se stranka nacte znovu; bez kotvy by zacinala od zacatku
# a clovek by hledal, kde to byl. Hlaska je plovouci okenko nahore,
# takze o ni tim neprijde.
# Polohu drzi `data-keep-scroll` (skript v base.html si ji pri kliknuti
# zapamatuje a po nacteni vrati). Kotva v adrese je jen zachrana, kdyby
# skript nedobehl - bez ni by clovek skoncil na zacatku stranky.
# Zmereno v prohlizeci pres CDP: 1644 px pred i po zapomenuti,
# 3355 px pred i po vraceni.
sablona_karty = (PROJECT / "jellyscope" / "templates" / "nastaveni"
                 / "tasks.html").read_text(encoding="utf-8")
i_potvrzeni = sablona_karty.index('form="zapomenut"',
                                  sablona_karty.index("okno-zapomenut"))
check("data-keep-scroll" in sablona_karty[i_potvrzeni:i_potvrzeni + 200],
      "potvrzení zapomenutí drží polohu stránky")
i_vratit = sablona_karty.index("Vrátit zpět")
check("data-keep-scroll" in sablona_karty[i_vratit - 200:i_vratit],
      "a tlačítko Vrátit zpět taky")
check("[data-keep-scroll]" in (PROJECT / "jellyscope" / "templates"
                               / "base.html").read_text(encoding="utf-8"),
      "obsluha, která polohu vrací, na stránce je")
check(kam_to_vrati("/settings/historie/zapomen", {"user_id": "u-nikdo"})
      .endswith("#odklizeni"), "a bez skriptu se aspoň skočí ke kartě")
check('id="odklizeni"' in klient.get("/settings?section=tasks").text,
      "kotva na stránce je")

print()
print("--- vrácení ---")
check(kam_to_vrati("/settings/historie/vrat", {"udalost": "0"})
      .endswith("#odklizeni"), "vrácení taky")
odpoved = klient.post("/settings/historie/vrat",
                      data={"udalost": str(seznam[0]["id"])},
                      follow_redirects=True)
text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", odpoved.text))
check(v_historii("u-mirek") == 40, f"historie je zpátky ({v_historii('u-mirek')})")
check(v_kosi("u-mirek") == 0, "a koš je prázdný")
check("je zpátky" in text, "hláška to potvrzuje")
check(odklizeni.preskladani_ceka() == "",
      "objednaný přepis se zrušil - nemá co uklízet")
check(odklizeni.zapomenuti() == [], "a ze seznamu zmizel")
radek = db.query_one("SELECT item_name, watched_seconds FROM playback"
                     " WHERE user_id = 'u-mirek' ORDER BY session_key LIMIT 1")
check(dict(radek or {}).get("item_name") == "Titul 0"
      and dict(radek or {}).get("watched_seconds") == 1200,
      f"i s obsahem, ne jen počtem ({dict(radek or {})})")

print()
print("--- noční vysypání je konečné ---")
zapomen("u-mirek")
check(v_kosi("u-mirek") == 40, "znovu v koši")
stara = datetime.now(timezone.utc) - timedelta(days=2)
db.set_setting(odklizeni.ODLOZENE_KLIC, stara.strftime(db.TIME_FORMAT))
db.forget_settings()
asyncio.run(tasks._dodelej_odlozene_preskladani())
check(v_kosi("u-mirek") == 0, "koš je po noci prázdný")
check(v_historii("u-mirek") == 0, "a v historii taky nic")
seznam = odklizeni.zapomenuti()
check(len(seznam) == 1 and not seznam[0]["v_kosi"] and seznam[0]["ma_zalohu"],
      f"seznam nabízí zálohu ({seznam})")
check(seznam[0]["zaloha_soubor"].startswith("jellyscope-"),
      f"a jmenuje ji ({seznam[0]['zaloha_soubor']})")
vysledek = odklizeni.vrat_z_kose(seznam[0]["id"])
check(vysledek["vraceno"] == 0, "vrátit z prázdného koše nejde")

print()
print("--- když zmizí i záloha, řádek ze seznamu odejde ---")
for soubor in ZALOHY.glob("jellyscope-*"):
    soubor.unlink()
check(odklizeni.zapomenuti() == [], "seznam je prázdný")
check(db.query_value("SELECT COUNT(*) FROM zapomenuti") == 0,
      "a nezůstal po něm ani řádek v databázi")

print()
print("--- do_kose=False maže rovnou ---")
naplnit("u-jana", "Jana", 15)
vysledek = odklizeni.zapomen_uzivatele("u-jana")
check(vysledek["smazano"] == 15 and v_historii("u-jana") == 0, "smazáno")
check(v_kosi("u-jana") == 0, "a v koši nic - tahle cesta koš nepoužívá")
check(odklizeni.zapomenuti() == [], "ani v seznamu")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
