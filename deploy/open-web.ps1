<#
.SYNOPSIS
  A szerveren futo StockWatch felulet megnyitasa SSH-alagutan at (domain / HTTPS nelkul).

.EXAMPLE
  .\deploy\open-web.ps1                  # alap: az ~/.ssh/config "stockwatch" gepe
  .\deploy\open-web.ps1 -Server ubuntu@92.5.59.191

.NOTES
  Helyben a 18765-os portot hasznalja, hogy ne utkozzon a helyi start.bat felulettel (8765).
  Bezaras: ebben az ablakban Ctrl+C.
#>
param(
    [string]$Server = "stockwatch",
    [int]$LocalPort = 18765
)
$url = "http://127.0.0.1:$LocalPort"
Write-Host "Alagut: $url -> $Server (a szerveren 127.0.0.1:8765). Bezaras: Ctrl+C" -ForegroundColor Green
Start-Job { Start-Sleep 3; Start-Process $using:url } | Out-Null
ssh -N -o ExitOnForwardFailure=yes -L "${LocalPort}:127.0.0.1:8765" $Server
