// Remplace le littéral INDICATORS (massif) dans app.js par un chargeur async
// qui lit data/indicators.json. Corrige aussi l'unique usage top-level.
// Idempotent : ne fait rien si déjà appliqué.
const fs = require("fs");
const path = require("path");
const APPJS = path.resolve(__dirname, "..", "assets", "js", "app.js");

let src = fs.readFileSync(APPJS, "utf8");

if (src.includes("async function loadIndicators(")) {
  console.log("Déjà appliqué (loadIndicators présent). Rien à faire.");
  process.exit(0);
}

const lines = src.split("\n");
let start = -1, end = -1;
for (let i = 0; i < lines.length; i++) {
  if (start < 0 && /^const INDICATORS = \[/.test(lines[i])) start = i;
  if (start >= 0 && /^\];/.test(lines[i])) { end = i; break; }
}
if (start < 0 || end < 0) { console.error("INDICATORS introuvable"); process.exit(1); }

const replacement = [
  "// ----------------------------------------------------------------------------",
  "// INDICATORS — métadonnées des ~7350 indicateurs ({key,label,unit,group,levels,help}).",
  "// Ce tableau (~4,5 Mo, 94 % de l'ancien app.js) est désormais chargé depuis",
  "// data/indicators.json au démarrage, au lieu d'être embarqué ici. Gain : app.js",
  "// passe de ~4,9 Mo à ~0,3 Mo → la compilation + l'évaluation du littéral (~200 ms",
  "// sur le thread principal, une longue tâche qui plombait LCP/TBT sous le throttling",
  "// CPU de Lighthouse mobile) disparaissent. JSON.parse est nettement moins coûteux.",
  "// MAINTENANCE : pour ajouter/modifier des indicateurs, éditer data/indicators.json",
  "// (ou régénérer via le pipeline puis re-extraire). L'ordre est préservé ;",
  "// INDICATORS[0] reste « Recettes totales » (indicateur par défaut).",
  "// ----------------------------------------------------------------------------",
  "let INDICATORS = [];",
  "async function loadIndicators() {",
  "  if (INDICATORS.length) return INDICATORS;",
  "  INDICATORS = await loadJson(\"data/indicators.json\");",
  "  return INDICATORS;",
  "}",
];

const newLines = lines.slice(0, start).concat(replacement, lines.slice(end + 1));
let out = newLines.join("\n");

// Corrige l'unique usage top-level : state.currentIndicator = INDICATORS[0]
// (INDICATORS est vide au module-eval ; on le fixe dans init() après chargement).
const before = out;
out = out.replace(
  /currentIndicator: INDICATORS\[0\],/,
  "currentIndicator: null, // défini dans init() après loadIndicators()",
);
if (out === before) {
  console.warn("ATTENTION : 'currentIndicator: INDICATORS[0],' introuvable — à vérifier à la main.");
}

fs.writeFileSync(APPJS, out);
const newSize = Buffer.byteLength(out);
console.log("Remplacé lignes", start + 1, "->", end + 1, "(", end - start + 1, "lignes) par le chargeur.");
console.log("Nouvelle taille app.js :", (newSize / 1024 / 1024).toFixed(2), "Mo");
