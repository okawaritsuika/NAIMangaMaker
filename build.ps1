param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name NAIMangaMaker --icon assets/app.ico --hidden-import server --collect-submodules engine --add-data 'assets;assets' --add-data 'web;web' --add-data 'engine;engine' --add-data 'licenses;licenses' remote.py
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
} finally { Pop-Location }
