# -*- coding: utf-8 -*-
r"""The app's addresses are English (part 10d1 of the English-code spec).

The final version of the app is English except the translations of the GUI (the user's
decision of 20. 11. 2026). An address is a name the outside world sees - a bookmark, a
form's target, a script's fetch - so a renamed Czech address gets no redirect: the old one
answers 404, and a link still pointing at it would break. This guards both sides:

  (a1) no route path (redirects included) has a Czech part: a word of ADDRESS_CZECH_PARTS
       between slashes or dashes, a path parameter's name included, or a letter with
       diacritics; and every path with a method exists once.
  (a2) no address written in jellyscope/** (templates, scripts, Python - comments and
       docstrings too), `README.md` or `JAK-TO-FUNGUJE.md` has such a part - also as a piece
       of an address put together at run time ("/partials/arr/" + app + "/stahuje-se").

The redirects from the old addresses stay for a while (jellyscope/legacy_urls.py, their removal
is in TODO.md): Czech is allowed there and in their routes only.

Run:
    .venv/bin/python tests/test_addresses.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "addresses.db")
os.environ["SECRET_KEY"] = "test-key-addresses"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _package import flat_routes  # noqa: E402
from jellyscope import api, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Czech words of addresses: those the app used (part 10d1's map and the old menu addresses).
ADDRESS_CZECH_PARTS: set[str] = {
    "archiv", "bez", "ctenari", "datum", "disky", "divaci", "dlazdice", "hledat", "historie", "hudba", "interpret",
    "jazyka", "kapacita", "klic", "klice", "mapa", "obrazek", "plakat", "podrobna", "podrobnost", "pohyb",
    "prava", "prehled", "priradit", "rozhodnuti", "rust", "smazat", "srovnani", "stahnout", "stahuje",
    "stav", "stejne", "titul", "typ", "udalost", "udalosti", "upravit", "virtualni", "vrat", "vypnout",
    "vyvoj", "zapnout", "zapomen", "zrusit",
}
DIACRITICS = re.compile("[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")


def address_words(path: str) -> set[str]:
    """The words of an address's path (the query is part 10d2's): parts between slashes, dashes and
    underscores, path parameter names included."""
    return {w for w in re.split(r"[/_{}\-.]+", re.split(r"[?#]|&amp;", path, maxsplit=1)[0].lower()) if w}


def czech_parts(path: str, parts: set[str] = ADDRESS_CZECH_PARTS) -> set[str]:
    found = address_words(path) & parts
    bare = re.split(r"[?#]", path, maxsplit=1)[0]
    return found | ({DIACRITICS.search(bare).group()} if DIACRITICS.search(bare) else set())


# A written address: "/" at the start of a line or after a quote, a backtick, a bracket, "=", "+", ">"
# or a space, and before word characters; it runs over Jinja and f-string holes ({{ x }}, {app}) and
# stops at a quote, a space or "<".
WRITTEN = re.compile(r"""(?:^|(?<=["'`(=+ >]))/[A-Za-z{][^\s"'`<>()]*""", re.M)


def written_czech(text: str, where: str) -> list[str]:
    found = []

    def scan(piece: str, source: str, offset: int) -> None:
        for m in WRITTEN.finditer(piece):
            address = m.group()
            parts = czech_parts(address)
            if parts:
                found.append(f"{where}:{source.count(chr(10), 0, offset + m.start()) + 1}: {address[:70]}")

    holes = re.compile(r"\{\{[^\n]*?\}\}|\{%[^\n]*?%\}")       # Jinja tags, one line each
    for hole in holes.finditer(text):                  # addresses written inside a tag: link("/x/" ~ id)
        scan(hole.group()[2:-2], text, hole.start() + 2)
    # Outside the tags a tag stands for a path segment: href="/library/{{ lib.id }}/archive".
    outside = holes.sub("{x}", text)
    scan(outside, outside, 0)
    return found


print("--- (a1) routes ---")
routes = [(r.path, sorted(getattr(r, "methods", None) or [])) for r in flat_routes(web.app.router.routes) if hasattr(r, "path")]
# The redirects from old addresses stay for a while (legacy_urls.py; their removal is in TODO.md):
# Czech there, and only there.
legacy = {r.path for r in flat_routes(web.app.router.routes) if getattr(r, "name", "") == "legacy_redirect"}
czech_routes = sorted(f"{p} {czech_parts(p)}" for p, _ in routes
                      if czech_parts(p) and p not in legacy)
check(len(routes) > 100 and not czech_routes, f"(a1) no route has a Czech part ({len(czech_routes)}: {czech_routes[:6]})")
seen: dict[tuple[str, str], int] = {}
for path, methods in routes:
    for method in methods or ["*"]:
        seen[(path, method)] = seen.get((path, method), 0) + 1
twice = sorted(f"{m} {p}" for (p, m), n in seen.items() if n > 1 and m != "HEAD")
check(not twice, f"(a1) every path with a method is one route ({twice[:6]})")

print("--- (a2) written addresses ---")
sources = sorted(p for p in (PROJECT / "jellyscope").rglob("*")
                 if p.suffix in (".py", ".html", ".js", ".css") and "__pycache__" not in p.parts)
sources += [PROJECT / "README.md", PROJECT / "JAK-TO-FUNGUJE.md"]
sources += sorted((PROJECT / "jellyscope" / "translations").glob("*.json"))     # texts can name addresses too
lost = []
for path in sources:
    if path.is_file() and path != PROJECT / "jellyscope" / "legacy_urls.py":
        lost += written_czech(path.read_text(encoding="utf-8"), path.relative_to(PROJECT).as_posix())
check(len(sources) > 100 and not lost, f"(a2) no written address has a Czech part ({len(lost)}: {lost[:6]})")

print("--- (a3, a4) address parameters ---")
# Part 10d2: the names of address parameters and their code values (the old Czech ones).
PARAM_CZECH: set[str] = {"od", "do", "a_od", "a_do", "b_od", "b_do", "od_ts", "do_ts", "obdobi", "rust_disk",
                         "log_druh", "log_klic", "log_ucet", "priradit", "soubor", "verze", "vyvoj_chyba"}
VALUE_CZECH = re.compile(r"[?&](?:amp;)?(?:period|obdobi|growth_disk|rust_disk)=(?:vse|dnes|vlastni)\b"
                         r'|\bdata-growth-disk="vse"')


def czech_param(name: str) -> bool:
    words = set(name.lower().split("_"))
    return bool(name in PARAM_CZECH or words & ADDRESS_CZECH_PARTS or DIACRITICS.search(name))


def read_params() -> set[str]:
    """Names of address parameters the app reads: route arguments from the query and query_params.get()."""
    names = set()
    for r in flat_routes(web.app.router.routes):
        stack = [r.dependant] if hasattr(r, "dependant") else []
        while stack:
            d = stack.pop()
            stack += d.dependencies
            names |= {x.alias for x in d.query_params}
    for path in (PROJECT / "jellyscope").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r"""query_params(?:\.get(?:list)?\(\s*|\[\s*)["'](\w+)["']""", text))
        # Read through a name bound to the query: query = request.query_params; query.get("since").
        for alias in set(re.findall(r"(\w+)\s*=\s*request\.query_params\b", text)):
            names |= set(re.findall(rf"""\b{alias}(?:\.get(?:list)?\(\s*|\[\s*)["'](\w+)["']""", text))
    names |= set().union(*api.KNOWN_PARAMS.values())      # the API's own plain parameters
    for path in (PROJECT / "jellyscope" / "templates").rglob("*.html"):     # a template reads one too
        names |= set(re.findall(r"""request\.query_params\.get\(\s*["'](\w+)["']""", path.read_text(encoding="utf-8")))
    return names


def written_params(text: str, where: str) -> list[str]:
    """Old parameter names and values written: in a query (?od=, &amp;od=), as a field of a template's
    form (name="od"), as a script's string ("od_ts" for searchParams); an old value (?obdobi=vse)."""
    found = []
    for m in re.finditer(r"[?&](?:amp;)?([A-Za-z_]\w*)=", text):
        if m.group(1) in PARAM_CZECH:
            found.append(f"{where}:{text.count(chr(10), 0, m.start()) + 1}: ?{m.group(1)}=")
    if where.endswith(".html"):
        for m in re.finditer(r'\bname="(\w+)"', text):
            if m.group(1) in PARAM_CZECH:
                found.append(f"{where}:{text.count(chr(10), 0, m.start()) + 1}: name={m.group(1)}")
        # A name put together in the template: name="{{ prefix }}_od" (the Comparison's two periods).
        for m in re.finditer(r'\bname="[^"]*\}\}_(od|do)"', text):
            found.append(f"{where}:{text.count(chr(10), 0, m.start()) + 1}: name=…_{m.group(1)}")
    if where.endswith(".js"):
        for m in re.finditer(r"""["'](\w+)["']""", text):
            if m.group(1) in PARAM_CZECH:
                found.append(f"{where}:{text.count(chr(10), 0, m.start()) + 1}: '{m.group(1)}'")
    for m in VALUE_CZECH.finditer(text):
        found.append(f"{where}:{text.count(chr(10), 0, m.start()) + 1}: {m.group()}")
    return found


czech_read = sorted(n for n in read_params() if czech_param(n))
check(not czech_read, f"(a3) no address parameter the app reads has a Czech name ({czech_read})")
lost_params = []
for path in sources:
    if path.is_file() and path != PROJECT / "jellyscope" / "legacy_urls.py":
        lost_params += written_params(path.read_text(encoding="utf-8"), path.relative_to(PROJECT).as_posix())
check(not lost_params, f"(a4) no old parameter or value is written ({len(lost_params)}: {lost_params[:6]})")

print("--- (a5, a6) form fields ---")
# Part 10d3: the names of form fields and their code values (the old Czech ones). The fields saved under
# their own name as a setting key stayed Czech until the database step 5 (FIELD_KEPT, empty since).
FIELD_CZECH: set[str] = {
    "zpusob", "rezim", "anonymizace", "historie", "smi_klic", "vidi_*",
    "kapacita", "jednotka", "zdroj", "nazev", "cesta", "odpoved", "cil", "cil_*", "potvrzeno", "smazat",
    "poradi", "pohyb", "udalost", "datum",
    "zapnuto", "kanal", "text_*_predmet", "smtp_heslo", "smtp_komu", "smtp_odesilatel", "smtp_uzivatel",
    "event_misto", "event_sberac", "event_souhrn", "misto_dnu", "souhrn_den", "souhrn_hodina", "souhrn_minuta",
    "ticho_minut", "ui_radky_arr", "ui_radky_seerr", "ui_radky_hudba",
}
# Czech words in field names: a new field with one is Czech even when the list above lacks it.
FIELD_CZECH_WORDS: set[str] = {
    "zpusob", "rezim", "anonymizace", "historie", "smi", "klic", "vidi", "kapacita", "jednotka", "zdroj", "nazev",
    "cesta", "odpoved", "cil", "potvrzeno", "smazat", "poradi", "pohyb", "zapnuto", "udalost", "datum", "kanal",
    "predmet", "heslo", "komu", "odesilatel", "uzivatel", "misto", "sberac", "souhrn", "den", "hodina", "minuta",
    "ticho", "minut", "dnu", "dni", "pocet", "cas", "presne", "podklad", "radky", "hudba", "udalosti", "ctenar",
    "divak",
}
# Old code values: old field name -> (its new name, the old values) - what a template, a script or a
# test sends. An old value is wrong under either name.
FIELD_VALUES_CZECH: dict[str, tuple[str, set[str]]] = {
    "zpusob": ("method", {"mistni"}),
    "rezim": ("mode", {"vse", "vybrane", "skupina", "vlastni"}),
    "odpoved": ("answer", {"ano", "ne"}),
    "smi_klic": ("may_have_key", {"skupina", "ano", "ne"}),
}
FIELD_KEPT: dict[str, str] = {}     # none left: the fields and their stored keys are English since the database step 5


def field_pattern(name: str) -> str:
    """A field name with the parts filled in at run time as *: f"vidi_{area.key}", name="cil_{{ p.id }}"."""
    return re.sub(r"\{\{.*?\}\}|\{[^{}]*\}", "*", name)


def old_values(name: str) -> set[str]:
    """The old code values of a field (by its old or its new name)."""
    for old, (new, values) in FIELD_VALUES_CZECH.items():
        if name in (old, new):
            return values
    return set()


def in_fields(name: str, names: set[str] | dict[str, str]) -> bool:
    """`name` is one of `names`, also through a * (vidi_* holds vidi_jazyky and vidi_*)."""
    return name in names or any(re.fullmatch(re.escape(c).replace(r"\*", r"[\w.-]*"), name) for c in names if "*" in c)


def czech_field(name: str) -> bool:
    name = field_pattern(name)
    if in_fields(name, FIELD_KEPT):
        return False
    return in_fields(name, FIELD_CZECH) or bool(set(name.split("_")) & FIELD_CZECH_WORDS) or bool(DIACRITICS.search(name))


def read_fields() -> dict[str, str]:
    """Form fields the app reads -> where: Form(...) arguments of the routes, and request.form()
    read by name (form.get/getlist/[], also an f-string and a startswith prefix)."""
    names: dict[str, str] = {}
    for r in flat_routes(web.app.router.routes):
        stack = [r.dependant] if hasattr(r, "dependant") else []
        while stack:
            d = stack.pop()
            stack += d.dependencies
            for x in d.body_params:
                names.setdefault(x.alias, getattr(r, "path", "?"))
    for path in (PROJECT / "jellyscope").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "request.form()" not in text and "form: Any" not in text:
            continue
        aliases = set(re.findall(r"(\w+)\s*=\s*await\s+request\.form\(\)", text)) | {"form"}
        where = path.relative_to(PROJECT).as_posix()
        for alias in aliases:
            for m in re.finditer(rf"""\b{alias}(?:\.get(?:list)?\(\s*|\[\s*)f?["']([^"']+)["']""", text):
                names.setdefault(field_pattern(m.group(1)), f"{where}:{text.count(chr(10), 0, m.start()) + 1}")
        for m in re.finditer(r"""\.startswith\(\s*["'](\w+_)["']\s*\)""", text):
            names.setdefault(m.group(1) + "*", f"{where}:{text.count(chr(10), 0, m.start()) + 1}")
    return names


def written_fields(text: str, where: str) -> list[str]:
    """Old field names and values written: name="…" in a template (also put together with {{ }}),
    [name="…"] and an old value compared in a script, the keys and values of data={…} in a test."""
    found = []

    def at(m: re.Match) -> str:
        return f"{where}:{text.count(chr(10), 0, m.start()) + 1}"

    if where.endswith(".html"):
        for m in re.finditer(r'\bname="([^"]+)"', text):
            if czech_field(m.group(1)):
                found.append(f"{at(m)}: name={field_pattern(m.group(1))}")
        for m in re.finditer(r"<(?:input|button|option)\b[^>]*>", text):
            tag = m.group()
            name, value = re.search(r'\bname="([^"]+)"', tag), re.search(r'\bvalue="([^"]*)"', tag)
            if name and value and value.group(1) in old_values(name.group(1)):
                found.append(f"{at(m)}: {name.group(1)}={value.group(1)}")
        # The values of a <select> are on its <option>s, the name on the <select>.
        for m in re.finditer(r'<select\b[^>]*\bname="([^"]+)"[^>]*>(.*?)</select>', text, re.S):
            for v in re.finditer(r'<option\b[^>]*\bvalue="([^"]*)"', m.group(2)):
                if v.group(1) in old_values(m.group(1)):
                    found.append(f"{at(m)}: {m.group(1)}={v.group(1)}")
    if where.endswith(".js"):
        for m in re.finditer(r"""\[name=["']?([\w{}]+)["']?\]""", text):
            if czech_field(m.group(1)):
                found.append(f"{at(m)}: [name={m.group(1)}]")
        compared = set().union(*(old_values(old) for old in FIELD_VALUES_CZECH))
        for m in re.finditer(r"""\.value\s*[!=]==?\s*["'](\w+)["']""", text):
            if m.group(1) in compared:
                found.append(f"{at(m)}: .value == {m.group(1)}")
    if where.endswith(".py") and where.startswith("tests/"):
        # A browser test's selector in a string: input[name=cesta] (quoted or not).
        for m in re.finditer(r"""\[name=\\?["']?([\w{}]+)\\?["']?\]""", text):
            if czech_field(m.group(1)):
                found.append(f"{at(m)}: [name={m.group(1)}]")
        for d in re.finditer(r"\bdata\s*=\s*\{([^{}]*)\}", text):
            # A test that sends an old name to check it is not read says so on the line.
            if "# old name on purpose" in text[d.start():text.find("\n", d.end()) % (len(text) + 1)]:
                continue
            for m in re.finditer(r"""["']([^"']+)["']\s*:\s*(?:["']([^"']*)["'])?""", d.group(1)):
                key, value = m.group(1), m.group(2)
                if czech_field(key):
                    found.append(f"{where}:{text.count(chr(10), 0, d.start()) + 1}: data {key}")
                elif value is not None and value in old_values(key):
                    found.append(f"{where}:{text.count(chr(10), 0, d.start()) + 1}: data {key}={value}")
    return found


def macro_fields(templates: dict[str, str]) -> dict[str, list[tuple[int, str]]]:
    """Macros that write a field's name from a parameter (name="{{ input_name }}"): {macro: [(index, param)]}."""
    found: dict[str, list[tuple[int, str]]] = {}
    for text in templates.values():
        for m in re.finditer(r"\{%-?\s*macro\s+(\w+)\(([^)]*)\)\s*-?%\}(.*?)\{%-?\s*endmacro", text, re.S):
            params = [x.split("=")[0].strip() for x in m.group(2).split(",") if x.strip()]
            for i, param in enumerate(params):
                if re.search(r'\bname="\{\{\s*' + re.escape(param) + r"\s*\}\}", m.group(3)):
                    found.setdefault(m.group(1), []).append((i, param))
    return found


def macro_call_fields(text: str, where: str, macros: dict[str, list[tuple[int, str]]]) -> list[str]:
    """Old field names handed to such a macro as a string: target_picker('cil'), target_picker('cil_' ~ p.id)."""
    found = []
    for name, params in macros.items():
        for m in re.finditer(rf"\b{name}\(([^()]*)\)", text):
            args = [a.strip() for a in re.split(r",(?![^\[]*\])", m.group(1))]
            for index, param in params:
                arg = next((a.split("=", 1)[1].strip() for a in args if a.startswith(param + "=")),
                           args[index] if index < len(args) else "")
                literal = re.match(r"""^(["'])([\w.-]*)\1(\s*~.*)?$""", arg)
                if literal:
                    field = literal.group(2) + ("*" if literal.group(3) else "")
                    if czech_field(field):
                        found.append(f"{where}:{text.count(chr(10), 0, m.start()) + 1}: {name}({field})")
    return found


TEMPLATES_TEXT = {p.relative_to(PROJECT).as_posix(): p.read_text(encoding="utf-8")
                  for p in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html"))}
MACRO_FIELDS = macro_fields(TEMPLATES_TEXT)
czech_fields = sorted(f"{n} ({w})" for n, w in read_fields().items() if czech_field(n))
check(not czech_fields, f"(a5) no form field the app reads has a Czech name ({len(czech_fields)}: {czech_fields[:6]})")
field_sources = sources + sorted((PROJECT / "tests").glob("*.py"))
lost_fields = []
for path in field_sources:
    if path.is_file() and path.name != "test_addresses.py":
        lost_fields += written_fields(path.read_text(encoding="utf-8"), path.relative_to(PROJECT).as_posix())
lost_fields += [x for where, text in TEMPLATES_TEXT.items() for x in macro_call_fields(text, where, MACRO_FIELDS)]
check("target_picker" in MACRO_FIELDS and not lost_fields,
      f"(a6) no old form field or value is written, also as a macro's argument ({len(lost_fields)}: {lost_fields[:6]})")

print("--- traps ---")
check(czech_param("od") and czech_param("rust_disk") and czech_param("log_klic") and not czech_param("since")
      and not czech_param("growth_disk") and not czech_param("days"),
      "an old or Czech parameter name is found, an English one passes")
params_trap = ('<a href="/history?obdobi=7&amp;od={{ x }}">\n<input name="do" value=""> <input name="{{ p }}_od">'
               ' <a data-growth-disk="vse">\n'
               'searchParams.set("od_ts", t);\n"/library?rust_disk=vse"\n'
               '<a href="/history?period=7&amp;since=x"><input name="until"> "since_ts" "?growth_disk=all"')
params_found = written_params(params_trap, "t.html") + written_params('x.set("od_ts", 1)', "t.js")
check(len(params_found) == 8 and not written_params(params_trap.split("\n")[-1], "t.html"),
      f"an old parameter in a link, a form field, a script and an old value are found; English passes ({params_found})")
check(czech_parts("/settings/ctenari") == {"ctenari"} and czech_parts("/arr/{app}/plakat/{titul_id}") == {"plakat", "titul"}
      and czech_parts("/partials/arr/{app}/stahuje-se") == {"stahuje"} and not czech_parts("/settings/readers")
      and not czech_parts("/library/{library_id}/archive/delete") and not czech_parts("/settings?section=log&log_klic=1"),
      "a Czech part of an address is found, also in a path parameter's name; an English address and a query pass")
sample = ('<form action="/library/{{ lib.id }}/archiv/smazat">\n'
          'fetch("/partials/arr/" + app + "/stahuje-se");\n'
          'poster = f"/arr/{app}/plakat/{block}"\n'
          '# see `/settings/log/stahnout`\n'
          'href="/library/{{ lib.id }}/archive/delete" "/static/js/app.js" "Kopírovat / smazat"')
trap = written_czech(sample, "t")
check([t.split(":")[1] for t in trap] == ["1", "2", "3", "4"],
      f"a Czech address in a template, a script's piece, an f-string and a comment is found; English, "
      f"static and prose are not ({trap})")
edges = ('fetch("/library/disky/" + action);\n'
         '/srovnani at the start of a wrapped docstring line\n'
         '<code>/settings/ctenari</code>\n'
         'href="/settings/účty"\n'
         'a "/library/disks/" + action and <code>/settings/readers</code>')
edges_found = written_czech(edges, "t")
check(sorted({t.split(":")[1] for t in edges_found}) == ["1", "2", "3", "4"],
      f"an address joined in a script, at the start of a line, after > and with diacritics is found ({edges_found})")
inside = ('{% call link("/hudba/album/" ~ row.id) %}x{% endcall %}\n'
          "<a href=\"{{ '/settings/arr/' ~ app ~ '/smazat' }}\">\n"
          '{% set url = "/account/prehled" %}\n'
          '{% call link("/music/album/" ~ row.id) %}x{% endcall %}')
inside_found = written_czech(inside, "t")
check(sorted({t.split(":")[1] for t in inside_found}) == ["1", "2", "3"],
      f"an address inside a Jinja tag (link(), a string joined with ~, set) is found too ({inside_found})")

fields_trap = ('<input name="kapacita"> <button name="zpusob" value="mistni">\n'
               '<input type="checkbox" name="vidi_{{ area.key }}"> <input name="text_{{ m.key }}_predmet">\n'
               '<button name="method" value="mistni"> <select name="may_have_key"><option value="ano">x</option></select>\n'
               '<input name="capacity"> <button name="method" value="local"> <input name="sees_{{ a.key }}">')
fields_found = written_fields(fields_trap, "t.html")
js_found = written_fields('form.querySelector(\'input[name="rezim"]:checked\'); x.value === "vlastni"; '
                          'y.value === "custom"; [name="mode"]', "t.js")
test_found = written_fields('c.post("/login", data={"username": u, "zpusob": "mistni"})\n'
                            'c.post("/login", data={"zpusob": "jellyfin"})  # old name on purpose\n'
                            'c.post("/library/disks/same", data={"a": a, "answer": "ano"})\n'
                            'c.post("/login", data={"username": u, "method": "local"})\n'
                            'p.js("document.querySelector(\'input[name=cesta]\')")', "tests/t.py")
check(len(fields_found) == 7 and not written_fields(fields_trap.split("\n")[-1], "t.html")
      and len(js_found) == 2 and len(test_found) == 3,
      f"an old field name and value in a template, put together with {{{{ }}}}, in a script and in a test's "
      f"data are found; English passes ({fields_found} {js_found} {test_found})")
macro_found = macro_call_fields("{{ target_picker('cil') }}\n{{ target_picker('cil_' ~ p.id) }}\n"
                               "{{ target_picker('target') }} {{ target_picker('target_' ~ p.id) }}", "t.html", MACRO_FIELDS)
check(len(macro_found) == 2, f"an old name handed to a macro that writes name= is found; English passes ({macro_found})")
check(czech_field("vidi_*") and czech_field("text_*_predmet") and czech_field(field_pattern("cil_{{ p.id }}"))
      and not czech_field("sees_*") and not czech_field("text_*_subject") and not czech_field("username")
      and czech_field("souhrn_vterina") and czech_field("vidi_jazyky") and czech_field("sees_hudba_kdo")
      and czech_field("enabled_hudba") and czech_field("ui_cas_presne") and not czech_field("sees_music_who"),
      "a field put together at run time is matched by its fixed parts, a new one by a Czech word; "
      "the old names of the database step 5 are found too")

print(f"\n{'DONE - failures: 0' if not failures else f'DONE - failures: {failures}'}")
sys.exit(1 if failures else 0)
