# -*- coding: utf-8 -*-
"""
Génère le jeu d'icônes PWA (Progressive Web App) pour « Échelons locaux ».

Design (cohérent avec la charte du site) :
  - Tuile au dégradé bleu de marque  #2c5282 -> #1a365d (--accent / --accent-hover).
  - L'Hexagone (silhouette symbolique de la France) en blanc.
  - 3 barres ascendantes bleues à l'intérieur : même motif que le bouton
    « Analyser » du site -> langage visuel commun « data / finances ».

Sorties (dans assets/icons/) :
  - icon-192.png             192x192   purpose "any"     (tuile arrondie)
  - icon-512.png             512x512   purpose "any"     (tuile arrondie)
  - icon-maskable-512.png    512x512   purpose "maskable" (pleine page, zone de
                                                           sécurité Android 80%)
  - apple-touch-icon.png     180x180   iOS (pleine page, iOS arrondit lui-même)

Le favicon vectoriel (assets/icons/favicon.svg) est écrit à la main, hors de ce
script, pour rester net à toute taille.

Idempotent : ré-exécutable sans risque, réécrit les PNG.
Encodage stdout forcé en UTF-8 (workaround Windows cp1252).
"""

import io
import sys
import math
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from PIL import Image, ImageDraw

# --- Palette (identique au CSS du site) -------------------------------------
ACCENT_TOP = (44, 82, 130)   # #2c5282  (--accent)
ACCENT_BOT = (26, 54, 93)    # #1a365d  (--accent-hover)
WHITE = (255, 255, 255)

SS = 4  # supersampling : on dessine à 4x puis on réduit -> bords lissés

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "assets", "icons")


def _vgradient(size, top, bot):
    """Dégradé vertical top->bot, image RGB carrée de côté `size`."""
    strip = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        strip.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return strip.resize((size, size))


def _rounded_mask(size, radius):
    """Masque L (alpha) : rectangle arrondi plein."""
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def _hexagon(cx, cy, r):
    """Sommets d'un hexagone 'pointe en haut' (vertex nord), rayon r."""
    pts = []
    for k in range(6):
        ang = math.radians(60 * k + 90)      # 90° = sommet en haut
        pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))  # -sin : y vers le haut
    return pts


def draw_icon(size, full_bleed=False, corner_frac=0.22, hex_radius_frac=0.40):
    """Rend une icône `size`x`size` (RGBA)."""
    S = size * SS
    grad = _vgradient(S, ACCENT_TOP, ACCENT_BOT)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    if full_bleed:
        out.paste(grad, (0, 0))
    else:
        # Tuile arrondie autonome (coins transparents) -> rendu "icône d'app".
        mask = _rounded_mask(S, int(S * corner_frac))
        out.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(out)
    cx = cy = S / 2.0
    r = S * hex_radius_frac

    # --- L'Hexagone (blanc) ---
    draw.polygon(_hexagon(cx, cy, r), fill=WHITE)

    # --- 3 barres ascendantes bleues, centrées dans l'hexagone ---
    cluster_w = 0.95 * r
    bar_w = cluster_w / 4.0           # 3 barres + 2 espaces (espace = 0.5*bar_w)
    gap = 0.5 * bar_w
    baseline = cy + 0.42 * r          # base des barres (moitié basse de l'hexagone)
    max_h = 0.95 * r
    heights = [0.42 * max_h, 0.70 * max_h, 0.98 * max_h]
    left = cx - cluster_w / 2.0
    rad = max(1, int(bar_w * 0.18))
    for i, h in enumerate(heights):
        x0 = left + i * (bar_w + gap)
        draw.rounded_rectangle(
            [x0, baseline - h, x0 + bar_w, baseline],
            radius=rad,
            fill=ACCENT_TOP,
        )

    return out.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    specs = [
        ("icon-192.png", 192, dict(full_bleed=False, hex_radius_frac=0.40)),
        ("icon-512.png", 512, dict(full_bleed=False, hex_radius_frac=0.40)),
        # maskable : pleine page + hexagone plus petit (zone de sécurité 80%)
        ("icon-maskable-512.png", 512, dict(full_bleed=True, hex_radius_frac=0.34)),
        # apple-touch : pleine page (iOS applique son propre arrondi)
        ("apple-touch-icon.png", 180, dict(full_bleed=True, hex_radius_frac=0.40)),
    ]

    for name, size, kw in specs:
        img = draw_icon(size, **kw)
        path = os.path.join(OUT_DIR, name)
        img.save(path, "PNG", optimize=True)
        print(f"  ecrit {name:26s} {size}x{size}")

    print("Icones PWA generees dans assets/icons/")


if __name__ == "__main__":
    main()
