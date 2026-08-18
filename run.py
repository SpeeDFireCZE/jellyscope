"""Spouštěč Jellyscope.

Tohle je soubor, který spouštíš:  python run.py

Nedělá nic jiného, než že načte konfiguraci, připraví databázi a předá
řídicí otěž uvicornu — serveru, který naši webovou aplikaci obsluhuje.
"""

from __future__ import annotations

import uvicorn

from jellyscope.config import load_config


def main() -> int:
    config = load_config()

    # Databázi připravíme už tady, ne až ve web.py. Díky tomu můžeme rovnou
    # vypsat, v jakém stavu aplikace je - a případná chyba se objeví
    # v logu při startu, ne až u prvního požadavku.
    from jellyscope import accounts, db

    added = db.init_db()
    if added:
        print(f"Databáze doplněna o sloupce: {', '.join(added)}")

    database = db.database_config()
    jellyfin_url, jellyfin_key = db.jellyfin_connection()

    print(f"Jellyscope startuje na  http://{config.host}:{config.port}")
    print(f"Databáze:               {database.describe()}")

    # Adresa Jellyfinu ani API klíč nejsou v .env - nastavují se v aplikaci
    # (Nastavení -> Připojení k Jellyfinu). Nevyplněné připojení proto NENÍ
    # důvod odmítnout start: uživatel se potřebuje dostat do rozhraní,
    # aby ho mohl vyplnit. Jen na to upozorníme.
    if jellyfin_url and jellyfin_key:
        print(f"Jellyfin:               {jellyfin_url}")
    else:
        print("Jellyfin:               nenastaven "
              "- vyplň v Nastavení → Připojení k Jellyfinu")

    if accounts.any_exists():
        print(f"Účty:                   {accounts.count()} (přihlas se svým účtem)")
    else:
        print("Účty:                   žádné "
              "- při prvním otevření si založíš správce")

    # Za reverzní proxy je potřeba vědět skutečnou adresu klienta a to,
    # jestli spojení přišlo po HTTPS. Proxy to posílá v hlavičkách
    # X-Forwarded-*, ale věřit se jim smí jen tehdy, když víme, že přišly
    # od naší proxy - jinak by si je mohl podvrhnout kdokoliv.
    forwarded = config.forwarded_allow_ips
    if forwarded:
        print(f"Důvěryhodné proxy:      {forwarded}")
    if config.secure_cookies:
        print("Cookies:                jen po HTTPS (SECURE_COOKIES)")

    uvicorn.run(
        "jellyscope.web:app",
        host=config.host,
        port=config.port,
        # reload=True by se hodilo při vývoji, ale rozbíjí běh sběrače dat
        # na pozadí (spouští aplikaci dvakrát), takže ho tu necháváme vypnutý.
        reload=False,
        log_level="info",
        proxy_headers=bool(forwarded),
        forwarded_allow_ips=forwarded or None,
        # Bez barevných escape sekvencí - v logu supervisord by jen překážely.
        use_colors=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
