"""Génère ``data/communes/meta-communes-2024.json``, un fichier light
réutilisable côté site pour le **leaderboard national** des communes.

Pourquoi ce fichier ?
---------------------
Le calque décoratif (``decoratif-communes-2024.json``) ne transporte que les
contours SVG et les valeurs : pas de noms ni de codes, pour rester compact
(~30 Mo gzippé). Pour afficher un classement national lisible côté site
(« top 50 des communes les plus dépensières »), on a besoin d'un mapping
``position dans le décoratif`` → ``nom, code INSEE, département``.

Format
------
Indexé positionnellement, dans le **même ordre** que le décoratif (donc
``meta.communes[i]`` correspond toujours à ``decoratif.communes[i]``) :

    {
      "communes": [
        ["L'Abergement-Clémenciat", "01001", "01", "Ain", 860],
        ["L'Abergement-de-Varey",   "01002", "01", "Ain", 245],
        ...
      ]
    }

Taille typique : ~1.5 Mo brut, ~500 Ko gzippé pour 35 000 communes.

Réutilise l'algorithme de jointure de ``write_communes_decoratif`` dans
``fetch_all.py`` : on parcourt synthese dans son ordre natif, on garde
uniquement les communes qui ont un contour SVG niveau FRA (les autres sont
exclues à la fois du décoratif et du meta), puis on émet la ligne dans le
même ordre.

Usage : ``python scripts/build_communes_meta.py``.

Idempotent. Pas de réseau, ne dépend que des fichiers déjà présents dans
``data/communes/``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYNTHESE_PATH = DATA / "communes" / "synthese-communes-2024.json"
SVG_PATH = DATA / "communes" / "communes-svg-FRA.json"
OUT_PATH = DATA / "communes" / "meta-communes-2024.json"


def main() -> None:
    t0 = time.time()
    print(f"[meta] lecture de {SYNTHESE_PATH.name} ...", end=" ", flush=True)
    synthese = json.loads(SYNTHESE_PATH.read_text(encoding="utf-8"))
    print(f"{len(synthese['communes'])} communes")

    print(f"[meta] lecture de {SVG_PATH.name} ...", end=" ", flush=True)
    svg_data = json.loads(SVG_PATH.read_text(encoding="utf-8"))
    # On reproduit exactement la sélection de write_communes_decoratif :
    # uniquement les paths niveau "FRA" (= calque France entière) indexés
    # par SIREN. Les autres niveaux de zoom (départemental, etc.) sont
    # ignorés ici, le drill-down lit ailleurs.
    svg_sirens: set[str] = {
        str(s.get("data_fill_id"))
        for s in svg_data
        if s.get("niveau_zoom") == "FRA"
    }
    print(f"{len(svg_sirens)} contours FRA")

    print("[meta] construction du tableau ...", end=" ", flush=True)
    communes_meta: list[list] = []
    skipped = 0
    for row in synthese["communes"]:
        siren = str(row.get("siren") or "")
        if siren not in svg_sirens:
            # Même filtre que le décoratif : la commune n'a pas de contour
            # FRA, on l'écarte pour garder l'indexation alignée.
            skipped += 1
            continue
        communes_meta.append([
            row.get("nom") or "",
            row.get("insee") or "",
            row.get("dep_code") or "",
            row.get("nom_dep") or "",
            row.get("population"),
        ])
    print(f"{len(communes_meta)} retenues, {skipped} sans contour FRA")

    payload = {
        # Documentation inline du format positionnel pour les lecteurs futurs
        "schema": ["nom", "insee", "dep_code", "dep_name", "population"],
        "communes": communes_meta,
    }

    print(f"[meta] écriture de {OUT_PATH.name} ...", end=" ", flush=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mo = OUT_PATH.stat().st_size / 1024 / 1024
    print(f"{size_mo:.2f} Mo en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
