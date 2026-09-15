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
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from . import db, formatting, insights, scanner, stats

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
            "/api/v1/libraries": "every library: items, size, codecs",
            "/api/v1/top-items": "most watched titles (?days=30&limit=10&kind=both|movies|series)",
            "/api/v1/least-played": "least played files, never played first (?days=365&limit=25&min_size_mb=0)",
            "/api/v1/unwatched": "files nobody watched over the period, and how much space they take (?days=365&limit=25)",
            "/api/v1/users": "viewers with watch time over the period (?days=30)",
            "/api/v1/history": "recent playbacks (?days=30&limit=50&user=<id>&kind=both|movies|series)",
            "/api/v1/recently-added": "what came into the library last (?limit=18)",
            "/api/v1/play-methods": "direct play vs. transcode, and the clients used (?days=30)",
            "/api/v1/insights": "transcode offenders, upgrade candidates, oversized and never finished titles (?days=30&limit=15)",
            "/api/v1/bandwidth": "how much data went out and where to (?days=30)",
            "/api/v1/item/{id}": "one title: file, versions and who watched it",
        },
        "note": "Read-only. Authenticate with: Authorization: Bearer <token>",
    }


def _limit(limit: Any, vychozi: int, strop: int = 500) -> int:
    """Kolik řádků. Nesmysl spadne na výchozí, moc se seřízne.

    Strop je tu kvůli serveru, ne kvůli datům: „dej mi všechno" nad
    knihovnou se čtyřiceti tisíci tituly je odpověď o desítkách MB a
    dashboard, který se ptá každých pár vteřin, by tím server zaměstnal
    naplno.
    """
    try:
        cislo = int(limit)
    except (TypeError, ValueError):
        return vychozi
    return max(1, min(cislo, strop))


def _druh(kind: Any) -> str:
    """"both" / "movies" / "series" - cokoliv jiného je "both"."""
    hodnota = str(kind or "").strip().lower()
    return hodnota if hodnota in (stats.KIND_BOTH, stats.KIND_MOVIE,
                                  stats.KIND_SERIES) else stats.KIND_BOTH


def _titul(row: dict[str, Any]) -> dict[str, Any]:
    """Jeden titul z knihovny tak, jak ho API vrací - všude stejně."""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("type"),
        "series": row.get("series_name"),
        "year": row.get("production_year"),
        "library": row.get("library_name"),
        "size_bytes": row.get("size_bytes"),
        "size_human": formatting.bytes_human(row.get("size_bytes") or 0),
        "resolution": (f"{row.get('width')}x{row.get('height')}"
                       if row.get("width") and row.get("height") else None),
        "video_codec": row.get("video_codec"),
        "added_at": row.get("date_created"),
    }


@router.get("/libraries")
def knihovny(token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Každá knihovna zvlášť - to, co jsou karty na stránce Knihovna."""
    karty = [{
        "id": k["id"],
        "name": k["name"],
        "type": k.get("collection_type"),
        "items": int(k.get("item_count") or 0),
        "size_bytes": int(k.get("size_bytes") or 0),
        "size_human": formatting.bytes_human(int(k.get("size_bytes") or 0)),
        "runtime_hours": round(float(k.get("hours") or 0), 1),
    } for k in stats.library_cards()]
    return {"count": len(karty), "libraries": karty}


@router.get("/top-items")
def nejsledovanejsi(days: Any = 30, limit: Any = 10, kind: Any = "both",
                    token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Nejsledovanější tituly za období. Díly seriálu se sčítají."""
    dny = _dny(days)
    radky = stats.top_items(dny, _limit(limit, 10, 200), _druh(kind))
    return {
        "days": dny,
        "kind": _druh(kind),
        "items": [{
            "title": r.get("label"),
            "is_series": bool(r.get("is_series")),
            "plays": int(r.get("plays") or 0),
            "watched_seconds": int(r.get("seconds") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "url": r.get("detail_url"),
        } for r in radky],
    }


@router.get("/least-played")
def nejmene_hrane(days: Any = 365, limit: Any = 25, min_size_mb: Any = 0,
                  token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Soubory od nejméně hraných: nuly napřed, v nich největší první.

    Počet spuštění je za období, poslední přehrání za celou historii -
    u nuly v období je právě to ta informace, kterou člověk chce.
    """
    dny = _dny(days, 365)
    try:
        min_bajtu = max(0, int(float(min_size_mb))) * 1_048_576
    except (TypeError, ValueError):
        min_bajtu = 0
    radky = insights.least_played(dny, _limit(limit, 25), min_bajtu)
    return {
        "days": dny,
        "items": [{
            **_titul(r),
            "plays": int(r.get("plays") or 0),
            "watched_seconds": int(r.get("watched_seconds") or 0),
            "last_played": r.get("last_played"),
        } for r in radky],
    }


@router.get("/unwatched")
def nesledovane(days: Any = 365, limit: Any = 25,
                token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Co se za období vůbec nehrálo - a kolik místa to zabírá.

    Nejcennější číslo, které Jellyscope umí: podíl knihovny, který nikoho
    nezajímá. Tituly přidané v posledních dnech se nepočítají, ty ještě
    šanci nedostaly.
    """
    dny = _dny(days, 365)
    mrtve = insights.dead_storage(_limit(limit, 25), dny)
    return {
        "days": dny,
        "count": int(mrtve.get("item_count") or 0),
        "size_bytes": int(mrtve.get("size_bytes") or 0),
        "size_human": formatting.bytes_human(int(mrtve.get("size_bytes") or 0)),
        "library_bytes": int(mrtve.get("library_bytes") or 0),
        "share_percent": round(float(mrtve.get("share") or 0), 1),
        "items": [_titul(r) for r in mrtve.get("rows") or []],
    }


@router.get("/users")
def divaci(days: Any = 30,
           token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Diváci a kolik toho za období odsledovali.

    Jsou tu jména - stejně jako na stránce Uživatelé. Token k API má
    v ruce správce a je to jeho server; komu jména dávat nechce, ten
    token nedá.
    """
    dny = _dny(days)
    return {
        "days": dny,
        "users": [{
            "id": r.get("id"),
            "name": r.get("name"),
            "is_administrator": bool(r.get("is_administrator")),
            "plays": int(r.get("plays") or 0),
            "titles": int(r.get("item_count") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "transcoded_hours": round(float(r.get("transcoded_hours") or 0), 2),
            "last_seen": r.get("last_seen"),
        } for r in stats.user_table(dny)],
    }


@router.get("/history")
def historie(days: Any = 30, limit: Any = 50, user: Any = None,
             kind: Any = "both",
             token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Poslední přehrávání - nejnovější první.

    Pole jsou vyjmenovaná, ne `p.*`: řádek historie nese i adresu,
    ze které se člověk díval, a ta do souhrnu pro dashboard nepatří.
    Pro provoz po síti je `/api/v1/bandwidth`, a to bez adres.
    """
    dny = _dny(days)
    # Historie filtruje po DNECH v místním čase (tak, jak to má stránka
    # Historie), ne po okamžicích - proto datum, ne časová značka.
    od = (date.today() - timedelta(days=dny - 1)).isoformat()
    kdo = str(user).strip()[:64] if user else None
    radky = stats.history(limit=_limit(limit, 50), user_id=kdo,
                          kind=_druh(kind), od=od)
    return {
        "days": dny,
        "count": len(radky),
        "playbacks": [{
            "user": r.get("user_name"),
            "user_id": r.get("user_id"),
            "title": r.get("item_name"),
            "series": r.get("series_name"),
            "type": r.get("item_type"),
            "item_id": r.get("item_id"),
            "started_at": r.get("started_at"),
            "watched_seconds": int(r.get("watched_seconds") or 0),
            "play_method": r.get("play_method"),
            "is_transcode": str(r.get("play_method") or "").lower().startswith("transcode"),
            "client": r.get("client"),
            "device": r.get("device_name"),
            "audio_language": r.get("audio_language"),
            "subtitle_language": r.get("subtitle_language"),
        } for r in radky],
    }


@router.get("/recently-added")
def nedavno_pridane(limit: Any = 18,
                    token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Co do knihovny přišlo naposledy. Díly jedné dávky jsou u sebe."""
    radky = stats.recently_added(_limit(limit, 18, 200))
    return {"count": len(radky), "items": [_titul(r) for r in radky]}


@router.get("/play-methods")
def zpusoby_prehrani(days: Any = 30,
                     token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Přímé přehrávání proti překódování - a čím se lidé dívají."""
    dny = _dny(days)
    return {
        "days": dny,
        "methods": [{
            "method": r.get("method"),
            "hours": round(float(r.get("hours") or 0), 2),
        } for r in stats.play_method_breakdown(dny)],
        "clients": [{
            "client": r.get("label"),
            "hours": round(float(r.get("hours") or 0), 2),
            "plays": int(r.get("plays") or 0),
        } for r in stats.client_breakdown(dny, 25)],
    }


@router.get("/insights")
def zjisteni(days: Any = 30, limit: Any = 15,
             token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Stránka Zjištění v číslech - tituly, které stojí za pozornost."""
    dny = _dny(days)
    kolik = _limit(limit, 15, 100)
    return {
        "days": dny,
        "transcode_offenders": [{
            "title": r.get("label"),
            "transcodes": int(r.get("transcodes") or 0),
            "reasons": r.get("reasons"),
            "video_codec": r.get("video_codec"),
            "audio_codec": r.get("audio_codec"),
            "resolution": _rozliseni(r),
            "url": r.get("detail_url"),
        } for r in insights.transcode_offenders(dny, kolik)],
        "upgrade_candidates": [{
            "title": r.get("label"),
            "plays": int(r.get("plays") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "resolution": _rozliseni(r),
            "bitrate": r.get("bitrate"),
            "video_codec": r.get("video_codec"),
            "url": r.get("detail_url"),
        } for r in insights.upgrade_candidates(dny, kolik)],
        "oversized_rarely_watched": [{
            "id": r.get("id"),
            "title": r.get("label"),
            "plays": int(r.get("plays") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "size_bytes": r.get("size_bytes"),
            "size_human": formatting.bytes_human(r.get("size_bytes") or 0),
            "gb_per_hour": round(float(r.get("gb_per_hour") or 0), 2),
            "resolution": _rozliseni(r),
        } for r in insights.oversized_rarely_watched(dny, kolik)],
        "never_finished": [{
            "title": r.get("label"),
            "attempts": int(r.get("attempts") or 0),
            "best_percent": round(float(r.get("best_percent") or 0), 1),
            "runtime_minutes": round(float(r.get("runtime_minutes") or 0)),
        } for r in insights.never_finished(dny, kolik)],
    }


def _rozliseni(row: dict[str, Any]) -> str | None:
    """"1920x1080", nebo None, když soubor rozměry nemá."""
    if row.get("width") and row.get("height"):
        return f"{row['width']}x{row['height']}"
    return None


@router.get("/bandwidth")
def provoz(days: Any = 30,
           token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Kolik dat odešlo a kam - souhrn, bez adres jednotlivých lidí."""
    dny = _dny(days)
    prehled = stats.bandwidth_prehled(dny)
    odkud = stats.odkud_se_divaji(dny)["skupiny"]
    return {
        "days": dny,
        "total_bytes": int(prehled.get("bajtu") or 0),
        "total_human": formatting.bytes_human(int(prehled.get("bajtu") or 0)),
        "transcode_bytes": int(prehled.get("bajtu_transcode") or 0),
        "transcode_share_percent": float(prehled.get("podil_transcode") or 0),
        "peak_mbit": round(float(prehled.get("spicka_mbit") or 0), 2),
        "peak_at": prehled.get("spicka_kdy"),
        "average_mbit": float(prehled.get("prumer_mbit") or 0),
        "playbacks": int(prehled.get("prehravani") or 0),
        # Doma / z internetu / neznamo odkud - souhrn, zadne adresy.
        "origin": {
            "home": _puvod(odkud["doma"]),
            "internet": _puvod(odkud["internet"]),
            "unknown": _puvod(odkud["neznamo"]),
        },
        "countries": [{
            "country": z.get("label"),
            "code": z.get("kod"),
            "plays": int(z.get("plays") or 0),
            "watched_seconds": int(z.get("sekund") or 0),
            "bytes": int(z.get("bajtu") or 0),
        } for z in stats.zeme_divaku(dny)],
    }


def _puvod(skupina: dict[str, Any]) -> dict[str, Any]:
    return {
        "plays": int(skupina.get("plays") or 0),
        "watched_seconds": int(skupina.get("sekund") or 0),
        "bytes": int(skupina.get("bajtu") or 0),
    }


@router.get("/item/{item_id}")
def titul(item_id: str,
          token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Jeden titul: soubor, jeho verze a kdo se na něj díval."""
    # Id z Jellyfinu je hex, v ukazce "demo-ep-0-1" - pismena, cislice,
    # pomlcka, podtrzitko. Cokoliv jineho je preklep nebo pokus a odpoved
    # je stejna jako u neexistujiciho: co presne je spatne, se nerika.
    # Do dotazu jde id jako parametr, takze tohle neni obrana pred SQL,
    # ale pred tim, aby se s nesmyslem vubec pracovalo.
    if not item_id or len(item_id) > 64 or not all(
            c.isalnum() or c in "-_" for c in item_id):
        raise HTTPException(status_code=404, detail="Titul neexistuje.")
    polozka = stats.item(item_id)
    if polozka is None:
        raise HTTPException(status_code=404, detail="Titul neexistuje.")
    souhrn = stats.item_playback_summary(item_id)
    return {
        **_titul(polozka),
        "is_missing": bool(polozka.get("is_missing")),
        "path": polozka.get("path"),
        "container": polozka.get("container"),
        "bitrate": polozka.get("bitrate"),
        "runtime_seconds": int((polozka.get("runtime_ticks") or 0) / 10_000_000),
        "versions": [{
            "source_id": v.get("source_id"),
            "name": v.get("nazev"),
            "path": v.get("path"),
            "size_bytes": v.get("size_bytes"),
            "resolution": (f"{v.get('width')}x{v.get('height')}"
                           if v.get("width") and v.get("height") else None),
            "video_codec": v.get("video_codec"),
        } for v in stats.verze_polozky(item_id)],
        "plays": int(souhrn.get("plays") or 0),
        "watched_hours": round(float(souhrn.get("hours") or 0), 2),
        "viewers": int(souhrn.get("users") or 0),
        "last_played": souhrn.get("last_played"),
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
