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
│   ├── warp/                  # Config du terminal Warp
│   │   ├── settings.toml
│   │   ├── launch_configurations/launch_config.yaml
│   │   ├── tab_configs/my_tab_config.toml
│   │   └── default_tab_configs/worktree.toml
│   ├── windows-terminal/      # settings.json (profils, raccourcis)
│   ├── vscode/                # settings.json, keybindings, extensions.txt
│   └── winget-packages.json   # Export de toutes les apps winget installees
└── wsl/
    └── kali/
        ├── setup.sh           # Installeur Kali/WSL idempotent
        └── dotfiles/          # Style du terminal Kali
            ├── zshrc
            ├── bashrc
            └── profile
```

## Utilisation

### Après une réinstallation de Windows (procédure complète)

Le disque **D:** (dont `D:\Archive`) est conservé au reset. Ce dépôt y vit, avec le
coffre KeePass et les archives. Une fois Windows réinstallé :

1. **PowerShell en administrateur** (nécessaire pour activer WSL) :
   ```powershell
   pwsh -ExecutionPolicy Bypass -File D:\Archive\infra\setup.ps1
   ```
2. Si le script demande un **redémarrage** (activation de WSL / Virtual Machine Platform),
   redémarre puis **relance la même commande**.
3. À la première installation de Kali, ouvre une fois `kali-linux` pour créer ton
   utilisateur, puis **relance** `setup.ps1` : les dotfiles Kali se déploient alors.

Le script est **rejouable** à l'infini : il vérifie l'état avant chaque action et
n'écrase rien d'inutile (les fichiers existants sont sauvegardés en `.bak-<horodatage>`).

### Usage courant

```powershell
pwsh -ExecutionPolicy Bypass -File .\setup.ps1
```

Options :

| Option           | Effet                                               |
|------------------|-----------------------------------------------------|
| `-SkipFeatures`  | ne pas activer les fonctionnalités Windows (WSL…)   |
| `-SkipApps`      | ne pas installer les applications (winget / npm)    |
| `-SkipConfigs`   | ne pas déployer les profils + autostart Warp        |
| `-SkipExtensions`| ne pas poser la policy d'extension Chromium         |
| `-SkipWsl`       | ne pas installer/configurer Kali                    |
| `-SkipKeePass`   | ne pas restaurer le coffre sur le Bureau            |
| `-SkipClone`     | ne pas cloner les dépôts des onglets Warp           |
| `-KeePassSource` | chemin du `.kdbx` à restaurer (défaut `D:\Archive\KeePass\Database.kdbx`) |

### Kali / WSL (seul)

```bash
bash wsl/kali/setup.sh
```

## Ce que `setup.ps1` fait

**Fonctionnalités Windows 11** : active WSL (`Microsoft-Windows-Subsystem-Linux`) et
`VirtualMachinePlatform`, règle WSL en version 2, puis installe la distribution
`kali-linux` si absente. (Étape admin ; peut demander un redémarrage.)

**Applications (winget)** : Android Studio, Claude (desktop), VS Code, **GitHub Desktop**,
Brave, Google Chrome, Docker Desktop, **Python 3.14**, **nvm-windows**, PowerShell 7
(+ preview), Oh My Posh, Git, Node.js (+ **npm** fourni avec), Windows Terminal, Warp,
**GitHub CLI** (`gh`), **JetBrainsMono Nerd Font** (glyphes du prompt).

**CLI globaux (npm)** : Claude Code (`@anthropic-ai/claude-code`), opencode (`opencode-ai`).

**Modules PowerShell (PSGallery)** : `posh-git`, `Terminal-Icons`, `z`, `DockerCompletion`,
`npm-completion` (requis par le profil).

**Extensions VS Code** : réinstallées depuis `windows/vscode/extensions.txt`.

**Extension « Claude in Chrome »** : déployée via la policy `ExtensionInstallForcelist`
sur tous les navigateurs Chromium présents (Chrome, Brave, Edge).
ID de l'extension : `fcoeoabgfenejglbffodgkkbkcdhcgfn`.

**Configs déployées** : profil + config PowerShell, config Warp (`settings.toml` / launch
config / tab configs), Windows Terminal (`settings.json`), VS Code (`settings.json` /
`keybindings.json`).

> `windows/winget-packages.json` est un **instantané** de toutes les apps winget installées.
> Pour tout réinstaller d'un coup sur une machine neuve :
> `winget import -i windows/winget-packages.json --accept-package-agreements --accept-source-agreements`
> (le `setup.ps1` se limite, lui, à la liste curée ci-dessus.)

**WSL/Kali** : paquets apt (zsh + syntax-highlighting + autosuggestions, git, tmux…),
dotfiles (style de terminal Kali à deux lignes), zsh en shell par défaut.

**Autostart Warp** : crée un raccourci dans le dossier Démarrage qui lance Warp avec
`warp://launch/launch_config.yaml`, pour retrouver ta disposition de fenêtres/onglets
à l'ouverture de session.

**Coffre KeePass** : si `Database.kdbx` n'est pas déjà sur le Bureau, il est recopié
depuis `D:\Archive\KeePass\Database.kdbx` (disque conservé au reset).
⚠️ Le `.kdbx` **n'est jamais versionné dans ce dépôt** (exclu par `.gitignore`).

**Clonage des dépôts** : clone les repos de tes onglets Warp (listés dans
`windows/repos.txt`) dans `Documents\GitHub\<repo>` via `gh repo clone`, s'ils sont
absents. Nécessite `gh auth login` au préalable. **CoinRich et le projet RAT sont exclus.**

**nvm** : côté Windows via winget (nvm-windows) ; côté Kali, le script officiel nvm
est installé par `wsl/kali/setup.sh`.

## Sécurité

- Aucun token, clé, mot de passe ou historique de shell n'est versionné.
- Les patterns de secrets sont exclus par `.gitignore`.
- La config Warp a été nettoyée (aucune donnée sensible, aucun chemin de projet privé).
