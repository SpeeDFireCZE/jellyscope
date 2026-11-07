"""Co se hraje právě teď a popis přepočtu (transcode).

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from typing import Any

from .. import db


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


def rychlost_prevodu(row: dict[str, Any]) -> dict[str, Any] | None:
    """Kolik snimku za vterinu prevod zvladal - a jestli to stacilo.

    U bezicho prehravani je zajimavy stav TED (prave seka?), u historie
    prumer za cele prehravani - jedno cislo z posledniho snimku by bylo
    nahodne. Kdyz jedno chybi, vezme se druhe.

    `nestiha` je pravda jen tehdy, kdyz je rychlost znatelne pod
    snimkovanim videa: prevod, ktery jede presne tempem videa, je
    v poradku, a vterinova odchylka je sum mereni.
    """
    # Obraz se jen kopiroval - zadny prevod, jehoz rychlost by mela smysl.
    if row.get("transcode_video_direct") == 1:
        return None
    vzorku = int(row.get("transcode_fps_vzorku") or 0)
    prumer = (round(int(row.get("transcode_fps_soucet") or 0) / vzorku)
              if vzorku else None)
    ted = row.get("transcode_fps")
    if row.get("is_active"):
        fps, je_prumer = (ted, False) if ted else (prumer, True)
    else:
        fps, je_prumer = (prumer, True) if prumer else (ted, False)
    if not fps:
        return None
    video = row.get("video_fps") or None
    return {"co": "Snímky", "fps": int(fps), "prumer": je_prumer,
            "video_fps": video,
            "nestiha": bool(video and int(fps) < int(video) - 1)}


def popis_prepoctu(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Co presne server u tohohle prehravani prepocitava.

    Znacka "transcode" sama o sobe nerika skoro nic. Prepocet obrazu
    vytizi procesor nasobne vic nez prepocet zvuku - a kdyz clovek hleda,
    proc server topi, je to prvni vec, kterou potrebuje videt. Rozdil
    hlasi Jellyfin sam v `TranscodingInfo` (viz collector).

    Vraci seznam faktu, ne hotovou vetu: prekladat se ma v sablone, kde
    je jazyk prihlaseneho cloveka. Kazdy fakt je slovnik:

        {"co": "Obraz", "primo": False, "z": "hevc", "na": "h264"}
        {"co": "Snímky", "fps": 96, "prumer": True, "video_fps": 24, ...}
        {"co": "Titulky", "vypaluji": True}
        {"co": "Hardware", "text": "qsv"}

    U starsich zaznamu (a u importovane historie) priznak chybi. Tehdy
    se odvodi z kodeku: stejny kodek na obou stranach znamena, ze se
    stopa jen prebaluje. Presne to totiz "primy" znamena - a oba kodeky
    jsou vedle sebe videt, takze si to clovek muze overit.
    """
    if (row.get("play_method") or "") != "Transcode":
        return []

    fakta: list[dict[str, Any]] = []
    for co, primo, zdroj, cil in (
        ("Obraz", row.get("transcode_video_direct"),
         row.get("source_codec"), row.get("video_codec")),
        ("Zvuk", row.get("transcode_audio_direct"),
         row.get("source_audio_codec"), row.get("audio_codec")),
    ):
        if primo is None:
            if not zdroj or not cil:
                continue   # nemame co rict, radeji mlcime
            primo = str(zdroj).lower() == str(cil).lower()
        fakta.append({"co": co, "primo": bool(primo), "z": zdroj, "na": cil})

    rychlost = rychlost_prevodu(row)
    if rychlost:
        fakta.append(rychlost)

    # Titulky se do obrazu vypaluji - tedy prepocet obrazu, i kdyz je
    # kodek podporovany. Jellyfin to rekne jedine timhle duvodem.
    duvody = (row.get("transcode_reasons") or "").lower()
    if "subtitle" in duvody:
        fakta.append({"co": "Titulky", "vypaluji": True})

    if row.get("transcode_hw"):
        fakta.append({"co": "Hardware", "text": str(row["transcode_hw"])})

    return fakta


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
               i.image_tag,
               i.series_image_tag,
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
        # U epizody chceme plakat serialu, ne snimek z dilu - a k nemu
        # otisk toho spravneho plakatu, at se pri zmene neservíruje stary.
        row["poster_id"] = row.get("series_id") or row.get("item_id")
        row["poster_tag"] = (row.get("series_image_tag")
                             if row.get("series_id") else row.get("image_tag"))
        # Co presne se prepocitava - do bubliny u znacky "transcode".
        row["prepocet"] = popis_prepoctu(row)

    return rows
