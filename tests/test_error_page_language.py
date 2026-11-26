# -*- coding: utf-8 -*-
r"""The error page speaks the reader's language.

A route that does not find what it was asked for answers 404 with a reason
(`HTTPException(detail=...)`), and error.html shows it. The reason used to be a
finished Czech sentence, never looked up in the dictionary - an English or a
Spanish reader got "Knihovna nenalezena." Since the source language is English
the reason is an English key, translated when the page is drawn.

Run:
    .venv/bin/python tests/test_error_page_language.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
HOME = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=HOME, DATABASE_PATH=str(Path(HOME) / "e.db"), SECRET_KEY="error-page",
                  JELLYFIN_URL="http://127.0.0.1:1", JELLYFIN_API_KEY="x", JELLYSCOPE_DEMO="0")

from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("error_admin", "error-page-pw", is_admin=True)
accounts.create("error_reader", "error-page-pw")


def page(user: str, language: str, path: str) -> tuple[int, str]:
    db.set_setting("ui_language", language)
    client = TestClient(web.app)
    client.post("/login", data={"username": user, "password": "error-page-pw", "method": "local"})
    answer = client.get(path)
    return answer.status_code, answer.text


print("--- a reason of a 404 in the reader's language ---")
for language, sentence in (("cs", "Knihovna nenalezena."), ("en", "Library not found."),
                           ("es", "Biblioteca no encontrada."),
                           # German is the contributor's file (PR #23): a sentence he has not translated yet
                           # shows in English, the source - never Czech.
                           ("de", "Library not found.")):
    code, text = page("error_admin", language, "/library/no-such-library")
    check(code == 404 and sentence in text, f"{language}: {sentence} ({code})")
code, text = page("error_admin", "en", "/library/no-such-library")
check("Knihovna" not in text, "and no Czech word on the English page")

print()
print("--- a 404 without a reason, a 403 ---")
code, text = page("error_admin", "en", "/no-such-page-at-all")
check(code == 404 and "Page not found." in text, f"a page that does not exist ({code})")
code, text = page("error_admin", "cs", "/no-such-page-at-all")
check(code == 404 and "Stránka nenalezena." in text and "Not Found" not in text,
      "in Czech too, not Starlette's English default")
code, text = page("error_reader", "en", "/users/no-such-user")
check(code in (403, 404) and "Uzivatel" not in text and "admin" not in text,
      f"a reader's error page has no Czech either ({code})")

print()
print("--- a malformed number in a page address (audit QW-14) ---")
# A page parameter that is no number (an edited bookmark, a hand-typed address) used to answer with
# FastAPI's raw JSON 422 - a browser showed {"detail":[{"type":"int_parsing",...}]}. A page gets the
# app's error page in the reader's language; the API keeps its JSON.
SENTENCES = {"en": "A value in the address or the form is not valid.",
             "cs": "Hodnota v adrese nebo ve formuláři není platná."}
for language, path in (("en", "/users?days=abc"), ("cs", "/history?page="), ("en", "/network?days=1.5"),
                       ("cs", "/settings?log_lines=x"), ("en", "/arr/sonarr/poster/x")):
    code, text = page("error_admin", language, path)
    check(code == 400 and SENTENCES[language] in text and "int_parsing" not in text and "<html" in text,
          f"{language} {path}: the app's error page, not a JSON dump ({code}, {text[:80]!r})")
handler = web.app.exception_handlers.get(RequestValidationError)
error = RequestValidationError([{"type": "int_parsing", "loc": ("query", "days"), "msg": "x", "input": "abc"}])
api_answer = asyncio.run(handler(Request({"type": "http", "method": "GET", "path": "/api/v1/summary", "headers": [],
                                          "query_string": b"", "app": web.app}), error))
check(api_answer.status_code == 422 and json.loads(api_answer.body)["detail"][0]["type"] == "int_parsing",
      f"the API keeps the JSON answer ({api_answer.status_code})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
