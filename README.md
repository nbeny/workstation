# infra — configuration & auto-setup de la machine

Dépôt de configuration **portable** pour reconstruire mon poste de travail :
config des terminaux, profils shell, et un installeur idempotent des applications.
Aucun secret n'y est stocké.

## Contenu

```
infra/
├── setup.ps1                  # Installeur Windows idempotent (point d'entrée)
├── windows/
│   ├── powershell/            # Profil PowerShell 7 + config
│   │   ├── Microsoft.PowerShell_profile.ps1
│   │   └── powershell.config.json
│   └── warp/                  # Config du terminal Warp
│       ├── settings.toml
│       ├── launch_configurations/launch_config.yaml
│       ├── tab_configs/my_tab_config.toml
│       └── default_tab_configs/worktree.toml
└── wsl/
    └── kali/
        ├── setup.sh           # Installeur Kali/WSL idempotent
        └── dotfiles/          # Style du terminal Kali
            ├── zshrc
            ├── bashrc
            └── profile
```

## Utilisation

### Windows

Depuis PowerShell, à la racine du dépôt :

```powershell
pwsh -ExecutionPolicy Bypass -File .\setup.ps1
```

Le script est **rejouable** : il vérifie l'état avant chaque action et ne réinstalle
ni n'écrase rien d'inutile (les fichiers existants sont sauvegardés en `.bak-<horodatage>`).

Options :

| Option           | Effet                                             |
|------------------|---------------------------------------------------|
| `-SkipApps`      | ne pas installer les applications (winget / npm)  |
| `-SkipConfigs`   | ne pas déployer les profils PowerShell / Warp     |
| `-SkipExtensions`| ne pas poser la policy d'extension Chromium       |
| `-SkipWsl`       | ne pas lancer le setup Kali                       |

### Kali / WSL (seul)

```bash
bash wsl/kali/setup.sh
```

## Ce que `setup.ps1` installe

**Applications (winget)** : Android Studio, Claude (desktop), VS Code, Brave,
Google Chrome, Docker Desktop, PowerShell 7, Oh My Posh, Git, Node.js,
Windows Terminal, Warp, GitHub CLI.

**CLI globaux (npm)** : Claude Code (`@anthropic-ai/claude-code`), opencode (`opencode-ai`).

**Extension « Claude in Chrome »** : déployée via la policy `ExtensionInstallForcelist`
sur tous les navigateurs Chromium présents (Chrome, Brave, Edge).
ID de l'extension : `fcoeoabgfenejglbffodgkkbkcdhcgfn`.

**Configs déployées** : profil + config PowerShell, `settings.toml` / launch config /
tab configs de Warp.

**WSL/Kali** : paquets apt (zsh + syntax-highlighting + autosuggestions, git, tmux…),
dotfiles (style de terminal Kali à deux lignes), zsh en shell par défaut.

## Sécurité

- Aucun token, clé, mot de passe ou historique de shell n'est versionné.
- Les patterns de secrets sont exclus par `.gitignore`.
- La config Warp a été nettoyée (aucune donnée sensible, aucun chemin de projet privé).
