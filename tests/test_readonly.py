# -*- coding: utf-8 -*-
"""Jellyscope do Jellyfinu jen čte.

Tenhle test hlídá slib, na kterém celá aplikace stojí: **Jellyfin se nikdy
nemění**. Statistiky se dají spočítat i bez zápisu, takže není důvod si na
cizí server sahat — a kdyby se do kódu jednou zápis dostal, má to shodit
test, ne rozbít někomu knihovnu.

Kontroluje se trojí:
  1. klient nemá jinou HTTP metodu než GET,
  2. každá adresa, která se v kódu objeví, je v seznamu čtecích,
  3. SQL posílané pluginu Playback Reporting projde pojistkou `jen_cteni`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope.importers import PBR_QUERY, ImportError_, jen_cteni  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


BALICEK = PROJECT / "jellyscope"
SOUBORY = ("jellyfin.py", "scanner.py", "collector.py", "importers.py")


def kod(nazev: str) -> str:
    """Zdroják bez komentářů - jde o to, co se volá, ne co je popsané."""
    text = (BALICEK / nazev).read_text(encoding="utf-8")
    return "\n".join(
        "" if radek.strip().startswith("#") else radek
        for radek in text.splitlines()
    )


print("--- klient umí jen GET ---")
klient = kod("jellyfin.py")
for metoda in ("post", "put", "delete", "patch"):
    check(f"_client.{metoda}(" not in klient,
          f"jellyfin.py nevolá _client.{metoda}()")
check(klient.count("async def _get") == 1, "existuje jediná odesílací funkce")
check("await self._client.get(" in klient, "stahování obrázků jde taky přes GET")


print()
print("--- adresy, které se v kódu objevují ---")
# Sbíráme VŠECHNY textové adresy, ne jen ty psané přímo do _get():
# některé se skládají do proměnné (path = f"/Users/{id}/Items") a kontrola
# koukající jen na volání by je minula.
CTECI_ADRESY = {
    "/System/Info",                     # verze serveru, kontrola spojení
    "/Users",                           # seznam uživatelů
    "/Sessions",                        # co se právě hraje
    "/Library/VirtualFolders",          # seznam knihoven
    "/Items",                           # položky knihovny
    "/Users/{user_id}/Items",           # položky očima jednoho uživatele
    "/Items/{item_id}/Images/{kind}",   # plakát
    # Dotazovací rozhraní pluginu Playback Reporting. Jediné místo, kde se
    # posílá POST - SQL jde v těle požadavku a hlídá ho jen_cteni().
    "/user_usage_stats/submit_custom_query",
}

nalezene: set[str] = set()
for soubor in SOUBORY:
    for adresa in re.findall(r'f?"(/[A-Za-z][^"\s]*)"', kod(soubor)):
        nalezene.add(adresa.split("?")[0])

navic = sorted(nalezene - CTECI_ADRESY)
check(not navic, f"žádná neznámá adresa (navíc: {navic})")
print(f"       nalezené adresy: {sorted(nalezene)}")

# Adresy, kterými se Jellyfin ovládá nebo mění. Ani jedna se nesmí
# objevit ani jako text - i kdyby ji dnes nikdo nezavolal.
MENICI_ADRESY = (
    "/Playing", "/Library/Refresh", "/ScheduledTasks/Running",
    "/System/Restart", "/System/Shutdown", "/Users/New",
    "/Plugins/", "/Packages/", "/Items/Delete", "/Sessions/Logout",
    "/Users/AuthenticateByName", "/Startup/",
)
vsechen_kod = "\n".join(kod(soubor) for soubor in SOUBORY)
for adresa in MENICI_ADRESY:
    check(adresa not in vsechen_kod, f"nikde se neobjevuje {adresa}")


print()
print("--- plugin Playback Reporting: jen SELECT ---")
# Tohle je jediné místo s POST. POST sám o sobě nic nemění - mění to,
# co se v něm pošle. Proto ta pojistka.
importery = kod("importers.py")
posty = re.findall(r"_client\.post\(", importery)
check(len(posty) == 2, f"POST je jen na dvou místech (je {len(posty)})")
check(importery.count("jen_cteni(") >= 3,
      "obě volání procházejí pojistkou jen_cteni()")

check(jen_cteni("SELECT * FROM PlaybackActivity") == "SELECT * FROM PlaybackActivity",
      "SELECT projde")
check(jen_cteni("  select  a  from  b  ;") == "select a from b",
      "zbytečné mezery i koncový středník se srovnají")

for zly in [
    "DELETE FROM PlaybackActivity",
    "DROP TABLE PlaybackActivity",
    "UPDATE PlaybackActivity SET x = 1",
    "INSERT INTO PlaybackActivity VALUES (1)",
    "SELECT 1; DROP TABLE PlaybackActivity",
    "SELECT 1; DELETE FROM x",
    "PRAGMA journal_mode = WAL",
    "ATTACH DATABASE '/etc/passwd' AS p",
    "VACUUM",
    "CREATE TABLE x (a int)",
    "REPLACE INTO x VALUES (1)",
]:
    try:
        jen_cteni(zly)
        prosel = True
    except ImportError_:
        prosel = False
    check(not prosel, f"odmítne: {zly[:45]}")

try:
    jen_cteni(PBR_QUERY)
    ok_dotaz = True
except ImportError_ as exc:
    ok_dotaz = False
    print("       ", exc)
check(ok_dotaz, "skutečný dotaz na plugin pojistkou projde")


print()
print("--- zápis míří jen do vlastní databáze ---")
check("httpx" not in kod("db.py"), "db.py vůbec nemluví po síti")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
