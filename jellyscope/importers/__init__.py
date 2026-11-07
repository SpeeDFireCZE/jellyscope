"""Import historie z jinych nastroju.

Jellyscope zaznamenava prehravani az od chvile, kdy bezi. Kdyz uz nekde
historii mas, nema smysl zacinat od nuly - da se prevzit.

Podporujeme dva zdroje:

1. **Playback Reporting** - oficialni plugin Jellyfinu. Data drzi ve vlastni
   tabulce a nabizi je pres API, kteremu se da poslat SQL dotaz. Nemusime
   tedy sahat na zadny soubor; staci se zeptat Jellyfinu, jako u vseho
   ostatniho. (Stejnou cestou to resi i Jellystat.)

2. **Jellystat** - jeho zaloha se da vyexportovat do JSON. Soubor nahrajes
   ve formulari a my si z nej vytahneme tabulku prehravani.

## Jak se resi opakovany import

Duplicity se hlidaji **dvakrat**, protoze jedna pojistka nestaci.

**1. Klic zaznamu.** Kazdy prevzaty radek dostane klic, ktery ho popisuje
u zdroje - `import:pbr:<rowid>:<polozka>` nebo `import:jst:<id>:<polozka>`.
Pred vlozenim se overi, ze takovy klic uz v databazi neni. Diky tomu je
import **idempotentni**: tentyz soubor muzes nahrat desetkrat a nic se
nezdvoji.

**2. Shoda podle obsahu.** Klic ale plati jen v ramci jednoho zdroje.
Tataz historie prectena z Jellystatu a z Playback Reportingu ma dva ruzne
klice, takze by se cela zdvojila - a vlastni zaznamy sberace nemaji klic
z importu vubec. Proto se navic porovnava obsah: stejny uzivatel, stejna
polozka a **prekryvajici se cas** znamena tentyz zaznam.

Prekryv, a ne shoda casu zacatku - kazdy nastroj si zapisuje cas trochu
jinak a presna shoda by vetsinu duplicit propasla. Viz `_uz_tam_je()`.

## Balík podle oblastí

Zvenku se volá jako dřív (`importers.narovnej_data()`,
`importers.import_jellystat_json(...)`): tenhle soubor vystaví všechno,
co dřív bylo v jednom importers.py (jellyscope/importers/):

* `zaklad` - Společné pomocníky importu: čas, klíče záznamů, pojistka „do Jellyfinu jen čteme", sestavení a zápis řádku, rozbor názvu epizody.
* `shoda` - Druhá pojistka proti duplicitám: shoda podle obsahu (uživatel, položka, překryv času).
* `zalozeni` - Založení chybějících položek z Jellyfinu (díly pod seriálem, knihovny podle cest).
* `propojeni` - Přiřazení importované historie k položkám: id, TMDB, název, číslo dílu.
* `pbr` - Import z Playback Reporting (plugin Jellyfinu) - přes API i ze souboru TSV.
* `jellystat` - Import ze zálohy Jellystatu (JSON).
* `uklid` - Úklid duplicit ve vlastní historii a zkrácení nesmyslných délek.
* `dvojice` - Duplicity mezi dvěma zdroji importu a díly u špatného seriálu.
* `osirele` - Záznamy bez položky: počty, zastaralé názvy, dohledání v Jellyfinu, rozbor a ruční přiřazení.
* `narovnani` - Narovnání dat (všechny opravy za sebou) a souhrn importu.

Testy, které jméno podvrhují a čekají účinek uvnitř balíku, používají
podvrhni() z tests/_balik.py (tests/test_importers_balik.py, kontrola e).
"""
from __future__ import annotations

from ..jellyfin import JellyfinClient  # noqa: F401 - testy ho podvrhují jako importers.JellyfinClient
from .zaklad import (
    ImportError_,
    JAZYKOVE_SLOUPCE,
    ZAKAZANA_SLOVA,
    _NAZEV_EPIZODY,
    _build_row,
    _existing_keys,
    _insert,
    _jazyk_ze_zdroje,
    _normalizuj_id,
    _parse_time,
    jen_cteni,
    rozbor_nazvu,
)
from .shoda import (
    _epocha,
    _epocha_pomalu,
    _index_prehravani,
    _known_items,
    _known_users,
    _uz_tam_je,
    _zapamatuj,
)
from .zalozeni import (
    _dil_v_knihovne,
    _dily_pod_serialem,
    _knihovna_pro,
    _knihovny_podle_cest,
    zaloz_z_jellyfinu,
)
from .propojeni import (
    _link_by_episode_number,
    _link_by_name,
    _link_by_tmdb,
    _orphan_item_ids,
    _osirele_zaznamy,
    _relink,
    _sjednot_identifikatory,
    link_imported_history,
    refresh_playback_metadata,
)
from .pbr import (
    PBR_QUERY,
    PBR_QUERY_BEZ_ROWID,
    PBR_TSV_SLOUPCE,
    _tsv_sloupce,
    _zpracuj_pbr,
    import_playback_reporting,
    import_playback_reporting_tsv,
    playback_reporting_available,
)
from .jellystat import (
    JELLYSTAT_TABLES,
    _find_jellystat_rows,
    _jellystat_item_type,
    _pick,
    import_jellystat_json,
)
from .uklid import (
    STROP_NASOBEK,
    duplicate_playback_count,
    duplicate_playback_groups,
    merge_duplicate_playback,
    zkrat_nesmyslne_delky,
)
from .dvojice import (
    DELKA_TOLERANCE_S,
    ROZESTUP_MAX_S,
    _existuje_import,
    _patri_k_sobe,
    _titul_pro_srovnani,
    _uplnost,
    import_duplicate_count,
    import_duplicate_groups,
    merge_import_duplicates,
    misplaced_episode_rows,
)
from .osirele import (
    DOHLEDAT_NEJVYS,
    DUVODY_POPIS,
    DUVOD_ID,
    DUVOD_JEDNA_SHODA,
    DUVOD_NIC,
    DUVOD_SERIAL_JE,
    DUVOD_SERIAL_NENI,
    DUVOD_VIC_SHOD,
    DUVOD_ZNA_SERIAL,
    ROZBOR_NEJVYS,
    dohledej_osirele_v_jellyfinu,
    kandidati_pro_osireleho,
    orphan_items_count,
    orphan_playback_count,
    prirad_rucne,
    relink_orphans,
    repair_episode_links,
    rozbor_osirelych,
    sjednot_nazvy,
    stale_name_rows,
)
from .narovnani import (
    import_summary,
    narovnej_data,
)

__all__ = [
    "DELKA_TOLERANCE_S",
    "DOHLEDAT_NEJVYS",
    "DUVODY_POPIS",
    "DUVOD_ID",
    "DUVOD_JEDNA_SHODA",
    "DUVOD_NIC",
    "DUVOD_SERIAL_JE",
    "DUVOD_SERIAL_NENI",
    "DUVOD_VIC_SHOD",
    "DUVOD_ZNA_SERIAL",
    "ImportError_",
    "JAZYKOVE_SLOUPCE",
    "JELLYSTAT_TABLES",
    "JellyfinClient",
    "PBR_QUERY",
    "PBR_QUERY_BEZ_ROWID",
    "PBR_TSV_SLOUPCE",
    "ROZBOR_NEJVYS",
    "ROZESTUP_MAX_S",
    "STROP_NASOBEK",
    "ZAKAZANA_SLOVA",
    "_NAZEV_EPIZODY",
    "_build_row",
    "_dil_v_knihovne",
    "_dily_pod_serialem",
    "_epocha",
    "_epocha_pomalu",
    "_existing_keys",
    "_existuje_import",
    "_find_jellystat_rows",
    "_index_prehravani",
    "_insert",
    "_jazyk_ze_zdroje",
    "_jellystat_item_type",
    "_knihovna_pro",
    "_knihovny_podle_cest",
    "_known_items",
    "_known_users",
    "_link_by_episode_number",
    "_link_by_name",
    "_link_by_tmdb",
    "_normalizuj_id",
    "_orphan_item_ids",
    "_osirele_zaznamy",
    "_parse_time",
    "_patri_k_sobe",
    "_pick",
    "_relink",
    "_sjednot_identifikatory",
    "_titul_pro_srovnani",
    "_tsv_sloupce",
    "_uplnost",
    "_uz_tam_je",
    "_zapamatuj",
    "_zpracuj_pbr",
    "dohledej_osirele_v_jellyfinu",
    "duplicate_playback_count",
    "duplicate_playback_groups",
    "import_duplicate_count",
    "import_duplicate_groups",
    "import_jellystat_json",
    "import_playback_reporting",
    "import_playback_reporting_tsv",
    "import_summary",
    "jen_cteni",
    "kandidati_pro_osireleho",
    "link_imported_history",
    "merge_duplicate_playback",
    "merge_import_duplicates",
    "misplaced_episode_rows",
    "narovnej_data",
    "orphan_items_count",
    "orphan_playback_count",
    "playback_reporting_available",
    "prirad_rucne",
    "refresh_playback_metadata",
    "relink_orphans",
    "repair_episode_links",
    "rozbor_nazvu",
    "rozbor_osirelych",
    "sjednot_nazvy",
    "stale_name_rows",
    "zaloz_z_jellyfinu",
    "zkrat_nesmyslne_delky",
]
