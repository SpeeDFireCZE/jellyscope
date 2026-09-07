# -*- coding: utf-8 -*-
"""Náhradní stránka, když aplikace nemá databázi.

Bez databáze nejde udělat nic: účty, nastavení i přihlášení jsou v ní.
Dřív se v takovém případě proces ukončil - jenže v kontejneru to znamená
prázdno. Člověk otevře adresu, prohlížeč hlásí, že se nelze připojit,
a odpověď proč leží v `docker compose logs`, kam ho nikdo neposlal.

Tenhle modul postaví server, který na každou adresu odpoví jednou
stránkou: co se stalo, čím to je a co s tím. Kontejner tedy naběhne,
web odpovídá a řešení je vidět tam, kam se člověk dívá.

Vrací 503 (Service Unavailable) schválně - je to pravda, a kontrola
zdraví kontejneru tak vidí, že něco není v pořádku. Kdyby vracel 200,
`docker ps` by hlásil zdravý kontejner s rozbitou aplikací.

Celá stránka je česky i anglicky, včetně samotné hlášky: jazyk rozhraní
se vybírá v nastavení, které je uložené... v databázi. Tady se tedy
zeptat nemáme koho.
"""
from __future__ import annotations

import html

from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Route

NADPIS = "Jellyscope nemůže otevřít databázi"
NADPIS_EN = "Jellyscope cannot open its database"
UVOD = ("Aplikace běží, ale nedostane se ke svým datům. Dokud to platí, "
        "nejde se ani přihlásit - účty i nastavení jsou právě v té databázi.")
UVOD_EN = ("The application is running, but it cannot reach its data. Until "
           "that is fixed there is nothing to log in to: the accounts and the "
           "settings live in that database.")
POTOM = ("Až to spravíš, restartuj aplikaci - v Dockeru "
         "<code>docker compose restart</code>.")
POTOM_EN = ("Once that is done, restart the application - in Docker, "
            "<code>docker compose restart</code>.")

STRANKA = """<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{nadpis}</title>
<style>
  :root {{ color-scheme: dark light; }}
  body {{ margin: 0; padding: 32px 20px; background: #101318; color: #e8ecf1;
         font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; }}
  h2 {{ font-size: 15px; font-weight: 600; color: #9aa6b2; margin: 28px 0 6px;
        text-transform: uppercase; letter-spacing: .04em; }}
  p {{ margin: 10px 0; }}
  pre {{ background: #0a0d11; border: 1px solid #23303d; border-radius: 8px;
         padding: 14px 16px; overflow-x: auto; white-space: pre-wrap;
         color: #cfe3f7; }}
  code {{ background: #0a0d11; border-radius: 4px; padding: 1px 5px; }}
  .en {{ color: #9aa6b2; margin-top: 40px; padding-top: 24px;
         border-top: 1px solid #23303d; }}
  .en h1 {{ color: #9aa6b2; font-size: 17px; }}
</style>
</head>
<body>
<main>
  <h1>{nadpis}</h1>
  <p>{uvod}</p>
  <h2>Co se stalo</h2>
  <pre>{hlaska}</pre>
  <p>{potom}</p>

  <div class="en">
    <h1>{nadpis_en}</h1>
    <p>{uvod_en}</p>
    <h2>What happened</h2>
    <pre>{hlaska_en}</pre>
    <p>{potom_en}</p>
  </div>
</main>
</body>
</html>
"""


def stranka(hlaska: str, hlaska_en: str = "") -> str:
    """HTML náhradní stránky. Hlášky jsou texty z `db.DatabaseNedostupna`."""
    return STRANKA.format(
        nadpis=html.escape(NADPIS), nadpis_en=html.escape(NADPIS_EN),
        uvod=UVOD, uvod_en=UVOD_EN, potom=POTOM, potom_en=POTOM_EN,
        # Hláška nese cesty ze systému - do stránky patří jako text,
        # ne jako kus HTML.
        hlaska=html.escape(hlaska),
        hlaska_en=html.escape(hlaska_en or hlaska))


def aplikace(hlaska: str, hlaska_en: str = "") -> Starlette:
    """Server, který na cokoliv odpoví tou jednou stránkou."""
    obsah = stranka(hlaska, hlaska_en)

    async def kdekoliv(request):  # noqa: ANN001 - podpis Starlette
        return HTMLResponse(obsah, status_code=503)

    # Jedna cesta pro všechno včetně kořene: kdo přijde na /settings nebo
    # /login, má dostat tutéž odpověď - jiná stejně není.
    return Starlette(routes=[Route("/{cesta:path}", kdekoliv)])
