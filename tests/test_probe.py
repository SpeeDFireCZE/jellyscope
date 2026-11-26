# -*- coding: utf-8 -*-
r"""Running ffprobe on one file (probe.probe_file): an ffprobe that hangs does not stay behind.

A file on a stalled network share (path mappings to NFS or SMB) or a damaged one can keep ffprobe busy past
the time limit. The analysis gives that file up and goes on - and ffprobe must end with it, otherwise every
such file leaves one process running, outside the count of the semaphore, until the service stops. The same
when the analysis is cancelled (another file failed, the task ended). Looking at the file on the disk runs
outside the event loop: a share that hangs must not stop the web and the collector with it.

Run:
    .venv/bin/python tests/test_probe.py
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "probe.db")
os.environ["SECRET_KEY"] = "probe-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"

from jellyscope import probe  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


MOVIE = Path(_tmp) / "movie.mkv"
MOVIE.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
PID_FILE = Path(_tmp) / "ffprobe.pid"


def fake_ffprobe(name: str, body: str) -> str:
    """A stand-in for ffprobe: a shell script that writes its process id and then does `body`."""
    script = Path(_tmp) / name
    script.write_text(f"#!/bin/sh\necho $$ > '{PID_FILE}'\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def started_pid() -> int:
    for _ in range(100):
        if PID_FILE.is_file() and PID_FILE.read_text().strip():
            return int(PID_FILE.read_text())
        time.sleep(0.05)
    return 0


def running(pid: int) -> bool:
    """Is the process still there (a reaped one is gone; a killed but not reaped one is a zombie)?"""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as file:
            return file.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def end(pid: int) -> None:
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


if sys.platform == "win32" or not Path("/proc").is_dir():
    print("SKIP   needs a shell script as ffprobe and /proc")
    print()
    print(f"DONE - failures: {failures}")
    sys.exit(0)

print("--- ffprobe past the time limit ---")
HANGING = fake_ffprobe("hanging-ffprobe", "exec sleep 30")
probe.PROBE_TIMEOUT_SECONDS = 1


async def timed_out() -> Exception | None:
    try:
        await probe.probe_file(str(MOVIE), HANGING)
    except probe.ProbeError as error:
        return error
    return None


PID_FILE.unlink(missing_ok=True)
error = asyncio.run(timed_out())
pid = started_pid()
check(error is not None and "did not answer" in error.template, f"the file is given up with a message ({error})")
check(pid and not running(pid), f"and ffprobe ended with it (process {pid} running: {running(pid)})")
end(pid)

print()
print("--- the analysis cancelled while ffprobe runs ---")
probe.PROBE_TIMEOUT_SECONDS = 60


async def cancelled() -> None:
    job = asyncio.ensure_future(probe.probe_file(str(MOVIE), HANGING))
    await asyncio.sleep(0.5)
    job.cancel()
    try:
        await job
    except asyncio.CancelledError:
        pass


PID_FILE.unlink(missing_ok=True)
asyncio.run(cancelled())
pid = started_pid()
check(pid and not running(pid), f"ffprobe ends with the cancelled analysis (process {pid} running: {running(pid)})")
end(pid)

print()
print("--- looking at the file runs outside the event loop ---")
ANSWERING = fake_ffprobe("answering-ffprobe",
                         "echo '{\"format\": {\"format_name\": \"matroska,webm\"}, \"streams\": []}'")
on_loop: list[str] = []
real_is_file, real_stat = pathlib.Path.is_file, pathlib.Path.stat


def watched_is_file(self, *args, **kwargs):
    if str(self) == str(MOVIE) and threading.current_thread() is threading.main_thread():
        on_loop.append("is_file")
    return real_is_file(self, *args, **kwargs)


def watched_stat(self, *args, **kwargs):
    if str(self) == str(MOVIE) and threading.current_thread() is threading.main_thread():
        on_loop.append("stat")
    return real_stat(self, *args, **kwargs)


pathlib.Path.is_file, pathlib.Path.stat = watched_is_file, watched_stat
try:
    summary = asyncio.run(probe.probe_file(str(MOVIE), ANSWERING))
finally:
    pathlib.Path.is_file, pathlib.Path.stat = real_is_file, real_stat
check(summary["container"] == "matroska" and summary["size_bytes"] == MOVIE.stat().st_size,
      f"the answer is read, the size from the disk ({summary['container']}, {summary['size_bytes']})")
check(not on_loop, f"no look at the disk on the event loop's thread ({on_loop})")

print()
print("--- a file on a stalled share ends the analysis ---")
# A hard-mounted NFS or SMB share that stalls hangs the look at the file for good: the analysis held its lock and its
# places in the semaphore, Stop did not reach it, and the night's sync ended with "Another task is already running"
# until a restart. Giving the one file up would not help - the next one lies on the same share.
from jellyscope import db, scanner  # noqa: E402

releases: list[threading.Event] = []
timers: list[threading.Timer] = []


def stalling() -> None:
    """A share that stalls from now on - each case its own, which the looks of the case wait for."""
    releases.append(threading.Event())
    timers.append(threading.Timer(20, releases[-1].set))     # whatever happens, the stalled looks return in the end
    timers[-1].start()


def stalled_is_file(self, *args, **kwargs):
    if str(self).startswith("/stalled-share/"):
        releases[-1].wait()
        return True
    return real_is_file(self, *args, **kwargs)


async def bounded(work):
    return await asyncio.wait_for(work, 8)


db.init_db()
with db.connect() as conn:
    for number in range(5):
        conn.execute("INSERT INTO items (id, name, type, path, is_missing, synced_at) VALUES (?, ?, 'Movie', ?, 0, ?)",
                     (f"stalled-{number}", f"Stalled {number}", f"/stalled-share/movie-{number}.mkv", db.utcnow()))
db.set_setting("tech_source", "ffprobe")
db.set_setting("ffprobe_path", ANSWERING)
real_limit = probe.PROBE_TIMEOUT_SECONDS
probe.PROBE_TIMEOUT_SECONDS = 0.5
pathlib.Path.is_file = stalled_is_file
try:
    stalling()
    started = time.monotonic()
    try:
        alone: object = asyncio.run(bounded(probe.probe_file("/stalled-share/movie-0.mkv", ANSWERING)))
    except Exception as error:  # noqa: BLE001 - what it ends with is the point
        alone = error
    alone_took = time.monotonic() - started
    stalling()
    started = time.monotonic()
    try:
        outcome: object = asyncio.run(bounded(scanner.run_tech_scan(only_missing=True)))
    except Exception as error:  # noqa: BLE001 - the old behaviour, measured
        outcome = error
    took = time.monotonic() - started
finally:
    pathlib.Path.is_file = real_is_file
    probe.PROBE_TIMEOUT_SECONDS = real_limit
check(isinstance(alone, Exception) and not isinstance(alone, (probe.ProbeError, TimeoutError)) and alone_took < 5,
      f"the look gives up after its limit, with an error that is not one file's ({alone!r}, {alone_took:.1f} s)")
record = scanner.last_scan("tech") or {}
check(isinstance(outcome, dict) and outcome.get("status") == "error" and took < 5,
      f"the analysis ends with an error ({outcome!r:.120}, {took:.1f} s)")
check(record.get("status") == "error" and bool(record.get("finished_at")) and not scanner.is_scan_running(),
      f"its run is closed as failed and the lock is free ({record.get('status')}, {record.get('message')!r:.80})")
for release, timer in zip(releases, timers):
    release.set()
    timer.cancel()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
