# video-ai — production IA vidéo locale (ComfyUI, RX 6900 XT)

Influenceurs virtuels photoréalistes cohérents, clips musicaux et vidéos 9:16 / 16:9, générés en local.

| Brique | Choix | Pourquoi |
|---|---|---|
| Orchestrateur | ComfyUI v0.40.0 depuis les sources, `127.0.0.1:8188` | Desktop incompatible RDNA2 (voir `docs/limitations.md`) |
| Calcul | PyTorch 2.10 + ROCm 7.13 (TheRock gfx103X), venv Python 3.12 dédié | Seule pile native Windows qui fait vraiment calculer la RX 6900 XT |
| Images | FLUX.2 [klein] 4B distillé fp8 + Qwen3-4B | Apache-2.0, ~8 Go de VRAM, 4 étapes, édition par référence native |
| Vidéo | Wan 2.2 TI2V 5B fp16 + umT5-XXL fp8 | Apache-2.0, image→vidéo et texte→vidéo, tient en 16 Go |
| Montage | FFmpeg 8.1 (AMF H.264/H.265 matériel) + `vai` | Automatisable, sans dépendance à DaVinci Resolve |

## Démarrage

Automatique : l'onglet Warp **video-ai** lance `scripts/start.ps1` à l'ouverture de session.
Manuel : `pwsh -File video-ai\scripts\start.ps1`, puis ouvrir http://127.0.0.1:8188.

Première installation sur une machine neuve : `setup.ps1` (étape video-ai), ou
`pwsh -File video-ai\scripts\install.ps1`, puis `pwsh -File video-ai\scripts\download-models.ps1`.

Commandes, CLI `vai`, ajout de workflows, règles : voir [`../CLAUDE.md`](../CLAUDE.md).
Chaîne d'un clip musical : [`workflows/music-video/README.md`](workflows/music-video/README.md).
Modèles : [`models/README.md`](models/README.md). Limites : [`docs/limitations.md`](docs/limitations.md).

## Arborescence

```
video-ai/                      (versionné)
  config/      video-ai.json (config), models.json (manifeste + SHA-256), requirements.lock.txt, local.json (ignoré)
  scripts/     start.ps1, stop.ps1, install.ps1, download-models.ps1, common.ps1
  vai/         CLI Python : comfy (API), workflows (jobs/seeds/reprise), audio, storyboard, edit, project, selftest
  workflows/   image-generation/, video-generation/ (*.api.json + *.params.json), music-video/
  assets/      characters/_template (fiche personnage)
  tests/       run-tests.ps1
  docs/        limitations.md
E:\video-ai\                   (runtime, hors Git — VIDEO_AI_HOME)
  ComfyUI\  venv\  models\  wheelhouse\  comfy-io\  projects\<projet>\{storyboard,inputs,renders,exports}
  assets\characters\<slug>\  logs\  run\  cache\miopen\  tests\  backups\
```
