"""Přehled a jeho karty, srovnání dvou období, vlastní přehledy a Postřehy.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import pristup, sekce, stats
from ..i18n import translate as _t
from .zaklad import (
    _context,
    _flash,
    _obdobi_do_sablony,
    _posledni_den,
    require_admin,
    require_login,
    templates,
)
from .obdobi import (
    ALLOWED_DAYS,
    DEFAULT_DAYS,
    TOP_KIND_SESSION_KEY,
    _datum_z_textu,
    _days,
    _kind,
    _obdobi_do_session,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Stranky
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, days: int | None = None, kind: str | None = None,
              od: str | None = None, do: str | None = None,
              top_kind: str | None = None,
              account: dict[str, Any] = Depends(require_login)):
    """Přehled: pevné rozvržení (dashboard.html), karty z registru.

    Které karty kdo uvidí, rozhodne registr (sekce.povolene podle oblasti
    každé karty) a spočítají se jen jejich data - stejně jako ve vlastním
    přehledu. Dřív se tu počítalo všech deset karet pro každého a karty
    z jiných oblastí se hlídaly ručně; dvě tak proklouzly bez hlídání.
    """
    days = _days(request, days, od, do)
    povolene = sekce.povolene([sekce.PODLE_KLICE[k] for k in sekce.PREHLED], account)
    return templates.TemplateResponse(request, "dashboard.html", _context(
        request, account,
        days=days,
        karty={karta.klic: karta for karta in povolene},
        **sekce.data_pro(povolene, days, _volby_karet(request, kind, top_kind)),
    ))


def _volby_karet(request: Request, kind: str | None = None,
                 top_kind: str | None = None) -> dict[str, str]:
    """Volby karet Filmy/Seriály z adresy, jinak z relace (viz _kind).

    Pro Přehled i vlastní přehled - je to tatáž karta, takže volba platí
    na obou stránkách.
    """
    return {"kind": _kind(request, kind),
            "top_kind": _kind(request, top_kind, TOP_KIND_SESSION_KEY)}


# ---------------------------------------------------------------------------
# Srovnani dvou obdobi
# ---------------------------------------------------------------------------
#
# Vlastni pamet, ne ta spolecna: stranka ma obdobi dve a spolecny filtr
# nese jedno. Kdyby si bralo to spolecne, prepnuti tady by prehodilo
# obdobi i na vsech ostatnich strankach - a naopak.
SROVNANI_A_KEY = "srovnani_a"
SROVNANI_B_KEY = "srovnani_b"


def _obdobi_srovnani(request: Request, klic: str, days: int | None,
                     od: str | None, do: str | None, vychozi: Any) -> Any:
    """Jedno ze dvou obdobi na strance Srovnani.

    Chova se jako `_days()`, jen si pamatuje pod vlastnim klicem a nezna
    vyber tazenim v grafu - na teto strance zadny graf k tazeni neni.
    """
    if od or do:
        obdobi = stats.obdobi_od_do(_datum_z_textu(od), _datum_z_textu(do))
        if obdobi is not None:
            request.session[klic] = _obdobi_do_session(obdobi)
            return obdobi

    if days in ALLOWED_DAYS:
        request.session[klic] = int(days)
        return int(days)

    zapamatovane = request.session.get(klic)
    if isinstance(zapamatovane, dict):
        obdobi = stats.obdobi_od_do(zapamatovane.get("od"), zapamatovane.get("do"))
        if obdobi is not None:
            return obdobi
    if zapamatovane in ALLOWED_DAYS:
        return int(zapamatovane)

    return vychozi


def _dotaz_obdobi(predpona: str, zadani: Any) -> dict[str, str]:
    """Obdobi jako dvojice do adresy - aby odkazy jednoho to druhe neztratily.

    Kdyby v odkazu chybelo, vratilo by se pri kazdem prepnuti na to, co je
    v pameti, a vyber druheho obdobi by pod rukama skakal.

    Vraci se slovnik, ne hotovy retezec: sablona ho potrebuje dvakrat -
    v adrese odkazu (`| urlencode`) a jako skryta pole formulare.
    """
    if isinstance(zadani, stats.Obdobi):
        od = (zadani.od_mistni or zadani.od)[:10]
        do = _posledni_den(zadani.do_mistni or zadani.do)
        return {f"{predpona}_od": od, f"{predpona}_do": do}
    return {f"{predpona}_days": str(int(zadani))}


@router.get("/srovnani", response_class=HTMLResponse)
def srovnani(request: Request,
             a_days: int | None = None, a_od: str | None = None,
             a_do: str | None = None,
             b_days: int | None = None, b_od: str | None = None,
             b_do: str | None = None,
             account: dict[str, Any] = Depends(require_login)):
    """Dve libovolna obdobi vedle sebe.

    Vychozi dvojice je to, co uz clovek zna z Prehledu: zvolene obdobi
    a stejne dlouhe okno pred nim. Stranka tak neco ukazuje hned a teprve
    kdo chce "srpen versus prosinec", saha na prepinace.
    """
    vychozi = _days(request, None)
    if getattr(vychozi, "cela_historie", False):
        # Cela historie nema stejne dlouhe okno pred sebou - pred prvnim
        # prehranim nic neni a srovnavalo by se vsechno s prazdnem.
        vychozi = DEFAULT_DAYS
    prvni = _obdobi_srovnani(request, SROVNANI_A_KEY, a_days, a_od, a_do,
                             vychozi=vychozi)
    druhe = _obdobi_srovnani(request, SROVNANI_B_KEY, b_days, b_od, b_do,
                             vychozi=stats.predchozi(prvni))

    return templates.TemplateResponse(request, "srovnani.html", _context(
        request, account,
        srovnani=stats.srovnani(prvni, druhe),
        obdobi_a=_obdobi_do_sablony(prvni),
        obdobi_b=_obdobi_do_sablony(druhe),
        # Do odkazu jednoho prepinace patri stav toho druheho.
        dotaz_a=_dotaz_obdobi("a", prvni),
        dotaz_b=_dotaz_obdobi("b", druhe),
    ))


@router.get("/partials/top-items", response_class=HTMLResponse)
def top_items_partial(
    request: Request,
    kind: str | None = None,
    days: int | None = None,
    # `od` a `do` tu chybely, prestoze se s nimi v tele pocitalo: volani
    # koncilo na NameError, tedy chybou 500. Nikdo si toho nevsiml,
    # protoze stranka po neuspechu nacte celou sebe (viz `.catch`
    # ve static/js/app.js) - filtr fungoval dal, jen pomalu a se skokem nahoru,
    # tedy presne tim, cemu se timhle vyrezem predchazelo.
    od: str | None = None,
    do: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Jen karta "Nejsledovanější tituly".

    Přepnutí filtru vymění tuhle jednu kartu, takže stránka zůstane tam,
    kde je. Dřív se načítala celá a odrolovala na začátek - a tahle karta
    je až v druhé polovině Přehledu.
    """
    return templates.TemplateResponse(request, "_top_items.html", _context(
        request, account,
        top_kind=_kind(request, kind, TOP_KIND_SESSION_KEY),
        top_items=stats.top_items(_days(request, days, od, do),
                                  kind=_kind(request, kind, TOP_KIND_SESSION_KEY)),
    ))


@router.get("/top-items", response_class=HTMLResponse)
def top_items_vse(
    request: Request,
    kind: str | None = None,
    days: int | None = None,
    od: str | None = None,
    do: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Celý žebříček nejsledovanějších - graf na Přehledu ukazuje deset.

    Jedna routa, dvě podoby: pro `fetch` z okna nad Přehledem vrací jen
    výřez (tabulku), pro obyčejné otevření celou stránku. Bez
    JavaScriptu tak o seznam nikdo nepřijde a obsah je v obou případech
    z téže šablony.

    Strop tisíc řádků není omezení, ale pojistka: seriály se sčítají,
    takže tisíc řádků znamená tisíc různých titulů za období.
    """
    druh = _kind(request, kind, TOP_KIND_SESSION_KEY)
    context = _context(
        request, account,
        top_kind=druh,
        top_items=stats.top_items(_days(request, days, od, do), limit=1000,
                                  kind=druh),
    )
    if request.headers.get("x-requested-with") == "fetch":
        return templates.TemplateResponse(request, "_top_items_vse.html", context)
    return templates.TemplateResponse(request, "top_items_vse.html", context)


@router.get("/partials/recently-added", response_class=HTMLResponse)
def recently_added_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Jen pás "Nedávno přidané".

    Dokud tohle nebylo, musel člověk po doběhnutí synchronizace stránku
    obnovit ručně - jinak se na už otevřeném Přehledu nový film neobjevil.
    Teď si ho stránka vymění sama, jakmile se změní otisk knihovny.

    Vrací se hotové HTML, ne JSON: kreslí se **stejnou šablonou** jako při
    běžném načtení, takže není druhé místo, které se časem rozejde s prvním.
    """
    return templates.TemplateResponse(request, "_recently_added.html", _context(
        request, account,
        recent=stats.recently_added(),
    ))


@router.get("/partials/network-live", response_class=HTMLResponse)
def network_live_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Jen karta "Právě teče" ze stránky Síť.

    Stejný nápad jako u "právě se hraje": stránka Síť ukazovala stav
    z okamžiku načtení, takže kdo ji nechal otevřenou, viděl minulost.
    Obnovuje se jen tenhle výřez - kdyby se načítala celá stránka,
    odrolovala by na začátek a zahodila rozečtenou tabulku adres.
    """
    # Obdobi se bere z relace - tam si ho ulozila stranka, kdyz ho clovek
    # vybral. Vyrez si ho tedy nemusi predavat v adrese a zustane platny
    # i po prepnuti filtru bez obnoveni stranky.
    days = _days(request, None)
    return templates.TemplateResponse(request, "_sit_zive.html", _context(
        request, account,
        days=days,
        ted=stats.tok_ted(),
        zive=stats.bandwidth_zive(days),
    ))


@router.get("/partials/now-playing", response_class=HTMLResponse)
def now_playing_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Jen karta "Právě se hraje".

    Sběrač se ptá Jellyfinu každých pár vteřin, ale stránka o tom nevěděla -
    ukazovala stav z okamžiku načtení, dokud ji člověk neobnovil ručně.
    Tenhle výřez si Přehled ve stejném rytmu vyzvedne sám.

    Vrací se hotové HTML, ne JSON: kreslí se **stejnou šablonou** jako při
    běžném načtení, takže není druhé místo, které se časem rozejde s prvním.
    """
    return templates.TemplateResponse(request, "_now_playing.html", _context(
        request, account,
        active=stats.active_sessions(),
    ))


@router.get("/partials/daily", response_class=HTMLResponse)
def daily_partial(
    request: Request,
    kind: str | None = None,
    days: int | None = None,
    od: str | None = None,
    do: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Jen karta "Sledovanost po dnech" - bez zbytku stranky.

    K cemu to je: prepnuti filmy/serialy drive znamenalo nacist celou
    stranku znovu, coz prohlizec odmenil skokem na zacatek. Tady se
    vymeni jen ta jedna karta, takze zustanes presne tam, kde jsi byl.

    Kresli se **stejnou sablonou** jako na Prehledu (_daily_card.html),
    ne jeji kopii. Kdyby to byly dva soubory, jeden z nich by casem
    odesel jinam a nikdo by si toho nevsiml.
    """
    days = _days(request, days, od, do)
    kind = _kind(request, kind)
    return templates.TemplateResponse(request, "_daily_card.html", _context(
        request, account,
        days=days,
        kind=kind,
        daily=stats.daily_activity_split(days),
        ostatni=stats.rozpad_ostatnich(days),
    ))


@router.get("/dashboard", response_class=HTMLResponse)
def vlastni_prehled(request: Request, days: int | None = None,
                    od: str | None = None, do: str | None = None,
                    account: dict[str, Any] = Depends(require_login)):
    """Přehled poskládaný z existujících sekcí.

    Vypnutý je jako by nebyl: záložka není v menu a adresa vede zpátky
    na Přehled. Zapíná se v Nastavení -> Rozhraní. Stejně tak pro toho,
    kdo v něm podle svých práv nemá co vidět (sekce.vidi_prehled, totéž
    co menu) - jen správce sem smí i s prázdným, aby si ho sestavil.
    """
    if not (sekce.vidi_prehled(account)
            or (account.get("is_admin") and sekce.je_zapnuty())):
        return RedirectResponse("/", status_code=303)
    return _vykresli_prehled(request, account, _days(request, days, od, do),
                             osobni=False)


@router.get("/account/prehled", response_class=HTMLResponse)
def muj_prehled(request: Request, days: int | None = None,
                od: str | None = None, do: str | None = None,
                account: dict[str, Any] = Depends(require_login)):
    """Můj přehled - stránka, kterou si účet poskládá sám a vidí jen on.

    Účet se bere z relace, nikdy z adresy: cizí Můj přehled se otevřít nedá.
    Karta, ke které účet mezitím ztratil oprávnění, zůstane na svém místě
    s hláškou proč - sám si ji přidal, tak nemá hádat, co se rozbilo.
    """
    return _vykresli_prehled(request, account, _days(request, days, od, do),
                             osobni=True)


def _vykresli_prehled(request: Request, account: dict[str, Any], obdobi: Any,
                      *, osobni: bool):
    """Stránka přehledu - pro všechny i Můj přehled, jedna šablona.

    Oba ukazují jen data karet, které účet smí; ostatní se ani nespočítají,
    jinak by přehled byl zadní vrátka na stránky, kam člověk nesmí. Přehled
    pro všechny takovou kartu vynechá, Můj přehled ji nechá na místě
    s hláškou proč.
    """
    if osobni:
        rozvrzeni = sekce.nacti_rozvrzeni(account["id"])
        povolene = sekce.povolene(rozvrzeni, account)
        nadpis, adresa_stranky = _t("Můj přehled"), "/account/prehled"
        akce_ulozeni, muze_upravovat = "/account/prehled", True
    else:
        rozvrzeni = povolene = sekce.povolene(sekce.nacti_rozvrzeni(), account)
        # Nadpis i titulek okna: název od správce. Prázdný jen u přehledu
        # zapnutého dřív, než se název zadával, a bez restartu (migrace
        # ho doplní při startu) - pak to, jak se mu říkalo.
        nadpis, adresa_stranky = sekce.nazev() or _t("Vlastní přehled"), "/dashboard"
        # V ukázce tlačítko zůstává vidět schválně - stejně jako všude
        # jinde. Akci zastaví až `_demo_blokuje` a řekne to nahlas.
        akce_ulozeni, muze_upravovat = "/dashboard/layout", bool(account.get("is_admin"))
    # Spočítá se JEN to, co je poskládané a smí se - stejně jako na
    # Přehledu (routa dashboard), který jede přes tentýž registr.
    data = sekce.data_pro(povolene, obdobi, _volby_karet(request))
    return templates.TemplateResponse(request, "vlastni_prehled.html", _context(
        request, account,
        days=obdobi,
        nadpis=nadpis,
        # Karty poznají, že nejsou na své stránce (mapa nenabízí stažení
        # GeoIP, tabulka uživatelů má jiný nadpis).
        v_prehledu=True,
        akce_ulozeni=akce_ulozeni,
        adresa_stranky=adresa_stranky,
        osobni=osobni,
        rozvrzeni=rozvrzeni,
        bez_prava={s.klic for s in rozvrzeni} - {s.klic for s in povolene},
        # Názvy oblastí pro hlášku „nemáš oprávnění k oblasti …".
        oblasti=pristup.PODLE_KLICE,
        # Do okna jde celý registr, co účet smí; co už v přehledu je, se
        # jen zašedne. Kdyby se použité sekce vynechávaly, seznam by při
        # přidávání a odebírání poskakoval a člověk by ztrácel místo.
        # Jen pro toho, kdo smí skládat - jinak se okno ani nekreslí.
        vsechny_sekce=sekce.povolene(list(sekce.SEZNAM), account) if muze_upravovat else [],
        pouzite={s.klic for s in rozvrzeni},
        sirky=sekce.SIRKY,
        # Přepínač období má smysl jen tehdy, když ho aspoň jedna karta
        # používá - jinak by tam stál a nic nedělal.
        potrebuje_obdobi=any(s.obdobi for s in povolene),
        # Karty Zjištění ukážou pár řádků a zbytek otevřou ve společném okně.
        potrebuje_okno_tabulka=any(s.okno_tabulka for s in povolene),
        muze_upravovat=muze_upravovat,
        **data,
    ))


@router.post("/dashboard/layout")
def vlastni_prehled_uloz(request: Request, poradi: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Uloží poskládané pořadí sekcí.

    Chodí sem "klíč:šířka" oddělené čárkami - přesně to, co drží skryté
    pole formuláře, které při přeskládání aktualizuje JavaScript.
    """
    ulozene = sekce.uloz_rozvrzeni(poradi or "")
    _flash(request, "Uloženo." if ulozene else "Přehled je teď prázdný.",
           "success" if ulozene else "info")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/account/prehled")
def muj_prehled_uloz(request: Request, poradi: str = Form(""),
                     account: dict[str, Any] = Depends(require_login)):
    """Uloží Můj přehled - vždycky účtu z relace, `account_id` z formuláře
    se nečte vůbec.

    Uložit jde karta, kterou účet smí, nebo karta, která v jeho přehledu
    už je (i bez práva - jinak by se přeskládáním potichu ztratila). Cizí
    kartu si podvrženým formulářem nepřidá: spadla by mezi „bez práva"
    a ukazovala jen hlášku, ale do databáze nepatří.
    """
    smi = ({s.klic for s in sekce.povolene(list(sekce.SEZNAM), account)}
           | {s.klic for s in sekce.nacti_rozvrzeni(account["id"])})
    polozky = [(klic, sirka) for klic, sirka in sekce.rozdel_zadani(poradi or "")
               if klic in smi]
    ulozene = sekce.uloz_rozvrzeni(polozky, account["id"])
    _flash(request, "Uloženo." if ulozene else "Přehled je teď prázdný.",
           "success" if ulozene else "info")
    return RedirectResponse("/account/prehled", status_code=303)


@router.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, days: int | None = None,
                  od: str | None = None, do: str | None = None,
                  account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, od, do)
    # Stránka je složená z karet Zjištění, data bere z jejich registru.
    return templates.TemplateResponse(request, "insights.html", _context(
        request, account, days=days, **sekce.data_oblasti("zebricky", days)))
