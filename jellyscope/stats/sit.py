"""Síť: kolik toho teklo k přehrávačům, špičky a živý tok.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from .. import db, formatting
from .divaci import DIVAK_JMENO, DIVAK_SKUPINA
from .obdobi import _meze, _obdobi


# ---------------------------------------------------------------------------
# Sit - kolik toho teklo k prehravacum
#
# Cislo, se kterym se tu pracuje, je `playback.bitrate`: tok toho streamu
# v bitech za sekundu. Sberac ho bere z relace - kdyz server prekoduje,
# je to vysledny (prepocitany) tok, jinak bitrate zdrojoveho souboru.
#
# Je to **odhad podle deklarovaneho bitrate, ne mereni dratu.** Preskakovani
# v prehravaci a buffer znamenaji, ze skutecne prenesene bajty se lisi.
# Presne cislo umi jen reverzni proxy nebo pocitadla systemu - mimo
# Jellyfin. Rika to i stranka, at si to nikdo neplete s merenim.
#
# Pauzy se ale odecitaji: pocita se `watched_seconds`, tedy cas, kdy se
# doopravdy sledovalo. Pozastavene prehravani zadna data netahne, takze
# by ho graf jinak ukazoval jako beziciho - viz bandwidth_prubeh().
# ---------------------------------------------------------------------------

def _sitove_radky(days: int) -> list[dict[str, Any]]:
    """Prehravani s known bitrate za obdobi. Zaklad pro vsechno ostatni."""
    return db.query_all(
        """
        SELECT started_at, ended_at, last_seen_at, watched_seconds, bitrate,
               user_name, client, device_name, remote_address, play_method
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
           AND bitrate IS NOT NULL AND bitrate > 0
         ORDER BY started_at
        """,
        (*_meze(days),),
    )


def _sekundy(text: Any) -> float | None:
    """Cas z databaze na sekundy. None, kdyz se neda precist.

    `replace(tzinfo=utc)` neni kosmetika. V databazi je cas v UTC a bez
    zony; `datetime.timestamp()` ale takovy cas povazuje za MISTNI, takze
    vysledek byl posunuty o cely offset. Popisky pod grafem pak ukazovaly
    UTC, zatimco zbytek aplikace mistni cas - vecerni spicka tak v lete
    "nastavala" o hodinu driv, nez ve skutecnosti byla.

    Zbytek aplikace to dela stejne, viz formatting._parse_any().
    """
    try:
        cas = datetime.strptime(str(text).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return None
    return cas.replace(tzinfo=UTC).timestamp()


def bandwidth_prubeh(days: int, bodu: int = 120) -> list[dict[str, Any]]:
    """Soubezny tok v case - kolik Mbit/s teklo ze serveru zaroven.

    Pocita se **prochazenim udalosti**, ne vzorkovanim: kazde prehravani
    prida svuj bitrate na zacatku a ubere ho na konci. Serazenim udalosti
    podle casu vznikne presna krivka souběžneho toku, at uz prehravani
    trvalo minutu nebo pet hodin.

    Do grafu se z ni bere maximum v kazdem useku - prumer by spicky
    zahladil, a prave spicka je to, co zajima: podle ni se dimenzuje
    linka.
    """
    radky = _sitove_radky(days)
    if not radky:
        return []

    udalosti: list[tuple[float, int]] = []
    for radek in radky:
        zacatek = _sekundy(radek["started_at"])
        konec = _sekundy(radek["ended_at"] or radek["last_seen_at"])
        if zacatek is None:
            continue
        if konec is None or konec <= zacatek:
            konec = zacatek + float(radek["watched_seconds"] or 0)
        # Pauza neteče. Bez tohohle omezení se bitrate rozprostřel přes
        # celý čas od začátku do konce - takže film pozastavený přes noc
        # držel v grafu plný tok do rána a stejně tak přehrávání, které
        # je pozastavené PRÁVĚ TEĎ (u něj `last_seen_at` běží dál).
        #
        # Kdy přesně se pauzovalo, databáze neví - ukládá se jen součet
        # `watched_seconds`. Bereme proto délku toku podle něj: tok trvá
        # tak dlouho, jak dlouho se doopravdy sledovalo. U pauzy uprostřed
        # je tím pádem posunutý dopředu, ale jeho **množství i výška**
        # sedí - a součet pod křivkou tak konečně odpovídá objemu dat,
        # který stránka ukazuje vedle.
        odsledovano = float(radek["watched_seconds"] or 0)
        if odsledovano > 0:
            konec = min(konec, zacatek + odsledovano)

        udalosti.append((zacatek, int(radek["bitrate"])))
        udalosti.append((konec, -int(radek["bitrate"])))

    if not udalosti:
        return []
    udalosti.sort()

    od, do = udalosti[0][0], udalosti[-1][0]
    krok = max((do - od) / max(1, bodu), 1.0)

    body: list[dict[str, Any]] = []
    soucet = 0
    hranice = od + krok
    vrchol = 0
    for cas, zmena in udalosti:
        while cas > hranice:
            body.append({"cas": hranice - krok / 2, "mbit": round(vrchol / 1e6, 2)})
            hranice += krok
            vrchol = soucet
        soucet += zmena
        vrchol = max(vrchol, soucet)
    body.append({"cas": hranice - krok / 2, "mbit": round(vrchol / 1e6, 2)})

    for bod in body:
        # Tataz zona jako u vypisu casu jinde - viz formatting.zona().
        # Osa grafu a sloupec "kdy" v historii musi rikat totez, jinak
        # clovek hleda tentyz zaznam na dvou ruznych casech.
        bod["popisek"] = datetime.fromtimestamp(
            bod["cas"], formatting.zona()).strftime("%d.%m. %H:%M")
        # Jak dlouhy usek jeden bod zastupuje. Grafu se lidi casto ptaji
        # "jak casto se to meri" - nemeri se vubec, prochazeji se zacatky
        # a konce prehravani a z kazdeho useku se bere spicka. Bez tohohle
        # udaje vypada krivka nahodne; s nim je videt, ze jeden vrchol
        # muze byt jedina hodina z sesti.
        bod["krok_minut"] = round(krok / 60)
    return body


def tok_ted() -> dict[str, Any]:
    """Kolik tece ze serveru PRAVE TED - z bezicich prehravani.

    Na rozdil od krivky niz se tady nic nedopocitava z historie: bere se
    to, co sberac videl pri poslednim dotazu. Pozastavena prehravani se
    nepocitaji, protoze pri pauze nic netece - ale pocitaji se zvlast,
    aby bylo videt, ze tam jsou.
    """
    radky = db.query_all(
        "SELECT bitrate, is_paused, play_method, user_name, item_name "
        "FROM playback WHERE is_active = 1")

    tekouci = [r for r in radky if not r["is_paused"] and (r["bitrate"] or 0) > 0]
    return {
        "mbit": round(sum(int(r["bitrate"]) for r in tekouci) / 1e6, 2),
        "streamu": len(tekouci),
        "pozastavenych": sum(1 for r in radky if r["is_paused"]),
        "bez_toku": sum(1 for r in radky
                        if not r["is_paused"] and not (r["bitrate"] or 0)),
        "prepoctu": sum(1 for r in tekouci
                        if str(r["play_method"] or "").lower().startswith("transcode")),
    }


# Nejkratsi okno zive krivky. Pod jeden den nema smysl klesat: hodinovy
# vyrez rekne "prave nic netece" i vecer, kdy se hralo celou dobu, jen
# zrovna dobehl posledni dil.
NEJKRATSI_ZIVE_OKNO = 24 * 3600


# Jak dlouhy usek zastupuje jeden bod zive krivky a kolik bodu nejvic.
#
# 180 bodu pres tyden znamenalo jeden bod na 56 minut - a protoze se
# z useku bere spicka, desetiminutovy stream se v grafu roztahl na
# hodinu. Peti minutam uz odpovida tvar krivky tomu, co se doopravdy
# delo; strop je kvuli velikosti obrazku.
USEK_ZIVE_SEKUND = 300
# Strop bodu. Puvodne 1500, jenze u tydne z toho byla srst: spicka jeden
# pixel siroka, kterou mys netrefi. Sest set bodu znamena u dne porad
# petiminutovy krok a u tydne ctvrthodinovy - to uz je videt jako sloupec,
# ne jako vlas. Delsi obdobi patri karte "Spicka po dnech".
NEJVIC_BODU_ZIVE = 600


def bandwidth_zive(days: Any = None, bodu: int | None = None) -> list[dict[str, Any]]:
    """Tok v case pro zive okno - podle zvoleneho obdobi, nejmene 24 hodin.

    Proc zvlast a ne jen jiny rozsah te krivky niz: ta bere prehravani
    podle toho, kdy ZACALA, takze stream spusteny predevcirem by do okna
    vubec nespadl, prestoze prave tece. Tady se hledaji prehravani, ktera
    se s oknem **prekryvaji**.

    `OR is_active = 1` v podmince neni pojistka navic. Bezici prehravani
    tece i tehdy, kdyz se sberac naposledy ozval pred delsi dobou - treba
    protoze Jellyfin chvili neodpovidal. Bez toho by krivka spadla na nulu,
    zatimco cislo vedle ni by ukazovalo plny tok.
    """
    ted = datetime.now(UTC).timestamp()

    # Okno se ridi filtrem stranky. Cislo vedle krivky je porad "ted",
    # ale krivka ukazuje to, co si clovek vybral nahore - jinak by filtr
    # na te karte nedelal nic a pusobil rozbite.
    okno = NEJKRATSI_ZIVE_OKNO
    do_okna = ted
    if days is not None:
        obdobi = _obdobi(days)
        zacatek = _sekundy(obdobi.od)
        konec = min(_sekundy(obdobi.do) or ted, ted)
        if zacatek is not None and konec > zacatek:
            if obdobi.relativni:
                # "Poslednich N dni" konci ted a pod den se neklesa - viz
                # NEJKRATSI_ZIVE_OKNO.
                okno = max(NEJKRATSI_ZIVE_OKNO, konec - zacatek)
            else:
                # Pevne obdobi (vyklikane, nebo vybrane tazenim v grafu)
                # se bere PRESNE tak, jak zni - vcetne konce v minulosti.
                # Kdyby krivka porad koncila "ted", ukazovala by neco
                # jineho, nez rika prepinac nad ni - a druhy tah by pak
                # vybral uplne jiny cas, nez na ktery clovek ukazuje.
                okno, do_okna = konec - zacatek, konec
    od = do_okna - okno

    # Rozliseni podle delky okna, ne pevny pocet bodu.
    if bodu is None:
        bodu = int(min(NEJVIC_BODU_ZIVE, max(60, okno // USEK_ZIVE_SEKUND)))

    radky = db.query_all(
        """
        SELECT started_at, ended_at, last_seen_at, watched_seconds, bitrate,
               is_paused, is_active
          FROM playback
         WHERE bitrate IS NOT NULL AND bitrate > 0
           AND watched_seconds > 0
           AND (COALESCE(ended_at, last_seen_at) >= ? OR is_active = 1)
         ORDER BY started_at
        """,
        (datetime.fromtimestamp(od, UTC).strftime(db.TIME_FORMAT),),
    )

    udalosti: list[tuple[float, int]] = []
    for radek in radky:
        zacatek = _sekundy(radek["started_at"])
        if zacatek is None:
            continue
        konec = _sekundy(radek["ended_at"] or radek["last_seen_at"]) or ted
        # Pozastavene prave ted netece - konec dame tam, kde se naposledy
        # hralo. U ostatnich plati tataz uvaha jako u dlouhe krivky:
        # tok trva tak dlouho, jak dlouho se doopravdy sledovalo.
        odsledovano = float(radek["watched_seconds"] or 0)
        if odsledovano > 0:
            konec = min(konec, zacatek + odsledovano)
        if radek["is_active"] and not radek["is_paused"]:
            # Bezici stream tece i ted, i kdyz se sberac ozval pred chvili.
            konec = max(konec, ted)
        if konec <= od:
            continue
        udalosti.append((max(zacatek, od), int(radek["bitrate"])))
        udalosti.append((konec, -int(radek["bitrate"])))

    krok = (do_okna - od) / max(1, bodu)
    body: list[dict[str, Any]] = []
    udalosti.sort()
    soucet = 0
    kolik = 0            # kolik streamu tece zaroven
    index = 0
    for i in range(bodu):
        hranice = od + (i + 1) * krok
        vrchol = soucet
        vrchol_kolik = kolik
        while index < len(udalosti) and udalosti[index][0] <= hranice:
            soucet += udalosti[index][1]
            kolik += 1 if udalosti[index][1] > 0 else -1
            if soucet > vrchol:
                vrchol = soucet
                vrchol_kolik = kolik
            index += 1
        stred = hranice - krok / 2
        # U kratkeho okna staci hodina a minuta; jakmile krivka prekroci
        # dva dny, musi byt videt i datum - jinak se streda a ctvrtek na
        # ose nedaji rozlisit.
        tvar = "%H:%M" if okno <= 48 * 3600 else "%d.%m. %H:%M"
        body.append({
            "cas": stred,
            "mbit": round(max(0, vrchol) / 1e6, 2),
            # Kolik streamu ten vrchol tvorilo. V bubline to odpovida na
            # druhou otazku, kterou clovek u spicky ma: bylo to hodne lidi,
            # nebo jeden film ve 4K?
            "streamu": max(0, vrchol_kolik),
            "popisek": datetime.fromtimestamp(stred, formatting.zona()).strftime(tvar),
            "krok_minut": round(krok / 60) or 1,
        })
    return body


def bandwidth_denni_spicky(days: int) -> list[dict[str, Any]]:
    """Nejvyssi soubezny tok kazdeho dne.

    Pres delsi obdobi nema smysl kreslit kazdou minutu - bodu by bylo
    vic nez pixelu a krivka by vypadala jako sum. Jeden den = jedno
    cislo, a to nejvyssi: podle spicky se dimenzuje linka, prumer by ji
    zahladil.
    """
    radky = _sitove_radky(days)
    if not radky:
        return []

    udalosti: list[tuple[float, int]] = []
    for radek in radky:
        zacatek = _sekundy(radek["started_at"])
        konec = _sekundy(radek["ended_at"] or radek["last_seen_at"])
        if zacatek is None:
            continue
        if konec is None or konec <= zacatek:
            konec = zacatek + float(radek["watched_seconds"] or 0)
        odsledovano = float(radek["watched_seconds"] or 0)
        if odsledovano > 0:
            konec = min(konec, zacatek + odsledovano)
        udalosti.append((zacatek, int(radek["bitrate"])))
        udalosti.append((konec, -int(radek["bitrate"])))

    if not udalosti:
        return []
    udalosti.sort()

    # Prochazime udalosti a u kazdeho dne si pamatujeme nejvyssi soucet.
    # Den se urcuje v ZONE APLIKACE - jinak by spicka z pulnoci padla do
    # jineho dne, nez ve kterem ji clovek zazil.
    zona = formatting.zona()
    spicky: dict[str, float] = {}
    soucet = 0
    for cas, zmena in udalosti:
        soucet += zmena
        den = datetime.fromtimestamp(cas, zona).strftime("%Y-%m-%d")
        spicky[den] = max(spicky.get(den, 0), soucet)

    return [{"den": den,
             "popisek": datetime.strptime(den, "%Y-%m-%d").strftime("%d.%m."),
             "mbit": round(hodnota / 1e6, 2)}
            for den, hodnota in sorted(spicky.items())]


def bandwidth_prehled(days: int) -> dict[str, Any]:
    """Spicka, objem dat a podil transcode. Cisla nad grafem."""
    radky = _sitove_radky(days)
    prubeh = bandwidth_prubeh(days, bodu=600)

    bajtu = 0.0
    bajtu_transcode = 0.0
    for radek in radky:
        objem = float(radek["bitrate"]) * float(radek["watched_seconds"] or 0) / 8.0
        bajtu += objem
        if str(radek["play_method"] or "") == "Transcode":
            bajtu_transcode += objem

    spicka = max(prubeh, key=lambda b: b["mbit"], default=None)
    return {
        "spicka_mbit": spicka["mbit"] if spicka else 0.0,
        "spicka_kdy": spicka["popisek"] if spicka else "",
        "bajtu": int(bajtu),
        "bajtu_transcode": int(bajtu_transcode),
        "podil_transcode": round(bajtu_transcode / bajtu * 100, 1) if bajtu else 0.0,
        "prehravani": len(radky),
        "prumer_mbit": (round(sum(float(r["bitrate"]) for r in radky)
                              / len(radky) / 1e6, 2) if radky else 0.0),
    }


def bandwidth_podle(days: int, sloupec: str, limit: int = 12) -> list[dict[str, Any]]:
    """Objem dat podle uzivatele, klienta nebo zarizeni - od nejvetsiho.

    `sloupec` se porovnava proti pevnemu seznamu, takze se z adresy nikdy
    nedostane nic do SQL.
    """
    povolene = {"user_name", "client", "device_name"}
    if sloupec not in povolene:
        sloupec = "user_name"
    # Diváci po divácích (viz DIVAK_JMENO), klienti a zařízení po názvu.
    divaci = sloupec == "user_name"
    popisek = f"COALESCE({DIVAK_JMENO}, '?')" if divaci else f"COALESCE(p.{sloupec}, '?')"
    skupina = DIVAK_SKUPINA if divaci else f"COALESCE(p.{sloupec}, '?')"
    spojeni = "LEFT JOIN users u ON u.id = p.user_id" if divaci else ""

    rows = db.query_all(
        f"""
        SELECT {popisek}                                       AS label,
               SUM(p.watched_seconds * p.bitrate) / 8.0        AS bajtu,
               MAX(p.bitrate)                                  AS spicka,
               COUNT(*)                                        AS plays,
               -- Id uzivatele kvuli prokliku na jeho detail. MIN, protoze
               -- PostgreSQL nedovoli sloupec mimo GROUP BY; u divaku je
               -- user_id ve skupine, takze je to vzdy tentyz clovek.
               MIN(p.user_id)                                  AS user_id
          FROM playback p
          {spojeni}
         WHERE p.started_at >= ? AND p.started_at < ?
           AND p.watched_seconds > 0
           AND p.bitrate IS NOT NULL AND p.bitrate > 0
         GROUP BY {skupina}
         ORDER BY SUM(p.watched_seconds * p.bitrate) DESC
         LIMIT ?
        """,
        (*_meze(days), limit),
    )
    # Graf dostava gigabajty, ne bajty: sloupec popsany "98 739 089 379 B"
    # se neda precist, a presnost na bajt tu k nicemu neni.
    return [{"label": r["label"], "bajtu": int(r["bajtu"] or 0),
             "gb": round(float(r["bajtu"] or 0) / 1e9, 1),
             "spicka_mbit": round(float(r["spicka"] or 0) / 1e6, 2),
             "plays": int(r["plays"] or 0),
             # Jen u lidi - proklik na "Chrome" nebo "Shield TV" nikam
             # nevede, takovou stranku nemame.
             "user_id": r["user_id"] if sloupec == "user_name" else None}
            for r in rows]
