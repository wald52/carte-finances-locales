"""Télécharge et synthétise les données financières des EPCI (groupements à
fiscalité propre) à partir d'OFGL.

Approche A (cf. discussion concept) : pas de reconstruction géographique
des contours EPCI — la cartographie reste basée sur les 35k communes,
coloriées en mode « niveau intercommunalités » selon la valeur de leur EPCI
parent. Le drill-down vers une EPCI montre ses communes membres avec leurs
**propres** données financières (zoom).

Données extraites uniquement depuis ``ofgl-base-gfp`` (zéro reconstruction
à partir des chiffres communaux — engagement « données brutes »).

Fichiers produits :
  - data/intercommunalites/ofgl-base-gfp.json   : cache brut OFGL
  - data/intercommunalites/synthese-intercommunalites-2024.json
      Format multi-années 2017-2024, 1 entrée par EPCI avec series par
      indicateur (en €/hab). Schéma équivalent à
      synthese-departements-2024.json.
  - data/intercommunalites/by-epci/{siren}.json : pour le drill-down
      Liste des communes membres avec leurs SVG paths et données propres
      (réutilise les by-dep existants en filtrant par siren_epci).
  - data/intercommunalites/by-epci/_index.json : index pour le drill-down
      Pour chaque EPCI : siren, nom, bbox, départements principaux, nb
      communes membres.

Effet de bord : ajoute aussi le `siren_epci` à `data/communes/meta-communes-2024.json`
(indexé positionnellement, comme les autres champs) — nécessaire au site
pour faire le lookup commune -> EPCI lors de la coloration overview.

Usage : ``python scripts/fetch_epci.py``  (idempotent ; --force pour
re-télécharger).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INTERCO_DIR = DATA / "intercommunalites"
BY_EPCI_DIR = INTERCO_DIR / "by-epci"
BY_REGION_DIR = INTERCO_DIR / "by-region"

OFGL_EXPORT = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{ds}/exports/{fmt}"
GEO_API = "https://geo.api.gouv.fr"

# Période couverte par ofgl-base-gfp (cohérente avec ofgl-base-communes).
ANNEES_EPCI = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]


def download_commune_epci_mapping(force: bool = False) -> Path:
    """Télécharge `code commune INSEE -> code SIREN EPCI` depuis geo.api.gouv.fr.
    ~3 Mo brut, ne nécessite que `fields=code,codeEpci`.

    Pourquoi geo.api ? Le fichier OFGL `ofgl-base-communes` contient bien
    `epci_code`, mais OFGL ne l'expose pas dans les fichiers déjà téléchargés
    pour la synthèse (carto + historique). geo.api fournit l'info en une
    requête légère, à jour, et au format INSEE 5 chiffres + SIREN 9 chiffres."""
    INTERCO_DIR.mkdir(parents=True, exist_ok=True)
    out = INTERCO_DIR / "communes-epci-mapping.json"
    if out.exists() and not force:
        print(f"  [epci]  mapping commune->EPCI : cache ({out.stat().st_size//1024} Ko)")
        return out
    url = f"{GEO_API}/communes?fields=code,codeEpci&format=json"
    print(f"  [epci]  téléchargement {url} ...", end=" ", flush=True)
    t0 = time.time()
    urllib.request.urlretrieve(url, out)
    print(f"{out.stat().st_size//1024} Ko en {time.time()-t0:.1f}s")
    return out


def load_commune_epci_mapping(path: Path) -> dict[str, str]:
    """Renvoie { insee_padded : siren_epci }."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for r in raw:
        code = r.get("code")
        epci = r.get("codeEpci")
        if code and epci:
            mapping[code] = epci
    return mapping


def download_ofgl_base_gfp(force: bool = False) -> Path:
    """Télécharge le CSV complet de ofgl-base-gfp (filtré au Budget Principal,
    sinon le volume est ingérable). Le format CSV est ~10× plus compact que
    le JSON pour ce dataset."""
    INTERCO_DIR.mkdir(parents=True, exist_ok=True)
    out = INTERCO_DIR / "ofgl-base-gfp.csv"
    if out.exists() and not force:
        print(f"  [epci]  cache : {out.name} ({out.stat().st_size//1024//1024} Mo)")
        return out

    # Pas de filtre côté API (le serveur OFGL refuse certains WHERE complexes
    # sur l'export complet). On filtre côté Python.
    url = OFGL_EXPORT.format(ds="ofgl-base-gfp", fmt="csv")
    print(f"  [epci]  téléchargement {url} ...", end=" ", flush=True)
    t0 = time.time()
    urllib.request.urlretrieve(url, out)
    mb = out.stat().st_size / 1024 / 1024
    print(f"{mb:.1f} Mo en {time.time()-t0:.1f}s")
    return out


def build_synthese(csv_path: Path) -> tuple[list[dict], dict[str, dict]]:
    """Parse le CSV ofgl-base-gfp et construit la synthese multi-années.

    Retourne (liste d'entités EPCI, dict siren -> metadata).
    """
    # series[siren][indicator][year] = {montant, eur_hab}
    series: dict[str, dict[str, dict[int, dict]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    meta: dict[str, dict] = {}
    population_by_year: dict[str, dict[int, int]] = defaultdict(dict)
    seen_agregats: set[str] = set()

    print(f"  [epci]  parsing {csv_path.name} ...", end=" ", flush=True)
    t0 = time.time()
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        # OFGL utilise ';' comme délimiteur
        reader = csv.DictReader(fh, delimiter=";")
        n = 0
        for row in reader:
            n += 1
            # On ne garde que le Budget Principal (les budgets annexes sont
            # exclus pour éviter les doubles comptages : équivalent à ce
            # qu'on fait déjà pour les départements/régions).
            if row.get("type_de_budget") != "Budget principal":
                continue
            siren = (row.get("siren") or "").strip()
            if not siren:
                continue
            try:
                # Le champ "exer" est au format "YYYY-01-01"
                exer = row.get("exer", "")[:4]
                year = int(exer)
            except ValueError:
                continue
            if year not in ANNEES_EPCI:
                continue

            agregat = row.get("agregat") or ""
            if not agregat:
                continue
            seen_agregats.add(agregat)

            def _f(s):
                if s is None or s == "":
                    return None
                try:
                    return float(s)
                except ValueError:
                    return None

            montant = _f(row.get("montant"))
            eur_hab = _f(row.get("euros_par_habitant"))
            series[siren][agregat][year] = {"montant": montant, "eur_hab": eur_hab}

            # Population (peut varier d'une année à l'autre)
            pop = row.get("ptot")
            if pop and pop.strip().isdigit():
                population_by_year[siren][year] = int(pop)

            # Métadonnées (on prend la dernière vue, plus à jour)
            if siren not in meta or year >= meta[siren].get("_last_year", 0):
                meta[siren] = {
                    "siren": siren,
                    "nom": row.get("epci_name") or row.get("lbudg") or "",
                    "categ": row.get("categ"),
                    "nat_juridique": row.get("nat_juridique"),
                    "mode_financement": row.get("mode_financement"),
                    "dep_code": row.get("dep_code"),
                    "dep_name": row.get("dep_name"),
                    "reg_code": row.get("reg_code"),
                    "reg_name": row.get("reg_name"),
                    "outre_mer": row.get("outre_mer"),
                    "gfp_tranche_population": row.get("gfp_tranche_population"),
                    "_last_year": year,
                }
    print(f"{n} lignes en {time.time()-t0:.1f}s")
    print(f"  [epci]  {len(series)} EPCIs · {len(seen_agregats)} agrégats distincts")

    # Construction des séries multi-années pour chaque (EPCI, indicateur)
    indicators = sorted(seen_agregats)
    entities = []
    for siren, ag_data in series.items():
        m = meta[siren]
        values_eur_hab: dict[str, list] = {}
        values_montant: dict[str, list] = {}
        for ag in indicators:
            year_data = ag_data.get(ag, {})
            serie_eur = [year_data.get(y, {}).get("eur_hab") for y in ANNEES_EPCI]
            serie_mt = [year_data.get(y, {}).get("montant") for y in ANNEES_EPCI]
            # On stocke uniquement la série en €/hab (cohérent avec les autres
            # niveaux côté JS). La série brute en € est disponible si besoin
            # mais non exportée — on ferait pareil pour les départements/régions.
            values_eur_hab[ag] = serie_eur
            values_montant[ag] = serie_mt  # noqa: F841 (réservé futur usage)

        # Population : série multi-années (pour l'affichage dans le panel)
        pop_series = [population_by_year[siren].get(y) for y in ANNEES_EPCI]

        ent = {
            "siren": siren,
            "nom": m["nom"],
            "categ": m["categ"],
            "nat_juridique": m["nat_juridique"],
            "mode_financement": m["mode_financement"],
            "dep_code": m["dep_code"],
            "dep_name": m["dep_name"],
            "reg_code": m["reg_code"],
            "reg_name": m["reg_name"],
            "outre_mer": m["outre_mer"],
            "gfp_tranche_population": m["gfp_tranche_population"],
            "population": pop_series,
            "values": values_eur_hab,
        }
        entities.append(ent)

    return indicators, entities, meta


def write_synthese(indicators: list[str], entities: list[dict]) -> None:
    """Écrit data/intercommunalites/synthese-intercommunalites-2024.json."""
    out = INTERCO_DIR / "synthese-intercommunalites-2024.json"
    payload = {
        "years": ANNEES_EPCI,
        "indicators": indicators,
        "entities": entities,
    }
    out.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    mo = out.stat().st_size / 1024 / 1024
    print(f"  [epci]  synthese écrite : {len(entities)} EPCIs ({mo:.1f} Mo)")


# ---------------------------------------------------------------------------
# Construction by-epci/{siren}.json à partir des by-dep existants
# ---------------------------------------------------------------------------

def _expand_epci_prefix(s: str) -> str:
    """Transforme un nom EPCI abrégé en sa forme longue.
    Ex: "CC des 7 Vallées" -> "Communauté de communes des 7 Vallées".
    OFGL utilise la forme longue dans ofgl-base-gfp, mais les communes ont
    le `nom_gfp` abrégé dans ofgl-base-communes — incohérence interne au
    fournisseur, qu'on gère côté client en normalisant les deux côtés."""
    if not s:
        return s
    s = s.strip()
    expansions = [
        ("CC ", "Communauté de communes "),
        ("CA ", "Communauté d'agglomération "),
        ("CU ", "Communauté urbaine "),
        ("METRO ", "Métropole "),
    ]
    for short, full in expansions:
        if s.startswith(short):
            return full + s[len(short):]
    return s


def _contract_epci_prefix(s: str) -> str:
    """Inverse de _expand_epci_prefix : forme longue -> abrégée."""
    if not s:
        return s
    s = s.strip()
    contractions = [
        ("Communauté de communes ", "CC "),
        ("Communauté d'agglomération ", "CA "),
        ("Communauté urbaine ", "CU "),
        ("Métropole ", "METRO "),
    ]
    for full, short in contractions:
        if s.startswith(full):
            return short + s[len(full):]
    return s


def _epci_name_variants(s: str) -> list[str]:
    """Renvoie les variantes utiles d'un nom d'EPCI pour la jointure textuelle
    entre données communes (abrégées) et données EPCI (longues). Inclut une
    forme « canonicalisée » qui élimine les différences orthographiques
    fréquentes entre les deux datasets OFGL :
      - ligature œ vs « oe » (ex: « Cœur du Jura » vs « Coeur du Jura »)
      - parenthèses parasites (ex: « (LFA) » à la fin)
      - espaces autour de la ponctuation (ex: « Poligny, Salins » vs
        « Poligny,Salins »)
      - accents et casse
    """
    if not s:
        return []
    variants = [s]
    expanded = _expand_epci_prefix(s)
    if expanded != s:
        variants.append(expanded)
    contracted = _contract_epci_prefix(s)
    if contracted != s:
        variants.append(contracted)
    canon = _canonicalize_epci_name(s)
    if canon and canon not in variants:
        variants.append(canon)
    return variants


_PREFIXES_TO_STRIP_CANON = (
    "communaute de communes ", "communaute d'agglomeration ",
    "communaute urbaine ", "metropole ",
    "cc ", "ca ", "cu ",
)


def _canonicalize_epci_name(s: str) -> str:
    """Forme « ultra-normalisée » d'un nom d'EPCI utilisée comme clé de
    matching de dernier recours. La même chaîne canonique est produite
    qu'on parte de la forme abrégée OFGL-communes ou de la forme longue
    OFGL-gfp, modulo ces transformations :
      - ligatures œ/Œ -> oe/OE, æ/Æ -> ae/AE (Python NFKD ne les décompose pas)
      - lowercase
      - suppression des accents (NFKD + filtrage combining)
      - suppression du contenu entre parenthèses
      - suppression des préfixes communautaires
      - suppression de toute ponctuation et espace
    """
    import unicodedata as _ud
    import re as _re
    if not s:
        return ""
    # 1. Décomposer ligatures (NFKD ne le fait PAS pour œ/Œ/æ/Æ)
    s = (s.replace("œ", "oe").replace("Œ", "OE")
          .replace("æ", "ae").replace("Æ", "AE"))
    # 2. Strip accents
    s = _ud.normalize("NFKD", s)
    s = "".join(c for c in s if not _ud.combining(c))
    s = s.lower().strip()
    # 3. Strip parenthèses + contenu
    s = _re.sub(r"\([^)]*\)", "", s)
    # 4. Strip préfixes communautaires (qu'ils soient longs ou courts)
    for p in _PREFIXES_TO_STRIP_CANON:
        if s.startswith(p):
            s = s[len(p):]
            break
    # 5. Strip toute ponctuation et espace → comparaison stricte de mots
    s = _re.sub(r"[^a-z0-9]", "", s)
    return s


def _build_epci_name_indices(epci_meta: dict[str, dict]):
    """Construit deux dicts : nom -> siren et (nom, dep_code) -> siren.
    Chaque EPCI est indexé sous SES TROIS VARIANTES (forme OFGL canonique,
    contractée, expansée). Pour les noms en doublon (2 cas), on indexe
    uniquement dans le second dict avec le dep_code en désambiguateur."""
    nom_count: dict[str, int] = defaultdict(int)
    for m in epci_meta.values():
        for variant in _epci_name_variants(m["nom"]):
            nom_count[variant] += 1
    nom_to_siren: dict[str, str] = {}
    nom_dep_to_siren: dict[tuple, str] = {}
    for siren, m in epci_meta.items():
        for variant in _epci_name_variants(m["nom"]):
            if nom_count[variant] == 1:
                nom_to_siren[variant] = siren
            else:
                nom_dep_to_siren[(variant, m.get("dep_code"))] = siren
    return nom_to_siren, nom_dep_to_siren


def _make_epci_name_resolver(nom_to_siren, nom_dep_to_siren):
    """Renvoie une fonction `(nom_gfp, dep_code) -> siren | None` qui
    tente toutes les variantes du nom (canonique, abrégée, longue) avant
    d'abandonner."""
    def resolve(nom_gfp: str | None, dep_code: str | None) -> str | None:
        if not nom_gfp:
            return None
        for candidate in _epci_name_variants(nom_gfp):
            s = nom_to_siren.get(candidate)
            if s:
                return s
            s = nom_dep_to_siren.get((candidate, dep_code))
            if s:
                return s
        return None
    return resolve


def build_by_epci(
    epci_meta: dict[str, dict],
    commune_to_epci: dict[str, str],
) -> dict[str, dict]:
    """Pour chaque EPCI, agrège ses communes membres depuis les fichiers
    by-dep existants.

    Jointure principale : mapping `insee_commune -> siren_epci` issu de
    geo.api.gouv.fr (jointure officielle par SIREN, robuste).

    Fallback pour les communes ABSENTES du mapping : on tombe sur le
    `nom_gfp` présent dans les données OFGL (synthese-communes), qu'on
    résout en SIREN via une table `nom_epci -> siren` construite depuis
    `ofgl-base-gfp`. Ce cas couvre les communes qui ont FUSIONNÉ après
    le millésime OFGL (ex: Huby-Saint-Leu et Marconne fusionnées dans
    Hesdin-la-Forêt en 2025 — geo.api ne les connaît plus, mais OFGL les
    a encore avec leur `nom_gfp = "CC des 7 Vallées"`).

    Retourne : { siren_epci: {communes: [...], bbox: {...}, dep_codes: [...] } }
    """
    print("  [epci]  construction des fichiers by-epci (drill-down) ...")
    t0 = time.time()

    # Pour chaque EPCI : liste des communes membres + bbox cumulée
    epci_data: dict[str, dict] = {}
    for siren in epci_meta:
        epci_data[siren] = {
            "communes": [],
            "bbox": None,
            "dep_codes": set(),
        }

    # Table de fallback `nom_epci -> siren` construite depuis ofgl-base-gfp.
    # Indexe BOTH the OFGL canonical name AND its abbreviated form, parce
    # qu'OFGL utilise des conventions différentes selon les datasets :
    #   - ofgl-base-gfp        : nom long ("Communauté de communes des 7 Vallées")
    #   - ofgl-base-communes   : nom abrégé ("CC des 7 Vallées") dans `nom_gfp`
    # Sans la double indexation, le fallback nom_gfp -> siren manque les
    # communes fusionnées dont on cherche à récupérer l'EPCI.
    nom_to_siren, nom_dep_to_siren = _build_epci_name_indices(epci_meta)
    resolve_epci_by_name = _make_epci_name_resolver(nom_to_siren, nom_dep_to_siren)

    # On parcourt tous les by-dep
    by_dep_dir = DATA / "communes" / "by-dep"
    files = sorted(p for p in by_dep_dir.glob("*.json") if not p.name.startswith("_"))
    n_unmatched = 0
    n_matched_via_siren = 0
    n_matched_via_name = 0
    n_unknown_epci = 0
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        dep_code = d.get("dep_code")
        for c in d.get("communes", []):
            data = c.get("data") or {}
            insee = data.get("insee")
            if not insee:
                continue
            # Normaliser sur 5 chiffres (au cas où)
            insee = insee.zfill(5) if insee.isdigit() else insee
            # 1. Tentative : mapping officiel par SIREN (geo.api.gouv.fr)
            siren_epci = commune_to_epci.get(insee)
            matched_via = "siren"
            # 2. Fallback : la commune n'est plus dans geo.api (fusion récente)
            #    mais OFGL la garde avec son `nom_gfp` — on remonte au siren
            #    de l'EPCI via le nom.
            if not siren_epci:
                nom_gfp = data.get("nom_gfp")
                siren_epci = resolve_epci_by_name(nom_gfp, dep_code)
                matched_via = "name"
            if not siren_epci:
                n_unmatched += 1
                continue
            # L'EPCI doit aussi être dans nos metadata OFGL (sinon = EPCI
            # disparu ou non couvert par ofgl-base-gfp)
            if siren_epci not in epci_data:
                n_unknown_epci += 1
                continue
            if matched_via == "siren":
                n_matched_via_siren += 1
            else:
                n_matched_via_name += 1
            ent = epci_data[siren_epci]
            ent["communes"].append(c)
            ent["dep_codes"].add(dep_code)
            # Bbox depuis le path SVG : on extrait les min/max des coordonnées
            d_path = (c.get("svg") or {}).get("d") or ""
            xmin, ymin, xmax, ymax = _bbox_from_svg_path(d_path)
            if xmin is not None:
                if ent["bbox"] is None:
                    ent["bbox"] = {
                        "x_min": xmin, "x_max": xmax,
                        "y_min": ymin, "y_max": ymax,
                    }
                else:
                    bb = ent["bbox"]
                    bb["x_min"] = min(bb["x_min"], xmin)
                    bb["x_max"] = max(bb["x_max"], xmax)
                    bb["y_min"] = min(bb["y_min"], ymin)
                    bb["y_max"] = max(bb["y_max"], ymax)
    print(
        f"  [epci]  jointure : {n_matched_via_siren} par SIREN, "
        f"{n_matched_via_name} par nom_gfp (communes fusionnées), "
        f"{n_unmatched} sans EPCI, "
        f"{n_unknown_epci} EPCI hors ofgl-base-gfp"
    )

    # Écriture des fichiers
    BY_EPCI_DIR.mkdir(parents=True, exist_ok=True)
    n_written = 0
    for siren, ent in epci_data.items():
        m = epci_meta[siren]
        if not ent["communes"]:
            continue  # EPCI sans commune (cas rares : EPCI dissous mais data 2024)
        out = BY_EPCI_DIR / f"{siren}.json"
        payload = {
            "siren_epci": siren,
            "nom_epci": m["nom"],
            "categ": m["categ"],
            "bbox": ent["bbox"],
            "dep_codes": sorted(ent["dep_codes"]),
            "years": ANNEES_EPCI,
            # On reprend les mêmes indicateurs que les by-dep (49 indicateurs
            # déjà présents par commune, y compris taux). Pas de nouveau
            # téléchargement à faire ici.
            "communes": ent["communes"],
        }
        out.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        n_written += 1
    print(f"  [epci]  by-epci/ : {n_written} fichiers écrits en {time.time()-t0:.1f}s")

    # Index global
    index_payload = []
    for siren, ent in epci_data.items():
        if not ent["communes"]:
            continue
        m = epci_meta[siren]
        index_payload.append({
            "siren": siren,
            "nom": m["nom"],
            "categ": m["categ"],
            "bbox": ent["bbox"],
            "dep_codes": sorted(ent["dep_codes"]),
            "nb_communes": len(ent["communes"]),
        })
    # Tri alphabétique par nom pour navigation côté JS
    index_payload.sort(key=lambda x: (x["nom"] or "").lower())
    (BY_EPCI_DIR / "_index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  [epci]  _index.json : {len(index_payload)} EPCIs référencés")

    return epci_data


# ---------------------------------------------------------------------------
# Construction by-region/_index.json
# ---------------------------------------------------------------------------

def build_by_region(epci_data: dict[str, dict], commune_to_epci: dict[str, str]) -> None:
    """Pour chaque région : liste des EPCIs ayant au moins une commune dans
    la région + bbox englobante.

    Pourquoi ? Le niveau « Intercommunalités » du site fait un drill-down
    par RÉGION (pas par EPCI individuel). On charge alors tous les EPCIs
    concernés et leurs communes membres — y compris celles HORS-région
    pour les EPCIs à cheval, afin que l'utilisateur voie le contour complet
    de l'EPCI et comprenne qu'il déborde de la région cliquée.

    Mapping construit en deux temps :
      1. commune INSEE -> SIREN EPCI (déjà fait via geo.api dans
         `commune_to_epci`)
      2. commune INSEE -> code région (via data/communes.json téléchargé
         par fetch_all.py, ou via geo.api si besoin)

    Format de sortie : ``data/intercommunalites/by-region/_index.json`` =
    ``[{reg_code, reg_name, bbox, epcis: [siren, ...]}, ...]``.
    """
    print("  [epci]  construction by-region/_index.json ...", end=" ", flush=True)
    t0 = time.time()
    BY_REGION_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Charger commune INSEE -> code région via data/communes.json
    communes_path = DATA / "communes.json"
    if not communes_path.exists():
        print("  [warn] data/communes.json absent, on saute by-region")
        return
    communes_meta = json.loads(communes_path.read_text(encoding="utf-8"))
    insee_to_reg: dict[str, str] = {}
    for c in communes_meta:
        code = c.get("code")
        reg = c.get("codeRegion")
        if code and reg:
            insee_to_reg[code] = reg

    # 2. Construire reg_code -> set(siren_epci)
    reg_to_epcis: dict[str, set[str]] = defaultdict(set)
    for insee, siren_epci in commune_to_epci.items():
        reg = insee_to_reg.get(insee)
        if reg and siren_epci in epci_data:
            reg_to_epcis[reg].add(siren_epci)

    # 3. Pour chaque région, calculer la bbox = union des bbox des EPCIs
    #    concernés. Cela inclut les communes "débordantes" des EPCIs à
    #    cheval, ce qui est exactement ce qu'on veut afficher en drill-down.
    region_payloads: list[dict] = []
    for reg_code, sirens in sorted(reg_to_epcis.items()):
        bbox = None
        for s in sirens:
            ent = epci_data.get(s)
            if not ent or not ent.get("bbox"):
                continue
            bb = ent["bbox"]
            if bbox is None:
                bbox = dict(bb)
            else:
                bbox["x_min"] = min(bbox["x_min"], bb["x_min"])
                bbox["x_max"] = max(bbox["x_max"], bb["x_max"])
                bbox["y_min"] = min(bbox["y_min"], bb["y_min"])
                bbox["y_max"] = max(bbox["y_max"], bb["y_max"])
        region_payloads.append({
            "reg_code": reg_code,
            # Le nom de la région sera renseigné côté JS via la synthese-regions
            # déjà chargée — on garde le payload minimal côté serveur.
            "bbox": bbox,
            "epcis": sorted(sirens),
            "nb_epcis": len(sirens),
        })

    out = BY_REGION_DIR / "_index.json"
    out.write_text(
        json.dumps(region_payloads, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"{len(region_payloads)} régions · "
        f"total {sum(p['nb_epcis'] for p in region_payloads)} liens EPCI-région "
        f"en {time.time()-t0:.1f}s"
    )


_NUMBER_RE = __import__("re").compile(r"-?\d+(?:\.\d+)?")


def _bbox_from_svg_path(d: str) -> tuple:
    """Extrait (xmin, ymin, xmax, ymax) d'un path SVG (commands M et L).
    OFGL n'utilise que M/L/Z donc on peut juste extraire toutes les paires
    de nombres successives."""
    if not d:
        return (None, None, None, None)
    nums = [float(m) for m in _NUMBER_RE.findall(d)]
    if len(nums) < 2:
        return (None, None, None, None)
    # Les coordonnées sont des paires (x, y) successives.
    xs = nums[0::2]
    ys = nums[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# Enrichissement de meta-communes-2024.json avec siren_epci
# ---------------------------------------------------------------------------

def enrich_meta_communes(
    commune_to_epci: dict[str, str],
    epci_meta: dict[str, dict],
) -> None:
    """Ajoute le `siren_epci` au fichier meta-communes-2024.json (indexé
    positionnellement comme les autres champs). Permet au site de faire le
    lookup commune -> EPCI pour la coloration overview en mode
    « Intercommunalités ».

    Jointure :
      1. INSEE -> SIREN via le mapping geo.api.gouv.fr (robuste)
      2. Fallback : si l'INSEE n'est pas dans le mapping (commune fusionnée
         après le millésime OFGL), on remonte via `nom_gfp` lu depuis
         synthese-communes-2024.json. Cas des ~2-5 communes par cycle de
         fusion (ex: Huby-Saint-Leu + Marconne -> Hesdin-la-Forêt en 2025).

    Idempotent : si le champ existe déjà, il est écrasé."""
    print("  [epci]  enrichissement de meta-communes-2024.json ...")
    t0 = time.time()
    meta_path = DATA / "communes" / "meta-communes-2024.json"
    if not meta_path.exists():
        print("  [warn]  meta-communes introuvable, on saute")
        return

    # Index nom -> siren avec toutes les variantes (canonique OFGL,
    # abrégée, expansée) — cf. fonctions helper en haut du module.
    nom_to_siren, nom_dep_to_siren = _build_epci_name_indices(epci_meta)
    resolve_by_name = _make_epci_name_resolver(nom_to_siren, nom_dep_to_siren)

    # Charger synthese-communes pour récupérer nom_gfp par INSEE
    synth_path = DATA / "communes" / "synthese-communes-2024.json"
    nom_gfp_by_insee: dict[str, str] = {}
    if synth_path.exists():
        synth = json.loads(synth_path.read_text(encoding="utf-8"))
        for c in synth.get("communes", []):
            insee = c.get("insee")
            nom_gfp = c.get("nom_gfp")
            if insee and nom_gfp:
                nom_gfp_by_insee[insee] = nom_gfp

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_schema = meta.get("schema", [])

    if "siren_epci" in old_schema:
        epci_col = old_schema.index("siren_epci")
        new_schema = old_schema
    else:
        epci_col = len(old_schema)
        new_schema = old_schema + ["siren_epci"]

    matched_siren = 0
    matched_name = 0
    new_communes = []
    for entry in meta["communes"]:
        # entry = [nom, insee, dep_code, dep_name, population, (siren_epci?)]
        insee = (entry[1] or "")
        insee = insee.zfill(5) if insee.isdigit() else insee
        dep_code = entry[2]
        siren_epci = commune_to_epci.get(insee)
        if siren_epci:
            matched_siren += 1
        else:
            # Fallback par nom_gfp (communes fusionnées post-OFGL)
            nom_gfp = nom_gfp_by_insee.get(insee)
            siren_epci = resolve_by_name(nom_gfp, dep_code)
            if siren_epci:
                matched_name += 1
        new_entry = list(entry)
        while len(new_entry) <= epci_col:
            new_entry.append(None)
        new_entry[epci_col] = siren_epci
        new_communes.append(new_entry)

    meta["schema"] = new_schema
    meta["communes"] = new_communes
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    mo = meta_path.stat().st_size / 1024 / 1024
    print(
        f"  [epci]  meta enrichi : {matched_siren} par SIREN + "
        f"{matched_name} par nom_gfp / {len(new_communes)} communes "
        f"({mo:.2f} Mo) en {time.time()-t0:.1f}s"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="Re-télécharge le CSV OFGL même s'il existe déjà.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("EPCI (Intercommunalités) — synthèse OFGL 2017-2024")
    print("=" * 60)
    t0 = time.time()

    # 1. Données financières OFGL
    csv_path = download_ofgl_base_gfp(force=args.force)
    indicators, entities, epci_meta = build_synthese(csv_path)
    write_synthese(indicators, entities)

    # 2. Mapping commune INSEE -> SIREN EPCI (geo.api.gouv.fr, jointure robuste)
    mapping_path = download_commune_epci_mapping(force=args.force)
    commune_to_epci = load_commune_epci_mapping(mapping_path)
    print(f"  [epci]  mapping commune->EPCI chargé : {len(commune_to_epci)} communes")

    # 3. Drill-down par EPCI + enrichissement meta avec siren_epci
    epci_data = build_by_epci(epci_meta, commune_to_epci)
    enrich_meta_communes(commune_to_epci, epci_meta)

    # 4. Drill-down par RÉGION : pour le niveau Intercommunalités côté site,
    #    qui fonctionne en zoom par région (et pas par EPCI individuel).
    build_by_region(epci_data, commune_to_epci)

    print()
    print(f"Terminé en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
