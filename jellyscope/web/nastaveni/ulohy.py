"""Synchronizace, zastavení, technický sken, plánované úlohy a log.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from typing import Any

from ... import applog, db, i18n, odklizeni, scanner, stats, tasks
from ..zaklad import _clamp, _flash, require_admin

router = APIRouter()


@router.post("/settings/sync")
async def settings_sync(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Spusti synchronizaci knihovny na pozadi.

    Nespoustime ji primo v teto funkci - u velke knihovny by prohlizec
    cekal na odpoved nekolik minut a vypadalo by to jako zamrznuti.
    Ulohu odpalime a hned odpovime.
    """
    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        # V jazyce serveru, ne v jazyce toho, kdo kliknul - viz i18n.bez_jazyka_uctu.
        asyncio.create_task(i18n.bez_jazyka_uctu(scanner.sync_library()))
        _flash(request, "Synchronizace knihovny spuštěna.", "info")
        # `wait=task` necha stranku pockat a po dokonceni ji obnovi -
        # jinak clovek koukа na "bezi" a netusi, kdy uz je hotovo.
        return RedirectResponse("/settings?section=jellyfin&wait=task",
                                status_code=303)
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@router.post("/settings/stop")
async def settings_stop(
    request: Request,
    back: str = Form("tasks"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Poprosi bezici ulohu, aby skoncila.

    Uloha se nepreruší uprostred prace - dodela rozdelanou polozku a teprve
    pak skonci. Proto se tady taky nic necekaji: jen se preda pokyn.
    """
    if scanner.request_stop():
        _flash(
            request,
            "Pokyn k zastavení předán. Úloha dokončí rozpracovanou položku a skončí. Stránka se "
            "pak obnoví sama.",
            "info",
        )
    else:
        _flash(request, "Žádná úloha zrovna neběží.", "warning")

    sekce = back if back in ("tasks", "jellyfin", "data") else "tasks"
    # `wait=task` rekne strance, at si pocka a obnovi se sama, jakmile
    # uloha doopravdy skonci. Uzivatel tak nemusi hadat, kdy uz muze.
    cekat = "&wait=task" if scanner.stop_requested() else ""
    return RedirectResponse(f"/settings?section={sekce}{cekat}", status_code=303)


@router.post("/settings/scan")
async def settings_scan(
    request: Request,
    mode: str = Form("missing"),
    library_id: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Spusti technickou analyzu souboru pres ffprobe.

    `library_id` spousti tlacitko primo u hlasky na detailu knihovny -
    analyzuje se pak jen ta knihovna a clovek se na ni zase vrati.
    """
    # Kam se po spusteni vratit. Jen id knihovny, ktera opravdu existuje;
    # cizi hodnota z formulare se nesmi dostat do adresy presmerovani.
    # Bez knihovny zpatky mezi Ulohy - tam ta tlacitka jsou.
    zpet = "/settings?section=tasks"
    if library_id and stats.library(library_id):
        zpet = f"/library/{library_id}"
    else:
        library_id = ""

    if db.get_setting("tech_source") != "ffprobe":
        _flash(
            request,
            "Zdroj technických dat je nastavený na Jellyfin. "
            "Přepni ho na ffprobe a ulož nastavení.",
            "warning",
        )
        return RedirectResponse(zpet, status_code=303)

    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(i18n.bez_jazyka_uctu(scanner.run_tech_scan(
            only_missing=(mode == "missing"), library_id=library_id or None)))
        _flash(request, "Analýza souborů spuštěna.", "info")
        oddelovac = "&" if "?" in zpet else "?"
        return RedirectResponse(f"{zpet}{oddelovac}wait=task", status_code=303)

    return RedirectResponse(zpet, status_code=303)


@router.post("/settings/tasks")
async def tasks_save(
    request: Request,
    backup_path: str = Form(""),
    backup_keep: str = Form("7"),
    pg_dump_path: str = Form(""),
    history_retention_days: str = Form(""),
    logrotate_dni: str = Form(""),
    logrotate_pocet: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Ulozi nastaveni vsech uloh najednou.

    Zaskrtavatka se do formulare posilaji jen kdyz jsou zaskrtnuta - proto
    se ctou primo z tela pozadavku, ne jako pojmenovane parametry.
    Nezaskrtnute pole se v datech vubec neobjevi.
    """
    form = await request.form()

    for task in tasks.TASKS.values():
        enabled = "1" if form.get(f"enabled_{task.key}") else "0"
        db.set_setting(task.enabled_setting, enabled)

        if task.je_denni:
            # Hodina a minuta chodí ze dvou polí zvlášť, tady se z nich
            # zase složí "HH:MM". `_clamp` ohlídá rozsah a doplní nulu
            # (napsané "3" a "5" je "03:05"), `platny_cas` je poslední
            # pojistka: co by přesto neodpovídalo tvaru, se uloží jako
            # výchozí, ne jako rozbitý rozvrh.
            #
            # Náhradou při nesmyslu je to, co je uložené teď: vymazané
            # pole tak rozvrh nezmění. Nula by z něj tiše udělala půlnoc.
            soucasne = tasks.denni_cas(task).split(":")
            hodina = _clamp(str(form.get(f"time_{task.key}_h", "")),
                            0, 23, int(soucasne[0]))
            minuta = _clamp(str(form.get(f"time_{task.key}_m", "")),
                            0, 59, int(soucasne[1]))
            cas = f"{int(hodina):02d}:{int(minuta):02d}"
            db.set_setting(task.time_setting,
                           tasks.platny_cas(cas, task.default_time))
        else:
            db.set_setting(
                task.interval_setting,
                _clamp(str(form.get(f"minutes_{task.key}", task.default_minutes)),
                       0, 10080, task.default_minutes),
            )

    # Kolik historie se necha. Ulozi se i kdyz je odklizeni vypnute -
    # az se zapne, ma platit cislo, ktere clovek videl na strance.
    db.set_setting(odklizeni.RETENCE_KLIC,
                   _clamp(history_retention_days, odklizeni.MIN_DNU,
                          odklizeni.MAX_DNU, odklizeni.VYCHOZI_DNU))
    db.set_setting("backup_path", backup_path.strip())
    db.set_setting("backup_keep", _clamp(backup_keep, 1, 365, 7))
    db.set_setting("pg_dump_path", pg_dump_path.strip())
    # Rotace logu. Vymazane pole nema rozvrh rozbit: nahradou je ulozena
    # hodnota, ne nula.
    db.set_setting(applog.DNI_KLIC, _clamp(logrotate_dni, 1, applog.DNI_MAX,
                                           tasks.dni_rotace()))
    db.set_setting(applog.POCET_KLIC, _clamp(logrotate_pocet, 1, applog.POCET_MAX,
                                             tasks.pocet_rotace()))
    applog.nastav_pocet(tasks.pocet_rotace())

    _flash(request, "Nastavení úloh uloženo.", "success")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.get("/settings/log/stahnout")
def log_stahnout(soubor: str = "",
                 account: dict[str, Any] = Depends(require_admin)):
    """Stáhne log - i odrotovanou zálohu, dokud ji rotace nesmazala.

    Jméno se ověří proti složce s logy (applog.ke_stazeni), jinak by šlo
    přes adresu stáhnout libovolný soubor. Tajné hodnoty se nahradí
    hvězdičkami stejně jako ve výpisu na stránce.
    """
    cesta = applog.ke_stazeni(soubor)
    if cesta is None:
        raise HTTPException(status_code=404, detail="Takový log tu není.")
    return StreamingResponse(
        applog.stahni(cesta), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{cesta.name}"',
                 "Cache-Control": "no-store"})


@router.post("/settings/tasks/run")
async def tasks_run(
    request: Request,
    key: str = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    """Rucni spusteni ulohy.

    Ulohu odpalime na pozadi a hned odpovime - u velke knihovny by jinak
    prohlizec cekal nekolik minut a vypadalo by to jako zamrznuti.
    """
    task = tasks.TASKS.get(key)
    if task is None:
        _flash(request, "Neznámá úloha.", "error")
    elif scanner.is_scan_running() and key in ("sync", "recent", "tech"):
        # "recent" tu drive chybelo, takze slo spustit soubezne se scanem -
        # a druha uloha pak jen narazila na zamek a tise skoncila.
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(i18n.bez_jazyka_uctu(tasks.run_now(key)))
        # Název úlohy je česká konstanta z tasks.py - v anglickém
        # rozhraní se musí přeložit stejně jako zbytek hlášky.
        _flash(request, "{uloha}: {stav}", "info",
               uloha=i18n.translate(task.name),
               stav=i18n.translate("spuštěno."))
        # Stranka si pocka a po dokonceni se obnovi sama - stejne jako
        # u synchronizace knihovny.
        return RedirectResponse("/settings?section=tasks&wait=task", status_code=303)

    return RedirectResponse("/settings?section=tasks", status_code=303)
