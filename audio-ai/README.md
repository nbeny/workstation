# audio-ai — séparation, analyse et mastering audio sur GPU AMD

Pipeline local et gratuit : séparation de stems (Demucs v4, option BS-Roformer), BPM et tonalité,
recalage tempo/pitch, remix, normalisation LUFS avec limiteur true-peak, export WAV 24 bits.
Pilotable en CLI (`aai.ps1`) ou par Claude via un serveur MCP.

## Choix du backend GPU (RX 6900 XT, gfx1030)

| Option | Verdict |
|---|---|
| **PyTorch ROCm natif Windows (TheRock gfx103X)** | **Retenu.** Même build que `video-ai` (déjà validé sur cette carte), STFT/iSTFT et conv1d sur GPU, aucune couche de traduction. |
| torch-directml | Écarté : bloqué sur d'anciennes versions de PyTorch, en maintenance côté Microsoft, et sans tenseurs complexes. `torch.stft` (utilisé par Demucs) repasse sur le CPU ou échoue. |
| WSL2 + ROCm | Écarté : ROCm sous WSL ne prend en charge que RDNA3/RDNA4, pas RDNA2. |

Le device PyTorch s'appelle `cuda`, mais `torch.version.hip` montre qu'il s'agit de ROCm (`aai.ps1 gpu`).
Sans GPU, le pipeline **s'arrête** : il ne repasse jamais en CPU en silence (il faut `--device cpu` explicite).

## Installation

Intégrée à `setup.ps1` (étape 11, `-SkipAudioAI` pour l'ignorer, `-AudioAIRoformer` pour ajouter Roformer).
Seule :

```powershell
pwsh -File audio-ai\scripts\install.ps1                 # idempotent
pwsh -File audio-ai\scripts\install.ps1 -WithRoformer   # + python-audio-separator (BS-Roformer)
```

Le script crée `E:\audio-ai\venv` (Python 3.12), y installe torch/torchaudio ROCm depuis le wheelhouse de
video-ai et les dépendances avec torch verrouillé. Il contrôle les DLL avec Smart App Control, télécharge
les poids Demucs, lance un selftest réel sur le GPU, puis enregistre le serveur MCP dans Claude Desktop
(avec une sauvegarde `.bak-<horodatage>`) et dans Claude Code (portée utilisateur).

## Utilisation

```powershell
.\audio-ai\aai.ps1 gpu                                   # GPU vu par PyTorch + benchmark GPU/CPU
.\audio-ai\aai.ps1 analyze morceau.mp3                   # BPM, tonalité (+ Camelot), LUFS, true peak
.\audio-ai\aai.ps1 separate morceau.mp3                  # stems -> E:\audio-ai\projects\<nom>\stems
.\audio-ai\aai.ps1 process morceau.mp3 --target-bpm 128 --target-key Am --gains vocals=-2 --lufs -14
.\audio-ai\aai.ps1 process morceau.mp3 --mute vocals     # instrumental / karaoké
.\audio-ai\aai.ps1 mix E:\audio-ai\projects\x\stems -o remix.wav --gains drums=+2 --semitones -1
.\audio-ai\aai.ps1 selftest --with-model
```

Sorties dans `E:\audio-ai\projects\<nom>\` : `stems\*.wav` (float 32 bits, en cache), `analysis.json`,
`<nom>_master.wav` (PCM 24 bits) et `<nom>_master.job.json` (statut, device, pic VRAM, mesures du fichier écrit).

## Choix techniques

- **Séparation** : `htdemucs_ft` par défaut (4 modèles spécialisés). `hybrid` sépare la voix avec BS-Roformer
  (meilleur SDR sur la voix), puis passe l'instrumental dans Demucs pour la batterie, la basse et le reste.
- **BPM** : suivi de temps librosa sur le **stem batterie**, affiné par régression linéaire des instants de temps. Pour une
  cible lointaine, l'octave la plus proche est choisie (85 → 170).
- **Tonalité** : chroma CQT (avec estimation de l'accordage) des stems harmoniques, corrélé aux profils de
  Krumhansl-Kessler, avec un indice de confiance. Un pitch-shift ne change pas le mode : une cible de mode
  différent est ramenée à sa relative.
- **Tempo/pitch** : Rubber Band (filtre FFmpeg) stem par stem, avec des réglages identiques (même latence,
  pas de déphasage au remix). Formants préservés sur la voix.
- **Mastering** : loudness BS.1770-4 (pyloudnorm), puis limiteur à anticipation sur le true peak (suréchantillonnage x4).
  Avec un gain lissé qui reste sous la valeur requise, aucun échantillon ne dépasse le plafond. Si la cible exige plus de
  4 dB de limitation, le rapport le signale.
- **Export** : WAV PCM 24 bits avec dither TPDF. Le fichier écrit est relu et mesuré (LUFS, true peak).
- **PyDub** n'est pas utilisé : il travaille en entiers 16 bits et `audioop` a disparu de Python 3.13.
  FFmpeg et soundfile couvrent ses usages sans perte.

## Limites

- Un seul gros modèle en VRAM à la fois : arrêter ComfyUI (`video-ai\scripts\stop.ps1`) pendant un Wan.
  Demucs consomme peu de VRAM, mais Wan en prend 15,5 Go.
- La première séparation compile les kernels MIOpen (lente) ; ils sont ensuite en cache dans `E:\audio-ai\cache\miopen`.
- Au-delà de ±12 % de tempo ou ±4 demi-tons, les artefacts deviennent audibles (signalés dans le rapport).
