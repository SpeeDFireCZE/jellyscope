"""The kinds of items Jellyscope keeps (a movie, an episode) and the wrappers that have no file.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

# The kinds of items Jellyscope keeps in its library.
#
# They are exactly those that match one file on the disk - a movie and an episode of a series. Neither a
# series nor a season has a file, so there would be nothing to measure: size, codec, bitrate, nothing.
#
# This list **must match what we ask Jellyfin for** (IncludeItemTypes in jellyfin.py). When an item of another
# kind gets into the table, the sync never sees it again - and `_mark_missing()` marks it as gone on every run.
# The library is then haunted by an "archived" series nobody deleted and which is in Jellyfin.
MANAGED_TYPES = ("Movie", "Episode")

# Wrappers Jellyfin can send instead of the items in them - collections (since version twelve even through
# the type filter, see CollapseBoxSetItems in jellyfin.py), a series, a season, a folder, a playlist. Those
# are never written. On purpose **not** the other way round ("only Movie and Episode"): a freshly added file
# is sent by Jellyfin as `Video` for a while before it files it, and that one IS to be stored - on the next
# run it corrects itself to a movie or an episode (see sync_recent).
WRAPPER_TYPES = ("BoxSet", "Series", "Season", "Folder", "CollectionFolder",
                 "Playlist", "UserView")

# The kind the sync MAY write although it is neither a movie nor an episode: a freshly added file Jellyfin has
# not filed yet. On the next run it corrects itself (see sync_recent), so not even the clean-up may get rid of
# it.
NOT_YET_CLASSIFIED = "Video"

WRITTEN_TYPES = MANAGED_TYPES + (NOT_YET_CLASSIFIED,)


def is_wrapper(item: dict) -> bool:
    """Is it a container, not a title? By its type, or by IsFolder.

    `IsFolder` is here as a guard for a kind we do not know yet. It does no harm to a movie ripped as a folder
    (BluRay, DVD): Jellyfin reports `IsFolder: false` and the kind `Movie` for those - verified on 10.10.7 and
    12.0.0.
    """
    return (str(item.get("Type") or "") in WRAPPER_TYPES
            or bool(item.get("IsFolder")))
