"""Vyber rozsahu tazenim v grafu.

Graf musi rict, kde na jeho ose lezi jaky okamzik, server musi ty
okamziky prijmout a prepocitat do sve zony a stranka se pak ma divat
jen na vybrany usek.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vyber.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import charts, db, stats, web  # noqa: E402

chyb = 0


def zkontroluj(popis, podminka, detail=""):
    global chyb
    if podminka:
        print(f"  OK   {popis}")
    else:
        chyb += 1
        print(f"  CHYBA {popis} {detail}")


db.init_db()
db.set_setting("app_timezone", "Europe/Prague")

print("Graf prozradi meze osy")
zaklad = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc).timestamp()
body = [{"popisek": f"20.08. {18 + i}:00", "cas": zaklad + i * 3600, "mbit": 10.0 + i}
        for i in range(5)]
svg = charts.area_chart_multi(body, "popisek", [{"key": "mbit", "label": "Mbit/s"}],
                              unit="", vyber=True)
zkontroluj("graf je označený jako vybíratelný v čase", 'data-vyber="cas"' in svg)
zkontroluj("nese krajní okamžiky",
           f'data-cas-od="{int(zaklad)}"' in svg
           and f'data-cas-do="{int(zaklad + 4 * 3600)}"' in svg)
zkontroluj("nese meze osy x", 'data-x-od=' in svg and 'data-x-do=' in svg)
zkontroluj("má připravený obdélník výběru", 'class="chart-vyber"' in svg)

bez = charts.area_chart_multi(body, "popisek", [{"key": "mbit", "label": "Mbit/s"}],
                              unit="")
zkontroluj("bez vyber=True se nic nepřidává",
           "data-vyber" not in bez and "chart-vyber" not in bez)

print("Graf po dnech se zaokrouhli na dny")
dny = [{"day": f"2026-08-{den:02d}", "hours": den} for den in range(10, 18)]
svg_dny = charts.area_chart_multi(dny, "day", [{"key": "hours", "label": "Hodiny"}],
                                  vyber=True)
zkontroluj("je označený jako denní", 'data-vyber="dny"' in svg_dny)
zkontroluj("nese seznam dnů",
           'data-dny="2026-08-10,2026-08-11' in svg_dny and "2026-08-17" in svg_dny)
zkontroluj("čas neposílá", "data-cas-od" not in svg_dny)

# Den bez data v ose (jen popisek) se vybírat neda - nebylo by z ceho
# poznat, ktery den to je.
bez_dnu = charts.area_chart_multi(
    [{"popisek": f"{d}.08.", "mbit": d} for d in range(10, 18)], "popisek",
    [{"key": "mbit", "label": "Mbit/s"}], vyber=True)
zkontroluj("bez dnů i času se nevybírá", "data-vyber" not in bez_dnu)

print("Okamžiky se prevedou do zony aplikace")
od = datetime(2026, 8, 20, 19, 30, tzinfo=timezone.utc).timestamp()
do = datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc).timestamp()
o = stats.obdobi_z_okamziku(od, do)
zkontroluj("dotaz je v UTC", o.od == "2026-08-20 19:30:00" and o.do == "2026-08-20 21:45:00",
           f"({o.od} -> {o.do})")
zkontroluj("pro člověka místní čas",
           o.od_mistni == "2026-08-20 21:30:00" and o.do_mistni == "2026-08-20 23:45:00",
           f"({o.od_mistni} -> {o.do_mistni})")
zkontroluj("není relativní", o.relativni is False)
zkontroluj("obrácené meze se odmítnou", stats.obdobi_z_okamziku(do, od) is None)
zkontroluj("nesmysl se odmítne", stats.obdobi_z_okamziku("x", "y") is None)

print("Cely den v mistni zone, ne v UTC")
den = stats.obdobi_od_do("2026-08-20", "2026-08-20")
zkontroluj("léto: den začíná ve 22:00 UTC předchozího dne",
           den.od == "2026-08-19 22:00:00" and den.do == "2026-08-20 22:00:00",
           f"({den.od} -> {den.do})")
zima = stats.obdobi_od_do("2026-01-15", "2026-01-15")
zkontroluj("zima: posun o hodinu",
           zima.od == "2026-01-14 23:00:00" and zima.do == "2026-01-15 23:00:00",
           f"({zima.od} -> {zima.do})")

print("Prepinac nahore ukaze cas jen tehdy, kdyz o nej jde")
popis = web._obdobi_do_sablony(o)
zkontroluj("výběr části dne ukáže čas",
           popis["od_popis"] == "20.8.2026 21:30" and popis["do_popis"] == "20.8.2026 23:45",
           f"({popis['od_popis']} – {popis['do_popis']})")
popis_den = web._obdobi_do_sablony(den)
zkontroluj("celý den zůstane bez času",
           popis_den["od_popis"] == "20.8.2026" and popis_den["do_popis"] == "20.8.2026",
           f"({popis_den['od_popis']} – {popis_den['do_popis']})")
zkontroluj("formulář dostane pořád jen datum",
           popis["od_text"] == "20.8.2026" and popis["do_text"] == "20.8.2026")

print("Vybrany usek opravdu zuzi data")
teď = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
with db.connect() as conn:
    for poradi, posun in enumerate([-3, -1, 3]):   # hodiny od `teď`
        zacatek = teď + timedelta(hours=posun)
        konec = zacatek + timedelta(hours=1)
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds,"
            " is_paused, is_active) VALUES (?,?,?,?,?,?,?,?,?,0,0,0)",
            (f"s{poradi}", f"u{poradi}", "Pepa", f"i{poradi}", "Film",
             zacatek.strftime(db.TIME_FORMAT), konec.strftime(db.TIME_FORMAT),
             konec.strftime(db.TIME_FORMAT), 3600))
    conn.commit()

usek = stats.obdobi_z_okamziku(
    (teď - timedelta(hours=2)).timestamp(), (teď + timedelta(hours=1)).timestamp())
prehled = stats.overview(usek)
zkontroluj("v úseku je jen to, co do něj patří",
           prehled["watched_seconds"] == 3600,
           f"(sekund: {prehled['watched_seconds']})")

print("Obdobi kratsi nez dva dny ma porad co ukazat")
# Vyber tazenim muze skoncit u jednoho jedineho dne. Bod bez souseda nema
# delku, takze z nej byla cara o nulove sirce - graf vypadal prazdny,
# prestoze se ten den koukalo.
jeden = charts.area_chart_multi([{"day": "2026-08-31", "movie_hours": 3.65}], "day",
                                [{"key": "movie_hours", "label": "Filmy"}])
cesty = re.findall(r'<path d="([^"]+)"', jeden)
zkontroluj("z jednoho dne se graf nakreslí", len(cesty) == 2, f"(cest: {len(cesty)})")
zkontroluj("a čára jde přes celou šířku",
           all("46.0," in c and "738.0," in c for c in cesty))
# Cara (druha cesta) zacina na vysce hodnoty. Nula lezi na zakladne,
# takze cim vys, tim mensi souradnice - a rovnou na zakladne by to
# znamenalo, ze se ten den nekoukalo.
zaklad = 240 - 30      # vyska grafu minus spodni okraj
vyska_cary = float(re.match(r"M[\d.]+,([\d.]+)", cesty[1]).group(1))
zkontroluj("čára je ve výšce hodnoty, ne na nule", vyska_cary < zaklad - 20,
           f"(y={vyska_cary}, základna={zaklad})")

# Minigraf pod hlavnim cislem je na vyvoj - u jednoho dne zadny neni,
# tak at nezabira misto prazdnou plochou.
zkontroluj("minigraf z jednoho dne se nekreslí",
           charts.sparkline([{"day": "2026-08-31", "hours": 3.65}]) == "")
zkontroluj("ze dvou dnů ano",
           charts.sparkline([{"day": "2026-08-30", "hours": 1.0},
                             {"day": "2026-08-31", "hours": 3.65}]) != "")

print("Srovnani s predchozim obdobim meri stejne dlouhy usek")
# Vyber tazenim muze byt kratsi nez den. Kdyby se predchozi okno odvodilo
# z poctu dnu (dvouhodinovy vyber ma `dny == 1`), porovnavaly by se dve
# hodiny s celym predchozim dnem - a kazda sipka "oproti predchozimu
# obdobi" by hlasila propad, ktery se nestal.
def delka(o):
    return (datetime.strptime(o.do, db.TIME_FORMAT)
            - datetime.strptime(o.od, db.TIME_FORMAT))

for popis, hodin in (("dvě hodiny", 2), ("šest hodin", 6), ("den a půl", 36)):
    vyber = stats.obdobi_z_okamziku((teď - timedelta(hours=hodin)).timestamp(),
                                    teď.timestamp())
    minule = stats.predchozi(vyber)
    zkontroluj(f"{popis}: předchozí okno je stejně dlouhé",
               delka(minule) == delka(vyber), f"({delka(minule)} vs {delka(vyber)})")
    zkontroluj(f"{popis}: a končí tam, kde vybrané začíná", minule.do == vyber.od)

# Cele dny i "poslednich N dni" musi zustat, jak byly.
den = stats.obdobi_od_do("2026-08-20", "2026-08-20")
zkontroluj("celý den: předchozí je taky den", delka(stats.predchozi(den)).days == 1)
zkontroluj("posledních 30 dní: předchozí navazuje",
           stats.predchozi(30).do == stats._obdobi(30).od)
zkontroluj("a je třicetidenní", delka(stats.predchozi(30)).days == 30)

print()
print(f"HOTOVO - chyb: {chyb}")
sys.exit(1 if chyb else 0)
