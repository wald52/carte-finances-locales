"""Fetch & intègre ofgl-base-departements-fonctionnelle.

Présentation FONCTIONNELLE des comptes départementaux 2012-2024 : au lieu
de décomposer les dépenses par nature comptable (Frais de personnel,
Achats…), on les décompose par MISSION / POLITIQUE PUBLIQUE (Action sociale,
Enseignement, Transports…). Particulièrement pertinent pour le « modèle
social » car on peut isoler les dépenses APA/RSA/PCH, Frais d'hébergement
ASE, etc.

Architecture :

1. **Récupération** : un seul export JSON OFGL (niveau_hierarchique=1 +
   Budget principal) filtre les ~125 k records (vs 795 k records totaux du
   dataset) puis on traite tout en mémoire.

2. **Nomenclature** : entre 2012 et 2024, les départements ont migré de la
   M52 à la M57. Même CODE de fonction = label différent. On applique un
   mapping vers 7 catégories CANONIQUES :

     - Services généraux         (M52 0 = M57 0)
     - Sécurité                  (M52 1 = M57 1)
     - Enseignement              (M52 2 ≈ M57 2)
     - Culture, jeunesse, sports (M52 3 = M57 3)
     - Action sociale et santé   (M52 4+5 → M57 4)  ⭐ bucket social principal
     - Transports                (M52 8 = M57 8)
     - Aménagement et économie   (M52 6+7+9 → M57 5+6+7+9)  reste

   La fusion M52 4+5 → "Action sociale et santé" permet une série stable
   2012-2024 même quand le département a basculé en M57.

3. **Sortie** : on enrichit ``data/departements/synthese-departements-2024.json``
   en place avec de nouveaux indicateurs au format
   ``"F: {canonical_fonction} — {agregat}"``. La clé `F: ` permet au JS de
   les filtrer pour les afficher dans un sous-groupe « Présentation
   fonctionnelle ».

Lance ``python scripts/fetch_departements_fonctionnel.py`` après
``fetch_consolidees.py``. Idempotent : ré-exécutable, écrase les anciens
enrichissements fonctionnels (préfixe ``F: ``) sans toucher au reste.
"""

from __future__ import annotations

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
TMP_EXPORT = DATA / "_tmp_fonctionnel.json"  # cache local du fetch
EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "ofgl-base-departements-fonctionnelle/exports/json"
)

ANNEES = list(range(2012, 2025))
N_ANNEES = len(ANNEES)

# Mapping fonction → canonical (7 buckets). On utilise le label nom_fonction
# car le code seul est ambigu (M52 vs M57 réutilisent les mêmes codes 4/5/6/7
# avec des sémantiques différentes). Tout label non listé tombe dans
# "Autres".
CANONICAL = {
    # 0 ─ Services généraux (M52 et M57)
    "SERVICES GÉNÉRAUX": "Services généraux",
    "Services généraux": "Services généraux",
    # 1 ─ Sécurité (M52 et M57)
    "Sécurité": "Sécurité",
    # 2 ─ Enseignement
    "Enseignement": "Enseignement",
    "Enseignement, formation professionnelle et apprentissage": "Enseignement",
    # 3 ─ Culture / jeunesse / sports
    "Culture, vie sociale, jeunesse, sports et loisirs": "Culture, jeunesse, sports",
    # 4 + 5 (M52) → Action sociale et santé ; 4 (M57) déjà fusionné
    "Prévention médico-sociale": "Action sociale et santé",
    "Action sociale": "Action sociale et santé",
    "Santé et action sociale (hors APA, RSA et régularisations RMI)": "Action sociale et santé",
    # 8 ─ Transports (M52 et M57)
    "Transports": "Transports",
    # 5/6/7/9 → catch-all économie/aménagement/environnement
    "Aménagement des territoires et habitat": "Aménagement et économie",
    "Réseaux et infrastructures": "Aménagement et économie",
    "Aménagement et environnement": "Aménagement et économie",
    "Environnement": "Aménagement et économie",
    "Action économique": "Aménagement et économie",
    "Développement économique": "Aménagement et économie",
}

# Préfixe utilisé pour stocker les indicateurs fonctionnels dans la synthese.
# Permet au JS de les filtrer pour les regrouper sous un optgroup dédié.
PREFIX = "F: "


def _download_export() -> list[dict]:
    """Télécharge l'export JSON (filtré niveau_hierarchique=1 + Budget principal).

    ~32 Mo, ~125 k records. Mis en cache local dans data/_tmp_fonctionnel.json
    pour éviter de retélécharger entre runs (supprimer manuellement pour forcer
    un refresh)."""
    if TMP_EXPORT.exists():
        print(f"[cache] réutilisation {TMP_EXPORT.relative_to(ROOT)} "
              f"({TMP_EXPORT.stat().st_size/1024/1024:.1f} Mo)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))

    params = {
        "where": 'niveau_hierarchique=1 AND type_de_budget:"Budget principal"',
        "select": "exer,dep_code,dep_name,fonction,nom_fonction,agregat,"
                  "montant,euros_par_habitant,ptot",
    }
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:100]}…")
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=300) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024/1024:.1f} Mo en {time.time()-t0:.1f}s, "
          f"caché dans {TMP_EXPORT.relative_to(ROOT)}")
    return json.loads(raw.decode("utf-8"))


def main() -> None:
    print("=" * 60)
    print("Fetch ofgl-base-departements-fonctionnelle → synthese-dpt")
    print("=" * 60)

    t0 = time.time()
    records = _download_export()
    print(f"  {len(records)} records bruts (niveau=1, Budget principal)")

    # Inventaire des fonctions rencontrées vs mappées
    unmapped = set()
    for r in records:
        nf = r.get("nom_fonction")
        if nf and nf not in CANONICAL:
            unmapped.add(nf)
    if unmapped:
        print(f"  ⚠️  {len(unmapped)} fonctions sans mapping (ignorées) :")
        for u in sorted(unmapped):
            print(f"     - {u}")

    # Agrégation : (dep_code, year_idx, canonical_fonction, agregat)
    #   → {"montant": float, "euros_par_habitant": float}
    # On somme les montants à travers les codes M52/M57 qui mappent au même
    # canonical (cas Action sociale et santé : M52 f4 + M52 f5 + M57 f4 sont
    # additionnés). On somme aussi €/hab — bien que ce ne soit pas strictement
    # un total pondéré, c'est cohérent car les codes proviennent du même dpt
    # × année (1 nomenclature à la fois, donc pas de double-comptage).
    agg = defaultdict(lambda: defaultdict(lambda: {"montant": 0.0, "ehab": 0.0}))
    n_skipped = 0
    for r in records:
        nf = r.get("nom_fonction")
        canonical = CANONICAL.get(nf)
        if not canonical:
            n_skipped += 1
            continue
        try:
            year = int(r["exer"])
            if year < 2012 or year > 2024:
                continue
            yidx = year - 2012
        except (ValueError, KeyError):
            continue
        dep = r.get("dep_code")
        agregat = r.get("agregat")
        if not dep or not agregat:
            continue
        montant = r.get("montant")
        ehab = r.get("euros_par_habitant")
        key = (dep, canonical, agregat)
        cell = agg[key][yidx]
        if montant is not None:
            try:
                cell["montant"] += float(montant)
            except (TypeError, ValueError):
                pass
        if ehab is not None:
            try:
                cell["ehab"] += float(ehab)
            except (TypeError, ValueError):
                pass

    print(f"\n[aggregation] {len(agg)} (dpt × fonction × agregat) triplets "
          f"({n_skipped} records ignorés)")

    # Liste des (canonical_fonction, agregat) effectivement présents
    pairs = sorted({(canon, ag) for (_, canon, ag) in agg.keys()})
    print(f"  {len(pairs)} combinaisons (fonction × agregat) à exposer")

    # Charger synthese-departements
    print(f"\n[load] {SYNTHESE_FILE.relative_to(ROOT)}…")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    entities = synth.get("entities") or []
    print(f"  {len(entities)} entités départements")

    # Indicateurs déjà présents : on nettoie les anciens préfixe "F: " pour
    # repartir d'un état propre (idempotence).
    existing_indicators = synth.get("indicators") or []
    indicators_clean = [
        i for i in existing_indicators
        if not (i.get("key", "") if isinstance(i, dict) else i).startswith(PREFIX)
    ]
    print(f"  {len(existing_indicators) - len(indicators_clean)} anciens "
          f"indicateurs '{PREFIX}…' supprimés")

    # Construit les nouvelles entrées indicators (métadonnées)
    new_indicators = []
    for canon, ag in pairs:
        key = f"{PREFIX}{canon} — {ag}"
        new_indicators.append({
            "key": key,
            "label": f"{canon} — {ag}",
            "fonction": canon,
            "agregat": ag,
        })

    # Pour chaque entité, ajouter les nouvelles séries values
    print(f"\n[enrich] injection dans entities[].values…")
    n_cells_written = 0
    for ent in entities:
        dep = ent.get("code")
        if not dep:
            continue
        if "values" not in ent or ent["values"] is None:
            ent["values"] = {}
        # Nettoyer anciennes clés F:
        for k in list(ent["values"].keys()):
            if k.startswith(PREFIX):
                del ent["values"][k]
        # Injecter les nouvelles
        for canon, ag in pairs:
            key = f"{PREFIX}{canon} — {ag}"
            cell_year = agg.get((dep, canon, ag), {})
            serie = []
            has_any = False
            # Stockage en €/hab pour cohérence avec les autres indicateurs
            # dpt (Recettes totales etc. déjà en €/hab dans synthese). Permet
            # la comparaison directe entre dpt sur la carte.
            #
            # Note : la somme des €/hab à travers M52 f4 + M52 f5 + M57 f4
            # (cas Action sociale et santé) est correcte car les 3 rows
            # proviennent du même (dpt, année) → même ptot, donc
            # sum(montant/ptot) = sum(montant)/ptot. ✓
            for yidx in range(N_ANNEES):
                v = cell_year.get(yidx)
                if v is None:
                    serie.append(None)
                else:
                    ehab = v["ehab"]
                    serie.append(ehab if ehab != 0 else None)
                    if ehab != 0:
                        has_any = True
            if has_any:
                ent["values"][key] = serie
                n_cells_written += 1

    print(f"  {n_cells_written} séries non vides écrites")

    # Mettre à jour la liste indicators de la synthese
    synth["indicators"] = indicators_clean + new_indicators

    # Sauvegarder
    SYNTHESE_FILE.write_text(
        json.dumps(synth, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = SYNTHESE_FILE.stat().st_size / 1024 / 1024
    print(f"\n[save] {SYNTHESE_FILE.relative_to(ROOT)} : {sz:.2f} Mo")
    print(f"\nTerminé en {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
