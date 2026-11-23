/* ---------------------------------------------------------------------------
   Seasonal game: Easter - an egg (events.js loads it after a click on the
   egg behind the name).

   Each click (or Enter) adds a crack and the egg wobbles,
   after five the top shell tips off and a chick peeks out. The chick is clipped
   by the egg and in the same group as the shell - while wobbling they move
   together and nothing sticks out.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    // The crack's edge from right to left - shared by the top and the bottom shell.
    var EDGE = "L260 150 L240 140 L218 156 L198 132 L178 156 L158 134 L138 158 L118 134 L98 156 L80 136 L60 150";
    var JUMP = 70;   // how much lower the chick sits in the egg than when it peeks out

    function eggDrawing() {
        return '<ellipse cx="150" cy="160" rx="82" ry="108" fill="#f2c94c"/>' +
            '<path d="M60 120 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14 l14-14 l14 14" stroke="#e05a8a" stroke-width="9" fill="none"/>' +
            '<path d="M60 205 H240" stroke="#3f7fd4" stroke-width="10"/>' +
            '<circle cx="110" cy="172" r="6" fill="#fff"/><circle cx="150" cy="176" r="6" fill="#fff"/><circle cx="190" cy="172" r="6" fill="#fff"/>' +
            '<ellipse cx="150" cy="160" rx="82" ry="108" fill="url(#event-eggShadow)"/>';
    }

    JellyscopeGames.register("easter", function (stage, api) {
        var canvas = api.svg("svg", { "class": "canvas egg-canvas", viewBox: "0 0 300 300" });
        canvas.innerHTML =
            '<defs>' +
            '<clipPath id="event-shellTop"><path d="M0 0 H300 V150 ' + EDGE + ' L0 150 Z"/></clipPath>' +
            '<clipPath id="event-shellBottom"><path d="M0 300 H300 V150 ' + EDGE + ' L0 150 Z"/></clipPath>' +
            '<clipPath id="event-eggShape"><ellipse cx="150" cy="160" rx="82" ry="108"/></clipPath>' +
            '<radialGradient id="event-eggShadow" cx=".36" cy=".3" r=".85"><stop offset="0" stop-color="#fff" stop-opacity=".45"/>' +
            '<stop offset=".45" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#5a3200" stop-opacity=".35"/></radialGradient>' +
            '<radialGradient id="event-chickBody" cx=".4" cy=".35" r=".8"><stop offset="0" stop-color="#fff38a"/><stop offset="1" stop-color="#f0b90b"/></radialGradient>' +
            '</defs>' +
            '<g class="eggs" tabindex="0" role="button">' +
            '<g clip-path="url(#event-eggShape)"><g class="chick" transform="translate(0 ' + JUMP + ')">' +
            '<ellipse cx="150" cy="168" rx="58" ry="52" fill="url(#event-chickBody)"/>' +
            '<circle cx="150" cy="118" r="38" fill="url(#event-chickBody)"/>' +
            '<path d="M146 96 Q150 80 156 92 Q160 82 162 98" stroke="#f2b705" stroke-width="4" fill="none" stroke-linecap="round"/>' +
            '<circle cx="137" cy="115" r="5" fill="#222"/><circle cx="163" cy="115" r="5" fill="#222"/>' +
            '<circle cx="138.5" cy="113.5" r="1.6" fill="#fff"/><circle cx="164.5" cy="113.5" r="1.6" fill="#fff"/>' +
            '<path d="M142 126 L150 138 L158 126 Z" fill="#ff8a1f"/>' +
            '<ellipse cx="96" cy="160" rx="14" ry="24" transform="rotate(25 96 160)" fill="#f7c925"/>' +
            '<ellipse cx="204" cy="160" rx="14" ry="24" transform="rotate(-25 204 160)" fill="#f7c925"/>' +
            '</g></g>' +
            '<g class="shell-bottom" clip-path="url(#event-shellBottom)"><g clip-path="url(#event-eggShape)">' + eggDrawing() + '</g></g>' +
            '<g class="shell-top" clip-path="url(#event-shellTop)"><g clip-path="url(#event-eggShape)">' + eggDrawing() + '</g></g>' +
            // The whole egg over both halves until it cracks - otherwise a thin zigzag seam
            // would show through between the two clips.
            '<g class="egg-whole" clip-path="url(#event-eggShape)">' + eggDrawing() + '</g>' +
            '<g class="cracks">' +
            '<path d="M150 60 L144 78 L154 92 L146 108"/>' +
            '<path d="M86 130 L102 138 L96 152 L112 160"/>' +
            '<path d="M214 128 L198 140 L206 152 L190 162"/>' +
            '<path d="M120 196 L132 184 L126 172 L140 166"/>' +
            '<path d="M60 150 L80 136 L98 156 L118 134 L138 158 L158 134 L178 156 L198 132 L218 156 L240 140"/>' +
            '</g></g>';
        stage.appendChild(canvas);
        var hint = document.createElement("p");
        hint.className = "event-hint";
        hint.textContent = api.texts.hint;
        stage.appendChild(hint);
        var egg = canvas.querySelector(".eggs");
        egg.setAttribute("aria-label", api.texts.egg);
        // The place for the wish is here from the start, empty (a screen reader then announces it).
        var wish = document.createElement("div");
        wish.className = "event-wish";
        wish.setAttribute("role", "status");
        wish.style.top = "86%";
        stage.appendChild(wish);
        var cracks = canvas.querySelectorAll(".cracks path");
        var clicks = 0;

        function tap() {
            if (clicks >= cracks.length) { return; }
            cracks[clicks].classList.add("shown");
            clicks++;
            egg.style.transformBox = "fill-box";
            egg.style.transformOrigin = "50% 100%";
            if (!api.quiet()) {
                egg.animate([{ transform: "rotate(0)" }, { transform: "rotate(-7deg)" }, { transform: "rotate(6deg)" },
                               { transform: "rotate(-3deg)" }, { transform: "rotate(0)" }], { duration: 420 });
            }
            if (clicks === cracks.length) {
                egg.removeAttribute("tabindex");
                egg.removeAttribute("role");
                egg.removeAttribute("aria-label");
                if (document.activeElement === egg) { stage.focus(); }
                api.after(api.quiet() ? 0 : 450, hatch);
            }
        }
        egg.addEventListener("click", tap);
        egg.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                tap();
            }
        });

        function hatch() {
            canvas.querySelector(".cracks").style.display = "none";
            canvas.querySelector(".egg-whole").style.display = "none";
            var top = canvas.querySelector(".shell-top"), chick = canvas.querySelector(".chick");
            hint.textContent = "";
            var pip = document.createElement("div");
            pip.className = "pip";
            pip.textContent = api.texts.pip;
            pip.style.left = "62%";
            pip.style.top = "14%";
            stage.appendChild(pip);
            if (api.quiet()) {
                top.style.display = "none";
                chick.setAttribute("transform", "translate(0 0)");
                pip.style.opacity = "1";
                wish.textContent = api.texts.wish;
                return;
            }
            top.style.transformBox = "fill-box";
            top.style.transformOrigin = "30% 100%";
            top.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                          { transform: "translate(-40px,-120px) rotate(-55deg)", opacity: 0 }],
                         { duration: 700, easing: "cubic-bezier(.3,.6,.4,1)", fill: "forwards" });
            // The chick peeks out: slowing down as it emerges.
            var start = performance.now();
            function emerge(t) {
                var k = Math.min(1, (t - start) / 700), y = JUMP * Math.pow(1 - k, 3);
                chick.setAttribute("transform", "translate(0 " + y.toFixed(1) + ")");
                if (k < 1) { api.frame(emerge); }
            }
            api.frame(emerge);
            pip.animate([{ opacity: 0, transform: "scale(.4)" }, { opacity: 1, transform: "scale(1.15)", offset: 0.5 },
                         { opacity: 1, transform: "scale(1)" }], { duration: 500, delay: 650, fill: "both" });
            wish.animate([{ opacity: 0, transform: "translateY(12px)" }, { opacity: 1, transform: "translateY(0)" }],
                          { duration: 600, delay: 900, fill: "both" });
            api.after(900, function () { wish.textContent = api.texts.wish; });
            api.after(650, function () {
                var r = canvas.getBoundingClientRect(), j = stage.getBoundingClientRect();
                api.confetti(stage, r.left - j.left + r.width / 2, r.top - j.top + r.height * 0.4, 40);
            });
        }
    });
})();
