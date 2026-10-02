# -*- coding: utf-8 -*-
r"""Rychlost převodu (fps) v bublině u značky transcode.

Jellyfin posílá v `TranscodingInfo.Framerate`, kolik snímků za vteřinu
převod zvládá. Samo číslo nic neříká - 30 fps u filmu (24) stačí, u
sportu (50) ne - proto se ukládá i snímkování videa a bublina řekne,
když převod nestíhá.

U běžícího přehrávání platí stav teď, u historie průměr za celé
přehrávání. Pauza se do průměru nepočítá: ffmpeg při ní stojí a průměr
by tvrdil, že server nestíhal.

Bublina je na čtyřech místech: Právě se hraje, Historie, detail titulu
a detail knihovny.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_transcode_fps.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "fps.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, collector, db, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, video_codec, audio_codec, is_missing)"
                 " VALUES ('film', 'Duna', 'Movie', 'lib', 'hevc', 'eac3', 0)")
    conn.commit()

print("--- čtení čísla ---")
check(collector._fps(23.976) == 24, "23,976 je 24")
check(collector._fps("96.4") == 96, "text z JSONu se přečte")
for nesmysl in (None, 0, "", "abc", -5, 99999):
    check(collector._fps(nesmysl) is None, f"{nesmysl!r} -> nic")


def relace(fps, pauza=False, prevod=True):
    """Relace tak, jak ji posílá Jellyfin."""
    item = {"Id": "film", "Name": "Duna", "Type": "Movie", "RunTimeTicks": 90_000_000_000,
            "MediaStreams": [{"Type": "Video", "Codec": "hevc", "Width": 3840,
                              "Height": 2160, "RealFrameRate": 23.976},
                             {"Type": "Audio", "Codec": "eac3"}]}
    s = {"Id": "s1", "UserId": "u1", "UserName": "Jana", "DeviceId": "d1",
         "Client": "Jellyfin Web", "DeviceName": "Chrome",
         "NowPlayingItem": item,
         "PlayState": {"PlayMethod": "Transcode" if prevod else "DirectPlay",
                       "IsPaused": pauza, "PositionTicks": 600_000_000}}
    if prevod:
        s["TranscodingInfo"] = {"VideoCodec": "h264", "AudioCodec": "aac",
                                "IsVideoDirect": False, "IsAudioDirect": False,
                                "HardwareAccelerationType": "qsv",
                                "Framerate": fps,
                                "TranscodeReasons": ["VideoCodecNotSupported"]}
    return s


def radek():
    return dict(db.query_one("SELECT * FROM playback WHERE item_id = 'film'"))


print()
print("--- sběrač ---")
collector._store_sessions([relace(96.3)], 30)
r = radek()
check(r["transcode_fps"] == 96 and r["video_fps"] == 24,
      f"první snímek: převod 96, video 24 ({r['transcode_fps']}, {r['video_fps']})")
check(r["transcode_fps_soucet"] == 96 and r["transcode_fps_vzorku"] == 1, "a je to první vzorek")

collector._store_sessions([relace(120)], 30)
r = radek()
check(r["transcode_fps"] == 120 and r["transcode_fps_vzorku"] == 2
      and r["transcode_fps_soucet"] == 216, "druhý vzorek se přičte")

collector._store_sessions([relace(3, pauza=True)], 30)
r = radek()
check(r["transcode_fps_vzorku"] == 2 and r["transcode_fps"] is None,
      "pauza se nepočítá a stav teď zmizí - ffmpeg stojí, to není nestíhání")

print()
print("--- bublina u běžícího ---")
zive = stats.active_sessions()[0]
fakt = next((f for f in zive["prepocet"] if f.get("fps")), None)
check(fakt is not None and fakt["fps"] == 108 and fakt["prumer"],
      f"během pauzy ukáže průměr ({fakt})")
collector._store_sessions([relace(18)], 30)
zive = stats.active_sessions()[0]
fakt = next(f for f in zive["prepocet"] if f.get("fps"))
check(fakt["fps"] == 18 and not fakt["prumer"] and fakt["nestiha"],
      "teď 18 při 24 fps videa: nestíhá")
poradi = [f["co"] for f in zive["prepocet"]]
check(poradi.index("Snímky") == poradi.index("Zvuk") + 1
      and poradi.index("Snímky") < poradi.index("Hardware"),
      f"pod Zvukem, nad Hardwarem ({poradi})")

klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo",
                            "zpusob": "mistni"})
prave = klient.get("/partials/now-playing").text
check("Převod: 18 fps (video má 24) – nestíhá" in prave,
      "karta Právě se hraje to říká v bublině")

print()
print("--- historie: průměr ---")
collector._store_sessions([], 30)          # relace skončila
# Test běží ve vteřinách, takže se nic „neodsledovalo" - a stránky
# ukazují jen přehrávání, která nějaký čas mají.
with db.connect() as conn:
    conn.execute("UPDATE playback SET watched_seconds = 3600 WHERE item_id = 'film'")
    conn.commit()
r = radek()
check(r["is_active"] == 0, "přehrávání je uzavřené")
ulozene = stats.rychlost_prevodu(r)
check(ulozene["fps"] == 78 and ulozene["prumer"] and not ulozene["nestiha"],
      f"v historii průměr (96+120+18)/3 = 78 ({ulozene})")
for adresa, kde in (("/history", "Historie"), ("/item/film", "detail titulu"),
                    ("/library/lib?tab=activity", "detail knihovny")):
    html = klient.get(adresa).text
    check("Převod: v průměru 78 fps (video má 24)" in html, f"{kde} ukazuje průměr")

print()
print("--- převod jen zvuku: obraz se kopíruje, rychlost se nezapisuje ---")
s_kopii = relace(640)
s_kopii["Id"] = "s2"
s_kopii["DeviceId"] = "d2"
# Jiny divak: u tehoz diváka a filmu by sberac spravne navazal na
# predchozi prehravani (pokracovani po pauze) a novy radek by nevznikl.
s_kopii["UserId"] = "u2"
s_kopii["TranscodingInfo"]["IsVideoDirect"] = True
s_kopii["TranscodingInfo"]["TranscodeReasons"] = ["AudioCodecNotSupported"]
collector._store_sessions([s_kopii], 30)
kopie = dict(db.query_one("SELECT * FROM playback WHERE device_id = 'd2'"))
check(kopie["transcode_fps"] is None and kopie["transcode_fps_vzorku"] == 0,
      "640 fps kopírování obrazu se nezapsalo jako rychlost převodu")
check(stats.rychlost_prevodu({**kopie, "transcode_fps_soucet": 640,
                              "transcode_fps_vzorku": 1}) is None,
      "a bublina ho neukáže ani u dat uložených dřív")
collector._store_sessions([], 30)

print()
print("--- bez převodu obrazu nic ---")
check(stats.rychlost_prevodu({"is_active": 0, "transcode_fps_vzorku": 0}) is None,
      "bez vzorků žádný řádek")
check(stats.rychlost_prevodu({"is_active": 1, "transcode_fps": 30, "video_fps": None})["nestiha"] is False,
      "bez snímkování videa se nestíhání netvrdí")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
