r"""Každé oprávnění drží - na stránkách, ve fragmentech i v API.

Strom „co kdo vidí" (pristup.OBLASTI) hlídá middleware podle adres. To
ale nestačí: stránka jedné oblasti může vložit obsah jiné (Přehled kartu
„Právě se hraje" nebo „Nedávno přidané"). Tady se proto pro každou oblast
zvlášť vypne jen ona a projde se všechno, co účet otevřít smí:

* adresy vypnuté oblasti se neotevřou (403, u Přehledu přesměrování),
* obsah vypnuté oblasti není na žádné jiné stránce ani v API,
* se vším zapnutým se tentýž obsah opravdu ukazuje - jinak by značky
  hlídaly naprázdno.

Totéž pro API: klíč účtu vidí přesně to, co jeho vlastník, a root klíč
s vybranými oblastmi jen ty oblasti.

Spuštění:
    .venv/bin/python tests/test_prava_matice.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "matice.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"
os.environ.pop("JELLYSCOPE_UDALOST_DATUM", None)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, collector, db, pristup, sekce, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# --- data ----------------------------------------------------------------
db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo")
accounts.create("divak", "dlouheheslo")
TED = datetime.now(timezone.utc).replace(tzinfo=None)


def cas(posun: timedelta) -> str:
    return (TED + posun).strftime(db.TIME_FORMAT)


with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-petr' WHERE username = 'divak'")
    conn.execute("UPDATE accounts SET smi_klic = '1' WHERE username IN ('ctenar', 'divak')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-petr', 'PetrZQX', 0, 0), ('u-jana', 'JanaZQX', 0, 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for item_id, nazev in (("film-1", "KnihaTitulQX"), ("film-2", "ZivyTitulQX")):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, production_year, path, is_missing,"
            " date_created, video_codec, height, width) VALUES (?, ?, 'Movie', 'filmy', 2024, ?, 0, ?,"
            " 'hevc', 2160, 3840)",
            (item_id, nazev, f"/media/filmy/{nazev}.mkv", cas(-timedelta(days=1))))
    # Dokončené přehrávání (Petr i Jana na film-1) a jedno právě běžící (Jana na film-2).
    for klic, uzivatel, jmeno, adresa in (("hotovo-p", "u-petr", "PetrZQX", "203.0.113.77"),
                                          ("hotovo-j", "u-jana", "JanaZQX", "203.0.113.78")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
            " library_id, client, device_name, remote_address, play_method, audio_language,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
            " VALUES (?, ?, ?, 'film-1', 'KnihaTitulQX', 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak',"
            " ?, 'DirectPlay', 'cze', ?, ?, ?, 5400, 0, 0)",
            (klic, uzivatel, jmeno, adresa, cas(-timedelta(hours=5)), cas(-timedelta(hours=3)),
             cas(-timedelta(hours=3))))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, client, device_name, remote_address, play_method, started_at, last_seen_at,"
        " watched_seconds, paused_seconds, position_ticks, is_active, media_runtime_ticks)"
        " VALUES ('zive-j', 'u-jana', 'JanaZQX', 'film-2', 'ZivyTitulQX', 'Movie', 'filmy',"
        " 'Jellyfin Web', 'Obyvak', '198.51.100.9', 'DirectPlay', ?, ?, 600, 0, 6000000000, 1,"
        " 72000000000)", (cas(-timedelta(minutes=10)), cas(-timedelta(seconds=5))))
    conn.commit()

# Vlastní přehled zapnutý a v něm karta z Přehledu - ať /dashboard je co otevřít.
db.set_setting(sekce.ZAPNUTO, "1")
# Klíč diváka platí jen se zapnutým přihlášením přes Jellyfin (divák je účet odtamtud).
db.set_setting(pristup.LOGIN_KLIC, "1")
# Sběrač „běží" - jen pak levý panel vypisuje stav i s počtem přehrání.
db.set_setting(collector.STATUS_KEY, "ok")
sekce.uloz_rozvrzeni([("souhrn", "cela")])
db.forget_settings()

VSECHNY = [o.klic for o in pristup.OBLASTI]


def nastav(jmeno: str, zapnute: list[str], anonymizace: bool = False) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET pristup = ? WHERE username = ?",
                     (pristup.zabal_prava(zapnute, anonymizace), jmeno))
        conn.commit()
    db.forget_settings()


def efektivne(zapnute: set[str]) -> set[str]:
    """Které oblasti platí: pod-oblast jen se zapnutou nadřazenou."""
    vysledek = set()
    for oblast in pristup.OBLASTI:
        if oblast.klic in zapnute and (not oblast.pod or oblast.pod in zapnute):
            vysledek.add(oblast.klic)
    return vysledek


def prihlas(jmeno: str) -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": jmeno, "password": "dlouheheslo", "zpusob": "mistni"})
    return k


# Adresy každé oblasti, konkrétně.
ADRESY_OBLASTI = {
    "zebricky": ["/insights", "/top-items"],
    "knihovna": ["/library", "/library/filmy", "/item/film-1", "/partials/recently-added"],
    "knihovna_kdo": [],
    "prehled": ["/", "/partials/daily"],
    "dashboard": ["/dashboard"],
    "srovnani": ["/srovnani"],
    "prave_hraje": ["/partials/now-playing", "/api/now-playing"],
    "jazyky": ["/languages"],
    "uzivatele": ["/users", "/users/u-jana"],
    "sit": ["/network", "/partials/network-live"],
}
check(set(ADRESY_OBLASTI) == set(VSECHNY), f"test zná všechny oblasti ({sorted(set(VSECHNY) - set(ADRESY_OBLASTI))})")
VZDY_STRANKY = ["/history", "/account", "/health", "/library/filmy?tab=activity"]
# Adresy, které patří víc oblastem: otevřou se, když platí aspoň jedna.
# Přepínač Filmy/Seriály u karty Nejsledovanější na Přehledu ji volá
# stejně jako stránka Zjištění.
SDILENE = {"/partials/top-items": ("zebricky", "prehled")}
VSECHNY_STRANKY = [a for adresy in ADRESY_OBLASTI.values() for a in adresy] + VZDY_STRANKY + list(SDILENE)

# Obsah oblasti: (popis, značka) - značka nesmí být nikde, kde oblast neplatí.
OBSAH = {
    # Titul běžícího přehrávání je i v Historii a žebříčcích - to je v pořádku.
    # Přehrávání „teď" poznají karta a pozice v titulu (position_seconds).
    "prave_hraje": [("karta přehrávání", '<div class="session"'), ("pozice přehrávání", '"position_seconds"')],
    "knihovna": [("Nedávno přidané", 'id="recently-added"')],
    "sit": [("IP adresa", "203.0.113.77"), ("IP adresa živého", "198.51.100.9")],
}
# Počet běžících přehrání (/health) je taky „kdo co sleduje teď".
OBSAH["prave_hraje"].append(("počet přehrání v /health", '"active_sessions"'))
# ...a v levém panelu každé stránky (pod stavem sběrače) a v souhrnu API.
OBSAH["prave_hraje"].append(("počet přehrání v panelu", "aktivních přehrávání"))
# V API i souhrn (na stránce Můj účet je "active_now" jen v ukázce návodu).
OBSAH_API = {"prave_hraje": [("počet přehrání v API souhrnu", '"active_now"')]}
# Kdo to sledoval - detail titulu a aktivita knihovny (jména jinde řídí anonymizace).
KDO_SLEDOVAL = ("/item/film-1", "/library/filmy?tab=activity")
JMENO = "JanaZQX"


def blokovano(stav: int) -> bool:
    return stav in (303, 307, 403, 404)


def mrtve_odkazy(k: TestClient, stranky: dict) -> list[tuple[str, str]]:
    """Odkazy ze stránek, které končí na „sem nesmíš" (403) nebo nikam (404)."""
    mrtve, videne = [], {}
    for adresa, (stav, text) in stranky.items():
        if stav != 200:
            continue
        for cil in sorted(set(re.findall(r'href="(/[^"#]*)"', text))):
            if cil.startswith(("/static/", "/image/", "/logout")):
                continue
            if cil not in videne:
                videne[cil] = k.get(cil, follow_redirects=False).status_code
            if videne[cil] in (403, 404):
                mrtve.append((adresa, cil))
    return mrtve


def projdi(jmeno: str, zapnute: set[str], popis: str) -> None:
    nastav(jmeno, sorted(zapnute))
    plati = efektivne(zapnute)
    k = prihlas(jmeno)
    stranky = {}
    for adresa in VSECHNY_STRANKY:
        r = k.get(adresa, follow_redirects=False)
        stranky[adresa] = (r.status_code, r.text if r.status_code == 200 else "")
    # 1) Adresy oblastí: otevřou se, jen když oblast platí.
    for oblast, adresy in ADRESY_OBLASTI.items():
        for adresa in adresy:
            if jmeno == "divak" and adresa == "/users/u-jana":
                continue      # divák smí jen svou stránku diváka - hlídá test_rozsah_divaka
            stav = stranky[adresa][0]
            if oblast in plati:
                check(stav == 200, f"{popis}: {adresa} se otevře ({stav})")
            else:
                check(blokovano(stav), f"{popis}: {adresa} je zavřená ({stav})")
    for adresa, oblasti in SDILENE.items():
        stav = stranky[adresa][0]
        if set(oblasti) & plati:
            check(stav == 200, f"{popis}: {adresa} se otevře ({stav})")
        else:
            check(blokovano(stav), f"{popis}: {adresa} je zavřená ({stav})")
    # Historie: divák vidí jen svou, takže vždy. Čtenář nemá vlastního
    # diváka - jeho Historie je celá cizí (jména, zařízení), proto jen
    # s „…včetně kdo to sledoval". Menu ji pak čtenáři nenabízí.
    smi_historii = jmeno == "divak" or "knihovna_kdo" in plati
    stav = stranky["/history"][0]
    check((stav == 200) == smi_historii,
          f"{popis}: /history {'se otevře' if smi_historii else 'je zavřená'} ({stav})")
    if jmeno == "ctenar":
        v_menu = 'href="/history"' in stranky["/account"][1]
        check(v_menu == smi_historii, f"{popis}: Historie v menu {'je' if v_menu else 'není'}")
    # Celý žebříček (/top-items) jen s jeho oblastí - jinak by odkaz vedl na „sem nesmíš“.
    stav, text = stranky["/"]
    if stav == 200 and "zebricky" not in plati:
        check('href="/top-items' not in text, f"{popis}: Přehled neodkazuje na celý žebříček")
    # Odkaz, za kterým čeká „sem nesmíš", je horší než žádný.
    mrtve = mrtve_odkazy(k, stranky)
    check(not mrtve, f"{popis}: žádný odkaz nevede na zavřenou stránku ({len(mrtve)}: {mrtve[:4]})")
    # 2) Obsah vypnutých oblastí na žádné otevřené stránce.
    for oblast, znacky in OBSAH.items():
        if oblast in plati:
            continue
        for nazev, znacka in znacky:
            kde = [a for a, (stav, text) in stranky.items() if znacka in text]
            check(not kde, f"{popis}: {nazev} ({oblast}) se neukáže nikde ({kde})")
    for adresa in KDO_SLEDOVAL:
        stav, text = stranky[adresa]
        if stav != 200:
            continue
        if "knihovna_kdo" in plati:
            check(JMENO in text, f"{popis}: {adresa} vypíše diváky (knihovna_kdo zapnuté)")
        else:
            check(JMENO not in text, f"{popis}: {adresa} bez diváků (knihovna_kdo vypnuté)")


print("--- se vším zapnutým se obsah oblastí opravdu ukazuje (značky nehlídají naprázdno) ---")
nastav("ctenar", VSECHNY)
k = prihlas("ctenar")
texty = {a: k.get(a, follow_redirects=False).text for a in VSECHNY_STRANKY}
for oblast, znacky in OBSAH.items():
    for nazev, znacka in znacky:
        check(any(znacka in t for t in texty.values()), f"{nazev} ({oblast}) je vidět aspoň někde")
check('"active_now"' in TestClient(web.app).get("/api/v1/summary", headers={
    "Authorization": f"Bearer {api.vytvor('kontrola', account_id=accounts.get_by_name('ctenar')['id'])['token']}"}).text,
      "počet přehrání v API souhrnu je vidět se vším zapnutým")
for adresa in KDO_SLEDOVAL:
    check(JMENO in texty[adresa], f"{adresa}: diváci jsou vidět (knihovna_kdo zapnuté)")

for jmeno in ("ctenar", "divak"):
    print()
    print(f"--- {jmeno}: vypnutá vždy jen jedna oblast ---")
    projdi(jmeno, set(VSECHNY), f"{jmeno}, vše")
    for oblast in VSECHNY:
        projdi(jmeno, set(VSECHNY) - {oblast}, f"{jmeno} bez {oblast}")


print()
print("--- se skrytými jmény není cizí jméno nikde (stránky i API) ---")

for jmeno, cizi in (("ctenar", ("JanaZQX", "PetrZQX")), ("divak", ("JanaZQX",))):
    nastav(jmeno, VSECHNY, anonymizace=True)
    k = prihlas(jmeno)
    stranky = {}
    for adresa in VSECHNY_STRANKY:
        r = k.get(adresa, follow_redirects=False)
        stranky[adresa] = (r.status_code, r.text)
        if r.status_code != 200:
            continue
        uniklo = [j for j in cizi if j in r.text]
        check(not uniklo, f"{jmeno} se skrytými jmény: {adresa} bez cizích jmen ({uniklo})")
    # Náhradní id místo skutečného nesmí vést na prázdnou stránku.
    mrtve = mrtve_odkazy(k, stranky)
    check(not mrtve, f"{jmeno} se skrytými jmény: žádný mrtvý odkaz ({len(mrtve)}: {mrtve[:4]})")

print()
print("--- graf Diváci v knihovně: jeden divák = jeden sloupec ---")
# Přejmenovaný divák má do další synchronizace v historii obě jména.
from jellyscope import stats  # noqa: E402

with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
        " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
        " VALUES ('prejmenovana', 'u-jana', 'JanaStaraQX', 'film-1', 'KnihaTitulQX', 'Movie', 'filmy', ?, ?, ?,"
        " 1800, 0, 0)", (cas(-timedelta(hours=8)), cas(-timedelta(hours=7)), cas(-timedelta(hours=7))))
    conn.commit()
divaci = stats.library_activity("filmy", 30)["top_users"]
jany = [r for r in divaci if r.get("user_id") == "u-jana"]
# 5400 s dokončeného + 600 s běžícího + 1800 s pod starým jménem
check(len(jany) == 1 and abs(float(jany[0]["hours"]) - 7800 / 3600) < 0.01 and jany[0]["label"] == "JanaZQX",
      f"přejmenovaná divačka je jeden sloupec se všemi hodinami ({[(r['label'], round(float(r['hours']), 2)) for r in jany]})")
# Import bez UserId: různá jména zůstanou různými sloupci (NULL se jinak
# v GROUP BY slije do jednoho).
with db.connect() as conn:
    for klic, jmeno in (("import-a", "ImportAQX"), ("import-b", "ImportBQX")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
            " VALUES (?, NULL, ?, 'film-1', 'KnihaTitulQX', 'Movie', 'filmy', ?, ?, ?, 600, 0, 0)",
            (klic, jmeno, cas(-timedelta(hours=8)), cas(-timedelta(hours=7)), cas(-timedelta(hours=7))))
    conn.commit()
bez_id = sorted(r["label"] for r in stats.library_activity("filmy", 30)["top_users"] if not r.get("user_id"))
check(bez_id == ["ImportAQX", "ImportBQX"], f"dvě jména bez user_id jsou dva sloupce ({bez_id})")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key IN ('prejmenovana', 'import-a', 'import-b')")
    conn.commit()

# --- API ------------------------------------------------------------------
print()
print("--- API: klíč účtu vidí přesně to, co jeho vlastník ---")
ADRESY_API = {cesta.replace("{item_id}", "film-1"): oblast for cesta, oblast in api.OBLASTI_ADRES.items()
              if cesta not in ("/api/v1", "/api/v1/")}
klice = {}
for jmeno in ("ctenar", "divak"):
    ucet = accounts.get_by_name(jmeno)
    klice[jmeno] = api.vytvor(f"klic-{jmeno}", account_id=ucet["id"])["token"]


def api_get(token: str, adresa: str):
    return TestClient(web.app).get(adresa, headers={"Authorization": f"Bearer {token}"})


def projdi_api(token: str, plati: set[str], historie: bool, popis: str) -> None:
    odpovedi = {}
    for adresa, oblast in ADRESY_API.items():
        r = api_get(token, adresa)
        odpovedi[adresa] = r
        smi = (historie if adresa == "/api/v1/history" else (oblast is None or oblast in plati))
        if smi:
            check(r.status_code == 200, f"{popis}: {adresa} odpoví ({r.status_code})")
        else:
            check(r.status_code == 403, f"{popis}: {adresa} je zavřená ({r.status_code})")
    for oblast, znacky in list(OBSAH.items()) + list(OBSAH_API.items()):
        if oblast in plati:
            continue
        for nazev, znacka in znacky:
            hledej = znacka if oblast in OBSAH_API and (nazev, znacka) in OBSAH_API[oblast] else znacka.strip('"')
            kde = [a for a, r in odpovedi.items() if r.status_code == 200 and hledej in r.text]
            if nazev == "karta přehrávání":
                continue      # HTML značka - v JSON API nemá co dělat
            check(not kde, f"{popis}: {nazev} ({oblast}) není v žádné odpovědi API ({kde})")
    if "knihovna_kdo" not in plati:
        kde = [a for a in ("/api/v1/item/film-1", "/api/v1/library", "/api/v1/libraries", "/api/v1/recently-added")
               if odpovedi[a].status_code == 200 and JMENO in odpovedi[a].text]
        check(not kde, f"{popis}: knihovna v API bez diváků (knihovna_kdo vypnuté) ({kde})")


for jmeno in ("ctenar", "divak"):
    for oblast in [None] + VSECHNY:
        zapnute = set(VSECHNY) - ({oblast} if oblast else set())
        nastav(jmeno, sorted(zapnute))
        # Historie klíče účtu = historie účtu (viz /history výš).
        projdi_api(klice[jmeno], efektivne(zapnute),
                   jmeno == "divak" or "knihovna_kdo" in efektivne(zapnute),
                   f"klíč {jmeno} " + (f"bez {oblast}" if oblast else "se vším"))

print()
print("--- API: root klíč s jednou oblastí otevře jen ji ---")
for oblast in VSECHNY:
    zapnute = {oblast} | ({pristup.PODLE_KLICE[oblast].pod} - {""})
    for historie in (False, True):
        token = api.vytvor(f"root-{oblast}-{historie}",
                           prava=api.zabal_prava_klice(sorted(zapnute), False, historie))["token"]
        projdi_api(token, efektivne(zapnute), historie,
                   f"root {'+'.join(sorted(zapnute))}{' + historie' if historie else ''}")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
