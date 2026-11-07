"""Výběr období a druhu obsahu, společný pro stránky (z adresy, z relace, výchozí hodnoty).

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import Request

from .. import stats
from .zaklad import _posledni_den


# Obdobi, ktera si uzivatel muze vybrat. Pevny seznam - do SQL se nikdy
# nedostane cislo primo z adresy.
ALLOWED_DAYS = (7, 30, 90, 365)
# `?days=0` = celá historie (stats.obdobi_cela_historie). Není v přepínači,
# je jako tlačítko v okně vlastního období.
CELA_HISTORIE = 0
DAY_LABELS = {7: "7 dnů", 30: "30 dnů", 90: "90 dnů", 365: "rok"}

# Jedno vychozi obdobi pro celou aplikaci. Drive melo kazde stranka vlastni
# (nekde 30 dnu, jinde rok) a pri prepinani zalozek se cislo neocekavane
# menilo. Ted je spolecne a zvolena hodnota se drzi napric strankami.
DEFAULT_DAYS = 30
DAYS_SESSION_KEY = "days"

# Tvar data pro filtr v historii (proklik z tabulky na Prehledu).
_VALID_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _days(request: Request, value: int | None,
          od: str | None = None, do: str | None = None) -> Any:
    """Zvolene obdobi - spolecne pro vsechny stranky.

    Vraci bud pocet dnu (7, 30, 90, 365), nebo `stats.Obdobi` s vlastnimi
    mezemi. Statistiky prijmou obojí - viz stats._meze().

    Kdyz je v adrese platna hodnota, pouzijeme ji a zapamatujeme si ji.
    Kdyz v adrese nic neni, vezmeme naposledy zvolene. Diky tomu prepnuti
    na "rok" na Prehledu plati i po prechodu na Jazyky - okno je synchronni.
    Vlastni obdobi se pamatuje stejne, jen jako dvojice datumu.

    Cislo se porovnava proti pevnemu seznamu a datumy projdou pres
    `strptime`, takze se do SQL nikdy nedostane text primo z adresy.
    """
    # Vyber tazenim v grafu posila okamziky, ne datumy - viz
    # stats.obdobi_z_okamziku(). Ma prednost: kdyz clovek zrovna tahnul
    # mysi, chce videt prave to.
    od_ts = request.query_params.get("od_ts")
    do_ts = request.query_params.get("do_ts")
    if od_ts and do_ts:
        obdobi = stats.obdobi_z_okamziku(od_ts, do_ts)
        if obdobi is not None:
            request.session[DAYS_SESSION_KEY] = _obdobi_do_session(obdobi)
            return obdobi

    if od or do:
        # Datum se pise po cesku (1.8.2026), bere se i RRRR-MM-DD - stejny
        # parser jako ve filtru historie, at se to nikde nechova jinak.
        obdobi = stats.obdobi_od_do(_datum_z_textu(od), _datum_z_textu(do))
        if obdobi is not None:
            request.session[DAYS_SESSION_KEY] = _obdobi_do_session(obdobi)
            return obdobi

    if value in ALLOWED_DAYS:
        request.session[DAYS_SESSION_KEY] = int(value)
        return int(value)
    if value == CELA_HISTORIE:
        # Pamatuje se volba, ne meze: zítra má zahrnout i zítřek.
        request.session[DAYS_SESSION_KEY] = CELA_HISTORIE
        return stats.obdobi_cela_historie()

    remembered = request.session.get(DAYS_SESSION_KEY)
    if remembered == CELA_HISTORIE and not isinstance(remembered, bool):
        return stats.obdobi_cela_historie()
    if isinstance(remembered, dict):
        obdobi = stats.obdobi_od_do(remembered.get("od"), remembered.get("do"))
        if obdobi is not None:
            return obdobi
    if remembered in ALLOWED_DAYS:
        return int(remembered)

    return DEFAULT_DAYS


def _obdobi_do_session(obdobi: stats.Obdobi) -> dict[str, str]:
    """Co si o vlastnim obdobi pamatovat mezi strankami.

    Uklada se v MISTNIM case. Kdyby se ukladaly meze z dotazu (UTC),
    precetly by se pri dalsim kroku znovu jako mistni - a obdobi by se
    pri kazdem prechodu jinam posunulo zpatky, casto o cely den.

    Tvar nese vyznam a stejne ho cte i stats.obdobi_od_do():
    samotne datum je cely den, datum s casem je presny usek vybrany
    tazenim v grafu.
    """
    if obdobi.cely_den:
        return {"od": obdobi.od_mistni[:10], "do": _posledni_den(obdobi.do_mistni)}
    return {"od": obdobi.od_mistni[:16], "do": obdobi.do_mistni[:16]}


KIND_SESSION_KEY = "kind"
# Vlastni pamet pro filtr u nejsledovanejsich titulu - viz _kind().
TOP_KIND_SESSION_KEY = "top_kind"


# Nejsledovanejsi tituly "Ostatní" nenabizeji: skladaji se z nazvu
# titulu, kdezto "Ostatní" je pytlik na zaznamy, u kterych se o titulu
# nic nevi. Seznam by vysel prazdny nebo plny "6. dilu" bez seriálu.
TOP_ALLOWED_KINDS = (stats.KIND_BOTH, stats.KIND_MOVIE, stats.KIND_SERIES)


def _kind(request: Request, value: str | None,
          session_key: str = KIND_SESSION_KEY) -> str:
    """Mix / filmy / serialy / ostatni - stejny princip jako u obdobi.

    Volba se pamatuje v session, aby po prekliknuti jinam a zpatky
    zustala. Porovnava se proti pevnemu seznamu, takze se z adresy
    nikdy nedostane nic do SQL.

    `session_key` odlisuje jednotlive filtry. Nejsledovanejsi tituly maji
    vlastni - kdyby sdilely volbu se sledovanosti po dnech, prepnuti
    u jedne karty by beze slova prekreslilo i druhou. Zaroven maji uzsi
    seznam povolenych hodnot (viz TOP_ALLOWED_KINDS), takze se do nich
    "Ostatní" nepropise ani pres adresu.
    """
    povolene = (TOP_ALLOWED_KINDS if session_key == TOP_KIND_SESSION_KEY
                else stats.ALLOWED_KINDS)
    if value in povolene:
        request.session[session_key] = value
        return value

    remembered = request.session.get(session_key)
    if remembered in povolene:
        return str(remembered)

    return stats.KIND_BOTH


def _datum_z_textu(text: str | None) -> str | None:
    """Datum psané po česku (1.8.2026) na tvar pro databázi.

    Bereme i tvar RRRR-MM-DD - tak chodí proklik z tabulky na Přehledu
    a tak si ho někdo může uložit do záložek.
    """
    text = (text or "").strip()
    if not text:
        return None
    # "2026-08-20 21:30" - takhle chodi ulozeny vyber z grafu.
    presny = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", text)
    if presny:
        return f"{presny.group(1)} {presny.group(2)}"
    if _VALID_DAY.match(text):
        return text
    shoda = re.match(r"^(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{4})$", text)
    if not shoda:
        return None
    den, mesic, rok = (int(c) for c in shoda.groups())
    try:
        return date(rok, mesic, den).isoformat()
    except ValueError:
        # Třicátý únor. Radši nic než datum, které tiše ukáže prázdno.
        return None
