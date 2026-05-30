"""Télécharge et intègre les comptes des CCAS-CIAS.

Source : ``ofgl-base-ccas-cias`` (4,6 M lignes, 15 686 SIREN distincts).

Architecture distinguant CCAS (communes) et CIAS (EPCIs) :
  - **CCAS / CAS Paris** : établissement communal → niveau commune
    Indicateurs ``"CCAS - {agregat} (€)"`` injectés dans synthese-communes
  - **CIAS** : établissement intercommunal → niveau EPCI
    Indicateurs ``"CIAS - {agregat} (€)"`` injectés dans synthese-intercommunalites

Le dataset OFGL ne fournit pas de `code_insee` ni `siren_epci` pour relier
un CCAS/CIAS à sa collectivité de rattachement. On utilise :
  1. L'API ``recherche-entreprises.api.gouv.fr`` pour mapper chaque SIREN
     au code INSEE de la commune du siège.
  2. Pour les CIAS, on remonte ensuite à l'EPCI parent via
     ``meta-communes-2024.json`` (position 5 = siren_epci).

Subtilités :
  - 1 commune peut avoir plusieurs CCAS (ex: Paris = CAS Paris + 17 CCAS
    d'arrondissement). On somme les montants au niveau commune.
  - 1 CIAS sert plusieurs communes mais on l'attribue à 1 SEUL EPCI
    (son siège). C'est l'EPCI qui porte juridiquement le CIAS.
  - Consolidation PLM : arrondissements 75101-75120 → 75056 (Paris),
    69381-69389 → 69123 (Lyon), 13201-13216 → 13055 (Marseille).
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CCAS_DIR = DATA / "ccas"
CACHE_DIR = CCAS_DIR / "agregats"
MAPPING_PATH = CCAS_DIR / "siren-to-insee.json"

OFGL_DS = "ofgl-base-ccas-cias"
OFGL_EXPORT_JSON = f"https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{OFGL_DS}/exports/json"
OFGL_RECORDS = f"https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{OFGL_DS}/records"
SEARCH_API = "https://recherche-entreprises.api.gouv.fr/search"

ANNEES_COMMUNES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
ANNEES_EPCI = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]


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


def _normalize_insee(s) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    return s.zfill(5) if s.isdigit() else s


def _consolidate_plm(insee: str | None) -> str | None:
    """Mapping arrondissement PLM → commune mère.
    Paris 75101-75120 → 75056, Lyon 69381-69389 → 69123, Marseille 13201-13216 → 13055."""
    if not insee:
        return insee
    if insee.startswith("751") and len(insee) == 5 and insee[3:].isdigit():
        try:
            n = int(insee[3:])
            if 1 <= n <= 20:
                return "75056"
        except ValueError:
            pass
    if insee.startswith("693") and len(insee) == 5 and insee[2:].isdigit():
        try:
            n = int(insee[2:])
            if 381 <= n <= 389:
                return "69123"
        except ValueError:
            pass
    if insee.startswith("132") and len(insee) == 5 and insee[2:].isdigit():
        try:
            n = int(insee[2:])
            if 201 <= n <= 216:
                return "13055"
        except ValueError:
            pass
    return insee


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
# Étape 1 : récupérer la liste des SIREN CCAS
# ---------------------------------------------------------------------------

def list_sirens() -> list[str]:
    cache = CCAS_DIR / "sirens.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    CCAS_DIR.mkdir(parents=True, exist_ok=True)
    sirens = set()
    offset = 0
    while True:
        params = {"select": "siren", "group_by": "siren", "limit": "100", "offset": str(offset)}
        url = f"{OFGL_RECORDS}?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=120) as r:
            d = json.loads(r.read())
        results = d.get("results", [])
        if not results:
            break
        for r in results:
            s = r.get("siren")
            if s:
                sirens.add(str(s).strip())
        if len(results) < 100:
            break
        offset += 100
        time.sleep(0.2)
    sirens_list = sorted(sirens)
    cache.write_text(json.dumps(sirens_list, ensure_ascii=False), encoding="utf-8")
    print(f"  [ccas] {len(sirens_list)} SIREN distincts")
    return sirens_list


# ---------------------------------------------------------------------------
# Étape 2 : mapping SIREN → INSEE
# ---------------------------------------------------------------------------

def _lookup_siren_insee(siren: str) -> str | None:
    params = {"q": siren, "page": "1", "per_page": "1"}
    url = f"{SEARCH_API}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "RATE_LIMIT"
        return None
    except Exception:
        return None
    if not d.get("results"):
        return None
    siege = d["results"][0].get("siege", {})
    return _normalize_insee(siege.get("commune"))


def build_mapping(force: bool = False) -> dict[str, str]:
    CCAS_DIR.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    if MAPPING_PATH.exists():
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    sirens = list_sirens()
    todo = [s for s in sirens if s not in mapping or mapping.get(s) == "RATE_LIMIT"]
    if not todo:
        print(f"  [ccas] mapping déjà complet : {len(mapping)} SIREN")
        return mapping
    print(f"  [ccas] {len(todo)} SIREN à résoudre…")
    t0 = time.time()
    n_ok = n_failed = 0
    for i, siren in enumerate(todo, 1):
        result = _lookup_siren_insee(siren)
        if result == "RATE_LIMIT":
            time.sleep(10)
            result = _lookup_siren_insee(siren)
        if result and result != "RATE_LIMIT":
            mapping[siren] = result
            n_ok += 1
        else:
            mapping[siren] = ""
            n_failed += 1
        if i % 100 == 0:
            MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
            elapsed = time.time() - t0
            rate = i / elapsed
            remaining = (len(todo) - i) / rate
            print(f"  [ccas]  [{i}/{len(todo)}] OK={n_ok} failed={n_failed} ({rate:.1f} req/s, reste ~{remaining/60:.0f} min)")
        time.sleep(0.2)
    MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    return mapping


# ---------------------------------------------------------------------------
# Étape 3 : télécharger les agrégats avec le champ `type`
# ---------------------------------------------------------------------------

def list_agregats() -> list[str]:
    cache = CCAS_DIR / "agregats.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    CCAS_DIR.mkdir(parents=True, exist_ok=True)
    params = {"select": "agregat", "group_by": "agregat", "order_by": "agregat", "limit": "100"}
    url = f"{OFGL_RECORDS}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        d = json.loads(r.read())
    ags = [r.get("agregat") for r in d.get("results", []) if r.get("agregat")]
    cache.write_text(json.dumps(ags, ensure_ascii=False), encoding="utf-8")
    return ags


def download_agregats(force: bool = False) -> None:
    """Télécharge un fichier par agrégat. **Filtre Budget principal** pour
    éviter de mélanger avec les budgets annexes (logements sociaux des
    CCAS, etc. — données différentes)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    agregats = list_agregats()
    print(f"  [ccas] {len(agregats)} agrégats à télécharger")
    for i, ag in enumerate(agregats, 1):
        out = CACHE_DIR / f"{_slug(ag, 80)}.json"
        if out.exists() and not force:
            continue
        t0 = time.time()
        params = {
            "where": f'agregat = "{ag}" AND type_de_budget = "Budget principal"',
            "select": "exer,siren,type,montant",
        }
        url = f"{OFGL_EXPORT_JSON}?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                out.write_bytes(r.read())
        except Exception as e:
            print(f"    ERR {ag}: {e}")
            continue
        sz = out.stat().st_size
        print(f"  [ccas] [{i}/{len(agregats)}] {ag[:60]:60} -> {sz/1024:.0f} Ko en {time.time()-t0:.1f}s")


# ---------------------------------------------------------------------------
# Étape 4 : INSEE → SIREN EPCI parent
# ---------------------------------------------------------------------------

def load_insee_to_epci() -> dict[str, str]:
    """Mapping INSEE → SIREN EPCI parent, depuis meta-communes-2024.json.
    Format meta : ``[nom, insee, dep_code, dep_name, pop, siren_epci]``."""
    meta_path = DATA / "communes" / "meta-communes-2024.json"
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    out = {}
    for entry in meta.get("communes", []):
        insee = _normalize_insee(entry[1] if len(entry) > 1 else None)
        siren_epci = entry[5] if len(entry) > 5 else None
        if insee and siren_epci:
            out[insee] = str(siren_epci).strip()
    return out


# ---------------------------------------------------------------------------
# Étape 5 : construire 2 index séparés (CCAS communes / CIAS EPCIs)
# ---------------------------------------------------------------------------

def build_indexes(mapping: dict[str, str]) -> tuple[dict[str, dict[str, list]], dict[str, dict[str, list]]]:
    """Retourne ``(idx_ccas, idx_cias)`` :
      - ``idx_ccas[insee][ind_key] = série_8_ans`` pour CCAS+CAS Paris (commune-level)
      - ``idx_cias[siren_epci][ind_key] = série_8_ans`` pour CIAS (EPCI-level)
    """
    agregats = list_agregats()
    insee_to_epci = load_insee_to_epci()
    idx_ccas: dict[str, dict[str, list]] = {}
    idx_cias: dict[str, dict[str, list]] = {}
    n_annees = len(ANNEES_COMMUNES)
    null_serie = [None] * n_annees

    cias_no_epci = set()  # SIREN CIAS dont on n'a pas pu trouver l'EPCI parent

    for ag in agregats:
        f = CACHE_DIR / f"{_slug(ag, 80)}.json"
        if not f.exists():
            continue
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        ind_ccas = f"CCAS - {ag} (€)"
        ind_cias = f"CIAS - {ag} (€)"
        for r in records:
            siren = str(r.get("siren") or "").strip()
            tp = (r.get("type") or "").strip()
            insee_raw = mapping.get(siren)
            if not insee_raw:
                continue
            insee = _consolidate_plm(insee_raw)
            annee = _parse_exer(r.get("exer"))
            if annee is None or annee not in ANNEES_COMMUNES:
                continue
            valeur = r.get("montant")
            if valeur is None:
                continue
            year_idx = ANNEES_COMMUNES.index(annee)

            if tp == "CIAS":
                # Remonter à l'EPCI parent via la commune siège
                siren_epci = insee_to_epci.get(insee)
                if not siren_epci:
                    cias_no_epci.add(siren)
                    continue
                entry = idx_cias.setdefault(siren_epci, {})
                serie = entry.setdefault(ind_cias, list(null_serie))
                if serie[year_idx] is None:
                    serie[year_idx] = float(valeur)
                else:
                    serie[year_idx] += float(valeur)
            else:
                # CCAS ou CAS Paris : niveau commune
                entry = idx_ccas.setdefault(insee, {})
                serie = entry.setdefault(ind_ccas, list(null_serie))
                if serie[year_idx] is None:
                    serie[year_idx] = float(valeur)
                else:
                    serie[year_idx] += float(valeur)

    if cias_no_epci:
        print(f"  [ccas]  {len(cias_no_epci)} CIAS sans EPCI parent identifié (ignorés)")
    return idx_ccas, idx_cias


# ---------------------------------------------------------------------------
# Étape 6 : fusion communes (CCAS)
# ---------------------------------------------------------------------------

def merge_ccas(idx: dict[str, dict[str, list]]) -> None:
    agregats = list_agregats()
    all_inds = [f"CCAS - {ag} (€)" for ag in agregats]
    n_annees = len(ANNEES_COMMUNES)
    null_serie = [None] * n_annees

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
    synth.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  [merge]  CCAS synthese-communes : {matched} communes enrichies")

    # 2. by-dep
    by_dep_dir = DATA / "communes" / "by-dep"
    matched = total = 0
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
        path.write_text(json.dumps(dep_d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  [merge]  CCAS by-dep : {matched}/{total} communes enrichies")

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
        payload = {"indicator": k, "years": ANNEES_COMMUNES, "values": values}
        out_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        total_size += out_file.stat().st_size
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [merge]  CCAS decoratif-values : {len(new_inds)} indicateurs ajoutés "
          f"({total_size/1024/1024:.1f} Mo) ; index : {len(index)} indicateurs")


# ---------------------------------------------------------------------------
# Étape 7 : fusion EPCIs (CIAS)
# ---------------------------------------------------------------------------

def merge_cias(idx: dict[str, dict[str, list]]) -> None:
    agregats = list_agregats()
    all_inds = [f"CIAS - {ag} (€)" for ag in agregats]

    synth = DATA / "intercommunalites" / "synthese-intercommunalites-2024.json"
    d = json.loads(synth.read_text(encoding="utf-8"))
    for k in all_inds:
        if k not in d["indicators"]:
            d["indicators"].append(k)
    matched = 0
    for ent in d.get("entities", []):
        siren = str(ent.get("siren") or "").strip()
        if not siren or siren not in idx:
            continue
        matched += 1
        values = ent.setdefault("values", {})
        for k, serie in idx[siren].items():
            values[k] = serie
    synth.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  [merge]  CIAS synthese-intercommunalites : {matched} EPCIs enrichis")


# ---------------------------------------------------------------------------
# Cleanup : supprimer les anciens indicateurs CCAS-CIAS combinés
# ---------------------------------------------------------------------------

def cleanup_old_ccas_cias_combined() -> None:
    """Supprime les anciens indicateurs ``"CCAS-CIAS - ..."`` qui mélangeaient
    CCAS et CIAS au niveau commune (avant la refonte CCAS/CIAS séparés)."""
    import os
    # 1. Decoratif-values : supprimer les fichiers et nettoyer l'index
    values_dir = DATA / "communes" / "decoratif-values"
    index_path = values_dir / "_index.json"
    if index_path.exists():
        idx = json.loads(index_path.read_text(encoding="utf-8"))
        removed = 0
        for k in list(idx.keys()):
            if k.startswith("CCAS-CIAS - "):
                slug = idx.pop(k)
                p = values_dir / f"{slug}.json"
                if p.exists():
                    p.unlink()
                removed += 1
        index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        if removed:
            print(f"  [cleanup] decoratif-values : {removed} indicateurs CCAS-CIAS supprimés")

    # 2. synthese-communes : retirer les indicateurs CCAS-CIAS de la liste
    synth = DATA / "communes" / "synthese-communes-2024.json"
    if synth.exists():
        d = json.loads(synth.read_text(encoding="utf-8"))
        d["indicators"] = [k for k in d["indicators"] if not k.startswith("CCAS-CIAS - ")]
        for c in d.get("communes", []):
            v = c.get("values", {})
            for k in list(v.keys()):
                if k.startswith("CCAS-CIAS - "):
                    del v[k]
        synth.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  [cleanup] synthese-communes : indicateurs CCAS-CIAS retirés")

    # 3. by-dep
    by_dep_dir = DATA / "communes" / "by-dep"
    if by_dep_dir.exists():
        n = 0
        for path in sorted(by_dep_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            dep_d = json.loads(path.read_text(encoding="utf-8"))
            dep_d["indicators"] = [k for k in dep_d["indicators"] if not k.startswith("CCAS-CIAS - ")]
            for c in dep_d.get("communes", []):
                v = (c.get("data") or {}).get("values", {})
                for k in list(v.keys()):
                    if k.startswith("CCAS-CIAS - "):
                        del v[k]
            path.write_text(json.dumps(dep_d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            n += 1
        print(f"  [cleanup] by-dep : {n} fichiers nettoyés")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-mapping", action="store_true")
    parser.add_argument("--skip-mapping", action="store_true")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip téléchargement des agrégats.")
    parser.add_argument("--force-download", action="store_true",
                        help="Re-télécharge les agrégats (utile si format cache changé).")
    parser.add_argument("--skip-cleanup", action="store_true",
                        help="Skip le nettoyage des anciens indicateurs CCAS-CIAS combinés.")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("CCAS-CIAS — refonte CCAS communes / CIAS EPCIs")
    print("=" * 60)

    if not args.skip_mapping:
        mapping = build_mapping(force=args.force_mapping)
    else:
        if MAPPING_PATH.exists():
            mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        else:
            print("ERR : --skip-mapping mais mapping introuvable")
            return
    print()

    if not args.skip_download:
        download_agregats(force=args.force_download)
        print()

    if not args.skip_cleanup:
        print("Cleanup des anciens indicateurs CCAS-CIAS combinés...")
        cleanup_old_ccas_cias_combined()
        print()

    print("Construction des 2 index (CCAS communes + CIAS EPCIs)...")
    idx_ccas, idx_cias = build_indexes(mapping)
    print(f"  [index] CCAS : {sum(1 for v in idx_ccas.values() if v)} communes")
    print(f"  [index] CIAS : {sum(1 for v in idx_cias.values() if v)} EPCIs")
    print()

    print("Fusion CCAS (communes)...")
    merge_ccas(idx_ccas)
    print()

    print("Fusion CIAS (EPCIs)...")
    merge_cias(idx_cias)
    print()

    print(f"Terminé en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
