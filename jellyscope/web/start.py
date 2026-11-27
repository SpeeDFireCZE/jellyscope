"""The start and shutdown of the app (lifespan): what starts running with the server and what stops at shutdown.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import access, accounts, api, applog, cards, collector, db, notifications, scanner, tasks, web_sessions
from ..arr import prowlarr
from ..config import warn_about_short_secret_key
from .base import config, log

# How long the start waits for the work on the data and backup files (_tidy_files). A backup folder on a network share
# that does not answer (a NAS asleep, a hard NFS mount) must not keep the port closed; past it the work goes on in its
# thread, without the start.
FILE_WORK_SECONDS: float = 10


async def _tidy_files() -> None:
    """At the start: half-written backups go and files from before umask 077 are narrowed - off the event loop.

    The backups the interrupted runs were writing stayed half-written (*.partial), the size of the database each time;
    the database, logs and backups of an installation from before umask 077 were readable to every account. Both
    touch the backup folder, which may lie on another disk or a share.
    """
    def work() -> tuple[int, int]:
        return tasks.remove_partial_backups(), tasks.narrow_private_files()

    try:
        partial, narrowed = await asyncio.wait_for(asyncio.to_thread(work), FILE_WORK_SECONDS)
    except TimeoutError:
        log.warning("the data and backup files did not answer within %s s, the start goes on without them",
                    FILE_WORK_SECONDS)
        return
    if partial:
        log.info("deleted %s half-written backups left by the previous start", partial)
    if narrowed:
        log.info("made %s data files and folders readable by their owner only", narrowed)


def _convert_rights() -> None:
    """The one-off conversions of stored rights after an update - each runs once, the administrator decides after it.

    The order matters: the old root keys keep the areas a key could use until 1.9 before a key with Network gets
    where people watch from.
    """
    # The custom dashboard no longer needs the overview (16. 11. 2026): where the overview was
    # off, the dashboard stays off too - once, the administrator decides from then on.
    access.keep_dashboard_with_overview()
    # Music's comparison and insights got areas of their own (22. 11. 2026): whoever saw Music sees them too,
    # once - the administrator decides from then on.
    access.split_music_rights()
    # Books got their own readers' names and comparison areas (23. 11. 2026): a group follows its Music values, once.
    access.split_books_rights()
    # Root keys stored with areas the API has no address for lose them (16. 11. 2026), once.
    api.drop_unusable_key_areas()
    # After it: the old keys keep the areas a key could use until 1.9 first, then a key with Network gets where people
    # watch from (27. 11. 2026); the groups and accounts the same.
    access.split_network_rights()
    api.add_network_origin_to_keys()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """What happens at the start and at the shutdown of the app.

    The data collector is started here in the background. It runs alongside the web:
    while you click through pages, it asks Jellyfin every few seconds what is
    playing right now. Beside it Prowlarr's loop measures the indexers' status
    (it idles while Prowlarr is not connected; in the demo the made-up one).
    """
    # Writing the log to a file is switched on first, so that a possible error
    # while preparing the database gets into it too - that one is the most interesting.
    log_file = applog.setup()
    if log_file:
        log.info("the log is also written to a file: %s", log_file)
    warn_about_short_secret_key(config)
    if config.demo_mode:
        # The demo's made-up apps before anything else: from here on no client of the demo reaches the network.
        from .. import demo_apps

        demo_apps.load_fakes()

    db.init_db()
    log.info("database ready")
    # How many old logs to keep, also for the rotation by the size safeguard.
    # The log is switched on before the database, so it learns of it only now.
    applog.set_count(tasks.rotation_count())
    # The dashboard for everyone has a name since the version with My dashboard. An
    # existing one switched on without a name gets the one people knew it by.
    cards.fill_in_dashboard_name()
    _convert_rights()
    # The mail server's certificate is checked (25. 11. 2026): an SMTP set up before keeps sending unchecked, once.
    notifications.convert_smtp_verification()

    # The app's time zone into the process environment.
    #
    # Writing out times takes it by itself (formatting.zone()), but SQL splits
    # into days and hours - SQLite by the modifier 'localtime', PostgreSQL
    # by the connection's zone. Both read the zone of the OPERATING SYSTEM, not our
    # variable, so it has to be set in the environment before the first
    # connection opens.
    #
    # `tzset()` is unix only. On Windows the zone of a running process cannot
    # be switched, so the system's holds there - writing out times is right
    # either way, only the split into days goes by the machine. Production runs
    # on Linux, where all of it holds.
    app_zone = (db.get_setting("app_timezone", "") or "").strip()
    if app_zone:
        os.environ["TZ"] = app_zone
        if hasattr(time, "tzset"):
            time.tzset()
            log.info("application time zone: %s", app_zone)
        else:
            log.warning("the time zone %s cannot be set for the process on this system; "
                        "times are shown in it, the split into days in charts is not",
                        app_zone)

    # Into the log, so that the question "why does it say I am in a container"
    # needs no guessing. Two things are told apart: any container (decides
    # about backups) and our image (decides about the update).
    if config.in_docker or config.our_image:
        log.info("environment: container=%s, our image=%s",
                 config.in_docker, config.our_image)

    # In a container the backup folder is set by the app itself - see db.prefill_backup_folder().
    backup_folder = db.prefill_backup_folder()
    if backup_folder:
        log.info("in a container: backup folder set to %s", backup_folder)

    # A task the previous start left running (a restart in the middle of it) ends as failed - nothing runs yet.
    interrupted = scanner.close_interrupted_task_logs()
    if interrupted:
        log.info("closed %s task runs interrupted by the previous start", interrupted)
    # The backups those runs were writing stayed half-written, and old private files readable to everyone.
    await _tidy_files()

    # Items that should never have come into the library (a series instead of an episode).
    # Without it they would look vanished again at every synchronisation.
    phantoms = scanner.clean_up_phantoms()
    if phantoms:
        log.info("removed %s items from the library that do not belong there", phantoms)

    # Copies of the same movie in one library: after an upgrade they are marked at once,
    # not only after the first synchronisation. Otherwise it is a few queries that write nothing.
    scanner.recount_copies_after_sync()

    # Accounts from Jellyfin have no local password (24. 11. 2026): one an older version let be set is cleared, once.
    cleared = accounts.clear_jellyfin_passwords()
    if cleared:
        log.info("cleared the local passwords of %s accounts from Jellyfin", cleared)
    # Sign-in blocks stored under a whole IPv6 address move under their network, where the brake looks today.
    accounts.convert_block_keys()
    # Expired sign-in blocks tell nothing any more - the permanent ones stay.
    deleted = accounts.clean_up_blocks()
    if deleted:
        log.info("cleaned up %s stale sign-in blocks", deleted)
    # Sign-in sessions unused for longer than the cookie lives - the quick refresh cleans them too.
    ended = web_sessions.clean_up()
    if ended:
        log.info("cleaned up %s expired sign-in sessions", ended)

    background = []
    if config.demo_mode:
        # In the demo mode there is nothing to connect to. The collector would only
        # fill the log with errors and at start close the made-up playback that is
        # to be seen on the Overview.
        db.set_setting(collector.STATUS_KEY, "demo")
        log.info("demo mode: the collector is not started")
        # Made-up data age: they are made for the moment of the seed and the public
        # demo runs for weeks. Once a day they are made again, shifted
        # to today - otherwise a line is all that is left of "the last 30 days".
        from .. import demodata

        background.append(
            asyncio.create_task(demodata.keep_refreshing(), name="demo-refresh")
        )
        # And something always plays in Now playing - the made-up stream plays on.
        background.append(
            asyncio.create_task(demodata.keep_live_forever(), name="demo-live-stream")
        )
    else:
        background.append(
            asyncio.create_task(collector.run_forever(), name="collector")
        )
        background.append(
            asyncio.create_task(tasks.run_scheduler(), name="scheduler")
        )
    # In the demo too: the made-up Prowlarr answers it, after the seed's samples of the past (arr/prowlarr/demo.py).
    background.append(asyncio.create_task(prowlarr.watch_indexers_forever(), name="prowlarr-watch"))

    try:
        yield
    finally:
        # At shutdown the tasks are ended politely and waited for until they really end.
        for job in background:
            job.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        log.info("background tasks stopped")
        # The connection pool keeps connections to PostgreSQL open. When it is
        # not closed, they hang on the server until its own limit.
        db.close_pool()
