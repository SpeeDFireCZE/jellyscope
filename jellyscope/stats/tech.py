"""The library's technique: resolution, codecs, dynamic range.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from typing import Any

from .. import content_kinds, db, formatting


# Converting the picture's height to a resolution group. It is used in several places, so it is a constant, not a
# copy in five places.
RESOLUTION_CASE = """
    CASE
        WHEN height IS NULL AND width IS NULL      THEN 'unknown'
        WHEN COALESCE(width, 0)  >= 3400
          OR COALESCE(height, 0) >= 2000           THEN '4K'
        WHEN COALESCE(width, 0)  >= 1800
          OR COALESCE(height, 0) >= 1000           THEN '1080p'
        WHEN COALESCE(width, 0)  >= 1200
          OR COALESCE(height, 0) >= 700            THEN '720p'
        WHEN COALESCE(width, 0)  >= 900
          OR COALESCE(height, 0) >= 500            THEN '576p'
        ELSE 'SD'
    END
"""

# The order the resolution groups are shown in (from the highest).
RESOLUTION_ORDER = ["4K", "1080p", "720p", "576p", "SD", "unknown"]


# ---------------------------------------------------------------------------
# The library (the technical part - what MediaLyze can do)
# ---------------------------------------------------------------------------

def tech_coverage() -> dict[str, Any]:
    """How many items have technical data and where it comes from.

    `size_bytes` and `without_size` cover every content kind (video and music - "Velikost
    celkem"); `kinds` = {kind: bytes} of the other kinds, `kind_shares` = [{"share", "size"}]
    for the tile's "of it" note. The counts of items and technical data stay video only.
    """
    coverage = _video_coverage()
    totals = content_kinds.totals()
    coverage["size_bytes"] = int(coverage.get("size_bytes") or 0) + sum(t["size"] for t in totals.values())
    coverage["without_size"] = int(coverage.get("without_size") or 0) + sum(t["without_size"] for t in totals.values())
    coverage["kinds"] = {key: t["size"] for key, t in totals.items() if t["size"]}
    coverage["kind_shares"] = [{"share": content_kinds.BY_KEY[key].share, "size": size}
                               for key, size in coverage["kinds"].items()]
    return coverage


def _video_coverage() -> dict[str, Any]:
    """Technical data of movies and episodes (`items`)."""
    return db.query_one(
        """
        SELECT COUNT(*) AS total,
               -- The size of all libraries together. COALESCE because an item
               -- without technical data has NULL - and NULL would drop the
               -- whole sum down to nothing.
               SUM(COALESCE(size_bytes, 0)) AS size_bytes,
               SUM(CASE WHEN tech_source = 'ffprobe'  THEN 1 ELSE 0 END) AS from_ffprobe,
               SUM(CASE WHEN tech_source = 'jellyfin' THEN 1 ELSE 0 END) AS from_jellyfin,
               SUM(CASE WHEN tech_source IS NULL      THEN 1 ELSE 0 END) AS missing,
               SUM(CASE WHEN tech_error IS NOT NULL   THEN 1 ELSE 0 END) AS errors,
               -- How many items add zero to the sum. Without this number
               -- "Total size" looks like the whole truth even while part of
               -- the library is waiting for the analysis - typically after a
               -- file was replaced, when the technical data were thrown away
               -- on purpose (see scanner.merge_archive_into_live).
               SUM(CASE WHEN size_bytes IS NULL OR size_bytes = 0
                        THEN 1 ELSE 0 END)            AS without_size
        FROM items
        WHERE is_missing = 0
        """
    ) or {}


def codec_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0 AND video_codec IS NOT NULL"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    return db.query_all(
        f"""
        SELECT UPPER(video_codec)               AS label,
               COUNT(*)                         AS item_count,
               COALESCE(SUM(size_bytes), 0)     AS size_bytes
        FROM items {where}
        GROUP BY UPPER(video_codec)
        ORDER BY item_count DESC
        """,
        tuple(params),
    )


def resolution_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    rows = db.query_all(
        f"""
        SELECT {RESOLUTION_CASE} AS label,
               COUNT(*) AS item_count,
               COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM items {where}
        GROUP BY label
        """,
        tuple(params),
    )
    # Sorted by our own logic (4K, 1080p, ...), not alphabetically.
    order = {label: index for index, label in enumerate(RESOLUTION_ORDER)}
    rows.sort(key=lambda row: order.get(row["label"], 99))
    return rows


# The dynamic range as we show it.
#
# Dolby Vision is a POSITIVE FINDING: when any of the sources reports it, it holds. None of them reports it by
# mistake - but both can miss it. Specifically ffprobe before version 5 cannot read DV from Matroska, so about a
# movie Jellyfin describes as "Dolby Vision Profile 8.1" it says only "HDR" - and the overview's third column would
# stay empty although the library holds DV.
#
# Otherwise the measured value holds; when it is missing, the reported one is taken.
RANGE_CASE = """
    CASE WHEN video_range = 'DOVI' OR video_range_reported = 'DOVI' THEN 'DOVI'
         ELSE COALESCE(video_range, video_range_reported) END
"""


def item_range(item: dict[str, Any] | None) -> str | None:
    """The same for one item - for the detail, where no SQL is at hand."""
    if not item:
        return None
    measured = (item.get("video_range") or "").strip().upper()
    reported = (item.get("video_range_reported") or "").strip().upper()
    if "DOVI" in (measured, reported):
        return "DOVI"
    return measured or reported or None


def video_range_breakdown() -> list[dict[str, Any]]:
    rows = db.query_all(
        f"""
        SELECT {RANGE_CASE} AS range_group,
               COUNT(*) AS item_count
        FROM items
        WHERE is_missing = 0 AND tech_source IS NOT NULL
        GROUP BY range_group
        ORDER BY item_count DESC
        """
    )
    # Several spellings can end under one label (an empty string and NULL are both "unknown"), so what belongs
    # together is summed - otherwise the chart would have two columns of the same name.
    totals: dict[str, int] = {}
    for row in rows:
        label = formatting.video_range_human(row["range_group"])
        totals[label] = totals.get(label, 0) + int(row["item_count"] or 0)
    return [{"label": label, "item_count": count}
            for label, count in sorted(totals.items(), key=lambda d: -d[1])]
