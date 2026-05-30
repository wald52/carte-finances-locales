"""Télécharge les taux d'imposition des EPCIs (REI 2023-2024) et les
fusionne dans la synthèse des intercommunalités.

Côté EPCI, OFGL/REI publie les taux votés par l'EPCI sur les principaux
impôts directs locaux qu'il lève (en plus de ceux votés par la commune).
Le contribuable d'une commune en EPCI à fiscalité propre paye donc la
**SOMME** des taux communaux et intercommunaux — cette mise en visibilité
côté site est l'objectif de ce script.

Taux extraits :

  * **TFB** — taxe foncière sur les propriétés bâties, part EPCI
    (variable « FB - GFP / TAUX VOTÉ »)
  * **TFNB** — taxe foncière sur les propriétés non bâties, part EPCI
    (« FNB - GFP / TAUX VOTÉ »)
  * **TH-RS** — taxe d'habitation sur les résidences secondaires, part
    EPCI (« TH - INTERCOMMUNALITÉ / TAUX VOTÉ »)
  * **CFE** — cotisation foncière des entreprises, part EPCI.
    Particularité : la varlib varie selon le régime fiscal de l'EPCI
    (FPU, FPU+ZAE, FA, FPZ-ZAE, FPE-ZONE ÉOLIENNE). Comme un EPCI n'a
    qu'UN régime à la fois, on télécharge TOUTES les variantes et on
    prend celle qui a une valeur non-nulle pour chaque EPCI.

Couverture : 2023-2024 (limitation du dataset REI ; pas d'historique
pré-2023 au niveau commune/EPCI publié par OFGL).

Format injecté dans `data/intercommunalites/synthese-intercommunalites-2024.json` :
série multi-années de longueur 8 (2017-2024) avec ``[null × 6, v2023, v2024]``.

Idempotent.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

OFGL_EXPORT_JSON = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/rei/exports/json"

# Mapping varlib REI → clé d'indicateur stockée côté JSON.
# Pour la CFE, on a 5 régimes mutuellement exclusifs ; toutes les valeurs
# qui apparaissent vont dans le même indicateur côté JSON (un EPCI n'a
# qu'UN régime, donc 4 colonnes sur 5 sont nulles pour lui).
TAUX_MAPPING_DIRECT = {
    "FB - GFP / TAUX VOTÉ":          "Taux TFB voté EPCI (%)",
    "FNB - GFP / TAUX VOTÉ":         "Taux TFNB voté EPCI (%)",
    "TH - INTERCOMMUNALITÉ / TAUX VOTÉ": "Taux TH résid. secondaires voté EPCI (%)",
    # GEMAPI : taxe additionnelle optionnelle votée par l'EPCI, ventilée par les
    # services fiscaux sur les 4 taxes locales. OFGL publie pour chaque taxe
    # UN seul varlib agrégé (le taux effectivement appliqué) :
    #   - FB / FNB    : "TAUX NET INTERCOMMUNALITÉ"
    #   - TH          : "TAUX" (sans qualifier "NET")
    #   - CFE         : "TAUX INTERCOMMUNAL" (sans suffixe de régime fiscal).
    #     Pour la CFE, OFGL publie aussi 5 varlibs avec suffixe de régime
    #     (FP UNIQUE, FA, FPZ, FPE, FP UNIQUE OU EN ZAE) qui sont des VUES
    #     ANNOTÉES de la même valeur — on n'en a pas besoin, le varlib sans
    #     suffixe est la valeur de référence applicable à tous les régimes.
    # Pas de "TAUX VOTÉ" GEMAPI : la taxe est calculée par les services
    # fiscaux à partir du produit voté + bases d'imposition.
    "FB - GEMAPI / TAUX NET INTERCOMMUNALITÉ":  "Taux GEMAPI sur TFB EPCI (%)",
    "FNB - GEMAPI / TAUX NET INTERCOMMUNALITÉ": "Taux GEMAPI sur TFNB EPCI (%)",
    "TH GEMAPI - INTERCOMMUNALITÉ / TAUX":      "Taux GEMAPI sur TH-RS EPCI (%)",
    "CFE - GEMAPI / TAUX INTERCOMMUNAL":        "Taux GEMAPI sur CFE EPCI (%)",
}
TAUX_CFE_VARLIBS = [
    "CFE - INTERCOMMUNALITÉ / TAUX VOTÉ / FP UNIQUE",
    "CFE - INTERCOMMUNALITÉ / TAUX VOTÉ / FP UNIQUE OU EN ZAE",
    "CFE - INTERCOMMUNALITÉ / TAUX VOTÉ / FISCALITÉ ADDITIONNELLE OU FP DE ZONE (HORS ZONE)",
    "CFE - INTERCOMMUNALITÉ / TAUX VOTÉ / FPZ EN ZAE",
    "CFE - INTERCOMMUNALITÉ / TAUX VOTÉ / FPE EN ZONE ÉOLIENNE",
]
TAUX_CFE_KEY = "Taux CFE voté EPCI (%)"

# Plage temporelle alignée sur ofgl-base-gfp (et synthese-interco).
ANNEES_EPCI = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_REI = [2023, 2024]  # années publiées par REI


def _download_one(varlib: str, out_path: Path, force: bool) -> None:
    if out_path.exists() and not force:
        print(f"  [taux-epci]  {varlib[:60]:60s} -> cache "
              f"({out_path.stat().st_size//1024} Ko)")
        return
    params = {
        "where": f'varlib = "{varlib}"',
        # `sirepci` = SIREN de l'EPCI, présent dans REI
        "select": "annee,sirepci,valeur",
    }
    url = f"{OFGL_EXPORT_JSON}?{urllib.parse.urlencode(params)}"
    print(f"  [taux-epci]  {varlib[:60]:60s} -> téléchargement ...",
          end=" ", flush=True)
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    print(f"{len(data)/1024:.0f} Ko en {time.time()-t0:.1f}s")


def download_all_taux_epci(force: bool = False) -> Path:
    taux_dir = DATA / "taux"
    taux_dir.mkdir(parents=True, exist_ok=True)
    all_varlibs = list(TAUX_MAPPING_DIRECT.keys()) + TAUX_CFE_VARLIBS
    for varlib in all_varlibs:
        slug = (varlib.lower()
                .replace(" ", "_")
                .replace("/", "_")
                .replace("é", "e")
                .replace("(", "_")
                .replace(")", "_")
                .replace(",", "_"))
        out = taux_dir / f"rei-epci-{slug}.json"
        _download_one(varlib, out, force)
    return taux_dir


def build_index(taux_dir: Path) -> dict[str, dict[str, list]]:
    """Construit { siren_epci : { indicateur : [v2017..v2024] } }."""
    idx: dict[str, dict[str, list]] = {}

    def _slugify(varlib: str) -> str:
        return (varlib.lower()
                .replace(" ", "_").replace("/", "_").replace("é", "e")
                .replace("(", "_").replace(")", "_").replace(",", "_"))

    def _ingest(varlib: str, indicator_key: str) -> int:
        path = taux_dir / f"rei-epci-{_slugify(varlib)}.json"
        if not path.exists():
            return 0
        records = json.loads(path.read_text(encoding="utf-8"))
        n = 0
        for r in records:
            siren = (r.get("sirepci") or "").strip()
            if not siren:
                continue
            try:
                annee = int(r.get("annee") or 0)
            except (TypeError, ValueError):
                continue
            if annee not in ANNEES_REI:
                continue
            valeur = r.get("valeur")
            if valeur is None:
                continue
            year_idx = ANNEES_EPCI.index(annee)
            entry = idx.setdefault(siren, {})
            serie = entry.setdefault(indicator_key, [None] * len(ANNEES_EPCI))
            # On garde la 1re valeur non-null si plusieurs régimes répondent
            # (cas marginal — théoriquement un EPCI a un seul régime).
            if serie[year_idx] is None:
                serie[year_idx] = float(valeur)
            n += 1
        return n

    # 1. Taux directs (un seul varlib par indicateur)
    for varlib, key in TAUX_MAPPING_DIRECT.items():
        n = _ingest(varlib, key)
        print(f"  [taux-epci]  {key:50s} : {n} lignes")

    # 2. CFE : on parcourt les 5 régimes pour le même indicateur
    total_cfe = 0
    for varlib in TAUX_CFE_VARLIBS:
        total_cfe += _ingest(varlib, TAUX_CFE_KEY)
    print(f"  [taux-epci]  {TAUX_CFE_KEY:50s} : {total_cfe} lignes (5 régimes combinés)")

    return idx


def merge_into_synthese(taux_idx: dict[str, dict[str, list]]) -> None:
    """Injecte les taux dans data/intercommunalites/synthese-intercommunalites-2024.json.

    Choix méthodologique : **lecture stricte des varlibs OFGL**, sans
    transformation. Si REI ne publie pas de ligne pour un EPCI/varlib, la
    valeur reste `null` côté site. Pas d'heuristique « 0 factuel » : un
    null reste un null, comme dans la donnée source.
    """
    path = DATA / "intercommunalites" / "synthese-intercommunalites-2024.json"
    if not path.exists():
        print(f"  [warn]   {path.name} introuvable, on saute")
        return
    d = json.loads(path.read_text(encoding="utf-8"))

    new_keys = [TAUX_CFE_KEY] + list(TAUX_MAPPING_DIRECT.values())
    for k in new_keys:
        if k not in d["indicators"]:
            d["indicators"].append(k)

    matched = 0
    for ent in d.get("entities", []):
        siren = ent.get("siren")
        if not siren:
            continue
        series = taux_idx.get(siren)
        if not series:
            continue
        matched += 1
        values = ent.setdefault("values", {})
        for ind_key, serie in series.items():
            values[ind_key] = serie

    path.write_text(
        json.dumps(d, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    mo = path.stat().st_size / 1024 / 1024
    print(
        f"  [taux-epci]  synthese-interco enrichie : "
        f"{matched}/{len(d.get('entities', []))} EPCIs avec au moins un taux "
        f"publié par REI ({mo:.2f} Mo)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="Re-télécharge même si le fichier source existe déjà.",
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Saute le téléchargement, fusionne juste ce qui est en cache.",
    )
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("Taux d'imposition EPCI (REI 2023-2024)")
    print("=" * 60)

    if args.skip_download:
        taux_dir = DATA / "taux"
        print(f"  [taux-epci]  skip-download : lecture depuis {taux_dir}/")
    else:
        taux_dir = download_all_taux_epci(force=args.force)

    print()
    print("Construction de l'index par SIREN...")
    taux_idx = build_index(taux_dir)
    print(f"  [index]      {len(taux_idx)} EPCIs ont au moins un taux")

    print()
    print("Fusion dans la synthèse intercommunalités...")
    merge_into_synthese(taux_idx)

    print()
    print(f"Terminé en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
