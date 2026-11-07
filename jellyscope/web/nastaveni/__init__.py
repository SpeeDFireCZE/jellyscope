"""Routy Nastavení - všech šestačtyřicet.

Bydlí zvlášť proto, že jich je skoro třetina celého webu a s ostatními
stránkami nemají nic společného. Kdo hledá, proč se něco neuložilo, má
hledat tady, a ne mezi grafy.

Používá se `APIRouter`, ne `@app.post(...)`. Modul tím nemusí vůbec vědět
o `app` - `web/__init__.py` si router jen vyzvedne a připojí. Bez toho by se oba
moduly musely importovat navzájem.

Co potřebuje každá stránka (šablony, kontext, hlášky, závory přihlášení),
je v `zaklad.py`.

Podbalík podle oblastí (jellyscope/web/nastaveni/). Každý soubor s routami
má svůj router; tenhle soubor je spojí do jednoho `router`, který web
připojí (web/__init__.py), a vystaví všechna jména jako dřív
(`web_nastaveni._stranka_aktualizace`, `web_nastaveni.MAX_UPLOAD_MB`…):

* `spolecne` - Společné pomocníky Nastavení: rozepsané nastavení databáze, strop nahrávání, časové zóny, drobnosti.
* `stranka` - Stránka Nastavení (všechny sekce) a uložení obecného formuláře.
* `ucty` - Účty, práva, čtenáři, diváci, blokace adres a API klíče.
* `pripojeni` - Připojení k Jellyfinu a k databázi.
* `upozorneni` - Upozornění: kanály, události, texty, zkušební zpráva.
* `rozhrani` - Rozhraní, kontrola aktualizací a jazyk.
* `aktualizace` - Aktualizace, restart a zálohy (stažení, smazání, obnova).
* `ulohy` - Synchronizace, zastavení, technický sken, plánované úlohy a log.
* `historie` - Import historie, narovnání dat, sirotci, zapomenutí diváka.

Testy, které jméno podvrhují a čekají účinek uvnitř podbalíku, používají
podvrhni() z tests/_balik.py (tests/test_nastaveni_balik.py, kontrola e).
"""
from __future__ import annotations

from fastapi import APIRouter

from ...jellyfin import JellyfinClient  # noqa: F401 - testy ho podvrhují jako web_nastaveni.JellyfinClient
from .spolecne import (
    CASTE_ZONY,
    MAX_UPLOAD_MB,
    _DB_DRAFT,
    _DRAFT_TTL_SECONDS,
    _dny_v_tydnu,
    _draft_clear,
    _draft_read,
    _draft_save,
    _generace_nesedi,
    _import_ze_souboru,
    _kapacita_do_formulare,
    _nacti_zalohu,
    _pocty_uklidu,
    _uloz_tajemstvi,
    _zona_existuje,
)
from .stranka import (
    settings_page,
    settings_save,
)
from .ucty import (
    VOLBY_KLICE,
    _prava_klice_z_formulare,
    _prava_root_klice,
    _strom_klice,
    _uloz_strom,
    account_create,
    account_delete,
    account_password,
    account_role,
    api_token_novy,
    api_token_prava,
    api_token_vypnout,
    api_token_zapnout,
    api_token_zrusit,
    blocks_permanent,
    blocks_unblock,
    settings_ctenari,
    settings_divaci,
    settings_klice,
    settings_prava_uctu,
)
from .pripojeni import (
    settings_connection,
    settings_database,
)
from .upozorneni import (
    settings_notification_events,
    settings_notification_test,
    settings_notification_texts,
    settings_notifications,
)
from .rozhrani import (
    settings_interface,
    settings_language,
    settings_updates,
)
from .aktualizace import (
    _naplanuj_restart,
    _stranka_aktualizace,
    backup_delete,
    backup_download,
    backup_restore,
    settings_restart,
    settings_update,
)
from .ulohy import (
    log_stahnout,
    settings_scan,
    settings_stop,
    settings_sync,
    tasks_run,
    tasks_save,
)
from .historie import (
    historie_vrat,
    historie_zapomen,
    history_assign,
    history_orphans,
    history_tidy,
    import_detect,
    import_jellystat,
    import_playback_reporting,
    import_playback_reporting_file,
)
from . import (aktualizace, historie, pripojeni, rozhrani, stranka, ucty, ulohy, upozorneni)

# Jeden router pro celé Nastavení - web/__init__.py ho připojí. Na pořadí
# nezáleží (žádné dvě routy nemají stejnou adresu se stejnou metodou).
router = APIRouter()
router.include_router(stranka.router)
router.include_router(ulohy.router)
router.include_router(ucty.router)
router.include_router(pripojeni.router)
router.include_router(upozorneni.router)
router.include_router(rozhrani.router)
router.include_router(aktualizace.router)
router.include_router(historie.router)

__all__ = [
    "CASTE_ZONY",
    "JellyfinClient",
    "MAX_UPLOAD_MB",
    "VOLBY_KLICE",
    "_DB_DRAFT",
    "_DRAFT_TTL_SECONDS",
    "_dny_v_tydnu",
    "_draft_clear",
    "_draft_read",
    "_draft_save",
    "_generace_nesedi",
    "_import_ze_souboru",
    "_kapacita_do_formulare",
    "_nacti_zalohu",
    "_naplanuj_restart",
    "_pocty_uklidu",
    "_prava_klice_z_formulare",
    "_prava_root_klice",
    "_stranka_aktualizace",
    "_strom_klice",
    "_uloz_strom",
    "_uloz_tajemstvi",
    "_zona_existuje",
    "account_create",
    "account_delete",
    "account_password",
    "account_role",
    "api_token_novy",
    "api_token_prava",
    "api_token_vypnout",
    "api_token_zapnout",
    "api_token_zrusit",
    "backup_delete",
    "backup_download",
    "backup_restore",
    "blocks_permanent",
    "blocks_unblock",
    "historie_vrat",
    "historie_zapomen",
    "history_assign",
    "history_orphans",
    "history_tidy",
    "import_detect",
    "import_jellystat",
    "import_playback_reporting",
    "import_playback_reporting_file",
    "log_stahnout",
    "router",
    "settings_connection",
    "settings_ctenari",
    "settings_database",
    "settings_divaci",
    "settings_interface",
    "settings_klice",
    "settings_language",
    "settings_notification_events",
    "settings_notification_test",
    "settings_notification_texts",
    "settings_notifications",
    "settings_page",
    "settings_prava_uctu",
    "settings_restart",
    "settings_save",
    "settings_scan",
    "settings_stop",
    "settings_sync",
    "settings_update",
    "settings_updates",
    "tasks_run",
    "tasks_save",
]
