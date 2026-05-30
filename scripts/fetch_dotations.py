"""Télécharge et intègre les dotations OFGL (communes/EPCIs/dpts/régions).

Quatre datasets traités :
  - ``dotations-communes`` (277 variables, ~28 M lignes)
  - ``dotations-gfp`` (101 variables, ~690 k lignes)
  - ``dotations-departements`` (87 variables, ~45 k lignes)
  - ``dotations-regions`` (14 variables, ~450 lignes)

Structure des datasets : OFGL publie une ligne par (commune × catégorie ×
variable × exercice). Chaque ligne contient ``categorie`` + ``variable`` +
``unite`` + ``valeur`` + l'identifiant de la collectivité (``code_insee``
pour communes, ``siren_epci`` pour EPCIs, etc.).

Architecture cache : un fichier par (categorie, variable, dataset) :
``data/dotations/{ds}__{cat-slug}__{var-slug}.json``.

Mode d'écriture :
  - dotations-communes → synthese-communes-2024.json + by-dep + decoratif-values
  - dotations-gfp → synthese-intercommunalites-2024.json
  - dotations-departements → synthese-departements-2024.json
  - dotations-regions → synthese-regions-2024.json
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INVENTORY_PATH = ROOT / "inventory_clean.json"
DOTATIONS_DIR = DATA / "dotations"

OFGL_EXPORT_JSON = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{ds}/exports/json"

# Plage temporelle alignée sur les synthèses existantes
ANNEES_COMMUNES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_EPCI = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_DEP = [2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_REG = [2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]

# OFGL publie 2018-2026 pour dotations (mais 2026 partiel) ; on s'aligne sur
# les années des synthèses cibles
ANNEES_DOTATIONS_DISPO = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]

DATASETS = {
    # OFGL utilise des noms de colonnes différents pour identifier la
    # collectivité selon le dataset (subtil pour les EPCIs : c'est `siren`,
    # pas `siren_epci` comme dans REI).
    "dotations-communes":     {"id_field": "code_insee",       "annees": ANNEES_COMMUNES},
    "dotations-gfp":          {"id_field": "siren",            "annees": ANNEES_EPCI},
    "dotations-departements": {"id_field": "code_departement", "annees": ANNEES_DEP},
    "dotations-regions":      {"id_field": "code_region",      "annees": ANNEES_REG},
}


def _slug(s: str, max_len: int = 80) -> str:
    """Slug ASCII stable. Reproduit la logique de fetch_taux_communes."""
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


def _indicator_key(ds: str, cat: str, var: str) -> str:
    """Construit la clé d'indicateur stable, lisible côté utilisateur.

    Format : ``{cat} - {var} ({dataset_suffix})``. Le suffixe permet
    d'éviter les collisions entre datasets (ex: « DILICO » existe dans
    les 4 datasets, mais avec des valeurs différentes selon le niveau).
    """
    suffix_map = {
        "dotations-communes": "commune",
        "dotations-gfp": "EPCI",
        "dotations-departements": "département",
        "dotations-regions": "région",
    }
    suffix = suffix_map.get(ds, "?")
    # Forme courte
    return f"{cat} - {var} ({suffix})"


def _file_slug(ds: str, cat: str, var: str) -> str:
    """Nom de fichier cache pour une variable donnée."""
    return f"{_slug(ds, 30)}__{_slug(cat, 30)}__{_slug(var, 80)}"


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def _download_one(ds: str, cat: str, var: str, force: bool) -> Path:
    DOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out = DOTATIONS_DIR / f"{_file_slug(ds, cat, var)}.json"
    if out.exists() and not force:
        return out
    info = DATASETS[ds]
    params = {
        "where": f'categorie = "{cat}" AND variable = "{var}"',
        "select": f"exercice,{info['id_field']},valeur,unite",
    }
    url = f"{OFGL_EXPORT_JSON.format(ds=ds)}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=300) as resp:
            data = resp.read()
    except Exception as e:
        print(f"    ERR {var[:60]}: {e}")
        return out
    out.write_bytes(data)
    return out


def download_all(force: bool = False) -> None:
    with open(INVENTORY_PATH, encoding="utf-8") as f:
        inv = json.load(f)
    t0 = time.time()
    total_done = 0
    for ds, info in inv.items():
        n_done = 0
        n_to_dl = 0
        for v in info["vars"]:
            cat = v["categorie"]
            var = v["variable"]
            out_path = DOTATIONS_DIR / f"{_file_slug(ds, cat, var)}.json"
            if out_path.exists() and not force:
                n_done += 1
                continue
            n_to_dl += 1
        print(f"  [dotations] {ds}: cache {n_done}, à télécharger {n_to_dl}")
        # Téléchargements
        i = 0
        for v in info["vars"]:
            cat = v["categorie"]
            var = v["variable"]
            out_path = DOTATIONS_DIR / f"{_file_slug(ds, cat, var)}.json"
            if out_path.exists() and not force:
                continue
            i += 1
            t1 = time.time()
            _download_one(ds, cat, var, force)
            sz = out_path.stat().st_size if out_path.exists() else 0
            print(f"  [dotations] [{i}/{n_to_dl}] {ds[:20]:20} | {cat[:25]:25} | {var[:60]:60} "
                  f"-> {sz/1024:.0f} Ko en {time.time()-t1:.1f}s")
        total_done += len(info["vars"])
    print(f"  [dotations] total téléchargé/en cache : {total_done} variables en {time.time()-t0:.1f}s")


# ---------------------------------------------------------------------------
# Construction de l'index : { id : { indicator_key : [v_y1, ..., v_yN] } }
# ---------------------------------------------------------------------------

def _normalize_insee(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s).strip()
    return s.zfill(5) if s.isdigit() else s


def _parse_exer(ex) -> int | None:
    """Extrait l'année d'un champ ``exercice`` OFGL (peut être ISO timestamp
    ou juste l'année en string)."""
    if ex is None:
        return None
    s = str(ex)
    if "-" in s:
        s = s.split("-")[0]
    try:
        return int(s)
    except ValueError:
        return None


def build_index(ds: str, force_reload: bool = False) -> dict[str, dict[str, list]]:
    """Construit l'index { id_collectivite : { ind_key : [valeur_par_année] } }."""
    info = DATASETS[ds]
    annees = info["annees"]
    id_field = info["id_field"]
    n_annees = len(annees)
    idx: dict[str, dict[str, list]] = {}

    with open(INVENTORY_PATH, encoding="utf-8") as f:
        inv = json.load(f)
    if ds not in inv:
        return idx
    for v in inv[ds]["vars"]:
        cat = v["categorie"]
        var = v["variable"]
        ind_key = _indicator_key(ds, cat, var)
        out_path = DOTATIONS_DIR / f"{_file_slug(ds, cat, var)}.json"
        if not out_path.exists():
            continue
        try:
            records = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for r in records:
            ent_id_raw = r.get(id_field)
            if not ent_id_raw:
                continue
            ent_id = _normalize_insee(ent_id_raw) if id_field == "code_insee" else str(ent_id_raw).strip()
            annee = _parse_exer(r.get("exercice"))
            if annee is None or annee not in annees:
                continue
            valeur = r.get("valeur")
            if valeur is None:
                continue
            year_idx = annees.index(annee)
            entry = idx.setdefault(ent_id, {})
            serie = entry.setdefault(ind_key, [None] * n_annees)
            if serie[year_idx] is None:
                # OFGL peut publier plusieurs lignes pour le même triplet
                # (id, var, exercice) sur des sous-strates différentes ; on
                # prend la 1re valeur non-null
                serie[year_idx] = float(valeur)
    return idx


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def _all_indicators_for_ds(ds: str) -> list[str]:
    """Liste tous les indicateurs d'un dataset (en ordre stable)."""
    with open(INVENTORY_PATH, encoding="utf-8") as f:
        inv = json.load(f)
    if ds not in inv:
        return []
    return [_indicator_key(ds, v["categorie"], v["variable"]) for v in inv[ds]["vars"]]


def merge_communes(idx: dict[str, dict[str, list]]) -> None:
    """Fusionne dotations-communes dans synthese, by-dep et decoratif-values."""
    annees = DATASETS["dotations-communes"]["annees"]
    n_annees = len(annees)
    null_serie = [None] * n_annees
    all_inds = _all_indicators_for_ds("dotations-communes")

    # 1. synthese-communes
    synth = DATA / "communes" / "synthese-communes-2024.json"
    d = json.loads(synth.read_text(encoding="utf-8"))
    for k in all_inds:
        if k not in d["indicators"]:
            d["indicators"].append(k)
    matched = 0
    for c in d.get("communes", []):
        insee = _normalize_insee(c.get("insee"))
        if not insee or insee not in idx:
            continue
        matched += 1
        values = c.setdefault("values", {})
        for k, serie in idx[insee].items():
            values[k] = serie
    synth.write_text(
        json.dumps(d, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  [merge]  synthese-communes : {matched} communes enrichies")

    # 2. by-dep
    by_dep_dir = DATA / "communes" / "by-dep"
    matched = 0
    total = 0
    for path in sorted(by_dep_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        dep_d = json.loads(path.read_text(encoding="utf-8"))
        for k in all_inds:
            if k not in dep_d["indicators"]:
                dep_d["indicators"].append(k)
        for c in dep_d.get("communes", []):
            data = c.get("data") or {}
            insee = _normalize_insee(data.get("insee"))
            total += 1
            if not insee or insee not in idx:
                continue
            matched += 1
            values = data.setdefault("values", {})
            for k, serie in idx[insee].items():
                values[k] = serie
        path.write_text(
            json.dumps(dep_d, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    print(f"  [merge]  by-dep : {matched}/{total} communes enrichies")

    # 3. decoratif-values (lazy)
    paths_path = DATA / "communes" / "decoratif-paths-2024.json"
    meta_path = DATA / "communes" / "meta-communes-2024.json"
    if not paths_path.exists() or not meta_path.exists():
        print("  [warn]   paths/meta manquants, skip decoratif")
        return
    paths_data = json.loads(paths_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    n_paths = len(paths_data.get("paths", []))
    n_meta = len(meta.get("communes", []))
    if n_paths != n_meta:
        print(f"  [warn]   désalignement paths/meta ({n_paths} vs {n_meta})")
        return
    values_dir = DATA / "communes" / "decoratif-values"
    values_dir.mkdir(parents=True, exist_ok=True)
    index_path = values_dir / "_index.json"
    index = {}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    new_inds = [k for k in all_inds if k not in index]
    total_size = 0
    for k in new_inds:
        values = []
        for i in range(n_meta):
            insee = _normalize_insee(meta["communes"][i][1])
            entry = idx.get(insee, {}) if insee else {}
            values.append(entry.get(k, null_serie))
        slug = _slug(k, max_len=100)
        index[k] = slug
        out_file = values_dir / f"{slug}.json"
        payload = {"indicator": k, "years": annees, "values": values}
        out_file.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        total_size += out_file.stat().st_size
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    mb = total_size / 1024 / 1024
    print(f"  [merge]  decoratif-values : {len(new_inds)} indicateurs ajoutés ({mb:.1f} Mo total) ; index : {len(index)} indicateurs")


def merge_generic(ds: str, idx: dict[str, dict[str, list]], synth_path: Path, id_key_in_synth: str) -> None:
    """Fusionne un index dans la synthèse correspondante.

    `id_key_in_synth` : clé d'identification dans chaque entité de la synthèse
    (ex: "siren" pour EPCI, "code" pour dpt/région).
    """
    annees = DATASETS[ds]["annees"]
    all_inds = _all_indicators_for_ds(ds)
    if not synth_path.exists():
        print(f"  [warn] {synth_path.name} introuvable, skip")
        return
    d = json.loads(synth_path.read_text(encoding="utf-8"))
    entities_key = "entities" if "entities" in d else "communes"
    for k in all_inds:
        if k not in d["indicators"]:
            d["indicators"].append(k)
    matched = 0
    for ent in d.get(entities_key, []):
        ent_id = ent.get(id_key_in_synth)
        if not ent_id:
            continue
        ent_id_str = str(ent_id).strip()
        if ent_id_str not in idx:
            continue
        matched += 1
        values = ent.setdefault("values", {})
        for k, serie in idx[ent_id_str].items():
            values[k] = serie
    synth_path.write_text(
        json.dumps(d, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  [merge]  {synth_path.name} : {matched} entités enrichies")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Re-télécharge les fichiers déjà en cache.")
    parser.add_argument("--skip-download", action="store_true",
                        help="Saute le téléchargement, fusionne juste le cache.")
    parser.add_argument("--only", choices=list(DATASETS.keys()), nargs="*",
                        help="Limite à certains datasets.")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("Dotations OFGL (2018-2025)")
    print("=" * 60)

    if not INVENTORY_PATH.exists():
        print(f"ERR : {INVENTORY_PATH} manquant. Lancer d'abord la construction de l'inventaire.")
        return

    if not args.skip_download:
        download_all(force=args.force)
        print()

    datasets_to_process = args.only or list(DATASETS.keys())

    if "dotations-communes" in datasets_to_process:
        print("Communes : construction de l'index...")
        idx = build_index("dotations-communes")
        print(f"  [index]  {len(idx)} communes ont au moins une dotation")
        print("Fusion communes...")
        merge_communes(idx)
        print()

    if "dotations-gfp" in datasets_to_process:
        print("EPCIs : construction de l'index...")
        idx = build_index("dotations-gfp")
        print(f"  [index]  {len(idx)} EPCIs ont au moins une dotation")
        print("Fusion EPCIs...")
        merge_generic(
            "dotations-gfp", idx,
            DATA / "intercommunalites" / "synthese-intercommunalites-2024.json",
            id_key_in_synth="siren",
        )
        print()

    if "dotations-departements" in datasets_to_process:
        print("Départements : construction de l'index...")
        idx = build_index("dotations-departements")
        print(f"  [index]  {len(idx)} départements ont au moins une dotation")
        print("Fusion départements...")
        merge_generic(
            "dotations-departements", idx,
            DATA / "departements" / "synthese-departements-2024.json",
            id_key_in_synth="code",
        )
        print()

    if "dotations-regions" in datasets_to_process:
        print("Régions : construction de l'index...")
        idx = build_index("dotations-regions")
        print(f"  [index]  {len(idx)} régions ont au moins une dotation")
        print("Fusion régions...")
        merge_generic(
            "dotations-regions", idx,
            DATA / "regions" / "synthese-regions-2024.json",
            id_key_in_synth="code",
        )
        print()

    print(f"Terminé en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
