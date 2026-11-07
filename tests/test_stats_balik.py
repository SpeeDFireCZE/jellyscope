r"""Statistiky jako balík podle témat (jellyscope/stats/) - pravidla balíku.

Dřív jeden soubor stats.py (3 905 řádků). Teď 14 podmodulů a vstupní
dveře stats/__init__.py, které vystaví všechno, co se dřív volalo
`stats.X`. Hlídá se tu:

(a) vstupní dveře mají každé jméno, které se kde volá (`stats.X` v modulech
    i testech, `from .stats import X`);
(b) každé jméno z původního stats.py bydlí ve svém podmodulu (PODMODULY)
    a dveře ukazují na tentýž objekt;
(c) závislosti mezi podmoduly vedou jedním směrem, bez kruhu;
(d) žádný podmodul nad 600 řádků;
(e) testy podvrhují tam, kde to zasáhne: jméno, které se čte i uvnitř
    balíku, se podvrhuje v podmodulu - jinak by ho kód balíku neviděl
    a test by tiše testoval něco jiného;
(f) starý stats.py není;
(g) komentáře se při přesunu neztratily;
(h) testy, které čtou zdrojáky, vidí i balík (glob bez podsložek by
    statistiky tiše vynechal);
(i) logger se jmenuje jako dřív (`jellyscope.stats`) - jména loggerů jsou
    vidět v každém řádku logu.

Spec: docs/superpowers/specs/2026-11-06-stats-balik-design.md

Spuštění:
    .venv/bin/python tests/test_stats_balik.py
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

from _balik import podvrhuje_v_podmodulu, podvrhy_v_podmodulech, podvrhy_ve_dverich, spatne_podvrhy  # noqa: E402

_tmp = tempfile.mkdtemp()
os.environ.setdefault("JELLYFIN_URL", "http://127.0.0.1:1")
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "stats_balik.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


BALIK = PROJECT / "jellyscope" / "stats"
STARY = PROJECT / "jellyscope" / "stats.py"

# Všechna jména nejvyšší úrovně původního stats.py (6. 11. 2026, z AST).
PUVODNI = """log RESOLUTION_CASE RESOLUTION_ORDER Obdobi KONEC_CASU BEZ_LIMITU NEJSTARSI_HISTORIE obdobi_dnu
obdobi_cela_historie _mistni_cas obdobi_od_do obdobi_z_okamziku _obdobi _meze predchozi prvni_zaznam lze_srovnat
je_transcode overview previous_overview delka_sekund _prekryvaji_se _radek DORUCENI_ROLE _jazykove_radky srovnani
active_session_count rychlost_prevodu popis_prepoctu active_sessions _NEJNOVEJSI_SKUPINY recently_added _serad_dily
_prednost_dilu DAVKA_HODIN _o_hodin_zpet _ve_stejne_davce KIND_MOVIE KIND_SERIES KIND_OTHER KIND_BOTH ALLOWED_KINDS
SERIALOVE_TYPY _ZNAME_TYPY _SEZNAM_SERIALOVYCH _SEZNAM_ZNAMYCH _kind_condition TYPY_POLOZEK TYP_NEZNAMY nazev_typu
rozpad_ostatnich _mistni_den daily_activity_split DIVAK_JMENO DIVAK_SKUPINA DIVAK_PORADI top_users _SERIAL_Z_NAZVU
serial_z_nazvu _dily_podle_nazvu _jmena_serialu klic_titulu SKUPINA_TITULU_KLIC SKUPINA_TITULU_SLOUPCE _slouc_tituly
top_items play_method_breakdown client_breakdown hourly_heatmap ZPUSOBY _pojistne_meze _filtr_historie history
historie_pro_api history_count hodnoty_filtru user_table _s_prokliknutim user_genres user_detail tech_coverage
codec_breakdown resolution_breakdown ROZSAH_CASE rozsah_polozky _dopoctena_krivka stav_knihovny snimky _volne_misto_ted
rust_knihovny video_range_breakdown _SKUPINA _SKUPINA_SLOUPCE _PREHRANI _knihovna_filtr library_rows library_rows_count
series_detail _spolecna_slozka library_cards library library_overview library_activity item verze_polozky item_streams
item_playback item_playback_summary sibling_episodes archived_count delete_item _smaz_polozku jen_z_archivu
smaz_z_archivu nahled_polozky jmeno_souboru popis_polozky _VSECHNA_PREHRANI archivovane_polozky _KOD_DILU _ASCII_KUS
_OKRAJ_SLOVA HLEDANI_KANDIDATU _pro_hledani _znacka_dilu _samostatne _like_kdekoli hledej_existujici PrirazeniError
prirad_k_jinemu _sitove_radky _sekundy bandwidth_prubeh tok_ted NEJKRATSI_ZIVE_OKNO USEK_ZIVE_SEKUND NEJVIC_BODU_ZIVE
bandwidth_zive bandwidth_denni_spicky bandwidth_prehled bandwidth_podle DOMACI_PREFIXY je_domaci _provoz_podle_adresy
odkud_se_divaji mista hotspoty zeme_divaku""".split()
KOMENTARU_PUVODNE = 492

# Kde které jméno bydlí - plán docs/superpowers/plans/2026-11-06-stats-balik.md.
PODMODULY: dict[str, list[str]] = {k: v.split() for k, v in {
    "obdobi": "Obdobi KONEC_CASU BEZ_LIMITU NEJSTARSI_HISTORIE obdobi_dnu obdobi_cela_historie _mistni_cas obdobi_od_do "
              "obdobi_z_okamziku _obdobi _meze predchozi prvni_zaznam lze_srovnat je_transcode _mistni_den",
    "druhy": "KIND_MOVIE KIND_SERIES KIND_OTHER KIND_BOTH ALLOWED_KINDS SERIALOVE_TYPY _ZNAME_TYPY _SEZNAM_SERIALOVYCH "
             "_SEZNAM_ZNAMYCH _kind_condition TYPY_POLOZEK TYP_NEZNAMY nazev_typu",
    "prehled": "overview previous_overview delka_sekund _prekryvaji_se _radek DORUCENI_ROLE _jazykove_radky srovnani "
               "rozpad_ostatnich daily_activity_split play_method_breakdown client_breakdown hourly_heatmap",
    "prehravani": "active_session_count rychlost_prevodu popis_prepoctu active_sessions",
    "nedavno": "_NEJNOVEJSI_SKUPINY recently_added _serad_dily _prednost_dilu DAVKA_HODIN _o_hodin_zpet _ve_stejne_davce",
    "tituly": "_SERIAL_Z_NAZVU serial_z_nazvu _dily_podle_nazvu _jmena_serialu klic_titulu SKUPINA_TITULU_KLIC "
              "SKUPINA_TITULU_SLOUPCE _slouc_tituly top_items",
    "divaci": "DIVAK_JMENO DIVAK_SKUPINA DIVAK_PORADI top_users user_table _s_prokliknutim user_genres user_detail",
    "historie": "ZPUSOBY _pojistne_meze _filtr_historie history historie_pro_api history_count hodnoty_filtru",
    "technika": "RESOLUTION_CASE RESOLUTION_ORDER tech_coverage codec_breakdown resolution_breakdown ROZSAH_CASE "
                "rozsah_polozky video_range_breakdown",
    "rust": "_dopoctena_krivka stav_knihovny snimky _volne_misto_ted rust_knihovny",
    "knihovna": "_SKUPINA _SKUPINA_SLOUPCE _PREHRANI _knihovna_filtr library_rows library_rows_count series_detail "
                "_spolecna_slozka library_cards library library_overview library_activity",
    "polozka": "item verze_polozky item_streams item_playback item_playback_summary sibling_episodes delete_item "
               "_smaz_polozku nahled_polozky jmeno_souboru popis_polozky log",
    "archiv": "archived_count jen_z_archivu smaz_z_archivu _VSECHNA_PREHRANI archivovane_polozky _KOD_DILU _ASCII_KUS "
              "_OKRAJ_SLOVA HLEDANI_KANDIDATU _pro_hledani _znacka_dilu _samostatne _like_kdekoli hledej_existujici "
              "PrirazeniError prirad_k_jinemu log",
    "sit": "_sitove_radky _sekundy bandwidth_prubeh tok_ted NEJKRATSI_ZIVE_OKNO USEK_ZIVE_SEKUND NEJVIC_BODU_ZIVE "
           "bandwidth_zive bandwidth_denni_spicky bandwidth_prehled bandwidth_podle",
    # Odděleno od sit kvůli stropu 600 řádků - samostatné téma, na toku nezávislé.
    "odkud": "DOMACI_PREFIXY je_domaci _provoz_podle_adresy odkud_se_divaji mista hotspoty zeme_divaku",
}.items()}


def definovane(strom: ast.Module) -> dict[str, ast.stmt]:
    """Jména nejvyšší úrovně modulu -> jejich uzel (def, class, přiřazení)."""
    vysledek: dict[str, ast.stmt] = {}
    for uzel in strom.body:
        if isinstance(uzel, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            vysledek[uzel.name] = uzel
        elif isinstance(uzel, ast.Assign):
            vysledek.update({c.id: uzel for c in uzel.targets if isinstance(c, ast.Name)})
        elif isinstance(uzel, ast.AnnAssign) and isinstance(uzel.target, ast.Name):
            vysledek[uzel.target.id] = uzel
    return vysledek


def soubory_balicku() -> list[Path]:
    return sorted(BALIK.glob("*.py")) if BALIK.is_dir() else []


print("--- (f) soubor ---")
check(not STARY.exists(), "starý jellyscope/stats.py není")
check((BALIK / "__init__.py").is_file(), "jellyscope/stats/__init__.py je")

print()
print("--- (a) vstupní dveře mají všechno, co se kde volá ---")
stats = importlib.import_module("jellyscope.stats")
JMENO_STATS = re.compile(r"(?<!\w)stats\.([A-Za-z_]\w*)")
FROM_STATS = re.compile(r"from\s+(?:\.|jellyscope\.)stats\s+import\s+\(?([^)\n]+)\)?")
pouzite: dict[str, str] = {}
for soubor in [*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py")]:
    if BALIK in soubor.parents or soubor.name in (Path(__file__).name, "test_web_balik.py", "_balik.py"):
        continue      # balík sám a soubory s ukázkami „stats.X" v textu
    text = soubor.read_text(encoding="utf-8")
    for jmeno in JMENO_STATS.findall(text):
        pouzite.setdefault(jmeno, soubor.name)
    for skupina in FROM_STATS.findall(text):
        for kus in skupina.split(","):
            jmeno = kus.split(" as ")[0].strip()
            if jmeno:
                pouzite.setdefault(jmeno, soubor.name)
chybi = sorted(f"{j} ({kde})" for j, kde in pouzite.items()
               if j not in ("py",) and j not in PODMODULY and not hasattr(stats, j))
check(len(pouzite) > 50 and not chybi, f"všech {len(pouzite)} jmen jde dveřmi (chybí {chybi[:6]})")

print()
print("--- (b) každé jméno bydlí ve svém podmodulu ---")
vsechna = sorted(j for jmena in PODMODULY.values() for j in set(jmena))
check(sorted(set(PUVODNI)) == sorted(set(vsechna)),
      f"PODMODULY pokrývají přesně původní jména (navíc {sorted(set(vsechna) - set(PUVODNI))[:4]}, "
      f"chybí {sorted(set(PUVODNI) - set(vsechna))[:4]})")
for modul, jmena in PODMODULY.items():
    cesta = BALIK / f"{modul}.py"
    if not cesta.is_file():
        check(False, f"stats/{modul}.py je")
        continue
    v_souboru = definovane(ast.parse(cesta.read_text(encoding="utf-8")))
    chybi = [j for j in jmena if j not in v_souboru]
    check(not chybi, f"stats/{modul}.py definuje svá jména (chybí {chybi[:5]})")
    podmodul = importlib.import_module(f"jellyscope.stats.{modul}")
    jine = [j for j in jmena if j != "log" and getattr(stats, j, None) is not getattr(podmodul, j, object())]
    check(not jine, f"dveře ukazují na objekty ze stats/{modul}.py (jinak: {jine[:5]})")

print()
print("--- (c) závislosti jedním směrem ---")
graf: dict[str, set[str]] = {}
for cesta in soubory_balicku():
    if cesta.stem == "__init__":
        continue
    strom = ast.parse(cesta.read_text(encoding="utf-8"))
    graf[cesta.stem] = {u.module for u in ast.walk(strom)
                        if isinstance(u, ast.ImportFrom) and u.level == 1 and u.module}
check(len(graf) == len(PODMODULY), f"balík má {len(PODMODULY)} podmodulů ({len(graf)})")
zbyva, poradi = dict(graf), []
while zbyva:
    listy = [m for m, zavislosti in zbyva.items() if not (zavislosti & set(zbyva))]
    if not listy:
        break
    poradi += listy
    for m in listy:
        del zbyva[m]
check(not zbyva, f"bez kruhu v importech (zbylo v kruhu: {sorted(zbyva)})")
check(graf.get("obdobi", {"?"}) == set() and graf.get("druhy", {"?"}) == set(),
      f"obdobi a druhy jsou základ ({graf.get('obdobi')}, {graf.get('druhy')})")

print()
print("--- (d) velikost ---")
velke = [(c.name, len(c.read_text(encoding="utf-8").splitlines())) for c in soubori if
         len(c.read_text(encoding="utf-8").splitlines()) > 600] if (soubori := soubory_balicku()) else [("balík není", 0)]
check(not velke, f"žádný podmodul nad 600 řádků ({velke})")

print()
print("--- (e) testy podvrhují tam, kde to zasáhne ---")
zdroje = soubory_balicku() or [STARY]
# Kdo které jméno čte: podvrh musí být v podmodulu, kde se čte.
ctenari: dict[str, set[str]] = {}
for cesta in zdroje:
    strom = ast.parse(cesta.read_text(encoding="utf-8"))
    for uzel in strom.body:
        vlastni = set(definovane(ast.Module(body=[uzel], type_ignores=[])))
        for jmeno in {n.id for n in ast.walk(uzel) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                      and n.id in PUVODNI and n.id not in vlastni}:
            ctenari.setdefault(jmeno, set()).add(cesta.stem)
vnitrni = set(ctenari)
# Podvrhy pozná sdílený pomocník (tests/_balik.py) - stejně jako u webu.


# Kontrola kontroly: každý tvar podvrhu se pozná, porovnání (==) ne.
for ukazka, ocekavane in (("stats.mista = x", {"mista"}), ("api.stats.mista = x", {"mista"}),
                          ("sekce.stats.mista = x", {"mista"}), ("web.stats.mista = x", {"mista"}),
                          ('setattr(stats, "mista", x)', {"mista"}), ('monkeypatch.setattr(web.stats, "mista", x)', {"mista"}),
                          ('mock.patch.object(stats, "mista", x)', {"mista"}), ('mock.patch("jellyscope.stats.mista", x)', {"mista"}),
                          ("if stats.mista == x:", set()), ("langstats.mista = x", set())):
    check(podvrhy_ve_dverich(ukazka, "stats") == ocekavane, f"(e) pozná podvrh: {ukazka!r} -> {ocekavane}")
for ukazka in ("stats.odkud.mista = x", "web.stats.odkud.mista = x", 'setattr(stats.odkud, "mista", x)',
               'mock.patch.object(stats.odkud, "mista", x)', 'mock.patch("jellyscope.stats.odkud.mista", x)'):
    check(podvrhuje_v_podmodulu(ukazka, "stats", "mista"), f"(e) pozná podvrh v podmodulu: {ukazka!r}")
check(not podvrhuje_v_podmodulu("stats.mista = x", "stats", "mista"), "(e) podvrh jen ve dveřích není podvrh v podmodulu")
check(podvrhuje_v_podmodulu("stats.odkud.mista = x", "stats", "mista", {"odkud"})
      and not podvrhuje_v_podmodulu("stats.prehled.mista = x", "stats", "mista", {"odkud"}),
      "(e) podvrh ve špatném podmodulu se nepočítá")
check(podvrhy_v_podmodulech("api.stats.prehled.overview = x", "stats") == {("prehled", "overview")},
      "(e) pozná podvrhy v podmodulech")

spatne = []
for soubor in sorted(PROJECT.glob("tests/*.py")):
    if soubor.name in (Path(__file__).name, "test_web_balik.py", "_balik.py"):
        continue   # ukázky v řetězcích nejsou podvrhy
    text = soubor.read_text(encoding="utf-8")
    spatne += [f"{soubor.name}: {chyba}" for chyba in spatne_podvrhy(text, "stats", ctenari)]
check(not spatne, f"jména čtená uvnitř balíku se podvrhují v podmodulu ({spatne})")
# A naopak: podvrh v podmodulu, který jméno nečte ani nedefinuje, nezasáhne nic.
jmena_v: dict[str, set[str]] = {}
for cesta in soubory_balicku():
    st = ast.parse(cesta.read_text(encoding="utf-8"))
    jmena_v[cesta.stem] = set(definovane(st)) | {n.id for n in ast.walk(st) if isinstance(n, ast.Name)}
mimo = []
for soubor in sorted(PROJECT.glob("tests/*.py")):
    if soubor.name in (Path(__file__).name, "test_web_balik.py", "_balik.py"):
        continue
    for m, jmeno in sorted(podvrhy_v_podmodulech(soubor.read_text(encoding="utf-8"), "stats")):
        if m in jmena_v and jmeno not in jmena_v[m]:
            mimo.append(f"{soubor.name}: stats.{m}.{jmeno}")
check(not mimo, f"každý podvrh v podmodulu míří tam, kde se jméno čte ({mimo})")

print()
print("--- (g) komentáře ---")
komentaru = sum(1 for c in soubory_balicku() for r in c.read_text(encoding="utf-8").splitlines()
                if r.strip().startswith("#"))
check(KOMENTARU_PUVODNE <= komentaru <= KOMENTARU_PUVODNE + 40,
      f"komentáře se neztratily ({komentaru}, původně {KOMENTARU_PUVODNE})")

print()
print("--- (h) testy, které čtou zdrojáky, vidí i balík ---")
# Test, který prochází jellyscope/*.py bez podsložek, by po rozdělení
# dál procházel - jen by statistiky tiše přestal kontrolovat (SQL pro
# PostgreSQL, GROUP BY, překlady hlášek…). Výjimka jen se značkou
# „jen horní úroveň" a důvodem na stejném řádku.
MELKE = re.compile(r'\(PROJECT / "jellyscope"\)\.glob\("\*\.py"\)')
melke = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py")) if s.resolve() != Path(__file__).resolve()
         for i, radek in enumerate(s.read_text(encoding="utf-8").splitlines(), 1)
         if MELKE.search(radek) and "jen horní úroveň" not in radek]
check(not melke, f"žádný test nečte zdrojáky bez podsložek ({melke})")

print()
print("--- (i) logger se jmenuje jako dřív ---")
# Všechna volání, ne jedno na soubor - druhý getLogger by jinak přebil první.
loggery = [(cesta.name, volani) for cesta in soubory_balicku()
           for volani in re.findall(r"logging\.getLogger\(([^)]*)\)", cesta.read_text(encoding="utf-8"))]
spatne_l = [(s, v) for s, v in loggery if v != '"jellyscope.stats"']
check(loggery and not spatne_l,
      f"podmoduly logují jako jellyscope.stats, ne pod vlastním jménem ({spatne_l or len(loggery)})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
