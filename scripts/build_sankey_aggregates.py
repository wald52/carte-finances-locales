#!/usr/bin/env python3
"""
Agrégats nationaux pour la page « Vision globale » (diagramme de flux Sankey).

Additionne, pour CHAQUE niveau de collectivité (régions, départements,
intercommunalités, communes) et pour chaque année, les MONTANTS (€) des grands
agrégats recettes / dépenses / épargne publiés par l'OFGL. Produit aussi une
ligne `combine` = somme des quatre niveaux (⚠ double comptage des flux entre
collectivités assumé et documenté côté page).

Deux sources de montants :

  1. **OFGL officiel (défaut)** — agrégation côté serveur via l'API Explore v2.1
     (`select=sum(montant)…&group_by=exer`), filtre `type_de_budget="Budget
     principal"` (même convention que fetch_all.py / fetch_epci.py). Payload
     minuscule : on ne télécharge pas les bases lourdes (départements ~277 Mo,
     communes 22 M lignes), le serveur fait la somme. C'est le chemin du pipeline
     de publication. Datasets : ofgl-base-regions / -departements / -gfp /
     -communes.

  2. **Reconstruction locale (repli, --offline ou si le réseau échoue)** —
     recompose le montant national en sommant `euros_par_habitant × population`
     depuis les fichiers de synthèse DÉJÀ présents sur le disque
     (data/<niveau>/synthese-*.json.gz et data/communes/by-dep/*.json.gz). Comme
     l'OFGL publie `euros_par_habitant = montant / population`, le produit
     reconstruit le montant (au centime près sur régions, dont le €/hab servi est
     arrondi par optimize_served_payload.py ; population des communes = snapshot
     2024, léger écart sur les années antérieures). Sert à régénérer un artefact
     hors-ligne ; pour les montants officiels exacts, lancer SANS --offline avec
     un accès réseau.

Sortie : data/sankey/aggregates-2024.json
  { "years": [2012..2024],
    "source": "ofgl" | "reconstruction",
    "levels": { "regions": {agregat: [m_2012..m_2024]}, ..., "combine": {...} } }

Idempotent. Cache brut OFGL : data/_tmp_sankey.json (supprimer pour rafraîchir).

Usage :
    python scripts/build_sankey_aggregates.py            # OFGL officiel
    python scripts/build_sankey_aggregates.py --offline  # reconstruction locale
    python scripts/build_sankey_aggregates.py --force    # ignore le cache OFGL
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Encodage stdout forcé en UTF-8 (workaround Windows cp1252).
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_DIR = DATA / "sankey"
OUT_FILE = OUT_DIR / "aggregates-2024.json"
CACHE_FILE = DATA / "_tmp_sankey.json"

OFGL_RECORDS = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{ds}/records"

# Axe temporel unifié de la page. Régions/départements couvrent 2012-2024 ;
# intercommunalités/communes 2017-2024 (les années antérieures restent null,
# alignées sur cet axe).
YEARS = list(range(2012, 2025))

# Niveau -> (dataset OFGL, fichier(s) de synthèse local pour le repli).
LEVELS = {
    "regions": {
        "dataset": "ofgl-base-regions",
        "synthese": DATA / "regions" / "synthese-regions-2024.json.gz",
    },
    "departements": {
        "dataset": "ofgl-base-departements",
        "synthese": DATA / "departements" / "synthese-departements-2024.json.gz",
    },
    "intercommunalites": {
        "dataset": "ofgl-base-gfp",
        "synthese": DATA / "intercommunalites"
        / "synthese-intercommunalites-2024.json.gz",
    },
    "communes": {
        "dataset": "ofgl-base-communes",
        # Shardé par département : data/communes/by-dep/*.json.gz
        "synthese": DATA / "communes" / "by-dep",
    },
}

# Agrégats OFGL nécessaires au diagramme de flux détaillé (sous-ensemble de
# INDICATEURS_COMMUNS de fetch_all.py). Noms = champ `agregat` OFGL verbatim.
AGREGATS = [
    # Totaux (référence / tooltips)
    "Recettes totales",
    "Dépenses totales",
    # --- Section de fonctionnement : recettes ---
    "Recettes de fonctionnement",
    "Impôts et taxes",
    "Concours de l'Etat",
    "Subventions reçues et participations",
    "Ventes de biens et services",
    # --- Section de fonctionnement : dépenses ---
    "Dépenses de fonctionnement",
    "Frais de personnel",
    "Achats et charges externes",
    "Dépenses d'intervention",
    "Charges financières",
    # --- Pivot ---
    "Epargne brute",
    # --- Section d'investissement : recettes ---
    "Recettes d'investissement",
    "Recettes d'investissement hors emprunts",
    "FCTVA",
    "Emprunts hors GAD",
    # --- Section d'investissement : dépenses ---
    "Dépenses d'investissement",
    "Dépenses d'équipement",
    "Subventions d'équipement versées",
    "Remboursements d'emprunts hors GAD",
]

UA = "carte-finances-locales/sankey (+https://wald52.github.io/carte-finances-locales/)"


# ---------------------------------------------------------------------------
# Source 1 : OFGL officiel (agrégation côté serveur)
# ---------------------------------------------------------------------------
def _http_get_json(url: str, retries: int = 4) -> dict:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise last  # type: ignore[misc]


def _fetch_ofgl_level(dataset: str) -> dict[str, dict[int, float]]:
    """Pour un dataset, renvoie {agregat: {annee: montant_total}} via
    l'API d'agrégation OFGL (somme des montants au Budget principal)."""
    out: dict[str, dict[int, float]] = {}
    for ag in AGREGATS:
        where = f'type_de_budget="Budget principal" and agregat="{ag}"'
        qs = urllib.parse.urlencode(
            {
                "select": "sum(montant) as m",
                "where": where,
                "group_by": "exer",
                "order_by": "exer",
                "limit": 100,
            }
        )
        url = OFGL_RECORDS.format(ds=dataset) + "?" + qs
        data = _http_get_json(url)
        by_year: dict[int, float] = {}
        for rec in data.get("results", []):
            try:
                y = int(rec.get("exer"))
            except (TypeError, ValueError):
                continue
            m = rec.get("m")
            if m is not None:
                by_year[y] = float(m)
        out[ag] = by_year
        print(f"      · {ag}: {len(by_year)} années", flush=True)
    return out


def build_from_ofgl(force: bool) -> dict[str, dict[str, list]]:
    if CACHE_FILE.exists() and not force:
        print(f"  [cache] {CACHE_FILE.name}")
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    else:
        cache = {}
        for level, cfg in LEVELS.items():
            print(f"  [OFGL] {level} ({cfg['dataset']}) ...", flush=True)
            cache[level] = {
                ag: {str(y): v for y, v in by_year.items()}
                for ag, by_year in _fetch_ofgl_level(cfg["dataset"]).items()
            }
        CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  [cache écrit] {CACHE_FILE.name}")

    levels: dict[str, dict[str, list]] = {}
    for level in LEVELS:
        per_ag = cache.get(level, {})
        levels[level] = {
            ag: [per_ag.get(ag, {}).get(str(y)) for y in YEARS] for ag in AGREGATS
        }
    return levels


# ---------------------------------------------------------------------------
# Source 2 : reconstruction locale (€/hab × population, repli hors-ligne)
# ---------------------------------------------------------------------------
def _load_gz_json(path: Path) -> dict:
    with gzip.open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def _accumulate(
    totals: dict[str, list[float | None]],
    syn_years: list[int],
    values: dict,
    population,
):
    """Ajoute (€/hab × pop) d'une entité aux totaux nationaux, en mappant les
    années de l'entité (syn_years) sur l'axe unifié YEARS. `population` est soit
    une liste alignée sur syn_years, soit un entier (snapshot communes)."""
    for ag in AGREGATS:
        serie = values.get(ag)
        if not serie:
            continue
        for i, y in enumerate(syn_years):
            if i >= len(serie):
                continue
            eur_hab = serie[i]
            if eur_hab is None:
                continue
            if isinstance(population, list):
                pop = population[i] if i < len(population) else None
            else:
                pop = population
            if not pop:
                continue
            try:
                eur_hab = float(eur_hab)
                pop = float(pop)
            except (TypeError, ValueError):
                continue
            gi = YEARS.index(y) if y in YEARS else None
            if gi is None:
                continue
            cur = totals[ag][gi]
            totals[ag][gi] = (cur or 0.0) + eur_hab * pop


def _empty_totals() -> dict[str, list]:
    return {ag: [None] * len(YEARS) for ag in AGREGATS}


def build_from_reconstruction() -> dict[str, dict[str, list]]:
    levels: dict[str, dict[str, list]] = {}

    # Régions / départements / intercommunalités : un fichier synthèse, entités
    # avec population (liste) + values (€/hab par année).
    for level in ("regions", "departements", "intercommunalites"):
        path = LEVELS[level]["synthese"]
        syn = _load_gz_json(path)
        syn_years = syn["years"]
        totals = _empty_totals()
        for ent in syn["entities"]:
            _accumulate(totals, syn_years, ent.get("values", {}), ent.get("population"))
        levels[level] = totals
        print(f"  [recon] {level}: {len(syn['entities'])} entités")

    # Communes : shardées par département (population = entier snapshot).
    bydep = LEVELS["communes"]["synthese"]
    totals = _empty_totals()
    n = 0
    for f in sorted(bydep.glob("*.json.gz")):
        if f.name.startswith("_"):
            continue
        shard = _load_gz_json(f)
        syn_years = shard["years"]
        for com in shard["communes"]:
            d = com.get("data", com)
            _accumulate(totals, syn_years, d.get("values", {}), d.get("population"))
            n += 1
    levels["communes"] = totals
    print(f"  [recon] communes: {n} communes")
    return levels


# ---------------------------------------------------------------------------
# Assemblage + écriture
# ---------------------------------------------------------------------------
def add_combine(levels: dict[str, dict[str, list]]) -> None:
    combine = _empty_totals()
    for ag in AGREGATS:
        for gi in range(len(YEARS)):
            s = None
            for level in ("regions", "departements", "intercommunalites", "communes"):
                v = levels.get(level, {}).get(ag, [None] * len(YEARS))[gi]
                if v is not None:
                    s = (s or 0.0) + v
            combine[ag][gi] = s
    levels["combine"] = combine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="reconstruction locale (€/hab × pop) sans réseau")
    ap.add_argument("--force", action="store_true",
                    help="ignore le cache OFGL data/_tmp_sankey.json")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    source = "reconstruction" if args.offline else "ofgl"
    if not args.offline:
        try:
            print("Source : OFGL officiel (agrégation serveur)")
            levels = build_from_ofgl(args.force)
        except Exception as e:  # noqa: BLE001 — repli volontaire sur le local
            print(f"  ⚠ OFGL injoignable ({e.__class__.__name__}: {e})")
            print("  → repli sur la reconstruction locale (€/hab × population)")
            source = "reconstruction"
            levels = build_from_reconstruction()
    else:
        print("Source : reconstruction locale (€/hab × population)")
        levels = build_from_reconstruction()

    add_combine(levels)

    out = {"years": YEARS, "source": source, "levels": levels}
    OUT_FILE.write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"\n✓ écrit {OUT_FILE.relative_to(ROOT)}  (source={source})")

    # Contrôle de cohérence : RF ≈ DF + Épargne brute (2024) sur le combiné.
    idx = YEARS.index(2024)
    c = levels["combine"]
    rf = c["Recettes de fonctionnement"][idx]
    df = c["Dépenses de fonctionnement"][idx]
    eb = c["Epargne brute"][idx]
    if rf and df is not None and eb is not None:
        ecart = abs(rf - (df + eb)) / rf * 100
        print(f"  cohérence 2024 (combiné) : RF={rf/1e9:.1f} Md€, "
              f"DF+EB={(df+eb)/1e9:.1f} Md€, écart={ecart:.1f} %")


if __name__ == "__main__":
    main()
