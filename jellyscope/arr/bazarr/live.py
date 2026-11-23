"""Bazarr's live state: what is missing, the providers and the warnings.

It is not stored - it is "how it looks right now". The Bazarr tab asks when shown, but through a
one-minute cache (`CACHE`), so reloading the page or switching the period does not flood Bazarr with
requests. The cache notices a change of the address and of the key (the key only as a fingerprint):
fixed settings load at once.

Five requests at once: the counts from Bazarr's header (`badges`), the enabled providers, the warnings
and the first `MISSING_LIMIT` episodes and movies without subtitles - of those only the languages they
lack. When more are missing, the state says so (`languages_incomplete`): the counts by language are then only
from the first part.

The two lists of the missing are the most expensive part (thousands of items) and change slowly: so
the languages are kept longer (`LANGUAGES_TTL`) and after a minute only three cheap requests are read.
They hold only as long as the counts of episodes and movies without subtitles they were read with
still match, though - as soon as a count changes, they are read again at once, so the breakdown by
language does not contradict the numbers next to it. A failed read is not kept - it is tried again
next time.

An unavailable Bazarr does not bring the page down: `dostupna: False` and the error (without the key,
translated on every read); the statistics from the database still show.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter
from typing import Any

import httpx

from ... import db, i18n
from ...services import integer
from .client import BazarrClient, BazarrError

TTL = 60.0             # seconds in the cache
LANGUAGES_TTL = 600.0  # how long the languages of the missing hold (two big lists)
MISSING_LIMIT = 2000   # how many episodes and how many movies without subtitles are read at most for the languages
GOOD = "Good"          # the state of a provider that is not throttled
# Under the key "bazarr": (when loaded - time.monotonic(), the address and the key's fingerprint, state, error).
# Under the key "languages": (when, the address and the key's fingerprint, (the languages of the missing, whether
# there are more than read, (episodes, movies without subtitles at the time of the read))).
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


async def _load(url: str, key: str, transport: httpx.AsyncBaseTransport | None,
                in_memory: tuple[Any, ...] | None = None) -> tuple[dict[str, Any], BazarrError | None, bool]:
    """Three cheap requests at once and after them the two lists of the missing - those only when the languages
    are not in memory (`in_memory`) or the count of episodes or movies without subtitles changed since they were
    read. What fails stays None (unknown); if everything fails, Bazarr is unavailable.

    None, not zero: "without subtitles 0" after a failed request would be a claim nobody checked.
    Returns (the state, the first error, whether the languages are freshly loaded).
    """
    state = _empty()
    try:
        client = BazarrClient(url, key, transport=transport)
    except BazarrError as error:             # an invalid stored address or key
        return state, error, False
    async with client as c:
        results = list(await asyncio.gather(c.badges(), c.providers(), c.health(), return_exceptions=True))
        first_badges = results[0] if isinstance(results[0], dict) else {}
        counts = (integer(first_badges.get("episodes"), None), integer(first_badges.get("movies"), None))
        from_memory = in_memory is not None and in_memory[2] == counts
        # When not even one of the cheap requests answered, the two expensive ones are not waited for.
        if not from_memory and not all(isinstance(r, BaseException) for r in results):
            results += await asyncio.gather(c.missing("episode", MISSING_LIMIT), c.missing("movie", MISSING_LIMIT),
                                            return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, (BazarrError, ValueError, TypeError, AttributeError)):
            raise error            # an error in the program, not in the app: let it show
    first = next((e if isinstance(e, BazarrError) else BazarrError("{reason}", reason=i18n.error_text(e)) for e in errors), None)
    if len(errors) == len(results):
        return state, first, False
    badges, providers, health, *missing = (None if isinstance(r, BaseException) else r for r in results)
    badges = badges if isinstance(badges, dict) else {}
    state["available"] = True
    state["episode_count"], state["movie_count"] = integer(badges.get("episodes"), None), integer(badges.get("movies"), None)
    if providers is not None:
        state["providers"] = _providers(providers)
        state["limited_count"] = sum(not p["fine"] for p in state["providers"])
    else:
        state["limited_count"] = integer(badges.get("providers"), None)
    if health is not None:
        state["health"] = [{"what": str(h.get("object") or ""), "problem": str(h.get("issue") or "")} for h in health]
        state["warnings"] = len(state["health"])
    else:
        state["warnings"] = integer(badges.get("status"), None)
    fresh = False
    if from_memory:
        state["languages"], state["languages_incomplete"] = in_memory[0], in_memory[1]
    elif len(missing) == 2 and all(listing is not None for listing in missing):
        state["languages"], state["languages_incomplete"] = _languages(missing)
        fresh = True
    return state, first, fresh


async def live_state(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Bazarr's state now (from the cache, if it is younger than a minute).

    The error is translated only here, on every read: the cache is shared by everyone and everyone is
    to read it in their own language.
    """
    url, key = db.get_setting("arr_bazarr_url", ""), db.get_setting("arr_bazarr_api_key", "")
    fingerprint = url + "|" + hashlib.sha256(key.encode()).hexdigest()
    cached = CACHE.get("bazarr")
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < TTL:
        _, _, state, error = cached
    else:
        languages = CACHE.get("languages")
        in_memory = languages[2] if languages and languages[1] == fingerprint and time.monotonic() - languages[0] < LANGUAGES_TTL else None
        state, error, fresh = await _load(url, key, transport, in_memory)
        CACHE["bazarr"] = (time.monotonic(), fingerprint, state, error)
        if fresh:
            CACHE["languages"] = (time.monotonic(), fingerprint,
                               (state["languages"], state["languages_incomplete"], (state["episode_count"], state["movie_count"])))
    return {**state, "error": error.translated() if error else ""}
