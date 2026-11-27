"""Bazarr's live state: the counts of what is missing, the providers and the warnings - and the languages.

It is not stored - it is "how it looks right now". The Bazarr tab asks when shown, but through a
one-minute cache (`CACHE`), so reloading the page or switching the period does not flood Bazarr with
requests, and every request waits at most LIVE_TIMEOUT (3 s): the page waits for it. The cache notices a
change of the address and of the key (the key only as a fingerprint): fixed settings load at once.

Three cheap requests at once: the counts from Bazarr's header (`badges`), the enabled providers and the warnings.
The languages missing subtitles come from the first `MISSING_LIMIT` episodes and movies without subtitles - two
big lists (thousands of items) that change slowly, so the *Arr sync reads them (sync.py, with a long limit) and
stores the languages; this only adds them with their time (`stored_languages`). When more are missing, the state
says so (`languages_incomplete`): the counts by language are then only from the first part.

An unavailable Bazarr does not bring the page down: `dostupna: False` and the error (without the key,
translated on every read); the statistics from the database and the stored languages still show.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from typing import Any

import httpx

from ... import db, i18n
from ...services import LIVE_TIMEOUT, integer, settings_fingerprint
from .client import BazarrClient, BazarrError

TTL = 60.0             # seconds in the cache
# The languages missing subtitles, stored by the sync: {"fingerprint", "languages", "incomplete", "at"}.
LANGUAGES_KEY = "arr_bazarr_missing_languages"
MISSING_LIMIT = 2000   # how many episodes and how many movies without subtitles are read at most for the languages
GOOD = "Good"          # the state of a provider that is not throttled
# Under the key "bazarr": (when loaded - time.monotonic(), the address and the key's fingerprint, state, error).
CACHE: dict[str, tuple[Any, ...]] = {}


def _empty() -> dict[str, Any]:
    return {"available": False, "error": "", "episode_count": None, "movie_count": None, "limited_count": None, "warnings": None,
            "languages": None, "languages_incomplete": False, "providers": None, "health": None}


def _languages(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """How many episodes and movies wait for which language (subtitles for the hearing impaired and forced ones apart), the most common first.

    The second value: are there more missing than were read?
    """
    counts: Counter[tuple[str, bool, bool]] = Counter()
    incomplete = False
    for page in pages:
        # Without a known count (an answer without `total`) a full page is most likely only a part.
        total = page["total"]
        incomplete = incomplete or (len(page["data"]) >= MISSING_LIMIT if total is None else total > len(page["data"]))
        for item in page["data"]:
            missing = item.get("missing_subtitles")
            for language in missing if isinstance(missing, list) else []:
                if isinstance(language, dict):
                    code = str(language.get("code2") or language.get("code3") or language.get("name") or "")
                    counts[(code, language.get("hi") is True, language.get("forced") is True)] += 1
    rows = [{"language": code, "hi": hi, "forced": forced, "count": count} for (code, hi, forced), count in counts.items()]
    return sorted(rows, key=lambda r: (-r["count"], r["language"], r["hi"], r["forced"])), incomplete


def _providers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The enabled providers, the throttled first. `again` is Bazarr's text ("in 12 hours"), empty for one that is not throttled."""
    rows = []
    for record in records:
        state, retry = str(record.get("status") or ""), str(record.get("retry") or "")
        good = state == GOOD
        rows.append({"name": str(record.get("name") or "?"), "state": "" if good else state, "fine": good,
                     "again": "" if good or retry == "-" else retry})
    return sorted(rows, key=lambda r: (r["fine"], r["name"]))


def stored_languages(fingerprint: str) -> dict[str, Any]:
    """The languages the sync stored: {"languages": rows or None (not counted yet), "languages_incomplete",
    "languages_at"} - only for the same address and key."""
    try:
        stored = json.loads(db.get_setting(LANGUAGES_KEY, "") or "{}")
    except ValueError:
        stored = {}
    if not isinstance(stored, dict) or stored.get("fingerprint") != fingerprint or not isinstance(stored.get("languages"), list):
        return {"languages": None, "languages_incomplete": False, "languages_at": ""}
    return {"languages": stored["languages"], "languages_incomplete": bool(stored.get("incomplete")),
            "languages_at": str(stored.get("at") or "")}


async def _load(url: str, key: str,
                transport: httpx.AsyncBaseTransport | None) -> tuple[dict[str, Any], BazarrError | None]:
    """Three cheap requests at once. What fails stays None (unknown); if everything fails, Bazarr is unavailable.

    None, not zero: "without subtitles 0" after a failed request would be a claim nobody checked.
    Returns (the state, the first error).
    """
    state = _empty()
    try:
        client = BazarrClient(url, key, transport=transport, timeout=LIVE_TIMEOUT)
    except BazarrError as error:             # an invalid stored address or key
        return state, error
    async with client as c:
        results = list(await asyncio.gather(c.badges(), c.providers(), c.health(), return_exceptions=True))
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, (BazarrError, ValueError, TypeError, AttributeError)):
            raise error            # an error in the program, not in the app: let it show
    first = next((e if isinstance(e, BazarrError) else BazarrError("{reason}", reason=i18n.error_text(e)) for e in errors), None)
    if len(errors) == len(results):
        return state, first
    badges, providers, health = (None if isinstance(r, BaseException) else r for r in results)
    badges = badges if isinstance(badges, dict) else {}
    state["available"] = True
    state["episode_count"], state["movie_count"] = integer(badges.get("episodes"), None), integer(badges.get("movies"), None)
    if providers is not None:
        state["providers"] = _providers(providers)
        state["limited_count"] = sum(not p["fine"] for p in state["providers"])
    else:
        state["limited_count"] = integer(badges.get("providers"), None)
    if health is not None:
        # Also in the shape of the other apps (type, message) for the API (api_arr.health): Bazarr has no type.
        state["health"] = [{"what": str(h.get("object") or ""), "problem": str(h.get("issue") or ""), "type": "",
                            "message": ": ".join(part for part in (str(h.get("object") or ""), str(h.get("issue") or "")) if part)}
                           for h in health]
        state["warnings"] = len(state["health"])
    else:
        state["warnings"] = integer(badges.get("status"), None)
    return state, first


async def live_state(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Bazarr's state now (from the cache, if it is younger than a minute), with the languages the sync stored.

    The error is translated only here, on every read: the cache is shared by everyone and everyone is
    to read it in their own language.
    """
    url, key = db.get_setting("arr_bazarr_url", ""), db.get_setting("arr_bazarr_api_key", "")
    fingerprint = settings_fingerprint(url, key)
    cached = CACHE.get("bazarr")
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < TTL:
        _, _, state, error = cached
    else:
        state, error = await _load(url, key, transport)
        CACHE["bazarr"] = (time.monotonic(), fingerprint, state, error)
    return {**state, **stored_languages(fingerprint), "error": error.translated() if error else ""}
