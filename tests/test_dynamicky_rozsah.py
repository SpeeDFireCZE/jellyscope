# -*- coding: utf-8 -*-
r"""Dynamicky rozsah: SDR / HDR / Dolby Vision.

Rozsah se bere ze dvou zdroju - z ffprobe (cte se soubor) nebo z Jellyfinu
(cte se knihovna). Ten druhy zna v poli `VideoRange` jen SDR a HDR, takze
Dolby Vision se pod nim schova jako obycejne HDR; rozlisi ho az
`VideoRangeType` ("DOVIWithHDR10" je profil 8.1).

Bez toho mela knihovna v prehledu "SDR / HDR / Dolby Vision" treti sloupec
vzdycky prazdny - a tentyz soubor se pocital jinak podle toho, odkud se
udaje vzaly.

Spusteni:
    .\.venv\Scripts\python.exe tests\test_dynamicky_rozsah.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rozsah.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, formatting, jellyfin, probe, stats  # noqa: E402

db.init_db()

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


def z_jellyfinu(**video) -> str | None:
    tech = jellyfin.extract_tech_from_item(
        {"MediaSources": [{"MediaStreams": [dict(video, Type="Video", Codec="hevc")]}]})
    return tech.get("video_range")


print("--- co hlásí Jellyfin ---")
# Profil 8.1 je presne ten pripad z knihovny: Jellyfin ho v badge pise
# jako "Dolby Vision Profile 8.1 (HDR10)" a v datech jako DOVIWithHDR10.
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="DOVIWithHDR10") == "DOVI",
      "Dolby Vision profil 8.1 je DOVI, ne jen HDR")
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="DOVI") == "DOVI",
      "profil 5 taky")
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="DOVIWithHLG") == "DOVI",
      "a DV nad HLG taky")
# Doslova z knihovny: Jellyfin u toho filmu hlasi VideoRange "HDR"
# a VideoRangeType "DOVIWithHDR10Plus" (profil 8, level 6, compat id 1).
# Kdyby se hledala presna jmena misto predpony "DOVI", tahle podoba
# s "Plus" by propadla.
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="DOVIWithHDR10Plus",
                  VideoDoViTitle="Dolby Vision Profile 8.1 (HDR10)") == "DOVI",
      "DOVIWithHDR10Plus (profil 8.1 z ostré knihovny)")
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="HDR10") == "HDR", "HDR10 je HDR")
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="HDR10Plus") == "HDR", "HDR10+ taky")
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="HLG") == "HDR", "HLG taky")
check(z_jellyfinu(VideoRange="SDR", VideoRangeType="SDR") == "SDR", "SDR zůstává SDR")

# Starsi Jellyfin `VideoRangeType` nema - tam plati, co rekne `VideoRange`.
check(z_jellyfinu(VideoRange="HDR") == "HDR", "bez typu se věří VideoRange")
check(z_jellyfinu(VideoRange="SDR") == "SDR", "v obou směrech")

# "Unknown" se drive ulozilo tak, jak prislo, takze v grafu pribyl sloupec
# doslova nazvany "Unknown" vedle naseho "neznámé".
check(z_jellyfinu(VideoRange="Unknown") is None, "„Unknown“ se nevydává za rozsah")
check(z_jellyfinu() is None, "a chybějící údaj taky ne")

print()
print("--- co změří ffprobe ---")
# Dolby Vision hlasi ruzne verze ffprobe a ruzne kontejnery jinak. Staci,
# aby se minula jedna cesta, a DV soubor se zapise jako obycejne HDR -
# proto se hleda trojmo.
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "DOVI configuration record",
                         "dv_profile": 8, "dv_bl_signal_compatibility_id": 1}],
     "color_transfer": "smpte2084"}) == "DOVI", "postranní data, přesný název")
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "Dolby Vision Configuration Record"}]}) == "DOVI",
    "název bloku psaný jinak")
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "unknown", "dv_version_major": 1,
                         "dv_profile": 5}]}) == "DOVI",
    "neznámý název bloku, ale profil v něm je")
check(probe._detect_video_range(
    {"codec_tag_string": "dvhe", "color_transfer": "smpte2084"}) == "DOVI",
    "značka kodeku dvhe (MP4/MOV)")
check(probe._detect_video_range({"codec_tag_string": "dvh1"}) == "DOVI", "a dvh1")
check(probe._detect_video_range(
    {"profile": "Dolby Vision Profile 8.1"}) == "DOVI", "jméno profilu")

# A hlavne: nic z toho nesmi udelat DV z obycejneho souboru.
check(probe._detect_video_range(
    {"codec_tag_string": "hvc1", "color_transfer": "smpte2084"}) == "HDR",
    "obyčejné HDR10 zůstává HDR")
check(probe._detect_video_range(
    {"codec_tag_string": "hev1", "color_transfer": "bt709"}) == "SDR",
    "a SDR zůstává SDR")
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "Content light level metadata",
                         "max_content": 1000}],
     "color_transfer": "smpte2084"}) == "HDR",
    "jiná postranní data z DV nedělají")
check(probe._detect_video_range({"color_transfer": "smpte2084"}) == "HDR", "HDR ze souboru")
check(probe._detect_video_range({"color_transfer": "bt709"}) == "SDR", "SDR ze souboru")

# Oba zdroje musi ze stejneho souboru udelat tentyz zaznam - jinak by
# statistika zavisela na nastaveni, ne na knihovne.
check(z_jellyfinu(VideoRange="HDR", VideoRangeType="DOVIWithHDR10")
      == probe._detect_video_range(
          {"side_data_list": [{"side_data_type": "DOVI configuration record"}]}),
      "oba zdroje se na Dolby Vision shodnou")

print()
print("--- jak se to píše člověku ---")
check(formatting.video_range_human("DOVI") == "Dolby Vision", "DOVI je Dolby Vision")
check(formatting.video_range_human("HDR") == "HDR", "HDR zůstává")
check(formatting.video_range_human(None) == "neznámé", "prázdno je neznámé")
check(formatting.video_range_human("nesmysl") == "neznámé", "a nesmysl taky")

print()
print("--- rozpad v knihovně ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name) VALUES ('l1','Filmy')")
    for i, rozsah in enumerate(["DOVI", "DOVI", "HDR", "SDR", "SDR", "SDR", None, ""]):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
            " video_range) VALUES (?,?,?,?,0,'jellyfin',?)",
            (f"i{i}", f"Film{i}", "Movie", "l1", rozsah))
    conn.commit()

rozpad = {r["label"]: r["item_count"] for r in stats.video_range_breakdown()}
check(rozpad.get("Dolby Vision") == 2, f"Dolby Vision má vlastní sloupec ({rozpad})")
check(rozpad.get("SDR") == 3 and rozpad.get("HDR") == 1, "SDR i HDR sedí")
# NULL a prazdny retezec jsou totez - jeden sloupec, ne dva stejne nazvane.
check(rozpad.get("neznámé") == 2, f"neznámé se sečtou do jednoho ({rozpad})")

check(stats.library_overview("l1")["hdr_count"] == 3,
      "dlaždice „HDR / Dolby Vision“ počítá obojí dohromady")

print()
print("--- starý ffprobe o Dolby Vision neví, Jellyfin ano ---")
# Tohle je doslovny vystup ffprobe 4.2.7 (Ubuntu 20.04) na filmu, ktery
# Jellyfin popisuje jako "Dolby Vision Profile 8.1 (HDR10)". Cist DV
# z Matrosky umi az ffmpeg 5, takze tady po nem neni ani stopa: zadna
# postranni data, znacka kodeku "[0][0][0][0]" (MKV znacky nema)
# a profil "Main 10".
stary_ffprobe = {
    "codec_name": "hevc", "profile": "Main 10", "codec_type": "video",
    "codec_tag_string": "[0][0][0][0]", "codec_tag": "0x0000",
    "width": 3822, "height": 2066, "pix_fmt": "yuv420p10le",
    "color_range": "tv", "color_space": "bt2020nc",
    "color_transfer": "smpte2084", "color_primaries": "bt2020",
}
check(probe._detect_video_range(stary_ffprobe) == "HDR",
      "z takového výstupu se DV vyčíst nedá - vyjde HDR")
check(jellyfin.video_range_of({"MediaSources": [{"MediaStreams": [
        {"Type": "Video", "VideoRange": "HDR",
         "VideoRangeType": "DOVIWithHDR10Plus",
         "VideoDoViTitle": "Dolby Vision Profile 8.1 (HDR10)"}]}]}) == "DOVI",
      "ale Jellyfin o tomtéž souboru ví")

# Proto se hlaseny udaj uklada vedle zmereneho a Dolby Vision plati jako
# pozitivni nalez: zadny ze zdroju ho nehlasi omylem, oba ho umi minout.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range, video_range_reported) VALUES"
        " ('dv1','Tvůj film','Movie','l1',0,'ffprobe','HDR','DOVI')")
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range, video_range_reported) VALUES"
        " ('dv2','Naopak','Movie','l1',0,'ffprobe','DOVI','HDR')")
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range, video_range_reported) VALUES"
        " ('dv3','Bez měření','Movie','l1',0,'jellyfin',NULL,'HDR')")
    conn.commit()

check(stats.rozsah_polozky(stats.item("dv1")) == "DOVI",
      "změřeno HDR + hlášeno DOVI = Dolby Vision")
check(stats.rozsah_polozky(stats.item("dv2")) == "DOVI",
      "a obráceně taky - Jellyfin ho minout může")
check(stats.rozsah_polozky(stats.item("dv3")) == "HDR",
      "bez měření platí, co hlásí Jellyfin")

rozpad2 = {r["label"]: r["item_count"] for r in stats.video_range_breakdown()}
check(rozpad2.get("Dolby Vision") == 4, f"v přehledu jsou obojí ({rozpad2})")
check(stats.library_overview("l1")["hdr_count"] == 6,
      f"a dlaždice počítá HDR i DV ({stats.library_overview('l1')['hdr_count']})")

print()
print("--- doplnění rozsahu při analýze souborů ---")
# Hlaseny rozsah zapisovala jen synchronizace knihovny. Clovek si ale
# technicka data spojuje s "Analyzou souboru": spustil ji, nic se
# nezmenilo, a nemel duvod tusit, ze Dolby Vision doplni az jina uloha.
import asyncio  # noqa: E402

from jellyscope import scanner  # noqa: E402

with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range) VALUES ('an1','Analyzovaný','Movie','l1',0,'ffprobe','HDR')")
    conn.commit()

check("an1" in scanner.kandidati_na_rozsah_z_jellyfinu(),
      "položka bez hlášeného rozsahu je kandidát")
check("dv1" not in scanner.kandidati_na_rozsah_z_jellyfinu(),
      "co už hlášené má, se znovu netahá")


class _FalesnyKlient:
    """Jellyfin, který odpoví tak, jak odpověděl ten skutečný."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def items_by_ids(self, ids):
        return [{"Id": "an1", "MediaSources": [{"MediaStreams": [
            {"Type": "Video", "Codec": "hevc", "VideoRange": "HDR",
             "VideoRangeType": "DOVIWithHDR10Plus",
             "VideoDoViTitle": "Dolby Vision Profile 8.1 (HDR10)"}]}]}]


puvodni = scanner.JellyfinClient
scanner.JellyfinClient = _FalesnyKlient
try:
    check(asyncio.run(scanner.doplnit_rozsah_z_jellyfinu(["an1"])) == 1,
          "analýza si rozsah od Jellyfinu vyžádá")
finally:
    scanner.JellyfinClient = puvodni

po = stats.item("an1")
check(po["video_range"] == "HDR", "změřený údaj zůstává nedotčený")
check(po["video_range_reported"] == "DOVI", "a vedle něj přibude hlášený")
check(stats.rozsah_polozky(po) == "DOVI", "položka je Dolby Vision")
check("an1" not in scanner.kandidati_na_rozsah_z_jellyfinu(),
      "a podruhé se na ni Jellyfina neptáme")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
