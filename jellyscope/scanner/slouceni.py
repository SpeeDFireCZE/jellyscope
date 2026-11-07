"""Sloučení podle TMDB, dvojníci nových položek, přenos historie, archiv.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db

log = logging.getLogger("jellyscope.scanner")


def _stare_ke_slouceni(
    pairs: list[tuple[tuple[str, int, int], str]],
    chranena: set[str],
    videno_od: str | None = None,
) -> dict[str, str]:
    """Ktere ulozene polozky by slucovani zabralo. Vraci {stare id: nove id}.

    Jen ctenim - slouzi k tomu, aby se dalo predem overit, jestli ta
    "stara" polozka v Jellyfinu opravdu uz neni. Viz `_merge_by_tmdb`.
    """
    if not pairs:
        return {}

    nalezene: dict[str, str] = {}
    with db.connect() as conn:
        for (tmdb, rada, dil), new_id in pairs:
            podminka = ""
            parametry: list[Any] = [tmdb, rada, dil, new_id]
            if videno_od:
                # Polozku, kterou Jellyfin v tomhle behu poslal, slucovat
                # nesmime - ta zjevne existuje dal.
                podminka = " AND (i.synced_at IS NULL OR i.synced_at < ?)"
                parametry.append(videno_od)

            row = conn.execute(
                f"""
                SELECT i.id
                  FROM items i
             LEFT JOIN playback p ON p.item_id = i.id
                 WHERE i.tmdb_id = ?
                   AND COALESCE(i.parent_index_number, -1) = ?
                   AND COALESCE(i.index_number, -1) = ?
                   AND i.id != ?{podminka}
              GROUP BY i.id
              ORDER BY COALESCE(SUM(p.watched_seconds), 0) DESC
                 LIMIT 1
                """,
                tuple(parametry),
            ).fetchone()
            if row is not None and str(row["id"]) not in chranena:
                nalezene[str(row["id"])] = new_id
    return nalezene


def _merge_by_tmdb(pairs: list[tuple[tuple[str, int, int], str]],
                   chranena: set[str] | None = None,
                   videno_od: str | None = None) -> int:
    """Preveze historii ze stare polozky na novou, kdyz jde o tentyz poradek.

    Situace, kterou to resi: prekodujes film do HEVC a nahradis puvodni
    soubor. Jellyfin to nepozna jako zmenu - zalozi **novou polozku
    s novym ItemId** a stara zmizi. Bez tehle funkce by se historie
    prehravani rozpadla na dva tituly, z nichz jeden by skoncil v archivu.

    **Slucuje se jen tehdy, kdyz ta stara polozka opravdu zmizela.**
    Tim se lisi vymena souboru od druhe kopie tehoz dilu: kdyz mas
    epizodu v knihovne dvakrat (jina kvalita, zbyla stara verze), jsou
    v Jellyfinu obe - a slouceni by jednu z nich smazalo. Pri dalsim
    scanu by se vratila a smazala tu druhou; polozky by se stridaly
    a odkazy na ne prestavaly platit. Proto:
      * `chranena` = id, o kterych vime, ze v Jellyfinu jsou (prave
        zapisovana davka, u rychle synchronizace i overeni doptanim),
      * `videno_od` = zacatek behu; polozka se `synced_at` z tohohle behu
        prisla z Jellyfinu prave ted, takze se take nesluci.

    Poznavacim znamenim je `identita_polozky()`: u filmu tmdb_id, u epizody
    tmdb_id serialu spolu s cislem rady a dilu. Samotne tmdb_id na epizodu
    nestaci - vsechny dily serialu ho maji stejne, takze by se slucovaly
    navzajem. Viz komentar u `identita_polozky()`.

    Postup je opatrny a v tomhle poradi:
      1. stopy stare polozky smazeme - k novemu souboru stejne nepatri
         a cizi klic by nam nedovolil zmenit id, dokud existuji,
      2. historii prehravani prepiseme na nove id,
      3. teprve pak zmenime id polozky,
      4. a technicka data smazeme - popisuji stary soubor, ktery uz
         neexistuje. Datum pridani a zbytek zustavaji.

    Vraci pocet slouceni.
    """
    # Stara polozka = stejna identita, ale jine ItemId. Kdyz jich je vic
    # (napr. film byl v knihovne dvakrat), bere se ta, ktera ma odsledovaneho
    # nejvic - o tu prijit nechceme. Vyber resi `_stare_ke_slouceni()`.
    dvojice = _stare_ke_slouceni(pairs, chranena or set(), videno_od)
    if not dvojice:
        return 0

    merged = 0
    with db.connect() as conn:
        for old_id, new_id in dvojice.items():
            # Kdyz uz nova polozka v databazi je, nesmime na ni stare id
            # prepsat - vzniklo by duplicitni id. V tom pripade jen
            # preneseme historii a starou polozku zahodime.
            exists = conn.execute(
                "SELECT 1 FROM items WHERE id = ?", (new_id,)
            ).fetchone()

            # Stopy i verze popisuji STARY soubor - ten uz na disku neni,
            # jinak by se neslucovalo. Musi pryc driv, nez se polozka
            # smaze nebo prejmenuje: odkazuji se na ni cizim klicem.
            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (old_id,))
            conn.execute("DELETE FROM item_versions WHERE item_id = ?", (old_id,))
            conn.execute(
                "UPDATE playback SET item_id = ? WHERE item_id = ?", (new_id, old_id)
            )

            if exists:
                conn.execute("DELETE FROM items WHERE id = ?", (old_id,))
            else:
                conn.execute(
                    "UPDATE items SET id = ?, is_missing = 0 WHERE id = ?",
                    (new_id, old_id),
                )
                # A technicka data pryc. Prejmenovanim polozky by jinak
                # zustala viset na novem souboru - jenze popisuji ten
                # STARY, ktery uz na disku neni. Slucujeme prave proto,
                # ze se soubor vymenil.
                #
                # Nejhorsi na tom bylo `tech_source`: analyza souboru
                # bere jen polozky, ktere zadna data nemaji, takze tuhle
                # navzdy preskakovala. Na detailu pak svitilo rozliseni
                # puvodniho souboru (nebo prazdno) a pomohlo jen rucni
                # "Nacist metadata znovu".
                conn.execute(
                    """
                    UPDATE items
                       SET container = NULL, video_codec = NULL, audio_codec = NULL,
                           audio_channels = NULL, width = NULL, height = NULL,
                           bitrate = NULL, size_bytes = NULL, video_range = NULL,
                           video_range_reported = NULL,
                           audio_languages = NULL, subtitle_languages = NULL,
                           default_audio_language = NULL,
                           audio_from_name = NULL, subtitle_from_name = NULL,
                           tech_source = NULL, tech_updated_at = NULL, tech_error = NULL
                     WHERE id = ?
                    """,
                    (new_id,),
                )

            log.info("slouceno: %s -> %s", old_id, new_id)
            merged += 1

    return merged


def dvojnici_novych(nova_id: list[str]) -> dict[str, list[str]]:
    """Ke každé novince starší položky, které vypadají jako tentýž titul.

    **Rozhoduje TMDB id**, protože přežije překódování, přejmenování
    souboru i to, že si Jellyfin doplní jiný název. Teprve když ho titul
    nemá - a to je přesně ten případ, který tudy propadával - hledá se
    podle názvu a roku, u dílu podle seriálu a čísel řady a dílu.

    U epizody **tmdb id samo nestačí**: Jellyfin u dílu hlásí id celého
    SERIÁLU, takže by se podle něj slily všechny díly do jednoho. Proto
    se k němu vždycky přidávají čísla řady a dílu.

    Vrací {nové id: [starší id]}. Že je to opravdu náhrada a ne druhá
    kopie, se rozhodne až podle Jellyfinu - viz `srovnej_po_novych`.
    """
    if not nova_id:
        return {}

    nalezeno: dict[str, list[str]] = {}
    with db.connect() as conn:
        for id_novy in nova_id:
            radek = conn.execute(
                "SELECT type, name, series_id, index_number,"
                " parent_index_number, production_year, tmdb_id"
                " FROM items WHERE id = ?", (id_novy,)).fetchone()
            if radek is None:
                continue
            novy = dict(radek)

            kandidati = _podle_tmdb(conn, id_novy, novy)
            if not kandidati:
                kandidati = _podle_nazvu(conn, id_novy, novy)
            if kandidati:
                nalezeno[id_novy] = kandidati
    return nalezeno


def _podle_tmdb(conn: Any, id_novy: str,
                novy: dict[str, Any]) -> list[str]:
    """Dvojníci podle TMDB id. Prázdný seznam = nemá ho, nebo nikdo nesedí."""
    tmdb = str(novy.get("tmdb_id") or "").strip()
    if not tmdb:
        return []

    podminky = ["tmdb_id = ?", "type = ?"]
    parametry: list[Any] = [id_novy, tmdb, novy.get("type")]
    if novy.get("type") == "Episode":
        # Serial ma tmdb id spolecne pro vsechny dily - bez cisel by se
        # slila cela rada do jednoho.
        _rovno_nebo_nic(podminky, parametry, "index_number",
                        novy.get("index_number"))
        _rovno_nebo_nic(podminky, parametry, "parent_index_number",
                        novy.get("parent_index_number"))
        if novy.get("index_number") is None:
            # Dil bez cisla nejde podle tmdb rozlisit od ostatnich -
            # radsi nic nez slit cely serial.
            return []
    return _vyber(conn, podminky, parametry)


def _podle_nazvu(conn: Any, id_novy: str,
                 novy: dict[str, Any]) -> list[str]:
    """Záloha pro tituly bez TMDB id: název a rok, u dílu seriál a čísla."""
    if novy.get("type") == "Episode" and novy.get("series_id"):
        podminky = ["type = 'Episode'", "series_id = ?"]
        parametry: list[Any] = [id_novy, novy["series_id"]]
        _rovno_nebo_nic(podminky, parametry, "index_number",
                        novy.get("index_number"))
        _rovno_nebo_nic(podminky, parametry, "parent_index_number",
                        novy.get("parent_index_number"))
    else:
        podminky = ["type = ?", "name = ?"]
        parametry = [id_novy, novy.get("type"), novy.get("name")]
        _rovno_nebo_nic(podminky, parametry, "production_year",
                        novy.get("production_year"))
    return _vyber(conn, podminky, parametry)


def _vyber(conn: Any, podminky: list[str], parametry: list[Any]) -> list[str]:
    radky = conn.execute(
        "SELECT id FROM items WHERE id <> ? AND is_missing = 0 AND "
        + " AND ".join(podminky), tuple(parametry)).fetchall()
    return [str(dict(r)["id"]) for r in radky]


def prenes_historii(dvojice: dict[str, str]) -> int:
    """Historie nahrazeného souboru patří tomu novému. {starý: nový}.

    Položka zůstává v archivu - v Jellyfinu doopravdy není a tvářit se,
    že je, by byla lež. Přesouvá se jen historie: „kolikrát jsem to
    viděl" má být o filmu, ne o souboru, který se mezitím překódoval.

    Technická data se starým souborem odcházejí taky. Popisovala ho, ne
    ten nový, a kdyby zůstala, analýza je už nikdy nepřepíše - bere jen
    položky, které žádná nemají.
    """
    if not dvojice:
        return 0
    preneseno = 0
    with db.connect() as conn:
        for stary, novy in dvojice.items():
            radek = conn.execute(
                "SELECT COUNT(*) AS pocet FROM playback WHERE item_id = ?",
                (stary,)).fetchone()
            kolik = int(dict(radek or {}).get("pocet") or 0)
            if not kolik:
                continue
            conn.execute("UPDATE playback SET item_id = ? WHERE item_id = ?",
                         (novy, stary))
            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (stary,))
            conn.execute("DELETE FROM item_versions WHERE item_id = ?", (stary,))
            preneseno += kolik
            log.info("nahrazeny soubor: historie %s -> %s (%s zaznamu)",
                     stary, novy, kolik)
        conn.commit()
    return preneseno


def _rovno_nebo_nic(podminky: list[str], parametry: list[Any],
                    sloupec: str, hodnota: Any) -> None:
    """Podmínka „sloupec se rovná", která umí i prázdnou hodnotu.

    `sloupec IS ?` je zkratka, kterou zná jen SQLite - PostgreSQL za
    `IS` parametr nepustí a skončí syntaktickou chybou. A `= NULL` se
    v SQL nikdy nerovná ničemu, takže prázdný rok nebo chybějící číslo
    dílu by tiše nenašly nic. Proto se podmínka skládá podle hodnoty.
    """
    if hodnota is None:
        podminky.append(f"{sloupec} IS NULL")
    else:
        podminky.append(f"{sloupec} = ?")
        parametry.append(hodnota)


def do_archivu(ids: list[str]) -> int:
    """Označí položky za zmizelé. Nic nemaže - historie na ně odkazuje."""
    if not ids:
        return 0
    with db.connect() as conn:
        for zacatek in range(0, len(ids), 200):
            davka = ids[zacatek:zacatek + 200]
            otazniky = ",".join("?" for _ in davka)
            conn.execute(
                f"UPDATE items SET is_missing = 1 WHERE id IN ({otazniky})",
                tuple(davka))
        conn.commit()
    return len(ids)
