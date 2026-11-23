// Settings -> API.
// Used to be inline in the template settings/api.html; moved with its logic unchanged (CSP).

/* With "Everything" the tree and the tick boxes hide - the key may
   do everything regardless of them. Without the script everything stays visible. */
document.querySelectorAll("form.key-permissions").forEach(function (form) {
    var chosen = form.querySelector(".permissions-selected");
    function update() {
        var mode = form.querySelector('input[name="mode"]:checked');
        chosen.hidden = !!(mode && mode.value === "all");
    }
    form.querySelectorAll('input[name="mode"]').forEach(function (r) {
        r.addEventListener("change", update);
    });
    update();
});
