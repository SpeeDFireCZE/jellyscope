// Settings -> Log.
// Used to be inline in the template settings/log.html; moved with its logic unchanged (CSP).

    // Scroll to the end: the newest line is the one a person comes here
    // for. Without this they would be looking at the oldest one after opening.
    (function () {
        var box = document.getElementById("log-view");
        if (box) { box.scrollTop = box.scrollHeight; }
    })();
