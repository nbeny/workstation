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
            [IO.File]::WriteAllBytes((Join-Path $dir $Name), [byte[]]::new($Size))
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
}
