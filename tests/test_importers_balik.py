r"""Pravidla balíku importers/ - importers.py rozdělený podle oblastí (přestavba 3d).

`jellyscope/importers.py` měl 2 568 řádků: import historie z Playback
Reporting a Jellystatu, propojení importovaných záznamů s položkami,
pojistky proti duplicitám, úklid historie, záznamy bez položky a celé
narovnání dat. Je z něj balík `jellyscope/importers/` se vstupními
dveřmi (`importers/__init__.py`), takže zvenku se nemění nic. Test hlídá:

(a) vstupní dveře mají všechno, co se zvenku čte jako `importers.X`
    nebo `from jellyscope.importers import X`;
(b) každé jméno bydlí ve svém podmodulu a dveře ukazují na tentýž objekt;
(c) bez kruhů: závislosti jedním směrem, žádný `from . import X`, který
    by mířil do balíku, každý podmodul (i partneři v kruhu importů -
    scanner, tasks) jde naimportovat jako první v čistém procesu;
(d) žádný podmodul nad 600 řádků;
(e) testy podvrhují tam, kde to zasáhne (tests/_balik.py);
(f) starý importers.py není a žádný test ho nečte podle cesty (pojistka
    „do Jellyfinu jen čteme" v test_readonly musí vidět celý import);
(g) komentáře se při přesunu neztratily - srovnané po jednom;
(h) logger se jmenuje jako dřív (`jellyscope.importers`).

Spec: docs/superpowers/specs/2026-11-06-importers-balik-design.md

Spuštění:
    .venv/bin/python tests/test_importers_balik.py
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "importers_balik.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _balik import podvrhy_v_podmodulech, spatne_podvrhy  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


BALIK = PROJECT / "jellyscope" / "importers"
STARY = PROJECT / "jellyscope" / "importers.py"
S_UKAZKAMI = {Path(__file__).name, "test_stats_balik.py", "test_web_balik.py", "test_scanner_balik.py", "_balik.py"}

# Mapa jmen nejvyšší úrovně importers.py -> podmodul (spec, oddíl Rozdělení).
PODMODULY: dict[str, list[str]] = {
    "zaklad": ["ImportError_", "_parse_time", "_existing_keys", "ZAKAZANA_SLOVA", "jen_cteni", "_normalizuj_id",
               "_insert", "JAZYKOVE_SLOUPCE", "_jazyk_ze_zdroje", "_build_row", "_NAZEV_EPIZODY", "rozbor_nazvu"],
    "shoda": ["_epocha_pomalu", "_epocha", "_index_prehravani", "_uz_tam_je", "_zapamatuj", "_known_users",
              "_known_items"],
    "zalozeni": ["zaloz_z_jellyfinu", "_dily_pod_serialem", "_knihovny_podle_cest", "_knihovna_pro",
                 "_dil_v_knihovne"],
    "propojeni": ["_sjednot_identifikatory", "refresh_playback_metadata", "_orphan_item_ids", "_relink",
                  "_link_by_tmdb", "_osirele_zaznamy", "_link_by_name", "_link_by_episode_number",
                  "link_imported_history"],
    "pbr": ["PBR_QUERY", "PBR_QUERY_BEZ_ROWID", "playback_reporting_available", "import_playback_reporting",
            "PBR_TSV_SLOUPCE", "_tsv_sloupce", "import_playback_reporting_tsv", "_zpracuj_pbr"],
    "jellystat": ["_jellystat_item_type", "JELLYSTAT_TABLES", "_find_jellystat_rows", "_pick", "import_jellystat_json"],
    "uklid": ["STROP_NASOBEK", "zkrat_nesmyslne_delky", "duplicate_playback_groups", "merge_duplicate_playback",
              "duplicate_playback_count"],
    "dvojice": ["DELKA_TOLERANCE_S", "ROZESTUP_MAX_S", "_titul_pro_srovnani", "_existuje_import",
                "import_duplicate_groups", "_patri_k_sobe", "_uplnost", "merge_import_duplicates",
                "import_duplicate_count", "misplaced_episode_rows"],
    "osirele": ["orphan_playback_count", "stale_name_rows", "sjednot_nazvy", "orphan_items_count", "DOHLEDAT_NEJVYS",
                "dohledej_osirele_v_jellyfinu", "DUVOD_ID", "DUVOD_JEDNA_SHODA", "DUVOD_VIC_SHOD",
                "DUVOD_SERIAL_JE", "DUVOD_SERIAL_NENI", "DUVOD_ZNA_SERIAL", "DUVOD_NIC", "DUVODY_POPIS",
                "ROZBOR_NEJVYS", "rozbor_osirelych", "kandidati_pro_osireleho", "prirad_rucne", "relink_orphans",
                "repair_episode_links"],
    "narovnani": ["narovnej_data", "import_summary"],
}
SOUBORY = list(PODMODULY)
# Graf závislostí podle specu: kdo smí na koho sahat.
SMI = {
    "zaklad": set(), "shoda": {"zaklad"}, "zalozeni": {"zaklad"}, "propojeni": {"zaklad", "zalozeni"},
    "pbr": {"zaklad", "shoda", "propojeni"}, "jellystat": {"zaklad", "shoda", "propojeni"},
    "uklid": {"zaklad", "shoda"}, "dvojice": {"zaklad", "shoda"},
    "osirele": {"zaklad", "zalozeni", "propojeni", "dvojice"}, "narovnani": {"propojeni", "uklid", "dvojice", "osirele"},
}

# --- dnešní stav (vygenerovaný z importers.py 6. 11. 2026) -----------------------------
PUVODNI = [
    'PBR_QUERY', 'PBR_QUERY_BEZ_ROWID', 'ImportError_', '_parse_time', '_existing_keys',
    '_epocha_pomalu', '_epocha', '_index_prehravani', '_uz_tam_je', '_zapamatuj', '_known_users',
    '_known_items', '_jellystat_item_type', '_normalizuj_id', '_sjednot_identifikatory',
    'refresh_playback_metadata', '_orphan_item_ids', '_relink', '_link_by_tmdb',
    '_osirele_zaznamy', '_link_by_name', 'link_imported_history', '_insert', 'JAZYKOVE_SLOUPCE',
    '_jazyk_ze_zdroje', '_build_row', 'ZAKAZANA_SLOVA', 'jen_cteni',
    'playback_reporting_available', 'import_playback_reporting', 'PBR_TSV_SLOUPCE', '_tsv_sloupce',
    'import_playback_reporting_tsv', '_zpracuj_pbr', 'JELLYSTAT_TABLES', '_find_jellystat_rows',
    '_pick', 'import_jellystat_json', 'STROP_NASOBEK', 'zkrat_nesmyslne_delky',
    'duplicate_playback_groups', 'merge_duplicate_playback', 'duplicate_playback_count',
    'DELKA_TOLERANCE_S', 'ROZESTUP_MAX_S', '_titul_pro_srovnani', '_existuje_import',
    'import_duplicate_groups', '_patri_k_sobe', '_uplnost', 'merge_import_duplicates',
    'import_duplicate_count', 'misplaced_episode_rows', '_NAZEV_EPIZODY', 'rozbor_nazvu',
    '_link_by_episode_number', 'orphan_playback_count', 'stale_name_rows', 'sjednot_nazvy',
    'orphan_items_count', 'DOHLEDAT_NEJVYS', 'dohledej_osirele_v_jellyfinu', 'zaloz_z_jellyfinu',
    '_dily_pod_serialem', '_knihovny_podle_cest', '_knihovna_pro', '_dil_v_knihovne', 'DUVOD_ID',
    'DUVOD_JEDNA_SHODA', 'DUVOD_VIC_SHOD', 'DUVOD_SERIAL_JE', 'DUVOD_SERIAL_NENI',
    'DUVOD_ZNA_SERIAL', 'DUVOD_NIC', 'DUVODY_POPIS', 'ROZBOR_NEJVYS', 'rozbor_osirelych',
    'kandidati_pro_osireleho', 'prirad_rucne', 'relink_orphans', 'repair_episode_links',
    'narovnej_data', 'import_summary',
]

# Řádky komentářů dnešního importers.py (po strip), v pořadí.
KOMENTARE_PUVODNE = [
    '## Jak se resi opakovany import',
    '# Dotaz do tabulky pluginu Playback Reporting.',
    '# Zaloha bez `rowid`. Nektere verze pluginu ten sloupec ve svem dotazovaci',
    '# nepovoli a cely dotaz odmitnou chybou 500, takze by import nesel spustit',
    '# vubec. Bez rowid prijdeme jen o cast klice proti opakovanemu importu -',
    '# a tu dnes zastoupi porovnani podle obsahu (viz _uz_tam_je).',
    '# ---------------------------------------------------------------------------',
    '# Spolecne pomocniky',
    '# ---------------------------------------------------------------------------',
    '# ---------------------------------------------------------------------------',
    '# Druha pojistka proti duplicitam: shoda podle obsahu',
    '# ---------------------------------------------------------------------------',
    '#',
    '# Klic `import:pbr:<rowid>:<itemid>` ohlida opakovany import **tehoz zdroje**.',
    '# Nestaci ale na dva pripady, ktere v praxi nastanou snadno:',
    '#',
    '#   1. Tataz historie se naimportuje z Jellystatu **i** z Playback Reportingu.',
    '#      Klice maji jiny tvar (jst / pbr), takze o sobe nevi a vsechno se zdvoji.',
    '#   2. Naimportuje se obdobi, ktere uz Jellyscope sam nasbiral. Vlastni zaznamy',
    '#      maji uplne jiny session_key.',
    '#',
    '# Proto porovnavame i obsah. Za tentyz zaznam povazujeme prehravani, ktere ma',
    '# stejneho uzivatele, stejnou polozku a **casove se prekryva**.',
    '#',
    '# Proc prekryv, a ne shodu casu zacatku: kazdy zdroj si zapisuje cas trochu',
    '# jinak (zacatek prehravani vs. okamzik zapisu do tabulky), takze presna shoda',
    '# by vetsinu duplicit propasla. Prekryv je odolnejsi a pritom bezpecny - tentyz',
    '# film nemuze jeden clovek sledovat dvakrat zaroven.',
    '# Napr. "2026-13-45 99:99:99" - tvar sedi, hodnoty ne.',
    '# Dotykajici se useky (konec == zacatek) prekryv nejsou - divak',
    '# si mohl pustit dalsi dil hned, jak dokoukal predchozi.',
    '# ---------------------------------------------------------------------------',
    '# Dohledani polozek k importovane historii',
    '# ---------------------------------------------------------------------------',
    '#',
    '# Ani Playback Reporting, ani Jellystat neposilaji tmdb ID. Posilaji ItemId',
    '# z Jellyfinu - jenze ten se meni pokazde, kdyz soubor prekodujes nebo',
    '# presunes. Importovana historie tak casto ukazuje na polozky, ktere uz',
    '# v knihovne nejsou pod tim jmenem.',
    '#',
    '# Bez dohledani by takove zaznamy zustaly viset ve vzduchu: v historii',
    '# by byly videt, ale v knihovne by k nim nic nevedlo a do statistik podle',
    '# knihovny nebo kodeku by se nezapocitaly.',
    '#',
    '# Dohledavame ve dvou krocich, od nejspolehlivejsiho:',
    '#',
    '#   1. **Podle tmdb ID.** Zeptame se Jellyfinu na to stare ItemId. Kdyz',
    '#      ho jeste zna, dostaneme ProviderIds a v nich tmdb. Pak uz jen',
    '#      najdeme polozku se stejnym tmdb v nasi knihovne.',
    '#',
    '#   2. **Podle nazvu.** Kdyz Jellyfin id nezna (soubor je pryc uplne),',
    '#      zkusime shodu nazvu. Parujeme jen tehdy, kdyz je vysledek',
    '#      **jednoznacny** - u dvou stejne pojmenovanych titulu radeji',
    '#      nespojime nic nez spatne.',
    '# Dva ruzne zaznamy se stejnym normalizovanym tvarem.',
    '# Nemelo by nastat - a kdyz ano, radeji nehadame.',
    '# Nejdriv srovnat tvar identifikatoru, jinak by se nize nesparovalo nic.',
    '# Typ polozky bereme z knihovny vzdycky, kdyz ho tam mame - zdroje',
    '# importu ho hlasi nespolehlive a nase tabulka je merodajna.',
    '# Bez znameho serialu hledame podle nazvu, at uz polozka',
    '# k nejakemu serialu patri nebo ne.',
    '#',
    '# Drive tu stalo `AND series_name IS NULL` a tim se odrizla',
    '# prave ta skupina, kvuli ktere tahle zaloha existuje:',
    '# osirely zaznam o EPIZODE. Ten serial nezna (jinak by se',
    '# sparoval driv), ale kazda epizoda v knihovne serial ma -',
    '# takze se shoda nenasla nikdy. V prehledu pak zustal dil',
    '# "Zvony" jako samostatny titul misto aby se zaradil pod',
    '# "Hra o truny".',
    '# Nez zacneme cokoli dohledavat, srovname tvar identifikatoru - jinak',
    '# by se cela naimportovana historie tvarila jako "polozka neznama".',
    '# Neni co dohledavat - ale doplnit jmena a typy ma smysl vzdycky.',
    '# (Treba po prvni synchronizaci knihovny, kdy identifikatory sedi',
    '# a chybi jen jmena uzivatelu.)',
    '# Co se nesparovalo s uz ulozenou polozkou, ale Jellyfin to zna,',
    '# rovnou zalozime. Bez toho zustala historie osirela, i kdyz',
    '# jsme o titulu vedeli uplne vsechno - jen jsme si to nikam',
    '# nezapsali.',
    '# Import uz probehl a data jsou ulozena. Kdyz se dohledani',
    '# nepovede, je to skoda, ne duvod hlasit chybu - zkusi se znovu',
    '# pri pristim importu.',
    '# Podle cisla dilu napred - je to jednoznacne. Jellystat i Playback',
    '# Reporting davaji do nazvu "Serial - s02e07 - Nazev", takze tohle',
    '# posadi na misto vetsinu prevzate historie serialu.',
    '# Az ted, kdyz historie ukazuje na spravne polozky, ma smysl z nich',
    '# prevzit typ, knihovnu a nazev serialu.',
    '# Jak se muze jmenovat sloupec s jazykem stopy. Zakladni tabulka pluginu',
    '# ho nema, ale nektere verze a vlastni upravy ano - a kdyz uz ten udaj',
    '# dorazi, byla by skoda ho zahodit. Porovnava se malymi pismeny bez',
    '# podtrzitek, at na zapisu nezalezi.',
    '# Jmeno serialu: nase knihovna je merodajna, ale kdyz o te polozce',
    '# jeste nic nevime (typicky import drive, nez probehla prvni',
    '# synchronizace), vezme se to, co poslal zdroj.',
    '# Jazyk, ktery zdroj poslal, ma prednost - je to udaj o tom, co',
    '# divak SKUTECNE poslouchal. Nektere verze pluginu Playback',
    '# Reporting ho ukladaji; vetsina zdroju ne.',
    '#',
    '# Kdyz ho zdroj nema, zbyva vychozi stopa souboru - a kdyz nezname',
    '# ani tu, zustane prazdny. Zamerne nic nedomyslime: vymysleny udaj',
    '# je horsi nez zadny.',
    '# ---------------------------------------------------------------------------',
    '# Pojistka: do Jellyfinu jen čteme',
    '# ---------------------------------------------------------------------------',
    '#',
    '# Jellyscope Jellyfin **nikdy nemění**. Všechno ostatní jsou obyčejné GET',
    '# dotazy, u kterých to platí samo. Jediná výjimka je tenhle plugin: jeho',
    '# dotazovací rozhraní je POST, protože se mu SQL posílá v těle požadavku.',
    '#',
    '# POST samo o sobě nic nemění - mění to, co se v něm pošle. Aby se sem',
    '# omylem nedostal jiný příkaz než čtení (třeba při pozdější úpravě),',
    '# projde každý dotaz tímhle sítem. Radši ať import odmítne pracovat,',
    '# než aby zapsal do cizí databáze.',
    '# Středník uprostřed by znamenal víc příkazů za sebou.',
    '# ---------------------------------------------------------------------------',
    '# 1. Playback Reporting (plugin Jellyfinu)',
    '# ---------------------------------------------------------------------------',
    '# I tady vypisujeme telo odpovedi - "chyba 500" bez neho nikomu',
    '# neporadi, kde hledat.',
    '# Zkusime oba tvary dotazu. Prvni je bohatsi (obsahuje rowid,',
    '# ze ktereho skladame klic proti opakovanemu importu), druhy',
    '# projde i tam, kde plugin rowid odmita.',
    '# Telo odpovedi obsahuje, co se pluginu nelibilo. Bez nej',
    '# je "chyba 500" hlaska, se kterou se neda nic delat.',
    '# Poradi sloupcu v zaloze pluginu Playback Reporting. Odpovida tabulce',
    '# PlaybackActivity - plugin ji do souboru vypisuje tak, jak je.',
    '# Bez hlavicky se drzime poradi z tabulky. Kdyz ma radek sloupcu vic',
    '# (nektere verze pridavaji na zacatek rowid), prebytek na zacatku',
    '# pojmenujeme, at se dvojice nerozjedou.',
    '# Zalohy z Windows byvaji v cp1250; a kdyz nesedi ani to, radeji',
    '# nahradime nectene znaky, nez abychom import odmitli kvuli',
    '# jedinemu pismenu v nazvu filmu.',
    '# Soubor bez tabulatoru je skoro jiste neco jineho, nez si clovek mysli.',
    '# Rict to rovnou je lepsi nez naimportovat nula zaznamu bez vysvetleni.',
    '# Klic zaznamu. `rowid` je nejlepsi, protoze ho plugin nemeni -',
    '# kdyz ho ale odmitl (viz PBR_QUERY_BEZ_ROWID), poskladame klic',
    '# z toho, co zaznam jednoznacne popisuje. Bez teto zalozky by',
    '# vsechny radky tehoz titulu dostaly stejny klic a naimportoval',
    '# by se z nich jediny.',
    '# Tentyz zaznam uz muze byt v databazi z Jellystatu nebo ze sberace.',
    '# Import neposila tmdb ID - dohledame ho sami, at se prevzata historie',
    '# spoji se skutecnymi tituly v knihovne.',
    '# ---------------------------------------------------------------------------',
    '# 2. Jellystat (JSON zaloha)',
    '# ---------------------------------------------------------------------------',
    '# Jellystat uklada prehravani do tabulky jf_playback_activity. V zaloze',
    '# muze byt bud primo seznam, nebo slovnik s nazvy tabulek.',
    '# Bud rovnou seznam zaznamu, nebo seznam tabulek.',
    '# Nekdy je vse zabalene o uroven niz.',
    '# Ke kazdemu zdrojovemu radku (prvni dve casti klice) si zapamatujeme,',
    '# pod jakym klicem uz v databazi je. Podle toho se pozna oprava.',
    '# U epizody posila Jellystat DVE ruzna id a zalezi, ktere se vezme:',
    '#',
    '#   NowPlayingItemId  = id SERIALU   ("Tym SEAL")',
    '#   EpisodeId         = id DILU      (to, co chceme)',
    '#',
    '# Drive se bralo to prvni, takze cela historie serialu skoncila',
    '# na jednom id, ke kteremu v knihovne nic nevede - a protoze se',
    '# zaznam jmenoval jmenem serialu, nedal se dil dopocitat vubec.',
    '# Zadne dohledavani to uz nespravi; jedina cesta je vzit spravne',
    '# id rovnou pri importu.',
    '# Stejny pribeh u nazvu: NowPlayingItemName je u epizody jmeno',
    '# serialu. Jmeno dilu je v EpisodeName - a jmeno serialu si',
    '# necháme zvlast, at se prehravani zaradi pod svuj serial.',
    '# Klic ma tvar "import:jst:<id radku v zaloze>:<id polozky>".',
    '# Prvni dve casti identifikuji ZDROJOVY RADEK, treti to, na co',
    '# ukazuje - a prave ta se opravou meni.',
    '# Tentyz radek zalohy uz v databazi je, jen ukazuje jinam - to je',
    '# presne stav po importu, ktery bral id serialu misto dilu.',
    '# Opravime ho na miste; kdybychom vlozili novy, mel by clovek',
    '# kazde prehravani dvakrat.',
    '# Tentyz zaznam uz muze byt v databazi z Playback Reportingu nebo',
    '# ze sberace.',
    '# Zaloha Jellystatu tmdb ID neobsahuje - dohledame ho sami.',
    '# ---------------------------------------------------------------------------',
    '# Uklid duplicit ve vlastni historii',
    '# ---------------------------------------------------------------------------',
    '#',
    '# Duplicity resi tenhle modul uz u importu (viz `_uz_tam_je`). Vzniknout ale',
    '# muzou i bez importu: kdyz proti jedne databazi bezi **dva sberace naraz**',
    '# (typicky nedopatrenim - stara a nova verze aplikace se stejnym nastavenim),',
    '# kazdy si zalozi vlastni radek pro tentyz film.',
    '#',
    '# Predchazi se tomu ve sberaci (viz collector._prevzit_cizi_relaci) - jenze',
    '# to plati az od ted. Zaznamy, ktere uz v databazi lezi, je potreba uklidit',
    '# jednorazove, a to dela tahle funkce.',
    '# Kolikanasobek delky poradu jeste bereme jako sledovani.',
    '#',
    '# Prehravac spusteny na dvojnasobek delky dilu je porad predstavitelny',
    '# (clovek se vrati a pusti si scenu znovu), tricetinasobek uz ne - to je',
    '# televize, kterou nikdo nevypnul. Polovina navic je kompromis: nechava',
    '# prostor na previjeni a pritom zachyti prave ty pres noc.',
    '## Kdy jsou dva zaznamy tentyz zazitek',
    '# Klasicke slucovani intervalu: jedeme zleva doprava a co se',
    '# prekryva s rozdelanou skupinou, do ni patri. Zaznam z jineho',
    '# zarizeni do skupiny nepatri, i kdyz se casem prekryva.',
    '# Nejuplnejsi zaznam si necháme; pri shode ten starsi (nizsi id),',
    '# aby byl vysledek stejny pri kazdem spusteni.',
    '# Kdyz nektery ze zaznamu jeste bezel, bezi i slouceny -',
    '# jinak by prave hrajici film zmizel z "Prave se hraje".',
    '# ---------------------------------------------------------------------------',
    '# Duplicity mezi dvema zdroji importu',
    '# ---------------------------------------------------------------------------',
    '#',
    '# `duplicate_playback_groups()` vys hleda **prekryv v case**. Na duplicity',
    '# ze dvou sberacu to staci, protoze oba popisuji tutez relaci a lisi se',
    '# o vteriny. Mezi Jellystatem a Playback Reportingem ale prekryv nestaci:',
    '# kazdy si zapisuje jiny okamzik (zacatek prehravani vs. zapis do tabulky),',
    '# takze tytez dva a pul hodiny filmu vyjdou tu v 18:15, tu v 18:49 - a u',
    '# nocniho koukani klidne az druhy den rano.',
    '#',
    '# Poznat se to ale da podle neceho jineho: **stejne dlouhe prehravani**.',
    '# Kdyz tentyz clovek na temze zarizeni sledoval tentyz titul stejne dlouho',
    '# (na par minut presne) a v ramci jednoho dne, jsou to dva zapisy o jedne',
    '# vecerni podivane, ne dve podivane.',
    '#',
    '# Je to odhad, ne dukaz - proto se pouziva jen tam, kde je aspon jeden',
    '# ze zaznamu z importu. Vlastni sber si duplicity nedela.',
    '# O kolik se smi lisit odsledovany cas, aby to porad byl tentyz zazitek.',
    '# Jak daleko od sebe smi byt zacatky.',
    '# Zarizeni schvalne NENI v klici skupiny - kdyz ho jeden ze zdroju',
    '# nehlasi, klic by se rozesel a duplicita by se nenasla. Porovnava',
    '# se az v `_patri_k_sobe()`, kde nezname zarizeni nikomu neodporuje.',
    '# Zarizeni si nesmi odporovat. Tentyz film muze jeden clovek pustit',
    '# na televizi i na telefonu; nezname zarizeni nikomu neodporuje,',
    '# jinak by se nesparoval zaznam ze zdroje, ktery ho neposila.',
    '# Delsi z obou: kratsi zaznam byva ten, ktery nekdo',
    '# zastavil driv, nez stihl zapsat konec.',
    '# Dily podle serialu a nazvu. Nazev muze v jednom serialu vyjimecne',
    '# sedet na vic dilu ("Cast 1"); takove skupiny preskocime.',
    '# Jak Jellystat i Playback Reporting skládají název epizody:',
    '#',
    '#     "Seal Team 6 - s02e07 - 7. epizoda"',
    '#     "Nadace - s02e10 - Mýty o stvoření"',
    '#',
    '# Je v tom všechno, co potřebujeme k jednoznačnému určení dílu: seriál,',
    '# řada i číslo. Hledat v knihovně **celý ten řetězec** nemá smysl - tam',
    '# se ten díl jmenuje jen "7. epizoda".',
    '# Když číslo dílu nesedí, zkusíme ještě název epizody v rámci',
    '# téhož seriálu - někdy se číslování mezi zdroji rozchází.',
    '# Kolik osirelych id se najednou pta Jellyfinu. Vic nez tisic titulu',
    '# osirele historie nebyva a jeden dotaz zvladne padesat, takze je to',
    '# strop pro jistotu, ne pro vykon.',
    '# Kolik zaznamu na to id jeste visi. Kdyz zadny, neni co resit -',
    '# Jellyfin muze vratit i polozku, kterou mezitim navazal nekdo',
    '# jiny, a zakladat ji kvuli nicemu by bylo horsi nez nedelat nic.',
    '# Serial nebo rada: nemaji soubor, synchronizace je nezna a jako',
    '# polozka knihovny by skoncily v archivu. Viz docstring.',
    '# Dily toho serialu, jak je znama knihovna. Bereme i archivovane -',
    '# historie na ne odkazovat smi, jen uz nejdou prehrat.',
    '# Vic dilu stejneho jmena v jednom serialu se stava',
    '# (dvoudilne epizody). Hadat nesmime - spatne prirazena',
    '# historie je horsi nez neprirazena.',
    '# Dil neurcime, ale serial ano.',
    '# Duvody, proc se zaznam nepodarilo zaradit. Poradi je od "da se s tim',
    '# neco delat" k "uz opravdu nic" - v tom poradi se i vypisuji.',
    '# Co ktery duvod znamena a jestli se s tim da neco delat. Texty jsou',
    '# tady, ne v sablone: patri k tomu rozdeleni, ne k jeho vykresleni.',
    '# Kolik osirelych titulu se rozebira najednou. Vic uz neni seznam,',
    '# ale vypis - a stranka by se stala necitelnou.',
    '# Knihovnu si nacteme jednou do pameti. Dotaz na kazdy titul zvlast',
    '# by pri par stech osirelych znamenal tisice dotazu.',
    '# Nejdřív podle čísla dílu - to je jednoznačné. Teprve pak podle',
    '# holého názvu, kde se dá jen doufat, že je jedinečný.',
    '# Dohledani potrebuje Jellyfin. Kdyz nebezi, neni to duvod zastavit',
    '# zbytek - ten se obejde bez site.',
    '# Nejdriv archiv: kdyz se dil vrati k zivemu zaznamu, maji dalsi kroky',
    '# o jednu polozku min, na kterou by mohly historii navazat.',
]


# Komentáře upravené schválně až po přesunu (starý text -> nový), aby
# odpovídaly novému umístění - každá taková změna je vidět tady.
ZMENENE_KOMENTARE = {
    "# `duplicate_playback_groups()` vys hleda **prekryv v case**. Na duplicity":
        "# `duplicate_playback_groups()` (v uklid.py) hleda **prekryv v case**. Na duplicity",
}


def definovane(st: ast.Module) -> set[str]:
    """Jména definovaná na nejvyšší úrovni (def, class, přiřazení)."""
    jmena: set[str] = set()
    for uzel in st.body:
        if isinstance(uzel, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            jmena.add(uzel.name)
        elif isinstance(uzel, ast.Assign):
            jmena |= {c.id for c in uzel.targets if isinstance(c, ast.Name)}
        elif isinstance(uzel, ast.AnnAssign) and isinstance(uzel.target, ast.Name):
            jmena.add(uzel.target.id)
    return jmena


def strom(cesta: Path) -> ast.Module:
    return ast.parse(cesta.read_text(encoding="utf-8"))


def je_balik() -> bool:
    return (BALIK / "__init__.py").is_file()


def nactena(st: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(st) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


importers = importlib.import_module("jellyscope.importers")

print("--- (f) starý soubor ---")
check(je_balik() and not STARY.exists(), "importers.py není, balík importers/ ano")
PO_STARU = re.compile(r"""["']importers\.py["']""")
po_staru = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py"))
            if s.name not in S_UKAZKAMI and s.name != "test_sync_do_produ.py"
            for i, radek in enumerate(s.read_text(encoding="utf-8").splitlines(), 1) if PO_STARU.search(radek)]
check(not po_staru, f"žádný test nečte importers.py podle cesty ({po_staru[:6]})")

print()
print("--- (a) vstupní dveře mají všechno, co se zvenku čte ---")
JMENO = re.compile(r"(?<![\w.])importers\.([A-Za-z_]\w*)")
FROM = re.compile(r"from\s+jellyscope\.importers\s+import\s+\(?([^)\n]+)\)?")
pouzite: dict[str, str] = {}
for cesta in sorted([*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
                     *(PROJECT / j for j in ("demo.py", "run.py", "manage.py"))]):
    if not cesta.is_file() or BALIK in cesta.parents or cesta == STARY or cesta.name in S_UKAZKAMI:
        continue
    text = cesta.read_text(encoding="utf-8")
    for jmeno in JMENO.findall(text):
        pouzite.setdefault(jmeno, cesta.name)
    for skupina in FROM.findall(text):
        for kus in skupina.split("#")[0].split(","):
            if kus.strip():
                pouzite.setdefault(kus.split(" as ")[0].strip(), cesta.name)
pouzite.pop("py", None)
chybi = sorted(f"{j} ({kde})" for j, kde in pouzite.items() if j not in SOUBORY and not hasattr(importers, j))
check(len(pouzite) > 20 and not chybi, f"všech {len(pouzite)} jmen jde dveřmi (chybí {chybi[:6]})")

print()
print("--- (b) každé jméno bydlí ve svém podmodulu ---")
check(sorted(j for js in PODMODULY.values() for j in js) == sorted(PUVODNI),
      f"mapa pokrývá všech {len(PUVODNI)} jmen importers.py, každé jednou")
if je_balik():
    chybi_v, jine = [], []
    for m, jmena in PODMODULY.items():
        cesta = BALIK / f"{m}.py"
        tam = definovane(strom(cesta)) if cesta.is_file() else set()
        chybi_v += [f"{m}.{j}" for j in jmena if j not in tam]
        if cesta.is_file():
            modul = importlib.import_module(f"jellyscope.importers.{m}")
            jine += [f"{m}.{j}" for j in jmena if j in tam and getattr(importers, j, None) is not getattr(modul, j)]
    check(not chybi_v, f"jména jsou v podmodulech podle mapy ({chybi_v[:6]})")
    check(not jine, f"dveře ukazují na tytéž objekty jako podmoduly ({jine[:6]})")
    jellyfin = importlib.import_module("jellyscope.jellyfin")
    check(getattr(importers, "JellyfinClient", None) is jellyfin.JellyfinClient,
          "importers.JellyfinClient je klient z jellyfin.py (testy ho podvrhují)")
else:
    check(False, "jména jsou v podmodulech podle mapy (balík importers/ není)")

print()
print("--- (c) bez kruhů ---")
if je_balik():
    hrany: dict[str, set[str]] = {m: set() for m in SOUBORY}
    zakazane = []
    for m in SOUBORY:
        if not (BALIK / f"{m}.py").is_file():
            continue
        for uzel in ast.walk(strom(BALIK / f"{m}.py")):
            if isinstance(uzel, ast.ImportFrom) and uzel.level == 1:
                if uzel.module is None:
                    zakazane += [f"{m}: from . import {a.name}" for a in uzel.names if a.name not in SOUBORY]
                    hrany[m] |= {a.name for a in uzel.names if a.name in SOUBORY}
                elif uzel.module in SOUBORY:
                    hrany[m].add(uzel.module)
                else:
                    zakazane.append(f"{m}: from .{uzel.module} import")
    check(not zakazane, f"žádný import v podmodulu nemíří omylem do balíku ({zakazane[:6]})")
    navic = {m: sorted(hrany[m] - SMI[m]) for m in SOUBORY if hrany[m] - SMI[m]}
    check(not navic, f"závislosti podle grafu ve specu (navíc {navic})")
    # Import podmodulu vždy napřed spustí __init__ balíku (a ten načte všechny
    # podmoduly) - jedna kontrola na balík pokryje všechny, víc by jen opakovalo totéž.
    # Partneři v kruhu importů (scanner a tasks berou importers líně) jsou jiný vstup.
    for jmeno in ["jellyscope.importers", "jellyscope.scanner", "jellyscope.tasks"]:
        beh = subprocess.run([sys.executable, "-c", f"import {jmeno}"], cwd=PROJECT, env=os.environ.copy(),
                             capture_output=True, text=True, timeout=180)
        chyba = (beh.stderr.strip().splitlines() or [""])[-1]
        check(beh.returncode == 0, f"`import {jmeno}` jako první v čistém procesu ({chyba[:120]})")
else:
    check(False, "závislosti bez kruhu (balík importers/ není)")

print()
print("--- (d) velikost ---")
if je_balik():
    velke = [(p.name, n) for p in sorted(BALIK.glob("*.py")) if (n := len(p.read_text(encoding="utf-8").splitlines())) > 600]
else:
    velke = [("importers.py", len(STARY.read_text(encoding="utf-8").splitlines()))]
check(not velke, f"žádný podmodul nad 600 řádků ({velke})")

print()
print("--- (e) testy podvrhují tam, kde to zasáhne ---")
KDE = {j: m for m, js in PODMODULY.items() for j in js}
ctenari: dict[str, set[str]] = {}
if je_balik():
    for m in SOUBORY:
        if (BALIK / f"{m}.py").is_file():
            for jmeno in nactena(strom(BALIK / f"{m}.py")):
                ctenari.setdefault(jmeno, set()).add(m)
else:
    for uzel in strom(STARY).body:
        moduly = {KDE[j] for j in definovane(ast.Module(body=[uzel], type_ignores=[])) if j in KDE}
        for jmeno in nactena(uzel):
            ctenari.setdefault(jmeno, set()).update(moduly)
ctenari = {j: m for j, m in ctenari.items() if m}
jmena_v = {m: definovane(strom(BALIK / f"{m}.py")) | nactena(strom(BALIK / f"{m}.py"))
           for m in SOUBORY if (BALIK / f"{m}.py").is_file()}
spatne, mimo = [], []
for cesta in sorted(PROJECT.glob("tests/*.py")):
    if cesta.name in S_UKAZKAMI:
        continue
    text = cesta.read_text(encoding="utf-8")
    spatne += [f"{cesta.name}: {chyba}" for chyba in spatne_podvrhy(text, "importers", ctenari)]
    for m, jmeno in sorted(podvrhy_v_podmodulech(text, "importers")):
        if m in jmena_v and jmeno not in jmena_v[m]:
            mimo.append(f"{cesta.name}: importers.{m}.{jmeno}")
check(not spatne, f"jména čtená v podmodulech se podvrhují přes podvrhni nebo ve všech čtenářích ({spatne})")
check(not mimo, f"každý podvrh v podmodulu míří tam, kde se jméno čte ({mimo})")

print()
print("--- (g) komentáře ---")
if je_balik():
    ted = Counter(r.strip() for p in BALIK.glob("*.py") for r in p.read_text(encoding="utf-8").splitlines()
                  if r.strip().startswith("#"))
    cekane = Counter(ZMENENE_KOMENTARE.get(k, k) for k in KOMENTARE_PUVODNE)
    chybi_k = cekane - ted
    nove = sum((ted - cekane).values())
    check(not chybi_k, f"žádný komentář se neztratil (chybí {list(chybi_k)[:4]})")
    check(nove <= 30, f"nových komentářů nejvýš 30 ({nove})")
else:
    print("       čeká na balík (viz f)")

print()
print("--- (h) logger ---")
if je_balik():
    # Všechna volání, ne jedno na soubor - druhý getLogger by jinak přebil první.
    loggery = [(p.name, v) for p in sorted(BALIK.glob("*.py"))
               for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
    spatne_l = [(s, v) for s, v in loggery if v != '"jellyscope.importers"']
    check(loggery and not spatne_l,
          f"balík loguje jako jellyscope.importers ({spatne_l or len(loggery)})")
else:
    check(False, "logger (balík importers/ není)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
