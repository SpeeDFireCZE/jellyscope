# -*- coding: utf-8 -*-
r"""Živý tok na stránce Síť a špičky po dnech.

Dřív měla Síť jediný graf: souběžný tok za celé období, rozdělený na
sto dvacet úseků. U měsíce to znamenalo jeden bod na pět hodin - a v něm
se hledala špička. Kdo se chtěl podívat, co teče **teď**, se to nedozvěděl
vůbec; a křivka vypadala náhodně, protože jeden vrchol mohl být jediná
hodina ze šesti.

Teď jsou to dvě různé otázky a dvě různé karty:

* **Právě teče** - součet toků běžících přehrávání a poslední hodina po
  minutách. Obnovuje se sama, stejným mechanismem jako „právě se hraje".
* **Špička po dnech** - jedno číslo na den, to nejvyšší.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_zivy_tok.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "tok.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
TED = datetime.now(timezone.utc)


def prehravani(klic: str, pred_minutami: int, delka_minut: int, mbit: int,
               aktivni: int = 0, pauza: int = 0, videno_pred: int | None = None) -> None:
    zacatek = TED - timedelta(minutes=pred_minutami)
    konec = zacatek + timedelta(minutes=delka_minut)
    videno = TED - timedelta(minutes=videno_pred) if videno_pred is not None else konec
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds,"
            " bitrate, is_paused, is_active, play_method, remote_address, client, device_name)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (klic, "u1", "Pepa", "film", "Duna",
             zacatek.strftime(db.TIME_FORMAT), videno.strftime(db.TIME_FORMAT),
             None if aktivni else konec.strftime(db.TIME_FORMAT),
             delka_minut * 60, 0, mbit * 1_000_000, pauza, aktivni,
             "Transcode" if mbit > 15 else "DirectPlay", "10.0.0.5", "Web", "Chrome"))


print("--- co teče právě teď ---")
prehravani("bezi-1", pred_minutami=30, delka_minut=30, mbit=20, aktivni=1)
prehravani("bezi-2", pred_minutami=10, delka_minut=10, mbit=8, aktivni=1)
prehravani("pauza", pred_minutami=40, delka_minut=5, mbit=12, aktivni=1, pauza=1)
prehravani("davno", pred_minutami=600, delka_minut=60, mbit=30)

ted = stats.tok_ted()
check(ted["mbit"] == 28.0, f"sečte jen běžící ({ted['mbit']} Mbit/s)")
check(ted["streamu"] == 2, f"a spočítá je ({ted['streamu']})")
check(ted["pozastavenych"] == 1, "pozastavené uvede zvlášť")
check(ted["prepoctu"] == 1, "i kolik z nich je přepočet")

print()
print("--- živá křivka ---")
zive = stats.bandwidth_zive(None, bodu=144)
check(len(zive) == 144, f"tolik bodů, kolik se řeklo ({len(zive)})")
check(zive[-1]["mbit"] == 28.0, f"poslední bod odpovídá tomu, co teče ({zive[-1]['mbit']})")
check(zive[5]["mbit"] == 0.0, f"před hodinami nic ({zive[5]['mbit']})")

print()
print("--- pozastavené se do křivky nepočítá ---")
# Při pauze nic neteče. Poznat to jde jedině z toho, co hlásí sběrač
# o běžících přehráváních - a přesně podle toho se to tady řídí:
# pozastavený stream končí tam, kde se naposledy hrálo, a dál už do
# křivky nepřispívá.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
prehravani("pauza-ted", pred_minutami=90, delka_minut=30, mbit=25, aktivni=1, pauza=1)
zive = stats.bandwidth_zive(None, bodu=144)
check(zive[-1]["mbit"] == 0.0, f"teď z něj neteče nic ({zive[-1]['mbit']})")
check(max(b["mbit"] for b in zive) == 25.0,
      "ale doba, kdy se hrálo, v křivce zůstala")
check(stats.tok_ted()["mbit"] == 0.0, "a do čísla vedle křivky se nepočítá")
check(stats.tok_ted()["pozastavenych"] == 1, "jen se řekne, že tam je")

print()
print("--- okno se řídí filtrem, nejméně jeden den ---")
# Hodinové okno vypadalo rozbitě: večer, kdy se hrálo celou dobu, řeklo
# "nic neteče", protože zrovna doběhl poslední díl.
def hodin(obdobi) -> float:
    body = stats.bandwidth_zive(obdobi, bodu=180)
    return (body[-1]["cas"] - body[0]["cas"]) / 3600

check(23 <= hodin(None) <= 25, f"bez filtru den ({hodin(None):.1f} h)")
check(160 <= hodin(7) <= 170, f"sedm dnů ({hodin(7):.1f} h)")
check(700 <= hodin(30) <= 725, f"třicet dnů ({hodin(30):.1f} h)")
# Krátké vlastní období se nesmí scvrknout pod den.
kratke = stats.obdobi_od_do("2026-08-20", "2026-08-20")
check(23 <= hodin(kratke) <= 25, f"jeden den zůstane dnem ({hodin(kratke):.1f} h)")

print()
print("--- popisek podle délky okna ---")
# Do dvou dnů staci hodina a minuta; pres dva dny musi byt videt datum,
# jinak se streda a ctvrtek na ose nerozlisi.
check(":" in stats.bandwidth_zive(None, bodu=24)[0]["popisek"], "krátké okno: čas")
check("." in stats.bandwidth_zive(30, bodu=24)[0]["popisek"], "dlouhé okno: i datum")

print()
print("--- běžící přehrávání patří do okna vždy ---")
# Sběrač se nemusel ozvat celou hodinu (Jellyfin chvíli neodpovídal),
# ale stream pořád běží. Kdyby vypadl z okna, křivka by spadla na nulu
# a číslo vedle ní by ukazovalo plný tok.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
prehravani("dlouho-bezi", pred_minutami=300, delka_minut=300, mbit=15,
           aktivni=1, videno_pred=120)
zive = stats.bandwidth_zive(None, bodu=144)
check(zive[-1]["mbit"] == 15.0, f"tok se počítá dál ({zive[-1]['mbit']})")

print()
print("--- špička po dnech ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
# Dva streamy zároveň včera, jeden dnes.
prehravani("vcera-a", pred_minutami=26 * 60, delka_minut=60, mbit=10)
prehravani("vcera-b", pred_minutami=26 * 60, delka_minut=60, mbit=20)
prehravani("dnes", pred_minutami=120, delka_minut=30, mbit=12)

dny = stats.bandwidth_denni_spicky(7)
check(len(dny) == 2, f"dva dny ({len(dny)})")
if len(dny) == 2:
    check(dny[0]["mbit"] == 30.0, f"včera se sečetly souběžné ({dny[0]['mbit']})")
    check(dny[1]["mbit"] == 12.0, f"dnes jen jeden ({dny[1]['mbit']})")

print()
print("--- varianty přepočtu mají rozlišitelné barvy ---")
# Odstinovani jedne barvy tu bylo drive a v uzkem pruhu splynulo.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    for i, metoda in enumerate(("DirectPlay", "Transcode",
                                "Transcode (v:h264 a:direct)",
                                "Transcode (v:direct a:aac)")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " started_at, last_seen_at, watched_seconds, paused_seconds, play_method,"
            " bitrate, is_paused, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0)",
            (f"m{i}", "u1", "Pepa", "film", "Duna",
             (TED - timedelta(hours=2)).strftime(db.TIME_FORMAT),
             (TED - timedelta(hours=1)).strftime(db.TIME_FORMAT),
             3600, 0, metoda, 10_000_000))

zpusoby = stats.play_method_breakdown(7)
role = {r["method"]: r["role"] for r in zpusoby}
check(role.get("Transcode (v:h264 a:direct)") == "serious",
      "podrobná varianta se pozná jako přepočet")
barvy = [r.get("barva") or r["role"] for r in zpusoby]
check(len(set(barvy)) == len(barvy), f"a každá má svou barvu ({barvy})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
