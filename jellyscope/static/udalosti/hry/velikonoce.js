/* ---------------------------------------------------------------------------
   Sezónní hra: Velikonoce - vajíčko (načte udalosti.js po kliknutí na
   vajíčko za názvem).

   Každé kliknutí (nebo Enter) přidá prasklinu a vajíčko se zakymácí,
   po pěti se vrchní skořápka odklopí a vykoukne kuřátko. Kuře je v ořezu
   podle vajíčka a ve stejné skupině jako skořápka - při kymácení se hýbou
   spolu a nic nevyčuhuje.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    // Hrana prasknutí zprava doleva - společná pro vrchní i spodní skořápku.
    var HRANA = "L260 150 L240 140 L218 156 L198 132 L178 156 L158 134 L138 158 L118 134 L98 156 L80 136 L60 150";
    var VYSKOK = 70;   // o kolik je kuře ve vajíčku níž, než když vykoukne

    function kresbaVejce() {
        return '<ellipse cx="150" cy="160" rx="82" ry="108" fill="#f2c94c"/>' +
            '<path d="M60 120 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14" stroke="#e05a8a" stroke-width="9" fill="none"/>' +
            '<path d="M60 205 H240" stroke="#3f7fd4" stroke-width="10"/>' +
            '<circle cx="110" cy="172" r="6" fill="#fff"/><circle cx="150" cy="176" r="6" fill="#fff"/><circle cx="190" cy="172" r="6" fill="#fff"/>' +
            '<ellipse cx="150" cy="160" rx="82" ry="108" fill="url(#udalost-vejceStin)"/>';
    }

    JellyscopeHry.registruj("velikonoce", function (jeviste, api) {
        var platno = api.svg("svg", { "class": "platno vejce-platno", viewBox: "0 0 300 300" });
        platno.innerHTML =
            '<defs>' +
            '<clipPath id="udalost-skorTop"><path d="M0 0 H300 V150 ' + HRANA + ' L0 150 Z"/></clipPath>' +
            '<clipPath id="udalost-skorSpod"><path d="M0 300 H300 V150 ' + HRANA + ' L0 150 Z"/></clipPath>' +
            '<clipPath id="udalost-vejceTvar"><ellipse cx="150" cy="160" rx="82" ry="108"/></clipPath>' +
            '<radialGradient id="udalost-vejceStin" cx=".36" cy=".3" r=".85"><stop offset="0" stop-color="#fff" stop-opacity=".45"/>' +
            '<stop offset=".45" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#5a3200" stop-opacity=".35"/></radialGradient>' +
            '<radialGradient id="udalost-kureTelo" cx=".4" cy=".35" r=".8"><stop offset="0" stop-color="#fff38a"/><stop offset="1" stop-color="#f0b90b"/></radialGradient>' +
            '</defs>' +
            '<g class="vejce" tabindex="0" role="button">' +
            '<g clip-path="url(#udalost-vejceTvar)"><g class="kure" transform="translate(0 ' + VYSKOK + ')">' +
            '<ellipse cx="150" cy="168" rx="58" ry="52" fill="url(#udalost-kureTelo)"/>' +
            '<circle cx="150" cy="118" r="38" fill="url(#udalost-kureTelo)"/>' +
            '<path d="M146 96 Q150 80 156 92 Q160 82 162 98" stroke="#f2b705" stroke-width="4" fill="none" stroke-linecap="round"/>' +
            '<circle cx="137" cy="115" r="5" fill="#222"/><circle cx="163" cy="115" r="5" fill="#222"/>' +
            '<circle cx="138.5" cy="113.5" r="1.6" fill="#fff"/><circle cx="164.5" cy="113.5" r="1.6" fill="#fff"/>' +
            '<path d="M142 126 L150 138 L158 126 Z" fill="#ff8a1f"/>' +
            '<ellipse cx="96" cy="160" rx="14" ry="24" transform="rotate(25 96 160)" fill="#f7c925"/>' +
            '<ellipse cx="204" cy="160" rx="14" ry="24" transform="rotate(-25 204 160)" fill="#f7c925"/>' +
            '</g></g>' +
            '<g class="skorapka-spod" clip-path="url(#udalost-skorSpod)"><g clip-path="url(#udalost-vejceTvar)">' + kresbaVejce() + '</g></g>' +
            '<g class="skorapka-vrch" clip-path="url(#udalost-skorTop)"><g clip-path="url(#udalost-vejceTvar)">' + kresbaVejce() + '</g></g>' +
            // Celé vajíčko přes obě poloviny, dokud nepraskne - jinak by mezi
            // dvěma ořezy prosvítal tenký klikatý šev.
            '<g class="vejce-cele" clip-path="url(#udalost-vejceTvar)">' + kresbaVejce() + '</g>' +
            '<g class="praskliny">' +
            '<path d="M150 60 L144 78 L154 92 L146 108"/>' +
            '<path d="M86 130 L102 138 L96 152 L112 160"/>' +
            '<path d="M214 128 L198 140 L206 152 L190 162"/>' +
            '<path d="M120 196 L132 184 L126 172 L140 166"/>' +
            '<path d="M60 150 L80 136 L98 156 L118 134 L138 158 L158 134 L178 156 L198 132 L218 156 L240 140"/>' +
            '</g></g>';
        jeviste.appendChild(platno);
        var napoveda = document.createElement("p");
        napoveda.className = "udalost-napoveda";
        napoveda.textContent = api.texty.napoveda;
        jeviste.appendChild(napoveda);
        var vejce = platno.querySelector(".vejce");
        vejce.setAttribute("aria-label", api.texty.vajicko);
        // Místo pro přání je tu od začátku a prázdné (čtečka ho pak ohlásí).
        var prani = document.createElement("div");
        prani.className = "udalost-prani";
        prani.setAttribute("role", "status");
        prani.style.top = "86%";
        jeviste.appendChild(prani);
        var praskliny = platno.querySelectorAll(".praskliny path");
        var kliku = 0;

        function klepni() {
            if (kliku >= praskliny.length) { return; }
            praskliny[kliku].classList.add("videt");
            kliku++;
            vejce.style.transformBox = "fill-box";
            vejce.style.transformOrigin = "50% 100%";
            if (!api.klid()) {
                vejce.animate([{ transform: "rotate(0)" }, { transform: "rotate(-7deg)" }, { transform: "rotate(6deg)" },
                               { transform: "rotate(-3deg)" }, { transform: "rotate(0)" }], { duration: 420 });
            }
            if (kliku === praskliny.length) {
                vejce.removeAttribute("tabindex");
                vejce.removeAttribute("role");
                vejce.removeAttribute("aria-label");
                if (document.activeElement === vejce) { jeviste.focus(); }
                api.po(api.klid() ? 0 : 450, vyklubni);
            }
        }
        vejce.addEventListener("click", klepni);
        vejce.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                klepni();
            }
        });

        function vyklubni() {
            platno.querySelector(".praskliny").style.display = "none";
            platno.querySelector(".vejce-cele").style.display = "none";
            var vrch = platno.querySelector(".skorapka-vrch"), kure = platno.querySelector(".kure");
            napoveda.textContent = "";
            var pip = document.createElement("div");
            pip.className = "pip";
            pip.textContent = api.texty.pip;
            pip.style.left = "62%";
            pip.style.top = "14%";
            jeviste.appendChild(pip);
            if (api.klid()) {
                vrch.style.display = "none";
                kure.setAttribute("transform", "translate(0 0)");
                pip.style.opacity = "1";
                prani.textContent = api.texty.prani;
                return;
            }
            vrch.style.transformBox = "fill-box";
            vrch.style.transformOrigin = "30% 100%";
            vrch.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                          { transform: "translate(-40px,-120px) rotate(-55deg)", opacity: 0 }],
                         { duration: 700, easing: "cubic-bezier(.3,.6,.4,1)", fill: "forwards" });
            // Kuře vykoukne: zpomaluje, jak se vynoří.
            var start = performance.now();
            function vystup(t) {
                var k = Math.min(1, (t - start) / 700), y = VYSKOK * Math.pow(1 - k, 3);
                kure.setAttribute("transform", "translate(0 " + y.toFixed(1) + ")");
                if (k < 1) { api.snimek(vystup); }
            }
            api.snimek(vystup);
            pip.animate([{ opacity: 0, transform: "scale(.4)" }, { opacity: 1, transform: "scale(1.15)", offset: 0.5 },
                         { opacity: 1, transform: "scale(1)" }], { duration: 500, delay: 650, fill: "both" });
            prani.animate([{ opacity: 0, transform: "translateY(12px)" }, { opacity: 1, transform: "translateY(0)" }],
                          { duration: 600, delay: 900, fill: "both" });
            api.po(900, function () { prani.textContent = api.texty.prani; });
            api.po(650, function () {
                var r = platno.getBoundingClientRect(), j = jeviste.getBoundingClientRect();
                api.konfety(jeviste, r.left - j.left + r.width / 2, r.top - j.top + r.height * 0.4, 40);
            });
        }
    });
})();
