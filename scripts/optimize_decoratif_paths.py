"""Optimisation des contours SVG du calque décoratif communes.

Réduit le poids de ``data/communes/decoratif-paths-2024.json`` (les contours
des ~35 000 communes, chargés une fois au démarrage du niveau communes) par :

  1. **Simplification géométrique** (Douglas-Peucker par sous-chemin) : retire
     les sommets dont la suppression déplace le contour de moins de TOLERANCE
     unités SVG.
  2. **Réduction de précision** : coordonnées arrondies à DECIMALS décimale(s),
     puis points consécutifs identiques dédupliqués.

C'est une transformation de **présentation**, pas une altération de donnée :
le calque décoratif n'est affiché qu'à l'échelle « France entière » (viewBox
~800×623 unités), où une commune moyenne ne fait que quelques pixels. Le
drill-down départemental charge ``by-dep/`` en pleine résolution et n'est pas
concerné. Les valeurs financières (``decoratif-values/``) sont intactes.

L'indexation du décoratif est **positionnelle** (alignée avec meta-communes et
decoratif-values) : le script préserve donc l'ordre ET le nombre exact de paths.

Idempotent et réajustable : au premier passage, l'original pleine précision est
sauvegardé en ``decoratif-paths-2024.full.json`` ; les passages suivants
re-simplifient toujours depuis ce backup, donc on peut changer TOLERANCE/DECIMALS
et relancer sans perte cumulative. Le ``.full.json`` est un artefact de build
local (source réajustable) — inutile de le déployer.

Usage :
    python scripts/optimize_decoratif_paths.py
    python scripts/optimize_decoratif_paths.py --tolerance 0.3 --decimals 1
"""

import argparse
import io
import json
import shutil
import sys
import time
from pathlib import Path

# NB : l'encodage stdout UTF-8 est forcé dans main() (pas au niveau module)
# pour que `simplify_svg_path` reste importable sans effet de bord — fetch_all.py
# l'importe et gère son propre stdout.

DATA = Path(__file__).resolve().parent.parent / "data"
PATHS_FILE = DATA / "communes" / "decoratif-paths-2024.json"
FULL_FILE = DATA / "communes" / "decoratif-paths-2024.full.json"

# Tolérance de simplification, en unités SVG (viewBox ~800×623 pour la France).
# 0.25 ≈ 0.3 px à l'échelle France entière → déplacement invisible.
TOLERANCE = 0.25
# Décimales conservées sur les coordonnées (2 = précision OFGL d'origine).
DECIMALS = 1
# Un sous-chemin (polygone) doit garder au moins ce nombre de sommets distincts
# pour ne pas dégénérer en segment.
MIN_VERTICES = 4


def _douglas_peucker(pts, tol):
    """Simplification Douglas-Peucker d'une polyligne ``pts`` = [(x, y), ...].

    Itératif (pile explicite) pour ne pas exploser la pile Python sur les gros
    contours. Garde toujours les deux extrémités. La distance mesurée est celle
    au **segment** (extrémités incluses), plus robuste que la distance à la
    droite infinie quand le segment est très court.
    """
    n = len(pts)
    if n < 3:
        return pts[:]
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    tol2 = tol * tol
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        ax, ay = pts[first]
        bx, by = pts[last]
        dx, dy = bx - ax, by - ay
        seg_len2 = dx * dx + dy * dy
        max_d2 = -1.0
        idx = -1
        for i in range(first + 1, last):
            px, py = pts[i]
            if seg_len2 == 0.0:
                ddx, ddy = px - ax, py - ay
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                ddx = px - (ax + t * dx)
                ddy = py - (ay + t * dy)
            d2 = ddx * ddx + ddy * ddy
            if d2 > max_d2:
                max_d2 = d2
                idx = i
        if max_d2 > tol2:
            keep[idx] = True
            stack.append((first, idx))
            stack.append((idx, last))
    return [pts[i] for i in range(n) if keep[i]]


def _fmt(v, decimals):
    """Formate une coordonnée en supprimant les zéros décimaux inutiles.
    Ex. (decimals=1) : 461.5 -> "461.5" ; 460.0 -> "460" ; -0.0 -> "0"."""
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", "-"):
        s = "0"
    return s


def _round_dedup(pts, decimals):
    """Arrondit chaque point et retire les doublons consécutifs."""
    out = []
    last = None
    for x, y in pts:
        rx = round(x, decimals)
        ry = round(y, decimals)
        if (rx, ry) != last:
            out.append((rx, ry))
            last = (rx, ry)
    return out


def simplify_svg_path(d, tolerance=TOLERANCE, decimals=DECIMALS):
    """Simplifie un attribut ``d`` SVG composé de sous-chemins ``M x y ... Z``
    en coordonnées absolues (seules commandes présentes dans le décoratif).

    Retourne une chaîne ``d`` simplifiée, même structure de sous-chemins.
    Robuste : un sous-chemin qui dégénérerait (< MIN_VERTICES sommets) est
    conservé à sa résolution d'origine (juste arrondi)."""
    # Tokenisation : on isole M et Z, le reste est une suite de paires x y.
    toks = d.replace("M", " M ").replace("Z", " Z ").split()
    subpaths = []
    cur = None
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t == "M":
            cur = []
            subpaths.append(cur)
            i += 1
        elif t == "Z":
            cur = None
            i += 1
        else:
            cur.append((float(t), float(toks[i + 1])))
            i += 2

    parts = []
    for sp in subpaths:
        simp = _round_dedup(_douglas_peucker(sp, tolerance), decimals)
        if len(simp) < MIN_VERTICES:
            # Polygone déjà minimal : on garde l'original (juste arrondi/dédup)
            # plutôt que de le réduire à un segment dégénéré.
            simp = _round_dedup(sp, decimals)
        coords = " ".join(f"{_fmt(x, decimals)} {_fmt(y, decimals)}" for x, y in simp)
        parts.append("M" + coords + "Z")
    return "".join(parts)


def _count_points(d):
    """Nombre de paires (x, y) dans un attribut ``d`` (pour les stats)."""
    return sum(1 for t in d.replace("M", " ").replace("Z", " ").split()) // 2


def main():
    # Encodage stdout forcé en UTF-8 (workaround Windows cp1252 sur les → / é).
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    parser.add_argument("--decimals", type=int, default=DECIMALS)
    args = parser.parse_args()

    if not PATHS_FILE.exists():
        print(f"[erreur] introuvable : {PATHS_FILE}")
        sys.exit(1)

    # Source = backup pleine précision si présent, sinon le fichier courant
    # (qu'on sauvegarde alors comme backup réajustable).
    if FULL_FILE.exists():
        src = FULL_FILE
        print(f"[source] backup pleine précision : {FULL_FILE.name}")
    else:
        src = PATHS_FILE
        shutil.copy2(PATHS_FILE, FULL_FILE)
        print(f"[backup] original sauvegardé -> {FULL_FILE.name}")

    t0 = time.time()
    # Taille « avant » lue ici : au premier run src == PATHS_FILE et on
    # l'écrase plus bas, donc la capture doit précéder l'écriture.
    size_before = src.stat().st_size / 1024 / 1024
    data = json.loads(src.read_text(encoding="utf-8"))
    paths = data.get("paths", [])
    n_paths = len(paths)

    pts_before = 0
    pts_after = 0
    new_paths = []
    for d in paths:
        pts_before += _count_points(d)
        nd = simplify_svg_path(d, args.tolerance, args.decimals)
        pts_after += _count_points(nd)
        new_paths.append(nd)

    # Sécurité : l'indexation est positionnelle, le compte DOIT être préservé.
    assert len(new_paths) == n_paths, (
        f"compte de paths modifié ({len(new_paths)} != {n_paths})"
    )

    data["paths"] = new_paths
    PATHS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    size_after = PATHS_FILE.stat().st_size / 1024 / 1024
    print(
        f"[ok] {n_paths} paths | "
        f"points {pts_before:,} -> {pts_after:,} "
        f"(-{100 * (1 - pts_after / max(pts_before, 1)):.0f}%) | "
        f"taille {size_before:.1f} -> {size_after:.1f} Mo "
        f"(-{100 * (1 - size_after / max(size_before, 1e-9)):.0f}%) | "
        f"tol={args.tolerance} dec={args.decimals} | {time.time() - t0:.1f}s"
    )
    print(
        f"[fidélité] déplacement max garanti ≤ {args.tolerance} unité SVG "
        f"(+ {0.5 / 10 ** args.decimals:.2f} d'arrondi) — invisible à l'échelle France."
    )


if __name__ == "__main__":
    main()
