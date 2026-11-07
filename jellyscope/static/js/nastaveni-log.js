// Nastavení -> Log.
// Dřív vložené v šabloně nastaveni/log.html; přesunuté beze změny logiky (CSP).

    // Odrolovat na konec: nejnovější řádek je ten, kvůli kterému se sem
    // člověk dívá. Bez tohohle by po otevření koukal na nejstarší.
    (function () {
        var box = document.getElementById("log-view");
        if (box) { box.scrollTop = box.scrollHeight; }
    })();
