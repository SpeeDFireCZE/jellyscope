# -*- coding: utf-8 -*-
"""Aggregate queries must pass on PostgreSQL too.

SQLite has a leniency almost nobody knows: in a query with `GROUP BY` the selection
may contain a column that is neither grouped nor aggregated. It then takes the value
from **any** row of the group. PostgreSQL refuses it:

    ERROR: column "i.audio_languages" must appear in the GROUP BY clause
           or be used in an aggregate function

Such a query therefore works on SQLite for years and the moment somebody switches
the database to PostgreSQL, the whole page falls over with "internal server error" -
exactly this happened to the Languages tab.

It can be checked only by reading the queries, not by running them: without
a PostgreSQL server the error never shows. Hence this test.

It does not look for generally valid SQL - it looks for one particular trap we know
slips into the project easily and shows late.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


AGGREGATE = re.compile(
    r"\b(SUM|COUNT|AVG|MIN|MAX|TOTAL|GROUP_CONCAT|STRING_AGG)\s*\(", re.I)

# Words that are not a column reference in the selection.
KEYWORDS = {
    "case", "when", "then", "else", "end", "as", "and", "or", "not", "null",
    "is", "in", "like", "cast", "integer", "real", "text", "distinct", "on",
    "coalesce", "select", "from", "where", "group", "by", "order", "limit",
    "desc", "asc", "over", "partition", "join", "left", "inner", "outer",
}


def split_by_commas(text: str) -> list[str]:
    """Splits a list into items - commas inside parentheses do not count."""
    pieces: list[str] = []
    depth = 0
    current = ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            pieces.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        pieces.append(current)
    return [k.strip() for k in pieces if k.strip()]


def alias_and_expression(item: str) -> tuple[str, str]:
    """Separates `expression AS alias`. Watches for AS inside parentheses (CAST(x AS INTEGER))."""
    depth = 0
    for match in re.finditer(r"\s+AS\s+", item, re.I):
        depth = (item[:match.start()].count("(")
                 - item[:match.start()].count(")"))
        if depth == 0:
            return item[:match.start()].strip(), item[match.end():].strip()
    return item.strip(), ""


def columns(expression: str) -> set[str]:
    """Column references in an expression - without keywords, numbers and strings."""
    without_strings = re.sub(r"'[^']*'", " ", expression)
    found = re.findall(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?",
                       without_strings)
    return {n for n in found
            if n.lower() not in KEYWORDS and not re.match(r"^\d", n)}


def query_problems(sql: str) -> list[str]:
    """Returns the selection's items PostgreSQL would refuse."""
    one_line = " ".join(re.sub(r"--[^\n]*", " ", sql).split())
    if not re.search(r"\bGROUP BY\b", one_line, re.I):
        return []

    selection = re.search(r"\bSELECT\b(.*?)\bFROM\b", one_line, re.I)
    grouping = re.search(r"\bGROUP BY\b(.*?)(?:\bORDER BY\b|\bHAVING\b|\bLIMIT\b|$)",
                         one_line, re.I)
    if not selection or not grouping:
        return []

    grouped: set[str] = set()
    for item in split_by_commas(grouping.group(1)):
        grouped.add(item.strip().lower())
        grouped |= {s.lower() for s in columns(item)}

    found: list[str] = []
    for item in split_by_commas(selection.group(1)):
        expression, alias = alias_and_expression(item)

        if AGGREGATE.search(expression):
            continue                       # an aggregate is always fine
        if re.search(r"\bSELECT\b", expression, re.I):
            continue                       # a subquery has its own rules
        if alias and alias.lower() in grouped:
            continue                       # grouped by the alias
        if expression.lower() in grouped:
            continue                       # grouped by the whole expression

        # The rest is fine only when every column it touches
        # is grouped itself (say `i.runtime_ticks / 600.0`).
        missing = {s for s in columns(expression) if s.lower() not in grouped}
        if missing:
            found.append(f"{item.strip()}   (missing: {', '.join(sorted(missing))})")

    return found


print("--- the check of itself ---")
# If the check stopped looking, it would stay silent and pretend everything is fine.
BAD = """
    SELECT COALESCE(i.series_name, i.name) AS label, i.audio_languages,
           SUM(p.watched_seconds) AS hours
      FROM playback p JOIN items i ON i.id = p.item_id
     GROUP BY label
"""
GOOD = """
    SELECT COALESCE(i.series_name, i.name) AS label,
           MAX(i.audio_languages) AS audio_languages,
           SUM(p.watched_seconds) AS hours
      FROM playback p JOIN items i ON i.id = p.item_id
     GROUP BY label
"""
check(len(query_problems(BAD)) == 1, "the check finds a bare column")
check(query_problems(GOOD) == [], "the same query with MAX() passes")
check(query_problems("SELECT a, b FROM t") == [], "a query without GROUP BY is not dealt with")
check(query_problems(
    "SELECT CAST(strftime('%H', x) AS INTEGER) AS hour, COUNT(*) AS n"
    " FROM t GROUP BY hour") == [],
    "AS inside CAST() does not confuse the search for the alias")


MARKER = "__EXPR__"

# String constants of modules (`VIEWER_GROUP = "p.user_id, …"`) that are
# substituted into queries - under their name and as `module.NAME`. These are substituted
# for real: otherwise the check would not see what is grouped in a query with a shared GROUP BY,
# and would report bare columns that are not bare. Only pieces,
# not whole queries: a nested SELECT with its own GROUP BY would be credited to the outer
# selection - and the whole queries are checked here on their own too.
CONSTANTS: dict[str, str] = {}
for _source_file in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    for _node in ast.parse(_source_file.read_text(encoding="utf-8")).body:
        if (isinstance(_node, ast.Assign) and len(_node.targets) == 1
                and isinstance(_node.targets[0], ast.Name)
                and isinstance(_node.value, ast.Constant) and isinstance(_node.value.value, str)
                and not re.search(r"\bSELECT\b", _node.value.value, re.I)):
            CONSTANTS[_node.targets[0].id] = _node.value.value
            CONSTANTS[f"{_source_file.stem}.{_node.targets[0].id}"] = _node.value.value
            # A package's submodule (jellyscope/stats/viewers.py) is called through
            # the package's front door too - `stats.VIEWER_GROUP`.
            if _source_file.parent.name != "jellyscope":
                CONSTANTS[f"{_source_file.parent.name}.{_node.targets[0].id}"] = _node.value.value


def _constant(expression: ast.AST) -> str | None:
    """The value of a substituted constant (NAME or module.NAME), otherwise None."""
    if isinstance(expression, ast.Name):
        return CONSTANTS.get(expression.id)
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        return CONSTANTS.get(f"{expression.value.id}.{expression.attr}")
    return None


def query_text(node: ast.AST) -> str | None:
    """The SQL text from a node - a plain string and an f-string too.

    Part of the queries is put together with an f-string, because a function in them differs by
    the dialect (`group_concat_distinct`). If f-strings were skipped,
    the check would silently leave out exactly the most interesting queries - among others
    the one this test was made for. Substituted expressions are replaced by
    a marker; items that contain it cannot be judged then, but
    the rest of the query is checked normally.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        pieces = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                pieces.append(part.value)
            elif isinstance(part, ast.FormattedValue) and _constant(part.value) is not None:
                pieces.append(_constant(part.value))
            else:
                pieces.append(MARKER)
        return "".join(pieces)
    return None


print()
print("--- all the queries in the project ---")
total = 0
substituted = 0
for source_file in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        sql = query_text(node)
        if sql is None or "GROUP BY" not in sql.upper():
            continue
        total += 1
        if MARKER in sql:
            substituted += 1
        # Items with a substituted expression cannot be judged - we do not know what will
        # be in them. The others can.
        found = [p for p in query_problems(sql) if MARKER not in p]
        for problem in found:
            print(f"       {source_file.name}:{node.lineno}  {problem}")
        check(not found, f"{source_file.name}:{node.lineno}")

check(total >= 5, f"enough aggregate queries were found ({total})")
check(substituted >= 1, f"the ones put together with an f-string are checked too ({substituted})")
# The check of itself: a query with a shared GROUP BY (stats.VIEWER_GROUP) is
# judged whole, not as "we do not know".
check("p.user_id IS NULL" in CONSTANTS.get("VIEWER_ORDER", ""), "the queries' constants are substituted")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
