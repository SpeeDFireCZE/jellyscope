# -*- coding: utf-8 -*-
r"""Ukládá se po rozdělení na karty pořád všechno?

Pole se k formuláři hlásí atributem `form="ulohy"`, ne tím, že by v něm
ležela. Kdyby se na některé zapomnělo, prohlížeč ho neodešle a ono tiše
přestane fungovat - formulář se uloží, jen se ta jedna hodnota nezmění.
Proto se to zkouší odesláním, ne pohledem do šablony.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_karty_uloh.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
_t = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=_t, DATABASE_PATH=os.path.join(_t, "u.db"),
                  SECRET_KEY="dost-dlouhy-testovaci-klic-na-podpisy")

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
c = TestClient(web.app)
c.post("/login", data={"username": "spravce", "password": "dlouheheslo"})

html = c.get("/settings?section=tasks").text

# Ktera pole se k formulari opravdu hlasi? Vytahneme je ze stranky, ne
# ze seznamu v hlave - ten by lhal spolu se sablonou.
POLE = set(re.findall(r'name="([a-z_0-9]+)"[^>]*form="ulohy"', html))
POLE |= set(re.findall(r'form="ulohy"[^>]*name="([a-z_0-9]+)"', html))
print("Úlohy: polí hlásících se k formuláři:", len(POLE))

NUTNA = {"history_retention_days", "backup_path", "backup_keep"}
chybi = NUTNA - POLE
print("  chybí ve formuláři:", chibi if (chibi := chybi) else "nic")

odpoved = c.post("/settings/tasks", data={
    "history_retention_days": "123",
    "backup_path": "/tmp/zalohy-test",
    "backup_keep": "9",
    "enabled_purge": "1",
}, follow_redirects=False)
db.forget_settings()
ulozeno = {k: db.get_setting(k, "") for k in
           ("history_retention_days", "backup_path", "backup_keep")}
print("  odeslání:", odpoved.status_code, ulozeno)

spatne = []
if chybi:
    spatne.append(f"pole mimo formulář úloh: {sorted(chybi)}")
if ulozeno != {"history_retention_days": "123",
               "backup_path": "/tmp/zalohy-test", "backup_keep": "9"}:
    spatne.append(f"úlohy se neuložily: {ulozeno}")

# --- Sber dat a Rozhrani -----------------------------------------------
# Tady formular karty obepina, takze staci overit, ze zadne pole
# nezustalo venku - a ze se to porad uklada.
for sekce, akce, zkouska in (
    ("data", "/settings",
     {"tech_source": "ffprobe", "poll_interval": "25",
      "ffprobe_concurrency": "3"}),
    ("interface", "/settings/interface",
     {"ui_max_streams": "7", "ui_max_viewers": "9"}),
):
    stranka = c.get(f"/settings?section={sekce}").text
    zacatek = stranka.find(f'<form method="post" action="{akce}"')
    konec = stranka.find("</form>", zacatek)
    if zacatek < 0 or konec < 0:
        spatne.append(f"{sekce}: formulář se nenašel")
        continue
    uvnitr = set(re.findall(r'name="([a-z_0-9]+)"', stranka[zacatek:konec]))
    # `kind` a `viewport` jsou meta znacky ze zakladni sablony.
    venku = (set(re.findall(r'name="([a-z_0-9]+)"', stranka))
             - uvnitr - {"section", "kind", "viewport"})
    print(f"{sekce}: polí ve formuláři {len(uvnitr)}, mimo něj {sorted(venku)}")
    if venku:
        spatne.append(f"{sekce}: pole mimo formulář: {sorted(venku)}")

    odpoved = c.post(akce, data=zkouska, follow_redirects=False)
    db.forget_settings()
    mame = {k: db.get_setting(k, "") for k in zkouska}
    print(f"  odeslání: {odpoved.status_code} {mame}")
    if mame != zkouska:
        spatne.append(f"{sekce} se neuložila: {mame} != {zkouska}")

print()
if spatne:
    for potiz in spatne:
        print("CHYBA ", potiz)
else:
    print("OK     každá karta ukládá, žádné pole nezůstalo mimo formulář")
print()
print("HOTOVO - chyb:", len(spatne))
sys.exit(1 if spatne else 0)
