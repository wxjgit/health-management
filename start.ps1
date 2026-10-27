param([ValidateRange(1024, 65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Run .\setup.ps1 to initialize the environment first.'
}
Write-Host "Qingji API: http://127.0.0.1:$Port/api/health . Press Ctrl+C to stop."
Write-Host 'Import miniprogram in WeChat DevTools. The server no longer hosts a web frontend.'
Write-Host 'Development mode: no operating-system sandbox. Hardened deployment: scripts/start-hardened.ps1.'
Write-Host 'One Engine owns each data directory. Stop the service before offline job administration.'
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $Port --no-access-log
