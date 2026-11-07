"""Souhrn období: přehled, srovnání dvou období, sledovanost po dnech, způsoby přehrání, přehrávače, heatmapa.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db, formatting
from ..i18n import translate as _t
from .divaci import top_users
from .druhy import KIND_BOTH, KIND_OTHER, SERIALOVE_TYPY, _kind_condition, nazev_typu
from .obdobi import Obdobi, _meze, _mistni_den, _obdobi, je_transcode, predchozi
from .rust import rust_knihovny
from .sit import bandwidth_prehled
from .tituly import top_items


# ---------------------------------------------------------------------------
# Prehled (dashboard)
# ---------------------------------------------------------------------------

def overview(days: int) -> dict[str, Any]:
    """Hlavni cisla za zvolene obdobi."""
    row = db.query_one(
        f"""
        SELECT
            COUNT(*)                                        AS plays,
            COALESCE(SUM(watched_seconds), 0)               AS watched_seconds,
            COUNT(DISTINCT user_id)                         AS users,
            COUNT(DISTINCT item_id)                         AS item_count,
            COALESCE(SUM(CASE WHEN {je_transcode()}
                              THEN watched_seconds ELSE 0 END), 0) AS transcoded_seconds
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        """,
        (*_meze(days),),
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
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        """,
        _meze(predchozi(days)),
    ) or {}



# ---------------------------------------------------------------------------
# Dve obdobi vedle sebe
# ---------------------------------------------------------------------------

def delka_sekund(zadani: Any) -> float:
    """Jak dlouhe obdobi doopravdy je - v sekundach.

    Meri se z mezi, ne z poctu dnu. U celych dnu vyjde oboji nastejno,
    u useku vybraneho tazenim v grafu uz ne: dvouhodinovy vyber ma
    `dny == 1` a denni prumer by z nej vysel dvanactkrat mensi, nez je.

    "Poslednich N dni" ma konec otevreny (KONEC_CASU), takze z mezi se
    delka zmerit neda - u toho plati pocet dnu.
    """
    obdobi = _obdobi(zadani)
    if obdobi.relativni:
        return obdobi.dny * 86400.0
    try:
        od = datetime.strptime(obdobi.od, db.TIME_FORMAT)
        do = datetime.strptime(obdobi.do, db.TIME_FORMAT)
    except ValueError:
        return max(1, obdobi.dny) * 86400.0
    return max(1.0, (do - od).total_seconds())


def _prekryvaji_se(prvni: Obdobi, druhe: Obdobi) -> bool:
    """Maji obdobi spolecny kus casu?

    Dve prekryvajici se obdobi nejsou "srpen versus prosinec": cast dat je
    v obou sloupcich a rozdil mezi nimi nemeri nic. Rict to nahlas je
    poctivejsi nez to spocitat a mlcet.
    """
    return prvni.od < druhe.do and druhe.od < prvni.do


def _radek(popisek: str, tvar: str, hodnota_a: Any, hodnota_b: Any) -> dict[str, Any]:
    """Jeden radek tabulky srovnani i s rozdilem.

    `relativni` (o kolik procent) se u procentualnich udaju nepocita:
    "z 12 % na 18 %" je +6 PROCENTNICH BODU, ne +50 %. Druhe cislo je
    formalne spravne a v praxi matouci, tak se nedodava vubec.
    """
    a = float(hodnota_a or 0)
    b = float(hodnota_b or 0)
    return {"popisek": popisek, "tvar": tvar,
            "a": hodnota_a or 0, "b": hodnota_b or 0,
            "rozdil": a - b,
            "relativni": None if tvar == "procenta" or not b else (a - b) / b * 100}


# Role zpusobu doruceni -> popisek v tabulce. Scita se podle ROLE, ne
# podle presneho nazvu: importovana historie nese hodnoty jako
# "Transcode (v:h264 a:direct)" a pri shode na presny retezec by
# propadly, takze by prepocet z importu v srovnani chybel.
DORUCENI_ROLE: tuple[tuple[str, str], ...] = (
    ("good", "Přímé přehrávání"),
    ("info", "Přebalení (direct stream)"),
    ("serious", "Transcode"),
)


def _jazykove_radky(prvni: Any, druhe: Any) -> list[dict[str, Any]]:
    """Kolik se v kterem jazyce sledovalo - v obou obdobich.

    `langstats` az tady, ne nahore: stats je nejnizsi patro a nema se na
    co vysoke vazat. Cyklus by to dnes neudelalo, ale poradi importu se
    tim uz nikdy nebude muset resit.

    Radky jsou SJEDNOCENI obou obdobi. Jazyk, ktery je jen v jednom, musi
    byt videt - "v prosinci se nove zacalo koukat slovensky" je prave ta
    odpoved, kvuli ktere se clovek na srovnani diva. Pri pruniku by
    z tabulky tise vypadl.

    Vedle hodin jde i podil. U ruzne dlouhych obdobi je podil to jedine
    poctive cislo: v roce se nasleduje vic hodin cehokoliv nez v srpnu,
    ale pomer cestiny a anglictiny se tim nemeni.
    """
    from .. import langstats

    barvy = langstats.colour_map()
    a = {r["code"]: r for r in langstats.watched_languages(prvni, barvy)["rows"]}
    b = {r["code"]: r for r in langstats.watched_languages(druhe, barvy)["rows"]}

    kody = sorted(set(a) | set(b),
                  key=lambda k: -max(float((a.get(k) or {}).get("hours") or 0),
                                     float((b.get(k) or {}).get("hours") or 0)))

    radky = []
    for kod in kody:
        prvni_radek, druhy_radek = a.get(kod) or {}, b.get(kod) or {}
        radek = _radek(str(prvni_radek.get("label") or druhy_radek.get("label") or kod),
                       "hodiny",
                       prvni_radek.get("hours") or 0, druhy_radek.get("hours") or 0)
        radek["podil_a"] = float(prvni_radek.get("percent") or 0)
        radek["podil_b"] = float(druhy_radek.get("percent") or 0)
        radky.append(radek)
    return radky


def srovnani(prvni_zadani: Any, druhe_zadani: Any) -> dict[str, Any]:
    """Tytez statistiky za dve libovolna obdobi, vedle sebe.

    "Oproti minulemu obdobi" umi aplikace davno, jenze minule obdobi si
    clovek nevybira - je to okno tesne pred tim zvolenym. Na otazku
    "byl srpen lepsi nez prosinec?" to neodpovi.

    Dve veci, ktere se tady daji snadno zamlcet, a nesmi:

    * **Ruzne dlouha obdobi.** Souctem vyhraje delsi obdobi skoro vzdycky.
      Proto je hned pod souctem denni prumer a stranka rekne, ze se delky
      lisi.
    * **Prekryv.** Kdyz maji obdobi spolecny kus, je cast dat v obou
      sloupcich a rozdil nemeri nic.
    """
    prvni, druhe = _obdobi(prvni_zadani), _obdobi(druhe_zadani)
    sekund_a, sekund_b = delka_sekund(prvni), delka_sekund(druhe)

    prehled_a, prehled_b = overview(prvni), overview(druhe)
    sit_a, sit_b = bandwidth_prehled(prvni), bandwidth_prehled(druhe)

    def hodin(prehled: dict[str, Any]) -> float:
        return float(prehled.get("watched_seconds") or 0) / 3600.0

    def denne(prehled: dict[str, Any], sekund: float) -> float:
        return hodin(prehled) / max(1.0, sekund / 86400.0)

    def podle_role(zadani: Any) -> dict[str, float]:
        soucty: dict[str, float] = {}
        for radek in play_method_breakdown(zadani):
            role = str(radek.get("role") or "")
            soucty[role] = soucty.get(role, 0.0) + float(radek.get("hours") or 0)
        return soucty

    role_a, role_b = podle_role(prvni), podle_role(druhe)

    skupiny = [
        {
            "nadpis": _t("Sledování"),
            "radky": [
                _radek(_t("Odsledováno"), "hodiny", hodin(prehled_a), hodin(prehled_b)),
                _radek(_t("Denní průměr"), "hodiny",
                       denne(prehled_a, sekund_a), denne(prehled_b, sekund_b)),
                _radek(_t("Spuštění"), "cislo",
                       prehled_a.get("plays"), prehled_b.get("plays")),
                _radek(_t("Diváci"), "cislo",
                       prehled_a.get("users"), prehled_b.get("users")),
                _radek(_t("Různých titulů"), "cislo",
                       prehled_a.get("item_count"), prehled_b.get("item_count")),
            ],
        },
        {
            "nadpis": _t("Jak server obsah doručoval"),
            "radky": [
                _radek(_t(popisek), "hodiny",
                       role_a.get(role, 0.0), role_b.get(role, 0.0))
                for role, popisek in DORUCENI_ROLE
            ] + [
                _radek(_t("Podíl transcode"), "procenta",
                       prehled_a.get("transcode_share"),
                       prehled_b.get("transcode_share")),
            ],
        },
        {
            "nadpis": _t("Síť"),
            "radky": [
                _radek(_t("Přeneseno"), "bajty",
                       sit_a.get("bajtu"), sit_b.get("bajtu")),
                _radek(_t("Špička"), "mbit",
                       sit_a.get("spicka_mbit"), sit_b.get("spicka_mbit")),
            ],
        },
    ]

    # Jazyky maji promenlivy pocet radku, takze se skupina prida jen kdyz
    # je co ukazat - prazdna tabulka s nadpisem tvrdi, ze se nedivalo,
    # coz je jiná informace nez "jazyk stopy neznáme".
    jazyky = _jazykove_radky(prvni, druhe)
    if jazyky:
        skupiny.append({"nadpis": _t("V jakém jazyce se sledovalo"),
                        "radky": jazyky, "s_podilem": True})

    # Knihovna stoji na dennich snimcich, a ty zacinaji az u verze, ktera
    # je umi zapisovat. Za starsi obdobi proto nejsou - a misto tabulky
    # nul se rekne proc. "Nepribylo nic" a "nevime" jsou dve ruzne veci.
    rust_a, rust_b = rust_knihovny(prvni), rust_knihovny(druhe)
    knihovna_zname = bool(rust_a.get("dost_dat") and rust_b.get("dost_dat"))
    knihovna_dopoctena = bool(rust_a.get("dopocteno") or rust_b.get("dopocteno"))
    if knihovna_zname:
        skupiny.append({
            "nadpis": _t("Knihovna"),
            "radky": [
                _radek(_t("Přibylo položek"), "cislo",
                       rust_a.get("polozek"), rust_b.get("polozek")),
                _radek(_t("Přibylo dat"), "bajty",
                       rust_a.get("prirustek"), rust_b.get("prirustek")),
                _radek(_t("Velikost na konci období"), "bajty",
                       rust_a.get("velikost"), rust_b.get("velikost")),
            ],
        })

    return {
        "a": {"obdobi": prvni, "dnu": sekund_a / 86400.0,
              "top": top_items(prvni, limit=5, kind=KIND_BOTH),
              "uzivatele": top_users(prvni, limit=5)},
        "b": {"obdobi": druhe, "dnu": sekund_b / 86400.0,
              "top": top_items(druhe, limit=5, kind=KIND_BOTH),
              "uzivatele": top_users(druhe, limit=5)},
        "skupiny": skupiny,
        # Rozdil delek do jednoho procenta je zaokrouhleni, ne rozdil.
        "stejne_dlouha": abs(sekund_a - sekund_b) <= max(sekund_a, sekund_b) * 0.01,
        "prekryv": _prekryvaji_se(prvni, druhe),
        # Ma vubec co srovnavat? Dve prazdna obdobi vedle sebe jsou tabulka
        # nul a je lepsi rict proc.
        "ma_data": bool((prehled_a.get("plays") or 0) or (prehled_b.get("plays") or 0)),
        # Kdyz o knihovne za nektere obdobi nic nevime, stranka to rekne
        # misto toho, aby skupina tise chybela.
        "knihovna_zname": knihovna_zname,
        # A kdyz cast stoji na dopoctu z data vzniku polozek, rekne i to:
        # dopocet popisuje minulost dnesnimi velikostmi.
        "knihovna_dopoctena": knihovna_dopoctena,
    }


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
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {_kind_condition(KIND_OTHER)}
        GROUP BY item_type
        ORDER BY SUM(watched_seconds) DESC
        """,
        (*_meze(days),),
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
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY day, item_type
        ORDER BY day
        """,
        (*_meze(days),),
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

    # Kalendar kreslime cely, at je v grafu videt i den, kdy se nekoukalo.
    #
    # U "poslednich N dni" je poslednim dnem dnesek a prvnim ten N-ty
    # zpatky - tedy N radku, ne N+1. U vlastniho obdobi zacina a konci
    # tam, kde rekl clovek.
    okno = _obdobi(days)
    # Vsechna data tu jsou uz prepoctena do zony aplikace (viz 'localtime'
    # v dotazu vyse) - kalendar se proto musi stavet ze stejnych hodin.
    # Meze okna jsou v UTC; kdyby se z nich bral den primo, zacinal by
    # graf v lete o den driv, protoze pulnoc v Praze je 22:00 predchoziho
    # dne v UTC.
    dnes = datetime.now(formatting.zona()).date()
    if okno.relativni:
        prvni, posledni = dnes - timedelta(days=okno.dny - 1), dnes
    else:
        prvni = _mistni_den(okno.od_mistni, okno.od)
        posledni = min(dnes, _mistni_den(okno.do_mistni, okno.do, konec=True))
    calendar = [prvni + timedelta(days=offset)
                for offset in range(max(1, (posledni - prvni).days + 1))]

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


def play_method_breakdown(days: int) -> list[dict[str, Any]]:
    """Kolik casu se hralo primo a kolik se muselo prepocitavat."""
    rows = db.query_all(
        """
        SELECT COALESCE(play_method, 'nezname') AS method,
               SUM(watched_seconds) / 3600.0    AS hours
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY method
        ORDER BY hours DESC
        """,
        (*_meze(days),),
    )
    # Popisky prochazeji `_t()`, protoze se vypisuji uzivateli - jak
    # v legende, tak primo v pruhu grafu.
    labels = {
        "DirectPlay": _t("Přímé přehrávání"),
        "DirectStream": _t("Přebalení (direct stream)"),
        "Transcode": _t("Transcode"),
        "nezname": _t("Neznámé"),
    }
    # Barva podle toho, co to server stoji - ne podle poradi v seznamu.
    # Tyhle tri hodnoty nejsou libovolne kategorie, ale stupnice: prime
    # prehravani neni prace zadna, prebaleni skoro zadna, prepocet vytizi
    # procesor nebo grafickou kartu. Graf tim rika totez co odznak
    # u prehravani a jantarovy kus na konci je varovani.
    # Presne tytez role, jake nese odznak u prehravani - viz makro
    # `method_badge`. Kdyz se lisily (odznak oranzovy, graf jantarovy),
    # vypadalo to jako dve ruzne veci; pritom je to tentyz udaj jednou
    # jako stav a podruhe jako podil.
    # Role se hleda podle ZACATKU nazvu, ne presnou shodou. Importovana
    # historie (Playback Reporting) nese podrobnejsi hodnoty - treba
    # "Transcode (v:h264 a:direct)" - a ty by pri presne shode propadly
    # do "muted", takze by v grafu zesedly, prestoze jde o prepocet.
    def _role(metoda: str) -> str:
        m = (metoda or "").strip().lower()
        if m.startswith("transcode"):
            return "serious"
        if m.startswith("directstream") or m.startswith("remux"):
            return "info"
        if m.startswith("directplay") or m.startswith("direct play"):
            return "good"
        return "muted"

    # Vic variant prepoctu pod sebou by melo jednu barvu a nesly by
    # rozeznat. Prvni si nechava oranzovou role - at je na prvni pohled
    # videt, ze jde o prepocet -, dalsi berou barvy z palety serii, tedy
    # z te same, jakou pouziva "kdo v jakem jazyce sleduje".
    #
    # Odstinovani jedne barvy tu bylo drive a nefungovalo: casti byvaji
    # uzke par pixelu a tri odstiny hnede na takove plose splynou.
    DALSI_BARVY = ("var(--series-5)", "var(--series-7)", "var(--series-4)",
                   "var(--series-8)", "var(--series-2)", "var(--series-6)")
    poradi: dict[str, int] = {}
    for row in rows:
        row["label"] = labels.get(row["method"], row["method"])
        row["role"] = _role(row["method"])
        poradi[row["role"]] = poradi.get(row["role"], -1) + 1
        kolikaty = poradi[row["role"]]
        if kolikaty:
            row["barva"] = DALSI_BARVY[(kolikaty - 1) % len(DALSI_BARVY)]
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
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY label
        ORDER BY hours DESC, label
        LIMIT ?
        """,
        (*_meze(days), limit),
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
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
        GROUP BY weekday, hour
        """,
        (*_meze(days),),
    )

    grid = [[0.0 for _ in range(24)] for _ in range(7)]
    for row in rows:
        # SQLite pocita tyden od nedele (0). My chceme od pondeli.
        weekday = (int(row["weekday"]) + 6) % 7
        grid[weekday][int(row["hour"])] += float(row["hours"] or 0)
    return grid
