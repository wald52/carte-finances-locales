// Extraction unique du tableau INDICATORS (94 % du poids de app.js, ~4,5 Mo de
// METADONNEES) vers data/indicators.json, pour alléger app.js (compile/éval).
// app.js charge ensuite ce JSON au démarrage (cf. loadIndicators()).
//
// Le littéral référence des constantes de niveaux (ALL_LEVELS, REG_DEP…) : on
// les définit puis on évalue le tableau dans un contexte vm isolé.
//
// Usage : node scripts/extract_indicators.js
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const APPJS = path.join(ROOT, "assets", "js", "app.js");
const OUT = path.join(ROOT, "data", "indicators.json");

const src = fs.readFileSync(APPJS, "utf8");
const lines = src.split("\n");

let start = -1, end = -1;
for (let i = 0; i < lines.length; i++) {
  if (start < 0 && /^const INDICATORS = \[/.test(lines[i])) start = i;
  if (start >= 0 && /^\];/.test(lines[i])) { end = i; break; }
}
if (start < 0 || end < 0) { console.error("INDICATORS introuvable"); process.exit(1); }

let arrText = lines.slice(start, end + 1).join("\n");
// `const INDICATORS = [...]` -> assignation au contexte pour récupérer la valeur
arrText = arrText.replace(/^const INDICATORS = /, "globalThis.__OUT = ");

const ctx = {
  ALL_LEVELS: ["regions", "departements", "intercommunalites", "communes"],
  ALL_LEVELS_NO_EPCI: ["regions", "departements", "communes"],
  REG_DEP: ["regions", "departements"],
  REG_DEP_EPCI: ["regions", "departements", "intercommunalites"],
  COM_EPCI: ["intercommunalites", "communes"],
  globalThis: {},
};
ctx.globalThis = ctx;
vm.createContext(ctx);
try {
  vm.runInContext(arrText, ctx);
} catch (e) {
  console.error("Echec eval INDICATORS :", e.message);
  process.exit(1);
}
const ind = ctx.__OUT;
if (!Array.isArray(ind)) { console.error("__OUT n'est pas un tableau"); process.exit(1); }

// Validation : chaque entrée a key/levels
let bad = 0;
const byLevel = {};
for (const x of ind) {
  if (!x || !x.key || !Array.isArray(x.levels)) bad++;
  for (const lv of (x.levels || [])) byLevel[lv] = (byLevel[lv] || 0) + 1;
}
fs.writeFileSync(OUT, JSON.stringify(ind));
const bytes = Buffer.byteLength(JSON.stringify(ind));
console.log("INDICATORS extraits :", ind.length, "entrées | invalides:", bad);
console.log("par niveau :", JSON.stringify(byLevel));
console.log("data/indicators.json :", (bytes / 1024 / 1024).toFixed(2), "Mo (brut)");
console.log("lignes app.js du tableau :", start + 1, "->", end + 1);
