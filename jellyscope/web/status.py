"""A small JSON interface: /health and /api/now-playing.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from .. import __version__, access, collector, db, formatting, scanner, stats
from .base import STARTED_AT, _self_refresh, change_markers, current_account, require_login

router = APIRouter()

# ---------------------------------------------------------------------------
# A small JSON interface (handy for checking that the app is alive)
# ---------------------------------------------------------------------------


@router.get("/health")
def health(request: Request):
    """The state of the app. A page comes back here every ten seconds.

    Without a sign-in it answers only "alive" and when it started - that is what
    monitoring asks (and the waiting after a restart, when the sign-in does not hold yet).
    The rest, that is what plays right now and how large the library is, is data
    about the server's content and belongs behind the sign-in.
    """
    basics = {"status": "ok", "started_at": int(STARTED_AT)}
    account = current_account(request)
    if account is None:
        return basics

    versions = scanner.versions()
    last_poll = db.get_setting(collector.LAST_POLL_KEY, "")
    return {
        **basics,
        # The version of the running process. The waiting room after an update waits
        # for it: it is the very question it asks ("does the new version run already?"),
        # while `started_at` answers only "something restarted". Somebody not signed in
        # is not told - for monitoring it is enough that the app is alive.
        "version": __version__,
        "collector": db.get_setting(collector.STATUS_KEY, "unknown"),
        "last_poll": last_poll,
        # The left panel's "Last run" as it writes it (in the reader's language), so an open page can keep it current.
        "last_poll_text": formatting.relative_human(last_poll) if last_poll else "",
        # How much is being watched right now - only with the right "Now playing".
        **({"active_sessions": stats.active_session_count()} if access.account_sees(account, "now_playing") else {}),
        "task_running": scanner.is_scan_running(),
        "stop_pending": scanner.stop_requested(),
        "progress": scanner.progress(),
        # The library's version (the Overview watches it, so that it shows the newly
        # added titles by itself once a synchronisation finishes) and the tasks'
        # version (by its change one knows a task ended - even when all of it
        # ran between two queries). Both by one query, because the browser
        # comes back here every ten seconds.
        "library_version": versions["library"],
        "tasks_version": versions["tasks"],
        # What changed for the cards, a marker per source - see change_markers. Only with Refreshing cards on
        # (Settings -> Interface): switched off, no page asks for them.
        **({"markers": change_markers(account, versions)} if _self_refresh() else {}),
    }


@router.get("/api/now-playing")
def api_now_playing(account: dict[str, Any] = Depends(require_login)):
    """The plays running right now as JSON for whoever is signed in (outside the API with keys).

    With hidden names anonymised like the "Now playing" page - raw data with
    names, devices and IP addresses used to be here.
    """
    # Music and books only with "who listened" and "who read".
    sessions = access.visible_plays(stats.active_sessions(), account)
    # Where somebody connects from belongs to Network's "...where they watch from", not to "Now playing".
    if not access.account_sees(account, "network_origin"):
        sessions = [{k: v for k, v in r.items() if k not in access.ADDRESS_KEYS} for r in sessions]
    return access.anonymise_for({"sessions": sessions}, account)
