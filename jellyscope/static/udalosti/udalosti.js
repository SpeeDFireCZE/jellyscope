/* ---------------------------------------------------------------------------
   Sezónní výzdoba: animace přes celou stránku.

   Načítá se jen v době svátku (templates/_udalost_hlava.html); který svátek
   to je, říká <html data-udalost="...">. Vrstva leží přes celou stránku,
   je poloprůhledná a klikání jde skrz (udalosti.css), takže nic neblokuje.

   Bez pohybu - systémové „omezit animace" nebo volba na stránce Můj účet
   (data-udalost-pohyb="0") - se nic nehýbe, jen se rozestaví pár stojících
   ozdob. Na skryté kartě animace stojí, ať zbytečně nežere procesor.

   Vzhled prvků (sníh, netopýři, rachejtle, zajíček) je v CSS svátku.
   Na konci je rámec her, které se otevřou po kliknutí na ozdobu.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    var koren = document.documentElement;
    var svatek = koren.getAttribute("data-udalost");
    if (!svatek || !document.body) { return; }

    // Stropy počtu prvků - víc už jen zatěžuje procesor, ne oko.
    var SNIH = 50, SNIH_MALO = 25;
    var NETOPYRI = 6, NETOPYRI_MALO = 3;
    var UZKE = 720;   // px - pod touhle šířkou okna polovina prvků

    var SVG_VLOCKA = '<svg viewBox="-10 -10 20 20"><g stroke="currentColor" stroke-width="1.7" stroke-linecap="round" fill="none">' +
        '<path d="M0-9V9M-7.8-4.5L7.8 4.5M-7.8 4.5L7.8-4.5"/>' +
        '<path d="M-2.2-7 0-5 2.2-7M-2.2 7 0 5 2.2 7M-7.1-1.6-4.3-2.5-4.9-5.3M7.1 1.6 4.3 2.5 4.9 5.3M-4.9 5.3-4.3 2.5-7.1 1.6M4.9-5.3 4.3-2.5 7.1-1.6"/></g></svg>';
    var SVG_NETOPYR = '<svg viewBox="-14 -7 28 17">' +
        '<path d="M0 2 C-2-3-7-5.5-13.5-2.5 C-11.5-.5-10.5 2.5-10.5 5.5 C-8.5 3.8-6.5 4.3-5.5 7 C-4.3 5-2.3 5.2 0 8.5 C2.3 5.2 4.3 5 5.5 7 C6.5 4.3 8.5 3.8 10.5 5.5 C10.5 2.5 11.5-.5 13.5-2.5 C7-5.5 2-3 0 2 Z"/>' +
        '<ellipse cx="0" cy="3.2" rx="2.3" ry="3.4"/>' +
        '<path d="M-2.1 1.5 L-1.7-3 L-.5-.6 L.5-.6 L1.7-3 L2.1 1.5 Z"/>' +
        '<circle cx="-.8" cy=".6" r=".45" fill="#ff9d2e"/><circle cx=".8" cy=".6" r=".45" fill="#ff9d2e"/></svg>';
    var SVG_PAVOUK = '<svg viewBox="-12 -9 24 22"><g style="stroke:var(--udalost-netopyr)" stroke-width="1.3" fill="none" stroke-linecap="round">' +
        '<path d="M-2.5-1 L-8-6 L-11-2M-2.5 0 L-9-2 L-12 2M-2.5 1.5 L-9 3.5 L-11 8M-2.5 3 L-7 7 L-8 11"/>' +
        '<path d="M2.5-1 L8-6 L11-2M2.5 0 L9-2 L12 2M2.5 1.5 L9 3.5 L11 8M2.5 3 L7 7 L8 11"/></g>' +
        '<circle cx="0" cy="-3" r="2.7" style="fill:var(--udalost-netopyr)"/><ellipse cx="0" cy="2.8" rx="3.7" ry="4.6" style="fill:var(--udalost-netopyr)"/>' +
        '<circle cx="-1" cy="-3.5" r=".6" fill="#ff3b3b"/><circle cx="1" cy="-3.5" r=".6" fill="#ff3b3b"/></svg>';
    var SVG_ZAJIC = '<svg viewBox="0 0 60 50"><g style="stroke:var(--udalost-zajic-obrys)" stroke-width="1">' +
        '<ellipse cx="37" cy="10" rx="3.3" ry="10" transform="rotate(-12 37 10)" style="fill:var(--udalost-zajic)"/>' +
        '<ellipse cx="44" cy="10" rx="3.3" ry="10" transform="rotate(12 44 10)" style="fill:var(--udalost-zajic)"/>' +
        '<ellipse cx="24" cy="34" rx="16" ry="12" style="fill:var(--udalost-zajic)"/>' +
        '<circle cx="40.5" cy="25" r="9" style="fill:var(--udalost-zajic)"/>' +
        '<ellipse cx="31" cy="45.5" rx="6.5" ry="2.8" style="fill:var(--udalost-zajic)"/></g>' +
        '<circle cx="8.5" cy="31" r="4.6" fill="#fff" style="stroke:var(--udalost-zajic-obrys)" stroke-width="1"/>' +
        '<ellipse cx="37" cy="10.5" rx="1.4" ry="7" transform="rotate(-12 37 10)" fill="#f4a7b9"/>' +
        '<ellipse cx="44" cy="10.5" rx="1.4" ry="7" transform="rotate(12 44 10)" fill="#f4a7b9"/>' +
        '<circle cx="44.5" cy="23" r="1.5" fill="#222"/><circle cx="49" cy="26.5" r="1.4" fill="#e57b94"/></svg>';
    var BARVY_VAJEC = [["#f2c94c", "#e05a8a"], ["#7cc6f2", "#f2c94c"], ["#f28b82", "#ffffff"],
                       ["#a8e07c", "#7b5bd6"], ["#c9a7f2", "#ffd36e"]];
    var BARVY_RACHEJTLI = ["#ffd166", "#ff6b6b", "#7aa2ff", "#6ee7a8", "#ff8fd1", "#e0b03a"];
    var vajicek = 0;

    function svgVajicko(barvy) {
        // Každé vajíčko vlastní ořez - stejné id dvakrát by vzalo první.
        var id = "udalost-v" + (vajicek++);
        return '<svg viewBox="0 0 14 18"><defs><clipPath id="' + id + '"><ellipse cx="7" cy="9.5" rx="6" ry="8"/></clipPath></defs>' +
            '<ellipse cx="7" cy="9.5" rx="6" ry="8" fill="' + barvy[0] + '"/><g clip-path="url(#' + id + ')" fill="none" stroke-width="1.7">' +
            '<path d="M0 8 l2-2 l2 2 l2-2 l2 2 l2-2 l2 2 l2-2" stroke="' + barvy[1] + '"/>' +
            '<path d="M0 12.5 h14" stroke="' + barvy[1] + '" opacity=".7"/></g></svg>';
    }

    var vrstva = document.createElement("div");
    vrstva.className = "udalost-efekt";
    vrstva.setAttribute("aria-hidden", "true");
    document.body.appendChild(vrstva);

    var systemKlid = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    var casovace = [];
    var skryta = document.hidden;

    function bezPohybu() {
        return koren.getAttribute("data-udalost-pohyb") === "0" || Boolean(systemKlid && systemKlid.matches);
    }
    function uzke() { return window.innerWidth < UZKE; }
    function nahodne(od, do_) { return od + Math.random() * (do_ - od); }
    function vyber(seznam) { return seznam[Math.floor(Math.random() * seznam.length)]; }
    function po(ms, fn) { casovace.push(setTimeout(fn, ms)); }
    // Opakované spouštění (rachejtle, zajíček) na skryté kartě vynechá kolo.
    function kazdych(ms, fn) { casovace.push(setInterval(function () { if (!skryta) { fn(); } }, ms)); }
    function prvek(trida, html, promenne) {
        var el = document.createElement("span");
        el.className = trida;
        el.innerHTML = html;
        Object.keys(promenne || {}).forEach(function (k) { el.style.setProperty(k, promenne[k]); });
        vrstva.appendChild(el);
        return el;
    }
    function vycisti() {
        casovace.forEach(function (c) { clearTimeout(c); clearInterval(c); });
        casovace = [];
        vrstva.getAnimations({ subtree: true }).forEach(function (a) { a.cancel(); });
        vrstva.innerHTML = "";
    }

    var animace = {
        vanoce: function () {
            var pocet = uzke() ? SNIH_MALO : SNIH;
            for (var i = 0; i < pocet; i++) {
                var d = nahodne(7, 12);
                var velka = Math.random() < 0.45;
                prvek("vlocka", "<i>" + (velka ? SVG_VLOCKA : "<b></b>") + "</i>", {
                    "--x": nahodne(0, 100) + "%", "--s": (velka ? nahodne(9, 15) : nahodne(3, 6)) + "px",
                    "--o": nahodne(0.55, 0.95).toFixed(2), "--d": d.toFixed(2) + "s",
                    "--z": (-nahodne(0, d)).toFixed(2) + "s"
                });
            }
        },
        halloween: function () {
            var w = vrstva.clientWidth, h = vrstva.clientHeight;
            var pocet = uzke() ? NETOPYRI_MALO : NETOPYRI;
            for (var i = 0; i < pocet; i++) {
                var d = nahodne(7, 11);
                var el = prvek("netopyr" + (i % 2 ? " zpet" : ""), "<i>" + SVG_NETOPYR + "</i>", {
                    "--y": nahodne(20, h * 0.85) + "px", "--s": nahodne(30, 48) + "px",
                    "--d": d.toFixed(2) + "s", "--z": (-nahodne(0, d)).toFixed(2) + "s"
                });
                el.firstChild.style.animationDelay = (-nahodne(0, 1.3)).toFixed(2) + "s";
            }
            // Pavouk se spouští do volného místa vpravo nahoře, k nadpisu.
            prvek("pavouk", '<span class="nit"></span>' + SVG_PAVOUK, {
                "--x": (w * 0.88) + "px", "--hloubka": (uzke() ? 30 : 58) + "px"
            });
        },
        silvestr: function () {
            var w = vrstva.clientWidth, h = vrstva.clientHeight;
            function raketa() {
                var x = nahodne(40, w - 40);
                var cil = h * nahodne(0.1, 0.42);
                var barva = vyber(BARVY_RACHEJTLI);
                var r = document.createElement("span");
                r.className = "raketa";
                r.style.left = x + "px";
                r.style.top = h + "px";
                r.style.background = barva;
                r.style.boxShadow = "0 0 6px " + barva;
                vrstva.appendChild(r);
                var let_ = r.animate([{ transform: "translateY(0)" }, { transform: "translateY(" + (cil - h) + "px)" }],
                                     { duration: 950, easing: "cubic-bezier(.2,.6,.4,1)", fill: "forwards" });
                let_.onfinish = function () { r.remove(); vybuch(x, cil, barva); };
            }
            function vybuch(x, y, barva) {
                var n = 20, polomer = uzke() ? nahodne(35, 55) : nahodne(55, 85);
                for (var i = 0; i < n; i++) {
                    var uhel = (i / n) * Math.PI * 2;
                    var c = document.createElement("span");
                    c.className = "jiskra";
                    c.style.left = x + "px";
                    c.style.top = y + "px";
                    c.style.background = barva;
                    c.style.boxShadow = "0 0 7px " + barva;
                    vrstva.appendChild(c);
                    var k = nahodne(0.75, 1.15);
                    c.animate([{ transform: "translate(0,0) scale(1)", opacity: 1 },
                               { transform: "translate(" + (Math.cos(uhel) * polomer * k) + "px," +
                                 (Math.sin(uhel) * polomer * k + polomer * 0.4) + "px) scale(.35)", opacity: 0 }],
                              { duration: 1400, easing: "cubic-bezier(.1,.7,.3,1)", fill: "forwards" })
                        .onfinish = (function (el) { return function () { el.remove(); }; })(c);
                }
            }
            raketa();
            kazdych(1000, function () { raketa(); if (Math.random() < 0.35) { po(300, raketa); } });
        },
        velikonoce: function () {
            var w = vrstva.clientWidth;
            var s = uzke() ? 44 : 60;
            function beh() {
                var zajic = prvek("zajic", "<i>" + SVG_ZAJIC + "</i>", { "--s": s + "px" });
                var snesl = 0;
                var t = setInterval(function () {
                    if (skryta) { return; }
                    var r = zajic.getBoundingClientRect(), v = vrstva.getBoundingClientRect();
                    var x = r.left - v.left + s * 0.15;
                    if (x < 10 || x > w - 20 || snesl > 5) { return; }
                    snesl++;
                    var vajicko = prvek("vajicko", svgVajicko(vyber(BARVY_VAJEC)));
                    vajicko.style.left = x + "px";
                    vajicko.animate([{ transform: "scale(0)", opacity: 0 },
                                     { transform: "scale(1.15)", opacity: 1, offset: 0.12 },
                                     { transform: "scale(1)", opacity: 1, offset: 0.2 },
                                     { transform: "scale(1)", opacity: 1, offset: 0.8 },
                                     { transform: "scale(.8)", opacity: 0 }], { duration: 4500, fill: "forwards" })
                        .onfinish = function () { vajicko.remove(); };
                }, 1500);
                casovace.push(t);
                po(10500, function () { clearInterval(t); zajic.remove(); });
            }
            beh();
            kazdych(13000, beh);
        }
    };

    // Bez pohybu: pár ozdob, které stojí na okraji.
    var klidne = {
        vanoce: function () {
            for (var i = 0; i < 16; i++) {
                var el = prvek("staticky vlocka-st", SVG_VLOCKA);
                el.style.left = nahodne(2, 96) + "%";
                el.style.top = nahodne(2, 96) + "%";
                el.style.width = nahodne(8, 14) + "px";
                el.style.transform = "rotate(" + nahodne(0, 60) + "deg)";
            }
        },
        halloween: function () {
            [[0.55, 18], [0.72, 34]].forEach(function (p) {
                var el = prvek("staticky netopyr-st", SVG_NETOPYR);
                el.style.left = (vrstva.clientWidth * p[0]) + "px";
                el.style.top = p[1] + "px";
            });
            var pavouk = prvek("staticky pavouk-st", '<span class="nit"></span>' + SVG_PAVOUK);
            pavouk.style.left = (vrstva.clientWidth * 0.88) + "px";
            pavouk.style.top = (uzke() ? 30 : 58) + "px";
        },
        silvestr: function () {
            [[0.35, 40, "#f2c14e"], [0.6, 28, "#ff6b6b"], [0.8, 44, "#7aa2ff"]].forEach(function (p) {
                for (var i = 0; i < 14; i++) {
                    var uhel = (i / 14) * Math.PI * 2, polomer = 24;
                    var c = prvek("staticky jiskra", "");
                    c.style.left = (vrstva.clientWidth * p[0] + Math.cos(uhel) * polomer) + "px";
                    c.style.top = (p[1] + Math.sin(uhel) * polomer) + "px";
                    c.style.background = p[2];
                    c.style.boxShadow = "0 0 6px " + p[2];
                }
            });
        },
        velikonoce: function () {
            var zajic = prvek("staticky zajic-st", SVG_ZAJIC);
            zajic.style.right = "40px";
            zajic.style.width = (uzke() ? 44 : 60) + "px";
            for (var i = 0; i < 4; i++) {
                var v = prvek("staticky vajicko-st", svgVajicko(BARVY_VAJEC[i]));
                v.style.right = (120 + i * 22) + "px";
            }
        }
    };

    function pust() {
        vycisti();
        vrstva.style.setProperty("--w", (vrstva.clientWidth + 90) + "px");
        vrstva.style.setProperty("--h", vrstva.clientHeight + "px");
        var sada = bezPohybu() ? klidne : animace;
        if (sada[svatek]) { sada[svatek](); }
    }

    // Skrytá karta: běžící animace se pozastaví, nové se nespouštějí.
    document.addEventListener("visibilitychange", function () {
        skryta = document.hidden;
        vrstva.getAnimations({ subtree: true }).forEach(function (a) {
            if (skryta) { a.pause(); } else { a.play(); }
        });
    });

    // Změna velikosti okna. Mobilní prohlížeč posílá resize při každém
    // skrytí adresního řádku (mění se jen výška) - kvůli tomu se efekt
    // nepřestavuje, jen se přepočítá výška pro padání. Znovu rozestavit
    // (s prodlevou, ne při každém pixelu) jen při změně ŠÍŘKY.
    var sirka = window.innerWidth;
    var prodleva = null;
    window.addEventListener("resize", function () {
        clearTimeout(prodleva);
        prodleva = setTimeout(function () {
            if (window.innerWidth !== sirka) {
                sirka = window.innerWidth;
                pust();
            } else {
                vrstva.style.setProperty("--h", vrstva.clientHeight + "px");
            }
        }, 200);
    });
    if (systemKlid && systemKlid.addEventListener) { systemKlid.addEventListener("change", pust); }


    /* -----------------------------------------------------------------------
       Hry po kliknutí na ozdobu za názvem (_udalost_hra.html).

       Kód hry je static/udalosti/hry/<svátek>.js, stahuje se se stránkou
       (jen hra dnešního svátku) a zaregistruje se přes
       JellyscopeHry.registruj(klíč, fn).
       Hra dostane jeviště uprostřed scény (dialog přes celou obrazovku)
       a `api` s texty v jazyce účtu a pomůckami. Časovače a snímky přes
       api.po / api.snimek se po zavření scény samy zruší - zavřená scéna
       už nic nekreslí.
       ----------------------------------------------------------------------- */
    var NS = "http://www.w3.org/2000/svg";
    var BARVY_KONFET = ["#ff5252", "#ffd34d", "#4fc3f7", "#7ee08a", "#ff8fd1", "#ffffff"];
    var hry = {};
    var nacita = {};
    var scena = null, tlacitko = null, uklid = [];
    var zamek = null;   // styly <html> a <body> před zamčením rolování
    // Stejná verze jako udalosti.js (?v=...), ať se po vydání stáhne nová hra.
    var verze = document.currentScript ? document.currentScript.src.replace(/^[^?]*/, "") : "";

    var texty = {};
    try {
        var zdroj = document.getElementById("udalost-texty");
        texty = zdroj ? JSON.parse(zdroj.textContent) : {};
    } catch (chyba) { texty = {}; }

    // Nejdřív se scéna odpojí (api.po/snimek už nic nespustí), pak úklid.
    // getAnimations starší prohlížeče nemají - zavřít se musí i tam.
    function zavri() {
        if (!scena) { return; }
        var stara = scena, ukoly = uklid;
        scena = null;
        uklid = [];
        ukoly.forEach(function (fn) { fn(); });
        if (stara.getAnimations) { stara.getAnimations({ subtree: true }).forEach(function (a) { a.cancel(); }); }
        stara.remove();
        odemkniRolovani();
        if (tlacitko && document.contains(tlacitko)) { tlacitko.focus(); }
        tlacitko = null;
    }

    function konfety(jeviste, x, y, kolik) {
        if (bezPohybu()) { return; }
        for (var i = 0; i < kolik; i++) {
            var k = document.createElement("span");
            k.className = "udalost-konfeta";
            k.style.left = x + "px";
            k.style.top = y + "px";
            k.style.background = BARVY_KONFET[i % BARVY_KONFET.length];
            jeviste.appendChild(k);
            var uhel = -Math.PI / 2 + (Math.random() - 0.5) * 2.2, rychlost = nahodne(120, 280);
            var dx = Math.cos(uhel) * rychlost, dy = Math.sin(uhel) * rychlost;
            k.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                       { transform: "translate(" + dx + "px," + (dy + 40) + "px) rotate(" + nahodne(0, 540) + "deg)", opacity: 1, offset: 0.6 },
                       { transform: "translate(" + (dx * 1.2) + "px," + (dy + 220) + "px) rotate(" + nahodne(0, 900) + "deg)", opacity: 0 }],
                      { duration: nahodne(1800, 2400), easing: "cubic-bezier(.2,.7,.4,1)", fill: "forwards" })
                .onfinish = (function (el) { return function () { el.remove(); }; })(k);
        }
    }

    function svg(tag, atributy, rodic) {
        var el = document.createElementNS(NS, tag);
        Object.keys(atributy || {}).forEach(function (k) { el.setAttribute(k, atributy[k]); });
        if (rodic) { rodic.appendChild(el); }
        return el;
    }
    // Bod ukazatele (myš, prst) v souřadnicích SVG plátna.
    function bodVSvg(platno, e) {
        var p = platno.createSVGPoint();
        p.x = e.clientX;
        p.y = e.clientY;
        return p.matrixTransform(platno.getScreenCTM().inverse());
    }

    // Stránka pod otevřenou scénou neroluje (kolečko ani prst mimo plátno).
    // Místo zmizelého posuvníku přibude okraj, ať obsah neposkočí.
    function zamkniRolovani() {
        var html = document.documentElement, body = document.body;
        var posuvnik = window.innerWidth - html.clientWidth;
        zamek = { overflow: html.style.overflow, padding: body.style.paddingRight };
        html.style.overflow = "hidden";
        if (posuvnik > 0) { body.style.paddingRight = posuvnik + "px"; }
    }
    function odemkniRolovani() {
        if (!zamek) { return; }
        document.documentElement.style.overflow = zamek.overflow;
        document.body.style.paddingRight = zamek.padding;
        zamek = null;
    }

    // Prvky, na které se ve scéně dá dostat Tabem.
    function fokusovatelne() {
        return Array.prototype.filter.call(
            scena.querySelectorAll('button, [href], input, [tabindex]:not([tabindex="-1"])'),
            function (el) { return !el.disabled && el.getClientRects().length > 0; });
    }

    function otevri(klic, odkud) {
        zavri();
        tlacitko = odkud;
        scena = document.createElement("div");
        scena.className = "udalost-scena";
        scena.setAttribute("role", "dialog");
        scena.setAttribute("aria-modal", "true");
        scena.setAttribute("aria-label", texty.ozdoba || "");
        scena.innerHTML = '<div class="udalost-scena-pozadi"></div><div class="udalost-jeviste">' +
            '<button class="udalost-zavrit" type="button">×</button></div>';
        var zavrit = scena.querySelector(".udalost-zavrit");
        zavrit.setAttribute("aria-label", texty.zavrit || "×");
        zavrit.addEventListener("click", zavri);
        // Druhý klik dvojkliku na ozdobu dopadne na pozadí - ten scénu nezavře.
        var otevrenoV = Date.now();
        scena.querySelector(".udalost-scena-pozadi").addEventListener("click", function () {
            if (Date.now() - otevrenoV > 400) { zavri(); }
        });
        // Jeviště jde zaměřit skriptem (ne Tabem): hra na něj přesune fokus,
        // když její část přestane být ovladatelná - Enter navíc pak nic nezavře.
        var jeviste = scena.querySelector(".udalost-jeviste");
        jeviste.setAttribute("tabindex", "-1");
        zamkniRolovani();
        document.body.appendChild(scena);

        var tahle = scena, odpocty = [], snimky = [];
        uklid.push(function () {
            odpocty.forEach(clearTimeout);
            snimky.forEach(cancelAnimationFrame);
        });
        var api = {
            texty: texty,
            klid: bezPohybu,
            konfety: konfety,
            // Zavře jen tuhle scénu - opožděné volání nezavře novou.
            zavri: function () { if (scena === tahle) { zavri(); } },
            svg: svg,
            bodVSvg: bodVSvg,
            po: function (ms, fn) {
                odpocty.push(setTimeout(function () { if (scena === tahle) { fn(); } }, ms));
            },
            snimek: function (fn) {
                snimky.push(requestAnimationFrame(function (cas) { if (scena === tahle) { fn(cas); } }));
            }
        };
        try {
            hry[klic](jeviste, api);
        } catch (chyba) {
            // Spadlá hra nesmí nechat přes stránku neviditelnou vrstvu.
            zavri();
            return;
        }
        requestAnimationFrame(function () { if (scena === tahle) { tahle.classList.add("otevrena"); } });
        zavrit.focus();
    }

    // Esc zavře, Tab a Shift+Tab kolují jen po scéně.
    document.addEventListener("keydown", function (e) {
        if (!scena) { return; }
        if (e.key === "Escape") {
            e.preventDefault();
            zavri();
            return;
        }
        if (e.key !== "Tab") { return; }
        var prvky = fokusovatelne();
        if (!prvky.length) { return; }
        var prvni = prvky[0], posledni = prvky[prvky.length - 1];
        var mimo = !scena.contains(document.activeElement);
        if (e.shiftKey && (mimo || document.activeElement === prvni)) {
            e.preventDefault();
            posledni.focus();
        } else if (!e.shiftKey && (mimo || document.activeElement === posledni)) {
            e.preventDefault();
            prvni.focus();
        }
    });

    // Kód hry se stahuje se stránkou (_udalost_hlava.html). Když se tehdy
    // nenačetl (offline, 404), zkusí se znovu při kliknutí; nepovede-li se,
    // nic se neotevře a další klik to zkusí zase. Jen hra dnešního svátku
    // (klíč jde do adresy skriptu) a jen s texty - bez nich by hra psala
    // „undefined“.
    function spust(klic, odkud) {
        if (klic !== svatek || !texty.ozdoba) { return; }
        if (hry[klic]) {
            otevri(klic, odkud);
            return;
        }
        if (nacita[klic]) { return; }
        nacita[klic] = true;
        var skript = document.createElement("script");
        skript.src = "/static/udalosti/hry/" + klic + ".js" + verze;
        skript.onload = function () {
            nacita[klic] = false;
            if (hry[klic]) { otevri(klic, odkud); } else { skript.remove(); }
        };
        skript.onerror = function () {
            nacita[klic] = false;
            skript.remove();
        };
        document.head.appendChild(skript);
    }

    window.JellyscopeHry = {
        registruj: function (klic, fn) { hry[klic] = fn; }
    };
    document.querySelectorAll(".udalost-hra[data-hra]").forEach(function (el) {
        el.addEventListener("click", function () { spust(el.getAttribute("data-hra"), el); });
    });

    pust();
})();
