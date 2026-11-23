"""Technical analysis of files with ffprobe.

This is the part MediaLyze can do and Jellystat cannot: look at a file right
on the disk and find out what really is in it.

`ffprobe` is a tool from the ffmpeg package. We call it as an external
program, tell it "print what you see, as JSON", and read the answer.

Important: ffprobe only **reads** files. It rewrites nothing, deletes nothing.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from . import languages
from .i18n import TranslatableError

# When one file takes longer than this, we give it up and go on.
# Without a limit one faulty recording could stop the whole scan.
PROBE_TIMEOUT_SECONDS = 60


class ProbeError(TranslatableError):
    """Analysing one file failed - a key and its values (i18n.TranslatableError), stored with the item."""


def find_ffprobe(configured_path: str = "") -> str | None:
    """Finds an executable ffprobe.

    It tries the path from the settings first, then the system PATH, then a
    few places ffmpeg is usually unpacked to on Windows.
    """
    if configured_path:
        candidate = Path(configured_path)
        if candidate.is_file():
            return str(candidate)
        # The user may have given a folder instead of a file - we try to work it out.
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
    """Translates a path Jellyfin reports to a path valid here.

    What it is for: Jellyfin running in Docker sees a movie as
    `/media/filmy/Duna.mkv`, but on the host it is `D:\\media\\filmy\\Duna.mkv`.
    A mapping rewrites the start of the path from the first shape to the second.

    When Jellyscope runs on the same machine and without Docker, the list of
    mappings is empty and the function returns the path unchanged.
    """
    if not path:
        return path

    for mapping in mappings:
        source = (mapping.get("from") or "").strip()
        target = (mapping.get("to") or "").strip()
        if not source:
            continue
        # Compared regardless of case and of the direction of slashes -
        # Windows and Linux differ in both.
        normalised = path.replace("\\", "/")
        normalised_source = source.replace("\\", "/")
        if normalised.lower().startswith(normalised_source.lower()):
            rest = normalised[len(normalised_source):].lstrip("/")
            # The separator is taken from the TARGET path, not from the system
            # (os.sep). The target describes what a path looks like on the
            # machine it maps to - and that need not be the one we run on.
            # By os.sep a mapping to "D:\\media" would give on Linux
            # "D:\\media/filmy/Duna.mkv", that is mixed slashes.
            separator = "\\" if ("\\" in target and "/" not in target) else "/"
            return target.rstrip("/\\") + separator + rest.replace("/", separator)

    return path


async def probe_file(path: str, ffprobe_bin: str) -> dict[str, Any]:
    """Runs ffprobe on one file and returns the technical data.

    We use `create_subprocess_exec`, not `shell=True`. The difference is
    essential: exec passes the arguments to the program directly, whereas a
    shell would first send them to the command-line interpreter - and a file
    name with a quote or a semicolon would become a command. This is called
    command injection, and it is one of the most common security holes of all.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ProbeError("The file does not exist or cannot be seen from here.")

    args = [
        ffprobe_bin,
        "-v", "error",              # print nothing but real errors
        "-print_format", "json",
        "-show_format",             # data about the container (size, length, bitrate)
        "-show_streams",            # the single streams (video, sound, subtitles)
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
    except TimeoutError as exc:
        raise ProbeError("ffprobe did not answer within {seconds} s.", seconds=PROBE_TIMEOUT_SECONDS) from exc
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe was not found ({path}).", path=ffprobe_bin) from exc
    except OSError as exc:
        raise ProbeError("ffprobe could not be started: {error}", error=exc) from exc

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:200]
        if detail:
            raise ProbeError("ffprobe reported an error: {detail}", detail=detail)
        raise ProbeError("ffprobe ended with code {code}.", code=process.returncode)

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ProbeError("ffprobe returned invalid JSON.") from exc

    summary = _summarise(data, file_path)
    summary["streams"] = extract_streams(data)
    return summary


def _summarise(data: dict[str, Any], file_path: Path) -> dict[str, Any]:
    """Takes out of ffprobe's verbose answer only what interests us."""
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
        # ffprobe reports the language in three letters ("ces"), Jellyfin sometimes
        # otherwise. It is unified right here, so the database has one shape only.
        "audio_languages": languages.pack(_language_of(s) for s in audio_streams),
        "subtitle_languages": languages.pack(_language_of(s) for s in subtitle_streams),
        "default_audio_language": (
            languages.normalize(_language_of(audio)) if audio is not None else None
        ),
    }

    if result["size_bytes"] is None:
        # When ffprobe did not state the size, we ask the file system directly.
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
    """The single streams from ffprobe's answer, in the same shape as from Jellyfin.

    What matters is "in the same shape": the rest of the application then
    need not know where the data came from. The conversion to the common
    shape belongs here, at the edge - not in the middle, where everybody
    would have to know about it.
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
    """Takes the stream's language out of ffprobe's metadata.

    ffprobe hides it in a nested dictionary "tags", which is often missing
    altogether - nobody filled the language in for home recordings or old files.
    """
    if not stream:
        return None
    return (stream.get("tags") or {}).get("language")


# Codec tags Dolby Vision announces itself with in the container: dvh1/dvhe
# is HEVC with DV, dvav/dva1 the same for H.264 and dav1 for AV1. Plain
# hev1/hvc1 do not have this tag, so there is no risk of a false detection.
DV_MARKS = {"dvh1", "dvhe", "dvav", "dva1", "dav1"}


def _is_dolby_vision(video: dict[str, Any]) -> bool:
    """Does the video stream carry Dolby Vision?

    We ask about three things, because different versions of ffprobe and
    different containers report it differently - and it is enough to miss
    one for a DV file to be stored as plain HDR:

    * **side data** - the main road ("DOVI configuration record"). The name
      differed between versions, though, so a substring is looked for, and
      as a safeguard it is enough that the block carries a `dv_profile` field.
    * **the codec tag** ("dvh1", "dvhe") - in MP4 and MOV it is in the
      container even when ffprobe does not print the side data.
    * **the profile name** - some builds write "Dolby Vision" right into
      `profile`.

    What this does not catch is an old ffmpeg over MKV: reading DV from
    Matroska needs a newer version, and an older one says nothing about it.
    """
    for block in (video.get("side_data_list") or []):
        kind = str(block.get("side_data_type") or "").lower()
        if "dovi" in kind or "dolby vision" in kind:
            return True
        if any(str(key).startswith("dv_") for key in block):
            return True

    if str(video.get("codec_tag_string") or "").strip().lower() in DV_MARKS:
        return True

    return "dolby vision" in str(video.get("profile") or "").lower()


def _detect_video_range(video: dict[str, Any]) -> str:
    """Estimates whether the video is SDR, HDR or Dolby Vision.

    It is an estimate from the colour metadata. A more precise classification
    would want a deeper analysis, but for the statistic "how much HDR content
    do I have" this is enough.
    """
    if _is_dolby_vision(video):
        return "DOVI"

    transfer = (video.get("color_transfer") or "").lower()
    if transfer in {"smpte2084", "arib-std-b67"}:
        return "HDR"

    pix_fmt = (video.get("pix_fmt") or "").lower()
    if "10le" in pix_fmt and transfer in {"bt2020-10", "bt2020_10"}:
        return "HDR"

    return "SDR"


def _to_int(value: Any) -> int | None:
    """A safe conversion to a whole number. ffprobe often returns numbers as text."""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
