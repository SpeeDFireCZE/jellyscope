# -*- coding: utf-8 -*-
"""Volba preferovaného jazyka, obnova nedávno přidaných a hlášky o spojení.

Tři nezávislé věci, každá s vlastní pastí:

  * **Preferovaný jazyk.** Stránka Jazyky měla češtinu zadrátovanou v SQL,
    v názvech funkcí i v nadpisech. Kdo si Jellyscope postaví v Portugalsku,
    dostal stránku o něčem, co ho nezajímá. Volba se ukládá, takže po
    obnovení stránky zůstane – a nabízí se jen jazyky, které v knihovně
    doopravdy jsou.

  * **Nedávno přidané.** Úloha na pozadí najde nový film a zapíše ho do
    databáze. Na už otevřeném Přehledu se ale nic nestalo, dokud stránku
    někdo neobnovil ručně. Slouží k tomu otisk knihovny: změní se jen
    tehdy, když je opravdu co ukázat.

  * **„Nepodařilo se spojit."** Když Jellyfin odpovídá pomalu, je to něco
    úplně jiného než když neodpovídá vůbec. Se společným třicetivteřinovým
    stropem padala plná synchronizace velké knihovny na hlášku o spojení –
    a chyba se pak marně hledala v adrese a API klíči.

Spusteni:
    .\\.venv\\Scripts\\python.exe tests\\test_jazyk_vyber_a_spojeni.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vyber.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402

from jellyscope import accounts, db, demodata, jellyfin, langstats, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "tajneheslo1", is_admin=True)
accounts.create("ctenar", "ctenarheslo1", is_admin=False)
demodata.seed()

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402


print("--- nabízejí se jen jazyky, které v knihovně jsou ---")
volby = langstats.library_language_options()
kody = [v["code"] for v in volby]
check(len(kody) > 0, f"knihovna nějaké jazyky má ({kody})")
check(len(kody) == len(set(kody)), "žádný jazyk není v seznamu dvakrát")
check("und" not in kody, "'Neuvedeno' se jako volba nenabízí")
check(all(v["title_count"] > 0 for v in volby), "u každé volby je počet titulů")

# Seřazeno podle počtu: nejčastější jazyk nahoře, kde ho člověk čeká.
pocty = [v["title_count"] for v in volby]
check(pocty == sorted(pocty, reverse=True), f"seřazeno podle počtu titulů ({pocty})")

# Jazyk, který v knihovně opravdu není, se mezi volbami objevit nesmí -
# jinak by šlo vybrat něco, co vyrobí stránku samých nul.
vsechny_stopy = db.query_all(
    "SELECT audio_languages FROM items WHERE audio_languages IS NOT NULL")
skutecne = set()
for radek in vsechny_stopy:
    skutecne.update(part for part in str(radek["audio_languages"]).split(",") if part)
check(set(kody) <= skutecne, "nenabízí se nic, co v datech není")


print()
print("--- volba se ukládá a přežije obnovení stránky ---")
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "tajneheslo1"})

    vychozi = langstats.preferred_language()
    check(vychozi == "cs", f"bez nastavení se drží čeština ({vychozi})")

    jiny = next(kod for kod in kody if kod != "cs")
    response = client.post("/languages/preferred", data={"code": jiny},
                           follow_redirects=False)
    check(response.status_code == 303, "volba přijata")
    check(db.get_setting(langstats.PREFERRED_SETTING) == jiny,
          f"uložila se do nastavení ({db.get_setting(langstats.PREFERRED_SETTING)})")
    check(langstats.preferred_language() == jiny, "a čte se zpátky")

    # Tohle je jádro požadavku: po obnovení stránky výběr nezmizí.
    page = client.get("/languages?days=365").text
    nazev = langstats.languages.display(jiny)
    check(f'value="{jiny}"' in page and "selected" in page,
          "ve výběru zůstane vybraný ten uložený")

    # U každé volby musí být vidět počet titulů. Vypadá to jako drobnost,
    # ale je to past: kdyby se klíč jmenoval "items", Jinja by v šabloně
    # sáhla na metodu `dict.items` místo na hodnotu a místo čísla by se
    # vypsala pomlčka. Chyba, kterou test odhalí a oko na první pohled ne.
    volby_v_html = re.findall(r"<option[^>]*>\s*(.*?)\s*</option>", page, re.S)
    check(all(re.search(r"\(\s*\d", v) for v in volby_v_html),
          f"u každé volby je vidět počet titulů ({volby_v_html})")

    print()
    print("--- nadpisy se mění spolu s volbou ---")
    check(f"Podíl jazyka {nazev}" in re.sub(r"\s+", " ", page),
          f"hlavní číslo mluví o zvoleném jazyce ({nazev})")
    nadpisy = [re.sub(r"\s+", " ", h) for h in re.findall(r"<h2>(.*?)</h2>", page, re.S)]
    chybi = [h for h in nadpisy if "stopa chybí" in h]
    check(any(nazev in h for h in chybi),
          f"i nadpis seznamu chybějících stop ({chybi})")
    check(not any("češtin" in h.lower() for h in nadpisy),
          f"a nikde nezůstala natvrdo čeština ({nadpisy})")

    print()
    print("--- z každého řádku vede proklik na detail ---")
    chybejici = langstats.missing_preferred(365, jiny)
    check(bool(chybejici["rows"]), "seznam něco obsahuje")
    check(all(r["detail_url"] for r in chybejici["rows"]), "každý řádek má odkaz")
    # Seriál vede na seriál (tam jsou díly), film na detail souboru.
    for radek in chybejici["rows"]:
        cekano = (f"/series/{radek['series_id']}" if radek["series_id"]
                  else f"/item/{radek['item_id']}")
        if radek["detail_url"] != cekano:
            check(False, f"špatný cíl u {radek['label']}: {radek['detail_url']}")
            break
    else:
        check(True, "seriál míří na seriál, film na soubor")

    # A odkaz musí opravdu někam vést, ne skončit na 404.
    cil = chybejici["rows"][0]["detail_url"]
    check(client.get(cil).status_code == 200, f"odkaz {cil} funguje")

    print()
    print("--- seriál se hlásí celý jen tehdy, když chybí opravdu všem dílům ---")
    # Nahlášená chyba: filtr na jazyk se uplatnil na jednotlivé díly ještě
    # PŘED seskupením, takže ze skupiny vypadly právě ty díly, které stopu
    # mají - a zbytek se tvářil jako "celý seriál stopu nemá", i když ji
    # neměl třeba jediný díl z dvaceti.
    ted = db.utcnow()
    with db.connect() as conn:
        conn.execute("INSERT INTO libraries (id, name, synced_at)"
                     " VALUES ('lib-t','Serialy',?)", (ted,))
        # Seriál A: čeština u všech dílů kromě jednoho.
        for cislo, jazyky in ((1, "cs,en"), (2, "cs,en"), (3, "en")):
            conn.execute(
                """INSERT INTO items (id, name, type, library_id, series_id,
                                      series_name, parent_index_number,
                                      index_number, audio_languages,
                                      subtitle_languages, is_missing, synced_at)
                   VALUES (?,?,'Episode','lib-t','ser-a','Skoro česky',1,?,?,'cs',0,?)""",
                (f"a-{cislo}", f"Díl {cislo}", cislo, jazyky, ted))
        # Seriál B: čeština nikde.
        for cislo in (1, 2):
            conn.execute(
                """INSERT INTO items (id, name, type, library_id, series_id,
                                      series_name, parent_index_number,
                                      index_number, audio_languages,
                                      subtitle_languages, is_missing, synced_at)
                   VALUES (?,?,'Episode','lib-t','ser-b','Vůbec česky',1,?,'en','',0,?)""",
                (f"b-{cislo}", f"Díl {cislo}", cislo, ted))
        # Divák viděl u obou seriálů jen ten díl bez češtiny.
        for klic, polozka in (("t-a", "a-3"), ("t-b", "b-1")):
            conn.execute(
                """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                         started_at, last_seen_at, watched_seconds,
                                         is_active)
                   VALUES (?, 'u-t', ?, 'x', ?, ?, 5400, 0)""",
                (klic, polozka, ted, ted))

    vysledek = langstats.missing_preferred(365, "cs", limit=50)
    podle_odkazu = {r["detail_url"]: r for r in vysledek["rows"]}

    check("/series/ser-b" in podle_odkazu,
          f"seriál bez češtiny u všech dílů je jeden řádek ({list(podle_odkazu)})")
    check(podle_odkazu.get("/series/ser-b", {}).get("is_series") is True,
          "a je označený jako celý seriál")

    check("/series/ser-a" not in podle_odkazu,
          "seriál, který češtinu u většiny dílů má, se jako celek NEhlásí")
    check("/item/a-3" in podle_odkazu,
          f"místo něj se hlásí ten jeden díl ({list(podle_odkazu)})")
    dil = podle_odkazu.get("/item/a-3", {})
    check("S01E03" in (dil.get("label") or ""),
          f"a je u něj poznat, o který díl jde ({dil.get('label')})")
    check("Skoro česky" in (dil.get("label") or ""),
          "i ke kterému seriálu patří")
    check(dil.get("is_series") is False, "díl se netváří jako celý seriál")

    # Film nesmí mít pod názvem tentýž název ještě jednou.
    filmy = [r for r in vysledek["rows"] if not r["is_series"] and not r["episode_name"]]
    check(all(r["label"] != r["episode_name"] for r in filmy),
          "u filmu se název neopakuje")

    print()
    print("--- vybrat jde jen jazyk, který v knihovně je ---")
    response = client.post("/languages/preferred", data={"code": "klingon"},
                           follow_redirects=False)
    check(db.get_setting(langstats.PREFERRED_SETTING) == jiny,
          "vymyšlený jazyk uloženou volbu nepřepsal")

    print()
    print("--- volbu smí měnit jen správce ---")
    # Je to nastavení celé aplikace, ne osobní filtr. Kdyby ji přepnul
    # kdokoliv, přepsal by ji i všem ostatním.
    client.post("/logout")
    client.post("/login", data={"username": "ctenar", "password": "ctenarheslo1"})
    response = client.post("/languages/preferred", data={"code": "cs"},
                           follow_redirects=False)
    check(response.status_code == 403, f"čtenáři zamítnuto ({response.status_code})")
    check(db.get_setting(langstats.PREFERRED_SETTING) == jiny, "volba zůstala")
    check(client.get("/languages").status_code == 200, "ale stránku vidí")


print()
print("--- otisk knihovny se mění jen tehdy, když je co ukázat ---")
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "tajneheslo1"})

    otisk = scanner.library_version()
    check(bool(otisk), f"otisk existuje ({otisk})")
    check(scanner.library_version() == otisk, "opakované čtení dá totéž")

    # Přibude titul -> otisk se musí změnit, jinak by se pás nedávno
    # přidaných na už otevřené stránce nikdy nedoplnil.
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (id, name, type, date_created, is_missing, synced_at)
               VALUES ('cerstvy', 'Čerstvý film', 'Movie', ?, 0, ?)""",
            (db.utcnow(), db.utcnow()))
    check(scanner.library_version() != otisk, "po přibytí titulu je otisk jiný")

    # /health ho musí nabídnout, protože podle něj se stránka rozhoduje.
    zdravi = client.get("/health").json()
    check(zdravi.get("library_version") == scanner.library_version(),
          "/health hlásí stejný otisk")
    check("task_running" in zdravi, "a taky jestli úloha běží (kvůli poskakování)")

    # Výřez se musí dát vyzvednout samostatně a kreslit se stejnou šablonou.
    response = client.get("/partials/recently-added")
    check(response.status_code == 200, f"/partials/recently-added -> {response.status_code}")
    check("Čerstvý film" in response.text, "a obsahuje nově přidaný titul")
    check("poster-card" in response.text, "kreslí se jako pás plakátů")

    # Přehled musí otisk vypsat, jinak nemá prohlížeč co porovnávat.
    dashboard = client.get("/").text
    check('id="recently-added"' in dashboard, "Přehled ten výřez ohraničuje")
    check("data-version=" in dashboard, "a nese si otisk k porovnání")

    # Bez přihlášení se výřez vydat nesmí - jsou v něm názvy z knihovny.
    with TestClient(app) as host:
        check(host.get("/partials/recently-added",
                       follow_redirects=False).status_code == 303,
              "nepřihlášenému se výřez nevydá")


print()
print("--- pomalá odpověď není totéž co nedostupný server ---")
# Tohle byla nahlášená chyba: automatická synchronizace hlásila, že se
# nepodařilo spojit s Jellyfinem, a chyba se hledala v adrese a klíči.
# Ve skutečnosti server odpovídal, jen pomaleji než povolený strop.
check(jellyfin.DEFAULT_TIMEOUT.connect <= 15,
      f"navázání spojení má krátký strop ({jellyfin.DEFAULT_TIMEOUT.connect} s)")
check(jellyfin.DEFAULT_TIMEOUT.read >= 120,
      f"ale čtení odpovědi dlouhý ({jellyfin.DEFAULT_TIMEOUT.read} s)")
check(jellyfin.DEFAULT_TIMEOUT.read > jellyfin.DEFAULT_TIMEOUT.connect,
      "čtení nesmí mít stejný strop jako navázání spojení")
check(jellyfin.QUICK_TIMEOUT.read < jellyfin.DEFAULT_TIMEOUT.read,
      "sběrač a test spojení mají vlastní krátký strop")


class PomaluOdpovida(httpx.AsyncBaseTransport):
    """Server, který existuje, ale odpověď nepošle včas."""

    def __init__(self) -> None:
        self.pokusu = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.pokusu += 1
        raise httpx.ReadTimeout("simulace", request=request)


class Nedostupny(httpx.AsyncBaseTransport):
    """Server, na který se vůbec nedá připojit."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulace", request=request)


async def zeptej_se(transport: httpx.AsyncBaseTransport) -> str:
    client = jellyfin.JellyfinClient("http://server:8096", "klic")
    # Podstrčíme přenos, ať test nepotřebuje síť ani skutečný Jellyfin.
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://server:8096",
                                       transport=transport)
    try:
        await client.system_info()
    except jellyfin.JellyfinError as exc:
        return str(exc)
    finally:
        await client.close()
    return ""


pomaly = PomaluOdpovida()
# Opakování mezi pokusy čeká pár vteřin. V testu ho zkrátíme na nulu -
# ověřujeme počet pokusů a znění hlášky, ne skutečné čekání.
_spanek = asyncio.sleep


async def bez_cekani(_sekund: float) -> None:
    await _spanek(0)


jellyfin.asyncio.sleep = bez_cekani          # type: ignore[assignment]
try:
    hlaska = asyncio.run(zeptej_se(pomaly))
finally:
    jellyfin.asyncio.sleep = _spanek         # type: ignore[assignment]

check("neodpovedel vcas" in hlaska,
      f"pomalý server hlásí vypršení času: {hlaska[:80]}")
check("Nepodarilo se spojit" not in hlaska,
      "a NEhlásí, že se nepodařilo spojit - to posílalo hledat chybu jinam")
check(pomaly.pokusu == jellyfin.TIMEOUT_RETRIES + 1,
      f"než to vzdá, zkusí to znovu ({pomaly.pokusu} pokusů)")

hlaska = asyncio.run(zeptej_se(Nedostupny()))
check("Nepodarilo se spojit" in hlaska,
      f"nedostupný server naopak hlásí spojení: {hlaska[:80]}")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
