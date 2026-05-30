"""Fetch & integrate ``ofgl-base-ei`` (OFGL) — Comptes consolidés des
ensembles intercommunaux 2017-2024, au niveau intercommunalités.

CONCEPT — Un « ensemble intercommunal » (EI) = l'EPCI à fiscalité propre
**PLUS toutes ses communes membres**, avec neutralisation des flux croisés
entre la structure et ses communes. C'est la vue « territoire consolidé »,
distincte des deux niveaux déjà présents dans le projet :
  - ``ofgl-base-gfp``      → budget de la STRUCTURE intercommunale seule
  - ``ofgl-base-communes`` → communes une par une
  - ``ofgl-base-ei``       → le TERRITOIRE entier consolidé (= structure
                             + communes − flux internes)

OFGL publie ce niveau comme jeu à part entière (carto dédiée incluse).
53 agrégats financiers, **identiques à ceux du niveau GFP** mais en
valeur consolidée. Période 2017-2024, série complète (pas un snapshot).

COUVERTURE — 1233 EI, dont 23 outre-mer (Mayotte incluse). Tous les
types : GFP (EPCI classiques), **EPT (11) et MGP** sont présents comme
entités à part entière → la zone Paris+petite couronne est coloriée.
Les EI « commune isolée » (type CI) ont un SIREN de commune et ne
matchent aucune entité EPCI de la synthese → ignorés (zone grise
assumée, fidélité OFGL).

STOCKAGE — Deux endroits, comme le veut l'architecture du site :
  1. ``synthese-intercommunalites-2024.json`` : on enrichit chaque entité
     EPCI (clé ``siren``) avec 53 indicateurs préfixés ``EI — `` portant
     la série **€/hab consolidé** (valeur OFGL ``euros_par_habitant``,
     verbatim). C'est ce qui colorie la carte + alimente la courbe.
     La coloration runtime se fait via
     ``state.epciBySiren[siren].values["EI — …"]`` (lookup, pas de
     fichier décoratif dédié — comme ``fetch_actifs_gfp.py``).
  2. ``data/intercommunalites/ei-details/{siren}.json`` : la
     **décomposition** ``montant_gfp`` / ``montant_communes`` /
     ``montant_flux`` / ``montant`` (consolidé) + ``eur_hab`` par
     agrégat × année. Lazy-chargé au clic sur un EPCI pour afficher
     « dont structure / dont communes / flux neutralisés » dans le
     panneau détail (mirror du pattern ``data/syndicats/details/``).

Émet aussi le snippet d'indicateurs JS (``_tmp_indicators_ei.txt``) et le
nom de groupe (``_tmp_groups_ei.txt``) à insérer dans ``app.js`` par
``insert_ei_indicators.py``.

Cache local : ``data/_tmp_ei.json`` (supprimer ou ``--force`` pour refresh).
Idempotent : nettoie tous les indicateurs préfixés ``EI — `` et vide le
dossier ``ei-details/`` avant d'écrire.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
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
DETAILS_DIR = DATA / "intercommunalites" / "ei-details"
TMP_EXPORT = DATA / "_tmp_ei.json"
SNIPPET_OUT = DATA / "_tmp_indicators_ei.txt"
GROUPS_OUT = DATA / "_tmp_groups_ei.txt"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "ofgl-base-ei/exports/json"
)

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEE_IDX = {y: i for i, y in enumerate(ANNEES)}
N_ANNEES = len(ANNEES)

PREFIX = "EI — "
GROUP_NAME = "Ensemble intercommunal — territoire consolidé"

HELP_TEXT = (
    "Comptes consolidés de l'ensemble intercommunal (EPCI à fiscalité propre "
    "+ communes membres, flux internes neutralisés) — vue « territoire » et "
    "non « structure seule ». Source OFGL « ofgl-base-ei ». Cliquer un EPCI "
    "affiche la décomposition « dont structure / dont communes / flux »."
)


def _f(s):
    """Parse float tolérant (None/'' → None)."""
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _download_export() -> list[dict]:
    if TMP_EXPORT.exists():
        sz = TMP_EXPORT.stat().st_size / 1024 / 1024
        print(f"[cache] {TMP_EXPORT.relative_to(ROOT)} ({sz:.1f} Mo)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))
    params = {
        "select": (
            "siren,epci_name,type_ei,agregat,annee_join,"
            "montant_gfp,montant_communes,montant_flux,montant,"
            "euros_par_habitant,ptot"
        )
    }
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:120]}…")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024/1024:.1f} Mo en {time.time()-t0:.1f}s")
    return json.loads(raw.decode("utf-8"))


def _load_synth_sirens() -> set[str]:
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    return {str(e.get("siren") or "").strip() for e in synth.get("entities", [])}


def _build(records: list[dict], synth_sirens: set[str]):
    """Construit (values_eur_hab, details, meta, seen_agregats).

    - values_eur_hab : {siren: {"EI — <agregat>": [8 €/hab]}}  → synthese
    - details        : {siren: {<agregat>: {gfp/communes/flux/montant/eur_hab: [8]}}}
    - meta           : {siren: {"nom":…, "type_ei":…}}
    """
    values: dict[str, dict[str, list]] = defaultdict(dict)
    details: dict[str, dict[str, dict]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    seen_agregats: set[str] = set()
    seen_types: defaultdict[str, set] = defaultdict(set)

    n_ok = n_skip_nomatch = n_skip_other = 0
    for r in records:
        siren = str(r.get("siren") or "").strip()
        if not siren:
            n_skip_other += 1
            continue
        type_ei = r.get("type_ei")
        if siren not in synth_sirens:
            # EI hors périmètre EPCI synthese (ex. type CI = commune isolée).
            n_skip_nomatch += 1
            if type_ei:
                seen_types[type_ei].add(siren)
            continue
        ag = r.get("agregat")
        if not ag:
            n_skip_other += 1
            continue
        try:
            year = int(r.get("annee_join"))
        except (TypeError, ValueError):
            n_skip_other += 1
            continue
        yi = ANNEE_IDX.get(year)
        if yi is None:
            n_skip_other += 1
            continue

        eur = _f(r.get("euros_par_habitant"))

        # 1) série €/hab pour la synthese (carte + courbe)
        key = PREFIX + ag
        serie = values[siren].setdefault(key, [None] * N_ANNEES)
        serie[yi] = eur

        # 2) décomposition pour le panneau détail
        d = details[siren].setdefault(
            ag,
            {
                "gfp": [None] * N_ANNEES,
                "communes": [None] * N_ANNEES,
                "flux": [None] * N_ANNEES,
                "montant": [None] * N_ANNEES,
                "eur_hab": [None] * N_ANNEES,
            },
        )
        d["gfp"][yi] = _f(r.get("montant_gfp"))
        d["communes"][yi] = _f(r.get("montant_communes"))
        d["flux"][yi] = _f(r.get("montant_flux"))
        d["montant"][yi] = _f(r.get("montant"))
        d["eur_hab"][yi] = eur

        meta[siren] = {"nom": r.get("epci_name"), "type_ei": type_ei}
        seen_agregats.add(ag)
        if type_ei:
            seen_types[type_ei].add(siren)
        n_ok += 1

    print(f"  {n_ok} valeurs intégrées · {len(values)} EI matchés synthese")
    print(f"  {n_skip_nomatch} lignes ignorées (SIREN hors EPCI synthese), "
          f"{n_skip_other} autres skip")
    print("  Types EI (nb SIREN distincts) : "
          + ", ".join(f"{t}={len(s)}" for t, s in sorted(seen_types.items())))
    print(f"  {len(seen_agregats)} agrégats distincts")
    return values, details, meta, seen_agregats


def _cleanup_indicators_list(inds) -> list:
    out = []
    for i in inds:
        key = i if isinstance(i, str) else i.get("key", "")
        if not key.startswith(PREFIX):
            out.append(i)
    return out


def update_synthese(values: dict[str, dict[str, list]], new_keys: list[str]) -> int:
    print(f"\n[synthese] {SYNTHESE_FILE.relative_to(ROOT)}")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    synth["indicators"] = _cleanup_indicators_list(synth.get("indicators", [])) + new_keys
    matched = 0
    for ent in synth.get("entities", []):
        vals = ent.setdefault("values", {})
        for k in list(vals.keys()):
            if k.startswith(PREFIX):
                del vals[k]  # idempotence
        siren = str(ent.get("siren") or "").strip()
        if siren and siren in values:
            matched += 1
            for k, serie in values[siren].items():
                vals[k] = serie
    SYNTHESE_FILE.write_text(
        json.dumps(synth, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = SYNTHESE_FILE.stat().st_size / 1024 / 1024
    print(f"  {matched} EPCI enrichis, {sz:.1f} Mo")
    return matched


def write_details(details: dict[str, dict[str, dict]], meta: dict[str, dict]) -> int:
    print(f"\n[details] {DETAILS_DIR.relative_to(ROOT)}")
    if DETAILS_DIR.exists():
        shutil.rmtree(DETAILS_DIR)  # idempotence
    DETAILS_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for siren, agregats in details.items():
        payload = {
            "siren": siren,
            "nom": meta.get(siren, {}).get("nom"),
            "type_ei": meta.get(siren, {}).get("type_ei"),
            "years": ANNEES,
            "agregats": agregats,
        }
        (DETAILS_DIR / f"{siren}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        n += 1
    print(f"  {n} fichiers détail écrits")
    return n


def _js_escape(s: str) -> str:
    """Pour insérer dans une string JS double-quote : remplace " par « »
    (cf. pièges CLAUDE.md) et neutralise les backslashes."""
    return (s or "").replace("\\", "").replace('"', "«")


def emit_snippet(seen_agregats: set[str]) -> None:
    print(f"\n[snippet] {SNIPPET_OUT.relative_to(ROOT)}")
    lines = [
        "  // ====================================================================",
        "  // EI - Comptes consolidés des ensembles intercommunaux (ofgl-base-ei)",
        "  // EPCI + communes membres, flux neutralisés. Décomposition lazy en ei-details/.",
        "  // ====================================================================",
    ]
    help_js = _js_escape(HELP_TEXT)
    for ag in sorted(seen_agregats):
        key = _js_escape(PREFIX + ag)
        label = _js_escape(ag)
        lines.append(
            f'  {{ key: "{key}", label: "{label}", unit: "€/hab",\n'
            f'    group: "{_js_escape(GROUP_NAME)}", levels: ["intercommunalites"],\n'
            f'    help: "{help_js}" }},'
        )
    SNIPPET_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    GROUPS_OUT.write_text(f'  "{_js_escape(GROUP_NAME)}",\n', encoding="utf-8")
    print(f"  {len(seen_agregats)} indicateurs, groupe « {GROUP_NAME} »")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Supprime le cache et re-télécharge.")
    args = parser.parse_args()

    if args.force and TMP_EXPORT.exists():
        TMP_EXPORT.unlink()

    t0 = time.time()
    print("=" * 64)
    print("ofgl-base-ei — Comptes consolidés des ensembles intercommunaux")
    print("=" * 64)

    records = _download_export()
    print(f"  {len(records)} records bruts")

    synth_sirens = _load_synth_sirens()
    print(f"  {len(synth_sirens)} entités EPCI dans la synthese")

    values, details, meta, seen_agregats = _build(records, synth_sirens)
    new_keys = sorted({PREFIX + ag for ag in seen_agregats})

    update_synthese(values, new_keys)
    write_details(details, meta)
    emit_snippet(seen_agregats)

    print(f"\nTerminé en {time.time()-t0:.1f}s")
    print(f"  → lancer ensuite : python scripts/insert_ei_indicators.py")


if __name__ == "__main__":
    main()
