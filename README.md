# Gestion des échelons locaux — carte des finances locales

Site statique (carte de France interactive) qui visualise les indicateurs
financiers des collectivités territoriales françaises — régions, départements,
intercommunalités, communes, syndicats — sur 2012-2024, à partir des données
ouvertes de l'**OFGL** et de **BANATIC**.

**Site en ligne :** https://wald52.github.io/carte-finances-locales/

## Données mobilisées

- **OFGL** (Observatoire des finances et de la gestion publique locales) :
  comptes des collectivités, dotations, fiscalité.
- **BANATIC** (base nationale sur l'intercommunalité) : périmètres et
  compétences des groupements.
- **INSEE** : population, géographie administrative.
- **geo.api.gouv.fr** : contours géographiques.

Méthodologie et précautions de lecture détaillées dans `sources.html`.

## Mettre à jour le site (une seule commande)

Depuis le dossier du projet, dans PowerShell :

```powershell
.\scripts\publier.ps1
```

Le script s'occupe de **tout, automatiquement** (aucun tri de fichiers à la
main) :

1. il recompresse les données qui ont changé,
2. il met à jour la version du cache navigateur (sinon les visiteurs déjà venus
   verraient encore l'ancienne version),
3. il envoie le tout sur GitHub.

GitHub republie le site tout seul, environ une minute après.

> Pour accompagner la mise à jour d'un message :
> `.\scripts\publier.ps1 "Ajout des données 2025"`

## Comment c'est hébergé (à savoir)

Le site charge ~7 Go de données. Pour tenir sur l'hébergement gratuit GitHub
Pages, **seules les données compressées (`.json.gz`, ~1,3 Go) sont publiées** :
le navigateur les décompresse à la volée. Les données brutes OFGL/BANATIC et les
caches de travail (~12 Go) **restent sur ton ordinateur** et ne sont pas
envoyés en ligne (cf. `.gitignore`). Le fichier `.nojekyll` est nécessaire pour
que GitHub serve correctement les fichiers internes (`_index.json.gz`).

La compression est gérée par `scripts/build_gzip_served.py` (appelé
automatiquement par `publier.ps1`).

## Servir le site en local

```bash
python -m http.server --directory . 8000
```
Puis ouvrir http://localhost:8000.

## Reconstruire les données depuis zéro

Voir `CLAUDE.md` (section 5, pipeline des scripts `fetch_*` / `build_*`).

## Licence

Données sous Licence Ouverte 2.0.
