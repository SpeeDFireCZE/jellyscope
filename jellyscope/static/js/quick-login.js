// Sign-in with a code: asks whether somebody has confirmed the code yet.
// Used to be inline in the template login_quick.html; moved with its logic unchanged (CSP).
(function () {
    var TEXTS = JSON.parse((document.getElementById("texts-js") || {}).textContent || "{}");
    var state = document.getElementById("quick-state");
    var running = true;

    function ask() {
        if (!running) { return; }
        fetch("/login/quick/status", { credentials: "same-origin",
                                     cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { return; }
                if (data.done) {
                    running = false;
                    window.location.href = "/";
                    return;
                }
                if (data.end) {
                    running = false;
                    state.textContent = TEXTS.codeExpired;
                }
            })
            .catch(function () { /* a network outage is no reason to stop */ });
    }

    document.getElementById("quick-now").addEventListener("click", ask);
    /* Every second, not every tenth: a person confirms it in another
       app, so asking faster only loads Jellyfin. */
    setInterval(ask, 2000);
})();
