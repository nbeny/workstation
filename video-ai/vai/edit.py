"""Montage automatise avec FFmpeg : plans -> segments normalises -> concat -> musique -> export.

- coupes calees sur les bornes du storyboard (donc sur les reperes musicaux)
- sorties 9:16 (1080x1920) et 16:9 (1920x1080), mode 'fill' (recadrage) ou 'blur' (fond flou)
- normalisation loudness EBU R128 en deux passes (-14 LUFS, -1 dBTP par defaut)
- encodeur materiel AMD AMF si fonctionnel, sinon libx264/libx265
DaVinci Resolve reste optionnel pour la finition : ces exports sont autonomes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SIZES = {"9x16": (1080, 1920), "16x9": (1920, 1080), "1x1": (1080, 1080)}
_enc_cache: dict[str, bool] = {}


def _ff(args: list[str], what: str):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(f"ffmpeg ({what}) : {r.stderr.strip()[-2000:]}")
    return r


def encoder_works(name: str) -> bool:
    if name not in _enc_cache:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=320x240:d=0.3", "-c:v", name, "-f", "null", "-"],
                           capture_output=True)
        _enc_cache[name] = r.returncode == 0
    return _enc_cache[name]


def video_codec_args(codec: str = "h264", hw: str = "auto") -> tuple[str, list[str]]:
    if codec not in ("h264", "h265"):
        raise ValueError("codec : h264 ou h265")
    amf = "h264_amf" if codec == "h264" else "hevc_amf"
    if hw in ("auto", "amf") and encoder_works(amf):
        return amf, ["-c:v", amf, "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20",
                     *(["-qp_b", "22"] if codec == "h264" else []), "-pix_fmt", "yuv420p"]
    if hw == "amf":
        raise RuntimeError(f"{amf} indisponible")
    if codec == "h264":
        return "libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
    return "libx265", ["-c:v", "libx265", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]


def _fit_filter(w: int, h: int, mode: str) -> str:
    if mode == "fill":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    if mode == "blur":
        return (f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"boxblur=20:2[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    raise ValueError("mode : fill ou blur")


def render_segment(src: Path, dur: float, out: Path, w: int, h: int, fps: int, mode: str):
    """Un plan -> segment a duree exacte (fige la derniere image si le clip est trop court)."""
    fit = _fit_filter(w, h, mode)
    is_image = src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    inp = ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(src)] if is_image else ["-i", str(src)]
    vf = f"fps={fps},{fit},tpad=stop_mode=clone:stop_duration={dur:.3f},trim=duration={dur:.3f},setpts=PTS-STARTPTS"
    _ff([*inp, "-an", "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", str(out)], f"segment {src.name}")


def loudnorm_filter(music: Path, start: float, dur: float, target: float = -14.0, tp: float = -1.0) -> str:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                        "-i", str(music), "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return f"loudnorm=I={target}:TP={tp}:LRA=11"
    d = json.loads(m.group(0))
    return (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={d['input_i']}:measured_TP={d['input_tp']}:"
            f"measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:offset={d['target_offset']}:linear=true")


def assemble(sb: dict, project: Path, fmt: str = "9x16", codec: str = "h264", mode: str = "fill",
             normalize: bool = True, hw: str = "auto", out_name: str | None = None) -> dict:
    w, h = SIZES[fmt]
    fps = sb.get("fps", 24)
    exports = project / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="vai-edit-", dir=project))
    try:
        segs, used = [], []
        for s in sb["shots"]:
            src = next((Path(p) for p in (s.get("clip"), s.get("keyframe")) if p and Path(p).exists()), None)
            if not src:
                raise FileNotFoundError(f"plan {s['id']} : ni clip ni image-cle disponible")
            seg = work / f"{s['id']}.mp4"
            render_segment(src, s["duration"], seg, w, h, fps, mode)
            segs.append(seg)
            used.append({"shot": s["id"], "source": str(src), "kind": "clip" if src == Path(s.get("clip") or "") else "image"})
        lst = work / "concat.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8")
        video = work / "video.mp4"
        _ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)], "concat")

        start, end = sb["range"]
        dur = sum(s["duration"] for s in sb["shots"])
        enc, vargs = video_codec_args(codec, hw)
        af = loudnorm_filter(Path(sb["music"]), start, dur) if normalize else "anull"
        out = exports / (out_name or f"{project.name}_{fmt}_{codec}.mp4")
        _ff(["-i", str(video), "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", sb["music"],
             "-map", "0:v", "-map", "1:a", *vargs, "-r", str(fps),
             "-af", f"{af},aresample=48000", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(out)], "export")
        return {"output": str(out), "encoder": enc, "format": fmt, "size": [w, h], "mode": mode,
                "duration_target": round(dur, 3), "shots": used, "check": verify(out, dur)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify(path: Path, expected: float | None = None, tol: float = 0.25) -> dict:
    """Le fichier existe, se lit integralement, et a la duree attendue."""
    path = Path(path)
    res = {"exists": path.exists(), "bytes": path.stat().st_size if path.exists() else 0}
    if not res["exists"]:
        res["ok"] = False
        return res
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True)
    info = json.loads(p.stdout or "{}")
    res["streams"] = info.get("streams", [])
    res["duration"] = float(info.get("format", {}).get("duration", 0))
    d = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    res["decode_errors"] = d.stderr.strip()[:500]
    res["ok"] = (p.returncode == 0 and d.returncode == 0 and not res["decode_errors"]
                 and (expected is None or abs(res["duration"] - expected) <= tol))
    return res
