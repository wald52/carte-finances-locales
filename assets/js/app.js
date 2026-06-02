/* ============================================================================
 * Échelons locaux — application
 * ----------------------------------------------------------------------------
 * Application sans dépendance externe. Charge les données depuis data/,
 * dessine une carte SVG cliquable des collectivités françaises (régions,
 * départements, communes), gère un sélecteur d'indicateur et un panneau
 * d'info détaillé.
 *
 * Au niveau Communes, on utilise un drill-down par département pour éviter
 * de manipuler les ~35 000 polygones simultanément :
 *   - Mode "overview" : on affiche les ~100 départements (chargement léger)
 *   - Click sur un département → mode "drilldown" : on charge ~300 communes
 *     du département dans un fichier dédié (data/communes/by-dep/{code}.json)
 *   - La viewBox SVG zoome automatiquement sur le département
 *   - Un bouton "Retour à la France" permet de revenir au mode overview
 * ========================================================================= */

// ----------------------------------------------------------------------------
// Niveaux administratifs disponibles
// ----------------------------------------------------------------------------

const LEVELS = {
  regions: {
    label: "Régions",
    dataUrl: "data/regions/synthese-regions-2024.json",
    svgUrl: "data/regions/regions-svg.json",
    svgZoom: "FRA",
    svgIdKey: "nom_reg",
    dataIdKey: "reg_name",
    dataCodeKey: "reg_code",
    svgLabelKey: "nom_reg",
    panelSubtitle: (d) =>
      `Population : ${formatPopulation(getPopulationForYear(d))} · Code INSEE ${d.reg_code}`,
    notes: (d) => {
      if (["02", "03", "94"].includes(d.reg_code)) {
        return "Cette collectivité (CTU) cumule les compétences de la région et du département. Ses chiffres ne sont pas directement comparables aux 13 régions métropolitaines classiques.";
      }
      if (d.reg_code === "06") {
        return "Mayotte n'a pas de Conseil régional : les compétences régionales sont exercées par le Département (CTU depuis 2011). OFGL ne publie aucun compte régional pour Mayotte ; seuls les EPL (établissements publics locaux) physiquement implantés à Mayotte apparaissent ici. Pour une vue financière complète, sélectionner le niveau Départements (code 976).";
      }
      if (["01", "04"].includes(d.reg_code)) {
        return "Région ultramarine avec une structure de recettes spécifique (octroi de mer, dotations particulières) qui rend la comparaison avec la métropole délicate.";
      }
      return null;
    },
    noDataMessage:
      "Données financières non disponibles (OFGL ne publie pas cet indicateur au niveau régional pour cette collectivité).",
  },
  departements: {
    label: "Départements",
    dataUrl: "data/departements/synthese-departements-2024.json",
    svgUrl: "data/departements/departements-svg.json",
    svgZoom: "FRA",
    svgIdKey: "nom_dep",
    dataIdKey: "dep_name",
    dataCodeKey: "dep_code",
    svgLabelKey: "nom_dep",
    panelSubtitle: (d) =>
      `Population : ${formatPopulation(getPopulationForYear(d))} · Code INSEE ${d.dep_code}` +
      (d.reg_name ? ` · Région : ${d.reg_name}` : ""),
    notes: (d) => {
      if (d.categ === "PARIS") {
        return "Paris cumule les compétences de la commune et du département. Ses montants par habitant sont mécaniquement plus élevés.";
      }
      if (d.categ === "ML") {
        return "La Métropole de Lyon exerce les compétences départementales sur son territoire depuis 2015 (en remplacement du conseil départemental du Rhône sur ce périmètre).";
      }
      if (d.dep_code === "67A") {
        return "La Collectivité européenne d'Alsace (CEA) résulte de la fusion des départements du Bas-Rhin et du Haut-Rhin en 2021.";
      }
      if (d.categ === "CTU" && d.dep_code === "Corse") {
        // Pseudo-entité Corse injectée par fetch_sdis.py : seul le SDIS
        // est disponible (consolidé 2A+2B). Les comptes dpt sont au
        // niveau Régions (Collectivité de Corse) et ne sont pas répliqués.
        return "Corse : Collectivité Territoriale Unique (CTU) — les comptes du conseil sont au niveau Régions. Les chiffres SDIS sont consolidés à partir des deux SDIS distincts (Corse-du-Sud 2A + Haute-Corse 2B), avec moyenne pondérée par population.";
      }
      if (d.outre_mer === "Oui") {
        // Martinique et Guyane sont des CTUs depuis 2015 mais OFGL conserve
        // leurs entrées dpt distinctes (SIREN dpt ≠ SIREN région) → les
        // comptes apparaissent ici. Mayotte est officiellement dpt+région.
        return "Département d'outre-mer : structure de recettes spécifique qui rend la comparaison avec la métropole délicate. Pour Martinique et Guyane (CTU), les comptes dpt sont conservés distinctement par OFGL — les valeurs visibles ici sont réelles.";
      }
      return null;
    },
    noDataMessage:
      "Aucune donnée départementale n'est disponible pour cette entité. Cas particulier : pour la Corse, seuls les SDIS sont consolidés ici — les comptes du conseil sont au niveau Régions (Collectivité de Corse, CTU).",
  },
  // Intercommunalités : géré comme les communes avec mode overview/drilldown.
  //   - overview : on réutilise le calque décoratif communes, mais on
  //     re-coloriée chaque commune selon la valeur de SON EPCI parent
  //     (lookup via siren_epci stocké dans meta-communes-2024.json).
  //     Le clic sur une commune sélectionne l'EPCI parent et zoome.
  //   - drilldown : on charge data/intercommunalites/by-epci/{siren}.json,
  //     qui contient les communes membres avec leurs SVG paths et leurs
  //     données propres. C'est l'équivalent du drill-down départements.
  //
  // Engagement « zéro reconstruction » : tous les chiffres affichés au niveau
  // EPCI proviennent de ofgl-base-gfp (1335 EPCIs × 54 agrégats). Aucune
  // valeur n'est calculée par agrégation depuis les communes.
  intercommunalites: {
    label: "Intercommunalités",
    panelSubtitle: (d) => {
      const parts = [`Population : ${formatPopulation(getPopulationForYear(d))}`];
      if (d.categ) parts.push(`Type : ${describeEpciCategory(d.categ)}`);
      if (d.dep_name) parts.push(`Département principal : ${d.dep_name}`);
      if (d.reg_name) parts.push(`Région : ${d.reg_name}`);
      return parts.join(" · ");
    },
    notes: (d) => {
      if (d.outre_mer === "Oui") {
        return "EPCI d'outre-mer : structure de recettes spécifique (octroi de mer, dotations particulières).";
      }
      if (d.categ === "MET69") {
        return "La Métropole de Lyon est juridiquement un EPCI à statut particulier qui exerce aussi les compétences départementales sur son territoire depuis 2015.";
      }
      // Métropole du Grand Paris : SIREN 200054781 OU nat_juridique = "MET75"
      if (String(d.siren) === "200054781" || d.nat_juridique === "MET75") {
        return "La Métropole du Grand Paris est un EPCI à statut particulier englobant Paris + 130 communes de la petite couronne. La MGP n'est pas un Ensemble Intercommunal au sens FPIC — les 11 EPT (Établissements Publics Territoriaux) sont les EI parisiens, chacun dispose de ses propres valeurs FPIC dans le leaderboard. Sur la carte, les communes des EPT sont coloriées par leur EPT (valeur OFGL réelle) en l'absence de valeur MGP.";
      }
      if (d.categ === "GP") {
        return "La Métropole du Grand Paris est un EPCI à statut particulier englobant Paris et 130 communes des départements de la petite couronne.";
      }
      return null;
    },
    noDataMessage:
      "Données financières non disponibles pour cet EPCI (cas marginal : EPCI dissous ou récemment créé sans compte 2024 disponible).",
  },
  // Communes : géré spécialement avec drill-down (cf. plus bas).
  // On garde une config minimale ici pour le rendu d'une commune individuelle.
  communes: {
    label: "Communes",
    panelSubtitle: (d) => {
      const parts = [`Population : ${formatPopulation(getPopulationForYear(d))}`];
      if (d.insee) parts.push(`Code INSEE ${d.insee}`);
      if (d.nom_dep) parts.push(`${d.nom_dep}`);
      if (d.nom_reg) parts.push(`${d.nom_reg}`);
      let sub = parts.join(" · ");
      if (d.nom_gfp) sub += `<br /><span class="panel__epci">EPCI : ${d.nom_gfp}</span>`;
      return sub;
    },
    notes: (d) => {
      // Les 6 villages "morts pour la France" : détruits pendant la bataille
      // de Verdun en 1916, jamais reconstruits, sans habitants ni budget.
      // Leur statut de commune est maintenu en mémoire des combats (maire
      // désigné par arrêté préfectoral, pas d'élections).
      const VILLAGES_MORTS_VERDUN = [
        "55039", // Beaumont-en-Verdunois
        "55050", // Bezonvaux
        "55139", // Cumières-le-Mort-Homme
        "55189", // Fleury-devant-Douaumont
        "55239", // Haumont-près-Samogneux
        "55307", // Louvemont-Côte-du-Poivre
      ];
      if (d.insee && VILLAGES_MORTS_VERDUN.includes(d.insee)) {
        return "Village « mort pour la France » détruit lors de la bataille de Verdun (1916) et jamais reconstruit. Sans habitant ni activité, il n'a pas de budget propre — son maire est désigné par arrêté préfectoral en mémoire des combats. C'est pour cela qu'aucune donnée financière n'est disponible.";
      }
      if (d.population != null && d.population < 200) {
        return "Très petite commune : les ratios financiers (taux d'épargne brute, capacité de désendettement) peuvent être très instables — une dépense ponctuelle d'investissement suffit à les faire varier énormément.";
      }
      return null;
    },
    noDataMessage:
      "Données financières non disponibles pour cette commune (cas marginal : commune nouvelle récemment fusionnée ou COM).",
  },
};

// Cas particulier : la Corse est un seul polygone dans le SVG mais découpée
// en 2A et 2B au niveau commune (data/communes/by-dep/2A.json et 2B.json).
// Les autres divergences de nomenclature (Alsace 67A, Métropole de Lyon 691)
// sont gérées côté script Python (fichiers 67A.json et 691.json générés).
const DEPS_FILES_BY_DEP_NAME = {
  "Corse": ["2A", "2B"],
};

// Mapping des dep_codes "virtuels" / agrégés utilisés par synthese-departements
// vers les dep_codes "physiques" basés sur le préfixe INSEE des communes.
// Utilisé pour rapprocher la sélection (qui parle en code synthese) des
// données syndicat (qui parlent en préfixe INSEE via meta-communes).
//
//   - "67A" : Collectivité européenne d'Alsace = fusion des ex-67 et ex-68.
//     Les communes membres conservent leur INSEE 67xxx / 68xxx → leurs
//     member_deps côté syndicat sont "67" et "68".
//   - "691" : Métropole de Lyon = subset des INSEE 69xxx (les autres restent
//     en Rhône "69"). Non distinguable sans table par INSEE → on retombe
//     sur "69" qui ramène trop large ; à raffiner si nécessaire.
const VIRTUAL_DEP_CODES_EXPANSION = {
  "67A": ["67", "68"],
  "691": ["69"],
};

/** Expanse un tableau de dep_codes (potentiellement virtuels comme "67A")
 *  vers le set des dep_codes physiques utilisés par les member_deps des
 *  syndicats. Idempotent pour les codes physiques classiques. */
function expandDepCodes(codes) {
  const out = new Set();
  for (const c of codes || []) {
    const exp = VIRTUAL_DEP_CODES_EXPANSION[c];
    if (exp) {
      for (const e of exp) out.add(e);
    } else {
      out.add(c);
    }
  }
  return out;
}

// ----------------------------------------------------------------------------
// Indicateurs proposés
// ----------------------------------------------------------------------------

// Liste des indicateurs disponibles, structurée par groupe pour l'affichage
// dans le sélecteur (en <optgroup>). Le champ `levels` indique pour quels
// niveaux administratifs l'indicateur est pertinent — au switch de niveau,
// le sélecteur est reconstruit avec les indicateurs correspondants.
const ALL_LEVELS = ["regions", "departements", "intercommunalites", "communes"];
// Indicateurs ALL_LEVELS qui n'ont PAS d'équivalent EPCI dans `ofgl-base-gfp` :
// utilisé pour CVAE (supprimée en 2023, plus dans les 54 agrégats EPCI) et
// les ratios pré-calculés (taux d'épargne brute, capacité désendettement)
// qu'OFGL ne pré-calcule pas pour les groupements.
const ALL_LEVELS_NO_EPCI = ["regions", "departements", "communes"];
const REG_DEP = ["regions", "departements"];
// Certains indicateurs REG_DEP existent aussi pour EPCI (dette détaillée,
// fiscalité reversée, fonds de soutien).
const REG_DEP_EPCI = ["regions", "departements", "intercommunalites"];
// EPCI + communes (TEOM, DETR — perçus tantôt par la commune tantôt par l'EPCI)
const COM_EPCI = ["intercommunalites", "communes"];

// Helper utilisé par LEVELS.intercommunalites.panelSubtitle pour afficher
// la nature juridique de l'EPCI en clair. Codes catégorie OFGL :
//   CC = communauté de communes (la grande majorité, ~990 entités)
//   CA = communauté d'agglomération (~225)
//   CU = communauté urbaine (~14)
//   M = métropole (~22, hors statuts particuliers)
//   MET69 = Métropole de Lyon (statut particulier)
//   GP = Métropole du Grand Paris (statut particulier)
function describeEpciCategory(c) {
  switch (c) {
    case "CC": return "Communauté de communes";
    case "CA": return "Communauté d'agglomération";
    case "CU": return "Communauté urbaine";
    case "M":  return "Métropole";
    case "MET69": return "Métropole de Lyon (statut particulier)";
    case "GP": return "Métropole du Grand Paris";
    default:   return c || "EPCI";
  }
}

// ----------------------------------------------------------------------------
// INDICATORS — métadonnées des ~7350 indicateurs ({key,label,unit,group,levels,help}).
// Ce tableau (~4,5 Mo, 94 % de l'ancien app.js) est désormais chargé depuis
// data/indicators.json au démarrage, au lieu d'être embarqué ici. Gain : app.js
// passe de ~4,9 Mo à ~0,3 Mo → la compilation + l'évaluation du littéral (~200 ms
// sur le thread principal, une longue tâche qui plombait LCP/TBT sous le throttling
// CPU de Lighthouse mobile) disparaissent. JSON.parse est nettement moins coûteux.
// MAINTENANCE : pour ajouter/modifier des indicateurs, éditer data/indicators.json
// (ou régénérer via le pipeline puis re-extraire). L'ordre est préservé ;
// INDICATORS[0] reste « Recettes totales » (indicateur par défaut).
// ----------------------------------------------------------------------------
let INDICATORS = [];
async function loadIndicators() {
  if (INDICATORS.length) return INDICATORS;
  INDICATORS = await loadJson("data/indicators.json");
  return INDICATORS;
}

// Ordre d'affichage des groupes dans le sélecteur (optgroup).
const INDICATOR_GROUP_ORDER = [
  "Recettes",
  "Dépenses",
  "Solde et épargne",
  "Dette",
  // Patrimoine non financier — actif réévalué OFGL (snapshot 2024, communes + GFP).
  // Placé après "Dette" car c'est aussi un stock comptable (vs flux Recettes/Dépenses).
  "Patrimoine (actif réévalué)",
  "Trésorerie",
  "Ratios",
  "Ensemble intercommunal — territoire consolidé",
  // Contexte & critères des EPCI (interne-criteres-*) — hors comptes OFGL.
  "Contexte & critères (EPCI)",
  "Taux d'imposition",
  "Bases & produits fiscaux",
  "TSE — Taxes spéciales d'équipement",
  "Chambres consulaires",
  "IFER",
  "TVA — Compensations État",
  "TASCOM — Surfaces commerciales",
  "TP — Compensations ex-Taxe Professionnelle",
  "Allocations compensatrices",
  "Bases CFE détaillées",
  "TASA — Aéroports",
  "TSC — Taxe spéciale chambre",
  "FSRIF — Solidarité Île-de-France",
  // Dotations OFGL — 38 groupes, ~479 indicateurs
  "Dotations commune - DACOM",
  "Dotations commune - DILICO",
  "Dotations commune - DNP",
  "Dotations commune - DSR",
  "Dotations commune - DSR Bourg-centre",
  "Dotations commune - DSR Cible",
  "Dotations commune - DSR Péréquation",
  "Dotations commune - DSU",
  "Dotations commune - Dotation commune nouvelle",
  "Dotations commune - Dotation de biodiversité et d'aménités rurales",
  "Dotations commune - Dotation forfaitaire",
  "Dotations commune - Effort fiscal",
  "Dotations commune - FPIC",
  "Dotations commune - FSRIF",
  "Dotations commune - Général",
  "Dotations commune - Potentiel fiscal et financier",
  "Dotations EPCI - CIF",
  "Dotations EPCI - CRFP",
  "Dotations EPCI - DILICO",
  "Dotations EPCI - Dotation d'intercommunalité",
  "Dotations EPCI - Dotation de compensation",
  "Dotations EPCI - Dotation groupements touristiques",
  "Dotations EPCI - Général",
  "Dotations EPCI - Potentiel fiscal",
  "Dotations département - Caractéristiques",
  "Dotations département - DGF",
  "Dotations département - DILICO",
  "Dotations département - FSDRIF",
  "Dotations département - Fonds CVAE",
  "Dotations département - Fonds DMTO (2018-2019)",
  "Dotations département - Fonds DMTO (2020-2024)",
  "Dotations département - Fonds de solidarité (2018-2019)",
  "Dotations département - Fonds de solidarité interdépartemental (2019)",
  "Dotations département - Potentiel financier",
  "Dotations département - Potentiel fiscal",
  "Dotations région - DILICO",
  "Dotations région - Fonds de péréquation des régions",
  "Dotations région - Fonds de solidarité régional",
  "MDPH - Action handicap départementale",
  // SDIS (sapeurs-pompiers) — 97 dpts (sauf Paris/petite couronne = BSPP militaire)
  "SDIS — Sapeurs-pompiers (département)",
  // Présentation fonctionnelle des dpts (ofgl-base-departements-fonctionnelle).
  // Groupes triés par pertinence pour le modèle social.
  "Dpt fonctionnel — Action sociale et santé",
  "Dpt fonctionnel — Enseignement",
  "Dpt fonctionnel — Transports",
  "Dpt fonctionnel — Culture, jeunesse, sports",
  "Dpt fonctionnel — Sécurité",
  "Dpt fonctionnel — Services généraux",
  "Dpt fonctionnel — Aménagement et économie",
  // Base extra-financière départements (collèges & voirie). Données reprises
  // telles quelles, hors comptes OFGL (cf. avertissement dans le help).
  "Extra-financier (collèges & voirie)",
  // Comptes consolidés (budget principal + budgets annexes)
  "Comptes consolidés commune",
  "Comptes consolidés EPCI",
  "Comptes consolidés département",
  "Comptes consolidés région",
  // CCAS-CIAS : action sociale communale (mapping SIREN → INSEE via recherche-entreprises.api.gouv.fr)
  "CCAS - Action sociale communale",
  "CIAS - Action sociale intercommunale",
  // EPL - Etablissements publics locaux (mapping SIREN → INSEE siège via recherche-entreprises)
  "EPL - Abattoirs",
  "EPL - Activités agricoles et forestières",
  "EPL - Activités culturelles",
  "EPL - Activités diverses",
  "EPL - Activités sanitaires",
  "EPL - Activités scolaires",
  "EPL - Activités sociales",
  "EPL - Activités sportives",
  "EPL - Adduction ou distribution d'eau",
  "EPL - Adduction ou distribution d'eau et assainissement",
  "EPL - Administration générale",
  "EPL - Aménagement de zones industrielles et artisanat",
  "EPL - Assainissement",
  "EPL - Camping",
  "EPL - Cantine administrative",
  "EPL - Cantines du 1° degré",
  "EPL - Chauffage urbain",
  "EPL - Collecte et traitement des ordures ménagères",
  "EPL - Commerce multi-services",
  "EPL - Enfance",
  "EPL - Exploitation de parc de stationnement",
  "EPL - Foires, halles et marchés",
  "EPL - Gestion des ports et aéroports",
  "EPL - Habitat, réserves foncières, parc immobilier",
  "EPL - Laboratoires d'analyses",
  "EPL - Logements sociaux",
  "EPL - Nouvelles Technologies de l'Information et de la Communication",
  "EPL - Personnes âgées",
  "EPL - Pompes funèbres",
  "EPL - Production et distribution d'énergie",
  "EPL - Protection et mise en valeur de l'environnement",
  "EPL - Ramassage scolaire",
  "EPL - Remontées mécaniques",
  "EPL - Tourisme",
  "EPL - Transport",
  // FPIC : péréquation horizontale entre EI (1298 ensembles intercommunaux)
  "FPIC — Péréquation intercommunale",
  // Syndicats (BANATIC + OFGL syndicats — niveau dédié)
  "Syndicats — Abattoirs publics",
  "Syndicats — Accueil du jeune enfant : Crèches",
  "Syndicats — Accueil du jeune enfant : Maisons d'assistants maternels",
  "Syndicats — Accueil du jeune enfant : Relais petite enfance",
  "Syndicats — Action sociale communale",
  "Syndicats — Actions de développement économique dans les conditions prévues à l'article L. 4251-17 ; politique locale du commerce et soutien aux activités commerciales",
  "Syndicats — Actions de valorisation du patrimoine naturel et paysager",
  "Syndicats — Activités culturelles ou socioculturelles",
  "Syndicats — Activités périscolaires (activités culturelles, sportives, artistiques complémentaires aux enseignements scolaires)",
  "Syndicats — Activités sanitaires",
  "Syndicats — Activités sportives",
  "Syndicats — Aide sociale",
  "Syndicats — Amélioration du parc immobilier bâti",
  "Syndicats — Aménagement et gestion d'un parc naturel régional",
  "Syndicats — Aménagement, entretien et gestion des aires d'accueil des gens du voyage et des terrains familiaux locatifs",
  "Syndicats — Animation et concertation dans les domaines de la prévention du risque d'inondation ainsi que de la gestion et de la protection de la ressource en eau et des milieux aquatiques dans un sous-bassin ou un groupement de sous-bassins, ou dans un système aquifère, correspondant à une unité hydrographique (L.211-7-12° du code de l'environnement)",
  "Syndicats — Approvisionnement en eau (L. 211-7-3° du code de l'environnement)",
  "Syndicats — Assainissement collectif des eaux usées",
  "Syndicats — Assainissement non collectif des eaux usées",
  "Syndicats — Autres",
  "Syndicats — Centre de première intervention des services locaux d'incendie et de secours (L. 1424-36-4)",
  "Syndicats — Centre intercommunal d'action sociale",
  "Syndicats — Concession de la distribution publique d'électricité",
  "Syndicats — Concession de la distribution publique de gaz",
  "Syndicats — Constitution de réserves foncières (articles L.210-1 et L.221-1 du code de l'urbanisme)",
  "Syndicats — Construction, aménagement, entretien et fonctionnement d'équipements culturels et sportifs",
  "Syndicats — Construction, entretien et fonctionnement d'équipements de l'enseignement préélémentaire et élémentaire",
  "Syndicats — Construction, reconstruction, aménagement, entretien et fonctionnement des collèges (accueil, restauration, hébergement, entretien général et technique)",
  "Syndicats — Construction, reconstruction, aménagement, entretien et fonctionnement des lycées (accueil, restauration, hébergement, entretien général et technique)",
  "Syndicats — Contribution à la transition énergétique",
  "Syndicats — Création et entretien des infrastructures de charge nécessaires à l'usage des véhicules électriques ou hybrides rechargeables, en application de l'article L2224-37 du CGCT",
  "Syndicats — Création, aménagement, entretien de la voirie communale",
  "Syndicats — Création, aménagement, entretien et gestion de zones d'activité industrielle, commerciale, tertiaire, artisanale, touristique, portuaire ou aéroportuaire",
  "Syndicats — Création, aménagement, entretien et gestion des réseaux de chaleur ou de froid urbains",
  "Syndicats — Création, gestion, extension et translation des cimetières et sites cinéraires",
  "Syndicats — Définition, création et réalisation d'opérations d'aménagement d'intérêt communautaire au sens de l'article L.300-1 du code de l'urbanisme (les ZAC entrent dans cette catégorie)",
  "Syndicats — Délivrance des autorisations d'occupation du sol (Permis de construire...) (article L.422-3 du code de l'urbanisme)",
  "Syndicats — Eau (production, traitement, stockage, transport, distribution)",
  "Syndicats — Eclairage public",
  "Syndicats — Elaboration du diagnostic du territoire et définition des orientations du contrat de ville, animation et coordination des dispositifs contractuels de développement urbain, de développement local et d'insertion économique et sociale ainsi que des dispositifs locaux de prévention de la délinquance ; programmes d'actions définis dans le contrat de ville",
  "Syndicats — Elaboration et adoption du plan climat-air-énergie territorial en application de l'article L. 229-26 du code de l'environnement",
  "Syndicats — Entretien des bâtiments et espaces publics",
  "Syndicats — Exercice de la compétence collecte des déchets ménagers et assimilés",
  "Syndicats — Exercice de la compétence traitement des déchets ménagers et assimilés",
  "Syndicats — Exploitation d'aérodrome dont organisation de services aériens de transport public (L. 6321-2 du code des transports)",
  "Syndicats — Exploitation, entretien et aménagement d'ouvrages hydrauliques existants (L. 211-7 10° du code de l'environnement)",
  "Syndicats — GEMAPI : Aménagement d'un bassin ou d'une fraction de bassin hydrographique (L. 211-7 1° du code de l'environnement)",
  "Syndicats — GEMAPI : Défense contre les inondations et contre la mer (L. 211-7 5° du code de l'environnement)",
  "Syndicats — GEMAPI : Entretien et aménagement d'un cours d'eau, canal, lac ou plan d'eau (L. 211-7 2° du code de l'environnement)",
  "Syndicats — GEMAPI : Protection et restauration des sites, des écosystèmes aquatiques, des zones humides et des formations boisées riveraines (L. 211-7 8° du code de l'environnement)",
  "Syndicats — Garderie périscolaire",
  "Syndicats — Gestion de ports de plaisance ou de ports maritimes de commerce (L. 5314-4 du code des transports)",
  "Syndicats — Gestion des eaux pluviales urbaines",
  "Syndicats — Gestion des sentiers de randonnée",
  "Syndicats — Gestion des équipements touristiques",
  "Syndicats — Installation d'hydroélectricité, d'énergies renouvelables et autres installations visées à l'article L2224-32 du CGCT",
  "Syndicats — Lutte contre la pollution (L.211-7-6° du code de l'environnement)",
  "Syndicats — Maisons de santé pluridisciplinaires",
  "Syndicats — Marchés d'intérêt national, halles, foires et marchés",
  "Syndicats — Maîtrise des eaux pluviales et de ruissellement ou la lutte contre l'érosion des sols (L. 211-7 4° du code de l'environnement)",
  "Syndicats — Mise en place d'itinéraires cyclables",
  "Syndicats — Mise en place et exploitation de dispositifs de surveillance de la ressource en eau et des milieux aquatiques (L. 211-7 11 ° du code de l'environnement)",
  "Syndicats — Opération programmée d'amélioration de l'habitat (OPAH)",
  "Syndicats — Organisation de services réguliers / à la demande de transports publics de personnes, des services de mobilité solidaire, organisation ou contribution au développement des services relatifs aux mobilités actives définies à l'article L. 1271-1 du code des transports, organisation ou contribution au développement des services relatifs aux usages partagés des véhicules terrestres à moteur.",
  "Syndicats — Organisation de transports scolaires",
  "Syndicats — Plan local d'urbanisme et document d'urbanisme en tenant lieu (Art. L. 153-1 du code de l'urbanisme)",
  "Syndicats — Programme de soutien et d'aides aux établissements d'enseignement supérieur et de recherche et aux programmes de recherche",
  "Syndicats — Programme local de l'habitat",
  "Syndicats — Promotion du tourisme dont la création d'offices de tourisme et animation touristique",
  "Syndicats — Protection et la conservation des eaux superficielles et souterraines (L.211-7-7° du code de l'environnement) ",
  "Syndicats — Restauration scolaire",
  "Syndicats — Réseaux et services locaux de communications électroniques d'initiative publique au sens de l'article L 1425-1 CGCT",
  "Syndicats — Schéma de cohérence territoriale (SCOT) (Art. L. 143-16 code de l'urbanisme)",
  "Syndicats — Schéma de secteur (Art. L. 173-1 du code de l'urbanisme)",
  "Syndicats — Service extérieur de pompes funèbres (L. 2223-19 du CGCT)",
  "Syndicats — Service public de défense extérieure contre l'incendie",
  "Syndicats — Signalisation, abris de voyageurs, parcs et aires de stationnement",
  "Syndicats — Soutien aux actions de maîtrise d'énergie ",
  "Syndicats — Syndicat de transport de type SRU",
  "Syndicats — Transports publics non urbains (L. 3111-1 du code des transports)",
  "Spécifique régions",
  "Spécifique départements",
  "Spécifique intercommunalités",
  "Spécifique communes",
];

/** Retourne la liste des indicateurs pertinents pour un niveau donné. */
function getIndicatorsForLevel(level) {
  return INDICATORS.filter((ind) => ind.levels.includes(level));
}

// ----------------------------------------------------------------------------
// Palette Cividis
// ----------------------------------------------------------------------------
// Palette séquentielle conçue par Berkeley spécifiquement pour rester lisible
// par TOUS les types de daltonisme (deutéranopie, protanopie, tritanopie) tout
// en restant perceptuellement uniforme (chaque pas correspond à une variation
// visuelle constante). 7 paliers équidistants sur l'échelle Cividis :
// du bleu foncé (faibles valeurs) au jaune (valeurs élevées).
const PALETTE = [
  "#00204c", "#1c3c69", "#555b6c", "#7b7a77",
  "#a59c74", "#d3c065", "#ffe945",
];

// ViewBox France entière : on ajoute 12 unités de marge sur chaque côté
// (sur les 800×623 unités natives) pour que les halos de mise en évidence
// des régions/EPCIs au bord (Hauts-de-France au nord, Bretagne à l'ouest,
// PACA et Corse au sud) ne soient pas tronqués par le bord du viewBox.
// 12 unités ≈ 16 pixels à la taille viewport typique → couvre largement
// le halo le plus large (mode "triple" : 6.5 + 3.5 + 1.5 = 11.5 px outward).
const FRANCE_VIEWBOX = "-12 -12 824 647";

// SIREN de la Métropole du Grand Paris. Utilisé pour le fallback de lecture
// EPCI sur les communes Paris/PC (qui ont aussi un sirenEpt) et pour enrichir
// le panneau d'info EPT avec la valeur MGP côte à côte sur les 147
// indicateurs publiés par les deux niveaux (recettes/dépenses, dette,
// fiscalité directe…). Voir CLAUDE.md §6 « Métropole du Grand Paris ».
const MGP_SIREN = "200054781";

// ----------------------------------------------------------------------------
// Utilitaires
// ----------------------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/**
 * Formate une valeur numérique selon son unité — **toujours la valeur exacte**.
 *
 * Principe : zéro arrondi. On affiche tout ce que publie OFGL, jusqu'à 20
 * décimales (limite max d'Intl.NumberFormat). Cela inclut :
 *   - Les vraies décimales OFGL (1-4 pour les taux d'imposition, 1 pour €/hab)
 *   - Les artefacts de précision flottante OFGL (ex: "Annuité de la dette"
 *     en €/hab = montant ÷ population publiée par OFGL avec 17 décimales
 *     d'artefact IEEE 754, comme `14,818537951057653 €/hab`)
 *
 * Choix utilisateur explicite : fidélité totale, même au prix de la
 * lisibilité. Le CSS gère la largeur (les valeurs longues s'étendent ou
 * wrappent selon le contexte d'affichage).
 *
 * @param {number|null} value  La valeur à formater (null → "—")
 * @param {string} unit         L'unité ("€", "€/hab", "%", "ans", "MW", "coef", "")
 */
function formatValue(value, unit) {
  if (value == null || Number.isNaN(value)) return "—";

  // Précision d'AFFICHAGE par unité. NB : c'est un arrondi purement de
  // présentation — la donnée brute des fichiers JSON garde sa précision
  // complète. Le nombre de décimales dépend du sens de l'unité.
  let numOpts;
  if (unit === "coef") {
    // Ratios : 2 décimales fixes (homogénéité visuelle "1,00" / "1,05").
    numOpts = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  } else if (unit === "%" || unit === "ans") {
    // Pourcentages et durées : 1 décimale.
    numOpts = { maximumFractionDigits: 1 };
  } else if (unit === "MW") {
    // Puissance : 2 décimales (valeurs parfois petites).
    numOpts = { maximumFractionDigits: 2 };
  } else if (
    unit === "€" || unit === "€/hab" || unit === "€/km" ||
    unit === "€/élève" || unit === "km" || unit === "élèves" || unit === "hab"
  ) {
    // Montants en euros et dénombrements : entiers.
    numOpts = { maximumFractionDigits: 0 };
  } else {
    // Unité inconnue ou sans unité : arrondi raisonnable par défaut.
    numOpts = { maximumFractionDigits: 2 };
  }
  // Évite l'affichage trompeur "-0" : une valeur négative qui s'arrondit à
  // zéro à la précision retenue est affichée comme 0 (sans signe).
  if (Math.round(value * Math.pow(10, numOpts.maximumFractionDigits)) === 0) {
    value = 0;
  }
  const formatted = new Intl.NumberFormat("fr-FR", numOpts).format(value);

  // Suffixes d'unité
  if (unit === "€") return `${formatted} €`;
  if (unit === "€/hab") return `${formatted} €/hab`;
  if (unit === "%") return `${formatted} %`;
  if (unit === "ans") return `${formatted} ans`;
  if (unit === "MW") return `${formatted} MW`;
  if (unit === "km") return `${formatted} km`;
  if (unit === "élèves") return `${formatted} élèves`;
  if (unit === "€/km") return `${formatted} €/km`;
  if (unit === "€/élève") return `${formatted} €/élève`;
  if (unit === "hab") return `${formatted} hab.`;
  return formatted;
}

function formatPopulation(p) {
  if (p == null) return "—";
  // La population est un dénombrement : affichage en entier.
  return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 }).format(p) + " hab.";
}

function computeQuantileBreaks(values, n) {
  const sorted = [...values]
    .filter((v) => v != null && !Number.isNaN(v))
    .sort((a, b) => a - b);
  if (sorted.length === 0) return [];
  const breaks = [];
  for (let i = 1; i < n; i++) {
    const idx = (sorted.length * i) / n;
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    breaks.push((sorted[lo] + sorted[Math.min(hi, sorted.length - 1)]) / 2);
  }
  return breaks;
}

function classify(value, breaks) {
  if (value == null || Number.isNaN(value)) return -1;
  for (let i = 0; i < breaks.length; i++) {
    if (value <= breaks[i]) return i;
  }
  return breaks.length;
}

// ----------------------------------------------------------------------------
// Indicateurs CATÉGORIELS (interne-criteres-* : nature juridique, régime
// fiscal, QPV, strates OFGL…). Contrairement aux indicateurs numériques
// (dégradé via classify/PALETTE), la valeur stockée par année est un CODE
// string ; la coloration est DISCRÈTE (une couleur par catégorie) et la
// légende liste les catégories. Voir CLAUDE.md §6 « Contexte & critères ».
// ----------------------------------------------------------------------------

// Palette QUALITATIVE pour les indicateurs nominaux (catégories sans ordre).
// Couleurs choisies distinctes entre elles et du gris « donnée absente ».
const CATEGORICAL_PALETTE = [
  "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2",
  "#edc948", "#ff9da7", "#9c755f", "#bab0ac", "#86bcb6", "#d37295",
];

function isCategoricalIndicator(ind) {
  return !!(ind && ind.kind === "categorical" && Array.isArray(ind.categories));
}

/** Map mémoïsée code → couleur pour un indicateur catégoriel.
 *  - ordinal : rampe séquentielle réutilisant PALETTE (bas = bleu foncé,
 *    haut = jaune), cohérente avec le dégradé numérique.
 *  - nominal : palette qualitative cyclique. */
function categoryColorMap(ind) {
  if (ind._catColors) return ind._catColors;
  const m = new Map();
  const cats = ind.categories || [];
  if (ind.scale === "ordinal") {
    const n = cats.length;
    cats.forEach((c, i) => {
      const t = n <= 1 ? 0 : i / (n - 1);
      const pi = Math.round(t * (PALETTE.length - 1));
      m.set(String(c.code), PALETTE[pi]);
    });
  } else {
    cats.forEach((c, i) =>
      m.set(String(c.code), CATEGORICAL_PALETTE[i % CATEGORICAL_PALETTE.length]),
    );
  }
  ind._catColors = m;
  return m;
}

function categoryColor(ind, code) {
  if (code == null) return null;
  return categoryColorMap(ind).get(String(code)) || null;
}

function categoryLabel(ind, code) {
  if (code == null) return null;
  const c = (ind.categories || []).find((x) => String(x.code) === String(code));
  return c ? c.label : String(code);
}

/** Formate une valeur selon l'indicateur : libellé de catégorie si
 *  catégoriel, sinon formatValue numérique habituel. */
function formatIndicatorValue(ind, v) {
  if (isCategoricalIndicator(ind)) {
    return v == null ? "—" : categoryLabel(ind, v);
  }
  return formatValue(v, ind.unit);
}

/** Couleur de remplissage d'une entité pour l'indicateur courant.
 *  Renvoie une couleur CSS, ou null si « donnée absente ». Unifie le cas
 *  catégoriel (lookup discret) et numérique (classify + PALETTE). Pour le
 *  numérique, `breaks` doit être les seuils calculés ; pour le catégoriel
 *  il est ignoré (passer []). */
function colorForValue(ind, v, breaks) {
  if (isCategoricalIndicator(ind)) return categoryColor(ind, v);
  const cls = classify(v, breaks);
  return cls < 0 ? null : PALETTE[cls];
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ----------------------------------------------------------------------------
// État global
// ----------------------------------------------------------------------------

const state = {
  currentLevel: "regions",
  currentIndicator: null, // défini dans init() après loadIndicators()
  selectedId: null,

  /** Année sélectionnée (slider) et son index dans `years`. */
  currentYear: 2024,
  currentYearIdx: 0,
  /** Liste des années disponibles pour le niveau courant. */
  years: [],
  /** Mode de calcul des seuils de coloration :
   *   - "global" (défaut) : une seule échelle calculée sur toutes les années
   *     dispo. La couleur a un sens absolu, comparable d'une année à l'autre.
   *   - "yearly" : seuils recalculés à chaque année. Maximise le contraste
   *     spatial au sein d'une année mais empêche la comparaison temporelle. */
  scaleMode: "global",

  /** Entités du niveau courant : [{svg, id, label, data}] */
  currentEntities: [],
  /** Index id -> entité */
  currentEntityById: new Map(),
  /** Index id -> élément <path> du SVG courant */
  pathById: new Map(),

  /** Quantile breaks pour le niveau et l'indicateur courants */
  currentBreaks: [],

  /** Cache simple : LEVELS.{regions,departements} (objet {years, entities}) */
  cacheRegions: null,
  cacheDepartements: null,

  /** Mode communes : "overview" (départements affichés) ou "drilldown" (communes
   *  d'un département affichées). drillDownDepName est le nom du département
   *  cliqué dans le SVG départements (ex: "Aisne", "Corse"). */
  communesMode: "overview",
  drillDownDepName: null,

  /** Cache des fichiers by-dep/{code}.json déjà chargés */
  communesByDepCache: new Map(),
  /** Index global des fichiers by-dep (chargé une fois) */
  depIndex: null,

  /** Mode intercommunalités : analogue à communesMode mais le drill-down est
   *  par RÉGION (pas par EPCI individuel) — décision UX : on veut toujours
   *  voir des chiffres EPCI, jamais descendre au niveau commune.
   *
   *  - "overview"  : 35k communes coloriées par leur EPCI parent + ~18
   *    contours de régions cliquables en surimpression
   *  - "drilldown" : on a cliqué sur une région, on zoom et on affiche les
   *    communes de tous les EPCIs ayant ≥1 commune dans la région —
   *    y compris les communes hors-région des EPCIs à cheval (26 cas),
   *    pour que l'utilisateur voie le contour complet de l'EPCI.
   *  drillDownRegCode : code INSEE région (ex. "11" Île-de-France). */
  intercommunalitesMode: "overview",
  drillDownRegCode: null,
  drillDownRegName: null,

  /** Cache des fichiers by-epci/{siren}.json déjà chargés (réutilisé pour
   *  agréger les communes d'une région en drill-down). */
  epcisBySirenCache: new Map(),
  /** Index global des fichiers by-epci (1255 EPCIs avec leurs siren/bbox).
   *  Sert au panel pour récupérer les bbox et noms. */
  epciIndex: null,
  /** Index région -> liste de SIRENs EPCI à charger en drill-down + bbox
   *  cumulée. Format : [{reg_code, bbox, epcis: [siren, ...]}, ...]. */
  epciByRegionIndex: null,
  /** Synthese complète des EPCIs (chargée à l'entrée du niveau intercommunalites)
   *  Schéma : { years, indicators, entities: [{siren, nom, values, …}] } */
  epciEntities: null,
  /** Index siren_epci -> entité EPCI, pour les lookups rapides en mode overview */
  epciBySiren: null,

  /** Layer décoratif : 35 000 communes colorées affichées en arrière-plan
   *  pendant le mode communes overview, non interactives. Chargé une seule
   *  fois par session de navigation. */
  decorativeEntities: null,
  decorativeYears: null,
  decorativePathById: new Map(),

  /** Métadonnées légères des communes (nom, INSEE, dep_code, dep_name,
   *  population, siren_epci) indexées comme le décoratif. Chargé en lazy à
   *  la première ouverture du panneau en mode communes overview (utilisé pour
   *  le leaderboard national + le lookup commune→EPCI en mode intercommunalités).
   *  Format : tableau indexé par position
   *  ``[nom, insee, dep_code, dep_name, population, siren_epci]``. */
  communesMeta: null,

  /** Mode syndicats : analogue à communesMode mais le drill-down est
   *  par DÉPARTEMENT. En overview, on voit toute la France ; en drilldown,
   *  on zoom sur le département cliqué et le leaderboard filtre les
   *  syndicats ayant ≥1 commune membre dans ce département.
   *  drillDownSyndDepCodes : tableau de codes département (ex. ["87"],
   *  ["2A","2B"] pour la Corse vue comme une seule entité). */
  syndicatsMode: "overview",
  drillDownSyndDepCodes: null,
  drillDownSyndDepName: null,
  /** Syndicat sélectionné en mode syndicats : son SIREN (lookup vers le
   *  payload du leaderboard pour afficher les détails dans le panel). */
  selectedSyndicatSiren: null,
};

let _selectedPath = null;
/** Liste des paths "halo" dupliqués pour la mise en évidence (1 ou 2 selon
 *  le mode : 1 pour standard/vivid/pulse, 2 pour triple, 0 pour glow). */
let _selectionHalos = [];
/** En sélection multi-paths (drill-down région EPCI), tableau des paths
 *  qui ont reçu la classe `--selected`. Utilisé pour la retirer au clear. */
let _selectedPathsMulti = [];

// ----------------------------------------------------------------------------
// Helpers multi-années
// ----------------------------------------------------------------------------

/** Récupère la valeur d'un indicateur pour l'année courante (depuis l'objet
 *  `data` d'une entité, qui contient `data.values[indicator] = [v_y1, ...]`). */
function getValueForYear(data, indicatorKey, yearIdx = state.currentYearIdx) {
  if (!data || !data.values) return null;
  const serie = data.values[indicatorKey];
  if (!serie) return null;
  return serie[yearIdx] ?? null;
}

/** Récupère la population pour l'année courante. Pour régions/dpts, c'est
 *  un array multi-années ; pour les communes, un nombre unique (issu de la
 *  liste INSEE millésimée). */
function getPopulationForYear(data, yearIdx = state.currentYearIdx) {
  if (!data || data.population == null) return null;
  if (Array.isArray(data.population)) {
    return data.population[yearIdx] ?? null;
  }
  return data.population;
}

/** Collecte toutes les valeurs d'un indicateur sur toutes les années pour
 *  l'ensemble des entités fournies. Sert au calcul des seuils GLOBAUX :
 *  les classes de couleur sont identiques quelle que soit l'année affichée,
 *  ce qui rend la comparaison temporelle directement lisible (la couleur a
 *  un sens absolu, pas relatif à l'année courante). */
function collectAllValues(entities, indicatorKey) {
  const all = [];
  for (const ent of entities) {
    const serie = ent?.data?.values?.[indicatorKey];
    if (!serie) continue;
    for (const v of serie) {
      if (v != null && !Number.isNaN(v)) all.push(v);
    }
  }
  return all;
}

/** Retourne les valeurs servant au calcul des seuils selon le mode courant :
 *  "global" → toutes années confondues ; "yearly" → seulement l'année courante. */
function collectScaleValues(entities, indicatorKey, yearIdx) {
  if (state.scaleMode === "yearly") {
    return entities
      .map((e) => getValueForYear(e.data, indicatorKey, yearIdx))
      .filter((v) => v != null && !Number.isNaN(v));
  }
  return collectAllValues(entities, indicatorKey);
}

/** Calcule [min, max] sur un tableau numérique sans utiliser le spread.
 *  Important : `Math.min(...arr)` et `Math.max(...arr)` passent chaque
 *  valeur comme argument à la fonction, ce qui déclenche un
 *  RangeError "Maximum call stack size exceeded" dès que `arr` dépasse
 *  ~65 000 éléments (limite d'arguments du moteur JS). En communes
 *  overview avec scaleMode="global" on a ~35 000 communes × ~8 années
 *  = ~280 000 valeurs, largement au-dessus du seuil.
 *  Renvoie [null, null] pour un tableau vide. */
function arrayMinMax(values) {
  if (!values || values.length === 0) return [null, null];
  let min = values[0];
  let max = values[0];
  for (let i = 1; i < values.length; i++) {
    const v = values[i];
    if (v < min) min = v;
    else if (v > max) max = v;
  }
  return [min, max];
}

/** Sauvegarde / restaure la préférence du mode d'échelle en localStorage. */
function loadScalePreference() {
  try {
    const saved = localStorage.getItem("scaleMode");
    if (saved === "global" || saved === "yearly") {
      state.scaleMode = saved;
    }
  } catch (_) {
    /* localStorage indisponible (mode privé strict) : on garde le défaut */
  }
}
function saveScalePreference() {
  try {
    localStorage.setItem("scaleMode", state.scaleMode);
  } catch (_) {}
}

/** Met à jour l'année courante et recalcule l'index. */
function setYears(years) {
  state.years = years || [];
  if (state.years.length === 0) {
    state.currentYearIdx = 0;
    return;
  }
  // Conserver l'année courante si dispo dans la nouvelle plage,
  // sinon prendre la dernière (la plus récente).
  let idx = state.years.indexOf(state.currentYear);
  if (idx < 0) {
    idx = state.years.length - 1;
    state.currentYear = state.years[idx];
  }
  state.currentYearIdx = idx;
}

// ----------------------------------------------------------------------------
// Chargement des données
// ----------------------------------------------------------------------------

async function loadJson(url) {
  // Les données servies en ligne sont compressées en gzip (fichiers `.json.gz`)
  // pour réduire le poids hébergé. On tente d'abord la version compressée et on
  // la décompresse directement dans le navigateur via DecompressionStream (API
  // standard, supportée par tous les navigateurs récents). Si le `.gz` est
  // absent (ex. développement local sur des fichiers non compressés), on
  // retombe automatiquement sur le `.json` brut — la même fonction marche donc
  // dans les deux cas, sans toucher aux appelants.
  if (typeof DecompressionStream !== "undefined") {
    try {
      const res = await fetch(url + ".gz");
      if (res.ok && res.body) {
        const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
        return await new Response(stream).json();
      }
    } catch (_e) {
      // Réseau ou décompression KO → on bascule sur le `.json` brut ci-dessous.
    }
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} pour ${url}`);
  return res.json();
}

/** Charge un niveau simple (régions ou départements) et construit ses entités.
 *  Format multi-années : la synthèse retourne {years, indicators, entities}.
 *  Chaque entité enrichie a `data.values[indicateur] = [v_y1, v_y2, ...]`
 *  et `data.population = [pop_y1, pop_y2, ...]`. La fonction renvoie
 *  ``{years, entities}`` ; les entités contiennent les noms et valeurs
 *  multi-années dans `data`. */
async function loadSimpleLevel(levelKey) {
  const cacheKey = levelKey === "regions" ? "cacheRegions" : "cacheDepartements";
  if (state[cacheKey]) return state[cacheKey];

  const cfg = LEVELS[levelKey];
  const [svg, synth] = await Promise.all([
    loadJson(cfg.svgUrl),
    loadJson(cfg.dataUrl),
  ]);
  const fra = svg.filter((s) => s.niveau_zoom === cfg.svgZoom);

  // Index par nom : `synth.entities` contient des objets {code, name, meta, population, values}
  const byName = new Map();
  for (const ent of synth.entities) byName.set(ent.name, ent);

  const entities = fra.map((s) => {
    const synthEnt = byName.get(s[cfg.svgIdKey]);
    let data = null;
    if (synthEnt) {
      // Mise à plat des metas pour rester compatible avec l'API existante
      // (panelSubtitle, notes lisent ent.data.reg_code, ent.data.outre_mer, etc.)
      data = {
        [cfg.dataCodeKey]: synthEnt.code,
        [cfg.dataIdKey]: synthEnt.name,
        ...synthEnt.meta,
        population: synthEnt.population, // array multi-années
        values: synthEnt.values, // {indicator: [serie multi-années]}
      };
    }
    return {
      svg: s,
      id: String(s[cfg.svgIdKey]),
      label: s[cfg.svgLabelKey],
      data,
    };
  });

  const result = { years: synth.years, entities };
  state[cacheKey] = result;
  return result;
}

/** Charge la vue overview du niveau communes : les contours des départements,
 *  affichés en gris uniforme. C'est le SVG des départements (déjà dispo) qu'on
 *  réutilise comme couche cliquable pour entrer en drill-down. */
async function loadCommunesOverview() {
  // On réutilise le cache des départements pour les contours et les noms
  const dep = await loadSimpleLevel("departements");
  const depEntities = dep.entities;

  // Toutes les entités SVG départements deviennent cliquables même si elles
  // n'ont pas de data départementale (Corse, Martinique, Guyane CTU).
  // En mode communes overview, on n'affiche pas les indicateurs mais on
  // rend tout cliquable pour le drill-down.
  return depEntities.map((e) => ({
    svg: e.svg,
    id: e.label,            // on indexe par nom (cohérent avec le SVG)
    label: e.label,
    data: null,             // pas de data en overview
    depDataCodes: DEPS_FILES_BY_DEP_NAME[e.label] || (e.data ? [e.data.dep_code] : null),
  }));
}

/** Charge l'index global des fichiers by-dep (101 départements avec leurs
 *  noms, codes et bbox). Mis en cache après le premier appel. */
async function loadDepIndex() {
  if (state.depIndex) return state.depIndex;
  state.depIndex = await loadJson("data/communes/by-dep/_index.json");
  return state.depIndex;
}

/** Promesse mémoïsée du chargement décoratif : permet de gérer plusieurs
 *  appels concurrents (le 2e attend la même promesse au lieu de relancer). */
let _decorativeLoadPromise = null;

/** Charge les contours SVG des 35 000 communes pour le layer décoratif.
 *
 *  Architecture LAZY-LOADING : ce fichier (`decoratif-paths-2024.json`)
 *  contient UNIQUEMENT les contours SVG (~100 Mo). Les valeurs des
 *  indicateurs sont chargées séparément à la demande, indicateur par
 *  indicateur, via `ensureDecorativeIndicatorLoaded(indKey)` qui fetch
 *  `data/communes/decoratif-values/{slug}.json` (~1-3 Mo par indicateur).
 *
 *  Cette séparation évite l'OOM Chrome qui se produisait avec un décoratif
 *  monolithique de 500+ Mo contenant 300+ indicateurs.
 *
 *  Le fetch + JSON.parse (~500-1000 ms) + transformation (~50-100 ms) sont
 *  délégués à un Web Worker dédié → le main thread reste fluide.
 *  Fallback synchrone si Worker indisponible.
 *  Cache permanent : appelé une seule fois par session. */
function loadDecorativeCommunes() {
  if (state.decorativeEntities) return Promise.resolve(state.decorativeEntities);
  if (_decorativeLoadPromise) return _decorativeLoadPromise;

  const url = "data/communes/decoratif-paths-2024.json";

  if (typeof Worker === "undefined") {
    // Fallback : pas de support Worker. Parse dans le main thread.
    _decorativeLoadPromise = loadDecorativeCommunesMainThread(url);
    return _decorativeLoadPromise;
  }

  _decorativeLoadPromise = new Promise((resolve, reject) => {
    const worker = new Worker("assets/js/decoratif-worker.js");
    worker.onmessage = (ev) => {
      worker.terminate();
      if (ev.data.error) {
        reject(new Error(ev.data.error));
        return;
      }
      state.decorativeEntities = ev.data.entities;
      state.decorativeYears = ev.data.years || ANNEES_COMMUNES_FALLBACK;
      // Le meta peut avoir été chargé en parallèle. Si oui, on relie
      // immédiatement les noms aux entités fraîchement parsées.
      if (state.communesMeta) hydrateDecorativeWithMeta();
      resolve(state.decorativeEntities);
    };
    worker.onerror = (err) => {
      worker.terminate();
      // Fallback : si la création/exécution du worker échoue (ex. erreur de
      // chemin en dev), on retombe sur le parse main thread plutôt que
      // d'échouer silencieusement.
      console.warn(
        "Web Worker indisponible, fallback main thread :",
        err.message,
      );
      loadDecorativeCommunesMainThread(url).then(resolve, reject);
    };
    // Important : on passe une URL **absolue** au worker. Côté worker, une
    // URL relative serait résolue par rapport au script du worker
    // (`assets/js/decoratif-worker.js`), pas par rapport à la page →
    // produirait `assets/js/data/communes/...` (404).
    const absoluteUrl = new URL(url, self.location.href).href;
    worker.postMessage({ url: absoluteUrl });
  });

  return _decorativeLoadPromise;
}

/** Implémentation de secours qui fait tout sur le main thread, pour les
 *  navigateurs sans support Web Worker (rare en 2025+) ou en cas d'échec
 *  d'instanciation du worker. */
async function loadDecorativeCommunesMainThread(url) {
  const data = await loadJson(url);
  const paths = data.paths || [];
  state.decorativeEntities = paths.map((d, idx) => ({
    svg: { d },
    id: idx,
    label: null,
    data: { values: {} },
  }));
  state.decorativeYears = data.years || ANNEES_COMMUNES_FALLBACK;
  if (state.communesMeta) hydrateDecorativeWithMeta();
  return state.decorativeEntities;
}

/** Cache des index de slugs pour les fichiers decoratif-values/.
 *  Chargé une fois la première fois qu'on en a besoin, et conservé en
 *  mémoire pour tout le reste de la session. */
let _decorativeValuesIndex = null;
let _decorativeValuesIndexPromise = null;
// Index séparé pour les valeurs syndicats (format sparse)
let _decorativeSyndicatsIndex = null;
let _decorativeSyndicatsIndexPromise = null;

function loadDecorativeValuesIndex() {
  if (_decorativeValuesIndex) return Promise.resolve(_decorativeValuesIndex);
  if (_decorativeValuesIndexPromise) return _decorativeValuesIndexPromise;
  _decorativeValuesIndexPromise = loadJson(
    "data/communes/decoratif-values/_index.json",
  ).then((idx) => {
    _decorativeValuesIndex = idx || {};
    return _decorativeValuesIndex;
  });
  return _decorativeValuesIndexPromise;
}

function loadDecorativeSyndicatsIndex() {
  if (_decorativeSyndicatsIndex) return Promise.resolve(_decorativeSyndicatsIndex);
  if (_decorativeSyndicatsIndexPromise) return _decorativeSyndicatsIndexPromise;
  _decorativeSyndicatsIndexPromise = loadJson(
    "data/syndicats/decoratif-values/_index.json",
  ).then((idx) => {
    _decorativeSyndicatsIndex = idx || {};
    return _decorativeSyndicatsIndex;
  });
  return _decorativeSyndicatsIndexPromise;
}

// ----------------------------------------------------------------------------
// Détails syndicat : 1 fichier par syndicat (data/syndicats/details/{siren}.json)
// contenant tous les agrégats financiers + métadonnées. Lazy-chargé au clic
// pour afficher le panneau complet.
// ----------------------------------------------------------------------------
const _syndicatDetailFiles = new Map();
async function loadSyndicatDetailFile(siren) {
  if (!siren) return null;
  const cached = _syndicatDetailFiles.get(siren);
  if (cached !== undefined) return cached;
  const promise = (async () => {
    try {
      const payload = await loadJson(`data/syndicats/details/${siren}.json`);
      _syndicatDetailFiles.set(siren, payload);
      return payload;
    } catch (err) {
      console.warn(`Échec chargement détail syndicat ${siren}:`, err.message || err);
      _syndicatDetailFiles.delete(siren);
      return null;
    }
  })();
  _syndicatDetailFiles.set(siren, promise);
  return promise;
}

// ----------------------------------------------------------------------------
// Leaderboard syndicats : 1 ligne = 1 SYNDICAT (≠ décoratif qui produit
// 1 valeur par commune membre). Index + fichiers chargés à la demande.
// ----------------------------------------------------------------------------
let _syndicatsLeaderboardIndex = null;
let _syndicatsLeaderboardIndexPromise = null;
/** Cache { indicatorKey -> payload | Promise<payload> } pour les fichiers
 *  individuels de leaderboard (≤ 116 Mo cumulés, mais chargés un par un). */
const _syndicatsLeaderboardFiles = new Map();

function loadSyndicatsLeaderboardIndex() {
  if (_syndicatsLeaderboardIndex) return Promise.resolve(_syndicatsLeaderboardIndex);
  if (_syndicatsLeaderboardIndexPromise) return _syndicatsLeaderboardIndexPromise;
  _syndicatsLeaderboardIndexPromise = loadJson(
    "data/syndicats/leaderboards/_index.json",
  ).then((idx) => {
    _syndicatsLeaderboardIndex = idx || {};
    return _syndicatsLeaderboardIndex;
  });
  return _syndicatsLeaderboardIndexPromise;
}

/** Charge le fichier leaderboard d'un indicateur syndicats, avec déduplication
 *  des appels concurrents. Résout vers le payload { syndicats: [...] } ou
 *  vers null si l'indicateur n'a pas de leaderboard (cas vide ignoré côté
 *  build, ou clé non syndicats). */
async function loadSyndicatsLeaderboardFile(indicatorKey) {
  if (!indicatorKey || !indicatorKey.startsWith("Syndicats ")) return null;
  const cached = _syndicatsLeaderboardFiles.get(indicatorKey);
  if (cached !== undefined) return cached;

  const promise = (async () => {
    const index = await loadSyndicatsLeaderboardIndex();
    const slug = index[indicatorKey];
    if (!slug) return null;
    try {
      const payload = await loadJson(`data/syndicats/leaderboards/${slug}.json`);
      _syndicatsLeaderboardFiles.set(indicatorKey, payload);
      return payload;
    } catch (err) {
      console.warn(
        `Échec chargement leaderboard syndicats "${indicatorKey}":`,
        err.message || err,
      );
      _syndicatsLeaderboardFiles.delete(indicatorKey);
      return null;
    }
  })();
  _syndicatsLeaderboardFiles.set(indicatorKey, promise);
  return promise;
}

/** Map { indicatorKey -> Promise<void> | true } : déduplication des
 *  fetches concurrents pour un même indicateur, et marque les indicateurs
 *  déjà chargés (la valeur `true` indique "valeurs déjà injectées dans
 *  les entités décoratives"). */
const _decorativeIndicatorState = new Map();

/** Vrai si l'indicateur n'est pas encore chargé dans les entités
 *  décoratives. Utilisé pour décider si on doit déclencher un fetch
 *  avant de colorier la carte France entière. */
function _needsDecorativeValuesLoad(indicatorKey) {
  if (!indicatorKey) return false;
  if (!state.decorativeEntities) return false;
  return _decorativeIndicatorState.get(indicatorKey) !== true;
}

/** Charge les valeurs d'un indicateur dans le décoratif (lazy).
 *
 *  Garantit qu'à la résolution de la Promise, chaque
 *  `state.decorativeEntities[i].data.values[indicatorKey]` contient sa
 *  série multi-années (ou `null` si la commune n'a pas de donnée pour
 *  cet indicateur).
 *
 *  Idempotent + concurrence-safe : appels multiples concurrents pour le
 *  même indicateur partagent une seule requête réseau.
 *
 *  @param {string} indicatorKey  Clé d'indicateur (ex: "Recettes totales")
 *  @returns {Promise<boolean>}    true si les valeurs sont chargées, false sinon
 *                                  (échec réseau ou indicateur inexistant côté server)
 */
async function ensureDecorativeIndicatorLoaded(indicatorKey) {
  if (!indicatorKey) return false;
  if (!state.decorativeEntities) {
    // Les entités doivent être chargées d'abord (paths)
    await loadDecorativeCommunes();
  }
  const existing = _decorativeIndicatorState.get(indicatorKey);
  if (existing === true) return true;
  if (existing instanceof Promise) return existing;

  // Détecter si c'est un indicateur Syndicats (clé commence par "Syndicats ")
  // → format sparse, chemin différent (data/syndicats/decoratif-values/)
  const isSyndicat = indicatorKey.startsWith("Syndicats ");
  // Les EPL sont aussi en format sparse (1510 indicateurs très épars,
  // souvent < 100 communes par indicateur sur 35 000+) mais stockés dans
  // data/communes/decoratif-values/ avec les autres indicateurs communes.
  const isEpl = indicatorKey.startsWith("EPL ");

  const promise = (async () => {
    let index, baseUrl, isSparse;
    if (isSyndicat) {
      index = await loadDecorativeSyndicatsIndex();
      baseUrl = "data/syndicats/decoratif-values";
      isSparse = true;
    } else {
      index = await loadDecorativeValuesIndex();
      baseUrl = "data/communes/decoratif-values";
      isSparse = isEpl;
    }
    const slug = index[indicatorKey];
    if (!slug) {
      // Indicateur sans fichier values (peut-être un indicateur EPCI/dept
      // pas exposé au niveau commune). Pas une erreur : juste pas de
      // valeurs à injecter.
      _decorativeIndicatorState.set(indicatorKey, true);
      return false;
    }
    let payload;
    try {
      payload = await loadJson(`${baseUrl}/${slug}.json`);
    } catch (err) {
      console.warn(
        `Échec chargement ${baseUrl}/${slug}.json pour "${indicatorKey}":`,
        err.message || err,
      );
      _decorativeIndicatorState.delete(indicatorKey);
      return false;
    }
    const ents = state.decorativeEntities;

    if (isSparse) {
      // Format sparse syndicats : `values_sparse: [[idx, v0, ..., v7], ...]`
      // On ne stocke des valeurs QUE pour les communes concernées ; les
      // autres sont laissées à `undefined` (= absentes du Map values).
      const sparse = payload && payload.values_sparse;
      if (!Array.isArray(sparse)) {
        console.warn(
          `${baseUrl}/${slug}.json : format invalide (pas d'array "values_sparse")`,
        );
        _decorativeIndicatorState.delete(indicatorKey);
        return false;
      }
      for (const row of sparse) {
        if (!Array.isArray(row) || row.length < 2) continue;
        const idx = row[0];
        if (idx < 0 || idx >= ents.length) continue;
        const serie = row.slice(1);
        ents[idx].data.values[indicatorKey] = serie;
      }
    } else {
      // Format dense communes : `values: [serie_idx0, serie_idx1, ...]`
      const values = payload && payload.values;
      if (!Array.isArray(values)) {
        console.warn(
          `${baseUrl}/${slug}.json : format invalide (pas d'array "values")`,
        );
        _decorativeIndicatorState.delete(indicatorKey);
        return false;
      }
      const n = Math.min(values.length, ents.length);
      for (let i = 0; i < n; i++) {
        ents[i].data.values[indicatorKey] = values[i];
      }
    }

    _decorativeIndicatorState.set(indicatorKey, true);
    return true;
  })();

  _decorativeIndicatorState.set(indicatorKey, promise);
  return promise;
}

/** Charge `meta-communes-2024.json` : noms + INSEE + département + population
 *  indexés positionnellement, alignés sur `decorativeEntities`. Léger
 *  (~1.5 Mo brut), lazy-loaded au moment où on a besoin d'afficher le
 *  leaderboard national des communes.
 *
 *  Effet de bord : enrichit chaque `decorativeEntities[i]` avec les champs
 *  `label`, `insee`, `depCode`, `depName`, `population` issus du meta, de
 *  façon à ce que `renderLeaderboardHTML()` puisse utiliser les noms sans
 *  modifier sa signature. Mémoïsé via `state.communesMeta` et la promesse
 *  `_communesMetaPromise`. */
let _communesMetaPromise = null;
function loadCommunesMeta() {
  if (state.communesMeta) return Promise.resolve(state.communesMeta);
  if (_communesMetaPromise) return _communesMetaPromise;

  _communesMetaPromise = (async () => {
    const meta = await loadJson("data/communes/meta-communes-2024.json");
    state.communesMeta = meta.communes;
    // Hydrate les entités décoratives avec les noms si elles sont chargées
    if (state.decorativeEntities) hydrateDecorativeWithMeta();
    return state.communesMeta;
  })();
  return _communesMetaPromise;
}

/** Injecte les labels et codes de département dans les `decorativeEntities`
 *  à partir du meta chargé. Idempotent : peut être appelé plusieurs fois
 *  (par exemple si le décoratif termine après le meta, ou inversement). */
function hydrateDecorativeWithMeta() {
  if (!state.decorativeEntities || !state.communesMeta) return;
  const meta = state.communesMeta;
  const ents = state.decorativeEntities;
  // Garde-fou : alignement positionnel attendu. Si la longueur diffère,
  // on n'hydrate pas pour ne pas corrompre les correspondances (l'utilisateur
  // verra alors le placeholder « impossible de charger les noms »).
  if (ents.length !== meta.length) {
    console.warn(
      `Désalignement décoratif (${ents.length}) ≠ meta (${meta.length}) — leaderboard national désactivé.`,
    );
    return;
  }
  for (let i = 0; i < ents.length; i++) {
    // Format meta : [nom, insee, dep_code, dep_name, population, (siren_epci?)]
    // `siren_epci` est optionnel (présent uniquement si fetch_epci.py a été
    // exécuté côté Python pour enrichir meta).
    const m = meta[i];
    ents[i].label = m[0];
    ents[i].insee = m[1];
    ents[i].depCode = m[2];
    ents[i].depName = m[3];
    ents[i].population = m[4];
    ents[i].sirenEpci = m[5] || null;
    // sirenEpt : pointeur additionnel vers l'EPT (Établissement Public
    // Territorial) pour les ~130 communes de Paris+petite couronne, qui
    // sont rattachées simultanément à la MGP (siren_epci) et à un EPT.
    // Utilisé en fallback dans applyDecorativeColors pour lire les vraies
    // valeurs OFGL au niveau EPT quand la MGP n'a pas l'indicateur (cas
    // FPIC notamment). Pour les ~34 800 autres communes : null.
    ents[i].sirenEpt = m[6] || null;
  }
}

/** Charge les communes d'un département (par nom du polygone SVG). Renvoie
 *  les entités et la bbox englobante pour adapter la viewBox. */
async function loadCommunesForDepartement(depName) {
  // Liste des codes à charger : soit cas particulier (Corse), soit lookup
  // par nom dans l'index global (qui inclut les CTU et tous les alias).
  let codes;
  if (DEPS_FILES_BY_DEP_NAME[depName]) {
    codes = DEPS_FILES_BY_DEP_NAME[depName];
  } else {
    const idx = await loadDepIndex();
    // Cas Alsace : dep_name="Alsace" apparaît 3 fois dans l'index (codes
    // "67", "67A", "68"). On veut le fichier consolidé "67A" qui contient
    // les 880 communes des deux ex-départements. Heuristique générale :
    // si plusieurs entrées partagent le même nom, on prend celle avec le
    // count le plus élevé (= la version consolidée / canonique de
    // synthese-departements). Pour les noms uniques, le filtrage retombe
    // sur l'unique match.
    const matches = idx.filter((d) => d.dep_name === depName);
    if (matches.length === 0) {
      throw new Error(`Aucun fichier by-dep pour "${depName}"`);
    }
    const best = matches.reduce(
      (a, b) => ((b.count || 0) > (a.count || 0) ? b : a),
    );
    codes = [best.dep_code];
  }

  // Charger tous les fichiers nécessaires (généralement 1, 2 pour la Corse)
  const payloads = await Promise.all(
    codes.map(async (code) => {
      if (state.communesByDepCache.has(code)) {
        return state.communesByDepCache.get(code);
      }
      const payload = await loadJson(`data/communes/by-dep/${code}.json`);
      state.communesByDepCache.set(code, payload);
      return payload;
    }),
  );

  // Concaténer les communes
  const communes = payloads.flatMap((p) => p.communes);

  // BBox englobante (union dans le cas Corse 2A + 2B)
  const bboxes = payloads.map((p) => p.bbox);
  const bbox = {
    x_min: Math.min(...bboxes.map((b) => b.x_min)),
    x_max: Math.max(...bboxes.map((b) => b.x_max)),
    y_min: Math.min(...bboxes.map((b) => b.y_min)),
    y_max: Math.max(...bboxes.map((b) => b.y_max)),
  };

  // Années disponibles (toutes les payloads partagent le même set)
  const years = payloads[0]?.years || ANNEES_COMMUNES_FALLBACK;

  const entities = communes.map((c) => ({
    svg: c.svg,
    id: String(c.svg.data_fill_id),
    label: c.svg.nom_com,
    // c.data contient déjà {insee, nom, siren, nom_dep, nom_reg, nom_gfp,
    // population, values: {ind: [v_y1, v_y2, v_y3]}}
    data: c.data,
  }));

  return { entities, bbox, years };
}

/** Plage d'années par défaut pour les communes (si l'index n'est pas chargé). */
const ANNEES_COMMUNES_FALLBACK = [2022, 2023, 2024];
const ANNEES_REG_DEP_FALLBACK = Array.from({ length: 13 }, (_, i) => 2012 + i);
/** EPCI : période OFGL ofgl-base-gfp = 2017-2024 (cohérent avec communes). */
const ANNEES_EPCI_FALLBACK = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024];

// ----------------------------------------------------------------------------
// Loaders pour le niveau « Intercommunalités »
// ----------------------------------------------------------------------------

/** Charge la synthese-intercommunalites-2024.json (~11 Mo, 1335 EPCIs ×
 *  54 indicateurs × 8 ans). Mémoïsée pour la session. Indexe aussi par siren
 *  pour les lookups rapides (utilisé par la coloration overview communes-par-EPCI). */
async function loadIntercommunalitesOverview() {
  if (state.epciEntities) return state.epciEntities;
  const payload = await loadJson(
    "data/intercommunalites/synthese-intercommunalites-2024.json",
  );
  // On construit la même structure d'entité que pour les autres niveaux,
  // pour réutiliser le pipeline existant (panel, leaderboard, etc.).
  const entities = payload.entities.map((e) => ({
    svg: null, // pas de SVG dédié EPCI (cf. approche A)
    id: e.siren, // l'id stable = le SIREN à 9 chiffres
    label: e.nom,
    data: e, // toute la donnée brute (population multi-années + values + meta)
  }));
  state.epciEntities = entities;
  state.epciYears = payload.years;
  state.epciIndicators = payload.indicators;
  // Index pour les lookups O(1) commune→EPCI lors de la coloration overview
  state.epciBySiren = new Map();
  for (const ent of entities) {
    state.epciBySiren.set(ent.id, ent);
  }
  return entities;
}

/** Charge l'index des EPCIs (siren, nom, bbox, dep_codes, nb_communes).
 *  Sert au panel pour les libellés et bbox individuelles. */
async function loadEpciIndex() {
  if (state.epciIndex) return state.epciIndex;
  state.epciIndex = await loadJson("data/intercommunalites/by-epci/_index.json");
  return state.epciIndex;
}

/** Charge l'index région → EPCIs (mapping pour le drill-down par région).
 *  Léger (~30 Ko), mémoïsé. Format : [{reg_code, bbox, epcis, nb_epcis}, ...]. */
async function loadEpciByRegionIndex() {
  if (state.epciByRegionIndex) return state.epciByRegionIndex;
  state.epciByRegionIndex = await loadJson(
    "data/intercommunalites/by-region/_index.json",
  );
  return state.epciByRegionIndex;
}

/** Charge toutes les communes des EPCIs présents dans une région donnée.
 *  Approche : on lit l'index by-region pour obtenir la liste des sirens et
 *  la bbox, on charge en parallèle tous les by-epci/{siren}.json (chacun
 *  mémoïsé) et on concatène. Chaque commune est annotée avec son `sirenEpci`
 *  pour faciliter la coloration et le clic côté carto.
 *
 *  IMPORTANT : pour les EPCIs à cheval sur plusieurs régions (26 cas), on
 *  charge **toutes leurs communes membres**, y compris celles hors-région.
 *  Cela permet à l'utilisateur de voir le contour complet de l'EPCI dans
 *  le drill-down — comportement UX demandé. */
async function loadCommunesForRegion(regCode) {
  const idx = await loadEpciByRegionIndex();
  const reg = idx.find((r) => r.reg_code === regCode);
  if (!reg) throw new Error(`Région ${regCode} introuvable dans l'index EPCI`);

  // Récupérer le nom de la région depuis le niveau régions (déjà chargé
  // ou chargeable). Le nom n'est pas stocké côté serveur dans by-region
  // pour rester minimal.
  const regions = await loadSimpleLevel("regions");
  const regEnt = regions.entities.find(
    (e) => e.data && e.data.reg_code === regCode,
  );
  const regName = regEnt ? regEnt.label : `Région ${regCode}`;

  // Charge tous les by-epci correspondants (mémoïsé) + meta-communes
  // (pour récupérer le sirenEpt des communes Paris/PC).
  const sirens = reg.epcis;
  const [epciPayloads] = await Promise.all([
    Promise.all(sirens.map((s) => loadCommunesForEpci(s))),
    loadCommunesMeta(),
  ]);

  // Build INSEE → sirenEpt map depuis meta (seules les ~130 communes
  // Paris/PC ont une valeur ; pour les autres communes c'est null).
  const inseeToSirenEpt = new Map();
  if (state.communesMeta) {
    for (const m of state.communesMeta) {
      // m = [nom, insee, dep_code, dep_name, population, siren_epci, siren_ept]
      if (m && m[6]) inseeToSirenEpt.set(m[1], m[6]);
    }
  }

  // Aplatit toutes les communes en annotant avec siren_epci (EPCI du loop)
  // et siren_ept (pour le fallback lecture en drill-down EPCI région —
  // sans ça les communes MGP restent grises sur les indicateurs FPIC où
  // seul l'EPT publie une valeur OFGL).
  const allCommunes = [];
  for (let i = 0; i < sirens.length; i++) {
    const siren = sirens[i];
    const payload = epciPayloads[i];
    for (const e of payload.entities) {
      const insee = e.data?.insee;
      const sirenEpt = (insee && inseeToSirenEpt.get(insee)) || null;
      allCommunes.push({ ...e, sirenEpci: siren, sirenEpt });
    }
  }

  return {
    entities: allCommunes,
    bbox: reg.bbox,
    // Plage d'années EPCI (alignée sur ofgl-base-gfp 2017-2024).
    years: state.epciYears || ANNEES_EPCI_FALLBACK,
    regCode,
    regName,
    sirens: new Set(sirens),
  };
}

/** Charge les communes membres d'un EPCI (par siren). Mémoïsé. Renvoie le
 *  même format que loadCommunesForDepartement : {entities, bbox, years}. */
async function loadCommunesForEpci(sirenEpci) {
  if (state.epcisBySirenCache.has(sirenEpci)) {
    return state.epcisBySirenCache.get(sirenEpci);
  }
  const payload = await loadJson(
    `data/intercommunalites/by-epci/${sirenEpci}.json`,
  );
  const entities = payload.communes.map((c) => ({
    svg: c.svg,
    id: String(c.svg.data_fill_id),
    label: c.svg.nom_com,
    data: c.data, // données propres à la commune (pas de l'EPCI)
  }));
  const result = {
    entities,
    bbox: payload.bbox,
    years: payload.years || ANNEES_COMMUNES_FALLBACK,
    epciName: payload.nom_epci,
  };
  state.epcisBySirenCache.set(sirenEpci, result);
  return result;
}

// ----------------------------------------------------------------------------
// Rendu de la carte
// ----------------------------------------------------------------------------

/** Dessine les 35 000 paths du layer décoratif en **une seule passe** :
 *  tous les <path> sont construits dans un documentFragment détaché (aucune
 *  rastérisation tant qu'il n'est pas attaché au DOM), puis insérés en un
 *  unique appendChild → un seul repaint GPU. C'est nettement plus rapide que
 *  l'ancien rendu progressif par `requestAnimationFrame`, qui re-rastérisait
 *  le calque entier à chaque frame (~18 repaints d'un calque grandissant). */
function renderDecorativeLayer() {
  const g = $("#map__decorative");
  while (g.firstChild) g.removeChild(g.firstChild);
  state.decorativePathById = new Map();

  if (!state.decorativeEntities) return;

  // Lazy-loading des valeurs de l'indicateur courant : si pas encore
  // chargées, on dessine d'abord les paths en gris puis on applique les
  // couleurs dès que le fetch arrive. Pas de blocage du rendu.
  const indKey = state.currentIndicator?.key;
  if (indKey && _needsDecorativeValuesLoad(indKey)) {
    ensureDecorativeIndicatorLoaded(indKey).then(() => {
      // Vérifie que l'indicateur courant n'a pas changé entre-temps
      if (state.currentIndicator?.key === indKey) {
        applyDecorativeColors();
      }
    });
  }

  // Pré-calcul des quantiles selon le mode d'échelle choisi par l'utilisateur
  // (global : toutes années ; yearly : année courante uniquement).
  const ind = state.currentIndicator;
  const decoYears = state.decorativeYears || ANNEES_COMMUNES_FALLBACK;
  let yearIdx = decoYears.indexOf(state.currentYear);
  if (yearIdx < 0) yearIdx = decoYears.length - 1;

  // Calcul des seuils selon le mode (communes overview ou intercommunalites overview).
  const isEpciOverview =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "overview" &&
    state.epciBySiren;
  let breaks;
  let epciYearIdx = yearIdx;
  if (isEpciOverview) {
    const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
    epciYearIdx = epciYears.indexOf(state.currentYear);
    if (epciYearIdx < 0) epciYearIdx = epciYears.length - 1;
    const epciValues = collectScaleValues(
      state.epciEntities, ind.key, epciYearIdx,
    );
    breaks = computeQuantileBreaks(epciValues, PALETTE.length);
  } else {
    const scaleValues = collectScaleValues(
      state.decorativeEntities, ind.key, yearIdx,
    );
    breaks = computeQuantileBreaks(scaleValues, PALETTE.length);
  }

  const ns = "http://www.w3.org/2000/svg";
  const entities = state.decorativeEntities;

  // sharedMode pour la coloration initiale (cohérence avec applyDecorativeColors).
  const { sharedMode } = isEpciOverview
    ? getMgpEptSharedStatus(ind?.key, epciYearIdx)
    : { sharedMode: false };

  // Construction de tous les paths dans un fragment détaché : aucune
  // rastérisation tant qu'il n'est pas attaché au DOM. Un unique appendChild
  // final → un seul repaint GPU pour les 35 000 communes.
  const fragment = document.createDocumentFragment();
  for (let i = 0; i < entities.length; i++) {
    const ent = entities[i];
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ent.svg.d);
    path.setAttribute("class", "map__region");
    // En mode intercommunalites overview, on attribue aussi un
    // `data-siren-epci` pour que le clic puisse identifier l'EPCI parent.
    if (isEpciOverview && ent.sirenEpci) {
      path.setAttribute("data-siren-epci", ent.sirenEpci);
    }
    // Coloration directe (évite un second passage). Fallback EPT pour
    // les communes MGP (cf. applyDecorativeColors pour le détail).
    let v;
    if (isEpciOverview) {
      if (sharedMode && ent.sirenEpci === MGP_SIREN) {
        if (ent.sirenEpt) {
          const ept = state.epciBySiren.get(ent.sirenEpt);
          v = ept ? getValueForYear(ept.data, ind.key, epciYearIdx) : null;
        } else {
          v = null;
        }
      } else {
        const epci = ent.sirenEpci ? state.epciBySiren.get(ent.sirenEpci) : null;
        v = epci ? getValueForYear(epci.data, ind.key, epciYearIdx) : null;
        if (v == null && ent.sirenEpt) {
          const ept = state.epciBySiren.get(ent.sirenEpt);
          if (ept) v = getValueForYear(ept.data, ind.key, epciYearIdx);
        }
      }
    } else {
      v = getValueForYear(ent.data, ind.key, yearIdx);
    }
    const col = colorForValue(ind, v, breaks);
    path.style.fill = col || "var(--no-data)";
    state.decorativePathById.set(ent.id, path);
    fragment.appendChild(path);
  }
  g.appendChild(fragment);

  // L'overlay MGP (bandes diagonales Paris + petite couronne) n'est dessiné
  // qu'en mode intercommunalités overview. Auparavant, c'était l'appel final à
  // applyDecorativeColors() qui s'en chargeait — mais il re-coloriait pour rien
  // les 35 000 paths déjà colorisés dans la boucle ci-dessus. On appelle donc
  // directement renderMgpOverlay() (les couleurs sont déjà posées).
  if (isEpciOverview) renderMgpOverlay();
}

function clearDecorativeLayer() {
  const g = $("#map__decorative");
  while (g.firstChild) g.removeChild(g.firstChild);
  state.decorativePathById = new Map();
  // Reset display:none laissé par un drilldown précédent. Sans ça, un
  // re-rendu après un changement de niveau (depuis un drilldown) crée
  // les paths dans un container caché → carte vide visuellement.
  g.style.display = "";
  // L'overlay MGP référence des paths du décoratif — on le vide aussi
  // pour éviter d'afficher des bandes orphelines après un switch de niveau.
  const ov = $("#map__mgp_overlay");
  if (ov) while (ov.firstChild) ov.removeChild(ov.firstChild);
  const ovBorders = $("#map__mgp_overlay_borders");
  if (ovBorders) while (ovBorders.firstChild) ovBorders.removeChild(ovBorders.firstChild);
}

/** S'assure que le calque décoratif est rendu en mode communes overview.
 *  Charge les données en arrière-plan si nécessaire. Idempotent : peut
 *  être appelé à chaque entrée en mode overview.
 *
 *  Avant que les données arrivent : la carte montre les contours
 *  départementaux, l'utilisateur peut déjà cliquer pour drill-down.
 *  Quand les données sont prêtes : rendu progressif (par chunks via
 *  requestAnimationFrame) et mise à jour de la légende. */
function ensureDecorativeRendered() {
  // Le décoratif est utilisé en mode :
  //   - communes overview (chaque commune coloriée par SA valeur)
  //   - intercommunalites overview (chaque commune coloriée par la valeur
  //     de SON EPCI parent — cf. applyDecorativeColors / renderDecorativeLayer)
  //   - syndicats (chaque commune coloriée par valeurs agrégées des syndicats
  //     exerçant la compétence sélectionnée)
  const inOverview =
    (state.currentLevel === "communes" && state.communesMode === "overview") ||
    (state.currentLevel === "intercommunalites" &&
      state.intercommunalitesMode === "overview") ||
    state.currentLevel === "syndicats";
  if (!inOverview) return;

  // En mode intercommunalites overview, on a besoin du meta (pour les
  // siren_epci par commune) en plus du décoratif. On les charge en parallèle.
  const needsMeta = state.currentLevel === "intercommunalites";
  const promises = [loadDecorativeCommunes()];
  if (needsMeta) promises.push(loadCommunesMeta());

  Promise.all(promises)
    .then(() => {
      // Re-vérifier l'état au moment où la promesse résout : l'utilisateur
      // a peut-être quitté le mode overview pendant le chargement.
      const stillInOverview =
        (state.currentLevel === "communes" &&
          state.communesMode === "overview") ||
        (state.currentLevel === "intercommunalites" &&
          state.intercommunalitesMode === "overview") ||
        state.currentLevel === "syndicats";
      if (!stillInOverview) return;
      // S'assurer que les entités décoratives sont bien hydratées avec leur
      // siren_epci (idempotent si déjà fait).
      hydrateDecorativeWithMeta();
      // Synchroniser le slider avec les années réellement chargées (peut
      // différer de la valeur fallback utilisée avant le chargement).
      if (state.decorativeYears) {
        setYears(state.decorativeYears);
        syncYearSlider();
      }
      renderDecorativeLayer();
      renderLegend();
    })
    .catch((err) => {
      console.error("Erreur de chargement du calque décoratif :", err);
    });
}

function setDecorativeVisible(visible) {
  $("#map__decorative").style.display = visible ? "" : "none";
}

/** Calcule si l'indicateur courant est « partagé » MGP + EPT pour l'année :
 *  la MGP publie une valeur ET au moins un des 11 EPT en publie une aussi.
 *  Quand c'est le cas, le rendu affiche des bandes diagonales (EPT en fond,
 *  MGP en overlay) sur les ~130 communes Paris+PC.
 *
 *  Hors sharedMode : comportement classique avec fallback `MGP → EPT` sur
 *  les communes Paris+PC selon ce que publie OFGL.
 *
 *  Retourne { sharedMode, vMgp } pour éviter une double lecture côté
 *  appelant. */
function getMgpEptSharedStatus(indKey, yearIdx) {
  if (!state.epciBySiren) return { sharedMode: false, vMgp: null };
  const mgp = state.epciBySiren.get(MGP_SIREN);
  const vMgp = mgp ? getValueForYear(mgp.data, indKey, yearIdx) : null;
  if (vMgp == null) return { sharedMode: false, vMgp: null };
  for (const ent of state.epciBySiren.values()) {
    if (ent.data?.categ === "EPT") {
      const v = getValueForYear(ent.data, indKey, yearIdx);
      if (v != null) return { sharedMode: true, vMgp };
    }
  }
  return { sharedMode: false, vMgp };
}

/** Dessine ou réinitialise le calque overlay MGP (bandes diagonales).
 *
 *  - En intercommunalités overview : duplique les ~130 paths Paris+PC du
 *    calque décoratif, colorés selon la valeur MGP.
 *  - En drill-down région IDF : duplique les paths Paris+PC du calque
 *    interactif (state.currentEntities).
 *  - Dans tous les autres contextes : vide le calque (no-op visuel).
 *
 *  Le `<mask url="#mgp-diag-mask">` appliqué au groupe (cf. index.html)
 *  ne montre qu'une bande sur deux à 45°, ce qui laisse voir la couleur
 *  EPT du calque décoratif en arrière-plan dans les autres bandes.
 *
 *  Un second calque `#map__mgp_overlay_borders` redessine les contours
 *  des mêmes communes par-dessus l'overlay (sans mask), pour que les
 *  bandes diagonales ne « rayent » pas visuellement les frontières
 *  communales et départementales. */
function renderMgpOverlay() {
  const g = $("#map__mgp_overlay");
  const gBorders = $("#map__mgp_overlay_borders");
  if (!g) return;
  while (g.firstChild) g.removeChild(g.firstChild);
  if (gBorders) while (gBorders.firstChild) gBorders.removeChild(gBorders.firstChild);

  if (!state.epciBySiren) return;

  const isEpciOverview =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "overview";
  const isDrillDownIdf =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "drilldown" &&
    state.drillDownRegCode === "11";
  if (!isEpciOverview && !isDrillDownIdf) return;

  const ind = state.currentIndicator;
  if (!ind) return;

  const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
  let yearIdx = epciYears.indexOf(state.currentYear);
  if (yearIdx < 0) yearIdx = epciYears.length - 1;

  const { sharedMode, vMgp } = getMgpEptSharedStatus(ind.key, yearIdx);
  if (!sharedMode) return;

  // Réutilise les bornes déjà calculées par la coloration principale
  // (applyDecorativeColors en overview, applyColors en drill-down) pour
  // que la couleur MGP soit sur la même échelle que la couleur EPT.
  let breaks;
  if (isEpciOverview) {
    const scaleValues = collectScaleValues(state.epciEntities, ind.key, yearIdx);
    breaks = computeQuantileBreaks(scaleValues, PALETTE.length);
  } else {
    breaks = state.currentBreaks || [];
  }
  const clsMgp = classify(vMgp, breaks);
  const mgpFill = clsMgp < 0 ? "var(--no-data)" : PALETTE[clsMgp];

  // Largeur de stroke pour les contours restaurés : on s'aligne sur le
  // contour des paths sous-jacents.
  //   - overview EPCI : décoratif (35k communes) à stroke-width 0.05
  //   - drill-down IDF : paths interactifs map__regions à stroke-width 0.15
  const borderStrokeWidth = isEpciOverview ? 0.05 : 0.15;

  const ns = "http://www.w3.org/2000/svg";
  const fillFragment = document.createDocumentFragment();
  const borderFragment = document.createDocumentFragment();
  const source = isEpciOverview
    ? state.decorativeEntities
    : state.currentEntities;
  if (!source) return;

  for (const ent of source) {
    if (ent.sirenEpci !== MGP_SIREN) continue;
    const d = ent.svg?.d;
    if (!d) continue;
    // Calque 1 : remplissage MGP, masqué pour donner les bandes diagonales.
    const fillPath = document.createElementNS(ns, "path");
    fillPath.setAttribute("d", d);
    fillPath.setAttribute("fill", mgpFill);
    fillFragment.appendChild(fillPath);
    // Calque 2 : contour blanc reproduisant la frontière communale, dessiné
    // par-dessus l'overlay pour que les bandes ne dépassent pas visuellement.
    if (gBorders) {
      const borderPath = document.createElementNS(ns, "path");
      borderPath.setAttribute("d", d);
      borderPath.setAttribute("stroke", "white");
      borderPath.setAttribute("stroke-width", String(borderStrokeWidth));
      borderPath.setAttribute("stroke-linejoin", "round");
      borderFragment.appendChild(borderPath);
    }
  }
  g.appendChild(fillFragment);
  if (gBorders) gBorders.appendChild(borderFragment);
}

/** Colorise les 35 000 paths décoratifs selon l'indicateur et l'année
 *  courants. Les seuils respectent le mode d'échelle choisi (global =
 *  comparable entre années ; yearly = contraste max au sein de l'année). */
function applyDecorativeColors() {
  if (!state.decorativeEntities) return;
  const ind = state.currentIndicator;

  // Lazy-loading : si les valeurs ne sont pas chargées pour cet indicateur,
  // on les fetch d'abord puis on rappelle applyDecorativeColors. En mode
  // overview EPCI, on utilise les valeurs EPCI déjà chargées en mémoire,
  // donc pas besoin du lazy load communes.
  const isEpciOverview =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "overview";
  if (!isEpciOverview && ind && _needsDecorativeValuesLoad(ind.key)) {
    ensureDecorativeIndicatorLoaded(ind.key).then(() => {
      if (state.currentIndicator?.key === ind.key) {
        applyDecorativeColors();
      }
    });
    return; // on attendra le re-call
  }

  if (isEpciOverview) {
    // Mode intercommunalités overview : on colore chaque commune par la
    // valeur de SON EPCI parent (lookup via ent.sirenEpci enrichi depuis
    // meta-communes-2024.json). Les seuils sont calculés sur les ~1335 EPCIs
    // (pas les 35k communes) → pas de risque RangeError ici.
    if (!state.epciBySiren) return;
    // Plage d'années EPCI ≠ plage communes : 2017-2024 vs 2017-2024 actuellement
    // (donc identique en pratique, mais on reste défensif).
    const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
    let yearIdx = epciYears.indexOf(state.currentYear);
    if (yearIdx < 0) yearIdx = epciYears.length - 1;

    const scaleValues = collectScaleValues(state.epciEntities, ind.key, yearIdx);
    const breaks = computeQuantileBreaks(scaleValues, PALETTE.length);

    // sharedMode : MGP + au moins 1 EPT publient cet indicateur. On force
    // alors le fond des communes Paris+PC à la couleur EPT (et non MGP via
    // fallback), pour que la couche overlay MGP en bandes diagonales soit
    // lisible par contraste. Paris (sans EPT) reste gris en fond, avec
    // MGP en overlay au-dessus.
    const { sharedMode } = getMgpEptSharedStatus(ind.key, yearIdx);

    for (const ent of state.decorativeEntities) {
      const path = state.decorativePathById.get(ent.id);
      if (!path) continue;
      let v;
      if (sharedMode && ent.sirenEpci === MGP_SIREN) {
        if (ent.sirenEpt) {
          const ept = state.epciBySiren.get(ent.sirenEpt);
          v = ept ? getValueForYear(ept.data, ind.key, yearIdx) : null;
        } else {
          v = null;
        }
      } else {
        const epci = ent.sirenEpci ? state.epciBySiren.get(ent.sirenEpci) : null;
        v = epci ? getValueForYear(epci.data, ind.key, yearIdx) : null;
        // Fallback : si l'EPCI principal n'a pas la valeur (typiquement le
        // cas FPIC pour les communes de la MGP), on essaie l'EPT auquel la
        // commune est aussi rattachée. C'est une SUPERPOSITION sans synthèse :
        // chaque valeur lue vient directement d'OFGL au niveau publié.
        if (v == null && ent.sirenEpt) {
          const ept = state.epciBySiren.get(ent.sirenEpt);
          if (ept) v = getValueForYear(ept.data, ind.key, yearIdx);
        }
      }
      const col = colorForValue(ind, v, breaks);
      path.style.fill = col || "var(--no-data)";
    }
    renderMgpOverlay();
    return;
  }

  // Mode communes overview (par défaut) : on colore chaque commune par SA
  // propre valeur.
  const decoYears = state.decorativeYears || ANNEES_COMMUNES_FALLBACK;
  let yearIdx = decoYears.indexOf(state.currentYear);
  if (yearIdx < 0) yearIdx = decoYears.length - 1;

  const scaleValues = collectScaleValues(state.decorativeEntities, ind.key, yearIdx);
  const breaks = computeQuantileBreaks(scaleValues, PALETTE.length);

  for (const ent of state.decorativeEntities) {
    const path = state.decorativePathById.get(ent.id);
    if (!path) continue;
    const v = getValueForYear(ent.data, ind.key, yearIdx);
    const col = colorForValue(ind, v, breaks);
    path.style.fill = col || "var(--no-data)";
  }
}

function renderMap(opts = {}) {
  const { viewBox = FRANCE_VIEWBOX } = opts;

  const g = $("#map__regions");
  while (g.firstChild) g.removeChild(g.firstChild);

  const mapEl = $("#map");
  mapEl.classList.toggle("map--regions", state.currentLevel === "regions");
  mapEl.classList.toggle("map--departements", state.currentLevel === "departements");
  mapEl.classList.toggle(
    "map--communes",
    state.currentLevel === "communes" && state.communesMode === "drilldown",
  );
  mapEl.classList.toggle(
    "map--communes-overview",
    state.currentLevel === "communes" && state.communesMode === "overview",
  );
  // Niveau Syndicats : réutilise les mêmes styles que communes overview
  // (décoratif coloré en arrière-plan, ~100 départements transparents
  // au-dessus pour la navigation).
  mapEl.classList.toggle(
    "map--syndicats-overview",
    state.currentLevel === "syndicats" &&
      state.syndicatsMode === "overview",
  );
  // Niveau syndicats drilldown : on rend des communes individuelles (du
  // dpt cliqué) coloriées par leur valeur syndicat. La classe `map--syndicats`
  // est ajoutée pour partager le styling fin-trait des classes équivalentes
  // `.map--communes` / `.map--intercommunalites` (cf. CSS : règles
  // multi-sélecteurs sur stroke-width 0.15).
  mapEl.classList.toggle(
    "map--syndicats",
    state.currentLevel === "syndicats" &&
      state.syndicatsMode === "drilldown",
  );
  // Niveau intercommunalités :
  //   - overview : pas de polygones interactifs dans #map__regions, le clic
  //     se fait directement sur le décoratif (CSS `map--intercommunalites-overview`).
  //   - drilldown : on charge les communes membres d'un EPCI dans #map__regions,
  //     même style que les communes drilldown (cf. CSS).
  mapEl.classList.toggle(
    "map--intercommunalites",
    state.currentLevel === "intercommunalites" &&
      state.intercommunalitesMode === "drilldown",
  );
  mapEl.classList.toggle(
    "map--intercommunalites-overview",
    state.currentLevel === "intercommunalites" &&
      state.intercommunalitesMode === "overview",
  );
  mapEl.setAttribute("viewBox", viewBox);

  state.pathById = new Map();
  _selectedPath = null;
  _selectedPathsMulti = [];
  _selectionHalos = [];

  const ns = "http://www.w3.org/2000/svg";
  // En mode intercommunalites overview, `state.currentEntities` = EPCIs (sans
  // SVG). On rend à la place les ~18 contours de régions (calque interactif
  // pour le drill-down par région). `state.epciOverviewMapEntities` est
  // chargé par switchLevel.
  const isEpciOverview =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "overview";
  const entities = isEpciOverview && state.epciOverviewMapEntities
    ? state.epciOverviewMapEntities
    : state.currentEntities;
  const isLargeLevel = entities.length > 1000;

  const fragment = document.createDocumentFragment();
  for (const ent of entities) {
    // Sécurité : si une entité n'a pas de SVG (cas EPCI sans calque dédié
    // si l'override ci-dessus n'a pas eu lieu), on saute.
    if (!ent.svg || !ent.svg.d) continue;
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ent.svg.d);
    path.setAttribute("class", "map__region");
    // En mode intercommunalites overview, le data-id est le code région
    // INSEE (pour que le clic puisse appeler enterDrillDownEpciByRegion).
    if (isEpciOverview && ent.data?.reg_code) {
      path.setAttribute("data-id", ent.data.reg_code);
    } else {
      path.setAttribute("data-id", ent.id);
    }

    if (!isLargeLevel) {
      path.setAttribute("tabindex", "0");
      path.setAttribute("role", "button");
      // Nom accessible explicite (en plus du <title> natif SVG, qui sert de
      // tooltip au survol) : garantit que le bouton-région est annoncé par
      // tous les lecteurs d'écran et satisfait l'audit « button-name » une fois
      // les régions exposées (cf. role="group" sur #map, index.html).
      path.setAttribute("aria-label", ent.label);
      const titleEl = document.createElementNS(ns, "title");
      titleEl.textContent = ent.label;
      path.appendChild(titleEl);
    }

    state.pathById.set(
      isEpciOverview && ent.data?.reg_code ? ent.data.reg_code : ent.id,
      path,
    );
    fragment.appendChild(path);
  }
  g.appendChild(fragment);

  applyColors();
}

function applyColors() {
  const ind = state.currentIndicator;
  const entities = state.currentEntities;
  const isOverviewCommunes =
    state.currentLevel === "communes" && state.communesMode === "overview";
  const isOverviewEpci =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "overview";
  const isOverviewSyndicats =
    state.currentLevel === "syndicats" &&
    state.syndicatsMode === "overview";
  const isDrilldownEpciRegion =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "drilldown";

  if (isDrilldownEpciRegion) {
    // En drill-down région : currentEntities = communes (avec svg), mais la
    // VALEUR utilisée pour la coloration est celle de l'EPCI parent.
    // Seuils calculés sur les ~50-200 EPCIs présents dans la région (et non
    // sur les centaines/milliers de communes membres).
    if (!state.epciBySiren || !state.currentRegionEpciSirens) return;
    const regionEpcis = [];
    for (const s of state.currentRegionEpciSirens) {
      const ent = state.epciBySiren.get(s);
      if (ent) regionEpcis.push(ent);
    }
    const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
    let yearIdx = epciYears.indexOf(state.currentYear);
    if (yearIdx < 0) yearIdx = epciYears.length - 1;
    const scaleValues = collectScaleValues(regionEpcis, ind.key, yearIdx);
    state.currentBreaks = isCategoricalIndicator(ind)
      ? []
      : computeQuantileBreaks(scaleValues, PALETTE.length);

    // sharedMode : sur les indicateurs publiés à la fois par MGP et EPT, on
    // force le fond des communes Paris+PC à la couleur EPT pour que la
    // couche overlay MGP (bandes diagonales) soit visible par contraste.
    const { sharedMode } = getMgpEptSharedStatus(ind.key, yearIdx);

    for (const ent of entities) {
      const path = state.pathById.get(ent.id);
      if (!path) continue;
      const epci = ent.sirenEpci ? state.epciBySiren.get(ent.sirenEpci) : null;
      let v;
      let displayEpci = epci;
      if (sharedMode && ent.sirenEpci === MGP_SIREN) {
        if (ent.sirenEpt) {
          const ept = state.epciBySiren.get(ent.sirenEpt);
          v = ept ? getValueForYear(ept.data, ind.key, yearIdx) : null;
          if (ept) displayEpci = ept;
        } else {
          v = null;
        }
      } else {
        v = epci ? getValueForYear(epci.data, ind.key, yearIdx) : null;
        // Fallback EPT pour les communes MGP en drill-down région
        if (v == null && ent.sirenEpt) {
          const ept = state.epciBySiren.get(ent.sirenEpt);
          if (ept) {
            v = getValueForYear(ept.data, ind.key, yearIdx);
            displayEpci = ept;
          }
        }
      }
      const col = colorForValue(ind, v, state.currentBreaks);
      if (col == null) {
        path.style.fill = "";
        path.classList.add("map__region--no-data");
      } else {
        path.style.fill = col;
        path.classList.remove("map__region--no-data");
      }
      // Le title (tooltip) affiche le nom de l'EPCI + valeur, pas celui
      // de la commune (qui est juste l'outil cartographique).
      const title = path.querySelector("title");
      if (title && displayEpci) {
        // precise=true pour le tooltip : valeur exacte (pas K€/M€/Md€).
        const accLabel =
          v == null
            ? `${displayEpci.label} : donnée non disponible`
            : `${displayEpci.label} : ${formatIndicatorValue(ind, v)}`;
        title.textContent = accLabel;
        // Nom accessible = même libellé que le tooltip (EPCI + valeur courante),
        // cohérent avec le clic (qui sélectionne l'EPCI) et avec le panneau.
        path.setAttribute("aria-label", accLabel);
      }
    }
    renderMgpOverlay();
    return;
  }

  if (isOverviewCommunes || isOverviewEpci || isOverviewSyndicats) {
    // Mode overview : le calque interactif #map__regions est laissé vide
    // (communes/syndicats overview : ~100 départements transparents
    // cliquables ; intercommunalites overview : aucun polygone — la
    // coloration ET le clic sont délégués au calque décoratif des 35 000
    // communes). On colore quand même les éventuels paths déjà présents
    // en transparent.
    state.currentBreaks = [];
    for (const ent of entities) {
      const path = state.pathById.get(ent.id);
      if (!path) continue;
      path.style.fill = "";
      path.classList.add("map__region--no-data");
    }
    // Recoloriser le décoratif (au cas où l'indicateur a changé)
    applyDecorativeColors();
    return;
  }

  // Seuils selon le mode d'échelle choisi par l'utilisateur.
  const scaleValues = collectScaleValues(entities, ind.key, state.currentYearIdx);
  state.currentBreaks = isCategoricalIndicator(ind)
    ? []
    : computeQuantileBreaks(scaleValues, PALETTE.length);

  const isLargeLevel = entities.length > 1000;
  for (const ent of entities) {
    const path = state.pathById.get(ent.id);
    if (!path) continue;
    const v = getValueForYear(ent.data, ind.key);
    const col = colorForValue(ind, v, state.currentBreaks);
    if (col == null) {
      path.style.fill = "";
      if (!path.classList.contains("map__region--no-data")) {
        path.classList.add("map__region--no-data");
      }
    } else {
      path.style.fill = col;
      if (path.classList.contains("map__region--no-data")) {
        path.classList.remove("map__region--no-data");
      }
    }

    if (!isLargeLevel) {
      // precise=true pour le tooltip : valeur exacte.
      const accLabel =
        v == null
          ? `${ent.label} : donnée non disponible`
          : `${ent.label} : ${formatIndicatorValue(ind, v)}`;
      const title = path.querySelector("title");
      if (title) title.textContent = accLabel;
      // Nom accessible synchronisé avec le tooltip (nom + valeur courante) : le
      // lecteur d'écran annonce la même information que l'infobulle au survol,
      // et le nom se met à jour à chaque changement d'indicateur ou d'année.
      path.setAttribute("aria-label", accLabel);
    }
  }

  if (state.selectedId) {
    const sel = state.pathById.get(state.selectedId);
    if (sel) sel.classList.add("map__region--selected");
  }

  // S'assure que l'overlay MGP est vidé hors des contextes intercommunalités
  // applicables (régions, départements, communes, syndicats…). La fonction
  // vide systématiquement avant de re-render, c'est un no-op si déjà vide.
  renderMgpOverlay();
}

// ----------------------------------------------------------------------------
// Légende
// ----------------------------------------------------------------------------

function renderLegend() {
  const ind = state.currentIndicator;
  const isOverviewCommunes =
    state.currentLevel === "communes" && state.communesMode === "overview";
  const isOverviewEpci =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "overview";
  const isOverviewSyndicats =
    state.currentLevel === "syndicats" &&
    state.syndicatsMode === "overview";
  const isOverview = isOverviewCommunes || isOverviewEpci || isOverviewSyndicats;
  const el = $("#legend");

  // Cas transitoire : on est en mode overview mais les données nécessaires
  // ne sont pas encore là. On affiche un message d'attente — la légende sera
  // mise à jour quand prêt.
  if (isOverviewCommunes && !state.decorativeEntities) {
    el.innerHTML = `
      <div class="legend__title">${ind.label}</div>
      <p class="legend__hint">Chargement des couleurs pour les ~35 000 communes…</p>
      <p class="legend__hint">En attendant, vous pouvez cliquer sur un département pour explorer ses communes.</p>
    `;
    return;
  }
  if (isOverviewEpci && (!state.decorativeEntities || !state.epciEntities)) {
    el.innerHTML = `
      <div class="legend__title">${ind.label}</div>
      <p class="legend__hint">Chargement des couleurs pour les ~1 300 intercommunalités…</p>
    `;
    return;
  }
  if (isOverviewSyndicats) {
    // Pour les syndicats, les valeurs sont lazy-loadées par indicateur.
    // Tant qu'elles ne sont pas dans le décoratif, on affiche un message.
    const noEnts = !state.decorativeEntities;
    const noValues = ind && _needsDecorativeValuesLoad(ind.key);
    if (noEnts || noValues) {
      el.innerHTML = `
        <div class="legend__title">${ind.label}</div>
        <p class="legend__hint">Chargement des valeurs du syndicat sélectionné…</p>
        <p class="legend__hint">Les communes membres seront coloriées dès que les données seront disponibles.</p>
      `;
      return;
    }
  }

  const cells = PALETTE
    .map((c) => `<span class="legend__bar-cell" style="background:${c}"></span>`)
    .join("");

  // Détermine la source des valeurs pour le calcul des bornes :
  //   - intercommunalites overview     : les ~1335 EPCIs (chaque commune
  //     décorative est coloriée selon la valeur de son EPCI parent)
  //   - intercommunalites drilldown    : les EPCIs présents dans la région
  //     (chaque polygone commune est coloriée selon la valeur de son EPCI)
  //   - communes overview              : les ~35 000 communes décoratives
  //   - autres modes                   : les entités du niveau courant
  const isDrilldownEpciRegion =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "drilldown";
  let sourceEntities, scaleYearIdx;
  if (isOverviewEpci) {
    sourceEntities = state.epciEntities;
    const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
    scaleYearIdx = epciYears.indexOf(state.currentYear);
    if (scaleYearIdx < 0) scaleYearIdx = epciYears.length - 1;
  } else if (isDrilldownEpciRegion && state.currentRegionEpciSirens && state.epciBySiren) {
    sourceEntities = [];
    for (const s of state.currentRegionEpciSirens) {
      const ent = state.epciBySiren.get(s);
      if (ent) sourceEntities.push(ent);
    }
    const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
    scaleYearIdx = epciYears.indexOf(state.currentYear);
    if (scaleYearIdx < 0) scaleYearIdx = epciYears.length - 1;
  } else if (isOverviewCommunes || isOverviewSyndicats) {
    sourceEntities = state.decorativeEntities;
    const decoYears = state.decorativeYears || ANNEES_COMMUNES_FALLBACK;
    scaleYearIdx = decoYears.indexOf(state.currentYear);
    if (scaleYearIdx < 0) scaleYearIdx = decoYears.length - 1;
  } else {
    sourceEntities = state.currentEntities;
    scaleYearIdx = state.currentYearIdx;
  }

  // Indicateur CATÉGORIEL : légende DISCRÈTE (une pastille par catégorie +
  // effectif sur l'année courante), pas de barre de dégradé numérique.
  if (isCategoricalIndicator(ind)) {
    const counts = new Map();
    let nNoData = 0;
    for (const e of sourceEntities) {
      const code = getValueForYear(e.data, ind.key, scaleYearIdx);
      if (code == null) { nNoData++; continue; }
      counts.set(String(code), (counts.get(String(code)) || 0) + 1);
    }
    // Aucune entité coloriée pour l'année affichée (ex. critères 2020-2023
    // visualisés en 2024) : plutôt qu'une carte grise muette, on indique la
    // couverture et on invite à déplacer le curseur d'année.
    if (counts.size === 0) {
      const years = state.years || [];
      const covered = [];
      for (const e of sourceEntities) {
        const serie = e?.data?.values?.[ind.key];
        if (!serie) continue;
        for (let i = 0; i < serie.length; i++) {
          if (serie[i] != null && years[i] != null && !covered.includes(years[i])) {
            covered.push(years[i]);
          }
        }
      }
      covered.sort((a, b) => a - b);
      const range = covered.length
        ? (covered.length > 1 ? `${covered[0]}–${covered[covered.length - 1]}` : `${covered[0]}`)
        : "—";
      el.innerHTML = `
        <div class="legend__title">${escapeHtml(ind.label)}</div>
        <p class="legend__hint">Aucune donnée pour <strong>${state.currentYear}</strong>. Couverture : <strong>${range}</strong>. Déplacez le curseur d'année pour colorier la carte.</p>
      `;
      return;
    }
    const cmap = categoryColorMap(ind);
    const rows = ind.categories
      .filter((c) => counts.has(String(c.code)))
      .map((c) => {
        const n = counts.get(String(c.code));
        return `<li class="legend__cat">
          <span class="legend__cat-swatch" style="background:${cmap.get(String(c.code))}"></span>
          <span class="legend__cat-label">${escapeHtml(c.label)}</span>
          <span class="legend__cat-count">${n.toLocaleString("fr-FR")}</span>
        </li>`;
      })
      .join("");
    const noDataRow = nNoData > 0
      ? `<li class="legend__cat legend__cat--nodata">
           <span class="legend__cat-swatch legend__cat-swatch--nodata"></span>
           <span class="legend__cat-label">Donnée non disponible</span>
           <span class="legend__cat-count">${nNoData.toLocaleString("fr-FR")}</span>
         </li>`
      : "";
    el.innerHTML = `
      <div class="legend__title">${escapeHtml(ind.label)}</div>
      <ul class="legend__categories">${rows}${noDataRow}</ul>
      <p class="legend__scale-info">Répartition au ${state.currentYear} · ${sourceEntities.length.toLocaleString("fr-FR")} entités. Coloration discrète par catégorie (verbatim OFGL, hors comptes).</p>
    `;
    return;
  }

  const scaleValues = collectScaleValues(sourceEntities, ind.key, scaleYearIdx);
  // arrayMinMax au lieu de Math.min(...) / Math.max(...) : en communes
  // overview, scaleValues peut contenir ~280 000 valeurs (35k × 8 ans),
  // ce qui dépasse la limite d'arguments du spread (≈65k → RangeError).
  const [min, max] = arrayMinMax(scaleValues);

  // Calcul des bornes pour l'ANNÉE COURANTE uniquement, indépendamment du
  // scaleMode. Sert à clarifier la légende quand l'échelle est globale :
  // les chiffres affichés sur la barre (min/max sur 2012-2024) ne
  // correspondent pas aux extrêmes du classement (qui ne porte que sur
  // l'année courante). On les montre côte à côte pour lever l'ambiguïté.
  const yearlyValues = [];
  for (const e of sourceEntities) {
    const v = getValueForYear(e.data, ind.key, scaleYearIdx);
    if (v != null && !Number.isNaN(v)) yearlyValues.push(v);
  }
  const [yearMin, yearMax] = arrayMinMax(yearlyValues);

  // Légende explicative selon le mode d'échelle
  const yearsRange =
    state.years && state.years.length > 1
      ? `${state.years[0]}–${state.years[state.years.length - 1]}`
      : `${state.currentYear}`;
  const scaleInfo =
    state.scaleMode === "global"
      ? `Bornes ci-dessus : min/max sur ${yearsRange} (échelle constante, couleurs comparables d'une année à l'autre).`
      : `Bornes ci-dessus : min/max sur ${state.currentYear} uniquement (échelle recalculée chaque année).`;

  // Complément ne s'affichant qu'en mode global : on rappelle les bornes de
  // l'année affichée, qui correspondent à celles du classement. En mode
  // yearly, les deux séries sont identiques par définition → on ne montre
  // pas la ligne pour ne pas faire de doublon.
  const yearRangeLine =
    state.scaleMode === "global" && yearMin != null && yearMax != null
      ? `<p class="legend__year-range">
           Année ${state.currentYear} : ${formatValue(yearMin, ind.unit)} – ${formatValue(yearMax, ind.unit)}
           <span class="legend__year-range-hint">(extrêmes du classement)</span>
         </p>`
      : "";

  // Encart MGP/EPT : actif uniquement en intercommunalités (overview ou
  // drill-down IDF) quand l'indicateur courant est publié à la fois par la
  // MGP et par au moins un EPT. On affiche alors un texte sous la légende
  // pour expliquer ce que sont les bandes diagonales visibles sur Paris+PC.
  let mgpEptHint = "";
  const isIdfDrill =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "drilldown" &&
    state.drillDownRegCode === "11";
  if ((isOverviewEpci || isIdfDrill) && ind && state.epciBySiren) {
    const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
    let yi = epciYears.indexOf(state.currentYear);
    if (yi < 0) yi = epciYears.length - 1;
    const { sharedMode } = getMgpEptSharedStatus(ind.key, yi);
    if (sharedMode) {
      mgpEptHint = `
        <p class="legend__mgp-ept-hint">
          <span class="legend__mgp-ept-swatch" aria-hidden="true"></span>
          <span><strong>Bandes diagonales</strong> sur Paris + petite couronne :
          la commune appartient à la <strong>MGP</strong> (compétences métropolitaines)
          ET à un <strong>EPT</strong> (compétences de proximité), tous deux publient
          une valeur sur cet indicateur. Fond = EPT, bandes = MGP. Pas de synthèse,
          lecture verbatim OFGL.</span>
        </p>
      `;
    }
  }

  if (isOverview) {
    el.innerHTML = `
      <div class="legend__title">${ind.label}</div>
      <div class="legend__bar" role="img" aria-label="Échelle de couleurs">${cells}</div>
      <div class="legend__labels">
        <span>${formatValue(min, ind.unit)}</span>
        <span>${formatValue(max, ind.unit)}</span>
      </div>
      <p class="legend__scale-info">${scaleInfo}</p>
      ${yearRangeLine}
      ${mgpEptHint}
      <p class="legend__hint">Cliquez sur un département pour explorer ses communes individuellement.</p>
    `;
    return;
  }

  el.innerHTML = `
    <div class="legend__title">${ind.label}</div>
    <div class="legend__bar" role="img" aria-label="Échelle de couleurs">${cells}</div>
    <div class="legend__labels">
      <span>${formatValue(min, ind.unit)}</span>
      <span>${formatValue(max, ind.unit)}</span>
    </div>
    <p class="legend__scale-info">${scaleInfo}</p>
    ${yearRangeLine}
    ${mgpEptHint}
    <div class="legend__no-data">Donnée non disponible</div>
  `;
}

// ----------------------------------------------------------------------------
// Panneau d'info
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Décomposition « ensemble intercommunal » (ofgl-base-ei). Pour les indicateurs
// du groupe EI (clé préfixée "EI — "), le panneau affiche la ventilation du
// montant consolidé : structure (EPCI) / communes membres / flux neutralisés,
// lazy-chargée depuis data/intercommunalites/ei-details/{siren}.json.
// Mirror du pattern loadSyndicatDetailFile (Map + race-guard par token).
// ----------------------------------------------------------------------------
const EI_INDICATOR_PREFIX = "EI — ";
function isEiIndicatorKey(key) {
  return typeof key === "string" && key.startsWith(EI_INDICATOR_PREFIX);
}

const _eiDetailFiles = new Map();
async function loadEiDetailFile(siren) {
  if (!siren) return null;
  const cached = _eiDetailFiles.get(siren);
  if (cached !== undefined) return cached;
  const promise = (async () => {
    try {
      const payload = await loadJson(
        `data/intercommunalites/ei-details/${siren}.json`,
      );
      _eiDetailFiles.set(siren, payload);
      return payload;
    } catch (err) {
      console.warn(`Échec chargement détail EI ${siren}:`, err.message || err);
      _eiDetailFiles.delete(siren);
      return null;
    }
  })();
  _eiDetailFiles.set(siren, promise);
  return promise;
}

let _eiDecompToken = 0;
/** Remplit le slot #ei-decomp-slot avec la décomposition de l'agrégat courant
 *  pour l'ensemble intercommunal `siren`. Race-safe : un token invalide les
 *  fetchs obsolètes, et on revalide sélection + indicateur avant d'écrire. */
async function fillEiDecomposition(siren, agregat) {
  const myToken = ++_eiDecompToken;
  const payload = await loadEiDetailFile(siren);
  if (myToken !== _eiDecompToken) return; // un rendu plus récent a pris la main
  if (state.selectedId !== siren) return; // la sélection a changé entre-temps
  const ind = state.currentIndicator;
  if (!ind || ind.key !== EI_INDICATOR_PREFIX + agregat) return; // indicateur changé
  const slot = document.getElementById("ei-decomp-slot");
  if (!slot) return;
  const block = payload && payload.agregats ? payload.agregats[agregat] : null;
  const years = (payload && payload.years) || ANNEES_EPCI_FALLBACK;
  let yi = years.indexOf(state.currentYear);
  if (yi < 0) yi = years.length - 1;
  if (!block) {
    slot.innerHTML = "";
    return;
  }
  const gfp = block.gfp ? block.gfp[yi] : null;
  const comm = block.communes ? block.communes[yi] : null;
  const flux = block.flux ? block.flux[yi] : null;
  const tot = block.montant ? block.montant[yi] : null;
  const eurHab = block.eur_hab ? block.eur_hab[yi] : null;
  if (gfp == null && comm == null && tot == null) {
    slot.innerHTML = "";
    return;
  }
  const fmtE = (v) => (v == null ? "—" : formatValue(v, "€"));
  slot.innerHTML = `
    <div class="ei-decomp">
      <div class="ei-decomp__head">Territoire consolidé · décomposition ${state.currentYear}</div>
      <ul class="ei-decomp__rows">
        <li><span class="ei-decomp__k">dont structure (EPCI)</span><span class="ei-decomp__v">${fmtE(gfp)}</span></li>
        <li><span class="ei-decomp__k">dont communes membres</span><span class="ei-decomp__v">${fmtE(comm)}</span></li>
        <li class="ei-decomp__flux"><span class="ei-decomp__k">flux internes neutralisés</span><span class="ei-decomp__v">${flux == null ? "—" : "− " + formatValue(flux, "€")}</span></li>
        <li class="ei-decomp__total"><span class="ei-decomp__k">= ensemble intercommunal</span><span class="ei-decomp__v">${fmtE(tot)}${eurHab == null ? "" : " · " + formatValue(eurHab, "€/hab")}</span></li>
      </ul>
    </div>
  `;
}

function renderPanel() {
  // En drill-down intercommunalités, on classe et affiche des EPCIs
  // (jamais des communes). Le cfg reste « intercommunalites » et
  // currentEntities pour le panel = les EPCIs présents dans la région.
  const isEpciDrill =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "drilldown";
  const cfg = LEVELS[state.currentLevel];
  const el = $("#info-panel");
  const isCommunesOverview =
    state.currentLevel === "communes" && state.communesMode === "overview";
  const isSyndicatsOverview = state.currentLevel === "syndicats";

  if (isCommunesOverview) {
    renderNationalCommunesLeaderboard(el);
    return;
  }

  if (isSyndicatsOverview) {
    renderNationalSyndicatsLeaderboard(el);
    return;
  }

  // Pré-calcul du classement sur l'indicateur courant.
  // - Drill-down EPCI région : on classe les EPCIs présents (lookup dans
  //   state.epciBySiren via state.currentRegionEpciSirens).
  // - Tous les autres cas : on classe les `currentEntities`.
  const ind = state.currentIndicator;
  let rankingEntities = state.currentEntities;
  let rankingYearIdx = state.currentYearIdx;
  if (isEpciDrill && state.currentRegionEpciSirens && state.epciBySiren) {
    rankingEntities = [];
    for (const s of state.currentRegionEpciSirens) {
      const ent = state.epciBySiren.get(s);
      if (ent) rankingEntities.push(ent);
    }
    // Les EPCIs ont leur propre indexation d'années (2017-2024)
    const epciYears = state.epciYears || ANNEES_EPCI_FALLBACK;
    rankingYearIdx = epciYears.indexOf(state.currentYear);
    if (rankingYearIdx < 0) rankingYearIdx = epciYears.length - 1;
  }
  // Pour un indicateur CATÉGORIEL, le « classement » numérique n'a aucun sens :
  // on affiche une RÉPARTITION par catégorie (helper dédié). computeRanking
  // est donc court-circuité (ranked/unranked vides).
  const catMode = isCategoricalIndicator(ind);
  const { ranked, unranked } = catMode
    ? { ranked: [], unranked: [] }
    : computeRanking(rankingEntities, ind.key, rankingYearIdx);
  const leaderboardHTML = (selId) =>
    catMode
      ? renderCategoricalLeaderboardHTML(rankingEntities, selId, ind, rankingYearIdx)
      : renderLeaderboardHTML(ranked, unranked.length, selId, ind);

  const leaderboardLabel = buildLeaderboardLabel();

  // Cas 1 : pas de sélection → leaderboard plein écran (top + bottom)
  if (!state.selectedId) {
    const what = {
      regions: "une région",
      departements: "un département",
      intercommunalites: "un EPCI",
      communes: "une commune",
    }[state.currentLevel];
    // Selon le mode, l'interaction sur la carte change : on adapte le hint.
    let hintInteraction;
    if (
      state.currentLevel === "intercommunalites" &&
      state.intercommunalitesMode === "overview"
    ) {
      hintInteraction =
        "Cliquez sur une région dans la carte pour zoomer sur ses intercommunalités, ou choisissez un EPCI dans la liste ci-dessous.";
    } else if (isEpciDrill) {
      hintInteraction =
        "Cliquez sur une commune dans la carte pour voir les indicateurs de son EPCI parent, ou choisissez un EPCI dans la liste ci-dessous.";
    } else {
      hintInteraction = `Cliquez sur ${what} dans la carte ou la liste ci-dessous pour voir ses indicateurs détaillés.`;
    }
    el.innerHTML = `
      <h2 class="panel__title">Classement — ${escapeHtml(ind.label)}</h2>
      <p class="panel__subtitle">
        ${leaderboardLabel} · ${state.currentYear}${ind.unit ? " · " + escapeHtml(ind.unit) : ""}
      </p>
      <p class="panel__placeholder" style="margin-bottom: 0.6rem;">
        ${hintInteraction}
      </p>
      ${leaderboardHTML(null)}
    `;
    return;
  }

  // En drill-down EPCI région, l'entité « sélectionnée » est un EPCI (siren_epci) :
  // on la cherche dans state.epciBySiren, pas dans currentEntityById qui
  // contient des communes.
  const ent = isEpciDrill && state.epciBySiren
    ? state.epciBySiren.get(state.selectedId)
    : state.currentEntityById.get(state.selectedId);
  if (!ent) {
    el.innerHTML = `<p class="panel__placeholder">Entité non trouvée.</p>`;
    return;
  }

  if (!ent.data) {
    // Entité sans donnée (ex: Mayotte au niveau régions, Corse au niveau
    // départements, etc.) : on affiche le placeholder explicatif + le
    // classement des autres entités pour permettre de naviguer ailleurs.
    el.innerHTML = `
      <h2 class="panel__title">${escapeHtml(ent.label)}</h2>
      <p class="panel__placeholder">${cfg.noDataMessage}</p>
      <details class="panel__leaderboard-section" open>
        <summary class="panel__leaderboard-summary">
          Classement — ${escapeHtml(ind.label)}
          <span class="panel__leaderboard-summary-meta">${leaderboardLabel}</span>
        </summary>
        ${leaderboardHTML(state.selectedId)}
      </details>
    `;
    return;
  }

  // Cas 2 : entité sélectionnée → détails + leaderboard compact en bas.
  const d = ent.data;
  const rankInfo = getEntityRank(ranked, state.selectedId);

  // Enrichissement MGP pour les EPT : sur le territoire MGP, chaque
  // commune Paris/PC appartient simultanément à un EPT (fiscalité,
  // FPIC, compétences de proximité) et à la MGP (compétences
  // métropolitaines). 147 indicateurs sont publiés des deux côtés
  // (recettes/dépenses, dette, fiscalité directe). On les juxtapose
  // dans le panneau pour donner la lecture complète, sans synthèse.
  const isEpt = ent.data?.categ === "EPT";
  const mgpEnt = isEpt && state.epciBySiren
    ? state.epciBySiren.get(MGP_SIREN)
    : null;
  const mgpData = mgpEnt?.data || null;

  // On affiche dans le panel uniquement les indicateurs pertinents pour le
  // niveau courant. En drill-down EPCI région, l'entité sélectionnée est un
  // EPCI (sirenEpci) → on garde les indicateurs EPCI. Dans les autres cas,
  // c'est l'entité du niveau courant (régions, départements, communes).
  const items = getIndicatorsForLevel(state.currentLevel).map((indItem) => {
    const v = getValueForYear(d, indItem.key);
    const vMgp = mgpData ? getValueForYear(mgpData, indItem.key) : null;
    const serie = d.values?.[indItem.key] || [];
    const indItemCat = isCategoricalIndicator(indItem);
    const sparkline = indItemCat
      ? buildCategoricalStrip(indItem, serie, state.currentYearIdx)
      : buildSparkline(serie, state.currentYearIdx);
    const isHighlighted = indItem.key === ind.key;
    const highlightedClass = isHighlighted ? " panel__indicator--highlighted" : "";
    const rankBadge =
      isHighlighted && rankInfo
        ? `<span class="panel__rank-badge" title="Rang sur ${rankInfo.total} entités classées">${rankInfo.rank}<span class="panel__rank-badge-total"> / ${rankInfo.total}</span></span>`
        : "";

    // Construction du bloc de valeur(s). Trois cas quand on est sur un EPT :
    //   a) EPT + MGP ont tous deux une valeur → stack (EPT puis MGP)
    //   b) EPT seul (cas FPIC) → valeur EPT seule, comme avant
    //   c) MGP seule (cas CIF, dotations, potentiel...) → valeur MGP seule,
    //      avec étiquette explicite pour que l'utilisateur sache que ce
    //      n'est PAS la valeur EPT
    // Hors EPT : comportement normal (valeur seule, sans étiquette).
    let valueBlock;
    if (isEpt && v != null && vMgp != null) {
      valueBlock = `
        <span class="panel__indicator-value-stack">
          <span class="panel__indicator-value"><span class="panel__indicator-source">EPT</span>${formatIndicatorValue(indItem, v)}</span>
          <span class="panel__indicator-value panel__indicator-value--mgp"><span class="panel__indicator-source panel__indicator-source--mgp">MGP</span>${formatIndicatorValue(indItem, vMgp)}</span>
        </span>
      `;
    } else if (isEpt && v == null && vMgp != null) {
      valueBlock = `
        <span class="panel__indicator-value panel__indicator-value--mgp"><span class="panel__indicator-source panel__indicator-source--mgp">MGP</span>${formatIndicatorValue(indItem, vMgp)}</span>
      `;
    } else if (indItemCat) {
      const sw = v == null
        ? ""
        : `<span class="panel__cat-dot" style="background:${categoryColor(indItem, v) || "var(--no-data)"}"></span>`;
      valueBlock = `<span class="panel__indicator-value">${sw}${formatIndicatorValue(indItem, v)}</span>`;
    } else {
      valueBlock = `<span class="panel__indicator-value">${formatValue(v, indItem.unit)}</span>`;
    }

    return `
      <li class="panel__indicator${highlightedClass}" data-indicator-key="${escapeHtml(indItem.key)}" tabindex="0" title="Afficher la courbe de cet indicateur">
        <span class="panel__indicator-label">${indItem.label}${rankBadge}</span>
        ${sparkline}
        ${valueBlock}
      </li>
    `;
  }).join("");

  const note = cfg.notes(d);
  // Bloc décomposition EI : uniquement au niveau intercommunalités quand
  // l'indicateur courant appartient au groupe « Ensemble intercommunal ».
  const showEiDecomp =
    state.currentLevel === "intercommunalites" && isEiIndicatorKey(ind.key);

  // Grande courbe d'évolution de l'indicateur COURANT pour l'entité sélectionnée.
  // En drill-down EPCI région, l'entité est un EPCI → axe X sur epciYears
  // (et non state.years qui pointe alors sur les années communes).
  // Catégoriels exclus (ils gardent leur bande dans la liste).
  const panelYears = isEpciDrill ? (state.epciYears || ANNEES_EPCI_FALLBACK) : state.years;
  const panelYearIdx = isEpciDrill ? rankingYearIdx : state.currentYearIdx;
  let chartHTML = "";
  if (!catMode) {
    const primarySerie = d.values?.[ind.key] || [];
    const extraSeries = [];
    let primaryLabel = ent.label;
    // EPT : superposer la série MGP quand l'indicateur est publié des deux
    // côtés (cohérent avec la juxtaposition EPT/MGP de la liste).
    if (isEpt && mgpData) {
      const mgpSerie = mgpData.values?.[ind.key] || [];
      if (mgpSerie.some((v) => v != null && !Number.isNaN(v))) {
        primaryLabel = "EPT";
        extraSeries.push({ serie: mgpSerie, label: "MGP", color: "#6a51a3" });
      }
    }
    chartHTML = buildEvolutionChart(primarySerie, panelYears, ind, {
      currentIdx: panelYearIdx,
      primaryLabel,
      extraSeries,
    });
  }
  el.innerHTML = `
    <h2 class="panel__title">${escapeHtml(ent.label)}</h2>
    <p class="panel__subtitle">${cfg.panelSubtitle(d)}</p>
    <p class="panel__year-info">Données ${state.currentYear} · évolution sur ${state.years.length} ans</p>
    ${chartHTML}
    ${showEiDecomp ? `<div id="ei-decomp-slot" class="ei-decomp-slot"><p class="panel__placeholder" style="margin:0;">Chargement de la décomposition…</p></div>` : ""}
    <ul class="panel__indicators">${items}</ul>
    ${note ? `<p class="panel__note"><strong>Note :</strong> ${note}</p>` : ""}
    <details class="panel__leaderboard-section" open>
      <summary class="panel__leaderboard-summary">
        Classement — ${escapeHtml(ind.label)}
        <span class="panel__leaderboard-summary-meta">${leaderboardLabel}</span>
      </summary>
      ${renderLeaderboardHTML(ranked, unranked.length, state.selectedId, ind)}
    </details>
  `;
  if (showEiDecomp) {
    fillEiDecomposition(state.selectedId, ind.key.slice(EI_INDICATOR_PREFIX.length));
  }
}

/** Affiche le leaderboard NATIONAL des communes (≈35 000 entités) dans le
 *  panneau, en mode communes overview.
 *
 *  Étapes :
 *   1. Charge le décoratif (paths + valeurs) s'il ne l'est pas déjà.
 *   2. Charge en parallèle le meta (noms + dep_code).
 *   3. Quand les deux sont prêts, hydrate les entités décoratives avec les
 *      noms et rend le classement (top 50 + bottom 10 par défaut, ou
 *      tronqué autour d'une éventuelle sélection).
 *
 *  Un token de rendu (`_nationalRenderToken`) protège contre les écritures
 *  obsolètes : si l'utilisateur quitte le mode overview pendant le
 *  chargement, le rendu différé est ignoré. */
let _nationalRenderToken = 0;
function renderNationalCommunesLeaderboard(el) {
  const ind = state.currentIndicator;

  // Affichage immédiat d'un placeholder de chargement (ne pas attendre
  // que la promesse résolve avant de donner un feedback visuel)
  const decoLoading = !state.decorativeEntities;
  const metaLoading = !state.communesMeta;

  if (decoLoading || metaLoading) {
    const what = [];
    if (decoLoading) what.push("les valeurs des 35 000 communes");
    if (metaLoading) what.push("leurs noms");
    el.innerHTML = `
      <h2 class="panel__title">Classement national — ${escapeHtml(ind.label)}</h2>
      <p class="panel__placeholder">
        Chargement de ${what.join(" et de ")}…
      </p>
      <p class="controls__help" style="margin-top:0.5rem;">
        En attendant, vous pouvez cliquer sur un département dans la carte
        pour explorer ses communes individuellement.
      </p>
    `;
  }

  const myToken = ++_nationalRenderToken;

  // Déclenche le chargement des trois ressources nécessaires en parallèle
  // (helpers mémoïsés : si déjà chargés, résout immédiatement).
  //   1. Les contours décoratifs (decorativeEntities)
  //   2. Les noms/INSEE des communes (communesMeta)
  //   3. Les VALEURS de l'indicateur courant (sinon le leaderboard rend
  //      « 0 communes classées » : les data.values des entités ne sont
  //      pas hydratées tant que ensureDecorativeIndicatorLoaded n'a pas
  //      injecté les séries depuis decoratif-values/{slug}.json).
  Promise.all([
    loadDecorativeCommunes(),
    loadCommunesMeta(),
    ind ? ensureDecorativeIndicatorLoaded(ind.key) : Promise.resolve(),
  ])
    .then(() => {
      // Si l'utilisateur a quitté le mode overview pendant le chargement,
      // ou si un nouveau rendu a été demandé depuis (autre indicateur), on
      // n'écrit pas par-dessus la vue actuelle.
      if (myToken !== _nationalRenderToken) return;
      if (
        state.currentLevel !== "communes" ||
        state.communesMode !== "overview"
      ) {
        return;
      }
      // Hydrate au cas où la course a fait que le decoratif est arrivé
      // avant le meta — un appel idempotent supplémentaire ne coûte rien.
      hydrateDecorativeWithMeta();
      drawNationalLeaderboard(el);
    })
    .catch((err) => {
      if (myToken !== _nationalRenderToken) return;
      console.error("Erreur de chargement du leaderboard national :", err);
      el.innerHTML = `
        <h2 class="panel__title">Classement national — ${escapeHtml(ind.label)}</h2>
        <p class="panel__placeholder" style="color:#c00;">
          Impossible de charger les données nécessaires au classement.
        </p>
      `;
    });
}

/** Écrit le HTML du leaderboard national une fois que decoratif + meta sont
 *  hydratés. Séparé de `renderNationalCommunesLeaderboard` pour pouvoir être
 *  appelé directement quand tout est déjà en cache (re-render synchrone). */
function drawNationalLeaderboard(el) {
  const ind = state.currentIndicator;
  const ents = state.decorativeEntities || [];
  const yearIdx = (state.decorativeYears || ANNEES_COMMUNES_FALLBACK)
    .indexOf(state.currentYear);
  const yIdx = yearIdx >= 0 ? yearIdx : 0;

  const { ranked, unranked } = computeRanking(ents, ind.key, yIdx);

  el.innerHTML = `
    <h2 class="panel__title">Classement national — ${escapeHtml(ind.label)}</h2>
    <p class="panel__subtitle">
      ${ranked.length.toLocaleString("fr-FR")} communes classées · ${state.currentYear}${ind.unit ? " · " + escapeHtml(ind.unit) : ""}
    </p>
    <p class="panel__placeholder" style="margin-bottom: 0.6rem;">
      Cliquez sur une ligne pour ouvrir le département concerné, ou sur un
      département dans la carte pour voir le détail de toutes ses communes.
    </p>
    ${renderLeaderboardHTML(ranked, unranked.length, null, ind, {
      nameSuffix: (entity) =>
        entity.depCode
          ? ` <span class="leaderboard__row-meta">(${escapeHtml(entity.depCode)})</span>`
          : "",
      itemAttr: 'data-leaderboard-national="1"',
    })}
  `;
}

// ----------------------------------------------------------------------------
// Leaderboard national — niveau Syndicats
// ----------------------------------------------------------------------------
/** Niveau syndicats : on liste les SYNDICATS exerçant la compétence
 *  sélectionnée, triés par la valeur de l'agrégat (typiquement 2024).
 *  Chaque ligne montre : rang, nom du syndicat, nature (SIVU/SIVOM/…),
 *  département, nombre de communes membres, valeur.
 *
 *  Données : `data/syndicats/leaderboards/{slug}.json` (généré par
 *  `scripts/build_syndicats_leaderboard.py`, ~116 Mo cumulés mais
 *  chargés un par indicateur, ~30-50 Ko chacun en moyenne). */
let _syndicatsRenderToken = 0;
function renderNationalSyndicatsLeaderboard(el) {
  const ind = state.currentIndicator;
  const myToken = ++_syndicatsRenderToken;

  // Si le fichier est déjà en cache (changement d'année ou re-render après
  // un changement d'indicateur déjà vu), on rend immédiatement sans flash
  // de placeholder.
  const cached = _syndicatsLeaderboardFiles.get(ind.key);
  if (cached && !(cached instanceof Promise)) {
    drawSyndicatsLeaderboard(el, cached);
    return;
  }

  el.innerHTML = `
    <h2 class="panel__title">Classement — ${escapeHtml(ind.label)}</h2>
    <p class="panel__placeholder">
      Chargement de la liste des syndicats exerçant cette compétence…
    </p>
    <p class="controls__help" style="margin-top:0.5rem;">
      Le classement liste les syndicats (SIVU, SIVOM, syndicats mixtes…)
      eux-mêmes, pas les communes membres. Chaque ligne indique le nombre
      de communes desservies par le syndicat.
    </p>
  `;

  loadSyndicatsLeaderboardFile(ind.key)
    .then((payload) => {
      if (myToken !== _syndicatsRenderToken) return;
      if (state.currentLevel !== "syndicats") return;
      drawSyndicatsLeaderboard(el, payload);
    })
    .catch((err) => {
      if (myToken !== _syndicatsRenderToken) return;
      console.error("Erreur de chargement du leaderboard syndicats :", err);
      el.innerHTML = `
        <h2 class="panel__title">Classement — ${escapeHtml(ind.label)}</h2>
        <p class="panel__placeholder" style="color:#c00;">
          Impossible de charger la liste des syndicats pour cet indicateur.
        </p>
      `;
    });
}

/** Rend le tableau leaderboard à partir du payload syndicats.
 *  Si `payload` est null → l'indicateur n'a pas de fichier (rare, ex.
 *  compétence × agrégat sans aucune valeur effective).
 *
 *  Si un syndicat est sélectionné (`state.selectedSyndicatSiren`), on
 *  affiche son détail. Sinon, le leaderboard.
 *
 *  En drill-down (par département), on filtre `payload.syndicats` sur
 *  ceux ayant le dep_code courant dans leur `member_deps`. */
function drawSyndicatsLeaderboard(el, payload) {
  const ind = state.currentIndicator;
  if (!payload || !Array.isArray(payload.syndicats) || payload.syndicats.length === 0) {
    el.innerHTML = `
      <h2 class="panel__title">Classement — ${escapeHtml(ind.label)}</h2>
      <p class="panel__placeholder">
        Aucun syndicat n'a déclaré de valeur pour cette compétence × agrégat.
      </p>
    `;
    return;
  }

  // Si on est en drill-down avec un syndicat sélectionné, affiche le détail.
  if (state.selectedSyndicatSiren) {
    const synd = payload.syndicats.find(
      (s) => s.siren === state.selectedSyndicatSiren,
    );
    if (synd) {
      drawSyndicatDetailPanel(el, synd, payload);
      return;
    }
  }

  // Filtrage en drill-down : syndicats ayant ≥1 commune membre dans un
  // des dep_codes du département courant (via member_deps).
  // - Corse : depCodes = ["2A","2B"] → on inclut les syndicats de l'un ou l'autre.
  // - Alsace : depCodes = ["67A"] expansé en ["67","68"] via expandDepCodes
  //   (les member_deps des syndicats utilisent le préfixe INSEE physique).
  let syndicatsList = payload.syndicats;
  const isDrill =
    state.syndicatsMode === "drilldown" &&
    Array.isArray(state.drillDownSyndDepCodes) &&
    state.drillDownSyndDepCodes.length > 0;
  if (isDrill) {
    const dcSet = expandDepCodes(state.drillDownSyndDepCodes);
    syndicatsList = payload.syndicats.filter(
      (s) => Array.isArray(s.member_deps) && s.member_deps.some((d) => dcSet.has(d)),
    );
  }

  const years = payload.years || ANNEES_COMMUNES_FALLBACK;
  let yIdx = years.indexOf(state.currentYear);
  if (yIdx < 0) yIdx = years.length - 1;

  // Construire les objets ranked pour réutiliser la machinerie existante.
  const ranked = [];
  const unranked = [];
  for (const s of syndicatsList) {
    const v = (s.values && yIdx < s.values.length) ? s.values[yIdx] : null;
    if (v == null || Number.isNaN(v)) {
      unranked.push({ entity: _syndicatToEntity(s), value: null, rank: null });
    } else {
      ranked.push({ entity: _syndicatToEntity(s), value: v });
    }
  }
  ranked.sort((a, b) => b.value - a.value);
  const total = ranked.length;
  let lastValue = null;
  let lastRank = 0;
  for (let i = 0; i < ranked.length; i++) {
    if (ranked[i].value !== lastValue) {
      lastRank = i + 1;
      lastValue = ranked[i].value;
    }
    ranked[i].rank = lastRank;
    ranked[i].total = total;
  }
  for (const u of unranked) u.total = total;

  // Sous-titre adapté selon mode
  const depCodesLabel = isDrill
    ? state.drillDownSyndDepCodes.map(escapeHtml).join("/")
    : "";
  const scopeLabel = isDrill
    ? `${total.toLocaleString("fr-FR")} syndicats actifs dans ${escapeHtml(state.drillDownSyndDepName)} (${depCodesLabel})`
    : `${total.toLocaleString("fr-FR")} syndicats classés (France entière)`;

  const hintInteraction = isDrill
    ? "Cliquez sur un syndicat dans la liste pour voir le détail (nature, comptes complets, communes membres)."
    : "Cliquez sur un département dans la carte pour zoomer et filtrer les syndicats qui le couvrent.";

  el.innerHTML = `
    <h2 class="panel__title">Classement — ${escapeHtml(ind.label)}</h2>
    <p class="panel__subtitle">
      ${scopeLabel} · ${state.currentYear}${ind.unit ? " · " + escapeHtml(ind.unit) : ""}
    </p>
    <p class="panel__placeholder" style="margin-bottom: 0.6rem;">
      ${hintInteraction}
    </p>
    ${renderLeaderboardHTML(ranked, unranked.length, null, ind, {
      nameSuffix: (entity) => {
        const bits = [];
        if (entity.nature) bits.push(escapeHtml(entity.nature));
        if (entity.depCode) bits.push(escapeHtml(entity.depCode));
        if (typeof entity.nMembres === "number") {
          bits.push(`${entity.nMembres} commune${entity.nMembres > 1 ? "s" : ""}`);
        }
        return bits.length
          ? ` <span class="leaderboard__row-meta">(${bits.join(" · ")})</span>`
          : "";
      },
      // Marquer les lignes "syndicat" pour que le handler de clic du panel
      // puisse les distinguer et déclencher le détail (pas le drill-down
      // communes par défaut).
      itemAttr: 'data-leaderboard-syndicat="1"',
    })}
  `;
}

/** Adapte un objet syndicat (sortie du builder Python) au format attendu
 *  par `renderLeaderboardHTML` qui consomme des entités à champs id/label. */
function _syndicatToEntity(s) {
  return {
    id: s.siren || s.nom,
    label: s.nom || "(sans nom)",
    nature: s.nature || "",
    depCode: s.dep_code || "",
    nMembres: s.n_membres || 0,
  };
}

/** Token monotone : protège contre les écritures obsolètes quand le
 *  fichier détail arrive après que l'utilisateur ait déjà cliqué ailleurs
 *  (changement d'indicateur, fermeture du détail, etc.). */
let _syndicatDetailRenderToken = 0;

/** Affiche le détail d'un syndicat dans le panel : ses 43 agrégats financiers
 *  avec sparklines + valeur courante, à l'image des panels communes / EPCI /
 *  départements / régions. Les agrégats sont chargés depuis
 *  `data/syndicats/details/{siren}.json` (lazy, ~5 Ko par syndicat).
 *
 *  L'agrégat correspondant à l'indicateur courant est surligné et reçoit
 *  un badge de rang (position dans le leaderboard syndicat de la
 *  compétence courante). */
function drawSyndicatDetailPanel(el, synd, leaderboardPayload) {
  const ind = state.currentIndicator;
  const myToken = ++_syndicatDetailRenderToken;

  // Placeholder synchrone immédiat (pas de flash blanc pendant le fetch)
  el.innerHTML = `
    <div class="syndicat-detail">
      <button class="drilldown-back" type="button" id="syndicat-detail-back">
        <span aria-hidden="true">←</span> Retour au classement
      </button>
      <h2 class="panel__title">${escapeHtml(synd.nom || "(sans nom)")}</h2>
      <p class="panel__placeholder">Chargement des comptes du syndicat…</p>
    </div>
  `;
  _attachSyndicatDetailBackBtn();

  loadSyndicatDetailFile(synd.siren)
    .then((detail) => {
      if (myToken !== _syndicatDetailRenderToken) return;
      if (state.selectedSyndicatSiren !== synd.siren) return;
      if (!detail) {
        el.innerHTML = `
          <div class="syndicat-detail">
            <button class="drilldown-back" type="button" id="syndicat-detail-back">
              <span aria-hidden="true">←</span> Retour au classement
            </button>
            <h2 class="panel__title">${escapeHtml(synd.nom || "(sans nom)")}</h2>
            <p class="panel__placeholder" style="color:#c00;">
              Impossible de charger le détail de ce syndicat.
            </p>
          </div>
        `;
        _attachSyndicatDetailBackBtn();
        return;
      }
      _drawSyndicatDetailFromFile(el, detail, leaderboardPayload, ind);
    });
}

/** Rendu effectif du panel détail à partir du fichier détail chargé. */
function _drawSyndicatDetailFromFile(el, detail, leaderboardPayload, ind) {
  const years = detail.years || ANNEES_COMMUNES_FALLBACK;
  let yIdx = years.indexOf(state.currentYear);
  if (yIdx < 0) yIdx = years.length - 1;

  // Extrait l'agrégat courant depuis la clé d'indicateur :
  // "Syndicats {competence[:60]} — {agregat} (€)" → agregat
  const currentAgregat = _extractAgregatFromSyndicatsIndicatorKey(ind.key);

  // Calcul du rang du syndicat pour l'agrégat courant (utilise le payload
  // leaderboard déjà en mémoire, filtré sur la compétence courante).
  let rankInfo = null;
  if (leaderboardPayload && Array.isArray(leaderboardPayload.syndicats)) {
    const sorted = leaderboardPayload.syndicats
      .map((s) => ({ siren: s.siren, v: s.values?.[yIdx] }))
      .filter((s) => s.v != null && !Number.isNaN(s.v))
      .sort((a, b) => b.v - a.v);
    const idx = sorted.findIndex((s) => s.siren === detail.siren);
    if (idx >= 0) {
      rankInfo = { rank: idx + 1, total: sorted.length };
    }
  }

  // Liste des agrégats triés par ordre alphabétique (sauf si on veut un
  // ordre canonique — pour l'instant alpha = stable et neutre).
  const agregatNames = Object.keys(detail.comptes || {}).sort();

  const items = agregatNames.map((agr) => {
    const serie = detail.comptes[agr] || [];
    const v = serie[yIdx];
    const sparkline = buildSparkline(serie, yIdx);
    const isHighlighted = agr === currentAgregat;
    const highlightedClass = isHighlighted ? " panel__indicator--highlighted" : "";
    const rankBadge =
      isHighlighted && rankInfo
        ? `<span class="panel__rank-badge" title="Rang sur ${rankInfo.total} syndicats classés pour cette compétence">${rankInfo.rank}<span class="panel__rank-badge-total"> / ${rankInfo.total}</span></span>`
        : "";
    return `
      <li class="panel__indicator${highlightedClass}">
        <span class="panel__indicator-label">${escapeHtml(agr)}${rankBadge}</span>
        ${sparkline}
        <span class="panel__indicator-value">${formatValue(v, "€")}</span>
      </li>
    `;
  }).join("");

  // Métadonnées syndicat
  const natureLabel = detail.nature || "Syndicat";
  const siege = detail.commune_siege_nom
    ? `${escapeHtml(detail.commune_siege_nom)} (${escapeHtml(detail.dep_code || "?")})`
    : `département ${escapeHtml(detail.dep_code || "?")}`;
  const members = Array.isArray(detail.members) ? detail.members : [];
  const nMembres = members.length;
  // `member_groups` = membres non-communes (EPCI / personnes morales) d'un
  // syndicat de second degré. Les communes ci-dessus proviennent alors (en
  // tout ou partie) de l'expansion de ces EPCI vers leurs communes.
  const groups = Array.isArray(detail.member_groups) ? detail.member_groups : [];
  const nGroups = groups.length;
  const nEpci = groups.filter((g) => (g.categ || "").toLowerCase() === "groupement").length;
  const communeWord = (n) => `${n} commune${n > 1 ? "s" : ""}`;
  const subtitle = nGroups > 0
    ? `${escapeHtml(natureLabel)} · siège : ${siege} · ${communeWord(nMembres)} · ${nGroups} membre${nGroups > 1 ? "s" : ""} groupé${nGroups > 1 ? "s" : ""}`
    : `${escapeHtml(natureLabel)} · siège : ${siege} · ${communeWord(nMembres)} membre${nMembres > 1 ? "s" : ""}`;

  const competences = Array.isArray(detail.competences) ? detail.competences : [];
  const competencesHtml = competences.length
    ? `<details class="syndicat-detail__competences">
         <summary>${competences.length} compétence${competences.length > 1 ? "s" : ""} exercée${competences.length > 1 ? "s" : ""}</summary>
         <ul>${competences.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
       </details>`
    : "";

  // Membres groupés (EPCI / personnes morales) : structure réelle du syndicat
  // de second degré, affichée avant la liste des communes du territoire.
  const groupsHtml = nGroups
    ? `<details class="syndicat-detail__members">
         <summary>${nGroups} membre${nGroups > 1 ? "s" : ""} groupé${nGroups > 1 ? "s" : ""}${nEpci ? ` (dont ${nEpci} intercommunalité${nEpci > 1 ? "s" : ""})` : ""}</summary>
         <ul>${groups.map((g) => {
           const cc = g.nb_communes != null
             ? ` <span class="leaderboard__row-meta">(${g.nb_communes} commune${g.nb_communes > 1 ? "s" : ""})</span>`
             : "";
           return `<li>${escapeHtml(g.nom || "")}${cc}</li>`;
         }).join("")}</ul>
       </details>`
    : "";

  const membersSummary = nGroups > 0
    ? `${communeWord(nMembres)} du territoire`
    : `${communeWord(nMembres)} membre${nMembres > 1 ? "s" : ""}`;
  const membersHtml = members.length
    ? `<details class="syndicat-detail__members">
         <summary>${membersSummary}</summary>
         <ul>${members.map((m) => `<li>${escapeHtml(m.nom)} <span class="leaderboard__row-meta">(${escapeHtml(m.insee)})</span></li>`).join("")}</ul>
       </details>`
    : "";

  el.innerHTML = `
    <div class="syndicat-detail">
      <button class="drilldown-back" type="button" id="syndicat-detail-back">
        <span aria-hidden="true">←</span> Retour au classement
      </button>
      <h2 class="panel__title">${escapeHtml(detail.nom || "(sans nom)")}</h2>
      <p class="panel__subtitle">${subtitle}</p>
      <p class="panel__year-info">Données ${state.currentYear} · évolution sur ${years.length} ans</p>
      ${competencesHtml}
      <ul class="panel__indicators">${items}</ul>
      ${groupsHtml}
      ${membersHtml}
      ${detail.siren
        ? `<p class="panel__placeholder syndicat-detail__siren">SIREN : <code>${escapeHtml(detail.siren)}</code></p>`
        : ""}
    </div>
  `;
  _attachSyndicatDetailBackBtn();
}

/** Extrait l'agrégat à partir d'une clé d'indicateur syndicat.
 *  Format attendu : "Syndicats {competence[:60]} — {agregat} (€)".
 *  Retourne null si la clé n'est pas au format syndicat. */
function _extractAgregatFromSyndicatsIndicatorKey(key) {
  if (!key || !key.startsWith("Syndicats ")) return null;
  // Sépare sur " — " (em-dash entouré d'espaces) entre compétence et agrégat
  const idx = key.indexOf(" — ");
  if (idx < 0) return null;
  let agregatPart = key.slice(idx + 3);
  // Retire le " (€)" final
  agregatPart = agregatPart.replace(/\s*\(€\)\s*$/, "");
  return agregatPart;
}

/** Attache le handler du bouton "Retour au classement" (commun aux deux
 *  états du panel : placeholder pendant le chargement et rendu final). */
function _attachSyndicatDetailBackBtn() {
  const backBtn = document.getElementById("syndicat-detail-back");
  if (!backBtn) return;
  backBtn.addEventListener("click", () => {
    state.selectedSyndicatSiren = null;
    if (state.syndicatsMode === "drilldown") {
      state.selectedId = null;
      clearSelectionHighlight();
    }
    renderPanel();
  });
}

/** Libellé décrivant l'ensemble classé selon le niveau / le drill-down.
 *  Ex. "13 régions", "101 départements", "284 communes du Pas-de-Calais",
 *  "1335 intercommunalités", "27 communes — Eurométropole de Strasbourg". */
function buildLeaderboardLabel() {
  const n = state.currentEntities.length;
  const lvl = state.currentLevel;
  if (lvl === "regions") return `${n} régions`;
  if (lvl === "departements") return `${n} départements`;
  // Intercommunalités : overview = liste des EPCIs ; drilldown = EPCIs
  // présents dans une région (le `n` du classement est alors le nombre
  // d'EPCIs, pas de communes).
  if (lvl === "intercommunalites") {
    if (state.intercommunalitesMode === "drilldown") {
      const reg = state.drillDownRegName || "cette région";
      const nbEpcis = state.currentRegionEpciSirens
        ? state.currentRegionEpciSirens.size
        : 0;
      return `${nbEpcis} intercommunalité${nbEpcis > 1 ? "s" : ""} — ${reg}`;
    }
    return `${n.toLocaleString("fr-FR")} intercommunalités`;
  }
  // Communes : seulement en drill-down (l'overview affiche le leaderboard national)
  if (lvl === "communes" && state.communesMode === "drilldown") {
    const dep = state.drillDownDepName || "ce département";
    return `${n} communes — ${dep}`;
  }
  return `${n} entités`;
}

/** Construit une sparkline SVG pour une série de valeurs.
 *  - `serie` : tableau de valeurs (peut contenir des null pour des trous)
 *  - `currentIdx` : index dans la série de l'année courante (mise en évidence)
 *  Retourne du HTML inline ou chaîne vide si moins de 2 points valides. */
function buildSparkline(serie, currentIdx, width = 80, height = 18) {
  if (!serie || serie.length < 2) return "";
  const valid = [];
  for (let i = 0; i < serie.length; i++) {
    if (serie[i] != null && !Number.isNaN(serie[i])) valid.push({ i, v: serie[i] });
  }
  if (valid.length < 2) return "";

  const min = Math.min(...valid.map((p) => p.v));
  const max = Math.max(...valid.map((p) => p.v));
  const range = max - min || 1;
  const xStep = serie.length > 1 ? width / (serie.length - 1) : 0;
  const yPad = 2;

  const yFor = (v) => height - yPad - ((v - min) / range) * (height - 2 * yPad);
  const points = valid.map((p) => ({ x: p.i * xStep, y: yFor(p.v) }));
  // Fidélité totale : coordonnées SVG à pleine précision (pas d'arrondi
  // à toFixed(1)). Cela alourdit légèrement le DOM mais respecte la
  // directive utilisateur "zéro arrondi nulle part".
  const path = points
    .map((p, k) => (k === 0 ? "M" : "L") + p.x + "," + p.y)
    .join(" ");

  // Point en évidence sur l'année courante (si valide pour cette série)
  let dot = "";
  const currentPoint = valid.find((p) => p.i === currentIdx);
  if (currentPoint) {
    const cx = currentPoint.i * xStep;
    const cy = yFor(currentPoint.v);
    dot = `<circle cx="${cx}" cy="${cy}" r="2.2" fill="var(--accent)" />`;
  }

  return `<svg class="sparkline" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${path}" fill="none" stroke="#888" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"/>
    ${dot}
  </svg>`;
}

// ----------------------------------------------------------------------------
// Grande courbe d'évolution (panneau) — SVG fait main, multi-séries
// ----------------------------------------------------------------------------

/** Formate une valeur pour une étiquette d'AXE (compacte : 1,2 M / 3,4 k…).
 *  PRÉSENTATION uniquement : la valeur exacte (sans arrondi, doctrine) reste
 *  affichée au survol du point et dans la liste d'indicateurs. */
function formatAxisTick(v) {
  if (v == null || Number.isNaN(v)) return "";
  const abs = Math.abs(v);
  if (abs !== 0 && abs < 1) {
    return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 }).format(v);
  }
  if (abs >= 10000) {
    return new Intl.NumberFormat("fr-FR", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(v);
  }
  return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(v);
}

/** "Nice numbers" (Heckbert) pour des graduations d'axe lisibles. */
function _niceNum(range, round) {
  if (!(range > 0) || !Number.isFinite(range)) return 1;
  const exp = Math.floor(Math.log10(range));
  const frac = range / Math.pow(10, exp);
  let nice;
  if (round) {
    nice = frac < 1.5 ? 1 : frac < 3 ? 2 : frac < 7 ? 5 : 10;
  } else {
    nice = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
  }
  return nice * Math.pow(10, exp);
}

/** Échelle Y « jolie » : renvoie { min, max, ticks } englobant [min,max]. */
function _niceScale(min, max, count = 5) {
  if (!(min < max)) {
    const d = Math.abs(min) || 1;
    min -= d * 0.5;
    max += d * 0.5;
  }
  const step = _niceNum(_niceNum(max - min, false) / Math.max(1, count - 1), true);
  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = niceMin; v <= niceMax + step * 0.5; v += step) {
    ticks.push(Math.round(v / step) * step); // anti-bruit flottant
  }
  return { min: niceMin, max: niceMax, ticks };
}

// Couleurs des séries superposées (MGP, comparaison multi-territoires).
const CHART_EXTRA_COLORS = ["#d2691e", "#6a51a3", "#2c7fb8", "#d6336c", "#198754"];

/** Grande courbe d'évolution temporelle (axes chiffrés + survol).
 *  - `serie`  : valeurs de la série principale, alignées sur `years`.
 *  - `years`  : tableau d'années (axe X).
 *  - `indicator` : { label, unit }.
 *  - `opts.currentIdx`   : index de l'année surlignée (guide vertical + gros point).
 *  - `opts.primaryLabel` : libellé de la série principale (légende si extras).
 *  - `opts.extraSeries`  : [{ serie, label, color }] superposées (MGP…).
 *  Fidélité : trous (null) NON interpolés (rupture du tracé) ; série à un seul
 *  point valide = pas de courbe trompeuse (point + mention « donnée ponctuelle »).
 *  Coordonnées SVG à pleine précision (pas d'arrondi), comme le sparkline. */
function buildEvolutionChart(serie, years, indicator, opts = {}) {
  years = years || [];
  serie = serie || [];
  const unit = indicator?.unit || "";
  const currentIdx = opts.currentIdx ?? -1;
  const extraSeries = (opts.extraSeries || []).map((s, i) => ({
    serie: s.serie || [],
    label: s.label || "",
    color: s.color || CHART_EXTRA_COLORS[i % CHART_EXTRA_COLORS.length],
  }));
  const series = [
    {
      serie,
      label: opts.primaryLabel || indicator?.label || "",
      color: opts.primaryColor || "var(--accent)",
      primary: true,
    },
    ...extraSeries,
  ];

  // Valeurs valides, toutes séries confondues (pour l'échelle Y).
  const allValid = [];
  let idxWithData = 0;
  for (let i = 0; i < years.length; i++) {
    let any = false;
    for (const s of series) {
      const v = s.serie[i];
      if (v != null && !Number.isNaN(v)) {
        allValid.push(v);
        any = true;
      }
    }
    if (any) idxWithData++;
  }

  if (allValid.length === 0) {
    return `<div class="panel__chart panel__chart--empty">Aucune donnée chiffrée sur la période pour cet indicateur.</div>`;
  }

  // Géométrie (unités viewBox ; le conteneur CSS gère la taille réelle).
  // W/H paramétrables : 340×190 dans le panneau, plus grand dans le tiroir.
  const W = opts.W || 340, H = opts.H || 190, mL = 50, mR = 14, mT = 14, mB = 28;
  const innerW = W - mL - mR;
  const innerH = H - mT - mB;
  const n = years.length;
  const xFor = (i) => (n <= 1 ? mL + innerW / 2 : mL + (i / (n - 1)) * innerW);

  let dataMin = Infinity, dataMax = -Infinity;
  for (const v of allValid) {
    if (v < dataMin) dataMin = v;
    if (v > dataMax) dataMax = v;
  }
  const scale = _niceScale(dataMin, dataMax, 5);
  const yMin = scale.min, yMax = scale.max;
  const yRange = yMax - yMin || 1;
  const yFor = (v) => mT + (1 - (v - yMin) / yRange) * innerH;

  // Grille horizontale + étiquettes Y.
  let grid = "";
  for (const t of scale.ticks) {
    if (t < yMin - 1e-9 || t > yMax + 1e-9) continue;
    const y = yFor(t);
    grid += `<line x1="${mL}" y1="${y}" x2="${mL + innerW}" y2="${y}" stroke="#ececec" stroke-width="1"/>`;
    grid += `<text x="${mL - 6}" y="${y}" text-anchor="end" dominant-baseline="middle" font-size="8" fill="#777">${escapeHtml(formatAxisTick(t))}</text>`;
  }

  // Étiquettes X (années). Au-delà de 9 années, on n'affiche qu'une sur deux
  // (+ première, dernière, année courante) pour éviter le chevauchement.
  const labelEvery = n <= 9 ? 1 : 2;
  let xlabels = "";
  for (let i = 0; i < n; i++) {
    const show = i % labelEvery === 0 || i === n - 1 || i === currentIdx;
    if (!show) continue;
    const isCur = i === currentIdx;
    xlabels += `<text x="${xFor(i)}" y="${H - 8}" text-anchor="middle" font-size="8" fill="${isCur ? "var(--accent)" : "#777"}" font-weight="${isCur ? "700" : "400"}">${years[i]}</text>`;
  }

  // Guide vertical sur l'année courante.
  let guide = "";
  if (currentIdx >= 0 && currentIdx < n) {
    const gx = xFor(currentIdx);
    guide = `<line x1="${gx}" y1="${mT}" x2="${gx}" y2="${mT + innerH}" stroke="#cfcfcf" stroke-width="1" stroke-dasharray="2 2"/>`;
  }

  // Tracés + points pour chaque série (trous non interpolés).
  let paths = "", dots = "";
  for (const s of series) {
    let d = "", move = true;
    for (let i = 0; i < n; i++) {
      const v = s.serie[i];
      if (v == null || Number.isNaN(v)) { move = true; continue; }
      const x = xFor(i), y = yFor(v);
      d += (move ? "M" : "L") + x + "," + y + " ";
      move = false;
      const isCur = i === currentIdx;
      const r = isCur && s.primary ? 3.4 : isCur ? 2.8 : 2;
      dots += `<circle cx="${x}" cy="${y}" r="${r}" style="fill:${s.color}"${isCur ? ' stroke="#fff" stroke-width="1"' : ""}/>`;
    }
    if (d) {
      paths += `<path d="${d.trim()}" fill="none" style="stroke:${s.color}" stroke-width="${s.primary ? 1.8 : 1.4}" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
  }

  // Bandes de survol : un rectangle transparent par année, tooltip natif.
  let bands = "";
  const bandW = n <= 1 ? innerW : innerW / (n - 1);
  for (let i = 0; i < n; i++) {
    const cx = xFor(i);
    const bx = Math.max(mL, cx - bandW / 2);
    const bw = Math.min(mL + innerW, cx + bandW / 2) - bx;
    const lines = [String(years[i])];
    for (const s of series) {
      const v = s.serie[i];
      const fv = v == null || Number.isNaN(v) ? "—" : formatValue(v, unit);
      lines.push(series.length > 1 ? `${s.label} : ${fv}` : fv);
    }
    bands += `<rect x="${bx}" y="${mT}" width="${bw}" height="${innerH}" fill="transparent" pointer-events="all"><title>${escapeHtml(lines.join("\n"))}</title></rect>`;
  }

  const firstYear = years[0], lastYear = years[n - 1];
  const ariaLabel = `Évolution de ${indicator?.label || ""}${unit ? " en " + unit : ""}, de ${firstYear} à ${lastYear}`;

  // Légende (uniquement si séries superposées).
  let legend = "";
  if (extraSeries.length > 0) {
    const sw = (color, label) =>
      `<span class="panel__chart-legend-item"><span class="panel__chart-legend-swatch" style="background:${color}"></span>${escapeHtml(label)}</span>`;
    legend = `<div class="panel__chart-legend">${sw(series[0].color, series[0].label)}${extraSeries.map((s) => sw(s.color, s.label)).join("")}</div>`;
  }

  // Mention « donnée ponctuelle » si la série principale n'a qu'un point.
  const primaryValid = serie.filter((v) => v != null && !Number.isNaN(v));
  let note = "";
  if (primaryValid.length < 2 && extraSeries.length === 0) {
    const yOnly = years[serie.findIndex((v) => v != null && !Number.isNaN(v))];
    note = `<div class="panel__chart-note">Donnée ponctuelle${yOnly != null ? " (millésime " + yOnly + ")" : ""} — pas de série temporelle pour cet indicateur.</div>`;
  }

  return `<figure class="panel__chart">
    <figcaption class="panel__chart-caption">${escapeHtml(indicator?.label || "")}${unit ? ` <span class="panel__chart-unit">${escapeHtml(unit)}</span>` : ""}</figcaption>
    <svg class="panel__chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(ariaLabel)}">
      ${grid}${guide}${paths}${dots}${xlabels}${bands}
    </svg>
    ${legend}${note}
  </figure>`;
}

// ----------------------------------------------------------------------------
// Leaderboard / classement
// ----------------------------------------------------------------------------

/** Calcule le classement des entités d'un niveau pour un indicateur donné.
 *  - Tri décroissant : la valeur la plus élevée a le rang 1.
 *  - Gestion des ex æquo : deux entités avec la même valeur partagent le
 *    même rang (méthode "standard competition ranking" : 1, 2, 2, 4…).
 *  - Les entités sans valeur (null/NaN) sont exclues du classement et
 *    listées à part en queue (rang `null`) — utile pour les indicateurs
 *    spécifiques aux DOM ou aux régions dont les données manquent.
 *  Retourne un tableau `[{ entity, value, rank, total }]` trié, avec
 *  `total` = nombre d'entités classées (pour afficher "5e / 13"). */
function computeRanking(entities, indicatorKey, yearIdx) {
  const ranked = [];
  const unranked = [];
  for (const e of entities) {
    const v = getValueForYear(e.data, indicatorKey, yearIdx);
    if (v == null || Number.isNaN(v)) {
      unranked.push({ entity: e, value: null, rank: null });
    } else {
      ranked.push({ entity: e, value: v });
    }
  }
  ranked.sort((a, b) => b.value - a.value);

  const total = ranked.length;
  let lastValue = null;
  let lastRank = 0;
  for (let i = 0; i < ranked.length; i++) {
    if (ranked[i].value !== lastValue) {
      lastRank = i + 1;
      lastValue = ranked[i].value;
    }
    ranked[i].rank = lastRank;
    ranked[i].total = total;
  }
  for (const u of unranked) u.total = total;
  return { ranked, unranked, total };
}

/** Détermine quels rangs afficher dans le leaderboard, selon la taille du
 *  classement et la présence éventuelle d'une entité sélectionnée.
 *  Retourne un tableau d'index croissants (dans `ranked`), avec d'éventuels
 *  "trous" matérialisés par "…" lors du rendu.
 *
 *  Stratégie :
 *    - Jusqu'à 1000 entités : on montre TOUT, sans troncature. Cela couvre
 *      les régions (13), les départements (≈100) et les communes en
 *      drill-down (de quelques dizaines à ≈900). L'utilisateur scrolle dans
 *      le panneau si besoin.
 *    - Au-delà (cas du leaderboard national des communes, ≈35 000 entités) :
 *      on tronque, car afficher 35 000 lignes serait à la fois injouable côté
 *      DOM et illisible côté UX.
 *        · Sélection présente : top 25 + (sélection ± 2 voisins) + bottom 10.
 *        · Pas de sélection :   top 50 + bottom 10.
 */
function pickLeaderboardIndices(ranked, selectedId) {
  const n = ranked.length;
  const FULL_LIST_THRESHOLD = 1000;
  if (n <= FULL_LIST_THRESHOLD) return ranked.map((_, i) => i);

  const indices = new Set();
  const TOP_WITH_SEL = 25;
  const BOTTOM_WITH_SEL = 10;
  const TOP_NOSEL = 50;
  const BOTTOM_NOSEL = 10;

  if (selectedId) {
    for (let i = 0; i < Math.min(TOP_WITH_SEL, n); i++) indices.add(i);
    const sel = ranked.findIndex((r) => r.entity.id === selectedId);
    if (sel >= 0) {
      for (let i = Math.max(0, sel - 2); i <= Math.min(n - 1, sel + 2); i++) {
        indices.add(i);
      }
    }
    for (let i = Math.max(0, n - BOTTOM_WITH_SEL); i < n; i++) indices.add(i);
  } else {
    for (let i = 0; i < Math.min(TOP_NOSEL, n); i++) indices.add(i);
    for (let i = Math.max(0, n - BOTTOM_NOSEL); i < n; i++) indices.add(i);
  }

  return [...indices].sort((a, b) => a - b);
}

/** Construit le HTML du leaderboard pour l'indicateur courant.
 *  - `ranked` : array trié de `{entity, value, rank, total}` issus de computeRanking
 *  - `selectedId` : id de l'entité sélectionnée (mise en surbrillance)
 *  - `indicator` : objet indicateur ({label, unit, …})
 *  - `opts.nameSuffix(entity)` : optionnel, retourne du HTML à ajouter
 *    après le nom (ex: code département entre parenthèses).
 *  - `opts.itemAttr` : optionnel, attribut HTML supplémentaire injecté sur
 *    chaque `<li>` pour permettre au délégué de clic de distinguer plusieurs
 *    types de leaderboard (ex: national vs régional). */
// ============================================================================
// Liste de territoires : champ de recherche + défilement VIRTUALISÉ
// ----------------------------------------------------------------------------
// Le classement peut être très long (≈35 000 communes, ≈8 000 syndicats). Pour
// l'afficher EN ENTIER tout en restant fluide, on virtualise : seules les
// ~30 lignes visibles dans la fenêtre de défilement sont réellement dans le
// DOM (recyclées au scroll). Un champ de recherche filtre par nom (accents et
// casse ignorés). Le clic reste géré par la délégation de #info-panel.
//
// renderLeaderboardHTML() ne fait que poser le « shell » (recherche + zone de
// défilement vide) et met les données de côté dans `_lbStash`. Un
// MutationObserver (setupLeaderboardAutoInit) détecte le shell dès son
// insertion et appelle initActiveLeaderboard() qui câble tout.
// ============================================================================
const _LB_ROW_H = 46; // hauteur fixe d'une ligne (px) — requise pour virtualiser ; tient 2 lignes de nom
const _lbStash = new Map(); // id de shell -> { ranked, indicator, opts, selectedId }
let _lbSeq = 0;
let _lbQuery = ""; // recherche courante (persiste tant que le contexte ne change pas)
let _lbCtxKey = ""; // signature niveau/mode/drill — réinitialise la recherche au changement

/** Signature du contexte courant : si elle change (niveau, drill-down…), on
 *  vide la recherche pour repartir propre sur la nouvelle liste. */
function _lbContextKey() {
  return [
    state.currentLevel, state.communesMode, state.intercommunalitesMode,
    state.syndicatsMode, state.drillDownDepName, state.drillDownRegCode,
    (state.drillDownSyndDepCodes || []).join(","),
  ].join("|");
}

/** Texte de recherche normalisé d'une entité (nom + code dpt + nature). */
function _lbSearchText(entity) {
  let s = entity.label || "";
  if (entity.depCode) s += " " + entity.depCode;
  if (entity.nature) s += " " + entity.nature;
  return _comboNorm(s);
}

/** Abréviations du préfixe juridique (présentation uniquement — le nom complet
 *  reste disponible au survol et dans l'aria-label). Permet de distinguer les
 *  intercommunalités dont le nom commence toutes pareil ("Communauté de
 *  communes de…"). Ordre : les libellés les plus longs/spécifiques d'abord. */
const _LB_ABBREV = [
  ["Communauté de communes", "CC"],
  ["Communauté d'agglomération", "CA"],
  ["Communauté urbaine", "CU"],
  ["Établissement public territorial", "EPT"],
  ["Syndicat intercommunal à vocation multiple", "SIVOM"],
  ["Syndicat intercommunal à vocation unique", "SIVU"],
  ["Syndicat mixte fermé", "SM fermé"],
  ["Syndicat mixte ouvert", "SM ouvert"],
  ["Syndicat mixte", "SM"],
  ["Syndicat intercommunal", "SI"],
];

/** Nom abrégé pour l'AFFICHAGE dans la liste (préfixe juridique → sigle).
 *  Sans correspondance, renvoie le nom inchangé. */
function _lbDisplayName(label) {
  if (!label) return "";
  const low = label.toLowerCase();
  for (const [full, abbr] of _LB_ABBREV) {
    if (low.startsWith(full.toLowerCase())) return abbr + label.slice(full.length);
  }
  return label;
}

/** HTML d'une ligne du classement (markup identique à l'ancien : la délégation
 *  de clic et les styles existants s'appliquent tels quels). Nom abrégé pour
 *  l'affichage, nom complet conservé dans `title` (survol) et `aria-label`. */
function _lbRowHTML(r, indicator, opts, selectedId) {
  const nameSuffix = opts.nameSuffix || (() => "");
  const itemAttr = opts.itemAttr ? " " + opts.itemAttr : "";
  const isSel = r.entity.id === selectedId;
  const fullLabel = r.entity.label ?? "";
  const fullEsc = escapeHtml(fullLabel);
  const displayEsc = escapeHtml(_lbDisplayName(fullLabel));
  const valTxt = formatValue(r.value, indicator.unit);
  return `<li class="leaderboard__row${isSel ? " leaderboard__row--selected" : ""}" data-leaderboard-id="${escapeHtml(String(r.entity.id))}"${itemAttr} tabindex="0" title="${fullEsc}" aria-label="Rang ${r.rank} sur ${r.total} : ${fullEsc}, ${valTxt}"><span class="leaderboard__rank">${r.rank}.</span><span class="leaderboard__name">${displayEsc}${nameSuffix(r.entity)}</span><span class="leaderboard__value">${valTxt}</span></li>`;
}

function renderLeaderboardHTML(ranked, unrankedCount, selectedId, indicator, opts = {}) {
  if (ranked.length === 0) {
    return `<p class="leaderboard__empty">Aucune valeur disponible pour cet indicateur.</p>`;
  }
  const id = "lb" + ++_lbSeq;
  if (_lbStash.size > 40) _lbStash.clear(); // garde-fou anti-fuite
  _lbStash.set(id, { ranked, indicator, opts, selectedId });

  const unrankedNote =
    unrankedCount > 0
      ? `<p class="leaderboard__footnote">${unrankedCount.toLocaleString("fr-FR")} entité${unrankedCount > 1 ? "s" : ""} sans donnée pour cet indicateur (non classée${unrankedCount > 1 ? "s" : ""}).</p>`
      : "";

  return `
    <div class="lb" data-lb-id="${id}">
      <input type="search" id="${id}-search" class="lb__search" placeholder="Rechercher dans la liste…"
             aria-label="Rechercher un territoire dans la liste" autocomplete="off" />
      <p class="lb__count" aria-live="polite"></p>
      <div class="lb__viewport"><div class="lb__sizer"><ul class="lb__rows"></ul></div></div>
      ${unrankedNote}
    </div>
  `;
}

/** Virtualise + câble la recherche sur un shell `.lb` fraîchement inséré.
 *  Idempotent (marque le shell `data-lb-ready="1"`). */
function initActiveLeaderboard(lbEl) {
  if (!lbEl || lbEl.dataset.lbReady === "1") return;
  const data = _lbStash.get(lbEl.dataset.lbId);
  if (!data) return;
  lbEl.dataset.lbReady = "1";
  _lbStash.delete(lbEl.dataset.lbId);
  const { ranked, indicator, opts, selectedId } = data;

  // Réinitialise la recherche si le contexte (niveau / drill-down) a changé.
  const ctx = _lbContextKey();
  if (ctx !== _lbCtxKey) { _lbCtxKey = ctx; _lbQuery = ""; }

  const viewport = lbEl.querySelector(".lb__viewport");
  const sizer = lbEl.querySelector(".lb__sizer");
  const rowsEl = lbEl.querySelector(".lb__rows");
  const searchEl = lbEl.querySelector(".lb__search");
  const countEl = lbEl.querySelector(".lb__count");
  if (!viewport || !sizer || !rowsEl) return;

  let filtered = ranked;

  const applyFilter = () => {
    const terms = _comboNorm(_lbQuery.trim()).split(/\s+/).filter(Boolean);
    filtered = terms.length === 0
      ? ranked
      : ranked.filter((r) => {
          const t = _lbSearchText(r.entity);
          return terms.every((term) => t.includes(term));
        });
  };

  const renderWindow = () => {
    const total = filtered.length;
    if (total === 0) {
      sizer.style.height = "auto";
      rowsEl.style.transform = "translateY(0)";
      rowsEl.innerHTML = `<li class="lb__empty">Aucun résultat pour « ${escapeHtml(_lbQuery.trim())} ».</li>`;
      return;
    }
    sizer.style.height = total * _LB_ROW_H + "px";
    const vpH = viewport.clientHeight || 360;
    const buffer = 6;
    const start = Math.max(0, Math.floor(viewport.scrollTop / _LB_ROW_H) - buffer);
    const end = Math.min(total, start + Math.ceil(vpH / _LB_ROW_H) + buffer * 2);
    rowsEl.style.transform = `translateY(${start * _LB_ROW_H}px)`;
    let html = "";
    for (let i = start; i < end; i++) html += _lbRowHTML(filtered[i], indicator, opts, selectedId);
    rowsEl.innerHTML = html;
  };

  const updateCount = () => {
    if (!countEl) return;
    countEl.textContent = _lbQuery.trim()
      ? `${filtered.length.toLocaleString("fr-FR")} résultat${filtered.length > 1 ? "s" : ""} sur ${ranked.length.toLocaleString("fr-FR")}`
      : "";
  };

  if (searchEl) {
    searchEl.value = _lbQuery;
    searchEl.addEventListener("input", () => {
      _lbQuery = searchEl.value;
      applyFilter();
      viewport.scrollTop = 0;
      renderWindow();
      updateCount();
    });
  }
  viewport.addEventListener("scroll", renderWindow);

  applyFilter();
  renderWindow();
  updateCount();
}

/** Branche une fois pour toutes l'auto-initialisation des leaderboards : dès
 *  qu'un shell `.lb` non initialisé apparaît dans #info-panel (à chaque
 *  renderPanel / drawNationalLeaderboard / drawSyndicatsLeaderboard), on le
 *  virtualise — évite de devoir hooker chaque point de rendu. */
function setupLeaderboardAutoInit() {
  const panel = $("#info-panel");
  if (!panel) return;
  const obs = new MutationObserver(() => {
    const lb = panel.querySelector(".lb:not([data-lb-ready])");
    if (lb) initActiveLeaderboard(lb);
  });
  obs.observe(panel, { childList: true, subtree: true });
}

/** Équivalent du sparkline pour un indicateur CATÉGORIEL : une bande de
 *  pastilles (une par année), coloriée par catégorie, l'année courante en
 *  évidence. Montre l'évolution de la catégorie dans le temps. */
function buildCategoricalStrip(ind, serie, currentIdx) {
  if (!serie || serie.length === 0) return "";
  const cmap = categoryColorMap(ind);
  const cells = serie
    .map((code, i) => {
      const color = code == null ? null : cmap.get(String(code));
      const cls =
        "cat-strip__cell" +
        (i === currentIdx ? " cat-strip__cell--current" : "") +
        (color ? "" : " cat-strip__cell--nodata");
      const title =
        code == null ? "donnée absente" : escapeHtml(categoryLabel(ind, code));
      const bg = color ? ` style="background:${color}"` : "";
      return `<span class="${cls}"${bg} title="${title}"></span>`;
    })
    .join("");
  return `<span class="cat-strip" aria-hidden="true">${cells}</span>`;
}

/** « Classement » d'un indicateur CATÉGORIEL : on ne classe pas des nombres,
 *  on affiche la RÉPARTITION des entités par catégorie (effectif + part), puis
 *  — si l'effectif reste raisonnable (drill-down) — la liste cliquable groupée
 *  par catégorie. Mirror fonctionnel de renderLeaderboardHTML pour le discret. */
function renderCategoricalLeaderboardHTML(entities, selectedId, ind, yearIdx) {
  const cmap = categoryColorMap(ind);
  const byCode = new Map(); // code → [entities]
  let nNoData = 0;
  for (const e of entities) {
    const code = getValueForYear(e.data, ind.key, yearIdx);
    if (code == null) {
      nNoData++;
      continue;
    }
    const k = String(code);
    if (!byCode.has(k)) byCode.set(k, []);
    byCode.get(k).push(e);
  }
  const totalWithData = entities.length - nNoData;
  if (totalWithData === 0) {
    return `<p class="leaderboard__empty">Aucune valeur disponible pour cet indicateur.</p>`;
  }

  // Ordre = ordre des catégories de l'indicateur, filtré aux présentes.
  const presentCats = ind.categories.filter((c) => byCode.has(String(c.code)));
  const pct = (n) =>
    ((100 * n) / totalWithData).toLocaleString("fr-FR", { maximumFractionDigits: 1 });

  const distrib = presentCats
    .map((c) => {
      const n = byCode.get(String(c.code)).length;
      const color = cmap.get(String(c.code));
      return `<li class="cat-distrib__row">
        <span class="cat-distrib__swatch" style="background:${color}"></span>
        <span class="cat-distrib__label">${escapeHtml(c.label)}</span>
        <span class="cat-distrib__bar"><span class="cat-distrib__bar-fill" style="width:${pct(n)}%;background:${color}"></span></span>
        <span class="cat-distrib__count">${n.toLocaleString("fr-FR")} · ${pct(n)} %</span>
      </li>`;
    })
    .join("");
  const noDataLine =
    nNoData > 0
      ? `<li class="cat-distrib__row cat-distrib__row--nodata">
           <span class="cat-distrib__swatch cat-distrib__swatch--nodata"></span>
           <span class="cat-distrib__label">Donnée non disponible</span>
           <span class="cat-distrib__bar"></span>
           <span class="cat-distrib__count">${nNoData.toLocaleString("fr-FR")}</span>
         </li>`
      : "";

  // Liste groupée cliquable seulement si l'effectif reste raisonnable
  // (drill-down région). En overview (~1335 EPCI), on s'en tient à la
  // répartition pour ne pas saturer le DOM (cf. seuil du leaderboard numérique).
  const LIST_THRESHOLD = 400;
  let groupedList;
  if (entities.length <= LIST_THRESHOLD) {
    const groups = presentCats
      .map((c) => {
        const color = cmap.get(String(c.code));
        const ents = byCode
          .get(String(c.code))
          .slice()
          .sort((a, b) => String(a.label).localeCompare(String(b.label), "fr"));
        const rows = ents
          .map((e) => {
            const isSel = e.id === selectedId;
            return `<li class="leaderboard__row${isSel ? " leaderboard__row--selected" : ""}"
                data-leaderboard-id="${escapeHtml(String(e.id))}" tabindex="0"
                aria-label="${escapeHtml(String(e.label ?? ""))} : ${escapeHtml(c.label)}">
              <span class="cat-distrib__swatch" style="background:${color}"></span>
              <span class="leaderboard__name">${escapeHtml(String(e.label ?? ""))}</span>
            </li>`;
          })
          .join("");
        return `<li class="cat-group">
          <div class="cat-group__head"><span class="cat-distrib__swatch" style="background:${color}"></span>${escapeHtml(c.label)} <span class="cat-group__n">(${ents.length})</span></div>
          <ul class="leaderboard cat-group__list">${rows}</ul>
        </li>`;
      })
      .join("");
    groupedList = `<ul class="cat-groups">${groups}</ul>`;
  } else {
    groupedList = `<p class="leaderboard__footnote">Cliquez sur une région (carte) pour explorer la liste détaillée des intercommunalités par catégorie.</p>`;
  }

  return `
    <div class="cat-distrib"><ul class="cat-distrib__rows">${distrib}${noDataLine}</ul></div>
    ${groupedList}
  `;
}

/** Renvoie le rang d'une entité (ou null si non classée).
 *  Helper pratique pour afficher "Rang : 5/13" à côté de l'indicateur en
 *  surbrillance dans le panneau de détails. */
function getEntityRank(ranked, entityId) {
  const found = ranked.find((r) => r.entity.id === entityId);
  return found ? { rank: found.rank, total: found.total } : null;
}

function renderDrillDownHeader() {
  const wrapper = $("#drilldown-header");
  const isCommunesDrill =
    state.currentLevel === "communes" && state.communesMode === "drilldown";
  const isEpciDrill =
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "drilldown";
  const isSyndDrill =
    state.currentLevel === "syndicats" && state.syndicatsMode === "drilldown";
  if (!isCommunesDrill && !isEpciDrill && !isSyndDrill) {
    wrapper.hidden = true;
    return;
  }
  wrapper.hidden = false;
  const title = $("#drilldown-title");
  if (isCommunesDrill) {
    const n = state.currentEntities.length;
    title.textContent = `${state.drillDownDepName} — ${n} communes`;
  } else if (isEpciDrill) {
    // Drilldown EPCI par région : on indique le nombre d'EPCIs présents
    // (et non de communes, qui sont juste l'outil cartographique).
    const nbEpcis = state.currentRegionEpciSirens
      ? state.currentRegionEpciSirens.size
      : 0;
    title.textContent =
      `${state.drillDownRegName} — ${nbEpcis} intercommunalité${nbEpcis > 1 ? "s" : ""}`;
  } else {
    // Drilldown syndicats : le nombre de syndicats filtrés sera mis à jour
    // par le panel après chargement. Pour le header on indique juste le dep.
    const codes = Array.isArray(state.drillDownSyndDepCodes)
      ? state.drillDownSyndDepCodes.join("/")
      : "";
    title.textContent = `${state.drillDownSyndDepName}${codes ? " (" + codes + ")" : ""}`;
  }
}

// ----------------------------------------------------------------------------
// Sélection
// ----------------------------------------------------------------------------

/** Retire la sélection actuelle (path + halos) du DOM. */
function clearSelectionHighlight() {
  if (_selectedPath) {
    _selectedPath.classList.remove("map__region--selected");
    _selectedPath = null;
  }
  // Sélection multi-paths (cas drill-down région EPCI : un EPCI = plusieurs
  // communes membres simultanément en surbrillance).
  for (const p of _selectedPathsMulti) {
    p.classList.remove("map__region--selected");
  }
  _selectedPathsMulti = [];
  for (const h of _selectionHalos) h.remove();
  _selectionHalos = [];
}

/** Applique la mise en évidence visuelle (halo magenta) au path donné :
 *  duplique un halo sous le path, puis remet le path au-dessus. */
function applySelectionHighlight(path) {
  // L'ordre d'insertion DOM = ordre de dessin SVG : le halo (appendChild en
  // premier) passe dessous, le path sélectionné repasse au-dessus.
  const halo = path.cloneNode(false);
  halo.removeAttribute("data-id");
  halo.removeAttribute("tabindex");
  halo.removeAttribute("role");
  // Le halo est un duplicata purement décoratif : on retire aussi le nom
  // accessible (sinon aria-label sur un path sans rôle interactif → violation
  // « aria-prohibited-attr », et doublon du nom de la région sélectionnée).
  halo.removeAttribute("aria-label");
  halo.setAttribute("class", "map__region--selection-halo halo-1");
  path.parentNode.appendChild(halo);
  _selectionHalos.push(halo);

  path.classList.add("map__region--selected");
  path.parentNode.appendChild(path);
  _selectedPath = path;
}

function selectEntity(id) {
  // Cas spéciaux : un clic sur le calque interactif en mode overview déclenche
  // un drill-down (pas une simple sélection).
  if (state.currentLevel === "communes" && state.communesMode === "overview") {
    enterDrillDown(id);
    return;
  }
  // En syndicats OVERVIEW uniquement, clic sur un département → drill-down.
  // En drilldown, les clics carto sur la périphérie d'un autre dpt sont
  // ignorés : il faut repasser par "Retour à la France" pour changer
  // de département (comportement aligné sur les modes communes /
  // intercommunalités, pas de raccourci direct).
  if (
    state.currentLevel === "syndicats" &&
    state.syndicatsMode === "overview"
  ) {
    const ent = state.currentEntityById.get(id);
    const depCodes = ent?.depDataCodes || null;
    if (!depCodes || depCodes.length === 0) {
      console.warn(`Pas de dep_code pour "${id}"`);
      return;
    }
    enterDrillDownSyndicats(id, depCodes);
    return;
  }
  // En syndicats DRILLDOWN, currentEntities = communes du dpt. Le clic
  // sur une commune ne sélectionne PAS la commune individuellement : on
  // remonte au SYNDICAT parent (analogue au comportement EPCI drilldown
  // qui sélectionne l'EPCI plutôt qu'une commune isolée). L'union des
  // communes membres du syndicat est highlightée d'un seul tenant.
  if (
    state.currentLevel === "syndicats" &&
    state.syndicatsMode === "drilldown"
  ) {
    const commune = state.currentEntityById.get(id);
    const siren = commune?.sirenSyndicat;
    if (!siren) {
      // Commune non couverte par un syndicat pour cette compétence : on
      // déselectionne tout pour éviter la confusion.
      state.selectedId = null;
      state.selectedSyndicatSiren = null;
      clearSelectionHighlight();
      renderPanel();
      return;
    }
    state.selectedId = siren;
    state.selectedSyndicatSiren = siren;
    clearSelectionHighlight();
    applySelectionHighlightForSyndicat(siren);
    renderPanel();
    return;
  }
  // En intercommunalites overview, `id` peut venir de deux sources :
  //   * clic sur une région dans la carte → id = code INSEE région (2-3 chars,
  //     ex. "11" Île-de-France, "93" Provence-Alpes-Côte d'Azur)
  //   * clic sur un EPCI dans le leaderboard → id = SIREN à 9 chiffres.
  // On distingue par la longueur (les deux espaces de codes ne se chevauchent
  // jamais). Dans le cas EPCI, on ouvre la RÉGION PRINCIPALE de cet EPCI
  // (cohérent avec le fait qu'on n'a pas de vue dédiée par EPCI individuel).
  if (
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "overview"
  ) {
    if (id && String(id).length <= 3) {
      enterDrillDownEpciByRegion(id);
    } else if (state.epciBySiren) {
      const epci = state.epciBySiren.get(id);
      const regCode = epci?.data?.reg_code;
      if (regCode) enterDrillDownEpciByRegion(regCode);
    }
    return;
  }
  // En intercommunalites drilldown, un clic carto vient du calque des
  // communes (les paths SVG). L'utilisateur veut sélectionner l'EPCI, pas
  // la commune — on remonte au SIREN stocké sur l'entité commune.
  //
  // Priorité au sirenEpt si présent (communes Paris+PC) : c'est le niveau
  // intercommunal "opérationnel" pour ces communes (FPIC, fiscalité directe),
  // alors que le sirenEpci pointe sur la MGP qui n'a pas les mêmes
  // indicateurs. Pour les autres communes (sans sirenEpt), comportement
  // inchangé : on remonte au sirenEpci.
  if (
    state.currentLevel === "intercommunalites" &&
    state.intercommunalitesMode === "drilldown"
  ) {
    const commune = state.currentEntityById.get(id);
    const siren = commune?.sirenEpt || commune?.sirenEpci;
    if (!siren) return;
    state.selectedId = siren;
    clearSelectionHighlight();
    // Halo : si on a sélectionné l'EPT, on highlightight les communes
    // partageant ce sirenEpt ; sinon les communes partageant le sirenEpci.
    if (commune?.sirenEpt && siren === commune.sirenEpt) {
      _applySelectionHighlightForUnion((ent) => ent.sirenEpt === siren);
    } else {
      applySelectionHighlightForEpci(siren);
    }
    renderPanel();
    return;
  }

  state.selectedId = id;
  clearSelectionHighlight();

  if (id) {
    const next = state.pathById.get(id);
    if (next) applySelectionHighlight(next);
  }
  renderPanel();
}

function setupMapDelegation() {
  const g = $("#map__regions");

  g.addEventListener("click", (ev) => {
    const target = ev.target.closest(".map__region");
    if (target && target.dataset.id) {
      selectEntity(target.dataset.id);
    }
  });

  $("#map").addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const target = ev.target.closest(".map__region");
    if (target && target.dataset.id) {
      ev.preventDefault();
      selectEntity(target.dataset.id);
    }
  });

  // Le calque décoratif (#map__decorative) reste passif côté clic : en
  // intercommunalites overview, c'est le calque interactif #map__regions
  // qui porte les ~18 polygones de régions cliquables (cf. setupMapDelegation
  // ci-dessus). En intercommunalites drilldown, l'utilisateur clique sur les
  // communes via #map__regions (rendu par renderMap), qui sont ensuite
  // remontées à leur EPCI via selectEntity.
}

/** Met en évidence une UNION de plusieurs communes simultanément (halo
 *  unique autour de leur silhouette agrégée).
 *
 *  Technique : SVG filter avec feMorphology. On crée un `<path>` composite
 *  contenant la concaténation des `d` de toutes les communes membres, on
 *  applique un fill opaque, et on lui attache un filtre `epci-union-halo-filter`
 *  qui :
 *   1. dilate l'alpha (la silhouette rasterizée de l'union) par N pixels
 *   2. soustrait l'alpha original pour ne garder que le « ring » extérieur
 *   3. colore ce ring
 *  Le filtre opère sur l'alpha rendu → AUCUNE seam interne entre communes
 *  voisines (qu'elles soient du même groupe ou non). Le composite est rendu
 *  APRÈS toutes les communes, donc le halo s'affiche par-dessus les communes
 *  voisines tout en laissant les communes du groupe visibles à l'intérieur
 *  (le filtre n'émet rien à l'intérieur de l'union).
 *
 *  Utilisé pour EPCI drilldown (`ent.sirenEpci`) et syndicats drilldown
 *  (`ent.sirenSyndicat`). Le matcher passé en paramètre détermine quelles
 *  entités font partie de l'union. */
function _applySelectionHighlightForUnion(matchFn) {
  const memberPaths = [];
  for (const ent of state.currentEntities) {
    if (!matchFn(ent)) continue;
    const p = state.pathById.get(ent.id);
    if (p) memberPaths.push(p);
  }
  if (memberPaths.length === 0) return;

  const compositeD = memberPaths.map((p) => p.getAttribute("d")).join(" ");

  // Conversion px écran -> user units viewBox pour les rayons du filtre.
  const mapEl = $("#map");
  const vbWidth = mapEl.viewBox.baseVal.width || 800;
  const viewportPx = mapEl.clientWidth || 800;
  const uuPerPx = vbWidth / viewportPx;

  // "Anneaux" du halo (de l'EXTÉRIEUR vers l'INTÉRIEUR), reproduisant le halo
  // single-path CSS : magenta vif large + contour blanc fin. Côté extérieur
  // seulement, car le filtre ne produit rien à l'intérieur de l'union.
  const rings = [
    { px: 4.5, color: "#ff1744" },
    { px: 1.5, color: "white" },
  ];

  // (Re)construit le contenu du filtre <defs>
  const filter = $("#epci-union-halo-filter");
  while (filter.firstChild) filter.removeChild(filter.firstChild);

  // Bornes du filtre : on utilise userSpaceOnUse + une marge pour englober
  // le halo le plus large + un peu plus. Padding très généreux pour ne pas
  // tronquer le halo aux bords du viewBox.
  const padding = 20 * uuPerPx; // 20px de marge en user units
  filter.setAttribute("x", String(mapEl.viewBox.baseVal.x - padding));
  filter.setAttribute("y", String(mapEl.viewBox.baseVal.y - padding));
  filter.setAttribute("width", String(vbWidth + 2 * padding));
  filter.setAttribute("height", String(mapEl.viewBox.baseVal.height + 2 * padding));

  const SVGNS = "http://www.w3.org/2000/svg";
  const fe = (tag, attrs) => {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    return el;
  };

  // Pour chaque anneau, on dilate l'alpha jusqu'à sa frontière extérieure.
  // Anneau i = (dilate par r_i) MINUS (dilate par r_{i+1}).
  // r_n+1 = 0 (= SourceAlpha) pour le dernier anneau (intérieur).
  // Les rayons sont CUMULATIFS depuis l'extérieur : r_outer = sum des
  // largeurs jusqu'ici.
  let cumul = 0;
  const cumulRadii = [];
  // On parcourt de l'intérieur vers l'extérieur pour cumuler
  for (let i = rings.length - 1; i >= 0; i--) {
    cumul += rings[i].px;
    cumulRadii.unshift(cumul); // index 0 = outer cumulé, dernier = inner
  }

  // Génère les feMorphology pour chaque frontière (cumulRadii + 0)
  for (let i = 0; i < cumulRadii.length; i++) {
    filter.appendChild(fe("feMorphology", {
      operator: "dilate",
      radius: cumulRadii[i] * uuPerPx,
      in: "SourceAlpha",
      result: `dilated-${i}`,
    }));
  }

  // Pour chaque anneau : (dilated-i) MINUS (dilated-{i+1} OR SourceAlpha)
  const mergeChildren = [];
  for (let i = 0; i < rings.length; i++) {
    const outerKey = `dilated-${i}`;
    const innerKey = i + 1 < cumulRadii.length ? `dilated-${i + 1}` : "SourceAlpha";
    filter.appendChild(fe("feComposite", {
      operator: "out",
      in: outerKey,
      in2: innerKey,
      result: `ring-shape-${i}`,
    }));
    filter.appendChild(fe("feFlood", {
      "flood-color": rings[i].color,
      result: `flood-${i}`,
    }));
    filter.appendChild(fe("feComposite", {
      operator: "in",
      in: `flood-${i}`,
      in2: `ring-shape-${i}`,
      result: `ring-${i}`,
    }));
    mergeChildren.push(`ring-${i}`);
  }

  // Merge : l'ordre détermine la superposition. Anneau extérieur en bas,
  // intérieur en haut (donc le contour interne couvre la jonction).
  const merge = fe("feMerge", {});
  for (const r of mergeChildren) {
    merge.appendChild(fe("feMergeNode", { in: r }));
  }
  filter.appendChild(merge);

  // Composite path : fill opaque pour fournir l'alpha au filtre ; le résultat
  // du filtre REMPLACE le rendu (donc le noir ne s'affiche pas en tant que tel).
  const halo = document.createElementNS(SVGNS, "path");
  halo.setAttribute("d", compositeD);
  halo.setAttribute("fill", "#000");
  halo.setAttribute("stroke", "none");
  halo.setAttribute("filter", "url(#epci-union-halo-filter)");
  halo.setAttribute("pointer-events", "none");
  halo.setAttribute("class", "map__region--epci-union-halo");

  // Append APRÈS toutes les communes pour être au-dessus en z-order. Le
  // filtre n'émet rien à l'intérieur de l'union (composite "out" contre
  // SourceAlpha) donc les fills des communes membres restent visibles.
  const parent = memberPaths[0].parentNode;
  parent.appendChild(halo);
  _selectionHalos.push(halo);
  _selectedPathsMulti = [];
}

/** Wrapper EPCI : halo autour de toutes les communes ayant `sirenEpci`
 *  égal à la valeur passée. */
function applySelectionHighlightForEpci(sirenEpci) {
  _applySelectionHighlightForUnion((ent) => ent.sirenEpci === sirenEpci);
}

/** Wrapper syndicat : halo autour de toutes les communes ayant
 *  `sirenSyndicat` égal à la valeur passée. Le champ est injecté dans
 *  les entités à l'entrée du drill-down syndicats (cf. enterDrillDownSyndicats). */
function applySelectionHighlightForSyndicat(sirenSyndicat) {
  _applySelectionHighlightForUnion((ent) => ent.sirenSyndicat === sirenSyndicat);
}

/** Délégation des clics et focus sur les lignes du leaderboard. Le contenu
 *  du panel est ré-écrit à chaque renderPanel(), mais l'élément racine
 *  `#info-panel` persiste — donc un seul listener suffit pour tout le cycle
 *  de vie de l'app. */
function setupPanelDelegation() {
  const panel = $("#info-panel");

  const handleActivation = (row) => {
    if (row.dataset.leaderboardNational === "1") {
      // Cas du leaderboard NATIONAL des communes : l'id est un index dans
      // `state.decorativeEntities`. On déclenche le drill-down du département
      // correspondant, puis on présélectionne la commune cliquée par son
      // code INSEE pour atterrir directement sur ses indicateurs détaillés.
      const idx = Number(row.dataset.leaderboardId);
      const ent =
        state.decorativeEntities && state.decorativeEntities[idx];
      if (ent && ent.depName) {
        const targetInsee = ent.insee;
        enterDrillDown(ent.depName).then(() => {
          if (!targetInsee) return;
          // Une fois la drill-down chargée, `currentEntities` contient les
          // communes du département. On y retrouve celle qu'on cherche par
          // son code INSEE (champ `data.insee`).
          const match = state.currentEntities.find(
            (e) => e.data && e.data.insee === targetInsee,
          );
          if (match) selectEntity(match.id);
        });
      }
    } else if (row.dataset.leaderboardSyndicat === "1") {
      // Leaderboard syndicats : l'id est le SIREN du syndicat. On le
      // sélectionne et on re-rend le panel qui affichera le détail. En
      // drilldown, on highlight aussi l'union des communes membres sur
      // la carte (analogue à un clic carto sur l'une de ces communes).
      const siren = row.dataset.leaderboardId;
      state.selectedSyndicatSiren = siren;
      if (state.syndicatsMode === "drilldown") {
        state.selectedId = siren;
        clearSelectionHighlight();
        applySelectionHighlightForSyndicat(siren);
      }
      renderPanel();
    } else {
      // Cas standard : l'id correspond à une entité du niveau courant.
      selectEntity(row.dataset.leaderboardId);
    }
    // Faire défiler la carte / le panel en haut pour voir le résultat
    // (sur mobile, le panel passe sous la carte).
    if (window.matchMedia("(max-width: 720px)").matches) {
      $("#map").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  panel.addEventListener("click", (ev) => {
    const row = ev.target.closest("[data-leaderboard-id]");
    if (!row) return;
    handleActivation(row);
  });

  panel.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const row = ev.target.closest("[data-leaderboard-id]");
    if (!row) return;
    ev.preventDefault();
    handleActivation(row);
  });

  // Cliquer une ligne d'indicateur dans le panneau « promeut » cet indicateur
  // en indicateur courant (via le <select> natif → change), ce qui redessine
  // la carte, la légende ET la grande courbe d'évolution en tête de panneau.
  const activateIndicatorRow = (row) => {
    const key = row.dataset.indicatorKey;
    if (!key) return;
    selectIndicatorFromCombo(key);
    // Remonter en douceur juste sous le bloc de contrôles pour voir la
    // nouvelle courbe / carte, sans bondir tout en haut de la page.
    scrollBelowControls();
  };
  panel.addEventListener("click", (ev) => {
    const row = ev.target.closest("[data-indicator-key]");
    if (!row) return;
    activateIndicatorRow(row);
  });
  panel.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const row = ev.target.closest("[data-indicator-key]");
    if (!row) return;
    ev.preventDefault();
    activateIndicatorRow(row);
  });
}

/** Défilement vertical doux avec une courbe ease-out (départ rapide, fin
 *  douce). Le `behavior: "smooth"` natif fait un ease-in-out ; on veut
 *  explicitement « monter vite puis ralentir », d'où cette animation maison.
 *  Respecte prefers-reduced-motion (saut instantané). */
function smoothScrollToY(targetY, duration = 480) {
  targetY = Math.max(0, targetY);
  const startY = window.scrollY;
  const dist = targetY - startY;
  if (Math.abs(dist) < 2) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    window.scrollTo(0, targetY);
    return;
  }
  const start = performance.now();
  const easeOut = (t) => 1 - Math.pow(1 - t, 3); // cubique : rapide puis lent
  function step(now) {
    const t = Math.min(1, (now - start) / duration);
    window.scrollTo(0, startY + dist * easeOut(t));
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

/** Remonte juste sous le bloc de contrôles (haut de la zone carte/panneau),
 *  sans aller jusqu'en haut de la page. Appelé après avoir promu un
 *  indicateur depuis la liste du panneau. Le défilement est différé d'un
 *  frame : le panneau vient d'être reconstruit (renderPanel), et mesurer/
 *  défiler avant que la mise en page soit stable laisse l'ancrage de
 *  défilement du navigateur fausser la position. */
function scrollBelowControls() {
  // setTimeout(0) plutôt que requestAnimationFrame : le panneau vient d'être
  // reconstruit (renderPanel, synchrone) ; on diffère d'un tick pour mesurer
  // sur une mise en page stable. (rAF serait suspendu si l'onglet n'est pas
  // au premier plan, ce qui empêcherait le défilement.)
  setTimeout(() => {
    const target = document.querySelector(".map-area");
    if (!target) return;
    const y = window.scrollY + target.getBoundingClientRect().top - 8;
    smoothScrollToY(y);
  }, 0);
}

// ----------------------------------------------------------------------------
// Drill-down communes
// ----------------------------------------------------------------------------

// ============================================================================
// Drill-down — primitives communes
// ----------------------------------------------------------------------------
// Les 3 niveaux drill-downables (communes, intercommunalités, syndicats)
// partagent exactement la même séquence d'entrée/sortie :
//   - entrée : remplacer currentEntities, masquer le décoratif (display:none
//     pour préserver le DOM), zoomer le viewBox sur une bbox avec marge
//     standard 8% / 4 unités, re-render tout.
//   - sortie : restaurer currentEntities overview, viewBox France entière,
//     ré-afficher le décoratif (instantané si déjà rendu).
//
// Les helpers ci-dessous factorisent cette mécanique. Chaque enter/exit
// spécifique ne s'occupe que de la PARTIE DONNÉES (load des bonnes entités,
// mise à jour des flags de mode propres au niveau) puis délègue le rendu
// commun.
// ============================================================================

/** Calcule le viewBox SVG zoomé sur une bbox avec marge standard.
 *  Marge : max(8 % relatif, 4 unités absolues) — garantit que les halos
 *  de sélection ne soient pas tronqués par le bord du viewBox, même dans
 *  les très petits départements/EPCIs. */
function _computeDrillDownViewBox(bbox) {
  const marginRel = 0.08;
  const marginAbs = 4;
  const w = bbox.x_max - bbox.x_min;
  const h = bbox.y_max - bbox.y_min;
  const mw = Math.max(w * marginRel, marginAbs);
  const mh = Math.max(h * marginRel, marginAbs);
  return `${bbox.x_min - mw} ${bbox.y_min - mh} ${w + 2 * mw} ${h + 2 * mh}`;
}

/** Affecte les entités du niveau courant + rebuild de l'index by-id.
 *  `byId` peut être passé tel quel pour réutiliser une Map pré-construite
 *  (cas EPCI overview où state.epciBySiren existe déjà). */
function _setCurrentEntities(entities, byId) {
  state.currentEntities = entities;
  if (byId instanceof Map) {
    state.currentEntityById = byId;
  } else {
    state.currentEntityById = new Map();
    for (const e of entities) state.currentEntityById.set(e.id, e);
  }
}

/** Phase commune à toute ENTRÉE de drill-down. Doit être appelée APRÈS
 *  avoir mis à jour les flags de mode spécifiques au niveau (ex:
 *  state.communesMode, state.drillDownDepName, …).
 *
 *  @param {object} args
 *  @param {Array}  args.entities  Entités à rendre dans #map__regions
 *  @param {object} args.bbox      {x_min, x_max, y_min, y_max} pour viewBox
 *  @param {Array=} args.years     Slider years (omettre pour ne pas toucher)
 *  @param {Map=}   args.byId      Map id→entité pré-construite (optionnel)
 */
function _applyDrillDownView({ entities, bbox, years, byId }) {
  state.selectedId = null;
  _setCurrentEntities(entities, byId);
  if (years) {
    setYears(years);
    syncYearSlider();
  }
  // Masque le décoratif (35k communes) sans détruire le DOM. Le retour
  // overview est ainsi instantané (pas de re-rendu des 35k paths).
  setDecorativeVisible(false);
  renderMap({ viewBox: _computeDrillDownViewBox(bbox) });
  renderLegend();
  renderPanel();
  renderDrillDownHeader();
}

/** Phase commune à toute SORTIE de drill-down. Doit être appelée APRÈS
 *  avoir réinitialisé les flags de mode spécifiques au niveau.
 *
 *  @param {object} args
 *  @param {Array}  args.entities  Entités overview (résultat d'un load*Overview)
 *  @param {Array=} args.years     Slider years
 *  @param {Map=}   args.byId      Map id→entité pré-construite (optionnel)
 */
function _applyOverviewView({ entities, years, byId }) {
  state.selectedId = null;
  _setCurrentEntities(entities, byId);
  if (years) {
    setYears(years);
    syncYearSlider();
  }
  renderMap({ viewBox: FRANCE_VIEWBOX });
  renderLegend();
  renderPanel();
  renderDrillDownHeader();
  // Restaure le décoratif : ré-affichage instantané si DOM préservé,
  // sinon déclenche un rendu. Re-sync les couleurs en cas de
  // changement d'indicateur pendant le drill-down.
  if (state.decorativePathById.size > 0) {
    setDecorativeVisible(true);
    applyDecorativeColors();
  } else {
    ensureDecorativeRendered();
  }
}

// ============================================================================
// Drill-down communes (par DÉPARTEMENT)
// ============================================================================

async function enterDrillDown(depName) {
  showLoader(`Chargement des communes du département…`);
  try {
    const { entities, bbox, years } = await loadCommunesForDepartement(depName);
    state.communesMode = "drilldown";
    state.drillDownDepName = depName;
    _applyDrillDownView({
      entities,
      bbox,
      years: years || ANNEES_COMMUNES_FALLBACK,
    });
  } catch (err) {
    console.error("Erreur drill-down", err);
    $("#info-panel").innerHTML = `
      <p class="panel__placeholder" style="color:#c00;">
        Impossible de charger les communes du département "${escapeHtml(depName)}".
      </p>
    `;
  } finally {
    hideLoader();
  }
}

async function exitDrillDown() {
  state.communesMode = "overview";
  state.drillDownDepName = null;
  const overview = await loadCommunesOverview();
  _applyOverviewView({
    entities: overview,
    years: state.decorativeYears || ANNEES_COMMUNES_FALLBACK,
  });
}

// ----------------------------------------------------------------------------
// Drill-down intercommunalités (par RÉGION)
// ----------------------------------------------------------------------------
//
// Architecture (décision UX) : dans le niveau Intercommunalités, on
// **n'expose jamais les chiffres des communes**. Le drill-down zoome sur
// une région et affiche les EPCIs présents dans cette région (chacun
// dessiné comme l'ensemble de ses communes membres, coloriées selon la
// valeur EPCI). Les EPCIs à cheval sur plusieurs régions sont rendus en
// entier — l'utilisateur voit donc parfois des communes « débordant »
// hors du contour régional, ce qui est explicite et voulu.

/** Drill-down sur une région : charge tous les EPCIs ayant ≥1 commune
 *  dans la région (cf. by-region/_index.json), zoome sur la bbox cumulée. */
async function enterDrillDownEpciByRegion(regCode) {
  showLoader("Chargement des intercommunalités de la région…");
  try {
    const { entities, bbox, years, regName, sirens } =
      await loadCommunesForRegion(regCode);
    state.intercommunalitesMode = "drilldown";
    state.drillDownRegCode = regCode;
    state.drillDownRegName = regName;
    state.currentRegionEpciSirens = sirens;
    // Le sélecteur d'indicateur reste sur les indicateurs EPCI : c'est
    // toujours la valeur de l'EPCI parent qu'on visualise, même si les
    // polygones affichés sont des communes.
    _applyDrillDownView({ entities, bbox, years });
  } catch (err) {
    console.error("Erreur drill-down région EPCI", err);
    $("#info-panel").innerHTML = `
      <p class="panel__placeholder" style="color:#c00;">
        Impossible de charger les intercommunalités de cette région.
      </p>
    `;
  } finally {
    hideLoader();
  }
}

/** Retour à la France entière depuis le drill-down région EPCI. */
async function exitDrillDownEpci() {
  state.intercommunalitesMode = "overview";
  state.drillDownRegCode = null;
  state.drillDownRegName = null;
  state.currentRegionEpciSirens = null;
  const epciEntities = await loadIntercommunalitesOverview();
  _applyOverviewView({
    entities: epciEntities,
    byId: state.epciBySiren, // Map pré-construite (siren → EPCI)
    years: state.epciYears || ANNEES_EPCI_FALLBACK,
  });
}

// ============================================================================
// Drill-down syndicats (par DÉPARTEMENT)
// ----------------------------------------------------------------------------
// Architecture alignée sur communes/EPCI :
//   1. On charge le dataset commune du département (data/communes/by-dep/{code}.json,
//      ~1 Mo, mémoïsé) pour obtenir les contours SVG individuels des
//      communes du dpt + leur INSEE.
//   2. On injecte dans chaque entité commune la valeur du syndicat qui la
//      couvre pour la compétence courante (lookup INSEE depuis
//      decorativeEntities, déjà hydraté par ensureDecorativeIndicatorLoaded).
//   3. On masque le décoratif (35k communes nationales) et on délègue à
//      _applyDrillDownView qui zoom le viewBox et rend la carte.
//
//   ⇒ Effet : on ne voit QUE les communes du département sélectionné,
//      coloriées par leur valeur syndicat, sans contour des dpts voisins.
//      Exactement le même comportement que le drill-down communes.
// ============================================================================

/** Construit les 2 maps d'enrichissement utilisées par le drill-down syndicats
 *  pour l'indicateur courant :
 *    - inseeToSyndValues : INSEE → { "Syndicats X — Y (€)": [serie multi-années] }
 *      (sous-ensemble des valeurs du décoratif filtré sur les clés syndicats)
 *    - inseeToSyndicatSiren : INSEE → SIREN du syndicat parent (depuis le
 *      payload leaderboard, qui liste les member_insees par syndicat)
 *
 *  Les deux dépendent de l'indicateur courant (la compétence) → à recalculer
 *  à chaque changement d'indicateur en drill-down syndicats. */
function _buildSyndicatsEnrichmentMaps(leaderboardPayload) {
  const inseeToSyndValues = new Map();
  for (const d of state.decorativeEntities || []) {
    if (!d.insee || !d.data?.values) continue;
    const synEntries = {};
    for (const [k, v] of Object.entries(d.data.values)) {
      if (k.startsWith("Syndicats ")) synEntries[k] = v;
    }
    if (Object.keys(synEntries).length) {
      inseeToSyndValues.set(d.insee, synEntries);
    }
  }
  const inseeToSyndicatSiren = new Map();
  if (leaderboardPayload && Array.isArray(leaderboardPayload.syndicats)) {
    // En cas de chevauchement (commune membre de plusieurs syndicats pour
    // la même compétence, ~5 % des cas), on garde le premier rencontré.
    // L'utilisateur peut accéder aux autres via le leaderboard.
    for (const s of leaderboardPayload.syndicats) {
      if (!s.siren || !Array.isArray(s.member_insees)) continue;
      for (const insee of s.member_insees) {
        if (!inseeToSyndicatSiren.has(insee)) {
          inseeToSyndicatSiren.set(insee, s.siren);
        }
      }
    }
  }
  return { inseeToSyndValues, inseeToSyndicatSiren };
}

/** Applique les maps d'enrichissement aux entités passées en argument.
 *  Si `inPlace` est true, modifie les entités existantes ; sinon retourne
 *  une nouvelle liste de copies shallow. */
function _applySyndicatsEnrichment(entities, maps, { inPlace = false } = {}) {
  const { inseeToSyndValues, inseeToSyndicatSiren } = maps;
  if (inPlace) {
    for (const e of entities) {
      const insee = e.data?.insee;
      e.sirenSyndicat = (insee && inseeToSyndicatSiren.get(insee)) || null;
      const syn = insee && inseeToSyndValues.get(insee);
      if (syn) {
        e.data = { ...e.data, values: { ...(e.data?.values || {}), ...syn } };
      }
    }
    return entities;
  }
  return entities.map((e) => {
    const insee = e.data?.insee;
    const syn = insee && inseeToSyndValues.get(insee);
    const sirenSyndicat = (insee && inseeToSyndicatSiren.get(insee)) || null;
    const next = { ...e, sirenSyndicat };
    if (syn) {
      next.data = { ...e.data, values: { ...(e.data?.values || {}), ...syn } };
    }
    return next;
  });
}

/** Re-hydratation des entités du drill-down syndicats sur changement
 *  d'indicateur : reload des valeurs et du leaderboard de la nouvelle
 *  compétence, puis ré-enrichissement IN PLACE. À appeler depuis le
 *  handler de changement d'indicateur quand on est en drilldown. */
async function _rehydrateSyndicatsDrillDown() {
  const ind = state.currentIndicator;
  if (!ind) return;
  const [, leaderboardPayload] = await Promise.all([
    ensureDecorativeIndicatorLoaded(ind.key),
    loadSyndicatsLeaderboardFile(ind.key),
  ]);
  const maps = _buildSyndicatsEnrichmentMaps(leaderboardPayload);
  _applySyndicatsEnrichment(state.currentEntities, maps, { inPlace: true });
  // Le currentEntityById pointe vers les mêmes objets → pas besoin de rebuild.
}

async function enterDrillDownSyndicats(depName, depCodes) {
  showLoader(`Chargement des communes du département…`);
  try {
    // En parallèle :
    //   a. dataset commune du dpt (mémoïsé via state.communesByDepCache)
    //   b. meta-communes-2024.json — INDISPENSABLE pour avoir l'insee sur
    //      chaque entité décorative (sinon les lookups ci-dessous échouent
    //      silencieusement et les communes apparaissent en gris)
    //   c. valeurs syndicat de l'indicateur courant (lazy sparse)
    //   d. leaderboard syndicats de l'indicateur courant — nous donne
    //      member_insees par syndicat (mapping commune → SIREN parent)
    const ind = state.currentIndicator;
    const [{ entities: rawCommunes, bbox, years }, , , leaderboardPayload] =
      await Promise.all([
        loadCommunesForDepartement(depName),
        loadCommunesMeta(),
        ind ? ensureDecorativeIndicatorLoaded(ind.key) : Promise.resolve(),
        ind ? loadSyndicatsLeaderboardFile(ind.key) : Promise.resolve(null),
      ]);

    const maps = _buildSyndicatsEnrichmentMaps(leaderboardPayload);
    const enriched = _applySyndicatsEnrichment(rawCommunes, maps);

    state.syndicatsMode = "drilldown";
    state.drillDownSyndDepCodes =
      Array.isArray(depCodes) ? depCodes.slice() : [depCodes];
    state.drillDownSyndDepName = depName;
    state.selectedSyndicatSiren = null;
    _applyDrillDownView({
      entities: enriched,
      bbox,
      years: years || ANNEES_COMMUNES_FALLBACK,
    });
  } catch (err) {
    console.error("Erreur drill-down syndicats", err);
    $("#info-panel").innerHTML = `
      <p class="panel__placeholder" style="color:#c00;">
        Impossible de charger le département "${escapeHtml(depName)}".
      </p>
    `;
  } finally {
    hideLoader();
  }
}

async function exitDrillDownSyndicats() {
  state.syndicatsMode = "overview";
  state.drillDownSyndDepCodes = null;
  state.drillDownSyndDepName = null;
  state.selectedSyndicatSiren = null;
  const overview = await loadCommunesOverview();
  _applyOverviewView({
    entities: overview,
    years: state.decorativeYears || ANNEES_COMMUNES_FALLBACK,
  });
}

// ----------------------------------------------------------------------------
// Sélecteur d'indicateur
// ----------------------------------------------------------------------------

function buildIndicatorSelector() {
  setupIndicatorCombobox();
  rebuildIndicatorOptions();
  $("#indicator-select").addEventListener("change", async (ev) => {
    const ind = INDICATORS.find((i) => i.key === ev.target.value);
    if (!ind) return;
    state.currentIndicator = ind;
    // En syndicats, le détail d'un syndicat ne survit pas au changement
    // d'indicateur (l'agrégat change, le syndicat peut ne pas être présent
    // dans le nouveau leaderboard).
    state.selectedSyndicatSiren = null;
    updateIndicatorHelp();
    updateIndicatorComboTrigger();

    // En drilldown syndicats, le mapping commune → SIREN syndicat dépend
    // de la compétence courante : on doit recharger le leaderboard
    // correspondant et ré-enrichir state.currentEntities AVANT applyColors
    // (sinon : couleurs absentes + clic carto fait référence à l'ancien
    // syndicat).
    if (
      state.currentLevel === "syndicats" &&
      state.syndicatsMode === "drilldown"
    ) {
      // Reset halo précédent (ancien syndicat sélectionné)
      state.selectedId = null;
      clearSelectionHighlight();
      await _rehydrateSyndicatsDrillDown();
    }

    applyColors();
    renderLegend();
    renderPanel();
  });
}

/** (Re)construit les options du sélecteur d'indicateur, regroupées en
 *  <optgroup> et filtrées par le niveau courant. À appeler à chaque
 *  changement de niveau. Préserve l'indicateur sélectionné s'il reste
 *  pertinent pour le nouveau niveau, sinon retombe sur le premier
 *  indicateur disponible. */
function rebuildIndicatorOptions() {
  const select = $("#indicator-select");
  // Le sélecteur d'indicateur suit toujours le niveau courant. En drill-down
  // EPCI région, on reste sur les indicateurs EPCI (jamais commune) :
  // les polygones affichés sont des communes mais représentent l'EPCI parent.
  const visible = getIndicatorsForLevel(state.currentLevel);

  // Vide
  while (select.firstChild) select.removeChild(select.firstChild);

  // Groupage
  const byGroup = new Map();
  for (const ind of visible) {
    if (!byGroup.has(ind.group)) byGroup.set(ind.group, []);
    byGroup.get(ind.group).push(ind);
  }

  // Insertion dans l'ordre canonique des groupes
  for (const groupName of INDICATOR_GROUP_ORDER) {
    const inds = byGroup.get(groupName);
    if (!inds || inds.length === 0) continue;
    const optgroup = document.createElement("optgroup");
    optgroup.label = groupName;
    for (const ind of inds) {
      const opt = document.createElement("option");
      opt.value = ind.key;
      opt.textContent = ind.label;
      optgroup.appendChild(opt);
    }
    select.appendChild(optgroup);
  }

  // Conserver la sélection si encore valide, sinon prendre la première
  const currentKey = state.currentIndicator?.key;
  const stillValid = visible.find((i) => i.key === currentKey);
  if (stillValid) {
    select.value = currentKey;
  } else if (visible.length > 0) {
    state.currentIndicator = visible[0];
    select.value = visible[0].key;
  }
  updateIndicatorHelp();

  // Miroir custom : reconstruire la liste du combobox + le libellé du trigger.
  rebuildIndicatorCombobox(visible, byGroup);
  updateIndicatorComboTrigger();
}

function updateIndicatorHelp() {
  // Ne réécrit le texte que s'il a réellement changé. Réassigner `textContent`
  // recrée le nœud texte, ce qui compte comme un NOUVEAU « largest contentful
  // paint » attribué à l'exécution de app.js (tardive sous 4G). Or #indicator-help
  // est l'élément LCP de la page et il est déjà pré-rempli dans le HTML statique
  // (cf. index.html) avec l'aide de l'indicateur par défaut. En évitant la
  // réécriture inutile au chargement initial, l'élément LCP reste peint au
  // premier rendu (FCP) au lieu d'être ré-attribué à app.js → LCP plus bas.
  const el = $("#indicator-help");
  const next = state.currentIndicator.help || "";
  if (el.textContent !== next) el.textContent = next;
}

// ----------------------------------------------------------------------------
// Combobox custom du sélecteur d'indicateur
//
// Le <select> natif tronque les libellés longs (compétences syndicats). On le
// masque et on superpose un combobox accessible : trigger + panneau avec
// recherche et options à retour à la ligne complet. Le <select> reste la
// source de vérité : sélectionner une option écrit `select.value` et déclenche
// son `change` → toute la logique existante (rebuild, applyColors, panel…)
// fonctionne sans modification.
// ----------------------------------------------------------------------------

/** Normalise pour la recherche : minuscules + sans accents. */
function _comboNorm(s) {
  return (s || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

/** Crée le DOM du combobox (une seule fois) et câble ses événements. */
function setupIndicatorCombobox() {
  const select = $("#indicator-select");
  if (!select || $("#indicator-combo")) return;
  select.classList.add("controls__select--native-hidden");

  const combo = document.createElement("div");
  combo.className = "combo";
  combo.id = "indicator-combo";
  combo.innerHTML = `
    <button type="button" class="combo__trigger" id="indicator-combo-trigger"
            aria-haspopup="listbox" aria-expanded="false" aria-label="Choisir l'indicateur affiché">
      <span class="combo__trigger-label" id="indicator-combo-label"></span>
      <span class="combo__caret" aria-hidden="true">▾</span>
    </button>
    <div class="combo__panel" id="indicator-combo-panel" hidden>
      <div class="combo__search-wrap">
        <input type="text" class="combo__search" id="indicator-combo-search"
               placeholder="Rechercher un indicateur…" autocomplete="off"
               aria-label="Rechercher un indicateur" />
      </div>
      <div class="combo__list" id="indicator-combo-list" role="listbox" tabindex="-1"></div>
    </div>`;
  select.insertAdjacentElement("afterend", combo);

  const trigger = $("#indicator-combo-trigger", combo);
  const panel = $("#indicator-combo-panel", combo);
  const search = $("#indicator-combo-search", combo);

  trigger.addEventListener("click", () => {
    if (panel.hidden) openIndicatorCombo();
    else closeIndicatorCombo();
  });
  search.addEventListener("input", () => filterIndicatorCombo(search.value));
  search.addEventListener("keydown", _comboKeydown);
  // Clic en dehors → fermer
  document.addEventListener("click", (e) => {
    if (!panel.hidden && !combo.contains(e.target)) closeIndicatorCombo();
  });
}

/** (Re)construit la liste du combobox depuis les indicateurs visibles, groupés
 *  dans l'ordre canonique (même logique que rebuildIndicatorOptions). */
function rebuildIndicatorCombobox(visible, byGroup) {
  const list = $("#indicator-combo-list");
  if (!list) return;
  list.innerHTML = "";
  const currentKey = state.currentIndicator?.key;
  for (const groupName of INDICATOR_GROUP_ORDER) {
    const inds = byGroup.get(groupName);
    if (!inds || inds.length === 0) continue;
    const groupEl = document.createElement("div");
    groupEl.className = "combo__group";
    const header = document.createElement("div");
    header.className = "combo__group-header";
    header.textContent = groupName;
    groupEl.appendChild(header);
    for (const ind of inds) {
      const opt = document.createElement("div");
      opt.className = "combo__option";
      opt.setAttribute("role", "option");
      opt.dataset.key = ind.key;
      opt.dataset.search = _comboNorm(ind.label + " " + groupName);
      opt.textContent = ind.label;
      if (ind.key === currentKey) opt.classList.add("combo__option--selected");
      opt.addEventListener("click", () => selectIndicatorFromCombo(ind.key));
      groupEl.appendChild(opt);
    }
    list.appendChild(groupEl);
  }
}

/** Filtre les options par sous-chaînes (insensible aux accents/casse). Masque
 *  les groupes devenus vides. */
function filterIndicatorCombo(query) {
  const list = $("#indicator-combo-list");
  if (!list) return;
  const terms = _comboNorm(query.trim()).split(/\s+/).filter(Boolean);
  let totalVisible = 0;
  for (const groupEl of list.querySelectorAll(".combo__group")) {
    let anyVisible = false;
    for (const opt of groupEl.querySelectorAll(".combo__option")) {
      const match = terms.every((t) => opt.dataset.search.includes(t));
      opt.hidden = !match;
      if (match) {
        anyVisible = true;
        totalVisible++;
      }
      opt.classList.remove("combo__option--active");
    }
    groupEl.hidden = !anyVisible;
  }
  // Message « aucun résultat »
  let empty = list.querySelector(".combo__empty");
  if (totalVisible === 0) {
    if (!empty) {
      empty = document.createElement("div");
      empty.className = "combo__empty";
      empty.textContent = "Aucun indicateur ne correspond.";
      list.appendChild(empty);
    }
    empty.hidden = false;
  } else if (empty) {
    empty.hidden = true;
  }
}

function openIndicatorCombo() {
  const panel = $("#indicator-combo-panel");
  const trigger = $("#indicator-combo-trigger");
  const search = $("#indicator-combo-search");
  if (!panel) return;
  panel.hidden = false;
  trigger.setAttribute("aria-expanded", "true");
  search.value = "";
  filterIndicatorCombo("");
  // Faire défiler jusqu'à l'option sélectionnée
  const sel = panel.querySelector(".combo__option--selected");
  if (sel) sel.scrollIntoView({ block: "center" });
  search.focus();
}

function closeIndicatorCombo() {
  const panel = $("#indicator-combo-panel");
  const trigger = $("#indicator-combo-trigger");
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  trigger.setAttribute("aria-expanded", "false");
}

/** Sélectionne une option : écrit dans le <select> natif et déclenche `change`
 *  (la logique existante prend le relais), puis ferme le panneau. */
function selectIndicatorFromCombo(key) {
  const select = $("#indicator-select");
  if (!select) return;
  select.value = key;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  closeIndicatorCombo();
  // preventScroll : sans ça, focus() fait bondir la page vers le trigger
  // (situé dans le bloc de contrôles). Le défilement voulu est géré ailleurs.
  $("#indicator-combo-trigger")?.focus({ preventScroll: true });
}

/** Met à jour le libellé du trigger + l'option marquée sélectionnée. */
function updateIndicatorComboTrigger() {
  const ind = state.currentIndicator;
  if (!ind) return;
  const label = $("#indicator-combo-label");
  if (label) label.textContent = ind.label || "";
  const list = $("#indicator-combo-list");
  if (list) {
    for (const o of list.querySelectorAll(".combo__option")) {
      o.classList.toggle("combo__option--selected", o.dataset.key === ind.key);
    }
  }
}

/** Clavier dans le champ de recherche : Échap ferme, Entrée valide l'option
 *  active (ou la 1re visible), flèches déplacent la surbrillance. */
function _comboKeydown(e) {
  const list = $("#indicator-combo-list");
  if (!list) return;
  if (e.key === "Escape") {
    closeIndicatorCombo();
    $("#indicator-combo-trigger")?.focus();
    return;
  }
  const visibleOpts = $$(".combo__option", list).filter((o) => !o.hidden);
  if (e.key === "Enter") {
    e.preventDefault();
    const target = list.querySelector(".combo__option--active") || visibleOpts[0];
    if (target) selectIndicatorFromCombo(target.dataset.key);
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!visibleOpts.length) return;
    let idx = visibleOpts.findIndex((o) => o.classList.contains("combo__option--active"));
    idx = e.key === "ArrowDown" ? idx + 1 : idx - 1;
    if (idx < 0) idx = 0;
    if (idx > visibleOpts.length - 1) idx = visibleOpts.length - 1;
    visibleOpts.forEach((o) => o.classList.remove("combo__option--active"));
    visibleOpts[idx].classList.add("combo__option--active");
    visibleOpts[idx].scrollIntoView({ block: "nearest" });
  }
}

// ----------------------------------------------------------------------------
// Sélecteur de niveau
// ----------------------------------------------------------------------------

function buildLevelSelector() {
  for (const tab of $$(".level-tab")) {
    if (tab.disabled) continue;
    tab.addEventListener("click", async () => {
      const level = tab.dataset.level;
      if (level === state.currentLevel) return;
      await switchLevel(level);
    });
  }
  syncLevelTabs();
}

function syncLevelTabs() {
  for (const tab of $$(".level-tab")) {
    const active = tab.dataset.level === state.currentLevel;
    tab.classList.toggle("level-tab--active", active);
    tab.setAttribute("aria-selected", String(active));
  }
}

async function switchLevel(level) {
  state.currentLevel = level;
  state.selectedId = null;
  state.communesMode = "overview";
  state.drillDownDepName = null;
  state.intercommunalitesMode = "overview";
  state.drillDownRegCode = null;
  state.drillDownRegName = null;
  state.currentRegionEpciSirens = null;
  state.syndicatsMode = "overview";
  state.drillDownSyndDepCodes = null;
  state.drillDownSyndDepName = null;
  state.selectedSyndicatSiren = null;
  syncLevelTabs();
  // Reconstruit le sélecteur d'indicateurs pour le niveau courant
  // (les indicateurs spécifiques varient selon le niveau).
  rebuildIndicatorOptions();

  // Pour les niveaux régions/départements, on libère le décoratif.
  // Pour communes, intercommunalites ET syndicats, on le réutilisera plus bas
  // (le niveau syndicats utilise les mêmes paths communes mais avec des
  // valeurs venant des syndicats agrégés par commune membre).
  const usesDecorative =
    level === "communes" ||
    level === "intercommunalites" ||
    level === "syndicats";
  const switchingDecoratifMode = usesDecorative && state.decorativePathById.size > 0;
  if (!usesDecorative) {
    clearDecorativeLayer();
  } else if (switchingDecoratifMode) {
    clearDecorativeLayer(); // forcera le re-rendu via ensureDecorativeRendered
  }

  // Affichage progressif aux niveaux communes/intercommunalites : on charge
  // **uniquement** les ~100 départements (léger, rapide), on rend la carte
  // interactive immédiatement, puis on déclenche en arrière-plan le chargement
  // des 35 000 communes décoratives. Pendant ce temps, l'utilisateur peut déjà
  // cliquer sur un département / un EPCI pour drill-down.
  showLoader("Chargement…");

  try {
    if (level === "communes") {
      // Pour les communes en mode overview, on affiche les contours
      // départements ; les "années" du niveau sont celles du décoratif
      // (2022-2024) qui seront chargées en arrière-plan.
      const entities = await loadCommunesOverview();
      state.currentEntities = entities;
      state.currentEntityById = new Map();
      for (const e of entities) state.currentEntityById.set(e.id, e);
      // En attendant que le décoratif soit chargé, on suppose la plage
      // habituelle 2022-2024.
      setYears(state.decorativeYears || ANNEES_COMMUNES_FALLBACK);
    } else if (level === "syndicats") {
      // Niveau Syndicats : utilise les paths communes (déjà chargés via
      // loadDecorativeCommunes) en arrière-plan. Les valeurs viennent des
      // syndicats agrégés par commune membre, via lazy fetch vers
      // data/syndicats/decoratif-values/{slug}.json (format sparse).
      // Pas d'entités principales propres : on traite les communes
      // décoratives comme support cartographique.
      const entities = await loadCommunesOverview();
      state.currentEntities = entities;
      state.currentEntityById = new Map();
      for (const e of entities) state.currentEntityById.set(e.id, e);
      setYears(state.decorativeYears || ANNEES_COMMUNES_FALLBACK);
    } else if (level === "intercommunalites") {
      // Pour les intercommunalités en mode overview :
      //   * `currentEntities` = les ~1335 EPCIs (pour le leaderboard et le panel).
      //   * Calque interactif #map__regions : on charge les ~18 contours de
      //     régions, transparents et cliquables, stockés à part dans
      //     `state.epciOverviewMapEntities`. C'est leur clic qui déclenche
      //     `enterDrillDownEpciByRegion`.
      //   * Calque décoratif #map__decorative : les 35k communes coloriées
      //     selon la valeur de leur EPCI parent (chargement async, cf.
      //     ensureDecorativeRendered).
      const epciEntities = await loadIntercommunalitesOverview();
      state.currentEntities = epciEntities;
      state.currentEntityById = state.epciBySiren;
      setYears(state.epciYears || ANNEES_EPCI_FALLBACK);
      // Charge en parallèle les régions (pour le calque interactif) et
      // l'index by-region (pour le drill-down). Très léger : ~17 polygones
      // SVG + ~30 Ko d'index.
      const [regionsLoaded] = await Promise.all([
        loadSimpleLevel("regions"),
        loadEpciByRegionIndex(),
      ]);
      state.epciOverviewMapEntities = regionsLoaded.entities;
    } else {
      const result = await loadSimpleLevel(level);
      state.currentEntities = result.entities;
      state.currentEntityById = new Map();
      for (const e of result.entities) state.currentEntityById.set(e.id, e);
      setYears(result.years);
    }
    syncYearSlider();
  } catch (err) {
    console.error("Erreur de chargement du niveau", level, err);
    hideLoader();
    $("#info-panel").innerHTML =
      `<p class="panel__placeholder" style="color:#c00;">Impossible de charger les données pour ce niveau.</p>`;
    return;
  }

  await new Promise((r) => setTimeout(r, 0));

  renderMap({ viewBox: FRANCE_VIEWBOX });
  renderLegend();
  renderPanel();
  renderDrillDownHeader();
  hideLoader();

  // Niveaux communes / intercommunalites / syndicats : on lance le chargement
  // et le rendu du calque décoratif (35 000 communes) en arrière-plan, sans
  // bloquer l'interface. Pour syndicats, les valeurs viennent des fichiers
  // sparse syndicats lazy-fetchés au moment de la coloration.
  if (level === "communes" || level === "intercommunalites" || level === "syndicats") {
    ensureDecorativeRendered();
  }
}

// ----------------------------------------------------------------------------
// Loader
// ----------------------------------------------------------------------------

function showLoader(message) {
  let loader = $("#map-loader");
  if (!loader) {
    loader = document.createElement("div");
    loader.id = "map-loader";
    loader.className = "map-loader";
    $(".map-area").appendChild(loader);
  }
  loader.textContent = message || "Chargement…";
  loader.hidden = false;
}

function hideLoader() {
  const loader = $("#map-loader");
  if (loader) loader.hidden = true;
}

// ----------------------------------------------------------------------------
// Bouton "Retour à la France" (mode drill-down)
// ----------------------------------------------------------------------------

function setupDrillDownBackButton() {
  $("#drilldown-back").addEventListener("click", () => {
    // On retourne à l'overview du niveau courant : communes,
    // intercommunalités OU syndicats selon le niveau actif.
    if (
      state.currentLevel === "intercommunalites" &&
      state.intercommunalitesMode === "drilldown"
    ) {
      exitDrillDownEpci();
    } else if (
      state.currentLevel === "syndicats" &&
      state.syndicatsMode === "drilldown"
    ) {
      exitDrillDownSyndicats();
    } else {
      exitDrillDown();
    }
  });
}

// ----------------------------------------------------------------------------
// Slider d'année
// ----------------------------------------------------------------------------

function setupYearSlider() {
  const slider = $("#year-slider");
  const output = $("#year-value");
  // Mise à jour live de la valeur affichée pendant le drag. Toute interaction
  // manuelle interrompt la lecture automatique.
  slider.addEventListener("input", () => {
    pauseYearPlay();
    const idx = Number(slider.value);
    if (state.years[idx] != null) {
      output.textContent = state.years[idx];
      _setCurrentYearTick(idx);
    }
  });
  // Mise à jour effective de la carte au relâchement (évite de recalculer
  // les quantiles à chaque pixel de drag).
  slider.addEventListener("change", () => {
    const idx = Number(slider.value);
    if (state.years[idx] != null) setYear(idx);
  });
}

/** Va à l'année d'index `idx` : état + curseur + crans + carte/légende/panneau.
 *  Point d'entrée commun au curseur (au relâchement) et à la lecture auto. */
function setYear(idx) {
  if (!state.years || state.years[idx] == null) return;
  state.currentYear = state.years[idx];
  state.currentYearIdx = idx;
  const slider = $("#year-slider");
  if (slider) slider.value = String(idx);
  const output = $("#year-value");
  if (output) output.textContent = state.years[idx];
  _setCurrentYearTick(idx);
  _updateYearPlayButtons();
  applyColors();
  renderLegend();
  renderPanel();
}

// ---- Lecture automatique de l'évolution (play / pause / stop) --------------
const YEAR_PLAY_INTERVAL = 900; // ms entre deux années
let _yearTimer = null;

function _yearIsPlaying() {
  return _yearTimer !== null;
}

/** Lance (ou reprend) la lecture : avance d'une année toutes les
 *  YEAR_PLAY_INTERVAL ms, EN BOUCLE — après la dernière année, repart de la
 *  première. On l'arrête avec Pause ou Stop. */
function startYearPlay() {
  if (_yearIsPlaying() || !state.years || state.years.length < 2) return;
  if (state.currentYearIdx >= state.years.length - 1) setYear(0);
  _yearTimer = setInterval(() => {
    const last = state.years.length - 1;
    setYear(state.currentYearIdx >= last ? 0 : state.currentYearIdx + 1);
  }, YEAR_PLAY_INTERVAL);
  _updateYearPlayButtons();
}

function pauseYearPlay() {
  if (_yearTimer !== null) {
    clearInterval(_yearTimer);
    _yearTimer = null;
  }
  _updateYearPlayButtons();
}

/** Stop = pause + retour à la première année. */
function stopYearPlay() {
  pauseYearPlay();
  setYear(0);
}

/** Active/désactive les boutons selon l'état (lecture, position, nb d'années). */
function _updateYearPlayButtons() {
  const n = (state.years || []).length;
  const playing = _yearIsPlaying();
  const play = $("#year-play");
  const pause = $("#year-pause");
  const stop = $("#year-stop");
  if (play) play.disabled = playing || n < 2;
  if (pause) pause.disabled = !playing;
  if (stop) stop.disabled = (!playing && state.currentYearIdx === 0) || n < 2;
}

function setupYearPlayback() {
  $("#year-play")?.addEventListener("click", startYearPlay);
  $("#year-pause")?.addEventListener("click", pauseYearPlay);
  $("#year-stop")?.addEventListener("click", stopYearPlay);
  _updateYearPlayButtons();
}

function setupScaleModeToggle() {
  const checkbox = $("#scale-mode-toggle");
  // Synchroniser l'UI avec l'état chargé depuis localStorage
  checkbox.checked = state.scaleMode === "global";
  checkbox.addEventListener("change", () => {
    state.scaleMode = checkbox.checked ? "global" : "yearly";
    saveScalePreference();
    applyColors();
    renderLegend();
  });
}

/** Synchronise le slider avec l'état courant (range et valeur).
 *  À appeler à chaque switch de niveau ou de drill-down (les années
 *  disponibles peuvent varier). */
function syncYearSlider() {
  // Un changement de niveau / drill-down interrompt la lecture en cours
  // (les années disponibles changent).
  pauseYearPlay();
  const slider = $("#year-slider");
  const output = $("#year-value");
  if (!state.years || state.years.length === 0) {
    slider.disabled = true;
    _updateYearPlayButtons();
    return;
  }
  slider.disabled = false;
  slider.min = "0";
  slider.max = String(state.years.length - 1);
  slider.value = String(state.currentYearIdx);
  output.textContent = state.years[state.currentYearIdx];
  renderYearTicks();
  _updateYearPlayButtons();
}

/** Dessine les crans + années sous le curseur. Une marque par année (donc par
 *  position du curseur) ; les libellés sont espacés (1 sur 2 au-delà de 9 ans)
 *  pour ne pas se chevaucher. Les positions tiennent compte de la largeur du
 *  pouce (--yt-thumb) pour s'aligner sur l'endroit où le curseur s'arrête. */
function renderYearTicks() {
  const el = $("#year-ticks");
  if (!el) return;
  const years = state.years || [];
  const n = years.length;
  if (n <= 1) {
    el.innerHTML = "";
    return;
  }
  const labelStep = n > 9 ? 2 : 1;
  let html = "";
  for (let i = 0; i < n; i++) {
    const pct = i / (n - 1);
    const labeled = i % labelStep === 0 || i === n - 1;
    const cur = i === state.currentYearIdx ? " year-tick--current" : "";
    html +=
      `<span class="year-tick${cur}" data-yidx="${i}" ` +
      `style="left: calc(var(--yt-thumb) / 2 + ${pct} * (100% - var(--yt-thumb)))">` +
      `<span class="year-tick__mark"></span>` +
      (labeled ? `<span class="year-tick__label">${years[i]}</span>` : "") +
      `</span>`;
  }
  el.innerHTML = html;
}

/** Met en évidence le cran de l'année courante (sans tout redessiner). */
function _setCurrentYearTick(idx) {
  const el = $("#year-ticks");
  if (!el) return;
  el.querySelector(".year-tick--current")?.classList.remove("year-tick--current");
  el.querySelector(`.year-tick[data-yidx="${idx}"]`)?.classList.add("year-tick--current");
}

// ----------------------------------------------------------------------------
// Initialisation
// ----------------------------------------------------------------------------

// ============================================================================
// Tiroir « Analyser » — graphiques d'analyse
// ----------------------------------------------------------------------------
// Surface dédiée (overlay) ouverte à la demande. Garde la carte propre :
// les graphiques d'analyse (comparaison, corrélation, distribution,
// décomposition) vivent ici, pas dispersés dans l'UI principale.
// Opère sur le NIVEAU + l'INDICATEUR + l'ANNÉE courants (figés tant que le
// tiroir est ouvert, puisque les contrôles sont derrière l'overlay).
// ============================================================================

const ANALYZE_MODES = [
  { key: "comparaison", label: "Comparaison" },
  { key: "correlation", label: "Corrélation" },
  { key: "distribution", label: "Distribution" },
  { key: "decomposition", label: "Décomposition" },
];
const ANALYZE_CMP_MAX = 8;
let _analyzeCtx = null;
let _analyzeKeydownHandler = null;
let _analyzeYInds = null;

/** Retire les diacritiques pour une recherche insensible aux accents. */
function _deburr(s) {
  return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/** Contexte d'analyse du niveau courant : entités (avec séries), années,
 *  index d'année. `ok:false` + `message` quand le niveau ne s'y prête pas
 *  (communes overview, syndicats) — message qui guide l'utilisateur. */
function getAnalysisContext() {
  const level = state.currentLevel;
  if (level === "regions" || level === "departements") {
    return {
      ok: true, level,
      entities: state.currentEntities || [],
      years: state.years,
      yearIdx: state.currentYearIdx,
      setLabel: level === "regions" ? "régions" : "départements",
    };
  }
  if (level === "intercommunalites") {
    const years = state.epciYears || ANNEES_EPCI_FALLBACK;
    let yearIdx = years.indexOf(state.currentYear);
    if (yearIdx < 0) yearIdx = years.length - 1;
    let entities, setLabel;
    if (
      state.intercommunalitesMode === "drilldown" &&
      state.currentRegionEpciSirens &&
      state.epciBySiren
    ) {
      entities = state.currentRegionEpciSirens
        .map((s) => state.epciBySiren.get(s))
        .filter(Boolean);
      setLabel = "intercommunalités de " + (state.drillDownRegName || "la région");
    } else {
      entities = state.epciBySiren ? [...state.epciBySiren.values()] : [];
      setLabel = "intercommunalités";
    }
    return { ok: true, level, entities, years, yearIdx, setLabel };
  }
  if (level === "communes") {
    if (state.communesMode === "drilldown") {
      return {
        ok: true, level,
        entities: state.currentEntities || [],
        years: state.years,
        yearIdx: state.currentYearIdx,
        setLabel: "communes de " + (state.drillDownDepName || "ce département"),
      };
    }
    return {
      ok: false, level, entities: [],
      message:
        "Zoomez sur un département (clic sur la carte) pour analyser ses communes une à une.",
    };
  }
  if (level === "syndicats") {
    return {
      ok: false, level, entities: [],
      message:
        "L'analyse comparée n'est pas disponible au niveau Syndicats (chiffres chargés à la demande, par compétence).",
    };
  }
  return { ok: false, level, entities: [], message: "Analyse non disponible pour ce niveau." };
}

function openAnalyzeDrawer() {
  if (!state.analyzeMode) state.analyzeMode = "comparaison";
  if (!state.analyzeSelection) state.analyzeSelection = new Set();
  // Réinitialiser la sélection de comparaison si on a changé de niveau depuis
  // la dernière ouverture (les ids ne sont plus valides).
  if (state._analyzeSelLevel !== state.currentLevel) {
    state.analyzeSelection.clear();
    state.analyzeYKey = null;
    state.analyzeDecompEntId = null;
    state._analyzeSelLevel = state.currentLevel;
  }
  const overlay = $("#analyze-overlay");
  if (!overlay) return;
  overlay.hidden = false;
  document.body.classList.add("analyze-open");
  renderAnalyzeTabs();
  renderAnalyzeBody();
  _analyzeKeydownHandler = (e) => {
    if (e.key === "Escape") closeAnalyzeDrawer();
  };
  document.addEventListener("keydown", _analyzeKeydownHandler);
  $("#analyze-overlay .analyze-close")?.focus();
}

function closeAnalyzeDrawer() {
  const overlay = $("#analyze-overlay");
  if (!overlay) return;
  overlay.hidden = true;
  document.body.classList.remove("analyze-open");
  if (_analyzeKeydownHandler) {
    document.removeEventListener("keydown", _analyzeKeydownHandler);
    _analyzeKeydownHandler = null;
  }
  $("#analyze-open")?.focus();
}

function renderAnalyzeTabs() {
  const el = $("#analyze-tabs");
  if (!el) return;
  el.innerHTML = ANALYZE_MODES.map(
    (m) =>
      `<button class="analyze-tab${m.key === state.analyzeMode ? " analyze-tab--active" : ""}" role="tab" data-analyze-mode="${m.key}" aria-selected="${m.key === state.analyzeMode}">${m.label}</button>`,
  ).join("");
}

function renderAnalyzeBody() {
  const el = $("#analyze-body");
  const ctxEl = $("#analyze-context");
  if (!el) return;
  const ctx = getAnalysisContext();
  _analyzeCtx = ctx;
  const ind = state.currentIndicator;
  if (ctxEl) {
    ctxEl.innerHTML = `Niveau : <strong>${escapeHtml(LEVELS[state.currentLevel]?.label || state.currentLevel)}</strong> · Indicateur : <strong>${escapeHtml(ind?.label || "")}</strong> · Année : <strong>${state.currentYear}</strong>`;
  }
  if (!ctx.ok) {
    el.innerHTML = `<p class="analyze-empty">${escapeHtml(ctx.message || "Analyse non disponible.")}</p>`;
    return;
  }
  // Élaguer la sélection de comparaison aux entités du contexte courant.
  if (state.analyzeSelection && state.analyzeSelection.size) {
    const ids = new Set(ctx.entities.map((e) => String(e.id)));
    for (const id of [...state.analyzeSelection]) {
      if (!ids.has(id)) state.analyzeSelection.delete(id);
    }
  }
  switch (state.analyzeMode) {
    case "comparaison":
      return renderAnalyzeComparaison(el, ctx);
    case "correlation":
      return renderAnalyzeCorrelation(el, ctx);
    case "distribution":
      return renderAnalyzeDistribution(el, ctx);
    case "decomposition":
      return renderAnalyzeDecomposition(el, ctx);
    default:
      el.innerHTML = "";
  }
}

// ----- Mode Comparaison -----------------------------------------------------

function renderAnalyzeComparaison(el, ctx) {
  const ind = state.currentIndicator;
  if (isCategoricalIndicator(ind)) {
    el.innerHTML = `<p class="analyze-empty">La comparaison temporelle ne s'applique pas à un indicateur catégoriel (« ${escapeHtml(ind.label)} »). Choisissez un indicateur chiffré dans le sélecteur.</p>`;
    return;
  }
  // Pré-sélection : l'entité actuellement sélectionnée sur la carte.
  if (state.analyzeSelection.size === 0 && state.selectedId != null) {
    const sid = String(state.selectedId);
    if (ctx.entities.some((e) => String(e.id) === sid)) state.analyzeSelection.add(sid);
  }
  el.innerHTML = `
    <div class="analyze-cmp">
      <div class="analyze-cmp__picker">
        <div class="analyze-cmp__search-row">
          <input type="search" id="analyze-cmp-search" class="analyze-cmp__search" placeholder="Rechercher un territoire…" autocomplete="off" />
          <button type="button" id="analyze-cmp-clear" class="analyze-cmp__clear">Tout effacer</button>
        </div>
        <p class="analyze-cmp__count" id="analyze-cmp-count"></p>
        <ul class="analyze-cmp__list" id="analyze-cmp-list"></ul>
      </div>
      <div class="analyze-cmp__chart" id="analyze-cmp-chart"></div>
    </div>
  `;
  _updateCmpList();
  _updateCmpChart();
}

/** Entités du contexte triées par valeur courante décroissante (comme le
 *  classement), suivies de celles sans valeur. */
function _cmpSortedEntities() {
  const ind = state.currentIndicator;
  const { ranked, unranked } = computeRanking(
    _analyzeCtx.entities,
    ind.key,
    _analyzeCtx.yearIdx,
  );
  return [...ranked.map((r) => r.entity), ...unranked.map((u) => u.entity)];
}

function _updateCmpList() {
  const listEl = $("#analyze-cmp-list");
  if (!listEl || !_analyzeCtx) return;
  const ind = state.currentIndicator;
  const q = _deburr(($("#analyze-cmp-search")?.value || "").trim().toLowerCase());
  const sel = state.analyzeSelection;
  const all = _cmpSortedEntities();
  const CAP = 200;
  const selectedEnts = all.filter((e) => sel.has(String(e.id)));
  let pool = all.filter((e) => !sel.has(String(e.id)));
  if (q) pool = pool.filter((e) => _deburr((e.label || "").toLowerCase()).includes(q));
  const shown = pool.slice(0, CAP);
  const hiddenCount = pool.length - shown.length;
  const rowFor = (e) => {
    const id = String(e.id);
    const checked = sel.has(id);
    const v = getValueForYear(e.data, ind.key, _analyzeCtx.yearIdx);
    const disabled = !checked && sel.size >= ANALYZE_CMP_MAX;
    return `<li class="analyze-cmp__row">
      <label class="analyze-cmp__opt${disabled ? " analyze-cmp__opt--disabled" : ""}">
        <input type="checkbox" data-cmp-id="${escapeHtml(id)}"${checked ? " checked" : ""}${disabled ? " disabled" : ""} />
        <span class="analyze-cmp__name">${escapeHtml(e.label || id)}</span>
        <span class="analyze-cmp__val">${v == null || Number.isNaN(v) ? "—" : formatValue(v, ind.unit)}</span>
      </label></li>`;
  };
  let html = "";
  if (selectedEnts.length) {
    html += selectedEnts.map(rowFor).join("");
    html += `<li class="analyze-cmp__sep" aria-hidden="true"></li>`;
  }
  html += shown.map(rowFor).join("");
  if (hiddenCount > 0) {
    html += `<li class="analyze-cmp__more">${hiddenCount.toLocaleString("fr-FR")} autre(s)… affinez la recherche.</li>`;
  }
  if (!selectedEnts.length && !shown.length) {
    html = `<li class="analyze-cmp__more">Aucun territoire ne correspond.</li>`;
  }
  listEl.innerHTML = html;
  const countEl = $("#analyze-cmp-count");
  if (countEl) {
    countEl.textContent = `${sel.size} / ${ANALYZE_CMP_MAX} territoire(s) — clic pour ajouter/retirer`;
  }
}

function _updateCmpChart() {
  const chartEl = $("#analyze-cmp-chart");
  if (!chartEl || !_analyzeCtx) return;
  const ind = state.currentIndicator;
  const ids = [...state.analyzeSelection];
  if (ids.length === 0) {
    chartEl.innerHTML = `<p class="analyze-empty">Cochez des territoires à gauche pour superposer leurs courbes de « ${escapeHtml(ind.label)} ».</p>`;
    return;
  }
  const byId = new Map(_analyzeCtx.entities.map((e) => [String(e.id), e]));
  const picked = ids.map((id) => byId.get(id)).filter(Boolean);
  if (picked.length === 0) {
    chartEl.innerHTML = `<p class="analyze-empty">Sélection vide.</p>`;
    return;
  }
  const first = picked[0];
  const extraSeries = picked.slice(1).map((e, i) => ({
    serie: e.data?.values?.[ind.key] || [],
    label: e.label || String(e.id),
    color: CHART_EXTRA_COLORS[(i + 1) % CHART_EXTRA_COLORS.length],
  }));
  chartEl.innerHTML = buildEvolutionChart(
    first.data?.values?.[ind.key] || [],
    _analyzeCtx.years,
    ind,
    {
      currentIdx: _analyzeCtx.yearIdx,
      primaryLabel: first.label || String(first.id),
      primaryColor: CHART_EXTRA_COLORS[0],
      extraSeries,
      W: 720, H: 380,
    },
  );
}

// ----- Mode Corrélation -----------------------------------------------------

/** Coefficient de corrélation de Pearson (null si non calculable). */
function _pearson(points) {
  const n = points.length;
  if (n < 2) return null;
  let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0;
  for (const p of points) {
    sx += p.x; sy += p.y; sxx += p.x * p.x; syy += p.y * p.y; sxy += p.x * p.y;
  }
  const cov = sxy - (sx * sy) / n;
  const vx = sxx - (sx * sx) / n;
  const vy = syy - (sy * sy) / n;
  if (vx <= 0 || vy <= 0) return null;
  return cov / Math.sqrt(vx * vy);
}

/** Nuage de points X vs Y (1 point = 1 territoire). Droite de régression +
 *  r de Pearson affichés comme CALCUL indicatif (hors données OFGL). */
function buildScatterChart(points, xInd, yInd) {
  if (!points || points.length < 2) {
    return `<p class="analyze-empty">Pas assez de territoires ont à la fois une valeur pour « ${escapeHtml(xInd.label)} » et « ${escapeHtml(yInd.label)} » en ${state.currentYear}.</p>`;
  }
  const W = 720, H = 440, mL = 70, mR = 18, mT = 16, mB = 54;
  const innerW = W - mL - mR, innerH = H - mT - mB;
  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  for (const p of points) {
    if (p.x < xMin) xMin = p.x;
    if (p.x > xMax) xMax = p.x;
    if (p.y < yMin) yMin = p.y;
    if (p.y > yMax) yMax = p.y;
  }
  const sx = _niceScale(xMin, xMax, 5), sy = _niceScale(yMin, yMax, 5);
  const xFor = (v) => mL + ((v - sx.min) / ((sx.max - sx.min) || 1)) * innerW;
  const yFor = (v) => mT + (1 - (v - sy.min) / ((sy.max - sy.min) || 1)) * innerH;

  let grid = "", xlab = "", ylab = "";
  for (const t of sx.ticks) {
    if (t < sx.min - 1e-9 || t > sx.max + 1e-9) continue;
    const x = xFor(t);
    grid += `<line x1="${x}" y1="${mT}" x2="${x}" y2="${mT + innerH}" stroke="#f0f0f0" stroke-width="1"/>`;
    xlab += `<text x="${x}" y="${mT + innerH + 14}" text-anchor="middle" font-size="8" fill="#777">${escapeHtml(formatAxisTick(t))}</text>`;
  }
  for (const t of sy.ticks) {
    if (t < sy.min - 1e-9 || t > sy.max + 1e-9) continue;
    const y = yFor(t);
    grid += `<line x1="${mL}" y1="${y}" x2="${mL + innerW}" y2="${y}" stroke="#f0f0f0" stroke-width="1"/>`;
    ylab += `<text x="${mL - 6}" y="${y}" text-anchor="end" dominant-baseline="middle" font-size="8" fill="#777">${escapeHtml(formatAxisTick(t))}</text>`;
  }
  const axes = `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${mT + innerH}" stroke="#bbb"/><line x1="${mL}" y1="${mT + innerH}" x2="${mL + innerW}" y2="${mT + innerH}" stroke="#bbb"/>`;

  const r = _pearson(points);
  let reg = "";
  if (r != null) {
    const n = points.length;
    let sX = 0, sY = 0, sXX = 0, sXY = 0;
    for (const p of points) { sX += p.x; sY += p.y; sXX += p.x * p.x; sXY += p.x * p.y; }
    const b = (sXY - (sX * sY) / n) / ((sXX - (sX * sX) / n) || 1);
    const a = (sY - b * sX) / n;
    reg = `<line x1="${xFor(sx.min)}" y1="${yFor(a + b * sx.min)}" x2="${xFor(sx.max)}" y2="${yFor(a + b * sx.max)}" stroke="#c0392b" stroke-width="1.3" stroke-dasharray="5 3" clip-path="url(#corrClip)"/>`;
  }

  let dots = "", selDot = "";
  for (const p of points) {
    const cx = xFor(p.x), cy = yFor(p.y);
    const title = `${p.label}\n${xInd.label} : ${formatValue(p.x, xInd.unit)}\n${yInd.label} : ${formatValue(p.y, yInd.unit)}`;
    if (p.selected) {
      selDot = `<circle cx="${cx}" cy="${cy}" r="5" style="fill:var(--accent)" stroke="#fff" stroke-width="1.5"><title>${escapeHtml(title)}</title></circle>`;
    } else {
      dots += `<circle cx="${cx}" cy="${cy}" r="2.6" fill="#2c7fb8" fill-opacity="0.55"><title>${escapeHtml(title)}</title></circle>`;
    }
  }

  const xTitle = `${xInd.label}${xInd.unit ? " (" + xInd.unit + ")" : ""}`;
  const yTitle = `${yInd.label}${yInd.unit ? " (" + yInd.unit + ")" : ""}`;
  const rTxt = r == null ? "non calculable" : r.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
  return `
    <p class="analyze-corr__readout">Corrélation (r de Pearson) : <strong>${rTxt}</strong> · ${points.length} territoires · <span class="analyze-corr__caveat">indicateur statistique calculé, hors données OFGL</span></p>
    <svg class="analyze-chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Nuage de points ${escapeHtml(xInd.label)} contre ${escapeHtml(yInd.label)}">
      <defs><clipPath id="corrClip"><rect x="${mL}" y="${mT}" width="${innerW}" height="${innerH}"/></clipPath></defs>
      ${grid}${axes}${reg}${dots}${selDot}${xlab}${ylab}
      <text x="${mL + innerW / 2}" y="${H - 6}" text-anchor="middle" font-size="9" fill="#555">${escapeHtml(xTitle)}</text>
      <text x="14" y="${mT + innerH / 2}" text-anchor="middle" font-size="9" fill="#555" transform="rotate(-90 14 ${mT + innerH / 2})">${escapeHtml(yTitle)}</text>
    </svg>
  `;
}

function renderAnalyzeCorrelation(el, ctx) {
  const xInd = state.currentIndicator;
  if (isCategoricalIndicator(xInd)) {
    el.innerHTML = `<p class="analyze-empty">La corrélation demande un indicateur chiffré en X. « ${escapeHtml(xInd.label)} » est catégoriel — choisissez un autre indicateur dans le sélecteur.</p>`;
    return;
  }
  const numeric = getIndicatorsForLevel(ctx.level).filter((i) => !isCategoricalIndicator(i));
  if (!state.analyzeYKey || state.analyzeYKey === xInd.key || !numeric.some((i) => i.key === state.analyzeYKey)) {
    const alt = numeric.find((i) => i.key !== xInd.key);
    state.analyzeYKey = alt ? alt.key : xInd.key;
  }
  _analyzeYInds = numeric;
  const yLabel = (numeric.find((i) => i.key === state.analyzeYKey) || {}).label || "—";
  el.innerHTML = `
    <div class="analyze-corr">
      <div class="analyze-corr__controls">
        <span class="analyze-corr__axis-label">Axe vertical (Y) :</span>
        <div class="analyze-combo" id="analyze-y-combo">
          <button type="button" id="analyze-y-trigger" class="analyze-combo__trigger" aria-haspopup="listbox" aria-expanded="false">
            <span id="analyze-y-label">${escapeHtml(yLabel)}</span>
            <span class="analyze-combo__caret" aria-hidden="true">▾</span>
          </button>
          <div class="analyze-combo__panel" id="analyze-y-panel" hidden>
            <input type="search" id="analyze-y-search" class="analyze-combo__search" placeholder="Rechercher un indicateur…" autocomplete="off" />
            <ul class="analyze-combo__list" id="analyze-y-list" role="listbox"></ul>
          </div>
        </div>
      </div>
      <p class="analyze-corr__hint">Axe horizontal (X) = indicateur courant : <strong>${escapeHtml(xInd.label)}</strong>. Un point = un territoire (${escapeHtml(ctx.setLabel)}) en ${state.currentYear}. Survolez un point pour le détail.</p>
      <div id="analyze-corr-chart"></div>
    </div>
  `;
  _buildYList("");
  _updateCorrChart();
}

/** Remplit la liste du combo Y (cap 200, recherche accent-insensible). */
function _buildYList(filter) {
  const listEl = $("#analyze-y-list");
  if (!listEl || !_analyzeYInds) return;
  const q = _deburr((filter || "").trim().toLowerCase());
  const xKey = state.currentIndicator?.key;
  let pool = _analyzeYInds.filter((i) => i.key !== xKey);
  if (q) pool = pool.filter((i) => _deburr(i.label.toLowerCase()).includes(q));
  const CAP = 200;
  const shown = pool.slice(0, CAP);
  let html = shown
    .map((i) => `<li class="analyze-combo__opt${i.key === state.analyzeYKey ? " analyze-combo__opt--sel" : ""}" role="option" data-y-key="${escapeHtml(i.key)}" aria-selected="${i.key === state.analyzeYKey}">${escapeHtml(i.label)}</li>`)
    .join("");
  if (pool.length > CAP) {
    html += `<li class="analyze-combo__more">${(pool.length - CAP).toLocaleString("fr-FR")} autre(s)… affinez la recherche.</li>`;
  }
  if (!shown.length) html = `<li class="analyze-combo__more">Aucun indicateur ne correspond.</li>`;
  listEl.innerHTML = html;
}

function _openYCombo() {
  const p = $("#analyze-y-panel");
  if (!p) return;
  p.hidden = false;
  $("#analyze-y-trigger")?.setAttribute("aria-expanded", "true");
  const s = $("#analyze-y-search");
  if (s) { s.value = ""; _buildYList(""); s.focus(); }
}
function _closeYCombo() {
  const p = $("#analyze-y-panel");
  if (!p || p.hidden) return;
  p.hidden = true;
  $("#analyze-y-trigger")?.setAttribute("aria-expanded", "false");
}
function _selectY(key) {
  state.analyzeYKey = key;
  const ind = _analyzeYInds?.find((i) => i.key === key);
  const lab = $("#analyze-y-label");
  if (lab) lab.textContent = ind?.label || key;
  _closeYCombo();
  _updateCorrChart();
}

function _updateCorrChart() {
  const host = $("#analyze-corr-chart");
  if (!host || !_analyzeCtx) return;
  const xInd = state.currentIndicator;
  const yInd = INDICATORS.find((i) => i.key === state.analyzeYKey);
  if (!yInd) {
    host.innerHTML = `<p class="analyze-empty">Indicateur Y introuvable.</p>`;
    return;
  }
  const pts = [];
  for (const e of _analyzeCtx.entities) {
    const xv = getValueForYear(e.data, xInd.key, _analyzeCtx.yearIdx);
    const yv = getValueForYear(e.data, yInd.key, _analyzeCtx.yearIdx);
    if (xv == null || Number.isNaN(xv) || yv == null || Number.isNaN(yv)) continue;
    pts.push({
      x: xv, y: yv,
      label: e.label || String(e.id),
      selected: String(e.id) === String(state.selectedId),
    });
  }
  host.innerHTML = buildScatterChart(pts, xInd, yInd);
}

// ----- Mode Distribution ----------------------------------------------------

/** Centile interpolé (p ∈ [0,100]) d'un tableau trié croissant. */
function _percentile(sortedAsc, p) {
  const n = sortedAsc.length;
  if (!n) return null;
  if (n === 1) return sortedAsc[0];
  const idx = (p / 100) * (n - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return sortedAsc[lo];
  return sortedAsc[lo] + (sortedAsc[hi] - sortedAsc[lo]) * (idx - lo);
}
function _mean(vals) {
  if (!vals.length) return null;
  let s = 0;
  for (const v of vals) s += v;
  return s / vals.length;
}

/** Histogramme des valeurs (année courante) + repères médiane/quartiles et
 *  position de l'entité sélectionnée. */
function buildHistogram(values, ind, selValue) {
  const vals = values.filter((v) => v != null && !Number.isNaN(v));
  if (vals.length < 2) {
    return `<p class="analyze-empty">Pas assez de valeurs pour « ${escapeHtml(ind.label)} » en ${state.currentYear}.</p>`;
  }
  const sorted = [...vals].sort((a, b) => a - b);
  const min = sorted[0], max = sorted[sorted.length - 1];
  const W = 720, H = 420, mL = 58, mR = 18, mT = 18, mB = 52;
  const innerW = W - mL - mR, innerH = H - mT - mB;
  const sc = _niceScale(min, max, 6);
  const dom0 = sc.min, domR = (sc.max - sc.min) || 1;
  const bins = Math.max(6, Math.min(20, Math.round(Math.sqrt(vals.length))));
  const counts = new Array(bins).fill(0);
  for (const v of vals) {
    let b = Math.floor(((v - dom0) / domR) * bins);
    if (b < 0) b = 0;
    if (b >= bins) b = bins - 1;
    counts[b]++;
  }
  let maxCount = 0;
  for (const c of counts) if (c > maxCount) maxCount = c;
  const cs = _niceScale(0, maxCount, 4);
  const xFor = (v) => mL + ((v - dom0) / domR) * innerW;
  const yFor = (c) => mT + (1 - (c - cs.min) / ((cs.max - cs.min) || 1)) * innerH;

  let grid = "", ylab = "", xlab = "";
  for (const t of cs.ticks) {
    if (t < cs.min - 1e-9 || t > cs.max + 1e-9) continue;
    const y = yFor(t);
    grid += `<line x1="${mL}" y1="${y}" x2="${mL + innerW}" y2="${y}" stroke="#f0f0f0"/>`;
    ylab += `<text x="${mL - 6}" y="${y}" text-anchor="end" dominant-baseline="middle" font-size="8" fill="#777">${escapeHtml(formatAxisTick(t))}</text>`;
  }
  for (const t of sc.ticks) {
    if (t < sc.min - 1e-9 || t > sc.max + 1e-9) continue;
    const x = xFor(t);
    xlab += `<text x="${x}" y="${mT + innerH + 14}" text-anchor="middle" font-size="8" fill="#777">${escapeHtml(formatAxisTick(t))}</text>`;
  }

  let bars = "";
  const bw = innerW / bins;
  for (let i = 0; i < bins; i++) {
    const c = counts[i];
    if (c <= 0) continue;
    const x = mL + i * bw;
    const y = yFor(c);
    const h = mT + innerH - y;
    const lo = dom0 + (i / bins) * domR, hi = dom0 + ((i + 1) / bins) * domR;
    const title = `${formatValue(lo, ind.unit)} – ${formatValue(hi, ind.unit)} : ${c} territoire(s)`;
    bars += `<rect x="${x + 0.5}" y="${y}" width="${Math.max(0, bw - 1)}" height="${h}" fill="#2c7fb8" fill-opacity="0.78"><title>${escapeHtml(title)}</title></rect>`;
  }

  const marker = (v, color, label, dash) =>
    v == null
      ? ""
      : `<line x1="${xFor(v)}" y1="${mT}" x2="${xFor(v)}" y2="${mT + innerH}" stroke="${color}" stroke-width="1.3"${dash ? ` stroke-dasharray="${dash}"` : ""}/><text x="${xFor(v)}" y="${mT - 4}" text-anchor="middle" font-size="7.5" fill="${color}">${label}</text>`;
  let markers = "";
  markers += marker(_percentile(sorted, 25), "#999", "Q1", "4 2");
  markers += marker(_percentile(sorted, 50), "#c0392b", "médiane", "");
  markers += marker(_percentile(sorted, 75), "#999", "Q3", "4 2");
  let selMark = "";
  if (selValue != null && !Number.isNaN(selValue) && selValue >= sc.min && selValue <= sc.max) {
    selMark = `<line x1="${xFor(selValue)}" y1="${mT}" x2="${xFor(selValue)}" y2="${mT + innerH}" stroke="var(--accent)" stroke-width="2"/><text x="${xFor(selValue)}" y="${mT + innerH + 26}" text-anchor="middle" font-size="8" fill="var(--accent)" font-weight="700">sélection</text>`;
  }
  const axes = `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${mT + innerH}" stroke="#bbb"/><line x1="${mL}" y1="${mT + innerH}" x2="${mL + innerW}" y2="${mT + innerH}" stroke="#bbb"/>`;
  return `<svg class="analyze-chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Distribution de ${escapeHtml(ind.label)}">
    ${grid}${axes}${bars}${markers}${selMark}${xlab}${ylab}
    <text x="${mL + innerW / 2}" y="${H - 4}" text-anchor="middle" font-size="9" fill="#555">${escapeHtml(ind.label + (ind.unit ? " (" + ind.unit + ")" : ""))}</text>
    <text x="12" y="${mT + innerH / 2}" text-anchor="middle" font-size="9" fill="#555" transform="rotate(-90 12 ${mT + innerH / 2})">Nombre de territoires</text>
  </svg>`;
}

function renderAnalyzeDistribution(el, ctx) {
  const ind = state.currentIndicator;
  if (isCategoricalIndicator(ind)) {
    el.innerHTML = `<p class="analyze-empty">La distribution s'applique à un indicateur chiffré. « ${escapeHtml(ind.label)} » est catégoriel.</p>`;
    return;
  }
  const vals = [];
  let selValue = null;
  for (const e of ctx.entities) {
    const v = getValueForYear(e.data, ind.key, ctx.yearIdx);
    if (v == null || Number.isNaN(v)) continue;
    vals.push(v);
    if (String(e.id) === String(state.selectedId)) selValue = v;
  }
  if (vals.length < 2) {
    el.innerHTML = `<p class="analyze-empty">Pas assez de territoires ont une valeur pour « ${escapeHtml(ind.label)} » en ${state.currentYear}.</p>`;
    return;
  }
  const sorted = [...vals].sort((a, b) => a - b);
  const stat = (p) => escapeHtml(formatValue(_percentile(sorted, p), ind.unit));
  const meanTxt = escapeHtml(formatValue(_mean(vals), ind.unit));
  el.innerHTML = `
    <p class="analyze-corr__hint">Répartition de <strong>${escapeHtml(ind.label)}</strong> sur ${vals.length} ${escapeHtml(ctx.setLabel)} en ${state.currentYear}.${selValue != null ? " La barre verticale bleue marque le territoire sélectionné." : ""}</p>
    <p class="analyze-corr__readout">min ${stat(0)} · Q1 ${stat(25)} · <strong>médiane ${stat(50)}</strong> · moyenne ${meanTxt} · Q3 ${stat(75)} · max ${stat(100)}</p>
    ${buildHistogram(vals, ind, selValue)}
  `;
}

// ----- Mode Décomposition (curée, additive) ---------------------------------

// Décompositions PRÉ-DÉFINIES : composantes publiées par l'OFGL qui constituent
// un total. On empile les composantes ET on superpose la LIGNE du total publié :
// si la somme des composantes ne couvre pas tout le total, l'écart (résidu)
// reste visible — zéro agrégation inventée, fidélité respectée.
const DECOMP_PALETTE = ["#2c7fb8", "#7fcdbb", "#d2691e", "#6a51a3", "#41ab5d", "#d6336c", "#c9b458"];
const DECOMP_BREAKDOWNS = [
  {
    id: "recettes",
    label: "Recettes totales = fonctionnement + investissement",
    parent: "Recettes totales",
    comps: ["Recettes de fonctionnement", "Recettes d'investissement"],
    exact: true,
  },
  {
    id: "depenses",
    label: "Dépenses totales = fonctionnement + investissement",
    parent: "Dépenses totales",
    comps: ["Dépenses de fonctionnement", "Dépenses d'investissement"],
    exact: true,
  },
  {
    id: "fonct_nature",
    label: "Dépenses de fonctionnement, par nature",
    parent: "Dépenses de fonctionnement",
    comps: ["Frais de personnel", "Achats et charges externes", "Dépenses d'intervention", "Charges financières"],
    exact: false,
  },
];

/** Barres empilées des composantes + ligne du total publié.
 *  comps : [{ label, color, serie }] ; parentSerie aligné sur years. */
function buildStackedChart(years, comps, parentSerie, parentLabel, unit, currentIdx) {
  const n = years.length;
  let maxV = 0;
  const stacks = [];
  for (let i = 0; i < n; i++) {
    let acc = 0;
    const segs = [];
    for (const c of comps) {
      const v = c.serie[i];
      if (v == null || Number.isNaN(v) || v <= 0) continue;
      segs.push({ label: c.label, color: c.color, v, from: acc, to: acc + v });
      acc += v;
    }
    const pv = parentSerie[i];
    const top = Math.max(acc, pv != null && !Number.isNaN(pv) ? pv : 0);
    if (top > maxV) maxV = top;
    stacks.push({ segs, total: acc, parent: pv });
  }
  if (maxV <= 0) {
    return `<p class="analyze-empty">Pas de données chiffrées pour cette décomposition sur la période.</p>`;
  }
  const W = 720, H = 420, mL = 60, mR = 16, mT = 16, mB = 42;
  const innerW = W - mL - mR, innerH = H - mT - mB;
  const cs = _niceScale(0, maxV, 5);
  const yFor = (v) => mT + (1 - (v - cs.min) / ((cs.max - cs.min) || 1)) * innerH;
  const slotW = innerW / n;
  const barW = Math.min(40, slotW * 0.62);
  const xCenter = (i) => mL + slotW * (i + 0.5);

  let grid = "", ylab = "";
  for (const t of cs.ticks) {
    if (t < cs.min - 1e-9 || t > cs.max + 1e-9) continue;
    const y = yFor(t);
    grid += `<line x1="${mL}" y1="${y}" x2="${mL + innerW}" y2="${y}" stroke="#f0f0f0"/>`;
    ylab += `<text x="${mL - 6}" y="${y}" text-anchor="end" dominant-baseline="middle" font-size="8" fill="#777">${escapeHtml(formatAxisTick(t))}</text>`;
  }

  let bars = "";
  for (let i = 0; i < n; i++) {
    const x = xCenter(i) - barW / 2;
    for (const s of stacks[i].segs) {
      const y = yFor(s.to), h = yFor(s.from) - yFor(s.to);
      bars += `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(0, h)}" fill="${s.color}"><title>${escapeHtml(`${years[i]} · ${s.label} : ${formatValue(s.v, unit)}`)}</title></rect>`;
    }
  }

  let pPath = "", pDots = "", move = true;
  for (let i = 0; i < n; i++) {
    const pv = stacks[i].parent;
    if (pv == null || Number.isNaN(pv)) { move = true; continue; }
    const x = xCenter(i), y = yFor(pv);
    pPath += (move ? "M" : "L") + x + "," + y + " ";
    move = false;
    pDots += `<circle cx="${x}" cy="${y}" r="2.6" fill="#222"><title>${escapeHtml(`${years[i]} · ${parentLabel} (total publié) : ${formatValue(pv, unit)}`)}</title></circle>`;
  }
  const parentLine = pPath ? `<path d="${pPath.trim()}" fill="none" stroke="#222" stroke-width="1.6" stroke-dasharray="1 0"/>` : "";

  const labelEvery = n <= 9 ? 1 : 2;
  let xlab = "";
  for (let i = 0; i < n; i++) {
    if (!(i % labelEvery === 0 || i === n - 1)) continue;
    const isCur = i === currentIdx;
    xlab += `<text x="${xCenter(i)}" y="${mT + innerH + 14}" text-anchor="middle" font-size="8" fill="${isCur ? "var(--accent)" : "#777"}" font-weight="${isCur ? "700" : "400"}">${years[i]}</text>`;
  }
  const axes = `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${mT + innerH}" stroke="#bbb"/><line x1="${mL}" y1="${mT + innerH}" x2="${mL + innerW}" y2="${mT + innerH}" stroke="#bbb"/>`;
  return `<svg class="analyze-chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Décomposition empilée">
    ${grid}${axes}${bars}${parentLine}${pDots}${xlab}${ylab}
  </svg>`;
}

function renderAnalyzeDecomposition(el, ctx) {
  // Décompositions disponibles au niveau courant (toutes leurs clés existent).
  const levelKeys = new Set(getIndicatorsForLevel(ctx.level).map((i) => i.key));
  const avail = DECOMP_BREAKDOWNS.filter(
    (b) => levelKeys.has(b.parent) && b.comps.every((k) => levelKeys.has(k)),
  );
  if (avail.length === 0) {
    el.innerHTML = `<p class="analyze-empty">Aucune décomposition pré-définie n'est disponible pour ce niveau.</p>`;
    return;
  }
  if (!state.analyzeDecompId || !avail.some((b) => b.id === state.analyzeDecompId)) {
    state.analyzeDecompId = avail[0].id;
  }
  // Territoire : entité sélectionnée si présente, sinon 1re du contexte.
  const byId = new Map(ctx.entities.map((e) => [String(e.id), e]));
  if (!state.analyzeDecompEntId || !byId.has(state.analyzeDecompEntId)) {
    state.analyzeDecompEntId =
      state.selectedId != null && byId.has(String(state.selectedId))
        ? String(state.selectedId)
        : (ctx.entities[0] ? String(ctx.entities[0].id) : null);
  }
  const entsSorted = [...ctx.entities].sort((a, b) =>
    (a.label || "").localeCompare(b.label || "", "fr"),
  );
  const bOpts = avail
    .map((b) => `<option value="${b.id}"${b.id === state.analyzeDecompId ? " selected" : ""}>${escapeHtml(b.label)}</option>`)
    .join("");
  const eOpts = entsSorted
    .map((e) => `<option value="${escapeHtml(String(e.id))}"${String(e.id) === state.analyzeDecompEntId ? " selected" : ""}>${escapeHtml(e.label || String(e.id))}</option>`)
    .join("");
  el.innerHTML = `
    <div class="analyze-corr__controls">
      <label for="analyze-decomp-breakdown" class="analyze-corr__axis-label">Décomposition :</label>
      <select id="analyze-decomp-breakdown" class="analyze-corr__select">${bOpts}</select>
      <label for="analyze-decomp-entity" class="analyze-corr__axis-label">Territoire :</label>
      <select id="analyze-decomp-entity" class="analyze-corr__select">${eOpts}</select>
    </div>
    <div id="analyze-decomp-chart"></div>
  `;
  _updateDecompChart();
}

function _updateDecompChart() {
  const host = $("#analyze-decomp-chart");
  if (!host || !_analyzeCtx) return;
  const bd = DECOMP_BREAKDOWNS.find((b) => b.id === state.analyzeDecompId);
  const ent = _analyzeCtx.entities.find((e) => String(e.id) === state.analyzeDecompEntId);
  if (!bd || !ent) {
    host.innerHTML = `<p class="analyze-empty">Sélectionnez une décomposition et un territoire.</p>`;
    return;
  }
  const parentInd = INDICATORS.find((i) => i.key === bd.parent);
  const unit = parentInd?.unit || "€/hab";
  const comps = bd.comps.map((k, idx) => {
    const ind = INDICATORS.find((i) => i.key === k);
    return {
      label: ind?.label || k,
      color: DECOMP_PALETTE[idx % DECOMP_PALETTE.length],
      serie: ent.data?.values?.[k] || [],
    };
  });
  const parentSerie = ent.data?.values?.[bd.parent] || [];
  const legendItems = [
    ...comps.map((c) => `<span class="panel__chart-legend-item"><span class="panel__chart-legend-swatch" style="background:${c.color}"></span>${escapeHtml(c.label)}</span>`),
    `<span class="panel__chart-legend-item"><span class="panel__chart-legend-swatch" style="background:#222"></span>${escapeHtml(parentInd?.label || bd.parent)} (total publié)</span>`,
  ].join("");
  const note = bd.exact
    ? "Les deux composantes s'additionnent pour reconstituer le total (la ligne noire doit coïncider avec le sommet des barres)."
    : "Composantes principales publiées : leur somme peut être inférieure au total (la ligne noire) — l'écart correspond aux autres postes non détaillés ici.";
  host.innerHTML = `
    <p class="analyze-corr__hint">${escapeHtml(ent.label || "")} · ${escapeHtml(_analyzeCtx.setLabel)} · ${unit}. ${note}</p>
    ${buildStackedChart(_analyzeCtx.years, comps, parentSerie, parentInd?.label || bd.parent, unit, _analyzeCtx.yearIdx)}
    <div class="panel__chart-legend">${legendItems}</div>
  `;
}

/** Câble le bouton d'ouverture + la délégation d'événements de l'overlay
 *  (attachée UNE fois ; le corps est réécrit à chaque rendu). */
function setupAnalyzeDrawer() {
  const btn = $("#analyze-open");
  if (btn) btn.addEventListener("click", openAnalyzeDrawer);
  const overlay = $("#analyze-overlay");
  if (!overlay) return;

  overlay.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-analyze-close]")) {
      closeAnalyzeDrawer();
      return;
    }
    const tab = ev.target.closest("[data-analyze-mode]");
    if (tab) {
      state.analyzeMode = tab.dataset.analyzeMode;
      renderAnalyzeTabs();
      renderAnalyzeBody();
      return;
    }
    const yOpt = ev.target.closest("[data-y-key]");
    if (yOpt) {
      _selectY(yOpt.dataset.yKey);
      return;
    }
    if (ev.target.closest("#analyze-y-trigger")) {
      const p = $("#analyze-y-panel");
      if (p && p.hidden) _openYCombo();
      else _closeYCombo();
      return;
    }
    if (ev.target.closest("#analyze-cmp-clear")) {
      state.analyzeSelection.clear();
      _updateCmpList();
      _updateCmpChart();
      return;
    }
    // Tout autre clic dans l'overlay ferme le combo Y s'il est ouvert.
    if (!ev.target.closest("#analyze-y-combo")) _closeYCombo();
  });

  overlay.addEventListener("change", (ev) => {
    if (ev.target.id === "analyze-decomp-breakdown") {
      state.analyzeDecompId = ev.target.value;
      _updateDecompChart();
      return;
    }
    if (ev.target.id === "analyze-decomp-entity") {
      state.analyzeDecompEntId = ev.target.value;
      _updateDecompChart();
      return;
    }
    const cb = ev.target.closest("[data-cmp-id]");
    if (!cb) return;
    const id = cb.dataset.cmpId;
    if (cb.checked) {
      if (state.analyzeSelection.size >= ANALYZE_CMP_MAX) {
        cb.checked = false;
        return;
      }
      state.analyzeSelection.add(id);
    } else {
      state.analyzeSelection.delete(id);
    }
    _updateCmpChart();
    _updateCmpList();
  });

  overlay.addEventListener("input", (ev) => {
    if (ev.target.id === "analyze-cmp-search") _updateCmpList();
    else if (ev.target.id === "analyze-y-search") _buildYList(ev.target.value);
  });
}

async function init() {
  // Charger les préférences utilisateur (mode d'échelle, etc.) AVANT de
  // construire l'UI, pour que les contrôles soient déjà au bon état
  loadScalePreference();

  // INDICATORS est désormais externe (data/indicators.json, ~4,5 Mo sorti de
  // app.js) : on le charge AVANT de construire le sélecteur (qui le filtre par
  // niveau). Le fichier est préchargé dans le <head> → déjà en cache ici.
  await loadIndicators();
  state.currentIndicator = INDICATORS[0]; // « Recettes totales » (1er du tableau)

  buildIndicatorSelector();
  buildLevelSelector();
  setupMapDelegation();
  setupPanelDelegation();
  setupLeaderboardAutoInit();
  setupDrillDownBackButton();
  setupYearSlider();
  setupYearPlayback();
  setupScaleModeToggle();
  setupAnalyzeDrawer();

  // Laisser le navigateur PEINDRE les contrôles finalisés (dont l'élément LCP,
  // le texte d'aide #indicator-help) AVANT de charger + rendre les données.
  // Sans ce yield, le 1er paint incluant les contrôles n'a lieu qu'en fin
  // d'init (après le téléchargement de la synthèse) → Lighthouse rattache le
  // LCP à ce moment tardif (~5 s) alors que les contrôles sont prêts bien avant.
  // requestAnimationFrame = paint-first ; repli setTimeout car rAF est suspendu
  // dans un onglet en arrière-plan (sinon la carte ne se rendrait jamais).
  await new Promise((r) => {
    let done = false;
    const go = () => { if (!done) { done = true; r(); } };
    requestAnimationFrame(() => requestAnimationFrame(go));
    setTimeout(go, 60);
  });

  showLoader("Chargement…");
  try {
    const result = await loadSimpleLevel(state.currentLevel);
    state.currentEntities = result.entities;
    state.currentEntityById = new Map();
    for (const e of result.entities) state.currentEntityById.set(e.id, e);
    setYears(result.years);
    syncYearSlider();
  } catch (err) {
    console.error("Erreur de chargement initial :", err);
    hideLoader();
    $("#info-panel").innerHTML = `
      <p class="panel__placeholder" style="color:#c00;">
        Impossible de charger les données. Si vous ouvrez ce fichier directement
        depuis le système de fichiers (file://), lancez plutôt un serveur HTTP
        local (par ex. <code>python -m http.server</code>).
      </p>
    `;
    return;
  }

  renderMap({ viewBox: FRANCE_VIEWBOX });
  renderLegend();
  renderDrillDownHeader();
  // Affiche immédiatement le classement par défaut dans le panneau, sans
  // attendre une première sélection sur la carte.
  renderPanel();
  hideLoader();
}

// Selon le moment où app.js s'exécute, DOMContentLoaded peut déjà être passé
// (script module en fin de <body>) : on appelle alors init() directement,
// sinon on attend l'événement. Robuste quel que soit le mode de chargement.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

// ----------------------------------------------------------------------------
// Service Worker
// ----------------------------------------------------------------------------
// Cache-first pour les fichiers statiques et les données (~25 Mo). Au premier
// chargement, les fichiers sont téléchargés normalement puis stockés dans le
// cache du navigateur. Aux visites suivantes, ils sont servis depuis le cache,
// ce qui rend le site quasi instantané.
//
// Pour invalider le cache après une mise à jour des données : bumper la
// constante CACHE_NAME dans sw.js (v1 → v2).

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch((err) => {
      // Silencieux : un échec de SW ne doit pas casser le site (utile en
      // dev local sur certains setups où le scope n'est pas configurable).
      console.warn("Service Worker non enregistré :", err.message);
    });
  });
}
