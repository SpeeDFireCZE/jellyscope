"""Malé JSON rozhraní: /health a /api/now-playing.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from .. import __version__, collector, db, pristup, scanner, stats
from .zaklad import STARTED_AT, current_account, require_login

router = APIRouter()


# ---------------------------------------------------------------------------
# Male JSON rozhrani (hodi se na kontrolu, ze aplikace zije)
# ---------------------------------------------------------------------------


@router.get("/health")
def health(request: Request):
    """Stav aplikace. Stránka se sem vrací každých deset vteřin.

    Bez přihlášení odpoví jen "žiju" a kdy nastartovala - na to se ptá
    monitoring (a čekání po restartu, kdy přihlášení ještě neplatí).
    Zbytek, tedy co se právě hraje a jak velká je knihovna, je údaj
    o obsahu serveru a patří až za přihlášení.
    """
    zaklad = {"status": "ok", "started_at": int(STARTED_AT)}
    ucet = current_account(request)
    if ucet is None:
        return zaklad

    otisk = scanner.otisky()
    return {
        **zaklad,
        # Verze beziciho procesu. Cekarna po aktualizaci na ni ceka: je to
        # primo ta otazka, na kterou se pta ("bezi uz nova verze?"), kdezto
        # `started_at` odpovida jen "neco se restartovalo". Neprihlasenemu
        # ji nerikame - monitoringu staci, ze aplikace zije.
        "version": __version__,
        "collector": db.get_setting(collector.STATUS_KEY, "unknown"),
        "last_poll": db.get_setting(collector.LAST_POLL_KEY, ""),
        # Kolik se právě sleduje - jen s právem „Právě se hraje".
        **({"active_sessions": stats.active_session_count()} if pristup.vidi_ucet(ucet, "prave_hraje") else {}),
        "task_running": scanner.is_scan_running(),
        "stop_pending": scanner.stop_requested(),
        "progress": scanner.progress(),
        # Otisk knihovny (Přehled si ho hlídá, aby po doběhnutí
        # synchronizace sám ukázal nově přidané tituly) a otisk úloh
        # (podle jeho změny se pozná, že úloha skončila - i když celá
        # proběhla mezi dvěma dotazy). Obojí jedním dotazem, protože
        # sem se prohlížeč vrací každých deset vteřin.
        "library_version": otisk["library"],
        "tasks_version": otisk["tasks"],
    }


@router.get("/api/now-playing")
def api_now_playing(account: dict[str, Any] = Depends(require_login)):
    """Právě běžící přehrávání jako JSON pro přihlášeného (mimo API s klíči).

    Se skrytými jmény anonymizované jako stránka „Právě se hraje" - dřív
    tu byla surová data i se jmény, zařízeními a IP adresami.
    """
    relace = stats.active_sessions()
    # Odkud se kdo připojuje patří Síti, ne „Právě se hraje".
    if not pristup.vidi_ucet(account, "sit"):
        relace = [{k: v for k, v in r.items() if k not in pristup.ADRESY} for r in relace]
    return pristup.anonymizuj_pro({"sessions": relace}, account)
