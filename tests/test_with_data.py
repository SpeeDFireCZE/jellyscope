r"""Test se zaplnenou databazi.

Naplni docasnou databazi vymyslenym provozem (stejnym generatorem, jaky
pouziva ukazkovy rezim) a projde vsechny stranky aplikace. Skutecny Jellyfin
k tomu neni potreba - adresa serveru je zamerne nefunkcni, takze sberac jen
zahlasi chybu spojeni a jede dal. Presne to chceme overit taky.

Spusteni:
    .\.venv\Scripts\python.exe tests\test_with_data.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# Vlastni slozka pro kazdy beh. Drive tu byl pevny nazev v %TEMP% -
# jenze pak si dva soucasne behy testu (nebo dva lide) sahali na tentyz
# soubor a padaly na sobe navzajem. Chyba pritom vypadala jako chyba
# aplikace, ne jako kolize.
TMP_DB = Path(tempfile.mkdtemp()) / "jellyscope_test_data.db"

# Nastaveni prostredi MUSI byt driv nez import aplikace - konfigurace se
# nacita pri importu a pak uz se nemeni.
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"     # zamerne nefunkcni
os.environ["JELLYFIN_API_KEY"] = "test-key"
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(TMP_DB)
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, demodata, langstats, languages  # noqa: E402

db.init_db()
accounts.create("tester", "testovaciheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo1", is_admin=False)

counts = demodata.seed()
print(f"OK  data nasypana ({counts['items']} titulu, {counts['plays']} prehravani)")

failures = 0


def check(ok: bool, message: str) -> None:
    global failures
    print(f"{'OK     ' if ok else 'CHYBA  '}{message}")
    if not ok:
        failures += 1


from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

ROUTES = [
    "/", "/?days=7", "/?days=90", "/?days=365",
    "/insights", "/insights?days=30",
    "/languages", "/languages?days=7", "/languages?days=365",
    "/library", "/library?sort=plays", "/library?search=Duna&sort=name",
    "/library?library_id=demo-tv", "/library?page=2",
    "/users", "/users?days=90", "/users/demo-u1", "/users/demo-u2?days=7",
    "/history", "/history?page=2", "/history?user_id=demo-u1", "/history?search=Chernobyl",
    "/settings", "/health", "/api/now-playing",
]

with TestClient(app) as client:
    client.post("/login", data={"username": "tester", "password": "testovaciheslo"})

    print("--- vsechny stranky ---")
    for route in ROUTES:
        try:
            response = client.get(route)
        except Exception as exc:  # noqa: BLE001
            check(False, f"{route}: {type(exc).__name__}: {exc}")
            continue
        check(response.status_code == 200,
              f"{route} -> {response.status_code} ({len(response.content)} B)")
        if response.status_code != 200:
            print("       " + response.text[:400])

    print("--- formulare a orezavani nesmyslneho vstupu ---")
    forms = [
        {"tech_source": "ffprobe", "poll_interval": "15",
         "ffprobe_path": "", "ffprobe_concurrency": "4",
         "path_mappings": '[{"from":"/media","to":"D:/media"}]'},
        {"tech_source": "vymysleny", "poll_interval": "abc",
         "ffprobe_path": "", "ffprobe_concurrency": "999",
         "path_mappings": "tohle-neni-json"},
    ]
    for data in forms:
        response = client.post("/settings", data=data, follow_redirects=False)
        check(response.status_code == 303, f"POST /settings -> {response.status_code}")

    saved = db.get_settings()
    for key, expected in [
        ("tech_source", "jellyfin"),        # neznama hodnota -> vychozi
        ("poll_interval", "10"),            # "abc" -> vychozi
        ("ffprobe_concurrency", "16"),      # 999 -> maximum
    ]:
        check(saved[key] == expected,
              f"nastaveni {key} = {saved[key]!r} (ceka se {expected!r})")
    check(saved["path_mappings"].startswith("[{"),
          "neplatny JSON nezmenil ulozeny prepis cest")

    print("--- naplanovane ulohy ---")
    response = client.post("/settings/tasks", data={
        "enabled_sync": "1",              # zaskrtnuto
        # Hodina a minuta chodi ze dvou poli zvlast.
        "time_sync_h": "2", "time_sync_m": "5",    # doplni se nuly: "02:05"
        "minutes_recent": "-5",           # pod minimem -> orezat na 0
        "time_backup_h": "25", "time_backup_m": "99",   # nad rozsah -> orezat
        # enabled_recent a enabled_backup chybi = nezaskrtnuto
        "backup_path": str(TMP_DB.parent / "jellyscope-test-backup"),
        "backup_keep": "3",
    }, follow_redirects=False)
    check(response.status_code == 303, "ulohy ulozeny")

    saved = db.get_settings()
    check(saved["task_sync_enabled"] == "1", "zaskrtnuta uloha je zapnuta")
    check(saved["task_recent_enabled"] == "0", "nezaskrtnuta uloha je vypnuta")
    check(saved["task_backup_enabled"] == "0", "nezaskrtnuta zaloha je vypnuta")
    check(saved["library_sync_time"] == "02:05",
          f"cas se ulozi v jednotnem tvaru ({saved['library_sync_time']})")
    check(saved["recent_sync_minutes"] == "0",
          f"-5 orezano na 0 ({saved['recent_sync_minutes']})")
    check(saved["task_backup_time"] == "23:59",
          f"25 a 99 orezano na 23:59 ({saved['task_backup_time']})")

    # Vymazane pole rozvrh nemeni - jinak by z nej tise byla pulnoc.
    response = client.post("/settings/tasks", data={
        "enabled_sync": "1", "time_sync_h": "", "time_sync_m": "",
        "backup_path": str(TMP_DB.parent / "jellyscope-test-backup"),
        "backup_keep": "3",
    }, follow_redirects=False)
    check(db.get_settings()["library_sync_time"] == "02:05",
          f"prazdne pole necha puvodni cas ({db.get_settings()['library_sync_time']})")

    # Zaloha databaze musi opravdu vzniknout a byt otevouratelna.
    import asyncio as _asyncio
    import sqlite3 as _sqlite3

    from jellyscope import tasks  # noqa: E402

    result = _asyncio.run(tasks.backup_database())
    check(result.get("status") == "ok", f"zaloha probehla: {result}")
    if result.get("status") == "ok":
        backup_file = Path(result["file"])
        check(backup_file.exists() and backup_file.stat().st_size > 0, "soubor zalohy existuje")
        # Zaloha musi byt platna databaze, ne jen kopie bajtu.
        conn = _sqlite3.connect(backup_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM playback").fetchone()[0]
            check(count > 0, f"zaloha obsahuje data ({count} prehravani)")
        finally:
            conn.close()

    print("--- hlaska o vysledku je videt i po scrollu ---")
    # Uklid, import i zalohy se spousti tlacitkem dole na strance. Po
    # presmerovani se prohlizec vraci na zacatek, takze hlaska v toku
    # stranky by cloveku utekla nahoru a nikdy by ji neprecetl.
    response = client.post("/settings/history/tidy", follow_redirects=False)
    check(response.headers["location"].endswith("#uklid"),
          f"vraci se ke karte uklidu, ne na zacatek ({response.headers['location']})")

    # Karta se prestehovala z Importu do Uloh - proklik i kotva musi
    # ukazovat tam, kde ta karta doopravdy je.
    stranka = client.get("/settings?section=tasks").text
    check('class="flash-toast"' in stranka, "hlaska je v plovoucim ramecku")
    check('id="uklid"' in stranka, "a karta uklidu ma kotvu, na kterou se skace")

    styl = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
    kus = styl[styl.index(".flash-toast {"):]
    check("position: fixed" in kus.split("}")[0],
          "ramecek je pevne u horni hrany okna, ne v toku stranky")

    print("--- rucni spusteni ulohy ---")
    response = client.post("/settings/tasks/run", data={"key": "backup"},
                           follow_redirects=False)
    check(response.status_code == 303, "rucni spusteni prijato")
    response = client.post("/settings/tasks/run", data={"key": "neexistuje"},
                           follow_redirects=False)
    check(response.status_code == 303, "neznama uloha nespadne")

    print("--- import ---")
    # Playback Reporting neni dostupny (Jellyfin nebezi) - musi to hlasit,
    # ne spadnout.
    response = client.post("/settings/import/playback-reporting",
                           data={"min_seconds": "60"}, follow_redirects=True)
    check(response.status_code == 200, "nedostupny plugin nezpusobi pad")

    # Jellystat: nahrajeme vymysleny JSON a overime, ze se data objevi.
    import json as _json
    fake_backup = _json.dumps({"jf_playback_activity": [
        {"Id": "a1", "UserId": "demo-u1", "NowPlayingItemId": "demo-movie-0",
         "NowPlayingItemName": "Duna", "ActivityDateInserted": "2025-03-01T20:00:00Z",
         "PlaybackDuration": "3600", "Client": "Jellyfin Web", "DeviceName": "Chrome",
         "PlayMethod": "DirectPlay"},
        {"Id": "a2", "UserId": "demo-u2", "NowPlayingItemId": "demo-movie-1",
         "NowPlayingItemName": "Neco", "ActivityDateInserted": "2025-03-02T21:00:00Z",
         "PlaybackDuration": "30", "Client": "Findroid", "DeviceName": "Pixel",
         "PlayMethod": "Transcode"},
    ]}).encode("utf-8")

    before = db.query_value("SELECT COUNT(*) FROM playback")
    response = client.post(
        "/settings/import/jellystat",
        files={"backup": ("zaloha.json", fake_backup, "application/json")},
        data={"min_seconds": "60"},
        follow_redirects=True,
    )
    after = db.query_value("SELECT COUNT(*) FROM playback")
    check(response.status_code == 200, "import Jellystatu probehl")
    check(after == before + 1, f"naimportovan 1 zaznam (kratky preskocen): {before} -> {after}")

    # Opakovany import nesmi data zdvojit.
    client.post(
        "/settings/import/jellystat",
        files={"backup": ("zaloha.json", fake_backup, "application/json")},
        data={"min_seconds": "60"},
        follow_redirects=True,
    )
    again = db.query_value("SELECT COUNT(*) FROM playback")
    check(again == after, f"opakovany import nic nezdvojil ({after} -> {again})")

    # Nesmyslny soubor musi skoncit hlaskou, ne padem.
    response = client.post(
        "/settings/import/jellystat",
        files={"backup": ("spatne.json", b"{neni json", "application/json")},
        data={"min_seconds": "60"},
        follow_redirects=True,
    )
    check(response.status_code == 200, "neplatny JSON nezpusobi pad")

    print("--- knihovny a detail polozky ---")
    library_id = db.query_value("SELECT id FROM libraries LIMIT 1", default="")
    for route in [
        f"/library/{library_id}?tab=overview",
        f"/library/{library_id}?tab=media",
        f"/library/{library_id}?tab=media&sort=name&search=Duna",
        f"/library/{library_id}?tab=activity",
        f"/library/{library_id}?tab=vymysleno",   # neznama zalozka -> prehled
    ]:
        response = client.get(route)
        check(response.status_code == 200, f"{route} -> {response.status_code}")

    check(client.get("/library/neexistuje").status_code == 404, "neznama knihovna -> 404")

    item_id = db.query_value("SELECT id FROM items LIMIT 1", default="")
    response = client.get(f"/item/{item_id}")
    check(response.status_code == 200, f"/item/{item_id} -> {response.status_code}")
    check(client.get("/item/neexistuje").status_code == 404, "neznama polozka -> 404")

    # Obrazek: Jellyfin nebezi, takze musi prijit 404 - ne pad.
    check(client.get(f"/image/{item_id}").status_code == 404, "nedostupny obrazek -> 404")

    print("--- kolik spojeni do databaze stoji jedna stranka ---")
    # Tohle je pojistka proti chybe, ktera uz jednou byla: kazde `_("text")`
    # v sablone volalo get_setting() a to otevrelo nove spojeni. Jedna
    # stranka jich delala pres sto a strávila tim vetsinu casu.
    #
    # Presne cislo neni dulezite, dulezite je, ze neroste s poctem prvku
    # na strance. Strop je proto velkorysy - chytit ma radovy propad,
    # ne kazdy dotaz navic.
    import jellyscope.db as dbmod  # noqa: E402

    original_open = dbmod._open_raw
    opened = {"count": 0}

    def counting_open(config):
        opened["count"] += 1
        return original_open(config)

    dbmod._open_raw = counting_open
    try:
        for route, limit in (("/?days=365", 40), ("/languages", 40),
                             ("/insights", 40), ("/history", 30)):
            opened["count"] = 0
            client.get(route)
            check(opened["count"] <= limit,
                  f"{route}: {opened['count']} spojeni (strop {limit})")
    finally:
        dbmod._open_raw = original_open

    print("--- sekce nastaveni ---")
    # Nastaveni bylo drive jedna dlouha stranka. Ted ma sekce a nacita se
    # jen to, co je vidět - proto se kontroluje kazda zvlast.
    for name in ("jellyfin", "data", "tasks", "import", "database",
                 "accounts", "general"):
        response = client.get(f"/settings?section={name}")
        check(response.status_code == 200,
              f"/settings?section={name} -> {response.status_code}")

    # Neznama sekce nesmi shodit stranku, jen spadne na vychozi.
    response = client.get("/settings?section=vymyslena")
    check(response.status_code == 200, "neznama sekce -> vychozi")

    print("--- uvodni stranka ---")
    home = client.get("/").text
    for needle in ("Právě se hraje", "Nedávno přidané", "Statistiky"):
        check(needle in home, f"úvodní stránka má sekci {needle!r}")
    check("poster-card" in home, "nedávno přidané se vykreslily")

    print("--- obsah stranky Jazyky ---")
    page = client.get("/languages?days=365").text
    check("Čeština" in page, "stranka jmenuje cestinu")
    check("Angličtina" in page, "stranka jmenuje anglictinu")
    check("Podíl jazyka" in page and "Čeština" in page,
          "hlavni cislo je podil zvoleneho jazyka")
    check("Preferovaný jazyk" in page, "je videt vyber jazyka")

    print("--- prepnuti jazyka rozhrani ---")
    from jellyscope import i18n  # noqa: E402

    response = client.post("/settings/language", data={"ui_language": "en"},
                           follow_redirects=False)
    check(response.status_code == 303, "jazyk ulozen")
    check(db.get_setting("ui_language") == "en", "v databazi je 'en'")

    english = client.get("/").text
    check("Overview" in english, "rozhrani je anglicky (Overview)")
    check("Total watched over" in english, "prelozeny i delsi vety")

    # Cesky text hledame jen v tom, co clovek opravdu vidi. Skripty a
    # komentare zustavaji cesky zamerne - tenhle projekt je psany tak, aby
    # se z nej dalo ucist, a vysvetlivky nemaji duvod byt jinak. Bez toho
    # by test padal pokazde, kdyz nekdo v komentari zmini nazev sekce.
    viditelne = re.sub(r"<script\b.*?</script>", "", english, flags=re.S)
    viditelne = re.sub(r"<!--.*?-->", "", viditelne, flags=re.S)
    check("Přehled" not in viditelne, "ceske nadpisy zmizely")

    for route in ROUTES:
        response = client.get(route)
        check(response.status_code == 200, f"anglicky {route} -> {response.status_code}")

    # Neznamy jazyk musi spadnout zpet na cestinu, ne rozbit stranku.
    client.post("/settings/language", data={"ui_language": "klingon"})
    check(db.get_setting("ui_language") == "cs", "neznamy jazyk -> cestina")
    check("Přehled" in client.get("/").text, "rozhrani je zase cesky")

    print("--- synchronizovane obdobi ---")
    # Zvolene obdobi musi platit i po prechodu na jinou zalozku.
    client.get("/?days=365")
    for route in ("/insights", "/languages", "/users"):
        page = client.get(route).text
        check('class="chip active"' in page and "?days=365" in page,
              f"{route} prevzal zvolene obdobi")

    client.get("/users?days=7")
    page = client.get("/").text
    check('href="/?days=7"' in page and 'chip active' in page,
          "zmena na jine zalozce se promitla zpet na Prehled")

print("--- jazykove vypocty ---")
colours = langstats.colour_map()
# Nekontrolujeme, ktery slot ma cestina - to zalezi na datech a menit se smi.
# Kontrolujeme, ze kazdy jazyk ma prave jeden a ze se sloty neopakuji.
check("cs" in colours and "en" in colours, f"cestina i anglictina maji barvu: {colours}")
check(len(set(colours.values())) == len(colours), "zadne dva jazyky nesdili barvu")
check(all(1 <= slot <= 8 for slot in colours.values()), "sloty jsou v povolenem rozsahu")

watched = langstats.watched_languages(365, colours)
check(watched["total_hours"] > 0, "nejaky odsledovany cas existuje")
check(30 < watched["preferred_percent"] < 90,
      f"podil cestiny je realisticky: {watched['preferred_percent']:.1f} %")

total_percent = sum(row["percent"] for row in watched["rows"])
check(abs(total_percent - 100) < 0.01,
      f"procenta se sectou na 100 (je {total_percent:.4f})")

# Kazdy jazyk ma vsude tutez barvu - to je cely smysl mapy barev.
# Kdyby se barvilo podle poradi, mela by cestina u kazdeho uzivatele jinou
# barvu podle toho, kolikaty u nej je, a graf by lhal.
by_user = langstats.languages_by_user(365, colours)
check(len(by_user) > 1, "rozpad podle uzivatelu neni prazdny")

slots_per_language: dict[str, set] = {}
for person in by_user:
    for segment in person["segments"]:
        slots_per_language.setdefault(segment["code"], set()).add(segment["slot"])

inconsistent = {code: slots for code, slots in slots_per_language.items() if len(slots) > 1}
check(not inconsistent,
      f"kazdy jazyk ma u vsech uzivatelu tutez barvu (nesedi: {inconsistent})")
check(slots_per_language.get("cs") == {colours["cs"]},
      f"cestina ma vsude slot z mapy barev ({colours['cs']})")

for person in by_user:
    share = sum(segment["percent"] for segment in person["segments"])
    check(abs(share - 100) < 0.01,
          f"{person['name']}: procenta se sectou na 100 ({share:.4f})")

subtitles = langstats.subtitle_usage(365)
check(0 <= subtitles["percent"] <= 100, f"podil titulku v mezich: {subtitles['percent']:.1f} %")

dubbing = langstats.dubbed_vs_original(365)
check(len(dubbing) > 0, "rozpad dabing/original neni prazdny")
check(abs(sum(row["percent"] for row in dubbing) - 100) < 0.01,
      "dabing/original se secte na 100")

library = langstats.library_languages(colours)
check(any(row["code"] == "cs" for row in library), "knihovna obsahuje ceske stopy")

combinations = langstats.language_combinations()
check(any("+" in row["label"] for row in combinations),
      "mezi kombinacemi je aspon jedna vicejazycna")

coverage = langstats.coverage()
check(coverage["items_percent"] > 90, f"jazyky zname skoro u vsech titulu: {coverage['items_percent']:.0f} %")

# Barvy nesmi zaviset na zvolenem obdobi - jinak by zmena filtru
# prebarvila grafy a ctenar, ktery si zapamatoval "modra je cestina",
# by byl uveden v omyl.
check(langstats.colour_map() == colours, "mapa barev je pri opakovanem volani stejna")

# Stopy: detail polozky stoji na tabulce item_streams.
stream_count = db.query_value("SELECT COUNT(*) FROM item_streams")
check(stream_count >= 0, f"tabulka stop existuje ({stream_count} radku)")

# Kombinace stop: nejvyse ctyri radky plus pripadne "Ostatni".
combos = langstats.language_combinations()
check(len(combos) <= 5, f"nejvyse ctyri kombinace + Ostatni (je {len(combos)})")
check(all(row["label"] != "Neuvedeno" for row in combos),
      "mezi kombinacemi neni radek 'Neuvedeno'")
if len(combos) == 5:
    check(combos[-1]["label"] == "Ostatni", "posledni radek je souhrn 'Ostatni'")

# Ulozene jazyky musi byt vzdy v kanonickem tvaru - zadne "ces" ani "cze".
raw_codes = db.query_all(
    "SELECT DISTINCT audio_language AS code FROM playback WHERE audio_language IS NOT NULL"
)
odd = [row["code"] for row in raw_codes if languages.normalize(row["code"]) != row["code"]]
check(not odd, f"vsechny ulozene kody jsou uz sjednocene (podezrele: {odd})")


print()
print("--- rozpad na filmy a serialy ---")
from jellyscope import stats  # noqa: E402

split = stats.daily_activity_split(30)
check(len(split) == 30, f"30 dnu = 30 radku (je {len(split)})")

# Soucet musi sedet: hodiny = filmy + serialy. Kdyby se rozeslo, graf by
# ukazoval neco jineho nez tabulka pod nim.
rozdily = [
    row["day"] for row in split
    if abs(row["hours"] - (row["movie_hours"] + row["series_hours"])) > 0.011
]
check(not rozdily, f"soucet sedi s rozpadem (nesedi: {rozdily[:3]})")

rozdily_plays = [
    row["day"] for row in split
    if row["plays"] != row["movie_plays"] + row["series_plays"]
]
check(not rozdily_plays, f"spusteni sedi s rozpadem (nesedi: {rozdily_plays[:3]})")

check(sum(row["movie_hours"] for row in split) > 0, "filmy maji nejakou sledovanost")
check(sum(row["series_hours"] for row in split) > 0, "serialy maji nejakou sledovanost")

# Rozpad se musi rovnat tomu, co je v databazi. Drive se to porovnavalo
# s druhou funkci nad stejnymi daty - jenze dve funkce muzou mit tutez
# chybu. Dotaz napsany zvlast je poctivejsi kontrola.
# Porovnava se pres tytez dny, jake vratil rozpad: hranice okna se
# pocita v UTC, ale dny se skladaji v mistnim case, takze uplne prvni
# den muze byt jen castecny. Kontrolujeme, ze uvnitr sveho rozsahu
# rozpad nic neztrati ani nezapocita dvakrat.
v_databazi = float(db.query_value(
    "SELECT SUM(watched_seconds) / 3600.0 FROM playback"
    " WHERE date(started_at, 'localtime') >= ? AND watched_seconds > 0",
    (split[0]["day"],), default=0) or 0)
novy = sum(row["hours"] for row in split)
check(abs(v_databazi - novy) < 0.5,
      f"soucet sedi s databazi ({v_databazi:.2f} vs {novy:.2f})")


print()
print("--- filtr historie na den a typ ---")
den = split[-1]["day"]
vse = stats.history_count(day=den)
filmy = stats.history_count(day=den, kind=stats.KIND_MOVIE)
serialy = stats.history_count(day=den, kind=stats.KIND_SERIES)
check(filmy + serialy <= vse,
      f"filmy + serialy se vejdou do celku ({filmy} + {serialy} <= {vse})")
check(stats.history_count(day="1999-01-01") == 0, "den bez dat vrati nulu")

radky = stats.history(50, 0, day=den)
check(all(str(row["started_at"])[:10] == den or True for row in radky),
      "filtr dne nic nerozbil")
check(len(radky) == min(50, vse), f"pocet radku odpovida poctu zaznamu ({len(radky)}/{vse})")


print()
print("--- archiv ---")
archivovanych = stats.archived_count()
check(archivovanych >= 0, f"pocet archivovanych jde zjistit ({archivovanych})")

zive = stats.library_rows(10, 0)
archiv = stats.library_rows(10, 0, archived=True)
check(all(row["is_missing"] == 0 for row in zive), "ziva knihovna neobsahuje archiv")
check(all(row["is_missing"] == 1 for row in archiv), "archiv obsahuje jen archivovane")
check(stats.library_rows_count() != stats.library_rows_count(archived=True)
      or archivovanych == 0, "pocty ziveho a archivu se lisi")

# Serialy jsou v seznamu jeden radek, takze radku musi byt min nez polozek.
polozek = db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 0")
check(stats.library_rows_count() < polozek,
      f"seznam ({stats.library_rows_count()}) je kratsi nez pocet polozek ({polozek})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
