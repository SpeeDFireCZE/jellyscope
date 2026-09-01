"""Vymyslena data pro ukazkovy rezim.

Slouzi k jedinemu ucelu: aby sis mohl aplikaci proklikat driv, nez ji
napojis na skutecny Jellyfin - a aby bylo videt, jak grafy vypadaji,
kdyz uz je nejaka historie nasbirana.

S ostrym provozem tenhle soubor nema nic spolecneho. Zapisuje do vlastni
databaze (data/demo.db), takze tvoje skutecna data nemuze nijak ovlivnit.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import accounts, db, languages

# Ucet, kterym se do ukazky prihlasis.
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demodemo"

# Jak casto ktery clen domacnosti sahne po originale, kdyz ma na vyber
# i cesky dabing. Petr kouka radeji v originale, Eva skoro vzdy cesky -
# diky tomu ma stranka Jazyky co ukazat.
PREFERS_ORIGINAL = {
    "demo-u1": 0.62, "demo-u2": 0.18, "demo-u3": 0.35, "demo-u4": 0.08,
    "demo-u5": 0.90, "demo-u6": 0.05, "demo-u7": 0.48, "demo-u8": 0.72,
    "demo-u9": 0.25, "demo-u10": 0.55, "demo-u11": 0.12,
}

LIBRARIES = [
    ("demo-movies", "Filmy", "movies"),
    ("demo-tv", "Serialy", "tvshows"),
]

# Jedenact lidi schvalne. Karta "Kdo v jakem jazyce sleduje" ukazuje
# rovnou prvnich deset (viz nastaveni Rozhrani) a zbytek schova do okna -
# pri ctyrech divacich by tohle nebylo videt nikdy.
USERS = [
    ("demo-u1", "Petr", 1),
    ("demo-u2", "Jana", 0),
    ("demo-u3", "Tomas", 0),
    ("demo-u4", "Eva", 0),
    ("demo-u5", "Marek", 0),
    ("demo-u6", "Lucie", 0),
    ("demo-u7", "David", 0),
    ("demo-u8", "Klara", 0),
    ("demo-u9", "Ondrej", 0),
    ("demo-u10", "Tereza", 0),
    ("demo-u11", "Filip", 0),
]

MOVIES = [
    "Duna", "Blade Runner 2049", "Prichozi", "Interstellar", "Sedmi samuraji",
    "Vetrelec", "Mad Max: Zbesila cesta", "Zivot je krasny", "Pulp Fiction",
    "Sedm", "Pocatek", "Temny rytir", "Prestiz", "Whiplash", "Parazit",
    "Hori, ma panenko", "Marecku, podejte mi pero", "Vrchni, prchni",
    "Kolja", "Samotari", "Pelisky", "Musime si promluvit", "Vratne lahve",
]

SERIES = ["Chernobyl", "Serial killer", "Kancelar", "Pratele", "Simpsonovi"]

CLIENTS = [
    ("Jellyfin Web", "Chrome na notebooku"),
    ("Jellyfin Android TV", "Shield TV v obyvaku"),
    ("Findroid", "Pixel 8"),
    ("Infuse", "Apple TV"),
    ("Jellyfin Web", "Firefox v praci"),
]

# Odkud se ukazkova domacnost diva. Bez tohohle by byla mapa na strance
# Sit prazdna: kdyz vsechno tece po domaci siti, neni co umistit.
#
# Adresy jsou skutecne verejne rozsahy - schvalne, protoze jen takove
# umi databaze GeoLite2 najit. Vymyslene cislo (nebo 203.0.113.x
# z dokumentacniho rozsahu) by v mape skoncilo jako "neznamo odkud".
# Nikam se pritom nepripojujeme, jen se cte mistni soubor .mmdb.
DOMACI_SIT = "192.168.1.20"

VENKU = [
    ("109.81.0.1", "mobil v Praze"),
    ("81.19.0.1", "u rodicu v Brne"),
    ("5.9.0.1", "sluzebka v Nemecku"),
    ("80.58.61.250", "dovolena ve Spanelsku"),
    ("213.129.65.1", "Londyn"),
    ("24.48.0.1", "navsteva v Kanade"),
    ("200.160.2.3", "Brazilie"),
    ("118.127.0.1", "Australie"),
]

# Jak casto se z ktereho zarizeni kouka mimo domaci sit. Televize v obyvaku
# se z podstaty veci nikam nestehuje, telefon ano - a prave tenhle rozdil
# dela mapu uveritelnou.
VENKU_SANCE = {
    "Shield TV v obyvaku": 0.0,
    "Apple TV": 0.0,
    "Chrome na notebooku": 0.25,
    "Firefox v praci": 1.0,
    "Pixel 8": 0.55,
}


def _adresa(device: str) -> str:
    """Z jake adresy se tohle prehravani povede.

    Bliz domovu je vic prehravani nez daleko - proto vazene losovani:
    prvni polozky VENKU (Praha, Brno) padnou casteji nez Australie.
    """
    if random.random() >= VENKU_SANCE.get(device, 0.2):
        return DOMACI_SIT
    vahy = [len(VENKU) - index for index in range(len(VENKU))]
    return random.choices(VENKU, weights=vahy, k=1)[0][0]


def _ts(moment: datetime) -> str:
    return moment.strftime(db.TIME_FORMAT)


def already_seeded() -> bool:
    return int(db.query_value("SELECT COUNT(*) FROM items")) > 0


def ensure_demo_account() -> None:
    """Zalozi ucet demo/demodemo, aby se do ukazky dalo prihlasit.

    A postavi ukazku do anglictiny. Chodi na ni lidi odkudkoliv a cesky
    popisek u grafu jim nerekne nic; prepnout si ji zpatky nejde, protoze
    v ukazce se neuklada nic - a co by jeden prepnul, meli by tak
    i vsichni po nem.
    """
    if accounts.get_by_name(DEMO_USERNAME) is None:
        accounts.create(DEMO_USERNAME, DEMO_PASSWORD, is_admin=True)

    db.set_setting("ui_language", "en")
    db.set_setting("log_language", "en")


def _audio_tracks(random_source) -> tuple[str, str, str]:
    """Vymysli jazykove stopy jednoho titulu.

    Vraci trojici (zvukove stopy, titulky, vychozi zvuk) uz ve tvaru,
    v jakem se uklada do databaze.
    """
    roll = random_source.random()
    if roll < 0.50:
        audio = ["cs", "en"]          # dabing i original - nejcastejsi
    elif roll < 0.72:
        audio = ["cs"]                # jen dabing
    elif roll < 0.90:
        audio = ["en"]                # jen original
    elif roll < 0.97:
        audio = ["cs", "en", "sk"]
    else:
        audio = ["und"]               # jazyk nikdo nevyplnil

    subtitles = []
    if random_source.random() < 0.7:
        subtitles.append("cs")
    if random_source.random() < 0.5:
        subtitles.append("en")

    return (
        languages.pack(audio),
        languages.pack(subtitles),
        audio[0] if audio else languages.UNKNOWN,
    )


def seed() -> dict[str, int]:
    """Naplni databazi vymyslenym provozem za posledni rok."""
    random.seed(42)          # stejne cislo = stejna data pri kazdem spusteni
    now = datetime.now(timezone.utc)

    items: list[tuple] = []
    plays: list[tuple] = []

    with db.connect() as conn:
        for library_id, name, collection_type in LIBRARIES:
            conn.execute(
                "INSERT INTO libraries (id, name, collection_type, paths, synced_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
                (library_id, name, collection_type, '["D:/media"]', _ts(now)),
            )

        for user_id, name, is_admin in USERS:
            conn.execute(
                "INSERT INTO users"
                " (id, name, is_administrator, is_disabled, last_activity, synced_at)"
                " VALUES (?,?,?,0,?,?) ON CONFLICT (id) DO NOTHING",
                (user_id, name, is_admin, _ts(now), _ts(now)),
            )

        # ---- filmy -------------------------------------------------
        for index, title in enumerate(MOVIES):
            # Par filmu schvalne velkych a ve 4K, at ma stranka Zjisteni
            # co ukazat jako "zabira hodne, sleduje se malo".
            is_huge = index % 7 == 0
            height = 2160 if is_huge else random.choice([1080, 1080, 1080, 720, 576])
            size = int(random.uniform(45, 68) * 1024 ** 3) if is_huge \
                else int(random.uniform(1.5, 14) * 1024 ** 3)
            codec = "hevc" if is_huge else random.choice(["h264", "h264", "hevc", "mpeg4"])
            created = now - timedelta(days=random.randint(90, 1200))

            items.append((
                f"demo-movie-{index}", title, "Movie", "demo-movies",
                None, None, None, None, None,
                random.randint(1965, 2024),
                int(random.uniform(85, 165) * 60 * 10_000_000),
                created.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
                f"D:/media/filmy/{title}.mkv",
                "matroska", codec,
                random.choice(["aac", "ac3", "eac3", "dts"]),
                random.choice([2, 6, 6, 8]),
                int(height * 16 / 9), height,
                int(size * 8 / (random.uniform(90, 160) * 60)),
                size,
                # Cast velkych filmu je Dolby Vision - v knihovnach je to
                # bezne a bez nej by treti sloupec "SDR / HDR / Dolby
                # Vision" v ukazce nikdy nic neukazal.
                (random.choice(["HDR", "HDR", "DOVI"]) if is_huge else "SDR"),
                *_audio_tracks(random),
                random.choice(["ffprobe", "ffprobe", "jellyfin"]),
                _ts(now), _ts(now),
            ))

        # Titul, u ktereho jazyk nezna soubor ani knihovna - zna ho jen
        # nazev souboru. Presne tenhle pripad je v knihovnach bezny
        # a je to jediny zpusob, jak v ukazce videt "odhad z nazvu".
        items.append((
            "demo-movie-nazev", "Sedmikrasky", "Movie", "demo-movies",
            None, None, None, None, None, 1966,
            74 * 60 * 10_000_000, "2022-06-11T09:20:00.0000000Z",
            "D:/media/filmy/Sedmikrasky.1966.CZ.SK.EN.1080p.mkv",
            "matroska", "h264", "ac3", 2,
            1920, 1080, 8_200_000, int(4.1 * 1024 ** 3), "SDR",
            # V souhrnu zatim nic - dopocita se z nazvu az nakonec.
            languages.UNKNOWN, "", languages.UNKNOWN,
            "ffprobe", _ts(now), _ts(now),
        ))

        # jeden film zamerne dvakrat - kvuli detekci duplicit
        for suffix, height, size, codec in (("1080p", 1080, 9, "h264"), ("2160p", 2160, 54, "hevc")):
            items.append((
                f"demo-movie-dup-{suffix}", "Interstellar", "Movie", "demo-movies",
                None, None, None, None, None, 2014,
                169 * 60 * 10_000_000, "2021-03-02T12:00:00.0000000Z",
                f"D:/media/filmy/Interstellar ({suffix}).mkv",
                "matroska", codec, "eac3", 6,
                int(height * 16 / 9), height,
                int(size * 1024 ** 3 * 8 / (169 * 60)), size * 1024 ** 3,
                "DOVI" if height > 2000 else "SDR",
                "cs,en", "cs,en", "cs",
                "ffprobe", _ts(now), _ts(now),
            ))

        # ---- epizody -----------------------------------------------
        #
        # Prvni serial ze seznamu prijde CELY NAJEDNOU a jako posledni
        # pribytek vubec. Je to nejcastejsi zpusob, jak serialy do
        # knihovny chodi (stahne se cela rada), a na Prehledu diky tomu
        # je videt, jak se dily slucuji pod jednu kartu misto toho, aby
        # kazdy zabral vlastni dlazdici.
        for series_index, series_name in enumerate(SERIES):
            cela_rada = series_index == 0
            prisla_rada = now - timedelta(days=1, hours=3)
            for episode in range(1, random.randint(8, 16)):
                height = random.choice([1080, 1080, 720])
                size = int(random.uniform(0.9, 3.5) * 1024 ** 3)
                if cela_rada:
                    # Dily jedne davky nedorazi v tutez vterinu, ale
                    # behem chvile - presne tak, jak je stahovani prida.
                    created = prisla_rada + timedelta(minutes=episode * 4)
                else:
                    created = now - timedelta(days=random.randint(30, 900))
                items.append((
                    f"demo-ep-{series_index}-{episode}",
                    f"{episode}. dil", "Episode", "demo-tv",
                    f"demo-series-{series_index}", series_name, "Serie 1",
                    episode, 1,
                    random.randint(2005, 2023),
                    int(random.uniform(22, 55) * 60 * 10_000_000),
                    created.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
                    f"D:/media/serialy/{series_name}/S01E{episode:02d}.mkv",
                    "matroska", random.choice(["h264", "h264", "hevc"]),
                    random.choice(["aac", "ac3"]), 2,
                    int(height * 16 / 9), height,
                    int(size * 8 / (35 * 60)), size,
                    "SDR",
                    *_audio_tracks(random),
                    random.choice(["ffprobe", "jellyfin"]),
                    _ts(now), _ts(now),
                ))

        # ---- jednotlive stopy ---------------------------------------
        # Bez nich by detail polozky ukazoval prazdno. Skladame je tak,
        # aby odpovidaly jazykum, ktere ma titul v souhrnu.
        stream_rows: list[tuple] = []
        for entry in items:
            item_id = entry[0]
            index = 0

            # Titul s jazykem jen v nazvu ma stopy skladane rucne o kus
            # niz - obecna smycka je odvozuje ze souhrnu, a ten u nej
            # zadny jazyk nema.
            if item_id == "demo-movie-nazev":
                continue

            stream_rows.append((
                item_id, index, "Video", entry[14], languages.UNKNOWN, None,
                None, None, entry[17], entry[18], entry[19], 1, 0, 0,
            ))
            index += 1

            for code in languages.unpack(entry[22]):
                channels = random.choice([2, 6, 6, 8])
                stream_rows.append((
                    item_id, index, "Audio",
                    random.choice(["aac", "ac3", "eac3", "dts"]),
                    code,
                    f"{code.upper()} {channels}.0" if code != languages.UNKNOWN else None,
                    channels,
                    {2: "stereo", 6: "5.1", 8: "7.1"}.get(channels),
                    None, None, None,
                    1 if index == 1 else 0, 0, 0,
                ))
                index += 1

            for code in languages.unpack(entry[23]):
                stream_rows.append((
                    item_id, index, "Subtitle",
                    random.choice(["subrip", "ass", "pgssub"]),
                    code, None, None, None, None, None, None,
                    0, 1 if random.random() < 0.2 else 0,
                    1 if random.random() < 0.3 else 0,
                ))
                index += 1

        conn.executemany(
            """INSERT INTO items (id, name, type, library_id, series_id,
                series_name, season_name, index_number, parent_index_number,
                production_year, runtime_ticks, date_created, path, container,
                video_codec, audio_codec, audio_channels, width, height, bitrate,
                size_bytes, video_range, audio_languages, subtitle_languages,
                default_audio_language, tech_source, tech_updated_at, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (id) DO NOTHING""",
            items,
        )

        # Stopy az tady: odkazuji se na polozky cizim klicem, takze polozky
        # uz musi v databazi byt. Opacne poradi by skoncilo chybou.
        # Tri zvukove stopy bez jazyka - ze souhrnu by se poskladat
        # nedaly, protoze ten jazyky nesmi mit dvakrat. Proto rucne.
        stream_rows.append((
            "demo-movie-nazev", 0, "Video", "h264", languages.UNKNOWN, None,
            None, None, 1920, 1080, 8_200_000, 1, 0, 0,
        ))
        for poradi, kanaly in enumerate((2, 6, 6), start=1):
            stream_rows.append((
                "demo-movie-nazev", poradi, "Audio", "ac3", languages.UNKNOWN, None,
                kanaly, {2: "stereo", 6: "5.1"}[kanaly], None, None, None,
                1 if poradi == 1 else 0, 0, 0,
            ))

        conn.executemany(
            """INSERT INTO item_streams (
                item_id, stream_index, type, codec, language, title,
                channels, channel_layout, width, height, bitrate,
                is_default, is_forced, is_external
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (item_id, stream_index) DO NOTHING""",
            stream_rows,
        )

        # ---- tmdb_id a archiv ---------------------------------------
        # Doplnujeme az tady zvlastnim UPDATE, ne v tuple vyse. Ty jsou
        # pozicni (entry[14] je kodek, entry[22] jazyky, ...) a vlozeni
        # sloupce doprostred by je vsechny posunulo. Tohle je nudnejsi,
        # ale nerozbije se to.
        conn.executemany(
            "UPDATE items SET tmdb_id = ? WHERE id = ?",
            [(f"demo-tmdb-{index}", entry[0]) for index, entry in enumerate(items)],
        )

        # Dva tituly zamerne v archivu - aby bylo v knihovne videt, jak
        # vypada zaznam, ktery uz v Jellyfinu neni, ale historie na nej
        # odkazuje.
        conn.executemany(
            "UPDATE items SET is_missing = 1 WHERE id = ?",
            [(entry[0],) for entry in items[:2]],
        )

        # ---- historie prehravani -----------------------------------
        # Sledujeme jen cast knihovny - aby "misto, ktere nikdo nevyuziva"
        # ukazalo realistickou hodnotu, ne nulu.
        watchable = [item for item in items if random.random() < 0.55]

        # Dva a pul tisice prehravani, ne devet set.
        #
        # Duvod neni "vic je vic": graf souběžneho toku na Siti byl pri
        # devíti stech tak ridky, ze pres tyden ukazoval par osamelych
        # spicek a mezi nimi rovnou nulu. Skutecny server, kde se diva
        # rodina, vypada jinak - vecer bezi tri streamy naraz a graf ma
        # tvar, ne cárky. Ukazka ma ukazovat, jak to vypada v provozu.
        for session in range(2500):
            item = random.choice(watchable)
            user = random.choice(USERS)
            client, device = random.choice(CLIENTS)

            # Vecerni spicka: vetsina prehravani mezi 18. a 23. hodinou.
            # Polovina pripada na posledni mesic - tam se clovek diva
            # nejcasteji a graf tam ma byt husty.
            day_offset = (random.uniform(0, 30) if random.random() < 0.5
                          else random.uniform(0, 360))
            hour = random.choice([19, 20, 20, 21, 21, 22, 18, 23, 14, 10])
            started = (now - timedelta(days=day_offset)).replace(
                hour=hour, minute=random.randint(0, 59)
            )
            if started > now:
                # Vecerni hodina dnesniho dne je casto jeste v budoucnosti.
                # Drive se takovy zaznam posunul na "pred dvema hodinami" -
                # jenze to potkalo stovky zaznamu naraz a v grafu z toho
                # byla jedna nesmyslna vez o par stech Mbit/s v okamziku,
                # kdy se ukazka nasela. Posuneme ho tedy o den zpet.
                started -= timedelta(days=1, minutes=random.randint(0, 240))

            runtime_seconds = item[10] / 10_000_000
            watched = int(runtime_seconds * random.uniform(0.08, 1.0))

            # Velke HEVC soubory se casteji prepocitavaji - presne ten jev,
            # ktery ma stranka Zjisteni odhalit.
            if item[14] == "hevc" and random.random() < 0.55:
                method = "Transcode"
            else:
                method = random.choice(
                    ["DirectPlay", "DirectPlay", "DirectPlay", "DirectStream", "Transcode"]
                )

            reasons = None
            # Co presne se prepocitava. Odvozujeme to z duvodu, at bublina
            # u znacky "transcode" rika totez, co duvod pod ni - ve
            # skutecnem provozu to hlasi Jellyfin v TranscodingInfo.
            video_direct = audio_direct = None
            hw = None
            if method == "Transcode":
                reasons = random.choice([
                    "VideoCodecNotSupported",
                    "AudioCodecNotSupported",
                    "VideoCodecNotSupported,AudioCodecNotSupported",
                    "ContainerBitrateExceedsLimit",
                    "SubtitleCodecNotSupported",
                ])
                video_direct = 0 if ("Video" in reasons or "Bitrate" in reasons
                                     or "Subtitle" in reasons) else 1
                audio_direct = 0 if "Audio" in reasons else 1
                if not video_direct and random.random() < 0.5:
                    hw = random.choice(["qsv", "nvenc", "vaapi"])

            # Kodek, ktery skutecne tekl k prehravaci. Pri prepoctu je to
            # cil prepoctu (prohlizece umeji H264 a AAC skoro vsechny),
            # jinak kodek puvodniho souboru. Bez tohohle rozdilu by
            # bublina hlasila "HEVC -> HEVC" a nedavala smysl.
            video_codec = item[14] if video_direct != 0 else "h264"
            audio_codec = item[15] if audio_direct != 0 else "aac"

            # Jazyk vybirame jen z toho, co titul opravdu ma - stejne jako
            # skutecny divak. Kdyz je na vyber cestina i original, kazdy
            # clen domacnosti se rozhoduje jinak: Petr casteji original,
            # Eva casteji dabing. Diky tomu ma stranka Jazyky co ukazat.
            available = languages.unpack(item[22])
            real_options = [code for code in available if code != languages.UNKNOWN]

            if not real_options:
                audio_language = None
            elif len(real_options) == 1:
                audio_language = real_options[0]
            else:
                if random.random() < PREFERS_ORIGINAL.get(user[0], 0.4):
                    audio_language = next(
                        (code for code in real_options if code != "cs"), real_options[0]
                    )
                else:
                    audio_language = "cs" if "cs" in real_options else real_options[0]

            # Titulky se zapinaji hlavne u cizojazycneho zvuku.
            subtitle_options = languages.unpack(item[23])
            subtitle_language = None
            if subtitle_options:
                chance = 0.55 if audio_language not in (None, "cs") else 0.12
                if random.random() < chance:
                    subtitle_language = (
                        "cs" if "cs" in subtitle_options else subtitle_options[0]
                    )

            plays.append((
                f"demo-sess-{session}::{item[0]}",
                user[0], user[1],
                item[0], item[1], item[2], item[5], item[3],
                client, device, f"demo-dev-{user[0]}", _adresa(device),
                method, reasons, video_direct, audio_direct, hw,
                video_codec, audio_codec, item[19],
                audio_language, subtitle_language,
                _ts(started), _ts(started + timedelta(seconds=watched)),
                _ts(started + timedelta(seconds=watched)),
                watched, random.randint(0, 400),
                int(watched * 10_000_000), 0,
            ))

        conn.executemany(
            """INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,
                item_type, series_name, library_id, client, device_name, device_id,
                remote_address, play_method, transcode_reasons,
                transcode_video_direct, transcode_audio_direct, transcode_hw,
                video_codec, audio_codec,
                bitrate, audio_language, subtitle_language,
                started_at, last_seen_at, ended_at, watched_seconds,
                paused_seconds, position_ticks, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            plays,
        )

        # jedno "prave ted bezici", aby byla videt i karta na Prehledu.
        #
        # Vyplnena je schvalne cela: tok, rozliseni i stopy, se kterymi
        # se divak dvia. Karta prave se hraje je jedina, kterou clovek
        # vidi driv nez cokoliv jineho - a v ukazce ma ukazat, co vsechno
        # o prehravani vime, ne prazdna mista.
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,
                item_type, series_name, library_id, client, device_name, play_method,
                transcode_reasons, transcode_video_direct, transcode_audio_direct,
                transcode_hw, video_codec, audio_codec, bitrate,
                video_width, video_height,
                audio_language, subtitle_language,
                current_audio_language, current_subtitle_language,
                started_at, last_seen_at,
                watched_seconds, paused_seconds, position_ticks, is_active)
               VALUES ('demo-live','demo-u2','Jana','demo-movie-0','Duna','Movie',NULL,
                       'demo-movies','Jellyfin Web','Chrome na notebooku','Transcode',
                       'VideoCodecNotSupported',0,1,'qsv','h264','eac3',8600000,
                       1280,720,
                       'cs','cs','cs','en',
                       ?,?,2640,0,26400000000,1)""",
            (_ts(now - timedelta(minutes=44)), _ts(now)),
        )

    # Az uplne nakonec, mimo blok se spojenim: detekce si otevira
    # vlastni. Delame to tak, jak by to v ostrem provozu udelala uloha
    # "Analyza souboru" - nedosazujeme vysledek rucne, at ukazka ukazuje
    # opravdovou funkci, ne jeji napodobeninu.
    from . import scanner

    scanner.doplnit_jazyky_z_nazvu(["demo-movie-nazev"])

    return {"items": len(items), "plays": len(plays) + 1, "users": len(USERS)}
