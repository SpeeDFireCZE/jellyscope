# -*- coding: utf-8 -*-
r"""Obrázky se po opravě v Jellyfinu přestanou držet; dlouhé seznamy jdou do okna.

**Obrázky.** Ukládaly se do `data/imagecache/<id>-<druh>-<šířka>.img`
a jednou uložený soubor se už nikdy neptal, jestli pořád platí. Když
Jellyfin přiřadil špatný plakát a člověk to v něm opravil, Jellyscope
dál servíroval ten starý - a nešlo s tím nic dělat.

Řeší to `ImageTags`, otisk obrázku od Jellyfinu: ukládá se k položce,
jde do jména souboru v mezipaměti a synchronizace při jeho změně staré
soubory smaže.

**Dlouhé seznamy.** Nad určitý počet se přehrávání i jazykové pruhy
schovají do okna, jinak by každý další posouval statistiky níž.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_obrazky_a_okna.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obrazky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, config, db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

MEZIPAMET = config.load_config().database_path.parent / "imagecache"
MEZIPAMET.mkdir(parents=True, exist_ok=True)


def polozka(item_id: str, otisk: str | None, jmeno: str = "Duna") -> dict:
    """To, co pošle Jellyfin."""
    zaznam = {"Id": item_id, "Name": jmeno, "Type": "Movie",
              "Path": f"/media/{jmeno}.mkv"}
    if otisk:
        zaznam["ImageTags"] = {"Primary": otisk}
    return zaznam


def zapis(item_id: str, otisk: str | None) -> None:
    scanner._write_items(
        [scanner._radek_polozky(polozka(item_id, otisk), "lib", {}, db.utcnow())],
        keep_existing_tech=True)


print("--- otisk obrázku se ukládá k položce ---")
zapis("film-1", "abc123")
check(db.query_value("SELECT image_tag FROM items WHERE id = 'film-1'") == "abc123",
      "otisk je v databázi")


print()
print("--- když se obrázek v Jellyfinu změní, ten starý se zapomene ---")
# Tři soubory v mezipaměti: dvě velikosti plakátu a jedno pozadí.
for jmeno in ("film-1-Primary-400.img", "film-1-Primary-200.img",
              "film-1-Backdrop-600.img"):
    (MEZIPAMET / jmeno).write_bytes(b"stary obrazek")
# A jeden cizí, kterého se to týkat nesmí.
(MEZIPAMET / "film-2-Primary-400.img").write_bytes(b"jiny film")

zapis("film-1", "xyz789")   # v Jellyfinu někdo opravil špatně určený film

zbylo = sorted(p.name for p in MEZIPAMET.glob("*.img"))
check(zbylo == ["film-2-Primary-400.img"],
      f"obrázky té položky jsou pryč, cizí zůstal ({zbylo})")
check(db.query_value("SELECT image_tag FROM items WHERE id = 'film-1'") == "xyz789",
      "a uložil se nový otisk")


print()
print("--- stejný otisk nic nemaže ---")
(MEZIPAMET / "film-1-Primary-400.img").write_bytes(b"novy obrazek")
zapis("film-1", "xyz789")
check((MEZIPAMET / "film-1-Primary-400.img").is_file(),
      "beze změny se mezipaměť nechává být")
# Jinak by každá synchronizace zahodila celou mezipaměť a Jellyfin by
# musel poslat všechny plakáty znovu.


print()
print("--- otisk je součástí adresy obrázku ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    # Jellyfin v testu neodpovídá, takže obrázek nepřijde - jde o to, že
    # se různé otisky nesmí potkat v jednom souboru mezipaměti.
    client.get("/image/film-1?kind=Primary&w=400&tag=xyz789")
    client.get("/image/film-1?kind=Primary&w=400&tag=jinyotisk")
    stranka = client.get("/library").text
    check("Velikost celkem" in stranka or "Total size" in stranka,
          "knihovna ukazuje velikost všech knihoven dohromady")


print()
print("--- dlouhé seznamy jdou do okna ---")
import re  # noqa: E402

from jellyscope import web  # noqa: E402

stropy = web._stropy()
check(stropy["strop_streamu"] >= 1 and stropy["strop_lidi"] >= 1,
      f"stropy se čtou z nastavení ({stropy})")

# Mění se v Nastavení, ne v kódu - a hodnota z formuláře se ořízne do mezí,
# ať tam někdo napíše cokoliv.
db.set_setting("ui_max_streams", "3")
check(web._stropy()["strop_streamu"] == 3, "změna v nastavení se projeví")
db.set_setting("ui_max_streams", "999")
check(web._stropy()["strop_streamu"] == web.STROP_MAX,
      f"nesmysl se ořízne na strop ({web._stropy()['strop_streamu']})")
db.set_setting("ui_max_streams", "nesmysl")
check(web._stropy()["strop_streamu"] == 10, "a text spadne na výchozí hodnotu")
db.set_setting("ui_max_streams", "2")

# Mají vlastní sekci v Nastavení - poznámka pro příště, až voleb vzhledu
# přibude: patří tam, ne do Obecného.
sekce = dict((k, n) for k, n, _a in web.SETTINGS_SECTIONS)
check(sekce.get("interface") == "Rozhraní", "vzhled má vlastní sekci nastavení")
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/settings?section=interface").text
    check('name="ui_max_streams"' in stranka and 'name="ui_max_viewers"' in stranka,
          "a v ní jsou obě pole")
    odpoved = client.post("/settings/interface",
                          data={"ui_max_streams": "7", "ui_max_viewers": "9"},
                          follow_redirects=False)
    check(odpoved.headers.get("location") == "/settings?section=interface",
          "uložení vrací zpátky na tu samou sekci")
    check(web._stropy() == {"strop_streamu": 7, "strop_lidi": 9},
          f"a hodnoty z formuláře se uložily ({web._stropy()})")
db.set_setting("ui_max_streams", "2")

print()
print("--- přibližování mapy si vybírá uživatel ---")
# Kolečko nad mapou zastaví rolování stránky. Komu to vadí, přepne na
# klikání - a pak se na kolečko vůbec neposlouchá.
from jellyscope import charts  # noqa: E402

check(web._zoom_rezim() == "click", "výchozí je klikání, ne kolečko")
check('data-zoom="click"' in charts.mapa_sveta([
    {"lat": 50.0, "lon": 14.4, "sekund": 60, "plays": 1, "lidi": 1}]),
    "režim jde do mapy jako atribut")

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "wheel"}, follow_redirects=False)
    check(web._zoom_rezim() == "wheel", "volba se uloží")
    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "vymysl"}, follow_redirects=False)
    check(web._zoom_rezim() == "click", "nesmysl spadne zpátky na klikání")

zaklad = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
check('mapa.dataset.zoom === "wheel"' in zaklad,
      "posluchač kolečka je podmíněný režimem")
check('data-mapa="blize"' in (PROJECT / "jellyscope" / "templates"
                              / "network.html").read_text(encoding="utf-8"),
      "nad mapou jsou tlačítka pro přiblížení")
db.set_setting("ui_max_streams", "2")

sablona = (PROJECT / "jellyscope" / "templates" / "_now_playing.html").read_text(
    encoding="utf-8")
check('data-okno="okno-streamy"' in sablona, "tlačítko otevírá okno se streamy")
check('data-filtr-okna="okno-streamy"' in sablona, "a v okně je filtr uživatelů")
check(sablona.count("{{ stream(row) }}") == 2,
      "stream se kreslí jedním makrem pro kartu i okno")

sablona = (PROJECT / "jellyscope" / "templates" / "languages.html").read_text(
    encoding="utf-8")
check('data-okno="okno-jazyky"' in sablona, "totéž u jazyků")
check('data-filtr-okna="okno-jazyky"' in sablona, "i s filtrem")

# Filtr smí platit jen v okně - proto se filtruje v prohlížeči a nikam
# se neukládá. Kdyby šel do adresy, přežil by zavření okna.
zaklad = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
check("data-filtr-okna" in zaklad, "obsluha filtru je v základní šabloně")
check("bylOtevreny" in zaklad,
      "a otevřené okno přežije automatickou obnovu karty")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
