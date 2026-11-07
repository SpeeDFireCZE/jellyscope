"""Zápis položek, verzí, obrázků a stop; sloučení archivu do živých; úklid fantomů.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db
from .slouceni import _merge_by_tmdb
from .typy import ZAPISOVANE_TYPY

log = logging.getLogger("jellyscope.scanner")


def zapis_verze(verze: list[tuple[str, list[dict[str, Any]]]]) -> int:
    """Uloží verze titulů. Co u titulu nepřišlo, se smaže.

    Mazání je schválně: když se jedna ze dvou verzí z disku odstraní,
    Jellyfin ji přestane posílat a v detailu by po ní zůstal řádek
    slibující soubor, který tam není.
    """
    if not verze:
        return 0
    ted = db.utcnow()
    zapsano = 0
    with db.connect() as conn:
        for item_id, seznam in verze:
            if not item_id or not seznam:
                # Prazdny seznam neznamena "soubory zmizely", ale
                # "Jellyfin je v teto odpovedi neposlal" - treba dotaz
                # bez `MediaSources` ve `Fields`. Smazat podle toho, co
                # nikdo netvrdil, by v detailu vymazalo verze, ktere na
                # disku dal jsou.
                continue
            conn.execute("DELETE FROM item_versions WHERE item_id = ?",
                         (item_id,))
            for v in seznam:
                conn.execute(
                    "INSERT INTO item_versions (item_id, source_id, poradi,"
                    " nazev, path, container, video_codec, audio_codec,"
                    " audio_channels, width, height, bitrate, size_bytes,"
                    " video_range, runtime_ticks, audio_languages,"
                    " subtitle_languages, synced_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item_id, v.get("source_id"), v.get("poradi") or 0,
                     v.get("nazev"), v.get("path"), v.get("container"),
                     v.get("video_codec"), v.get("audio_codec"),
                     v.get("audio_channels"), v.get("width"), v.get("height"),
                     v.get("bitrate"), v.get("size_bytes"),
                     v.get("video_range"), v.get("runtime_ticks"),
                     v.get("audio_languages"), v.get("subtitle_languages"),
                     ted))
                zapsano += 1
        conn.commit()
    return zapsano


def _write_batch(
    items: list[tuple[Any, ...]],
    streams: list[tuple[str, list[dict[str, Any]]]],
    tmdb_pairs: list[tuple[tuple[str, int, int], str]],
    keep_existing_tech: bool,
    videno_od: str | None = None,
    chranena: set[str] | None = None,
) -> None:
    """Zapis jedne davky. Bezi ve vlakne, ne na smycce udalosti."""
    # Slucovani musi byt PRED zapisem polozek. Kdyby se novy zaznam vlozil
    # driv, mel by prazdnou historii a stara polozka by uz jen cekala na
    # oznaceni "chybi" - presne to, cemu se chceme vyhnout.
    #
    # Polozky z teto davky jsou zjevne zive - Jellyfin je poslal pred
    # chvili -, takze se do slucovani davaji jako chranene.
    zive = set(chranena or set())
    zive.update(str(radek[0]) for radek in items if radek[0])
    _merge_by_tmdb(tmdb_pairs, chranena=zive, videno_od=videno_od)

    if items:
        _write_items(items, keep_existing_tech=keep_existing_tech)
    # Stopy az po polozkach - odkazuji se na ne cizim klicem, takze
    # polozka musi v databazi uz existovat.
    for item_id, item_streams in streams:
        save_streams(item_id, item_streams)


def _write_items(rows: list[tuple[Any, ...]], keep_existing_tech: bool) -> None:
    """Zapise davku polozek.

    `keep_existing_tech` resi jemny, ale dulezity detail: kdyz je zdrojem
    technickych dat ffprobe, nesmi nam bezna synchronizace knihovny prepsat
    drive namerene hodnoty prazdnymi. COALESCE(?, sloupec) znamena
    "vezmi novou hodnotu, a kdyz je NULL, nech puvodni".
    """
    if keep_existing_tech:
        tech_update = """
                    container       = COALESCE(excluded.container, items.container),
                    video_codec     = COALESCE(excluded.video_codec, items.video_codec),
                    audio_codec     = COALESCE(excluded.audio_codec, items.audio_codec),
                    audio_channels  = COALESCE(excluded.audio_channels, items.audio_channels),
                    width           = COALESCE(excluded.width, items.width),
                    height          = COALESCE(excluded.height, items.height),
                    bitrate         = COALESCE(excluded.bitrate, items.bitrate),
                    size_bytes      = COALESCE(excluded.size_bytes, items.size_bytes),
                    video_range     = COALESCE(excluded.video_range, items.video_range),
                    video_range_reported = excluded.video_range_reported,
                    audio_languages = COALESCE(excluded.audio_languages, items.audio_languages),
                    subtitle_languages = COALESCE(excluded.subtitle_languages, items.subtitle_languages),
                    default_audio_language = COALESCE(excluded.default_audio_language,
                                                      items.default_audio_language),
                    tech_source     = COALESCE(excluded.tech_source, items.tech_source),
                    tech_updated_at = COALESCE(excluded.tech_updated_at, items.tech_updated_at),
        """
    else:
        tech_update = """
                    container       = excluded.container,
                    video_codec     = excluded.video_codec,
                    audio_codec     = excluded.audio_codec,
                    audio_channels  = excluded.audio_channels,
                    width           = excluded.width,
                    height          = excluded.height,
                    bitrate         = excluded.bitrate,
                    size_bytes      = excluded.size_bytes,
                    video_range     = excluded.video_range,
                    video_range_reported = excluded.video_range_reported,
                    audio_languages = excluded.audio_languages,
                    subtitle_languages = excluded.subtitle_languages,
                    default_audio_language = excluded.default_audio_language,
                    tech_source     = excluded.tech_source,
                    tech_updated_at = excluded.tech_updated_at,
                    tech_error      = NULL,
        """

    sql = f"""
        INSERT INTO items (
            id, name, type, library_id, series_id, series_name, season_name,
            index_number, parent_index_number, production_year, runtime_ticks,
            date_created, path, tmdb_id, genres, container, video_codec, audio_codec,
            audio_channels, width, height, bitrate, size_bytes, video_range,
            video_range_reported,
            audio_languages, subtitle_languages, default_audio_language,
            tech_source, tech_updated_at, series_image_tag, image_tag, synced_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            library_id = excluded.library_id,
            series_id = excluded.series_id,
            series_name = excluded.series_name,
            genres = excluded.genres,
            season_name = excluded.season_name,
            index_number = excluded.index_number,
            parent_index_number = excluded.parent_index_number,
            production_year = excluded.production_year,
            runtime_ticks = excluded.runtime_ticks,
            date_created = excluded.date_created,
            path = excluded.path,
            -- COALESCE: kdyz Jellyfin tmdb_id zrovna neposle (nedohledane
            -- metadata), nesmime uz ulozene prepsat na NULL - prisli bychom
            -- o jedinou vec, podle ktere umime polozky slucovat.
            tmdb_id = COALESCE(excluded.tmdb_id, items.tmdb_id),
            {tech_update}
            image_tag = excluded.image_tag,
            series_image_tag = excluded.series_image_tag,
            synced_at = excluded.synced_at,
            is_missing = 0
    """

    # Ktere polozky menily obrazek? Musime se zeptat PRED zapisem - potom
    # uz je v databazi novy otisk a rozdil by nebyl poznat.
    zmenene = _polozky_s_jinym_obrazkem(rows)

    with db.connect() as conn:
        conn.executemany(sql, rows)

    # Az po zapisu: kdyby zapis selhal, nemazali bychom obrazky k nicemu.
    if zmenene:
        _zapomen_obrazky(zmenene)


# V radku polozky (viz _radek_polozky) je otisk predposledni a id prvni.
_SLOUPEC_ID = 0
_SLOUPEC_SERIAL = 4
_SLOUPEC_OTISK_SERIALU = -3
_SLOUPEC_OTISK = -2


def _polozky_s_jinym_obrazkem(rows: list[tuple[Any, ...]]) -> list[str]:
    """Ktere obrazky uz neplati - id polozek i id serialu.

    Otisk (`ImageTags.Primary`) hlasi Jellyfin u kazde polozky. Kdyz se
    zmeni, znamena to, ze plakat je jiny - typicky proto, ze nekdo
    v Jellyfinu opravil spatne urcenou polozku.

    U serialu je to o krok slozitejsi: polozku pro nej nemame, plakat se
    ale stahuje pod jeho id. Jellyfin proto u kazde epizody hlasi jeste
    `SeriesPrimaryImageTag` - otisk plakatu jejiho serialu. Kdyz se
    zmeni, vracime **id serialu**, at se zahodi i jeho obrazek.
    """
    podle_id = {str(radek[_SLOUPEC_ID]): radek[_SLOUPEC_OTISK]
                for radek in rows if radek[_SLOUPEC_ID]}
    serialy = {str(radek[_SLOUPEC_SERIAL]): radek[_SLOUPEC_OTISK_SERIALU]
               for radek in rows if radek[_SLOUPEC_SERIAL]}
    if not podle_id:
        return []

    zmenene: list[str] = []
    ids = list(podle_id)
    with db.connect() as conn:
        for zacatek in range(0, len(ids), 300):
            davka = ids[zacatek:zacatek + 300]
            otazniky = ",".join("?" for _ in davka)
            for radek in conn.execute(
                    f"SELECT id, image_tag FROM items WHERE id IN ({otazniky})",
                    tuple(davka)).fetchall():
                stary_otisk = radek["image_tag"]
                novy_otisk = podle_id.get(str(radek["id"]))
                if stary_otisk and novy_otisk and stary_otisk != novy_otisk:
                    zmenene.append(str(radek["id"]))

        # Serialy: staci se zeptat jedne epizody z kazdeho - otisk plakatu
        # serialu maji vsechny stejny.
        for serial, novy_otisk in serialy.items():
            if not novy_otisk:
                continue
            radek = conn.execute(
                "SELECT series_image_tag FROM items "
                " WHERE series_id = ? AND series_image_tag IS NOT NULL LIMIT 1",
                (serial,)).fetchone()
            stary_otisk = radek["series_image_tag"] if radek else None
            # Zadny ulozeny otisk znamena bud serial, ktery jsme jeste
            # nevideli (a v mezipameti tedy nic nemuze byt), nebo
            # databazi z doby pred timhle sloupcem - a tam lezi plakat,
            # o kterem nevime, jestli jeste plati. Presne kvuli nemu
            # sloupec vznikl, takze ho jednou zahodime; priste uz je
            # co porovnavat.
            if stary_otisk != novy_otisk:
                zmenene.append(serial)
    return zmenene


def _zapomen_obrazky(ids: list[str]) -> int:
    """Smaze obrazky techto polozek z mezipameti.

    Mezipamet je slozka `data/imagecache` a jmeno souboru zacina id
    polozky. Mazeme vsechny druhy i velikosti - kdyz se zmenil plakat,
    nema smysl verit ani nahledu na pozadi.

    Neni to jen uklid: dokud tam soubor lezi, servirujeme stary obrazek
    a uzivatel nema jak se dobrat noveho.
    """
    from .. import config

    slozka = config.load_config().database_path.parent / "imagecache"
    if not slozka.is_dir():
        return 0

    smazano = 0
    for item_id in ids:
        for soubor in slozka.glob(f"{item_id}-*"):
            try:
                soubor.unlink()
                smazano += 1
            except OSError:  # noqa: PERF203 - jeden zamceny soubor nesmi zastavit zbytek
                continue
    if smazano:
        log.info("zapomenuto %s obrazku, ktere uz v Jellyfinu neplati", smazano)
    return smazano


def save_streams(item_id: str, streams: list[dict[str, Any]]) -> None:
    """Prepise stopy jedne polozky.

    Nejdriv smazat, pak vlozit. Kdybychom jen vkladali, zustaly by po
    prekodovani souboru viset stopy, ktere uz v nem nejsou.
    """
    if not streams:
        return

    with db.connect() as conn:
        conn.execute("DELETE FROM item_streams WHERE item_id = ?", (item_id,))
        # Cerstve zmereny soubor prebiji odhad z nazvu: kdyby zustal,
        # pricital by se do souhrnu i potom, co uz je jazyk znamy.
        conn.execute("UPDATE items SET audio_from_name = NULL, "
                     "subtitle_from_name = NULL WHERE id = ?", (item_id,))
        # ON CONFLICT ... DO UPDATE misto SQLite konstrukce INSERT OR REPLACE -
        # rozumi mu SQLite i PostgreSQL. Navic je z nej videt, co presne se
        # pri konfliktu stane, coz u "REPLACE" nikdy nebylo jasne.
        conn.executemany(
            """
            INSERT INTO item_streams (
                item_id, stream_index, type, codec, language, language_source,
                title, channels, channel_layout, width, height, bitrate,
                is_default, is_forced, is_external
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (item_id, stream_index) DO UPDATE SET
                type = excluded.type,
                codec = excluded.codec,
                language = excluded.language,
                language_source = excluded.language_source,
                title = excluded.title,
                channels = excluded.channels,
                channel_layout = excluded.channel_layout,
                width = excluded.width,
                height = excluded.height,
                bitrate = excluded.bitrate,
                is_default = excluded.is_default,
                is_forced = excluded.is_forced,
                is_external = excluded.is_external
            """,
            [
                (
                    item_id, s.get("stream_index"), s.get("type"), s.get("codec"),
                    s.get("language"), s.get("language_source"), s.get("title"),
                    s.get("channels"), s.get("channel_layout"), s.get("width"),
                    s.get("height"), s.get("bitrate"), s.get("is_default", 0),
                    s.get("is_forced", 0), s.get("is_external", 0),
                )
                for s in streams
            ],
        )


def _mark_missing(started_at: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET is_missing = 1 WHERE synced_at < ?", (started_at,)
        )


def slouc_archiv_do_zivych() -> int:
    """Dil z archivu, ktery v knihovne existuje znovu, pripoji k tomu zivemu.

    Co se stalo: soubor dilu se v Jellyfinu nahradil (jina kvalita, novy
    rip, presun mezi slozkami). Jellyfin nezalozil zmenu, ale **novou
    polozku s novym ItemId** - a ta stara pri dalsi synchronizaci spadla
    do archivu. V detailu serialu pak stalo "v archivu je navic 3 dilu",
    prestoze ty dily v knihovne normalne jsou.

    Proc to nezachytilo slucovani pri synchronizaci (`_merge_by_tmdb`):
    to porovnava ulozene `tmdb_id`, a starsi zaznamy (typicky z importu
    historie nebo z doby pred timhle sloupcem) zadne nemaji. Bez nej
    dvojici nema podle ceho najit.

    Tady se identita bere odjinud - **serial plus cislo rady a dilu**.
    Serial se pozna podle id, a kdyz to nesedi, podle jmena: kdyz nekdo
    v Jellyfinu smaze a znovu prida cely adresar serialu, dostanou dily
    nova series_id a podle nich by se stara polozka nenasla nikdy.
    Slucuje se jen archivovana polozka do zive, nikdy naopak: archivovana
    v Jellyfinu prokazatelne neni, takze o nic neprijdeme. Kdyz je zivych
    kopii vic (4K i 1080p vedle sebe), historie pripadne te odsledovanejsi.

    Historie prehravani se prepise na novou polozku - presne to je duvod,
    proc se to dela: aby "kolikrat jsem to videl" neskoncilo na zaznamu,
    ktery uz nikdo neuvidi.

    Vraci pocet slouceni.
    """
    # Dva dotazy a parovani v Pythonu, ne jeden vnoreny dotaz: v SQL by
    # to byl poddotaz s vlastnim GROUP BY uvnitr, a takovy se cte hur,
    # nez kolik usetri.
    #
    # Zive dily si vedeme pod dvema klici zaroven:
    #   podle id serialu   - normalni pripad, vymenil se soubor jednoho dilu,
    #   podle nazvu serialu - kdyz Jellyfin zalozil cely serial znovu
    #                         (smazany a znovu pridany adresar), takze dily
    #                         maji jine series_id a podle nej by se nenasly.
    zive: dict[tuple[str, int, int], tuple[str, float, Any]] = {}
    podle_nazvu: dict[tuple[str, int, int], tuple[str, float, Any]] = {}
    for radek in db.query_all(
        """
        SELECT i.id, i.series_id, i.series_name, i.tmdb_id,
               i.parent_index_number, i.index_number,
               COALESCE(SUM(p.watched_seconds), 0) AS videno
          FROM items i
     LEFT JOIN playback p ON p.item_id = i.id
         WHERE i.is_missing = 0
           AND i.series_id IS NOT NULL
           AND i.parent_index_number IS NOT NULL
           AND i.index_number IS NOT NULL
      GROUP BY i.id, i.series_id, i.series_name, i.tmdb_id,
               i.parent_index_number, i.index_number
        """
    ):
        rada, dil = int(radek["parent_index_number"]), int(radek["index_number"])
        videno = float(radek["videno"] or 0)
        # Kdyz je zivych kopii vic (4K i 1080p vedle sebe), bere se ta
        # odsledovanejsi - o tu prijit nechceme.
        for kam, klic in (
            (zive, (str(radek["series_id"]), rada, dil)),
            (podle_nazvu, (str(radek["series_name"] or "").strip().lower(), rada, dil)),
        ):
            if not klic[0]:
                continue
            if klic not in kam or videno > kam[klic][1]:
                kam[klic] = (str(radek["id"]), videno, radek["tmdb_id"])

    # Serialy, ktere pod svym id porad zijou. Jejich archivovane dily
    # se podle jmena parovat NESMI: kdyz serial v Jellyfinu je a jeden
    # jeho dil chybi, pak ten dil opravdu chybi - a shoda jmena by
    # historii poslala k nekomu jinemu (dva serialy se muzou jmenovat
    # stejne).
    zive_serialy = {
        str(radek["series_id"]) for radek in db.query_all(
            "SELECT DISTINCT series_id FROM items "
            " WHERE is_missing = 0 AND series_id IS NOT NULL")
    }

    pary: list[tuple[str, str]] = []
    for radek in db.query_all(
        """
        SELECT id, series_id, series_name, tmdb_id,
               parent_index_number, index_number
          FROM items
         WHERE is_missing = 1
           AND parent_index_number IS NOT NULL
           AND index_number IS NOT NULL
        """
    ):
        rada, dil = int(radek["parent_index_number"]), int(radek["index_number"])
        serial = str(radek["series_id"] or "")
        # Nejdriv podle id serialu; teprve kdyz serial pod tim id uz vubec
        # neexistuje, zkusime jmeno. Poradi je dulezite: id je jistota,
        # jmeno je jenom shoda.
        nalezeny = zive.get((serial, rada, dil))
        if not nalezeny and serial not in zive_serialy:
            kandidat = podle_nazvu.get(
                (str(radek["series_name"] or "").strip().lower(), rada, dil))
            # Kdyz obe strany znaji tmdb_id a lisi se, je to prokazatelne
            # jiny serial - tim se odlisi "Kancelar (US)" od "Kancelar (UK)".
            # Kdyz ho jedna z nich nema, rozhoduje jmeno a cislo dilu;
            # nic lepsiho o tech zaznamech nevime.
            stare_tmdb, nove_tmdb = radek["tmdb_id"], (kandidat or (None,) * 3)[2]
            if kandidat and not (stare_tmdb and nove_tmdb
                                 and str(stare_tmdb) != str(nove_tmdb)):
                nalezeny = kandidat
        if nalezeny and nalezeny[0] != str(radek["id"]):
            pary.append((str(radek["id"]), nalezeny[0]))

    if not pary:
        return 0

    with db.connect() as conn:
        for stare, nove in pary:
            # Poradi je dane cizim klicem: stopy odkazuji na polozku,
            # takze musi pryc driv, nez se polozka smaze.
            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (stare,))
            conn.execute("DELETE FROM item_versions WHERE item_id = ?", (stare,))
            conn.execute("UPDATE playback SET item_id = ? WHERE item_id = ?",
                         (nove, stare))
            conn.execute("DELETE FROM items WHERE id = ?", (stare,))
            log.info("z archivu slouceno: %s -> %s", stare, nove)

    log.info("archiv: slouceno %s dilu, ktere v knihovne existuji znovu",
             len(pary))
    return len(pary)


def uklid_fantomu() -> int:
    """Vyhodi z knihovny polozky druhu, ktery synchronizace nezna.

    Naprava po chybe: `importers.zaloz_z_jellyfinu()` zakladala polozku
    z ceho koliv, co Jellyfin vratil - vcetne celeho SERIALU, kdyz zaznam
    historie visel na jeho id. Synchronizace se pta jen na filmy a dily,
    takze takovou polozku nikdy neuvidela a `_mark_missing()` ji pri
    kazdem behu poslala do archivu. V knihovne pak stal "archivovany"
    serial, ktery v Jellyfinu je - a vedle nej ten spravny, poskladany
    z dilu.

    Smazat je spravne reseni, ne jen prepnout priznak: takova polozka
    nikdy nemela vzniknout. Historie se tim neztraci - zaznamy zustavaji
    a vrati se mezi osirele, coz je pravda o nich rikala uz predtim.

    Vola se pri startu. Je to levny dotaz na indexovany sloupec a bezna
    databaze nema co uklizet, takze se nic nezdrzi.
    """
    # Maze se vsechno, co synchronizace zapsat nemuze - tedy i druh, na
    # ktery se tu nemyslelo (hudebni klip, upoutavka z davneho importu).
    # Vyjimka je `Video`: cerstve pridany soubor je chvili prave ten a ma
    # tu zustat, dokud ho Jellyfin nezaradi. Od 1.6.8 se uklid vola i po
    # kazde plne synchronizaci, tak nesmi mazat, co prave ulozila.
    otazniky = ",".join("?" for _ in ZAPISOVANE_TYPY)
    with db.connect() as conn:
        fantomy = [str(r["id"]) for r in conn.execute(
            f"SELECT id FROM items WHERE type NOT IN ({otazniky})",
            tuple(ZAPISOVANE_TYPY)).fetchall()]
        if not fantomy:
            return 0
        for zacatek in range(0, len(fantomy), 200):
            davka = fantomy[zacatek:zacatek + 200]
            znaky = ",".join("?" for _ in davka)
            conn.execute(
                f"DELETE FROM item_streams WHERE item_id IN ({znaky})", tuple(davka))
            conn.execute(
                f"DELETE FROM item_versions WHERE item_id IN ({znaky})",
                tuple(davka))
            conn.execute(f"DELETE FROM items WHERE id IN ({znaky})", tuple(davka))

    log.info("uklizeno %s polozek, ktere do knihovny nepatri (kolekce, serialy, rady)",
             len(fantomy))
    return len(fantomy)
