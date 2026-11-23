"""Numbers turned into text for people.

"1099511627776" tells nobody anything. "1,0 TB" does. All such conversions are
here, so they need not repeat in the templates and look the same everywhere.

They register as Jinja2 filters, so a template then says:
    {{ item.size_bytes | bytes }}
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, UTC
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import TIME_FORMAT
from .i18n import translate as _t

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def ticks_seconds(value: Any) -> float:
    """Jellyfin's "tick" to seconds. One tick is 100 nanoseconds.

    An empty value is zero, not an error: the length is missing for items not
    synchronised yet, and the template must not fail because of it.
    """
    try:
        return float(value or 0) / 10_000_000
    except (TypeError, ValueError):
        return 0.0


def bytes_human(value: Any) -> str:
    """The size of a file in units a person can read."""
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

    # Bytes and kilobytes without a decimal place - for them it would carry
    # no information.
    return f"{number_in_unit(size, whole=index <= 1)} {_UNITS[index]}"


def number_in_unit(value: float, whole: bool = False) -> str:
    """The number of a size as it is written: "28,8" or "145".

    One place for the tiles and the charts. When it was in two, they drifted
    apart: a chart wrote "29 TB" where the tile next to it wrote "28,8 TB",
    and that made half a terabyte of difference between two numbers about
    the same thing.
    """
    if whole or value >= 100:
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:.1f}".replace(".", ",")


def hours_human(value: Any) -> str:
    """Hours as "3 h 42 min" instead of "3.7"."""
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


def hours_hhmm(value: Any) -> str:
    """Hours as "12:24" instead of "12 h".

    In the charts, hours are written as decimals, and above ten the decimal
    places are cut off, because "40" reads better on an axis than "39,6". For
    a particular value, though, it means a day with 34.52 hours reports 35
    hours - nearly half an hour that never was. And "34,5 h" says half an
    hour to nobody; hours and minutes do.

    Switched on in Settings -> Interface; see exact_time().
    """
    try:
        hours = float(value or 0)
    except (TypeError, ValueError):
        return "-"
    if hours < 0:
        hours = 0.0
    minutes = int(round(hours * 60))
    return f"{minutes // 60}:{minutes % 60:02d}"


def exact_time() -> bool:
    """Is the time in the charts to be written exact to the minute?

    Rounding is the default - round numbers read better in a chart, and they
    are enough for most people. Whoever needs precision switches it in the
    settings.
    """
    from .db import get_setting          # only here, so there is no cycle

    try:
        return (get_setting("ui_exact_time", "0") or "0").strip() == "1"
    except Exception:                       # noqa: BLE001
        # Drawing a chart must not depend on whether the database is at
        # hand. That happens during a migration (the settings table does not
        # exist yet) and in tests that try just the shape of a chart.
        # Rounding is the default state, so nothing is lost.
        return False


def seconds_human(value: Any) -> str:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return "-"
    return hours_human(seconds / 3600)


def timecode(value: Any) -> str:
    """The time in the shape a player shows: 1:17:04, or 17:04.

    Why not "1 h 17 min" as elsewhere: this number is compared with what the
    viewer has on the screen in front of them. When it says 00:01:17 there,
    the same has to stand here too, otherwise one has to recount it.

    Hours are printed only when there are some - "00:17:04" for a
    quarter-hour episode only takes up space.
    """
    try:
        total = int(float(value or 0))
    except (TypeError, ValueError):
        return "-"
    total = max(0, total)

    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def bitrate_human(value: Any) -> str:
    """The bitrate in Mb/s - the unit quality is talked about in."""
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
    """The resolution group from the dimensions of the picture.

    The **width** decides, not the height. Movies shot in a widescreen format
    have the black bars baked into the picture, so a 4K movie in 2.40:1 has
    the dimensions 3840x1608 - the height 1608 is below the 1080p thresholds,
    and by it such a movie would come out as "1080p". The width 3840 is
    unambiguous, though.

    The height is used only as a fallback when the width is not known (older
    records) - and also as a safeguard for content that is narrow and tall
    the other way round.
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


# How the inner shortcut of a range is written for a person. "DOVI" is our
# notation, not the name of the technology, and "unknown" was an outright
# programmer's shortcut - and both used to show in the chart as they stand in
# the database.
RANGE_LABELS = {"SDR": "SDR", "HDR": "HDR", "DOVI": "Dolby Vision"}


def czech_date(iso: Any) -> str:
    """2026-08-01 -> 1.8.2026. For the form and for sentences about a period."""
    if not iso:
        return ""
    try:
        day = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return ""
    return f"{day.day}.{day.month}.{day.year}"


def video_range_human(value: Any) -> str:
    """The dynamic range for a person. What we do not know is admitted."""
    return RANGE_LABELS.get(str(value or "").strip().upper()) or _t("unknown#neuter")


def number(value: Any) -> str:
    """A number with a space after the thousands - the Czech convention."""
    try:
        return f"{int(value or 0):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "-"


def percent(value: Any, decimals: int = 0) -> str:
    try:
        return f"{float(value or 0):.{decimals}f} %".replace(".", ",")
    except (TypeError, ValueError):
        return "-"


def zone():
    """The application's time zone. `None` means "what the system says".

    One place for the whole application. Without it, it depended on the
    zone of the machine - and when the server ran in UTC and someone looked
    from Prague, the evening peak in the charts "came" two hours earlier
    than they lived it.

    A `ZoneInfo` is returned, not an offset in hours: the offset changes
    during the year (summer time), and a record stored in March has to be
    converted differently from a record of December.
    """
    from .db import get_setting          # only here, so there is no cycle

    name = (get_setting("app_timezone", "") or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A nonsense zone must not bring the printing of times down - the system's instead.
        return None


def step_label(minutes: Any) -> str:
    """The length of a chart's step: "6 h", "45 min", "2 dny"."""
    try:
        m = int(minutes or 0)
    except (TypeError, ValueError):
        return "-"
    if m <= 0:
        return "-"
    if m < 90:
        return f"{m} min"
    if m < 60 * 36:
        hours = round(m / 60)
        return f"{hours} h"
    days = round(m / 60 / 24)
    return plural("{n} days", days)


def datetime_human(value: Any) -> str:
    """A stored time (UTC) converted to the application's time and printed nicely."""
    if not value:
        return "-"
    text = str(value)
    parsed = _parse_any(text)
    if parsed is None:
        return text

    local = parsed.astimezone(zone())
    return local.strftime("%d.%m.%Y %H:%M")


def plural(key: str, n: Any, /, **values: Any) -> str:
    """A text with a count in the current language, in the form the number needs.

    `key` is the text without the form suffix ("{n} records"), see
    i18n.translate_plural.
    """
    from . import i18n

    return i18n.translate_plural(key, n, None, **values)

def relative_human(value: Any) -> str:
    """"4 minutes ago" - for current data more readable than the exact time."""
    parsed = _parse_any(str(value or ""))
    if parsed is None:
        return "-"

    delta = datetime.now(UTC) - parsed
    if delta < timedelta(0):
        return _t("in a moment")
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return _t("just now")
    # The preposition and the unit are part of the translation: English says
    # "5 min ago", Czech "před 5 min" - the order differs, so putting it
    # together from pieces would not do.
    if seconds < 3600:
        return _t("{n} min ago").format(n=seconds // 60)
    if seconds < 86400:
        return _t("{n} h ago").format(n=seconds // 3600)
    days = seconds // 86400
    if days < 31:
        return plural("{n} days ago", days)
    return datetime_human(value)


def _parse_any(text: str) -> datetime | None:
    """Tries to read a time in several formats.

    Our own format is just one, but Jellyfin sends times its own way (with a T,
    with a Z, with a varying number of decimal places), so we have to be lenient.
    """
    if not text:
        return None

    candidate = text.strip().replace("Z", "+00:00")

    # Jellyfin sends up to 7 decimal places of a second, Python handles 6 at most.
    # So the fractional part is cut to six digits and the rest (a possible time
    # zone) is left in place.
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
        # A time without a zone is taken for UTC - that is how we store it.
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

    return None


# Why Jellyfin transcodes - its codes (TranscodeReason) and a sentence for a person.
#
# The code used to be printed as it came: "VideoCodecNotSupported,
# AudioCodecNotSupported". In History it did not even wrap for the missing
# space, and in the chart on Insights it was cut to "AudioCodecNotSupp...".
# The sentences are short and start with a small letter so they can be put
# one after another ("nepodporovany kodek videa, nepodporovany kodek zvuku");
# only the start of the whole text gets a capital letter. A code that is not
# here (a newer Jellyfin) shows as it came - better a foreign code than an
# empty space.
TRANSCODE_REASONS: dict[str, str] = {
    "ContainerNotSupported": "unsupported container",
    "VideoCodecNotSupported": "unsupported video codec",
    "AudioCodecNotSupported": "unsupported audio codec",
    "SubtitleCodecNotSupported": "unsupported subtitle format",
    "AudioIsExternal": "audio in a separate file",
    "SecondaryAudioNotSupported": "unsupported additional audio track",
    "VideoProfileNotSupported": "unsupported video profile",
    "VideoLevelNotSupported": "unsupported video level",
    "VideoResolutionNotSupported": "unsupported resolution",
    "VideoBitDepthNotSupported": "unsupported video bit depth",
    "VideoFramerateNotSupported": "unsupported frame rate",
    "RefFramesNotSupported": "unsupported number of reference frames",
    "AnamorphicVideoNotSupported": "unsupported anamorphic video",
    "InterlacedVideoNotSupported": "unsupported interlaced video",
    "AudioChannelsNotSupported": "unsupported number of audio channels",
    "AudioProfileNotSupported": "unsupported audio profile",
    "AudioSampleRateNotSupported": "unsupported audio sample rate",
    "AudioBitDepthNotSupported": "unsupported audio bit depth",
    "ContainerBitrateExceedsLimit": "bitrate over the limit",
    "VideoBitrateNotSupported": "video bitrate too high",
    "AudioBitrateNotSupported": "audio bitrate too high",
    "UnknownVideoStreamInfo": "missing video details",
    "UnknownAudioStreamInfo": "missing audio details",
    "DirectPlayError": "direct play error",
    "VideoRangeTypeNotSupported": "unsupported HDR format",
    "VideoCodecTagNotSupported": "unsupported video codec tag",
    "StreamCountExceedsLimit": "too many streams in the file",
}


def transcode_reasons_text(text: Any) -> str:
    """Jellyfin's reason codes ("A,B" and "A, B") as a sentence for a person."""
    codes = [code.strip() for code in str(text or "").split(",") if code.strip()]
    sentences = [_t(TRANSCODE_REASONS[code]) if code in TRANSCODE_REASONS else code for code in codes]
    result = ", ".join(sentences)
    return result[:1].upper() + result[1:]


def episode_code(row: Any) -> str:
    """"S01E03" from a row with `parent_index_number` and `index_number`, otherwise empty.

    In one place, not in every template: the copies asked for truthiness, and
    the episode code quietly disappeared for specials (season 0).
    """
    season = (row or {}).get("parent_index_number")
    episode = (row or {}).get("index_number")
    if season is None or episode is None:
        return ""
    return f"S{int(season):02d}E{int(episode):02d}"


def register(env: Any) -> None:
    """Adds every filter to the Jinja2 environment."""
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
        # The date as a person writes it into the period filter - so it is one
        # date on the page, not two different notations of the same.
        "czech_date": czech_date,
        "datetime": datetime_human,
        "relative": relative_human,
        "step_label": step_label,
        "video_range": video_range_human,
        "transcode_reasons": transcode_reasons_text,
        "episode_code": episode_code,
    })
    # A number with the right word form ("1 album", "3 alba", "5 alb") - a function, it takes three keys.
