"""Insère le bloc d'indicateurs « Critères — » (interne-criteres-*) dans app.js.

- Injecte le snippet `data/_tmp_indicators_criteres.txt` juste avant la
  fermeture `];` du tableau INDICATORS.
- Ajoute le groupe « Contexte & critères (EPCI) » dans INDICATOR_GROUP_ORDER,
  après « Ensemble intercommunal — territoire consolidé ».

Idempotent : ne réinjecte pas si le bloc est déjà présent. Mirror de
insert_ei_indicators.py / insert_epl_indicators.py.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "assets" / "js" / "app.js"
SNIPPET = ROOT / "data" / "_tmp_indicators_criteres.txt"

HEADER_MARKER = "// CONTEXTE & CRITÈRES (EPCI) — interne-criteres-*"
GROUP_LINE = '  "Contexte & critères (EPCI)",'

# Ancre de fin du tableau INDICATORS : dernier indicateur extra-financier
# suivi de la fermeture du tableau. Unique dans le fichier.
ARRAY_END_ANCHOR = 'erreurs." },\n];'

# Ancre groupe : la ligne EI suivie de la ligne Taux d'imposition (unique).
GROUP_ANCHOR = (
    '  "Ensemble intercommunal — territoire consolidé",\n'
    '  "Taux d\'imposition",'
)


def main() -> None:
    src = APP.read_text(encoding="utf-8")
    snippet = SNIPPET.read_text(encoding="utf-8")

    changed = False

    # 1) Bloc INDICATORS
    if HEADER_MARKER in src:
        print("• snippet INDICATORS déjà présent — non réinjecté")
    else:
        if src.count(ARRAY_END_ANCHOR) != 1:
            sys.exit(f"✗ ancre fin de tableau introuvable/ambiguë "
                     f"({src.count(ARRAY_END_ANCHOR)} occurrences)")
        block = snippet if snippet.endswith("\n") else snippet + "\n"
        src = src.replace(ARRAY_END_ANCHOR, 'erreurs." },\n' + block + "];")
        print("• snippet INDICATORS inséré")
        changed = True

    # 2) INDICATOR_GROUP_ORDER
    if GROUP_LINE in src:
        print("• groupe déjà présent dans INDICATOR_GROUP_ORDER")
    else:
        if src.count(GROUP_ANCHOR) != 1:
            sys.exit(f"✗ ancre groupe introuvable/ambiguë "
                     f"({src.count(GROUP_ANCHOR)} occurrences)")
        repl = (
            '  "Ensemble intercommunal — territoire consolidé",\n'
            '  // Contexte & critères des EPCI (interne-criteres-*) — hors comptes OFGL.\n'
            + GROUP_LINE + "\n"
            "  \"Taux d'imposition\","
        )
        src = src.replace(GROUP_ANCHOR, repl)
        print("• groupe ajouté à INDICATOR_GROUP_ORDER")
        changed = True

    if changed:
        APP.write_text(src, encoding="utf-8")
        print(f"✓ {APP.relative_to(ROOT)} mis à jour")
    else:
        print("rien à faire")


if __name__ == "__main__":
    main()
