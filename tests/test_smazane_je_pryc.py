# -*- coding: utf-8 -*-
r"""Co se smaže, zmizí i ze souboru - ne jen z pohledu aplikace.

`DELETE` v SQLite řádek jen vyřadí ze seznamu na stránce. Jeho bajty
zůstanou ležet, dokud je něco nepřepíše, takže „smazaná" historie se dá
ze souboru přečíst dál. Jednou to zachránilo data smazaná omylem - a je
to zároveň důvod, proč se na „smazáno" nedalo spolehnout.

Proto se po zásazích, které mažou schválně, databáze přeskládá
(`db.preskladat()`). Test se neptá aplikace, jestli tam data jsou -
ta by řekla „nejsou" v obou případech. Ptá se **souboru**: hledá v jeho
bajtech jméno diváka.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_smazane_je_pryc.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
SOUBOR = str(Path(_tmp) / "stopa.db")
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = SOUBOR
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, odklizeni  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def stopy(znamka: str) -> int:
    """Kolikrát je ten text v souborech databáze - včetně deníku WAL."""
    celkem = 0
    for pripona in ("", "-wal", "-shm"):
        cesta = Path(SOUBOR + pripona)
        if cesta.exists():
            celkem += cesta.read_bytes().count(znamka.encode())
    return celkem


def naplnit(user_id: str, znamka: str, kolik: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (user_id, znamka, db.utcnow()))
        for poradi in range(kolik):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,0)",
                (f"{user_id}-{poradi}", user_id, znamka, f"i-{poradi}",
                 "2020-01-01 10:00:00", "2020-01-01 10:20:00", 1200))
        conn.commit()


db.init_db()

# Jmeno schvalne neobvykle - v souboru se nemuze objevit nahodou.
ZNAMKA = "MirekNeobvykleJmeno"
naplnit("u-mirek", ZNAMKA, 200)

print("--- zapomenutí diváka ---")
pred = stopy(ZNAMKA)
check(pred > 100, f"než se maže, je jméno v souboru {pred}x")

vysledek = odklizeni.zapomen_uzivatele("u-mirek")
check(vysledek["smazano"] == 200, f"smazalo se 200 záznamů ({vysledek['smazano']})")

po = stopy(ZNAMKA)
# Jedna stopa zustava: jmeno v tabulce `users`. Ucet je v Jellyfinu,
# odsud se nemaze - viz zapomen_uzivatele().
check(po <= 1, f"po zapomenutí zbylo v souboru {po} stop (účet v users)")
check(db.query_value("SELECT COUNT(*) FROM users WHERE id = ?",
                     ("u-mirek",)) == 1, "a účet v seznamu zůstal")

print()
print("--- odklízení podle stáří ---")
STARA = "JanaNeobvykleJmeno"
naplnit("u-jana", STARA, 150)
check(stopy(STARA) > 100, "než se maže, je jméno v souboru")

# Zaznamy jsou z roku 2020, takze je odklidi i nejkratsi retence.
odklid = odklizeni.smaz_stare(odklizeni.MIN_DNU)
check(odklid["smazano"] == 150, f"smazalo se 150 záznamů ({odklid['smazano']})")
zbylo = stopy(STARA)
check(zbylo <= 1, f"a po nich zbylo v souboru {zbylo} stop")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
