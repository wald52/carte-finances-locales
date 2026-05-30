"""Compresse en gzip exactement les fichiers de `data/` réellement servis au
navigateur, afin de réduire le poids publié sur GitHub Pages.

Pour chaque fichier servi `X.json`, produit `X.json.gz` à côté. Le site
(app.js / decoratif-worker.js) charge d'abord le `.gz` et le décompresse dans
le navigateur (DecompressionStream), avec repli sur le `.json` brut.

Propriétés :
  - INCRÉMENTAL : ne recompresse que les fichiers dont le `.json` est plus
    récent que son `.json.gz` (les mises à jour sont donc rapides).
  - DÉTERMINISTE : en-tête gzip sans horodatage (mtime=0) → un fichier source
    inchangé produit toujours les mêmes octets → aucun « bruit » dans git.
  - PARALLÈLE : compression multi-cœurs.
  - PRUNE : supprime les `.json.gz` orphelins (dont le `.json` source a disparu).

Idempotent : ré-exécutable sans risque. Usage : `python scripts/build_gzip_served.py`
(option `--force` pour tout recompresser, `--no-prune` pour garder les orphelins).
"""

import concurrent.futures
import gzip
import io
import os
import sys
import time

# Sortie UTF-8 forcée (Windows cp1252 sinon plante sur les accents / flèches).
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVEL = 6  # bon compromis vitesse / taille (cf. mesure : ratio global ~x5.2)

# --- Ensemble EXACT des fichiers servis (= ce que app.js / le worker fetchent).
#     Toute famille de .json chargée au runtime doit figurer ici, sinon elle ne
#     sera pas compressée et le site la chargera en .json brut (repli), ce qui
#     gonfle le poids publié. Voir app.js (fonction loadJson + call sites).
SERVED_DIRS = [
    "data/communes/by-dep",
    "data/communes/decoratif-values",
    "data/intercommunalites/by-epci",
    "data/intercommunalites/by-region",
    "data/intercommunalites/ei-details",
    "data/syndicats/decoratif-values",
    "data/syndicats/leaderboards",
    "data/syndicats/details",
]
SERVED_SINGLES = [
    "data/regions/synthese-regions-2024.json",
    "data/regions/regions-svg.json",
    "data/departements/synthese-departements-2024.json",
    "data/departements/departements-svg.json",
    "data/communes/decoratif-paths-2024.json",
    "data/communes/meta-communes-2024.json",
    "data/intercommunalites/synthese-intercommunalites-2024.json",
]


def served_json_files():
    """Liste absolue de tous les .json servis (dossiers + singles)."""
    files = []
    for rel in SERVED_DIRS:
        ad = os.path.join(ROOT, rel)
        if not os.path.isdir(ad):
            continue
        for name in os.listdir(ad):
            if name.endswith(".json"):
                files.append(os.path.join(ad, name))
    for rel in SERVED_SINGLES:
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            files.append(p)
    return files


def needs_recompress(src):
    gz = src + ".gz"
    if not os.path.exists(gz):
        return True
    return os.path.getmtime(src) > os.path.getmtime(gz)


def compress_one(src):
    """Compresse src → src.gz (déterministe). Renvoie (octets_in, octets_out).

    Robustesse Windows :
      - le fichier temporaire porte le PID du worker → deux exécutions du script
        en parallèle ne se marchent pas dessus (cause d'un PermissionError vécu).
      - `os.replace` est réessayé : un antivirus peut tenir brièvement le `.gz`
        fraîchement écrit, ce qui fait échouer le renommage de façon transitoire.
    """
    gz = src + ".gz"
    with open(src, "rb") as f:
        data = f.read()
    out = gzip.compress(data, compresslevel=LEVEL, mtime=0)
    tmp = f"{gz}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(out)
    for attempt in range(6):
        try:
            os.replace(tmp, gz)  # écriture atomique
            break
        except PermissionError:
            if attempt == 5:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.25)
    return len(data), len(out)


def prune_orphans():
    """Supprime les .json.gz dont le .json source n'existe plus."""
    removed = 0
    for rel in SERVED_DIRS:
        ad = os.path.join(ROOT, rel)
        if not os.path.isdir(ad):
            continue
        for name in os.listdir(ad):
            if name.endswith(".json.gz"):
                src = os.path.join(ad, name[:-3])  # retire « .gz »
                if not os.path.exists(src):
                    os.remove(os.path.join(ad, name))
                    removed += 1
    return removed


def cleanup_tmp():
    """Supprime les fichiers temporaires `*.gz.tmp` / `*.gz.<pid>.tmp` laissés
    par une exécution précédente interrompue."""
    dirs = list(SERVED_DIRS)
    for rel in SERVED_SINGLES:
        dirs.append(os.path.dirname(rel))
    seen = set()
    for rel in dirs:
        ad = os.path.join(ROOT, rel)
        if ad in seen or not os.path.isdir(ad):
            continue
        seen.add(ad)
        for name in os.listdir(ad):
            if name.endswith(".tmp") and ".gz" in name:
                try:
                    os.remove(os.path.join(ad, name))
                except OSError:
                    pass


def main():
    force = "--force" in sys.argv
    do_prune = "--no-prune" not in sys.argv

    t0 = time.time()
    cleanup_tmp()
    files = served_json_files()
    todo = files if force else [f for f in files if needs_recompress(f)]
    print(f"{len(files)} fichiers servis ; {len(todo)} à (re)compresser"
          + (" [--force]" if force else ""))

    so = sg = 0
    if todo:
        with concurrent.futures.ProcessPoolExecutor() as ex:
            done = 0
            for (oi, og) in ex.map(compress_one, todo, chunksize=16):
                so += oi
                sg += og
                done += 1
                if done % 2000 == 0:
                    print(f"  … {done}/{len(todo)}")

    pruned = prune_orphans() if do_prune else 0

    total_gz = 0
    for f in files:
        gz = f + ".gz"
        if os.path.exists(gz):
            total_gz += os.path.getsize(gz)

    print(f"Compressé {len(todo)} fichier(s) en {time.time() - t0:.0f}s"
          + (f" ({so / 1e6:.0f} Mo → {sg / 1e6:.0f} Mo)" if todo else ""))
    if pruned:
        print(f"Orphelins supprimés : {pruned}")
    print(f"Poids total .gz servi : {total_gz / 1e9:.2f} Go")


if __name__ == "__main__":
    main()
