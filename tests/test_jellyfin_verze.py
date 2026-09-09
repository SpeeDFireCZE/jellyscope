# -*- coding: utf-8 -*-
r"""Jellyfin 12 a starší: čím se prokazujeme a kam se ptáme.

Jellyfin 12.0 přestal parsovat staré přihlašovací hlavičky
(`X-Emby-Token`, `X-Emby-Authorization`, `X-MediaBrowser-Token`), zrušil
cesty `/emby/` a `/mediabrowser/` a u `GetItems` změnil, kdy platí
`Recursive`. Platný zůstal `Authorization: MediaBrowser Token="..."` -
tedy to, čím se Jellyscope prokazuje odjakživa.

Co tenhle test hlídá:

* **Hlavička s klíčem chodí vždycky a ve správném tvaru.** Kdyby se
  rozbila, dostane uživatel 401 a bude ho hledat v API klíči, kde není.
* **Stará hlavička se posílá jen starým serverům.** Dvanáctce je k ničemu
  a klíč navíc opakuje v každém dotazu.
* **Neznámá verze se chová jako starý server.** Opačná volba by na starém
  serveru znamenala odmítnuté spojení; přebytečná hlavička na novém
  neznamená nic.
* **Ruční volba generace přebíjí zjištěnou verzi** a při jejím uložení se
  ta druhá hodnota zapomene, ať si dvě čísla neodporují.
* **Záložní cesta `/Users/{id}/Items` se u dvanáctky nezkouší** - tam je
  `/Items` ta správná a selhání znamená něco jiného.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_jellyfin_verze.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "verze.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

import httpx  # noqa: E402

from jellyscope import db, jellyfin  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


db.init_db()

print("--- čtení verze ---")
for text, ceka in (("12.0.1", (12, 0)), ("12.1", (12, 1)), ("10.10.7", (10, 10)),
                   ("", (0, 0)), ("nesmysl", (0, 0)), (None, (0, 0)),
                   ("10.11.0-rc1", (10, 11))):
    dostal = jellyfin.verze_serveru(text)
    check(dostal == ceka, f"{text!r} -> {dostal}")

print()
print("--- kdo je „starý server“ ---")
check(jellyfin.stary_server("10.10.7") is True, "desítka ano")
check(jellyfin.stary_server("12.0.1") is False, "dvanáctka ne")
check(jellyfin.stary_server("13.2") is False, "a nic novějšího taky ne")
# Neznama verze se chova jako stara: prebytecna hlavicka novemu serveru
# nevadi, kdezto chybejici by starym znamenala odmitnuty klic.
check(jellyfin.stary_server("") is True, "neznámá verze se bere jako stará")

print()
print("--- hlavičky ---")
nove = jellyfin.hlavicky("KLIC", "12.0.1")
stare = jellyfin.hlavicky("KLIC", "10.10.7")
check(nove["Authorization"] == 'MediaBrowser Token="KLIC"',
      f"moderní hlavička je vždy ({nove['Authorization']})")
check(stare["Authorization"] == nove["Authorization"],
      "a je stejná pro obě generace")
check("X-Emby-Token" not in nove, "dvanáctce se stará hlavička neposílá")
check(stare.get("X-Emby-Token") == "KLIC", "desítce ano")
check("X-Emby-Token" in jellyfin.hlavicky("KLIC", ""),
      "a při neznámé verzi taky")
# Klic nikdy v adrese - ta se loguje v proxy i v historii prohlizece.
check(all("KLIC" not in klic for klic in nove),
      "klíč je v hodnotě hlavičky, ne v jejím názvu")

print()
print("--- co server doopravdy dostane ---")
# Skutecny pozadavek pres httpx, jen s podstrcenou dopravou. Tady uz se
# netestuje funkce, ale to, co by prislo Jellyfinu.
videne: list[httpx.Request] = []


def odpovez(zadost: httpx.Request) -> httpx.Response:
    videne.append(zadost)
    return httpx.Response(200, json={"Version": "12.0.1", "ServerName": "test"})


async def zeptej_se(verze: str) -> httpx.Request:
    klient = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC", verze=verze)
    klient._client = httpx.AsyncClient(
        base_url="http://jellyfin.test",
        headers=jellyfin.hlavicky("KLIC", verze),
        transport=httpx.MockTransport(odpovez))
    async with klient:
        await klient.system_info()
    return videne[-1]


zadost = asyncio.run(zeptej_se("12.0.1"))
check(zadost.headers.get("authorization") == 'MediaBrowser Token="KLIC"',
      "dvanáctka dostane moderní hlavičku")
check("x-emby-token" not in zadost.headers, "a starou ne")
check("api_key" not in str(zadost.url) and "ApiKey" not in str(zadost.url),
      f"klíč není v adrese ({zadost.url})")
check(str(zadost.url).endswith("/System/Info"),
      f"a cesta nemá prefix /emby/ ({zadost.url})")

zadost = asyncio.run(zeptej_se("10.10.7"))
check(zadost.headers.get("x-emby-token") == "KLIC",
      "desítka dostane i tu starou")

print()
print("--- verze se zapamatuje ---")
db.set_setting(jellyfin.GENERACE_KLIC, "auto")
db.forget_settings()
asyncio.run(zeptej_se(""))
db.forget_settings()
check(db.get_setting(jellyfin.VERZE_KLIC, "") == "12.0.1",
      f"po dotazu na /System/Info ({db.get_setting(jellyfin.VERZE_KLIC, '')})")

print()
print("--- ruční volba přebíjí zjištěnou verzi ---")
db.set_setting(jellyfin.VERZE_KLIC, "10.10.7")
db.set_setting(jellyfin.GENERACE_KLIC, "12")
db.forget_settings()
klient = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC")
check(klient.verze == "12.0", f"volba 12 platí ({klient.verze})")
check("X-Emby-Token" not in jellyfin.hlavicky("KLIC", klient.verze),
      "a stará hlavička se neposílá")
asyncio.run(klient.close())

db.set_setting(jellyfin.GENERACE_KLIC, "10")
db.forget_settings()
klient = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC")
check(jellyfin.stary_server(klient.verze) is True, "volba 10 platí taky")
asyncio.run(klient.close())

# Zjistena verze je POZOROVANI serveru: zapisuje se i pri rucni volbe,
# aby bylo videt, kdyz si obe cisla odporuji. Chovani ridi volba.
db.set_setting(jellyfin.VERZE_KLIC, "")
db.forget_settings()
asyncio.run(zeptej_se(jellyfin.GENERACE["10"]))
db.forget_settings()
check(db.get_setting(jellyfin.VERZE_KLIC, "") == "12.0.1",
      f"i při ruční volbě se zapíše, co server hlásí "
      f"({db.get_setting(jellyfin.VERZE_KLIC, '')})")
klient = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC")
check(jellyfin.stary_server(klient.verze) is True,
      "ale rozhoduje pořád ruční volba (10)")
asyncio.run(klient.close())

print()
print("--- přepnutí generace: co zůstane a co se zjistí znovu ---")
# Adresa a klic jsou pro obe generace tytez - prepisovat je znovu by byla
# otrava za nic. Zjistena verze ale patri k te predchozi volbe, takze se
# zahodi a zjisti znovu; jinak by na strance zustala dve cisla, ktera si
# odporuji.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402
from jellyscope import web_nastaveni  # noqa: E402

db.set_setting("jellyfin_url", "http://jellyfin.doma:8096")
db.set_setting("jellyfin_api_key", "TAJNY-KLIC")
db.set_setting(jellyfin.VERZE_KLIC, "10.10.7")
db.set_setting(jellyfin.GENERACE_KLIC, "auto")
db.forget_settings()

accounts.create("spravce", "dlouheheslo", is_admin=True)
with TestClient(app) as www:
    www.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
             follow_redirects=False)

    def uloz(generace: str) -> None:
        # Prazdny klic ve formulari znamena "nech stavajici".
        www.post("/settings/connection", follow_redirects=False, data={
            "jellyfin_url": "http://jellyfin.doma:8096",
            "jellyfin_api_key": "",
            "jellyfin_generation": generace,
            "action": "save"})
        db.forget_settings()

    uloz("12")
    check(db.get_setting(jellyfin.GENERACE_KLIC, "") == "12", "volba se uložila")
    check(db.get_setting("jellyfin_api_key", "") == "TAJNY-KLIC",
          "API klíč zůstal")
    check(db.get_setting("jellyfin_url", "") == "http://jellyfin.doma:8096",
          "adresa zůstala")
    check(db.get_setting(jellyfin.VERZE_KLIC, "") == "",
          f"zjištěná verze se zahodila, zjistí se znovu "
          f"({db.get_setting(jellyfin.VERZE_KLIC, '')!r})")

    # Ulozeni tehoz nastaveni znovu uz nic nezahazuje - jinak by kazde
    # ulozeni adresy smazalo verzi, kterou jsme prave zjistili.
    db.set_setting(jellyfin.VERZE_KLIC, "12.0.1")
    db.forget_settings()
    uloz("12")
    check(db.get_setting(jellyfin.VERZE_KLIC, "") == "12.0.1",
          "uložení beze změny volby verzi nechá být")

    uloz("auto")
    check(db.get_setting(jellyfin.VERZE_KLIC, "") == "",
          "cesta zpátky na „zjistit\u201c ji zjistí znovu taky")
    check(db.get_setting("jellyfin_api_key", "") == "TAJNY-KLIC",
          "a klíč pořád drží")

    uloz("vymysl")
    check(db.get_setting(jellyfin.GENERACE_KLIC, "") == "auto",
          "nesmysl spadne na zjišťování")

print()
print("--- uložení připojení zjistí verzi samo ---")
# Drive se verze zjistila jen pri "Otestovat spojeni" nebo pri
# synchronizaci. Kdo prepnul generaci a dal Ulozit, koukal na prazdno
# a nevedel, jestli se neco stalo.
import jellyscope.web as web  # noqa: E402


class FalesnyKlient:
    """Jellyfin, ktery vzdycky odpovi. Podstrkuje se misto sitoveho."""

    posledni_url = ""

    def __init__(self, url, key, timeout=None, verze=None):
        FalesnyKlient.posledni_url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def system_info(self):
        db.set_setting(jellyfin.VERZE_KLIC, "12.0.1")
        return {"Version": "12.0.1", "ServerName": "falesny"}


# Routa /settings/connection bydli ve `web_nastaveni` a ma vlastni
# jmeno `JellyfinClient`. Podstrcit klienta do `web` by nic neudelalo -
# test by prosel, aniz by cokoliv zkousel.
puvodni_klient = web_nastaveni.JellyfinClient
web_nastaveni.JellyfinClient = FalesnyKlient
try:
    db.set_setting(jellyfin.VERZE_KLIC, "")
    db.set_setting(jellyfin.GENERACE_KLIC, "auto")
    db.forget_settings()
    with TestClient(app) as www:
        www.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                 follow_redirects=False)
        odpoved = www.post("/settings/connection", follow_redirects=False, data={
            "jellyfin_url": "http://jellyfin.doma:8096",
            "jellyfin_api_key": "TAJNY-KLIC",
            "jellyfin_generation": "auto",
            "action": "save"})
    db.forget_settings()
    check(odpoved.status_code == 303, "uložení přesměruje")
    check(db.get_setting(jellyfin.VERZE_KLIC, "") == "12.0.1",
          f"a verze je rovnou zjištěná ({db.get_setting(jellyfin.VERZE_KLIC, '')})")
    check(FalesnyKlient.posledni_url == "http://jellyfin.doma:8096",
          "ptalo se to uložené adresy")
finally:
    web_nastaveni.JellyfinClient = puvodni_klient


print()
print("--- když server neběží, uložení se tím nezkazí ---")
# Ulozeni nastaveni nesmi zaviset na tom, jestli je Jellyfin zrovna
# nahore. Verze proste zustane prazdna a doplni se priste.
db.set_setting(jellyfin.VERZE_KLIC, "")
db.forget_settings()
with TestClient(app) as www:
    www.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
             follow_redirects=False)
    odpoved = www.post("/settings/connection", follow_redirects=False, data={
        # Port 1 nikdo neposloucha - spojeni selze hned.
        "jellyfin_url": "http://127.0.0.1:1",
        "jellyfin_api_key": "TAJNY-KLIC",
        "jellyfin_generation": "auto",
        "action": "save"})
db.forget_settings()
check(odpoved.status_code == 303, "uložení projde i tak")
check(db.get_setting("jellyfin_url", "") == "http://127.0.0.1:1",
      "adresa se uložila")
check(db.get_setting(jellyfin.VERZE_KLIC, "") == "",
      "verze zůstala prázdná, doplní se příště")

print()
print("--- nesoulad volby a serveru se pozná ---")
# Kdyz si clovek zvoli dvanactku a server hlasi desitku, neni to chyba -
# od toho ta volba je. Ale ma to byt videt.
db.set_setting(jellyfin.GENERACE_KLIC, "12")
db.set_setting(jellyfin.VERZE_KLIC, "10.10.7")
db.forget_settings()
check(web_nastaveni._generace_nesedi() is True, "12 proti hlášené 10.10.7")
db.set_setting(jellyfin.VERZE_KLIC, "12.0.1")
db.forget_settings()
check(web_nastaveni._generace_nesedi() is False, "12 proti hlášené 12.0.1 sedí")
db.set_setting(jellyfin.GENERACE_KLIC, "auto")
db.forget_settings()
check(web_nastaveni._generace_nesedi() is False, "u zjišťování se nic neporovnává")

print()
print("--- záložní cesta jen u starých serverů ---")
zdroj = (PROJECT / "jellyscope" / "jellyfin.py").read_text(encoding="utf-8")
check(zdroj.count("not stary_server(self.verze)") == 2,
      "obě místa se ptají na verzi (iter_items i recent_items)")
check('f"/Users/{user_id}/Items" if user_id else "/Items"' in zdroj,
      "a bez uživatele se pořád ptáme na /Items")

print()
print("--- v projektu nezůstalo nic zrušeného ---")
# /emby/ a /mediabrowser/ dvanactka zrusila, api_key v adrese neparsuje.
soubory = list((PROJECT / "jellyscope").glob("*.py"))
spatne = []
for cesta in soubory:
    text = cesta.read_text(encoding="utf-8")
    for vzor in ('"/emby', "'/emby", '"/mediabrowser', "api_key=' +"):
        if vzor in text:
            spatne.append(f"{cesta.name}: {vzor}")
check(not spatne, f"žádná zrušená cesta ({spatne})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
