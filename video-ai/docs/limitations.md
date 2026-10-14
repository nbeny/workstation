# Limites connues — RX 6900 XT (gfx1030) sous Windows 11

## Pile logicielle

| Point | Situation | Conséquence |
|---|---|---|
| ComfyUI Desktop | Plante au démarrage sur RX 6900 XT (`access violation` dans `torch.cuda.is_available()`, [Comfy-Org/desktop#1745](https://github.com/Comfy-Org/desktop/issues/1745), non résolu, dépôt archivé) | ComfyUI tourne depuis les sources, dans un venv dédié |
| Builds AMD officiels (Desktop/Portable) | Ciblent RDNA3 et RDNA4 | Non utilisés |
| ROCm sous WSL | Ne gère pas RDNA2 | Non utilisé |
| PyTorch | Wheels AMD « TheRock » **nightly** `gfx103X-dgpu` (torch 2.10.0 + ROCm 7.13.0a20260421) | Version figée dans `config/video-ai.json` ; copie locale dans `E:\video-ai\wheelhouse` au cas où l'index AMD ne les servirait plus |
| Smart App Control | Actif (mode application) : refuse les DLL non signées sans réputation (`WinError 4551`), plus souvent sous `%TEMP%` | `install.ps1` refuse un home sous `%TEMP%` ; voir la ligne « Smart App Control » plus bas |
| fp8 | Pas de calcul fp8 natif sur RDNA2 | Les poids fp8 (FLUX.2 Klein, umT5) sont convertis à la volée : ça marche, mais sans gain de vitesse |
| Attention | Ni flash ni memory-efficient attention compilées pour gfx1030 | `--use-split-cross-attention` (découpe pour tenir en VRAM) |
| MIOpen | Première convolution lente (compilation des kernels) | Cache persistant : `E:\video-ai\cache\miopen` ; `MIOPEN_FIND_MODE=FAST` |
| hipBLASLt | Pas d'images gfx1030 | GEMM via rocBLAS, plus lent que sur RDNA3 |

| Pinned memory | Avec la mémoire hôte épinglée (option par défaut), le chargement partiel de Wan échoue : `HIP error: invalid argument` dans `model_patcher.load → .to(cuda)` | `--disable-pinned-memory` (dans `config/video-ai.json`) |
| Smart App Control | Les binaires récents, sans réputation, sont refusés **n'importe où** (scipy 1.18.1, parfois `origami.dll`, `aimdo_rocm.dll`). Le premier passage sur chaque DLL est lent | `scipy==1.17.1` épinglé (`constraints`) ; `TORCH_BLAS_PREFER_HIPBLASLT=0` ; vérification par `vai saccheck` (lancée aussi par `install.ps1`) |

## Performances mesurées (14/10/2026, ComfyUI 0.40.0, torch 2.10.0+rocm7.13)

| Opération | Résultat |
|---|---|
| Démarrage de ComfyUI (`start.ps1` → API prête) | 56 à 135 s ; réutilisation d'une instance déjà lancée : 1 s |
| FLUX.2 Klein 4B, 704x1280, 4 étapes | échantillonnage ~14 s (3,2 s/étape) ; job complet 209 à 322 s, chargement des modèles et encodage du texte compris |
| Wan 2.2 TI2V 5B i2v, 352x640, 25 images, 12 étapes | échantillonnage 4 min 22 (~22 s/étape) ; job complet 737 s |
| Pic de VRAM dédiée | 11,7 Go (FLUX) ; **15,5 Go (Wan, à la limite des 16 Go)** |
| Pic de RAM système | 31,9 Go / 32 (saturée, pagination) |
| Montage 8 s, 9:16 H.264 AMF + 16:9 H.265 AMF | ~16 s |

Extrapolation, à valider : un plan Wan de 121 images en 704x1280 représente environ 25 fois plus de
tokens que le test. Comptez de nombreuses minutes par étape, avec un risque de dépassement de VRAM.
Privilégiez des plans de 2 à 3 s (49 à 73 images) en 480x832 ou 704x1280, puis vérifiez sur un plan
avant de lancer tout un storyboard.

## Mémoire

- **VRAM 16 Go** : un seul gros modèle chargé à la fois. ComfyUI décharge automatiquement entre
  FLUX.2 et Wan, ce qui implique un rechargement depuis le disque à chaque bascule.
- **RAM** : 32 Go installés, mais seulement **5 Go libres environ** quand Docker Desktop (19 conteneurs) et
  les applications de bureau tournent. Charger Wan (10 Go) + umT5 (6,7 Go) fait alors paginer sur
  `C:\pagefile.sys`. Fermer les applications lourdes, ou plafonner WSL (`.wslconfig` → `memory=8GB`,
  à faire avec ton accord), accélère nettement.
- **Wan 2.2 TI2V 5B** : 121 images maximum par plan (~5 s à 24 fps). Le 1280x704 natif est lent sur cette
  carte : pour valider, rester en 640x352, 25 images, 12 étapes. Les plans plus longs se montent par
  enchaînement.

## Disque

Le runtime vit sur **`E:\video-ai`** (WD Gold 10 To, CMR, ~330 Mo/s en lecture). **Ne pas utiliser `D:`** :
c'est un Seagate ST8000DM004 (**SMR**) qui héberge `D:\docker\disk\docker_data.vhdx` (217 Go ; 19 conteneurs
Docker y font des accès aléatoires en permanence). Mesuré lors d'un premier essai sur D: : disque occupé à
100 %, file d'attente de 10 à 16 requêtes, hash SHA-256 à moins de 1 Mo/s, installation pip à 0,8 Mo/s,
soit des jours pour installer les modèles.

Déménager (vers C: pour la vitesse NVMe, par exemple) : `stop.ps1`, déplacer le dossier, renseigner
`config/local.json` → `{"home": "C:\\video-ai"}` (ou `VIDEO_AI_HOME`), puis relancer `install.ps1`. Le venv
n'est pas déplaçable : il est recréé depuis `wheelhouse\`, et `extra_model_paths.yaml` est régénéré.

## Qualité et usage

- Les tempo et temps détectés par `vai audio` sont estimés (autocorrélation du flux spectral), sans
  librosa : à vérifier à l'oreille sur les morceaux complexes ou à tempo variable.
- La cohérence d'identité repose sur le prompt canonique et l'image de référence (`ReferenceLatent`).
  Elle reste imparfaite tant qu'aucune LoRA n'est entraînée (phase ultérieure, non automatique).
- Personnages **fictifs** uniquement.
