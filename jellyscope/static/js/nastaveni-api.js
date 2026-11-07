// Nastavení -> API.
// Dřív vložené v šabloně nastaveni/api.html; přesunuté beze změny logiky (CSP).

/* U „Všechno" se strom a zaškrtávátka schovají - klíč smí všechno
   bez ohledu na ně. Bez skriptu zůstane vidět všechno. */
document.querySelectorAll("form.prava-klice").forEach(function (form) {
    var vybrane = form.querySelector(".prava-vybrane");
    function prepni() {
        var rezim = form.querySelector('input[name="rezim"]:checked');
        vybrane.hidden = !!(rezim && rezim.value === "vse");
    }
    form.querySelectorAll('input[name="rezim"]').forEach(function (r) {
        r.addEventListener("change", prepni);
    });
    prepni();
});
