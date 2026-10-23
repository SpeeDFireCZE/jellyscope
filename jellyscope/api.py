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

Vlastník a stav
---------------

Uživatelský klíč (Můj účet) patří účtu a smí **přesně to, co jeho
vlastník** - ne víc. Root klíč (Nastavení → API, `account_id` je NULL)
nepatří nikomu a smí jen to, co mu nastavil správce (`api_tokens.prava`);
bez nastavených práv smí všechno - to jsou i klíče z doby před vlastníky,
aby se po aktualizaci nerozbila Grafana ani Homepage.

Správce může klíč vypnout a zase zapnout - třeba když má podezření, že
unikl, a chce to nejdřív prověřit. Vypnutý klíč nic nevrátí, ale v
seznamu zůstane. Každý požadavek s existujícím klíčem se zapíše do
logu API (`data/logs/api.log`): kdy, kam, odkud a jak dopadl. Viz
zapis_pouziti().
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from . import (accounts, api_dotaz, applog, db, dialect, formatting, insights, pristup,
               scanner, stats)
from .api_dotaz import ChybaApi

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

# Stavy klice. Zneplatneni stav nema - to je smazani radku.
AKTIVNI = "aktivni"
VYPNUTY = "vypnuty"

router = APIRouter(prefix="/api/v1", tags=["api"])

# Kterou cast stromu "co kdo vidi" (pristup.OBLASTI) ktera adresa ukazuje.
# Klic je cesta routy tak, jak ji zna FastAPI - i se `{item_id}`. None =
# smi kazdy, kdo prosel over_token: rozcestnik neukazuje data a historie
# si filtr na vlastniho divaka vnuti sama, jako stranka Historie.
#
# Adresa, ktera tu chybi, je zavrena pro kazdeho krome spravce - nova
# routa bez oblasti se tak neotevre omylem. Test hlida, ze tu je kazda.
OBLASTI_ADRES: dict[str, str | None] = {
    "/api/v1": None,
    "/api/v1/": None,
    "/api/v1/libraries": "knihovna",
    "/api/v1/library": "knihovna",
    "/api/v1/recently-added": "knihovna",
    "/api/v1/item/{item_id}": "knihovna",
    "/api/v1/top-items": "zebricky",
    "/api/v1/least-played": "zebricky",
    "/api/v1/unwatched": "zebricky",
    "/api/v1/insights": "zebricky",
    "/api/v1/summary": "prehled",
    "/api/v1/play-methods": "prehled",
    "/api/v1/now-playing": "prave_hraje",
    "/api/v1/users": "uzivatele",
    "/api/v1/history": None,
    "/api/v1/bandwidth": "sit",
}


def _otisk(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def vytvor(jmeno: str, account_id: int | None = None,
           prava: str | None = None) -> dict[str, Any]:
    """Vyrobí token. Vrátí ho v čitelné podobě - naposledy.

    Ukládá se jen otisk, takže tuhle hodnotu už podruhé nikdo nezjistí.
    Stránka to říká nahlas; token, který jde přečíst kdykoliv, je jen
    delší heslo v databázi.

    `account_id` je vlastník - uživatelský klíč (Můj účet). Bez něj vznikne
    root klíč (Nastavení → API) s právy `prava` (`zabal_prava_klice`);
    bez práv má všechno, jako klíče z doby před vlastníky.
    """
    jmeno = (jmeno or "").strip()[:60] or "bez názvu"
    token = PREDPONA + secrets.token_urlsafe(32)
    with db.connect() as conn:
        token_id = conn.insert_returning_id(
            "INSERT INTO api_tokens (name, token_hash, ukazka, created_at, account_id, prava)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (jmeno, _otisk(token), token[:UKAZKA_ZNAKU], db.utcnow(), account_id,
             prava if account_id is None else None),
        )
        conn.commit()
    log.info("Vyroben API token %s", jmeno)
    return {"token": token, "jmeno": jmeno, "id": token_id}


def seznam(account_id: int | None = None) -> list[dict[str, Any]]:
    """Tokeny pro výpis. Bez otisků - ty tam nemají co dělat.

    Bez parametru všechny i se jménem vlastníka (pro správce), s ním jen
    klíče toho účtu (Můj účet). `vlastnik` je None u root klíče.
    """
    sql = ("SELECT t.id, t.name, t.ukazka, t.created_at, t.last_used_at,"
           "       t.stav, t.account_id, t.prava, a.username AS vlastnik"
           "  FROM api_tokens t LEFT JOIN accounts a ON a.id = t.account_id")
    if account_id is None:
        return db.query_all(sql + " ORDER BY t.id DESC")
    return db.query_all(sql + " WHERE t.account_id = ? ORDER BY t.id DESC",
                        (account_id,))


def zabal_prava_klice(oblasti: Any, anonymizace: bool, historie: bool) -> str:
    """Práva root klíče do podoby, v jaké se ukládají (`api_tokens.prava`).

    Stejný tvar jako vlastní práva účtu (`pristup.zabal_prava`) a navíc
    historie: u účtu je stránka Historie vidět vždy, root klíč pro widget
    knihovny ji ale dostat nemá, dokud ji správce nezaškrtne.
    """
    return json.dumps({
        "oblasti": sorted({str(k) for k in oblasti if str(k) in pristup.PODLE_KLICE}),
        "anonymizace": bool(anonymizace),
        "historie": bool(historie),
    })


def prava_klice(prava: Any) -> dict[str, Any] | None:
    """Uložená práva root klíče. None = všechno.

    Rozbitý JSON (ruční zásah do databáze) NIKDY neznamená všechno: bere se
    jako „nic" - žádná oblast, žádná historie, jména schovaná. U účtu je
    to naopak skupina (`pristup.vlastni_prava`); klíč skupinu nemá a plná
    práva by byla přesně ta díra, kterou nikdo nečeká.
    """
    if prava is None:
        return None
    try:
        data = json.loads(prava) if isinstance(prava, str) else prava
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        return {"oblasti": set(), "anonymizace": True, "historie": False}
    oblasti = data.get("oblasti")
    # Jen skutecne true/false: bool("false") je True, a ruka v databazi
    # (text misto hodnoty, chybejici klic) nesmi otevrit historii ani
    # odkryt jmena. Pochybnost = historie ne, jmena schovana.
    return {
        "oblasti": ({str(k) for k in oblasti if str(k) in pristup.PODLE_KLICE}
                    if isinstance(oblasti, list) else set()),
        "anonymizace": data.get("anonymizace") is not False,
        "historie": data.get("historie") is True,
    }


def uloz_prava_klice(token_id: int, prava: str | None) -> dict[str, Any] | None:
    """Změní práva ROOT klíče. Vrací jméno a předchozí práva, jinak None.

    Uživatelský klíč se tudy nezmění - ten se řídí právy svého účtu.
    Platí od dalšího požadavku: over_token čte práva při každém z nich.
    """
    radek = db.query_one("SELECT name, account_id, prava FROM api_tokens WHERE id = ?",
                         (token_id,))
    if not radek or radek["account_id"] is not None:
        return None
    with db.connect() as conn:
        conn.execute("UPDATE api_tokens SET prava = ? WHERE id = ?", (prava, token_id))
        conn.commit()
    return {"jmeno": radek["name"], "pred": radek["prava"]}


def popis_prav_do_logu(prava: str | None) -> str:
    """Práva root klíče jedním řádkem pro aplikační log."""
    rozbalena = prava_klice(prava)
    if rozbalena is None:
        return "all"
    return (f"areas={','.join(sorted(rozbalena['oblasti'])) or '-'}"
            f" history={'yes' if rozbalena['historie'] else 'no'}"
            f" hide_names={'yes' if rozbalena['anonymizace'] else 'no'}")


def _nastav_stav(token_id: int, stav: str) -> str:
    radek = db.query_one("SELECT name FROM api_tokens WHERE id = ?", (token_id,))
    if not radek:
        return ""
    with db.connect() as conn:
        conn.execute("UPDATE api_tokens SET stav = ? WHERE id = ?", (stav, token_id))
        conn.commit()
    if stav == VYPNUTY:
        log.info("Vypnut API token %s", radek["name"])
    else:
        log.info("Zapnut API token %s", radek["name"])
    return str(radek["name"])


def vypni(token_id: int) -> str:
    """Vypne klíč, ale nesmaže ho. Vrátí jeho jméno (prázdné = není)."""
    return _nastav_stav(token_id, VYPNUTY)


def zapni(token_id: int) -> str:
    """Vypnutý klíč zase pustí dovnitř. Vrátí jeho jméno."""
    return _nastav_stav(token_id, AKTIVNI)


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
    """Závora před každou adresou API.

    Pořadí kontrol: klíč existuje (jinak 401) → je zapnutý → vlastník smí.
    Jakmile se klíč najde, zapíše se jeho id do `request.state`, aby
    požadavek šel do záznamu použití - i když skončí zamítnutím.

    Vrátí řádek klíče a navíc `account` (účet, podle kterého se řídí
    práva: vlastník; u root klíče s vybranými právy náhradní účet, u root
    klíče se vším None), `role` (skupina) a `historie` (smí na historii).
    """
    token = token_z_hlavicky(request)
    if not token:
        raise ChybaApi(401, "missing_token",
                       "Missing token. Send the header: Authorization: Bearer <token>",
                       hlavicky={"WWW-Authenticate": "Bearer"})

    radek = db.query_one(
        "SELECT id, name, last_used_at, stav, account_id, prava"
        "  FROM api_tokens WHERE token_hash = ?",
        (_otisk(token),),
    )
    # Ucet smazany bez kaskady (rucni zasah do databaze) = klic, ktery
    # nikomu nepatri. Tvari se jako neexistujici, ne jako root klic.
    ucet = accounts.get(radek["account_id"]) if radek and radek["account_id"] else None
    if not radek or (radek["account_id"] and not ucet):
        # Zamerne tataz veta jako u chybejiciho tokenu, jen jina prvni
        # pulka: co presne je spatne, se utocnik dozvedet nema.
        raise ChybaApi(401, "invalid_token", "Invalid token.",
                       hlavicky={"WWW-Authenticate": "Bearer"})

    # Pro log API (web.ZaznamPouzitiApi): zapíše se i zamítnutý požadavek
    # - právě ten je při podezření na únik zajímavý.
    request.state.api_klic_do_logu = {
        "id": radek["id"], "jmeno": radek["name"],
        "vlastnik": (ucet or {}).get("username") or ""}
    if radek["stav"] != AKTIVNI:
        raise ChybaApi(403, "key_disabled", "The key is disabled.")

    # Uzivatelsky klic: prava vlastnika. Klic spravce ma vsechno - ale jen
    # dokud je spravcem: kdo o roli prijde, ma prava ctenare (divaka) a jeho
    # klic se zacne ridit povolenim stejne jako kazdy jiny.
    #
    # Root klic (bez vlastnika): bez prav vsechno (i klice z doby
    # pred vlastniky), s pravy se pocita jako ctenarsky ucet s vlastnimi
    # pravy - pristup.vidi a anonymizuj_pro pak plati beze zmeny. Na zadny
    # skutecny ucet nesaha, takze ho zmena spravce neovlivni.
    ucet_prav = ucet
    historie = True
    if ucet:
        kdo = pristup.role(ucet)
    else:
        prava = prava_klice(radek["prava"])
        if prava is None:
            kdo = pristup.SPRAVCE
        else:
            kdo = pristup.CTENAR
            historie = prava["historie"]
            ucet_prav = {"id": None, "username": "", "is_admin": False,
                         pristup.PRISTUP: zabal_prava_klice(
                             prava["oblasti"], prava["anonymizace"], prava["historie"])}
    if ucet and not pristup.smi_klic(ucet):
        raise ChybaApi(403, "key_not_allowed",
                       "The key's account is not allowed a key of its own.")
    # Divak z Jellyfinu smi dovnitr jen tim, ze ho pusti Jellyfin. Kdyz
    # spravce prihlasovani pres Jellyfin vypne (nebo divaka v Jellyfinu
    # zakaze), ma prestat i jeho klic - jinak by si nastroj bral data dal,
    # zatimco spravce je presvedceny, ze divaci jsou venku.
    if kdo == pristup.DIVAK:
        if not pristup.login_zapnuty():
            raise ChybaApi(403, "jellyfin_login_off", "Sign-in through Jellyfin is off.")
        divak = db.query_one("SELECT is_disabled FROM users WHERE id = ?",
                             (str(ucet.get("jellyfin_user_id") or ""),))
        if divak and divak["is_disabled"]:
            raise ChybaApi(403, "viewer_disabled", "The account is disabled in Jellyfin.")

    _oznac_pouziti(radek)
    return {**radek, "account": ucet_prav, "role": kdo, "historie": historie}


def _smi(token: dict[str, Any], cesta: str) -> bool:
    """Smí vlastník klíče na adresu s touhle cestou routy?"""
    # Historie nemá oblast (u účtu je vidět vždy); root klíč s vybranými
    # právy ji dostane jen se zaškrtnutím.
    if cesta == "/api/v1/history" and not token.get("historie", True):
        return False
    if token["role"] == pristup.SPRAVCE:
        return True
    if cesta not in OBLASTI_ADRES:
        return False
    oblast = OBLASTI_ADRES[cesta]
    return oblast is None or pristup.vidi(token["role"], oblast, token["account"])


def povolene_adresy(account: dict[str, Any] | None) -> list[str]:
    """Adresy, na které smí klíč tohohle účtu - pro návod na Můj účet.

    Tytéž podmínky jako smi_adresu(), jen bez klíče v ruce. Rozcestník
    (`/api/v1/`) je tu jednou, i když na něj vedou dvě cesty.
    """
    token = {"role": pristup.role(account), "account": account}
    return [cesta.replace("{item_id}", "{id}") for cesta in OBLASTI_ADRES
            if cesta != "/api/v1" and _smi(token, cesta)]


def smi_adresu(request: Request,
               token: dict[str, Any] = Depends(over_token)) -> dict[str, Any]:
    """Závora za over_token: klíč vidí přesně to, co jeho vlastník.

    Jedna závislost pro všechny adresy, ne kontrola v každé zvlášť -
    adresa, na kterou by se zapomnělo, je přesně ta, kterou někdo najde.
    """
    cesta = getattr(request.scope.get("route"), "path", "")
    if not _smi(token, cesta):
        raise ChybaApi(403, "area_not_allowed",
                       "The key's owner cannot see this part of the statistics.")
    return token


def dotaz_adresy(request: Request,
                 token: dict[str, Any] = Depends(smi_adresu)) -> api_dotaz.Dotaz:
    """Přečtený a zkontrolovaný dotaz adresy (filtry, sort, parametry).

    Stojí za `smi_adresu`, takže FastAPI ji spustí až po závoře: kdo nemá
    platný klíč, dostane 401/403 a nedozví se ani to, jaké parametry nebo
    pole adresa zná.
    """
    cesta = getattr(request.scope.get("route"), "path", "")
    return api_dotaz.nacti(list(request.query_params.multi_items()),
                           ZNAME_PARAMETRY.get(cesta, set()), SEZNAMY.get(cesta, {}))


def _dny_dotazu(dotaz: api_dotaz.Dotaz, vychozi: int = 30) -> int:
    return api_dotaz.cele_cislo(dotaz, "days", vychozi, 1, 3650)


def _limit_dotazu(dotaz: api_dotaz.Dotaz, vychozi: int | None, strop: int) -> int | None:
    """Kolik řádků. Přes strop nebo nesmysl je 400, ne tiché oříznutí.

    Strop je kvůli serveru, ne kvůli datům: „dej mi všechno" nad
    knihovnou se čtyřiceti tisíci tituly je odpověď o desítkách MB a
    dashboard, který se ptá každých pár vteřin, by tím server zaměstnal
    naplno.
    """
    return api_dotaz.cele_cislo(dotaz, "limit", vychozi, 1, strop)


def _nacist(dotaz: api_dotaz.Dotaz, seznam: str, limit: int | None) -> int:
    """Kolik řádků si říct funkci, která má limit v SQL.

    S filtrem nebo řazením celý seznam - filtr musí vidět všechno, jinak
    by hledané řádky hlouběji v pořadí vypadly. Bez nich stačí `limit`
    jako dřív: celý seznam je na velké knihovně drahý (historie za rok
    ze 150 000 přehrání trvá sekundy) a výsledek by byl stejný.
    """
    if limit is None or any(dotaz.pro(seznam)):
        return stats.BEZ_LIMITU
    return limit


def _druh_dotazu(dotaz: api_dotaz.Dotaz) -> str:
    return api_dotaz.volba(dotaz, "kind", stats.KIND_BOTH,
                           (stats.KIND_BOTH, stats.KIND_MOVIE, stats.KIND_SERIES))


def _pro_vlastnika(token: dict[str, Any], data: Any) -> Any:
    """Schová cizí jména, id a adresy, když je má vlastník klíče skryté.

    Stejné pravidlo jako na stránkách (pristup.anonymizuj_pro). Projdou
    tudy **zdrojové řádky** ze `stats`, ne hotová odpověď: tam se jméno
    jmenuje `user` a pristup.anonymizuj() ho podle klíče nepozná.
    """
    return pristup.anonymizuj_pro(data, token.get("account"))


def zapis_pouziti(klic: dict[str, Any], cesta: str, ip: str, vysledek: int) -> None:
    """Zapíše jeden požadavek do logu API (applog.zapis_api).

    Volá ho middleware ve web.py až po odpovědi - jen tehdy je známý
    výsledek. `klic` je to, co nechal v požadavku over_token(): id, jméno
    a vlastník (prázdný = root klíč). Jak dlouho se záznam drží, řídí
    úloha Rotace logů.
    """
    applog.zapis_api(klic["id"], klic.get("jmeno") or "", klic.get("vlastnik") or "",
                     cesta, ip, vysledek)


@router.get("")
@router.get("/")
def rozcestnik(token: dict[str, Any] = Depends(smi_adresu),
               dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Co API umí. Aby se to nemuselo hledat v dokumentaci.

    Jen adresy, které tenhle klíč smí - co by vrátilo 403, tu nemá co
    dělat. U adres se seznamem i jejich pole a typy (`lists`) a jak se
    filtruje (`filtering`), takže program zjistí, co smí poslat, sám.
    """
    from . import __version__

    return {
        "jellyscope": __version__,
        "endpoints": {adresa: popis for adresa, popis in {
            "/api/v1/summary": "watching over a period (?days=30)",
            "/api/v1/now-playing": "what is playing right now",
            "/api/v1/library": "size of the library, growth and free space",
            "/api/v1/libraries": "every library: items, size, codecs",
            "/api/v1/top-items": "most watched titles (?days=30&limit=10&kind=both|movies|series)",
            "/api/v1/least-played": "least played files, never played first; within the same play count biggest first, or by date added with order=oldest|newest (?days=365&limit=25&min_size_mb=0&order=size)",
            "/api/v1/unwatched": "files nobody watched over the period, and how much space they take (?days=365&limit=25)",
            "/api/v1/users": "viewers with watch time over the period (?days=30)",
            "/api/v1/history": "recent playbacks (?days=30&limit=50&user=<id>&kind=both|movies|series)",
            "/api/v1/recently-added": "what came into the library last (?limit=18)",
            "/api/v1/play-methods": "direct play vs. transcode, and the clients used (?days=30)",
            "/api/v1/insights": "transcode offenders, upgrade candidates, oversized and never finished titles, conversion speed in fps (?days=30&limit=15)",
            "/api/v1/bandwidth": "how much data went out and where to (?days=30)",
            "/api/v1/item/{id}": "one title: file, versions and who watched it",
        }.items() if _smi(token, adresa.replace("{id}", "{item_id}"))},
        "note": "Read-only. Authenticate with: Authorization: Bearer <token>. "
                "Every list takes filters, sort and limit (see filtering and lists); "
                "a malformed request is a 400 with an error code, never default data.",
        "filtering": api_dotaz.napoveda(),
        "lists": {adresa: seznamy for adresa, seznamy in SEZNAMY.items()
                  if _smi(token, adresa)},
        "item_fields": "every item that is one file (least-played, unwatched, recently-added, "
                       "item, insights.oversized_rarely_watched) has path, tmdb_id, season and "
                       "episode; for an episode tmdb_id is the series' TMDB id and season + "
                       "episode say which one. Lists by title (top-items and the other insights "
                       "lists) have no single file; a playback in history has item_id for /item/{id}",
    }


def _soubor(row: dict[str, Any]) -> dict[str, Any]:
    """Cesta a tmdb id - u každé položky (souboru), kterou API vrací.

    U dílu je `tmdb_id` id **seriálu**: díl sám v TMDB vlastní id
    obvykle nemá a Jellyscope si ukládá to seriálové. Který díl to je,
    říká řada a číslo dílu vedle - teprve dohromady je to adresa dílu
    v TMDB. U všeho ostatního jsou obě None: hudební stopa má v
    `index_number` číslo stopy a za díl by se vydávala.

    Cesta je celá, jak ji vidí server - stejně jako v detailu titulu.
    Klíč ji dostane, jen když vlastník smí na adresu, která ji vrací
    (viz OBLASTI_ADRES).
    """
    dil = row.get("type") == "Episode"
    return {
        "path": row.get("path"),
        "tmdb_id": row.get("tmdb_id"),
        "season": row.get("parent_index_number") if dil else None,
        "episode": row.get("index_number") if dil else None,
    }


def _titul(row: dict[str, Any]) -> dict[str, Any]:
    """Jeden titul z knihovny tak, jak ho API vrací - všude stejně."""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("type"),
        "series": row.get("series_name"),
        "year": row.get("production_year"),
        "library": row.get("library_name"),
        **_soubor(row),
        "size_bytes": row.get("size_bytes"),
        "size_human": formatting.bytes_human(row.get("size_bytes") or 0),
        "resolution": (f"{row.get('width')}x{row.get('height')}"
                       if row.get("width") and row.get("height") else None),
        "video_codec": row.get("video_codec"),
        "added_at": row.get("date_created"),
    }


# Co jednotlivé adresy vracejí - podle toho se kontroluje filtr i řazení
# a z toho se skládá nápověda. Test hlídá, že klíče řádku odpovědi jsou
# přesně tahle pole: nápověda tak nemůže slíbit pole, které neexistuje.
_T, _C, _CAS, _AN = api_dotaz.TEXT, api_dotaz.CISLO, api_dotaz.CAS, api_dotaz.ANO_NE
POLE_TITULU: dict[str, str] = {
    "id": _T, "name": _T, "type": _T, "series": _T, "year": _C, "library": _T,
    "path": _T, "tmdb_id": _T, "season": _C, "episode": _C, "size_bytes": _C,
    "size_human": _T, "resolution": _T, "video_codec": _T, "added_at": _CAS,
}
SEZNAMY: dict[str, dict[str, dict[str, str]]] = {
    "/api/v1/least-played": {"items": {**POLE_TITULU, "plays": _C,
                                       "watched_seconds": _C, "last_played": _CAS}},
    "/api/v1/unwatched": {"items": POLE_TITULU},
    "/api/v1/recently-added": {"items": POLE_TITULU},
    "/api/v1/top-items": {"items": {"title": _T, "is_series": _AN, "plays": _C,
                                    "watched_seconds": _C, "watched_hours": _C,
                                    "url": _T}},
    "/api/v1/users": {"users": {"id": _T, "name": _T, "is_administrator": _AN,
                                "plays": _C, "titles": _C, "watched_hours": _C,
                                "transcoded_hours": _C, "last_seen": _CAS}},
    "/api/v1/history": {"playbacks": {
        "user": _T, "user_id": _T, "title": _T, "series": _T, "type": _T,
        "item_id": _T, "started_at": _CAS, "watched_seconds": _C,
        "play_method": _T, "is_transcode": _AN, "client": _T, "device": _T,
        "audio_language": _T, "subtitle_language": _T}},
    "/api/v1/now-playing": {"sessions": {
        "user": _T, "title": _T, "series": _T, "library": _T, "client": _T,
        "device": _T, "play_method": _T, "is_transcode": _AN, "is_paused": _AN,
        "audio_language": _T, "bitrate": _C, "position_seconds": _C,
        "runtime_seconds": _C, "started_at": _CAS}},
    "/api/v1/libraries": {"libraries": {"id": _T, "name": _T, "type": _T, "items": _C,
                                        "size_bytes": _C, "size_human": _T,
                                        "runtime_hours": _C}},
    "/api/v1/bandwidth": {"countries": {"country": _T, "code": _T, "plays": _C,
                                        "watched_seconds": _C, "bytes": _C}},
    # Víc seznamů na jedné adrese: pole v dotazu nese předponu seznamu
    # (`never_finished.title[contains]=x`). Bez filtru se volají jako dřív.
    "/api/v1/insights": {
        "transcode_offenders": {"title": _T, "transcodes": _C, "reasons": _T,
                                "video_codec": _T, "audio_codec": _T,
                                "resolution": _T, "url": _T},
        "upgrade_candidates": {"title": _T, "plays": _C, "watched_hours": _C,
                               "resolution": _T, "bitrate": _C, "video_codec": _T,
                               "url": _T},
        "oversized_rarely_watched": {"id": _T, "title": _T, "type": _T, "path": _T,
                                     "tmdb_id": _T, "season": _C, "episode": _C,
                                     "plays": _C, "watched_hours": _C,
                                     "size_bytes": _C, "size_human": _T,
                                     "gb_per_hour": _C, "resolution": _T},
        "never_finished": {"title": _T, "attempts": _C, "best_percent": _C,
                           "runtime_minutes": _C},
    },
    "/api/v1/play-methods": {
        "methods": {"method": _T, "hours": _C},
        "clients": {"client": _T, "hours": _C, "plays": _C},
    },
}
# Obyčejné parametry adresy - co tu není (a není to filtr ani `sort`), je
# 400 unknown_parameter.
ZNAME_PARAMETRY: dict[str, set[str]] = {
    "/api/v1/least-played": {"days", "limit", "min_size_mb", "order"},
    "/api/v1/unwatched": {"days", "limit"},
    "/api/v1/recently-added": {"limit"},
    "/api/v1/top-items": {"days", "limit", "kind"},
    "/api/v1/users": {"days", "limit"},
    "/api/v1/history": {"days", "limit", "user", "kind"},
    "/api/v1/now-playing": {"limit"},
    "/api/v1/libraries": {"limit"},
    "/api/v1/bandwidth": {"days", "limit"},
    "/api/v1/insights": {"days", "limit"},
    "/api/v1/play-methods": {"days", "limit"},
    "/api/v1": set(),
    "/api/v1/": set(),
    "/api/v1/summary": {"days"},
    "/api/v1/library": {"days"},
    "/api/v1/item/{item_id}": set(),
}


def pole_adres(adresy: list[str]) -> dict[str, dict[str, dict[str, str]]]:
    """Pole seznamů u adres, které klíč smí - pro okno „Jak se to používá"."""
    return {adresa: SEZNAMY[adresa] for adresa in adresy if adresa in SEZNAMY}


@router.get("/libraries")
def knihovny(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Každá knihovna zvlášť - to, co jsou karty na stránce Knihovna."""
    limit = _limit_dotazu(dotaz, None, 500)
    karty = [{
        "id": k["id"],
        "name": k["name"],
        "type": k.get("collection_type"),
        "items": int(k.get("item_count") or 0),
        "size_bytes": int(k.get("size_bytes") or 0),
        "size_human": formatting.bytes_human(int(k.get("size_bytes") or 0)),
        "runtime_hours": round(float(k.get("hours") or 0), 1),
    } for k in stats.library_cards()]
    karty = api_dotaz.pouzij(karty, *dotaz.pro("libraries"), limit)
    return {"count": len(karty), "libraries": karty}


@router.get("/top-items")
def nejsledovanejsi(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Nejsledovanější tituly za období. Díly seriálu se sčítají."""
    dny = _dny_dotazu(dotaz)
    limit = _limit_dotazu(dotaz, 10, 200)
    druh = _druh_dotazu(dotaz)
    polozky = [{
        "title": r.get("label"),
        "is_series": bool(r.get("is_series")),
        "plays": int(r.get("plays") or 0),
        "watched_seconds": int(r.get("seconds") or 0),
        "watched_hours": round(float(r.get("hours") or 0), 2),
        "url": r.get("detail_url"),
    } for r in stats.top_items(dny, _nacist(dotaz, "items", limit), druh)]
    return {
        "days": dny,
        "kind": druh,
        "items": api_dotaz.pouzij(polozky, *dotaz.pro("items"), limit),
    }


@router.get("/least-played")
def nejmene_hrane(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Soubory od nejméně hraných: nuly napřed, v nich největší první.

    Počet spuštění je za období, poslední přehrání za celou historii -
    u nuly v období je právě to ta informace, kterou člověk chce.

    `order` je nepovinné a mění jen pořadí titulů se stejným počtem
    spuštění: `oldest` / `newest` podle data přidání místo velikosti.
    Bez něj se řadí jako dřív - dashboard postavený na staré odpovědi se
    nezmění. Neznámá hodnota je 400. `order` a `sort` naráz taky: řekly
    by si každé o jiné pořadí.
    """
    dny = _dny_dotazu(dotaz, 365)
    limit = _limit_dotazu(dotaz, 25, 500)
    min_bajtu = int(api_dotaz.cislo(dotaz, "min_size_mb", 0, 0)) * 1_048_576
    if "order" in dotaz.hodnoty and dotaz.razeni:
        raise api_dotaz.chyba("conflicting_parameters",
                              "Use either 'order' or 'sort', not both.", "order")
    poradi = api_dotaz.volba(dotaz, "order", "size",
                             tuple(insights.PORADI_NEJMENE_HRANYCH))
    polozky = [{
        **_titul(r),
        "plays": int(r.get("plays") or 0),
        "watched_seconds": int(r.get("watched_seconds") or 0),
        "last_played": r.get("last_played"),
    } for r in insights.least_played(dny, _nacist(dotaz, "items", limit),
                                     min_bajtu, poradi)]
    return {
        "days": dny,
        "order": poradi,
        "items": api_dotaz.pouzij(polozky, *dotaz.pro("items"), limit),
    }


@router.get("/unwatched")
def nesledovane(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Co se za období vůbec nehrálo - a kolik místa to zabírá.

    Nejcennější číslo, které Jellyscope umí: podíl knihovny, který nikoho
    nezajímá. Tituly přidané v posledních dnech se nepočítají, ty ještě
    šanci nedostaly.

    S filtrem se počet, velikost a podíl počítají jen z vybraných řádků
    (před limitem) - „kolik místa zabírá nesledované v téhle složce".
    """
    dny = _dny_dotazu(dotaz, 365)
    limit = _limit_dotazu(dotaz, 25, 500)
    mrtve = insights.dead_storage(_nacist(dotaz, "items", limit), dny)
    knihovna = int(mrtve.get("library_bytes") or 0)
    vybrane = api_dotaz.pouzij([_titul(r) for r in mrtve.get("rows") or []],
                               *dotaz.pro("items"), None)
    if dotaz.ma_filtr("items"):
        souhrn = _souhrn_nevyuzitych(vybrane, knihovna)
    else:
        velikost = int(mrtve.get("size_bytes") or 0)
        souhrn = {"count": int(mrtve.get("item_count") or 0), "size_bytes": velikost,
                  "size_human": formatting.bytes_human(velikost),
                  "share_percent": round(float(mrtve.get("share") or 0), 1)}
    return {
        "days": dny,
        "count": souhrn["count"],
        "size_bytes": souhrn["size_bytes"],
        "size_human": souhrn["size_human"],
        "library_bytes": knihovna,
        "share_percent": souhrn["share_percent"],
        "items": vybrane if limit is None else vybrane[:limit],
    }


def _souhrn_nevyuzitych(radky: list[dict[str, Any]], library_bytes: int) -> dict[str, Any]:
    """Počet, velikost a podíl na knihovně z (vyfiltrovaných) řádků."""
    velikost = sum(int(r.get("size_bytes") or 0) for r in radky)
    return {
        "count": len(radky),
        "size_bytes": velikost,
        "size_human": formatting.bytes_human(velikost),
        "share_percent": round(velikost / library_bytes * 100, 1) if library_bytes else 0.0,
    }


@router.get("/users")
def divaci(token: dict[str, Any] = Depends(smi_adresu),
           dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Diváci a kolik toho za období odsledovali.

    Jsou tu jména - stejně jako na stránce Uživatelé, a stejně jako tam
    je klíč diváka nebo čtenáře s anonymizací dostane schovaná. Filtr
    běží až nad schovanými jmény, takže skutečné jméno jím najít nejde.
    """
    dny = _dny_dotazu(dotaz)
    limit = _limit_dotazu(dotaz, None, 500)
    divaci = [{
            "id": r.get("id"),
            "name": r.get("name"),
            "is_administrator": bool(r.get("is_administrator")),
            "plays": int(r.get("plays") or 0),
            "titles": int(r.get("item_count") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "transcoded_hours": round(float(r.get("transcoded_hours") or 0), 2),
            "last_seen": r.get("last_seen"),
        } for r in _pro_vlastnika(token, stats.user_table(dny))]
    return {"days": dny, "users": api_dotaz.pouzij(divaci, *dotaz.pro("users"), limit)}


@router.get("/history")
def historie(token: dict[str, Any] = Depends(smi_adresu),
             dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Poslední přehrávání - nejnovější první.

    Pole jsou vyjmenovaná, ne `p.*`: řádek historie nese i adresu,
    ze které se člověk díval, a ta do souhrnu pro dashboard nepatří.
    Pro provoz po síti je `/api/v1/bandwidth`, a to bez adres.

    Divácký klíč vidí jen svou historii. Cizí `user` je 403 - dřív se
    potichu přepsal na vlastního diváka a nástroj si myslel, že má cizí
    data.
    """
    dny = _dny_dotazu(dotaz)
    limit = _limit_dotazu(dotaz, 50, 500)
    druh = _druh_dotazu(dotaz)
    # Historie filtruje po DNECH v místním čase (tak, jak to má stránka
    # Historie), ne po okamžicích - proto datum, ne časová značka.
    od = (date.today() - timedelta(days=dny - 1)).isoformat()
    # Prázdné `user=` ukazoval návod jako „všichni" - bere se jako bez něj.
    kdo = None if dotaz.hodnoty.get("user") == "" else api_dotaz.identifikator(dotaz, "user")
    muj = pristup.muj_divak(token.get("account"))
    if muj and kdo and accounts.hole_id(kdo) != accounts.hole_id(muj):
        raise ChybaApi(403, "own_history_only",
                       "This key sees only the history of its own account.", "user")
    filtry, razeni = dotaz.pro("playbacks")
    if not (filtry or razeni):
        radky = _pro_vlastnika(token, stats.history(limit=limit, user_id=muj or kdo,
                                                    kind=druh, od=od))
        prehrani = [_prehrani(r) for r in radky]
    else:
        # Celá historie období po dávkách: v paměti jen to, co filtr pustí.
        # Bez řazení stačí prvních `limit` shod - pořadí je dané dotazem.
        prehrani = []
        for davka in stats.historie_pro_api(user_id=muj or kdo, kind=druh, od=od,
                                            podminky=_predfiltr_historie(filtry)):
            prehrani += api_dotaz.pouzij([_prehrani(r) for r in _pro_vlastnika(token, davka)],
                                         filtry, [], None)
            if not razeni and len(prehrani) >= limit:
                break
        prehrani = api_dotaz.pouzij(prehrani, [], razeni, limit)
    return {"days": dny, "count": len(prehrani), "playbacks": prehrani}


# Pole historie, která jde předfiltrovat v SQL, a jejich sloupce. Jméno a id
# diváka tu NEJSOU: schovávají se až v Pythonu (`_pro_vlastnika`), takže
# filtr nad nimi v SQL by běžel nad skutečným jménem a prozradil by ho.
_SLOUPCE_HISTORIE = {
    "title": "p.item_name", "series": "p.series_name", "type": "p.item_type",
    "item_id": "p.item_id", "play_method": "p.play_method", "client": "p.client",
    "device": "p.device_name", "audio_language": "p.audio_language",
    "subtitle_language": "p.subtitle_language", "watched_seconds": "p.watched_seconds",
}
_POROVNANI = {"equals": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _predfiltr_historie(filtry: list[api_dotaz.Filtr]) -> list[tuple[str, list[Any]]]:
    """Filtry historie přeložené do SQL jako předfiltr. Jen nadmnožina!

    Celá historie období znamená na velkém serveru přenést z databáze
    statisíce řádků jen proto, aby je Python zahodil. Jednoduchá pole se
    proto zúží už v SQL. Přesně rozhoduje dál Python (`api_dotaz.pouzij`),
    takže SQL smí pustit víc, nikdy míň:

    * text jen pro hodnoty v ASCII a jen `equals`, `in`, `contains`,
      `starts_with`, `ends_with`; porovnává se malými písmeny jen v ASCII
      (`dialect.male_pismena`) a řádky s jiným než ASCII textem jdou vždycky
      dál (`dialect.mimo_ascii`) - `casefold` v Pythonu umí víc než LOWER
      v databázi (`Straße` najde `strasse`);
    * `%`, `_` i `!` v hodnotě jsou v LIKE obyčejné znaky (ESCAPE '!' -
      zpětné lomítko by na PostgreSQL se standard_conforming_strings=off
      rozbilo literál);
    * negace (`not`, `not_contains`) a `empty` zůstávají jen Pythonu.

    `is_transcode` kopíruje pravidlo z `_prehrani` (začátek „transcode"
    bez ohledu na velikost písmen), ne `stats.je_transcode`: nadmnožinou
    musí být vůči tomu, co rozhoduje Python.
    """
    kind = db.current_dialect()

    def mimo_ascii(sloupec: str) -> str:
        return dialect.mimo_ascii(kind, sloupec)

    podminky: list[tuple[str, list[Any]]] = []
    for filtr in filtry:
        if filtr.pole == "is_transcode":
            chce = filtr.hodnota if filtr.operator == "equals" else not filtr.hodnota
            vyraz = (dialect.male_pismena(kind, "COALESCE(p.play_method, '')")
                     + (" LIKE ?" if chce else " NOT LIKE ?"))
            podminky.append((f"({vyraz} OR {mimo_ascii('p.play_method')})",
                             ["transcode%"]))
            continue
        sloupec = _SLOUPCE_HISTORIE.get(filtr.pole)
        if not sloupec:
            continue
        if filtr.typ == api_dotaz.CISLO:
            if filtr.operator == "in":
                podminky.append((f"{sloupec} IN ({','.join('?' for _ in filtr.hodnota)})",
                                 list(filtr.hodnota)))
            elif filtr.operator in _POROVNANI:
                podminky.append((f"{sloupec} {_POROVNANI[filtr.operator]} ?", [filtr.hodnota]))
            continue
        hodnoty = filtr.hodnota if filtr.operator == "in" else (filtr.hodnota,)
        if (filtr.operator not in ("equals", "in", "contains", "starts_with", "ends_with")
                or not all(str(h).isascii() for h in hodnoty)):
            continue
        male = [str(h).lower() for h in hodnoty]
        male_sloupec = dialect.male_pismena(kind, sloupec)
        if filtr.operator in ("equals", "in"):
            vyraz = f"{male_sloupec} IN ({','.join('?' for _ in male)})"
            parametry = male
        else:
            holy = male[0].replace("!", "!!").replace("%", "!%").replace("_", "!_")
            vzor = {"contains": f"%{holy}%", "starts_with": f"{holy}%",
                    "ends_with": f"%{holy}"}[filtr.operator]
            vyraz = f"{male_sloupec} LIKE ? ESCAPE '!'"
            parametry = [vzor]
        podminky.append((f"({vyraz} OR {mimo_ascii(sloupec)})", parametry))
    return podminky


def _prehrani(r: dict[str, Any]) -> dict[str, Any]:
    """Jedno přehrání tak, jak ho API vrací."""
    return {
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
    }


@router.get("/recently-added")
def nedavno_pridane(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Co do knihovny přišlo naposledy. Díly jedné dávky jsou u sebe.

    Seriál, kterému přibylo víc dílů naráz, je tu jednou - položkou je
    nejnovější díl té dávky, takže id, cesta, řada a díl patří jemu.
    """
    limit = _limit_dotazu(dotaz, 18, 200)
    polozky = api_dotaz.pouzij(
        [_titul(r) for r in stats.recently_added(_nacist(dotaz, "items", limit))],
        *dotaz.pro("items"), limit)
    return {"count": len(polozky), "items": polozky}


@router.get("/play-methods")
def zpusoby_prehrani(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Přímé přehrávání proti překódování - a čím se lidé dívají.

    `limit` platí pro oba seznamy; bez něj jsou způsoby celé a klientů
    25 jako dřív. Klienti se počítají všichni a filtr jde přes ně celé.
    """
    dny = _dny_dotazu(dotaz)
    limit = _limit_dotazu(dotaz, None, 500)
    kolik_klientu = 25 if limit is None else limit
    zpusoby = [{
        "method": r.get("method"),
        "hours": round(float(r.get("hours") or 0), 2),
    } for r in stats.play_method_breakdown(dny)]
    klienti = [{
        "client": r.get("label"),
        "hours": round(float(r.get("hours") or 0), 2),
        "plays": int(r.get("plays") or 0),
    } for r in stats.client_breakdown(dny, _nacist(dotaz, "clients", kolik_klientu))]
    return {
        "days": dny,
        "methods": api_dotaz.pouzij(zpusoby, *dotaz.pro("methods"), limit),
        "clients": api_dotaz.pouzij(klienti, *dotaz.pro("clients"), kolik_klientu),
    }


@router.get("/insights")
def zjisteni(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Stránka Zjištění v číslech - tituly, které stojí za pozornost.

    Každý seznam se spočítá celý a filtr s jeho předponou jde přes něj
    celý; `limit` je až nakonec a platí pro všechny čtyři.
    """
    dny = _dny_dotazu(dotaz)
    kolik = _limit_dotazu(dotaz, 15, 100)

    def nacist(jmeno: str) -> int:
        return _nacist(dotaz, jmeno, kolik)

    def seznam(jmeno: str, polozky: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return api_dotaz.pouzij(polozky, *dotaz.pro(jmeno), kolik)

    return {
        "days": dny,
        "transcode_offenders": seznam("transcode_offenders", [{
            "title": r.get("label"),
            "transcodes": int(r.get("transcodes") or 0),
            "reasons": r.get("reasons"),
            "video_codec": r.get("video_codec"),
            "audio_codec": r.get("audio_codec"),
            "resolution": _rozliseni(r),
            "url": r.get("detail_url"),
        } for r in insights.transcode_offenders(dny, nacist("transcode_offenders"))]),
        "upgrade_candidates": seznam("upgrade_candidates", [{
            "title": r.get("label"),
            "plays": int(r.get("plays") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "resolution": _rozliseni(r),
            "bitrate": r.get("bitrate"),
            "video_codec": r.get("video_codec"),
            "url": r.get("detail_url"),
        } for r in insights.upgrade_candidates(dny, nacist("upgrade_candidates"))]),
        "oversized_rarely_watched": seznam("oversized_rarely_watched", [{
            "id": r.get("id"),
            "title": r.get("label"),
            "type": r.get("type"),
            **_soubor(r),
            "plays": int(r.get("plays") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "size_bytes": r.get("size_bytes"),
            "size_human": formatting.bytes_human(r.get("size_bytes") or 0),
            "gb_per_hour": round(float(r.get("gb_per_hour") or 0), 2),
            "resolution": _rozliseni(r),
        } for r in insights.oversized_rarely_watched(
            dny, nacist("oversized_rarely_watched"))]),
        "never_finished": seznam("never_finished", [{
            "title": r.get("label"),
            "attempts": int(r.get("attempts") or 0),
            "best_percent": round(float(r.get("best_percent") or 0), 1),
            "runtime_minutes": round(float(r.get("runtime_minutes") or 0)),
        } for r in insights.never_finished(dny, nacist("never_finished"))]),
        "transcode_speed": _rychlost(insights.rychlost_prevodu(dny)),
    }


def _rychlost(r: dict[str, Any]) -> dict[str, Any]:
    """Rychlost převodu do API - stejná čísla jako karta na Zjištění."""
    def souhrn(s: dict[str, Any]) -> dict[str, Any]:
        return {
            "playbacks": s["prehravani"],
            "average_fps": s["prumer"],
            "slowest_fps": s["nejmin"]["fps"],
            "slowest_title": s["nejmin"]["label"],
            "fastest_fps": s["nejvic"]["fps"],
            "fastest_title": s["nejvic"]["label"],
            "fell_behind": s["nestiha"],
        }
    if not r.get("prehravani"):
        return {"playbacks": 0, "by_hardware": []}
    return {**souhrn(r),
            "by_hardware": [{"hardware": s["hw"] or "cpu", **souhrn(s)}
                            for s in r["podle_hw"]]}


def _rozliseni(row: dict[str, Any]) -> str | None:
    """"1920x1080", nebo None, když soubor rozměry nemá."""
    if row.get("width") and row.get("height"):
        return f"{row['width']}x{row['height']}"
    return None


@router.get("/bandwidth")
def provoz(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Kolik dat odešlo a kam - souhrn, bez adres jednotlivých lidí.

    Filtr zúží jen seznam zemí; souhrnná čísla jsou ze všech přehrání.
    """
    dny = _dny_dotazu(dotaz)
    limit = _limit_dotazu(dotaz, None, 500)
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
        "countries": api_dotaz.pouzij([{
            "country": z.get("label"),
            "code": z.get("kod"),
            "plays": int(z.get("plays") or 0),
            "watched_seconds": int(z.get("sekund") or 0),
            "bytes": int(z.get("bajtu") or 0),
        } for z in stats.zeme_divaku(dny)], *dotaz.pro("countries"), limit),
    }


def _puvod(skupina: dict[str, Any]) -> dict[str, Any]:
    return {
        "plays": int(skupina.get("plays") or 0),
        "watched_seconds": int(skupina.get("sekund") or 0),
        "bytes": int(skupina.get("bajtu") or 0),
    }


@router.get("/item/{item_id}")
def titul(item_id: str,
          dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Jeden titul: soubor, jeho verze a kdo se na něj díval."""
    # Id z Jellyfinu je hex, v ukazce "demo-ep-0-1" - pismena, cislice,
    # pomlcka, podtrzitko. Cokoliv jineho je preklep nebo pokus a odpoved
    # je stejna jako u neexistujiciho: co presne je spatne, se nerika.
    # Do dotazu jde id jako parametr, takze tohle neni obrana pred SQL,
    # ale pred tim, aby se s nesmyslem vubec pracovalo.
    if not item_id or len(item_id) > 64 or not all(
            c.isalnum() or c in "-_" for c in item_id):
        raise ChybaApi(404, "not_found", "Item not found.")
    polozka = stats.item(item_id)
    if polozka is None:
        raise ChybaApi(404, "not_found", "Item not found.")
    souhrn = stats.item_playback_summary(item_id)
    return {
        **_titul(polozka),
        "is_missing": bool(polozka.get("is_missing")),
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
def souhrn(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Sledovanost za období - to, co je na Přehledu nahoře."""
    dny = _dny_dotazu(dotaz)
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
def prave_hraje(token: dict[str, Any] = Depends(smi_adresu),
                dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Právě běžící přehrávání. Prázdný seznam je taky odpověď."""
    limit = _limit_dotazu(dotaz, None, 500)
    relace = []
    for row in _pro_vlastnika(token, stats.active_sessions()):
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
    relace = api_dotaz.pouzij(relace, *dotaz.pro("sessions"), limit)
    return {"count": len(relace), "sessions": relace}


@router.get("/library")
def knihovna(dotaz: api_dotaz.Dotaz = Depends(dotaz_adresy)) -> dict[str, Any]:
    """Velikost knihovny, růst za období a volné místo.

    Tatáž čísla, jaká stojí na stránce Knihovna - počítaná týmiž
    funkcemi, ne vlastním dotazem. Dvě místa, která se ptají jinak, se
    dřív nebo později rozejdou.
    """
    dny = _dny_dotazu(dotaz)
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
