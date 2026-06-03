<#
  publier.ps1 — Met à jour le site en ligne en UNE commande.

  Depuis le dossier du projet, lance simplement :

      .\scripts\publier.ps1

  (ou avec un message :  .\scripts\publier.ps1 "Ajout des données 2025")

  Ce que fait le script, dans l'ordre :
    1. Minifie app.js / style.css → *.min (build_min.ps1) — ce sont les versions
       réellement servies au navigateur (allège le chemin critique).
    2. Recompresse les données servies qui ont changé   (build_gzip_served.py)
    3. Incrémente la version du Service Worker (sw.js)   — INDISPENSABLE pour
       que les visiteurs déjà venus voient les nouvelles données (sinon leur
       navigateur ressert l'ancienne version en cache).
    4. git add / commit / push  → GitHub republie le site automatiquement (~1 min).

  Tu n'as JAMAIS à trier les fichiers à la main : le .gitignore ne versionne
  que les .json.gz servis, et git détecte tout seul ce qui a changé.
#>

param(
  [string]$Message = ""
)

$ErrorActionPreference = "Stop"
# Se placer à la racine du projet (le dossier parent de /scripts).
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> 1/4  Minification de app.js et style.css..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "build_min.ps1")
if ($LASTEXITCODE -ne 0) { throw "La minification a echoue." }

Write-Host "==> 2/4  Compression des donnees servies (incremental)..." -ForegroundColor Cyan
python scripts/build_gzip_served.py
if ($LASTEXITCODE -ne 0) { throw "La compression a echoue." }

Write-Host "==> 3/4  Mise a jour de la version du Service Worker..." -ForegroundColor Cyan
$swPath = Join-Path $root "sw.js"
$sw = Get-Content $swPath -Raw
if ($sw -match 'echelons-locaux-v(\d+)') {
  $next = [int]$Matches[1] + 1
  $sw = $sw -replace 'echelons-locaux-v\d+', "echelons-locaux-v$next"
  Set-Content -Path $swPath -Value $sw -NoNewline -Encoding UTF8
  Write-Host "    Service Worker : v$next"
} else {
  Write-Warning "    Version du Service Worker introuvable dans sw.js (etape ignoree)."
}

Write-Host "==> 4/4  Envoi sur GitHub..." -ForegroundColor Cyan
if ([string]::IsNullOrWhiteSpace($Message)) {
  $Message = "Mise a jour du site ($(Get-Date -Format 'yyyy-MM-dd'))"
}
git add -A
# Ne commit/push que s'il y a vraiment quelque chose de nouveau.
$pending = git status --porcelain
if ([string]::IsNullOrWhiteSpace($pending)) {
  Write-Host "Rien de nouveau a publier. Site deja a jour." -ForegroundColor Green
  exit 0
}
git commit -m $Message
git push

Write-Host ""
Write-Host "Termine ! GitHub republie le site dans ~1 minute." -ForegroundColor Green
Write-Host "URL : https://wald52.github.io/carte-finances-locales/" -ForegroundColor Green
