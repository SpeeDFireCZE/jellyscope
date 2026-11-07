"""Síť: stránka, GeoIP a mapa (podrobná mapa, dlaždice).

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import formatting, geoip, i18n, mapa_podrobna, scanner, sekce
from .zaklad import _context, _flash, require_admin, require_login, templates
from .obdobi import _days

router = APIRouter()


@router.get("/network", response_class=HTMLResponse)
def network_page(request: Request, days: int | None = None,
                 od: str | None = None, do: str | None = None,
                 account: dict[str, Any] = Depends(require_login)):
    """Sit - kolik dat teklo ze serveru k prehravacum.

    Vsechno se pocita z `playback.bitrate`, ktery sberac uklada u kazdeho
    prehravani. Zadny novy sber to nepotrebuje - jen jina otazka nad
    daty, ktera uz mame.
    """
    days = _days(request, days, od, do)
    # Stránka je složená z karet Sítě, data bere z jejich registru.
    return templates.TemplateResponse(request, "network.html", _context(
        request, account, days=days, **sekce.data_oblasti("sit", days)))


@router.post("/network/geoip")
async def network_geoip(request: Request,
                        account: dict[str, Any] = Depends(require_admin)):
    """Stahne (nebo obnovi) databazi GeoLite2 pro mapu.

    Jedina akce v aplikaci, ktera sahne jinam nez na Jellyfin - a jen na
    kliknuti. Proto ji smi spustit spravce, ne kazdy prihlaseny.
    """
    if not geoip.knihovna_je():
        _flash(request, "Chybí knihovna maxminddb, bez ní se databáze nepřečte.",
               "error")
        return RedirectResponse("/network#mapa", status_code=303)

    vysledek = await geoip.stahni()
    if vysledek.get("status") == "ok":
        _flash(request, "Databáze GeoLite2 stažena ({velikost}).", "success",
               velikost=formatting.bytes_human(vysledek["bajtu"]))
    else:
        _flash(request, "Stažení se nepovedlo: {duvod}", "error",
               duvod=vysledek.get("message", "?"))
    return RedirectResponse("/network#mapa", status_code=303)


@router.post("/network/mapa-podrobna")
async def network_mapa_podrobna(request: Request,
                                account: dict[str, Any] = Depends(require_admin)):
    """Stáhne podrobnou mapu (Natural Earth 1:10M) - úloha na pozadí.

    Trvá pár minut (stažení asi 75 MB a rozřezání do dlaždic), proto se
    jen odpálí a stránka si počká (`?wait=task`). Pod zámkem úloh - když
    zrovna běží jiná (nebo tahle), druhá se nespustí.
    """
    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
        return RedirectResponse("/network#mapa", status_code=303)
    # V jazyce serveru, ne v jazyce toho, kdo kliknul - viz i18n.bez_jazyka_uctu.
    asyncio.create_task(i18n.bez_jazyka_uctu(mapa_podrobna.spust_ulohu()))
    _flash(request, "Stahování podrobné mapy spuštěno. Trvá pár minut, stránka se sama obnoví.",
           "info")
    return RedirectResponse("/network?wait=task#mapa", status_code=303)


@router.post("/network/mapa-podrobna/smazat")
def network_mapa_podrobna_smazat(request: Request,
                                 account: dict[str, Any] = Depends(require_admin)):
    """Smaže podrobnou mapu - mapa se vrátí k zabudovanému obrysu.

    Ne během úlohy: stahování by mapu po dokončení zase zapsalo.
    """
    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
        return RedirectResponse("/network#mapa", status_code=303)
    mapa_podrobna.smaz()
    _flash(request, "Podrobná mapa je smazaná.", "success")
    return RedirectResponse("/network#mapa", status_code=303)


@router.get("/network/mapa/dlazdice/{podrobnost}/{x}/{y}")
def network_mapa_dlazdice(request: Request, podrobnost: str, x: str, y: str,
                          account: dict[str, Any] = Depends(require_login)):
    """Jedna dlaždice podrobné mapy - JSON s cestami SVG po vrstvách.

    Souřadnice se čtou jako text a kontrolují ručně: nesmysl (záporné,
    mimo síť, ne číslo) je 404, ne chyba validace. Platná dlaždice bez dat
    (moře) je prázdný objekt - prohlížeč se na ni pak neptá znovu.
    Prohlížeč si dlaždice drží týden; ETag se mění s každým stažením mapy.
    """
    if not all(re.fullmatch(r"\d{1,4}", hodnota) for hodnota in (podrobnost, x, y)):
        raise HTTPException(status_code=404)
    nastaveni = mapa_podrobna.PODROBNOSTI.get(int(podrobnost))
    stav = mapa_podrobna.stav()
    if (nastaveni is None or not stav["platna"]
            or int(x) >= nastaveni["n"] or int(y) >= nastaveni["n"]):
        raise HTTPException(status_code=404)
    otisk = hashlib.sha256(f"{stav['kdy']}|{podrobnost}|{x}|{y}".encode()).hexdigest()[:20]
    hlavicky = {"Cache-Control": "private, max-age=604800", "ETag": f'"{otisk}"'}
    if request.headers.get("if-none-match") == hlavicky["ETag"]:
        return Response(status_code=304, headers=hlavicky)
    obsah = mapa_podrobna.dlazdice(int(podrobnost), int(x), int(y)) or "{}"
    return Response(content=obsah, media_type="application/json", headers=hlavicky)
