"""Volné místo na disku (z Jellyfinu nebo z kapacity) a denní snímek knihovny.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

# `stats` a `formatting` kvuli dennimu snimku knihovny (zapis_snimek).
# Ani jeden si netahne scanner zpatky, takze kruh nevznikne - na rozdil
# od `tasks`, ktere se proto importuje az uvnitr funkce.
from .. import db, formatting, probe, stats

log = logging.getLogger("jellyscope.scanner")


# ---------------------------------------------------------------------------
# Kolik zbyva mista
# ---------------------------------------------------------------------------
#
# Tri zdroje, v tomhle poradi - a stranka pak rekne, ktery to byl:
#
#   1. RUCNE ZADANA kapacita. Kdyz knihovna lezi v cloudu nebo na sdilenem
#      ulozisti, nepozna velikost ani Jellyfin. Spravce ji vi a muze ji
#      napsat; jeho cislo prebiji vsechno ostatni.
#   2. JELLYFIN. Sedi u tech souboru, takze se ptame jeho.
#   3. DISK POD APLIKACI. Puvodni zpusob. Plati jen tehdy, kdyz data lezi
#      na temze stroji - jinak merime uplne cizi disk, a to bylo spatne.

KAPACITA_KLIC = "library_capacity_bytes"      # rucne zadana, v bajtech
JF_VOLNE_KLIC = "jellyfin_free_bytes"         # z posledni synchronizace
JF_CELKEM_KLIC = "jellyfin_total_bytes"
ZDROJ_KLIC = "volne_misto_zdroj"              # 'rucne' / 'jellyfin' / 'disk'


def _cislo(hodnota: Any) -> int | None:
    """Cislo z odpovedi Jellyfinu. None u vseho, co cislo neni."""
    try:
        cislo = int(float(hodnota))
    except (TypeError, ValueError):
        return None
    return cislo if cislo > 0 else None


def misto_z_jellyfinu(odpoved: Any, cesta: str = "") -> dict[str, int]:
    """Volne a celkove misto z odpovedi /System/Storage.

    Nazvy poli se mezi verzemi Jellyfinu lisily, takze se berou vsechny
    obvykle podoby. Kdyz nesedi nic, vratime prazdno - hadat cislo, ze
    ktereho se pak pocita "misto dojde za X dnu", by bylo horsi nez
    priznat, ze ho neznáme.

    `cesta` je slozka knihovny. Kdyz ji mezi slozkami najdeme, plati ta;
    jinak se vezme ta s nejmensim volnym mistem, protoze prvni dojde.
    """
    if not isinstance(odpoved, dict):
        return {}

    # Jellyfin 12 (`/System/Info/Storage`, overeno na 12.1.0) posila
    # slovnik pojmenovanych slozek serveru (ProgramDataFolder, LogFolder,
    # ...) a k tomu `Libraries`: seznam knihoven, kazda se svym seznamem
    # `Folders`. Slozky knihoven jsou ty, o ktere jde - na nich lezi
    # filmy. Slozky serveru se berou az jako zaloha: kdyz knihovna lezi
    # na jinem disku nez server, cislo ze serverove slozky by lhalo.
    slozky: list[dict[str, Any]] = []
    for knihovna in odpoved.get("Libraries") or []:
        if isinstance(knihovna, dict):
            slozky.extend(s for s in knihovna.get("Folders") or []
                          if isinstance(s, dict))
    for klic in ("Folders", "folders", "StorageFolders", "Items"):
        hodnota = odpoved.get(klic)
        if isinstance(hodnota, list):
            slozky.extend(s for s in hodnota if isinstance(s, dict))
    if not slozky:
        slozky = [v for v in odpoved.values()
                  if isinstance(v, dict) and "Path" in v]
    if not slozky:
        # Nekdy prijde rovnou jedna slozka, ne seznam.
        slozky = [odpoved]

    def pole(slozka: dict[str, Any], *jmena: str) -> int | None:
        for jmeno in jmena:
            cislo = _cislo(slozka.get(jmeno))
            if cislo is not None:
                return cislo
        return None

    zmerene = []
    for slozka in slozky:
        volne = pole(slozka, "FreeSpace", "freeSpace", "FreeSpaceBytes")
        celkem = pole(slozka, "TotalSpace", "totalSpace", "TotalSpaceBytes")
        # Dvanactka celkove misto neposila - jen volne a obsazene.
        # Soucet je totez cislo; -1 znamena "neumim zmerit" a `_cislo`
        # ho zahodi.
        obsazene = pole(slozka, "UsedSpace", "usedSpace")
        if celkem is None and volne is not None and obsazene is not None:
            celkem = volne + obsazene
        if volne is None and celkem is None:
            continue
        zmerene.append({
            "cesta": str(slozka.get("Path") or slozka.get("path") or ""),
            "volne": volne, "celkem": celkem,
        })
    if not zmerene:
        return {}

    cesta = (cesta or "").replace("\\", "/").rstrip("/").lower()
    if cesta:
        for polozka in zmerene:
            jina = polozka["cesta"].replace("\\", "/").rstrip("/").lower()
            if jina and (cesta.startswith(jina) or jina.startswith(cesta)):
                return {k: v for k, v in polozka.items()
                        if k != "cesta" and v is not None}

    # Zadna slozka nesedi na knihovnu - bereme tu nejtesnejsi, protoze
    # misto dojde na ni.
    s_volnym = [p for p in zmerene if p["volne"] is not None]
    nejtesnejsi = min(s_volnym, key=lambda p: p["volne"]) if s_volnym else zmerene[0]
    return {k: v for k, v in nejtesnejsi.items() if k != "cesta" and v is not None}


def zapamatuj_misto_z_jellyfinu(odpoved: Any) -> dict[str, int]:
    """Ulozi, co Jellyfin rekl o mistu. Vola se pri synchronizaci.

    Uklada se do nastaveni, ne do snimku: snimek se pise az potom
    a `zapis_snimek()` je synchronni - na Jellyfin uz se odtud ptat
    nemuze.
    """
    radek = db.query_one(
        "SELECT path FROM items"
        " WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    cesta = str((radek or {}).get("path") or "")

    misto = misto_z_jellyfinu(odpoved, cesta)
    db.set_setting(JF_VOLNE_KLIC, str(misto.get("volne", "")))
    db.set_setting(JF_CELKEM_KLIC, str(misto.get("celkem", "")))
    return misto


def _volne_z_kapacity() -> int | None:
    """Volne misto z rucne zadane kapacity: kapacita minus velikost knihovny.

    Je to odhad a stranka to rika: predpoklada, ze na tom ulozisti nic
    jineho nelezi. Spravce ale zna cislo, ktere jinak nezna nikdo -
    u knihovny v cloudu je to jedina cesta, jak neco rict.
    """
    kapacita = db.get_int_setting(KAPACITA_KLIC, 0, 10 ** 18, 0)
    if kapacita <= 0:
        return None
    velikost = int(db.query_value(
        "SELECT COALESCE(SUM(COALESCE(size_bytes, 0)), 0) FROM items"
        " WHERE is_missing = 0", default=0) or 0)
    return max(0, kapacita - velikost)


def _volne_misto_knihovny() -> int | None:
    """Volne misto tam, kde knihovna lezi. None, kdyz ho nezname.

    Poradi zdroju viz komentar na zacatku teto sekce.
    """
    rucne = _volne_z_kapacity()
    if rucne is not None:
        db.set_setting(ZDROJ_KLIC, "rucne")
        return rucne

    z_jellyfinu = db.get_int_setting(JF_VOLNE_KLIC, 0, 10 ** 18, 0)
    if z_jellyfinu > 0:
        db.set_setting(ZDROJ_KLIC, "jellyfin")
        return z_jellyfinu

    # `tasks` az tady: samo si tahne scanner, takze nahore by z toho byl kruh.
    from .. import tasks

    radek = db.query_one(
        "SELECT path FROM items"
        " WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    if not radek or not radek.get("path"):
        db.set_setting(ZDROJ_KLIC, "")
        return None

    try:
        mappings = json.loads(db.get_setting("path_mappings", "") or "[]")
        if not isinstance(mappings, list):
            mappings = []
    except ValueError:
        mappings = []

    cesta = Path(probe.apply_path_mappings(str(radek["path"]), mappings))
    # Slozka souboru, ne soubor sam - `disk_usage` chce adresar.
    volne = tasks.free_space(str(cesta.parent))
    db.set_setting(ZDROJ_KLIC, "disk" if volne is not None else "")
    return volne


def zapis_snimek() -> dict[str, Any] | None:
    """Ulozi dnesni stav knihovny - jeden radek na den.

    Tyz den se prepisuje: platí posledni znamy stav dne, ne prvni. Kdyz
    se behem dne neco doanalyzuje nebo prida, ma snimek ukazat to.

    Den se bere v ZONE APLIKACE, aby řádky sedely s tim, co je videt
    v grafech - ne v UTC, kde by se vecerni synchronizace zapsala uz
    na zitrek.
    """
    # Tytéž součty, jaké kreslí konec křivky růstu a ukazuje dlaždice
    # „Velikost celkem" - jedna funkce, aby se ta tři čísla nemohla
    # rozejít. Viz stats.stav_knihovny().
    souhrn = stats.stav_knihovny()
    if not souhrn or not souhrn.get("polozek"):
        return None          # prazdna knihovna: neni co zaznamenat

    den = datetime.now(formatting.zona()).strftime("%Y-%m-%d")
    radek = {
        "den": den,
        "polozek": int(souhrn["polozek"] or 0),
        "filmu": int(souhrn["filmu"] or 0),
        "epizod": int(souhrn["epizod"] or 0),
        "velikost": int(souhrn["velikost"] or 0),
        "uhd": int(souhrn["uhd"] or 0),
        "hdr": int(souhrn["hdr"] or 0),
        "bez_technik": int(souhrn["bez_technik"] or 0),
        "volne_misto": _volne_misto_knihovny(),
        "zapsano_v": db.utcnow(),
    }

    # Hodnoty se vyjmenovavaji, ne berou z `radek.values()`: spolehat se
    # na poradi klicu ve slovniku je past, ktera se pri pridani sloupce
    # projevi tim, ze se cisla prohodí - a nikdo si toho nevsimne.
    sloupce = ("den", "polozek", "filmu", "epizod", "velikost", "uhd", "hdr",
               "bez_technik", "volne_misto", "zapsano_v")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO library_snapshot
                (den, polozek, filmu, epizod, velikost, uhd, hdr,
                 bez_technik, volne_misto, zapsano_v)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (den) DO UPDATE SET
                polozek     = excluded.polozek,
                filmu       = excluded.filmu,
                epizod      = excluded.epizod,
                velikost    = excluded.velikost,
                uhd         = excluded.uhd,
                hdr         = excluded.hdr,
                bez_technik = excluded.bez_technik,
                volne_misto = excluded.volne_misto,
                zapsano_v   = excluded.zapsano_v
            """,
            tuple(radek[jmeno] for jmeno in sloupce),
        )
        conn.commit()

    log.info("snimek knihovny %s: %s polozek, %s bajtu",
             den, radek["polozek"], radek["velikost"])
    return radek
