# -*- coding: utf-8 -*-
"""Čtecí API pro nástroje jako Grafana nebo Homepage.

Je to **zdroj čísel, ne druhá cesta do dat**. Proto:

* **Jen čtení.** Pod `/api/` nevede žádná routa, která by něco měnila -
  ne jako pravidlo v hlavě, ale doslova: jsou tu samá `GET`. Cokoliv
  jiného vrátí 405.
* **Hotové přehledy, ne dotazovací jazyk.** Pár adres, které odpovídají
  na otázky, kvůli kterým si člověk dashboard staví. Obecné API nad
  statistikami by znamenalo držet navěky tvar každého vnitřního dotazu.
* **Token v hlavičce, nikdy v adrese.** Adresa se objeví v logu proxy,
  v historii prohlížeče a v odkazu, který někdo pošle dál. Hlavička ne.

Tokeny
------

Ukládá se **otisk**, ne token - stejně jako u hesel. Kdo si přečte
databázi, nedostane přístup; komu token unikne, ten ho v Nastavení
zneplatní a vyrobí nový, aniž by se to dotklo ostatních.

Otisk je SHA-256, ne PBKDF2 jako u hesel, a je to schválně: token si
nikdo nevymýšlí, vyrábí ho `secrets.token_urlsafe(32)` - 256 bitů
náhody. Pomalý otisk chrání před hádáním slabého hesla; tady není co
hádat a každý požadavek by se o to zpomalil.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from . import db, formatting, scanner, stats

log = logging.getLogger("jellyscope.api")

# Predpona, at je na prvni pohled videt, co to je - v logu, v konfiguraci
# dashboardu i v cizim repozitari, kam se omylem dostane.
PREDPONA = "js_"

# Kolik znaku tokenu se ukazuje v Nastaveni - vcetne predpony. Zbytek
# je za hvezdickami a nikde ho uz neni odkud vzit: ulozeny je jen otisk.
# Pet znaku je z nahodne casti dvojice; rozeznat tokeny od sebe se ma
# podle jmena, ne podle zacatku klice.
UKAZKA_ZNAKU = 5

# Jak casto se prepisuje "naposledy pouzit". Grafana se pta kazdych par
# vterin a zapis pri kazdem dotazu je zbytecny; na otazku "zije tenhle
# token jeste?" staci minuta.
ZAPIS_POUZITI_PO = timedelta(minutes=1)

router = APIRouter(prefix="/api/v1", tags=["api"])


def _otisk(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def vytvor(jmeno: str) -> dict[str, Any]:
    """Vyrobí token. Vrátí ho v čitelné podobě - naposledy.

    Ukládá se jen otisk, takže tuhle hodnotu už podruhé nikdo nezjistí.
    Stránka to říká nahlas; token, který jde přečíst kdykoliv, je jen
    delší heslo v databázi.
    """
    jmeno = (jmeno or "").strip()[:60] or "bez názvu"
    token = PREDPONA + secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO api_tokens (name, token_hash, ukazka, created_at)"
            " VALUES (?, ?, ?, ?)",
            (jmeno, _otisk(token), token[:UKAZKA_ZNAKU], db.utcnow()),
        )
        conn.commit()
    log.info("Vyroben API token %s", jmeno)
    return {"token": token, "jmeno": jmeno}


def seznam() -> list[dict[str, Any]]:
    """Tokeny pro výpis v Nastavení. Bez otisků - ty tam nemají co dělat."""
    return db.query_all(
        "SELECT id, name, ukazka, created_at, last_used_at"
        "  FROM api_tokens ORDER BY id DESC"
    )


def zrus(token_id: int) -> str:
    """Zneplatní token. Vrátí jeho jméno, ať má hláška co říct."""
    radek = db.query_one("SELECT name FROM api_tokens WHERE id = ?", (token_id,))
    if not radek:
        return ""
    with db.connect() as conn:
        conn.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
        conn.commit()
    log.info("Zrusen API token %s", radek["name"])
    return str(radek["name"])


def _oznac_pouziti(radek: dict[str, Any]) -> None:
    """Zapíše „naposledy použit" - ale ne při každém dotazu."""
    naposledy = radek.get("last_used_at")
    if naposledy:
        try:
            kdy = datetime.strptime(str(naposledy), db.TIME_FORMAT).replace(
                tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - kdy < ZAPIS_POUZITI_PO:
                return
        except ValueError:
            pass
    with db.connect() as conn:
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                     (db.utcnow(), radek["id"]))
        conn.commit()


def token_z_hlavicky(request: Request) -> str:
    """Token z `Authorization: Bearer ...`, nebo prázdno.

    Podporuje se jedna hlavička, ne tři způsoby. Víc cest dovnitř
    znamená víc míst, kde se dá udělat chyba - a `Bearer` je to, co
    každý nástroj umí sám od sebe.
    """
    hlavicka = request.headers.get("authorization", "")
    if hlavicka.lower().startswith("bearer "):
        return hlavicka[7:].strip()
    return ""


def over_token(request: Request) -> dict[str, Any]:
    """Závora před každou adresou API."""
    token = token_z_hlavicky(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Chybí token. Pošli hlavičku: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    radek = db.query_one(
        "SELECT id, name, last_used_at FROM api_tokens WHERE token_hash = ?",
        (_otisk(token),),
    )
    if not radek:
        # Zamerne tataz veta jako u chybejiciho tokenu, jen jina prvni
        # pulka: co presne je spatne, se utocnik dozvedet nema.
        raise HTTPException(status_code=401, detail="Neplatný token.",
                            headers={"WWW-Authenticate": "Bearer"})

    _oznac_pouziti(radek)
    return dict(radek)


def _dny(days: Any, vychozi: int = 30) -> int:
    """Počet dní z adresy. Nesmysl spadne na výchozí hodnotu."""
    try:
        cislo = int(days)
    except (TypeError, ValueError):
        return vychozi
    return max(1, min(cislo, 3650))


@router.get("")
@router.get("/")
def rozcestnik(token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Co API umí. Aby se to nemuselo hledat v dokumentaci."""
    from . import __version__

    return {
        "jellyscope": __version__,
        "endpoints": {
            "/api/v1/summary": "watching over a period (?days=30)",
            "/api/v1/now-playing": "what is playing right now",
            "/api/v1/library": "size of the library, growth and free space",
        },
        "note": "Read-only. Authenticate with: Authorization: Bearer <token>",
    }


@router.get("/summary")
def souhrn(days: Any = 30,
           token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Sledovanost za období - to, co je na Přehledu nahoře."""
    dny = _dny(days)
    prehled = stats.overview(dny)
    watched = int(prehled.get("watched_seconds") or 0)
    return {
        "days": dny,
        "watched_seconds": watched,
        "watched_hours": round(watched / 3600, 2),
        "plays": int(prehled.get("plays") or 0),
        "users": int(prehled.get("users") or 0),
        "titles": int(prehled.get("item_count") or 0),
        "transcode_share_percent": round(float(prehled.get("transcode_share") or 0), 1),
        "active_now": stats.active_session_count(),
    }


@router.get("/now-playing")
def prave_hraje(token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Právě běžící přehrávání. Prázdný seznam je taky odpověď."""
    relace = []
    for row in stats.active_sessions():
        relace.append({
            "user": row.get("user_name"),
            "title": row.get("item_name"),
            "series": row.get("series_name"),
            "library": row.get("library_name"),
            "client": row.get("client"),
            "device": row.get("device_name"),
            "play_method": row.get("play_method"),
            "is_transcode": str(row.get("play_method") or "").startswith("Transcode"),
            "is_paused": bool(row.get("is_paused")),
            "audio_language": row.get("current_audio_language") or row.get("audio_language"),
            "bitrate": row.get("bitrate"),
            "position_seconds": int(row.get("position_seconds") or 0),
            "runtime_seconds": int(row.get("runtime_seconds") or 0),
            "started_at": row.get("started_at"),
        })
    return {"count": len(relace), "sessions": relace}


@router.get("/library")
def knihovna(days: Any = 30,
             token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Velikost knihovny, růst za období a volné místo.

    Tatáž čísla, jaká stojí na stránce Knihovna - počítaná týmiž
    funkcemi, ne vlastním dotazem. Dvě místa, která se ptají jinak, se
    dřív nebo později rozejdou.
    """
    dny = _dny(days)
    stav = stats.stav_knihovny()
    rust = stats.rust_knihovny(dny)
    volne = scanner._volne_misto_knihovny()
    return {
        "items": stav["polozek"],
        "movies": stav["filmu"],
        "episodes": stav["epizod"],
        "size_bytes": stav["velikost"],
        "size_human": formatting.bytes_human(stav["velikost"]),
        "items_without_size": int(stats.tech_coverage().get("bez_velikosti") or 0),
        "free_bytes": volne,
        "growth": {
            "days": dny,
            "enough_data": bool(rust.get("dost_dat")),
            "added_bytes": int(rust.get("prirustek") or 0),
            "per_day_bytes": int(rust.get("denne") or 0),
            "days_until_full": rust.get("dnu_do_konce"),
        },
    }
