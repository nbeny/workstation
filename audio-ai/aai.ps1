#Requires -Version 7
# Raccourci vers la CLI audio-ai avec le venv dedie : .\audio-ai\aai.ps1 <commande> [options]
#   gpu | analyze <f> | separate <f> | process <f> [--target-bpm N --target-key Am --gains vocals=-3 --mute drums] | mix <dossier> -o <wav> | selftest
$home_ = if ($env:AUDIO_AI_HOME) { $env:AUDIO_AI_HOME } else { 'E:\audio-ai' }
$py = Join-Path $home_ 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw "venv absent : pwsh -File $PSScriptRoot\scripts\install.ps1" }
$cfg = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config\audio-ai.json') -Raw | ConvertFrom-Json -AsHashtable
foreach ($k in $cfg.env.Keys) { if (-not (Test-Path "env:$k")) { Set-Item "env:$k" $cfg.env[$k] } }
& $py (Join-Path $PSScriptRoot 'audio_pipeline.py') @args
exit $LASTEXITCODE
