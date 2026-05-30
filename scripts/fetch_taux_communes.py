"""Télécharge les taux d'imposition des communes (REI 2023-2024) et les
fusionne dans la synthèse multi-années (2017-2024) existante.

Source : dataset OFGL ``rei`` (Recensement des Éléments d'Imposition à la
fiscalité directe locale), construit à partir du fichier REI de la DGFIP.
Couverture : exercices 2023 et 2024 uniquement (le dataset historique
``fiscalite-locale-rei-trace`` ne fournit que la documentation des codes,
pas les valeurs annuelles antérieures).

Taux extraits (uniquement les parts votées par la **commune** ; les taux
votés par l'EPCI/syndicats viendront avec le futur niveau « Intercommunalités ») :

  * **TFB** — taxe foncière sur les propriétés bâties (variable
    « FB - COMMUNE / TAUX VOTÉ »)
  * **TFNB** — taxe foncière sur les propriétés non bâties
    (« FNB - COMMUNE / TAUX VOTÉ »)
  * **TH-RS** — taxe d'habitation sur les résidences secondaires
    (depuis la suppression de la THRP en 2023, la commune ne vote
    plus que celle-ci ; variable « TH - COMMUNE / TAUX VOTÉ »)
  * **CFE** — cotisation foncière des entreprises, part communale
    (uniquement pour les communes hors EPCI à fiscalité professionnelle
    unique — la majorité étant en FPU, la valeur est souvent nulle ;
    variable « CFE - COMMUNE / TAUX VOTÉ »)

Format injecté dans les fichiers existants : série multi-années de
longueur 8 (2017-2024) avec ``[null × 6, v_2023, v_2024]``, pour
respecter la structure attendue côté JS.

Fichiers mis à jour en place (idempotent) :
  - data/communes/by-dep/{code}.json
  - data/communes/synthese-communes-2024.json
  - data/communes/decoratif-communes-2024.json
  - data/communes/meta-communes-2024.json (inchangé, mais on relance
    le hash via le script build_communes_meta.py)

Usage : ``python scripts/fetch_taux_communes.py``.
Idempotent : si les fichiers sources existent déjà, on ne les re-télécharge
pas (ajouter ``--force`` pour forcer).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Dataset OFGL
OFGL_EXPORT_JSON = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/rei/exports/json"

# Mapping (varlib REI) → (clé d'indicateur stockée dans le JSON, libellé court).
# La clé doit être stable car référencée par INDICATORS dans app.js. On garde
# le suffixe « (%) » pour rester homogène avec « Taux epargne brute (%) ».
TAUX_MAPPING = {
    "FB - COMMUNE / TAUX VOTÉ":  "Taux TFB voté commune (%)",
    "FNB - COMMUNE / TAUX VOTÉ": "Taux TFNB voté commune (%)",
    "TH - COMMUNE / TAUX VOTÉ":  "Taux TH résid. secondaires voté commune (%)",
    "CFE - COMMUNE / TAUX VOTÉ": "Taux CFE voté commune (%)",
    # TEOM : Taxe d'Enlèvement des Ordures Ménagères. OFGL publie le taux net
    # par INSEE de commune (le contribuable d'une commune paye CE taux, peu
    # importe qui le vote : commune, EPCI ou syndicat). Les taux peuvent
    # varier d'une commune à l'autre au sein d'un même EPCI selon les
    # conventions locales.
    #
    # OFGL publie 4 taux NET par commune, qui coexistent sur le territoire
    # communal et s'appliquent à des zones différentes (typiquement zonage
    # par fréquence de collecte) :
    #   - TAUX PLEIN  : la majorité des contribuables (24 412 communes en 2024)
    #   - TAUX REDUIT A : ~813 communes (zones avec collecte moins fréquente)
    #   - TAUX REDUIT B : ~155 communes (idem, plus restreint)
    #   - TAUX REDUIT C : ~12 communes (cas marginaux)
    # OFGL ne publie pas de TAUX NET pour les zones D et E (seulement BASE
    # et MONTANT). Un contribuable d'une commune zonée paye UN seul de ces
    # taux selon sa localisation, définie par la collectivité.
    "FB - TEOM / TAUX PLEIN - TAUX NET":     "Taux TEOM plein (%)",
    "FB - TEOM / TAUX REDUIT A - TAUX NET":  "Taux TEOM réduit A (%)",
    "FB - TEOM / TAUX REDUIT B - TAUX NET":  "Taux TEOM réduit B (%)",
    "FB - TEOM / TAUX REDUIT C - TAUX NET":  "Taux TEOM réduit C (%)",

    # Bases nettes et produits (montants réels) par taxe, part commune.
    # Lecture directe d'un varlib REI chacun. Unité : € (montant brut).
    # Pour la CFE, la majorité des communes (en EPCI à FPU) ont des valeurs
    # nulles côté commune car la CFE est levée par l'EPCI ; les ~4 500
    # communes hors FPU ont leur propre base/produit communal.
    "FB - COMMUNE / BASE NETTE":      "Base nette TFB commune (€)",
    "FB - COMMUNE / MONTANT RÉEL":    "Produit TFB commune (€)",
    "FNB - COMMUNE / BASE NETTE":     "Base nette TFNB commune (€)",
    "FNB - COMMUNE / MONTANT RÉEL":   "Produit TFNB commune (€)",
    "TH - COMMUNE / BASE NETTE THS":  "Base nette TH résid. sec. commune (€)",
    "TH - COMMUNE / MONTANT RÉEL THS":"Produit TH résid. sec. commune (€)",
    "CFE - COMMUNE / BASE":           "Base CFE commune (€)",
    "CFE - COMMUNE / PRODUIT RÉEL":   "Produit CFE commune (€)",

    # Bases nettes et produits — PART EPCI publiée par REI pour chaque commune.
    # REI publie une valeur par commune même pour les varlibs nommés "GFP" ou
    # "INTERCOMMUNALITÉ" : chaque ligne représente la contribution de cette
    # commune à la base/produit de SON EPCI. Pas de somme côté Python (cf.
    # discussion Option B) — on lit directement le varlib OFGL, sans
    # transformation, et on stocke au niveau commune. Pour CFE, on prend le
    # varlib sans suffixe (équivalent au TAUX/PRODUIT NET applicable, regardless
    # of régime fiscal — la valeur cohérente pour tous les EPCIs).
    "FB - GFP / BASE NETTE":                       "Base nette TFB EPCI (€)",
    "FB - GFP / MONTANT RÉEL":                     "Produit TFB EPCI (€)",
    "FNB - GFP / BASE NETTE":                      "Base nette TFNB EPCI (€)",
    "FNB - GFP / MONTANT RÉEL":                    "Produit TFNB EPCI (€)",
    "TH - INTERCOMMUNALITÉ / BASE NETTE THS":      "Base nette TH résid. sec. EPCI (€)",
    "TH - INTERCOMMUNALITÉ / MONTANT RÉEL THS":    "Produit TH résid. sec. EPCI (€)",
    "CFE - INTERCOMMUNALITÉ / BASE":               "Base CFE EPCI (€)",
    "CFE - INTERCOMMUNALITÉ / PRODUIT RÉEL":       "Produit CFE EPCI (€)",

    # TEOM (Taxe d'Enlèvement des Ordures Ménagères) : bases nettes et
    # montants par ZONE GÉOGRAPHIQUE de la commune. Le TAUX PLEIN a un
    # "MONTANT NET LISSE" (lissage temporel des transitions tarifaires) ;
    # les TAUX RÉDUITS A-B-C ont un "MONTANT NET" simple.
    # Particularités OFGL :
    #   - Zones D et E : OFGL publie des lignes (118 chacune) mais avec
    #     valeur=null systématique en 2023-2024 ; exposées par fidélité.
    #   - "TAUX REDUIT C -  MONTANT NET" : double espace OFGL conservé tel quel
    "FB - TEOM / TAUX PLEIN - BASE NETTE":         "Base nette TEOM plein (€)",
    "FB - TEOM / TAUX PLEIN - MONTANT NET LISSE":  "Montant net lissé TEOM plein (€)",
    "FB - TEOM / TAUX REDUIT A - BASE NETTE":      "Base nette TEOM réduit A (€)",
    "FB - TEOM / TAUX REDUIT A - MONTANT NET":     "Montant net TEOM réduit A (€)",
    "FB - TEOM / TAUX REDUIT B - BASE NETTE":      "Base nette TEOM réduit B (€)",
    "FB - TEOM / TAUX REDUIT B - MONTANT NET":     "Montant net TEOM réduit B (€)",
    "FB - TEOM / TAUX REDUIT C - BASE NETTE":      "Base nette TEOM réduit C (€)",
    "FB - TEOM / TAUX REDUIT C -  MONTANT NET":    "Montant net TEOM réduit C (€)",
    "FB - TEOM / TAUX REDUIT D - BASE NETTE":      "Base nette TEOM réduit D (€)",
    "FB - TEOM / TAUX REDUIT D - MONTANT NET":     "Montant net TEOM réduit D (€)",
    "FB - TEOM / TAUX REDUIT E - BASE NETTE":      "Base nette TEOM réduit E (€)",
    "FB - TEOM / TAUX REDUIT E - MONTANT NET":     "Montant net TEOM réduit E (€)",
    # Agrégés et lissage TEOM
    "FB - TEOM / MONTANT RÉEL TOTAL":              "Montant TEOM total commune (€)",
    "FB - TEOM / NOMBRE D'ARTICLES":               "Nombre d'articles TEOM commune",
    "FB - TEOM / MONTANT LISSAGE":                 "Montant lissage TEOM commune (€)",
    "FB - TEOM / NOMBRE LISSAGE":                  "Nombre lissage TEOM commune",
    # TEOM INCITATIVE (TEOMI) : variante facultative basée sur la
    # production réelle de déchets (au lieu de la valeur locative).
    # Adoptée par ~118 communes / ~2 800 EPCIs / ~178 syndicats en 2024.
    "FB - TEOM INCITATIVE / MONTANT RÉEL / COMMUNE":   "Montant TEOM Incitative commune (€)",
    "FB - TEOM INCITATIVE / MONTANT RÉEL / GFP":       "Montant TEOM Incitative EPCI (€)",
    "FB - TEOM INCITATIVE / MONTANT RÉEL / SYNDICAT":  "Montant TEOM Incitative syndicat (€)",
    # Valeurs locatives moyennes TEOM (assiette de la TEOM)
    "Valeur locative moyenne TEOM N - COMMUNE":            "Valeur locative moyenne TEOM commune N (€)",
    "Valeur locative moyenne TEOM N - INTERCOMMUNALITÉ":   "Valeur locative moyenne TEOM EPCI N (€)",
    "Valeur locative moyenne TEOM N-1 - COMMUNE":          "Valeur locative moyenne TEOM commune N-1 (€)",
    "Valeur locative moyenne TEOM N-1 - INTERCOMMUNALITÉ": "Valeur locative moyenne TEOM EPCI N-1 (€)",

    # TSE — Taxe Spéciale d'Équipement. Taxe additionnelle versée par le
    # contribuable en plus des 4 taxes locales, destinée aux Établissements
    # Publics Fonciers (EPF) régionaux/départementaux et à des organismes
    # spéciaux (Société du Grand Paris, EPFL Guadeloupe, EPFL Martinique).
    # OFGL publie deux variantes :
    #   - "TSE"        : la TSE classique (EPF régionaux/départementaux)
    #     → ~14 500 communes concernées (zones de compétence des EPF)
    #   - "TSE AUTRES" : Société du Grand Paris + EPFL Guadeloupe/Martinique
    #     → ~1 850 communes (IDF + Antilles)
    # Lecture stricte des varlibs OFGL, un varlib = un indicateur.
    "FB - TSE / TAUX NET":                          "Taux TSE sur TFB (%)",
    "FB - TSE / BASE NETTE":                        "Base nette TSE sur TFB (€)",
    "FB - TSE / MONTANT RÉEL":                      "Montant TSE sur TFB (€)",
    "FB - TSE AUTRES / TAUX NET":                   "Taux TSE Autres sur TFB (%)",
    "FB - TSE AUTRES / BASE NETTE":                 "Base nette TSE Autres sur TFB (€)",
    "FB - TSE AUTRES / MONTANT RÉEL":               "Montant TSE Autres sur TFB (€)",
    "FNB - TSE / TAUX NET":                         "Taux TSE sur TFNB (%)",
    "FNB - TSE / BASE NETTE":                       "Base nette TSE sur TFNB (€)",
    "FNB - TSE / MONTANT RÉEL":                     "Montant TSE sur TFNB (€)",
    "FNB - TSE AUTRES / TAUX NET":                  "Taux TSE Autres sur TFNB (%)",
    "FNB - TSE AUTRES / BASE NETTE":                "Base nette TSE Autres sur TFNB (€)",
    "FNB - TSE AUTRES / MONTANT RÉEL":              "Montant TSE Autres sur TFNB (€)",
    "TH - TSE / TAUX NET":                          "Taux TSE sur TH (%)",
    "TH - TSE / BASE NETTE THS":                    "Base nette TSE sur TH résid. sec. (€)",
    "TH - TSE / MONTANT RÉEL THS":                  "Montant TSE sur TH résid. sec. (€)",
    "TH - TSE / BASE NETTE THLV":                   "Base nette TSE sur TH log. vacants (€)",
    "TH - TSE / MONTANT RÉEL THLV":                 "Montant TSE sur TH log. vacants (€)",
    "TH - TSE AUTRES / TAUX NET":                   "Taux TSE Autres sur TH (%)",
    "TH - TSE AUTRES / BASE NETTE THS":             "Base nette TSE Autres sur TH résid. sec. (€)",
    "TH - TSE AUTRES / MONTANT RÉEL THS":           "Montant TSE Autres sur TH résid. sec. (€)",
    "TH - TSE AUTRES / BASE NETTE THLV":            "Base nette TSE Autres sur TH log. vacants (€)",
    "TH - TSE AUTRES / MONTANT RÉEL THLV":          "Montant TSE Autres sur TH log. vacants (€)",
    "CFE - TSE / TAUX NET":                         "Taux TSE sur CFE (%)",
    "CFE - TSE / BASES":                            "Base TSE sur CFE (€)",
    "CFE - TSE / PRODUIT RÉEL":                     "Produit TSE sur CFE (€)",

    # CHAMBRES CONSULAIRES — taxes additionnelles versées par le contribuable
    # via la CFE (entreprises) ou la TFNB (agriculture) au profit des
    # Chambres de Commerce et Industrie (CCI), Chambres des Métiers et de
    # l'Artisanat (CMA), et Chambres d'Agriculture. Lecture stricte des
    # varlibs OFGL.

    # — CCI (via CFE)
    "CFE - CHAMBRE DE COMMERCE ET INDUSTRIE / TAUX NET":          "Taux CCI sur CFE (%)",
    "CFE - CHAMBRE DE COMMERCE ET INDUSTRIE / BASE":              "Base CCI sur CFE (€)",
    "CFE - CHAMBRE DE COMMERCE ET INDUSTRIE / PRODUIT RÉEL NET":  "Produit CCI sur CFE (€)",

    # — CMA via CFE : Droit Additionnel (proportionnel au CA, en %)
    "CFE - CHAMBRES DES METIERS /  DROIT ADDITIONNEL / TAUX NET": "Taux CMA droit additionnel sur CFE (%)",
    "CFE - CHAMBRE DES METIERS /  DROIT ADDITIONNEL / BASE":      "Base CMA droit additionnel sur CFE (€)",
    "CFE - CHAMBRE DES METIERS /  DROIT ADDITIONNEL / PRODUIT NET":"Produit CMA droit additionnel sur CFE (€)",

    # — CMA via CFE : Droit Fixe (forfait par contribuable, en €)
    "CFE - CHAMBRE DES METIERS / DROIT FIXE / QUOTITÉ":           "Quotité CMA droit fixe sur CFE",
    "CFE - CHAMBRE DES METIERS / DROIT FIXE / MONTANT":           "Montant CMA droit fixe sur CFE (€)",

    # — Droits fixes CMA hors CFE (CMA France + Chambre Métiers départementale)
    "Droit fixe CMA France (tous sauf 570,670,680)":              "Droit fixe CMA France standard (€)",
    "Droit fixe CMA France (570,670,680)":                        "Droit fixe CMA France (Alsace-Moselle) (€)",
    "Droit fixe CM départementale (570, 670, 680)":               "Droit fixe CM départementale (Alsace-Moselle) (€)",
    "Taux d'imposition CHAMBRE DES METIERS":                      "Taux d'imposition CMA (%)",

    # — Chambre d'Agriculture (via TFNB)
    "FNB - CHAMBRE D'AGRICULTURE / TAUX NET":                     "Taux Chambre Agriculture sur TFNB (%)",
    "FNB - CHAMBRE D'AGRICULTURE / BASE NETTE":                   "Base Chambre Agriculture sur TFNB (€)",
    "FNB - CHAMBRE D'AGRICULTURE / MONTANT RÉEL":                 "Montant Chambre Agriculture sur TFNB (€)",

    # — CAAA (Caisse d'Assurance Accident Agricole) sur TFNB :
    #   Droit Proportionnel (taux × base) + Droit Fixe (forfait par article)
    "FNB - CAAA / DROIT PROPORTIONNEL - TAUX NET":                "Taux CAAA droit proportionnel sur TFNB (%)",
    "FNB - CAAA / DROIT PROPORTIONNEL - BASE IMPOSABLE":          "Base CAAA droit proportionnel sur TFNB (€)",
    "FNB - CAAA / DROIT PROPORTIONNEL - MONTANT NET (BASE x TAUX)":"Montant CAAA droit proportionnel sur TFNB (€)",
    "FNB - CAAA / DROIT FIXE - TARIF":                            "Tarif CAAA droit fixe sur TFNB (€)",
    "FNB - CAAA / DROIT FIXE - MONTANT NET (NOMBRE D'ARTICLE x TARIF)":"Montant CAAA droit fixe sur TFNB (€)",

    # ------------------------------------------------------------------
    # IFER — Imposition Forfaitaire sur les Entreprises de Réseaux
    # ------------------------------------------------------------------
    # Taxe sur les installations énergétiques, télécoms et ferroviaires.
    # OFGL ventile par commune (idcom) chaque ligne, y compris quand le
    # destinataire est l'EPCI, le département ou la région : la valeur
    # représente la CONTRIBUTION DE CETTE COMMUNE au montant que reçoit
    # le bénéficiaire. Stockage strict au niveau commune, sans agrégation.
    #
    # 9 composantes :
    #   - Barrages hydrauliques (art. 1519 F)
    #   - Centrales nucléaires/thermiques à flamme (art. 1519 E)
    #   - Centrales photovoltaïques (art. 1519 F, même article que barrages)
    #   - Éoliennes terrestres (art. 1519 D)
    #   - Géothermie (art. 1519 HB)
    #   - Hydroliennes (art. 1519 D)
    #   - Installations gaz naturel (art. 1519 HA)
    #   - Stations radioélectriques / antennes (art. 1519 H)
    #   - Transformateurs électriques (art. 1519 G)
    # + 3 composantes "régionales" :
    #   - Matériel roulant ferroviaire SNCF (1599 quater A)
    #   - Matériel roulant RATP (1599 quater A bis, Grand Paris)
    #   - Répartiteurs téléphoniques (1599 quater B)
    # + Fonds de compensation IFER nucléaire/thermique
    # + IFER TOTALE par bénéficiaire (commune/EPCI/dept/région)

    # === IFER niveau COMMUNE — montants reçus par la commune ===
    "IFER BARRAGES HYDRAULIQUES ART 1519 F DU CGI / COMMUNE / MONTANT":
        "Montant IFER barrages hydrauliques commune (€)",
    "IFER BARRAGES HYDRAULIQUES ART 1519 F DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER barrages hydrauliques commune",
    "IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME ART 1519 E DU CGI / COMMUNE / MONTANT":
        "Montant IFER nucléaire/thermique commune (€)",
    "IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME ART 1519 E DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER nucléaire/thermique commune",
    "IFER CENTRALES PHOTOVOLTAIQUES ART 1519 F DU CGI / COMMUNE / MONTANT":
        "Montant IFER photovoltaïque commune (€)",
    "IFER CENTRALES PHOTOVOLTAIQUES ART 1519 F DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER photovoltaïque commune",
    "IFER ÉOLIENNES ART 1519 D DU CGI / COMMUNE / MONTANT":
        "Montant IFER éoliennes commune (€)",
    "IFER ÉOLIENNES ART 1519 D DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER éoliennes commune",
    "IFER GÉOTHERMIE (Art 1519 HB) DU CGI / COMMUNE / MONTANT":
        "Montant IFER géothermie commune (€)",
    "IFER GÉOTHERMIE (Art 1519 HB) DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER géothermie commune",
    "IFER HYDROLIENNES ART 1519 D DU CGI / COMMUNE / MONTANT":
        "Montant IFER hydroliennes commune (€)",
    "IFER HYDROLIENNES ART 1519 D DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER hydroliennes commune",
    "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / COMMUNE / MONTANT":
        "Montant IFER gaz naturel commune (€)",
    "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER gaz naturel commune",
    "IFER STATIONS RADIOÉLECTRIQUES ART 1519 H DU CGI / COMMUNE / MONTANT":
        "Montant IFER stations radioélectriques commune (€)",
    "IFER STATIONS RADIOÉLECTRIQUES ART 1519 H DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER stations radioélectriques commune",
    "IFER TRANSFORMATEURS ÉLECTRIQUES ART 1519 G DU CGI / COMMUNE / MONTANT":
        "Montant IFER transformateurs commune (€)",
    "IFER TRANSFORMATEURS ÉLECTRIQUES ART 1519 G DU CGI / COMMUNE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER transformateurs commune",
    "IFER TOTALE / COMMUNE":
        "IFER totale commune (€)",
    "FONDS DE COMPENSATION IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME (ART 1519 E CGI) / MONTANT":
        "Fonds de compensation IFER nucléaire/thermique (€)",

    # === IFER niveau DÉPARTEMENT — montants reçus par le dept au titre de cette commune ===
    "IFER BARRAGES HYDRAULIQUES ART 1519 F DU CGI / DÉPARTEMENT / MONTANT":
        "Montant IFER barrages hydrauliques département (€)",
    "IFER BARRAGES HYDRAULIQUES ART 1519 F DU CGI / DÉPARTEMENT / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER barrages hydrauliques département",
    "IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME ART 1519 E DU CGI / DÉPARTEMENT / MONTANT":
        "Montant IFER nucléaire/thermique département (€)",
    "IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME ART 1519 E DU CGI / DÉPARTEMENT / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER nucléaire/thermique département",
    "IFER CENTRALES PHOTOVOLTAIQUES ART 1519 F DU CGI / DÉPARTEMENT / MONTANT":
        "Montant IFER photovoltaïque département (€)",
    "IFER CENTRALES PHOTOVOLTAIQUES ART 1519 F DU CGI / DÉPARTEMENT / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER photovoltaïque département",
    "IFER ÉOLIENNES ART 1519 D DU CGI / DÉPARTEMENT / MONTANT":
        "Montant IFER éoliennes département (€)",
    "IFER ÉOLIENNES ART 1519 D DU CGI / DÉPARTEMENT / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER éoliennes département",
    "IFER HYDROLIENNES ART 1519 D DU CGI / DÉPARTEMENT / MONTANT":
        "Montant IFER hydroliennes département (€)",
    "IFER HYDROLIENNES ART 1519 D DU CGI / DÉPARTEMENT / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER hydroliennes département",
    "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / DÉPARTEMENT / MONTANT":
        "Montant IFER gaz naturel département (€)",
    "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / DÉPARTEMENT / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER gaz naturel département",
    "IFER STATIONS RADIOÉLECTRIQUES ART 1519 H DU CGI / DÉPARTEMENT / MONTANT":
        "Montant IFER stations radioélectriques département (€)",
    "IFER STATIONS RADIOÉLECTRIQUES ART 1519 H DU CGI / DÉPARTEMENT / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER stations radioélectriques département",
    "IFER TOTALE / DÉPARTEMENT":
        "IFER totale département (€)",

    # === IFER niveau RÉGION — composantes purement régionales ===
    "IFER GÉOTHERMIE (Art 1519 HB) DU CGI / RÉGION / MONTANT":
        "Montant IFER géothermie région (€)",
    "IFER GÉOTHERMIE (Art 1519 HB) DU CGI / RÉGION / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER géothermie région",
    "IFER MATÉRIEL ROULANT FERROVIAIRE ART 1599 QUATER A DU CGI / RÉGION / MONTANT DECLARE PAR LES SOCIETES DE TRANSPORT FERROVIAIRE NATIONAL":
        "Montant IFER matériel roulant ferroviaire déclaré (€)",
    "IFER MATÉRIEL ROULANT FERROVIAIRE ART 1599 QUATER A DU CGI / RÉGION / MONTANT REPARTI PAR RÉGION BENEFICIAIRE":
        "Montant IFER matériel roulant ferroviaire réparti (€)",
    "IFER MATÉRIEL ROULANT FERROVIAIRE ART 1599 QUATER A DU CGI / RÉGION / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER matériel roulant ferroviaire",
    "IFER MATÉRIEL ROULANT RATP ART 1599 QUATER A BIS DU CGI / GRAND PARIS / MONTANT":
        "Montant IFER matériel roulant RATP Grand Paris (€)",
    "IFER MATÉRIEL ROULANT RATP ART 1599 QUATER A BIS DU CGI / GRAND PARIS / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER matériel roulant RATP Grand Paris",
    "IFER RÉPARTITEURS PRINCIPAUX ART 1599 QUATER B DU CGI / RÉGION / MONTANT":
        "Montant IFER répartiteurs téléphoniques région (€)",
    "IFER RÉPARTITEURS PRINCIPAUX ART 1599 QUATER B DU CGI / RÉGION / MONTANT RÉPARTI PAR RÉGION BENEFICIAIRE":
        "Montant IFER répartiteurs téléphoniques réparti (€)",
    "IFER RÉPARTITEURS PRINCIPAUX ART 1599 QUATER B DU CGI / RÉGION / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER répartiteurs téléphoniques",
    "IFER TOTALE / RÉGION":
        "IFER totale région (€)",

    # === IFER niveau EPCI — régime unique (FU+ZAE seulement) ===
    "IFER BARRAGES HYDRAULIQUES ART 1519 F DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT":
        "Montant IFER barrages hydrauliques EPCI (€)",
    "IFER BARRAGES HYDRAULIQUES ART 1519 F DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER barrages hydrauliques EPCI",
    "IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME ART 1519 E DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT":
        "Montant IFER nucléaire/thermique EPCI (€)",
    "IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME ART 1519 E DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER nucléaire/thermique EPCI",
    "IFER CENTRALES PHOTOVOLTAIQUES ART 1519 F DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT":
        "Montant IFER photovoltaïque EPCI (€)",
    "IFER CENTRALES PHOTOVOLTAIQUES ART 1519 F DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER photovoltaïque EPCI",
    "IFER STATIONS RADIOÉLECTRIQUES ART 1519 H DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT":
        "Montant IFER stations radioélectriques EPCI (€)",
    "IFER STATIONS RADIOÉLECTRIQUES ART 1519 H DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER stations radioélectriques EPCI",
    "IFER TRANSFORMATEURS ÉLECTRIQUES ART 1519 G DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT":
        "Montant IFER transformateurs EPCI (€)",
    "IFER TRANSFORMATEURS ÉLECTRIQUES ART 1519 G DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS":
        "Nombre établissements IFER transformateurs EPCI",
    "IFER TOTALE / INTERCOMMUNALITÉ":
        "IFER totale EPCI (€)",

    # === IFER — PUISSANCE (ventilée par commune, unité : MW) ===
    "IFER BARRAGES HYDRAULIQUES ART 1519 F DU CGI / PUISSANCE TOTALE":
        "Puissance totale barrages hydrauliques (MW)",
    "IFER CENTRALES NUCLÉAIRES OU THERMIQUES A FLAMME ART 1519 E DU CGI / PUISSANCE TOTALE":
        "Puissance totale nucléaire/thermique (MW)",
    "IFER CENTRALES PHOTOVOLTAIQUES ART 1519 F DU CGI / PUISSANCE TOTALE":
        "Puissance totale photovoltaïque (MW)",
    "IFER ÉOLIENNES ART 1519 D DU CGI / PUISSANCE TOTALE":
        "Puissance totale éoliennes (MW)",
    "IFER GÉOTHERMIE (Art 1519 HB) DU CGI / PUISSANCE":
        "Puissance géothermie (MW)",
    "IFER HYDROLIENNES ART 1519 D DU CGI / PUISSANCE TOTALE":
        "Puissance totale hydroliennes (MW)",

    # ------------------------------------------------------------------
    # TVA — Compensations versées par l'État aux collectivités
    # ------------------------------------------------------------------
    # Depuis la réforme de 2020 (suppression progressive de la THP, baisse
    # des impôts de production CVAE, réforme des valeurs locatives
    # industrielles TFB), l'État compense la perte de recettes par des
    # fractions de TVA versées aux collectivités. 3 compensations distinctes :
    #   - Perte THP (Taxe d'Habitation sur résidences Principales),
    #     supprimée 2020-2023 : compensation versée à l'EPCI
    #   - Perte CVAE (Cotisation sur Valeur Ajoutée Entreprises),
    #     supprimée progressivement 2023-2027 : compensée pour les 4 niveaux
    #     (commune, EPCI, dept, région) + cas particuliers CTU et
    #     Métropole de Lyon
    #   - Perte TFB (revalorisation valeurs locatives industrielles 2021) :
    #     compensation versée au département (et à la Métropole de Lyon)
    #
    # 3 mesures par compensation/niveau :
    #   - "Actualisé N" : montant prévisionnel pour l'exercice N
    #   - "Définitif N-1" : montant définitif de l'exercice précédent
    #   - "Solde N-1" : ajustement positif/négatif entre actualisé et définitif
    #
    # Particularité OFGL : ventilé par commune (idcom) pour tous les niveaux.
    # Incohérences orthographiques OFGL conservées telles quelles (parfois
    # "actualisé" sans accord, double espace, "Montant" parfois préfixé par
    # "Montant total", etc.). On lit strictement les varlibs publiés.

    # === Compensation perte THP — EPCI ===
    "Montant TVA actualisé N en compensation perte THP - INTERCOMMUNALITÉ":
        "TVA actualisée compensation THP EPCI (€)",
    "Montant TVA définitive N-1 en compensation perte THP - INTERCOMMUNALITÉ":
        "TVA définitive N-1 compensation THP EPCI (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte THP - INTERCOMMUNALITÉ":
        "Solde TVA N-1 compensation THP EPCI (€)",

    # === Compensation perte THP — COMMUNE (marginal, 2 lignes effectives) ===
    "Montant TVA actualisée N en compensation perte THP - COMMUNE":
        "TVA actualisée compensation THP commune (€)",
    "Montant TVA définitive N-1 en compensation perte THP - COMMUNE":
        "TVA définitive N-1 compensation THP commune (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte THP - COMMUNE":
        "Solde TVA N-1 compensation THP commune (€)",

    # === Compensation perte CVAE — COMMUNE ===
    # NB: OFGL utilise "Montant total TVA actualisée N en compensation perte
    # CVAE -  COMMUNE" avec un DOUBLE ESPACE avant COMMUNE. On reproduit
    # exactement la chaîne pour le filtrage côté API.
    "Montant total TVA actualisée N en compensation perte CVAE -  COMMUNE":
        "TVA actualisée compensation CVAE commune (€)",
    "Montant total TVA définitive N-1 en compensation perte CVAE - COMMUNE":
        "TVA définitive N-1 compensation CVAE commune (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte CVAE - COMMUNE":
        "Solde TVA N-1 compensation CVAE commune (€)",

    # === Compensation perte CVAE — EPCI ===
    "Montant total TVA actualisée N en compensation perte CVAE -  INTERCOMMUNALITÉ":
        "TVA actualisée compensation CVAE EPCI (€)",
    "Montant total TVA définitive N-1 en compensation perte CVAE - INTERCOMMUNALITÉ":
        "TVA définitive N-1 compensation CVAE EPCI (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte CVAE - INTERCOMMUNALITÉ":
        "Solde TVA N-1 compensation CVAE EPCI (€)",

    # === Compensation perte CVAE — DÉPARTEMENT ===
    # Inconsistance OFGL : "Montant total" est utilisé pour actualisée N,
    # mais pas pour définitive N-1.
    "Montant total TVA actualisée N en compensation perte CVAE -  DEPARTEMENT":
        "TVA actualisée compensation CVAE département (€)",
    "Montant TVA définitive N-1 en compensation perte CVAE - DEPARTEMENT":
        "TVA définitive N-1 compensation CVAE département (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte CVAE - DEPARTEMENT":
        "Solde TVA N-1 compensation CVAE département (€)",

    # === Compensation perte CVAE — RÉGION ===
    # Inconsistance OFGL : "actualisée" / "définitive" sans "perte" pour
    # les 2 premiers varlibs, mais "perte CVAE" pour le solde.
    "Montant TVA actualisée N en compensation CVAE - RÉGION":
        "TVA actualisée compensation CVAE région (€)",
    "Montant TVA définitive N-1 en compensation CVAE - RÉGION":
        "TVA définitive N-1 compensation CVAE région (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte CVAE - RÉGION":
        "Solde TVA N-1 compensation CVAE région (€)",

    # === Compensation perte CVAE régionale — CTU (Guyane/Martinique/Corse) ===
    "Montant TVA actualisée N en compensation CVAE régionale - CTU":
        "TVA actualisée compensation CVAE régionale CTU (€)",
    "Montant TVA définitive N-1 en compensation CVAE régionale - CTU":
        "TVA définitive N-1 compensation CVAE régionale CTU (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte CVAE régionale - CTU":
        "Solde TVA N-1 compensation CVAE régionale CTU (€)",

    # === Compensation perte CVAE départementale — Métropole de Lyon ===
    "Montant total TVA actualisée N en compensation perte CVAE départementale -  METROPOLE DE LYON":
        "TVA actualisée compensation CVAE départementale Métropole Lyon (€)",
    "Montant TVA définitive N-1 en compensation perte CVAE départementale - METROPOLE DE LYON":
        "TVA définitive N-1 compensation CVAE départementale Métropole Lyon (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte CVAE départementale - METROPOLE DE LYON":
        "Solde TVA N-1 compensation CVAE départementale Métropole Lyon (€)",

    # === Compensation perte TFB — DÉPARTEMENT ===
    "Montant TVA actualisée N en compensation perte TFB - DEPARTEMENT":
        "TVA actualisée compensation TFB département (€)",
    "Montant TVA définitive N-1 en compensation perte TFB - DEPARTEMENT":
        "TVA définitive N-1 compensation TFB département (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte TFB - DEPARTEMENT":
        "Solde TVA N-1 compensation TFB département (€)",

    # === Compensation perte TFB — Métropole de Lyon ===
    # NB: "Montant TVA actualisé" SANS accord (masculin), contrairement aux
    # autres compensations TFB qui utilisent "actualisée".
    "Montant TVA actualisé N en compensation perte TFB - METROPOLE DE LYON":
        "TVA actualisée compensation TFB Métropole Lyon (€)",
    "Montant TVA définitive N-1 en compensation perte TFB - METROPOLE DE LYON":
        "TVA définitive N-1 compensation TFB Métropole Lyon (€)",
    "Solde (positif ou négatif) TVA N-1 en compensation perte TFB - METROPOLE DE LYON":
        "Solde TVA N-1 compensation TFB Métropole Lyon (€)",

    # ------------------------------------------------------------------
    # TASCOM — Taxe sur les Surfaces COMmerciales
    # ------------------------------------------------------------------
    # Acquittée par les exploitants de grandes surfaces commerciales
    # (CA ≥ 460 000 €, surface de vente > 400 m² depuis 2009). Tarif
    # national fixé par la loi (€/m²), multiplié par un coefficient
    # voté par la collectivité bénéficiaire (commune ou EPCI), borné
    # entre 0,8 et 1,2 (puis 0,95-1,05 par an).
    # Bénéficiaires :
    #   - Commune isolée hors EPCI à FPU : la commune perçoit la TASCOM
    #     et vote son coefficient
    #   - Commune en EPCI à FPU : l'EPCI perçoit la TASCOM et vote son
    #     coefficient (le coef. commune s'applique alors aux surfaces
    #     pré-2010 par dérogation, cas marginal)
    #   - Coefficient ZAE : EPCIs avec Zone d'Activité Économique
    #     spécifique peuvent voter un coefficient ZAE distinct
    "TASCOM - Coefficient multiplicateur":
        "Coefficient multiplicateur TASCOM commune",
    "TASCOM - Coefficient multiplicateur en ZAE":
        "Coefficient multiplicateur TASCOM en ZAE",
    "TASCOM au profit de la commune":
        "Montant TASCOM commune (€)",
    "TASCOM au profit du GFP":
        "Montant TASCOM EPCI (€)",

    # ------------------------------------------------------------------
    # TP — Ex-Taxe Professionnelle (compensations)
    # ------------------------------------------------------------------
    # La Taxe Professionnelle a été supprimée en 2010 et remplacée par la
    # Contribution Économique Territoriale (CET = CVAE + CFE). Pour
    # compenser les pertes de recettes, l'État a mis en place :
    #   - DCRTP : Dotation de Compensation de la Réforme de la Taxe
    #     Professionnelle (compensation directe, versée chaque année)
    #   - FNGIR : Fonds National de Garantie Individuelle des Ressources
    #     - Prélèvement FNGIR : collectivités "gagnantes" PAIENT au fonds
    #     - Reversement FNGIR : collectivités "perdantes" REÇOIVENT du fonds
    #   - Dotations d'exonérations TP : 10 catégories d'exonérations
    #     (ZRR, ZFU, ZF DOM, abattement 16%, etc.) compensées via dotations
    #     versées au département et à la région
    #
    # Cas particulier Métropole de Lyon (MGL) : collectivité à statut
    # spécial exerçant les compétences départementales sur 59 communes du
    # Rhône. OFGL publie pour MGL des varlibs "quote part" dédiés.
    #
    # Ventilation OFGL par commune (idcom) pour tous les niveaux.

    # === DCRTP — Dotation de Compensation de la Réforme TP ===
    "DCRTP / Commune":
        "DCRTP commune (€)",
    "DCRTP / INTERCOMMUNALITÉ":
        "DCRTP EPCI (€)",
    "DCRTP / DEPARTEMENT":
        "DCRTP département (€)",
    "DCRTP / RÉGION":
        "DCRTP région (€)",
    "DCRTP département – quote part MGL":
        "DCRTP département quote part Métropole Lyon (€)",

    # === FNGIR — Fonds National de Garantie Individuelle des Ressources ===
    "Prélèvement GIR / Commune":
        "Prélèvement FNGIR commune (€)",
    "Prélèvement GIR / INTERCOMMUNALITÉ":
        "Prélèvement FNGIR EPCI (€)",
    "Prélèvement GIR / DEPARTEMENT":
        "Prélèvement FNGIR département (€)",
    "Reversement GIR / Commune":
        "Reversement FNGIR commune (€)",
    "Reversement GIR / INTERCOMMUNALITÉ":
        "Reversement FNGIR EPCI (€)",
    "Reversement GIR / DEPARTEMENT":
        "Reversement FNGIR département (€)",
    "Versement GIR département - quote part MGL":
        "Versement FNGIR département quote part Métropole Lyon (€)",

    # === Dotations d'exonérations TP — niveau DÉPARTEMENT ===
    "TP - DOTATION - ABATTEMENT GENERAL DE 16 % (NET)/ DEPARTEMENT":
        "Dotation TP abattement 16% département (€)",
    "TP - DOTATION - ARTISANS ET AUTRES DANS LES ZRR / DEPARTEMENT":
        "Dotation TP artisans en ZRR département (€)",
    "TP - DOTATION - CRÉATIONS DANS LES ZFU 1ère et 2ème génération / DEPARTEMENT":
        "Dotation TP créations en ZFU 1ère/2ème gén. département (€)",
    "TP - DOTATION - EXTENSIONS DANS LES ZFU 1ère et 2ème génération / DEPARTEMENT":
        "Dotation TP extensions en ZFU 1ère/2ème gén. département (€)",
    "TP - DOTATION - EXO ZFU 3ème génération / DEPARTEMENT":
        "Dotation TP exo ZFU 3ème gén. département (€)",
    "TP - DOTATION - CRÉATIONS DANS LES ZRR / DEPARTEMENT":
        "Dotation TP créations en ZRR département (€)",
    "TP - DOTATION - EXTENSIONS DANS LES ZRR / DEPARTEMENT":
        "Dotation TP extensions en ZRR département (€)",
    "TP - DOTATION - EXONÉRATION DANS LA ZF DOM / DEPARTEMENT":
        "Dotation TP exo en ZF DOM département (€)",
    "TP - DOTATION - RÉDUCTION CRÉATION ÉTABLISSEMENT (NET)/ DEPARTEMENT":
        "Dotation TP réduction création établissement département (€)",
    "TP - DOTATION - RÉDUCTION DE LA FRACTION IMPOSABLE DES SALAIRES / DEPARTEMENT":
        "Dotation TP réduction fraction imposable salaires département (€)",
    "TP - DOTATION - RÉDUCTION RECETTES BNC / DEPARTEMENT":
        "Dotation TP réduction recettes BNC département (€)",

    # === Dotations d'exonérations TP — niveau RÉGION ===
    "TP - DOTATION - ABATTEMENT GENERAL DE 16 % (NET) / RÉGION":
        "Dotation TP abattement 16% région (€)",
    "TP - DOTATION - ARTISANS ET AUTRES DANS LES ZRR / RÉGION":
        "Dotation TP artisans en ZRR région (€)",
    "TP - DOTATION - Créations et Extensions ZFU 1ère et 2ème génération / RÉGION":
        "Dotation TP créations/extensions en ZFU 1ère/2ème gén. région (€)",
    "TP - DOTATION - EXO ZFU 3ème génération / RÉGION":
        "Dotation TP exo ZFU 3ème gén. région (€)",
    "TP - DOTATION - CRÉATIONS DANS LES ZRR / RÉGION":
        "Dotation TP créations en ZRR région (€)",
    "TP - DOTATION - EXTENSIONS DANS LES ZRR / RÉGION":
        "Dotation TP extensions en ZRR région (€)",
    "TP - DOTATION - EXO dans la ZF DOM / RÉGION":
        "Dotation TP exo en ZF DOM région (€)",
    "TP - DOTATION - RÉDUCTION CRÉATION ÉTABLISSEMENT (NET) / RÉGION":
        "Dotation TP réduction création établissement région (€)",
    "TP - DOTATION - RÉDUCTION DE LA FRACTION IMPOSABLE DES SALAIRES / RÉGION":
        "Dotation TP réduction fraction imposable salaires région (€)",
    "TP - DOTATION - RÉDUCTION RECETTES BNC / RÉGION":
        "Dotation TP réduction recettes BNC région (€)",

    # === TP - Somme des allocations compensatrices ===
    "TP - SOMME DES ALLOCATIONS COMPENSATRICES / DEP":
        "Somme allocations compensatrices TP département (€)",
    "TP - SOMME DES ALLOCATIONS COMPENSATRICES / REG":
        "Somme allocations compensatrices TP région (€)",
    "TP - SOMME DES ALLOCATIONS COMPENSATRICES / DEP QUOTE PART METROPOLE DE LYON":
        "Somme allocations compensatrices TP département quote part Métropole Lyon (€)",

    # === Dotations TP — quote part Métropole de Lyon (statut spécial) ===
    "Dotation TP Abattement général de 16 % - département quote part MGL":
        "Dotation TP abattement 16% département quote part Métropole Lyon (€)",
    "Dotation TP Exonération ZFU1 et ZFU2 créations – département quote part MGL":
        "Dotation TP exo ZFU 1/2 créations département quote part Métropole Lyon (€)",
    "Dotation TP Exonération ZFU1 et ZFU2 extensions – département quote part MGL":
        "Dotation TP exo ZFU 1/2 extensions département quote part Métropole Lyon (€)",
    "Dotation TP Exonération ZFU3 – département quote part MGL":
        "Dotation TP exo ZFU 3 département quote part Métropole Lyon (€)",
    "Dotation TP Exonération ZRR artisans et autres – département quote part MGL":
        "Dotation TP exo ZRR artisans département quote part Métropole Lyon (€)",
    "Dotation TP Exonération ZRR créations – département quote part MGL":
        "Dotation TP exo ZRR créations département quote part Métropole Lyon (€)",
    "Dotation TP Exonération ZRR extensions – département quote part MGL":
        "Dotation TP exo ZRR extensions département quote part Métropole Lyon (€)",
    "Dotation TP Réduction de la fraction imposable des salaires – département quote part MGL":
        "Dotation TP réduction fraction imposable salaires département quote part Métropole Lyon (€)",
    "Dotation TP Réduction des recettes BNC – département quote part MGL":
        "Dotation TP réduction recettes BNC département quote part Métropole Lyon (€)",

    # ------------------------------------------------------------------
    # Allocations compensatrices CFE / FB / FNB / TH (81 varlibs)
    # ------------------------------------------------------------------
    # L'État impose certaines exonérations fiscales (logements sociaux,
    # zones franches urbaines, ZRR, etc.) et compense les collectivités
    # pour la perte de recettes. Ces allocations s'ajoutent aux montants
    # perçus pour donner la recette fiscale réelle.
    # Ventilation OFGL par commune pour tous les niveaux.

    # === CFE Allocations compensatrices (31 varlibs) ===
    "CFE - SOMME DES ALLOCATIONS COMPENSATRICES / EPCI":
        "Somme allocations compensatrices CFE EPCI (€)",
    "CFE - SOMME DES ALLOCATIONS COMPENSATRICES / COMMUNE":
        "Somme allocations compensatrices CFE commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION BASE MINIMUM CA<=5000  / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE exo base minimum CA≤5000 EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION BASE MINIMUM CA<=5000  / COMMUNE":
        "Allocation compensatrice CFE exo base minimum CA≤5000 commune (€)",
    "CFE - ALLOCATION COMPENSATRICE ABATTEMENT 50% VL EI (CODIFIEE MU)/ INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE abattement 50% VL ÉI EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE ABATTEMENT 50% VL EI (CODIFIEE MU)/ COMMUNE":
        "Allocation compensatrice CFE abattement 50% VL ÉI commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - RÉDUCTION CRÉATION ÉTABLISSEMENT (NET) / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE réduction création établissement EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - RÉDUCTION CRÉATION ÉTABLISSEMENT (NET) / COMMUNE":
        "Allocation compensatrice CFE réduction création établissement commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - RÉDUCTION CRÉATION ÉTABLISSEMENT (NET) / INTERCOMMUNALITÉ (en ZAE)":
        "Allocation compensatrice CFE réduction création établissement EPCI en ZAE (€)",
    "CFE - ALLOCATION COMPENSATRICE - DIFFUSEURS DE PRESSE / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE diffuseurs de presse EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXO DIFFUSEURS DE PRESSE/ COMMUNE":
        "Allocation compensatrice CFE diffuseurs de presse commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - ARTISANS ET AUTRES DANS LES ZRR / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE artisans en ZRR EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - ARTISANS ET AUTRES DANS LES ZRR / COMMUNE":
        "Allocation compensatrice CFE artisans en ZRR commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXTENSIONS DANS LES ZRR / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE extensions en ZRR EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - CRÉATIONS DANS LES ZRR / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE créations en ZRR EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - CRÉATIONS DANS LES ZRR / COMMUNE":
        "Allocation compensatrice CFE créations en ZRR commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - CRÉATIONS DANS LES ZFU 1ère et 2ème génération / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE créations ZFU 1/2 EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - CRÉATIONS DANS LES ZFU 1ère et 2ème génération / COMMUNE":
        "Allocation compensatrice CFE créations ZFU 1/2 commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXO ZFU 3ème génération / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE exo ZFU 3 EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXO ZFU 3ème génération / COMMUNE":
        "Allocation compensatrice CFE exo ZFU 3 commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION DANS LES QPV / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE exo QPV EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXO dans les QPV / COMMUNE":
        "Allocation compensatrice CFE exo QPV commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION ZRC / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE exo ZRC EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION ZRC  / COMMUNE":
        "Allocation compensatrice CFE exo ZRC commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION ZDP  / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE exo ZDP EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION ZDP  / COMMUNE":
        "Allocation compensatrice CFE exo ZDP commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION DANS LA ZF DOM / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE exo ZF DOM EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - EXONÉRATION BASSINS URBAINS A DYNAMISER (COMPENSEE) / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE exo BUD EPCI (€)",
    "CFE - ALLOCATION COMPENSATRICE - ALLEGEMENT CORSE / COMMUNE":
        "Allocation compensatrice CFE allègement Corse commune (€)",
    "CFE - ALLOCATION COMPENSATRICE - ALLEGEMENT CORSE / INTERCOMMUNALITÉ":
        "Allocation compensatrice CFE allègement Corse EPCI (€)",

    # === FB Allocations compensatrices (27 varlibs) ===
    "FB - SOMME DES ALLOCATIONS COMPENSATRICES / COMMUNE":
        "Somme allocations compensatrices TFB commune (€)",
    "FB - SOMME DES ALLOCATIONS COMPENSATRICES / EPCI":
        "Somme allocations compensatrices TFB EPCI (€)",
    "FB - SOMME DES ALLOCATIONS COMPENSATRICES / REG":
        "Somme allocations compensatrices TFB région (€)",
    "FB - ALLOCATION COMPENSATRICE (ANCIENS DO) / COMMUNE":
        "Allocation compensatrice TFB anciens DO commune (€)",
    "FB - ALLOCATION COMPENSATRICE (ANCIENS DO) / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB anciens DO EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE ABATTEMENT 50% VL EI (CODIFIEE MU)/ INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB abattement 50% VL ÉI EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE ABATTEMENT 50% VL EI (CODIFIEE MU)/ COMMUNE":
        "Allocation compensatrice TFB abattement 50% VL ÉI commune (€)",
    "FB - ALLOCATION COMPENSATRICE - EXONÉRATIONS DE LONGUE DUREE / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exonérations longue durée EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE - EXONÉRATIONS DE LONGUE DUREE / COMMUNE":
        "Allocation compensatrice TFB exonérations longue durée commune (€)",
    "FB - ALLOCATION COMPENSATRICE - Exo logements faisant l'objet d'un contrat de ville (QV) et des locaux QV (ex-ZT)  / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exo logements contrat de ville EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE - Exo logements faisant l'objet d'un contrat de ville (QV) et des locaux QV (ex-ZT) / COMMUNE":
        "Allocation compensatrice TFB exo logements contrat de ville commune (€)",
    "FB - ALLOCATION COMPENSATRICE - Exo des locaux QV (ex-ZT) / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exo locaux QV ex-ZT EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE - Exo des locaux QV (ex-ZT) / COMMUNE":
        "Allocation compensatrice TFB exo locaux QV ex-ZT commune (€)",
    "FB - ALLOCATION COMPENSATRICE - Exo des locaux à bail à réhabilitation (RC)/ INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exo bail réhabilitation EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE - Exo des locaux à bail à réhabilitation (RC)/ COMMUNE":
        "Allocation compensatrice TFB exo bail réhabilitation commune (€)",
    "FB - ALLOCATION COMPENSATRICE - EXONÉRATIONS dans les QPV / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exo QPV EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE - EXO dans les QPV / COMMUNE":
        "Allocation compensatrice TFB exo QPV commune (€)",
    "FB - ALLOCATION COMPENSATRICE - EXONÉRATIONS dans la ZF DOM / COMMUNE":
        "Allocation compensatrice TFB exo ZF DOM commune (€)",
    "FB - ALLOCATION COMPENSATRICE - EXONÉRATIONS dans la ZF DOM / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exo ZF DOM EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE EXONÉRATION BUD / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exo BUD EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE EXONÉRATION BUD/ COMMUNE":
        "Allocation compensatrice TFB exo BUD commune (€)",
    "FB - ALLOCATION COMPENSATRICE EXONÉRATION ZRC/ INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB exo ZRC EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE EXONÉRATION ZRC/ COMMUNE":
        "Allocation compensatrice TFB exo ZRC commune (€)",
    "FB - ALLOCATION COMPENSATRICE - ABATTEMENT 30% INSTALL. ANTI-SISMIQUES / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB abattement 30% antisismiques EPCI (€)",
    "FB - ALLOCATION COMPENSATRICE - ABATTEMENT 30% INSTALL. ANTI-SISMIQUES / COMMUNE":
        "Allocation compensatrice TFB abattement 30% antisismiques commune (€)",
    "FB - ALLOCATION COMPENSATRICE MAYOTTE / COMMUNE":
        "Allocation compensatrice TFB Mayotte commune (€)",
    "FB - ALLOCATION COMPENSATRICE MAYOTTE / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFB Mayotte EPCI (€)",

    # === FNB Allocations compensatrices (12 varlibs) ===
    "FNB - SOMME DES ALLOCATIONS COMPENSATRICES / COMMUNE":
        "Somme allocations compensatrices TFNB commune (€)",
    "FNB - SOMME DES ALLOCATIONS COMPENSATRICES / EPCI":
        "Somme allocations compensatrices TFNB EPCI (€)",
    "FNB - SOMME DES ALLOCATIONS COMPENSATRICES / DEP":
        "Somme allocations compensatrices TFNB département (€)",
    "FNB - SOMME DES ALLOCATIONS COMPENSATRICES / REG":
        "Somme allocations compensatrices TFNB région (€)",
    "FNB - ALLOCATION COMPENSATRICE - EXONÉRATIONS TERRES AGRICOLES / COMMUNE":
        "Allocation compensatrice TFNB exo terres agricoles commune (€)",
    "FNB - ALLOCATION COMPENSATRICE - EXONÉRATIONS TERRES AGRICOLES / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFNB exo terres agricoles EPCI (€)",
    "FNB - ALLOCATION COMPENSATRICE - EXONÉRATIONS DE LONGUE DUREE / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFNB exo longue durée EPCI (€)",
    "FNB - ALLOCATION COMPENSATRICE - EXONÉRATIONS DE LONGUE DUREE / COMMUNE":
        "Allocation compensatrice TFNB exo longue durée commune (€)",
    "FNB - ALLOCATION COMPENSATRICE - Exonération FNB Natura 2000 / COMMUNE":
        "Allocation compensatrice TFNB exo Natura 2000 commune (€)",
    "FNB - ALLOCATION COMPENSATRICE - EXONÉRATION NATURA 2000 / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFNB exo Natura 2000 EPCI (€)",
    "FNB - ALLOCATION COMPENSATRICE - EXONÉRATION dans la ZF DOM / COMMUNE":
        "Allocation compensatrice TFNB exo ZF DOM commune (€)",
    "FNB - ALLOCATION COMPENSATRICE - EXONÉRATION dans la ZF DOM / INTERCOMMUNALITÉ":
        "Allocation compensatrice TFNB exo ZF DOM EPCI (€)",

    # === TH Allocations compensatrices (11 varlibs) ===
    "TH - SOMME DES ALLOCATIONS COMPENSATRICES / DEP":
        "Somme allocations compensatrices TH département (€)",
    "TH - SOMME DES ALLOCATIONS COMPENSATRICES / REG":
        "Somme allocations compensatrices TH région (€)",
    "TH - SOMME DES ALLOCATIONS COMPENSATRICES / COMMUNE":
        "Somme allocations compensatrices TH commune (€)",
    "TH - SOMME DES ALLOCATIONS COMPENSATRICES / EPCI":
        "Somme allocations compensatrices TH EPCI (€)",
    "TH - SOMME DES ALLOCATIONS COMPENSATRICES / DEP QUOTE-PART METROPOLE DE LYON":
        "Somme allocations compensatrices TH département quote-part Métropole Lyon (€)",
    "TH - Allocation compensatrice TH suppression THLV suite à passage à TLV en 2024 - COMMUNE":
        "Allocation compensatrice TH suppression THLV 2024 commune (€)",
    "TH - Allocation compensatrice TH suppression THLV suite à passage à TLV en 2024 - INTERCOMMUNALITÉ":
        "Allocation compensatrice TH suppression THLV 2024 EPCI (€)",
    "TH - Allocation compensatrice TH suppression THLV suite à passage à TLV en 2013 - COMMUNE":
        "Allocation compensatrice TH suppression THLV 2013 commune (€)",
    "TH - Allocation compensatrice TH suppression THLV suite à passage à TLV en 2013 - INTERCOMMUNALITÉ":
        "Allocation compensatrice TH suppression THLV 2013 EPCI (€)",
    "TH - ALLOCATION COMPENSATRICE MAYOTTE / COMMUNE":
        "Allocation compensatrice TH Mayotte commune (€)",
    "TH - ALLOCATION COMPENSATRICE MAYOTTE / INTERCOMMUNALITÉ":
        "Allocation compensatrice TH Mayotte EPCI (€)",

    # ------------------------------------------------------------------
    # Bases CFE détaillées : par catégorie de locaux, par tranche de CA
    # ------------------------------------------------------------------
    # Vision fine de la structure du tissu économique communal :
    # quelle proportion vient des locaux industriels / commerciaux /
    # d'habitation / agricoles ? Combien d'établissements à la base
    # minimum par tranche de chiffre d'affaires ?

    # === Bases brutes CFE par catégorie de locaux (14 varlibs) ===
    "CFE - BASES BRUTES : AUTRES LOCAUX / MONTANT":
        "Bases brutes CFE autres locaux — montant (€)",
    "CFE - BASES BRUTES : AUTRES LOCAUX / NOMBRE ARTICLES":
        "Bases brutes CFE autres locaux — nombre articles",
    "CFE - BASES BRUTES : LOCAUX D'HABITATION (H) / MONTANT":
        "Bases brutes CFE locaux d'habitation — montant (€)",
    "CFE - BASES BRUTES : LOCAUX D'HABITATION (H) / NOMBRE ARTICLES":
        "Bases brutes CFE locaux d'habitation — nombre articles",
    "CFE - BASES BRUTES : LOCAUX INDUSTRIELS ÉVALUÉS MÉTHODE COMPTABLE (EVAL A) / MONTANT":
        "Bases brutes CFE locaux industriels EVAL A — montant (€)",
    "CFE - BASES BRUTES : LOCAUX INDUSTRIELS ÉVALUÉS MÉTHODE COMPTABLE (EVAL A) / NOMBRE ARTICLES":
        "Bases brutes CFE locaux industriels EVAL A — nombre articles",
    "CFE - BASES BRUTES : LOCAUX PROF, COMMER ET ASS (C,L,P ou US) / MONTANT":
        "Bases brutes CFE locaux professionnels/commerciaux — montant (€)",
    "CFE - BASES BRUTES : LOCAUX PROF, COMMER ET ASS (C,L,P ou US) / NOMBRE ARTICLES":
        "Bases brutes CFE locaux professionnels/commerciaux — nombre articles",
    "CFE - BASES BRUTES : LOCAUX ÉVALUÉS BARÈME (EVAL E) / MONTANT":
        "Bases brutes CFE locaux EVAL E barème — montant (€)",
    "CFE - BASES BRUTES : LOCAUX ÉVALUÉS BARÈME (EVAL E) / NOMBRE ARTICLES":
        "Bases brutes CFE locaux EVAL E barème — nombre articles",
    "CFE - BASES BRUTES : PROPRIÉTÉS NON BÂTIES / MONTANT":
        "Bases brutes CFE propriétés non bâties — montant (€)",
    "CFE - BASES BRUTES : PROPRIÉTÉS NON BÂTIES / NOMBRE ARTICLES":
        "Bases brutes CFE propriétés non bâties — nombre articles",
    "CFE - BASES BRUTES RÉDUITES / MONTANT":
        "Bases brutes CFE réduites — montant (€)",
    "CFE - BASES BRUTES TOTALES / MONTANT":
        "Bases brutes CFE totales — montant (€)",

    # === Bases brutes CFE — réductions (4 varlibs) ===
    "CFE - BASES BRUTES / NOMBRE ARTICLES COMPORTANT UNE RÉDUCTION CRÉATION ÉTABLISSEMENT":
        "Bases brutes CFE — nombre articles avec réduction création établissement",
    "CFE - BASES BRUTES / RÉDUCTION POUR CRÉATION ÉTABLISSEMENT / MONTANT":
        "Bases brutes CFE — montant réduction création établissement (€)",
    "CFE - BASES BRUTES / NOMBRE ARTICLES AVEC RÉDUCTION ARTISANS":
        "Bases brutes CFE — nombre articles avec réduction artisans",
    "CFE - BASES BRUTES / RÉDUCTIONS ARTISANS ET COOPÉRATIVES AGRICOLES / MONTANT":
        "Bases brutes CFE — montant réductions artisans/coop agricoles (€)",

    # === Application des bases minimum (4 varlibs) ===
    "CFE - APPLICATION DES BASES MINIMUM / BASES AVANT / MONTANT":
        "Bases CFE avant application base minimum — montant (€)",
    "CFE - APPLICATION DES BASES MINIMUM / BASES AVANT / NOMBRE ARTICLES":
        "Bases CFE avant application base minimum — nombre articles",
    "CFE - APPLICATION DES BASES MINIMUM / BASES APRES / MONTANT":
        "Bases CFE après application base minimum — montant (€)",
    "CFE - APPLICATION DES BASES MINIMUM / BASES APRES / NOMBRE ARTICLES":
        "Bases CFE après application base minimum — nombre articles",

    # === Bases minimum CFE par tranche de CA — Temps complet (12 varlibs) ===
    "CFE - BASE MINIMUM TEMPS COMPLET - TRANCHE 1 (CA <=10 000)":
        "Base minimum CFE temps complet — tranche 1 (CA ≤10 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET - TRANCHE 2 (CA >10 000 ET <= 32 600)":
        "Base minimum CFE temps complet — tranche 2 (10 000<CA≤32 600) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET - TRANCHE 3 (CA >32 600 ET <= 100 000)":
        "Base minimum CFE temps complet — tranche 3 (32 600<CA≤100 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET - TRANCHE 4 (CA >100 000 ET <= 250 000)":
        "Base minimum CFE temps complet — tranche 4 (100 000<CA≤250 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET - TRANCHE 5 (CA >250 000 ET <= 500 000)":
        "Base minimum CFE temps complet — tranche 5 (250 000<CA≤500 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET - TRANCHE 6 (CA> 500 000)":
        "Base minimum CFE temps complet — tranche 6 (CA>500 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET (dans la ZAE) - TRANCHE 1 (CA <=10 000)":
        "Base minimum CFE temps complet ZAE — tranche 1 (CA ≤10 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET (dans la ZAE) - TRANCHE 2 (CA >10 000 ET <= 32 600)":
        "Base minimum CFE temps complet ZAE — tranche 2 (10 000<CA≤32 600) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET (dans la ZAE) - TRANCHE 3 (CA >32 600 ET <= 100 000)":
        "Base minimum CFE temps complet ZAE — tranche 3 (32 600<CA≤100 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET (dans la ZAE) - TRANCHE 4 (CA >100 000 ET <= 250 000)":
        "Base minimum CFE temps complet ZAE — tranche 4 (100 000<CA≤250 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET (dans la ZAE) - TRANCHE 5 (CA >250 000 ET <= 500 000)":
        "Base minimum CFE temps complet ZAE — tranche 5 (250 000<CA≤500 000) (€)",
    "CFE - BASE MINIMUM TEMPS COMPLET (dans la ZAE) - TRANCHE 6 (CA> 500 000)":
        "Base minimum CFE temps complet ZAE — tranche 6 (CA>500 000) (€)",

    # === Bases minimum CFE par tranche de CA — Temps partiel (12 varlibs) ===
    "CFE - BASE MINIMUM TEMPS PARTIEL - TRANCHE 1 (CA <=10 000)":
        "Base minimum CFE temps partiel — tranche 1 (CA ≤10 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL - TRANCHE 2 (CA >10 000 ET <= 32 600)":
        "Base minimum CFE temps partiel — tranche 2 (10 000<CA≤32 600) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL - TRANCHE 3 (CA >32 600 ET <= 100 000)":
        "Base minimum CFE temps partiel — tranche 3 (32 600<CA≤100 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL - TRANCHE 4 (CA >100 000 ET <= 250 000)":
        "Base minimum CFE temps partiel — tranche 4 (100 000<CA≤250 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL - TRANCHE 5 (CA >250 000 ET <= 500 000)":
        "Base minimum CFE temps partiel — tranche 5 (250 000<CA≤500 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL - TRANCHE 6 (CA> 500 000)":
        "Base minimum CFE temps partiel — tranche 6 (CA>500 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL (dans la ZAE) - TRANCHE 1 (CA <=10 000)":
        "Base minimum CFE temps partiel ZAE — tranche 1 (CA ≤10 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL (dans la ZAE) - TRANCHE 2 (CA >10 000 ET <= 32 600)":
        "Base minimum CFE temps partiel ZAE — tranche 2 (10 000<CA≤32 600) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL (dans la ZAE) - TRANCHE 3 (CA >32 600 ET <= 100 000)":
        "Base minimum CFE temps partiel ZAE — tranche 3 (32 600<CA≤100 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL (dans la ZAE) - TRANCHE 4 (CA >100 000 ET <= 250 000)":
        "Base minimum CFE temps partiel ZAE — tranche 4 (100 000<CA≤250 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL (dans la ZAE) - TRANCHE 5 (CA >250 000 ET <= 500 000)":
        "Base minimum CFE temps partiel ZAE — tranche 5 (250 000<CA≤500 000) (€)",
    "CFE - BASE MINIMUM TEMPS PARTIEL (dans la ZAE) - TRANCHE 6 (CA> 500 000)":
        "Base minimum CFE temps partiel ZAE — tranche 6 (CA>500 000) (€)",

    # === Nombre établissements assujettis à la base minimum (varlibs) ===
    "CFE - NOMBRE ARTICLES COMPORTANT UNE BASE MINIMUM / ENSEMBLE":
        "Nombre articles CFE base minimum — ensemble",
    "CFE - NOMBRE ARTICLES COMPORTANT UNE BASE MINIMUM / DONT TEMPS PARTIEL":
        "Nombre articles CFE base minimum — dont temps partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE TEMPS COMPLET ET TEMPS PARTIEL TOTAL":
        "Nombre établissements CFE base minimum — total temps complet+partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA <=5 000 TEMPS COMPLET ET TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — CA ≤5 000 temps complet+partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA <=5 000 TEMPS COMPLET ET TEMPS PARTIEL TOTAL":
        "Nombre établissements CFE base minimum — CA ≤5 000 total",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA <=5 000 TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — CA ≤5 000 temps partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >5 000 et <=10 000 TEMPS COMPLET":
        "Nombre établissements CFE base minimum — 5 000<CA≤10 000 temps complet",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >5 000 ET <=10 000  TEMPS COMPLET":
        "Nombre établissements CFE base minimum — 5 000<CA≤10 000 temps complet (variante)",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >5 000 ET <=10 000 TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — 5 000<CA≤10 000 temps partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >10 000 ET <= 32 600 TEMPS COMPLET":
        "Nombre établissements CFE base minimum — 10 000<CA≤32 600 temps complet",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >10 000 ET <= 32 600  TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — 10 000<CA≤32 600 temps partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >10 000 ET <= 32 600 TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — 10 000<CA≤32 600 temps partiel (variante)",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >32 600 ET <= 100 000 TEMPS COMPLET":
        "Nombre établissements CFE base minimum — 32 600<CA≤100 000 temps complet",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >32 600 ET <= 100 000 TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — 32 600<CA≤100 000 temps partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >100 000 ET <= 250 000 TEMPS COMPLET":
        "Nombre établissements CFE base minimum — 100 000<CA≤250 000 temps complet",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >100 000 ET <= 250 000 TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — 100 000<CA≤250 000 temps partiel",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETIIS À LA BASE MINIMUM DE CFE DONT CA >250 000 ET <= 500 000 TEMPS COMPLET":
        "Nombre établissements CFE base minimum — 250 000<CA≤500 000 temps complet",
    "CFE - NOMBRE ÉTABLISSEMENTS ASSUJETTIS À LA BASE MINIMUM DE CFE DONT CA >250 000 ET <= 500 000 TEMPS PARTIEL":
        "Nombre établissements CFE base minimum — 250 000<CA≤500 000 temps partiel",
    "CFE - NOMBRE AUTOENTREPRENEURS SOUMIS À LA BASE MINIMUM":
        "Nombre autoentrepreneurs CFE soumis à la base minimum",
    "CFE - NOMBRE AUTOENTREPRENEURS NON SOUMIS À LA BASE MINIMUM":
        "Nombre autoentrepreneurs CFE non soumis à la base minimum",
    "CFE - NOMBRE DE MICRO-ENTREPRISES OU SPECIAL BNC SOUMIS A LA BASE MINIMUM":
        "Nombre micro-entreprises/BNC CFE soumis à la base minimum",
    "CFE - NOMBRE DE MICRO-ENTREPRISES OU SPECIAL BNC SOUMIS A LA BASE MINIMUM DONT CA <= 5000":
        "Nombre micro-entreprises/BNC CFE soumis à la base minimum dont CA ≤5 000",
    "CFE - NOMBRE DE MICRO-ENTREPRISES OU SPECIAL BNC NON SOUMIS A LA BASE MINIMUM":
        "Nombre micro-entreprises/BNC CFE non soumis à la base minimum",
    "CFE - NOMBRE MICRO-ENTREPRENEURS SOUMIS A LA BASE MINIMUM DONT CA <= 5000":
        "Nombre micro-entrepreneurs CFE soumis à la base minimum dont CA ≤5 000",
    "CFE - COMMUNE / NOMBRE ARTICLES COMPORTANT UNE BASE TAXABLE DE CFE":
        "Nombre articles CFE avec base taxable (commune)",

    # ------------------------------------------------------------------
    # TASA — Taxe Spéciale Annexe (aéroports principalement)
    # ------------------------------------------------------------------
    # Taxe additionnelle perçue par les régions sur la CFE et le Foncier
    # Bâti dans le périmètre des aéroports d'envergure nationale ou
    # internationale (CDG, Orly, Marseille, Bordeaux, Lyon, etc.).
    # Couverture : ~50 communes en France (aéroports + communes adjacentes).

    # === TASA sur CFE (5 varlibs) ===
    "CFE - TASA / TAUX NET":
        "Taux TASA sur CFE (%)",
    "CFE - TASA / BASE NETTE":
        "Base nette TASA sur CFE (€)",
    "CFE - TASA / PRODUIT RÉEL NET":
        "Produit TASA sur CFE (€)",
    "CFE - TASA / NOMBRE ARTICLES":
        "Nombre articles TASA sur CFE",
    "CFE - Dotation abattement 50% VL EI (de droit) - TASARIF / RÉGION":
        "Dotation abattement 50% VL ÉI - TASARIF région (€)",

    # === TASA sur TFB (7 varlibs) ===
    "FB - TASA / TAUX NET":
        "Taux TASA sur TFB (%)",
    "FB - TASA / BASE NETTE":
        "Base nette TASA sur TFB (€)",
    "FB - TASA / MONTANT RÉEL":
        "Montant TASA sur TFB (€)",
    "FB - TASA / NOMBRE D'ARTICLES":
        "Nombre articles TASA sur TFB",
    "FB - TASA / LISSAGE - MONTANT":
        "Montant lissage TASA sur TFB (€)",
    "FB - TASA / LISSAGE - NOMBRE":
        "Nombre lissage TASA sur TFB",
    "FB - Dotation abattement 50% VL EI (de droit) - TASARIF / RÉGION":
        "Dotation abattement 50% VL ÉI TFB - TASARIF région (€)",

    # ------------------------------------------------------------------
    # TSC — Taxe Spéciale de la Chambre (CFE)
    # ------------------------------------------------------------------
    # Taxe additionnelle au profit d'une chambre consulaire spéciale
    # (cas marginaux : ~4 500 communes concernées).
    "CFE - TSC / TAUX NET":
        "Taux TSC sur CFE (%)",
    "CFE - TSC / BASE NETTE":
        "Base nette TSC sur CFE (€)",
    "CFE - TSC / MONTANT RÉEL":
        "Montant TSC sur CFE (€)",
    "CFE - TSC / NOMBRE ARTICLES":
        "Nombre articles TSC sur CFE",
    "CFE - LISSAGE / MONTANT / TSC":
        "Montant lissage TSC sur CFE (€)",
    "CFE - LISSAGE / NOMBRE / TSC":
        "Nombre lissage TSC sur CFE",

    # ------------------------------------------------------------------
    # FSRIF — Fonds de Solidarité des communes de la Région Île-de-France
    # ------------------------------------------------------------------
    # Mécanisme de péréquation horizontale propre à l'IDF : les communes
    # "riches" d'Île-de-France prélevées au profit des communes "pauvres".
    # ~294 communes contributrices en 2023-2024.
    "Prélèvement pour le FSRIF (communes d Ile-de-France)":
        "Prélèvement FSRIF commune Île-de-France (€)",
}

# ---------------------------------------------------------------------------
# Mapping MULTI varlibs -> 1 indicateur (combinaison "premier non-null").
# Cas : composantes IFER versées à un EPCI dont le régime fiscal varie
# (FU+ZAE, FA, ou éolien). Un EPCI n'a qu'UN régime à la fois ; on stocke
# donc le montant qui s'applique, peu importe lequel des 3 varlibs OFGL le
# publie. Pour CHAQUE commune-année, AU PLUS UN varlib aura une valeur ;
# les autres sont null.
# ---------------------------------------------------------------------------
TAUX_MAPPING_MULTI: dict[str, list[str]] = {
    "Montant IFER éoliennes EPCI (€)": [
        "IFER ÉOLIENNES ART 1519 D DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT",
        "IFER ÉOLIENNES ART 1519 D DU CGI / EPCI À FISCALITÉ ADDITIONNELLE / MONTANT",
        "IFER ÉOLIENNES ART 1519 D DU CGI / EPCI À RÉGIME ÉOLIEN / MONTANT",
    ],
    "Nombre établissements IFER éoliennes EPCI": [
        "IFER ÉOLIENNES ART 1519 D DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS",
        "IFER ÉOLIENNES ART 1519 D DU CGI / EPCI À FISCALITÉ ADDITIONNELLE / NOMBRE ÉTABLISSEMENTS",
        "IFER ÉOLIENNES ART 1519 D DU CGI / EPCI À RÉGIME ÉOLIEN / NOMBRE ÉTABLISSEMENTS",
    ],
    "Montant IFER hydroliennes EPCI (€)": [
        "IFER HYDROLIENNES ART 1519 D DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT",
        "IFER HYDROLIENNES ART 1519 D DU CGI / EPCI À RÉGIME ÉOLIEN / MONTANT",
    ],
    "Nombre établissements IFER hydroliennes EPCI": [
        "IFER HYDROLIENNES ART 1519 D DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS",
        "IFER HYDROLIENNES ART 1519 D DU CGI / EPCI À RÉGIME ÉOLIEN / NOMBRE ÉTABLISSEMENTS",
    ],
    "Montant IFER gaz naturel EPCI (€)": [
        "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / MONTANT",
        "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / EPCI À FISCALITÉ ADDITIONNELLE / MONTANT",
    ],
    "Nombre établissements IFER gaz naturel EPCI": [
        "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / EPCI À FISCALITÉ UNIQUE OU ZAE / NOMBRE ÉTABLISSEMENTS",
        "IFER INSTALLATIONS DE GAZ NATUREL ART 1519HA DU CGI / EPCI À FISCALITÉ ADDITIONNELLE / NOMBRE ÉTABLISSEMENTS",
    ],
}


def _all_indicator_keys() -> list[str]:
    """Liste consolidée des indicateurs (1:1 + multi), dans l'ordre."""
    return list(TAUX_MAPPING.values()) + list(TAUX_MAPPING_MULTI.keys())

# Années cibles pour l'enrichissement de la série temporelle. Doit
# correspondre à la liste `years` présente dans synthese-communes-2024.json.
ANNEES_COMMUNES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
# Années réellement couvertes par REI (les autres restent en `null`).
ANNEES_REI = [2023, 2024]


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def _download_one_taux(varlib: str, out_path: Path, force: bool) -> None:
    """Télécharge tous les enregistrements REI pour une `varlib` donnée
    et les écrit en JSON. Filtre côté API pour minimiser le volume."""
    if out_path.exists() and not force:
        print(f"  [taux]   {varlib:50s} -> cache ({out_path.stat().st_size//1024} Ko)")
        return

    # On filtre sur le varlib exact, et on ne récupère que les colonnes utiles.
    params = {
        "where": f'varlib = "{varlib}"',
        "select": "annee,idcom,dep,sirepci,valeur",
        # 'json' streame en NDJSON-array — pratique pour gros volumes
    }
    url = f"{OFGL_EXPORT_JSON}?{urllib.parse.urlencode(params)}"
    print(f"  [taux]   {varlib:50s} -> téléchargement ...", end=" ", flush=True)
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    print(f"{len(data)/1024:.0f} Ko en {time.time()-t0:.1f}s")


def _varlib_slug(varlib: str) -> str:
    """Slug stable pour nom de fichier cache. Doit être <240 caractères pour
    rester sous la limite Windows MAX_PATH classique (260) avec chemin.
    Les caractères interdits Windows (< > : " | ? * \\) sont remplacés
    par "_" pour éviter `OSError [Errno 22]`."""
    s = varlib.lower().replace("é", "e")
    out = []
    for c in s:
        if c.isalnum() or c == "-":
            out.append(c)
        else:
            # Tout caractère non-alphanumérique → underscore, y compris
            # les caractères interdits Windows < > : " | ? * \ /
            out.append("_")
    s = "".join(out)
    # Compresser les underscores consécutifs
    while "__" in s:
        s = s.replace("__", "_")
    s = s.strip("_")
    if len(s) > 200:
        s = s[:200]
    return s


def download_all_taux(force: bool = False) -> Path:
    """Télécharge tous les varlibs (1:1 + multi) dans ``data/taux/``."""
    taux_dir = DATA / "taux"
    taux_dir.mkdir(parents=True, exist_ok=True)
    # Varlibs uniques à télécharger (déduplication car multi peut référencer
    # d'autres varlibs)
    all_varlibs = set(TAUX_MAPPING.keys())
    for varlibs in TAUX_MAPPING_MULTI.values():
        all_varlibs.update(varlibs)
    for varlib in sorted(all_varlibs):
        out = taux_dir / f"rei-{_varlib_slug(varlib)}.json"
        _download_one_taux(varlib, out, force)
    return taux_dir


# ---------------------------------------------------------------------------
# Construction de l'index { insee -> { indicateur -> [v2017..v2024] } }
# ---------------------------------------------------------------------------

def _normalize_insee(s: str | None) -> str | None:
    """Pad INSEE codes to 5 chars (handles e.g. "5024" -> "05024")."""
    if not s:
        return None
    s = str(s)
    return s.zfill(5) if s.isdigit() else s


def _ingest_varlib(
    taux_dir: Path,
    varlib: str,
    indicator_key: str,
    taux_idx: dict[str, dict[str, list]],
) -> int:
    """Charge un varlib et fusionne ses valeurs dans `taux_idx`.
    Politique : pour une cellule (commune, année) déjà renseignée, on garde
    la première valeur lue (utile pour le mode multi-varlibs où plusieurs
    varlibs feed le même indicateur)."""
    path = taux_dir / f"rei-{_varlib_slug(varlib)}.json"
    if not path.exists():
        print(f"  [warn]   {path.name} introuvable, on saute")
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for r in records:
        insee = _normalize_insee(r.get("idcom"))
        if not insee:
            continue
        try:
            annee = int(r.get("annee") or 0)
        except (TypeError, ValueError):
            continue
        if annee not in ANNEES_REI:
            continue
        valeur = r.get("valeur")
        if valeur is None:
            continue
        year_idx = ANNEES_COMMUNES.index(annee)
        entry = taux_idx.setdefault(insee, {})
        serie = entry.setdefault(indicator_key, [None] * len(ANNEES_COMMUNES))
        if serie[year_idx] is None:
            serie[year_idx] = float(valeur)
            n += 1
    return n


def build_taux_index(taux_dir: Path) -> dict[str, dict[str, list]]:
    """Lit les fichiers téléchargés et construit :
        { insee_padded : { indicateur_key : [v2017, …, v2024] } }
    avec `None` pour les années avant 2023.

    Gère deux types de mappings :
      - TAUX_MAPPING : 1 varlib -> 1 indicateur (lecture directe)
      - TAUX_MAPPING_MULTI : N varlibs -> 1 indicateur (combinaison
        "premier non-null" : un EPCI a UN seul régime donc 1 varlib par
        commune-année est non-null).
    """
    taux_idx: dict[str, dict[str, list]] = {}

    # 1. Mapping 1:1
    for varlib, indicator_key in TAUX_MAPPING.items():
        n = _ingest_varlib(taux_dir, varlib, indicator_key, taux_idx)
        print(f"  [taux]   {varlib[:70]:70s} -> {n} cellules")

    # 2. Mapping N:1 (combinaison de régimes EPCI)
    for indicator_key, varlibs in TAUX_MAPPING_MULTI.items():
        total = 0
        for varlib in varlibs:
            total += _ingest_varlib(taux_dir, varlib, indicator_key, taux_idx)
        print(f"  [multi]  {indicator_key:70s} -> {total} cellules "
              f"({len(varlibs)} régimes combinés)")

    return taux_idx


# ---------------------------------------------------------------------------
# Fusion dans les fichiers existants
# ---------------------------------------------------------------------------

def merge_into_by_dep(taux_idx: dict[str, dict[str, list]]) -> None:
    """Injecte les séries de taux dans chaque fichier by-dep/{code}.json.

    - Ajoute les nouveaux indicateurs à la liste `indicators` (s'ils n'y
      sont pas déjà, pour idempotence).
    - Pour chaque commune, ajoute la série dans `data.values[indicator]`
      (création si absent ; remplacement sinon, pour rafraîchir).
    """
    by_dep_dir = DATA / "communes" / "by-dep"
    files = sorted(p for p in by_dep_dir.glob("*.json") if not p.name.startswith("_"))
    print(f"  [merge]  by-dep : {len(files)} fichiers")
    matched = 0
    total = 0
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        added_indicators = [k for k in _all_indicator_keys() if k not in d["indicators"]]
        for k in added_indicators:
            d["indicators"].append(k)
        for c in d.get("communes", []):
            data = c.get("data") or {}
            insee = _normalize_insee(data.get("insee"))
            total += 1
            if not insee:
                continue
            series = taux_idx.get(insee)
            if not series:
                continue
            matched += 1
            values = data.setdefault("values", {})
            for ind_key, serie in series.items():
                values[ind_key] = serie
        path.write_text(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    print(f"  [merge]  by-dep : {matched}/{total} communes enrichies")


def merge_into_synthese(taux_idx: dict[str, dict[str, list]]) -> None:
    """Injecte les séries de taux dans synthese-communes-2024.json."""
    path = DATA / "communes" / "synthese-communes-2024.json"
    if not path.exists():
        print(f"  [warn]   {path.name} introuvable, on saute")
        return
    d = json.loads(path.read_text(encoding="utf-8"))
    added_indicators = [k for k in _all_indicator_keys() if k not in d["indicators"]]
    for k in added_indicators:
        d["indicators"].append(k)
    matched = 0
    for c in d.get("communes", []):
        insee = _normalize_insee(c.get("insee"))
        if not insee:
            continue
        series = taux_idx.get(insee)
        if not series:
            continue
        matched += 1
        values = c.setdefault("values", {})
        for ind_key, serie in series.items():
            values[ind_key] = serie
    path.write_text(
        json.dumps(d, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  [merge]  synthese : {matched}/{len(d.get('communes',[]))} communes enrichies")


def _slug_indicator(name: str) -> str:
    """Slug stable pour les noms d'indicateurs (utilisé pour les fichiers
    `decoratif-values/{slug}.json`). Translittération ASCII + minuscules +
    tirets ; tronqué à 100 caractères. **Doit produire le même résultat
    que `_slug_indicator` dans fetch_all.py** (cohérence inter-scripts)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    out_chars = []
    for c in s:
        if c.isalnum():
            out_chars.append(c)
        elif c in (" ", "-", "_", "/", "(", ")", ",", "'", "\"", ".", "%"):
            out_chars.append("-")
    slug = "".join(out_chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    if len(slug) > 100:
        slug = slug[:100].rstrip("-")
    return slug


def merge_into_decoratif(taux_idx: dict[str, dict[str, list]]) -> None:
    """Écrit un fichier par indicateur dans ``decoratif-values/{slug}.json``
    et met à jour l'index ``decoratif-values/_index.json``.

    Architecture lazy-loading : le décoratif est désormais éclaté en :
      - ``decoratif-paths-2024.json`` : contours SVG seuls (chargé une fois)
      - ``decoratif-values/{slug}.json`` : valeurs par indicateur (chargés
        à la demande quand l'utilisateur sélectionne l'indicateur côté UI)

    L'alignement positionnel commune ↔ index est donné par
    ``meta-communes-2024.json`` (les listes ``values`` produites ici sont
    indexées comme ``meta["communes"]`` et ``paths_data["paths"]``).
    """
    paths_path = DATA / "communes" / "decoratif-paths-2024.json"
    meta_path = DATA / "communes" / "meta-communes-2024.json"
    if not paths_path.exists() or not meta_path.exists():
        print("  [warn]   decoratif-paths ou meta absent, on saute")
        return

    paths_data = json.loads(paths_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    n_paths = len(paths_data.get("paths", []))
    n_meta = len(meta.get("communes", []))
    if n_paths != n_meta:
        print(
            f"  [warn]   désalignement paths/meta ({n_paths} vs {n_meta}) "
            f"-> abort merge decoratif"
        )
        return

    values_dir = DATA / "communes" / "decoratif-values"
    values_dir.mkdir(parents=True, exist_ok=True)
    index_path = values_dir / "_index.json"
    index: dict[str, str] = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {}

    null_serie = [None] * len(ANNEES_COMMUNES)
    new_inds = [k for k in _all_indicator_keys() if k not in index]
    written = 0
    total_size = 0

    for ind_key in new_inds:
        # Construire l'array des séries pour cet indicateur, aligné sur meta
        values = []
        for i in range(n_meta):
            insee = _normalize_insee(meta["communes"][i][1])
            series_by_insee = taux_idx.get(insee, {})
            values.append(series_by_insee.get(ind_key, null_serie))

        slug = _slug_indicator(ind_key)
        index[ind_key] = slug
        out_payload = {
            "indicator": ind_key,
            "years": ANNEES_COMMUNES,
            "values": values,
        }
        out_file = values_dir / f"{slug}.json"
        out_file.write_text(
            json.dumps(out_payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        total_size += out_file.stat().st_size
        written += 1

    # Mettre à jour l'index global
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    mb = total_size / 1024 / 1024
    print(
        f"  [merge]  decoratif-values : {written} indicateurs ajoutés "
        f"({mb:.1f} Mo total) ; index : {len(index)} indicateurs"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="Re-télécharge même si le fichier source existe déjà.",
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Saute le téléchargement, fusionne juste ce qui est en cache.",
    )
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("Taux d'imposition communes (REI 2023-2024)")
    print("=" * 60)

    if args.skip_download:
        taux_dir = DATA / "taux"
        print(f"  [taux]   skip-download : on lit depuis {taux_dir}/")
    else:
        taux_dir = download_all_taux(force=args.force)

    print()
    print("Construction de l'index par INSEE...")
    taux_idx = build_taux_index(taux_dir)
    print(f"  [index]  {len(taux_idx)} communes ont au moins un taux")

    print()
    print("Fusion dans les fichiers existants...")
    merge_into_by_dep(taux_idx)
    merge_into_synthese(taux_idx)
    merge_into_decoratif(taux_idx)

    print()
    print(f"Terminé en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
