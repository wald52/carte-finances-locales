"""Enrichit meta-communes-2024.json avec un siren_ept additionnel.

Contexte :
  Pour les ~131 communes de Paris + petite couronne (75/92/93/94),
  le mapping `commune → siren_epci` stocké pointe sur la **Métropole
  du Grand Paris (MGP)** (SIREN 200054781). Or la MGP est un EPCI à
  statut particulier qui SUPERPOSE 11 EPT (Établissements Publics
  Territoriaux), chacun avec ses propres comptes ET son propre FPIC.

  Conséquence avec le mapping actuel : pour les indicateurs disponibles
  uniquement au niveau EPT (notamment FPIC), la coloration carto échoue
  (MGP.values[FPIC] == null) → toutes ces communes en gris.

  Solution sans synthèse : ajouter un POINTEUR ADDITIONNEL siren_ept
  dans meta-communes. Côté JS, fallback siren_epci → siren_ept quand
  la valeur principale est null. On lit ainsi la **donnée OFGL réelle**
  à un niveau ou l'autre selon ce qui est publié, sans inventer de
  valeur agrégée.

Source du mapping :
  ``detail_compositions_intercommunales_2012_2024`` (OFGL/DGCL). Pour
  une commune avec double rattachement MGP+EPT, le dataset publie DEUX
  lignes par année (annee_texte), une par niveau. On extrait la ligne
  EPT (≠ MGP).

Format de sortie :
  meta-communes-2024.json passe de schema
    [nom, insee, dep_code, dep_name, population, siren_epci]
  à
    [nom, insee, dep_code, dep_name, population, siren_epci, siren_ept_or_null]

  Seules les communes parisiennes ont siren_ept renseigné. Toutes les
  autres ont None à la position 6 (rétrocompatible : les consommateurs
  qui accèdent par index 0-5 ne sont pas affectés).
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
META_FILE = DATA / "communes" / "meta-communes-2024.json"
TMP_EXPORT = DATA / "_tmp_compositions.json"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "detail_compositions_intercommunales_2012_2024/exports/json"
)

# SIREN MGP — on identifie l'EPT comme "l'EPCI d'une commune dont le
# SIREN n'est PAS celui de la MGP". Pas besoin de hardcoder les 11 EPT.
SIREN_MGP = "200054781"


def _download_export() -> list[dict]:
    """Télécharge le dataset compositions intercommunales (toutes années
    confondues). Filtre serveur sur 2024 pour réduire le volume."""
    if TMP_EXPORT.exists():
        print(f"[cache] réutilisation {TMP_EXPORT.relative_to(ROOT)} "
              f"({TMP_EXPORT.stat().st_size/1024/1024:.1f} Mo)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))

    params = {
        "where": 'annee_texte="2024" AND type_inst="commune"',
        "select": "insee,siren,nom,siren_epci,nom_epci",
    }
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:120]}…")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024/1024:.1f} Mo en {time.time()-t0:.1f}s")
    return json.loads(raw.decode("utf-8"))


def main() -> None:
    t0 = time.time()
    print("=" * 60)
    print("Enrichissement meta-communes avec siren_ept (Paris/PC)")
    print("=" * 60)

    records = _download_export()
    print(f"  {len(records)} records (compositions communes 2024)")

    # Pour chaque INSEE, on a typiquement 1 record (le mapping commune→EPCI).
    # Pour les communes MGP, on a 2 records : un pour la MGP, un pour l'EPT.
    # On veut récupérer le SIREN qui n'est PAS la MGP = c'est l'EPT.
    insee_to_ept: dict[str, str] = {}
    insee_to_epci: dict[str, str] = {}
    for r in records:
        insee = str(r.get("insee") or "").strip()
        siren_epci = str(r.get("siren_epci") or "").strip()
        if not insee or not siren_epci:
            continue
        if siren_epci == SIREN_MGP:
            # On note que cette commune appartient à la MGP (peut servir
            # à valider que les EPT trouvés sont cohérents).
            insee_to_epci[insee] = siren_epci
        else:
            # Pour les communes MGP, c'est l'EPT (l'autre ligne du couple).
            # Pour les communes hors MGP, c'est leur EPCI normal — on
            # n'enregistre PAS dans siren_ept car le mapping principal
            # est déjà correct.
            #
            # On utilise un test : si une autre ligne existe avec MGP pour
            # le même INSEE, alors celle-ci est bien l'EPT (cas Paris/PC).
            pass

    # Deuxième passe : on identifie les EPT en regardant uniquement les
    # communes pour lesquelles on a aussi une ligne MGP (= doublement
    # rattachées). Pour ces communes, l'autre siren_epci ≠ MGP est l'EPT.
    insee_with_mgp = set(insee_to_epci.keys())
    for r in records:
        insee = str(r.get("insee") or "").strip()
        siren_epci = str(r.get("siren_epci") or "").strip()
        if not insee or not siren_epci:
            continue
        if insee not in insee_with_mgp:
            continue  # commune sans rattachement MGP → pas d'EPT
        if siren_epci != SIREN_MGP:
            # C'est la ligne EPT pour une commune MGP
            insee_to_ept[insee] = siren_epci

    print(f"\n  {len(insee_with_mgp)} communes rattachées à la MGP")
    print(f"  {len(insee_to_ept)} mappings INSEE → SIREN EPT extraits")

    # Distinct EPTs trouvés (vérification cohérence)
    ept_sirens = sorted(set(insee_to_ept.values()))
    print(f"  {len(ept_sirens)} EPT distincts identifiés :")
    for s in ept_sirens:
        n = sum(1 for v in insee_to_ept.values() if v == s)
        print(f"     SIREN {s} → {n} communes")

    # Charger meta-communes
    print(f"\n[load] {META_FILE.relative_to(ROOT)}…")
    meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    rows = meta.get("communes") or []
    schema = meta.get("schema") or []
    print(f"  {len(rows)} communes, schema actuel : {schema}")

    # Enrichir : on étend chaque ligne avec siren_ept (None par défaut).
    # Si la ligne avait déjà 7 colonnes (re-run idempotent), on écrase
    # la position 6.
    n_enriched = 0
    for row in rows:
        # row format: [nom, insee, dep_code, dep_name, population, siren_epci, (siren_ept)?]
        insee = str(row[1]).strip() if len(row) > 1 else ""
        ept = insee_to_ept.get(insee) or None
        if len(row) >= 7:
            row[6] = ept
        else:
            row.append(ept)
        if ept:
            n_enriched += 1

    # Mettre à jour le schema déclaré
    new_schema = ["nom", "insee", "dep_code", "dep_name", "population", "siren_epci", "siren_ept"]
    meta["schema"] = new_schema

    META_FILE.write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = META_FILE.stat().st_size / 1024 / 1024
    print(f"\n[save] {n_enriched} communes enrichies avec siren_ept")
    print(f"       {META_FILE.relative_to(ROOT)} : {sz:.2f} Mo")
    print(f"\nTerminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
