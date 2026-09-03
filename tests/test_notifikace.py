# -*- coding: utf-8 -*-
r"""Upozornění: kanály, události a to, aby se z hlídače nestal budík.

Jellyscope je pasivní. Sběrač se ptá Jellyfinu každých pár vteřin, a když
mu vyprší token, aplikace běží dál a historie se **tiše** zastaví –
zjistíš to za tři týdny podle díry v grafu. Kvůli tomu tahle věc vznikla.

Čtyři místa, kde se to dá udělat špatně, a proto se měří:

* **Zpráva jen při změně.** Kdyby chodila při každém běhu úlohy, byl by
  z hlídače budík po patnácti minutách a vyplo by se to hned první den.
  A když se to spraví, musí přijít i zpráva o tom – bez ní člověk neví,
  jestli má ještě něco řešit.
* **Selhání jednoho kanálu nesmí shodit ostatní.** Když nefunguje SMTP,
  zpráva má pořád dojít na Discord.
* **Tajemství se nesmí dostat do stránky.** Heslo, webhook a token jsou
  přístupové údaje jako klíč k Jellyfinu.
* **Prázdné pole hesla znamená „nech, jak bylo".** Uložená hodnota se do
  formuláře nevypisuje, takže by ji jinak každé uložení smazalo.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_notifikace.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "notifikace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import applog, collector, db, notifikace  # noqa: E402

# Prahy loggeru nastavuje applog - bez nej by kontrola nize merila
# vychozi stav Pythonu, ne to, co plati v aplikaci.
applog.setup()

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


db.init_db()

# Odeslane zpravy se misto na sit sbiraji sem.
odeslane: list[tuple[str, str, str]] = []
selze: set[str] = set()


def _falesny(kanal: str):
    async def posli(predmet: str, text: str) -> None:
        if kanal in selze:
            raise RuntimeError(f"{kanal} nefunguje")
        odeslane.append((kanal, predmet, text))
    return posli


notifikace.ODESILATELE = {k: _falesny(k) for k in notifikace.KANALY}


def nastav_kanal(kanal: str) -> None:
    db.set_setting(notifikace.klic(kanal, "enabled"), "1")
    if kanal == "smtp":
        db.set_setting(notifikace.klic("smtp", "host"), "smtp.doma.cz")
        db.set_setting(notifikace.klic("smtp", "komu"), "ja@doma.cz")
    elif kanal == "discord":
        db.set_setting(notifikace.klic("discord", "webhook"),
                       "https://discord.com/api/webhooks/1/tajne")
    elif kanal == "telegram":
        db.set_setting(notifikace.klic("telegram", "token"), "1:tajne")
        db.set_setting(notifikace.klic("telegram", "chat"), "42")
    db.forget_settings()


print("--- kanál se pozná podle toho, co je vyplněné ---")
check(not notifikace.zapnute_kanaly(), "bez nastavení není kudy poslat")
db.set_setting(notifikace.klic("discord", "enabled"), "1")
db.forget_settings()
check(not notifikace.zapnute_kanaly(),
      "zapnutý, ale nevyplněný kanál se nepočítá")
nastav_kanal("discord")
check(notifikace.zapnute_kanaly() == ["discord"], "vyplněný ano")

print()
print("--- selhání jednoho kanálu neshodí ostatní ---")
nastav_kanal("smtp")
selze.add("smtp")
odeslane.clear()
vysledky = asyncio.run(notifikace.posli("předmět", "text"))
check(len(vysledky) == 2, f"zkusily se oba kanály ({len(vysledky)})")
check([v["kanal"] for v in vysledky if v["ok"]] == ["discord"],
      "Discord prošel, i když SMTP selhalo")
check(any(v["chyba"] for v in vysledky if not v["ok"]),
      "a u toho selhaného je i důvod")
check(db.get_setting("notify_last_error", "").startswith("smtp:"),
      "důvod se uloží, aby ho nastavení ukázalo")
selze.clear()

print()
print("--- sběrač: hlásí se jen změna ---")
db.set_setting(notifikace.klic_udalosti("sberac"), "1")
db.forget_settings()

TED = datetime.now(timezone.utc).replace(tzinfo=None)


def posledni_dotaz(pred_minutami: float) -> None:
    db.set_setting(collector.STATUS_KEY, "ok")
    db.set_setting(collector.ERROR_KEY, "")
    db.set_setting(collector.LAST_POLL_KEY,
                   (TED - timedelta(minutes=pred_minutami)).strftime(notifikace.CAS_FORMAT))
    db.forget_settings()


posledni_dotaz(1)
odeslane.clear()
asyncio.run(notifikace.zkontroluj())
check(not odeslane, "když sběrač sbírá, nic se neposílá")

posledni_dotaz(120)                 # dvě hodiny ticha
odeslane.clear()
asyncio.run(notifikace.zkontroluj())
check(len(odeslane) == 2, f"po dlouhém tichu přijde zpráva ({len(odeslane)}x)")
check("120" in odeslane[0][2] or "119" in odeslane[0][2],
      f"a je v ní, jak dlouho se mlčí ({odeslane[0][2]!r})")

# Tohle je ta hlavni past: druhy beh uz posilat NESMI.
odeslane.clear()
asyncio.run(notifikace.zkontroluj())
check(not odeslane, "podruhé se totéž neposílá - jinak by z hlídače byl budík")

posledni_dotaz(1)
odeslane.clear()
asyncio.run(notifikace.zkontroluj())
check(len(odeslane) == 2, "a když se to spraví, přijde zpráva o tom")
check("běží" in odeslane[0][1] or "running" in odeslane[0][1],
      f"která to říká ({odeslane[0][1]!r})")

odeslane.clear()
asyncio.run(notifikace.zkontroluj())
check(not odeslane, "a dál už je zase ticho")

print()
print("--- vypnutá událost se neřeší ---")
db.set_setting(notifikace.klic_udalosti("sberac"), "0")
db.forget_settings()
posledni_dotaz(500)
odeslane.clear()
asyncio.run(notifikace.zkontroluj())
check(not odeslane, "vypnuté upozornění mlčí, i když je zle")
db.set_setting(notifikace.klic_udalosti("sberac"), "1")
db.set_setting(notifikace._stav_klic("sberac"), "ok")
db.forget_settings()

print()
print("--- ukázkový režim není porucha ---")
db.set_setting(collector.STATUS_KEY, "demo")
db.forget_settings()
spatne, _proc = notifikace._sberac_nesbira()
check(not spatne, "v ukázkovém režimu se nesbírá schválně")
posledni_dotaz(1)

print()
print("--- týdenní souhrn: jednou týdně, ne pořád ---")
db.set_setting(notifikace.klic_udalosti("souhrn"), "1")
db.set_setting("notify_souhrn_den", "0")        # pondělí
db.set_setting("notify_souhrn_cas", "09:00")
db.set_setting("notify_souhrn_odeslan", "")
db.forget_settings()

pondeli = datetime(2026, 9, 7, 10, 0)           # pondělí, po deváté
check(pondeli.weekday() == 0, "kontrolní datum je pondělí")
odeslane.clear()
asyncio.run(notifikace.zkontroluj(ted=pondeli))
check(any("souhrn" in p.lower() or "summary" in p.lower() for _k, p, _t in odeslane),
      f"v pondělí ráno souhrn přijde ({[p for _k, p, _t in odeslane]})")

odeslane.clear()
asyncio.run(notifikace.zkontroluj(ted=pondeli.replace(hour=18)))
check(not odeslane, "podruhé tentýž den už ne")

odeslane.clear()
asyncio.run(notifikace.zkontroluj(ted=datetime(2026, 9, 8, 10, 0)))
check(not odeslane, "a v úterý taky ne")

# Pred cerstvym pondelim se posle znovu.
odeslane.clear()
asyncio.run(notifikace.zkontroluj(ted=datetime(2026, 9, 14, 10, 0)))
check(bool(odeslane), "další pondělí ano")
db.set_setting(notifikace.klic_udalosti("souhrn"), "0")
db.forget_settings()

print()
print("--- tajemství neuniknou v textu chyby ---")
# Skutecny nalez z bezpecnostni kontroly: chyba od HTTP klienta nese
# v sobe CELOU adresu. U Telegramu je v adrese token bota, u Discordu je
# adresa webhooku sama tajemstvim - a ta hlaska se UKLADA a UKAZUJE
# v nastaveni, takze token svitil na obrazovce.
TOKEN = "SUPER-TAJNY-TOKEN-12345"
db.set_setting(notifikace.klic("telegram", "token"), f"999:{TOKEN}")
db.set_setting(notifikace.klic("discord", "webhook"),
               f"https://discord.com/api/webhooks/1/{TOKEN}")
db.forget_settings()

hlaska = (f"Client error '401 Unauthorized' for url "
          f"'https://api.telegram.org/bot999:{TOKEN}/sendMessage'")
check(TOKEN not in notifikace.bez_tajemstvi(hlaska),
      f"token z adresy se vymaskuje ({notifikace.bez_tajemstvi(hlaska)[-46:]})")
check("401 Unauthorized" in notifikace.bez_tajemstvi(hlaska),
      "ale zbytek hlášky zůstane - jinak by nebyla k ničemu")
check(TOKEN not in notifikace.bez_tajemstvi(
          f"connect to https://discord.com/api/webhooks/1/{TOKEN} failed"),
      "a webhook Discordu taky")

# A totez pri skutecnem selhani, ne jen v pomocne funkci.
selze.add("telegram")
db.set_setting(notifikace.klic("telegram", "enabled"), "1")
db.set_setting(notifikace.klic("telegram", "chat"), "42")
db.forget_settings()


async def _selze_s_adresou(predmet: str, text: str) -> None:
    raise RuntimeError(hlaska)


puvodni = notifikace.ODESILATELE["telegram"]
notifikace.ODESILATELE["telegram"] = _selze_s_adresou
asyncio.run(notifikace.posli("p", "t", jen_kanal="telegram"))
notifikace.ODESILATELE["telegram"] = puvodni
selze.clear()
check(TOKEN not in db.get_setting("notify_last_error", ""),
      f"uložená hláška token neobsahuje ({db.get_setting('notify_last_error', '')[-40:]})")

# HTTP klient loguje kazdy pozadavek vcetne adresy. Za bezneho provozu se
# to zahodi, ale staci spustit server s --log-level debug.
import logging  # noqa: E402

for jmeno in ("httpx", "httpcore"):
    check(logging.getLogger(jmeno).level >= logging.WARNING,
          f"logger {jmeno} má práh aspoň WARNING"
          f" ({logging.getLevelName(logging.getLogger(jmeno).level)})")

print()
print("--- zmínky v Discordu se nevyhodnocují ---")
# Do zpravy jdou nazvy titulu z knihovny. Film pojmenovany "@everyone"
# je jmeno souboru, ne pokyn pingnout cely kanal.
zdrojak = (PROJECT / "jellyscope" / "notifikace.py").read_text(encoding="utf-8")
check('"allowed_mentions": {"parse": []}' in zdrojak,
      "zpráva na Discord zmínky vypíná")

print()
print("--- tajemství se do stránky nedostanou ---")
verejne = db.get_public_settings()
tajne = [k for k in notifikace.TAJNA if k in verejne]
check(not tajne, f"heslo, webhook ani token nejsou ve veřejném nastavení: {tajne}")
check(db.get_setting(notifikace.klic("discord", "webhook"), "") != "",
      "ale uložené jsou")

print()
print("--- stránka a ukládání ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/settings?section=notifications")
    check(stranka.status_code == 200, "sekce Upozornění se načte")
    html = stranka.text
    check("tajne" not in html,
          "a uložený webhook se do ní nevypíše ani omylem")
    for pole in ("smtp_host", "discord_webhook", "telegram_token",
                 "event_sberac", "event_misto", "event_souhrn"):
        check(f'name="{pole}"' in html, f"formulář má pole {pole}")

    # Prazdne heslo nesmi to ulozene smazat.
    db.set_setting(notifikace.klic("smtp", "heslo"), "puvodni")
    db.forget_settings()
    client.post("/settings/notifications", follow_redirects=False, data={
        "smtp_enabled": "1", "smtp_host": "jiny.server", "smtp_port": "465",
        "smtp_komu": "ja@doma.cz", "smtp_heslo": ""})
    db.forget_settings()
    check(db.get_setting(notifikace.klic("smtp", "heslo"), "") == "puvodni",
          "prázdné pole hesla ho nechá být")
    check(db.get_setting(notifikace.klic("smtp", "host"), "") == "jiny.server",
          "a ostatní pole se uloží")

    client.post("/settings/notifications", follow_redirects=False, data={
        "smtp_enabled": "1", "smtp_host": "jiny.server", "smtp_komu": "ja@doma.cz",
        "smtp_heslo": "nove"})
    db.forget_settings()
    check(db.get_setting(notifikace.klic("smtp", "heslo"), "") == "nove",
          "vyplněné pole ho přepíše")

    # Nesmyslny kanal se do odesilani nedostane.
    odeslane.clear()
    client.post("/settings/notifications/test", data={"kanal": "../../etc"},
                follow_redirects=False)
    check(not odeslane, "neznámý kanál se neposílá")

    odeslane.clear()
    client.post("/settings/notifications/test", data={"kanal": "discord"},
                follow_redirects=False)
    check(len(odeslane) == 1 and odeslane[0][0] == "discord",
          f"zkušební zpráva jde jen tím jedním kanálem ({odeslane})")

print()
print("--- úloha ---")
from jellyscope import tasks  # noqa: E402

check("notifikace" in tasks.TASKS, "kontrola má vlastní úlohu")
check(not tasks.TASKS["notifikace"].je_denni,
      "a je intervalová - porucha se má vědět dneska, ne zítra ráno")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
