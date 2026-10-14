#Requires -Version 7
<#
.SYNOPSIS
    Installe / verifie ComfyUI + venv Python dedie (PyTorch ROCm gfx103X) dans <home> (defaut E:\video-ai).

.DESCRIPTION
    Idempotent. Ne touche a aucun autre environnement Python : tout vit dans <home>\venv.
    - clone ComfyUI au tag fige (config/video-ai.json -> comfyui.ref)
    - cree le venv (uv, Python 3.12)
    - telecharge les wheels PyTorch AMD dans <home>\wheelhouse (reinstallation hors-ligne possible)
    - installe PyTorch puis les dependances ComfyUI SANS laisser pip remplacer torch
    - genere extra_model_paths.yaml et le lock video-ai/config/requirements.lock.txt
    Les modeles se telechargent a part : scripts\download-models.ps1.

.EXAMPLE
    pwsh -File video-ai\scripts\install.ps1
    pwsh -File video-ai\scripts\install.ps1 -Update     # passe ComfyUI au tag configure si different
#>
[CmdletBinding()]
param([switch]$Update, [switch]$CheckSAC)
. "$PSScriptRoot\common.ps1"
$cfg = Get-VaiConfig
$P = $cfg.paths
foreach ($d in 'home','models','wheelhouse','input','output','user','temp','projects','assets','logs','run','cache') {
    New-Item -ItemType Directory -Force -Path $P[$d] | Out-Null
}
Initialize-VaiLog $cfg 'install'

# ComfyUI vivant hors %TEMP% : Smart App Control y bloque les DLL ROCm non signees.
if ($P.home -like "$env:TEMP*") { throw "home ne doit pas etre sous %TEMP% (Smart App Control bloque les DLL ROCm)." }

foreach ($tool in 'git','uv') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool introuvable dans le PATH." }
}

# 1. ComfyUI -----------------------------------------------------------------
Write-Step "ComfyUI ($($cfg.comfyui.ref)) -> $($P.comfyui)"
if (-not (Test-Path -LiteralPath (Join-Path $P.comfyui '.git'))) {
    git clone --quiet $cfg.comfyui.repo $P.comfyui
    if ($LASTEXITCODE) { throw 'git clone ComfyUI a echoue' }
    git -C $P.comfyui -c advice.detachedHead=false checkout --quiet $cfg.comfyui.ref
    Write-Ok "clone au tag $($cfg.comfyui.ref)"
} else {
    $cur = (git -C $P.comfyui describe --tags --exact-match 2>$null)
    if ($cur -eq $cfg.comfyui.ref) { Write-Skip "deja au tag $cur" }
    elseif ($Update) {
        if (git -C $P.comfyui status --porcelain) { throw 'modifications locales dans ComfyUI : mise a jour annulee.' }
        git -C $P.comfyui fetch --quiet --tags
        git -C $P.comfyui -c advice.detachedHead=false checkout --quiet $cfg.comfyui.ref
        Write-Ok "mis a jour : $cur -> $($cfg.comfyui.ref)"
    } else { Write-WarnL "ComfyUI est a '$cur', config = '$($cfg.comfyui.ref)' (relancez avec -Update pour aligner)" }
}

# 2. venv dedie ----------------------------------------------------------------
Write-Step "venv Python $($cfg.python) -> $($P.venv)"
if (-not (Test-Path -LiteralPath $P.python)) {
    uv venv --quiet --python $cfg.python $P.venv
    if ($LASTEXITCODE) { throw 'creation du venv echouee' }
    Write-Ok 'venv cree'
} else { Write-Skip 'venv deja present' }
$uvpy = @('--python', $P.python)

# 3. PyTorch ROCm gfx103X (wheelhouse local) -----------------------------------
Write-Step 'PyTorch ROCm (gfx103X)'
$torchPins = $cfg.torch.packages
$wantTorch = ($torchPins | Where-Object { $_ -like 'torch==*' }) -replace '^torch==',''
# torch, torchvision et torchaudio doivent venir du MEME build (sinon WinError 127 dans torchvision\_C.pyd).
$pinCheck = "import importlib.metadata as m,sys`nfor p in sys.argv[1:]:`n n,v=p.split('==')`n try:`n  if m.version(n)!=v: print(p)`n except m.PackageNotFoundError: print(p)"
$mismatch = @(& $P.python -I -c $pinCheck @torchPins 2>$null)
if (-not $mismatch) { Write-Skip "$($torchPins -join ', ') deja installes" }
else {
    Write-Host "    a installer : $($mismatch -join ', ')" -ForegroundColor White
    # Copie locale des wheels (les nightlies AMD peuvent disparaitre de l'index) ; pip download saute l'existant.
    uv pip install --quiet @uvpy pip
    & $P.python -I -m pip download --quiet --dest $P.wheelhouse --index-url $cfg.torch.index @torchPins
    if ($LASTEXITCODE) { Write-WarnL 'pip download du wheelhouse incomplet — installation directe depuis l''index' }
    else { Write-Ok "wheelhouse : $($P.wheelhouse)" }
    uv pip install --quiet @uvpy --find-links $P.wheelhouse --index-url $cfg.torch.index @torchPins
    if ($LASTEXITCODE) {
        Write-WarnL 'index AMD indisponible : tentative hors-ligne depuis le wheelhouse'
        uv pip install --quiet @uvpy --no-index --find-links $P.wheelhouse @torchPins
        if ($LASTEXITCODE) { throw 'installation PyTorch ROCm echouee' }
    }
    Write-Ok "torch $wantTorch installe"
}

# 4. Dependances ComfyUI, torch verrouille -------------------------------------
Write-Step 'Dependances ComfyUI'
$reqSrc = Join-Path $P.comfyui 'requirements.txt'
$reqFiltered = Join-Path $P.run 'requirements.comfyui.txt'
$constraints = Join-Path $P.run 'constraints.torch.txt'
(Get-Content -LiteralPath $reqSrc) | Where-Object { $_ -notmatch '^\s*(torch|torchvision|torchaudio)\s*$' } |
    Set-Content -LiteralPath $reqFiltered -Encoding utf8
@($torchPins) + @($cfg.constraints.packages) | Set-Content -LiteralPath $constraints -Encoding utf8
$stamp = Join-Path $P.run 'requirements.installed.sha256'
$reqHash = (Get-FileHash -InputStream ([IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes(
    (Get-Content -LiteralPath $reqFiltered -Raw) + (Get-Content -LiteralPath $constraints -Raw))))).Hash
$depsChanged = $false
if ((Test-Path -LiteralPath $stamp) -and (Get-Content -LiteralPath $stamp) -eq $reqHash) { Write-Skip 'dependances deja a jour' }
else {
    $depsChanged = $true
    uv pip install --quiet @uvpy -r $reqFiltered --constraint $constraints --find-links $P.wheelhouse --index-url https://pypi.org/simple
    if ($LASTEXITCODE) { throw 'installation des dependances ComfyUI echouee' }
    $reqHash | Set-Content -LiteralPath $stamp
    Write-Ok 'dependances installees'
}
$after = @(& $P.python -I -c $pinCheck @torchPins)
if ($after) { throw "paquets PyTorch non conformes aux versions figees : $($after -join ', ') — verifiez les contraintes." }
# Charge reellement les extensions natives (detecte une incompatibilite d'ABI torch/torchvision/torchaudio).
& $P.python -I -c "import torch, torchvision, torchaudio; torchvision.ops.nms" 2>$null
if ($LASTEXITCODE) { throw 'torch/torchvision/torchaudio ne se chargent pas ensemble (ABI) — verifiez les versions figees.' }
Write-Ok "PyTorch verrouille et chargeable : $wantTorch"

# 5. Chemins des modeles ---------------------------------------------------------
Write-Step 'extra_model_paths.yaml'
$m = $P.models -replace '\\','/'
@"
# Genere par video-ai/scripts/install.ps1 — ne pas editer (modifier config/video-ai.json ou VIDEO_AI_MODELS).
video_ai:
    base_path: "$m"
    is_default: true
    checkpoints: checkpoints
    diffusion_models: |
        diffusion_models
        unet
    text_encoders: |
        text_encoders
        clip
    vae: vae
    loras: loras
    clip_vision: clip_vision
    upscale_models: upscale_models
    controlnet: controlnet
"@ | Set-Content -LiteralPath $P.extra_yaml -Encoding utf8
foreach ($sub in 'checkpoints','diffusion_models','text_encoders','vae','loras','clip_vision','upscale_models','controlnet') {
    New-Item -ItemType Directory -Force -Path (Join-Path $P.models $sub) | Out-Null
}
Write-Ok $P.extra_yaml

# 6. Lock des versions (versionne dans le depot) ----------------------------------
$lock = Join-Path $script:VaiRepo 'config\requirements.lock.txt'
$freeze = uv pip freeze @uvpy
@("# Genere par install.ps1 — ComfyUI $($cfg.comfyui.ref), Python $(& $P.python -I -c 'import platform;print(platform.python_version())')") + $freeze |
    Set-Content -LiteralPath $lock -Encoding utf8
Write-Ok "lock : $lock"

# 7. Smart App Control : aucun binaire du venv ne doit etre refuse ------------------------------
# (lent au premier passage : Windows evalue la reputation de chaque DLL ; rapide ensuite)
if ($depsChanged -or $CheckSAC) {
    Write-Step 'Smart App Control (binaires du venv)'
    $saved = $env:PYTHONPATH; $env:PYTHONPATH = $script:VaiRepo
    $report = Join-Path $P.logs 'saccheck.json'
    try { & $P.python -m vai.saccheck > $report } finally { $env:PYTHONPATH = $saved }
    $sac = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    if ($sac.blocked.Count) {
        foreach ($b in $sac.blocked) { Write-Fail "bloque : $($b.file)" }
        Write-WarnL "epingler une version plus ancienne du paquet concerne dans config/video-ai.json -> constraints, puis relancer install.ps1"
    } else { Write-Ok "$($sac.checked) binaires acceptes" }
}
Write-Host "`nInstallation terminee. Lancement : pwsh -File video-ai\scripts\start.ps1" -ForegroundColor Magenta
