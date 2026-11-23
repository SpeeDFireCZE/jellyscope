/* ---------------------------------------------------------------------------
   Seasonal game: New Year's Eve - a rocket (events.js loads it after a click on the
   champagne).

   The lighter is dragged (or put with Enter) to the fuse, which burns down
   from its end to the rocket, the scene closes and a big rocket flies across
   the whole page: a burst, the sparks form the year being celebrated (by the server's
   date) and a wish. The finale runs on Web Animations only - no timers, the layer
   disappears with the last animation.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    // The end of the fuse and the tip of the lighter's flame (relative to its origin).
    var FUSE_END = [214, 216], FLAME_TIP = [4, -48];
    var COLOURS = ["#ffd166", "#ff6b6b", "#7aa2ff", "#6ee7a8", "#ff8fd1", "#ffffff"];
    var FLIGHT = 1300;   // ms - the big rocket from the bottom of the screen to the burst

    // Points (every 6 px) that make up the text - by the font on a canvas.
    function textPoints(text, width, height) {
        var c = document.createElement("canvas");
        c.width = width;
        c.height = height;
        var ctx = c.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.font = "800 " + Math.round(height * 0.82) + "px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(text, width / 2, height / 2);
        var data = ctx.getImageData(0, 0, width, height).data, points = [];
        for (var y = 0; y < height; y += 6) {
            for (var x = 0; x < width; x += 6) {
                if (data[(y * width + x) * 4 + 3] > 128) { points.push([x - width / 2, y - height / 2]); }
            }
        }
        return points;
    }
    function slowsDown(t) { return 1 - Math.pow(1 - t, 3); }
    function spark(layer, x, y, colour) {
        var j = document.createElement("span");
        j.className = "spark-f";
        j.style.left = x + "px";
        j.style.top = y + "px";
        j.style.background = colour;
        layer.appendChild(j);
        return j;
    }

    function finale(api) {
        var layer = document.createElement("div");
        layer.className = "event-finale";
        document.body.appendChild(layer);
        var w = layer.clientWidth, h = layer.clientHeight;
        var cx = w / 2, cy = h * 0.36;
        var yearWidth = Math.min(360, w - 40), yearHeight = Math.round(yearWidth * 130 / 360);
        var text = document.createElement("div");
        text.className = "year-text";
        text.setAttribute("role", "status");
        text.style.top = (cy + yearHeight * 0.62) + "px";
        // The place for the wish is in the layer from the start, empty - the text comes
        // with the burst, so a screen reader announces it.
        layer.appendChild(text);

        // Without motion: the year and the wish just stand there, stay a while and disappear.
        if (api.quiet()) {
            textPoints(api.texts.year, yearWidth, yearHeight).forEach(function (point) {
                var j = spark(layer, cx + point[0], cy + point[1], "#ffd97a");
                j.style.boxShadow = "0 0 9px #ffb52e";
            });
            text.style.opacity = "1";
            // The text a moment later (not right on insertion), so it gets announced.
            text.animate([{ opacity: 1 }, { opacity: 1 }], { duration: 60 })
                .onfinish = function () { text.textContent = api.texts.wish; };
            layer.animate([{ opacity: 1 }, { opacity: 1, offset: 0.92 }, { opacity: 0 }], { duration: 5000 })
                .onfinish = function () { layer.remove(); };
            return;
        }

        function explode() {
            for (var i = 0; i < 110; i++) {
                var u = Math.random() * Math.PI * 2, r = 60 + Math.random() * Math.min(w, h) * 0.42;
                var b = COLOURS[i % COLOURS.length];
                var j = spark(layer, cx, cy, b);
                j.style.boxShadow = "0 0 8px " + b;
                j.animate([{ transform: "translate(0,0)", opacity: 1 },
                           { transform: "translate(" + (Math.cos(u) * r) + "px," + (Math.sin(u) * r + r * 0.35) + "px)", opacity: 0 }],
                          { duration: 1600 + Math.random() * 700, easing: "cubic-bezier(.1,.7,.3,1)", fill: "forwards" })
                    .onfinish = (function (el) { return function () { el.remove(); }; })(j);
            }
            textPoints(api.texts.year, yearWidth, yearHeight).forEach(function (point, i) {
                var j = spark(layer, cx, cy, "#ffd97a");
                j.style.boxShadow = "0 0 9px #ffb52e";
                j.animate([{ transform: "translate(0,0)", opacity: 0 },
                           { transform: "translate(" + point[0] + "px," + point[1] + "px)", opacity: 1, offset: 0.35 },
                           { transform: "translate(" + point[0] + "px," + point[1] + "px)", opacity: 1, offset: 0.85 },
                           { transform: "translate(" + point[0] + "px," + (point[1] + 40) + "px)", opacity: 0 }],
                          { duration: 4800, delay: 150 + (i % 7) * 20, easing: "ease-out", fill: "both" });
            });
            text.textContent = api.texts.wish;
            // The wish finishes last - the whole layer goes with it.
            text.animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "translateY(0)", offset: 0.25 },
                          { opacity: 1, offset: 0.85 }, { opacity: 0 }], { duration: 4800, delay: 600, fill: "both" })
                .onfinish = function () { layer.remove(); };
        }

        // The rocket's flight: slowing towards the top of the path. The trail of sparks is
        // computed ahead (every 40 ms, the place where the rocket is at that moment).
        var rocket = document.createElement("span");
        rocket.className = "rocket-big";
        rocket.style.left = cx + "px";
        rocket.style.top = h + "px";
        layer.appendChild(rocket);
        var paths = [];
        for (var k = 0; k <= 12; k++) { paths.push({ transform: "translateY(" + ((cy - h) * slowsDown(k / 12)) + "px)" }); }
        rocket.animate(paths, { duration: FLIGHT, fill: "forwards" }).onfinish = function () {
            rocket.remove();
            explode();
        };
        for (var t = 0; t < FLIGHT - 80; t += 40) {
            var trail = spark(layer, cx, h + (cy - h) * slowsDown(t / FLIGHT) + 34, "#ffd166");
            trail.style.opacity = "0";
            trail.animate([{ opacity: 1, transform: "translate(0,0)" },
                           { opacity: 0, transform: "translate(" + ((Math.random() - 0.5) * 16) + "px,24px)" }],
                          { duration: 500, delay: t, fill: "forwards" })
                .onfinish = (function (el) { return function () { el.remove(); }; })(trail);
        }
    }

    JellyscopeGames.register("newyear", function (stage, api) {
        var canvas = api.svg("svg", { "class": "canvas", viewBox: "0 0 300 330" });
        canvas.innerHTML =
            '<defs>' +
            '<linearGradient id="event-rBody" x1="0" x2="1"><stop offset="0" stop-color="#7a1616"/><stop offset=".32" stop-color="#e2453c"/>' +
            '<stop offset=".48" stop-color="#ff9a8c"/><stop offset=".62" stop-color="#d63a3a"/><stop offset="1" stop-color="#6e1414"/></linearGradient>' +
            '<linearGradient id="event-rStripe" x1="0" x2="1"><stop offset="0" stop-color="#8a6400"/><stop offset=".45" stop-color="#ffe9a0"/>' +
            '<stop offset=".55" stop-color="#ffd34d"/><stop offset="1" stop-color="#7a5800"/></linearGradient>' +
            '<linearGradient id="event-rCone" x1="0" x2="1"><stop offset="0" stop-color="#142f7a"/><stop offset=".42" stop-color="#5b8cff"/>' +
            '<stop offset=".55" stop-color="#3a66d8"/><stop offset="1" stop-color="#112565"/></linearGradient>' +
            '<linearGradient id="event-rStick" x1="0" x2="1"><stop offset="0" stop-color="#5a4020"/><stop offset=".5" stop-color="#c49a62"/><stop offset="1" stop-color="#4a3418"/></linearGradient>' +
            '<linearGradient id="event-zBody" x1="0" x2="1"><stop offset="0" stop-color="#123e73"/><stop offset=".35" stop-color="#3f8fe0"/>' +
            '<stop offset=".5" stop-color="#a9d6ff"/><stop offset=".62" stop-color="#3b86d4"/><stop offset="1" stop-color="#0f3561"/></linearGradient>' +
            '<linearGradient id="event-zMetal" x1="0" x2="1"><stop offset="0" stop-color="#5f666f"/><stop offset=".4" stop-color="#dfe4ea"/>' +
            '<stop offset=".5" stop-color="#ffffff"/><stop offset=".62" stop-color="#b3bac4"/><stop offset="1" stop-color="#555b63"/></linearGradient>' +
            '<radialGradient id="event-zGlow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffe27a" stop-opacity=".9"/>' +
            '<stop offset="1" stop-color="#ff9a1f" stop-opacity="0"/></radialGradient>' +
            '</defs>' +
            '<ellipse cx="150" cy="316" rx="40" ry="6" fill="#000" opacity=".35"/>' +
            // stick, body (a cylinder), stripes, cone
            '<rect x="147" y="180" width="6" height="134" rx="2" fill="url(#event-rStick)"/>' +
            '<rect x="134" y="118" width="32" height="64" fill="url(#event-rBody)"/>' +
            '<ellipse cx="150" cy="182" rx="16" ry="4.5" fill="#5e1111"/>' +
            '<rect x="134" y="131" width="32" height="6" fill="url(#event-rStripe)"/>' +
            '<rect x="134" y="147" width="32" height="6" fill="url(#event-rStripe)"/>' +
            '<rect x="134" y="163" width="32" height="6" fill="url(#event-rStripe)"/>' +
            '<path d="M132 120 L150 82 L168 120 Q150 127 132 120 Z" fill="url(#event-rCone)"/>' +
            '<ellipse cx="150" cy="120" rx="18" ry="4.5" fill="none" stroke="#0d1f52" stroke-width="1.2" opacity=".6"/>' +
            // the fuse from the bottom of the rocket to its end
            '<path class="fuse" d="M150 182 C150 200 172 202 180 196 C190 189 204 206 214 216" fill="none" stroke="#6b4a2b" stroke-width="3.5" stroke-linecap="round"/>' +
            '<circle class="fuse-target" cx="214" cy="216" r="7" fill="url(#event-zGlow)"/>' +
            '<circle class="fuse-end" cx="214" cy="216" r="3" fill="#a07b4f"/>' +
            '<g class="fuse-spark" opacity="0"><circle r="8" fill="#ffd166" opacity=".45"/><circle r="3.4" fill="#fff4c2"/></g>' +
            // the lighter: a glossy body, a metal head with a wheel, a flame with a halo
            '<g class="lighter" transform="translate(56 236)" tabindex="0" role="button">' +
            '<circle class="prompt" cx="0" cy="8" r="40" fill="none" stroke="#ffd166" stroke-width="2.5"/>' +
            '<rect x="-18" y="0" width="36" height="58" rx="8" fill="url(#event-zBody)"/>' +
            '<rect x="-12" y="6" width="5" height="44" rx="2.5" fill="#fff" opacity=".35"/>' +
            '<rect x="-18" y="-14" width="36" height="16" rx="3" fill="url(#event-zMetal)"/>' +
            '<circle cx="-9" cy="-17" r="6" fill="url(#event-zMetal)" stroke="#4a4f56" stroke-width="1.2"/>' +
            '<path d="M-14 -17 h10 M-9 -22 v10" stroke="#4a4f56" stroke-width="1"/>' +
            '<circle class="halo" cx="4" cy="-33" r="17" fill="url(#event-zGlow)"/>' +
            '<path class="flame" d="M4 -48 C15 -36 13 -24 4 -19 C-5 -24 -7 -36 4 -48 Z" fill="#ffad1f"/>' +
            '<path d="M4 -38 C9 -31 8 -25 4 -23 C0 -25 -1 -31 4 -38 Z" fill="#fff4b0"/>' +
            '</g>';
        stage.appendChild(canvas);
        var hint = document.createElement("p");
        hint.className = "event-hint";
        hint.textContent = api.texts.hint;
        stage.appendChild(hint);
        var lighter = canvas.querySelector(".lighter");
        lighter.setAttribute("aria-label", api.texts.lighter);
        var position = { x: 56, y: 236 }, shift = null, burning = false;
        // The lighter can be carried over the whole window, it just cannot escape it:
        // the limits are the window's edges converted to canvas coordinates, shrunk by
        // the lighter's body (horizontally +-18, vertically from the flame -48 to the bottom 58).
        function bounds() {
            var m = canvas.getScreenCTM().inverse();
            var a = canvas.createSVGPoint(), b = canvas.createSVGPoint();
            b.x = window.innerWidth;
            b.y = window.innerHeight;
            a = a.matrixTransform(m);
            b = b.matrixTransform(m);
            return { x1: a.x + 20, x2: b.x - 20, y1: a.y + 50, y2: b.y - 60 };
        }
        function move(x, y) {
            var frameWindow = bounds();
            position = { x: Math.min(frameWindow.x2, Math.max(frameWindow.x1, x)), y: Math.min(frameWindow.y2, Math.max(frameWindow.y1, y)) };
            lighter.setAttribute("transform", "translate(" + position.x + " " + position.y + ")");
        }
        lighter.addEventListener("pointerdown", function (e) {
            if (burning || e.button !== 0 || !e.isPrimary) { return; }
            lighter.setPointerCapture(e.pointerId);
            lighter.classList.add("held");
            var b = api.pointInSvg(canvas, e);
            shift = { x: b.x - position.x, y: b.y - position.y };
        });
        lighter.addEventListener("pointermove", function (e) {
            if (!shift || burning) { return; }
            if (e.buttons === 0) {   // a release that never arrived
                shift = null;
                return;
            }
            var b = api.pointInSvg(canvas, e);
            move(b.x - shift.x, b.y - shift.y);
            if (Math.hypot(position.x + FLAME_TIP[0] - FUSE_END[0],
                           position.y + FLAME_TIP[1] - FUSE_END[1]) < 20) { ignite(); }
        });
        lighter.addEventListener("pointerup", function () { shift = null; });
        lighter.addEventListener("pointercancel", function () { shift = null; });
        // Keyboard: Enter puts the lighter right to the fuse.
        lighter.addEventListener("keydown", function (e) {
            if ((e.key === "Enter" || e.key === " ") && !burning) {
                e.preventDefault();
                move(FUSE_END[0] - FLAME_TIP[0], FUSE_END[1] - FLAME_TIP[1]);
                ignite();
            }
        });

        function ignite() {
            burning = true;
            shift = null;
            hint.textContent = api.texts.burning;
            lighter.removeAttribute("tabindex");
            if (document.activeElement === lighter) { stage.focus(); }
            lighter.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 400, fill: "forwards" });
            canvas.querySelector(".fuse-target").style.display = "none";
            var fuse = canvas.querySelector(".fuse");
            var length = fuse.getTotalLength();
            var flame = canvas.querySelector(".fuse-spark");
            flame.setAttribute("opacity", "1");
            canvas.querySelector(".fuse-end").setAttribute("opacity", "0");
            var flightTime = api.quiet() ? 300 : 2600, start = performance.now();
            function step(t) {
                var k = Math.min(1, (t - start) / flightTime);
                // What remains is the stretch from the rocket to the spark;
                // the burnt end disappears.
                var remaining = length * (1 - k);
                fuse.style.strokeDasharray = remaining + " " + length;
                var b = fuse.getPointAtLength(remaining);
                flame.setAttribute("transform", "translate(" + b.x + " " + b.y + ")");
                if (!api.quiet() && Math.random() < 0.6) { sparkFlight(b.x, b.y); }
                if (k < 1) {
                    api.frame(step);
                } else {
                    api.after(250, function () {
                        api.close();
                        finale(api);
                    });
                }
            }
            api.frame(step);
        }
        function sparkFlight(x, y) {
            var c = api.svg("circle", { cx: x, cy: y, r: 1.3, fill: Math.random() < 0.5 ? "#ffd166" : "#fff" }, canvas);
            c.animate([{ transform: "translate(0,0)", opacity: 1 },
                       { transform: "translate(" + ((Math.random() - 0.5) * 22) + "px," + (-6 - Math.random() * 16) + "px)", opacity: 0 }],
                      { duration: 450, fill: "forwards" }).onfinish = function () { c.remove(); };
        }
    });
})();
