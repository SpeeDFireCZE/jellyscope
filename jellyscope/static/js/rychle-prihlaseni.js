// Přihlášení kódem: ptá se, jestli už kód někdo potvrdil.
// Dřív vložené v šabloně login_quick.html; přesunuté beze změny logiky (CSP).
(function () {
    var TEXTY = JSON.parse((document.getElementById("texty-js") || {}).textContent || "{}");
    var stav = document.getElementById("quick-stav");
    var bezi = true;

    function zeptej_se() {
        if (!bezi) { return; }
        fetch("/login/quick/stav", { credentials: "same-origin",
                                     cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { return; }
                if (data.hotovo) {
                    bezi = false;
                    window.location.href = "/";
                    return;
                }
                if (data.konec) {
                    bezi = false;
                    stav.textContent = TEXTY.kod_neplati;
                }
            })
            .catch(function () { /* vypadek site neni duvod prestat */ });
    }

    document.getElementById("quick-ted").addEventListener("click", zeptej_se);
    /* Po vterinach, ne po desetinach: potvrzuje to clovek v jine
       aplikaci, takze rychlejsi ptani jen zatezuje Jellyfin. */
    setInterval(zeptej_se, 2000);
})();
