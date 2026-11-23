"""Reading the logs for the interface - and one file of our own, so there always is one.

An administrator who wanted to know why the sync failed had to get onto the
server through SSH so far. This module makes the log available right in
Settings.

## Where the log comes from

The folder `data/logs/` holds what the process manager caught:

  * **supervisord** writes `out.log` (the standard output) and `err.log`
    (the error output) there - log messages go to the latter,
  * **systemd** writes nothing there, it sends everything to the journal.

So the viewer offers every file it finds in the folder, plus `jellyscope.log`,
which the application writes itself. That one is here exactly because of
systemd installations: without it the section would stay empty for them and
point to `journalctl`, which a browser cannot reach anyway.

The output to the standard output stays unchanged, so whoever is used to
`journalctl -u jellyscope -f` loses nothing.

## Secrets do not get into the browser

The log is the only place in the application where an API key or a database
password could show up - all sorts of things get into an error message.
Before a line is sent to the page, it goes through `_mask()`, which replaces
the known secret values with asterisks. It is masked **when reading**, not
when writing: masking when writing would mean reading the current key from
the database for every line - and writing to the log while reading from the
database is the road to an endless loop.
"""

from __future__ import annotations

import functools
import logging
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import i18n
from .config import BASE_DIR

log = logging.getLogger("jellyscope.applog")

# The logs are rotated by the task Log rotation (how often and how many old
# ones to keep is in Tasks). The size ceiling is a safeguard between
# rotations: without it, a log on a long-running server would quietly fill
# the disk - and a backup that does not fit on the disk does more harm than good.
MAX_BYTES = 20 * 1024 * 1024
DEFAULT_COUNT = 4
MAX_COUNT = 100
DEFAULT_DAYS = 7
MAX_DAYS = 365
COUNT_KEY = "logrotate_count"
DAYS_KEY = "logrotate_days"
# When it rotated last. The underscore: an operational record, it does not belong in the log.
LAST_ROTATION_KEY = "_logrotate_last"

# How many lines can be shown at once. The ceiling is here because a file can
# have two megabytes, and sending all of them to the page would only flood it.
MAX_LINES = 2000
DEFAULT_LINES = 200

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_handler: RotatingFileHandler | None = None


# The application's own file. The others (out.log, err.log) are created by
# the process manager; we only read those.
OWN_NAME = "jellyscope.log"
# Every request with an existing API key: when, the result, from where, whose
# key and where to. A file of its own, not a line in the application's log -
# Grafana asks every few seconds and everything else would get lost in it.
API_NAME = "api.log"
# Both are rotated by the task Log rotation; out.log and err.log belong to the process manager.
OWN_FILES = (OWN_NAME, API_NAME)

# Labels, so the interface tells whose file is which. Whoever deployed with
# supervisord would not know otherwise why there are three files.
LABELS = {
    OWN_NAME: "written by Jellyscope",
    "out.log": "standard output (supervisord)",
    "err.log": "error output (supervisord)",
}

# The address in the API log: a longer one is cut (a query can be long just
# because somebody sent it that way).
PATH_CHARS = 300
# How many lines of the API log are gone through at most when filtering.
API_LINES_SCANNED = 20000


def log_dir() -> Path:
    """The folder with the logs. It follows JELLYSCOPE_HOME like everything else."""
    return BASE_DIR / "data" / "logs"


def log_path() -> Path:
    """The file the application writes itself."""
    return log_dir() / OWN_NAME


def available_files() -> list[dict[str, Any]]:
    """Logs that really lie in the folder - the basis for the switch.

    Only files are listed, not rotated backups (`out.log.1`): there are
    usually five of them, and in the switch they would drown out what matters.
    """
    folder = log_dir()
    if not folder.is_dir():
        return []

    found = []
    try:
        for path in sorted(folder.glob("*.log")):
            if path.name == API_NAME:  # it has a view of its own with filters
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            found.append({
                "name": path.name,
                "label": LABELS.get(path.name, ""),
                "size_bytes": size,
            })
    except OSError:
        return []

    # The own file first - it has the most readable format and is always at hand.
    found.sort(key=lambda s: (s["name"] != OWN_NAME, s["name"]))
    return found


_BACKUP_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.log\.(\d{1,3})$")


def _safe_path(name: str, with_backups: bool = False) -> Path | None:
    """Turns a name from the form into a file in the folder with the logs.

    The name comes from the browser, so it must not be treated as a path -
    `../../.env` is a "name" too. So nothing but a bare file name is taken,
    and the result is checked against the list of what really is in the
    folder. `with_backups` lets rotated backups through too
    (`jellyscope.log.2`) - for downloading only.
    """
    if not name or name != Path(name).name:
        return None
    if not (name.endswith(".log") or (with_backups and _BACKUP_NAME.match(name))):
        return None
    path = log_dir() / name
    if not path.is_file():
        return None
    return path


def backups(name: str) -> list[dict[str, Any]]:
    """The rotated backups of one log (`api.log.1`, `.2` …), the newest first.

    They are shown so they can be downloaded before the rotation deletes them.
    """
    folder = log_dir()
    if not name or name != Path(name).name or not folder.is_dir():
        return []
    found = []
    for path in folder.glob(f"{name}.*"):
        match = _BACKUP_NAME.match(path.name)
        if not match or path.name[:-len(match.group(1)) - 1] != name:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append({"name": path.name, "number": int(match.group(1)),
                      "size_bytes": stat.st_size,
                      "changed": datetime.fromtimestamp(stat.st_mtime)
                      .strftime("%d.%m.%Y %H:%M")})
    return sorted(found, key=lambda z: z["number"])


def downloadable(name: str) -> Path | None:
    """A file (a backup too) that may be downloaded. None = there is no such one."""
    return _safe_path(name, with_backups=True)


def masked_lines(path: Path):
    """The log's content line by line, with the secret values replaced by asterisks.

    A downloaded file is the same as the listing on the page: what must not be
    shown must not leave either. It is read line by line, not whole into memory.
    """
    secrets = _secrets()
    with path.open("rb") as file:
        for line in file:
            yield _mask(line.decode("utf-8", errors="replace"),
                        secrets).encode("utf-8")


# ---------------------------------------------------------------------------
# The language of the log
# ---------------------------------------------------------------------------
#
# The log file is written in English, always (the user, 21. 11. 2026): it is what gets downloaded,
# pasted into a bug report and searched for on the internet. The Log page shows it in the reader's
# language - `read_lines` recognises each line of the application by its English template
# (translations/log/en.json), takes the values out of it and puts them into the reader's
# translation (`translate_line`). Lines of other libraries and tracebacks stay as they are.

# The names from the interface among a message's values (i18n.UiName at the call): template →
# positions. A guard in tests/test_log_language.py compares it with the calls in the code.
UI_NAME_VALUES: dict[str, tuple[int, ...]] = {"scheduled task: %s": (0,)}

# The values that carry a caught error (a log call inside `except ... as error` that hands `error` on):
# template → positions. Only there is a value read as a message of the interface ("Could not connect to
# Jellyfin: …") and translated - any other value is data (a title, an account, a setting) and stays as it
# is. The same guard compares it with the code; a new such log call is to be added here.
ERROR_VALUES: dict[str, tuple[int, ...]] = {
    "could not read Seerr's public settings: %s": (0,),
    '%s sync failed: %s': (1,),
    'address %s could not be located: %s': (1,),
    'aligning the imported history failed: %s': (0,),
    'backup %s could not be deleted: %s': (1,),
    'books sync failed: %s': (0,),
    'collector: %s': (0,),
    'could not download poster %s from Seerr: %s': (1,),
    'could not download the poster of %s %s from %s: %s': (3,),
    'could not download the artist list of %s, the albums keep the names they came with: %s': (1,),
    'could not download the author list of %s, the books keep the names they came with: %s': (1,),
    'could not download the title list of %s, will retry next time: %s': (1,),
    'could not get the free space from Jellyfin: %s': (0,),
    'could not download the editions of %s, the books keep the editions they came with: %s': (1,),
    'could not load the series from Jellyfin, the matching of %s titles stays as it was: %s': (1,),
    'could not look up the title of %s %s: %s': (2,),
    'could not process the titles of %s, the links will be filled in next time: %s: %s': (2,),
    'could not revoke the sign-in token: %s': (0,),
    'could not start quick connect: %s': (0,),
    'could not verify the Jellyfin sign-in: %s': (0,),
    'Jellyfin does not know %s (%s)': (1,),
    'languages could not be filled in from Jellyfin: %s': (0,),
    'lookup by tmdb failed: %s': (0,),
    'marking the copies of movies failed: %s': (0,),
    'music sync failed: %s': (0,),
    'pg_dump cannot be used (%s), falling back to the built-in export': (0,),
    'prowlarr: the history cannot be read reliably: %s': (0,),
    'quick connect: the status request failed: %s': (0,),
    'the API log could not be created (%s): %s': (1,),
    'the command to refresh downloads in %s failed: %s': (1,),
    'the comparison with GitHub failed: %s': (0,),
    'the database could not be rewritten: %s': (0,),
    'the detailed map could not be downloaded: %s': (0,),
    'the dynamic range could not be filled in from Jellyfin: %s': (0,),
    'the GeoLite2 database could not be downloaded: %s': (0,),
    'the item count could not be determined: %s': (0,),
    'the Jellyfin lookup failed: %s': (0,),
    'the Jellyfin version could not be detected while saving: %s': (0,),
    'the log file could not be created (%s): %s': (1,),
    'the poster of title %s could not be downloaded from %s: %s': (2,),
    'The signing key cannot be saved (%s). Using a temporary one; everyone will have to sign in again after a restart.': (0,),
    'the version check could not be made: %s': (0,),
    'translation %s cannot be read: %s': (1,),
}

# A placeholder of a log template; "%%" is a per cent sign, "%(name)s" a named one.
_PLACEHOLDER = re.compile(r"%(?:%|\((\w+)\)[sdrf]|0?\d*(?:\.\d+)?[sdrf])")
# A line of the application: the time, the level, the logger "jellyscope…", the message.
_APP_LINE = re.compile(r"^(\S+ \S+ \S+\s+jellyscope[\w.]*: )(.*)$", re.S)
# A placeholder of an interface template ("{name}").
_UI_PLACEHOLDER = re.compile(r"\{(\w+)\}")
# A sentence of the interface: words and an ending - not a status like "error".
_SENTENCE_END = (".", "?", "!", ")")


def fixed_text(template: str) -> str:
    """The text of a log template without its placeholders - what a line must contain to match it."""
    return _PLACEHOLDER.sub(lambda m: "%" if m.group(0) == "%%" else "", template)


def _log_pattern(template: str) -> tuple[re.Pattern[str], list[str], list[str | None]]:
    """A template as a pattern that takes the values out of a message, the kinds of its values and their
    names (None for a positional one)."""
    parts, kinds, names, position = [], [], [], 0
    for found in _PLACEHOLDER.finditer(template):
        parts.append(re.escape(template[position:found.start()]))
        token = found.group(0)
        if token == "%%":
            parts.append("%")
        else:
            kinds.append(token[-1])
            names.append(found.group(1))
            parts.append({"d": r"(-?\d+)", "f": r"(-?\d+(?:\.\d+)?)"}.get(token[-1], "(.*?)"))
        position = found.end()
    parts.append(re.escape(template[position:]))
    return re.compile("".join(parts), re.S), kinds, names


def _lead(template: str, placeholder: re.Pattern[str]) -> str:
    """The first three letters of a template's fixed beginning - "" when it starts with a value."""
    lead = placeholder.split(template, 1)[0]
    return lead[:3] if len(lead) >= 3 else ""


# The index of the log's templates, rebuilt when the dictionary object changes (a translation update).
# Always replaced whole, never emptied and filled in place: a concurrent reader keeps the old one intact.
_TEMPLATE_INDEX: dict[str, Any] = {}


def _log_index() -> dict[str, Any]:
    """The patterns of all log templates, the longest fixed text first, by the first letters of the
    message (a template starting with a value goes to every message). Built once per dictionary."""
    index = _TEMPLATE_INDEX
    if index.get("source") is i18n.LOG_SOURCE:
        return index
    buckets: dict[str, list[tuple[int, str, re.Pattern[str], list[str], list[str | None]]]] = {}
    for template in i18n.LOG_SOURCE:
        fixed = fixed_text(template)
        if not fixed.strip():
            continue                # a template of values only would match anything
        pattern, kinds, names = _log_pattern(template)
        buckets.setdefault(_lead(template, _PLACEHOLDER), []).append((len(fixed), template, pattern, kinds, names))
    for entries in buckets.values():
        entries.sort(key=lambda entry: -entry[0])
    return _swap_index({"source": i18n.LOG_SOURCE, "buckets": buckets})


def _swap_index(index: dict[str, Any]) -> dict[str, Any]:
    global _TEMPLATE_INDEX
    _TEMPLATE_INDEX = index
    return index


def _match(message: str) -> tuple[str, list[str], list[str], list[str | None]] | None:
    buckets = _log_index()["buckets"]
    candidates = sorted(buckets.get(message[:3], []) + buckets.get("", []), key=lambda entry: -entry[0])
    for _, template, pattern, kinds, names in candidates:
        found = pattern.fullmatch(message)
        if found:
            return template, list(found.groups()), kinds, names
    return None


def match_template(message: str) -> tuple[str, list[str]] | None:
    """The log template a message was made from and its values - or None (a foreign message)."""
    found = _match(message)
    return (found[0], found[1]) if found else None


_UI_INDEX: tuple[Any, dict[str, list[tuple[str, str, str, re.Pattern[str]]]]] = (None, {})


def _ui_index() -> dict[str, list[tuple[str, str, str, re.Pattern[str]]]]:
    """The interface's templates with placeholders as patterns, by the first letters of their text:
    (key, English text, the longest fixed piece, pattern). Replaced whole like the log's index."""
    global _UI_INDEX
    source, index = _UI_INDEX
    if source is i18n.SOURCE:
        return index
    buckets: dict[str, list[tuple[str, str, str, re.Pattern[str]]]] = {}
    for key, text in i18n.SOURCE.items():
        names = _UI_PLACEHOLDER.findall(text)
        pieces = _UI_PLACEHOLDER.split(text)[::2]
        if not names or len(set(names)) != len(names) or not "".join(pieces).strip():
            continue
        unit = key.rsplit("_", 1)[0] if i18n.is_plural(key.rsplit("_", 1)[0]) else key
        parts, position = [], 0
        for found in _UI_PLACEHOLDER.finditer(text):
            parts.append(re.escape(text[position:found.start()]))
            parts.append(f"(?P<{found.group(1)}>.*?)")
            position = found.end()
        parts.append(re.escape(text[position:]))
        longest = max(pieces, key=len)
        buckets.setdefault(_lead(text, _UI_PLACEHOLDER), []).append(
            (unit, text, longest, re.compile("".join(parts), re.S)))
    for entries in buckets.values():
        entries.sort(key=lambda entry: -len(_UI_PLACEHOLDER.sub("", entry[1])))
    _UI_INDEX = (i18n.SOURCE, buckets)
    return buckets


@functools.lru_cache(maxsize=4096)
def _translated_value(value: str, language: str, source_id: int, depth: int = 0) -> str:
    """A caught error among a line's values in the reader's language - when it is a message of the
    interface. Remembered: the collector writes the same error at every attempt."""
    if depth > 2 or " " not in value.strip():
        return value
    if value in i18n.SOURCE and value.rstrip().endswith(_SENTENCE_END):
        return i18n.translate(value, language)
    buckets = _ui_index()
    candidates = sorted(buckets.get(value[:3], []) + buckets.get("", []),
                        key=lambda entry: -len(_UI_PLACEHOLDER.sub("", entry[1])))
    for key, _text, longest, pattern in candidates:
        if longest not in value:
            continue
        found = pattern.fullmatch(value)
        if not found:
            continue
        values: dict[str, Any] = {name: _translated_value(part, language, source_id, depth + 1)
                                  for name, part in found.groupdict().items()}
        if i18n.is_plural(key):
            try:
                values["n"] = int(str(values.get("n", "")).replace(",", "").replace("\u202f", "").replace(" ", ""))
            except ValueError:
                return value
        try:
            return i18n.translate_with(key, language, **values)
        except (KeyError, IndexError, ValueError, TypeError):
            return value
    return value


def translate_line(line: str, language: str) -> str:
    """A line of the log in a language: a line of the application whose template has a translation
    gets the translation with its values; any other line comes back as it is. A value is translated
    only where the code puts a name from the interface (UI_NAME_VALUES) or a caught error (ERROR_VALUES)."""
    if language == i18n.SOURCE_LANGUAGE or language not in i18n.LOG_TRANSLATIONS:
        return line
    head = _APP_LINE.match(line)
    if not head:
        return line
    found = _match(head.group(2))
    if found is None:
        return line
    template, values, kinds, names = found
    target = i18n.LOG_TRANSLATIONS[language].get(template)
    if not target:
        return line
    ui_names, errors = UI_NAME_VALUES.get(template, ()), ERROR_VALUES.get(template, ())
    shown = []
    for position, (kind, value) in enumerate(zip(kinds, values, strict=True)):
        if kind != "r" and position in ui_names:
            value = i18n.translate(value, language)
        elif kind != "r" and position in errors:
            value = _translated_value(value, language, id(i18n.SOURCE))
        shown.append(value)
    # The translation's own placeholders take the values as text, a named one by its name.
    as_text = _PLACEHOLDER.sub(lambda m: "%%" if m.group(0) == "%%" else
                               (f"%({m.group(1)})s" if m.group(1) else "%s"), target)
    try:
        text = as_text % (dict(zip(names, shown)) if any(names) else tuple(shown))
    except (TypeError, ValueError, KeyError):
        return line
    return head.group(1) + text


def setup() -> Path | None:
    """Attaches the writing to a file. Called once at the start of the application.

    When the file cannot be created (read-only, missing permissions), the
    application must not fail because of it - logging is an aid, not a
    condition of running. We say so aloud into the other logs and go on.
    """
    global _handler
    if _handler is not None:
        return Path(_handler.baseFilename)

    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=DEFAULT_COUNT, encoding="utf-8")
    except OSError as exc:
        log.warning("the log file could not be created (%s): %s", path, exc)
        return None

    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(logging.INFO)

    # We hang on the root logger, not just on "jellyscope". Thanks to that the
    # messages of uvicorn get into the file too - and those are the ones that
    # say why the server did not come up.
    logging.getLogger().addHandler(handler)

    # Without this the file would hold only warnings and errors. The default
    # threshold of a logger is WARNING, so messages like
    # "synchronizace hotova, 12 nových" would get nowhere - and exactly those
    # are what people look for in a log most often.
    #
    # The threshold is raised for our logger only, not for the root one: httpx
    # and the like log every single request at INFO and would flood the log.
    logging.getLogger("jellyscope").setLevel(logging.INFO)

    # The HTTP client logs every request including the WHOLE address - and the
    # address holds the bot's token for Telegram and the whole webhook for
    # Discord. In normal running that message is thrown away (the root logger
    # is at WARNING), but it is enough to start the server with
    # --log-level debug and the token is in the file. So the threshold is set
    # fixed, instead of relying on the default value.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _handler = handler
    return path


# ---------------------------------------------------------------------------
# The API log
# ---------------------------------------------------------------------------
#
# A line goes straight to its own file handler, not through a logger: it
# belongs only in api.log (not in the application's log nor on the standard
# output), and it is a record for the administrator, not diagnostics - no
# setting of logging levels nor logging.disable() may silence it. The file is
# created only with the first write, in tests and scripts that do not call
# setup() too.
_api_handler: RotatingFileHandler | None = None


def _api_ready() -> RotatingFileHandler | None:
    global _api_handler
    if _api_handler is None:
        path = log_dir() / API_NAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _api_handler = RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=DEFAULT_COUNT, encoding="utf-8")
        except OSError as exc:
            log.warning("the API log could not be created (%s): %s", path, exc)
            return None
        _api_handler.setFormatter(logging.Formatter("%(message)s"))
    return _api_handler


def _field(value: Any) -> str:
    """A value for a line of the API log: without the separator and without line ends."""
    text = "".join(z if z.isprintable() else " " for z in str(value or ""))
    return text.replace("|", "/").strip() or "-"


def write_api(key_id: int, key: str, owner: str, path: str, ip: str,
              result: int) -> None:
    """Writes one request with an API key to api.log.

    The line: `when | result | IP | #id key name | owner | address`.
    The owner "-" is a root key (without an account). Read back in read_api().
    """
    handler = _api_ready()
    if handler is None:
        return
    when = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = " | ".join((when, str(int(result)), _field(ip),
                       f"#{int(key_id)} {_field(key)}", _field(owner),
                       _field(path)[:PATH_CHARS]))
    # handle(): the lock, the safety rotation by size and the write - without
    # the logger and its levels.
    handler.handle(logging.LogRecord("jellyscope.api_log", logging.INFO, __file__,
                                     0, line, None, None))


def _parse_api(line: str) -> dict[str, Any] | None:
    parts = line.rstrip("\n").split(" | ", 5)
    if len(parts) != 6:
        return None
    when, result, ip, key, owner, path = parts
    match = re.match(r"^#(\d+) ?(.*)$", key)
    try:
        number = int(result)
    except ValueError:
        return None
    return {"at": when, "result": number, "ip": "" if ip == "-" else ip,
            "key_id": int(match.group(1)) if match else None,
            "key": match.group(2) if match else key,
            "owner": "" if owner == "-" else owner, "path": path}


def read_api(limit: int = DEFAULT_LINES, account: str = "", key: Any = None,
             ip: str = "") -> dict[str, Any]:
    """The last `limit` requests from api.log, the newest first.

    Filters: `account` = the key's owner ("-" = a root key), `key` = the
    key's id, `ip` = a part of the address. The end of the file is gone
    through (API_LINES_SCANNED lines), not the whole - the older part is in
    the backups to download.
    """
    limit = max(10, min(MAX_LINES, limit))
    path = log_dir() / API_NAME
    result: dict[str, Any] = {"rows": [], "name": API_NAME, "path": str(path),
                              "exists": path.is_file(), "size_bytes": 0}
    if not result["exists"]:
        return result
    try:
        lines = _tail(path, API_LINES_SCANNED)
        result["size_bytes"] = path.stat().st_size
    except OSError:
        return result
    try:
        key_id = int(key) if key not in (None, "") else None
    except (TypeError, ValueError):
        key_id = None
    chosen = []
    for line in lines:
        record = _parse_api(line)
        if record is None:
            continue
        if account and (record["owner"] or "-") != account:
            continue
        if key_id is not None and record["key_id"] != key_id:
            continue
        if ip and ip.strip() not in record["ip"]:
            continue
        chosen.append(record)
    result["rows"] = list(reversed(chosen[-limit:]))
    return result


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

def set_count(count: int) -> None:
    """How many old logs to keep - for the safety rotation by size too."""
    count = max(1, min(MAX_COUNT, int(count)))
    for handler in (_handler, _api_ready()):
        if handler is not None:
            handler.backupCount = count


def rotate(count: int) -> dict[str, Any]:
    """Rotates the application's and the API's logs: the current file becomes the backup .1.

    The older backups move on (.1 → .2 …), and what is over `count` is
    deleted. The process manager's files (out.log, err.log) are not rotated -
    they belong to it. Returns what was rotated and what failed.
    """
    count = max(1, min(MAX_COUNT, int(count)))
    set_count(count)
    rotated, errors = [], []
    for name, handler in ((OWN_NAME, _handler), (API_NAME, _api_ready())):
        if handler is None:
            continue
        try:
            handler.doRollover()
            rotated.append(name)
        except OSError as exc:
            # Typically Windows: another process holds the file open.
            errors.append(f"{name}: {exc}")
        for backup in backups(name):
            if backup["number"] > count:
                try:
                    (log_dir() / backup["name"]).unlink()
                except OSError as exc:
                    errors.append(f"{backup['name']}: {exc}")
    return {"rotated": rotated, "errors": errors}


def _secrets() -> list[str]:
    """Values that must not appear in the listing.

    It is read with every view of the log, not kept in stock: the key can be
    changed in Settings at any time, and masking the old value would be no use.
    """
    from . import db

    values: list[str] = []
    try:
        _, api_key = db.jellyfin_connection()
        if api_key:
            values.append(api_key)
        database = db.database_config()
        if getattr(database, "password", ""):
            values.append(database.password)
        # Credentials for the notifications. The Telegram token is part of
        # the address, so it gets into a message even without anybody writing
        # it there - see notifications.without_secrets().
        from . import notifications

        values += [db.get_setting(key, "") for key in notifications.SECRET_KEYS]
    except Exception:  # noqa: BLE001
        # An unreachable database must not prevent showing the log - exactly
        # that is where it will most likely say why it cannot be reached.
        pass
    return [h for h in values if h and len(h) >= 4]


# A key can appear in a message in the shape "api_key=xxx" or
# "X-Emby-Token: xxx" as well, that is with a value we do not have in the
# settings (say a typo when entering it). Hence a general pattern too.
_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[=:]\s*)(\S+)", re.I),
    re.compile(r"(token\s*[=:\"']+\s*)([^\s\"'&]+)", re.I),
    re.compile(r"(password\s*[=:]\s*)(\S+)", re.I),
    # A password inside a connection string: postgres://user:password@host
    re.compile(r"(://[^:/\s]+:)([^@\s]+)(@)"),
]


def _mask(text: str, secrets: list[str]) -> str:
    for value in secrets:
        text = text.replace(value, "***")
    for pattern in _PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1***\3", text)
        else:
            text = pattern.sub(r"\1***", text)
    return text


def _level(line: str) -> str:
    """Takes the level out of a line, so it can be coloured and filtered in the listing."""
    for level in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
        if f" {level} " in line or f" {level:<7} " in line:
            return level
    return ""


def read_lines(name: str = "", limit: int = DEFAULT_LINES,
               level: str = "", language: str | None = None) -> dict[str, Any]:
    """The last `limit` lines of the chosen log, ready to be shown - in `language` (the reader's when None).

    Secrets are masked first, then each line of the application is translated (`translate_line`).

    We read from the end of the file: a log makes sense read backwards, the
    last line is the one a person comes here for. The whole file is not read
    into memory - for a two-megabyte file that would be a pointless waste.
    """
    limit = max(10, min(MAX_LINES, limit))
    files = available_files()

    if not name and files:
        name = files[0]["name"]
    path = _safe_path(name)

    if path is None:
        return {"lines": [], "name": name, "files": files,
                "path": str(log_dir()), "exists": False,
                "size_bytes": 0, "truncated": False}

    try:
        lines = _tail(path, limit)
        size = path.stat().st_size
    except OSError as exc:
        return {"lines": [{"text": i18n.translate_with("The log could not be read: {error}", None, error=exc),
                           "level": "ERROR"}],
                "name": name, "files": files, "path": str(path),
                "exists": True, "size_bytes": 0, "truncated": False}

    secrets = _secrets()
    language = language or i18n.current_language()
    result = []
    for line in lines:
        line_level = _level(line)
        if level and line_level != level:
            continue
        result.append({"text": translate_line(_mask(line.rstrip("\n"), secrets), language),
                       "level": line_level})

    return {
        "lines": result,
        "name": name,
        "files": files,
        "path": str(path),
        "exists": True,
        "size_bytes": size,
        "truncated": len(lines) >= limit,
    }


def _tail(path: Path, limit: int) -> list[str]:
    """The last `limit` lines of a file, without reading it whole into memory.

    It is read in blocks from the end until there are enough. The simpler
    `file.readlines()[-limit:]` would pull everything of a big log into
    memory just to take the last bit of it.
    """
    block = 8192
    with path.open("rb") as file:
        file.seek(0, 2)
        end = file.tell()
        data = b""
        while end > 0 and data.count(b"\n") <= limit:
            step = min(block, end)
            end -= step
            file.seek(end)
            data = file.read(step) + data

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()

    # When we read from the end and did not get to the start of the file, the
    # first line is torn in the middle - the block ended where it ended. Such a
    # line is thrown away: half a message confuses more than none.
    if end > 0 and lines:
        lines = lines[1:]
    return lines[-limit:]


def levels() -> list[str]:
    """The levels offered in the filter - from the most serious."""
    return ["ERROR", "WARNING", "INFO"]
