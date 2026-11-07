"""Uživatelé: seznam a detail.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import pristup, stats
from .zaklad import _context, require_login, templates
from .obdobi import _days

router = APIRouter()


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, days: int | None = None,
          od: str | None = None, do: str | None = None,
          account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, od, do)
    return templates.TemplateResponse(request, "users.html", _context(
        request, account,
        days=days,
        rows=stats.user_table(days),
    ))


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(
    request: Request, user_id: str, days: int | None = None,
    od: str | None = None, do: str | None = None,
    account: dict[str, Any] = Depends(require_login)
):
    days = _days(request, days, od, do)
    # Se skrytymi jmeny vede odkaz na nahradni id (skryto-2) a skutecne
    # id ciziho divaka nenajde nic - viz pristup.divak_z_adresy.
    detail = stats.user_detail(pristup.divak_z_adresy(user_id, account), days)
    if not detail:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen")

    return templates.TemplateResponse(request, "user_detail.html", _context(
        request, account, days=days, **detail
    ))
