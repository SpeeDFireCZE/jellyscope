// Settings -> Accounts.
// Used to be inline in the template settings/accounts.html; moved with its logic unchanged (CSP).

/* The tree locks while a group is chosen - ticking something that will
   not be saved would confuse. Without the script it stays unlocked and the switch decides. */
document.querySelectorAll("form.account-permissions").forEach(function (form) {
    var tree = form.querySelector(".permissions-tree");
    var anonymise = form.querySelector('input[name="anonymise"]');
    function update() {
        var custom = form.querySelector('input[name="mode"]:checked');
        var locked = !(custom && custom.value === "custom");
        tree.classList.toggle("locked", locked);
        tree.querySelectorAll("input").forEach(function (i) { i.disabled = locked; });
        anonymise.disabled = locked;
    }
    form.querySelectorAll('input[name="mode"]').forEach(function (r) {
        r.addEventListener("change", update);
    });
    update();
});
