"""Ucty do Jellyscope - prihlasovani a sprava uzivatelu.

Pozor na rozdil, ktery je snadne prehlednout:

  * **users**    = uzivatele Jellyfinu. Ty jen ctem, abychom vedeli,
                   kdo se na co dival. Prihlasit se jimi nikam nejde.
  * **accounts** = ucty do teto aplikace. O ty se staráme my.

Jsou to dve nezavisle veci a schvalne se nemichaji: kdyz nekomu das
pristup do Jellyscope, nedavas mu tim pristup do Jellyfinu, a naopak.

## Jak se uklada heslo

Nikdy, za zadnych okolnosti, se heslo neuklada tak, jak ho uzivatel napsal.
Kdyby se nekdo dostal k databazi, mel by rovnou vsechna hesla - a protoze
lide pouzivaji stejne heslo na vic mistech, prisel by i k jejich e-mailum.

Misto hesla se uklada jeho **otisk** (hash): vysledek vypoctu, ktery jde
udelat jen jednim smerem. Z hesla otisk spocitas snadno, z otisku heslo
nezjistis. Pri prihlaseni se spocita otisk zadaneho hesla a porovna se
s ulozenym.

Pouzivame PBKDF2 - je primo v Pythonu, nepotrebuje zadnou knihovnu navic,
a ma dve dulezite vlastnosti:

  * **sul** (salt) - nahodny retezec ulozeny vedle otisku. Diky nemu maji
    dva uzivatele se stejnym heslem ruzne otisky, takze podle otisku nejde
    poznat, ze maji stejne heslo.
  * **pocet opakovani** - vypocet se opakuje statisickrat, aby zkouseni
    hesel jedno po druhem trvalo utocnikovi neunosne dlouho.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

# Cim vic opakovani, tim pomalejsi zkouseni hesel - ale taky tim pomalejsi
# prihlaseni. 600 000 je doporuceni OWASP pro PBKDF2 se SHA-256.
ITERATIONS = 600_000
ALGORITHM = "pbkdf2_sha256"

MIN_PASSWORD_LENGTH = 8
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")


class AccountError(ValueError):
    """Chyba, kterou ma smysl ukazat uzivateli (na rozdil od padu programu)."""


# ---------------------------------------------------------------------------
# Brzda na hadani hesel
# ---------------------------------------------------------------------------
#
# Samotne hashovani uz je pomale schvalne (600 000 opakovani je asi ctvrt
# vteriny), takze hesla nejdou zkouset po tisicich za vterinu. Slovnikovy
# utok pres noc by ale porad vysel - a kazdy pokus stoji ctvrt vteriny
# procesoru, takze se tudy da aplikace i zahltit.
#
# Brzda ma dve patra:
#
#   1. **Pocitani pokusu** drzime v pameti procesu. Je to udaj o poslednich
#      minutach a zapisovat kazde spatne heslo do databaze by bylo presne
#      to, co utocnik chce.
#   2. **Blokace** uz do databaze patri: ma prezit restart, jinak by stacilo
#      pockat na aktualizaci. A hlavne se stupnuje - kdo prijde poosme,
#      zjevne nezkousi svoje heslo.
#
# Stupne jsou zamerne kratke na zacatku a tvrde na konci. Kdo si splete
# heslo, pocka minutu a nadava; kdo hada, narazi po ctvrt hodine na zed.
POKUSU_DO_BLOKACE = 8
STUPNE_BLOKACE = (60, 120, 300, 900)      # 1 min, 2 min, 5 min, 15 min
# Po ctyrech blokacich uz je to trvale - odblokovat musi spravce v Nastaveni.

# Kdyz se adresa dlouho chova slusne, zacina se znovu od prvniho stupne.
# Bez toho by se clovek, ktery si jednou za pul roku splete heslo, po
# nekolika letech zablokoval natrvalo.
ZAPOMENUT_PO_HODINACH = 24

_pokusy: dict[str, list[float]] = {}


def _radek_blokace(ip: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM login_blocks WHERE ip = ?", (ip,))


def blokace_zbyva(ip: str) -> int:
    """Kolik vterin musi adresa jeste pockat. 0 = muze zkusit, -1 = trvale."""
    radek = _radek_blokace(ip)
    if radek is None:
        return 0
    if radek["permanent"]:
        return -1
    if not radek["blocked_until"]:
        return 0

    zbyva = _do_kdy_sekund(str(radek["blocked_until"]))
    return max(0, zbyva)


def _do_kdy_sekund(cas_utc: str) -> int:
    """Kolik vterin zbyva do daneho casu v UTC."""
    try:
        konec = datetime.strptime(cas_utc[:19].replace("T", " "), db.TIME_FORMAT)
    except (TypeError, ValueError):
        return 0
    return int((konec - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())


def zapocitej_neuspech(ip: str) -> dict[str, Any] | None:
    """Zapise neuspesny pokus. Kdyz uz je jich moc, zalozi blokaci.

    Vraci popis blokace, kdyz prave vznikla - jinak None.
    """
    ted = time.monotonic()
    pokusy = [t for t in _pokusy.get(ip, []) if ted - t < STUPNE_BLOKACE[0]]
    pokusy.append(ted)
    _pokusy[ip] = pokusy[-POKUSU_DO_BLOKACE:]

    # Strop na pocet adres, at se pamet neda zaplnit stridanim adres.
    if len(_pokusy) > 5000:
        _pokusy.clear()

    if len(pokusy) < POKUSU_DO_BLOKACE:
        return None

    _pokusy.pop(ip, None)
    return _zablokuj(ip)


def _zablokuj(ip: str) -> dict[str, Any]:
    """Zalozi nebo prituhne blokaci adresy."""
    radek = _radek_blokace(ip)
    stupen = int(radek["level"]) if radek else 0
    pokusu = int(radek["failures"] or 0) if radek else 0

    # Dlouho klid? Zacneme znovu od zacatku.
    if radek and radek["last_failure"]:
        od_posledniho = -_do_kdy_sekund(str(radek["last_failure"]))
        if od_posledniho > ZAPOMENUT_PO_HODINACH * 3600:
            stupen = 0

    stupen += 1
    trvale = stupen > len(STUPNE_BLOKACE)
    sekund = 0 if trvale else STUPNE_BLOKACE[stupen - 1]
    do_kdy = None if trvale else _za_sekund(sekund)

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO login_blocks (ip, level, blocked_until, permanent,
                                      failures, last_failure)
                 VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (ip) DO UPDATE SET
                level         = excluded.level,
                blocked_until = excluded.blocked_until,
                permanent     = excluded.permanent,
                failures      = excluded.failures,
                last_failure  = excluded.last_failure
            """,
            (ip, stupen, do_kdy, 1 if trvale else 0,
             pokusu + POKUSU_DO_BLOKACE, db.utcnow()),
        )
    return {"ip": ip, "level": stupen, "seconds": sekund, "permanent": trvale}


def _za_sekund(sekund: int) -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(seconds=sekund)).strftime(db.TIME_FORMAT)


def zapomen_neuspechy(ip: str) -> None:
    """Po uspesnem prihlaseni se zacina znovu od nuly.

    Maze se i zaznam v databazi, tedy stupen blokace: kdo zna heslo, je
    zjevne majitel uctu a nema si nest do budoucna, ze se mu par pokusu
    nepovedlo. Blokovanou adresu to neobchazi - dokud blokace plati,
    heslo se vubec nekontroluje.
    """
    _pokusy.pop(ip, None)
    with db.connect() as conn:
        conn.execute("DELETE FROM login_blocks WHERE ip = ? AND permanent = 0",
                     (ip,))


def seznam_blokaci() -> list[dict[str, Any]]:
    """Blokace pro vypis v Nastaveni - od nejcerstvejsi."""
    radky = db.query_all(
        "SELECT * FROM login_blocks ORDER BY last_failure DESC, ip")
    for radek in radky:
        radek["permanent"] = bool(radek["permanent"])
        radek["seconds_left"] = (0 if radek["permanent"]
                                 else max(0, _do_kdy_sekund(str(radek["blocked_until"] or ""))))
        radek["active"] = radek["permanent"] or radek["seconds_left"] > 0
    return radky


def odblokuj(ip: str) -> bool:
    """Zrusi blokaci adresy vcetne pocitadla stupnu."""
    with db.connect() as conn:
        cursor = conn.execute("DELETE FROM login_blocks WHERE ip = ?", (ip,))
    _pokusy.pop(ip, None)
    return bool(cursor.rowcount)


def zablokuj_natrvalo(ip: str) -> None:
    """Rucni trvala blokace ze seznamu v Nastaveni."""
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO login_blocks (ip, level, blocked_until, permanent,
                                      failures, last_failure)
                 VALUES (?, ?, NULL, 1, 0, ?)
            ON CONFLICT (ip) DO UPDATE SET
                permanent = 1, blocked_until = NULL,
                level = login_blocks.level + 1
            """,
            (ip, len(STUPNE_BLOKACE) + 1, db.utcnow()),
        )


def uklid_blokaci() -> int:
    """Smaze davno vyprsele zaznamy. Vola se pri startu."""
    hranice = (datetime.now(timezone.utc).replace(tzinfo=None)
               - timedelta(hours=ZAPOMENUT_PO_HODINACH)).strftime(db.TIME_FORMAT)
    with db.connect() as conn:
        cursor = conn.execute(
            "DELETE FROM login_blocks WHERE permanent = 0 AND last_failure < ?",
            (hranice,),
        )
    return cursor.rowcount or 0


# ---------------------------------------------------------------------------
# Hesla
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Vyrobi otisk hesla ve tvaru  algoritmus$opakovani$sul$otisk."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Overi heslo proti ulozenemu otisku."""
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False

    # compare_digest porovnava v konstantnim case. Bezne "==" skonci u prvniho
    # odlisneho bajtu a z doby odpovedi by sel otisk postupne uhodnout.
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------------------
# Kontroly vstupu
# ---------------------------------------------------------------------------

def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_PATTERN.match(username):
        raise AccountError(
            "Jméno musí mít 3 až 32 znaků a smí obsahovat jen písmena bez "
            "diakritiky, číslice, tečku, pomlčku a podtržítko."
        )
    return username


def validate_password(password: str, again: str | None = None) -> str:
    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Heslo musí mít aspoň {MIN_PASSWORD_LENGTH} znaků.")
    if again is not None and password != again:
        raise AccountError("Hesla se neshodují.")
    return password


# ---------------------------------------------------------------------------
# Cteni
# ---------------------------------------------------------------------------

def count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM accounts"))


def any_exists() -> bool:
    """Existuje uz aspon jeden ucet? Kdyz ne, aplikace nabidne prvni nastaveni."""
    return count() > 0


def get_by_name(username: str) -> dict[str, Any] | None:
    # Porovnani pres LOWER() zaridi, ze "Petr" a "petr" je tyz ucet -
    # a funguje stejne v SQLite i PostgreSQL. Spolehat se na COLLATE NOCASE
    # by slo jen v SQLite.
    return db.query_one(
        "SELECT * FROM accounts WHERE LOWER(username) = LOWER(?)",
        (username.strip(),),
    )


def get(account_id: int) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))


def all_accounts() -> list[dict[str, Any]]:
    return db.query_all(
        "SELECT id, username, is_admin, created_at, last_login"
        " FROM accounts ORDER BY is_admin DESC, LOWER(username)"
    )


def admin_count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM accounts WHERE is_admin = 1"))


# ---------------------------------------------------------------------------
# Zmeny
# ---------------------------------------------------------------------------

def create(username: str, password: str, again: str | None = None, is_admin: bool = False) -> int:
    username = validate_username(username)
    password = validate_password(password, again)

    if get_by_name(username) is not None:
        raise AccountError(f"Účet '{username}' už existuje.")

    with db.connect() as conn:
        return conn.insert_returning_id(
            "INSERT INTO accounts (username, password_hash, is_admin, created_at)"
            " VALUES (?,?,?,?)",
            (username, hash_password(password), 1 if is_admin else 0, db.utcnow()),
        )


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Vrati ucet, kdyz jmeno i heslo sedi. Jinak None."""
    account = get_by_name(username or "")
    if account is None:
        # I kdyz ucet neexistuje, spocitame otisk naprazdno. Bez toho by
        # odpoved prisla znatelne rychleji a slo by tak zjistit, ktera
        # jmena na serveru existuji.
        hash_password(password or "")
        return None

    if not verify_password(password or "", account["password_hash"]):
        return None

    with db.connect() as conn:
        conn.execute("UPDATE accounts SET last_login = ? WHERE id = ?",
                     (db.utcnow(), account["id"]))
    return account


def set_password(account_id: int, password: str, again: str | None = None) -> None:
    password = validate_password(password, again)
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?",
                     (hash_password(password), account_id))


def set_admin(account_id: int, is_admin: bool) -> None:
    """Zmeni opravneni. Posledniho spravce degradovat nedovolime."""
    account = get(account_id)
    if account is None:
        raise AccountError("Účet neexistuje.")
    if account["is_admin"] and not is_admin and admin_count() <= 1:
        raise AccountError("Nemůžeš odebrat práva poslednímu správci.")

    with db.connect() as conn:
        conn.execute("UPDATE accounts SET is_admin = ? WHERE id = ?",
                     (1 if is_admin else 0, account_id))


def delete(account_id: int) -> None:
    """Smaze ucet. Posledni spravce zustava - jinak by se uz nikdo nedostal dovnitr."""
    account = get(account_id)
    if account is None:
        raise AccountError("Účet neexistuje.")
    if account["is_admin"] and admin_count() <= 1:
        raise AccountError("Posledního správce smazat nelze.")

    with db.connect() as conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
