"""Books: the e-books and audiobooks of Jellyfin's books libraries.

The collector writes listening to an audiobook into the playback history (`playback`, type
"AudioBook") like any other playback, and reading in Jellyfin's reader as type "Book" - if Jellyfin
reports it at all. This package fetches what the books libraries hold, so that listening has a title,
an author, a format and a size; the statistics, cards and pages come later in their own modules.
Watching numbers do not count books (`stats.watching_only`).

The door of the package - whoever writes `from jellyscope import books` gets everything from here:

* `sync` - an incremental run every hour, a full pass once a day (what vanished gets `missing`),
  pairing of imported history afterwards, the state of the last run, `has_books()` (whether the
  Books section has something to show), `book_title_key` (a multi-file audiobook
  is one book), `folded` (text without case and diacritics, for searching and sorting).

Spec: docs/superpowers/specs/2026-11-23-books-design.md
"""
from __future__ import annotations

from .sync import (
    ALREADY_RUNNING,
    NAME,
    BooksError,
    book_title_key,
    folded,
    has_books,
    sync_running,
    sync_state,
    synchronize,
)

__all__ = [
    "ALREADY_RUNNING",
    "NAME",
    "BooksError",
    "book_title_key",
    "folded",
    "has_books",
    "sync_running",
    "sync_state",
    "synchronize",
]
