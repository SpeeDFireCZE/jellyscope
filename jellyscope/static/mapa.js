/* Mapa na stránce Síť a na kartě přehledu.

   Přibližuje se klikáním nebo kolečkem, tažením se posouvá. Čím se
   přibližuje, si vybírá správce v Nastavení → Rozhraní a server to pošle
   v data-zoom. Kolečko je pohodlnější, jenže nad mapou přestane rolovat
   stránka - kdo kolem mapy jen projíždí, uvízne u ní. Proto je výchozí
   klikání: kolečko pak mapa vůbec neodchytává.

   Mění se jen viewBox toho SVG. Shluky spočítal server pro úrovně
   přiblížení 1×, 2×, 4× … (mapa.py, seznam v data-urovne); každá tečka
   nese, od které do které úrovně platí (data-od, data-do). Skript ukazuje
   tečky úrovně, která k přiblížení patří: z dálky se slévají, zblízka
   rozpadají až na jednotlivá místa.

   Souřadnice jsou ve Web Mercatoru, svět je čtverec 360 × 360 (data-svet);
   výchozí pohled (data-vychozi) vynechává póly. Jak daleko jde přiblížit,
   říká data-max. */
(function () {
    "use strict";

    var mapa = document.querySelector(".mapa");
    if (!mapa) { return; }

    function cisla(text, nahrada) {
        return (text || nahrada).split(" ").map(Number);
    }

    var svet = cisla(mapa.dataset.svet, "0 0 360 360");
    var vychozi = cisla(mapa.dataset.vychozi, "0 0 360 360");
    var maxPriblizeni = Number(mapa.dataset.max) || 20;
    var pohled = vychozi.slice();
    var tahne = null;

    /* Tečky i čáry se musí při přiblížení ZMENŠIT v souřadnicích mapy,
       jinak se s ní jen zvětší a dvě města vedle sebe zůstanou slitá do
       jedné skvrny. Poloměr se proto přepočítává podle toho, jak moc je
       výřez menší než výchozí pohled - na obrazovce zůstává stejný. */
    var tecky = Array.prototype.slice.call(mapa.querySelectorAll(".mapa-bod"));
    var sekundy = tecky.map(function (tecka) { return Number(tecka.dataset.s) || 0; });
    var maxima = cisla(mapa.dataset.maxima, "1");
    var uroven = 0;   /* pořadí viditelné úrovně v data-urovne */
    var hranice = mapa.querySelector(".mapa-hranice");
    var urovne = cisla(mapa.dataset.urovne, "1");

    /* Bez skriptu jsou vidět jen tečky úrovně 1× (ostatní mají
       display="none"). Se skriptem se prolínají přes průhlednost - display
       se animovat nedá. Viz .mapa.js-urovne ve style.css. */
    tecky.forEach(function (tecka) { tecka.removeAttribute("display"); });
    mapa.classList.add("js-urovne");

    /* Podklady (dlaždice OSM, podrobná mapa) se sem přihlásí a dostanou
       každou změnu výřezu. */
    var poZmene = [];

    function priblizeni() {
        return svet[2] / pohled[2];
    }

    /* Úroveň = největší, která ještě nepřesahuje přiblížení: při 3× je to
       2×, při 600× 512×. Vidět jsou tečky, které na ní platí. */
    function vyberUroven() {
        var z = priblizeni() + 1e-9;
        uroven = 0;
        urovne.forEach(function (hodnota, poradi) {
            if (hodnota <= z) { uroven = poradi; }
        });
        var hodnota = urovne[uroven];
        tecky.forEach(function (tecka) {
            tecka.classList.toggle("aktivni", Number(tecka.dataset.od) <= hodnota &&
                                              hodnota <= Number(tecka.dataset.do));
        });
    }

    function vykresli() {
        mapa.setAttribute("viewBox", pohled.join(" "));
        var pomer = pohled[2] / vychozi[2];
        vyberUroven();
        /* Velikost v rámci viditelné úrovně: největší shluk na ní má plnou
           velikost, ostatní podle odmocniny času (oko porovnává plochy).
           Stejný vzorec jako charts.mapa_sveta. */
        var nejvic = maxima[uroven] || 1;
        tecky.forEach(function (tecka, i) {
            var polomer = (1.2 + Math.sqrt(Math.min(1, sekundy[i] / nejvic)) * 4) * 2.2;
            tecka.setAttribute("r", (polomer * pomer).toPrecision(4));
        });
        if (hranice) {
            hranice.setAttribute("stroke-width", (0.16 * pomer).toPrecision(4));
            /* Hranice se NEzapínají skokem: při pohledu na celý svět by
               z nich byla jen síť čar přes kontinenty, ale jakmile se
               člověk začne přibližovat, mají postupně přibývat. */
            var sila = Math.min(1, Math.max(0, (1 - pomer) * 1.6));
            hranice.setAttribute("stroke-opacity", sila.toFixed(3));
        }
        mapa.classList.toggle("je-priblizena", pohled[2] < vychozi[2] - 0.5);
        poZmene.forEach(function (posluchac) { posluchac(pohled, priblizeni()); });
    }

    /* Ať výřez nevyjede mimo svět - jinak člověk skončí u prázdné plochy
       a neví, jak zpátky. */
    function omez() {
        pohled[0] = Math.min(Math.max(pohled[0], svet[0]), svet[0] + svet[2] - pohled[2]);
        pohled[1] = Math.min(Math.max(pohled[1], svet[1]), svet[1] + svet[3] - pohled[3]);
    }

    /* Aby přiblížení drželo bod pod kurzorem na místě, počítá se poloha
       myši v souřadnicích mapy - ne v pixelech. */
    function mistoPodMysi(event) {
        var box = mapa.getBoundingClientRect();
        return {
            x: pohled[0] + (event.clientX - box.left) / box.width * pohled[2],
            y: pohled[1] + (event.clientY - box.top) / box.height * pohled[3]
        };
    }

    /* Jedno přiblížení, ať přijde odkudkoli. `krok` menší než 1
       přibližuje, větší oddaluje; `stred` je bod v souřadnicích mapy,
       který má zůstat na místě. Dál než výchozí pohled se oddálit nedá,
       přiblížit do data-max. */
    function priblizit(krok, stred) {
        stred = stred || { x: pohled[0] + pohled[2] / 2,
                           y: pohled[1] + pohled[3] / 2 };
        var sirka = Math.min(vychozi[2],
                             Math.max(svet[2] / maxPriblizeni, pohled[2] * krok));
        var pomer = sirka / pohled[2];

        pohled[2] = sirka;
        pohled[3] = pohled[3] * pomer;
        pohled[0] = stred.x - (stred.x - pohled[0]) * pomer;
        pohled[1] = stred.y - (stred.y - pohled[1]) * pomer;
        omez();
        vykresli();
    }

    function celySvet() {
        pohled = vychozi.slice();
        vykresli();
    }

    /* Kolečko se odchytává JEN v režimu "wheel". Jinak se na ně vůbec
       neposlouchá, takže stránka nad mapou roluje dál - to je celý smysl
       té volby. */
    if (mapa.dataset.zoom === "wheel") {
        mapa.addEventListener("wheel", function (event) {
            event.preventDefault();
            priblizit(event.deltaY < 0 ? 0.8 : 1.25, mistoPodMysi(event));
        }, { passive: false });
    }

    mapa.addEventListener("pointerdown", function (event) {
        tahne = { x: event.clientX, y: event.clientY,
                  vx: pohled[0], vy: pohled[1], posunuto: false };
        mapa.setPointerCapture(event.pointerId);
    });

    mapa.addEventListener("pointermove", function (event) {
        if (!tahne) { return; }
        var box = mapa.getBoundingClientRect();
        /* Posun o pár pixelů ještě není tažení - myš ani prst nedrží při
           kliknutí úplně klidně. Bez téhle tolerance by se v režimu
           klikání skoro nikdy nepřiblížilo. */
        if (Math.abs(event.clientX - tahne.x) > 4 ||
            Math.abs(event.clientY - tahne.y) > 4) {
            tahne.posunuto = true;
        }
        pohled[0] = tahne.vx - (event.clientX - tahne.x) / box.width * pohled[2];
        pohled[1] = tahne.vy - (event.clientY - tahne.y) / box.height * pohled[3];
        omez();
        vykresli();
    });

    function pustit(event) {
        if (!tahne) { return; }
        /* Klik = stisk a puštění bez tažení. Přibližuje se ×2 na místo
           kliknutí - klik na shluk ho tak rovnou rozdělí. S Altem (nebo
           pravým tlačítkem) se oddaluje. */
        if (!tahne.posunuto && mapa.dataset.zoom !== "wheel") {
            priblizit(event.altKey || event.button === 2 ? 2 : 0.5, mistoPodMysi(event));
        }
        tahne = null;
        if (mapa.hasPointerCapture(event.pointerId)) {
            mapa.releasePointerCapture(event.pointerId);
        }
    }
    mapa.addEventListener("pointerup", pustit);
    mapa.addEventListener("pointercancel", pustit);

    /* Dvojklik vrátí výchozí pohled - jediná cesta zpátky, kterou člověk
       uhodne, aniž by ji někdo vysvětloval. */
    mapa.addEventListener("dblclick", celySvet);

    /* Tlačítka nad mapou. Jsou tam v obou režimech: kdo nechce klikat do
       mapy (a trefovat se), má plus a mínus. Krok ×2, ať je k nejbližšímu
       místu kousek: z 1× na 1024× je to deset kliknutí. */
    var obal = mapa.closest(".mapa-obal");
    if (obal) {
        obal.addEventListener("click", function (event) {
            var tlacitko = event.target.closest("[data-mapa]");
            if (!tlacitko) { return; }
            if (tlacitko.dataset.mapa === "zpet") {
                celySvet();
            } else {
                priblizit(tlacitko.dataset.mapa === "blize" ? 0.5 : 2);
            }
        });
    }

    /* Podrobná offline mapa (Natural Earth 1:10M, stahuje se na stránce
       Síť). Od 8× se místo zabudovaného obrysu berou dlaždice ze serveru:
       podrobnost 1 (síť 32 × 32, zjednodušené čáry) do 127×, podrobnost 2
       (síť 128 × 128) od 128×. Načítají se jen dlaždice výřezu; prohlížeč
       si je drží v mezipaměti a verze v adrese (?v=) zajistí, že po novém
       stažení mapy nevezme ty staré. Při zapnutém OSM se nenačítají vůbec.

       Přechody bez díry: dlaždice jiné podrobnosti zůstávají, dokud nejsou
       načtené všechny nové ve výřezu, a dokud se načítají, je pod nimi
       vidět hrubý obrys - jinak by na okamžik zbylo jen moře. */
    var vrstvaPodrobna = mapa.querySelector(".mapa-podrobna");
    var zakladni = mapa.querySelector(".mapa-podklad-zakladni");
    if (mapa.dataset.podrobna === "1" && mapa.dataset.podklad !== "osm" && vrstvaPodrobna) {
        /* Velikost sítě každé podrobnosti posílá server (mapa_podrobna). */
        var site = cisla(mapa.dataset.site, "32 128");
        var SIT = { 1: site[0], 2: site[1] };
        /* Pořadí zespodu - jezero leží na pevnině, řeka přes město. */
        var VRSTVY = ["pevnina", "jezera", "mesta", "reky", "kraje", "hranice"];
        var verze = encodeURIComponent(mapa.dataset.podrobnaVerze || "");
        /* klíč "p/x/y" -> {g: <g> nebo null, nacteno: bool, prisla: bool} */
        var podrobne = {};
        var chciPodrobne = {};
        var SVG_NS = "http://www.w3.org/2000/svg";

        function odeber(klic) {
            if (podrobne[klic].g) { vrstvaPodrobna.removeChild(podrobne[klic].g); }
            delete podrobne[klic];
        }

        /* Když je načtený celý výřez, pryč s dlaždicemi jiné podrobnosti.
           Hrubý obrys se schová, jen když aspoň jedna dlaždice opravdu
           přišla - když selžou všechny (vypršelé přihlášení, smazaná mapa),
           zůstane vidět on, ne holé moře. */
        function dokonci() {
            var klice = Object.keys(chciPodrobne);
            var hotovo = klice.every(function (klic) {
                return podrobne[klic] && podrobne[klic].nacteno;
            });
            var prisla = klice.some(function (klic) {
                return podrobne[klic] && podrobne[klic].prisla;
            });
            if (hotovo) {
                Object.keys(podrobne).forEach(function (klic) {
                    if (!chciPodrobne[klic]) { odeber(klic); }
                });
            }
            if (zakladni) { zakladni.style.display = klice.length && hotovo && prisla ? "none" : ""; }
            if (hranice) { hranice.style.display = klice.length && prisla ? "none" : ""; }
        }

        /* Odpověď patří jen záznamu, který si ji vyžádal. Když dlaždice
           mezitím odjela z výřezu a vrátila se (nový záznam, nový dotaz),
           stará odpověď se zahodí - jinak by v mapě zůstala skupina, o které
           už nikdo neví a kterou nikdo neodebere. */
        function vlozDlazdici(klic, zaznam, data) {
            if (podrobne[klic] !== zaznam) { return; }
            if (data) {
                var skupina = document.createElementNS(SVG_NS, "g");
                VRSTVY.forEach(function (vrstva) {
                    if (!data[vrstva]) { return; }
                    var cesta = document.createElementNS(SVG_NS, "path");
                    cesta.setAttribute("class", "v-" + vrstva);
                    cesta.setAttribute("d", data[vrstva]);
                    skupina.appendChild(cesta);
                });
                vrstvaPodrobna.appendChild(skupina);
                zaznam.g = skupina;
                zaznam.prisla = true;
            }
            zaznam.nacteno = true;
            dokonci();
        }

        poZmene.push(function (vyrez, z) {
            var podrobnost = z >= 128 ? 2 : (z >= 8 ? 1 : 0);
            chciPodrobne = {};
            if (podrobnost) {
                var n = SIT[podrobnost];
                var velikost = svet[2] / n;
                var x0 = Math.max(0, Math.floor(vyrez[0] / velikost));
                var x1 = Math.min(n - 1, Math.floor((vyrez[0] + vyrez[2]) / velikost));
                var y0 = Math.max(0, Math.floor(vyrez[1] / velikost));
                var y1 = Math.min(n - 1, Math.floor((vyrez[1] + vyrez[3]) / velikost));
                for (var x = x0; x <= x1; x++) {
                    for (var y = y0; y <= y1; y++) {
                        chciPodrobne[podrobnost + "/" + x + "/" + y] = true;
                    }
                }
            }
            /* Mimo výřez a ze stejné podrobnosti pryč hned; jiná podrobnost
               počká, až bude nová kompletní (dokonci). */
            Object.keys(podrobne).forEach(function (klic) {
                if (chciPodrobne[klic]) { return; }
                if (!podrobnost || klic.charAt(0) === String(podrobnost)) { odeber(klic); }
            });
            Object.keys(chciPodrobne).forEach(function (klic) {
                if (podrobne[klic]) { return; }
                var zaznam = { g: null, nacteno: false, prisla: false };
                podrobne[klic] = zaznam;
                fetch("/network/mapa/dlazdice/" + klic + "?v=" + verze, { credentials: "same-origin" })
                    .then(function (odpoved) { return odpoved.ok ? odpoved.json() : null; })
                    /* I nepovedená dlaždice je „vyřízená", ať se nečeká navždy. */
                    .then(function (data) { vlozDlazdici(klic, zaznam, data); })
                    .catch(function () { vlozDlazdici(klic, zaznam, null); });
            });
            dokonci();
        });
    }

    /* Podklad z OpenStreetMap (Nastavení → Rozhraní, výchozí vypnutý).

       Svět je v Mercatoru čtverec 360 × 360 - stejný, jaký dlaždice OSM
       dělí na 2^z × 2^z kousků, takže dlaždice (z, x, y) leží přesně na
       jednotkách x·360/2^z … (x+1)·360/2^z. Úroveň dlaždic se volí tak,
       aby jedna měla na obrazovce zhruba 256 px. Stahují se jen dlaždice
       výřezu s rezervou jedné kolem dokola. Dlaždice jiné úrovně zůstávají,
       dokud se nové ve výřezu nenačtou - jinak by při každém přiblížení
       problikla kreslená mapa.

       referrerpolicy: OSM chce vědět, odkud požadavek je (Referer), ale
       aplikace jinak ven žádný neposílá. "strict-origin" pošle jen adresu
       serveru, ne stránku (a pro prohlížeče, které atribut u SVG neznají,
       totéž říká hlavička stránky - viz web.py). Atribut se nastavuje dřív
       než adresa. Pod dlaždicemi zůstává kreslená mapa: dokud se nenačtou,
       nebo když není internet, je vidět ona. */
    var vrstvaOsm = mapa.querySelector(".mapa-dlazdice");
    if (mapa.dataset.podklad === "osm" && vrstvaOsm) {
        var dlazdice = {};   /* klíč "z/x/y" -> {img, z, hotovo} */
        var chciOsm = {};
        var SVG = "http://www.w3.org/2000/svg";

        function uklidOsm() {
            var hotovo = Object.keys(chciOsm).every(function (klic) {
                return dlazdice[klic] && dlazdice[klic].hotovo;
            });
            if (!hotovo) { return; }
            Object.keys(dlazdice).forEach(function (klic) {
                if (!chciOsm[klic]) {
                    vrstvaOsm.removeChild(dlazdice[klic].img);
                    delete dlazdice[klic];
                }
            });
        }

        poZmene.push(function (vyrez) {
            var box = mapa.getBoundingClientRect();
            if (!box.width) { return; }   /* skrytá karta - ResizeObserver to zopakuje */
            var z = Math.round(Math.log(svet[2] * box.width / vyrez[2] / 256) / Math.LN2);
            z = Math.max(0, Math.min(19, z));
            var pocet = Math.pow(2, z);
            var velikost = svet[2] / pocet;
            var x0 = Math.max(0, Math.floor(vyrez[0] / velikost) - 1);
            var x1 = Math.min(pocet - 1, Math.floor((vyrez[0] + vyrez[2]) / velikost) + 1);
            var y0 = Math.max(0, Math.floor(vyrez[1] / velikost) - 1);
            var y1 = Math.min(pocet - 1, Math.floor((vyrez[1] + vyrez[3]) / velikost) + 1);

            chciOsm = {};
            for (var x = x0; x <= x1; x++) {
                for (var y = y0; y <= y1; y++) {
                    var klic = z + "/" + x + "/" + y;
                    chciOsm[klic] = true;
                    if (dlazdice[klic]) { continue; }
                    var obrazek = document.createElementNS(SVG, "image");
                    obrazek.setAttribute("referrerpolicy", "strict-origin");
                    obrazek.setAttribute("x", x * velikost);
                    obrazek.setAttribute("y", y * velikost);
                    /* O chlup větší, ať mezi dlaždicemi nesvítí vlasové
                       spáry ze zaokrouhlení. */
                    obrazek.setAttribute("width", velikost * 1.002);
                    obrazek.setAttribute("height", velikost * 1.002);
                    obrazek.setAttribute("preserveAspectRatio", "none");
                    dlazdice[klic] = { img: obrazek, z: z, hotovo: false };
                    (function (zaznam) {
                        function vyrizeno() { zaznam.hotovo = true; uklidOsm(); }
                        zaznam.img.addEventListener("load", vyrizeno);
                        zaznam.img.addEventListener("error", vyrizeno);
                    })(dlazdice[klic]);
                    obrazek.setAttribute("href",
                        "https://tile.openstreetmap.org/" + klic + ".png");
                    vrstvaOsm.appendChild(obrazek);
                }
            }
            /* Stejná úroveň mimo výřez pryč hned, jiná až po načtení nových. */
            Object.keys(dlazdice).forEach(function (klic) {
                if (!chciOsm[klic] && dlazdice[klic].z === z) {
                    vrstvaOsm.removeChild(dlazdice[klic].img);
                    delete dlazdice[klic];
                }
            });
            uklidOsm();
        });
    }

    /* Změna velikosti okna nebo karta, která se teprve ukázala (byla
       skrytá a měla šířku 0): přepočítat úroveň dlaždic i výřez. */
    if (window.ResizeObserver) {
        new window.ResizeObserver(function () { vykresli(); }).observe(mapa);
    } else {
        window.addEventListener("resize", vykresli);
    }

    vykresli();
})();
