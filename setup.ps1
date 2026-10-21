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
    [switch]$SkipFeatures,
    [switch]$SkipApps,
    [switch]$SkipConfigs,
    [switch]$SkipExtensions,
    [switch]$SkipWsl,
    [switch]$SkipKeePass,
    [switch]$SkipClone,
    [switch]$SkipSsh,
    [switch]$SkipVideoAI,
    # Telecharge aussi les poids des modeles video-ai (~31 Go). Desactive par defaut.
    [switch]$VideoAIModels,
    [switch]$SkipAudioAI,
    # Ajoute BS-Roformer (python-audio-separator) a audio-ai. Desactive par defaut.
    [switch]$AudioAIRoformer,
    # Source du coffre KeePass a restaurer sur le Bureau (jamais versionne dans git).
    [string]$KeePassSource = 'D:\Archive\KeePass\Database.kdbx',
    # Source de la config SSH (cles + config + known_hosts) a restaurer dans ~/.ssh.
    # JAMAIS versionnee dans git : elle vit sur le disque conserve au reset.
    [string]$SshSource = 'D:\Archive\ssh'
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

function Write-Step  { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Skip  { param($m) Write-Host "    [--] $m" -ForegroundColor DarkGray }
function Write-WarnL { param($m) Write-Host "    [!!] $m" -ForegroundColor Yellow }

function Test-IsAdmin {
    return ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------------------
# 0. Fonctionnalites Windows 11 (WSL) — a faire en premier sur une machine neuve
# ---------------------------------------------------------------------------
function Enable-WindowsFeatures {
    Write-Step 'Fonctionnalites Windows (WSL / Virtual Machine Platform)'
    if (-not (Test-IsAdmin)) {
        Write-WarnL 'droits admin requis pour activer WSL. Relancez PowerShell "en tant qu''administrateur".'
        return
    }
    $features = 'Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform'
    $rebootNeeded = $false
    # CIM + dism.exe plutot que le module DISM : sous pwsh (paquet MSIX),
    # Get-WindowsOptionalFeature echoue avec "Classe non enregistree".
    foreach ($f in $features) {
        $state = (Get-CimInstance Win32_OptionalFeature -Filter "Name='$f'" -ErrorAction SilentlyContinue).InstallState
        if ($state -eq 1) { Write-Skip "$f deja active"; continue }
        Write-Host "    activation de $f ..." -ForegroundColor White
        dism.exe /online /enable-feature /featurename:$f /all /norestart /quiet | Out-Null
        # 3010 = succes, redemarrage requis
        if ($LASTEXITCODE -eq 3010) { $rebootNeeded = $true }
        elseif ($LASTEXITCODE -ne 0) { Write-WarnL "$f : echec dism (code $LASTEXITCODE)"; continue }
        Write-Ok "$f active"
    }
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        wsl.exe --set-default-version 2 2>&1 | Out-Null
        Write-Ok 'WSL version par defaut = 2'
    }
    if ($rebootNeeded) {
        Write-WarnL 'REDEMARRAGE requis pour finir l''activation de WSL, puis relancez setup.ps1.'
    }
}

# ---------------------------------------------------------------------------
# 1. Applications via winget
# ---------------------------------------------------------------------------
# ID winget -> nom lisible. Ces IDs correspondent aux applications reellement
# installees sur la machine d'origine.
$WingetApps = [ordered]@{
    'Google.AndroidStudio'          = 'Android Studio'
    'Anthropic.Claude'              = 'Claude (desktop)'
    'Microsoft.VisualStudioCode'    = 'VS Code'
    'GitHub.GitHubDesktop'          = 'GitHub Desktop'
    'Brave.Brave'                   = 'Brave'
    'Google.Chrome'                 = 'Google Chrome'
    'Docker.DockerDesktop'          = 'Docker Desktop'
    'Python.Python.3.14'            = 'Python 3.14'
    'CoreyButler.NVMforWindows'     = 'nvm-windows'
    'Microsoft.PowerShell'          = 'PowerShell 7'
    'Microsoft.PowerShell.Preview'  = 'PowerShell 7 (preview)'
    'JanDeDobbeleer.OhMyPosh'       = 'Oh My Posh'
    'Git.Git'                       = 'Git'
    'OpenJS.NodeJS'                 = 'Node.js'
    'Microsoft.WindowsTerminal'     = 'Windows Terminal'
    'Warp.Warp'                     = 'Warp'
    'GitHub.cli'                    = 'GitHub CLI'
    '7zip.7zip'                     = '7-Zip'
    'BlenderFoundation.Blender'     = 'Blender'
    'Unity.UnityHub'                = 'Unity Hub'
    'DEVCOM.JetBrainsMonoNerdFont'  = 'JetBrainsMono Nerd Font'
    'Gyan.FFmpeg'                   = 'FFmpeg'
    'astral-sh.uv'                  = 'uv (venv video-ai)'
    'yt-dlp.yt-dlp'                 = 'yt-dlp'
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
$PsModules = @('posh-git', 'Terminal-Icons', 'z', 'DockerCompletion', 'npm-completion',
                'Microsoft.WinGet.CommandNotFound')

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
        Write-WarnL 'wsl introuvable. Activez d''abord les fonctionnalites (setup.ps1 en admin), redemarrez, puis relancez.'
        return
    }
    $distros = (wsl.exe -l -q) -replace "`0", ''
    if ($distros -notmatch 'kali-linux') {
        Write-Host "    installation de la distribution kali-linux ..." -ForegroundColor White
        wsl.exe --install -d kali-linux --no-launch 2>&1 | Out-Null
        Write-Ok 'kali-linux installee'
        Write-WarnL 'premiere ouverture de kali-linux requise pour creer l''utilisateur, puis relancez setup.ps1 pour les dotfiles.'
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
# 6. Warp : charger launch_config.yaml au demarrage de session
# ---------------------------------------------------------------------------
# Reproduit le comportement deja en place : Warp est ouvert avec l'URI
# warp://launch/launch_config.yaml a l'ouverture de session, via un raccourci
# dans le dossier Demarrage de l'utilisateur (visible, supprimable a la main).
function Set-WarpAutostart {
    Write-Step 'Warp — chargement de launch_config.yaml au demarrage'
    $warpExe = Join-Path $env:LOCALAPPDATA 'Programs\Warp\warp.exe'
    if (-not (Test-Path $warpExe)) {
        Write-WarnL "warp.exe introuvable ($warpExe) — relancez cette etape apres l'installation de Warp."
        return
    }
    $startup = [Environment]::GetFolderPath('Startup')
    $lnk = Join-Path $startup 'Warp (launch config).lnk'
    if (Test-Path $lnk) { Write-Skip 'raccourci de demarrage Warp deja present'; return }
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($lnk)
    $sc.TargetPath = $warpExe
    $sc.Arguments  = 'warp://launch/launch_config.yaml'
    $sc.Save()
    Write-Ok "raccourci cree : $lnk"
}

# ---------------------------------------------------------------------------
# 7. Restaurer un fichier personnel sur le Bureau s'il est absent
# ---------------------------------------------------------------------------
# Le fichier n'est PAS dans ce depot (jamais versionne) : il est simplement
# recopie depuis le disque d'archive conserve au reset, s'il manque au Bureau.
function Restore-DesktopFile {
    Write-Step 'Restauration du fichier personnel sur le Bureau'
    $dest = Join-Path ([Environment]::GetFolderPath('Desktop')) (Split-Path $KeePassSource -Leaf)
    if (Test-Path $dest) { Write-Skip "deja present : $dest"; return }
    if (-not (Test-Path $KeePassSource)) {
        Write-WarnL "source introuvable ($KeePassSource) — a recopier manuellement sur le Bureau."
        return
    }
    Copy-Item $KeePassSource $dest -Force
    Write-Ok "restaure : $dest"
}

# ---------------------------------------------------------------------------
# 8. Cloner les depots des onglets Warp (idempotent, via gh)
# ---------------------------------------------------------------------------
# Lit windows/repos.txt (slugs owner/repo) et clone chacun dans
# Documents\GitHub\<repo> s'il est absent. Necessite `gh auth login` au prealable.
function Clone-Repos {
    Write-Step 'Clonage des depots (onglets Warp)'
    $listFile = "$Root\windows\repos.txt"
    if (-not (Test-Path $listFile)) { Write-Skip 'aucune liste de depots'; return }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-WarnL 'gh introuvable — installez GitHub CLI puis relancez.'
        return
    }
    # Code de retour plutot que le texte : "not logged into" contient aussi "logged in".
    gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-WarnL 'gh non authentifie : lancez "gh auth login" puis relancez cette etape.'
        return
    }
    $base = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'GitHub'
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    foreach ($line in (Get-Content $listFile | Where-Object { $_.Trim() -and $_ -notmatch '^\s*#' })) {
        $slug = $line.Trim()
        $name = $slug.Split('/')[-1]
        $dest = Join-Path $base $name
        if (Test-Path (Join-Path $dest '.git')) { Write-Skip "$name deja clone"; continue }
        Write-Host "    gh repo clone $slug ..." -ForegroundColor White
        gh repo clone $slug $dest 2>&1 | Out-Null
        if (Test-Path (Join-Path $dest '.git')) { Write-Ok "$name clone" }
        else { Write-WarnL "$name : clonage non confirme (acces au depot ?)" }
    }
}

# ---------------------------------------------------------------------------
# 9. SSH : restaurer ~/.ssh (cles, config d'acces serveur, known_hosts)
# ---------------------------------------------------------------------------
# Les cles privees et la config NE SONT PAS dans ce depot. Elles sont recopiees
# depuis le disque conserve au reset ($SshSource), puis les permissions sont
# verrouillees sur l'utilisateur courant. Le dossier de quarantaine COMPROMIS*
# (anciennes cles revoquees) est explicitement ignore.
function Restore-SshKeys {
    Write-Step 'SSH — cles et config d''acces serveur'
    $sshDir = Join-Path $env:USERPROFILE '.ssh'
    if (Test-Path (Join-Path $sshDir 'id_ed25519')) {
        Write-Skip 'cle privee deja presente dans ~/.ssh'
        return
    }
    if (-not (Test-Path $SshSource)) {
        Write-WarnL "source SSH introuvable ($SshSource) — placez-y votre sauvegarde ~/.ssh (hors COMPROMIS*)."
        return
    }
    New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
    Get-ChildItem $SshSource -Force | Where-Object { $_.Name -notlike 'COMPROMIS*' } | ForEach-Object {
        $target = Join-Path $sshDir $_.Name
        Copy-Item $_.FullName $target -Recurse -Force
        Write-Ok "restaure : ~/.ssh\$($_.Name)"
    }
    # Verrouiller les permissions : retirer l'heritage, n'autoriser que l'utilisateur.
    icacls $sshDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" 2>&1 | Out-Null
    Write-Ok 'permissions ~/.ssh verrouillees sur l''utilisateur'
    Write-Host "    (la config SSH restauree porte les hotes d'acces a ton serveur)" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 10. video-ai : ComfyUI + PyTorch ROCm (RX 6900 XT) dans un venv dedie
# ---------------------------------------------------------------------------
# Delegue a video-ai\scripts\install.ps1 (idempotent, n'affecte aucun autre Python).
# Les modeles (~31 Go) ne sont telecharges qu'avec -VideoAIModels.
function Install-VideoAI {
    Write-Step 'video-ai (ComfyUI, PyTorch ROCm gfx103X)'
    # uv/pwsh fraichement installes par winget ne sont pas encore dans le PATH de cette session.
    # On complete le PATH sans le remplacer (il peut contenir des entrees propres a la session).
    $env:Path = (@($env:Path -split ';') + ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';') +
                 ([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') | Where-Object { $_ } | Select-Object -Unique) -join ';'
    if (-not (Get-Command pwsh -ErrorAction SilentlyContinue)) { Write-WarnL 'pwsh introuvable — relancez apres installation de PowerShell 7.'; return }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-WarnL 'uv introuvable (pip install uv / winget astral-sh.uv) — etape ignoree.'; return }
    & pwsh -NoProfile -ExecutionPolicy Bypass -File "$Root\video-ai\scripts\install.ps1"
    if ($LASTEXITCODE) { Write-WarnL 'installation video-ai incomplete (voir les logs dans <home>\logs)'; return }
    if ($VideoAIModels) { & pwsh -NoProfile -ExecutionPolicy Bypass -File "$Root\video-ai\scripts\download-models.ps1" }
    else { Write-Skip 'modeles non telecharges (option -VideoAIModels, ou video-ai\scripts\download-models.ps1)' }
}

# ---------------------------------------------------------------------------
# 11. audio-ai : separation de stems (Demucs v4), BPM/tonalite, mastering LUFS, serveur MCP
# ---------------------------------------------------------------------------
# Delegue a audio-ai\scripts\install.ps1 (idempotent, venv dedie E:\audio-ai). Reprend le build
# PyTorch ROCm et le wheelhouse de video-ai, puis enregistre le serveur MCP dans Claude Desktop.
function Install-AudioAI {
    Write-Step 'audio-ai (Demucs v4 sur GPU AMD, mastering, serveur MCP)'
    $env:Path = (@($env:Path -split ';') + ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';') +
                 ([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') | Where-Object { $_ } | Select-Object -Unique) -join ';'
    foreach ($t in 'pwsh','uv','ffmpeg') {
        if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { Write-WarnL "$t introuvable — etape audio-ai ignoree."; return }
    }
    $extra = @(); if ($AudioAIRoformer) { $extra += '-WithRoformer' }
    & pwsh -NoProfile -ExecutionPolicy Bypass -File "$Root\audio-ai\scripts\install.ps1" @extra
    if ($LASTEXITCODE) { Write-WarnL 'installation audio-ai incomplete (voir E:\audio-ai\logs)' }
}

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
Write-Host "Auto-setup machine — depot infra" -ForegroundColor Magenta
Write-Host "Racine : $Root" -ForegroundColor DarkGray

if (-not $SkipFeatures)   { Enable-WindowsFeatures }                  else { Write-Skip 'fonctionnalites Windows ignorees (-SkipFeatures)' }
if (-not $SkipApps)       { Install-WingetApps; Install-NpmGlobals; Install-PsModules; Install-VSCodeExtensions } else { Write-Skip 'applications ignorees (-SkipApps)' }
if (-not $SkipConfigs)    { Deploy-Configs; Set-WarpAutostart }       else { Write-Skip 'configs ignorees (-SkipConfigs)' }
if (-not $SkipExtensions) { Set-ExtensionForcelist }                  else { Write-Skip 'extensions ignorees (-SkipExtensions)' }
if (-not $SkipWsl)        { Invoke-KaliSetup }                        else { Write-Skip 'WSL ignore (-SkipWsl)' }
if (-not $SkipKeePass)    { Restore-DesktopFile }                     else { Write-Skip 'restauration Bureau ignoree (-SkipKeePass)' }
if (-not $SkipClone)      { Clone-Repos }                             else { Write-Skip 'clonage des depots ignore (-SkipClone)' }
if (-not $SkipSsh)        { Restore-SshKeys }                         else { Write-Skip 'restauration SSH ignoree (-SkipSsh)' }
if (-not $SkipVideoAI)    { Install-VideoAI }                         else { Write-Skip 'video-ai ignore (-SkipVideoAI)' }
if (-not $SkipAudioAI)    { Install-AudioAI }                         else { Write-Skip 'audio-ai ignore (-SkipAudioAI)' }

Write-Host "`nTermine." -ForegroundColor Magenta
