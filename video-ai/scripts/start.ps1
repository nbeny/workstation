#Requires -Version 7
<#
.SYNOPSIS
    Lancement du workspace video-ai : verifications, demarrage de ComfyUI (une seule instance),
    attente de l'API, controle de sante et resume. Appele par l'onglet Warp "video-ai".

.DESCRIPTION
    1. materiel (GPU, RAM, disque)      2. dependances + chemins des modeles
    3. environnement Python (install.ps1 si absent, jamais d'ecrasement)
    4. demarrage de ComfyUI si necessaire (127.0.0.1 uniquement)
    5. attente de l'API                 6. controle de sante (backend GPU reel)
    7. resume : URL, GPU, modeles, erreurs
    Un modele manquant n'empeche pas le demarrage : la commande d'installation est affichee.

.EXAMPLE
    pwsh -File video-ai\scripts\start.ps1
    pwsh -File video-ai\scripts\start.ps1 -NoStart      # diagnostic seul
    pwsh -File video-ai\scripts\start.ps1 -Open         # ouvre l'interface dans le navigateur
#>
[CmdletBinding()]
param(
    [switch]$NoStart,
    [switch]$NoInstall,
    [switch]$Open
)
. "$PSScriptRoot\common.ps1"
$cfg = Get-VaiConfig
$P = $cfg.paths
New-Item -ItemType Directory -Force -Path $P.logs, $P.run | Out-Null
Initialize-VaiLog $cfg 'start'
$problems = [System.Collections.Generic.List[string]]::new()
Write-Host "video-ai — workspace de production IA video" -ForegroundColor Magenta

# 1. Materiel -------------------------------------------------------------------
Write-Step 'Materiel'
$gpu = Get-CimInstance Win32_VideoController | Where-Object Name -match 'Radeon|NVIDIA|Arc' | Select-Object -First 1
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($gpu) { Write-Ok "GPU : $($gpu.Name) (driver $($gpu.DriverVersion))" } else { Write-WarnL 'aucun GPU dedie detecte'; $problems.Add('GPU non detecte') }
Write-Ok "RAM : $ramGB Go"
$homeDrive = [System.IO.Path]::GetPathRoot($P.home).Substring(0,1)
$free = (Get-PSDrive -Name $homeDrive -ErrorAction SilentlyContinue).Free
if ($null -eq $free) { Write-Fail "lecteur $homeDrive`: indisponible"; $problems.Add("lecteur $homeDrive absent"); }
elseif ($free -lt 20GB) { Write-WarnL ("{0}: seulement {1} libres" -f $homeDrive, (Format-Bytes $free)) }
else { Write-Ok ("disque {0}: {1} libres" -f $homeDrive, (Format-Bytes $free)) }

# 2. Dependances ------------------------------------------------------------------
Write-Step 'Dependances'
foreach ($t in 'git','uv','ffmpeg','ffprobe') {
    $c = Get-Command $t -ErrorAction SilentlyContinue
    if ($c) { Write-Ok "$t" } else { Write-Fail "$t introuvable"; $problems.Add("$t manquant") }
}

# 3. Environnement Python -----------------------------------------------------------
Write-Step 'Environnement ComfyUI'
$envReady = (Test-Path -LiteralPath $P.python) -and (Test-Path -LiteralPath (Join-Path $P.comfyui 'main.py')) -and (Test-Path -LiteralPath $P.extra_yaml)
if (-not $envReady -and -not $NoInstall -and -not $NoStart) {
    Write-WarnL 'environnement absent : installation (une seule fois, ~3 Go)...'
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'install.ps1')
    $envReady = ($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $P.python)
}
if ($envReady) { Write-Ok "ComfyUI : $($P.comfyui)"; Write-Ok "venv : $($P.venv)" }
else { Write-Fail 'environnement ComfyUI non installe -> pwsh -File video-ai\scripts\install.ps1'; $problems.Add('ComfyUI non installe') }

# Modeles (verification rapide : presence + taille) ------------------------------------
Write-Step "Modeles ($($P.models))"
$manifest = Get-ModelManifest
$modelState = [ordered]@{}
foreach ($g in $manifest.groups.Keys) {
    $st = @(Get-ModelGroupStatus $cfg $g)
    $miss = @($st | Where-Object { -not $_.present })
    $modelState[$g] = ($miss.Count -eq 0)
    if (-not $miss) { Write-Ok "$g : pret" }
    else {
        Write-WarnL ("{0} : {1} fichier(s) manquant(s) ({2}) -> pwsh -File video-ai\scripts\download-models.ps1 -Group {0}" -f $g, $miss.Count, (Format-Bytes (($miss | Measure-Object size -Sum).Sum)))
    }
}

# 4. ComfyUI ------------------------------------------------------------------------
Write-Step "ComfyUI ($($cfg.url))"
function Test-ComfyApi { try { return Invoke-VaiApi $cfg '/system_stats' 3 } catch { return $null } }

$owned = Get-VaiOwnedProcess $cfg
$stats = Test-ComfyApi
$started = $null
if ($stats -and $owned) { Write-Skip "deja demarre par ce workspace (PID $($owned.Id))" }
elseif ($stats) { Write-WarnL "une instance ComfyUI NON geree par ce workspace repond deja sur le port $($cfg.port) — utilisee telle quelle, jamais arretee par stop.ps1" }
elseif (Test-TcpPortListening $cfg.host $cfg.port) {
    $o = Get-PortOwner $cfg.port
    Write-Fail "port $($cfg.port) occupe par '$($o.ProcessName)' (PID $($o.Id)) — choisissez un autre port : `$env:VIDEO_AI_PORT=8189"
    $problems.Add("port $($cfg.port) occupe")
}
elseif ($NoStart) { Write-Skip 'non demarre (-NoStart)' }
elseif (-not $envReady) { Write-Skip 'non demarre (environnement absent)' }
else {
    if ($owned) { Write-WarnL "PID $($owned.Id) vivant mais API muette — redemarrage"; & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'stop.ps1') | Out-Null }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $errLog = Join-Path $P.logs "comfyui-$stamp.log"
    $miopen = Join-Path $P.cache 'miopen'
    New-Item -ItemType Directory -Force -Path $miopen, $P.input, $P.output, $P.user, $P.temp | Out-Null
    # Script de lancement genere : variables d'environnement propres a ComfyUI + journal unique.
    $childEnv = [ordered]@{ PYTHONUNBUFFERED = '1'; MIOPEN_USER_DB_PATH = $miopen; MIOPEN_CUSTOM_CACHE_DIR = $miopen }
    foreach ($k in $cfg.env.Keys) { $childEnv[$k] = $cfg.env[$k] }
    $argv = @('main.py', '--listen', $cfg.host, '--port', "$($cfg.port)",
              '--extra-model-paths-config', $P.extra_yaml,
              '--input-directory', $P.input, '--output-directory', $P.output,
              '--user-directory', $P.user, '--temp-directory', $P.temp,
              '--disable-auto-launch') + $cfg.comfy_args
    $quoted = ($argv | ForEach-Object { if ($_ -match '[\s&|<>^]') { '"{0}"' -f $_ } else { $_ } }) -join ' '
    $launcher = Join-Path $P.run 'comfyui-launch.cmd'
    @('@echo off', 'rem Genere par video-ai\scripts\start.ps1 — ne pas editer.') +
        ($childEnv.Keys | ForEach-Object { 'set "{0}={1}"' -f $_, $childEnv[$_] }) +
        @(('cd /d "{0}"' -f $P.comfyui), ('"{0}" {1} > "{2}" 2>&1' -f $P.python, $quoted, $errLog)) |
        Set-Content -LiteralPath $launcher -Encoding ascii
    # Win32_Process.Create : processus detache (aucun handle herite de ce terminal, survit a la fermeture de l'onglet).
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = "cmd.exe /d /c `"$launcher`""; CurrentDirectory = $P.comfyui }
    if ($r.ReturnValue -ne 0) { throw "Win32_Process.Create a echoue (code $($r.ReturnValue))" }
    $started = Get-Process -Id $r.ProcessId
    # Arbre : cmd.exe -> python.exe (lanceur du venv uv) -> python.exe (interpreteur reel)
    $tree = @($started)
    foreach ($i in 1..40) {
        $kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($tree[-1].Id)" | Where-Object Name -eq 'python.exe')
        if ($kids) { $kp = Get-Process -Id $kids[0].ProcessId -ErrorAction SilentlyContinue; if ($kp) { $tree += $kp } }
        if ($tree.Count -ge 3 -or $started.HasExited) { break }
        if (-not $kids) { Start-Sleep -Milliseconds 250 }
    }
    @{ procs = @($tree | ForEach-Object { @{ pid = $_.Id; ticks = $_.StartTime.ToUniversalTime().Ticks; name = $_.ProcessName } });
       port = $cfg.port; log = $errLog } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $P.pidfile -Encoding utf8
    Write-Ok "processus demarre (PID $(($tree | ForEach-Object Id) -join ' > ')) — journal : $errLog"

    # 5. Attente de l'API (Ctrl+C => arret propre de l'instance qu'on vient de lancer)
    $healthy = $false
    try {
        $deadline = (Get-Date).AddSeconds($cfg.startup_timeout_sec)
        while ((Get-Date) -lt $deadline) {
            if ($started.HasExited) { break }
            $stats = Test-ComfyApi
            if ($stats) { $healthy = $true; break }
            Start-Sleep -Seconds 2
        }
    } finally {
        if (-not $healthy) {
            Write-Fail 'ComfyUI n''a pas repondu — arret de l''instance lancee'
            & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'stop.ps1') | Out-Null
            if (Test-Path -LiteralPath $errLog) {
                Write-Host '    --- fin du journal ---' -ForegroundColor DarkGray
                Get-Content -LiteralPath $errLog -Tail 25 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            }
            $problems.Add('ComfyUI ne demarre pas')
        }
    }
    if ($healthy) { Write-Ok "API disponible en $([int]((Get-Date) - $started.StartTime).TotalSeconds) s" }
}

# 6. Controle de sante --------------------------------------------------------------------
if ($stats) {
    Write-Step 'Controle de sante'
    $dev = $stats.devices | Select-Object -First 1
    $isGpu = $dev.type -eq 'cuda' -and $dev.name -match 'AMD|Radeon|NVIDIA'
    if ($isGpu) { Write-Ok ("backend GPU : {0} — VRAM libre {1} / {2}" -f $dev.name, (Format-Bytes $dev.vram_free), (Format-Bytes $dev.vram_total)) }
    else { Write-Fail "backend NON GPU : $($dev.type) $($dev.name)"; $problems.Add('ComfyUI tourne sur CPU') }
    Write-Ok ("ComfyUI {0}, PyTorch {1}, Python {2}" -f $stats.system.comfyui_version, $stats.system.pytorch_version, ($stats.system.python_version -split ' ')[0])
    try {
        $unet = (Invoke-VaiApi $cfg '/object_info/UNETLoader' 10).UNETLoader.input.required.unet_name[0]
        Write-Ok ("modeles de diffusion visibles par ComfyUI : {0}" -f $(if ($unet) { $unet -join ', ' } else { '(aucun)' }))
    } catch { Write-WarnL "object_info indisponible : $($_.Exception.Message)" }
}

# 7. Resume ---------------------------------------------------------------------------------
Write-Host ''
Write-Host '──────────────────────────────────────────────────────────' -ForegroundColor DarkGray
if ($stats) { Write-Host "  Interface / API : $($cfg.url)" -ForegroundColor Green }
Write-Host "  Modeles         : $($P.models)"
Write-Host "  Projets         : $($P.projects)"
Write-Host "  Journaux        : $($P.logs)"
foreach ($g in $modelState.Keys) { Write-Host ("  {0,-16}: {1}" -f $g, $(if ($modelState[$g]) { 'pret' } else { 'a telecharger' })) }
Write-Host '  Arret           : pwsh -File video-ai\scripts\stop.ps1'
Write-Host '  Tests           : pwsh -File video-ai\tests\run-tests.ps1'
if ($problems.Count) {
    Write-Host ("  PROBLEMES       : {0}" -f ($problems -join ' | ')) -ForegroundColor Red
} else { Write-Host '  Etat            : pret' -ForegroundColor Green }
Write-Host '──────────────────────────────────────────────────────────' -ForegroundColor DarkGray
Write-VaiLog ("resume : problems=[{0}]" -f ($problems -join '; '))
if ($Open -and $stats) { Start-Process $cfg.url }
exit $(if ($problems.Count) { 1 } else { 0 })
