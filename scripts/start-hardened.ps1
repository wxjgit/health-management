param([Parameter(Mandatory=$true)][string]$SecretFile)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$secretPath = (Resolve-Path -LiteralPath $SecretFile).Path
if ($secretPath.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Secret must be outside the repository/build context.'
}
if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) { throw 'Secret file required.' }
$env:MODEL_API_KEY_SOURCE_FILE = $secretPath
& docker compose -f compose.hardened.yaml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid hardened deployment configuration.' }
& docker compose -f compose.hardened.yaml up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Hardened deployment failed; no development fallback was started.' }
Write-Host 'Containers started. Host network policy, external supervision and runtime verification are required before accepting hardened deployment.'
& python (Join-Path $PSScriptRoot 'supervise-runtime.py') --project qingji-hardened
if ($LASTEXITCODE -ne 0) { throw 'Runtime supervision stopped. Inspect deployment before accepting traffic.' }
