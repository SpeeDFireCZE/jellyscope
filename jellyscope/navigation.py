"""The menu as data: sections, their pages, and what one account gets to see.

Since 16. 11. 2026 the menu is split into sections by the kind of content
(docs/superpowers/specs/2026-11-16-menu-sections-design.md). The tree lives
here, once; base.html only draws what `menu()` returns - the list on the
desktop and the same tree in the burger on a phone.

* `SECTIONS` - the sections in menu order and their pages. A page has its
  address, a label (the English source text, translated when the menu is
  built) and the address prefixes that count as "this page is open" (an
  artist's page belongs to the Music overview, an item's detail to the
  Library).
* A section is shown only when Jellyfin has content of its kind (`available`)
  and the account may open at least one of its pages (the same rule the
  permission guard uses, access.may_open). A section without content
  also answers 404 on its pages (`require`) - except a detail page other pages
  link to (an artist's page), like an album's page in the Library.
* Only the section of the open page is expanded; a click on a section
  opens its first page the account may see.

Above the sections stay the personal entries (a viewer's own statistics and
history, the custom dashboard, My dashboard) exactly as before.

Books (23. 11. 2026) are the section after Music, laid out like it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from . import access, cards, db, i18n, music
from .books import has_books
from .arr import registry as arr_apps
from .seerr import is_connected as seerr_connected

# The item types of the "Movies & series" section (the library sync stores no others as items).
VIDEO_TYPES = ("Movie", "Episode")


def has_video() -> bool:
    """Is there a movie or an episode that has not disappeared from Jellyfin?"""
    return bool(db.query_value(
        "SELECT 1 FROM items WHERE type IN (?, ?) AND is_missing = 0 LIMIT 1", VIDEO_TYPES))


def has_music() -> bool:
    """Is there a track that has not disappeared from Jellyfin? (music.has_music)"""
    return music.has_music()


@dataclass(frozen=True)
class Page:
    key: str
    label: str
    url: str
    prefixes: tuple[str, ...]
    available: Callable[[], bool] | None = None
    # An *Arr app's page: available while the app is connected. One menu asks for the
    # connected apps once, not once per page.
    app: str = ""


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    pages: tuple[Page, ...]
    available: Callable[[], bool] | None = None
    # False = a single item without a submenu (Seerr, Library, Network, ...).
    submenu: bool = True


def _arr_page(key: str) -> Page:
    app = arr_apps.APPS[key]
    return Page(key, app.name, f"/arr/{key}", (f"/arr/{key}",), app=key)


def _single(key: str, label: str, url: str, prefixes: tuple[str, ...],
            available: Callable[[], bool] | None = None) -> Section:
    return Section(key, label, (Page(key, label, url, prefixes),), available=available, submenu=False)


SECTIONS: tuple[Section, ...] = (
    Section("video", "Movies & series", (
        Page("overview", "Overview", "/video/overview", ("/video/overview",)),
        Page("compare", "Comparison", "/video/compare", ("/video/compare",)),
        Page("insights", "Insights", "/video/insights", ("/video/insights",)),
        Page("languages", "Languages", "/video/languages", ("/video/languages",)),
    ), available=has_video),
    Section("music", "Music", (
        # Like Movies & series (22. 11. 2026). An artist's page belongs to the overview.
        Page("overview", "Overview", "/music/overview", ("/music/overview", "/music/artist/")),
        Page("compare", "Comparison", "/music/compare", ("/music/compare",)),
        Page("insights", "Insights", "/music/insights", ("/music/insights",)),
    ), available=has_music),
    Section("books", "Books", (
        Page("overview", "Overview", "/books/overview", ("/books/overview",)),
        Page("compare", "Comparison", "/books/compare", ("/books/compare",)),
        Page("insights", "Insights", "/books/insights", ("/books/insights",)),
    ), available=has_books),
    # The *Arr family under its usual name; each app is in the submenu only while it is connected.
    Section("arr", "*Arr", tuple(_arr_page(key) for key in arr_apps.APPS)),
    _single("seerr", "Seerr", "/seerr", ("/seerr",), available=seerr_connected),
    _single("library", "Library", "/library", ("/library", "/item/", "/series/", "/album/", "/book/")),
    _single("network", "Network", "/network", ("/network",)),
    _single("users", "Users", "/users", ("/users",)),
    _single("history", "History", "/history", ("/history",)),
    _single("settings", "Settings", "/settings", ("/settings",)),
)
_BY_KEY = {section.key: section for section in SECTIONS}


def _page_available(page: Page, memo: dict[Any, Any]) -> bool:
    """Has this page content now? `memo` keeps every answer for one menu or one landing.

    An *Arr app's page is available while its app is connected - asked once per memo, not once
    per app; any other page by its own condition (None = always).
    """
    if page.app:
        if "arr" not in memo:
            memo["arr"] = arr_apps.connected()
        return page.app in memo["arr"]
    if page.available is None:
        return True
    if page.available not in memo:
        memo[page.available] = bool(page.available())
    return memo[page.available]


def _is_open(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def _personal(account: dict[str, Any], path: str) -> list[dict[str, Any]]:
    """The entries above the sections: a viewer's own pages, the custom dashboard, My dashboard."""
    items: list[tuple[str, str, str, tuple[str, ...]]] = []
    viewer = access.own_viewer(account)
    if viewer:
        items += [("my_statistics", i18n.translate("My statistics"), f"/users/{viewer}", ("/users",)),
                  ("my_history", i18n.translate("My history"), "/history", ("/history",))]
    if cards.shows_dashboard_tab(account):
        items.append(("dashboard", cards.dashboard_name() or i18n.translate("Custom overview"), "/dashboard", ("/dashboard",)))
    if cards.load_layout(account["id"]):
        items.append(("my_dashboard", i18n.translate("My overview"), "/account/dashboard", ("/account/dashboard",)))
    return [{"key": key, "label": label, "url": url, "active": _is_open(path, prefixes), "expanded": False,
             "submenu": False, "pages": []} for key, label, url, prefixes in items]


def menu(account: dict[str, Any] | None, path: str,
         allowed: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """The menu of one account on one page: personal entries, then the sections it may see.

    Each entry is {"key", "label", "url", "active", "expanded", "submenu", "pages": [{"key",
    "label", "url", "active"}]}; labels are already translated into the language of the page.
    `allowed` is the page's cached "may this account open this address" (web/base._may_open_for);
    without it the permission rule is asked directly. Every content condition is evaluated
    once per call - the menu is drawn on every page.
    """
    if not account:
        return []
    may_open = allowed or (lambda url: access.may_open(account, url))
    memo: dict[Any, Any] = {}

    def has(condition: Callable[[], bool] | None) -> bool:
        if condition is None:
            return True
        if condition not in memo:
            memo[condition] = bool(condition())
        return memo[condition]

    viewer = access.own_viewer(account)
    entries = _personal(account, path)
    for section in SECTIONS:
        # A viewer has their own history above; the shared one is not for them.
        if section.key == "history" and viewer:
            continue
        if not has(section.available):
            continue
        pages = [{"key": page.key, "label": _page_label(page), "url": page.url,
                  "active": _is_open(path, page.prefixes)}
                 for page in section.pages if _page_available(page, memo) and may_open(page.url)]
        if not pages:
            continue
        active = any(page["active"] for page in pages)
        entries.append({"key": section.key, "label": i18n.translate(section.label), "url": pages[0]["url"],
                        "active": active, "expanded": active and section.submenu, "submenu": section.submenu,
                        "pages": pages if section.submenu else []})
    return entries


def _page_label(page: Page) -> str:
    """A page's label in the menu; an *Arr app's page is named by the app that is connected (Chaptarr, not Readarr)."""
    return arr_apps.APPS[page.app].shown_name() if page.app else i18n.translate(page.label)


def current_title(entries: list[dict[str, Any]]) -> str:
    """The label of the open page for the burger button: "Section · Page", a single item by its name."""
    for entry in entries:
        if not entry["active"]:
            continue
        page = next((page for page in entry["pages"] if page["active"]), None)
        return f"{entry['label']} · {page['label']}" if entry["submenu"] and page else entry["label"]
    return i18n.translate("Menu")


def require(section_key: str) -> None:
    """A page of a section without content does not exist: 404 (the section is not in the menu either)."""
    available = _BY_KEY[section_key].available
    if available is not None and not available():
        raise HTTPException(status_code=404)


# --- The default page ------------------------------------------------------
# What / opens (and the logo, and signing in): the overview of a section or the custom
# dashboard, chosen by the administrator in Settings > Interface. Books is offered while there are books.
DEFAULT_PAGE_KEY = "ui_default_page"
DEFAULT_PAGES: dict[str, str] = {
    "video": "/video/overview", "music": "/music/overview", "books": "/books/overview", "dashboard": "/dashboard"}
# The Settings value for "nothing chosen" (stored as ""): see default_page_url.
AUTO = "auto"
# What is tried when the chosen page is not available, after the choice itself.
_FALLBACK = ("video", "music", "books", "dashboard")
_PAGE_LABELS = {
    "/video/overview": "Movies & series", "/music/overview": "Music", "/books/overview": "Books", "/library": "Library",
    "/history": "History", "/account": "My account"}


def _dashboard_label() -> str:
    return cards.dashboard_name() or i18n.translate("Custom overview")


def default_page_options(account: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The choices Settings > Interface offers now: (value, translated label), only what exists.

    A section without content is not offered, the custom dashboard only while it is on.
    """
    options = []
    if has_video():
        options.append(("video", i18n.translate("Movies & series")))
    if has_music():
        options.append(("music", i18n.translate("Music")))
    if has_books():
        options.append(("books", i18n.translate("Books")))
    if cards.is_enabled():
        options.append(("dashboard", _dashboard_label()))
    return options


def landing(key: str, account: dict[str, Any]) -> str | None:
    """Where the default page or section `key` ("video", "music", "books", "dashboard", any section key)
    opens for this account now, or None when it cannot. Also the target of /video, /music and /books
    (web/legacy.section_root).

    A section opens at its first page the account may open - like a click on it in the
    menu (an account with Insights but without the overview lands on Insights).
    """
    if key == "dashboard":
        # On, allowed and with a card the account sees.
        return DEFAULT_PAGES[key] if cards.sees_dashboard(account) else None
    section = _BY_KEY[key]
    if section.available is not None and not section.available():
        return None
    memo: dict[Any, Any] = {}
    return next((page.url for page in section.pages
                 if _page_available(page, memo) and access.may_open(account, page.url)), None)


def default_page_url(account: dict[str, Any]) -> str:
    """Where / sends this account: its default page, or the first one it can open.

    A viewer from Jellyfin starts on their own page. Without a saved choice (an installation
    from before the menu by sections) the custom dashboard opens when it has something to show,
    otherwise Movies & series - as before. A saved choice holds even while unavailable; the
    order Movies & series -> Music -> Books -> custom dashboard -> Library -> History -> My account then
    picks the first page the account may open.
    """
    viewer = access.own_viewer(account)
    if viewer:
        return f"/users/{viewer}"
    chosen = db.get_setting(DEFAULT_PAGE_KEY, "")
    if chosen not in DEFAULT_PAGES:
        chosen = "dashboard" if cards.sees_dashboard(account) else "video"
    for key in (chosen, *(k for k in _FALLBACK if k != chosen)):
        target = landing(key, account)
        if target:
            return target
    if access.may_open(account, "/library"):
        return "/library"
    return "/history" if access.sees_history(account) else "/account"


def _label_of(url: str) -> str:
    """The name of a page `default_page_url` can return, for "Opening now: ..."."""
    if url == DEFAULT_PAGES["dashboard"]:
        return _dashboard_label()
    for section in SECTIONS:
        if any(page.url == url for page in section.pages):
            return i18n.translate(section.label)
    return i18n.translate(_PAGE_LABELS.get(url, url))


def default_page_field(account: dict[str, Any]) -> dict[str, Any]:
    """What Settings > Interface shows: the choices, the selected one, and what opens now.

    "Automatic" (AUTO, nothing saved) is the way of installations from before the menu by
    sections: the custom dashboard when it has something to show, otherwise Movies & series.
    A saved choice stays selected even when it is not on offer now (marked as unavailable), so
    saving another setting of the same form keeps it. `now` names the page that opens when it
    is not the chosen one - always for Automatic, and for a choice that cannot open now.
    """
    options = [(AUTO, i18n.translate("Automatic")), *default_page_options(account)]
    stored = db.get_setting(DEFAULT_PAGE_KEY, "")
    chosen = stored if stored in DEFAULT_PAGES else AUTO
    if chosen not in dict(options):
        label = {"video": i18n.translate("Movies & series"), "music": i18n.translate("Music"),
                 "books": i18n.translate("Books"), "dashboard": _dashboard_label()}[chosen]
        options.append((chosen, i18n.translate("{page} (not available now)").format(page=label)))
    url = default_page_url(account)
    now = "" if url == DEFAULT_PAGES.get(chosen) else _label_of(url)
    return {"options": options, "chosen": chosen, "now": now}
