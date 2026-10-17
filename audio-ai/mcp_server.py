"""Serveur MCP (stdio) d'audio-ai pour Claude Desktop / Claude Code.

Les traitements longs (separation, chaine complete) tournent dans un sous-processus audio_pipeline.py :
  - la VRAM est liberee a la fin de chaque job (pas de modele qui reste en memoire a cote de ComfyUI) ;
  - un seul job GPU a la fois (file d'attente) ;
  - l'outil rend la main tout de suite avec un job_id : le client n'atteint pas son timeout.
Suivi : job_status(job_id) lit le <job>.job.json ecrit a chaque etape par le pipeline.
stdout est reserve au protocole MCP : les journaux vont dans <AUDIO_AI_HOME>\\logs.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "audio_pipeline.py"
HOME = Path(os.environ.get("AUDIO_AI_HOME", r"E:\audio-ai"))
JOBS_DIR = HOME / "run" / "jobs"
LOGS = HOME / "logs"

mcp = FastMCP("audio-ai")
_queue: "queue.Queue[str]" = queue.Queue()
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _run_sync(args: list[str], timeout: int = 600) -> dict:
    """Commande courte (gpu, analyze) : sortie JSON sur stdout, journal sur stderr."""
    r = subprocess.run([sys.executable, str(PIPELINE), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-2000:] or f"code {r.returncode}")
    return json.loads(r.stdout)


def _worker() -> None:
    while True:
        jid = _queue.get()
        job = _jobs[jid]
        LOGS.mkdir(parents=True, exist_ok=True)
        with _lock:
            job["status"] = "running"
            job["started"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(job["log"], "w", encoding="utf-8") as log:
            p = subprocess.run([sys.executable, str(PIPELINE), *job["argv"], "--job-file", job["job_file"]],
                               stdout=log, stderr=subprocess.STDOUT)
        with _lock:
            job["returncode"] = p.returncode
            job["status"] = "done" if p.returncode == 0 else "error"
        _queue.task_done()


threading.Thread(target=_worker, daemon=True).start()


def _submit(kind: str, argv: list[str]) -> dict:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        _jobs[jid] = {"id": jid, "kind": kind, "status": "queued", "argv": argv,
                      "job_file": str(JOBS_DIR / f"{jid}.job.json"), "log": str(LOGS / f"mcp-{jid}.log")}
    _queue.put(jid)
    return {"job_id": jid, "status": "queued", "position": _queue.qsize(),
            "hint": "suivre avec job_status(job_id) ; une separation htdemucs_ft prend ~1 a 3 min par morceau"}


def _opt(flag: str, value) -> list[str]:
    return [] if value in (None, "", []) else [flag, str(value)]


def _gains(stem_gains_db: dict[str, float] | None) -> str | None:
    return ",".join(f"{k}={v:+g}" for k, v in (stem_gains_db or {}).items()) or None


@mcp.tool()
def gpu_status() -> dict:
    """Verifie que PyTorch voit le GPU AMD via ROCm et mesure STFT/conv1d GPU contre CPU (preuve d'execution)."""
    return _run_sync(["gpu"])


@mcp.tool()
def analyze_track(path: str) -> dict:
    """Analyse rapide d'un fichier audio sans separation : BPM, tonalite (+ Camelot), loudness LUFS / true peak."""
    return _run_sync(["analyze", path])


@mcp.tool()
def separate_stems(path: str, output_dir: str | None = None, engine: str = "demucs",
                   model: str = "htdemucs_ft", shifts: int = 1) -> dict:
    """Lance la separation des stems sur le GPU (job asynchrone).
    engine : demucs (drums/bass/other/vocals), roformer (vocals/instrumental, BS-Roformer),
    hybrid (voix Roformer + Demucs sur l'instrumental). model : htdemucs_ft | htdemucs | htdemucs_6s.
    Les stems (WAV 32 bits flottant) vont dans <output_dir>/stems (defaut E:\\audio-ai\\projects\\<nom>)."""
    return _submit("separate", ["separate", path, "--engine", engine, "--model", model, "--shifts", str(shifts),
                                *_opt("-o", output_dir)])


@mcp.tool()
def process_track(path: str, output_dir: str | None = None, target_bpm: float | None = None,
                  target_key: str | None = None, semitones: float | None = None,
                  stem_gains_db: dict[str, float] | None = None, mute_stems: list[str] | None = None,
                  target_lufs: float = -14.0, ceiling_dbtp: float = -1.0, out_sample_rate: int | None = None,
                  engine: str = "demucs", model: str = "htdemucs_ft", export_stems: bool = False) -> dict:
    """Chaine complete (job asynchrone) : separation GPU -> BPM/tonalite -> recalage tempo (target_bpm) et
    pitch (target_key ex. 'Am', '8A' ou semitones) -> remix (stem_gains_db {'vocals': -3}, mute_stems
    ['vocals'] pour un karaoke) -> normalisation LUFS + limiteur true-peak -> WAV 24 bits."""
    if target_key and semitones is not None:
        raise ValueError("target_key et semitones sont exclusifs")
    argv = ["process", path, "--engine", engine, "--model", model, "--lufs", str(target_lufs),
            "--ceiling", str(ceiling_dbtp), *_opt("-o", output_dir), *_opt("--target-bpm", target_bpm),
            *_opt("--target-key", target_key), *_opt("--semitones", semitones),
            *_opt("--gains", _gains(stem_gains_db)), *_opt("--mute", ",".join(mute_stems or [])),
            *_opt("--out-sr", out_sample_rate)]
    if export_stems:
        argv.append("--export-stems")
    return _submit("process", argv)


@mcp.tool()
def mix_stems(stems_dir: str, output_file: str, stem_gains_db: dict[str, float] | None = None,
              mute_stems: list[str] | None = None, target_bpm: float | None = None, target_key: str | None = None,
              semitones: float | None = None, target_lufs: float = -14.0, ceiling_dbtp: float = -1.0) -> dict:
    """Recombine un dossier de stems WAV (drums.wav, bass.wav, vocals.wav...) avec gains, mutes,
    recalage tempo/pitch, puis mastering LUFS et export WAV 24 bits (job asynchrone, sans GPU)."""
    argv = ["mix", stems_dir, "-o", output_file, "--lufs", str(target_lufs), "--ceiling", str(ceiling_dbtp),
            *_opt("--target-bpm", target_bpm), *_opt("--target-key", target_key), *_opt("--semitones", semitones),
            *_opt("--gains", _gains(stem_gains_db)), *_opt("--mute", ",".join(mute_stems or []))]
    return _submit("mix", argv)


@mcp.tool()
def job_status(job_id: str) -> dict:
    """Etat d'un job : queued / running / done / error, etapes franchies, analyse, mesures du fichier final."""
    with _lock:
        job = dict(_jobs.get(job_id) or {})
    jf = Path(job.get("job_file") or JOBS_DIR / f"{job_id}.job.json")
    if not job and not jf.exists():
        return {"job_id": job_id, "status": "unknown"}
    report = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
    res = {"job_id": job_id, "status": job.get("status", report.get("status")), "report": report}
    if res["status"] == "error" and job.get("log") and Path(job["log"]).exists():
        res["log_tail"] = Path(job["log"]).read_text(encoding="utf-8", errors="replace")[-3000:]
    return res


@mcp.tool()
def list_jobs() -> list[dict]:
    """Jobs de cette session du serveur (les plus recents en dernier)."""
    with _lock:
        return [{k: j.get(k) for k in ("id", "kind", "status", "job_file")} for j in _jobs.values()]


if __name__ == "__main__":
    mcp.run()
