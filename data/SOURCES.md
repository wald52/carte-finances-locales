# Sources des données

Ce document recense **toutes** les sources de données utilisées dans le projet, avec
leur URL d'origine, leur licence, leur périmètre et la méthodologie appliquée.

Toutes les sources sont publiques, libres d'usage et accessibles sans clé d'API.
Le script [`scripts/fetch_all.py`](../scripts/fetch_all.py) permet de regénérer
intégralement le contenu de ce dossier `data/` à partir de ces sources.

À côté de chaque dataset téléchargé se trouve un fichier `*.meta.json` qui
contient l'URL exacte de la source, la description et la date de téléchargement.

---

## Index

- [Listes administratives officielles](#listes-administratives-officielles) — INSEE / geo.api.gouv.fr
- [Comptes des collectivités](#comptes-des-collectivités) — OFGL
- [Méthodologie OFGL](#méthodologie-ofgl) — définitions et formules officielles des agrégats
- [Données cartographiques](#données-cartographiques) — contours SVG et indicateurs prêts à cartographier
- [Indicateurs calculés en interne](#indicateurs-calculés-en-interne) — formules utilisées dans nos synthèses
- [Notes méthodologiques importantes](#notes-méthodologiques-importantes)
- [Comment vérifier un chiffre par soi-même](#comment-vérifier-un-chiffre-par-soi-même)

---

## Listes administratives officielles

### Source : API Découpage administratif (`geo.api.gouv.fr`)

| Élément | Valeur |
|---|---|
| Producteur | [Etalab](https://www.etalab.gouv.fr) (mission interministérielle Open Data) |
| Source amont | INSEE — Code Officiel Géographique (COG) |
| URL de la documentation | <https://geo.api.gouv.fr/decoupage-administratif> |
| Licence | [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence) |
| Authentification | Aucune |

### Datasets utilisés

| Fichier local | URL téléchargée | Contenu |
|---|---|---|
| [`regions.json`](regions.json) | <https://geo.api.gouv.fr/regions?fields=nom,code> | 18 régions (13 métropolitaines + 5 ultra-marines) |
| [`departements.json`](departements.json) | <https://geo.api.gouv.fr/departements?fields=nom,code,codeRegion> | 101 départements (96 métro + 5 DROM) |
| [`communes.json`](communes.json) | <https://geo.api.gouv.fr/communes?fields=nom,code,codeDepartement,codeRegion,population,siren&format=json> | 34 969 communes (métro + DROM + COM) |

---

## Comptes des collectivités

### Source : Observatoire des Finances et de la Gestion publique Locales (OFGL)

| Élément | Valeur |
|---|---|
| Producteur | OFGL (créé par la loi NOTRe, 2016) |
| Tutelle | Comité des finances locales (CFL) |
| Mandat légal | Centraliser, retraiter et publier les données financières des collectivités pour les rendre comparables |
| Portail | <https://data.ofgl.fr> |
| Page institutionnelle | <https://www.collectivites-locales.gouv.fr/ofgl> |
| API documentation | <https://data.ofgl.fr/api/v1/console/datasets/1.0/search/> |
| Licence | [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence) |
| Authentification | Aucune |

L'OFGL est l'organisme officiel chargé d'agréger et d'harmoniser les comptes
administratifs des collectivités issus de la DGFiP. Les données y sont déjà
ramenées par habitant et nettoyées des effets de nomenclature comptable
(M14/M57/M71...). C'est la **source de référence** pour comparer des collectivités
entre elles.

### Datasets utilisés

| Fichier local | Dataset OFGL | Contenu |
|---|---|---|
| [`regions/ofgl-base-regions.json`](regions/ofgl-base-regions.json) | [`ofgl-base-regions`](https://data.ofgl.fr/explore/dataset/ofgl-base-regions/) | Comptes régions 2012-2024, format long, 22 933 lignes |
| [`departements/ofgl-base-departements.json`](departements/ofgl-base-departements.json) | [`ofgl-base-departements`](https://data.ofgl.fr/explore/dataset/ofgl-base-departements/) | Comptes départements 2012-2024, format long, 297 447 lignes |
| [`communes/carto/carto-communes-*.csv`](communes/carto/) (10 fichiers) | [`donnees_carto_communes`](https://data.ofgl.fr/explore/dataset/donnees_carto_communes/) filtré par agrégat | Comptes communes 2022-2024 (carto), un fichier CSV par agrégat (~12 Mo chacun) |
| [`communes/communes-svg-FRA.json`](communes/communes-svg-FRA.json) | [`communes_formes_geo_svg`](https://data.ofgl.fr/explore/dataset/communes_formes_geo_svg/) filtré sur `niveau_zoom=FRA` | Contours SVG des ~35 000 communes au niveau France entière (~32 Mo) |
| [`communes/disponibilite-comptes-communes.json`](communes/disponibilite-comptes-communes.json) | [`disponibilite-des-comptes-des-communes`](https://data.ofgl.fr/explore/dataset/disponibilite-des-comptes-des-communes/) | Disponibilité des comptes par commune et par année 2012-2024 |

#### Particularité des communes : pourquoi pas la base brute ?

Le dataset [`ofgl-base-communes`](https://data.ofgl.fr/explore/dataset/ofgl-base-communes/) (équivalent communal de `ofgl-base-regions` et `ofgl-base-departements`) contient **22 364 887 lignes** — son téléchargement complet en JSON dépasserait largement la dizaine de Go et les 100 Mo/fichier autorisés sur GitHub Pages.

Stratégie retenue :

1. **Pour les indicateurs de la synthèse 2024** : on utilise le dataset cartographique [`donnees_carto_communes`](https://data.ofgl.fr/explore/dataset/donnees_carto_communes/) filtré côté serveur via l'API. Ce dataset est *déjà* limité à 53 agrégats clés et aux trois dernières années (2022-2024), et structuré en 1 ligne par commune × agrégat × type de budget. On télécharge un fichier CSV par agrégat (10 fichiers, ~12 Mo chacun).
2. **Pour les contours SVG** : on télécharge uniquement le niveau de zoom France entière (`niveau_zoom="FRA"`, ~35 000 entrées) ; les niveaux régionaux et départementaux pourront être ajoutés à la demande quand le site web en aura besoin.
3. **Pour des analyses ponctuelles** sur une commune ou un agrégat hors de la liste : faire des requêtes ciblées à l'API OFGL plutôt que de télécharger la base entière (cf. section [Comment vérifier un chiffre par soi-même](#comment-vérifier-un-chiffre-par-soi-même)).

### Schéma des comptes (champs principaux)

| Champ | Type | Description |
|---|---|---|
| `exer` | string | Exercice budgétaire (année) |
| `reg_code`, `reg_name` | string | Code INSEE et nom de la région (toujours présent) |
| `dep_code`, `dep_name` | string | Code INSEE et nom du département (base départements seulement) |
| `categ` | string | Catégorie : `REG`, `CTU`, `DEPT`, `ML` (Métropole de Lyon), `PARIS` |
| `siren` | string | Identifiant SIREN de la collectivité |
| `type_de_budget` | string | `Budget principal` ou `Budget annexe` |
| `nomen` | string | Nomenclature comptable : `M14`, `M57`, `M71`, `M4`, etc. |
| `agregat` | string | Nom de l'agrégat financier (cf. méthodologie ci-dessous) |
| `montant` | float | Montant en euros |
| `montant_en_millions` | float | Idem, en millions d'euros |
| `ptot` | int | Population totale |
| `pmun` | int | Population municipale |
| `euros_par_habitant` | float | Montant ramené à la population totale |
| `euros_par_habitant_pmun` | float | Idem, ramené à la population municipale |

### Périmètre temporel

- **Régions** : 2012 à 2024 inclus (13 exercices) — base brute OFGL `ofgl-base-regions`
- **Départements** : 2012 à 2024 inclus (13 exercices) — base brute `ofgl-base-departements`
- **Communes** : 2017 à 2024 inclus (8 exercices) — fusion de deux sources :
  - **2022-2024** : dataset cartographique `donnees_carto_communes` (3 années glissantes en colonnes `m_<year>`)
  - **2017-2021** : 235 fichiers CSV téléchargés ciblement depuis la base brute `ofgl-base-communes` (un fichier par couple {année × agrégat × budget principal}), stockés dans `data/communes/historique/<year>/`. Total ~2 Go en local. Permet d'étendre la profondeur historique sans dépendre de la mémoïsation à 3 ans glissants du carto OFGL.

Toutes les synthèses sont **multi-années** : un seul fichier par niveau contient l'ensemble des exercices disponibles, sous la forme `{years: [...], indicators: [...], entities: [{code, name, meta, population: [...], values: {indicator: [serie]}}]}`. Le site web peut ainsi naviguer dans le temps via un slider, et afficher des sparklines d'évolution dans le panel détaillé.

### Filtre appliqué dans nos synthèses

Pour produire les fichiers `synthese-*-2024.csv`, le filtre suivant est appliqué :
- exercice = 2024
- type de budget = Budget principal

→ Les budgets annexes (transports, déchets, eau, ports, lycées professionnels,
etc.) sont **exclus** des synthèses pour ne refléter que la collectivité elle-même.
Ils restent présents dans les fichiers bruts régions/départements si l'on souhaite les analyser.

---

## Méthodologie OFGL

### Définitions et formules des agrégats financiers

| Fichier local | Dataset OFGL | Contenu |
|---|---|---|
| [`methodologie/ofgl-definitions-agregats.json`](methodologie/ofgl-definitions-agregats.json) | [`methodologie-ofgl-definitions-agregats-financiers`](https://data.ofgl.fr/explore/dataset/methodologie-ofgl-definitions-agregats-financiers/) | 87 définitions textuelles + arbre hiérarchique |
| [`methodologie/ofgl-formules-agregats.json`](methodologie/ofgl-formules-agregats.json) | [`methodologie-ofgl-formules-des-agregats-financiers`](https://data.ofgl.fr/explore/dataset/methodologie-ofgl-formules-des-agregats-financiers/) | 57 865 formules (par type de collectivité × année × nomenclature × budget) |

Le fichier des **formules** documente précisément, pour chaque agrégat,
la combinaison de comptes du plan comptable général qui sert au calcul,
sous une forme du type `CN73111 + CN73112 - CN7398` accompagnée des libellés
parlants.

C'est ce qui garantit qu'un agrégat « Recettes de fonctionnement » a la même
définition d'une région à l'autre, malgré des nomenclatures comptables
différentes (M71 historique vs M57 nouvelle).

### Liste des agrégats utilisés dans nos synthèses

Les 10 agrégats suivants ont été retenus comme indicateurs centraux :

| Agrégat | Catégorie | Pourquoi cet indicateur |
|---|---|---|
| Recettes totales | Recettes | Volume global des ressources |
| Recettes de fonctionnement | Recettes | Recettes récurrentes (impôts, dotations, services) |
| Dépenses totales | Dépenses | Volume global des charges |
| Dépenses de fonctionnement | Dépenses | Dépenses récurrentes (personnel, achats, interventions) |
| Dépenses d'équipement | Investissement | Effort d'investissement sur le patrimoine propre |
| Frais de personnel | Charges | Poids de la masse salariale |
| Epargne brute | Solde | Différence recettes/dépenses de fonctionnement — capacité à financer l'investissement |
| Encours de dette | Dette | Stock de dette restant dû |
| Annuité de la dette | Dette | Charge annuelle de remboursement (capital + intérêts) |
| Charges financières | Dette | Intérêts payés sur la dette |

---

## Données cartographiques

| Fichier local | Dataset OFGL | Contenu |
|---|---|---|
| [`regions/regions-svg.json`](regions/regions-svg.json) | [`regions_formes_geo_svg`](https://data.ofgl.fr/explore/dataset/regions_formes_geo_svg/) | Tracés SVG (`d="M ..."`) des régions, 36 entrées (1 vue France + zooms) |
| [`regions/regions-carto.json`](regions/regions-carto.json) | [`donnees_carto_regions`](https://data.ofgl.fr/explore/dataset/donnees_carto_regions/) | 1 768 lignes : pour chaque région × agrégat, ratios prêts à cartographier (€/hab, évolution, poids dans recettes/dépenses) |
| [`departements/departements-svg.json`](departements/departements-svg.json) | [`departements_formes_geo_svg`](https://data.ofgl.fr/explore/dataset/departements_formes_geo_svg/) | Tracés SVG des départements |
| [`departements/departements-carto.json`](departements/departements-carto.json) | [`donnees_carto_departements`](https://data.ofgl.fr/explore/dataset/donnees_carto_departements/) | Indicateurs cartographiques pour les départements |

Les datasets `donnees_carto_*` exposent en plus, pour les agrégats cartographiables,
des **ratios déjà calculés par l'OFGL** comme :

- Taux d'épargne brute
- Taux d'épargne nette
- Taux d'équipement
- Taux d'endettement
- Capacité de désendettement
- Trésorerie en jours de dépenses

Quand nous calculons nous-mêmes ces ratios (cf. ci-dessous), nous nous référons
à ces fichiers pour valider la cohérence.

---

## Indicateurs calculés en interne

Les fichiers de synthèse `synthese-*-2024.{csv,json}` ajoutent **deux ratios**
calculés à partir des agrégats OFGL :

### Taux d'épargne brute

> **Taux d'épargne brute (%)** = 100 × Épargne brute / Recettes de fonctionnement

- Mesure la part des recettes courantes qui n'est pas consommée par les
  dépenses courantes — donc disponible pour l'investissement et le remboursement
  de la dette.
- Plus le ratio est élevé, plus la collectivité dégage de marge.
- Repères usuels en analyse financière locale :
  - **> 15 %** : marge confortable
  - **8 % à 15 %** : marge correcte
  - **< 8 %** : marge faible
  - **< 0 %** : épargne négative — la collectivité ne couvre pas ses dépenses
    de fonctionnement par ses recettes courantes

### Capacité de désendettement

> **Capacité de désendettement (années)** = Encours de dette / Épargne brute

- Mesure le nombre d'années qu'il faudrait à la collectivité, à épargne
  constante, pour rembourser intégralement sa dette.
- Repères usuels :
  - **< 8 ans** : situation saine
  - **8 à 12 ans** : zone de vigilance
  - **> 12 ans** : zone d'alerte (seuil retenu par la loi de programmation
    des finances publiques 2018-2022 pour les régions)
- Limite : ce ratio devient **très instable** quand l'épargne brute s'effondre
  (un dénominateur proche de zéro fait exploser le ratio). À toujours lire
  conjointement avec l'encours de dette en €/hab et le taux d'épargne brute.

Les autres colonnes des synthèses sont reprises **telles quelles de l'OFGL**,
ramenées à la population totale (`euros_par_habitant`).

---

## Notes méthodologiques importantes

### Collectivités à statut particulier

Certaines entités ne sont pas directement comparables aux autres parce qu'elles
**cumulent plusieurs niveaux de compétences** :

| Entité | Code | Particularité | Conséquence |
|---|---|---|---|
| Corse | 94 | CTU (depuis 2018), exerce compétences région + département | Apparaît dans la base régions, **pas** dans la base départements |
| Martinique | 02 | CTU (depuis 2015), idem | Idem |
| Guyane | 03 | CTU (depuis 2015), idem | Idem |
| Métropole de Lyon | 691 | Exerce les compétences départementales sur son territoire (depuis 2015) | Apparaît dans la base départements à côté du Rhône (69) |
| Paris | 75 | Cumule commune + département | Apparaît dans la base départements ; montants/hab plus élevés |
| Alsace (CEA) | 67A | Fusion Bas-Rhin + Haut-Rhin (2021) | Code spécifique `67A` |
| Mayotte | 06 (région) / 976 (dpt) | Pas de comptes régionaux séparés | Présent comme département mais absent de la base régions |

### Couverture des communes

- **34 936 communes** présentes dans la base carto OFGL en budget principal (sur ~35 000 dans la liste INSEE).
- **34 935 communes** ont également un contour SVG (niveau France entière) ; la jointure SVG ↔ synthèse se fait sur le **SIREN** (le `data_fill_id` du SVG) car les noms ne sont pas uniques (homonymes : Saint-Martin, Saint-Pierre, Notre-Dame, etc.).
- Les **102 communes INSEE absentes du carto OFGL** sont essentiellement des communes nouvelles récemment fusionnées et certaines collectivités d'outre-mer (Saint-Pierre-et-Miquelon).
- Les **5 communes** sans donnée 2024 sur certains agrégats sont signalées dans le fichier `disponibilite-comptes-communes.json`.
- Pour 69 communes, la **population n'est pas disponible** dans la liste INSEE (généralement des communes ayant fusionné juste après le millésime de référence).

### Synthèse minimale pour le calque décoratif (`data/communes/decoratif-communes-2024.json`)

Pour accélérer le chargement initial du tab Communes côté site, un fichier compact regroupe pour chaque commune **uniquement** ce qui est nécessaire au calque décoratif (l'aperçu coloré de la France entière) : SIREN, contour SVG, et les 12 indicateurs financiers. Format positionnel `[siren, d, [valeurs...]]` qui économise sur les noms de clés JSON répétés 35 000 fois.

| | Avant | Après |
|---|---:|---:|
| Fichiers | `communes-svg-FRA.json` + `synthese-communes-2024.json` | `decoratif-communes-2024.json` |
| Taille brute | ~48 Mo | **~26 Mo** |
| Taille gzip (prod) | ~12 Mo | **~9 Mo** |

**Aucune perte d'information** : tous les champs présents dans ce fichier sont également dans `synthese-communes-2024.json` et `data/communes/by-dep/`. C'est juste une vue minimale optimisée pour la performance d'affichage initial.

### Découpage par département (`data/communes/by-dep/`)

Pour permettre un chargement progressif côté site web sans réduire la précision des données, un **regroupement par département** des communes est généré automatiquement par `scripts/fetch_all.py`. **Aucune perte d'information** par rapport aux fichiers globaux : c'est juste une réorganisation pour la performance.

| Fichier | Contenu |
|---|---|
| `data/communes/by-dep/_index.json` | Index global (101 départements) avec nom, bbox géographique et nombre de communes — chargé en premier pour piloter le drill-down |
| `data/communes/by-dep/<code_dep>.json` | Pour chaque département : ses communes (contour SVG + indicateurs financiers) et la bbox englobante |

Les codes de département utilisent la nomenclature INSEE complète, y compris `2A` / `2B` pour la Corse, `67A` pour la Collectivité européenne d'Alsace, et `691` pour la Métropole de Lyon. Total : 44 Mo répartis en 101 fichiers de 2 Ko (Paris) à 995 Ko (Pas-de-Calais).

### Outre-mer

Les DROM (Guadeloupe 971, La Réunion 974, Mayotte 976) ont des structures
de recettes très différentes (octroi de mer, dotations spécifiques) qui rendent
les comparaisons avec la métropole délicates. Le champ `outre_mer` permet de
les filtrer dans les analyses.

### Nomenclatures comptables

Plusieurs nomenclatures coexistent :
- **M14** : communes
- **M57** : nouvelle nomenclature unifiée (en généralisation, déjà utilisée
  pour la plupart des régions et départements en 2024)
- **M71** : ancienne nomenclature des régions
- **M52** : ancienne nomenclature des départements
- **M4 / M41 / M42 / M43 / M49** : services publics locaux à caractère
  industriel et commercial (eau, transport, déchets...)

L'OFGL harmonise déjà les agrégats à travers ces nomenclatures, donc rien à
faire de notre côté pour comparer les collectivités entre elles.

### Périmètre des budgets

- **Budget principal** : la collectivité elle-même.
- **Budgets annexes** : services à comptabilité séparée (transports, déchets,
  eau, lycées professionnels, ports...).
- **Comptes consolidés** (datasets `*-consolidee` non utilisés ici) : agrègent
  budget principal + budgets annexes pour avoir une vue financière totale.

Nos synthèses utilisent le **budget principal seul**. Si l'objectif change
(par ex. évaluer la gestion d'un service public spécifique), il faudra
basculer sur les budgets annexes ou les comptes consolidés.

---

## Comment vérifier un chiffre par soi-même

Si vous voulez auditer un chiffre publié sur ce site, voici les chemins :

### Vérifier un agrégat depuis l'OFGL (interface web)

1. Aller sur <https://data.ofgl.fr>.
2. Section « Les données » → choisir `Comptes des régions 2012-2024` (ou
   départements selon le cas).
3. Filtrer sur :
   - `Exercice` = année voulue
   - `Région` ou `Département` = collectivité voulue
   - `Type de budget` = `Budget principal`
   - `Agrégat` = indicateur voulu
4. Lire la colonne `Montant en € par habitant` (ou `Montant`).

### Vérifier via l'API REST OFGL (programmatique)

L'API OFGL utilise la plateforme Opendatasoft. Exemple pour les recettes de
fonctionnement de la région Bretagne en 2024 :

```bash
curl "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/ofgl-base-regions/records?\
where=exer%3D%222024%22%20AND%20reg_name%3D%22Bretagne%22%20AND%20agregat%3D%22Recettes%20de%20fonctionnement%22%20AND%20type_de_budget%3D%22Budget%20principal%22&\
select=exer,reg_name,agregat,montant,euros_par_habitant"
```

Pour une **commune** spécifique (ex. Nantes, INSEE 44109) :

```bash
curl "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/ofgl-base-communes/records?\
where=exer%3D%222024%22%20AND%20insee%3D%2244109%22%20AND%20agregat%3D%22Epargne%20brute%22%20AND%20type_de_budget%3D%22Budget%20principal%22&\
select=exer,nom,agregat,montant,euros_par_habitant"
```

### Remonter à la source primaire (DGFiP)

Les comptes individuels bruts produits par la DGFiP sont également publiés
sur <https://data.economie.gouv.fr> (jeux de données « Comptes individuels
des régions / départements / communes ») et sur
<https://www.collectivites-locales.gouv.fr>. C'est la source **amont** que
l'OFGL retraite ; à utiliser pour vérifier qu'aucun retraitement n'a déformé
le chiffre.

### Comprendre la formule d'un agrégat

1. Ouvrir [`methodologie/ofgl-definitions-agregats.json`](methodologie/ofgl-definitions-agregats.json) : recherche par nom d'agrégat → définition textuelle.
2. Ouvrir [`methodologie/ofgl-formules-agregats.json`](methodologie/ofgl-formules-agregats.json) : recherche par
   `agregat` + `type_collectivite` + `annee` + `nomenclatures` → formule
   exacte sous forme de combinaison de comptes (par ex. `CN73111 + CN73112`).
3. Le compte `CNxxxx` correspond à un compte du plan comptable public —
   référentiel téléchargeable sur le site de la DGFiP.

---

## Versions et reproductibilité

Pour regénérer intégralement le dossier `data/` :

```bash
python scripts/fetch_all.py --force
```

Toutes les données sont re-téléchargées depuis les URLs ci-dessus, et les
fichiers de synthèse sont reconstruits. La date de chaque téléchargement
est inscrite dans le fichier `*.meta.json` correspondant.

L'OFGL met à jour ses bases environ deux fois par an (rapport annuel publié
au printemps, mise à jour des données comptables N-1 à l'été/automne).
