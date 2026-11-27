"""The demo's switch: in the demo mode the clients get a made-up app in place of the network.

The demo (JELLYSCOPE_DEMO) shows the apps around Jellyfin with made-up data through the real code. Every
connection to an app goes through one core (services.HttpClient), Jellyfin's through jellyfin.JellyfinClient,
and the core asks here for its transport when none is passed:

* in the demo mode it gets the made-up app registered under its name (`register`; demo_apps.load_fakes
  registers them all) - an httpx.MockTransport that answers inside the process. A name with nothing
  registered gets one that answers 503 to everything: in the demo nothing goes to the network. What the
  network refuses before it is asked, the switch refuses the same way (`_as_the_network`): an address
  without http(s) - none entered yet - or without a host is an error of the transport, as the clients know
  it, never a request the made-up app answers;
* outside the demo mode always None - the client takes the network as always. A real installation never
  gets a made-up app, whatever is registered.

Shared by every app: it names no app's package nor table (tests/test_arr_tree.py).
"""
from __future__ import annotations

from collections.abc import Callable

import httpx

from .config import load_config

# A made-up app: a request -> its answer, inside the process (httpx.MockTransport).
Handler = Callable[[httpx.Request], httpx.Response]

_HANDLERS: dict[str, Handler] = {}


def register(name: str, handler: Handler) -> None:
    """Register the made-up app the clients named `name` get in the demo mode ("Sonarr", "Jellyfin").

    Registering the same name again replaces the handler.
    """
    _HANDLERS[name] = handler


def registered(name: str) -> bool:
    """Is a made-up app registered under `name`? (demo_apps connects only those.)"""
    return name in _HANDLERS


def clear() -> None:
    """Forget every registered made-up app (tests)."""
    _HANDLERS.clear()


def _unavailable(request: httpx.Request) -> httpx.Response:
    """The demo has no made-up app of this name: 503 to everything, nothing goes to the network."""
    return httpx.Response(503)


def _as_the_network(handler: Handler) -> Handler:
    """The made-up app behind what the network checks first (httpcore's connection pool): a request without the
    protocol (an empty or a relative address) or with another one raises httpx.UnsupportedProtocol, one without a
    host httpx.ConnectError (the network finds no such host). Otherwise httpx would hand the made-up app a relative
    address and fall afterwards on its answer with a ValueError the clients do not catch."""
    def answer(request: httpx.Request) -> httpx.Response:
        scheme = request.url.scheme
        if not scheme:
            raise httpx.UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol.", request=request)
        if scheme not in ("http", "https"):
            raise httpx.UnsupportedProtocol(f"Request URL has an unsupported protocol '{scheme}://'.", request=request)
        if not request.url.host:
            raise httpx.ConnectError("Request URL has no host.", request=request)
        return handler(request)

    return answer


def transport(name: str) -> httpx.MockTransport | None:
    """The transport for a client named `name`: None outside the demo mode (the network), the made-up app in it."""
    if not load_config().demo_mode:
        return None
    return httpx.MockTransport(_as_the_network(_HANDLERS.get(name, _unavailable)))
