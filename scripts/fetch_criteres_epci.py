"""Fetch & intègre les CRITÈRES de contexte des EPCI (``interne-criteres-*``).

Bases **internes** OFGL décrivant le CONTEXTE socio-économique et
institutionnel de chaque groupement / ensemble intercommunal. **Ce ne sont
PAS des comptes OFGL/BANATIC** : le revenu par habitant vient de la DGFiP
(revenus fiscaux des ménages), la part de logements sociaux du RPLS/SRU
(ministère du Logement), les QPV de l'ANCT, la nature juridique et le mode
de financement du référentiel BANATIC. Reprises **telles quelles** (doctrine
de fidélité) avec avertissement de source dans le ``help`` et ``sources.html``.

Niveau : **intercommunalités uniquement** (clé = ``epci_code`` = SIREN).

Sources & couverture temporelle :
  - ``interne-criteres-ei-ofgl-2021/2022/2023`` — ENSEMBLES intercommunaux,
    surensemble (~1272) incluant MGP + 11 EPT + communes isolées.
  - ``interne-criteres-gfp-ofgl-2020`` — GROUPEMENTS à FP (~1266), utilisé
    pour gagner l'exercice 2020 (EI ne démarre qu'en 2021).
  EI et GFP portent des valeurs **identiques** sur les exercices communs
  (vérifié) ; on prend donc EI pour 2021-2023 (couvre MGP/EPT/CI) et GFP
  pour 2020. **Pas de 2024** dans la source.

Temporalité : array positionnel aligné sur years=[2017..2024]. Renseigné
sur 2020-2023 (indices 3-6) ; 2017-2019 et 2024 restent None (gris assumé).
``type_ei`` est EI-only → null en 2020.

12 indicateurs, préfixe « Critères — », groupe « Contexte & critères (EPCI) » :
  numériques (3) : Revenu fiscal/hab, Part de logements sociaux, Population ;
  catégoriels (9) : Nature juridique, Mode de financement, Régime fiscal
  détaillé, Présence de QPV, Type d'ensemble intercommunal, Outre-mer, et
  3 strates ordinales OFGL (population, revenu/hab, poids logements sociaux).

Les indicateurs catégoriels portent ``kind:"categorical"`` + ``scale`` +
``categories:[{code,label}]`` (les couleurs sont assignées côté JS). La
valeur stockée par année est le **code verbatim** OFGL (string).

Fichiers :
  - enrichit data/intercommunalites/synthese-intercommunalites-2024.json
    (coloration EPCI = lookup runtime via state.epciBySiren — pas de fichier
    décoratif dédié, comme fetch_actifs_gfp.py).
  - émet le snippet INDICATORS dans data/_tmp_indicators_criteres.txt
    (à insérer dans app.js).

CONTRAINTE D'ORDRE : enrichit la synthese EPCI → doit tourner APRÈS
``fetch_epci.py`` (qui réécrit la synthese et effacerait l'enrichissement —
même piège que fetch_actifs_gfp.py / fetch_ei.py). Idempotent (nettoie le
préfixe « Critères — » avant écriture). Cache : data/_tmp_criteres/.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYNTHESE_FILE = DATA / "intercommunalites" / "synthese-intercommunalites-2024.json"
CACHE_DIR = DATA / "_tmp_criteres"
SNIPPET_FILE = DATA / "_tmp_indicators_criteres.txt"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{ds}/exports/json"
)

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES)
YEAR_IDX = {y: i for i, y in enumerate(ANNEES)}

PREFIX = "Critères — "
GROUP = "Contexte & critères (EPCI)"

# Sources : (dataset_id, exercice, type). EI prioritaire (surensemble +
# MGP/EPT/CI) sur ses années ; GFP uniquement pour gagner 2020.
SOURCES = [
    ("interne-criteres-gfp-ofgl-2020", 2020),
    ("interne-criteres-ei-ofgl-2021", 2021),
    ("interne-criteres-ei-ofgl-2022", 2022),
    ("interne-criteres-ei-ofgl-2023", 2023),
]

# ── Indicateurs NUMÉRIQUES : champ OFGL → (label, unité) ────────────────────
NUMERIC_SPECS: list[tuple[str, str, str]] = [
    # (field, label, unit)
    ("revenu_hab", "Revenu fiscal par habitant", "€/hab"),
    ("part_logements_soc", "Part de logements sociaux", "%"),
    ("intercommunalite_pop_tot", "Population totale", "hab"),
]

# ── Indicateurs CATÉGORIELS : champ OFGL → (label, scale, label_map) ─────────
# label_map : code verbatim OFGL → libellé lisible. "self" = la valeur EST le
# libellé (funding_method_name). "ordinal" = strate numérique (libellé dérivé).
NAT_JURIDIQUE = {
    "CC": "Communauté de communes",
    "CA": "Communauté d'agglomération",
    "CU": "Communauté urbaine",
    "M": "Métropole",
    "MET69": "Métropole de Lyon",
    "MET75": "Métropole du Grand Paris",
    "EPT": "Établissement public territorial",
    "CI": "Commune isolée",
}
MODE_FIN = {
    "FPU": "Fiscalité professionnelle unique",
    "FA": "Fiscalité additionnelle",
    "CF": "Contributions fiscalisées / budgétaires",
}
QPV = {"0": "Aucun QPV", "1": "Au moins un QPV"}
TYPE_EI = {
    "GFP": "Groupement à fiscalité propre",
    "EPT": "Établissement public territorial",
    "MGP": "Métropole du Grand Paris",
    "CI": "Commune isolée",
}
OUTRE_MER = {"Non": "Hexagone", "Oui": "Outre-mer"}

CATEG_SPECS: list[dict] = [
    {"field": "nat_juridique", "label": "Nature juridique", "scale": "nominal",
     "labels": NAT_JURIDIQUE,
     "help": "Catégorie juridique du groupement (référentiel BANATIC). Donnée de contexte, hors comptes OFGL."},
    {"field": "mode_financement", "label": "Mode de financement", "scale": "nominal",
     "labels": MODE_FIN,
     "help": "Régime fiscal du groupement (FPU = fiscalité professionnelle unique, FA = fiscalité additionnelle, CF = contributions des membres). Référentiel BANATIC, hors comptes OFGL."},
    {"field": "funding_method_name", "label": "Régime fiscal détaillé", "scale": "nominal",
     "labels": "self",
     "help": "Libellé détaillé du mode de financement (variantes FPZ/FPE/FPU…). Verbatim BANATIC, hors comptes OFGL."},
    {"field": "qpv", "label": "Présence de QPV", "scale": "nominal",
     "labels": QPV,
     "help": "Présence d'au moins un quartier prioritaire de la politique de la ville sur le territoire (source ANCT). Hors comptes OFGL."},
    {"field": "type_ei", "label": "Type d'ensemble intercommunal", "scale": "nominal",
     "labels": TYPE_EI,
     "help": "Type d'ensemble intercommunal OFGL (GFP, EPT, MGP, commune isolée). Non renseigné en 2020 (source EI à partir de 2021)."},
    {"field": "outre_mer", "label": "Outre-mer", "scale": "nominal",
     "labels": OUTRE_MER,
     "help": "Localisation du groupement (hexagone vs outre-mer). Donnée de contexte OFGL."},
    {"field": "gfp_tranche_population", "label": "Strate de population (indice OFGL)",
     "scale": "ordinal", "labels": "ordinal", "ordinal_word": "Strate",
     "help": "Strate de population OFGL (indice croissant : 0 = plus petite, valeur élevée = plus peuplé). Découpage interne OFGL, repris verbatim."},
    {"field": "gfp_tranche_revenu_imposable_par_habitant", "label": "Tranche de revenu/hab (indice OFGL)",
     "scale": "ordinal", "labels": "ordinal", "ordinal_word": "Tranche",
     "help": "Tranche de revenu imposable par habitant OFGL (indice croissant). Découpage interne OFGL, repris verbatim. Source revenus : DGFiP."},
    {"field": "gfp_tranche_poids_des_logements_sociaux", "label": "Tranche poids logements sociaux (indice OFGL)",
     "scale": "ordinal", "labels": "ordinal", "ordinal_word": "Tranche",
     "help": "Tranche de poids des logements sociaux OFGL (indice croissant). Découpage interne OFGL, repris verbatim. Source : RPLS/SRU."},
]


def _download(ds: str) -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{ds}.json"
    if cache.exists():
        print(f"  [cache] {cache.relative_to(ROOT)}")
        return json.loads(cache.read_text(encoding="utf-8"))
    # Pas de `select` : les champs diffèrent entre EI et GFP (ex. type_ei
    # n'existe pas dans GFP 2020 → un select le mentionnant renvoie HTTP 400).
    # Les datasets sont minuscules (~1270 lignes), on récupère tout.
    url = EXPORT_URL.format(ds=ds)
    print(f"  [download] {ds}…")
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read()
    cache.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def _siren(rec: dict) -> str:
    v = rec.get("epci_code")
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).strip() if v is not None else ""


def _num(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _cat(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def build() -> tuple[dict[str, dict[str, list]], dict[str, set]]:
    """Retourne (idx siren→{key:[…8…]}, observed codes par champ catégoriel)."""
    idx: dict[str, dict[str, list]] = defaultdict(dict)
    observed: dict[str, Counter] = {s["field"]: Counter() for s in CATEG_SPECS}
    n_ok = 0
    for ds, year in SOURCES:
        recs = _download(ds)
        yi = YEAR_IDX[year]
        n_ds = 0
        for r in recs:
            siren = _siren(r)
            if not siren:
                continue
            ent = idx[siren]
            for field, label, _unit in NUMERIC_SPECS:
                v = _num(r.get(field))
                if v is None:
                    continue
                key = PREFIX + label
                ent.setdefault(key, [None] * N_ANNEES)[yi] = v
                n_ok += 1
            for spec in CATEG_SPECS:
                code = _cat(r.get(spec["field"]))
                if code is None:
                    continue
                key = PREFIX + spec["label"]
                ent.setdefault(key, [None] * N_ANNEES)[yi] = code
                observed[spec["field"]][code] += 1
                n_ok += 1
            n_ds += 1
        print(f"    {ds} ({year}) : {n_ds} EPCI")
    print(f"  {n_ok} valeurs intégrées, {len(idx)} EPCI distincts")
    return idx, observed


def _ordered_categories(spec: dict, observed: Counter) -> list[dict]:
    """Liste ordonnée [{code,label}] des catégories observées."""
    labels = spec["labels"]
    if labels == "ordinal":
        word = spec.get("ordinal_word", "Niveau")
        codes = sorted(observed.keys(), key=lambda c: (int(c) if c.lstrip("-").isdigit() else 9999, c))
        return [{"code": c, "label": f"{word} {c}"} for c in codes]
    if labels == "self":
        # Par fréquence décroissante (la valeur est son propre libellé).
        codes = [c for c, _ in observed.most_common()]
        return [{"code": c, "label": c} for c in codes]
    # Mapping explicite : ordre du dict, filtré aux codes observés, puis
    # tout code observé inattendu (verbatim) ajouté en fin.
    out = [{"code": c, "label": lbl} for c, lbl in labels.items() if c in observed]
    extra = [c for c in observed if c not in labels]
    for c in sorted(extra):
        out.append({"code": c, "label": c})
    return out


def emit_snippet(observed: dict[str, Counter]) -> list[str]:
    """Écrit le snippet INDICATORS JS et renvoie la liste des clés."""
    lines: list[str] = []
    lines.append("  // ══════════════════════════════════════════════════════════════════════")
    lines.append("  // CONTEXTE & CRITÈRES (EPCI) — interne-criteres-* (HORS comptes OFGL)")
    lines.append("  // Revenu DGFiP, logements sociaux RPLS/SRU, QPV ANCT, juridique BANATIC.")
    lines.append("  // Catégoriels : kind/scale/categories ; couleurs assignées côté JS.")
    lines.append("  // ══════════════════════════════════════════════════════════════════════")
    keys: list[str] = []

    def J(v) -> str:
        return json.dumps(v, ensure_ascii=False)

    src_note = " Couverture 2020-2023 (pas de 2024). Donnée de contexte, non issue des comptes OFGL/BANATIC."

    for field, label, unit in NUMERIC_SPECS:
        key = PREFIX + label
        keys.append(key)
        if field == "revenu_hab":
            help_ = "Revenu fiscal de référence par habitant (source DGFiP, revenus des ménages)."
        elif field == "part_logements_soc":
            help_ = "Part de logements sociaux sur le territoire (source RPLS/SRU, ministère du Logement)."
        else:
            help_ = "Population totale du groupement publiée par l'OFGL avec ces critères."
        lines.append(
            f"  {{ key: {J(key)}, label: {J(label)}, unit: {J(unit)}, "
            f"group: {J(GROUP)}, levels: [\"intercommunalites\"], "
            f"help: {J(help_ + src_note)} }},"
        )

    for spec in CATEG_SPECS:
        key = PREFIX + spec["label"]
        keys.append(key)
        cats = _ordered_categories(spec, observed[spec["field"]])
        lines.append(
            f"  {{ key: {J(key)}, label: {J(spec['label'])}, unit: \"\", "
            f"group: {J(GROUP)}, levels: [\"intercommunalites\"], "
            f"kind: \"categorical\", scale: {J(spec['scale'])}, "
            f"categories: {J(cats)}, "
            f"help: {J(spec['help'] + src_note)} }},"
        )

    SNIPPET_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[snippet] {SNIPPET_FILE.relative_to(ROOT)} — {len(keys)} indicateurs")
    return keys


def _cleanup_list(inds) -> list:
    out = []
    for i in inds:
        key = i if isinstance(i, str) else i.get("key", "")
        if not key.startswith(PREFIX):
            out.append(i)
    return out


def update_synthese(idx: dict[str, dict[str, list]], keys: list[str]) -> None:
    print(f"\n[synthese] {SYNTHESE_FILE.relative_to(ROOT)}")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    synth["indicators"] = _cleanup_list(synth.get("indicators", [])) + keys
    matched = 0
    for ent in synth.get("entities", []):
        values = ent.setdefault("values", {})
        for k in list(values.keys()):
            if k.startswith(PREFIX):
                del values[k]
        siren = str(ent.get("siren") or "").strip()
        if not siren or siren not in idx:
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
    synth_sirens = {str(e.get("siren") or "").strip() for e in synth.get("entities", [])}
    ofgl_only = [s for s in idx if s not in synth_sirens]
    if ofgl_only:
        print(f"  ⚠ {len(ofgl_only)} SIREN OFGL absents de la synthese : {ofgl_only[:5]}…")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Supprime le cache et re-télécharge.")
    args = parser.parse_args()
    if args.force and CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()

    t0 = time.time()
    print("=" * 64)
    print("interne-criteres-* — Contexte & critères des EPCI (hors comptes)")
    print("=" * 64)

    idx, observed = build()
    keys = emit_snippet(observed)
    update_synthese(idx, keys)

    print(f"\nTerminé en {time.time()-t0:.1f}s — {len(keys)} indicateurs")
    for f, lbl, _u in NUMERIC_SPECS:
        print(f"  [num] {PREFIX}{lbl}")
    for spec in CATEG_SPECS:
        n = len(observed[spec["field"]])
        print(f"  [cat:{spec['scale']:7}] {PREFIX}{spec['label']} ({n} catégories)")


if __name__ == "__main__":
    main()
