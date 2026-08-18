r"""Kontrola nastavení pro nasazení na server.

Netestuje běžící aplikaci, ale **konfiguraci** — a to má smysl: chyba
v ní se projeví až na produkci, kde se hledá nejhůř. Typicky tak, že se
nikdo nepřihlásí a není vidět proč.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_deploy.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import config as cfg  # noqa: E402

failures = 0


def check(ok: bool, message: str) -> None:
    global failures
    print(f"{'OK     ' if ok else 'CHYBA  '}{message}")
    if not ok:
        failures += 1


print("--- výchozí stav (bez proměnných) ---")
# Výchozí hodnoty musí být ty bezpečné pro místní běh. Kdyby se zapnuly
# samy, aplikace by po instalaci nešla otevřít.
for key in ("SECURE_COOKIES", "FORWARDED_ALLOW_IPS"):
    os.environ.pop(key, None)
os.environ.setdefault("SECRET_KEY", "testovaci-klic")

config = cfg.load_config(reload=True)
check(config.secure_cookies is False, "SECURE_COOKIES výchozí = vypnuto")
check(config.forwarded_allow_ips == "",
      "FORWARDED_ALLOW_IPS výchozí = prázdné (nevěřit nikomu)")

print("--- různé tvary pravdivostní hodnoty ---")
# Lidé píšou "1", "true", "yes"... Hádat se s uživatelem o tvar hodnoty
# nemá smysl, ale nesmysl se musí chovat jako vypnuto.
for value, want in [("1", True), ("true", True), ("TRUE", True), ("yes", True),
                    ("on", True), ("0", False), ("false", False), ("", False),
                    ("nesmysl", False)]:
    os.environ["SECURE_COOKIES"] = value
    check(cfg.load_config(reload=True).secure_cookies is want,
          f"SECURE_COOKIES={value!r} -> {want}")

os.environ["FORWARDED_ALLOW_IPS"] = " 127.0.0.1 "
check(cfg.load_config(reload=True).forwarded_allow_ips == "127.0.0.1",
      "mezery kolem adresy proxy se ořežou")

print("--- start bez nastaveného Jellyfinu ---")
# Adresa Jellyfinu a API klíč se nastavují v aplikaci, ne v .env. Spouštěč
# proto NESMÍ odmítnout start, když nejsou vyplněné - uživatel se potřebuje
# dostat do rozhraní, aby je mohl vyplnit. Přesně tohle tu jednou bylo
# a aplikace by na čerstvém serveru vůbec nenaběhla.
launcher = (PROJECT / "run.py").read_text(encoding="utf-8")
check("config.jellyfin_api_key" not in launcher,
      "spouštěč nečte API klíč z .env")
check("return 1" not in launcher,
      "spouštěč nemá cestu, kterou by start odmítl")

print("--- soubory pro nasazení ---")
for name in ("DEPLOY.md", "README.md", "deploy/jellyscope.conf", "deploy/jellyscope.service"):
    check((PROJECT / name).is_file(), f"{name} existuje")

supervisor = (PROJECT / "deploy" / "jellyscope.conf").read_text(encoding="utf-8")
for needle, why in [
    ("/opt/jellyscope/.venv/bin/python", "plná cesta k pythonu (supervisord nezná PATH)"),
    ("user=jellyscope", "neběží pod rootem"),
    ("stopasgroup=true", "ukončí i ffprobe a pg_dump"),
    ("TZ=", "časová zóna pro denní součty"),
    ("autorestart=true", "restart po pádu"),
    ("directory=/opt/jellyscope", "pracovní adresář"),
]:
    check(needle in supervisor, f"supervisord: {why}")

# Systemd unit musí umět totéž co konfigurace pro supervisord - jinak by
# volba správce procesů měnila chování aplikace, ne jen způsob spuštění.
systemd = (PROJECT / "deploy" / "jellyscope.service").read_text(encoding="utf-8")
for needle, why in [
    ("/opt/jellyscope/.venv/bin/python", "plná cesta k pythonu (systemd nezná PATH)"),
    ("User=jellyscope", "neběží pod rootem"),
    ("WorkingDirectory=/opt/jellyscope", "pracovní adresář"),
    ("Environment=TZ=", "časová zóna pro denní součty"),
    ("Restart=always", "restart po pádu"),
    ("KillMode=control-group", "ukončí i ffprobe a pg_dump"),
    ("TimeoutStopSec=30", "dá aplikaci čas na slušné vypnutí"),
    ("WantedBy=multi-user.target", "nastartuje po rebootu"),
    ("ReadWritePaths=/opt/jellyscope/data", "smí psát jen do své složky data/"),
]:
    check(needle in systemd, f"systemd: {why}")

print("--- instalační skripty ---")
# Konfigurace služeb musí mít LF taky - systemd i supervisord si s CR
# na konci řádku poradí různě dobře a hledá se to mizerně.
for name in ("deploy/jellyscope.service", "deploy/jellyscope.conf"):
    raw = (PROJECT / name).read_bytes()
    check(b"\r" not in raw, f"{name} má unixové konce řádků (LF)")

for name in ("deploy/install.sh", "deploy/update.sh"):
    path = PROJECT / name
    check(path.is_file(), f"{name} existuje")
    if not path.is_file():
        continue

    raw = path.read_bytes()
    # Windowsové konce řádků by na Linuxu shodily bash hned na shebangu
    # hláškou "bad interpreter: /bin/bash^M". Řeší to .gitattributes,
    # ale test hlídá i soubor v pracovní kopii.
    check(b"\r\n" not in raw, f"{name} má unixové konce řádků (LF)")
    check(raw.startswith(b"#!/bin/bash"), f"{name} má shebang")

    # Príznak spustitelnosti musí být i v gitu, ne jen na disku.
    #
    # Windows ho na souborech nezná, takže se skripty snadno commitnou
    # jako 100644. Po `git clone` na serveru pak nejsou spustitelné
    # a sudo odpoví matoucím "command not found" - jako by soubor
    # neexistoval.
    rezim = subprocess.run(
        ["git", "ls-files", "-s", name],
        cwd=str(PROJECT), capture_output=True, text=True,
    ).stdout.strip()
    if not rezim:
        print(f"PRESKOCENO  {name} není v gitu")
    else:
        check(rezim.startswith("100755"),
              f"{name} je v gitu spustitelný ({rezim.split()[0]})")

    text = raw.decode("utf-8")
    check("set -euo pipefail" in text,
          f"{name}: skončí při první chybě místo aby pokračoval")

install = (PROJECT / "deploy" / "install.sh").read_text(encoding="utf-8")
update = (PROJECT / "deploy" / "update.sh").read_text(encoding="utf-8")
for needle, why in [
    ("EUID -eq 0", "pozná, jestli běží pod rootem"),
    ("secrets.token_hex", "generuje vlastní SECRET_KEY"),
    ("useradd --system", "poradí, jak si založit vyhrazený účet"),
    ("/setup", "zkušebně ověří, že aplikace naběhne"),
    ("REVERSE PROXY", "vypíše, kam poslat provoz"),
    ("X-Forwarded-Proto", "vypíše potřebné hlavičky"),
    ("SECURE_COOKIES=1", "vypíše, co zapnout po zprovoznění HTTPS"),
    ("/run/systemd/system", "pozná, jestli systemd doopravdy běží jako init"),
    ("command -v supervisorctl", "pozná supervisord"),
    ("systemctl enable --now jellyscope", "poradí, jak zapnout start po rebootu"),
    ("supervisorctl reread", "poradí, jak načíst konfiguraci supervisordu"),
    ("jellyscope.service.ready", "připraví vyplněnou konfiguraci pro systemd"),
    ("jellyscope.conf.ready", "připraví vyplněnou konfiguraci pro supervisord"),
]:
    check(needle in install, f"install.sh: {why}")

# Webový server ani správce procesů si každý řeší po svém - skript
# do nich nesmí sahat. Doinstalovat supervisord na server, který má
# systemd, je přesně ten druh "pomoci", co po sobě nechá binec.
for needle in ("apt-get install -y -qq nginx", "systemctl reload nginx", "certbot"):
    check(needle not in install, f"install.sh nenastavuje webový server ({needle})")

check("MISSING+=(supervisor)" not in install,
      "install.sh neinstaluje supervisord")


def spustitelny_kod(text: str) -> str:
    """Ze shell skriptu vrátí jen to, co se doopravdy spouští.

    Vyhodí tři věci, které vypadají jako příkazy, ale nejsou:
      * komentáře,
      * obsah dvojitých uvozovek (víceřádkové texty die/warn s návody),
      * heredocy (závěrečný výpis s příkazy, které má spustit uživatel).

    Bez toho by test hlásil chybu tam, kde je jen nápověda - a přesně
    to je rozdíl mezi "skript to udělá" a "skript to poradí".
    """
    # 1. heredocy pryč (cat <<EOF ... EOF)
    radky = text.split("\n")
    bez_heredocu: list[str] = []
    v_heredocu = False
    for radek in radky:
        if v_heredocu:
            bez_heredocu.append("")
            if radek.strip() == "EOF":
                v_heredocu = False
            continue
        if re.search(r"<<-?'?EOF'?", radek):
            v_heredocu = True
            bez_heredocu.append("")
            continue
        bez_heredocu.append(radek)

    # 2. obsah dvojitých uvozovek pryč, řádkování zachovat
    ven: list[str] = []
    v_retezci = False
    spojene = "\n".join(bez_heredocu)
    index = 0
    while index < len(spojene):
        znak = spojene[index]
        if znak == "\\" and index + 1 < len(spojene):
            index += 2
            continue
        if znak == '"':
            v_retezci = not v_retezci
        elif not v_retezci or znak == "\n":
            ven.append(znak)
        index += 1

    # 3. komentáře pryč
    return "\n".join(
        "" if radek.lstrip().startswith("#") else radek
        for radek in "".join(ven).split("\n")
    )


# Instalace běží pod tím, kdo ji spustil. Žádné zakládání systémového
# účtu, žádné přebírání vlastnictví - tím odpadá celá kategorie potíží
# s právy, na kterou se při nasazení naráží nejčastěji.
kod_install = spustitelny_kod(install)
kod_update = spustitelny_kod(update)

check("useradd" not in kod_install, "install.sh nezakládá systémového uživatele")
check("sudo -u" not in kod_install, "install.sh nespouští nic přes sudo -u")
check("sudo -u" not in kod_update, "update.sh nespouští nic přes sudo -u")
check("APP_USER=\"${SUDO_USER:-$(id -un)}\"" in install,
      "install.sh vezme uživatele ze SUDO_USER, ne natvrdo 'jellyscope'")

# Aplikace nesmí skončit puštěná pod rootem. Mluví po síti, čte cizí
# soubory a spouští ffprobe - kdyby ji někdo obešel, měl by celý stroj.
check('"$APP_USER" == "root"' in install,
      "install.sh odmítne nastavit aplikaci pod rootem")

# Kontroly smí volat die() až poté, co je definované - jinak by uživatel
# místo vysvětlení dostal "die: command not found". Tohle se tu jednou
# stalo, protože pojistka vznikla nahoře u proměnných.
radky_install = install.splitlines()
kde_die = next(i for i, r in enumerate(radky_install) if r.startswith("die()"))
kde_kontroly = [i for i, r in enumerate(radky_install)
                if "die " in r and not r.strip().startswith("#")
                and not r.startswith("die()")]
check(all(i > kde_die for i in kde_kontroly),
      f"install.sh volá die() až po jeho definici (řádek {kde_die + 1})")
check("ALLOW_ROOT" in install and "ALLOW_ROOT" in update,
      "obě skripty mají vědomou výjimku ALLOW_ROOT (kontejnery)")

# Logy patří do složky projektu, ne do /var/log - tam by mohl psát
# jen root a byli bychom zpátky u řešení práv.
check("$APP_DIR/data/logs" in install, "install.sh dává logy do složky projektu")
service_conf = (PROJECT / "deploy" / "jellyscope.conf").read_text(encoding="utf-8")
check("/var/log/jellyscope" not in service_conf,
      "šablona supervisordu nepíše do /var/log")

# Uživatelská systemd služba - autostart úplně bez roota.
check("jellyscope.user.service.ready" in install,
      "install.sh připraví i uživatelskou systemd službu")
check("loginctl enable-linger" in install,
      "install.sh poradí lingering, aby služba jela i po odhlášení")
check("systemctl --user cat jellyscope.service" in update,
      "update.sh pozná uživatelskou službu a restartuje ji bez roota")

# ProtectHome=true schová celý /home. Dokud aplikace bydlela v /opt, byla
# to čistá výhra. Jakmile se doporučenou cestou stala instalace do
# domovského adresáře, znamenalo by to službu, která se nedostane ke
# svému vlastnímu kódu - a systemd ji vůbec nespustí.
check('"$APP_DIR" == /home/*' in install,
      "install.sh pozná instalaci v domovském adresáři")
check("PROTECT_HOME" in install,
      "install.sh umí ProtectHome vypnout, když by aplikaci zablokoval")

# Složka může být kdekoliv (typicky /opt/jellyscope) - musí ale patřit
# tomu, pod kým aplikace poběží. Skript to spraví sám, ať se uživatel
# nemusí babrat v právech.
check('OWNER="$(stat -c' in install,
      "install.sh zjistí, komu složka patří")
check('as_root chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"' in install,
      "install.sh složku sám převede na uživatele, pod kterým se instaluje")

# Plošné chmod 755 by zpřístupnilo i .env s podpisovým klíčem cookies.
check("chmod -R" not in kod_install, "install.sh nedělá plošné chmod")

# Ovladač PostgreSQL se instaluje rovnou, ať si ho nikdo nemusí shánět
# přes příkazovou řádku, když si v Nastavení přepne databázi.
check('pip install --quiet "psycopg[binary,pool]"' in install,
      "install.sh doinstaluje ovladač PostgreSQL")

# Jeho selhání ale nesmí shodit instalaci: psycopg-binary nemá balíčky
# pro každou platformu a `set -e` by vzal s sebou i instalaci pro toho,
# kdo chce jen SQLite.
# Hledáme v původním textu, ne ve filtrovaném kódu: filtr zahazuje
# obsah uvozovek, takže by z řádku zmizel právě název balíčku.
psycopg_radek = next(
    (r for r in install.splitlines()
     if 'psycopg[binary,pool]' in r and "pip install" in r), ""
)
check(psycopg_radek.strip().startswith("if "),
      f"selhání psycopg neshodí instalaci (řádek: {psycopg_radek.strip()!r})")
check("psycopg" not in (PROJECT / "requirements.txt").read_text(encoding="utf-8")
      .split("# ---")[0],
      "psycopg není mezi povinnými závislostmi")

# ffprobe (balíček ffmpeg) se doinstaluje taky - jinak by volba
# "ffprobe + Jellyfin" v Nastavení byla k nekliknutí.
# Ubuntu 20.04 ma Python 3.8 a instalace na nem koncila hlaskou
# "Could not find a version that satisfies the requirement uvicorn",
# ktera pricinu vubec nepojmenuje. Skript to musi poznat driv.
check("MIN_MINOR=10" in install, "install.sh zna minimalni verzi Pythonu")
check("python3.11" in install, "hleda novejsi Python nainstalovany vedle")
check("deadsnakes" in install, "poradi, jak novejsi Python pridat")
# Minimum drzi pohromade s tim, co si zada FastAPI z requirements.txt.
# Kdyby se cislo zmenilo jen na jednom miste, instalace by prosla
# a spadla az pri pip installu - s hlaskou, ktera pricinu nepojmenuje.
check("Python 3.10 or newer" in (PROJECT / "requirements.txt").read_text(encoding="utf-8"),
      "a requirements.txt rika totez")

# Deadsnakes je az druha volba: nestavi balicky pro ARM a je to cizi
# zdroj. Ubuntu 22.04 a novejsi ma python3.10 primo u sebe.
check("python3.10-venv" in install, "nabizi python3.10 z repozitare Ubuntu")
check(install.index("python3.10-venv") < install.index("ppa:deadsnakes"),
      "a nabizi ho DRIV nez PPA deadsnakes")
check("dpkg --print-architecture" in install,
      "kdyz PPA nezabere, poradi zjistit architekturu (deadsnakes neumi ARM)")
# Kontroluje se, ze skript apt upgrade NESPOUSTI. V textu hlasky
# se objevit smi - tam naopak varuje, aby ho uzivatel nedelal.
check("apt upgrade" not in spustitelny_kod(install),
      "install.sh nespousti apt upgrade")
check("apt upgrade" in install, "ale varuje, aby ho uzivatel nedelal")
check('"$PYTHON_BIN" -m venv' in install,
      "prostredi se stavi vybranym interpretem, ne natvrdo python3")

# Nejcastejsi past na Ubuntu 20.04: prvni pokus postavi .venv Pythonem 3.8,
# spadne na "Could not find a version that satisfies the requirement
# fastapi", uzivatel doinstaluje python3.11 - a chyba se opakuje, protoze
# .venv porad stoji na 3.8. Zvenku to vypada, ze instalace Pythonu
# nepomohla. Skript musi stare prostredi poznat a zahodit.
# Hledame v surovem textu, ne v spustitelny_kod(): ten vyhazuje obsah
# uvozovek, takze by z prikazu zbylo jen "verze_staci".
prikazove_radky = [r for r in install.splitlines() if not r.strip().startswith("#")]
check(any('version_ok "$VENV/bin/python"' in r for r in prikazove_radky),
      "skript overuje verzi Pythonu i u UZ EXISTUJICIHO prostredi")
kus = install[install.find('version_ok "$VENV/bin/python"'):]
check('rm -rf "$VENV"' in kus[:600],
      "a stare prostredi zahodi, misto aby ho pouzil")
check("Could not find a version" in install,
      "hlaska pri selhani zminuje presne to, co pip vypise")

# Port a adresa se musi propsat i do UZ EXISTUJICIHO .env - jinak druha
# instalace s PORT=... skonci tim, ze aplikace nabehne na starem portu
# a nikdo nevi proc.
check("PORT_GIVEN" in install and "HOST_GIVEN" in install,
      "skript pozna, jestli byl port a adresa vyslovne zadane")
check("set_env PORT" in spustitelny_kod(install),
      "a zadany port promitne do existujiciho .env")
check("set_env HOST" in spustitelny_kod(install),
      "totez pro adresu")
# Prepsat cely .env by znamenalo novy SECRET_KEY a odhlaseni vsech -
# proto se meni jen jeden radek pres sed.
check('sed -i "s|^${key}=' in install,
      "meni se jen jeden radek, ne cely soubor")

# Po zapisu se musi cist skutecne hodnoty z .env - jinak by zkusebni
# spusteni klepalo na jiny port, nez na kterem aplikace posloucha.
# Hledame v surovem textu: spustitelny_kod() vyhazuje obsah uvozovek,
# takze by z prirazeni zbylo jen "PORT=".
check(any('from_env PORT' in r for r in prikazove_radky),
      "dalsi kroky beru port z .env, ne z promenne")
check("TEST_HOST" in install and "0.0.0.0" in install,
      "na 0.0.0.0 se neklepe - pro zkousku se pouzije 127.0.0.1")
check("HOST=0.0.0.0" in install,
      "vypis poradi, jak zpristupnit aplikaci z jineho pocitace")
check("--upgrade pip" in spustitelny_kod(install),
      "pip se aktualizuje (ten z Ubuntu 20.04 je 20.0.2)")

check("SKIP_FFMPEG" in install, "instalaci ffmpeg jde přeskočit (SKIP_FFMPEG=1)")
check("apt-get install -y -qq ffmpeg" in install, "install.sh umí doinstalovat ffmpeg")
check("ffmpeg-free" in install, "zná i název balíčku ve Fedoře")

# Stejně jako u psycopg nesmí selhání shodit instalaci - aplikace bez
# ffprobe funguje, jen bere technické údaje z Jellyfinu.
ffmpeg_radky = [r.strip() for r in kod_install.splitlines()
                if "install" in r and "ffmpeg" in r]
check(ffmpeg_radky and all(r.endswith("|| true") for r in ffmpeg_radky),
      f"selhání ffmpeg neshodí instalaci {ffmpeg_radky}")
check('chmod 600 "$APP_DIR/.env"' in install, "install.sh chrání .env právy 600")

# Past, na kterou instalace na Ubuntu spadla: modul `venv` je v základní
# instalaci vždycky, ale bez balíčku python3-venv v něm chybí ensurepip.
# `python3 -m venv` pak stihne vyrobit bin/python a teprve pak selže -
# a další běh skriptu skončil na "pip: command not found".
check('python3 -c "import ensurepip"' in install,
      "install.sh testuje ensurepip, ne jen import venv")
check('python3 -c "import venv"' not in install,
      "install.sh už nespoléhá na 'import venv' (ta kontrola lhala)")

# pip se volá jako `python -m pip`. Spouštěč .venv/bin/pip nemusí
# existovat a nese v sobě pevnou cestu, která se po přesunu složky rozbije.
for name, text in (("install.sh", install),
                   ("update.sh", (PROJECT / "deploy" / "update.sh").read_text(encoding="utf-8"))):
    kod = [radek for radek in text.splitlines() if not radek.strip().startswith("#")]
    check(not any("bin/pip" in radek for radek in kod),
          f"{name} nevolá .venv/bin/pip přímo")

check('-x "$VENV/bin/python"' in install,
      "install.sh pozná nedodělané prostředí podle chybějícího pythonu")
check("-m ensurepip --upgrade" in install,
      "install.sh umí chybějící pip doplnit")
check("apt install python3-venv" in install,
      "install.sh poradí, který balíček doinstalovat")

# Rozdíl mezi "skript to udělá" a "skript to poradí" je v tom, jestli je
# příkaz spuštěný, nebo jen vypsaný. Vypsané rady jsou uvnitř heredoců
# odsazené a začínají "sudo " - skutečné volání by stálo na začátku řádku.
spustene = [
    radek.strip() for radek in kod_install.splitlines()
    if radek.strip().startswith(("supervisorctl ", "systemctl "))
]
check(not spustene,
      f"install.sh sám službu nespouští, jen poradí jak na to {spustene}")

for needle, why in [
    ("pull --ff-only", "nepřepíše místní historii"),
    ("diff --quiet", "odmítne aktualizaci s neuloženými změnami"),
    ("reset --hard", "poradí návrat na předchozí verzi"),
    ("pg_dump", "umí zazálohovat i PostgreSQL"),
    ("sqlite3", "umí zazálohovat SQLite"),
    ("PGPASSWORD=", "heslo předává prostředím, ne příkazovou řádkou"),
    ("SKIP_BACKUP", "jde přeskočit zálohu, ale jen vědomě"),
    ('DB_KIND="sqlite"', "bez konfigurace předpokládá SQLite"),
    ("systemctl restart jellyscope", "umí restartovat službu pod systemd"),
    ("supervisorctl restart jellyscope", "umí restartovat službu pod supervisord"),
    ("systemctl cat jellyscope.service", "zjišťuje, kdo službu doopravdy drží"),
    ("safe.directory", "poradí, co dělat s 'dubious ownership'"),
    # Šablony se čtou ze souborů při každém požadavku, ale Python kód
    # zůstává v paměti. Po `git pull` bez restartu tak běží nové šablony
    # nad starým kódem a stránky padají na "Internal Server Error".
    # Když update nedokáže restartovat sám, musí to říct nahlas.
    ("RUNS THE OLD VERSION", "upozorní, když aplikaci nerestartoval"),
    ("/etc/supervisor/conf.d/jellyscope.conf",
     "pozná supervisord i podle konfigurace (sudo -n selže, když chce heslo)"),
    # Prostredi postavene na Pythonu 3.9 pip odmitne az u fastapi, hlaskou
    # "Could not find a version that satisfies the requirement" - ta zni
    # jako by balicek neexistoval, ne jako "mas stary Python". Update to
    # musi poznat driv a poradit, co s tim.
    ("sys.version_info >= (3, 10)", "pozná prostředí na starém Pythonu"),
    ("bash deploy/install.sh", "a poradí, čím ho postavit znovu"),
]:
    check(needle in update, f"update.sh: {why}")

# Git od 2.35.2 odmítne pracovat v repozitáři, který patří někomu jinému
# ("detected dubious ownership"). Skript běží pod rootem, ale složka patří
# uživateli aplikace - každé volání gitu proto musí jít přes něj.
check('git_() { git -C "$APP_DIR" "$@"; }' in update,
      "update.sh má pomocníka git_(), kterým jdou všechna volání gitu")

mimo_pomocnika = []
for cislo, radek in enumerate(kod_update.splitlines(), 1):
    holy = radek.strip()
    if "git_()" in holy:
        continue
    if re.search(r"(^|[^_\w])git ", holy):
        mimo_pomocnika.append(f"{cislo}: {holy}")
check(not mimo_pomocnika,
      f"update.sh volá git jen přes git_() {mimo_pomocnika}")

print("--- příklady pro reverzní proxy ---")
for name in ("nginx.conf.example", "Caddyfile.example", "apache.conf.example"):
    path = PROJECT / "deploy" / name
    check(path.is_file(), f"deploy/{name} existuje")
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        check("127.0.0.1:8097" in text, f"{name}: míří na správný port")
        check("SECURE_COOKIES=1" in text, f"{name}: připomíná zapnutí bezpečné cookie")

print("--- verzování statických souborů ---")
# Bez ?v=číslo v adrese by si prohlížeč (a u nginxu i proxy) držel starý
# styl a změna vzhledu by byla dny neviditelná. Přesně na tohle jsem
# jednou naletěl při ladění přihlašovací stránky.
templates_dir = PROJECT / "jellyscope" / "templates"
for name in ("base.html", "login.html", "setup.html", "error.html"):
    text = (templates_dir / name).read_text(encoding="utf-8")
    check("style.css?v={{ asset_version }}" in text,
          f"{name}: styl má v adrese verzi")
    check("/static/logo.svg\"" not in text,
          f"{name}: žádný odkaz na logo bez verze")

web = (PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8")
check('globals["asset_version"]' in web, "web.py verzi počítá a předává šablonám")

print("--- .gitignore a .gitattributes ---")
ignore = (PROJECT / ".gitignore").read_text(encoding="utf-8")
for needle in (".env", "data/", ".venv"):
    check(needle in ignore, f".gitignore vylučuje {needle}")

attributes = (PROJECT / ".gitattributes").read_text(encoding="utf-8")
# Porovnáváme řádky se sraženými mezerami. Test nemá padat kvůli tomu,
# že někdo srovnal sloupce - má hlídat pravidlo, ne odsazení.
pravidla = {" ".join(radek.split()) for radek in attributes.splitlines()}
for vzor in ("*.sh text eol=lf", "*.conf text eol=lf", "*.service text eol=lf"):
    check(vzor in pravidla, f".gitattributes vynucuje LF: {vzor}")

print("--- limit nahravaneho souboru sedi s proxy ---")
# Reverzni proxy odmita driv nez aplikace. Kdyz ma nizsi strop, clovek
# dostane hlasku od nginxu misto srozumitelne vety z Jellyscope - a diva
# se do logu aplikace, kde nic neni.
import re as _re

web_kod = (PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8")
strop = int(_re.search(r"MAX_UPLOAD_MB = (\d+)", web_kod).group(1))
check(strop > 0, f"aplikace ma jeden strop pro nahravani ({strop} MB)")
check(web_kod.count("* 1024 * 1024") == web_kod.count("MAX_UPLOAD_MB * 1024 * 1024"),
      "a pouziva se u vsech nahravani, ne cislo napsane rucne")

nginx = (PROJECT / "deploy" / "nginx.conf.example").read_text(encoding="utf-8")
shoda = _re.search(r"client_max_body_size\s+(\d+)M", nginx)
check(shoda and int(shoda.group(1)) >= strop,
      f"nginx pusti aspon tolik ({shoda.group(1) if shoda else '?'}M >= {strop}M)")

apache = (PROJECT / "deploy" / "apache.conf.example").read_text(encoding="utf-8")
shoda = _re.search(r"LimitRequestBody\s+(\d+)", apache)
check(shoda and int(shoda.group(1)) >= strop * 1024 * 1024,
      f"apache taky ({int(shoda.group(1)) // 1024 // 1024 if shoda else '?'} MB)")

instalator = (PROJECT / "deploy" / "install.sh").read_text(encoding="utf-8")
check(f"{strop} MB" in instalator and f"{strop}M" in instalator,
      f"a instalator radi tutez hodnotu ({strop} MB)")


print("--- .env.example ---")
example = (PROJECT / ".env.example").read_text(encoding="utf-8")
check("SECURE_COOKIES=" in example, "SECURE_COOKIES je v příkladu zmíněné")
check("FORWARDED_ALLOW_IPS=" in example, "FORWARDED_ALLOW_IPS je v příkladu zmíněné")
# Kdyby bylo v příkladu zapnuté, člověk by ho zkopíroval, spustil na
# http://localhost a marně by se pokoušel přihlásit.
check("SECURE_COOKIES=1" not in example,
      "SECURE_COOKIES v příkladu NENÍ zapnuté")
check("JELLYSCOPE_PASSWORD" not in example,
      "v příkladu už není staré heslo (přesunulo se do databáze)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
