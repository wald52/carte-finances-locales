"""Ordre métier canonique des agrégats financiers (EPL + Syndicats).

Remplace le tri alphabétique (sorted()) par un ordre comptable cohérent avec
les blocs « cœur » d'app.js (Recettes → Dépenses → Solde & épargne → Dette →
Trésorerie). Importé par fetch_epl.py et build_syndicats_leaderboard.py pour
que toute régénération du snippet INDICATORS conserve cet ordre.

Filet de sécurité : tout agrégat ABSENT de la liste est renvoyé en fin, en
ordre alphabétique (aucune disparition silencieuse si OFGL publie un nouvel
agrégat — il atterrit en bas jusqu'à ce qu'on l'insère ici).

⚠ Les noms sont VERBATIM OFGL et doivent matcher les clés telles quelles :
« Concours de l'Etat » (sans accent), « Epargne … » (sans accent),
« Taxe d'enlévement … » (accent aigu). Ne pas « corriger » l'orthographe.
Ordre validé avec l'utilisateur (expert métier) le 2026-05-31.
"""

AGREGATS_ORDER = [
    # ── Recettes ───────────────────────────────────────────────────────────
    "Recettes totales",
    "Recettes totales hors emprunts",
    "Recettes de fonctionnement",
    "Recettes d'investissement",
    "Recettes d'investissement hors emprunts",
    "Impôts et taxes",
    "Impôts locaux",
    "Autres impôts et taxes",
    "Versement transport",
    "Taxe d'enlévement des ordures ménagères",
    "Fiscalité reversée",
    "Concours de l'Etat",
    "Dotation globale de fonctionnement",
    "Autres dotations de fonctionnement",
    "Autres dotations et subventions",
    "DETR",
    "Subventions reçues et participations",
    "FCTVA",
    "Péréquations et compensations fiscales",
    "Ventes de biens et services",
    "Produit des cessions d'immobilisations",
    "Reversements de taxe de séjour",
    "Autres recettes de fonctionnement",
    "Autres recettes d'investissement",
    # ── Dépenses ───────────────────────────────────────────────────────────
    "Dépenses totales",
    "Dépenses totales hors remb",
    "Dépenses de fonctionnement",
    "Dépenses d'investissement",
    "Dépenses d'investissement hors remb",
    "Dépenses d'équipement",
    "Frais de personnel",
    "Achats et charges externes",
    "Dépenses d'intervention",
    "Subventions d'équipement versées",
    "Subventions aux personnes de droit privé",
    "Charges financières",
    "Autres dépenses de fonctionnement",
    "Autres dépenses d'investissement",
    # ── Solde & épargne ────────────────────────────────────────────────────
    "Epargne brute",
    "Epargne nette",
    "Epargne de gestion",
    "Capacité ou besoin de financement",
    # ── Dette ──────────────────────────────────────────────────────────────
    "Encours de dette",
    "Encours de dette - Dettes bancaires et assimilées",
    "Encours de dette - Dépôts et cautionnements reçus",
    "Annuité de la dette",
    "Flux net de dette",
    "Emprunts hors GAD",
    "Remboursements d'emprunts hors GAD",
    "Fonds de soutien aux emprunts à risque",
    # ── Trésorerie ─────────────────────────────────────────────────────────
    "Fonds de roulement",
    "Variation du fonds de roulement",
    "Crédits de trésorerie",
    "Dépôts au Trésor",
]

_INDEX = {a: i for i, a in enumerate(AGREGATS_ORDER)}


def agregat_sort_key(agregat: str):
    """Clé de tri : agrégats connus dans l'ordre métier, inconnus en fin (alpha).

    Retourne un tuple comparable :
      - connu   → (0, position_canonique, "")
      - inconnu → (1, 0, nom)   (fallback : groupés en fin, alphabétiques)
    """
    i = _INDEX.get(agregat)
    if i is None:
        return (1, 0, agregat)
    return (0, i, "")


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    assert len(AGREGATS_ORDER) == len(set(AGREGATS_ORDER)), "doublon dans AGREGATS_ORDER"
    print(f"{len(AGREGATS_ORDER)} agrégats dans l'ordre canonique (0 doublon).")
