"""Sprava uctu z prikazove radky.

K cemu to je: kdyz zapomenes heslo, z prohlizece uz se dovnitr nedostanes.
Tenhle skript pracuje primo s databazi, takze prihlaseni nepotrebuje.

    python manage.py ucty                      vypise vsechny ucty
    python manage.py pridat jana               zalozi ucet (heslo zada interaktivne)
    python manage.py pridat petr --spravce     zalozi ucet se spravcovskymi pravy
    python manage.py heslo petr                zmeni heslo
    python manage.py smazat jana               smaze ucet

Prave proto, ze tenhle skript obchazi prihlaseni, ho muze spustit jen ten,
kdo ma pristup k souborum na serveru. To je v poradku - kdo ma pristup
k databazi, muze s ni delat cokoliv tak jako tak.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from jellyscope import accounts, db
from jellyscope.formatting import datetime_human


def cmd_list(_: argparse.Namespace) -> int:
    rows = accounts.all_accounts()
    if not rows:
        print("Zadne ucty. Zaloz prvni pres  python manage.py pridat <jmeno> --spravce")
        return 0

    print(f"{'JMENO':<24} {'ROLE':<10} {'VYTVOREN':<18} POSLEDNI PRIHLASENI")
    print("-" * 78)
    for row in rows:
        role = "spravce" if row["is_admin"] else "ctenar"
        print(f"{row['username']:<24} {role:<10} "
              f"{datetime_human(row['created_at']):<18} "
              f"{datetime_human(row['last_login'])}")
    return 0


def _ask_password(prompt: str = "Heslo: ") -> str:
    """Nacte heslo, aniz by se pri psani zobrazovalo na obrazovce.

    Proto getpass a ne input(). Heslo napsane v terminalu by zustalo
    v historii prikazu a videl by ho kazdy, kdo se podiva pres rameno.
    """
    first = getpass.getpass(prompt)
    second = getpass.getpass("Heslo znovu: ")
    if first != second:
        print("Hesla se neshoduji.", file=sys.stderr)
        raise SystemExit(1)
    return first


def cmd_add(args: argparse.Namespace) -> int:
    try:
        accounts.create(args.username, _ask_password(), is_admin=args.spravce)
    except accounts.AccountError as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 1

    role = "spravce" if args.spravce else "ctenar"
    print(f"Ucet '{args.username}' vytvoren ({role}).")
    return 0


def _s_uctem(username: str, akce, hotovo: str) -> int:
    """Najdi ucet, proved s nim akci, ohlas vysledek.

    Prikazy "heslo" a "smaz" se lisi jednim radkem uprostred; okolo je
    stejne hledani uctu a stejne hlaseni chyby. Dokud to bylo opsane
    dvakrat, znamenala kazda zmena hlaseni dve upravy - a jednou se na
    druhou zapomnelo.
    """
    account = accounts.get_by_name(username)
    if account is None:
        print(f"Ucet '{username}' neexistuje.", file=sys.stderr)
        return 1

    try:
        akce(account)
    except accounts.AccountError as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 1

    print(hotovo)
    return 0


def cmd_password(args: argparse.Namespace) -> int:
    return _s_uctem(
        args.username,
        lambda ucet: accounts.set_password(ucet["id"], _ask_password("Nove heslo: ")),
        f"Heslo uctu '{args.username}' zmeneno.")


def cmd_delete(args: argparse.Namespace) -> int:
    return _s_uctem(args.username,
                    lambda ucet: accounts.delete(ucet["id"]),
                    f"Ucet '{args.username}' smazan.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sprava uctu do Jellyscope",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("ucty", help="vypsat vsechny ucty").set_defaults(func=cmd_list)

    add = subparsers.add_parser("pridat", help="zalozit novy ucet")
    add.add_argument("username", help="uzivatelske jmeno")
    add.add_argument("--spravce", action="store_true", help="dat uctu spravcovska prava")
    add.set_defaults(func=cmd_add)

    password = subparsers.add_parser("heslo", help="zmenit heslo")
    password.add_argument("username")
    password.set_defaults(func=cmd_password)

    delete = subparsers.add_parser("smazat", help="smazat ucet")
    delete.add_argument("username")
    delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    db.init_db()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
