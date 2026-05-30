"""Insère le snippet d'indicateurs EPL généré par fetch_epl.py dans app.js.

Étapes :
  1. Lit data/_tmp_indicators_epl.txt (snippet des definitions JS)
  2. Lit data/_tmp_groups_epl.txt (liste des groupes pour INDICATOR_GROUP_ORDER)
  3. Localise dans app.js la fin du bloc CIAS (ligne contenant
     'CIAS - Ventes de biens et services')
  4. Insère le snippet juste après, avant le bloc FPIC
  5. Insère les groupes dans INDICATOR_GROUP_ORDER après "CIAS - Action sociale intercommunale"
  6. Idempotent : retire d'abord toute zone EPL existante

Bonus : exécute `node -c` à la fin pour vérifier la syntaxe.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "assets" / "js" / "app.js"
SNIPPET = ROOT / "data" / "_tmp_indicators_epl.txt"
GROUPS = ROOT / "data" / "_tmp_groups_epl.txt"

ANCHOR_INDICATORS = '"CIAS - Ventes de biens et services (€)"'
ANCHOR_GROUPS = '"CIAS - Action sociale intercommunale",'

EPL_START_MARKER = "  // ===================================================================="
EPL_BANNER_TEXT = "  // EPL - Etablissements publics locaux"
EPL_END_MARKER_RE = re.compile(
    r"  // ===================+\n"
    r"  // EPL - Etablissements publics locaux.*?\n"
    r"(?:.*\n)*?"
    r"(?=  // ===)",
    re.MULTILINE,
)

GROUPS_BANNER = (
    "  // EPL - Etablissements publics locaux "
    "(mapping SIREN → INSEE siège via recherche-entreprises)"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def remove_existing_epl_block(text: str) -> str:
    """Supprime tout bloc EPL precedemment insere (idempotence).

    Strategie : regex multi-ligne avec lookahead negatif. L'ancienne version
    (lazy `(?:    .*?\n)*?`) consommait 0 sous-ligne et laissait des items
    orphelins (SyntaxError JS). La nouvelle consomme toutes les lignes qui
    ne commencent pas par un nouveau separateur de banner `  // ==`.

    Supprime :
      * Bloc d'indicateurs : banner ouvrant (4 lignes ===/EPL.../comm/===)
        + tous les items jusqu'au prochain `  // ==` exclusif.
      * Lignes de groupes dans INDICATOR_GROUP_ORDER : le commentaire
        `  // EPL - ...` + les `  "EPL - ..."` qui suivent.
    """
    # Bloc d'indicateurs : banner ouvrant + items, stop au prochain banner.
    block_pattern = re.compile(
        r"  // ===+\n"
        r"  // EPL - Etablissements publics locaux.*?\n"
        r"  // .*?\n"
        r"  // ===+\n"
        r"(?:(?!  // ==).*\n)*",
        re.MULTILINE,
    )
    text = block_pattern.sub("", text)

    # Lignes de groupes dans INDICATOR_GROUP_ORDER : commentaire + entrees.
    groups_pattern = re.compile(
        r"  // EPL - Etablissements publics locaux.*?\n"
        r'(?:  "EPL - [^"]+",\n)+',
        re.MULTILINE,
    )
    text = groups_pattern.sub("", text)
    return text


def insert_indicators(text: str, snippet: str) -> str:
    """Insère le snippet juste après la ligne contenant ANCHOR_INDICATORS."""
    lines = text.split("\n")
    target_idx = -1
    for i, line in enumerate(lines):
        if ANCHOR_INDICATORS in line:
            # On veut insérer après la } qui ferme cet item.
            # L'item CCAS-CIAS s'étale sur 3 lignes : key,..., group,..., help,... },
            # On cherche la ligne suivante qui termine par '},'
            for j in range(i, min(i + 5, len(lines))):
                if lines[j].rstrip().endswith("},"):
                    target_idx = j
                    break
            break
    if target_idx < 0:
        raise RuntimeError(f"Ancre indicateurs introuvable : {ANCHOR_INDICATORS}")
    # Insérer une ligne vide puis le snippet
    snippet_lines = snippet.rstrip("\n").split("\n")
    new_lines = lines[: target_idx + 1] + [""] + snippet_lines + lines[target_idx + 1 :]
    return "\n".join(new_lines)


def insert_groups(text: str, groups_block: str) -> str:
    """Insère les groupes EPL dans INDICATOR_GROUP_ORDER après l'ancre CIAS."""
    lines = text.split("\n")
    target_idx = -1
    for i, line in enumerate(lines):
        if ANCHOR_GROUPS in line and i > 17900:  # éviter false match avant
            target_idx = i
            break
    if target_idx < 0:
        raise RuntimeError(f"Ancre groupes introuvable : {ANCHOR_GROUPS}")
    insert_block = [GROUPS_BANNER] + groups_block.rstrip("\n").split("\n")
    new_lines = lines[: target_idx + 1] + insert_block + lines[target_idx + 1 :]
    return "\n".join(new_lines)


def main() -> None:
    if not SNIPPET.exists():
        print(f"ERR : {SNIPPET} introuvable. Lancer fetch_epl.py d'abord.")
        return
    if not GROUPS.exists():
        print(f"ERR : {GROUPS} introuvable. Lancer fetch_epl.py d'abord.")
        return

    snippet = _read(SNIPPET)
    groups_block = _read(GROUPS)
    text = _read(APP_JS)
    n_before = len(text)

    text = remove_existing_epl_block(text)
    text = insert_indicators(text, snippet)
    text = insert_groups(text, groups_block)

    _write(APP_JS, text)
    n_after = len(text)
    print(f"app.js : {n_before:,} -> {n_after:,} chars (+{n_after-n_before:,})")
    print(f"  snippet indicateurs : {snippet.count(chr(10))+1} lignes")
    print(f"  groupes : {groups_block.count(chr(10))+1}")

    # Verifier syntax JS
    print("Verification syntax JS (node -c)...")
    try:
        r = subprocess.run(
            ["node", "-c", str(APP_JS)],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        if r.returncode != 0:
            print(f"  ❌ ERREUR SYNTAXE :")
            print(r.stderr[:2000])
            sys.exit(1)
        print("  ✅ OK")
    except FileNotFoundError:
        print("  (node non disponible, syntax check skip)")


if __name__ == "__main__":
    main()
