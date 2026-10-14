# Modèles

Les poids ne sont **jamais** versionnés. Ils vivent dans `E:\video-ai\models\` (surchargeable par
`VIDEO_AI_MODELS` ou `config/local.json` → `models_dir`). ComfyUI les trouve grâce au fichier
`E:\video-ai\run\extra_model_paths.yaml`, généré par `scripts/install.ps1`.

Manifeste : [`../config/models.json`](../config/models.json) (URL, taille, SHA-256).

| Groupe | Fichiers | Taille | Licence |
|---|---|---|---|
| `flux2-klein-4b` | `diffusion_models/flux-2-klein-4b-fp8.safetensors`, `text_encoders/qwen_3_4b.safetensors`, `vae/flux2-vae.safetensors` | 12,4 Go | Apache-2.0 |
| `wan22-ti2v-5b` | `diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`, `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`, `vae/wan2.2_vae.safetensors` | 18,1 Go | Apache-2.0 |

```powershell
pwsh -File video-ai\scripts\download-models.ps1 -List                    # état
pwsh -File video-ai\scripts\download-models.ps1 -Group flux2-klein-4b    # installer un groupe
pwsh -File video-ai\scripts\download-models.ps1 -Verify                  # re-vérifier les SHA-256
```

Le téléchargement est idempotent : il reprend un fichier interrompu (`.part`), vérifie le SHA-256 avant
de mettre le fichier en place, et n'écrase jamais un fichier existant de taille inattendue.

## Écartés (et pourquoi)

- **FLUX.2 Klein 9B** : licence non commerciale, et trop juste pour 16 Go avec l'encodeur Qwen3-8B.
- **Wan 2.2 A14B (T2V/I2V)** : deux experts de 14B, irréaliste sur 16 Go RDNA2 sans quantification GGUF,
  qui demanderait des custom nodes.
- **PuLID / IP-Adapter** : conçus pour FLUX.1 et SDXL, non compatibles FLUX.2. La cohérence passe par
  `ReferenceLatent` (natif).
