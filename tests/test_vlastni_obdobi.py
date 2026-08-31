# -*- coding: utf-8 -*-
r"""Vlastní období: statistiky za rozmezí, které si člověk zvolí sám.

Dosud se ptalo jen „od kdy" (`datetime('now', '-30 days')`), takže okno
vždycky končilo teď. „Celý loňský prosinec" se tím vyjádřit nedalo -
a přitom je to jedna z prvních otázek, kvůli kterým se člověk na
statistiky dívá.

Každý dotaz proto dostal i horní mez. U „posledních N dní" je otevřená
(viz `stats.KONEC_CASU`): čas se ukládá zaokrouhlený na vteřiny, takže
přehrávání zapsané v téže vteřině, ve které se ptáme, by se do okna
jinak nevešlo - a právě to je ten záznam, který člověk hledá.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_vlastni_obdobi.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obdobi.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, formatting, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

TED = datetime.now(timezone.utc).replace(tzinfo=None)


def prehrani(kdy: datetime, sekund: int, klic: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', 'Petr', 'film-1', 'Duna', 'Movie', ?, ?, ?, 0)""",
            (klic, kdy.strftime(db.TIME_FORMAT), kdy.strftime(db.TIME_FORMAT), sekund),
        )


# Tři přehrávání v různých dnech - podle okna se má počítat jiná trojice.
prehrani(TED, 3600, "dnes")
prehrani(TED - timedelta(days=10), 7200, "pred-10-dny")
prehrani(TED - timedelta(days=100), 1800, "pred-100-dny")

print("--- posledních N dní ---")
check(stats.overview(7)["plays"] == 1, "za týden je vidět jen dnešek")
check(stats.overview(30)["plays"] == 2, "za měsíc dva záznamy")
check(stats.overview(365)["plays"] == 3, "za rok všechny tři")

print()
print("--- záznam z právě probíhající vteřiny nesmí vypadnout ---")
# Kvůli tomuhle je horní mez u relativního období otevřená: čas se
# ukládá na vteřiny, takže „teď" v databázi a „teď" v dotazu jsou stejný
# řetězec - a s ostrou horní mezí by se do okna nevešel.
prehrani(datetime.now(timezone.utc).replace(tzinfo=None), 60, "prave-ted")
check(stats.overview(7)["plays"] == 2, "právě zapsané přehrávání je vidět hned")

print()
print("--- vlastní rozmezí ---")
pred_10 = (TED - timedelta(days=10)).date().isoformat()
obdobi = stats.obdobi_od_do(pred_10, pred_10)
check(obdobi is not None, "rozmezí jednoho dne dává smysl")
check(obdobi.dny == 1, f"a je dlouhé jeden den ({obdobi.dny})")
check(stats.overview(obdobi)["plays"] == 1,
      "v tom dni je právě jeden záznam")
check(int(stats.overview(obdobi)["watched_seconds"]) == 7200,
      "a sedí i odsledovaný čas")

print()
print("--- konec rozmezí je včetně celého dne ---")
# Do dotazu jde půlnoc dne následujícího, jinak by z posledního dne
# vypadlo všechno po 00:00 - tedy prakticky celý den.
#
# Ta půlnoc je MÍSTNÍ, tedy v zóně aplikace. V UTC (a tak je uložená
# historie) vyjde na jiný okamžik - v létě na 22:00 předchozího dne.
# Dřív se za půlnoc brala ta v UTC a "20. srpna" pak ve skutečnosti
# znamenalo 20. srpna od dvou ráno do dvou ráno dne dalšího.
konec_mistni = datetime.strptime(obdobi.do_mistni, db.TIME_FORMAT)
check(konec_mistni.date() == (TED - timedelta(days=9)).date()
      and konec_mistni.time().isoformat() == "00:00:00",
      f"horní mez je místní půlnoc dalšího dne ({obdobi.do_mistni})")
check(datetime.strptime(obdobi.do, db.TIME_FORMAT)
      .replace(tzinfo=timezone.utc).astimezone(formatting.zona())
      .strftime(db.TIME_FORMAT) == obdobi.do_mistni,
      f"a v dotazu je tatáž chvíle přepočtená do UTC ({obdobi.do})")
check(obdobi.cely_den, "období zadané dny je celodenní")

print()
print("--- co nedává smysl, se nepoužije ---")
check(stats.obdobi_od_do("2026-08-10", "2026-08-01") is None,
      "konec před začátkem se odmítne")
check(stats.obdobi_od_do("nesmysl", "2026-08-01") is None, "text taky")
check(stats.obdobi_od_do(None, None) is None, "a prázdno")

print()
print("--- graf po dnech má tolik řádků, kolik má období dní ---")
check(len(stats.daily_activity_split(30)) == 30, "30 dní = 30 řádků")
check(len(stats.daily_activity_split(7)) == 7, "7 dní = 7 řádků")
tyden = stats.obdobi_od_do((TED - timedelta(days=6)).date().isoformat(),
                           TED.date().isoformat())
check(len(stats.daily_activity_split(tyden)) == 7,
      f"vlastní týden taky sedm ({len(stats.daily_activity_split(tyden))})")

print()
print("--- srovnání s předchozím obdobím ---")
predchozi = stats.predchozi(30)
check(predchozi.do == stats._obdobi(30).od,
      "předchozí okno končí tam, kde to zvolené začíná")
check(predchozi.dny == 30, "a je stejně dlouhé")

print()
print("--- přepínač a okno na stránce ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    # Datum jde zadat po česku i ve tvaru z adresy - stejný parser jako
    # ve filtru historie.
    cesky = client.get("/?od=1.8.2026&do=24.8.2026")
    check(cesky.status_code == 200 and "1.8.2026" in cesky.text,
          "datum psané po česku projde")

    stranka = client.get(f"/?od={pred_10}&do={pred_10}").text
    check('id="okno-obdobi"' in stranka, "okno pro vlastní období je na stránce")
    check(stranka.count("data-kalendar") >= 2,
          "obě pole mají našeptávač s kalendářem")
    # Šipky měsíců překreslují obsah panelu, takže než by kliknutí
    # probublalo k obsluze "kliknuto mimo", tlačítko už v dokumentu není
    # a `closest` na odpojeném uzlu vrátí null. Kalendář se tím zavíral
    # a měsíce nešly přepínat vůbec - proto zachycovací fáze.
    zaklad = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
    obsluha = zaklad[zaklad.index("input[data-kalendar]"):]
    obsluha = obsluha[:obsluha.index("});") + 3]
    check(obsluha.rstrip().endswith("}, true);")
          or "}, true);" in zaklad[zaklad.index("var vstup = event.target.closest"):
                                   zaklad.index("var vstup = event.target.closest") + 600],
          "kliknutí mimo kalendář se hlídá v zachycovací fázi")
    check(stranka.count("data-obdobi-od=") == 3,
          "a jsou tam tři rychlé volby (tento měsíc, minulý, letos)")
    # Datum se píše po česku - nativní <input type="date"> vypadá
    # v každém prohlížeči jinak a uprostřed okna trčí.
    # Pole jsou textová, ne nativní <input type="date"> - ten vypadá
    # v každém prohlížeči jinak. (Hledat samotné `type="date"` ve stránce
    # nejde: ta věta je i v komentáři, který vysvětluje proč.)
    check('<input type="text" id="obdobi-od"' in stranka
          and '<input type="text" id="obdobi-do"' in stranka,
          "obě pole na datum jsou textová")
    check(pred_10 in stranka, "a přepínač ukazuje zvolené datum")

    # Zapamatuje se stejně jako počet dnů, takže platí i na dalších
    # stránkách - jinak by se okno při každém přechodu vracelo na 30 dnů.
    # Po česku, stejně jako v polích - ať to je jedno datum, ne dva
    # různé zápisy téhož.
    d = datetime.strptime(pred_10, "%Y-%m-%d")
    pred_10_cesky = f"{d.day}.{d.month}.{d.year}"

    dalsi = client.get("/languages").text
    check(pred_10_cesky in dalsi, "vlastní období platí i po přechodu jinam")

    # Pozor na naivní hledání data ve stránce: tentýž den se objeví
    # i v tabulce pod grafem. Ptáme se proto přepínače.
    import re

    def vybrany_chip(html: str) -> str:
        m = re.search(r'class="chip active"[^>]*>\s*([^<]+?)\s*<', html)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    check(pred_10_cesky in vybrany_chip(stranka),
          f"vlastní období je vybrané v přepínači ({vybrany_chip(stranka)})")
    zpet = client.get("/?days=30").text
    check(pred_10_cesky not in vybrany_chip(zpet),
          f"a klik na 30 dnů ho zase zruší ({vybrany_chip(zpet)})")

    for cesta in ("/insights", "/network", "/users", "/languages"):
        odpoved = client.get(f"{cesta}?od={pred_10}&do={pred_10}")
        check(odpoved.status_code == 200, f"{cesta} s vlastním obdobím")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
