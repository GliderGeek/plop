// Aankoopformulier: wijnregels met samenvatting (ingeklapt), teller, 'nog'-info en +/- knoppen.

function getal(el) {
  return parseInt((el && el.value) || "0", 10);
}

function isBasis(regel) {
  return regel.querySelector("input[type=radio][value=basis]").checked;
}

function regelNaam(regel) {
  return isBasis(regel)
    ? regel.querySelector("[name$='-vast']").value
    : regel.querySelector("[name$='-naam']").value.trim();
}

// Het aantal van een regel is de som van de verdeling.
function regelAantal(regel) {
  let som = 0;
  regel.querySelectorAll("input.verdeel").forEach((i) => (som += getal(i)));
  return som;
}

function werkTellerBij(regel) {
  const som = regelAantal(regel);
  regel.querySelector(".teller").textContent = som ? `Totaal ${som}` : "\u00a0";
}

// Ingeklapte regel: naam · aantal, plus een waarschuwing als er een wijn is maar nog niets verdeeld.
function werkSamenvattingBij(regel) {
  const som = regelAantal(regel);
  regel.querySelector(".sam-naam").textContent = regelNaam(regel) || "Nieuwe wijn";
  regel.querySelector(".sam-aantal").textContent = som || "";
  regel.querySelector(".sam-status").textContent = !som && !isBasis(regel) && regelNaam(regel) ? "niets verdeeld" : "";
}

// Categorie 'Basis': wijn kiezen uit de dropdown met basisflessen i.p.v. vrij typen.
function werkCategorieBij(regel) {
  const basis = isBasis(regel);
  regel.querySelector(".wijn-los").hidden = basis;
  regel.querySelector(".wijn-vast").hidden = !basis;
}

// Gekozen categorie, of bij basis de gekozen fles: "iets_luxer", "luxe" of "basis:<fles>".
function keuze(regel) {
  const cat = regel.querySelector("input[type=radio]:checked")?.value;
  return cat === "basis" ? `basis:${regel.querySelector("[name$='-vast']").value}` : cat;
}

// 'nog' is berekend bij het laden en bevat de opgeslagen verdeling van deze bon al (data-start).
// Eenmalig terugrekenen naar 'nog zonder deze bon'; daarna telt wat er nu in het formulier staat,
// over alle regels met dezelfde keuze (ook verwijderde of omgezette regels kloppen zo).
function nogZonderDezeBon() {
  const regels = document.getElementById("regels");
  if (regels.nogZonderBon) return regels.nogZonderBon;
  const alles = JSON.parse(regels.dataset.nog || "{}");
  const nog = {};
  for (const [cat, perVia] of Object.entries(alles)) {
    if (cat !== "basis") nog[cat] = { ...perVia };
  }
  for (const [fles, perVia] of Object.entries(alles.basis || {})) nog[`basis:${fles}`] = { ...perVia };
  regels.querySelectorAll("[data-regel]").forEach((regel) => {
    const orig = regel.dataset.origCat === "basis" ? `basis:${regel.dataset.origWijn}` : regel.dataset.origCat;
    if (!nog[orig]) return;
    regel.querySelectorAll("input.verdeel").forEach((i) => {
      nog[orig][i.dataset.via] = (nog[orig][i.dataset.via] || 0) + parseInt(i.dataset.start || "0", 10);
    });
  });
  return (regels.nogZonderBon = nog);
}

// Per via ter info hoeveel er nog nodig is voor de gekozen categorie (bij basis: de gekozen fles).
function werkNogBij(regel) {
  const k = keuze(regel);
  const nog = (k && nogZonderDezeBon()[k]) || {};
  const zelfde = [...document.querySelectorAll("[data-regel]")].filter((r) => keuze(r) === k);
  regel.querySelectorAll(".verdeel-cel").forEach((cel) => {
    const via = cel.querySelector("input.verdeel").dataset.via;
    let rest = nog[via] || 0;
    zelfde.forEach((r) => (rest -= getal(r.querySelector(`input.verdeel[data-via="${via}"]`))));
    const info = cel.querySelector(".nog-info");
    info.hidden = !(rest > 0);
    info.textContent = `nog ${rest}`;
  });
}

function werkAlleNogBij() {
  document.querySelectorAll("[data-regel]").forEach(werkNogBij);
}

// Na een wijziging in een regel: die regel bijwerken, en 'nog' overal (regels delen hun 'nog').
function werkRegelBij(regel) {
  if (!regel) return;
  werkCategorieBij(regel);
  werkTellerBij(regel);
  werkSamenvattingBij(regel);
  werkAlleNogBij();
}

function werkAlleRegelsBij() {
  if (!document.getElementById("regels")) return;
  document.querySelectorAll("[data-regel]").forEach(werkRegelBij);
}

// Er staat steeds maar één regel open; de rest is ingeklapt.
function openRegel(regel) {
  document.querySelectorAll("[data-regel].open").forEach((r) => r !== regel && r.classList.remove("open"));
  regel.classList.add("open");
  regel.scrollIntoView({ block: "nearest" });
}

document.addEventListener("click", (e) => {
  const stap = e.target.closest("[data-step]");
  if (stap) {
    const input = stap.parentElement.querySelector("input");
    input.value = Math.max(0, getal(input) + parseInt(stap.dataset.step, 10)) || "";
    werkRegelBij(stap.closest("[data-regel]"));
    return;
  }
  const open = e.target.closest("[data-open-regel]");
  if (open) {
    openRegel(open.closest("[data-regel]"));
    return;
  }
  const sluit = e.target.closest("[data-sluit-regel]");
  if (sluit) {
    sluit.closest("[data-regel]").classList.remove("open");
    return;
  }
  if (e.target.closest("#regel-toevoegen")) {
    const regels = document.getElementById("regels");
    const i = Math.max(-1, ...[...regels.querySelectorAll("[name$='-naam']")].map((el) => parseInt(el.name.split("-")[1], 10))) + 1;
    const html = document.getElementById("regel-sjabloon").innerHTML.replaceAll("__i__", i);
    regels.insertAdjacentHTML("beforeend", html);
    const nieuw = regels.lastElementChild;
    werkRegelBij(nieuw);
    openRegel(nieuw);
    return;
  }
  const weg = e.target.closest("[data-verwijder-regel]");
  if (weg) {
    const regels = document.querySelectorAll("#regels [data-regel]");
    const regel = weg.closest("[data-regel]");
    if (regels.length <= 1) return;
    const naam = regelNaam(regel);
    // lege regel zonder vragen weg; anders eerst bevestigen
    if ((naam || regelAantal(regel)) && !confirm(`${naam || "Deze wijn"} van de aankoop verwijderen?`)) return;
    regel.remove();
    werkAlleNogBij();
  }
});

document.addEventListener("input", (e) => {
  const regel = e.target.closest("[data-regel]");
  if (regel) werkRegelBij(regel);
});
document.addEventListener("DOMContentLoaded", werkAlleRegelsBij);
document.addEventListener("htmx:load", werkAlleRegelsBij);

// Foutmelding na een htmx-actie: sluit open popups zodat de melding zichtbaar is,
// en open de wijnregel waar de melding over gaat.
document.addEventListener("htmx:afterSwap", (e) => {
  if (e.detail.target.id !== "melding") return;
  document.querySelectorAll("[popover]").forEach((p) => {
    if (p.matches(":popover-open")) p.hidePopover();
  });
  const tekst = e.detail.target.textContent.toLowerCase();
  const regels = [...document.querySelectorAll("[data-regel]")];
  const fout = regels.find((r) => regelNaam(r) && tekst.includes(regelNaam(r).toLowerCase() + ":"))
            || (tekst.includes("vul bij elke regel een wijn in") && regels.find((r) => !regelNaam(r)));
  if (fout) openRegel(fout);
});

// Niet-opgeslagen wijzigingen in een formulier met data-bewaak: waarschuwen bij weggaan.
// Links lopen via htmx (hx-boost), daarom ook de klik afvangen, niet alleen beforeunload.
function onopgeslagen() {
  const form = document.querySelector("form[data-bewaak]");
  return form && form.dataset.gewijzigd === "1" && form.dataset.opslaan !== "1";
}

document.addEventListener("input", (e) => {
  const form = e.target.closest("form[data-bewaak]");
  if (form) form.dataset.gewijzigd = "1";
});
document.addEventListener("click", (e) => {
  // regel toevoegen of verwijderen is ook een wijziging
  const form = e.target.closest("form[data-bewaak]");
  if (form && e.target.closest("[data-step], #regel-toevoegen, [data-verwijder-regel]")) form.dataset.gewijzigd = "1";
});
document.addEventListener("submit", (e) => {
  if (e.target.matches("form[data-bewaak]")) e.target.dataset.opslaan = "1";
});
document.addEventListener("click", (e) => {
  const link = e.target.closest("a[href]");
  if (!link || !onopgeslagen()) return;
  if (!confirm("De aankoop is nog niet opgeslagen. Wijzigingen weggooien?")) {
    e.preventDefault();
    e.stopPropagation();
  }
}, true);
window.addEventListener("beforeunload", (e) => {
  if (onopgeslagen()) e.preventDefault();
});
// opslaan mislukt (foutmelding): formulier blijft staan, dus weer bewaken
document.addEventListener("htmx:afterSwap", (e) => {
  if (e.detail.target.id !== "melding") return;
  const form = document.querySelector("form[data-bewaak]");
  if (form) delete form.dataset.opslaan;
});

// Inklapbare secties op het overzicht: de stand blijft per browser bewaard.
// Een link naar een sectie (#verrekening) klapt die altijd open.
function herstelSecties() {
  document.querySelectorAll("details[data-sectie]").forEach((sectie) => {
    const stand = localStorage.getItem(`sectie-${sectie.dataset.sectie}`);
    if (stand) sectie.open = stand === "open";
    if (location.hash === `#${sectie.id}`) sectie.open = true;
  });
}

document.addEventListener("DOMContentLoaded", herstelSecties);
document.addEventListener("htmx:load", herstelSecties);
// 'toggle' borrelt niet op, dus afvangen in de capture-fase
document.addEventListener("toggle", (e) => {
  if (!e.target.matches?.("details[data-sectie]")) return;
  localStorage.setItem(`sectie-${e.target.dataset.sectie}`, e.target.open ? "open" : "dicht");
}, true);

// Bestelling van een klant helemaal toegekend: ontkurken, bubbels en een melding.
// Alleen op de kaart waarop getikt is, dus niet bij wijzigingen van iemand anders.
let geluid;
let plopBuffer;
let kaartVoorSwap = null;

// Speelt het geluid uit data-plop; ontbreekt het bestand, dan is het feestje gewoon stil.
function plop() {
  plopBuffer.then((buffer) => {
    if (!buffer) return;
    const bron = geluid.createBufferSource();
    bron.buffer = buffer;
    bron.connect(geluid.destination);
    bron.start();
  });
}

function vier(naam) {
  const feest = document.createElement("div");
  feest.className = "feest";
  for (let i = 0; i < 40; i++) {
    const bubbel = document.createElement("i");
    bubbel.style.left = `${Math.random() * 100}%`;
    bubbel.style.setProperty("--maat", `${6 + Math.random() * 14}px`);
    bubbel.style.animationDelay = `${Math.random() * 0.9}s`;
    feest.append(bubbel);
  }
  const tekst = document.createElement("div");
  tekst.className = "feest-tekst";
  tekst.setAttribute("role", "status");
  tekst.textContent = `🍾 ${naam} compleet!`;
  feest.append(tekst);
  document.body.append(feest);
  setTimeout(() => feest.remove(), 3000);
  if (geluid) plop();
}

// Audio mag alleen starten vanuit een tik; het antwoord van de server komt daarna.
document.addEventListener("click", (e) => {
  if (!e.target.closest(".klant[data-compleet] button")) return;
  if (!geluid) {
    geluid = new AudioContext();
    plopBuffer = fetch(document.body.dataset.plop)
      .then((r) => r.arrayBuffer())
      .then((data) => geluid.decodeAudioData(data))
      .catch(() => null);
  }
  geluid.resume();
});
document.addEventListener("htmx:beforeRequest", (e) => {
  const kaart = e.target.closest(".klant[data-compleet]");
  kaartVoorSwap = kaart?.dataset.compleet === "0" ? kaart.id : null;
});
document.addEventListener("htmx:afterSwap", () => {
  const kaart = kaartVoorSwap && document.getElementById(kaartVoorSwap);
  kaartVoorSwap = null;
  if (kaart?.dataset.compleet === "1") vier(kaart.querySelector("strong").textContent);
});

// Oogje bij het wachtwoordveld: laat zien wat je getypt hebt.
document.addEventListener("click", (e) => {
  const knop = e.target.closest("[data-toon-wachtwoord]");
  if (!knop) return;
  const veld = knop.parentElement.querySelector("input");
  const tonen = veld.type === "password";
  veld.type = tonen ? "text" : "password";
  knop.setAttribute("aria-pressed", tonen);
  knop.title = knop.ariaLabel = tonen ? "Wachtwoord verbergen" : "Wachtwoord tonen";
  veld.focus();
});

// Grafiek 'Over de jaren': tooltip bij hover en focus op een segment (data-tip). Via document,
// zodat het blijft werken nadat htmx de pagina heeft vervangen.
function toonTip(el, x, y) {
  const tip = document.getElementById("grafiek-tip");
  if (!tip) return;
  tip.textContent = el.dataset.tip;
  tip.hidden = false;
  tip.style.left = Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = y - tip.offsetHeight - 10 + "px";
}
function verbergTip() {
  const tip = document.getElementById("grafiek-tip");
  if (tip) tip.hidden = true;
}
document.addEventListener("pointermove", (e) => {
  const el = e.target.closest && e.target.closest("[data-tip]");
  el ? toonTip(el, e.clientX, e.clientY) : verbergTip();
});
document.addEventListener("focusin", (e) => {
  const el = e.target.closest && e.target.closest("[data-tip]");
  if (!el) return verbergTip();
  const r = el.getBoundingClientRect();
  toonTip(el, r.left + r.width / 2, r.top);
});
document.addEventListener("focusout", verbergTip);
