"""The Jellyscope launcher.

This is the file you run:  python run.py

It does nothing else than load the configuration, prepare the database and
hand the reins to uvicorn — the server that serves our web application.
"""

from __future__ import annotations

import os

import uvicorn

from jellyscope.config import load_config


def _outage_mode(config, error: Exception) -> int:
    """A substitute server that only explains why the application is not running.

    Apart from `main`, so it is plain at first sight that nothing else starts
    in this mode - no collector, no tasks. Only the page.
    """
    from jellyscope import outage

    print(f"A stand-in page runs at  http://{config.host}:{config.port}")
    print("Once the database can be reached again, restart the application.")
    uvicorn.run(outage.app(error), host=config.host, port=config.port,
                log_level="warning", use_colors=False)
    return 1


def main() -> int:
    # Everything the app creates is for its user only: the database holds the apps' keys and passwords readably,
    # the logs and backups the same. With the usual umask 022 they were readable to every account on the machine.
    os.umask(0o077)
    config = load_config()

    # We prepare the database already here, not only in web/start.py. Thanks
    # to that we can print right away what state the application is in - and
    # a possible error shows in the log at the start, not only at the first request.
    from jellyscope import accounts, applog, db

    # The log file first: this init_db is the one that converts an old database to English names, and
    # its summary is what README and update.sh send people to the log for. (The web's start calls
    # setup() again - it keeps the handler it has.)
    applog.setup()
    try:
        added = db.init_db()
    except db.DatabaseUnavailable as error:
        # Without the database nothing can be done. But ending means that
        # nothing stays in the container but a record in the log nobody went
        # to look at: the browser shows only "cannot connect". So the
        # application starts and answers every address with one page that
        # says what happened and what to do about it - see jellyscope/outage.py.
        print()
        print(str(error))
        print()
        return _outage_mode(config, error)
    if added:
        print(f"Columns added to the database: {', '.join(added)}")

    # The demo mode in a container: the image runs this file, not demo.py, so
    # the demo would start empty - and because nothing is stored in it, not
    # even an administrator could be created in it. So the data are prepared
    # here. It does not touch a regular installation: without JELLYSCOPE_DEMO
    # nothing happens at all.
    demo = None
    if config.demo_mode:
        from jellyscope import demodata

        demo = demodata.prepare(quiet=True)

    database = db.database_config()
    jellyfin_url, jellyfin_key = db.jellyfin_connection()

    # The version right on the first line: in a container `docker compose logs`
    # is the only place where it can be found out whether what a person copied
    # a moment ago is running - the image does not rebuild by itself.
    from jellyscope import __version__

    print(f"Jellyscope {__version__} starts at  http://{config.host}:{config.port}")
    print(f"Database:               {database.describe()}")

    if demo is not None:
        print("Demo:                   " + (
            f"{demo['items']} titles, {demo['plays']} playbacks prepared"
            if demo["items"] else "the made-up data is in the database already")
            + " (nothing is stored in it)")

    # Neither Jellyfin's address nor the API key are in .env - they are set in
    # the application (Settings -> Connection to Jellyfin). An empty connection
    # is therefore NOT a reason to refuse the start: the user needs to get into
    # the interface to fill it in. We only point it out.
    if jellyfin_url and jellyfin_key:
        print(f"Jellyfin:               {jellyfin_url}")
    else:
        print("Jellyfin:               not set "
              "- fill it in under Settings → Jellyfin")

    if accounts.any_exists():
        print(f"Accounts:               {accounts.count()} (sign in with yours)")
    else:
        print("Accounts:               none "
              "- the first visit creates the administrator")

    # Behind a reverse proxy the real address of the client is needed and
    # whether the connection came over HTTPS. The proxy sends it in the
    # X-Forwarded-* headers, but they may be trusted only when we know they
    # came from our proxy - otherwise anybody could forge them.
    forwarded = config.forwarded_allow_ips
    if forwarded:
        print(f"Trusted proxies:        {forwarded}")
    if config.secure_cookies:
        print("Cookies:                over HTTPS only (SECURE_COOKIES)")

    uvicorn.run(
        "jellyscope.web:app",
        host=config.host,
        port=config.port,
        # reload=True would be handy during development, but it breaks the data
        # collector running in the background (it starts the application
        # twice), so we leave it off here.
        reload=False,
        log_level="info",
        proxy_headers=bool(forwarded),
        forwarded_allow_ips=forwarded or None,
        # Without colour escape sequences - in supervisord's log they would only get in the way.
        use_colors=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
