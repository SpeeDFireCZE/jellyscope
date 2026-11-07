"""Společné kontroly pro balíky se vstupními dveřmi (stats/, web/, scanner/, importers/).

Test, který podvrhne jméno ve vstupních dveřích (`stats.X = náhrada`),
změní jen jméno ve dveřích. Kód uvnitř balíku si jméno bere ze svého
podmodulu a náhradu neuvidí - test by tiše testoval něco jiného, než si
myslí. Funkce tady poznají podvrh ve dveřích a v podmodulu, ať je
napsaný jakkoliv: přiřazení (`stats.X =`, i s předponou `api.`, `sekce.`,
`web.`…), setattr(…stats, "X", …) i mock.patch / patch.object.

Používá je kontrola (e) v testech balíků (`tests/test_stats_balik.py`,
`test_web_balik.py`, `test_scanner_balik.py`, `test_importers_balik.py`):
`spatne_podvrhy` je celé pravidlo, `podvrhni` náhrada jednoho přiřazení
pro testy, které podvrhují jméno čtené víc podmoduly, `routy_naplocho`
rozbalí routy z include_router. Soubor začíná podtržítkem, takže ho celá
sada nespouští jako test.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable
from types import ModuleType


def _predpona(balik: str) -> str:
    """Jméno balíku i s předponou modulu (`stats`, `api.stats`, `jellyscope.web`)."""
    return rf"(?<![\w.])(?:\w+\.)*{balik}"


def podvrhni(balik: ModuleType, jmeno: str, hodnota: object) -> Callable[[], None]:
    """Podvrhne jméno ve vstupních dveřích balíku i ve všech jeho podmodulech.

    Před rozdělením na balík stačilo jedno přiřazení (`scanner.X = náhrada`):
    všechen kód bydlel v jednom souboru a jméno četl z jednoho místa. Po
    rozdělení si každý podmodul jméno drží sám, a tak se musí podvrhnout
    ve všech, které ho mají - přesně tohle dělá `podvrhni`. Vrátí funkci,
    která všechno vrátí na původní hodnoty (test, který nevrací, ji zahodí).

    Bere jen podmoduly, které už jsou načtené (`sys.modules`) - balík je
    načte sám, jakmile se naimportuje jeho `__init__`.
    """
    puvodni: list[tuple[ModuleType, object]] = []
    for nazev, modul in list(sys.modules.items()):
        if modul is not None and (nazev == balik.__name__ or nazev.startswith(balik.__name__ + ".")) \
                and hasattr(modul, jmeno):
            puvodni.append((modul, getattr(modul, jmeno)))
            setattr(modul, jmeno, hodnota)
    if not puvodni:
        # Překlep ve jméně by jinak tiše nepodvrhl nic a test by běžel
        # se skutečným kódem (třeba se skutečným klientem Jellyfinu).
        raise AttributeError(f"{balik.__name__} ani jeho podmoduly nemají {jmeno!r}")

    def vrat() -> None:
        for modul, hodnota_pred in puvodni:
            setattr(modul, jmeno, hodnota_pred)
    return vrat


def podvrhy_ve_dverich(text: str, balik: str) -> set[str]:
    """Jména, která text podvrhuje ve vstupních dveřích (<balik>.X)."""
    predpona = _predpona(balik)
    return (set(re.findall(predpona + r"\.(\w+)\s*=(?!=)", text))
            | set(re.findall(r"setattr\(\s*" + predpona + r"\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(r"patch\.object\(\s*" + predpona + r"\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(rf"patch\(\s*[\"']jellyscope\.{balik}\.(\w+)[\"']", text)))


def spatne_podvrhy(text: str, balik: str, ctenari: dict[str, set[str]]) -> list[str]:
    """Podvrhy ve dveřích (<balik>.X = …), které kód podmodulů nezasáhnou.

    Jméno, které čte nějaký podmodul, smí být podvržené prostým přiřazením
    ve dveřích jen tehdy, když je podvržené i ve **všech** podmodulech, které
    ho čtou - jinak část kódu dál používá původní hodnotu. Posuzuje se celý
    text: `podvrhni()` jinde v souboru prosté přiřazení neomluví (to by
    přepnulo jen dveře a podmoduly by si nechaly předchozí podvrh).
    Nejjednodušší je místo přiřazení volat `podvrhni(balik, "X", náhrada)`.
    """
    chyby = []
    for jmeno in sorted(podvrhy_ve_dverich(text, balik) & set(ctenari)):
        chybi = [m for m in sorted(ctenari[jmeno]) if not podvrhuje_v_podmodulu(text, balik, jmeno, {m})]
        if chybi:
            chyby.append(f"{balik}.{jmeno} (chybí podvrh v {chybi}; použij podvrhni)")
    return chyby


def podvrhy_v_podmodulech(text: str, balik: str) -> set[tuple[str, str]]:
    """Dvojice (podmodul, jméno), které text podvrhuje v podmodulech (<balik>.<podmodul>.X).

    Podvrh v podmodulu, který jméno nečte ani nedefinuje, nezasáhne nic -
    kontrola (e) proto ověří, že každá dvojice míří tam, kde se jméno čte."""
    predpona = _predpona(balik)
    return (set(re.findall(predpona + r"\.(\w+)\.(\w+)\s*=(?!=)", text))
            | set(re.findall(r"setattr\(\s*" + predpona + r"\.(\w+)\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(r"patch\.object\(\s*" + predpona + r"\.(\w+)\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(rf"patch\(\s*[\"']jellyscope\.{balik}\.(\w+)\.(\w+)[\"']", text)))


def routy_naplocho(routy):
    """Všechny routy aplikace, i ty uvnitř include_router.

    FastAPI 0.162 drží routy připojené přes `app.include_router` jako jeden
    `_IncludedRouter` bez adresy - kdo prochází `app.routes` přímo, uvidí
    z webu rozděleného do balíku jen pár adres a tiše nic nezkontroluje.
    """
    for routa in routy:
        if type(routa).__name__ == "_IncludedRouter":
            yield from routy_naplocho(routa.original_router.routes)
        else:
            yield routa


def podvrhuje_v_podmodulu(text: str, balik: str, jmeno: str,
                          podmoduly: Iterable[str] | None = None) -> bool:
    """Podvrhuje text jméno i v podmodulu (<balik>.<podmodul>.X)?

    S `podmoduly` se počítá jen podvrh v jednom z nich - v tom, který jméno
    opravdu čte. Podvrh v jiném podmodulu kód nezasáhne (každý modul čte
    jméno ze svého jmenného prostoru) a test by tiše testoval něco jiného.
    """
    predpona = _predpona(balik)
    kde = "(?:" + "|".join(sorted(map(re.escape, podmoduly))) + ")" if podmoduly else r"\w+"
    return any(re.search(vzor, text) for vzor in (
        predpona + rf"\.{kde}\.{jmeno}\s*=(?!=)",
        r"setattr\(\s*" + predpona + rf"\.{kde}\s*,\s*[\"']{jmeno}[\"']",
        r"patch\.object\(\s*" + predpona + rf"\.{kde}\s*,\s*[\"']{jmeno}[\"']",
        rf"patch\(\s*[\"']jellyscope\.{balik}\.{kde}\.{jmeno}[\"']"))
