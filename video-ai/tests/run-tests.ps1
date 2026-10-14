#Requires -Version 7
<#
.SYNOPSIS
    Tests de bout en bout video-ai (dependances, ComfyUI, API, GPU, image, video, montage, exports).
.EXAMPLE
    pwsh -File video-ai\tests\run-tests.ps1            # suite complete (generations reelles)
    pwsh -File video-ai\tests\run-tests.ps1 -Quick     # sans generation (5 et 6 = SKIP)
    pwsh -File video-ai\tests\run-tests.ps1 -Only 1,4
#>
[CmdletBinding()]
param([switch]$Quick, [string]$Only)
. "$PSScriptRoot\..\scripts\common.ps1"
$cfg = Get-VaiConfig
if (-not (Test-Path -LiteralPath $cfg.paths.python)) { Write-Error 'venv absent : lancez scripts\start.ps1'; exit 1 }
try { Invoke-VaiApi $cfg '/system_stats' 3 | Out-Null }
catch {
    Write-Host 'ComfyUI ne repond pas : demarrage via start.ps1' -ForegroundColor Yellow
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot '..\scripts\start.ps1')
}
$a = @()
if ($Quick) { $a += '--quick' }
if ($Only)  { $a += @('--only', $Only) }
$saved = $env:PYTHONPATH
$env:PYTHONPATH = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
try { & $cfg.paths.python -m vai.selftest @a; $code = $LASTEXITCODE } finally { $env:PYTHONPATH = $saved }
exit $code
