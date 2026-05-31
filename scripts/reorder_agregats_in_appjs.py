"""Réordonne les entrées d'indicateurs EPL et Syndicats DANS app.js selon
l'ordre métier canonique (scripts/_agregats_order.py), sans relancer les gros
builds (fetch_epl / build_syndicats_leaderboard).

- Ne touche QUE les groupes dont le nom commence par « EPL - » ou
  « Syndicats — » ; tout le reste du fichier est laissé bit-pour-bit identique
  (vérifié : même multiset de lignes).
- Les entrées sont DÉPLACÉES verbatim (on ne régénère pas leur texte : 0 risque
  d'altérer label/help). Les activités EPL et les compétences Syndicats gardent
  leur ordre relatif (alphabétique) ; seuls les agrégats internes sont triés.
- Idempotent : relancer ne change rien.

Usage :
    python scripts/reorder_agregats_in_appjs.py          # dry-run (n'écrit pas)
    python scripts/reorder_agregats_in_appjs.py --write  # applique
"""

import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from _agregats_order import AGREGATS_ORDER, agregat_sort_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "assets" / "js" / "app.js"

KEY_RE = re.compile(r'key:\s*"((?:[^"\\]|\\.)*)"')
GROUP_RE = re.compile(r'group:\s*"((?:[^"\\]|\\.)*)"')
SUFFIX = " (€)"


def agregat_of(group: str, key: str):
    """Déduit le nom d'agrégat depuis (group, key) pour EPL / Syndicats."""
    if group.startswith("EPL - "):
        activite = group[len("EPL - "):]
        prefix = f"EPL {activite} - "
        if key.startswith(prefix) and key.endswith(SUFFIX):
            return key[len(prefix):-len(SUFFIX)]
    elif group.startswith("Syndicats — "):
        comp = group[len("Syndicats — "):]
        prefix = f"Syndicats {comp} — "
        if key.startswith(prefix) and key.endswith(SUFFIX):
            return key[len(prefix):-len(SUFFIX)]
    return None


def is_target(group: str) -> bool:
    return group.startswith("EPL - ") or group.startswith("Syndicats — ")


def main() -> None:
    write = "--write" in sys.argv

    raw = APP.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)

    # 1. Délimiter le tableau INDICATORS
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("const INDICATORS = [")), None)
    assert start is not None, "const INDICATORS = [ introuvable"
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("];")), None)
    assert end is not None, "fin du tableau INDICATORS (];) introuvable"

    head, body, tail = lines[:start + 1], lines[start + 1:end], lines[end:]

    # 2. Tokeniser le corps en lignes brutes + entrées multi-lignes
    tokens = []  # ("raw", line) | ("entry", [lines], group, agregat)
    i, n = 0, len(body)
    while i < n:
        stripped = body[i].lstrip()
        if stripped.startswith("{ key:") or stripped.startswith("{key:"):
            j, entry_lines = i, []
            while j < n:
                entry_lines.append(body[j])
                if body[j].rstrip().endswith("},"):
                    break
                j += 1
            text = "\n".join(entry_lines)
            km, gm = KEY_RE.search(text), GROUP_RE.search(text)
            key = km.group(1) if km else ""
            group = gm.group(1) if gm else ""
            tokens.append(("entry", entry_lines, group, agregat_of(group, key)))
            i = j + 1
        else:
            tokens.append(("raw", body[i]))
            i += 1

    # 3. Réordonner dans chaque sous-run consécutif de même groupe EPL/Synd
    known = set(AGREGATS_ORDER)
    all_target_agg = set()
    unknown_encountered = set()
    none_groups = set()
    out, reordered_groups, k, m = [], 0, 0, len(tokens)

    while k < m:
        if tokens[k][0] != "entry":
            out.append(tokens[k][1])
            k += 1
            continue
        run = []
        while k < m and tokens[k][0] == "entry":
            run.append(tokens[k])
            k += 1
        # partition en sous-runs consécutifs de même groupe
        parts = []
        for t in run:
            if parts and parts[-1][0][2] == t[2]:
                parts[-1].append(t)
            else:
                parts.append([t])
        for part in parts:
            grp = part[0][2]
            if is_target(grp):
                aggs = [t[3] for t in part]
                for a in aggs:
                    if a is None:
                        none_groups.add(grp)
                    else:
                        all_target_agg.add(a)
                        if a not in known:
                            unknown_encountered.add(a)
                if None in aggs:
                    # parsing incertain → on ne touche pas (sécurité)
                    for t in part:
                        out.extend(t[1])
                    continue
                before = list(aggs)
                part_sorted = sorted(part, key=lambda t: agregat_sort_key(t[3]))
                if [t[3] for t in part_sorted] != before:
                    reordered_groups += 1
                for t in part_sorted:
                    out.extend(t[1])
            else:
                for t in part:
                    out.extend(t[1])

    # 4. Garde-fous
    assert sorted(out) == sorted(body), "INVARIANT CASSÉ : multiset de lignes différent !"
    new_lines = head + out + tail

    # 5. Rapports
    print(f"Agrégats EPL/Synd rencontrés : {len(all_target_agg)}")
    missing_in_data = known - all_target_agg
    if missing_in_data:
        print(f"  ⚠ Dans AGREGATS_ORDER mais ABSENTS des données ({len(missing_in_data)}) :")
        for a in sorted(missing_in_data):
            print(f"      - {a!r}")
    if unknown_encountered:
        print(f"  ⚠ Dans les données mais HORS AGREGATS_ORDER → fallback alpha ({len(unknown_encountered)}) :")
        for a in sorted(unknown_encountered):
            print(f"      - {a!r}")
    if none_groups:
        print(f"  ⚠ Groupes EPL/Synd avec agrégat non parsé (non réordonnés) : {sorted(none_groups)}")
    if not missing_in_data and not unknown_encountered and not none_groups:
        print("  ✓ Couverture parfaite : tous les agrégats réels sont dans l'ordre canonique.")

    print(f"Groupes effectivement réordonnés : {reordered_groups}")

    if new_lines == lines:
        print("Résultat : aucun changement (déjà dans l'ordre canonique).")
        return

    if write:
        APP.write_bytes(newline.join(new_lines).encode("utf-8"))
        print(f"Résultat : app.js RÉÉCRIT (newline={newline!r}).")
    else:
        print("Résultat : changements détectés (DRY-RUN, rien écrit). "
              "Relancer avec --write pour appliquer.")


if __name__ == "__main__":
    main()
