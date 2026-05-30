"""Génère les fichiers décoratifs pour le niveau "Syndicats" du site.

Pour chaque (compétence, agrégat financier), produit un fichier
``data/syndicats/decoratif-values/{slug}.json`` au format sparse :

    {
      "indicator": "Eau - Recettes totales (€)",
      "competence": "Eau (production, traitement, stockage, ...)",
      "agregat": "Recettes totales",
      "years": [2017, ..., 2024],
      "values_sparse": [[idx_meta, v2017, v2018, ..., v2024], ...]
    }

L'index `idx_meta` est aligné sur ``meta-communes-2024.json`` (même
ordre positionnel que decoratif-paths). Côté JS : reconstruction de
l'array dense au chargement.

Mécanisme :
  - Pour chaque commune membre d'un syndicat exerçant la compétence,
    on récupère les valeurs annuelles du syndicat pour cet agrégat.
  - En cas de chevauchement (1 commune membre de 2+ syndicats pour la
    même compétence), on SOMME les valeurs.

Filtres pour éviter de produire des fichiers vides :
  - Seules les compétences exercées par >= 10 syndicats sont traitées.
  - Seuls les (compétence, agrégat) avec >= 1 cellule effective sont
    écrits sur disque.

Index produit : ``data/syndicats/decoratif-values/_index.json``
mapping ``"{competence} - {agregat} (€)"`` → slug.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

# Force UTF-8 stdout
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYND_JSON = DATA / "syndicats" / "syndicats-2024.json"
META_COMMUNES = DATA / "communes" / "meta-communes-2024.json"
OUT_DIR = DATA / "syndicats" / "decoratif-values"
OUT_INDEX = OUT_DIR / "_index.json"

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES)

# Seuil minimal pour considérer une compétence
MIN_SYNDICATS_POUR_COMPETENCE = 10


def _slug(s: str, max_len: int = 100) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    out = []
    for c in s:
        if c.isalnum() or c == "-":
            out.append(c)
        else:
            out.append("_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    s = s.strip("_")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s


def synd_slug(competence: str, agregat: str) -> str:
    """Slug fichier ``(compétence × agrégat)`` court ET sans collision.

    Le préfixe lisible (50 car. slugifiés) aide au debug ; le hash6 de la
    compétence COMPLÈTE garantit l'unicité même quand deux compétences
    partagent leurs 50/60 premiers caractères (cas collèges/lycées). Doit
    être IDENTIQUE dans build_syndicats_leaderboard.py (mêmes noms de
    fichiers carte + classement)."""
    h = hashlib.md5(competence.encode("utf-8")).hexdigest()[:6]
    return f"synd_{_slug(competence, 50)}_{h}__{_slug(agregat, 60)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files", type=int, default=None,
                        help="Limite le nombre de fichiers générés (debug).")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("Construction des fichiers décoratifs Syndicats")
    print("=" * 60)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Charger syndicats-2024.json
    print(f"\n[load] {SYND_JSON.name}…")
    d = json.loads(SYND_JSON.read_text(encoding="utf-8"))
    syndicats = d["syndicats"]
    print(f"  {len(syndicats)} syndicats chargés")

    # 2. Charger meta-communes pour avoir l'ordre positionnel des INSEE
    print(f"[load] {META_COMMUNES.name}…")
    meta = json.loads(META_COMMUNES.read_text(encoding="utf-8"))
    insee_to_idx = {}
    for idx, entry in enumerate(meta["communes"]):
        insee = entry[1] if len(entry) > 1 else None
        if insee:
            insee_to_idx[str(insee).strip()] = idx
    n_communes = len(meta["communes"])
    print(f"  {n_communes} communes (positions positionnelles)")

    # 3. Inventorier toutes les compétences et compter syndicats par compétence
    print("\n[inventory] compétences distinctes…")
    competence_syndicats = defaultdict(list)  # {competence: [syndicat_dict]}
    for s in syndicats:
        for c in s.get("competences", []):
            competence_syndicats[c].append(s)
    print(f"  {len(competence_syndicats)} compétences distinctes au total")

    # Filtrer compétences exercées par >= 10 syndicats
    competences_retenues = {
        c: synds for c, synds in competence_syndicats.items()
        if len(synds) >= MIN_SYNDICATS_POUR_COMPETENCE
    }
    print(f"  {len(competences_retenues)} compétences retenues (>= {MIN_SYNDICATS_POUR_COMPETENCE} syndicats)")
    skipped = len(competence_syndicats) - len(competences_retenues)
    if skipped:
        print(f"  {skipped} compétences ignorées (trop peu de syndicats)")

    # 4. Inventorier les agrégats financiers disponibles
    print("\n[inventory] agrégats financiers…")
    agregats_set = set()
    for s in syndicats:
        agregats_set.update((s.get("comptes") or {}).keys())
    agregats = sorted(agregats_set)
    print(f"  {len(agregats)} agrégats financiers")

    # 5. Construire fichiers
    print(f"\n[build] {len(competences_retenues)} × {len(agregats)} = {len(competences_retenues)*len(agregats)} couples maximum")
    print("Génération des fichiers values (filtrage des cas vides en cours)…")

    index_out = {}
    n_written = 0
    n_skipped_empty = 0
    total_size = 0

    competences_list = list(competences_retenues.items())
    pairs_max = len(competences_list) * len(agregats)
    if args.max_files:
        pairs_max = min(pairs_max, args.max_files)
    pair_idx = 0

    for competence, synds_concernes in competences_list:
        for agregat in agregats:
            pair_idx += 1
            if args.max_files and pair_idx > args.max_files:
                break

            # Construire l'array sparse pour ce (compétence, agrégat)
            # values_by_idx[idx] = [v_2017, ..., v_2024]
            values_by_idx: dict[int, list] = {}
            n_effective = 0
            for s in synds_concernes:
                serie = (s.get("comptes") or {}).get(agregat)
                if not serie:
                    continue
                # Pour chaque membre, sommer dans values_by_idx
                for m in s.get("members", []):
                    insee = m.get("insee")
                    if not insee:
                        continue
                    idx = insee_to_idx.get(str(insee).strip())
                    if idx is None:
                        continue
                    cur = values_by_idx.get(idx)
                    if cur is None:
                        cur = [None] * N_ANNEES
                        values_by_idx[idx] = cur
                    for i in range(N_ANNEES):
                        v = serie[i] if i < len(serie) else None
                        if v is None:
                            continue
                        if cur[i] is None:
                            cur[i] = float(v)
                            n_effective += 1
                        else:
                            cur[i] += float(v)

            # Filtrer : si aucune valeur effective, skip
            if n_effective == 0:
                n_skipped_empty += 1
                continue

            # Indicateur — compétence COMPLÈTE dans la clé (plus de troncature
            # [:60] : nom complet affiché + clés collèges/lycées distinctes).
            ind_key = f"Syndicats {competence} — {agregat} (€)"
            slug = synd_slug(competence, agregat)
            out_file = OUT_DIR / f"{slug}.json"

            # Format sparse : [[idx, v0, v1, ..., v7], ...]
            sparse = [[idx] + serie for idx, serie in sorted(values_by_idx.items())]

            payload = {
                "indicator": ind_key,
                "competence": competence,
                "agregat": agregat,
                "years": ANNEES,
                "values_sparse": sparse,
                "n_communes": len(values_by_idx),
            }
            out_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            index_out[ind_key] = slug
            total_size += out_file.stat().st_size
            n_written += 1
            if n_written % 100 == 0:
                elapsed = time.time() - t0
                rate = n_written / elapsed
                remaining = (pairs_max - pair_idx) / rate if rate else 0
                print(f"  [{pair_idx}/{pairs_max}] écrits={n_written} vides={n_skipped_empty} "
                      f"({rate:.0f}/s, reste ~{remaining/60:.0f} min)")

        if args.max_files and pair_idx > args.max_files:
            break

    # 6. Écrire l'index
    OUT_INDEX.write_text(
        json.dumps(index_out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n[done] {n_written} fichiers écrits ({total_size/1024/1024:.1f} Mo)")
    print(f"       {n_skipped_empty} couples vides ignorés")
    print(f"       Index : {OUT_INDEX.name} ({len(index_out)} indicateurs)")
    print(f"\nTerminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
