"""audio-ai : separation de stems, analyse BPM/tonalite, recalage tempo/pitch, remix et mastering LUFS.

Chaine (commande `process`) :
  1. decodage FFmpeg -> float32 stereo 44,1 kHz (tous formats : mp3, flac, m4a, wav...)
  2. separation des stems sur le GPU AMD (Demucs v4 htdemucs_ft par defaut, ou BS-Roformer + Demucs)
  3. analyse : BPM sur le stem batterie, tonalite (Krumhansl) sur les stems harmoniques
  4. recalage tempo/pitch stem par stem (Rubber Band via FFmpeg ; formants preserves sur la voix)
  5. remix (gains / mutes par stem), normalisation LUFS (BS.1770) + limiteur true-peak a anticipation
  6. export WAV 24 bits (dither TPDF), mesure du fichier ecrit, rapport <nom>.job.json

GPU : PyTorch ROCm (wheels AMD TheRock gfx103X, Windows natif). L'API torch.cuda pilote le GPU HIP :
le device s'appelle "cuda" mais torch.version.hip prouve qu'il s'agit de ROCm. Aucun repli CPU silencieux :
sans GPU, le traitement s'arrete sauf --device cpu explicite.

  python audio_pipeline.py gpu
  python audio_pipeline.py analyze morceau.mp3
  python audio_pipeline.py separate morceau.mp3 [-o dossier] [--engine demucs|roformer|hybrid] [--model htdemucs_ft]
  python audio_pipeline.py process morceau.mp3 [-o dossier] [--target-bpm 128] [--target-key Am | --semitones -2]
                                  [--gains vocals=-2,drums=+1] [--mute vocals] [--lufs -14] [--ceiling -1] [--out-sr 48000]
  python audio_pipeline.py mix <dossier_stems> -o rendu.wav [--gains ...] [--mute ...] [--target-bpm ...]
  python audio_pipeline.py fetch-models [--model htdemucs_ft]
  python audio_pipeline.py selftest [--with-model]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

# --- Environnement GPU / caches : a definir AVANT l'import de torch --------------------------------------
HOME = Path(os.environ.get("AUDIO_AI_HOME", r"E:\audio-ai"))
os.environ.setdefault("TORCH_HOME", str(HOME / "models" / "torch"))           # poids Demucs (torch.hub)
os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")                             # memes reglages que video-ai
os.environ.setdefault("TORCH_BLAS_PREFER_HIPBLASLT", "0")
os.environ.setdefault("MIOPEN_USER_DB_PATH", str(HOME / "cache" / "miopen"))  # kernels compiles une fois
os.environ.setdefault("MIOPEN_CUSTOM_CACHE_DIR", str(HOME / "cache" / "miopen"))

import numpy as np  # noqa: E402

SR = 44100                       # frequence native des modeles Demucs v4
ANALYSIS_SR = 22050
STEM_ORDER = ("drums", "bass", "other", "vocals", "guitar", "piano", "instrumental")
NOTES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
# Profils de Krumhansl-Kessler (poids perceptifs des 12 degres en majeur / mineur)
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
ROFORMER_DEFAULT = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"


def log(msg: str) -> None:
    """Toute la sortie humaine va sur stderr : stdout reste propre (JSON, protocole MCP)."""
    print(f"[audio-ai] {msg}", file=sys.stderr, flush=True)


def ffmpeg_bin() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg introuvable (winget install Gyan.FFmpeg, ou variable FFMPEG)")
    return exe


def db2lin(db: float) -> float:
    return float(10 ** (db / 20))


def lin2db(x: float) -> float:
    return float(20 * np.log10(max(x, 1e-12)))


# =========================================================================================================
# 1. GPU AMD : detection et preuve d'execution
# =========================================================================================================
def gpu_report(benchmark: bool = True) -> dict:
    """Decrit le GPU vu par PyTorch et prouve qu'il calcule : STFT/iSTFT + conv1d (les operations de Demucs)
    executees sur le GPU, comparees au CPU (ecart numerique et temps)."""
    import torch

    hip = getattr(torch.version, "hip", None)
    info = {"torch": torch.__version__, "backend": "ROCm/HIP" if hip else ("CUDA" if torch.version.cuda else "CPU"),
            "hip_version": hip, "gpu_available": torch.cuda.is_available()}
    if not info["gpu_available"]:
        return info
    p = torch.cuda.get_device_properties(0)
    free, total = torch.cuda.mem_get_info(0)
    info.update(name=p.name, arch=getattr(p, "gcnArchName", None), vram_total_gb=round(total / 2**30, 2),
                vram_free_gb=round(free / 2**30, 2), is_amd=bool(hip) or "radeon" in p.name.lower() or "amd" in p.name.lower())
    if benchmark:
        torch.manual_seed(0)
        x = torch.randn(2, SR * 20)
        conv = torch.nn.Conv1d(2, 48, 8, stride=4)

        def work(dev: str):
            xx, cc = x.to(dev), conv.to(dev)
            win = torch.hann_window(4096, device=dev)
            if dev == "cuda":
                torch.cuda.synchronize()
            t = time.perf_counter()
            for _ in range(5):
                spec = torch.stft(xx, 4096, 1024, window=win, return_complex=True)
                rec = torch.istft(spec, 4096, 1024, window=win, length=xx.shape[-1])
                feat = cc(rec[None])
            if dev == "cuda":
                torch.cuda.synchronize()
            return time.perf_counter() - t, rec.cpu(), feat.detach().cpu(), rec.device.type

        work("cuda")                                   # 1er passage : compilation des kernels MIOpen
        t_gpu, rec_g, feat_g, devtype = work("cuda")
        t_cpu, rec_c, feat_c, _ = work("cpu")
        info["benchmark"] = {"ops": "5x (STFT 4096 + iSTFT + conv1d) sur 20 s stereo",
                             "tensor_device": devtype, "gpu_s": round(t_gpu, 3), "cpu_s": round(t_cpu, 3),
                             "speedup": round(t_cpu / max(t_gpu, 1e-6), 1),
                             "max_abs_err": float(max((rec_g - rec_c).abs().max(), (feat_g - feat_c).abs().max()))}
        info["gpu_ok"] = devtype == "cuda" and info["benchmark"]["max_abs_err"] < 1e-2
    return info


def pick_device(requested: str = "auto") -> str:
    """'auto' exige le GPU : on refuse un repli CPU silencieux (10 a 30x plus lent)."""
    import torch

    if requested == "cpu":
        log("device : CPU (demande explicite)")
        return "cpu"
    if not torch.cuda.is_available():
        raise RuntimeError("aucun GPU visible par PyTorch (ROCm). Verifier le driver AMD, ou relancer avec --device cpu.")
    name = torch.cuda.get_device_name(0)
    log(f"device : GPU {name} | backend {'ROCm/HIP ' + torch.version.hip if torch.version.hip else 'CUDA'}")
    return "cuda"


# =========================================================================================================
# 2. Entrees / sorties audio
# =========================================================================================================
def load_audio(path: Path, sr: int = SR) -> np.ndarray:
    """Decode n'importe quel format via FFmpeg (reechantillonneur soxr) -> float32 (2, n)."""
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-ac", "2",
                        "-af", f"aresample={sr}:resampler=soxr:precision=28", "-f", "f32le", "-"], capture_output=True)
    if r.returncode:
        raise RuntimeError(f"decodage FFmpeg de {path} : {r.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).T.copy()


def write_float(path: Path, x: np.ndarray, sr: int) -> None:
    """Intermediaires en WAV 32 bits flottant : aucune saturation possible avant le mastering."""
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x.T, sr, subtype="FLOAT")


def export_wav24(path: Path, x: np.ndarray, sr: int, dither: bool = True) -> None:
    """Export final PCM 24 bits avec dither TPDF (+-1 LSB) : la quantification ne laisse pas de distorsion
    correlee au signal. Le limiteur a deja garanti la marge true-peak."""
    import soundfile as sf

    lsb = 1.0 / 2**23
    y = x.astype(np.float64)
    if dither:
        rng = np.random.default_rng(0)
        y = y + (rng.random(y.shape) - rng.random(y.shape)) * lsb
    y = np.clip(y, -1.0, 1.0 - lsb)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), y.T, sr, subtype="PCM_24")


def ffmpeg_filter(x: np.ndarray, sr: int, af: str, work: Path, tag: str) -> tuple[np.ndarray, int]:
    """Passe un signal dans une chaine de filtres FFmpeg (rubberband, aresample...) en flottant 32 bits."""
    import soundfile as sf

    work.mkdir(parents=True, exist_ok=True)
    src, dst = work / f"_{tag}_in.wav", work / f"_{tag}_out.wav"
    write_float(src, x, sr)
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-af", af, "-c:a", "pcm_f32le", str(dst)],
                       capture_output=True, text=True, errors="replace")
    try:
        if r.returncode:
            raise RuntimeError(f"FFmpeg -af '{af}' : {r.stderr.strip()}")
        y, sr2 = sf.read(str(dst), dtype="float32", always_2d=True)
        return y.T.copy(), sr2
    finally:
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)


# =========================================================================================================
# 3. Separation des stems (GPU)
# =========================================================================================================
def separate_demucs(mix: np.ndarray, device: str, model_name: str = "htdemucs_ft", shifts: int = 1,
                    overlap: float = 0.25) -> tuple[dict, dict]:
    """Demucs v4 (Hybrid Transformer). htdemucs_ft = 4 modeles specialises (un par stem), meilleur SDR ;
    htdemucs_6s ajoute guitare et piano. Le morceau est decoupe en segments : la VRAM reste bornee."""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model(model_name)
    model.eval()
    # Modele place sur le GPU AVANT apply_model : Demucs remet chaque sous-modele sur son device d'origine
    # en fin de calcul ; ainsi il reste sur le GPU et model_device sert de preuve.
    model.to(device)
    if model.samplerate != SR:
        raise RuntimeError(f"{model_name} attend {model.samplerate} Hz")
    wav = torch.from_numpy(mix)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8                 # normalisation identique a demucs.separate
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    t = time.perf_counter()
    with torch.inference_mode():
        out = apply_model(model, ((wav - mean) / std)[None], device=device, shifts=shifts, split=True,
                          overlap=overlap, progress=True)[0]
    out = out * std + mean
    stats = {"engine": "demucs", "model": model_name, "device": device, "seconds": round(time.perf_counter() - t, 1),
             "model_device": str(next(model.parameters()).device)}
    if device == "cuda":
        stats["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
        if stats["vram_peak_gb"] <= 0:
            raise RuntimeError("aucune allocation GPU pendant la separation : le calcul n'a pas tourne sur le GPU")
        torch.cuda.empty_cache()
    return {name: out[i].numpy().astype(np.float32) for i, name in enumerate(model.sources)}, stats


def separate_roformer(path: Path, work: Path, model_file: str = ROFORMER_DEFAULT) -> tuple[dict, dict]:
    """BS-Roformer (paquet optionnel audio-separator, install.ps1 -WithRoformer) : voix / instrumental,
    SDR voix ~12,9 dB, au-dessus de Demucs pour l'extraction vocale."""
    import torch
    try:
        from audio_separator.separator import Separator
    except ImportError as e:
        raise RuntimeError("Roformer indisponible : relancer audio-ai\\scripts\\install.ps1 -WithRoformer") from e
    work.mkdir(parents=True, exist_ok=True)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t = time.perf_counter()
    sep = Separator(output_dir=str(work), model_file_dir=str(HOME / "models" / "audio-separator"),
                    output_format="WAV", sample_rate=SR)
    sep.load_model(model_filename=model_file)
    files = [Path(f) if Path(f).is_absolute() else work / f for f in sep.separate(str(path))]
    stems = {}
    for f in files:
        key = "vocals" if "(vocals)" in f.name.lower() else "instrumental"
        stems[key] = load_audio(f)
        f.unlink(missing_ok=True)
    stats = {"engine": "roformer", "model": model_file, "seconds": round(time.perf_counter() - t, 1),
             "device": "cuda" if torch.cuda.is_available() else "cpu"}
    if torch.cuda.is_available():
        stats["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    return stems, stats


def separate(path: Path, mix: np.ndarray, stems_dir: Path, engine: str, model: str, device: str,
             shifts: int = 1) -> tuple[dict, dict]:
    """Separe et met en cache dans stems_dir (WAV float). Relancer avec les memes reglages reutilise le cache."""
    import soundfile as sf

    stamp = stems_dir / "stems.json"
    want = {"engine": engine, "model": model, "shifts": shifts, "source": str(path), "source_size": path.stat().st_size}
    if stamp.exists():
        meta = json.loads(stamp.read_text(encoding="utf-8"))
        if meta.get("settings") == want and all((stems_dir / f"{s}.wav").exists() for s in meta["stems"]):
            log(f"stems deja presents ({engine}/{model}) : separation sautee")
            stems = {s: sf.read(str(stems_dir / f"{s}.wav"), dtype="float32", always_2d=True)[0].T.copy() for s in meta["stems"]}
            return stems, {**meta["stats"], "cached": True}
    log(f"separation : {engine} / {model} sur {device}")
    if engine == "demucs":
        stems, stats = separate_demucs(mix, device, model, shifts)
    elif engine == "roformer":
        stems, stats = separate_roformer(path, stems_dir / "_work")
    elif engine == "hybrid":
        # Voix par BS-Roformer, puis Demucs sur l'instrumental pour batterie / basse / autres.
        rof, s1 = separate_roformer(path, stems_dir / "_work")
        dem, s2 = separate_demucs(rof["instrumental"], device, model, shifts)
        dem.pop("vocals", None)
        n = min(rof["vocals"].shape[1], min(v.shape[1] for v in dem.values()))
        stems = {**{k: v[:, :n] for k, v in dem.items()}, "vocals": rof["vocals"][:, :n]}
        stats = {"engine": "hybrid", "roformer": s1, "demucs": s2, "seconds": round(s1["seconds"] + s2["seconds"], 1),
                 "device": device, "vram_peak_gb": max(s1.get("vram_peak_gb", 0), s2.get("vram_peak_gb", 0))}
    else:
        raise ValueError(f"moteur inconnu : {engine}")
    for name, x in stems.items():
        write_float(stems_dir / f"{name}.wav", x, SR)
    stamp.write_text(json.dumps({"settings": want, "stems": sorted(stems), "stats": stats}, indent=2), encoding="utf-8")
    log(f"stems ecrits dans {stems_dir} ({stats['seconds']} s, pic VRAM {stats.get('vram_peak_gb', '-')} Go)")
    return stems, stats


# =========================================================================================================
# 4. Analyse : BPM, tonalite, loudness
# =========================================================================================================
def _mono(x: np.ndarray, sr: int = SR) -> np.ndarray:
    import librosa

    return librosa.resample(x.mean(axis=0), orig_sr=sr, target_sr=ANALYSIS_SR, res_type="soxr_hq")


def detect_bpm(x: np.ndarray, sr: int = SR) -> dict:
    """Tempo par suivi de temps (librosa) sur l'enveloppe d'attaques. Sur le stem batterie, l'enveloppe est
    nette (pas de voix ni de nappes). Le BPM est affine par regression lineaire des instants de
    temps : plus precis que la mediane des intervalles, quantifiee au pas d'analyse (23 ms)."""
    import librosa

    y = _mono(x, sr)
    oenv = librosa.onset.onset_strength(y=y, sr=ANALYSIS_SR, aggregate=np.median)
    tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=ANALYSIS_SR, units="time", start_bpm=120)
    tempo = float(np.atleast_1d(tempo)[0])
    if len(beats) > 8:
        ibi = np.diff(beats)
        bpm = 60.0 / float(np.polyfit(np.arange(len(beats)), beats, 1)[0])
        stability = float(1 - min(1.0, np.std(ibi) / np.mean(ibi) * 4))   # 1 = temps parfaitement reguliers
    else:
        bpm, stability = tempo, 0.0
    return {"bpm": round(bpm, 2), "bpm_librosa": round(tempo, 2), "alternatives": [round(bpm / 2, 2), round(bpm * 2, 2)],
            "beats": len(beats), "first_beat_s": round(float(beats[0]), 3) if len(beats) else None,
            "stability": round(stability, 2)}


def detect_key(x: np.ndarray, sr: int = SR, harmonic_only: bool = False) -> dict:
    """Tonalite : chromagramme CQT (accordage estime) correle aux 24 profils de Krumhansl-Kessler.
    Sans stems, on isole d'abord la partie harmonique (HPSS) pour que la batterie ne biaise pas le chroma."""
    import librosa

    y = _mono(x, sr)
    if not harmonic_only:
        y = librosa.effects.harmonic(y, margin=3.0)
    tuning = float(librosa.estimate_tuning(y=y, sr=ANALYSIS_SR))
    chroma = librosa.feature.chroma_cqt(y=y, sr=ANALYSIS_SR, hop_length=2048, bins_per_octave=36, tuning=tuning)
    energy = librosa.feature.rms(y=y, frame_length=4096, hop_length=2048)[0][: chroma.shape[1]]
    profile = (chroma[:, : len(energy)] * energy).sum(axis=1)       # trames fortes = plus de poids
    scores = []
    for pc in range(12):
        for mode, ref in (("major", KK_MAJOR), ("minor", KK_MINOR)):
            scores.append((float(np.corrcoef(profile, np.roll(ref, pc))[0, 1]), pc, mode))
    scores.sort(reverse=True)
    (r1, pc, mode), (r2, pc2, mode2) = scores[0], scores[1]
    return {"key": f"{NOTES[pc]} {mode}", "tonic": NOTES[pc], "mode": mode, "pitch_class": pc,
            "camelot": camelot(pc, mode), "correlation": round(r1, 3), "confidence": round(r1 - r2, 3),
            "runner_up": f"{NOTES[pc2]} {mode2}", "tuning_cents": round(tuning * 100, 1)}


def camelot(pc: int, mode: str) -> str:
    """Notation Camelot (roue DJ) : les tonalites relatives partagent le meme numero."""
    major_pc = pc if mode == "major" else (pc + 3) % 12
    return f"{(8 + 7 * major_pc) % 12 or 12}{'B' if mode == 'major' else 'A'}"


def parse_key(s: str) -> tuple[int, str]:
    """'Am', 'A minor', 'F#m', 'Bb', 'C# major', 'Ebmin' ou Camelot '8A' -> (classe de hauteur, mode)."""
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})([ABab])", s)
    if m:
        num, mode = int(m.group(1)), "major" if m.group(2).upper() == "B" else "minor"
        major_pc = next(p for p in range(12) if ((8 + 7 * p) % 12 or 12) == num)
        return (major_pc if mode == "major" else (major_pc - 3) % 12), mode
    m = re.fullmatch(r"([A-Ga-g])([#b]?)\s*(m|min|minor|maj|major|M)?", s)
    if not m:
        raise ValueError(f"tonalite illisible : {s!r}")
    pc = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1).upper()]
    pc = (pc + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    return pc, ("minor" if m.group(3) in ("m", "min", "minor") else "major")


def semitones_to_key(src_pc: int, src_mode: str, dst: str) -> tuple[int, str | None]:
    """Decalage minimal (-6..+5 demi-tons) vers la tonalite cible. Un pitch-shift ne change pas le mode :
    si les modes different, on vise la relative (ex. Do majeur -> La mineur = 0 demi-ton)."""
    dst_pc, dst_mode = parse_key(dst)
    note = None
    if dst_mode != src_mode:
        dst_pc = (dst_pc + 3) % 12 if dst_mode == "minor" else (dst_pc - 3) % 12
        note = f"mode different : cible ramenee a la relative {NOTES[dst_pc]} {src_mode}"
    return (dst_pc - src_pc + 6) % 12 - 6, note


def measure_loudness(x: np.ndarray, sr: int) -> dict:
    """Loudness integree ITU-R BS.1770-4 (pyloudnorm) + true peak (sur-echantillonnage x4)."""
    import pyloudnorm as pyln

    lufs = pyln.Meter(sr).integrated_loudness(x.T.astype(np.float64))
    return {"integrated_lufs": round(float(lufs), 2), "true_peak_dbtp": round(lin2db(float(_tp_envelope(x).max())), 2),
            "sample_peak_dbfs": round(lin2db(float(np.abs(x).max())), 2)}


def analyze(mix: np.ndarray, stems: dict | None = None, sr: int = SR) -> dict:
    """BPM sur la batterie et tonalite sur basse + voix + autres quand les stems existent (plus fiable)."""
    t = time.perf_counter()
    rhythm_src = stems["drums"] if stems and "drums" in stems else mix
    harm = [stems[k] for k in ("bass", "other", "vocals", "guitar", "piano", "instrumental") if stems and k in stems]
    res = {"tempo": detect_bpm(rhythm_src, sr), "key": detect_key(sum(harm) if harm else mix, sr, harmonic_only=bool(harm)),
           "loudness": measure_loudness(mix, sr), "duration_s": round(mix.shape[1] / sr, 2),
           "sources": {"tempo": "drums" if rhythm_src is not mix else "mix", "key": "+".join(
               k for k in ("bass", "other", "vocals", "guitar", "piano", "instrumental") if stems and k in stems) or "mix (HPSS)"}}
    res["seconds"] = round(time.perf_counter() - t, 1)
    return res


# =========================================================================================================
# 5. Recalage tempo / pitch (Rubber Band, via FFmpeg --enable-librubberband)
# =========================================================================================================
def tempo_ratio(bpm: float, target: float) -> tuple[float, float]:
    """Ratio de tempo vers la cible, en choisissant l'octave de BPM la plus proche (85 -> 170 plutot que x2)."""
    cand = min((bpm, bpm * 2, bpm / 2), key=lambda b: abs(np.log(target / b)))
    return target / cand, cand


def retime(x: np.ndarray, sr: int, ratio: float, semitones: float, stem: str, work: Path) -> np.ndarray:
    """Time-stretch + pitch-shift haute qualite. Reglages identiques pour tous les stems (meme latence,
    donc pas de decalage de phase au remix) ; seule la voix preserve ses formants (pas d'effet 'chipmunk')."""
    if abs(ratio - 1) < 1e-4 and abs(semitones) < 1e-3:
        return x
    af = (f"rubberband=tempo={ratio:.6f}:pitch={2 ** (semitones / 12):.6f}:pitchq=quality:transients=mixed"
          f":detector=compound:phase=laminar:channels=together:formant={'preserved' if stem == 'vocals' else 'shifted'}")
    y, _ = ffmpeg_filter(x, sr, af, work, f"rb_{stem}")
    return y


# =========================================================================================================
# 6. Mastering : normalisation LUFS + limiteur true-peak (sans saturation)
# =========================================================================================================
def _tp_envelope(x: np.ndarray, os_factor: int = 4, block: int = SR * 30) -> np.ndarray:
    """Crete inter-echantillons par echantillon : sur-echantillonnage x4 (polyphase) par blocs, max sur les
    canaux et les 4 sous-echantillons. Mesure BS.1770 du true peak, sans charger tout le morceau x4 en RAM."""
    from scipy.signal import resample_poly

    n, pad = x.shape[1], 64
    out = np.empty(n, dtype=np.float32)
    for a in range(0, n, block):
        b = min(n, a + block)
        lo, hi = max(0, a - pad), min(n, b + pad)
        up = np.abs(resample_poly(x[:, lo:hi], os_factor, 1, axis=1))
        up = up[:, (a - lo) * os_factor:(a - lo + b - a) * os_factor]
        out[a:b] = up.reshape(x.shape[0], b - a, os_factor).max(axis=(0, 2))
    return out


def true_peak_limit(x: np.ndarray, sr: int, ceiling_dbtp: float, lookahead_ms: float = 2.0,
                    release_ms: float = 120.0) -> tuple[np.ndarray, float]:
    """Limiteur a anticipation (brickwall) sur le true peak.

    1. gain requis r[n] = plafond / crete_TP[n] (<= 1)
    2. minimum glissant sur 2L-1 echantillons : le gain commence a baisser L echantillons AVANT la crete
    3. release exponentiel (en dB) : remontee douce, pas de pompage audible
    4. moyenne glissante causale sur L : courbe sans clic. Chaque valeur moyennee est <= r[n], donc le
       gain lisse reste <= r[n] : aucune crete ne depasse le plafond (pas de saturation).
    """
    from scipy.ndimage import minimum_filter1d

    ceil = db2lin(ceiling_dbtp - 0.05)                      # petite marge de securite
    need = np.minimum(1.0, ceil / np.maximum(_tp_envelope(x), 1e-9)).astype(np.float64)
    if need.min() >= 1.0:
        return x, 0.0
    L = max(1, int(sr * lookahead_ms / 1000))
    h = minimum_filter1d(need, size=2 * L - 1, mode="nearest")
    blk = 32                                               # release calcule par blocs de 32 echantillons
    nb = -(-len(h) // blk)
    hb = np.pad(h, (0, nb * blk - len(h)), mode="edge").reshape(nb, blk).min(axis=1)
    gdb = 20 * np.log10(hb)
    a = float(np.exp(-blk / (sr * release_ms / 1000)))
    g, prev = np.empty_like(gdb), 0.0
    for i, v in enumerate(gdb):                            # ~140k iterations pour 4 min : rapide
        prev = v if v < prev * a else prev * a
        g[i] = prev
    g = np.repeat(g, blk)[: len(h)]
    c = np.concatenate([[0.0], np.cumsum(g)])
    idx = np.arange(len(g))
    g = (c[idx + 1] - c[np.maximum(0, idx + 1 - L)]) / np.minimum(idx + 1, L)
    return (x * (10 ** (g / 20))[None, :]).astype(np.float32), float(-g.min())


def master(x: np.ndarray, sr: int, target_lufs: float = -14.0, ceiling_dbtp: float = -1.0) -> tuple[np.ndarray, dict]:
    """Gain vers la cible LUFS, puis limiteur true-peak ; on compense la loudness perdue par la limitation
    (3 iterations max). Si la cible exige trop de limitation, on le signale au lieu d'ecraser en silence."""
    import pyloudnorm as pyln

    meter = pyln.Meter(sr)
    lufs_in = float(meter.integrated_loudness(x.T.astype(np.float64)))
    if not np.isfinite(lufs_in):
        raise RuntimeError("signal silencieux : normalisation LUFS impossible")
    total_gain = target_lufs - lufs_in
    y = x * db2lin(total_gain)
    for i in range(4):
        limited, gr = true_peak_limit(y, sr, ceiling_dbtp)
        diff = target_lufs - float(meter.integrated_loudness(limited.T.astype(np.float64)))
        if abs(diff) < 0.1 or i == 3:
            break
        y, total_gain = y * db2lin(diff), total_gain + diff   # compense, puis on relimite le signal non limite
    y = limited
    rep = {"input_lufs": round(lufs_in, 2), "target_lufs": target_lufs, "ceiling_dbtp": ceiling_dbtp,
           "gain_db": round(total_gain, 2), "limiter_max_reduction_db": round(gr, 2)}
    if gr > 4:
        rep["warning"] = (f"limitation jusqu'a {gr:.1f} dB : cible trop forte pour ce materiau "
                          f"(baisser --lufs, ex. {target_lufs - round(gr - 2)} LUFS)")
    return y, rep


# =========================================================================================================
# 7. Orchestration : rapport de job, process, mix
# =========================================================================================================
class Job:
    """Rapport <nom>.job.json ecrit a chaque etape (statut lisible pendant le traitement, comme video-ai)."""

    def __init__(self, path: Path, kind: str, params: dict):
        self.path, self.t0 = path, time.perf_counter()
        self.data = {"kind": kind, "status": "running", "params": params, "steps": {},
                     "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getpid()}
        self.save()

    def step(self, name: str, value) -> None:
        self.data["steps"][name] = value
        self.save()

    def finish(self, status: str, **extra) -> dict:
        self.data.update(status=status, seconds=round(time.perf_counter() - self.t0, 1),
                         finished=time.strftime("%Y-%m-%dT%H:%M:%S"), **extra)
        self.save()
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def parse_gains(s: str | None) -> dict:
    """'vocals=-3,drums=+1.5' -> {'vocals': -3.0, 'drums': 1.5} (dB)."""
    out = {}
    for part in filter(None, (p.strip() for p in (s or "").split(","))):
        k, v = part.split("=")
        out[k.strip()] = float(v)
    return out


def render(stems: dict, analysis: dict, args, work: Path, job: Job) -> tuple[np.ndarray, int]:
    """Gains/mutes -> recalage tempo/pitch par stem -> somme -> (reechantillonnage) -> mastering."""
    gains, mute = parse_gains(args.gains), set(filter(None, (args.mute or "").split(",")))
    unknown = (set(gains) | mute) - set(stems)
    if unknown:
        raise ValueError(f"stems inconnus : {sorted(unknown)} (disponibles : {sorted(stems)})")
    ratio, semis, notes = 1.0, float(args.semitones or 0), []
    if args.target_bpm:
        ratio, ref_bpm = tempo_ratio(analysis["tempo"]["bpm"], args.target_bpm)
        if ref_bpm != analysis["tempo"]["bpm"]:
            notes.append(f"BPM interprete a {ref_bpm} (octave la plus proche de la cible)")
        if not 0.5 <= ratio <= 2.0:
            raise ValueError(f"ratio de tempo {ratio:.3f} hors limites (0,5-2)")
        if abs(ratio - 1) > 0.12:
            notes.append(f"ecart de tempo de {abs(ratio - 1):.0%} : artefacts possibles au-dela de ~12 %")
    if args.target_key:
        k = analysis["key"]
        semis, note = semitones_to_key(k["pitch_class"], k["mode"], args.target_key)
        if note:
            notes.append(note)
    if abs(semis) > 4:
        notes.append(f"transposition de {semis:+g} demi-tons : timbre altere au-dela de +-4")
    job.step("transform", {"tempo_ratio": round(ratio, 5), "semitones": semis, "notes": notes,
                           "gains_db": gains, "muted": sorted(mute)})
    for n in notes:
        log(n)

    out = None
    for name in sorted(stems, key=lambda s: STEM_ORDER.index(s) if s in STEM_ORDER else 99):
        if name in mute:
            continue
        log(f"stem {name} : gain {gains.get(name, 0):+.1f} dB, tempo x{ratio:.4f}, {semis:+g} demi-tons")
        y = retime(stems[name] * db2lin(gains.get(name, 0.0)), SR, ratio, semis, name, work)
        if args.export_stems:
            write_float(Path(args.out_dir_resolved) / "stems_processed" / f"{name}.wav", y, SR)
        if out is None:
            out = y
        else:
            n = min(out.shape[1], y.shape[1])                  # quelques echantillons d'ecart max apres stretch
            out = out[:, :n] + y[:, :n]
    if out is None:
        raise ValueError("tous les stems sont mutes")
    sr = SR
    if args.out_sr and args.out_sr != SR:
        out, sr = ffmpeg_filter(out, SR, f"aresample={args.out_sr}:resampler=soxr:precision=28", work, "resample")
    return out, sr


def cmd_process(args, stems_dir_input: Path | None = None) -> dict:
    """process (fichier audio) ou mix (dossier de stems deja separes)."""
    import soundfile as sf

    src = Path(args.input).resolve()
    base = src.stem if stems_dir_input is None else src.name
    out_dir = Path(args.output_dir or (HOME / "projects" / base)).resolve()
    args.out_dir_resolved = str(out_dir)
    out_file = Path(args.out) if getattr(args, "out", None) else out_dir / f"{base}_master.wav"
    params = {k: v for k, v in vars(args).items() if k not in ("func", "out_dir_resolved")}
    job = Job(Path(args.job_file) if args.job_file else out_file.with_suffix(".job.json"), args.cmd, params)
    work = out_dir / "_work"
    try:
        if stems_dir_input is None:
            device = pick_device(args.device)
            if device == "cuda":
                job.step("gpu", gpu_report(benchmark=False))
            mix = load_audio(src)
            job.step("input", {"file": str(src), "duration_s": round(mix.shape[1] / SR, 2), "sample_rate": SR})
            if args.no_separate:
                stems = {"mix": mix}
            else:
                stems, st = separate(src, mix, out_dir / "stems", args.engine, args.model, device, args.shifts)
                job.step("separation", st)
        else:
            files = sorted(p for p in stems_dir_input.glob("*.wav") if not p.name.startswith("_"))
            if not files:
                raise FileNotFoundError(f"aucun stem .wav dans {stems_dir_input}")
            stems = {}
            for f in files:
                y, fsr = sf.read(str(f), dtype="float32", always_2d=True)
                stems[f.stem] = y.T.copy() if fsr == SR else ffmpeg_filter(y.T.copy(), fsr, f"aresample={SR}:resampler=soxr", work, f.stem)[0]
            n = min(v.shape[1] for v in stems.values())
            stems = {k: v[:, :n] for k, v in stems.items()}
            mix = sum(stems.values())
            job.step("input", {"stems_dir": str(stems_dir_input), "stems": sorted(stems)})

        analysis = analyze(mix, None if "mix" in stems else stems)
        job.step("analysis", analysis)
        log(f"analyse : {analysis['tempo']['bpm']} BPM, {analysis['key']['key']} ({analysis['key']['camelot']}), "
            f"{analysis['loudness']['integrated_lufs']} LUFS")
        (out_dir / "analysis.json").parent.mkdir(parents=True, exist_ok=True)
        (out_dir / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")

        rendered, sr = render(stems, analysis, args, work, job)
        mastered, mrep = master(rendered, sr, args.lufs, args.ceiling)
        job.step("mastering", mrep)
        export_wav24(out_file, mastered, sr, dither=not args.no_dither)

        # Verification sur le fichier reellement ecrit (relu depuis le disque)
        y, fsr = sf.read(str(out_file), dtype="float32", always_2d=True)
        info = sf.info(str(out_file))
        check = {"file": str(out_file), "format": f"{info.format}/{info.subtype}", "sample_rate": fsr,
                 "channels": info.channels, "duration_s": round(info.duration, 2), **measure_loudness(y.T, fsr)}
        check["ceiling_respected"] = check["true_peak_dbtp"] <= args.ceiling + 0.1
        log(f"export : {out_file} | {check['integrated_lufs']} LUFS | TP {check['true_peak_dbtp']} dBTP | {info.subtype}")
        shutil.rmtree(work, ignore_errors=True)
        return job.finish("done", output=check)
    except Exception as e:
        job.finish("error", error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
        raise


def cmd_separate(args) -> dict:
    src = Path(args.input).resolve()
    out_dir = Path(args.output_dir or (HOME / "projects" / src.stem)).resolve()
    job = Job(Path(args.job_file) if args.job_file else out_dir / f"{src.stem}_separate.job.json", "separate",
              {k: v for k, v in vars(args).items() if k != "func"})
    try:
        device = pick_device(args.device)
        mix = load_audio(src)
        stems, st = separate(src, mix, out_dir / "stems", args.engine, args.model, device, args.shifts)
        job.step("separation", st)
        return job.finish("done", output={"stems_dir": str(out_dir / "stems"),
                                          "stems": {k: str(out_dir / "stems" / f"{k}.wav") for k in stems}})
    except Exception as e:
        job.finish("error", error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
        raise


def cmd_selftest(args) -> dict:
    """Signal synthetique (120 BPM, La mineur) : verifie GPU, BPM, tonalite, Rubber Band (alignement des
    stems), mastering et export 24 bits. --with-model ajoute une vraie separation Demucs sur le GPU."""
    import soundfile as sf

    res, ok = {}, True
    res["gpu"] = gpu_report()
    ok &= bool(res["gpu"].get("gpu_ok"))
    sr, dur, bpm = SR, 12.0, 120.0
    t = np.arange(int(sr * dur)) / sr
    drums = np.zeros_like(t)
    for b in np.arange(0, dur, 60 / bpm):                 # grosse caisse sur chaque temps
        i = int(b * sr)
        seg = t[: int(0.15 * sr)]
        drums[i:i + len(seg)] += (np.sin(2 * np.pi * 55 * seg * (1 + 2 * np.exp(-seg * 40))) * np.exp(-seg * 25))[: len(drums) - i]
    chord = sum(np.sin(2 * np.pi * f * t) * a for f, a in ((110, .5), (220, .4), (261.63, .3), (329.63, .3), (440, .2)))
    stems = {"drums": np.stack([drums, drums]).astype(np.float32) * 0.6,
             "other": np.stack([chord, chord]).astype(np.float32) * 0.15}
    mix = stems["drums"] + stems["other"]
    a = analyze(mix, stems)
    res["analysis"] = {"bpm": a["tempo"]["bpm"], "key": a["key"]["key"], "camelot": a["key"]["camelot"]}
    ok &= abs(a["tempo"]["bpm"] - 120) < 2 and a["key"]["key"] == "A minor"
    work = HOME / "run" / "selftest"
    # Alignement : les stems etires separement doivent rester en phase (meme latence Rubber Band)
    d2 = retime(stems["drums"], sr, 126 / 120, 2, "drums", work)
    v2 = retime(stems["drums"], sr, 126 / 120, 2, "vocals", work)
    W, N = 200, sr * 2
    ref = d2[0, W:W + N]
    lag = max(range(-W, W + 1), key=lambda l: float(np.dot(ref, v2[0, W + l:W + l + N])))
    res["rubberband"] = {"length_ratio": round(d2.shape[1] / mix.shape[1], 4), "expected": round(120 / 126, 4),
                         "stem_alignment_samples": lag}
    ok &= abs(res["rubberband"]["length_ratio"] - 120 / 126) < 0.01 and abs(lag) <= 2
    m, rep = master(mix * 0.3, sr, -14, -1)
    out = work / "selftest_master.wav"
    export_wav24(out, m, sr)
    y, _ = sf.read(str(out), dtype="float32", always_2d=True)
    res["mastering"] = {**rep, "written": measure_loudness(y.T, sr), "subtype": sf.info(str(out)).subtype}
    ok &= abs(res["mastering"]["written"]["integrated_lufs"] + 14) < 0.5 and res["mastering"]["written"]["true_peak_dbtp"] <= -0.9
    ok &= res["mastering"]["subtype"] == "PCM_24"
    if args.with_model:
        st = separate_demucs(mix, "cuda", args.model)[1]
        res["demucs"] = st
        ok &= st.get("vram_peak_gb", 0) > 0 and st["model_device"].startswith("cuda")
    shutil.rmtree(work, ignore_errors=True)
    res["status"] = "pass" if ok else "fail"
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common_sep(p):
        p.add_argument("--engine", choices=("demucs", "roformer", "hybrid"), default="demucs")
        p.add_argument("--model", default="htdemucs_ft", help="htdemucs_ft (4 stems, qualite max), htdemucs, htdemucs_6s")
        p.add_argument("--shifts", type=int, default=1, help="moyenne sur N decalages : +qualite, xN temps")
        p.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")

    def common_render(p):
        p.add_argument("--target-bpm", type=float)
        g = p.add_mutually_exclusive_group()
        g.add_argument("--target-key", help="ex. Am, 'F# minor', Bb, 8A")
        g.add_argument("--semitones", type=float)
        p.add_argument("--gains", help="dB par stem : vocals=-3,drums=+1")
        p.add_argument("--mute", help="stems a retirer : vocals (karaoke), drums...")
        p.add_argument("--lufs", type=float, default=-14.0, help="-14 streaming, -9 club, -23 broadcast EBU R128")
        p.add_argument("--ceiling", type=float, default=-1.0, help="plafond true peak en dBTP")
        p.add_argument("--out-sr", type=int, help="frequence de sortie (44100 par defaut, 48000 pour la video)")
        p.add_argument("--no-dither", action="store_true")
        p.add_argument("--export-stems", action="store_true", help="ecrit aussi les stems transformes")
        p.add_argument("--job-file")

    p = sub.add_parser("gpu", help="verifie le GPU AMD et mesure GPU vs CPU")
    p = sub.add_parser("analyze", help="BPM, tonalite, loudness (sans separation)")
    p.add_argument("input")
    p = sub.add_parser("separate", help="separe les stems")
    p.add_argument("input"); p.add_argument("-o", "--output-dir"); p.add_argument("--job-file"); common_sep(p)
    p = sub.add_parser("process", help="chaine complete")
    p.add_argument("input"); p.add_argument("-o", "--output-dir"); p.add_argument("--out", help="fichier WAV final")
    p.add_argument("--no-separate", action="store_true", help="traite le mix tel quel (pas de stems)")
    common_sep(p); common_render(p)
    p = sub.add_parser("mix", help="recombine un dossier de stems")
    p.add_argument("input", help="dossier contenant drums.wav, bass.wav...")
    p.add_argument("-o", "--out", required=True); p.add_argument("--output-dir")
    common_render(p)
    p = sub.add_parser("fetch-models", help="telecharge les poids Demucs")
    p.add_argument("--model", default="htdemucs_ft")
    p = sub.add_parser("selftest")
    p.add_argument("--with-model", action="store_true"); p.add_argument("--model", default="htdemucs")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "gpu":
            res = gpu_report()
        elif args.cmd == "analyze":
            res = analyze(load_audio(Path(args.input)))
        elif args.cmd == "separate":
            res = cmd_separate(args)
        elif args.cmd == "process":
            res = cmd_process(args)
        elif args.cmd == "mix":
            args.device, args.no_separate = "cpu", False
            if not args.output_dir:
                args.output_dir = str(Path(args.out).resolve().parent)
            res = cmd_process(args, stems_dir_input=Path(args.input).resolve())
        elif args.cmd == "fetch-models":
            from demucs.pretrained import get_model
            get_model(args.model)
            res = {"model": args.model, "torch_home": os.environ["TORCH_HOME"]}
        elif args.cmd == "selftest":
            res = cmd_selftest(args)
    except Exception as e:
        log(f"ERREUR : {type(e).__name__}: {e}")
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0 if not (isinstance(res, dict) and res.get("status") == "fail") else 1


if __name__ == "__main__":
    sys.exit(main())
