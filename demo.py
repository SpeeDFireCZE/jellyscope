"""The demo mode - runs Jellyscope with made-up data.

What it is for: clicking through the application before you connect it to
your Jellyfin. It needs no API key, no .env file, nothing.

    python demo.py

Then open http://127.0.0.1:8098

Everything is stored into data/demo.db. To start anew, delete that file.
Your real data (data/jellyscope.db) do not change by it in any way.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Setting the environment MUST come before importing the application - the
# configuration is loaded at import and does not change after that.
os.environ["DATABASE_PATH"] = "data/demo.db"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"   # broken on purpose
os.environ["JELLYFIN_API_KEY"] = "ukazkovy-rezim-bez-serveru"
# setdefault, not an assignment: the sign-in cookies are signed with this key
# and "ukazkovy-rezim" stands in the public source. At home it does not
# matter, a public demo server should set a key of its own.
os.environ.setdefault("SECRET_KEY", "ukazkovy-rezim")
# The demo mode: the collector does not start (there is nobody to ask)
# and the made-up "now playing" thus stays visible on the Overview.
os.environ["JELLYSCOPE_DEMO"] = "1"
# The address and the port can be overridden from the environment. The
# default 127.0.0.1 is on purpose: a demo on your own machine should not
# listen to the network. In a container it is a necessity the other way
# round, though - nobody from outside gets through to 127.0.0.1 inside the
# container, hence HOST=0.0.0.0 is set there.
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8098")

_ADDRESS = os.environ["HOST"]
_PORT = int(os.environ["PORT"])

import uvicorn  # noqa: E402

from jellyscope import db, demodata  # noqa: E402


def main() -> None:
    db.init_db()

    if demodata.already_seeded():
        print("The sample data is in the database already, only starting the server.")
    counts = demodata.prepare()
    if counts["items"]:
        print(f"  {counts['items']} titles, {counts['plays']} playbacks, "
              f"{counts['users']} users")

    print()
    print("=" * 58)
    print("  DEMO MODE - the data is made up")
    print()
    print(f"  Open in a browser:  http://{_ADDRESS}:{_PORT}")
    print()
    print(f"  Sign in:  {demodata.DEMO_USERNAME} / {demodata.DEMO_PASSWORD}")
    print()
    print("  Stop it with Ctrl+C")
    print("=" * 58)
    print()

    # In the demo mode there is no point bothering an unreachable Jellyfin
    # with requests every ten seconds.
    db.set_setting("poll_interval", "300")
    # And even less point in running the scheduled tasks - they have nothing to reach.
    db.set_setting("task_sync_enabled", "0")
    db.set_setting("task_recent_enabled", "0")

    uvicorn.run("jellyscope.web:app", host=_ADDRESS, port=_PORT,
                log_level="warning")


if __name__ == "__main__":
    main()
