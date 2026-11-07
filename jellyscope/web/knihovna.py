"""Knihovna, tituly a položky, obnova z Jellyfinu, archiv a obrázky.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import db, langstats, languages, pristup, scanner, sekce, stats, tasks
from ..i18n import translate as _t
from ..jellyfin import JellyfinClient
from .zaklad import _context, _flash, config, require_admin, require_login, templates
from .obdobi import _days

router = APIRouter()


# Kolik archivovanych polozek ukazuji okna archivu (nejvic prehravane
# napred). U prirazeni ma kazdy radek vlastni hledani, proto mene; po
# smazani nebo prirazeni se ukazou dalsi.
STROP_PRIRAZENI_ARCHIVU = 500
STROP_MAZANI_ARCHIVU = 1000


@router.get("/library", response_class=HTMLResponse)
def library_index(request: Request, days: int | None = None,
                  od: str | None = None, do: str | None = None,
                  account: dict[str, Any] = Depends(require_login)):
    """Rozcestnik: dlazdice jednotlivych knihoven z Jellyfinu.

    Obdobi je tytez jako vsude jinde - jedna volba pro celou aplikaci,
    vcetne vlastniho rozmezi. Tyka se jedineho grafu na strance (rust
    knihovny); dlazdice nad nim jsou stav, ne obdobi, a rikaji to samy.
    """
    days = _days(request, days, od, do)
    return templates.TemplateResponse(request, "library_index.html", _context(
        request, account,
        days=days,
        libraries=stats.library_cards(),
        coverage=stats.tech_coverage(),
        codecs=stats.codec_breakdown(),
        resolutions=stats.resolution_breakdown(),
        ranges=stats.video_range_breakdown(),
        **sekce.data_rustu(days),
    ))


@router.get("/library/{library_id}", response_class=HTMLResponse)
def library_detail(
    request: Request,
    library_id: str,
    tab: str = "overview",
    search: str | None = None,
    sort: str = "size",
    page: int = 1,
    archived: int = 0,
    days: int | None = None,
    od: str | None = None,
    do: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Detail jedne knihovny. Zalozky se prepinaji parametrem v adrese.

    Proc parametrem a ne JavaScriptem: kazda zalozka ma pak vlastni adresu,
    takze funguje zpetne tlacitko, da se poslat odkaz a stranka se nemusi
    nacitat cela dopredu.
    """
    library_row = stats.library(library_id)
    if library_row is None:
        raise HTTPException(status_code=404, detail="Knihovna nenalezena.")

    if tab not in ("overview", "media", "activity"):
        tab = "overview"

    show_archived = bool(archived)
    context: dict[str, Any] = {
        "library": library_row,
        "tab": tab,
        "search": search or "",
        "sort": sort,
        "archived": show_archived,
        "archived_count": stats.archived_count(library_id),
        "overview": stats.library_overview(library_id),
        # Tlacitko "dopocitat technicka data" ma smysl jen pri ffprobe -
        # z Jellyfinu se data berou samy pri synchronizaci.
        "tech_source": db.get_setting("tech_source", "jellyfin"),
    }

    if tab == "overview":
        context.update(
            codecs=stats.codec_breakdown(library_id),
            resolutions=stats.resolution_breakdown(library_id),
            languages_in_library=langstats.library_languages(
                langstats.colour_map(), library_id
            ),
        )
    elif tab == "media":
        page = max(1, page)
        per_page = 48
        # Serialy se v seznamu ukazuji jako jeden radek, ne po dilech -
        # viz stats.library_rows(). Kvuli tomu se pocita i strankovani
        # ze skupin, ne z polozek.
        total = stats.library_rows_count(library_id, search, archived=show_archived)
        context.update(
            items=stats.library_rows(per_page, (page - 1) * per_page, library_id,
                                     search, sort, archived=show_archived),
            total=total,
            page=page,
            pages=max(1, (total + per_page - 1) // per_page),
        )
        # Okna „přiřadit k jiným titulům" a „smazat z archivu" - jen
        # v archivu a jen správci. Položky jednotlivě, díly zvlášť:
        # přiřazuje i maže se po souborech. Mazání má delší seznam - řádek
        # je jen zaškrtávátko, kdežto u přiřazení je v každém hledání.
        if show_archived and account.get("is_admin"):
            archiv = stats.archivovane_polozky(library_id, limit=STROP_MAZANI_ARCHIVU)
            context["k_mazani"] = archiv
            context["strop_mazani"] = STROP_MAZANI_ARCHIVU
            context["k_prirazeni"] = archiv[:STROP_PRIRAZENI_ARCHIVU]
    else:
        # Aktivita bere zvolene obdobi jako ostatni stranky (i vlastni
        # rozmezi a i zapamatovane odjinud). Drive mela napevno 90 dni
        # a nebylo to poznat - kdo mel jinde 30 dni, cetl cisla za 90.
        # Prehled a Media zustavaji bez obdobi: jsou o tom, co v knihovne
        # je, ne co se sledovalo.
        obdobi = _days(request, days, od, do)
        # Diváci a jejich poslední přehrání - jen s „…včetně kdo to sledoval".
        aktivita = stats.library_activity(
            library_id, obdobi, s_divaky=pristup.vidi_ucet(account, "knihovna_kdo"))
        context.update(days=obdobi, activity=aktivita)

    return templates.TemplateResponse(
        request, "library_detail.html", _context(request, account, **context)
    )


@router.get("/series/{series_id}", response_class=HTMLResponse)
def series_detail(
    request: Request, series_id: str, account: dict[str, Any] = Depends(require_login)
):
    """Seriál rozdělený na řady a v nich díly.

    V seznamu knihovny je seriál jeden řádek - jinak by u seriálu o deseti
    řadách zabral půl stránky a všechno ostatní by v něm zaniklo. Rozpad
    na díly je až tady, kde ho člověk doopravdy hledá.
    """
    series = stats.series_detail(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Seriál nenalezen.")

    return templates.TemplateResponse(request, "series_detail.html", _context(
        request, account,
        series=series,
        library=stats.library(series["library_id"]) if series["library_id"] else None,
    ))


@router.get("/item/{item_id}", response_class=HTMLResponse)
def item_detail(
    request: Request, item_id: str, verze: str | None = None,
    account: dict[str, Any] = Depends(require_login)
):
    """Detail jedne polozky - vcetne vsech zvukovych stop a titulku."""
    item_row = stats.item(item_id)
    if item_row is None:
        raise HTTPException(status_code=404, detail="Polozka nenalezena.")

    return templates.TemplateResponse(request, "item_detail.html", _context(
        request, account,
        item=item_row,
        # Rozsah slucuje zmereny udaj s tim, co hlasi Jellyfin - viz
        # stats.ROZSAH_CASE. Sablona nema kde to spocitat.
        rozsah=stats.rozsah_polozky(item_row),
        # Verze titulu (4K vedle 1080p). `vybrana` je ta, jejíž údaje
        # se kreslí; bez parametru je to ta, kterou Jellyfin považuje za
        # hlavní - tedy tatáž, ze které se počítají statistiky.
        # Ne `verze`: tak se v base.html jmenuje verze aplikace v patičce
        # a seznam souborů by ji přepsal - patička by ukázala jen "v".
        verze_souboru=stats.verze_polozky(item_id),
        vybrana_verze=verze,
        streams=stats.item_streams(item_id),
        # „…včetně kdo to sledoval": bez něj se přehrání nevypíšou vůbec
        # (jména, zařízení) - šablona sekci schová, data ani nedostane.
        playback=stats.item_playback(item_id) if pristup.vidi_ucet(account, "knihovna_kdo") else [],
        summary=stats.item_playback_summary(item_id),
        siblings=stats.sibling_episodes(item_row),
        lang=languages,
    ))


@router.post("/series/{series_id}/refresh")
async def series_refresh(request: Request, series_id: str,
                         account: dict[str, Any] = Depends(require_admin)):
    """Znovu načte metadata všech dílů seriálu - i s obrázky.

    Stejná akce jako u jednoho dílu, jen pro celý seriál: po opravě
    špatně určeného seriálu v Jellyfinu se mění všechny díly najednou.
    """
    vysledek = await scanner.refresh_series(series_id)
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
        return RedirectResponse(f"/series/{series_id}", status_code=303)

    zprava = _t("Metadata načtena znovu: {n} dílů").format(n=vysledek["dilu"])
    tech = vysledek.get("tech") or {}
    if tech.get("ok"):
        zprava += " " + _t("(včetně změření souborů)")
    _flash(request, zprava, "success")
    return RedirectResponse(f"/series/{series_id}", status_code=303)


@router.post("/item/{item_id}/refresh")
async def item_refresh(
    request: Request,
    item_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Znovu načte metadata jedné položky z Jellyfinu.

    Na rozdíl od synchronizace knihovny se ptáme na jediné id - kdo
    v Jellyfinu opravil rok nebo jazyk stopy, nemusí kvůli tomu čekat
    na noční průchod celou knihovnou. Z Jellyfinu se přitom jen čte.
    """
    vysledek = await scanner.refresh_item(item_id)
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    zprava = _t("Metadata načtena znovu: {nazev}").format(
        nazev=vysledek.get("name", "")).strip()
    tech = vysledek.get("tech") or {}
    if tech.get("ok"):
        zprava += " " + _t("(včetně změření souboru)")
    elif tech.get("failed"):
        zprava += " " + _t("(soubor se změřit nepodařilo, viz Log)")
    _flash(request, zprava, "success")
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@router.post("/item/{item_id}/delete")
async def item_delete(
    request: Request,
    item_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Nenavratne smaze archivovanou polozku i s jeji historii.

    Jen pro spravce a jen rucne. Automaticky se nemaze nikdy - polozka,
    ktera v Jellyfinu zmizi, se jen archivuje. Kdyz Jellyfin na chvili
    vypadne, prijdes jinak o historii kvuli docasnemu vypadku.

    Pred smazanim zaloha, stejne jako u zapomenuti divaka: kdo klikl
    na spatny titul, ma odkud se vratit. Kdyz se zaloha nepovede,
    nemaze se.
    """
    # Jen titul z archivu. Titul, který v knihovně je, by přišel
    # o historii a příští synchronizace by ho založila znovu prázdný.
    polozka = stats.item(item_id)
    if polozka is None or not polozka.get("is_missing"):
        _flash(request, "Položka neexistuje." if polozka is None
               else "Smazat jde jen titul z archivu.", "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nesmazalo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    result = stats.delete_item(item_id)
    if result.get("status") != "ok":
        _flash(request, result.get("message", "Smazání selhalo."), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    nazev = result["name"]
    if result.get("series_name"):
        nazev = f"{result['series_name']} - {nazev}"
    _flash(
        request,
        "Smazáno: {nazev} (a {n} záznamů v historii).",
        "success",
        nazev=nazev, n=result["plays"],
    )
    return RedirectResponse("/library", status_code=303)


@router.get("/archiv/hledat")
def archiv_hledat(q: str = "", account: dict[str, Any] = Depends(require_admin)):
    """Živé tituly pro výběr cíle přiřazení - JSON pro okno v prohlížeči.

    Jen pro správce, stejně jako přiřazení samo. Vrací se jen to, co
    okno ukáže: id, popis, knihovna a jméno souboru (ne celá cesta -
    ta o disku serveru prozradí víc, než výběr potřebuje).
    """
    return JSONResponse(stats.hledej_existujici(q))


@router.post("/item/{item_id}/priradit")
async def item_priradit(
    request: Request,
    item_id: str,
    cil: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Historie archivované položky přejde na vybraný titul, položka zmizí."""
    try:
        _over_prirazeni(item_id, cil)
    except stats.PrirazeniError as chyba:
        _flash(request, str(chyba), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nepřesunulo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    try:
        vysledek = await asyncio.to_thread(stats.prirad_k_jinemu, item_id, cil)
    except stats.PrirazeniError as chyba:
        _flash(request, str(chyba), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)
    _flash(request, "Přiřazeno: {z} → {na} ({n} přehrávání). Položka z archivu je smazaná.",
           "success", z=vysledek["z"], na=vysledek["na"], n=vysledek["prehravani"])
    return RedirectResponse(f"/item/{vysledek['novy_id']}", status_code=303)


@router.post("/library/{library_id}/archiv/priradit")
async def archiv_priradit(
    request: Request,
    library_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Hromadné přiřazení z okna archivu.

    Formulář nese pole `cil_<id archivované položky>`. Prázdné pole =
    ta položka se nechává být. Jedna záloha na celou dávku, ne na každý
    řádek - a před ní kontrola všech dvojic, ať se kvůli překlepu
    v jedné neudělá záloha zbytečně.
    """
    formular = await request.form()
    dvojice: list[tuple[str, str]] = []
    for klic, hodnota in formular.multi_items():
        if not klic.startswith("cil_") or not str(hodnota).strip():
            continue
        dvojice.append((klic[len("cil_"):], str(hodnota).strip()))
    zpet = f"/library/{library_id}?tab=media&archived=1"
    # Meni se az po potvrzeni rekapitulace. Tlacitko "Ano, priradit" je
    # jedine, ktere tohle pole posila - odeslani jinou cestou (Enter ve
    # formulari, stranka bez skriptu) nezmeni nic.
    if formular.get("potvrzeno") != "1":
        _flash(request, "Nic se nezměnilo. Přiřazení se provede až po potvrzení rekapitulace.",
               "info")
        return RedirectResponse(zpet, status_code=303)
    if not dvojice:
        _flash(request, "Žádné přiřazení, u žádného titulu nebyl vybraný cíl.", "info")
        return RedirectResponse(zpet, status_code=303)

    chyby: list[str] = []
    platne: list[tuple[str, str]] = []
    for stary, novy in dvojice:
        try:
            _over_prirazeni(stary, novy)
            platne.append((stary, novy))
        except stats.PrirazeniError as chyba:
            chyby.append(str(chyba))
    if not platne:
        _flash(request, "Nic se nepřiřadilo: {duvod}", "error", duvod=chyby[0])
        return RedirectResponse(zpet, status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nepřesunulo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(zpet, status_code=303)

    titulu = prehravani = 0
    for stary, novy in platne:
        try:
            vysledek = await asyncio.to_thread(stats.prirad_k_jinemu, stary, novy)
        except stats.PrirazeniError as chyba:
            chyby.append(str(chyba))
            continue
        titulu += 1
        prehravani += vysledek["prehravani"]
    _flash(request, "Přiřazeno titulů: {t}, přesunuto přehrávání: {n}. Položky z archivu jsou smazané.",
           "success" if not chyby else "warning", t=titulu, n=prehravani)
    return RedirectResponse(zpet, status_code=303)


@router.post("/library/{library_id}/archiv/smazat")
async def archiv_smazat(
    request: Request,
    library_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Hromadné smazání z okna archivu - tituly i jejich historie.

    Formulář nese pole `smazat` s id každé zaškrtnuté položky. Smaže se
    jen to, co je opravdu v archivu téhle knihovny (viz
    `stats.jen_z_archivu`). Jedna záloha na celou dávku, a jen když je
    co mazat.
    """
    # Zaškrtnout jde až STROP_MAZANI_ARCHIVU položek; Starlette jinak
    # pustí nejvýš 1000 polí a s „Vybrat vše" by formulář neprošel.
    formular = await request.form(max_fields=STROP_MAZANI_ARCHIVU + 10)
    zpet = f"/library/{library_id}?tab=media&archived=1"
    # Maže se až po potvrzení. Tlačítko „Ano, smazat" je jediné, které
    # tohle pole posílá.
    if formular.get("potvrzeno") != "1":
        _flash(request, "Nic se nesmazalo. Smazání se provede až po potvrzení.", "info")
        return RedirectResponse(zpet, status_code=303)
    vybrane = [str(h) for h in formular.getlist("smazat")]
    if not stats.jen_z_archivu(vybrane, library_id):
        _flash(request, "Nic se nesmazalo, nebyla vybraná žádná položka z archivu.", "info")
        return RedirectResponse(zpet, status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nesmazalo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(zpet, status_code=303)

    vysledek = await asyncio.to_thread(stats.smaz_z_archivu, vybrane, library_id)
    _flash(request, "Smazáno z archivu. Titulů: {t}, záznamů v historii: {n}.", "success",
           t=vysledek["titulu"], n=vysledek["prehravani"])
    return RedirectResponse(zpet, status_code=303)


def _over_prirazeni(stary_id: str, novy_id: str) -> None:
    """Kontrola dvojice ještě před zálohou - stejná pravidla jako přesun sám."""
    if not stary_id or not novy_id or stary_id == novy_id:
        raise stats.PrirazeniError("Vyber jiný titul, než je ten v archivu.")
    stary = stats.item(stary_id)
    if stary is None or not stary.get("is_missing"):
        raise stats.PrirazeniError("Přiřazovat jde jen položku z archivu.")
    novy = stats.item(novy_id)
    if novy is None or novy.get("is_missing"):
        raise stats.PrirazeniError("Cíl musí být titul, který v knihovně je.")


@router.get("/image/{item_id}")
async def item_image(
    request: Request,
    item_id: str,
    kind: str = "Primary",
    w: int = 400,
    tag: str = "",
    account: dict[str, Any] = Depends(require_login),
):
    """Obrazek polozky - stazeny z Jellyfinu a ulozeny na disk.

    Obrazky vodime pres nas server zamerne: adresa Jellyfinu ani API klic
    se tak nikdy nedostanou do stranky v prohlizeci.

    Kazdy obrazek stahujeme jen jednou. Bez teto pameti by mrizka o padesati
    dlazdicich znamenala padesat dotazu na Jellyfin pri kazdem nacteni.
    """
    if kind not in ("Primary", "Backdrop", "Thumb", "Logo"):
        kind = "Primary"
    w = max(80, min(1200, w))

    # Id z adresy se dál používá **jen v téhle prověřené podobě** - a to
    # na obou místech: v názvu souboru v mezipaměti i v dotazu do
    # Jellyfinu.
    #
    # Proč i v dotazu: id se skládá do cesty `/Items/<id>/Images/<druh>`.
    # Kdyby v něm zůstal otazník nebo lomítko, přihlášený čtenář by si
    # tou cestou mohl říct o jiný koncový bod Jellyfinu - a ten se ptá
    # naším API klíčem, tedy s právy správce. Id z Jellyfinu je vždycky
    # hexadecimální GUID, takže tenhle filtr nic platného nezahodí.
    safe_id = "".join(c for c in item_id if c.isalnum() or c in "-_")
    if not safe_id or safe_id != item_id:
        raise HTTPException(status_code=404, detail="Neplatne id.")

    # Otisk obrazku (ImageTags z Jellyfinu) je soucasti jmena souboru.
    # Diky tomu neni potreba mezipamet nijak "invalidovat": jiny obrazek
    # ma jiny otisk, tedy jinou adresu i jiny soubor. Bez toho drzel
    # Jellyscope navzdy ten prvni obrazek - i kdyz ho clovek v Jellyfinu
    # opravil, protoze spatne urcena polozka mela spatny plakat.
    safe_tag = "".join(c for c in tag if c.isalnum())[:32]

    cache_dir = config.database_path.parent / "imagecache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{safe_id}-{kind}-{w}{'-' + safe_tag if safe_tag else ''}.img"

    if cached.exists():
        return Response(
            cached.read_bytes(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    async with JellyfinClient(*db.jellyfin_connection()) as client:
        result = await client.image_bytes(safe_id, kind, w)
        # Ne každá položka má každý druh obrázku. Backdrop bývá u seriálu,
        # ne u dílu; Logo často chybí úplně. Plakát má skoro všechno, tak
        # ho vezmeme jako náhradu - lepší než prázdné šedivé místo.
        if result is None and kind != "Primary":
            result = await client.image_bytes(safe_id, "Primary", w)

    if result is None:
        raise HTTPException(status_code=404, detail="Obrazek neni k dispozici.")

    content, media_type = result
    cached.write_bytes(content)
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )
