"""Configuration of the application.

A rule worth keeping for a lifetime: **secrets do not belong in the code**.
API keys and passwords are read from the environment (the .env file), not
from a .py file. Thanks to that you can put the code on GitHub and give
nothing away.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from .dialect import write_private

log = logging.getLogger("jellyscope.config")

# The folder the whole project lives in (one level above this file).
# The root folder of the installation: .env and the data/ folder (holding
# the database and database.json with the choice of database) are looked
# for here.
#
# `JELLYSCOPE_HOME` can redirect it. That is no whim - without it the
# application cannot be tested properly: `DATABASE_PATH` **is overridden**
# by the choice stored in data/database.json, so a test that sets up its
# own database would end up in the live one anyway. And since it would find
# a finished schema there, it would look as if it passed.
#
# The order is deliberate: the stored choice wins over .env in the
# application (the user changes it in Settings and that has to work),
# whereas JELLYSCOPE_HOME switches the whole home - the stored choice
# included.
BASE_DIR = Path(
    os.environ.get("JELLYSCOPE_HOME") or Path(__file__).resolve().parent.parent
).resolve()


def _load_dotenv(path: Path) -> None:
    """Reads the .env file into environment variables.

    There is a library for it (python-dotenv), but it is fifteen lines of
    code and it is useful to see there is nothing magic about it: read the
    lines, skip the comments, split on "key=value".

    Environment variables already set win - a habit that lets you override
    something from the command line for a while.
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    """All the settings in one place.

    `frozen=True` means the object cannot change once created. That is the
    intent: the configuration is read once at start and is a constant from
    then on.
    """

    jellyfin_url: str
    jellyfin_api_key: str
    database_path: Path
    host: str
    port: int
    secret_key: str
    # Switch on behind a reverse proxy with HTTPS. The sign-in cookie is then
    # sent over an encrypted connection only - without it, it can be
    # overheard on the way.
    secure_cookies: bool
    # Whom to trust the X-Forwarded-* headers. Empty = nobody (direct
    # traffic). Behind a proxy set it to the proxy's address, typically
    # 127.0.0.1.
    forwarded_allow_ips: str
    # Demo mode (demo.py). The collector does not start - there is nobody to
    # ask, and it would only close the made-up playback that is meant to be
    # seen.
    demo_mode: bool
    # Does the application run in any container? It decides where the backup
    # folder is set the first time - see _in_container().
    in_docker: bool
    # And does it run from OUR image (the Dockerfile in this repository)?
    # Only there does "update by rebuilding the image" make sense; in someone
    # else's container the application can be installed from git, and
    # `git pull` works for it.
    our_image: bool


_cached: Config | None = None

# The shortest signing key that is taken for one: the generated one has 64 characters, a stored one is used from 32.
SHORTEST_SECRET_KEY = 32


def load_config(reload: bool = False) -> Config:
    """Returns the configuration. The second time just the one read before (cache)."""
    global _cached
    if _cached is not None and not reload:
        return _cached

    _load_dotenv(BASE_DIR / ".env")

    db_path = Path(os.environ.get("DATABASE_PATH", "data/jellyscope.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    _cached = Config(
        # rstrip("/") - so it works even when the user writes the address with a slash at the end
        jellyfin_url=os.environ.get("JELLYFIN_URL", "http://localhost:8096").rstrip("/"),
        jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY", "").strip(),
        database_path=db_path,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8097")),
        secret_key=os.environ.get("SECRET_KEY", "").strip() or _generated_secret_key(),
        secure_cookies=_flag("SECURE_COOKIES"),
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "").strip(),
        demo_mode=_flag("JELLYSCOPE_DEMO"),
        in_docker=_in_container(),
        our_image=_flag("JELLYSCOPE_DOCKER"),
    )
    return _cached


def warn_about_short_secret_key(config: Config) -> bool:
    """Says in the log that SECRET_KEY (from .env or the environment) is short. True = it warned.

    The key signs the sign-in cookie, and a short word is found from any cookie's signature offline - with it a
    cookie of a Jellyfin account (an administrator's too) can be signed without a password. It is still used:
    refusing to start or switching to another key would sign everybody out without saying why. Called at the
    start of the app after the log file is switched on, so the warning is in it at every start.
    """
    if len(config.secret_key) >= SHORTEST_SECRET_KEY:
        return False
    log.warning(
        "SECRET_KEY has only %s characters, so a sign-in cookie can be forged by guessing it. Set at least %s random characters (python3 -c \"import secrets; print(secrets.token_hex(32))\") and restart.",
        len(config.secret_key), SHORTEST_SECRET_KEY)
    return True


def _in_container() -> bool:
    """Does the application run in a container? In any, not necessarily ours.

    We ask in three ways, because each one fails somewhere on its own:

    * `JELLYSCOPE_DOCKER=1` is set by our Dockerfile - for our image it is
      a certainty, nothing is guessed,
    * `/.dockerenv` is created by Docker itself, so it catches someone
      else's image too,
    * `/run/.containerenv` is the same for Podman.

    The cgroup does not tell reliably: on cgroup v2 the file usually holds
    just "0::/" and no "docker" at all.
    """
    if _flag("JELLYSCOPE_DOCKER"):
        return True
    try:
        return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    except OSError:
        return False


def _generated_secret_key() -> str:
    """The signing key, when nobody set one in `.env`.

    This key **signs the sign-in cookie**. There used to be a fixed fallback
    value here - but that is the same in every copy of the source, so anyone
    who knows it signs their own cookie with somebody else's account and is
    inside as an admin. Without a password.

    So a random key is made instead and saved to the file `data/secret_key`.
    A random key kept in memory only would not do: it would differ after
    every restart and everybody would have to sign in again - and that is
    exactly the kind of nuisance that makes people reach for the unsafe
    solution.

    The file gets permissions 600 (readable by its owner only). On Windows
    `chmod` cannot do that; there the file is protected by access to the
    folder.
    """
    path = BASE_DIR / "data" / "secret_key"
    try:
        if path.is_file():
            stored = path.read_text(encoding="utf-8").strip()
            if len(stored) >= SHORTEST_SECRET_KEY:
                return stored

        new_key = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, new_key)     # 600 from the first moment (see dialect.write_private)
        log.warning(
            "SECRET_KEY was not set, so a random one was generated and "
            "stored in %s. Sign-in stays safe; set it in .env to "
            "manage the key yourself.", path)
        return new_key
    except OSError as exc:
        # The disk cannot be written (read-only, missing permissions). Better
        # a key in memory only - after a restart everybody signs in again,
        # but nobody can sign somebody else's sign-in.
        log.error("The signing key cannot be saved (%s). Using a temporary one; "
                  "everyone will have to sign in again after a restart.", exc)
        return secrets.token_hex(32)


def _flag(name: str) -> bool:
    """True or false from an environment variable.

    Takes "1", "true", "yes", "on" - people write them differently, and
    arguing with the user about the shape of the value makes no sense.
    """
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
