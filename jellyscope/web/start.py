"""Spuštění a vypnutí aplikace (lifespan): co se rozběhne se serverem a co se při vypnutí zastaví.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import accounts, applog, collector, db, scanner, sekce, tasks
from .zaklad import config, log


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Co se stane pri startu a pri vypnuti aplikace.

    Tady se poustí sberac dat na pozadi. Bezi soubezne s webem: zatimco
    ty klikas po strankach, on se kazdych par sekund pta Jellyfinu,
    co se prave hraje.
    """
    # Zápis logu do souboru zapínáme jako první, ať se do něj dostane
    # i případná chyba při přípravě databáze - právě ta je nejzajímavější.
    soubor = applog.setup()
    if soubor:
        log.info("log se píše i do souboru: %s", soubor)

    db.init_db()
    log.info("databaze pripravena")
    # Kolik starých logů držet, i pro rotaci pojistkou podle velikosti.
    # Log se zapíná před databází, takže se to dozví až teď.
    applog.nastav_pocet(tasks.pocet_rotace())
    # Přehled pro všechny má od verze s Mým přehledem název. Stávající
    # zapnutý bez názvu dostane ten, pod kterým ho lidé znali.
    sekce.doplnit_nazev()

    # Zona aplikace do prostredi procesu.
    #
    # Vypis casu si ji bere sam (formatting.zona()), ale deleni na dny
    # a hodiny dela SQL - SQLite modifikatorem 'localtime', PostgreSQL
    # podle zony spojeni. Obojí cte zonu OPERACNIHO SYSTEMU, ne nasi
    # promennou, takze ji musime nastavit do prostredi drive, nez se
    # otevre prvni spojeni.
    #
    # `tzset()` je jen na unixu. Na Windows se zona procesu za behu
    # prepnout neda, takze tam plati systemova - vypis casu je spravne
    # tak jako tak, jen deleni dnu jde podle stroje. Ostry provoz bezi
    # na Linuxu, kde to plati cele.
    zona_aplikace = (db.get_setting("app_timezone", "") or "").strip()
    if zona_aplikace:
        os.environ["TZ"] = zona_aplikace
        if hasattr(time, "tzset"):
            time.tzset()
            log.info("casova zona aplikace: %s", zona_aplikace)
        else:
            log.warning("casovou zonu %s nejde na tomhle systemu nastavit procesu; "
                        "vypis casu ji respektuje, deleni dnu v grafech ne",
                        zona_aplikace)

    # Do logu, at se pri dotazu "proc mi to rika, ze jsem v kontejneru"
    # nemusi hadat. Rozlisujeme dve veci: jakykoliv kontejner (rozhoduje
    # o zalohach) a nas obraz (rozhoduje o aktualizaci).
    if config.in_docker or config.nas_obraz:
        log.info("prostredi: kontejner=%s, nas obraz=%s",
                 config.in_docker, config.nas_obraz)

    # V kontejneru si slozku na zalohy nastavime sami - viz db.predvyplnene_zalohy().
    zalohy = db.predvyplnene_zalohy()
    if zalohy:
        log.info("v kontejneru: slozka na zalohy nastavena na %s", zalohy)

    # Polozky, ktere do knihovny nikdy nemely prijit (serial misto dilu).
    # Bez toho by se pri kazde synchronizaci znovu tvarily jako zmizele.
    fantomu = scanner.uklid_fantomu()
    if fantomu:
        log.info("z knihovny odstraneno %s polozek, ktere do ni nepatri", fantomu)

    # Vyprsele blokace prihlasovani uz nic nerikaji - trvale zustavaji.
    applog.nastav_jazyk(db.get_setting("log_language", "cs"))

    smazano = accounts.uklid_blokaci()
    if smazano:
        log.info("uklizeno %s starych blokaci prihlasovani", smazano)

    background = []
    if config.demo_mode:
        # V ukazkovem rezimu neni na co se pripojovat. Sberac by jen plnil
        # log chybami a pri startu uzavrel vymyslene prehravani, ktere ma
        # byt na Prehledu videt.
        db.set_setting(collector.STATUS_KEY, "demo")
        log.info("ukazkovy rezim - sberac se nespousti")
        # Vymyslena data starnou: jsou vyrobena k okamziku seedu a verejna
        # ukazka bezi tydny. Jednou za den se vyrobi znovu, posunuta
        # k dnesku - jinak z grafu "poslednich 30 dnu" zbyde cara.
        from .. import demodata

        background.append(
            asyncio.create_task(demodata.obnovuj(), name="demo-obnova")
        )
        # A v Prave se hraje porad neco bezi - vymysleny stream hraje dal.
        background.append(
            asyncio.create_task(demodata.udrzuj_zive(), name="demo-zivy-stream")
        )
    else:
        background.append(
            asyncio.create_task(collector.run_forever(), name="collector")
        )
        background.append(
            asyncio.create_task(tasks.run_scheduler(), name="scheduler")
        )

    try:
        yield
    finally:
        # Pri vypinani ulohy slusne ukoncime a pockame, az doopravdy skonci.
        for job in background:
            job.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        log.info("ulohy na pozadi ukonceny")
        # Zasobnik spojeni drzi u PostgreSQL otevrena spojeni. Kdyz ho
        # nezavreme, zustanou na serveru viset az do jeho vlastniho limitu.
        db.close_pool()
