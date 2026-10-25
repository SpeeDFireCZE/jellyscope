# -*- coding: utf-8 -*-
r"""Sezónní výzdoba: kalendář svátků, nastavení a stránky.

Kolem Vánoc, Halloweenu, Silvestra a Velikonoc se Jellyscope sám ozdobí.
Hlídá se tu:

* termíny svátků (obě meze včetně) a Velikonoce pro libovolný rok;
* odkud se bere dnešní datum (vývojový háček → JELLYSCOPE_UDALOST_DATUM
  → skutečné datum) a že nesmyslná proměnná nic nerozbije;
* vypínač správce a volba „bez pohybu" u účtu.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_udalosti.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "udalosti.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"
os.environ.pop("JELLYSCOPE_UDALOST_DATUM", None)

from jellyscope import db, udalosti  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


def datum(text: str | None) -> None:
    """Podvrhne den přes proměnnou prostředí (None = skutečné datum)."""
    if text is None:
        os.environ.pop("JELLYSCOPE_UDALOST_DATUM", None)
    else:
        os.environ["JELLYSCOPE_UDALOST_DATUM"] = text


db.init_db()

print("--- Velikonoce ---")
for rok, cekam in ((2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)),
                   (2027, date(2027, 3, 28)), (2038, date(2038, 4, 25)), (2285, date(2285, 3, 22))):
    check(udalosti.velikonoce(rok) == cekam, f"{rok}: {udalosti.velikonoce(rok)} (čekám {cekam})")
spatne = [rok for rok in range(1583, 4100)
          if udalosti.velikonoce(rok).weekday() != 6
          or not date(rok, 3, 22) <= udalosti.velikonoce(rok) <= date(rok, 4, 25)]
check(not spatne, f"1583–4099: vždy neděle mezi 22. 3. a 25. 4. ({spatne[:5]})")

print("--- termíny ---")
for den, cekam in (
        ("2026-10-23", None), ("2026-10-24", "halloween"), ("2026-10-31", "halloween"), ("2026-11-01", None),
        ("2026-12-16", None), ("2026-12-17", "vanoce"), ("2026-12-26", "vanoce"), ("2026-12-27", "silvestr"),
        ("2026-12-31", "silvestr"), ("2027-01-01", "silvestr"), ("2027-01-02", None),
        ("2026-03-28", None), ("2026-03-29", "velikonoce"), ("2026-04-06", "velikonoce"), ("2026-04-07", None)):
    vyslo = udalosti.udalost_pro(date.fromisoformat(den))
    check(vyslo == cekam, f"{den}: {vyslo} (čekám {cekam})")
check(udalosti.UDALOSTI == ("vanoce", "halloween", "silvestr", "velikonoce"), "seznam klíčů")

print("--- odkud je dnešek ---")
datum(None)
check(udalosti.dnes() == date.today(), "bez proměnné skutečné datum")
datum(" 2026-12-24 ")
check(udalosti.dnes() == date(2026, 12, 24), "proměnná s mezerami kolem platí")
zaznamy: list[logging.LogRecord] = []


class Zachyt(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        zaznamy.append(record)


logging.getLogger("jellyscope.udalosti").addHandler(Zachyt())
datum("24.12.2026")
check(udalosti.dnes() == date.today() and udalosti.dnes() == date.today(),
      "nesmyslná proměnná = skutečné datum")
check(len(zaznamy) == 1, f"nesmysl je v logu jednou, ne při každém volání ({len(zaznamy)}×)")
datum("2026-12-24")
udalosti.ZDROJ_DATA = lambda: date(2027, 3, 29)
check(udalosti.dnes() == date(2027, 3, 29), "vývojový háček má přednost před proměnnou")
udalosti.ZDROJ_DATA = lambda: None
check(udalosti.dnes() == date(2026, 12, 24), "háček bez data = proměnná")
udalosti.ZDROJ_DATA = None

print("--- zapnuto / aktivní / pohyb ---")
datum("2026-12-24")
check(udalosti.zapnuto() and udalosti.aktivni() == "vanoce", "výchozí zapnuto, o Vánocích vanoce")
db.set_setting("ui_udalosti", "0")
db.forget_settings()
check(not udalosti.zapnuto() and udalosti.aktivni() is None, "vypnuto správcem = nic")
check(udalosti.pro_sablonu() == "", "šabloně vypnutá výzdoba = prázdno")
db.set_setting("ui_udalosti", "1")
db.forget_settings()
check(udalosti.pro_sablonu() == "vanoce", "šabloně klíč svátku")
datum("2026-07-01")
check(udalosti.aktivni() is None and udalosti.pro_sablonu() == "", "v létě nic")
check(udalosti.pohyb(None) and udalosti.pohyb({"udalosti_pohyb": None})
      and not udalosti.pohyb({"udalosti_pohyb": "0"}) and udalosti.pohyb({"udalosti_pohyb": "x"}),
      "pohyb: jen „0“ ho vypíná")

print("--- nastavení a účet ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402


def prihlas(jmeno: str, heslo: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
           follow_redirects=False)
    return k


accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo")
accounts.create("divak", "divakheslo")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-divak' WHERE username = 'divak'")
    conn.commit()
spravce = prihlas("spravce", "dlouheheslo")


def nastaveni() -> str:
    return db.get_setting("ui_udalosti", "") or ""


spravce.post("/settings/interface", data={"ui_udalosti": "0"}, follow_redirects=False)
db.forget_settings()
check(nastaveni() == "0", f"správce výzdobu vypne ({nastaveni()!r})")
spravce.post("/settings/interface", data={}, follow_redirects=False)
db.forget_settings()
check(nastaveni() == "1", f"formulář bez pole výzdobu nevypne ({nastaveni()!r})")
rozhrani = spravce.get("/settings?section=interface").text
check('name="ui_udalosti"' in rozhrani
      and "Kolem některých svátků se Jellyscope sám ozdobí. Tady se to dá vypnout." in rozhrani,
      "Rozhraní má vypínač s popiskem")
karta = rozhrani[rozhrani.find("Sezónní výzdoba"):]
karta = karta[:karta.find("</form>")]
check(karta and not any(s in karta for s in ("Halloween", "Vánoce", "Velikonoce", "Silvestr")),
      "karta vypínače svátky neprozrazuje")

ctenar = prihlas("ctenar", "ctenarheslo")
ucet_id = accounts.get_by_name("ctenar")["id"]
check('action="/account/udalosti-pohyb"' in ctenar.get("/account").text,
      "Můj účet nabízí volbu pohybu, když je výzdoba zapnutá")
ctenar.post("/account/udalosti-pohyb", data={"pohyb": "0"}, follow_redirects=False)
check(accounts.get(ucet_id)["udalosti_pohyb"] == "0", "účet si pohyb vypne")
ctenar.post("/account/udalosti-pohyb", data={"pohyb": "1"}, follow_redirects=False)
check(accounts.get(ucet_id)["udalosti_pohyb"] is None, "a zase zapne (NULL)")
ctenar.post("/account/udalosti-pohyb", data={"pohyb": "nesmysl"}, follow_redirects=False)
check(accounts.get(ucet_id)["udalosti_pohyb"] is None, "nesmysl = pohyb zapnutý")
db.set_setting("ui_udalosti", "0")
db.forget_settings()
check('action="/account/udalosti-pohyb"' not in ctenar.get("/account").text,
      "s vypnutou výzdobou volba na Můj účet není")
db.set_setting("ui_udalosti", "1")
db.forget_settings()

divak = prihlas("divak", "divakheslo")
odpoved = divak.post("/account/udalosti-pohyb", data={"pohyb": "0"}, follow_redirects=False)
check(odpoved.status_code == 303 and odpoved.headers.get("location") == "/account"
      and accounts.get_by_name("divak")["udalosti_pohyb"] == "0",
      f"divák z Jellyfinu si pohyb vypnout smí ({odpoved.status_code} {odpoved.headers.get('location')})")

print("--- stránky ---")
anonym = TestClient(app)
spravce_id = accounts.get_by_name("spravce")["id"]
for den, klic in (("2026-10-31", "halloween"), ("2026-12-24", "vanoce"),
                  ("2026-12-31", "silvestr"), ("2026-04-05", "velikonoce")):
    datum(den)
    for nazev, stranka in (("/login", anonym.get("/login").text), ("/", spravce.get("/").text)):
        check(f'data-udalost="{klic}"' in stranka and 'data-udalost-pohyb="1"' in stranka
              and f"/static/udalosti/{klic}.css" in stranka
              and "/static/udalosti/udalosti.css" in stranka
              and "/static/udalosti/udalosti.js" in stranka
              and 'class="brand-logo"' in stranka,
              f"{den} {nazev}: výzdoba {klic}")
datum("2026-12-24")
accounts.nastav_pohyb_udalosti(spravce_id, False)
check('data-udalost-pohyb="0"' in spravce.get("/").text, "účet bez pohybu: data-udalost-pohyb=\"0\"")
check('data-udalost-pohyb="1"' in anonym.get("/login").text, "přihlašovací stránka vždy s pohybem")
accounts.nastav_pohyb_udalosti(spravce_id, True)


def bez_vyzdoby(stranka: str) -> bool:
    return not any(s in stranka for s in ("/static/udalosti/", "data-udalost", "brand-ozdoby",
                                          "brand-logo"))


datum("2026-07-01")
check(bez_vyzdoby(anonym.get("/login").text) and bez_vyzdoby(spravce.get("/").text),
      "v létě stránka nic z výzdoby neobsahuje")
datum("2026-12-24")
db.set_setting("ui_udalosti", "0")
db.forget_settings()
check(bez_vyzdoby(anonym.get("/login").text) and bez_vyzdoby(spravce.get("/").text),
      "vypnutá výzdoba: nic ani o Vánocích")
db.set_setting("ui_udalosti", "1")
db.forget_settings()

puvodni_zapnuto = udalosti.zapnuto


def rozbite() -> bool:
    raise RuntimeError("databáze neodpovídá")


udalosti.zapnuto = rozbite
vypadek = anonym.get("/login")
check(udalosti.pro_sablonu() == "" and vypadek.status_code == 200 and bez_vyzdoby(vypadek.text),
      f"když nastavení nejde přečíst, stránka běží bez výzdoby ({vypadek.status_code})")
udalosti.zapnuto = puvodni_zapnuto

print("--- soubory výzdoby ---")
STATIC = PROJECT / "jellyscope" / "static" / "udalosti"
for klic in udalosti.UDALOSTI:
    css = (STATIC / f"{klic}.css").read_text(encoding="utf-8") if (STATIC / f"{klic}.css").exists() else ""
    check("prefers-reduced-motion" in css and '[data-udalost-pohyb="0"]' in css,
          f"{klic}.css: ozdoby u loga stojí bez pohybu (systém i účet)")
spolecne = (STATIC / "udalosti.css").read_text(encoding="utf-8") if (STATIC / "udalosti.css").exists() else ""
check("@media print" in spolecne and "pointer-events: none" in spolecne,
      "udalosti.css: netiskne se a klikání jde skrz")

print("--- efekty (udalosti.js) ---")
js_soubor = STATIC / "udalosti.js"
js = js_soubor.read_text(encoding="utf-8") if js_soubor.exists() else ""
for co, proc in (("visibilitychange", "na skryté kartě stojí"),
                 ("prefers-reduced-motion", "hlídá systémové omezení animací"),
                 ("data-udalost-pohyb", "hlídá volbu účtu"),
                 ("aria-hidden", "čtečky obrazovky vrstvu přeskočí"),
                 ("resize", "při změně okna se přepočítá")):
    check(co in js, f"udalosti.js: {proc}")
import re  # noqa: E402

stropy = dict(re.findall(r"([A-Z_]+)\s*=\s*(\d+)\s*[,;]", js))
check(stropy.get("SNIH") == "50" and stropy.get("SNIH_MALO") == "25"
      and stropy.get("NETOPYRI") == "6" and stropy.get("NETOPYRI_MALO") == "3"
      and stropy.get("UZKE") == "720",
      f"stropy počtu prvků: sníh 50/25, netopýři 6/3, úzké okno pod 720 px ({stropy})")

print("--- data beze změny ---")
# Svatek meni barvu aplikace (--accent). Stav "info" (Direct stream),
# jednobarevne grafy a voda na mape ale nesou vyznam - o Halloweenu by
# prime streamovani vypadalo jako prepocet. Pouzivaji --barva-dat.
from jellyscope import charts  # noqa: E402

AKCENT = re.compile(r"var\(--accent(-2)?\)")
check(charts.ROLE_BARVY["info"] == "var(--barva-dat)", f"stav info = barva dat ({charts.ROLE_BARVY['info']})")
pruh = charts.stacked_bar([{"label": "a", "value": 3, "role": "info"}, {"label": "b", "value": 1}])
check("var(--barva-dat)" in pruh and "var(--ink-barva-dat" in pruh, "dílek info: barva dat i její písmo")
for soubor in ("jellyscope/charts.py", "jellyscope/templates/_sekce_rust.html",
               "jellyscope/templates/_sekce_spicky.html", "jellyscope/templates/_sit_zive.html"):
    text = (PROJECT / soubor).read_text(encoding="utf-8")
    check(not AKCENT.search(text), f"{soubor}: grafy bez sváteční barvy ({AKCENT.findall(text)[:3]})")
STYL = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
for selektor in (".badge.info     .dot", ".session-tags .badge.info {", ".chart-vyber {",
                 ".chart .chart-hit circle", ".mapa-podrobna .v-jezera", ".mapa-podrobna .v-reky {"):
    i = STYL.find(selektor)
    telo = STYL[i:STYL.find("}", i)] if i >= 0 else ""
    check(i >= 0 and "--barva-dat" in telo and not AKCENT.search(telo), f"style.css {selektor.strip()}: barva dat")

print("--- odolnost efektů ---")
# Atribut fill="var(--x)" v SVG nebere kazdy prohlizec; style="fill: var(--x)" ano.
for soubor in (STATIC / "udalosti.js", PROJECT / "jellyscope" / "templates" / "_znacka.html"):
    text = soubor.read_text(encoding="utf-8")
    atributy = re.findall(r'(?:fill|stroke|stop-color)="var\(', text)
    check(not atributy, f"{soubor.name}: barvy z proměnných jen přes style, ne atributem ({len(atributy)}×)")
# Mobilni prohlizec posila resize pri kazdem skryti adresniho radku.
# Prestavet efekt se ma jen pri zmene SIRKY, vyska jen prepocita --h.
check(re.search(r"innerWidth\s*!==?\s*\w+|\w+\s*!==?\s*window\.innerWidth", js) is not None,
      "udalosti.js: při změně jen výšky okna se efekt nepřestavuje")

print("--- hry po kliknutí na ozdobu ---")
import json as json_modul  # noqa: E402


def texty_hry(stranka: str) -> dict | None:
    nalez = re.search(r'<script type="application/json" id="udalost-texty">(.*?)</script>', stranka, re.S)
    return json_modul.loads(nalez.group(1)) if nalez else None


def tlacitko_hry(stranka: str) -> str:
    nalez = re.search(r'<button[^>]*class="udalost-hra[ "][^>]*>', stranka)
    return nalez.group(0) if nalez else ""


for den, klic, prani in (("2026-12-24", "vanoce", "Veselé Vánoce!"), ("2026-10-31", "halloween", None),
                         ("2026-12-31", "silvestr", "Šťastný nový rok!"), ("2026-04-05", "velikonoce", "Veselé Velikonoce!")):
    datum(den)
    for nazev, stranka in (("/login", anonym.get("/login").text), ("/", spravce.get("/").text)):
        tl, tx = tlacitko_hry(stranka), texty_hry(stranka) or {}
        check(f'data-hra="{klic}"' in tl and 'type="button"' in tl and 'aria-label="' in tl
              and tx.get("zavrit") and tx.get("ozdoba") and tx.get("napoveda")
              and (prani is None or tx.get("prani") == prani),
              f"{den} {nazev}: tlačítko hry {klic} a její texty")
# Kód hry dnešního svátku se stahuje se stránkou (v hlavičce), ne až po
# kliknutí - na Přehledu by jinak čekal ve frontě za plakáty.
for den, klic in (("2026-12-24", "vanoce"), ("2026-10-31", "halloween"),
                  ("2026-12-31", "silvestr"), ("2026-04-05", "velikonoce")):
    datum(den)
    for nazev, stranka in (("/login", anonym.get("/login").text), ("/", spravce.get("/").text)):
        hlava = stranka.split("</head>")[0]
        check(f'<script defer src="/static/udalosti/hry/{klic}.js?v=' in hlava
              and hlava.find("/udalosti.js?v=") < hlava.find(f"/hry/{klic}.js?v="),
              f"{den} {nazev}: hra {klic} se stahuje se stránkou (po udalosti.js)")
datum("2026-07-01")
check("/static/udalosti/hry/" not in spravce.get("/").text, "mimo sezónu se žádná hra nestahuje")

# Finále Silvestra píše rok, který se slaví - podle data serveru (i
# podstrčeného), ne podle hodin v prohlížeči.
check(udalosti.slaveny_rok(date(2026, 12, 27)) == 2027 and udalosti.slaveny_rok(date(2027, 1, 1)) == 2027,
      "slavený rok: 27. 12. i 1. 1. je ten nový")
for den in ("2026-12-31", "2027-01-01"):
    datum(den)
    check((texty_hry(spravce.get("/").text) or {}).get("rok") == "2027", f"{den}: finále slaví rok 2027")
accounts.nastav_jazyk(spravce_id, "en")
check((texty_hry(spravce.get("/").text) or {}).get("prani") == "Happy New Year!", "novoroční přání s vykřičníkem (en)")
accounts.nastav_jazyk(spravce_id, None)


# Vývojový zdroj data čte databázi; když vypadne, rok pro šablonu nesmí
# shodit stránku (svátek už se v tu chvíli ukazuje).
def _vypadek() -> date:
    raise RuntimeError("databáze neodpovídá")


udalosti.ZDROJ_DATA = _vypadek
try:
    rok = udalosti.rok_pro_sablonu()
except Exception as chyba:  # noqa: BLE001
    rok = f"výjimka {chyba}"
udalosti.ZDROJ_DATA = None
check(rok == str(udalosti.slaveny_rok(date.today())), f"rok pro šablonu přežije výpadek zdroje data ({rok})")
datum("2026-12-24")
from jellyscope import web  # noqa: E402

_rok_global = web.templates.env.globals["udalost_rok"]
web.templates.env.globals["udalost_rok"] = _vypadek
try:
    _vanoce = spravce.get("/")
    _vanoce_ok = _vanoce.status_code == 200 and "rok" not in (texty_hry(_vanoce.text) or {})
except RuntimeError:
    _vanoce_ok = False
web.templates.env.globals["udalost_rok"] = _rok_global
check(_vanoce_ok, "rok se počítá jen na Silvestra")

# Přihlášení kódem (login_quick.html) má ozdobu i texty jako ostatní stránky.
from starlette.requests import Request  # noqa: E402

_zadost = Request({"type": "http", "method": "POST", "path": "/login/quick", "headers": [],
                   "query_string": b"", "session": {}, "app": web.app})
_quick = web.templates.TemplateResponse(_zadost, "login_quick.html", {"kod": "123456"}).body.decode()
check(tlacitko_hry(_quick) and (texty_hry(_quick) or {}).get("prani") == "Veselé Vánoce!",
      "přihlášení kódem: tlačítko hry a texty")
datum("2026-12-24")
stranka = spravce.get("/").text
odkaz = stranka.find('<a class="brand"')
check(odkaz >= 0 and stranka.find("udalost-hra", odkaz) > stranka.find("</a>", odkaz),
      "ozdoba je mimo odkaz na Přehled (klik na ni Přehled neotevře)")
accounts.nastav_jazyk(spravce_id, "en")
check((texty_hry(spravce.get("/").text) or {}).get("prani") == "Merry Christmas!", "texty hry v jazyce účtu (en)")
accounts.nastav_jazyk(spravce_id, None)
datum("2026-07-01")
check("udalost-hra" not in anonym.get("/login").text and "udalost-texty" not in spravce.get("/").text,
      "mimo sezónu žádné tlačítko ani texty hry")
for klic in udalosti.UDALOSTI:
    soubor = STATIC / "hry" / f"{klic}.js"
    obsah = soubor.read_text(encoding="utf-8") if soubor.exists() else ""
    check(f'JellyscopeHry.registruj("{klic}"' in obsah, f"hry/{klic}.js se zaregistruje")
js = (STATIC / "udalosti.js").read_text(encoding="utf-8")
for co, proc in (("JellyscopeHry", "rámec her"), ("Escape", "Esc zavře scénu"), ("aria-modal", "scéna je dialog"),
                 ("/static/udalosti/hry/", "hra se stáhne až po kliknutí"), (".focus()", "fokus do scény a zpět"),
                 ('"Tab"', "Tab zůstává ve scéně"), ("onerror", "nenačtená hra nic nerozbije")):
    check(co in js, f"udalosti.js: {proc}")

# Kód jednotlivých her: texty jen z api.texty (překlady), časovače přes api
# (po zavření scény se zruší), ovládání i klávesnicí, bez pohybu konečný stav.
OCEKAVA_HRA = {
    "vanoce": ("api.texty.prani", "api.texty.napoveda", "api.texty.darek", '"Enter"', "tabindex",
               "api.klid()", "api.konfety(", "api.snimek(", "api.po("),
    "halloween": ("api.texty.napoveda", "api.texty.rozsvitit", "api.texty.sfouknout", "api.texty.znovu",
                  "api.texty.dyne", "api.klid()", "api.bodVSvg(", "pointercancel"),
    "silvestr": ("api.texty.napoveda", "api.texty.hori", "api.texty.zapalovac", "api.texty.prani",
                 "api.texty.rok", '"Enter"', "tabindex", "api.klid()", "api.bodVSvg(", "api.snimek(",
                 "api.po(", "api.zavri()", "udalost-finale", "onfinish"),
    "velikonoce": ("api.texty.napoveda", "api.texty.vajicko", "api.texty.pip", "api.texty.prani", '"Enter"',
                   "tabindex", "api.klid()", "api.konfety(", "api.snimek(", "api.po("),
}
CESKE_TEXTY = ("Veselé", "Klikni", "Dárek", "Dýně", "Hoří", "píp", "Šťastný")
# id v kresbách her mají předponu udalost- jako zbytek výzdoby - jinak by se
# mohla potkat s id grafů nebo jiné kresby na stránce.
for soubor in sorted((STATIC / "hry").glob("*.js")):
    obsah = soubor.read_text(encoding="utf-8")
    idcka = re.findall(r'\bid="([^"]+)"', obsah) + re.findall(r'\bid = "([^"]+)"', obsah)
    spatne = sorted({i for i in idcka if not i.startswith("udalost-")})
    check(idcka and not spatne, f"hry/{soubor.name}: id s předponou udalost- ({spatne})")
for soubor in ("halloween.css",):
    odkazy = re.findall(r"url\(#([^)]+)\)", (STATIC / soubor).read_text(encoding="utf-8"))
    check(all(o.startswith("udalost-") for o in odkazy), f"{soubor}: odkazy na id her s předponou ({odkazy})")
# Zvětšení ozdoby pod myší jen tam, kde myš je - na dotyku by zůstalo viset.
css = (STATIC / "udalosti.css").read_text(encoding="utf-8")
mimo_media = re.sub(r"@media \(hover: hover\) \{[^}]*\}\s*\}", "", css)
check(".udalost-hra:hover" in css and ".udalost-hra:hover" not in mimo_media,
      "zvětšení ozdoby pod myší jen v @media (hover: hover)")
for klic, ocekava in OCEKAVA_HRA.items():
    obsah = (STATIC / "hry" / f"{klic}.js").read_text(encoding="utf-8")
    chybi = [co for co in ocekava if co not in obsah]
    check(not chybi, f"hry/{klic}.js: texty, klávesnice, klid, časovače přes api (chybí {chybi})")
    check("setTimeout(" not in obsah and "requestAnimationFrame(" not in obsah,
          f"hry/{klic}.js: žádný časovač mimo api (po zavření scény by běžel dál)")
    check(not [t for t in CESKE_TEXTY if t in obsah], f"hry/{klic}.js: žádný text natvrdo česky")

print("--- bez vývojových souborů (prod, demo) ---")
# jellyscope/vyvoj.py se do produ nekopiruje (JEN_DEV). Aplikace bez nej
# musi nabehnout a datum si brat jen z promenne nebo z kalendare.
import subprocess  # noqa: E402

prostredi = {**os.environ, "JELLYSCOPE_HOME": tempfile.mkdtemp(),
             "DATABASE_PATH": str(Path(tempfile.mkdtemp()) / "bez.db"), "PYTHONIOENCODING": "utf-8"}
beh = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.modules['jellyscope.vyvoj'] = None\n"
     "import jellyscope.web as w\n"
     "from jellyscope import udalosti\n"
     "assert w.vyvoj is None, 'vyvoj'\n"
     "assert udalosti.ZDROJ_DATA is None, 'zdroj'\n"
     "assert 'vyvoj_datum' not in w.templates.env.globals, 'globál'\n"
     "print('bez vyvoj ok')"],
    cwd=str(PROJECT), env=prostredi, capture_output=True, text=True, timeout=120)
check(beh.returncode == 0 and "bez vyvoj ok" in beh.stdout,
      f"aplikace běží i bez vyvoj.py ({beh.returncode} {beh.stderr.strip()[-160:]})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
