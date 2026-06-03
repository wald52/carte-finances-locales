/* ============================================================================
 * Service Worker — cache hors-ligne pour Échelons locaux
 * ----------------------------------------------------------------------------
 * Stratégie :
 *   - Pré-cache des fichiers statiques (HTML, CSS, JS) à l'installation.
 *   - Pour data/ et assets/ : cache-first avec mise en cache progressive
 *     des fichiers téléchargés (les ~25 Mo de données ne sont pas pré-cachés
 *     pour éviter une installation trop longue, mais sont gardés dès la
 *     première visite).
 *   - Le navigateur sert depuis le cache si disponible, sinon télécharge
 *     et met en cache.
 *
 * Pour invalider le cache après une mise à jour :
 *   - Bumpez la constante CACHE_NAME ci-dessous (v1 → v2).
 *   - L'ancien cache sera nettoyé à l'activation.
 * ========================================================================= */

const CACHE_NAME = "echelons-locaux-v158";

const PRECACHE_URLS = [
  "./",
  "./index.html",
  "./sources.html",
  "./assets/css/style.min.css",
  "./assets/js/app.min.js",
  "./assets/js/decoratif-worker.js",
  // PWA : manifeste + icônes (pour une installation pleinement hors-ligne)
  "./manifest.webmanifest",
  "./assets/icons/favicon.svg",
  "./assets/icons/icon-192.png",
  "./assets/icons/icon-512.png",
  "./assets/icons/icon-maskable-512.png",
  "./assets/icons/apple-touch-icon.png",
];

/** Préfixes de chemins qui doivent être mis en cache à la première requête.
 *  Les autres ressources (CDN, images externes, etc.) restent non cachées. */
const RUNTIME_CACHE_PREFIXES = ["data/", "assets/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((n) => n !== CACHE_NAME)
            .map((n) => caches.delete(n)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  // Ignorer les requêtes vers d'autres origines
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;

      return fetch(event.request).then((response) => {
        // Met en cache les réponses réussies pour les chemins ciblés
        const path = url.pathname;
        const shouldCache =
          response.ok &&
          response.status === 200 &&
          RUNTIME_CACHE_PREFIXES.some((prefix) => path.includes(prefix));

        if (shouldCache) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      });
    }),
  );
});
