"""Aktualizace, restart a zálohy (stažení, smazání, obnova).

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from html import escape
from typing import Any

from ... import __version__, db, i18n, tasks, updates
from ..zaklad import STARTED_AT, _flash, log, require_admin

router = APIRouter()


@router.post("/settings/update")
async def settings_update(request: Request,
                          account: dict[str, Any] = Depends(require_admin)):
    """Stáhne novou verzi a restartuje aplikaci.

    Dělá totéž co `deploy/update.sh`, jen bez toho posledního kroku:
    `git pull`, doinstalování závislostí a pak **restart vlastního
    procesu** (`os.execv`), tedy stejný restart jako tlačítko v Nastavení.
    Správce služby k tomu není potřeba.

    Chybějící sloupce v databázi si aplikace doplní sama při startu, takže
    po restartu je hotovo. Když se aktualizace nepovede, nic se
    nerestartuje a běží dál stará verze - to je bezpečnější pořadí.
    """
    vysledek = await updates.aktualizuj()
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Aktualizace se nepovedla."), "error",
               **vysledek.get("parametry", {}))
        return RedirectResponse("/settings?section=general", status_code=303)

    _naplanuj_restart()
    # Zamerne NE presmerovani na stranku aplikace.
    #
    # Sablony se ctou ze souboru pri kazdem pozadavku, kod aplikace bydli
    # v pameti procesu. Mezi stazenim a restartem tedy bezi STARY KOD nad
    # NOVYMI SABLONAMI - a jakmile nova sablona chce promennou, o ktere
    # stary kod nevi, skonci to chybou. Presne tohle delalo po aktualizaci
    # z prohlizece "Internal Server Error".
    #
    # Tahle stranka je slozena tady v Pythonu: zadna sablona, zadny
    # kontext, nic, co by se mohlo rozejit. Pocka na novy proces a teprve
    # pak pusti cloveka dal. Jeji skript je vlozeny (do static/js nesmi -
    # ten se s verzi meni), proto jednorazovy nonce do hlavicky CSP.
    nonce = secrets.token_urlsafe(18)
    request.state.csp_nonce = nonce
    return HTMLResponse(_stranka_aktualizace(nonce))


def _stranka_aktualizace(nonce: str) -> str:
    """Čekárna na restart po aktualizaci - bez šablony, schválně.

    Je to jediná stránka, kterou musí vykreslit **stará** verze aplikace
    v okamžiku, kdy na disku už leží nová. Proto nesmí sáhnout na nic,
    co se s verzí mění: ani na šablonu, ani na styl, ani na kontext.
    Skript je proto vložený a pod CSP projde jen s `nonce` z hlavičky
    (web.csp) - jediná taková stránka v aplikaci.
    """
    # Stranka je skladana v Pythonu, takze ji nechrani autoescape Jinja.
    # Preklady pisou cizi lide pres Weblate: znacka v nich se musi ukazat
    # jako text a apostrof ("It's taking longer") nesmi rozbit JS retezec,
    # jinak by funkce zkus() nevznikla a cekarna by nikdy nepustila dal.
    nadpis = escape(i18n.translate("Aktualizuji…"))
    popis = escape(i18n.translate("Nová verze je stažená. Aplikace se teď restartuje. Jakmile bude "
                                  "nahoře, pustím tě dál."))
    dlouho = escape(i18n.translate("Trvá to déle, než je zdrávo."))
    pokracovat = escape(i18n.translate("Zkusit to znovu"))
    pozn_pomalu = json.dumps(f'{dlouho} <a href="/">{pokracovat}</a>')
    beh_od = int(STARTED_AT)
    verze_ted = __version__
    return f"""<!doctype html>
<html lang="{i18n.current_language()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{nadpis} &middot; Jellyscope</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #131312; color: #eceae4; font-size: 15px;
         font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  .box {{ max-width: 420px; padding: 28px 30px; border-radius: 14px;
          border: 1px solid rgba(255,255,255,.10); background: #1b1b1a;
          box-shadow: 0 24px 60px rgb(0 0 0 / 45%); text-align: center; }}
  h1 {{ font-size: 19px; margin: 0 0 10px; }}
  p  {{ color: #b6b4ac; line-height: 1.5; margin: 0; }}
  .pozn:not(:empty) {{ margin-top: 14px; font-size: 14px; }}
  .pozn a {{ color: #6aa9ee; }}
  .tecka {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%;
            background: #3987e5; margin-bottom: 14px;
            animation: tep 1.4s ease-in-out infinite; }}
  @keyframes tep {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .25; }} }}
  @media (prefers-reduced-motion: reduce) {{ .tecka {{ animation: none; }} }}
</style>
</head>
<body>
<div class="box">
  <span class="tecka"></span>
  <h1>{nadpis}</h1>
  <p>{popis}</p>
  <p class="pozn" id="pozn"></p>
</div>
<script nonce="{nonce}">
  // Ptáme se na /health, dokud neodpoví nový proces - pozná se podle
  // `started_at`, které se výměnou procesu změní. Teprve pak stránku
  // pustíme dál; do té doby by ji kreslila stará verze nad novými
  // šablonami, což je právě to, čemu se tady vyhýbáme.
  //
  // Od čeho čekáme, je zapsané tady ze serveru - je to start procesu,
  // který tuhle stránku vykreslil. Zjišťovat si to až první odpovědí
  // nešlo: restart přijde do vteřiny, takže první odpověď už patřila
  // novému procesu, čekárna si ho zapsala jako výchozí stav a čekala na
  // změnu, která nikdy nepřišla. Aplikace byla přitom dávno nahoře.
  var puvodni = {beh_od};        // start procesu, ktery tuhle stranku vykreslil
  var verze = "{verze_ted}";     // a verze, kterou mel
  var pokusu = 0;
  var VZDAT_TO = 150;              // ~5 minut

  function zkus() {{
    if (pokusu++ > VZDAT_TO) {{
      document.getElementById("pozn").innerHTML =
        {pozn_pomalu};
      return;
    }}
    fetch("/health", {{ cache: "no-store", credentials: "same-origin" }})
      .then(function (r) {{ return r.json(); }})
      .then(function (data) {{
        // Dva signály, protože každý sám o sobě někde selže:
        //   * `version` je přesně ta otázka, na kterou čekáme - ale hlásí
        //     se jen přihlášenému,
        //   * `started_at` odpoví komukoliv, jen říká míň ("něco se
        //     restartovalo").
        // Stačí, když se změní jeden.
        if (data.started_at !== puvodni) {{ location.href = "/"; }}
        else if (data.version && data.version !== verze) {{ location.href = "/"; }}
        else {{ setTimeout(zkus, 2000); }}
      }})
      .catch(function () {{
        // Aplikace je právě dole - přesně to čekáme.
        setTimeout(zkus, 2000);
      }});
  }}

  zkus();
</script>
</body>
</html>"""


@router.post("/settings/restart")
async def settings_restart(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Restartuje **Jellyscope**, ne Jellyfin.

    Nahrazujeme vlastni proces, nic jineho. Na medialni server se tim
    nesaha - beziciho prehravani se to nedotkne. Jedine, co se stane,
    je ze tahle aplikace na par vterin prestane odpovidat.

    Vetsina nastaveni se projevi hned, protoze se cte z databaze pri kazdem
    pouziti. Restart je potreba u veci, ktere se nacitaji jednou pri startu -
    hlavne u zmeny databaze.

    Restart delame tak, ze proces nahradi sam sebe (`os.execv`). Az odpoved
    dorazi do prohlizece, aplikace se zvedne znovu.
    """
    log.info("restart aplikace vyzadan uctem %s", account["username"])
    _flash(request, "Aplikace se restartuje. Stránka se obnoví sama, jakmile bude nahoře.", "info")
    _naplanuj_restart()
    # `wait=restart`: stranka si sama pocka, az se zvedne novy proces,
    # a nacte se znovu. Drive to uzivatel musel odhadnout a obnovit rucne.
    return RedirectResponse("/settings?section=general&wait=restart", status_code=303)


def _naplanuj_restart() -> None:
    """Za chvilku nahradí proces sám sebou.

    Vlastní funkce, protože restart potřebuje víc míst - ruční tlačítko
    i obnova zálohy. Dvě kopie téhle logiky by se časem rozešly a jedna
    z nich by zapomněla zavřít spojení.
    """
    async def _restart_soon() -> None:
        # Chvilka na odeslani odpovedi - jinak by prohlizec dostal
        # preruseni spojeni misto presmerovani.
        await asyncio.sleep(1.0)
        log.info("restart na zadost uzivatele")
        # execv nahradi proces, takze zadny uklid uz nikdo neudela -
        # spojeni je potreba zavrit ted, dokud jeste bezime.
        db.close_pool()
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except OSError:
            # Kdyby execv neproslo, aspon slusne skoncime - spravce sluzby
            # (nebo uzivatel) aplikaci nastartuje znovu.
            log.exception("restart pres execv selhal, koncim")
            os._exit(1)

    asyncio.create_task(_restart_soon())


@router.get("/settings/backup/download")
def backup_download(name: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """Stáhne jednu zálohu databáze.

    Jméno se ověří proti tomu, co ve složce se zálohami doopravdy leží -
    viz `tasks.backup_file()`. Bez toho by šlo přes adresu stáhnout
    libovolný soubor ze stroje.
    """
    cesta = tasks.backup_file(name)
    if cesta is None:
        raise HTTPException(status_code=404, detail="Taková záloha tu není.")

    # `media_type` schválně octet-stream: prohlížeč soubor uloží, místo
    # aby se pokusil zobrazit SQL jako text ve stránce.
    return FileResponse(cesta, filename=cesta.name,
                        media_type="application/octet-stream")


@router.post("/settings/backup/delete")
def backup_delete(request: Request, name: str = Form(""),
                  account: dict[str, Any] = Depends(require_admin)):
    """Smaže jednu zálohu."""
    if tasks.delete_backup(name):
        _flash(request, "Záloha {nazev} smazána.", "success", nazev=name)
    else:
        _flash(request, "Takovou zálohu se nepodařilo najít.", "error")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.post("/settings/backup/restore")
async def backup_restore(request: Request, name: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Obnoví databázi ze zálohy a restartuje aplikaci.

    Restart není kosmetika: aplikace má v paměti nastavení i otevřená
    spojení do databáze, která po obnově už neplatí.
    """
    vysledek = await asyncio.to_thread(tasks.restore_backup, name)
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Obnova selhala."), "error")
        return RedirectResponse("/settings?section=tasks", status_code=303)

    _flash(request,
           "Databáze obnovena ze zálohy {nazev}. Stav před obnovou zůstal "
           "uložený jako {zaloha}. Aplikace se restartuje.",
           "success", nazev=name, zaloha=vysledek["safety"])
    _naplanuj_restart()
    return RedirectResponse("/settings?section=tasks&wait=restart", status_code=303)
