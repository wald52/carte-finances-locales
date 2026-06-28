// ============================================================================
// Vision globale — diagramme de flux (Sankey) recettes / dépenses
// ----------------------------------------------------------------------------
// Page autonome (sankey.html). Lit data/sankey/aggregates-2024.json : sommes
// nationales des MONTANTS OFGL (Budget principal) par niveau, agrégat et année.
// Dessine deux Sankeys conservés (la somme des flux entrants = sortants à
// chaque nœud) : la section de FONCTIONNEMENT (compte de résultat, à la AAPL)
// et la section d'INVESTISSEMENT. Aucun framework, SVG dessiné à la main.
//
// Doctrine : montants OFGL verbatim. La vue « Toutes collectivités » (combine)
// additionne les 4 niveaux → DOUBLE COMPTAGE des flux entre collectivités
// (dotations/subventions croisées) : avertissement affiché explicitement.
// ============================================================================

// --- Chargement JSON avec repli .gz → .json (recopié de app.js, cf. CLAUDE.md
//     §13 ; on évite tout couplage au monolithe app.js). ------------------
async function loadJson(url) {
  if (typeof DecompressionStream !== "undefined") {
    try {
      const res = await fetch(url + ".gz");
      if (res.ok && res.body) {
        const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
        return await new Response(stream).json();
      }
    } catch (_e) {
      // repli sur le .json brut ci-dessous
    }
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} pour ${url}`);
  return res.json();
}

// --- Formatage des montants (présentation : Md € / M € / k €). --------------
const NF1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
const NF0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });

function formatMontant(v) {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return NF1.format(v / 1e9) + " Md €";
  if (a >= 1e6) return NF0.format(v / 1e6) + " M €";
  if (a >= 1e3) return NF0.format(v / 1e3) + " k €";
  return NF0.format(v) + " €";
}

// ----------------------------------------------------------------------------
// État + données
// ----------------------------------------------------------------------------
const LEVELS = [
  { id: "communes", label: "Communes" },
  { id: "intercommunalites", label: "Intercommunalités" },
  { id: "departements", label: "Départements" },
  { id: "regions", label: "Régions" },
  { id: "combine", label: "Toutes collectivités" },
];

const state = {
  data: null, // { years, source, levels }
  level: "combine",
  yearIndex: 0, // indice dans data.years
};

const SVGNS = "http://www.w3.org/2000/svg";

// Couleurs (cohérentes avec la charte + l'image fournie).
const C = {
  revenue: "#4a7fb5", // bleu recettes (proche de --accent éclairci)
  revenueNode: "#2c5282", // --accent
  cost: "#c0504d", // rouge dépenses
  costNode: "#9e3b39",
  saving: "#4f9d69", // vert épargne / résultat
  savingNode: "#357a4c",
};

// Accès montant : data.levels[level][agregat][yearIndex] (null si absent).
function val(ag) {
  const lv = state.data.levels[state.level];
  if (!lv) return null;
  const arr = lv[ag];
  return arr ? arr[state.yearIndex] : null;
}
function num(ag) {
  const v = val(ag);
  return typeof v === "number" ? v : 0;
}

// ----------------------------------------------------------------------------
// Modèles de flux (nœuds + liens), conservés par construction
// ----------------------------------------------------------------------------
// Section de fonctionnement (le « compte de résultat ») :
//   sources de recettes → Recettes de fonctionnement → { Dépenses de
//   fonctionnement (→ postes), Épargne brute }.
function modelFonctionnement() {
  const RF = num("Recettes de fonctionnement");
  const DF = num("Dépenses de fonctionnement");
  const EB = Math.max(RF - DF, 0); // épargne brute = RF − DF (définition OFGL)

  const srcDefs = [
    ["Impôts et taxes", "Impôts et taxes"],
    ["Concours de l'Etat", "Concours de l'État"],
    ["Subventions reçues et participations", "Subventions reçues"],
    ["Ventes de biens et services", "Ventes de biens et services"],
  ];
  const sources = srcDefs.map(([k, label]) => ({ label, value: num(k) }));
  const autresR = Math.max(RF - sources.reduce((s, x) => s + x.value, 0), 0);
  if (autresR > 0) sources.push({ label: "Autres recettes", value: autresR });

  const posteDefs = [
    ["Frais de personnel", "Frais de personnel"],
    ["Achats et charges externes", "Achats et charges externes"],
    ["Dépenses d'intervention", "Dépenses d'intervention"],
    ["Charges financières", "Charges financières"],
  ];
  const postes = posteDefs.map(([k, label]) => ({ label, value: num(k) }));
  const autresD = Math.max(DF - postes.reduce((s, x) => s + x.value, 0), 0);
  if (autresD > 0) postes.push({ label: "Autres charges", value: autresD });

  // Nœuds (id unique, col, couleur).
  const nodes = {};
  const add = (id, label, value, col, color) =>
    (nodes[id] = { id, label, value, col, color });

  sources.forEach((s, i) => add("src" + i, s.label, s.value, 0, C.revenueNode));
  add("RF", "Recettes de fonctionnement", RF, 1, C.revenueNode);
  add("DF", "Dépenses de fonctionnement", DF, 2, C.costNode);
  add("EB", "Épargne brute", EB, 2, C.savingNode);
  postes.forEach((p, i) => add("p" + i, p.label, p.value, 3, C.costNode));

  const links = [];
  sources.forEach((s, i) =>
    links.push({ from: "src" + i, to: "RF", value: s.value, color: C.revenue })
  );
  links.push({ from: "RF", to: "DF", value: DF, color: C.cost });
  links.push({ from: "RF", to: "EB", value: EB, color: C.saving });
  postes.forEach((p, i) =>
    links.push({ from: "DF", to: "p" + i, value: p.value, color: C.cost })
  );

  return { nodes, links, scale: RF, cols: 4 };
}

// Section d'investissement : ressources (épargne + recettes d'inv.) →
//   « Financement de l'investissement » → emplois (équipement, subventions
//   versées, remboursement de dette, variation des réserves).
function modelInvestissement() {
  const RF = num("Recettes de fonctionnement");
  const DF = num("Dépenses de fonctionnement");
  const EB = Math.max(RF - DF, 0);
  const recInv = num("Recettes d'investissement");
  const fctva = num("FCTVA");
  const emprunts = num("Emprunts hors GAD");
  const autresRInv = Math.max(recInv - fctva - emprunts, 0);

  const ressources = [
    { label: "Épargne brute", value: EB, color: C.savingNode, link: C.saving },
    { label: "FCTVA", value: fctva, color: C.revenueNode, link: C.revenue },
    { label: "Emprunts", value: emprunts, color: C.revenueNode, link: C.revenue },
  ];
  if (autresRInv > 0)
    ressources.push({ label: "Autres recettes d'inv.", value: autresRInv, color: C.revenueNode, link: C.revenue });
  const totalRes = ressources.reduce((s, x) => s + x.value, 0);

  const equip = num("Dépenses d'équipement");
  const subv = num("Subventions d'équipement versées");
  const remb = num("Remboursements d'emprunts hors GAD");
  const depInv = num("Dépenses d'investissement");
  const autresDInv = Math.max(depInv - equip - subv - remb, 0);
  const emplois = [
    { label: "Dépenses d'équipement", value: equip, color: C.costNode },
    { label: "Subventions d'équipement versées", value: subv, color: C.costNode },
    { label: "Remboursement de dette", value: remb, color: C.costNode },
  ];
  if (autresDInv > 0)
    emplois.push({ label: "Autres dépenses d'inv.", value: autresDInv, color: C.costNode });
  const totalEmp = emplois.reduce((s, x) => s + x.value, 0);

  // Résidu pour conserver le flux (totalRes vs totalEmp). Le surplus de
  // ressources alimente les réserves (variation du fonds de roulement) ; un
  // surplus d'emplois est financé par les réserves.
  const residu = totalRes - totalEmp;

  const nodes = {};
  const add = (id, label, value, col, color) =>
    (nodes[id] = { id, label, value, col, color });
  ressources.forEach((r, i) => add("r" + i, r.label, r.value, 0, r.color));
  const central = Math.max(totalRes, totalEmp);
  add("FIN", "Financement de l'investissement", central, 1, C.savingNode);
  emplois.forEach((e, i) => add("e" + i, e.label, e.value, 2, e.color));
  if (residu > 0) add("resv", "Réserves (var. fonds de roulement)", residu, 2, C.savingNode);
  else if (residu < 0) add("resc", "Puisé dans les réserves", -residu, 0, C.savingNode);

  const links = [];
  ressources.forEach((r, i) =>
    links.push({ from: "r" + i, to: "FIN", value: r.value, color: r.link })
  );
  if (residu < 0) links.push({ from: "resc", to: "FIN", value: -residu, color: C.saving });
  emplois.forEach((e, i) =>
    links.push({ from: "FIN", to: "e" + i, value: e.value, color: C.cost })
  );
  if (residu > 0) links.push({ from: "FIN", to: "resv", value: residu, color: C.saving });

  return { nodes, links, scale: central, cols: 3 };
}

// ----------------------------------------------------------------------------
// Moteur de rendu Sankey (générique, conservé)
// ----------------------------------------------------------------------------
const NODE_W = 13;
const COL_GAP_MIN = 230; // espace horizontal entre colonnes (pour les libellés)
const LABEL_PAD = 245; // marge à droite pour les libellés de la dernière colonne
const PAD = { top: 24, bottom: 24, left: 10, right: 10 };
const NODE_GAP = 10; // espace vertical mini entre nœuds d'une colonne

function renderSankey(svg, model, opts = {}) {
  const { nodes, links, scale, cols } = model;
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  // Échelle verticale : le plus grand total de colonne occupe la hauteur utile.
  const colTotals = [];
  for (let c = 0; c < cols; c++) colTotals[c] = 0;
  Object.values(nodes).forEach((n) => (colTotals[n.col] += n.value));
  const maxColTotal = Math.max(scale || 0, ...colTotals) || 1;

  const colCounts = [];
  for (let c = 0; c < cols; c++) colCounts[c] = 0;
  Object.values(nodes).forEach((n) => colCounts[n.col]++);
  const maxGaps = Math.max(...colCounts.map((k) => (k - 1) * NODE_GAP), 0);

  // Tous les libellés sont placés à DROITE de leur nœud (anchor start) : chaque
  // colonne dépose ses libellés dans l'espace qui la suit, donc aucune collision
  // entre colonnes (cf. l'image de référence). La dernière colonne déborde dans
  // LABEL_PAD à droite.
  const W = PAD.left + (cols - 1) * COL_GAP_MIN + NODE_W + LABEL_PAD;
  const usableH = 520;
  const H = PAD.top + PAD.bottom + usableH;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  const pxPerVal = (usableH - maxGaps) / maxColTotal;
  const colX = (c) => PAD.left + c * COL_GAP_MIN;

  // Positionnement vertical : chaque colonne centrée.
  const byCol = {};
  Object.values(nodes).forEach((n) => {
    (byCol[n.col] ||= []).push(n);
  });
  Object.keys(byCol).forEach((c) => {
    const list = byCol[c];
    const total = list.reduce((s, n) => s + n.value, 0);
    const blockH = total * pxPerVal + (list.length - 1) * NODE_GAP;
    let y = PAD.top + (usableH - blockH) / 2;
    list.forEach((n) => {
      n.x = colX(n.col);
      n.y0 = y;
      n.h = Math.max(n.value * pxPerVal, 1);
      n.y1 = y + n.h;
      n.outY = n.y0;
      n.inY = n.y0;
      y += n.h + NODE_GAP;
    });
  });

  // --- Liens (ribbons) en premier (sous les nœuds). ---
  const gLinks = document.createElementNS(SVGNS, "g");
  links.forEach((lk) => {
    const s = nodes[lk.from];
    const t = nodes[lk.to];
    if (!s || !t || !(lk.value > 0)) return;
    const h = lk.value * pxPerVal;
    const sx = s.x + NODE_W;
    const sy = s.outY;
    s.outY += h;
    const tx = t.x;
    const ty = t.inY;
    t.inY += h;
    const cx = (sx + tx) / 2;
    const d =
      `M${sx},${sy} C${cx},${sy} ${cx},${ty} ${tx},${ty} ` +
      `L${tx},${ty + h} C${cx},${ty + h} ${cx},${sy + h} ${sx},${sy + h} Z`;
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", lk.color);
    path.setAttribute("fill-opacity", "0.4");
    path.setAttribute("class", "sankey-link");
    const title = document.createElementNS(SVGNS, "title");
    title.textContent = `${s.label} → ${t.label} : ${formatMontant(lk.value)}`;
    path.appendChild(title);
    gLinks.appendChild(path);
  });
  svg.appendChild(gLinks);

  // --- Nœuds (rect + libellé). ---
  const gNodes = document.createElementNS(SVGNS, "g");
  Object.values(nodes).forEach((n) => {
    if (!(n.value > 0)) return;
    const rect = document.createElementNS(SVGNS, "rect");
    rect.setAttribute("x", n.x);
    rect.setAttribute("y", n.y0);
    rect.setAttribute("width", NODE_W);
    rect.setAttribute("height", n.h);
    rect.setAttribute("rx", "1.5");
    rect.setAttribute("fill", n.color);
    rect.setAttribute("class", "sankey-node");
    const title = document.createElementNS(SVGNS, "title");
    title.textContent = `${n.label} : ${formatMontant(n.value)}`;
    rect.appendChild(title);
    gNodes.appendChild(rect);

    // Libellé : toujours à droite du nœud (cf. W ci-dessus).
    const tx = n.x + NODE_W + 6;
    const ty = (n.y0 + n.y1) / 2;
    const label = document.createElementNS(SVGNS, "text");
    label.setAttribute("x", tx);
    label.setAttribute("y", ty);
    label.setAttribute("text-anchor", "start");
    label.setAttribute("dominant-baseline", "middle");
    label.setAttribute("class", "sankey-label");
    const name = document.createElementNS(SVGNS, "tspan");
    name.textContent = n.label;
    const amount = document.createElementNS(SVGNS, "tspan");
    amount.setAttribute("x", tx);
    amount.setAttribute("dy", "1.2em");
    amount.setAttribute("class", "sankey-amount");
    amount.textContent = formatMontant(n.value);
    label.appendChild(name);
    label.appendChild(amount);
    gNodes.appendChild(label);
  });
  svg.appendChild(gNodes);
}

// ----------------------------------------------------------------------------
// Rendu de la page
// ----------------------------------------------------------------------------
function render() {
  const yr = state.data.years[state.yearIndex];
  document.getElementById("sankey-year-value").textContent = yr;

  // Avertissement double comptage (vue combinée uniquement).
  const warn = document.getElementById("sankey-warning");
  warn.hidden = state.level !== "combine";

  // Bandeau résumé.
  const RT = num("Recettes totales");
  const DT = num("Dépenses totales");
  const summary = document.getElementById("sankey-summary");
  if (RT || DT) {
    summary.innerHTML =
      `<strong>Recettes totales</strong> ${formatMontant(RT)} ` +
      `· <strong>Dépenses totales</strong> ${formatMontant(DT)}`;
  } else {
    summary.textContent = "Aucune donnée pour ce niveau et cette année.";
  }

  renderSankey(document.getElementById("sankey-fonct"), modelFonctionnement());
  renderSankey(document.getElementById("sankey-invest"), modelInvestissement());
}

function setLevel(level) {
  state.level = level;
  document.querySelectorAll(".level-tab").forEach((b) => {
    const on = b.dataset.level === level;
    b.classList.toggle("level-tab--active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  render();
}

function setupControls() {
  // Onglets de niveau.
  document.querySelectorAll(".level-tab").forEach((b) => {
    b.addEventListener("click", () => setLevel(b.dataset.level));
  });
  // Curseur d'année.
  const slider = document.getElementById("sankey-year-slider");
  slider.min = "0";
  slider.max = String(state.data.years.length - 1);
  slider.value = String(state.yearIndex);
  slider.addEventListener("input", () => {
    state.yearIndex = parseInt(slider.value, 10);
    render();
  });
}

async function init() {
  const status = document.getElementById("sankey-status");
  try {
    state.data = await loadJson("data/sankey/aggregates-2024.json");
  } catch (e) {
    status.textContent = "Impossible de charger les données (" + e.message + ").";
    return;
  }
  status.hidden = true;
  // Année par défaut = la plus récente (2024).
  state.yearIndex = state.data.years.length - 1;
  setupControls();
  setLevel(state.level);
}

init();
