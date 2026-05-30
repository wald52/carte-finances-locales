"""Fetch & intègre interne-base-extrafinanciere-departements au niveau département.

Base EXTRA-FINANCIÈRE des départements (OFGL). C'est une base INTERNE OFGL
qui alimente la datastory « zoom sur les dépenses départementales ». Les
données sont reprises ICI TELLES QUELLES (aucune transformation), avec un
avertissement de fiabilité côté UI (help des indicateurs) et dans sources.html.

⚠️ Particularités à connaître (documentées dans le help des indicateurs) :

  - Les données ne viennent PAS des comptes OFGL/BANATIC mais de sources
    tierces reprises par l'OFGL :
      * Effectifs collèges publics → ministère de l'Éducation nationale
      * Longueur de voirie         → fichiers dotations DGCL
  - Les deux RATIOS (« par km », « par collégien ») sont une valeur cumulée
    2019-2024 UNIQUE, publiée à l'identique sur chaque exercice 2017-2024
    (donc courbe plate sur la timeline — ce n'est pas un historique annuel).
    On la stocke telle quelle (verbatim) sur chaque année où la source la
    renseigne.
  - Certaines valeurs peuvent comporter des erreurs : base non vérifiée au
    même niveau que les comptes financiers OFGL.

4 indicateurs (préfixe « Extra-financier — ») au niveau département :

  | indicateur                                   | unité   | couverture source        |
  |----------------------------------------------|---------|--------------------------|
  | Effectifs collèges publics                   | élèves  | 2017-2024 (1 null)       |
  | Longueur de voirie (km)                      | km      | 2018-2024 (2017 = null)  |
  | Dépenses d'équipement voirie par km          | €/km    | valeur 2019-2024 répétée |
  | Dépenses d'équipement collèges par collégien | €/élève | valeur 2019-2024 répétée |

Codes département : la source utilise DÉJÀ 67A (CEA consolidée, pas 67+68) et
69/691 distincts → AUCUNE consolidation à faire (contrairement à fetch_sdis.py
ou fetch_syndicats_mdph.py). Seule normalisation : zéro-padding des codes
métropolitains à un chiffre (« 1 » → « 01 »), car la source publie « 1 » alors
que synthese-departements utilise « 01 ». 2A / 2B / 67A / 691 inchangés.

Couverture : 99 départements. DOM : 971 / 974 / 976 seulement (972 Martinique
et 973 Guyane absents de la source → restent gris, fidélité à la donnée).

Idempotent : nettoie les anciens indicateurs préfixe « Extra-financier — »
avant d'écrire. Lance après fetch_departements_fonctionnel.py.

Cache local : data/_tmp_extrafinanciere_dep.json (supprimer ou --force pour
re-télécharger).
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

# Force UTF-8 stdout (workaround Windows cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYNTHESE_FILE = DATA / "departements" / "synthese-departements-2024.json"
TMP_EXPORT = DATA / "_tmp_extrafinanciere_dep.json"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "interne-base-extrafinanciere-departements/exports/json"
)

ANNEES_DEP = list(range(2012, 2025))
N_ANNEES = len(ANNEES_DEP)

PREFIX = "Extra-financier — "

# (champ source, suffixe de clé indicateur). L'ordre fixe l'ordre d'affichage.
FIELDS = [
    ("nb_collegiens_public", "Effectifs collèges publics"),
    ("lgvoirie_km", "Longueur de voirie (km)"),
    ("depvoir_lgvoir", "Dépenses d'équipement voirie par km"),
    ("depcollpub_effectif", "Dépenses d'équipement collèges par collégien"),
]


def _download_export(force: bool) -> list[dict]:
    """Télécharge l'export JSON complet (~778 records, < 1 Mo).

    Mis en cache local dans data/_tmp_extrafinanciere_dep.json."""
    if TMP_EXPORT.exists() and not force:
        print(f"[cache] réutilisation {TMP_EXPORT.relative_to(ROOT)} "
              f"({TMP_EXPORT.stat().st_size/1024:.0f} Ko)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))

    params = {
        "select": "exer,dep_code,dep_name,"
                  + ",".join(f for f, _ in FIELDS),
    }
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:100]}…")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024:.0f} Ko en {time.time()-t0:.1f}s")
    return json.loads(raw.decode("utf-8"))


def _norm_code(c: str | None) -> str | None:
    """Normalise le code département source vers la convention synthese.

    La source publie les codes métropolitains à un chiffre SANS zéro de
    tête (« 1 » au lieu de « 01 »). On pad uniquement ce cas. Les codes
    déjà à 2/3 chiffres (10..95, 971..976), Corse (2A/2B), CEA (67A) et
    Métropole de Lyon (691) sont laissés tels quels.
    """
    if c is None:
        return None
    c = str(c).strip()
    if not c:
        return None
    if c.isdigit() and len(c) == 1:
        return "0" + c
    return c


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch base extra-financière dpt")
    parser.add_argument("--force", action="store_true",
                        help="Supprime le cache et re-télécharge.")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 64)
    print("Base extra-financière départements (collèges & voirie) — OFGL")
    print("=" * 64)

    records = _download_export(args.force)
    print(f"  {len(records)} records bruts")

    # data[dep][suffixe] = série[N_ANNEES] de valeurs VERBATIM (None par défaut)
    data: dict[str, dict[str, list]] = defaultdict(
        lambda: {suf: [None] * N_ANNEES for _, suf in FIELDS}
    )
    deps_seen: set[str] = set()
    n_skipped = 0
    for r in records:
        try:
            year = int(r["exer"])
        except (KeyError, ValueError, TypeError):
            n_skipped += 1
            continue
        if year < 2012 or year > 2024:
            n_skipped += 1
            continue
        yidx = year - 2012
        dep = _norm_code(r.get("dep_code"))
        if not dep:
            n_skipped += 1
            continue
        deps_seen.add(dep)
        for field, suf in FIELDS:
            v = r.get(field)
            if v is not None:
                # Stockage TEL QUEL : int pour les effectifs, float sinon.
                data[dep][suf][yidx] = v

    print(f"  {len(deps_seen)} départements distincts dans la source")

    # Charger synthese-departements
    print(f"\n[load] {SYNTHESE_FILE.relative_to(ROOT)}…")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    entities = synth.get("entities") or []
    print(f"  {len(entities)} entités départements")

    # Idempotence : retirer les anciens indicateurs / valeurs préfixe PREFIX
    existing = synth.get("indicators") or []
    indicators_clean = [
        i for i in existing
        if not (i.get("key", "") if isinstance(i, dict) else i).startswith(PREFIX)
    ]
    n_removed = len(existing) - len(indicators_clean)
    if n_removed:
        print(f"  {n_removed} anciens indicateurs « {PREFIX}… » supprimés")

    # Clés indicateurs (ordre = FIELDS)
    all_keys = [PREFIX + suf for _, suf in FIELDS]

    # Injection dans entities[].values
    matched = 0
    per_key_filled: dict[str, int] = {k: 0 for k in all_keys}
    for ent in entities:
        code = str(ent.get("code") or "").strip()
        values = ent.setdefault("values", {})
        # Nettoyer anciennes clés
        for k in list(values.keys()):
            if k.startswith(PREFIX):
                del values[k]
        dep_data = data.get(code)
        if not dep_data:
            continue
        wrote_any = False
        for field, suf in FIELDS:
            key = PREFIX + suf
            serie = dep_data[suf]
            if any(v is not None for v in serie):
                values[key] = serie
                per_key_filled[key] += 1
                wrote_any = True
        if wrote_any:
            matched += 1

    # Codes source non rattachés à une entité synthese (diagnostic)
    synth_codes = {str(e.get("code") or "").strip() for e in entities}
    unmatched = sorted(deps_seen - synth_codes)

    synth["indicators"] = indicators_clean + all_keys
    SYNTHESE_FILE.write_text(
        json.dumps(synth, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = SYNTHESE_FILE.stat().st_size / 1024 / 1024

    print(f"\n[enrich] {matched} départements enrichis")
    for k in all_keys:
        print(f"    {per_key_filled[k]:>3} dpts  ←  {k}")
    if unmatched:
        print(f"\n  ⚠️  {len(unmatched)} codes source sans entité synthese "
              f"(ignorés) : {', '.join(unmatched)}")
    print(f"\n[save] {SYNTHESE_FILE.relative_to(ROOT)} : {sz:.2f} Mo")
    print(f"Terminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
