"""Synchronizace knihovny a technicka analyza souboru.

Dve ulohy, ktere se poustej rucne z Nastaveni nebo obcas samy:

1. `sync_library()` - stahne z Jellyfinu seznam uzivatelu, knihoven a polozek.
2. `run_tech_scan()` - doplni k polozkam technicke udaje (kodek, bitrate, ...).

U druhe ulohy si uzivatel v nastaveni vybira zdroj:
  * "jellyfin" - udaje, ktere uz zna Jellyfin. Rychle, funguje vzdy, mene presne.
  * "ffprobe"  - cteni souboru na disku. Presne, ale potrebuje pristup k souborum.

Balík podle oblastí (jellyscope/scanner/). Zvenku se volá jako dřív
(`scanner.sync_library()`, `scanner.is_scan_running()`): tenhle soubor
vystaví všechno, co dřív bylo v jednom scanner.py.

* `typy` - Druhy položek, které Jellyscope vede (film, díl) a obaly, které soubor nemají.
* `beh` - Běh úloh: zámek, zastavení, průběh a záznam o úloze (scan_log).
* `misto` - Volné místo na disku (z Jellyfinu nebo z kapacity) a denní snímek knihovny.
* `slouceni` - Sloučení podle TMDB, dvojníci nových položek, přenos historie, archiv.
* `zapis` - Zápis položek, verzí, obrázků a stop; sloučení archivu do živých; úklid fantomů.
* `synchronizace` - Synchronizace celé knihovny: uživatelé, knihovny a položky z Jellyfinu.
* `obnova` - Obnova jedné položky a seriálu, nedávné změny a jejich revize.
* `jazyky` - Doplnění jazyků stop z Jellyfinu a z názvů souborů.
* `technika` - Technická analýza souborů (ffprobe nebo Jellyfin) a dynamický rozsah.

Testy, které jméno podvrhují a čekají účinek uvnitř balíku, používají
podvrhni() z tests/_balik.py - podvrhne jméno ve všech podmodulech
(tests/test_scanner_balik.py, kontrola e).
"""
from __future__ import annotations

from ..jellyfin import JellyfinClient  # noqa: F401 - testy ho podvrhují jako scanner.JellyfinClient
from .typy import (
    OBALY,
    SPRAVOVANE_TYPY,
    ZAPISOVANE_TYPY,
    ZATIM_NEZARAZENY,
    je_obal,
)
# _stop_requested (příznak Zastavit) se ven nevystavuje: je to bool a import
# by udělal kopii, která by se nikdy nezměnila. Ptá se na něj stop_requested().
from .beh import (
    ZAZNAMU_NA_DRUH,
    _add_progress,
    _clear_progress,
    _clear_stop,
    _progress,
    _proredit_zaznamy,
    _scan_lock,
    _start_progress,
    finish_task_log,
    is_scan_running,
    last_scan,
    library_version,
    otisky,
    progress,
    request_stop,
    start_task_log,
    stop_requested,
    tasks_version,
    zamek_uloh,
)
from .misto import (
    JF_CELKEM_KLIC,
    JF_VOLNE_KLIC,
    KAPACITA_KLIC,
    ZDROJ_KLIC,
    _cislo,
    _volne_misto_knihovny,
    _volne_z_kapacity,
    misto_z_jellyfinu,
    zapamatuj_misto_z_jellyfinu,
    zapis_snimek,
)
from .slouceni import (
    _merge_by_tmdb,
    _podle_nazvu,
    _podle_tmdb,
    _rovno_nebo_nic,
    _stare_ke_slouceni,
    _vyber,
    do_archivu,
    dvojnici_novych,
    prenes_historii,
)
from .zapis import (
    _SLOUPEC_ID,
    _SLOUPEC_OTISK,
    _SLOUPEC_OTISK_SERIALU,
    _SLOUPEC_SERIAL,
    _mark_missing,
    _polozky_s_jinym_obrazkem,
    _write_batch,
    _write_items,
    _zapomen_obrazky,
    save_streams,
    slouc_archiv_do_zivych,
    uklid_fantomu,
    zapis_verze,
)
from .synchronizace import (
    _ktere_uz_zname,
    _radek_polozky,
    _sync_items_of_library,
    _sync_libraries,
    _sync_users,
    _uloz_nove_polozky,
    identita_polozky,
    sync_library,
    tmdb_id_of,
)
from .obnova import (
    RECENT_OVERLAP_MINUTES,
    REVIZE_STROP,
    _naslednici_podle_cesty,
    _posledni_pridano,
    _reviduj_nedavne,
    _zprava_nedavnych,
    refresh_item,
    refresh_series,
    srovnej_po_novych,
    sync_recent,
)
from .jazyky import (
    _doplnit_jazyky_polozky,
    _doplnit_z_nazvu_polozky,
    _prepocitej_jazyky_polozky,
    _rozdelit_podle_nazvu,
    _sparuj_stopy,
    doplnit_jazyky_z_jellyfinu,
    doplnit_jazyky_z_nazvu,
    kandidati_na_jazyk_z_nazvu,
)
from .technika import (
    _chybi_jazyk,
    _save_probe_result,
    _save_tech,
    _save_tech_error,
    doplnit_rozsah_z_jellyfinu,
    kandidati_na_rozsah_z_jellyfinu,
    run_tech_scan,
)

__all__ = [
    "JF_CELKEM_KLIC",
    "JF_VOLNE_KLIC",
    "JellyfinClient",
    "KAPACITA_KLIC",
    "OBALY",
    "RECENT_OVERLAP_MINUTES",
    "REVIZE_STROP",
    "SPRAVOVANE_TYPY",
    "ZAPISOVANE_TYPY",
    "ZATIM_NEZARAZENY",
    "ZAZNAMU_NA_DRUH",
    "ZDROJ_KLIC",
    "_SLOUPEC_ID",
    "_SLOUPEC_OTISK",
    "_SLOUPEC_OTISK_SERIALU",
    "_SLOUPEC_SERIAL",
    "_add_progress",
    "_chybi_jazyk",
    "_cislo",
    "_clear_progress",
    "_clear_stop",
    "_doplnit_jazyky_polozky",
    "_doplnit_z_nazvu_polozky",
    "_ktere_uz_zname",
    "_mark_missing",
    "_merge_by_tmdb",
    "_naslednici_podle_cesty",
    "_podle_nazvu",
    "_podle_tmdb",
    "_polozky_s_jinym_obrazkem",
    "_posledni_pridano",
    "_prepocitej_jazyky_polozky",
    "_progress",
    "_proredit_zaznamy",
    "_radek_polozky",
    "_reviduj_nedavne",
    "_rovno_nebo_nic",
    "_rozdelit_podle_nazvu",
    "_save_probe_result",
    "_save_tech",
    "_save_tech_error",
    "_scan_lock",
    "_sparuj_stopy",
    "_stare_ke_slouceni",
    "_start_progress",
    "_sync_items_of_library",
    "_sync_libraries",
    "_sync_users",
    "_uloz_nove_polozky",
    "_volne_misto_knihovny",
    "_volne_z_kapacity",
    "_vyber",
    "_write_batch",
    "_write_items",
    "_zapomen_obrazky",
    "_zprava_nedavnych",
    "do_archivu",
    "doplnit_jazyky_z_jellyfinu",
    "doplnit_jazyky_z_nazvu",
    "doplnit_rozsah_z_jellyfinu",
    "dvojnici_novych",
    "finish_task_log",
    "identita_polozky",
    "is_scan_running",
    "je_obal",
    "kandidati_na_jazyk_z_nazvu",
    "kandidati_na_rozsah_z_jellyfinu",
    "last_scan",
    "library_version",
    "misto_z_jellyfinu",
    "otisky",
    "prenes_historii",
    "progress",
    "refresh_item",
    "refresh_series",
    "request_stop",
    "run_tech_scan",
    "save_streams",
    "slouc_archiv_do_zivych",
    "srovnej_po_novych",
    "start_task_log",
    "stop_requested",
    "sync_library",
    "sync_recent",
    "tasks_version",
    "tmdb_id_of",
    "uklid_fantomu",
    "zamek_uloh",
    "zapamatuj_misto_z_jellyfinu",
    "zapis_snimek",
    "zapis_verze",
]


# Planovani techto uloh uz neni tady, ale v tasks.py - spolecne pro vsechny
# naplanovane cinnosti. Tenhle soubor umi ulohy udelat; kdy se maji delat,
# rozhoduje nekdo jiny. Je to same rozdeleni jako "co" versus "kdy" u budiku.
