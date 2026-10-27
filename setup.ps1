$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r backend\requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
Push-Location -LiteralPath 'miniprogram'
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Mini program dependency installation failed.' }
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Mini program type check failed.' }
    npm run build:weapp
    if ($LASTEXITCODE -ne 0) { throw 'Mini program build failed.' }
} finally {
    Pop-Location
}
Write-Host 'Setup complete. Run .\start.ps1 for the API. Import miniprogram in WeChat DevTools.'
