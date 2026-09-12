param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python release_packaging.py
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
} finally { Pop-Location }
