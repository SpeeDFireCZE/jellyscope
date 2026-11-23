/* ---------------------------------------------------------------------------
   Seasonal game: Halloween - a pumpkin (events.js loads it after a click on the coffin).

   The pumpkin's body is made of lobes; dragging the mouse or a finger traces a shape, and on
   release it is cut out - a hole with a band of flesh at the edge (depth), the cut piece
   of rind falls out. A button lights the pumpkin (a flame inside).
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    // The pumpkin's lobes from back to front: [cx, rx, ry]. Common centre y = 178.
    var LOBES = [[62, 44, 80], [238, 44, 80], [90, 54, 92], [210, 54, 92], [120, 58, 98], [180, 58, 98], [150, 60, 102]];

    function path(points) {
        return "M" + points.map(function (p) { return p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" L");
    }

    JellyscopeGames.register("halloween", function (stage, api) {
        var canvas = api.svg("svg", { "class": "canvas pumpkin-canvas", viewBox: "0 0 300 300", role: "img" });
        canvas.setAttribute("aria-label", api.texts.pumpkin);
        var body = LOBES.map(function (l) {
            return '<ellipse cx="' + l[0] + '" cy="178" rx="' + l[1] + '" ry="' + l[2] + '"/>';
        }).join("");
        canvas.innerHTML =
            '<defs>' +
            '<clipPath id="event-pumpkinBody">' + body + '</clipPath>' +
            '<radialGradient id="event-dLobe" cx=".42" cy=".38" r=".75"><stop offset="0" stop-color="#ffb04a"/>' +
            '<stop offset=".55" stop-color="#f07d14"/><stop offset="1" stop-color="#b44a06"/></radialGradient>' +
            '<radialGradient id="event-dLobeDark" cx=".5" cy=".4" r=".8"><stop offset="0" stop-color="#e8740f"/><stop offset="1" stop-color="#9c3e05"/></radialGradient>' +
            '<linearGradient id="event-dStem" x1="0" x2="1"><stop offset="0" stop-color="#6b8f2a"/><stop offset="1" stop-color="#3d5a17"/></linearGradient>' +
            '<radialGradient id="event-dFlame" cx=".5" cy=".55" r=".6"><stop offset="0" stop-color="#fff7c4"/>' +
            '<stop offset=".45" stop-color="#ffc83d"/><stop offset="1" stop-color="#ff7a00"/></radialGradient>' +
            '<radialGradient id="event-dInner" cx=".5" cy=".55" r=".55"><stop offset="0" stop-color="#ffcf5a" stop-opacity=".5"/>' +
            '<stop offset="1" stop-color="#ff7a00" stop-opacity="0"/></radialGradient>' +
            '<filter id="event-dGlow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="3" result="r"/>' +
            '<feMerge><feMergeNode in="r"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' +
            '</defs>' +
            '<ellipse cx="150" cy="282" rx="118" ry="14" fill="#000" opacity=".35"/>' +
            LOBES.map(function (l, i) {
                return '<ellipse cx="' + l[0] + '" cy="178" rx="' + l[1] + '" ry="' + l[2] + '" fill="url(#' + (i < 2 ? "event-dLobeDark" : "event-dLobe") + ')"/>';
            }).join("") +
            // grooves between the lobes
            '<path d="M93 92 Q78 178 93 266 M207 92 Q222 178 207 266 M122 82 Q112 178 122 274 M178 82 Q188 178 178 274" ' +
            'stroke="#9c3e05" stroke-width="2.5" fill="none" opacity=".55"/>' +
            '<ellipse cx="150" cy="84" rx="34" ry="9" fill="#8a3504"/>' +
            '<path d="M140 86 C138 64 146 48 166 40 L172 50 C158 56 154 68 156 86 Z" fill="url(#event-dStem)"/>' +
            '<ellipse cx="108" cy="128" rx="18" ry="34" fill="#fff" opacity=".13" transform="rotate(-12 108 128)"/>' +
            '<ellipse class="pumpkin-light" cx="150" cy="185" rx="130" ry="105" fill="url(#event-dInner)"/>' +
            '<g class="cutouts" clip-path="url(#event-pumpkinBody)"></g>' +
            '<path class="knife-trail" d="" fill="none" stroke="#3a1505" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="5 4"/>';
        stage.appendChild(canvas);
        var hint = document.createElement("p");
        hint.className = "event-hint";
        hint.textContent = api.texts.hint;
        stage.appendChild(hint);
        var buttons = document.createElement("div");
        buttons.className = "event-buttons";
        var lightUp = document.createElement("button");
        lightUp.type = "button";
        lightUp.className = "primary-choice";
        lightUp.textContent = api.texts.lightUp;
        var again = document.createElement("button");
        again.type = "button";
        again.textContent = api.texts.again;
        buttons.appendChild(lightUp);
        buttons.appendChild(again);
        stage.appendChild(buttons);

        var cutouts = canvas.querySelector(".cutouts"), trail = canvas.querySelector(".knife-trail");
        // Only one pointer draws - the left button or the first finger. The right
        // button, a second finger or a move without a press neither start nor spoil a stroke.
        var points = null, pointer = null, counter = 0;
        function cancel() {
            points = null;
            pointer = null;
            trail.setAttribute("d", "");
        }
        canvas.addEventListener("pointerdown", function (e) {
            if (e.button !== 0 || !e.isPrimary) { return; }
            canvas.setPointerCapture(e.pointerId);
            pointer = e.pointerId;
            var b = api.pointInSvg(canvas, e);
            points = [[b.x, b.y]];
        });
        canvas.addEventListener("pointermove", function (e) {
            if (!points || e.pointerId !== pointer) { return; }
            // A release that never arrived (a context menu, the mouse outside the window).
            if (e.buttons === 0) {
                cancel();
                return;
            }
            var b = api.pointInSvg(canvas, e), lastPoint = points[points.length - 1];
            if (Math.hypot(b.x - lastPoint[0], b.y - lastPoint[1]) < 2) { return; }
            points.push([b.x, b.y]);
            trail.setAttribute("d", path(points));
        });
        // Release: the stroke's shape is cut out (a stroke that is too small is dropped).
        function finished(e) {
            if (!points || e.pointerId !== pointer) { return; }
            var shape = points;
            cancel();
            var xs = shape.map(function (p) { return p[0]; }), ys = shape.map(function (p) { return p[1]; });
            if (shape.length < 4 || Math.max.apply(null, xs) - Math.min.apply(null, xs) < 6
                    || Math.max.apply(null, ys) - Math.min.apply(null, ys) < 6) { return; }
            cutOut(shape);
        }
        canvas.addEventListener("pointerup", finished);
        // An interrupted stroke (the system took the gesture) is not cut out.
        canvas.addEventListener("pointercancel", function (e) {
            if (e.pointerId === pointer) { cancel(); }
        });

        // A cut-out = a hole in the wall of flesh: the shape filled with flesh, and in it shifted
        // darkness clipped by the same shape - at the bottom edge a band of flesh remains
        // that looks like the cut surface (depth).
        function cutOut(shape) {
            var d = path(shape) + " Z";
            var id = "event-dCutout" + (++counter);
            var g = api.svg("g", { "class": "cutout" }, cutouts);
            var clip = api.svg("clipPath", { id: id }, api.svg("defs", {}, g));
            api.svg("path", { d: d }, clip);
            api.svg("path", { "class": "cutout-flesh", d: d, fill: "#f6c06a" }, g);
            var hole = api.svg("g", { "clip-path": "url(#" + id + ")" }, g);
            api.svg("path", { "class": "cutout-dark", d: d, transform: "translate(1.5 -4.5)", fill: "#240c02" }, hole);
            // the cut piece of rind falls out
            if (!api.quiet()) {
                var piece = api.svg("path", { d: d, fill: "#f07d14", stroke: "#b44a06", "stroke-width": "1" }, canvas);
                piece.style.transformBox = "fill-box";
                piece.style.transformOrigin = "50% 50%";
                piece.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                             { transform: "translate(" + ((Math.random() - 0.5) * 30) + "px,140px) rotate(" + ((Math.random() - 0.5) * 120) + "deg)", opacity: 0 }],
                            { duration: 750, easing: "cubic-bezier(.4,0,.8,.6)", fill: "forwards" }).onfinish = function () { piece.remove(); };
            }
        }
        lightUp.addEventListener("click", function () {
            var lit = !canvas.classList.contains("glowing-pumpkin");
            canvas.classList.toggle("glowing-pumpkin", lit);
            lightUp.textContent = lit ? api.texts.blowOut : api.texts.lightUp;
        });
        again.addEventListener("click", function () { cutouts.innerHTML = ""; });
    });
})();
