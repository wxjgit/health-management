param([string]$Project = 'qingji-hardened', [string]$Output = 'output/runtime-verification.json')
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'verify-runtime.py') --project $Project --output $Output
exit $LASTEXITCODE
