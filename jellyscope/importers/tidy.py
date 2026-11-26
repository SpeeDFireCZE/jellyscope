"""Tidying the data (every repair in a row) and the import summary.

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, i18n, scanner
from ..jellyfin import JellyfinError
from .cleanup import cap_nonsense_durations, merge_duplicate_playback
from .duplicates import merge_import_duplicates
from .linking import _link_by_episode_number
from .orphans import (
    look_up_orphans_in_jellyfin,
    orphan_playback_count,
    relink_orphans,
    repair_episode_links,
    unify_names,
)

log = logging.getLogger("jellyscope.importers")


async def tidy_data() -> dict[str, Any]:
    """Put the history in order. One action, so that nobody picks the order.

    The steps go in a row on purpose - each builds on the previous one:

      1. **Looking up in Jellyfin.** The identifier in the imported history is the real Jellyfin ItemId.
         When Jellyfin still knows it, it tells the series and the episode's number - and so the record
         can be filed. It must be first: it gives the links all the further steps work with.
      2. **Linking by name and episode number** for what Jellyfin no longer knows.
      3. **Returning to the right episodes** - the remedy after a bug where a series' whole history merged
         into one episode.
      4. **Merging duplicates** within one source and across sources.
      5. **Capping nonsense lengths** in imported history: what ran multiples of the show's length was an
         open player, not watching.
      6. **Unifying names** by the library: a renamed title has one name on the whole overview, not two.

    The button in Settings and the scheduled task use it - hence one function and not two almost equal
    pieces of code.

    Returns a dict with the counts of every step; the key "parts" is a ready list of sentences for the
    message.
    """
    result: dict[str, Any] = {}

    # The look-up needs Jellyfin. When it does not run, that is no reason to stop the rest - the rest does
    # without the network.
    try:
        result["jellyfin"] = await look_up_orphans_in_jellyfin()
    except JellyfinError as exc:
        log.warning("the Jellyfin lookup failed: %s", exc)
        result["jellyfin"] = {"status": "error", "message": i18n.error_text(exc)}

    # The archive first: when an episode returns to its live record, the further steps have one item less
    # they could link the history to.
    result["archive"] = scanner.merge_archive_into_live()
    result["linked"] = relink_orphans()
    result["episode_number"] = _link_by_episode_number()
    result["returned"] = repair_episode_links()
    result["duplicates"] = merge_duplicate_playback()
    result["from_import"] = merge_import_duplicates()
    result["lengths"] = cap_nonsense_durations()
    result["unified_names"] = unify_names()

    jf = result["jellyfin"]
    parts: list[tuple[str, dict[str, Any]]] = []
    if result["archive"]:
        parts.append(("returned from the archive to live episodes: {n}",
                      {"n": result["archive"]}))
    if jf.get("linked"):
        parts.append(("assigned from Jellyfin: {n} titles", {"n": jf["linked"]}))
    if jf.get("created_count"):
        parts.append(("added to the library: {n} titles", {"n": jf["created_count"]}))
    if jf.get("filled"):
        parts.append(("series filled in for {n} titles", {"n": jf["filled"]}))
    if result["linked"]["items"]:
        parts.append(("linked by name: {n} records",
                      {"n": result["linked"]["rows"]}))
    if result["episode_number"][1]:
        parts.append(("linked by episode number: {n} records",
                      {"n": result["episode_number"][1]}))
    if result["returned"]["moved"]:
        parts.append(("moved back to the right episodes: {n}",
                      {"n": result["returned"]["moved"]}))
    if result["duplicates"]["removed"]:
        parts.append(("duplicates merged: {n}",
                      {"n": result["duplicates"]["removed"]}))
    if result["from_import"]["removed"]:
        parts.append(("merged across import sources: {n}",
                      {"n": result["from_import"]["removed"]}))
    if result["lengths"]["rows"]:
        parts.append(("playbacks longer than the title itself, shortened: {n}",
                      {"n": result["lengths"]["rows"]}))
    if result["unified_names"]["rows"]:
        parts.append(("names aligned with the library: {n}",
                      {"n": result["unified_names"]["rows"]}))

    result["parts"] = parts
    result["left"] = orphan_playback_count()
    if parts:
        # The plays changed: an open page draws its cards over plays again (the plays' marker in /health).
        db.note_plays_rewritten()
    log.info("data tidy-up: %s changes, %s orphans remain",
             len(parts), result["left"])
    return result


def import_summary() -> dict[str, Any]:
    """How many records are from the import already - so that Settings show what happened."""
    return db.query_one(
        """
        SELECT
            SUM(CASE WHEN session_key LIKE 'import:pbr:%' THEN 1 ELSE 0 END) AS playback_reporting,
            SUM(CASE WHEN session_key LIKE 'import:jst:%' THEN 1 ELSE 0 END) AS jellystat,
            SUM(CASE WHEN session_key NOT LIKE 'import:%' THEN 1 ELSE 0 END) AS own,
            MIN(started_at) AS oldest
        FROM playback
        """
    ) or {}
