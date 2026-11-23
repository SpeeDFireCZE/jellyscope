/* ---------------------------------------------------------------------------
   Seasonal decoration: an animation over the whole page.

   Loaded only during a holiday (templates/_event_head.html); which holiday
   it is, <html data-event="..."> says. The layer lies over the whole page,
   is semi-transparent and clicks go through it (events.css), so it blocks nothing.

   Without motion - the system's "reduce motion" or the option on the My account
   page (data-event-motion="0") - nothing moves, only a few standing ornaments
   are placed. On a hidden tab the animation stops, so it does not eat the CPU for nothing.

   The look of the elements (snow, bats, rockets, the bunny) is in the holiday's CSS.
   At the end is the frame of the games that open after a click on the ornament.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    var root = document.documentElement;
    var holiday = root.getAttribute("data-event");
    if (!holiday || !document.body) { return; }

    // Caps on the number of elements - more only loads the CPU, not the eye.
    var SNOW = 50, SNOW_FEW = 25;
    var BATS = 6, BATS_FEW = 3;
    var NARROW = 720;   // px - below this window width, half the elements

    var SVG_SNOWFLAKE = '<svg viewBox="-10 -10 20 20"><g stroke="currentColor" stroke-width="1.7" stroke-linecap="round" fill="none">' +
        '<path d="M0-9V9M-7.8-4.5L7.8 4.5M-7.8 4.5L7.8-4.5"/>' +
        '<path d="M-2.2-7 0-5 2.2-7M-2.2 7 0 5 2.2 7M-7.1-1.6-4.3-2.5-4.9-5.3M7.1 1.6 4.3 2.5 4.9 5.3M-4.9 5.3-4.3 2.5-7.1 1.6M4.9-5.3 4.3-2.5 7.1-1.6"/></g></svg>';
    var SVG_BAT = '<svg viewBox="-14 -7 28 17">' +
        '<path d="M0 2 C-2-3-7-5.5-13.5-2.5 C-11.5-.5-10.5 2.5-10.5 5.5 C-8.5 3.8-6.5 4.3-5.5 7 C-4.3 5-2.3 5.2 0 8.5 C2.3 5.2 4.3 5 5.5 7 C6.5 4.3 8.5 3.8 10.5 5.5 C10.5 2.5 11.5-.5 13.5-2.5 C7-5.5 2-3 0 2 Z"/>' +
        '<ellipse cx="0" cy="3.2" rx="2.3" ry="3.4"/>' +
        '<path d="M-2.1 1.5 L-1.7-3 L-.5-.6 L.5-.6 L1.7-3 L2.1 1.5 Z"/>' +
        '<circle cx="-.8" cy=".6" r=".45" fill="#ff9d2e"/><circle cx=".8" cy=".6" r=".45" fill="#ff9d2e"/></svg>';
    var SVG_SPIDER = '<svg viewBox="-12 -9 24 22"><g style="stroke:var(--event-bat)" stroke-width="1.3" fill="none" stroke-linecap="round">' +
        '<path d="M-2.5-1 L-8-6 L-11-2M-2.5 0 L-9-2 L-12 2M-2.5 1.5 L-9 3.5 L-11 8M-2.5 3 L-7 7 L-8 11"/>' +
        '<path d="M2.5-1 L8-6 L11-2M2.5 0 L9-2 L12 2M2.5 1.5 L9 3.5 L11 8M2.5 3 L7 7 L8 11"/></g>' +
        '<circle cx="0" cy="-3" r="2.7" style="fill:var(--event-bat)"/><ellipse cx="0" cy="2.8" rx="3.7" ry="4.6" style="fill:var(--event-bat)"/>' +
        '<circle cx="-1" cy="-3.5" r=".6" fill="#ff3b3b"/><circle cx="1" cy="-3.5" r=".6" fill="#ff3b3b"/></svg>';
    var SVG_BUNNY = '<svg viewBox="0 0 60 50"><g style="stroke:var(--event-bunny-outline)" stroke-width="1">' +
        '<ellipse cx="37" cy="10" rx="3.3" ry="10" transform="rotate(-12 37 10)" style="fill:var(--event-bunny)"/>' +
        '<ellipse cx="44" cy="10" rx="3.3" ry="10" transform="rotate(12 44 10)" style="fill:var(--event-bunny)"/>' +
        '<ellipse cx="24" cy="34" rx="16" ry="12" style="fill:var(--event-bunny)"/>' +
        '<circle cx="40.5" cy="25" r="9" style="fill:var(--event-bunny)"/>' +
        '<ellipse cx="31" cy="45.5" rx="6.5" ry="2.8" style="fill:var(--event-bunny)"/></g>' +
        '<circle cx="8.5" cy="31" r="4.6" fill="#fff" style="stroke:var(--event-bunny-outline)" stroke-width="1"/>' +
        '<ellipse cx="37" cy="10.5" rx="1.4" ry="7" transform="rotate(-12 37 10)" fill="#f4a7b9"/>' +
        '<ellipse cx="44" cy="10.5" rx="1.4" ry="7" transform="rotate(12 44 10)" fill="#f4a7b9"/>' +
        '<circle cx="44.5" cy="23" r="1.5" fill="#222"/><circle cx="49" cy="26.5" r="1.4" fill="#e57b94"/></svg>';
    var EGG_COLOURS = [["#f2c94c", "#e05a8a"], ["#7cc6f2", "#f2c94c"], ["#f28b82", "#ffffff"],
                       ["#a8e07c", "#7b5bd6"], ["#c9a7f2", "#ffd36e"]];
    var ROCKET_COLOURS = ["#ffd166", "#ff6b6b", "#7aa2ff", "#6ee7a8", "#ff8fd1", "#e0b03a"];
    var eggCounter = 0;

    function svgEgg(colours) {
        // Each egg its own clip - the same id twice would take the first one.
        var id = "event-v" + (eggCounter++);
        return '<svg viewBox="0 0 14 18"><defs><clipPath id="' + id + '"><ellipse cx="7" cy="9.5" rx="6" ry="8"/></clipPath></defs>' +
            '<ellipse cx="7" cy="9.5" rx="6" ry="8" fill="' + colours[0] + '"/><g clip-path="url(#' + id + ')" fill="none" stroke-width="1.7">' +
            '<path d="M0 8 l2-2 l2 2 l2-2 l2 2 l2-2 l2 2 l2-2" stroke="' + colours[1] + '"/>' +
            '<path d="M0 12.5 h14" stroke="' + colours[1] + '" opacity=".7"/></g></svg>';
    }

    var layer = document.createElement("div");
    layer.className = "event-effect";
    layer.setAttribute("aria-hidden", "true");
    document.body.appendChild(layer);

    var systemQuiet = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    var timers = [];
    var isHidden = document.hidden;

    function withoutMotion() {
        return root.getAttribute("data-event-motion") === "0" || Boolean(systemQuiet && systemQuiet.matches);
    }
    function isNarrow() { return window.innerWidth < NARROW; }
    function random(low, high) { return low + Math.random() * (high - low); }
    function pick(items) { return items[Math.floor(Math.random() * items.length)]; }
    function after(ms, fn) { timers.push(setTimeout(fn, ms)); }
    // Repeated launches (rockets, the bunny) skip a round on a hidden tab.
    function every(ms, fn) { timers.push(setInterval(function () { if (!isHidden) { fn(); } }, ms)); }
    function element(cls, html, variables) {
        var el = document.createElement("span");
        el.className = cls;
        el.innerHTML = html;
        Object.keys(variables || {}).forEach(function (k) { el.style.setProperty(k, variables[k]); });
        layer.appendChild(el);
        return el;
    }
    function clear() {
        timers.forEach(function (c) { clearTimeout(c); clearInterval(c); });
        timers = [];
        layer.getAnimations({ subtree: true }).forEach(function (a) { a.cancel(); });
        layer.innerHTML = "";
    }

    var animations = {
        christmas: function () {
            var count = isNarrow() ? SNOW_FEW : SNOW;
            for (var i = 0; i < count; i++) {
                var d = random(7, 12);
                var big = Math.random() < 0.45;
                element("snowflake", "<i>" + (big ? SVG_SNOWFLAKE : "<b></b>") + "</i>", {
                    "--x": random(0, 100) + "%", "--s": (big ? random(9, 15) : random(3, 6)) + "px",
                    "--o": random(0.55, 0.95).toFixed(2), "--d": d.toFixed(2) + "s",
                    "--z": (-random(0, d)).toFixed(2) + "s"
                });
            }
        },
        halloween: function () {
            var w = layer.clientWidth, h = layer.clientHeight;
            var count = isNarrow() ? BATS_FEW : BATS;
            for (var i = 0; i < count; i++) {
                var d = random(7, 11);
                var el = element("bat" + (i % 2 ? " reverse" : ""), "<i>" + SVG_BAT + "</i>", {
                    "--y": random(20, h * 0.85) + "px", "--s": random(30, 48) + "px",
                    "--d": d.toFixed(2) + "s", "--z": (-random(0, d)).toFixed(2) + "s"
                });
                el.firstChild.style.animationDelay = (-random(0, 1.3)).toFixed(2) + "s";
            }
            // The spider lowers itself into the free space top right, by the heading.
            element("spider", '<span class="thread"></span>' + SVG_SPIDER, {
                "--x": (w * 0.88) + "px", "--depth": (isNarrow() ? 30 : 58) + "px"
            });
        },
        newyear: function () {
            var w = layer.clientWidth, h = layer.clientHeight;
            function rocket() {
                var x = random(40, w - 40);
                var goal = h * random(0.1, 0.42);
                var colour = pick(ROCKET_COLOURS);
                var r = document.createElement("span");
                r.className = "rocket";
                r.style.left = x + "px";
                r.style.top = h + "px";
                r.style.background = colour;
                r.style.boxShadow = "0 0 6px " + colour;
                layer.appendChild(r);
                var flight = r.animate([{ transform: "translateY(0)" }, { transform: "translateY(" + (goal - h) + "px)" }],
                                     { duration: 950, easing: "cubic-bezier(.2,.6,.4,1)", fill: "forwards" });
                flight.onfinish = function () { r.remove(); explosion(x, goal, colour); };
            }
            function explosion(x, y, colour) {
                var n = 20, radius = isNarrow() ? random(35, 55) : random(55, 85);
                for (var i = 0; i < n; i++) {
                    var angle = (i / n) * Math.PI * 2;
                    var c = document.createElement("span");
                    c.className = "spark";
                    c.style.left = x + "px";
                    c.style.top = y + "px";
                    c.style.background = colour;
                    c.style.boxShadow = "0 0 7px " + colour;
                    layer.appendChild(c);
                    var k = random(0.75, 1.15);
                    c.animate([{ transform: "translate(0,0) scale(1)", opacity: 1 },
                               { transform: "translate(" + (Math.cos(angle) * radius * k) + "px," +
                                 (Math.sin(angle) * radius * k + radius * 0.4) + "px) scale(.35)", opacity: 0 }],
                              { duration: 1400, easing: "cubic-bezier(.1,.7,.3,1)", fill: "forwards" })
                        .onfinish = (function (el) { return function () { el.remove(); }; })(c);
                }
            }
            rocket();
            every(1000, function () { rocket(); if (Math.random() < 0.35) { after(300, rocket); } });
        },
        easter: function () {
            var w = layer.clientWidth;
            var s = isNarrow() ? 44 : 60;
            function run() {
                var bunny = element("bunny", "<i>" + SVG_BUNNY + "</i>", { "--s": s + "px" });
                var laid = 0;
                var t = setInterval(function () {
                    if (isHidden) { return; }
                    var r = bunny.getBoundingClientRect(), v = layer.getBoundingClientRect();
                    var x = r.left - v.left + s * 0.15;
                    if (x < 10 || x > w - 20 || laid > 5) { return; }
                    laid++;
                    var egg = element("egg", svgEgg(pick(EGG_COLOURS)));
                    egg.style.left = x + "px";
                    egg.animate([{ transform: "scale(0)", opacity: 0 },
                                     { transform: "scale(1.15)", opacity: 1, offset: 0.12 },
                                     { transform: "scale(1)", opacity: 1, offset: 0.2 },
                                     { transform: "scale(1)", opacity: 1, offset: 0.8 },
                                     { transform: "scale(.8)", opacity: 0 }], { duration: 4500, fill: "forwards" })
                        .onfinish = function () { egg.remove(); };
                }, 1500);
                timers.push(t);
                after(10500, function () { clearInterval(t); bunny.remove(); });
            }
            run();
            every(13000, run);
        }
    };

    // Without motion: a few ornaments standing at the edge.
    var quietAnimations = {
        christmas: function () {
            for (var i = 0; i < 16; i++) {
                var el = element("static snowflake-static", SVG_SNOWFLAKE);
                el.style.left = random(2, 96) + "%";
                el.style.top = random(2, 96) + "%";
                el.style.width = random(8, 14) + "px";
                el.style.transform = "rotate(" + random(0, 60) + "deg)";
            }
        },
        halloween: function () {
            [[0.55, 18], [0.72, 34]].forEach(function (p) {
                var el = element("static bat-static", SVG_BAT);
                el.style.left = (layer.clientWidth * p[0]) + "px";
                el.style.top = p[1] + "px";
            });
            var spider = element("static spider-static", '<span class="thread"></span>' + SVG_SPIDER);
            spider.style.left = (layer.clientWidth * 0.88) + "px";
            spider.style.top = (isNarrow() ? 30 : 58) + "px";
        },
        newyear: function () {
            [[0.35, 40, "#f2c14e"], [0.6, 28, "#ff6b6b"], [0.8, 44, "#7aa2ff"]].forEach(function (p) {
                for (var i = 0; i < 14; i++) {
                    var angle = (i / 14) * Math.PI * 2, radius = 24;
                    var c = element("static spark", "");
                    c.style.left = (layer.clientWidth * p[0] + Math.cos(angle) * radius) + "px";
                    c.style.top = (p[1] + Math.sin(angle) * radius) + "px";
                    c.style.background = p[2];
                    c.style.boxShadow = "0 0 6px " + p[2];
                }
            });
        },
        easter: function () {
            var bunny = element("static bunny-static", SVG_BUNNY);
            bunny.style.right = "40px";
            bunny.style.width = (isNarrow() ? 44 : 60) + "px";
            for (var i = 0; i < 4; i++) {
                var v = element("static egg-static", svgEgg(EGG_COLOURS[i]));
                v.style.right = (120 + i * 22) + "px";
            }
        }
    };

    function release() {
        clear();
        layer.style.setProperty("--w", (layer.clientWidth + 90) + "px");
        layer.style.setProperty("--h", layer.clientHeight + "px");
        var variant = withoutMotion() ? quietAnimations : animations;
        if (variant[holiday]) { variant[holiday](); }
    }

    // A hidden tab: running animations pause, new ones do not start.
    document.addEventListener("visibilitychange", function () {
        isHidden = document.hidden;
        layer.getAnimations({ subtree: true }).forEach(function (a) {
            if (isHidden) { a.pause(); } else { a.play(); }
        });
    });

    // A window resize. A mobile browser sends resize every time the address
    // bar hides (only the height changes) - so the effect is not rebuilt for
    // that, only the height for falling is recomputed. Placed again
    // (with a delay, not at every pixel) only when the WIDTH changes.
    var width = window.innerWidth;
    var pending = null;
    window.addEventListener("resize", function () {
        clearTimeout(pending);
        pending = setTimeout(function () {
            if (window.innerWidth !== width) {
                width = window.innerWidth;
                release();
            } else {
                layer.style.setProperty("--h", layer.clientHeight + "px");
            }
        }, 200);
    });
    if (systemQuiet && systemQuiet.addEventListener) { systemQuiet.addEventListener("change", release); }


    /* -----------------------------------------------------------------------
       Games after a click on the ornament behind the name (_event_game.html).

       A game's code is static/events/games/<holiday>.js, downloaded with the page
       (only the game of today's holiday), and it registers itself through
       JellyscopeGames.register(key, fn).
       The game gets a stage in the middle of the scene (a full-screen dialog)
       and an `api` with texts in the account's language and helpers. Timers and
       frames through api.after / api.frame cancel themselves when the scene
       closes - a closed scene draws nothing any more.
       ----------------------------------------------------------------------- */
    var NS = "http://www.w3.org/2000/svg";
    var CONFETTI_COLOURS = ["#ff5252", "#ffd34d", "#4fc3f7", "#7ee08a", "#ff8fd1", "#ffffff"];
    var games = {};
    var loading = {};
    var scene = null, button = null, cleanup = [];
    var lock = null;   // styles of <html> and <body> before scrolling is locked
    // The same version as events.js (?v=...), so a new game is downloaded after a release.
    var version = document.currentScript ? document.currentScript.src.replace(/^[^?]*/, "") : "";

    var texts = {};
    try {
        var source = document.getElementById("event-texts");
        texts = source ? JSON.parse(source.textContent) : {};
    } catch (error) { texts = {}; }

    // First the scene detaches (api.after/frame start nothing any more), then the cleanup.
    // Older browsers have no getAnimations - closing must work there too.
    function close() {
        if (!scene) { return; }
        var old = scene, tasks = cleanup;
        scene = null;
        cleanup = [];
        tasks.forEach(function (fn) { fn(); });
        if (old.getAnimations) { old.getAnimations({ subtree: true }).forEach(function (a) { a.cancel(); }); }
        old.remove();
        unlockScroll();
        if (button && document.contains(button)) { button.focus(); }
        button = null;
    }

    function confetti(stage, x, y, howMany) {
        if (withoutMotion()) { return; }
        for (var i = 0; i < howMany; i++) {
            var k = document.createElement("span");
            k.className = "event-confetti";
            k.style.left = x + "px";
            k.style.top = y + "px";
            k.style.background = CONFETTI_COLOURS[i % CONFETTI_COLOURS.length];
            stage.appendChild(k);
            var angle = -Math.PI / 2 + (Math.random() - 0.5) * 2.2, speed = random(120, 280);
            var dx = Math.cos(angle) * speed, dy = Math.sin(angle) * speed;
            k.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                       { transform: "translate(" + dx + "px," + (dy + 40) + "px) rotate(" + random(0, 540) + "deg)", opacity: 1, offset: 0.6 },
                       { transform: "translate(" + (dx * 1.2) + "px," + (dy + 220) + "px) rotate(" + random(0, 900) + "deg)", opacity: 0 }],
                      { duration: random(1800, 2400), easing: "cubic-bezier(.2,.7,.4,1)", fill: "forwards" })
                .onfinish = (function (el) { return function () { el.remove(); }; })(k);
        }
    }

    function svg(tag, attributes, parent) {
        var el = document.createElementNS(NS, tag);
        Object.keys(attributes || {}).forEach(function (k) { el.setAttribute(k, attributes[k]); });
        if (parent) { parent.appendChild(el); }
        return el;
    }
    // The pointer's point (mouse, finger) in the coordinates of the SVG canvas.
    function pointInSvg(canvas, e) {
        var p = canvas.createSVGPoint();
        p.x = e.clientX;
        p.y = e.clientY;
        return p.matrixTransform(canvas.getScreenCTM().inverse());
    }

    // The page under an open scene does not scroll (neither the wheel nor a finger off the canvas).
    // A margin replaces the vanished scrollbar, so the content does not jump.
    function lockScroll() {
        var html = document.documentElement, body = document.body;
        var scrollbar = window.innerWidth - html.clientWidth;
        lock = { overflow: html.style.overflow, padding: body.style.paddingRight };
        html.style.overflow = "hidden";
        if (scrollbar > 0) { body.style.paddingRight = scrollbar + "px"; }
    }
    function unlockScroll() {
        if (!lock) { return; }
        document.documentElement.style.overflow = lock.overflow;
        document.body.style.paddingRight = lock.padding;
        lock = null;
    }

    // Elements in the scene that Tab can reach.
    function focusable() {
        return Array.prototype.filter.call(
            scene.querySelectorAll('button, [href], input, [tabindex]:not([tabindex="-1"])'),
            function (el) { return !el.disabled && el.getClientRects().length > 0; });
    }

    function open(key, opener) {
        close();
        button = opener;
        scene = document.createElement("div");
        scene.className = "event-scene";
        scene.setAttribute("role", "dialog");
        scene.setAttribute("aria-modal", "true");
        scene.setAttribute("aria-label", texts.ornament || "");
        scene.innerHTML = '<div class="event-scene-backdrop"></div><div class="event-stage">' +
            '<button class="event-close" type="button">×</button></div>';
        var closeButton = scene.querySelector(".event-close");
        closeButton.setAttribute("aria-label", texts.close || "×");
        closeButton.addEventListener("click", close);
        // The second click of a double click on the ornament lands on the backdrop -
        // that one does not close the scene.
        var openedAt = Date.now();
        scene.querySelector(".event-scene-backdrop").addEventListener("click", function () {
            if (Date.now() - openedAt > 400) { close(); }
        });
        // The stage can be focused by script (not by Tab): a game moves focus to it
        // when a part of it stops being operable - an extra Enter then closes nothing.
        var stage = scene.querySelector(".event-stage");
        stage.setAttribute("tabindex", "-1");
        lockScroll();
        document.body.appendChild(scene);

        var own = scene, countdowns = [], frames = [];
        cleanup.push(function () {
            countdowns.forEach(clearTimeout);
            frames.forEach(cancelAnimationFrame);
        });
        var api = {
            texts: texts,
            quiet: withoutMotion,
            confetti: confetti,
            // Closes only this scene - a late call does not close a new one.
            close: function () { if (scene === own) { close(); } },
            svg: svg,
            pointInSvg: pointInSvg,
            after: function (ms, fn) {
                countdowns.push(setTimeout(function () { if (scene === own) { fn(); } }, ms));
            },
            frame: function (fn) {
                frames.push(requestAnimationFrame(function (time) { if (scene === own) { fn(time); } }));
            }
        };
        try {
            games[key](stage, api);
        } catch (error) {
            // A crashed game must not leave an invisible layer over the page.
            close();
            return;
        }
        requestAnimationFrame(function () { if (scene === own) { own.classList.add("expanded"); } });
        closeButton.focus();
    }

    // Esc closes, Tab and Shift+Tab cycle only within the scene.
    document.addEventListener("keydown", function (e) {
        if (!scene) { return; }
        if (e.key === "Escape") {
            e.preventDefault();
            close();
            return;
        }
        if (e.key !== "Tab") { return; }
        var elements = focusable();
        if (!elements.length) { return; }
        var first = elements[0], last = elements[elements.length - 1];
        var outside = !scene.contains(document.activeElement);
        if (e.shiftKey && (outside || document.activeElement === first)) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && (outside || document.activeElement === last)) {
            e.preventDefault();
            first.focus();
        }
    });

    // A game's code is downloaded with the page (_event_head.html). When it did
    // not load then (offline, 404), it is tried again on the click; if that fails,
    // nothing opens and the next click tries again. Only the game of today's holiday
    // (the key goes into the script's address) and only with texts - without them the game
    // would write "undefined".
    function launch(key, opener) {
        if (key !== holiday || !texts.ornament) { return; }
        if (games[key]) {
            open(key, opener);
            return;
        }
        if (loading[key]) { return; }
        loading[key] = true;
        var script = document.createElement("script");
        script.src = "/static/events/games/" + key + ".js" + version;
        script.onload = function () {
            loading[key] = false;
            if (games[key]) { open(key, opener); } else { script.remove(); }
        };
        script.onerror = function () {
            loading[key] = false;
            script.remove();
        };
        document.head.appendChild(script);
    }

    window.JellyscopeGames = {
        register: function (key, fn) { games[key] = fn; }
    };
    document.querySelectorAll(".event-game[data-game]").forEach(function (el) {
        el.addEventListener("click", function () { launch(el.getAttribute("data-game"), el); });
    });

    release();
})();
