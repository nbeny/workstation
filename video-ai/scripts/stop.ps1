#Requires -Version 7
<#
.SYNOPSIS
    Arrete l'instance ComfyUI demarree par ce workspace — et uniquement elle.
.DESCRIPTION
    L'instance est identifiee par run\comfyui.pid.json (PID + heure de demarrage).
    Un ComfyUI lance autrement (ou un PID recycle) n'est jamais touche.
#>
[CmdletBinding()]
param()
. "$PSScriptRoot\common.ps1"
$cfg = Get-VaiConfig
Initialize-VaiLog $cfg 'stop'
Write-Step 'Arret de ComfyUI (instance du workspace)'
$procs = @(Get-VaiOwnedProcesses $cfg)
if (-not $procs) {
    Write-Skip 'aucune instance demarree par ce workspace'
    if (Test-Path -LiteralPath $cfg.paths.pidfile) { Remove-Item -LiteralPath $cfg.paths.pidfile }
    if (Test-TcpPortListening $cfg.host $cfg.port) {
        $o = Get-PortOwner $cfg.port
        Write-WarnL "le port $($cfg.port) reste utilise par '$($o.ProcessName)' (PID $($o.Id)), non lance par ce workspace : laisse intact."
    }
    exit 0
}
# Lanceur (venv uv) + interpreteur reel, chacun verifie (PID + heure de demarrage) -> arret de l'arbre.
foreach ($p in $procs) { & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null }
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline -and (Test-TcpPortListening $cfg.host $cfg.port)) { Start-Sleep -Milliseconds 500 }
Remove-Item -LiteralPath $cfg.paths.pidfile -ErrorAction SilentlyContinue
if (Test-TcpPortListening $cfg.host $cfg.port) { Write-Fail "le port $($cfg.port) est encore occupe"; exit 1 }
Write-Ok "ComfyUI arrete (PID $(($procs | ForEach-Object Id) -join ', '))"
