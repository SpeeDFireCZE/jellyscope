/* The map on the Network page and on the overview card.

   It zooms by clicking or with the wheel, dragging pans it. Which of the two
   zooms is chosen by the admin in Settings → Interface and the server sends it
   in data-zoom. The wheel is handier, but the page stops scrolling over the
   map - somebody just scrolling past gets stuck on it. That is why clicking is
   the default: the map then does not catch the wheel at all.

   Only the viewBox of the SVG changes. The server computed the clusters for
   zoom levels 1×, 2×, 4× … (map_clusters.py, the list in data-levels); every dot
   carries the levels it is valid for (data-from, data-to). The script shows
   the dots of the level that belongs to the zoom: from afar they merge,
   up close they break apart down to single places.

   Coordinates are Web Mercator, the world is a 360 × 360 square (data-world);
   the default view (data-default) leaves out the poles. How far it can zoom
   in is given by data-max. */
(function () {
    "use strict";

    var map = document.querySelector(".map");
    if (!map) { return; }

    function numbers(text, fallback) {
        return (text || fallback).split(" ").map(Number);
    }

    var world = numbers(map.dataset.world, "0 0 360 360");
    var initial = numbers(map.dataset.default, "0 0 360 360");
    var maxZoom = Number(map.dataset.max) || 20;
    var view = initial.slice();
    var dragging = null;

    /* Dots and lines have to SHRINK in map coordinates when zooming in,
       otherwise they just grow with it and two towns next to each other stay
       merged into one blot. The radius is therefore recomputed from how much
       smaller the view is than the default one - on screen it stays the same. */
    var dots = Array.prototype.slice.call(map.querySelectorAll(".map-point"));
    var seconds = dots.map(function (dot) { return Number(dot.dataset.s) || 0; });
    var maxima = numbers(map.dataset.maxima, "1");
    var level = 0;   /* index of the visible level in data-levels */
    var borders = map.querySelector(".map-borders");
    var levels = numbers(map.dataset.levels, "1");

    /* Without the script only the dots of level 1× show (the others have
       display="none"). With the script they cross-fade through opacity - display
       cannot be animated. See .map.js-levels in style.css. */
    dots.forEach(function (dot) { dot.removeAttribute("display"); });
    map.classList.add("js-levels");

    /* Underlays (OSM tiles, the detailed map) register here and get
       every change of the view. */
    var afterChange = [];

    function currentZoom() {
        return world[2] / view[2];
    }

    /* The level = the largest one not above the zoom: at 3× it is
       2×, at 600× 512×. The dots valid on it are visible. */
    function chooseLevel() {
        var z = currentZoom() + 1e-9;
        level = 0;
        levels.forEach(function (value, order) {
            if (value <= z) { level = order; }
        });
        var value = levels[level];
        dots.forEach(function (dot) {
            dot.classList.toggle("on", Number(dot.dataset.from) <= value &&
                                              value <= Number(dot.dataset.to));
        });
    }

    function draw() {
        map.setAttribute("viewBox", view.join(" "));
        var ratio = view[2] / initial[2];
        chooseLevel();
        /* Size within the visible level: the biggest cluster on it has full
           size, the others by the square root of the time (the eye compares areas).
           The same formula as charts.world_map. */
        var most = maxima[level] || 1;
        dots.forEach(function (dot, i) {
            var radius = (1.2 + Math.sqrt(Math.min(1, seconds[i] / most)) * 4) * 2.2;
            dot.setAttribute("r", (radius * ratio).toPrecision(4));
        });
        if (borders) {
            borders.setAttribute("stroke-width", (0.16 * ratio).toPrecision(4));
            /* Borders do NOT switch on in one jump: seen over the whole world they
               would be just a net of lines across the continents, but once a
               person starts zooming in, they are to appear gradually. */
            var strength = Math.min(1, Math.max(0, (1 - ratio) * 1.6));
            borders.setAttribute("stroke-opacity", strength.toFixed(3));
        }
        map.classList.toggle("is-zoomed", view[2] < initial[2] - 0.5);
        afterChange.forEach(function (listener) { listener(view, currentZoom()); });
    }

    /* Keep the view from leaving the world - otherwise a person ends up at an
       empty area and does not know the way back. */
    function clamp() {
        view[0] = Math.min(Math.max(view[0], world[0]), world[0] + world[2] - view[2]);
        view[1] = Math.min(Math.max(view[1], world[1]), world[1] + world[3] - view[3]);
    }

    /* To keep the point under the cursor in place while zooming, the mouse
       position is computed in map coordinates - not in pixels. */
    function pointUnderMouse(event) {
        var box = map.getBoundingClientRect();
        return {
            x: view[0] + (event.clientX - box.left) / box.width * view[2],
            y: view[1] + (event.clientY - box.top) / box.height * view[3]
        };
    }

    /* One zoom, wherever it comes from. A `step` below 1 zooms in,
       above 1 zooms out; `center` is the point in map coordinates that is
       to stay in place. It cannot zoom out past the default view, and zooms
       in up to data-max. */
    function zoom(step, center) {
        center = center || { x: view[0] + view[2] / 2,
                           y: view[1] + view[3] / 2 };
        var width = Math.min(initial[2],
                             Math.max(world[2] / maxZoom, view[2] * step));
        var ratio = width / view[2];

        view[2] = width;
        view[3] = view[3] * ratio;
        view[0] = center.x - (center.x - view[0]) * ratio;
        view[1] = center.y - (center.y - view[1]) * ratio;
        clamp();
        draw();
    }

    function wholeWorld() {
        view = initial.slice();
        draw();
    }

    /* The wheel is caught ONLY in the "wheel" mode. Otherwise nothing
       listens to it at all, so the page keeps scrolling over the map - that is
       the whole point of the option. */
    if (map.dataset.zoom === "wheel") {
        map.addEventListener("wheel", function (event) {
            event.preventDefault();
            zoom(event.deltaY < 0 ? 0.8 : 1.25, pointUnderMouse(event));
        }, { passive: false });
    }

    map.addEventListener("pointerdown", function (event) {
        dragging = { x: event.clientX, y: event.clientY,
                  vx: view[0], vy: view[1], moved: false };
        map.setPointerCapture(event.pointerId);
    });

    map.addEventListener("pointermove", function (event) {
        if (!dragging) { return; }
        var box = map.getBoundingClientRect();
        /* A move of a few pixels is not a drag yet - neither a mouse nor a finger
           holds perfectly still while clicking. Without this tolerance the click
           mode would hardly ever zoom. */
        if (Math.abs(event.clientX - dragging.x) > 4 ||
            Math.abs(event.clientY - dragging.y) > 4) {
            dragging.moved = true;
        }
        view[0] = dragging.vx - (event.clientX - dragging.x) / box.width * view[2];
        view[1] = dragging.vy - (event.clientY - dragging.y) / box.height * view[3];
        clamp();
        draw();
    });

    function release(event) {
        if (!dragging) { return; }
        /* A click = press and release without a drag. It zooms ×2 to the
           clicked place - so a click on a cluster splits it right away. With Alt
           (or the right button) it zooms out. */
        if (!dragging.moved && map.dataset.zoom !== "wheel") {
            zoom(event.altKey || event.button === 2 ? 2 : 0.5, pointUnderMouse(event));
        }
        dragging = null;
        if (map.hasPointerCapture(event.pointerId)) {
            map.releasePointerCapture(event.pointerId);
        }
    }
    map.addEventListener("pointerup", release);
    map.addEventListener("pointercancel", release);

    /* A double click returns the default view - the only way back a person
       guesses without anybody explaining it. */
    map.addEventListener("dblclick", wholeWorld);

    /* The buttons above the map. They are there in both modes: whoever does not
       want to click into the map (and aim) has plus and minus. A step of ×2, so
       the nearest place is not far: from 1× to 1024× it is ten clicks. */
    var wrapper = map.closest(".map-wrapper");
    if (wrapper) {
        wrapper.addEventListener("click", function (event) {
            var button = event.target.closest("[data-map]");
            if (!button) { return; }
            if (button.dataset.map === "back") {
                wholeWorld();
            } else {
                zoom(button.dataset.map === "closer" ? 0.5 : 2);
            }
        });
    }

    /* The detailed offline map (Natural Earth 1:10M, downloaded on the Network
       page). From 8× on, tiles from the server replace the built-in outline:
       detail 1 (a 32 × 32 grid, simplified lines) up to 127×, detail 2
       (a 128 × 128 grid) from 128×. Only the tiles of the view load; the browser
       keeps them in its cache and the version in the address (?v=) makes sure
       it does not take the old ones after the map is downloaded again. With OSM
       on they do not load at all.

       Transitions without a hole: tiles of the other detail stay until all the
       new ones in the view have loaded, and while they load, the coarse outline
       shows under them - otherwise only the sea would be left for a moment. */
    var detailedLayer = map.querySelector(".map-detailed");
    var base = map.querySelector(".map-base");
    if (map.dataset.detailed === "1" && map.dataset.basemap !== "osm" && detailedLayer) {
        /* The server sends the grid size of each detail (detailed_map). */
        var grids = numbers(map.dataset.grids, "32 128");
        var GRIDS = { 1: grids[0], 2: grids[1] };
        /* Order from the bottom - a lake lies on land, a river over a town. */
        var LAYERS = ["land", "lakes", "cities", "rivers", "regions", "borders"];
        var version = encodeURIComponent(map.dataset.detailedVersion || "");
        /* key "p/x/y" -> {g: <g> or null, loaded: bool, arrived: bool} */
        var detailed = {};
        var wantDetailed = {};
        var SVG_NS = "http://www.w3.org/2000/svg";

        function remove(key) {
            if (detailed[key].g) { detailedLayer.removeChild(detailed[key].g); }
            delete detailed[key];
        }

        /* Once the whole view has loaded, away with the tiles of the other detail.
           The coarse outline hides only when at least one tile really
           arrived - when all of them fail (an expired sign-in, a deleted map),
           it stays visible, not a bare sea. */
        function finish() {
            var keys = Object.keys(wantDetailed);
            var done = keys.every(function (key) {
                return detailed[key] && detailed[key].loaded;
            });
            var arrived = keys.some(function (key) {
                return detailed[key] && detailed[key].arrived;
            });
            if (done) {
                Object.keys(detailed).forEach(function (key) {
                    if (!wantDetailed[key]) { remove(key); }
                });
            }
            if (base) { base.style.display = keys.length && done && arrived ? "none" : ""; }
            if (borders) { borders.style.display = keys.length && arrived ? "none" : ""; }
        }

        /* A response belongs only to the record that asked for it. When a tile
           left the view in the meantime and came back (a new record, a new request),
           the old response is dropped - otherwise a group would stay in the map
           that nobody knows about and nobody removes. */
        function insertTile(key, entry, data) {
            if (detailed[key] !== entry) { return; }
            if (data) {
                var group = document.createElementNS(SVG_NS, "g");
                LAYERS.forEach(function (layer) {
                    if (!data[layer]) { return; }
                    var path = document.createElementNS(SVG_NS, "path");
                    path.setAttribute("class", "v-" + layer);
                    path.setAttribute("d", data[layer]);
                    group.appendChild(path);
                });
                detailedLayer.appendChild(group);
                entry.g = group;
                entry.arrived = true;
            }
            entry.loaded = true;
            finish();
        }

        afterChange.push(function (frame, z) {
            var detail = z >= 128 ? 2 : (z >= 8 ? 1 : 0);
            wantDetailed = {};
            if (detail) {
                var n = GRIDS[detail];
                var size = world[2] / n;
                var x0 = Math.max(0, Math.floor(frame[0] / size));
                var x1 = Math.min(n - 1, Math.floor((frame[0] + frame[2]) / size));
                var y0 = Math.max(0, Math.floor(frame[1] / size));
                var y1 = Math.min(n - 1, Math.floor((frame[1] + frame[3]) / size));
                for (var x = x0; x <= x1; x++) {
                    for (var y = y0; y <= y1; y++) {
                        wantDetailed[detail + "/" + x + "/" + y] = true;
                    }
                }
            }
            /* Outside the view and of the same detail: gone at once; another detail
               waits until the new one is complete (finish). */
            Object.keys(detailed).forEach(function (key) {
                if (wantDetailed[key]) { return; }
                if (!detail || key.charAt(0) === String(detail)) { remove(key); }
            });
            Object.keys(wantDetailed).forEach(function (key) {
                if (detailed[key]) { return; }
                var entry = { g: null, loaded: false, arrived: false };
                detailed[key] = entry;
                fetch("/network/map/tiles/" + key + "?v=" + version, { credentials: "same-origin" })
                    .then(function (response) { return response.ok ? response.json() : null; })
                    /* Even a failed tile is "settled", so nothing waits forever. */
                    .then(function (data) { insertTile(key, entry, data); })
                    .catch(function () { insertTile(key, entry, null); });
            });
            finish();
        });
    }

    /* The OpenStreetMap underlay (Settings → Interface, off by default).

       In Mercator the world is a 360 × 360 square - the same one OSM tiles
       split into 2^z × 2^z pieces, so tile (z, x, y) lies exactly on the
       units x·360/2^z … (x+1)·360/2^z. The tile level is chosen so that
       one tile is roughly 256 px on screen. Only the tiles of the view are
       fetched, with a margin of one tile all around. Tiles of another level
       stay until the new ones in the view have loaded - otherwise the drawn
       map would flash at every zoom.

       referrerpolicy: OSM wants to know where a request comes from (Referer),
       while the app otherwise sends none out. "strict-origin" sends only the
       server's address, not the page (and for browsers that do not know the
       attribute on SVG the page's header says the same - see csp() in web/base.py).
       The attribute is set before the address. The drawn map stays under the
       tiles: until they load, or when there is no internet, it is what shows. */
    var osmLayer = map.querySelector(".map-tile");
    if (map.dataset.basemap === "osm" && osmLayer) {
        var tiles = {};   /* key "z/x/y" -> {img, z, done} */
        var wantOsm = {};
        var SVG = "http://www.w3.org/2000/svg";

        function clearOsm() {
            var done = Object.keys(wantOsm).every(function (key) {
                return tiles[key] && tiles[key].done;
            });
            if (!done) { return; }
            Object.keys(tiles).forEach(function (key) {
                if (!wantOsm[key]) {
                    osmLayer.removeChild(tiles[key].img);
                    delete tiles[key];
                }
            });
        }

        afterChange.push(function (frame) {
            var box = map.getBoundingClientRect();
            if (!box.width) { return; }   /* a hidden card - ResizeObserver repeats it */
            var z = Math.round(Math.log(world[2] * box.width / frame[2] / 256) / Math.LN2);
            z = Math.max(0, Math.min(19, z));
            var count = Math.pow(2, z);
            var size = world[2] / count;
            var x0 = Math.max(0, Math.floor(frame[0] / size) - 1);
            var x1 = Math.min(count - 1, Math.floor((frame[0] + frame[2]) / size) + 1);
            var y0 = Math.max(0, Math.floor(frame[1] / size) - 1);
            var y1 = Math.min(count - 1, Math.floor((frame[1] + frame[3]) / size) + 1);

            wantOsm = {};
            for (var x = x0; x <= x1; x++) {
                for (var y = y0; y <= y1; y++) {
                    var key = z + "/" + x + "/" + y;
                    wantOsm[key] = true;
                    if (tiles[key]) { continue; }
                    var image = document.createElementNS(SVG, "image");
                    image.setAttribute("referrerpolicy", "strict-origin");
                    image.setAttribute("x", x * size);
                    image.setAttribute("y", y * size);
                    /* A hair bigger, so no hairline gaps from rounding
                       shine between the tiles. */
                    image.setAttribute("width", size * 1.002);
                    image.setAttribute("height", size * 1.002);
                    image.setAttribute("preserveAspectRatio", "none");
                    tiles[key] = { img: image, z: z, done: false };
                    (function (entry) {
                        function settled() { entry.done = true; clearOsm(); }
                        entry.img.addEventListener("load", settled);
                        entry.img.addEventListener("error", settled);
                    })(tiles[key]);
                    image.setAttribute("href",
                        "https://tile.openstreetmap.org/" + key + ".png");
                    osmLayer.appendChild(image);
                }
            }
            /* The same level outside the view goes at once, another one after the new ones load. */
            Object.keys(tiles).forEach(function (key) {
                if (!wantOsm[key] && tiles[key].z === z) {
                    osmLayer.removeChild(tiles[key].img);
                    delete tiles[key];
                }
            });
            clearOsm();
        });
    }

    /* A window resize or a card that has only just shown (it was
       hidden and had width 0): recompute the tile level and the view. */
    if (window.ResizeObserver) {
        new window.ResizeObserver(function () { draw(); }).observe(map);
    } else {
        window.addEventListener("resize", draw);
    }

    draw();
})();
