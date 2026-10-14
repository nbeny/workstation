BeforeAll {
    . (Join-Path $PSScriptRoot 'media-sync.ps1')
}

Describe 'Get-PendingRecordings' {
    BeforeEach {
        $src = Join-Path $TestDrive 'src'
        $dst = Join-Path $TestDrive 'dst'
        Remove-Item $src, $dst -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $src, $dst | Out-Null
        $now = [datetime]'2026-10-14T12:00:00'

        function New-Rec([string]$Name, [datetime]$Written, [int]$Size = 10) {
            $p = Join-Path $src $Name
            [IO.File]::WriteAllBytes($p, [byte[]]::new($Size))
            (Get-Item -LiteralPath $p).LastWriteTime = $Written
        }
        function New-Copy([string]$Name, [int]$Size) {
            $dir = Join-Path $dst '2026-10'
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            $c = Join-Path $dir $Name
            [IO.File]::WriteAllBytes($c, [byte[]]::new($Size))
            # robocopy conserve la date : une copie complete a la meme date que la source.
            (Get-Item -LiteralPath $c).LastWriteTime = (Get-Item -LiteralPath (Join-Path $src $Name)).LastWriteTime
        }
        function Invoke-Pending {
            @(Get-PendingRecordings -Source $src -Destination $dst -Now $now -SettleMinutes 2)
        }
    }

    It 'retient un enregistrement termine, absent du partage, rangé par mois' {
        New-Rec 'a.mp4' $now.AddMinutes(-5)
        $r = Invoke-Pending
        $r.Count | Should -Be 1
        $r[0].File.Name | Should -Be 'a.mp4'
        $r[0].TargetDir | Should -Be (Join-Path $dst '2026-10')
    }

    It 'ignore un enregistrement encore en cours d ecriture' {
        New-Rec 'b.mp4' $now.AddMinutes(-1)
        (Invoke-Pending).Count | Should -Be 0
    }

    It 'ignore un fichier deja copie avec la meme taille' {
        New-Rec 'c.mp4' $now.AddMinutes(-5) -Size 10
        New-Copy 'c.mp4' 10
        (Invoke-Pending).Count | Should -Be 0
    }

    It 'recopie un fichier present mais tronque' {
        New-Rec 'd.mp4' $now.AddMinutes(-5) -Size 10
        New-Copy 'd.mp4' 3
        (Invoke-Pending).Count | Should -Be 1
    }

    It 'ignore ce qui n est pas un mp4' {
        New-Rec 'e.mkv' $now.AddMinutes(-5)
        (Invoke-Pending).Count | Should -Be 0
    }

    It 'ne renvoie rien si la source n existe pas' {
        @(Get-PendingRecordings -Source (Join-Path $TestDrive 'absent') -Destination $dst -Now $now -SettleMinutes 2).Count |
            Should -Be 0
    }

    It 'retient un fichier de meme taille mais de date differente (copie interrompue)' {
        New-Rec 'f.mp4' $now.AddMinutes(-5) -Size 10
        New-Copy 'f.mp4' 10
        (Get-Item -LiteralPath (Join-Path $dst '2026-10\f.mp4')).LastWriteTime = [datetime]'1980-01-01'
        (Invoke-Pending).Count | Should -Be 1
    }

    It 'ignore un fichier de meme taille et de meme date' {
        New-Rec 'g.mp4' $now.AddMinutes(-5) -Size 10
        New-Copy 'g.mp4' 10
        (Get-Item -LiteralPath (Join-Path $dst '2026-10\g.mp4')).LastWriteTime = $now.AddMinutes(-5)
        (Invoke-Pending).Count | Should -Be 0
    }

    It 'ignore un fichier verrouille (encore ecrit par OBS)' {
        New-Rec 'h.mp4' $now.AddMinutes(-5)
        $fs = [IO.File]::Open((Join-Path $src 'h.mp4'), 'Open', 'ReadWrite', 'None')
        try { (Invoke-Pending).Count | Should -Be 0 }
        finally { $fs.Dispose() }
    }
}

Describe 'media-sync.ps1 (parametres)' {
    It 'le partage par defaut est le chemin UNC exact' {
        $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'media-sync.ps1'), [ref]$null, [ref]$null)
        $param = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'Destination' }
        $param.DefaultValue.Value | Should -Be '\\10.0.0.165\media\raw\discord'
    }
}
