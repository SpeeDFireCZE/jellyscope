"""Technicka analyza souboru pomoci ffprobe.

Tohle je ta cast, kterou umi MediaLyze a Jellystat ne: podivat se na soubor
primo na disku a zjistit, co v nem doopravdy je.

`ffprobe` je nastroj z balicku ffmpeg. Zavolame ho jako externi program,
rekneme mu "vypis, co vidis, ve formatu JSON", a odpoved si prectem.

Dulezite: ffprobe soubory jen **cte**. Nic neprepisuje, nic nemaze.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from . import languages

# Kdyz jeden soubor trva dele nez tohle, vzdame ho a jdeme dal.
# Bez limitu by jedna vadna nahravka dokazala zastavit cely scan.
PROBE_TIMEOUT_SECONDS = 60


class ProbeError(RuntimeError):
    """Analyza jednoho souboru se nepovedla."""


def find_ffprobe(configured_path: str = "") -> str | None:
    """Najde spustitelny ffprobe.

    Nejdriv zkusi cestu z nastaveni, pak systemovou PATH, pak par mist,
    kam se ffmpeg na Windows obvykle rozbaluje.
    """
    if configured_path:
        candidate = Path(configured_path)
        if candidate.is_file():
            return str(candidate)
        # Uzivatel mohl zadat slozku misto souboru - zkusime to domyslet.
        if candidate.is_dir():
            for name in ("ffprobe.exe", "ffprobe"):
                if (candidate / name).is_file():
                    return str(candidate / name)

    found = shutil.which("ffprobe")
    if found:
        return found

    for guess in (
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
        "/usr/bin/ffprobe",
        "/usr/local/bin/ffprobe",
    ):
        if Path(guess).is_file():
            return guess

    return None


def apply_path_mappings(path: str, mappings: list[dict[str, str]]) -> str:
    """Prelozi cestu, kterou hlasi Jellyfin, na cestu platnou tady.

    K cemu to je: Jellyfin bezici v Dockeru vidi film jako
    `/media/filmy/Duna.mkv`, ale na hostiteli to je `D:\\media\\filmy\\Duna.mkv`.
    Mapovani prepise zacatek cesty z prvniho tvaru na druhy.

    Kdyz Jellyscope bezi na stejnem stroji a bez Dockeru, seznam mapovani
    je prazdny a funkce vrati cestu beze zmeny.
    """
    if not path:
        return path

    for mapping in mappings:
        source = (mapping.get("from") or "").strip()
        target = (mapping.get("to") or "").strip()
        if not source:
            continue
        # Porovnavame bez ohledu na velikost pismen a na smer lomitek -
        # Windows a Linux se v obojim lisi.
        normalised = path.replace("\\", "/")
        normalised_source = source.replace("\\", "/")
        if normalised.lower().startswith(normalised_source.lower()):
            rest = normalised[len(normalised_source):].lstrip("/")
            # Oddelovac bereme z CILOVE cesty, ne ze systemu (os.sep).
            # Cil popisuje, jak vypada cesta na stroji, kam se mapuje -
            # a to nemusi byt ten, na kterem zrovna bezime. Podle os.sep
            # by z mapovani na "D:\\media" vyslo na Linuxu
            # "D:\\media/filmy/Duna.mkv", tedy pomichane lomitka.
            oddelovac = "\\" if ("\\" in target and "/" not in target) else "/"
            return target.rstrip("/\\") + oddelovac + rest.replace("/", oddelovac)

    return path


async def probe_file(path: str, ffprobe_bin: str) -> dict[str, Any]:
    """Spusti ffprobe na jeden soubor a vrati technicke udaje.

    Pouzivame `create_subprocess_exec`, ne `shell=True`. Rozdil je zasadni:
    exec preda argumenty programu primo, kdezto shell by je nejdriv poslal
    interpretu prikazove radky - a nazev souboru s uvozovkou nebo strednikem
    by se stal prikazem. Tomuhle se rika command injection a je to jedna
    z nejcastejsich bezpecnostnich der vubec.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ProbeError("soubor neexistuje nebo na nej nevidim")

    args = [
        ffprobe_bin,
        "-v", "error",              # nic nevypisuj, krome skutecnych chyb
        "-print_format", "json",
        "-show_format",             # udaje o kontejneru (velikost, delka, bitrate)
        "-show_streams",            # jednotlive stopy (video, zvuk, titulky)
        str(file_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=PROBE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        raise ProbeError(f"ffprobe neodpovedel do {PROBE_TIMEOUT_SECONDS} s") from exc
    except FileNotFoundError as exc:
        raise ProbeError(f"ffprobe nenalezen ({ffprobe_bin})") from exc
    except OSError as exc:
        raise ProbeError(f"ffprobe se nepodarilo spustit: {exc}") from exc

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:200]
        raise ProbeError(detail or f"ffprobe skoncil s kodem {process.returncode}")

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ProbeError("ffprobe vratil neplatny JSON") from exc

    summary = _summarise(data, file_path)
    summary["streams"] = extract_streams(data)
    return summary


def _summarise(data: dict[str, Any], file_path: Path) -> dict[str, Any]:
    """Z ukecane odpovedi ffprobe vytahne jen to, co nas zajima."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    audio = audio_streams[0] if audio_streams else None

    result: dict[str, Any] = {
        "container": (fmt.get("format_name") or "").split(",")[0] or None,
        "size_bytes": _to_int(fmt.get("size")),
        "bitrate": _to_int(fmt.get("bit_rate")),
        "video_codec": None,
        "audio_codec": None,
        "audio_channels": None,
        "width": None,
        "height": None,
        "video_range": None,
        # ffprobe hlasi jazyk trojpismenne ("ces"), Jellyfin nekdy jinak.
        # Sjednotime to hned tady, aby databaze mela jen jeden tvar.
        "audio_languages": languages.pack(_language_of(s) for s in audio_streams),
        "subtitle_languages": languages.pack(_language_of(s) for s in subtitle_streams),
        "default_audio_language": (
            languages.normalize(_language_of(audio)) if audio is not None else None
        ),
    }

    if result["size_bytes"] is None:
        # Kdyz ffprobe velikost neuvedl, zeptame se rovnou souboroveho systemu.
        try:
            result["size_bytes"] = file_path.stat().st_size
        except OSError:
            pass

    if video is not None:
        result["video_codec"] = video.get("codec_name")
        result["width"] = _to_int(video.get("width"))
        result["height"] = _to_int(video.get("height"))
        result["video_range"] = _detect_video_range(video)
        if result["bitrate"] is None:
            result["bitrate"] = _to_int(video.get("bit_rate"))

    if audio is not None:
        result["audio_codec"] = audio.get("codec_name")
        result["audio_channels"] = _to_int(audio.get("channels"))

    return result


def extract_streams(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Jednotlive stopy z odpovedi ffprobe, ve stejnem tvaru jako z Jellyfinu.

    Dulezite je to "ve stejnem tvaru": zbytek aplikace pak nemusi vedet,
    odkud data prisla. Prevod na spolecny tvar patri sem, k okraji - ne
    doprostred, kde by o nem musel vedet kazdy.
    """
    result = []
    for index, stream in enumerate(data.get("streams") or []):
        codec_type = (stream.get("codec_type") or "").lower()
        mapped = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle"}.get(codec_type)
        if mapped is None:
            continue

        tags = stream.get("tags") or {}
        result.append({
            "stream_index": _to_int(stream.get("index")) or index,
            "type": mapped,
            "codec": stream.get("codec_name"),
            "language": languages.normalize(tags.get("language")),
            "title": tags.get("title"),
            "channels": _to_int(stream.get("channels")),
            "channel_layout": stream.get("channel_layout"),
            "width": _to_int(stream.get("width")),
            "height": _to_int(stream.get("height")),
            "bitrate": _to_int(stream.get("bit_rate")),
            "is_default": 1 if (stream.get("disposition") or {}).get("default") else 0,
            "is_forced": 1 if (stream.get("disposition") or {}).get("forced") else 0,
            "is_external": 0,
        })
    return result


def _language_of(stream: dict[str, Any] | None) -> str | None:
    """Vytahne jazyk stopy z metadat ffprobe.

    ffprobe je schovava do vnoreneho slovniku "tags", ktery casto chybi
    uplne - u domacich nahravek nebo starych souboru jazyk nikdo nevyplnil.
    """
    if not stream:
        return None
    return (stream.get("tags") or {}).get("language")


# Znacky kodeku, kterymi se Dolby Vision hlasi v kontejneru: dvh1/dvhe
# je HEVC s DV, dvav/dva1 totez pro H.264 a dav1 pro AV1. Obycejne
# hev1/hvc1 tuhle znacku nemaji, takze planou detekci nehrozi.
DV_ZNACKY = {"dvh1", "dvhe", "dvav", "dva1", "dav1"}


def _je_dolby_vision(video: dict[str, Any]) -> bool:
    """Nese video stopa Dolby Vision?

    Ptame se na tri veci, protoze ruzne verze ffprobe a ruzne kontejnery
    to hlasi jinak - a staci, aby se minula jedna, a DV soubor se zapsal
    jako obycejne HDR:

    * **postranni data** - hlavni cesta ("DOVI configuration record").
      Nazev se ale mezi verzemi lisil, tak se hleda podretezec, a jako
      pojistka staci i to, ze blok nese pole `dv_profile`.
    * **znacka kodeku** ("dvh1", "dvhe") - u MP4 a MOV je v kontejneru
      i tehdy, kdyz ffprobe postranni data nevypise.
    * **jmeno profilu** - nektere sestaveni pisou "Dolby Vision" rovnou
      do `profile`.

    Co tohle nechyti, je stary ffmpeg nad MKV: cist DV z Matrosky umi az
    novejsi verze, a starsi o nem nerekne vubec nic.
    """
    for blok in (video.get("side_data_list") or []):
        typ = str(blok.get("side_data_type") or "").lower()
        if "dovi" in typ or "dolby vision" in typ:
            return True
        if any(str(klic).startswith("dv_") for klic in blok):
            return True

    if str(video.get("codec_tag_string") or "").strip().lower() in DV_ZNACKY:
        return True

    return "dolby vision" in str(video.get("profile") or "").lower()


def _detect_video_range(video: dict[str, Any]) -> str:
    """Odhadne, jestli je video SDR, HDR nebo Dolby Vision.

    Jde o odhad podle barevnych metadat. Presnejsi klasifikace by chtela
    hlubsi rozbor, ale pro statistiku "kolik mam HDR obsahu" tohle staci.
    """
    if _je_dolby_vision(video):
        return "DOVI"

    transfer = (video.get("color_transfer") or "").lower()
    if transfer in {"smpte2084", "arib-std-b67"}:
        return "HDR"

    pix_fmt = (video.get("pix_fmt") or "").lower()
    if "10le" in pix_fmt and transfer in {"bt2020-10", "bt2020_10"}:
        return "HDR"

    return "SDR"


def _to_int(value: Any) -> int | None:
    """Bezpecny prevod na cele cislo. ffprobe vraci cisla casto jako text."""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
