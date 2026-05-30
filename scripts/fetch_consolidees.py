"""Télécharge et intègre les comptes consolidés OFGL (budget principal +
budgets annexes : eau, assainissement, ZAC, parkings, déchets, etc.).

Quatre datasets traités :
  - ``ofgl-base-communes-consolidee`` (13,6 M lignes, 55 agrégats)
  - ``ofgl-base-gfp-consolidee`` (512 k lignes, 54 agrégats)
  - ``ofgl-base-departements-consolidee`` (82 k lignes, 68 agrégats)
  - ``ofgl-base-regions-consolidee`` (14 k lignes, 69 agrégats)

Structure OFGL : chaque ligne porte ``agregat`` + ``montant_bp`` (budget
principal) + ``montant_ba`` (budgets annexes) + ``montant_flux`` (flux
croisés à éliminer) + ``montant`` (consolidé total = bp + ba - flux) +
``euros_par_habitant`` (consolidé / population).

Indicateurs produits par agrégat :
  - ``{agregat} (consolidé €/hab)`` — euros_par_habitant (budget principal + annexes)
  - ``{agregat} - budgets annexes (€)`` — montant_ba brut

Mode d'écriture :
  - communes-consolidee → synthese-communes + by-dep + decoratif-values
  - gfp-consolidee → synthese-intercommunalites
  - departements-consolidee → synthese-departements
  - regions-consolidee → synthese-regions
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
INVENTORY_PATH = ROOT / "inventory_consolidees.json"
CACHE_DIR = DATA / "consolidees"

OFGL_EXPORT_JSON = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{ds}/exports/json"

ANNEES_COMMUNES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_EPCI = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_DEP = [2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_REG = [2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]

DATASETS = {
    "ofgl-base-communes-consolidee":     {"id_field": "insee", "annees": ANNEES_COMMUNES, "suffix": "commune"},
    "ofgl-base-gfp-consolidee":          {"id_field": "siren", "annees": ANNEES_EPCI,     "suffix": "EPCI"},
    "ofgl-base-departements-consolidee": {"id_field": "dep_code", "annees": ANNEES_DEP,   "suffix": "département"},
    "ofgl-base-regions-consolidee":      {"id_field": "reg_code", "annees": ANNEES_REG,   "suffix": "région"},
}


def _slug(s: str, max_len: int = 80) -> str:
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


def _indicator_keys(ds: str, agregat: str) -> tuple[str, str]:
    """Retourne ``(consolidé_key, budgets_annexes_key)`` pour cet agrégat."""
    suffix = DATASETS[ds]["suffix"]
    return (
        f"{agregat} (consolidé {suffix} €/hab)",
        f"{agregat} - budgets annexes {suffix} (€)",
    )


def _file_slug(ds: str, agregat: str) -> str:
    return f"{_slug(ds, 35)}__{_slug(agregat, 80)}"


def _normalize_insee(s) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    return s.zfill(5) if s.isdigit() else s


def _parse_exer(ex) -> int | None:
    if ex is None:
        return None
    s = str(ex)
    if "-" in s:
        s = s.split("-")[0]
    try:
        return int(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def _download_one(ds: str, agregat: str, force: bool) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{_file_slug(ds, agregat)}.json"
    if out.exists() and not force:
        return out
    info = DATASETS[ds]
    # Champs minimaux : exer, id, montant_ba, euros_par_habitant
    select = f"exer,{info['id_field']},montant_ba,euros_par_habitant"
    params = {
        "where": f'agregat = "{agregat}"',
        "select": select,
    }
    url = f"{OFGL_EXPORT_JSON.format(ds=ds)}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=600) as resp:
            data = resp.read()
    except Exception as e:
        print(f"    ERR {agregat[:60]}: {e}")
        return out
    out.write_bytes(data)
    return out


def download_all(force: bool = False) -> None:
    with open(INVENTORY_PATH, encoding="utf-8") as f:
        inv = json.load(f)
    t0 = time.time()
    for ds, agregats in inv.items():
        if ds not in DATASETS:
            continue
        n_done = 0
        to_dl = []
        for ag in agregats:
            out_path = CACHE_DIR / f"{_file_slug(ds, ag)}.json"
            if out_path.exists() and not force:
                n_done += 1
            else:
                to_dl.append(ag)
        print(f"  [consolidees] {ds}: cache {n_done}, à télécharger {len(to_dl)}")
        for i, ag in enumerate(to_dl, 1):
            t1 = time.time()
            _download_one(ds, ag, force)
            out_path = CACHE_DIR / f"{_file_slug(ds, ag)}.json"
            sz = out_path.stat().st_size if out_path.exists() else 0
            print(f"  [consolidees] [{i}/{len(to_dl)}] {ds[:30]:30} | {ag[:50]:50} "
                  f"-> {sz/1024:.0f} Ko en {time.time()-t1:.1f}s")
    print(f"  [consolidees] terminé en {time.time()-t0:.1f}s")


# ---------------------------------------------------------------------------
# Construction de l'index : { id : { indicator_key : [valeurs par année] } }
# ---------------------------------------------------------------------------

def build_index(ds: str) -> dict[str, dict[str, list]]:
    info = DATASETS[ds]
    annees = info["annees"]
    id_field = info["id_field"]
    n = len(annees)
    idx: dict[str, dict[str, list]] = {}
    with open(INVENTORY_PATH, encoding="utf-8") as f:
        inv = json.load(f)
    for ag in inv.get(ds, []):
        cons_key, ba_key = _indicator_keys(ds, ag)
        out = CACHE_DIR / f"{_file_slug(ds, ag)}.json"
        if not out.exists():
            continue
        try:
            records = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for r in records:
            ent_raw = r.get(id_field)
            if not ent_raw:
                continue
            ent_id = _normalize_insee(ent_raw) if id_field == "insee" else str(ent_raw).strip()
            annee = _parse_exer(r.get("exer"))
            if annee is None or annee not in annees:
                continue
            year_idx = annees.index(annee)
            entry = idx.setdefault(ent_id, {})
            # Consolidé €/hab
            eur = r.get("euros_par_habitant")
            if eur is not None:
                serie = entry.setdefault(cons_key, [None] * n)
                if serie[year_idx] is None:
                    serie[year_idx] = float(eur)
            # Budgets annexes (€)
            ba = r.get("montant_ba")
            if ba is not None:
                serie = entry.setdefault(ba_key, [None] * n)
                if serie[year_idx] is None:
                    serie[year_idx] = float(ba)
    return idx


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def _all_indicators_for_ds(ds: str) -> list[str]:
    with open(INVENTORY_PATH, encoding="utf-8") as f:
        inv = json.load(f)
    keys = []
    for ag in inv.get(ds, []):
        cons_key, ba_key = _indicator_keys(ds, ag)
        keys.extend([cons_key, ba_key])
    return keys


def merge_communes(idx: dict[str, dict[str, list]]) -> None:
    ds = "ofgl-base-communes-consolidee"
    annees = DATASETS[ds]["annees"]
    n_annees = len(annees)
    null_serie = [None] * n_annees
    all_inds = _all_indicators_for_ds(ds)

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
        return
    paths_data = json.loads(paths_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    n_meta = len(meta.get("communes", []))
    if len(paths_data.get("paths", [])) != n_meta:
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
    print(f"  [merge]  decoratif-values : {len(new_inds)} indicateurs ajoutés "
          f"({total_size/1024/1024:.1f} Mo) ; index : {len(index)} indicateurs")


def merge_generic(ds: str, idx: dict[str, dict[str, list]], synth_path: Path, id_key_in_synth: str) -> None:
    all_inds = _all_indicators_for_ds(ds)
    if not synth_path.exists():
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
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--only", choices=list(DATASETS.keys()), nargs="*")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("Comptes consolidés OFGL (2017-2024)")
    print("=" * 60)

    if not INVENTORY_PATH.exists():
        print(f"ERR : {INVENTORY_PATH} manquant.")
        return

    if not args.skip_download:
        download_all(force=args.force)
        print()

    to_process = args.only or list(DATASETS.keys())

    if "ofgl-base-communes-consolidee" in to_process:
        print("Communes consolidées :")
        idx = build_index("ofgl-base-communes-consolidee")
        print(f"  [index] {len(idx)} communes")
        merge_communes(idx)
        print()

    if "ofgl-base-gfp-consolidee" in to_process:
        print("EPCIs consolidés :")
        idx = build_index("ofgl-base-gfp-consolidee")
        print(f"  [index] {len(idx)} EPCIs")
        merge_generic(
            "ofgl-base-gfp-consolidee", idx,
            DATA / "intercommunalites" / "synthese-intercommunalites-2024.json",
            id_key_in_synth="siren",
        )
        print()

    if "ofgl-base-departements-consolidee" in to_process:
        print("Départements consolidés :")
        idx = build_index("ofgl-base-departements-consolidee")
        print(f"  [index] {len(idx)} dpts")
        merge_generic(
            "ofgl-base-departements-consolidee", idx,
            DATA / "departements" / "synthese-departements-2024.json",
            id_key_in_synth="code",
        )
        print()

    if "ofgl-base-regions-consolidee" in to_process:
        print("Régions consolidées :")
        idx = build_index("ofgl-base-regions-consolidee")
        print(f"  [index] {len(idx)} régions")
        merge_generic(
            "ofgl-base-regions-consolidee", idx,
            DATA / "regions" / "synthese-regions-2024.json",
            id_key_in_synth="code",
        )
        print()

    print(f"Terminé en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
