"""The library, titles and items, refreshing from Jellyfin, the archive and images.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import access, cards, db, i18n, images, langstats, languages, scanner, stats, tasks
from ..i18n import translate as _t
from ..stats.kinds import is_video_library
from . import library_kinds
from .base import _context, _flash, require_admin, require_login, templates
from .periods import _days, _growth_disk

router = APIRouter()


# How many archived items the archive windows show (the most played
# first). With assigning every row has a search of its own, hence fewer; after
# deleting or assigning the next ones show.
ARCHIVE_ASSIGN_LIMIT = 500
ARCHIVE_DELETE_LIMIT = 1000


@router.get("/library", response_class=HTMLResponse)
def library_index(request: Request, days: int | None = None,
                  since: str | None = None, until: str | None = None, growth_disk: str | None = None,
                  account: dict[str, Any] = Depends(require_login)):
    """The signpost: the tiles of the single libraries from Jellyfin.

    The period is the same as everywhere else - one choice for the whole app,
    a custom range included. It concerns a single chart on the page (the library
    growth); the tiles above it are a state, not a period, and say so themselves.
    """
    days = _days(request, days, since, until)
    return templates.TemplateResponse(request, "library_index.html", _context(
        request, account,
        days=days,
        # Sections of the other kinds of content (music…) next to the video statistics.
        content_sections=library_kinds.summaries(account),
        libraries=stats.library_cards(),
        coverage=stats.tech_coverage(),
        codecs=stats.codec_breakdown(),
        resolutions=stats.resolution_breakdown(),
        ranges=stats.video_range_breakdown(),
        disks=stats.disks_overview(days, stats.library_growth),
        **cards.growth_data(days, _growth_disk(request, growth_disk)),
    ))


@router.get("/library/partials/growth", response_class=HTMLResponse)
def growth_partial(request: Request, days: int | None = None,
                   since: str | None = None, until: str | None = None, growth_disk: str | None = None,
                   account: dict[str, Any] = Depends(require_login)):
    """Only the "Růst knihovny" card - for the disk choice above the chart.

    A disk's chip replaces this one card (static/js/app.js), so the page stays
    where it is - it does not reload and does not scroll away. The same template as
    on the Library and in a custom dashboard. The address is under /library, so
    whoever may open the Library may open it (the area "library").
    """
    days = _days(request, days, since, until)
    return templates.TemplateResponse(request, "_card_growth.html", _context(
        request, account, **cards.growth_data(days, _growth_disk(request, growth_disk))))


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
    since: str | None = None,
    until: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """The detail of one library. The tabs are switched by a parameter in the address.

    Why a parameter and not JavaScript: every tab then has an address of its own,
    so the back button works, a link can be sent and the page need not load all
    of it in advance.
    """
    library_row = stats.library(library_id)
    if library_row is None:
        raise HTTPException(status_code=404, detail="Library not found.")
    # A library of another kind Jellyscope collects (music, books) has its own page (web/library_kinds.py);
    # a type nobody collects (photos...) has a tile, not a page.
    kind = library_kinds.KINDS.get(library_row["collection_type"] or "")
    if kind is not None:
        return kind.page(request, library_row, account)
    if not is_video_library(library_row["collection_type"]):
        raise HTTPException(status_code=404, detail="Library not found.")

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
        # The button "work out the technical data" makes sense only with ffprobe -
        # from Jellyfin the data come by themselves at synchronisation.
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
        # Series show in the list as one row, not by episodes -
        # see stats.library_rows(). Hence the paging too is counted
        # from groups, not from items.
        total = stats.library_rows_count(library_id, search, archived=show_archived)
        context.update(
            items=stats.library_rows(per_page, (page - 1) * per_page, library_id,
                                     search, sort, archived=show_archived),
            total=total,
            page=page,
            pages=max(1, (total + per_page - 1) // per_page),
        )
        # The windows "assign to other titles" and "delete from the archive" - only
        # in the archive and only for the administrator. Items one by one, episodes
        # separately: assigning and deleting go by files. Deleting has a longer list -
        # a row is only a tick box, while with assigning there is a search in each.
        if show_archived and account.get("is_admin"):
            archived_items = stats.archived_items(library_id, limit=ARCHIVE_DELETE_LIMIT)
            context["to_delete"] = archived_items
            context["delete_limit"] = ARCHIVE_DELETE_LIMIT
            context["to_assign"] = archived_items[:ARCHIVE_ASSIGN_LIMIT]
    else:
        # Activity takes the chosen period like the other pages (a custom
        # range too, also one remembered from elsewhere). It used to have 90 days
        # fixed and one could not tell - whoever had 30 days elsewhere read numbers
        # for 90. Overview and Media stay without a period: they are about what is
        # in the library, not what was watched.
        period = _days(request, days, since, until)
        # The viewers and their last play - only with "...including who watched it".
        activity = stats.library_activity(
            library_id, period, with_viewers=access.account_sees(account, "library_who"))
        context.update(days=period, activity=activity)

    return templates.TemplateResponse(
        request, "library_detail.html", _context(request, account, **context)
    )


@router.get("/series/{series_id}", response_class=HTMLResponse)
def series_detail(
    request: Request, series_id: str, account: dict[str, Any] = Depends(require_login)
):
    """A series split into seasons and episodes in them.

    In the library's list a series is one row - otherwise a series of ten
    seasons would take half a page and everything else would be lost in it. The
    split into episodes is only here, where a person really looks for it.
    """
    series = stats.series_detail(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found.")

    return templates.TemplateResponse(request, "series_detail.html", _context(
        request, account,
        series=series,
        library=stats.library(series["library_id"]) if series["library_id"] else None,
    ))


@router.get("/item/{item_id}", response_class=HTMLResponse)
def item_detail(
    request: Request, item_id: str, version: str | None = None,
    account: dict[str, Any] = Depends(require_login)
):
    """The detail of one item - with all its audio streams and subtitles."""
    item_row = stats.item(item_id)
    if item_row is None:
        raise HTTPException(status_code=404, detail="Item not found.")

    return templates.TemplateResponse(request, "item_detail.html", _context(
        request, account,
        item=item_row,
        # The range merges the measured value with what Jellyfin reports - see
        # stats.RANGE_CASE. The template has nowhere to compute it.
        dynamic_range=stats.item_range(item_row),
        # The versions of a title (4K next to 1080p). `chosen_version` is the one whose data
        # are drawn; without the parameter it is the one Jellyfin takes for the
        # main one - the same the statistics are computed from.
        # Not `version_info`: that is the name of the app's version in the footer of base.html
        # and the list of files would overwrite it - the footer would show only "v".
        file_versions=stats.item_versions(item_id),
        chosen_version=version,
        # The switch in the "File" card: versions inside the item and other copies
        # of the same movie in the same library. It switches only the file (the path,
        # the data, the streams) - the rest of the page is the title's shared record.
        files_of_item=stats.title_files(item_id, version),
        streams=stats.item_streams(item_id),
        # "...including who watched it": without it the plays are not listed at all
        # (names, devices) - the template hides the section, it does not even get the data.
        playback=stats.item_playback(item_id) if access.account_sees(account, "library_who") else [],
        summary=stats.item_playback_summary(item_id),
        siblings=stats.sibling_episodes(item_row),
        lang=languages,
    ))


@router.post("/series/{series_id}/refresh")
async def series_refresh(request: Request, series_id: str,
                         account: dict[str, Any] = Depends(require_admin)):
    """Reads the metadata of all episodes of a series again - images included.

    The same action as with one episode, only for the whole series: after fixing
    a wrongly identified series in Jellyfin all the episodes change at once.
    """
    result = await scanner.refresh_series(series_id)
    if result.get("status") != "ok":
        _flash(request, result.get("message", "It did not work."), "error")
        return RedirectResponse(f"/series/{series_id}", status_code=303)

    message = i18n.translate_plural("Metadata reloaded: {n} episodes", result["episode_count"])
    tech = result.get("tech") or {}
    if tech.get("ok"):
        message += " " + _t("(including measuring the files)")
    _flash(request, message, "success")
    return RedirectResponse(f"/series/{series_id}", status_code=303)


@router.post("/item/{item_id}/refresh")
async def item_refresh(
    request: Request,
    item_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Reads the metadata of one item from Jellyfin again.

    Unlike the library synchronisation, a single id is asked for - whoever
    fixed a year or a stream's language in Jellyfin need not wait for the nightly
    pass through the whole library. Jellyfin is only read from.
    """
    result = await scanner.refresh_item(item_id)
    if result.get("status") != "ok":
        _flash(request, result.get("message", "It did not work."), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    message = _t("Metadata reloaded: {name}").format(
        name=result.get("name", "")).strip()
    tech = result.get("tech") or {}
    if tech.get("ok"):
        message += " " + _t("(including measuring the file)")
    elif tech.get("failed"):
        message += " " + _t("(the file could not be measured, see the Log)")
    _flash(request, message, "success")
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@router.post("/item/{item_id}/delete")
async def item_delete(
    request: Request,
    item_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Irreversibly deletes an archived item with its history.

    Only for the administrator and only by hand. Nothing is ever deleted
    automatically - an item that vanishes from Jellyfin is only archived. When
    Jellyfin drops out for a while, the history would otherwise be lost over a
    temporary outage.

    A backup before deleting, the same as with forgetting a viewer: whoever clicked
    the wrong title has something to come back from. When the backup fails,
    nothing is deleted.
    """
    # Only a title from the archive. A title that is in the library would lose
    # its history and the next synchronisation would create it again empty.
    item = stats.item(item_id)
    if item is None or not item.get("is_missing"):
        _flash(request, "The item does not exist." if item is None
               else "Only a title from the archive can be deleted.", "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    backup = await tasks.backup_before_delete()
    if backup.get("status") != "ok":
        _flash(request, "Nothing was deleted because the backup failed first: {reason}",
               "error", reason=backup.get("message") or "?")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    result = stats.delete_item(item_id)
    if result.get("status") != "ok":
        _flash(request, result.get("message", "Deleting failed."), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    name = result["name"]
    if result.get("series_name"):
        name = f"{result['series_name']} - {name}"
    _flash(
        request,
        "Deleted: {name} (and {n} records from the history).",
        "success",
        name=name, n=result["plays"],
    )
    return RedirectResponse("/library", status_code=303)


@router.get("/archive/search")
def archive_search(q: str = "", account: dict[str, Any] = Depends(require_admin)):
    """Live titles for choosing the target of an assignment - JSON for the window in the browser.

    Only for the administrator, the same as the assignment itself. Only what the
    window shows is returned: the id, the label, the library and the file's name (not
    the whole path - that tells more about the server's disk than the choice needs).
    """
    return JSONResponse(stats.find_existing(q))


@router.post("/item/{item_id}/assign")
async def item_assign(
    request: Request,
    item_id: str,
    target: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """The history of an archived item moves to the chosen title, the item vanishes."""
    try:
        _check_assignment(item_id, target)
    except stats.AssignmentError as error:
        _flash(request, str(error), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    backup = await tasks.backup_before_delete()
    if backup.get("status") != "ok":
        _flash(request, "Nothing was moved because the backup failed first: {reason}",
               "error", reason=backup.get("message") or "?")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    try:
        result = await asyncio.to_thread(stats.assign_to_other, item_id, target)
    except stats.AssignmentError as error:
        _flash(request, str(error), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)
    _flash(request, "Assigned: {old} → {new} ({n} playbacks). The archived item is deleted.",
           "success", old=result["z"], new=result["to"], n=result["playbacks"])
    return RedirectResponse(f"/item/{result['new_id']}", status_code=303)


@router.post("/library/{library_id}/archive/assign")
async def archive_assign(
    request: Request,
    library_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Assigning in bulk from the archive window.

    The form carries the fields `target_<id of the archived item>`. An empty field =
    that item is left alone. One backup for the whole batch, not for every
    row - and a check of all pairs before it, so that a typo in one does not
    cause a needless backup.
    """
    form = await request.form()
    pairs: list[tuple[str, str]] = []
    for key, value in form.multi_items():
        if not key.startswith("target_") or not str(value).strip():
            continue
        pairs.append((key[len("target_"):], str(value).strip()))
    back = f"/library/{library_id}?tab=media&archived=1"
    # It changes only after the summary is confirmed. The button "Ano, priradit" is
    # the only one that sends this field - sending another way (Enter in the
    # form, a page without the script) changes nothing.
    if form.get("confirmed") != "1":
        _flash(request, "Nothing changed. The assignment happens only after the summary is confirmed.",
               "info")
        return RedirectResponse(back, status_code=303)
    if not pairs:
        _flash(request, "Nothing assigned: no title had a target chosen.", "info")
        return RedirectResponse(back, status_code=303)

    errors: list[str] = []
    valid: list[tuple[str, str]] = []
    for old, new in pairs:
        try:
            _check_assignment(old, new)
            valid.append((old, new))
        except stats.AssignmentError as error:
            errors.append(str(error))
    if not valid:
        _flash(request, "Nothing was assigned: {reason}", "error", reason=errors[0])
        return RedirectResponse(back, status_code=303)

    backup = await tasks.backup_before_delete()
    if backup.get("status") != "ok":
        _flash(request, "Nothing was moved because the backup failed first: {reason}",
               "error", reason=backup.get("message") or "?")
        return RedirectResponse(back, status_code=303)

    titles = plays = 0
    for old, new in valid:
        try:
            result = await asyncio.to_thread(stats.assign_to_other, old, new)
        except stats.AssignmentError as error:
            errors.append(str(error))
            continue
        titles += 1
        plays += result["playbacks"]
    _flash(request, "Titles assigned: {t}, playbacks moved: {n}. The archived items are deleted.",
           "success" if not errors else "warning", t=titles, n=plays)
    return RedirectResponse(back, status_code=303)


@router.post("/library/{library_id}/archive/delete")
async def archive_delete(
    request: Request,
    library_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Deleting in bulk from the archive window - titles with their history.

    The form carries the field `delete` with the id of every ticked item. Only
    what really is in this library's archive is deleted (see
    `stats.only_archived`). One backup for the whole batch, and only when there is
    something to delete.
    """
    # Up to ARCHIVE_DELETE_LIMIT items can be ticked; Starlette otherwise
    # lets at most 1000 fields through and the form would not pass with "Select all".
    form = await request.form(max_fields=ARCHIVE_DELETE_LIMIT + 10)
    back = f"/library/{library_id}?tab=media&archived=1"
    # Deleting happens only after confirming. The button "Yes, delete" is the only
    # one that sends this field.
    if form.get("confirmed") != "1":
        _flash(request, "Nothing was deleted. The deletion happens only after it is confirmed.", "info")
        return RedirectResponse(back, status_code=303)
    chosen = [str(h) for h in form.getlist("delete")]
    if not stats.only_archived(chosen, library_id):
        _flash(request, "Nothing was deleted: no archived item was selected.", "info")
        return RedirectResponse(back, status_code=303)

    backup = await tasks.backup_before_delete()
    if backup.get("status") != "ok":
        _flash(request, "Nothing was deleted because the backup failed first: {reason}",
               "error", reason=backup.get("message") or "?")
        return RedirectResponse(back, status_code=303)

    result = await asyncio.to_thread(stats.delete_from_archive, chosen, library_id)
    _flash(request, "Deleted from the archive. Titles: {t}, history records: {n}.", "success",
           t=result["title_count"], n=result["playbacks"])
    return RedirectResponse(back, status_code=303)


def _check_assignment(old_id: str, new_id: str) -> None:
    """The check of a pair before the backup - the same rules as the move itself."""
    if not old_id or not new_id or old_id == new_id:
        raise stats.AssignmentError("Choose a different title than the archived one.")
    old = stats.item(old_id)
    if old is None or not old.get("is_missing"):
        raise stats.AssignmentError("Only an archived item can be assigned.")
    new = stats.item(new_id)
    if new is None or new.get("is_missing"):
        raise stats.AssignmentError("The target has to be a title that is in the library.")


@router.get("/image/{item_id}")
async def item_image(
    request: Request,
    item_id: str,
    kind: str = "Primary",
    w: int = 400,
    tag: str = "",
    account: dict[str, Any] = Depends(require_login),
):
    """An item's image - downloaded from Jellyfin and saved to the disk.

    Images are passed through our server on purpose: neither Jellyfin's address
    nor the API key ever gets into a page in the browser.

    Every image is downloaded only once. Without this memory a grid of fifty
    tiles would mean fifty queries to Jellyfin at every load.
    """
    if kind not in ("Primary", "Backdrop", "Thumb", "Logo"):
        kind = "Primary"
    w = max(80, min(1200, w))

    # The id from the address is used further **only in this checked form** - and
    # that in both places: in the file's name in the cache and in the query to
    # Jellyfin.
    #
    # Why in the query too: the id goes into the path `/Items/<id>/Images/<kind>`.
    # Were a question mark or a slash left in it, a signed-in reader could use
    # that path to ask for another endpoint of Jellyfin - and that one is asked
    # with our API key, that is with the administrator's rights. An id from
    # Jellyfin is always a hexadecimal GUID, so this filter throws nothing valid away.
    # `isascii`: isalnum() alone lets through letters and digits outside ASCII too
    # ("é", "²"), which do not belong in a file name in the cache (images.py refuses them).
    safe_id = "".join(c for c in item_id if c.isascii() and (c.isalnum() or c in "-_"))
    if not safe_id or safe_id != item_id:
        raise HTTPException(status_code=404, detail="Invalid id.")

    # The image's tag (ImageTags from Jellyfin) is a part of the file's name.
    # So the cache need not be "invalidated" in any way: another image
    # has another tag, hence another address and another file. Without it
    # Jellyscope kept the first image for ever - even after a person fixed it in
    # Jellyfin, because a wrongly identified item had a wrong poster.
    safe_tag = "".join(c for c in tag if c.isascii() and c.isalnum())[:32]

    # Where the image lies and where it is taken from only the store knows (jellyscope/images.py):
    # from the disk, otherwise from Jellyfin - and then it is saved, so that it is downloaded only once.
    result = await images.item_image(safe_id, kind, w, safe_tag)
    if result is None:
        raise HTTPException(status_code=404, detail="The picture is not available.")

    content, media_type = result
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )
