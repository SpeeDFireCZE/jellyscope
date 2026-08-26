"""Prevod cisel na text pro cloveka.

"1099511627776" nikomu nic nerekne. "1,0 TB" ano. Vsechny takove prevody
jsou tady, aby se v sablonach nemusely opakovat a aby vypadaly vsude stejne.

Registruji se jako filtry Jinja2, takze v sablone se pak pise:
    {{ item.size_bytes | bytes }}
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import TIME_FORMAT
from .i18n import translate as _t

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def ticks_seconds(value: Any) -> float:
    """Jellyfinovy "tik" na sekundy. Jeden tik je 100 nanosekund.

    Prazdna hodnota je nula, ne chyba: delka chybi u polozek, ktere se
    jeste nesynchronizovaly, a sablona kvuli tomu nema spadnout.
    """
    try:
        return float(value or 0) / 10_000_000
    except (TypeError, ValueError):
        return 0.0


def bytes_human(value: Any) -> str:
    """Velikost souboru v jednotkach, ktere clovek precte."""
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "-"
    if size <= 0:
        return "-"

    index = 0
    while size >= 1024 and index < len(_UNITS) - 1:
        size /= 1024
        index += 1

    decimals = 0 if index <= 1 or size >= 100 else 1
    return f"{size:.{decimals}f} {_UNITS[index]}".replace(".", ",")


def hours_human(value: Any) -> str:
    """Hodiny jako "3 h 42 min" misto "3.7"."""
    try:
        hours = float(value or 0)
    except (TypeError, ValueError):
        return "-"
    if hours <= 0:
        return "0 min"

    total_minutes = int(round(hours * 60))
    if total_minutes < 60:
        return f"{total_minutes} min"

    whole_hours, minutes = divmod(total_minutes, 60)
    if whole_hours >= 100:
        return f"{whole_hours:,} h".replace(",", " ")
    return f"{whole_hours} h {minutes:02d} min"


def seconds_human(value: Any) -> str:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return "-"
    return hours_human(seconds / 3600)


def timecode(value: Any) -> str:
    """Cas ve tvaru, jaky ukazuje prehravac: 1:17:04, nebo 17:04.

    Proc ne "1 h 17 min" jako jinde: tohle cislo se porovnava s tim, co ma
    divak pred sebou na obrazovce. Kdyz tam stoji 00:01:17, musi to samé
    stat i tady, jinak si to clovek musi prepocitavat.

    Hodiny se vypisuji jen kdyz nejake jsou - "00:17:04" u ctvrthodinoveho
    dilu jen zabira misto.
    """
    try:
        celkem = int(float(value or 0))
    except (TypeError, ValueError):
        return "-"
    celkem = max(0, celkem)

    hodiny, zbytek = divmod(celkem, 3600)
    minuty, sekundy = divmod(zbytek, 60)
    if hodiny:
        return f"{hodiny}:{minuty:02d}:{sekundy:02d}"
    return f"{minuty}:{sekundy:02d}"


def bitrate_human(value: Any) -> str:
    """Bitrate v Mb/s - to je jednotka, ve ktere se o kvalite mluvi."""
    try:
        bits = float(value or 0)
    except (TypeError, ValueError):
        return "-"
    if bits <= 0:
        return "-"
    if bits < 1_000_000:
        return f"{bits / 1000:.0f} kb/s"
    return f"{bits / 1_000_000:.1f} Mb/s".replace(".", ",")


def resolution_human(height: Any, width: Any = None) -> str:
    """Skupina rozliseni z rozmeru obrazu.

    Rozhoduje **sirka**, ne vyska. Filmy natocene v sirokoúhlem formatu
    maji cerne pruhy uz zapecene v obraze, takze 4K film ve formatu 2.40:1
    ma rozmery 3840x1608 - vyska 1608 je nizsi nez 1080p prahy a podle ni
    by takovy film vysel jako "1080p". Sirka 3840 je pritom jednoznacna.

    Vyska se pouzije jen jako zaloha, kdyz sirku neznáme (starsi zaznamy)
    - a taky jako pojistka pro obsah, ktery je naopak uzky a vysoky.
    """
    try:
        h = int(height or 0)
        w = int(width or 0)
    except (TypeError, ValueError):
        return "-"
    if h <= 0 and w <= 0:
        return "-"
    if w >= 3400 or h >= 2000:
        return "4K"
    if w >= 1800 or h >= 1000:
        return "1080p"
    if w >= 1200 or h >= 700:
        return "720p"
    if w >= 900 or h >= 500:
        return "576p"
    return f"{h}p" if h > 0 else f"{w}x?"


def number(value: Any) -> str:
    """Cislo s mezerou po tisicich - ceska konvence."""
    try:
        return f"{int(value or 0):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "-"


def percent(value: Any, decimals: int = 0) -> str:
    try:
        return f"{float(value or 0):.{decimals}f} %".replace(".", ",")
    except (TypeError, ValueError):
        return "-"


def zona():
    """Casova zona aplikace. `None` znamena "co rika system".

    Jedno misto pro celou aplikaci. Bez nej zalezelo na tom, jakou zonu
    ma stroj - a kdyz server bezel v UTC a clovek se dival z Prahy,
    vecerni spicka v grafech "nastavala" o dve hodiny driv, nez ji zazil.

    Vraci se `ZoneInfo`, ne posun v hodinach: posun se behem roku meni
    (letni cas) a ulozeny udaj z brezna se musi prepocitat jinak nez
    udaj z prosince.
    """
    from .db import get_setting          # az tady, at nevznikne kruh

    jmeno = (get_setting("app_timezone", "") or "").strip()
    if not jmeno:
        return None
    try:
        return ZoneInfo(jmeno)
    except (ZoneInfoNotFoundError, ValueError):
        # Nesmyslna zona nesmi shodit vypis casu - radeji systemova.
        return None


def usek(minut: Any) -> str:
    """Delka useku grafu: "6 h", "45 min", "2 dny"."""
    try:
        m = int(minut or 0)
    except (TypeError, ValueError):
        return "-"
    if m <= 0:
        return "-"
    if m < 90:
        return f"{m} min"
    if m < 60 * 36:
        hodin = round(m / 60)
        return f"{hodin} h"
    dnu = round(m / 60 / 24)
    return f"{dnu} " + (_t("dny") if dnu < 5 else _t("dnů"))


def datetime_human(value: Any) -> str:
    """Ulozeny cas (UTC) prevedeny na cas aplikace a hezky vypsany."""
    if not value:
        return "-"
    text = str(value)
    parsed = _parse_any(text)
    if parsed is None:
        return text

    local = parsed.astimezone(zona())
    return local.strftime("%d.%m.%Y %H:%M")


def relative_human(value: Any) -> str:
    """"pred 4 minutami" - u aktualnich udaju citelnejsi nez presny cas."""
    parsed = _parse_any(str(value or ""))
    if parsed is None:
        return "-"

    delta = datetime.now(timezone.utc) - parsed
    if delta < timedelta(0):
        return _t("za chvíli")
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return _t("právě teď")
    # Predlozka i jednotka jsou soucasti prekladu: anglictina rika
    # "5 min ago", cestina "před 5 min" - poradi se lisi, takze slozit
    # to z kousku by nestacilo.
    if seconds < 3600:
        return _t("před {n} min").format(n=seconds // 60)
    if seconds < 86400:
        return _t("před {n} h").format(n=seconds // 3600)
    days = seconds // 86400
    if days < 31:
        return _t("před {n} dny").format(n=days)
    return datetime_human(value)


def _parse_any(text: str) -> datetime | None:
    """Zkusi precist cas v nekolika formatech.

    Nas vlastni format je jen jeden, ale Jellyfin posila casy po svem
    (s T, s Z, s ruznym poctem desetinnych mist), takze musime byt shovivavi.
    """
    if not text:
        return None

    candidate = text.strip().replace("Z", "+00:00")

    # Jellyfin posila az 7 desetinnych mist sekundy, Python zvlada nejvyse 6.
    # Zlomkovou cast proto oreze na sest cislic a zbytek (pripadnou casovou
    # zonu) necha na miste.
    match = re.match(r"^(.*?)\.(\d+)(.*)$", candidate)
    if match:
        head, fraction, tail = match.groups()
        candidate = f"{head}.{fraction[:6]}{tail}"

    for parser in (
        lambda t: datetime.fromisoformat(t),
        lambda t: datetime.strptime(t, TIME_FORMAT),
    ):
        try:
            parsed = parser(candidate)
        except ValueError:
            continue
        # Cas bez zony povazujeme za UTC - tak ho ukladame.
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

    return None


def register(env: Any) -> None:
    """Prida vsechny filtry do prostredi Jinja2."""
    env.filters.update({
        "bytes": bytes_human,
        "ticks": ticks_seconds,
        "hours": hours_human,
        "seconds": seconds_human,
        "timecode": timecode,
        "bitrate": bitrate_human,
        "resolution": resolution_human,
        "number": number,
        "percent": percent,
        "datetime": datetime_human,
        "relative": relative_human,
        "usek": usek,
    })
