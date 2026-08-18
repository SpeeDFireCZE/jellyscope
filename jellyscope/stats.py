"""Statistiky - vsechny otazky, ktere klademe databazi.

Kazda funkce tady dela jednu vec: polozi jeden SQL dotaz a vrati vysledek
jako obycejny seznam slovniku. Zadna funkce nic nekresli a nic nezapisuje.

Tohle rozdeleni ("odkud data" / "co s nimi" / "jak vypadaji") je ta nejlepsi
navykovka, kterou si z projektu muzes odnest. Kdyz se pak nekde objevi
spatne cislo, vis presne, ve kterem ze tri souboru hledat.

Poznamka k casu: cas ukladame v UTC, ale pro cloveka ho prevadime na mistni
cas modifikatorem 'localtime'. "Nejaktivnejsi hodina" musi odpovidat hodinam
na hodinach na zdi, ne v Greenwichi.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from . import db
from .i18n import translate as _t

# Prevod vysky obrazu na skupinu rozliseni. Pouziva se na vic mistech,
# takze je to konstanta, ne kopie na peti radcich.
RESOLUTION_CASE = """
    CASE
        WHEN height IS NULL AND width IS NULL      THEN 'nezname'
        WHEN COALESCE(width, 0)  >= 3400
          OR COALESCE(height, 0) >= 2000           THEN '4K'
        WHEN COALESCE(width, 0)  >= 1800
          OR COALESCE(height, 0) >= 1000           THEN '1080p'
        WHEN COALESCE(width, 0)  >= 1200
          OR COALESCE(height, 0) >= 700            THEN '720p'
        WHEN COALESCE(width, 0)  >= 900
          OR COALESCE(height, 0) >= 500            THEN '576p'
        ELSE 'SD'
    END
"""

# Poradi, v jakem se skupiny rozliseni maji zobrazovat (od nejvyssiho).
RESOLUTION_ORDER = ["4K", "1080p", "720p", "576p", "SD", "nezname"]


def _range(days: int) -> str:
    """Prevede pocet dnu na modifikator pro SQLite ('-30 days')."""
    return f"-{max(1, int(days))} days"


# ---------------------------------------------------------------------------
# Prehled (dashboard)
# ---------------------------------------------------------------------------

def overview(days: int) -> dict[str, Any]:
    """Hlavni cisla za zvolene obdobi."""
    row = db.query_one(
        """
        SELECT
            COUNT(*)                                        AS plays,
            COALESCE(SUM(watched_seconds), 0)               AS watched_seconds,
            COUNT(DISTINCT user_id)                         AS users,
            COUNT(DISTINCT item_id)                         AS item_count,
            COALESCE(SUM(CASE WHEN play_method = 'Transcode'
                              THEN watched_seconds ELSE 0 END), 0) AS transcoded_seconds
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds > 0
        """,
        (_range(days),),
    ) or {}

    watched = row.get("watched_seconds") or 0
    transcoded = row.get("transcoded_seconds") or 0
    row["transcode_share"] = (transcoded / watched * 100) if watched else 0.0
    return row


def previous_overview(days: int) -> dict[str, Any]:
    """Stejna cisla za predchozi stejne dlouhe obdobi - kvuli srovnani.

    Bez teto funkce by stat "142 hodin" nerekl nic. Vedle "+18 % oproti
    minulemu obdobi" uz je z toho informace.
    """
    return db.query_one(
        """
        SELECT
            COUNT(*)                          AS plays,
            COALESCE(SUM(watched_seconds), 0) AS watched_seconds,
            COUNT(DISTINCT user_id)           AS users
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND started_at <  datetime('now', ?)
          AND watched_seconds > 0
        """,
        (_range(days * 2), _range(days)),
    ) or {}


def active_session_count() -> int:
    """Kolik se toho prave hraje. Jen cislo, ne cely seznam.

    Vlastni dotaz schvalne: cislo chce kazda stranka (odznak v postrannim
    menu) a k tomu /health, na ktere se prohlizec pta kazdych deset vterin
    z kazde otevrene karty. Pres `len(active_sessions())` to znamenalo
    pokazde spojit tri tabulky a vytahnout vsechny sloupce jen proto,
    aby se spocetly radky.
    """
    return int(db.query_value(
        "SELECT COUNT(*) FROM playback WHERE is_active = 1", default=0) or 0)


def active_sessions() -> list[dict[str, Any]]:
    """Co se prave ted hraje - vcetne toho, kde v poradi je.

    Postup prehravani dopocitavame tady, ne v sablone: je to vypocet
    a ten do sablony nepatri. Sablona jen zobrazuje.
    """
    rows = db.query_all(
        """
        SELECT p.*,
               -- Prednost ma delka z relace: u polozky, kterou jsme jeste
               -- nesynchronizovali, zadna v `items` neni - a bez ni by
               -- ukazatel postupu chybel.
               COALESCE(p.media_runtime_ticks, i.runtime_ticks) AS runtime_ticks,
               -- Prednost ma to, co hlasi sama relace. Do `items` sahame,
               -- jen kdyz relace rozmery neposlala (starsi zaznamy).
               COALESCE(p.video_height, i.height) AS height,
               COALESCE(p.video_width,  i.width)  AS width,
               i.video_codec  AS source_codec,
               i.audio_codec  AS source_audio_codec,
               i.series_id,
               i.season_name,
               i.index_number,
               i.parent_index_number,
               l.name AS library_name
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        LEFT JOIN libraries l ON l.id = p.library_id
        WHERE p.is_active = 1
        ORDER BY p.started_at
        """
    )

    for row in rows:
        runtime = row.get("runtime_ticks") or 0
        position = row.get("position_ticks") or 0

        row["runtime_seconds"] = runtime / 10_000_000 if runtime else 0
        row["position_seconds"] = position / 10_000_000 if position else 0
        row["remaining_seconds"] = max(
            0.0, row["runtime_seconds"] - row["position_seconds"]
        )
        # Bez znamé delky nema procento smysl - radeji nic nez vymysleny udaj.
        # Zaokrouhlujeme uz tady: procento jde primo do sirky pruhu ve
        # stylu, a "5.126498002663116%" v HTML nikomu neposlouzi.
        row["progress"] = (
            round(min(100.0, position / runtime * 100), 1)
            if runtime and position else None
        )
        # U epizody chceme plakat serialu, ne snimek z dilu.
        row["poster_id"] = row.get("series_id") or row.get("item_id")

    return rows


def recently_added(limit: int = 18) -> list[dict[str, Any]]:
    """Naposledy pridane tituly.

    Serial se ma objevit jednou, ne desetkrat za kazdou epizodu. Slucujeme
    proto podle serialu - a delame to v Pythonu, protoze "vezmi nejnovejsi
    radek z kazde skupiny" se v SQL pise ve dvou databazich jinak
    a citelne to neni ani v jedne.
    """
    rows = db.query_all(
        """
        SELECT i.id, i.name, i.type, i.series_id, i.series_name,
               i.production_year, i.date_created, i.height, i.size_bytes,
               i.library_id, i.parent_index_number, i.index_number,
               l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.is_missing = 0
          AND i.date_created IS NOT NULL
        ORDER BY i.date_created DESC
        LIMIT ?
        """,
        (limit * 6,),
    )

    poradi: list[str] = []
    skupiny: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        # Klic skupiny: serial, nebo film sam za sebe.
        key = str(row["series_id"] or row["id"])
        if key not in skupiny:
            if len(skupiny) >= limit:
                continue
            poradi.append(key)
            skupiny[key] = []
        skupiny[key].append(row)

    result: list[dict[str, Any]] = []
    for key in poradi:
        dily = skupiny[key]
        nejnovejsi = dily[0]
        is_episode = bool(nejnovejsi["series_id"])

        # Kolik dilu prislo "v jedne davce". Cela sezona nedorazi v jednu
        # vterinu, ale ani se nerozlozi do tydne - okno jednoho dne od
        # nejnovejsiho dilu je poctivy odhad. Bez nej by se ve vypisu
        # objevily i dily pridane pred mesicem, jen proto, ze u nich uz
        # dlouho zadny dalsi nepribyl.
        davka = [d for d in dily if _ve_stejne_davce(nejnovejsi["date_created"],
                                                     d["date_created"])]
        davka = _serad_dily(davka)

        result.append({
            **nejnovejsi,
            "title": nejnovejsi["series_name"] if is_episode else nejnovejsi["name"],
            # Odkaz vede na konkretni polozku - u serialu na tu nejnovejsi
            # epizodu, coz je presne to, co uzivatele zajima.
            "poster_id": nejnovejsi["series_id"] or nejnovejsi["id"],
            "is_episode": is_episode,
            # Ostatni dily te davky. Kdyz prisel jen jeden, seznam je
            # prazdny a karta se chova jako driv - proklik rovnou na nej.
            "episodes": davka if is_episode and len(davka) > 1 else [],
            "series_url": f"/series/{nejnovejsi['series_id']}" if is_episode else None,
        })

    return result


def _serad_dily(davka: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serad davku od prvniho dilu k poslednimu a zahod duplicity.

    Razeni: seznam se cte jako seznam dilu, a ten se cte odshora dolu -
    S01E01 nahore. Podle data pridani to nesedi; Jellyfin nacte soubory
    v poradi, v jakem je najde na disku, takze davka prijde zamichana.

    Duplicity: tentyz dil se ve vypisu objevil dvakrat. Muze za to
    knihovna, kde je epizoda ve dvou souborech (ruzna kvalita, zbyla
    stara verze) - v Jellyfinu jsou to dve polozky s vlastnim ItemId
    a obe do davky doopravdy patri. Ve vypisu novinek je ale takovy
    radek dvakrat jen matouci, takze necháme ten, ktery uz ma zmerena
    technicka data (a pri shode novejsi z nich).
    """
    def poradi(d: dict[str, Any]) -> tuple[Any, ...]:
        # None az na konec: dil bez cisla se nema vecpat pred S01E01.
        rada, cislo = d["parent_index_number"], d["index_number"]
        return (rada is None, rada or 0, cislo is None, cislo or 0,
                str(d["name"] or ""))

    nejlepsi: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for d in davka:
        klic = (d["series_id"], d["parent_index_number"], d["index_number"])
        if klic[1] is None and klic[2] is None:
            # Dil bez cisel se s nicim slucovat nesmi - bez cisla neni
            # jak poznat, jestli jde o tentyz dil, nebo o jiny.
            klic = (d["id"], None, None)
        stavajici = nejlepsi.get(klic)
        if stavajici is None or _prednost_dilu(d) > _prednost_dilu(stavajici):
            nejlepsi[klic] = d

    return sorted(nejlepsi.values(), key=poradi)


def _prednost_dilu(d: dict[str, Any]) -> tuple[int, str]:
    """Ktera ze dvou polozek tehoz dilu se ma ukazat."""
    ma_data = 1 if (d.get("height") or d.get("size_bytes")) else 0
    return (ma_data, str(d["date_created"] or ""))


# Jak daleko od sebe smi byt dily, aby se povazovaly za jednu davku.
DAVKA_HODIN = 24


def _ve_stejne_davce(nejnovejsi: Any, zkoumany: Any) -> bool:
    """Prisel tenhle dil spolu s tim nejnovejsim?"""
    try:
        a = datetime.strptime(str(nejnovejsi).replace("T", " ")[:19], db.TIME_FORMAT)
        b = datetime.strptime(str(zkoumany).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return False
    return abs((a - b).total_seconds()) <= DAVKA_HODIN * 3600


# Podle ceho poznáme film od serialu. Jellyfin typuje epizodu jako
# "Episode", film jako "Movie"; cokoliv jineho (koncert, domaci video,
# zive vysilani - a hlavne prevzata historie, u ktere typ vubec neznáme)
# padne do "ostatniho".
KIND_MOVIE = "movies"
KIND_SERIES = "series"
KIND_OTHER = "other"
KIND_BOTH = "both"
ALLOWED_KINDS = (KIND_BOTH, KIND_MOVIE, KIND_SERIES, KIND_OTHER)

# Co v historii znamena "serial". Krome dilu i samotny serial a rada:
#
# Prevzata historie obcas nese druh "Series" misto "Episode" - bud tak
# prisel primo z Jellystatu (viz importers._typ_polozky), nebo se do
# zaznamu zkopiroval z polozky pri sjednoceni nazvu. Rika to min nez
# "Episode": vime jen ze ktereho serialu se divalo, ne ktery dil.
# Porad je to ale serial, takze do "Ostatního" nepatri - tam clovek
# hleda koncerty a zive vysilani, ne hodiny sledovani serialu.
SERIALOVE_TYPY = ("Episode", "Series", "Season")
_ZNAME_TYPY = ("Movie",) + SERIALOVE_TYPY
_SEZNAM_SERIALOVYCH = ", ".join(f"'{typ}'" for typ in SERIALOVE_TYPY)
_SEZNAM_ZNAMYCH = ", ".join(f"'{typ}'" for typ in _ZNAME_TYPY)


def _kind_condition(kind: str, prefix: str = "") -> str:
    """Kus SQL, ktery omezi dotaz na jeden druh.

    `prefix` je nazev tabulky s teckou ("p."). Potrebuje ho dotaz, ktery
    spojuje playback s items - obe tabulky maji sloupec item_type.

    Delici cara je stejna jako v grafu (`daily_activity_split`). Musi byt:
    z tabulky pod grafem vede na kazdem dni proklik do historie i s tim
    filtrem, takze kdyby se rozesly, ukazal by seznam jina cisla, nez na
    ktera clovek prave kliknul.
    """
    if kind == KIND_MOVIE:
        return f" AND {prefix}item_type = 'Movie'"
    if kind == KIND_SERIES:
        return f" AND {prefix}item_type IN ({_SEZNAM_SERIALOVYCH})"
    if kind == KIND_OTHER:
        # IS NULL musi byt zvlast: v SQL neni NULL "ruzne od 'Movie'",
        # NULL neni ruzne od niceho. Bez teho by prevzata historie bez
        # typu vypadla i z "ostatniho" - a to je prave ta cast, kvuli
        # ktere tenhle filtr vznikl.
        return (f" AND ({prefix}item_type IS NULL OR {prefix}item_type = ''"
                f" OR {prefix}item_type NOT IN ({_SEZNAM_ZNAMYCH}))")
    return ""


# Jak se druhu polozky rika cesky. Klice jsou to, co posila Jellyfin.
#
# K cemu to je: v historii i v bublinach grafu chceme misto "TvChannel"
# videt "Živé vysílání". Co v seznamu neni, ukaze se tak, jak to prislo -
# lepsi nez schovat to pod "Ostatní", protoze prave ta surova hodnota je
# stopa, podle ktere se da dohledat, o co slo.
TYPY_POLOZEK = {
    "Movie": "Film",
    "Episode": "Díl seriálu",
    "Series": "Seriál",
    "Season": "Řada",
    "Audio": "Hudba",
    "MusicVideo": "Videoklip",
    "MusicAlbum": "Album",
    "Video": "Video",
    "Trailer": "Upoutávka",
    "TvChannel": "Živé vysílání",
    "LiveTvChannel": "Živé vysílání",
    "TvProgram": "Pořad",
    "Program": "Pořad",
    "Recording": "Nahrávka",
    "Book": "Kniha",
    "AudioBook": "Audiokniha",
    "Photo": "Fotka",
}

# Jak se v historii pojmenuje zaznam, u ktereho typ vubec neni. Typicky
# prevzata historie: Jellystat ani Playback Reporting druh polozky
# neposilaji, takze u nich zustane prazdny.
TYP_NEZNAMY = "Neznámý (z importu)"


def nazev_typu(item_type: Any) -> str:
    """Cesky nazev druhu polozky. Prazdna hodnota = prevzaty zaznam."""
    text = str(item_type or "").strip()
    if not text:
        return _t(TYP_NEZNAMY)
    prelozeny = TYPY_POLOZEK.get(text)
    return _t(prelozeny) if prelozeny else text


def rozpad_ostatnich(days: int) -> list[dict[str, Any]]:
    """Z ceho se sklada "Ostatní" - podle druhu, od nejvetsiho.

    Bez tohohle je "Ostatní" v grafu slepa skvrna: clovek vidi hodiny,
    ale nema jak zjistit, co to bylo. Pouziva se jako popisek pod grafem
    a v bubline u legendy.
    """
    rows = db.query_all(
        f"""
        SELECT item_type,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds > 0
          {_kind_condition(KIND_OTHER)}
        GROUP BY item_type
        ORDER BY SUM(watched_seconds) DESC
        """,
        (_range(days),),
    )
    # Slucujeme podle popisku, ne podle syrove hodnoty: prazdny retezec
    # a NULL jsou pro SQL dve ruzne skupiny, ale pro cloveka jedna a tataz
    # vec - zaznam, u ktereho druh neznáme. Bez toho by se pod grafem
    # objevilo "Neznámý (z importu)" dvakrat za sebou s jinymi cisly.
    slouceno: dict[str, dict[str, Any]] = {}
    for r in rows:
        popisek = nazev_typu(r["item_type"])
        polozka = slouceno.setdefault(
            popisek, {"item_type": r["item_type"], "label": popisek,
                      "hours": 0.0, "plays": 0})
        polozka["hours"] += float(r["hours"] or 0)
        polozka["plays"] += int(r["plays"] or 0)

    for polozka in slouceno.values():
        polozka["hours"] = round(polozka["hours"], 2)
    return sorted(slouceno.values(), key=lambda p: p["hours"], reverse=True)


def daily_activity_split(days: int) -> list[dict[str, Any]]:
    """Sledovanost po dnech rozpadla na filmy a serialy.

    Vraci jeden radek na den se ctyrmi cisly: hodiny a spusteni zvlast
    pro filmy a pro serialy. Soucet si udela sablona - kdyby se posilal
    predpocitany, musely by se pri prepnutí filtru tahat data znovu.

    Proc rozpad podle typu polozky a ne podle knihovny: knihovnu si kazdy
    pojmenuje jinak ("Filmy", "Movies", "4K"), typ polozky hlasi Jellyfin
    vzdycky stejne.
    """
    rows = db.query_all(
        """
        SELECT date(started_at, 'localtime') AS day,
               item_type,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds > 0
        GROUP BY day, item_type
        ORDER BY day
        """,
        (_range(days),),
    )

    # "other" je treti pytlik na vsechno, co neni film ani epizoda:
    # koncerty, domaci videa - a hlavne importovana historie, ke ktere
    # jeste nemame polozku v knihovne, takze typ neznáme.
    #
    # Bez nej se takove zaznamy ztratily uplne: soucet se pocital jako
    # filmy + serialy, takze po importu do prazdne knihovny zustal graf
    # prazdny, i kdyz hodiny v souhrnu nad nim sedely.
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        entry = by_day.setdefault(
            str(row["day"]),
            {"movie_hours": 0.0, "movie_plays": 0, "series_hours": 0.0,
             "series_plays": 0, "other_hours": 0.0, "other_plays": 0},
        )
        prefix = ("movie" if row["item_type"] == "Movie"
                  else "series" if row["item_type"] in SERIALOVE_TYPY
                  else "other")
        entry[f"{prefix}_hours"] += float(row["hours"] or 0)
        entry[f"{prefix}_plays"] += int(row["plays"] or 0)

    today = datetime.now().date()
    calendar = [today - timedelta(days=offset) for offset in range(max(1, days) - 1, -1, -1)]

    result = []
    for day in calendar:
        entry = by_day.get(day.isoformat(), {})
        movie_hours = round(float(entry.get("movie_hours") or 0.0), 2)
        series_hours = round(float(entry.get("series_hours") or 0.0), 2)
        other_hours = round(float(entry.get("other_hours") or 0.0), 2)
        movie_plays = int(entry.get("movie_plays") or 0)
        series_plays = int(entry.get("series_plays") or 0)
        other_plays = int(entry.get("other_plays") or 0)
        result.append({
            "day": day.isoformat(),
            "movie_hours": movie_hours,
            "series_hours": series_hours,
            "other_hours": other_hours,
            "movie_plays": movie_plays,
            "series_plays": series_plays,
            "other_plays": other_plays,
            # Soucet vsech tri, at "Celkem" v tabulce odpovida skutecnosti
            # i tehdy, kdyz typ neznáme.
            "hours": round(movie_hours + series_hours + other_hours, 2),
            "plays": movie_plays + series_plays + other_plays,
        })
    return result


def top_users(days: int, limit: int = 8) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT user_name                     AS label,
               user_id,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds > 0
          AND user_name IS NOT NULL
        GROUP BY user_id, user_name
        ORDER BY hours DESC
        LIMIT ?
        """,
        (_range(days), limit),
    )


# Název dílu tak, jak ho zapisuje plugin Playback Reporting a některé
# klienty: "Seriál - s01e01 - Název dílu". U takového záznamu nemáme
# `series_name` ani položku v knihovně, takže seriál se dá poznat jedině
# odtud - a bez toho se v přehledech rozpadl na jednotlivé díly.
_SERIAL_Z_NAZVU = re.compile(r"^(?P<serial>.+?)\s*[-–]\s*s\d{1,3}e\d{1,4}\b", re.I)


def serial_z_nazvu(nazev: Any) -> str | None:
    """Ze zápisu "Seriál - s01e01 - Díl" vytáhne název seriálu."""
    if not nazev:
        return None
    shoda = _SERIAL_Z_NAZVU.match(str(nazev).strip())
    if not shoda:
        return None
    serial = shoda.group("serial").strip(" -–")
    return serial or None


def _dily_podle_nazvu() -> dict[str, tuple[str, str]]:
    """Názvy epizod z knihovny -> id seriálu, ke kterému patří.

    Slouží k jediné věci: záznam historie, který nese jen jméno dílu
    ("Zvony") a k jehož položce už v knihovně nic nevede, se dá zařadit
    pod seriál, do kterého ten díl patří ("Hra o trůny"). Bez toho z něj
    v přehledu vznikl samostatný titul - film, který neexistuje.

    **Jen jednoznačné shody.** Názvy dílů se mezi seriály opakují
    ("Pilot", "Část 1"); u takového jména radši nehádáme, protože špatně
    přiřazená historie je horší než ta, o které víme, že je stranou.

    Klíče mají předponu `dil:`, aby se nemíchaly s názvy seriálů - seriál
    a jeho epizoda se můžou jmenovat stejně.
    """
    podle_jmena: dict[str, set[str]] = {}
    for radek in db.query_all(
        "SELECT name, series_id FROM items WHERE series_id IS NOT NULL"
        "  AND name IS NOT NULL AND name != ''"
    ):
        jmeno = str(radek["name"]).strip().lower()
        podle_jmena.setdefault(jmeno, set()).add(str(radek["series_id"]))

    jmena = _jmena_serialu()
    return {f"dil:{jmeno}": (next(iter(serialy)),
                             jmena.get(next(iter(serialy)), "?"))
            for jmeno, serialy in podle_jmena.items() if len(serialy) == 1}


def _jmena_serialu() -> dict[str, str]:
    """id seriálu -> jeho název. Pro popisek řádku."""
    return {str(r["series_id"]): str(r["name"]) for r in db.query_all(
        "SELECT series_id, MAX(series_name) AS name FROM items"
        " WHERE series_id IS NOT NULL AND series_name IS NOT NULL"
        " GROUP BY series_id")}


def klic_titulu(row: dict[str, Any],
                podle_nazvu: dict[str, str] | None = None) -> tuple[str, str, bool]:
    """Do jaké skupiny řádek historie patří: (klíč, popisek, je to seriál).

    Pořadí je od nejspolehlivějšího údaje k nejslabšímu:

      1. `series_id` z knihovny - jediné, co nezáleží na tom, jak se který
         záznam kdysi vyplnil,
      2. název seriálu (ze záznamu nebo z položky),
      3. název seriálu vyparsovaný z "Seriál - s01e01 - Díl",
      4. díl toho jména, který zná knihovna (viz `_dily_podle_nazvu()`),
      5. samotná položka - film, nebo díl, u kterého o seriálu nevíme nic.

    Body 3 a 4 tu jsou proto, že převzatá historie o seriálu neřekne nic.
    Playback Reporting ukládá název jako "Blue - s01e17 - Calypso"; jiné
    zdroje jen "Zvony". V přehledu se pak objevilo pět samostatných řádků
    místo jednoho "Blue", nebo díl "Zvony" jako film vedle "Hry o trůny".
    """
    if row.get("series_id"):
        return (f"serial:{row['series_id']}",
                str(row.get("series_name") or row.get("i_series_name")
                    or serial_z_nazvu(row.get("item_name")) or row.get("i_name") or "?"),
                True)

    # Položku v knihovně známe a **není to epizoda** - tím je hádání
    # skončené. Všechno pod tímhle řádkem jsou dohady z názvu a ty jsou
    # tu jen pro záznamy, u kterých se nedá zjistit nic jiného.
    #
    # Bez téhle zábrany se stalo tohle: film se jmenoval stejně jako díl
    # jiného seriálu (nebo jako seriál sám), takže spadl do jeho skupiny.
    # Skupina se pak jmenovala po tom druhém titulu, ale proklik vedl na
    # tenhle film - popisek říkal jedno a odkaz vedl jinam.
    #
    # Epizoda bez `series_id` je výjimka: tam hádání pomáhá, protože
    # k seriálu ji jinak nemáme jak přiřadit.
    if row.get("existujici_id") and str(row.get("i_type") or "") != "Episode":
        return (f"polozka:{row.get('item_id') or row['existujici_id']}",
                str(row.get("i_name") or row.get("item_name") or "?"), False)

    # Poslední pokus, ještě před vzdáním se: díl toho jména v knihovně
    # existuje a seriál zná. Záznam o něm neví nic a poznat ho z názvu
    # ("Zvony") nejde - ale knihovna ho zná. Bez tohohle z něj v přehledu
    # vznikl samostatný titul vedle seriálu, do kterého patří.
    # Viz `_dily_podle_nazvu()`.
    z_knihovny = (podle_nazvu or {}).get(
        "dil:" + str(row.get("item_name") or "").strip().lower())
    if z_knihovny:
        serial, jmeno = z_knihovny
        return (f"serial:{serial}", jmeno, True)

    nazev_serialu = (row.get("series_name") or row.get("i_series_name")
                     or serial_z_nazvu(row.get("item_name")))
    if nazev_serialu:
        srovnany = str(nazev_serialu).strip().lower()
        # Když ten samý seriál v knihovně známe, použijeme jeho id -
        # jinak by se rozpadl na dva řádky: jeden složený z importované
        # historie (kde je jen název) a druhý z vlastního sběru (kde už
        # je i id). Přesně to bylo vidět v přehledu.
        znamy = (podle_nazvu or {}).get(srovnany)
        if znamy:
            return (f"serial:{znamy}", str(nazev_serialu).strip(), True)

        # Klíč z názvu je slabší než z id, ale pořád sloučí to, co k sobě
        # patří. Porovnává se malými písmeny - "Blue" a "blue" je totéž.
        return (f"nazev:{srovnany}", str(nazev_serialu).strip(), True)

    # A ještě jedna možnost: název záznamu je rovnou jméno seriálu.
    # Stane se to u převzaté historie, která si epizodu nepamatuje,
    # nebo když Jellyfin zaznamená přehrávání na úrovni seriálu.
    jako_serial = (podle_nazvu or {}).get(
        str(row.get("item_name") or "").strip().lower())
    if jako_serial:
        return (f"serial:{jako_serial}", str(row["item_name"]).strip(), True)

    polozka = row.get("item_id") or row.get("item_name") or "?"
    return (f"polozka:{polozka}",
            str(row.get("i_name") or row.get("item_name") or "?"), False)


# Sloupce, ze kterých `klic_titulu()` skládá skupinu. Používají je oba
# dotazy níž (nejsledovanější tituly i překódované soubory), takže je
# lepší mít je na jednom místě než ve dvou kopiích, které se časem
# rozejdou.
#
# Seskupuje se přes SKUPINA_TITULU_KLIC, ne přes holé `p.item_id`: záznam
# bez id položky se v Jellyscope běžně nevyskytuje, ale kdyby se objevil,
# spadly by všechny takové řádky do jedné skupiny a vznikl by titul
# poskládaný z několika různých filmů.
SKUPINA_TITULU_KLIC = "COALESCE(p.item_id, p.item_name)"
SKUPINA_TITULU_SLOUPCE = """
        MAX(p.item_id)             AS item_id,
        MAX(p.item_name)           AS item_name,
        MAX(p.series_name)         AS series_name,
        MAX(i.id)                  AS existujici_id,
        MAX(i.series_id)           AS series_id,
        MAX(i.series_name)         AS i_series_name,
        MAX(i.name)                AS i_name,
        MAX(i.type)                AS i_type,
        MAX(i.is_missing)          AS is_missing
"""


def _slouc_tituly(radky: list[dict[str, Any]], kind: str, limit: int,
                  hodnoty: dict[str, str],
                  prenest: tuple[str, ...] = (),
                  skryt_nezarazene: bool = False) -> list[dict[str, Any]]:
    """Řádky za jednotlivé položky složí do titulů (seriál = jeden řádek).

    `hodnoty` říká, které sloupce se mají sečíst - u nejsledovanějších
    titulů jsou to hodiny a spuštění, u překódovaných počet transcodů.

    `prenest` jsou sloupce, které se nesčítají, ale opisují od nejsilnějšího
    dílu skupiny - kodek, rozlišení, důvod transcode. U seriálu jsou
    prakticky u všech dílů stejné, takže průměrovat je by nic nepřineslo.

    `skryt_nezarazene` vynechá tituly, ke kterým v knihovně **ani v archivu**
    nic nevede. Jsou to zbytky historie, u kterých se nedá zjistit, o co
    šlo: záznam nese jen "6. díl" nebo "Epizoda 6" a takový díl má v knihovně
    každý seriál, takže jednoznačná shoda neexistuje. Přiřadit je naslepo
    by znamenalo připsat sledování cizímu seriálu.

    V žebříčku nejsledovanějších takový řádek nic neříká - nejde
    prokliknout a tváří se jako film, který neexistuje. Kolik jich bylo,
    se vrací v `_skryto`, aby to stránka mohla napsat nahlas; odsledovaný
    čas se tím nikde neztrácí, Historie ho ukazuje dál.

    Slučuje se v Pythonu, ne v SQL: klíč skupiny umí spadnout až na
    rozbor názvu ("Seriál - s01e01 - Díl") a to se v SQL napsat srozumitelně
    nedá ani v jedné z obou databází.
    """
    # Nejdřív si projdeme řádky a zapamatujeme, které jméno seriálu patří
    # ke kterému id. Teprve pak se skládají skupiny - jinak by se stejný
    # seriál rozdělil podle toho, jestli ten který záznam id zná.
    podle_nazvu: dict[str, str] = {}
    for radek in radky:
        if not radek.get("series_id"):
            continue
        for jmeno in (radek.get("series_name"), radek.get("i_series_name"),
                      serial_z_nazvu(radek.get("item_name"))):
            if jmeno:
                podle_nazvu.setdefault(str(jmeno).strip().lower(),
                                       str(radek["series_id"]))

    # Doplníme názvy z knihovny, ne jen z řádků výsledku: seriál, ze
    # kterého je v období jen osiřelý záznam, by jinak v mapě chyběl
    # a neměl by se k čemu přiřadit.
    for serial, jmeno in _jmena_serialu().items():
        podle_nazvu.setdefault(jmeno.strip().lower(), serial)

    # A názvy dílů. Řeší případ, kdy záznam nese jen jméno epizody
    # ("Zvony") a k položce už nic nevede - v přehledu z něj jinak
    # vznikne samostatný "film" vedle seriálu, do kterého patří.
    podle_nazvu.update(_dily_podle_nazvu())

    skupiny: dict[str, dict[str, Any]] = {}

    for radek in radky:
        klic, popisek, je_serial = klic_titulu(radek, podle_nazvu)

        if kind == KIND_MOVIE and je_serial:
            continue
        if kind == KIND_SERIES and not je_serial:
            continue

        skupina = skupiny.get(klic)
        if skupina is None:
            skupina = {"group_key": klic, "label": popisek, "is_series": je_serial,
                       "_zastupci": [], "_nejsilnejsi": (-1.0, {})}
            for cil in hodnoty:
                skupina[cil] = 0.0
            skupiny[klic] = skupina
        elif je_serial and len(popisek) < len(skupina["label"]):
            # Kratší popisek je ten obecnější ("Blue" vs. "Blue - s01e17
            # - Calypso"), a o seriálu mluvíme jeho jménem.
            skupina["label"] = popisek

        vaha = float(radek.get(next(iter(hodnoty.values()))) or 0)
        for cil, zdroj in hodnoty.items():
            skupina[cil] += float(radek.get(zdroj) or 0)
        if vaha > skupina["_nejsilnejsi"][0]:
            skupina["_nejsilnejsi"] = (vaha, radek)

        # Zástupce pro proklik. Bere se i **archivovaná** položka, tedy
        # ta, která v knihovně byla a v Jellyfinu už není: sledování se
        # opravdu stalo, detail se otevře a je na něm vidět, že je titul
        # v archivu. Živá má přednost, proto to první číslo (0/1).
        #
        # Bez archivu by film, který jsi kdysi viděl a od té doby smazal,
        # spadl mezi "nezařazené" a `skryt_nezarazene` by ho vyhodil -
        # zmizela by platná statistika, ne odpad.
        if radek.get("existujici_id"):
            skupina["_zastupci"].append(
                (1 if radek.get("is_missing") else 0,
                 -float(radek.get(next(iter(hodnoty.values()))) or 0),
                 radek["existujici_id"], radek.get("series_id")))

    poradi = next(iter(hodnoty))
    serazene = sorted(skupiny.values(), key=lambda r: r[poradi], reverse=True)

    # Odkaz se skládá až tady, takže "nezařazený" se pozná až po něm.
    # Proto se ořezává na `limit` až nakonec - jinak by skryté řádky
    # ubraly místo těm, které se mají ukázat.
    for skupina in serazene:
        _, nejsilnejsi = skupina.pop("_nejsilnejsi")
        for sloupec in prenest:
            skupina[sloupec] = nejsilnejsi.get(sloupec)

        zastupci = skupina.pop("_zastupci")
        if not zastupci:
            # Žádný záznam téhle skupiny neukazuje na živou položku. Když
            # ale skupinu určil seriál z knihovny, odkaz máme i tak - klíč
            # nese jeho id. Nastane to u historie, kde k dílu už položka
            # nevede, ale sám seriál v knihovně pořád je.
            klic = str(skupina["group_key"])
            skupina["detail_url"] = ("/series/" + klic[len("serial:"):]
                                     if klic.startswith("serial:") else None)
            # Titul, ke kterému už v knihovně není vůbec nic, zůstane
            # bez odkazu - odkaz na chybovou stránku 404 je horší než žádný.
            continue
        # Živé napřed (0 < 1), v rámci toho nejsledovanější (váha je
        # záporná, takže obyčejné vzestupné řazení dá největší první).
        # Odkaz se řídí **klíčem skupiny**, ne nejsilnějším záznamem.
        #
        # Tohle byl nahlášený bug: skupina seriálu se jmenovala správně,
        # ale proklik vedl na cizí položku. Stane se to, když do skupiny
        # spadne záznam, který se k seriálu přiřadil podle názvu - ten
        # `series_id` sám nenese (proto se hádalo z názvu), takže z něj
        # vyšel odkaz na jeho vlastní položku. A protože se zástupce
        # vybírá podle odsledovaného času, stačilo, aby byl takový záznam
        # nejsilnější, a odkaz zamířil docela jinam než popisek.
        #
        # Klíč `serial:<id>` je přitom to nejspolehlivější, co o skupině
        # víme - id seriálu z knihovny. Když ho máme, odkaz nemá být
        # z čeho jiného.
        klic = str(skupina["group_key"])
        if klic.startswith("serial:"):
            skupina["detail_url"] = "/series/" + klic[len("serial:"):]
            continue

        zastupci.sort()
        _, _, polozka, serial = zastupci[0]
        skupina["detail_url"] = (f"/series/{serial}" if serial
                                 else f"/item/{polozka}")

    if not skryt_nezarazene:
        return serazene[:limit]

    zarazene = [s for s in serazene if s["detail_url"]]

    # Když by skrývání nenechalo vůbec nic, neskrývá se. Nastane to
    # v jediné situaci, zato důležité: historie se naimportuje DŘÍV, než
    # se aplikace napojí na Jellyfin. Knihovna je pak prázdná, takže žádný
    # titul nemá protějšek - a člověk by po importu koukal na prázdný
    # žebříček a myslel si, že se nic nenaimportovalo. Jakmile
    # synchronizace proběhne, tituly se navážou a skrývání začne dávat
    # smysl samo.
    if not zarazene:
        return serazene[:limit]

    skryto = len(serazene) - len(zarazene)
    vysledek = zarazene[:limit]
    if vysledek:
        vysledek[0]["_skryto"] = skryto
    return vysledek


def top_items(days: int, limit: int = 10,
              kind: str = KIND_BOTH) -> list[dict[str, Any]]:
    """Nejsledovanejsi tituly. Epizody stejneho serialu se scitaji.

    `kind` je "both" / "movies" / "series" - stejne tri moznosti jako
    u sledovanosti po dnech, at se clovek nemusi ucit dve ruzne ovladani.

    Jak se serial pozna a kam vede proklik, popisuje `klic_titulu()`
    a `_slouc_tituly()`.
    """
    radky = db.query_all(
        f"""
        SELECT {SKUPINA_TITULU_SLOUPCE},
               SUM(p.watched_seconds) AS seconds,
               COUNT(*)               AS plays
        FROM playback p
   LEFT JOIN items i ON i.id = p.item_id
       WHERE p.started_at >= datetime('now', ?)
         AND p.watched_seconds > 0
    GROUP BY {SKUPINA_TITULU_KLIC}
        """,
        (_range(days),),
    )

    tituly = _slouc_tituly(radky, kind, limit,
                           {"seconds": "seconds", "plays": "plays"},
                           skryt_nezarazene=True)
    for titul in tituly:
        titul["hours"] = titul["seconds"] / 3600.0
        titul["plays"] = int(titul["plays"])
    return tituly


def play_method_breakdown(days: int) -> list[dict[str, Any]]:
    """Kolik casu se hralo primo a kolik se muselo prepocitavat."""
    rows = db.query_all(
        """
        SELECT COALESCE(play_method, 'nezname') AS method,
               SUM(watched_seconds) / 3600.0    AS hours
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds > 0
        GROUP BY method
        ORDER BY hours DESC
        """,
        (_range(days),),
    )
    # Popisky prochazeji `_t()`, protoze se vypisuji uzivateli - jak
    # v legende, tak primo v pruhu grafu.
    labels = {
        "DirectPlay": _t("Přímé přehrávání"),
        "DirectStream": _t("Přebalení (direct stream)"),
        "Transcode": _t("Transcode"),
        "nezname": _t("Neznámé"),
    }
    for row in rows:
        row["label"] = labels.get(row["method"], row["method"])
        # Graf deleneho pruhu ocekava klic "value" - pripravime ho tady,
        # at sablona nemusi nic pocitat.
        row["value"] = row["hours"]
    return rows


def client_breakdown(days: int, limit: int = 8) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT COALESCE(client, 'nezname') AS label,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*) AS plays
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds > 0
        GROUP BY label
        ORDER BY hours DESC
        LIMIT ?
        """,
        (_range(days), limit),
    )


def hourly_heatmap(days: int) -> list[list[float]]:
    """Mrizka 7 dnu x 24 hodin s poctem odsledovanych hodin.

    Vraci seznam sedmi radku (pondeli az nedele), kazdy o 24 hodnotach.
    """
    rows = db.query_all(
        """
        SELECT CAST(strftime('%w', started_at, 'localtime') AS INTEGER) AS weekday,
               CAST(strftime('%H', started_at, 'localtime') AS INTEGER) AS hour,
               SUM(watched_seconds) / 3600.0 AS hours
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds > 0
        GROUP BY weekday, hour
        """,
        (_range(days),),
    )

    grid = [[0.0 for _ in range(24)] for _ in range(7)]
    for row in rows:
        # SQLite pocita tyden od nedele (0). My chceme od pondeli.
        weekday = (int(row["weekday"]) + 6) % 7
        grid[weekday][int(row["hour"])] += float(row["hours"] or 0)
    return grid


def history(
    limit: int = 50,
    offset: int = 0,
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_BOTH,
) -> list[dict[str, Any]]:
    """Surova historie prehravani, strankovana.

    `day` je datum ve tvaru "2026-08-13" a omezi vypis na jeden den -
    pouziva se pri prokliku z tabulky na Prehledu. Porovnava se stejnym
    vyrazem, jakym se den pocita v grafu (mistni cas), aby proklik
    ukazal presne to, co bylo v tabulce.
    """
    where = ["p.watched_seconds > 0"]
    params: list[Any] = []

    # Nazvy sloupcu maji prefix tabulky (p.), protoze dotaz spojuje playback
    # a items - a obe tabulky maji sloupec series_name. Bez prefixu by
    # SQLite nevedela, kterou z nich myslime, a dotaz by skoncil chybou.
    if user_id:
        where.append("p.user_id = ?")
        params.append(user_id)
    if search:
        where.append("(p.item_name LIKE ? OR p.series_name LIKE ?)")
        # Znaky % kolem hledaneho textu znamenaji "kdekoliv uvnitr".
        # Text davame jako parametr (?), nikdy ho nelepime do SQL primo -
        # tak vznika SQL injection.
        params.extend([f"%{search}%", f"%{search}%"])
    if day:
        where.append("date(p.started_at, 'localtime') = ?")
        params.append(day)
    # Stejna podminka jako v grafu, jen s prefixem tabulky - aby seznam
    # ukazoval presne to, co je nad nim v krivce.
    podminka = _kind_condition(kind, "p.")
    if podminka:
        where.append(podminka.replace(" AND ", "", 1))

    params.extend([limit, offset])
    return db.query_all(
        f"""
        SELECT p.*,
               -- Přednost mají rozměry zaznamenané u přehrávání: relace ví,
               -- co doopravdy teklo, kdežto `items` popisuje soubor tak, jak
               -- ho známe z poslední synchronizace. Do `items` sáhneme jen
               -- u starších záznamů, které rozměry ještě neukládaly.
               --
               -- A **oba** rozměry, ne jen výšku. Dřív se vybírala jen
               -- `i.height` a šířka do šablony nedorazila vůbec; 4K film
               -- v poměru scope (3840×1608) pak vyšel jako 1080p, protože
               -- 1608 je pod hranicí pro výšku. Rozlišení se pozná podle
               -- šířky - viz RESOLUTION_CASE.
               COALESCE(p.video_height, i.height) AS height,
               COALESCE(p.video_width,  i.width)  AS width,
               i.video_codec AS source_video_codec,
               i.size_bytes
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE {' AND '.join(where)}
        ORDER BY p.started_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def history_count(user_id: str | None = None, search: str | None = None,
                  day: str | None = None, kind: str = KIND_BOTH) -> int:
    where = ["watched_seconds > 0"]
    params: list[Any] = []
    if day:
        where.append("date(started_at, 'localtime') = ?")
        params.append(day)
    where.append("1 = 1" + _kind_condition(kind))
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if search:
        where.append("(item_name LIKE ? OR series_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    return int(db.query_value(
        f"SELECT COUNT(*) FROM playback WHERE {' AND '.join(where)}", tuple(params)
    ))


# ---------------------------------------------------------------------------
# Uzivatele
# ---------------------------------------------------------------------------

def user_table(days: int) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT u.id,
               u.name,
               u.is_administrator,
               COALESCE(SUM(p.watched_seconds), 0) / 3600.0 AS hours,
               COUNT(p.id)                                  AS plays,
               COUNT(DISTINCT p.item_id)                    AS item_count,
               MAX(p.started_at)                            AS last_seen,
               COALESCE(SUM(CASE WHEN p.play_method = 'Transcode'
                                 THEN p.watched_seconds ELSE 0 END), 0) / 3600.0
                                                            AS transcoded_hours
        FROM users u
        LEFT JOIN playback p
               ON p.user_id = u.id
              AND p.started_at >= datetime('now', ?)
              AND p.watched_seconds > 0
        GROUP BY u.id, u.name, u.is_administrator
        ORDER BY hours DESC, u.name
        """,
        (_range(days),),
    )


def _s_prokliknutim(rows: list[dict[str, Any]],
                    zastupci: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ke slucovanym radkum doplni id polozky, na kterou se da prokliknout.

    `zastupci` jsou serazeni od nejsledovanejsiho, takze prvni nalez pro
    dany nazev je ten spravny.
    """
    nejlepsi: dict[str, str] = {}
    for row in zastupci:
        nejlepsi.setdefault(str(row["label"]), str(row["item_id"]))
    for row in rows:
        row["item_id"] = nejlepsi.get(str(row["label"]))
    return rows


def user_genres(user_id: str, days: int, limit: int = 8) -> list[dict[str, Any]]:
    """Zanry, ktere uzivatel sleduje - podle odsledovaneho casu.

    Jeden titul ma zanru vic ("Akcni, Sci-Fi, Dobrodruzny"), takze se jeho
    cas zapocita do kazdeho z nich. Soucet proto neni roven celkovemu casu
    a procenta se nescitaji na sto - v UI je u toho poznamka.

    Rozdelit cas mezi zanry rovnym dilem by vypadalo poctiveji, ale nebylo
    by to o nic pravdivejsi: nikdo nevi, kolik z filmu bylo "akce" a kolik
    "sci-fi". Radeji rekneme nahlas, ze se titul pocita vickrat.
    """
    rows = db.query_all(
        """
        SELECT i.genres, SUM(p.watched_seconds) / 3600.0 AS hours
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.user_id = ? AND p.started_at >= datetime('now', ?)
           AND p.watched_seconds > 0
           AND i.genres IS NOT NULL AND i.genres != ''
         GROUP BY i.genres
        """,
        (user_id, _range(days)),
    )

    soucty: dict[str, float] = {}
    for row in rows:
        hodin = float(row["hours"] or 0)
        for zanr in str(row["genres"]).split("|"):
            zanr = zanr.strip()
            if zanr:
                soucty[zanr] = soucty.get(zanr, 0.0) + hodin

    if not soucty:
        return []

    serazene = sorted(soucty.items(), key=lambda dvojice: -dvojice[1])
    celkem = sum(soucty.values())

    vysledek = [
        {"label": zanr, "hours": hodin, "value": hodin,
         "percent": hodin / celkem * 100 if celkem else 0.0}
        for zanr, hodin in serazene[:limit]
    ]
    # Zbytek do jednoho radku. Dlouhy seznam zanru, z nichz kazdy ma pul
    # hodiny, nikdo necte - a to hlavni v nem zanikne.
    zbytek = serazene[limit:]
    if zbytek:
        hodin = sum(h for _zanr, h in zbytek)
        vysledek.append({
            "label": _t("Ostatní"), "hours": hodin, "value": hodin,
            "percent": hodin / celkem * 100 if celkem else 0.0,
            "genres": len(zbytek),
        })
    return vysledek


def user_detail(user_id: str, days: int) -> dict[str, Any]:
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        return {}

    return {
        "user": user,
        "totals": db.query_one(
            """
            SELECT COUNT(*) AS plays,
                   COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
                   COUNT(DISTINCT item_id) AS item_count
            FROM playback
            WHERE user_id = ? AND started_at >= datetime('now', ?) AND watched_seconds > 0
            """,
            (user_id, _range(days)),
        ) or {},
        # `item_id` je tu kvuli prokliku do knihovny. U serialu se radky
        # slucuji podle nazvu, takze zadne jedno spravne id neexistuje -
        # vezmeme to nejsledovanejsi, coz je i to, ktere clovek hleda.
        "top_items": _s_prokliknutim(
            db.query_all(
                """
                SELECT COALESCE(series_name, item_name) AS label,
                       SUM(watched_seconds) / 3600.0 AS hours,
                       COUNT(*) AS plays
                FROM playback
                WHERE user_id = ? AND started_at >= datetime('now', ?)
                  AND watched_seconds > 0
                GROUP BY label
                ORDER BY hours DESC
                LIMIT 10
                """,
                (user_id, _range(days)),
            ),
            db.query_all(
                """
                SELECT COALESCE(series_name, item_name) AS label, item_id,
                       SUM(watched_seconds) AS total
                FROM playback
                WHERE user_id = ? AND started_at >= datetime('now', ?)
                  AND watched_seconds > 0 AND item_id IS NOT NULL
                GROUP BY label, item_id
                ORDER BY total DESC
                """,
                (user_id, _range(days)),
            ),
        ),
        "genres": user_genres(user_id, days),
        "devices": db.query_all(
            """
            SELECT COALESCE(device_name, 'nezname') AS label,
                   COALESCE(client, '') AS client,
                   SUM(watched_seconds) / 3600.0 AS hours
            FROM playback
            WHERE user_id = ? AND started_at >= datetime('now', ?) AND watched_seconds > 0
            GROUP BY label, client
            ORDER BY hours DESC
            LIMIT 10
            """,
            (user_id, _range(days)),
        ),
    }


# ---------------------------------------------------------------------------
# Knihovna (technicka cast - to, co umi MediaLyze)
# ---------------------------------------------------------------------------

def tech_coverage() -> dict[str, Any]:
    """Kolik polozek ma technicka data a odkud pochazeji."""
    return db.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN tech_source = 'ffprobe'  THEN 1 ELSE 0 END) AS from_ffprobe,
               SUM(CASE WHEN tech_source = 'jellyfin' THEN 1 ELSE 0 END) AS from_jellyfin,
               SUM(CASE WHEN tech_source IS NULL      THEN 1 ELSE 0 END) AS missing,
               SUM(CASE WHEN tech_error IS NOT NULL   THEN 1 ELSE 0 END) AS errors
        FROM items
        WHERE is_missing = 0
        """
    ) or {}


def codec_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0 AND video_codec IS NOT NULL"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    return db.query_all(
        f"""
        SELECT UPPER(video_codec)               AS label,
               COUNT(*)                         AS item_count,
               COALESCE(SUM(size_bytes), 0)     AS size_bytes
        FROM items {where}
        GROUP BY UPPER(video_codec)
        ORDER BY item_count DESC
        """,
        tuple(params),
    )


def resolution_breakdown(library_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE is_missing = 0"
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    rows = db.query_all(
        f"""
        SELECT {RESOLUTION_CASE} AS label,
               COUNT(*) AS item_count,
               COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM items {where}
        GROUP BY label
        """,
        tuple(params),
    )
    # Seradime podle sve vlastni logiky (4K, 1080p, ...), ne abecedne.
    order = {label: index for index, label in enumerate(RESOLUTION_ORDER)}
    rows.sort(key=lambda row: order.get(row["label"], 99))
    return rows


def video_range_breakdown() -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT COALESCE(video_range, 'nezname') AS label,
               COUNT(*) AS item_count
        FROM items
        WHERE is_missing = 0 AND tech_source IS NOT NULL
        GROUP BY label
        ORDER BY item_count DESC
        """
    )


# ---------------------------------------------------------------------------
# Knihovna po titulech, ne po dilech
# ---------------------------------------------------------------------------
#
# V tabulce `items` je kazda epizoda samostatny radek - tak je posila
# Jellyfin a tak je potrebujeme pro statistiky. V seznamu knihovny je to
# ale k nicemu: serial o peti radach zabere sto radku a mezi nimi zanikne
# vsechno ostatni.
#
# Seskupujeme proto podle serialu. Klic skupiny je `COALESCE(series_id, id)`,
# takze film (ktery series_id nema) tvori skupinu sam za sebe a stejny dotaz
# obslouzi obojí - zadne dva dotazy, ktere by se casem rozesly.
_SKUPINA = "COALESCE(i.series_id, i.id)"

# Sloupce skupiny. U filmu je skupina jednoprvkova, takze SUM i MAX vraci
# proste jeho vlastni hodnotu - proto nemusime rozlisovat.
_SKUPINA_SLOUPCE = f"""
        {_SKUPINA}                                   AS id,
        MAX(CASE WHEN i.series_id IS NULL THEN 0 ELSE 1 END) AS is_series,
        MAX(COALESCE(i.series_name, i.name))         AS name,
        MAX(i.type)                                  AS type,
        MAX(i.library_id)                            AS library_id,
        COUNT(*)                                     AS episode_count,
        COUNT(DISTINCT i.parent_index_number)        AS season_count,
        COALESCE(SUM(i.size_bytes), 0)               AS size_bytes,
        COALESCE(SUM(i.runtime_ticks), 0)            AS runtime_ticks,
        MAX(i.width)                                 AS width,
        MAX(i.height)                                AS height,
        MAX(i.bitrate)                               AS bitrate,
        MAX(i.video_codec)                           AS video_codec,
        MAX(i.production_year)                       AS production_year,
        MAX(i.date_created)                          AS date_created,
        MAX(i.tech_source)                           AS tech_source,
        -- Skupina je bud cela ziva, nebo cela archivovana - filtr je
        -- ve WHERE. Sablona to potrebuje kvuli znacce "Archivovano".
        MAX(i.is_missing)                            AS is_missing,
        COALESCE(SUM(COALESCE(p.plays, 0)), 0)       AS plays
"""

# Prehrani si spocitame jednim dotazem dopredu a pripojime. Poddotaz na
# kazdy radek by u seskupeni nesel pouzit a u velke knihovny by stejne
# znamenal tisice dotazu navic.
_PREHRANI = """
    LEFT JOIN (SELECT item_id, COUNT(*) AS plays
                 FROM playback WHERE watched_seconds > 60
                GROUP BY item_id) p ON p.item_id = i.id
"""


def _knihovna_filtr(library_id: str | None, search: str | None,
                    archived: bool) -> tuple[list[str], list[Any]]:
    where = ["i.is_missing = 1" if archived else "i.is_missing = 0"]
    params: list[Any] = []
    if library_id:
        where.append("i.library_id = ?")
        params.append(library_id)
    if search:
        where.append("(i.name LIKE ? OR i.series_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    return where, params


def library_rows(limit: int, offset: int, library_id: str | None = None,
                 search: str | None = None, sort: str = "size",
                 archived: bool = False) -> list[dict[str, Any]]:
    """Seznam knihovny: filmy jednotlive, serialy jako jeden radek."""
    where, params = _knihovna_filtr(library_id, search, archived)

    # Razeni se do SQL nesmi vlepit z uzivatelskeho vstupu primo. Pevny
    # seznam moznosti a bereme z nej.
    sort_options = {
        "size": "size_bytes DESC",
        "bitrate": "bitrate DESC",
        "name": "name ASC",
        "plays": "plays DESC",
        "resolution": "height DESC",
    }
    order_by = sort_options.get(sort, sort_options["size"])

    params.extend([limit, offset])
    return db.query_all(
        f"""
        SELECT {_SKUPINA_SLOUPCE}
        FROM items i
        {_PREHRANI}
        WHERE {' AND '.join(where)}
        GROUP BY {_SKUPINA}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def library_rows_count(library_id: str | None = None, search: str | None = None,
                       archived: bool = False) -> int:
    """Kolik radku seznam ma - tedy skupin, ne polozek."""
    where, params = _knihovna_filtr(library_id, search, archived)
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM (
            SELECT {_SKUPINA} AS id FROM items i
             WHERE {' AND '.join(where)}
             GROUP BY {_SKUPINA}
        ) skupiny
        """,
        tuple(params),
    ))


def series_detail(series_id: str) -> dict[str, Any]:
    """Serial rozdeleny na rady a v nich dily.

    Vraci prazdny slovnik, kdyz takovy serial neznáme - routa z toho
    udela 404.

    **Archivovane dily se nepocitaji mezi zive.** Archiv je seznam
    souboru, ktere v Jellyfinu uz nejsou - historie prehravani u nich
    zustava, ale titul sam zmizel. Drive je detail serialu vypisoval
    vedle zivych bez rozdilu, zatimco seznam knihovny je vynechaval;
    serial pak v Jellyfinu ukazoval sto dilu a tady sto dvacet, aniz
    by z toho slo poznat proc.

    Vypisujeme je proto zvlast a rekneme, kolik jich je.
    """
    vsechny = db.query_all(
        f"""
        SELECT i.*, COALESCE(p.plays, 0) AS plays
        FROM items i
        {_PREHRANI}
        WHERE i.series_id = ?
        ORDER BY i.parent_index_number, i.index_number, i.name
        """,
        (series_id,),
    )
    if not vsechny:
        return {}

    dily = [d for d in vsechny if not d["is_missing"]]
    archivovane = [d for d in vsechny if d["is_missing"]]

    # Serial, ze ktereho zbyl uz jen archiv, porad ma smysl ukazat -
    # jinak by odkaz z historie skoncil na 404.
    if not dily:
        dily, archivovane = archivovane, []

    rady: list[dict[str, Any]] = []
    podle_rady: dict[Any, dict[str, Any]] = {}
    for dil in dily:
        cislo = dil["parent_index_number"]
        rada = podle_rady.get(cislo)
        if rada is None:
            rada = {
                "number": cislo,
                # Nazev rady mame jen u nekterych polozek; kdyz chybi,
                # slozime ho z cisla.
                "name": dil["season_name"] or (
                    f"{_t('Řada')} {cislo}" if cislo is not None else _t("Ostatní")),
                "episodes": [],
                "size_bytes": 0,
                "plays": 0,
            }
            podle_rady[cislo] = rada
            rady.append(rada)
        rada["episodes"].append(dil)
        rada["size_bytes"] += int(dil["size_bytes"] or 0)
        rada["plays"] += int(dil["plays"] or 0)

    prvni = dily[0]
    return {
        "id": series_id,
        "name": prvni["series_name"] or prvni["name"],
        "library_id": prvni["library_id"],
        "seasons": rady,
        "episode_count": len(dily),
        "season_count": len(rady),
        "size_bytes": sum(int(d["size_bytes"] or 0) for d in dily),
        "runtime_ticks": sum(int(d["runtime_ticks"] or 0) for d in dily),
        "plays": sum(int(d["plays"] or 0) for d in dily),
        "missing": all(d["is_missing"] for d in dily),
        # Díly, které v Jellyfinu už nejsou. Vypisují se zvlášť, ať je
        # z rozdílu proti Jellyfinu hned jasné, čím je způsobený.
        "archived": archivovane,
        "archived_count": len(archivovane),
    }


def library_cards() -> list[dict[str, Any]]:
    """Knihovny s cisly pro uvodni dlazdice.

    `poster_id` je polozka, jejiz obrazek se pouzije jako pozadi dlazdice.
    Knihovny samotne casto vlastni obrazek nemaji, tak vezmeme nejvetsi
    titul uvnitr - ten byva i vizualne vyrazny.

    U serialove knihovny bereme **serial**, ne epizodu. Nejvetsi polozka
    v takove knihovne je vzdycky nejaky dil - a epizoda v Jellyfinu
    obvykle zadny backdrop nema, ten patri serialu. Dlazdice serialu proto
    zustavala seda: obrazek se nenacetl a `onerror` ho odstranil.
    """
    rows = db.query_all(
        """
        SELECT l.id,
               l.name,
               l.collection_type,
               COUNT(i.id)                    AS item_count,
               COALESCE(SUM(i.size_bytes), 0) AS size_bytes,
               COALESCE(SUM(i.runtime_ticks), 0) / 36000000000.0 AS hours,
               (SELECT COALESCE(s.series_id, s.id) FROM items s
                 WHERE s.library_id = l.id AND s.is_missing = 0
                 ORDER BY s.size_bytes DESC LIMIT 1) AS poster_id
        FROM libraries l
        LEFT JOIN items i ON i.library_id = l.id AND i.is_missing = 0
        GROUP BY l.id, l.name, l.collection_type
        ORDER BY item_count DESC, l.name
        """
    )
    type_names = {
        "movies": _t("Filmy"),
        "tvshows": _t("Seriály"),
        "homevideos": _t("Domácí videa"),
        "mixed": _t("Smíšený obsah"),
    }
    for row in rows:
        row["type_label"] = type_names.get(row["collection_type"], _t("Ostatní"))
    return rows


def library(library_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM libraries WHERE id = ?", (library_id,))


def library_overview(library_id: str) -> dict[str, Any]:
    """Souhrnna cisla jedne knihovny."""
    return db.query_one(
        f"""
        SELECT COUNT(*)                       AS item_count,
               COALESCE(SUM(size_bytes), 0)   AS size_bytes,
               COALESCE(SUM(runtime_ticks), 0) / 36000000000.0 AS hours,
               COALESCE(AVG(bitrate), 0)      AS avg_bitrate,
               SUM(CASE WHEN {RESOLUTION_CASE} = '4K' THEN 1 ELSE 0 END) AS uhd_count,
               SUM(CASE WHEN video_range IN ('HDR', 'DOVI') THEN 1 ELSE 0 END) AS hdr_count,
               SUM(CASE WHEN tech_source IS NULL THEN 1 ELSE 0 END) AS without_tech
        FROM items
        WHERE is_missing = 0 AND library_id = ?
        """,
        (library_id,),
    ) or {}


def library_activity(library_id: str, days: int = 90) -> dict[str, Any]:
    """Co se v teto knihovne sledovalo."""
    return {
        "totals": db.query_one(
            """
            SELECT COUNT(*) AS plays,
                   COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
                   COUNT(DISTINCT user_id) AS users,
                   COUNT(DISTINCT item_id) AS item_count
            FROM playback
            WHERE library_id = ? AND started_at >= datetime('now', ?)
              AND watched_seconds > 0
            """,
            (library_id, _range(days)),
        ) or {},
        "top_items": db.query_all(
            """
            SELECT COALESCE(series_name, item_name) AS label,
                   SUM(watched_seconds) / 3600.0 AS hours,
                   COUNT(*) AS plays
            FROM playback
            WHERE library_id = ? AND started_at >= datetime('now', ?)
              AND watched_seconds > 0
            GROUP BY label ORDER BY hours DESC LIMIT 10
            """,
            (library_id, _range(days)),
        ),
        "top_users": db.query_all(
            """
            SELECT user_name AS label, SUM(watched_seconds) / 3600.0 AS hours
            FROM playback
            WHERE library_id = ? AND started_at >= datetime('now', ?)
              AND watched_seconds > 0 AND user_name IS NOT NULL
            GROUP BY label ORDER BY hours DESC LIMIT 10
            """,
            (library_id, _range(days)),
        ),
        "recent": db.query_all(
            """
            SELECT p.*, i.height
            FROM playback p
            LEFT JOIN items i ON i.id = p.item_id
            WHERE p.library_id = ? AND p.watched_seconds > 0
            ORDER BY p.started_at DESC LIMIT 25
            """,
            (library_id,),
        ),
    }


# ---------------------------------------------------------------------------
# Detail jedne polozky
# ---------------------------------------------------------------------------

def item(item_id: str) -> dict[str, Any] | None:
    return db.query_one(
        """
        SELECT i.*, l.name AS library_name, l.collection_type
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.id = ?
        """,
        (item_id,),
    )


def item_streams(item_id: str) -> dict[str, list[dict[str, Any]]]:
    """Stopy polozky rozdelene na video, zvuk a titulky."""
    rows = db.query_all(
        "SELECT * FROM item_streams WHERE item_id = ? ORDER BY type, stream_index",
        (item_id,),
    )
    grouped: dict[str, list[dict[str, Any]]] = {"Video": [], "Audio": [], "Subtitle": []}
    for row in rows:
        grouped.setdefault(row["type"], []).append(row)
    return grouped


def item_playback(item_id: str, limit: int = 25) -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT * FROM playback
        WHERE item_id = ? AND watched_seconds > 0
        ORDER BY started_at DESC LIMIT ?
        """,
        (item_id, limit),
    )


def item_playback_summary(item_id: str) -> dict[str, Any]:
    return db.query_one(
        """
        SELECT COUNT(*) AS plays,
               COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
               COUNT(DISTINCT user_id) AS users,
               MAX(started_at) AS last_played
        FROM playback
        WHERE item_id = ? AND watched_seconds > 0
        """,
        (item_id,),
    ) or {}


def sibling_episodes(item_row: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    """Ostatni epizody tehoz serialu - navigace v detailu epizody."""
    if not item_row.get("series_id"):
        return []
    return db.query_all(
        """
        SELECT id, name, index_number, parent_index_number, size_bytes, height
        FROM items
        WHERE series_id = ? AND is_missing = 0
        ORDER BY parent_index_number, index_number
        LIMIT ?
        """,
        (item_row["series_id"], limit),
    )
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


def delete_item(item_id: str) -> dict[str, Any]:
    """Nenavratne smaze polozku i jeji historii prehravani.

    Tohle je jedina cesta, jak z Jellyscope neco doopravdy zmizi, a je
    zamerne jen rucni. Automaticky se polozky vzdycky jen archivuji -
    kdyz Jellyfin na chvili vypadne nebo se prepoji uloziste, prisel bys
    jinak o historii kvuli docasnemu vypadku.

    Vraci, co se smazalo, aby slo uzivateli rict, o co prisel.
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, series_name FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return {"status": "error", "message": "Položka neexistuje."}

        plays = int(conn.execute(
            "SELECT COUNT(*) AS c FROM playback WHERE item_id = ?", (item_id,)
        ).fetchone()["c"])

        # Stopy zmizi samy pres ON DELETE CASCADE, historii mazeme rucne -
        # cizi klic tam schvalne neni, aby vypadek Jellyfinu nikdy
        # nemohl smazat statistiky.
        conn.execute("DELETE FROM playback WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM item_streams WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

    return {
        "status": "ok",
        "name": row["name"],
        "series_name": row["series_name"],
        "plays": plays,
    }
