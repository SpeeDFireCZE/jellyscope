"""Sezónní výzdoba: který svátek je dnes.

Kolem Vánoc, Halloweenu, Silvestra a Velikonoc se Jellyscope sám ozdobí
(ozdoby u loga, sváteční barva, animace přes stránku - viz
`static/udalosti/`). Tady se jen rozhoduje, **jestli a který** svátek
zrovna je; jak vypadá, je věc šablon a CSS.

Správce smí výzdobu vypnout (`ui_udalosti`), ne spustit ani upravit.
Kdo ji chce vidět mimo sezónu, podstrčí serveru datum proměnnou
prostředí `JELLYSCOPE_UDALOST_DATUM=2026-12-24`.
"""
from __future__ import annotations

import functools
import logging
import os
import re
from datetime import date, timedelta
from typing import Any, Callable

from . import db

log = logging.getLogger("jellyscope.udalosti")

UDALOSTI: tuple[str, ...] = ("vanoce", "halloween", "silvestr", "velikonoce")

# Odkud vzít „dnes" ještě před proměnnou prostředí. V produ zůstává None;
# nastavuje ho jen vývojový nástroj (jellyscope/vyvoj.py, do produ se
# nekopíruje), aby šlo v devu přepnout datum v Nastavení.
ZDROJ_DATA: Callable[[], date | None] | None = None

PROMENNA = "JELLYSCOPE_UDALOST_DATUM"
# Jen RRRR-MM-DD: fromisoformat by vzal i "20261224" nebo čas za datem.
_TVAR_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# Nesmysl v proměnné se hlásí jednou za běh, ne při každé stránce.
_nahlaseno = False


@functools.lru_cache(maxsize=64)
def velikonoce(rok: int) -> date:
    """Velikonoční neděle pro libovolný rok (gregoriánský kalendář).

    Anonymní gregoriánský algoritmus (Meeus/Jones/Butcher): první neděle
    po prvním jarním úplňku, celočíselně a bez tabulky. Ověřeno proti
    `dateutil.easter` pro roky 1583-4099.
    """
    a = rok % 19                          # kde je rok v 19letém cyklu Měsíce
    b, c = divmod(rok, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30    # dny od 21. 3. k úplňku
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - dny z úplňku do neděle
    m = (a + 11 * h + 19 * l) // 433
    mesic = (h + l - 7 * m + 90) // 25
    den = (h + l - 7 * m + 33 * mesic + 19) % 32
    return date(rok, mesic, den)


def udalost_pro(datum: date) -> str | None:
    """Klíč svátku pro daný den, nebo None. Obě meze oken platí.

    Silvestr začíná až 27. 12.: týden před Novým rokem (25. 12.) by
    přebil Vánoce. Okna se nepřekrývají - Velikonoce leží vždy mezi
    15. 3. a 26. 4.
    """
    m, d = datum.month, datum.day
    if m == 10 and 24 <= d <= 31:
        return "halloween"
    if m == 12 and 17 <= d <= 26:
        return "vanoce"
    if (m == 12 and d >= 27) or (m == 1 and d == 1):
        return "silvestr"
    nedele = velikonoce(datum.year)
    if nedele - timedelta(days=7) <= datum <= nedele + timedelta(days=1):
        return "velikonoce"
    return None


def dnes() -> date:
    """Dnešní datum pro výzdobu.

    Pořadí: vývojový háček (`ZDROJ_DATA`), proměnná prostředí, skutečné
    místní datum serveru (stejně jako Historie). Proměnná se čte pokaždé,
    takže se dá měnit bez restartu v testech.
    """
    global _nahlaseno
    if ZDROJ_DATA is not None:
        podstrcene = ZDROJ_DATA()
        if podstrcene is not None:
            return podstrcene
    text = os.environ.get(PROMENNA, "").strip()
    if text:
        if _TVAR_DATA.fullmatch(text):
            try:
                return date.fromisoformat(text)
            except ValueError:      # 2026-02-30
                pass
        if not _nahlaseno:
            _nahlaseno = True
            log.warning("neplatne JELLYSCOPE_UDALOST_DATUM %r, plati dnesni datum", text)
    return date.today()


def zapnuto() -> bool:
    """Má správce výzdobu zapnutou? Výchozí ano."""
    return (db.get_setting("ui_udalosti", "1") or "1").strip() != "0"


def aktivni() -> str | None:
    """Svátek, který se má teď ukázat - nebo None (vypnuto, mimo sezónu)."""
    if not zapnuto():
        return None
    return udalost_pro(dnes())


def slaveny_rok(datum: date | None = None) -> int:
    """Rok, jehož příchod Silvestr slaví (finále hry): v prosinci ten příští."""
    datum = datum or dnes()
    return datum.year + 1 if datum.month == 12 else datum.year


def rok_pro_sablonu() -> str:
    """Slavený rok pro šablonu (globální `udalost_rok()` v Jinja).

    Jako `pro_sablonu`: vývojový zdroj data čte databázi, a když ta
    zrovna neodpoví, platí skutečné dnešní datum - stránka nespadne.
    """
    try:
        return str(slaveny_rok())
    except Exception:  # noqa: BLE001 - výzdoba je navíc, stránka má přednost
        return str(slaveny_rok(date.today()))


def pohyb(account: dict[str, Any] | None) -> bool:
    """Smí se výzdoba hýbat? Ne jen tehdy, když si to účet vypnul.

    Systémové „omezit animace" řeší CSS a JS samy (prefers-reduced-motion),
    tady jde jen o volbu na stránce Můj účet. Neznámá hodnota = pohyb.
    """
    return not (account and account.get("udalosti_pohyb") == "0")


def pro_sablonu() -> str:
    """Klíč svátku pro šablonu (globální `udalost()` v Jinja), jinak "".

    Výzdoba nesmí shodit stránku: když nejde přečíst nastavení (výpadek
    databáze), prostě se neukáže.
    """
    try:
        return aktivni() or ""
    except Exception:  # noqa: BLE001 - výzdoba je navíc, stránka má přednost
        return ""
