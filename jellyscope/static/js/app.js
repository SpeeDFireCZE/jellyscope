// Scripts of the app's pages (everything standing on base.html). They used to be
// inline in base.html; the CSP header no longer lets inline code run (see base.js
// and tests/test_no_inline_js.py). Moved with the logic unchanged.
//
// Loaded with `defer` - it runs once the page has fully loaded. Each part
// first checks that its element is on the page: one file serves all
// pages.
//
// Texts the script shows were translated by the template into the data block
// <script type="application/json" id="texts-js"> (base.html).
//
// The whole file is one block, so nothing leaks into `window` - the parts
// inside each keep to their own. No indentation on purpose: the block is only
// a wrapper and the whole file one level deeper would read worse. What others
// are to call is put into `window` explicitly (window.rememberPosition).
(function () {
var TEXTS = JSON.parse((document.getElementById("texts-js") || {}).textContent || "{}");

/* The page's period (base.html data-period) added to an address the page asks by itself.
   Without it the server took the period from the session - and another tab may have changed
   that meanwhile: the card was drawn for a year under a switch saying a week. Shared by the
   cards switched in place and the cards that refresh by themselves. */
function withPeriod(address) {
    var period = document.body.dataset.period;
    if (!period) { return address; }
    return address + (address.indexOf("?") === -1 ? "?" : "&") + period;
}

// It disappears by itself, but not before it can be read. Errors
// stay - with those the most important thing is that a person
// notices them, and they can close them themselves.
(function () {
    var box = document.getElementById("flash-toast");
    if (!box || box.querySelector(".flash.error")) { return; }
    setTimeout(function () { box.classList.add("leaving"); }, 9000);
    setTimeout(function () { box.remove(); }, 9600);
})();

// ------------------------------------------------------------------
// Demo: a message on the spot instead of submitting the form
//
// Signing in and out are the exception - without them one could
// neither get into the demo nor out of it.
// ------------------------------------------------------------------
(function () {
    if (document.body.dataset.demo !== "1") { return; }
    var ALLOWED = ["/login", "/logout"];
    var flash = document.getElementById("demo-flash");
    var timer = null;

    document.addEventListener("submit", function (event) {
        var form = event.target;
        var whereTo = (form.getAttribute("action") || "").split("?")[0];
        // Only what writes is stopped.
        //
        //   method="dialog"  closes the window, sends nothing anywhere,
        //   method="get"     is a filter - changes the address, not data
        //                    (the statistics' period, the history filter),
        //   ALLOWED          signing in and out.
        //
        // The server guards it the same way: the middleware `demo_mode_middleware` lets
        // GET and HEAD through without asking.
        if (form.method === "dialog" || form.method === "get"
                || ALLOWED.indexOf(whereTo) !== -1) { return; }

        event.preventDefault();
        // Into the open window, when the form is in one: a window is in the top layer and the message
        // on the page would stay under its dimmed backdrop (the same trap as with the value tooltip).
        (form.closest("dialog[open]") || document.body).appendChild(flash);
        flash.textContent = TEXTS.demo;
        flash.classList.add("shown");
        clearTimeout(timer);
        timer = setTimeout(function () { flash.classList.remove("shown"); }, 4500);
    });
})();

// ------------------------------------------------------------------
// A calendar for a typed date
//
// Our own, not <input type="date">: that one looks different in every
// browser and sticks out in the middle of a matched window. Typing a date
// still works - this is only a suggester that opens after a click into the field.
//
// The browser takes the names of months and days from the page's language
// (Intl), so they need no translation and are declined correctly.
// ------------------------------------------------------------------
(function () {
    var field = null;      // the input the calendar belongs to right now
    var panel = null;
    var monthIndex = null;     // the first day of the month shown

    function language() {
        return document.documentElement.getAttribute("lang") || "cs";
    }

    function monthName(date) {
        return new Intl.DateTimeFormat(language(), {
            month: "long", year: "numeric"
        }).format(date);
    }

    function dayAbbreviations() {
        // 2024-01-01 was a Monday - seven days forward from it.
        var formatter = new Intl.DateTimeFormat(language(), { weekday: "short" });
        var days = [];
        for (var i = 0; i < 7; i++) {
            days.push(formatter.format(new Date(2024, 0, 1 + i)));
        }
        return days;
    }

    function readDate(text) {
        var match = /^\s*(\d{1,2})\s*[.\-\/]\s*(\d{1,2})\s*[.\-\/]\s*(\d{4})\s*$/.exec(text || "");
        if (match) {
            return new Date(+match[3], +match[2] - 1, +match[1]);
        }
        match = /^\s*(\d{4})-(\d{2})-(\d{2})\s*$/.exec(text || "");
        return match ? new Date(+match[1], +match[2] - 1, +match[3]) : null;
    }

    function writeDate(date) {
        return date.getDate() + "." + (date.getMonth() + 1) + "."
             + date.getFullYear();
    }

    function sameDay(a, b) {
        return a && b && a.getFullYear() === b.getFullYear()
            && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    }

    function close() {
        if (panel) { panel.remove(); panel = null; }
        field = null;
    }

    function render() {
        panel.innerHTML = "";

        var head = document.createElement("div");
        head.className = "calendar-head";
        var back = document.createElement("button");
        back.type = "button";
        back.className = "calendar-arrow";
        back.textContent = "\u2039";
        back.addEventListener("click", function () {
            monthIndex = new Date(monthIndex.getFullYear(), monthIndex.getMonth() - 1, 1);
            render();
        });
        var title = document.createElement("span");
        title.className = "calendar-month";
        title.textContent = monthName(monthIndex);
        var forward = document.createElement("button");
        forward.type = "button";
        forward.className = "calendar-arrow";
        forward.textContent = "\u203a";
        forward.addEventListener("click", function () {
            monthIndex = new Date(monthIndex.getFullYear(), monthIndex.getMonth() + 1, 1);
            render();
        });
        head.append(back, title, forward);
        panel.appendChild(head);

        var grid = document.createElement("div");
        grid.className = "calendar-grid";
        dayAbbreviations().forEach(function (abbreviation) {
            var cell = document.createElement("span");
            cell.className = "calendar-weekday";
            cell.textContent = abbreviation;
            grid.appendChild(cell);
        });

        // The week starts on Monday; getDay() has Sunday as zero.
        var first = new Date(monthIndex.getFullYear(), monthIndex.getMonth(), 1);
        var offsetBy = (first.getDay() + 6) % 7;
        var today = new Date();
        var selected = readDate(field.value);

        for (var i = 0; i < offsetBy; i++) {
            grid.appendChild(document.createElement("span"));
        }

        var count = new Date(monthIndex.getFullYear(), monthIndex.getMonth() + 1, 0).getDate();
        for (var day = 1; day <= count; day++) {
            (function (day) {
                var date = new Date(monthIndex.getFullYear(), monthIndex.getMonth(), day);
                var button = document.createElement("button");
                button.type = "button";
                button.className = "calendar-day";
                button.textContent = day;
                if (sameDay(date, today)) { button.classList.add("today"); }
                if (sameDay(date, selected)) { button.classList.add("selected"); }
                // Statistics from the future are empty, so we do not go there.
                if (date > today) { button.disabled = true; }
                button.addEventListener("click", function () {
                    field.value = writeDate(date);
                    field.dispatchEvent(new Event("change", { bubbles: true }));
                    close();
                });
                grid.appendChild(button);
            })(day);
        }
        panel.appendChild(grid);
        place();
    }

    // The panel is `position: fixed` (style.css): under the field, above it when there is no room
    // below, always inside the screen. A window that scrolls (overflow) would clip a panel placed
    // inside it - the calendar of the last date field on a phone was cut off or out of reach.
    function place() {
        var margin = 8;
        var box = field.getBoundingClientRect();
        var height = panel.offsetHeight;
        var top = box.bottom + 6;
        if (top + height > window.innerHeight - margin && box.top - 6 - height >= margin) {
            top = box.top - 6 - height;
        }
        top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));
        panel.style.top = top + "px";
        panel.style.left = Math.max(margin, Math.min(box.left, window.innerWidth - panel.offsetWidth - margin)) + "px";
    }

    function open(input) {
        close();
        field = input;
        var selected = readDate(field.value) || new Date();
        monthIndex = new Date(selected.getFullYear(), selected.getMonth(), 1);

        panel = document.createElement("div");
        panel.className = "calendar";
        // Into the field's parent, not into <body>: a window (<dialog>) is drawn
        // in the top layer and anything attached to body would hide
        // under it - the same trap as with the value tooltip.
        var wrapper = field.closest(".field") || field.parentElement;
        wrapper.appendChild(panel);
        render();
    }

    // The field moves when its window or the page scrolls (or the screen turns) - the panel goes with it.
    // In the capture phase: a scroll of an element (the window) does not bubble.
    document.addEventListener("scroll", function () { if (panel) { place(); } }, true);
    window.addEventListener("resize", function () { if (panel) { place(); } });

    // We catch it in the capture phase (`true`), not while bubbling.
    //
    // The month arrows redraw the panel's content, so before the click
    // bubbled up here, the button that was clicked is no longer in the
    // document - and `closest(".calendar")` on a detached node returns null.
    // The calendar took that for a click outside itself and closed:
    // the months could not be switched at all.
    document.addEventListener("click", function (event) {
        var input = event.target.closest("input[data-calendar]");
        if (input) {
            if (input !== field) { open(input); }
            return;
        }
        if (panel && !event.target.closest(".calendar")) { close(); }
    }, true);

    // preventDefault: the same Escape would also close the window around the field
    // (and the dates typed into it would be lost) - it closes only the calendar.
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && panel) {
            event.preventDefault();
            close();
        }
    });
})();

// ------------------------------------------------------------------
// Quick choices in a custom period
//
// They fill the fields but do not submit the form - so it is visible what was
// chosen before it is clicked. Without JavaScript the fields stay for a
// hand-typed date, so nothing is lost.
// ------------------------------------------------------------------
document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-period-since]");
    if (!button) { return; }
    var form = button.closest("form");
    if (!form) { return; }
    // Fields are found by data-field, not by name: on the Comparison both
    // periods have their own prefix (a_since, b_since), so they do not overwrite each
    // other in the address, and a search by name would find nothing there.
    var sinceField = form.querySelector('[data-field="since"]');
    var untilField = form.querySelector('[data-field="until"]');
    if (!sinceField || !untilField) { return; }
    sinceField.value = button.dataset.periodSince;
    untilField.value = button.dataset.periodUntil;
});

// ------------------------------------------------------------------
// The value tooltip
//
// Instead of every bar having its own listener, we listen on the whole
// document and on an event ask what the mouse is over. This is called
// event delegation: one listener instead of a thousand, so a page
// with a big table does not slow down.
// ------------------------------------------------------------------
(function () {
    var tip = document.getElementById("tooltip");
    var current = null;

    function place(event) {
        var offset = 14;
        var margin = 8;
        var x = event.clientX + offset;
        var y = event.clientY + offset;

        // Keep the tooltip from leaving the window: when it does not fit on the right
        // (below), it goes to the other side of the cursor.
        var box = tip.getBoundingClientRect();
        if (x + box.width > window.innerWidth - margin) {
            x = event.clientX - box.width - offset;
        }
        if (y + box.height > window.innerHeight - margin) {
            y = event.clientY - box.height - offset;
        }
        /* On a phone it often fits on neither side - the tooltip
           is almost as wide as the display and the tap tends to be in the middle.
           Flipping then sent it to negative coordinates, half the
           text vanished behind the left edge. So in the end we always
           push it inside the window; better to cover the finger than
           to cut the text. */
        x = Math.max(margin, Math.min(x, window.innerWidth - box.width - margin));
        y = Math.max(margin, Math.min(y, window.innerHeight - box.height - margin));
        tip.style.left = x + "px";
        tip.style.top = y + "px";
    }

    /* The tooltip's content.

       Charts send values in `data-tip-json`: a heading (the day) and rows,
       each with the colour of its series. It is built from nodes and text
       through textContent - so markup from a film's name can never get into
       the tooltip, even if there were some in it.

       Everything else (bars, the heat map, the transcode badge) has
       only `data-tip`. When it holds several values separated by " · ",
       they break into rows - that reads better than one long sentence
       across the whole screen. */
    function fillTooltip(target) {
        tip.textContent = "";

        var data = null;
        try {
            data = JSON.parse(target.getAttribute("data-tip-json") || "null");
        } catch (e) {
            data = null;   /* broken JSON is no reason to show nothing */
        }

        var heading = data ? data.heading : null;
        var rows = data ? data.rows : (target.getAttribute("data-tip") || "")
            .split(" · ").map(function (text) { return { text: text }; });

        if (heading) {
            var h = document.createElement("div");
            h.className = "tooltip-heading";
            h.textContent = heading;
            tip.appendChild(h);
        }
        rows.forEach(function (row) {
            if (!row || !row.text) { return; }
            var r = document.createElement("div");
            r.className = "tooltip-row";
            if (row.color) { r.style.color = row.color; }
            r.textContent = row.text;
            tip.appendChild(r);
        });
    }

    document.addEventListener("mousemove", function (event) {
        var target = event.target.closest ? event.target.closest("[data-tip]") : null;

        if (!target) {
            if (current) {
                current = null;
                tip.classList.remove("visible");
            }
            return;
        }

        if (target !== current) {
            current = target;
            fillTooltip(target);
            tip.classList.add("visible");
        }

        /* A window (<dialog>) is drawn in the so-called top layer, which is
           above everything else - a tooltip attached to <body> hides
           under it, and in the window one could not tell what is what. So we move
           it into that window, and back on leaving. */
        var modal = target.closest("dialog[open]");
        var whereTo = modal || document.body;
        if (tip.parentElement !== whereTo) { whereTo.appendChild(tip); }
        place(event);
    }, { passive: true });

    // When the page scrolls or the mouse leaves the window, the tooltip has to go -
    // otherwise it would stay hanging over nothing.
    document.addEventListener("scroll", function () {
        current = null;
        tip.classList.remove("visible");
    }, { passive: true });

    document.addEventListener("mouseleave", function () {
        current = null;
        tip.classList.remove("visible");
    });
})();

// ------------------------------------------------------------------
// Database settings: show only the part that applies
//
// The server draws the initial state (class is-sqlite / is-postgres on
// the form), so nothing flashes on load and without JavaScript
// the right half is visible. This code only reacts to a switch.
//
// Hidden fields are still submitted - that is intended. When you switch to
// SQLite and save, the PostgreSQL settings are not deleted and are there when
// you come back.
// ------------------------------------------------------------------
(function () {
    var form = document.querySelector("[data-db-form]");
    if (!form) { return; }

    form.addEventListener("change", function (event) {
        if (!event.target.matches('input[name="kind"]')) { return; }
        var postgres = event.target.value === "postgres";
        form.classList.toggle("is-postgres", postgres);
        form.classList.toggle("is-sqlite", !postgres);
    });
})();


// ------------------------------------------------------------------
// Keeping the position on the page when the period changes
//
// The 7 / 30 / 90 days filter changes numbers across the whole page, so it loads
// again - and on a new load the browser always jumps to the top.
// If you click the filter down by a chart, you would have to scroll back.
//
// The fix is old and simple: before leaving, remember where you are,
// and after loading go back there. The key holds the address without parameters,
// so the position of one page is not used on another.
//
// (The browser gets the comments of this file too - that is why the names
// of sections we translate elsewhere are not here.)
//
// sessionStorage, not localStorage: it holds only for this tab and vanishes
// when it closes. Remembering a page position forever makes no sense.
// ------------------------------------------------------------------
(function () {
    if (!window.sessionStorage) { return; }
    var key = "scroll:" + window.location.pathname;

    // Code that loads the page by itself (waiting for a task, refreshing
    // after it has finished) stores the position too - that is why it is a function,
    // not just the listener's body. Without that, a task started by hand would end
    // up at the top of the page even though the click remembered the position:
    // the page was loaded by the script, not by a person.
    window.rememberPosition = function () {
        try {
            sessionStorage.setItem(key, window.scrollY + ":" + Date.now());
        } catch (e) { /* a private window, storage disallowed */ }
    };

    document.addEventListener("click", function (event) {
        if (event.target.closest("[data-keep-scroll]")) {
            window.rememberPosition();
        }
    }, { passive: true });

    var saved = sessionStorage.getItem(key);
    if (saved === null) { return; }
    sessionStorage.removeItem(key);

    // The position holds only for a while. Not every click loads the page again
    // (a file download, an action that answers with another page) - and without
    // this time limit the remembered position would be used at some
    // later opening of the same page, which would look like
    // a spontaneous jump from goodness knows where.
    var parts = String(saved).split(":");
    if (parts.length === 2 && Date.now() - parseInt(parts[1], 10) > 10000) {
        return;
    }
    saved = parts[0];

    // Only after drawing has finished, otherwise the page does not have its height yet and the jump
    // would end at the end of the content instead of the right place.
    window.addEventListener("load", function () {
        requestAnimationFrame(function () {
            window.scrollTo(0, parseInt(saved, 10) || 0);
        });
    });
})();

// ------------------------------------------------------------------
// The custom dashboard's layout
//
// It is put together in a window, on a grid of tiles - not by dragging the real
// panels around the page. A panel with a chart is over half a screen tall,
// so while dragging it one could not see where it was heading, and the whole
// layout did not fit on one screen. On the grid it is all visible at once.
//
// While dragging, the tiles do NOT move: only a stripe shows where
// the tile will land. While the order changed during the drag,
// the grid rearranged itself under the cursor and the next decision flipped
// back from that changed geometry - the tiles then jumped around.
// ------------------------------------------------------------------
(function () {
    var grid = document.getElementById("dash-grid");
    if (!grid) { return; }

    var menu = document.getElementById("dash-menu");
    var formElement = document.getElementById("dash-form");
    var fieldOrder = document.getElementById("dash-order");
    var cancelButton = document.getElementById("dash-cancel");
    var modal = document.getElementById("modal-layout");
    // The widths and their labels come from the registry (cards.WIDTHS) - one
    // truth instead of two that drift apart over time.
    var MARKS = JSON.parse(grid.dataset.widths || "{}");
    var WIDTHS = Object.keys(MARKS);

    var dragged = null;       // the tile being moved
    var indicator = null;     // the stripe at the place where it will land
    var goal = null;          // the tile it will be inserted before
    var spots = [];          // all the places it can be dropped

    function tile() {
        return Array.prototype.slice.call(
            grid.querySelectorAll(".dash-tile"));
    }

    function layoutInput() {
        return tile().map(function (d) {
            return d.dataset.card + ":" + (d.dataset.width || "full");
        }).join(",");
    }

    function refreshMenu() {
        if (!menu) { return; }
        var used = {};
        tile().forEach(function (d) { used[d.dataset.card] = true; });
        Array.prototype.forEach.call(
            menu.querySelectorAll("[data-add]"), function (b) {
                b.disabled = !!used[b.dataset.add];
            });
        grid.classList.toggle("dash-empty", tile().length === 0);
    }

    // A button of a tile. `named`: the label is its name too, not only its tooltip -
    // the width button's name is the width it shows.
    function toolButton(className, text, label, named) {
        var button = document.createElement("button");
        button.className = "btn " + className;
        button.type = "button";
        button.textContent = text;
        if (label) {
            button.title = label;
            if (named) { button.setAttribute("aria-label", label); }
        }
        return button;
    }

    // --- adding and removing --------------------------------------
    if (menu) {
        menu.addEventListener("click", function (event) {
            var button = event.target.closest("[data-add]");
            if (!button || button.disabled) { return; }
            var width = button.dataset.width || "full";
            var fresh = document.createElement("div");
            fresh.className = "dash-tile dash-" + width;
            fresh.dataset.card = button.dataset.add;
            fresh.dataset.width = width;

            var title = document.createElement("span");
            title.className = "dash-tile-name";
            // textContent, not innerHTML: the name is text, not markup.
            title.textContent =
                button.querySelector(".dash-item-name").textContent;

            // The same buttons as the server's tiles, their names included (the grid's
            // data-label-*): "×" alone is no name a screen reader can say.
            var tools = document.createElement("span");
            tools.className = "dash-tile-tools";
            var earlier = toolButton("dash-move", "←", grid.dataset.labelEarlier, true);
            earlier.dataset.move = "-1";
            var later = toolButton("dash-move", "→", grid.dataset.labelLater, true);
            later.dataset.move = "1";
            tools.appendChild(earlier);
            tools.appendChild(later);
            tools.appendChild(toolButton("dash-width", MARKS[width], grid.dataset.labelWidth, false));
            tools.appendChild(toolButton("dash-removed", "×", grid.dataset.labelRemove, true));

            fresh.appendChild(title);
            fresh.appendChild(tools);
            grid.appendChild(fresh);
            refreshMenu();
            fresh.scrollIntoView({ block: "nearest" });
        });
    }

    grid.addEventListener("click", function (event) {
        var dl = event.target.closest(".dash-tile");
        if (!dl) { return; }

        if (event.target.closest(".dash-removed")) {
            dl.remove();
            refreshMenu();
            return;
        }
        // Moving by the arrows - the way for a finger and a keyboard, which cannot drag.
        // The neighbour moves, not the tile: the pressed button stays where it is and keeps
        // its focus, so pressing it again moves the tile one more step.
        var move = event.target.closest(".dash-move");
        if (move) {
            var tiles = tile();
            var step = move.dataset.move === "-1" ? -1 : 1;
            var neighbour = tiles[tiles.indexOf(dl) + step];
            if (!neighbour) { return; }
            grid.insertBefore(neighbour, step < 0 ? dl.nextElementSibling : dl);
            return;
        }
        var toggle = event.target.closest(".dash-width");
        if (toggle) {
            var next = WIDTHS[(WIDTHS.indexOf(dl.dataset.width) + 1) % WIDTHS.length];
            WIDTHS.forEach(function (s) { dl.classList.remove("dash-" + s); });
            dl.classList.add("dash-" + next);
            dl.dataset.width = next;
            toggle.textContent = MARKS[next];
        }
    });

    // --- moving by dragging ---------------------------------------
    function computeSpots() {
        // One place before each tile and one more after the last
        // one. The geometry is taken now and does not change during the drag.
        var list = [];
        tile().forEach(function (d) {
            var r = d.getBoundingClientRect();
            list.push({ before: d, x: r.left - 5, y: r.top, height: r.height });
            list.push({ before: d.nextElementSibling, x: r.right + 5,
                          y: r.top, height: r.height });
        });
        return list;
    }

    function showTarget(spot) {
        // The places' coordinates are from the window, the marker is drawn into the
        // grid's content - and that scrolls. Without adding the scroll the stripe
        // of a scrolled grid would sit elsewhere than where the tile lands.
        var r = grid.getBoundingClientRect();
        indicator.style.left = (spot.x - r.left + grid.scrollLeft) + "px";
        indicator.style.top = (spot.y - r.top + grid.scrollTop) + "px";
        indicator.style.height = spot.height + "px";
    }

    grid.addEventListener("pointerdown", function (event) {
        if (event.button !== 0) { return; }
        if (event.target.closest("button")) { return; }
        dragged = event.target.closest(".dash-tile");
        if (!dragged) { return; }

        spots = computeSpots();
        goal = null;
        dragged.classList.add("dash-dragged");
        grid.classList.add("dash-dragging");
        indicator = document.createElement("div");
        indicator.className = "dash-target";
        grid.appendChild(indicator);
        if (grid.setPointerCapture) { grid.setPointerCapture(event.pointerId); }
        event.preventDefault();
    });

    // Scrolling during the drag changes what lies where - so the places are
    // computed again. Otherwise the stripe would point at tiles where they no longer are.
    grid.addEventListener("scroll", function () {
        if (dragged) { spots = computeSpots(); }
    });

    grid.addEventListener("pointermove", function (event) {
        if (!dragged) { return; }
        var best = null;
        var fewest = Infinity;
        spots.forEach(function (m) {
            // Dropping a tile where it already is makes no sense.
            if (m.before === dragged) { return; }
            var dx = event.clientX - m.x;
            var dy = event.clientY - (m.y + m.height / 2);
            var distance = dx * dx + dy * dy;
            if (distance < fewest) { fewest = distance; best = m; }
        });
        if (!best) { return; }
        goal = best;
        showTarget(best);
    });

    function endDrag() {
        if (!dragged) { return; }
        // Only now does the tile move - nothing moved during the drag.
        if (goal) { grid.insertBefore(dragged, goal.before || null); }
        dragged.classList.remove("dash-dragged");
        grid.classList.remove("dash-dragging");
        if (indicator && indicator.parentNode) {
            indicator.parentNode.removeChild(indicator);
        }
        dragged = null;
        indicator = null;
        goal = null;
    }

    grid.addEventListener("pointerup", endDrag);
    // A finger the browser took for scrolling (a tile has no `touch-action: none`, so the
    // grid can still be scrolled by a swipe over it): the drag is called off and nothing
    // moves - the stripe a first small move set would have dropped the tile somewhere.
    grid.addEventListener("pointercancel", function () {
        goal = null;
        endDrag();
    });

    // --- saving and closing ---------------------------------------
    if (formElement) {
        formElement.addEventListener("submit", function () {
            fieldOrder.value = layoutInput();
        });
    }
    // Close without saving - the cross, the Cancel button, Esc and a click on the
    // backdrop alike. A changed layout makes the page load again, so no half-made
    // layout stays in the grid for the next opening and the next Save: whoever closed
    // the window threw it away. Saving does not close the window, it leaves the page.
    var shownLayout = layoutInput();
    if (modal) {
        Array.prototype.forEach.call(
            modal.querySelectorAll(".dash-close"), function (button) {
                button.addEventListener("click", function () { modal.close(); });
            });
        modal.addEventListener("close", function () {
            if (layoutInput() === shownLayout) { return; }
            if (window.rememberPosition) { window.rememberPosition(); }
            window.location.reload();
        });
    }
    refreshMenu();
}());

// ------------------------------------------------------------------
// Picking a range by dragging in a chart
//
// Marking a stretch sets a custom period for the page - the same as if
// a person clicked it in the switcher at the top. A peak shows that something
// flowed; the question that comes right after is "what was it" - and the tables
// under the chart answer it as soon as they narrow to that stretch.
//
// The bounds are sent as instants (`since_ts`/`until_ts`), not as text.
// The browser does not know the app's time zone - it knows its own. So the server
// converts, see stats.period_from_instants().
// ------------------------------------------------------------------
(function () {
    var MIN_DRAG = 6;   // below this it is a click, not a drag

    var chart = null, rect = null, beginning = 0, end = 0;

    // From the mouse's pixel to a coordinate inside the viewBox. The SVG stretches
    // with the page's width, so the ratio has to be computed from the current
    // box, not from the numbers in the viewBox.
    //
    // `null` means "this chart cannot be measured". It happens: a card
    // refreshes itself and a press can hit the chart at the moment the
    // swap has just detached it from the document. A detached element reports zero
    // size - and from that a selection from the chart's edge would follow, a stretch
    // nobody pointed at.
    function xInChart(svg, pointerX) {
        if (!svg.isConnected) { return null; }
        var frame = svg.getBoundingClientRect();
        if (!frame.width) { return null; }
        var viewBoxWidth = (svg.viewBox && svg.viewBox.baseVal.width) || frame.width;
        return (pointerX - frame.left) / frame.width * viewBoxWidth;
    }

    function clamp(value, sinceField, destination) {
        return Math.min(Math.max(value, sinceField), destination);
    }

    // Where on the axis the given point lies - 0 at the left edge, 1 at the right.
    function share(svg, x) {
        var xFrom = parseFloat(svg.dataset.xFrom), xTo = parseFloat(svg.dataset.xTo);
        return (clamp(x, xFrom, xTo) - xFrom) / Math.max(1, xTo - xFrom);
    }

    function time(svg, x) {
        var timeFrom = parseFloat(svg.dataset.timeFrom), timeTo = parseFloat(svg.dataset.timeTo);
        return Math.round(timeFrom + share(svg, x) * (timeTo - timeFrom));
    }

    // A chart by days knows nothing finer than a day, so the selection is rounded
    // to days. If it sent a time, it would pretend to be more precise than it is.
    function day(svg, x) {
        var days = (svg.dataset.days || "").split(",");
        var index = Math.round(share(svg, x) * (days.length - 1));
        return days[Math.min(Math.max(index, 0), days.length - 1)];
    }

    function draw() {
        var sinceField = Math.min(beginning, end), destination = Math.max(beginning, end);
        rect.setAttribute("x", sinceField.toFixed(1));
        rect.setAttribute("width", (destination - sinceField).toFixed(1));
        // By the attribute, not `rect.hidden = false`: only an HTML element has
        // the `hidden` property. On an SVG element that would create a plain
        // property, the attribute would stay - and the selection rectangle would not
        // be visible, although everything else works.
        rect.removeAttribute("hidden");
    }

    function cleanup() {
        if (rect) {
            rect.setAttribute("hidden", "");
            rect.setAttribute("width", "0");
        }
        if (chart) { chart.classList.remove("chart-dragging"); }
        delete document.body.dataset.dragging;
        chart = null;
        rect = null;
    }

    document.addEventListener("pointerdown", function (event) {
        if (event.button !== 0) { return; }
        var svg = event.target.closest ? event.target.closest("svg[data-selection]") : null;
        if (!svg) { return; }
        rect = svg.querySelector(".chart-selection");
        if (!rect) { return; }
        var where = xInChart(svg, event.clientX);
        if (where === null) { return; }
        chart = svg;
        beginning = end = where;
        chart.classList.add("chart-dragging");
        document.body.dataset.dragging = "1";
        // So dragging goes on even when the mouse leaves the chart.
        if (svg.setPointerCapture) { svg.setPointerCapture(event.pointerId); }
        event.preventDefault();
    });

    document.addEventListener("pointermove", function (event) {
        if (!chart) { return; }
        var where = xInChart(chart, event.clientX);
        // The chart vanished under our hands - we drop the selection, so it does not get drawn
        // somewhere where nothing is any more.
        if (where === null) { cleanup(); return; }
        end = where;
        draw();
    });

    document.addEventListener("pointerup", function () {
        if (!chart) { return; }
        var svg = chart;
        var sinceField = Math.min(beginning, end), destination = Math.max(beginning, end);
        cleanup();
        // A short drag is a mistake or a plain click - let the chart
        // keep that for the tooltip.
        if (destination - sinceField < MIN_DRAG) { return; }
        var address = new URL(window.location.href);
        ["days", "since", "until", "since_ts", "until_ts"].forEach(function (name) {
            address.searchParams.delete(name);
        });
        if (svg.dataset.selection === "days") {
            var d1 = day(svg, sinceField), d2 = day(svg, destination);
            if (!d1 || !d2) { return; }
            address.searchParams.set("since", d1);
            address.searchParams.set("until", d2);
        } else {
            address.searchParams.set("since_ts", time(svg, sinceField));
            address.searchParams.set("until_ts", time(svg, destination));
        }
        // The same as with the period switcher: the new period redraws the whole
        // page and without this it would start from the top, even though the drag was
        // on a chart somewhere down below.
        if (window.rememberPosition) { window.rememberPosition(); }
        window.location.href = address.toString();
    });

    // Escape by key - the selection is cancelled and nothing happens.
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && chart) { cleanup(); }
    });
}());

// ------------------------------------------------------------------
// The "Now playing" card refreshes itself
//
// The collector asks Jellyfin every few seconds, but the page did not
// know about it - it showed the state from the moment of loading. Now it
// fetches the fragment itself at the same rhythm. Faster would make no sense:
// newer data do not exist anywhere yet.
//
// Only this one piece refreshes, not the whole page. If the whole page
// reloaded, it would scroll to the top and throw away a half-read table.
// ------------------------------------------------------------------
(function () {
    // Every element with `data-refresh` and `data-partial` refreshes itself,
    // at its own rhythm. It used to be hard-coded for "Now playing";
    // when the live flow on Network arrived, that would have made a second copy of the same -
    // and that drifts apart from the first one over time.
    //
    // And only the first such element on a page used to refresh. Since the
    // *Arr card "Downloading" there can be several at once (Sonarr and Radarr
    // next to "Now playing" on the overview), each with a different rhythm - and the
    // server decides it: the header X-Jellyscope-Refresh in the response
    // says in how many seconds to ask next (5 while downloading, otherwise
    // a minute). Only the element's content is swapped, the rest of the page stands still.
    if (!window.fetch) { return; }
    var refreshers = [];
    // After an unsuccessful response (an expired sign-in, a disconnected app, a server
    // error) the element asks at most once per this many seconds - otherwise the
    // card would ask a dead address at a five-second rhythm for as long as the page
    // is open. The first successful response restores the rhythm.
    var AFTER_FAILURE = 60;
    var HALF_TICK = 500;
    // How many network errors in a row slow the element down. One (sleep, a network switch)
    // must not stop the card for a minute - while downloading it would stand still.
    var ERRORS_IN_A_ROW = 3;
    // What can hold keyboard focus inside a refreshed element.
    var FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";

    // Swapping the content also throws away the element keyboard focus is on - whoever
    // tabbed to a button in the card would be back at the top of the page within a few
    // seconds. So before the swap it is remembered which element it
    // was: by what it does (which window it opens, where it leads - blocks
    // rearrange between refreshes) and which such one it is in the element; the others
    // by order.
    function focusPosition(box) {
        var active = document.activeElement;
        if (!active || active === box || !box.contains(active)) { return null; }
        var attr = active.getAttribute("data-modal") ? "data-modal" : (active.getAttribute("href") ? "href" : null);
        var value = attr ? active.getAttribute(attr) : null;
        var same = Array.prototype.filter.call(box.querySelectorAll(FOCUSABLE), function (e) {
            return !attr || e.getAttribute(attr) === value;
        });
        return { attribute: attr, value: value, order: same.indexOf(active) };
    }

    function restoreFocus(box, where) {
        if (!where || where.order < 0) { return; }
        var same = Array.prototype.filter.call(box.querySelectorAll(FOCUSABLE), function (e) {
            return !where.attribute || e.getAttribute(where.attribute) === where.value;
        });
        var goal = same[where.order];
        // preventScroll: the page is not to move because of the returned focus.
        if (goal && typeof goal.focus === "function") { goal.focus({ preventScroll: true }); }
    }

    function scrolls(modal) {
        return Array.prototype.map.call(modal.querySelectorAll(".modal-body"), function (e) { return e.scrollTop; });
    }

    function restoreScrolls(modal, whereTo) {
        Array.prototype.forEach.call(modal.querySelectorAll(".modal-body"), function (e, i) {
            if (whereTo[i]) { e.scrollTop = whereTo[i]; }
        });
    }

    // The filters of an open window (the User filter of the streams' window, data-filter-modal):
    // the new content comes with "everyone" chosen, so after every refresh the whole long list
    // was back. The choice is carried over - when the new list still has it - and the filter
    // applied again to the new rows.
    function filters(modal) {
        var chosen = {};
        Array.prototype.forEach.call(modal.querySelectorAll("select[data-filter-modal][id]"), function (e) {
            chosen[e.id] = e.value;
        });
        return chosen;
    }

    function restoreFilters(modal, chosen) {
        Array.prototype.forEach.call(modal.querySelectorAll("select[data-filter-modal][id]"), function (e) {
            var value = chosen[e.id];
            var known = Array.prototype.some.call(e.options, function (o) { return o.value === value; });
            if (!value || !known) { return; }
            e.value = value;
            e.dispatchEvent(new Event("change", { bubbles: true }));
        });
    }

    // The new content of an element with an open window in it. If the window were swapped
    // with the rest of the content, it would close and open again - and a screen reader
    // would announce it from the start at every refresh. When the new response has
    // it too (the same id, at the same level), the open window therefore stays the same
    // element: only its inside and everything around it are swapped. Returns whether
    // it worked; otherwise the whole content is swapped (see below).
    function swapAroundModal(box, modal, html) {
        if (!modal.id || modal.parentNode !== box) { return false; }
        var newItem = document.createElement("div");
        newItem.innerHTML = html;
        var newModal = Array.prototype.filter.call(newItem.children, function (e) {
            return e.tagName === "DIALOG" && e.id === modal.id;
        })[0];
        if (!newModal) { return false; }
        var focus = focusPosition(modal);
        var whereTo = scrolls(modal);
        var chosen = filters(modal);
        modal.innerHTML = newModal.innerHTML;
        restoreFilters(modal, chosen);
        restoreScrolls(modal, whereTo);
        restoreFocus(modal, focus);
        // After closing, the browser returns focus to the button that opened the window - but
        // the swap below has already replaced it with a new one. Focus then goes to the button
        // of the same window in the new content, otherwise it would fall to the top of the page.
        if (!modal.dataset.focusBack) {
            modal.dataset.focusBack = "1";
            modal.addEventListener("close", function () {
                var active = document.activeElement;
                if (active && active !== document.body && document.contains(active)) { return; }
                var trigger = Array.prototype.filter.call(box.querySelectorAll("[data-modal]"), function (e) {
                    return e.getAttribute("data-modal") === modal.id;
                })[0];
                if (trigger) { trigger.focus({ preventScroll: true }); }
            });
        }
        Array.prototype.slice.call(box.childNodes).forEach(function (node) {
            if (node !== modal) { box.removeChild(node); }
        });
        var before = true;
        Array.prototype.slice.call(newItem.childNodes).forEach(function (node) {
            if (node === newModal) { before = false; return; }
            if (before) { box.insertBefore(node, modal); } else { box.appendChild(node); }
        });
        return true;
    }

    // The new content of a refreshing element (data-refresh, data-partial).
    function swapBox(box, html) {
        if (!html) { return; }
        /* The response could arrive in the middle of a drag in a chart -
           the guard of the asking only keeps the request from starting,
           not the one already sent from arriving. The swap would
           pull the chart out from under the hand and the marked stretch would vanish;
           so it waits for the next round. */
        if (document.body.dataset.dragging) { return; }
        /* If the content were swapped with an open window, the window would
           vanish in the middle of reading - and again at every refresh.
           So the window stays (swapAroundModal). Where that is not possible
           (the window is deeper in the content), we remember that it was
           open and open it again after the swap. When the new
           response does not have it (all of a series' downloads finished or
           somebody cancelled them), it goes with the old one - there is nothing
           left to show. */
        var previous = box.querySelector("dialog[open]");
        if (previous && swapAroundModal(box, previous, html)) { return; }
        var wasOpen = previous ? previous.id : null;
        /* A window with long content (a breakdown of twenty downloads) is
           scrolled - and after the swap it would jump back up every few
           seconds. The scroll is on .modal-body (style.css). */
        var whereTo = previous ? scrolls(previous) : [];
        var chosen = previous ? filters(previous) : {};
        var focus = focusPosition(box);
        box.innerHTML = html;
        var reopened = false;
        if (wasOpen) {
            var newOnes = document.getElementById(wasOpen);
            if (newOnes && box.contains(newOnes) && typeof newOnes.showModal === "function") {
                newOnes.showModal();
                reopened = true;
                restoreFilters(newOnes, chosen);
                restoreScrolls(newOnes, whereTo);
            }
        }
        // A reopened window takes focus by itself (showModal).
        if (!reopened) { restoreFocus(box, focus); }
    }

    // Something the page asks again and again at its own rhythm: a refreshing element, and the markers of the
    // cards that refresh by themselves (below). What every such asking needs is here once - one request at a
    // time (a slow server does not get a pile of them), nothing on a hidden tab or in the middle of a drag,
    // and after a failure at most once a minute.
    //
    // `ask()` sends the request and returns a promise: of the next rhythm in seconds (none = the starting
    // one), or of null after an unsuccessful answer; a rejected promise is a network error. `changed(seconds)`
    // hears every new rhythm. Returns the asking: reload (ask now), isDue (has its rhythm passed), stop.
    function repeated(initial, ask, changed) {
        var seconds = initial;
        var running = false;
        var stopped = false;
        var errors = 0;

        function rhythm(newItem) {
            if (newItem === seconds) { return; }
            seconds = newItem;
            if (changed) { changed(newItem); }
        }
        // setInterval alone is not enough. On a hidden tab the browser throttles it
        // to one run a minute, and after the computer wakes up or sleeps
        // it can drift apart entirely. So the time is watched: when the element's
        // rhythm has passed since the last refresh, it catches up at once.
        var last = Date.now();

        function reload() {
            last = Date.now();
            // Asking makes no sense on a hidden browser tab. It saves the
            // server and the phone's battery; on return the state is fetched at once.
            //
            // Not in the middle of a drag either: swapping the content would pull the chart
            // out from under the hand, together with the half-made selection.
            if (stopped || running || document.hidden || document.body.dataset.dragging) { return; }
            running = true;
            ask()
                // An unsuccessful answer (an expired sign-in, a disconnected app, a server error): at most once
                // a minute from now on. The first successful answer brings the rhythm back - the one the
                // server sent, otherwise the starting one.
                .then(function (next) {
                    errors = 0;
                    rhythm(next === null ? Math.max(seconds, AFTER_FAILURE) : (next > 0 ? next : initial));
                })
                // A network outage (also a proxy with a sign-in that redirects to another
                // domain - the browser rejects that as an error). The first time it tries again
                // at the same rhythm, only several errors in a row slow it down.
                .catch(function () {
                    errors += 1;
                    if (errors >= ERRORS_IN_A_ROW) { rhythm(Math.max(seconds, AFTER_FAILURE)); }
                })
                .finally(function () { running = false; });
        }

        var asking = {
            reload: reload,
            // Half a tick of tolerance: the timer below ticks every second, and not exactly.
            // A tick that comes a millisecond before the rhythm has passed would otherwise mean
            // waiting another whole second - five seconds would become six.
            isDue: function () { return !stopped && Date.now() - last >= seconds * 1000 - HALF_TICK; },
            stop: function () { stopped = true; }
        };
        refreshers.push(asking);
        return asking;
    }

    Array.prototype.forEach.call(document.querySelectorAll("[data-refresh][data-partial]"), function (box) {
        var cutout = box.dataset.partial;
        repeated(parseInt(box.dataset.refresh, 10) || 10, function () {
            return fetch(cutout, {
                headers: { "X-Requested-With": "fetch" },
                credentials: "same-origin"
            })
                // `redirected` watches for an expired sign-in. In that case the server
                // answers with a redirect to /login, fetch obediently
                // follows it - and without this condition the sign-in page would spill
                // into the card.
                .then(function (r) {
                    if (!r.ok || r.redirected) { return null; }
                    // The rhythm from the server: when to ask next. Without the header
                    // (or with nonsense) the one the page came with holds.
                    var fromServer = parseInt(r.headers.get("X-Jellyscope-Refresh"), 10);
                    return r.text().then(function (html) {
                        swapBox(box, html);
                        return fromServer;
                    });
                });
        }, function (seconds) { box.dataset.refresh = String(seconds); });
    });

    // ------------------------------------------------------------------
    // The cards that refresh by themselves (Settings → Interface → Refreshing cards)
    //
    // Every card drawn from the registry stands in its slot (_card.html: .card-slot with the card's key and
    // the sources its data come from). Every ten seconds the page asks /health what changed - a marker per
    // source (web/status.py) - and when one moved, it asks for the cards over that source again, all of them
    // in ONE request (/partials/cards, web/dashboards.py: what they share is computed once). Only a card whose
    // content really differs is swapped; the others are not touched, so whatever a person does in them stays.
    //
    // The rhythm (docs/superpowers/specs/2026-11-26-live-cards-design.md, part A):
    //   * the cards over plays at most once a minute - while something plays, their marker moves every minute;
    //   * an app's live cards (the source "live" has no marker) once a minute - as long as the server keeps
    //     the app's live state;
    //   * a card with a period on a page with a rolling one ("the last 7 days") once in ten minutes even when
    //     no marker moved - the window moves on by itself;
    //   * the cards over the other sources (the library, a task's data) at the next round after their marker moved.
    // The cards with a refresher of their own (Now playing, Recently added, Downloading, Flowing right now)
    // keep it and are left out here: asked here too they would be refreshed twice, and swapped around their
    // element the refresher would go on writing into an element no longer on the page.
    // ------------------------------------------------------------------
    var MARKERS_EVERY = 10;
    var PLAYS_EVERY = 60;
    var LIVE_EVERY = 60;
    var ROLLING_EVERY = 600;
    var PLAYS = "plays";
    var LIVE = "live";
    // The cards' choices as the page shows them (the Movies/Series chips, the disk over Library growth): drawn
    // anew, a card keeps what the person chose here, not what another tab left in the session.
    var CHOICES = {
        kind: ["#daily-card [data-kind].active", "kind"],
        top_kind: ["#top-items-card [data-kind].active", "kind"],
        growth_disk: ["#library-growth [data-growth-disk].active", "growthDisk"]
    };

    // What a card's content is for telling whether it changed: its HTML as the browser reads it, without what
    // the page's own scripts do to it (an image that did not load hides or goes, base.js) and without the
    // charts' gradient names - a counter of the server's process numbers them, so a card drawn again gets new
    // ones (charts.py). Whitespace counts as one space.
    function content(slot) {
        var copy = slot.cloneNode(true);
        Array.prototype.forEach.call(copy.querySelectorAll("img[data-on-error]"), function (img) {
            var frame = img.parentElement;
            if (frame) {
                frame.classList.remove("no-image");
                if (img.dataset.onError === "hide-parent") { frame.style.display = ""; }
                if (!frame.getAttribute("style")) { frame.removeAttribute("style"); }
            }
            img.parentNode.removeChild(img);
        });
        return copy.innerHTML.replace(/gradient-(mini-)?\d+/g, "gradient").replace(/\s+/g, " ").trim();
    }

    // A field whose value is not the one the page came with - typed into and not sent. The filter of a window
    // (data-filter-modal) is no such thing: it is not sent anywhere, and the swap carries its choice over.
    function edited(field) {
        if (field.hasAttribute("data-filter-modal")) { return false; }
        if (field.type === "checkbox" || field.type === "radio") { return field.checked !== field.defaultChecked; }
        if (field.tagName === "SELECT") {
            var options = Array.prototype.slice.call(field.options);
            if (field.multiple) {
                return options.some(function (option) { return option.selected !== option.defaultSelected; });
            }
            // A list without a chosen option starts at its first one.
            var original = options.filter(function (option) { return option.defaultSelected; }).pop() || options[0];
            return !!original && !original.selected;
        }
        return field.value !== field.defaultValue;
    }

    // Keyboard focus, as the browser tells it (the ring around the control). A browser that does not know
    // the question counts every focus.
    function keyboardFocus(element) {
        try { return element.matches(":focus-visible"); } catch (e) { return true; }
    }

    // Is a person doing something in the card right now? Then it is not swapped and is asked again next time:
    // an open window, a field typed into and not sent (the Disks card's forms), text selected in it, a part
    // that says it is in use (the map zoomed in or held, map.js: data-busy), a switch of the card on its way
    // (the Movies/Series chips, the disk over Library growth: .is-loading - its answer would land in a card
    // no longer on the page), keyboard focus on one of its controls - and a text field or a list with focus
    // even from the mouse. (A drag in a chart stops the whole round, see swapCards.) Focus a click left on a
    // link or a button does not stop it: the swap puts it back.
    function busy(slot) {
        if (slot.querySelector("dialog[open], [data-busy], .is-loading")) { return true; }
        if (Array.prototype.some.call(slot.querySelectorAll("input, select, textarea"), edited)) { return true; }
        var active = document.activeElement;
        if (active && active !== slot && slot.contains(active)
                && (active.matches("input, select, textarea, [contenteditable]") || keyboardFocus(active))) {
            return true;
        }
        var selection = window.getSelection ? window.getSelection() : null;
        return !!selection && !selection.isCollapsed && selection.rangeCount > 0 && selection.containsNode(slot, true);
    }

    // Where an element is in a card, so that the same one is found in its fresh content: by its tag, id and
    // class (a <details> by its summary too) and by its order among the elements alike.
    function signature(element) {
        var summary = element.tagName === "DETAILS" ? element.querySelector("summary") : null;
        return [element.tagName, element.id, element.getAttribute("class") || "",
                summary ? summary.textContent.trim() : ""].join("|");
    }

    function places(slot, wanted) {
        var counts = {};
        var found = [];
        Array.prototype.forEach.call(slot.querySelectorAll("*"), function (element) {
            var sign = signature(element);
            counts[sign] = (counts[sign] || 0) + 1;
            if (wanted(element)) {
                found.push({ sign: sign, order: counts[sign], top: element.scrollTop, left: element.scrollLeft });
            }
        });
        return found;
    }

    function found(slot, place) {
        var order = 0;
        return Array.prototype.filter.call(slot.querySelectorAll("*"), function (element) {
            if (signature(element) !== place.sign) { return false; }
            order += 1;
            return order === place.order;
        })[0] || null;
    }

    // The lowest edge of a card on the screen: its slot makes no box of its own (display: contents).
    function bottomOf(slot) {
        return Array.prototype.reduce.call(slot.children, function (lowest, child) {
            return Math.max(lowest, child.getBoundingClientRect().bottom);
        }, -Infinity);
    }

    // What the person reads when a card wholly above the window is drawn anew: an element in the middle of the
    // window, outside the card, and where it stands. The browser keeps it in place by itself (scroll anchoring);
    // `keepInPlace` puts it back only where the browser did not.
    function readPlace(slot) {
        if (!slot.children.length || bottomOf(slot) > 0) { return null; }
        var element = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
        return element && !slot.contains(element) ? { element: element, top: element.getBoundingClientRect().top } : null;
    }

    function keepInPlace(place) {
        if (!place || !place.element.isConnected) { return; }
        var moved = place.element.getBoundingClientRect().top - place.top;
        if (Math.abs(moved) >= 1) { window.scrollBy(0, moved); }
    }

    // The fresh card in place of the old one. What a person had in it stays: the place on the page they read
    // (readPlace), the scroll of the card's own scrolling boxes (a wide table), the open <details>, the choice
    // in a window's filter (filters, restoreFilters) and the focus a click left on a link or a button
    // (focusPosition, restoreFocus). The page's own offset is not put back: when a card above the window
    // changes its height, the content in view moves with it unless the offset changes too - the browser's
    // scroll anchoring does exactly that. The page's listeners are on the document, and a part that binds to
    // its own element starts again on the event card-redrawn (the map, map.js).
    function redraw(slot, fresh) {
        var reading = readPlace(slot);
        var opened = places(slot, function (e) { return e.tagName === "DETAILS" && e.open; });
        var scrolled = places(slot, function (e) { return e.scrollTop > 0 || e.scrollLeft > 0; });
        var chosen = filters(slot);
        var focus = focusPosition(slot);
        while (slot.firstChild) { slot.removeChild(slot.firstChild); }
        while (fresh.firstChild) { slot.appendChild(fresh.firstChild); }
        opened.forEach(function (place) {
            var element = found(slot, place);
            if (element) { element.open = true; }
        });
        restoreFilters(slot, chosen);
        scrolled.forEach(function (place) {
            var element = found(slot, place);
            if (element) { element.scrollTop = place.top; element.scrollLeft = place.left; }
        });
        restoreFocus(slot, focus);
        keepInPlace(reading);
        slot.dispatchEvent(new CustomEvent("card-redrawn", { bubbles: true }));
    }

    // Not the cards with a refresher of their own (above), nor a card the page keeps still (data-card-still: the
    // page writes numbers of its own the card adds up to, _macros.html show_card).
    var liveCards = document.body.dataset.selfRefresh !== "1" ? [] : Array.prototype.filter.call(
        document.querySelectorAll(".card-slot[data-card-key]"),
        function (slot) { return slot.dataset.cardStill !== "1" && !slot.querySelector("[data-partial], [data-version]"); }
    ).map(function (slot) {
        return {
            slot: slot,
            key: slot.dataset.cardKey,
            sources: (slot.dataset.cardSources || "").split(" ").filter(Boolean),
            period: slot.dataset.cardPeriod === "1",
            // The content the card was drawn with, and when: the page's own drawing to begin with.
            known: content(slot),
            shown: Date.now(),
            // The sources whose marker moved since the card was drawn.
            moved: {}
        };
    });
    // History's bar of new plays (see askNewPlays); none with Refreshing cards off.
    var newPlays = document.body.dataset.selfRefresh === "1" ? document.getElementById("history-new") : null;
    // The markers of the previous answer. To begin with those the page was drawn with (base.html: data-markers,
    // taken before the page computed its data), so a change since - while the page was computed or on its way -
    // moves a marker already at the first answer. A page without them: the first answer only says where it
    // starts from.
    var markers = drawnMarkers();
    var markersAsking = null;

    function drawnMarkers() {
        try {
            var drawn = JSON.parse(document.body.dataset.markers || "null");
            return drawn && typeof drawn === "object" ? drawn : null;
        } catch (e) {
            return null;
        }
    }

    // The sign-in has expired (14 days, signed out in another tab, a disabled account): one bar at the top says
    // so and the asking stops - otherwise the cards would stand still without a word.
    function expire() {
        markersAsking.stop();
        var bar = document.getElementById("session-expired");
        if (bar) { bar.hidden = false; }
        return 0;
    }

    // The left panel's "N active playbacks" and "Last run" from the same answer (base.html). The number comes
    // only with the right "Now playing"; at nought the row hides, as the server draws it.
    function panel(data) {
        var count = document.querySelector("[data-active-count]");
        if (count && typeof data.active_sessions === "number") {
            count.textContent = String(data.active_sessions);
            count.parentNode.hidden = !data.active_sessions;
        }
        var lastRun = document.querySelector("[data-last-poll]");
        if (lastRun && data.last_poll_text) { lastRun.textContent = data.last_poll_text; }
    }

    // The cards to draw anew after an answer of the markers (by the rhythm above), and which of them a person is
    // not busy in right now.
    function due(current) {
        var moved = Object.keys(current).filter(function (source) {
            return markers !== null && markers[source] !== current[source];
        });
        markers = current;
        var now = Date.now() + HALF_TICK;
        var rolling = /(^|&)days=[1-9]/.test(document.body.dataset.period || "");
        return liveCards.filter(function (card) {
            moved.forEach(function (source) {
                if (card.sources.indexOf(source) !== -1) { card.moved[source] = true; }
            });
            var since = now - card.shown;
            var wanted = Object.keys(card.moved).some(function (source) {
                return source !== PLAYS || since >= PLAYS_EVERY * 1000;
            })
                || (card.sources.indexOf(LIVE) !== -1 && since >= LIVE_EVERY * 1000)
                || (card.period && rolling && since >= ROLLING_EVERY * 1000);
            return wanted && !busy(card.slot);
        });
    }

    function choices() {
        return Object.keys(CHOICES).map(function (name) {
            var chip = document.querySelector(CHOICES[name][0]);
            return chip ? "&" + name + "=" + encodeURIComponent(chip.dataset[CHOICES[name][1]] || "") : "";
        }).join("");
    }

    // The answer taken apart by the slots: each asked card swapped only when its content differs.
    function swapCards(asked, html) {
        var answer = document.createElement("template");
        answer.innerHTML = html;
        var fresh = {};
        Array.prototype.forEach.call(answer.content.querySelectorAll(".card-slot[data-card-key]"), function (slot) {
            fresh[slot.dataset.cardKey] = slot;
        });
        var now = Date.now();
        asked.forEach(function (card) {
            var next = fresh[card.key];
            // A drag in a chart began, or the person started doing something in the card while it was being
            // drawn: it is asked again at the next round.
            if (next && (document.body.dataset.dragging || busy(card.slot))) { return; }
            card.shown = now;
            card.moved = {};
            // Not in the answer: the account may no longer see it, or its app went away - it stays as it is.
            if (!next) { return; }
            var text = content(next);
            if (text === card.known) { return; }
            card.known = text;
            redraw(card.slot, next);
        });
    }

    function askCards(asked) {
        if (!asked.length) { return 0; }
        var keys = asked.map(function (card) { return encodeURIComponent(card.key); }).join(",");
        var inDashboard = asked[0].slot.closest(".dash-card") ? "&in_dashboard=1" : "";
        return fetch(withPeriod("/partials/cards?keys=" + keys + inDashboard + choices()), {
            headers: { "X-Requested-With": "fetch" },
            credentials: "same-origin"
        }).then(function (r) {
            // A redirect to /login: the sign-in expired since the markers answered.
            if (r.redirected) { return expire(); }
            if (!r.ok) { return null; }
            return r.text().then(function (html) { swapCards(asked, html); });
        });
    }

    // History (history.html) is not rewritten under the reader's eyes: it has no cards, only a bar above its
    // table. When the plays' marker moved, the page asks how many plays its filter finds more than the page was
    // drawn with - the server counts them by the page's own filter and rights (/history/new, web/history.py), so
    // the number is what loading the page again adds - and the bar shows it; its link loads the page again with
    // the same filter, at the same place (data-keep-scroll). Like the markers, never on a hidden tab (repeated).
    function askNewPlays() {
        return fetch(newPlays.dataset.historyNew, {
            headers: { "X-Requested-With": "fetch" },
            credentials: "same-origin",
            cache: "no-store"
        }).then(function (r) {
            if (r.redirected) { return expire(); }
            if (!r.ok) { return null; }
            return r.json().then(function (data) {
                var count = data && data.count > 0 ? data.count : 0;
                newPlays.querySelector("[data-history-new-text]").textContent = count ? data.text : "";
                newPlays.hidden = !count;
            });
        });
    }

    function askMarkers() {
        // cache: no-store - the browser must not answer from its memory (see the watcher of finished tasks).
        return fetch("/health", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
            // A proxy that signs in by itself redirects; the app answers a signed-out page without markers.
            if (r.redirected) { return expire(); }
            if (!r.ok) { return null; }
            return r.json().then(function (data) {
                // Signed out: the app answers only that it is alive (no version).
                if (!data || !data.version) { return expire(); }
                panel(data);
                // Signed in without markers: Refreshing cards were switched off meanwhile - the page stops asking
                // and stays as it is, as every page does with them off.
                if (!data.markers) {
                    markersAsking.stop();
                    return 0;
                }
                var playsMoved = markers !== null && markers[PLAYS] !== data.markers[PLAYS];
                var asked = due(data.markers);
                // History has no cards, a page with cards no bar of new plays.
                return newPlays && playsMoved ? askNewPlays() : askCards(asked);
            });
        });
    }

    if (liveCards.length || newPlays) {
        markersAsking = repeated(MARKERS_EVERY, askMarkers);
        // At once: what changed since the page was drawn.
        markersAsking.reload();
    }
    if (!refreshers.length) { return; }

    // The swap used to be skipped while the mouse was over the card. That sounded
    // considerate, but it had an unpleasant consequence: whoever looked at the card
    // with the mouse on it stopped getting new data altogether. A live view is to
    // show reality - and there is nothing in the card that could be clicked open
    // by mistake.

    function allOf() {
        refreshers.forEach(function (o) { o.reload(); });
    }

    setInterval(function () {
        refreshers.forEach(function (o) { if (o.isDue()) { o.reload(); } });
    }, 1000);

    // After returning to the tab, show the current state at once, not after the interval.
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) { allOf(); }
    });
    // The same after the machine wakes up or the network comes back.
    window.addEventListener("focus", allOf);
    window.addEventListener("online", allOf);
})();

// ------------------------------------------------------------------
// The watcher of finished tasks
//
// A background task finds a new film and writes it to the database - and on an
// already open page nothing happens, because that shows the state from the moment
// of loading. This watcher solves it for two places at once:
//
//   * the Overview - swaps the "Recently added" strip,
//   * Settings → Tasks - loads the page again, so the "task running" stripe
//     disappears and the result is filled in.
//
// What decides is the **transition** from "task running" to "not running". Originally
// only the library's fingerprint was watched, but that does not change when a task
// finds nothing - and the stripe about a running task then stayed hanging on the page forever.
// The fingerprint stays as a second trigger: it also catches a change not made by
// a task in this process (a second instance, a manual edit of the database).
//
// We ask /health, which is a few hundred bytes. The whole fragment is downloaded
// only when there is something to show.
// ------------------------------------------------------------------
(function () {
    var strip = document.getElementById("recently-added");
    var reloadPage = document.body.dataset.reloadOnTask === "1";
    if ((!strip && !reloadPage) || !window.fetch) { return; }

    var knownVersion = strip ? (strip.dataset.version || "") : null;
    // How many tasks had finished when the page was being drawn. This is the
    // number that matters: by its change a finish is recognised even when
    // the whole task fits between two requests - and for a quick
    // sync over a small library it fits easily.
    var knownTasks = document.body.dataset.tasksVersion || "";
    var running = false;

    function loadStrip() {
        return fetch("/partials/recently-added", {
            headers: { "X-Requested-With": "fetch" },
            credentials: "same-origin"
        }).then(function (r) {
            // An expired sign-in: the server redirects to /login and fetch
            // follows it. Without this condition the sign-in page would spill
            // into the poster strip.
            return (r.ok && !r.redirected) ? r.text() : null;
        }).then(function (html) {
            if (html && strip) { strip.innerHTML = html; }
        });
    }

    function verify() {
        if (running || document.hidden) { return; }
        running = true;
        // cache: no-store is a necessity here, not caution. Without it the
        // browser may answer from its memory and the watcher would look forever
        // at the state from the first request.
        fetch("/health", { credentials: "same-origin", cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { return null; }

                // Nothing refreshes while it runs - results change in
                // batches and the page would jump around in the middle of a scan.
                if (data.task_running) { return null; }

                var finished = data.tasks_version !== undefined
                           && String(data.tasks_version) !== knownTasks;
                if (finished) { knownTasks = String(data.tasks_version); }

                if (finished && reloadPage) {
                    if (window.rememberPosition) { window.rememberPosition(); }
                    window.location.reload();
                    return null;
                }

                var change = data.library_version
                         && data.library_version !== knownVersion;
                if (strip && (finished || change)) {
                    knownVersion = data.library_version;
                    return loadStrip();
                }
                return null;
            })
            .catch(function () { /* a network outage - it tries again in a while */ })
            .finally(function () { running = false; });
    }

    // Ten seconds: often enough not to miss a task finishing,
    // and rarely enough not to keep the server busy with it.
    setInterval(verify, 10000);
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) { verify(); }
    });
    window.addEventListener("focus", verify);
    // Right after loading, so a task that was already running is recognised too.
    verify();
})();

// ------------------------------------------------------------------
// The progress indicator of a running task
//
// The server knows how many items there will be in total (it asks Jellyfin first)
// and how many are done. The page takes that from /health - the same
// place the waiting below asks.
//
// When the total is unknown, at least "hotovo N" shows, without percent.
// Making percent up would be worse than not having them.
// ------------------------------------------------------------------
(function () {
    var box = document.getElementById("task-progress");
    if (!box || !window.fetch) { return; }

    var bar = document.getElementById("task-progress-fill");
    var description = document.getElementById("task-progress-text");
    var running = false;
    var timer = null;
    // Has anything on the page been typed or ticked since it loaded? (The Stop form is no such thing.)
    var edited = false;
    ["input", "change"].forEach(function (kind) {
        document.addEventListener(kind, function (event) {
            var target = event.target;
            if (target && target.closest && target.closest("form") && !target.closest("[data-running-task]")) {
                edited = true;
            }
        }, true);
    });

    function reloadAll() {
        // Not on a hidden tab (the same as the other watchers) and not while the previous
        // request still waits - a heavy task can slow /health down and requests piled up.
        if (running || document.hidden) { return; }
        running = true;
        fetch("/health", { credentials: "same-origin", cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                // The box is drawn only while a task runs (the macro running_task). When it has
                // ended, the asking stops and the page loads again - otherwise "A task is running."
                // and the Stop button stayed. Where the page waits for the task by itself
                // (data-wait-for, data-reload-on-task), it loads the page itself.
                if (data && !data.task_running) {
                    clearInterval(timer);
                    box.hidden = true;
                    if (!document.body.dataset.waitFor && document.body.dataset.reloadOnTask !== "1") {
                        // Not over a form somebody has started to fill in: a reload would throw the typed
                        // values away without a word. Then only the strip and the Stop button go.
                        if (edited) {
                            document.querySelectorAll("[data-running-task]").forEach(function (part) {
                                part.hidden = true;
                            });
                        } else {
                            if (window.rememberPosition) { window.rememberPosition(); }
                            window.location.reload();
                        }
                    }
                    return;
                }
                var p = data && data.progress;
                if (!p || !p.kind) { box.hidden = true; return; }

                box.hidden = false;
                if (p.percent === null || p.percent === undefined) {
                    bar.style.width = "100%";
                    bar.classList.add("indeterminate");
                    description.textContent = TEXTS.done + " " + p.done;
                } else {
                    bar.classList.remove("indeterminate");
                    bar.style.width = p.percent + "%";
                    description.textContent = p.done + " / " + p.total
                        + "  (" + p.percent + " %)";
                }
            })
            .catch(function () { /* it tries again in a while */ })
            .finally(function () { running = false; });
    }

    reloadAll();
    timer = setInterval(reloadAll, 2000);
})();

// ------------------------------------------------------------------
// Waiting for the app's restart and for a stopped task to finish
//
// Both used to end with a person having to refresh the page themselves -
// and above all not knowing when it would work. Here the waiting is done for them.
//
// A restart is recognised by `started_at` from /health: after the process is swapped it is
// a different number. Waiting for "stops answering and starts again" is not enough -
// a restart takes a moment and a request need not hit that gap.
// ------------------------------------------------------------------
(function () {
    if (!window.fetch) { return; }

    var reason = document.body.dataset.waitFor;   // "restart" | "task"
    if (!reason) { return; }

    // The start of the process that drew the page - see `running_since` in web/base.py.
    // Taking it from the first /health response is a trap: the restart comes before
    // the first request manages to go out, and the waiting page then waits for a change
    // of a number that has already changed.
    var originalStart = Number(document.body.dataset.runSince) || null;
    var seenRunning = false;
    var attempts = 0;
    var MAX_ATTEMPTS = 900;                        // ~30 minutes, then we give up

    function announce(text) {
        var box = document.getElementById("wait-note");
        if (box) { box.textContent = text; }
    }

    function reload() {
        // `replace`, not `reload`: it also drops ?wait=... from the address,
        // otherwise the waiting would start again and the page would spin
        // round and round. And the back button does not lead to an in-between state.
        if (window.rememberPosition) { window.rememberPosition(); }
        var url = new URL(window.location.href);
        url.searchParams.delete("wait");
        window.location.replace(url.toString());
    }

    function attempt() {
        if (attempts++ > MAX_ATTEMPTS) {
            announce(TEXTS.takingLong);
            return;
        }
        fetch("/health", { credentials: "same-origin", cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { throw new Error("unavailable"); }

                if (reason === "restart") {
                    if (originalStart === null) { originalStart = data.started_at; }
                    else if (data.started_at !== originalStart) { reload(); return; }
                    // Otherwise it keeps waiting - the process is still the original one.
                } else {
                    // The task starts in the background, so right after
                    // the redirect it need not be running yet. If we
                    // concluded straight away "nothing runs, done", the page
                    // would load again before the task started.
                    //
                    // So we wait until we see it running - and only then
                    // watch for when it ends. When it does not get going even after
                    // a few tries, we load the page anyway (perhaps
                    // it ended with an error before we asked).
                    if (data.task_running) { seenRunning = true; }
                    else if (seenRunning || attempts > 5) { reload(); return; }
                }
                setTimeout(attempt, 2000);
            })
            .catch(function () {
                // The app is down right now - during a restart that is expected.
                setTimeout(attempt, 2000);
            });
    }

    attempt();
})();

// ------------------------------------------------------------------
// The films / series switch without loading the page
//
// The links stay plain links - without JavaScript they keep working, only
// the whole page loads. With JavaScript we ask the server for just that
// one card and swap it in place, so you stay where you are.
//
// The server still draws it, with the same template as on a normal load.
// Drawing the chart a second time in JavaScript would mean two places where
// a mistake can be made - and one of them would drift apart from the other over time.
// ------------------------------------------------------------------
(function () {
    if (!window.fetch) { return; }

    /* One handler for each card that has its own switch.
       `id` is the card's element, `path` the fragment on the server and `parameter` the name
       of the part of the address the switch changes. The listener is on the document, not on
       the card: a card that refreshes by itself is drawn anew, wrapper included, and a listener
       on the old element would be gone with it (the same as the disk chips below). */
    function switchableCard(id, path, parameter) {
        var loading = false;

        document.addEventListener("click", function (event) {
            var link = event.target.closest ? event.target.closest("[data-kind]") : null;
            var card = link ? link.closest("#" + id) : null;
            if (!card || loading) { return; }

            event.preventDefault();
            loading = true;
            card.classList.add("is-loading");

            fetch(withPeriod(path + "?kind=" + encodeURIComponent(link.dataset.kind)), {
                headers: { "X-Requested-With": "fetch" },
                credentials: "same-origin"
            })
                .then(function (response) {
                    // `redirected` = an expired sign-in: the server sends /login and fetch follows it -
                    // the sign-in page would end up inside the card. The whole page goes there instead.
                    if (!response.ok || response.redirected) { throw new Error(response.status); }
                    return response.text();
                })
                .then(function (html) {
                    card.innerHTML = html;
                    // The address is rewritten to match what is visible -
                    // refreshing the page then shows the same. replaceState instead of
                    // pushState: switching a filter is not a step in history,
                    // the back button is to lead to the previous page.
                    var url = new URL(window.location.href);
                    url.searchParams.set(parameter, link.dataset.kind);
                    window.history.replaceState({}, "", url);
                })
                .catch(function () {
                    // When it fails, we do it the old way - with the whole
                    // page. The user must not be left with a dead button.
                    // We remember the position again: since the click, enough time
                    // may have passed for the remembered one to no longer hold.
                    // The page's period goes along: the link's "?kind=" alone would load the page for the session's period.
                    if (window.rememberPosition) { window.rememberPosition(); }
                    window.location.href = withPeriod(link.href);
                })
                .finally(function () {
                    loading = false;
                    card.classList.remove("is-loading");
                });
        });
    }

    /* Windows (<dialog>) in Settings. A button carries data-modal with the id of that
       window - so one listener is enough for all of them, including those that
       are yet to come.

       showModal() (not show()) on purpose: it dims the background, catches Esc
       and does not let clicks through to the page under the window. */
    document.addEventListener("click", function (event) {
        var button = event.target.closest ?
            event.target.closest("[data-modal]") : null;
        if (!button) { return; }
        var modal = document.getElementById(button.dataset.modal);
        if (modal && typeof modal.showModal === "function") {
            event.preventDefault();
            modal.showModal();
        }
    });

    /* The disk choice above the Library growth chart (chips with data-growth-disk).
       It swaps only the card - the page does not load again and does not move, like
       the Films/Series switch (switchableCard). The listener is on the whole
       document: the card is swapped whole, chips included, so a listener on it would
       vanish after the first swap. When the swap fails, the page loads the old way. */
    document.addEventListener("click", function (event) {
        var chip = event.target.closest ? event.target.closest("[data-growth-disk]") : null;
        var cardElement = chip ? chip.closest("#library-growth") : null;
        if (!cardElement) { return; }
        event.preventDefault();
        if (cardElement.classList.contains("is-loading")) { return; }
        cardElement.classList.add("is-loading");

        fetch(withPeriod("/library/partials/growth?growth_disk=" + encodeURIComponent(chip.dataset.growthDisk)), {
            headers: { "X-Requested-With": "fetch" },
            credentials: "same-origin"
        })
            .then(function (response) {
                if (!response.ok) { throw new Error(response.status); }
                return response.text();
            })
            .then(function (html) {
                var wrapper = document.createElement("div");
                wrapper.innerHTML = html;
                var fresh = wrapper.querySelector("#library-growth");
                if (!fresh) { throw new Error("no card"); }
                cardElement.parentNode.replaceChild(fresh, cardElement);
                // The address matches what is visible; replaceState - a switch
                // is not a step in history (see switchableCard).
                var url = new URL(window.location.href);
                url.searchParams.set("growth_disk", chip.dataset.growthDisk);
                window.history.replaceState({}, "", url);
            })
            .catch(function () {
                cardElement.classList.remove("is-loading");
                if (window.rememberPosition) { window.rememberPosition(); }
                window.location.href = withPeriod(chip.href);
            });
    });

    /* Paths of a virtual disk (the Disks card, the disk's window). "Add a path"
       clones the first row (field + cross) and empties it; the cross removes
       a row - the last one it only empties, so the form never loses the field
       for a path. The server drops empty fields on save.

       After every change the rows are renumbered for a screen reader: the first field
       carries the id the label "Cesty" points at, the other fields and crosses
       a number ("Cesta 2", "Odebrat cestu 2"). The id of the first field and the texts
       of the labels are carried by the list (data-paths, data-label…), so they are translated. */
    function renumber(list) {
        [].forEach.call(list.querySelectorAll(".path-row"), function (row, order) {
            var field = row.querySelector("input");
            var cross = row.querySelector("[data-remove-path]");
            if (order === 0) {
                field.id = list.dataset.paths;
                field.removeAttribute("aria-label");
            } else {
                field.removeAttribute("id");
                field.setAttribute("aria-label", list.dataset.label + " " + (order + 1));
            }
            cross.setAttribute("aria-label", list.dataset.labelRemove + " " + (order + 1));
        });
    }

    document.addEventListener("click", function (event) {
        var addButton = event.target.closest ? event.target.closest("[data-add-path]") : null;
        var remove = event.target.closest ? event.target.closest("[data-remove-path]") : null;
        if (!addButton && !remove) { return; }
        var formElement = (addButton || remove).closest("form");
        var list = formElement ? formElement.querySelector("[data-paths]") : null;
        if (!list) { return; }
        event.preventDefault();
        if (addButton) {
            var row = list.querySelector(".path-row").cloneNode(true);
            var field = row.querySelector("input");
            field.value = "";
            field.removeAttribute("id");
            list.appendChild(row);
            renumber(list);
            field.focus();
            return;
        }
        var rows = list.querySelectorAll(".path-row");
        var thisOne = remove.closest(".path-row");
        if (rows.length > 1) {
            thisOne.parentNode.removeChild(thisOne);
            renumber(list);
        } else {
            thisOne.querySelector("input").value = "";
        }
    });

    /* Copy - a freshly made API key (macro key_cell).
       The browser's clipboard (navigator.clipboard) works only on HTTPS or
       localhost, but Jellyscope often runs on plain HTTP
       in a home network. There the key is at least selected and the older
       document.execCommand("copy") is tried; when even that fails, the message says
       to copy it by hand (Ctrl+C). The result goes to
       role="status" next to the button - a screen reader reads it, focus stays. */
    document.addEventListener("click", function (event) {
        var button = event.target.closest ?
            event.target.closest("[data-copy]") : null;
        if (!button) { return; }
        var source = document.getElementById(button.dataset.copy);
        if (!source) { return; }
        var state = button.parentNode.querySelector('[role="status"]');
        function notify(text) { if (state) { state.textContent = text; } }
        function fallback() {
            var range = document.createRange();
            range.selectNodeContents(source);
            var selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            var succeeded = false;
            try { succeeded = document.execCommand("copy"); } catch (e) { succeeded = false; }
            notify(succeeded ? button.dataset.done : button.dataset.marked);
        }
        if (navigator.clipboard && window.isSecureContext) {
            /* The browser may ask for permission and the promise then hangs -
               without a safety net a person would get no response. What is
               not settled within a second ends as on plain HTTP. */
            var settled = false;
            var safeguard = setTimeout(function () {
                if (!settled) { settled = true; fallback(); }
            }, 1000);
            navigator.clipboard.writeText(source.textContent.trim()).then(function () {
                /* Even late (after the safety net): the clipboard has the key, so
                   the message "copy with Ctrl+C" would lie. */
                settled = true;
                clearTimeout(safeguard);
                notify(button.dataset.done);
            }, function () {
                if (settled) { return; }
                settled = true;
                clearTimeout(safeguard);
                fallback();
            });
        } else {
            fallback();
        }
    });

    /* The whole table in a window (Insights). A card shows only the first few
       rows by Settings → Interface → Long lists; the button
       under it copies the card's whole table into the shared window
       #table-window together with the card's heading. A copy, not a move: the card
       stays as it was, and one window is enough for all tables. */
    document.addEventListener("click", function (event) {
        var button = event.target.closest ?
            event.target.closest("[data-table-window]") : null;
        if (!button) { return; }
        var modal = document.getElementById("table-window");
        var cardElement = button.closest(".card");
        var table = cardElement ? cardElement.querySelector("table") : null;
        if (!modal || !table || typeof modal.showModal !== "function") { return; }
        event.preventDefault();
        var copy = table.cloneNode(true);
        copy.querySelectorAll(".extra-row, .desktop-only").forEach(function (row) {
            row.classList.remove("extra-row", "desktop-only");
        });
        var heading = cardElement.querySelector(".card-head h2");
        modal.querySelector(".modal-head h2").textContent = heading ? heading.textContent : "";
        var content = modal.querySelector(".modal-body");
        content.innerHTML = "";
        content.appendChild(copy);
        // The window is one for all tables: without the reset the second
        // table would start where the first one was scrolled to - and the first
        // rows, the most important ones, would not be visible.
        content.scrollTop = 0;
        modal.scrollTop = 0;
        modal.showModal();
    });

    /* A window with content that loads only after a click. The link carries
       data-modal-load with the window's id and href with the fragment's address; without
       JavaScript the link opens as a whole page (the route can do both),
       so nobody loses the list. The loaded content stays in the window -
       the second opening is instant. */
    document.addEventListener("click", function (event) {
        var anchor = event.target.closest ?
            event.target.closest("[data-modal-load]") : null;
        if (!anchor) { return; }
        var modal = document.getElementById(anchor.dataset.modalLoad);
        if (!modal || typeof modal.showModal !== "function") { return; }
        event.preventDefault();
        var content = modal.querySelector(".modal-body");
        modal.showModal();
        if (content.dataset.loaded === anchor.href) { return; }
        content.innerHTML = '<p class="hint">…</p>';
        fetch(withPeriod(anchor.href), { headers: { "X-Requested-With": "fetch" },
                                         credentials: "same-origin" })
            // `redirected`: an expired sign-in (see switchableCard) - not the sign-in page inside the window.
            .then(function (r) { if (!r.ok || r.redirected) { throw new Error(r.status); } return r.text(); })
            .then(function (html) {
                content.innerHTML = html;
                content.dataset.loaded = anchor.href;
            })
            .catch(function () {
                // Without the fragment the whole page opens - the same reasoning
                // as with switchable cards. The position is remembered
                // for the way back: whoever comes back lands where they
                // clicked.
                if (window.rememberPosition) { window.rememberPosition(); }
                window.location.href = withPeriod(anchor.href);
            });
    });

    /* Picking the target title (assignment from the archive) - see the macro
       target_picker. It searches only from two characters on and with a small delay, so
       typing does not send a request for every letter. An older response
       that arrives after a newer one is dropped. */
    document.querySelectorAll("[data-target-picker]").forEach(function (box) {
        var field = box.querySelector('input[type="search"]');
        var hiddenOnes = box.querySelector('input[type="hidden"]');
        var list = box.querySelector(".target-picker-matches");
        var chosen = box.querySelector(".target-picker-chosen");
        var timer = null;
        var last = "";

        function notify() {
            box.dispatchEvent(new Event("change", { bubbles: true }));
        }
        /* A little poster - a small poster next to the name in the assignment menu. When
           the image fails to load, an empty grey box of the same size stays. */
        function smallPoster(wrapper, address) {
            wrapper.textContent = "";
            if (!address) { return; }
            var img = document.createElement("img");
            img.alt = "";
            img.loading = "lazy";
            img.onerror = function () { img.remove(); };
            img.src = address;
            wrapper.appendChild(img);
        }
        function where(p) {
            return [p.library, p.file].filter(Boolean).join(" · ");
        }
        function selection(entry) {
            hiddenOnes.value = entry.id;
            /* For the recap of a bulk assignment. */
            box.dataset.targetDescription = entry.description;
            box.dataset.targetPreview = entry.thumbnail || "";
            box.dataset.targetWhere = where(entry);
            smallPoster(chosen.querySelector(".poster-small"), entry.thumbnail);
            chosen.querySelector("strong").textContent = entry.description;
            chosen.querySelector("small").textContent = where(entry);
            chosen.hidden = false;
            field.hidden = true;
            list.hidden = true;
            notify();
        }
        function render(entries, next) {
            list.textContent = "";
            entries.forEach(function (p) {
                var li = document.createElement("li");
                var button = document.createElement("button");
                button.type = "button";
                var wrapper = document.createElement("span");
                wrapper.className = "poster-small";
                smallPoster(wrapper, p.thumbnail);
                var text = document.createElement("span");
                text.className = "target-picker-text";
                var title = document.createElement("span");
                title.textContent = p.description;
                var spot = document.createElement("small");
                spot.textContent = where(p);
                text.appendChild(title);
                text.appendChild(spot);
                button.appendChild(wrapper);
                button.appendChild(text);
                button.addEventListener("click", function () { selection(p); });
                li.appendChild(button);
                list.appendChild(li);
            });
            /* An empty list with more results is not "nothing": only the cap
               of candidates was gone through - then a hint to refine the search is enough. */
            if (!entries.length && !next) {
                var nothing = document.createElement("li");
                nothing.className = "empty";
                nothing.textContent = box.dataset.empty || "";
                list.appendChild(nothing);
            }
            /* The server returns only the first few; whoever looks for the sixth series
               is to know they will find it by refining, not by scrolling. */
            if (next) {
                var more = document.createElement("li");
                more.className = "empty";
                more.textContent = box.dataset.next || "";
                list.appendChild(more);
            }
            list.hidden = false;
        }
        function search(query) {
            last = query;
            fetch("/archive/search?q=" + encodeURIComponent(query),
                  { credentials: "same-origin" })
                .then(function (r) {
                    // An expired sign-in (redirected to /login): the page goes to signing in,
                    // otherwise the search would find nothing in silence.
                    if (r.redirected) { if (window.rememberPosition) { window.rememberPosition(); } window.location.reload(); return null; }
                    return r.ok ? r.json() : { items: [] };
                })
                .then(function (data) {
                    if (data && query === last) { render(data.items || [], data.next); }
                })
                .catch(function () { /* a network outage: the menu stays as it was */ });
        }
        field.addEventListener("input", function () {
            clearTimeout(timer);
            var query = field.value.trim();
            if (query.length < 2) {
                last = "";
                list.hidden = true;
                return;
            }
            timer = setTimeout(function () { search(query); }, 250);
        });
        /* Enter in the search must not submit the form - nothing has been picked yet. */
        field.addEventListener("keydown", function (event) {
            if (event.key === "Enter") { event.preventDefault(); }
        });
        chosen.querySelector("button").addEventListener("click", function () {
            hiddenOnes.value = "";
            delete box.dataset.targetDescription;
            delete box.dataset.targetPreview;
            delete box.dataset.targetWhere;
            chosen.hidden = true;
            field.hidden = false;
            field.value = "";
            field.focus();
            notify();
        });
    });

    /* The assignment's confirm button: while there is nothing to assign, it is
       off, and in the bulk window it shows how many titles will move. */
    document.querySelectorAll("form[data-assignment]").forEach(function (form) {
        var confirmButton = form.querySelector("[data-assignment-confirm]");
        var count = form.querySelector("[data-assignment-count]");
        function align() {
            var chosen = 0;
            form.querySelectorAll('.target-picker input[type="hidden"]').forEach(function (i) {
                if (i.value) { chosen += 1; }
            });
            confirmButton.disabled = chosen === 0;
            if (count) { count.textContent = chosen; }
        }
        form.addEventListener("change", align);
        align();
    });

    /* Archive windows with two steps - the choice and the confirmation (assigning
       and deleting). This is their shared part: switching the step,
       "Back to the choice" and returning to the choice after the window closes. Returns
       a function that switches to the confirmation (true) or back (false).
       Focus on the confirmation goes to "Back": Enter out of habit then does
       nothing. */
    function twoSteps(form) {
        var selection = form.querySelector('[data-step="select"]');
        var confirmation = form.querySelector('[data-step="confirm"]');
        var back = confirmation.querySelector("[data-step-back]");
        function show(onConfirm) {
            selection.hidden = onConfirm;
            confirmation.hidden = !onConfirm;
            if (onConfirm) { back.focus(); }
        }
        back.addEventListener("click", function () { show(false); });
        var modal = form.closest("dialog");
        if (modal) { modal.addEventListener("close", function () { show(false); }); }
        return show;
    }

    /* One item in the confirmation: (a little poster), the name, a note under it.
       Without a preview address - an archive item whose image is no longer
       in Jellyfin - no little poster is drawn. Text only through
       textContent, never as HTML. */
    function recapItem(preview, description, note) {
        var wrapper = document.createElement("span");
        wrapper.className = "recap-item";
        if (preview) {
            var poster = document.createElement("span");
            poster.className = "poster-small";
            var img = document.createElement("img");
            img.alt = "";
            img.onerror = function () { img.remove(); };
            img.src = preview;
            poster.appendChild(img);
            wrapper.appendChild(poster);
        }
        var text = document.createElement("span");
        text.className = "target-picker-text";
        var title = document.createElement("strong");
        title.textContent = description || "?";
        text.appendChild(title);
        if (note) {
            var noteElement = document.createElement("small");
            noteElement.textContent = note;
            text.appendChild(noteElement);
        }
        wrapper.appendChild(text);
        return wrapper;
    }

    /* The note under the name in a row of the choice (plays · file) without
       the template's line breaks and spaces. */
    function rowNote(row) {
        var small = row.querySelector("small");
        return small ? small.textContent.replace(/\s+/g, " ").trim() : "";
    }

    /* The recap of a bulk assignment. "Assign (N)" submits
       nothing - it only shows what will happen: every pair archive →
       target (with a poster), how many plays move and how many items
       remain. Only "Yes, assign" submits (it carries confirmed=1). */
    document.querySelectorAll("form[data-recap]").forEach(function (form) {
        var showStep = twoSteps(form);
        var list = form.querySelector("[data-recap-list]");
        var summary = form.querySelector("[data-recap-summary]");
        var remainder = form.querySelector("[data-recap-rest]");
        var language = document.documentElement.lang || "cs";

        form.querySelector("[data-step-next]").addEventListener("click", function () {
            list.textContent = "";
            var titles = 0;
            var playback = 0;
            var without = 0;
            form.querySelectorAll(".assignment-row").forEach(function (row) {
                var box = row.querySelector("[data-target-picker]");
                if (!box.querySelector('input[type="hidden"]').value) {
                    without += 1;
                    return;
                }
                titles += 1;
                playback += parseInt(row.dataset.plays || "0", 10) || 0;
                /* On the left the archive item (plays · file),
                   on the right the target with its library and file. */
                var li = document.createElement("li");
                li.appendChild(recapItem("", row.dataset.description, rowNote(row)));
                var arrow = document.createElement("span");
                arrow.className = "recap-arrow";
                arrow.setAttribute("aria-hidden", "true");
                arrow.textContent = "→";
                li.appendChild(arrow);
                li.appendChild(recapItem(box.dataset.targetPreview, box.dataset.targetDescription,
                    box.dataset.targetWhere));
                list.appendChild(li);
            });
            if (!titles) { return; }
            summary.textContent = summary.dataset.template
                .replace("{t}", titles.toLocaleString(language))
                .replace("{n}", playback.toLocaleString(language));
            remainder.textContent = without
                ? remainder.dataset.template.replace("{count}", without.toLocaleString(language)) : "";
            showStep(true);
        });
    });

    /* Deleting from the archive. Tick boxes by the rows, "Select all" at the top
       (half-ticked when only some are chosen) and a button that
       shows how many are chosen. "Delete selected" only shows what
       will vanish - only "Ano, smazat" deletes (it carries confirmed=1). */
    document.querySelectorAll("form[data-archive-delete]").forEach(function (form) {
        var showStep = twoSteps(form);
        var everything = form.querySelector("[data-select-all]");
        var fields = Array.prototype.slice.call(
            form.querySelectorAll('.delete-row input[type="checkbox"]'));
        var onward = form.querySelector("[data-step-next]");
        var count = form.querySelector("[data-delete-count]");
        var list = form.querySelector("[data-delete-list]");
        var summary = form.querySelector("[data-delete-summary]");
        var language = document.documentElement.lang || "cs";

        function selected() {
            return fields.filter(function (p) { return p.checked; });
        }
        function align() {
            var n = selected().length;
            count.textContent = n.toLocaleString(language);
            onward.disabled = n === 0;
            everything.checked = n > 0 && n === fields.length;
            everything.indeterminate = n > 0 && n < fields.length;
        }
        form.addEventListener("change", function (evt) {
            if (evt.target === everything) {
                fields.forEach(function (p) { p.checked = everything.checked; });
            }
            align();
        });

        onward.addEventListener("click", function () {
            var picked = selected();
            if (!picked.length) { return; }
            list.textContent = "";
            var playback = 0;
            picked.forEach(function (inputField) {
                playback += parseInt(inputField.dataset.plays || "0", 10) || 0;
                var row = inputField.closest(".delete-row");
                var li = document.createElement("li");
                li.appendChild(recapItem("", row.querySelector("strong").textContent,
                    rowNote(row)));
                list.appendChild(li);
            });
            summary.textContent = summary.dataset.template
                .replace("{t}", picked.length.toLocaleString(language))
                .replace("{n}", playback.toLocaleString(language));
            showStep(true);
        });
        /* On returning to the page the browser can restore the ticks -
           the count on the button has to be right at once. */
        align();
    });

    /* A click on the dimmed backdrop closes. The browser reports a click on
       the <dialog> itself even when you miss its content -
       that is why the click's target is compared. And where: the window's padding
       and the gaps between its parts are the <dialog> too, and a text selection
       dragged out of the window ends with a click on it (the common parent of where
       it started and ended). So only a click that both started and ended outside
       the window's box closes it. */
    var pressedOnBackdrop = false;
    function outsideBox(dialog, event) {
        var box = dialog.getBoundingClientRect();
        return event.clientX < box.left || event.clientX > box.right
            || event.clientY < box.top || event.clientY > box.bottom;
    }
    document.addEventListener("pointerdown", function (event) {
        pressedOnBackdrop = event.target.tagName === "DIALOG" && outsideBox(event.target, event);
    });
    document.addEventListener("click", function (event) {
        if (event.target.tagName === "DIALOG" && pressedOnBackdrop && outsideBox(event.target, event)) {
            event.target.close();
        }
        pressedOnBackdrop = false;
    });

    /* The history filter: the fields for exact dates show only when
       a person picks "custom period". Without JavaScript they are visible
       too - `hidden` is removed only here, so nobody can end up
       without the option to enter a date. */
    (function () {
        var choice = document.querySelector("select[data-period]");
        if (!choice) { return; }
        var field = document.querySelectorAll(".filter-custom");

        function align() {
            field.forEach(function (element) {
                element.hidden = choice.value !== "custom";
            });
        }
        choice.addEventListener("change", align);
        align();
    })();

    /* A filter inside a window. The window's content is already on the page, so nothing
       loads - only rows that do not match hide. That is exactly why
       the filter is "only in the window": closing makes it vanish and it is saved nowhere. */
    document.addEventListener("change", function (event) {
        var choice = event.target.closest ?
            event.target.closest("[data-filter-modal]") : null;
        if (!choice) { return; }

        var modal = document.getElementById(choice.dataset.filterModal);
        if (!modal) { return; }

        modal.querySelectorAll(".modal-body [data-user]").forEach(function (row) {
            row.hidden = choice.value !== "" &&
                           row.dataset.user !== choice.value;
        });
    });

    switchableCard("daily-card", "/video/partials/daily", "kind");
    switchableCard("top-items-card", "/video/partials/top-items", "top_kind");
})();

// ------------------------------------------------------------------
// Settings -> Tasks: "Zapomenout" (forget) a viewer
//
// First a confirmation window with the chosen viewer's name, only from it is
// the form submitted. It used to be onclick= in the tag - and when the function
// was once missing, the form went off without a question and the viewer's history vanished.
// ------------------------------------------------------------------
(function () {
    if (!document.querySelector('[data-action="forget-viewer"]')) { return; }
    document.addEventListener("click", function (event) {
        var button = event.target.closest && event.target.closest('[data-action="forget-viewer"]');
        if (!button) { return; }
        var v = document.getElementById('forget_viewer');
        if (!v || !v.value) { return; }
        document.getElementById('whom-to-forget').textContent = v.options[v.selectedIndex].text.trim();
        document.getElementById('modal-forget').showModal();
    });
})();

})();
