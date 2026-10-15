Import-Module posh-git
Import-Module Terminal-Icons
Import-Module z

# Autosuggestions
Set-PSReadLineOption -PredictionSource History
Set-PSReadLineOption -PredictionViewStyle InlineView

# Tab menu completion
Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete

# Recherche historique style Linux
Set-PSReadLineKeyHandler -Key Ctrl+r -Function ReverseSearchHistory

# Oh My Posh prompt
oh-my-posh init pwsh | Invoke-Expression
#f45873b3-b655-43a6-b217-97c00aa0db58 PowerToys CommandNotFound module

if (Get-Module -ListAvailable -Name Microsoft.WinGet.CommandNotFound) {
    Import-Module -Name Microsoft.WinGet.CommandNotFound
}
#f45873b3-b655-43a6-b217-97c00aa0db58
