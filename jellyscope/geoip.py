"""The geographic location of public addresses from the GeoLite2 database.

What it is for: showing on the Network page where people watch from when they
are not watching from the home network. An address from the home network
marks no place - `192.168.1.5` is the same in Prague and in Sydney -, so only
public addresses are looked up.

Three things worth explaining:

**The database is a file, not a service.** Asking some web service about
every address at run time would mean sending the viewers' addresses to
someone else's server - and that is exactly what this application does not
do. GeoLite2 is one file (`.mmdb`) that lies in `data/` and answers offline.

**The library comes with the installation.** `maxminddb` is in requirements.txt
(it used to be optional, and the map stayed empty for whoever missed the note).
The code still copes without it: the section then simply does not show.

**Precision.** GeoLite2 is free and it shows: the city fits rather for fixed
lines, for mobile networks it points even to the middle of the country. For
the question "does somebody watch from abroad?" that is enough, for finding
people it is not - and so it should stay.

Data © MaxMind, GeoLite2 (CC BY-SA 4.0). The application does not download it
by itself; the admin downloads the file with a button on the Network page.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import config, db, i18n
from datetime import UTC

log = logging.getLogger("jellyscope.geoip")

# The file name is fixed. Setting a path would mean another field in
# Settings for something the application downloads itself anyway.
FILE_NAME = "GeoLite2-City.mmdb"

# Where it is downloaded from. It is a mirror that publishes the MaxMind
# databases as a release on GitHub - MaxMind itself needs an account and a
# key, which would turn one button into a form with three fields.
SOURCE_URL = ("https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/"
              + FILE_NAME)

# The ceiling of a download. The file has about 65 MB; an answer without an end (a mirror gone wrong) must not
# fill the disk the application's database lies on too (like detailed_map.MAX_BYTES).
MAX_BYTES = 200 * 1024 * 1024

# A downloaded file that does not open as a MaxMind database of cities (a translation key - one string).
NOT_A_DATABASE = "The downloaded file is not a GeoLite2 City database."


def database_path() -> Path:
    """Where the file lies. Next to the application's database, that is in data/."""
    return config.load_config().database_path.parent / FILE_NAME


def is_available() -> bool:
    """Can anything be looked up at all? Both have to be there - the library and the file."""
    return library_installed() and database_path().is_file()


def library_installed() -> bool:
    try:
        import maxminddb  # noqa: F401
    except ImportError:
        return False
    return True


def database_size() -> int:
    path = database_path()
    return path.stat().st_size if path.is_file() else 0


def database_age() -> str:
    """When the file was last downloaded. Empty when it is not here."""
    path = database_path()
    if not path.is_file():
        return ""
    from datetime import datetime
    time = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return time.strftime(db.TIME_FORMAT)


_reader: Any = None


def _open() -> Any:
    """The open database file. It is kept open - it is reading from the disk
    through mmap, so opening it again and again would be pointless.
    """
    global _reader
    if _reader is None:
        import maxminddb
        _reader = maxminddb.open_database(str(database_path()))
    return _reader


def forget() -> None:
    """Closes the file. Called after a new version is downloaded."""
    global _reader
    if _reader is not None:
        try:
            _reader.close()
        except Exception:  # noqa: BLE001 - closing must not bring anything down
            pass
        _reader = None


def _text(value: Any, language: str = "en") -> str:
    """Picks one name out of a name that comes as a dictionary of languages."""
    if isinstance(value, dict):
        return str(value.get(language) or value.get("en") or "")
    return str(value or "")


def lookup(address: str) -> dict[str, Any] | None:
    """Where the address is. None when it cannot be found out.

    Returns the geographic coordinates, the city, the country and its
    two-letter code. When the database does not know the address (a new
    range, a VPN, a satellite), it returns None - that is no error, nobody
    just knows it about that address.
    """
    if not is_available():
        return None
    import maxminddb

    try:
        record = _open().get(address)
    except (ValueError, OSError, maxminddb.InvalidDatabaseError) as error:
        # An invalid address or a damaged file (a file that is no database at all is InvalidDatabaseError,
        # a RuntimeError). The map is an extra, so nothing is brought down because of it - the page shows
        # without it, with the button to download it again.
        log.debug("address %s could not be located: %s", address, error)
        return None

    if not isinstance(record, dict):
        return None

    location = record.get("location") or {}
    if location.get("latitude") is None or location.get("longitude") is None:
        return None

    country = record.get("country") or record.get("registered_country") or {}
    city = record.get("city") or {}
    return {
        "lat": float(location["latitude"]),
        "lon": float(location["longitude"]),
        "city": _text(city.get("names")),
        "country": _text(country.get("names")),
        "code": str(country.get("iso_code") or ""),
    }


def _check_database(path: Path) -> None:
    """Raises when the file is no MaxMind database of cities - before it replaces the working one."""
    import maxminddb

    try:
        reader = maxminddb.open_database(str(path))
    except (ValueError, OSError, maxminddb.InvalidDatabaseError) as error:
        raise i18n.TranslatableError(NOT_A_DATABASE) from error
    try:
        kind = str(reader.metadata().database_type or "")
    finally:
        reader.close()
    if "City" not in kind:
        raise i18n.TranslatableError(NOT_A_DATABASE)


async def download() -> dict[str, Any]:
    """Downloads the database from SOURCE_URL to data/.

    It is the only place where the application reaches elsewhere than to
    Jellyfin, and it happens **only on a click**, never by itself. The file
    has over 60 MB, so it is written in pieces - not whole into memory.
    """
    import httpx

    target = database_path()
    partial = target.with_suffix(".stahuje")
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", SOURCE_URL) as response:
                response.raise_for_status()
                written = 0
                with partial.open("wb") as file:
                    async for chunk in response.aiter_bytes(1 << 20):
                        written += len(chunk)
                        if written > MAX_BYTES:
                            raise i18n.TranslatableError("{file} is larger than {mb} MB", file=FILE_NAME,
                                                         mb=MAX_BYTES // (1024 * 1024))
                        file.write(chunk)
        # Anything with 200 used to replace a working database: a page of a captive portal, a damaged file.
        _check_database(partial)
    except Exception as error:  # noqa: BLE001 - the network fails in many ways
        partial.unlink(missing_ok=True)
        log.warning("the GeoLite2 database could not be downloaded: %s", error)
        return {"status": "error", "message": i18n.error_text(error)}

    # Renaming only at the end: were the download interrupted, the old
    # database stays valid instead of half of the new one.
    forget()
    partial.replace(target)
    log.info("the GeoLite2 database has been downloaded (%s MB)", round(database_size() / 1e6, 1))
    return {"status": "ok", "byte_count": database_size()}
