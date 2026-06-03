<#
  serve.ps1 — Sert le site en local pour tester, AVEC les assets minifiés à jour.

      .\scripts\serve.ps1            # port 8000 par défaut
      .\scripts\serve.ps1 8001       # autre port

  Pourquoi ce script : le HTML sert app.min.js / style.min.css (versions
  minifiées). Si tu édites app.js ou style.css puis recharges la page sans
  régénérer les .min, tu verrais l'ANCIENNE version. Ce script regénère d'abord
  les .min (build_min.ps1) puis lance le serveur — ton test local reflète donc
  toujours tes dernières modifs.

  (Ctrl+C pour arrêter le serveur. Pense à Ctrl+F5 dans le navigateur pour
  contourner le cache du Service Worker, cf. CLAUDE.md.)
#>

param(
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& (Join-Path $PSScriptRoot "build_min.ps1")
if ($LASTEXITCODE -ne 0) { throw "La minification a echoue." }

Write-Host ""
Write-Host "Serveur local : http://localhost:$Port/  (Ctrl+C pour arreter)" -ForegroundColor Green
python -m http.server $Port --directory .
