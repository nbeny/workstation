"""Analyse musicale avec FFmpeg + numpy (pas de librosa : aucune dependance lourde).

Produit un fichier de reperes (cues) : metadonnees, duree, loudness, silences, tempo estime,
temps (beats), mesures, courbe d'energie, sections et changements d'energie.
Les estimations de tempo/beats sont heuristiques : a verifier a l'oreille pour les morceaux complexes.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

SR = 22050
HOP = 512
NFFT = 2048


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe(path: Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    if r.returncode:
        raise RuntimeError(f"ffprobe : {r.stderr.strip()}")
    data = json.loads(r.stdout)
    fmt = data.get("format", {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    return {
        "file": str(path), "duration": float(fmt.get("duration", 0)), "format": fmt.get("format_name"),
        "bitrate": int(fmt.get("bit_rate", 0) or 0), "codec": a.get("codec_name"),
        "sample_rate": int(a.get("sample_rate", 0) or 0), "channels": a.get("channels"),
        "title": tags.get("title"), "artist": tags.get("artist"), "album": tags.get("album"),
    }


def silences(path: Path, noise_db: float = -35, min_dur: float = 0.5) -> list[dict]:
    r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
              f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [{"start": round(s, 3), "end": round(e, 3)} for s, e in zip(starts, ends)]


def loudness(path: Path) -> dict:
    r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
              "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return {}
    d = json.loads(m.group(0))
    return {"integrated_lufs": float(d["input_i"]), "true_peak_db": float(d["input_tp"]),
            "lra": float(d["input_lra"]), "threshold": float(d["input_thresh"])}


def decode(path: Path) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode:
        raise RuntimeError(f"decodage audio : {r.stderr.decode(errors='replace')}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def _frames(y: np.ndarray) -> np.ndarray:
    n = 1 + max(0, (len(y) - NFFT) // HOP)
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    return y[idx] * np.hanning(NFFT)[None, :].astype(np.float32)


def _smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    k = np.ones(win) / win
    return np.convolve(x, k, mode="same")


def _peaks(x: np.ndarray, min_dist: int, thresh: float) -> np.ndarray:
    cand = np.where((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:]) & (x[1:-1] > thresh))[0] + 1
    keep = []
    for i in cand[np.argsort(-x[cand])]:
        if all(abs(i - j) >= min_dist for j in keep):
            keep.append(i)
    return np.array(sorted(keep), dtype=int)


def analyze(path: Path) -> dict:
    path = Path(path)
    meta = probe(path)
    y = decode(path)
    fps = SR / HOP  # trames d'analyse par seconde
    fr = _frames(y)
    spec = np.abs(np.fft.rfft(fr, axis=1))
    rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-9)

    # Enveloppe d'attaques : flux spectral (log) positif
    logspec = np.log1p(100 * spec)
    flux = np.maximum(0, np.diff(logspec, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    onset = flux - _smooth(flux, int(fps * 0.5))
    onset = np.maximum(onset, 0)
    onset /= onset.max() + 1e-9

    # Tempo par autocorrelation (60-180 BPM), ponderee autour de 120 BPM
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    lags = np.arange(len(ac))
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 60)
    bpm_axis = 60 * fps / np.maximum(lags[lo:hi], 1)
    weight = np.exp(-0.5 * (np.log2(bpm_axis / 120) / 0.9) ** 2)
    best = lo + int(np.argmax(ac[lo:hi] * weight))
    bpm = 60 * fps / best
    period = best

    # Grille de beats : phase qui maximise l'energie d'attaque, puis recalage local (+-10 %)
    phases = [onset[p::period].sum() for p in range(period)]
    phase = int(np.argmax(phases))
    beats, t = [], phase
    tol = max(1, int(period * 0.1))
    while t < len(onset):
        a, b = max(0, t - tol), min(len(onset), t + tol + 1)
        t_adj = a + int(np.argmax(onset[a:b]))
        beats.append(t_adj)
        t = t_adj + period
    beat_times = [round(b / fps, 3) for b in beats]

    # Mesures (4 temps) : on choisit le decalage dont les temps 1 ont le plus d'energie
    if len(beats) >= 8:
        strength = [np.mean([onset[b] for b in beats[o::4]]) for o in range(4)]
        off = int(np.argmax(strength))
    else:
        off = 0
    bar_times = beat_times[off::4]

    # Energie lissee (fenetre 1 s) et changements d'energie (nouveaute sur +-4 s)
    energy = _smooth(rms_db, int(fps))
    w = int(fps * 4)
    nov = np.zeros_like(energy)
    for i in range(w, len(energy) - w, max(1, int(fps / 4))):
        nov[i] = abs(energy[i:i + w].mean() - energy[i - w:i].mean())
    nov = _smooth(nov, int(fps / 2))
    thr = max(2.0, float(np.percentile(nov[nov > 0], 75)) if (nov > 0).any() else 2.0)
    change_idx = _peaks(nov, int(fps * 6), thr)
    changes = [round(float(i) / fps, 3) for i in change_idx]

    # Sections entre changements, niveau d'energie relatif (terciles)
    bounds = [0.0] + changes + [round(meta["duration"] or len(y) / SR, 3)]
    sec_e = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = energy[int(a * fps):max(int(a * fps) + 1, int(b * fps))]
        sec_e.append(float(seg.mean()) if len(seg) else -90.0)
    q1, q2 = (np.percentile(sec_e, [33, 66]) if len(sec_e) > 2 else (min(sec_e), max(sec_e)))
    sections = [{"start": a, "end": b, "energy_db": round(e, 1),
                 "level": "high" if e >= q2 else ("low" if e <= q1 else "mid")}
                for a, b, e in zip(bounds[:-1], bounds[1:], sec_e)]

    step = max(1, int(fps / 2))
    return {
        "meta": meta,
        "loudness": loudness(path),
        "silences": silences(path),
        "tempo_bpm": round(float(bpm), 1),
        "beats": beat_times,
        "bars": bar_times,
        "energy_changes": changes,
        "sections": sections,
        "energy_curve": {"step_s": round(step / fps, 3),
                         "db": [round(float(v), 1) for v in energy[::step]]},
        "notes": "tempo/beats estimes (autocorrelation du flux spectral) : verifier a l'oreille.",
    }
