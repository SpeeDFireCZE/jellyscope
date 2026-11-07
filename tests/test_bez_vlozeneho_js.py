r"""Žádný vložený JavaScript ani obsluha v atributech (on…=, javascript:).

Hlavička CSP má `script-src 'self'`: prohlížeč spustí jen skripty ze
souborů aplikace. Kdyby se kdekoliv ve výpisu dat objevila chyba
v escapování, vložený `<script>` ani `onerror=` se nespustí - ale jen
tehdy, když je aplikace sama nepoužívá. Jinak by CSP musela vložený kód
povolit a pojistka by nebyla. Nový skript patří do static/js, hodnoty ze
šablony do datového bloku `texty-js` nebo do atributů `data-…`.

Hlídá se to dvakrát: ve zdrojích šablon (i větve, které se v testu
nevykreslí - ukázka, svátky) a ve vykreslených stránkách a výřezech jako
správce, čtenář, divák i nepřihlášený (i HTML, které skládá Python).

Spec: docs/superpowers/specs/2026-11-05-csp-skripty-design.md

Spuštění:
    .venv/bin/python tests/test_bez_vlozeneho_js.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _balik import podvrhni  # noqa: E402 - podvrh ve všech souborech podbalíku web/nastaveni

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bez_js.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, pristup, sekce  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


# Obsluha v atributu: on…= uvnitř značky, klidně přes víc řádků.
OBSLUHA = re.compile(r"<[a-zA-Z][^<>]*?\son[a-z]+\s*=", re.S)
# Adresa javascript: v hodnotě atributu.
JS_ADRESA = re.compile(r"=\s*[\"']?\s*javascript:", re.I)
# Spustitelný vložený skript: <script> bez src. Datový blok
# (type="application/json") se nespouští, ten smí.
VLOZENY_SKRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", re.I)


def obsluhy(html: str) -> list[str]:
    """Obsluhy v atributech, adresy javascript: a vložené skripty - úryvky pro hlášení."""
    nalezy = [html[max(0, m.end() - 80):m.end() + 40] for m in OBSLUHA.finditer(html)]
    nalezy += [html[m.start():m.end() + 60] for m in VLOZENY_SKRIPT.finditer(html)]
    nalezy += [html[max(0, m.start() - 40):m.end() + 20] for m in JS_ADRESA.finditer(html)]
    return nalezy


print("--- kontrola sebe ---")
check(len(obsluhy('<a onclick="x()">')) == 1, "najde onclick= ve značce")
check(len(obsluhy('<button\n  class="b"\n  onclick="a;\n b">')) == 1, "najde obsluhu přes víc řádků")
check(len(obsluhy('<a href="javascript:void(0)">')) == 1, "najde adresu javascript:")
check(obsluhy('<a data-zavrit-okno href="/x">') == [], "data-… atribut není obsluha")
check(obsluhy("if (a < b) { el.onclick = f; }") == [], "kód s porovnáním a přiřazením není značka")
check(len(obsluhy("<script>alert(1)</script>")) == 1, "najde vložený skript")
check(len(obsluhy('<SCRIPT type="module">x()</SCRIPT>')) == 1, "i s jiným typem a velkými písmeny")
check(obsluhy('<script type="application/json" id="x">{}</script>') == [], "datový blok JSON smí")
check(obsluhy('<script src="/static/js/app.js?v=1" defer></script>') == [], "skript ze souboru smí")


print()
print("--- (a) zdroje šablon ---")
JINJA_KOMENTAR = re.compile(r"\{#.*?#\}", re.S)
zakazane_zdroje = []
for soubor in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html")):
    text = JINJA_KOMENTAR.sub("", soubor.read_text(encoding="utf-8"))
    jmeno = soubor.relative_to(PROJECT / "jellyscope" / "templates").as_posix()
    zakazane_zdroje += [f"{jmeno}: {n!r}" for n in obsluhy(text)]
for radek in zakazane_zdroje:
    print("       " + radek)
check(not zakazane_zdroje, f"šablony bez obsluhy v atributech a vložených skriptů ({len(zakazane_zdroje)} nálezů)")


print()
print("--- (b) vykreslené stránky ---")
TED = datetime.now(UTC).replace(tzinfo=None)


def cas(posun: timedelta) -> str:
    return (TED + posun).strftime(db.TIME_FORMAT)


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo")
accounts.create("divak", "dlouheheslo")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-jana' WHERE username = 'divak'")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-jana', 'Jana', 0, 0), ('u-petr', 'Petr', 0, 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies'),"
                 " ('serialy', 'Seriály', 'tvshows')")
    conn.execute("INSERT INTO items (id, name, type, library_id, production_year, path, is_missing, date_created,"
                 " image_tag, video_codec, height, width) VALUES ('f1', 'Film', 'Movie', 'filmy', 2024,"
                 " '/media/filmy/Film.mkv', 0, ?, 'tag', 'hevc', 2160, 3840)", (cas(-timedelta(days=1)),))
    conn.execute("INSERT INTO items (id, name, type, library_id, series_id, series_name, parent_index_number,"
                 " index_number, path, is_missing, date_created) VALUES ('e1', 'Pilot', 'Episode', 'serialy',"
                 " 's1', 'Seriál', 1, 1, '/media/serialy/S/S01E01.mkv', 0, ?)", (cas(-timedelta(days=2)),))
    for klic, uid, jmeno, item, druh in (("h1", "u-jana", "Jana", "f1", "Movie"),
                                         ("h2", "u-petr", "Petr", "e1", "Episode")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " client, device_name, remote_address, play_method, audio_language, bitrate, started_at,"
            " last_seen_at, ended_at, watched_seconds, paused_seconds, is_active) VALUES (?, ?, ?, ?, 'X', ?,"
            " 'filmy', 'Jellyfin Web', 'Obyvak', '203.0.113.7', 'Transcode', 'cze', 8000000, ?, ?, ?, 3600, 0, 0)",
            (klic, uid, jmeno, item, druh, cas(-timedelta(hours=5)), cas(-timedelta(hours=4)),
             cas(-timedelta(hours=4))))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
        " client, device_name, play_method, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " position_ticks, is_active, media_runtime_ticks) VALUES ('zive', 'u-jana', 'Jana', 'f1', 'Film',"
        " 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak', 'DirectPlay', ?, ?, 600, 0, 6000000000, 1, 72000000000)",
        (cas(-timedelta(minutes=10)), cas(-timedelta(seconds=5))))
    conn.commit()
db.set_setting(sekce.ZAPNUTO, "1")
db.set_setting(pristup.LOGIN_KLIC, "1")


# Rychlé přihlášení se otevírá POSTem z /login a ptá se Jellyfinu - ten
# v testu není, odpovědi se podvrhnou.
async def _ano(self) -> bool:
    return True


async def _zahaj(self):
    return {"kod": "123456", "tajemstvi": "t"}


JellyfinClient.quick_connect_zapnuty = _ano
JellyfinClient.quick_connect_zahaj = _zahaj


def prihlas(jmeno: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": jmeno, "password": "dlouheheslo", "zpusob": "mistni"})
    return k


# Stahování a akce, které nejsou stránka. Odkazy z menu se jinak berou všechny.
NE = ("/logout", "/static/", "/image/", "/settings/backup/", "/settings/log/stahnout", "/api/")
ODKAZ = re.compile(r'href="(/[^"#]*)"')


def klic_stranky(adresa: str) -> str:
    """Varianty jedné stránky jen s jiným obdobím nebo filtrem jsou jedna stránka."""
    casti = urlsplit(adresa.replace("&amp;", "&"))
    zachovat = {k: v[0] for k, v in parse_qs(casti.query).items() if k in ("section", "tab")}
    return casti.path + ("?" + "&".join(f"{k}={v}" for k, v in sorted(zachovat.items())) if zachovat else "")


def projdi(k: TestClient, zacatky: list[str], hloubka: int) -> dict[str, str]:
    """Stránky dosažitelné odkazy z `zacatky` do dané hloubky: {adresa: HTML}."""
    videne: dict[str, str] = {}
    vlna = [klic_stranky(z) for z in zacatky]
    for _ in range(hloubka + 1):
        dalsi = []
        for adresa in vlna:
            if adresa in videne or adresa.startswith(NE):
                continue
            r = k.get(adresa, follow_redirects=False)
            videne[adresa] = r.text if r.status_code == 200 and "html" in r.headers.get("content-type", "") else ""
            dalsi += [klic_stranky(o) for o in ODKAZ.findall(videne[adresa])]
        vlna = dalsi
    return {a: h for a, h in videne.items() if h}


VYREZY = ["/partials/now-playing", "/partials/recently-added", "/partials/daily", "/partials/top-items",
          "/partials/network-live"]
spravce = prihlas("spravce")
STRANKY = sorted(projdi(spravce, ["/", "/settings"], 2)) + VYREZY
check(len(STRANKY) >= 40, f"prošlo se dost stránek ({len(STRANKY)})")


def zkontroluj(nazev: str, k: TestClient, adresy: list[str], *, post: bool = False) -> None:
    for adresa in adresy:
        r = k.post(adresa, follow_redirects=False) if post else k.get(adresa, follow_redirects=False)
        if "html" not in r.headers.get("content-type", ""):
            continue
        zakazane = obsluhy(r.text)
        check(not zakazane, f"{nazev} {adresa} ({r.status_code}): bez obsluhy a vloženého skriptu {zakazane[:2]}")
        # Starý JavaScript z mezipaměti po aktualizaci: každý soubor s verzí.
        bez_verze = [s for s in re.findall(r'src="(/static/js/[^"]+)"', r.text) if "?v=" not in s]
        check(not bez_verze, f"{nazev} {adresa}: skripty s verzí v adrese {bez_verze}")


zkontroluj("správce", spravce, STRANKY + ["/neexistuje"])
zkontroluj("čtenář", prihlas("ctenar"), STRANKY + ["/settings"])
zkontroluj("divák", prihlas("divak"), STRANKY + ["/users/u-jana"])
host = TestClient(app)
zkontroluj("nepřihlášený", host, ["/login", "/neexistuje"])
zkontroluj("nepřihlášený", host, ["/login/quick"], post=True)

print()
print("--- (d) motiv v hlavičce, před vykreslením ---")
for adresa, k in (("/", spravce), ("/login", host)):
    html = k.get(adresa).text
    hlava = html.split("</head>", 1)[0]
    znacka = re.search(r'<script\b[^>]*src="/static/js/zaklad\.js[^"]*"[^>]*>', hlava)
    check(bool(znacka) and "defer" not in znacka.group(0) and "async" not in znacka.group(0),
          f"{adresa}: zaklad.js v <head> bez defer a async ({znacka and znacka.group(0)})")

print()
print("--- (g) hlavička CSP ---")
# Bez 'unsafe-inline' u skriptů: prohlížeč spustí jen soubory aplikace.
# Styly zůstávají (rozhodnutí 5. 11. 2026 - mimo rozsah přestavby).
for adresa, k in (("/", spravce), ("/login", host)):
    csp = k.get(adresa).headers.get("Content-Security-Policy", "")
    pravidla = {c.split()[0]: c.split()[1:] for c in (x.strip() for x in csp.split(";")) if c}
    check(pravidla.get("script-src") == ["'self'"], f"{adresa}: script-src jen 'self' ({pravidla.get('script-src')})")
    check(pravidla.get("style-src") == ["'self'", "'unsafe-inline'"],
          f"{adresa}: style-src beze změny ({pravidla.get('style-src')})")

print()
print("--- (h) stránky, které skládá Python ---")
# Čekárna po aktualizaci (web.nastaveni._stranka_aktualizace) se skládá
# bez šablony a vloženému skriptu se nevyhne: vykresluje ji stará verze
# nad novými soubory, takže nesmí sáhnout na nic, co se s verzí mění - ani
# na static/js. Její skript proto nese jednorázový nonce z hlavičky.
from jellyscope import updates  # noqa: E402
from jellyscope.web import nastaveni as web_nastaveni  # noqa: E402


async def _aktualizace_ok():
    return {"status": "ok"}


_puvodni = (updates.aktualizuj, web_nastaveni._naplanuj_restart)
updates.aktualizuj = _aktualizace_ok
podvrhni(web_nastaveni, "_naplanuj_restart", lambda: None)
try:
    r = spravce.post("/settings/update", follow_redirects=False)
finally:
    updates.aktualizuj = _puvodni[0]
    podvrhni(web_nastaveni, "_naplanuj_restart", _puvodni[1])
csp = r.headers.get("Content-Security-Policy", "")
nonce = re.search(r"script-src 'self' 'nonce-([A-Za-z0-9_-]{16,})'", csp)
skripty = re.findall(r"<script\b[^>]*>", r.text, re.I)
check(r.status_code == 200 and bool(nonce), f"čekárna: hlavička má nonce ({csp[:120]})")
check(bool(skripty) and nonce is not None and all(f'nonce="{nonce.group(1)}"' in s for s in skripty),
      f"čekárna: každý její skript nese nonce z hlavičky ({skripty})")
check(not [n for n in obsluhy(r.text) if "nonce=" not in n], "čekárna: jinak bez vloženého kódu")
check(spravce.get("/").headers.get("Content-Security-Policy", "").count("nonce-") == 0,
      "ostatní stránky nonce nemají (zůstávají bez vloženého kódu)")

print()
print("--- (f) texty pro skripty ---")
import json  # noqa: E402

from jellyscope import i18n  # noqa: E402

TEXTY = re.compile(r'<script type="application/json" id="texty-js">(.*?)</script>', re.S)


def texty(text: str) -> dict:
    """Obsah bloku tak, jak ho čte app.js (JSON.parse textContent).

    Bez html.unescape: prohlížeč entity uvnitř <script> nedekóduje, takže
    blok escapovaný jako HTML by JSON.parse shodil - a s ním celý app.js.
    """
    m = TEXTY.search(text)
    try:
        return json.loads(m.group(1)) if m else {}
    except ValueError:
        return {}


def v_jazyce(jazyk: str, nacti):
    """Stránka v jazyce serveru `jazyk` (účty bez vlastní volby ho přebírají)."""
    db.set_setting("ui_language", jazyk)
    try:
        return texty(nacti())
    finally:
        db.set_setting("ui_language", "cs")


for nazev, nacti, klice in (
        ("/", lambda: spravce.get("/").text, {"ukazka", "hotovo", "trva_dlouho"}),
        ("/login/quick", lambda: TestClient(app).post("/login/quick").text, {"kod_neplati"})):
    cs, en = v_jazyce("cs", nacti), v_jazyce("en", nacti)
    check(set(cs) == klice and all(cs.values()), f"{nazev}: texty-js je platný JSON s klíči {sorted(klice)} ({sorted(cs)})")
    check(set(en) == klice and en != cs, f"{nazev}: v angličtině jiné texty ({en})")

# Překlad, který by blok ukončil: tojson escapuje „<", takže </script>
# zůstane textem uvnitř bloku a za ním nevznikne spustitelný skript.
UTOK = 'hotovo</script><script>alert(1)</script>'
puvodni = i18n.TRANSLATIONS["en"].get("hotovo")
i18n.TRANSLATIONS["en"]["hotovo"] = UTOK
try:
    db.set_setting("ui_language", "en")
    stranka = spravce.get("/").text
finally:
    db.set_setting("ui_language", "cs")
    if puvodni is None:
        i18n.TRANSLATIONS["en"].pop("hotovo", None)
    else:
        i18n.TRANSLATIONS["en"]["hotovo"] = puvodni
check(texty(stranka).get("hotovo") == UTOK, "překlad s </script> zůstane celý uvnitř bloku")
check(not obsluhy(stranka), f"a za blokem nevznikne vložený skript ({obsluhy(stranka)[:1]})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
