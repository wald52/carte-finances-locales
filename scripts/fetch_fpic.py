"""Fetch & intègre fpic-ensembles-intercommunaux au niveau intercommunalités.

FPIC = Fonds national de Péréquation des ressources Intercommunales et
Communales. Mécanisme de solidarité HORIZONTALE entre EI (Ensembles
Intercommunaux = EPCI + communes membres) : les EI les plus « riches »
(potentiel financier élevé par rapport au revenu de la population)
prélèvent une contribution qui est REDISTRIBUÉE aux EI les plus « pauvres ».

Source : ``fpic-ensembles-intercommunaux`` (DGCL), 2018-2025 (on filtre
sur 2018-2024 pour rester aligné sur les autres datasets EPCI).

Clé EI = `siren` (= SIREN EPCI) → join direct avec
``synthese-intercommunalites-2024.json``.

Variables intégrées (curées parmi 84 disponibles) :

  **Solde et flux FPIC** :
    - Prélèvement (€ et €/hab)
    - Versement (€ et €/hab)
    - Solde net (€ et €/hab) — NÉGATIF si l'EI est contributeur net
  **Indices** :
    - Indice synthétique de prélèvement (sans unité)
    - Indice synthétique de reversement
    - Rang de classement au reversement
  **Éligibilité (booléens 0/1)** :
    - Éligibilité au reversement
    - Assujetti au prélèvement
    - Éligibilité à une garantie de reversement
  **Population et revenu** :
    - Population DGF
    - Population INSEE
    - Revenu fiscal de référence (€/hab calculé)
  **Potentiels** :
    - Potentiel fiscal agrégé (€/hab)
    - Potentiel financier agrégé (€/hab)
  **Effort fiscal** :
    - Effort fiscal agrégé (index)

Les conversions €/hab utilisent la **Population DGF** (référence officielle
des dotations) du même EI × année.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

# Force UTF-8 stdout
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYNTHESE_FILE = DATA / "intercommunalites" / "synthese-intercommunalites-2024.json"
TMP_EXPORT = DATA / "_tmp_fpic.json"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "fpic-ensembles-intercommunaux/exports/json"
)

# Synthese intercommunalites couvre 2017-2024 ; FPIC démarre en 2018.
# On peut couvrir 2018-2024 ; 2017 restera None (pas de FPIC à l'époque
# de ce dataset).
ANNEES_EPCI = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES_EPCI)

PREFIX = "FPIC — "

# Variables OFGL à télécharger (curées). Le filtre serveur réduit ~370k
# records à ~150k.
VARIABLES_TO_FETCH = [
    # Général
    "Population DGF",
    "Population Insee",
    "Revenu fiscal de référence",
    # FPIC (10)
    "Montant FPIC - Prélèvement",
    "Montant FPIC - Versement",
    "Montant FPIC - Solde",
    "Indice synthétique de prélèvement",
    "Indice synthétique de reversement",
    "Eligibilité au reversement",
    "Ensemble intercommunal assujetti au prélèvement",
    "Rang de classement au reversement",
    "Montant de la garantie de reversement",
    "Eligibilité à une garantie de reversement",
    # Potentiels
    "Potentiel fiscal agrégé",
    "Potentiel financier agrégé",
    # Ressources fiscales
    "Ressources fiscales agrégées",
    # Effort fiscal
    "Effort fiscal agrégé",
]

# Mapping variable OFGL → (label exposé, mode de présentation, unité)
# - mode "raw"     : valeur telle quelle
# - mode "per_dgf" : on calcule aussi une variante €/hab (montant ÷ Pop DGF)
# - mode "bool"    : conversion OUI/NON → 1/0 (ou conservation 1/0 si déjà num)
INDICATOR_SPEC = {
    "Population DGF":                              {"label": "FPIC — Population DGF",                    "mode": "raw",     "unit": "hab"},
    "Population Insee":                            {"label": "FPIC — Population INSEE",                  "mode": "raw",     "unit": "hab"},
    "Revenu fiscal de référence":                  {"label": "FPIC — Revenu fiscal de référence",        "mode": "per_dgf", "unit": "€"},
    "Montant FPIC - Prélèvement":                  {"label": "FPIC — Prélèvement",                       "mode": "per_dgf", "unit": "€"},
    "Montant FPIC - Versement":                    {"label": "FPIC — Versement",                         "mode": "per_dgf", "unit": "€"},
    "Montant FPIC - Solde":                        {"label": "FPIC — Solde net (versement − prélèvement)", "mode": "per_dgf", "unit": "€"},
    "Indice synthétique de prélèvement":           {"label": "FPIC — Indice synthétique de prélèvement", "mode": "raw",     "unit": ""},
    "Indice synthétique de reversement":           {"label": "FPIC — Indice synthétique de reversement", "mode": "raw",     "unit": ""},
    "Eligibilité au reversement":                  {"label": "FPIC — Éligibilité au reversement (0/1)",  "mode": "bool",    "unit": ""},
    "Ensemble intercommunal assujetti au prélèvement": {"label": "FPIC — Assujetti au prélèvement (0/1)",   "mode": "bool", "unit": ""},
    "Rang de classement au reversement":           {"label": "FPIC — Rang de classement au reversement", "mode": "raw",     "unit": ""},
    "Montant de la garantie de reversement":       {"label": "FPIC — Garantie de reversement",           "mode": "per_dgf", "unit": "€"},
    "Eligibilité à une garantie de reversement":   {"label": "FPIC — Éligibilité à une garantie (0/1)",  "mode": "bool",    "unit": ""},
    "Potentiel fiscal agrégé":                     {"label": "FPIC — Potentiel fiscal agrégé",           "mode": "per_dgf", "unit": "€"},
    "Potentiel financier agrégé":                  {"label": "FPIC — Potentiel financier agrégé",        "mode": "per_dgf", "unit": "€"},
    "Ressources fiscales agrégées":                {"label": "FPIC — Ressources fiscales agrégées",      "mode": "per_dgf", "unit": "€"},
    "Effort fiscal agrégé":                        {"label": "FPIC — Effort fiscal agrégé (index)",      "mode": "raw",     "unit": ""},
}


def _download_export() -> list[dict]:
    """Télécharge l'export JSON FPIC filtré sur les variables d'intérêt.

    Cache local dans data/_tmp_fpic.json (supprimer manuellement pour
    forcer un refresh)."""
    if TMP_EXPORT.exists():
        print(f"[cache] réutilisation {TMP_EXPORT.relative_to(ROOT)} "
              f"({TMP_EXPORT.stat().st_size/1024/1024:.1f} Mo)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))

    # Filtre serveur sur les variables qu'on veut (réduit ~370k → ~150k)
    # Échappement des guillemets dans la condition where ODSQL :
    var_list = ", ".join(f'"{v}"' for v in VARIABLES_TO_FETCH)
    params = {
        "where": f"variable IN ({var_list})",
        "select": "exercice,siren,variable,valeur,unite",
    }
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:120]}…")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024/1024:.1f} Mo en {time.time()-t0:.1f}s")
    return json.loads(raw.decode("utf-8"))


def _parse_year(s) -> int | None:
    """Parse exercice (peut être '2020' ou '2020-01-01')."""
    if s is None:
        return None
    s = str(s)
    if "-" in s:
        s = s.split("-")[0]
    try:
        y = int(s)
        if 2017 <= y <= 2024:
            return y
        return None
    except ValueError:
        return None


def _normalize_value(raw_value, mode: str) -> float | None:
    """Convertit la valeur OFGL selon le mode."""
    if raw_value is None:
        return None
    if mode == "bool":
        # OFGL stocke 0/1 dans valeur (double) pour les booléens. On
        # garde la convention 0.0/1.0 pour rester compatible avec la
        # coloration carto (un dégradé 2 couleurs).
        try:
            return 1.0 if float(raw_value) >= 0.5 else 0.0
        except (TypeError, ValueError):
            return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Supprime le cache et re-télécharge.")
    args = parser.parse_args()

    if args.force and TMP_EXPORT.exists():
        TMP_EXPORT.unlink()

    t0 = time.time()
    print("=" * 60)
    print("FPIC — Fonds de Péréquation Intercommunal et Communal")
    print("=" * 60)

    records = _download_export()
    print(f"  {len(records)} records bruts FPIC")

    # Pivot : (siren, year_idx, variable_ofgl) → valeur brute
    # Aussi : (siren, year_idx) → Population DGF (référence pour €/hab)
    raw_values: dict[tuple, dict[str, float]] = defaultdict(dict)
    pop_dgf: dict[tuple, float] = {}

    n_skipped = 0
    sirens_seen = set()
    for r in records:
        year = _parse_year(r.get("exercice"))
        if year is None:
            n_skipped += 1
            continue
        yidx = year - 2017
        siren = r.get("siren")
        if siren is None:
            n_skipped += 1
            continue
        siren_str = str(siren)
        sirens_seen.add(siren_str)
        var = r.get("variable")
        if var not in INDICATOR_SPEC:
            n_skipped += 1
            continue
        spec = INDICATOR_SPEC[var]
        val = _normalize_value(r.get("valeur"), spec["mode"])
        if val is None:
            continue
        raw_values[(siren_str, yidx)][var] = val
        if var == "Population DGF":
            pop_dgf[(siren_str, yidx)] = val

    print(f"  {len(sirens_seen)} EPCI distincts, "
          f"{n_skipped} records hors périmètre")
    print(f"  Population DGF : {len(pop_dgf)} (siren × année) couverts")

    # Note : on ne synthétise PLUS de valeurs agrégées pour la MGP
    # (SIREN 200054781). Le dataset FPIC OFGL ne contient que les 11 EPT
    # parisiens, jamais la MGP elle-même. Toute agrégation côté script
    # serait une INVENTION non publiée par OFGL.
    #
    # Le mapping commune → EPT (pour les 130 communes Paris+PC) est
    # désormais stocké dans meta-communes-2024.json (champ siren_ept,
    # généré par scripts/enrich_meta_with_ept.py). Côté JS, la coloration
    # carto fait un fallback siren_epci (MGP) → siren_ept (EPT) quand la
    # valeur principale est null, lisant ainsi la donnée OFGL réelle au
    # niveau publié sans aucune transformation.

    # Construire l'index final { siren : { ind_key : serie[N_ANNEES] } }
    # Pour les variables monétaires (mode per_dgf), on fournit deux clés :
    #   "{label}"             — valeur brute en €
    #   "{label} (€/hab)"     — valeur ÷ Population DGF
    idx: dict[str, dict[str, list]] = defaultdict(dict)
    indicators_meta: list[dict] = []
    indicators_emitted: set[str] = set()

    def _emit_indicator(key: str, unit: str):
        if key in indicators_emitted:
            return
        indicators_emitted.add(key)
        indicators_meta.append({"key": key, "label": key, "unit": unit})

    for (siren, yidx), var_vals in raw_values.items():
        for var, val in var_vals.items():
            spec = INDICATOR_SPEC[var]
            label = spec["label"]
            unit = spec["unit"]
            mode = spec["mode"]
            if mode == "per_dgf":
                # Variante 1 : valeur brute en €
                key_raw = label + " (€)"
                serie = idx[siren].setdefault(key_raw, [None] * N_ANNEES)
                serie[yidx] = val
                _emit_indicator(key_raw, "€")
                # Variante 2 : €/hab (si Pop DGF disponible)
                pop = pop_dgf.get((siren, yidx))
                if pop and pop > 0:
                    key_per = label + " (€/hab)"
                    serie2 = idx[siren].setdefault(key_per, [None] * N_ANNEES)
                    serie2[yidx] = val / pop
                    _emit_indicator(key_per, "€/hab")
            else:
                # Modes "raw" et "bool" : unique clé, sans conversion
                key = label
                serie = idx[siren].setdefault(key, [None] * N_ANNEES)
                serie[yidx] = val
                _emit_indicator(key, unit)

    print(f"\n  {len(idx)} EPCIs avec ≥1 valeur FPIC")
    print(f"  {len(indicators_meta)} indicateurs FPIC produits "
          f"(brut + €/hab variants)")

    # Charger synthese-intercommunalites
    print(f"\n[load] {SYNTHESE_FILE.relative_to(ROOT)}…")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    entities = synth.get("entities") or []
    print(f"  {len(entities)} entités EPCI")

    # Nettoyage idempotent : retirer anciens indicateurs FPIC —
    existing = synth.get("indicators") or []
    indicators_clean = [
        i for i in existing
        if not (i.get("key", "") if isinstance(i, dict) else i).startswith(PREFIX)
    ]
    n_removed = len(existing) - len(indicators_clean)
    if n_removed:
        print(f"  {n_removed} anciens indicateurs '{PREFIX}…' supprimés")
    # Ajouter les nouvelles métadonnées (triées par label pour stabilité)
    indicators_meta.sort(key=lambda d: d["key"])
    indicators_clean.extend(im["key"] if not isinstance(im, dict) else im for im in indicators_meta)

    # Injection dans entities[].values
    matched = 0
    for ent in entities:
        siren = str(ent.get("siren") or "")
        values = ent.setdefault("values", {})
        # Nettoyer anciennes clés FPIC
        for k in list(values.keys()):
            if k.startswith(PREFIX):
                del values[k]
        if not siren or siren not in idx:
            continue
        matched += 1
        for k, serie in idx[siren].items():
            values[k] = serie

    synth["indicators"] = indicators_clean

    SYNTHESE_FILE.write_text(
        json.dumps(synth, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = SYNTHESE_FILE.stat().st_size / 1024 / 1024
    print(f"\n[save] {matched} EPCIs enrichis ; {len(indicators_meta)} indicateurs FPIC")
    print(f"       {SYNTHESE_FILE.relative_to(ROOT)} : {sz:.2f} Mo")
    print(f"\nTerminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
