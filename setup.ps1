#Requires -Version 5.1
<#
.SYNOPSIS
    Auto-setup idempotent de la machine (Windows) : applications, PowerShell, Warp,
    extensions Chromium et environnement WSL/Kali.

.DESCRIPTION
    Rejouable sans risque : chaque etape verifie l'etat avant d'agir.
    - Installe/verifie les applications via winget
    - Installe les outils CLI globaux via npm (Claude Code, opencode)
    - Deploie le profil PowerShell et la config Warp depuis ce depot
    - Force l'extension "Claude in Chrome" sur tous les navigateurs Chromium
    - Lance le setup Kali (wsl/kali/setup.sh) si WSL/Kali est present

    Aucun secret n'est contenu ni requis par ce script.

.EXAMPLE
    pwsh -ExecutionPolicy Bypass -File .\setup.ps1
    pwsh -File .\setup.ps1 -SkipWsl
#>
[CmdletBinding()]
param(
    [switch]$SkipApps,
    [switch]$SkipConfigs,
    [switch]$SkipExtensions,
    [switch]$SkipWsl
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

function Write-Step  { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Skip  { param($m) Write-Host "    [--] $m" -ForegroundColor DarkGray }
function Write-WarnL { param($m) Write-Host "    [!!] $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 1. Applications via winget
# ---------------------------------------------------------------------------
# ID winget -> nom lisible. Ces IDs correspondent aux applications reellement
# installees sur la machine d'origine.
$WingetApps = [ordered]@{
    'Google.AndroidStudio'          = 'Android Studio'
    'Anthropic.Claude'              = 'Claude (desktop)'
    'Microsoft.VisualStudioCode'    = 'VS Code'
    'Brave.Brave'                   = 'Brave'
    'Google.Chrome'                 = 'Google Chrome'
    'Docker.DockerDesktop'          = 'Docker Desktop'
    'Microsoft.PowerShell'          = 'PowerShell 7'
    'JanDeDobbeleer.OhMyPosh'       = 'Oh My Posh'
    'Git.Git'                       = 'Git'
    'OpenJS.NodeJS'                 = 'Node.js'
    'Microsoft.WindowsTerminal'     = 'Windows Terminal'
    'Warp.Warp'                     = 'Warp'
    'GitHub.cli'                    = 'GitHub CLI'
}

function Test-WingetInstalled {
    param([string]$Id)
    $out = winget list --id $Id --exact --accept-source-agreements 2>$null | Out-String
    return ($out -match [regex]::Escape($Id))
}

function Install-WingetApps {
    Write-Step 'Applications (winget)'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-WarnL 'winget introuvable. Installez "App Installer" depuis le Microsoft Store, puis relancez.'
        return
    }
    foreach ($id in $WingetApps.Keys) {
        $name = $WingetApps[$id]
        if (Test-WingetInstalled -Id $id) {
            Write-Skip "$name deja installe ($id)"
            continue
        }
        Write-Host "    installation de $name ..." -ForegroundColor White
        winget install --id $id --exact --silent `
            --accept-package-agreements --accept-source-agreements `
            --disable-interactivity 2>&1 | Out-Null
        if (Test-WingetInstalled -Id $id) { Write-Ok "$name installe" }
        else { Write-WarnL "$name : installation non confirmee ($id)" }
    }
}

# ---------------------------------------------------------------------------
# 2. Outils CLI globaux via npm
# ---------------------------------------------------------------------------
$NpmGlobals = [ordered]@{
    '@anthropic-ai/claude-code' = 'Claude Code'
    'opencode-ai'               = 'opencode'
}

# Modules PowerShell (PSGallery) requis par le profil.
$PsModules = @('posh-git', 'Terminal-Icons', 'z', 'DockerCompletion', 'npm-completion')

function Install-NpmGlobals {
    Write-Step 'Outils CLI globaux (npm)'
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-WarnL 'npm introuvable (Node.js pas encore dans le PATH ?). Rouvrez un terminal puis relancez.'
        return
    }
    $installed = (npm ls -g --depth=0 2>$null | Out-String)
    foreach ($pkg in $NpmGlobals.Keys) {
        $name = $NpmGlobals[$pkg]
        if ($installed -match [regex]::Escape($pkg)) {
            Write-Skip "$name deja installe ($pkg)"
            continue
        }
        Write-Host "    npm i -g $pkg ..." -ForegroundColor White
        npm install -g $pkg 2>&1 | Out-Null
        Write-Ok "$name installe"
    }
}

function Install-PsModules {
    Write-Step 'Modules PowerShell (PSGallery)'
    foreach ($m in $PsModules) {
        if (Get-Module -ListAvailable -Name $m) {
            Write-Skip "$m deja installe"
            continue
        }
        Write-Host "    Install-Module $m ..." -ForegroundColor White
        Install-Module $m -Scope CurrentUser -Force -AcceptLicense -ErrorAction Stop
        Write-Ok "$m installe"
    }
}

function Install-VSCodeExtensions {
    Write-Step 'Extensions VS Code'
    if (-not (Get-Command code -ErrorAction SilentlyContinue)) {
        Write-WarnL 'commande "code" introuvable (VS Code pas dans le PATH ?). Rouvrez un terminal puis relancez.'
        return
    }
    $listFile = "$Root\windows\vscode\extensions.txt"
    if (-not (Test-Path $listFile)) { Write-Skip 'aucune liste d''extensions'; return }
    $installed = (code --list-extensions 2>$null)
    foreach ($ext in (Get-Content $listFile | Where-Object { $_.Trim() })) {
        if ($installed -contains $ext) { Write-Skip "$ext deja installee"; continue }
        Write-Host "    code --install-extension $ext ..." -ForegroundColor White
        code --install-extension $ext 2>&1 | Out-Null
        Write-Ok "$ext installee"
    }
}

# ---------------------------------------------------------------------------
# 3. Deploiement des configs (PowerShell + Warp)
# ---------------------------------------------------------------------------
function Copy-IfDifferent {
    param([string]$Src, [string]$Dst)
    if (-not (Test-Path $Src)) { Write-WarnL "source absente : $Src"; return }
    New-Item -ItemType Directory -Force -Path (Split-Path $Dst) | Out-Null
    if ((Test-Path $Dst) -and ((Get-FileHash $Src).Hash -eq (Get-FileHash $Dst).Hash)) {
        Write-Skip "a jour : $Dst"
        return
    }
    if (Test-Path $Dst) {
        Copy-Item $Dst "$Dst.bak-$(Get-Date -Format yyyyMMddHHmmss)" -Force
    }
    Copy-Item $Src $Dst -Force
    Write-Ok "deploye : $Dst"
}

function Deploy-Configs {
    Write-Step 'Configurations (PowerShell + Warp)'

    # PowerShell 7 ($PROFILE pointe vers Documents\PowerShell\Microsoft.PowerShell_profile.ps1)
    $psDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'PowerShell'
    Copy-IfDifferent "$Root\windows\powershell\Microsoft.PowerShell_profile.ps1" "$psDir\Microsoft.PowerShell_profile.ps1"
    Copy-IfDifferent "$Root\windows\powershell\powershell.config.json"           "$psDir\powershell.config.json"

    # Warp (settings sous LOCALAPPDATA, data sous APPDATA/Roaming)
    $warpLocal = Join-Path $env:LOCALAPPDATA 'warp\Warp\config'
    $warpData  = Join-Path $env:APPDATA      'warp\Warp\data'
    Copy-IfDifferent "$Root\windows\warp\settings.toml"                                   "$warpLocal\settings.toml"
    Copy-IfDifferent "$Root\windows\warp\launch_configurations\launch_config.yaml"        "$warpData\launch_configurations\launch_config.yaml"
    Copy-IfDifferent "$Root\windows\warp\tab_configs\my_tab_config.toml"                  "$warpData\tab_configs\my_tab_config.toml"
    Copy-IfDifferent "$Root\windows\warp\default_tab_configs\worktree.toml"               "$warpData\default_tab_configs\worktree.toml"

    # Windows Terminal
    $wtState = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState'
    if (Test-Path $wtState) {
        Copy-IfDifferent "$Root\windows\windows-terminal\settings.json" "$wtState\settings.json"
    } else {
        Write-Skip 'Windows Terminal non installe (store) — settings non deploye'
    }

    # VS Code (settings + keybindings)
    $vscUser = Join-Path $env:APPDATA 'Code\User'
    Copy-IfDifferent "$Root\windows\vscode\settings.json"    "$vscUser\settings.json"
    if (Test-Path "$Root\windows\vscode\keybindings.json") {
        Copy-IfDifferent "$Root\windows\vscode\keybindings.json" "$vscUser\keybindings.json"
    }
}

# ---------------------------------------------------------------------------
# 4. Extension "Claude in Chrome" sur tous les navigateurs Chromium
# ---------------------------------------------------------------------------
# ID detecte sur la machine d'origine (extension "Claude").
$ClaudeExtId = 'fcoeoabgfenejglbffodgkkbkcdhcgfn'
$ChromiumPolicyPaths = @(
    'SOFTWARE\Policies\Google\Chrome',
    'SOFTWARE\Policies\BraveSoftware\Brave',
    'SOFTWARE\Policies\Microsoft\Edge'
)

function Set-ExtensionForcelist {
    Write-Step 'Extension Claude in Chrome (policy Chromium)'
    $isAdmin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $hive = if ($isAdmin) { 'HKLM' } else { 'HKCU' }
    if (-not $isAdmin) { Write-WarnL 'pas admin : policy ecrite sous HKCU (peut etre ignoree selon la version du navigateur)' }

    $value = "$ClaudeExtId;https://clients2.google.com/service/update2/crx"
    foreach ($p in $ChromiumPolicyPaths) {
        $key = "${hive}:\$p\ExtensionInstallForcelist"
        New-Item -Path $key -Force | Out-Null
        # trouve le prochain index libre ou reutilise un index pointant deja sur l'ext
        $existing = Get-Item $key | Select-Object -ExpandProperty Property
        $already = $false
        foreach ($name in $existing) {
            if ((Get-ItemProperty $key -Name $name).$name -like "$ClaudeExtId;*") { $already = $true; break }
        }
        if ($already) { Write-Skip "deja force : $p"; continue }
        $idx = 1
        while ($existing -contains "$idx") { $idx++ }
        New-ItemProperty -Path $key -Name "$idx" -Value $value -PropertyType String -Force | Out-Null
        Write-Ok "force sur $p (index $idx)"
    }
    Write-Host "    (redemarrez les navigateurs pour que la policy prenne effet)" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 5. WSL / Kali
# ---------------------------------------------------------------------------
function Invoke-KaliSetup {
    Write-Step 'WSL / Kali Linux'
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Write-WarnL 'wsl introuvable. Installez WSL (wsl --install) puis la distribution kali-linux.'
        return
    }
    $distros = (wsl.exe -l -q) -replace "`0", ''
    if ($distros -notmatch 'kali-linux') {
        Write-WarnL "distribution kali-linux absente. Installez-la : wsl --install -d kali-linux"
        return
    }
    # chemin du setup.sh traduit vers /mnt/<lecteur>/...
    $sh = Join-Path $Root 'wsl\kali\setup.sh'
    $drive = ($sh.Substring(0,1)).ToLower()
    $wslPath = "/mnt/$drive" + ($sh.Substring(2) -replace '\\','/')
    Write-Host "    execution de $wslPath dans kali-linux ..." -ForegroundColor White
    wsl.exe -d kali-linux -- bash "$wslPath"
    Write-Ok 'setup Kali termine'
}

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
Write-Host "Auto-setup machine — depot infra" -ForegroundColor Magenta
Write-Host "Racine : $Root" -ForegroundColor DarkGray

if (-not $SkipApps)       { Install-WingetApps; Install-NpmGlobals; Install-PsModules; Install-VSCodeExtensions } else { Write-Skip 'applications ignorees (-SkipApps)' }
if (-not $SkipConfigs)    { Deploy-Configs }                          else { Write-Skip 'configs ignorees (-SkipConfigs)' }
if (-not $SkipExtensions) { Set-ExtensionForcelist }                  else { Write-Skip 'extensions ignorees (-SkipExtensions)' }
if (-not $SkipWsl)        { Invoke-KaliSetup }                        else { Write-Skip 'WSL ignore (-SkipWsl)' }

Write-Host "`nTermine." -ForegroundColor Magenta
