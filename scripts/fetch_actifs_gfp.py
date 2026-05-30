"""Fetch & integrate ``actifs_gfp_2024`` (OFGL) au niveau intercommunalités.

Patrimoine non financier des groupements à fiscalité propre (GFP) —
actif réévalué selon la même méthodologie OFGL que pour les communes
(``fetch_actifs_communes.py``). 13 indicateurs identiques, snapshot
31/12/2024.

Périmètre : 866 groupements sur 1335 (types CC=659, CA=174, CU=12,
METRO=19, MET13=1, EPT=1). Couverture OFGL partielle, assumée telle quelle :
  - MET13 = Métropole d'Aix-Marseille-Provence (couverte, ~4,4 Md€).
  - EPT : seul Est Ensemble (200057875) est publié ; les 10 autres EPT
    du Grand Paris sont absents → gris.
  - Métropole de Lyon (200046977) : ABSENTE (collectivité à statut
    particulier, traitée au niveau département 691 dans le projet).
  - MGP (200054781) : absente.
  - 469 EPCI (surtout petites CC) sans donnée patrimoine → gris.
Doctrine : si OFGL ne publie pas, on n'invente pas. Les zones grises
sont assumées.

Variables OFGL (13) → libellés exposés : voir ``fetch_actifs_communes.py``.
Préfixe et clés sont identiques aux indicateurs communes, ce qui permet
à l'utilisateur de garder la même sélection en switchant de niveau.

Temporalité : OFGL ne publie qu'un snapshot 2024. Les valeurs sont
stockées dans un array positionnel [None]*8 aligné sur years=[2017..2024],
seul l'index 7 (2024) est renseigné.

Fichiers enrichis :
  - data/intercommunalites/synthese-intercommunalites-2024.json

Pas de fichier ``decoratif-values`` dédié : la coloration du calque
décoratif au niveau intercommunalités se fait en lookup runtime via
``state.epciBySiren[ent.sirenEpci].values[ind.key]`` (côté JS).

Cache local : data/_tmp_actifs_gfp.json (supprimer pour refresh).
Idempotent : nettoie tous les indicateurs préfixés "Patrimoine — " avant
écriture.
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

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYNTHESE_FILE = DATA / "intercommunalites" / "synthese-intercommunalites-2024.json"
TMP_EXPORT = DATA / "_tmp_actifs_gfp.json"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "actifs_gfp_2024/exports/json"
)

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES)
YEAR_2024_IDX = ANNEES.index(2024)

PREFIX = "Patrimoine — "

# Mêmes mappings que pour les communes (libellés identiques pour cohérence
# inter-niveaux : un utilisateur qui sélectionne "Patrimoine — Actif brut"
# voit la donnée commune ou EPCI selon le niveau actif).
INDICATOR_SPEC: dict[str, tuple[str, str]] = {
    "Actif brut":                                 ("Actif brut", "€"),
    "Actif brut par habitant":                    ("Actif brut/hab", "€/hab"),
    "Actif net":                                  ("Actif net", "€"),
    "Actif net par habitant":                     ("Actif net/hab", "€/hab"),
    "Dotation aux amortissements":                ("Dotation aux amortissements", "€"),
    "Taux d'actif brut":                          ("Taux d'actif brut", "%"),
    "Taux d'actif net":                           ("Taux d'actif net", "%"),
    "Taux de vétusté":                            ("Taux de vétusté", "%"),
    "Dette sur actif brut":                       ("Dette sur actif brut", "%"),
    "Epargne sur amortissement":                  ("Épargne sur amortissement", "%"),
    "Subventions sur amortissement":              ("Subventions sur amortissement", "%"),
    "Durée de vie moyenne (DVM)":                 ("Durée de vie moyenne", "années"),
    "Délai de renouvellement patrimonial (DRP)":  ("Délai de renouvellement patrimonial", "années"),
}


def _download_export() -> list[dict]:
    if TMP_EXPORT.exists():
        sz = TMP_EXPORT.stat().st_size / 1024 / 1024
        print(f"[cache] {TMP_EXPORT.relative_to(ROOT)} ({sz:.1f} Mo)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))
    params = {"select": "siren,nom,type_inst,variable,valeur,unite,annee_jointure"}
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:120]}…")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024/1024:.1f} Mo en {time.time()-t0:.1f}s")
    return json.loads(raw.decode("utf-8"))


def _build_index(records: list[dict]) -> dict[str, dict[str, list]]:
    """siren → {indicator_key: [None]*8 with v at idx 7}."""
    idx: dict[str, dict[str, list]] = defaultdict(dict)
    n_skipped = n_ok = 0
    seen_vars: set[str] = set()
    seen_types: set[str] = set()
    for r in records:
        var = r.get("variable")
        if var:
            seen_vars.add(var)
        t = r.get("type_inst")
        if t:
            seen_types.add(t)
        spec = INDICATOR_SPEC.get(var)
        if spec is None:
            n_skipped += 1
            continue
        label, _unit = spec
        key = PREFIX + label
        siren = r.get("siren")
        if siren is None:
            n_skipped += 1
            continue
        siren_str = str(siren).strip()
        if not siren_str:
            n_skipped += 1
            continue
        raw_val = r.get("valeur")
        if raw_val is None:
            n_skipped += 1
            continue
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            n_skipped += 1
            continue
        serie = idx[siren_str].setdefault(key, [None] * N_ANNEES)
        serie[YEAR_2024_IDX] = val
        n_ok += 1
    print(f"  {n_ok} valeurs intégrées, {n_skipped} skip "
          f"({len(idx)} groupements distincts)")
    print(f"  Types juridiques OFGL : {sorted(seen_types)}")
    unknown = seen_vars - set(INDICATOR_SPEC.keys())
    if unknown:
        print(f"  ⚠ variables OFGL inconnues ignorées : {sorted(unknown)}")
    return idx


def _cleanup_indicators_list(inds) -> list:
    out = []
    for i in inds:
        key = i if isinstance(i, str) else i.get("key", "")
        if not key.startswith(PREFIX):
            out.append(i)
    return out


def _cleanup_values_dict(values: dict) -> None:
    for k in list(values.keys()):
        if k.startswith(PREFIX):
            del values[k]


def update_synthese(idx: dict[str, dict[str, list]], new_keys: list[str]) -> None:
    print(f"\n[synthese] {SYNTHESE_FILE.relative_to(ROOT)}")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    synth["indicators"] = _cleanup_indicators_list(synth.get("indicators", [])) + new_keys
    matched = 0
    unmatched: list[str] = []
    for ent in synth.get("entities", []):
        values = ent.setdefault("values", {})
        _cleanup_values_dict(values)
        siren = str(ent.get("siren") or "").strip()
        if not siren or siren not in idx:
            if siren:
                unmatched.append(siren)
            continue
        matched += 1
        for k, serie in idx[siren].items():
            values[k] = serie
    SYNTHESE_FILE.write_text(
        json.dumps(synth, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = SYNTHESE_FILE.stat().st_size / 1024 / 1024
    print(f"  {matched} EPCI enrichis, {sz:.1f} Mo")
    # Sanity-check : combien d'EPCI OFGL n'ont PAS matché un EPCI de la synthese ?
    synth_sirens = {str(e.get("siren") or "").strip() for e in synth.get("entities", [])}
    ofgl_only = [s for s in idx.keys() if s not in synth_sirens]
    if ofgl_only:
        print(f"  ⚠ {len(ofgl_only)} SIREN OFGL absents de la synthese "
              f"(non enrichis) : {ofgl_only[:5]}…")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Supprime le cache et re-télécharge.")
    args = parser.parse_args()

    if args.force and TMP_EXPORT.exists():
        TMP_EXPORT.unlink()

    t0 = time.time()
    print("=" * 64)
    print("actifs_gfp_2024 — Patrimoine non financier des EPCI à FP")
    print("=" * 64)

    records = _download_export()
    print(f"  {len(records)} records bruts")

    idx = _build_index(records)
    new_keys = sorted({PREFIX + lbl for lbl, _u in INDICATOR_SPEC.values()})

    update_synthese(idx, new_keys)

    print(f"\nTerminé en {time.time()-t0:.1f}s")
    print(f"  {len(new_keys)} nouveaux indicateurs : {new_keys}")


if __name__ == "__main__":
    main()
