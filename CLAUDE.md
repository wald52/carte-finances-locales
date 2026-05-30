# Échelons locaux — handover pour une nouvelle session Claude

Ce fichier te donne tout le contexte pour reprendre le projet à froid.
Lis-le en entier avant de toucher au code. Le projet a un historique
décisionnel important : beaucoup de choix sont contre-intuitifs au
premier abord et ont une raison.

---

## 1. Vue d'ensemble

**Site statique GitHub Pages** (vanilla HTML/CSS/JS, zéro framework) qui
visualise les indicateurs financiers des collectivités locales françaises
sur la période 2012-2024 (selon les niveaux). Carte de France
interactive avec choix de niveau (Régions / Départements / Intercommunalités
/ Communes / Syndicats) et choix d'indicateur (~1000 indicateurs).

**Doctrine fondamentale (à respecter strictement)** : **fidélité maximale
à la donnée OFGL/BANATIC**. Pas de synthèse, pas d'agrégation inventée,
pas de transformation sauf si elle est documentée comme purement de
présentation (passage € → €/hab, regroupement de fonctions M52/M57 pour
stabilité temporelle). Si OFGL ne publie pas, on n'invente pas. Si une
zone reste grise, on l'assume et on documente pourquoi.

L'utilisateur est un débutant en code mais expert métier ; il valide les
trade-offs lui-même, on lui propose des options claires.

---

## 2. Stack et arborescence

```
gestion des échelons locaux/
├── index.html              # carte principale
├── sources.html            # méthodologie + précautions de lecture
├── sw.js                   # Service Worker — bump CACHE_NAME à chaque release
├── assets/
│   ├── css/style.css       # styles
│   └── js/app.js           # 21k lignes, MONOLITHE — tout le rendu carto
├── data/                   # données générées (gros ~1 Go avec by-dep et by-epci)
│   ├── regions/            # synthese-regions-2024.json + svg + base brute
│   ├── departements/       # synthese-departements-2024.json + svg + base brute
│   ├── intercommunalites/  # synthese-intercommunalites-2024.json + by-epci/ + by-region/
│   │                       # + ei-details/{siren}.json (décompo ofgl-base-ei, lazy)
│   ├── communes/           # synthese-communes-2024.json + by-dep/ + decoratif-paths-2024.json
│   │                       # + decoratif-values/{slug}.json (lazy)
│   │                       # + meta-communes-2024.json (nom + insee + dep_code + siren_epci + siren_ept)
│   ├── syndicats/          # syndicats-2024.json (43 Mo) + leaderboards/{slug}.json
│   │                       # + decoratif-values/{slug}.json (sparse) + details/{siren}.json
│   ├── ccas/               # SIREN → INSEE mapping
│   └── banatic/            # cache parsed BANATIC XLSX
├── scripts/                # Python (UTF-8 stdout forcé pour Windows)
│   ├── fetch_*.py          # download + transform OFGL/BANATIC datasets
│   ├── build_*.py          # post-process pour fichiers spécifiques au site
│   └── enrich_*.py         # ajustements ciblés sur des fichiers existants
├── TODO.md                 # chantiers reportés (lire avant d'attaquer un sujet)
└── README.md               # README utilisateur final
```

---

## 3. Architecture des niveaux et modes

Le `state` JS (dans `assets/js/app.js`) tient :

```js
state.currentLevel = "regions" | "departements" | "intercommunalites" | "communes" | "syndicats"

// Modes par niveau (overview = vue de la France, drilldown = zoom sur un territoire)
state.communesMode = "overview" | "drilldown"
state.drillDownDepName            // nom du dpt courant en drill-down communes

state.intercommunalitesMode = "overview" | "drilldown"
state.drillDownRegCode            // région courante en drill-down EPCI

state.syndicatsMode = "overview" | "drilldown"
state.drillDownSyndDepCodes       // array : ["67A"] ou ["2A","2B"]
state.selectedSyndicatSiren       // SIREN du syndicat sélectionné dans le panel détail
```

**Helpers communs drill-down** (refactor récent, ne pas casser) :
- `_computeDrillDownViewBox(bbox)` : marge 8 % / 4 unités
- `_applyDrillDownView({entities, bbox, years, byId})` : remplace currentEntities + hide decorative + zoom + render
- `_applyOverviewView({entities, years, byId})` : restore overview + show decorative + render
- `_setCurrentEntities(entities, byId)`

Les 3 paires `enterDrillDown*` / `exitDrillDown*` (communes, EPCI, syndicats) délèguent toutes à ces helpers. **Toujours utiliser ces helpers** quand on touche au drill-down — on en a souffert plusieurs fois.

---

## 4. Le calque décoratif (35 000 communes)

C'est le mécanisme central pour les niveaux qui exposent une carte
fine (Communes overview, Intercommunalités overview, Syndicats).

- `state.decorativeEntities` : 35 000 entités commune (paths SVG + valeurs)
- `state.communesMeta` : array positionnel `[nom, insee, dep_code, dep_name, population, siren_epci, siren_ept]`. Le `siren_ept` est non-null uniquement pour les ~130 communes de Paris+petite couronne (cf. plus bas, Métropole du Grand Paris).
- Hydratation par `hydrateDecorativeWithMeta()` : enrichit chaque
  `decorativeEntities[i]` avec nom, insee, depCode, depName, sirenEpci, sirenEpt depuis le meta indexé positionnellement.
- Lazy-load par indicateur via `ensureDecorativeIndicatorLoaded(key)` :
  télécharge `data/communes/decoratif-values/{slug}.json` ou
  `data/syndicats/decoratif-values/{slug}.json` (format sparse pour
  syndicats). Mémoïsé.

**Coloration par EPCI** : le calque décoratif est colorié au niveau
intercommunalités overview en lookup `state.epciBySiren[ent.sirenEpci].values[ind.key]`.

**Fallback Paris/PC (récent et important)** : pour les communes ayant
un `sirenEpt`, la coloration tente d'abord `sirenEpci` puis fallback
sur `sirenEpt` si null. C'est ce qui permet d'afficher le FPIC pour
Plaine Commune, Est Ensemble, etc. (la MGP n'est pas un EI au sens
FPIC, seuls les 11 EPT le sont). Voir section 6.

---

## 5. Pipeline de données (Python)

L'ordre des scripts à exécuter pour un build complet from scratch :

1. **`fetch_all.py`** — base : 3 niveaux régions/dpts/communes + carto. Long (~30 min). Génère `synthese-*-2024.json`, `by-dep/`, `decoratif-*`, `meta-communes-2024.json`.
2. **`fetch_consolidees.py`** — ajoute les comptes consolidés (budgets annexes).
3. **`fetch_dotations.py`** — dotations DGCL (DSU, DSR, DGF, etc.).
4. **`fetch_epci.py`** — synthese intercommunalites + by-epci/ + siren_epci dans meta-communes.
5. **`fetch_taux_communes.py` + `fetch_taux_epci.py`** — taux d'imposition REI DGFIP.
6. **`fetch_ccas_cias.py`** — CCAS au niveau commune, CIAS au niveau EPCI.
7. **`fetch_syndicats_mdph.py`** — MDPH au niveau département (1 par dpt, 67A et 691 reconciliés).
8. **`fetch_syndicats_dedie.py`** — BANATIC + **`ofgl-base-syndicats-consolidee`** (BP + BA - flux croisés, valeurs OFGL officielles) pour ~8000 syndicats hors MDPH. Couverture 2017-2024. **Migration 2026-05** : avant cette date, utilisait `ofgl-base-syndicats` avec filtre `Budget principal` (BA ignorés). Cache : `data/syndicats/ofgl-by-agregat-consolidee/` (l'ancien `ofgl-by-agregat/` peut être supprimé). **Mise à jour 2026-05 (2ᵉ degré)** : résout les membres EPCI → communes (cf. section 6 « Syndicats de second degré ») ; produit `members` (communes résolues) + `member_groups` (membres EPCI/personne morale verbatim).
9. **`build_syndicats_decoratif.py`** — ~3983 fichiers sparse par compétence × agrégat (pour coloration carte). Clé indicateur = **compétence complète** ; slug via `synd_slug()` = `synd_{_slug(competence,50)}_{md5(competence)[:6]}__{agregat}` (anti-collision, cf. section 6). Lit `s["members"]` (inclut les communes via-EPCI).
10. **`build_syndicats_leaderboard.py`** — ~3984 fichiers leaderboard (1 ligne = 1 syndicat). **C'est aussi le générateur reproductible du bloc INDICATORS syndicats** (émet `data/_tmp_indicators_syndicats.txt`, l'ancien générateur étant perdu) : clé/label/groupe/help avec compétence complète. `synd_slug()` IDENTIQUE à celui du décoratif.
11. **`build_syndicats_details.py`** — 8055 fichiers détail par syndicat (lazy-loadé au clic). Porte `members` (communes) **et** `member_groups` (EPCI/personne morale, pour le panneau).
12. **`fetch_sdis.py`** — 57 indicateurs SDIS par dpt (67A + 691 consolidés ; Paris/petite couronne = BSPP militaire → null).
13. **`fetch_departements_fonctionnel.py`** — 105 indicateurs par fonction × agrégat (présentation fonctionnelle des comptes dpt — RSA, APA, Frais hébergement…).
14. **`fetch_fpic.py`** — 25 indicateurs FPIC au niveau EPCI (péréquation horizontale).
15. **`fetch_epl.py`** + **`insert_epl_indicators.py`** — EPL hors syndicats/CCAS/MDPH (EPA, Régies personnalisées EPIC/EPCC, GIP Autre). Source **`ofgl-base-epl-consolidee`** (BP + BA - flux croisés, valeurs OFGL officielles, ~1 450 SIREN). 1 indicateur = (activité × agrégat), agrégation géographique multi-niveau (commune/EPCI/dpt/région). **Migration 2026-05** : avant cette date, utilisait `ofgl-base-epl` (non consolidée) avec sommation manuelle BP+BA sans neutralisation des flux. Cache : `data/epl/agregats-consolidee/` (l'ancien `agregats/` peut être supprimé). Ne pas confondre avec `fetch_syndicats_dedie.py` (qui cible la nature juridique syndicat) ni avec `fetch_ccas_cias.py`.
16. **`enrich_meta_with_ept.py`** — ajoute `siren_ept` dans meta-communes pour les 130 communes Paris+PC (depuis `detail_compositions_intercommunales_2012_2024`).
17. **`fetch_actifs_communes.py`** — 13 indicateurs de patrimoine (actif réévalué) au niveau commune, source `actifs_communes_2024`. Snapshot 2024. Enrichit synthese-communes + by-dep + by-epci + `decoratif-values/patrimoine-*.json`. Paris exclu. Voir section 6.
18. **`fetch_actifs_gfp.py`** — mêmes 13 indicateurs au niveau intercommunalités, source `actifs_gfp_2024`. Snapshot 2024. Enrichit synthese-intercommunalites seulement (coloration EPCI via lookup runtime). 866/1335 EPCI couverts. Voir section 6.
19. **`fetch_extrafinanciere_departements.py`** — base EXTRA-FINANCIÈRE des départements, source `interne-base-extrafinanciere-departements` (base **interne** OFGL). 4 indicateurs (préfixe `Extra-financier — `) au niveau dpt : Effectifs collèges publics, Longueur de voirie (km), Dépenses d'équipement voirie par km, Dépenses d'équipement collèges par collégien. Données **NON issues des comptes OFGL/BANATIC** (Éducation nationale + DGCL), reprises **telles quelles** avec avertissement de fiabilité (help + `sources.html`). Enrichit synthese-departements seulement. Voir section 6.
20. **`fetch_ei.py`** + **`insert_ei_indicators.py`** — comptes consolidés des **ensembles intercommunaux** (`ofgl-base-ei`) au niveau intercommunalités. 53 agrégats (= ceux du GFP) en vue **territoire consolidé** (EPCI à FP + communes membres − flux internes neutralisés), série 2017-2024. Préfixe clés `EI — `, groupe « Ensemble intercommunal — territoire consolidé ». €/hab consolidé dans synthese-intercommunalites (coloration runtime via `epciBySiren`) ; **décomposition** structure/communes/flux/consolidé dans `data/intercommunalites/ei-details/{siren}.json` (lazy au clic, panneau détail). **Doit tourner APRÈS `fetch_epci.py`** (qui réécrit la synthese — même contrainte que `fetch_actifs_gfp.py`). 1298/1335 EPCI rattachés (GFP + 11 EPT + MGP). Cache `data/_tmp_ei.json`. Voir section 6.
21. **`fetch_criteres_epci.py`** + **`insert_criteres_indicators.py`** — base **CONTEXTE & critères** des EPCI (`interne-criteres-ei-ofgl-2021..2023` + `interne-criteres-gfp-ofgl-2020`). 12 indicateurs (préfixe `Critères — `, groupe « Contexte & critères (EPCI) », niveau intercommunalités) : 3 numériques (revenu fiscal/hab, part logements sociaux, population) + **9 catégoriels** (nature juridique, mode de financement, régime fiscal détaillé, QPV, type d'EI, outre-mer, 3 strates ordinales). Données **NON issues des comptes OFGL/BANATIC** (revenu = DGFiP, logements = RPLS/SRU, QPV = ANCT, juridique = BANATIC), reprises **telles quelles** (help + `sources.html`). EI = surensemble (couvre MGP + 11 EPT + CI) ; GFP n'apporte que 2020. **Couverture 2020-2023, pas de 2024** (array `[None]×8`, indices 3-6 ; gris à 2024 → message de couverture dans la légende). Enrichit synthese-intercommunalites seulement (coloration runtime via `epciBySiren`). **Doit tourner APRÈS `fetch_epci.py`** (même contrainte que fetch_ei/fetch_actifs_gfp). 1271/1335 EPCI enrichis (5 SIREN `type_ei=CI` = communes isolées ignorées). Cache `data/_tmp_criteres/`. Voir sections 6 & 7 (coloration catégorielle).

Tous les scripts sont **idempotents** (ré-exécutables sans casse), ont
un cache local en `data/_tmp_*.json` (supprimer pour forcer un refresh).
**Encodage stdout forcé en UTF-8** au début de chaque script (workaround
Windows cp1252).

---

## 6. Cas particuliers à connaître (chaque ligne = une heure de debug)

### Mayotte région (06)
- Mayotte n'a **pas de Conseil régional** : compétences exercées par le Département (CTU depuis 2011). OFGL ne publie **aucun** compte régional pour Mayotte (`ofgl-base-regions` filtrer Mayotte → 0 records).
- Mais OFGL publie les **EPL** (établissements publics locaux) physiquement implantés à Mayotte avec `reg_code='6'` (7 SIREN, 1324 records).
- Entité créée manuellement dans `synthese-regions-2024.json` (code `'06'`, name `'Mayotte'`, population héritée du dpt 976, values rempli par `fetch_epl.py`). Position : entre `'04'` La Réunion et `'11'` Île-de-France.
- Le SVG région inclut déjà Mayotte (`nom_reg='Mayotte'`, niveau FRA) — donc cliquable sur la carte régions.
- Sur la carte, Mayotte affiche les EPL et reste grise pour les autres indicateurs régionaux (Recettes/Dépenses/Dotations) — comportement assumé.
- **Bug historique fix 2026-05** : `fetch_epl.py` ne normalisait pas les codes région DOM (OFGL publie `'1','2','3','4','6'` sans padding alors que synthese-regions utilise `'01'..'06'`). Helper `_normalize_reg_code()` ajouté : avant le fix, les 4 DOM (Guadeloupe, Martinique, Guyane, La Réunion) avaient 0 EPL au niveau région.

### Alsace (CEA)
- Synthese dep code = **`67A`** (Collectivité européenne d'Alsace, fusion 67+68 en 2021)
- OFGL stocke encore 67 et 68 séparément dans certains datasets
- `loadCommunesForDepartement("Alsace")` doit charger le fichier consolidé `67A.json` (880 communes), pas 67.json (514 communes). Logique : on prend l'entrée du `_index.json` avec le `count` le plus élevé en cas d'ambiguïté de nom.
- Pour les indicateurs SDIS/MDPH/syndicats : code physique 67 et 68 sommés (montant) puis €/hab recalculé avec pop combinée.

### Métropole de Lyon (691) vs Rhône (69)
- 691 (Métropole de Lyon) exerce les compétences dpt depuis 2015. Existe comme entité dpt distincte de 69.
- Pour SDIS et MDPH : 691 dupliqué depuis 69 (institution unique pour les deux territoires).

### Métropole du Grand Paris (MGP) et 11 EPT
- 131 communes Paris+petite couronne ont `siren_epci = MGP (200054781)` dans meta-communes
- **MGP n'est pas un EI au sens FPIC** → pas dans `fpic-ensembles-intercommunaux`. Les 11 EPT sont les vrais EI (Plaine Commune 200057867, Est Ensemble 200057875, etc.)
- Solution implémentée : champ additionnel `siren_ept` dans meta-communes (généré par `enrich_meta_with_ept.py` depuis `detail_compositions_intercommunales_2012_2024`)
- Côté JS, **fallback** dans la coloration : `state.epciBySiren[sirenEpci].values[ind]` puis si null `state.epciBySiren[sirenEpt].values[ind]`. Zéro synthèse, lecture verbatim OFGL.
- **Click handler en drill-down EPCI région** : priorité au `sirenEpt` si présent. C'est ce qui permet à l'utilisateur de cliquer sur Plaine Commune et voir le panel EPT (et pas MGP).
- Paris (75056) : `siren_epci = MGP`, `siren_ept = null` (commune isolée). FPIC reste gris au niveau intercommunalités — c'est correct, sa donnée FPIC est dans `dotations-communes`, pas dans `fpic-ensembles-intercommunaux`. Voir TODO.md pour intégration future.

### Actif réévalué / patrimoine (actifs_communes_2024 + actifs_gfp_2024)
- Datasets OFGL **snapshot 31/12/2024 uniquement** (pas de série temporelle), même si la reconstruction patrimoniale s'appuie sur les mouvements comptables depuis 2012. 13 indicateurs identiques aux deux niveaux (Actif brut/net, €/hab, taux d'actif brut/net, taux de vétusté, dette/actif, épargne & subventions sur amortissement, DVM, DRP, dotation aux amortissements).
- Clés communes aux deux niveaux : préfixe `Patrimoine — ` (ex. `Patrimoine — Actif net/hab`), `levels: ["communes","intercommunalites"]`. Groupe sélecteur `Patrimoine (actif réévalué)`, placé après « Dette » dans `INDICATOR_GROUP_ORDER` (stock comptable, comme la dette).
- **Stockage temporel** : array positionnel `[None]×7 + valeur_2024` aligné sur `years=[2017..2024]`. La timeline reste utilisable ; balayer 2017-2023 → gris total. Choix assumé (pas de duplication fictive de la valeur 2024 sur les années antérieures).
- Scripts : `fetch_actifs_communes.py` (enrichit synthese-communes + by-dep + by-epci + 13 fichiers `decoratif-values/patrimoine-*.json`) et `fetch_actifs_gfp.py` (enrichit synthese-intercommunalites seulement — la coloration EPCI overview se fait en lookup runtime via `epciBySiren`). Idempotents (cleanup par préfixe `Patrimoine — `). Caches : `data/_tmp_actifs_communes.json`, `data/_tmp_actifs_gfp.json`.
- **Couverture communes** : 34 913 / ~34 936 décoratif. **Paris (75056) exclu par OFGL** (+ défusions) → gris assumé. Le DRP est null pour ~580 communes (calcul OFGL impossible) — donnée verbatim.
- **Couverture GFP** : **866 / 1335 EPCI**. Cas à connaître (zones grises assumées, fidélité OFGL) :
  - **Métropole de Lyon (200046977) : ABSENTE** du dataset GFP (collectivité à statut particulier, traitée au niveau dpt 691). Gris au niveau intercommunalités.
  - **EPT du Grand Paris : un seul publié** — Est Ensemble (200057875). Les 10 autres EPT + la MGP (200054781) sont absents → gris. Le fallback `epciBySiren[sirenEpt]` colorie donc uniquement les communes d'Est Ensemble parmi Paris+PC.
  - Aix-Marseille (200054807, type OFGL `MET13`) et Bordeaux : couvertes. 469 EPCI (surtout petites CC) sans donnée → gris.

### Ensembles intercommunaux — comptes consolidés (ofgl-base-ei)
- EI = EPCI à FP **+ ses communes membres**, flux croisés neutralisés. 3ᵉ vue, distincte de `ofgl-base-gfp` (la structure seule) et `ofgl-base-communes` (communes isolées). Les 53 agrégats sont **ceux du niveau GFP**, mais en valeur consolidée « territoire ». Série complète **2017-2024** (pas un snapshot).
- Clés préfixées `EI — `, **niveau intercommunalités uniquement**, groupe « Ensemble intercommunal — territoire consolidé » (après « Ratios » dans `INDICATOR_GROUP_ORDER` ; « Ratios » étant vide pour les EPCI, le groupe apparaît juste après « Trésorerie » dans le sélecteur). €/hab consolidé (OFGL `euros_par_habitant`, **verbatim**) stocké dans synthese-intercommunalites → coloration carte (lookup runtime `epciBySiren`) + courbe.
- **Décomposition** `montant_gfp` / `montant_communes` / `montant_flux` / `montant` dans `data/intercommunalites/ei-details/{siren}.json` (1 fichier/EI, lazy au clic). Rendue par `fillEiDecomposition()` (race-guard par token, mirror de `loadSyndicatDetailFile`) dans le slot `#ei-decomp-slot` du panneau, uniquement pour un EPCI **sélectionné en drill-down** et un indicateur `EI — ` courant. En overview, cliquer un EPCI dans le leaderboard zoome sur sa région (comportement existant inchangé).
- **Couverture : 1298/1335 EPCI.** Inclut **MGP (200054781) + les 11 EPT + Paris** comme EI à part entière → la zone Paris+PC est **coloriée** (≠ `actifs_gfp` où seul Est Ensemble est publié). 37 EPCI non rattachés + 4 « communes isolées » (`type_ei=CI`, SIREN de commune) ignorées → gris assumé.
- **Contrainte d'ordre** : `fetch_ei.py` enrichit la synthese EPCI, donc **doit tourner APRÈS `fetch_epci.py`** (qui réécrit `synthese-intercommunalites-2024.json` et effacerait l'enrichissement EI — même piège que `fetch_actifs_gfp.py`). Puis `insert_ei_indicators.py` injecte les 53 indicateurs dans `app.js`. Idempotents. Cache `data/_tmp_ei.json`.

### CCAS et CIAS
- CCAS = établissement communal d'action sociale → niveau **commune**
- CIAS = établissement intercommunal d'action sociale → niveau **EPCI**
- Erreur classique : OFGL `ofgl-base-ccas-cias` mélange les deux dans le même dataset. Filtrer par `categorie` (`CCAS` vs `CIAS`) puis router au bon niveau via SIREN.
- Mapping SIREN établissement → SIREN commune/EPCI parent via `recherche-entreprises.api.gouv.fr` ou via meta-communes (position 5 pour siren_epci).
- Cas Paris/Lyon/Marseille arrondissements : INSEE PLM (75101-75120, 69381-69389, 13201-13216) consolidés vers commune entière (75056, 69123, 13055).

### MDPH (Maisons Départementales des Personnes Handicapées)
- Filtre `categorie_synd = "Maison départementale des personnes handicapées (MDPH)"` dans `ofgl-base-syndicats`
- 1 MDPH par dpt en théorie (99/101 dans la donnée — Paris + petite couronne couverts via la CAF, pas dans le périmètre)
- 67A : somme MDPH 67 + MDPH 68 (Bas-Rhin + Haut-Rhin avant fusion CEA)
- 691 : duplique 69 (MDPH du Rhône partagée entre Rhône-dpt et Métropole de Lyon)
- **Données disponibles à partir de 2017 seulement** (limitation source)

### Syndicats (hors MDPH)
- BANATIC XLSX (74 Mo) + ofgl-base-syndicats. Cache parsed dans `data/banatic/banatic-parsed.json` pour éviter re-parsing.
- ~8000 syndicats, ~120 compétences (Eau potable, Crèches, Abattoirs, etc.) × ~43 agrégats financiers = ~3970 (compétence × agrégat) avec données effectives.
- Format sparse pour le décoratif : 1 fichier par (compétence × agrégat), valeurs indexées par position commune.
- 1 commune = 1 syndicat par compétence dans 95 %+ des cas (vérifié).
- Codes virtuels dans la sélection : `expandDepCodes(["67A"])` → `["67","68"]` car les `member_deps` des syndicats utilisent le préfixe INSEE physique (67xxx → "67"), pas le code synthese (67A).

### Syndicats de second degré (membres EPCI) — migration 2026-05
- Beaucoup de syndicats ont des membres qui sont des **EPCI** (BANATIC `categ = 'groupement'`) ou des **personnes morales**, pas des communes. Symptôme historique : « un chiffre OFGL mais 0 commune membre » (ex. SI du Pays de Maurienne, 5 CC membres). ~1430 syndicats purs 2ᵉ degré (0 commune directe), ~2556 avec ≥1 membre EPCI.
- **`fetch_syndicats_dedie.py`** expanse chaque membre EPCI vers **ses communes** via le mapping `siren_epci → communes` de `meta-communes` (position 5). Résultat : `members` = communes directes ∪ communes des EPCI membres (dédupliqué par INSEE, le direct prime ; chaque commune via-EPCI porte `via_epci`/`via_epci_nom`, `categ: 'commune (via EPCI)'`). Nouveau champ **`member_groups`** = membres non-communes verbatim BANATIC (EPCI/personne morale) pour l'affichage de la structure dans le panneau.
- **Pas de sur-inclusion** (vérifié sur 8055 syndicats : `members ⊆ directes ∪ communes-des-EPCI-membres`). Seuls les EPCI **à fiscalité propre** sont expansés ; un membre qui est lui-même un **syndicat** (ex. SIAEP, pas dans `siren_epci`) **n'est PAS** expansé (3ᵉ degré non traité) → ~172 syndicats / ~6714 communes laissées grises = **sous-couverture conservatrice assumée**. Les personnes morales ne s'expansent jamais (~26 bloqués).
- Côté JS : `members` (avec INSEE) alimente automatiquement decoratif/leaderboard/index inverse ; le panneau détail (`app.js`) affiche `member_groups` (« N intercommunalités membres ») + « N communes du territoire ».
- Tout reste **verbatim BANATIC** (la composition des EPCI est de la donnée, pas une invention) — transformation de présentation documentée.

### Collision collèges/lycées + troncature des compétences — migration 2026-05
- **Avant** : la clé indicateur et le slug utilisaient `competence[:60]`, le label `competence[:50]`. Deux compétences distinctes — « Construction… des **collèges** » (102 synd.) et « … des **lycées** » (85 synd.) — partageaient leurs 60 premiers caractères → **même clé + même slug** → un jeu de données **écrasait** l'autre. Les libellés longs étaient aussi coupés à l'écran.
- **Après** : compétence **complète** dans clé/label/groupe/help ; slug `synd_slug()` = `synd_{_slug(competence,50)}_{md5(competence)[:6]}__{agregat}` (hash6 = unicité garantie, vérifié 0 collision, noms de fichiers ~94 car.). Fonction **IDENTIQUE** dans `build_syndicats_decoratif.py` et `build_syndicats_leaderboard.py` (carte + classement = même nom de fichier).
- **Pièges** : (1) `build_syndicats_leaderboard.py` régénère le bloc INDICATORS (`data/_tmp_indicators_syndicats.txt`) → à réinjecter dans `app.js` (entre le bloc « Dpt fonctionnel » et le bloc « PATRIMOINE ») ; (2) `INDICATOR_GROUP_ORDER` doit lister les noms de groupe **complets** (80 entrées syndicats) — `rebuildIndicatorOptions()` **ignore silencieusement** tout groupe absent de cette liste (symptôme : options manquantes).

### Présentation fonctionnelle des dpts (M52 vs M57)
- M52 (≤ 2023) et M57 (2024+) utilisent les mêmes codes fonction (4, 5, 6, 7) avec des sémantiques **différentes**.
- M52 f5 = "Action sociale", M57 f5 = "Aménagement des territoires et habitat" — donc somme directe = faux.
- Solution dans `fetch_departements_fonctionnel.py` : mapping `CANONICAL` qui regroupe vers 7 buckets stables, dont **"Action sociale et santé"** qui fusionne M52 f4+f5 + M57 f4 (le bucket "social" reste cohérent 2012-2024).
- Préfixe `F: ` dans les clés d'indicateurs pour distinguer présentation fonctionnelle vs par nature.

### SDIS et BSPP
- 97 SDIS sur 101 dpts. **75/92/93/94 non couverts** car desservis par la **BSPP (Brigade Sapeurs-Pompiers de Paris)**, unité militaire (ministère de l'Intérieur), donc hors comptes locaux OFGL. La grisaille est correcte.
- 67A : somme SDIS 67 + SDIS 68 (deux SDIS distincts contrairement à la MDPH). Recalcul €/hab avec pop combinée.
- 691 : duplique 69 (SDMIS Rhône-Métropole partagé).
- Plusieurs budgets annexes (cantine, formation, restaurant) par SDIS — on additionne avec le budget principal.

### Recentralisation du RSA
- 5 départements ont **recentralisé** leur RSA vers l'État (l'État finance via TVA, pas le dpt) :
  - 974 La Réunion (2020), 976 Mayotte (2019), 93 Seine-Saint-Denis (2022), 66 Pyrénées-Orientales (2023), 09 Ariège (2024)
- Pour ces dpts à partir de leur année de bascule, "Allocations RSA" devient null dans OFGL → carte grise. C'est correct, pas un bug. Documenter dans le help si l'utilisateur s'en plaint.

### COM hors périmètre OFGL
- 975 Saint-Pierre-et-Miquelon, 977 Saint-Barthélemy, 978 Saint-Martin, 986 Wallis-et-Futuna, 987 Polynésie française, 988 Nouvelle-Calédonie : exclus volontairement par OFGL (régimes fiscaux propres, plans comptables distincts). Pas dans nos données. Pas de SVG. Documenté dans `sources.html` et `TODO.md`.

### Base extra-financière des départements (collèges & voirie)
- Source `interne-base-extrafinanciere-departements` : base **interne** OFGL alimentant la datastory « zoom dépenses départementales ». **Données NON issues des comptes OFGL/BANATIC** : effectifs collèges = ministère de l'Éducation nationale, longueur de voirie = fichiers dotations DGCL. Reprises **telles quelles** (doctrine fidélité) + avertissement de fiabilité dans le `help` de chaque indicateur et dans `sources.html`.
- 4 indicateurs préfixe `Extra-financier — `, niveau dpt uniquement, groupe `Extra-financier (collèges & voirie)` (placé après le bloc « Dpt fonctionnel » dans `INDICATOR_GROUP_ORDER`). Unités ajoutées à `formatValue` : `km`, `élèves`, `€/km`, `€/élève`.
- **Les 2 ratios** (`… par km`, `… par collégien`) sont une **valeur unique cumulée 2019-2024**, publiée à l'identique par l'OFGL sur chaque exercice → stockée verbatim sur 2017-2024 (courbe **plate**, pas un historique). Les 2 dénominateurs (collégiens, voirie) sont de vraies séries (collégiens 2017-2024 ; voirie 2018-2024, 2017 null à la source).
- **Codes déjà alignés** sur synthese : `67A` (CEA) et `69`/`691` distincts → **aucune consolidation** (contrairement à SDIS/MDPH). Seule normalisation (`_norm_code`) : zéro-padding `1`→`01` (la source publie les codes métropolitains à un chiffre sans zéro de tête).
- **Couverture** : 99 dpts. Outre-mer : 971/974/976 seulement — **972 Martinique et 973 Guyane absents** de la source → gris assumé. Quirks verbatim connus : Mayotte €/élève = 0 ; rupture voirie Métropole de Lyon (~490 → ~3495 km). Cache : `data/_tmp_extrafinanciere_dep.json`.

---

## 7. Conventions JS importantes

- **`state.epciBySiren`** : Map de SIREN → entité EPCI. Construite à partir de `synthese-intercommunalites-2024.json`. Source de vérité pour toutes les valeurs EPCI (y compris EPT).
- **`state.pathById`** : Map dataId → élément SVG `<path>` du calque interactif `#map__regions`. Reconstruite à chaque `renderMap()`.
- **`state.decorativePathById`** : Map dataId → path du calque décoratif `#map__decorative`. Préservée pendant les drill-down (juste `display: none` puis `display: ""` au retour).
- **`INDICATORS`** : tableau gigantesque (~1000 entrées) avec `{key, label, unit, group, levels, help}`. **Modifié par insertion massive** quand on ajoute un nouveau dataset (cf. les fetch scripts ; le snippet JS est généré côté Python via stdout, puis inséré dans `app.js` à l'endroit pertinent).
- **`INDICATOR_GROUP_ORDER`** : ordre des `<optgroup>` dans le sélecteur. À mettre à jour quand on ajoute un nouveau groupe. **Piège** : `rebuildIndicatorOptions()` **n'affiche que** les groupes présents dans cette liste — un `group` absent est **silencieusement ignoré** (symptôme : options manquantes au menu). Les 80 groupes syndicats y figurent avec leur **nom complet** (cf. section 6, collision collèges/lycées).
- **Indicateurs CATÉGORIELS** (`kind: "categorical"` + `scale: "nominal"|"ordinal"` + `categories: [{code,label}]` ; ex. groupe « Contexte & critères (EPCI) ») : la valeur stockée par année est un **code string** (pas un nombre). Coloration **DISCRÈTE** via `colorForValue()` / `categoryColorMap()` (nominal = palette qualitative `CATEGORICAL_PALETTE` ; ordinal = rampe `PALETTE` bleu→jaune) — branchée aux **4 points** de coloration : `renderDecorativeLayer` (paint initial), `applyDecorativeColors` (overview/communes), et les 2 boucles de `applyColors` (drill-down EPCI + générique). `renderLegend` → légende discrète (pastilles + effectifs) ; si l'année courante n'a aucune donnée → message de couverture (au lieu d'une carte grise muette). `renderPanel` → `buildCategoricalStrip()` (bande par année) au lieu du sparkline, et `renderCategoricalLeaderboardHTML()` (répartition + liste groupée cliquable si ≤ 400 entités) au lieu du classement numérique. `formatIndicatorValue()` renvoie le libellé de catégorie. **Couleurs côté JS** (présentation), **codes/labels côté data** (verbatim OFGL).
- **Sélecteur d'indicateur = combobox custom** (et non plus `<select>` natif) : le `<select id="indicator-select">` reste dans le DOM **masqué** (`controls__select--native-hidden`) comme **source de vérité** ; un combobox custom (`#indicator-combo`, fonctions `setupIndicatorCombobox`/`rebuildIndicatorCombobox`/`filterIndicatorCombo`/`selectIndicatorFromCombo`/`updateIndicatorComboTrigger`) le superpose pour afficher en entier les libellés longs (compétences syndicats, retour à la ligne) + recherche accent-insensible. Sélectionner une option écrit `select.value` + dispatch `change` → la logique existante (rebuild, applyColors, panel…) tourne **inchangée**. **Gotcha CSS** : la règle `.combo__panel[hidden] { display: none }` est **obligatoire** (sinon `.combo__panel { display: flex }` l'emporte sur `[hidden]` → panneau ouvert au chargement et impossible à fermer).
- **Service Worker `sw.js`** : bumper `CACHE_NAME = "echelons-locaux-vNN"` à chaque release pour invalider le cache navigateur. **Toujours bumper après une modif de app.js, css ou index.html**. Le SW fait `skipWaiting` + `clients.claim`, mais la page déjà chargée garde l'ancien CSS/JS : pour voir une modif tout de suite, faire **Ctrl+F5** (sinon ~2 rechargements normaux). Version actuelle : **v106**.

---

## 8. Workflow type pour ajouter un nouveau dataset

1. **Explorer le dataset OFGL** : `curl https://data.ofgl.fr/api/v2/catalog/datasets/<name>?include=fields` puis quelques `aggregates` pour voir variables et couverture.
2. **Écrire `scripts/fetch_<dataset>.py`** :
   - Force UTF-8 stdout (`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")`)
   - Cache local dans `data/_tmp_<dataset>.json`
   - Idempotent (nettoie les anciens enrichissements avant d'écrire)
   - Réutilise le pattern de consolidation 67A/691 si dpt-level (cf. `fetch_sdis.py` ou `fetch_syndicats_mdph.py`)
3. **Enrichir le synthese cible** (`synthese-departements-2024.json` ou `-intercommunalites-2024.json` ou `-communes-2024.json`)
4. **Générer le snippet JS d'indicateurs** depuis Python (`> data/_tmp_indicators_<dataset>.txt`) avec `{ key, label, unit, group, levels, help }`
5. **Insérer le snippet dans `app.js`** au bon endroit (script Python d'insertion qui trouve un marker `// ====` et inject avant)
6. **Mettre à jour `INDICATOR_GROUP_ORDER`** pour le nouveau groupe
7. **Bumper le SW** (`sw.js` : `CACHE_NAME` incrémenté de 1)
8. **Vérifier `node -c assets/js/app.js`** (syntax check)
9. **Recharger Ctrl+F5** côté navigateur

---

## 9. Pièges connus (déjà tombé dedans)

- **`SyntaxError` JS** sur les `help:` strings contenant des `"` non échappés → préférer `« »` français pour les guillemets internes.
- **Python `//` au lieu de `#`** dans les commentaires (copier-coller JS → Python). Vérifier avec `python -c "import scripts.foo"` avant de lancer.
- **`idx.find()` sur `_index.json` ambigu** : Alsace a 3 entrées `dep_name == "Alsace"` (codes 67, 67A, 68). `.find()` renvoie le premier (67). Toujours utiliser `.filter(...).reduce(highest count)` pour préférer le consolidé.
- **`siren_epci` int vs string** : OFGL renvoie parfois int, on stocke en string. Toujours `String(x)` avant comparaison Map.get().
- **Encoding cp1252 sur Windows** : un caractère `→` ou `é` dans un `print()` crashe sans le `TextIOWrapper`.
- **Drill-down EPCI région** : les entités viennent de `loadCommunesForEpci(siren)` qui n'a PAS `sirenEpt` par défaut. Si tu fais du drill-down sur Paris/PC, **inject `sirenEpt` via `communesMeta`** dans `loadCommunesForRegion`. Cf. la fonction actuelle qui le fait déjà.
- **Génération snippet JS depuis Python** : si la chaîne contient `"` à l'intérieur, échapper en `«` ou `'`. Toujours tester `node -c app.js` après insertion.
- **Bumper le SW** : oublier de bump = utilisateur garde la version cachée. Vérifier que `sw.js` a un `CACHE_NAME` à jour à chaque commit.

---

## 10. Décisions reportées (TODO.md)

Lire `TODO.md` à la racine. Deux gros chantiers documentés :
1. **Couverture COM** (NC, Polynésie, etc.) — bloqué par absence SVG + sources non-OFGL
2. **FPIC communes isolées (incl. Paris)** — faisable en 1-2 h, source = `dotations-communes` catégorie FPIC

---

## 11. Mantras de l'utilisateur

À garder en tête pendant toute discussion :

> « **Je veux me rapprocher le plus de la donnée.** »

> « **Tout intégrer** (~6 360 indicateurs syndicats × compétences) — pas de filtrage opportuniste. »

> « **Pas besoin de réinventer la roue sur le drill-down** » — toujours regarder s'il existe déjà un pattern pour communes/EPCI avant de coder.

> « **Il faut CCAS pour les communes et CIAS pour les EPCI** » — ne pas mélanger les niveaux.

> « **MDPH au niveau département** » — pas un niveau syndicats à part.

Quand un trade-off se présente, proposer 2-3 options explicites avec
leurs avantages/inconvénients **factuels** (pas d'avis personnel), laisser
l'utilisateur trancher. Il est rapide à décider quand le choix est clair.

---

## 12. Commandes utiles

```bash
# Servir le site localement (Python)
python -m http.server --directory . 8000

# Run un fetch ciblé
python scripts/fetch_fpic.py
python scripts/fetch_sdis.py --force   # bypass cache

# Syntax check JS après insertion
node -c "assets/js/app.js"

# Inspecter la synthese
python -c "
import json
d = json.loads(open('data/intercommunalites/synthese-intercommunalites-2024.json',encoding='utf-8').read())
print(f'{len(d[\"entities\"])} entités, {len(d[\"indicators\"])} indicateurs')
"

# Bumper le SW (à la main)
sed -i 's/echelons-locaux-v[0-9]*/echelons-locaux-vNN/' sw.js
```

Bonne chance. Ne casse pas la doctrine de fidélité aux données — c'est
ce qui fait la valeur du projet.
