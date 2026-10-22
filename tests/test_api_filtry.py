# -*- coding: utf-8 -*-
r"""Filtry, řazení a přísné parametry API přes skutečnou aplikaci.

`test_api_dotaz.py` hlídá samotné čtení dotazu; tady se ověřuje, že ho
adresy opravdu používají - nad databází, s klíči a jejich právy:

* nesmysl v dotazu vrátí 400, nikdy výchozí data;
* chyby API mají jedno tělo (`detail` + `error`), i 401/403/404;
* filtr běží nad celým seznamem, takže najde i to, co je v dnešním
  pořadí hluboko (5 nejméně hraných z cesty, od nejstaršího);
* filtr vidí jen to, co klíč - schované jméno jím najít nejde.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_api_filtry.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "filtry.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


db.init_db()
GB = 1024 ** 3
pred = lambda dnu: (datetime.now(timezone.utc) - timedelta(days=dnu)).strftime(db.TIME_FORMAT)  # noqa: E731

# 27 filmů v /media/jine/ přehraných 0-2x a 3 v /media/xy/ přehrané 5-6x:
# ty jsou v dnešním pořadí nejméně hraných až za 27 jinými, tedy mimo
# výchozí limit 25. Filtr je musí najít i tak.
XY = (("xa", 5, "2024-03-01 10:00:00"), ("xb", 5, "2024-01-01 10:00:00"),
      ("xc", 6, "2023-06-01 10:00:00"))
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES"
                 " ('lib1', 'Filmy', 'movies'), ('lib2', 'Seriály', 'tvshows')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    prehrani = []
    for i in range(27):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, width, height, video_codec,"
            " library_id, date_created, path, tmdb_id, tech_source, is_missing)"
            " VALUES (?, ?, 'Movie', ?, 1920, 1080, 'h264', 'lib1', ?, ?, ?, 'jellyfin', 0)",
            (f"i{i}", f"Film {i}", (i + 1) * GB, pred(400 + i),
             f"/media/jine/film{i}.mkv", str(1000 + i)))
        prehrani += [(f"i{i}", f"Film {i}")] * (i % 3)
    for jmeno, kolikrat, pridano in XY:
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, width, height, video_codec,"
            " library_id, date_created, path, tech_source, is_missing)"
            " VALUES (?, ?, 'Movie', ?, 3840, 2160, 'hevc', 'lib1', ?, ?, 'jellyfin', 0)",
            (jmeno, jmeno.upper(), 2 * GB, pridano, f"/media/xy/{jmeno}.mkv"))
        prehrani += [(jmeno, jmeno.upper())] * kolikrat
    conn.execute(
        "INSERT INTO items (id, name, type, series_id, series_name, parent_index_number,"
        " index_number, size_bytes, library_id, date_created, path, tmdb_id, tech_source,"
        " is_missing) VALUES ('e1', 'Pilot', 'Episode', 's1', 'Kancl', 1, 2, ?, 'lib2', ?,"
        " '/media/serialy/Kancl/S01E02.mkv', '2316', 'jellyfin', 0)", (GB, pred(30)))
    prehrani += [("e1", "Pilot")] * 2
    for k, (item, nazev) in enumerate(prehrani):
        kdo = "u1" if k % 2 else "u2"
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client, remote_address, bitrate)"
            " VALUES (?, ?, ?, ?, ?, 'Movie', ?, ?, 3600, 0, 0, 0, ?, ?,"
            " '203.0.113.77', 8000000)",
            (f"s{k}", kdo, "Jana" if kdo == "u1" else "Petr", item, nazev,
             pred(1 + k % 20), pred(1 + k % 20),
             "Transcode" if k % 5 == 0 else "DirectPlay",
             "Jellyfin Web" if k % 3 else "Infuse"))
    conn.commit()

klient = TestClient(app)
SERVER = api.vytvor("Grafana")
H = {"Authorization": f"Bearer {SERVER['token']}"}
CTENAR_ID = accounts.create("ctenar", "ctenarheslo")
CTENAR = api.vytvor("Homepage", CTENAR_ID)
HC = {"Authorization": f"Bearer {CTENAR['token']}"}


def get(adresa: str, hlavicky: dict | None = None):
    return klient.get(adresa, headers=H if hlavicky is None else hlavicky)


def kod(odpoved) -> str | None:
    try:
        return odpoved.json().get("error")
    except ValueError:
        return None


print("--- chyby mají jedno tělo ---")
odpoved = get("/api/v1/summary", {})
check(odpoved.status_code == 401 and kod(odpoved) == "missing_token"
      and odpoved.headers.get("www-authenticate") == "Bearer"
      and odpoved.json().get("detail"),
      f"bez tokenu: 401 missing_token s WWW-Authenticate ({odpoved.status_code}, {kod(odpoved)})")
odpoved = get("/api/v1/summary", {"Authorization": "Bearer js_nesmysl"})
check(odpoved.status_code == 401 and kod(odpoved) == "invalid_token"
      and odpoved.headers.get("www-authenticate") == "Bearer",
      f"neplatný token: 401 invalid_token ({kod(odpoved)})")

api.vypni(SERVER["id"])
odpoved = get("/api/v1/summary")
check(odpoved.status_code == 403 and kod(odpoved) == "key_disabled",
      f"vypnutý klíč: 403 key_disabled ({kod(odpoved)})")
api.zapni(SERVER["id"])

odpoved = get("/api/v1/summary", HC)
check(odpoved.status_code == 403 and kod(odpoved) == "key_not_allowed",
      f"čtenář bez povolení klíče: 403 key_not_allowed ({kod(odpoved)})")
db.set_setting("ctenar_smi_klic", "1")
check(get("/api/v1/summary", HC).status_code == 200, "s povolením projde")
db.set_setting("ctenar_vidi_sit", "0")
odpoved = get("/api/v1/bandwidth", HC)
check(odpoved.status_code == 403 and kod(odpoved) == "area_not_allowed",
      f"čtenář mimo oblast: 403 area_not_allowed ({kod(odpoved)})")

odpoved = get("/api/v1/item/neexistuje")
check(odpoved.status_code == 404 and kod(odpoved) == "not_found",
      f"neexistující titul: 404 not_found ({kod(odpoved)})")
odpoved = get("/api/v1/item/i;DROP")
check(odpoved.status_code == 404 and kod(odpoved) == "not_found",
      "nesmyslné id: taky 404 not_found")
check(all(set(get(a, h).json()) >= {"detail", "error"} for a, h in (
          ("/api/v1/summary", {}), ("/api/v1/bandwidth", HC), ("/api/v1/item/x", H))),
      "každé tělo chyby má detail i error")

print()
print("--- seznamy titulů ---")
from jellyscope import insights, stats  # noqa: E402

vychozi = get("/api/v1/least-played").json()["items"]
check(not any(p["path"].startswith("/media/xy") for p in vychozi),
      "soubory z /media/xy jsou v dnešním pořadí až za výchozím limitem")
odpoved = get("/api/v1/least-played?path[starts_with]=/media/xy&sort=plays,added_at&limit=5")
check(odpoved.status_code == 200
      and [p["id"] for p in odpoved.json().get("items", [])] == ["xb", "xa", "xc"],
      "5 nejméně hraných z /media/xy, při shodě od nejstaršího: všechny 3 "
      f"({odpoved.status_code}, {[p['id'] for p in odpoved.json().get('items', [])]})")
odpoved = get("/api/v1/least-played?order=oldest&sort=plays")
check(odpoved.status_code == 400 and kod(odpoved) == "conflicting_parameters",
      f"order a sort naráz: 400 conflicting_parameters ({kod(odpoved)})")
check(get("/api/v1/least-played?order=size").status_code == 200, "order=size dál projde")

TRI = "/media/jine/film0.mkv,/media/jine/film3.mkv,/media/jine/film6.mkv"
mrtve = insights.dead_storage(25, 365)
cele = get("/api/v1/unwatched").json()
check(cele.get("count") == mrtve["item_count"] and cele.get("size_bytes") == mrtve["size_bytes"]
      # float(): PostgreSQL vrací součet jako Decimal a Decimal('30.4') != 30.4
      and cele.get("share_percent") == round(float(mrtve["share"]), 1)
      and [p["id"] for p in cele.get("items", [])] == [r["id"] for r in mrtve["rows"]],
      "unwatched bez filtru: stejná čísla i položky jako dnes "
      f"({cele.get('count')}/{mrtve['item_count']}, {cele.get('size_bytes')}/{mrtve['size_bytes']!r}, "
      f"{cele.get('share_percent')}/{mrtve['share']!r})")
tri = get(f"/api/v1/unwatched?path[in]={TRI}").json()
check(tri.get("count") == 3 and tri.get("size_bytes") == 12 * GB
      and tri.get("share_percent") == round(12 * GB / cele["library_bytes"] * 100, 1)
      and tri.get("library_bytes") == cele["library_bytes"],
      f"unwatched s filtrem: součty jen za vybrané ({tri.get('count')}, {tri.get('size_bytes')})")
nic = get("/api/v1/unwatched?path[starts_with]=/nikde").json()
check(nic.get("items") == [] and nic.get("count") == 0 and nic.get("share_percent") == 0,
      "filtr, který nic nenajde: prázdný seznam a nuly")
check(api._souhrn_nevyuzitych([], 0)["share_percent"] == 0,
      "prázdná knihovna: podíl 0, žádné dělení nulou")

serialy = get("/api/v1/top-items?is_series[equals]=true").json().get("items", [])
check(serialy and all(p["is_series"] for p in serialy), "top-items: jen seriály")
check([p["title"] for p in get("/api/v1/top-items").json()["items"]]
      == [r["label"] for r in stats.top_items(30, 10, "both")],
      "top-items bez filtru jako dnes")
filmy = get("/api/v1/recently-added?library[equals]=Filmy&limit=3").json().get("items", [])
check(0 < len(filmy) <= 3 and all(p["library"] == "Filmy" for p in filmy),
      "recently-added: jen Filmy, nejvýš 3")
check([p["id"] for p in get("/api/v1/recently-added").json()["items"]]
      == [r["id"] for r in stats.recently_added(18)], "recently-added bez filtru jako dnes")
check([p["id"] for p in vychozi]
      == [r["id"] for r in insights.least_played(365, 25, 0, "size")],
      "least-played bez filtru jako dnes")

for adresa, cekany in (
        ("/api/v1/least-played?days=abc", "invalid_value"),
        ("/api/v1/least-played?days=0", "invalid_value"),
        ("/api/v1/least-played?days=3651", "invalid_value"),
        ("/api/v1/least-played?limit=0", "invalid_value"),
        ("/api/v1/least-played?limit=501", "invalid_value"),
        ("/api/v1/unwatched?limit=abc", "invalid_value"),
        ("/api/v1/top-items?limit=201", "invalid_value"),
        ("/api/v1/top-items?kind=DROP", "invalid_value"),
        ("/api/v1/least-played?min_size_mb=-5", "invalid_value"),
        ("/api/v1/least-played?order=bogus", "invalid_value"),
        ("/api/v1/least-played?days=7&days=30", "duplicate_parameter"),
        ("/api/v1/least-played?foo=1", "unknown_parameter"),
        ("/api/v1/recently-added?days=7", "unknown_parameter"),
        ("/api/v1/top-items?plays[contains]=1", "operator_not_allowed")):
    odpoved = get(adresa)
    check(odpoved.status_code == 400 and kod(odpoved) == cekany,
          f"{adresa} -> 400 {cekany} ({odpoved.status_code}, {kod(odpoved)})")
check(get("/api/v1/top-items?kind=MOVIES").status_code == 200, "kind velkými písmeny projde")

print()
print("--- lidé a provoz ---")
from jellyscope import pristup  # noqa: E402

historie = get("/api/v1/history?is_transcode[equals]=false&sort=-started_at&limit=500").json()
radky = historie.get("playbacks", [])
check(radky and all(not p["is_transcode"] for p in radky)
      and [p["started_at"] for p in radky] == sorted((p["started_at"] for p in radky), reverse=True),
      f"history: jen přímá přehrání, od nejnovějšího ({len(radky)})")
petr = get("/api/v1/history?user=u2&limit=500").json().get("playbacks", [])
check(petr and all(p["user_id"] == "u2" for p in petr), "history?user=u2 jen Petr")
for spatne in ("a;b", "a" * 65):
    odpoved = get(f"/api/v1/history?user={spatne}")
    check(odpoved.status_code == 400 and kod(odpoved) == "invalid_value",
          f"history?user={spatne[:8]!r} -> 400 invalid_value ({kod(odpoved)})")
check(get("/api/v1/history?user=").status_code == 200,
      "prázdné user= (tak ho ukazoval návod) = bez omezení na diváka")

# Divák z Jellyfinu (Petr, u2) se svým klíčem.
db.set_setting(pristup.LOGIN_KLIC, "1")
db.set_setting("divak_smi_klic", "1")
divak = accounts.z_jellyfinu({"id": "u2", "jmeno": "Petr", "spravce": False, "vypnuty": False})
HD = {"Authorization": f"Bearer {api.vytvor('Mobil', divak['id'])['token']}"}
odpoved = get("/api/v1/history?user=u1", HD)
check(odpoved.status_code == 403 and kod(odpoved) == "own_history_only",
      f"divák s cizím user: 403 own_history_only ({odpoved.status_code}, {kod(odpoved)})")
for adresa in ("/api/v1/history?user=u2", "/api/v1/history"):
    odpoved = get(adresa, HD)
    vlastni = odpoved.json().get("playbacks", []) if odpoved.status_code == 200 else []
    check(odpoved.status_code == 200 and vlastni and all(p["user_id"] == "u2" for p in vlastni),
          f"divák {adresa}: 200 a jen vlastní ({odpoved.status_code})")
odpoved = get("/api/v1/history?user=a;b", HD)
check(odpoved.status_code == 400, "nesmyslné user je 400 dřív, než se řeší, čí je")

# Čtenář se schovanými jmény: skutečné jméno filtrem nenajde, ani po písmenkách.
db.set_setting("ctenar_anonymizace", "1")
check(get("/api/v1/users?name[equals]=Petr").json().get("users"),
      "klíč serveru Petra najde (filtr funguje)")
for adresa, seznam in (("/api/v1/users?name[equals]=Petr", "users"),
                       ("/api/v1/history?user[contains]=Pe", "playbacks"),
                       ("/api/v1/history?user[starts_with]=P", "playbacks")):
    odpoved = get(adresa, HC)
    check(odpoved.status_code == 200 and odpoved.json().get(seznam) == [],
          f"čtenář s anonymizací: {adresa} nic nenajde")
db.set_setting("ctenar_anonymizace", "0")

divaci = get("/api/v1/users?plays[gte]=1&sort=-watched_hours").json().get("users", [])
check(divaci and all(u["plays"] >= 1 for u in divaci)
      and [u["watched_hours"] for u in divaci] == sorted((u["watched_hours"] for u in divaci),
                                                         reverse=True),
      "users: filtr i řazení")
check(len(get("/api/v1/users?limit=1").json().get("users", [])) == 1, "users?limit=1")
odpoved = get("/api/v1/users?limit=501")
check(odpoved.status_code == 400 and kod(odpoved) == "invalid_value", "users?limit=501 -> 400")

with db.connect() as conn:
    for k, pauza in enumerate((0, 1)):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client)"
            " VALUES (?, 'u1', 'Jana', 'i1', 'Film 1', 'Movie', ?, ?, 60, 0, ?, 1,"
            " 'DirectPlay', 'Jellyfin Web')", (f"zive{k}", pred(0), pred(0), pauza))
    conn.commit()
vse = get("/api/v1/now-playing").json()
bezi = get("/api/v1/now-playing?is_paused[equals]=false").json()
check(vse.get("count") == 2 and bezi.get("count") == 1
      and all(not s["is_paused"] for s in bezi.get("sessions", [])),
      f"now-playing: jen ty, co nejsou pozastavené ({vse.get('count')}, {bezi.get('count')})")
odpoved = get("/api/v1/now-playing?days=7")
check(odpoved.status_code == 400 and kod(odpoved) == "unknown_parameter",
      "now-playing nezná days -> unknown_parameter")

knihovny = get("/api/v1/libraries?type[equals]=movies").json()
check(knihovny.get("count") == 1 and knihovny["libraries"][0]["name"] == "Filmy",
      "libraries: jen filmové")

# Země bez databáze GeoIP nejsou - podstrčí se, ať je co filtrovat.
puvodni_zeme = stats.zeme_divaku
stats.zeme_divaku = lambda dny: [
    {"label": "Česko", "kod": "CZ", "plays": 3, "sekund": 100, "bajtu": 500},
    {"label": "Německo", "kod": "DE", "plays": 1, "sekund": 10, "bajtu": 50},
    {"label": "Slovensko", "kod": "SK", "plays": 2, "sekund": 60, "bajtu": 900}]
cely = get("/api/v1/bandwidth").json()
cz_sk = get("/api/v1/bandwidth?code[in]=CZ,SK&sort=-bytes").json()
stats.zeme_divaku = puvodni_zeme
check([z["code"] for z in cz_sk.get("countries", [])] == ["SK", "CZ"],
      f"bandwidth: jen CZ a SK, od největšího ({[z['code'] for z in cz_sk.get('countries', [])]})")
check(cz_sk.get("total_bytes") == cely.get("total_bytes") and len(cely.get("countries", [])) == 3,
      "souhrn provozu filtr nemění")

print()
print("--- víc seznamů: insights a play-methods ---")
cele = get("/api/v1/insights?days=30&limit=15").json()
check([r["title"] for r in cele.get("transcode_offenders", [])]
      == [r["label"] for r in insights.transcode_offenders(30, 15)]
      and [r["title"] for r in cele.get("upgrade_candidates", [])]
      == [r["label"] for r in insights.upgrade_candidates(30, 15)]
      and [r["title"] for r in cele.get("oversized_rarely_watched", [])]
      == [r["label"] for r in insights.oversized_rarely_watched(30, 15)]
      and [r["title"] for r in cele.get("never_finished", [])]
      == [r["label"] for r in insights.never_finished(30, 15)]
      and "transcode_speed" in cele,
      "insights bez filtru jako dnes (všechny čtyři seznamy i rychlost)")
metody = get("/api/v1/play-methods?days=30").json()
check([m["method"] for m in metody.get("methods", [])]
      == [m["method"] for m in stats.play_method_breakdown(30)]
      and [c["client"] for c in metody.get("clients", [])]
      == [c["label"] for c in stats.client_breakdown(30, 25)],
      "play-methods bez filtru jako dnes")

# Seznamy Zjištění jsou na malých datech prázdné - podstrčí se.
puvodni_nedokoncene = insights.never_finished
puvodni_velke = insights.oversized_rarely_watched
insights.never_finished = lambda dny, limit=15: [
    {"label": "A", "attempts": 3, "best_percent": 20, "runtime_minutes": 100},
    {"label": "B", "attempts": 5, "best_percent": 40, "runtime_minutes": 90},
    {"label": "C", "attempts": 9, "best_percent": 80, "runtime_minutes": 80}][:limit]
insights.oversized_rarely_watched = lambda dny, limit=15: [
    {"id": i, "label": i.upper(), "type": "Movie", "path": f"/media/{slozka}/{i}.mkv",
     "tmdb_id": None, "plays": 1, "hours": 1, "size_bytes": 50 * GB, "gb_per_hour": 50,
     "width": 3840, "height": 2160}
    for i, slozka in (("v1", "jine"), ("v2", "xy"), ("v3", "xy"))][:limit]
zuzene = get("/api/v1/insights?never_finished.best_percent[lt]=50"
             "&sort=-never_finished.attempts").json()
check([r["title"] for r in zuzene.get("never_finished", [])] == ["B", "A"],
      f"never_finished.best_percent[lt]=50 + sort ({[r['title'] for r in zuzene.get('never_finished', [])]})")
check(zuzene.get("transcode_offenders") == cele.get("transcode_offenders")
      and zuzene.get("upgrade_candidates") == cele.get("upgrade_candidates")
      and zuzene.get("transcode_speed") == cele.get("transcode_speed")
      and len(zuzene.get("oversized_rarely_watched", [])) == 3,
      "ostatní seznamy beze změny")
xy = get("/api/v1/insights?oversized_rarely_watched.path[starts_with]=/media/xy").json()
check([r["id"] for r in xy.get("oversized_rarely_watched", [])] == ["v2", "v3"],
      "oversized: jen soubory z /media/xy")
insights.never_finished = puvodni_nedokoncene
insights.oversized_rarely_watched = puvodni_velke

odpoved = get("/api/v1/insights?title[contains]=x")
check(odpoved.status_code == 400 and kod(odpoved) == "unknown_list"
      and odpoved.json().get("allowed") == ["transcode_offenders", "upgrade_candidates",
                                            "oversized_rarely_watched", "never_finished"],
      f"insights: pole bez seznamu -> 400 unknown_list s výčtem ({kod(odpoved)})")
odpoved = get("/api/v1/insights?foo.title[contains]=x")
check(odpoved.status_code == 400 and kod(odpoved) == "unknown_list", "neznámý seznam")
klienti = get("/api/v1/play-methods?clients.client[equals]=infuse&sort=-clients.plays").json()
check([c["client"] for c in klienti.get("clients", [])] == ["Infuse"]
      and klienti.get("methods") == metody.get("methods"),
      "play-methods: zúží jen clients")
for adresa in ("/api/v1/insights?limit=101", "/api/v1/play-methods?limit=501",
               "/api/v1/insights?days=-3", "/api/v1/play-methods?days=NaN"):
    odpoved = get(adresa)
    check(odpoved.status_code == 400 and kod(odpoved) == "invalid_value",
          f"{adresa} -> 400 invalid_value ({kod(odpoved)})")

print()
print("--- každá adresa ---")
from jellyscope import applog  # noqa: E402
from jellyscope.api_dotaz import OPERATORY  # noqa: E402

check(set(api.ZNAME_PARAMETRY) == set(api.OBLASTI_ADRES),
      f"každá adresa má popsané parametry ({sorted(set(api.ZNAME_PARAMETRY) ^ set(api.OBLASTI_ADRES))})")
check(set(api.SEZNAMY) <= set(api.OBLASTI_ADRES), "seznamy jen u existujících adres")


def ma_zavoru(zavislost) -> bool:
    return any(d.call is api.smi_adresu or ma_zavoru(d) for d in zavislost.dependencies)


cesty = sorted(r.path for r in api.router.routes if "GET" in getattr(r, "methods", ()))
for cesta in cesty:
    adresa = cesta.replace("{item_id}", "i1")
    routa = next(r for r in api.router.routes if r.path == cesta)
    bez = get(adresa + "?days=abc&nesmysl=1&plays[gt]=x", {})
    s_klicem = get(adresa + ("&" if "?" in adresa else "?") + "nesmysl=1")
    check(bez.status_code == 401 and s_klicem.status_code == 400
          and kod(s_klicem) == "unknown_parameter" and ma_zavoru(routa.dependant),
          f"{cesta}: bez klíče 401 i s nesmysly, s klíčem 400, má závoru "
          f"({bez.status_code}, {s_klicem.status_code}, {kod(s_klicem)})")

for adresa, cekany in (("/api/v1/summary?plays[gt]=1", "not_filterable"),
                       ("/api/v1/library?sort=x", "not_filterable"),
                       ("/api/v1/item/i1?path[contains]=x", "not_filterable"),
                       ("/api/v1/summary?limit=5", "unknown_parameter"),
                       ("/api/v1/?days=7", "unknown_parameter")):
    odpoved = get(adresa)
    check(odpoved.status_code == 400 and kod(odpoved) == cekany,
          f"{adresa} -> 400 {cekany} ({odpoved.status_code}, {kod(odpoved)})")
check(get("/api/v1/summary?days=7").status_code == 200
      and get("/api/v1/library?days=7").status_code == 200, "summary a library s days projdou")

print()
print("--- popis polí sedí s odpovědí ---")
# Prázdné seznamy dostanou jeden podstrčený řádek: tvar řádku dělá adresa
# (`r.get(...)`), takže stačí prázdný slovník.
podstrcene = {(insights, "never_finished"), (insights, "oversized_rarely_watched"),
              (insights, "upgrade_candidates"), (insights, "transcode_offenders"),
              (stats, "zeme_divaku")}
puvodni = {(m, n): getattr(m, n) for m, n in podstrcene}
for modul, jmeno in podstrcene:
    setattr(modul, jmeno, lambda *a, **k: [{}])
for cesta, seznamy in api.SEZNAMY.items():
    odpoved = get(cesta).json()
    for seznam, pole in seznamy.items():
        radky = odpoved.get(seznam) or []
        check(bool(radky) and all(set(r) == set(pole) for r in radky),
              f"{cesta} {seznam}: klíče řádku = popis "
              f"({sorted(set(radky[0]) ^ set(pole)) if radky else 'prázdný seznam'})")
for (modul, jmeno), funkce in puvodni.items():
    setattr(modul, jmeno, funkce)

print()
print("--- rozcestník ---")
rozcestnik = get("/api/v1/").json()
check(rozcestnik.get("filtering", {}).get("operators") == {k: list(v) for k, v in OPERATORY.items()},
      "rozcestník: operátory podle typu")
check(rozcestnik.get("lists", {}).get("/api/v1/least-played") == api.SEZNAMY["/api/v1/least-played"]
      and "/api/v1/insights" in rozcestnik.get("lists", {}),
      "rozcestník: pole seznamů u adres")
ctenar_lists = get("/api/v1/", HC).json().get("lists", {})
check("/api/v1/bandwidth" not in ctenar_lists and "/api/v1/least-played" in ctenar_lists,
      "čtenář bez Sítě nevidí pole /api/v1/bandwidth")
check(api.pole_adres(["/api/v1/least-played", "/api/v1/summary"])
      == {"/api/v1/least-played": api.SEZNAMY["/api/v1/least-played"]},
      "pole_adres: jen adresy se seznamem")

print()
print("--- bez filtru se nenačítá celý seznam ---")
# Celý seznam je drahý (na knihovně o 40 000 souborech desetiny sekundy
# až sekundy). Bez filtru a řazení stačí, co bylo dřív: limit rovnou
# v SQL. S filtrem nebo řazením se musí načíst všechno.
limity = []
puvodni_nejmene = insights.least_played


def spion(dny, limit=25, *args, **kwargs):
    limity.append(limit)
    return puvodni_nejmene(dny, limit, *args, **kwargs)


insights.least_played = spion
get("/api/v1/least-played?limit=7")
get("/api/v1/least-played?sort=plays&limit=7")
get("/api/v1/least-played?path[contains]=x&limit=7")
insights.least_played = puvodni_nejmene
check(limity == [7, stats.BEZ_LIMITU, stats.BEZ_LIMITU],
      f"bez filtru limit v SQL, se sort nebo filtrem celý seznam ({limity})")

get("/api/v1/least-played?foo=1")
posledni = applog.read_api(5)["radky"][0]
check(posledni["vysledek"] == 400 and posledni["cesta"] == "/api/v1/least-played?foo=1",
      f"odmítnutý dotaz je v logu API se stavem 400 ({posledni['vysledek']})")

print()
print("--- historie s filtrem jen tenké řádky ---")
# stats.history() vrací p.* se vším a ke každému řádku rozbor přepočtu:
# celá historie tak na 150 000 přehráních zabrala přes 400 MB. S filtrem
# se proto čte tenká historie po dávkách (stats.historie_pro_api).
import json  # noqa: E402

# Chyba serveru jako 500, ne výjimka - ať test dojde až do konce.
odolny = TestClient(app, raise_server_exceptions=False)


class _Odpoved:
    """Odpověď, kterou jde vždycky přečíst jako JSON (500 je {})."""

    def __init__(self, odpoved):
        self.status_code = odpoved.status_code
        try:
            self._json = odpoved.json()
        except ValueError:
            self._json = {}

    def json(self):
        return self._json


def get(adresa: str, hlavicky: dict | None = None):  # noqa: F811
    return _Odpoved(odolny.get(adresa, headers=H if hlavicky is None else hlavicky))


puvodni_historie = stats.history


def jen_s_limitem(*args, **kwargs):
    if kwargs.get("limit") == stats.BEZ_LIMITU:
        raise AssertionError("celá tlustá historie")
    return puvodni_historie(*args, **kwargs)


stats.history = jen_s_limitem
odpoved = get("/api/v1/history?is_transcode[equals]=false&sort=-started_at&limit=500")
vse_filtrem = get("/api/v1/history?watched_seconds[gte]=0&limit=500").json().get("playbacks", [])
stats.history = puvodni_historie
check(odpoved.status_code == 200, f"filtr nečte celou tlustou historii ({odpoved.status_code})")
bez_filtru = get("/api/v1/history?limit=500").json().get("playbacks", [])
check(len(vse_filtrem) == len(bez_filtru) > 0
      and sorted(json.dumps(p, sort_keys=True) for p in vse_filtrem)
      == sorted(json.dumps(p, sort_keys=True) for p in bez_filtru),
      "tenká cesta vrací stejné řádky jako dřív (filtr, který pustí všechno)")

print()
print("--- rozbitá data a velká knihovna ---")
# Jellyfin umí poslat DateCreated 0001-01-01 (MinValue). S filtrem se
# nově projde celá knihovna, a posun o den zpět u roku 0001 přetekl.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, library_id, date_created,"
                 " tech_source, is_missing) VALUES ('nula', 'Nula', 'Movie', 'lib1',"
                 " '0001-01-01T00:00:00.0000000Z', 'jellyfin', 0)")
    conn.commit()
odpoved = get("/api/v1/recently-added?name[contains]=Film&limit=5")
check(odpoved.status_code == 200, f"datum přidání z roku 0001 nic neshodí ({odpoved.status_code})")

# Víc skupin, než SQLite snese parametrů (32 766): dřív se klíče všech
# skupin dosadily do IN (?, ?, ...) a dotaz spadl.
with db.connect() as conn:
    conn.executemany(
        "INSERT INTO items (id, name, type, library_id, date_created, tech_source,"
        " is_missing) VALUES (?, ?, 'Movie', 'lib1', ?, 'jellyfin', 0)",
        [(f"h{i}", f"Hromadny {i}", pred(500 + i % 300)) for i in range(33000)])
    conn.commit()
odpoved = get("/api/v1/recently-added?name[contains]=Hromadny%201&limit=5")
check(odpoved.status_code == 200 and len(odpoved.json().get("items", [])) == 5,
      f"33 000 skupin: recently-added s filtrem projde ({odpoved.status_code})")
check(get("/api/v1/recently-added?sort=name&limit=5").status_code == 200,
      "i se sort")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
