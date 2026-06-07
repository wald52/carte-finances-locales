# Mesurer la performance par niveau / par indicateur

Harnais **réversible** ajouté dans `assets/js/app.js` (bloc « MESURE PERF »
juste au-dessus de `init()`). Il permet de charger **un seul niveau** (et
éventuellement **un seul indicateur**) directement via l'URL, pour lancer
Lighthouse / WebPageTest dessus **isolément** — sans découper le site en pages
séparées.

> **100 % inerte sans paramètre.** Une visite normale (`index.html` sans
> `?level=`) emprunte exactement le même chemin de chargement qu'avant. Le
> harnais ne s'active que si l'URL porte `?level=`.

---

## Relevé initial — baseline du 2026-06-07 (local, à froid)

Mesuré via ce harnais sur `scripts/serve.ps1` (localhost), **cache + service
worker purgés avant chaque niveau**. **Payload** = somme des `encodedBodySize`
= octets téléchargés à froid par un nouveau visiteur (les `.gz`, donc
représentatif de GitHub Pages). **boot→peinte** = ms entre le début d'`init()`
et la 1ʳᵉ carte peinte, sur localhost → reflète surtout le coût **CPU parse +
rendu** (réseau localhost quasi nul), hors throttling. Le LCP *throttlé*
« vérité » reste à mesurer **en ligne** (Lighthouse, cf. §4).

| Niveau | Payload à froid | boot→peinte | Poste dominant |
|---|---:|---:|---|
| **Régions** (landing) | **0,85 Mo** (dont 0,15 différé post-paint) | 94 ms | synthèse 363 Ko + SVG 187 Ko |
| **Départements** | **7,2 Mo** | 363 ms | synthèse 4,2 Mo + **SVG 2,1 Mo** |
| **Intercommunalités** | **25,7 Mo** | 1382 ms | **synthèse 22,4 Mo** |
| **Communes** (overview) | **~10,6 Mo** | 357 ms | synthèse-dpt 4,2 Mo\* + SVG-dpt 2,1 Mo + sparse 1,9 Mo + paths 1,2 Mo |
| **Syndicats** (overview) | **~8,3 Mo** | 439 ms | synthèse-dpt 4,2 Mo\* + SVG-dpt 2,1 Mo + paths 1,2 Mo |

(Communes/Syndicats : +~1,2 Mo de `decoratif-paths` chargés dans le **worker**,
non comptés côté thread principal mais inclus dans le total ci-dessus.)

\* Communes/Syndicats overview chargent la **synthèse départements (4,2 Mo) sans
l'afficher** (contours gris : seule la géométrie SVG est nécessaire) → gisement.

**Bagage présent sur chaque page** (préchargé par le HTML même hors régions) :
régions-svg 187 Ko + synthèse-régions 363 Ko (~0,55 Mo, inutiles hors régions) +
`indicators.json` 153 Ko (différé) + app.min.js 117 Ko + css ~7 Ko.

**Cibles de gain, par ordre d'impact :**
1. **Intercommunalités — 22,4 Mo de synthèse** chargée en entier pour peindre 1
   indicateur → lazy-load par indicateur (**Option C**). Gain ~−22 Mo. *Priorité 1.*
2. **Communes/Syndicats** : ne pas charger la synthèse-dpt (4,2 Mo) en overview,
   seulement le SVG. Gain ~−4,2 Mo chacun. *Quick win, indépendant de l'Option C.*
3. **SVG départements 2,1 Mo** : simplification géométrique (comme régions-svg
   déjà allégé −69 %). Touche dpts + communes + syndicats.
4. **Préchargement régions** (0,55 Mo) inutile sur une page deep-linkée non-régions.

> Les `boot→peinte` sont indicatifs (localhost, variance ±, c'est du CPU parse+rendu) ;
> le **payload est déterministe**. Pour le LCP/score throttlé réel : Lighthouse en ligne.

---

## 1. Charger un niveau précis

```
index.html?level=regions
index.html?level=departements
index.html?level=intercommunalites
index.html?level=communes
index.html?level=syndicats
```

Le niveau ciblé est chargé et rendu directement (via le chemin unifié
`switchLevel()`), sans passer par l'affichage des régions d'abord — donc le
LCP mesuré est bien celui **de ce niveau**.

Un `?level=` inconnu (faute de frappe) est ignoré avec un avertissement console
et retombe sur le chemin par défaut (régions).

## 2. Charger un indicateur précis (optionnel)

Ajouter `&ind=<clé d'indicateur>` (la clé doit être **encodée URL** car elle
contient souvent espaces et tirets) :

```
index.html?level=communes&ind=Recettes%20totales
index.html?level=intercommunalites&ind=EI%20%E2%80%94%20Recettes%20totales%2Fhab
```

C'est surtout utile pour **Communes** et **Syndicats** : changer d'indicateur y
déclenche un téléchargement *sparse* (`decoratif-values/{slug}.json.gz`) — c'est
le coût réseau propre à chaque indicateur, visible dans l'onglet réseau de
Lighthouse. Pour Régions / Départements / Intercommunalités, les valeurs de tous
les indicateurs sont déjà dans la synthèse chargée → changer d'indicateur ne
fait qu'un re-coloriage (pas de réseau).

Un `?ind=` indisponible au niveau visé est ignoré avec un avertissement.

### Lister les clés d'indicateurs d'un niveau

Dans la console du navigateur (app.js est un module, `INDICATORS` n'est pas
global — un helper est exposé sur `window`) :

```js
__indicatorKeys("communes")        // => ["Recettes totales", "Dépenses totales", ...]
__indicatorKeys("syndicats").length
```

## 3. Lire les temps de boot

Sans rien installer, le harnais journalise dans la console :

```
[perf] boot direct → niveau « communes » / indicateur « Recettes totales »
[perf] boot → carte peinte : 331 ms
[perf] indicateur appliqué : « Recettes totales »
```

Et expose le détail sur `window.__perf` :

```js
window.__perf   // => [{ étape: "boot → carte peinte", ms: 331 }]
```

`boot → carte peinte` = temps entre le début de `init()` et la première carte
peinte du niveau (fetch synthèse/décoratif + parse + rendu compris).

## 4. Lancer Lighthouse

⚠️ Rappel (cf. mémoire perf) : **le LCP n'est fidèle qu'en ligne**, pas en
localhost (artefact du simulateur Lantern). Pour des chiffres exploitables :

1. Publier (`.\scripts\publier.ps1`) — rebuild `.min`, bump SW, push.
2. Attendre ~15-20 min (propagation CDN GitHub Pages, sinon TBT/LCP gonflés).
3. Lighthouse sur les URLs **en ligne**, p. ex. :
   `https://wald52.github.io/carte-finances-locales/?level=intercommunalites`

Pour une mesure purement **comparative en local** (poids transféré, temps de
parse, `window.__perf`), le banc `.\scripts\serve.ps1` (port 8000) ou
`scripts/_perf_server.js` (réplique les en-têtes GitHub Pages, port 8125)
suffisent.

---

## 5. Retirer le harnais (réversibilité)

Tout est concentré et fencé. Pour revenir à l'état d'avant :

1. Dans `assets/js/app.js`, supprimer le bloc `// MESURE PERF — harnais
   réversible …` situé juste **au-dessus** de `async function init()`.
2. Dans `init()`, supprimer la branche `// === MESURE PERF (réversible) :
   deep-link … ===` et les 4 lignes `perfMark(…)` / `perfMeasure(…)` /
   `applyIndicatorByKey(_deep.ind)` / `console.table(window.__perf)`.
3. Régénérer les `.min` (`.\scripts\build_min.ps1`) + bumper le SW.

Aucun autre fichier n'en dépend. Rechercher `MESURE PERF` dans `app.js` retrouve
tous les points d'ancrage.
