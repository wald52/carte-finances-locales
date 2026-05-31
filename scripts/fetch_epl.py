"""Telecharge et integre les comptes des EPL (etablissements publics locaux
non syndicaux non CCAS/CIAS).

Source : ``ofgl-base-epl-consolidee`` (379 441 lignes, ~9 200 SIREN dont
~1 450 EPL hors GIP-MDPH). Valeurs **consolidees par OFGL** : budget
principal + budgets annexes - flux croises entre BP et BA. 1 ligne =
1 (siren x agregat x exercice).

Categories couvertes (filtre ``categorie_epl_abr != "GIP - MDPH"``) :
  - EPA (Etablissements publics administratifs)
  - Regies personnalisees - EPIC (industriel & commercial)
  - Regies personnalisees - EPCC (cooperation culturelle)
  - GIP - Autre (groupements d'interet public hors MDPH)

Doctrine : presentation par agregation geographique multi-niveau.
OFGL publie nativement reg_code et dep_code. On enrichit avec l'INSEE de la
commune du siege (via API recherche-entreprises) pour remonter aux niveaux
EPCI et commune. Aucune tutelle inventee : la donnee est presentee partout
ou elle est geographiquement localisee.

Granularite : 1 indicateur = (activite x agregat). Toutes categories
fusionnees (EPA + EPIC + EPCC + GIP-Autre). Valeurs consolidees OFGL
(BP + BA - flux), pas de sommation manuelle par budget.

Format des cles d'indicateurs : ``EPL {activite} - {agregat} (eur)``

Historique : avant 2026-05, utilisait ``ofgl-base-epl`` (non consolidee)
avec sommation manuelle BP + BA sans neutralisation des flux croises.
Le passage a la base consolidee corrige le double-comptage des flux
internes BP<->BA, particulierement significatif pour les EPL Transports
et services industriels et commerciaux. Cache : ``data/epl/agregats-consolidee/``
(l'ancien ``data/epl/agregats/`` peut etre supprime).

Subtilites :
  - Plusieurs EPL pour 1 commune/EPCI/dpt/region : sommation des montants
  - Consolidation PLM : arrondissements Paris/Lyon/Marseille -> commune mere
  - Consolidation Alsace (67A = 67 + 68) et Metropole Lyon (691 dupliquee 69)
  - Champ ``type_de_budget`` absent de la base consolidee (1 ligne = 1 valeur deja consolidee)
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
import io
import sys
from pathlib import Path

# Ordre métier canonique des agrégats, partagé avec build_syndicats_leaderboard.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _agregats_order import agregat_sort_key

# Force UTF-8 stdout for Windows (cp1252 workaround)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EPL_DIR = DATA / "epl"
# Nouveau cache distinct (l'ancien `agregats/` contient les valeurs BP+BA
# brutes de l'ancien pipeline ; supprimable manuellement apres validation).
CACHE_DIR = EPL_DIR / "agregats-consolidee"
SIRENS_PATH = EPL_DIR / "sirens.json"
AGREGATS_PATH = EPL_DIR / "agregats-consolidee.json"
ACTIVITES_PATH = EPL_DIR / "activites-consolidee.json"
MAPPING_PATH = EPL_DIR / "siren-to-insee.json"

OFGL_DS = "ofgl-base-epl-consolidee"
OFGL_EXPORT_JSON = f"https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{OFGL_DS}/exports/json"
OFGL_RECORDS = f"https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/{OFGL_DS}/records"
SEARCH_API = "https://recherche-entreprises.api.gouv.fr/search"

ANNEES = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
N_ANNEES = len(ANNEES)
NULL_SERIE: list = [None] * N_ANNEES

# Pour regions et departements, les syntheses utilisent une plage 2012-2024.
# Les donnees EPL OFGL ne commencent qu'en 2017, donc on prefixe avec 5 nulls
# (2012, 2013, 2014, 2015, 2016) pour aligner sur la grille de 13 annees.
ANNEES_FULL = [2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]
PADDING_PRE_2017: list = [None] * (len(ANNEES_FULL) - N_ANNEES)


def _pad_to_full(serie: list) -> list:
    """Etend une serie [v_2017,...,v_2024] (8) en [None*5, v_2017,...,v_2024] (13)
    pour aligner sur years=[2012,...,2024] des syntheses regions/dpts."""
    return PADDING_PRE_2017 + list(serie)

# Filtre OFGL pour exclure les GIP-MDPH (deja couverts par fetch_syndicats_mdph.py).
# On filtre sur ``categorie_epl_abr`` (la categorie reelle) plutot que sur ``categ``
# (categorie meta-OFGL), parce qu'OFGL a reclasse Ile-de-France Mobilites
# (SIREN 287500078) comme ``categ=SYND`` en 2024 alors que c'est un EPA. Le
# filtre par ``categorie_epl_abr`` est plus fiable.
WHERE_EPL = 'categorie_epl_abr != "GIP - MDPH"'


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


def _normalize_insee(s) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    return s.zfill(5) if s.isdigit() else s


def _consolidate_plm(insee: str | None) -> str | None:
    """Mapping arrondissement PLM -> commune mere.
    Paris 75101-75120 -> 75056, Lyon 69381-69389 -> 69123, Marseille 13201-13216 -> 13055."""
    if not insee:
        return insee
    if insee.startswith("751") and len(insee) == 5 and insee[3:].isdigit():
        try:
            n = int(insee[3:])
            if 1 <= n <= 20:
                return "75056"
        except ValueError:
            pass
    if insee.startswith("693") and len(insee) == 5 and insee[2:].isdigit():
        try:
            n = int(insee[2:])
            if 381 <= n <= 389:
                return "69123"
        except ValueError:
            pass
    if insee.startswith("132") and len(insee) == 5 and insee[2:].isdigit():
        try:
            n = int(insee[2:])
            if 201 <= n <= 216:
                return "13055"
        except ValueError:
            pass
    return insee


def _consolidate_dep_code(dep_code: str | None) -> str | None:
    """Consolidation 67 + 68 -> 67A (Collectivite europeenne d'Alsace).
    Pour la Metropole de Lyon (691) il faut maintenir le dataset OFGL tel quel :
    seuls les EPL dont dep_code='691' (s'il y en a) iront sur 691. La
    duplication 69 -> 691 se fait au niveau merge (cf. merge_departements).
    """
    if dep_code in ("67", "68"):
        return "67A"
    return dep_code


def _normalize_reg_code(reg_code: str | None) -> str | None:
    """Normalise un code region OFGL pour matcher la synthese.

    OFGL publie les codes region DOM sans padding (Guadeloupe='1',
    Martinique='2', Guyane='3', Reunion='4', Mayotte='6'), alors que
    synthese-regions-2024.json utilise un padding sur 2 chiffres
    ('01', '02', '03', '04'). Sans cette normalisation, les 4 DOM
    n'avaient AUCUN indicateur EPL (Mayotte n'a de toute facon pas
    d'entite region dans la synthese).
    """
    if reg_code is None:
        return None
    s = str(reg_code).strip()
    if not s:
        return None
    return s.zfill(2) if s.isdigit() else s


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


def indicator_key(activite: str, agregat: str) -> str:
    """Cle canonique d'un indicateur EPL : 'EPL {activite} - {agregat} (eur)'."""
    return f"EPL {activite} - {agregat} (€)"


# ---------------------------------------------------------------------------
# Etape 1 : recuperer la liste des SIREN, activites et agregats
# ---------------------------------------------------------------------------

def list_sirens() -> list[str]:
    if SIRENS_PATH.exists():
        return json.loads(SIRENS_PATH.read_text(encoding="utf-8"))
    EPL_DIR.mkdir(parents=True, exist_ok=True)
    sirens: set[str] = set()
    offset = 0
    while True:
        params = {
            "select": "siren",
            "group_by": "siren",
            "where": WHERE_EPL,
            "limit": "100",
            "offset": str(offset),
        }
        url = f"{OFGL_RECORDS}?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=120) as r:
            d = json.loads(r.read())
        results = d.get("results", [])
        if not results:
            break
        for rec in results:
            s = rec.get("siren")
            if s:
                sirens.add(str(s).strip())
        if len(results) < 100:
            break
        offset += 100
        time.sleep(0.2)
    sirens_list = sorted(sirens)
    SIRENS_PATH.write_text(json.dumps(sirens_list, ensure_ascii=False), encoding="utf-8")
    print(f"  [epl] {len(sirens_list)} SIREN EPL distincts (hors MDPH)")
    return sirens_list


def list_agregats() -> list[str]:
    if AGREGATS_PATH.exists():
        return json.loads(AGREGATS_PATH.read_text(encoding="utf-8"))
    EPL_DIR.mkdir(parents=True, exist_ok=True)
    params = {
        "select": "agregat",
        "group_by": "agregat",
        "order_by": "agregat",
        "where": WHERE_EPL,
        "limit": "100",
    }
    url = f"{OFGL_RECORDS}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        d = json.loads(r.read())
    ags = [rec.get("agregat") for rec in d.get("results", []) if rec.get("agregat")]
    AGREGATS_PATH.write_text(json.dumps(ags, ensure_ascii=False), encoding="utf-8")
    print(f"  [epl] {len(ags)} agregats")
    return ags


def list_activites() -> list[str]:
    if ACTIVITES_PATH.exists():
        return json.loads(ACTIVITES_PATH.read_text(encoding="utf-8"))
    EPL_DIR.mkdir(parents=True, exist_ok=True)
    params = {
        "select": "libelle_cacti_simplifie",
        "group_by": "libelle_cacti_simplifie",
        "order_by": "libelle_cacti_simplifie",
        "where": WHERE_EPL,
        "limit": "100",
    }
    url = f"{OFGL_RECORDS}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        d = json.loads(r.read())
    acts = [rec.get("libelle_cacti_simplifie") for rec in d.get("results", []) if rec.get("libelle_cacti_simplifie")]
    ACTIVITES_PATH.write_text(json.dumps(acts, ensure_ascii=False), encoding="utf-8")
    print(f"  [epl] {len(acts)} activites")
    return acts


# ---------------------------------------------------------------------------
# Etape 2 : mapping SIREN -> INSEE commune du siege
# ---------------------------------------------------------------------------

def _lookup_siren_insee(siren: str) -> str | None:
    params = {"q": siren, "page": "1", "per_page": "1"}
    url = f"{SEARCH_API}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "RATE_LIMIT"
        return None
    except Exception:
        return None
    if not d.get("results"):
        return None
    siege = d["results"][0].get("siege", {})
    return _normalize_insee(siege.get("commune"))


def build_mapping(force: bool = False) -> dict[str, str]:
    EPL_DIR.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    if MAPPING_PATH.exists() and not force:
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    sirens = list_sirens()
    todo = [s for s in sirens if s not in mapping or mapping.get(s) == "RATE_LIMIT"]
    if not todo:
        print(f"  [epl] mapping deja complet : {len(mapping)} SIREN")
        return mapping
    print(f"  [epl] {len(todo)} SIREN a resoudre...")
    t0 = time.time()
    n_ok = n_failed = 0
    for i, siren in enumerate(todo, 1):
        result = _lookup_siren_insee(siren)
        if result == "RATE_LIMIT":
            time.sleep(10)
            result = _lookup_siren_insee(siren)
        if result and result != "RATE_LIMIT":
            mapping[siren] = result
            n_ok += 1
        else:
            mapping[siren] = ""
            n_failed += 1
        if i % 100 == 0:
            MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
            elapsed = time.time() - t0
            rate = i / elapsed
            remaining = (len(todo) - i) / rate
            print(f"  [epl]  [{i}/{len(todo)}] OK={n_ok} failed={n_failed} ({rate:.1f} req/s, reste ~{remaining/60:.0f} min)")
        time.sleep(0.2)
    MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  [epl] mapping termine : {n_ok} OK, {n_failed} failed")
    return mapping


# ---------------------------------------------------------------------------
# Etape 3 : telechargement des agregats
# ---------------------------------------------------------------------------

def download_agregats(force: bool = False) -> None:
    """Telecharge 1 fichier JSON par agregat. Filtre `categorie_epl_abr != "GIP - MDPH"`.
    Selection minimaliste : exer, siren, libelle_cacti_simplifie, reg_code,
    dep_code, montant. Le `montant` est la valeur consolidee OFGL (BP + BA - flux),
    1 ligne = 1 (siren x agregat x exer)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    agregats = list_agregats()
    print(f"  [epl] telechargement {len(agregats)} agregats")
    for i, ag in enumerate(agregats, 1):
        out = CACHE_DIR / f"{_slug(ag, 80)}.json"
        if out.exists() and not force:
            continue
        t0 = time.time()
        params = {
            "where": f'agregat = "{ag}" AND {WHERE_EPL}',
            "select": "exer,siren,libelle_cacti_simplifie,reg_code,dep_code,montant",
        }
        url = f"{OFGL_EXPORT_JSON}?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                out.write_bytes(r.read())
        except Exception as e:
            print(f"    ERR {ag}: {e}")
            continue
        sz = out.stat().st_size
        print(f"  [epl] [{i}/{len(agregats)}] {ag[:60]:60} -> {sz/1024:.0f} Ko en {time.time()-t0:.1f}s")


# ---------------------------------------------------------------------------
# Etape 4 : meta-communes -> mapping INSEE -> SIREN EPCI
# ---------------------------------------------------------------------------

def load_insee_to_epci() -> dict[str, str]:
    """INSEE -> SIREN EPCI parent, depuis meta-communes-2024.json.
    Format meta : ``[nom, insee, dep_code, dep_name, pop, siren_epci, siren_ept]``."""
    meta_path = DATA / "communes" / "meta-communes-2024.json"
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for entry in meta.get("communes", []):
        insee = _normalize_insee(entry[1] if len(entry) > 1 else None)
        siren_epci = entry[5] if len(entry) > 5 else None
        if insee and siren_epci:
            out[insee] = str(siren_epci).strip()
    return out


# ---------------------------------------------------------------------------
# Etape 5 : construire 4 index multi-niveau
# ---------------------------------------------------------------------------

def build_indexes(mapping: dict[str, str]) -> tuple[
    dict[str, dict[str, list]],  # idx_regions[reg_code][indicator] = serie 8 ans
    dict[str, dict[str, list]],  # idx_departements[dep_code][indicator] = serie 8 ans
    dict[str, dict[str, list]],  # idx_epci[siren_epci][indicator] = serie 8 ans
    dict[str, dict[str, list]],  # idx_communes[insee][indicator] = serie 8 ans
    set[str],                    # ensemble des indicateurs effectivement crees
]:
    """Construit 4 index a partir des fichiers d'agregats caches.

    Pour chaque ligne :
      - On agrege sur la region (reg_code natif)
      - On agrege sur le departement (dep_code natif, consolide 67A)
      - Si la commune du siege est connue (via mapping), on agrege aussi sur
        l'EPCI parent et la commune (consolidation PLM)

    Les montants (deja consolides BP+BA-flux par OFGL au niveau EPL) sont
    sommes par (territoire, activite, agregat) — sommation inter-EPL
    necessaire quand plusieurs EPL existent sur le meme territoire. Les
    valeurs nulles restent nulles ; un montant seul n'ecrase pas un null
    (initialise a None, additionne si non-None).
    """
    agregats = list_agregats()
    insee_to_epci = load_insee_to_epci()

    idx_regions: dict[str, dict[str, list]] = {}
    idx_departements: dict[str, dict[str, list]] = {}
    idx_epci: dict[str, dict[str, list]] = {}
    idx_communes: dict[str, dict[str, list]] = {}
    indicators_present: set[str] = set()

    n_records_total = 0
    n_records_no_insee = 0
    n_records_no_epci = 0
    n_records_no_dep = 0

    def _add(idx: dict, key: str, ind: str, year_idx: int, value: float) -> None:
        entry = idx.setdefault(key, {})
        serie = entry.setdefault(ind, list(NULL_SERIE))
        if serie[year_idx] is None:
            serie[year_idx] = value
        else:
            serie[year_idx] += value

    for ag in agregats:
        f = CACHE_DIR / f"{_slug(ag, 80)}.json"
        if not f.exists():
            continue
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for rec in records:
            n_records_total += 1
            siren = str(rec.get("siren") or "").strip()
            activite = (rec.get("libelle_cacti_simplifie") or "").strip()
            if not activite:
                continue
            annee = _parse_exer(rec.get("exer"))
            if annee is None or annee not in ANNEES:
                continue
            valeur = rec.get("montant")
            if valeur is None:
                continue
            try:
                valeur = float(valeur)
            except (TypeError, ValueError):
                continue
            year_idx = ANNEES.index(annee)
            ind = indicator_key(activite, ag)
            indicators_present.add(ind)

            # Niveau region : natif (avec normalisation padding 0 sur 2 chiffres
            # pour matcher synthese-regions qui utilise '01'..'04' pour les DOM)
            reg_code = _normalize_reg_code(rec.get("reg_code"))
            if reg_code:
                _add(idx_regions, reg_code, ind, year_idx, valeur)

            # Niveau departement : natif + consolidation 67A
            dep_code_raw = (rec.get("dep_code") or "").strip()
            if dep_code_raw:
                dep_code = _consolidate_dep_code(dep_code_raw)
                _add(idx_departements, dep_code, ind, year_idx, valeur)
            else:
                n_records_no_dep += 1

            # Niveau commune et EPCI : via mapping SIREN -> INSEE siege
            insee_raw = mapping.get(siren)
            if not insee_raw:
                n_records_no_insee += 1
                continue
            insee = _consolidate_plm(insee_raw)
            _add(idx_communes, insee, ind, year_idx, valeur)

            siren_epci = insee_to_epci.get(insee)
            if siren_epci:
                _add(idx_epci, siren_epci, ind, year_idx, valeur)
            else:
                n_records_no_epci += 1

    print(f"  [epl] lignes traitees    : {n_records_total}")
    print(f"  [epl]  sans INSEE siege  : {n_records_no_insee}")
    print(f"  [epl]  sans EPCI parent  : {n_records_no_epci}")
    print(f"  [epl]  sans dep_code     : {n_records_no_dep}")
    print(f"  [epl] indicateurs crees  : {len(indicators_present)}")
    print(f"  [epl] regions enrichies  : {len(idx_regions)}")
    print(f"  [epl] dpts enrichis      : {len(idx_departements)}")
    print(f"  [epl] EPCI enrichis      : {len(idx_epci)}")
    print(f"  [epl] communes enrichies : {len(idx_communes)}")

    # Duplication Metropole de Lyon : si '69' present, dupliquer en '691'
    # Hypothese : pas d'EPL dep_code=691 dans la donnee OFGL ; on ajoute donc
    # le contenu de 69 sur 691 (institution unique Rhone/Metropole, comme SDIS/MDPH).
    if "69" in idx_departements and "691" not in idx_departements:
        idx_departements["691"] = {
            k: list(v) for k, v in idx_departements["69"].items()
        }
        print(f"  [epl] 691 duplique depuis 69 ({len(idx_departements['691'])} indicateurs)")

    return idx_regions, idx_departements, idx_epci, idx_communes, indicators_present


# ---------------------------------------------------------------------------
# Etape 6 : fusion dans les synthese-*.json
# ---------------------------------------------------------------------------

def _indicator_id(ind) -> str:
    """Recupere la cle textuelle d'un indicateur, qu'il soit une string ou
    un dict avec champ 'key' (cas des synthese-departements et
    -intercommunalites qui contiennent un mix)."""
    if isinstance(ind, str):
        return ind
    if isinstance(ind, dict):
        return ind.get("key") or ""
    return ""


def _ensure_indicators(d: dict, indicators: list[str]) -> None:
    existing = {_indicator_id(ind) for ind in d.get("indicators", [])}
    for k in indicators:
        if k not in existing:
            d["indicators"].append(k)
            existing.add(k)


def merge_regions(idx: dict[str, dict[str, list]], indicators: list[str]) -> None:
    synth = DATA / "regions" / "synthese-regions-2024.json"
    if not synth.exists():
        print(f"  [merge regions] {synth} introuvable, skip")
        return
    d = json.loads(synth.read_text(encoding="utf-8"))
    _ensure_indicators(d, indicators)
    matched = 0
    for ent in d.get("entities", []):
        code = str(ent.get("code") or "").strip()
        if not code or code not in idx:
            continue
        matched += 1
        values = ent.setdefault("values", {})
        # synthese-regions a years=[2012..2024] (13 annees) : prefixer 5 nulls
        for k, serie in idx[code].items():
            values[k] = _pad_to_full(serie)
    synth.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  [merge regions] synthese-regions : {matched} regions enrichies (series 13 ans)")


def merge_departements(idx: dict[str, dict[str, list]], indicators: list[str]) -> None:
    synth = DATA / "departements" / "synthese-departements-2024.json"
    if not synth.exists():
        print(f"  [merge dpts] {synth} introuvable, skip")
        return
    d = json.loads(synth.read_text(encoding="utf-8"))
    _ensure_indicators(d, indicators)
    matched = 0
    for ent in d.get("entities", []):
        code = str(ent.get("code") or "").strip()
        if not code or code not in idx:
            continue
        matched += 1
        values = ent.setdefault("values", {})
        # synthese-departements a years=[2012..2024] (13 annees) : prefixer 5 nulls
        for k, serie in idx[code].items():
            values[k] = _pad_to_full(serie)
    synth.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  [merge dpts] synthese-departements : {matched} dpts enrichis (series 13 ans)")


def merge_epci(idx: dict[str, dict[str, list]], indicators: list[str]) -> None:
    synth = DATA / "intercommunalites" / "synthese-intercommunalites-2024.json"
    if not synth.exists():
        print(f"  [merge epci] {synth} introuvable, skip")
        return
    d = json.loads(synth.read_text(encoding="utf-8"))
    _ensure_indicators(d, indicators)
    matched = 0
    for ent in d.get("entities", []):
        siren = str(ent.get("siren") or "").strip()
        if not siren or siren not in idx:
            continue
        matched += 1
        values = ent.setdefault("values", {})
        for k, serie in idx[siren].items():
            values[k] = serie
    synth.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  [merge epci] synthese-intercommunalites : {matched} EPCI enrichis")


def merge_communes(idx: dict[str, dict[str, list]], indicators: list[str]) -> None:
    # 1. synthese-communes
    synth = DATA / "communes" / "synthese-communes-2024.json"
    if synth.exists():
        d = json.loads(synth.read_text(encoding="utf-8"))
        _ensure_indicators(d, indicators)
        matched = 0
        for c in d.get("communes", []):
            insee = _normalize_insee(c.get("insee"))
            if not insee or insee not in idx:
                continue
            matched += 1
            values = c.setdefault("values", {})
            for k, serie in idx[insee].items():
                values[k] = serie
        synth.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  [merge communes] synthese-communes : {matched} communes enrichies")

    # 2. by-dep
    by_dep_dir = DATA / "communes" / "by-dep"
    if by_dep_dir.exists():
        matched = total = 0
        for path in sorted(by_dep_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            dep_d = json.loads(path.read_text(encoding="utf-8"))
            _ensure_indicators(dep_d, indicators)
            for c in dep_d.get("communes", []):
                data = c.get("data") or {}
                insee = _normalize_insee(data.get("insee"))
                total += 1
                if not insee or insee not in idx:
                    continue
                matched += 1
                values = data.setdefault("values", {})
                for k, serie in idx[insee].items():
                    values[k] = serie
            path.write_text(json.dumps(dep_d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  [merge communes] by-dep : {matched}/{total} communes enrichies")

    # 3. decoratif-values (lazy par indicateur)
    paths_path = DATA / "communes" / "decoratif-paths-2024.json"
    meta_path = DATA / "communes" / "meta-communes-2024.json"
    if not paths_path.exists() or not meta_path.exists():
        return
    paths_data = json.loads(paths_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    n_meta = len(meta.get("communes", []))
    if len(paths_data.get("paths", [])) != n_meta:
        return
    values_dir = DATA / "communes" / "decoratif-values"
    values_dir.mkdir(parents=True, exist_ok=True)
    index_path = values_dir / "_index.json"
    index = {}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    new_inds = [k for k in indicators if k not in index]
    total_size = 0
    for k in new_inds:
        # Format sparse pour EPL : ne stocker que les communes avec au moins
        # une valeur non-null. Ratio typique : ~50/35000 communes par indicateur.
        sparse: list = []
        for i in range(n_meta):
            insee = _normalize_insee(meta["communes"][i][1])
            entry = idx.get(insee, {}) if insee else {}
            serie = entry.get(k)
            if serie is None:
                continue
            if not any(v is not None for v in serie):
                continue
            sparse.append([i] + list(serie))
        slug = _slug(k, max_len=100)
        index[k] = slug
        out_file = values_dir / f"{slug}.json"
        payload = {
            "indicator": k,
            "years": ANNEES,
            "values_sparse": sparse,
            "n_communes": len(sparse),
        }
        out_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        total_size += out_file.stat().st_size
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [merge communes] decoratif-values : {len(new_inds)} indicateurs ajoutes "
          f"({total_size/1024/1024:.1f} Mo, format sparse) ; index : {len(index)} indicateurs")


# ---------------------------------------------------------------------------
# Cleanup : supprimer les anciens indicateurs EPL avant insertion
# ---------------------------------------------------------------------------

def cleanup_old_epl() -> None:
    """Supprime tous les indicateurs commencant par 'EPL ' avant re-insertion."""
    prefix = "EPL "

    # 1. Decoratif-values communes
    values_dir = DATA / "communes" / "decoratif-values"
    index_path = values_dir / "_index.json"
    if index_path.exists():
        idx = json.loads(index_path.read_text(encoding="utf-8"))
        removed = 0
        for k in list(idx.keys()):
            if k.startswith(prefix):
                slug = idx.pop(k)
                p = values_dir / f"{slug}.json"
                if p.exists():
                    p.unlink()
                removed += 1
        index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        if removed:
            print(f"  [cleanup] decoratif-values : {removed} indicateurs EPL supprimes")

    # 2. Syntheses
    targets = [
        (DATA / "regions" / "synthese-regions-2024.json", ("entities",)),
        (DATA / "departements" / "synthese-departements-2024.json", ("entities",)),
        (DATA / "intercommunalites" / "synthese-intercommunalites-2024.json", ("entities",)),
        (DATA / "communes" / "synthese-communes-2024.json", ("communes",)),
    ]
    for path, entity_keys in targets:
        if not path.exists():
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        d["indicators"] = [k for k in d.get("indicators", []) if not _indicator_id(k).startswith(prefix)]
        for ek in entity_keys:
            for c in d.get(ek, []):
                v = c.get("values") or {}
                for k in list(v.keys()):
                    if k.startswith(prefix):
                        del v[k]
        path.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 3. by-dep communes
    by_dep_dir = DATA / "communes" / "by-dep"
    if by_dep_dir.exists():
        for path in sorted(by_dep_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            dep_d = json.loads(path.read_text(encoding="utf-8"))
            dep_d["indicators"] = [k for k in dep_d.get("indicators", []) if not k.startswith(prefix)]
            for c in dep_d.get("communes", []):
                v = (c.get("data") or {}).get("values", {})
                for k in list(v.keys()):
                    if k.startswith(prefix):
                        del v[k]
            path.write_text(json.dumps(dep_d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"  [cleanup] anciens indicateurs EPL purges des syntheses")


# ---------------------------------------------------------------------------
# Etape 7 : generer le snippet JS d'indicateurs
# ---------------------------------------------------------------------------

def write_indicators_snippet(indicators_present: set[str]) -> Path:
    """Genere un snippet JS avec les definitions d'indicateurs EPL.

    Format : 1 entree par indicateur, groupe = 'EPL - {activite}', levels = ALL_LEVELS.
    Aide standardisee expliquant l'agregation geographique.
    """
    out_path = DATA / "_tmp_indicators_epl.txt"
    # Reconstituer (activite, agregat) depuis la cle d'indicateur
    # Format : "EPL {activite} - {agregat} (eur)"
    entries: list[tuple[str, str, str]] = []  # (activite, agregat, full_key)
    for k in indicators_present:
        if not k.startswith("EPL ") or " - " not in k or not k.endswith(" (€)"):
            continue
        body = k[len("EPL "):-len(" (€)")]  # "Tourisme - Achats et charges externes"
        if " - " not in body:
            continue
        activite, agregat = body.split(" - ", 1)
        entries.append((activite, agregat, k))
    # Tri : activités alphabétiques (= ordre des groupes), puis agrégats dans
    # l'ordre MÉTIER canonique (et non alphabétique). cf. scripts/_agregats_order.py
    entries.sort(key=lambda e: (e[0], agregat_sort_key(e[1])))

    help_text = ("Comptes des établissements publics locaux (EPA, régies "
                 "personnalisées EPIC et EPCC, GIP autres) d'activité "
                 "« {activite} » toutes catégories confondues. Valeurs "
                 "consolidées OFGL (budget principal + budgets annexes - "
                 "flux croisés). Agrégation géographique : région et "
                 "département par champs natifs OFGL ; intercommunalité "
                 "et commune par localisation du siège (via API "
                 "recherche-entreprises). Agrégat OFGL : « {agregat} ». "
                 "Source : ofgl-base-epl-consolidee.")

    def _js_escape(s: str) -> str:
        # Pour JS double-quoted string : remplacer " par « », garder ' tel quel,
        # échapper les backslashes. Pas de fancy escape pour rester simple.
        return s.replace("\\", "\\\\").replace('"', '«')

    lines = [
        "  // ====================================================================",
        "  // EPL - Etablissements publics locaux (EPA, regies EPIC/EPCC, GIP autres)",
        "  // Agregation geographique multi-niveau. Source : ofgl-base-epl-consolidee.",
        "  // ====================================================================",
    ]
    for activite, agregat, key in entries:
        label = key[:-len(" (€)")]  # retirer le suffixe pour le label
        group = f"EPL - {activite}"
        help_str = help_text.format(activite=activite, agregat=agregat)
        lines.append(
            f'  {{ key: "{_js_escape(key)}", label: "{_js_escape(label)}", unit: "€",'
        )
        lines.append(
            f'    group: "{_js_escape(group)}", levels: ALL_LEVELS,'
        )
        lines.append(
            f'    help: "{_js_escape(help_str)}" }},'
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Liste des groupes uniques pour INDICATOR_GROUP_ORDER
    groups = sorted({f"EPL - {a}" for a, _, _ in entries})
    groups_path = DATA / "_tmp_groups_epl.txt"
    groups_path.write_text("\n".join(f'  "{g}",' for g in groups), encoding="utf-8")

    print(f"  [snippet] {len(entries)} indicateurs ecrits dans {out_path}")
    print(f"  [snippet] {len(groups)} groupes ecrits dans {groups_path}")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-mapping", action="store_true",
                        help="Forcer le re-mapping SIREN -> INSEE.")
    parser.add_argument("--skip-mapping", action="store_true",
                        help="Sauter le mapping (utilise le cache existant).")
    parser.add_argument("--skip-download", action="store_true",
                        help="Sauter le telechargement des agregats.")
    parser.add_argument("--force-download", action="store_true",
                        help="Re-telecharger meme si le cache existe.")
    parser.add_argument("--skip-cleanup", action="store_true",
                        help="Sauter le cleanup des anciens indicateurs EPL.")
    parser.add_argument("--skip-merge", action="store_true",
                        help="Sauter l'etape de fusion dans les syntheses.")
    parser.add_argument("--snippet-only", action="store_true",
                        help="Generer uniquement le snippet JS, sans toucher aux donnees.")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("EPL - Etablissements publics locaux (hors MDPH, hors CCAS/CIAS)")
    print("=" * 60)
    print()

    if args.snippet_only:
        # Charger les indicateurs depuis la synthese communes ou regenerer depuis cache
        synth = DATA / "communes" / "synthese-communes-2024.json"
        indicators = set()
        if synth.exists():
            d = json.loads(synth.read_text(encoding="utf-8"))
            for k in d.get("indicators", []):
                if k.startswith("EPL "):
                    indicators.add(k)
        write_indicators_snippet(indicators)
        return

    # 1. Mapping
    if not args.skip_mapping:
        print("[1/5] Mapping SIREN -> INSEE siege...")
        mapping = build_mapping(force=args.force_mapping)
    else:
        if MAPPING_PATH.exists():
            mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
            print(f"[1/5] Mapping charge depuis cache : {len(mapping)} SIREN")
        else:
            print("ERR : --skip-mapping mais mapping introuvable")
            return
    print()

    # 2. Telechargement des agregats
    if not args.skip_download:
        print("[2/5] Telechargement des agregats...")
        download_agregats(force=args.force_download)
    print()

    # 3. Cleanup
    if not args.skip_cleanup:
        print("[3/5] Cleanup des anciens indicateurs EPL...")
        cleanup_old_epl()
    print()

    # 4. Build indexes
    print("[4/5] Construction des 4 index multi-niveau...")
    idx_reg, idx_dep, idx_epci, idx_com, indicators_present = build_indexes(mapping)
    indicators_list = sorted(indicators_present)
    print()

    # 5. Merge
    if not args.skip_merge:
        print("[5/5] Fusion dans les syntheses...")
        merge_regions(idx_reg, indicators_list)
        merge_departements(idx_dep, indicators_list)
        merge_epci(idx_epci, indicators_list)
        merge_communes(idx_com, indicators_list)
        print()

    # 6. Snippet
    write_indicators_snippet(indicators_present)

    print(f"\nTermine en {time.time()-t0:.1f}s.")


if __name__ == "__main__":
    main()
