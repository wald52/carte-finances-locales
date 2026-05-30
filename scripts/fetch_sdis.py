"""Fetch & intègre ofgl-base-sdis (Services Départementaux d'Incendie et de
Secours) au niveau département.

Source : ``ofgl-base-sdis`` (1 SDIS par dpt, 97 SDIS au total). Les comptes
sont organisés par (dpt × année × agrégat × budget). On agrège tous les
budgets annexes (cantine, formation, télé-assistance…) avec le budget
principal pour obtenir le total SDIS.

Mapping codes physiques → canoniques (cohérent avec fetch_syndicats_mdph) :
  - 67 + 68 → 67A (Alsace, fusion CEA depuis 2021)
  - 69 → aussi 691 (le SDMIS Rhône inclut déjà la Métropole de Lyon, on
    duplique la valeur pour les deux dpts synthese)

Départements manquants : 75 (Paris) + 92 + 93 + 94. Couverts par la
**BSPP** (Brigade des Sapeurs-Pompiers de Paris), unité militaire hors
périmètre OFGL. Aucune valeur SDIS pour ces 4 dpts.

Indicateurs produits : ``"SDIS — {agregat}"`` (57 agrégats, en €/hab pour
cohérence avec les autres indicateurs département).
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
SYNTHESE_FILE = DATA / "departements" / "synthese-departements-2024.json"
TMP_EXPORT = DATA / "_tmp_sdis.json"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "ofgl-base-sdis/exports/json"
)

ANNEES_DEP = list(range(2012, 2025))
N_ANNEES = len(ANNEES_DEP)

PREFIX = "SDIS — "


def _download_export() -> list[dict]:
    """Télécharge l'export JSON SDIS complet (~6 Mo, ~32 k records).

    Pas de filtre serveur — on prend tout puis on filtre côté client par
    agrégat. Mis en cache local dans data/_tmp_sdis.json (supprimer
    manuellement pour forcer un refresh)."""
    if TMP_EXPORT.exists():
        print(f"[cache] réutilisation {TMP_EXPORT.relative_to(ROOT)} "
              f"({TMP_EXPORT.stat().st_size/1024/1024:.1f} Mo)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))

    params = {
        "select": "exer,code_dep,nom_dep,agregat,montant,euros_par_habitant,"
                  "lbudg,cbudg,siren,population_totale",
    }
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:100]}…")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024/1024:.1f} Mo en {time.time()-t0:.1f}s")
    return json.loads(raw.decode("utf-8"))


def _consolidate_two_dpts(
    code_combined: str,
    code_a: str,
    code_b: str,
    montants: dict[tuple, dict[int, float]],
    pops: dict[tuple, int],
) -> None:
    """Consolide les montants et populations de deux codes physiques vers
    un code canonique (ex: 67+68 → 67A, 2A+2B → Corse).

    Modifie `montants` et `pops` en place : ajoute des entrées pour
    `code_combined` calculées comme la somme des entrées A et B.
    """
    # Toutes les paires (dep, agregat) qui appartiennent à A ou B
    keys_a = {(d, ag) for (d, ag) in montants if d == code_a}
    keys_b = {(d, ag) for (d, ag) in montants if d == code_b}
    all_agregats = {ag for (_, ag) in (keys_a | keys_b)}

    for ag in all_agregats:
        cell_a = montants.get((code_a, ag), {})
        cell_b = montants.get((code_b, ag), {})
        merged = {}
        all_yidx = set(cell_a.keys()) | set(cell_b.keys())
        for yidx in all_yidx:
            va = cell_a.get(yidx, {}).get("montant", 0.0)
            vb = cell_b.get(yidx, {}).get("montant", 0.0)
            merged[yidx] = {"montant": va + vb}
        montants[(code_combined, ag)] = merged

    # Populations consolidées : somme A + B par année
    for yidx in range(N_ANNEES):
        pa = pops.get((code_a, yidx), 0)
        pb = pops.get((code_b, yidx), 0)
        if pa or pb:
            pops[(code_combined, yidx)] = pa + pb


def _compute_ehab(
    montants: dict[tuple, dict[int, float]],
    pops: dict[tuple, int],
) -> dict[str, dict[str, list]]:
    """Calcule les €/hab par (dep, agregat, année) à partir des montants
    et populations. Retourne { dep : { ind_key : serie[N_ANNEES] } }.
    """
    out: dict[str, dict[str, list]] = defaultdict(dict)
    for (dep, agregat), year_cells in montants.items():
        ind_key = f"{PREFIX}{agregat}"
        serie = []
        has_any = False
        for yidx in range(N_ANNEES):
            v = year_cells.get(yidx)
            pop = pops.get((dep, yidx), 0)
            if v is None or not pop:
                serie.append(None)
            else:
                ehab = v["montant"] / pop if pop > 0 else None
                serie.append(ehab)
                if ehab is not None and ehab != 0:
                    has_any = True
        if has_any:
            out[dep][ind_key] = serie
    return out


def _duplicate_to(idx: dict[str, dict[str, list]], src: str, dst: str) -> None:
    """Copie les séries du dpt src vers dst (cas 691 = 69 : SDMIS unique
    qui sert deux territoires synthese — chacun reçoit la même valeur
    €/hab qui représente la dépense globale de l'institution)."""
    if src in idx and dst not in idx:
        idx[dst] = {k: list(v) for k, v in idx[src].items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Supprime le cache et re-télécharge.")
    args = parser.parse_args()

    if args.force and TMP_EXPORT.exists():
        TMP_EXPORT.unlink()

    t0 = time.time()
    print("=" * 60)
    print("SDIS — Services Départementaux d'Incendie et de Secours")
    print("=" * 60)

    records = _download_export()
    print(f"  {len(records)} records bruts SDIS")

    # Agrégation : (dep_code, year_idx, agregat) → {montant, pop} sommé
    # à travers les budgets annexes (un même SDIS peut avoir plusieurs
    # budgets : principal, formation, restaurant, etc. — on additionne
    # les montants ; la population reste constante au sein du dpt-année).
    # On NE somme PAS les €/hab : ils seront recalculés en fin de pipeline
    # après la consolidation (67A = 67+68, Corse = 2A+2B) avec population
    # combinée — sinon sum(montant_A/pop_A + montant_B/pop_B) ≠ (montant_A+
    # montant_B)/(pop_A+pop_B), erreur de plusieurs dizaines de %.
    agg: dict[tuple, dict[int, dict]] = defaultdict(lambda: defaultdict(lambda: {"montant": 0.0, "pop": 0}))
    pop_by_dep_year: dict[tuple, int] = {}
    agregats = set()
    deps_seen = set()
    n_skipped = 0
    for r in records:
        try:
            year = int(r["exer"])
            if year < 2012 or year > 2024:
                n_skipped += 1
                continue
            yidx = year - 2012
        except (ValueError, KeyError):
            n_skipped += 1
            continue
        dep = r.get("code_dep")
        agregat = r.get("agregat")
        if not dep or not agregat:
            n_skipped += 1
            continue
        agregats.add(agregat)
        deps_seen.add(dep)
        m = r.get("montant")
        if m is not None:
            try:
                agg[(dep, agregat)][yidx]["montant"] += float(m)
            except (TypeError, ValueError):
                pass
        pop = r.get("population_totale")
        if pop is not None:
            try:
                # Population identique entre tous les budgets/agrégats d'un
                # même dpt-année → on garde la valeur (max au cas où certains
                # budgets l'omettent à 0).
                pop_by_dep_year[(dep, yidx)] = max(
                    pop_by_dep_year.get((dep, yidx), 0), int(pop)
                )
            except (TypeError, ValueError):
                pass

    print(f"  {len(agg)} (dpt × agregat) triplets, {len(agregats)} agrégats")
    print(f"  {len(deps_seen)} départements avec SDIS")
    if n_skipped:
        print(f"  {n_skipped} records ignorés")

    # Consolide les codes physiques vers les codes canoniques de synthese.
    # On opère sur les MONTANTS (sommables sans biais), puis on recalcule
    # les €/hab à partir des populations consolidées.
    montants = dict(agg)  # alias
    pops = dict(pop_by_dep_year)

    # 67 + 68 → 67A (Alsace, deux SDIS distincts dans la CEA)
    _consolidate_two_dpts("67A", "67", "68", montants, pops)
    # 2A + 2B → Corse (deux SDIS distincts dans la Collectivité de Corse)
    _consolidate_two_dpts("Corse", "2A", "2B", montants, pops)

    # Recalcule les €/hab pour tous les codes (physiques ET consolidés)
    idx = _compute_ehab(montants, pops)

    # 691 (Métropole de Lyon) : duplication du 69. Le SDMIS Rhône-Métropole
    # est une institution unique servant les deux territoires synthese ;
    # chacun reçoit le même €/hab (qui représente la dépense globale).
    _duplicate_to(idx, "69", "691")

    # Liste des indicateurs (triés par nom d'agrégat pour stabilité)
    all_inds = sorted({k for series in idx.values() for k in series.keys()})

    # Charger synthese-departements
    print(f"\n[load] {SYNTHESE_FILE.relative_to(ROOT)}…")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    entities = synth.get("entities") or []
    print(f"  {len(entities)} entités départements")

    # Nettoyage : enlever les anciens indicateurs SDIS — (idempotence)
    existing = synth.get("indicators") or []
    indicators_clean = [
        i for i in existing
        if not (i.get("key", "") if isinstance(i, dict) else i).startswith(PREFIX)
    ]
    n_removed = len(existing) - len(indicators_clean)
    if n_removed:
        print(f"  {n_removed} anciens indicateurs '{PREFIX}…' supprimés")
    indicators_clean.extend(all_inds)

    # Injection dans entities[].values pour les entités existantes
    matched = 0
    corse_entity_exists = False
    for ent in entities:
        code = str(ent.get("code") or "").strip()
        if code == "Corse":
            corse_entity_exists = True
        values = ent.setdefault("values", {})
        # Nettoyer anciennes clés SDIS
        for k in list(values.keys()):
            if k.startswith(PREFIX):
                del values[k]
        if not code or code not in idx:
            continue
        matched += 1
        for k, serie in idx[code].items():
            values[k] = serie

    # Cas spécial Corse : le polygone SVG est "Corse" (entité unique) mais
    # OFGL stocke 2A et 2B séparément → aucune entité synthese ne matche
    # le polygone, l'utilisateur voit une carte grise et le noDataMessage.
    # On injecte une pseudo-entité "Corse" avec les valeurs SDIS consolidées
    # (autres indicateurs restent absents — la Corse est une CTU sans comptes
    # dpt propres). Population = somme 2A + 2B par année.
    if "Corse" in idx and not corse_entity_exists:
        # Populations 2A + 2B par année (pour le subtitle "Population : …").
        pop_corse = []
        for yidx in range(N_ANNEES):
            pa = pops.get(("2A", yidx), 0)
            pb = pops.get(("2B", yidx), 0)
            pop_corse.append(pa + pb if (pa or pb) else None)
        corse_ent = {
            "code": "Corse",
            "name": "Corse",
            "meta": {
                "reg_code": "94",
                "reg_name": "Corse",
                "categ": "CTU",
                "dep_status": "rural",
                "outre_mer": "Non",
            },
            "population": pop_corse,
            "values": dict(idx["Corse"]),
        }
        entities.append(corse_ent)
        matched += 1
        print(f"  [+] Pseudo-entité Corse injectée (CTU — SDIS uniquement)")

    synth["entities"] = entities
    synth["indicators"] = indicators_clean

    SYNTHESE_FILE.write_text(
        json.dumps(synth, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = SYNTHESE_FILE.stat().st_size / 1024 / 1024
    print(f"\n[save] {matched} départements enrichis avec {len(all_inds)} indicateurs SDIS")
    print(f"       {SYNTHESE_FILE.relative_to(ROOT)} : {sz:.2f} Mo")
    print(f"\nTerminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
