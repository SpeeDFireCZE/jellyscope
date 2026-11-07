r"""Pravidla podbalíku web/nastaveni/ - routy Nastavení podle oblastí (přestavba 3e).

`jellyscope/web/nastaveni.py` měl 2 081 řádků a 46 rout Nastavení. Je
z něj podbalík `jellyscope/web/nastaveni/`: každý soubor s routami má svůj
`router` a `nastaveni/__init__.py` je spojí do jednoho `router`, který web
připojí jako dřív. Dveře vystaví všechna jména, takže testy s
`from jellyscope.web import nastaveni as web_nastaveni` se nemění.
Test hlídá:

(a) vstupní dveře mají všechno, co se zvenku čte (`web_nastaveni.X`,
    `web.nastaveni.X`, `from jellyscope.web.nastaveni import X`);
(b) každé jméno bydlí ve svém souboru, dveře ukazují na tentýž objekt
    a `nastaveni.router` má všech 46 rout;
(c) bez kruhů a se správným počtem teček v relativních importech
    (`..zaklad`, `...` = balík jellyscope); každý soubor jde naimportovat
    jako první v čistém procesu;
(d) žádný soubor nad 600 řádků;
(e) testy podvrhují tam, kde to zasáhne (i přes alias web_nastaveni);
(f) starý nastaveni.py není;
(g) komentáře se při přesunu neztratily - srovnané po jednom;
(h) v podbalíku není vlastní logger (loguje se přes `log` z web/zaklad.py).

Spec: docs/superpowers/specs/2026-11-06-nastaveni-balik-design.md

Spuštění:
    .venv/bin/python tests/test_nastaveni_balik.py
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "nastaveni_balik.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _balik import podvrhy_v_podmodulech, podvrhy_ve_dverich, routy_naplocho, spatne_podvrhy  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


BALIK = PROJECT / "jellyscope" / "web" / "nastaveni"
STARY = PROJECT / "jellyscope" / "web" / "nastaveni.py"
S_UKAZKAMI = {Path(__file__).name, "test_stats_balik.py", "test_web_balik.py", "test_scanner_balik.py",
              "test_importers_balik.py", "_balik.py"}

# Mapa jmen nejvyšší úrovně nastaveni.py -> soubor podbalíku (spec, oddíl Rozdělení).
PODMODULY: dict[str, list[str]] = {
    "spolecne": ["_DB_DRAFT", "MAX_UPLOAD_MB", "_DRAFT_TTL_SECONDS", "CASTE_ZONY", "_nacti_zalohu", "_draft_save",
                 "_draft_read", "_draft_clear", "_zona_existuje", "_pocty_uklidu", "_import_ze_souboru",
                 "_dny_v_tydnu", "_kapacita_do_formulare", "_generace_nesedi", "_uloz_tajemstvi"],
    "stranka": ["settings_page", "settings_save"],
    "ucty": ["_uloz_strom", "settings_klice", "VOLBY_KLICE", "settings_prava_uctu", "settings_ctenari",
             "settings_divaci", "blocks_unblock", "blocks_permanent", "account_create", "account_password",
             "account_role", "_strom_klice", "_prava_root_klice", "_prava_klice_z_formulare", "api_token_novy",
             "api_token_prava", "api_token_zrusit", "api_token_vypnout", "api_token_zapnout", "account_delete"],
    "pripojeni": ["settings_connection", "settings_database"],
    "upozorneni": ["settings_notifications", "settings_notification_events", "settings_notification_texts",
                   "settings_notification_test"],
    "rozhrani": ["settings_interface", "settings_updates", "settings_language"],
    "aktualizace": ["settings_update", "_stranka_aktualizace", "settings_restart", "_naplanuj_restart",
                    "backup_download", "backup_delete", "backup_restore"],
    "ulohy": ["settings_sync", "settings_stop", "settings_scan", "tasks_save", "tasks_run", "log_stahnout"],
    "historie": ["historie_zapomen", "historie_vrat", "import_detect", "import_playback_reporting",
                 "import_playback_reporting_file", "history_tidy", "history_orphans", "history_assign",
                 "import_jellystat"],
}
SOUBORY = list(PODMODULY)
S_ROUTAMI = [m for m in SOUBORY if m != "spolecne"]
# Graf závislostí podle specu: kdo smí na koho sahat.
SMI = {"spolecne": set(), "stranka": {"spolecne", "ucty"}, "ucty": set(), "pripojeni": {"spolecne"},
       "upozorneni": {"spolecne"}, "rozhrani": {"spolecne"}, "aktualizace": set(), "ulohy": set(),
       "historie": {"spolecne"}}
ROUT = 46

# --- dnešní stav (vygenerovaný z web/nastaveni.py 6. 11. 2026) ---------------------------
PUVODNI = [
    '_DB_DRAFT', 'MAX_UPLOAD_MB', '_DRAFT_TTL_SECONDS', 'CASTE_ZONY', '_nacti_zalohu',
    '_draft_save', '_draft_read', '_draft_clear', '_zona_existuje', '_pocty_uklidu',
    '_import_ze_souboru', '_dny_v_tydnu', '_kapacita_do_formulare', '_generace_nesedi',
    'settings_page', 'settings_save', 'settings_sync', 'settings_stop', 'settings_scan',
    '_uloz_strom', 'settings_klice', 'VOLBY_KLICE', 'settings_prava_uctu', 'settings_ctenari',
    'settings_divaci', 'settings_connection', 'settings_database', '_uloz_tajemstvi',
    'settings_notifications', 'settings_notification_events', 'settings_notification_texts',
    'settings_notification_test', 'settings_interface', 'settings_updates', 'settings_language',
    'settings_update', '_stranka_aktualizace', 'settings_restart', '_naplanuj_restart',
    'historie_zapomen', 'historie_vrat', 'tasks_save', 'log_stahnout', 'backup_download',
    'backup_delete', 'backup_restore', 'tasks_run', 'import_detect', 'import_playback_reporting',
    'import_playback_reporting_file', 'history_tidy', 'history_orphans', 'history_assign',
    'import_jellystat', 'blocks_unblock', 'blocks_permanent', 'account_create', 'account_password',
    'account_role', '_strom_klice', '_prava_root_klice', '_prava_klice_z_formulare',
    'api_token_novy', 'api_token_prava', 'api_token_zrusit', 'api_token_vypnout',
    'api_token_zapnout', 'account_delete',
]

# Řádky komentářů dnešního web/nastaveni.py (po strip), v pořadí.
KOMENTARE_PUVODNE = [
    '# Rozepsané nastavení databáze.',
    '#',
    '# Tlačítka "Otestovat spojení" a "Přenést data" nic neukládají - po nich se',
    '# stránka načte znovu a formulář by se vrátil k tomu, co je uložené. Vyplníš',
    '# tedy server, port, uživatele i heslo, otestuješ spojení, ono řekne "funguje"',
    '# - a formulář je zase prázdný a přepnutý na SQLite. Proto si rozepsané',
    '# hodnoty na chvíli podržíme.',
    '#',
    '# Proč v paměti a ne v session: session je u nás **podepsaná cookie**, tedy',
    '# něco, co si prohlížeč nese s sebou a co jde přečíst. Heslo k databázi tam',
    '# nepatří. Tady zůstane v paměti procesu a po restartu zmizí.',
    '# Strop pro nahrany soubor pri importu historie.',
    '#',
    '# Musi sedet s limitem na reverzni proxy - ta odmita drive nez aplikace',
    '# a hlaska pak prijde od nginxu, ne od nas. Priklady konfigurace ve',
    '# slozce deploy/ maji tutez hodnotu; hlida to test_deploy.py.',
    '#',
    '# 200 MB je s rezervou: soubor tehle velikosti je pres milion radku',
    '# historie, coz zadna domacnost nenasbira. A cely se cte do pameti,',
    '# takze vetsi strop by na malem serveru delal vic skody nez uzitku.',
    '# Nabidka casovych zon. Cely seznam ma pres sest set polozek, takze se',
    '# nabizi jen ty, ktere lidi opravdu pouzivaji - napsat jde libovolnou.',
    '# Nastaveni je rozdelene na sekce. Drive to byla jedna dlouha stranka,',
    '# na ktere se nedalo nic najit - a navic se pri kazdem otevreni pocitalo',
    '# vsechno naraz, vcetne poctu radku v databazi a vypisu zaloh z disku.',
    '#',
    '# Ted se nacita jen to, co patri k otevrene sekci.',
    '# Starsi nastaveni jednotku neznalo - u velkych hodnot jsou',
    '# terabajty citelnejsi.',
    '# Volby prohlížeče logu. Jsou v adrese, ne v nastavení: je to pohled,',
    '# ne volba serveru - dva správci se můžou dívat každý na něco jiného.',
    '# Který log: aplikace („app"), nebo API. U logu API filtry: vlastník',
    '# klíče, klíč a IP. Text, ne číslo - nesmysl v adrese má dát celý',
    '# log, ne 422.',
    '# Čtenář má jen jednu sekci - a je to ta, kde si mění heslo.',
    '# Šabloně dáváme nastavení BEZ tajemství - API klíč se do stránky',
    '# nesmí dostat ani nedopatřením.',
    '# Nabidka sekci se kresli DVAKRAT - jako zalozky na desktopu a jako',
    '# rozbalovaci seznam na mobilu - a proto se sklada tady, jednou.',
    '# Dva rucne psane seznamy by se casem rozesly a v jednom by nova',
    '# sekce chybela.',
    '# Když uživatel jen testoval spojení, nastavení se neuložilo -',
    '# do formuláře ale patří to, co vyplnil, ne uložená hodnota.',
    '# Jinak by po každém testu vyplňoval adresu znovu.',
    '# Generace serveru: co je zvolené a co se naposledy zjistilo.',
    '# Ruční volba proti tomu, co server hlásí. Když si odporují,',
    '# není to chyba (od toho ta volba je), ale má to být vidět -',
    '# jinak se ptáš proč aplikace posílá jiné dotazy, než čekáš.',
    '# Prihlaseni divaku jellyfinovym heslem patri sem: je to',
    '# o Jellyfinu a jeho uctech, ne o uctech Jellyscope.',
    '# Klic i hesla divaku jdou na Jellyfin citelne - sifruje az',
    '# https. Kdyz adresa zacina http:// a nevede na tenhle stroj,',
    '# ma to byt videt.',
    '# Ulozena tajemstvi se do stranky nevypisuji (db.TAJNA_NASTAVENI),',
    '# ale formular ma poznat, ze uz neco ulozeneho JE - jinak by',
    '# prazdne pole vypadalo jako nenastaveno.',
    '# Karta s analýzou souborů se přestěhovala mezi Úlohy - tahle',
    '# sekce už jen vybírá zdroj dat, takže nepotřebuje ani pokrytí,',
    '# ani stav posledního běhu.',
    '# Kapacita se uklada v bajtech, ale zadava se v jednotce, kterou',
    '# si clovek vybral - u dvacetiterabajtoveho pole je "30000 GB"',
    '# zbytecne dlouhe cislo.',
    '# Karta "Narovnání dat" se sem přestěhovala z Importu, takže',
    '# sem patří i čísla, která vypisuje.',
    '# Když úloha doběhne, zatímco je člověk na téhle sekci, načte',
    '# se stránka sama - jinak by tu proužek "úloha běží" zůstal',
    '# viset a výsledek by se neobjevil. Viz hlídač ve static/js/app.js.',
    '# Co je na stroji za pg_dump a co za server. Bez toho se',
    '# nesoulad verzí hledá jen podle chybové hlášky po tom, co',
    '# záloha selže - viz tasks._vyber_pg_dump().',
    '# Odklízení historie. Čísla se počítají i když je vypnuté -',
    '# právě podle nich se člověk rozhoduje, jestli ho zapnout.',
    '# Root klíče: co smí (sloupec Typ a práva) a předvyplněné okno',
    '# Práva. Nový klíč začíná s ničím zaškrtnutým - práva se mu',
    '# nastavují vědomě.',
    '# Okno „Jak se to používá" (_api_navod.html) - správci vyjdou',
    '# všechny adresy, ale seznam se předává vždycky výslovně.',
    '# Nově vyrobený klíč se ukazuje jen jednou, hned po',
    '# přesměrování; drží se proto v relaci, ne v adrese - a při',
    '# dalším načtení stránky už ho nemá kdo vypsat. Ukáže se',
    '# v řádku svého klíče (makro bunka_klice).',
    '# Kolik je v historii duplicitních a špatně přiřazených záznamů.',
    '# Ukazuje se předem, ať je vidět, jestli má úklid vůbec smysl.',
    '# Dvě různé konfigurace, a plete se to snadno:',
    '#',
    '#   ulozena  = co je v data/database.json, tedy co se použije',
    '#              po restartu. Tohle patří do formuláře.',
    '#   bezici   = co aplikace používá teď. Změna databáze se projeví',
    '#              až restartem, takže do restartu se tyhle dvě liší.',
    '#',
    '# Dřív se do formuláře dávala běžící konfigurace - a protože ta',
    '# je v paměti zakešovaná, po uložení PostgreSQL se formulář',
    '# přepnul zpátky na SQLite. Vypadalo to, že se uložení nepovedlo,',
    '# i když soubor byl zapsaný správně.',
    '# Když má uživatel něco rozepsaného (otestoval spojení, ale ještě',
    '# neuložil), ukážeme ve formuláři to - jinak by o vyplněné údaje',
    '# při každém testu přišel.',
    '# Cesta k pythonu, kterým aplikace zrovna běží. Návod na',
    '# doinstalování psycopg tak ukáže příkaz, který jde',
    '# zkopírovat - ne obecné "pip install", které by v případě',
    '# virtuálního prostředí instalovalo někam jinam.',
    '# Kolik je správců - podle toho se u posledního z nich',
    '# neukáže tlačítko Smazat. Viz accounts.delete().',
    '# Vlastní práva každého účtu bez práv správce: strom tak,',
    '# jak pro něj platí (vlastní, nebo zděděný od skupiny), a',
    '# jestli je vlastní. Správce žádný strom nemá - vidí všechno.',
    '# Vyjimka pro vlastni API klic: skupina / ano / ne.',
    '# Je zvlast od stromu a plati v obou rezimech. Volba',
    '# se cte zpatky z VOLBY_KLICE, at jsou obe strany jedna.',
    '# Vstup z adresy nikdy nedůvěřuj - ani vlastnímu odkazu. Jméno',
    '# souboru si ověří applog sám (viz _bezpecna_cesta), tady stačí',
    '# počet řádků, úroveň a filtry.',
    '# Nabídka filtrů: klíče, které existují, a jejich vlastníci',
    '# („-" = root klíč). Smazaný klíč v logu zůstane, jen se',
    '# podle něj nedá vybrat.',
    '# Nabidka zon a aktualni cas v te zvolene - at je po ulozeni videt,',
    '# ze se opravdu neco zmenilo. Bez toho by clovek koukal na pole',
    '# s textem a musel hadat, jestli ta zona vubec plati.',
    '# Vstup z formulare se nikdy neuklada bez kontroly. Uzivatel muze',
    '# (omylem i schvalne) poslat cokoliv.',
    '# Cas synchronizace knihovny se sem uz nepise - patri k naplanovanym ulohám',
    '# a meni se ve vlastnim formulari. Kdyby ho ukladaly oba, prepsaly by',
    '# si hodnotu navzajem.',
    '# Kapacita se zadava v GB nebo TB. Nula znamena "zjisti si to sam".',
    '# V jazyce serveru, ne v jazyce toho, kdo kliknul - viz i18n.bez_jazyka_uctu.',
    '# `wait=task` necha stranku pockat a po dokonceni ji obnovi -',
    '# jinak clovek koukа na "bezi" a netusi, kdy uz je hotovo.',
    '# `wait=task` rekne strance, at si pocka a obnovi se sama, jakmile',
    '# uloha doopravdy skonci. Uzivatel tak nemusi hadat, kdy uz muze.',
    '# Kam se po spusteni vratit. Jen id knihovny, ktera opravdu existuje;',
    '# cizi hodnota z formulare se nesmi dostat do adresy presmerovani.',
    '# Bez knihovny zpatky mezi Ulohy - tam ta tlacitka jsou.',
    '# Volba „Vlastní API klíče" v okně Práva -> hodnota sloupce accounts.smi_klic.',
    '# Vlastni API klic se uklada v obou rezimech - je to jina otazka nez',
    '# strom stranek. Formular bez te volby (starsi stranka v otevrene',
    '# karte) vyjimku necha, jak byla.',
    '# Generace serveru. Adresa ani klíč se nezahazují - ty jsou pro obě',
    '# generace tytéž a liší se jen to, jak se posílají dotazy.',
    '#',
    '# Zahodí se ale **zjištěná verze**, a jen při skutečné změně volby:',
    '# patří k tomu, co bylo vybrané předtím, takže by na stránce zůstala',
    '# dvě čísla, která si odporují. Prázdná se zjistí znovu při nejbližším',
    '# testu spojení nebo synchronizaci.',
    '# Prazdny klic znamena "nech stavajici". Pri testu bereme i rozepsany,',
    '# aby slo otestovat vic pokusu za sebou bez opakovaneho vypisovani.',
    '# Rozepsane si podrzime, at se formular po testu nevyprazdni.',
    '# Verzi zjistíme rovnou při uložení, ne až při testu spojení nebo',
    '# noční synchronizaci: kdo přepne generaci a klikne na Uložit, má',
    '# hned vidět, co server hlásí - a hlavně se hned pozná, když si to',
    '# s ruční volbou odporuje.',
    '#',
    '# Krátký strop a spolknutá chyba: uložení nesmí selhat kvůli tomu,',
    '# že server zrovna neběží. Verze pak zůstane prázdná a doplní se',
    '# při nejbližší příležitosti.',
    '# Nezaskrtnute policko se v HTML formulari vubec neposila, proto je',
    '# vychozi hodnota "" = vypnuto. To je zaroven duvod, proc se sem neda',
    '# dat Form(True) - to by slo zapnout, ale uz nikdy vypnout.',
    '# Heslo se do formuláře nikdy nevypisuje, takže prázdné pole znamená',
    '# "nech to, co už znám". Rozepsané nastavení je v tom pořadí první:',
    '# po otestování spojení chceme uložit přesně to, co se testovalo.',
    '# Test ani přenos nic neukládají, ale rozepsané hodnoty si podržíme -',
    '# jinak by je uživatel po každém kliknutí vyplňoval znovu.',
    '# Zapnuty kanal s nevyplnenymi udaji je tichá past: nastaveni se ulozi,',
    '# nic nehlasi chybu - a clovek se spolehne na upozorneni, ktera nikdy',
    '# neprijdou. Rekneme to hned, ne az pri prvnim poplachu.',
    '# Hodina a minuta chodí ze dvou polí; `_clamp` ohlídá rozsah a doplní',
    '# nulu, `tasks.platny_cas` je poslední pojistka - do rozvrhu se nesmí',
    '# dostat nic jiného než "HH:MM".',
    '# Cokoli mimo znamé režimy je překlep nebo podvržený formulář -',
    '# v obou případech je správná odpověď výchozí hodnota, ne uložit to.',
    '# Podklad z OSM posila prohlizece divaku na cizi server - jen kdyz',
    '# je to vyslovne "osm", cokoliv jineho je kreslena mapa.',
    '# Sezónní výzdoba: vypne ji jen výslovné "0". Formulář bez pole (jiná',
    '# karta, starší stránka) ji nechá zapnutou - výchozí stav je zapnuto.',
    '# Přehled pro všechny má název, který jde do menu. Zkontroluje se, kdykoliv',
    '# ho někdo posílá nebo přehled zapíná; neplatný se neuloží a platí ten',
    '# předchozí. Bez platného názvu se přehled nezapne - v menu by nebylo',
    '# co napsat. Ostatní nastavení se uloží i tak.',
    '# Zapínat s neplatným názvem nejde, i když si vypnutý přehled pamatuje',
    '# starý: hláška říká „nejde" a přehled nemá potichu naskočit pod jménem,',
    '# které správce právě přepsal. Zapnutý zůstane zapnutý pod dosavadním.',
    '# Kdo přehled právě zapnul, jde rovnou sestavit - zapnout a sestavit',
    '# jsou dva kroky téže věci, ne dvě různá místa k hledání.',
    '# Ctyri odpovedi, kazda jina - clovek ma z hlasky poznat, co ho ceka,',
    '# driv nez cokoliv otevre. "Neco je nove" bez upresneni by ho poslalo',
    '# hledat.',
    '# Jazyk logu se ukláda ze stejného formuláře, ale je to jiná volba:',
    '# log často čte někdo jiný, než kdo se dívá do rozhraní.',
    '# Zona se uklada, jen kdyz ji Python zna. Preklep by jinak zpusobil,',
    '# ze se casy vypisuji podle systemu, a nikdo by nevedel proc.',
    '# Prostredi procesu prepiseme rovnou - vypis casu se tim srovna',
    '# hned. Deleni dnu v SQL az po restartu, viz lifespan().',
    '# Hláška v jazyce, ve kterém se stránka hned ukáže: nově zvolený jazyk',
    '# serveru, ledaže má správce vlastní jazyk (Můj účet) - pak ten.',
    '# Zamerne NE presmerovani na stranku aplikace.',
    '#',
    '# Sablony se ctou ze souboru pri kazdem pozadavku, kod aplikace bydli',
    '# v pameti procesu. Mezi stazenim a restartem tedy bezi STARY KOD nad',
    '# NOVYMI SABLONAMI - a jakmile nova sablona chce promennou, o ktere',
    '# stary kod nevi, skonci to chybou. Presne tohle delalo po aktualizaci',
    '# z prohlizece "Internal Server Error".',
    '#',
    '# Tahle stranka je slozena tady v Pythonu: zadna sablona, zadny',
    '# kontext, nic, co by se mohlo rozejit. Pocka na novy proces a teprve',
    '# pak pusti cloveka dal. Jeji skript je vlozeny (do static/js nesmi -',
    '# ten se s verzi meni), proto jednorazovy nonce do hlavicky CSP.',
    '# Stranka je skladana v Pythonu, takze ji nechrani autoescape Jinja.',
    '# Preklady pisou cizi lide pres Weblate: znacka v nich se musi ukazat',
    '# jako text a apostrof ("It\'s taking longer") nesmi rozbit JS retezec,',
    '# jinak by funkce zkus() nevznikla a cekarna by nikdy nepustila dal.',
    '# `wait=restart`: stranka si sama pocka, az se zvedne novy proces,',
    '# a nacte se znovu. Drive to uzivatel musel odhadnout a obnovit rucne.',
    '# Chvilka na odeslani odpovedi - jinak by prohlizec dostal',
    '# preruseni spojeni misto presmerovani.',
    '# execv nahradi proces, takze zadny uklid uz nikdo neudela -',
    '# spojeni je potreba zavrit ted, dokud jeste bezime.',
    '# Kdyby execv neproslo, aspon slusne skoncime - spravce sluzby',
    '# (nebo uzivatel) aplikaci nastartuje znovu.',
    '# Do kose, ne rovnou pryc: ze statistik je divak hned, ale do noci',
    '# se da zasah vzit zpet - viz odklizeni.vrat_z_kose(). Presun bezi',
    '# ve vlakne, at velka historie nezastavi obsluhu ostatnich stranek.',
    '# Log uz zapsalo `odklizeni.zapomen_uzivatele()`. Druhy radek',
    '# o teze akci by v logu jen prekazel.',
    '# Hodina a minuta chodí ze dvou polí zvlášť, tady se z nich',
    '# zase složí "HH:MM". `_clamp` ohlídá rozsah a doplní nulu',
    '# (napsané "3" a "5" je "03:05"), `platny_cas` je poslední',
    '# pojistka: co by přesto neodpovídalo tvaru, se uloží jako',
    '# výchozí, ne jako rozbitý rozvrh.',
    '#',
    '# Náhradou při nesmyslu je to, co je uložené teď: vymazané',
    '# pole tak rozvrh nezmění. Nula by z něj tiše udělala půlnoc.',
    '# Kolik historie se necha. Ulozi se i kdyz je odklizeni vypnute -',
    '# az se zapne, ma platit cislo, ktere clovek videl na strance.',
    '# Rotace logu. Vymazane pole nema rozvrh rozbit: nahradou je ulozena',
    '# hodnota, ne nula.',
    '# `media_type` schválně octet-stream: prohlížeč soubor uloží, místo',
    '# aby se pokusil zobrazit SQL jako text ve stránce.',
    '# "recent" tu drive chybelo, takze slo spustit soubezne se scanem -',
    '# a druha uloha pak jen narazila na zamek a tise skoncila.',
    '# Název úlohy je česká konstanta z tasks.py - v anglickém',
    '# rozhraní se musí přeložit stejně jako zbytek hlášky.',
    '# Stranka si pocka a po dokonceni se obnovi sama - stejne jako',
    '# u synchronizace knihovny.',
    '# Jellyfin je jen první krok. Zbytek proběhl, takže to není chyba',
    '# celé akce - ale musí to být vidět, jinak by člověk marně čekal,',
    '# že se osiřelé záznamy dohledají.',
    '# ---------------------------------------------------------------------------',
    '# Sprava uctu',
    '#',
    '# Vsimni si, ze kazda akce ma vlastni adresu a posila se metodou POST.',
    '# Mazani pres GET (treba odkazem /smazat?id=3) je klasicka chyba: takovou',
    '# adresu si prohlizec muze nacist sam, treba pri predbeznem nacitani odkazu,',
    '# a ucet zmizi bez toho, aby na neco nekdo klikl.',
    '# ---------------------------------------------------------------------------',
    '# Jazyky, vlastní přehled ani srovnání nemají v API adresu - klíč jen',
    '# s nimi by nesměl nic a nikdo by nevěděl proč.',
    '# Jméno jen jako text: soubor poslaný pod tímhle polem by se jinak',
    '# stal jménem klíče („UploadFile(...)").',
    '# Do relace, ne do adresy: adresa se objeví v logu proxy i v historii',
    '# prohlížeče, a tohle je jediná chvíle, kdy je token čitelný.',
]


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


ALIAS = re.compile(r"(?:from\s+jellyscope\.web\s+import\s+nastaveni|import\s+jellyscope\.web\.nastaveni)\s+as\s+(\w+)")


def predpony(text: str) -> list[str]:
    """Jména, pod kterými test podbalík zná: web_nastaveni, nastaveni a vlastní aliasy (`as _wn`)."""
    return sorted({"web_nastaveni", "nastaveni", *ALIAS.findall(text)})


def ma_jmeno(modul: str, jmeno: str) -> bool:
    """Má modul atribut `jmeno`, nebo (u balíku) podmodul toho jména?"""
    m = importlib.import_module(modul)
    if hasattr(m, jmeno):
        return True
    return hasattr(m, "__path__") and importlib.util.find_spec(f"{modul}.{jmeno}") is not None


def importy_souboru(st: ast.Module, m: str) -> tuple[list[str], set[str]]:
    """Špatné importy souboru podbalíku (i vnořené ve funkci) a jeho závislosti v podbalíku.

    Každý import se převede na cílový modul - počítat tečky nestačí:
    `from ...zaklad` (o tečku víc) by prošlo a spadlo až v řídké větvi. Cíl
    musí existovat a nesmí to být web ani jiná jeho část než zaklad a sám
    podbalík.
    """
    zakazane: list[str] = []
    hrany: set[str] = set()
    for uzel in ast.walk(st):
        if isinstance(uzel, ast.Import):
            cile = [(a.name, None) for a in uzel.names]
        elif isinstance(uzel, ast.ImportFrom):
            zaklad_cile = (importlib.util.resolve_name("." * uzel.level + (uzel.module or ""), "jellyscope.web.nastaveni")
                           if uzel.level else uzel.module)
            cile = [(zaklad_cile, a.name) for a in uzel.names]
        else:
            continue
        for cil, jmeno in cile:
            if cil != "jellyscope" and not cil.startswith("jellyscope."):
                continue                 # stdlib, fastapi...
            # `from ... import web` míří na podmodul web, ne na jméno v balíku
            # jellyscope - cílem je tedy ten podmodul (a tak se i hlídá).
            if (jmeno and importlib.util.find_spec(cil) is not None
                    and hasattr(importlib.import_module(cil), "__path__")
                    and importlib.util.find_spec(f"{cil}.{jmeno}") is not None):
                cil, jmeno = f"{cil}.{jmeno}", None
            if cil == "jellyscope.web" or (cil.startswith("jellyscope.web.") and cil != "jellyscope.web.zaklad"
                                           and not cil.startswith("jellyscope.web.nastaveni")):
                zakazane.append(f"{m}: {cil} (jen web.zaklad a podbalík)")
            elif importlib.util.find_spec(cil) is None:
                zakazane.append(f"{m}: {cil} neexistuje")
            elif jmeno and not ma_jmeno(cil, jmeno):
                zakazane.append(f"{m}: {cil} nemá {jmeno}")
            if cil.startswith("jellyscope.web.nastaveni."):
                hrany.add(cil.rsplit(".", 1)[1])
            elif cil == "jellyscope.web.nastaveni" and jmeno in SOUBORY:
                hrany.add(jmeno)
    return zakazane, hrany


def routy(r) -> list[str]:
    return sorted(f"{','.join(sorted(getattr(x, 'methods', None) or []))} {x.path}" for x in routy_naplocho(r.routes))


nastaveni = importlib.import_module("jellyscope.web.nastaveni")

print("--- (f) starý soubor ---")
check(je_balik() and not STARY.exists(), "web/nastaveni.py není, podbalík web/nastaveni/ ano")

print()
print("--- (a) vstupní dveře mají všechno, co se zvenku čte ---")
VZORY = (re.compile(r"(?<![\w.])web_nastaveni\.([A-Za-z_]\w*)"),
         re.compile(r"(?<![\w.])(?:jellyscope\.)?web\.nastaveni\.([A-Za-z_]\w*)"))
FROM = re.compile(r"from\s+jellyscope\.web\.nastaveni\s+import\s+\(?([^)\n]+)\)?")
pouzite: dict[str, str] = {}
for cesta in sorted([*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
                     *(PROJECT / j for j in ("demo.py", "run.py", "manage.py"))]):
    if not cesta.is_file() or BALIK in cesta.parents or cesta == STARY or cesta.name in S_UKAZKAMI:
        continue
    text = cesta.read_text(encoding="utf-8")
    vzory = [*VZORY, *(re.compile(rf"(?<![\w.]){re.escape(a)}\.([A-Za-z_]\w*)") for a in ALIAS.findall(text))]
    for vzor in vzory:
        for jmeno in vzor.findall(text):
            pouzite.setdefault(jmeno, cesta.name)
    for skupina in FROM.findall(text):
        for kus in skupina.split("#")[0].split(","):
            if kus.strip():
                pouzite.setdefault(kus.split(" as ")[0].strip(), cesta.name)
pouzite.pop("py", None)
chybi = sorted(f"{j} ({kde})" for j, kde in pouzite.items() if j not in SOUBORY and not hasattr(nastaveni, j))
check(len(pouzite) > 5 and not chybi, f"všech {len(pouzite)} jmen jde dveřmi (chybí {chybi[:6]})")

print()
print("--- (b) každé jméno bydlí ve svém souboru, router má všechny routy ---")
check(sorted(j for js in PODMODULY.values() for j in js) == sorted(PUVODNI),
      f"mapa pokrývá všech {len(PUVODNI)} jmen nastaveni.py, každé jednou")
check(len(routy(nastaveni.router)) == ROUT, f"nastaveni.router má všech {ROUT} rout ({len(routy(nastaveni.router))})")
if je_balik():
    chybi_v, jine = [], []
    for m, jmena in PODMODULY.items():
        cesta = BALIK / f"{m}.py"
        tam = definovane(strom(cesta)) if cesta.is_file() else set()
        chybi_v += [f"{m}.{j}" for j in jmena if j not in tam]
        if cesta.is_file():
            modul = importlib.import_module(f"jellyscope.web.nastaveni.{m}")
            jine += [f"{m}.{j}" for j in jmena if j in tam and getattr(nastaveni, j, None) is not getattr(modul, j)]
    check(not chybi_v, f"jména jsou v souborech podle mapy ({chybi_v[:6]})")
    check(not jine, f"dveře ukazují na tytéž objekty jako soubory ({jine[:6]})")
    jellyfin = importlib.import_module("jellyscope.jellyfin")
    check(getattr(nastaveni, "JellyfinClient", None) is jellyfin.JellyfinClient,
          "web_nastaveni.JellyfinClient je klient z jellyfin.py (testy ho podvrhují)")
    z_souboru = sorted(r for m in S_ROUTAMI if (BALIK / f"{m}.py").is_file()
                       for r in routy(importlib.import_module(f"jellyscope.web.nastaveni.{m}").router))
    check(z_souboru == routy(nastaveni.router), "nastaveni.router spojuje přesně routy souborů")
else:
    check(False, "jména jsou v souborech podle mapy (podbalík web/nastaveni/ není)")

print()
print("--- (c) bez kruhů, relativní importy se správným počtem teček ---")
if je_balik():
    # Každý import (i vnořený ve funkci) se převede na cílový modul - počítat
    # tečky nestačí: `from ...zaklad` (o tečku víc) by prošlo a spadlo až
    # v řídké větvi. Cíl musí existovat a nesmí to být web ani jiná jeho část
    # než zaklad a sám podbalík.
    hrany: dict[str, set[str]] = {m: set() for m in SOUBORY}
    zakazane = []
    for m in SOUBORY:
        if (BALIK / f"{m}.py").is_file():
            chyby_m, hrany[m] = importy_souboru(strom(BALIK / f"{m}.py"), m)
            zakazane += chyby_m
    # Kontrola kontroly: na ukázkách se pozná, co projít má a co ne.
    for ukazka, ma_projit in (("def f():\n    from ... import web\n", False),
                              ("def f():\n    from ... import notifkace_preklep\n", False),
                              ("def f():\n    from ...zaklad import _flash\n", False),
                              ("from jellyscope.web import app\n", False),
                              ("def f():\n    from ... import notifikace\n", True),
                              ("from ..zaklad import _flash\n", True),
                              ("from .. import zaklad\n", True)):
        check((not importy_souboru(ast.parse(ukazka), "ucty")[0]) == ma_projit,
              f"kontrola importů {'pustí' if ma_projit else 'pozná'}: {ukazka.strip().splitlines()[-1].strip()}")
    check(not zakazane, f"importy míří na existující povolené moduly ({zakazane[:6]})")
    navic = {m: sorted(hrany[m] - SMI[m]) for m in SOUBORY if hrany[m] - SMI[m]}
    check(not navic, f"závislosti podle grafu ve specu (navíc {navic})")
    # Import podmodulu vždy napřed spustí __init__ balíku (a ten načte všechny
    # podmoduly) - jedna kontrola na balík pokryje všechny, víc by jen opakovalo totéž.
    for jmeno in ["jellyscope.web.nastaveni"]:
        beh = subprocess.run([sys.executable, "-c", f"import {jmeno}"], cwd=PROJECT, env=os.environ.copy(),
                             capture_output=True, text=True, timeout=180)
        chyba = (beh.stderr.strip().splitlines() or [""])[-1]
        check(beh.returncode == 0, f"`import {jmeno}` jako první v čistém procesu ({chyba[:120]})")
else:
    check(False, "bez kruhů (podbalík web/nastaveni/ není)")

print()
print("--- (d) velikost ---")
if je_balik():
    velke = [(p.name, n) for p in sorted(BALIK.glob("*.py")) if (n := len(p.read_text(encoding="utf-8").splitlines())) > 600]
else:
    velke = [("nastaveni.py", len(STARY.read_text(encoding="utf-8").splitlines()))]
check(not velke, f"žádný soubor nad 600 řádků ({velke})")

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
check(podvrhy_ve_dverich("updates.aktualizuj, web_nastaveni._naplanuj_restart = a, b", "web_nastaveni")
      == {"_naplanuj_restart"}, "(e) podvrh ve dvojici se pozná")
jmena_v = {m: definovane(strom(BALIK / f"{m}.py")) | nactena(strom(BALIK / f"{m}.py"))
           for m in SOUBORY if (BALIK / f"{m}.py").is_file()}
spatne, mimo = [], []
for cesta in sorted(PROJECT.glob("tests/*.py")):
    if cesta.name in S_UKAZKAMI:
        continue
    text = cesta.read_text(encoding="utf-8")
    for predpona in predpony(text):
        spatne += [f"{cesta.name}: {chyba}" for chyba in spatne_podvrhy(text, predpona, ctenari)]
        for m, jmeno in sorted(podvrhy_v_podmodulech(text, predpona)):
            if m in jmena_v and jmeno not in jmena_v[m]:
                mimo.append(f"{cesta.name}: {predpona}.{m}.{jmeno}")
check(not spatne, f"jména čtená v souborech se podvrhují přes podvrhni nebo ve všech čtenářích ({spatne})")
check(not mimo, f"každý podvrh v souboru míří tam, kde se jméno čte ({mimo})")

print()
print("--- (g) komentáře ---")
if je_balik():
    ted = Counter(r.strip() for p in BALIK.glob("*.py") for r in p.read_text(encoding="utf-8").splitlines()
                  if r.strip().startswith("#"))
    chybi_k = Counter(KOMENTARE_PUVODNE) - ted
    nove = sum((ted - Counter(KOMENTARE_PUVODNE)).values())
    check(not chybi_k, f"žádný komentář se neztratil (chybí {list(chybi_k)[:4]})")
    check(nove <= 30, f"nových komentářů nejvýš 30 ({nove})")
else:
    print("       čeká na podbalík (viz f)")

print()
print("--- (h) logger ---")
if je_balik():
    loggery = [(p.name, v) for p in sorted(BALIK.glob("*.py"))
               for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
    check(not loggery, f"podbalík nemá vlastní logger, loguje přes log z web/zaklad.py ({loggery})")
else:
    check(False, "logger (podbalík web/nastaveni/ není)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
