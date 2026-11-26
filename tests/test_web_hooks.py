# -*- coding: utf-8 -*-
r"""Classes, ids and data-* attributes hold together across templates, CSS, JS and Python.

A name that templates, the stylesheets and the scripts agree on lives in several languages at once:
`class="dash-grid"` in a template, `.dash-grid` in style.css, `classList.add("dash-dragging")` or
`el.dataset.onError` in a script, `class="…"` in an SVG built by charts.py. Renamed on one side only, the
element silently loses its look or its handler - no error anywhere. This guard reads the source (no
server, no browser) and checks:

  (m1) every data-* name a script or a stylesheet reads is written by a template, a script or Python,
       and every data-* name a template writes is read by a script, a stylesheet or a test
       (WRITTEN_ONLY names the few that nothing reads on purpose);
  (m2) every class a stylesheet or a script reads is written somewhere, and every class a template
       writes literally has a rule in a stylesheet or is read by a script (HOOKS names the classes
       that only tests or the markup's meaning use);
  (m3) every id that is pointed at - for=, form=, aria-controls/labelledby/describedby, href="#x",
       getElementById, `#x` in a selector, the modal ids in data-modal-like attributes - exists as an
       id in a template or is set by a script (an id built with a number counts by its prefix);
  (m4) no class, id or data-* name of a finished kind holds a Czech word of CZECH_PARTS (the old
       names of step 10c1), except KEPT - names built from stored values or equal to a form field;
       the same for the files under static/ (kind file) and the stylesheets' custom properties and
       @keyframes (kind css);
  (m5) every custom property a stylesheet, a template, a script or Python reads with var(--x) is
       defined (--x: in a stylesheet) or set (style="--x: …", setProperty("--x", …));
  (m6) every address /static/… that a template, a stylesheet, a script or Python names exists (a
       folder for an address completed at run time: "/static/events/games/" + key + ".js").
  (m7) every text a script or a template reads by its key exists in the block that holds it: TEXTS.x in
       static/js/ is a key of a #texts-js block of a template; api.texts.x in a game is a key of that
       holiday's texts in _event_game.html (or one the template adds to all: close, year); texts.x in
       events.js and game_texts.x in the template are keys every holiday has.

Traps: every check runs once more over planted texts and has to report each fault.

Run:
    .venv/bin/python tests/test_web_hooks.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
STATIC = PROJECT / "jellyscope" / "static"
TEMPLATES = PROJECT / "jellyscope" / "templates"

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(("OK    " if condition else "FAIL  ") + f" {label}")
    if not condition:
        failures += 1


NAME = r"-?[_a-zA-Z][\w-]*"
JS_TOKEN = re.compile(r"""//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`""", re.S)
SELECTOR = re.compile(rf"\.({NAME})|#({NAME})|\[\s*data-([\w-]+)")
# The start of an attribute up to its opening quote. The value is walked by hand (template_attrs): a Jinja block
# {{ ... }} or {% ... %} may hold a quote and ends at its first closing brackets, a lone brace is a plain character.
# No regex for the value - CodeQL (py/redos) reads a lookahead inside a starred alternation as exponential.
TEMPLATE_ATTR_START = re.compile(
    r"""(?<![\w-])(class|id|for|form|list|aria-controls|aria-labelledby|aria-describedby|href|data-[\w-]+)\s*=\s*\"""")


def _value_end(text: str, start: int) -> int:
    """Where the closing quote of a value that starts at `start` is - -1 when the value is not closed."""
    position = start
    while position < len(text):
        if text[position] == '"':
            return position
        if text.startswith(("{{", "{%"), position):
            closing = text.find("}}" if text[position + 1] == "{" else "%}", position + 2)
            if closing < 0:
                return -1                       # a block never closed spoils the value
            position = closing + 2
        else:
            position += 1
    return -1


def template_attrs(text: str) -> list[tuple[str, str]]:
    """(name, value) of every attribute TEMPLATE_ATTR_START names, in the order of the text."""
    found, position = [], 0
    while (start := TEMPLATE_ATTR_START.search(text, position)) is not None:
        end = _value_end(text, start.end())
        if end < 0:
            position = start.start() + 1        # no attribute here; look on from the next character
            continue
        found.append((start.group(1), text[start.end():end]))
        position = end + 1
    return found


STRING_ATTR = re.compile(r"""(?<![\w-])(class|id|data-[\w-]+)\s*=\s*\\?["']([^"'\\]*)""")


def kebab(name: str) -> str:
    return re.sub(r"[A-Z]", lambda m: "-" + m.group().lower(), name)


def css_selectors(text: str) -> list[str]:
    """The selector preludes of a stylesheet (not @-rule preludes, not @keyframes steps)."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'", '""', text)
    out, stack, last = [], [], 0
    for i, ch in enumerate(text):
        if ch == "{":
            prelude = text[last:i].strip()
            if stack and stack[-1] != "group":
                stack.append("decl")
            elif prelude.startswith("@keyframes"):
                stack.append("keyframes")
            elif prelude.startswith(("@media", "@supports", "@container", "@layer")):
                stack.append("group")
            elif prelude.startswith("@"):
                stack.append("decl")
            else:
                out.append(prelude)
                stack.append("decl")
            last = i + 1
        elif ch == "}":
            if stack:
                stack.pop()
            last = i + 1
        elif ch == ";" and (not stack or stack[-1] == "group"):
            last = i + 1
    return out


class Names:
    """What the sources write and read, per kind."""

    def __init__(self) -> None:
        self.class_written: set[str] = set()
        self.class_literal: dict[str, set[str]] = {}       # literal template class -> templates
        self.class_read: set[str] = set()
        self.id_defined: set[str] = set()
        self.id_prefixes: set[str] = set()
        self.id_pointed: dict[str, set[str]] = {}
        self.data_written: dict[str, set[str]] = {}
        self.data_read: set[str] = set()
        self.data_test_read: set[str] = set()
        self.class_prefixes: set[str] = set()
        self.pending_prefix: list[tuple[str, str]] = []
        self.id_suffixes: set[str] = set()
        self.pending_suffix: list[tuple[str, str]] = []
        self.all_names: dict[str, set[tuple[str, str]]] = {"class": set(), "id": set(), "data": set()}

    def point(self, name: str, where: str) -> None:
        self.id_pointed.setdefault(name, set()).add(where)

    def stylesheet(self, text: str, where: str) -> None:
        # a declaration reads a data-* attribute too: content: attr(data-label)
        self.data_read.update(re.findall(r"attr\(\s*data-([\w-]+)", re.sub(r"/\*.*?\*/", " ", text, flags=re.S)))
        for selector in css_selectors(text):
            for c, i, d in SELECTOR.findall(selector):
                if c:
                    self.class_read.add(c)
                    self.all_names["class"].add((c, where))
                elif i:
                    self.point(i, where)
                elif d:
                    self.data_read.add(d)

    def template(self, text: str, where: str) -> None:
        text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
        for attr, value in template_attrs(text):
            static = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " \x00 ", value, flags=re.S)
            strings = [s for e in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", value, flags=re.S)
                       for s in re.findall(r"'([^']*)'|\"([^\"]*)\"", e) for s in s if s]
            if attr == "class":
                for prefix in re.findall(r"([\w-]+-) \x00", static):
                    self.class_prefixes.add(prefix)          # dash-{{ card.width }}
                static = re.sub(r"[\w-]+- \x00", " \x00", static)
                for token in static.split():
                    if token != "\x00":
                        self.class_written.add(token)
                        self.class_literal.setdefault(token, set()).add(where)
                        self.all_names["class"].add((token, where))
                for s in strings:
                    self.class_written.update(s.split())
            elif attr == "id":
                if static.lstrip().startswith("\x00"):
                    self.id_suffixes.add(static.split("\x00")[-1].strip())     # id="{{ prefix }}-path"
                first = static.split("\x00")[0].strip()
                if first and "\x00" in static:
                    self.id_prefixes.add(first)
                elif first:
                    self.id_defined.add(first)
                for part in re.findall(r"[\w-]+", static):
                    self.all_names["id"].add((part, where))
            elif attr == "href":
                frag = re.search(r"#([\w-]+)", static.split("\x00")[0])
                if frag and "\x00" not in static[static.find("#"):]:
                    self.point(frag.group(1), where)
            elif attr.startswith("data-"):
                self.data_written.setdefault(attr[5:], set()).add(where)
                if attr[5:] in ID_DATA:                     # the value names an element by its id
                    start, end = static.split("\x00")[0].strip(), static.split("\x00")[-1].strip()
                    if "\x00" not in static and start:
                        self.point(start, where)
                    elif start:
                        self.pending_prefix.append((start, where))
                    elif end:
                        self.pending_suffix.append((end, where))
                self.all_names["data"].add((attr[5:], where))
            else:
                # a pointer built with an expression counts by its static start (a prefix of an id)
                if "\x00" in static:
                    start = static.split("\x00")[0].strip()
                    if start and not any(p.startswith(start) or start.startswith(p) for p in self.id_prefixes):
                        self.pending_prefix.append((start, where))
                    continue
                for token in static.split():
                    self.point(token, where)
        # an SVG reference url(#clip) and a fragment built in an expression ("/album/" ~ x ~ "#track-" ~ y)
        for frag in re.findall(r"url\(#([\w-]+)\)", text):
            self.point(frag, where)
        for expr in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", text, flags=re.S):
            for frag in re.findall(r"[\"']#([\w-]+)[\"']", expr):
                if frag.endswith("-"):
                    self.pending_prefix.append((frag, where))
                else:
                    self.point(frag, where)
        # every word of a template string may be a class handed to a macro (poster(…, "poster-frame obal"))
        for string in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", text, flags=re.S):
            for part in re.findall(r"'([^']*)'|\"([^\"]*)\"", string):
                self.class_written.update(w for p in part for w in re.findall(r"[\w-]+", p))
        # a data-* name a macro writes from a string, and modal ids handed to data attributes
        for m in re.finditer(r"(?<![\w-])data-([\w-]+)", text):
            self.data_written.setdefault(m.group(1), set()).add(where)

    def script(self, text: str, where: str) -> None:
        for m in JS_TOKEN.finditer(text):
            token = m.group()
            if token.startswith(("//", "/*")):
                continue
            body = token[1:-1]
            before = text[max(0, m.start() - 60):m.start()].rstrip()
            if re.search(r"(querySelector(All)?|closest|matches)\(\s*$", before):
                for c, i, d in SELECTOR.findall(body):
                    if c:
                        self.class_read.add(c)
                    elif i:
                        self.point(i, where)
                    elif d:
                        self.data_read.add(d)
            elif re.search(r"classList\.(contains)\(\s*$", before):
                self.class_read.update(body.split())
            elif re.search(r"getElementById\(\s*$", before) and re.fullmatch(r"[\w-]+", body):
                self.point(body, where)
            elif re.search(r"(get|has)Attribute\(\s*$", before) and body.startswith("data-"):
                self.data_read.add(body[5:])
            elif re.search(r"(set|toggle)Attribute\(\s*$", before) and body.startswith("data-"):
                self.data_written.setdefault(body[5:], set()).add(where)
            # every word of every string may be a class handed on (classList.add, className, a helper)
            self.class_written.update(re.findall(r"[\w-]+", body))
            if body.endswith("-") and re.match(r"\s*\+", text[m.end():]):
                self.class_prefixes.add(body)                 # "v-" + layer
            for d in re.findall(r"(?<![\w-])data-([\w-]+)", body):
                self.data_written.setdefault(d, set()).add(where)
            if re.search(r"\.id\s*=\s*$", before) and re.fullmatch(r"[\w-]+", body):
                self.id_defined.add(body)
        code = JS_TOKEN.sub(lambda m: " " * len(m.group()), text)
        for m in re.finditer(r"\.dataset\.([A-Za-z_]\w*)(\s*=(?!=))?", code):
            name = kebab(m.group(1))
            if m.group(2):
                self.data_written.setdefault(name, set()).add(where)
            else:
                self.data_read.add(name)

    def python(self, text: str, where: str) -> None:
        # Python builds classes in f-strings and dicts too (charts.py): every word of a string may be one
        for string in re.findall(r"\"([^\"\n]*)\"|'([^'\n]*)'", text):
            self.class_written.update(w for part in string for w in re.findall(r"[\w-]+", part))
        self.id_defined.update(re.findall(r"wrapper_id\s*=\s*\"([\w-]+)\"", text))
        for attr, value in STRING_ATTR.findall(text):
            if attr == "class":
                self.class_written.update(value.split())
            elif attr == "id":
                self.id_defined.add(value)
            else:
                self.data_written.setdefault(attr[5:], set()).add(where)

    def tests(self, text: str) -> None:
        """Tests read data-* attributes (checking the markup) - such a name counts as read."""
        self.data_test_read.update(re.findall(r"(?<![\w-])data-([\w-]+)", text))
        self.data_test_read.update(kebab(n) for n in re.findall(r"dataset\.([A-Za-z_]\w*)", text))


def read_sources() -> Names:
    names = Names()
    for path in sorted(STATIC.rglob("*")):
        rel = path.relative_to(PROJECT).as_posix()
        if path.suffix == ".css":
            names.stylesheet(path.read_text(encoding="utf-8"), rel)
        elif path.suffix == ".js":
            names.script(path.read_text(encoding="utf-8"), rel)
    for path in sorted(TEMPLATES.rglob("*.html")):
        names.template(path.read_text(encoding="utf-8"), path.relative_to(PROJECT).as_posix())
    for path in sorted((PROJECT / "jellyscope").rglob("*.py")):
        names.python(path.read_text(encoding="utf-8"), path.relative_to(PROJECT).as_posix())
    for path in sorted((PROJECT / "tests").glob("*.py")):
        if path.name != Path(__file__).name:
            names.tests(path.read_text(encoding="utf-8"))
    return names


def data_problems(n: Names, written_only: dict[str, str],
                  read_only: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    unwritten = sorted(d for d in n.data_read if d not in n.data_written and d not in (read_only or {}))
    unread = sorted(d for d in n.data_written if d not in n.data_read | n.data_test_read and d not in written_only)
    return unwritten, unread


def class_problems(n: Names, hooks: dict[str, str]) -> tuple[list[str], list[str]]:
    unwritten = sorted(c for c in n.class_read if c not in n.class_written
                       and not any(c.startswith(p) for p in n.class_prefixes))
    unstyled = sorted(c for c in n.class_literal if c not in n.class_read and c not in hooks)
    return unwritten, unstyled


def id_problems(n: Names) -> list[str]:
    lost = [f"{i} ({', '.join(sorted(w))})" for i, w in n.id_pointed.items()
            if i not in n.id_defined and not any(i.startswith(p) for p in n.id_prefixes)]
    lost += [f"{start}* ({where})" for start, where in n.pending_prefix
             if not any(p.startswith(start) or start.startswith(p) for p in n.id_prefixes)]
    lost += [f"*{end} ({where})" for end, where in n.pending_suffix if end not in n.id_suffixes]
    return sorted(lost)


def custom_properties(sources: dict[str, str]) -> tuple[set[str], set[str], set[str], set[str]]:
    """(defined, set, read, set prefixes) custom properties over stylesheets and other sources."""
    defined, set_, read, prefixes = set(), set(), set(), set()
    for where, text in sources.items():
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S) if where.endswith(".css") else text
        read |= set(re.findall(r"var\(\s*(--[\w-]+)", text))
        if where.endswith(".css"):
            defined |= set(re.findall(r"(?<![\w-])(--[\w-]+)\s*:", text))
        else:
            set_ |= set(re.findall(r"(?<![\w-])(--[\w-]+)[\"']?\s*:", text))      # style="--x: …", {"--x": …}
            for name in re.findall(r"setProperty\(\s*[\"'](--[\w-]+)[\"']", text):
                set_.add(name)
        for name in re.findall(r"[\"'](--[\w-]+-)[\"']\s*\+", text):
            prefixes.add(name)                       # "--series-" + n
    return defined, set_, read, prefixes


def missing_properties(sources: dict[str, str], undefined_ok: dict[str, str] | None = None) -> list[str]:
    defined, set_, read, prefixes = custom_properties(sources)
    return sorted(r for r in read if r not in defined | set_ and not any(r.startswith(p) for p in prefixes)
                  and not r.endswith("-") and r not in (undefined_ok or {}))


def static_addresses(sources: dict[str, str]) -> list[tuple[str, str]]:
    """Every /static/… address named in the sources: (address, where); a folder when completed at run time."""
    out = []
    for where, text in sources.items():
        for m in re.finditer(r"/static/[\w./-]*", text):
            address = m.group().rstrip(".")
            out.append((address, where))
    return out


def missing_static(sources: dict[str, str], static_root: Path) -> list[str]:
    lost = set()
    for address, where in static_addresses(sources):
        path = static_root / address.removeprefix("/static/")
        if not (path.is_file() or (address.endswith("/") and path.is_dir())):
            lost.add(f"{address} ({where})")
    return sorted(lost)


def texts_block_keys(templates: dict[str, str]) -> set[str]:
    """Keys of every #texts-js block (a dict literal handed to tojson)."""
    keys = set()
    for text in templates.values():
        for block in re.findall(r'id="texts-js">\{\{\s*\{(.*?)\}\s*\|\s*tojson', text, re.S):
            keys |= set(re.findall(r'"(\w+)"\s*:', block))
    return keys


def game_text_keys(template: str) -> tuple[dict[str, set[str]], set[str]]:
    """(per holiday: its text keys, the keys the template adds to every holiday) of _event_game.html."""
    block = re.search(r"set games_texts = \{(.*?)\} %\}", template, re.S)
    per = {h: set(re.findall(r'"(\w+)"\s*:', body)) for h, body in
           re.findall(r'"(\w+)"\s*:\s*\{(.*?)\}', block.group(1) if block else "", re.S)}
    added = set(re.findall(r"dict\(\s*(?:games_texts\[holiday\]|game_texts)\s*,\s*(\w+)\s*=", template))
    return per, added


def missing_texts(scripts: dict[str, str], templates: dict[str, str], game_template: str) -> list[str]:
    lost = []
    block_keys = texts_block_keys(templates)
    per, added = game_text_keys(game_template)
    common = set.intersection(*per.values()) if per else set()
    code = {where: JS_TOKEN.sub(lambda m: " " * len(m.group()), text) for where, text in scripts.items()}
    for where, text in code.items():
        for key in re.findall(r"\bTEXTS\.(\w+)", text):
            if key not in block_keys:
                lost.append(f"{where}: TEXTS.{key}")
        game = re.search(r"games/(\w+)\.js$", where)
        for key in re.findall(r"\bapi\.texts\.(\w+)", text):
            if not game or key not in per.get(game.group(1), set()) | added:
                lost.append(f"{where}: api.texts.{key}")
        if where.endswith("events/events.js"):
            for key in re.findall(r"(?<![\w.])texts\.(\w+)", text):
                if key not in common | added:
                    lost.append(f"{where}: texts.{key}")
    for key in re.findall(r"\bgame_texts\.(\w+)", game_template):
        if key not in common | added:
            lost.append(f"_event_game.html: game_texts.{key}")
    return sorted(set(lost))


def czech_names(n: Names, kinds: set[str], parts: set[str], kept: set[str]) -> list[str]:
    out = []
    for kind in kinds & set(n.all_names):
        for name, where in n.all_names[kind]:
            if name in kept:
                continue
            if set(re.split(r"[-_]", name)) & parts:
                out.append(f"{kind} {name} ({where})")
    return sorted(set(out))


# data-* attributes whose value is the id of another element (a modal to open, a field to copy, the
# suffix of the path fields' ids a script numbers).
ID_DATA = {"modal", "modal-load", "filter-modal", "open-modal", "copy", "paths"}
# data-* names written on purpose though no script, stylesheet or test reads them.
WRITTEN_ONLY: dict[str, str] = {
    "y-from": "charts.py marks the plot's top for a later script; nothing reads it yet",
    "y-to": "charts.py marks the plot's bottom for a later script; nothing reads it yet",
}
# data-* names a script reads though nothing writes them now.
READ_ONLY: dict[str, str] = {}
# Literal template classes without a rule or a script: the markup's meaning or a test reads them.
HOOKS: dict[str, str] = {
    "dash-no-permission": "a test finds the card without the right by it",
    "filter-chips": "names the chips of the filter; styled through .chip",
    "hero-card": "names the hero card; styled through .card",
    "hero-number": "names the hero number; styled by its parent",
    "period-form": "a test finds the period form by it",
    "page-note": "names the note under a page heading",
    "s1": "the first of the s1/s2/s3 lights; only s2 and s3 get a delay",
    "downloading": "tests find the downloading card by it",
    "tree-group": "names a group of the rights tree; role=group carries it",
    "live-card": "names the live card of the network page",
}
# Custom properties read though nothing defines or sets them - older than step 10c1, reported to the user;
# a fix would change the look, which a rename must not.
UNDEFINED_PROPERTIES: dict[str, str] = {}
# Kinds whose Czech names are gone (step 10c1 adds them one by one: data, id, class).
DONE_KINDS: set[str] = {"data", "id", "class", "file", "css"}
# The words of the Czech names steps 10c1 and 10c2 renamed (a part of a name between - or _).
CZECH_PARTS: set[str] = {
    "adresy", "akce", "aktivni", "aktualizace", "alba", "aplikace", "archivu", "beh", "bez", "bo", "bod",
    "bunka", "carky", "cas", "cely", "cepice", "cesta", "cestu", "cesty", "chipy", "chybe", "cil", "cile",
    "cislo", "cizi", "ctenari", "dal", "dalsi", "darek", "datum", "den", "detailu", "disky", "divaci",
    "divaka", "dl", "dlazdice", "dnes", "dnu", "dny", "do", "dokumentace", "drzi", "duvod", "duzina", "dyne",
    "efekt", "filtr", "fokus", "graf", "heslo", "hlaseni", "hlaska", "hlasku", "hlava", "hlavni", "hledani",
    "hodina", "hotovo", "hra", "hranice", "husta", "hvezda", "jazyku", "jazyky", "je", "jednotkou", "jen",
    "jeviste", "jezera", "jiskra", "jiskry", "kalendar", "kanaly", "kapacita", "karta", "kde", "kdekoli",
    "kdo", "klic", "klice", "knihovny", "kod", "koho", "kombinace", "komu", "konfeta", "kopirovat", "kraje",
    "krok", "kroky", "lahev", "lam", "licence", "lze", "mapa", "mazani", "mesic", "mesta", "minut", "miste",
    "misto", "mobil", "motiv", "mrizka", "muj", "na", "nabidka", "nacist", "nacteno", "nad", "nadpis",
    "nahled", "nahoru", "nalezy", "napoveda", "nastaveni", "nastroje", "navic", "nebo", "nedostupna", "neni",
    "nesbira", "netopyr", "neurcity", "nevyuzite", "nit", "nova", "novy", "obal", "obnovit", "obrazku",
    "obsah", "oci", "od", "odchazi", "odebrat", "odesilatel", "odeslat", "odkaz", "odklizeni", "okna", "okne",
    "okno", "otevrena", "otevrit", "ovladani", "oz", "ozdoby", "oznaceno", "pata", "pavouk", "pena", "pevnina",
    "plakat", "plakatek", "plakaty", "plaminek", "platno", "plocha", "pod", "podklad", "podle", "podpis",
    "podrobna", "pohyb", "polozka", "popis", "popisek", "popisky", "poradi", "posuvne", "potvrdit", "pouziti",
    "pozadi", "poznamky", "prani", "praskliny", "prava", "prazdno", "predmet", "prehled", "preklady",
    "prepinac", "prepnout", "prepoctu", "prevodu", "pri", "priblizena", "pridat", "priradit", "prirazeni",
    "promenne", "pryc", "radek", "radky", "raketa", "rakev", "ram", "rekap", "rekapitulace", "reky", "rok",
    "rozbusky", "rozhrani", "rozvrzeni", "rust", "rychle", "rychlost", "sablona", "sampanske", "scena", "se",
    "sekce", "sekci", "seznam", "sipka", "sirka", "sirky", "siroke", "siroky", "sit", "skladba", "skladby",
    "skupina", "sloupce", "sloupec", "slozky", "smazat", "smi", "souboru", "souhrn", "srovnani", "stahovani",
    "stahuje", "staticky", "stav", "stejny", "streamu", "streamy", "strom", "stromecek", "svatozar", "svet",
    "svetlo", "svitici", "tabulka", "tahne", "tazena", "ted", "texty", "ticho", "titul", "tlacitka",
    "tlacitko", "tma", "tydne", "ucet", "ucho", "uctu", "udalost", "udalosti", "ukazky", "uklid", "ulohy",
    "ulozit", "upir", "urovne", "usi", "uzivatel", "uzky", "vajicko", "vejce", "velka", "velky", "verze",
    "vice", "videt", "vidi", "vika", "vlastni", "vlocka", "volba", "volby", "vyber", "vybirat", "vybrane",
    "vybrano", "vybrany", "vybrat", "vychozi", "vyrez", "vyrezy", "vyvoj", "vyzdoba", "vyzva", "zajic",
    "zamceno", "zapalovac", "zapomenout", "zapomenut", "forgettings", "zarizeni", "zatka", "zavrit", "zbytek",
    "zdroj", "zive", "zmene", "znak", "zony", "zpet", "zrusit",
    "barva", "bila", "blik", "cuknuti", "houpani", "hry", "kyv", "let", "linka", "mavani", "obrys", "padani",
    "plamen", "prichod", "prihlaseni", "silvestr", "skok", "snih", "tep", "tmava", "tres", "trpyt", "ucty",
    "vanoce", "velikonoce", "vrti", "zaklad",
    # step 10c2: words of the hooks, custom properties and @keyframes renamed then
    "hloubka", "priklady", "zare", "pozn", "tecka", "tep"
}
# Names kept until a later step: equal to a form field's name.
KEPT: set[str] = set()      # none left after the database step 5

names = read_sources()
print("--- data-* attributes ---")
unwritten_data, unread_data = data_problems(names, WRITTEN_ONLY, READ_ONLY)
check(len(names.data_read) > 50 and not unwritten_data,
      f"(m1) every data-* name a script or a stylesheet reads is written somewhere ({unwritten_data[:8]})")
check(not unread_data, f"(m1) every data-* name a template writes is read somewhere ({unread_data[:8]})")
stale = sorted([f"WRITTEN_ONLY {d}" for d in WRITTEN_ONLY if d not in names.data_written or d in names.data_read]
               + [f"READ_ONLY {d}" for d in READ_ONLY if d not in names.data_read or d in names.data_written]
               + [f"HOOKS {c}" for c in HOOKS if c not in names.class_literal or c in names.class_read])
_props = custom_properties({path.relative_to(PROJECT).as_posix(): path.read_text(encoding="utf-8")
                            for base, pattern in ((STATIC, "*.css"), (STATIC, "*.js"), (TEMPLATES, "*.html"),
                                                  (PROJECT / "jellyscope", "*.py")) for path in sorted(base.rglob(pattern))})
stale += sorted(f"UNDEFINED_PROPERTIES {x}" for x in UNDEFINED_PROPERTIES if x not in _props[2] or x in _props[0] | _props[1])
check(not stale, f"no exception of the guard is stale - each still names a real case ({stale})")
print("--- classes ---")
unwritten_classes, unstyled_classes = class_problems(names, HOOKS)
check(len(names.class_read) > 200 and not unwritten_classes,
      f"(m2) every class a stylesheet or a script reads is written somewhere ({unwritten_classes[:8]})")
check(not unstyled_classes, f"(m2) every literal class of a template has a rule or a script ({unstyled_classes[:8]})")
print("--- ids ---")
lost_ids = id_problems(names)
check(len(names.id_pointed) > 30 and not lost_ids, f"(m3) every id pointed at exists ({lost_ids[:8]})")
print("--- Czech names ---")
czech = czech_names(names, DONE_KINDS, CZECH_PARTS, KEPT)
check(not czech, f"(m4) no Czech name in the finished kinds {sorted(DONE_KINDS)} ({czech[:8]})")

print("--- custom properties and static addresses ---")
sources = {path.relative_to(PROJECT).as_posix(): path.read_text(encoding="utf-8")
           for base, pattern in ((STATIC, "*.css"), (STATIC, "*.js"), (TEMPLATES, "*.html"), (PROJECT / "jellyscope", "*.py"))
           for path in sorted(base.rglob(pattern))}
lost_properties = missing_properties(sources, UNDEFINED_PROPERTIES)
check(len(custom_properties(sources)[2]) > 30 and not lost_properties,
      f"(m5) every custom property read with var() is defined or set ({lost_properties[:8]})")
lost_static = missing_static(sources, STATIC)
check(len(static_addresses(sources)) > 10 and not lost_static, f"(m6) every /static/ address exists ({lost_static[:8]})")
static_names = {(part, path.relative_to(PROJECT).as_posix()) for path in STATIC.rglob("*")
                for part in [path.stem if path.is_file() else path.name]}
css_texts = {where: text for where, text in sources.items() if where.endswith(".css")}
css_texts.update({f"{where} <style>": "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style>", text, re.S))
                  for where, text in sources.items() if not where.endswith(".css") and "<style" in text})
css_names = {(name, where) for where, text in css_texts.items()
             for name in re.findall(r"(?<![\w-])--([\w-]+)\s*:", text) + re.findall(r"@keyframes\s+([\w-]+)", text)}
czech_static = sorted(f"{name} ({where})" for kind, found in (("file", static_names), ("css", css_names))
                      if kind in DONE_KINDS for name, where in found
                      if set(re.split(r"[-_]", name)) & CZECH_PARTS and name not in KEPT)
check(not czech_static, f"(m4) no Czech name of a static file (kind file), custom property or @keyframes (kind css) "
      f"({czech_static[:8]})")

print("--- texts read by their keys ---")
scripts = {path.relative_to(PROJECT).as_posix(): path.read_text(encoding="utf-8") for path in sorted(STATIC.rglob("*.js"))}
templates = {path.relative_to(PROJECT).as_posix(): path.read_text(encoding="utf-8")
             for path in sorted(TEMPLATES.rglob("*.html"))}
game_template = (TEMPLATES / "_event_game.html").read_text(encoding="utf-8")
lost_texts = missing_texts(scripts, templates, game_template)
check(len(texts_block_keys(templates)) >= 4 and len(game_text_keys(game_template)[0]) == 4 and not lost_texts,
      f"(m7) every text read by its key exists in its block ({lost_texts[:8]})")

print("--- traps ---")
trap_game = ('{%- set games_texts = {"a": {"wish": 1, "hint": 2}, "b": {"hint": 3}} %}'
             '{%- set game_texts = dict(games_texts[holiday], close=1) %}<i aria-label="{{ game_texts.wish }}">')
trap_found = missing_texts({"static/js/x.js": "TEXTS.done; TEXTS.gone; // TEXTS.comment",
                            "static/events/games/b.js": "api.texts.hint; api.texts.wish; api.texts.close",
                            "static/events/events.js": "texts.hint; texts.wish"},
                           {"t.html": '<script type="application/json" id="texts-js">{{ {"done": 1} | tojson }}</script>'},
                           trap_game)
check(trap_found == ["_event_game.html: game_texts.wish", "static/events/events.js: texts.wish",
                     "static/events/games/b.js: api.texts.wish", "static/js/x.js: TEXTS.gone"],
      f"(m7) a text key that its block lacks is found in a script, a game, events.js and the template ({trap_found})")
trap_sources = {"a.css": ":root { --have: 1; } .x { color: var(--have); fill: var(--gone); } "
                         ".y { background: url(/static/missing.png); }",
                "b.js": 'el.style.setProperty("--set-here", 1); x.style.color = "var(--set-here)";'
                        ' var c = "var(--series-" + n + ")"; load("/static/js/app.js"); load("/static/nowhere/" + k);',
                "c.html": '<i style="--inline: 2; color: var(--inline)"></i>'}
check(missing_properties(trap_sources) == ["--gone"],
      f"(m5) a custom property read but never defined or set is found ({missing_properties(trap_sources)})")
check([x.split(" ")[0] for x in missing_static(trap_sources, STATIC)] == ["/static/missing.png", "/static/nowhere/"],
      f"(m6) an address of a missing static file or folder is found ({missing_static(trap_sources, STATIC)})")
trap = Names()
trap.stylesheet(".styled { } .ghost-rule { } [data-read-only] { } #nowhere { } .styled::after { content: attr(data-by-attr); }",
                "t.css")
trap.template('<svg><g clip-path="url(#clip-gone)"></g></svg>{{ link("/a" ~ "#frag-" ~ n) }}'
              '<div class="styled bare" id="here" data-written-only="1"><label for="missing"></label>'
              '<a href="#here"></a><a href="/x#gone"></a><i id="row-{{ n }}"></i></div>'
              '<button data-modal="dialog-x"></button><button data-copy="key-{{ k }}"></button>'
              '<input id="{{ p }}-path"><div data-paths="{{ p }}-cesta"></div>', "t.html")
trap.script('document.querySelector(".styled"); el.dataset.readHere; document.getElementById("row-7");'
            'document.getElementById("absent");', "t.js")
check(data_problems(trap, {}) == (["by-attr", "read-here", "read-only"], ["copy", "modal", "paths", "written-only"]),
      f"(m1) a data-* name read but not written and one written but not read are found {data_problems(trap, {})}")
check(class_problems(trap, {}) == (["ghost-rule"], ["bare"]),
      f"(m2) a class read but not written and a literal class without a rule are found {class_problems(trap, {})}")
check([p.split(" ")[0] for p in id_problems(trap)] == ["*-cesta", "absent", "clip-gone", "dialog-x", "frag-*", "gone",
                                                       "key-*", "missing", "nowhere"],
      f"(m3) an id pointed at by for=, #fragment, getElementById or a selector that exists nowhere is found "
      f"{id_problems(trap)}")
check(czech_names(trap, {"class", "data"}, {"bare", "written"}, set()) == ["class bare (t.html)",
                                                                           "data written-only (t.html)"],
      "(m4) a name with a Czech part is found in a finished kind")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
