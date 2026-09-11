# -*- coding: utf-8 -*-
"""Co uvidí divák přihlášený svým jellyfinovým účtem.

Tři věci, a každá se dá vypnout:

* **přihlášení přes Jellyfin** - dokud ho správce nezapne, dovnitř se
  chodí jen místními účty. Otevřít statistiky celé domácnosti je
  rozhodnutí, ne vedlejší účinek aktualizace, takže se nic nezapíná samo.

* **co uvidí ten, kdo není správce** - seznam oblastí (žebříčky,
  knihovna, přehled…), z nichž si správce zaškrtne, co je v jeho
  instalaci v pořádku. Jeden server je rodinný a nikdo tam nic netají,
  druhý je pro cizí lidi.

  Nastavuje se **zvlášť pro dvě skupiny**, protože to jsou dva různé
  případy. *Divák z Jellyfinu* přišel sám a jeho účet patří konkrétnímu
  člověku, takže mu k jeho vlastním číslům povolujeme něco navíc.
  *Místní čtenářský účet* zakládá správce ručně a obvykle právě proto,
  aby někdo viděl celé statistiky - ten proto ve výchozím stavu vidí
  všechno jako dosud, a kdo chce, ubere.

* **anonymizace** - když správce pustí diváky na stránku, kde jsou
  vypsaní lidé, nemusí je tam vidět jmény. Místo jména je „Divák 3",
  místo adresy pomlčka. Vlastní jméno divák vidí dál - o sobě přece ví.

Rozhoduje se tu, ne v šablonách: co se nemá ukázat, se nemá ani načíst,
a seznam cest je **povolující**. Co v něm není, je zakázané - routa, na
kterou se při psaní zapomene, tak nikomu nic neprozradí.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import db

# Zapnuté přihlašování jellyfinovým heslem.
LOGIN_KLIC = "jellyfin_login_enabled"

# Dvě skupiny, které nejsou správce. Rozhoduje o nich totéž nastavení,
# jen s jiným předponou klíče a jinými výchozími hodnotami.
DIVAK = "divak"          # přihlásil se jellyfinovým heslem
CTENAR = "ctenar"        # místní účet bez práv správce
SPRAVCE = "spravce"


def role(account: dict[str, Any] | None) -> str:
    """Do které skupiny účet patří. Prázdno = nepřihlášený."""
    if not account:
        return ""
    if account.get("is_admin"):
        return SPRAVCE
    return DIVAK if account.get("jellyfin_user_id") else CTENAR


class Oblast:
    """Jeden řádek ve stromu „co divák uvidí"."""

    def __init__(self, klic: str, nazev: str, popis: str,
                 cesty: tuple[str, ...], vychozi: bool = False,
                 pod: str = "") -> None:
        self.klic = klic
        self.nazev = nazev
        self.popis = popis
        self.cesty = cesty
        self.vychozi = vychozi
        self.pod = pod          # klíč nadřazené oblasti, "" = kořen


# Pořadí je pořadím ve stromu: co je `pod` něčím, se kreslí odsazeně.
#
# „Moje statistiky" v seznamu schválně nejsou. Vlastní čísla vidí divák
# vždycky - bez nich by přihlášení nemělo smysl a vypínat to není co.
OBLASTI: tuple[Oblast, ...] = (
    Oblast("zebricky", "Zjištění a žebříčky titulů",
           "Co se na serveru hraje nejvíc, co leží ladem. O titulech, ne o lidech.",
           ("/insights", "/partials/top-items"), vychozi=True),
    Oblast("knihovna", "Knihovna",
           "Kolik je čeho, kodeky, rozlišení, chybějící data.",
           ("/library", "/item/", "/series/", "/partials/recently-added"),
           vychozi=True),
    Oblast("knihovna_kdo", "…včetně „kdo to sledoval“",
           "V detailu titulu a knihovny se vypíšou i diváci. Jména lze skrýt anonymizací.",
           (), pod="knihovna"),
    Oblast("prehled", "Přehled serveru",
           "Souhrn za celý server: kolik se sledovalo, kdy, čím. Čísla jsou za všechny dohromady.",
           ("/", "/partials/daily")),
    Oblast("dashboard", "Vlastní přehled",
           "Přehled poskládaný z vybraných karet. Co v něm je, určuje správce.",
           ("/dashboard",), pod="prehled"),
    Oblast("srovnani", "Srovnání období",
           "Tentýž přehled za dvě období vedle sebe.",
           ("/srovnani",), pod="prehled"),
    Oblast("prave_hraje", "Právě se hraje",
           "Kdo co sleduje teď. Jména lze skrýt anonymizací.",
           ("/partials/now-playing", "/api/now-playing"), pod="prehled"),
    Oblast("jazyky", "Jazyky",
           "Dabing a titulky - souhrn i po jednotlivých divácích.",
           ("/languages",)),
    Oblast("uzivatele", "Seznam diváků",
           "Kdo na serveru sleduje a kolik. Jména lze skrýt anonymizací.",
           ("/users",)),
    Oblast("sit", "Síť a adresy",
           "Odkud se kdo připojuje. Tohle jsou IP adresy - rozmysli si to.",
           ("/network", "/partials/network-live")),
)

PODLE_KLICE = {oblast.klic: oblast for oblast in OBLASTI}

# Cesty, které smí každý přihlášený - na nich není nic o nikom jiném.
VZDY = (
    "/history",          # vlastni historie; filtr se vnucuje v route
    "/image/",
    "/static/",
    "/health",
    "/login",
    "/logout",
    "/setup",
    "/favicon.ico",
)


def login_zapnuty() -> bool:
    """Smí se dovnitř jellyfinovým heslem?"""
    return db.get_setting(LOGIN_KLIC, "0") == "1"


def klic_oblasti(kdo: str, oblast: "Oblast") -> str:
    return f"{kdo}_vidi_{oblast.klic}"


def _vychozi(kdo: str, oblast: "Oblast") -> bool:
    """Co platí, dokud to správce neřešil.

    Čtenářský účet zakládá správce sám a skoro vždycky proto, aby někdo
    viděl statistiky - vidí tedy všechno, dokud neubere. Divák
    z Jellyfinu přišel po svých; tomu se povoluje po kouskách.
    """
    return True if kdo == CTENAR else oblast.vychozi


def anonymizace_zapnuta(kdo: str = DIVAK) -> bool:
    """Skrývají se téhle skupině cizí jména a adresy?

    U diváka z Jellyfinu ano - přišel sám a o ostatních mu nic není.
    U čtenářského účtu ne: ten dosud jména viděl a správce ho zakládal
    s tím, že je uvidí. Kdo chce jinak, přepne to.
    """
    return db.get_setting(f"{kdo}_anonymizace",
                          "1" if kdo == DIVAK else "0") == "1"


def vidi(kdo: str, klic: str) -> bool:
    if kdo == SPRAVCE:
        return True
    oblast = PODLE_KLICE.get(klic)
    if oblast is None:
        return False
    vychozi = "1" if _vychozi(kdo, oblast) else "0"
    if db.get_setting(klic_oblasti(kdo, oblast), vychozi) != "1":
        return False
    # Pod-oblast platí jen tehdy, když je zapnutá i ta nad ní. Jinak by
    # „…včetně kdo to sledoval" pouštělo jména na stránce, kterou dotyčný
    # stejně nesmí otevřít - a po zapnutí knihovny by se to objevilo,
    # aniž by o tom někdo rozhodl.
    return vidi(kdo, oblast.pod) if oblast.pod else True


def povolene_cesty(kdo: str) -> tuple[str, ...]:
    """Adresy, které tahle skupina smí otevřít."""
    cesty: list[str] = list(VZDY)
    for oblast in OBLASTI:
        if oblast.cesty and vidi(kdo, oblast.klic):
            cesty.extend(oblast.cesty)
    return tuple(cesty)


def domovska(kdo: str, muj_divak: str = "") -> str:
    """Kam po přihlášení, když Přehled není povolený.

    Přistát na stránce s „sem nemáš přístup" hned po zadání hesla je
    nejhorší možné uvítání. Vezme se proto první, co dotyčný otevřít smí.
    """
    if muj_divak:
        return f"/users/{muj_divak}"
    if vidi(kdo, "prehled"):
        return "/"
    for cesta in ("/insights", "/library", "/languages", "/users",
                  "/network"):
        if smi_cestu(kdo, cesta):
            return cesta
    return "/history"


def smi_cestu(kdo: str, cesta: str) -> bool:
    """Smí tahle skupina otevřít tuhle adresu?

    Kořen `/` se porovnává **přesně**. Jako předpona by odpovídal každé
    adrese na světě, takže by zapnutý Přehled potichu otevřel i Nastavení
    - a tenhle seznam by přestal cokoliv hlídat.
    """
    if kdo == SPRAVCE:
        return True
    for povolena in povolene_cesty(kdo):
        if povolena == "/":
            if cesta == "/":
                return True
            continue
        if cesta == povolena:
            return True
        if povolena.endswith("/") and cesta.startswith(povolena):
            return True
        if not povolena.endswith("/") and cesta.startswith(povolena + "/"):
            return True
    return False


def strom(kdo: str) -> list[dict[str, Any]]:
    """Oblasti pro stránku Nastavení - i se stavem a odsazením."""
    return [
        {
            "klic": oblast.klic,
            "nastaveni": klic_oblasti(kdo, oblast),
            "nazev": oblast.nazev,
            "popis": oblast.popis,
            "pod": oblast.pod,
            "zapnuto": db.get_setting(
                klic_oblasti(kdo, oblast),
                "1" if _vychozi(kdo, oblast) else "0") == "1",
        }
        for oblast in OBLASTI
    ]


# ---------------------------------------------------------------------------
# Anonymizace
# ---------------------------------------------------------------------------
#
# Přezdívka musí být stálá - „Divák 3" má být zítra týž člověk jako dnes,
# jinak se z čísel nedá nic vyčíst a stránka je k ničemu. Bere se proto
# z pořadí v tabulce diváků, ne z pořadí na stránce.

# Klíče, ve kterých bývá jméno diváka nebo jeho adresa.
JMENA = ("user_name", "jmeno_uzivatele", "uzivatel")

# `label` je popisek v grafu - u řádku s `user_id` je to jméno diváka,
# jinde název titulu nebo jazyka. Rozhoduje tedy soused, ne klíč sám.
POPISEK = "label"
ADRESY = ("remote_address", "ip", "adresa_klienta")

# `adresa` se jmenuje i odkaz na vydání a adresa Jellyfinu, takže ten
# klíč sám o sobě nic neznamená. Schovává se jen v řádku, který je
# doopravdy o připojení - pozná se podle sousedních sloupců.
SOUSEDI_ADRESY = ("domaci", "lidi", "zeme", "mesto", "naposledy")

SKRYTA_ADRESA = "—"


def _poradi() -> dict[str, int]:
    radky = db.query_all("SELECT id FROM users ORDER BY id")
    return {str(dict(r)["id"]): poradi
            for poradi, r in enumerate(radky, start=1)}


def prezdivka(user_id: str, poradi: dict[str, int] | None = None) -> str:
    cisla = _poradi() if poradi is None else poradi
    return f"Divák {cisla.get(str(user_id), 0) or '?'}"


def anonymizuj(hodnota: Any, muj_id: str,
               poradi: dict[str, int] | None = None) -> Any:
    """Projde data pro šablonu a schová, co je o ostatních.

    Dělá se to tady, na jednom místě, a ne v šablonách: šablon jsou
    desítky a stačí na jednu zapomenout. Sem projde všechno, co se
    kreslí - takže co se jmenuje `user_name`, je jméno, ať je to
    kdekoliv.

    Vlastní jméno i vlastní adresa zůstávají: divák o sobě ví.
    """
    poradi = _poradi() if poradi is None else poradi

    if isinstance(hodnota, dict):
        vysledek = {}
        cizi_id = str(hodnota.get("user_id") or hodnota.get("id") or "")
        cizi = cizi_id != muj_id
        je_divak = _je_divak(hodnota)
        for klic, obsah in hodnota.items():
            if cizi and isinstance(obsah, str) and (
                    klic in JMENA
                    or (klic == POPISEK and "user_id" in hodnota)
                    or (klic == "name" and je_divak)):
                vysledek[klic] = prezdivka(cizi_id, poradi)
            elif cizi and cizi_id and klic in ("user_id",) or (
                    cizi and cizi_id and klic == "id" and je_divak):
                # I samotné id je stopa: podle něj se dá člověk poznat
                # v adrese, v odkazu i napříč stránkami. Nahrazuje se
                # stálou náhradou, takže se řádky dají pořád rozlišit -
                # jen nevedou k nikomu konkrétnímu.
                vysledek[klic] = _nahradni_id(cizi_id, poradi)
            elif cizi and isinstance(obsah, str) and (
                    klic in ADRESY
                    or (klic == "adresa" and _je_adresa_radek(hodnota))):
                vysledek[klic] = SKRYTA_ADRESA
            else:
                vysledek[klic] = anonymizuj(obsah, muj_id, poradi)
        return vysledek

    if isinstance(hodnota, list):
        return [anonymizuj(polozka, muj_id, poradi) for polozka in hodnota]
    if isinstance(hodnota, tuple):
        return tuple(anonymizuj(polozka, muj_id, poradi) for polozka in hodnota)
    return hodnota


def _je_divak(radek: dict[str, Any]) -> bool:
    """Je tenhle řádek divák z tabulky `users`, nebo něco jiného?

    Rozhoduje se podle sloupců, které má jen divák - `name` samo o sobě
    má i titul, knihovna a spousta dalšího, a přejmenovat film na
    „Divák 2" by bylo horší než ukázat jméno.
    """
    if "user_id" in radek:
        return True
    return any(klic in radek for klic in
               ("is_administrator", "is_disabled", "relaci", "last_seen",
                "hours", "last_activity"))


def _nahradni_id(user_id: str, poradi: dict[str, int] | None = None) -> str:
    cisla = _poradi() if poradi is None else poradi
    return f"skryto-{cisla.get(str(user_id), 0) or '?'}"


def _je_adresa_radek(radek: dict[str, Any]) -> bool:
    """Je `adresa` v tomhle řádku IP adresa diváka, nebo odkaz?"""
    return any(klic in radek for klic in SOUSEDI_ADRESY)


def klice_oblasti() -> Iterable[str]:
    return (oblast.klic for oblast in OBLASTI)
