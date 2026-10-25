/* ---------------------------------------------------------------------------
   Sezónní hra: Halloween - dýně (načte udalosti.js po kliknutí na rakev).

   Tělo dýně je z laloků; tahem myši nebo prstem se obtáhne tvar a po puštění se
   vyřízne - díra s pruhem dužiny u okraje (hloubka), vyříznutý kousek
   slupky vypadne. Tlačítkem se dýně rozsvítí (plamen uvnitř).
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    // Laloky dýně zezadu dopředu: [cx, rx, ry]. Společný střed y = 178.
    var LALOKY = [[62, 44, 80], [238, 44, 80], [90, 54, 92], [210, 54, 92], [120, 58, 98], [180, 58, 98], [150, 60, 102]];

    function cesta(body) {
        return "M" + body.map(function (p) { return p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" L");
    }

    JellyscopeHry.registruj("halloween", function (jeviste, api) {
        var platno = api.svg("svg", { "class": "platno dyne-platno", viewBox: "0 0 300 300", role: "img" });
        platno.setAttribute("aria-label", api.texty.dyne);
        var telo = LALOKY.map(function (l) {
            return '<ellipse cx="' + l[0] + '" cy="178" rx="' + l[1] + '" ry="' + l[2] + '"/>';
        }).join("");
        platno.innerHTML =
            '<defs>' +
            '<clipPath id="udalost-dyneTelo">' + telo + '</clipPath>' +
            '<radialGradient id="udalost-dLalok" cx=".42" cy=".38" r=".75"><stop offset="0" stop-color="#ffb04a"/>' +
            '<stop offset=".55" stop-color="#f07d14"/><stop offset="1" stop-color="#b44a06"/></radialGradient>' +
            '<radialGradient id="udalost-dLalokTmavy" cx=".5" cy=".4" r=".8"><stop offset="0" stop-color="#e8740f"/><stop offset="1" stop-color="#9c3e05"/></radialGradient>' +
            '<linearGradient id="udalost-dStopka" x1="0" x2="1"><stop offset="0" stop-color="#6b8f2a"/><stop offset="1" stop-color="#3d5a17"/></linearGradient>' +
            '<radialGradient id="udalost-dPlamen" cx=".5" cy=".55" r=".6"><stop offset="0" stop-color="#fff7c4"/>' +
            '<stop offset=".45" stop-color="#ffc83d"/><stop offset="1" stop-color="#ff7a00"/></radialGradient>' +
            '<radialGradient id="udalost-dVnitrni" cx=".5" cy=".55" r=".55"><stop offset="0" stop-color="#ffcf5a" stop-opacity=".5"/>' +
            '<stop offset="1" stop-color="#ff7a00" stop-opacity="0"/></radialGradient>' +
            '<filter id="udalost-dZare" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="3" result="r"/>' +
            '<feMerge><feMergeNode in="r"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' +
            '</defs>' +
            '<ellipse cx="150" cy="282" rx="118" ry="14" fill="#000" opacity=".35"/>' +
            LALOKY.map(function (l, i) {
                return '<ellipse cx="' + l[0] + '" cy="178" rx="' + l[1] + '" ry="' + l[2] + '" fill="url(#' + (i < 2 ? "udalost-dLalokTmavy" : "udalost-dLalok") + ')"/>';
            }).join("") +
            // rýhy mezi laloky
            '<path d="M93 92 Q78 178 93 266 M207 92 Q222 178 207 266 M122 82 Q112 178 122 274 M178 82 Q188 178 178 274" ' +
            'stroke="#9c3e05" stroke-width="2.5" fill="none" opacity=".55"/>' +
            '<ellipse cx="150" cy="84" rx="34" ry="9" fill="#8a3504"/>' +
            '<path d="M140 86 C138 64 146 48 166 40 L172 50 C158 56 154 68 156 86 Z" fill="url(#udalost-dStopka)"/>' +
            '<ellipse cx="108" cy="128" rx="18" ry="34" fill="#fff" opacity=".13" transform="rotate(-12 108 128)"/>' +
            '<ellipse class="dyne-svetlo" cx="150" cy="185" rx="130" ry="105" fill="url(#udalost-dVnitrni)"/>' +
            '<g class="vyrezy" clip-path="url(#udalost-dyneTelo)"></g>' +
            '<path class="stopa-noze" d="" fill="none" stroke="#3a1505" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="5 4"/>';
        jeviste.appendChild(platno);
        var napoveda = document.createElement("p");
        napoveda.className = "udalost-napoveda";
        napoveda.textContent = api.texty.napoveda;
        jeviste.appendChild(napoveda);
        var tlacitka = document.createElement("div");
        tlacitka.className = "udalost-tlacitka";
        var svitit = document.createElement("button");
        svitit.type = "button";
        svitit.className = "hlavni";
        svitit.textContent = api.texty.rozsvitit;
        var znovu = document.createElement("button");
        znovu.type = "button";
        znovu.textContent = api.texty.znovu;
        tlacitka.appendChild(svitit);
        tlacitka.appendChild(znovu);
        jeviste.appendChild(tlacitka);

        var vyrezy = platno.querySelector(".vyrezy"), stopa = platno.querySelector(".stopa-noze");
        // Kreslí jen jeden ukazatel - levé tlačítko nebo první prst. Pravé
        // tlačítko, druhý prst ani pohyb bez stisku tah nezačnou ani nepokazí.
        var body = null, ukazatel = null, citac = 0;
        function zrus() {
            body = null;
            ukazatel = null;
            stopa.setAttribute("d", "");
        }
        platno.addEventListener("pointerdown", function (e) {
            if (e.button !== 0 || !e.isPrimary) { return; }
            platno.setPointerCapture(e.pointerId);
            ukazatel = e.pointerId;
            var b = api.bodVSvg(platno, e);
            body = [[b.x, b.y]];
        });
        platno.addEventListener("pointermove", function (e) {
            if (!body || e.pointerId !== ukazatel) { return; }
            // Puštění, které nedorazilo (kontextové menu, myš mimo okno).
            if (e.buttons === 0) {
                zrus();
                return;
            }
            var b = api.bodVSvg(platno, e), posl = body[body.length - 1];
            if (Math.hypot(b.x - posl[0], b.y - posl[1]) < 2) { return; }
            body.push([b.x, b.y]);
            stopa.setAttribute("d", cesta(body));
        });
        // Puštění: z tahu se vyřízne tvar (příliš malý tah se zahodí).
        function dotazeno(e) {
            if (!body || e.pointerId !== ukazatel) { return; }
            var tvar = body;
            zrus();
            var xs = tvar.map(function (p) { return p[0]; }), ys = tvar.map(function (p) { return p[1]; });
            if (tvar.length < 4 || Math.max.apply(null, xs) - Math.min.apply(null, xs) < 6
                    || Math.max.apply(null, ys) - Math.min.apply(null, ys) < 6) { return; }
            vyrizni(tvar);
        }
        platno.addEventListener("pointerup", dotazeno);
        // Přerušený tah (gesto si vzal systém) se nevyřízne.
        platno.addEventListener("pointercancel", function (e) {
            if (e.pointerId === ukazatel) { zrus(); }
        });

        // Výřez = díra ve stěně dužiny: tvar vyplněný dužinou, do něj posunutá
        // tma oříznutá stejným tvarem - u spodního okraje zbyde pruh dužiny,
        // který vypadá jako řezná plocha (hloubka).
        function vyrizni(tvar) {
            var d = cesta(tvar) + " Z";
            var id = "udalost-dVyrez" + (++citac);
            var g = api.svg("g", { "class": "vyrez" }, vyrezy);
            var orez = api.svg("clipPath", { id: id }, api.svg("defs", {}, g));
            api.svg("path", { d: d }, orez);
            api.svg("path", { "class": "vyrez-duzina", d: d, fill: "#f6c06a" }, g);
            var dira = api.svg("g", { "clip-path": "url(#" + id + ")" }, g);
            api.svg("path", { "class": "vyrez-tma", d: d, transform: "translate(1.5 -4.5)", fill: "#240c02" }, dira);
            // vyříznutý kousek slupky vypadne
            if (!api.klid()) {
                var kus = api.svg("path", { d: d, fill: "#f07d14", stroke: "#b44a06", "stroke-width": "1" }, platno);
                kus.style.transformBox = "fill-box";
                kus.style.transformOrigin = "50% 50%";
                kus.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                             { transform: "translate(" + ((Math.random() - 0.5) * 30) + "px,140px) rotate(" + ((Math.random() - 0.5) * 120) + "deg)", opacity: 0 }],
                            { duration: 750, easing: "cubic-bezier(.4,0,.8,.6)", fill: "forwards" }).onfinish = function () { kus.remove(); };
            }
        }
        svitit.addEventListener("click", function () {
            var sviti = !platno.classList.contains("svitici-dyne");
            platno.classList.toggle("svitici-dyne", sviti);
            svitit.textContent = sviti ? api.texty.sfouknout : api.texty.rozsvitit;
        });
        znovu.addEventListener("click", function () { vyrezy.innerHTML = ""; });
    });
})();
