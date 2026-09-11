# -*- coding: utf-8 -*-
r"""Hlášky v Upozorněních mají co říct - a nejsou prázdné.

Karty *Kudy posílat* a *Na co se ozvat* ukládaly hlášku do relace jako
holý text, jenže stránka z ní čte `flash.message` a `flash.level`.
Výsledek: po uložení vyskočilo prázdné okénko bez jediného slova, takže
to vypadalo jako chyba bez vysvětlení.

Hlášky proto musí chodit přes `_flash()` - ten uloží text i úroveň
a překlad udělá hned, ne až v šabloně.

K tomu jedna past navíc: zapnutý kanál s nevyplněnými údaji se uložil
mlčky. Člověk pak čeká upozornění, která nemají kudy odejít. Dneska to
uložení řekne.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_hlasky_upozorneni.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hlasky.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

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


def okenko(cesta: str, data: dict) -> tuple[str, str]:
    """(úroveň, text) hlášky po odeslání formuláře."""
    odpoved = klient.post(cesta, data=data, follow_redirects=True)
    shoda = re.search(r'class="flash ([^"]*)">.*?<span>(.*?)</span>',
                      odpoved.text, re.S)
    if not shoda:
        return ("", "")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", shoda.group(2))).strip()
    return (shoda.group(1).strip(), text)


print("--- prázdné uložení řekne, co se stalo ---")
uroven, text = okenko("/settings/notifications", {})
check(bool(text), f"kudy posílat: okénko není prázdné ({text!r})")
check(uroven == "success", f"a je to potvrzení, ne chyba ({uroven!r})")

uroven, text = okenko("/settings/notifications/events", {})
check(bool(text), f"na co se ozvat: okénko není prázdné ({text!r})")
check(uroven == "success", f"a je to potvrzení ({uroven!r})")

print()
print("--- zapnutý a nevyplněný kanál se neuloží mlčky ---")
uroven, text = okenko("/settings/notifications",
                      {"smtp_enabled": "on", "telegram_enabled": "on"})
check(uroven == "warning", f"je to upozornění ({uroven!r})")
check("E-mail" in text and "Telegram" in text,
      f"a jmenuje, co je nedodělané ({text!r})")
check("smtp" not in text.lower(),
      "kanálu se říká jménem z nastavení, ne názvem protokolu")

uroven, text = okenko("/settings/notifications", {
    "discord_enabled": "on",
    "discord_webhook": "https://discord.com/api/webhooks/1/x"})
check(uroven == "success", f"vyplněný kanál je v pořádku ({uroven!r}, {text!r})")

print()
print("--- zkušební zpráva ---")
uroven, text = okenko("/settings/notifications/test", {"kanal": "nesmysl"})
check(uroven == "error" and bool(text), f"neznámý kanál = chyba ({text!r})")
uroven, text = okenko("/settings/notifications/test", {"kanal": "smtp"})
check(uroven == "error" and "Nepodařilo" in text,
      f"nevyplněný kanál řekne proč ({text!r})")

print()
print("--- a nikde se neukládá holý text ---")
# Presne to bylo pricinou prazdneho okenka: session["flash"] = "text".
zdroj = "".join(
    (PROJECT / "jellyscope" / jmeno).read_text(encoding="utf-8")
    for jmeno in ("web.py", "web_nastaveni.py"))
check('session["flash"] =' not in zdroj,
      "hlášky chodí přes _flash(), ne rovnou do relace")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
