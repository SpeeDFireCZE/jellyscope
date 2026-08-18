# -*- coding: utf-8 -*-
"""Údaje o právě běžícím streamu a skupina rozlišení.

Dvě chyby, které spolu nesouvisí, ale obě způsobily, že se na Přehledu
zobrazovalo méně, než Jellyfin ví:

  1. **Stopy relace se hledaly na špatném místě.** `/Items` vrací stopy
     zabalené v `MediaSources`, ale `/Sessions` je posílá rovnou v položce
     jako `MediaStreams`. Kód hledal jen v prvním, takže u přímého
     přehrávání chyběl jazyk, kodek i bitrate. U přepočtu to vidět nebylo,
     protože tam se údaje berou z `TranscodingInfo`.

  2. **Rozlišení se určovalo podle výšky.** Širokoúhlý 4K film má rozměry
     3840×1608 - výška 1608 je pod prahem 1080p, takže 4K film vycházel
     jako „1080p". Rozhodovat musí šířka.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sessions.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import collector, db, formatting, jellyfin, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


print("--- skupina rozlišení se řídí šířkou ---")
res = formatting.resolution_human
# Tohle je ten případ z praxe: HEVC 3840x1608, tedy 4K v poměru 2.40:1.
check(res(1608, 3840) == "4K", f"3840x1608 je 4K (bylo {res(1608, 3840)})")
check(res(2160, 3840) == "4K", "3840x2160 je 4K")
check(res(1080, 1920) == "1080p", "1920x1080 je 1080p")
check(res(800, 1920) == "1080p", f"1920x800 je 1080p (bylo {res(800, 1920)})")
check(res(720, 1280) == "720p", "1280x720 je 720p")
check(res(576, 720) == "576p", "720x576 je 576p")
# Bez šířky se musí dát poznat aspoň něco - starší záznamy ji nemají.
check(res(2160) == "4K", "bez šířky rozhodne výška")
check(res(1080) == "1080p", "bez šířky: 1080")
check(res(None, None) == "-", "bez rozměrů pomlčka")
check(res(None, 3840) == "4K", "jen šířka taky stačí")


print()
print("--- stopy se najdou, ať je Jellyfin pošle kdekoli ---")
V_SOURCES = {"MediaSources": [{"Bitrate": 25_000_000, "MediaStreams": [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 3840, "Height": 1608},
    {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "cze", "IsDefault": True},
]}]}
# Přesně takhle to vypadá v odpovědi /Sessions - bez MediaSources.
V_POLOZCE = {"MediaStreams": [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 3840, "Height": 1608,
     "BitRate": 25_000_000},
    {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "cze", "IsDefault": True},
]}

for popis, item in (("z MediaSources", V_SOURCES), ("z MediaStreams", V_POLOZCE)):
    streams = jellyfin.media_streams(item)
    check(len(streams) == 2, f"{popis}: našly se obě stopy")
    check(jellyfin.video_dimensions(item) == (3840, 1608), f"{popis}: rozměry")
    check(jellyfin.source_bitrate(item) == 25_000_000, f"{popis}: bitrate")
    jazyky = jellyfin.selected_languages({"PlayState": {"AudioStreamIndex": 1}}, item)
    check(jazyky["audio_language"] == "cs", f"{popis}: vybraný jazyk")

check(jellyfin.media_streams({}) == [], "položka bez stop nespadne")
check(jellyfin.video_dimensions({}) == (None, None), "a rozměry jsou prázdné")


print()
print("--- karta relace zná kodek, bitrate i rozměry ---")
relace = {
    "Id": "s1", "UserId": "u1", "UserName": "Karel",
    "Client": "Jellyfin for Android", "DeviceName": "Xiaomi 17",
    "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": True, "AudioStreamIndex": 1},
    "NowPlayingItem": dict(V_POLOZCE, Id="ep-1", Name="1. díl", Type="Episode",
                           SeriesName="Kancelář"),
}
popis = collector._describe_stream(relace, relace["NowPlayingItem"])
check(popis["video_codec"] == "hevc", f"kodek: {popis['video_codec']}")
check(popis["bitrate"] == 25_000_000, f"bitrate: {popis['bitrate']}")
check(popis["video_width"] == 3840 and popis["video_height"] == 1608,
      f"rozměry: {popis['video_width']}x{popis['video_height']}")


print()
print("--- rozlišení funguje i u položky, která není v knihovně ---")
# Přesně případ z praxe: epizoda, kterou jsme ještě nesynchronizovali.
# Dřív se rozměry braly jen z tabulky `items`, takže tu badge chyběla.
db.init_db()
collector._store_sessions([relace], max_gap_seconds=60)

radky = stats.active_sessions()
check(len(radky) == 1, f"relace se zapsala ({len(radky)})")
radek = radky[0]
check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'ep-1'") == 0,
      "položka opravdu v knihovně není")
check(radek["width"] == 3840 and radek["height"] == 1608,
      f"přesto známe rozměry: {radek['width']}x{radek['height']}")
check(formatting.resolution_human(radek["height"], radek["width"]) == "4K",
      "a vyjde z nich 4K")
check(radek["current_audio_language"] == "cs", "i jazyk zvukové stopy")
# Do statistik se jazyk zapíše až po MIN_LANGUAGE_SECONDS - viz níž.
check(radek["audio_language"] is None, "ten se ale do statistik zatím nepočítá")
check(radek["video_codec"] == "hevc", "i kodek")
check(radek["is_paused"] == 1, "a že je pozastaveno")


print()
print("--- ukazatel postupu funguje i bez knihovny ---")
# Délku pořadu hlásí sama relace. Dřív se brala jen z tabulky `items`,
# takže u epizody, kterou jsme ještě nesynchronizovali, ukazatel chyběl
# a místo něj stálo jen "Běží 3 min".
TIK = 10_000_000
prehravani = {
    "Id": "postup", "UserId": "u8", "UserName": "Karel",
    "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False,
                  "AudioStreamIndex": 1, "PositionTicks": 77 * TIK},
    "NowPlayingItem": {"Id": "ep-bez-knihovny", "Name": "1. díl",
                       "Type": "Episode", "SeriesName": "Kancelář",
                       "RunTimeTicks": 1502 * TIK,
                       "MediaStreams": V_POLOZCE["MediaStreams"]},
}
collector._store_sessions([prehravani], max_gap_seconds=60)
r = next(x for x in stats.active_sessions() if x["item_id"] == "ep-bez-knihovny")

check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'ep-bez-knihovny'") == 0,
      "položka v knihovně opravdu není")
check(r["progress"] is not None, "postup se přesto spočítal")
check(round(r["position_seconds"]) == 77 and round(r["runtime_seconds"]) == 1502,
      f"pozice i délka sedí ({r['position_seconds']}/{r['runtime_seconds']})")
check(round(r["remaining_seconds"]) == 1425, "a zbývající čas taky")

# Čas se ukazuje jako v přehrávači, ne "1 h 17 min" - aby šel porovnat
# s tím, co má divák na obrazovce.
check(formatting.timecode(r["position_seconds"]) == "1:17",
      f"pozice jako v přehrávači ({formatting.timecode(r['position_seconds'])})")
check(formatting.timecode(r["runtime_seconds"]) == "25:02", "délka taky")
check(formatting.timecode(3661) == "1:01:01", "přes hodinu se hodiny doplní")
check(formatting.timecode(0) == "0:00" and formatting.timecode(None) == "0:00",
      "nula i chybějící hodnota jsou 0:00")

prehravani["PlayState"]["PositionTicks"] = 900 * TIK
collector._store_sessions([prehravani], max_gap_seconds=60)
r = next(x for x in stats.active_sessions() if x["item_id"] == "ep-bez-knihovny")
check(round(r["progress"]) == 60, f"po posunu se ukazatel pohnul ({r['progress']:.0f} %)")


print()
print("--- přepnutí stopy za běhu se na kartě projeví ---")
# Dvě různé otázky, dvě různé hodnoty:
#   audio_language          "v čem to sledoval"  -> statistiky
#   current_audio_language  "co hraje právě teď" -> karta Právě se hraje
# Kdyby to byla jedna hodnota, jedna z odpovědí by musela být špatná.
STOPY = [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 1920, "Height": 1080},
    {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "eng", "IsDefault": True},
    {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "cze"},
    {"Type": "Subtitle", "Index": 3, "Language": "cze"},
]


def snimek(zvuk: int, titulky: int | None) -> dict[str, Any]:
    """Jeden pohled na relaci - jako by ho zrovna vrátil Jellyfin."""
    return {
        "Id": "prepinani", "UserId": "u9", "UserName": "Karel",
        "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False,
                      "AudioStreamIndex": zvuk, "SubtitleStreamIndex": titulky},
        "NowPlayingItem": {"Id": "film-prepinani", "Name": "Matrix",
                           "Type": "Movie", "MediaStreams": STOPY},
    }


def relace() -> dict[str, Any]:
    return next(r for r in stats.active_sessions()
                if r["item_id"] == "film-prepinani")


def zestarni(sekund: int) -> None:
    """Posune relaci do minulosti - jako by uplynul daný čas.

    Test nemůže čekat minutu; posunout čas v databázi je totéž a je to
    hotové hned.
    """
    with db.connect() as conn:
        conn.execute(
            """UPDATE playback
                  SET last_seen_at   = datetime(last_seen_at, ?),
                      language_since = datetime(language_since, ?)
                WHERE session_key LIKE 'prepinani%'""",
            (f"-{sekund} seconds", f"-{sekund} seconds"),
        )


# Časování odpovídá pravidlu z collectoru: první čtyři minuty jsou
# loga a znělky (ignorují se), pak minuta na ustálení stopy.
collector._store_sessions([snimek(1, None)], max_gap_seconds=600)
r = relace()
check(r["current_audio_language"] == "en", "karta hned ukazuje, co hraje")
check(r["audio_language"] is None,
      f"do statistik se ale zatím nic nezapsalo ({r['audio_language']})")

zestarni(30)
collector._store_sessions([snimek(2, None)], max_gap_seconds=600)
r = relace()
check(r["current_audio_language"] == "cs", "po přepnutí karta ukazuje češtinu")
check(r["audio_language"] is None, "a angličtina se do statistik nedostala")

zestarni(120)          # celkem 2:30 - pořád úvod
collector._store_sessions([snimek(2, None)], max_gap_seconds=600)
check(relace()["audio_language"] is None,
      "v polovině druhé minuty ještě ne - běží úvod")

zestarni(120)          # celkem 4:30 - úvod skončil, měří se minuta
collector._store_sessions([snimek(2, None)], max_gap_seconds=600)
check(relace()["audio_language"] is None,
      "ani hned po úvodu - ještě musí uběhnout minuta")

zestarni(60)           # celkem 5:30
collector._store_sessions([snimek(2, None)], max_gap_seconds=600)
r = relace()
check(r["audio_language"] == "cs",
      f"po pěti a půl minutě se čeština započítá ({r['audio_language']})")
check(r["language_confirmed"] == 1, "a je označená jako potvrzená")


print()
print("--- jednou započítaný jazyk už se nemění ---")
zestarni(120)
collector._store_sessions([snimek(1, None)], max_gap_seconds=600)
r = relace()
check(r["audio_language"] == "cs",
      f"přepnutí na konci statistiku nepřepíše ({r['audio_language']})")
check(r["current_audio_language"] == "en", "karta ale jde s divákem dál")


print()
print("--- titulky se posuzují spolu se zvukem ---")
# Kdo si opravuje jazyk, obvykle rovnou srovná i titulky - proto se
# potvrzuje celá kombinace najednou.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key LIKE 'prepinani%'")

collector._store_sessions([snimek(1, 3)], max_gap_seconds=600)
zestarni(20)
collector._store_sessions([snimek(2, None)], max_gap_seconds=600)
check(relace()["subtitle_language"] is None,
      "titulky zapnuté na začátku a hned vypnuté se nezapočítají")

zestarni(400)          # přes úvod i minutu na ustálení
collector._store_sessions([snimek(2, None)], max_gap_seconds=600)
r = relace()
check(r["audio_language"] == "cs" and r["subtitle_language"] is None,
      f"započítá se kombinace, u které divák zůstal "
      f"({r['audio_language']}/{r['subtitle_language']})")


print()
print("--- krátké přehrávání zůstane bez jazyka ---")
# Kdo přepíná pořád dokola, nemá "jazyk, se kterým sledoval". Přiznat to
# je poctivější než vybrat jeden náhodně; do statistik se stejně dostanou
# jen přehrávání delší než langstats.MIN_PLAY_SECONDS.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key LIKE 'prepinani%'")

for stopa in (1, 2, 1, 2, 1, 2, 1, 2):
    collector._store_sessions([snimek(stopa, None)], max_gap_seconds=600)
    zestarni(90)       # dohromady přes deset minut, ale nic nevydrží minutu
check(relace()["audio_language"] is None, "žádný jazyk nevydržel dost dlouho")


print()
print("--- statistiky rozlišení počítají stejně ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('l','L',?)",
                 (db.utcnow(),))
    for ident, sirka, vyska in (("sirokouhly-4k", 3840, 1608), ("plne-4k", 3840, 2160),
                                ("full-hd", 1920, 1080), ("sirokouhle-hd", 1920, 800)):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, width, height,
                                  is_missing, synced_at)
               VALUES (?,?,'Movie','l',?,?,0,?)""",
            (ident, ident, sirka, vyska, db.utcnow()),
        )

skupiny = {row["label"]: row["item_count"]
           for row in stats.resolution_breakdown()}
check(skupiny.get("4K") == 2, f"oba 4K filmy jsou ve 4K: {skupiny}")
check(skupiny.get("1080p") == 2, f"a oba HD v 1080p: {skupiny}")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
