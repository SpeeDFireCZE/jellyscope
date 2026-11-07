r"""Pravidla balíku scanner/ - scanner.py rozdělený podle oblastí (přestavba 3c).

`jellyscope/scanner.py` měl 2 904 řádků: synchronizaci knihovny,
obnovu jedné položky a nedávných změn, sloučení podle TMDB, zápis
položek, volné místo, jazyky, technickou analýzu a řízení běžících úloh.
Je z něj balík `jellyscope/scanner/` se vstupními dveřmi
(`scanner/__init__.py`), takže zvenku se nemění nic. Test hlídá:

(a) vstupní dveře mají všechno, co se zvenku čte jako `scanner.X`;
(b) každé jméno bydlí ve svém podmodulu a dveře ukazují na tentýž objekt;
(c) bez kruhů: závislosti jedním směrem, žádný vnořený `from . import X`,
    který po přesunu míří do balíku, každý podmodul jde naimportovat
    jako první v čistém procesu;
(d) žádný podmodul nad 600 řádků;
(e) testy podvrhují tam, kde to zasáhne (tests/_balik.py, `podvrhni`);
(f) stav běžící úlohy je jeden: zámek a průběh jsou všude tentýž objekt,
    příznak zastavení čte a přepisuje jen beh.py (je to bool - import by
    udělal kopii a tlačítko Zastavit by sken nezastavilo);
(g) starý scanner.py není a žádný test ho nečte podle cesty;
(h) komentáře se při přesunu neztratily;
(i) logger se jmenuje jako dřív (`jellyscope.scanner`).

Spec: docs/superpowers/specs/2026-11-06-scanner-balik-design.md

Spuštění:
    .venv/bin/python tests/test_scanner_balik.py
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "scanner_balik.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _balik import podvrhni, podvrhy_v_podmodulech, spatne_podvrhy  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


BALIK = PROJECT / "jellyscope" / "scanner"
STARY = PROJECT / "jellyscope" / "scanner.py"
# Soubory s ukázkami podvrhů a jmen v řetězcích - nejsou to skutečná použití.
S_UKAZKAMI = {Path(__file__).name, "test_stats_balik.py", "test_web_balik.py", "_balik.py"}

# Mapa jmen nejvyšší úrovně scanner.py -> podmodul (spec, oddíl Rozdělení).
PODMODULY: dict[str, list[str]] = {
    "typy": ["SPRAVOVANE_TYPY", "OBALY", "ZATIM_NEZARAZENY", "ZAPISOVANE_TYPY", "je_obal"],
    "beh": ["_scan_lock", "is_scan_running", "zamek_uloh", "_stop_requested", "request_stop", "stop_requested",
            "_progress", "progress", "_start_progress", "_add_progress", "_clear_progress", "_clear_stop",
            "start_task_log", "otisky", "library_version", "tasks_version", "finish_task_log", "ZAZNAMU_NA_DRUH",
            "_proredit_zaznamy", "last_scan"],
    "misto": ["KAPACITA_KLIC", "JF_VOLNE_KLIC", "JF_CELKEM_KLIC", "ZDROJ_KLIC", "_cislo", "misto_z_jellyfinu",
              "zapamatuj_misto_z_jellyfinu", "_volne_z_kapacity", "_volne_misto_knihovny", "zapis_snimek"],
    "slouceni": ["_stare_ke_slouceni", "_merge_by_tmdb", "dvojnici_novych", "_podle_tmdb", "_podle_nazvu", "_vyber",
                 "prenes_historii", "_rovno_nebo_nic", "do_archivu"],
    "zapis": ["zapis_verze", "_write_batch", "_write_items", "_SLOUPEC_ID", "_SLOUPEC_SERIAL",
              "_SLOUPEC_OTISK_SERIALU", "_SLOUPEC_OTISK", "_polozky_s_jinym_obrazkem", "_zapomen_obrazky",
              "save_streams", "_mark_missing", "slouc_archiv_do_zivych", "uklid_fantomu"],
    "synchronizace": ["sync_library", "_sync_users", "_sync_libraries", "tmdb_id_of", "identita_polozky",
                      "_radek_polozky", "_ktere_uz_zname", "_uloz_nove_polozky", "_sync_items_of_library"],
    "obnova": ["RECENT_OVERLAP_MINUTES", "REVIZE_STROP", "_posledni_pridano", "refresh_item", "refresh_series",
               "_zprava_nedavnych", "sync_recent", "_reviduj_nedavne", "_naslednici_podle_cesty",
               "srovnej_po_novych"],
    "jazyky": ["doplnit_jazyky_z_jellyfinu", "_doplnit_jazyky_polozky", "kandidati_na_jazyk_z_nazvu",
               "doplnit_jazyky_z_nazvu", "_doplnit_z_nazvu_polozky", "_rozdelit_podle_nazvu", "_sparuj_stopy",
               "_prepocitej_jazyky_polozky"],
    "technika": ["run_tech_scan", "_chybi_jazyk", "kandidati_na_rozsah_z_jellyfinu", "doplnit_rozsah_z_jellyfinu",
                 "_save_probe_result", "_save_tech", "_save_tech_error"],
}
SOUBORY = list(PODMODULY)
# Stav, který se přepisuje (bool): ve dveřích by byla jen kopie z doby importu.
NE_VE_DVERICH = {"_stop_requested"}
# Listy grafu: tyhle podmoduly na nic dalšího v balíku nesahají (spec).
LISTY = {"typy", "beh", "misto", "slouceni", "jazyky"}

# --- dnešní stav (vygenerovaný z scanner.py 6. 11. 2026) -----------------------------
PUVODNI = [
    'SPRAVOVANE_TYPY', 'OBALY', 'ZATIM_NEZARAZENY', 'ZAPISOVANE_TYPY', 'je_obal', '_scan_lock',
    'is_scan_running', 'zamek_uloh', '_stop_requested', 'request_stop', 'stop_requested',
    'RECENT_OVERLAP_MINUTES', 'REVIZE_STROP', '_progress', 'progress', '_start_progress',
    '_add_progress', '_clear_progress', '_clear_stop', 'start_task_log', 'otisky',
    'library_version', 'tasks_version', 'finish_task_log', 'ZAZNAMU_NA_DRUH', '_proredit_zaznamy',
    'last_scan', '_posledni_pridano', 'refresh_item', 'refresh_series', '_zprava_nedavnych',
    'sync_recent', 'sync_library', 'KAPACITA_KLIC', 'JF_VOLNE_KLIC', 'JF_CELKEM_KLIC',
    'ZDROJ_KLIC', '_cislo', 'misto_z_jellyfinu', 'zapamatuj_misto_z_jellyfinu',
    '_volne_z_kapacity', '_volne_misto_knihovny', 'zapis_snimek', '_sync_users', '_sync_libraries',
    'tmdb_id_of', 'identita_polozky', '_radek_polozky', '_ktere_uz_zname', '_uloz_nove_polozky',
    '_sync_items_of_library', '_stare_ke_slouceni', '_merge_by_tmdb', 'dvojnici_novych',
    '_podle_tmdb', '_podle_nazvu', '_vyber', 'prenes_historii', '_rovno_nebo_nic', 'do_archivu',
    '_reviduj_nedavne', '_naslednici_podle_cesty', 'srovnej_po_novych', 'zapis_verze',
    '_write_batch', '_write_items', '_SLOUPEC_ID', '_SLOUPEC_SERIAL', '_SLOUPEC_OTISK_SERIALU',
    '_SLOUPEC_OTISK', '_polozky_s_jinym_obrazkem', '_zapomen_obrazky', 'save_streams',
    '_mark_missing', 'slouc_archiv_do_zivych', 'uklid_fantomu', 'run_tech_scan', '_chybi_jazyk',
    'kandidati_na_rozsah_z_jellyfinu', 'doplnit_rozsah_z_jellyfinu', 'doplnit_jazyky_z_jellyfinu',
    '_doplnit_jazyky_polozky', 'kandidati_na_jazyk_z_nazvu', 'doplnit_jazyky_z_nazvu',
    '_doplnit_z_nazvu_polozky', '_rozdelit_podle_nazvu', '_sparuj_stopy',
    '_prepocitej_jazyky_polozky', '_save_probe_result', '_save_tech', '_save_tech_error',
]

# Řádky komentářů dnešního scanner.py.
KOMENTARU_PUVODNE = 396


def definovane(strom: ast.Module) -> set[str]:
    """Jména definovaná na nejvyšší úrovni (def, class, přiřazení)."""
    jmena: set[str] = set()
    for uzel in strom.body:
        if isinstance(uzel, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            jmena.add(uzel.name)
        elif isinstance(uzel, ast.Assign):
            jmena |= {c.id for c in uzel.targets if isinstance(c, ast.Name)}
        elif isinstance(uzel, ast.AnnAssign) and isinstance(uzel.target, ast.Name):
            jmena.add(uzel.target.id)
    return jmena


def strom(cesta: Path) -> ast.Module:
    return ast.parse(cesta.read_text(encoding="utf-8"))


def je_balik() -> bool:
    return (BALIK / "__init__.py").is_file()


def nactena(st: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(st) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


scanner = importlib.import_module("jellyscope.scanner")
stats = importlib.import_module("jellyscope.stats")

print("--- (g) starý soubor ---")
check(je_balik() and not STARY.exists(), "scanner.py není, balík scanner/ ano")
PO_STARU = re.compile(r"""["']scanner\.py["']""")
po_staru = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py"))
            if s.name not in S_UKAZKAMI and s.name != "test_sync_do_produ.py"
            for i, radek in enumerate(s.read_text(encoding="utf-8").splitlines(), 1) if PO_STARU.search(radek)]
check(not po_staru, f"žádný test nečte scanner.py podle cesty ({po_staru[:6]})")

print()
print("--- (a) vstupní dveře mají všechno, co se zvenku čte ---")
JMENO = re.compile(r"(?<![\w.])scanner\.([A-Za-z_]\w*)")
pouzite: dict[str, str] = {}
for cesta in sorted([*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
                     *(PROJECT / j for j in ("demo.py", "run.py", "manage.py"))]):
    if not cesta.is_file() or BALIK in cesta.parents or cesta == STARY or cesta.name in S_UKAZKAMI:
        continue
    for jmeno in JMENO.findall(cesta.read_text(encoding="utf-8")):
        pouzite.setdefault(jmeno, cesta.name)
pouzite.pop("py", None)
chybi = sorted(f"{j} ({kde})" for j, kde in pouzite.items() if j not in SOUBORY and not hasattr(scanner, j))
check(len(pouzite) > 50 and not chybi, f"všech {len(pouzite)} jmen jde dveřmi (chybí {chybi[:6]})")

print()
print("--- (b) každé jméno bydlí ve svém podmodulu ---")
check(sorted(j for js in PODMODULY.values() for j in js) == sorted(PUVODNI),
      f"mapa pokrývá všech {len(PUVODNI)} jmen scanner.py, každé jednou")
if je_balik():
    chybi_v, jine = [], []
    for m, jmena in PODMODULY.items():
        cesta = BALIK / f"{m}.py"
        tam = definovane(strom(cesta)) if cesta.is_file() else set()
        chybi_v += [f"{m}.{j}" for j in jmena if j not in tam]
        if cesta.is_file():
            modul = importlib.import_module(f"jellyscope.scanner.{m}")
            jine += [f"{m}.{j}" for j in jmena if j in tam and j not in NE_VE_DVERICH
                     and getattr(scanner, j, None) is not getattr(modul, j)]
    check(not chybi_v, f"jména jsou v podmodulech podle mapy ({chybi_v[:6]})")
    check(not jine, f"dveře ukazují na tytéž objekty jako podmoduly ({jine[:6]})")
    check(not any(hasattr(scanner, j) for j in NE_VE_DVERICH),
          "příznak Zastavit (_stop_requested) ve dveřích není - byla by to kopie, která se nemění")
    jellyfin = importlib.import_module("jellyscope.jellyfin")
    check(getattr(scanner, "JellyfinClient", None) is jellyfin.JellyfinClient,
          "scanner.JellyfinClient je klient z jellyfin.py (testy ho podvrhují)")
else:
    check(False, "jména jsou v podmodulech podle mapy (balík scanner/ není)")

print()
print("--- (c) bez kruhů ---")
if je_balik():
    hrany: dict[str, set[str]] = {m: set() for m in SOUBORY}
    zakazane = []
    for m in SOUBORY:
        if not (BALIK / f"{m}.py").is_file():
            continue
        for uzel in ast.walk(strom(BALIK / f"{m}.py")):
            if isinstance(uzel, ast.ImportFrom) and uzel.level == 1:
                if uzel.module is None:
                    zakazane += [f"{m}: from . import {a.name}" for a in uzel.names if a.name not in SOUBORY]
                    hrany[m] |= {a.name for a in uzel.names if a.name in SOUBORY}
                elif uzel.module in SOUBORY:
                    hrany[m].add(uzel.module)
                else:
                    zakazane.append(f"{m}: from .{uzel.module} import")
    check(not zakazane, f"žádný import v podmodulu nemíří omylem do balíku ({zakazane[:6]})")
    zbyva = {m: set(h) for m, h in hrany.items()}
    while any(not h for h in zbyva.values()):
        hotove = {m for m, h in zbyva.items() if not h}
        zbyva = {m: h - hotove for m, h in zbyva.items() if m not in hotove}
    check(not zbyva, f"závislosti bez kruhu ({sorted(zbyva)})")
    check(all(not hrany[m] for m in LISTY), f"typy, beh, misto, slouceni a jazyky na nic v balíku nesahají "
          f"({ {m: sorted(hrany[m]) for m in LISTY if hrany[m]} })")
    # Import podmodulu vždy napřed spustí __init__ balíku (a ten načte všechny
    # podmoduly) - jedna kontrola na balík pokryje všechny, víc by jen opakovalo totéž.
    for jmeno in ["jellyscope.scanner"]:
        beh = subprocess.run([sys.executable, "-c", f"import {jmeno}"], cwd=PROJECT, env=os.environ.copy(),
                             capture_output=True, text=True, timeout=180)
        chyba = (beh.stderr.strip().splitlines() or [""])[-1]
        check(beh.returncode == 0, f"`import {jmeno}` jako první v čistém procesu ({chyba[:120]})")
else:
    check(False, "závislosti bez kruhu (balík scanner/ není)")

print()
print("--- (d) velikost ---")
if je_balik():
    velke = [(p.name, n) for p in sorted(BALIK.glob("*.py")) if (n := len(p.read_text(encoding="utf-8").splitlines())) > 600]
else:
    velke = [("scanner.py", len(STARY.read_text(encoding="utf-8").splitlines()))]
check(not velke, f"žádný podmodul nad 600 řádků ({velke})")

print()
print("--- (e) testy podvrhují tam, kde to zasáhne ---")
KDE = {j: m for m, js in PODMODULY.items() for j in js}
ctenari: dict[str, set[str]] = {}
if je_balik():
    for m in SOUBORY:
        if (BALIK / f"{m}.py").is_file():
            for jmeno in nactena(strom(BALIK / f"{m}.py")):
                ctenari.setdefault(jmeno, set()).add(m)
else:
    for uzel in strom(STARY).body:
        moduly = {KDE[j] for j in definovane(ast.Module(body=[uzel], type_ignores=[])) if j in KDE}
        for jmeno in nactena(uzel):
            ctenari.setdefault(jmeno, set()).update(moduly)
ctenari = {j: m for j, m in ctenari.items() if m}
# Ukázky pro podvrhni: nastaví jméno ve dveřích i v podmodulech, vrátí obojí.
puvodni_mista, puvodni_odkud = stats.mista, stats.odkud.mista


def nahrada(*a, **k):
    return []


vrat = podvrhni(stats, "mista", nahrada)
check(stats.mista is nahrada and stats.odkud.mista is nahrada, "podvrhni podvrhne ve dveřích i v podmodulu")
vrat()
check(stats.mista is puvodni_mista and stats.odkud.mista is puvodni_odkud, "a vrácení vrátí obojí")
try:
    podvrhni(stats, "mista_preklep", nahrada)
    check(False, "podvrhni s překlepem ve jméně spadne (jinak by tiše nepodvrhl nic)")
except AttributeError:
    check(True, "podvrhni s překlepem ve jméně spadne (jinak by tiše nepodvrhl nic)")
# Kontrola kontroly (e): posuzuje se celý soubor - prosté přiřazení vedle podvrhni se nepromine.
DVA = {"JellyfinClient": {"obnova", "synchronizace"}}
check(spatne_podvrhy('podvrhni(scanner, "JellyfinClient", F)\nscanner.JellyfinClient = G', "scanner", DVA),
      "(e) prosté přiřazení vedle podvrhni se nepromine")
check(spatne_podvrhy("scanner.JellyfinClient = F\nscanner.synchronizace.JellyfinClient = F", "scanner", DVA),
      "(e) podvrh jen v jednom ze dvou čtenářů nestačí")
check(not spatne_podvrhy('podvrhni(scanner, "JellyfinClient", F)', "scanner", DVA)
      and not spatne_podvrhy("scanner.JellyfinClient = F\nscanner.obnova.JellyfinClient = F\n"
                             "scanner.synchronizace.JellyfinClient = F", "scanner", DVA),
      "(e) podvrhni nebo podvrh ve všech čtenářích stačí")
spatne, mimo = [], []
jmena_v = {m: definovane(strom(BALIK / f"{m}.py")) | nactena(strom(BALIK / f"{m}.py"))
           for m in SOUBORY if (BALIK / f"{m}.py").is_file()}
for cesta in sorted(PROJECT.glob("tests/*.py")):
    if cesta.name in S_UKAZKAMI:
        continue
    text = cesta.read_text(encoding="utf-8")
    spatne += [f"{cesta.name}: {chyba}" for chyba in spatne_podvrhy(text, "scanner", ctenari)]
    for m, jmeno in sorted(podvrhy_v_podmodulech(text, "scanner")):
        if m in jmena_v and jmeno not in jmena_v[m]:
            mimo.append(f"{cesta.name}: scanner.{m}.{jmeno}")
check(not spatne, f"jména čtená v podmodulech se podvrhují přes podvrhni nebo ve čtenáři ({spatne})")
check(not mimo, f"každý podvrh v podmodulu míří tam, kde se jméno čte ({mimo})")

print()
print("--- (f) stav běžící úlohy je jeden ---")
if je_balik():
    kde_stop = sorted({p.stem for p in BALIK.glob("*.py")
                       for n in ast.walk(strom(p))
                       if (isinstance(n, ast.Name) and n.id == "_stop_requested")
                       or (isinstance(n, ast.Global) and "_stop_requested" in n.names)
                       or (isinstance(n, ast.alias) and n.name == "_stop_requested")})
    check(kde_stop == ["beh"], f"_stop_requested čte a přepisuje jen beh.py ({kde_stop})")
    beh_modul = importlib.import_module("jellyscope.scanner.beh")
    kopie = [f"{m}.{j}" for m in SOUBORY for j in ("_scan_lock", "_progress")
             if hasattr(mod := importlib.import_module(f"jellyscope.scanner.{m}"), j)
             and getattr(mod, j) is not getattr(beh_modul, j)]
    check(not kopie, f"zámek a průběh jsou ve všech podmodulech tentýž objekt jako v beh ({kopie})")
else:
    check(False, "stav běžící úlohy (balík scanner/ není)")

print()
print("--- (h) komentáře ---")
if je_balik():
    komentaru = sum(1 for p in BALIK.glob("*.py") for r in p.read_text(encoding="utf-8").splitlines()
                    if r.strip().startswith("#"))
    check(KOMENTARU_PUVODNE <= komentaru <= KOMENTARU_PUVODNE + 30,
          f"komentáře se neztratily ({komentaru}, čeká se {KOMENTARU_PUVODNE}-{KOMENTARU_PUVODNE + 30})")
else:
    print("       čeká na balík (viz g)")

print()
print("--- (i) logger ---")
if je_balik():
    # Všechna volání, ne jedno na soubor - druhý getLogger by jinak přebil první.
    loggery = [(p.name, v) for p in sorted(BALIK.glob("*.py"))
               for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
    spatne_l = [(s, v) for s, v in loggery if v != '"jellyscope.scanner"']
    check(loggery and not spatne_l,
          f"balík loguje jako jellyscope.scanner ({spatne_l or len(loggery)})")
else:
    check(False, "logger (balík scanner/ není)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
