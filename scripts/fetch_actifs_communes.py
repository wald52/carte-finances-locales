"""Fetch & integrate ``actifs_communes_2024`` (OFGL) au niveau communes.

Patrimoine non financier des communes — actif réévalué selon une
méthodologie nouvelle OFGL (reconstruction depuis 2012 des mouvements
comptables, mise au prix de 2024). 13 indicateurs, snapshot 31/12/2024.

Périmètre : 34 913 communes. **Paris exclu** explicitement par OFGL,
défusions exclues. Sur les autres communes (y compris arrondissements
PLM ? — à vérifier ; en pratique l'INSEE PLM n'apparaît pas dans
l'export, OFGL publie déjà au niveau de la commune mère).

Variables OFGL (13) → libellés exposés :

  - Actif brut                        → "Patrimoine — Actif brut"               (€)
  - Actif brut par habitant           → "Patrimoine — Actif brut/hab"           (€/hab)
  - Actif net                         → "Patrimoine — Actif net"                (€)
  - Actif net par habitant            → "Patrimoine — Actif net/hab"            (€/hab)
  - Dotation aux amortissements       → "Patrimoine — Dotation aux amortissements" (€)
  - Taux d'actif brut                 → "Patrimoine — Taux d'actif brut"        (%)
  - Taux d'actif net                  → "Patrimoine — Taux d'actif net"         (%)
  - Taux de vétusté                   → "Patrimoine — Taux de vétusté"          (%)
  - Dette sur actif brut              → "Patrimoine — Dette sur actif brut"     (%)
  - Epargne sur amortissement         → "Patrimoine — Épargne sur amortissement" (%)
  - Subventions sur amortissement     → "Patrimoine — Subventions sur amortissement" (%)
  - Durée de vie moyenne (DVM)        → "Patrimoine — Durée de vie moyenne"     (années)
  - Délai de renouvellement (DRP)     → "Patrimoine — Délai de renouvellement"  (années)

Temporalité : OFGL ne publie qu'un snapshot 2024. Les valeurs sont
stockées dans un array positionnel [None]*8 aligné sur years=[2017..2024],
seul l'index 7 (2024) est renseigné. Cohérent avec le pattern existant
(FPIC démarre en 2018, MDPH en 2017, etc.).

Fichiers enrichis :
  - data/communes/synthese-communes-2024.json
  - data/communes/by-dep/*.json
  - data/communes/by-epci/*.json
  - data/communes/decoratif-values/{slug}.json (13 nouveaux fichiers)
  - data/communes/decoratif-values/_index.json (13 nouvelles clés)

Cache local : data/_tmp_actifs_communes.json (supprimer pour refresh).
Idempotent : nettoie tous les indicateurs préfixés "Patrimoine — " avant
écriture.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYNTHESE_FILE = DATA / "communes" / "synthese-communes-2024.json"
BYDEP_DIR = DATA / "communes" / "by-dep"
# by-epci contient des données *communales* (paths SVG + indicateurs commune)
# mais est physiquement rangé sous data/intercommunalites/ car servant le
# drill-down EPCI (chaque fichier = communes membres d'un EPCI).
BYEPCI_DIR = DATA / "intercommunalites" / "by-epci"
DECORATIF_DIR = DATA / "communes" / "decoratif-values"
META_FILE = DATA / "communes" / "meta-communes-2024.json"
TMP_EXPORT = DATA / "_tmp_actifs_communes.json"

EXPORT_URL = (
    "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/"
    "actifs_communes_2024/exports/json"
)

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES)
YEAR_2024_IDX = ANNEES.index(2024)

PREFIX = "Patrimoine — "

# Mapping variable OFGL → (label_court, unité affichée)
INDICATOR_SPEC: dict[str, tuple[str, str]] = {
    "Actif brut":                                 ("Actif brut", "€"),
    "Actif brut par habitant":                    ("Actif brut/hab", "€/hab"),
    "Actif net":                                  ("Actif net", "€"),
    "Actif net par habitant":                     ("Actif net/hab", "€/hab"),
    "Dotation aux amortissements":                ("Dotation aux amortissements", "€"),
    "Taux d'actif brut":                          ("Taux d'actif brut", "%"),
    "Taux d'actif net":                           ("Taux d'actif net", "%"),
    "Taux de vétusté":                            ("Taux de vétusté", "%"),
    "Dette sur actif brut":                       ("Dette sur actif brut", "%"),
    "Epargne sur amortissement":                  ("Épargne sur amortissement", "%"),
    "Subventions sur amortissement":              ("Subventions sur amortissement", "%"),
    "Durée de vie moyenne (DVM)":                 ("Durée de vie moyenne", "années"),
    "Délai de renouvellement patrimonial (DRP)":  ("Délai de renouvellement patrimonial", "années"),
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
            out.append("-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    s = s.strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def _normalize_insee(s) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    return s.zfill(5) if s.isdigit() else s


def _download_export() -> list[dict]:
    if TMP_EXPORT.exists():
        sz = TMP_EXPORT.stat().st_size / 1024 / 1024
        print(f"[cache] {TMP_EXPORT.relative_to(ROOT)} ({sz:.1f} Mo)")
        return json.loads(TMP_EXPORT.read_text(encoding="utf-8"))
    params = {"select": "insee,siren,variable,valeur,unite"}
    url = EXPORT_URL + "?" + urllib.parse.urlencode(params)
    print(f"[download] {url[:120]}…")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "echelons-locaux/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read()
    TMP_EXPORT.write_bytes(raw)
    print(f"  {len(raw)/1024/1024:.1f} Mo en {time.time()-t0:.1f}s")
    return json.loads(raw.decode("utf-8"))


def _build_index(records: list[dict]) -> dict[str, dict[str, list]]:
    """insee → {indicator_key: [None]*8 with v at idx 7}."""
    idx: dict[str, dict[str, list]] = defaultdict(dict)
    n_skipped = n_ok = 0
    seen_vars: set[str] = set()
    for r in records:
        var = r.get("variable")
        if var:
            seen_vars.add(var)
        spec = INDICATOR_SPEC.get(var)
        if spec is None:
            n_skipped += 1
            continue
        label, _unit = spec
        key = PREFIX + label
        insee = _normalize_insee(r.get("insee"))
        if not insee:
            n_skipped += 1
            continue
        raw_val = r.get("valeur")
        if raw_val is None:
            n_skipped += 1
            continue
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            n_skipped += 1
            continue
        serie = idx[insee].setdefault(key, [None] * N_ANNEES)
        serie[YEAR_2024_IDX] = val
        n_ok += 1
    print(f"  {n_ok} valeurs intégrées, {n_skipped} skip "
          f"({len(idx)} communes distinctes)")
    unknown = seen_vars - set(INDICATOR_SPEC.keys())
    if unknown:
        print(f"  ⚠ variables OFGL inconnues ignorées : {sorted(unknown)}")
    return idx


def _cleanup_indicators_list(inds) -> list:
    """Retire les entries (string ou dict) dont la clé démarre par PREFIX."""
    out = []
    for i in inds:
        key = i if isinstance(i, str) else i.get("key", "")
        if not key.startswith(PREFIX):
            out.append(i)
    return out


def _cleanup_values_dict(values: dict) -> None:
    for k in list(values.keys()):
        if k.startswith(PREFIX):
            del values[k]


def update_synthese(idx: dict[str, dict[str, list]], new_keys: list[str]) -> None:
    print(f"\n[synthese] {SYNTHESE_FILE.relative_to(ROOT)}")
    synth = json.loads(SYNTHESE_FILE.read_text(encoding="utf-8"))
    synth["indicators"] = _cleanup_indicators_list(synth.get("indicators", [])) + new_keys
    matched = 0
    for c in synth.get("communes", []):
        values = c.setdefault("values", {})
        _cleanup_values_dict(values)
        insee = _normalize_insee(c.get("insee"))
        if not insee or insee not in idx:
            continue
        matched += 1
        for k, serie in idx[insee].items():
            values[k] = serie
    SYNTHESE_FILE.write_text(
        json.dumps(synth, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    sz = SYNTHESE_FILE.stat().st_size / 1024 / 1024
    print(f"  {matched} communes enrichies, {sz:.1f} Mo")


def update_bydep(idx: dict[str, dict[str, list]], new_keys: list[str]) -> None:
    files = sorted(BYDEP_DIR.glob("*.json"))
    files = [f for f in files if f.name != "_index.json"]
    print(f"\n[by-dep] {len(files)} fichiers")
    total_matched = 0
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        d["indicators"] = _cleanup_indicators_list(d.get("indicators", [])) + new_keys
        n_match = 0
        for c in d.get("communes", []):
            data = c.get("data") or {}
            values = data.setdefault("values", {})
            _cleanup_values_dict(values)
            insee = _normalize_insee(data.get("insee"))
            if not insee or insee not in idx:
                continue
            n_match += 1
            for k, serie in idx[insee].items():
                values[k] = serie
        f.write_text(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        total_matched += n_match
    print(f"  {total_matched} communes enrichies dans by-dep/")


def update_byepci(idx: dict[str, dict[str, list]], new_keys: list[str]) -> None:
    files = sorted(BYEPCI_DIR.glob("*.json"))
    files = [f for f in files if f.name != "_index.json"]
    print(f"\n[by-epci] {len(files)} fichiers")
    total_matched = 0
    for i, f in enumerate(files):
        d = json.loads(f.read_text(encoding="utf-8"))
        # by-epci ne stocke pas la liste d'indicators au top (vérifié), mais
        # on l'ajoute si présent par cohérence
        if "indicators" in d:
            d["indicators"] = _cleanup_indicators_list(d["indicators"]) + new_keys
        n_match = 0
        for c in d.get("communes", []):
            data = c.get("data") or {}
            values = data.setdefault("values", {})
            _cleanup_values_dict(values)
            insee = _normalize_insee(data.get("insee"))
            if not insee or insee not in idx:
                continue
            n_match += 1
            for k, serie in idx[insee].items():
                values[k] = serie
        f.write_text(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        total_matched += n_match
        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(files)}] {total_matched} communes traitées")
    print(f"  {total_matched} communes enrichies dans by-epci/")


def update_decoratif(idx: dict[str, dict[str, list]]) -> list[str]:
    """Génère un fichier decoratif-values/{slug}.json par indicateur, en
    respectant l'ordre positionnel de meta-communes.communes."""
    meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    schema = meta["schema"]
    insee_pos = schema.index("insee")
    meta_communes = meta["communes"]
    print(f"\n[decoratif] {len(meta_communes)} positions communes")

    # _index.json existant
    index_file = DECORATIF_DIR / "_index.json"
    index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}

    # Nettoyage : retirer entries préfixées + supprimer fichiers orphelins
    for k in list(index.keys()):
        if k.startswith(PREFIX):
            slug = index[k]
            old = DECORATIF_DIR / f"{slug}.json"
            if old.exists():
                old.unlink()
            del index[k]

    slugs_written: list[str] = []
    for ofgl_var, (label, unit) in INDICATOR_SPEC.items():
        key = PREFIX + label
        slug = _slug(key)
        values_array = []
        for entry in meta_communes:
            insee = _normalize_insee(entry[insee_pos] if len(entry) > insee_pos else None)
            serie = idx.get(insee or "", {}).get(key)
            if serie is None:
                values_array.append([None] * N_ANNEES)
            else:
                values_array.append(serie)
        out = {
            "indicator": key,
            "years": ANNEES,
            "values": values_array,
        }
        (DECORATIF_DIR / f"{slug}.json").write_text(
            json.dumps(out, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        index[key] = slug
        slugs_written.append(slug)
        n_renseignees = sum(1 for v in values_array if v[YEAR_2024_IDX] is not None)
        print(f"  {slug}.json : {n_renseignees}/{len(meta_communes)} communes renseignées")

    index_file.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return slugs_written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Supprime le cache et re-télécharge.")
    args = parser.parse_args()

    if args.force and TMP_EXPORT.exists():
        TMP_EXPORT.unlink()

    t0 = time.time()
    print("=" * 64)
    print("actifs_communes_2024 — Patrimoine non financier des communes")
    print("=" * 64)

    records = _download_export()
    print(f"  {len(records)} records bruts")

    idx = _build_index(records)
    new_keys = sorted({PREFIX + lbl for lbl, _u in INDICATOR_SPEC.values()})

    update_synthese(idx, new_keys)
    update_bydep(idx, new_keys)
    update_byepci(idx, new_keys)
    update_decoratif(idx)

    print(f"\nTerminé en {time.time()-t0:.1f}s")
    print(f"  {len(new_keys)} nouveaux indicateurs : {new_keys}")


if __name__ == "__main__":
    main()
