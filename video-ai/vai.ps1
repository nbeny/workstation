#Requires -Version 7
# Point d'entree de la CLI vai avec le Python du venv ComfyUI (numpy/psutil disponibles).
#   .\video-ai\vai.ps1 status
#   .\video-ai\vai.ps1 run flux2_klein_4b_t2i prompt="portrait ..." seed=42
. "$PSScriptRoot\scripts\common.ps1"
$cfg = Get-VaiConfig
if (-not (Test-Path -LiteralPath $cfg.paths.python)) {
    Write-Error "venv absent ($($cfg.paths.python)) : lancez d'abord video-ai\scripts\start.ps1"
    exit 1
}
$saved = $env:PYTHONPATH, $env:PYTHONUTF8
$env:PYTHONPATH = $PSScriptRoot; $env:PYTHONUTF8 = '1'
try { & $cfg.paths.python -m vai @args; $code = $LASTEXITCODE }
finally { $env:PYTHONPATH, $env:PYTHONUTF8 = $saved }
exit $code
