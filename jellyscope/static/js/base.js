// The basics every page needs - sign-in, the first-run wizard and the error
// page too, which do not stand on base.html and do not load app.js. Loaded
// in <head> WITHOUT `defer`: the theme has to be set before the page is
// drawn.
//
// Elements are handled declaratively here: a template writes a data-…
// attribute and this file knows what to do with it. Inline code (onclick=,
// onerror=…) is not let through by the CSP header - and it must not be: if
// it were, it would also run code smuggled into the page by a bug in how
// data are printed.
(function () {
    // Browser storage is only a convenience (a remembered theme). Where it is
    // blocked (Firefox with storage off, an extension, site data disallowed),
    // it throws or is missing altogether - and that exception must not take
    // down the rest of the file: the confirmation before deleting is
    // registered below it.
    function read(key) {
        try { return window.localStorage.getItem(key); } catch (e) { return null; }
    }
    function store(key, value) {
        try { window.localStorage.setItem(key, value); } catch (e) { /* the theme holds for this page only */ }
    }

    // The dark/light setting is read as early as possible, before the page
    // is drawn. If it were done later, the page would flash in the wrong
    // colours for a moment.
    var saved = read("jellyscope-theme");
    if (saved === "dark" || saved === "light") {
        document.documentElement.setAttribute("data-theme", saved);
    }

    function toggleTheme() {
        var root = document.documentElement;
        var current = root.getAttribute("data-theme");
        if (!current) {
            // So far the system setting decides the mode - find out which one it is
            // and switch to the other.
            current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        }
        var next = current === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        store("jellyscope-theme", next);
    }

    // An image that fails to load (a poster Jellyfin does not have). The
    // image's `error` event does not bubble, hence the capture phase (`true`) -
    // and one listener on the whole document also covers fragments inserted later.
    //   data-on-error="hide"          the image hides
    //   data-on-error="remove"        the image leaves the DOM
    //   data-on-error="hide-parent"   the frame around it hides
    //   data-on-error="fallback"      the image hides and the frame shows
    //                                  the stand-in it holds ready
    //                                  (class `no-image`, macro poster)
    document.addEventListener("error", function (event) {
        var img = event.target;
        if (!img || !img.dataset || !img.dataset.onError) { return; }
        if (img.dataset.onError === "remove") {
            img.remove();
        } else if (img.dataset.onError === "hide-parent") {
            img.parentElement.style.display = "none";
        } else if (img.dataset.onError === "fallback") {
            img.style.display = "none";
            if (img.parentElement) { img.parentElement.classList.add("no-image"); }
        } else {
            img.style.display = "none";
        }
    }, true);

    // Clicks - delegated to the document, so they also work for content
    // added later.
    document.addEventListener("click", function (event) {
        var element = event.target.closest && event.target.closest(
            "[data-close-modal], [data-close-flash], [data-toggle-theme], [data-open-modal]");
        if (!element) { return; }
        if (element.hasAttribute("data-close-modal")) {
            element.closest("dialog").close();
        } else if (element.hasAttribute("data-close-flash")) {
            element.closest(".flash-toast").remove();
        } else if (element.hasAttribute("data-toggle-theme")) {
            toggleTheme();
        } else {
            var modal = document.getElementById(element.dataset.openModal);
            if (modal) { modal.showModal(); }
        }
    });

    // A field changed: submit the form (a filter without a button), or show
    // the name of the chosen file (the file picker has its own look, the bare
    // field is hidden).
    document.addEventListener("change", function (event) {
        var field = event.target;
        if (field.hasAttribute("data-submit-on-change")) {
            // requestSubmit, not submit(): it fires the submit event, so the confirmation
            // below and the demo's guard (app.js) see the form too.
            if (field.form.requestSubmit) { field.form.requestSubmit(); } else { field.form.submit(); }
        } else if (field.hasAttribute("data-file-name")) {
            field.parentNode.querySelector(".file-name").textContent =
                field.files.length ? field.files[0].name : field.dataset.fileName;
        }
    });

    // Confirmation before an irreversible action (delete, restore, restart).
    // Registered in <head>, i.e. before the other submit listeners - the
    // same as onsubmit= in the tag used to be: the question first, then the rest.
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (form.dataset && form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
            event.preventDefault();
        }
    });
})();
