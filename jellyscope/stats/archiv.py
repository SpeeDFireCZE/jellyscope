"""Archiv: archivované položky, hledání mezi existujícími a přiřazení k jinému titulu.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from .. import db
from .polozka import _smaz_polozku, jmeno_souboru, nahled_polozky, popis_polozky

# Jméno loggeru jako před rozdělením: je vidět v každém řádku logu.
log = logging.getLogger("jellyscope.stats")


def archived_count(library_id: str | None = None) -> int:
    """Kolik polozek je v archivu. Pouziva se na odznak u prepinace."""
    where = ["is_missing = 1"]
    params: list[Any] = []
    if library_id:
        where.append("library_id = ?")
        params.append(library_id)
    return int(db.query_value(
        f"SELECT COUNT(*) FROM items WHERE {' AND '.join(where)}", tuple(params)
    ))


def jen_z_archivu(item_ids: list[str], library_id: str) -> list[str]:
    """Z vybraných id nechá jen položky z archivu téhle knihovny.

    Hromadné mazání nesmí sáhnout na titul, který v knihovně je (přišel
    by o historii a příští synchronizace by ho založila znovu prázdný),
    ani na archiv jiné knihovny. Pořadí zůstává, opakovaná id jednou.
    """
    jedinecne = list(dict.fromkeys(i for i in item_ids if i))
    if not jedinecne:
        return []
    otazniky = ", ".join("?" for _ in jedinecne)
    platne = {r["id"] for r in db.query_all(
        f"SELECT id FROM items WHERE id IN ({otazniky})"
        " AND is_missing = 1 AND library_id = ?",
        (*jedinecne, library_id))}
    return [i for i in jedinecne if i in platne]


def smaz_z_archivu(item_ids: list[str], library_id: str) -> dict[str, int]:
    """Hromadně smaže položky z archivu knihovny i s jejich historií.

    Projde jen to, co pustí `jen_z_archivu`. Všechno v jedné transakci:
    když se cokoliv pokazí, nesmaže se nic.
    """
    platne = jen_z_archivu(item_ids, library_id)
    prehravani = 0
    with db.connect() as conn:
        for item_id in platne:
            prehravani += _smaz_polozku(conn, item_id)
    log.info("z archivu smazano %s polozek (%s prehravani)", len(platne), prehravani)
    return {"titulu": len(platne), "prehravani": prehravani}


# Pocet prehravani na polozku - vsechna, i kratka: pri prirazovani jde
# o to, kolik historie se presune, ne kolik se "pocita".
_VSECHNA_PREHRANI = "SELECT item_id, COUNT(*) AS plays FROM playback GROUP BY item_id"


def archivovane_polozky(library_id: str | None, limit: int = 500) -> list[dict[str, Any]]:
    """Archivované položky jednotlivě (díly zvlášť) - pro hromadné přiřazení.

    V seznamu knihovny je seriál jedna dlaždice; tady se přiřazuje po
    souborech, takže každý díl je řádek. Nejdřív ty s nejvíc přehráními:
    právě u nich na přiřazení záleží.
    """
    podminky = ["i.is_missing = 1"]
    parametry: list[Any] = []
    if library_id:
        podminky.append("i.library_id = ?")
        parametry.append(library_id)
    parametry.append(limit)
    radky = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_name, i.parent_index_number,
               i.index_number, i.production_year, i.path,
               COALESCE(p.plays, 0) AS plays
        FROM items i
        LEFT JOIN ({_VSECHNA_PREHRANI}) p ON p.item_id = i.id
        WHERE {' AND '.join(podminky)}
        ORDER BY COALESCE(p.plays, 0) DESC, COALESCE(i.series_name, i.name), i.id
        LIMIT ?
        """,
        tuple(parametry),
    )
    # Bez plakátku: obrázek položky z archivu už v Jellyfinu není.
    for radek in radky:
        radek["popis"] = popis_polozky(radek)
        radek["soubor"] = jmeno_souboru(radek["path"])
    return radky


# Číslo dílu v hledání: S06E07, S6E7, S06 (celá řada) nebo 6x07.
_KOD_DILU = re.compile(r"s(\d{1,3})(?:e(\d{1,4}))?|(\d{1,3})x(\d{1,4})", re.IGNORECASE)
# Kus slova z ASCII písmen a číslic. LIKE ho najde bez ohledu na velikost
# písmen v SQLite i v PostgreSQL (ILIKE); u „Č" nebo „Š" by to SQLite
# nezvládlo, proto se jimi v SQL nefiltruje a dohlídá na ně Python.
_ASCII_KUS = re.compile(r"[A-Za-z0-9]+")
# Interpunkce na krajích slova („Zelenáč," „S06E07." „(2021)" „–") se nehledá.
_OKRAJ_SLOVA = re.compile(r"^[\W_]+|[\W_]+$")
# Kolik kandidátů nejvýš vybere SQL, než je Python projde slovo po slově.
# SQL filtruje každým slovem, takže se sem dojde jen u hodně obecného
# dotazu („S01") - a pak hledání řekne, že výsledků je víc. Hledá se při
# psaní, takže víc řádků na jedno stisknutí klávesy nemá smysl tahat.
HLEDANI_KANDIDATU = 1000


def _pro_hledani(text: Any) -> str:
    """Text pro porovnání: jedna podoba „é" (NFC) a malá písmena.

    lower(), ne casefold(): casefold udělá z „ß" „ss", což LIKE v SQL
    neumí - Python by pak pouštěl i to, co předběžný výběr nevybral.
    """
    return unicodedata.normalize("NFC", str(text or "")).lower()


def _znacka_dilu(rada: int, dil: int | None) -> str:
    """S06E07 (nebo S06) tak, jak bývá ve jménu souboru - malými písmeny."""
    return f"s{rada:02d}" + ("" if dil is None else f"e{dil:02d}")


def _samostatne(kus: str, text: str) -> bool:
    """Je kód v textu jako samostatné slovo? „s06" v „show s06e07", ne v „bass06"."""
    return re.search(rf"(?<![a-z0-9]){re.escape(kus)}(?![0-9])", text) is not None


def _like_kdekoli(vzory: list[str], sloupce: tuple[str, ...] = ("i.name", "i.series_name", "i.path")
                  ) -> tuple[str, list[str]]:
    """Podmínka „některý vzor je v některém sloupci" a parametry k ní."""
    casti = [f"{s} LIKE ?" for _ in vzory for s in sloupce]
    return "(" + " OR ".join(casti) + ")", [v for v in vzory for _ in sloupce]


def hledej_existujici(dotaz: str, limit: int = 50) -> dict[str, Any]:
    """Živé položky pro výběr cíle - podle názvu, seriálu, cesty a čísla dílu.

    Cesta je tu schválně: když Jellyfin titul určil špatně a pak ho po
    opravě založil znovu, nesedí název ani id, ale soubor je pořád
    tentýž - a jeho jméno člověk zná.

    Hledá se po slovech a na pořadí nezáleží: „Zelenáč S06E07" najde díl,
    u kterého je „Zelenáč" v názvu seriálu a S06E07 v číslech řady a dílu.
    Celý dotaz jako jeden kus textu by nenašel nic - ty dvě věci nejsou
    v žádném sloupci vedle sebe, takhle je skládá až popisek.

    Kód dílu (S06E07, S06, 6x07) sedí na čísla řady a dílu, nebo na text
    v názvu (seriál „4x4 Garage"). U položky, která čísla v metadatech
    nemá (film „4x4", soubor „Show 6x07.mkv"), se hledá i v cestě - jako
    samostatné slovo, ne kus jiného.

    Vrací `polozky` (nejvýš `limit`) a `dalsi` - jestli se jich našlo víc,
    než se ukazuje. Kdo pak hledá šestou řadu, ví, že má dotaz upřesnit.
    """
    nic: dict[str, Any] = {"polozky": [], "dalsi": False}
    dotaz = unicodedata.normalize("NFC", dotaz or "").strip()[:100]
    if len(dotaz) < 2:
        return nic

    texty: list[str] = []
    kody: list[tuple[int, int | None, str]] = []
    for slovo in dotaz.split():
        slovo = _OKRAJ_SLOVA.sub("", slovo)
        if not slovo:
            continue
        kod = _KOD_DILU.fullmatch(slovo)
        if kod:
            rada, dil = (kod.group(1), kod.group(2)) if kod.group(1) else (kod.group(3), kod.group(4))
            kody.append((int(rada), int(dil) if dil else None, slovo))
        else:
            texty.append(slovo)
    if not texty and not kody:
        return nic

    # Předběžný výběr v SQL - nadmnožina toho, co pustí Python níž. Každé
    # slovo musí být v názvu, seriálu, cestě nebo roce, takže tam je i jeho
    # nejdelší ASCII kus. Slova se spojují přes AND: kandidátů je pak
    # málo a strop HLEDANI_KANDIDATU se skoro nikdy neuplatní.
    podminky = ["i.is_missing = 0"]
    parametry: list[Any] = []
    sloupce_slova = ("i.name", "i.series_name", "i.path", "CAST(i.production_year AS TEXT)")
    for slovo in texty:
        kusy = _ASCII_KUS.findall(slovo)
        if kusy:
            vzory = [f"%{max(kusy, key=len)}%"]
        else:
            # Bez ASCII (azbuka, řečtina): SQLite tu velikost písmen
            # nesrovná, tak se hledají obvyklé podoby - jak je napsané,
            # malými, Velkým na začátku a VELKÝMI.
            vzory = [f"%{v}%" for v in dict.fromkeys((slovo, slovo.lower(), slovo.capitalize(), slovo.upper()))]
        sql, par = _like_kdekoli(vzory, sloupce_slova)
        podminky.append(sql)
        parametry += par
    for rada, dil, slovo in kody:
        cisla = "i.parent_index_number = ?"
        parametry.append(rada)
        if dil is not None:
            # Díl bez čísla epizody v metadatech řada pořád určuje.
            cisla += " AND (i.index_number = ? OR i.index_number IS NULL)"
            parametry.append(dil)
        v_nazvu, par_nazvu = _like_kdekoli([f"%{slovo}%"], ("i.name", "i.series_name"))
        bez_cisel, par_bez = _like_kdekoli([f"%{slovo}%", f"%{_znacka_dilu(rada, dil)}%"])
        podminky.append(f"(({cisla}) OR {v_nazvu} OR (i.parent_index_number IS NULL AND {bez_cisel}))")
        parametry += par_nazvu + par_bez

    # Bez čísel (filmy) napřed, v SQLite i v PostgreSQL stejně: NULL by se
    # v nich řadil jednou na začátek, jednou na konec.
    radky = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_name, i.parent_index_number,
               i.index_number, i.production_year, i.path,
               i.series_id, i.image_tag, i.series_image_tag,
               l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE {' AND '.join(podminky)}
        ORDER BY COALESCE(i.series_name, i.name), COALESCE(i.parent_index_number, -1),
                 COALESCE(i.index_number, -1), i.id
        LIMIT ?
        """,
        (*parametry, HLEDANI_KANDIDATU),
    )

    hledane = [_pro_hledani(t) for t in texty]

    def sedi(r: dict[str, Any]) -> bool:
        nazev = _pro_hledani(f"{r['name'] or ''} {r['series_name'] or ''}")
        kde = _pro_hledani(" ".join(str(x) for x in (r["name"], r["series_name"], r["path"],
                                                     r["production_year"]) if x is not None))
        if not all(t in kde for t in hledane):
            return False
        for rada, dil, slovo in kody:
            kod = _pro_hledani(slovo)
            if _samostatne(kod, nazev):
                continue                    # „4x4 Garage" - kód je součást názvu
            if r["parent_index_number"] is None:
                if not (_samostatne(kod, kde) or _samostatne(_znacka_dilu(rada, dil), kde)):
                    return False
            elif int(r["parent_index_number"]) != rada or (
                    dil is not None and r["index_number"] is not None and int(r["index_number"]) != dil):
                return False
        return True

    nalezy: list[dict[str, Any]] = []
    for r in radky:
        if sedi(r):
            nalezy.append(r)
            if len(nalezy) > limit:
                break
    return {
        "polozky": [{
            "id": r["id"],
            "popis": popis_polozky(r),
            "typ": r["type"],
            "knihovna": r["library_name"],
            "soubor": jmeno_souboru(r["path"]),
            "nahled": nahled_polozky(r),
        } for r in nalezy[:limit]],
        # Strop kandidátů: za ním můžou být další, které se neprošly.
        "dalsi": len(nalezy) > limit or len(radky) >= HLEDANI_KANDIDATU,
    }


class PrirazeniError(ValueError):
    """Přiřazení nejde provést - hláška je pro člověka."""


def prirad_k_jinemu(stary_id: str, novy_id: str) -> dict[str, Any]:
    """Historie a statistiky archivované položky přejdou na jinou, živou.

    K čemu: Jellyfin titul určil špatně (třeba jako „Behind the scenes"),
    po opravě ho založil znovu - a nesedí název, tmdb ani id, takže
    automatické párování nemá podle čeho. Člověk ví, co k čemu patří.

    Co se stane, v jedné transakci:

    * přehrávání (i ta v koši zapomenutých diváků) dostanou nové id
      **a s ním název, druh, seriál a knihovnu** - historie i žebříčky
      pak ukazují opravený titul, ne ten špatně určený,
    * archivovaná položka se smaže i se stopami a verzemi. Popisovala
      soubor tak, jak ho Jellyfin špatně určil; živá položka ho popisuje
      správně.

    Jen archivovaná do živé: živou by smazání vzalo z knihovny, kde
    doopravdy je, a archivovaná jako cíl by historii zahrabala.
    """
    if not stary_id or not novy_id or stary_id == novy_id:
        raise PrirazeniError("Vyber jiný titul, než je ten v archivu.")
    stary = db.query_one("SELECT id, name, series_name, is_missing FROM items WHERE id = ?",
                         (stary_id,))
    if stary is None or not stary["is_missing"]:
        raise PrirazeniError("Přiřazovat jde jen položku z archivu.")
    novy = db.query_one(
        "SELECT id, name, type, series_name, library_id, parent_index_number,"
        " index_number, production_year, is_missing FROM items WHERE id = ?",
        (novy_id,))
    if novy is None or novy["is_missing"]:
        raise PrirazeniError("Cíl musí být titul, který v knihovně je.")

    from ..db import KOS                    # az tady: jen kvuli nazvu tabulky

    with db.connect() as conn:
        presunuto = 0
        for tabulka in ("playback", KOS):
            if not db.sloupce(conn, tabulka):
                continue                   # kos vznika az s prvnim zapomenutim
            kurzor = conn.execute(
                f"UPDATE {tabulka} SET item_id = ?, item_name = ?, item_type = ?,"
                " series_name = ?, library_id = ? WHERE item_id = ?",
                (novy["id"], novy["name"], novy["type"], novy["series_name"],
                 novy["library_id"], stary_id))
            if tabulka == "playback":
                presunuto = int(kurzor.rowcount or 0)
        conn.execute("DELETE FROM item_streams WHERE item_id = ?", (stary_id,))
        conn.execute("DELETE FROM item_versions WHERE item_id = ?", (stary_id,))
        conn.execute("DELETE FROM items WHERE id = ?", (stary_id,))
        conn.commit()

    log.info("archivovana polozka %s prirazena k %s (%s prehravani)",
             stary_id, novy_id, presunuto)
    return {"status": "ok", "z": popis_polozky(dict(stary)),
            "na": popis_polozky(dict(novy)), "prehravani": presunuto,
            "novy_id": novy["id"]}
