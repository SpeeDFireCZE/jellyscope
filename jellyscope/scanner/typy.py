"""Druhy položek, které Jellyscope vede (film, díl) a obaly, které soubor nemají.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations


# Druhy polozek, ktere si Jellyscope vede v knihovne.
#
# Jsou to prave ty, ktere odpovidaji jednomu souboru na disku - film
# a dil serialu. Serial ani rada zadny soubor nemaji, takze by u nich
# nebylo co merit: velikost, kodek, bitrate, nic.
#
# Tenhle seznam **musi sedet s tim, na co se ptame Jellyfinu**
# (IncludeItemTypes v jellyfin.py). Kdyz se do tabulky dostane polozka
# jineho druhu, synchronizace ji uz nikdy neuvidi - a `_mark_missing()`
# ji pri kazdem behu oznaci za zmizelou. V knihovne pak strasi
# "archivovany" serial, ktery nikdo nesmazal a ktery v Jellyfinu je.
SPRAVOVANE_TYPY = ("Movie", "Episode")

# Obaly, ktere Jellyfin umi poslat misto polozek v nich - kolekce (od
# dvanactky i pres filtr typu, viz CollapseBoxSetItems v jellyfin.py),
# serial, rada, slozka, playlist. Ty se nezapisuji nikdy. Schvalne
# **ne** obracene ("jen Movie a Episode"): cerstve pridany soubor posila
# Jellyfin chvili jako `Video`, nez ho zaradi, a ten se ulozit MA - pri
# dalsim behu se sam opravi na film nebo dil (viz sync_recent).
OBALY = ("BoxSet", "Series", "Season", "Folder", "CollectionFolder",
         "Playlist", "UserView")

# Druh, ktery synchronizace zapsat SMI, i kdyz to neni film ani dil:
# cerstve pridany soubor, ktery Jellyfin jeste nezaradil. Pri dalsim behu
# se sam opravi (viz sync_recent), takze se ho nesmi zbavit ani uklid.
ZATIM_NEZARAZENY = "Video"
ZAPISOVANE_TYPY = SPRAVOVANE_TYPY + (ZATIM_NEZARAZENY,)


def je_obal(item: dict) -> bool:
    """Je to kontejner, ne titul? Podle typu, nebo podle IsFolder.

    `IsFolder` je tu jako pojistka pro druh, ktery zatim neznáme. Filmu
    ripnutemu jako slozka (BluRay, DVD) to neublizi: Jellyfin u nich
    hlasi `IsFolder: false` a druh `Movie` - overeno na 10.10.7 i 12.0.0.
    """
    return (str(item.get("Type") or "") in OBALY
            or bool(item.get("IsFolder")))
