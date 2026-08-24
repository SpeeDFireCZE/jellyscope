"""Konfigurace aplikace.

Pravidlo, ktere se vyplati drzet po cely zivot: **tajemstvi nepatri do kodu**.
API klice a hesla ctem z prostredi (souboru .env), ne z .py souboru. Diky tomu
muzes kod klidne dat na GitHub a nic tim nevyzradis.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jellyscope.config")

# Slozka, ve ktere lezi cely projekt (o uroven vys nez tenhle soubor).
# Korenova slozka instalace: tady se hleda .env a slozka data/ (v ni je
# databaze i database.json s vyberem databaze).
#
# `JELLYSCOPE_HOME` to umi presmerovat. Neni to rozmar - bez toho nejde
# aplikaci poradne otestovat: `DATABASE_PATH` totiz **prebiji** ulozeny
# vyber v data/database.json, takze test, ktery si nastavi vlastni
# databazi, by stejne skoncil v te ostre. A protoze by v ni nasel uz
# hotove schema, tvaril by se, ze prosel.
#
# Poradi je zamerne: ulozeny vyber ma v aplikaci prednost pred .env
# (uzivatel ho meni v Nastaveni a musi to fungovat), kdezto
# JELLYSCOPE_HOME prepina cely domecek - tedy i ten ulozeny vyber.
BASE_DIR = Path(
    os.environ.get("JELLYSCOPE_HOME") or Path(__file__).resolve().parent.parent
).resolve()


def _load_dotenv(path: Path) -> None:
    """Nacte soubor .env do promennych prostredi.

    Existuje na to knihovna (python-dotenv), ale je to patnact radku kodu
    a je uzitecne videt, ze na tom neni nic magickeho: precti radky,
    preskoc komentare, rozdel na "klic=hodnota".

    Uz nastavene promenne prostredi maji prednost - to je zvyk, ktery
    umoznuje docasne neco prebit z prikazove radky.
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    """Vsechna nastaveni na jednom miste.

    `frozen=True` znamena, ze se objekt po vytvoreni nemuze zmenit. To je
    zamer: konfigurace se nacte jednou pri startu a pak uz je konstantou.
    """

    jellyfin_url: str
    jellyfin_api_key: str
    database_path: Path
    host: str
    port: int
    secret_key: str
    # Za reverzni proxy s HTTPS zapnout. Prihlasovaci cookie se pak posle
    # jen po sifrovanem spojeni - bez toho ji lze po ceste odposlechnout.
    secure_cookies: bool
    # Komu verit hlavicky X-Forwarded-*. Prazdne = nikomu (primy provoz).
    # Za proxy nastav na jeji adresu, typicky 127.0.0.1.
    forwarded_allow_ips: str
    # Ukazkovy rezim (demo.py). Sberac se nespousti - nema se koho ptat
    # a jen by uzavrel vymyslene prehravani, ktere ma byt videt.
    demo_mode: bool
    # Bezi aplikace v jakemkoliv kontejneru? Rozhoduje o tom, kam se
    # poprve nastavi slozka na zalohy - viz _v_kontejneru().
    in_docker: bool
    # A bezi z NASEHO obrazu (Dockerfile v tomhle repozitari)? Jen tam
    # ma smysl rikat "aktualizuj prestavenim obrazu"; v cizim kontejneru
    # muze byt aplikace nainstalovana z gitu a `git pull` ji funguje.
    nas_obraz: bool


_cached: Config | None = None


def load_config(reload: bool = False) -> Config:
    """Vrati konfiguraci. Podruhe uz jen tu drive nactenou (cache)."""
    global _cached
    if _cached is not None and not reload:
        return _cached

    _load_dotenv(BASE_DIR / ".env")

    db_path = Path(os.environ.get("DATABASE_PATH", "data/jellyscope.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    _cached = Config(
        # rstrip("/") - aby fungovalo i kdyz uzivatel napise adresu s lomitkem na konci
        jellyfin_url=os.environ.get("JELLYFIN_URL", "http://localhost:8096").rstrip("/"),
        jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY", "").strip(),
        database_path=db_path,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8097")),
        secret_key=os.environ.get("SECRET_KEY", "").strip() or _vlastni_klic(),
        secure_cookies=_flag("SECURE_COOKIES"),
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "").strip(),
        demo_mode=_flag("JELLYSCOPE_DEMO"),
        in_docker=_v_kontejneru(),
        nas_obraz=_flag("JELLYSCOPE_DOCKER"),
    )
    return _cached


def _v_kontejneru() -> bool:
    """Bezi aplikace v kontejneru? V jakemkoliv, ne nutne v tom nasem.

    Ptame se trema zpusoby, protoze kazdy sam o sobe nekde selze:

    * `JELLYSCOPE_DOCKER=1` nastavuje nas Dockerfile - u naseho obrazu
      je to jistota, nic se nehada,
    * `/.dockerenv` zaklada Docker sam, takze chyti i cizi obraz,
    * `/run/.containerenv` je totez u Podmanu.

    Podle cgroup se to nepozna spolehlive: na cgroup v2 je v souboru
    obvykle jen "0::/" a zadne "docker" tam neni.
    """
    if _flag("JELLYSCOPE_DOCKER"):
        return True
    try:
        return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    except OSError:
        return False


def _vlastni_klic() -> str:
    """Podpisový klíč, když ho nikdo nenastavil v `.env`.

    Tímhle klíčem se **podepisuje přihlašovací cookie**. Dřív tu byla
    pevná náhradní hodnota - jenže ta je v každé kopii zdrojáku stejná,
    takže kdokoliv, kdo ji zná, si podepíše vlastní cookie s cizím
    účtem a je uvnitř jako správce. Bez hesla.

    Proto se místo toho vyrobí náhodný klíč a uloží se do souboru
    `data/secret_key`. Náhodný klíč držený jen v paměti by nestačil:
    po každém restartu by byl jiný a všichni by se museli přihlašovat
    znovu - a to je přesně ten druh otravnosti, kvůli které lidé
    sahají po nebezpečném řešení.

    Soubor dostane práva 600 (čte jen jeho vlastník). Na Windows to
    `chmod` neumí, tam chrání soubor přístup ke složce.
    """
    soubor = BASE_DIR / "data" / "secret_key"
    try:
        if soubor.is_file():
            ulozeny = soubor.read_text(encoding="utf-8").strip()
            if len(ulozeny) >= 32:
                return ulozeny

        novy = secrets.token_hex(32)
        soubor.parent.mkdir(parents=True, exist_ok=True)
        soubor.write_text(novy, encoding="utf-8")
        try:
            soubor.chmod(0o600)
        except OSError:
            pass
        log.warning(
            "SECRET_KEY nebyl nastaven, vyrobil jsem náhodný a uložil ho do %s. "
            "Přihlášení tím zůstává v bezpečí; kdo chce klíč spravovat sám, "
            "ať ho vyplní v .env.", soubor)
        return novy
    except OSError as exc:
        # Na disk se psát nedá (jen pro čtení, chybí práva). Radši klíč
        # jen v paměti - po restartu se všichni přihlásí znovu, ale
        # podepsat si cizí přihlášení nikdo nemůže.
        log.error("Podpisový klíč nejde uložit (%s). Použil jsem dočasný - "
                  "po restartu bude potřeba se přihlásit znovu.", exc)
        return secrets.token_hex(32)


def _flag(name: str) -> bool:
    """Pravda/nepravda z promenne prostredi.

    Prijima "1", "true", "yes", "on" - lide je pisou ruzne a hadat se
    s uzivatelem o tvar hodnoty nema smysl.
    """
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
