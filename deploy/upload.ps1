<#
.SYNOPSIS
  StockWatch Gyakorlas (oktatasi kiadas) feltoltese a szerverre es telepitese / frissitese (deploy/install.sh).
  A korabbi StockWatch mellett fut ugyanazon a gepen (stockwatch-learn, 8766-os port).

.EXAMPLE
  .\deploy\upload.ps1 -Server stockwatch -Domain gyakorlas.pelda.hu

.NOTES
  Csak a kod megy fel: a helyi data/ es .env SOHA (a fo StockWatch adatait es kulcsait tartalmazzak).
  A titkokat (DATABASE_URL, SMTP_*, MAIL_FROM, PUBLIC_URL) a szerveren kell beirni:
    sudo -u stockwatch-learn nano /etc/stockwatch-learn/stockwatch-learn.env
  Kell hozza: OpenSSH kliens (Windows 10/11 beepitett: ssh, scp) es tar.
#>
param(
    [Parameter(Mandatory)][string]$Server,     # root@IP, ssh-config nev, vagy sudo-joggal biro felhasznalo@IP
    [string]$Domain = ""
)
$ErrorActionPreference = "Stop"
if ($Domain -and $Domain -notmatch '^[A-Za-z0-9.-]+$') { throw "Ervenytelen domain: $Domain" }

$root = Split-Path -Parent $PSScriptRoot
$pkg = Join-Path $env:TEMP "stockwatch-learn-deploy.tgz"
$exclude = @("--exclude=./.venv", "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=./.pytest_cache",
             "--exclude=./logs", "--exclude=./data", "--exclude=./.env", "--exclude=./start.bat", "--exclude=./.git")

try {
    Write-Host "Csomagolas..." -ForegroundColor Green
    tar -czf $pkg @exclude -C $root .
    if ($LASTEXITCODE) { throw "A csomagolas nem sikerult." }
    Write-Host ("Csomag: {0:N1} MB" -f ((Get-Item $pkg).Length / 1MB))

    Write-Host "Feltoltes: $Server" -ForegroundColor Green
    scp -q $pkg "${Server}:stockwatch-learn-deploy.tgz"
    if ($LASTEXITCODE) { throw "A feltoltes nem sikerult (ssh-kapcsolat?)." }

    $domainArg = if ($Domain) { "--domain $Domain" } else { "" }
    # a kicsomagolt forras privat ideiglenes mappaba kerul, es mindenkepp torlodik
    $remote = 'set -e; chmod 600 ~/stockwatch-learn-deploy.tgz; d=$(mktemp -d); trap ''rm -rf "$d" ~/stockwatch-learn-deploy.tgz'' EXIT; ' +
              'tar -xzf ~/stockwatch-learn-deploy.tgz -C "$d"; sed -i ''s/\r$//'' "$d/deploy/install.sh"; ' +
              "sudo bash `"`$d/deploy/install.sh`" $domainArg"
    Write-Host "Telepites a szerveren..." -ForegroundColor Green
    ssh -t $Server $remote
    if ($LASTEXITCODE) { throw "A telepites hibaval allt le (lasd fent)." }
}
finally {
    Remove-Item $pkg -ErrorAction SilentlyContinue
}
