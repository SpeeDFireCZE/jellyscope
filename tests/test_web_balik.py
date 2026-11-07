r"""Pravidla balíku web/ - web.py rozdělený podle oblastí (přestavba 3b).

`jellyscope/web.py` měl 2 876 řádků: založení aplikace, middleware,
ošetření chyb a 52 rout z osmi oblastí. Je z něj balík `jellyscope/web/`
a přestěhovaly se do něj i `web_zaklad.py` (→ `zaklad.py`) a
`web_nastaveni.py` (→ `nastaveni.py`). Navenek se nemá změnit nic, a tak
test hlídá:

(a) vstupní dveře mají všechno, co se zvenku čte jako `web.X` nebo
    `from jellyscope.web import X` - a jiné moduly přes ně nikdo nebere;
(b) každé jméno bydlí v souboru podle mapy a dveře ukazují na tentýž
    objekt;
(c) bez kruhů: žádný podmodul nesahá na `__init__`, závislosti jedním
    směrem, každý soubor (i `jellyscope.vyvoj`) jde naimportovat jako
    první v čistém procesu;
(d) soubory webu (i podbalíku nastaveni/) do 600 řádků, kromě přestěhovaného zaklad.py;
(e) testy podvrhují tam, kde to zasáhne (tests/_balik.py);
(f) staré soubory nejsou a nikdo je neimportuje ani nečte po staru;
(g) komentáře se při přesunu neztratily;
(h) routy, middleware a ošetření chyb jsou stejné jako před rozdělením;
(i) šablony a statické soubory se hledají na svém místě;
(j) loggery se jmenují jako dřív (`jellyscope.web`).

Spec: docs/superpowers/specs/2026-11-06-web-balik-design.md

Spuštění:
    .venv/bin/python tests/test_web_balik.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "web_balik.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _balik import podvrhuje_v_podmodulu, podvrhy_v_podmodulech, podvrhy_ve_dverich, spatne_podvrhy, routy_naplocho  # noqa: E402
from starlette.routing import Match  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


BALIK = PROJECT / "jellyscope" / "web"
STARY = PROJECT / "jellyscope" / "web.py"
STARE = {"web_zaklad": PROJECT / "jellyscope" / "web_zaklad.py",
         "web_nastaveni": PROJECT / "jellyscope" / "web_nastaveni.py"}
# Soubory balíku; zaklad a nastaveni se jen přestěhovaly, ostatní vznikly z web.py.
SOUBORY = ["__init__", "start", "zaklad", "nastaveni", "obdobi", "prihlaseni", "ucet", "prehled",
           "knihovna", "uzivatele", "historie", "jazyky", "sit", "stav"]
Z_WEB_PY = [m for m in SOUBORY if m not in ("zaklad", "nastaveni")]
# Vývojové nástroje jsou jen v devu (JEN_DEV) - v produ a demu vyvoj.py není,
# a tak tam není ani jeho routa. Test běží i tam (CI produ).
VYVOJ = (PROJECT / "jellyscope" / "vyvoj.py").is_file()
# Soubory s ukázkami podvrhů a jmen v řetězcích - nejsou to skutečná použití.
S_UKAZKAMI = {Path(__file__).name, "test_stats_balik.py", "test_nastaveni_balik.py", "_balik.py"}

# Mapa jmen nejvyšší úrovně web.py -> soubor balíku (spec, oddíl Rozdělení).
PODMODULY: dict[str, list[str]] = {
    "__init__": ["STATIC_DIR", "app", "_cesta_odkud_prisel", "ukazkovy_rezim", "DIVAK_SMI_POST",
                 "rozsah_divaka", "_nesmis", "jazyk_uctu", "ZaznamPouzitiApi", "CHYBA_ZAPISU_PO_SEKUNDACH",
                 "_posledni_chyba_zapisu", "_zapis_pouziti", "DEMO_POVOLENO", "_demo_blokuje",
                 "bezpecnostni_hlavicky", "_asset_version", "KODY_HTTP_CHYB", "handle_http_error",
                 "handle_api_error"],
    # csp a OSM_DLAZDICE jsou čisté a __init__ by jinak přetekl 600 řádků (deník, Task 2).
    "zaklad": ["OSM_DLAZDICE", "csp"],
    "start": ["lifespan"],
    "obdobi": ["ALLOWED_DAYS", "CELA_HISTORIE", "DAY_LABELS", "DEFAULT_DAYS", "DAYS_SESSION_KEY", "_VALID_DAY",
               "_days", "_obdobi_do_session", "KIND_SESSION_KEY", "TOP_KIND_SESSION_KEY", "TOP_ALLOWED_KINDS",
               "_kind", "_datum_z_textu"],
    "prihlaseni": ["_setup_context", "setup_form", "setup_submit", "login_form", "_prihlas_pres_jellyfin",
                   "login_submit", "_kam_po_prihlaseni", "login_quick", "login_quick_stav", "_login_chyba",
                   "logout", "_blokace_hlaska", "_adresa_klienta"],
    "ucet": ["muj_ucet", "_bez_obrazku", "muj_obrazek", "muj_ucet_jazyk", "muj_ucet_pohyb_udalosti",
             "muj_ucet_klic", "muj_ucet_klic_zrusit", "muj_ucet_heslo"],
    "prehled": ["dashboard", "_volby_karet", "SROVNANI_A_KEY", "SROVNANI_B_KEY", "_obdobi_srovnani",
                "_dotaz_obdobi", "srovnani", "top_items_partial", "top_items_vse", "recently_added_partial",
                "network_live_partial", "now_playing_partial", "daily_partial", "vlastni_prehled", "muj_prehled",
                "_vykresli_prehled", "vlastni_prehled_uloz", "muj_prehled_uloz", "insights_page"],
    "knihovna": ["STROP_PRIRAZENI_ARCHIVU", "STROP_MAZANI_ARCHIVU", "library_index", "library_detail",
                 "series_detail", "item_detail", "series_refresh", "item_refresh", "item_delete", "archiv_hledat",
                 "item_priradit", "archiv_priradit", "archiv_smazat", "_over_prirazeni", "item_image"],
    "uzivatele": ["users", "user_detail"],
    "historie": ["OBDOBI", "history"],
    "jazyky": ["languages_undefined", "languages_preferred", "languages_page"],
    "sit": ["network_page", "network_geoip", "network_mapa_podrobna", "network_mapa_podrobna_smazat",
            "network_mapa_dlazdice"],
    "stav": ["health", "api_now_playing"],
}

# --- dnešní stav (vygenerovaný z web.py, web_zaklad.py, web_nastaveni.py a běžící aplikace 6. 11. 2026) ---
PUVODNI = [
    'STATIC_DIR', 'ALLOWED_DAYS', 'CELA_HISTORIE', 'DAY_LABELS', 'DEFAULT_DAYS',
    'DAYS_SESSION_KEY', 'STROP_PRIRAZENI_ARCHIVU', 'STROP_MAZANI_ARCHIVU', '_VALID_DAY',
    'lifespan', 'app', '_cesta_odkud_prisel', 'ukazkovy_rezim', 'DIVAK_SMI_POST', 'rozsah_divaka',
    '_nesmis', 'jazyk_uctu', 'ZaznamPouzitiApi', 'CHYBA_ZAPISU_PO_SEKUNDACH',
    '_posledni_chyba_zapisu', '_zapis_pouziti', 'DEMO_POVOLENO', '_demo_blokuje', 'OSM_DLAZDICE',
    'csp', 'bezpecnostni_hlavicky', '_asset_version', 'KODY_HTTP_CHYB', 'handle_http_error',
    'handle_api_error', '_setup_context', 'setup_form', 'setup_submit', 'login_form',
    '_prihlas_pres_jellyfin', 'login_submit', '_kam_po_prihlaseni', 'login_quick',
    'login_quick_stav', '_login_chyba', 'logout', 'muj_ucet', '_bez_obrazku', 'muj_obrazek',
    'muj_ucet_jazyk', 'muj_ucet_pohyb_udalosti', 'muj_ucet_klic', 'muj_ucet_klic_zrusit',
    'muj_ucet_heslo', '_blokace_hlaska', '_adresa_klienta', '_days', '_obdobi_do_session',
    'KIND_SESSION_KEY', 'TOP_KIND_SESSION_KEY', 'TOP_ALLOWED_KINDS', '_kind', 'dashboard',
    '_volby_karet', 'SROVNANI_A_KEY', 'SROVNANI_B_KEY', '_obdobi_srovnani', '_dotaz_obdobi',
    'srovnani', 'top_items_partial', 'top_items_vse', 'recently_added_partial',
    'network_live_partial', 'now_playing_partial', 'daily_partial', 'library_index',
    'library_detail', 'series_detail', 'item_detail', 'series_refresh', 'item_refresh',
    'item_delete', 'archiv_hledat', 'item_priradit', 'archiv_priradit', 'archiv_smazat',
    '_over_prirazeni', 'item_image', 'users', 'user_detail', 'vlastni_prehled', 'muj_prehled',
    '_vykresli_prehled', 'vlastni_prehled_uloz', 'muj_prehled_uloz', 'insights_page', 'OBDOBI',
    '_datum_z_textu', 'history', 'languages_undefined', 'languages_preferred', 'network_page',
    'network_geoip', 'network_mapa_podrobna', 'network_mapa_podrobna_smazat',
    'network_mapa_dlazdice', 'languages_page', 'health', 'api_now_playing',
]
PUVODNI_ZAKLAD = [
    'log', 'PACKAGE_DIR', 'TEMPLATES_DIR', 'config', 'templates', 'current_account',
    'prihlas_do_relace', 'require_login', 'muj_divak', 'require_admin', '_rychla_obdobi',
    '_obdobi_do_sablony', '_obdobi_popis', '_posledni_den', 'STROP_MIN', 'STROP_MAX',
    'ZOOM_REZIMY', 'PODKLADY_MAPY', 'VZHLEDY', '_vzhled', '_cas_presne', '_stropy',
    'NEANONYMIZOVAT', '_podklad_mapy', '_zoom_rezim', '_linked_note', 'ZPET_NA_IMPORT',
    '_hlaska_importu', '_veta', '_opraveno_note', '_known_note', '_flash', '_smi_pro', 'vidi_pro',
    'otisk_obrazku_uctu', 'adresa_obrazku_uctu', '_context', '_cesky_datum', 'KAPACITA_JEDNOTKY',
    '_clamp', '_novy_klic', '_bez_mezipameti', '_cislo_z_formulare', 'STARTED_AT',
    'SETTINGS_SECTIONS',
]
PUVODNI_NASTAVENI = [
    'router', '_DB_DRAFT', 'MAX_UPLOAD_MB', '_DRAFT_TTL_SECONDS', 'CASTE_ZONY', '_nacti_zalohu',
    '_draft_save', '_draft_read', '_draft_clear', '_zona_existuje', '_pocty_uklidu',
    '_import_ze_souboru', '_dny_v_tydnu', '_kapacita_do_formulare', '_generace_nesedi',
    'settings_page', 'settings_save', 'settings_sync', 'settings_stop', 'settings_scan',
    '_uloz_strom', 'settings_klice', 'VOLBY_KLICE', 'settings_prava_uctu', 'settings_ctenari',
    'settings_divaci', 'settings_connection', 'settings_database', '_uloz_tajemstvi',
    'settings_notifications', 'settings_notification_events', 'settings_notification_texts',
    'settings_notification_test', 'settings_interface', 'settings_updates', 'settings_language',
    'settings_update', '_stranka_aktualizace', 'settings_restart', '_naplanuj_restart',
    'historie_zapomen', 'historie_vrat', 'tasks_save', 'log_stahnout', 'backup_download',
    'backup_delete', 'backup_restore', 'tasks_run', 'import_detect', 'import_playback_reporting',
    'import_playback_reporting_file', 'history_tidy', 'history_orphans', 'history_assign',
    'import_jellystat', 'blocks_unblock', 'blocks_permanent', 'account_create', 'account_password',
    'account_role', '_strom_klice', '_prava_root_klice', '_prava_klice_z_formulare',
    'api_token_novy', 'api_token_prava', 'api_token_zrusit', 'api_token_vypnout',
    'api_token_zapnout', 'account_delete',
]
TRASY = sorted('''
GET / dashboard
GET /account muj_ucet
GET /account/obrazek muj_obrazek
GET /account/prehled muj_prehled
GET /api/now-playing api_now_playing
GET /api/v1 rozcestnik
GET /api/v1/ rozcestnik
GET /api/v1/bandwidth provoz
GET /api/v1/history historie
GET /api/v1/insights zjisteni
GET /api/v1/item/{item_id} titul
GET /api/v1/least-played nejmene_hrane
GET /api/v1/libraries knihovny
GET /api/v1/library knihovna
GET /api/v1/now-playing prave_hraje
GET /api/v1/play-methods zpusoby_prehrani
GET /api/v1/recently-added nedavno_pridane
GET /api/v1/summary souhrn
GET /api/v1/top-items nejsledovanejsi
GET /api/v1/unwatched nesledovane
GET /api/v1/users divaci
GET /archiv/hledat archiv_hledat
GET /dashboard vlastni_prehled
GET /health health
GET /history history
GET /image/{item_id} item_image
GET /insights insights_page
GET /item/{item_id} item_detail
GET /languages languages_page
GET /languages/bez-jazyka languages_undefined
GET /library library_index
GET /library/{library_id} library_detail
GET /login login_form
GET /login/quick/stav login_quick_stav
GET /network network_page
GET /network/mapa/dlazdice/{podrobnost}/{x}/{y} network_mapa_dlazdice
GET /partials/daily daily_partial
GET /partials/network-live network_live_partial
GET /partials/now-playing now_playing_partial
GET /partials/recently-added recently_added_partial
GET /partials/top-items top_items_partial
GET /series/{series_id} series_detail
GET /settings settings_page
GET /settings/backup/download backup_download
GET /settings/history/orphans history_orphans
GET /settings/log/stahnout log_stahnout
GET /setup setup_form
GET /srovnani srovnani
GET /top-items top_items_vse
GET /users users
GET /users/{user_id} user_detail
GET,HEAD /openapi.json openapi
MOUNT /static static
POST /account/klic muj_ucet_klic
POST /account/klic/zrusit muj_ucet_klic_zrusit
POST /account/language muj_ucet_jazyk
POST /account/password muj_ucet_heslo
POST /account/prehled muj_prehled_uloz
POST /account/udalosti-pohyb muj_ucet_pohyb_udalosti
POST /dashboard/layout vlastni_prehled_uloz
POST /item/{item_id}/delete item_delete
POST /item/{item_id}/priradit item_priradit
POST /item/{item_id}/refresh item_refresh
POST /languages/preferred languages_preferred
POST /library/{library_id}/archiv/priradit archiv_priradit
POST /library/{library_id}/archiv/smazat archiv_smazat
POST /login login_submit
POST /login/quick login_quick
POST /logout logout
POST /network/geoip network_geoip
POST /network/mapa-podrobna network_mapa_podrobna
POST /network/mapa-podrobna/smazat network_mapa_podrobna_smazat
POST /series/{series_id}/refresh series_refresh
POST /settings settings_save
POST /settings/accounts/create account_create
POST /settings/accounts/delete account_delete
POST /settings/accounts/klice settings_klice
POST /settings/accounts/password account_password
POST /settings/accounts/prava settings_prava_uctu
POST /settings/accounts/role account_role
POST /settings/api/token api_token_novy
POST /settings/api/token/prava api_token_prava
POST /settings/api/token/vypnout api_token_vypnout
POST /settings/api/token/zapnout api_token_zapnout
POST /settings/api/token/zrusit api_token_zrusit
POST /settings/backup/delete backup_delete
POST /settings/backup/restore backup_restore
POST /settings/blocks/permanent blocks_permanent
POST /settings/blocks/unblock blocks_unblock
POST /settings/connection settings_connection
POST /settings/ctenari settings_ctenari
POST /settings/database settings_database
POST /settings/divaci settings_divaci
POST /settings/historie/vrat historie_vrat
POST /settings/historie/zapomen historie_zapomen
POST /settings/history/assign history_assign
POST /settings/history/tidy history_tidy
POST /settings/import/detect import_detect
POST /settings/import/jellystat import_jellystat
POST /settings/import/playback-reporting import_playback_reporting
POST /settings/import/playback-reporting-file import_playback_reporting_file
POST /settings/interface settings_interface
POST /settings/language settings_language
POST /settings/notifications settings_notifications
POST /settings/notifications/events settings_notification_events
POST /settings/notifications/test settings_notification_test
POST /settings/notifications/texts settings_notification_texts
POST /settings/restart settings_restart
POST /settings/scan settings_scan
POST /settings/stop settings_stop
POST /settings/sync settings_sync
POST /settings/tasks tasks_save
POST /settings/tasks/run tasks_run
POST /settings/update settings_update
POST /settings/updates settings_updates
POST /settings/vyvoj/udalost-datum nastav_datum
POST /setup setup_submit
'''.strip().splitlines())
MIDDLEWARE = [('BaseHTTPMiddleware', 'bezpecnostni_hlavicky'), ('ZaznamPouzitiApi', ''), ('SessionMiddleware', ''), ('BaseHTTPMiddleware', 'jazyk_uctu'), ('BaseHTTPMiddleware', 'rozsah_divaka'), ('BaseHTTPMiddleware', 'ukazkovy_rezim')]
CHYBY = ['fastapi.exceptions.HTTPException', 'fastapi.exceptions.RequestValidationError',
         'fastapi.exceptions.WebSocketRequestValidationError', 'jellyscope.api_dotaz.ChybaApi',
         'starlette.exceptions.HTTPException']

# Řádky komentářů dnes: web.py 429 + web_zaklad.py 122 + web_nastaveni.py 252.
# 15 = pět prázdných nadpisů po dřívějším přesunu Nastavení (3 řádky každý).
KOMENTARU_PUVODNE = 803
SMAZANE_NADPISY = 15


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


def soubor(m: str) -> Path:
    if m == "nastaveni":            # od 3e podbalík web/nastaveni/ (hlídá ho test_nastaveni_balik)
        return BALIK / "nastaveni" / "__init__.py"
    return BALIK / ("__init__.py" if m == "__init__" else f"{m}.py")


def je_balik() -> bool:
    return (BALIK / "__init__.py").is_file()


def strom(cesta: Path) -> ast.Module:
    return ast.parse(cesta.read_text(encoding="utf-8"))


def zdrojaky_mimo_balik() -> list[Path]:
    """Kód a testy, které web používají zvenku (bez balíku a jeho budoucích částí)."""
    vse = [*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
           *(PROJECT / jmeno for jmeno in ("demo.py", "run.py", "manage.py"))]
    return sorted(p for p in vse if p.is_file() and BALIK not in p.parents
                  and p not in (STARY, *STARE.values()) and p.name not in S_UKAZKAMI)


web = importlib.import_module("jellyscope.web")
MODULY = ({p.stem for p in (PROJECT / "jellyscope").glob("*.py")}  # jen horní úroveň: jména modulů
          | {p.parent.name for p in (PROJECT / "jellyscope").glob("*/__init__.py")}) - {"web", "__init__"}


print("--- (f) staré soubory ---")
check(je_balik() and not STARY.exists() and not any(p.exists() for p in STARE.values()),
      "web.py, web_zaklad.py a web_nastaveni.py nejsou, balík web/ ano")
STARA_JMENA = set(STARE)
stare_importy = []
for cesta in [*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
              *(PROJECT / jmeno for jmeno in ("demo.py", "run.py", "manage.py"))]:
    if not cesta.is_file():
        continue
    for uzel in ast.walk(strom(cesta)):
        if isinstance(uzel, ast.ImportFrom):
            if uzel.module and uzel.module.split(".")[-1] in STARA_JMENA:
                stare_importy.append(f"{cesta.name}:{uzel.lineno}")
            elif uzel.module in (None, "jellyscope") and any(a.name in STARA_JMENA for a in uzel.names):
                stare_importy.append(f"{cesta.name}:{uzel.lineno}")
        elif isinstance(uzel, ast.Import) and any(a.name.split(".")[-1] in STARA_JMENA for a in uzel.names):
            stare_importy.append(f"{cesta.name}:{uzel.lineno}")
check(not stare_importy, f"nikdo neimportuje web_zaklad ani web_nastaveni ({stare_importy[:6]})")
# Test, který čte zdrojáky webu po staru, by po rozdělení tiše nic nekontroloval.
PO_STARU = re.compile(r"""glob\("web\*\.py"\)|["']web(_zaklad|_nastaveni)?\.py["']""")
po_staru = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py"))
            if s.name not in S_UKAZKAMI and s.name != "test_sync_do_produ.py"   # jména souborů ve fixture
            for i, radek in enumerate(s.read_text(encoding="utf-8").splitlines(), 1) if PO_STARU.search(radek)]
check(not po_staru, f"žádný test nečte zdrojáky webu po staru ({po_staru[:6]})")
# Web má podbalík (nastaveni/, 3e): glob("*.py") nad web/ ho nevidí a test by
# Nastavení tiše vynechal. Číst se má rglob, nastaveni.py už není.
MELCE = re.compile("|".join((
    r"""["'](?:jellyscope/)?web["']\)\.glob\(["']\*\.py["']\)""",      # (… / "web").glob("*.py")
    r"""glob\(["']jellyscope/web/\*\.py["']\)""",                     # PROJECT.glob("jellyscope/web/*.py")
    r"""(?:["']|/)nastaveni\.py["']""",                                 # "nastaveni.py", "…/web/nastaveni.py"
    r"""getsource\(\s*(?:web_)?nastaveni\s*\)""",                       # getsource(modulu) = jen __init__
)))


ALIAS_NASTAVENI = re.compile(
    r"(?:from\s+jellyscope\.web\s+import\s+nastaveni|import\s+jellyscope\.web\.nastaveni)\s+as\s+(\w+)")


def melke_radky(text: str) -> list[int]:
    """Čísla řádků testu, které čtou web mělce nebo nastaveni.py podle cesty.

    `getsource` podbalíku vrátí jen jeho __init__ - i když se jmenuje vlastním
    aliasem (`from jellyscope.web import nastaveni as _wn`).
    """
    aliasy = ALIAS_NASTAVENI.findall(text)
    pres_alias = (re.compile(rf"getsource\(\s*(?:{'|'.join(map(re.escape, aliasy))})\s*\)")
                  if aliasy else None)
    return [i for i, radek in enumerate(text.splitlines(), 1)
            if MELCE.search(radek) or (pres_alias and pres_alias.search(radek))]


melce = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py"))
         if s.name not in S_UKAZKAMI and s.name != "test_sync_do_produ.py"
         for i in melke_radky(s.read_text(encoding="utf-8"))]
check(not melce, f"žádný test nečte web mělce ani nastaveni.py podle cesty ({melce})")
# Kontrola kontroly: pozná i jiné zápisy mělkého čtení.
for ukazka in ('sorted((PROJECT / "jellyscope" / "web").glob("*.py"))', 'PROJECT.glob("jellyscope/web/*.py")',
               '(PROJECT / "jellyscope/web").glob("*.py")', 'PROJECT / "jellyscope/web/nastaveni.py"',
               '"web" / "nastaveni.py"', "inspect.getsource(web_nastaveni)", "inspect.getsource(nastaveni)"):
    check(bool(MELCE.search(ukazka)), f"pojistka pozná mělké čtení: {ukazka}")
check(not MELCE.search('sorted((PROJECT / "jellyscope" / "web").rglob("*.py"))'), "rglob pojistka pouští")
check(melke_radky("from jellyscope.web import nastaveni as _wn\nx = inspect.getsource(_wn)\n") == [2],
      "pojistka pozná getsource i přes alias podbalíku (as _wn)")
# app.routes skrývá routy z include_router (_IncludedRouter) - test, který
# je prochází přímo, vidí po rozdělení jen pár adres a tiše nic nehlídá.
PRIMO = re.compile(r"\bapp\.(?:router\.)?routes\b")
primo = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py")) if s.name not in S_UKAZKAMI
         for i, radek in enumerate(s.read_text(encoding="utf-8").splitlines(), 1)
         if PRIMO.search(radek) and "routy_naplocho(" not in radek]
check(not primo, f"routy aplikace se procházejí přes routy_naplocho ({primo})")

print()
print("--- (a) vstupní dveře mají všechno, co se zvenku čte ---")
JMENO_WEB = re.compile(r"(?<![\w.])_?web\.([A-Za-z_]\w*)")
FROM_WEB = re.compile(r"from\s+jellyscope\.web\s+import\s+\(?([^)\n]+)\)?")
pouzite: dict[str, str] = {}
for cesta in zdrojaky_mimo_balik():
    text = cesta.read_text(encoding="utf-8")
    for jmeno in JMENO_WEB.findall(text):
        pouzite.setdefault(jmeno, cesta.name)
    for skupina in FROM_WEB.findall(text):
        for kus in skupina.split("#")[0].split(","):
            jmeno = kus.split(" as ")[0].strip()
            if jmeno:
                pouzite.setdefault(jmeno, cesta.name)
pouzite.pop("py", None)                      # „web.py" v komentářích
moduly = sorted(f"{j} ({kde})" for j, kde in pouzite.items() if j in MODULY)
chybi = sorted(f"{j} ({kde})" for j, kde in pouzite.items()
               if j not in MODULY and j not in SOUBORY and not hasattr(web, j))
check(len(pouzite) > 15 and not chybi, f"všech {len(pouzite)} jmen jde dveřmi (chybí {chybi[:6]})")
check(not moduly, f"jiné moduly přes web nikdo nebere - modul si naimportuj přímo ({moduly})")

print()
print("--- (b) každé jméno bydlí ve svém souboru ---")
check(sorted(j for js in PODMODULY.values() for j in js) == sorted(PUVODNI),
      f"mapa pokrývá všech {len(PUVODNI)} jmen web.py, každé jednou")
if je_balik():
    chybi_v = []
    for m, jmena in [*PODMODULY.items(), ("zaklad", PUVODNI_ZAKLAD), ("nastaveni", PUVODNI_NASTAVENI)]:
        if m == "nastaveni":        # jména podbalíku vystaví jeho dveře
            tam = set(dir(importlib.import_module("jellyscope.web.nastaveni")))
        else:
            tam = definovane(strom(soubor(m))) if soubor(m).is_file() else set()
        chybi_v += [f"{m}.{j}" for j in jmena if j not in tam]
    check(not chybi_v, f"jména jsou v souborech podle mapy ({chybi_v[:6]})")
    jine = []
    for m in SOUBORY[1:]:
        modul = importlib.import_module(f"jellyscope.web.{m}")
        for j in definovane(strom(soubor(m))):
            if hasattr(web, j) and getattr(web, j) is not getattr(modul, j):
                jine.append(f"{m}.{j}")
    check(not jine, f"dveře ukazují na tytéž objekty jako podmoduly ({jine[:6]})")
else:
    check(False, "jména jsou v souborech podle mapy (balík web/ není)")

print()
print("--- (c) bez kruhů ---")
if je_balik():
    hrany: dict[str, set[str]] = {m: set() for m in SOUBORY}
    zakazane = []
    for m in SOUBORY[1:]:
        if m == "nastaveni":
            continue                 # podbalík má vlastní pravidla (test_nastaveni_balik)
        for uzel in ast.walk(strom(soubor(m))):
            if isinstance(uzel, ast.ImportFrom):
                if uzel.level == 1 and uzel.module is None:
                    for a in uzel.names:
                        (hrany[m].add(a.name) if a.name in SOUBORY else zakazane.append(f"{m}: from . import {a.name}"))
                elif uzel.level == 1:
                    (hrany[m].add(uzel.module) if uzel.module in SOUBORY
                     else zakazane.append(f"{m}: from .{uzel.module}"))
                elif uzel.level == 2 and uzel.module is None and any(a.name == "web" for a in uzel.names):
                    zakazane.append(f"{m}: from .. import web")
                elif uzel.level == 0 and (uzel.module or "").startswith("jellyscope.web"):
                    zakazane.append(f"{m}: from {uzel.module}")
            elif isinstance(uzel, ast.Import) and any(a.name.startswith("jellyscope.web") for a in uzel.names):
                zakazane.append(f"{m}: import jellyscope.web")
    check(not zakazane and not any("__init__" in h for h in hrany.values()),
          f"žádný podmodul nesahá na __init__ ({zakazane[:6]})")
    zbyva = {m: set(h) for m, h in hrany.items()}
    while any(not h for h in zbyva.values()):
        hotove = {m for m, h in zbyva.items() if not h}
        zbyva = {m: h - hotove for m, h in zbyva.items() if m not in hotove}
    check(not zbyva, f"závislosti bez kruhu ({sorted(zbyva)})")
    check(not hrany["zaklad"] and hrany["obdobi"] <= {"zaklad"},
          f"zaklad nesahá na sousedy, obdobi jen na zaklad ({hrany['zaklad']}, {hrany['obdobi']})")
else:
    check(False, "žádný podmodul nesahá na __init__ (balík web/ není)")
NAPLOCHO = """
def plocha(routy):
    for r in routy:
        if type(r).__name__ == "_IncludedRouter":
            yield from plocha(r.original_router.routes)
        else:
            yield r
"""
# Import podmodulu vždy napřed spustí __init__ balíku (a ten načte všechny podmoduly) -
# jedna kontrola na balík pokryje všechny; vyvoj.py je jiný vstup (kruh přes web.zaklad).
prvni = ["jellyscope.web"] if je_balik() else []
if not VYVOJ:
    print("       jellyscope/vyvoj.py tu není (prod, demo) - jeho import se přeskakuje")
for jmeno in [*prvni, *(["jellyscope.vyvoj"] if VYVOJ else [])]:
    kod = f"import {jmeno}\n"
    if jmeno == "jellyscope.vyvoj":
        kod += NAPLOCHO + ("import jellyscope.web as w\n"
                           "print('VYVOJ' if any(getattr(r, 'path', '') == '/settings/vyvoj/udalost-datum'"
                           " for r in plocha(w.app.router.routes)) else 'BEZ VYVOJE')\n")
    beh = subprocess.run([sys.executable, "-c", kod], cwd=PROJECT, env=os.environ.copy(),
                         capture_output=True, text=True, timeout=180)
    chyba = (beh.stderr.strip().splitlines() or [""])[-1]
    check(beh.returncode == 0 and (jmeno != "jellyscope.vyvoj" or "VYVOJ" in beh.stdout),
          f"`import {jmeno}` jako první v čistém procesu ({beh.stdout.strip()[-20:]} {chyba[:120]})")

print()
print("--- (d) velikost ---")
if je_balik():
    # Celý strom webu (i podbalík nastaveni/); výjimka jen zaklad.py - přestěhovaný beze změny.
    velke = [(str(p.relative_to(BALIK)), n) for p in sorted(BALIK.rglob("*.py")) if p != BALIK / "zaklad.py"
             and (n := len(p.read_text(encoding="utf-8").splitlines())) > 600]
else:
    velke = [("web.py", len(STARY.read_text(encoding="utf-8").splitlines()))]
check(not velke, f"soubory webu do 600 řádků, kromě zaklad.py ({velke})")

print()
print("--- (e) testy podvrhují tam, kde to zasáhne ---")
# Jméno čtené v souboru vzniklém z web.py (mimo __init__) podvrh ve
# dveřích nezasáhne. zaklad a nastaveni si jména braly samy už dřív -
# podvrh `web.X` je nikdy neviděl, a tak se nepočítají.
KDE = {j: m for m, js in PODMODULY.items() for j in js}
# Kdo které jméno čte: podvrh musí být v podmodulu, kde se čte.
ctenari: dict[str, set[str]] = {}
if je_balik():
    for m in Z_WEB_PY[1:]:
        for jmeno in {n.id for n in ast.walk(strom(soubor(m))) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}:
            ctenari.setdefault(jmeno, set()).add(m)
else:
    for uzel in strom(STARY).body:
        jmena = definovane(ast.Module(body=[uzel], type_ignores=[]))
        moduly = {KDE.get(j, "__init__") for j in jmena} - {"__init__"}
        for jmeno in {n.id for n in ast.walk(uzel) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}:
            ctenari.setdefault(jmeno, set()).update(moduly)
ctenari = {j: m for j, m in ctenari.items() if m}
vnitrni = set(ctenari)
for ukazka, ocekavane in (("web.JellyfinClient = x", {"JellyfinClient"}),
                          ('mock.patch("jellyscope.web.JellyfinClient", x)', {"JellyfinClient"}),
                          ("web_nastaveni.JellyfinClient = x", set())):
    check(podvrhy_ve_dverich(ukazka, "web") == ocekavane, f"(e) pozná podvrh ve dveřích: {ukazka!r}")
check(podvrhuje_v_podmodulu("web.knihovna.JellyfinClient = x", "web", "JellyfinClient")
      and not podvrhuje_v_podmodulu("web.JellyfinClient = x", "web", "JellyfinClient"),
      "(e) pozná podvrh v podmodulu (web.knihovna.X), podvrh ve dveřích ne")
# Podvrh se počítá jen v podmodulu, který jméno opravdu čte: /image bere
# JellyfinClient z web.knihovna, podvrh ve web.ucet by ho minul.
check(podvrhuje_v_podmodulu("web.knihovna.JellyfinClient = x", "web", "JellyfinClient", {"knihovna"})
      and not podvrhuje_v_podmodulu("web.ucet.JellyfinClient = x", "web", "JellyfinClient", {"knihovna"}),
      "(e) podvrh ve špatném podmodulu se nepočítá")
check(podvrhy_v_podmodulech("web.ucet.JellyfinClient = x\nmock.patch('jellyscope.web.sit.geoip', y)", "web")
      == {("ucet", "JellyfinClient"), ("sit", "geoip")}, "(e) pozná podvrhy v podmodulech")
spatne = []
for cesta in sorted(PROJECT.glob("tests/*.py")):
    if cesta.name in S_UKAZKAMI:
        continue
    text = cesta.read_text(encoding="utf-8")
    spatne += [f"{cesta.name}: {chyba}" for chyba in spatne_podvrhy(text, "web", ctenari)]
check(not spatne, f"jména čtená v podmodulu se podvrhují v podmodulu ({spatne})")
# A naopak: podvrh v podmodulu, který jméno nečte ani nedefinuje, nezasáhne nic.
jmena_v: dict[str, set[str]] = {}
if je_balik():
    for m in SOUBORY[1:]:
        st = strom(soubor(m))
        jmena_v[m] = definovane(st) | {n.id for n in ast.walk(st) if isinstance(n, ast.Name)}
mimo = []
for cesta in sorted(PROJECT.glob("tests/*.py")):
    if cesta.name in S_UKAZKAMI:
        continue
    for m, jmeno in sorted(podvrhy_v_podmodulech(cesta.read_text(encoding="utf-8"), "web")):
        if m in jmena_v and jmeno not in jmena_v[m]:
            mimo.append(f"{cesta.name}: web.{m}.{jmeno}")
check(not mimo, f"každý podvrh v podmodulu míří tam, kde se jméno čte ({mimo})")

print()
print("--- (g) komentáře ---")
if je_balik():
    komentaru = sum(1 for p in BALIK.rglob("*.py") for r in p.read_text(encoding="utf-8").splitlines()
                    if r.strip().startswith("#"))
    dolni = KOMENTARU_PUVODNE - SMAZANE_NADPISY
    check(dolni <= komentaru <= dolni + 40, f"komentáře se neztratily ({komentaru}, čeká se {dolni}-{dolni + 40})")
else:
    print("       čeká na balík (viz f)")

print()
print("--- (h) routy, middleware, ošetření chyb ---")
routy = list(routy_naplocho(web.app.router.routes))
tabulka = sorted(f"MOUNT {r.path} {r.name}" if type(r).__name__ == "Mount"
                 else f"{','.join(sorted(getattr(r, 'methods', None) or []))} {r.path} {r.name}" for r in routy)
cekane = [t for t in TRASY if VYVOJ or " /settings/vyvoj/" not in t]
navic, chybi_r = sorted(set(tabulka) - set(cekane)), sorted(set(cekane) - set(tabulka))
check(tabulka == cekane, f"všech {len(cekane)} rout jako dřív (navíc {navic[:4]}, chybí {chybi_r[:4]})")
srazky = []
for r in routy:
    for metoda in sorted(getattr(r, "methods", None) or []):
        scope = {"type": "http", "path": re.sub(r"\{[^}]+\}", "x1", r.path), "method": metoda, "root_path": ""}
        jine = [s.path for s in routy if s is not r and type(s).__name__ != "Mount"
                and metoda in (getattr(s, "methods", None) or set()) and s.matches(scope)[0] == Match.FULL]
        if jine:
            srazky.append(f"{metoda} {r.path} ~ {jine}")
check(not srazky, f"žádná routa nezastíní jinou ({srazky[:4]})")
middleware = [(m.cls.__name__, getattr(m.kwargs.get("dispatch"), "__name__", "")) for m in web.app.user_middleware]
check(middleware == MIDDLEWARE, f"middleware ve stejném pořadí ({middleware})")
chyby = sorted(f"{k.__module__}.{k.__name__}" for k in web.app.exception_handlers)
check(chyby == CHYBY, f"ošetření chyb stejné ({chyby})")

print()
print("--- (i) cesty ---")
if je_balik():
    zaklad = importlib.import_module("jellyscope.web.zaklad")
    sablony, staticke = Path(zaklad.TEMPLATES_DIR), Path(web.STATIC_DIR)
    check(sablony == PROJECT / "jellyscope" / "templates" and sablony.is_dir(), f"šablony v jellyscope/templates ({sablony})")
    check(staticke == PROJECT / "jellyscope" / "static" and staticke.is_dir(), f"statické soubory v jellyscope/static ({staticke})")
else:
    check(False, "šablony a statické soubory (balík web/ není)")

print()
print("--- (j) loggery ---")
if je_balik():
    # Všechna volání, ne jedno na soubor - druhý getLogger by jinak přebil první.
    loggery = [(p.name, v) for p in sorted(BALIK.rglob("*.py"))
               for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
    spatne_l = [(s, v) for s, v in loggery if v != '"jellyscope.web"']
    check(loggery and not spatne_l,
          f"balík loguje jako jellyscope.web, ne pod jménem souboru ({spatne_l or len(loggery)})")
else:
    check(False, "loggery (balík web/ není)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
