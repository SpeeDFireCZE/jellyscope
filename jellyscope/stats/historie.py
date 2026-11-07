"""Historie přehrání: filtry, stránka a API.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .. import db
from .druhy import KIND_BOTH, _kind_condition
from .prehravani import popis_prepoctu


# Co vsechno jde v historii filtrovat. Hodnoty se vzdycky predavaji jako
# parametr - z adresy se do SQL nikdy nedostane nic jineho nez otaznik.
ZPUSOBY = ("DirectPlay", "DirectStream", "Transcode")


def _pojistne_meze(where: list[str], params: list[Any], od: str | None,
                   do: str | None) -> None:
    """Meze `started_at` o dva dny širší než místní den - jen pro index."""
    try:
        if od:
            params.append((date.fromisoformat(str(od)[:10]) - timedelta(days=2)).isoformat())
            where.append("p.started_at >= ?")
        if do:
            params.append((date.fromisoformat(str(do)[:10]) + timedelta(days=3)).isoformat())
            where.append("p.started_at < ?")
    except ValueError:
        # Nesmyslné datum neomezí nic navíc - rozhodne date() jako dřív.
        return


def _filtr_historie(
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_BOTH,
    od: str | None = None,
    do: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
) -> tuple[list[str], list[Any]]:
    """Slozi podminky a hodnoty pro dotaz do historie.

    Jedno misto pro seznam i pro pocitadlo. Kdyby si kazdy stavel
    podminky sam, staci pridat filtr do jednoho z nich - a strankovani
    zacne lhat, protoze celkovy pocet uz neodpovida vypisu.
    """
    where = ["p.watched_seconds > 0"]
    params: list[Any] = []

    if user_id:
        where.append("p.user_id = ?")
        params.append(user_id)

    if search:
        where.append("(p.item_name LIKE ? OR p.series_name LIKE ?)")
        # Znaky % kolem hledaneho textu znamenaji "kdekoliv uvnitr".
        # Text jde do dotazu jako parametr, nikdy se nelepi do SQL.
        params.extend([f"%{search}%", f"%{search}%"])

    # Jeden den je zvlastni pripad obdobi - prijde proklikem z tabulky
    # na Prehledu. Kdyz je vyplneny, obdobi se ignoruje.
    # Pojistné meze v UTC: `date(..., 'localtime')` index použít neumí,
    # takže krátké období četlo celou tabulku. Meze jsou o dva dny širší než
    # jakýkoliv posun zóny, takže nic nevyřadí - přesně rozhoduje dál date().
    if day:
        where.append("date(p.started_at, 'localtime') = ?")
        params.append(day)
        _pojistne_meze(where, params, day, day)
    else:
        _pojistne_meze(where, params, od, do)
        if od:
            where.append("date(p.started_at, 'localtime') >= ?")
            params.append(od)
        if do:
            where.append("date(p.started_at, 'localtime') <= ?")
            params.append(do)

    podminka = _kind_condition(kind, "p.")
    if podminka:
        where.append(podminka.replace(" AND ", "", 1))

    if method in ZPUSOBY:
        where.append("p.play_method = ?")
        params.append(method)

    if client:
        where.append("p.client = ?")
        params.append(client)

    if language:
        # "und" znamena "nezjisteno" - a k tomu patri i prazdna hodnota,
        # jinak by se filtr minul s vetsinou zaznamu.
        if language == "und":
            where.append("(p.audio_language IS NULL OR p.audio_language = ''"
                         " OR p.audio_language = 'und')")
        else:
            where.append("p.audio_language = ?")
            params.append(language)

    return where, params


def history(
    limit: int = 50,
    offset: int = 0,
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_BOTH,
    od: str | None = None,
    do: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Surova historie prehravani, strankovana a filtrovana.

    Filtry se skladaji: uzivatel + druh + obdobi + zpusob prehrani +
    klient + jazyk naraz. Vsechny jsou v adrese, takze vysledek jde
    poslat odkazem a po obnoveni stranky zustane, co si clovek nastavil.
    """
    where, params = _filtr_historie(user_id, search, day, kind, od, do,
                                    method, client, language)
    params.extend([limit, offset])
    rows = db.query_all(
        f"""
        SELECT p.*,
               -- Prednost maji rozmery zaznamenane u prehravani: relace vi,
               -- co doopravdy teklo, kdezto `items` popisuje soubor tak, jak
               -- ho zname z posledni synchronizace.
               COALESCE(p.video_height, i.height) AS height,
               COALESCE(p.video_width,  i.width)  AS width,
               -- Kodeky souboru. Vedle nich stoji p.video_codec, tedy to,
               -- co skutecne teklo k prehravaci - a prave rozdil mezi
               -- nimi rika, co se prepocitavalo.
               i.video_codec AS source_codec,
               i.audio_codec AS source_audio_codec
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE {' AND '.join(where)}
        -- p.id rozhodne shodu casu: bez nej muze databaze (PostgreSQL)
        -- vratit stejne stare zaznamy pokazde jinak a pri strankovani by
        -- se jeden ukazal dvakrat a jiny vubec.
        ORDER BY p.started_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    for row in rows:
        # Stejna bublina jako u "prave se hraje" - jednou napsany rozbor
        # slouzi obema mistum.
        row["prepocet"] = popis_prepoctu(row)
    return rows


def historie_pro_api(user_id: str | None = None, kind: str = KIND_BOTH,
                     od: str | None = None, po: int = 2000,
                     podminky: Any = ()):
    """Historie pro API s filtrem: tenké řádky po dávkách, nejnovější první.

    S filtrem musí API projít celou historii zvoleného období. `history()`
    k tomu nepatří: vrací p.* se vším a ke každému řádku počítá rozbor
    přepočtu - na 150 000 přehráních to bylo přes 400 MB paměti. Tady jsou
    jen sloupce, které API vrací, a čtou se po `po` řádcích, takže
    v paměti zůstane jen to, co filtr pustí dál. Podmínky stejné jako
    v `history()` (`_filtr_historie`).

    `podminky` jsou dvojice (kus SQL, hodnoty) předfiltru z API
    (`api._predfiltr_historie`) - kus SQL je vždycky text z kódu, hodnoty
    jdou jako parametry.
    """
    where, params = _filtr_historie(user_id=user_id, kind=kind, od=od)
    for kus, hodnoty in podminky:
        where.append(kus)
        params.extend(hodnoty)
    with db.connect() as conn:
        kurzor = conn.execute(
            f"""
            SELECT p.user_name, p.user_id, p.item_name, p.series_name,
                   p.item_type, p.item_id, p.started_at, p.watched_seconds,
                   p.play_method, p.client, p.device_name,
                   p.audio_language, p.subtitle_language
            FROM playback p
            WHERE {' AND '.join(where)}
            ORDER BY p.started_at DESC, p.id DESC
            """,
            tuple(params),
        )
        while True:
            davka = kurzor.fetchmany(po)
            if not davka:
                return
            yield [dict(radek) for radek in davka]


def history_count(
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_BOTH,
    od: str | None = None,
    do: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
) -> int:
    """Kolik zaznamu filtru odpovida - podle toho se strankuje."""
    where, params = _filtr_historie(user_id, search, day, kind, od, do,
                                    method, client, language)
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE {' AND '.join(where)}
        """,
        tuple(params),
    ))


def hodnoty_filtru() -> dict[str, list[Any]]:
    """Cim vsim se da filtrovat - podle toho, co v historii doopravdy je.

    Nabizet vsechny mozne klienty a jazyky by znamenalo dlouhy seznam
    voleb, ktere u tebe stejne nic nenajdou.
    """
    klienti = [r["client"] for r in db.query_all(
        "SELECT client, COUNT(*) AS n FROM playback"
        " WHERE client IS NOT NULL AND client != ''"
        " GROUP BY client ORDER BY n DESC LIMIT 25") if r["client"]]

    jazyky = [r["audio_language"] for r in db.query_all(
        "SELECT audio_language, COUNT(*) AS n FROM playback"
        " WHERE audio_language IS NOT NULL AND audio_language != ''"
        " GROUP BY audio_language ORDER BY n DESC LIMIT 25")
        if r["audio_language"]]

    zpusoby = [r["play_method"] for r in db.query_all(
        "SELECT play_method, COUNT(*) AS n FROM playback"
        " WHERE play_method IS NOT NULL AND play_method != ''"
        " GROUP BY play_method ORDER BY n DESC") if r["play_method"]]

    return {"klienti": klienti, "jazyky": jazyky, "zpusoby": zpusoby}
