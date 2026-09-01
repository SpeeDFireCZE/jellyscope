# -*- coding: utf-8 -*-
r"""Poslední záchrana: jazyk podle názvu souboru.

Když jazyk nezná soubor (ffprobe) ani knihovna (Jellyfin), zbývá to, co
si do názvu napsal člověk - "Duna.2021.CZ.SK.EN.1080p.mkv".

Celé to stojí a padá s přesností, proto se hledá **celý úsek mezi
oddělovači**, ne výskyt písmen: "Czechacek" ani "enigma" tak neprojdou.
Dvoupísmenné značky se berou jen velkými (malé "de", "es", "ja" jsou
běžná slova) a celá jména jazyků až za rokem nebo číslem dílu - jinak by
"The Italian Job" byl italsky a "Polish Wedding" polsky.

Název ale říká jen to, KTERÉ jazyky v souboru jsou, ne která stopa je
která. Rozdělují se proto po řadě a označí jako odhad; množina jazyků -
a tím i statistiky - sedí, pořadí je odhad. A když počty nesedí nebo
známá stopa říká něco jiného, nedoplní se nic.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_jazyk_z_nazvu.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "nazvy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, languages, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

print("--- co se má v názvu najít ---")
NAJDE = [
    ("Duna.2021.CZ.SK.EN.1080p.BluRay.x264.mkv", ["cs", "sk", "en"], []),
    ("Uncharted (2022) CZ dabing.mkv", ["cs"], []),
    ("Serial.S01E03.CZdab.720p.mkv", ["cs"], []),
    ("Matrix.1999.ENG.CZ.tit.mkv", ["en"], ["cs"]),
    ("Parasite.2019.KOR.EN.sub.mkv", ["ko"], ["en"]),
    ("Amelie.2001.FRA.CZ.mkv", ["fr", "cs"], []),
    ("Show.S02E01.1080p.WEB-DL.GER.mkv", ["de"], []),
    ("Nejaky.film.2020.[CZ][SK].mkv", ["cs", "sk"], []),
    ("/mnt/filmy/Film.2018.1080p.multi.CZ.SK.mkv", ["cs", "sk"], []),
    # Tvary z opravdové knihovny - všechny sem přišly jako hlášení, že
    # se jazyk nepřiřadil. Malá písmena za rokem, značka slepená s rokem
    # i rok schovaný v závorce.
    ("Nejaky film (2004) HD cz.avi", ["cs"], []),
    ("Nejaky film (2004) HD en.avi", ["en"], []),
    ("Nejaky film 1080p - 5.1 CZ.mkv", ["cs"], []),
    ("Nejaky film (1964)(CZ)[TvRip].mp4", ["cs"], []),
    ("Film.2022.CZ.SK.EN.WebRip.1080p.HEVC.C4U.mkv", ["cs", "sk", "en"], []),
    ("Film.2002.DVDRip.XviD.AC3-2.0.CZ.avi", ["cs"], []),
    ("Nejaky film-2003CZ.mp4", ["cs"], []),
    ("Film.1080p.BDRip.x264.CZ.dabing.mkv", ["cs"], []),
    ("Film.2019.DVDRip.eng.avi", ["en"], []),
    ("Serial.S01E02.HDTV.sk.mp4", ["sk"], []),
]
for nazev, zvuk, titulky in NAJDE:
    v = languages.z_nazvu(nazev)
    check(v["zvuk"] == zvuk and v["titulky"] == titulky,
          f"{nazev[:44]:46} {v['zvuk']} / {v['titulky']}")

print()
print("--- a co se najít NESMÍ ---")
# Tohle je jádro celé věci. Kdyby se hledal podřetězec, propadlo by tudy
# všechno: "Czechacek" má v sobě "cze", "enigma" má "en".
NENAJDE = [
    "Czechacek.2020.1080p.mkv",              # obsahuje "cze"
    "Enigma.2001.1080p.BluRay.mkv",          # obsahuje "en"
    "IT.2017.1080p.BluRay.x264.mkv",         # film IT, ne italština
    "No.Time.To.Die.2021.2160p.mkv",         # "No" není norština
    "El.Camino.A.Breaking.Bad.Movie.2019.mkv",   # "El" není řečtina
    "Ja.Olga.Hepnarova.2016.1080p.mkv",      # "Ja" není japonština
    "Casa.de.Papel.S01E01.1080p.mkv",        # "de" malými = španělské slovo
    "The.Italian.Job.2003.1080p.mkv",        # jméno jazyka v názvu filmu
    "The.French.Connection.1971.1080p.mkv",
    "Polish.Wedding.1998.720p.mkv",
    "Russian.Doll.S01E01.1080p.mkv",
    "The Italian Job.mkv",                   # bez roku: slova neuznáváme
    "Sk8er.Boi.2002.mkv",
    "Frozen.2013.1080p.DUAL.mkv",            # "DUAL" neříká které jazyky
    "Denis.Villeneuve.dokument.2020.mkv",
    # Skupina vydavatele na konci nazvu. "C4U" se nesmi rozpadnout na
    # "C" + "4U" ani chytit jako znacka.
    "Film.2019.1080p.x264.C4U.mkv",
    # Male "cz" PRED rokem je soucast nazvu, ne znacka.
    "cz.film.o.nicem.2019.1080p.mkv",
]
for nazev in NENAJDE:
    v = languages.z_nazvu(nazev)
    check(not v["zvuk"] and not v["titulky"], f"{nazev[:44]:46} {v['zvuk']} / {v['titulky']}")

print()
print("--- rozdělení na stopy ---")


def polozka(item_id: str, cesta: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, is_missing, synced_at, path)"
            " VALUES (?, ?, 'Movie', 0, '2026-01-01 00:00:00', ?)",
            (item_id, item_id, cesta))


def stopy(item_id: str, *jazyky: str, druh: str = "Audio") -> None:
    scanner.save_streams(item_id, [
        {"stream_index": i + 1, "type": druh, "language": j, "codec": "ac3"}
        for i, j in enumerate(jazyky)])


def jazyky_stop(item_id: str) -> list[tuple]:
    return [(r["language"], r["language_source"]) for r in db.query_all(
        "SELECT language, language_source FROM item_streams WHERE item_id = ?"
        " ORDER BY stream_index", (item_id,))]


# Případ ze screenshotu: tři stopy bez jazyka, název slibuje tři jazyky.
polozka("f1", "/data/Duna.2021.CZ.SK.EN.1080p.mkv")
stopy("f1", "und", "und", "und")
check(scanner.doplnit_jazyky_z_nazvu(["f1"]) == 1, "položce se z názvu pomohlo")
check(jazyky_stop("f1") == [("cs", "nazev"), ("sk", "nazev"), ("en", "nazev")],
      f"po řadě a označené jako odhad ({jazyky_stop('f1')})")
souhrn = db.query_one("SELECT audio_languages FROM items WHERE id = 'f1'")
check(souhrn["audio_languages"] == "cs,en,sk",
      f"souhrn pro statistiky sedí ({souhrn['audio_languages']})")

print()
print("--- název jmenuje míň jazyků než kolik je stop ---")
# Nejčastější případ ze skutečných knihoven: v souboru je dabing
# i původní zvuk, ale v názvu je jen "CZ". Která z těch dvou stop je
# česká, se hádat nedá - ale že v souboru čeština JE, víme jistě.
polozka("f2", "/data/Film (2004) HD cz.avi")
stopy("f2", "und", "und")
check(scanner.doplnit_jazyky_z_nazvu(["f2"]) > 0, "něco se z názvu vytěžilo")
check(jazyky_stop("f2") == [("und", None), ("und", None)],
      f"ke stopám se nic nepřiřadilo ({jazyky_stop('f2')})")
souhrn = db.query_one("SELECT audio_languages, audio_from_name FROM items WHERE id = 'f2'")
check(souhrn["audio_from_name"] == "cs", f"ale čeština je zapsaná ({souhrn['audio_from_name']})")
check(souhrn["audio_languages"] == "cs,und",
      f"a statistika ji uvidí vedle neznámé stopy ({souhrn['audio_languages']})")

print()
print("--- co už víme, se z názvu odečte ---")
# Stopy [angličtina, neznámá] a název "CZ.EN": angličtinu známe, takže
# název přidává jedinou novinku - a je jediná neznámá stopa, kam patří.
polozka("f3", "/data/Film.2020.CZ.EN.1080p.mkv")
stopy("f3", "en", "und")
check(scanner.doplnit_jazyky_z_nazvu(["f3"]) == 1, "doplní se jedna stopa")
check(jazyky_stop("f3") == [("en", None), ("und", None)][:1] + [("cs", "nazev")],
      f"neznámá stopa dostala češtinu ({jazyky_stop('f3')})")

print()
print("--- čtyři stopy, tři značky: ke stopám nic ---")
polozka("f7", "/data/Film.2020.CZ.SK.EN.1080p.mkv")
stopy("f7", "und", "und", "und", "und")
scanner.doplnit_jazyky_z_nazvu(["f7"])
check(jazyky_stop("f7") == [("und", None)] * 4, "stopy zůstaly beze změny")
souhrn = db.query_one("SELECT audio_languages FROM items WHERE id = 'f7'")
check(souhrn["audio_languages"] == "cs,en,sk,und",
      f"ale všechny tři jazyky statistika zná ({souhrn['audio_languages']})")

print()
print("--- známá stopa souhlasí: zbytek se doplní ---")
polozka("f4", "/data/Film.2020.CZ.EN.1080p.mkv")
stopy("f4", "cs", "und")
check(scanner.doplnit_jazyky_z_nazvu(["f4"]) == 1, "doplní se jen ta neznámá")
check(jazyky_stop("f4") == [("cs", None), ("en", "nazev")],
      f"a to, co přečetl ffprobe, zůstává bez značky ({jazyky_stop('f4')})")

print()
print("--- titulky se nepletou se zvukem ---")
polozka("f5", "/data/Matrix.1999.ENG.CZ.tit.mkv")
scanner.save_streams("f5", [
    {"stream_index": 1, "type": "Audio", "language": "und", "codec": "ac3"},
    {"stream_index": 2, "type": "Subtitle", "language": "und", "codec": "subrip"},
])
check(scanner.doplnit_jazyky_z_nazvu(["f5"]) == 1, "obojí dostane své")
check(jazyky_stop("f5") == [("en", "nazev"), ("cs", "nazev")],
      f"zvuk anglicky, titulky česky ({jazyky_stop('f5')})")

print()
print("--- bez cesty k souboru se nehádá ---")
polozka("f6", "")
stopy("f6", "und")
check(scanner.doplnit_jazyky_z_nazvu(["f6"]) == 0, "nic k přečtení, nic k doplnění")

print()
print("--- na stránce je poznat, že jde o odhad ---")
# Tohle je půlka celé funkce: údaj z názvu není údaj ze souboru a nesmí
# tak vypadat. U stopy proto stojí "odhad z názvu"; u toho, co přečetl
# ffprobe, nestojí nic.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, web  # noqa: E402

accounts.create("divak", "dlouheheslo", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "divak", "password": "dlouheheslo"},
            follow_redirects=False)

stranka = client.get("/item/f1").text
check("odhad z názvu" in stranka, "u odhadnuté stopy je poznámka")
check(stranka.count("Jazyk nezná soubor ani Jellyfin") == 3,
      f"u všech tří stop ({stranka.count('Jazyk nezná soubor ani Jellyfin')})")

# Položka, u které se nedoplnilo nic, zůstává bez poznámky.
stranka7 = client.get("/item/f7").text
check("odhad z názvu" not in stranka7,
      "kde se ke stopám nic nepřiřadilo, poznámka u stop není")
# Zato v hlavičce karty musí být vidět, co název slíbil - jinak by
# statistika znala jazyk, který na stránce nikde není.
check("podle názvu souboru" in stranka7, "ale karta říká, co slíbil název")
check("Čeština" in stranka7 and "Slovenština" in stranka7,
      "a vyjmenuje je")

# A stopa přečtená z souboru poznámku nemá ani na položce, kde se jinde
# odhadovalo - jinak by značka ztratila smysl.
smisena = client.get("/item/f4").text
check(smisena.count("Jazyk nezná soubor ani Jellyfin") == 1,
      f"jen u té odhadnuté, ne u obou ({smisena.count('Jazyk nezná soubor ani Jellyfin')})")

print()
print("--- podoby z opravdové knihovny ---")
# Deset názvů z ostré knihovny. Nejsou to výmysly: liší se oddělovačem
# před značkou (tečka, pomlčka, mezera, závorka, podtržítko, vlnovka),
# velikostí písmen i tím, co za značkou následuje.
for nazev, ocekavane in (
    ("Vratne lahve 2022.CZ.SK.EN.WebRip.1080p.HEVC.C4U.mkv", ["cs", "sk", "en"]),
    ("Vratne lahve_ (2004) HD cz.avi", ["cs"]),
    ("Vratne lahve (Nejlepší Kvalita) CZ.avi", ["cs"]),
    ("Vratne lahve 1080p - 5.1 CZ.mkv", ["cs"]),
    ("Vratne lahve (1964)(CZ)[TvRip].mp4", ["cs"]),
    ("Vratne lahve ~ (2015) HD cz.avi", ["cs"]),
    ("Vratne lahve.2002.DVDRip.XviD.AC3-2.0.CZ.avi", ["cs"]),
    ("Vratne lahve-2003CZ.mp4", ["cs"]),
    ("Vratne lahve-1990-1080p-cz.mp4", ["cs"]),
    ("Vratne lahve.2015.THEATRiCAL.1080p.BDRip.x264.CZ.dabing.mkv", ["cs"]),
):
    nalezene = languages.z_nazvu(nazev)["zvuk"]
    check(nalezene == ocekavane,
          f"{nazev[13:]:<48} -> {nalezene or 'nic'}")

print()
print("--- ke kroku 'z názvu' se soubory musí vůbec dostat ---")
# Tohle byla ta chyba, kvůli které to v ostrém provozu nefungovalo:
# krok výš označí stopy, se kterými Jellyfin nepomohl, jako "neznamy" -
# a hledání kandidátů se ptalo jen na stopy BEZ označení. Seznam byl
# proto vždycky prázdný a název souboru se nepoužil vůbec; fungovalo to
# jen ve chvíli, kdy Jellyfin neodpověděl.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name) VALUES ('kn1','Filmy')"
                 " ON CONFLICT(id) DO NOTHING")
    # a) stopa, na kterou se Jellyfina už ptalo a nevěděl
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, path)"
        " VALUES ('kand1','Film','Movie','kn1',0,"
        "'/media/Film.2002.DVDRip.XviD.AC3-2.0.CZ.avi')")
    conn.execute("INSERT INTO item_streams (item_id, stream_index, type, language,"
                 " language_source) VALUES ('kand1', 1, 'Audio', 'und', 'neznamy')")
    # b) položka bez jediné stopy - analýza souboru se nikdy nepovedla
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, path)"
        " VALUES ('kand2','Film2','Movie','kn1',0,'/media/Film2-1990-1080p-cz.mp4')")
    # c) co jazyk zná, se do seznamu plést nesmí
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, path,"
        " audio_languages) VALUES ('kand3','Film3','Movie','kn1',0,"
        "'/media/Film3.CZ.mkv','cs')")
    conn.commit()

kandidati = scanner.kandidati_na_jazyk_z_nazvu()
check("kand1" in kandidati, "stopa označená 'neznamy' je kandidát")
check("kand2" in kandidati, "položka bez stop taky")
check("kand3" not in kandidati, "a co jazyk zná, se nepřidává")

check(scanner.doplnit_jazyky_z_nazvu(["kand1", "kand2"]) == 2, "oběma se jazyk doplní")
prvni = db.query_one("SELECT language, language_source FROM item_streams"
                     " WHERE item_id = 'kand1'")
check(prvni["language"] == "cs" and prvni["language_source"] == "nazev",
      "u stopy je čeština z názvu")
druhy = db.query_one("SELECT audio_languages, audio_from_name FROM items"
                     " WHERE id = 'kand2'")
check((druhy["audio_languages"] or druhy["audio_from_name"]) == "cs",
      "u položky bez stop je čeština aspoň v souhrnu")

# Podruhé už není co dělat - jinak by se to počítalo dokola při každé
# analýze knihovny.
check("kand2" not in scanner.kandidati_na_jazyk_z_nazvu(),
      "hotová položka se podruhé nenabízí")

# Rozsah: obnova jedné položky nesmí projít celou knihovnu.
check(scanner.kandidati_na_jazyk_z_nazvu(item_ids=["kand1"]) == ["kand1"]
      or scanner.kandidati_na_jazyk_z_nazvu(item_ids=["kand1"]) == [],
      "omezení na položku se dodrží")
check("kand1" not in scanner.kandidati_na_jazyk_z_nazvu(library_id="jina"),
      "a omezení na knihovnu taky")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
