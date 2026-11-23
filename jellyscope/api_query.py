"""Filters, sorting and a limit for lists in the API.

A query is written into the address: `field[operator]=value` (every filter
separately, all of them apply at once), `sort=field,-field` and `limit`. On
addresses with several lists (`/insights`, `/play-methods`) the field carries
the list's prefix: `never_finished.title[contains]=x`.

The module knows nothing about the database nor about FastAPI. It gets the
parameters of the address and a description of the lists
(`{list: {field: kind}}`) and either returns the parsed query or raises
`ApiError`. Nonsense in a query thus never passes quietly: an unknown
parameter used to be skipped and a wrong value fell back to the default, so
the answer looked fine, and yet it was not what was asked for.

Error messages are in English - the API is read by programs and developers,
and the API index and documentation are in English.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TEXT = "text"
NUMBER = "number"
TIME = "time"
YES_NO = "bool"

OPERATORS: dict[str, tuple[str, ...]] = {
    TEXT: ("equals", "not", "contains", "not_contains", "starts_with",
           "ends_with", "in", "empty"),
    NUMBER: ("equals", "not", "gt", "gte", "lt", "lte", "in", "empty"),
    TIME: ("gt", "gte", "lt", "lte", "empty"),
    YES_NO: ("equals", "not"),
}
ALL_OPERATORS = sorted({op for ops in OPERATORS.values() for op in ops})

# The query's ceilings. It is not about the data but about the server: a query
# is processed over the whole list, so nobody should keep it busy with a
# thousand filters.
MAX_FILTERS = 20
MAX_SORTS = 3
MAX_IN = 50
MAX_TEXT = 200

TIME_FORMAT_HINT = "%Y-%m-%d %H:%M:%S"
# Everything is compared through fullmatch and with ASCII digits only: `$`
# would let a newline at the end through (`days=30\n`), and `\d` Arabic or
# full-width digits as well, which int() reads happily.
_FILTER_KEY = re.compile(r"(?:([a-z0-9_]+)\.)?([a-z0-9_]+)\[([a-z0-9_]+)\]")
# A time in the shape the API returns it too (`2026-10-01T12:00:00.0000000Z`
# from Jellyfin): fractions of a second and the Z are dropped, compared in UTC.
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
                   r"(?:[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z?)?")
_INTEGER = re.compile(r"-?[0-9]+")
# A decimal number with a dot. float() by itself would take `1_0`, `٣`, `５`,
# `1e3` or a space and a newline around too - none of that belongs in an address.
_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ApiError(Exception):
    """An error of a request to the API - the status, a code and a message for the caller.

    One exception for 400 as well as 401/403/404, so every API error has the
    same body (`body()`): a program tells by `error` what is wrong and does
    not have to read the sentence.
    """

    def __init__(self, status: int, code: str, message: str,
                 param: str | None = None, allowed: list[str] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.param = param
        self.allowed = allowed
        self.headers = headers

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.message, "error": self.code}
        if self.param is not None:
            body["parameter"] = self.param
        if self.allowed is not None:
            body["allowed"] = self.allowed
        return body


def bad_request(code: str, message: str, param: str | None = None,
                allowed: list[str] | None = None) -> ApiError:
    """A 400 error - a badly written query."""
    return ApiError(400, code, message, param, allowed)


@dataclass(frozen=True)
class Filter:
    list_name: str
    field: str
    operator: str
    value: Any
    kind: str
    param: str


@dataclass(frozen=True)
class Sort:
    list_name: str
    field: str
    descending: bool
    kind: str


@dataclass
class Query:
    values: dict[str, str] = field(default_factory=dict)
    filters: list[Filter] = field(default_factory=list)
    sorts: list[Sort] = field(default_factory=list)

    def for_list(self, list_name: str) -> tuple[list[Filter], list[Sort]]:
        """The filters and sorting that aim at one list."""
        return ([f for f in self.filters if f.list_name == list_name],
                [r for r in self.sorts if r.list_name == list_name])

    def has_filter(self, list_name: str) -> bool:
        return any(f.list_name == list_name for f in self.filters)


def parse(params: list[tuple[str, str]], known: set[str],
          lists: dict[str, dict[str, str]]) -> Query:
    """Reads and checks the parameters of one address.

    `known` are the plain parameters of the address (`days`, `limit`…), each
    may come once. `lists` describe what the address returns; an empty
    dictionary = the address returns one object and can be neither filtered
    nor sorted.
    """
    query = Query()
    allowed = sorted(known) + (["sort"] if lists else [])
    for key, value in params:
        if "[" in key or "]" in key:
            if not lists:
                raise _unfilterable(key)
            if len(query.filters) >= MAX_FILTERS:
                raise bad_request("too_many", f"At most {MAX_FILTERS} filters in one request.", key)
            query.filters.append(_filter(key, value, lists, allowed))
        elif key == "sort":
            if not lists:
                raise _unfilterable(key)
            if query.sorts:
                raise bad_request("duplicate_parameter", "Parameter 'sort' given twice.", key)
            query.sorts = _sort(value, lists)
        elif key in known:
            if key in query.values:
                raise bad_request("duplicate_parameter", f"Parameter '{key}' given twice.", key)
            query.values[key] = value
        else:
            raise bad_request("unknown_parameter",
                              f"Unknown parameter '{key}'. Parameters: "
                              + (", ".join(allowed) or "none")
                              + ("; filters are field[operator]=value." if lists else "."),
                              key, allowed)
    return query


def _unfilterable(key: str) -> ApiError:
    return bad_request("not_filterable",
                       "This address returns one object, not a list: it cannot be "
                       "filtered or sorted.", key)


def _list_and_kind(prefix: str | None, field: str, lists: dict[str, dict[str, str]],
                   param: str) -> tuple[str, str]:
    """Which list a field aims at, and of what kind it is."""
    names = list(lists)
    if len(names) > 1:
        if prefix not in lists:
            raise bad_request("unknown_list",
                              "This address returns several lists: name one before the "
                              f"field, e.g. '{names[0]}.{field}'. Lists: {', '.join(names)}.",
                              param, names)
        list_name = prefix
    else:
        list_name = names[0]
        if prefix is not None and prefix != list_name:
            raise bad_request("unknown_list", f"Unknown list '{prefix}'. List: {list_name}.",
                              param, names)
    fields = lists[list_name]
    if field not in fields:
        raise bad_request("unknown_field",
                          f"Unknown field '{field}' in {list_name}. Fields: "
                          + ", ".join(fields) + ".", param, list(fields))
    return list_name, fields[field]


def _filter(key: str, text: str, lists: dict[str, dict[str, str]],
            allowed: list[str]) -> Filter:
    match = _FILTER_KEY.fullmatch(key)
    if not match:
        raise bad_request("unknown_parameter",
                          f"Unknown parameter '{key}'. Filters are field[operator]=value.",
                          key, allowed)
    prefix, field, operator = match.groups()
    list_name, kind = _list_and_kind(prefix, field, lists, key)
    if operator not in ALL_OPERATORS:
        raise bad_request("unknown_operator",
                          f"Unknown operator '{operator}'. Operators for {kind}: "
                          + ", ".join(OPERATORS[kind]) + ".", key, list(OPERATORS[kind]))
    if operator not in OPERATORS[kind]:
        raise bad_request("operator_not_allowed",
                          f"Operator '{operator}' does not work on {kind} field '{field}'. "
                          f"Operators for {kind}: " + ", ".join(OPERATORS[kind]) + ".",
                          key, list(OPERATORS[kind]))
    return Filter(list_name, field, operator, _filter_value(kind, operator, text, key),
                  kind, key)


def _filter_value(kind: str, operator: str, text: str, param: str) -> Any:
    if operator == "empty":
        return _yes_no(text, param)
    if operator == "in":
        parts = text.split(",")
        if len(parts) > MAX_IN:
            raise bad_request("too_many", f"At most {MAX_IN} values in 'in'.", param)
        # "a, b" would look for the second value with the space and quietly find nothing.
        if any(part != part.strip() for part in parts):
            raise bad_request("invalid_value",
                              "No spaces around the values in 'in': a,b not a, b.", param)
        return tuple(_value(kind, part, param) for part in parts)
    return _value(kind, text, param)


def _value(kind: str, text: str, param: str) -> Any:
    """One value of a filter, converted by the kind of the field."""
    if text == "":
        raise bad_request("invalid_value", "Empty value.", param)
    if kind == TEXT:
        if len(text) > MAX_TEXT:
            raise bad_request("invalid_value", f"Text longer than {MAX_TEXT} characters.", param)
        # PostgreSQL does not accept a text with NUL even as a parameter - the query would fail.
        if "\x00" in text:
            raise bad_request("invalid_value", "Text contains a NUL character.", param)
        return text
    if kind == NUMBER:
        number = float(text) if _DECIMAL.fullmatch(text) else math.nan
        if not math.isfinite(number):
            raise bad_request("invalid_value", f"'{text[:40]}' is not a number.", param)
        return number
    if kind == TIME:
        if not _TIME.fullmatch(text):
            raise bad_request("invalid_value",
                              "Time is YYYY-MM-DD or YYYY-MM-DD HH:MM:SS (UTC); "
                              "YYYY-MM-DDTHH:MM:SS.fffffffZ works too.", param)
        full = text.replace("T", " ")[:19] if len(text) > 10 else text + " 00:00:00"
        try:
            datetime.strptime(full, TIME_FORMAT_HINT)
        except ValueError:
            raise bad_request("invalid_value", f"'{text}' is not a valid time.", param) from None
        return full
    return _yes_no(text, param)


def _yes_no(text: str, param: str) -> bool:
    value = text.lower()
    if value not in ("true", "false"):
        raise bad_request("invalid_value", "Value is true or false.", param)
    return value == "true"


def _sort(text: str, lists: dict[str, dict[str, str]]) -> list[Sort]:
    parts = text.split(",")
    if len(parts) > MAX_SORTS:
        raise bad_request("too_many", f"Sort by at most {MAX_SORTS} fields.", "sort")
    sorts: list[Sort] = []
    for part in parts:
        descending = part.startswith("-")
        name = part[1:] if descending else part
        if not name:
            raise bad_request("invalid_value", "Empty field in 'sort'.", "sort")
        prefix, _, field = name.rpartition(".")
        list_name, kind = _list_and_kind(prefix or None, field, lists, "sort")
        if any(r.list_name == list_name and r.field == field for r in sorts):
            raise bad_request("duplicate_parameter", f"Field '{name}' twice in 'sort'.", "sort")
        sorts.append(Sort(list_name, field, descending, kind))
    return sorts


# ---- applying to a list --------------------------------------------------

def apply(rows: list[dict[str, Any]], filters: list[Filter], sorts: list[Sort],
          limit: int | None) -> list[dict[str, Any]]:
    """Filters → sorting → limit, in this order and over the whole list.

    The limit is only the last step: were the first 25 rows taken first and
    filtered only within them, the searched-for files deeper in the order
    would not get into the answer at all. Neither the input list nor its rows
    change.
    """
    result = [r for r in rows if all(_matches(r, f) for f in filters)]
    if sorts:
        result = _order_rows(result, sorts)
    return result if limit is None else result[:limit]


def _text(text: str) -> str:
    """A text for comparing: the same character in any notation and without case.

    "é" can be written as one character (NFC) and as "e" + an accent (NFD) -
    an address from a Mac or from the clipboard can well send the second.
    Without unifying, "Amélie" would not be found.
    """
    return unicodedata.normalize("NFC", text).casefold()


def _normalise(kind: str, value: Any) -> Any:
    """A value from a row prepared for comparing. None = a field without a value.

    An empty text is "without a value" too - the same as with the `empty`
    operator. A time comes in various shapes (`2026-10-01T12:00:00.0000000Z`
    from Jellyfin, `2026-10-01 12:00:00` from the database, a date alone); it
    is compared as 19 characters `YYYY-MM-DD HH:MM:SS`, which sorts as text
    too. An unreadable time is empty, not an error - that is an error of the
    data, not of the query.
    """
    if value is None:
        return None
    if kind == TEXT:
        text = str(value)
        return _text(text) if text else None
    if kind == NUMBER:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    if kind == TIME:
        text = str(value).replace("T", " ")[:19]
        if len(text) == 10:
            text += " 00:00:00"
        try:
            datetime.strptime(text, TIME_FORMAT_HINT)
        except ValueError:
            return None
        return text
    return bool(value) if isinstance(value, (bool, int)) else None


def _matches(row: dict[str, Any], rule: Filter) -> bool:
    value = _normalise(rule.kind, row.get(rule.field))
    operator = rule.operator
    if operator == "empty":
        return (value is None) == rule.value
    target = rule.value
    if rule.kind == TEXT:
        target = tuple(_text(c) for c in target) if operator == "in" else _text(target)
    # A field without a value "is not equal" to anything and "does not contain"
    # anything; everything else (equals, contains, greater than...) does not hold for it.
    if operator == "not":
        return value is None or value != target
    if operator == "not_contains":
        return value is None or target not in value
    if value is None:
        return False
    if operator == "equals":
        return value == target
    if operator == "in":
        return value in target
    if operator == "contains":
        return target in value
    if operator == "starts_with":
        return value.startswith(target)
    if operator == "ends_with":
        return value.endswith(target)
    if operator == "gt":
        return value > target
    if operator == "gte":
        return value >= target
    if operator == "lt":
        return value < target
    return value <= target


def _order_rows(rows: list[dict[str, Any]], sorts: list[Sort]) -> list[dict[str, Any]]:
    """Sorting by the fields from the last to the first, each pass stable.

    Empty values always go to the end, ascending as well as descending - a
    plain `reverse` would move them up in descending order. Sorting is done
    here, not in SQL, so the result is the same on SQLite and PostgreSQL (each
    puts NULL elsewhere).
    """
    result = list(rows)
    for rule in reversed(sorts):
        keys = [_normalise(rule.kind, r.get(rule.field)) for r in result]
        filled = [(k, r) for k, r in zip(keys, result) if k is not None]
        empty = [r for k, r in zip(keys, result) if k is None]
        filled.sort(key=lambda pair: pair[0], reverse=rule.descending)
        result = [r for _, r in filled] + empty
    return result


def help_text() -> dict[str, Any]:
    """How filtering works - a block for the index `/api/v1/`."""
    return {
        "syntax": "field[operator]=value; every filter is its own parameter "
                  "and all of them must match",
        "lists": "on addresses with several lists (/api/v1/insights, "
                 "/api/v1/play-methods) the field carries the list name: "
                 "never_finished.title[contains]=x; lists without a filter "
                 "come back unchanged",
        "sort": "sort=field,-field (minus = descending), at most "
                f"{MAX_SORTS} fields; empty values always last",
        "order": "filters, then sort, then limit",
        "values": "text ignores case; number is 12 or 1.5; time is YYYY-MM-DD "
                  "or YYYY-MM-DD HH:MM:SS in UTC; bool is true or false; "
                  "in takes values separated by commas; empty takes true or false",
        "operators": {kind: list(ops) for kind, ops in OPERATORS.items()},
        "limits": {"filters": MAX_FILTERS, "sort_fields": MAX_SORTS,
                   "in_values": MAX_IN, "text_length": MAX_TEXT},
        "example": "/api/v1/least-played?path[starts_with]=/media/movies"
                   "&sort=plays,added_at&limit=5",
    }


# ---- strict conversions of plain parameters ------------------------------
# Nonsense used to fall back to the default value quietly. Now it is a 400: a
# query that returns other data than what was asked for is worse than no answer.

def integer(query: Query, name: str, default: int | None, low: int, high: int) -> int | None:
    text = query.values.get(name)
    if text is None:
        return default
    if not _INTEGER.fullmatch(text) or not low <= int(text) <= high:
        raise bad_request("invalid_value",
                          f"'{name}' is a whole number from {low} to {high}.", name)
    return int(text)


def number(query: Query, name: str, default: float, low: float, high: float) -> float:
    """A number from the address within limits. The upper limit guards the
    database too: a huge number would not fit into SQL (`min_size_mb=1e20`
    used to end in a 500)."""
    text = query.values.get(name)
    if text is None:
        return default
    value = float(text) if _DECIMAL.fullmatch(text) else math.nan
    if not math.isfinite(value) or not low <= value <= high:
        raise bad_request("invalid_value",
                          f"'{name}' is a number from {low:g} to {high:g}.", name)
    return value


def choice(query: Query, name: str, default: str, choices: tuple[str, ...]) -> str:
    text = query.values.get(name)
    if text is None:
        return default
    value = text.lower()
    if value not in choices:
        raise bad_request("invalid_value",
                          f"'{name}' is one of: " + ", ".join(choices) + ".",
                          name, list(choices))
    return value


def identifier(query: Query, name: str) -> str | None:
    text = query.values.get(name)
    if text is None:
        return None
    if not _IDENTIFIER.fullmatch(text):
        raise bad_request("invalid_value",
                          f"'{name}' is 1-64 letters, digits, '-' or '_'.", name)
    return text
