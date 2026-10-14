# CLAUDE.md — dépôt `workstation`

Ce dépôt reconstruit le poste Windows de l'utilisateur (`setup.ps1` idempotent, configs Warp, PowerShell,
VS Code, Kali/WSL). Il contient aussi **`video-ai/`**, un environnement local de production IA vidéo
(ComfyUI sur RX 6900 XT). Langue du dépôt : français, scripts sans accents dans le code.

Règles générales : aucun secret versionné (voir `.gitignore`) ; les scripts restent **idempotents** et
sauvegardent tout fichier écrasé en `.bak-<horodatage>`.

## video-ai — architecture

| Où | Quoi |
|---|---|
| `video-ai/` (versionné) | `config/` (config, manifeste des modèles, lock pip), `scripts/` (PowerShell), `vai/` (CLI Python), `workflows/` (graphes ComfyUI au format API + paramètres), `assets/characters/_template`, `tests/`, `docs/` |
| `E:\video-ai\` = `VIDEO_AI_HOME` (hors Git) | `ComfyUI\` (tag figé), `venv\` (Python 3.12 dédié), `models\`, `wheelhouse\` (wheels PyTorch AMD), `comfy-io\{input,output}`, `projects\`, `assets\characters\`, `logs\`, `run\` (PID, yaml généré), `cache\miopen`, `backups\` |

- **GPU** : PyTorch 2.10 + ROCm 7.13 « TheRock » compilé pour gfx103X (`config/video-ai.json` → `torch`).
  ComfyUI Desktop et les builds AMD officiels **ne fonctionnent pas** sur RDNA2 : ne pas les proposer.
- **ComfyUI** écoute uniquement sur `127.0.0.1:8188`. Le démarrage passe toujours par `start.ps1`.
- **Lancement habituel** : l'onglet Warp `video-ai` (`windows/warp/launch_configurations/launch_config.yaml`)
  exécute `start.ps1` à l'ouverture de session.

## Commandes

```powershell
pwsh -File video-ai\scripts\start.ps1            # vérifs + démarrage ComfyUI + santé (idempotent)
pwsh -File video-ai\scripts\start.ps1 -NoStart   # diagnostic seul
pwsh -File video-ai\scripts\stop.ps1             # arrête UNIQUEMENT l'instance lancée par le workspace
pwsh -File video-ai\scripts\install.ps1          # (ré)installe ComfyUI + venv (idempotent)
pwsh -File video-ai\scripts\download-models.ps1 -List | -Group <groupe> | -Verify
pwsh -File video-ai\tests\run-tests.ps1 [-Quick] [-Only 1,4]
.\video-ai\vai.ps1 <commande>                    # CLI Python (voir ci-dessous)
```

Journaux : `E:\video-ai\logs\` (`start-*.log`, `comfyui-<horodatage>.log` = sortie ComfyUI, `install-*.log`, `saccheck.json`).
Le lanceur généré est `E:\video-ai\run\comfyui-launch.cmd` ; l'arbre de processus est dans `run\comfyui.pid.json`.
Chaque génération écrit un `<job>.job.json` (statut, seed, paramètres, durée, pic VRAM/RAM, erreur).

## CLI `vai` (pilotage de l'API locale)

```powershell
.\video-ai\vai.ps1 status
.\video-ai\vai.ps1 workflows                                   # workflows + paramètres exposés
.\video-ai\vai.ps1 run flux2_klein_4b_t2i prompt="..." seed=42 width=1024 height=1280 -o E:\video-ai\projects\x\renders\tests
.\video-ai\vai.ps1 run wan22_ti2v_5b_i2v image=C:\chemin\img.png prompt="..." frames=49 width=704 height=1280
.\video-ai\vai.ps1 resume <fichier.job.json>                   # relance à l'identique un job échoué
.\video-ai\vai.ps1 audio musique.mp3 -o cues.json
.\video-ai\vai.ps1 project new <nom>
.\video-ai\vai.ps1 storyboard <projet> musique.mp3 --format 9x16 --character <slug> [--start 30 --end 60]
.\video-ai\vai.ps1 render keyframes <projet> [--use-ref]  ;  .\video-ai\vai.ps1 render clips <projet>
.\video-ai\vai.ps1 assemble <projet> --format 9x16 16x9 --codec h264
.\video-ai\vai.ps1 character new "<nom>"  ;  .\video-ai\vai.ps1 character prompt <slug> --outfit casual --scene "..."
```

Chaîne d'un clip musical : `project new` → `storyboard` (analyse audio et plans calés sur les mesures et
changements d'énergie) → remplir `keyframe_prompt`/`video_prompt` dans `storyboard.json` →
`render keyframes` → `render clips` → `assemble`. Les rendus déjà faits sont sautés : relancer reprend là
où ça s'est arrêté.

## Ce que Claude Code fait (et ne fait pas)

- **Fait** : préparer storyboards et prompts, éditer `storyboard.json` et les fiches personnage, lancer des
  jobs via `vai`/l'API, lire les journaux et les `*.job.json`, diagnostiquer, préparer et lancer le montage FFmpeg.
- **Ne fait pas** : générer lui-même des images ou des vidéos. Ce sont les modèles locaux (ComfyUI) qui
  produisent les médias. Ne jamais présenter un résultat comme généré sans le `job.json` `done` correspondant.
- Un test **SKIP** n'est pas un succès. Ne jamais déclarer un workflow fonctionnel sans exécution réelle.

## Ajouter un workflow ComfyUI

1. Dans l'interface (`http://127.0.0.1:8188`), construire le graphe, puis **Workflow → Export (API)**.
2. L'enregistrer en `video-ai/workflows/<categorie>/<nom>.api.json`.
3. Écrire `<nom>.params.json` : `{"description", "params": {"prompt": {"targets": [["<id noeud>", "<entree>"]], "default": ...}}}`.
   Types spéciaux : `"type": "seed"` (-1 = aléatoire, valeur réelle tracée), `"image"` (chemin local → upload),
   `"frames"` (arrondi à 4n+1 pour Wan).
4. Valider : `pwsh -File video-ai\tests\run-tests.ps1 -Only 3`. Ce test confronte chaque workflow aux
   définitions réelles des nœuds (`/object_info`).
5. Les custom nodes ne s'installent **qu'avec l'accord de l'utilisateur** (risque de casser les dépendances du venv).

## Modèles et références

- Les poids sont listés dans `video-ai/config/models.json` (URL, taille, SHA-256) et téléchargés par
  `download-models.ps1` dans `E:\video-ai\models\<dossier>`. **Jamais dans Git.**
- Nouveau modèle : vérifier la licence, la taille, la compatibilité RDNA2/16 Go et l'existence de nœuds
  natifs, l'ajouter au manifeste avec son SHA-256, puis demander l'accord avant tout téléchargement de plus de 5 Go.
- Personnages : `E:\video-ai\assets\characters\<slug>\` (copie de `video-ai/assets/characters/_template`).
  Le prompt canonique (identité) ne change pas ; seuls la tenue, la scène et le cadrage varient. Cohérence
  par image de référence (`flux2_klein_4b_ref`). **PuLID et IP-Adapter ne sont pas compatibles FLUX.2.**
  Personnages **fictifs** uniquement. LoRA : phase ultérieure, jamais automatique.

## Limites RX 6900 XT (gfx1030, 16 Go) — détails : `video-ai/docs/limitations.md`

- Pas de fp8 natif (les poids fp8 sont convertis à la volée), pas de flash ni de memory-efficient attention :
  on lance avec `--use-split-cross-attention`.
- Un seul gros modèle à la fois en VRAM : ComfyUI décharge entre FLUX et Wan.
- Wan 2.2 5B : 121 images maximum (~5 s), 1280x704 natif et lent ; pour tester, rester en 640x352 / 25 images.
- La première exécution compile les kernels MIOpen (lente), ensuite mis en cache dans `E:\video-ai\cache\miopen`.
- Runtime sur `E:` (HDD CMR, ~330 Mo/s) : ne pas le remettre sur `D:` (SMR saturé par Docker, voir `video-ai/docs/limitations.md`).
- `--disable-pinned-memory` est **obligatoire** : sans lui, le chargement de Wan échoue (`HIP error: invalid argument`).
- Wan à 352x640, 25 images et 12 étapes prend environ 12 min par job, avec un pic de VRAM de 15,5 Go. Monter en
  résolution ou en durée progressivement, un plan à la fois.
- RAM : 32 Go, saturés quand Docker et les applications tournent. Penser à fermer les applications lourdes avant un rendu long.
- Smart App Control est actif : toute nouvelle dépendance pip peut avoir une DLL refusée (`WinError 4551`).
  Après chaque changement du venv : `.\video-ai\vai.ps1 saccheck`, et épingler une version plus ancienne
  dans `config/video-ai.json` → `constraints` si besoin. Ne rien exécuter depuis `%TEMP%`.

## Opérations soumises à l'accord de l'utilisateur

Suppression ou écrasement de fichiers, modèles ou workflows existants ; mise à jour du driver AMD, de
ComfyUI (`install.ps1 -Update`) ou de PyTorch ; installation de custom nodes ou de paquets pip dans le
venv ; téléchargement de plus de 5 Go non prévu ; modification de Windows (Smart App Control, Defender,
variables système) ; exposition réseau (`--listen 0.0.0.0`) ; tout service ou API payant (les clés
éventuelles vont dans `video-ai/config/local.json` ou des variables d'environnement, jamais versionnées).
