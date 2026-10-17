#Requires -Version 7
<#
.SYNOPSIS
    Installe / verifie audio-ai : venv Python dedie (PyTorch ROCm gfx103X), Demucs v4, librosa, pyloudnorm,
    serveur MCP, puis l'enregistre dans Claude Desktop (et Claude Code si la CLI est presente).

.DESCRIPTION
    Idempotent. Tout vit dans <home> (defaut E:\audio-ai, variable AUDIO_AI_HOME) ; aucun autre Python touche.
    - PyTorch : MEME build ROCm que video-ai (video-ai/config/video-ai.json -> torch), depuis son wheelhouse
    - dependances audio installees SANS laisser pip remplacer torch (contraintes)
    - controle Smart App Control des binaires du venv (vai.saccheck)
    - poids Demucs (~320 Mo pour htdemucs_ft) dans <home>\models\torch
    - selftest reel sur le GPU (BPM, tonalite, Rubber Band, mastering, separation Demucs)
    - fusion de l'entree "audio-ai" dans claude_desktop_config.json (sauvegarde .bak-<horodatage>)

.EXAMPLE
    pwsh -File audio-ai\scripts\install.ps1
    pwsh -File audio-ai\scripts\install.ps1 -WithRoformer     # + BS-Roformer (python-audio-separator)
    pwsh -File audio-ai\scripts\install.ps1 -NoMcp -SkipTest
#>
[CmdletBinding()]
param([switch]$WithRoformer, [switch]$NoMcp, [switch]$NoModels, [switch]$SkipTest, [switch]$CheckSAC)

# Fonctions partagees de video-ai : Write-Step/Ok/Skip/WarnL/Fail, Get-VaiConfig (build torch + wheelhouse).
. "$PSScriptRoot\..\..\video-ai\scripts\common.ps1"
$AudioRepo = Split-Path $PSScriptRoot -Parent
$acfg = Get-Content -LiteralPath (Join-Path $AudioRepo 'config\audio-ai.json') -Raw | ConvertFrom-Json -AsHashtable
$vcfg = Get-VaiConfig
$AHome = if ($env:AUDIO_AI_HOME) { $env:AUDIO_AI_HOME } else { $acfg.home }
$A = [ordered]@{
    venv = Join-Path $AHome 'venv'; python = Join-Path $AHome 'venv\Scripts\python.exe'
    logs = Join-Path $AHome 'logs'; run = Join-Path $AHome 'run'; projects = Join-Path $AHome 'projects'
    models = Join-Path $AHome 'models'; miopen = Join-Path $AHome 'cache\miopen'
}
foreach ($d in 'logs','run','projects','models','miopen') { New-Item -ItemType Directory -Force -Path $A[$d] | Out-Null }
$script:VaiLogFile = Join-Path $A.logs ("install-{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
Write-VaiLog "---- audio-ai install (pid $PID) ----"
$Pipeline = Join-Path $AudioRepo 'audio_pipeline.py'
$McpServer = Join-Path $AudioRepo 'mcp_server.py'

# Smart App Control bloque les DLL non signees executees depuis %TEMP%.
if ($AHome -like "$env:TEMP*") { throw "AUDIO_AI_HOME ne doit pas etre sous %TEMP% (Smart App Control)." }

# 0. Outils ----------------------------------------------------------------------
Write-Step 'Outils (uv, FFmpeg avec Rubber Band)'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv introuvable (winget install astral-sh.uv).' }
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) { throw 'ffmpeg introuvable (winget install Gyan.FFmpeg).' }
$filters = & $ffmpeg -hide_banner -filters 2>$null
foreach ($f in 'rubberband','aresample') {
    if (-not ($filters -match "\s$f\s")) { throw "FFmpeg sans filtre '$f' : installer le build complet (Gyan.FFmpeg)." }
}
Write-Ok "ffmpeg : $ffmpeg (rubberband + soxr)"

# 1. venv dedie --------------------------------------------------------------------
Write-Step "venv Python $($acfg.python) -> $($A.venv)"
if (-not (Test-Path -LiteralPath $A.python)) {
    uv venv --quiet --python $acfg.python $A.venv
    if ($LASTEXITCODE) { throw 'creation du venv echouee' }
    Write-Ok 'venv cree'
} else { Write-Skip 'venv deja present' }
$uvpy = @('--python', $A.python)

# 2. PyTorch ROCm : meme build que video-ai ------------------------------------------
Write-Step 'PyTorch ROCm (build video-ai, gfx103X)'
$torchPins = @($vcfg.torch.packages | Where-Object { $_ -notlike 'torchvision==*' })   # torchvision inutile ici
$pinCheck = "import importlib.metadata as m,sys`nfor p in sys.argv[1:]:`n n,v=p.split('==')`n try:`n  if m.version(n)!=v: print(p)`n except m.PackageNotFoundError: print(p)"
$mismatch = @(& $A.python -I -c $pinCheck @torchPins 2>$null)
if (-not $mismatch) { Write-Skip "$($torchPins -join ', ') deja installes" }
else {
    $wh = $vcfg.paths.wheelhouse
    New-Item -ItemType Directory -Force -Path $wh | Out-Null
    uv pip install --quiet @uvpy --find-links $wh --index-url $vcfg.torch.index @torchPins
    if ($LASTEXITCODE) {
        Write-WarnL 'index AMD indisponible : tentative hors-ligne depuis le wheelhouse video-ai'
        uv pip install --quiet @uvpy --no-index --find-links $wh @torchPins
        if ($LASTEXITCODE) { throw 'installation PyTorch ROCm echouee' }
    }
    Write-Ok "installe : $($torchPins -join ', ')"
}

# 3. Dependances audio, torch verrouille ----------------------------------------------
Write-Step 'Dependances audio (Demucs, librosa, soundfile, pyloudnorm, mcp)'
$reqs = @(Join-Path $AudioRepo 'requirements.txt')
if ($WithRoformer) { $reqs += Join-Path $AudioRepo 'requirements-roformer.txt' }
$constraints = Join-Path $A.run 'constraints.torch.txt'
@($torchPins) + @($vcfg.constraints.packages) | Set-Content -LiteralPath $constraints -Encoding utf8
$stamp = Join-Path $A.run 'requirements.installed.sha256'
$content = (($reqs | ForEach-Object { Get-Content -LiteralPath $_ -Raw }) -join "`n") + (Get-Content -LiteralPath $constraints -Raw)
$reqHash = (Get-FileHash -InputStream ([IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes($content)))).Hash
$depsChanged = $false
if ((Test-Path -LiteralPath $stamp) -and (Get-Content -LiteralPath $stamp) -eq $reqHash) { Write-Skip 'dependances deja a jour' }
else {
    $depsChanged = $true
    $rargs = $reqs | ForEach-Object { '-r'; $_ }
    uv pip install --quiet @uvpy @rargs --constraint $constraints --find-links $vcfg.paths.wheelhouse --index-url https://pypi.org/simple
    if ($LASTEXITCODE) { throw 'installation des dependances audio echouee' }
    $reqHash | Set-Content -LiteralPath $stamp
    Write-Ok 'dependances installees'
}
$after = @(& $A.python -I -c $pinCheck @torchPins)
if ($after) { throw "PyTorch remplace par une dependance : $($after -join ', ') — verifier les contraintes." }
& $A.python -I -c "import torch, torchaudio, demucs.apply, librosa, soundfile, pyloudnorm, scipy.signal, mcp.server.fastmcp"
if ($LASTEXITCODE) { throw 'un module audio ne se charge pas (voir ci-dessus ; Smart App Control ? -> vai saccheck)' }
Write-Ok 'modules charges : torch, demucs, librosa, soundfile, pyloudnorm, mcp'

# 4. Smart App Control : aucun binaire du venv ne doit etre refuse ---------------------
if ($depsChanged -or $CheckSAC) {
    Write-Step 'Smart App Control (binaires du venv audio-ai)'
    $saved = $env:PYTHONPATH; $env:PYTHONPATH = $script:VaiRepo
    $report = Join-Path $A.logs 'saccheck.json'
    try { & $A.python -m vai.saccheck > $report } finally { $env:PYTHONPATH = $saved }
    $sac = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    if ($sac.blocked.Count) {
        foreach ($b in $sac.blocked) { Write-Fail "bloque : $($b.file)" }
        Write-WarnL 'epingler une version plus ancienne du paquet concerne dans requirements.txt, puis relancer'
    } else { Write-Ok "$($sac.checked) binaires acceptes" }
}

# 5. Lock des versions (versionne) -------------------------------------------------------
$lock = Join-Path $AudioRepo 'config\requirements.lock.txt'
@("# Genere par audio-ai/scripts/install.ps1 — Python $(& $A.python -I -c 'import platform;print(platform.python_version())')") +
    (uv pip freeze @uvpy) | Set-Content -LiteralPath $lock -Encoding utf8
Write-Ok "lock : $lock"

# Environnement des processus Python (caches MIOpen et poids sur E:, ffmpeg resolu)
foreach ($k in $acfg.env.Keys) { Set-Item "env:$k" $acfg.env[$k] }
$env:AUDIO_AI_HOME = $AHome; $env:FFMPEG = $ffmpeg

# 6. Poids Demucs ---------------------------------------------------------------------------
if (-not $NoModels) {
    Write-Step 'Poids Demucs (torch.hub -> <home>\models\torch)'
    foreach ($m in @($acfg.default_model, $acfg.selftest_model) | Select-Object -Unique) {
        & $A.python $Pipeline fetch-models --model $m | Out-Null
        if ($LASTEXITCODE) { Write-WarnL "telechargement de $m echoue" } else { Write-Ok $m }
    }
} else { Write-Skip 'poids non telecharges (-NoModels)' }

# 7. Selftest reel sur le GPU ----------------------------------------------------------------
if (-not $SkipTest) {
    Write-Step 'Selftest GPU (BPM, tonalite, Rubber Band, mastering, separation Demucs)'
    $out = & $A.python $Pipeline selftest --with-model --model $acfg.selftest_model
    $code = $LASTEXITCODE
    $out | Set-Content -LiteralPath (Join-Path $A.logs 'selftest.json') -Encoding utf8
    if ($code) { Write-Fail "selftest en echec (voir $($A.logs)\selftest.json)" }
    else {
        $st = $out | ConvertFrom-Json
        Write-Ok ("GPU {0} ({1}) | x{2} vs CPU | {3} BPM, {4} | Demucs pic VRAM {5} Go" -f $st.gpu.name, $st.gpu.arch,
            $st.gpu.benchmark.speedup, $st.analysis.bpm, $st.analysis.key, $st.demucs.vram_peak_gb)
    }
} else { Write-Skip 'selftest ignore (-SkipTest)' }

# 8. Enregistrement MCP -------------------------------------------------------------------------
if (-not $NoMcp) {
    Write-Step 'Serveur MCP audio-ai (Claude Desktop / Claude Code)'
    $entry = [ordered]@{
        command = $A.python; args = @($McpServer)
        env = [ordered]@{ AUDIO_AI_HOME = $AHome; FFMPEG = $ffmpeg }
    }
    foreach ($k in $acfg.env.Keys | Sort-Object) { $entry.env[$k] = $acfg.env[$k] }
    # Forme canonique (cles triees) : l'ordre des cles relu depuis le JSON ne declenche pas de reecriture.
    function ConvertTo-Canon($e) {
        if (-not $e) { return '' }
        $envs = if ($e.env) { ($e.env.Keys | Sort-Object | ForEach-Object { "$_=$($e.env[$_])" }) -join ';' } else { '' }
        return "$($e.command)|$(@($e.args) -join ' ')|$envs"
    }
    # Claude Desktop, par ordre de priorite : donnees hors paquet des versions MSIX recentes
    # (%LOCALAPPDATA%\Claude-Data\claude_<id>, signale par .user-data-left.* dans l'ancien dossier),
    # puis dossier MSIX virtualise, puis installation classique (%APPDATA%\Claude).
    $dirs = @(Get-ChildItem "$env:LOCALAPPDATA\Claude-Data" -Directory -Filter 'claude_*' -ErrorAction SilentlyContinue |
              ForEach-Object FullName) +
            @(Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue |
              ForEach-Object { Join-Path $_.FullName 'LocalCache\Roaming\Claude' }) + @(Join-Path $env:APPDATA 'Claude')
    $dir = ($dirs | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'claude_desktop_config.json') } | Select-Object -First 1)
    if (-not $dir) { $dir = $dirs | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1 }
    if (-not $dir) { Write-WarnL 'Claude Desktop non detecte : modele dans audio-ai\config\claude_desktop_config.example.json' }
    else {
        $file = Join-Path $dir 'claude_desktop_config.json'
        $cfgJson = if (Test-Path -LiteralPath $file) { Get-Content -LiteralPath $file -Raw | ConvertFrom-Json -AsHashtable } else { $null }
        if (-not $cfgJson) { $cfgJson = [ordered]@{} }
        if (-not $cfgJson.Contains('mcpServers')) { $cfgJson.mcpServers = [ordered]@{} }
        if ((ConvertTo-Canon $cfgJson.mcpServers['audio-ai']) -eq (ConvertTo-Canon $entry)) { Write-Skip "deja enregistre : $file" }
        else {
            if (Test-Path -LiteralPath $file) {
                Copy-Item -LiteralPath $file "$file.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            }
            $cfgJson.mcpServers['audio-ai'] = $entry
            $cfgJson | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $file -Encoding utf8
            Write-Ok "Claude Desktop : $file (redemarrer Claude Desktop pour charger le serveur)"
        }
    }
    # Claude Code (portee utilisateur), si la CLI est installee
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        & claude mcp get audio-ai *> $null
        if ($LASTEXITCODE -eq 0) { Write-Skip 'Claude Code : serveur audio-ai deja enregistre' }
        else {
            $envArgs = $entry.env.GetEnumerator() | ForEach-Object { '-e'; "$($_.Key)=$($_.Value)" }
            # Le nom AVANT -e (option variadique) ; '--' entre guillemets, sinon PowerShell l'absorbe.
            & claude mcp add audio-ai --scope user @envArgs '--' $A.python $McpServer *> $null
            if ($LASTEXITCODE) { Write-WarnL 'Claude Code : enregistrement MCP echoue' } else { Write-Ok 'Claude Code : serveur audio-ai enregistre (portee utilisateur)' }
        }
    }
} else { Write-Skip 'enregistrement MCP ignore (-NoMcp)' }

Write-Host "`naudio-ai pret. Exemple : .\audio-ai\aai.ps1 process morceau.mp3 --target-bpm 128 --target-key Am" -ForegroundColor Magenta
