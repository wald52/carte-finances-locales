# -*- coding: utf-8 -*-
"""
Allègement (présentation / transfert) des fichiers SERVIS au navigateur.

Étape post-process du pipeline, à lancer APRÈS les fetch_* et AVANT
`build_gzip_served.py`. Elle ne modifie QUE la copie servie : la doctrine de
fidélité reste portée par les `fetch_*.py` (qui stockent la donnée OFGL/BANATIC
brute). Ici on réduit le poids réseau sans changer ce qui est affiché.

Deux familles de transformations :

  1. GÉOMÉTRIE SVG (présentation pure, déjà sanctionnée — cf. memory
     « contours SVG = simplifiables si fidélité visuelle prouvée ») :
       a. suppression des features `niveau_zoom != "FRA"` de regions-svg :
          le JS ne lit QUE les FRA (1 seule occurrence du filtre dans app.js),
          les formes REG_xx (zoom par région) sont téléchargées mais jamais
          utilisées (les régions n'ont pas de drill-down) → ~317 Ko gzip de
          poids mort.
       b. arrondi des coordonnées des chemins `d` à 1 décimale. viewBox =
          824 unités de large → 1 décimale = 0,1 unité ≈ 0,1 px → sous-pixel,
          invisible. Écart max introduit = 0,05 unité (prouvé).

  2. PRÉCISION DES VALEURS de synthèse (décision utilisateur 2026-06-01) :
     les `euros_par_habitant` OFGL sont publiés avec jusqu'à ~17 décimales
     d'artefact flottant (montant ÷ population). On les arrondit à 2 décimales
     (le centime) dans la copie SERVIE : aucune perte d'affichage (l'app
     formate à 0-2 décimales) pour ~140 Ko gzip économisés sur synthese-regions.
     NB : `fetch_all.py` continue de stocker la pleine précision à la source ;
     seule la copie servie est arrondie ici.

Idempotent (ré-exécutable sans dégradation). Après ce script :
    python scripts/build_gzip_served.py

Usage :
    python scripts/optimize_served_payload.py           # regions (défaut)
    python scripts/optimize_served_payload.py --all      # + departements SVG
"""
import io
import sys
import json
import re
import gzip
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

GEO_PRECISION = 1   # décimales conservées sur les coordonnées SVG
VAL_PRECISION = 2   # décimales conservées sur les €/hab et ratios

NUM_FIELDS = ("data_anchor_x", "data_anchor_y", "x_min", "x_max", "y_min", "y_max")
_FLOAT = re.compile(r"-?\d+\.\d+")


def _round_path(s, prec=GEO_PRECISION):
    def repl(m):
        v = round(float(m.group(0)), prec)
        if v == int(v):
            return str(int(v))
        return ("%.*f" % (prec, v)).rstrip("0").rstrip(".")
    return _FLOAT.sub(repl, s)


def _round_num(v, prec):
    try:
        r = round(float(v), prec)
        return int(r) if r == int(r) else r
    except (TypeError, ValueError):
        return v


def _gz(s):
    return len(gzip.compress(s.encode("utf-8"), 9))


def optimize_svg_file(path, drop_non_fra=True):
    if not path.exists():
        print(f"  [skip] {path.name} absent")
        return
    raw = path.read_text(encoding="utf-8")
    features = json.loads(raw)
    n0 = len(features)
    if drop_non_fra:
        features = [s for s in features if s.get("niveau_zoom") == "FRA"]
    for s in features:
        if isinstance(s.get("d"), str):
            s["d"] = _round_path(s["d"])
        for k in NUM_FIELDS:
            if k in s and s[k] is not None:
                s[k] = _round_num(s[k], GEO_PRECISION)
    out = json.dumps(features, ensure_ascii=False, separators=(",", ":"))
    path.write_text(out, encoding="utf-8")
    b, a = _gz(raw), _gz(out)
    print(f"  {path.name}: {n0}->{len(features)} features | gzip {b/1024:.0f}->{a/1024:.0f} Ko (-{100*(b-a)/b:.0f}%)")


def round_synthese_values(path, prec=VAL_PRECISION):
    """Arrondit les séries de `values` (€/hab, ratios) à `prec` décimales.
    Ne touche ni `population` (entiers), ni `meta`, ni `years`."""
    if not path.exists():
        print(f"  [skip] {path.name} absent")
        return
    raw = path.read_text(encoding="utf-8")
    d = json.loads(raw)
    for ent in d.get("entities", []):
        vals = ent.get("values", {})
        for k, serie in vals.items():
            if isinstance(serie, list):
                vals[k] = [
                    (None if v is None else _round_num(v, prec)) for v in serie
                ]
    out = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    path.write_text(out, encoding="utf-8")
    b, a = _gz(raw), _gz(out)
    print(f"  {path.name}: gzip {b/1024:.0f}->{a/1024:.0f} Ko (-{100*(b-a)/b:.0f}%)")


def main():
    do_all = "--all" in sys.argv
    print("Allègement des fichiers servis (présentation / transfert) :")
    print(" [géométrie SVG]")
    optimize_svg_file(DATA / "regions" / "regions-svg.json")
    if do_all:
        optimize_svg_file(DATA / "departements" / "departements-svg.json")
    print(" [précision valeurs synthèse]")
    round_synthese_values(DATA / "regions" / "synthese-regions-2024.json")
    print("OK. Lancer ensuite : python scripts/build_gzip_served.py")


if __name__ == "__main__":
    main()
