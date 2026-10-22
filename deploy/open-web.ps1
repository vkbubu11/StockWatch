<#
.SYNOPSIS
  A szerveren futo StockWatch Gyakorlas (oktatasi kiadas) megnyitasa SSH-alagutan at (domain / HTTPS nelkul).

.EXAMPLE
  .\deploy\open-web.ps1                  # alap: az ~/.ssh/config "stockwatch" gepe
  .\deploy\open-web.ps1 -Server ubuntu@92.5.59.191

.NOTES
  Helyben a 18766-os portot hasznalja (a szerveren 8766: a korabbi StockWatch a 8765-on fut).
  Bezaras: ebben az ablakban Ctrl+C.
#>
param(
    [string]$Server = "stockwatch",
    [int]$LocalPort = 18766
)
$url = "http://127.0.0.1:$LocalPort"
Write-Host "Alagut: $url -> $Server (a szerveren 127.0.0.1:8766). Bezaras: Ctrl+C" -ForegroundColor Green
Start-Job { Start-Sleep 3; Start-Process $using:url } | Out-Null
ssh -N -o ExitOnForwardFailure=yes -L "${LocalPort}:127.0.0.1:8766" $Server
