"""Filtry, řazení a limit pro seznamy v API.

Dotaz se píše do adresy: `pole[operátor]=hodnota` (každý filtr zvlášť,
platí všechny zároveň), `sort=pole,-pole` a `limit`. Na adresách s víc
seznamy (`/insights`, `/play-methods`) nese pole předponu seznamu:
`never_finished.title[contains]=x`.

Modul nic neví o databázi ani o FastAPI. Dostane parametry z adresy a
popis seznamů (`{seznam: {pole: typ}}`) a buď vrátí přečtený dotaz, nebo
vyhodí `ChybaApi`. Nesmysl v dotazu tak nikdy tiše neprojde: dřív se
neznámý parametr přeskočil a špatná hodnota spadla na výchozí, takže
odpověď vypadala v pořádku, a přitom nebyla to, oč se žádalo.

Zprávy chyb jsou anglicky - API čtou programy a vývojáři, rozcestník i
dokumentace API jsou anglicky.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TEXT = "text"
CISLO = "number"
CAS = "time"
ANO_NE = "bool"

OPERATORY: dict[str, tuple[str, ...]] = {
    TEXT: ("equals", "not", "contains", "not_contains", "starts_with",
           "ends_with", "in", "empty"),
    CISLO: ("equals", "not", "gt", "gte", "lt", "lte", "in", "empty"),
    CAS: ("gt", "gte", "lt", "lte", "empty"),
    ANO_NE: ("equals", "not"),
}
VSECHNY_OPERATORY = sorted({op for ops in OPERATORY.values() for op in ops})

# Stropy dotazu. Nejde o data, ale o server: dotaz se zpracovává nad celým
# seznamem, tak ať ho nikdo nezaměstná tisícem filtrů.
MAX_FILTRU = 20
MAX_RAZENI = 3
MAX_IN = 50
MAX_TEXTU = 200

TVAR_CASU = "%Y-%m-%d %H:%M:%S"
_KLIC_FILTRU = re.compile(r"^(?:([a-z0-9_]+)\.)?([a-z0-9_]+)\[([a-z0-9_]+)\]$")
_CAS = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")
_CELE = re.compile(r"^-?\d+$")
_IDENTIFIKATOR = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ChybaApi(Exception):
    """Chyba požadavku na API - status, kód a zpráva pro volajícího.

    Jedna výjimka pro 400 i 401/403/404, aby každá chyba API měla stejné
    tělo (`telo()`): program podle `error` pozná, co je špatně, a nemusí
    číst větu.
    """

    def __init__(self, status: int, kod: str, zprava: str,
                 parametr: str | None = None, povolene: list[str] | None = None,
                 hlavicky: dict[str, str] | None = None) -> None:
        super().__init__(zprava)
        self.status = status
        self.kod = kod
        self.zprava = zprava
        self.parametr = parametr
        self.povolene = povolene
        self.hlavicky = hlavicky

    def telo(self) -> dict[str, Any]:
        telo: dict[str, Any] = {"detail": self.zprava, "error": self.kod}
        if self.parametr is not None:
            telo["parameter"] = self.parametr
        if self.povolene is not None:
            telo["allowed"] = self.povolene
        return telo


def chyba(kod: str, zprava: str, parametr: str | None = None,
          povolene: list[str] | None = None) -> ChybaApi:
    """Chyba 400 - špatně zadaný dotaz."""
    return ChybaApi(400, kod, zprava, parametr, povolene)


@dataclass(frozen=True)
class Filtr:
    seznam: str
    pole: str
    operator: str
    hodnota: Any
    typ: str
    parametr: str


@dataclass(frozen=True)
class Razeni:
    seznam: str
    pole: str
    sestupne: bool
    typ: str


@dataclass
class Dotaz:
    hodnoty: dict[str, str] = field(default_factory=dict)
    filtry: list[Filtr] = field(default_factory=list)
    razeni: list[Razeni] = field(default_factory=list)

    def pro(self, seznam: str) -> tuple[list[Filtr], list[Razeni]]:
        """Filtry a řazení, které míří na jeden seznam."""
        return ([f for f in self.filtry if f.seznam == seznam],
                [r for r in self.razeni if r.seznam == seznam])

    def ma_filtr(self, seznam: str) -> bool:
        return any(f.seznam == seznam for f in self.filtry)


def nacti(parametry: list[tuple[str, str]], znama: set[str],
          seznamy: dict[str, dict[str, str]]) -> Dotaz:
    """Přečte a zkontroluje parametry jedné adresy.

    `znama` jsou obyčejné parametry adresy (`days`, `limit`…), každý smí
    přijít jednou. `seznamy` popisují, co adresa vrací; prázdný slovník =
    adresa vrací jeden objekt a filtrovat ani řadit nejde.
    """
    dotaz = Dotaz()
    povolene = sorted(znama) + (["sort"] if seznamy else [])
    for klic, hodnota in parametry:
        if "[" in klic or "]" in klic:
            if not seznamy:
                raise _nefiltrovatelne(klic)
            if len(dotaz.filtry) >= MAX_FILTRU:
                raise chyba("too_many", f"At most {MAX_FILTRU} filters in one request.", klic)
            dotaz.filtry.append(_filtr(klic, hodnota, seznamy, povolene))
        elif klic == "sort":
            if not seznamy:
                raise _nefiltrovatelne(klic)
            if dotaz.razeni:
                raise chyba("duplicate_parameter", "Parameter 'sort' given twice.", klic)
            dotaz.razeni = _razeni(hodnota, seznamy)
        elif klic in znama:
            if klic in dotaz.hodnoty:
                raise chyba("duplicate_parameter", f"Parameter '{klic}' given twice.", klic)
            dotaz.hodnoty[klic] = hodnota
        else:
            raise chyba("unknown_parameter",
                        f"Unknown parameter '{klic}'. Parameters: "
                        + (", ".join(povolene) or "none")
                        + ("; filters are field[operator]=value." if seznamy else "."),
                        klic, povolene)
    return dotaz


def _nefiltrovatelne(klic: str) -> ChybaApi:
    return chyba("not_filterable",
                 "This address returns one object, not a list: it cannot be "
                 "filtered or sorted.", klic)


def _seznam_a_typ(predpona: str | None, pole: str, seznamy: dict[str, dict[str, str]],
                  parametr: str) -> tuple[str, str]:
    """Na který seznam pole míří a jakého je typu."""
    jmena = list(seznamy)
    if len(jmena) > 1:
        if predpona not in seznamy:
            raise chyba("unknown_list",
                        "This address returns several lists: name one before the "
                        f"field, e.g. '{jmena[0]}.{pole}'. Lists: {', '.join(jmena)}.",
                        parametr, jmena)
        seznam = predpona
    else:
        seznam = jmena[0]
        if predpona is not None and predpona != seznam:
            raise chyba("unknown_list", f"Unknown list '{predpona}'. List: {seznam}.",
                        parametr, jmena)
    pole_seznamu = seznamy[seznam]
    if pole not in pole_seznamu:
        raise chyba("unknown_field",
                    f"Unknown field '{pole}' in {seznam}. Fields: "
                    + ", ".join(pole_seznamu) + ".", parametr, list(pole_seznamu))
    return seznam, pole_seznamu[pole]


def _filtr(klic: str, text: str, seznamy: dict[str, dict[str, str]],
           povolene: list[str]) -> Filtr:
    nalez = _KLIC_FILTRU.match(klic)
    if not nalez:
        raise chyba("unknown_parameter",
                    f"Unknown parameter '{klic}'. Filters are field[operator]=value.",
                    klic, povolene)
    predpona, pole, operator = nalez.groups()
    seznam, typ = _seznam_a_typ(predpona, pole, seznamy, klic)
    if operator not in VSECHNY_OPERATORY:
        raise chyba("unknown_operator",
                    f"Unknown operator '{operator}'. Operators for {typ}: "
                    + ", ".join(OPERATORY[typ]) + ".", klic, list(OPERATORY[typ]))
    if operator not in OPERATORY[typ]:
        raise chyba("operator_not_allowed",
                    f"Operator '{operator}' does not work on {typ} field '{pole}'. "
                    f"Operators for {typ}: " + ", ".join(OPERATORY[typ]) + ".",
                    klic, list(OPERATORY[typ]))
    return Filtr(seznam, pole, operator, _hodnota_filtru(typ, operator, text, klic),
                 typ, klic)


def _hodnota_filtru(typ: str, operator: str, text: str, parametr: str) -> Any:
    if operator == "empty":
        return _ano_ne(text, parametr)
    if operator == "in":
        casti = text.split(",")
        if len(casti) > MAX_IN:
            raise chyba("too_many", f"At most {MAX_IN} values in 'in'.", parametr)
        # „a, b" by druhou hodnotu hledalo i s mezerou a potichu nenašlo.
        if any(cast != cast.strip() for cast in casti):
            raise chyba("invalid_value",
                        "No spaces around the values in 'in': a,b not a, b.", parametr)
        return tuple(_hodnota(typ, cast, parametr) for cast in casti)
    return _hodnota(typ, text, parametr)


def _hodnota(typ: str, text: str, parametr: str) -> Any:
    """Jedna hodnota filtru převedená podle typu pole."""
    if text == "":
        raise chyba("invalid_value", "Empty value.", parametr)
    if typ == TEXT:
        if len(text) > MAX_TEXTU:
            raise chyba("invalid_value", f"Text longer than {MAX_TEXTU} characters.", parametr)
        return text
    if typ == CISLO:
        try:
            cislo = float(text)
        except ValueError:
            cislo = math.nan
        if not math.isfinite(cislo):
            raise chyba("invalid_value", f"'{text[:40]}' is not a number.", parametr)
        return cislo
    if typ == CAS:
        if not _CAS.match(text):
            raise chyba("invalid_value",
                        "Time is YYYY-MM-DD or YYYY-MM-DD HH:MM:SS (UTC).", parametr)
        uplny = text if len(text) > 10 else text + " 00:00:00"
        try:
            datetime.strptime(uplny, TVAR_CASU)
        except ValueError:
            raise chyba("invalid_value", f"'{text}' is not a valid time.", parametr) from None
        return uplny
    return _ano_ne(text, parametr)


def _ano_ne(text: str, parametr: str) -> bool:
    hodnota = text.lower()
    if hodnota not in ("true", "false"):
        raise chyba("invalid_value", "Value is true or false.", parametr)
    return hodnota == "true"


def _razeni(text: str, seznamy: dict[str, dict[str, str]]) -> list[Razeni]:
    casti = text.split(",")
    if len(casti) > MAX_RAZENI:
        raise chyba("too_many", f"Sort by at most {MAX_RAZENI} fields.", "sort")
    razeni: list[Razeni] = []
    for cast in casti:
        sestupne = cast.startswith("-")
        jmeno = cast[1:] if sestupne else cast
        if not jmeno:
            raise chyba("invalid_value", "Empty field in 'sort'.", "sort")
        predpona, _, pole = jmeno.rpartition(".")
        seznam, typ = _seznam_a_typ(predpona or None, pole, seznamy, "sort")
        if any(r.seznam == seznam and r.pole == pole for r in razeni):
            raise chyba("duplicate_parameter", f"Field '{jmeno}' twice in 'sort'.", "sort")
        razeni.append(Razeni(seznam, pole, sestupne, typ))
    return razeni


# ---- použití na seznam ----------------------------------------------------

def pouzij(radky: list[dict[str, Any]], filtry: list[Filtr], razeni: list[Razeni],
           limit: int | None) -> list[dict[str, Any]]:
    """Filtry → řazení → limit, v tomhle pořadí a nad celým seznamem.

    Limit je až poslední krok: kdyby se nejdřív vzalo prvních 25 řádků a
    filtrovalo se až v nich, hledané soubory hlouběji v pořadí by se do
    odpovědi nedostaly vůbec. Vstupní seznam ani řádky se nemění.
    """
    vysledek = [r for r in radky if all(_splnuje(r, f) for f in filtry)]
    if razeni:
        vysledek = _serad(vysledek, razeni)
    return vysledek if limit is None else vysledek[:limit]


def _normalizuj(typ: str, hodnota: Any) -> Any:
    """Hodnota z řádku připravená k porovnání. None = pole bez hodnoty.

    Prázdný text je taky „bez hodnoty" - stejně jako u operátoru `empty`.
    Čas přichází v různých tvarech (`2026-10-01T12:00:00.0000000Z` z
    Jellyfinu, `2026-10-01 12:00:00` z databáze, samotné datum); porovnává
    se jako 19 znaků `YYYY-MM-DD HH:MM:SS`, který se řadí i jako text.
    Nečitelný čas je prázdný, ne chyba - to je chyba dat, ne dotazu.
    """
    if hodnota is None:
        return None
    if typ == TEXT:
        text = str(hodnota)
        return text.casefold() if text else None
    if typ == CISLO:
        try:
            cislo = float(hodnota)
        except (TypeError, ValueError):
            return None
        return cislo if math.isfinite(cislo) else None
    if typ == CAS:
        text = str(hodnota).replace("T", " ")[:19]
        if len(text) == 10:
            text += " 00:00:00"
        try:
            datetime.strptime(text, TVAR_CASU)
        except ValueError:
            return None
        return text
    return bool(hodnota) if isinstance(hodnota, (bool, int)) else None


def _splnuje(radek: dict[str, Any], filtr: Filtr) -> bool:
    hodnota = _normalizuj(filtr.typ, radek.get(filtr.pole))
    operator = filtr.operator
    if operator == "empty":
        return (hodnota is None) == filtr.hodnota
    cil = filtr.hodnota
    if filtr.typ == TEXT:
        cil = tuple(c.casefold() for c in cil) if operator == "in" else cil.casefold()
    # Pole bez hodnoty „není rovno" ničemu a „neobsahuje" nic; všechno
    # ostatní (rovná se, obsahuje, větší než...) u něj neplatí.
    if operator == "not":
        return hodnota is None or hodnota != cil
    if operator == "not_contains":
        return hodnota is None or cil not in hodnota
    if hodnota is None:
        return False
    if operator == "equals":
        return hodnota == cil
    if operator == "in":
        return hodnota in cil
    if operator == "contains":
        return cil in hodnota
    if operator == "starts_with":
        return hodnota.startswith(cil)
    if operator == "ends_with":
        return hodnota.endswith(cil)
    if operator == "gt":
        return hodnota > cil
    if operator == "gte":
        return hodnota >= cil
    if operator == "lt":
        return hodnota < cil
    return hodnota <= cil


def _serad(radky: list[dict[str, Any]], razeni: list[Razeni]) -> list[dict[str, Any]]:
    """Řazení po polích od posledního k prvnímu, každý průchod stabilní.

    Prázdné hodnoty jdou vždy na konec, vzestupně i sestupně - prostý
    `reverse` by je u sestupného řazení přehodil nahoru. Řadí se tady, ne
    v SQL, takže výsledek je stejný na SQLite i PostgreSQL (každá řadí
    NULL jinam).
    """
    vysledek = list(radky)
    for pravidlo in reversed(razeni):
        klice = [_normalizuj(pravidlo.typ, r.get(pravidlo.pole)) for r in vysledek]
        plne = [(k, r) for k, r in zip(klice, vysledek) if k is not None]
        prazdne = [r for k, r in zip(klice, vysledek) if k is None]
        plne.sort(key=lambda dvojice: dvojice[0], reverse=pravidlo.sestupne)
        vysledek = [r for _, r in plne] + prazdne
    return vysledek


def napoveda() -> dict[str, Any]:
    """Jak se filtruje - blok do rozcestníku `/api/v1/`."""
    return {
        "syntax": "field[operator]=value; every filter is its own parameter "
                  "and all of them must match",
        "lists": "on addresses with several lists (/api/v1/insights, "
                 "/api/v1/play-methods) the field carries the list name: "
                 "never_finished.title[contains]=x; lists without a filter "
                 "come back unchanged",
        "sort": "sort=field,-field (minus = descending), at most "
                f"{MAX_RAZENI} fields; empty values always last",
        "order": "filters, then sort, then limit",
        "values": "text ignores case; number is 12 or 1.5; time is YYYY-MM-DD "
                  "or YYYY-MM-DD HH:MM:SS in UTC; bool is true or false; "
                  "in takes values separated by commas; empty takes true or false",
        "operators": {typ: list(ops) for typ, ops in OPERATORY.items()},
        "limits": {"filters": MAX_FILTRU, "sort_fields": MAX_RAZENI,
                   "in_values": MAX_IN, "text_length": MAX_TEXTU},
        "example": "/api/v1/least-played?path[starts_with]=/media/movies"
                   "&sort=plays,added_at&limit=5",
    }


# ---- přísné převody obyčejných parametrů ---------------------------------
# Dřív nesmysl tiše spadl na výchozí hodnotu. Teď je to 400: dotaz, který
# vrátí jiná data, než o která se žádalo, je horší než žádná odpověď.

def cele_cislo(dotaz: Dotaz, jmeno: str, vychozi: int | None, od: int, do: int) -> int | None:
    text = dotaz.hodnoty.get(jmeno)
    if text is None:
        return vychozi
    if not _CELE.match(text) or not od <= int(text) <= do:
        raise chyba("invalid_value",
                    f"'{jmeno}' is a whole number from {od} to {do}.", jmeno)
    return int(text)


def cislo(dotaz: Dotaz, jmeno: str, vychozi: float, od: float) -> float:
    text = dotaz.hodnoty.get(jmeno)
    if text is None:
        return vychozi
    try:
        hodnota = float(text)
    except ValueError:
        hodnota = math.nan
    if not math.isfinite(hodnota) or hodnota < od:
        raise chyba("invalid_value", f"'{jmeno}' is a number, at least {od:g}.", jmeno)
    return hodnota


def volba(dotaz: Dotaz, jmeno: str, vychozi: str, moznosti: tuple[str, ...]) -> str:
    text = dotaz.hodnoty.get(jmeno)
    if text is None:
        return vychozi
    hodnota = text.lower()
    if hodnota not in moznosti:
        raise chyba("invalid_value",
                    f"'{jmeno}' is one of: " + ", ".join(moznosti) + ".",
                    jmeno, list(moznosti))
    return hodnota


def identifikator(dotaz: Dotaz, jmeno: str) -> str | None:
    text = dotaz.hodnoty.get(jmeno)
    if text is None:
        return None
    if not _IDENTIFIKATOR.match(text):
        raise chyba("invalid_value",
                    f"'{jmeno}' is 1-64 letters, digits, '-' or '_'.", jmeno)
    return text
