"""Old page addresses and where they live now - a redirect for each.

Since the menu was split into sections (16. 11. 2026) the pages of a section sit under the
section's address (/video/..., /music/...; an album page belongs to the Library, /album/<id>), and
part 10d1 of the English code (20. 11. 2026) gave the addresses with Czech parts English names.
A page is what a person opens by itself - a bookmark, a link in a note, an address typed in - so
each old page address answers with a permanent redirect (308) to the new one, query string
included. A path parameter keeps its value (`{name}` stands for one segment, the same name on both
sides). Addresses of the application itself (form targets, fragments a script fetches, images,
downloads) get no redirect: the templates and scripts use the new ones, and a page left open across
an update is reloaded by it.

The Czech old addresses stay only for a while (the user, 20. 11. 2026: the final app is English
except the GUI translations); their removal is in TODO.md. This table is the one place in the app
where they are written (tests/test_addresses.py allows them only here).

This module imports nothing from the application: the permission guard (access.py) asks
`is_legacy` to let an old address through to its redirect - the new address is then checked as
usual, so an old address never opens a page its account may not see.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode

LEGACY_URLS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # the menu by sections (16. 11. 2026)
    ("/insights", "/video/insights", ("GET",)),
    ("/top-items", "/video/insights/top", ("GET",)),
    ("/languages", "/video/languages", ("GET",)),
    # temporary: Czech old page addresses (the menu by sections and part 10d1) - their removal is in TODO.md
    ("/srovnani", "/video/compare", ("GET",)),
    ("/languages/bez-jazyka", "/video/languages/undetermined", ("GET",)),
    ("/hudba", "/music/overview", ("GET",)),
    ("/hudba/interpret/{artist_id}", "/music/artist/{artist_id}", ("GET",)),
    ("/hudba/album/{album_id}", "/album/{album_id}", ("GET",)),
    ("/account/prehled", "/account/dashboard", ("GET",)),
)


def _pattern(old: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(old)) + "$")


_PATTERNS = tuple((_pattern(old), new) for old, new, _methods in LEGACY_URLS)

# The address of a section without a page: it opens the section's first page the account may
# open (web/legacy.section_root, navigation.landing) - like a click on the section in the menu.
SECTION_ROOTS: dict[str, str] = {"/video": "video", "/music": "music", "/books": "books"}


# Temporary, like the Czech old page addresses (removal in TODO.md): the Czech names of address
# parameters before part 10d2 and the code values that changed with them. A GET of a page that carries
# one is redirected (legacy_query_middleware in web/legacy.py); fragments, forms and the API (which never
# read these names) are not.
LEGACY_PARAMS: dict[str, str] = {
    "od": "since", "do": "until", "a_od": "a_since", "a_do": "a_until", "b_od": "b_since", "b_do": "b_until",
    "od_ts": "since_ts", "do_ts": "until_ts", "obdobi": "period", "rust_disk": "growth_disk",
    "log_druh": "log_kind", "log_klic": "log_key", "log_ucet": "log_account", "priradit": "assign",
    "soubor": "file", "verze": "version",
}
LEGACY_VALUES: dict[str, dict[str, str]] = {
    "period": {"vse": "all", "dnes": "today", "vlastni": "custom"},
    "growth_disk": {"vse": "all"},
}

# Part 10d3: form fields by their old Czech names. They are not read any more (decision 5), so a form
# that still sends one comes from a page rendered before the upgrade - left as it is, the route would
# read empty values and save them. web/legacy.py turns such a form back unsaved instead.
LEGACY_FIELDS: frozenset[str] = frozenset({
    "anonymizace", "cesta", "cil", "datum", "event_misto", "event_sberac", "event_souhrn", "historie",
    "jednotka", "kanal", "kapacita", "misto_dnu", "nazev", "odpoved", "pohyb", "poradi", "potvrzeno", "rezim",
    "smazat", "smi_klic", "smtp_heslo", "smtp_komu", "smtp_odesilatel", "smtp_uzivatel", "souhrn_den",
    "souhrn_hodina", "souhrn_minuta", "ticho_minut", "udalost", "ui_radky_arr", "ui_radky_hudba",
    "ui_radky_seerr", "zapnuto", "zdroj", "zpusob",
    # the database step 1 (21. 11. 2026): the music rows of Settings → Interface and the music task's
    # fields of Settings → Tasks (built from the task's key, "hudba" before)
    "ui_max_rows_hudba", "ui_max_rows_hudba_mobile", "enabled_hudba", "minutes_hudba",
    # the database step 5 (22. 11. 2026): the groups' own API keys (Settings → Accounts)
    "ctenar_smi_klic", "divak_smi_klic",
    # the base layer of the map (Settings → Interface)
    "ui_map_podklad",
    # the task of notifications (Settings → Tasks, built from the task's key, "notifikace" before)
    "enabled_notifikace", "minutes_notifikace",
    # the log rotation (Settings → Tasks) and the look of the pages (Settings → Interface)
    "logrotate_dni", "logrotate_pocet", "ui_cas_presne", "dashboard_nazev", "ui_udalosti",
})
LEGACY_FIELD_PATTERNS = (re.compile(r"vidi_[\w.-]+"), re.compile(r"cil_[\w.-]+"), re.compile(r"text_\w+_predmet"),
                         # step 5: a tick of a permission area by its old key (Settings → Accounts, API)
                         re.compile(r"sees_(prehled|srovnani|prave_hraje|zebricky|jazyky|hudba|hudba_kdo|seerr_kdo|knihovna|knihovna_kdo|sit|uzivatele)"),
                         # an own text of a message by its old key (Settings → Notifications)
                         re.compile(r"text_(sberac_porucha|sberac_obnova|misto_porucha|misto_obnova|souhrn)_(subject|text)"))


# The database step 6: the session cookie's Czech keys. A browser signed in before the upgrade still sends them;
# web.legacy.LegacySession moves them to the new keys at its first request, so nobody is signed out and a
# chosen period, a compared pair or a key just made does not vanish. Temporary, like the rest (TODO.md item 0).
LEGACY_SESSION_KEYS: dict[str, str] = {"otisk": "fingerprint", "novy_klic_uctu": "new_account_key",
                                       "novy_api_token": "new_api_token", "rust_disk": "growth_disk",
                                       "srovnani_a": "compare_a", "srovnani_b": "compare_b"}
# The bounds of a remembered custom period ({"od", "do"} before the step), under these session keys.
LEGACY_PERIOD_KEYS: dict[str, str] = {"od": "since", "do": "until"}
PERIOD_SESSION_KEYS = ("days", "compare_a", "compare_b")


def upgrade_session(session: dict[str, Any]) -> bool:
    """Moves the old keys of a session to the new ones (a new key already there wins) and renames the bounds of
    the remembered periods. True when something changed."""
    changed = False
    for old, new in LEGACY_SESSION_KEYS.items():
        if old in session:
            value = session.pop(old)
            session.setdefault(new, value)
            changed = True
    for key in PERIOD_SESSION_KEYS:
        period = session.get(key)
        if isinstance(period, dict) and set(period) & set(LEGACY_PERIOD_KEYS):
            session[key] = {LEGACY_PERIOD_KEYS.get(bound, bound): value for bound, value in period.items()}
            changed = True
    return changed


def stale_fields(names: Any) -> list[str]:
    """The old field names among a form's names (empty for a form of the current pages)."""
    return [n for n in names if n in LEGACY_FIELDS or any(p.fullmatch(n) for p in LEGACY_FIELD_PATTERNS)]


def legacy_query(query: str) -> str | None:
    """The query with the old parameter names (and their old values) renamed, or None when it has none.

    Other parameters, their order and repeated names stay as they were."""
    pairs = parse_qsl(query, keep_blank_values=True)
    if not any(name in LEGACY_PARAMS for name, _value in pairs):
        return None
    renamed = []
    for name, value in pairs:
        if name in LEGACY_PARAMS:
            name = LEGACY_PARAMS[name]
            value = LEGACY_VALUES.get(name, {}).get(value, value)
        renamed.append((name, value))
    return urlencode(renamed)


def legacy_target(path: str, query: str) -> str | None:
    """The new address of an old one (with `?query` when there is one), or None for any other address."""
    for pattern, new in _PATTERNS:
        found = pattern.match(path)
        if found:
            target = re.sub(r"\{(\w+)\}", lambda m: found.group(m.group(1)), new)
            return f"{target}?{query}" if query else target
    return None


def is_legacy(path: str) -> bool:
    """Is this an address that only redirects (an old address, or a section's root)?"""
    return path in SECTION_ROOTS or any(pattern.match(path) for pattern, _ in _PATTERNS)
