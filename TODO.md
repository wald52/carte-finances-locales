# Pistes d'évolution — chantiers reportés

Liste tenue à jour des décisions explicitement reportées, avec leur
contexte et les options envisagées.

## Couverture territoriale — COM hors OFGL

**Statut** : reporté. Le périmètre OFGL exclut volontairement ces
collectivités. Faisable mais nécessite des sources non-OFGL et des
contours SVG dédiés.

Territoires absents de toutes les bases OFGL principales
(`ofgl-base-regions`, `ofgl-base-departements`, `ofgl-base-communes`,
`fpic-ensembles-intercommunaux`) :

| Code | Nom | Statut |
|------|-----|--------|
| 975  | Saint-Pierre-et-Miquelon | COM (Art. 74) |
| 977  | Saint-Barthélemy | COM (Art. 74) |
| 978  | Saint-Martin | COM (Art. 74) |
| 986  | Wallis-et-Futuna | COM (Art. 74) |
| 987  | Polynésie française | POM (loi organique 2004) |
| 988  | Nouvelle-Calédonie | sui generis (Accord de Nouméa) |

**Pourquoi OFGL les exclut** :
- Régimes fiscaux propres (pas de TVA en NC/Polynésie, IR autonome, etc.)
- Plans comptables distincts (M71 territoriale en Polynésie, plan NC spécifique)
- Statut juridique non assimilable à dpt/région (Art. 74 et 76-77 de la Constitution)
- Comparabilité ligne à ligne impossible avec la métropole

**Pour intégrer plus tard** :

1. **Sources alternatives non-OFGL** par territoire :
   - Nouvelle-Calédonie : gouv.nc + isee.nc
   - Polynésie française : ispf.pf
   - Wallis-et-Futuna : territoire-wf.fr
   - Saint-Pierre-et-Miquelon : DGOM
   - Saint-Martin / Saint-Barthélemy : DGOM + Conseil territorial
2. **Contours géographiques** : pas dans `departements_formes_geo_svg` niveau
   FRA. Solutions : OpenStreetMap (extraction OSM + simplification),
   IGN BD CARTO Outre-Mer, ou fonds de carte par territoire.
3. **Encart cartographique séparé** sur la carte France (style classique
   pour les DROM), ou page dédiée par territoire.
4. **Indicateurs limités au sous-ensemble comparable** : population,
   dotations versées par l'État (déjà partiellement dans
   `dotations-communes` pour le FPIC). Éviter les agrégats sensibles
   à la nomenclature.

**Exception déjà gérée** : pour les flux versés par l'État central
(notamment FPIC), 65 communes isolées d'Outre-Mer apparaissent dans
`dotations-communes`. Une intégration ciblée est faisable sans sortir
d'OFGL. Voir ci-dessous.

## FPIC des communes isolées (incl. Paris)

**Statut** : reporté. Donnée disponible dans OFGL, intégration faisable
en 1-2 h.

Source : `dotations-communes` (OFGL), `categorie = "FPIC"`, 3 variables
(Prélèvement, Versement, Solde), 8 ans (2018-2025), **65 communes** :

- Paris (75056) — gros contributeur net (~200 M€/an)
- Île-de-Sein (29083), Ouessant (29155) — 2 îles bretonnes
- Saint-Pierre-et-Miquelon : 2 communes
- Polynésie française : ~30 communes
- Nouvelle-Calédonie : ~30 communes
- Wallis-et-Futuna : 3 communes

**Effet visible attendu** : Paris se colorie sur les indicateurs FPIC au
niveau intercommunalités (au lieu de gris), avec sa vraie valeur OFGL.
Les communes isolées d'Outre-Mer apparaissent dans le leaderboard FPIC.

**Implémentation** :
1. Script `fetch_fpic_communes_isolees.py` télécharge `dotations-communes`
   filtré `categorie="FPIC"` (~900 records)
2. Enrichit `synthese-communes-2024.json` avec les 3 variables (et €/hab
   via population INSEE)
3. Étend le fallback de coloration cartographique à un 3e niveau pour
   les communes isolées : MGP → EPT → commune (lecture verbatim de la
   donnée commune)
4. Ajoute les indicateurs au niveau communes dans `INDICATORS`
