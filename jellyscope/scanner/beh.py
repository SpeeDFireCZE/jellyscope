"""Běh úloh: zámek, zastavení, průběh a záznam o úloze (scan_log).

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import db

log = logging.getLogger("jellyscope.scanner")


# Zamek, ktery zajisti, ze nebezi dva scany naraz. Bez nej by dve soucasne
# spustene analyzy zbytecne zatezovaly disk a prepisovaly si vysledky.
_scan_lock = asyncio.Lock()


def is_scan_running() -> bool:
    return _scan_lock.locked()


def zamek_uloh() -> asyncio.Lock:
    """Zámek, pod kterým běží úlohy, které se nesmí potkat.

    Synchronizace, analýza souborů - a stažení podrobné mapy
    (mapa_podrobna.spust_ulohu), které by jinak se synchronizací soupeřilo
    o databázi i o paměť.
    """
    return _scan_lock


# ---------------------------------------------------------------------------
# Zastaveni bezici ulohy
# ---------------------------------------------------------------------------
#
# Uloha se **neprerusuje uprostred prace**. Nastavi se jen priznak a smycka
# si ho vsimne, az dodela rozdelanou polozku - teprve pak skonci.
#
# Proc takhle a ne task.cancel(): tvrde preruseni by mohlo prijit uprostred
# zapisu do databaze a nechat po sobe polovicni davku nebo neuzavrenou
# transakci. Takhle se skonci vzdycky na miste, kde je databaze v poradku.
# Uzivatel na to pocka nanejvys par sekund.
#
# Priznak je obycejna promenna modulu, stejne jako `_scan_lock` - obojí
# plati pro jeden bezici proces aplikace. Jellyscope bezi v jednom procesu
# (viz run.py), takze to staci; pri vice workerech by tohle prestalo platit
# stejne jako zamek sam.
_stop_requested = False


def request_stop() -> bool:
    """Poprosi bezici ulohu, aby skoncila. Vraci, jestli vubec neco bezi."""
    global _stop_requested
    if not is_scan_running():
        return False
    _stop_requested = True
    log.info("uloha dostala pokyn k zastaveni")
    return True


def stop_requested() -> bool:
    """Ceka bezici uloha na ukonceni?

    Podminka `is_scan_running()` tam neni navic. Priznak patri **te uloze,
    ktere byl nastaven** - kdyz skonci, uz nikomu nic nerika. Bez toho by
    Nastaveni po kazdem zastaveni navzdy hlasilo "zastavuji" a v testech
    by priznak pretekal z jedne ulohy do druhe.

    Uklidit ho pri odchodu z ulohy by znamenalo obalit celé telo funkce
    blokem try/finally kvuli jedne promenne. Takhle plati totez a je to
    videt na jednom miste.
    """
    return _stop_requested and is_scan_running()
# ---------------------------------------------------------------------------
# Prubeh bezici ulohy
# ---------------------------------------------------------------------------
#
# Synchronizace velke knihovny trva minuty a do ted o sobe nedavala vedet -
# uzivatel koukal na "uloha bezi" a nemel jak poznat, jestli je na zacatku
# nebo skoro hotova.
#
# Celkovy pocet zjistime predem: Jellyfin ho posila u kazde stranky jako
# `TotalRecordCount`, takze staci poprosit o stranku o velikosti nula. Pak
# uz jen pricitame, co je hotove.
#
# Drzime to v pameti, ne v databazi: je to udaj o **prave bezicim** procesu,
# ktery po skonceni nikoho nezajima. Zapisovat ho po kazde davce do databaze
# by znamenalo zapis navic bez uzitku.
_progress: dict[str, Any] = {"kind": None, "done": 0, "total": 0}


def progress() -> dict[str, Any]:
    """Kolik prace uz je hotovo. Prazdne, kdyz nic nebezi."""
    if not is_scan_running() or not _progress["kind"]:
        return {}
    hotovo, celkem = _progress["done"], _progress["total"]
    return {
        "kind": _progress["kind"],
        "done": hotovo,
        "total": celkem,
        # Procenta pocitame tady, at je sablona nemusi. A jen kdyz celkovy
        # pocet vubec zname - jinak by "0 %" lhalo o tom, ze nic nebezi.
        "percent": round(hotovo / celkem * 100) if celkem else None,
    }


def _start_progress(kind: str, total: int = 0) -> None:
    _progress.update(kind=kind, done=0, total=max(0, int(total or 0)))


def _add_progress(kolik: int) -> None:
    _progress["done"] += kolik


def _clear_progress() -> None:
    _progress.update(kind=None, done=0, total=0)


def _clear_stop() -> None:
    """Zahodi priznak. Vola se na zacatku kazde ulohy.

    Bez toho by pozdeji spustena uloha nasla priznak z te predchozi
    a hned by se ukoncila.
    """
    global _stop_requested
    _stop_requested = False


# ---------------------------------------------------------------------------
# Zaznam o prubehu scanu (aby bylo v UI videt, co se deje)
# ---------------------------------------------------------------------------

def start_task_log(kind: str) -> int:
    with db.connect() as conn:
        novy = conn.insert_returning_id(
            "INSERT INTO scan_log (kind, started_at, status) VALUES (?, ?, 'running')",
            (kind, db.utcnow()),
        )
        _proredit_zaznamy(conn, kind)
        return novy


def otisky() -> dict[str, str]:
    """Oba otisky naraz - knihovna i dobehle ulohy.

    Jeden dotaz, protoze se oba ptaji na tytez dve tabulky a chodi
    spolecne: sahá po nich **kazde volani /health**, na ktere se prohlizec
    pta kazdych deset vterin z kazde otevrene karty. Ve dvou dotazech to
    znamenalo dve spojeni do databaze misto jednoho - a rezie spojeni je
    tady vetsi nez prace samotna.

    `SELECT (poddotaz), (poddotaz)` bez FROM rozumi SQLite i PostgreSQL.
    """
    row = db.query_one(
        """
        SELECT (SELECT MAX(finished_at) FROM scan_log
                 WHERE kind IN ('library', 'recent'))      AS cas_knihovny,
               (SELECT COUNT(*) FROM items)                AS polozek,
               (SELECT MAX(id) FROM scan_log
                 WHERE finished_at IS NOT NULL)            AS uloh
        """
    ) or {}
    return {
        "library": f"{row.get('cas_knihovny') or ''}:{row.get('polozek') or 0}",
        "tasks": str(row.get("uloh") or 0),
    }


def library_version() -> str:
    """Otisk toho, jak knihovna vypada ted.

    Slouzi k jedine veci: prohlizec si ho zapamatuje pri nacteni stranky
    a kdyz se pozdeji zmeni, vi, ze ma znovu nacist pas nedavno pridanych.
    Bez toho by musel bud obnovovat naslepo porad dokola, nebo cekat, az
    stranku obnovi clovek sam - a nove pridany film by mu na uz otevrene
    strance nikdy nenaskocil.

    Skladame ho z posledni dokoncene ulohy a poctu titulu. Samotny cas
    nestaci: uloha, ktera nic nenasla, taky dobehne, a prekreslovat kvuli
    ni nema smysl.
    """
    return otisky()["library"]


def tasks_version() -> str:
    """Otisk toho, kolik uloh uz dobehlo.

    Stranka si ho zapamatuje pri nacteni a kdyz se zmeni, vi, ze nejaka
    uloha mezitim skoncila - a muze se obnovit.

    Proc ne "sleduj, jestli prave bezi uloha, a pockej az prestane":
    protoze rychla synchronizace nad malou knihovnou trva par vterin.
    Kdyz se stranka pta jednou za deset, cely beh se do mezery mezi dvema
    dotazy pohodlne vejde - uloha probehne, stranka o tom nikdy nevi
    a prouzek "uloha bezi" na ni zustane viset. Presne to se stalo.

    `MAX(id)` staci: cislo radku roste s kazdou dalsi ulohou, takze se
    nemusime spolehat na cas (dve ulohy muzou skoncit ve stejne vterine).
    """
    return otisky()["tasks"]


def finish_task_log(
    scan_id: int,
    status: str,
    total: int = 0,
    ok: int = 0,
    failed: int = 0,
    message: str = "",
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE scan_log
               SET finished_at = ?, status = ?, items_total = ?,
                   items_ok = ?, items_failed = ?, message = ?
             WHERE id = ?
            """,
            (db.utcnow(), status, total, ok, failed, message[:500], scan_id),
        )


# Kolik zaznamu o jednom druhu ulohy si necháváme.
#
# Rychla synchronizace bezi kazdych patnact minut, takze do tabulky
# pribyva pres devadesat radku denne - a nikdy se nic nemazalo. Za rok
# by jich bylo pres tricet tisic a kazdy dotaz na /health (prohlizec se
# pta kazdych deset vterin z kazde otevrene karty) by je musel projit.
#
# Tri sta staci: v Nastaveni se ukazuje posledni beh kazdeho druhu,
# zbytek je historie pro pripad, ze by se neco vysetrovalo.
ZAZNAMU_NA_DRUH = 300


def _proredit_zaznamy(conn: Any, kind: str) -> None:
    """Necha jen poslednich `ZAZNAMU_NA_DRUH` zaznamu daneho druhu.

    Hranici hledame dotazem, ne poctem: `DELETE ... LIMIT` umi SQLite,
    ale PostgreSQL ne, a tenhle tvar rozumi obema.
    """
    row = conn.execute(
        "SELECT id FROM scan_log WHERE kind = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
        (kind, ZAZNAMU_NA_DRUH - 1),
    ).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM scan_log WHERE kind = ? AND id < ?",
                 (kind, row["id"]))


def last_scan(kind: str) -> dict[str, Any] | None:
    return db.query_one(
        "SELECT * FROM scan_log WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
    )
