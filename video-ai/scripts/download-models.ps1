#Requires -Version 7
<#
.SYNOPSIS
    Telecharge (idempotent, reprise possible, SHA-256 verifie) les poids listes dans config/models.json.
.EXAMPLE
    pwsh -File video-ai\scripts\download-models.ps1                       # tous les groupes
    pwsh -File video-ai\scripts\download-models.ps1 -Group flux2-klein-4b
    pwsh -File video-ai\scripts\download-models.ps1 -List
    pwsh -File video-ai\scripts\download-models.ps1 -Verify               # re-hash des fichiers presents
#>
[CmdletBinding()]
param(
    [string[]]$Group,
    [switch]$List,
    [switch]$Verify
)
. "$PSScriptRoot\common.ps1"
$cfg = Get-VaiConfig
Initialize-VaiLog $cfg 'download-models'
$manifest = Get-ModelManifest
# pwsh -File passe "a,b" comme une seule chaine : on decoupe.
$Group = @($Group | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (-not $Group) { $Group = @($manifest.groups.Keys) }
$verifiedDir = Join-Path $cfg.paths.run 'verified'
New-Item -ItemType Directory -Force -Path $verifiedDir | Out-Null

if ($List) {
    foreach ($g in $manifest.groups.Keys) {
        Write-Host "`n$g — $($manifest.groups[$g].description)" -ForegroundColor Cyan
        foreach ($s in Get-ModelGroupStatus $cfg $g) {
            $state = if ($s.present) { '[ok]' } else { '[absent]' }
            Write-Host ("    {0,-9} {1,-45} {2,9}  {3}" -f $state, $s.name, (Format-Bytes $s.size), $s.folder)
        }
    }
    return
}

$curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if (-not $curl) { throw 'curl.exe introuvable (fourni avec Windows 10/11).' }

# Espace disque necessaire pour ce qui manque.
$missing = foreach ($g in $Group) {
    if (-not $manifest.groups.ContainsKey($g)) { throw "groupe inconnu : $g (voir -List)" }
    Get-ModelGroupStatus $cfg $g | Where-Object { -not $_.present }
}
$need = ($missing | Measure-Object -Property size -Sum).Sum
$drive = [System.IO.Path]::GetPathRoot($cfg.paths.models)
New-Item -ItemType Directory -Force -Path $cfg.paths.models | Out-Null
$free = (Get-PSDrive -Name $drive.Substring(0,1)).Free
if ($need -gt 0) {
    Write-Step ("A telecharger : {0} (libre sur {1} : {2})" -f (Format-Bytes $need), $drive, (Format-Bytes $free))
    if ($free -lt ($need + 5GB)) { throw "espace insuffisant sur $drive" }
}

function Test-Sha {
    param($Path, $Expected, $Marker)
    Write-Host "    verification SHA-256 de $(Split-Path $Path -Leaf) ..." -ForegroundColor White
    $h = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $Expected) { return $false }
    @{ sha256 = $h; size = (Get-Item -LiteralPath $Path).Length; verified_utc = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding utf8
    return $true
}

$failed = 0
foreach ($g in $Group) {
    Write-Step "Groupe $g"
    foreach ($f in $manifest.groups[$g].files) {
        $dir = Join-Path $cfg.paths.models $f.folder
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $dest = Join-Path $dir $f.name
        $marker = Join-Path $verifiedDir ("{0}__{1}.json" -f $f.folder, $f.name)

        if ((Test-Path -LiteralPath $dest) -and (Get-Item -LiteralPath $dest).Length -eq [int64]$f.size) {
            if (-not $Verify -and (Test-Path -LiteralPath $marker)) { Write-Skip "$($f.name) deja present et verifie"; continue }
            if (Test-Sha $dest $f.sha256 $marker) { Write-Ok "$($f.name) present, SHA-256 conforme"; continue }
            Write-Fail "$($f.name) : SHA-256 NON conforme — fichier laisse en place, renommez/supprimez-le manuellement puis relancez."
            $failed++; continue
        }
        if (Test-Path -LiteralPath $dest) {
            Write-Fail "$($f.name) existe avec une taille inattendue — non ecrase. Deplacez-le puis relancez."
            $failed++; continue
        }

        $part = "$dest.part"
        Write-Host ("    telechargement {0} ({1}) ..." -f $f.name, (Format-Bytes ([int64]$f.size))) -ForegroundColor White
        $t0 = Get-Date
        & $curl --fail --location --continue-at - --retry 5 --retry-delay 5 --silent --show-error --output $part $f.url
        if ($LASTEXITCODE -ne 0) { Write-Fail "$($f.name) : curl code $LASTEXITCODE (relancez : reprise automatique)"; $failed++; continue }
        if ((Get-Item -LiteralPath $part).Length -ne [int64]$f.size) { Write-Fail "$($f.name) : taille inattendue"; $failed++; continue }
        if (-not (Test-Sha $part $f.sha256 $marker)) {
            Write-Fail "$($f.name) : SHA-256 non conforme, fichier .part conserve pour inspection"; $failed++; continue
        }
        Move-Item -LiteralPath $part -Destination $dest
        $sec = [math]::Max(1, ((Get-Date) - $t0).TotalSeconds)
        Write-Ok ("{0} installe ({1:N0} Mo/s)" -f $f.name, ([int64]$f.size / 1MB / $sec))
    }
}
if ($failed) { Write-Fail "$failed fichier(s) en echec"; exit 1 }
Write-Ok 'modeles a jour'
