"""Insère le snippet d'indicateurs EI (généré par fetch_ei.py) dans app.js.

Étapes :
  1. Lit data/_tmp_indicators_ei.txt (53 définitions JS) + data/_tmp_groups_ei.txt
  2. Idempotent : retire d'abord tout bloc EI déjà inséré + la ligne de groupe
  3. Insère le snippet juste après le dernier indicateur FPIC EPCI
     (ancre « FPIC — Effort fiscal agrégé (index) »)
  4. Insère le groupe dans INDICATOR_GROUP_ORDER juste après « Ratios »
  5. node -c en fin (vérif syntaxe JS)

Ancres choisies : le bloc FPIC EPCI et le groupe « Ratios » sont tous deux
des points stables et thématiquement proches (financier EPCI-level).
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "assets" / "js" / "app.js"
SNIPPET = ROOT / "data" / "_tmp_indicators_ei.txt"
GROUPS = ROOT / "data" / "_tmp_groups_ei.txt"

ANCHOR_INDICATORS = '"FPIC — Effort fiscal agrégé (index)"'
ANCHOR_GROUP_LINE = '"Ratios",'  # comparé sur la ligne *strippée*
GROUP_NAME = "Ensemble intercommunal — territoire consolidé"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def remove_existing(text: str) -> str:
    """Supprime tout bloc EI précédemment inséré (idempotence)."""
    # Bloc d'indicateurs : banner ouvrant (4 lignes) + items, stop au prochain banner.
    block_pattern = re.compile(
        r"\n?  // ===+\n"
        r"  // EI - Comptes consolidés des ensembles intercommunaux.*?\n"
        r"  // .*?\n"
        r"  // ===+\n"
        r"(?:(?!  // ==).*\n)*",
        re.MULTILINE,
    )
    text = block_pattern.sub("\n", text)

    # Ligne de groupe dans INDICATOR_GROUP_ORDER (indent 2 espaces exactement,
    # ≠ des lignes `    group: "<nom>",` à 4 espaces des items d'indicateurs).
    group_pattern = re.compile(
        r'^  "' + re.escape(GROUP_NAME) + r'",\n',
        re.MULTILINE,
    )
    text = group_pattern.sub("", text)
    return text


def insert_indicators(text: str, snippet: str) -> str:
    lines = text.split("\n")
    target_idx = -1
    for i, line in enumerate(lines):
        if ANCHOR_INDICATORS in line:
            for j in range(i, min(i + 5, len(lines))):
                if lines[j].rstrip().endswith("},"):
                    target_idx = j
                    break
            break
    if target_idx < 0:
        raise RuntimeError(f"Ancre indicateurs introuvable : {ANCHOR_INDICATORS}")
    snippet_lines = snippet.rstrip("\n").split("\n")
    new_lines = lines[: target_idx + 1] + [""] + snippet_lines + lines[target_idx + 1 :]
    return "\n".join(new_lines)


def insert_group(text: str, group_block: str) -> str:
    lines = text.split("\n")
    target_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == ANCHOR_GROUP_LINE:
            target_idx = i
            break
    if target_idx < 0:
        raise RuntimeError(f"Ancre groupe introuvable : {ANCHOR_GROUP_LINE}")
    insert_block = group_block.rstrip("\n").split("\n")
    new_lines = lines[: target_idx + 1] + insert_block + lines[target_idx + 1 :]
    return "\n".join(new_lines)


def main() -> None:
    if not SNIPPET.exists() or not GROUPS.exists():
        print("ERR : snippets introuvables. Lancer fetch_ei.py d'abord.")
        sys.exit(1)

    snippet = _read(SNIPPET)
    group_block = _read(GROUPS)
    text = _read(APP_JS)
    n_before = len(text)

    text = remove_existing(text)
    text = insert_indicators(text, snippet)
    text = insert_group(text, group_block)

    _write(APP_JS, text)
    n_after = len(text)
    print(f"app.js : {n_before:,} -> {n_after:,} chars (+{n_after-n_before:,})")
    print(f"  indicateurs insérés : {snippet.count(chr(10))+1} lignes")
    print(f"  groupe : {GROUP_NAME}")

    print("Vérification syntaxe JS (node -c)...")
    try:
        r = subprocess.run(
            ["node", "-c", str(APP_JS)],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        if r.returncode != 0:
            print("  ❌ ERREUR SYNTAXE :")
            print(r.stderr[:2000])
            sys.exit(1)
        print("  ✅ OK")
    except FileNotFoundError:
        print("  (node non disponible, syntax check skip)")


if __name__ == "__main__":
    main()
