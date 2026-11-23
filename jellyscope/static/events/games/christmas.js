/* ---------------------------------------------------------------------------
   Seasonal game: Christmas - a gift (events.js loads it after a click on the tree).

   An isometric box with a ribbon. After a click the bow unties (the loops
   slip through the knot into the ends), the loose ends flip over the lid's edge
   and the bands fall out onto the ground - under the gift they stay stuck by its weight.
   Then the lid opens, light comes out, confetti and a wish. Without motion the gift
   shows unwrapped right away.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    function pointsOn(a) { return a.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" "); }
    function between(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]; }
    function shift(p, dy) { return [p[0], p[1] + dy]; }
    function enlarge(p, center, k) { return [center[0] + (p[0] - center[0]) * k, center[1] + (p[1] - center[1]) * k]; }
    function farther(a, b) { return Math.hypot(b[0] - a[0], b[1] - a[1]); }
    function polygon(vertices, fillColour) { return '<polygon points="' + pointsOn(vertices) + '" fill="' + fillColour + '"/>'; }
    function smoothly(t) { return t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t); }
    function segment(t, lo, hi) { return smoothly((t - lo) / (hi - lo)); }

    JellyscopeGames.register("christmas", function (stage, api) {
        var canvas = api.svg("svg", { "class": "canvas", viewBox: "0 0 240 260" });
        // The box: the centre of the bottom, wall height H, lid height V,
        // half the ribbon width W (a share of the edge).
        var center = [120, 140], H = 66, V = 16, W = 0.09;
        var Z = [120, 104], P = [192, 140], F = [120, 176], L = [48, 140];
        var vZ = enlarge(Z, center, 1.08), vP = enlarge(P, center, 1.08), vF = enlarge(F, center, 1.08), vL = enlarge(L, center, 1.08);
        var tZ = shift(vZ, -V), tP = shift(vP, -V), tF = shift(vF, -V), tL = shift(vL, -V);
        var top = [120, center[1] - V];
        var knot = [top[0], top[1] - 4];
        function band(a, b, h) {
            var p1 = between(a, b, 0.5 - W), p2 = between(a, b, 0.5 + W);
            return [p1, p2, shift(p2, h), shift(p1, h)];
        }
        function lidBand(a1, b1, a2, b2) {
            return [between(a1, b1, 0.5 - W), between(a1, b1, 0.5 + W), between(a2, b2, 0.5 + W), between(a2, b2, 0.5 - W)];
        }
        // A loop of the bow of size s (1 = whole, 0 = slipped through the knot),
        // direction -1 left / 1 right.
        function loop(s, direction) {
            var k = knot;
            var x = function (dx) { return (k[0] + dx * direction * s).toFixed(1); };
            var y = function (dy) { return (k[1] + dy * s).toFixed(1); };
            return "M" + k[0] + " " + k[1] + " C" + x(6) + " " + y(-22) + " " + x(44) + " " + y(-26) + " " + x(42) + " " + y(-8) +
                   " C" + x(40) + " " + y(8) + " " + x(12) + " " + y(4) + " " + k[0] + " " + k[1];
        }
        // An end of the bow: the smaller the loop, the longer the end
        // (the ribbon was pulled from the loop into it).
        function end(g, direction) {
            return "M" + knot[0] + " " + knot[1] + " Q" + (knot[0] + 10 * direction) + " " + (knot[1] + 18) + " " +
                   (knot[0] + (20 + g * 0.7) * direction).toFixed(1) + " " + (knot[1] + 24 + g * 0.45).toFixed(1);
        }
        canvas.innerHTML =
            '<defs>' +
            '<linearGradient id="event-dWallL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d9493a"/><stop offset="1" stop-color="#ad3226"/></linearGradient>' +
            '<linearGradient id="event-dWallR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#a62c21"/><stop offset="1" stop-color="#7e1f17"/></linearGradient>' +
            '<linearGradient id="event-dTop" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f4675a"/><stop offset="1" stop-color="#d23f30"/></linearGradient>' +
            '<radialGradient id="event-dLight" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff6c8"/><stop offset=".5" stop-color="#ffd25a" stop-opacity=".7"/><stop offset="1" stop-color="#ffb000" stop-opacity="0"/></radialGradient>' +
            '<linearGradient id="event-dBow" x1="0" x2="1"><stop offset="0" stop-color="#e0a812"/><stop offset=".5" stop-color="#ffe98a"/><stop offset="1" stop-color="#e0a812"/></linearGradient>' +
            '</defs>' +
            '<ellipse cx="120" cy="' + (F[1] + H + 2) + '" rx="96" ry="20" fill="#000" opacity=".35"/>' +
            '<g class="gift-whole" tabindex="0" role="button">' +
            // the ribbon behind the box (back bands that end on the ground behind it)
            '<g class="ribbon-back" style="display:none"></g>' +
            polygon([Z, P, F, L], "#3d0b08") +
            polygon([L, Z, shift(Z, 26), shift(L, 26)], "#5a1410") +
            '<ellipse class="gift-glow" cx="120" cy="140" rx="70" ry="38" fill="url(#event-dLight)" opacity="0"/>' +
            polygon([L, F, shift(F, H), shift(L, H)], "url(#event-dWallL)") +
            polygon([F, P, shift(P, H), shift(F, H)], "url(#event-dWallR)") +
            '<g class="ribbon-fixed">' + polygon(band(L, F, H), "#f2c230") + polygon(band(F, P, H), "#c99716") + '</g>' +
            '<g class="lid">' +
            polygon([vL, vF, tF, tL], "#c63a2c") +
            polygon([vF, vP, tP, tF], "#93271d") +
            '<g class="ribbon-fixed">' + polygon(band(tL, tF, V), "#f2c230") + polygon(band(tF, tP, V), "#c99716") + '</g>' +
            polygon([tZ, tP, tF, tL], "url(#event-dTop)") +
            '<g class="ribbon-fixed">' + polygon(lidBand(tL, tZ, tF, tP), "#ffd75a") + polygon(lidBand(tZ, tP, tL, tF), "#ffd75a") + '</g>' +
            '</g>' +
            '<path d="M' + pointsOn([L, F]).replace(" ", " L") + '" stroke="#fff" stroke-opacity=".25" stroke-width="1.2"/>' +
            // the ribbon in front of the box
            // (front bands, and the back ones while they lie on the lid)
            '<g class="ribbon-front" style="display:none"></g>' +
            '<g class="bow">' +
            '<path class="tail-l" d="' + end(0, -1) + '" stroke="#e8b31e" stroke-width="7" fill="none" stroke-linecap="round"/>' +
            '<path class="tail-r" d="' + end(0, 1) + '" stroke="#d9a51c" stroke-width="7" fill="none" stroke-linecap="round"/>' +
            '<path class="loop-l" d="' + loop(1, -1) + '" stroke="url(#event-dBow)" stroke-width="8" fill="none" stroke-linejoin="round"/>' +
            '<path class="loop-r" d="' + loop(1, 1) + '" stroke="url(#event-dBow)" stroke-width="8" fill="none" stroke-linejoin="round"/>' +
            '<circle class="knot" cx="' + knot[0] + '" cy="' + knot[1] + '" r="8" fill="#f2c230"/>' +
            '</g>' +
            '</g>' +
            '<g class="rays" opacity="0"></g>';
        stage.appendChild(canvas);
        var hint = document.createElement("p");
        hint.className = "event-hint";
        hint.textContent = api.texts.hint;
        stage.appendChild(hint);
        var gift = canvas.querySelector(".gift-whole");
        gift.setAttribute("aria-label", api.texts.gift);
        // The place for the wish is here from the start, empty - the text comes when
        // the wish shows, so a screen reader announces it.
        var wish = document.createElement("div");
        wish.className = "event-wish";
        wish.setAttribute("role", "status");
        wish.style.top = "18%";
        stage.appendChild(wish);

        // The ribbon's bands after untying. Under the gift the ribbon holds (the box's weight),
        // only the ends in the middle of the lid are loose. Each band: anchor A (where
        // it goes under the box), up the wall (Wt), along the lid's side (Ed, E) and over
        // the lid to the centre. After untying, the end on the lid flips over the edge
        // upwards and the whole band then falls out onto the ground - turning around the anchor.
        function stripe(a, b, ba, bb, colour, inFront) {
            var E = between(a, b, 0.5), Ed = shift(E, V), Wt = between(ba, bb, 0.5), A = shift(Wt, H);
            var u = [top[0] - E[0], top[1] - E[1]], lu = Math.hypot(u[0], u[1]);
            var outward = [Wt[0] - center[0], Wt[1] - center[1]], dv = Math.hypot(outward[0], outward[1]);
            // The band's width lies along the wall's edge (the axis the band turns
            // around while falling) - so the bottom end sits exactly on the box's edge.
            var hr = [bb[0] - ba[0], bb[1] - ba[1]], dh = Math.hypot(hr[0], hr[1]);
            return { A: A, Wt: Wt, Ed: Ed, E: E, u: u, lu: lu, outward: [outward[0] / dv, outward[1] / dv],
                     e: [hr[0] / dh, hr[1] / dh], w: W * dh,
                     length: H + farther(Wt, Ed) + V + lu, colour: colour, inFront: inFront };
        }
        var STRIPES = [stripe(tL, tZ, L, Z, "#e2b52a", false), stripe(tZ, tP, Z, P, "#e2b52a", false),
                     stripe(tL, tF, L, F, "#f2c230", true), stripe(tF, tP, F, P, "#d9a51c", true)];
        // The end on the lid flipped by angle a (0 = lies towards the centre, pi/2 = sticks up).
        function flip(p, a) {
            var C = [p.E[0] + Math.cos(a) * p.u[0], p.E[1] + Math.cos(a) * p.u[1] - Math.sin(a) * p.lu];
            return [p.A, p.Wt, p.Ed, p.E, C];
        }
        // The whole band tilted from the wall by angle f
        // (0 = straight up, pi/2 = lies on the ground outside).
        // Soft: the middle lags a little, the band bends while falling.
        function fall(p, f) {
            function direction(g) { return [Math.sin(g) * p.outward[0] * p.length, -Math.cos(g) * p.length + Math.sin(g) * p.outward[1] * p.length]; }
            // The bend is largest in the middle of the fall; on the ground the band lies straight.
            var goal = direction(f), bend = direction(f * (1 - 0.18 * Math.sin(2 * f)));
            return { A: p.A, c: [p.A[0] + bend[0] * 0.55, p.A[1] + bend[1] * 0.55], end: [p.A[0] + goal[0], p.A[1] + goal[1]] };
        }

        var opened = false;
        function unwrap() {
            if (opened) { return; }
            opened = true;
            gift.style.animation = "none";
            gift.removeAttribute("tabindex");
            gift.removeAttribute("role");
            gift.removeAttribute("aria-label");
            if (document.activeElement === gift) { stage.focus(); }
            hint.textContent = "";
            var lid = canvas.querySelector(".lid"), glow = canvas.querySelector(".gift-glow");
            var rays = canvas.querySelector(".rays"), bow = canvas.querySelector(".bow");
            var front = canvas.querySelector(".ribbon-front"), back = canvas.querySelector(".ribbon-back");
            var fixed = canvas.querySelectorAll(".ribbon-fixed");
            var el = function (s) { return canvas.querySelector(s); };
            for (var i = 0; i < 9; i++) {
                var u = -Math.PI / 2 + (i - 4) * 0.2, d = 150;
                api.svg("polygon", { points: "120,140 " + (120 + Math.cos(u - 0.05) * d).toFixed(1) + "," + (140 + Math.sin(u - 0.05) * d).toFixed(1) +
                                             " " + (120 + Math.cos(u + 0.05) * d).toFixed(1) + "," + (140 + Math.sin(u + 0.05) * d).toFixed(1),
                                     fill: "#ffe27a", opacity: (0.25 + (i % 3) * 0.12).toFixed(2) }, rays);
            }
            // A band = a flat strip: the centre line shifted by +-width along the edge.
            function bandElement(parent, colour) {
                return api.svg("polygon", { fill: colour, stroke: "#9c7410", "stroke-width": "1.2", "stroke-linejoin": "round" }, parent);
            }
            var drawings = STRIPES.map(function (p) { return { front: bandElement(front, p.colour), back: bandElement(back, p.colour) }; });
            function bandPoints(vertices, p) {
                var one = vertices.map(function (b) { return [b[0] + p.e[0] * p.w, b[1] + p.e[1] * p.w]; });
                var second = vertices.map(function (b) { return [b[0] - p.e[0] * p.w, b[1] - p.e[1] * p.w]; }).reverse();
                return pointsOn(one.concat(second));
            }
            function curve(a, c, k, n) {
                var vertices = [];
                for (var j = 0; j <= n; j++) {
                    var t = j / n, s = 1 - t;
                    vertices.push([s * s * a[0] + 2 * s * t * c[0] + t * t * k[0], s * s * a[1] + 2 * s * t * c[1] + t * t * k[1]]);
                }
                return vertices;
            }
            // A band over time: first the end flips (k1 0..1), then it falls out (k2 0..1).
            // Front bands are always in front of the gift, back ones behind it.
            function draw(i, k1, k2) {
                var p = STRIPES[i], drawing = drawings[i], here = p.inFront ? drawing.front : drawing.back, elsewhere = p.inFront ? drawing.back : drawing.front;
                if (k2 <= 0) {
                    var vertices = flip(p, Math.PI / 2 * k1);
                    // A back band: the wall is behind the box -
                    // in front of it only a piece from the lid's edge.
                    drawing.front.setAttribute("points", bandPoints(p.inFront ? vertices : vertices.slice(3), p));
                    drawing.back.setAttribute("points", "");
                    return;
                }
                var g = fall(p, Math.PI / 2 * k2);
                here.setAttribute("points", bandPoints(curve(g.A, g.c, g.end, 16), p));
                elsewhere.setAttribute("points", "");
            }
            if (api.quiet()) {
                [bow, lid].forEach(function (e) { e.style.display = "none"; });
                fixed.forEach(function (e) { e.style.display = "none"; });
                front.style.display = "";
                back.style.display = "";
                STRIPES.forEach(function (p, i) { draw(i, 1, 1); });
                glow.setAttribute("opacity", "1");
                rays.setAttribute("opacity", "1");
                wish.textContent = api.texts.wish;
                return;
            }
            var UNTYING = 900, FLIP = 450, FALL = 650, LANDING = 260;
            var SHIFT = [160, 240, 0, 80];   // each band a little later
            var start = performance.now();
            function step(t) {
                var time = t - start;
                // 1) The bow: the right loop pulls in and its end gets longer, then the left one.
                var sP = 1 - segment(time, 0, 520), sL = 1 - segment(time, 280, 820);
                el(".loop-r").setAttribute("d", loop(Math.max(sP, 0.001), 1));
                el(".loop-l").setAttribute("d", loop(Math.max(sL, 0.001), -1));
                el(".loop-r").style.opacity = sP > 0.03 ? 1 : 0;
                el(".loop-l").style.opacity = sL > 0.03 ? 1 : 0;
                el(".tail-r").setAttribute("d", end((1 - sP) * 36, 1));
                el(".tail-l").setAttribute("d", end((1 - sL) * 36, -1));
                el(".knot").setAttribute("r", (8 - 3 * segment(time, 600, UNTYING)).toFixed(1));
                if (time < UNTYING) { api.frame(step); return; }
                // 2) Untied: the knot and the bow's ends are now four loose ends of bands.
                if (front.style.display === "none") {
                    fixed.forEach(function (e) { e.style.display = "none"; });
                    bow.style.display = "none";
                    front.style.display = "";
                    back.style.display = "";
                }
                var done = true;
                STRIPES.forEach(function (p, i) {
                    var c = time - UNTYING - SHIFT[i];
                    var k1 = segment(c, 0, FLIP);
                    // The fall speeds up (k*k) and at the end the band bounces briefly.
                    var tp = (c - FLIP) / FALL, k2 = tp <= 0 ? 0 : tp >= 1 ? 1 : tp * tp;
                    var bounce = c - FLIP - FALL;
                    if (bounce > 0 && bounce < LANDING) { k2 = 1 - 0.07 * Math.sin(Math.PI * bounce / LANDING); }
                    if (bounce < LANDING) { done = false; }
                    draw(i, k1, k2);
                });
                if (!done) { api.frame(step); }
            }
            api.frame(step);
            // 3) Only the unwrapped gift opens.
            var OPENING = UNTYING + 240 + FLIP + FALL + LANDING + 200;
            lid.style.transformBox = "fill-box";
            lid.style.transformOrigin = "30% 80%";
            lid.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                           { transform: "translate(-10px,-40px) rotate(-12deg)", opacity: 1, offset: 0.35 },
                           { transform: "translate(-90px,-170px) rotate(-40deg)", opacity: 0 }],
                          { duration: 1100, delay: OPENING, easing: "cubic-bezier(.3,.6,.4,1)", fill: "forwards" });
            glow.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 600, delay: OPENING + 250, fill: "forwards" });
            rays.style.transformBox = "fill-box";
            rays.style.transformOrigin = "50% 100%";
            rays.animate([{ opacity: 0, transform: "scaleY(.3)" }, { opacity: 1, transform: "scaleY(1)" }],
                            { duration: 700, delay: OPENING + 350, easing: "ease-out", fill: "forwards" });
            wish.animate([{ transform: "translateY(90px) scale(.2)", opacity: 0 },
                           { transform: "translateY(-6px) scale(1.12)", opacity: 1, offset: 0.7 },
                           { transform: "translateY(0) scale(1)", opacity: 1 }],
                          { duration: 900, delay: OPENING + 500, easing: "ease-out", fill: "both" });
            api.after(OPENING + 500, function () { wish.textContent = api.texts.wish; });
            api.after(OPENING + 400, function () {
                var r = canvas.getBoundingClientRect(), j = stage.getBoundingClientRect();
                api.confetti(stage, r.left - j.left + r.width / 2, r.top - j.top + r.height * 0.5, 70);
            });
        }
        gift.addEventListener("click", unwrap);
        gift.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                unwrap();
            }
        });
    });
})();
