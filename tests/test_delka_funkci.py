r"""Délka funkcí - žádná funkce aplikace nemá být delší než menší modul.

Přestavba 3f rozložila čtyři nejdelší funkce (Nastavení, sběrač, analýza
souborů, import Jellystatu) na pojmenované kroky: hlavní funkce se čte
jako obsah, podrobnosti jsou o kus výš, každá pod svým jménem. Test hlídá,
aby se to tiše nevrátilo:

(a) žádná funkce ani metoda v jellyscope/ nad LIMIT řádků, kromě výjimek
    se zapsaným důvodem;
(b) každá výjimka existuje a limit pořád překračuje - jinak patří ze
    seznamu pryč (seznam se smí jen zmenšovat);
(c) čtyři rozložené hlavní funkce mají nejvýš HLAVNI_LIMIT řádků;
(d) komentáře, které byly v těch čtyřech funkcích, jsou pořád ve svém
    souboru - při rozkládání se žádný neztratil;
(e) kontrola kontroly: na uměle dlouhé funkci limit opravdu zabere.

Spec: docs/superpowers/specs/2026-11-06-slozite-funkce-design.md

Spuštění:
    .venv/bin/python tests/test_delka_funkci.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# Test jen čte zdrojáky, ale ať ho nikdo nepustí proti ostré databázi.
_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "delka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

BALIK = PROJECT / "jellyscope"
LIMIT = 150
HLAVNI_LIMIT = 90

# Klíč: cesta pod jellyscope/ a tečkované jméno (třída.metoda, vnější.vnořená).
VYJIMKY = {
    "demodata.py:seed": "generátor ukázkových dat - vymyšlený provoz, ne logika aplikace",
    "charts.py:area_chart_multi": "kreslení SVG po vrstvách; rozložení mimo přestavbu 3f",
    "stats/tituly.py:_slouc_tituly": "skládání titulů; rozložení mimo přestavbu 3f",
}
HLAVNI = {
    "web/nastaveni/stranka.py:settings_page",
    "collector.py:_store_sessions",
    "scanner/technika.py:run_tech_scan",
    "importers/jellystat.py:import_jellystat_json",
}

# Komentáře z těla čtyř funkcí před rozložením (commit 011ca3b).
KOMENTARE = {
    'web/nastaveni/stranka.py': [   # settings_page, 89 řádků
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
    ],
    'collector.py': [   # _store_sessions, 45 řádků
        '# Vzorek do prumeru rychlosti prevodu - jen kdyz se doopravdy',
        '# prevadi a nestoji to. Behem pauzy ffmpeg zpomali nebo stoji',
        '# a prumer by tvrdil, ze server nestihal.',
        '# Az kdyz nic nebezi: nenavazujeme na neco, co divak pred',
        '# chvili pauznul? Musi to byt az tady - bezici zaznam ma',
        '# prednost pred uzavrenym.',
        '# Do statistik zatim nic - jazyk se zapocita az po',
        '# MIN_LANGUAGE_SECONDS, viz vyse. Prvni vteriny',
        '# casto hraje stopa, kterou si divak hned prepne.',
        '# Relaci uz znam - pricti cas, ktery uplynul od minuleho snimku.',
        '# Orez: kdyz aplikace na hodinu vypadla, nechceme si tu hodinu',
        '# zapsat jako "sledoval". Zapocteme jen rozumny interval.',
        '# Kdy se jazyk zapocita do statistik.',
        '#',
        '# Dokud se stopy meni, hodiny se resetuji - do statistik se',
        '# dostane az kombinace, u ktere divak vydrzel aspon',
        '# MIN_LANGUAGE_SECONDS. Jakmile se jednou zapocita, uz se',
        '# nemeni: prehravani patri jazyku, se kterym se doopravdy',
        '# sledovalo, i kdyz si ho nekdo na konci prepne.',
        '#',
        '# Pocitame to tady a ne v SQL zamerne - je to rozhodovani,',
        '# ne ukladani, a v Pythonu je videt na prvni pohled.',
        '# Prvni minuty prehravani se preskakuji - viz',
        '# LANGUAGE_GRACE_SECONDS. Pocitame odsledovany cas, ne cas od',
        '# spusteni: kdyz si divak film pusti a odejde na hodinu k',
        '# obedu, pauza se do tech ctyr minut pocitat nema.',
        '# Behem uvodu hodiny porad resetujeme, takze se zacne',
        '# merit az od prvniho snimku po nem.',
        '# Dve podminky, ne jedna. Sama o sobe by prvni potvrdila',
        '# jazyk driv: hodiny se resetuji pri kazdem snimku uvodu,',
        '# takze posledni reset padne nekam TESNE PRED ctvrtou',
        '# minutu - a minuta mereni pak skonci pred patou.',
        '#',
        '# Druha podminka rika prosty pozadavek: nez se jazyk',
        '# zapocita, musi prehravani trvat aspon uvod plus tu',
        '# minutu. Kolik snimku se mezitim stihlo, uz nehraje roli.',
        '# Co se zapise do statistik. Prave potvrzena kombinace, jinak',
        '# to, co uz tam je.',
        '#',
        '# Drive to resil `CASE WHEN ? THEN ? ELSE ... END` primo v SQL.',
        '# Fungovalo to na SQLite, ale PostgreSQL potrebuje ve WHEN',
        '# pravdivostni hodnotu, ne cislo ("argument of CASE/WHEN must',
        '# be type boolean, not type smallint"). Rozhodnuti stejne patri',
        '# sem - SQL ma ukladat, ne rozhodovat.',
        '# Cokoliv, co je v databazi aktivni, ale Jellyfin uz to nehlasi, skoncilo.',
    ],
    'scanner/technika.py': [   # run_tech_scan, 40 řádků
        '# Otazniky, ne slepeny seznam - id prichazi z odpovedi Jellyfinu',
        '# a do SQL se hodnoty nikdy nevkladaji primo.',
        '# Semafor omezi, kolik ffprobe procesu bezi soucasne. Bez nej by se',
        '# u velke knihovny spustily tisice procesu naraz a stroj by stal.',
        '# Položky, u kterých soubor aspoň u jedné stopy jazyk neuvádí.',
        '# Na ty se pak doptáme Jellyfinu - viz doplnit_jazyky_z_jellyfinu().',
        '# Kontrola az tady, za semaforem: uloh je nachystano tolik,',
        '# kolik je souboru, ale soucasne jich bezi jen par. Ty',
        '# cekajici se po zastaveni jen tise preskoci, zatimco',
        '# rozpracovane doprobihaji. Zadny soubor tak neskonci',
        '# analyzovany napul.',
        '# Jeden soubor = jeden krok. Drive se `_add_progress(1)`',
        '# volalo na trech mistech uvnitr teto funkce (po prevzeti',
        '# semaforu, po analyze a po zapisu), takze hotovy soubor',
        '# posunul ukazatel o tri - a ten pak ukazoval treba 135 %.',
        '# `finally` navic zajisti, ze se krok zapocita i u souboru,',
        '# ktery skoncil chybou.',
        '# Take zapis do vlakna - u velke knihovny je tenhle blok',
        '# v jedne smycce tisickrat a kazdy zapis by jinak na chvili',
        '# zastavil obsluhu webu.',
        '# Až po analýze, ne během ní: doptat se dá na padesát položek',
        '# jedním dotazem, kdežto uprostřed smyčky by to byl jeden dotaz',
        '# na soubor. A když Jellyfin zrovna neodpovídá, je to jen chybějící',
        '# doplněk - změřená data už jsou uložená, takže se to nesmí počítat',
        '# jako neúspěch celé analýzy.',
        '# Zpětně i na to, co je změřené z dřívějška. Bez tohohle by doplnění',
        '# platilo jen pro nově analyzované soubory - tedy skoro pro nic,',
        '# protože knihovna je obvykle změřená celá už dávno. Ptáme se jen',
        '# na stopy bez značky o původu, takže na tytéž se podruhé',
        '# nedoptáváme (viz _doplnit_jazyky_polozky).',
        '# Stejný rozsah jako u analýzy: obnova jedné položky nesmí spustit',
        '# doptávání na celou knihovnu.',
        '# Co nezná ani Jellyfin, zkusíme uhodnout z názvu souboru. Až',
        '# teď a jen na tom, co zbylo: název je odhad, kdežto soubor',
        '# a knihovna jsou údaj.',
        '# Dynamicky rozsah podle Jellyfinu. Zapisuje ho sice uz',
        '# synchronizace knihovny, jenze technicka data si clovek spojuje',
        '# s TOUHLE ulohou - spustil "Analyzu souboru", videl, ze se nic',
        '# nezmenilo, a nemel duvod tusit, ze chybejici Dolby Vision',
        '# doplni az jina uloha.',
    ],
    'importers/jellystat.py': [   # import_jellystat_json, 25 řádků
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
    ],
}

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


def delky(zdrojak: str) -> dict[str, int]:
    """Tečkované jméno každé funkce (i metody a vnořené) → počet řádků."""
    vysledek: dict[str, int] = {}

    def projdi(uzel: ast.AST, cesta: tuple[str, ...]) -> None:
        for dite in ast.iter_child_nodes(uzel):
            if isinstance(dite, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                jmeno = cesta + (dite.name,)
                if not isinstance(dite, ast.ClassDef):
                    vysledek[".".join(jmeno)] = dite.end_lineno - dite.lineno + 1
                projdi(dite, jmeno)
            else:
                projdi(dite, cesta)

    projdi(ast.parse(zdrojak), ())
    return vysledek


vsechny: dict[str, int] = {}
for cesta in sorted(BALIK.rglob("*.py")):
    rel = cesta.relative_to(BALIK).as_posix()
    for jmeno, radku in delky(cesta.read_text(encoding="utf-8")).items():
        vsechny[f"{rel}:{jmeno}"] = radku

print("--- (a) žádná funkce nad limit ---")
check(len(vsechny) > 500, f"funkce se našly ({len(vsechny)})")
for klic, radku in sorted(vsechny.items()):
    if radku > LIMIT and klic not in VYJIMKY:
        check(False, f"{klic} má {radku} řádků (limit {LIMIT}) - rozlož ji na kroky")
check(all(r <= LIMIT for k, r in vsechny.items() if k not in VYJIMKY),
      f"kromě výjimek má každá funkce nejvýš {LIMIT} řádků")

print()
print("--- (b) výjimky platí ---")
for klic, duvod in VYJIMKY.items():
    check(bool(duvod.strip()), f"{klic}: výjimka má důvod")
    if klic not in vsechny:
        check(False, f"{klic}: výjimka míří na funkci, která není - smaž z VYJIMKY")
    else:
        check(vsechny[klic] > LIMIT,
              f"{klic}: pořád překračuje limit ({vsechny[klic]}), jinak smaž z VYJIMKY")

print()
print("--- (c) rozložené hlavní funkce jsou krátké ---")
for klic in sorted(HLAVNI):
    radku = vsechny.get(klic)
    check(radku is not None and radku <= HLAVNI_LIMIT,
          f"{klic} má nejvýš {HLAVNI_LIMIT} řádků ({radku})")

print()
print("--- (d) komentáře se neztratily ---")
for soubor, puvodni in KOMENTARE.items():
    ted = Counter(r.strip() for r in (BALIK / soubor).read_text(encoding="utf-8").splitlines()
                  if r.strip().startswith("#"))
    chybi = sorted((Counter(puvodni) - ted).elements())
    check(not chybi, f"{soubor}: všech {len(puvodni)} komentářů je na místě (chybí {chybi[:3]})")

print()
print("--- (e) kontrola kontroly ---")
dlouha = "def dlouha():\n" + "    x = 1\n" * LIMIT
presne = "def presne():\n" + "    x = 1\n" * (LIMIT - 1)
check(delky(dlouha) == {"dlouha": LIMIT + 1}, "funkce o řádek delší se pozná")
check(delky(presne)["presne"] <= LIMIT, "funkce přesně na limitu projde")
check(delky("class A:\n    def m(self):\n        def v():\n            pass\n")
      == {"A.m": 3, "A.m.v": 2}, "metody a vnořené funkce mají tečkované jméno")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
