/* ============================================================================
 * Web Worker — chargement des contours SVG du décoratif communes
 * ----------------------------------------------------------------------------
 * Architecture LAZY-LOADING :
 *   1. Au démarrage, ce worker charge UNIQUEMENT decoratif-paths-2024.json
 *      (contours SVG des 35 000 communes, ~100 Mo).
 *   2. Les valeurs des indicateurs sont chargées à la demande côté main
 *      thread via fetch direct (decoratif-values/{slug}.json, ~1-3 Mo par
 *      indicateur), pas par ce worker.
 *
 * Cette séparation évite l'OOM Chrome qui se produisait avec un décoratif
 * monolithique de 500+ Mo contenant 300+ indicateurs (~80 millions de
 * cellules en mémoire JS après parsing).
 *
 * Communication avec le main thread :
 *   - main → worker : postMessage({ url })
 *   - worker → main : postMessage({ entities, years }) ou postMessage({ error })
 *
 * Entities renvoyées : objets avec `svg.d` et `data.values = {}` vide. Le
 * main thread injecte ensuite les séries au fur et à mesure que l'utilisateur
 * sélectionne des indicateurs.
 * ========================================================================= */

/** Charge un JSON servi compressé (`.json.gz`, décompressé via
 *  DecompressionStream) avec repli automatique sur le `.json` brut. Miroir de
 *  `loadJson()` du main thread — voir app.js pour le détail. */
async function loadGzipJson(url) {
  if (typeof DecompressionStream !== "undefined") {
    try {
      const res = await fetch(url + ".gz");
      if (res.ok && res.body) {
        const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
        return await new Response(stream).json();
      }
    } catch (_e) {
      // bascule sur le `.json` brut ci-dessous
    }
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} sur ${url}`);
  return res.json();
}

self.addEventListener("message", async (ev) => {
  const url = ev.data && ev.data.url;
  if (!url) {
    self.postMessage({ error: "URL manquante" });
    return;
  }

  try {
    const data = await loadGzipJson(url);
    const years = data.years || [];
    const paths = data.paths || [];

    // Construction d'entités minimales : juste id + svg + slot vide pour
    // les valeurs. Les valeurs sont injectées plus tard par le main thread
    // via fetch('decoratif-values/{slug}.json').
    const entities = paths.map((d, idx) => ({
      svg: { d },
      id: idx,
      label: null,
      data: { values: {} },
    }));

    // postMessage clone les entities (structured clone). Pour ~35 000 objets
    // sans valeurs, c'est ~50-100 ms.
    self.postMessage({ entities, years });
  } catch (err) {
    self.postMessage({ error: err.message || String(err) });
  }
});
