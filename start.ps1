$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run .\setup.ps1 first.' }
& '.\.venv\Scripts\python.exe' scripts/init_env.py
if (-not (Test-Path -LiteralPath 'dist\index.html')) {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Build failed. Run .\setup.ps1.' }
}
Write-Host 'Starting Alfa Business. The server prints the selected local URLs below.'
& '.\.venv\Scripts\python.exe' run.py
