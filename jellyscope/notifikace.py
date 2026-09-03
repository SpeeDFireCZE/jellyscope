# -*- coding: utf-8 -*-
"""Upozorneni: kdyz se neco pokazi, aplikace to ma komu rict.

Jellyscope je pasivni - otevres ho, kdyz te neco zajima. Sberac se ale
pta Jellyfinu kazdych par vterin a kdyz mu vyprsi token nebo se zmeni
adresa serveru, aplikace bezi dal, stranky vypadaji normalne a historie
se TISE zastavi. Zjistis to za tri tydny podle diry v grafu - a ta dira
uz je vetsinou navzdy: prehravani se da doimportovat z Playback
Reportingu, ale jazyk stopy ani bitrate v nem nejsou.

**Co tenhle modul umet nemuze:** upozornit na to, ze Jellyscope nebezi.
Kdyz spadne kontejner, nema kdo poslat zpravu. "Bezi vubec aplikace"
patri do Uptime Kumy nebo podobneho hlidace, ne sem.

Proto jen tri veci, u kterych zprava opravdu neco rekne:

* **sberac** - bezi, ale nesbira,
* **misto** - na disku dochazi,
* **souhrn** - tydenni prehled; jedina, ktera neni porucha.

Zamerne tu NENI "vysla nova verze". Je to v rozhrani, hlida to uloha
a jako zprava je to presne ten druh sumu, kvuli kteremu clovek prestane
kanal cist - a pak mu unikne ta prvni polozka.

Kazda zprava se posila JEN PRI ZMENE stavu. Kdyby chodila pri kazdem
behu ulohy, byla by z hlidace budicek po deseti minutach a vyplo by se
to hned prvni den.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from . import db
from .i18n import translate as _t

log = logging.getLogger(__name__)

# Kanaly, kterymi se da poslat. Poradi je poradi v nastaveni.
KANALY: tuple[str, ...] = ("smtp", "discord", "telegram")

# Udalosti, na ktere se da upozornit. Kazda se da zvlast vypnout.
UDALOSTI: tuple[str, ...] = ("sberac", "misto", "souhrn")

# Kolik minut bez uspesneho dotazu uz znamena "nesbira". Sberac se pta
# po vterinach, takze pulhodina je jistota, ne netrpelivost - kratke
# vypadky site by jinak posilaly zpravu pokazde.
VYCHOZI_TICHO_MINUT = 30

# Pod kolik dnu do zaplneni disku se ozvat.
VYCHOZI_MISTO_DNU = 14

CAS_FORMAT = "%Y-%m-%d %H:%M:%S"


def klic(kanal: str, jmeno: str) -> str:
    """Nazev nastaveni jednoho pole kanalu."""
    return f"notify_{kanal}_{jmeno}"


def klic_udalosti(udalost: str) -> str:
    return f"notify_event_{udalost}"


def _stav_klic(udalost: str) -> str:
    """Kam se pamatuje, jak to dopadlo minule - kvuli hlaseni pri ZMENE."""
    return f"notify_state_{udalost}"


# Pole, ktera se nesmi dostat do sablony ani do logu.
TAJNA = tuple(klic(kanal, jmeno) for kanal, jmeno in
              (("smtp", "heslo"), ("discord", "webhook"), ("telegram", "token")))


def kanal_zapnuty(kanal: str) -> bool:
    return db.get_setting(klic(kanal, "enabled"), "") == "1"


def _neprazdne(kanal: str, *pole: str) -> bool:
    return all(db.get_setting(klic(kanal, jmeno), "").strip() for jmeno in pole)


def kanal_nastaveny(kanal: str) -> bool:
    """Ma kanal vyplnene vsechno, bez ceho se poslat neda?"""
    if kanal == "smtp":
        return _neprazdne("smtp", "host", "komu")
    if kanal == "discord":
        return _neprazdne("discord", "webhook")
    if kanal == "telegram":
        return _neprazdne("telegram", "token", "chat")
    return False


def zapnute_kanaly() -> list[str]:
    return [k for k in KANALY if kanal_zapnuty(k) and kanal_nastaveny(k)]


def udalost_zapnuta(udalost: str) -> bool:
    return db.get_setting(klic_udalosti(udalost), "") == "1"


# ---------------------------------------------------------------------------
# Odesilani
# ---------------------------------------------------------------------------

async def _posli_discord(predmet: str, text: str) -> None:
    import httpx

    webhook = db.get_setting(klic("discord", "webhook"), "").strip()
    async with httpx.AsyncClient(timeout=15) as client:
        odpoved = await client.post(webhook, json={
            "content": f"**{predmet}**\n{text}",
            # Do zpravy jdou nazvy titulu z knihovny. Film pojmenovany
            # "@everyone" by jinak pingnul cely kanal - a je to jmeno
            # souboru, ne pokyn. Discord bez tohohle pole zminky
            # zpracovava.
            "allowed_mentions": {"parse": []},
        })
        odpoved.raise_for_status()


async def _posli_telegram(predmet: str, text: str) -> None:
    import httpx

    token = db.get_setting(klic("telegram", "token"), "").strip()
    chat = db.get_setting(klic("telegram", "chat"), "").strip()
    async with httpx.AsyncClient(timeout=15) as client:
        odpoved = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": f"{predmet}\n\n{text}"})
        odpoved.raise_for_status()


def _posli_smtp_blokujici(predmet: str, text: str) -> None:
    """Vlastni funkce, protoze smtplib je blokujici - viz _posli_smtp()."""
    import smtplib
    from email.message import EmailMessage

    host = db.get_setting(klic("smtp", "host"), "").strip()
    port = db.get_int_setting(klic("smtp", "port"), 1, 65535, 587)
    uzivatel = db.get_setting(klic("smtp", "uzivatel"), "").strip()
    heslo = db.get_setting(klic("smtp", "heslo"), "")
    odesilatel = db.get_setting(klic("smtp", "odesilatel"), "").strip() or uzivatel
    komu = db.get_setting(klic("smtp", "komu"), "").strip()
    tls = db.get_setting(klic("smtp", "tls"), "1") == "1"

    zprava = EmailMessage()
    zprava["Subject"] = predmet
    zprava["From"] = odesilatel or komu
    zprava["To"] = komu
    zprava.set_content(text)

    # Port 465 je SMTPS (sifrovane od prvniho bajtu), 587 je STARTTLS
    # (nesifrovane spojeni, ktere se povysi). Zamenit je znamena, ze
    # spojeni jen tise visi az do timeoutu.
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as spojeni:
            if uzivatel:
                spojeni.login(uzivatel, heslo)
            spojeni.send_message(zprava)
        return

    with smtplib.SMTP(host, port, timeout=20) as spojeni:
        if tls:
            spojeni.starttls()
        if uzivatel:
            spojeni.login(uzivatel, heslo)
        spojeni.send_message(zprava)


async def _posli_smtp(predmet: str, text: str) -> None:
    # smtplib je blokujici a cekani na cizi server trva - ve vlakne, ať
    # se nezastavi cely server.
    await asyncio.to_thread(_posli_smtp_blokujici, predmet, text)


ODESILATELE = {
    "smtp": _posli_smtp,
    "discord": _posli_discord,
    "telegram": _posli_telegram,
}


def bez_tajemstvi(text: str) -> str:
    """Vysktrne z textu vsechno, co je pristupovym udajem.

    Chyba od HTTP klienta nese v sobe CELOU adresu - a u Telegramu je
    v adrese token bota, u Discordu je adresa webhooku sama tajemstvim.
    Ta hlaska se pritom uklada a **ukazuje v nastaveni**, takze by token
    svitil na obrazovce.

    Maskuje se podle ulozenych hodnot, ne podle vzoru: vzor by u nazvu,
    ktere se meni mezi sluzbami, vzdycky neco minul.
    """
    for klic_tajemstvi in TAJNA:
        hodnota = db.get_setting(klic_tajemstvi, "").strip()
        # Kratke hodnoty se nemaskuji: nahradit tri znaky by z hlasky
        # udelalo hadanku a k utajeni by to stejne nepomohlo.
        if len(hodnota) >= 8:
            text = text.replace(hodnota, "…")
        # Webhook je cela adresa; v hlasce byva i bez schematu.
        if hodnota.startswith("https://") and len(hodnota) > 16:
            text = text.replace(hodnota[8:], "…")
    return text


async def posli(predmet: str, text: str,
                jen_kanal: str | None = None) -> list[dict[str, Any]]:
    """Posle zpravu vsemi zapnutymi kanaly. Vraci vysledek za kazdy.

    Selhani jednoho kanalu neshodi ostatni: kdyz nefunguje SMTP, zprava
    ma porad dojit na Discord. Chyba se vraci jako text, protoze
    v nastaveni se ma ukazat, PROC to nefungovalo - "nepodarilo se"
    neni k nicemu.
    """
    kanaly = [jen_kanal] if jen_kanal else zapnute_kanaly()
    vysledky: list[dict[str, Any]] = []
    for kanal in kanaly:
        if kanal not in ODESILATELE:
            continue
        try:
            await ODESILATELE[kanal](predmet, text)
            vysledky.append({"kanal": kanal, "ok": True, "chyba": ""})
        except Exception as chyba:            # noqa: BLE001 - sit selhava ruzne
            # Text chyby projde maskovanim: HTTP klient do nej dava celou
            # adresu, a v te je u Telegramu token a u Discordu cely webhook.
            popis = bez_tajemstvi(str(chyba))
            log.warning("upozorneni pres %s se nepodarilo poslat: %s: %s",
                        kanal, type(chyba).__name__, popis)
            vysledky.append({"kanal": kanal, "ok": False, "chyba": popis})

    if vysledky:
        db.set_setting("notify_last_send", db.utcnow())
        db.set_setting("notify_last_error",
                       "; ".join(f"{v['kanal']}: {v['chyba']}"
                                 for v in vysledky if not v["ok"]))
    return vysledky


# ---------------------------------------------------------------------------
# Co hlidame
# ---------------------------------------------------------------------------

def _sberac_nesbira() -> tuple[bool, str]:
    """Bezi sberac, ale uz dlouho nic neprineslo? Vraci (spatne, proc)."""
    from . import collector

    stav = db.get_setting(collector.STATUS_KEY, "unknown")
    if stav == "demo":
        return False, ""            # ukazkovy rezim nic nesbira zamerne

    chyba = db.get_setting(collector.ERROR_KEY, "").strip()
    if stav == "error" and chyba:
        return True, chyba

    posledni = db.get_setting(collector.LAST_POLL_KEY, "").strip()
    if not posledni:
        # Jeste nikdy nic - to neni porucha, to je cerstva instalace.
        return False, ""

    ticho = db.get_int_setting("notify_ticho_minut", 5, 1440, VYCHOZI_TICHO_MINUT)
    try:
        kdy = datetime.strptime(posledni, CAS_FORMAT)
    except ValueError:
        return False, ""
    minut = (datetime.now(timezone.utc).replace(tzinfo=None) - kdy).total_seconds() / 60
    if minut >= ticho:
        return True, _t("poslední úspěšný dotaz na Jellyfin byl před {minut} minutami"
                        ).format(minut=int(minut))
    return False, ""


def _dochazi_misto() -> tuple[bool, str]:
    """Blizi se zaplneni disku? Jen tam, kam aplikace na soubory vidi."""
    from . import stats

    rust = stats.rust_knihovny(90)
    zbyva = rust.get("dnu_do_konce")
    if zbyva is None:
        # Volne misto neznáme, nebo knihovna neroste - v obou pripadech
        # neni co predpovidat.
        return False, ""

    hranice = db.get_int_setting("notify_misto_dnu", 1, 365, VYCHOZI_MISTO_DNU)
    if int(zbyva) > hranice:
        return False, ""
    return True, _t("při současném tempu dojde místo za {dnu} dnů").format(dnu=int(zbyva))


def _tydenni_souhrn() -> str:
    """Text tydenniho prehledu."""
    from . import formatting, stats

    prehled = stats.overview(7)
    hodin = float(prehled.get("watched_seconds") or 0) / 3600.0
    radky = [
        f"{_t('Odsledováno')}: {formatting.hours_human(hodin)}",
        f"{_t('Spuštění')}: {formatting.number(prehled.get('plays') or 0)}",
        f"{_t('Diváci')}: {formatting.number(prehled.get('users') or 0)}",
    ]

    tituly = stats.top_items(7, limit=3)
    if tituly:
        radky.append("")
        radky.append(_t("Nejsledovanější tituly") + ":")
        radky += [f"  {t['label']} - {formatting.hours_human(t['hours'])}"
                  for t in tituly]

    lide = stats.top_users(7, limit=3)
    if lide:
        radky.append("")
        radky.append(_t("Nejaktivnější uživatelé") + ":")
        radky += [f"  {u['label']} - {formatting.hours_human(u['hours'])}"
                  for u in lide]
    return "\n".join(radky)


async def _resi_poruchu(udalost: str, nadpis: str,
                        spatne: bool, proc: str) -> bool:
    """Posle zpravu jen tehdy, kdyz se stav ZMENIL.

    Bez toho by pri kazdem behu ulohy chodila tataz veta, clovek by si
    upozorneni vypnul a bylo by to k nicemu.
    """
    minule = db.get_setting(_stav_klic(udalost), "ok")
    ted = "spatne" if spatne else "ok"
    if minule == ted:
        return False

    db.set_setting(_stav_klic(udalost), ted)
    if spatne:
        await posli(f"Jellyscope: {nadpis}", proc)
    else:
        # Ze se to spravilo, je stejna informace jako ze se to pokazilo -
        # bez ni clovek neví, jestli ma jit neco resit.
        await posli(f"Jellyscope: {_t('zase to běží')}", nadpis)
    return True


def _je_cas_na_souhrn(ted: datetime) -> bool:
    """Je ten spravny den a hodina - a jeste se tenhle tyden neposilal?"""
    den = db.get_int_setting("notify_souhrn_den", 0, 6, 0)     # 0 = pondeli
    cas = db.get_setting("notify_souhrn_cas", "09:00").strip() or "09:00"
    try:
        hodina, minuta = (int(c) for c in cas.split(":", 1))
    except ValueError:
        hodina, minuta = 9, 0

    if ted.weekday() != den:
        return False
    if (ted.hour, ted.minute) < (hodina, minuta):
        return False

    # Jednou tydne, ne pri kazdem behu ulohy po zbytek dne.
    posledni = db.get_setting("notify_souhrn_odeslan", "").strip()
    return posledni != ted.date().isoformat()


async def zkontroluj(ted: datetime | None = None) -> dict[str, Any]:
    """Projde hlidane veci a posle, co je potreba. Pousti to uloha."""
    if not zapnute_kanaly():
        return {"status": "ok", "message": _t("Žádný kanál není nastavený.")}

    ted = ted or datetime.now()
    odeslano = []

    if udalost_zapnuta("sberac"):
        spatne, proc = _sberac_nesbira()
        if await _resi_poruchu("sberac", _t("sběrač nesbírá"), spatne, proc):
            odeslano.append("sberac")

    if udalost_zapnuta("misto"):
        spatne, proc = _dochazi_misto()
        if await _resi_poruchu("misto", _t("dochází místo"), spatne, proc):
            odeslano.append("misto")

    if udalost_zapnuta("souhrn") and _je_cas_na_souhrn(ted):
        db.set_setting("notify_souhrn_odeslan", ted.date().isoformat())
        await posli(f"Jellyscope: {_t('týdenní souhrn')}", _tydenni_souhrn())
        odeslano.append("souhrn")

    if not odeslano:
        return {"status": "ok", "message": _t("Nic nového k hlášení.")}
    return {"status": "ok",
            "message": _t("Odesláno: {co}").format(co=", ".join(odeslano))}


async def posli_zkusebni(kanal: str) -> dict[str, Any]:
    """Zkusebni zprava z tlacitka v nastaveni.

    Bez ni se nastaveni SMTP overuje az tim, ze se neco pokazi - a to je
    presne ta chvile, kdy uz musi fungovat.
    """
    if not kanal_nastaveny(kanal):
        return {"ok": False, "chyba": _t("Kanál není vyplněný.")}
    vysledky = await posli(
        _t("Jellyscope: zkušební zpráva"),
        _t("Když tohle čteš, upozornění fungují."), jen_kanal=kanal)
    return vysledky[0] if vysledky else {"ok": False, "chyba": "?"}


def stav() -> dict[str, Any]:
    """Co o upozorneních potrebuje stranka nastaveni."""
    return {
        "kanaly": [{"klic": k,
                    "zapnuty": kanal_zapnuty(k),
                    "nastaveny": kanal_nastaveny(k)} for k in KANALY],
        "udalosti": [{"klic": u, "zapnuta": udalost_zapnuta(u)} for u in UDALOSTI],
        "posledni_odeslani": db.get_setting("notify_last_send", ""),
        "posledni_chyba": db.get_setting("notify_last_error", ""),
        "ticho_minut": db.get_int_setting("notify_ticho_minut", 5, 1440,
                                          VYCHOZI_TICHO_MINUT),
        "misto_dnu": db.get_int_setting("notify_misto_dnu", 1, 365,
                                        VYCHOZI_MISTO_DNU),
        "souhrn_den": db.get_int_setting("notify_souhrn_den", 0, 6, 0),
        "souhrn_cas": db.get_setting("notify_souhrn_cas", "09:00") or "09:00",
    }
