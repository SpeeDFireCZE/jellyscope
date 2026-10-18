# -*- coding: utf-8 -*-
r"""Důvody přepočtu lidskou řečí.

Jellyfin posílá důvod přepočtu jako kód: `VideoCodecNotSupported,
AudioCodecNotSupported`. Takhle se dřív vypisoval všude - v Historii (bez
mezery za čárkou, takže se ani nezalomil), na kartě právě běžícího
přehrávání i v grafu „Proč se přepočítává", kde se navíc usekl na
„AudioCodecNotSupp…". Česky, anglicky ani španělsky z toho nikdo
nevyčte, že přehrávač neumí kodek zvuku.

Hlídá se:

* každý kód, který Jellyfin zná, má větu ve všech jazycích,
* neznámý kód (novější Jellyfin) se ukáže, jak přišel - nezmizí,
* Historie, karta přehrávání i Zjištění ukazují větu a kód zůstává
  v `title` pro toho, kdo ho hledá v dokumentaci Jellyfinu,
* v datech Zjištění zůstává vedle věty i kód - podle něj se dá hledat
  v dokumentaci Jellyfinu.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_duvody_prepoctu.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "duvody.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, formatting, insights, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


print("--- každý kód má větu ve všech jazycích ---")
SLOZKA = PROJECT / "jellyscope" / "translations"
for jazyk in ("cs", "en", "es"):
    slovnik = json.loads((SLOZKA / f"{jazyk}.json").read_text(encoding="utf-8"))
    chybi = [veta for veta in formatting.DUVODY_PREPOCTU.values()
             if not slovnik.get(veta, "").strip()]
    check(not chybi, f"{jazyk}.json: všech {len(formatting.DUVODY_PREPOCTU)} vět ({chybi[:2]})")
check({"VideoCodecNotSupported", "AudioCodecNotSupported",
       "ContainerBitrateExceedsLimit", "SubtitleCodecNotSupported"} <= set(formatting.DUVODY_PREPOCTU),
      "tabulka zná kódy, které chodí nejčastěji")

print()
print("--- převod kódů ---")
db.init_db()
db.set_setting("ui_language", "en")
db.forget_settings()
check(formatting.duvody_prepoctu("VideoCodecNotSupported,AudioCodecNotSupported")
      == "Unsupported video codec, unsupported audio codec",
      "dva kódy, dva důvody oddělené čárkou a mezerou, velké písmeno jen na začátku")
check(formatting.duvody_prepoctu("NecoNovehoZJellyfinu") == "NecoNovehoZJellyfinu",
      "neznámý kód se ukáže, jak přišel")
check(formatting.duvody_prepoctu("") == "" and formatting.duvody_prepoctu(None) == "",
      "prázdný důvod je prázdný text")
check("VideoCodec" not in formatting.duvody_prepoctu("VideoCodecNotSupported"),
      "známý kód se přeloží")

print()
print("--- Historie a Zjištění ---")
accounts.create("spravce", "dlouheheslo", is_admin=True)
ted = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, is_missing) "
                 "VALUES ('f1', 'Film', 'Movie', 'filmy', 0)")
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, started_at, last_seen_at, watched_seconds, is_active, play_method,"
        " transcode_reasons, device_name, client)"
        " VALUES ('s1', 'u1', 'Jana', 'f1', 'Film', 'Movie', 'filmy', ?, ?, 3600, 0,"
        " 'Transcode', 'VideoCodecNotSupported, AudioCodecNotSupported', 'Notebook', 'Jellyfin Web')",
        (ted, ted))
    conn.commit()

klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo", "zpusob": "mistni"})
db.set_setting("ui_language", "en")
db.forget_settings()

veta_video = formatting.duvody_prepoctu("VideoCodecNotSupported")
historie = klient.get("/history").text
check(veta_video in historie, "Historie ukazuje větu místo kódu")
check('title="VideoCodecNotSupported, AudioCodecNotSupported"' in historie,
      "a kód zůstává v title")

duvody = insights.transcode_reasons(30)
check(any(r.get("label") == "VideoCodecNotSupported" for r in duvody),
      "Zjištění: kód v datech zůstává")
check(any(r.get("nazev") == veta_video for r in duvody), "a vedle něj je věta")
zjisteni = klient.get("/insights").text
casti = zjisteni.split("hbars popisky-nad")
check(len(casti) > 1 and veta_video in casti[1][:3000],
      "graf důvodů má větu nad pruhem, celou, ne useknutou")

print()
print("--- karta právě běžícího přehrávání ---")
# Vykresluje se skutecna karta (vyrez, ktery si Prehled vyzvedava sam),
# ne hledani ve zdrojaku sablony - preklep ve filtru by se jinak nenasel.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, started_at, last_seen_at, watched_seconds, is_active, play_method,"
        " transcode_reasons, device_name, client)"
        " VALUES ('s2', 'u2', 'Petr', 'f1', 'Film', 'Movie', 'filmy', ?, ?, 60, 1,"
        " 'Transcode', 'ContainerBitrateExceedsLimit', 'TV', 'Jellyfin Android TV')",
        (ted, ted))
    conn.commit()
karta = klient.get("/partials/now-playing").text
veta_tok = formatting.duvody_prepoctu("ContainerBitrateExceedsLimit")
check(veta_tok in karta and "ContainerBitrateExceedsLimit</p>" not in karta,
      f"karta přehrávání ukazuje větu ({veta_tok!r})")
check('title="ContainerBitrateExceedsLimit"' in karta, "a kód je v title")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
