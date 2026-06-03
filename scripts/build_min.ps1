<#
  build_min.ps1 — Génère les versions MINIFIÉES servies de app.js et style.css.

  Pourquoi : alléger le chemin critique du chargement (recommandation Lighthouse
  « Minify JavaScript / CSS »). Les SOURCES restent app.js / style.css (éditables,
  commentées) ; le HTML sert les copies minifiées app.min.js / style.min.css.

  Lancé automatiquement par publier.ps1 (et par serve.ps1 pour le test local).
  À lancer aussi à la main après une édition de app.js/style.css si tu testes
  en local sans passer par serve.ps1 :

      .\scripts\build_min.ps1

  Minifieur : esbuild via `npx` (aucune dépendance installée dans le projet ;
  esbuild est mis en cache par npx au 1er appel — nécessite le réseau cette
  fois-là). app.js est servi en `type="module"` → on garde le format ESM.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Show-Saving($srcRel, $minRel) {
  $src = (Get-Item $srcRel).Length
  $min = (Get-Item $minRel).Length
  $pct = [math]::Round((1 - $min / $src) * 100)
  "{0,-26} {1,7:N0} -> {2,7:N0} octets  (-{3}%)" -f $minRel, $src, $min, $pct
}

Write-Host "Minification (esbuild via npx)..." -ForegroundColor Cyan

# JS — module ESM, charset utf-8 (garde les accents français lisibles, plus léger
# que des \u), pas de commentaires de licence.
npx --yes esbuild assets/js/app.js `
  --minify --format=esm --charset=utf8 --legal-comments=none `
  --outfile=assets/js/app.min.js
if ($LASTEXITCODE -ne 0) { throw "esbuild a echoue sur app.js" }

# CSS
npx --yes esbuild assets/css/style.css `
  --minify --charset=utf8 `
  --outfile=assets/css/style.min.css
if ($LASTEXITCODE -ne 0) { throw "esbuild a echoue sur style.css" }

Write-Host (Show-Saving "assets/js/app.js"     "assets/js/app.min.js")  -ForegroundColor Green
Write-Host (Show-Saving "assets/css/style.css" "assets/css/style.min.css") -ForegroundColor Green
