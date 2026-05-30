"""Génère un fichier de détail par syndicat, lazy-loadé côté JS quand
l'utilisateur ouvre le panneau d'un syndicat dans le drill-down.

Format de sortie : ``data/syndicats/details/{siren}.json``
    {
      "siren": "...",
      "nom": "...",
      "nature": "...",
      "dep_code": "...",
      "dep_name": "...",
      "commune_siege_nom": "...",
      "date_creation": "...",
      "population_totale": ...,
      "nb_membres_declare": ...,
      "competences": [...],          # liste des compétences exercées
      "members": [{"insee": "...", "nom": "..."}, ...],
      "years": [2017, ..., 2024],
      "comptes": {                   # 43 agrégats × 8 années
        "Achats et charges externes": [v_2017, ..., v_2024],
        ...
      }
    }

L'avantage du fichier par syndicat (vs charger syndicats-2024.json en entier
qui fait 43 Mo) : chaque détail fait ~3-8 Ko → chargement lazy instantané au
clic, et seuls les syndicats consultés sont téléchargés.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

# Force UTF-8 stdout
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYND_JSON = DATA / "syndicats" / "syndicats-2024.json"
OUT_DIR = DATA / "syndicats" / "details"

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES)


def _cast_serie(serie) -> list:
    """Normalise une série à N_ANNEES valeurs (None pour manquants), casts en float."""
    out = []
    for i in range(N_ANNEES):
        v = serie[i] if serie and i < len(serie) else None
        if v is None:
            out.append(None)
        else:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(None)
    return out


def _slim_member(m: dict) -> dict:
    """Conserve uniquement les champs utiles côté UI : insee, nom, population."""
    return {
        "insee": str(m.get("insee") or ""),
        "nom": m.get("nom") or "",
        "population": m.get("population"),
    }


def main() -> None:
    t0 = time.time()
    print("=" * 60)
    print("Construction des détails par syndicat")
    print("=" * 60)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n[load] {SYND_JSON.name}…")
    d = json.loads(SYND_JSON.read_text(encoding="utf-8"))
    syndicats = d["syndicats"]
    print(f"  {len(syndicats)} syndicats chargés")

    print(f"\n[build] écriture dans {OUT_DIR.relative_to(ROOT)}/…")
    n_written = 0
    total_size = 0
    skipped_no_siren = 0

    for s in syndicats:
        siren = s.get("siren")
        if not siren:
            skipped_no_siren += 1
            continue

        # Conversion des comptes : on garde tous les agrégats déclarés
        # (même ceux entièrement à None — l'UI affichera "—" pour eux).
        comptes_in = s.get("comptes") or {}
        comptes_out = {
            agregat: _cast_serie(serie)
            for agregat, serie in comptes_in.items()
        }

        members_in = s.get("members") or []
        members_out = [_slim_member(m) for m in members_in if m.get("insee")]
        # Membres non-communes (EPCI / personne morale) : structure réelle du
        # syndicat de second degré, affichée à part dans le panneau. Les
        # communes de `members` proviennent (en partie) de l'expansion de ces
        # EPCI — chacune porte alors `via_epci`.
        member_groups_out = [
            {
                "siren": str(g.get("siren") or ""),
                "nom": g.get("nom") or "",
                "categ": g.get("categ") or "",
                "nb_communes": g.get("nb_communes"),
            }
            for g in (s.get("member_groups") or [])
        ]

        payload = {
            "siren": siren,
            "nom": s.get("nom") or "(sans nom)",
            "nature": s.get("nature") or "",
            "dep_code": s.get("dep_code") or "",
            "dep_name": s.get("dep_name") or "",
            "commune_siege_nom": s.get("commune_siege_nom") or "",
            "date_creation": s.get("date_creation") or "",
            "population_totale": s.get("population_totale"),
            "nb_membres_declare": s.get("nb_membres_declare"),
            "competences": s.get("competences") or [],
            "members": members_out,
            "member_groups": member_groups_out,
            "years": ANNEES,
            "comptes": comptes_out,
        }

        out_file = OUT_DIR / f"{siren}.json"
        out_file.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        total_size += out_file.stat().st_size
        n_written += 1
        if n_written % 1000 == 0:
            print(f"  {n_written}/{len(syndicats)}…")

    print(f"\n[done] {n_written} fichiers écrits ({total_size/1024/1024:.1f} Mo)")
    if skipped_no_siren:
        print(f"       {skipped_no_siren} syndicats ignorés (sans SIREN)")
    print(f"       Moyenne : {total_size/max(n_written,1):.0f} octets/fichier")
    print(f"\nTerminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
