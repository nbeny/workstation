#Requires -Version 7
# Fonctions partagees par les scripts video-ai (a dot-sourcer : . "$PSScriptRoot\common.ps1").
# Aucun secret n'est lu ni ecrit ici.

$ErrorActionPreference = 'Stop'
$script:VaiRepo = Split-Path $PSScriptRoot -Parent          # ...\workstation\video-ai

function Write-Step  { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan;   Write-VaiLog "STEP $m" }
function Write-Ok    { param($m) Write-Host "    [ok] $m" -ForegroundColor Green; Write-VaiLog "OK   $m" }
function Write-Skip  { param($m) Write-Host "    [--] $m" -ForegroundColor DarkGray; Write-VaiLog "SKIP $m" }
function Write-WarnL { param($m) Write-Host "    [!!] $m" -ForegroundColor Yellow; Write-VaiLog "WARN $m" }
function Write-Fail  { param($m) Write-Host "    [KO] $m" -ForegroundColor Red;    Write-VaiLog "FAIL $m" }

function Write-VaiLog {
    param([string]$Message)
    if (-not $script:VaiLogFile) { return }
    try {
        $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        Add-Content -LiteralPath $script:VaiLogFile -Value $line -Encoding utf8
    } catch { }
}

function Merge-Hashtable {
    param([hashtable]$Base, [hashtable]$Over)
    foreach ($k in $Over.Keys) {
        if ($Base[$k] -is [hashtable] -and $Over[$k] -is [hashtable]) { Merge-Hashtable $Base[$k] $Over[$k] | Out-Null }
        else { $Base[$k] = $Over[$k] }
    }
    return $Base
}

# Config effective : config/video-ai.json < config/local.json (non versionne) < variables d'environnement.
function Get-VaiConfig {
    $cfg = Get-Content -LiteralPath (Join-Path $script:VaiRepo 'config\video-ai.json') -Raw | ConvertFrom-Json -AsHashtable
    $local = Join-Path $script:VaiRepo 'config\local.json'
    if (Test-Path -LiteralPath $local) {
        $cfg = Merge-Hashtable $cfg (Get-Content -LiteralPath $local -Raw | ConvertFrom-Json -AsHashtable)
    }
    if ($env:VIDEO_AI_HOME)   { $cfg.home = $env:VIDEO_AI_HOME }
    if ($env:VIDEO_AI_MODELS) { $cfg.models_dir = $env:VIDEO_AI_MODELS }
    if ($env:VIDEO_AI_PORT)   { $cfg.port = [int]$env:VIDEO_AI_PORT }

    $h = $cfg.home
    $cfg.paths = [ordered]@{
        home      = $h
        comfyui   = Join-Path $h 'ComfyUI'
        venv      = Join-Path $h 'venv'
        python    = Join-Path $h 'venv\Scripts\python.exe'
        models    = $(if ($cfg.models_dir) { $cfg.models_dir } else { Join-Path $h 'models' })
        wheelhouse= Join-Path $h 'wheelhouse'
        input     = Join-Path $h 'comfy-io\input'
        output    = Join-Path $h 'comfy-io\output'
        user      = Join-Path $h 'comfy-io\user'
        temp      = Join-Path $h 'comfy-io\temp'
        projects  = Join-Path $h 'projects'
        assets    = Join-Path $h 'assets'
        logs      = Join-Path $h 'logs'
        run       = Join-Path $h 'run'
        cache     = Join-Path $h 'cache'
        extra_yaml= Join-Path $h 'run\extra_model_paths.yaml'
        pidfile   = Join-Path $h 'run\comfyui.pid.json'
    }
    $cfg.url = "http://$($cfg.host):$($cfg.port)"
    return $cfg
}

function Initialize-VaiLog {
    param([hashtable]$Cfg, [string]$Name)
    New-Item -ItemType Directory -Force -Path $Cfg.paths.logs | Out-Null
    $script:VaiLogFile = Join-Path $Cfg.paths.logs ("{0}-{1}.log" -f $Name, (Get-Date -Format 'yyyyMMdd'))
    Write-VaiLog "---- $Name (pid $PID) ----"
}

function Test-TcpPortListening {
    param([string]$HostName, [int]$Port)
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$c
}

function Get-PortOwner {
    param([int]$Port)
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) { return $null }
    return Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
}

function Invoke-VaiApi {
    param([hashtable]$Cfg, [string]$Path, [int]$TimeoutSec = 5)
    return Invoke-RestMethod -Uri ($Cfg.url + $Path) -TimeoutSec $TimeoutSec -ErrorAction Stop
}

# Processus ComfyUI demarres PAR CE WORKSPACE : le lanceur python.exe du venv (uv) ET l'interpreteur
# reel qu'il engendre. Chacun est identifie par PID + heure de demarrage (ticks) dans run\comfyui.pid.json :
# un PID recycle par un autre processus n'est jamais reconnu.
function Test-ProcMatch {
    param($ProcId, $Ticks)
    if (-not $ProcId) { return $null }
    $p = Get-Process -Id $ProcId -ErrorAction SilentlyContinue
    # Ticks (entier) : ConvertFrom-Json convertirait une date ISO en DateTime.
    if ($p -and $p.StartTime.ToUniversalTime().Ticks -eq [int64]$Ticks) { return $p }
    return $null
}

function Get-VaiOwnedProcesses {
    param([hashtable]$Cfg)
    $pf = $Cfg.paths.pidfile
    if (-not (Test-Path -LiteralPath $pf)) { return @() }
    try { $info = Get-Content -LiteralPath $pf -Raw | ConvertFrom-Json } catch { return @() }
    return @($info.procs | ForEach-Object { Test-ProcMatch $_.pid $_.ticks }) | Where-Object { $_ }
}

function Get-VaiOwnedProcess {
    param([hashtable]$Cfg)
    return Get-VaiOwnedProcesses $Cfg | Select-Object -First 1
}

function Get-ModelManifest {
    return Get-Content -LiteralPath (Join-Path $script:VaiRepo 'config\models.json') -Raw | ConvertFrom-Json -AsHashtable
}

# Etat rapide (sans hash) d'un groupe de modeles : present = taille exacte.
function Get-ModelGroupStatus {
    param([hashtable]$Cfg, [string]$Group)
    $m = Get-ModelManifest
    $res = foreach ($f in $m.groups[$Group].files) {
        $p = Join-Path (Join-Path $Cfg.paths.models $f.folder) $f.name
        $ok = (Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p).Length -eq [int64]$f.size)
        [pscustomobject]@{ name = $f.name; folder = $f.folder; path = $p; present = $ok; size = [int64]$f.size }
    }
    return $res
}

function Format-Bytes { param([int64]$b) if ($b -ge 1GB) { '{0:N1} Go' -f ($b/1GB) } else { '{0:N0} Mo' -f ($b/1MB) } }
