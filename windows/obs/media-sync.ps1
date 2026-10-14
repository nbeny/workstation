#Requires -Version 7
<#
.SYNOPSIS
    Copie les enregistrements OBS termines vers le partage media du lab.

.DESCRIPTION
    Lance toutes les 10 minutes par la tache planifiee `media-sync` (setup.ps1).
    Un .mp4 de $Source est copie dans $Destination\AAAA-MM\ quand il n'a plus
    ete ecrit depuis $SettleMinutes et qu'il manque sur le partage (ou n'y a pas
    la meme taille). Ne supprime JAMAIS rien, ni en local ni sur le partage.

    Le partage est vise par son chemin UNC, pas par M: : un lecteur mappe n'est
    pas forcement visible depuis la session de la tache. Les identifiants sont
    ceux du Gestionnaire d'identifiants Windows (poses par setup.ps1).
#>
[CmdletBinding()]
param(
    [string]$Source = (Join-Path ([Environment]::GetFolderPath('MyVideos')) 'Discord'),
    [string]$Destination = '\10.0.0.165\media\raw\discord',
    [int]$SettleMinutes = 2,
    [string]$LogFile = (Join-Path $env:LOCALAPPDATA 'media-sync\media-sync.log')
)

function Get-PendingRecordings {
    param(
        [string]$Source,
        [string]$Destination,
        [datetime]$Now,
        [int]$SettleMinutes
    )
    if (-not (Test-Path -LiteralPath $Source)) { return }
    foreach ($f in Get-ChildItem -LiteralPath $Source -Filter '*.mp4' -File) {
        if ($f.LastWriteTime -gt $Now.AddMinutes(-$SettleMinutes)) { continue }  # encore en cours
        $dir = Join-Path $Destination $f.LastWriteTime.ToString('yyyy-MM')
        $copy = Get-Item -LiteralPath (Join-Path $dir $f.Name) -ErrorAction SilentlyContinue
        if ($copy -and $copy.Length -eq $f.Length) { continue }  # deja sur le partage
        [pscustomobject]@{ File = $f; TargetDir = $dir }
    }
}

function Write-Log {
    param([string]$Message)
    New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
    if ((Test-Path -LiteralPath $LogFile) -and (Get-Item -LiteralPath $LogFile).Length -gt 1MB) {
        Clear-Content -LiteralPath $LogFile
    }
    Add-Content -LiteralPath $LogFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
}

# Charge par les tests (dot-source) : definir les fonctions, ne rien copier.
if ($MyInvocation.InvocationName -eq '.') { return }

if (-not (Test-Path -LiteralPath $Destination)) {
    Write-Log "partage injoignable ($Destination) -- nouvel essai au prochain passage"
    exit 0
}

foreach ($p in @(Get-PendingRecordings -Source $Source -Destination $Destination -Now (Get-Date) -SettleMinutes $SettleMinutes)) {
    New-Item -ItemType Directory -Force -Path $p.TargetDir | Out-Null
    robocopy $p.File.DirectoryName $p.TargetDir $p.File.Name /Z /R:2 /W:10 /NP /NJH /NJS | Out-Null
    # robocopy : 0-7 = succes (1 = copie), 8 et plus = echec.
    if ($LASTEXITCODE -ge 8) { Write-Log "ECHEC robocopy $LASTEXITCODE : $($p.File.Name)" }
    else { Write-Log "copie : $($p.File.Name) -> $($p.TargetDir)" }
}
exit 0
