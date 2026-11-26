"""Drawing charts.

Charts are drawn here in two ways and the choice between them is not random:

* **HTML + CSS** - horizontal bars, a split bar, a heat map.
  Everything that is essentially a rectangle with a label. The browser fits
  it to the window's width by itself, the text stays readable and nothing
  has to scroll.

* **SVG** - line and area charts, sparklines.
  Where a real curve is needed that you cannot put together from rectangles.

Originally absolutely everything was in SVG and it had one unpleasant
property: the picture had a fixed width, so on a narrower window it had to
scroll sideways. That is bad for statistics you want to run your eyes over.
HTML does not have this trouble - so most charts moved there.

Colours are never written directly. We draw with CSS variables
(var(--series-1)), which are in style.css - thanks to that the charts have the
right colours in the light and the dark mode, without this file having to know
about it.
"""

from __future__ import annotations

import html
import itertools
import json
import re
import math
from typing import Any
from collections.abc import Callable, Sequence

from .i18n import translate as _t
from .i18n import translate_plural

# Colour slots for telling series apart. The order is on purpose - it is picked
# so that even a person with a colour vision deficiency tells neighbouring pairs
# apart. Never change it and never add a ninth colour; rather merge the rest into "Other".
SERIES_SLOTS = 8


# A counter for the names of gradients. Two charts on one page must not reach
# for the same "id": the browser would hand the second the fill of the first.
# A growing number is enough - within one page it is unique.
_TRANSITION_ORDER = itertools.count(1)


def _transition(slot: int, name: str, strength: float = 0.34,
                colour: str | None = None) -> str:
    """A vertical gradient from the series' colour to nothing.

    A full semi-transparent fill has two flaws. Where two series overlap a
    third, murky colour appears, in which both get lost. And at the bottom
    there is the most ink exactly where there is no information - what matters
    is the area's top edge, that is the line itself.

    The gradient gets rid of both: at the top it holds the series' colour, at
    the bottom it fades.
    """
    colour = colour or f"var(--series-{slot})"
    return (
        f'<linearGradient id="{name}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{_colour(colour)}" '
        f'stop-opacity="{strength:.2f}" />'
        f'<stop offset="100%" stop-color="{_colour(colour)}" '
        f'stop-opacity="0.015" />'
        f"</linearGradient>"
    )


def _step_path(points: Sequence[tuple[float, float]]) -> str:
    """A stepped line - holds the value, then jumps to the next.

    For quantities that **do not change smoothly**. The concurrent flow from
    the server is exactly such: as long as nobody starts or ends, it stays the
    same, and the moment a stream starts it jumps. A smoothed curve would draw a
    rise between two points that never happened - and above all it would make
    a soft hill over half an hour out of ten minutes of playback.

    The difference from _path(): there the curve is a shape (how many hours
    were watched when), here it is a record of a state.
    """
    if not points:
        return ""
    pieces = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        pieces.append(f"H{x2:.1f}")
        if abs(y2 - y1) > 0.01:
            pieces.append(f"V{y2:.1f}")
    return " ".join(pieces)


def _path(points: Sequence[tuple[float, float]]) -> str:
    """A smooth line through the given points - as `d` for <path>.

    We interpolate with a **monotone cubic** (Fritsch-Carlson), not a free
    spline. The difference is essential and it is the reason this maths is here:

    * A free spline "swings out" between points - it shoots above the highest
      measured value and below the lowest. The chart then shows a peak that
      never happened, and a zero in the middle of the week looks like a
      negative number.
    * A monotone cubic is guaranteed to stay between two points' values
      between them. When the series goes up, the curve goes up; when a point is
      the maximum, the curve does not rise above it.

    One reservation remains that no interpolation removes: there is no
    in-between day between Thursday and Friday, so the arc between them is
    drawn in. So the exact numbers are always carried by the tooltip and the
    table under the chart - the curve is a shape, not a source of values.
    """
    n = len(points)
    if n == 0:
        return ""
    if n == 1:
        return f"M{points[0][0]:.1f},{points[0][1]:.1f}"

    dx = [points[i + 1][0] - points[i][0] for i in range(n - 1)]
    dy = [points[i + 1][1] - points[i][1] for i in range(n - 1)]
    # The slopes between neighbours. A zero distance would divide by zero - such
    # a point we just skip (two points on the same x make no sense).
    slopes = [(dy[i] / dx[i]) if dx[i] else 0.0 for i in range(n - 1)]

    tangents = [slopes[0]]
    for i in range(1, n - 1):
        if slopes[i - 1] * slopes[i] <= 0:
            # A change of direction = a peak or a valley. A horizontal tangent is
            # exactly what keeps the curve below (or above) the measured point.
            tangents.append(0.0)
        else:
            w1 = 2 * dx[i] + dx[i - 1]
            w2 = dx[i] + 2 * dx[i - 1]
            tangents.append((w1 + w2) / (w1 / slopes[i - 1] + w2 / slopes[i]))
    tangents.append(slopes[-1])

    parts = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(n - 1):
        h = dx[i] / 3
        parts.append(
            f"C{points[i][0] + h:.1f},{points[i][1] + tangents[i] * h:.1f} "
            f"{points[i + 1][0] - h:.1f},{points[i + 1][1] - tangents[i + 1] * h:.1f} "
            f"{points[i + 1][0]:.1f},{points[i + 1][1]:.1f}"
        )
    return " ".join(parts)


# What may pass into an attribute as a colour: a theme variable, hex, a
# colour name or a function rgb()/hsl(). Narrow on purpose - the colours are
# decided by the application, not by the data.
_COLOUR = re.compile(
    r"^(?:var\(--[a-z0-9-]{1,40}\)|#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20}"
    r"|(?:rgb|rgba|hsl|hsla)\([0-9.,%\s/-]{1,40}\))$")

# When a colour does not pass, this is drawn. It does not fail and it is visible
# that something is off - better than an empty space or a broken chart.
FALLBACK_COLOUR = "var(--text-muted)"


def _bar_fill(colour: Any) -> str:
    """A gradient along a bar, made of a checked colour.

    It is put together here, not in the chart, so both can be seen in one
    place: that the colour is checked, and that checking the finished gradient
    makes no sense any more - `linear-gradient(...)` is no colour and
    `_colour()` would throw it away. Exactly that once made all bars grey.
    """
    colour = _colour(colour)
    return (f"linear-gradient(90deg, "
            f"color-mix(in oklab, {colour} 62%, var(--surface-1)), "
            f"{colour})")


def _colour(value: Any) -> str:
    """A colour for the `style` or `stop-color` attribute.

    Text is never put into an attribute as it came: `" onmouseover="` would
    jump out of it and become executable code. Only what really is a colour
    passes; the rest is replaced.
    """
    text = str(value or "").strip()
    return text if _COLOUR.match(text) else FALLBACK_COLOUR


def _e(value: Any) -> str:
    """Escaping text into HTML.

    When a movie's name containing "<" gets into the page, it breaks the
    document - and in a worse case it lets somebody else's tag in. So every
    text that does not come from us goes through here.
    """
    return html.escape(str(value), quote=True)


def _slot_of(segment: dict[str, Any], index: int) -> int:
    """Which series colour slot to use.

    When the segment carries its own "slot" key, we follow it. That matters
    more than it seems: the colour must belong to **the thing**, not to its
    order. Were it coloured by the order, Czech would be blue for one user and
    orange for another - and the chart would lie.
    """
    slot = segment.get("slot")
    if isinstance(slot, int) and 1 <= slot <= SERIES_SLOTS:
        return slot
    return (index % SERIES_SLOTS) + 1


def _fmt(value: float) -> str:
    """A number for a label - without needless decimal places, in the page's notation."""
    from .formatting import in_notation  # only here, so no cycle comes about

    if value >= 100:
        return in_notation(f"{value:,.0f}")
    if value >= 10:
        return f"{value:.0f}"
    if value >= 1:
        return in_notation(f"{value:.1f}".rstrip("0").rstrip("."))
    return in_notation(f"{value:.2f}".rstrip("0").rstrip("."))


# Units where one step means a lot. `_fmt` drops the decimal place above ten -
# for hours that is right ("29 h"), for terabytes it is a half-terabyte error:
# a library of 28.8 TB showed in the tooltip as "29 TB", while the tile next to
# the chart wrote "28,8 TB". Two numbers about the same thing, differing by
# 200 GB, and both "right".
LARGE_UNITS = ("TB", "GB", "MB", "PB")


def _value_label(value: float, unit: str = "") -> str:
    """A value with its unit the way a person reads it.

    One place for all tooltips and the numbers by the bars - axis labels do
    not belong here. On an axis "40" is right (it is a mark of the scale),
    whereas in a tooltip it is a concrete detail, and that can be exact to the
    minute. See formatting.exact_time().
    """
    from . import formatting             # only here, so no cycle comes about

    if unit == "h" and formatting.exact_time():
        return formatting.hours_hhmm(value)
    if unit in LARGE_UNITS:
        # The same function the tile uses - otherwise the two numbers about the
        # same thing drift apart. See formatting.number_in_unit().
        return f"{formatting.number_in_unit(value)} {unit}".strip()
    return f"{_fmt(value)} {unit}".strip()


def _tooltip(title: str, lines: Sequence[dict[str, Any]]) -> str:
    """The tooltip's attributes: a title and lines, each in its colour.

    It is sent as JSON, not as finished HTML: the tooltip is put together by the
    browser through textContent (see base.html), so no tag from a movie's name
    can get into the page. `data-tip` stays as plain text - should the JSON for
    whatever reason not arrive, the tooltip still shows something.
    """
    # The plain text has the title too - it is a fallback for the case the JSON
    # did not arrive, and without the date "2 h" would tell nobody anything.
    text = " · ".join([title] + [str(line["text"]) for line in lines]).strip(" ·")
    data = json.dumps({"heading": title, "rows": list(lines)},
                      ensure_ascii=False, separators=(",", ":"))
    return f'data-tip="{_e(text)}" data-tip-json="{_e(data)}"'


def _empty(message: str) -> str:
    return f'<p class="chart-empty">{_e(message)}</p>'


# ---------------------------------------------------------------------------
# Horizontal bars - comparing sizes
# ---------------------------------------------------------------------------

def hbar_chart(
    rows: Sequence[dict[str, Any]],
    label_key: str,
    value_key: str,
    unit: str = "h",
    limit: int = 12,
    link_prefix: str | None = None,
    link_key: str = "id",
    ranked: bool = False,
    labels_above: bool = False,
    may_open: Callable[[str], bool] | None = None,
) -> str:
    """Comparing values between categories.

    All bars have **the same colour**, unless the row says otherwise (with the
    key "slot"). Shading them by size is a common mistake: the bar's length
    already says the size, and the colour would lose its own meaning by that.

    The whole chart is a grid of three columns - the label, the groove, the
    value. The browser divides the width by itself, so nothing ever scrolls
    sideways.

    `link_prefix` makes a link of the label: the value of `link_key` of the
    given row is appended to the prefix (e.g. "/users/" + user_id). Rows
    without that value stay plain text - a link leading nowhere is worse than
    none. For the same reason `may_open` (the template's may_open() function):
    a row whose page the account may not open stays text.

    `ranked=True` adds the row's number before the label. It is turned on only
    where the chart really is a **ranking** - for the most watched titles or
    the most active users. Where categories are only compared with each other
    (codecs, languages), the number would lie about an order that is not there.

    `labels_above=True` puts the label on its own line above the bar, in full
    length. For labels that are a whole sentence (transcode reasons): in a
    third of the card they would be cut and only a start that says nothing
    would remain. It does not go with ranked - the number would take the first
    column and the grid would fall apart.
    """
    if labels_above and ranked:
        raise ValueError("hbar_chart: labels_above and ranked do not go together")
    rows = [row for row in rows if row is not None][:limit]
    if not rows:
        return _empty(_t("No data yet"))

    maximum = max(float(row.get(value_key) or 0) for row in rows) or 1.0

    parts = ['<div class="hbars labels-above">' if labels_above else '<div class="hbars">']
    for index, row in enumerate(rows):
        value = float(row.get(value_key) or 0)
        label = str(row.get(label_key, ""))
        percent = value / maximum * 100
        # When the row does not say "slot", it is no series - it is one quantity
        # measured for different categories (hours for users, for players).
        # Such a chart gets the application's colour; the series palette is for
        # when it is to be told which line is which.
        own_slot = bool(row.get("slot"))
        slot = _slot_of(row, index) if own_slot else 1
        title = f"{label}: {_value_label(value, unit)}"

        # The label is a link only when it really leads to an existing record.
        # Otherwise the user would click and nothing would happen.
        #
        # `is not None` on purpose, not plain truthiness: an empty prefix is a
        # valid choice for rows that carry the whole address themselves. That
        # is the case of the most watched titles, where a series leads to
        # /series/ and a movie to /item/ - one shared prefix would not do there.
        target = row.get(link_key) if link_prefix is not None else None
        if target and may_open is not None and not may_open(f"{link_prefix}{target}"):
            target = None
        label_html = _e(label)
        if target:
            label_html = (f'<a class="hbar-link" href="{_e(link_prefix)}{_e(target)}">'
                          f'{label_html}</a>')

        # A gradient along the bar: at the base the colour is a bit muted, at
        # the end full. The number stands exactly there, so the eye goes along
        # the bar to it. It does not change the length - only the length carries the value.
        # A gradient along the bar. For a series it goes from a muted to the full
        # colour (another colour would confuse the identity); for a one-colour
        # chart it goes right purple -> blue, that is the brand's gradient.
        if own_slot:
            fill = _bar_fill(f"var(--series-{slot})")
        else:
            fill = "linear-gradient(90deg, var(--data-color-2), var(--data-color))"
        rank = (f'<div class="hbar-rank">{index + 1}</div>') if ranked else ""
        parts.append(
            f'<div class="hbar-row{" is-ranked" if ranked else ""}" data-tip="{_e(title)}">'
            f'{rank}'
            f'<div class="hbar-label">{label_html}</div>'
            f'<div class="hbar-track">'
            f'<div class="hbar-fill" style="width: {percent:.2f}%; '
            f'background: {fill}"></div>'
            f"</div>"
            f'<div class="hbar-value">{_e(_value_label(value, unit))}</div>'
            f"</div>"
        )
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# A split bar - a share of the whole
# ---------------------------------------------------------------------------

# When the bar's parts are not arbitrary categories but STATES with an order,
# they carry the key "role" and take their colour by the meaning. An example is
# "how the server delivers the content": direct play costs the server nothing
# (green), remuxing a little (the application's colour), transcoding hurts (amber).
#
# The colour then says the same as the badge at the playback above - and the
# amber strip at the end is a warning, not a third colour in a row.
ROLE_COLOURS = {
    "good": "var(--good)",
    "info": "var(--data-color)",
    "warning": "var(--warning)",
    "serious": "var(--serious)",
    "critical": "var(--critical)",
    "muted": "var(--text-muted)",
}


def _segment_colour(segment: dict[str, Any], index: int) -> str:
    # An own colour wins over the role. The transcode breakdown uses it: the
    # first variant keeps the role's colour (so it can be told it is a
    # transcode), the next ones get their own shades from the series palette.
    #
    # The second and further variants used to be darkened by mixing with the
    # background. It looked right in the colour table, but not in the chart:
    # the parts tend to be a few pixels narrow and nobody tells three shades of
    # the same brown apart on such an area.
    own = str(segment.get("color") or "").strip()
    if own:
        return own

    role = str(segment.get("role") or "").strip()
    if role not in ROLE_COLOURS:
        return f"var(--series-{_slot_of(segment, index)})"
    return ROLE_COLOURS[role]


def _ink(colour: str) -> str:
    """The colour of text that is readable on the `colour` background.

    White does not fit everywhere: in the dark theme it had 1.9 : 1 on the
    turquoise part. The parts' colours are theme variables (var(--series-3),
    var(--good)) and each has an ink var(--ink-series-3) in CSS, computed for
    every theme apart; tests/test_contrast.py guards it. The application's
    colour has had its ink as --accent-ink since ever.
    """
    variable = re.fullmatch(r"var\(--([a-z0-9-]+)\)", colour)
    if not variable:
        return "#fff"
    if variable.group(1) == "accent":
        return "var(--accent-ink)"
    return f"var(--ink-{variable.group(1)}, #fff)"


def _stack_parts(segments: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The parts a stacked bar draws - and its legend names, so the two cannot drift apart.

    Only parts with a value, the first SERIES_SLOTS of them in colour; the rest is one grey "Other".
    """
    positive = [s for s in segments if float(s.get("value") or 0) > 0]
    parts = positive[:SERIES_SLOTS]
    rest = sum(float(s.get("value") or 0) for s in positive[SERIES_SLOTS:])
    if rest > 0:
        parts.append({"label": _t("Other"), "value": rest, "role": "muted"})
    return parts


def stacked_bar(segments: Sequence[dict[str, Any]], unit: str = "h") -> str:
    """One bar split into parts - "what the whole is made of".

    There is a 2px gap in the background's colour between the parts. We do it
    with a gap, not an outline: an outline adds ink that is not data, and the
    chart gets denser.
    """
    # The shares are of the whole, as the table beside the bar counts them; what does not fit into the colour
    # slots goes into one grey "Other", so the bar still makes 100 %.
    segments = _stack_parts(segments)
    total = sum(float(s.get("value") or 0) for s in segments)
    if total <= 0:
        return _empty(_t("No data yet"))

    parts = ['<div class="stack">']
    for index, segment in enumerate(segments):
        value = float(segment.get("value") or 0)
        share = value / total * 100
        label = segment.get("label", "")
        title = f"{label}: {_value_label(value, unit)} ({share:.0f} %)"

        # The label inside only when the part is wide enough. Cut text inside
        # the bar is worse than none - the legend carries it anyway.
        inner = f"{share:.0f} %" if share >= 9 else ""
        colour = _colour(_segment_colour(segment, index))
        parts.append(
            f'<div class="stack-seg" style="flex-grow: {share:.4f}; '
            f'background: {colour}; color: {_ink(colour)}"'
            f' data-tip="{_e(title)}">'
            f'<span>{inner}</span></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def donut_chart(
    rows: Sequence[dict[str, Any]],
    label_key: str = "label",
    value_key: str = "value",
    unit: str = "h",
    size: int = 220,
) -> str:
    """A pie chart with a hole in the middle.

    A circular sector is a shape that reads worse than bars - areas compare
    badly. It pays off only where the question is "what part of the whole", and
    there are few parts. That is exactly the question "what does this person watch".

    The hole in the middle is no decoration: a ring is read by the **length of
    the arc**, and that is an easier task for the eye than estimating a wedge's
    area. Moreover the total number fits into it, which otherwise has nowhere to go.

    It is drawn with one circle and `stroke-dasharray`: every part is a piece of
    the circumference. Without a single path with hand-computed coordinates -
    and without the mistake that creeps into them for a part bigger than a half circle.
    """
    rows = [row for row in rows if float(row.get(value_key) or 0) > 0]
    if not rows:
        return _empty(_t("No data yet"))

    total = sum(float(row.get(value_key) or 0) for row in rows)
    if total <= 0:
        return _empty(_t("No data yet"))

    radius = size / 2 - 18
    circumference = 2 * math.pi * radius
    centre = size / 2

    parts = [
        '<div class="donut-wrap">',
        f'<svg class="donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img" aria-label="{_e(_t("Genre share"))}">',
    ]

    offset = 0.0
    for index, row in enumerate(rows[:SERIES_SLOTS + 1]):
        value = float(row.get(value_key) or 0)
        share = value / total
        label = str(row.get(label_key, ""))
        percent = share * 100
        # Between the parts we leave a 2px gap in the background's colour - just
        # like for the split bar. With a single part it is left out: the ring
        # would have a needless hole.
        gap = 2.0 if len(rows) > 1 else 0.0
        length = max(1.0, share * circumference - gap)
        parts.append(
            f'<circle class="donut-seg" cx="{centre}" cy="{centre}" r="{radius:.2f}" '
            f'stroke="var(--series-{_slot_of(row, index)})" '
            f'stroke-dasharray="{length:.3f} {circumference:.3f}" '
            f'stroke-dashoffset="{-offset:.3f}" '
            f'data-tip="{_e(label)}: {_e(_value_label(value, unit))} '
            f'({percent:.0f} %)"></circle>'
        )
        offset += share * circumference

    parts.append(
        f'<text class="donut-total" x="{centre}" y="{centre - 4}" '
        f'text-anchor="middle">{_fmt(total)}</text>'
        f'<text class="donut-unit" x="{centre}" y="{centre + 14}" '
        f'text-anchor="middle">{_e(unit)}</text>'
    )
    parts.append("</svg>")

    # A legend with numbers. For a pie it is mandatory twice: the colour alone
    # carries no identity and the exact value cannot be read from a sector.
    parts.append('<ul class="donut-legend">')
    for index, row in enumerate(rows[:SERIES_SLOTS + 1]):
        value = float(row.get(value_key) or 0)
        parts.append(
            f'<li><span class="legend-swatch" '
            f'style="background: var(--series-{_slot_of(row, index)})"></span>'
            f'<span class="donut-name">{_e(row.get(label_key, ""))}</span>'
            f'<span class="donut-value">{value / total * 100:.0f} %</span></li>'
        )
    parts.append("</ul></div>")
    return "".join(parts)


def legend(items: Sequence[dict[str, Any]]) -> str:
    """A legend. With two or more series it is mandatory.

    The legend's text is in the usual text colour, the identity is carried by
    the coloured square next to it. Colouring the text itself is wrong - light
    shades (yellow, turquoise) are unreadable as text on a light background.

    When a series carries the key "tip", the legend item gets a tooltip. It is
    for cases where the name by itself is not enough - say "Other", under
    which more kinds of records hide at once.
    """
    return _legend_list([item for item in items if item is not None][:SERIES_SLOTS])


def stacked_legend(segments: Sequence[dict[str, Any]]) -> str:
    """The legend of a stacked_bar: the very parts the bar draws, its grey "Other" included.

    The plain legend cut the list at the colour slots with no "Other" and kept the parts
    with nothing, so under a bar with more parts it named another eight than the bar drew.
    """
    return _legend_list(_stack_parts(segments))


def _legend_list(items: Sequence[dict[str, Any]]) -> str:
    if not items:
        return ""

    parts = ['<ul class="legend">']
    for index, item in enumerate(items):
        tip = str(item.get("tip") or "").strip()
        attribute = f' data-tip="{_e(tip)}"' if tip else ""
        # The same colour as in the chart, the colour by meaning included - a
        # legend with another shade than the bar is worse than none.
        colour = item.get("color") or _segment_colour(item, index)
        parts.append(
            f'<li{attribute}><span class="legend-swatch" '
            f'style="background: {_colour(colour)}">'
            f'</span>{_e(item.get("label", ""))}</li>'
        )
    parts.append("</ul>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# A heat map - when it is watched
# ---------------------------------------------------------------------------

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# One colour in steps, from light to dark. One colour, not a rainbow: a rainbow
# scale has no order a person could read.
HEAT_STEPS = ["var(--seq-250)", "var(--seq-350)", "var(--seq-450)",
              "var(--seq-550)", "var(--seq-650)"]


def heatmap(grid: Sequence[Sequence[float]], unit: str = "h") -> str:
    """A grid day x hour - when the server is watched most.

    Built on a CSS grid with 24 equally wide columns. The cells thus fit the
    window's width by themselves and the grid never has to scroll.
    """
    maximum = max((max(row) for row in grid if row), default=0.0)
    if maximum <= 0:
        return _empty(_t("No data yet"))

    parts = ['<div class="heatmap">']

    # The header with hours - a label only every three hours, otherwise they would merge.
    parts.append('<div class="heatmap-corner"></div><div class="heatmap-hours">')
    for hour in range(24):
        text = str(hour) if hour % 3 == 0 else ""
        parts.append(f'<span>{text}</span>')
    parts.append("</div>")

    for day_index, row in enumerate(grid):
        # A translated text goes into the page only through _e(): translations are
        # written by people from outside (see TRANSLATING.md) and a tag in them
        # would otherwise end as executable code - the chart is put in through | safe.
        parts.append(
            f'<div class="heatmap-day">{_e(_t(DAY_NAMES[day_index]))}</div>')
        parts.append('<div class="heatmap-cells">')
        for hour, value in enumerate(row):
            if value <= 0:
                colour = "var(--grid)"
            else:
                # Which of the five steps the value belongs to.
                bucket = min(len(HEAT_STEPS) - 1, int(value / maximum * len(HEAT_STEPS)))
                colour = HEAT_STEPS[bucket]
            title = f"{_t(DAY_NAMES[day_index])} {hour}:00 - {_value_label(value, unit)}"
            parts.append(
                f'<i style="background: {_colour(colour)}"'
                f' data-tip="{_e(title)}"></i>'
            )
        parts.append("</div>")

    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# An area chart - the development in time (the only thing that stays in SVG)
# ---------------------------------------------------------------------------

def _nice_ticks(maximum: float, count: int = 4, whole: bool = False) -> list[float]:
    """Round values for the Y axis.

    An axis labelled 0 / 2.5 / 5 / 7.5 reads better than 0 / 2.37 / 4.74 / 7.11.
    The way: compute the "raw" step (the maximum divided by the number of
    steps), round it up to the nearest nice multiple of a power of ten (1, 2,
    2.5 or 5) and walk by it from zero until past the maximum.

    `whole=True` is for count axes (queries, subtitles, requests): the step is a
    whole number, at least 1 - "1.5 queries" do not exist.
    """
    if maximum <= 0:
        return [0.0, 1.0]

    raw_step = maximum / max(1, count)
    if whole:
        raw_step = max(1.0, raw_step)
    magnitude = 10 ** math.floor(math.log10(raw_step))

    step = magnitude * 10  # a fallback, should no multiple fit
    for multiplier in ((1, 2, 5, 10) if whole else (1, 2, 2.5, 5, 10)):
        if magnitude * multiplier >= raw_step:
            step = magnitude * multiplier
            break

    # The axis must always **pass** the maximum, not stop at it. The original
    # condition `while value <= maximum` ended at the last step that still fit
    # below the maximum: with a maximum of 3.4 the axis came out 0-3 and the
    # curve then climbed over the chart into the card's header. So the step is
    # always appended first and only then is it tested whether we are past the maximum.
    ticks: list[float] = []
    value = 0.0
    while len(ticks) < 12:
        ticks.append(round(value, 4))
        if value >= maximum - step * 1e-9:
            break
        value += step
    return ticks or [0.0, 1.0]


# How many hover areas one chart gets at most. Above that each is narrower than
# a finger and the HTML grows by hundreds of kilobytes - see the comment at
# their generation.
MAX_AREAS = 200


def area_chart_multi(
    points: Sequence[dict[str, Any]],
    x_key: str,
    series: Sequence[dict[str, Any]],
    unit: str = "h",
    height: int = 240,
    steps: bool = False,
    selectable: bool = False,
    whole_ticks: bool = False,
) -> str:
    """More series in one time, overlaid over each other.

    `whole_ticks=True` for count charts: the Y axis has whole numbers only.

    `series` is a list of dictionaries {"key": "movie_hours", "label": "Filmy",
    "slot": 1}. Every series has its own colour slot and that **does not
    change** when some series vanishes - the colour belongs to the thing, not
    to the order. Were it coloured by the index, switching a filter would
    recolour what remained, and the chart would lie.

    Why overlaid and not stacked on each other: the overlay allows comparing
    movies and series with each other ("we read the clean height of both
    lines"). A stacked chart can do only the sum - the upper series reads
    badly, because its base jumps with the lower one.

    The fill is therefore semi-transparent: where the series overlap, both are
    visible. The line stays full, so it can be followed in the overlay too.
    """
    if not points or not series:
        return _empty(_t("No data yet"))

    width = 760
    # The left margin is for the axis labels. They must fit into it even when on
    # a narrow screen they are bigger in viewBox units (see .axis-label in the
    # mobile part of the style) - otherwise a label runs out of the chart and is
    # cut. On a desktop it takes two percent of the area out of 760 units.
    margin_left, margin_right = 80, 22
    margin_top, margin_bottom = 16, 30
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    all_values = [
        float(point.get(entry["key"]) or 0)
        for entry in series for point in points
    ]
    maximum = max(all_values) if all_values else 0.0
    ticks = _nice_ticks(maximum or 1.0, whole=whole_ticks)
    scale_max = max(ticks[-1], maximum) or 1.0

    def px(index: int) -> float:
        if len(points) == 1:
            return margin_left + plot_width / 2
        return margin_left + plot_width * index / (len(points) - 1)

    def py(value: float) -> float:
        return margin_top + plot_height * (1 - value / scale_max)

    # Every series has its own gradient and its name must be unique on the
    # page - see _transition().
    number = next(_TRANSITION_ORDER)
    gradients = {
        entry.get("slot", 1): f"gradient-{number}-{index}"
        for index, entry in enumerate(series)
    }
    # A series can carry its own colour instead of a slot number. The chart on
    # the Overview uses it, where there are two to three lines that are to keep
    # the application's colours; where there are more categories (languages),
    # the series palette stays, because it is built so they do not get mixed up.
    colours = {entry.get("slot", 1): entry.get("color")
               for entry in series if entry.get("color")}

    def series_colour(slot: int) -> str:
        # The colour is decided by whoever calls the chart, but into an attribute
        # it goes only through _colour() - one place for all its uses (the line,
        # the dot, the gradient, the legend), so none can be forgotten.
        return _colour(colours.get(slot) or f"var(--series-{slot})")

    # Selecting by dragging: the chart says where on the axis which moment lies,
    # and the browser manages the rest. The coordinates are in the viewBox, not
    # in pixels - the SVG scales with the page's width, so JS does the conversion.
    #
    # Two modes depending on what is on the axis:
    #   "time" - a point is a moment (the live curve). Any stretch can be selected.
    #   "days" - a point is a whole day. The selection is **rounded to days**,
    #           because the chart knows nothing finer anyway; were it to send a
    #           time, it would look more exact than it really is.
    can_select = selectable and len(points) > 1
    days = [str(b.get("day") or "") for b in points]
    has_time = can_select and all(b.get("time") for b in points)
    has_days = can_select and all(re.match(r"^\d{4}-\d{2}-\d{2}$", d) for d in days)
    can_select = has_time or has_days

    select_attributes = ""
    if can_select:
        axis = (f' data-x-from="{margin_left}" data-x-to="{margin_left + plot_width}"'
                f' data-y-from="{margin_top}" data-y-to="{margin_top + plot_height}"')
        if has_time:
            select_attributes = (f' data-selection="time"{axis}'
                                 f' data-time-from="{int(points[0]["time"])}"'
                                 f' data-time-to="{int(points[-1]["time"])}"')
        else:
            select_attributes = f' data-selection="days"{axis} data-days="{",".join(days)}"' 

    parts: list[str] = [
        f'<svg class="chart{" chart-selectable" if can_select else ""}" '
        f'viewBox="0 0 {width} {height}" role="img"{select_attributes} '
        f'aria-label="{_e(translate_plural("Trend over time, {n} series", len(series)))}">',
        "<defs>",
        # The more series, the weaker the fill. With one curve the area helps -
        # it shows the volume. With two the areas overlap and a third, murky
        # colour appears, in which both get lost; there the information is
        # carried mainly by the line itself. So with two or more series the fill fades.
        *(_transition(slot, name, strength=0.34 if len(series) == 1 else 0.16,
                      colour=colours.get(slot))
          for slot, name in gradients.items()),
        "</defs>",
    ]

    baseline = margin_top + plot_height
    for tick in ticks:
        y = py(tick)
        # The zero line is the axis everything is measured from - that one stays
        # full. The others are only guides so the height can be estimated; dots
        # are enough and do not take the chart's attention.
        is_base = abs(y - baseline) < 0.5
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{margin_left + plot_width}" '
            f'y2="{y:.1f}" stroke="var({"--axis" if is_base else "--grid"})" '
            f'stroke-width="1"'
            + ("" if is_base else ' stroke-dasharray="1 5" stroke-linecap="round"')
            + " />"
        )
        parts.append(
            f'<text x="{margin_left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="axis-label">{_fmt(tick)}</text>'
        )

    for entry in series:
        # `None` MEANS "the series does not apply here", not zero. Drawing a
        # zero of it would be a drop to the axis that did not happen - for the
        # library's growth curve there are two series that alternate (the
        # computed past and the measured snapshots), and one always ends somewhere.
        #
        # A missing key is still zero: all other charts rely on a series without
        # a value for a given day drawing a zero.
        runs: list[list[tuple[float, float]]] = []
        run: list[tuple[float, float]] = []
        for index, point in enumerate(points):
            value = point.get(entry["key"], 0)
            if value is None:
                if run:
                    runs.append(run)
                run = []
                continue
            run.append((px(index), py(float(value))))
        if run:
            runs.append(run)

        slot = entry.get("slot", 1)
        for coordinates in runs:
            # One point has no length, so it would make a line of zero width -
            # the chart looked empty, even though there was watching that day. It
            # happens for a period shorter than two days (say a stretch selected by
            # dragging). We spread it over the chart's width: it reads as "the whole
            # time it was like this", which is exactly what one point means.
            #
            # It applies only to a series that IS one point. A lonely point in the
            # middle of a split series carries no such statement, so a dot is made of it.
            if len(coordinates) == 1:
                if len(runs) == 1:
                    level = coordinates[0][1]
                    coordinates = [(margin_left, level),
                                   (margin_left + plot_width, level)]
                else:
                    parts.append(
                        f'<circle cx="{coordinates[0][0]:.1f}" '
                        f'cy="{coordinates[0][1]:.1f}" r="2.5" '
                        f'fill="{series_colour(slot)}" />')
                    continue
            # The line and the area follow the same shape - otherwise the fill would
            # peek out from under the line. See _path() and _step_path().
            line = _step_path(coordinates) if steps else _path(coordinates)
            area = (
                f"M{coordinates[0][0]:.1f},{baseline:.1f} L"
                + line[1:]
                + f" L{coordinates[-1][0]:.1f},{baseline:.1f} Z"
            )
            parts.append(f'<path d="{area}" fill="url(#{gradients[slot]})" />')
            parts.append(
                f'<path d="{line}" fill="none" stroke="{series_colour(slot)}" '
                f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />'
            )

    step = max(1, len(points) // 6)
    for index in range(0, len(points), step):
        # A date from the database starts with the year ("2026-08-14"). Nobody
        # cares about the year on the axis and it takes space, so we cut it off -
        # but only when it really is there. It used to be cut always, so the
        # label "02.08. 22:11" turned into ". 22:11".
        raw = str(points[index].get(x_key, ""))
        label = raw[5:] if re.match(r"^\d{4}-", raw) else raw
        parts.append(
            f'<text x="{px(index):.1f}" y="{height - 10}" text-anchor="middle" '
            f'class="axis-label">{_e(label)}</text>'
        )

    # One area carries the values of all series at once - a person asks "what
    # was there that day", not "what were the movies that day".
    #
    # There are at most MAX_AREAS areas, even when there are a thousand
    # points. Each of them is its own <g> with the tooltip in an attribute, so
    # for a fine curve (the live flow has a point every five minutes) it would
    # make almost a megabyte of HTML - and the mouse cannot hit an area
    # narrower than a few pixels anyway.
    area_step = max(1, len(points) // MAX_AREAS + (1 if len(points) % MAX_AREAS else 0))
    hit_width = (plot_width / max(1, len(points) - 1) * area_step
                 if len(points) > 1 else plot_width)
    for index in range(0, len(points), area_step):
        # An area can stand for more points - and then it must not show the
        # first one's value, but **the highest in that band**. A peak tends to be
        # one point wide; were the tooltip to report its neighbour, the chart
        # would be good only to look at and the exact number could not be read from it.
        band = points[index:index + area_step]
        peaks = {}
        for entry in series:
            key = entry["key"]
            # Days where the series does not apply (None) do not count in the peaks -
            # otherwise the tooltip at the computed part of the curve would report
            # "0 GB" for a series that has nothing to say there at all.
            valid = [b for b in band if b.get(key, 0) is not None]
            if not valid:
                continue
            highest = max(valid, key=lambda b: float(b.get(key) or 0))
            peaks[key] = (float(highest.get(key) or 0), highest)

        # The time is taken from the series that peaks highest in the band - that
        # is the moment a person asks about.
        main = (max(peaks.values(), key=lambda pair: pair[0])[1]
                if peaks else band[0])
        x = px(index)

        # The title is the time, under it every series on its line and in its
        # colour - in the overlay of two lines it is the only way to tell which
        # number belongs to which.
        tooltip_lines = [
            {"text": f'{entry["label"]}: {_value_label(peaks[entry["key"]][0], unit)}',
             "color": series_colour(entry.get("slot", 1))}
            for entry in series if entry["key"] in peaks
        ]
        if main.get("stream_count"):
            tooltip_lines.append(
                {"text": _t("streams at once: {n}").format(n=main["stream_count"])})
        # Lines a point brings itself, already translated ("z toho hudba 120 GB" in the growth).
        tooltip_lines.extend({"text": str(line)} for line in main.get("tip_lines") or [])
        tip = _tooltip(str(main.get(x_key, "")), tooltip_lines)

        body = "".join(
            f'<circle cx="{x:.1f}" '
            f'cy="{py(peaks[entry["key"]][0]):.1f}" r="4" '
            f'fill="{series_colour(entry.get("slot", 1))}" '
            f'stroke="var(--surface-1)" stroke-width="2" />'
            for entry in series if entry["key"] in peaks
        )
        parts.append(
            f'<g class="chart-hit" {tip}>'
            f'<rect x="{max(0.0, x - hit_width / 2):.1f}" y="{margin_top}" '
            f'width="{hit_width:.1f}" height="{plot_height}" fill="transparent" />'
            f'<line x1="{x:.1f}" y1="{margin_top}" x2="{x:.1f}" '
            f'y2="{baseline:.1f}" stroke="var(--axis)" stroke-width="1" />'
            f"{body}</g>"
        )

    if can_select:
        # The selection rectangle is drawn by JS, but it belongs in the document
        # already now - this way CSS styles it like everything else and JS only
        # changes the coordinates. `pointer-events: none`, so it does not take
        # the tooltips under it.
        parts.append(
            f'<rect class="chart-selection" x="0" y="{margin_top}" width="0" '
            f'height="{plot_height}" hidden />'
        )

    parts.append("</svg>")
    if can_select:
        # A feature there is no way to learn about does not exist. The cursor
        # hints that something can be done with the chart, but not what - so,
        # just like for the map below, there is one line under the chart.
        parts.append(
            f'<p class="card-note chart-hint">'
            f'{_e(_t("Drag across the chart to pick a range."))}</p>'
        )
    return "".join(parts)


def sparkline(
    points: Sequence[dict[str, Any]],
    x_key: str = "day",
    y_key: str = "hours",
    unit: str = "h",
    width: int = 760,
    height: int = 64,
) -> str:
    """A sparkline under the main number.

    It takes the same data as the big chart below and behaves the same: on
    hover it shows how much there was which day. It used to be only a
    decorative shape without values - but what looks like a chart, a person
    tries to use as a chart. So either it is to be interactive, or it is not to
    be there at all.

    Axes and labels are not here on purpose: the exact numbers are carried by
    the chart below and its table. This is a quick shape, not a replacement.
    """
    values = [float(p.get(y_key) or 0) for p in points]
    if not values or max(values) <= 0:
        return ""
    # One point is no development. It happens for an own period shorter than two
    # days (say a stretch selected by dragging in the chart) - and an empty area
    # was then drawn under the main number, which looked like a missing chart.
    if len(values) < 2:
        return ""

    maximum = max(values)
    # The padding must cover the dot's radius with its ring (3.5 + 2). Without it
    # a dot at the edge or at the peak would be drawn half out of the picture -
    # the SVG is not clipped here, so it would climb over the card's edge.
    #
    # It holds even after the end dot vanished: the dots show only on hover,
    # but they are drawn in the same place.
    padding = 5
    pad_x = 6
    step = (width - pad_x * 2) / max(1, len(values) - 1)

    coordinates = [
        (pad_x + index * step,
         height - padding - (value / maximum) * (height - padding * 2))
        for index, value in enumerate(values)
    ]
    line = _path(coordinates)
    area = (
        f"M{coordinates[0][0]:.1f},{height} L"
        + line[1:]
        + f" L{coordinates[-1][0]:.1f},{height} Z"
    )
    # The hover areas are clipped to the picture's width - the outer ones would
    # otherwise stick out half a step and catch the mouse outside the chart.
    def _hit(x: float, half: float) -> tuple[float, float]:
        left = max(0.0, x - half)
        return left, min(float(width), x + half) - left

    name = f"gradient-mini-{next(_TRANSITION_ORDER)}"
    parts = [
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_e(translate_plural("Trend over time, {n} days", len(points)))}">',
        f"<defs>{_transition(1, name, strength=0.28, colour='var(--data-color)')}</defs>",
        f'<path d="{area}" fill="url(#{name})" />',
        f'<path d="{line}" fill="none" stroke="var(--data-color)" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />',
    ]

    # No permanent dot at the end.
    #
    # There used to be one - as a highlight of the last day. But all the other
    # dots appear only on hover, so the end one looked like a dot that "got
    # stuck". The big chart below (area_chart_multi) does not have it either; two
    # shapes of the same chart are to behave the same.

    # Invisible hover areas - one for every day. They must be wide enough to
    # hit; the dot itself is a small target.
    hit_width = step if len(coordinates) > 1 else width
    for index, (x, y) in enumerate(coordinates):
        tip = _tooltip(
            str(points[index].get(x_key, "")),
            [{"text": _value_label(values[index], unit),
              "color": "var(--data-color)"}])
        hit_x, hit_w = _hit(x, hit_width / 2)
        parts.append(
            f'<g class="chart-hit" {tip}>'
            f'<rect x="{hit_x:.1f}" y="0" '
            f'width="{hit_w:.1f}" height="{height}" fill="transparent" />'
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" '
            f'stroke="var(--axis)" stroke-width="1" />'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="var(--data-color)" '
            f'stroke="var(--surface-1)" stroke-width="2" />'
            f"</g>"
        )

    parts.append("</svg>")
    return "".join(parts)

# ---------------------------------------------------------------------------
# A world map - where they watch from
# ---------------------------------------------------------------------------

def world_map(places: Sequence[dict[str, Any]], mode: str = "click",
              basemap: str = "drawn", detailed: bool = False,
              detailed_tag: str = "") -> str:
    """A world map with dots by where playback came from.

    It is drawn by the same hand as the rest of the charts: one SVG, no
    library. The land's outline is in `worldmap` (Natural Earth 1:110M) in Web
    Mercator, like the dots, so one `viewBox` is enough.

    **Levels.** Clusters are computed for the zoom levels 1×, 2×, 4× …
    (map_clusters.clusters). Every dot carries from which to which level it
    holds (`data-from`, `data-to`), and the script (static/map.js) shows those
    that belong to the current zoom; without the script only 1× is visible.
    Under the dots are the basemap layers - the built-in outline, the detailed
    offline map and the OSM tiles; the script fills those two.

    `mode` says how the map zooms ("wheel" with the wheel, "click" by
    clicking), `basemap` is "drawn" or "osm" and `detailed` says whether
    the detailed map is downloaded - all only attributes for the script.

    A dot's size grows with the **square root** of the watched time and is
    computed within the level on which the dot appears: the biggest cluster
    there has the full size. The eye compares areas, so double the time has
    double the area.
    """
    from . import formatting, map_clusters
    from .detailed_map import DETAIL_LEVELS
    from .worldmap import BORDERS, LAND, WIDTH, DEFAULT_VIEW, HEIGHT

    if not places:
        return _empty(_t("No data yet"))

    levels = map_clusters.clusters(list(places))
    # How far it can zoom. With the bare drawn map to the level where the last
    # cluster splits (at least 20×) - the coarse outline shows nothing further.
    # With the detailed map or OSM there is something to see under the dots even
    # where nothing falls apart any more, so always up to the highest level.
    if detailed or basemap == "osm":
        zoom_limit = map_clusters.MAX_LEVEL
    else:
        zoom_limit = map_clusters.max_zoom(levels, len(places))
    default_view = " ".join(f"{v:g}" for v in DEFAULT_VIEW)

    parts = [
        f'<svg class="map" viewBox="{default_view}" '
        f'data-default="{default_view}" data-world="0 0 {WIDTH} {HEIGHT}" '
        f'data-max="{zoom_limit}" '
        f'data-levels="{" ".join(str(u["level"]) for u in levels)}" '
        # The most watched time on every level - the script computes the dot's
        # size from it within the level that is visible right now.
        f'data-maxima="{" ".join(str(max(int(s["seconds"] or 0) for s in u["clusters"])) for u in levels)}" '
        f'data-zoom="{_e(mode if mode in ("click", "wheel") else "click")}" '
        f'data-basemap="{_e(basemap if basemap in ("drawn", "osm") else "drawn")}" '
        f'data-detailed="{"1" if detailed else "0"}" '
        f'data-detailed-version="{_e(detailed_tag)}" '
        f'data-grids="{" ".join(str(DETAIL_LEVELS[p]["n"]) for p in sorted(DETAIL_LEVELS))}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="{_e(_t("Where they watch from"))}">',
        # A glow around the point. One transparent gradient for all dots: a
        # gradient is computed in the element's coordinates, so it fits its size
        # by itself and need not be defined for each apart.
        # The dots on the map are no series - it is one quantity in different
        # places. The application's colour, the same as the curve on the Overview.
        # A sharp white core is part of the gradient (the first 15 %) - one
        # circle per dot instead of two, a page with a thousand clusters is half the size.
        '<defs><radialGradient id="map-glow">'
        '<stop offset="0%" stop-color="#fff" stop-opacity="0.9" />'
        '<stop offset="15%" stop-color="#fff" stop-opacity="0.85" />'
        '<stop offset="19%" stop-color="var(--data-color)" stop-opacity="0.95" />'
        '<stop offset="45%" stop-color="var(--data-color)" stop-opacity="0.35" />'
        '<stop offset="100%" stop-color="var(--data-color)" stop-opacity="0" />'
        '</radialGradient></defs>',
        f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" '
        f'fill="var(--surface-2)" />',
        # The basemap from the bottom up: the built-in outline (always), the
        # detailed offline map and the OSM tiles (both filled by the script). The
        # detailed map hides the built-in outline when zoomed in - otherwise the
        # coarse land would climb into the sea.
        f'<g class="map-base"><path d="{LAND}" fill="var(--axis)" '
        f'fill-opacity="0.35" stroke="var(--axis)" stroke-width="0.2" /></g>',
        '<g class="map-detailed"></g>',
        '<g class="map-tile"></g>',
        # The countries' borders are drawn only after zooming in - looking at the
        # whole world they would be just a net of lines over the continents. The
        # line's width shrinks with the view when zooming, so it stays hairline.
        # We draw the borders in the text colour, not the chart line's: --axis has
        # almost the same shade as the land's fill, so the borders blended into it.
    ]
    # Over a real map from OSM the coarse 1:110M borders would lie kilometres
    # next to the real ones - with OSM they are not drawn at all.
    if basemap != "osm":
        parts.append(
            f'<path class="map-borders" d="{BORDERS}" fill="none" '
            f'stroke="var(--text-muted)" stroke-width="0.16" '
            f'stroke-opacity="0" stroke-linejoin="round" />')

    # A cluster that does not change over several levels (the same places) is
    # in the page once - with the range data-from / data-to. Every level apart
    # would make a megabyte of SVG for a few hundred places, because distant
    # cities are a standalone dot on almost all levels.
    dots: list[dict[str, Any]] = []
    alive: dict[tuple[int, ...], dict[str, Any]] = {}
    for level_entry in levels:
        largest = max(float(s["seconds"] or 0) for s in level_entry["clusters"]) or 1.0
        next_alive: dict[tuple[int, ...], dict[str, Any]] = {}
        for cluster in level_entry["clusters"]:
            dot = alive.get(cluster["members"])
            if dot is None:
                dot = {"cluster": cluster, "since": level_entry["level"], "most": largest}
                dots.append(dot)
            dot["until"] = level_entry["level"]
            next_alive[cluster["members"]] = dot
        alive = next_alive

    for dot in dots:
        cluster = dot["cluster"]
        # The size without the script: within the level on which the dot appears.
        # The script recomputes it by the level that is visible right now
        # (data-s, data-maxima) - otherwise a dot carried over from a lower level
        # would have the lower one's scale and a bigger time could look smaller.
        share = (float(cluster["seconds"] or 0) / dot["most"]) ** 0.5
        radius = (1.2 + share * 4.0) * 2.2
        tip = _t("{place}: {n}× · {people}").format(
            place=cluster["description"], n=cluster["plays"],
            people=formatting.plural("{n} people", cluster["people"]))
        # Without the script only the dots of the level 1× are visible - map.js
        # uncovers the rest by the zoom.
        hidden = ' display="none"' if dot["since"] != 1 else ""
        parts.append(
            f'<circle class="map-point" data-from="{dot["since"]}" data-to="{dot["until"]}" '
            f'data-s="{int(cluster["seconds"] or 0)}" '
            f'cx="{cluster["x"]:.4f}" cy="{cluster["y"]:.4f}" r="{radius:.2f}" '
            f'fill="url(#map-glow)" data-tip="{_e(tip)}"{hidden} />'
        )

    parts.append("</svg>")
    return "".join(parts)

