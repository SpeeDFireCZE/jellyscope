// Nastavení -> Účty.
// Dřív vložené v šabloně nastaveni/accounts.html; přesunuté beze změny logiky (CSP).

/* Strom se zamkne, dokud je zvolená skupina - zaškrtávat něco, co se
   neuloží, by mátlo. Bez skriptu zůstane odemčený a rozhoduje přepínač. */
document.querySelectorAll("form.prava-uctu").forEach(function (form) {
    var strom = form.querySelector(".prava-strom");
    var anonym = form.querySelector('input[name="anonymizace"]');
    function prepni() {
        var vlastni = form.querySelector('input[name="rezim"]:checked');
        var zamceno = !(vlastni && vlastni.value === "vlastni");
        strom.classList.toggle("zamceno", zamceno);
        strom.querySelectorAll("input").forEach(function (i) { i.disabled = zamceno; });
        anonym.disabled = zamceno;
    }
    form.querySelectorAll('input[name="rezim"]').forEach(function (r) {
        r.addEventListener("change", prepni);
    });
    prepni();
});
