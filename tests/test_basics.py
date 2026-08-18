r"""Test prihlasovani, uctu, prazdne databaze a pomocnych funkci.

Prazdna databaze je stav, ve kterem aplikaci uvidis jako uplne prvni -
a je to zaroven stav, ve kterem se nejsnadneji neco rozbije (deleni nulou,
prazdny seznam, chybejici radek). Proto se testuje zvlast.

Spusteni:
    .\.venv\Scripts\python.exe tests\test_basics.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# Vlastni slozka pro kazdy beh. Drive tu byl pevny nazev v %TEMP% -
# jenze pak si dva soucasne behy testu (nebo dva lide) sahali na tentyz
# soubor a padaly na sobe navzajem. Chyba pritom vypadala jako chyba
# aplikace, ne jako kolize.
TMP_DB = Path(tempfile.mkdtemp()) / "jellyscope_test_basics.db"

os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(TMP_DB)
os.environ["SECRET_KEY"] = "testovaci-klic"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, charts, formatting, languages, probe  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0
ROUTES = ["/", "/insights", "/languages", "/library", "/users", "/history",
          "/settings", "/?days=365"]


def check(ok: bool, message: str) -> None:
    global failures
    print(f"{'OK     ' if ok else 'CHYBA  '}{message}")
    if not ok:
        failures += 1


with TestClient(app) as client:
    print("--- bez uctu musi vse vest na prvni nastaveni ---")
    for route in ROUTES:
        response = client.get(route, follow_redirects=False)
        check(
            response.status_code == 303 and response.headers.get("location") == "/setup",
            f"{route} -> {response.status_code} {response.headers.get('location', '')}",
        )
    check(client.get("/setup").status_code == 200, "/setup se zobrazi")

    print("--- zalozeni prvniho spravce ---")
    bad_inputs = [
        ({"username": "ab", "password": "dlouheheslo", "password_again": "dlouheheslo"},
         "prilis kratke jmeno"),
        ({"username": "petr", "password": "krat", "password_again": "krat"},
         "prilis kratke heslo"),
        ({"username": "petr", "password": "dlouheheslo", "password_again": "jineheslo"},
         "hesla se neshoduji"),
        ({"username": "petr novak", "password": "dlouheheslo", "password_again": "dlouheheslo"},
         "mezera ve jmene"),
    ]
    for data, label in bad_inputs:
        response = client.post("/setup", data=data, follow_redirects=False)
        check(response.status_code == 400, f"odmitnuto: {label}")
    check(not accounts.any_exists(), "po neplatnych pokusech porad zadny ucet")

    response = client.post(
        "/setup",
        data={"username": "petr", "password": "tajneheslo", "password_again": "tajneheslo"},
        follow_redirects=False,
    )
    check(response.status_code == 303, "spravce vytvoren a rovnou prihlasen")
    check(accounts.count() == 1, "v databazi je jeden ucet")
    check(accounts.all_accounts()[0]["is_admin"] == 1, "prvni ucet je spravce")

    # Heslo se nikdy nesmi ulozit v citelne podobe.
    stored = accounts.get_by_name("petr")["password_hash"]
    check("tajneheslo" not in stored, "heslo neni v databazi v citelne podobe")
    check(stored.startswith("pbkdf2_sha256$"), "heslo je ulozene jako otisk PBKDF2")
    check(accounts.verify_password("tajneheslo", stored), "otisk sedi na spravne heslo")
    check(not accounts.verify_password("jineheslo", stored), "otisk nesedi na spatne heslo")

    # Druhy "prvni spravce" uz projit nesmi.
    response = client.post(
        "/setup",
        data={"username": "podvod", "password": "dlouheheslo", "password_again": "dlouheheslo"},
        follow_redirects=False,
    )
    check(accounts.count() == 1, "opakovane /setup dalsi ucet nezalozi")

    print("--- prazdna databaze, prihlaseny ---")
    for route in ROUTES + ["/health", "/api/now-playing", "/users/neexistuje"]:
        expected = 404 if route == "/users/neexistuje" else 200
        try:
            response = client.get(route)
        except Exception as exc:  # noqa: BLE001
            check(False, f"{route}: {type(exc).__name__}: {exc}")
            continue
        check(response.status_code == expected,
              f"{route} -> {response.status_code} ({len(response.content)} B)")

    print("--- sprava uctu ---")
    response = client.post("/settings/accounts/create", data={
        "username": "jana", "password": "jineheslo1", "password_again": "jineheslo1",
    }, follow_redirects=False)
    check(accounts.count() == 2, "spravce zalozil ctenarsky ucet")
    check(accounts.get_by_name("jana")["is_admin"] == 0, "novy ucet neni spravce")

    response = client.post("/settings/accounts/create", data={
        "username": "JANA", "password": "jineheslo1", "password_again": "jineheslo1",
    }, follow_redirects=True)
    check(accounts.count() == 2, "duplicitni jmeno (jina velikost pismen) odmitnuto")

    check(client.post("/logout", follow_redirects=False).status_code == 303, "odhlaseni")
    check(client.get("/", follow_redirects=False).status_code == 303, "po odhlaseni zavora")

    print("--- prihlaseni ---")
    response = client.post("/login", data={"username": "petr", "password": "spatne"},
                           follow_redirects=False)
    check(response.status_code == 401, f"spatne heslo odmitnuto ({response.status_code})")

    response = client.post("/login", data={"username": "neexistuje", "password": "cokoliv"},
                           follow_redirects=False)
    check(response.status_code == 401, "neexistujici ucet odmitnut")

    response = client.post("/login", data={"username": "jana", "password": "jineheslo1"},
                           follow_redirects=False)
    check(response.status_code == 303, "ctenar se prihlasil")

    print("--- opravneni ctenare ---")
    check(client.get("/").status_code == 200, "ctenar vidi statistiky")
    check(client.get("/settings").status_code == 200, "ctenar vidi nastaveni (jen ke cteni)")

    for route, data in [
        ("/settings", {"tech_source": "ffprobe", "poll_interval": "5",
                       "library_sync_minutes": "10", "ffprobe_path": "",
                       "ffprobe_concurrency": "2", "path_mappings": "[]"}),
        ("/settings/sync", {}),
        ("/settings/connection", {"jellyfin_url": "http://x", "action": "test"}),
        ("/settings/accounts/create", {"username": "vetrelec", "password": "dlouheheslo",
                                       "password_again": "dlouheheslo"}),
    ]:
        response = client.post(route, data=data, follow_redirects=False)
        check(response.status_code == 403, f"ctenari zamitnuto: POST {route}")

    check(accounts.count() == 2, "ctenar zadny ucet nezalozil")

    # Vlastni heslo si ale zmenit smi.
    jana = accounts.get_by_name("jana")
    response = client.post("/settings/accounts/password", data={
        "account_id": jana["id"], "password": "noveheslo1", "password_again": "noveheslo1",
    }, follow_redirects=False)
    check(response.status_code == 303, "ctenar si zmenil vlastni heslo")
    check(accounts.authenticate("jana", "noveheslo1") is not None, "nove heslo funguje")

    # Cizi uz ne.
    petr = accounts.get_by_name("petr")
    response = client.post("/settings/accounts/password", data={
        "account_id": petr["id"], "password": "prevzato11", "password_again": "prevzato11",
    }, follow_redirects=False)
    check(response.status_code == 403, "ctenar nesmi menit cizi heslo")
    check(accounts.authenticate("petr", "tajneheslo") is not None, "cizi heslo zustalo")

    client.post("/logout")

    print("--- posledniho spravce nelze odstranit ---")
    client.post("/login", data={"username": "petr", "password": "tajneheslo"})
    response = client.post("/settings/accounts/delete",
                           data={"account_id": petr["id"]}, follow_redirects=True)
    check(accounts.get_by_name("petr") is not None, "vlastni ucet smazat nejde")

    # Test spojeni je soucasti formulare pro pripojeni - testuje to,
    # co je vyplnene, ne ulozene nastaveni.
    response = client.post(
        "/settings/connection",
        data={"jellyfin_url": "http://127.0.0.1:1", "action": "test"},
        follow_redirects=True,
    )
    check(
        response.status_code == 200 and "selhalo" in response.text.lower(),
        "test spojeni na nedostupny server hlasi chybu misto padu",
    )

    # Prazdna adresa nesmi skoncit hlaskou o chybejicim http:// - to je
    # matouci. Ma rict, ze adresa neni vyplnena.
    response = client.post(
        "/settings/connection", data={"jellyfin_url": "", "action": "test"},
        follow_redirects=True,
    )
    check("vyplň adresu" in response.text.lower() or "vyplň adresu" in response.text,
          "prazdna adresa: rekne, ze chybi adresa")

print("--- zachovani pozice pri zmene obdobi ---")
# Filtr 7/30/90 dnu meni celou stranku, takze se nacita znovu - a nove
# nacteni skoci na zacatek. Skript v base.html si pozici pamatuje.
makra = (PROJECT / "jellyscope" / "templates" / "_macros.html").read_text(encoding="utf-8")
base = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
check("data-keep-scroll" in makra, "filtr obdobi je oznaceny data-keep-scroll")
check("data-keep-scroll" in base, "base.html ma obsluhu, ktera pozici uklada")
check("sessionStorage" in base and "scrollTo" in base,
      "pozice se uklada do sessionStorage a obnovuje scrollTo")
check("localStorage.setItem(\"scroll" not in base,
      "pozice nezustava v localStorage navzdy")

print("--- formatovani cisel ---")
for got, want in [
    (formatting.bytes_human(0), "-"),
    (formatting.bytes_human(1536), "2 KB"),
    (formatting.bytes_human(52 * 1024 ** 3), "52,0 GB"),
    (formatting.bytes_human(520 * 1024 ** 3), "520 GB"),
    (formatting.hours_human(0), "0 min"),
    (formatting.hours_human(0.5), "30 min"),
    (formatting.hours_human(3.7), "3 h 42 min"),
    (formatting.bitrate_human(8_000_000), "8,0 Mb/s"),
    (formatting.bitrate_human(None), "-"),
    (formatting.resolution_human(2160), "4K"),
    (formatting.resolution_human(None), "-"),
    (formatting.number(1234567), "1 234 567"),
    (formatting.percent(12.345, 1), "12,3 %"),
    (formatting.datetime_human(None), "-"),
    (formatting.relative_human(None), "-"),
]:
    check(got == want, f"{got!r} == {want!r}")

for raw in ("2026-08-12T09:47:00.1234567Z", "2026-08-12 09:47:00", "2026-08-12T09:47:00+02:00"):
    check(formatting.datetime_human(raw) != "-", f"cas {raw!r} precten")

print("--- sjednocovani jazyku ---")
# Ruzne zapisy tehoz jazyka musi skoncit u jedne hodnoty - jinak by
# statistika ukazala ctyri "cestiny".
for raw, want in [
    ("ces", "cs"), ("cze", "cs"), ("CS", "cs"), ("Czech", "cs"), ("cs-CZ", "cs"),
    ("eng", "en"), ("en-US", "en"), ("ger", "de"), ("slo", "sk"),
    ("", "und"), (None, "und"), ("und", "und"), ("zxx", "und"),
    ("klingon", "klingon"),
]:
    check(languages.normalize(raw) == want, f"normalize({raw!r}) -> {want!r}")

check(languages.display("ces") == "Čeština", "ces se zobrazi jako Cestina")
check(languages.display(None) == "Neuvedeno", "chybejici jazyk se zobrazi jako Neuvedeno")
check(languages.pack(["eng", "ces", "ces", None]) == "cs,en,und",
      f"pack setridi, odstrani duplicity a 'und' da na konec: {languages.pack(['eng', 'ces', 'ces', None])!r}")
check(languages.unpack("cs,en") == ["cs", "en"], "unpack")
check(languages.unpack(None) == [], "unpack prazdne hodnoty")
check(languages.combination_label("cs,en") == "CS + EN", "popisek kombinace")
check(languages.combination_label("und") == "Neuvedeno", "popisek bez jazyka")
check(languages.combination_label("cs,de,en,pl") == "CS + DE + EN + PL",
      "ctyri jazyky se vypisou cele")
check(languages.combination_label("cs,de,en,pl,ru,sk") == "CS + DE + EN + PL + ostatní",
      f"nad ctyri jazyky se zbytek shrne: {languages.combination_label('cs,de,en,pl,ru,sk')!r}")

print("--- prevod cest ---")
maps = [{"from": "/media", "to": r"D:\media"}]
for got, want in [
    (probe.apply_path_mappings("/media/filmy/Duna.mkv", maps), r"D:\media\filmy\Duna.mkv"),
    (probe.apply_path_mappings("/jine/x.mkv", maps), "/jine/x.mkv"),
    (probe.apply_path_mappings("", maps), ""),
    (probe.apply_path_mappings(r"D:\media\a.mkv", []), r"D:\media\a.mkv"),
]:
    check(got == want, f"{got!r} == {want!r}")

print("--- grafy s meznimi vstupy ---")
try:
    charts.area_chart_multi([], "day", [{"key": "hours", "label": "hodiny"}])
    charts.area_chart_multi([{"day": "2026-08-12", "hours": 0}], "day",
                            [{"key": "hours", "label": "hodiny"}])
    charts.hbar_chart([], "label", "value")
    charts.hbar_chart([{"label": "x" * 80, "value": 0}], "label", "value")
    charts.stacked_bar([])
    charts.stacked_bar([{"label": "a", "value": 0}])
    charts.heatmap([[0.0] * 24 for _ in range(7)])
    charts.sparkline([])
    charts.sparkline([{"day": "2026-08-12", "hours": 0}])
    charts.sparkline([{"day": "2026-08-12", "hours": 2}, {"day": "2026-08-13", "hours": 5}])
    charts.legend([])
    for maximum in (0, 0.4, 1, 7, 99, 1234, 1e6):
        charts._nice_ticks(maximum)
    check(True, "prazdna a mezni data grafy nerozbila")
except Exception as exc:  # noqa: BLE001
    check(False, f"grafy: {type(exc).__name__}: {exc}")

# Barva musi patrit veci, ne poradi: segment s vlastnim slotem si ho podrzi,
# i kdyz je v seznamu az druhy.
svg = charts.stacked_bar([
    {"label": "en", "value": 30, "slot": 2},
    {"label": "cs", "value": 70, "slot": 1},
])
check("var(--series-2)" in svg and "var(--series-1)" in svg,
      "stacked_bar respektuje vlastni barevny slot")

svg = charts.hbar_chart([{"label": "<script>x</script>", "value": 5}], "label", "value")
check("<script>" not in svg and "&lt;script&gt;" in svg, "nebezpecny nazev je escapovany")

# Minigraf musi nest hodnoty stejne jako velky graf - co vypada jako graf,
# to clovek zkusi pouzit jako graf.
spark = charts.sparkline(
    [{"day": "2026-08-11", "hours": 2}, {"day": "2026-08-12", "hours": 5}]
)
check('data-tip="2026-08-12: 5 h"' in spark, f"minigraf ma hodnoty k najeti mysi")
check('aria-hidden' not in spark, "minigraf uz neni oznaceny jako ozdoba")

# Zadny trvaly bod. Vsechny se ukazuji az po najeti mysi - koncovy bod
# na miste vypadal jako bod, ktery se "zasekl". Velky graf ho taky nema
# a dva tvary tehoz grafu se maji chovat stejne.
mimo_hover = spark.split('<g class="chart-hit"')[0]
check("<circle" not in mimo_hover,
      "minigraf nema trvaly bod na konci")
check(spark.count("<circle") == 2, f"body jsou jen v hover skupinach ({spark.count('<circle')})")

velky = charts.area_chart_multi(
    [{"day": "2026-08-11", "hours": 2}, {"day": "2026-08-12", "hours": 5}],
    "day", [{"key": "hours", "label": "Hodiny", "slot": 1}],
)
check("<circle" not in velky.split('<g class="chart-hit"')[0],
      "a velky graf se chova stejne")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
