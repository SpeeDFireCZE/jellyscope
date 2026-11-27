"""The tests' guard against the network: every way out of the process is recorded and refused - but the database.

The demo's tests prove that nothing of the demo goes to the network: its made-up apps answer inside the process.
`refuse_network()` substitutes every socket function the process could leave by with one that records the attempt
and raises ConnectionRefusedError. On PostgreSQL (.superpowers/tools/test_on_pg.py) the test's own database is a
server too: exactly its host and port (db.database_config(), read at every attempt) go to the real functions -
nothing else, not another port of that host nor another host. On SQLite nothing goes.

The file starts with an underscore, so the suite does not run it as a test. `jellyscope` is imported only inside
functions: the test sets up its environment (the database) before the first import.
"""
from __future__ import annotations

import socket
from typing import Any

_REAL = {"create_connection": socket.create_connection, "getaddrinfo": socket.getaddrinfo,
         "connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex}


def _database() -> tuple[str, str] | None:
    """(host, port) of the test's database when it is PostgreSQL, else None."""
    from jellyscope import db

    config = db.database_config()
    return (str(config.host), str(config.port)) if config.is_postgres else None


def _is_database(host: Any, port: Any) -> bool:
    return _database() == (str(host), str(port))


def _is_database_address(address: Any) -> bool:
    """A socket's address (an IP and a port, or a name and a port) of the database: its host or what it resolves to."""
    target = _database()
    if target is None or not isinstance(address, tuple) or len(address) < 2 or str(address[1]) != target[1]:
        return False
    if str(address[0]) == target[0]:
        return True
    return str(address[0]) in {info[4][0] for info in _REAL["getaddrinfo"](target[0], int(target[1]))}


def refuse_network() -> list[str]:
    """Substitute the socket functions; returns the list every refused attempt is recorded in (empty = none)."""
    refused: list[str] = []

    def refuse(args: tuple[Any, ...]) -> None:
        refused.append(repr(args[1:] if args and isinstance(args[0], socket.socket) else args)[:80])
        raise ConnectionRefusedError("the test allows no network")

    def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_database(host, port):
            return _REAL["getaddrinfo"](host, port, *args, **kwargs)
        return refuse((host, port, *args))

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(address, tuple) and len(address) >= 2 and _is_database(*address[:2]):
            return _REAL["create_connection"](address, *args, **kwargs)
        return refuse((address, *args))

    def connect(self: socket.socket, address: Any) -> Any:
        if _is_database_address(address):
            return _REAL["connect"](self, address)
        return refuse((self, address))

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if _is_database_address(address):
            return _REAL["connect_ex"](self, address)
        return refuse((self, address))

    socket.create_connection = create_connection
    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    return refused
