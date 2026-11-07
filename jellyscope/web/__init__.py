"""Webova aplikace - routy, sablony, prihlaseni.

FastAPI je knihovna, ktera prirazuje adresy funkcim. Kdyz prohlizec pozada
o "/library", zavola se funkce oznacena @app.get("/library"). Nic vic v tom
neni.

Kazda routa v balicku dela totez ve trech krocich:
    1. precti parametry z adresy (napr. za jak dlouhe obdobi)
    2. zeptej se modulu stats / insights na data
    3. predej data sablone, at je vykresli

Zadne SQL, zadne pocitani. Kdyz se v balicku web objevi vzorec, patri
jinam.

Balík podle oblastí (jellyscope/web/). Routy jsou v podmodulech jako
@router.get("/library") - router je skupina rout, kterou tenhle soubor
připojí k aplikaci (app.include_router):

* `__init__` - aplikace: app, middleware, ošetření chyb, připojení rout
* `start` - spuštění a vypnutí (lifespan)
* `zaklad` - co potřebuje každá stránka (šablony, kontext, hlášky, závora přihlášení)
* `nastaveni` - Nastavení
* `obdobi` - výběr období a druhu obsahu
* `prihlaseni` - první spuštění, přihlášení, odhlášení
* `ucet` - můj účet
* `prehled` - přehled a jeho karty, srovnání, vlastní přehledy, Postřehy
* `knihovna` - knihovna, tituly, archiv, obrázky
* `uzivatele` - uživatelé
* `historie` - historie přehrání
* `jazyky` - jazyky
* `sit` - síť
* `stav` - malé JSON rozhraní (/health, /api/now-playing)
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from typing import Any


from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .. import (api, api_dotaz, charts, formatting,
               i18n, languages,
               pristup,
               sekce,
               stats, udalosti)
# Verze běžícího procesu. Schválně natvrdo při importu: po `git pull`
# leží na disku nová, ale tenhle proces pořád běží starý kód - a čekárna
# po aktualizaci potřebuje vědět přesně to, co běží tady, ne co je na
# disku. Až se proces vymění, načte se soubor znovu i s novým číslem.
from .. import __version__  # noqa: F401 - čte se i zvenku
# Zaklad webu bydli vedle - potrebuji ho vsechny casti rout, takze
# by jinak musely importovat tenhle modul a on je.
from .zaklad import (  # noqa: F401 - dal se pouzivaji i zvenku
    KAPACITA_JEDNOTKY,
    PACKAGE_DIR,
    SETTINGS_SECTIONS,
    STARTED_AT,
    STROP_MAX,
    STROP_MIN,
    TEMPLATES_DIR,
    VZHLEDY,
    ZOOM_REZIMY,
    ZPET_NA_IMPORT,
    _bez_mezipameti,
    _cas_presne,
    _cesky_datum,
    _cislo_z_formulare,
    _clamp,
    _context,
    _flash,
    _hlaska_importu,
    _known_note,
    _linked_note,
    _novy_klic,
    _obdobi_do_sablony,
    _obdobi_popis,
    _opraveno_note,
    _posledni_den,
    _rychla_obdobi,
    _stropy,
    _veta,
    _vzhled,
    _podklad_mapy,
    _zoom_rezim,
    config,
    current_account,
    prihlas_do_relace,
    log,
    muj_divak,
    otisk_obrazku_uctu,
    require_admin,
    require_login,
    templates,
)
from .zaklad import OSM_DLAZDICE, csp  # noqa: F401 - OSM_DLAZDICE čte se i zvenku
from .obdobi import ALLOWED_DAYS, DAY_LABELS
from .prihlaseni import _adresa_klienta
from .start import lifespan

# Podmoduly s routami - připojí je app.include_router na konci souboru.
from . import (historie, jazyky, knihovna, nastaveni, prehled, prihlaseni, sit, stav, ucet,
               uzivatele)

# Vstupní dveře: jména z podmodulů, která se čtou i zvenku (web.X, testy).
# Kód balíku si je bere přímo z podmodulu, ne odsud.
from .knihovna import STROP_MAZANI_ARCHIVU, item_image  # noqa: F401 - čte se i zvenku
from .obdobi import _kind  # noqa: F401 - čte se i zvenku
from .prihlaseni import _setup_context  # noqa: F401 - čte se i zvenku
from .ucet import muj_obrazek  # noqa: F401 - čte se i zvenku


STATIC_DIR = PACKAGE_DIR / "static"


app = FastAPI(title="Jellyscope", lifespan=lifespan, docs_url=None, redoc_url=None)
def _cesta_odkud_prisel(referer: str | None) -> str:
    """Cesta z `referer`, nebo domů. Nikdy adresa na cizí server.

    Vrací se schválně jen cesta - bez schématu a hostitele. Takový odkaz
    je vždycky na tomtéž serveru, takže se nedá zneužít k odvedení
    člověka jinam, a přitom ho vrátí přesně tam, kde byl.

    `//zlo.cz/x` vypadá jako cesta, ale prohlížeč ho čte jako adresu na
    cizí server, takže neprojde.
    """
    adresa = urlsplit(referer or "")
    kam = adresa.path or "/"
    if adresa.query:
        kam = f"{kam}?{adresa.query}"
    if not kam.startswith("/") or kam.startswith("//"):
        return "/"
    return kam


@app.middleware("http")
async def ukazkovy_rezim(request: Request, call_next):
    """V ukázce se nic nemění - místo akce se objeví hláška.

    Ukázka běží na veřejné adrese a přihlašovací údaje jsou rovnou
    v přihlašovacím okně, takže dovnitř se dostane kdokoliv. Bez téhle
    pojistky by první návštěvník přepsal adresu Jellyfinu, spustil import
    nebo restart - a pro všechny ostatní by ukázka skončila.

    Hlídá se to tady, na jednom místě, a ne v každé routě zvlášť: routa,
    na kterou by se zapomnělo, je přesně ta, kterou někdo najde. Seznam
    výjimek je v DEMO_POVOLENO.

    Tlačítka zůstávají vidět schválně - ukázka má ukázat, co aplikace
    umí. Jen místo práce odpoví hláškou.
    """
    if _demo_blokuje(request):
        _flash(request, "Tohle je ukázka, data se v ní nemění. "
                        "Na vlastní instalaci tlačítko funguje.", "info")
        # Zpátky přesně tam, odkud člověk přišel - ne na Přehled. Kdo
        # zkoumá Nastavení, má po kliknutí zůstat v Nastavení; vyhodit ho
        # na úvodní stránku je trest za zvědavost.
        #
        # `referer` posílá prohlížeč u formuláře odeslaného ze stránky
        # vždycky - jenže je to hlavička od návštěvníka, tedy údaj zvenku.
        # Přesměrovat podle ní na celou adresu by z ukázky udělalo
        # otevřený přesměrovávač.
        #
        # Bere se proto jen **cesta**. Hostitel se neporovnává vůbec:
        # kontrola "začíná naší adresou" tu jednou byla a pouštěla
        # `https://jellyscope.cz.utocnik.cz/`, protože naší adresou
        # opravdu začíná. Relativní cesta nikam odejít nemůže, ať referer
        # přijde odkudkoliv, a nerozbije se za proxy, kde se liší schéma
        # i port.
        kam = _cesta_odkud_prisel(request.headers.get("referer"))
        return RedirectResponse(kam, status_code=303)
    return await call_next(request)


# Co smí divák z Jellyfinu odeslat (POST). Jinak jen čte. Vlastní API
# klíč si vyrobí jen s povolením - to hlídá routa sama.
DIVAK_SMI_POST = frozenset({"/logout", "/account/language", "/account/password",
                            "/account/klic", "/account/klic/zrusit",
                            # Bez pohybu u sezónní výzdoby - jen pro sebe.
                            "/account/udalosti-pohyb",
                            # Můj přehled - vlastní rozvržení, nikoho jiného.
                            "/account/prehled"})


@app.middleware("http")
async def rozsah_divaka(request: Request, call_next):
    """Kdo není správce, vidí jen to, co mu správce zaškrtl.

    Hlídá se to **na jednom místě**, ne v každé routě: kdyby se na jednu
    zapomnělo, neprojeví se to jako chyba, ale jako cizí historie na
    obrazovce někoho, komu do ní nic není. Proto povolující seznam a
    zamčené dveře - co v něm není, končí tady.

    Skupiny jsou dvě a liší se v tom, co znamená „moje":

    * **divák z Jellyfinu** má svůj účet u konkrétního člověka, takže mu
      vlastní čísla patří vždycky. Přehled ho pošle na jeho stránku,
      cizí stránka diváka je zavřená a nic nemění - jen čte.
    * **místní čtenářský účet** nikomu konkrétnímu nepatří (zakládá ho
      správce), takže „moje historie" u něj nedává smysl. Platí pro něj
      jen zaškrtnuté oblasti.
    """
    ucet = current_account(request)
    kdo = pristup.role(ucet)
    if kdo in ("", pristup.SPRAVCE):
        return await call_next(request)

    divak = muj_divak(ucet)
    cesta = request.url.path

    # Kdo nesmi Prehled, toho / posle na prvni stranku, kterou smi (divaka
    # na jeho vlastni) - ne na „sem nemas pristup". Sem miri i prihlaseni,
    # heslem i kodem, takze se po nem nikdo nezasekne na zavrenych dverich.
    # `ucet` jde s kazdym dotazem dal: ucet muze mit vlastni prava, ktera
    # skupinu prebiji (Nastaveni -> Ucty -> Prava).
    if cesta == "/" and not pristup.vidi(kdo, "prehled", ucet):
        return RedirectResponse(pristup.domovska(kdo, divak, ucet), status_code=303)
    if divak:
        # Divak nic nemeni, jen cte - s vyjimkou odhlaseni a sveho uctu
        # (Muj ucet). Vycet, ne predpona /account/: kazda dalsi zapisujici
        # routa se sem musi pridat vedome, jinak by byla divakum otevrena
        # sama od sebe. Routy berou ucet z relace a heslo uctu z Jellyfinu
        # odmitne sama routa (accounts.zmen_vlastni_heslo).
        if request.method != "GET" and cesta not in DIVAK_SMI_POST:
            return _nesmis(request)

    # Stejne pravidlo kresli v sablonach odkazy (smi), takze odkaz vede
    # jen tam, kam se opravdu da vejit.
    if not pristup.smi_otevrit(ucet, cesta):
        return _nesmis(request)

    return await call_next(request)


def _nesmis(request: Request):
    """Odpověď na „tohle ti nepatří". Stránka, ne holé 403."""
    ucet = current_account(request)
    return templates.TemplateResponse(
        request, "nesmis.html", _context(request, ucet), status_code=403)


@app.middleware("http")
async def jazyk_uctu(request: Request, call_next):
    """Jazyk, který si přihlášený účet vybral na stránce Můj účet.

    Pořadí middlewarů tu rozhoduje: každý další obaluje ty předchozí.
    Tenhle potřebuje relaci, takže musí být uvnitř SessionMiddleware (ten
    se přidává hned pod ním), a zároveň vně `rozsah_divaka`
    a `ukazkovy_rezim` - i jejich stránky („sem nemáš přístup", hláška
    ukázky) mají být v jazyce účtu. Bez účtu (přihlášení, API klíčem)
    zůstane jazyk serveru.
    """
    ucet = current_account(request)
    token = i18n.nastav_jazyk_uctu((ucet or {}).get("ui_language"))
    try:
        return await call_next(request)
    finally:
        i18n.vrat_jazyk_uctu(token)


app.add_middleware(
    SessionMiddleware,
    secret_key=config.secret_key,
    session_cookie="jellyscope_session",
    max_age=14 * 24 * 3600,
    # same_site="lax" znamena, ze cookie se neposle pri pozadavku z ciziho
    # webu. Diky tomu nemuze cizi stranka odeslat formular tvym jmenem.
    same_site="lax",
    # Za HTTPS proxy zapnout pres SECURE_COOKIES=1 v .env. Prohlizec pak
    # cookie posle jen po sifrovanem spojeni. Pri behu na localhostu bez
    # HTTPS musi zustat vypnute, jinak by se nikdo neprihlasil.
    https_only=config.secure_cookies,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Tri hlavicky, ktere prohlizeci rikaji, co s odpovedi nesmi delat.
# Stoji jeden middleware a zavirají cele skupiny utoku dopredu.
# Co smi ukazkovy rezim zmenit: prihlaseni a odhlaseni, nic vic.
#
# Ukazka bezi na verejne adrese a prihlasit se do ni muze kdokoliv -
# udaje jsou v prihlasovacim okne. Cokoliv ulozeneho plati pro vsechny
# dalsi navstevniky, takze i neskodne veci jako jazyk rozhrani jsou
# zamcene: kdo si prepne na cestinu, prepne ji i tomu po sobe.
#
# Tlacitka zustavaji videt schvalne: ukazka ma ukazat, co aplikace umi.
# Misto akce se objevi hlaska.
# Ctecí API. Vlastni modul a vlastni zavora: stranky se prokazuji
# prihlasovaci cookie, API tokenem. Nic pod /api/ nic nemeni - jsou tam
# sama GET.
app.include_router(api.router)


class ZaznamPouzitiApi:
    """Každý požadavek s existujícím klíčem se zapíše - až po odpovědi.

    Až teprve po ní je známý výsledek (200, 403, ...). Klíč (id, jméno,
    vlastník) sem nechává api.over_token() v `request.state`; neplatný
    klíč ho nenechá, a tak se nezapíše. Zapisuje se do logu API.

    Čisté ASGI, ne `@app.middleware`: tam odchází tělo odpovědi až po
    návratu middlewaru, takže by zápis zdržel každou odpověď API. Tady má
    klient odpověď dřív, než se zapisuje.

    Zápis nesmí shodit odpověď: kdo se ptá na čísla, má je dostat, i když
    se záznam zrovna nepovedl. A neodchycená chyba routy (500) se zapíše
    taky - pak se pošle dál, ať ji obslouží Starlette jako dřív.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        vysledek = 500

        async def posli(zprava) -> None:
            nonlocal vysledek
            if zprava["type"] == "http.response.start":
                vysledek = zprava["status"]
            await send(zprava)

        try:
            await self.app(scope, receive, posli)
        except Exception:
            await _zapis_pouziti(scope, vysledek)
            raise
        await _zapis_pouziti(scope, vysledek)


# Kdy se naposledy zalogovala chyba zapisu pouziti. Kdyz zapis selhava
# porad (plny disk, prava), Grafana by kazdych par vterin pridala do logu
# celou chybovou hlasku - staci jedna za ctvrt hodiny.
CHYBA_ZAPISU_PO_SEKUNDACH = 900
_posledni_chyba_zapisu = 0.0


async def _zapis_pouziti(scope, vysledek: int) -> None:
    global _posledni_chyba_zapisu
    klic = (scope.get("state") or {}).get("api_klic_do_logu")
    if not klic:
        return
    cesta = scope["path"]
    dotaz = scope.get("query_string", b"").decode("latin-1")
    if dotaz:
        cesta = f"{cesta}?{dotaz}"
    try:
        await asyncio.to_thread(api.zapis_pouziti, klic, cesta,
                                _adresa_klienta(Request(scope)), vysledek)
    except Exception:  # noqa: BLE001 - zaznam je navic, odpoved ne
        if time.time() - _posledni_chyba_zapisu >= CHYBA_ZAPISU_PO_SEKUNDACH:
            _posledni_chyba_zapisu = time.time()
            log.exception("Zapis pouziti API klice se nepovedl")


app.add_middleware(ZaznamPouzitiApi)


DEMO_POVOLENO = frozenset({
    "/login",
    "/logout",
})


def _demo_blokuje(request: Request) -> bool:
    """Ma se tenhle pozadavek v ukazce zastavit?"""
    if not config.demo_mode or request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    return request.url.path not in DEMO_POVOLENO


@app.middleware("http")
async def bezpecnostni_hlavicky(request: Request, call_next):
    response = await call_next(request)
    # Podklad mapy z OpenStreetMap meni dve hlavicky (CSP a Referrer-Policy).
    # Kdyz nastaveni nejde precist (databaze zrovna nebezi), plati prisna
    # varianta - hlavicka nesmi shodit odpoved, ani /health nebo styly.
    try:
        osm = _podklad_mapy() == "osm"
    except Exception:  # noqa: BLE001 - jakakoliv chyba = prisne hlavicky
        osm = False
    # "Neuhaduj typ obsahu." Bez toho prohlizec u souboru, ktery vypada
    # jako HTML, ignoruje deklarovany typ a spusti ho jako stranku -
    # tyka se to hlavne obrazku, ktere vodime z Jellyfinu.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # "Nevkladej me do ramecku na cizi strance." Bez toho jde stranku
    # prekryt neviditelnou vrstvou a nechat cloveka klikat na neco
    # jineho, nez si mysli (clickjacking).
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # "Pri odchodu na cizi web neposilej, odkud clovek prisel."
    # Adresy Jellyscope obsahuji id polozek i uzivatelu.
    #
    # Se zapnutym podkladem z OSM jde ven adresa serveru (bez cesty, takze
    # bez id): OSM dlazdice bez Refereru odmita a atribut referrerpolicy
    # u SVG <image> nemusi kazdy prohlizec znat. U volby to stoji.
    response.headers.setdefault(
        "Referrer-Policy", "strict-origin-when-cross-origin" if osm else "same-origin")
    # Nic z tohohle aplikace nepotrebuje, tak at si to o to nemuze rict
    # ani kus stranky, ktery by se sem nedopatrenim dostal.
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    # HSTS jen tam, kde uz aplikace bezi po HTTPS (SECURE_COOKIES) -
    # prohlizec si ho pamatuje dlouho, takze zaplé na serveru bez
    # certifikatu by lidi zamklo venku. Bez `includeSubDomains`
    # a `preload` zamerne: to jsou rozhodnuti o cele domene, ne o nas.
    if config.secure_cookies:
        response.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000")
    # Odkud smi stranka cokoliv nacist a kam smi cokoliv poslat.
    #
    # Jellyscope nema jediny cizi zdroj - zadne CDN, zadne pismo z internetu,
    # obrazky vodime pres vlastni server. Muzeme si proto dovolit rict
    # "jenom odsud" a nic tim nerozbit.
    #
    # Skripty jen ze souboru aplikace (`script-src 'self'`), zadny vlozeny:
    # kdyby se kdekoliv ve vypisu dat objevila chyba v escapovani, vlozeny
    # <script> ani onerror= prohlizec nespusti. Proto aplikace sama zadny
    # vlozeny kod nema - novy skript patri do static/js, hodnoty ze sablony
    # do datoveho bloku `texty-js` nebo atributu data-…, obsluha prvku do
    # data-… (static/js/zaklad.js). Hlida to tests/test_bez_vlozeneho_js.py;
    # `'unsafe-inline'` sem nevracet ani „docasne".
    #
    # Dal CSP bere utocnikovi, co by s kodem chtel delat: stahnout si dalsi
    # odjinud (script-src), odeslat data na cizi server (connect-src),
    # poslat formular jinam (form-action) nebo stranku zaramovat
    # (frame-ancestors). Styly vlozene byt smi (style-src) - jsou jich
    # stovky a bezpecnostni zisk by byl maly.
    #
    # Jedina vyjimka: cekarna po aktualizaci. Sklada ji Python a kresli ji
    # stara verze nad novymi soubory, takze nesmi sahnout na nic, co se
    # s verzi meni (ani na static/js). Jeji skript nese jednorazovy nonce,
    # ktery routa necha v request.state - jinak by se po aktualizaci
    # z prohlizece stranka nikdy nepohnula.
    nonce = getattr(request.state, "csp_nonce", None)
    response.headers.setdefault("Content-Security-Policy", csp(osm, nonce))
    return response


formatting.register(templates.env)
# Druh polozky cesky ("Episode" -> "Díl seriálu"). Bydli ve stats, protoze
# tam se rozhoduje, co je film, serial a co "ostatní" - sablona jen vypisuje.
templates.env.filters["type_name"] = stats.nazev_typu
# Sablony smi volat funkce z charts - kresleni patri do sablony, ne do routy.
templates.env.globals["charts"] = charts
templates.env.globals["day_labels"] = DAY_LABELS
# Makro karta() (dashboard.html): překlep v názvu karty = chyba, ne tichá díra.
templates.env.globals["over_kartu"] = sekce.over_kartu
templates.env.globals["allowed_days"] = ALLOWED_DAYS
# `lang` je v sablonach potreba na kazde strance, kde se zobrazuje jazyk -
# proto globalne, aby ho nemusela predavat kazda routa zvlast.
templates.env.globals["lang"] = languages
# Preklad rozhrani - v sablonach se pouziva jako funkce _("text").
i18n.register(templates.env)


def _asset_version() -> str:
    """Otisk statickych souboru pro adresu stylu a obrazku.

    K cemu to je: prohlizec si CSS ulozi a priste ho nestahuje znovu.
    U reverzni proxy, ktera /static/ jeste cachuje (viz priklady ve
    slozce deploy/), by zmena vzhledu byla tyden neviditelna - uzivatel
    by videl starou stranku a marne hledal, proc uprava nefunguje.

    Reseni je stara a spolehliva finta: k adrese se pripoji cislo, ktere
    se pri zmene souboru zmeni. Jina adresa = jiny soubor = prohlizec ho
    stahne znovu. Dokud se nic nemeni, cachuje se dal.

    Bereme cas posledni zmeny nejnovejsiho souboru ve static/. Pocita se
    jednou pri startu, takze po uprave stylu je potreba restart - stejne
    jako u sablon.
    """
    try:
        newest = max(path.stat().st_mtime
                     for path in STATIC_DIR.rglob("*") if path.is_file())
    except ValueError:
        return "0"
    return str(int(newest))


templates.env.globals["asset_version"] = _asset_version()
# Sezónní výzdoba: který svátek je (klíč, nebo "") a smí se účtu hýbat.
# Globální funkce, ne kontext - přihlašovací stránky žádný _context nemají.
templates.env.globals["udalost"] = udalosti.pro_sablonu
templates.env.globals["udalost_pohyb"] = udalosti.pohyb
templates.env.globals["udalost_rok"] = udalosti.rok_pro_sablonu
# Ukazkovy rezim pozna i sablona: prihlasovaci stranka v nem rovnou rekne,
# jakymi udaji se dovnitr. Je to globalni promenna, protoze prihlasovaci
# stranka zadny vlastni kontext nema.
templates.env.globals["demo_mode"] = config.demo_mode


# Kód chyby u HTTP chyb API, které nevznikly jako ChybaApi (neznámá adresa,
# jiná metoda než GET) - program pozná, co je špatně, bez čtení věty.
KODY_HTTP_CHYB = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                  405: "method_not_allowed"}


@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    """Presmerovani misto chybove hlasky u nepřihlaseneho uzivatele."""
    if exc.status_code == 307 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    # API se ptaji programy (Grafana, Homepage), ne lide: chybova stranka
    # v HTML by jim nerekla nic. Tam je kazda chyba JSON s duvodem.
    api_pozadavek = request.url.path.startswith("/api/")
    if exc.status_code == 403 and not api_pozadavek:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 403, "message": exc.detail}, status_code=403,
        )
    if exc.status_code == 404 and not api_pozadavek:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 404, "message": exc.detail or "Stranka nenalezena."},
            status_code=404,
        )
    # Hlavičky výjimky patří do odpovědi. U 401 z API je v nich
    # `WWW-Authenticate: Bearer`, tedy jediná věta, která volajícímu
    # řekne, čím se má prokázat - bez ní je to jen "nesmíš".
    telo: dict[str, Any] = {"detail": exc.detail}
    if api_pozadavek:
        telo["error"] = KODY_HTTP_CHYB.get(exc.status_code, "http_error")
    return JSONResponse(telo, status_code=exc.status_code,
                        headers=exc.headers or None)


@app.exception_handler(api_dotaz.ChybaApi)
async def handle_api_error(request: Request, exc: api_dotaz.ChybaApi):
    """Chyba API: vždy JSON se stejným tělem (`detail` + `error`).

    Program podle `error` pozná, co je špatně, bez čtení věty. U 401 jde
    s odpovědí i `WWW-Authenticate: Bearer`.
    """
    return JSONResponse(exc.telo(), status_code=exc.status, headers=exc.hlavicky)


# Routy bydlí v podmodulech podle oblastí; podmoduly si berou ze `zaklad`,
# ne z tohohle souboru, takže nic nevzniká dokola. Na pořadí nezáleží - žádné
# dvě routy nemají stejnou adresu se stejnou metodou (tests/test_web_balik.py).
app.include_router(prihlaseni.router)
app.include_router(ucet.router)
app.include_router(prehled.router)
app.include_router(knihovna.router)
app.include_router(uzivatele.router)
app.include_router(historie.router)
app.include_router(jazyky.router)
app.include_router(sit.router)
app.include_router(stav.router)
app.include_router(nastaveni.router)

# Nástroje jen pro vývoj (jellyscope/vyvoj.py - datum pro sezónní výzdobu).
# Do produ a dema se nekopírují (deploy/sync-do-produ.py, JEN_DEV), takže
# tam soubor chybí a tady se jen přeskočí. Demo spuštěné z dev složky
# (demo.py) ho sice má, ale přepínání data tam nepatří. find_spec, ne
# `except ImportError`: chyba UVNITŘ vyvoj.py se nemá tiše spolknout.
if not config.demo_mode and importlib.util.find_spec("..vyvoj", __package__) is not None:
    from .. import vyvoj  # noqa: E402
    vyvoj.zapoj(app, templates)
else:
    vyvoj = None
