# -*- coding: utf-8 -*-
"""Bezpečnostní kontroly, které se nesmí ztratit.

Každá z nich odpovídá díře, která v aplikaci opravdu byla:

1. **Podpisový klíč relací.** Když se nevyplnil `SECRET_KEY`, používala
   se pevná náhradní hodnota napsaná ve zdrojáku. Kdo ji zná, podepíše
   si cookie s cizím účtem a je uvnitř jako správce — bez hesla.

2. **Proxy obrázků.** Id z adresy se posílalo do Jellyfinu tak, jak
   přišlo. Skládá se přitom do cesty `/Items/<id>/Images/<druh>`, takže
   otazník nebo lomítko v něm mění, na co se náš server Jellyfinu zeptá —
   a ptá se naším API klíčem, tedy s právy správce.

3. **Hádání hesel.** Přihlášení šlo zkoušet donekonečna. Hashování je
   sice pomalé schválně, ale slovníkový útok přes noc by vyšel — a každý
   pokus stojí čtvrt vteřiny procesoru, takže se tudy dá i zahltit.

4. **/health bez přihlášení** vypisoval velikost knihovny a kolik se
   právě hraje.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bezpecnost.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
# Jako by před aplikací stála proxy. Právě v tomhle nastavení se dřív
# hlavičce X-Forwarded-For věřilo bez ohledu na to, kdo ji poslal.
os.environ["FORWARDED_ALLOW_IPS"] = "127.0.0.1"

from jellyscope import accounts, config, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo", is_admin=False)


print("--- podpisový klíč relací ---")
# Bez SECRET_KEY se musí vyrobit náhodný a uložit, ne sáhnout po pevné
# hodnotě ze zdrojáku.
domecek = Path(tempfile.mkdtemp())
puvodni_base = config.BASE_DIR
config.BASE_DIR = domecek
try:
    klic = config._vlastni_klic()
    check(len(klic) >= 32, f"vyrobený klíč je dost dlouhý ({len(klic)})")
    check(klic != "nezabezpeceny-vychozi-klic", "není to pevná hodnota ze zdrojáku")
    check((domecek / "data" / "secret_key").is_file(), "uložil se na disk")
    check(config._vlastni_klic() == klic,
          "po restartu je stejný - přihlášení nevyprší")

    druhy_domecek = Path(tempfile.mkdtemp())
    config.BASE_DIR = druhy_domecek
    check(config._vlastni_klic() != klic,
          "jiná instalace dostane jiný klíč")
finally:
    config.BASE_DIR = puvodni_base

zdroj = (PROJECT / "jellyscope" / "config.py").read_text(encoding="utf-8")
check('"nezabezpeceny-vychozi-klic"' not in zdroj.split("def _vlastni_klic")[0],
      "v kódu už žádný pevný klíč nezůstal")


print()
print("--- proxy obrázků nesmí sáhnout jinam než na obrázek ---")
volani: list[str] = []


class FalesnyKlient:
    """Zapíše si, na jakou adresu by se Jellyfinu ptal."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FalesnyKlient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def image_bytes(self, item_id: str, kind: str = "Primary",
                          max_width: int = 400) -> None:
        import httpx
        volani.append(str(httpx.URL("http://jellyfin:8096").join(
            f"/Items/{item_id}/Images/{kind}")))
        return None


import jellyscope.web as web  # noqa: E402

web.JellyfinClient = FalesnyKlient  # type: ignore[assignment]

from fastapi.testclient import TestClient  # noqa: E402

klient = TestClient(web.app)
klient.post("/login", data={"username": "ctenar", "password": "ctenarheslo"})

# Čistý požadavek musí projít.
volani.clear()
klient.get("/image/abc123def")
check(volani == ["http://jellyfin:8096/Items/abc123def/Images/Primary"],
      f"běžné id se zeptá na obrázek ({volani})")

# A tohle se do Jellyfinu nesmí dostat vůbec.
for zapis, popis in [
    ("x%3Fapi_key%3Dcizi", "otazník (podstrčené parametry dotazu)"),
    ("..%2F..%2FSessions%3F", "cesta ven z /Items"),
    ("x%2F..%2FUsers", "lomítko uprostřed"),
    ("x%23kotva", "mřížka"),
]:
    volani.clear()
    odpoved = klient.get(f"/image/{zapis}")
    check(odpoved.status_code == 404 and not volani,
          f"{popis}: odmítnuto ({odpoved.status_code}), do Jellyfinu nic ({volani})")

# Druh obrázku i šířka se berou z pevného seznamu, ne z adresy.
volani.clear()
klient.get("/image/abc123def?kind=../../Users&w=99999")
check(volani == ["http://jellyfin:8096/Items/abc123def/Images/Primary"],
      f"neznámý druh spadne zpátky na Primary ({volani})")


print()
print("--- hádání hesel má brzdu ---")
brzdic = TestClient(web.app)
stavy = []
for pokus in range(accounts.POKUSU_DO_BLOKACE + 3):
    stavy.append(brzdic.post(
        "/login", data={"username": "spravce", "password": f"spatne{pokus}"},
        follow_redirects=False).status_code)

check(stavy[0] == 401, f"první pokus je normální odmítnutí ({stavy[0]})")
check(429 in stavy, f"po několika pokusech přijde brzda ({stavy})")
check(stavy.count(429) >= 3,
      f"a drží, ne že by pustila každý druhý ({stavy.count(429)}x)")

# Dokud blokace platí, správné heslo ji neobejde - jinak by stačilo
# hádat dál a poslední pokus zkusit se správným heslem.
prihlaseni = brzdic.post("/login",
                         data={"username": "spravce", "password": "dlouheheslo"},
                         follow_redirects=False)
check(prihlaseni.status_code == 429,
      f"i správné heslo počká, dokud blokace trvá ({prihlaseni.status_code})")

# Po odblokování se přihlásí normálně - a záznam po sobě nenechá,
# takže příští překlep zase začíná od prvního stupně.
for adresa in [r["ip"] for r in accounts.seznam_blokaci()]:
    accounts.odblokuj(adresa)
prihlaseni = brzdic.post("/login",
                         data={"username": "spravce", "password": "dlouheheslo"},
                         follow_redirects=False)
check(prihlaseni.status_code == 303,
      f"po odblokování správné heslo projde ({prihlaseni.status_code})")
check(not accounts.seznam_blokaci(),
      "úspěšné přihlášení smaže i stupeň blokace")

# Podvržená hlavička nesmí brzdu obejít.
#
# Za proxy se skutečná adresa doplňuje z X-Forwarded-For - jenže tu
# hlavičku si umí poslat i útočník. Když jí aplikace věřila sama za sebe,
# stačilo do ní psát pokaždé jiné číslo a každý pokus o heslo se počítal
# zvlášť: brzda formálně existovala a nikdy nesepnula. Teď se adresa bere
# z request.client, kam ji z hlavičky přepíše až uvicorn - a jen tehdy,
# když ji poslal někdo ze seznamu důvěryhodných proxy.
stavy = []
for pokus in range(accounts.POKUSU_DO_BLOKACE + 3):
    stavy.append(brzdic.post(
        "/login", data={"username": "spravce", "password": f"spatne{pokus}"},
        headers={"X-Forwarded-For": f"203.0.113.{pokus}"},
        follow_redirects=False).status_code)
check(429 in stavy, f"pokaždé jiná adresa v hlavičce brzdu neobejde ({stavy})")
check(len(accounts.seznam_blokaci()) == 1,
      f"a je z toho jedna blokace, ne {accounts.POKUSU_DO_BLOKACE} různých "
      f"({[r['ip'] for r in accounts.seznam_blokaci()]})")
for adresa in [r["ip"] for r in accounts.seznam_blokaci()]:
    accounts.odblokuj(adresa)


# Blokace se stupňuje: každá další v řadě trvá déle, čtvrtá už je trvalá.
accounts.odblokuj("10.0.0.9")
stupne = []
for _ in range(len(accounts.STUPNE_BLOKACE) + 1):
    for _ in range(accounts.POKUSU_DO_BLOKACE - 1):
        check_neco = accounts.zapocitej_neuspech("10.0.0.9")
    blokace = accounts.zapocitej_neuspech("10.0.0.9")
    stupne.append("trvale" if blokace["permanent"] else blokace["seconds"])
    # Odemknout čas nejde uspíšit, tak jen zrušíme běžící pauzu - stupeň
    # zůstává, protože o něm rozhoduje sloupec `level`.
    with db.connect() as conn:
        conn.execute("UPDATE login_blocks SET blocked_until = ? WHERE ip = ?",
                     ("2000-01-01 00:00:00", "10.0.0.9"))

check(stupne == [60, 120, 300, 900, "trvale"],
      f"každá další blokace je delší, pátá trvalá ({stupne})")
check(accounts.blokace_zbyva("10.0.0.9") == -1, "trvalá blokace se nerozpouští")

seznam = {r["ip"]: r for r in accounts.seznam_blokaci()}
check("10.0.0.9" in seznam, "blokace je vidět v seznamu pro Nastavení")
check(seznam["10.0.0.9"]["permanent"] is True, "a je označená jako trvalá")

check(accounts.odblokuj("10.0.0.9"), "správce ji umí zrušit")
check(accounts.blokace_zbyva("10.0.0.9") == 0, "po odblokování jde zkusit hned")
check(not accounts.odblokuj("10.0.0.9"), "podruhé už není co rušit")

# Blokace přežije restart aplikace - jinak by stačilo počkat na aktualizaci.
accounts._zablokuj("10.0.0.10")
accounts._pokusy.clear()
check(accounts.blokace_zbyva("10.0.0.10") > 0,
      "blokace je v databázi, ne jen v paměti procesu")
accounts.odblokuj("10.0.0.10")


print()
print("--- ovládání blokací je jen pro správce ---")
odpoved = klient.post("/settings/blocks/unblock", data={"ip": "1.2.3.4"},
                      follow_redirects=False)
check(odpoved.status_code == 403, f"čtenář odblokovat nesmí ({odpoved.status_code})")
check(klient.get("/settings?section=blocks").status_code in (200, 303),
      "a sekci ani nedostane")


print()
print("--- /health bez přihlášení neprozradí obsah ---")
anonym = TestClient(web.app)
verejne = anonym.get("/health").json()
check(set(verejne) == {"status", "started_at"},
      f"nepřihlášený vidí jen stav a čas startu ({sorted(verejne)})")
check(verejne["status"] == "ok", "monitoring pozná, že aplikace žije")

prihlaseny = klient.get("/health").json()
check("library_version" in prihlaseny and "active_sessions" in prihlaseny,
      f"přihlášený vidí všechno ({sorted(prihlaseny)})")


print()
print("--- co se do stránky nesmí dostat ---")
# API klíč Jellyfinu je heslo k cizímu serveru. Do HTML nepatří ani omylem.
db.set_setting("jellyfin_api_key", "TAJNY-KLIC-1234567890")
spravce_klient = TestClient(web.app)
spravce_klient.post("/login", data={"username": "spravce", "password": "dlouheheslo"})
for cesta in ["/settings?section=jellyfin", "/settings?section=data", "/"]:
    obsah = spravce_klient.get(cesta).text
    check("TAJNY-KLIC-1234567890" not in obsah, f"{cesta}: klíč tam není")

# Název titulu se do grafu vkládá jako text, ne jako HTML.
from jellyscope import charts  # noqa: E402

utok = '<img src=x onerror=alert(1)>'
graf = charts.hbar_chart([{"label": utok, "value": 5}], "label", "value")
check(utok not in graf and "&lt;img" in graf,
      "název s HTML značkou se v grafu vypíše jako text")

# Totéž pro bublinu s hodnotami. Ta chodí do stránky jako JSON v atributu
# a prohlížeč z něj skládá uzly přes textContent - takže ani název filmu
# s <img onerror=...> se nemá jak stát značkou.
bublina = charts.area_chart_multi(
    [{"d": "2026-08-11", "v": 1}], "d", [{"key": "v", "label": utok, "slot": 1}])
check(utok not in bublina and "&lt;img" in bublina,
      "název s HTML značkou je escapovaný i v bublině")

zaklad = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
obsluha = zaklad[zaklad.index("function naplnBublinu"):]
obsluha = obsluha[:obsluha.index("document.addEventListener")]
check("innerHTML" not in obsluha,
      "bublina se skládá z uzlů, ne vkládáním HTML")
check("JSON.parse" in obsluha and "textContent" in obsluha,
      "hodnoty se čtou jako data a zapisují jako text")


print()
print("--- hlavičky odpovědi ---")
hlavicky = spravce_klient.get("/").headers
for jmeno, hodnota in [("X-Content-Type-Options", "nosniff"),
                       ("X-Frame-Options", "SAMEORIGIN"),
                       ("Referrer-Policy", "same-origin")]:
    check(hlavicky.get(jmeno) == hodnota,
          f"{jmeno}: {hlavicky.get(jmeno)!r}")

print()
print("--- odkud smí stránka načítat a kam smí posílat ---")
# Jellyscope nemá jediný cizí zdroj: žádné CDN, žádné písmo z internetu,
# obrázky vodíme přes vlastní server. Pravidlo proto může znít "jenom
# odsud" - a hlavně: kdyby se do stránky přes chybu dostal cizí skript,
# tohle mu bere to podstatné. Data nemá kam odeslat (connect-src),
# formulář nemá kam přesměrovat (form-action) a další kód si nemá odkud
# stáhnout (script-src bez cizího zdroje).
csp = hlavicky.get("Content-Security-Policy", "")
for pravidlo in ("default-src 'self'", "img-src 'self'", "connect-src 'self'",
                 "form-action 'self'", "frame-ancestors 'self'",
                 "base-uri 'self'", "object-src 'none'"):
    check(pravidlo in csp, pravidlo)
# 'unsafe-eval' není nikde potřeba - a kdyby se objevilo, CSP by přestala
# být obranou proti vloženému kódu.
check("unsafe-eval" not in csp, "nikde se nepovoluje eval")
check(hlavicky.get("Content-Security-Policy") ==
      spravce_klient.get("/settings").headers.get("Content-Security-Policy"),
      "stejná hlavička na každé stránce")

print()
print("--- strop na nahraný soubor platí během čtení ---")
# Strop je tu kvůli paměti. Když se soubor nejdřív načte celý a teprve
# pak změří, je to obrana, která přijde po škodě: gigabajtový soubor
# server položí dřív, než stačí říct, že je moc velký.
import asyncio  # noqa: E402

from jellyscope import web as _web  # noqa: E402


class NekonecnySoubor:
    """Tváří se jako nahraný soubor, který nikdy neskončí."""

    def __init__(self) -> None:
        self.precteno = 0

    async def read(self, velikost: int = -1) -> bytes:
        # Bez stropu by tohle běželo, dokud nedojde paměť.
        kolik = velikost if velikost > 0 else 4 * 1024 * 1024
        self.precteno += kolik
        return b"x" * kolik


class FalesnyRequest:
    """Jen tolik z Requestu, kolik potřebuje hláška."""

    def __init__(self) -> None:
        self.session: dict = {}


soubor = NekonecnySoubor()
vysledek = asyncio.run(_web._nacti_zalohu(FalesnyRequest(), soubor))
strop = _web.MAX_UPLOAD_MB * 1024 * 1024
check(vysledek is None, "moc velký soubor se odmítne")
check(soubor.precteno <= strop + 2 * 1024 * 1024,
      f"a čtení skončilo hned za stropem ({soubor.precteno // 1024 // 1024} MB "
      f"při stropu {_web.MAX_UPLOAD_MB} MB)")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
