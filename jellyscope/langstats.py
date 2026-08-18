"""Jazykove statistiky.

Odpovida na otazky, ktere si u vicejazycne knihovny clovek klade sam:

    "V jakem jazyce se u nas doma vlastne diva?"
    "Kolik procent je cesky a kolik anglicky?"
    "Kdo z rodiny sleduje v originale a kdo dabovane?"
    "Kolik titulu vubec nema ceskou stopu?"
    "Jak casto se pouzivaji titulky?"

Rozlisujeme dve ruzne veci, ktere se snadno pletou:

  * **co se sleduje**  - jazyk stopy, kterou si divak pustil (tabulka playback)
  * **co je k dispozici** - jazyky, ktere soubor obsahuje (tabulka items)

Prvni rika, jak se u vas mluvi. Druhe, co vam v knihovne chybi.
"""

from __future__ import annotations

from typing import Any

from . import charts, db, languages
from .i18n import translate as _t

# Kratsi prehravani se do jazykovych statistik nepocita.
#
# Hranice je svazana s tim, jak se jazyk urcuje: collector prvni ctyri
# minuty ignoruje (loga, znelky) a pak jeste minutu ceka, jestli u stopy
# divak zustane. Drive nez po peti minutach tedy jazyk potvrdit nejde.
#
# Kdyby tu zustalo dve minuty, prehravani dlouhe tri minuty by do statistik
# vstoupilo BEZ jazyka - a pribylo by "Neuvedeno" tam, kde jsme se odpoved
# jen jeste nestihli dozvedet. Viz collector.LANGUAGE_GRACE_SECONDS.
MIN_PLAY_SECONDS = 300

# ---------------------------------------------------------------------------
# Prevzata historie se do jazykovych statistik nepocita
# ---------------------------------------------------------------------------
#
# Ani Jellystat, ani plugin Playback Reporting jazyk prehravane stopy
# nezaznamenavaji - proste ten udaj nemaji. Prevzate zaznamy by tedy do
# statistik pridaly jen hromadu "Neuvedeno", ktera by prehlusila skutecna
# data ze sberace.
#
# A nebyl by to jen kosmeticky problem: "60 % cesky" spocitane vcetne
# zaznamu, u kterych jazyk nikdo nezna, je proste **spatne cislo**. Radeji
# mensi vzorek, o kterem neco vime, nez velky, ktery lze.
#
# Odfiltrujeme je podle `session_key`: prevzate radky maji predponu
# "import:", vlastni sber ma klic z relace Jellyfinu. Viz importers.py.
#
# Tyka se to jen statistik postavenych na tabulce `playback` ("co se
# sleduje"). Prehled toho, co je v knihovne k dispozici, cte tabulku
# `items` a s importem nema nic spolecneho.
BEZ_IMPORTU = "AND session_key NOT LIKE 'import:%'"
# Tyz filtr pro dotazy, ktere spojuji vic tabulek a potrebuji prefix.
BEZ_IMPORTU_P = "AND p.session_key NOT LIKE 'import:%'"

# ...ale kdyz prevzaty zaznam jazyk PRESTO ma, zahazovat ho by byla skoda.
#
# Duvod pro filtr vys je "nezname jazyk", ne "je to import" - to druhe byla
# jen zkratka za to prvni. Nektere verze pluginu Playback Reporting jazyk
# stopy ukladaji a Jellyscope si ho pri importu prevezme; takovy radek je
# stejne dobry jako z vlastniho sberu a do statistik patri.
#
# Pouziva se jen u dotazu na ZVUKOVOU stopu. U titulku ne: ani zaznam
# s jazykem zvuku nerika, jestli byly zapnute titulky, takze by se tvaril
# jako "bez titulku" a cislo by posunul dolu.
SE_ZNAMYM_JAZYKEM = (
    "AND (session_key NOT LIKE 'import:%'"
    "     OR (audio_language IS NOT NULL AND audio_language != ''))"
)
SE_ZNAMYM_JAZYKEM_P = (
    "AND (p.session_key NOT LIKE 'import:%'"
    "     OR (p.audio_language IS NOT NULL AND p.audio_language != ''))"
)


def imported_plays(days: int | None = None) -> int:
    """Kolik prehravani z importu do cisel opravdu nevstoupilo.

    Nepocitaji se vsechny prevzate zaznamy, ale jen ty **bez jazyka**.
    Import, ktery jazyk stopy nese (nektere verze Playback Reportingu ho
    ukladaji), je stejne dobry jako vlastni sber a do statistik patri -
    viz `SE_ZNAMYM_JAZYKEM` vys. Kdyby se sem pocital taky, hlaska nad
    grafy by tvrdila, ze chybi neco, co v cislech ve skutecnosti je.

    Vraci se, aby se na strance dalo poctive rici, co do cisel nevstoupilo.
    Skryta data, o kterych ctenar nevi, jsou horsi nez zadna.
    """
    bez_jazyka = ("AND session_key LIKE 'import:%'"
                  "  AND (audio_language IS NULL OR audio_language = '')")
    if days is None:
        return int(db.query_value(
            f"SELECT COUNT(*) FROM playback"
            f" WHERE watched_seconds >= ? {bez_jazyka}",
            (MIN_PLAY_SECONDS,),
        ))
    return int(db.query_value(
        f"SELECT COUNT(*) FROM playback"
        f" WHERE started_at >= datetime('now', ?) AND watched_seconds >= ?"
        f"   {bez_jazyka}",
        (_range(days), MIN_PLAY_SECONDS),
    ))


def short_plays(days: int) -> int:
    """Vlastni prehravani, ktera jsou na statistiku prilis kratka.

    Vraci se kvuli hlasce u prazdne stranky. Bez toho tam stalo "nech to
    par dni sbirat", i kdyz Jellyscope uz nekolik prehravani zaznamenal -
    jen zadne neprekrocilo hranici. To je matouci: clovek ceka dny na
    neco, co se nestane, misto aby se podival na delsi film.
    """
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM playback
         WHERE started_at >= datetime('now', ?)
           AND watched_seconds > 0
           AND watched_seconds < ?
           {BEZ_IMPORTU}
        """,
        (_range(days), MIN_PLAY_SECONDS),
    ))


def _range(days: int) -> str:
    return f"-{max(1, int(days))} days"


# ---------------------------------------------------------------------------
# Prirazeni barev - jednou pro celou stranku
# ---------------------------------------------------------------------------

def colour_map() -> dict[str, int]:
    """Prirazeni barevneho slotu kazdemu jazyku.

    Poradi urcuje **celkovy** odsledovany cas za celou dobu - zamerne bez
    ohledu na zvolene obdobi. Kdyby se barvy pocitaly z vybraneho obdobi,
    zmena filtru ze "30 dnu" na "rok" by prebarvila vsechny grafy: cestina
    by z modre skocila na oranzovou. Ctenar, ktery si zapamatoval "modra
    je cestina", by byl uveden v omyl.

    Tomuhle se rika prebarveni pri filtrovani a je to klasicka chyba
    v dashboardech. Barva patri **veci**, ne jejimu poradi v aktualnim
    vyberu.

    Devaty a dalsi jazyk uz vlastni barvu nedostane. Osm je strop, za kterym
    se barvy zacnou plest i cloveku bez poruchy barvocitu.
    """
    rows = db.query_all(
        f"""
        SELECT COALESCE(audio_language, 'und') AS code,
               SUM(watched_seconds) AS total
        FROM playback
        WHERE watched_seconds >= ?
          {SE_ZNAMYM_JAZYKEM}
        GROUP BY code
        ORDER BY total DESC
        """,
        (MIN_PLAY_SECONDS,),
    )
    colours = {row["code"]: index + 1
               for index, row in enumerate(rows[:charts.SERIES_SLOTS])}

    # Jazyky, ktere jsou v knihovne, ale nikdo je jeste neposlouchal,
    # by jinak zustaly bez barvy. Doplnime je za ty sledovane.
    if len(colours) < charts.SERIES_SLOTS:
        extra = db.query_all(
            "SELECT audio_languages FROM items"
            " WHERE is_missing = 0 AND audio_languages IS NOT NULL AND audio_languages != ''"
        )
        counter: dict[str, int] = {}
        for row in extra:
            for code in languages.unpack(row["audio_languages"]):
                counter[code] = counter.get(code, 0) + 1

        for code, _count in sorted(counter.items(), key=lambda pair: -pair[1]):
            if len(colours) >= charts.SERIES_SLOTS:
                break
            colours.setdefault(code, len(colours) + 1)

    return colours


def _decorate(rows: list[dict[str, Any]], colours: dict[str, int],
              total: float) -> list[dict[str, Any]]:
    """Doplni radkum lidsky nazev, procenta a barevny slot."""
    for row in rows:
        code = row.get("code") or languages.UNKNOWN
        row["code"] = code
        row["label"] = languages.display(code)
        row["percent"] = (float(row.get("hours") or 0) / total * 100) if total else 0.0
        row["value"] = row.get("hours") or 0
        row["slot"] = colours.get(code)
    return rows


# ---------------------------------------------------------------------------
# Co se sleduje
# ---------------------------------------------------------------------------

def watched_languages(days: int, colours: dict[str, int]) -> dict[str, Any]:
    """Podil jazyku na odsledovanem case."""
    rows = db.query_all(
        f"""
        SELECT COALESCE(audio_language, 'und')   AS code,
               SUM(watched_seconds) / 3600.0     AS hours,
               COUNT(*)                          AS plays,
               COUNT(DISTINCT user_id)           AS users,
               COUNT(DISTINCT item_id)           AS item_count
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds >= ?
          {SE_ZNAMYM_JAZYKEM}
        GROUP BY code
        ORDER BY hours DESC
        """,
        (_range(days), MIN_PLAY_SECONDS),
    )
    total = sum(float(row["hours"] or 0) for row in rows)
    _decorate(rows, colours, total)

    zvoleny = preferred_language()
    hlavni = next((row for row in rows if row["code"] == zvoleny), None)
    # Druhe cislo pro srovnani. Kdyz uz je preferovanym jazykem anglictina,
    # nema smysl ji ukazovat dvakrat - vezmeme dalsi nejsilnejsi jazyk.
    druhy = next((row for row in rows
                  if row["code"] != zvoleny and row["code"] != languages.UNKNOWN), None)

    return {
        "rows": rows,
        "total_hours": total,
        # Dve cisla, ktera uzivatele zajimaji nejvic.
        "preferred_code": zvoleny,
        "preferred_name": languages.display(zvoleny),
        "preferred_percent": hlavni["percent"] if hlavni else 0.0,
        "second_name": languages.display(druhy["code"]) if druhy else "",
        "second_percent": druhy["percent"] if druhy else 0.0,
        "known_share": sum(
            row["percent"] for row in rows if row["code"] != languages.UNKNOWN
        ),
    }


def languages_by_user(days: int, colours: dict[str, int]) -> list[dict[str, Any]]:
    """Rozpad jazyku pro kazdeho uzivatele zvlast.

    SQL vrati "dlouhou" tabulku (uzivatel, jazyk, hodiny). Pro zobrazeni
    ji potrebujeme "sirokou" - jeden radek na uzivatele. Tomuhle prevodu
    se rika **pivot** a v Pythonu je citelnejsi nez v SQL.
    """
    rows = db.query_all(
        f"""
        SELECT user_id,
               user_name,
               COALESCE(audio_language, 'und') AS code,
               SUM(watched_seconds) / 3600.0   AS hours
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds >= ?
          AND user_name IS NOT NULL
          {SE_ZNAMYM_JAZYKEM}
        GROUP BY user_id, user_name, code
        """,
        (_range(days), MIN_PLAY_SECONDS),
    )

    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        person = people.setdefault(row["user_id"], {
            "user_id": row["user_id"],
            "name": row["user_name"],
            "total": 0.0,
            "segments": [],
        })
        hours = float(row["hours"] or 0)
        person["total"] += hours
        person["segments"].append({
            "code": row["code"],
            "label": languages.display(row["code"]),
            "value": hours,
            "hours": hours,
            "slot": colours.get(row["code"]),
        })

    result = list(people.values())
    for person in result:
        person["segments"].sort(key=lambda seg: -seg["value"])
        for segment in person["segments"]:
            segment["percent"] = (segment["value"] / person["total"] * 100) if person["total"] else 0
        # Nejcastejsi jazyk uzivatele - hodi se jako shrnuti do tabulky.
        person["main"] = person["segments"][0] if person["segments"] else None

    result.sort(key=lambda person: -person["total"])
    return result


def subtitle_usage(days: int) -> dict[str, Any]:
    """Jak casto se pouzivaji titulky a v jakem jazyce."""
    summary = db.query_one(
        f"""
        SELECT SUM(CASE WHEN subtitle_language IS NOT NULL
                        THEN watched_seconds ELSE 0 END) / 3600.0 AS with_subtitles,
               SUM(CASE WHEN subtitle_language IS NULL
                        THEN watched_seconds ELSE 0 END) / 3600.0 AS without_subtitles
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds >= ?
          {BEZ_IMPORTU}
        """,
        (_range(days), MIN_PLAY_SECONDS),
    ) or {}

    with_subs = float(summary.get("with_subtitles") or 0)
    without_subs = float(summary.get("without_subtitles") or 0)
    total = with_subs + without_subs

    rows = db.query_all(
        f"""
        SELECT subtitle_language              AS code,
               SUM(watched_seconds) / 3600.0  AS hours,
               COUNT(*)                       AS plays
        FROM playback
        WHERE started_at >= datetime('now', ?)
          AND watched_seconds >= ?
          AND subtitle_language IS NOT NULL
          {BEZ_IMPORTU}
        GROUP BY code
        ORDER BY hours DESC
        """,
        (_range(days), MIN_PLAY_SECONDS),
    )
    for row in rows:
        row["label"] = languages.display(row["code"])

    return {
        "with_hours": with_subs,
        "without_hours": without_subs,
        "percent": (with_subs / total * 100) if total else 0.0,
        "rows": rows,
    }


def dubbed_vs_original(days: int, code: str | None = None) -> list[dict[str, Any]]:
    """Sledoval divak v preferovanem jazyce, i kdyz mel na vyber jiny?

    Spoji jazyk, ktery si pustil, s jazyky, ktere byly v souboru
    k dispozici - a rozdeli prehravani do tri prehlednych skupin.
    """
    code = code or preferred_language()
    nazev = languages.display(code)
    rows = db.query_all(
        f"""
        SELECT p.audio_language        AS chosen,
               i.audio_languages       AS available,
               SUM(p.watched_seconds) / 3600.0 AS hours
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= datetime('now', ?)
          AND p.watched_seconds >= ?
          AND p.audio_language IS NOT NULL
          AND i.audio_languages IS NOT NULL AND i.audio_languages != ''
          {SE_ZNAMYM_JAZYKEM_P}
        GROUP BY chosen, available
        """,
        (_range(days), MIN_PLAY_SECONDS),
    )

    buckets = {
        "with_choice": 0.0,   # pustil zvoleny jazyk, i kdyz mel i jiny
        "only": 0.0,          # zvoleny jazyk, jina moznost nebyla
        "other": 0.0,         # pustil jiny jazyk
    }

    for row in rows:
        available = languages.unpack(row["available"])
        hours = float(row["hours"] or 0)
        real_options = [kod for kod in available if kod != languages.UNKNOWN]

        if row["chosen"] == code:
            if len(real_options) > 1:
                buckets["with_choice"] += hours
            else:
                buckets["only"] += hours
        else:
            buckets["other"] += hours

    total = sum(buckets.values())
    # Popisky se skladaji az tady, protoze v nich je nazev zvoleneho jazyka.
    # Prekladaji se pres i18n uz slozene - viz _t() volani v i18n.py.
    #
    # Skladaji se z prelozitelnych kousku, ne jako cela veta: cela veta
    # obsahuje nazev jazyka, takze by se ve slovniku prekladu nikdy
    # nenasla - a popisek zustal cesky i v anglickem rozhrani.
    labels = [
        ("with_choice", f"{nazev} – {_t('i když byl na výběr jiný jazyk')}", 1),
        ("only", f"{nazev} – {_t('jiná možnost nebyla')}", 3),
        ("other", f"{_t('Jiný jazyk než')} {nazev.lower()}", 2),
    ]
    return [
        {
            "label": label,
            "value": buckets[key],
            "hours": buckets[key],
            "percent": (buckets[key] / total * 100) if total else 0.0,
            "slot": slot,
        }
        for key, label, slot in labels
        if buckets[key] > 0
    ]


# ---------------------------------------------------------------------------
# Co je v knihovne k dispozici
# ---------------------------------------------------------------------------

def library_languages(colours: dict[str, int],
                      library_id: str | None = None) -> list[dict[str, Any]]:
    """Kolik titulu obsahuje kterou zvukovou stopu.

    Jeden titul se muze zapocitat vickrat - film s ceskou i anglickou
    stopou patri do obou skupin. Proto se procenta nesecitaji na sto
    a v UI je u toho poznamka.
    """
    where = ("WHERE is_missing = 0 AND audio_languages IS NOT NULL"
             " AND audio_languages != ''")
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    rows = db.query_all(f"SELECT audio_languages FROM items {where}", tuple(params))

    counter: dict[str, int] = {}
    for row in rows:
        for code in languages.unpack(row["audio_languages"]):
            counter[code] = counter.get(code, 0) + 1

    total = len(rows)
    result = [
        {
            "code": code,
            "label": languages.display(code),
            "item_count": count,
            "percent": (count / total * 100) if total else 0.0,
            "slot": colours.get(code),
        }
        for code, count in counter.items()
    ]
    result.sort(key=lambda entry: -entry["item_count"])
    return result


def language_combinations(limit: int = 4) -> list[dict[str, Any]]:
    """Nejcastejsi kombinace stop - "CS + EN", "jen EN", ...

    Presne to, na co se u domaci knihovny clovek pta: mam u filmu obe
    stopy, nebo jen jednu?

    Vypiseme `limit` nejcastejsich kombinaci a zbytek shrneme do jedineho
    radku "Ostatni". Dlouhy seznam kombinaci, z nichz kazda ma dva tituly,
    nikdo necte - a prehled o tom hlavnim v nem zanikne.

    Tituly, u kterych jazyk nikdo nevyplnil, do prehledu nepatri: nerikaji
    nic o tom, jak je knihovna sestavena, jen o tom, ze chybi metadata.
    Jejich pocet vracime zvlast, aby bylo poctive videt, kolik jich je.
    """
    rows = db.query_all(
        """
        SELECT audio_languages              AS packed,
               COUNT(*)                     AS item_count,
               COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM items
        WHERE is_missing = 0
          AND audio_languages IS NOT NULL
          AND audio_languages != ''
          AND audio_languages != 'und'      -- tituly bez urceneho jazyka sem nepatri
        GROUP BY audio_languages
        ORDER BY item_count DESC
        """
    )

    top = rows[:limit]
    rest = rows[limit:]

    for row in top:
        row["label"] = languages.combination_label(row["packed"])
        row["value"] = row["item_count"]

    if rest:
        top.append({
            "packed": None,
            "label": _t("Ostatní"),
            "item_count": sum(row["item_count"] for row in rest),
            "size_bytes": sum(row["size_bytes"] or 0 for row in rest),
            "value": sum(row["item_count"] for row in rest),
            "combinations": len(rest),
        })

    # Procenta pocitame ze VSECH kombinaci, ne jen z tech vypsanych - jinak
    # by "56 %" znamenalo "z toho, co zrovna vidis", coz je jine cislo.
    celkem = sum(int(row["item_count"] or 0) for row in top)
    for row in top:
        row["percent"] = (int(row["item_count"] or 0) / celkem * 100) if celkem else 0.0

    return top


def undefined_language_files(limit: int, offset: int,
                            search: str | None = None) -> list[dict[str, Any]]:
    """Soubory, u kterych jazyk zvukove stopy nikdo nevyplnil.

    Zamerne po JEDNOTLIVYCH souborech, ne po titulech: jazyk se opravuje
    u konkretniho souboru, takze clovek potrebuje cestu k nemu. U serialu
    to znamena kazdy dil zvlast - presne ty, ktere jsou spatne.

    Cesta je tu to hlavni; bez ni by seznam rekl "neco je spatne" a nechal
    hledani na uzivateli.
    """
    where = ["is_missing = 0",
             "(audio_languages IS NULL OR audio_languages = '' OR audio_languages = 'und')"]
    params: list[Any] = []
    if search:
        where.append("(name LIKE ? OR series_name LIKE ? OR path LIKE ?)")
        params.extend([f"%{search}%"] * 3)

    params.extend([limit, offset])
    return db.query_all(
        f"""
        SELECT i.id, i.name, i.series_name, i.season_name, i.index_number,
               i.parent_index_number, i.path, i.size_bytes, i.width, i.height,
               i.video_codec, i.tech_source, i.audio_languages,
               l.name AS library_name
          FROM items i
     LEFT JOIN libraries l ON l.id = i.library_id
         WHERE {' AND '.join(where)}
      ORDER BY l.name, COALESCE(i.series_name, i.name),
               i.parent_index_number, i.index_number, i.name
         LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def undefined_language_count(search: str | None = None) -> int:
    where = ["is_missing = 0",
             "(audio_languages IS NULL OR audio_languages = '' OR audio_languages = 'und')"]
    params: list[Any] = []
    if search:
        where.append("(name LIKE ? OR series_name LIKE ? OR path LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    return int(db.query_value(
        f"SELECT COUNT(*) FROM items WHERE {' AND '.join(where)}", tuple(params)))


def undefined_language_items() -> int:
    """Kolik titulu nema u zadne stopy uvedeny jazyk.

    Vraci se zvlast, aby slo v UI rict "a k tomu 12 titulu bez metadat"
    misto aby se tvarily jako dalsi jazykova kombinace.
    """
    return int(db.query_value(
        """
        SELECT COUNT(*) FROM items
        WHERE is_missing = 0
          AND (audio_languages IS NULL OR audio_languages = '' OR audio_languages = 'und')
        """
    ))


PREFERRED_SETTING = "preferred_language"
PREFERRED_DEFAULT = "cs"


def preferred_language() -> str:
    """Jazyk, ktery si tenhle server preje mit u titulu.

    Cela stranka Jazyky driv predpokladala cestinu - byla natvrdo v SQL,
    v nazvech funkci i v nadpisech. Pro cesky server to sedelo, pro
    kohokoli jineho to byla stranka o nicem: Portugalec se nepotrebuje
    dozvidat, kolik procent sleduje cesky.

    Ulozeny kod se overuje proti seznamu jazyku, ktere v knihovne opravdu
    jsou. Kdyby zustal nastaveny jazyk, ktery mezitim z knihovny zmizel,
    stranka by ukazovala same nuly a nebylo by poznat proc.
    """
    ulozeny = languages.normalize(db.get_setting(PREFERRED_SETTING, "") or "")
    v_knihovne = [row["code"] for row in library_language_options()]

    if ulozeny and ulozeny != languages.UNKNOWN and ulozeny in v_knihovne:
        return ulozeny
    if PREFERRED_DEFAULT in v_knihovne:
        return PREFERRED_DEFAULT
    # Prazdna knihovna nebo jazyk, ktery v ni neni: drz se ulozene volby,
    # at se vyber po synchronizaci sam neprepne na neco jineho.
    return ulozeny or PREFERRED_DEFAULT


def library_language_options() -> list[dict[str, Any]]:
    """Jazyky, ktere se v knihovne opravdu vyskytuji - podklad pro vyber.

    Nabizet vsech osm set jazyku sveta by byl nepouzitelny seznam. Ten
    jeden, ktery clovek hleda, ma pritom jistotu v knihovne - kvuli nemu
    si ji prece staví.

    Radi se podle poctu titulu: nejcastejsi jazyk je nahore, kde ho clovek
    ceka.
    """
    rows = db.query_all(
        """
        SELECT audio_languages FROM items
        WHERE is_missing = 0
          AND audio_languages IS NOT NULL AND audio_languages != ''
        """
    )

    pocty: dict[str, int] = {}
    for row in rows:
        # Rozpad delame v Pythonu, ne v SQL. Rozdelit retezec na seznam umi
        # kazda databaze jinak a tohle je jednou za nacteni stranky.
        for code in languages.unpack(row["audio_languages"]):
            if code and code != languages.UNKNOWN:
                pocty[code] = pocty.get(code, 0) + 1

    # Klic se schvalne nejmenuje "items". V sablone by `option.items`
    # nesahlo na hodnotu, ale na metodu `dict.items` - Jinja hleda nejdriv
    # atribut a teprve pak polozku. Cislo by se pak vypsalo jako pomlcka.
    return [
        {"code": code, "name": languages.display(code), "title_count": pocet}
        for code, pocet in sorted(pocty.items(), key=lambda dvojice: (-dvojice[1], dvojice[0]))
    ]


def _serialy_bez_stopy(vzor: str) -> set[str]:
    """ID serialu, kterym stopa chybi u VSECH dilu v knihovne.

    Tohle je jadro opravy jedne konkretni chyby. Puvodne se filtr na jazyk
    uplatnil na jednotlive dily uz v databazi a teprve pak se vysledek
    seskupil podle serialu. Jenze tim z kazde skupiny vypadly prave ty
    dily, ktere stopu MAJI - a zbytek se pak tvaril jako "cely serial
    stopu nema", i kdyz ji nemel treba jediny dil z dvaceti.

    Rozhodovat se proto musi z celeho serialu najednou: kolik dilu ma
    a kolika z nich stopa chybi. Ptame se na knihovnu, ne jen na
    sledovane dily - jinak by serial, z nehoz clovek videl jediny
    (spatne otitulkovany) dil, vysel jako "cely bez stopy".
    """
    rows = db.query_all(
        """
        SELECT series_id,
               COUNT(*) AS celkem,
               SUM(CASE WHEN (',' || audio_languages || ',') NOT LIKE ?
                        THEN 1 ELSE 0 END) AS chybi
        FROM items
        WHERE is_missing = 0
          AND series_id IS NOT NULL
          AND audio_languages IS NOT NULL AND audio_languages != ''
        GROUP BY series_id
        """,
        (vzor,),
    )
    return {str(r["series_id"]) for r in rows
            if int(r["celkem"] or 0) > 0 and int(r["chybi"] or 0) == int(r["celkem"])}


def _oznaceni_dilu(row: dict[str, Any]) -> str:
    """"S01E03" - aby slo poznat, o ktery dil jde."""
    if row.get("season") is None or row.get("episode") is None:
        return ""
    return f"S{int(row['season']):02d}E{int(row['episode']):02d}"


def _slouc_do_radku(soubory: list[dict[str, Any]], cele_serialy: set[str],
                    code: str, limit: int) -> list[dict[str, Any]]:
    """Ze souboru bez stopy poskladá řádky tabulky.

    Serial, kteremu stopa chybi cely, je jeden radek. Kdyz chybi jen
    nekterym dilum, jsou to radky po dilech i s oznacenim - jinak by
    clovek hledal, ktery dil ma vlastne shanet.
    """
    skupiny: dict[str, dict[str, Any]] = {}

    for soubor in soubory:
        serial = str(soubor["series_id"]) if soubor["series_id"] else None
        cely_serial = serial is not None and serial in cele_serialy

        if cely_serial:
            klic = f"serial:{serial}"
            popisek = soubor["series_name"] or soubor["name"]
            odkaz = f"/series/{serial}"
        else:
            # Film, nebo jednotlivy dil serialu, ktery stopu jinak ma.
            klic = f"soubor:{soubor['item_id']}"
            oznaceni = _oznaceni_dilu(soubor)
            if soubor["series_name"]:
                popisek = f"{soubor['series_name']} – {oznaceni or soubor['name']}"
            else:
                popisek = soubor["name"]
            odkaz = f"/item/{soubor['item_id']}"

        skupina = skupiny.setdefault(klic, {
            "label": popisek,
            "series_id": serial if cely_serial else None,
            "item_id": soubor["item_id"],
            "detail_url": odkaz,
            "is_series": cely_serial,
            # Nazev dilu se vypisuje pod popiskem, ale jen u serialu.
            # U filmu je totozny s popiskem a stal by tam dvakrat.
            "episode_name": (soubor["name"]
                             if not cely_serial and soubor["series_name"] else None),
            "hours": 0.0,
            "plays": 0,
            "audio": [],
            "subtitles": [],
        })
        skupina["hours"] += float(soubor["hours"] or 0)
        skupina["plays"] += int(soubor["plays"] or 0)
        skupina["audio"].extend(languages.unpack(soubor["audio_languages"]))
        skupina["subtitles"].extend(languages.unpack(soubor["subtitle_languages"]))

    radky = sorted(skupiny.values(), key=lambda r: r["hours"], reverse=True)[:limit]
    for radek in radky:
        # pack() srovna posbirane kody do jednoho tvaru: setridene,
        # bez duplicit, "neuvedeno" az na konec.
        radek["audio_languages"] = languages.pack(radek.pop("audio"))
        radek["audio_label"] = languages.combination_label(radek["audio_languages"])
        radek["has_preferred_subtitles"] = code in radek.pop("subtitles")
    return radky


def missing_preferred(days: int, code: str, limit: int = 15) -> dict[str, Any]:
    """Sledovane tituly, ktere nemaji zvukovou stopu v preferovanem jazyce.

    Uzitecny seznam: tohle jsou tituly, u kterych by se vyplatilo
    poohlednout se po jine verzi - protoze se na ne opravdu divate.
    """
    # Hledany kod se do LIKE vklada jako parametr, ne do textu dotazu -
    # jde o hodnotu z nastaveni a ta do SQL nikdy nepatri primo.
    #
    # Obalujeme carkami z obou stran: ulozeny tvar je "cs,en,de", takze
    # hledat holy '%cs%' by naslo i kod, ktery cestinu jen obsahuje jako
    # cast. S carkami je porovnani presne.
    vzor = f"%,{code},%"

    # Jeden radek na SOUBOR, ne na serial. Seskupovat az v Pythonu je tu
    # nutnost, ne pohodlnost - viz `_serialy_bez_stopy()` nize.
    soubory = db.query_all(
        """
        SELECT i.id                            AS item_id,
               i.name                          AS name,
               i.series_id                     AS series_id,
               i.series_name                   AS series_name,
               i.parent_index_number           AS season,
               i.index_number                  AS episode,
               i.audio_languages               AS audio_languages,
               i.subtitle_languages            AS subtitle_languages,
               SUM(p.watched_seconds) / 3600.0 AS hours,
               COUNT(*)                        AS plays
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= datetime('now', ?)
          AND p.watched_seconds >= ?
          AND i.is_missing = 0
          AND i.audio_languages IS NOT NULL AND i.audio_languages != ''
          AND (',' || i.audio_languages || ',') NOT LIKE ?
        GROUP BY i.id, i.name, i.series_id, i.series_name,
                 i.parent_index_number, i.index_number,
                 i.audio_languages, i.subtitle_languages
        """,
        (_range(days), MIN_PLAY_SECONDS, vzor),
    )

    cele_serialy = _serialy_bez_stopy(vzor)
    rows = _slouc_do_radku(soubory, cele_serialy, code, limit)

    total = db.query_value(
        """
        SELECT COUNT(*) FROM items
        WHERE is_missing = 0
          AND audio_languages IS NOT NULL AND audio_languages != ''
          AND (',' || audio_languages || ',') NOT LIKE ?
        """,
        (vzor,),
    )
    return {"rows": rows, "total": total, "code": code,
            "name": languages.display(code)}


def coverage() -> dict[str, Any]:
    """Kolik dat o jazycich vlastne mame.

    Bez tohohle cisla by se cela stranka dala precist spatne: kdyz ma
    jazyk vyplneny jen ctvrtina souboru, "60 % cesky" znamena neco
    uplne jineho, nez kdyz ho ma vyplneny vsechno.
    """
    items = db.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN audio_languages IS NOT NULL AND audio_languages != ''
                        THEN 1 ELSE 0 END) AS with_languages
        FROM items WHERE is_missing = 0
        """
    ) or {}

    plays = db.query_one(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN audio_language IS NOT NULL THEN 1 ELSE 0 END) AS with_language
        FROM playback WHERE watched_seconds >= ?
          {BEZ_IMPORTU}
        """,
        (MIN_PLAY_SECONDS,),
    ) or {}

    item_total = items.get("total") or 0
    play_total = plays.get("total") or 0
    return {
        "items_total": item_total,
        "items_with_languages": items.get("with_languages") or 0,
        "items_percent": ((items.get("with_languages") or 0) / item_total * 100) if item_total else 0.0,
        "plays_total": play_total,
        "plays_with_language": plays.get("with_language") or 0,
        "plays_percent": ((plays.get("with_language") or 0) / play_total * 100) if play_total else 0.0,
    }
