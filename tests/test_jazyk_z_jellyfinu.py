# -*- coding: utf-8 -*-
r"""Co soubor o jazyku neříká, doplní Jellyfin - ale jen to.

ffprobe čte jazyk stopy z metadat souboru, a v mnoha souborech tam prostě
žádný není. Jellyfin ho přitom často zná: dopočítá si ho z názvu souboru,
ze složky nebo z toho, co si o titulu vede sám. Detail položky pak hlásil
u tří zvukových stop „Neuvedeno", zatímco Jellyfin u téhož souboru
nabízel češtinu a dvakrát slovenštinu.

Oba nástroje přitom měly pravdu - jen se každý díval jinam.

Doplňují se proto **jen mezery**. Co ffprobe přečetl, zůstává; a když
počty stop nesedí, nedoplňuje se nic: špatně určený jazyk je horší než
„neuvedeno", protože u „neuvedeno" je aspoň vidět, že se neví.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_jazyk_z_jellyfinu.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "jazyky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def polozka(item_id: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, is_missing, synced_at)"
            " VALUES (?, ?, 'Movie', 0, '2026-01-01 00:00:00')",
            (item_id, f"Film {item_id}"))


def stopa(index: int, druh: str, jazyk: str, **dalsi) -> dict:
    """Stopa tak, jak ji ukládá ffprobe."""
    return {"stream_index": index, "type": druh, "language": jazyk,
            "codec": "ac3", "channels": 2, **dalsi}


def jellyfin_polozka(item_id: str, stopy: list[dict]) -> dict:
    """Odpověď Jellyfinu - stopy zabalené v MediaSources."""
    return {"Id": item_id, "MediaSources": [{"MediaStreams": stopy}]}


def jf(index: int, druh: str, jazyk: str | None, **dalsi) -> dict:
    return {"Index": index, "Type": druh, "Language": jazyk,
            "Codec": "ac3", "Channels": 2, **dalsi}


def jazyky(item_id: str) -> list[tuple]:
    return [(r["stream_index"], r["language"], r["language_source"])
            for r in db.query_all(
                "SELECT stream_index, language, language_source FROM item_streams"
                " WHERE item_id = ? ORDER BY stream_index", (item_id,))]


print("--- soubor mlčí, knihovna ví ---")
# Přesně případ ze screenshotu: tři zvukové stopy bez jazyka, Jellyfin
# u nich hlásí češtinu a dvakrát slovenštinu.
polozka("film1")
scanner.save_streams("film1", [
    stopa(0, "Video", "und"),
    stopa(1, "Audio", "und"),
    stopa(2, "Audio", "und"),
    stopa(3, "Audio", "und"),
])
doplneno = scanner._doplnit_jazyky_polozky(jellyfin_polozka("film1", [
    jf(0, "Video", None),
    jf(1, "Audio", "ces"),
    jf(2, "Audio", "slo"),
    jf(3, "Audio", "slo"),
]))
check(doplneno == 3, f"doplnily se tři stopy ({doplneno})")
check(jazyky("film1") == [(0, "und", None), (1, "cs", "jellyfin"),
                          (2, "sk", "jellyfin"), (3, "sk", "jellyfin")],
      f"a je u nich poznat, odkud to je ({jazyky('film1')})")
# Obraz zůstal "neuvedeno" schválně - u videa jazyk nikdo nečeká.

souhrn = db.query_one("SELECT audio_languages, default_audio_language FROM items"
                      " WHERE id = 'film1'")
check(souhrn["audio_languages"] == "cs,sk",
      f"souhrn jazyků se přepočítal ({souhrn['audio_languages']})")
check(souhrn["default_audio_language"] == "cs",
      f"i výchozí jazyk ({souhrn['default_audio_language']})")

print()
print("--- co soubor uvádí, to se nepřepisuje ---")
# Tady je zdrojem pravdy soubor. Kdyby Jellyfin přebíjel i vyplněné
# stopy, přestala by analýza souborů dávat smysl.
polozka("film2")
scanner.save_streams("film2", [stopa(1, "Audio", "cs"), stopa(2, "Audio", "und")])
doplneno = scanner._doplnit_jazyky_polozky(jellyfin_polozka("film2", [
    jf(1, "Audio", "eng"),
    jf(2, "Audio", "eng"),
]))
check(doplneno == 1, f"doplní se jen ta prázdná ({doplneno})")
check(jazyky("film2") == [(1, "cs", None), (2, "en", "jellyfin")],
      f"první stopa zůstala česká ({jazyky('film2')})")

print()
print("--- když počty nesedí, radši nic ---")
polozka("film3")
scanner.save_streams("film3", [stopa(1, "Audio", "und"), stopa(2, "Audio", "und")])
doplneno = scanner._doplnit_jazyky_polozky(jellyfin_polozka("film3", [
    jf(1, "Audio", "ces"),
]))
check(doplneno == 0, f"nedoplní se nic ({doplneno})")
check([(i, j) for i, j, _ in jazyky("film3")] == [(1, "und"), (2, "und")],
      "jazyky zůstaly beze změny")

print()
print("--- externí titulky se do párování nepočítají ---")
# Jellyfin čísluje i titulky ležící vedle souboru. V souboru nejsou,
# takže by posunuly pořadí a jazyky by sedly na špatné stopy.
polozka("film4")
scanner.save_streams("film4", [stopa(1, "Subtitle", "und", codec="subrip")])
doplneno = scanner._doplnit_jazyky_polozky(jellyfin_polozka("film4", [
    jf(1, "Subtitle", "ces", Codec="subrip"),
    jf(2, "Subtitle", "eng", Codec="srt", IsExternal=True),
]))
check(doplneno == 1, f"vnitřní titulky se spárují ({doplneno})")
check(jazyky("film4") == [(1, "cs", "jellyfin")], f"a dostanou češtinu ({jazyky('film4')})")

print()
print("--- když neví ani Jellyfin, nevymýšlíme si ---")
polozka("film5")
scanner.save_streams("film5", [stopa(1, "Audio", "und")])
doplneno = scanner._doplnit_jazyky_polozky(jellyfin_polozka("film5", [jf(1, "Audio", "und")]))
check(doplneno == 0, f"zůstane neuvedeno ({doplneno})")
check(jazyky("film5") == [(1, "und", "neznamy")],
      f"ale je poznamenané, že jsme se ptali ({jazyky('film5')})")

print()
print("--- na totéž se podruhé neptáme ---")
# Odpověď by byla pokaždé stejná. Podle značky se pozná, že už se ptalo -
# a doptávání tak neroste s každou další analýzou.
nezeptane = db.query_all(
    "SELECT DISTINCT item_id FROM item_streams WHERE language = 'und'"
    " AND language_source IS NULL AND type IN ('Audio', 'Subtitle')")
check("film5" not in [r["item_id"] for r in nezeptane],
      "film5 už mezi nezeptanými není")
check("film3" not in [r["item_id"] for r in nezeptane],
      "ani film3, u kterého nesedly počty")

# Po novém změření se stopy přepíšou i se značkou, takže po opravě
# metadat v Jellyfinu se doptáme znovu.
scanner.save_streams("film5", [stopa(1, "Audio", "und")])
check(jazyky("film5") == [(1, "und", None)], "nové změření značku smaže")

print()
print("--- koho se vůbec doptávat ---")
check(scanner._chybi_jazyk([stopa(1, "Audio", "und")]) is True, "zvuk bez jazyka ano")
check(scanner._chybi_jazyk([stopa(1, "Subtitle", "und")]) is True, "titulky taky")
check(scanner._chybi_jazyk([stopa(0, "Video", "und")]) is False, "obraz ne")
check(scanner._chybi_jazyk([stopa(1, "Audio", "cs")]) is False, "vyplněný zvuk ne")

print()
print("--- dávka: jeden dotaz na padesát položek ---")
# Doptáváme se až po analýze, protože takhle jde padesát položek jedním
# dotazem. Uprostřed smyčky by to byl jeden dotaz na soubor.
import asyncio  # noqa: E402


class FalesnyKlient:
    """Jellyfin, který neexistuje - odpovídá z připraveného slovníku."""

    dotazy: list[list[str]] = []

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def items_by_ids(self, ids):
        FalesnyKlient.dotazy.append(list(ids))
        return [jellyfin_polozka("film6", [jf(1, "Audio", "ces")]),
                jellyfin_polozka("film7", [jf(1, "Audio", "ger")])]


polozka("film6")
polozka("film7")
scanner.save_streams("film6", [stopa(1, "Audio", "und")])
scanner.save_streams("film7", [stopa(1, "Audio", "und")])

puvodni = scanner.JellyfinClient
scanner.JellyfinClient = FalesnyKlient
try:
    doplneno = asyncio.run(scanner.doplnit_jazyky_z_jellyfinu(["film6", "film7"]))
finally:
    scanner.JellyfinClient = puvodni

check(doplneno == 2, f"doplnily se obě položky ({doplneno})")
check(FalesnyKlient.dotazy == [["film6", "film7"]],
      f"a stačil na to jeden dotaz ({FalesnyKlient.dotazy})")
check(jazyky("film6") == [(1, "cs", "jellyfin")], "film6 má češtinu")
check(jazyky("film7") == [(1, "de", "jellyfin")], "film7 němčinu")
check(asyncio.run(scanner.doplnit_jazyky_z_jellyfinu([])) == 0,
      "bez položek se Jellyfinu nevoláme vůbec")

print()
print("--- na stránce je vidět, že údaj není ze souboru ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, web  # noqa: E402

accounts.create("divak", "dlouheheslo", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "divak", "password": "dlouheheslo"},
            follow_redirects=False)
stranka = client.get("/item/film1").text
check("z Jellyfinu" in stranka, "u doplněné stopy je poznámka o původu")
# Dvakrát na stopu: jednou v popisku, jednou v bublině při najetí myší.
pocet = stranka.count('Soubor jazyk neuvádí')
check(pocet == 3, f"u všech tří stop ({pocet})")
nedoplneny = client.get("/item/film5").text
check("z Jellyfinu" not in nedoplneny, "u nedoplněné stopy poznámka není")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
