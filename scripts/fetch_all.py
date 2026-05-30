#!/usr/bin/env python3
"""
Téléchargement et préparation des données ouvertes sur les collectivités
locales françaises (régions, départements, communes).

Toutes les sources sont publiques, libres d'usage, sans clé d'API.
Détails dans data/SOURCES.md.

Usage :
    python scripts/fetch_all.py                  # télécharge ce qui manque
    python scripts/fetch_all.py --force          # re-télécharge tout
    python scripts/fetch_all.py --skip-heavy     # saute les gros fichiers (>50 Mo)
    python scripts/fetch_all.py --only regions   # ne traite qu'un niveau
    python scripts/fetch_all.py --only communes
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


# Coordonnées SVG : pleine précision OFGL (2 décimales) conservée partout
# (synthese, by-dep, décoratif .full.json), SAUF le fichier décoratif communes
# *servi* (data/communes/decoratif-paths-2024.json), simplifié (Douglas-Peucker)
# + arrondi à 1 décimale pour alléger le chargement du calque France entière.
# Transformation de présentation, invisible à cette échelle ; détail et
# réglages dans scripts/optimize_decoratif_paths.py.

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
# Toutes les URLs ci-dessous sont des points d'accès officiels et publics.
# Modifier ce dictionnaire suffit à ajouter ou actualiser une source.

GEO_API = "https://geo.api.gouv.fr"
OFGL_EXPORT = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{ds}/exports/{fmt}"

SOURCES = {
    # Listes administratives officielles (INSEE via geo.api.gouv.fr)
    "regions_liste": {
        "url": f"{GEO_API}/regions?fields=nom,code",
        "out": DATA / "regions.json",
        "description": "Liste des 18 régions (INSEE COG)",
        "level": "base",
        "heavy": False,
    },
    "departements_liste": {
        "url": f"{GEO_API}/departements?fields=nom,code,codeRegion",
        "out": DATA / "departements.json",
        "description": "Liste des 101 départements (INSEE COG)",
        "level": "base",
        "heavy": False,
    },
    "communes_liste": {
        "url": (
            f"{GEO_API}/communes?fields=nom,code,codeDepartement,"
            "codeRegion,population,siren&format=json"
        ),
        "out": DATA / "communes.json",
        "description": "Liste des ~35 000 communes (INSEE COG, population, SIREN)",
        "level": "base",
        "heavy": False,
    },
    # Méthodologie OFGL — partagée entre toutes les collectivités
    "ofgl_definitions": {
        "url": OFGL_EXPORT.format(ds="methodologie-ofgl-definitions-agregats-financiers", fmt="json"),
        "out": DATA / "methodologie" / "ofgl-definitions-agregats.json",
        "description": "Définitions officielles des 87 agrégats financiers OFGL",
        "level": "methodologie",
        "heavy": False,
    },
    "ofgl_formules": {
        "url": OFGL_EXPORT.format(ds="methodologie-ofgl-formules-des-agregats-financiers", fmt="json"),
        "out": DATA / "methodologie" / "ofgl-formules-agregats.json",
        "description": "Formules de calcul détaillées (par année × nomenclature × budget)",
        "level": "methodologie",
        "heavy": True,  # ~35 Mo
    },
    # Régions
    "ofgl_regions": {
        "url": OFGL_EXPORT.format(ds="ofgl-base-regions", fmt="json"),
        "out": DATA / "regions" / "ofgl-base-regions.json",
        "description": "Comptes des régions 2012-2024 (OFGL)",
        "level": "regions",
        "heavy": False,  # ~19 Mo
    },
    "regions_carto": {
        "url": OFGL_EXPORT.format(ds="donnees_carto_regions", fmt="json"),
        "out": DATA / "regions" / "regions-carto.json",
        "description": "Indicateurs cartographiques régions (ratios déjà calculés)",
        "level": "regions",
        "heavy": False,
    },
    "regions_svg": {
        "url": OFGL_EXPORT.format(ds="regions_formes_geo_svg", fmt="json"),
        "out": DATA / "regions" / "regions-svg.json",
        "description": "Contours SVG des régions",
        "level": "regions",
        "heavy": False,
    },
    # Départements
    "ofgl_departements": {
        "url": OFGL_EXPORT.format(ds="ofgl-base-departements", fmt="json"),
        "out": DATA / "departements" / "ofgl-base-departements.json",
        "description": "Comptes des départements 2012-2024 (OFGL)",
        "level": "departements",
        "heavy": True,  # ~277 Mo
    },
    "departements_carto": {
        "url": OFGL_EXPORT.format(ds="donnees_carto_departements", fmt="json"),
        "out": DATA / "departements" / "departements-carto.json",
        "description": "Indicateurs cartographiques départements (ratios déjà calculés)",
        "level": "departements",
        "heavy": False,
    },
    "departements_svg": {
        "url": OFGL_EXPORT.format(ds="departements_formes_geo_svg", fmt="json"),
        "out": DATA / "departements" / "departements-svg.json",
        "description": "Contours SVG des départements",
        "level": "departements",
        "heavy": False,
    },
    # Communes : la base brute (22M lignes) est trop volumineuse pour être
    # téléchargée en bloc. On utilise le dataset cartographique filtré par
    # agrégat (cf. SOURCES_COMMUNES_CARTO ci-dessous), un fichier par agrégat.
    "communes_svg_fra": {
        "url": (
            OFGL_EXPORT.format(ds="communes_formes_geo_svg", fmt="json")
            + "?" + urllib.parse.urlencode({"where": 'niveau_zoom="FRA"'})
        ),
        "out": DATA / "communes" / "communes-svg-FRA.json",
        "description": "Contours SVG des ~35 000 communes (niveau France entière)",
        "level": "communes",
        "heavy": False,  # ~32 Mo
    },
    "communes_disponibilite": {
        "url": OFGL_EXPORT.format(ds="disponibilite-des-comptes-des-communes", fmt="json"),
        "out": DATA / "communes" / "disponibilite-comptes-communes.json",
        "description": "Disponibilité des comptes par commune et par année 2012-2024",
        "level": "communes",
        "heavy": True,  # ~71 Mo
    },
}

# Indicateurs sélectionnés pour les tableaux de synthèse — ces noms
# correspondent aux agrégats officiels OFGL (champ `agregat`).
#
# Trois groupes :
#   - INDICATEURS_COMMUNS : disponibles aux 3 niveaux (régions, départements,
#     communes). Chargés systématiquement.
#   - INDICATEURS_SPECIFIQUES_<niveau> : pertinents uniquement pour ce niveau
#     (par exemple TICPE pour les régions, DMTO pour les départements, DETR
#     pour les communes).
#
# La fonction `indicateurs_pour_niveau()` ci-dessous compose la liste complète
# pour un niveau donné.

INDICATEURS_COMMUNS = [
    # Recettes
    "Recettes totales",
    "Recettes totales hors emprunts",
    "Recettes de fonctionnement",
    "Recettes d'investissement",
    "Recettes d'investissement hors emprunts",
    "Impôts et taxes",
    "Impôts locaux",
    "Autres impôts et taxes",
    "TVA",
    "CVAE",
    "Concours de l'Etat",
    "Dotation globale de fonctionnement",
    "Autres dotations de fonctionnement",
    "Autres dotations et subventions",
    "Subventions reçues et participations",
    "FCTVA",
    "Péréquations et compensations fiscales",
    "Ventes de biens et services",
    "Produit des cessions d'immobilisations",
    # Dépenses
    "Dépenses totales",
    "Dépenses totales hors remb",
    "Dépenses de fonctionnement",
    "Dépenses d'investissement",
    "Dépenses d'investissement hors remb",
    "Dépenses d'équipement",
    "Frais de personnel",
    "Achats et charges externes",
    "Dépenses d'intervention",
    "Subventions d'équipement versées",
    "Subventions aux personnes de droit privé",
    "Charges financières",
    # Solde / épargne
    "Epargne brute",
    "Epargne nette",
    "Epargne de gestion",
    "Capacité ou besoin de financement",
    # Dette
    "Encours de dette",
    "Annuité de la dette",
    "Flux net de dette",
    "Emprunts hors GAD",
    "Remboursements d'emprunts hors GAD",
    # Trésorerie
    "Fonds de roulement",
    "Variation du fonds de roulement",
    "Crédits de trésorerie",
    "Dépôts au Trésor",
]

# Présents dans les bases régions ET départements (souvent peu fournis pour
# les régions, sauf DMTO et fiscalité reversée). Pas applicables aux communes.
INDICATEURS_REG_ET_DEP = [
    # Recettes spécifiques (fiscalité partagée régions/départements)
    "DMTO avant péreq.",
    "DMTO après péreq.",
    "Attribution fonds de péreq. DMTO",
    "Prélèvement fonds de péreq. DMTO",
    "Fiscalité reversée",
    "TSCA",
    "CNSA",
    "FMDI",
    "Cartes grises",
    "DDEC",
    # Dépenses sociales (essentiellement départementales mais valeurs
    # parfois renseignées pour quelques régions DROM)
    "Allocations APA",
    "Allocations PCH",
    "Allocations RSA",
    "Frais d'hébergement",
    "Contributions aux SDIS",
    # Dette détaillée
    "Encours de dette - Dettes bancaires et assimilées",
    "Encours de dette - Dépôts et cautionnements reçus",
    "Fonds de soutien aux emprunts à risque",
]

INDICATEURS_SPECIFIQUES_REG = [
    "TICPE",
    "DRES",
    "Contributions aux organismes de transport",
]

INDICATEURS_SPECIFIQUES_DEP = [
    "Travaux en régie",
    "Epargne brute avant travaux en régie",
]

INDICATEURS_SPECIFIQUES_COM = [
    "DETR",
    "Taxe d'enlévement des ordures ménagères",
    "Versement mobilité",
]


def indicateurs_pour_niveau(niveau: str) -> list[str]:
    """Liste complète des indicateurs OFGL à charger pour un niveau donné."""
    base = list(INDICATEURS_COMMUNS)
    if niveau == "regions":
        return base + INDICATEURS_REG_ET_DEP + INDICATEURS_SPECIFIQUES_REG
    if niveau == "departements":
        return base + INDICATEURS_REG_ET_DEP + INDICATEURS_SPECIFIQUES_DEP
    if niveau == "communes":
        return base + INDICATEURS_SPECIFIQUES_COM
    raise ValueError(f"Niveau inconnu : {niveau}")


# Compatibilité avec l'ancien nom utilisé en plusieurs endroits du script.
INDICATEURS_SYNTHESE = INDICATEURS_COMMUNS

EXERCICE_SYNTHESE = "2024"

# Plage temporelle pour les synthèses multi-années.
# - Régions et départements : la base OFGL couvre 2012-2024 (13 ans).
# - Communes : 2017-2024 (8 ans). Les 3 dernières (2022-2024) viennent
#   du dataset cartographique `donnees_carto_communes`. Les 5 années
#   antérieures (2017-2021) sont récupérées par téléchargement ciblé
#   depuis la base brute `ofgl-base-communes` (un CSV par couple
#   {année × agrégat}, ~150 fichiers, ~1,5 Go au total).
ANNEES_REG_DEP = list(range(2012, 2025))
ANNEES_COMMUNES_HISTORIQUE = [2017, 2018, 2019, 2020, 2021]
ANNEES_COMMUNES_CARTO = [2022, 2023, 2024]
ANNEES_COMMUNES = ANNEES_COMMUNES_HISTORIQUE + ANNEES_COMMUNES_CARTO

# Carto communes : un fichier CSV par agrégat (filtre côté serveur).
# Ce découpage permet de rester sous la limite 100 Mo/fichier de GitHub Pages.
def _slug(s: str) -> str:
    s = s.lower()
    s = (s.replace("é", "e").replace("è", "e").replace("ê", "e")
          .replace("à", "a").replace("ô", "o").replace("'", "-"))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _communes_carto_url(agregat: str) -> str:
    where = f'agregat="{agregat}" AND type="Budget principal"'
    return (
        OFGL_EXPORT.format(ds="donnees_carto_communes", fmt="csv")
        + "?" + urllib.parse.urlencode({"where": where})
    )


def _communes_brut_url(agregat: str, year: int) -> str:
    """URL d'export CSV de la base brute communes pour une (année, agrégat,
    budget principal). Le champ `exer` est typé `date` côté OFGL, on doit
    donc utiliser la syntaxe `date'YYYY-01-01'`."""
    where = (
        f"exer=date'{year}-01-01' "
        f'AND type_de_budget="Budget principal" '
        f'AND agregat="{agregat}"'
    )
    return (
        OFGL_EXPORT.format(ds="ofgl-base-communes", fmt="csv")
        + "?" + urllib.parse.urlencode({"where": where})
    )


SOURCES_COMMUNES_CARTO = {
    f"communes_carto_{_slug(ag)}": {
        "url": _communes_carto_url(ag),
        "out": DATA / "communes" / "carto" / f"carto-communes-{_slug(ag)}.csv",
        "description": f"Carto communes — agrégat « {ag} » (budget principal, 2022-2024)",
        "level": "communes",
        "heavy": False,  # ~12 Mo chacun
        "agregat": ag,
    }
    for ag in indicateurs_pour_niveau("communes")
}


# Téléchargements historiques 2017-2021 (par année × agrégat) depuis la
# base brute. ~150 fichiers, ~12 Mo chacun (CSV de 35 000 lignes × ~30 colonnes).
# Marqués `heavy=True` car le total dépasse largement 50 Mo.
SOURCES_COMMUNES_HISTORIQUE = {
    f"communes_brut_{year}_{_slug(ag)}": {
        "url": _communes_brut_url(ag, year),
        "out": DATA / "communes" / "historique" / str(year)
                / f"carto-communes-{_slug(ag)}.csv",
        "description": (
            f"Base brute communes — agrégat « {ag} », exercice {year} "
            f"(budget principal)"
        ),
        "level": "communes",
        "heavy": True,
        "agregat": ag,
        "year": year,
    }
    for year in ANNEES_COMMUNES_HISTORIQUE
    for ag in indicateurs_pour_niveau("communes")
}


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def download(url: str, dest: Path) -> int:
    """Télécharge `url` vers `dest`. Retourne la taille en octets."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as f:
        while chunk := resp.read(64 * 1024):
            f.write(chunk)
    return dest.stat().st_size


def write_metadata(dest: Path, source_url: str, description: str,
                   downloaded_at: datetime | None = None) -> None:
    """Écrit un fichier `.meta.json` à côté du dataset pour la traçabilité."""
    when = downloaded_at or datetime.now(timezone.utc)
    meta = {
        "source_url": source_url,
        "description": description,
        "downloaded_at": when.isoformat(timespec="seconds"),
        "size_bytes": dest.stat().st_size,
    }
    dest.with_suffix(dest.suffix + ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fetch_one(name: str, src: dict, force: bool, skip_heavy: bool) -> None:
    if skip_heavy and src["heavy"]:
        print(f"  [skip-heavy] {name}")
        return
    out = src["out"]
    meta_path = out.with_suffix(out.suffix + ".meta.json")
    if out.exists() and not force:
        if not meta_path.exists():
            mtime = datetime.fromtimestamp(out.stat().st_mtime, tz=timezone.utc)
            write_metadata(out, src["url"], src["description"], downloaded_at=mtime)
            print(f"  [meta+]      {name} -> {out.relative_to(ROOT)} (meta créé)")
        else:
            print(f"  [exists]     {name} -> {out.relative_to(ROOT)}")
        return
    print(f"  [downloading]{name} ...", end=" ", flush=True)
    t0 = time.time()
    size = download(src["url"], out)
    write_metadata(out, src["url"], src["description"])
    print(f"{size:>12,} octets en {time.time()-t0:.1f}s".replace(",", " "))


def fetch_sources(force: bool, skip_heavy: bool, only: set[str] | None) -> None:
    all_sources = {
        **SOURCES,
        **SOURCES_COMMUNES_CARTO,
        **SOURCES_COMMUNES_HISTORIQUE,
    }
    for name, src in all_sources.items():
        if only and src.get("level") not in only and "base" not in only:
            continue
        _fetch_one(name, src, force=force, skip_heavy=skip_heavy)


# ---------------------------------------------------------------------------
# Synthèses régions / départements (depuis bases brutes JSON)
# ---------------------------------------------------------------------------

def synthese_from_base(base_path: Path, level: str, key_code: str, key_name: str,
                       extra_meta: list[str], out_csv: Path, out_json: Path) -> None:
    """Synthèse multi-années à partir de la base OFGL brute (régions, départements).

    Produit deux fichiers :

    - **JSON multi-années** (out_json) : format compact avec tous les exercices
      disponibles (2012-2024) pour chaque entité. Structure :
      ``{"years": [...], "indicators": [...], "entities": [{"code", "name",
      "meta", "values": {indicateur: [v_2012, ..., v_2024]}}]}``
      Permet au site web de naviguer dans le temps et d'afficher des
      sparklines d'évolution.

    - **CSV** (out_csv) : restreint à l'exercice de référence (EXERCICE_SYNTHESE)
      pour rester lisible par un humain ouvrant le fichier dans un tableur.
    """
    print(f"  [synthese]   {level} ({ANNEES_REG_DEP[0]}-{ANNEES_REG_DEP[-1]}) ...", end=" ", flush=True)
    t0 = time.time()

    base = json.loads(base_path.read_text(encoding="utf-8"))
    rows_filtre = [
        r for r in base
        if r.get("type_de_budget") == "Budget principal"
        and r.get("exer") and int(r.get("exer")) in ANNEES_REG_DEP
    ]

    # Indexation : (code, year) -> {agregat: {montant, eur_hab, ptot}}
    par_coll_year: dict[tuple, dict] = defaultdict(dict)
    meta: dict[str, dict] = {}
    pop_by_coll_year: dict[tuple, int] = {}
    for r in rows_filtre:
        code = r.get(key_code)
        year = int(r.get("exer"))
        par_coll_year[(code, year)][r.get("agregat")] = {
            "montant": r.get("montant"),
            "eur_hab": r.get("euros_par_habitant"),
        }
        # Une seule ligne meta par collectivité (la dernière vue)
        meta[code] = {k: r.get(k) for k in [key_name] + extra_meta}
        ptot = r.get("ptot")
        if ptot:
            pop_by_coll_year[(code, year)] = ptot

    indicateurs = indicateurs_pour_niveau(level)

    # === Synthèse multi-années (JSON compact) ===
    codes = sorted({c for c, _ in par_coll_year.keys()})
    entities_multi = []
    for code in codes:
        m = meta[code]
        # Population par année (et fallback sur la dernière connue)
        pop_serie = [pop_by_coll_year.get((code, y)) for y in ANNEES_REG_DEP]

        values_by_indic: dict[str, list] = {}
        for indic in indicateurs:
            serie = []
            for y in ANNEES_REG_DEP:
                ag = par_coll_year.get((code, y), {})
                v = ag.get(indic, {}).get("eur_hab")
                # Fidélité OFGL stricte : on stocke la valeur exacte sans
                # arrondi (OFGL publie parfois 17 décimales d'artefact
                # flottant, ex: montant÷population — on les préserve).
                serie.append(v)
            values_by_indic[indic] = serie

        # Ratios calculés par année (Taux épargne brute, Capacité de
        # désendettement). Ces 2 indicateurs sont DÉRIVÉS (pas publiés
        # directement par OFGL). On garde la précision native du flottant
        # pour ne pas perdre d'information.
        tx_eb_serie, cd_serie = [], []
        for y in ANNEES_REG_DEP:
            ag = par_coll_year.get((code, y), {})
            rf = ag.get("Recettes de fonctionnement", {}).get("montant")
            eb = ag.get("Epargne brute", {}).get("montant")
            enc = ag.get("Encours de dette", {}).get("montant")
            tx_eb_serie.append((100 * eb / rf) if rf and eb else None)
            cd_serie.append((enc / eb) if eb and enc and eb > 0 else None)
        values_by_indic["Taux epargne brute (%)"] = tx_eb_serie
        values_by_indic["Capacite desendettement (annees)"] = cd_serie

        entities_multi.append({
            "code": code,
            "name": m.get(key_name),
            "meta": {k: m.get(k) for k in extra_meta},
            "population": pop_serie,
            "values": values_by_indic,
        })

    payload_multi = {
        "years": ANNEES_REG_DEP,
        "indicators": list(values_by_indic.keys()) if entities_multi else indicateurs,
        "entities": entities_multi,
    }
    out_json.write_text(
        json.dumps(payload_multi, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # === CSV (lisible humain), restreint à l'exercice de référence ===
    ref_year = int(EXERCICE_SYNTHESE)
    if ref_year in ANNEES_REG_DEP:
        ref_idx = ANNEES_REG_DEP.index(ref_year)
        csv_rows = []
        for ent in entities_multi:
            row = {key_code: ent["code"], key_name: ent["name"]}
            row.update(ent["meta"])
            row["population"] = ent["population"][ref_idx]
            for indic, serie in ent["values"].items():
                row[indic] = serie[ref_idx]
            csv_rows.append(row)

        if csv_rows:
            with out_csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
                w.writeheader()
                w.writerows(csv_rows)

    print(f"{len(entities_multi)} entités × {len(ANNEES_REG_DEP)} ans en {time.time()-t0:.1f}s")


# ---------------------------------------------------------------------------
# Synthèse communes (assemble les 10 CSV cartographiques)
# ---------------------------------------------------------------------------

def synthese_communes() -> None:
    """Synthèse communes multi-années (2017-2024) à partir des CSV.

    Deux sources :
    - Les CSV cartographiques (`donnees_carto_communes`) couvrent 2022-2024
      avec les colonnes `m_<year>` et `m_hab_<year>`.
    - Les CSV de la base brute (`ofgl-base-communes`) téléchargés ciblement
      par couple {année, agrégat} couvrent 2017-2021. Une ligne par commune
      avec colonnes `montant` et `euros_par_habitant`.

    On fusionne les deux sources dans une seule série temporelle 2017-2024.
    """
    print(f"  [synthese]   communes ({ANNEES_COMMUNES[0]}-{ANNEES_COMMUNES[-1]}) ...", end=" ", flush=True)
    t0 = time.time()

    # Index population depuis la liste INSEE (data/communes.json)
    pop_by_insee: dict[str, int] = {}
    communes_liste_path = DATA / "communes.json"
    if communes_liste_path.exists():
        for c in json.loads(communes_liste_path.read_text(encoding="utf-8")):
            if c.get("population") is not None:
                pop_by_insee[c["code"]] = c["population"]

    # par_com[insee][ag_name][year] -> {montant, eur_hab}
    par_com: dict[str, dict] = defaultdict(lambda: defaultdict(dict))
    meta: dict[str, dict] = {}

    # Normalise un code INSEE pour qu'il fasse 5 caractères (padding à gauche
    # avec '0'). Le carto OFGL a parfois des INSEE mal formatés ("5024" au
    # lieu de "05024" pour Valdoule, par exemple), ce qui crée des doublons
    # quand l'autre source utilise le format correct.
    def _normalize_insee(s: str | None) -> str | None:
        if not s:
            return s
        s = str(s)
        return s.zfill(5) if s.isdigit() else s

    # === Lecture des CSV cartographiques 2022-2024 EN PREMIER ===
    # On lit le carto avant l'historique pour que le `meta[insee]` (qui
    # contient notamment le SIREN) reflète l'état **actuel** de la commune.
    # Pour les communes qui ont fusionné entre 2017 et 2024 (ex: Le Chesnay
    # devenu Le Chesnay-Rocquencourt), le SIREN actuel est nécessaire pour
    # la jointure ultérieure avec le SVG — qui utilise les SIRENs courants.
    def _store_value(insee, ag_name, year, montant, eur_hab):
        """Stocke (montant, eur_hab) sans écraser une valeur valide existante
        par une valeur null. Utile quand l'OFGL a plusieurs lignes pour la
        même commune (par ex. avec des INSEE différents qui se normalisent
        au même code) — on garde la ligne porteuse de données."""
        existing = par_com[insee][ag_name].get(year)
        if existing is None or (existing.get("montant") is None and montant is not None):
            par_com[insee][ag_name][year] = {"montant": montant, "eur_hab": eur_hab}

    for src in SOURCES_COMMUNES_CARTO.values():
        ag_name = src["agregat"]
        with src["out"].open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                insee = _normalize_insee(r["insee"])
                for year in ANNEES_COMMUNES_CARTO:
                    m_key = f"m_{year}"
                    h_key = f"m_hab_{year}"
                    montant = float(r[m_key]) if r.get(m_key) else None
                    eur_hab = float(r[h_key]) if r.get(h_key) else None
                    _store_value(insee, ag_name, year, montant, eur_hab)
                if insee not in meta:
                    meta[insee] = {
                        "siren": r.get("siren"),
                        "nom": r.get("nom"),
                        "nom_dep": r.get("nom_dep"),
                        "nom_reg": r.get("nom_reg"),
                        "nom_gfp": r.get("nom_gfp"),
                        # `dep_code` n'est pas exposé dans le carto OFGL ; on
                        # le complétera depuis l'historique ci-dessous, ou
                        # par lookup nom_dep ↔ code départements.
                        "dep_code": None,
                    }

    # === Lecture des CSV historiques 2017-2021 ENSUITE ===
    # On ne touche PAS aux entrées meta déjà existantes (`if insee not in meta`)
    # pour conserver le SIREN actuel — sauf le `dep_code` qu'on complète
    # systématiquement quand il manque (la base brute l'a, le carto non).
    for src in SOURCES_COMMUNES_HISTORIQUE.values():
        ag_name = src["agregat"]
        year = src["year"]
        path = src["out"]
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                insee = _normalize_insee(r.get("insee") or r.get("com_code"))
                if not insee:
                    continue
                montant = float(r["montant"]) if r.get("montant") else None
                eur_hab = float(r["euros_par_habitant"]) if r.get("euros_par_habitant") else None
                _store_value(insee, ag_name, year, montant, eur_hab)
                dep_code = r.get("dep_code")
                if insee not in meta:
                    meta[insee] = {
                        "siren": r.get("siren"),
                        "nom": r.get("com_name"),
                        "nom_dep": r.get("dep_name"),
                        "nom_reg": r.get("reg_name"),
                        "nom_gfp": r.get("epci_name"),
                        "dep_code": dep_code,
                    }
                elif not meta[insee].get("dep_code") and dep_code:
                    # Compléter dep_code si manquant (cas d'une commune
                    # initialisée depuis le carto qui n'expose pas dep_code)
                    meta[insee]["dep_code"] = dep_code

    indicateurs = indicateurs_pour_niveau("communes")

    # === Synthèse multi-années (JSON compact) ===
    entities_multi = []
    for insee in sorted(par_com.keys()):
        ag_year = par_com[insee]
        m = meta[insee]

        values_by_indic: dict[str, list] = {}
        for indic in indicateurs:
            serie = []
            for year in ANNEES_COMMUNES:
                v = ag_year.get(indic, {}).get(year, {}).get("eur_hab")
                # Fidélité OFGL stricte : on stocke la valeur exacte sans
                # arrondi (cf. commentaire identique côté régions/dpts).
                serie.append(v)
            values_by_indic[indic] = serie

        # Ratios calculés par année — précision native conservée.
        tx_eb_serie, cd_serie = [], []
        for year in ANNEES_COMMUNES:
            rf = ag_year.get("Recettes de fonctionnement", {}).get(year, {}).get("montant")
            eb = ag_year.get("Epargne brute", {}).get(year, {}).get("montant")
            enc = ag_year.get("Encours de dette", {}).get(year, {}).get("montant")
            tx_eb_serie.append((100 * eb / rf) if rf and eb else None)
            cd_serie.append((enc / eb) if eb and enc and eb > 0 else None)
        values_by_indic["Taux epargne brute (%)"] = tx_eb_serie
        values_by_indic["Capacite desendettement (annees)"] = cd_serie

        entities_multi.append({
            "insee": insee,
            "nom": m["nom"],
            "siren": m["siren"],
            "dep_code": m.get("dep_code"),
            "nom_dep": m["nom_dep"],
            "nom_reg": m["nom_reg"],
            "nom_gfp": m["nom_gfp"],
            "population": pop_by_insee.get(insee),
            "values": values_by_indic,
        })

    payload_multi = {
        "years": ANNEES_COMMUNES,
        "indicators": list(values_by_indic.keys()) if entities_multi else indicateurs,
        "communes": entities_multi,
    }

    out_csv = DATA / "communes" / "synthese-communes-2024.csv"
    out_json = DATA / "communes" / "synthese-communes-2024.json"

    out_json.write_text(
        json.dumps(payload_multi, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # === CSV (lisible humain), restreint à l'exercice 2024 ===
    ref_year = int(EXERCICE_SYNTHESE)
    if ref_year in ANNEES_COMMUNES:
        ref_idx = ANNEES_COMMUNES.index(ref_year)
        csv_rows = []
        for ent in entities_multi:
            row: dict = {"insee": ent["insee"], "nom": ent["nom"],
                         "siren": ent["siren"], "nom_dep": ent["nom_dep"],
                         "nom_reg": ent["nom_reg"], "nom_gfp": ent["nom_gfp"],
                         "population": ent["population"]}
            for indic, serie in ent["values"].items():
                row[indic] = serie[ref_idx]
            csv_rows.append(row)

        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)

    print(f"{len(entities_multi)} communes × {len(ANNEES_COMMUNES)} ans en {time.time()-t0:.1f}s")

    # Découpage par département pour le drill-down côté site
    split_communes_by_departement(entities_multi)

    # Synthèse minimale (compacte) pour le calque décoratif communes du site
    write_communes_decoratif(entities_multi)

    # Métadonnées (nom, INSEE, dep_code, population) indexées comme le
    # décoratif, pour le leaderboard national côté site.
    write_communes_meta(entities_multi)

    # Taux d'imposition (REI DGFIP via OFGL, 2023-2024). Fusionnés dans
    # les fichiers ci-dessus en post-process. Le script est autonome —
    # on l'invoque via subprocess pour conserver son isolement (un cache
    # de téléchargement séparé, ses propres options CLI, etc.).
    print("  [synthese]   taux d'imposition (REI 2023-2024) ...")
    import subprocess
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_taux_communes.py")],
        check=True,
    )

    # Niveau EPCI (ofgl-base-gfp + by-epci/_index pour le drill-down). Aussi
    # idempotent et autonome ; produit aussi le siren_epci dans meta-communes
    # nécessaire à la coloration overview côté site.
    print("  [synthese]   intercommunalités (ofgl-base-gfp) ...")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_epci.py")],
        check=True,
    )

    # Taux d'imposition EPCI (REI 2023-2024). Doit s'exécuter APRÈS
    # fetch_epci.py car il enrichit la synthese-intercommunalites déjà créée.
    print("  [synthese]   taux d'imposition EPCI (REI 2023-2024) ...")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_taux_epci.py")],
        check=True,
    )


def write_communes_decoratif(synthese_rows: list[dict]) -> None:
    """Produit `data/communes/decoratif-communes-2024.json`, un fichier
    compact dédié au calque décoratif du niveau communes côté site web.

    Format multi-années (A1) : pour chaque commune,
      ``[d, [serie_ind1, serie_ind2, ..., serie_indN]]``
    où chaque ``serie_indK`` est un tableau ``[v_2022, v_2023, v_2024]``
    (les contours géographiques sont stockés une seule fois, seules les
    valeurs sont dupliquées par année).

    Le SIREN n'est pas transporté (pas d'interaction sur le calque, qui
    a `pointer-events: none`). L'identifiant est implicite (position).

    Aucune perte d'information : toutes les valeurs sont également présentes
    dans synthese-communes-2024.json et data/communes/by-dep/.
    """
    print("  [synthese]   communes -> decoratif lazy (paths + values/) ...",
          end=" ", flush=True)
    t0 = time.time()

    INDICATEURS_DECORATIF = indicateurs_pour_niveau("communes") + [
        "Taux epargne brute (%)",
        "Capacite desendettement (annees)",
    ]

    # Index des paths SVG par SIREN (le SIREN sert seulement à la jointure
    # interne, il n'est PAS exporté dans le fichier final)
    svg_path = DATA / "communes" / "communes-svg-FRA.json"
    svg_data = json.loads(svg_path.read_text(encoding="utf-8"))
    svg_by_siren: dict[str, str] = {}
    for s in svg_data:
        if s.get("niveau_zoom") == "FRA":
            svg_by_siren[str(s.get("data_fill_id"))] = s.get("d")

    # Construction parallèle des paths + valeurs par indicateur.
    # Architecture lazy-loading :
    #   - decoratif-paths-2024.json : juste les contours SVG (~4 Mo après
    #     simplification, chargé une fois au démarrage de l'application)
    #   - decoratif-values/{slug}.json : un fichier par indicateur
    #     (~1-3 Mo chacun, chargé à la demande quand l'utilisateur
    #     sélectionne l'indicateur dans la liste)
    #   - decoratif-values/_index.json : mapping nom indicateur → slug
    paths: list[str] = []
    values_by_indicator: dict[str, list] = {ind: [] for ind in INDICATEURS_DECORATIF}

    for row in synthese_rows:
        siren = str(row.get("siren") or "")
        d = svg_by_siren.get(siren)
        if not d:
            continue
        # Contours collectés en pleine précision ici ; la simplification +
        # arrondi (présentation) est appliquée plus bas, uniquement au fichier
        # servi. Le .full.json conserve la pleine précision OFGL.
        paths.append(d)
        values_by_indic = row.get("values", {})
        for ind in INDICATEURS_DECORATIF:
            values_by_indicator[ind].append(
                values_by_indic.get(ind, [None] * len(ANNEES_COMMUNES))
            )

    # 1. Fichier paths-only (chargé une seule fois par session). Deux versions :
    #    - .full.json : pleine précision OFGL (2 décimales). Source de build
    #      réajustable par optimize_decoratif_paths.py ; NON déployée.
    #    - .json (servi) : géométrie simplifiée + 1 décimale, ~5× plus léger.
    #    simplify_svg_path utilise les mêmes réglages que le script autonome,
    #    donc fetch_all et optimize_decoratif_paths produisent un état cohérent.
    from optimize_decoratif_paths import simplify_svg_path

    full_out = DATA / "communes" / "decoratif-paths-2024.full.json"
    full_out.write_text(
        json.dumps({"years": ANNEES_COMMUNES, "paths": paths},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    paths_payload = {
        "years": ANNEES_COMMUNES,
        "paths": [simplify_svg_path(d) for d in paths],
    }
    paths_out = DATA / "communes" / "decoratif-paths-2024.json"
    paths_out.write_text(
        json.dumps(paths_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # 2. Fichiers values-par-indicateur (chargés à la demande)
    values_dir = DATA / "communes" / "decoratif-values"
    values_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}
    for ind, values in values_by_indicator.items():
        slug = _slug_indicator(ind)
        index[ind] = slug
        ind_payload = {
            "indicator": ind,
            "years": ANNEES_COMMUNES,
            "values": values,
        }
        (values_dir / f"{slug}.json").write_text(
            json.dumps(ind_payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    # 3. Index : mapping nom → slug
    (values_dir / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    paths_mb = paths_out.stat().st_size / 1024 / 1024
    total_values_mb = sum(
        (values_dir / f"{slug}.json").stat().st_size for slug in index.values()
    ) / 1024 / 1024
    print(
        f"{len(paths)} communes × {len(ANNEES_COMMUNES)} ans en "
        f"{time.time()-t0:.1f}s "
        f"(paths {paths_mb:.1f} Mo + {len(index)} indicateurs × ~{total_values_mb/max(len(index),1):.2f} Mo "
        f"= {total_values_mb:.1f} Mo total)"
    )


def _slug_indicator(name: str) -> str:
    """Slug stable pour les noms d'indicateurs (utilisé pour les noms de
    fichiers `decoratif-values/{slug}.json`). Translittération ASCII +
    minuscules + tirets ; tronqué à 100 caractères pour rester sous la
    limite MAX_PATH Windows."""
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
        # autres caractères ignorés
    slug = "".join(out_chars)
    # Compresser les tirets consécutifs
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    if len(slug) > 100:
        slug = slug[:100].rstrip("-")
    return slug


def write_communes_meta(synthese_rows: list[dict]) -> None:
    """Produit `data/communes/meta-communes-2024.json`, un fichier light
    avec un mapping ``position dans le décoratif`` → ``[nom, insee, dep_code,
    dep_name, population]``.

    Indexation strictement alignée sur ``decoratif-communes-2024.json`` :
    mêmes filtres, même ordre d'itération sur ``synthese_rows``. Le site
    charge ce fichier en arrière-plan pour pouvoir afficher un leaderboard
    national lisible des communes (le décoratif lui-même n'embarque pas
    les noms pour rester compact).

    Taille typique : ~1.5 Mo brut, ~500 Ko gzippé pour 35 000 communes.
    """
    print("  [synthese]   communes -> meta léger     ...", end=" ", flush=True)
    t0 = time.time()

    svg_path = DATA / "communes" / "communes-svg-FRA.json"
    svg_data = json.loads(svg_path.read_text(encoding="utf-8"))
    svg_sirens: set[str] = {
        str(s.get("data_fill_id"))
        for s in svg_data
        if s.get("niveau_zoom") == "FRA"
    }

    communes_meta: list[list] = []
    for row in synthese_rows:
        siren = str(row.get("siren") or "")
        if siren not in svg_sirens:
            continue
        communes_meta.append([
            row.get("nom") or "",
            row.get("insee") or "",
            row.get("dep_code") or "",
            row.get("nom_dep") or "",
            row.get("population"),
        ])

    payload = {
        "schema": ["nom", "insee", "dep_code", "dep_name", "population"],
        "communes": communes_meta,
    }
    out_path = DATA / "communes" / "meta-communes-2024.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"{len(communes_meta)} communes en {time.time()-t0:.1f}s "
        f"({out_path.stat().st_size/1024/1024:.2f} Mo)"
    )


def split_communes_by_departement(synthese_rows: list[dict]) -> None:
    """Pré-découpe les communes par département pour le drill-down du site.

    Format multi-années : chaque commune contient une entrée ``data`` avec
    un dictionnaire ``values`` indexant les indicateurs par leur série
    temporelle ``[v_2022, v_2023, v_2024]``. Le payload de chaque
    département expose en plus les champs ``years`` et ``indicators`` pour
    faciliter l'accès côté JS.

    Aucune perte d'information : chaque commune est toujours présente avec
    son contour SVG complet (précision originale, contrairement au calque
    décoratif qui réduit à 1 décimale).
    """
    print("  [synthese]   communes -> by-dep ...", end=" ", flush=True)
    t0 = time.time()

    # Mappings INSEE/SIREN -> code département depuis la liste INSEE officielle.
    # On a besoin des DEUX clés car certaines communes nouvelles utilisent un
    # INSEE différent côté OFGL (l'ancien code de la commune absorbante)
    # alors que leur SIREN est bien le SIREN actuel. Ex: Conques-en-Rouergue
    # est à l'INSEE 12218 dans la liste INSEE mais 12076 dans l'OFGL — par
    # contre le SIREN 200055929 est commun aux deux sources.
    dep_by_insee: dict[str, str] = {}
    dep_by_siren: dict[str, str] = {}
    nom_by_insee: dict[str, str] = {}
    communes_liste_path = DATA / "communes.json"
    if communes_liste_path.exists():
        for c in json.loads(communes_liste_path.read_text(encoding="utf-8")):
            cd = c.get("codeDepartement", "")
            if c.get("code"):
                dep_by_insee[c["code"]] = cd
                nom_by_insee[c["code"]] = c.get("nom", "")
            if c.get("siren"):
                dep_by_siren[c["siren"]] = cd

    # Chargement du SVG des communes (~30 Mo) — index par SIREN
    svg_data = json.loads(
        (DATA / "communes" / "communes-svg-FRA.json").read_text(encoding="utf-8")
    )
    svg_by_siren: dict[str, dict] = {}
    for s in svg_data:
        if s.get("niveau_zoom") == "FRA":
            svg_by_siren[str(s.get("data_fill_id"))] = s

    # Mapping département (code) -> nom depuis la synthèse départements
    # (la synthèse est désormais multi-années avec une structure {entities: [...]})
    dep_name_by_code: dict[str, str] = {}
    dep_synth_path = DATA / "departements" / "synthese-departements-2024.json"
    if dep_synth_path.exists():
        payload = json.loads(dep_synth_path.read_text(encoding="utf-8"))
        for r in payload.get("entities", []):
            dep_name_by_code[r["code"]] = r["name"]

    # Regroupement par département : on associe chaque commune (data + svg)
    # à son code département. On crée également quelques regroupements
    # "fonctionnels" pour caler avec le SVG des départements affiché côté
    # site web :
    #
    #   - Métropole de Lyon (691) : dans la liste INSEE, ses ~59 communes
    #     ont codeDepartement="69" (comme le reste du Rhône). On les isole
    #     via leur EPCI (nom_gfp = "Métropole de Lyon") pour produire un
    #     691.json séparé. Le 69.json garde alors uniquement le Rhône hors
    #     Métropole.
    #   - Alsace (67A) : alias regroupant 67 + 68 pour matcher le contour
    #     unique de la Collectivité européenne d'Alsace dans le SVG.
    #   - 67.json et 68.json sont conservés tels quels pour la traçabilité.
    by_dep: dict[str, list[dict]] = defaultdict(list)
    for row in synthese_rows:
        insee = row.get("insee")
        siren = row.get("siren")
        # Source du code département, par ordre de fiabilité décroissante :
        #   1. dep_code stocké dans la synthèse (issu de la base brute OFGL)
        #   2. lookup par SIREN dans la liste INSEE (geo.api.gouv.fr)
        #   3. lookup par INSEE dans la liste INSEE
        # Le 1er est essentiel pour les communes qui ont fusionné/disparu de
        # la liste INSEE mais existent toujours côté OFGL (ex: Saint-Genis-du-Bois).
        code_dep = (
            row.get("dep_code")
            or dep_by_siren.get(str(siren))
            or dep_by_insee.get(insee)
        )
        if not code_dep:
            continue
        svg_entry = svg_by_siren.get(str(siren))
        if not svg_entry:
            continue
        commune = {
            "data": row,
            "svg": {
                "d": svg_entry["d"],
                "data_fill_id": svg_entry.get("data_fill_id"),
                "nom_com": svg_entry.get("nom_com"),
                "x_min": svg_entry.get("x_min"),
                "x_max": svg_entry.get("x_max"),
                "y_min": svg_entry.get("y_min"),
                "y_max": svg_entry.get("y_max"),
            },
        }

        # Cas particulier : Métropole de Lyon → code fonctionnel 691
        if code_dep == "69" and row.get("nom_gfp") == "Métropole de Lyon":
            by_dep["691"].append(commune)
        else:
            by_dep[code_dep].append(commune)

    # Alias Alsace (67A) : union 67 + 68
    if "67" in by_dep or "68" in by_dep:
        by_dep["67A"] = by_dep.get("67", []) + by_dep.get("68", [])

    # Écriture des fichiers par département + de l'index global
    out_dir = DATA / "communes" / "by-dep"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for code_dep, communes in sorted(by_dep.items()):
        if not communes:
            continue
        # Bounding box englobant toutes les communes du dpt
        x_min = min(c["svg"]["x_min"] for c in communes if c["svg"]["x_min"] is not None)
        x_max = max(c["svg"]["x_max"] for c in communes if c["svg"]["x_max"] is not None)
        y_min = min(c["svg"]["y_min"] for c in communes if c["svg"]["y_min"] is not None)
        y_max = max(c["svg"]["y_max"] for c in communes if c["svg"]["y_max"] is not None)
        bbox = {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max}

        # Nom du département pour le payload : on essaie la synthèse OFGL,
        # sinon on prend le nom de l'EPCI dominant ou un libellé par défaut.
        name = dep_name_by_code.get(code_dep, "")
        if not name and communes:
            # Pour les dpts sans entrée dans synthese-departements (CTU comme
            # 2A/2B/972/973) : utiliser nom_dep de la première commune
            name = communes[0]["data"].get("nom_dep", "") or ""

        # Liste des indicateurs présents dans les valeurs (depuis la première
        # commune qui en a — toutes ont la même liste).
        indicators_list = []
        for c in communes:
            v = c["data"].get("values")
            if v:
                indicators_list = list(v.keys())
                break

        payload = {
            "dep_code": code_dep,
            "dep_name": name,
            "bbox": bbox,
            "years": ANNEES_COMMUNES,
            "indicators": indicators_list,
            "communes": communes,
        }
        out_path = out_dir / f"{code_dep}.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

        index.append({
            "dep_code": code_dep,
            "dep_name": name,
            "count": len(communes),
            "bbox": bbox,
            "size_bytes": out_path.stat().st_size,
        })

    (out_dir / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    total_size = sum(item["size_bytes"] for item in index)
    print(
        f"{len(by_dep)} departements en {time.time()-t0:.1f}s "
        f"(total {total_size/1024/1024:.1f} Mo)"
    )


def build_syntheses(only: set[str] | None) -> None:
    if not only or "regions" in only:
        synthese_from_base(
            base_path=DATA / "regions" / "ofgl-base-regions.json",
            level="regions",
            key_code="reg_code",
            key_name="reg_name",
            extra_meta=[],
            out_csv=DATA / "regions" / "synthese-regions-2024.csv",
            out_json=DATA / "regions" / "synthese-regions-2024.json",
        )
    if not only or "departements" in only:
        synthese_from_base(
            base_path=DATA / "departements" / "ofgl-base-departements.json",
            level="departements",
            key_code="dep_code",
            key_name="dep_name",
            extra_meta=["reg_code", "reg_name", "categ", "dep_status", "outre_mer"],
            out_csv=DATA / "departements" / "synthese-departements-2024.csv",
            out_json=DATA / "departements" / "synthese-departements-2024.json",
        )
    if not only or "communes" in only:
        synthese_communes()


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true",
                        help="re-télécharge même les fichiers déjà présents")
    parser.add_argument("--skip-heavy", action="store_true",
                        help="saute les fichiers > 50 Mo")
    parser.add_argument(
        "--only", nargs="+",
        choices=["regions", "departements", "communes", "methodologie"],
        help="ne traite que ces niveaux",
    )
    parser.add_argument("--no-synthese", action="store_true",
                        help="ne régénère pas les fichiers de synthèse")
    args = parser.parse_args()

    only = set(args.only) if args.only else None

    sys.stdout.reconfigure(encoding="utf-8")
    print("=== Téléchargement des sources ===")
    fetch_sources(force=args.force, skip_heavy=args.skip_heavy, only=only)

    if not args.no_synthese:
        print("\n=== Génération des synthèses ===")
        build_syntheses(only=only)

    print("\nTerminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
