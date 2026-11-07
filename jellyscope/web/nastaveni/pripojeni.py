"""Připojení k Jellyfinu a k databázi.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import db, dbmigrate, dialect, jellyfin
from ...config import BASE_DIR as PROJECT_DIR
from ...jellyfin import JellyfinClient, JellyfinError, QUICK_TIMEOUT
from ..zaklad import _clamp, _flash, log, require_admin
from .spolecne import _draft_clear, _draft_read, _draft_save

router = APIRouter()


@router.post("/settings/connection")
async def settings_connection(
    request: Request,
    jellyfin_url: str = Form(""),
    jellyfin_api_key: str = Form(""),
    jellyfin_generation: str = Form("auto"),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Ulozi nebo otestuje adresu Jellyfinu a API klic.

    Klic se do formulare nikdy nevypisuje - jen se pozna, jestli uz nejaky
    je. Prazdne pole proto znamena "nech ten stavajici", ne "smaz ho".
    Bez toho by staclo omylem ulozit formular a spojeni by prestalo fungovat.

    Testovani je soucasti **tehoz** formulare, ne samostatneho tlacitka
    vedle. Drive bylo zvlast a nic neposilalo, takze testovalo ulozene
    nastaveni misto toho vyplneneho: vyplnil jsi adresu, kliknul na
    "Otestovat spojeni" a dostal chybu o chybejicim http:// - protoze
    se testovala prazdna ulozena hodnota. A vyplnene udaje se pritom
    zahodily, protoze odeslani jednoho formulare zahodi obsah druheho.
    """
    url = jellyfin_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url

    # Generace serveru. Adresa ani klíč se nezahazují - ty jsou pro obě
    # generace tytéž a liší se jen to, jak se posílají dotazy.
    #
    # Zahodí se ale **zjištěná verze**, a jen při skutečné změně volby:
    # patří k tomu, co bylo vybrané předtím, takže by na stránce zůstala
    # dvě čísla, která si odporují. Prázdná se zjistí znovu při nejbližším
    # testu spojení nebo synchronizaci.
    volba = jellyfin_generation if jellyfin_generation in ("auto", "12", "10") \
        else "auto"
    if volba != db.get_setting(jellyfin.GENERACE_KLIC, "auto"):
        db.set_setting(jellyfin.GENERACE_KLIC, volba)
        db.set_setting(jellyfin.VERZE_KLIC, "")

    # Prazdny klic znamena "nech stavajici". Pri testu bereme i rozepsany,
    # aby slo otestovat vic pokusu za sebou bez opakovaneho vypisovani.
    rozepsane = _draft_read(account, "jellyfin") or {}
    key = (jellyfin_api_key.strip()
           or rozepsane.get("api_key", "")
           or db.get_setting("jellyfin_api_key", ""))

    if action == "test":
        # Rozepsane si podrzime, at se formular po testu nevyprazdni.
        _draft_save(account, "jellyfin", {"url": url, "api_key": key})

        if not url:
            _flash(request, "Nejdřív vyplň adresu serveru.", "error")
        else:
            try:
                async with JellyfinClient(url, key, QUICK_TIMEOUT) as client:
                    info = await client.system_info()
                _flash(
                    request,
                    "Spojení v pořádku: {server} (Jellyfin {verze})",
                    "success",
                    server=info.get("ServerName", "?"),
                    verze=info.get("Version", "?"),
                )
            except JellyfinError as exc:
                _flash(request, "Spojení selhalo: {duvod}", "error", duvod=str(exc))
        return RedirectResponse("/settings?section=jellyfin", status_code=303)

    db.set_setting("jellyfin_url", url)
    if key:
        db.set_setting("jellyfin_api_key", key)
    _draft_clear(account, "jellyfin")

    # Verzi zjistíme rovnou při uložení, ne až při testu spojení nebo
    # noční synchronizaci: kdo přepne generaci a klikne na Uložit, má
    # hned vidět, co server hlásí - a hlavně se hned pozná, když si to
    # s ruční volbou odporuje.
    #
    # Krátký strop a spolknutá chyba: uložení nesmí selhat kvůli tomu,
    # že server zrovna neběží. Verze pak zůstane prázdná a doplní se
    # při nejbližší příležitosti.
    if url and key:
        try:
            async with JellyfinClient(url, key, QUICK_TIMEOUT) as client:
                await client.system_info()
        except JellyfinError as chyba:
            log.info("verzi Jellyfinu se pri ulozeni nepodarilo zjistit: %s",
                     chyba)

    _flash(request, "Připojení uloženo.", "success")
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@router.post("/settings/database")
def settings_database(
    request: Request,
    kind: str = Form("sqlite"),
    sqlite_path: str = Form("data/jellyscope.db"),
    pg_host: str = Form("localhost"),
    pg_port: str = Form("5432"),
    pg_database: str = Form("jellyscope"),
    pg_user: str = Form("jellyscope"),
    pg_password: str = Form(""),
    # Nezaskrtnute policko se v HTML formulari vubec neposila, proto je
    # vychozi hodnota "" = vypnuto. To je zaroven duvod, proc se sem neda
    # dat Form(True) - to by slo zapnout, ale uz nikdy vypnout.
    pg_use_pool: str = Form(""),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Nastaveni databaze: ulozit, otestovat, nebo prenest data.

    Tohle jedine nastaveni nemuze byt v databazi - potrebujeme ho, abychom
    se k ni vubec pripojili. Uklada se proto do souboru data/database.json.
    """
    current = db.database_config()
    # Heslo se do formuláře nikdy nevypisuje, takže prázdné pole znamená
    # "nech to, co už znám". Rozepsané nastavení je v tom pořadí první:
    # po otestování spojení chceme uložit přesně to, co se testovalo.
    rozepsane = _draft_read(account, "database")

    candidate = dialect.DatabaseConfig(
        kind=kind if kind in (dialect.SQLITE, dialect.POSTGRES) else dialect.SQLITE,
        path=sqlite_path.strip() or "data/jellyscope.db",
        host=pg_host.strip() or "localhost",
        port=int(_clamp(pg_port, 1, 65535, 5432)),
        database=pg_database.strip() or "jellyscope",
        user=pg_user.strip() or "jellyscope",
        password=pg_password or (rozepsane.password if rozepsane else "")
                 or current.password,
        use_pool=bool(pg_use_pool),
    )

    # Test ani přenos nic neukládají, ale rozepsané hodnoty si podržíme -
    # jinak by je uživatel po každém kliknutí vyplňoval znovu.
    if action in ("test", "migrate"):
        _draft_save(account, "database", candidate)

    if action == "test":
        ok, message = db.test_connection(candidate)
        _flash(request, message, "success" if ok else "error")
        return RedirectResponse("/settings?section=database", status_code=303)

    if action == "migrate":
        ok, message = db.test_connection(candidate)
        if not ok:
            _flash(request, "Cílová databáze není dostupná: {duvod}", "error",
                   duvod=message)
            return RedirectResponse("/settings?section=database", status_code=303)

        result = dbmigrate.copy_all(current, candidate)
        if result.get("tabulka"):
            _flash(request, "Převod se zastavil u tabulky {tabulka}, nic se nezměnilo: {chyba}",
                   "error", tabulka=result["tabulka"], chyba=result.get("chyba", ""))
        elif result.get("status") != "ok":
            _flash(request, result.get("message", "Přenos selhal."), "error")
        else:
            _flash(
                request,
                "Přeneseno {n} řádků. Ulož nastavení a restartuj, "
                "aby se aplikace na novou databázi přepnula.",
                "success",
                n=result["total"],
            )
        return RedirectResponse("/settings?section=database", status_code=303)

    ok, message = db.test_connection(candidate)
    if not ok:
        _flash(request, "Neukládám, spojení nefunguje: {duvod}", "error",
               duvod=message)
        return RedirectResponse("/settings?section=database", status_code=303)

    dialect.save_config(PROJECT_DIR, candidate)
    _draft_clear(account, "database")   # uloženo, rozepsané už není k čemu
    _flash(
        request,
        "Nastavení databáze uloženo. Změna se projeví po restartu aplikace.",
        "success",
    )
    return RedirectResponse("/settings?section=database", status_code=303)
