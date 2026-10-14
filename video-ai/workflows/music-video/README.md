# Clip musical : chaîne complète

Pas de graphe ComfyUI unique : un clip enchaîne plusieurs workflows, pilotés par la CLI `vai`.

```powershell
.\video-ai\vai.ps1 project new mon-clip
.\video-ai\vai.ps1 character new "Lena Voss"          # puis éditer E:\video-ai\assets\characters\lena-voss\character.json
.\video-ai\vai.ps1 storyboard mon-clip C:\musique\titre.mp3 --format 9x16 --character lena-voss --start 30 --end 60
#   -> E:\video-ai\projects\mon-clip\storyboard\storyboard.json : remplir keyframe_prompt et video_prompt de chaque plan
.\video-ai\vai.ps1 render keyframes mon-clip --use-ref   # FLUX.2 Klein (+ référence d'identité)
.\video-ai\vai.ps1 render clips mon-clip                 # Wan 2.2 TI2V 5B, image -> vidéo
.\video-ai\vai.ps1 assemble mon-clip --format 9x16 16x9 --codec h264
```

| Étape | Entrée | Sortie |
|---|---|---|
| Analyse audio | musique | `storyboard/cues.json` : durée, loudness, silences, tempo, temps, mesures, changements d'énergie |
| Storyboard | `cues.json` | `storyboard.json` et `.md` : plans calés sur les changements d'énergie puis sur les mesures, de 2 à 5 s (paramétrable) |
| Images-clés | `keyframe_prompt` (+ fiche personnage) | `renders/keyframes/<plan>-keyframe.job.json` + PNG |
| Clips | image-clé + `video_prompt` | `renders/clips/<plan>-clip.job.json` + MP4 (24 fps, 4n+1 images, ≤ 121) |
| Montage | plans + musique | `exports/<projet>_<format>_<codec>.mp4`, loudness -14 LUFS, H.264 ou H.265 (AMF si disponible) |

- **Reprise** : `render` saute les plans déjà rendus. Un job échoué se relance à l'identique avec
  `vai resume <fichier.job.json>`.
- **Plan sans clip** : `assemble` utilise l'image-clé fixe. Un clip trop court est prolongé par sa dernière image.
- **Formats** : `--mode fill` recadre ; `--mode blur` place l'image entière sur un fond flou (utile pour
  passer du 16:9 au 9:16). Mieux vaut générer directement au bon ratio : `--format 9x16` au storyboard
  donne des plans en 704x1280.
- **Finition** : les exports se suffisent à eux-mêmes. DaVinci Resolve reste optionnel pour l'étalonnage
  et les titres : importer les clips de `renders/clips`.
