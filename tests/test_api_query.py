# -*- coding: utf-8 -*-
r"""Filters, sorting and a limit in the API - reading a query and working with a list.

`api_query` knows nothing about the database nor about FastAPI: it gets the
parameters of the address and a description of the lists
(`{list: {field: kind}}`) and either returns the parsed query or raises
`ApiError` with an error code. So it is tested here without the application.

It guards mainly what it was made for: nonsense in a query must never pass
quietly and return data as if it were fine.

Run:
    .venv/bin/python tests/test_api_query.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import api_query  # noqa: E402
from jellyscope.api_query import ApiError, Sort, parse  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def k(fn):
    """The code of the error `fn` raises, or None when it passes."""
    try:
        fn()
    except ApiError as error:
        return error.code
    return None


def error_of(fn) -> ApiError | None:
    try:
        fn()
    except ApiError as error:
        return error
    return None


S = {"items": {"path": "text", "plays": "number", "added_at": "time",
               "is_series": "bool", "tmdb_id": "text"}}


def one(params, known=frozenset({"limit"}), lists=None):
    return parse(list(params), set(known), S if lists is None else lists)


print("--- parsing ---")
d = one([("path[starts_with]", "/media/xy"), ("sort", "plays,-added_at"), ("limit", "5")])
f = d.filters[0] if d.filters else None
check(len(d.filters) == 1 and f.list_name == "items" and f.field == "path"
      and f.operator == "starts_with" and f.value == "/media/xy" and f.kind == "text",
      "a filter is parsed with its list and kind too")
check([(r.field, r.descending) for r in d.sorts] == [("plays", False), ("added_at", True)],
      "sorting: ascending without a minus, descending with one")
check(d.values == {"limit": "5"}, "plain parameters stay as text")
check(d.for_list("items") == (d.filters, d.sorts) and d.has_filter("items")
      and not d.has_filter("other"), "for_list() and has_filter() by the list")

for wrong in ("filtr", "day"):
    error = error_of(lambda: one([(wrong, "x")]))
    check(error is not None and error.code == "unknown_parameter"
          and "limit" in (error.allowed or []) and "sort" in (error.allowed or []),
          f"unknown parameter {wrong!r} -> unknown_parameter with a list")
check(k(lambda: one([("path[x", "1")])) == "unknown_parameter",
      "broken brackets -> unknown_parameter")
check(k(lambda: one([("limit", "5"), ("limit", "6")])) == "duplicate_parameter",
      "a parameter twice -> duplicate_parameter")
check(k(lambda: one([("sort", "plays"), ("sort", "path")])) == "duplicate_parameter",
      "sort twice -> duplicate_parameter")
check(k(lambda: one([("sort", "plays,plays")])) == "duplicate_parameter",
      "the same field twice in sort -> duplicate_parameter")

error = error_of(lambda: one([("patth[contains]", "x")]))
check(error is not None and error.code == "unknown_field"
      and sorted(error.allowed or []) == sorted(S["items"]),
      "unknown field -> unknown_field with the list's fields")
check(k(lambda: one([("sort", "-patth")])) == "unknown_field", "unknown field in sort")
check(k(lambda: one([("path[like]", "x")])) == "unknown_operator", "unknown operator")
check(k(lambda: one([("plays[contains]", "1")])) == "operator_not_allowed",
      "contains on a number -> operator_not_allowed")
check(k(lambda: one([("tmdb_id[gt]", "5")])) == "operator_not_allowed",
      "a text that looks like a number is still a text (gt does not work)")
d = one([("tmdb_id[equals]", "1001")])
check(d.filters[0].value == "1001", "tmdb_id[equals]=1001 passes as text")

for key, value in (("plays[gt]", "lots"), ("plays[gt]", "nan"), ("plays[gt]", "inf"),
                   ("added_at[gt]", "2026-13-01"), ("added_at[gt]", "2026-10-01T10:00"),
                   ("is_series[equals]", "yes"), ("path[contains]", ""),
                   ("path[contains]", "x" * 201), ("path[empty]", "maybe")):
    check(k(lambda: one([(key, value)])) == "invalid_value",
          f"{key}={value[:12]!r} -> invalid_value")
check(one([("path[contains]", "x" * 200)]).filters[0].value == "x" * 200,
      "200 characters still pass")
# PostgreSQL does not accept NUL in a text even as a parameter - the query would end in a 500.
check(k(lambda: one([("path[contains]", "a\x00b")])) == "invalid_value",
      "NUL in a value -> invalid_value")
check(k(lambda: one([("path[in]", "a,\x00")])) == "invalid_value", "in 'in' too")
check(one([("added_at[gte]", "2026-10-01")]).filters[0].value == "2026-10-01 00:00:00",
      "a date alone = midnight of that day")
check(one([("added_at[lt]", "2026-10-01 18:30:00")]).filters[0].value
      == "2026-10-01 18:30:00", "a date with a time stays")
check(one([("is_series[equals]", "TRUE")]).filters[0].value is True,
      "true/false regardless of case")
check(one([("path[empty]", "false")]).filters[0].value is False, "empty takes true/false")
check(one([("plays[gte]", "1.5")]).filters[0].value == 1.5, "a decimal number")

check(one([("plays[in]", "1,2")]).filters[0].value == (1.0, 2.0),
      "in: numbers separated by commas")
check(one([("path[in]", "a,b")]).filters[0].value == ("a", "b"), "in: texts")
check(k(lambda: one([("plays[in]", "1,x")])) == "invalid_value", "in: every value by the kind")
# A space after a comma would quietly find no text (" a2" is not "a2") - the
# result would narrow down without anybody noticing. So a 400, not quiet trimming.
for value in ("a, b", "a ,b", " a,b", "1, 2"):
    field = "plays[in]" if value[0].isdigit() else "path[in]"
    check(k(lambda: one([(field, value)])) == "invalid_value",
          f"in with a space around a value ({value!r}) -> invalid_value")
check(k(lambda: one([("plays[in]", ",".join(["1"] * 51))])) == "too_many", "in: 51 values")
check(len(one([("plays[in]", ",".join(["1"] * 50))]).filters[0].value) == 50,
      "in: 50 values pass")
check(k(lambda: one([("plays[gte]", "1")] * 21)) == "too_many", "21 filters -> too_many")
check(len(one([("plays[gte]", "1")] * 20).filters) == 20, "20 filters pass (even the same ones)")
check(k(lambda: one([("sort", "plays,path,added_at,tmdb_id")])) == "too_many",
      "4 fields in sort -> too_many")
check(k(lambda: one([("sort", "")])) == "invalid_value", "empty sort -> invalid_value")
check(k(lambda: one([("sort", "plays,,path")])) == "invalid_value", "an empty field in sort")

print()
print("--- several lists ---")
SEVERAL = {"a": {"title": "text", "attempts": "number"}, "b": {"title": "text"}}
error = error_of(lambda: parse([("title[contains]", "x")], set(), SEVERAL))
check(error is not None and error.code == "unknown_list" and error.allowed == ["a", "b"],
      "a field without a prefix -> unknown_list with the lists")
check(k(lambda: parse([("c.title[contains]", "x")], set(), SEVERAL)) == "unknown_list",
      "unknown prefix -> unknown_list")
check(k(lambda: parse([("sort", "attempts")], set(), SEVERAL)) == "unknown_list",
      "sort without a prefix -> unknown_list")
d = parse([("a.title[contains]", "x"), ("sort", "-a.attempts")], set(), SEVERAL)
check(d.filters[0].list_name == "a" and d.has_filter("a") and not d.has_filter("b"),
      "a.title[contains] aims only at the list a")
check(d.sorts == [Sort("a", "attempts", True, "number")],
      "sort=-a.attempts: the minus before the whole name")
check(k(lambda: parse([("b.attempts[gt]", "1")], set(), SEVERAL)) == "unknown_field",
      "a field another list has -> unknown_field")

check(one([("items.path[contains]", "x")]).filters[0].field == "path",
      "one list: its own prefix passes")
check(k(lambda: one([("other.path[contains]", "x")])) == "unknown_list",
      "one list: a foreign prefix -> unknown_list")

print()
print("--- an address without a list ---")
check(k(lambda: parse([("plays[gt]", "1")], {"days"}, {})) == "not_filterable",
      "a filter on an object -> not_filterable")
check(k(lambda: parse([("sort", "plays")], {"days"}, {})) == "not_filterable",
      "sort on an object -> not_filterable")
check(parse([("days", "7")], {"days"}, {}).values == {"days": "7"}, "a plain parameter passes")
error = error_of(lambda: parse([("limit", "5")], {"days"}, {}))
check(error is not None and error.code == "unknown_parameter"
      and "sort" not in (error.allowed or []),
      "without a list neither sort nor limit is among the allowed")

print()
print("--- strict conversions ---")


def query(**values):
    return parse(list(values.items()), set(values), {})


for wrong in ("abc", "", "0", "3651", "1e400", "1.5", "-1", " 7"):
    check(k(lambda: api_query.integer(query(days=wrong), "days", 30, 1, 3650))
          == "invalid_value", f"days={wrong!r} -> invalid_value")
check(api_query.integer(query(days="7"), "days", 30, 1, 3650) == 7, "days=7 -> 7")
check(api_query.integer(query(), "days", 30, 1, 3650) == 30, "missing -> the default")
check(api_query.integer(query(), "limit", None, 1, 500) is None, "the default can be None")
check(api_query.choice(query(kind="MOVIES"), "kind", "both", ("both", "movies", "series"))
      == "movies", "a choice regardless of case")
check(k(lambda: api_query.choice(query(kind="films"), "kind", "both",
                                ("both", "movies", "series"))) == "invalid_value",
      "an unknown choice -> invalid_value")
check(api_query.choice(query(), "kind", "both", ("both",)) == "both", "choice: missing -> the default")
check(api_query.number(query(min_size_mb="10.5"), "min_size_mb", 0, 0, 1e7) == 10.5, "number 10.5")
for wrong in ("-5", "x", "nan", ""):
    check(k(lambda: api_query.number(query(min_size_mb=wrong), "min_size_mb", 0, 0, 1e7))
          == "invalid_value", f"min_size_mb={wrong!r} -> invalid_value")
check(api_query.identifier(query(user="abc-DEF_12"), "user") == "abc-DEF_12",
      "an identifier passes")
check(api_query.identifier(query(), "user") is None, "identifier: missing -> None")
for wrong in ("a" * 65, "a;b", "", "a b"):
    check(k(lambda: api_query.identifier(query(user=wrong), "user")) == "invalid_value",
          f"user={wrong[:10]!r} -> invalid_value")
error = error_of(lambda: api_query.integer(query(days="x"), "days", 30, 1, 3650))
check(error is not None and error.param == "days" and error.status == 400,
      "a conversion error carries the parameter's name and the status 400")

print()
print("--- the error body ---")
body = ApiError(400, "unknown_field", "Unknown field.").body()
check(body == {"detail": "Unknown field.", "error": "unknown_field"},
      "without a parameter only detail and error")
body = ApiError(400, "unknown_field", "Unknown.", "x[eq]", ["a"]).body()
check(body.get("parameter") == "x[eq]" and body.get("allowed") == ["a"],
      "with a parameter and a list too")
error = ApiError(401, "missing_token", "No token.", headers={"WWW-Authenticate": "Bearer"})
check(error.status == 401 and error.headers == {"WWW-Authenticate": "Bearer"},
      "the status and the headers stay on the exception")
check(api_query.bad_request("invalid_value", "Bad.").status == 400, "bad_request() is a 400")

print()
print("--- filters ---")
T = {"rows": {"title": "text", "path": "text", "plays": "number",
              "added_at": "time", "is_series": "bool"}}
R = [
    {"id": 1, "title": "Amélie", "path": "/media/Filmy HD/Amélie.mkv", "plays": 3,
     "added_at": "2026-10-01T12:00:00.0000000Z", "is_series": False},
    {"id": 2, "title": "Duna", "path": "D:\\Filmy\\Duna.mkv", "plays": 0,
     "added_at": "2026-09-30 23:59:59", "is_series": False},
    {"id": 3, "title": None, "path": "/media/Tom & Jerry/x.mkv", "plays": None,
     "added_at": "2026-10-01", "is_series": True},
    {"id": 4, "title": "", "path": None, "plays": 1, "added_at": "yesterday", "is_series": None},
    {"id": 5, "title": "Kancl", "path": "/media/serialy/Kancl", "plays": 5,
     "added_at": None, "is_series": True},
]


def ids(params, rows=None, limit=None, lists=None):
    d = parse(list(params), set(), lists or T)
    list_name = next(iter(lists or T))
    result = api_query.apply(rows if rows is not None else R, *d.for_list(list_name), limit)
    return [r["id"] for r in result]


for params, expected in (
        ([("title[equals]", "AMÉLIE")], [1]),
        ([("title[equals]", "amélie")], [1]),
        ([("title[not]", "amélie")], [2, 3, 4, 5]),
        ([("path[contains]", "filmy")], [1, 2]),
        ([("path[not_contains]", "filmy")], [3, 4, 5]),
        ([("path[starts_with]", "/media/")], [1, 3, 5]),
        ([("path[ends_with]", ".MKV")], [1, 2, 3]),
        ([("title[in]", "duna,KANCL")], [2, 5]),
        ([("title[empty]", "true")], [3, 4]),
        ([("title[empty]", "false")], [1, 2, 5]),
        ([("plays[equals]", "0")], [2]),
        ([("plays[not]", "0")], [1, 3, 4, 5]),
        ([("plays[gt]", "1")], [1, 5]),
        ([("plays[gte]", "1")], [1, 4, 5]),
        ([("plays[lt]", "1")], [2]),
        ([("plays[lte]", "1")], [2, 4]),
        ([("plays[in]", "0,5")], [2, 5]),
        ([("plays[empty]", "true")], [3]),
        ([("added_at[gte]", "2026-10-01")], [1, 3]),
        ([("added_at[lt]", "2026-10-01")], [2]),
        ([("added_at[gt]", "2026-10-01 11:59:59")], [1]),
        ([("added_at[empty]", "true")], [4, 5]),
        ([("is_series[equals]", "true")], [3, 5]),
        ([("is_series[not]", "true")], [1, 2, 4]),
        ([("path[equals]", "/media/Filmy HD/Amélie.mkv")], [1]),
        ([("path[starts_with]", "D:\\Filmy")], [2]),
        ([("path[contains]", "Tom & Jerry")], [3]),
        ([("plays[gte]", "1"), ("plays[gte]", "3")], [1, 5]),
        ([("plays[gte]", "1"), ("plays[lte]", "3")], [1, 4]),
):
    label = "&".join(f"{a}={b}" for a, b in params)
    check(ids(params) == expected, f"{label} -> {expected} ({ids(params)})")

print()
print("--- sorting and the limit ---")
check(ids([("sort", "plays")]) == [2, 4, 1, 5, 3], "ascending, empty ones at the end")
check(ids([("sort", "-plays")]) == [5, 1, 4, 2, 3], "descending, empty ones still at the end")
check(ids([("sort", "title")]) == [1, 2, 5, 3, 4],
      "text regardless of case; null and an empty text at the end")
check(ids([("sort", "added_at")]) == [2, 3, 1, 4, 5],
      "a time in various shapes sorts right, an unreadable one as empty")
check(ids([]) == [1, 2, 3, 4, 5], "without sort the order is unchanged")

TIES = [{"id": i, "plays": p, "added_at": a, "title": "", "path": "", "is_series": False}
        for i, p, a in ((1, 1, "2026-03-01"), (2, 0, "2026-05-01"), (3, 1, "2026-01-01"),
                        (4, 0, "2026-02-01"), (5, 0, "2026-02-01"))]
check(ids([("sort", "plays,added_at")], TIES) == [4, 5, 2, 3, 1],
      "two fields: by plays, on a tie by the date; on a full tie the input order")
check(ids([("sort", "-plays,-added_at")], TIES) == [1, 3, 2, 4, 5],
      "two fields descending, stable here too")

# The example from the spec: the searched-for files are at the end of the
# input - were the limit used before the filter, nothing would come back.
LONG = [{"id": i, "path": f"/media/jine/{i}.mkv", "plays": i % 4, "title": "",
         "added_at": f"2026-01-{1 + i % 28:02d}", "is_series": False} for i in range(27)]
LONG += [{"id": 100, "path": "/media/xy/a.mkv", "plays": 1, "added_at": "2025-01-01",
          "title": "", "is_series": False},
         {"id": 101, "path": "/media/xy/b.mkv", "plays": 0, "added_at": "2025-06-01",
          "title": "", "is_series": False},
         {"id": 102, "path": "/media/XY/c.mkv", "plays": 0, "added_at": "2025-02-01",
          "title": "", "is_series": False}]
original = [dict(r) for r in LONG]
check(ids([("path[starts_with]", "/media/xy"), ("sort", "plays,added_at")], LONG, 5)
      == [102, 101, 100],
      "the 5 least played from /media/xy from the oldest: all 3, not 0")
check(LONG == original, "the input list stayed unchanged")
check(len(ids([], LONG, None)) == 30, "limit None returns everything")
check(ids([("sort", "-plays")], LONG, 2) == [3, 7], "the limit only after sorting")

print()
print("--- the help for the index ---")
help_block = api_query.help_text()
check(help_block.get("operators") == {k: list(v) for k, v in api_query.OPERATORS.items()},
      "operators by kind")
check("sort=plays,added_at" in help_block.get("example", ""), "the example from the spec")
check(help_block.get("limits") == {"filters": 20, "sort_fields": 3, "in_values": 50,
                                   "text_length": 200}, "the ceilings")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
