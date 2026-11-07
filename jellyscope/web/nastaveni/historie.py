"""Import historie, narovnání dat, sirotci, zapomenutí diváka.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Any

from ... import importers, odklizeni, tasks
from ...i18n import translate as _t
from ..zaklad import (
    ZPET_NA_IMPORT,
    _clamp,
    _context,
    _flash,
    _hlaska_importu,
    require_admin,
    templates,
)
from .spolecne import _import_ze_souboru

router = APIRouter()


@router.post("/settings/historie/zapomen")
async def historie_zapomen(request: Request, user_id: str = Form(""),
                           account: dict[str, Any] = Depends(require_admin)):
    """Smaže historii jednoho diváka. Účet zůstává v Jellyfinu.

    Vlastní routa, ne součást formuláře úloh: je to jednorázový zásah do
    dat, ne nastavení, a nemá se stát mimochodem při ukládání něčeho
    jiného.

    Pořadí je pevné: nejdřív záloha, pak mazání. Když se záloha nepovede,
    nemaže se - kdo klikl na špatné jméno, má odkud se vrátit.
    """
    user_id = (user_id or "").strip()
    if not odklizeni.pocet_relaci(user_id):
        _flash(request, "Nebylo co zapomenout, k tomu divákovi nic nemáme.",
               "info")
        return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request,
               "Historie se nesmazala, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)

    # Do kose, ne rovnou pryc: ze statistik je divak hned, ale do noci
    # se da zasah vzit zpet - viz odklizeni.vrat_z_kose(). Presun bezi
    # ve vlakne, at velka historie nezastavi obsluhu ostatnich stranek.
    vysledek = await asyncio.to_thread(
        odklizeni.zapomen_uzivatele, user_id, True, str(zaloha.get("file") or ""))
    # Log uz zapsalo `odklizeni.zapomen_uzivatele()`. Druhy radek
    # o teze akci by v logu jen prekazel.
    kdy = tasks.kdy_odlozene_preskladani()
    _flash(request,
           "Historie diváka {jmeno} je pryč ze statistik ({n} přehrávání). Do {kdy} ji jde "
           "vrátit zpět, potom bude smazaná nadobro.",
           "success",
           jmeno=vysledek["jmeno"] or user_id, n=vysledek["smazano"],
           kdy=kdy.strftime("%d.%m. %H:%M") if kdy else "?")
    return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)


@router.post("/settings/historie/vrat")
async def historie_vrat(request: Request, udalost: str = Form(""),
                        account: dict[str, Any] = Depends(require_admin)):
    """Vrátí do historie diváka, který je v koši.

    Není to nebezpečná akce - nic se nemaže, jen se vrací, co se před
    chvílí odklidilo - takže se na nic neptá a nemá vlastní okno.
    """
    vysledek = await asyncio.to_thread(odklizeni.vrat_z_kose, udalost)
    if not vysledek["vraceno"]:
        _flash(request,
               "Vrátit se nepodařilo, v koši už nic takového není.", "error")
    else:
        _flash(request, "Historie diváka {jmeno} je zpátky ({n} přehrávání).",
               "success", jmeno=vysledek["jmeno"], n=vysledek["vraceno"])
    return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)


@router.post("/settings/import/detect")
async def import_detect(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Zjisti, jestli je v Jellyfinu plugin Playback Reporting."""
    available, message = await importers.playback_reporting_available()
    _flash(request, message, "success" if available else "warning")
    return RedirectResponse(ZPET_NA_IMPORT, status_code=303)


@router.post("/settings/import/playback-reporting")
async def import_playback_reporting(
    request: Request,
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    result = await importers.import_playback_reporting(
        min_seconds=int(_clamp(min_seconds, 0, 3600, 60))
    )
    _hlaska_importu(
        request, result,
        "Playback Reporting: naimportováno {n} záznamů "
        "(z {nalezeno} nalezených, {duplicit} už existovalo).")

    return RedirectResponse(ZPET_NA_IMPORT, status_code=303)


@router.post("/settings/import/playback-reporting-file")
async def import_playback_reporting_file(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Import ze zalohy pluginu Playback Reporting (soubor TSV).

    Zaloha pro pripad, kdy plugin pres API nefunguje - viz
    importers.import_playback_reporting_tsv().
    """
    return await _import_ze_souboru(
        request, backup, min_seconds, importers.import_playback_reporting_tsv,
        "Playback Reporting (záloha): naimportováno {n} záznamů "
        "(z {nalezeno} nalezených, {duplicit} už existovalo).")


@router.post("/settings/history/tidy")
async def history_tidy(request: Request,
                       account: dict[str, Any] = Depends(require_admin)):
    """Narovnání dat - jedno tlačítko pro celý úklid historie.

    Dřív to byly dvě akce a člověk musel vědět, kterou pustit dřív.
    Pořadí přitom není na výběr: dohledání v Jellyfinu vyrábí vazby, se
    kterými pracuje všechno ostatní. Proto jedna routa - a stejnou funkci
    pouští i naplánovaná úloha, takže se ruční a automatický běh nemůžou
    rozejít.

    Pouštět se to dá opakovaně; podruhé už nenajde nic.
    """
    vysledek = await importers.narovnej_data()

    jf = vysledek.get("jellyfin") or {}
    if jf.get("status") == "error":
        # Jellyfin je jen první krok. Zbytek proběhl, takže to není chyba
        # celé akce - ale musí to být vidět, jinak by člověk marně čekal,
        # že se osiřelé záznamy dohledají.
        _flash(request, "Jellyfin neodpověděl ({duvod}), zbytek proběhl.",
               "warning", duvod=jf.get("message", "?"))
    elif not vysledek["casti"]:
        _flash(request, "Nebylo co narovnávat, historie je v pořádku.", "success")
    else:
        vety = [_t(sablona).format(**hodnoty)
                for sablona, hodnoty in vysledek["casti"]]
        zprava = ", ".join(vety)
        if vysledek["zbyva"]:
            zprava += ". " + _t("Zbývá {n} nezařazených záznamů.").format(
                n=vysledek["zbyva"])
        _flash(request, "Narovnání dat: {co}", "success", co=zprava)

    return RedirectResponse("/settings?section=tasks#uklid", status_code=303)


@router.get("/settings/history/orphans", response_class=HTMLResponse)
def history_orphans(request: Request, priradit: str = "", q: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """Seznam záznamů, které se nepodařilo zařadit - i s důvodem.

    Nic se nikam neukládá, seznam se počítá pokaždé znovu. Uložený by
    ukazoval stav po posledním úklidu, tedy něco, co už nemusí platit -
    mezitím mohla proběhnout synchronizace nebo další import.
    """
    osirele = importers.rozbor_osirelych()
    vybrany = None
    if priradit:
        vybrany = next((o for o in osirele if str(o["item_id"]) == priradit), None)

    return templates.TemplateResponse(request, "orphans.html", _context(
        request, account,
        orphans=osirele,
        duvody=importers.DUVODY_POPIS,
        vybrany=vybrany,
        hledat=q,
        kandidati=importers.kandidati_pro_osireleho(q) if vybrany else [],
    ))


@router.post("/settings/history/assign")
def history_assign(request: Request, item_id: str = Form(""),
                   target_id: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Ruční přiřazení osiřelých záznamů k položce z knihovny."""
    vysledek = importers.prirad_rucne(item_id.strip(), target_id.strip())
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
    else:
        _flash(request, "Přiřazeno k „{nazev}“: {n} záznamů.", "success",
               nazev=vysledek["name"], n=vysledek["rows"])
    return RedirectResponse("/settings/history/orphans", status_code=303)


@router.post("/settings/import/jellystat")
async def import_jellystat(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Import z nahraneho JSON souboru se zalohou Jellystatu."""
    return await _import_ze_souboru(
        request, backup, min_seconds, importers.import_jellystat_json,
        "Jellystat: naimportováno {n} záznamů "
        "(z {nalezeno} nalezených, {duplicit} už existovalo).")
