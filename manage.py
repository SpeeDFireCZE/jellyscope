"""Managing accounts from the command line.

What it is for: when you forget your password, or a sign-in block shuts you
out, you no longer get in from the browser. This script works with the
database directly, so it needs no sign-in.

    python manage.py accounts                  lists all accounts
    python manage.py add jana                  creates an account (the password is typed interactively)
    python manage.py add petr --admin          creates an account with administrator rights
    python manage.py password petr             changes the password
    python manage.py delete jana               deletes the account
    python manage.py blocks                    lists the sign-in blocks
    python manage.py unblock 203.0.113.7       lifts the block of an address (--all lifts every one)

The old Czech commands (ucty, pridat, heslo, smazat, --spravce) still work for
a while as aliases - TODO.md item 0 removes them with the old page addresses.

Exactly because this script goes around the sign-in, only whoever has access
to the files on the server can run it. That is fine - whoever has access to
the database can do anything with it anyway.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from jellyscope import accounts, db
from jellyscope.formatting import datetime_human


def cmd_list(_: argparse.Namespace) -> int:
    rows = accounts.all_accounts()
    if not rows:
        print("No accounts. Create the first one with  python manage.py add <name> --admin")
        return 0

    print(f"{'NAME':<24} {'ROLE':<10} {'CREATED':<18} LAST SIGN-IN")
    print("-" * 78)
    for row in rows:
        role = "admin" if row["is_admin"] else "reader"
        print(f"{row['username']:<24} {role:<10} "
              f"{datetime_human(row['created_at']):<18} "
              f"{datetime_human(row['last_login'])}")
    return 0


def _ask_password(prompt: str = "Password: ") -> str:
    """Reads a password without showing it on the screen while typing.

    Hence getpass and not input(). A password typed in the terminal would stay
    in the command history and anybody looking over the shoulder would see it.
    """
    first = getpass.getpass(prompt)
    second = getpass.getpass("Password again: ")
    if first != second:
        print("The passwords do not match.", file=sys.stderr)
        raise SystemExit(1)
    return first


def cmd_add(args: argparse.Namespace) -> int:
    try:
        accounts.create(args.username, _ask_password(), is_admin=args.admin)
    except accounts.AccountError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    role = "admin" if args.admin else "reader"
    print(f"Account '{args.username}' created ({role}).")
    return 0


def _with_account(username: str, action, done_message: str) -> int:
    """Finds the account, does the action with it, reports the result.

    The commands "password" and "delete" differ in one line in the middle; around
    it is the same looking up of the account and the same error report. While
    it was copied twice, every change of the report meant two edits - and once
    the second was forgotten.
    """
    account = accounts.get_by_name(username)
    if account is None:
        print(f"Account '{username}' does not exist.", file=sys.stderr)
        return 1

    try:
        action(account)
    except accounts.AccountError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(done_message)
    return 0


def cmd_password(args: argparse.Namespace) -> int:
    return _with_account(
        args.username,
        lambda account: accounts.set_password(account["id"], _ask_password("New password: ")),
        f"The password of account '{args.username}' was changed.")


def cmd_delete(args: argparse.Namespace) -> int:
    return _with_account(args.username,
                         lambda account: accounts.delete(account["id"]),
                         f"Account '{args.username}' deleted.")


def cmd_blocks(_: argparse.Namespace) -> int:
    """The sign-in blocks, as in Settings -> Blocked addresses."""
    rows = accounts.list_blocks()
    if not rows:
        print("No sign-in blocks.")
        return 0

    print(f"{'ADDRESS':<40} {'BLOCK':<12} {'LEVEL':<6} LAST FAILURE")
    print("-" * 78)
    for row in rows:
        if row["permanent"]:
            state = "permanent"
        elif row["active"]:
            state = f"{row['seconds_left']} s left"
        else:
            state = "over"
        print(f"{row['ip']:<40} {state:<12} {row['level']!s:<6} {datetime_human(row['last_failure'])}")
    return 0


def cmd_unblock(args: argparse.Namespace) -> int:
    """Lifts a sign-in block without signing in.

    A permanent block shuts out everybody at that address, the administrator included -
    behind a reverse proxy without FORWARDED_ALLOW_IPS that is every visitor - and in the
    browser only a signed-in administrator can lift it.
    """
    if args.all:
        addresses = [str(row["ip"]) for row in accounts.list_blocks()]
    elif args.address:
        addresses = [args.address.strip()]
    else:
        print("Give an address, or --all.", file=sys.stderr)
        return 1

    lifted = [address for address in addresses if accounts.unblock(address)]
    if args.address and not lifted:
        print(f"There is no block of {args.address}. See  python manage.py blocks", file=sys.stderr)
        return 1
    print(f"Unblocked: {', '.join(lifted)}" if lifted else "No sign-in blocks.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The commands. The aliases are the old Czech commands (TODO.md item 0)."""
    parser = argparse.ArgumentParser(
        description="Managing the accounts of Jellyscope",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("accounts", aliases=["ucty"], help="list all accounts").set_defaults(func=cmd_list)

    add = subparsers.add_parser("add", aliases=["pridat"], help="create a new account")
    add.add_argument("username", help="the user name")
    add.add_argument("--admin", "--spravce", dest="admin", action="store_true", help="give the account administrator rights")
    add.set_defaults(func=cmd_add)

    password = subparsers.add_parser("password", aliases=["heslo"], help="change a password")
    password.add_argument("username")
    password.set_defaults(func=cmd_password)

    delete = subparsers.add_parser("delete", aliases=["smazat"], help="delete an account")
    delete.add_argument("username")
    delete.set_defaults(func=cmd_delete)

    subparsers.add_parser("blocks", help="list the sign-in blocks").set_defaults(func=cmd_blocks)

    unblock = subparsers.add_parser("unblock", help="lift a sign-in block")
    unblock.add_argument("address", nargs="?", help="the address, as `blocks` lists it")
    unblock.add_argument("--all", action="store_true", help="lift every block")
    unblock.set_defaults(func=cmd_unblock)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    # A database this creates is for its user only, the same as one the app creates (see run.py).
    os.umask(0o077)
    db.init_db()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
