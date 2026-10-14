@echo off
rem StockWatch inditas: monitor (ertesitesek) kulon ablakban + webes felulet a bongeszoben.
rem Dupla kattintassal indithato. Leallitas: a ket ablak bezarasa (vagy Ctrl+C).
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Nem talalom a virtualis kornyezetet: %PY%
    echo Telepites:  python -m venv .venv  ^&^&  .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

rem Ha a 8765-os port mar foglalt, a webes felulet valoszinuleg mar fut: csak megnyitjuk.
netstat -ano | findstr /r /c:"127.0.0.1:8765 .*LISTENING" >nul
if not errorlevel 1 (
    echo A webes felulet mar fut, megnyitom: http://127.0.0.1:8765
    start "" "http://127.0.0.1:8765"
    exit /b 0
)

start "StockWatch monitor" "%PY%" cli.py monitor
"%PY%" cli.py web

if errorlevel 1 pause
