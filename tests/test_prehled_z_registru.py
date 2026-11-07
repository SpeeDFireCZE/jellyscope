r"""Přehled (/) se kreslí z registru karet - stejnou cestou jako vlastní přehled.

Dřív měl Přehled karty vložené v šabloně ručně a práva u nich hlídal
ručně `vidi()`; dvě karty tak jednou proklouzly bez hlídání. A protože
cesty byly dvě, rozešly se: živé chování karet (obnova Právě se hraje,
přepínač Filmy/Seriály) měl jen Přehled. Hlídá se tu:

* na `/` jsou přesně karty `sekce.PREHLED`, v tomto pořadí, bez těch,
  které účet nesmí (pro čtenáře i diváka, každá oblast zvlášť);
* vlastní přehled kreslí tytéž karty se stejnými živými obaly;
* zapamatovaná volba Filmy/Seriály (i „Ostatní") platí i ve vlastním přehledu;
* karta „bez práva" v Mém přehledu živý obal nemá.

Spec: docs/superpowers/specs/2026-11-06-prehled-z-registru-design.md

Spuštění:
    .venv/bin/python tests/test_prehled_z_registru.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prehled_registr.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, pristup, sekce  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


TED = datetime.now(UTC).replace(tzinfo=None)


def cas(posun: timedelta) -> str:
    return (TED + posun).strftime(db.TIME_FORMAT)


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo")
accounts.create("divak", "dlouheheslo")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-petr' WHERE username = 'divak'")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-petr', 'Petr', 0, 0), ('u-jana', 'Jana', 0, 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, production_year, path, is_missing, date_created)"
                 " VALUES ('film-1', 'Film', 'Movie', 'filmy', 2024, '/media/filmy/Film.mkv', 0, ?)",
                 (cas(-timedelta(days=1)),))
    for klic, uid, jmeno, druh in (("h1", "u-petr", "Petr", "Movie"), ("h2", "u-jana", "Jana", "Movie"),
                                   ("h3", "u-jana", "Jana", "TvChannel")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " client, device_name, play_method, started_at, last_seen_at, ended_at, watched_seconds,"
            " paused_seconds, is_active) VALUES (?, ?, ?, 'film-1', 'Film', ?, 'filmy', 'Jellyfin Web',"
            " 'Obyvak', 'DirectPlay', ?, ?, ?, 3600, 0, 0)",
            (klic, uid, jmeno, druh, cas(-timedelta(hours=5)), cas(-timedelta(hours=4)), cas(-timedelta(hours=4))))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
        " client, device_name, play_method, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " position_ticks, is_active, media_runtime_ticks) VALUES ('zive', 'u-jana', 'Jana', 'film-1', 'Film',"
        " 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak', 'DirectPlay', ?, ?, 600, 0, 6000000000, 1, 72000000000)",
        (cas(-timedelta(minutes=10)), cas(-timedelta(seconds=5))))
    conn.commit()
db.set_setting(sekce.ZAPNUTO, "1")
db.set_setting(pristup.LOGIN_KLIC, "1")


def prihlas(jmeno: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": jmeno, "password": "dlouheheslo", "zpusob": "mistni"})
    return k


def karty_na(html: str) -> list[str]:
    return re.findall(r"<!-- karta: (\w+) -->", html)


OBALY = re.compile(r'<div[^>]*id="(now-playing|recently-added|daily-card|top-items-card)"[^>]*>')


def obaly(html: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in OBALY.finditer(html)}


def aktivni(html: str, kind: str) -> bool:
    return bool(re.search(rf'<a class="chip active"[^>]*?data-kind="{kind}"', html, re.S))


def vyrez(html: str, obal_id: str) -> str:
    """Kus stránky od obalu karty po další obal (stačí na její přepínač)."""
    i = html.find(f'id="{obal_id}"')
    return html[i:i + 6000] if i != -1 else ""


print("--- (a) registr ---")
prehled = getattr(sekce, "PREHLED", None)
check(bool(prehled) and all(k in sekce.PODLE_KLICE for k in prehled), f"sekce.PREHLED má platné klíče ({prehled})")
over = getattr(sekce, "over_kartu", None)
try:
    over("neexistuje") if over else None
    check(False, "over_kartu('neexistuje') vyhodí KeyError")
except KeyError:
    check(True, "over_kartu('neexistuje') vyhodí KeyError")
# Karta z registru, která na Přehledu není (mapa): v dashboard.html by ji
# routa nespočítala a tiše by chyběla všem - proto taky chyba, ne ticho.
try:
    over("mapa") if over else None
    check(False, "over_kartu('mapa') vyhodí KeyError (v registru je, na Přehledu ne)")
except KeyError:
    check(True, "over_kartu('mapa') vyhodí KeyError (v registru je, na Přehledu ne)")
check(over is not None and over("souhrn") == "", "over_kartu('souhrn') projde")
PREHLED = list(prehled or [])

print()
print("--- (b) Přehled správce ---")
spravce = prihlas("spravce")
html_prehledu = spravce.get("/").text
check(karty_na(html_prehledu) == PREHLED and len(PREHLED) == 10,
      f"na / jsou karty PREHLED v pořadí ({karty_na(html_prehledu)})")

print()
print("--- (c) čtenář a divák, každá oblast vypnutá zvlášť ---")
VSECHNY = [o.klic for o in pristup.OBLASTI]
for jmeno in ("ctenar", "divak"):
    for vypnuta in [None] + VSECHNY:
        zapnute = [o for o in VSECHNY if o != vypnuta]
        with db.connect() as conn:
            conn.execute("UPDATE accounts SET pristup = ? WHERE username = ?",
                         (pristup.zabal_prava(zapnute, False), jmeno))
            conn.commit()
        ucet = accounts.get_by_name(jmeno)
        r = prihlas(jmeno).get("/", follow_redirects=False)
        if r.status_code != 200:
            continue
        cekam = [k for k in PREHLED if sekce.smi(sekce.PODLE_KLICE[k], ucet)]
        popis = f"{jmeno} bez {vypnuta}" if vypnuta else f"{jmeno} se vším"
        check(karty_na(r.text) == cekam and len(cekam) > 0, f"{popis}: karty na / podle práv ({karty_na(r.text)})")
        if not pristup.vidi_ucet(ucet, "prave_hraje"):
            check('id="now-playing"' not in r.text, f"{popis}: bez Právě se hraje ani její obal")
        if not pristup.vidi_ucet(ucet, "knihovna"):
            check('id="recently-added"' not in r.text, f"{popis}: bez Knihovny ani obal Nedávno přidaných")
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET pristup = NULL WHERE username = ?", (jmeno,))
        conn.commit()

print()
print("--- (d) vlastní přehled se všemi kartami Přehledu ---")
id_spravce = accounts.get_by_name("spravce")["id"]
sekce.uloz_rozvrzeni(PREHLED, id_spravce)
r = spravce.get("/account/prehled")
check(r.status_code == 200, f"Můj přehled se všemi deseti kartami se vykreslí ({r.status_code})")
check(karty_na(r.text) == PREHLED and len(PREHLED) == 10, f"a má všech deset karet ({karty_na(r.text)})")
na_prehledu, ve_vlastnim = obaly(html_prehledu), obaly(r.text)
check(len(na_prehledu) == 4 and ve_vlastnim == na_prehledu,
      f"živé obaly jsou stejné jako na / ({sorted(ve_vlastnim)} vs {sorted(na_prehledu)})")

print()
print("--- (e) zapamatované volby platí i ve vlastním přehledu ---")
spravce.get("/partials/top-items?kind=movies")
r = spravce.get("/account/prehled")
check(aktivni(vyrez(r.text, "top-items-card"), "movies"), "Nejsledovanější ukážou zvolené Filmy")
spravce.get("/partials/daily?kind=other")
r = spravce.get("/account/prehled")
check(aktivni(vyrez(r.text, "daily-card"), "other"), "Sledovanost po dnech ukáže zvolené Ostatní")
check(aktivni(vyrez(spravce.get("/").text, "daily-card"), "other"), "a stejně na /")

print()
print("--- (f) karta bez práva v Mém přehledu nemá živý obal ---")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET pristup = ? WHERE username = 'divak'",
                 (pristup.zabal_prava([o for o in VSECHNY if o != "prave_hraje"], False),))
    conn.commit()
sekce.uloz_rozvrzeni([("prave_se_hraje", ""), ("nejsledovanejsi", "")], accounts.get_by_name("divak")["id"])
r = prihlas("divak").get("/account/prehled")
check(r.status_code == 200 and "dash-bez-prava" in r.text, f"karta Právě se hraje je „bez práva\" ({r.status_code})")
check('data-partial="/partials/now-playing"' not in r.text and 'id="now-playing"' not in r.text,
      "a nemá obal, který by se ptal na zakázaný výřez")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
