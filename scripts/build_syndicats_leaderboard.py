"""Génère les fichiers leaderboard par (compétence, agrégat) pour le niveau Syndicats.

Pour chaque couple (compétence, agrégat), produit un fichier
``data/syndicats/leaderboards/{slug}.json`` au format :

    {
      "indicator": "Syndicats Eau (production, ...) — Recettes totales (€)",
      "competence": "Eau (production, traitement, ...)",
      "agregat": "Recettes totales",
      "years": [2017, ..., 2024],
      "syndicats": [
        {
          "siren": "200012345",
          "nom": "SIVU Petite Enfance Haute-Vienne Nord",
          "nature": "SIVU",
          "dep_code": "87",
          "n_membres": 4,
          "values": [v_2017, ..., v_2024]
        },
        ...
      ]
    }

Différence avec ``build_syndicats_decoratif.py`` :
  - le décoratif attribue à chaque commune membre la valeur totale du
    syndicat (pour colorier la carte) ;
  - le leaderboard liste 1 ligne par SYNDICAT (avec son nombre de membres).
    Sémantiquement correct pour un classement : "ce syndicat dépense X € pour
    cette compétence", pas "chacune des N communes membres dépense X €".

Réutilise le même slug que `build_syndicats_decoratif.py` pour que les deux
fichiers (carte + leaderboard) soient retrouvables par la même clé.

Index produit : ``data/syndicats/leaderboards/_index.json`` mapping
``"Syndicats {competence[:60]} — {agregat} (€)"`` → slug.
"""

from __future__ import annotations

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
OUT_DIR = DATA / "syndicats" / "leaderboards"
OUT_INDEX = OUT_DIR / "_index.json"

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES)

# Même seuil que decoratif pour cohérence : 10 syndicats minimum pour une compétence
MIN_SYNDICATS_POUR_COMPETENCE = 10


def _slug(s: str, max_len: int = 100) -> str:
    """Identique à la fonction du builder décoratif pour réutiliser les slugs."""
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
    """Slug fichier identique à build_syndicats_decoratif.synd_slug (carte +
    classement doivent porter le même nom de fichier). Hash6 de la compétence
    complète → pas de collision collèges/lycées."""
    h = hashlib.md5(competence.encode("utf-8")).hexdigest()[:6]
    return f"synd_{_slug(competence, 50)}_{h}__{_slug(agregat, 60)}"


def _has_any_value(values: list) -> bool:
    """Au moins une valeur non None dans la série annuelle."""
    return any(v is not None for v in values)


def _js_escape(s: str) -> str:
    """Échappe pour une chaîne JS entre guillemets doubles. Les compétences /
    agrégats syndicats ne contiennent ni " ni \\ (vérifié), mais on protège
    par sécurité pour rester robuste si la source OFGL/BANATIC évolue."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def write_indicators_snippet(pairs: list[tuple[str, str]], data_dir: Path) -> None:
    """Génère le bloc INDICATORS (app.js) des indicateurs syndicats avec le nom
    de compétence COMPLET (plus de troncature). 1 entrée par (compétence ×
    agrégat) effectivement écrit, trié par compétence puis agrégat, avec un
    séparateur commenté par compétence (style identique au bloc existant).

    Ce script est désormais le générateur reproductible de référence (l'ancien
    générateur, qui tronquait à [:50]/[:60], a été perdu)."""
    by_comp: dict[str, list[str]] = defaultdict(list)
    for comp, agr in pairs:
        by_comp[comp].append(agr)
    lines: list[str] = []
    for comp in sorted(by_comp):
        lines.append("  // ====================================================================")
        lines.append(f"  // Syndicats — {comp}")
        lines.append("  // ====================================================================")
        for agr in sorted(by_comp[comp]):
            key = f"Syndicats {comp} — {agr} (€)"
            label = f"{comp} — {agr}"
            group = f"Syndicats — {comp}"
            help_ = (
                f"Syndicats exerçant cette compétence. Compétence BANATIC complète : "
                f"« {comp} ». Agrégat OFGL : « {agr} ». "
                f"Source : BANATIC + ofgl-base-syndicats-consolidee."
            )
            lines.append(
                f'  {{ key: "{_js_escape(key)}", label: "{_js_escape(label)}", unit: "€",'
            )
            lines.append(f'    group: "{_js_escape(group)}", levels: ["syndicats"],')
            lines.append(f'    help: "{_js_escape(help_)}" }},')
    out = data_dir / "_tmp_indicators_syndicats.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"       Snippet INDICATORS : {out.name} "
          f"({len(pairs)} entrées, {len(by_comp)} compétences)")


def _extract_dep_from_insee(insee: str) -> str | None:
    """Extrait le code département à partir d'un code INSEE commune.
    - "01234" → "01"
    - "2A123" → "2A" (Corse)
    - "2B045" → "2B" (Corse)
    - "97134" → "971" (Outre-mer, codes à 3 chars : 971, 972, 973, 974, 976)
    """
    if not insee:
        return None
    insee = str(insee).strip()
    if len(insee) != 5:
        return None
    # Cas Corse : préfixe alpha
    if insee[:2] in ("2A", "2B"):
        return insee[:2]
    # Cas DOM : préfixe 97 → code à 3 chars
    if insee.startswith("97"):
        return insee[:3]
    # Cas standard
    return insee[:2]


def main() -> None:
    t0 = time.time()
    print("=" * 60)
    print("Construction des leaderboards Syndicats (1 ligne = 1 syndicat)")
    print("=" * 60)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Charger syndicats-2024.json
    print(f"\n[load] {SYND_JSON.name}…")
    d = json.loads(SYND_JSON.read_text(encoding="utf-8"))
    syndicats = d["syndicats"]
    print(f"  {len(syndicats)} syndicats chargés")

    # 2. Inventorier compétences (filtrer ≥10 syndicats)
    print("\n[inventory] compétences distinctes…")
    competence_syndicats = defaultdict(list)
    for s in syndicats:
        for c in s.get("competences", []):
            competence_syndicats[c].append(s)
    competences_retenues = {
        c: synds for c, synds in competence_syndicats.items()
        if len(synds) >= MIN_SYNDICATS_POUR_COMPETENCE
    }
    print(f"  {len(competence_syndicats)} compétences distinctes au total")
    print(f"  {len(competences_retenues)} compétences retenues (>= {MIN_SYNDICATS_POUR_COMPETENCE} syndicats)")

    # 3. Inventorier agrégats
    print("\n[inventory] agrégats financiers…")
    agregats_set = set()
    for s in syndicats:
        agregats_set.update((s.get("comptes") or {}).keys())
    agregats = sorted(agregats_set)
    print(f"  {len(agregats)} agrégats financiers")

    # 4. Construire fichiers
    print(f"\n[build] {len(competences_retenues)} × {len(agregats)} couples à examiner")
    print("Génération des leaderboards (filtrage des cas vides en cours)…")

    index_out = {}
    n_written = 0
    n_skipped_empty = 0
    total_size = 0
    # (compétence, agrégat) effectivement écrits → sert à générer le snippet
    # INDICATORS d'app.js (1:1 avec les fichiers leaderboard produits).
    pairs_written: list[tuple[str, str]] = []

    competences_list = list(competences_retenues.items())

    for competence, synds_concernes in competences_list:
        for agregat in agregats:
            # Pour chaque syndicat qui exerce la compétence ET a une série
            # pour cet agrégat, on construit une ligne {siren, nom, nature,
            # dep_code, n_membres, values}.
            rows = []
            for s in synds_concernes:
                serie = (s.get("comptes") or {}).get(agregat)
                if not serie:
                    continue
                # Normaliser à N_ANNEES valeurs (au cas où certains syndicats
                # auraient une série plus courte)
                serie_norm = list(serie[:N_ANNEES])
                while len(serie_norm) < N_ANNEES:
                    serie_norm.append(None)
                # Caster en float (les valeurs viennent d'OFGL en str/num mixte)
                serie_cast = []
                for v in serie_norm:
                    if v is None:
                        serie_cast.append(None)
                    else:
                        try:
                            serie_cast.append(float(v))
                        except (TypeError, ValueError):
                            serie_cast.append(None)
                if not _has_any_value(serie_cast):
                    continue
                # Codes département des communes membres : permet le filtrage
                # par dep en mode drill-down côté JS. Un syndicat peut couvrir
                # 1-3 départements (rarement plus), donc liste compacte.
                members = s.get("members") or []
                member_insees = sorted({
                    str(m["insee"]).strip()
                    for m in members
                    if m.get("insee")
                })
                member_deps = sorted({
                    d for d in (_extract_dep_from_insee(insee) for insee in member_insees)
                    if d is not None
                })
                rows.append({
                    "siren": s.get("siren") or "",
                    "nom": s.get("nom") or "(sans nom)",
                    "nature": s.get("nature") or "",
                    "dep_code": s.get("dep_code") or "",
                    "n_membres": len(members),
                    "member_deps": member_deps,
                    # Liste exhaustive des INSEE membres : permet côté JS de
                    # mapper INSEE commune → SIREN syndicat (pour highlighter
                    # toutes les communes membres au clic sur l'une d'elles).
                    "member_insees": member_insees,
                    "values": serie_cast,
                })

            if not rows:
                n_skipped_empty += 1
                continue

            # Tri stable par la dernière valeur connue (la plus récente, 2024)
            # — descendant pour que le top du leaderboard arrive en premier.
            # En cas d'ex æquo, on conserve l'ordre d'insertion (syndicats
            # apparaissent dans l'ordre du fichier source).
            def _last_value(r):
                for v in reversed(r["values"]):
                    if v is not None:
                        return v
                return 0
            rows.sort(key=_last_value, reverse=True)

            # Compétence COMPLÈTE dans la clé (plus de troncature [:60]).
            ind_key = f"Syndicats {competence} — {agregat} (€)"
            slug = synd_slug(competence, agregat)
            out_file = OUT_DIR / f"{slug}.json"

            payload = {
                "indicator": ind_key,
                "competence": competence,
                "agregat": agregat,
                "years": ANNEES,
                "syndicats": rows,
            }
            out_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            index_out[ind_key] = slug
            pairs_written.append((competence, agregat))
            total_size += out_file.stat().st_size
            n_written += 1
            if n_written % 200 == 0:
                elapsed = time.time() - t0
                rate = n_written / elapsed if elapsed > 0 else 0
                print(f"  écrits={n_written} vides={n_skipped_empty} ({rate:.0f}/s)")

    # 5. Écrire l'index
    OUT_INDEX.write_text(
        json.dumps(index_out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 6. Générer le snippet INDICATORS pour app.js (1:1 avec les fichiers écrits)
    write_indicators_snippet(pairs_written, DATA)

    print(f"\n[done] {n_written} fichiers écrits ({total_size/1024/1024:.1f} Mo)")
    print(f"       {n_skipped_empty} couples vides ignorés")
    print(f"       Index : {OUT_INDEX.name} ({len(index_out)} indicateurs)")
    print(f"\nTerminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
