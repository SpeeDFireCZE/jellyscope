"""The cards a custom dashboard is put together from - the card registry.

Each page of the app computes its data in its route and the template draws
them in a fixed order. A custom dashboard needs something else: only what can
stand on its own can be put together. A card is therefore a **pair** - a piece
of a template and a function that gets it its data - filed under its key.

What it all stands on:

* **The templates are the same ones the original pages use.** Were there a
  copy, somebody would one day change one and forget the other - and two
  places in the app would each claim something different.
* **Only what is put together and allowed is computed.** What an account does
  not see or did not choose is not even run.
* **The Overview (`/`) goes the same way.** Its layout is fixed in the template
  (`video/overview.html`); its cards with their permissions and data come from
  here (`OVERVIEW`, the macro `show_card()`) - it used to include them by hand and
  guard them by hand.

Adding a card means one more item in `ALL_CARDS` - for the Overview also its
key in `OVERVIEW` and `{{ show_card("…") }}` in `video/overview.html`.

The apps' cards (cards_sonarr.py, cards_radarr.py, cards_lidarr.py, cards_readarr.py, cards_bazarr.py, cards_prowlarr.py, cards_seerr.py) have
modules of their own - each app its own - and are only
added here; so are the cards of the Music and Books pages (cards_music.py, cards_books.py); what they share with the cards here (the class `Card`, the memo
of one computation, the account) is in cards_base.py.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from typing import Any
from collections.abc import Iterable, Mapping

from . import (access, cards_bazarr, cards_books, cards_lidarr, cards_music, cards_prowlarr, cards_radarr, cards_readarr, cards_seerr, cards_sonarr,
               content_kinds, db, detailed_map, formatting, geoip, i18n, insights, langstats, scanner, schema_names, stats)
from .i18n import translate as _t
# The registry's base (the card class, the memo of one computation, the choices, the account) is
# in cards_base.py, so that the modules with the apps' cards can use it too.
from .cards_base import Card, _account, _choices, _memo, _once, choice


def _colours() -> dict[str, Any]:
    """The languages' colours - the same for every language card."""
    return _once(langstats.colour_map)


def _watched_languages(period: Any) -> Any:
    return langstats.watched_languages(period, _colours())


def geoip_state() -> dict[str, Any]:
    """What the map needs and whether it has it - for the Network page and the dashboard card.

    The maxminddb library (optional), the GeoLite2 database (downloaded on a
    click), its size and age. And the path to the python the app runs with:
    the guide to installing it then shows a command that can be copied, not
    a general "pip install" that would miss in a virtual environment.
    """
    return {
        "library": geoip.library_installed(),
        "database": geoip.is_available(),
        "byte_count": geoip.database_size(),
        "age": geoip.database_age(),
        "python": sys.executable,
    }


def growth_data(period: Any, disk: str | None = None) -> dict[str, Any]:
    """The data for the library growth curve - the whole library, or one disk.

    `disk` is the disk chosen above the chart (a fingerprint of the key, see
    stats.disks_to_choose), or "all". Without it the card's choice
    `growth_disk` applies (the address and the session, see web.periods._growth_disk).
    The choice is offered only with at least two disks; a disk that is not
    among them (deleted, unknown) means the whole library.

    The chart draws numbers, not bytes, so every snapshot gets its size in
    gigabytes. That is done here, not in SQL: the database is to keep what
    was measured, not a conversion for the sake of one chart.

    The curve falls apart into two series, so that what was measured can be
    told from what was worked out from the items' creation dates (see
    stats.snapshots). Where a series does not hold, it is `None`, not zero -
    zero would draw a drop to the axis that never happened.

    The last worked-out day carries its value in the measured series too, so
    the two lines join up and there is no hole between them.
    """
    options = stats.disks_to_choose()
    if len(options) < 2:
        options = []
    disk = choice("growth_disk", "all") if disk is None else disk
    chosen = next((d for d in options if d["choice"] == disk), None)
    if chosen:
        rows = stats.disk_growth_series(period, chosen["key"])
        # The disk's series carries the data outside Jellyfin itself (measured days only).
        outside = {r["day"]: r["outside"] for r in rows if r.get("outside") is not None}
        growth = stats.disk_growth(period, chosen["key"], rows)
    else:
        rows = stats.snapshots(period)
        # The data outside Jellyfin (downloads, backups...) from the disks' snapshots - only
        # where Jellyfin measures them. Days without a disk snapshot stay empty.
        outside = {r["day"]: r["outside"] for r in stats.outside_series(period)}
        growth = stats.library_growth(period)

    # The unit is chosen by the size of the library, not fixed.
    #
    # The chart always drew gigabytes, while the tile beside it says
    # "12,8 TB". For a twelve-terabyte library the axis said 13 101 and the
    # tile 12,8 - one and the same number, only in another unit, which reads
    # as "the chart shows more". Now both speak the same.
    largest = max((int(r.get("size_bytes") or 0) for r in rows), default=0)
    if largest >= 1024 ** 4:
        divisor, unit, places = 1024 ** 4, "TB", 2
    else:
        divisor, unit, places = 1024 ** 3, "GB", 2

    first_measured = next((i for i, r in enumerate(rows)
                           if not r.get("backfilled")), None)
    for index, row in enumerate(rows):
        value = round(int(row.get("size_bytes") or 0) / divisor, places)
        row["gb"] = value
        worked_out = bool(row.get("backfilled"))
        row["gb_backfilled"] = value if worked_out else None
        row["gb_measured"] = None if worked_out else value
        row["gb_outside"] = (round(int(outside[row["day"]]) / divisor, places)
                          if row.get("day") in outside else None)
        # How much of the day is other content (music) - a line in the bubble, not a curve.
        row["tip_lines"] = [_t(content_kinds.BY_KEY[key].share).format(size=formatting.bytes_human(size))
                            for key, size in (row.get("kinds") or {}).items()
                            if size and key in content_kinds.BY_KEY]
        # The join: the last worked-out day belongs to the measured line too, otherwise
        # there would be a gap a whole day wide between them.
        if first_measured is not None and index == first_measured - 1:
            row["gb_measured"] = value
    return {"snapshots": rows, "growth": growth,
            "growth_unit": unit,
            "growth_outside": any(r.get("gb_outside") is not None for r in rows),
            "growth_disks": options,
            "growth_disk": chosen["choice"] if chosen else "all",
            "growth_disk_name": chosen["name"] if chosen else ""}


def period_summary(period: Any) -> dict[str, Any]:
    """The numbers of the Overview's main card: the summary, the changes and why it cannot be compared.

    One place, because the Overview and the custom dashboard's card need the
    same. Were it computed twice, one page would one day show other
    percentages than the other.
    """
    current = stats.overview(period)
    previous = stats.previous_overview(period)
    # It can be compared only with a period we have data for. When it reaches
    # further back than our history, it is no comparison of two periods but of
    # a period with emptiness - and the percentages come out as large as you
    # like. (A real case: the filter "this year" showed 1 619 572 %.)
    comparable = stats.can_compare(period)

    def change(now: Any, before: Any) -> float | None:
        if not comparable:
            return None
        try:
            now_value, before_value = float(now or 0), float(before or 0)
        except (TypeError, ValueError):
            return None
        if before_value <= 0:
            return None
        return (now_value - before_value) / before_value * 100

    # When it cannot be compared, say why - instead of a silently missing arrow.
    note = ""
    if not comparable:
        since = stats.first_record()
        note = (_t("no data for the previous period; the history starts {date}")
                .format(date=formatting.czech_date(since[:10])) if since
                else _t("nothing to compare with yet"))

    return {
        "overview": current,
        "deltas": {
            "watched": change(current.get("watched_seconds"),
                              previous.get("watched_seconds")),
            "plays": change(current.get("plays"), previous.get("plays")),
        },
        "comparison_note": note,
    }


def _map_data(period: Any) -> dict[str, Any]:
    """The data of the Map card. The places are computed once - for the map and the table of
    countries; with a long history it is the Network page's most expensive query."""
    places = stats.places(period)
    return {"hotspots": stats.hotspots(period, precomputed=places),
            "countries": stats.viewer_countries(period, precomputed=places),
            "geo": geoip_state(), "detailed": detailed_map.state(),
            "detailed_error": detailed_map.last_error()}


ALL_CARDS: tuple[Card, ...] = (
    # The page fetches the card again in the rhythm the collector asks Jellyfin in -
    # faster would make no sense, newer data are nowhere yet.
    Card("now_playing", "Now playing",
         "Who is watching what right now. The same card as on the Overview.",
         "_now_playing.html",
         lambda o: {"active": stats.active_sessions(),
                    "poll_interval": db.get_int_setting("poll_interval", 5, 300, 10)},
         uses_period=False,
         area="now_playing", wrapper_id="now-playing", refresh_partial="/partials/now-playing"),
    # By the library's version the page knows that a synchronisation finished
    # meanwhile and new titles came in - see /partials/recently-added.
    Card("recently_added", "Recently added",
         "A strip of posters of what arrived in the library most recently.",
         "_recently_added.html",
         lambda o: {"recent": stats.recently_added(), "library_version": scanner.library_version()},
         uses_period=False,
         area="library", wrapper_id="recently-added", after_sync=True),
    Card("summary", "Total watched",
         "The headline figure for the period, the change against the previous one, and the curve beside it.",
         "_card_summary.html",
         lambda o: dict(_once(period_summary, o),
                        daily=_once(stats.daily_activity_split, o)),
         area="overview"),
    Card("tiles", "Plays, users, titles, transcode",
         "A row of tiles with the period's headline numbers.",
         "_card_tiles.html", lambda o: _once(period_summary, o),
         area="overview"),
    Card("watching_by_day", "Watch time by day",
         "Films and TV shows day by day, with a table underneath.",
         "video/_daily_card.html",
          # `others`: what "Other" is made of - the label under the chart. Without
          # it the series is a blind spot: you see the hours but not what it was.
         lambda o: {"daily": _once(stats.daily_activity_split, o),
                    "others": stats.others_breakdown(o),
                    "kind": choice("kind", stats.KIND_BOTH)},
         area="overview", wrapper_id="daily-card"),
    Card("most_watched", "Most watched titles",
         "The ranking of films and shows over the chosen period.",
         "video/_top_items.html",
         lambda o: {"top_items": stats.top_items(o, kind=choice("top_kind", stats.KIND_BOTH)),
                    "top_kind": choice("top_kind", stats.KIND_BOTH)}, width="half", wrap_in_card=True,
         area="overview", wrapper_id="top-items-card"),
    Card("most_active_users", "Most active users",
         "Who watched the most hours over the chosen period.",
         "_card_top_users.html", lambda o: {"top_users": stats.top_users(o)},
         width="half",
         area="overview"),
    Card("delivery", "How the server delivers content",
         "Direct play, remux, transcode, and what each costs the server.",
         "_card_delivery.html",
         lambda o: {"methods": stats.play_method_breakdown(o)}, width="half",
         area="overview"),
    Card("players", "Players",
         "What people watch on: app, browser, television.",
         "_card_players.html",
         lambda o: {"clients": _unknown_named(stats.client_breakdown(o))}, width="half",
         area="overview"),
    Card("when_watched", "When people watch",
         "A grid of days and hours: when the server is busiest.",
         "_card_heatmap.html",
         lambda o: {"heatmap": stats.hourly_heatmap(o)},
         area="overview"),
    Card("live_traffic", "Flowing right now",
         "Concurrent throughput over time, refreshing on its own.",
         "_network_live.html",
         lambda o: {"now": stats.flow_now(), "live": stats.bandwidth_live(o)},
         area="network"),
    Card("peaks_by_day", "Peak by day",
         "The highest concurrent throughput of each day.",
         "_card_peaks_by_day.html",
         lambda o: {"daily_peaks": stats.bandwidth_daily_peaks(o)},
         area="network"),
    Card("who_streamed", "Who streamed the most",
         "Data transferred per user.",
         "_card_who_streamed.html",
         lambda o: {"by_viewer": stats.bandwidth_by(o, "user_name")},
         width="half",
         area="network"),
    Card("by_player", "Transfer per player",
         "How much data went through which app.",
         "_card_by_player.html",
         lambda o: {"by_client": stats.bandwidth_by(o, "client")},
         width="half",
         area="network"),
    Card("origin", "Where they watch from",
         "The addresses streams come from: home network, or outside.",
         "_card_where_from.html",
         lambda o: {"where_from": stats.watching_locations(o)},
         area="network"),
    Card("language_share", "Language ratio",
         "Which language people watch in on this server.",
         "_card_language_share.html",
         lambda o: {"watched": _once(_watched_languages, o)}, width="half",
         area="languages"),
    Card("dubbing", "Dubbing or original?",
         "How much is watched dubbed and how much in the original.",
         "_card_dub.html",
         lambda o: {"dubbing": langstats.dubbed_vs_original(
              o, _once(langstats.preferred_language))}, width="half",
         area="languages"),
    Card("subtitles", "Subtitles",
         "How often subtitles are switched on, and in which language.",
         "_card_subtitles.html",
         lambda o: {"subtitles": langstats.subtitle_usage(o)}, width="half",
         area="languages"),
    Card("library_languages", "Languages in the library",
         "What the library offers, regardless of what gets watched.",
         "_card_library_languages.html",
         lambda o: {"library": langstats.library_languages(_colours())},
         uses_period=False, width="half",
         area="languages"),
    Card("library_total", "Across all libraries",
         "Items, size and where the technical data comes from. A state, not a period.",
         "_card_library_total.html",
          # The technical data as on the Library page - `langstats.coverage`
          # is the languages' coverage for the Languages page, only under the same name.
         lambda o: {"coverage": stats.tech_coverage()}, uses_period=False,
         area="library"),
    Card("disks", "Disks",
         "Which disk each library is on, how much space is left and in how many days it runs out.",
         "_card_disks.html", lambda o: {"disks": stats.disks_overview(o, stats.library_growth, stats.disk_growth)},
         area="library"),
    Card("library_growth", "Library growth",
         "The size of the library day by day, and where it is heading.",
         "_card_growth.html", lambda o: growth_data(o),
         area="library"),
    Card("codecs", "Codecs",
         "What the library is encoded with. It ignores the period because it is the state of the library.",
         "_card_codecs.html", lambda o: {"codecs": stats.codec_breakdown()},
         uses_period=False, width="half",
         area="library"),
    Card("resolutions", "Resolution",
         "How much of the library is 4K, how much Full HD and how much the rest.",
         "_card_resolution.html",
         lambda o: {"resolutions": stats.resolution_breakdown()},
         uses_period=False, width="half",
         area="library"),
    Card("dynamic_range", "Dynamic range",
         "SDR, HDR and Dolby Vision in the library.",
         "_card_dynamic_range.html", lambda o: {"ranges": stats.video_range_breakdown()},
         uses_period=False, width="half",
         area="library"),

    # Insights. The data from the same functions and with the same number of rows as the
    # Insights page (insights.INSIGHT_ROWS); how many show straight away is set in
    # Settings → Interface → Long lists, the rest is in a window.
    Card("insight_unused", "Storage nobody uses",
         "Files nobody played over the period, and how much space they take.",
         "_card_insights_unused.html",
         lambda o: {"dead": insights.dead_storage(insights.INSIGHT_ROWS, days=o)},
         has_table_window=True, area="charts"),
    Card("insight_transcoded", "Most frequently transcoded files",
         "What the server transcodes most often, with codec and resolution.",
         "_card_insights_transcoded.html",
         lambda o: {"transcodes": insights.transcode_offenders(o, insights.INSIGHT_ROWS)},
         width="half", has_table_window=True, area="charts"),
    Card("insight_reasons", "Why it transcodes",
         "Why the server transcodes: codec, bitrate, subtitles.",
         "_card_insights_transcode_reasons.html",
         lambda o: {"reasons": insights.transcode_reasons(o)},
         width="half", area="charts"),
    Card("insight_speed", "Conversion speed",
         "Does the server keep up with conversions? Average, slowest and fastest.",
         "_card_insights_transcode_speed.html",
         lambda o: {"speed": insights.transcode_speeds(o)},
         area="charts"),
    Card("insight_quality", "Watched a lot, but in poor quality",
         "Often watched titles in low resolution: candidates for a better version.",
         "_card_insights_quality.html",
         lambda o: {"upgrades": insights.upgrade_candidates(o, insights.INSIGHT_ROWS)},
         width="half", has_table_window=True, area="charts"),
    Card("insight_large", "Takes a lot of space, barely watched",
         "Large files that hardly anyone watches.",
         "_card_insights_oversized.html",
         lambda o: {"oversized": insights.oversized_rarely_watched(
              o, insights.INSIGHT_ROWS)},
         width="half", has_table_window=True, area="charts"),
    Card("insight_libraries", "Library utilisation",
         "How much space each library takes against how much of it is watched.",
         "_card_insights_libraries.html",
         lambda o: {"efficiency": insights.storage_efficiency(o)},
         area="charts"),
    Card("insight_duplicates", "Possible duplicates",
         "Titles that are probably in the library more than once. A state, not a period.",
         "_card_insights_duplicates.html",
         lambda o: {"duplicates": _variants_named(insights.duplicate_candidates(insights.INSIGHT_ROWS))},
         uses_period=False, width="half", has_table_window=True, area="charts"),
    Card("insight_postponed", "Started and abandoned",
         "What people started watching and never finished.",
         "_card_insights_abandoned.html",
         lambda o: {"abandoned": insights.never_finished(o, insights.INSIGHT_ROWS)},
         width="half", has_table_window=True, area="charts"),

    # Languages - a complement to the cards above (ratio, dubbing, subtitles, the library's languages).
    Card("who_language", "Who watches in which language",
         "Audio and subtitle language per viewer.",
         "_card_viewer_languages.html",
          # `watched` for the legend - the same the Language ratio card takes.
         lambda o: {"by_user": langstats.languages_by_user(o, _colours()),
                    "watched": _once(_watched_languages, o)},
         area="languages"),
    Card("combinations", "Track combinations",
         "Which combinations of audio and subtitles the library has. A state, not a period.",
         "_card_stream_combinations.html",
         lambda o: {"combinations": langstats.language_combinations(),
                    "all_combinations": langstats.all_combinations(),
                    "undefined_items": langstats.undefined_language_items()},
         uses_period=False, width="half", area="languages"),
    Card("missing_track", "You watch it, but the track is missing",
         "Watched titles that lack an audio track in the main language.",
         "_card_missing_stream.html",
         lambda o: {"missing_preferred": langstats.missing_preferred(
              o, _once(langstats.preferred_language))},
         area="languages"),

    # Users. Both cards take the same table as the Users page.
    Card("users_chart", "Watch time by user",
         "How many hours each person watched over the period, as a chart.",
         "_card_users_chart.html",
         lambda o: {"rows": _once(stats.user_table, o)}, area="users"),
    Card("users_table", "Users",
         "Every user: hours, plays, titles, transcodes, last seen.",
         "_card_users_table.html",
         lambda o: {"rows": _once(stats.user_table, o)}, area="users"),

    # Library and Network.
    Card("libraries", "Libraries",
         "Library tiles: number of items and size, linking to the details.",
         "_card_libraries.html", lambda o: {"libraries": stats.library_cards()},
         uses_period=False, area="library"),
    Card("network_numbers", "Network figures",
         "Peak, total transferred, transcoded share and average stream rate.",
         "_card_network_numbers.html",
         lambda o: {"network_summary": stats.bandwidth_summary(o)}, area="network"),
    Card("map", "Map",
         "Where people watch from public addresses: on a map and by country.",
         "_card_map.html",
         lambda o: _map_data(o),
         area="network"),
    # The apps' cards live in modules of their own, each app in its own.
    # The registry only puts them after its own (in the order of the *Arr page's
    # tabs, Seerr at the end) - for the pages and dashboards they are cards like any other.
    *cards_sonarr.CARDS,
    *cards_radarr.CARDS,
    *cards_lidarr.CARDS,
    *cards_readarr.CARDS,
    *cards_bazarr.CARDS,
    *cards_prowlarr.CARDS,
    *cards_seerr.CARDS,
    # Music only after the apps: it is not an *Arr app but a page of its own (cards_music.py).
    *cards_music.CARDS,
    # Books after Music, like the menu: a page of their own too (cards_books.py).
    *cards_books.CARDS,
)

BY_KEY: dict[str, Card] = {card.key: card for card in ALL_CARDS}

# The Overview's (/) cards, in the order on the page. The layout (pairs, the heading
# "Statistics" with the filter) is in video/overview.html - every place there is
# {{ show_card("…") }}; the route computes the data by this list and the registry decides
# who sees which card (may_see). That the template draws exactly these cards in this
# order is guarded by tests/test_overview_from_registry.py.
OVERVIEW: tuple[str, ...] = (
    "now_playing", "recently_added", "summary", "tiles",
    "watching_by_day", "most_active_users", "most_watched",
    "delivery", "players", "when_watched",
)
# Not `assert`: Python with -O leaves it out and the error would show only on the page.
if not set(OVERVIEW) <= set(BY_KEY):
    raise RuntimeError(f"OVERVIEW may hold only cards of the registry: {sorted(set(OVERVIEW) - set(BY_KEY))}")


def check_card(key: str, keys: Iterable[str] | None = None) -> str:
    """For the macro show_card(): a foreign key is an error, not a silently missing card.

    Only the keys the page's route computes data for are valid: `keys` the page
    passed to the template (`page_card_keys` - the *Arr and Seerr pages), otherwise
    `OVERVIEW`. A card that is in the registry but the page does not compute it (a
    typo, a forgotten line) would be drawn for nobody, and nobody would notice.
    """
    valid = OVERVIEW if keys is None else tuple(keys)
    if key not in valid:
        raise KeyError(f"card {key!r} is not among the cards the page computes ({len(valid)} cards)")
    return ""


# The widths of a panel. The grid has six columns, so that both thirds and halves come out;
# the mark is what stands on the width switch in the layout window.
WIDTHS: dict[str, str] = {"third": "⅓", "half": "½", "full": "1"}

ENABLED_KEY = "ui_dashboard"


def _account_condition(account_id: int | None) -> tuple[str, list[Any]]:
    """The condition on the layout's owner - shared (NULL), or a given account.

    `account_id IS ?` cannot be written. SQLite takes it as a comparison that
    handles NULL too, but PostgreSQL knows only `IS NULL` - and `IS %s` is a
    syntax error for it. Since every request reads the layout (for the tab in
    the menu), it brought the whole app down on PostgreSQL, not just this
    page.
    """
    if account_id is None:
        return "account_id IS NULL", []
    return "account_id = ?", [account_id]


def may_see(card: Card, account: dict[str, Any] | None) -> bool:
    """May the account see this card? By its area, like the page it comes from.

    The administrator sees everything. The others by the permission tree - their
    own, or their group's. Without it a dashboard would be a back door: whoever may
    open the dashboard would see in it a card from a page they may not open
    (say the IP addresses from Network).
    """
    return access.account_sees(account, card.area)


def is_available(card: Card) -> bool:
    """Is what the card reads from available? The apps' cards only with the app connected.

    A card without a condition (`available=None`) is always available. It is asked
    on every page load - an app can be connected and disconnected while running.
    """
    return card.available is None or bool(card.available())


def allowed(layout: list[Card], account: dict[str, Any] | None) -> list[Card]:
    """Only the cards the account may see and that are available - in the same order.

    These are drawn, offered and computed. A disconnected app's card is not
    among them, but it does not vanish from the saved layout (see web.dashboards).
    """
    return [card for card in layout if may_see(card, account) and is_available(card)]


def groups(offered: list[Card]) -> list[tuple[tuple[str, ...], list[Card]]]:
    """The cards by groups for the offer in the layout window: [(heading, cards)].

    The heading is the card's group (an app: ("Sonarr",)), otherwise its area in
    the permission tree - for a sub-area the one above it ("Právě se hraje" belongs
    under the Overview) - with its menu section, the same rule as everywhere outside the tree
    (access.Area.heading): ("Filmy a seriály", "Přehled"). The groups go in the order they
    first appear in the registry, the cards inside in the registry's order. The parts of a
    heading are Czech sentences (translation keys), or the app's own name; the template
    translates them.
    """
    groups: dict[tuple[str, ...], list[Card]] = {}
    for card in offered:
        area = access.BY_KEY[card.area]
        heading = (card.group,) if card.group else access.BY_KEY[area.parent or area.key].heading
        groups.setdefault(heading, []).append(card)
    return list(groups.items())


def is_enabled() -> bool:
    """Is the custom dashboard switched on in the settings?"""
    return (db.get_setting(ENABLED_KEY, "0") or "0").strip() == "1"


def sees_dashboard(account: dict[str, Any] | None) -> bool:
    """Has the account something to see in the dashboard for everyone?

    Switched on, the account may open it (the dashboard's area) and by its
    permissions sees at least one card in it. One place for the menu, the landing
    after sign-in and the page itself: three copies of the same condition drifted
    apart twice. An empty page "nothing in it yet" would lie, "you have no access
    here" right after sign-in is the worst welcome possible.
    """
    return (is_enabled()
            and access.may_open(account, "/dashboard")
            and bool(allowed(load_layout(), account)))


def shows_dashboard_tab(account: dict[str, Any] | None) -> bool:
    """Is the custom dashboard's tab in this account's menu (navigation.menu)?

    For an account that sees a card in it (sees_dashboard), and for the administrator while it
    is on, even empty - an empty dashboard tells the others nothing, but the administrator
    who switched it on needs a way in to build it.
    """
    return bool(account) and (sees_dashboard(account) or (bool(account.get("is_admin")) and is_enabled()))


# The name of the dashboard for everyone. The administrator enters it in Settings → Interface
# and it shows in the menu and as the page's heading - "Rodina", "Kino", depending on what
# it was put together for. It is not translated: it is the administrator's text, not the app's sentence.
NAME_KEY = "dashboard_name"
NAME_MAX = 40


class InvalidDashboardName(ValueError):
    """A name that cannot be used. `template` is an English sentence = the translation key,
    `values` go into it (see web.base._flash)."""

    def __init__(self, template: str, **values: Any) -> None:
        super().__init__(template)
        self.template = template
        self.values = values


def dashboard_name() -> str:
    """The saved name of the dashboard for everyone, or empty."""
    return (db.get_setting(NAME_KEY, "") or "").strip()


def check_dashboard_name(text: str) -> str:
    """The cleaned name, or InvalidDashboardName.

    Spaces at the edges gone, several spaces in a row as one. The length in
    characters (not bytes), so that it fits in the menu. And it must not be
    mistaken for pages already in the menu - "Přehled" and "Můj přehled" in no
    interface language: whoever switches the language would see two same items.
    """
    clean = " ".join((text or "").split())
    if not clean:
        raise InvalidDashboardName(
            "The custom overview needs a page name. It shows in the menu and as the heading.")
    if len(clean) > NAME_MAX:
        raise InvalidDashboardName("The page name can have at most {max} characters.", max=NAME_MAX)
    taken = {i18n.translate(sentence, language).casefold()
             for sentence in ("Overview", "My overview") for language in i18n.LANGUAGES}
    if clean.casefold() in taken:
        raise InvalidDashboardName('Another page in the menu is already called "{name}". Pick a different name.',
                                   name=clean)
    return clean


def fill_in_dashboard_name() -> bool:
    """Existing installations: a dashboard switched on without a name gets "Vlastní přehled".

    That is what it was called until now, so nothing changes in the menu. Called
    at start; the second time it does nothing and it never overwrites a name the
    administrator gave. A dashboard switched off stays without a name - it gets
    one when somebody switches it on.
    """
    if not is_enabled() or dashboard_name():
        return False
    db.set_setting(NAME_KEY, i18n.translate("Custom overview", i18n.server_language()))
    return True


def load_layout(account_id: int | None = None) -> list[Card]:
    """The dashboard as put together. Unknown keys are skipped silently.

    Without `account_id` it is the dashboard for everyone (`account_id IS NULL`),
    with it that account's My dashboard - and only that: an empty My dashboard is
    empty, it does not borrow the layout of the dashboard for everyone. It is
    another page with another purpose, and whoever has not put theirs together
    yet is to see the invitation, not somebody else's choice they might then save
    as their own by mistake.
    """
    where, params = _account_condition(account_id)
    rows = db.query_all(
        f"SELECT card_key, width FROM dashboard_layout"
        f" WHERE {where} ORDER BY position", tuple(params))

    layout: list[Card] = []
    for row in rows:
        card = BY_KEY.get(row["card_key"])
        if card is None:
            continue
        # The saved width beats the registry's; an unknown one is ignored,
        # so that the page does not break over a value from another version.
        width = (row.get("width") or "").strip()
        layout.append(replace(card, width=width)
                      if width in WIDTHS else card)
    return layout


UNKNOWN = "unknown"      # the statistics' group of a play or a file the source says nothing about (SQL COALESCE)


def _unknown_named(rows: list[dict[str, Any]], key: str = "label") -> list[dict[str, Any]]:
    """The group "unknown" in the reader's language - a card shows it, the statistics and the API keep the value."""
    return [{**row, key: _t(UNKNOWN)} if row.get(key) == UNKNOWN else row for row in rows]


def _variants_named(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A duplicate's variants ("hevc / 1080p | h264 / unknown") with the unknown resolution in the reader's language."""
    def named(text: Any) -> Any:
        if not isinstance(text, str):
            return text
        return " | ".join(" / ".join(_t(UNKNOWN) if part == UNKNOWN else part for part in variant.split(" / "))
                          for variant in text.split(" | "))
    return [{**row, "variants": named(row.get("variants"))} for row in rows]


def parse_layout(text: str) -> list[tuple[str, str]]:
    """Takes apart what the form sent: "key:width,key:width".

    The width is optional - without it the registry's applies. An editor still open from before the database
    step 5 sends the old (Czech) keys and widths: they are read by the database's map.
    """
    old_keys = schema_names.VALUES.get(("dashboard_layout", "card_key"), {})
    old_widths = schema_names.VALUES.get(("dashboard_layout", "width"), {})
    items: list[tuple[str, str]] = []
    for piece in (text or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        key, _, width = piece.partition(":")
        key, width = key.strip(), width.strip()
        items.append((old_keys.get(key, key), old_widths.get(width, width)))
    return items


def save_layout(items: Any, account_id: int | None = None) -> list[str]:
    """Saves the order of the cards and their widths. Returns what was really saved.

    Takes a list of keys, of pairs (key, width), or straight what the form sent.
    Unknown keys, unknown widths and duplicates are thrown away - only what can be
    drawn belongs in the database. Otherwise it would be enough to take a card out
    of the registry and the page would fall over something somebody put there
    half a year ago.
    """
    if isinstance(items, str):
        items = parse_layout(items)

    saved: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        key, width = item if isinstance(item, tuple) else (item, "")
        if key in BY_KEY and key not in seen:
            seen.add(key)
            saved.append((key, width if width in WIDTHS else ""))

    where, params = _account_condition(account_id)
    with db.connect() as conn:
        conn.execute(f"DELETE FROM dashboard_layout WHERE {where}", tuple(params))
        conn.executemany(
            "INSERT INTO dashboard_layout (account_id, card_key, position, width)"
            " VALUES (?, ?, ?, ?)",
            [(account_id, key, position, width or None)
             for position, (key, width) in enumerate(saved)])
        conn.commit()
    return [key for key, _ in saved]


def data_for(layout: list[Card], period: Any,
             choices: Mapping[str, str] | None = None,
             account: dict[str, Any] | None = None) -> dict[str, Any]:
    """The data of all the cards put together, in one dict.

    The keys do not repeat across the cards, so they can be poured into the
    template's context directly - and the templates then work the same here as on
    their original pages. What several cards need is computed once (`_once`).
    `choices` are the cards' choices from the session (see choice()).

    `account` is the account it computes for: the apps' cards hide by it what is
    only for the administrator or only with a sub-area (cards_base.current_account).
    Without it they get the least permissions.
    """
    token = _memo.set({})
    choices_token = _choices.set(dict(choices or {}))
    account_token = _account.set(account)
    try:
        context: dict[str, Any] = {}
        # The Disks card and Library growth in one dashboard share the disks' curves (stats.disk_growth_memo).
        with stats.disk_growth_memo():
            for card in layout:
                context.update(card.data(period))
        return context
    finally:
        _account.reset(account_token)
        _choices.reset(choices_token)
        _memo.reset(token)


def area_data(area: str, period: Any) -> dict[str, Any]:
    """The data of all the cards of one area - for the page the cards come from.

    The page (Insights, Network) does not take them separately: two copies of the
    same computation drifted apart and the card in a dashboard then showed other
    numbers than on the page.
    """
    return data_for([card for card in ALL_CARDS if card.area == area], period)
