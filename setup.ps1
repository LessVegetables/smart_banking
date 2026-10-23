$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
    else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12+ and run setup.ps1 again.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& npm.cmd ci --no-audit --no-fund --cache .npm-cache
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
& '.\.venv\Scripts\python.exe' scripts/init_env.py
Write-Host 'Ready. Run .\start.ps1, then open http://127.0.0.1:8000'
