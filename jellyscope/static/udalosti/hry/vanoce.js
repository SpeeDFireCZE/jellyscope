/* ---------------------------------------------------------------------------
   Sezónní hra: Vánoce - dárek (načte udalosti.js po kliknutí na stromeček).

   Izometrická krabice se stuhou. Po kliknutí se mašle rozváže (kličky
   proklouznou uzlem do konců), volné konce se překlopí přes hranu víka
   a pásky padnou ven na zem - pod dárkem zůstanou přilepené jeho vahou.
   Pak se víko otevře, vyjde světlo, konfety a přání. Bez pohybu se dárek
   rovnou ukáže rozbalený.
   --------------------------------------------------------------------------- */
(function () {
    "use strict";

    function bodyNa(a) { return a.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" "); }
    function mezi(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]; }
    function posun(p, dy) { return [p[0], p[1] + dy]; }
    function zvetsi(p, stred, k) { return [stred[0] + (p[0] - stred[0]) * k, stred[1] + (p[1] - stred[1]) * k]; }
    function vzdal(a, b) { return Math.hypot(b[0] - a[0], b[1] - a[1]); }
    function polygon(body, vypln) { return '<polygon points="' + bodyNa(body) + '" fill="' + vypln + '"/>'; }
    function hladce(t) { return t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t); }
    function usek(t, od, do_) { return hladce((t - od) / (do_ - od)); }

    JellyscopeHry.registruj("vanoce", function (jeviste, api) {
        var platno = api.svg("svg", { "class": "platno", viewBox: "0 0 240 260" });
        // Krabice: střed dna, výška stěn H, výška víka V, polovina šířky stuhy W (podíl hrany).
        var stred = [120, 140], H = 66, V = 16, W = 0.09;
        var Z = [120, 104], P = [192, 140], F = [120, 176], L = [48, 140];
        var vZ = zvetsi(Z, stred, 1.08), vP = zvetsi(P, stred, 1.08), vF = zvetsi(F, stred, 1.08), vL = zvetsi(L, stred, 1.08);
        var tZ = posun(vZ, -V), tP = posun(vP, -V), tF = posun(vF, -V), tL = posun(vL, -V);
        var vrch = [120, stred[1] - V];
        var uzel = [vrch[0], vrch[1] - 4];
        function pas(a, b, h) {
            var p1 = mezi(a, b, 0.5 - W), p2 = mezi(a, b, 0.5 + W);
            return [p1, p2, posun(p2, h), posun(p1, h)];
        }
        function pasVicka(a1, b1, a2, b2) {
            return [mezi(a1, b1, 0.5 - W), mezi(a1, b1, 0.5 + W), mezi(a2, b2, 0.5 + W), mezi(a2, b2, 0.5 - W)];
        }
        // Klička mašle o velikosti s (1 = celá, 0 = proklouzla uzlem), smer -1 vlevo / 1 vpravo.
        function klicka(s, smer) {
            var k = uzel;
            var x = function (dx) { return (k[0] + dx * smer * s).toFixed(1); };
            var y = function (dy) { return (k[1] + dy * s).toFixed(1); };
            return "M" + k[0] + " " + k[1] + " C" + x(6) + " " + y(-22) + " " + x(44) + " " + y(-26) + " " + x(42) + " " + y(-8) +
                   " C" + x(40) + " " + y(8) + " " + x(12) + " " + y(4) + " " + k[0] + " " + k[1];
        }
        // Konec mašle: čím menší klička, tím delší konec (stuha se z kličky vytáhla do něj).
        function konec(g, smer) {
            return "M" + uzel[0] + " " + uzel[1] + " Q" + (uzel[0] + 10 * smer) + " " + (uzel[1] + 18) + " " +
                   (uzel[0] + (20 + g * 0.7) * smer).toFixed(1) + " " + (uzel[1] + 24 + g * 0.45).toFixed(1);
        }
        platno.innerHTML =
            '<defs>' +
            '<linearGradient id="udalost-dStenaL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d9493a"/><stop offset="1" stop-color="#ad3226"/></linearGradient>' +
            '<linearGradient id="udalost-dStenaP" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#a62c21"/><stop offset="1" stop-color="#7e1f17"/></linearGradient>' +
            '<linearGradient id="udalost-dVrch" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f4675a"/><stop offset="1" stop-color="#d23f30"/></linearGradient>' +
            '<radialGradient id="udalost-dSvetlo" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff6c8"/><stop offset=".5" stop-color="#ffd25a" stop-opacity=".7"/><stop offset="1" stop-color="#ffb000" stop-opacity="0"/></radialGradient>' +
            '<linearGradient id="udalost-dMasle" x1="0" x2="1"><stop offset="0" stop-color="#e0a812"/><stop offset=".5" stop-color="#ffe98a"/><stop offset="1" stop-color="#e0a812"/></linearGradient>' +
            '</defs>' +
            '<ellipse cx="120" cy="' + (F[1] + H + 2) + '" rx="96" ry="20" fill="#000" opacity=".35"/>' +
            '<g class="darek-cely" tabindex="0" role="button">' +
            // stuha za krabicí (zadní pruhy, které skončí na zemi za ní)
            '<g class="stuha-za" style="display:none"></g>' +
            polygon([Z, P, F, L], "#3d0b08") +
            polygon([L, Z, posun(Z, 26), posun(L, 26)], "#5a1410") +
            '<ellipse class="darek-zare" cx="120" cy="140" rx="70" ry="38" fill="url(#udalost-dSvetlo)" opacity="0"/>' +
            polygon([L, F, posun(F, H), posun(L, H)], "url(#udalost-dStenaL)") +
            polygon([F, P, posun(P, H), posun(F, H)], "url(#udalost-dStenaP)") +
            '<g class="stuha-pevna">' + polygon(pas(L, F, H), "#f2c230") + polygon(pas(F, P, H), "#c99716") + '</g>' +
            '<g class="vicko">' +
            polygon([vL, vF, tF, tL], "#c63a2c") +
            polygon([vF, vP, tP, tF], "#93271d") +
            '<g class="stuha-pevna">' + polygon(pas(tL, tF, V), "#f2c230") + polygon(pas(tF, tP, V), "#c99716") + '</g>' +
            polygon([tZ, tP, tF, tL], "url(#udalost-dVrch)") +
            '<g class="stuha-pevna">' + polygon(pasVicka(tL, tZ, tF, tP), "#ffd75a") + polygon(pasVicka(tZ, tP, tL, tF), "#ffd75a") + '</g>' +
            '</g>' +
            '<path d="M' + bodyNa([L, F]).replace(" ", " L") + '" stroke="#fff" stroke-opacity=".25" stroke-width="1.2"/>' +
            // stuha před krabicí (přední pruhy a zadní, dokud leží na víku)
            '<g class="stuha-pred" style="display:none"></g>' +
            '<g class="masle">' +
            '<path class="konec-l" d="' + konec(0, -1) + '" stroke="#e8b31e" stroke-width="7" fill="none" stroke-linecap="round"/>' +
            '<path class="konec-p" d="' + konec(0, 1) + '" stroke="#d9a51c" stroke-width="7" fill="none" stroke-linecap="round"/>' +
            '<path class="klicka-l" d="' + klicka(1, -1) + '" stroke="url(#udalost-dMasle)" stroke-width="8" fill="none" stroke-linejoin="round"/>' +
            '<path class="klicka-p" d="' + klicka(1, 1) + '" stroke="url(#udalost-dMasle)" stroke-width="8" fill="none" stroke-linejoin="round"/>' +
            '<circle class="uzel" cx="' + uzel[0] + '" cy="' + uzel[1] + '" r="8" fill="#f2c230"/>' +
            '</g>' +
            '</g>' +
            '<g class="paprsky" opacity="0"></g>';
        jeviste.appendChild(platno);
        var napoveda = document.createElement("p");
        napoveda.className = "udalost-napoveda";
        napoveda.textContent = api.texty.napoveda;
        jeviste.appendChild(napoveda);
        var darek = platno.querySelector(".darek-cely");
        darek.setAttribute("aria-label", api.texty.darek);
        // Místo pro přání je tu od začátku a prázdné - text přijde, až se
        // přání ukáže, a čtečka obrazovky ho tak ohlásí.
        var prani = document.createElement("div");
        prani.className = "udalost-prani";
        prani.setAttribute("role", "status");
        prani.style.top = "18%";
        jeviste.appendChild(prani);

        // Pruhy stuhy po rozvázání. Pod dárkem stuha drží (váha krabice),
        // volné jsou jen konce uprostřed víka. Každý pruh: kotva A (kde
        // zaleze pod krabici), nahoru stěnou (Wt), bokem víka (Ed, E) a po
        // víku ke středu. Po rozvázání se konec na víku překlopí přes hranu
        // nahoru a celý pruh pak padne ven na zem - otočí se kolem kotvy.
        function pruh(a, b, ba, bb, barva, vpredu) {
            var E = mezi(a, b, 0.5), Ed = posun(E, V), Wt = mezi(ba, bb, 0.5), A = posun(Wt, H);
            var u = [vrch[0] - E[0], vrch[1] - E[1]], lu = Math.hypot(u[0], u[1]);
            var ven = [Wt[0] - stred[0], Wt[1] - stred[1]], dv = Math.hypot(ven[0], ven[1]);
            // Šířka pásky leží podél hrany stěny (osa, kolem které se páska
            // při pádu otáčí) - spodní konec tak sedí přesně na hranu krabice.
            var hr = [bb[0] - ba[0], bb[1] - ba[1]], dh = Math.hypot(hr[0], hr[1]);
            return { A: A, Wt: Wt, Ed: Ed, E: E, u: u, lu: lu, ven: [ven[0] / dv, ven[1] / dv],
                     e: [hr[0] / dh, hr[1] / dh], w: W * dh,
                     delka: H + vzdal(Wt, Ed) + V + lu, barva: barva, vpredu: vpredu };
        }
        var PRUHY = [pruh(tL, tZ, L, Z, "#e2b52a", false), pruh(tZ, tP, Z, P, "#e2b52a", false),
                     pruh(tL, tF, L, F, "#f2c230", true), pruh(tF, tP, F, P, "#d9a51c", true)];
        // Konec na víku překlopený o úhel a (0 = leží ke středu, pi/2 = trčí nahoru).
        function preklop(p, a) {
            var C = [p.E[0] + Math.cos(a) * p.u[0], p.E[1] + Math.cos(a) * p.u[1] - Math.sin(a) * p.lu];
            return [p.A, p.Wt, p.Ed, p.E, C];
        }
        // Celý pruh odklopený od stěny o úhel f (0 = svisle nahoru, pi/2 = leží na zemi ven).
        // Měkký: prostředek trochu zaostává, pruh se při pádu prohne.
        function pad(p, f) {
            function smer(g) { return [Math.sin(g) * p.ven[0] * p.delka, -Math.cos(g) * p.delka + Math.sin(g) * p.ven[1] * p.delka]; }
            // Prohnutí je největší uprostřed pádu; na zemi pruh leží rovně.
            var cil = smer(f), ohyb = smer(f * (1 - 0.18 * Math.sin(2 * f)));
            return { A: p.A, c: [p.A[0] + ohyb[0] * 0.55, p.A[1] + ohyb[1] * 0.55], konec: [p.A[0] + cil[0], p.A[1] + cil[1]] };
        }

        var otevreny = false;
        function rozbal() {
            if (otevreny) { return; }
            otevreny = true;
            darek.style.animation = "none";
            darek.removeAttribute("tabindex");
            darek.removeAttribute("role");
            darek.removeAttribute("aria-label");
            if (document.activeElement === darek) { jeviste.focus(); }
            napoveda.textContent = "";
            var vicko = platno.querySelector(".vicko"), zare = platno.querySelector(".darek-zare");
            var paprsky = platno.querySelector(".paprsky"), masle = platno.querySelector(".masle");
            var pred = platno.querySelector(".stuha-pred"), za = platno.querySelector(".stuha-za");
            var pevne = platno.querySelectorAll(".stuha-pevna");
            var el = function (s) { return platno.querySelector(s); };
            for (var i = 0; i < 9; i++) {
                var u = -Math.PI / 2 + (i - 4) * 0.2, d = 150;
                api.svg("polygon", { points: "120,140 " + (120 + Math.cos(u - 0.05) * d).toFixed(1) + "," + (140 + Math.sin(u - 0.05) * d).toFixed(1) +
                                             " " + (120 + Math.cos(u + 0.05) * d).toFixed(1) + "," + (140 + Math.sin(u + 0.05) * d).toFixed(1),
                                     fill: "#ffe27a", opacity: (0.25 + (i % 3) * 0.12).toFixed(2) }, paprsky);
            }
            // Páska = plochý pás: středová čára posunutá o +-šířku podél hrany.
            function pasEl(rodic, barva) {
                return api.svg("polygon", { fill: barva, stroke: "#9c7410", "stroke-width": "1.2", "stroke-linejoin": "round" }, rodic);
            }
            var kresby = PRUHY.map(function (p) { return { pred: pasEl(pred, p.barva), za: pasEl(za, p.barva) }; });
            function pasBody(body, p) {
                var jedna = body.map(function (b) { return [b[0] + p.e[0] * p.w, b[1] + p.e[1] * p.w]; });
                var druha = body.map(function (b) { return [b[0] - p.e[0] * p.w, b[1] - p.e[1] * p.w]; }).reverse();
                return bodyNa(jedna.concat(druha));
            }
            function krivka(a, c, k, n) {
                var body = [];
                for (var j = 0; j <= n; j++) {
                    var t = j / n, s = 1 - t;
                    body.push([s * s * a[0] + 2 * s * t * c[0] + t * t * k[0], s * s * a[1] + 2 * s * t * c[1] + t * t * k[1]]);
                }
                return body;
            }
            // Páska v čase: nejdřív překlopení konce (k1 0..1), pak pád ven (k2 0..1).
            // Přední pásky jsou pořád před dárkem, zadní za ním.
            function vykresli(i, k1, k2) {
                var p = PRUHY[i], kr = kresby[i], tady = p.vpredu ? kr.pred : kr.za, jinde = p.vpredu ? kr.za : kr.pred;
                if (k2 <= 0) {
                    var body = preklop(p, Math.PI / 2 * k1);
                    // Zadní páska: stěna je za krabicí - před ní jen kus od hrany víka.
                    kr.pred.setAttribute("points", pasBody(p.vpredu ? body : body.slice(3), p));
                    kr.za.setAttribute("points", "");
                    return;
                }
                var g = pad(p, Math.PI / 2 * k2);
                tady.setAttribute("points", pasBody(krivka(g.A, g.c, g.konec, 16), p));
                jinde.setAttribute("points", "");
            }
            if (api.klid()) {
                [masle, vicko].forEach(function (e) { e.style.display = "none"; });
                pevne.forEach(function (e) { e.style.display = "none"; });
                pred.style.display = "";
                za.style.display = "";
                PRUHY.forEach(function (p, i) { vykresli(i, 1, 1); });
                zare.setAttribute("opacity", "1");
                paprsky.setAttribute("opacity", "1");
                prani.textContent = api.texty.prani;
                return;
            }
            var ROZVAZANI = 900, PREKLOP = 450, PAD = 650, DOPAD = 260;
            var POSUN = [160, 240, 0, 80];   // každý pruh o kousek jindy
            var start = performance.now();
            function krok(t) {
                var cas = t - start;
                // 1) Mašle: pravá klička se stáhne a její konec prodlouží, pak levá.
                var sP = 1 - usek(cas, 0, 520), sL = 1 - usek(cas, 280, 820);
                el(".klicka-p").setAttribute("d", klicka(Math.max(sP, 0.001), 1));
                el(".klicka-l").setAttribute("d", klicka(Math.max(sL, 0.001), -1));
                el(".klicka-p").style.opacity = sP > 0.03 ? 1 : 0;
                el(".klicka-l").style.opacity = sL > 0.03 ? 1 : 0;
                el(".konec-p").setAttribute("d", konec((1 - sP) * 36, 1));
                el(".konec-l").setAttribute("d", konec((1 - sL) * 36, -1));
                el(".uzel").setAttribute("r", (8 - 3 * usek(cas, 600, ROZVAZANI)).toFixed(1));
                if (cas < ROZVAZANI) { api.snimek(krok); return; }
                // 2) Rozvázáno: uzel a konce mašle jsou teď čtyři volné konce pruhů.
                if (pred.style.display === "none") {
                    pevne.forEach(function (e) { e.style.display = "none"; });
                    masle.style.display = "none";
                    pred.style.display = "";
                    za.style.display = "";
                }
                var hotovo = true;
                PRUHY.forEach(function (p, i) {
                    var c = cas - ROZVAZANI - POSUN[i];
                    var k1 = usek(c, 0, PREKLOP);
                    // Pád zrychluje (k*k) a na konci pruh krátce odskočí.
                    var tp = (c - PREKLOP) / PAD, k2 = tp <= 0 ? 0 : tp >= 1 ? 1 : tp * tp;
                    var odskok = c - PREKLOP - PAD;
                    if (odskok > 0 && odskok < DOPAD) { k2 = 1 - 0.07 * Math.sin(Math.PI * odskok / DOPAD); }
                    if (odskok < DOPAD) { hotovo = false; }
                    vykresli(i, k1, k2);
                });
                if (!hotovo) { api.snimek(krok); }
            }
            api.snimek(krok);
            // 3) Teprve rozbalený dárek se otevře.
            var OTEVRENI = ROZVAZANI + 240 + PREKLOP + PAD + DOPAD + 200;
            vicko.style.transformBox = "fill-box";
            vicko.style.transformOrigin = "30% 80%";
            vicko.animate([{ transform: "translate(0,0) rotate(0)", opacity: 1 },
                           { transform: "translate(-10px,-40px) rotate(-12deg)", opacity: 1, offset: 0.35 },
                           { transform: "translate(-90px,-170px) rotate(-40deg)", opacity: 0 }],
                          { duration: 1100, delay: OTEVRENI, easing: "cubic-bezier(.3,.6,.4,1)", fill: "forwards" });
            zare.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 600, delay: OTEVRENI + 250, fill: "forwards" });
            paprsky.style.transformBox = "fill-box";
            paprsky.style.transformOrigin = "50% 100%";
            paprsky.animate([{ opacity: 0, transform: "scaleY(.3)" }, { opacity: 1, transform: "scaleY(1)" }],
                            { duration: 700, delay: OTEVRENI + 350, easing: "ease-out", fill: "forwards" });
            prani.animate([{ transform: "translateY(90px) scale(.2)", opacity: 0 },
                           { transform: "translateY(-6px) scale(1.12)", opacity: 1, offset: 0.7 },
                           { transform: "translateY(0) scale(1)", opacity: 1 }],
                          { duration: 900, delay: OTEVRENI + 500, easing: "ease-out", fill: "both" });
            api.po(OTEVRENI + 500, function () { prani.textContent = api.texty.prani; });
            api.po(OTEVRENI + 400, function () {
                var r = platno.getBoundingClientRect(), j = jeviste.getBoundingClientRect();
                api.konfety(jeviste, r.left - j.left + r.width / 2, r.top - j.top + r.height * 0.5, 70);
            });
        }
        darek.addEventListener("click", rozbal);
        darek.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                rozbal();
            }
        });
    });
})();
