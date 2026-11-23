# -*- coding: utf-8 -*-
r"""Collections and playlists are no disk either (the final review of 23. 11. 2026).

Jellyfin's /System/Info/Storage lists the libraries from the same place as /Library/VirtualFolders - the
Collections folder included (verified on Jellyfin 12.1.0: the same Id, its own DeviceId, the configuration
folder). The library sync stopped storing such folders as libraries, but the disks came from the storage answer,
so a "Collections" disk appeared and its free space entered the sum under Library growth "All". The test runs a
whole library sync against a fake Jellyfin and watches:

A) a storage library the library sync did not store (Collections, Playlists) gives no disk and no free space;
B) a real library stays, also when the storage answer writes its Id with dashes and capitals;
C) a storage library without an Id stays (an answer we cannot pair says nothing against it).

Run:
    .venv/bin/python tests/test_disks_without_collections.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "disks-without-collections.db")
os.environ["SECRET_KEY"] = "test-key-disks-without-collections"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _package import substitute  # noqa: E402
from jellyscope import db, scanner  # noqa: E402

failures = 0
GB = 1024 ** 3
db.init_db()
db.set_setting(scanner.CONVERTED_KEY, "1")


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def folder(path: str, device: str, free: int) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": 100 * GB, "StorageType": "Fixed", "DeviceId": device}


FOLDERS = [{"ItemId": "f137a2dd21bbc1b99aa5c0f6bf02a805", "Name": "Movies", "CollectionType": "movies",
            "Locations": ["/media/movies"]},
           {"ItemId": "9d7ad6afe9afa2dab1a2f6e00ad28fa6", "Name": "Collections", "CollectionType": "boxsets",
            "Locations": ["/config/data/collections"]},
           {"ItemId": "5c1e0b2a0d0e4f7a9d6b3e2f1a0b9c8d", "Name": "Playlists", "CollectionType": "playlists",
            "Locations": ["/config/data/playlists"]}]
STORAGE: dict[str, Any] = {"Libraries": [
    # The movies' Id with dashes and capitals - the same library as in VirtualFolders.
    {"Name": "Movies", "Id": "F137A2DD-21BB-C1B9-9AA5-C0F6BF02A805", "Folders": [folder("/media/movies", "/mnt/media", 800 * GB)]},
    {"Name": "Collections", "Id": "9d7ad6afe9afa2dab1a2f6e00ad28fa6",
     "Folders": [folder("/config/data/collections", "/config", 20 * GB)]},
    {"Name": "Playlists", "Id": "5c1e0b2a0d0e4f7a9d6b3e2f1a0b9c8d",
     "Folders": [folder("/config/data/playlists", "/config", 20 * GB)]}]}


class FakeJellyfin:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeJellyfin":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": "u1", "Name": "Jana", "Policy": {"IsAdministrator": False}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [dict(f) for f in FOLDERS]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        return 0

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        return
        yield

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        return []

    async def recent_items(self, since: Any, cap: int = 2000, parent_id: str | None = None) -> list[dict[str, Any]]:
        return []

    async def storage(self) -> dict[str, Any]:
        return STORAGE


substitute(scanner, "JellyfinClient", FakeJellyfin)
result = asyncio.run(scanner.sync_library())
check(result.get("status") == "ok", f"(the sync passes: {result.get('status')})")


print("--- A) Collections and Playlists give no disk ---")
db.forget_settings()
check([disk["key"] for disk in scanner.physical_disks()] == ["/mnt/media"],
      f"only the movies' disk ({[disk['key'] for disk in scanner.physical_disks()]})")
check(scanner.free_space_now() == 800 * GB, f"the free space is the movies' disk only ({scanner.free_space_now()})")


print()
print("--- B, C) a real library stays; one without an Id stays ---")
check(scanner.physical_disks()[0]["libraries"] == ["Movies"], "the movies' disk carries its library")
STORAGE["Libraries"].append({"Name": "Old", "Folders": [folder("/media/old", "/mnt/old", 300 * GB)]})
asyncio.run(scanner.sync_library())
db.forget_settings()
check(sorted(disk["key"] for disk in scanner.physical_disks()) == ["/mnt/media", "/mnt/old"],
      "a storage library without an Id stays (nothing to pair it with)")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
