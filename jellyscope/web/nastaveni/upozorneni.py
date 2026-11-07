"""Upozornění: kanály, události, texty, zkušební zpráva.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import db, i18n, notifikace, tasks
from ..zaklad import _clamp, _flash, require_admin
from .spolecne import _uloz_tajemstvi

router = APIRouter()


@router.post("/settings/notifications")
def settings_notifications(
    request: Request,
    smtp_enabled: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_uzivatel: str = Form(""),
    smtp_heslo: str = Form(""),
    smtp_odesilatel: str = Form(""),
    smtp_komu: str = Form(""),
    smtp_tls: str = Form(""),
    discord_enabled: str = Form(""),
    discord_webhook: str = Form(""),
    telegram_enabled: str = Form(""),
    telegram_token: str = Form(""),
    telegram_chat: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Nastaveni kanalu, kterymi upozorneni chodi."""
    from ... import notifikace

    db.set_setting(notifikace.klic("smtp", "enabled"), "1" if smtp_enabled else "0")
    db.set_setting(notifikace.klic("smtp", "host"), smtp_host.strip())
    db.set_setting(notifikace.klic("smtp", "port"),
                   _clamp(smtp_port, 1, 65535, 587))
    db.set_setting(notifikace.klic("smtp", "uzivatel"), smtp_uzivatel.strip())
    _uloz_tajemstvi(notifikace.klic("smtp", "heslo"), smtp_heslo)
    db.set_setting(notifikace.klic("smtp", "odesilatel"), smtp_odesilatel.strip())
    db.set_setting(notifikace.klic("smtp", "komu"), smtp_komu.strip())
    db.set_setting(notifikace.klic("smtp", "tls"), "1" if smtp_tls else "0")

    db.set_setting(notifikace.klic("discord", "enabled"), "1" if discord_enabled else "0")
    _uloz_tajemstvi(notifikace.klic("discord", "webhook"), discord_webhook)

    db.set_setting(notifikace.klic("telegram", "enabled"),
                   "1" if telegram_enabled else "0")
    _uloz_tajemstvi(notifikace.klic("telegram", "token"), telegram_token)
    db.set_setting(notifikace.klic("telegram", "chat"), telegram_chat.strip())

    db.forget_settings()
    # Zapnuty kanal s nevyplnenymi udaji je tichá past: nastaveni se ulozi,
    # nic nehlasi chybu - a clovek se spolehne na upozorneni, ktera nikdy
    # neprijdou. Rekneme to hned, ne az pri prvnim poplachu.
    nedodelane = [k for k in notifikace.KANALY
                  if notifikace.kanal_zapnuty(k)
                  and not notifikace.kanal_nastaveny(k)]
    if nedodelane:
        _flash(request,
               "Kanály uloženy, ale tohle je zapnuté a nevyplněné: {kanaly}. "
               "Dokud to nedoplníš, nic se přes ně neodešle.", "warning",
               kanaly=", ".join(notifikace.NAZVY.get(k, k) for k in nedodelane))
    else:
        _flash(request, "Kanály uloženy.", "success")
    return RedirectResponse("/settings?section=notifications", status_code=303)


@router.post("/settings/notifications/events")
def settings_notification_events(
    request: Request,
    event_sberac: str = Form(""),
    event_misto: str = Form(""),
    event_souhrn: str = Form(""),
    ticho_minut: str = Form(""),
    misto_dnu: str = Form(""),
    souhrn_den: str = Form("0"),
    souhrn_hodina: str = Form("9"),
    souhrn_minuta: str = Form("0"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Na co upozornovat a kdy."""
    from ... import notifikace

    for udalost, hodnota in (("sberac", event_sberac), ("misto", event_misto),
                             ("souhrn", event_souhrn)):
        db.set_setting(notifikace.klic_udalosti(udalost), "1" if hodnota else "0")

    db.set_setting("notify_ticho_minut",
                   _clamp(ticho_minut, 5, 1440, notifikace.VYCHOZI_TICHO_MINUT))
    db.set_setting("notify_misto_dnu",
                   _clamp(misto_dnu, 1, 365, notifikace.VYCHOZI_MISTO_DNU))
    db.set_setting("notify_souhrn_den", _clamp(souhrn_den, 0, 6, 0))
    # Hodina a minuta chodí ze dvou polí; `_clamp` ohlídá rozsah a doplní
    # nulu, `tasks.platny_cas` je poslední pojistka - do rozvrhu se nesmí
    # dostat nic jiného než "HH:MM".
    cas = (f"{int(_clamp(souhrn_hodina, 0, 23, 9)):02d}"
           f":{int(_clamp(souhrn_minuta, 0, 59, 0)):02d}")
    db.set_setting("notify_souhrn_cas", tasks.platny_cas(cas, "09:00"))

    db.forget_settings()
    _flash(request, "Uloženo.", "success")
    return RedirectResponse("/settings?section=notifications", status_code=303)


@router.post("/settings/notifications/texts")
async def settings_notification_texts(
    request: Request,
    account: dict[str, Any] = Depends(require_admin),
):
    """Vlastní znění zpráv. Prázdné pole = výchozí text.

    Čte se z formuláře přímo, protože zpráv přibývá a každá má dvě
    pole; podepsat se pod všechna zvlášť znamená na jedno zapomenout.
    Text shodný s výchozím se ukládá jako prázdný - jinak by po změně
    jazyka aplikace zůstal ten starý, i když ho nikdo nepřepsal.
    """
    formular = await request.form()
    for zprava in notifikace.ZPRAVY:
        for cast, vychozi in (("predmet", zprava.predmet), ("text", zprava.text)):
            if cast == "text" and not zprava.text:
                continue
            hodnota = str(formular.get(f"text_{zprava.klic}_{cast}") or "").strip()
            hodnota = hodnota[:notifikace.MAX_DELKA_TEXTU]
            if hodnota in (vychozi, i18n.translate(vychozi)):
                hodnota = ""
            db.set_setting(notifikace.klic_textu(zprava.klic, cast), hodnota)

    db.forget_settings()
    _flash(request, "Texty zpráv uloženy. Prázdné pole znamená výchozí znění.",
           "success")
    return RedirectResponse("/settings?section=notifications#texty", status_code=303)


@router.post("/settings/notifications/test")
async def settings_notification_test(
    request: Request,
    kanal: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Zkusebni zprava jednim kanalem.

    Kanal se porovnava proti pevnemu seznamu - z formulare se do
    odesilani nesmi dostat nic jineho.
    """
    from ... import notifikace

    if kanal not in notifikace.KANALY:
        _flash(request, "Neznámý kanál.", "error")
        return RedirectResponse("/settings?section=notifications", status_code=303)

    vysledek = await notifikace.posli_zkusebni(kanal)
    if vysledek.get("ok"):
        _flash(request, "Zkušební zpráva odeslána.", "success")
    else:
        _flash(request, "Nepodařilo se odeslat: {chyba}", "error",
               chyba=vysledek.get("chyba") or "?")
    return RedirectResponse("/settings?section=notifications", status_code=303)
