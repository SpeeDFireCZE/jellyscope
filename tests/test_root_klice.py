# -*- coding: utf-8 -*-
r"""Root klíče: API klíč bez vlastníka, s vlastními právy.

Uživatelský klíč patří účtu a řídí se jeho právy. Root klíč vyrobí
správce v Nastavení → API: nepatří nikomu a smí jen to, co mu správce
nastavil - oblasti, historii přehrávání, schovaná jména. Hlídá se tu:

* práva se vynucují u každého požadavku a změna platí hned;
* historie jen se zaškrtnutím (u účtů je vidět vždy);
* rozbitá práva v databázi nikdy neznamenají plná práva;
* klíč nezávisí na správci, který ho vyrobil.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_root_klice.py
"""
from __future__ import annotations

import logging
import os
import re
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "root.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import api, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


db.init_db()
pred = lambda dnu: (datetime.now(timezone.utc) - timedelta(days=dnu)).strftime(db.TIME_FORMAT)  # noqa: E731
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    conn.execute("INSERT INTO items (id, name, type, size_bytes, library_id, date_created,"
                 " path, tech_source, is_missing) VALUES ('i1', 'Duna', 'Movie', 1000,"
                 " 'lib1', ?, '/media/filmy/duna.mkv', 'jellyfin', 0)", (pred(400),))
    for k, (kdo, jmeno) in enumerate((("u1", "Jana"), ("u2", "Petr"))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, 3600, 0, 0, 0, 'DirectPlay', 'Web')",
            (f"s{k}", kdo, jmeno, pred(1 + k), pred(1 + k)))
    conn.commit()

klient = TestClient(app)


def get(token: str, adresa: str):
    return klient.get(adresa, headers={"Authorization": f"Bearer {token}"})


def kod(odpoved) -> str | None:
    try:
        return odpoved.json().get("error")
    except ValueError:
        return None


print("--- kontrola ---")
widget = api.vytvor("Widget", None, api.zabal_prava_klice(["knihovna"], False, False))
W = widget["token"]
check(get(W, "/api/v1/libraries").status_code == 200, "root s Knihovnou: /libraries 200")
odpoved = get(W, "/api/v1/users")
check(odpoved.status_code == 403 and kod(odpoved) == "area_not_allowed",
      f"mimo oblasti: /users 403 area_not_allowed ({odpoved.status_code})")
odpoved = get(W, "/api/v1/history")
check(odpoved.status_code == 403 and kod(odpoved) == "area_not_allowed",
      f"historie bez zaškrtnutí: 403 ({odpoved.status_code})")
endpoints = get(W, "/api/v1/").json().get("endpoints", {})
check("/api/v1/libraries" in endpoints and "/api/v1/users" not in endpoints
      and "/api/v1/history" not in endpoints,
      f"rozcestník jen povolené adresy ({sorted(endpoints)})")

vysledek = api.uloz_prava_klice(widget["id"], api.zabal_prava_klice(["knihovna"], False, True))
check(vysledek is not None and vysledek["jmeno"] == "Widget", "uloz_prava_klice vrací jméno")
historie = get(W, "/api/v1/history")
jmena = {p["user"] for p in historie.json().get("playbacks", [])} if historie.status_code == 200 else set()
check(historie.status_code == 200 and jmena == {"Jana", "Petr"},
      f"s historií: 200 a za všechny diváky ({historie.status_code}, {jmena})")

api.uloz_prava_klice(widget["id"], api.zabal_prava_klice(["knihovna"], True, True))
jmena = {p["user"] for p in get(W, "/api/v1/history").json().get("playbacks", [])}
check(jmena and not jmena & {"Jana", "Petr"}, f"se schovanými jmény nejsou skutečná ({jmena})")
check(get(W, "/api/v1/history?user[contains]=Pe").json().get("playbacks") == [],
      "a filtr skutečné jméno nenajde")

api.uloz_prava_klice(widget["id"], api.zabal_prava_klice(["uzivatele"], False, False))
check(get(W, "/api/v1/users").status_code == 200 and get(W, "/api/v1/libraries").status_code == 403,
      "změna práv platí od dalšího požadavku")

jen_pod = api.vytvor("Pod", None, api.zabal_prava_klice(["knihovna_kdo"], False, False))
check(get(jen_pod["token"], "/api/v1/library").status_code == 403,
      "podoblast bez nadřazené: /library 403 (pravidlo nadřazené oblasti)")

serveru = api.vytvor("Grafana")
cesty = sorted(r.path for r in api.router.routes if "GET" in getattr(r, "methods", ()))
spatne = [c for c in cesty if "{item_id}" not in c and get(serveru["token"], c).status_code != 200]
check(not spatne, f"klíč bez vlastníka a bez práv = všechno ({spatne})")

with db.connect() as conn:
    conn.execute("UPDATE api_tokens SET prava = 'nesmysl{' WHERE id = ?", (jen_pod["id"],))
    conn.commit()
check(get(jen_pod["token"], "/api/v1/libraries").status_code == 403
      and get(jen_pod["token"], "/api/v1/history").status_code == 403,
      "rozbitý JSON v právech = nic, ne všechno")
check(api.prava_klice("nesmysl{") == {"oblasti": set(), "anonymizace": True, "historie": False},
      "prava_klice: rozbitý JSON -> žádná práva, jména schovaná")
check(api.prava_klice(None) is None, "prava_klice: NULL = všechno")
# Rucne upraveny JSON, ktery se precte, ale ma spatne typy: bool("false")
# je True - historie a skutecna jmena by prosla. Jen skutecne true otevira.
check(api.prava_klice('{"oblasti": ["knihovna"], "historie": "false", "anonymizace": "false"}')
      == {"oblasti": {"knihovna"}, "anonymizace": True, "historie": False},
      "prava_klice: \"false\" jako text historii neotevře a jména neodkryje")
check(api.prava_klice('{"oblasti": ["uzivatele"]}')
      == {"oblasti": {"uzivatele"}, "anonymizace": True, "historie": False},
      "prava_klice: chybějící klíče = historie ne, jména schovaná")
with db.connect() as conn:
    conn.execute("UPDATE api_tokens SET prava = ? WHERE id = ?",
                 ('{"oblasti": ["uzivatele"], "historie": "false"}', jen_pod["id"]))
    conn.commit()
uzivatele = get(jen_pod["token"], "/api/v1/users")
check(get(jen_pod["token"], "/api/v1/history").status_code == 403
      and uzivatele.status_code == 200
      and not {"Jana", "Petr"} & {u.get("name") for u in uzivatele.json().get("users", [])},
      "ručně upravená práva se špatnými typy: bez historie a se schovanými jmény")
check(api.prava_klice(api.zabal_prava_klice(["knihovna", "neexistuje"], False, True))
      == {"oblasti": {"knihovna"}, "anonymizace": False, "historie": True},
      "zabal_prava_klice zahodí neznámou oblast")

from jellyscope import accounts  # noqa: E402

spravce_id = accounts.create("spravce", "dlouheheslo", is_admin=True)
uzivatelsky = api.vytvor("Muj", spravce_id)
check(api.uloz_prava_klice(uzivatelsky["id"], api.zabal_prava_klice([], False, False)) is None
      and db.query_value("SELECT prava FROM api_tokens WHERE id = ?", (uzivatelsky["id"],),
                         default=None) is None,
      "uživatelský klíč přes uloz_prava_klice změnit nejde")
check(api.popis_prav_do_logu(None) == "all"
      and api.popis_prav_do_logu(api.zabal_prava_klice(["zebricky", "knihovna"], False, True))
      == "areas=knihovna,zebricky history=yes hide_names=no",
      "popis práv do logu")
check(any(r["id"] == widget["id"] and "prava" in r for r in api.seznam()), "seznam() nese práva")


print("--- Nastavení ---")
from jellyscope import pristup  # noqa: E402

# Hlasky aplikacniho logu se chytaji primo z loggeru - soubor logu se
# v testu nezaklada.
class Zachyt(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.radky: list[str] = []

    def emit(self, zaznam: logging.LogRecord) -> None:
        self.radky.append(zaznam.getMessage())


zachyt = Zachyt()
logging.getLogger("jellyscope").addHandler(zachyt)
logging.getLogger("jellyscope").setLevel(logging.INFO)

accounts.create("ctenar", "ctenarheslo")
CELY_KLIC = r"js_[A-Za-z0-9_\-]{20,}"


def prihlas(jmeno: str, heslo: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
           follow_redirects=False)
    return k


def posli(k: TestClient, adresa: str, **data):
    return k.post(adresa, data=data, follow_redirects=False)


def root_klice() -> dict:
    return {r["id"]: r for r in api.seznam() if r["account_id"] is None}


def vyrob(k: TestClient, **data) -> tuple[dict, str]:
    """Vyrobí klíč formulářem; vrátí jeho řádek a celý klíč ze stránky."""
    pred = set(root_klice())
    posli(k, "/settings/api/token", **data)
    nove = [r for i, r in root_klice().items() if i not in pred]
    cely = re.findall(CELY_KLIC, k.get("/settings?section=api").text)
    return (nove[0] if len(nove) == 1 else {}), (cely[0] if len(cely) == 1 else "")


spravce_k = prihlas("spravce", "dlouheheslo")
ctenar_k = prihlas("ctenar", "ctenarheslo")

nastenka, N = vyrob(spravce_k, name="Nastenka", rezim="vybrane",
                    vidi_knihovna="on", vidi_neexistuje="on")
check(nastenka.get("account_id", 0) is None
      and api.prava_klice(nastenka.get("prava"))
      == {"oblasti": {"knihovna"}, "anonymizace": False, "historie": False},
      f"jen vybrané: root klíč jen s Knihovnou, neznámá oblast zahozená ({nastenka.get('prava')})")
check(get(N, "/api/v1/libraries").status_code == 200 and get(N, "/api/v1/users").status_code == 403,
      "ukázaný klíč smí jen svou oblast")
check(any("Spravce spravce vyrobil root klic Nastenka" in r
          and "areas=knihovna history=no hide_names=no" in r for r in zachyt.radky),
      f"vyrobení je v aplikačním logu i s právy ({zachyt.radky[-3:]})")

vse, _ = vyrob(spravce_k, name="Vse", rezim="vse", vidi_knihovna="on")
check(bool(vse) and vse["prava"] is None, "Všechno: práva NULL")

jen_historie, H = vyrob(spravce_k, name="JenHistorie", rezim="vybrane", historie="on")
check(get(H, "/api/v1/history").status_code == 200 and get(H, "/api/v1/libraries").status_code == 403,
      "jen historie bez oblasti: klíč vznikne a smí jen historii")

pocet = len(api.seznam())
posli(spravce_k, "/settings/api/token", name="Prazdny", rezim="vybrane", anonymizace="on")
check(len(api.seznam()) == pocet, "jen vybrané bez oblasti i historie: klíč nevznikne")
check("aspoň jednu oblast nebo historii" in spravce_k.get("/settings?section=api").text,
      "a stránka řekne proč")
# Jazyky, vlastni prehled, srovnani nemaji v API zadnou adresu - klic jen
# s nimi by nesmel nic a spravce by nevedel proc.
pocet_pred = len(api.seznam())
posli(spravce_k, "/settings/api/token", name="Jazyky", rezim="vybrane", vidi_jazyky="on",
      vidi_srovnani="on")
check(len(api.seznam()) == pocet_pred
      and "nic neotevírají" in spravce_k.get("/settings?section=api").text,
      "oblasti bez adresy v API: klíč nevznikne a stránka řekne proč")
check(api.adresy_root_klice(api.zabal_prava_klice(["jazyky"], False, False)) == []
      and "/api/v1/history" in api.adresy_root_klice(api.zabal_prava_klice([], False, True))
      and "/api/v1/libraries" in api.adresy_root_klice(api.zabal_prava_klice(["knihovna"], False, False)),
      "adresy_root_klice: co klíč s právy opravdu otevře")
soubor = spravce_k.post("/settings/api/token", data={"rezim": "vse"},
                        files={"name": ("klic.txt", b"obsah", "text/plain")}, follow_redirects=False)
check(soubor.status_code == 303 and not any("UploadFile" in r["name"] for r in api.seznam()),
      "soubor poslaný místo názvu se jménem klíče nestane")
check("klíč serveru" not in (pristup.anonymizuj_pro.__doc__ or ""), "docstring mluví o root klíči")
pocet = len(api.seznam())
for spatne in ({"rezim": "cokoliv", "vidi_knihovna": "on"}, {"vidi_knihovna": "on"}):
    posli(spravce_k, "/settings/api/token", name="Divny", **spatne)
check(len(api.seznam()) == pocet, "neznámý nebo chybějící režim: klíč nevznikne")

check(posli(ctenar_k, "/settings/api/token", name="Cizi", rezim="vse").status_code == 403
      and len(api.seznam()) == pocet, "čtenář root klíč nevyrobí (403)")
check(posli(ctenar_k, "/settings/api/token/prava", token_id=nastenka["id"], rezim="vse").status_code == 403
      and root_klice()[nastenka["id"]]["prava"] == nastenka["prava"], "ani neupraví (403)")

posli(spravce_k, "/settings/api/token/prava", token_id=uzivatelsky["id"],
      rezim="vybrane", vidi_knihovna="on")
check(db.query_value("SELECT prava FROM api_tokens WHERE id = ?", (uzivatelsky["id"],),
                     default=None) is None,
      "formulář root klíče uživatelský klíč nezmění")
check("Takový root klíč tu není" in spravce_k.get("/settings?section=api").text, "a řekne to")

odpoved = posli(spravce_k, "/settings/api/token/prava", token_id=nastenka["id"],
                rezim="vybrane", vidi_uzivatele="on", historie="on")
check(odpoved.status_code == 303 and get(N, "/api/v1/users").status_code == 200
      and get(N, "/api/v1/libraries").status_code == 403
      and get(N, "/api/v1/history").status_code == 200,
      "úprava přes Nastavení platí od dalšího dotazu")
check(any("Spravce spravce zmenil prava root klice Nastenka" in r
          and "areas=knihovna history=no hide_names=no -> areas=uzivatele history=yes hide_names=no" in r
          for r in zachyt.radky),
      f"změna je v aplikačním logu: kdo, před, po ({zachyt.radky[-2:]})")
posli(spravce_k, "/settings/api/token/prava", token_id=nastenka["id"], rezim="vybrane")
check(get(N, "/api/v1/users").status_code == 200, "neplatná úprava (nic nezaškrtnuté) práva nezmění")

print("--- nezávislost na správci ---")
druhy_id = accounts.create("druhy", "druheheslo", is_admin=True)
druhy_k = prihlas("druhy", "druheheslo")
_, D = vyrob(druhy_k, name="Druheho", rezim="vybrane", vidi_knihovna="on")


def funguje() -> bool:
    return (get(D, "/api/v1/libraries").status_code == 200
            and get(D, "/api/v1/users").status_code == 403)


check(bool(D) and funguje(), "root klíč druhého správce funguje")
accounts.set_admin(druhy_id, False)
check(funguje(), "i když správce přijde o roli")
accounts.delete(druhy_id)
check(funguje(), "i když je jeho účet smazaný")

print("--- stránka ---")


def radek_klice(html: str, jmeno: str) -> str:
    for radek in re.findall(r"<tr>.*?</tr>", html, re.S):
        if f">{jmeno}<" in radek:
            return re.sub(r"\s+", " ", radek)
    return ""


html = spravce_k.get("/settings?section=api").text
check("Root · všechno" in radek_klice(html, "Grafana"), "klíč serveru: Root · všechno")
check("Root · Seznam diváků, historie" in radek_klice(html, "Nastenka"),
      f"vybrané: výčet oblastí a historie ({radek_klice(html, 'Nastenka')[:300]})")
check("Uživatelský · " in radek_klice(html, "Muj") and "spravce" in radek_klice(html, "Muj"),
      "uživatelský klíč: Uživatelský · účet")
check(f'data-okno="okno-prava-klice-{nastenka["id"]}"' in html
      and f'id="okno-prava-klice-{nastenka["id"]}"' in html
      and f'okno-prava-klice-{uzivatelsky["id"]}"' not in html,
      "tlačítko a okno Práva jen u root klíčů")
check('id="okno-novy-klic"' in html and 'data-okno="okno-novy-klic"' in html,
      "nový klíč se vyrábí v okně")

vsechna_pole = {f"vidi_{o.klic}" for o in pristup.OBLASTI}
ucty = spravce_k.get("/settings?section=accounts").text
okno = re.search(r'<dialog[^>]*id="okno-prava-\d+".*?</dialog>', ucty, re.S)
check(okno is not None and set(re.findall(r'name="(vidi_[a-z_]+)"', okno.group(0))) == vsechna_pole,
      "okno Práva účtu má po přesunu stromu do makra pořád všechna pole")
check(len(re.findall(r'name="vidi_[a-z_]+" form="ctenari-form"', ucty)) == len(vsechna_pole),
      "strom skupiny čtenářů taky (s form=ctenari-form)")
jf = spravce_k.get("/settings?section=jellyfin").text
check(len(re.findall(r'name="vidi_[a-z_]+" form="divaci-form"', jf)) == len(vsechna_pole),
      "strom diváků v sekci Jellyfin taky (s form=divaci-form)")

print("--- log ---")
get(uzivatelsky["token"], "/api/v1/")


def telo(html: str) -> str:
    nalez = re.search(r"<tbody>.*?</tbody>", html, re.S)
    return re.sub(r"\s+", " ", nalez.group(0)) if nalez else ""


log_api = spravce_k.get("/settings?section=log&log_druh=api").text
check("root klíč" in telo(log_api) and ">Muj<" in telo(log_api).replace("> Muj <", ">Muj<"),
      "log API: u dotazu root klíčem „root klíč“, uživatelský klíč je tam taky")
volba = re.search(r'<option value="-"[^>]*>(.*?)</option>', log_api, re.S)
check(volba is not None and volba.group(1).strip() == "root klíče",
      "výběr uživatele: volba „root klíče“ s hodnotou -")
check(re.search(rf'<option value="{nastenka["id"]}"[^>]*>\s*Nastenka \(root klíč\)', log_api) is not None,
      "výběr klíče: u root klíče „(root klíč)“")
jen_root = telo(spravce_k.get("/settings?section=log&log_druh=api&log_ucet=-").text)
check("Nastenka" in jen_root and "spravce" not in jen_root,
      "filtr log_ucet=- vrátí jen root klíče")
check("klíč serveru" not in log_api and "klíč serveru" not in spravce_k.get("/settings?section=api").text,
      "„klíč serveru“ už na stránkách API ani Log není")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
