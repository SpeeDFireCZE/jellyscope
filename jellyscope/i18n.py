"""Překlad rozhraní.

Aplikace je psaná česky a čeština je **zdrojový jazyk**: v šablonách jsou
rovnou české věty, ne umělé klíče jako `dashboard.title`.

```jinja
{{ _("Přehled") }}
```

Když je nastavená angličtina, funkce `t()` větu najde ve slovníku níže.
Když je nastavená čeština, vrátí ji beze změny.

Proč takhle a ne přes klíče:

* **Šablona je čitelná i bez slovníku.** Vidíš, co se vypíše, ne kód.
* **Chybějící překlad nic nerozbije** – jen se ukáže česky. U klíčů by se
  na stránce objevilo `dashboard.title`, což je horší než správná věta
  ve špatném jazyce.
* Nevýhoda: když opravíš překlep v češtině, přestane překlad sedět. Proto
  je na to test, který hlídá, že všechny klíče slovníku někde v šablonách
  opravdu existují.
"""

from __future__ import annotations

from typing import Any

# Jazyky, mezi kterými jde přepnout.
LANGUAGES = {"cs": "Čeština", "en": "English"}
DEFAULT_LANGUAGE = "cs"

# Anglické znění českých vět. Klíč = přesná česká věta ze šablony.
EN: dict[str, str] = {
    # --- navigace a společné prvky ------------------------------------
    "Přehled": "Overview",
    "Zjištění": "Insights",
    "Jazyky": "Languages",
    "Knihovna": "Library",
    "Knihovny": "Libraries",
    "Uživatelé": "Users",
    "Historie": "History",
    "Nastavení": "Settings",
    "Collector běží": "Collector running",
    "Ukázkový režim": "Demo mode",
    "Data jsou vymyšlená.": "The data is made up.",
    "Collector hlásí chybu": "Collector error",
    "Collector startuje": "Collector starting",
    "aktivních přehrávání": "active playbacks",
    "Naposledy": "Last seen",
    "Světlý / tmavý": "Light / dark",
    "Přihlášen": "Signed in",
    "správce": "administrator",
    "čtenář": "viewer",
    "Odhlásit": "Sign out",
    "Zobrazit": "Show",
    "Zrušit filtr": "Clear filter",
    "Zrušit hledání": "Clear search",
    "Předchozí": "Previous",
    "Další": "Next",
    "Strana": "Page",
    "z": "of",
    "celkem": "total",
    "Celkem": "Total",
    "záznamů": "records",
    "Zobrazit jako tabulku": "Show as table",
    "Zatím žádná data": "No data yet",
    "beze změny": "no change",
    "oproti minulému období": "vs previous period",
    "oproti předchozímu období": "vs previous period",
    "7 dnů": "7 days",
    "30 dnů": "30 days",
    "90 dnů": "90 days",
    "rok": "1 year",
    "chybí": "missing",

    # --- přehled -------------------------------------------------------
    "Právě se hraje": "Now playing",
    "Nikdo právě nic nesleduje.": "Nobody is watching anything right now.",
    "Nedávno přidané": "Recently added",
    "celá knihovna": "whole library",
    "Statistiky": "Statistics",
    "epizoda": "episode",
    "Důvod": "Reason",
    "hraje": "playing",
    "pozastaveno": "paused",
    # --- sekce nastavení -----------------------------------------------
    "Zavřít": "Close",
    "titul": "title",
    "tituly": "titles",
    "záznam": "record",
    "záznamy": "records",
    'Co se nepovedlo zařadit':
        'What could not be placed',
    'Nezařazené záznamy':
        'Unplaced records',
    'historie, ke které se nepodařilo najít titul v knihovně':
        'history with no matching title in the library',
    'Všechno je zařazené. To je vzácné – gratuluji.':
        'Everything is placed. That is rare - congratulations.',
    'Ruční přiřazení':
        'Manual assignment',
    'naposledy':
        'last time',
    'Název titulu nebo seriálu…':
        'Title or series name...',
    'Hledat v knihovně':
        'Search the library',
    'Zrušit':
        'Cancel',
    'Nic takového v knihovně není. Zkus kratší text nebo název seriálu.':
        'Nothing like that is in the library. Try a shorter text or the series name.',
    'v archivu':
        'archived',
    'Přiřadit sem':
        'Assign here',
    'Přiřadit tyhle záznamy k vybranému titulu?':
        'Assign these records to the selected title?',
    'Přiřadit ručně':
        'Assign manually',
    'Název ze záznamu':
        'Name from the record',
    'Seriál':
        'Series',
    'Záznamů':
        'Records',
    'nemá řešení':
        'no way around it',
    'Identifikátor sedí, jen jinak zapsaný':
        'The identifier matches, just written differently',
    'Titul v knihovně je - jen se jeho id píše jinak (s pomlčkami / bez nich). Spraví to Uklidit historii.':
        'The title is in the library - only its id is written differently (with or without dashes). Clean up history fixes it.',
    'Název sedí přesně na jeden titul':
        'The name matches exactly one title',
    'Mělo se navázat samo. Když to po úklidu zůstane, je to chyba a stojí za nahlášení.':
        'This should have linked itself. If it survives a cleanup, it is a bug worth reporting.',
    'Název sedí na víc titulů':
        'The name matches several titles',
    'Typicky „7. epizoda“ nebo „Pilot“ - takový díl má každý seriál. Stroj hádat nesmí, ale ty poznáš, kam to patří: zkus Dohledat v Jellyfinu, nebo přiřaď ručně.':
        'Typically "Episode 7" or "Pilot" - every series has one. The machine must not guess, but you can tell: try the Jellyfin lookup, or assign it manually.',
    'Seriál v knihovně je, díl nesedí':
        'The series is in the library, the episode is not',
    'Název nese seriál i číslo dílu, ale takový díl v knihovně není - číslování se mezi zdroji rozchází. Ruční přiřazení to vyřeší.':
        'The name carries the series and an episode number, but no such episode is in the library - the numbering differs between sources. Manual assignment solves it.',
    'Seriál známe, díl ne':
        'We know the series, not the episode',
    'Záznam ví, ze kterého seriálu je, ale díl toho jména v knihovně není.':
        'The record knows its series, but no episode of that name is in the library.',
    'Seriál v knihovně není':
        'The series is not in the library',
    'Seriál jsi nejspíš smazal. Historie zůstává platná, jen k ní nic nevede.':
        'You probably deleted the series. The history stays valid, there is just nothing behind it.',
    'Není z čeho vyjít':
        'Nothing to go on',
    'Zůstal jen název a ten se v knihovně nikde neopakuje. Titul už v knihovně není.':
        'Only a name is left and it appears nowhere in the library. The title is gone.',
    "titulů": "titles",
    "Dohledat osiřelé v Jellyfinu": "Look the orphans up in Jellyfin",
    "Dohledání se ptá Jellyfinu na identifikátory z převzaté historie – ty jsou pravé, jen k nim v knihovně nic nevede. Jellyfin z nich řekne seriál i číslo dílu, takže záznam, který nese jen „7. epizoda“, se konečně dá zařadit. Do Jellyfinu se přitom jen čte.":
        "The lookup asks Jellyfin about the identifiers in the imported history - they are genuine, there is just nothing in the library behind them. Jellyfin tells us the series and episode number, so a record that carries only \"Episode 7\" can finally be placed. Jellyfin is only read from.",
    "spuštěno.": "started.",
    "Záznamy s jiným názvem, než má titul v knihovně":
        "Records whose name differs from the title in the library",
    "Název se u přehrávání ukládá spolu se záznamem, aby historie smazaného titulu nezůstala bezejmenná. Když se ale titul přejmenuje – typicky když v Jellyfinu spravíš špatně určená metadata –, starý záznam si nese původní jméno. Ve statistikách pak k jednomu titulu patří název druhého.":
        "The name is stored together with each playback so that history of a deleted title does not end up nameless. But when a title gets renamed - typically when you fix wrongly identified metadata in Jellyfin - the old record keeps the original name. The statistics then show one title under another title's name.",
    "Rozhraní": "Interface",
    "Log aplikace": "Application log",
    "Platí pro nově zapsané řádky; co už v souboru je, zůstává, jak bylo. Hlášku, která překlad nemá, log napíše česky.":
        "Applies to newly written lines; what is already in the file stays as it was. A message with no translation is written in Czech.",
    "Vybrat soubor": "Choose file",
    "Soubor nevybrán": "No file chosen",
    "za chvíli": "in a moment",
    "právě teď": "just now",
    "před {n} min": "{n} min ago",
    "před {n} h": "{n} h ago",
    "před {n} dny": "{n} days ago",
    "{n} nejnovějších titulů (knihovna byla prázdná)": "{n} newest titles (the library was empty)",
    "{n} nových titulů (zkontrolováno {celkem})": "{n} new titles ({celkem} checked)",
    "Nic nového (zkontrolováno {celkem})": "Nothing new ({celkem} checked)",
    "Není co analyzovat.": "Nothing to analyse.",
    "i když byl na výběr jiný jazyk": "even though another language was available",
    "jiná možnost nebyla": "no other option",
    "Jiný jazyk než": "A language other than",
    "Synchronizace knihovny":
        "Library sync",
    "Stáhne z Jellyfinu seznam uživatelů, knihoven a titulů. Je-li ve Sběru dat zvolený ffprobe, naváže na ni analýza souborů, které technická data ještě nemají.":
        "Downloads the list of users, libraries and titles from Jellyfin. When ffprobe is picked in Data collection, an analysis of files without technical data follows.",
    "Stáhne jen tituly, které v knihovně ještě nejsou - podle data posledního přidaného. Jellyfin skoro nezatíží, takže plná synchronizace může běžet mnohem řidčeji. Při zdroji dat 'ffprobe' rovnou změří i nově přidané soubory, aby na technická data nečekaly do další analýzy.":
        "Downloads only titles that are not in the library yet - by the date of the last one added. It barely touches Jellyfin, so the full sync can run far less often. With ffprobe as the data source it also measures the new files right away, so they do not wait for the next analysis.",
    "Záloha databáze":
        "Database backup",
    "Uloží kopii databáze do zvolené složky a smaže přebytečné starší zálohy.":
        "Saves a copy of the database into the chosen folder and deletes surplus older backups.",
    "Blokace": "Blocked logins",
    "Blokované adresy": "Blocked addresses",
    "přihlašování, ne přístup k aplikaci": "signing in, not access to the app",
    "neúspěšných pokusech z jedné adresy se přihlašování z ní na chvíli zavře. Každá další blokace v řadě je delší:": "failed attempts from one address, signing in from it closes for a while. Each block in a row lasts longer:",
    "pak už trvale.": "and then permanently.",
    "Když se adresa": "When an address behaves for",
    "hodin chová slušně, počítá se zase od začátku.": "hours, the counting starts over.",
    "Zatím nikdo. To je dobře.": "Nobody so far. That is a good sign.",
    "Adresa": "Address",
    "Stav": "State",
    "Blokací v řadě": "Blocks in a row",
    "trvale": "permanent",
    "ještě": "still",
    "vypršelo": "expired",
    "Zablokovat natrvalo": "Block permanently",
    "Odblokovat": "Unblock",
    "Adresa se bere z připojení. Běží-li před Jellyscope reverzní proxy, vyplň v .env FORWARDED_ALLOW_IPS – jinak tu bude pokaždé adresa té proxy a blokace by platila pro všechny naráz.": "The address comes from the connection. If a reverse proxy sits in front of Jellyscope, fill in FORWARDED_ALLOW_IPS in .env - otherwise this will always be the proxy's address and a block would apply to everyone at once.",
    "Úlohy a zálohy": "Tasks and backups",
    "Soubory bez jazyka":
        "Files without a language",
    "Soubory bez určeného jazyka":
        "Files with no language set",
    "Hledat název nebo cestu...":
        "Search name or path...",
    "cesta není známá – spusť synchronizaci knihovny":
        "path unknown - run a library sync",
    "Všechny soubory mají jazyk vyplněný. To je vzácné – gratuluji.":
        "Every file has its language filled in. That is rare - congratulations.",
    "U těchhle souborů není u zvukové stopy vyplněný jazyk – Jellyfin ho hlásí jako „und“ (undefined) nebo vůbec. Do jazykových statistik proto nevstupují a v grafech by se objevily jen jako „Neuvedeno“.":
        "These files have no language on the audio track - Jellyfin reports it as und (undefined) or not at all. They therefore do not enter the language statistics, where they would only show up as Unknown.",
    "Spraví se to v souboru, ne v Jellyfinu: stopě chybí jazykový kód. U MKV ho doplníš třeba nástrojem mkvpropedit (mění jen hlavičku, nepřekódovává), pak stačí v Jellyfinu obnovit metadata titulu a v Jellyscope spustit synchronizaci.":
        "The fix belongs in the file, not in Jellyfin: the track is missing its language code. For MKV you can add it with mkvpropedit (it only rewrites the header, no re-encoding), then refresh the title metadata in Jellyfin and run a sync in Jellyscope.",

    "souborů": "files",
    "počet souborů": "file count",
    "souborů v této knihovně nemá technická data. Spusť analýzu v Nastavení.": "files in this library have no technical data. Run the analysis in Settings.",
    "souborů v této knihovně nemá technická data.": "files in this library have no technical data.",
    "Dopočítat chybějící": "Analyse the missing ones",

    "Souborů": "Files",
    "dílů": "episodes",
    "řad": "seasons",
    "řadách": "seasons",
    "Řad": "Seasons",
    "Dílů": "Episodes",
    "Řada": "Season",
    "v": "in",
    "přehráno": "played",
    "Zdroj dat": "Data source",
    "Tenhle seriál už v Jellyfinu není. Jellyscope ho nemaže – historie přehrávání na něj odkazuje.": "This series is no longer in Jellyfin. Jellyscope does not delete it - playback history points to it.",

    "hledá": "looks",
    "min zpět": "min back",
    "Čekám, až úloha doběhne – stránka se pak obnoví sama.":
        "Waiting for the task to finish - the page will reload itself.",
    "Nefunguje import přes API? Nahraj soubor pluginu":
        "API import not working? Upload the plugin's file",
    "Plugin si data ukládá do obyčejné databáze. Zkopíruj ji ze serveru s Jellyfinem a nahraj sem – obejde to rozbité API a do Jellyfinu se přitom vůbec nesahá.":
        "The plugin stores its data in a plain database file. Copy it from the Jellyfin server and upload it here - this bypasses the broken API and never touches Jellyfin itself.",
    "Nově přidané tituly":
        "Recently added titles",

    "poslední správce – nelze smazat ani si odebrat práva":
        "last administrator - cannot be deleted, nor demoted",
    "vlastní účet": "your own account",
    "Opravdu smazat účet": "Really delete the account",
    "Tenhle krok nejde vzít zpět. Nasbíraná data zůstanou, mizí jen přihlášení do Jellyscope.":
        "This cannot be undone. The collected data stays; only the Jellyscope login goes away.",
    "zbývá": "left",
    "Zbývá": "Remaining",
    "Do jazykových statistik se počítá přehrávání delší než": "Language statistics count playbacks longer than",
    "vteřin": "seconds",
    "kratší úseky o zvyklostech nic neříkají. Čas se počítá jen když se opravdu hraje, pauza ne.": "shorter stretches say nothing about habits. Time counts only while it actually plays, not while paused.",
    "Zaznamenaných přehrávání za tohle období": "Playbacks recorded in this period",
    "ale všechna jsou kratší. Zkus si pustit něco delšího, nebo se podívej za delší období.": "but all of them are shorter. Try watching something longer, or pick a longer period.",

    "Jazyk se u přehrávání započítá, až když s ním divák vydrží aspoň minutu – a první čtyři minuty se přeskakují úplně, protože v nich hrají loga a znělky. Stopa, kterou divák hned na začátku přepne, se tak do statistik nedostane.":
        "A playback's language only counts once the viewer stays with it for at least a minute - and the first four minutes are skipped entirely, because that is logos and intros. A track switched away right at the start therefore never reaches the statistics.",
    "Platí pro celou aplikaci. Změnit ho jde kdykoliv v Nastavení.":
        "Applies to the whole app. You can change it any time in Settings.",
    "Úloha právě běží.": "A task is running.",
    "čteno ze souboru": "read from file",
    "hlásí Jellyfin": "reported by Jellyfin",
    "Jaké žánry sleduje": "Which genres they watch",
    "Podíl žánrů": "Genre share",
    "v hodinách, kliknutím na titul se otevře v knihovně": "in hours, click a title to open it in the library",
    "Jeden titul má obvykle žánrů víc, takže se jeho čas počítá do každého z nich – procenta se proto nesčítají na sto. Žánry hlásí Jellyfin tak, jak je má u titulů vyplněné.": "A title usually has several genres, so its time counts towards each of them - the percentages therefore do not add up to a hundred. Genres come from Jellyfin exactly as they are filled in there.",
    "Zatím nevíme – žánry se doplní při další synchronizaci knihovny.": "Not known yet - genres get filled in during the next library sync.",

    "Zastavit úlohu": "Stop task",
    "Čekám, až se aplikace zvedne – stránka se pak obnoví sama.":
        "Waiting for the app to come back up - the page will reload itself.",
    "Čekám, až úloha dodělá rozpracovanou položku – stránka se pak obnoví sama.":
        "Waiting for the task to finish the item in progress - the page will reload itself.",
    "Trvá to déle, než je zdrávo. Zkus stránku obnovit ručně.":
        "This is taking longer than it should. Try reloading the page manually.",
    "Zastavuji – úloha dokončí rozpracovanou položku a skončí.":
        "Stopping - the task will finish the item it is working on and then end.",
    "Nepřeruší se uprostřed práce: dokončí položku, kterou má rozdělanou, a teprve pak skončí. Co se stihlo, zůstane uložené.":
        "It is not interrupted mid-work: it finishes the item it has in progress and only then ends. Whatever was done stays saved.",
    "Analýza souborů": "File analysis",
    "Obecné": "General",
    "Co se na serveru dělo za posledních": "Server activity over the last",
    "Právě teď": "Right now",
    "Uživatel": "User",
    "Co hraje": "Now playing",
    "Zařízení": "Device",
    "Způsob": "Method",
    "Běží": "Elapsed",
    "transcode": "transcode",
    "přebalení": "remux",
    "přímé": "direct",
    "Celkem odsledováno za": "Total watched over",
    "Spuštění": "Plays",
    "Aktivních uživatelů": "Active users",
    "Různých titulů": "Distinct titles",
    "Podíl transcode": "Transcode share",
    "čím míň, tím líp pro server": "lower is better for the server",
    "Sledovanost po dnech": "Watch time by day",
    "v hodinách": "in hours",
    "Den": "Day",
    "Hodiny": "Hours",
    "Nejaktivnější uživatelé": "Most active users",
    "Nejsledovanější tituly": "Most watched titles",
    "seriály jsou sečteny dohromady": "episodes are summed per series",
    "Jak server obsah doručuje": "How the server delivers content",
    "Přímé přehrávání": "Direct play",
    "Přebalení (direct stream)": "Remux (direct stream)",
    "Transcode": "Transcode",
    "Neznámé": "Unknown",
    "Přímé přehrávání nestojí server nic. Transcode znamená, že video musí za běhu překódovat – a to už procesor nebo grafická karta cítí.":
        "Direct play costs the server nothing. Transcoding means re-encoding the video on the fly - and the CPU or GPU feels that.",
    "Přehrávače": "Players",
    "Kdy se sleduje": "When people watch",
    "místní čas, výraznější barva = více hodin": "local time, stronger colour = more hours",

    # --- zjištění ------------------------------------------------------
    "Otázky, na které se dá odpovědět, jen když víš": "Questions you can only answer when you know",
    # Spojka mezi dvema zvyraznenymi kusy vety na strance Zjisteni.
    # Bez prekladu zustavala v anglickem rozhrani cesky.
    "i": "and",
    "co se sleduje": "what gets watched",
    "co ty soubory jsou": "what the files actually are",
    "Místo, které nikdo nevyužívá": "Storage nobody uses",
    "nic nepřehráno za": "nothing played in",
    "nově přidané tituly se nepočítají": "recently added titles excluded",
    "titulů": "titles",
    "z celkové velikosti knihovny": "of total library size",
    "Titul": "Title",
    "Velikost": "Size",
    "Rozlišení": "Resolution",
    "Kodek": "Codec",
    "Přidáno": "Added",
    "Jellyscope nic nemaže a nic mazat neumí. Jen ti ukáže, kde se dívat.":
        "Jellyscope never deletes anything and cannot. It only shows you where to look.",
    "Žádný nesledovaný obsah – nebo ještě nemáš dost historie.":
        "No unwatched content - or not enough history yet.",
    "Nejčastěji transcodované soubory": "Most frequently transcoded files",
    "Transcode": "Transcodes",
    "Převod těchto souborů do formátu, který tvoje přehrávače zvládnou přímo (obvykle H.264 + AAC), sundá zátěž z procesoru serveru.":
        "Converting these to a format your players handle directly (usually H.264 + AAC) takes the load off the server CPU.",
    "Žádný transcode – to je dobrá zpráva.": "No transcodes - that is good news.",
    "Proč se transcoduje": "Why it transcodes",
    "důvod hlásí přímo Jellyfin": "the reason comes from Jellyfin itself",
    "Zatím žádný transcode k rozboru.": "No transcodes to analyse yet.",
    "Díváš se na to hodně – ale ve slabé kvalitě": "Watched a lot - but in poor quality",
    "pod 1080p nebo pod 3 Mb/s": "below 1080p or below 3 Mbps",
    "Odsledováno": "Watched",
    "Kandidáti na sehnání lepší kopie.": "Candidates for a better copy.",
    "Vše, na co se díváš, je v pořádné kvalitě.": "Everything you watch is in decent quality.",
    "Zabírá hodně, sledováno skoro vůbec": "Takes a lot of space, barely watched",
    "gigabajty na jednu odsledovanou hodinu": "gigabytes per watched hour",
    "Přehrání": "Plays",
    "Kandidáti na překódování do úspornějšího formátu (např. HEVC).":
        "Candidates for re-encoding to a more efficient format (e.g. HEVC).",
    "Žádné neúměrně velké soubory.": "No disproportionately large files.",
    "Využití knihoven": "Library utilisation",
    "kolik místa zabírají versus kolik se z nich sleduje":
        "how much space they take versus how much gets watched",
    "Sledovaných titulů": "Watched titles",
    "Využití": "Utilisation",
    "Možné duplicity": "Possible duplicates",
    "shoda názvu a roku": "matching title and year",
    "Film": "Movie",
    "Kopií": "Copies",
    "Dohromady": "Combined",
    "cesty": "paths",
    "Pozor: shoda názvu není důkaz. Může jít o režisérský sestřih vedle kinoverze – proto \"možné\".":
        "Careful: a matching title is not proof. It may be a director's cut next to the theatrical version - hence \"possible\".",
    "Žádné duplicity nenalezeny.": "No duplicates found.",
    "Začaté a odložené": "Started and abandoned",
    "nikdo se nedostal dál než do 15 % délky": "nobody got past 15 % of the runtime",
    "Pokusů": "Attempts",
    "Nejdál": "Furthest",
    "Nic odloženého.": "Nothing abandoned.",

    # --- jazyky --------------------------------------------------------
    "V jakém jazyce se u vás doopravdy dívá – a co k tomu knihovna nabízí.":
        "What language people actually watch in - and what the library offers.",
    "Podíl jazyka": "Share of",
    "na odsledovaném čase": "in watched time",
    "Preferovaný jazyk": "Preferred language",
    "Uložit": "Save",
    "za": "over",
    "Poměr jazyků": "Language ratio",
    "podle odsledovaného času": "by watched time",
    "Jazyk": "Language",
    "Podíl": "Share",
    "Diváků": "Viewers",
    "Kdo v jakém jazyce sleduje": "Who watches in which language",
    "každý pruh je jeden člověk, šířka = podíl jazyka":
        "each bar is one person, width = language share",
    "převážně": "mostly",
    "Dabing, nebo originál?": "Dubbing or original?",
    "jen tam, kde víme, co bylo na výběr": "only where we know what was on offer",
    "Zatím nemáme dost dat o dostupných stopách.": "Not enough data about available tracks yet.",
    "Titulky": "Subtitles",
    "podíl času se zapnutými titulky": "share of time with subtitles on",
    "s titulky": "with subtitles",
    "bez titulků": "without subtitles",
    "Jazyk titulků": "Subtitle language",
    "Stav knihovny": "Library composition",
    "Aktuální obsah knihovny – nezávisí na zvoleném období.":
        "Current library contents - independent of the selected period.",
    "Jazyky v knihovně": "Languages in the library",
    "kolik titulů danou stopu obsahuje": "how many titles contain each track",
    "Jeden titul se počítá do každé stopy, kterou má – film s českou i anglickou stopou je v obou řádcích. Součet proto převyšuje počet titulů.":
        "A title counts towards every track it has - a movie with both Czech and English audio appears in both rows. The total therefore exceeds the number of titles.",
    "Kombinace stop": "Track combinations",
    "čtyři nejčastější, zbytek shrnutý": "four most common, rest summarised",
    "Kombinace": "Combination",
    "Titulů": "Titles",
    "dalších kombinací": "more combinations",
    "Sledujete to, ale stopa chybí": "You watch it, but the track is missing",
    "Dostupné stopy": "Available tracks",
    "celý seriál": "whole series",
    "ano": "yes",
    "ne": "no",
    "Kandidáti na sehnání jiné verze – díváte se na ně, ale zvuk v tomhle jazyce u nich Jellyfin nabídnout nemůže:":
        "Candidates for another version - you watch them, but Jellyfin has no audio in this language to offer:",
    "Odkud čísla pocházejí": "Where the numbers come from",
    "Tituly se známými jazyky stop": "Titles with known track languages",
    "Přehrávání se známým jazykem": "Playbacks with a known language",
    "Žádná jazyková data.": "No language data.",

    # --- knihovna ------------------------------------------------------
    "Vyber knihovnu a podívej se, co v ní doopravdy je.":
        "Pick a library and see what is really in it.",
    "Typ": "Type",
    "Délka obsahu": "Content length",
    "Filmy": "Movies",
    "Seriály": "TV shows",
    "Domácí videa": "Home videos",
    "Smíšený obsah": "Mixed content",
    "Ostatní": "Other",

    # Druhy položek, jak je hlásí Jellyfin (viz stats.TYPY_POLOZEK).
    # "Film" a "Řada" mají překlad jinde v tomhle slovníku - podruhé je
    # sem psát nesmíme, druhý klíč by ten první tiše přebil.
    "Mix": "Mix",
    "Druh": "Kind",
    # "Seriál" má překlad výš u osiřelých záznamů - podruhé ho sem psát
    # nesmíme, druhý klíč by ten první tiše přebil.
    "Díl seriálu": "Episode",
    "Hudba": "Music",
    "Videoklip": "Music video",
    "Album": "Album",
    "Video": "Video",
    "Upoutávka": "Trailer",
    "Živé vysílání": "Live TV",
    "Pořad": "Programme",
    "Nahrávka": "Recording",
    "Kniha": "Book",
    "Audiokniha": "Audiobook",
    "Fotka": "Photo",
    "Neznámý (z importu)": "Unknown (from import)",
    "V „Ostatní“ je": "„Other“ is made of",
    "Celkem za všechny knihovny": "Across all libraries",
    "Aktuální stav, nezávisle na období.": "Current state, independent of period.",
    "Položek celkem": "Items in total",
    "Změřeno přes ffprobe": "Measured with ffprobe",
    "přesná data ze souborů": "precise data from the files",
    "Převzato z Jellyfinu": "Taken from Jellyfin",
    "rychlé, méně detailů": "fast, less detail",
    "Bez technických dat": "Without technical data",
    "spusť analýzu v Nastavení": "run the analysis in Settings",
    "Kodeky": "Codecs",
    "počet položek": "number of items",
    "počet titulů": "number of titles",
    "podíl na velikosti knihovny": "share of library size",
    "Položek": "Items",
    "Dynamický rozsah": "Dynamic range",
    "Žádné knihovny. Spusť synchronizaci v Nastavení.":
        "No libraries. Run a synchronisation in Settings.",
    "všechny knihovny": "all libraries",
    "titulů": "titles",
    "obsahu": "of content",
    "Média": "Media",
    "Aktivita": "Activity",
    "Ve 4K": "In 4K",
    "HDR / Dolby Vision": "HDR / Dolby Vision",
    "Jazyky v této knihovně": "Languages in this library",
    "kolik titulů danou zvukovou stopu obsahuje": "how many titles contain each audio track",
    "Titul s českou i anglickou stopou se počítá do obou řádků, součet proto převyšuje počet titulů.":
        "A title with both Czech and English audio counts in both rows, so the total exceeds the number of titles.",
    "Hledat název...": "Search title...",
    "Podle velikosti": "By size",
    "Podle bitrate": "By bitrate",
    "Podle rozlišení": "By resolution",
    "Podle přehrání": "By play count",
    "Podle názvu": "By name",
    "nalezeno": "found",
    "Nic nenalezeno.": "Nothing found.",
    "za posledních 90 dnů": "over the last 90 days",
    "Nejsledovanější": "Most watched",
    "Diváci": "Viewers",
    "Poslední přehrávání": "Recent playbacks",
    "Kdy": "When",
    "Délka": "Duration",
    "Z této knihovny se zatím nic nepřehrávalo.": "Nothing has been played from this library yet.",
    "titulů v této knihovně nemá technická data. Spusť analýzu v Nastavení.":
        "titles in this library have no technical data. Run the analysis in Settings.",

    # --- detail položky ------------------------------------------------
    "Načíst metadata znovu": "Reload metadata",
    "Zeptá se Jellyfinu znovu na tenhle jeden titul. Nic se do Jellyfinu nezapisuje.":
        "Asks Jellyfin about this one title again. Nothing is written to Jellyfin.",
    "Soubor": "File",
    "Kontejner": "Container",
    "Celkový bitrate": "Total bitrate",
    "Dynamický rozsah": "Dynamic range",
    "Data změřena": "Data measured",
    "Cesta": "Path",
    "Obraz": "Video",
    "stopa": "track",
    "Popis": "Label",
    "Zvukové stopy": "Audio tracks",
    "Kanály": "Channels",
    "výchozí": "default",
    "vynucené": "forced",
    "externí soubor": "external file",
    "Formát": "Format",
    "Historie přehrávání": "Playback history",
    "diváků": "viewers",
    "Tenhle titul zatím nikdo nepřehrál.": "Nobody has played this title yet.",
    "Ostatní epizody": "Other episodes",
    "v seriálu": "in the series",
    "Epizoda": "Episode",
    "Název": "Name",
    "Data o stopách nejsou k dispozici. Spusť analýzu v Nastavení.":
        "Track data is not available. Run the analysis in Settings.",
    "Žádné zvukové stopy neznáme.": "No audio tracks known.",
    "Žádné titulky.": "No subtitles.",
    "Analýza tohoto souboru selhala": "Analysis of this file failed",

    # --- uživatelé -----------------------------------------------------
    "Kdo kolik sleduje za posledních": "Who watches how much over the last",
    "Sledovanost podle uživatele": "Watch time by user",
    "Z toho transcode": "Of that transcoded",
    "Žádní uživatelé. Spusť synchronizaci knihovny v Nastavení.":
        "No users. Run a library synchronisation in Settings.",
    "zpět na uživatele": "back to users",
    "posledních": "last",
    "Zobrazit celou historii tohoto uživatele": "Show this user's full history",

    # --- historie ------------------------------------------------------
    "Každé zaznamenané přehrávání, od nejnovějšího.": "Every recorded playback, newest first.",
    "Všichni uživatelé": "All users",
    "titulky": "subtitles",
    "Zatím žádná historie. Collector zaznamená každé přehrávání, které proběhne od chvíle, kdy Jellyscope běží. Starší data se dají naimportovat z Playback Reporting nebo Jellystatu – viz Nastavení.":
        "No history yet. The collector records every playback from the moment Jellyscope runs. Older data can be imported from Playback Reporting or Jellystat - see Settings.",

    # --- nastavení -----------------------------------------------------
    "Restartovat Jellyscope": "Restart Jellyscope",
    "Restartuje se jen Jellyscope, ne Jellyfin. Přehrávání na serveru to nijak nepřeruší.":
        "Only Jellyscope restarts, not Jellyfin. Playback on the server is not interrupted.",
    "Připojení k Jellyfinu": "Jellyfin connection",
    "Adresa serveru": "Server address",
    "API klíč": "API key",
    "Jellyfin → Ovládací panel → Rozšířené → Klíče API → \"+\"":
        "Jellyfin -> Dashboard -> Advanced -> API keys -> \"+\"",
    "Nech prázdné, pokud klíč měnit nechceš.": "Leave empty to keep the current key.",
    "Vyplněné údaje ještě nejsou uložené. API klíč si pamatuju, takže stačí kliknout na Uložit připojení.":
        "The values you entered are not saved yet. The API key is remembered, so "
        "just click Save connection.",
    "Nejdřív vyplň adresu serveru.": "Fill in the server address first.",
    "Připojení uloženo.": "Connection saved.",
    "Uložit připojení": "Save connection",
    "Otestovat spojení": "Test connection",
    "Synchronizovat knihovnu": "Synchronise library",
    "Poslední synchronizace": "Last synchronisation",
    "stav": "status",
    "Zdroj technických dat": "Technical data source",
    "odkud brát kodeky, bitrate a velikosti": "where codecs, bitrates and sizes come from",
    "Volba se týká jen údajů o souborech (kodek, rozlišení, velikost). Kdo se na co dívá, se čte z Jellyfinu vždycky – na tom tahle volba nic nemění.":
        "This choice only affects file details (codec, resolution, size). Who watches what is always read from Jellyfin - this setting does not change that.",
    "Jen Jellyfin API": "Jellyfin API only",
    "Údaje o souborech přebíráme od Jellyfinu. Funguje vždy a hned, nepotřebuje přístup k souborům ani ffmpeg. Některé údaje ale Jellyfin nehlásí vůbec, nebo jen odhadem.":
        "File details come from Jellyfin. Always works immediately, needs no file access or ffmpeg. But Jellyfin does not report some values at all, or only approximately.",
    "ffprobe + Jellyfin": "ffprobe + Jellyfin",
    "Soubory čteme přímo z disku přes ffprobe – přesné údaje včetně skutečné velikosti, HDR a počtu zvukových kanálů. Seznam titulů, uživatelé a statistiky přehrávání jdou dál z Jellyfinu. Potřebuje nainstalovaný ffmpeg a přístup k souborům.":
        "Files are read directly from disk via ffprobe - precise values including real size, HDR and channel counts. The title list, users and playback statistics still come from Jellyfin. Requires ffmpeg and access to the files.",
    "ffprobe nalezen": "ffprobe found",
    "ffprobe nenalezen": "ffprobe not found",
    "Cesta k ffprobe": "Path to ffprobe",
    "Nech prázdné, pokud je ffprobe dostupný v systémové PATH.":
        "Leave empty if ffprobe is on the system PATH.",
    "Kolik souborů analyzovat naráz": "How many files to analyse at once",
    "Vyšší číslo = rychlejší scan, ale větší zátěž disku. Při síťovém úložišti nech nízko (2–3).":
        "Higher = faster scan but heavier disk load. Keep it low (2-3) for network storage.",
    "Přepis cest": "Path mapping",
    "Potřebné jen tehdy, když Jellyfin vidí soubory na jiné cestě než Jellyscope (typicky při běhu v Dockeru). Formát JSON, například:":
        "Only needed when Jellyfin sees the files at a different path than Jellyscope (typically when running in Docker). JSON format, for example:",
    "Když obě aplikace běží na stejném stroji, nech": "When both run on the same machine, leave",
    "Sběr dat": "Data collection",
    "Jak často se ptát, co se hraje (sekundy)": "How often to ask what is playing (seconds)",
    "Nižší číslo = přesnější měření délky přehrávání, víc dotazů na server. Deset sekund je dobrý kompromis.":
        "Lower = more accurate playback length, more requests to the server. Ten seconds is a good compromise.",
    "Interval automatické synchronizace knihovny se nastavuje níže v sekci":
        "The library synchronisation interval is set below under",
    "společně s ostatními úlohami.": "together with the other tasks.",
    "Uložit nastavení": "Save settings",
    "Analýza souborů": "File analysis",
    "položek změřeno": "items measured",
    "Zdroj technických dat je nastavený na Jellyfin. Chceš-li měřit soubory přímo, přepni ve Sběru dat volbu na \"ffprobe + Jellyfin\" a ulož nastavení.":
        "The technical data source is set to Jellyfin. To measure files directly, switch the option in Data collection to \"ffprobe + Jellyfin\" and save.",
    "Analyzovat chybějící": "Analyse missing",
    "Analyzovat vše znovu": "Re-analyse everything",
    "Poslední analýza": "Last analysis",
    "hotovo": "done",
    "selhalo": "failed",
    "Úloha právě běží. Obnov stránku za chvíli.": "A task is running. Refresh in a moment.",
    "Naplánované úlohy": "Scheduled tasks",
    "běží samy, ale spustit je můžeš kdykoliv ručně":
        "they run on their own, but you can start them manually any time",
    "Úloha": "Task",
    "Automaticky": "Automatic",
    "Interval": "Interval",
    "Kdy": "When",
    "Samo se to děje po každé synchronizaci knihovny a u nově přidaných titulů. Tlačítka níž jsou na to, když nechceš čekat – nebo když potřebuješ přeměřit i soubory, které už změřené jsou (třeba po výměně souboru na stejném místě).":
        "It happens on its own after every library sync and for newly added titles. The buttons below are for when you don't want to wait - or when you need to re-measure files that already have data (after replacing a file in place, for instance).",
    "Čas": "Time",
    # Popisky pro čtečku obrazovky - vidět nejsou, ale přeložit se musí.
    "Hodina": "Hour",
    "Minuta": "Minute",
    "každý den": "every day",
    "dnes": "today",
    "zítra": "tomorrow",
    # Předložka zvlášť: v obou jazycích stojí mezi dnem a časem
    # ("zítra v 03:30" / "tomorrow at 03:30").
    "v": "at",
    "Naposledy": "Last run",
    "Další běh": "Next run",
    "zapnuto": "on",
    "vypnuto": "off",
    "běží": "running",
    "Když je pokrytí nízké, jsou i procenta výše jen o té části, kterou známe.":
        "When coverage is low, the percentages above describe only the part we know.",
    "přehrávání z importu se sem nepočítá.": "playbacks from imports are not counted here.",
    "Importy jazyk stopy často neobsahují, takže by čísla níže jen zředily na „Neuvedeno“. Platí proto to, co Jellyscope nasbíral sám, plus importy, u kterých jazyk uvedený byl. U titulků se import nepočítá vůbec – ten údaj v něm není nikdy.":
        "Imports often carry no audio track language, so they would only dilute the figures below into \"Unknown\". What counts is therefore what Jellyscope collected itself, plus those imports that did carry a language. For subtitles no import counts at all - that detail is never in one.",
    "Jazyk se zaznamenává až od chvíle, kdy Jellyscope běží. Nech ho pár dní sbírat a vrať se sem.":
        "The language is recorded only from the moment Jellyscope runs. Let it collect for a few days and come back.",
    "u kterých jazyk stopy nikdo nevyplnil. To nevypovídá o skladbě knihovny, jen o chybějících metadatech.":
        "have no track language filled in. That says nothing about the library's composition, only about missing metadata.",
    "minut": "minutes",
    "nikdy": "never",
    "při další kontrole": "at next check",
    "za": "in",
    "Složka pro zálohy databáze": "Database backup folder",
    "Nech prázdné, dokud zálohy nechceš. Složka se v případě potřeby vytvoří.":
        "Leave empty until you want backups. The folder is created if needed.",
    "Volné místo": "Free space",
    "Kolik záloh nechat": "How many backups to keep",
    "Starší se při každé záloze smažou, aby složka nerostla donekonečna.":
        "Older ones are deleted on each backup so the folder does not grow forever.",
    "Uložit úlohy": "Save tasks",
    "Spustit hned": "Run now",
    "Existující zálohy": "Existing backups",
    "Vytvořena": "Created",
    "Import historie": "History import",
    "převzetí dat z jiného nástroje": "taking over data from another tool",
    "Jellyscope zaznamenává přehrávání až od chvíle, kdy běží. Když už historii někde máš, dá se převzít. Import je možné spustit opakovaně – už naimportované záznamy se nezdvojí. Klidně použij oba zdroje i období, které už Jellyscope sám nasbíral: co v databázi je, se pozná podle uživatele, titulu a času a naimportuje se jen zbytek.":
        "Jellyscope records playbacks only from the moment it runs. If you already have history elsewhere, it can be taken over. The import can be run repeatedly - already imported records are not duplicated. Feel free to use both sources, and periods Jellyscope already collected itself: whatever is in the database is recognised by user, title and time, so only the rest is imported.",
    "Vlastních záznamů": "Own records",
    "Z Playback Reporting": "From Playback Reporting",
    "Z Jellystatu": "From Jellystat",
    "Nejstarší záznam": "Oldest record",
    "Playback Reporting (plugin Jellyfinu)": "Playback Reporting (Jellyfin plugin)",
    "Data se berou přímo přes Jellyfin, takže nemusíš nikam nahrávat žádný soubor. Plugin najdeš v Jellyfinu: Ovládací panel → Pluginy → Katalog.":
        "Data is taken directly through Jellyfin, so you do not have to upload any file. You will find the plugin in Jellyfin: Dashboard -> Plugins -> Catalogue.",
    "Ignorovat přehrávání kratší než": "Ignore playbacks shorter than",
    "sekund": "seconds",
    "Importovat": "Import",
    "Zjistit, jestli je plugin dostupný": "Check whether the plugin is available",
    "Jellystat (záloha JSON)": "Jellystat (JSON backup)",
    "V Jellystatu si vyexportuj zálohu do JSON a nahraj ji sem.":
        "Export a JSON backup from Jellystat and upload it here.",
    "sekund min.": "seconds min.",
    "Nahrát a importovat": "Upload and import",
    "Poslední import": "Last import",
    "Účty do Jellyscope": "Jellyscope accounts",
    "Tohle nejsou uživatelé Jellyfinu – to jsou účty, kterými se někdo přihlašuje sem. Když někomu založíš účet tady, do Jellyfinu se tím nedostane.":
        "These are not Jellyfin users - these are accounts for signing in here. Creating an account here gives nobody access to Jellyfin.",
    "Jméno": "Name",
    "Oprávnění": "Permissions",
    "Vytvořen": "Created",
    "Naposledy přihlášen": "Last sign-in",
    "Akce": "Actions",
    "ty": "you",
    "Udělat správcem": "Make administrator",
    "Odebrat práva": "Revoke privileges",
    "Smazat": "Delete",
    "Změnit své heslo": "Change your password",
    "Nové heslo": "New password",
    "Nové heslo znovu": "New password again",
    "Alespoň 8 znaků.": "At least 8 characters.",
    "Změnit heslo": "Change password",
    "Přidat uživatele": "Add user",
    "Uživatelské jméno": "Username",
    "3 až 32 znaků: písmena bez diakritiky, číslice, tečka, pomlčka, podtržítko.":
        "3 to 32 characters: plain letters, digits, dot, hyphen, underscore.",
    "Heslo": "Password",
    "Heslo znovu": "Password again",
    "Správce": "Administrator",
    "Smí měnit nastavení, spouštět analýzy a spravovat účty. Bez toho vidí jen statistiky – a to je pro většinu lidí správně.":
        "May change settings, run analyses and manage accounts. Without it they only see statistics - which is right for most people.",
    "Vytvořit účet": "Create account",
    "Změnit heslo jinému účtu": "Change another account's password",
    "Účet": "Account",
    "Nastavit heslo": "Set password",
    "Tvůj účet vidí statistiky, ale nastavení měnit nemůže. Heslo si změníš níže.":
        "Your account can see statistics but cannot change settings. You can change your password below.",
    "Databáze": "Database",
    "Účty": "Accounts",
    "právě používaná": "currently in use",
    "SQLite je výchozí a nic se pro něj neinstaluje – celá databáze je jeden soubor. PostgreSQL má smysl, když ho už doma provozuješ. Změna se projeví až po restartu aplikace.":
        "SQLite is the default and needs no installation - the whole database is one file. PostgreSQL makes sense if you already run one. The change takes effect after restarting the application.",
    "Jeden soubor na disku. Nic se neinstaluje, nic neběží na pozadí. Pro jednu domácnost naprosto dostačuje.":
        "One file on disk. Nothing to install, nothing running in the background. Plenty for a single household.",
    "Samostatný databázový server. Potřebuje knihovnu psycopg – nainstaluj ji příkazem":
        "A separate database server. Requires the psycopg library - install it with",
    "Soubor SQLite": "SQLite file",
    "Relativní cesta se počítá od složky projektu.": "A relative path is resolved from the project folder.",
    "Server": "Host",
    "Port": "Port",
    "Název databáze": "Database name",
    "Uživatel": "User",
    "Nech prázdné, pokud heslo měnit nechceš.": "Leave empty to keep the current password.",
    "Uložit a použít po restartu": "Save and use after restart",
    "Přenést data do vybrané databáze": "Copy data into the selected database",
    "Přenos dat cílovou databázi nejdřív vyprázdní a teprve pak do ní data zkopíruje. Ulož nastavení až potom, co přenos proběhl.":
        "The copy empties the target database first and only then writes the data. Save the settings only after the copy has finished.",
    "Tabulka": "Table",
    "Řádků": "Rows",
    "Log": "Log",
    "Log aplikace": "Application log",
    "Soubor": "File",
    "Řádků": "Lines",
    "Úroveň": "Level",
    "vše": "all",
    "Načíst znovu": "Reload",
    "zapisuje Jellyscope": "written by Jellyscope",
    "standardní výstup (supervisord)": "standard output (supervisord)",
    "chybový výstup (supervisord)": "error output (supervisord)",
    "Ve složce s logy zatím nic není. Soubor vznikne při prvním startu aplikace – zkus ji restartovat.":
        "The log folder is still empty. The file appears when the application starts - try restarting it.",
    "Na téhle úrovni v posledních řádcích nic není.":
        "Nothing at this level in the last lines.",
    "Soubor je zatím prázdný.": "The file is still empty.",
    "Zobrazuje se konec souboru. API klíč a hesla se ve výpisu nahrazují hvězdičkami, i kdyby je do logu zanesla hláška o chybě.":
        "Showing the end of the file. The API key and passwords are replaced with asterisks, even if an error message put them in the log.",
    "Kde jinde log hledat": "Where else to find the log",
    "Když aplikaci spouští systemd, posílá její výstup do journalu a do složky výše nic nepíše. Celý výpis včetně startu serveru vypíše:":
        "When systemd runs the application, it sends the output to the journal and writes nothing to the folder above. The full output, including server startup:",
    "U supervisordu je totéž ve výše nabízených souborech out.log a err.log.":
        "With supervisord the same lives in the out.log and err.log files offered above.",
    "Úklid historie": "History cleanup",
    "napraví, co v databázi zůstalo po opravených chybách":
        "repairs what past bugs left behind in the database",
    "Duplicitní záznamy vznikaly, když proti jedné databázi omylem běžely dva sběrače naráz – typicky stará verze aplikace vedle nové. Tomu už sběrač předchází sám.":
        "Duplicate records appeared when two collectors ran against one database by mistake - typically an old version of the app next to a new one. The collector now prevents that on its own.",
    "Záznamy visící na špatném dílu jsou pozůstatek chyby ve slučování podle TMDB: u epizody se bralo id celého seriálu, takže se historie všech dílů slila na jediný. Opravuje se podle názvu dílu, který v záznamu zůstal.":
        "Records attached to the wrong episode are left over from a bug in TMDB merging: for an episode it used the id of the whole series, so the history of every episode collapsed onto one. The repair uses the episode name kept in the record.",
    "Duplicitní záznamy": "Duplicate records",
    "Záznamy u špatného dílu": "Records on the wrong episode",
    "Uklidit historii": "Clean up history",
    "Pustit se to dá opakovaně – podruhé už nenajde nic. Záznamy se neslučují sečtením: dva zápisy o tomtéž přehrávání nejsou dvě zhlédnutí, takže ze skupiny zůstane ten úplnější.":
        "It can be run repeatedly - the second time it finds nothing. Records are not merged by adding up: two entries about the same playback are not two viewings, so the more complete one is kept.",
    "Nefunguje import přes API? Nahraj zálohu z pluginu":
        "API import not working? Upload a backup from the plugin",
    "Plugin si umí zálohu vyrobit sám a uloží ji jako soubor TSV. V Jellyfinu jdi na:":
        "The plugin can make a backup itself and saves it as a TSV file. In Jellyfin go to:",
    "Ovládací panel → Playback Reporting → Backup → Save backup":
        "Dashboard → Playback Reporting → Backup → Save backup",
    "Soubor pak nahraj sem – obejde to rozbité API a do Jellyfinu se přitom vůbec nesahá, čte se jen tvoje kopie.":
        "Then upload the file here - it bypasses the broken API and does not touch Jellyfin at all, only your copy is read.",
    "V archivu je navíc": "The archive holds an extra",
    "dílů, které už v Jellyfinu nejsou": "episodes that are no longer in Jellyfin",
    "do počtu nahoře se nepočítají. Historie přehrávání u nich zůstává.":
        "they are not included in the count above. Their playback history is kept.",
    "Zobrazit je": "Show them",
    "Podle čeho se pozná duplicita": "How a duplicate is recognised",
    "Za jeden a tentýž zážitek se považují dva záznamy, jen když platí všechno najednou:":
        "Two records count as one and the same viewing only when all of this holds at once:",
    "stejný uživatel a stejný titul,": "same user and same title,",
    "jejich časy se překrývají,": "their times overlap,",
    "neodporují si zařízením,": "they do not contradict each other on the device,",
    "ani jeden nepochází z importu.": "neither one comes from an import.",
    "Co se tedy NEsloučí:": "So what is NOT merged:",
    "film začatý včera večer a dokoukaný dnes – dvě sledování, dva záznamy, časy se nepřekrývají;":
        "a film started last night and finished today - two viewings, two records, the times do not overlap;",
    "film dokoukaný a hned puštěný znovu – navazující úseky překryv nejsou;":
        "a film finished and immediately replayed - adjoining stretches are not an overlap;",
    "tentýž film na televizi a na telefonu zároveň – liší se zařízením;":
        "the same film on the TV and on the phone at once - the device differs;",
    "dva různí uživatelé u téhož titulu ve stejnou chvíli.":
        "two different users on the same title at the same moment.",
    "Překryv, a ne shoda času začátku: dva sběrače zapisují každý o vteřinu jinde, takže přesná shoda by většinu duplicit minula.":
        "Overlap rather than an exact start time: two collectors each write a second apart, so an exact match would miss most duplicates.",
    "Záznamy bez položky v knihovně": "Records with no item in the library",
    "Zkusí se navázat podle názvu. Díl se tím zařadí pod svůj seriál – jinak se v přehledech tváří jako samostatný film.":
        "They are matched by name where possible. An episode then falls under its series - otherwise it looks like a standalone film in the overviews.",
    "Nezobrazuje se": "Not shown:",
    "titulů, ke kterým v knihovně ani v archivu nic nevede – ze záznamu se nedá zjistit, o co šlo.":
        "titles with no counterpart in the library or the archive - the record does not say what they were.",
    "Zkusit je navázat": "Try to match them",
    "Tatáž podívaná ze dvou zdrojů": "The same viewing from two sources",
    "Jellystat a Playback Reporting si zapisují jiný okamžik, takže se týž film objeví dvakrát s posunem. Pozná se podle stejně dlouhého přehrávání téhož titulu na témže zařízení v rámci jednoho dne.":
        "Jellystat and Playback Reporting each record a different moment, so the same film shows up twice, offset. It is recognised by an equally long playback of the same title on the same device within one day.",
    "Cesta k pg_dump": "Path to pg_dump",
    "Nech prázdné, pokud si nemá Jellyscope vybírat sám.":
        "Leave empty unless Jellyscope should not choose on its own.",
    "Server má verzi": "The server version is",
    "Nalezené nástroje": "Tools found",
    "verze": "version",
    "na tenhle server nestačí": "not enough for this server",
    "Žádný z nich server nezvládne – pg_dump umí zálohovat jen server stejné nebo starší verze. Doinstaluj klienta:":
        "None of them can handle the server - pg_dump can only back up a server of the same or an older version. Install the client:",
    "pg_dump se nenašel": "pg_dump not found",
    "Přidané díly": "Added episodes",
    "díly": "episodes",
    "Stáhnout": "Download",
    "Opravdu smazat zálohu": "Really delete backup",
    "Obnovit": "Restore",
    "Obnovit databázi ze zálohy": "Restore the database from backup",
    "Přepíše to VŠECHNA současná data. Stav před obnovou se nejdřív uloží jako další záloha a aplikace se pak restartuje.":
        "This overwrites ALL current data. The state before the restore is saved as another backup first, and the application then restarts.",
    "Jazyk rozhraní": "Interface language",
    "Volba platí pro celou aplikaci, ne jen pro tebe.":
        "The choice applies to the whole application, not just to you.",
    "Uložit jazyk": "Save language",
    "Co zůstává v .env": "What stays in .env",
    "V souboru .env už zůstává jen tajný klíč pro podepisování přihlašovacích cookies a nastavení sítě (adresa a port). Připojení k Jellyfinu, hesla i všechno ostatní je v databázi a mění se tady.":
        "The .env file now only holds the secret key for signing session cookies and the network settings (host and port). The Jellyfin connection, passwords and everything else live in the database and are changed here.",
    "Aplikace se restartuje. Počkej pár vteřin a obnov stránku.":
        "The application is restarting. Wait a few seconds and refresh.",

    # --- přihlášení ----------------------------------------------------
    "Přihlásit": "Sign in",
    "Špatné jméno nebo heslo.": "Wrong username or password.",
    "Zapomenuté heslo?": "Forgot password?",
    "Zapomenuté heslo": "Forgot password",
    "Heslo se ukládá jako otisk, zpátky ho přečíst nejde – to je jeho smysl. Nastav si nové přímo na serveru:":
        "The password is stored as a hash and cannot be read back - that is the whole point. Set a new one directly on the server:",
    "Nevíš, jaké účty existují? Vypíše je": "Not sure which accounts exist? List them with",
    "Rozumím": "Got it",
    "Vytvoř si správcovský účet": "Create an administrator account",
    "Tohle je první spuštění. Účet, který teď založíš, bude mít plná práva – další uživatele přidáš později v Nastavení.":
        "This is the first run. The account you create now will have full privileges - you can add more users later in Settings.",
    "3 až 32 znaků, bez diakritiky a mezer.": "3 to 32 characters, no accents or spaces.",
    "Alespoň 8 znaků. Uloží se jen jeho otisk, ne heslo samotné.":
        "At least 8 characters. Only its hash is stored, never the password itself.",
    "Založit účet a pokračovat": "Create account and continue",
    "Zpět na přehled": "Back to overview",

    # --- archiv a mazání -------------------------------------------------
    "V knihovně": "In library",
    "Archiv": "Archive",
    "Archivováno": "Archived",
    "Tyhle tituly už v Jellyfinu nejsou. Jellyscope je nemaže sám – historie přehrávání na ně odkazuje a bez nich by ve statistikách zůstaly bezejmenné záznamy. Když se soubor vrátí (i s jiným ItemId), Jellyscope ho podle tmdb ID zase spáruje a historie naváže.":
        "These titles are no longer in Jellyfin. Jellyscope does not delete them on "
        "its own - the playback history points at them, and without them the "
        "statistics would be full of nameless records. When the file comes back "
        "(even with a different ItemId), Jellyscope matches it by tmdb ID and the "
        "history continues.",
    "Tenhle titul už v Jellyfinu není – je v archivu. Historie přehrávání zůstává, aby ve statistikách nezůstaly bezejmenné záznamy. Když se soubor vrátí, Jellyscope ho podle tmdb ID zase spáruje.":
        "This title is no longer in Jellyfin - it is archived. The playback history "
        "stays so the statistics do not end up with nameless records. When the file "
        "comes back, Jellyscope matches it by tmdb ID.",
    "Smazat natrvalo": "Delete permanently",
    "Odstraní titul i všechna jeho přehrání z databáze. Statistiky se o ten čas sníží. Vrátit to nejde.":
        "Removes the title and all its playbacks from the database. The statistics "
        "will drop by that time. This cannot be undone.",
    "Smazat titul i historii": "Delete title and history",
    "Opravdu smazat i s historií přehrávání? Vrátit to nejde.":
        "Really delete it together with the playback history? This cannot be undone.",

    # --- rozpad filmy / seriály -------------------------------------------
    "Obojí": "Both",
    "Klikni na den a uvidíš v historii, co se ten den hrálo.":
        "Click a day to see in the history what played that day.",

    # --- zásobník spojení --------------------------------------------------
    "Samostatný databázový server. Potřebuje knihovnu psycopg – nainstaluj ji do prostředí, ve kterém aplikace běží:":
        "A standalone database server. It needs the psycopg library - install it "
        "into the environment the application runs in:",
    "Pak aplikaci restartuj. Doplněk pro connection pool je zvlášť, viz níže.":
        "Then restart the application. The connection pool extra is separate, see below.",
    "což je doplněk navíc k psycopg:": "which is an extra on top of psycopg:",
    "Samostatný databázový server. Má smysl, když ho už provozuješ – pro domácnost SQLite bohatě stačí.":
        "A standalone database server. Worth it if you already run one - for a "
        "household SQLite is plenty.",
    "ovladač psycopg chybí": "the psycopg driver is missing",
    "knihovna psycopg_pool chybí": "the psycopg_pool library is missing",
    "Pak aplikaci restartuj.": "Then restart the application.",
    "Ve formuláři jsou rozepsané hodnoty, které ještě nejsou uložené. Heslo si pamatuju, takže ho znovu vyplňovat nemusíš – stačí kliknout na Uložit.":
        "The form holds values that are not saved yet. The password is remembered, "
        "so you do not have to type it again - just click Save.",
    "Uloženo je": "Saved is",
    "ale aplikace zatím běží na": "but the application is still running on",
    "Přepne se až po restartu – tlačítko je nahoře vpravo.":
        "It switches over after a restart - the button is at the top right.",
    "Používat connection pool": "Use a connection pool",
    "Drží pár spojení otevřených a půjčuje je, místo aby se ke každému dotazu navazovalo nové. Jedna stránka si o data řekne asi dvanáctkrát – po síti je ten rozdíl znát, na stejném stroji jen malý.":
        "Keeps a few connections open and lends them out instead of opening a new "
        "one for every query. A single page asks for data about a dozen times - over "
        "a network the difference shows, on the same machine it is small.",
    "Potřebuje knihovnu": "Needs the library",
    "Když ji doinstalovat nemůžeš, nech tohle vypnuté – aplikace pojede dál, jen si bude spojení navazovat po jednom.":
        "If you cannot install it, leave this off - the application keeps working, "
        "it just opens connections one at a time.",

    # --- názvy jazyků ---------------------------------------------------
    # Zdroj je český název z languages._NAMES. Prochází sem přes
    # languages.display(), takže se odznak u relace přeloží stejně jako
    # zbytek stránky.
    "Čeština": "Czech",
    "Slovenština": "Slovak",
    "Angličtina": "English",
    "Němčina": "German",
    "Francouzština": "French",
    "Španělština": "Spanish",
    "Italština": "Italian",
    "Polština": "Polish",
    "Ruština": "Russian",
    "Ukrajinština": "Ukrainian",
    "Maďarština": "Hungarian",
    "Japonština": "Japanese",
    "Korejština": "Korean",
    "Čínština": "Chinese",
    "Portugalština": "Portuguese",
    "Nizozemština": "Dutch",
    "Švédština": "Swedish",
    "Dánština": "Danish",
    "Norština": "Norwegian",
    "Finština": "Finnish",
    "Turečtina": "Turkish",
    "Arabština": "Arabic",
    "Hindština": "Hindi",
    "Rumunština": "Romanian",
    "Řečtina": "Greek",
    "Hebrejština": "Hebrew",
    "Thajština": "Thai",
    "Vietnamština": "Vietnamese",
    "Indonéština": "Indonesian",
    "Bulharština": "Bulgarian",
    "Chorvatština": "Croatian",
    "Srbština": "Serbian",
    "Slovinština": "Slovenian",
    "Katalánština": "Catalan",
    "Perština": "Persian",
    "Neuvedeno": "Not specified",
    "ostatní": "other",
    "Ostatní": "Other",

    # --- popisky knihoven a dnů -----------------------------------------
    "Filmy": "Movies",
    "Seriály": "TV shows",
    "Domácí videa": "Home videos",
    "Smíšený obsah": "Mixed content",
    "Po": "Mon",
    "Út": "Tue",
    "St": "Wed",
    "Čt": "Thu",
    "Pá": "Fri",
    "So": "Sat",
    "Ne": "Sun",

    # --- hlášky po odeslání formuláře -----------------------------------
    # Procházejí přes web._flash(), který je překládá v okamžiku vzniku.
    "Účet vytvořen. Vítej v Jellyscope.": "Account created. Welcome to Jellyscope.",
    "Nastavení uloženo.": "Settings saved.",
    "Nastavení úloh uloženo.": "Task settings saved.",
    "Nastavení databáze uloženo. Změna se projeví po restartu aplikace.":
        "Database settings saved. The change takes effect after a restart.",
    "Připojení uloženo. Otestuj ho tlačítkem vedle.":
        "Connection saved. Test it with the button next to it.",
    "Aplikace se restartuje. Počkej pár vteřin a obnov stránku.":
        "The application is restarting. Wait a few seconds and refresh the page.",
    "Přepis cest není platný JSON - nechal jsem původní hodnotu.":
        "The path mapping is not valid JSON - I kept the original value.",
    "Jiná úloha už běží, počkej na její dokončení.":
        "Another task is already running, wait for it to finish.",
    "Synchronizace knihovny spuštěna - občas obnov stránku.":
        "Library sync started - refresh the page now and then.",
    "Analýza souborů spuštěna - občas obnov stránku.":
        "File analysis started - refresh the page now and then.",
    "Zdroj technických dat je nastavený na Jellyfin. ":
        "The technical data source is set to Jellyfin. ",
    "Přepni ho na ffprobe a ulož nastavení.": "Switch it to ffprobe and save the settings.",
    "Neznámá úloha.": "Unknown task.",
    "Soubor je prázdný.": "The file is empty.",
    "Soubor je větší než 200 MB.": "The file is larger than 200 MB.",
    "Import selhal.": "Import failed.",
    "Přenos selhal.": "Transfer failed.",
    "Heslo změněno.": "Password changed.",
    "Oprávnění změněno.": "Permissions changed.",
    "Vlastní účet smazat nemůžeš.": "You cannot delete your own account.",
    "Účet smazán.": "Account deleted.",

    # --- chyby z accounts.py --------------------------------------------
    "Jméno musí mít 3 až 32 znaků a smí obsahovat jen písmena bez diakritiky, číslice, tečku, pomlčku a podtržítko.":
        "The name must be 3 to 32 characters and may contain only unaccented "
        "letters, digits, a dot, a hyphen and an underscore.",
    "Hesla se neshodují.": "The passwords do not match.",
    "Účet neexistuje.": "The account does not exist.",
    "Nemůžeš odebrat práva poslednímu správci.":
        "You cannot remove privileges from the last administrator.",
    "Posledního správce smazat nelze.": "The last administrator cannot be deleted.",
    "Původní heslo nesouhlasí.": "The current password is not correct.",

    # --- hlasky po dokoncene akci (viz web._flash) ------------------------
    # Cislo nebo jmeno se do vety dosazuje az PO prekladu, proto {znacky}.
    "{uloha}: {stav}": "{uloha}: {stav}",
    "Synchronizace knihovny spuštěna.": "Library sync started.",
    "Analýza souborů spuštěna.": "File analysis started.",
    "Žádná úloha zrovna neběží.": "No task is running right now.",
    "Pokyn k zastavení předán. Úloha dokončí rozpracovanou položku a skončí - "
    "stránka se pak obnoví sama.":
        "The stop request has been passed on. The task will finish the item "
        "it is on and stop - the page then refreshes itself.",
    "Zdroj technických dat je nastavený na Jellyfin. Přepni ho na ffprobe "
    "a ulož nastavení.":
        "The technical data source is set to Jellyfin. Switch it to ffprobe "
        "and save the settings.",
    "Aplikace se restartuje. Stránka se obnoví sama, jakmile bude nahoře.":
        "The application is restarting. The page refreshes itself once it is up.",

    # import historie
    "Playback Reporting: naimportováno {n} záznamů "
    "(z {nalezeno} nalezených, {duplicit} už existovalo).":
        "Playback Reporting: imported {n} records "
        "(out of {nalezeno} found, {duplicit} already existed).",
    "Playback Reporting (záloha): naimportováno {n} záznamů "
    "(z {nalezeno} nalezených, {duplicit} už existovalo).":
        "Playback Reporting (backup file): imported {n} records "
        "(out of {nalezeno} found, {duplicit} already existed).",
    "Jellystat: naimportováno {n} záznamů "
    "(z {nalezeno} nalezených, {duplicit} už existovalo).":
        "Jellystat: imported {n} records "
        "(out of {nalezeno} found, {duplicit} already existed).",
    "Dohledáno {co} ({n} záznamů).": "Matched {co} ({n} records).",
    "{n} podle tmdb ID": "{n} by tmdb ID",
    "{n} podle čísla dílu": "{n} by episode number",
    "{n} podle názvu": "{n} by name",
    "{n} záznamů už v databázi bylo z jiného zdroje (z collectoru nebo "
    "z druhého importu), takže se nezdvojily.":
        "{n} records were already in the database from another source (the "
        "collector or a second import), so nothing was duplicated.",
    "Soubor je větší než {n} MB.": "The file is larger than {n} MB.",

    # uklid historie a dohledavani osirelych zaznamu
    "Úklid historie: {co}": "History cleanup: {co}",

    # --- narovnani dat (jedno tlacitko + stejnojmenna uloha) -------------
    "Narovnání dat": "Data tidy-up",
    "{n} starších záznamů se přeneslo ze seriálu na konkrétní díl.":
        "{n} older records were moved from the series to a specific episode.",
    "Nejdřív se Jellyfinu ukážou identifikátory z převzaté historie – ty jsou pravé, jen k nim v knihovně nic nevede. Jellyfin z nich řekne seriál i číslo dílu, takže záznam, který nese jen „7. epizoda“, se konečně dá zařadit. Do Jellyfinu se přitom jen čte. Teprve na tom stojí zbytek: navázání podle názvu, slučování duplicit a srovnání jmen.":
        "First the identifiers from the imported history are shown to "
        "Jellyfin – they are genuine, only nothing in the library leads to "
        "them. Jellyfin names the series and the episode number, so a record "
        "that carries just „7th episode“ can finally be placed. Jellyfin is "
        "only ever read from. The rest builds on that: linking by name, "
        "merging duplicates and aligning names.",

    # --- sit (stranka /network) ------------------------------------------
    "Síť": "Network",

    # --- mapa (GeoLite2) --------------------------------------------------
    "Mapa": "Map",

    # --- verze a hlidani noveho vydani ------------------------------------
    "Velikost celkem": "Total size",
    "všechny knihovny dohromady": "all libraries together",
    "Přehrávání jsou skrytá – je jich hodně": "Playbacks are hidden – there are many",
    "Diváci jsou skrytí – je jich hodně": "Viewers are hidden – there are many",
    "zobrazit všech": "show all",
    "Zobrazit všechny": "Show all",
    "Zobrazit všechna": "Show all",
    "Načíst metadata znovu": "Reload the metadata",
    "Metadata načtena znovu: {n} dílů": "Metadata reloaded: {n} episodes",
    "(včetně změření souborů)": "(including measuring the files)",
    "Obraz": "Video",
    "Zvuk": "Audio",
    "beze změny, jen se přebaluje": "unchanged, only repackaged",
    "přepočítává se": "being re-encoded",
    "Titulky se vypalují do obrazu": "Subtitles are burned into the picture",
    "Hardwarově": "Hardware accelerated",
    "Dlouhé seznamy a mapa": "Long lists and the map",
    "co karta ukáže rovnou a jak se ovládá mapa":
        "what a card shows straight away, and how the map is controlled",
    "Přibližování mapy": "Zooming the map",
    "Klikáním": "By clicking",
    "Kolečkem": "With the wheel",
    "Kliknutím do mapy se přiblíží na to místo, s Altem oddálí; nad mapou jsou i tlačítka + a −. Kolečko mapa vůbec nechytá, takže stránka roluje jako všude jinde.":
        "Clicking the map zooms in on that spot, Alt-click zooms out, and "
        "there are + and - buttons above the map. The map ignores the wheel "
        "entirely, so the page scrolls the same as everywhere else.",
    "Pohodlnější, ale dokud je kurzor nad mapou, stránka se kolečkem neposune – rolování si mapa vezme pro sebe.":
        "More comfortable, but while the cursor is over the map the page will "
        "not scroll - the map takes the wheel for itself.",
    "Přiblížit": "Zoom in",
    "Oddálit": "Zoom out",
    "Celý svět": "The whole world",
    "Kolečkem přiblížíš, tažením posuneš, dvojklik vrátí celý svět.":
        "Scroll to zoom, drag to pan, double-click for the whole world.",
    "Klikáním přiblížíš, tažením posuneš, dvojklik vrátí celý svět.":
        "Click to zoom, drag to pan, double-click for the whole world.",
    "Dlouhé seznamy": "Long lists",
    "kolik položek karta vypíše, než zbytek schová do okna":
        "how many items a card lists before it folds the rest into a dialog",
    "Každý další stream (nebo další divák) posouvá zbytek stránky dolů. "
    "Nad tímhle počtem se seznam schová za tlačítko a otevře se v okně. "
    "Kolik se vejde na obrazovku, víš líp než aplikace – proto je to tady, "
    "a ne natvrdo v kódu.":
        "Every extra stream (or viewer) pushes the rest of the page down. "
        "Above this count the list hides behind a button and opens in a "
        "dialog. How much fits on your screen is something you know better "
        "than the app - which is why this is here and not baked into the code.",
    "Přehrávání na Přehledu": "Playbacks on the Overview",
    "Diváků v jazykových statistikách": "Viewers in the language statistics",
    "Nad tento počet se „Právě se hraje“ schová do okna.":
        "Above this count „Now playing“ folds into a dialog.",
    "Nad tento počet se pruhy „Kdo v jakém jazyce sleduje“ schovají do okna.":
        "Above this count the „Who watches in which language“ bars fold into "
        "a dialog.",
    "Verze": "Version",

    # --- filtr v historii -------------------------------------------------
    "Filtr": "Filter",
    "Filtr historie": "History filter",
    "Období": "Period",
    "posledních 7 dní": "last 7 days",
    "posledních 30 dní": "last 30 days",
    "posledních 90 dní": "last 90 days",
    "poslední rok": "last year",
    "vlastní…": "custom…",
    "dnes": "today",
    "Od": "From",
    "Do": "To",
    "Přehrávač": "Player",
    "jakýkoliv": "any",
    "všichni": "everyone",
    "neuvedený": "not given",
    "zrušit den": "clear the day",
    "běží": "running",
    "Hlídat novou verzi": "Watch for a new version",
    "Jednou denně se zeptá GitHubu, jestli nevyšlo novější vydání. Nic "
    "neinstaluje – jen to řekne. Je to jediné spojení jinam než na Jellyfin, "
    "proto je ve výchozím stavu vypnuté.":
        "Once a day it asks GitHub whether a newer release is out. It "
        "installs nothing – it only tells you. This is the only connection "
        "anywhere other than Jellyfin, which is why it is off by default.",
    "Zkontrolovat teď": "Check now",
    "Naposledy kontrolováno": "Last checked",
    "nejnovější vydání": "latest release",
    "Je k dispozici verze": "Version available",
    "Je k dispozici novější verze": "A newer version is available",
    "Co je v ní nového": "What is new in it",
    "Aktualizuje se na serveru příkazem": "On the server it updates with",
    "Je k dispozici verze {verze}.": "Version {verze} is available.",
    "Máš nejnovější verzi.": "You have the latest version.",
    "Kontrolu se nepovedlo provést: {duvod}": "The check failed: {duvod}",
    "Uloženo.": "Saved.",

    # --- souhrn nahore na strance serialu --------------------------------
    "O seriálu": "About the series",
    "Průměrně na díl": "Average per episode",
    "Poslední přibyl díl": "Latest episode added",
    "Umístění": "Location",
    "velikost tečky odpovídá odsledovanému času":
        "the size of a dot follows the time watched",
    "Země": "Country",
    "Míst": "Places",
    "stažena": "downloaded",
    "Aktualizovat": "Update",
    "Stáhnout databázi GeoLite2": "Download the GeoLite2 database",
    "{misto}: {n}× · {lidi} lidí": "{misto}: {n}× · {lidi} people",
    "Umisťují se jen veřejné adresy – ta z domácí sítě žádné místo "
    "neoznačuje. Přesnost je podle GeoLite2: u pevných linek sedí město, "
    "u mobilních sítí ukáže klidně střed země. Na otázku „dívá se někdo "
    "z ciziny?“ to stačí, na hledání lidí ne.":
        "Only public addresses are placed – one from the home network marks "
        "no place at all. The accuracy is GeoLite2's: on fixed lines the city "
        "is right, on mobile networks it may point at the middle of the "
        "country. Enough for „is anyone watching from abroad?“, not for "
        "finding people.",
    "Mapa potřebuje knihovnu maxminddb, která není povinnou součástí "
    "aplikace. Doinstaluj ji do téhož prostředí, ve kterém Jellyscope běží:":
        "The map needs the maxminddb library, which is not a required part of "
        "the app. Install it into the same environment Jellyscope runs in:",
    "Mapa potřebuje databázi GeoLite2 – jeden soubor, který leží v data/ "
    "a odpovídá bez internetu. Stáhne se na kliknutí (asi 65 MB); je to "
    "jediná chvíle, kdy aplikace sáhne jinam než na Jellyfin.":
        "The map needs the GeoLite2 database – a single file that sits in "
        "data/ and answers without the internet. One click downloads it "
        "(about 65 MB); it is the only moment the app reaches anywhere other "
        "than Jellyfin.",
    "Data © MaxMind, GeoLite2 (CC BY-SA 4.0). Stahuje se ze zrcadla na "
    "GitHubu, protože u MaxMinda samotného je k tomu potřeba účet a klíč.":
        "Data © MaxMind, GeoLite2 (CC BY-SA 4.0). It comes from a GitHub "
        "mirror, because MaxMind itself asks for an account and a key.",
    "Databáze je připravená, ale žádné přehrávání z veřejné adresy zatím "
    "není – všechno šlo z domácí sítě.":
        "The database is ready, but there is no playback from a public "
        "address yet – everything came from the home network.",
    "Databáze GeoLite2 stažena ({velikost}).":
        "The GeoLite2 database has been downloaded ({velikost}).",
    "Stažení se nepovedlo: {duvod}": "The download failed: {duvod}",
    "Chybí knihovna maxminddb – bez ní se databáze nepřečte.":
        "The maxminddb library is missing – the database cannot be read "
        "without it.",
    "Kolik dat teklo ze serveru k přehrávačům – a kdy to bylo nejvíc.":
        "How much data left the server for the players – and when it peaked.",
    "Počítá se z bitrate, který Jellyfin hlásí u každého přehrávání – při "
    "překódování z výsledného toku, jinak ze zdrojového souboru. Je to tedy "
    "poctivý odhad, ne měření drátu: přeskakování, buffer a pauzy skutečná "
    "čísla posouvají. Přesně to umí změřit jen reverzní proxy nebo počítadla "
    "systému.":
        "Computed from the bitrate Jellyfin reports for each playback – the "
        "resulting stream when it transcodes, the source file otherwise. So "
        "it is an honest estimate, not a measurement of the wire: seeking, "
        "buffering and pauses move the real numbers. Only a reverse proxy or "
        "the system counters can measure that exactly.",
    "Špička": "Peak",
    "Přeneseno celkem": "Transferred in total",
    "Z toho překódovaných": "Of that transcoded",
    "Průměrný tok jednoho streamu": "Average bitrate of one stream",
    "Souběžný tok v čase": "Concurrent throughput over time",
    "nejvyšší hodnota v každém úseku": "the highest value in each slice",
    "Kdo nejvíc streamoval": "Who streamed the most",
    "Podle přehrávače": "By player",
    "Odkud se dívají": "Where they watch from",
    "podle adresy, kterou hlásí Jellyfin": "by the address Jellyfin reports",
    "Z domácí sítě": "From the home network",
    "Z internetu": "From the internet",
    "Neznámo odkud": "Origin unknown",
    "z importu – adresu nenesou": "from imports – they carry no address",
    "Odkud": "Origin",
    "Lidí": "People",
    "Přeneseno": "Transferred",
    "domácí síť": "home network",
    "internet": "internet",
    "přehrávání": "playbacks",
    "Mbit/s": "Mbit/s",
    "Mapa tu není schválně: adresa z domácí sítě (192.168.x.x) žádné místo "
    "na světě neoznačuje – je stejná v Praze i v Sydney. Zeměpisně jde "
    "umístit jen veřejná adresa, a i to potřebuje offline databázi GeoIP. "
    "Dokud se všichni dívají z domova, řekne rozdělení výš víc než mapa "
    "s jedním bodem.":
        "There is no map on purpose: an address from the home network "
        "(192.168.x.x) marks no place in the world – it is the same in "
        "Prague and in Sydney. Only a public address can be placed on a map, "
        "and even that needs an offline GeoIP database. As long as everyone "
        "watches from home, the split above says more than a map with one dot.",
    "Importovaná data nemusí obsahovat jazyk zvukové stopy ani důvod "
    "transcode. Playback Reporting jazyk občas pošle – takový záznam se do "
    "jazykových statistik počítá. Jellystat ho neposílá nikdy. Co v importu "
    "chybí, doplní se až u přehrávání, která zaznamená Jellyscope sám.":
        "Imported data need not contain the audio language or the transcode "
        "reason. Playback Reporting does send the language sometimes – such "
        "a record does count towards the language statistics. Jellystat never "
        "sends it. Whatever the import lacks is filled in by playbacks "
        "Jellyscope records itself.",
    "Připojuješ se k databázi, ve které data už jsou (třeba po "
    "přeinstalování aplikace)? Pak přenos NEspouštěj – stačí Uložit "
    "nastavení a restartovat. Aplikace se na ni připojí a data v ní zůstanou.":
        "Connecting to a database that already holds data (after "
        "reinstalling the app, say)? Then do NOT run the transfer – just "
        "Save the settings and restart. The app connects to it and the data "
        "stays where it is.",
    "Narovnat data": "Tidy the data",
    "srovná historii i archiv knihovny":
        "puts the history and the library archive straight",
    "Kromě historie srovná i knihovnu: díl, jehož soubor se v Jellyfinu vyměnil, se vrátí z archivu k tomu živému i s odsledovaným časem. Samotná synchronizace knihovny tohle nedělá.":
        "It straightens the library as well as the history: an episode whose file was replaced in Jellyfin comes back from the archive to the live one, watched time and all. A library sync on its own does not do this.",
    "Narovnání dat: {co}": "Data tidy-up: {co}",
    "Nebylo co narovnávat, historie je v pořádku.":
        "There was nothing to tidy, the history is in order.",
    "Zbývá {n} nezařazených záznamů.": "{n} records remain unassigned.",
    "Jellyfin neodpověděl ({duvod}), zbytek proběhl.":
        "Jellyfin did not answer ({duvod}), the rest went through.",
    "z Jellyfinu zařazeno: {n} titulů": "assigned from Jellyfin: {n} titles",
    "navázáno podle názvu: {n} záznamů": "linked by name: {n} records",
    "navázáno podle čísla dílu: {n} záznamů":
        "linked by episode number: {n} records",
    "vráceno ke správným dílům: {n}": "moved back to the right episodes: {n}",
    "sloučeno duplicit: {n}": "duplicates merged: {n}",
    "vráceno z archivu k živým dílům: {n}":
        "returned from the archive to live episodes: {n}",
    "sloučeno napříč zdroji importu: {n}": "merged across import sources: {n}",
    "srovnáno názvů podle knihovny: {n}": "names aligned with the library: {n}",
    "Ručně to mačkat nemusíš – totéž dělá úloha „Narovnání dat“ výš, "
    "každý den ve zvolený čas.":
        "You do not have to press this – the „Data tidy-up“ task above does "
        "the same thing every day at the time you pick.",
    "Srovná historii i knihovnu: dohledá v Jellyfinu záznamy, ke kterým "
    "nic nevede, naváže je podle názvu a čísla dílu, sloučí duplicity, "
    "srovná názvy - a vrátí z archivu díly, které v knihovně zase jsou "
    "(typicky po výměně souboru). Nic nemaže - jen opravuje vazby.":
        "Puts both the history and the library straight: asks Jellyfin "
        "about records that lead nowhere, links them by name and episode "
        "number, merges duplicates, aligns names - and brings back from "
        "the archive the episodes that are in the library again (typically "
        "after a file was replaced). It deletes nothing - it only repairs "
        "the links.",
    "nic k opravě, historie je v pořádku.":
        "nothing to fix, the history is in order.",
    "Není co dohledávat - osiřelé záznamy tu nejsou.":
        "Nothing to look up - there are no orphaned records.",
    "Jellyfin nezná ani jeden z {n} titulů. Jsou to tituly, které v knihovně "
    "už nejsou.":
        "Jellyfin knows none of the {n} titles. They are titles that are no "
        "longer in the library.",
    "Jellyfin zná {n} z {celkem}": "Jellyfin knows {n} out of {celkem}",
    "navázáno na knihovnu: {n} titulů": "linked to the library: {n} titles",
    "doplněno do knihovny: {n} titulů": "added to the library: {n} titles",
    "doplněn seriál u {n} titulů": "series filled in for {n} titles",
    "celkem {n} záznamů": "{n} records in total",
    "Přiřazeno k „{nazev}“ – {n} záznamů.": "Assigned to „{nazev}“ – {n} records.",

    # polozka, zalohy, databaze
    "Metadata načtena znovu: {nazev}": "Metadata reloaded: {nazev}",
    "(včetně změření souboru)": "(including measuring the file)",
    "(soubor se změřit nepodařilo - viz Log)":
        "(the file could not be measured - see the Log)",
    "Smazáno: {nazev} (a {n} záznamů v historii).":
        "Deleted: {nazev} (and {n} records from the history).",
    "Záloha {nazev} smazána.": "Backup {nazev} deleted.",
    "Takovou zálohu se nepodařilo najít.": "That backup could not be found.",
    "Databáze obnovena ze zálohy {nazev}. Stav před obnovou zůstal uložený "
    "jako {zaloha}. Aplikace se restartuje.":
        "The database has been restored from backup {nazev}. The state before "
        "the restore was saved as {zaloha}. The application is restarting.",
    "Cílová databáze není dostupná: {duvod}":
        "The target database is not reachable: {duvod}",
    "Přeneseno {n} řádků. Ulož nastavení a restartuj, aby se aplikace na "
    "novou databázi přepnula.":
        "Transferred {n} rows. Save the settings and restart so the "
        "application switches to the new database.",
    "Neukládám - spojení nefunguje: {duvod}":
        "Not saving - the connection does not work: {duvod}",

    # jellyfin, blokace, ucty, jazyky
    "Spojení v pořádku: {server} (Jellyfin {verze})":
        "Connection works: {server} (Jellyfin {verze})",
    "Spojení selhalo: {duvod}": "The connection failed: {duvod}",
    "Chybí adresa.": "The address is missing.",
    "Adresa {ip} je odblokovaná.": "Address {ip} has been unblocked.",
    "Adresa {ip} je zablokovaná natrvalo.":
        "Address {ip} has been blocked permanently.",
    "Taková blokace v seznamu není.": "There is no such block in the list.",
    "Účet '{jmeno}' vytvořen.": "Account '{jmeno}' created.",
    "Preferovaný jazyk: {jazyk} ✓": "Preferred language: {jazyk} ✓",
    "Tenhle jazyk v knihovně není.": "That language is not in the library.",

    # Hlasky slozene ze sablony a hodnoty. Preklada se sablona, cislo nebo
    # jmeno se dosazuje az potom - hotova veta by se ve slovniku nenasla.
    # Viz accounts.AccountError.prelozena() a web._blokace_hlaska().
    "Heslo musí mít aspoň {n} znaků.":
        "The password must be at least {n} characters.",
    "Účet '{jmeno}' už existuje.": "An account named '{jmeno}' already exists.",
    "Příliš mnoho pokusů. Zkus to za {n} min.":
        "Too many attempts. Try again in {n} min.",
    "Příliš mnoho pokusů. Zkus to za {n} s.":
        "Too many attempts. Try again in {n} s.",
    "Přihlašování z této adresy je zablokované. "
    "Odblokovat ho může správce v Nastavení.":
        "Signing in from this address is blocked. "
        "An administrator can lift it in Settings.",
}


# ---------------------------------------------------------------------------
# Hlášky do logu
# ---------------------------------------------------------------------------
#
# Log je psaný česky stejně jako zbytek aplikace. Kdo si v Nastavení zvolí
# anglický log, dostane překlad odsud - klíčem je **přesná hláška ze zdrojáku
# včetně zástupných znaků** (%s, %d), takže překlad musí mít tytéž zástupné
# znaky ve stejném pořadí.
#
# Chybějící překlad nic nerozbije: hláška se prostě zapíše česky. Stejné
# pravidlo jako u rozhraní - viz úvod tohohle souboru.
#
# Překládá se až při zápisu do souboru (viz applog.py), ne v místě volání.
# Díky tomu zůstávají volání `log.info(...)` čitelná a nikdo si při psaní
# nové hlášky nemusí pamatovat na překlad.
LOG_EN: dict[str, str] = {
    'Jellyfin neodpovedel vcas na %s, zkousim znovu (%d/%d)':
        'Jellyfin did not answer %s in time, retrying (%d/%d)',
    'Podpisový klíč nejde uložit (%s). Použil jsem dočasný - po restartu bude potřeba se přihlásit znovu.':
        'The signing key cannot be saved (%s). Using a temporary one - everyone will have to sign in again after a restart.',
    'SECRET_KEY nebyl nastaven, vyrobil jsem náhodný a uložil ho do %s. Přihlášení tím zůstává v bezpečí; kdo chce klíč spravovat sám, ať ho vyplní v .env.':
        'SECRET_KEY was not set, so a random one was generated and stored in %s. Sign-in stays safe; set it in .env to manage the key yourself.',
    'casovou zonu %r se nepodarilo nastavit':
        'time zone %r could not be set',
    'databaze obnovena ze zalohy %s':
        'database restored from backup %s',
    'databaze pripravena':
        'database ready',
    'dohledano v Jellyfinu: %s z %s dotazanych, navazano %s, zalozeno %s, doplnen serial u %s':
        'looked up in Jellyfin: %s of %s asked, linked %s, created %s,'
        ' series filled in for %s',
    'databaze GeoLite2 stazena (%s MB)':
        'the GeoLite2 database has been downloaded (%s MB)',
    'databazi GeoLite2 se nepodarilo stahnout: %s':
        'the GeoLite2 database could not be downloaded: %s',
    'adresu %s se nepodařilo umístit: %s':
        'address %s could not be located: %s',
    'import: %s zaznamu preneseno ze serialu na konkretni dil':
        'import: %s records moved from the series to a specific episode',
    'navazano na prerusene prehravani %s - pauza, ne nove spusteni':
        'continued the interrupted playback %s - a pause, not a new start',
    'je k dispozici nova verze %s (bezi %s)':
        'a new version %s is available (running %s)',
    'kontrolu verze se nepodarilo provest: %s':
        'the version check could not be made: %s',
    'obnoven serial %s: %s dilu':
        'series %s refreshed: %s episodes',
    'zapomenuto %s obrazku, ktere uz v Jellyfinu neplati':
        'forgot %s images that no longer match Jellyfin',
    'z archivu slouceno: %s -> %s': 'merged from the archive: %s -> %s',
    'archiv: slouceno %s dilu, ktere v knihovne existuji znovu':
        'archive: merged %s episodes that exist in the library again',
    'narovnani dat: %s uprav, osirelych zbyva %s':
        'data tidy-up: %s changes, %s orphans remain',
    'dohledani v Jellyfinu se nepovedlo: %s':
        'the Jellyfin lookup failed: %s',
    'serial %s: navazano %s dilu, u %s zbylo jen jmeno serialu':
        'series %s: %s episodes linked, %s left with just the series name',
    'hlasku %r se nepodarilo doplnit hodnotami %r':
        'could not fill message %r with values %r',
    'uklizeno %s polozek, ktere do knihovny nepatri (serialy a rady)':
        'removed %s items that do not belong in the library (series and seasons)',
    'z knihovny odstraneno %s polozek, ktere do ni nepatri':
        'removed %s items from the library that do not belong there',
    'dohledani podle tmdb se nepovedlo: %s':
        'lookup by tmdb failed: %s',
    'rucne prirazeno: %s -> %s (%s radku)':
        'manually assigned: %s -> %s (%s rows)',
    'historie doplnena: %s':
        'history filled in: %s',
    'historie: %s -> %s (podle %s S%02dE%02d)':
        'history: %s -> %s (by %s S%02dE%02d)',
    'import z Playback Reporting selhal':
        'import from Playback Reporting failed',
    'import: %s -> %s (podle identity %s)':
        'import: %s -> %s (by identity %s)',
    'import: dohledano %(by_tmdb)s podle tmdb, %(by_episode)s podle cisla dilu, %(by_name)s podle nazvu (%(rows)s zaznamu)':
        'import: matched %(by_tmdb)s by tmdb, %(by_episode)s by episode number, %(by_name)s by name (%(rows)s rows)',
    'log do souboru se nepodařilo založit (%s): %s':
        'the log file could not be created (%s): %s',
    'log se píše i do souboru: %s':
        'the log is also written to a file: %s',
    'naplanovana uloha: %s':
        'scheduled task: %s',
    'navazano %d polozek historie (%d radku)':
        'linked %d history items (%d rows)',
    'obnova zalohy selhala':
        'restoring the backup failed',
    'pg_dump nelze použít (%s), zálohuje se vlastním exportem':
        'pg_dump cannot be used (%s), falling back to the built-in export',
    'planovac uloh selhal':
        'the task scheduler failed',
    'plugin odmitl dotaz (%s)':
        'the plugin refused the query (%s)',
    'plugin odmitl rowid, pouzit zjednoduseny dotaz':
        'the plugin refused rowid, using the simplified query',
    'pocet polozek se nepodarilo zjistit: %s':
        'the item count could not be determined: %s',
    'prehravani: %d aktivnich, %d zacalo, %d skoncilo':
        'playbacks: %d active, %d started, %d ended',
    'prenos dat selhal':
        'the data transfer failed',
    'prevzat bezici zaznam %s (jiny klic relace) - duplicita nevznikla':
        'took over a running record %s (different session key) - no duplicate created',
    'prihlaseni z %s zablokovano (%s. stupen, %s)':
        'sign-in from %s blocked (level %s, %s)',
    'restart na zadost uzivatele':
        'restart requested by the user',
    'restart pres execv selhal, koncim':
        'restart via execv failed, shutting down',
    'rychla synchronizace selhala':
        'the quick sync failed',
    'rychla synchronizace: zmereno %s novych souboru':
        'quick sync: measured %s new files',
    'sberac: %s':
        'collector: %s',
    'sberac: neocekavana chyba':
        'collector: unexpected error',
    'sjednoceny identifikatory z importu: %s':
        'import identifiers unified: %s',
    'slouceno %d duplicitnich skupin v historii, smazano %d radku':
        'merged %d duplicate groups in history, deleted %d rows',
    'slouceno %d skupin duplicit z importu, smazano %d radku':
        'merged %d duplicate groups from the import, deleted %d rows',
    'slouceno: %s -> %s':
        'merged: %s -> %s',
    'srovnano nazvu v historii: %s polozek, %s radku':
        'aligned names in history: %s items, %s rows',
    'srovnani prevzate historie se nepovedlo: %s':
        'aligning the imported history failed: %s',
    'synchronizace knihovny selhala':
        'the library sync failed',
    'synchronizace: zmereno %s souboru':
        'sync: measured %s files',
    'ukazkovy rezim - sberac se nespousti':
        'demo mode - the collector is not started',
    'uklizeno %s starych blokaci prihlasovani':
        'cleaned up %s stale sign-in blocks',
    'uloha %s skoncila: %s':
        'task %s finished: %s',
    'uloha %s: zacinam pocitat rozvrh od ted, prvni beh v %s':
        'task %s: counting the schedule from now, first run at %s',
    'uloha dostala pokyn k zastaveni':
        'the task was asked to stop',
    'ulohy na pozadi ukonceny':
        'background tasks stopped',
    'vlastní záloha PostgreSQL: %d řádků':
        'built-in PostgreSQL backup: %d rows',
    'vraceno %d zaznamu ke spravnym dilum':
        'moved %d records back to the right episodes',
    'zaloha selhala':
        'the backup failed',
    'zaloha smazana: %s':
        'backup deleted: %s',
    'zalohu %s se nepodarilo smazat: %s':
        'backup %s could not be deleted: %s',
    'zalohu pred obnovou se nepodarilo vyrobit':
        'the safety backup before restoring could not be made',
    'zalozeni schematu v cilove databazi selhalo':
        'creating the schema in the target database failed',
}

TRANSLATIONS: dict[str, dict[str, str]] = {"en": EN}


def current_language() -> str:
    """Nastavený jazyk rozhraní. Čte se z databáze, ne z .env."""
    # Import až tady, aby se modul dal načíst i v testech bez databáze.
    from . import db

    value = db.get_setting("ui_language", DEFAULT_LANGUAGE)
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def translate(text: str, language: str | None = None) -> str:
    """Přeloží větu. Když překlad chybí, vrátí původní český text."""
    language = language or current_language()
    if language == DEFAULT_LANGUAGE:
        return text
    return TRANSLATIONS.get(language, {}).get(text, text)


def register(env: Any) -> None:
    """Zpřístupní překlad šablonám jako funkci `_`."""
    env.globals["_"] = translate
    env.globals["ui_languages"] = LANGUAGES
