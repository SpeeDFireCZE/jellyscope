"""Rozhraní, kontrola aktualizací a jazyk.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

import os
import time
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import applog, db, i18n, sekce, updates
from ..zaklad import (
    PODKLADY_MAPY,
    STROP_MAX,
    STROP_MIN,
    VZHLEDY,
    ZOOM_REZIMY,
    _clamp,
    _flash,
    require_admin,
)
from .spolecne import _zona_existuje

router = APIRouter()


@router.post("/settings/interface")
def settings_interface(request: Request,
                       ui_max_streams: str = Form(""),
                       ui_max_streams_mobile: str = Form(""),
                       ui_max_viewers: str = Form(""),
                       ui_max_viewers_mobile: str = Form(""),
                       ui_max_rows: str = Form(""),
                       ui_max_rows_mobile: str = Form(""),
                       ui_map_zoom: str = Form("click"),
                       ui_map_podklad: str = Form("kresleny"),
                       ui_skin: str = Form("novy"),
                       ui_cas_presne: str = Form("0"),
                       ui_dashboard: str = Form("0"),
                       dashboard_nazev: str = Form(""),
                       ui_udalosti: str = Form("1"),
                       account: dict[str, Any] = Depends(require_admin)):
    """Stropy dlouhých seznamů - kolik se vypíše, než se zbytek schová.

    Hodnoty se ukládají tak, jak přišly; ořezání do mezí dělá až čtení
    (`_stropy`). Kdyby se ořezávalo při ukládání, člověk by napsal 100,
    uvidel 50 a nevěděl proč - takhle se aspoň chová stránka a nastavení
    stejně.
    """
    db.set_setting("ui_max_streams", str(_clamp(ui_max_streams, STROP_MIN,
                                                STROP_MAX, 10)))
    db.set_setting("ui_max_streams_mobile",
                   _clamp(ui_max_streams_mobile, STROP_MIN,
                          STROP_MAX, 3))
    db.set_setting("ui_max_viewers", str(_clamp(ui_max_viewers, STROP_MIN,
                                                STROP_MAX, 10)))
    db.set_setting("ui_max_viewers_mobile",
                   _clamp(ui_max_viewers_mobile, STROP_MIN, STROP_MAX, 5))
    db.set_setting("ui_max_rows", _clamp(ui_max_rows, STROP_MIN, STROP_MAX, 10))
    db.set_setting("ui_max_rows_mobile",
                   _clamp(ui_max_rows_mobile, STROP_MIN, STROP_MAX, 5))
    # Cokoli mimo znamé režimy je překlep nebo podvržený formulář -
    # v obou případech je správná odpověď výchozí hodnota, ne uložit to.
    db.set_setting("ui_map_zoom",
                   ui_map_zoom if ui_map_zoom in ZOOM_REZIMY else "click")
    # Podklad z OSM posila prohlizece divaku na cizi server - jen kdyz
    # je to vyslovne "osm", cokoliv jineho je kreslena mapa.
    db.set_setting("ui_map_podklad",
                   ui_map_podklad if ui_map_podklad in PODKLADY_MAPY else "kresleny")
    db.set_setting("ui_skin", ui_skin if ui_skin in VZHLEDY else "novy")
    db.set_setting("ui_cas_presne", "1" if ui_cas_presne == "1" else "0")
    # Sezónní výzdoba: vypne ji jen výslovné "0". Formulář bez pole (jiná
    # karta, starší stránka) ji nechá zapnutou - výchozí stav je zapnuto.
    db.set_setting("ui_udalosti", "0" if ui_udalosti == "0" else "1")
    # Přehled pro všechny má název, který jde do menu. Zkontroluje se, kdykoliv
    # ho někdo posílá nebo přehled zapíná; neplatný se neuloží a platí ten
    # předchozí. Bez platného názvu se přehled nezapne - v menu by nebylo
    # co napsat. Ostatní nastavení se uloží i tak.
    chce_prehled = ui_dashboard == "1"
    chyba_nazvu = None
    if chce_prehled or dashboard_nazev.strip():
        try:
            db.set_setting(sekce.NAZEV_KLIC, sekce.over_nazev(dashboard_nazev))
        except sekce.NeplatnyNazev as chyba:
            chyba_nazvu = chyba
    # Zapínat s neplatným názvem nejde, i když si vypnutý přehled pamatuje
    # starý: hláška říká „nejde" a přehled nemá potichu naskočit pod jménem,
    # které správce právě přepsal. Zapnutý zůstane zapnutý pod dosavadním.
    if chce_prehled and (not sekce.nazev()
                         or (chyba_nazvu is not None and not sekce.je_zapnuty())):
        chce_prehled = False
    zapina_prehled = chce_prehled and not sekce.je_zapnuty()
    db.set_setting(sekce.ZAPNUTO, "1" if chce_prehled else "0")
    if chyba_nazvu is not None:
        _flash(request, chyba_nazvu.veta, "error", **chyba_nazvu.hodnoty)
        return RedirectResponse("/settings?section=interface", status_code=303)
    _flash(request, "Uloženo.", "success")

    # Kdo přehled právě zapnul, jde rovnou sestavit - zapnout a sestavit
    # jsou dva kroky téže věci, ne dvě různá místa k hledání.
    if zapina_prehled and not sekce.nacti_rozvrzeni():
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/settings?section=interface", status_code=303)


@router.post("/settings/updates")
async def settings_updates(request: Request,
                           account: dict[str, Any] = Depends(require_admin)):
    """Ruční kontrola nové verze.

    Jestli se má ptát pravidelně a kdy, se nastavuje v Úlohách jako
    u všeho ostatního, co běží samo - viz úloha "Kontrola aktualizací".
    Tady zůstalo jen jednorázové kliknutí, které se zeptá bez ohledu na
    to, jestli je úloha zapnutá: to je akce, ne rozvrh.
    """
    vysledek = await updates.zkontroluj(vynuceno=True)
    nove_vydani = bool(vysledek.get("je_novejsi"))
    preklady = int(vysledek.get("nove_preklady") or 0)
    # Ctyri odpovedi, kazda jina - clovek ma z hlasky poznat, co ho ceka,
    # driv nez cokoliv otevre. "Neco je nove" bez upresneni by ho poslalo
    # hledat.
    if vysledek.get("status") == "error":
        _flash(request, "Kontrolu se nepovedlo provést: {duvod}", "error",
               duvod=vysledek.get("message", "?"))
    elif nove_vydani and preklady:
        _flash(request,
               "Je k dispozici verze {verze} a k tomu nové překlady "
               "({n} souborů). Obojí se stáhne najednou.", "success",
               verze=vysledek.get("nalezena", "?"), n=preklady)
    elif nove_vydani:
        _flash(request, "Je k dispozici verze {verze}.", "success",
               verze=vysledek.get("nalezena", "?"))
    elif preklady:
        _flash(request,
               "Vydání je aktuální, ale na GitHubu přibyly překlady "
               "({n} souborů).", "success", n=preklady)
    else:
        _flash(request, "Máš nejnovější verzi i překlady.", "success")

    return RedirectResponse("/settings?section=general#verze", status_code=303)


@router.post("/settings/language")
def settings_language(
    request: Request,
    ui_language: str = Form("cs"),
    log_language: str = Form("cs"),
    app_timezone: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    if ui_language not in i18n.LANGUAGES:
        ui_language = i18n.DEFAULT_LANGUAGE
    db.set_setting("ui_language", ui_language)

    # Jazyk logu se ukláda ze stejného formuláře, ale je to jiná volba:
    # log často čte někdo jiný, než kdo se dívá do rozhraní.
    if log_language not in i18n.LANGUAGES:
        log_language = i18n.DEFAULT_LANGUAGE
    db.set_setting("log_language", log_language)
    applog.nastav_jazyk(log_language)

    # Zona se uklada, jen kdyz ji Python zna. Preklep by jinak zpusobil,
    # ze se casy vypisuji podle systemu, a nikdo by nevedel proc.
    zona = app_timezone.strip()
    if not zona:
        db.set_setting("app_timezone", "")
    elif _zona_existuje(zona):
        db.set_setting("app_timezone", zona)
        # Prostredi procesu prepiseme rovnou - vypis casu se tim srovna
        # hned. Deleni dnu v SQL az po restartu, viz lifespan().
        os.environ["TZ"] = zona
        if hasattr(time, "tzset"):
            time.tzset()
    else:
        _flash(request, "Časovou zónu {zona} neznám, nechal jsem tu původní.",
               "error", zona=zona)
        return RedirectResponse("/settings?section=general", status_code=303)

    # Hláška v jazyce, ve kterém se stránka hned ukáže: nově zvolený jazyk
    # serveru, ledaže má správce vlastní jazyk (Můj účet) - pak ten.
    vlastni = account.get("ui_language")
    jazyk_hlasky = vlastni if vlastni in i18n.LANGUAGES else ui_language
    _flash(request, i18n.translate("Uložit jazyk", jazyk_hlasky) + " ✓", "success")
    return RedirectResponse("/settings?section=general", status_code=303)
