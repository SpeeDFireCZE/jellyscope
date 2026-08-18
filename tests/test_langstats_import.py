# -*- coding: utf-8 -*-
"""Převzatá historie nesmí zkreslit jazykové statistiky.

Ani Jellystat, ani plugin Playback Reporting jazyk přehrávané stopy
nezaznamenávají — prostě ten údaj nemají. Kdyby se převzaté záznamy
počítaly, přidaly by do statistik jen hromadu „Neuvedeno" a přehlušily
skutečná data ze sběrače.

A není to jen kosmetika: „60 % česky" spočítané včetně záznamů, u kterých
jazyk nikdo nezná, je prostě **špatné číslo**.

Statistiky nad tabulkou `items` („co je v knihovně k dispozici") se toho
netýkají — ty s importem nemají nic společného a musí počítat dál.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lang.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, langstats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, audio_languages, subtitle_languages,"
        " is_missing, synced_at) VALUES ('film-1','Matrix','Movie','cs,en','cs',0,?)",
        (db.utcnow(),),
    )
    conn.execute(
        "INSERT INTO items (id, name, type, audio_languages, is_missing, synced_at)"
        " VALUES ('film-2','Duna','Movie','en',0,?)",
        (db.utcnow(),),
    )


def vloz(session_key: str, jazyk: str | None, sekund: int = 3600,
         uzivatel: str = "u1", polozka: str = "film-1") -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, audio_language,
                                     subtitle_language, started_at, last_seen_at,
                                     ended_at, watched_seconds, is_active)
               VALUES (?,?,'Karel',?,'Matrix','Movie',?,NULL,
                       datetime('now','-2 days'), datetime('now','-2 days'),
                       datetime('now','-2 days'), ?, 0)""",
            (session_key, uzivatel, polozka, jazyk, sekund),
        )


print("--- vlastní sběr: dvě hodiny česky, jedna anglicky ---")
vloz("relace-a:film-1", "cs")
vloz("relace-b:film-1", "cs")
vloz("relace-c:film-1", "en")

barvy = langstats.colour_map()
w = langstats.watched_languages(90, barvy)
check(round(w["total_hours"]) == 3, f"tři hodiny celkem ({w['total_hours']})")
check(round(w["preferred_percent"]) == 67,
      f"čeština 67 % ({w['preferred_percent']:.0f})")
check(len(w["rows"]) == 2, f"dva jazyky ({len(w['rows'])})")
check(langstats.imported_plays(90) == 0, "zatím nic z importu")

pred_coverage = langstats.coverage()
pred_titulky = langstats.subtitle_usage(90)
pred_uzivatele = langstats.languages_by_user(90, barvy)
pred_dabing = langstats.dubbed_vs_original(90)
pred_knihovna = langstats.library_languages(barvy)
pred_kombinace = langstats.language_combinations()
pred_chybi = langstats.missing_preferred(90, "cs")


print()
print("--- teď přibude 50 převzatých záznamů bez jazyka ---")
for i in range(50):
    vloz(f"import:jst:{i}:film-1", None)

check(langstats.imported_plays(90) == 50,
      f"stránka ví, že jich je 50 ({langstats.imported_plays(90)})")

w = langstats.watched_languages(90, langstats.colour_map())
check(round(w["total_hours"]) == 3, f"hodiny se nezměnily ({w['total_hours']})")
check(round(w["preferred_percent"]) == 67,
      f"čeština pořád 67 % ({w['preferred_percent']:.0f})")
check(len(w["rows"]) == 2, f"nepřibylo „Neuvedeno“ ({len(w['rows'])} jazyků)")
check(all(row["code"] != "und" for row in w["rows"]),
      "mezi jazyky žádné 'und' není")


print()
print("--- ostatní statistiky nad přehráváním taky ---")
po = langstats.coverage()
check(po["plays_total"] == pred_coverage["plays_total"],
      f"pokrytí počítá stejný vzorek ({po['plays_total']})")
check(po["plays_percent"] == pred_coverage["plays_percent"],
      f"a stejné procento ({po['plays_percent']:.0f} %)")
check(langstats.subtitle_usage(90) == pred_titulky, "titulky beze změny")
check(langstats.languages_by_user(90, barvy) == pred_uzivatele,
      "rozpad podle uživatelů beze změny")
check(langstats.dubbed_vs_original(90) == pred_dabing, "dabing/originál beze změny")


print()
print("--- ale co je v knihovně, se počítá dál ---")
# Tyhle přehledy čtou tabulku items - s importem nemají nic společného.
check(langstats.library_languages(barvy) == pred_knihovna,
      "jazyky v knihovně beze změny")
check(langstats.language_combinations() == pred_kombinace,
      "kombinace stop beze změny")
check(langstats.undefined_language_items() == 0, "titulů bez jazyka je 0")


print()
print("--- import, který jazyk NESE, se do čísel počítá ---")
# Některé verze Playback Reportingu jazyk stopy ukládají a Jellyscope si
# ho při importu převezme. Takový záznam je stejně dobrý jako z vlastního
# sběru - a hláška nad grafy o něm nesmí tvrdit, že chybí.
vloz("import:pbr:500:film-1", "cs")
vloz("import:pbr:501:film-1", "cs")

check(langstats.imported_plays(90) == 50,
      f"nezapočítaných zůstává 50, ne 52 ({langstats.imported_plays(90)})")

w = langstats.watched_languages(90, langstats.colour_map())
check(round(w["total_hours"]) == 5, f"hodiny narostly o dvě ({w['total_hours']})")
check(round(w["preferred_percent"]) == 80,
      f"čeština 80 % - 4 z 5 hodin ({w['preferred_percent']:.0f})")
check(all(row["code"] != "und" for row in w["rows"]),
      "a „Neuvedeno“ mezi jazyky pořád není")

# U titulků to neplatí: záznam s jazykem zvuku nic neříká o tom, jestli
# byly titulky zapnuté, takže by se tvářil jako "bez titulků".
check(langstats.subtitle_usage(90) == pred_titulky,
      "u titulků se import nepočítá ani s jazykem zvuku")


print()
print("--- „chybí česká stopa“ převzatá přehrávání počítá ---")
# Tady jazyk bereme z knihovny, ne z přehrávání. Import je platný důkaz
# toho, že se na titul někdo díval - zahodit ho by byla škoda.
vloz("import:jst:900:film-2", None, polozka="film-2")
chybi = langstats.missing_preferred(90, "cs")
nazvy = {row["label"] for row in chybi["rows"]}
check("Duna" in nazvy, f"titul sledovaný jen podle importu je v seznamu: {nazvy}")
check(len(chybi["rows"]) > len(pred_chybi["rows"]),
      "seznam se rozrostl, import se nezahodil")


print()
print("--- filtr je opravdu v každém dotazu nad playback ---")
import ast  # noqa: E402

zdroj = (PROJECT / "jellyscope" / "langstats.py").read_text(encoding="utf-8")
strom = ast.parse(zdroj)

# `ast.walk` u f-řetězce navštíví i jeho vnitřní kousky. Ty samy o sobě
# vypadají jako dotaz bez filtru, i když ho celek má - proto je vynecháme.
uvnitr_fretezce = {
    id(kus)
    for uzel in ast.walk(strom) if isinstance(uzel, ast.JoinedStr)
    for kus in uzel.values
}

for uzel in ast.walk(strom):
    if not isinstance(uzel, ast.FunctionDef) or uzel.name.startswith("_"):
        continue
    # missing_preferred a imported_plays jsou výjimky vysvětlené výše.
    if uzel.name in ("missing_preferred", "imported_plays", "library_language_options",
                     "preferred_language"):
        continue
    for pod in ast.walk(uzel):
        if isinstance(pod, ast.JoinedStr):
            text = "".join(c.value if isinstance(c, ast.Constant) else "{X}"
                           for c in pod.values)
        elif (isinstance(pod, ast.Constant) and isinstance(pod.value, str)
                and id(pod) not in uvnitr_fretezce):
            text = pod.value
        else:
            continue
        if "FROM playback" not in text:
            continue
        check("{X}" in text, f"{uzel.name}: dotaz nad playback filtr má")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
