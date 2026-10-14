<#
.SYNOPSIS
  StockWatch feltoltese a szerverre es telepitese / frissitese (deploy/install.sh).

.EXAMPLE
  # elso telepites: kod + config + data + .env (titkok), HTTPS-sel
  .\deploy\upload.ps1 -Server root@203.0.113.10 -Domain stockwatch.pelda.hu

.EXAMPLE
  # frissites: csak a kod (a szerveren levo adat es titkok maradnak)
  .\deploy\upload.ps1 -Server root@203.0.113.10 -Domain stockwatch.pelda.hu -CodeOnly

.NOTES
  Kell hozza: OpenSSH kliens (Windows 10/11 beepitett: ssh, scp) es tar.
  A csomag SSH-n (titkositva) megy at, a szerveren a telepites utan torlodik.
#>
param(
    [Parameter(Mandatory)][string]$Server,     # root@IP, vagy sudo-joggal biro felhasznalo@IP
    [string]$Domain = "",
    [switch]$CodeOnly                          # data/ es .env nelkul
)
$ErrorActionPreference = "Stop"
if ($Domain -and $Domain -notmatch '^[A-Za-z0-9.-]+$') { throw "Ervenytelen domain: $Domain" }

$root = Split-Path -Parent $PSScriptRoot
$pkg = Join-Path $env:TEMP "stockwatch-deploy.tgz"
$exclude = @("--exclude=./.venv", "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=./.pytest_cache",
             "--exclude=./logs", "--exclude=./data/research", "--exclude=./data/universe", "--exclude=./start.bat")
if ($CodeOnly) { $exclude += @("--exclude=./data", "--exclude=./.env") }

try {
    Write-Host "Csomagolas..." -ForegroundColor Green
    tar -czf $pkg @exclude -C $root .
    if ($LASTEXITCODE) { throw "A csomagolas nem sikerult." }
    Write-Host ("Csomag: {0:N1} MB" -f ((Get-Item $pkg).Length / 1MB))

    Write-Host "Feltoltes: $Server" -ForegroundColor Green
    scp -q $pkg "${Server}:stockwatch-deploy.tgz"
    if ($LASTEXITCODE) { throw "A feltoltes nem sikerult (ssh-kapcsolat?)." }

    $domainArg = if ($Domain) { "--domain $Domain" } else { "" }
    # a kicsomagolt forras (benne a .env) privat ideiglenes mappaba kerul, es mindenkepp torlodik
    $remote = 'set -e; chmod 600 ~/stockwatch-deploy.tgz; d=$(mktemp -d); trap ''rm -rf "$d" ~/stockwatch-deploy.tgz'' EXIT; ' +
              'tar -xzf ~/stockwatch-deploy.tgz -C "$d"; sed -i ''s/\r$//'' "$d/deploy/install.sh"; ' +
              "sudo bash `"`$d/deploy/install.sh`" $domainArg"
    Write-Host "Telepites a szerveren..." -ForegroundColor Green
    ssh -t $Server $remote
    if ($LASTEXITCODE) { throw "A telepites hibaval allt le (lasd fent)." }
}
finally {
    Remove-Item $pkg -ErrorAction SilentlyContinue      # a csomagban titkok is lehetnek
}
