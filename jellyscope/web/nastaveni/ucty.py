"""Účty, práva, čtenáři, diváci, blokace adres a API klíče.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import accounts, api, db, pristup
from ..zaklad import _cislo_z_formulare, _flash, log, require_admin, require_login

router = APIRouter()


def _uloz_strom(formular: Any, kdo: str) -> None:
    """Uloží zaškrtnutý strom jedné skupiny.

    Čte se z formuláře přímo, ne přes pojmenované parametry: oblastí
    přibývá a podepsat se pod každou zvlášť znamená, že se na jednu
    zapomene - a ta pak půjde jen vypnout, ne zapnout.
    """
    db.set_setting(f"{kdo}_anonymizace",
                   "1" if formular.get("anonymizace") else "0")
    for oblast in pristup.OBLASTI:
        db.set_setting(pristup.klic_oblasti(kdo, oblast),
                       "1" if formular.get(f"vidi_{oblast.klic}") else "0")


@router.post("/settings/accounts/klice")
def settings_klice(request: Request, ctenar_smi_klic: str = Form(""),
                   divak_smi_klic: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Kdo si smí vyrobit vlastní API klíč - výchozí hodnota skupin.

    Nezaškrtnuté zaškrtávátko prohlížeč vůbec nepošle, proto prázdno = ne.
    Výjimky u jednotlivých účtů (okno Práva) platí dál.
    """
    for kdo, hodnota in ((pristup.CTENAR, ctenar_smi_klic),
                         (pristup.DIVAK, divak_smi_klic)):
        db.set_setting(pristup.SMI_KLIC_KLIC.format(kdo=kdo), "1" if hodnota else "0")
    db.forget_settings()
    _flash(request, "Uloženo. Kdo má u účtu výjimku, toho se to netýká.", "success")
    return RedirectResponse("/settings?section=accounts#klice", status_code=303)


# Volba „Vlastní API klíče" v okně Práva -> hodnota sloupce accounts.smi_klic.
VOLBY_KLICE = {"skupina": None, "ano": "1", "ne": "0"}


@router.post("/settings/accounts/prava")
async def settings_prava_uctu(request: Request,
                              account: dict[str, Any] = Depends(require_admin)):
    """Vlastní práva jednoho účtu - nebo návrat ke skupině.

    Zaškrtávátka se čtou z formuláře přímo, ze stejného důvodu jako
    u skupin (viz `_uloz_strom`). Účet správce vlastní práva nemá:
    správce vidí všechno a strom by u něj jen mátl.
    """
    formular = await request.form()
    try:
        account_id = int(str(formular.get("account_id") or 0))
    except ValueError:
        account_id = 0
    cil = accounts.get(account_id)
    if cil is None or cil.get("is_admin"):
        _flash(request, "Tenhle účet vlastní práva nemá.", "error")
        return RedirectResponse("/settings?section=accounts", status_code=303)

    # Vlastni API klic se uklada v obou rezimech - je to jina otazka nez
    # strom stranek. Formular bez te volby (starsi stranka v otevrene
    # karte) vyjimku necha, jak byla.
    volba_klice = str(formular.get("smi_klic") or "")
    if volba_klice in VOLBY_KLICE:
        accounts.uloz_smi_klic(account_id, VOLBY_KLICE[volba_klice])

    if str(formular.get("rezim") or "") == "vlastni":
        prava = pristup.zabal_prava(
            (oblast.klic for oblast in pristup.OBLASTI
             if formular.get(f"vidi_{oblast.klic}")),
            bool(formular.get("anonymizace")))
        accounts.uloz_prava(account_id, prava)
        _flash(request, "Uloženo. Účet {jmeno} má vlastní práva, nastavení skupiny se ho už netýká.",
               "success", jmeno=cil["username"])
    else:
        accounts.uloz_prava(account_id, None)
        _flash(request, "Uloženo. Účet {jmeno} se zase řídí nastavením své skupiny.",
               "success", jmeno=cil["username"])
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/ctenari")
async def settings_ctenari(request: Request,
                           account: dict[str, Any] = Depends(require_admin)):
    """Co uvidí místní čtenářské účty."""
    formular = await request.form()
    _uloz_strom(formular, pristup.CTENAR)
    db.forget_settings()
    _flash(request, "Uloženo. Takhle uvidí statistiky čtenářské účty.",
           "success")
    return RedirectResponse("/settings?section=accounts#ctenari",
                            status_code=303)


@router.post("/settings/divaci")
async def settings_divaci(request: Request,
                          account: dict[str, Any] = Depends(require_admin)):
    """Uloží, kdo z Jellyfinu smí dovnitř a co uvidí.

    Zaškrtávátka se čtou z formuláře přímo, ne přes pojmenované
    parametry: oblastí přibývá a podepsat se pod každou zvlášť znamená,
    že se na jednu zapomene - a ta pak půjde jen vypnout, ne zapnout.
    """
    formular = await request.form()
    db.set_setting(pristup.LOGIN_KLIC,
                   "1" if formular.get("jellyfin_login") else "0")
    _uloz_strom(formular, pristup.DIVAK)
    db.forget_settings()

    if not pristup.login_zapnuty():
        _flash(request, "Uloženo. Jellyfinové účty dovnitř nesmí, přihlásit se jde jen místními "
                        "účty.", "success")
    else:
        _flash(request, "Uloženo. Diváci z Jellyfinu se přihlásí svým heslem.",
               "success")
    return RedirectResponse("/settings?section=jellyfin#divaci",
                            status_code=303)


@router.post("/settings/blocks/unblock")
def blocks_unblock(request: Request, ip: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Zruší blokaci přihlašování pro jednu adresu."""
    if accounts.odblokuj(ip.strip()):
        _flash(request, "Adresa {ip} je odblokovaná.", "success", ip=ip.strip())
    else:
        _flash(request, "Taková blokace v seznamu není.", "warning")
    return RedirectResponse("/settings?section=blocks", status_code=303)


@router.post("/settings/blocks/permanent")
def blocks_permanent(request: Request, ip: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Zablokuje adresu natrvalo - dokud ji správce sám nepustí."""
    adresa = ip.strip()
    if not adresa:
        _flash(request, "Chybí adresa.", "error")
    else:
        accounts.zablokuj_natrvalo(adresa)
        _flash(request, "Adresa {ip} je zablokovaná natrvalo.", "success",
               ip=adresa)
    return RedirectResponse("/settings?section=blocks", status_code=303)


# ---------------------------------------------------------------------------
# Sprava uctu
#
# Vsimni si, ze kazda akce ma vlastni adresu a posila se metodou POST.
# Mazani pres GET (treba odkazem /smazat?id=3) je klasicka chyba: takovou
# adresu si prohlizec muze nacist sam, treba pri predbeznem nacitani odkazu,
# a ucet zmizi bez toho, aby na neco nekdo klikl.
# ---------------------------------------------------------------------------

@router.post("/settings/accounts/create")
def account_create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_again: str = Form(""),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.create(username, password, password_again, is_admin=bool(is_admin))
        _flash(request, "Účet '{jmeno}' vytvořen.", "success", jmeno=username)
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/accounts/password")
def account_password(
    request: Request,
    account_id: int = Form(...),
    password: str = Form(""),
    password_again: str = Form(""),
    account: dict[str, Any] = Depends(require_login),
):
    """Zmena hesla jineho uctu (spravce).

    Vlastni heslo sem nepatri: meni se na strance Muj ucet, kde se chce
    soucasne heslo. Tady se nechce - spravce tudy pomaha tomu, kdo heslo
    zapomnel - takze kdyby slo i o vlastni, obesel by se tim pozadavek na
    soucasne heslo (a spravce z Jellyfinu by si nastavil mistni heslo).
    """
    if account_id == account["id"]:
        _flash(request, "Vlastní heslo se mění na stránce Můj účet.", "info")
        return RedirectResponse("/account", status_code=303)
    if not account["is_admin"]:
        raise HTTPException(status_code=403, detail="Cizi heslo smi menit jen spravce.")

    try:
        accounts.set_password(account_id, password, password_again)
        _flash(request, "Heslo změněno.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/accounts/role")
def account_role(
    request: Request,
    account_id: int = Form(...),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.set_admin(account_id, bool(is_admin))
        _flash(request, "Oprávnění změněno.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


def _strom_klice(oblasti: Any, historie: bool, anonymizace: bool,
                 vse: bool) -> dict[str, Any]:
    """Práva root klíče pro stránku: stav okna Práva a výčet do tabulky.

    Strom kreslí pristup.strom stejně jako u účtu s vlastními právy -
    jen z účtu, který existuje jen na tuhle chvíli.
    """
    zapnute = set(oblasti)
    return {
        "vse": vse,
        "historie": historie,
        "anonymizace": anonymizace,
        "strom": pristup.strom(pristup.CTENAR, {
            pristup.PRISTUP: pristup.zabal_prava(zapnute, anonymizace)}),
        "oblasti": [oblast for oblast in pristup.OBLASTI if oblast.klic in zapnute],
    }


def _prava_root_klice(prava: str | None) -> dict[str, Any]:
    """Co smí jeden root klíč (sloupec Typ a práva, okno Práva).

    U „všechno" je strom zaškrtnutý celý: přesně to klíč smí, a kdo
    přepne na „jen vybrané", ubírá z toho, co už platí - nezačíná od nuly.
    """
    rozbalena = api.prava_klice(prava)
    if rozbalena is None:
        return _strom_klice(pristup.PODLE_KLICE, historie=True, anonymizace=False, vse=True)
    return _strom_klice(rozbalena["oblasti"], rozbalena["historie"],
                        rozbalena["anonymizace"], vse=False)


def _prava_klice_z_formulare(formular: Any) -> tuple[str | None, str]:
    """Práva root klíče z formuláře: (práva, chyba). Prázdná chyba = v pořádku.

    `rezim` je „vse" (práva NULL - všechno, i oblasti přidané později),
    nebo „vybrane". Cokoliv jiného - i formulář bez něj ze starší stránky
    v otevřené kartě - je chyba: hádat, co správce myslel, by u klíče
    k datům znamenalo dát mu víc, než chtěl. Neznámé oblasti zahodí
    api.zabal_prava_klice.
    """
    rezim = str(formular.get("rezim") or "")
    if rezim == "vse":
        return None, ""
    if rezim != "vybrane":
        return None, "Nejde poznat, co má klíč smět. Načti stránku znovu a vyber to."
    oblasti = [oblast.klic for oblast in pristup.OBLASTI
               if formular.get(f"vidi_{oblast.klic}")]
    historie = bool(formular.get("historie"))
    if not oblasti and not historie:
        return None, "Zaškrtni, co klíč smí - aspoň jednu oblast nebo historii přehrávání."
    prava = api.zabal_prava_klice(oblasti, bool(formular.get("anonymizace")), historie)
    # Jazyky, vlastní přehled ani srovnání nemají v API adresu - klíč jen
    # s nimi by nesměl nic a nikdo by nevěděl proč.
    if not api.adresy_root_klice(prava):
        return None, ("Zaškrtnuté oblasti v API nic neotevírají. Přidej oblast, která má"
                      " adresu v API (třeba Knihovnu nebo Seznam diváků), nebo historii"
                      " přehrávání.")
    return prava, ""


@router.post("/settings/api/token")
async def api_token_novy(request: Request,
                         account: dict[str, Any] = Depends(require_admin)):
    """Vyrobí root klíč s právy z formuláře a jednou ho ukáže.

    Root klíč nepatří žádnému účtu - ani správci, který ho vyrobil. Smí
    jen to, co mu tu správce nastaví, a změna nebo smazání účtu správce
    ho neovlivní. Vlastní klíč (s právy svého účtu) se vyrábí v Můj účet.
    """
    formular = await request.form()
    prava, chyba = _prava_klice_z_formulare(formular)
    if chyba:
        _flash(request, chyba, "error")
        return RedirectResponse("/settings?section=api", status_code=303)
    # Jméno jen jako text: soubor poslaný pod tímhle polem by se jinak
    # stal jménem klíče („UploadFile(...)").
    jmeno = formular.get("name")
    vysledek = api.vytvor(jmeno if isinstance(jmeno, str) else "", None, prava)
    log.info("Spravce %s vyrobil root klic %s (%s)", account["username"],
             vysledek["jmeno"], api.popis_prav_do_logu(prava))
    # Do relace, ne do adresy: adresa se objeví v logu proxy i v historii
    # prohlížeče, a tohle je jediná chvíle, kdy je token čitelný.
    request.session["novy_api_token"] = {"id": vysledek["id"], "token": vysledek["token"]}
    _flash(request, "Klíč „{jmeno}“ vznikl.", "success",
           jmeno=vysledek["jmeno"])
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/prava")
async def api_token_prava(request: Request,
                          account: dict[str, Any] = Depends(require_admin)):
    """Změní práva root klíče. Platí od dalšího dotazu s ním.

    Jen root klíč: uživatelský se řídí právy svého účtu a tudy se změnit
    nedá (api.uloz_prava_klice ho odmítne). Kdo, co a jak bylo předtím,
    jde do aplikačního logu - v tabulce klíčů se to neukládá.
    """
    formular = await request.form()
    prava, chyba = _prava_klice_z_formulare(formular)
    if chyba:
        _flash(request, chyba, "error")
        return RedirectResponse("/settings?section=api", status_code=303)
    cislo = _cislo_z_formulare(formular.get("token_id"))
    zmena = api.uloz_prava_klice(cislo, prava) if cislo else None
    if zmena is None:
        _flash(request, "Takový root klíč tu není. Uživatelský klíč se řídí právy svého účtu.",
               "error")
        return RedirectResponse("/settings?section=api", status_code=303)
    log.info("Spravce %s zmenil prava root klice %s: %s -> %s", account["username"],
             zmena["jmeno"], api.popis_prav_do_logu(zmena["pred"]),
             api.popis_prav_do_logu(prava))
    _flash(request, "Práva klíče „{jmeno}“ jsou uložená. Platí od dalšího dotazu.",
           "success", jmeno=zmena["jmeno"])
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/zrusit")
def api_token_zrusit(request: Request, token_id: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Zneplatní jeden token - i cizí. Ostatních se to nedotkne."""
    cislo = _cislo_z_formulare(token_id)
    jmeno = api.zrus(cislo) if cislo else ""
    if jmeno:
        _flash(request, "Klíč „{jmeno}“ už neplatí.", "success",
               jmeno=jmeno)
    else:
        _flash(request, "Takový klíč tu není.", "info")
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/vypnout")
def api_token_vypnout(request: Request, token_id: str = Form(""),
                      account: dict[str, Any] = Depends(require_admin)):
    """Vypne klíč, ale nesmaže ho.

    Na chvíli, kdy je podezření, že klíč unikl: přestane fungovat hned,
    ale zůstane i se záznamy použití, takže se dá dohledat, kdo a odkud
    se s ním ptal - a když to byl planý poplach, zase zapnout.
    """
    cislo = _cislo_z_formulare(token_id)
    jmeno = api.vypni(cislo) if cislo else ""
    if jmeno:
        _flash(request, "Klíč „{jmeno}“ je vypnutý. Dokud ho nezapneš, API mu nic nevrátí.",
               "success", jmeno=jmeno)
    else:
        _flash(request, "Takový klíč tu není.", "info")
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/zapnout")
def api_token_zapnout(request: Request, token_id: str = Form(""),
                      account: dict[str, Any] = Depends(require_admin)):
    """Vypnutý klíč zase pustí k datům."""
    cislo = _cislo_z_formulare(token_id)
    jmeno = api.zapni(cislo) if cislo else ""
    if jmeno:
        _flash(request, "Klíč „{jmeno}“ zase funguje.", "success", jmeno=jmeno)
    else:
        _flash(request, "Takový klíč tu není.", "info")
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/accounts/delete")
def account_delete(
    request: Request,
    account_id: int = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    if account_id == account["id"]:
        _flash(request, "Vlastní účet smazat nemůžeš.", "warning")
        return RedirectResponse("/settings?section=accounts", status_code=303)

    try:
        accounts.delete(account_id)
        _flash(request, "Účet smazán.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)
