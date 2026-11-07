"""Historie přehrání.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import db, i18n, pristup, stats
from .zaklad import _cesky_datum, _context, muj_divak, require_login, templates
from .obdobi import _VALID_DAY, _datum_z_textu

router = APIRouter()


# Období, která si člověk v historii vybírá. Klíč jde do adresy, takže
# odkaz na "posledních 7 dní" platí i zítra - narozdíl od pevných datumů.
OBDOBI = {
    "vse": ("vše", None),
    "dnes": ("dnes", 0),
    "7": ("posledních 7 dní", 7),
    "30": ("posledních 30 dní", 30),
    "90": ("posledních 90 dní", 90),
    "365": ("poslední rok", 365),
    "vlastni": ("vlastní…", -1),
}


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    page: int = 1,
    search: str | None = None,
    user_id: str | None = None,
    day: str | None = None,
    kind: str | None = None,
    obdobi: str | None = None,
    od: str | None = None,
    do: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Historie přehrávání s filtrem, který se dá skládat.

    Všechny volby jsou v adrese, ne v session: filtr v historii je
    jednorázová otázka („kdo se díval na filmy minulý týden"), ne
    nastavení, které má přežít přechod jinam. Díky tomu se dá výsledek
    poslat odkazem.
    """
    page = max(1, page)
    per_page = 50

    # Divák přihlášený svým jellyfinovým účtem vidí jen svou historii -
    # a nezáleží na tom, co si napíše do adresy. Filtr se mu proto vnutí
    # tady, ne až v šabloně: šablona rozhoduje o tom, co je vidět, ne
    # o tom, co se načte.
    divak = muj_divak(account)
    if divak:
        user_id = divak
    else:
        # Se skrytými jmény přijde z nabídky náhradní id; skutečné id
        # cizího diváka nenajde nic (pristup.divak_z_adresy).
        user_id = pristup.divak_z_adresy(user_id, account)

    # Den přijde proklikem z tabulky na Přehledu; do SQL ho pustíme jen
    # ve tvaru RRRR-MM-DD. Jde tam jako parametr, takže i tak by byl
    # bezpečný - tohle je navíc proti překlepům, které by tiše vrátily
    # prázdný seznam.
    den = day if day and _VALID_DAY.match(day) else None

    obdobi = obdobi if obdobi in OBDOBI else ("vlastni" if (od or do) else "vse")
    if obdobi == "vlastni":
        od_iso, do_iso = _datum_z_textu(od), _datum_z_textu(do)
    else:
        dni = OBDOBI[obdobi][1]
        od_iso = ((date.today() - timedelta(days=dni)).isoformat()
                  if dni is not None and dni >= 0 else None)
        do_iso = None

    # Typ se sem posílá z prokliku v tabulce, ne ze session - filtr
    # v historii nemá přepisovat volbu na Přehledu.
    kind = kind if kind in stats.ALLOWED_KINDS else stats.KIND_BOTH

    # Způsob přehrání porovnáváme proti pevnému seznamu; přehrávače
    # a jazyky proti tomu, co v historii doopravdy je. Nabídka se staví
    # z dat, takže v ní nikdy není volba, která by nic nenašla.
    nabidka = stats.hodnoty_filtru()
    method = method if method in stats.ZPUSOBY else None
    client = client if client in nabidka["klienti"] else None
    language = language if (language == "und"
                            or language in nabidka["jazyky"]) else None

    filtr = {
        "user_id": user_id or None,
        "search": (search or "").strip() or None,
        "day": den,
        "kind": kind,
        "od": od_iso,
        "do": do_iso,
        "method": method,
        "client": client,
        "language": language,
    }

    total = stats.history_count(**filtr)
    stranek = max(1, -(-total // per_page))
    page = min(page, stranek)

    return templates.TemplateResponse(request, "history.html", _context(
        request, account,
        rows=stats.history(limit=per_page, offset=(page - 1) * per_page, **filtr),
        # `is_disabled` jen proto, aby anonymizace poznala, že řádek je
        # divák (holé id a jméno má i knihovna nebo titul) - jinak by
        # nabídka ukázala skutečná jména i id. Se skrytými jmény podle id,
        # tedy podle čísla „Divák N": pořadí podle jména by je prozradilo.
        users=db.query_all(
            "SELECT id, name, is_disabled FROM users ORDER BY id"
            if pristup.skryva_jmena(account)
            else "SELECT id, name, is_disabled FROM users ORDER BY name"),
        nabidka=nabidka,
        total=total,
        page=page,
        pages=stranek,
        obdobi=obdobi,
        obdobi_volby=[(klic, i18n.translate(popis))
                      for klic, (popis, _dni) in OBDOBI.items()],
        # Do formuláře se datum vypisuje po česku, do adresy jde ISO.
        od_text=_cesky_datum(od_iso),
        do_text=_cesky_datum(do_iso),
        **filtr,
        filtr_aktivni=(any(hodnota for klic, hodnota in filtr.items()
                           if klic != "kind")
                       or kind != stats.KIND_BOTH or obdobi != "vse"),
        # Do čipů nad tabulkou: kolik voleb je zapnutých a jak se jmenují.
        # Číslo na tlačítku je poznat na první pohled, seznam id ne.
        # Číslo na tlačítku musí sedět s počtem čipů pod ním. Datumy se
        # proto nepočítají: jsou to jen jiná podoba volby "období", a
        # kdyby se počítaly zvlášť, tlačítko by hlásilo o jedna víc,
        # než je vidět.
        pocet_filtru=(sum(1 for klic, hodnota in filtr.items()
                          if hodnota and klic not in ("kind", "od", "do", "day"))
                      + (1 if kind != stats.KIND_BOTH else 0)
                      + (1 if den or obdobi != "vse" else 0)),
        jmeno_uzivatele=(next((r["name"] for r in db.query_all(
            "SELECT id, name FROM users WHERE id = ?", (user_id,))), user_id)
            if user_id else ""),
        obdobi_popis=i18n.translate(OBDOBI[obdobi][0]),
    ))
