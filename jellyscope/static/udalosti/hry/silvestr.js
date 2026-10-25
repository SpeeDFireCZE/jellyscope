/* ---------------------------------------------------------------------------
   Sezónní hra: Silvestr - rachejtle (načte udalosti.js po kliknutí na
   šampaňské).

   Zapalovač se přetáhne (nebo Enterem přiloží) k roznětce, ta dohoří
   od konce k raketě, scéna se zavře a přes celou stránku vyletí velká
   rachejtle: výbuch, z jisker se složí slavený rok (podle data serveru)
   a přání. Finále běží jen na Web Animations - žádné časovače, vrstva
   zmizí s poslední animací.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    // Konec roznětky a špička plamene zapalovače (vůči jeho počátku).
    var KONEC_ROZBUSKY = [214, 216], SPICKA_PLAMENE = [4, -48];
    var BARVY = ["#ffd166", "#ff6b6b", "#7aa2ff", "#6ee7a8", "#ff8fd1", "#ffffff"];
    var LET = 1300;   // ms - velká rachejtle od spodku obrazovky k výbuchu

    // Body (po 6 px), ze kterých se skládá text - podle písma na plátně.
    function bodyTextu(text, sirka, vyska) {
        var c = document.createElement("canvas");
        c.width = sirka;
        c.height = vyska;
        var ctx = c.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.font = "800 " + Math.round(vyska * 0.82) + "px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(text, sirka / 2, vyska / 2);
        var data = ctx.getImageData(0, 0, sirka, vyska).data, body = [];
        for (var y = 0; y < vyska; y += 6) {
            for (var x = 0; x < sirka; x += 6) {
                if (data[(y * sirka + x) * 4 + 3] > 128) { body.push([x - sirka / 2, y - vyska / 2]); }
            }
        }
        return body;
    }
    function zpomaluje(t) { return 1 - Math.pow(1 - t, 3); }
    function jiskra(vrstva, x, y, barva) {
        var j = document.createElement("span");
        j.className = "jiskra-f";
        j.style.left = x + "px";
        j.style.top = y + "px";
        j.style.background = barva;
        vrstva.appendChild(j);
        return j;
    }

    function finale(api) {
        var vrstva = document.createElement("div");
        vrstva.className = "udalost-finale";
        document.body.appendChild(vrstva);
        var w = vrstva.clientWidth, h = vrstva.clientHeight;
        var cx = w / 2, cy = h * 0.36;
        var sirkaRoku = Math.min(360, w - 40), vyskaRoku = Math.round(sirkaRoku * 130 / 360);
        var text = document.createElement("div");
        text.className = "rok-text";
        text.setAttribute("role", "status");
        text.style.top = (cy + vyskaRoku * 0.62) + "px";
        // Místo pro přání je ve vrstvě od začátku a prázdné - text přijde
        // s výbuchem a čtečka obrazovky ho tak ohlásí.
        vrstva.appendChild(text);

        // Bez pohybu: rok a přání rovnou stojí, chvíli zůstanou a zmizí.
        if (api.klid()) {
            bodyTextu(api.texty.rok, sirkaRoku, vyskaRoku).forEach(function (bod) {
                var j = jiskra(vrstva, cx + bod[0], cy + bod[1], "#ffd97a");
                j.style.boxShadow = "0 0 9px #ffb52e";
            });
            text.style.opacity = "1";
            // Text až o chvilku později (ne hned s vložením), ať se ohlásí.
            text.animate([{ opacity: 1 }, { opacity: 1 }], { duration: 60 })
                .onfinish = function () { text.textContent = api.texty.prani; };
            vrstva.animate([{ opacity: 1 }, { opacity: 1, offset: 0.92 }, { opacity: 0 }], { duration: 5000 })
                .onfinish = function () { vrstva.remove(); };
            return;
        }

        function vybuchni() {
            for (var i = 0; i < 110; i++) {
                var u = Math.random() * Math.PI * 2, r = 60 + Math.random() * Math.min(w, h) * 0.42;
                var b = BARVY[i % BARVY.length];
                var j = jiskra(vrstva, cx, cy, b);
                j.style.boxShadow = "0 0 8px " + b;
                j.animate([{ transform: "translate(0,0)", opacity: 1 },
                           { transform: "translate(" + (Math.cos(u) * r) + "px," + (Math.sin(u) * r + r * 0.35) + "px)", opacity: 0 }],
                          { duration: 1600 + Math.random() * 700, easing: "cubic-bezier(.1,.7,.3,1)", fill: "forwards" })
                    .onfinish = (function (el) { return function () { el.remove(); }; })(j);
            }
            bodyTextu(api.texty.rok, sirkaRoku, vyskaRoku).forEach(function (bod, i) {
                var j = jiskra(vrstva, cx, cy, "#ffd97a");
                j.style.boxShadow = "0 0 9px #ffb52e";
                j.animate([{ transform: "translate(0,0)", opacity: 0 },
                           { transform: "translate(" + bod[0] + "px," + bod[1] + "px)", opacity: 1, offset: 0.35 },
                           { transform: "translate(" + bod[0] + "px," + bod[1] + "px)", opacity: 1, offset: 0.85 },
                           { transform: "translate(" + bod[0] + "px," + (bod[1] + 40) + "px)", opacity: 0 }],
                          { duration: 4800, delay: 150 + (i % 7) * 20, easing: "ease-out", fill: "both" });
            });
            text.textContent = api.texty.prani;
            // Přání doběhne poslední - s ním zmizí celá vrstva.
            text.animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "translateY(0)", offset: 0.25 },
                          { opacity: 1, offset: 0.85 }, { opacity: 0 }], { duration: 4800, delay: 600, fill: "both" })
                .onfinish = function () { vrstva.remove(); };
        }

        // Let rakety: zpomaluje ke špičce dráhy. Stopa jisker se spočítá
        // předem (každých 40 ms místo, kde raketa v tu chvíli je).
        var raketa = document.createElement("span");
        raketa.className = "raketa-velka";
        raketa.style.left = cx + "px";
        raketa.style.top = h + "px";
        vrstva.appendChild(raketa);
        var drahy = [];
        for (var k = 0; k <= 12; k++) { drahy.push({ transform: "translateY(" + ((cy - h) * zpomaluje(k / 12)) + "px)" }); }
        raketa.animate(drahy, { duration: LET, fill: "forwards" }).onfinish = function () {
            raketa.remove();
            vybuchni();
        };
        for (var t = 0; t < LET - 80; t += 40) {
            var stopa = jiskra(vrstva, cx, h + (cy - h) * zpomaluje(t / LET) + 34, "#ffd166");
            stopa.style.opacity = "0";
            stopa.animate([{ opacity: 1, transform: "translate(0,0)" },
                           { opacity: 0, transform: "translate(" + ((Math.random() - 0.5) * 16) + "px,24px)" }],
                          { duration: 500, delay: t, fill: "forwards" })
                .onfinish = (function (el) { return function () { el.remove(); }; })(stopa);
        }
    }

    JellyscopeHry.registruj("silvestr", function (jeviste, api) {
        var platno = api.svg("svg", { "class": "platno", viewBox: "0 0 300 330" });
        platno.innerHTML =
            '<defs>' +
            '<linearGradient id="udalost-rTelo" x1="0" x2="1"><stop offset="0" stop-color="#7a1616"/><stop offset=".32" stop-color="#e2453c"/>' +
            '<stop offset=".48" stop-color="#ff9a8c"/><stop offset=".62" stop-color="#d63a3a"/><stop offset="1" stop-color="#6e1414"/></linearGradient>' +
            '<linearGradient id="udalost-rPruh" x1="0" x2="1"><stop offset="0" stop-color="#8a6400"/><stop offset=".45" stop-color="#ffe9a0"/>' +
            '<stop offset=".55" stop-color="#ffd34d"/><stop offset="1" stop-color="#7a5800"/></linearGradient>' +
            '<linearGradient id="udalost-rKuzel" x1="0" x2="1"><stop offset="0" stop-color="#142f7a"/><stop offset=".42" stop-color="#5b8cff"/>' +
            '<stop offset=".55" stop-color="#3a66d8"/><stop offset="1" stop-color="#112565"/></linearGradient>' +
            '<linearGradient id="udalost-rTyc" x1="0" x2="1"><stop offset="0" stop-color="#5a4020"/><stop offset=".5" stop-color="#c49a62"/><stop offset="1" stop-color="#4a3418"/></linearGradient>' +
            '<linearGradient id="udalost-zTelo" x1="0" x2="1"><stop offset="0" stop-color="#123e73"/><stop offset=".35" stop-color="#3f8fe0"/>' +
            '<stop offset=".5" stop-color="#a9d6ff"/><stop offset=".62" stop-color="#3b86d4"/><stop offset="1" stop-color="#0f3561"/></linearGradient>' +
            '<linearGradient id="udalost-zKov" x1="0" x2="1"><stop offset="0" stop-color="#5f666f"/><stop offset=".4" stop-color="#dfe4ea"/>' +
            '<stop offset=".5" stop-color="#ffffff"/><stop offset=".62" stop-color="#b3bac4"/><stop offset="1" stop-color="#555b63"/></linearGradient>' +
            '<radialGradient id="udalost-zZare" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffe27a" stop-opacity=".9"/>' +
            '<stop offset="1" stop-color="#ff9a1f" stop-opacity="0"/></radialGradient>' +
            '</defs>' +
            '<ellipse cx="150" cy="316" rx="40" ry="6" fill="#000" opacity=".35"/>' +
            // tyč, tělo (válec), proužky, kužel
            '<rect x="147" y="180" width="6" height="134" rx="2" fill="url(#udalost-rTyc)"/>' +
            '<rect x="134" y="118" width="32" height="64" fill="url(#udalost-rTelo)"/>' +
            '<ellipse cx="150" cy="182" rx="16" ry="4.5" fill="#5e1111"/>' +
            '<rect x="134" y="131" width="32" height="6" fill="url(#udalost-rPruh)"/>' +
            '<rect x="134" y="147" width="32" height="6" fill="url(#udalost-rPruh)"/>' +
            '<rect x="134" y="163" width="32" height="6" fill="url(#udalost-rPruh)"/>' +
            '<path d="M132 120 L150 82 L168 120 Q150 127 132 120 Z" fill="url(#udalost-rKuzel)"/>' +
            '<ellipse cx="150" cy="120" rx="18" ry="4.5" fill="none" stroke="#0d1f52" stroke-width="1.2" opacity=".6"/>' +
            // roznětka od spodku rakety ke konci
            '<path class="rozbuska" d="M150 182 C150 200 172 202 180 196 C190 189 204 206 214 216" fill="none" stroke="#6b4a2b" stroke-width="3.5" stroke-linecap="round"/>' +
            '<circle class="cil-rozbusky" cx="214" cy="216" r="7" fill="url(#udalost-zZare)"/>' +
            '<circle class="konec-rozbusky" cx="214" cy="216" r="3" fill="#a07b4f"/>' +
            '<g class="jiskra-rozbusky" opacity="0"><circle r="8" fill="#ffd166" opacity=".45"/><circle r="3.4" fill="#fff4c2"/></g>' +
            // zapalovač: lesklé tělo, kovová hlava s kolečkem, plamen se svatozáří
            '<g class="zapalovac" transform="translate(56 236)" tabindex="0" role="button">' +
            '<circle class="vyzva" cx="0" cy="8" r="40" fill="none" stroke="#ffd166" stroke-width="2.5"/>' +
            '<rect x="-18" y="0" width="36" height="58" rx="8" fill="url(#udalost-zTelo)"/>' +
            '<rect x="-12" y="6" width="5" height="44" rx="2.5" fill="#fff" opacity=".35"/>' +
            '<rect x="-18" y="-14" width="36" height="16" rx="3" fill="url(#udalost-zKov)"/>' +
            '<circle cx="-9" cy="-17" r="6" fill="url(#udalost-zKov)" stroke="#4a4f56" stroke-width="1.2"/>' +
            '<path d="M-14 -17 h10 M-9 -22 v10" stroke="#4a4f56" stroke-width="1"/>' +
            '<circle class="svatozar" cx="4" cy="-33" r="17" fill="url(#udalost-zZare)"/>' +
            '<path class="plaminek" d="M4 -48 C15 -36 13 -24 4 -19 C-5 -24 -7 -36 4 -48 Z" fill="#ffad1f"/>' +
            '<path d="M4 -38 C9 -31 8 -25 4 -23 C0 -25 -1 -31 4 -38 Z" fill="#fff4b0"/>' +
            '</g>';
        jeviste.appendChild(platno);
        var napoveda = document.createElement("p");
        napoveda.className = "udalost-napoveda";
        napoveda.textContent = api.texty.napoveda;
        jeviste.appendChild(napoveda);
        var zapalovac = platno.querySelector(".zapalovac");
        zapalovac.setAttribute("aria-label", api.texty.zapalovac);
        var pozice = { x: 56, y: 236 }, posun = null, hori = false;
        // Zapalovač jde nosit po celém okně, jen z něj neuteče: meze jsou
        // okraje okna převedené do souřadnic plátna, zmenšené o tělo
        // zapalovače (vodorovně +-18, svisle od plamene -48 po spodek 58).
        function meze() {
            var m = platno.getScreenCTM().inverse();
            var a = platno.createSVGPoint(), b = platno.createSVGPoint();
            b.x = window.innerWidth;
            b.y = window.innerHeight;
            a = a.matrixTransform(m);
            b = b.matrixTransform(m);
            return { x1: a.x + 20, x2: b.x - 20, y1: a.y + 50, y2: b.y - 60 };
        }
        function presun(x, y) {
            var okno = meze();
            pozice = { x: Math.min(okno.x2, Math.max(okno.x1, x)), y: Math.min(okno.y2, Math.max(okno.y1, y)) };
            zapalovac.setAttribute("transform", "translate(" + pozice.x + " " + pozice.y + ")");
        }
        zapalovac.addEventListener("pointerdown", function (e) {
            if (hori || e.button !== 0 || !e.isPrimary) { return; }
            zapalovac.setPointerCapture(e.pointerId);
            zapalovac.classList.add("drzi");
            var b = api.bodVSvg(platno, e);
            posun = { x: b.x - pozice.x, y: b.y - pozice.y };
        });
        zapalovac.addEventListener("pointermove", function (e) {
            if (!posun || hori) { return; }
            if (e.buttons === 0) {   // puštění, které nedorazilo
                posun = null;
                return;
            }
            var b = api.bodVSvg(platno, e);
            presun(b.x - posun.x, b.y - posun.y);
            if (Math.hypot(pozice.x + SPICKA_PLAMENE[0] - KONEC_ROZBUSKY[0],
                           pozice.y + SPICKA_PLAMENE[1] - KONEC_ROZBUSKY[1]) < 20) { zapal(); }
        });
        zapalovac.addEventListener("pointerup", function () { posun = null; });
        zapalovac.addEventListener("pointercancel", function () { posun = null; });
        // Klávesnice: Enter přiloží zapalovač rovnou k roznětce.
        zapalovac.addEventListener("keydown", function (e) {
            if ((e.key === "Enter" || e.key === " ") && !hori) {
                e.preventDefault();
                presun(KONEC_ROZBUSKY[0] - SPICKA_PLAMENE[0], KONEC_ROZBUSKY[1] - SPICKA_PLAMENE[1]);
                zapal();
            }
        });

        function zapal() {
            hori = true;
            posun = null;
            napoveda.textContent = api.texty.hori;
            zapalovac.removeAttribute("tabindex");
            if (document.activeElement === zapalovac) { jeviste.focus(); }
            zapalovac.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 400, fill: "forwards" });
            platno.querySelector(".cil-rozbusky").style.display = "none";
            var rozbuska = platno.querySelector(".rozbuska");
            var delka = rozbuska.getTotalLength();
            var plamen = platno.querySelector(".jiskra-rozbusky");
            plamen.setAttribute("opacity", "1");
            platno.querySelector(".konec-rozbusky").setAttribute("opacity", "0");
            var trvani = api.klid() ? 300 : 2600, start = performance.now();
            function krok(t) {
                var k = Math.min(1, (t - start) / trvani);
                // Zbývá úsek od rakety k jiskře; ohořelý konec mizí.
                var zbyva = delka * (1 - k);
                rozbuska.style.strokeDasharray = zbyva + " " + delka;
                var b = rozbuska.getPointAtLength(zbyva);
                plamen.setAttribute("transform", "translate(" + b.x + " " + b.y + ")");
                if (!api.klid() && Math.random() < 0.6) { odletJiskry(b.x, b.y); }
                if (k < 1) {
                    api.snimek(krok);
                } else {
                    api.po(250, function () {
                        api.zavri();
                        finale(api);
                    });
                }
            }
            api.snimek(krok);
        }
        function odletJiskry(x, y) {
            var c = api.svg("circle", { cx: x, cy: y, r: 1.3, fill: Math.random() < 0.5 ? "#ffd166" : "#fff" }, platno);
            c.animate([{ transform: "translate(0,0)", opacity: 1 },
                       { transform: "translate(" + ((Math.random() - 0.5) * 22) + "px," + (-6 - Math.random() * 16) + "px)", opacity: 0 }],
                      { duration: 450, fill: "forwards" }).onfinish = function () { c.remove(); };
        }
    });
})();
