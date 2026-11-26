"""What a viewer signed in with their Jellyfin account sees.

Three things, and each can be switched off:

* **signing in through Jellyfin** - until the admin switches it on, only
  local accounts get in. Opening the statistics of the whole household is a
  decision, not a side effect of an update, so nothing switches on by itself.

* **what somebody who is not an admin sees** - a list of areas (charts, the
  library, the overview…) of which the admin ticks what is fine in their
  installation. One server is a family's and nobody hides anything there,
  another is for strangers.

  It is set **separately for two groups**, because those are two different
  cases. A *viewer from Jellyfin* came by themselves and their account belongs
  to a particular person, so we allow them something extra on top of their own
  numbers. A *local reader account* is created by the admin by hand, and
  usually exactly so that somebody sees the whole statistics - so by default
  it sees everything as before, and whoever wants takes some away.

  And **one account can override the group**: in Settings → Accounts it can
  get its own areas and its own anonymising ticked. Until the admin does that,
  the group applies; once they do, only what is with the account applies -
  the group stops being read for it altogether.

* **anonymising** - when the admin lets viewers onto a page that lists people,
  they need not see them by name there. Instead of the name it says "Viewer 3",
  instead of an address a dash. A viewer still sees their own name - they
  know about themselves after all.

It is decided here, not in the templates: what is not to be shown is not to
be loaded either, and the list of paths is **allowing**. What is not in it is
forbidden - a route forgotten while writing gives nothing away to anybody.
"""
from __future__ import annotations

import functools
import json
import re
import time
from typing import Any
from collections.abc import Iterable

from . import accounts, db, legacy_urls

# Signing in with the Jellyfin password switched on.
LOGIN_KEY = "jellyfin_login_enabled"

# The two groups that are not admins. The same setting decides about them,
# just with another prefix of the key and other default values.
VIEWER = "viewer"          # signed in with the Jellyfin password
READER = "reader"         # a local account without admin rights
ADMIN = "admin"


def role(account: dict[str, Any] | None) -> str:
    """Which group the account belongs to. Empty = not signed in."""
    if not account:
        return ""
    if account.get("is_admin"):
        return ADMIN
    return VIEWER if account.get("jellyfin_user_id") else READER


def own_viewer(account: dict[str, Any] | None) -> str:
    """The id of the viewer the account is limited to. Empty = it sees everything.

    Only whoever signed in **with their Jellyfin account** is limited: there we
    know whose playbacks are whose, and nothing else is their business. An admin
    sees everything, wherever they come from. A local reader account is created
    by the admin by hand and has always seen the whole statistics - changing
    that under their hands would mean that after an update they suddenly do
    not see what they saw yesterday.

    One place for the pages and the API keys: it is a security rule, and two
    copies would drift apart sooner or later.
    """
    if not account or account.get("is_admin"):
        return ""
    return str(account.get("jellyfin_user_id") or "")


def anonymise_for(data: Any, account: dict[str, Any] | None) -> Any:
    """Hides other people's names and addresses when the admin has them hidden for the account.

    For the pages (the whole context of a template) and for an API key (the
    data of its owner). A reader account belongs to no viewer, so it has every
    single name hidden - there is nobody to make an exception for. A user not
    signed in and a root key with everything (without an owner and without
    chosen permissions) get the data as they are.
    """
    if not hides_names(account):
        return data
    return anonymise(data, own_viewer(account))


def hides_names(account: dict[str, Any] | None) -> bool:
    """Are other people's names, addresses and ids hidden from this account?

    One rule for the data going out (anonymise_for) and for the ids coming in
    in an address (viewer_from_address) - were they to drift apart, one side
    would hide and the other give away.
    """
    who = role(account)
    return who not in ("", ADMIN) and anonymising_enabled(who, account)


# An id that leads to no viewer. It has the shape of a placeholder id, so it
# can go back into an address and a page unchanged; no id from Jellyfin looks
# like this with a question mark.
NOBODY = "skryto-?"
_PLACEHOLDER_ID = re.compile(r"skryto-([1-9][0-9]{0,8})")


def viewer_from_address(value: str | None, account: dict[str, Any] | None) -> str | None:
    """The viewer's id from an address (`?user=`, `/users/<id>`, the History filter) for a query.

    An account with hidden names knows other viewers only under a placeholder
    id (`skryto-2`) - the pages and the API show it no other. That is turned
    back into the viewer here. The real id of another viewer finds nothing:
    whoever knows it (it is in the address of a profile in Jellyfin, say) would
    tell by it that "Viewer 2" is Petr. The own id stays - a viewer knows about
    themselves.
    """
    if not value or not hides_names(account):
        return value
    own = own_viewer(account)
    if own and accounts.bare_id(value) == accounts.bare_id(own):
        return value
    match = _PLACEHOLDER_ID.fullmatch(value)
    if match:
        number = int(match.group(1))
        for user_id, position in _order().items():
            if position == number:
                return user_id
    return NOBODY


class Area:
    """One row in the tree "what a viewer will see"."""

    def __init__(self, key: str, name: str, description: str,
                 paths: tuple[str, ...], default: bool = False,
                 parent: str = "", section: str = "") -> None:
        self.key = key
        self.name = name
        self.description = description
        self.paths = paths
        self.default = default
        self.parent = parent    # the key of the parent area, "" = a root
        # The menu section the area belongs to, drawn in the tree as a heading over its areas
        # ("Music"); the label `name` then names what the section shows ("Overview").
        self.section = section

    @functools.cached_property
    def heading(self) -> tuple[str, ...]:
        """The area's name for places without the tree's headings.

        A section with one area of its own is named by the section alone (("Knihovna",)); in
        one with more, or for a sub-area, the label follows (("Movies & series", "Overview")) -
        "Overview" alone could be any overview. Translation keys; a template translates the
        parts and joins them with " · ". The tree draws the section as a heading and the label
        alone; the dashboard builder, the table of API keys and the note on a card without the
        right use this. Fixed once AREAS exists, so computed once per area.
        """
        if not self.section:
            return (self.name,)
        roots = [area for area in AREAS if area.section == self.section and not area.parent]
        if not self.parent and roots == [self]:
            return (self.section,)
        return (self.section, self.name)


# The section headings of the tree - the sections of the menu (translation keys, like `name`).
DASHBOARD = "Custom overview"
VIDEO = "Movies & series"
MUSIC = "Music"
BOOKS = "Books"
ARR = "*Arr"
SEERR = "Seerr"
LIBRARY = "Library"
NETWORK = "Network"
USERS = "Users"

# The order is the order in the tree - since 16. 11. 2026 the order of the menu by sections
# (navigation.py): every area under the heading of its section, what is `parent` something
# indented under it. The keys are stored with accounts and never change.
# A change of what an area means for an account already set up needs a one-off conversion
# (keep_dashboard_with_overview, split_music_rights).
#
# "My statistics" are not on the list on purpose. A viewer always sees their
# own numbers - without them signing in would make no sense, and there is
# nothing to switch off.
AREAS: tuple[Area, ...] = (
    # The custom dashboard is a root, like in the menu: each of its cards is checked by its own
    # area, so it shows nothing the account may not see elsewhere (it used to need the overview).
    Area("dashboard", "Dashboard of chosen cards",
         "An overview built from selected cards. The administrator decides what is in it; each card shows only with the right to its own area.",
         ("/dashboard",), section=DASHBOARD),
    # /video/partials/top-items here too: the Movies/Series switch of the Most watched card on
    # the overview calls it - it is the same card.
    Area("overview", "Overview",
         "A summary of movies and series for the whole server: how much was watched, when, with what. The numbers are for everyone together.",
         ("/video/overview", "/video/partials/daily", "/video/partials/top-items"), section=VIDEO),
    Area("comparison", "Comparison",
         "The same overview for two periods side by side.",
         ("/video/compare",), parent="overview", section=VIDEO),
    Area("now_playing", "Now playing",
         "Who is watching what right now. Names can be hidden by anonymising.",
         ("/partials/now-playing", "/api/now-playing"), parent="overview", section=VIDEO),
    Area("charts", "Insights and title charts",
         "What gets played most on the server, what lies unused. About titles, not people.",
         ("/video/insights", "/video/partials/top-items"), default=True, section=VIDEO),
    Area("languages", "Languages",
         "Dubbing and subtitles: in total and per viewer.",
         ("/video/languages",), section=VIDEO),
    # Music is separate from the Library: listening does not count as watching and has pages of its own. Split like
    # Movies & series (the user, 22. 11. 2026): the overview with the comparison under it, the insights apart.
    Area("music", "Overview",
         "Music listening: how much and when, who listens, the most played at a glance and how it is played.",
         ("/music/overview", "/music/artist/"), default=True, section=MUSIC),
    Area("music_comparison", "Comparison",
         "The same overview for two periods side by side.",
         ("/music/compare",), parent="music", section=MUSIC),
    Area("music_who", "…including “who listened”",
         "Listener names: the ranking of listeners and who listens to an artist or album. Names can be hidden by anonymisation.",
         (), parent="music", section=MUSIC),
    Area("music_insights", "Insights and charts",
         "The full charts of albums, artists, tracks and genres, and what nobody listens to. About music, not people.",
         ("/music/insights",), default=True, section=MUSIC),
    # Books (23. 11. 2026) are laid out like Music: the overview with the comparison and "who read" under it, the insights
    # apart. A new section, so there is nothing stored to convert.
    Area("books", "Overview",
         "Reading and listening to books: how much and when, who reads, the most read at a glance and how it is read.",
         ("/books/overview",), default=True, section=BOOKS),
    Area("books_comparison", "Comparison",
         "The same overview for two periods side by side.",
         ("/books/compare",), parent="books", section=BOOKS),
    Area("books_who", "…including who read and listened",
         "Reader names: the ranking of readers and who reads a book or an author. Names can be hidden by anonymisation.",
         (), parent="books", section=BOOKS),
    Area("books_insights", "Insights and charts",
         "The full charts of books, authors and genres, and what nobody reads. About books, not people.",
         ("/books/insights",), default=True, section=BOOKS),
    # The *Arr page (web/arr.py): Sonarr, Radarr, Lidarr, Readarr, Bazarr and Prowlarr. Titles, indexers and where things were
    # downloaded from, nothing about people.
    Area("arr", "Sonarr, Radarr, Lidarr, Readarr, Bazarr and Prowlarr",
         "Statistics from Sonarr, Radarr, Lidarr, Readarr, Bazarr and Prowlarr: what was added and when, from where, how fast, upgrades, subtitles and indexers.",
         # /partials/arr/: the cut-out of the live card "Downloading" (web/arr.py).
         ("/arr", "/partials/arr/"), default=True, section=ARR),
    # The Seerr page (web/seerr.py): numbers and titles; the requesters' names only with the sub-area.
    Area("seerr", "Requests",
         "Movie and show requests from Seerr: when and how many, how fast they are handled and whether the requested titles are watched.",
         ("/seerr",), default=True, section=SEERR),
    Area("seerr_who", '…including "who requested"',
         "Requesters' names: the ranking, pending requests and unwatched titles. Names can be hidden with anonymization.",
         (), parent="seerr", section=SEERR),
    Area("library", "Libraries and titles",
         "How much of what, codecs, resolutions, missing data.",
         ("/library", "/item/", "/series/", "/album/", "/book/", "/partials/recently-added"),
         default=True, section=LIBRARY),
    Area("library_who", '…including "who watched it"',
         "The title and library pages then list viewers too. A local account with this right also sees "
         "History (a Jellyfin viewer always sees only their own). Names can be hidden by anonymising.",
         (), parent="library", section=LIBRARY),
    Area("network", "Connections and IP addresses",
         "Where people connect from. These are IP addresses, so think it over.",
         ("/network", "/partials/network-live"), section=NETWORK),
    Area("users", "List of users",
         "Who watches on the server and how much. Names can be hidden by anonymising.",
         ("/users",), section=USERS),
)

BY_KEY = {area.key: area for area in AREAS}

# Paths every signed-in user may open - there is nothing about anybody else on them.
ALWAYS = (
    "/",                 # the start page only redirects (to the default page; web/legacy.py)
    "/history",          # a viewer: their own history (the route forces the filter); a reader: see sees_history
    "/account",          # My account: the language and password of one account, nothing of others
    "/image/",
    "/static/",
    "/health",
    # The cards of an open page drawn again: the route draws only those the account sees, card by card by its
    # area (cards.allowed) - an account without a single card gets an empty answer (web/dashboards.py).
    "/partials/cards",
    "/login",
    "/logout",
    "/setup",
    "/favicon.ico",
)


def login_enabled() -> bool:
    """May one get in with the Jellyfin password?"""
    return db.get_setting(LOGIN_KEY, "0") == "1"


def viewer_shut_out(account: dict[str, Any] | None) -> bool:
    """Is the account a viewer from Jellyfin while sign-in through Jellyfin is off?

    One rule for the browser's session (web.base.current_account) and the API key (api.verify_token): the
    administrator who switches it off is sure the viewers are out, so one signed in before must not go on
    reading. An administrator from Jellyfin is not concerned - the one switching it off may be one.
    """
    return role(account) == VIEWER and not login_enabled()


def area_key(who: str, area: Area) -> str:
    return f"{who}_sees_{area.key}"


def _default(who: str, area: Area) -> bool:
    """What applies until the admin dealt with it.

    A reader account is created by the admin themselves, and nearly always so
    that somebody sees the statistics - so it sees everything until something
    is taken away. A viewer from Jellyfin came on their own; they are allowed
    things bit by bit.
    """
    return True if who == READER else area.default


# ---------------------------------------------------------------------------
# The own permissions of one account
# ---------------------------------------------------------------------------
#
# The group is the default, an account can override it. It is stored with the
# account as JSON in the column `accounts.permissions`: NULL means "by the group",
# anything else is a full list - what is not ticked in it is off. It is not a
# difference against the group (changes only), because then a change of the
# group would quietly change accounts too that the admin believed were set by hand.

ACCESS_COLUMN = "permissions"


def own_permissions(account: dict[str, Any] | None) -> dict[str, Any] | None:
    """The account's own permissions, or None = the group applies.

    A broken JSON is taken as "no own permissions". Closing everything to a
    person because of a typo in the database would be worse than falling back
    to the group, which is set consciously.
    """
    if not account:
        return None
    raw = account.get(ACCESS_COLUMN)
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "areas": {str(k) for k in (data.get("areas") or [])},
        "anonymise": bool(data.get("anonymise")),
    }


def pack_permissions(enabled: Iterable[str], anonymise: bool) -> str:
    """Own permissions in the shape they are stored with an account."""
    return json.dumps({
        "areas": sorted(k for k in enabled if k in BY_KEY),
        "anonymise": bool(anonymise),
    })


# Done once: the setting says the conversion below ran (any non-empty value).
CONVERSION_DASHBOARD_KEY = "conversion_dashboard_overview"


def keep_dashboard_with_overview() -> bool:
    """One-off at start: the custom dashboard stays off where the overview was off.

    Until 16. 11. 2026 the dashboard was a sub-area of the overview: with the overview off it was
    off too, whatever its own box said. Now it is a root of its own - so a group or an account
    with the overview off and the dashboard box left ticked would get the dashboard after the
    upgrade without anybody deciding it. Here the box is unticked for them, once; whatever an
    administrator sets afterwards stays. True = the conversion ran now.
    """
    if db.get_setting(CONVERSION_DASHBOARD_KEY, ""):
        return False
    overview, dashboard = BY_KEY["overview"], BY_KEY["dashboard"]
    for group in (READER, VIEWER):
        def ticked(area: Area, group: str = group) -> bool:
            return db.get_setting(area_key(group, area), "1" if _default(group, area) else "0") == "1"
        if ticked(dashboard) and not ticked(overview):
            db.set_setting(area_key(group, dashboard), "0")
    for row in db.query_all(f"SELECT id, {ACCESS_COLUMN} FROM accounts WHERE {ACCESS_COLUMN} IS NOT NULL"):
        own = own_permissions(row)
        if own is not None and dashboard.key in own["areas"] and overview.key not in own["areas"]:
            accounts.save_permissions(int(row["id"]), pack_permissions(own["areas"] - {dashboard.key}, own["anonymise"]))
    db.set_setting(CONVERSION_DASHBOARD_KEY, "1")
    return True


# Done once: the setting says the conversion below ran (any non-empty value).
CONVERSION_MUSIC_KEY = "conversion_music_pages"
_UNSTORED = "\x00unstored"           # get_setting's default: no value of the group was ever stored
MUSIC_SPLIT = ("music_comparison", "music_insights")


def split_music_rights() -> bool:
    """One-off at start: whoever saw Music sees its comparison and insights too.

    Until 22. 11. 2026 one area "music" opened the whole of Music - its overview held the charts. Now the comparison
    and the insights have areas of their own, like the video ones; without this a group or an account would lose
    the charts it saw, or (the insights being on by default) a group with Music off would gain them. So the stored
    value of "music" is written into both, once; whatever an administrator sets afterwards stays. A group whose Music
    was never stored follows the defaults (Music and its insights on, the comparison like the video one) - nothing
    is written for it, so a fresh install keeps the declared defaults. True = it ran now.
    """
    if db.get_setting(CONVERSION_MUSIC_KEY, ""):
        return False
    music = BY_KEY["music"]
    for group in (READER, VIEWER):
        stored = db.get_setting(area_key(group, music), _UNSTORED)
        if stored == _UNSTORED:
            continue
        for key in MUSIC_SPLIT:
            db.set_setting(area_key(group, BY_KEY[key]), "1" if stored == "1" else "0")
    for row in db.query_all(f"SELECT id, {ACCESS_COLUMN} FROM accounts WHERE {ACCESS_COLUMN} IS NOT NULL"):
        own = own_permissions(row)
        if own is not None and music.key in own["areas"]:
            accounts.save_permissions(int(row["id"]), pack_permissions(own["areas"] | set(MUSIC_SPLIT), own["anonymise"]))
    db.set_setting(CONVERSION_MUSIC_KEY, "1")
    return True


CONVERSION_BOOKS_KEY = "conversion_books_rights"
BOOKS_FROM_MUSIC = (("music_who", "books_who"), ("music_comparison", "books_comparison"))


def split_books_rights() -> bool:
    """One-off at start: a group's stored Music rights for names and the comparison are copied to Books.

    A reader group with stored rights falls back to "on" for an area never stored (`_default`), so the Books areas
    that came later (the readers' names, the comparison) would open for a group that switched the music ones off.
    So the stored value of `music_who` / `music_comparison` is written into `books_who` / `books_comparison`, once.
    `books` and `books_insights` stay at their defaults; a group on the defaults gets nothing written; an account's
    own list is untouched (it gains nothing). True = it ran now.
    """
    if db.get_setting(CONVERSION_BOOKS_KEY, ""):
        return False
    for group in (READER, VIEWER):
        for source, target in BOOKS_FROM_MUSIC:
            # The EFFECTIVE value (the parent counts): the form stores sub-boxes independently, so Music off with
            # "...who listened" still ticked is stored as music=0, music_who=1 and a plain copy would open Books.
            parent = BY_KEY[source].parent
            # Written when the area is stored, or when its parent is stored off (an unstored child then differs
            # from the defaults of Books).
            if db.get_setting(area_key(group, BY_KEY[source]), _UNSTORED) != _UNSTORED \
                    or (parent and db.get_setting(area_key(group, BY_KEY[parent]), _UNSTORED) == "0"):
                db.set_setting(area_key(group, BY_KEY[target]), "1" if sees(group, source) else "0")
    db.set_setting(CONVERSION_BOOKS_KEY, "1")
    return True


def anonymising_enabled(who: str = VIEWER,
                        account: dict[str, Any] | None = None) -> bool:
    """Are other people's names and addresses hidden from this person?

    For a viewer from Jellyfin yes - they came by themselves and the others are
    none of their business. For a reader account no: it saw names so far, and
    the admin created it knowing it would. Whoever wants otherwise switches it -
    for the group, or for one account.
    """
    own = own_permissions(account)
    if own is not None:
        return own["anonymise"]
    return db.get_setting(f"{who}_anonymise",
                          "1" if who == VIEWER else "0") == "1"


# ---------------------------------------------------------------------------
# An own API key
# ---------------------------------------------------------------------------
#
# A reader or a viewer may make a key only when the admin allows it: to the
# group (the setting `{who}_may_have_key`, by default no) or to one account
# (`accounts.may_have_key`: "1" / "0", NULL = by the group). The exception for an
# account is independent of the tree of pages on purpose - one key should not
# require rewriting the whole tree.

MAY_HAVE_KEY_SETTING = "{who}_may_have_key"


def group_may_have_key(who: str) -> bool:
    """Allowing an own key for the whole group (readers / viewers)."""
    return db.get_setting(MAY_HAVE_KEY_SETTING.format(who=who), "0") == "1"


def may_have_key(account: dict[str, Any] | None) -> bool:
    """May this account make and use an own API key?

    Nonsense in the column (a manual change of the database) is taken as "by
    the group" - neither a closed nor an extra open door.
    """
    who = role(account)
    if who == ADMIN:
        return True
    if not who:
        return False
    override = (account or {}).get("may_have_key")
    if override in ("1", "0"):
        return override == "1"
    return group_may_have_key(who)


def sees(who: str, key: str, account: dict[str, Any] | None = None) -> bool:
    if who == ADMIN:
        return True
    area = BY_KEY.get(key)
    if area is None:
        return False
    own = own_permissions(account)
    if own is not None:
        enabled = area.key in own["areas"]
    else:
        default = "1" if _default(who, area) else "0"
        enabled = db.get_setting(area_key(who, area), default) == "1"
    if not enabled:
        return False
    # A sub-area applies only when the one above it is on as well. Otherwise
    # "…including who watched it" would let names through on a page the person
    # may not open anyway - and after the library was switched on, it would
    # show up without anybody deciding it.
    return sees(who, area.parent, account) if area.parent else True


def allowed_paths(who: str,
                  account: dict[str, Any] | None = None) -> tuple[str, ...]:
    """The addresses this person may open."""
    paths: list[str] = list(ALWAYS)
    for area in AREAS:
        if area.paths and sees(who, area.key, account):
            paths.extend(area.paths)
    return tuple(paths)


def may_open_path(who: str, path: str,
                  account: dict[str, Any] | None = None,
                  allowed: tuple[str, ...] | None = None) -> bool:
    """May this person open this address?

    The root `/` is compared **exactly**. As a prefix it would match every
    address in the world, so a switched-on Overview would quietly open
    Settings too - and this list would stop guarding anything.

    `allowed` = allowed_paths(who, account) computed already, when a template
    asks for every row of a table - otherwise they would be computed anew
    every time.
    """
    if who == ADMIN:
        return True
    if allowed is None:
        allowed = allowed_paths(who, account)
    for entry in allowed:
        if entry == "/":
            if path == "/":
                return True
            continue
        if path == entry:
            return True
        if entry.endswith("/") and path.startswith(entry):
            return True
        if not entry.endswith("/") and path.startswith(entry + "/"):
            return True
    return False


def may_open(account: dict[str, Any] | None, path: str,
             allowed: tuple[str, ...] | None = None) -> bool:
    """May this account open the page (GET)? The whole rule in one place.

    It is asked by the middleware when letting a page through, and by a
    template when drawing a link to it - a link behind which "you have no
    access here" waits is worse than none. Two copies of the rule would drift
    apart over time and the links would lie.

    A user not signed in may: for them signing in decides, not permissions.
    `allowed` see may_open_path.
    """
    who = role(account)
    if who in ("", ADMIN):
        return True
    # The path decides, not the parameters: a link /history?day=… leads to /history.
    path = path.split("?", 1)[0]
    # An old page address only redirects (web/legacy.py); its new address is checked on the next request.
    if legacy_urls.is_legacy(path):
        return True
    viewer = own_viewer(account)
    if viewer and path.startswith("/users/"):
        # A viewer has only their own page - another one is closed even with the Users area
        # (the list shows the others only in totals).
        return path.rstrip("/") == f"/users/{viewer}"
    if path == "/history" or path.startswith("/history/"):
        return sees_history(account)
    return may_open_path(who, path, account, allowed)


def sees_history(account: dict[str, Any] | None) -> bool:
    """May the account open History (the page and the /api/v1/history of its key)?

    An admin yes. A viewer always - they see only their own (the route forces
    the filter). A reader has no viewer of their own, so their History is all
    other people's: who watched what when, devices included. That is exactly
    "…including who watched it" - without it, opening History and searching
    for a title would do. A reader has everything by default, so this closes
    it only where the admin switched it off consciously.
    """
    who = role(account)
    if not who:
        return False
    if who == ADMIN or own_viewer(account):
        return True
    return sees(who, "library_who", account)


# A play outside watching tells who played what, when and on which device: a track's play is "...including who
# listened", a book's "...including who read". History's own gate (sees_history) knows only the library's "who watched".
# The kind (stats.KIND_MUSIC, stats.KIND_BOOKS) and the sub-area that lets its plays show with the person.
WHO_OF_KIND = {"music": "music_who", "books": "books_who"}


def _kinds_without_who(account: dict[str, Any] | None) -> frozenset[str]:
    if role(account) == ADMIN:
        return frozenset()
    return frozenset(kind for kind, area in WHO_OF_KIND.items() if not account_sees(account, area))


def hidden_kinds(account: dict[str, Any] | None) -> frozenset[str]:
    """The kinds of plays History and the history in the API leave out for the account (WHO_OF_KIND).

    A viewer from Jellyfin has nothing left out: their History holds only their own plays. Now playing lists
    everybody's, so it goes row by row (visible_plays).
    """
    return frozenset() if own_viewer(account) else _kinds_without_who(account)


def visible_plays(rows: list[dict[str, Any]], account: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Plays (Now playing) without the music and books the account may not see with the person (WHO_OF_KIND).

    A viewer's own play stays - they know about themselves.
    """
    from .stats import TYPES_OF_KIND    # here: the statistics package is heavy and access is imported early

    hidden = {item_type for kind in _kinds_without_who(account) for item_type in TYPES_OF_KIND[kind]}
    if not hidden:
        return rows
    own = accounts.bare_id(own_viewer(account))
    return [row for row in rows if row.get("item_type") not in hidden
            or (own and accounts.bare_id(str(row.get("user_id") or "")) == own)]


def missing_area(account: dict[str, Any] | None, area: str) -> str:
    """The area to tick so that `area` counts for the account: its parent when that is off.

    A sub-area counts only under its parent (sees), so a note "no right to Now playing" would
    send the administrator to a box ticked already. Assumes the account does not see `area`.
    """
    parent = BY_KEY[area].parent if area in BY_KEY else ""
    return parent if parent and not account_sees(account, parent) else area


def account_sees(account: dict[str, Any] | None, area: str) -> bool:
    """Does the account see an area of the permission tree? For content, not for an address.

    For a card a page puts in from another area (the Overview's
    "Now playing"), and for areas without an address of their own
    ("…including who watched it"). The middleware guards addresses only. The cards of the
    dashboards ask it (cards.may_see - the Overview and the custom dashboard) and
    so do the templates of pages (sees()) - one rule, not two copies.

    Without an account nothing: content is drawn only for those signed in, and
    should a summary without signing in ever want it (an e-mail, an export),
    it is to get a closed door.
    """
    if not account:
        return False
    who = role(account)
    return who == ADMIN or sees(who, area, account)


def tree(who: str, account: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The areas for the Settings page - with their state and indentation.

    With an account, its own permissions are drawn; when it has none, the
    group's state - that is what would apply to it, and what the admin starts
    editing from. The state here is **bare** (without the rule about the parent
    area), because a checkbox is to show what is stored, not what follows from it.
    """
    own = own_permissions(account)
    rows = []
    for area in AREAS:
        if own is not None:
            enabled = area.key in own["areas"]
        else:
            enabled = db.get_setting(
                area_key(who, area),
                "1" if _default(who, area) else "0") == "1"
        rows.append({
            "key": area.key,
            "settings": area_key(who, area),
            "name": area.name,
            "description": area.description,
            "parent": area.parent,
            "section": area.section,
            "enabled": enabled,
        })
    return rows


# ---------------------------------------------------------------------------
# Anonymising
# ---------------------------------------------------------------------------
#
# A nickname has to be stable - "Viewer 3" is to be the same person tomorrow as
# today, otherwise nothing can be read from the numbers and the page is of no
# use. So it is taken from the order in the table of viewers, not from the
# order on the page.

# Keys that hold a viewer's name or address.
NAME_KEYS = ("user_name", "user")

# `label` is a label in a chart - in a row with `user_id` it is the viewer's
# name, elsewhere the name of a title or a language. So the neighbour decides,
# not the key itself.
LABEL_KEY = "label"
ADDRESS_KEYS = ("remote_address", "ip")

# `url` is also the name of a link to a release and of Jellyfin's address,
# so the key by itself means nothing. It is hidden only in a row that really
# is about a connection - told by the neighbouring columns.
ADDRESS_NEIGHBOURS = ("home", "people", "country", "city", "last_at")

HIDDEN_ADDRESS = "—"

# Device names. In Jellyfin they are often named after people
# ("Petrův Shield"), and the neighbouring column would give the pseudonym
# "Viewer 3" away. The player (client: Jellyfin Web, Android TV…) gives nothing
# away and stays.
DEVICE_KEYS = ("device_name", "device")


def _order() -> dict[str, int]:
    rows = db.query_all("SELECT id FROM users ORDER BY id")
    return {str(dict(r)["id"]): number
            for number, r in enumerate(rows, start=1)}


def nickname(user_id: str, order: dict[str, int] | None = None) -> str:
    """A stable nickname of a viewer - "Viewer 3" is the same person tomorrow (in the reader's language)."""
    from .i18n import translate

    numbers = _order() if order is None else order
    return translate("Viewer {n}").format(n=numbers.get(str(user_id), 0) or "?")


def _device_order() -> dict[str, int]:
    """Device numbers by their first appearance, not alphabetically.

    Whoever knows the household's device names would map "Device 1..N" back
    by the alphabetical order. And a new device gets the next number, the
    numbers of the older ones do not move.
    """
    rows = db.query_all(
        "SELECT device_name FROM playback"
        " WHERE device_name IS NOT NULL AND device_name != ''"
        " GROUP BY device_name ORDER BY MIN(id)")
    return {str(dict(r)["device_name"]): number
            for number, r in enumerate(rows, start=1)}


# The order of devices is kept in memory for a while: the history in the API
# is anonymised in batches, and the query goes through the whole table of
# playbacks - again for every batch, an anonymised key would be about five
# times slower. A device not in memory yet (it has just started playing)
# reloads the order, at most once every DEVICE_ORDER_RETRY seconds.
DEVICE_ORDER_TTL = 60.0
DEVICE_ORDER_RETRY = 2.0
_DEVICES: dict[str, Any] = {"at": None, "map": {}}


def forget_devices() -> None:
    """Throws away the order of devices in memory - the tests call it; otherwise the order expires after DEVICE_ORDER_TTL."""
    _DEVICES.update(at=None, map={})


def _device_map(name: str) -> dict[str, int]:
    now = time.monotonic()
    age = None if _DEVICES["at"] is None else now - _DEVICES["at"]
    if age is None or age > DEVICE_ORDER_TTL or (
            name not in _DEVICES["map"] and age > DEVICE_ORDER_RETRY):
        _DEVICES.update(at=now, map=_device_order())
    return _DEVICES["map"]


def device_nickname(name: str, order: dict[str, int]) -> str:
    """A stable nickname of a device - "Device 3" is the same device tomorrow."""
    from .i18n import translate

    return translate("Device {n}").format(n=order.get(str(name), 0) or "?")


def anonymise(value: Any, own_id: str,
              order: dict[str, int] | None = None) -> Any:
    """Goes through the data for a template and hides what is about the others.

    It is done here, in one place, not in the templates: there are dozens of
    templates and forgetting one is enough. Everything that is drawn passes
    here - so what is called `user_name` is a name, wherever it is.

    The own name and the own address stay: a viewer knows about themselves.
    """
    order = _order() if order is None else order

    if isinstance(value, dict):
        result = {}
        other_id = str(value.get("user_id") or value.get("id") or "")
        # Without an own viewer (a reader) every row is somebody else's - even one
        # without an id, otherwise "" == "" would let a name through from an import without a UserId.
        foreign = not own_id or other_id != own_id
        is_viewer_row = _is_viewer(value)
        # A device without a viewer (a summary over several people) is always somebody else's.
        device_name = next((value[k] for k in DEVICE_KEYS
                            if isinstance(value.get(k), str) and value[k]), None)
        foreign_device = device_name is not None and (foreign or not other_id)
        for key, content in value.items():
            if foreign_device and isinstance(content, str) and (
                    key in DEVICE_KEYS or (key == LABEL_KEY and content == device_name)):
                result[key] = device_nickname(content, _device_map(content))
            elif foreign and isinstance(content, str) and (
                    key in NAME_KEYS
                    or (key == LABEL_KEY and "user_id" in value)
                    or (key == "name" and is_viewer_row)):
                result[key] = nickname(other_id, order)
            elif foreign and other_id and key in ("user_id",) or (
                    foreign and other_id and key == "id" and is_viewer_row):
                # Even the id alone is a trace: by it a person can be recognised
                # in an address, in a link and across pages. It is replaced by a
                # stable placeholder, so the rows can still be told apart - they
                # just lead to nobody in particular.
                result[key] = _placeholder_id(other_id, order)
            elif foreign and isinstance(content, str) and (
                    key in ADDRESS_KEYS
                    or (key == "address" and _is_address_row(value))):
                result[key] = HIDDEN_ADDRESS
            else:
                result[key] = anonymise(content, own_id, order)
        return result

    if isinstance(value, list):
        return [anonymise(item, own_id, order) for item in value]
    if isinstance(value, tuple):
        return tuple(anonymise(item, own_id, order) for item in value)
    return value


def _is_viewer(row: dict[str, Any]) -> bool:
    """Is this row a viewer from the table `users`, or something else?

    It is decided by the columns only a viewer has - `name` by itself is had by
    a title, a library and plenty else, and renaming a movie to "Viewer 2" would
    be worse than showing the name. Hence not `hours` either: the library's
    tile has it too (the length of the content), and viewers' rows always have
    something more from the list below.
    """
    if "user_id" in row:
        return True
    return any(key in row for key in
               ("is_administrator", "is_disabled", "session_count", "last_seen",
                "last_activity"))


def _placeholder_id(user_id: str, order: dict[str, int] | None = None) -> str:
    numbers = _order() if order is None else order
    return f"skryto-{numbers.get(str(user_id), 0) or '?'}"


def _is_address_row(row: dict[str, Any]) -> bool:
    """Is `url` in this row a viewer's IP address, or a link?"""
    return any(key in row for key in ADDRESS_NEIGHBOURS)
