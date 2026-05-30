"""Télécharge et intègre les comptes des MDPH (Maisons Départementales
des Personnes Handicapées) au niveau département.

Source : ``ofgl-base-syndicats`` filtré sur ``categorie_synd =
"Maison départementale des personnes handicapées (MDPH)"`` (~34 k lignes).

Les MDPH sont des établissements publics par département (1 par dpt en
théorie), créés par la loi du 11 février 2005. Le dataset OFGL fournit
``dep_current_code`` directement, donc pas besoin d'API externe pour
rattacher à un département.

Indicateurs produits : ``"MDPH - {agregat} (€)"`` au niveau département.
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
CACHE_DIR = DATA / "syndicats" / "mdph"

OFGL_DS = "ofgl-base-syndicats"
OFGL_EXPORT_JSON = f"https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{OFGL_DS}/exports/json"
OFGL_RECORDS = f"https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{OFGL_DS}/records"
MDPH_CATEGORIE = "Maison départementale des personnes handicapées (MDPH)"

# Plage d'années alignée sur synthese-departements
ANNEES_DEP = [2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]


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


def _normalize_dep_code(code) -> str | None:
    """Normalise un code département (Corse, DOM, etc.)."""
    if code is None:
        return None
    s = str(code).strip()
    return s if s else None


def list_agregats() -> list[str]:
    """Liste les agrégats distincts pour les MDPH."""
    cache = DATA / "syndicats" / "mdph_agregats.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    params = {
        "select": "agregat",
        "where": f'categorie_synd = "{MDPH_CATEGORIE}"',
        "group_by": "agregat",
        "order_by": "agregat",
        "limit": "100",
    }
    url = f"{OFGL_RECORDS}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        d = json.loads(r.read())
    ags = [r.get("agregat") for r in d.get("results", []) if r.get("agregat")]
    cache.write_text(json.dumps(ags, ensure_ascii=False), encoding="utf-8")
    print(f"  [mdph] {len(ags)} agrégats distincts")
    return ags


def download_agregats(force: bool = False) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    agregats = list_agregats()
    print(f"  [mdph] téléchargement de {len(agregats)} agrégats…")
    for i, ag in enumerate(agregats, 1):
        out = CACHE_DIR / f"{_slug(ag, 80)}.json"
        if out.exists() and not force:
            continue
        t0 = time.time()
        params = {
            "where": f'categorie_synd = "{MDPH_CATEGORIE}" AND agregat = "{ag}" AND type_de_budget = "Budget principal"',
            "select": "exer,siren,dep_current_code,montant",
        }
        url = f"{OFGL_EXPORT_JSON}?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                out.write_bytes(r.read())
        except Exception as e:
            print(f"    ERR {ag}: {e}")
            continue
        sz = out.stat().st_size
        print(f"  [mdph] [{i}/{len(agregats)}] {ag[:50]:50} -> {sz/1024:.0f} Ko en {time.time()-t0:.1f}s")


def build_index() -> dict[str, dict[str, list]]:
    """Index { dep_code : { ind_key : série_par_année } }.

    Plusieurs SIREN peuvent être rattachés au même département (cas rares
    de MDPH multi-établissements ou fusions). On somme les montants."""
    agregats = list_agregats()
    idx: dict[str, dict[str, list]] = {}
    n = len(ANNEES_DEP)
    null_serie = [None] * n
    for ag in agregats:
        f = CACHE_DIR / f"{_slug(ag, 80)}.json"
        if not f.exists():
            continue
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        ind_key = f"MDPH - {ag} (€)"
        for r in records:
            dep = _normalize_dep_code(r.get("dep_current_code"))
            if not dep:
                continue
            annee = _parse_exer(r.get("exer"))
            if annee is None or annee not in ANNEES_DEP:
                continue
            valeur = r.get("montant")
            if valeur is None:
                continue
            year_idx = ANNEES_DEP.index(annee)
            entry = idx.setdefault(dep, {})
            serie = entry.setdefault(ind_key, list(null_serie))
            if serie[year_idx] is None:
                serie[year_idx] = float(valeur)
            else:
                serie[year_idx] += float(valeur)
    return idx


def _merge_series(a: list, b: list) -> list:
    """Somme deux séries année par année, traitant None comme 0 si l'autre
    est non-None (sinon laisse None)."""
    n = max(len(a), len(b))
    out = []
    for i in range(n):
        va = a[i] if i < len(a) else None
        vb = b[i] if i < len(b) else None
        if va is None and vb is None:
            out.append(None)
        else:
            out.append((va or 0.0) + (vb or 0.0))
    return out


def _resolve_synthese_codes(idx: dict[str, dict[str, list]]) -> dict[str, dict[str, list]]:
    """Réconcilie les dep_codes OFGL (physiques : 67, 68, 69) avec les
    codes de synthese-departements (canoniques : 67A, 691, 69).

    Cas particuliers :
      - **67A (Alsace)** : la Collectivité européenne d'Alsace (depuis 2021)
        absorbe les ex-MDPH 67 et 68. On somme les deux séries par année
        pour reconstituer l'effort total du territoire 2012-2024.
      - **691 (Métropole de Lyon)** : la MDPH du Rhône (code 69) est
        partagée entre Rhône-département et Métropole de Lyon. On duplique
        la série du 69 vers 691 (chacun reçoit la même valeur, qui
        représente la dépense globale de la MDPH commune).

    Retourne une nouvelle map d'index enrichie avec les codes canoniques.
    Ne touche pas aux entrées physiques (67, 68, 69) qui restent disponibles
    pour traçabilité même si la synthese n'expose pas ces codes.
    """
    out = {code: dict(series) for code, series in idx.items()}

    # 67A = 67 + 68 (somme année par année)
    sub67 = idx.get("67") or {}
    sub68 = idx.get("68") or {}
    if sub67 or sub68:
        merged = {}
        all_keys = set(sub67.keys()) | set(sub68.keys())
        for k in all_keys:
            merged[k] = _merge_series(sub67.get(k, []), sub68.get(k, []))
        out["67A"] = merged

    # 691 = 69 (mêmes valeurs, MDPH partagée)
    sub69 = idx.get("69")
    if sub69:
        out["691"] = {k: list(v) for k, v in sub69.items()}

    return out


def merge_departements(idx: dict[str, dict[str, list]]) -> None:
    """Fusionne les valeurs MDPH dans synthese-departements-2024.json."""
    agregats = list_agregats()
    all_inds = [f"MDPH - {ag} (€)" for ag in agregats]

    # Résout les codes canoniques (67A, 691) à partir des physiques.
    idx = _resolve_synthese_codes(idx)

    synth = DATA / "departements" / "synthese-departements-2024.json"
    d = json.loads(synth.read_text(encoding="utf-8"))
    # `indicators` peut contenir des objets (présentation fonctionnelle) en
    # plus de simples strings. On ne dédoublonne que sur les strings.
    existing_strings = {
        (i.get("key") if isinstance(i, dict) else i)
        for i in d["indicators"]
    }
    for k in all_inds:
        if k not in existing_strings:
            d["indicators"].append(k)
    matched = 0
    for ent in d.get("entities", []):
        code = str(ent.get("code") or "").strip()
        if not code or code not in idx:
            continue
        matched += 1
        values = ent.setdefault("values", {})
        for k, serie in idx[code].items():
            values[k] = serie
    synth.write_text(
        json.dumps(d, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  [merge]  MDPH synthese-departements : {matched} départements enrichis")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-télécharge les agrégats.")
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("MDPH — Maisons Départementales des Personnes Handicapées")
    print("=" * 60)

    if not args.skip_download:
        download_agregats(force=args.force)
        print()

    print("Construction de l'index par département…")
    idx = build_index()
    n_with_data = sum(1 for v in idx.values() if v)
    print(f"  [index] {n_with_data} départements avec données MDPH")
    print()

    print("Fusion dans synthese-departements…")
    merge_departements(idx)
    print()

    print(f"Terminé en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
