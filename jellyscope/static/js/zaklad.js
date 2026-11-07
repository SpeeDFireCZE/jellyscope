// Základ, který potřebuje každá stránka - i přihlášení, průvodce prvním
// spuštěním a chybová stránka, které nestojí na base.html a app.js
// nenačítají. Načítá se v <head> BEZ `defer`: motiv se musí nastavit dřív,
// než se stránka vykreslí.
//
// Obsluha prvků je tu deklarativní: šablona napíše atribut data-…, tenhle
// soubor ví, co s ním. Vložený kód (onclick=, onerror=…) hlavička CSP
// nepustí - a nemá: kdyby ho pouštěla, spustila by i ten, který by do
// stránky propašovala chyba ve výpisu dat.
(function () {
    // Úložiště prohlížeče je jen pohodlí (zapamatovaný motiv). Kde je
    // zablokované (Firefox s vypnutým úložištěm, rozšíření, zakázaná data
    // webu), vyhodí výjimku nebo chybí úplně - a ta výjimka nesmí shodit
    // zbytek souboru: pod ní se registruje i potvrzení před smazáním.
    function precti(klic) {
        try { return window.localStorage.getItem(klic); } catch (e) { return null; }
    }
    function uloz(klic, hodnota) {
        try { window.localStorage.setItem(klic, hodnota); } catch (e) { /* motiv platí jen pro tuhle stránku */ }
    }

    // Nastavení tmavého/světlého režimu se čte co nejdřív, ještě před
    // vykreslením stránky. Kdyby se to dělalo až později, stránka by
    // na okamžik problikla ve špatných barvách.
    var saved = precti("jellyscope-theme");
    if (saved === "dark" || saved === "light") {
        document.documentElement.setAttribute("data-theme", saved);
    }

    function toggleTheme() {
        var root = document.documentElement;
        var current = root.getAttribute("data-theme");
        if (!current) {
            // Zatím se režim řídí nastavením systému - zjistíme, jaký je,
            // a přepneme na ten druhý.
            current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        }
        var next = current === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        uloz("jellyscope-theme", next);
    }

    // Obrázek, který se nenačte (plakát, který Jellyfin nemá). Událost
    // `error` u obrázku nebublá, proto zachytávací fáze (`true`) - a jeden
    // posluchač na celý dokument platí i pro výřezy vložené později.
    //   data-pri-chybe="skryt"         obrázek se schová
    //   data-pri-chybe="odebrat"       obrázek zmizí z DOMu
    //   data-pri-chybe="skryt-rodice"  schová se rámeček kolem něj
    document.addEventListener("error", function (event) {
        var img = event.target;
        if (!img || !img.dataset || !img.dataset.priChybe) { return; }
        if (img.dataset.priChybe === "odebrat") {
            img.remove();
        } else if (img.dataset.priChybe === "skryt-rodice") {
            img.parentElement.style.display = "none";
        } else {
            img.style.display = "none";
        }
    }, true);

    // Kliknutí - delegované na dokument, takže platí i pro obsah, který
    // přibude později.
    document.addEventListener("click", function (event) {
        var prvek = event.target.closest && event.target.closest(
            "[data-zavrit-okno], [data-zavrit-hlasku], [data-prepnout-motiv], [data-otevrit-okno]");
        if (!prvek) { return; }
        if (prvek.hasAttribute("data-zavrit-okno")) {
            prvek.closest("dialog").close();
        } else if (prvek.hasAttribute("data-zavrit-hlasku")) {
            prvek.closest(".flash-toast").remove();
        } else if (prvek.hasAttribute("data-prepnout-motiv")) {
            toggleTheme();
        } else {
            var okno = document.getElementById(prvek.dataset.otevritOkno);
            if (okno) { okno.showModal(); }
        }
    });

    // Změna pole: odeslat formulář (filtr bez tlačítka), nebo ukázat název
    // vybraného souboru (výběr souboru má vlastní vzhled, holé pole je
    // schované).
    document.addEventListener("change", function (event) {
        var pole = event.target;
        if (pole.hasAttribute("data-odeslat-pri-zmene")) {
            pole.form.submit();
        } else if (pole.hasAttribute("data-nazev-souboru")) {
            pole.parentNode.querySelector(".file-name").textContent =
                pole.files.length ? pole.files[0].name : pole.dataset.nazevSouboru;
        }
    });

    // Potvrzení před nevratnou akcí (smazat, obnovit, restartovat).
    // Registruje se v <head>, tedy dřív než ostatní posluchače odeslání -
    // stejně jako dřív onsubmit= ve značce: nejdřív otázka, pak zbytek.
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (form.dataset && form.dataset.potvrdit && !window.confirm(form.dataset.potvrdit)) {
            event.preventDefault();
        }
    });
})();
