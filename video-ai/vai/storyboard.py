"""Storyboard : liste de plans calee sur les reperes musicaux (mesures + changements d'energie).

storyboard.json est la source de verite d'un projet (editable a la main ou par Claude Code) :
chaque plan porte ses bornes temporelles, son prompt image (image-cle), son prompt video,
son seed, et les chemins des rendus. storyboard.md en est une vue lisible.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

WAN_FPS = 24
WAN_MAX_FRAMES = 121  # ~5 s : limite du modele Wan 2.2 TI2V 5B

MOTION = {"low": "slow, gentle camera movement", "mid": "steady tracking shot",
          "high": "dynamic camera movement, energetic motion"}


def wan_frames(duration: float) -> int:
    """Plus petit 4n+1 couvrant la duree, plafonne a 121."""
    n = math.ceil(duration * WAN_FPS)
    n = ((max(n, 5) - 1 + 3) // 4) * 4 + 1
    return min(n, WAN_MAX_FRAMES)


def build(cues: dict, fmt: str = "9x16", min_shot: float = 2.0, max_shot: float = 5.0,
          start: float = 0.0, end: float | None = None, character: str | None = None) -> dict:
    duration = cues["meta"]["duration"]
    end = min(end or duration, duration)
    bars = [b for b in cues.get("bars", []) if start <= b <= end]
    changes = [c for c in cues.get("energy_changes", []) if start < c < end]

    # 1. Coupes obligatoires : changements d'energie (espaces d'au moins min_shot)
    anchors = [start]
    for c in changes:
        if c - anchors[-1] >= min_shot and end - c >= min_shot:
            anchors.append(round(c, 3))
    anchors.append(end)
    # 2. Entre deux ancres : coupes sur les mesures, sans jamais descendre sous min_shot
    cuts = [start]
    for a, b in zip(anchors[:-1], anchors[1:]):
        for c in bars:
            if a < c < b and c - cuts[-1] >= max(min_shot, max_shot * 0.6) and b - c >= min_shot:
                cuts.append(round(c, 3))
        cuts.append(b)

    # Plans trop longs : decoupe en parts egales (pas de reliquat minuscule)
    final = [cuts[0]]
    for c in cuts[1:]:
        n = math.ceil((c - final[-1]) / max_shot - 1e-9)
        step = (c - final[-1]) / max(n, 1)
        for k in range(1, n):
            final.append(round(final[-1] + step, 3))
        final.append(c)

    def level_at(t):
        for s in cues.get("sections", []):
            if s["start"] <= t < s["end"]:
                return s["level"]
        return "mid"

    w, h = (704, 1280) if fmt == "9x16" else (1280, 704)
    shots = []
    for i, (a, b) in enumerate(zip(final[:-1], final[1:]), 1):
        lvl = level_at((a + b) / 2)
        shots.append({
            "id": f"s{i:03d}", "start": round(a, 3), "end": round(b, 3), "duration": round(b - a, 3),
            "energy": lvl,
            "keyframe_prompt": "",  # a remplir : description de l'image-cle (personnage, decor, cadrage)
            "video_prompt": f"{MOTION[lvl]}",  # a enrichir : action, mouvement
            "seed": -1,
            "width": w, "height": h, "frames": wan_frames(b - a),
            "keyframe": None, "clip": None, "status": "todo",
        })
    return {"format": fmt, "fps": WAN_FPS, "music": cues["meta"]["file"], "range": [start, end],
            "tempo_bpm": cues.get("tempo_bpm"), "character": character, "shots": shots}


def to_markdown(sb: dict) -> str:
    lines = [f"# Storyboard — {Path(sb['music']).name}", "",
             f"Format {sb['format']} · {sb['fps']} fps · tempo ~{sb['tempo_bpm']} BPM · "
             f"{sb['range'][0]:.1f}–{sb['range'][1]:.1f} s · {len(sb['shots'])} plans", "",
             "| Plan | Debut | Fin | Duree | Energie | Image-cle | Mouvement | Statut |",
             "|---|---|---|---|---|---|---|---|"]
    for s in sb["shots"]:
        lines.append(f"| {s['id']} | {s['start']:.2f} | {s['end']:.2f} | {s['duration']:.2f} | {s['energy']} | "
                     f"{s['keyframe_prompt'] or '_a ecrire_'} | {s['video_prompt']} | {s['status']} |")
    return "\n".join(lines) + "\n"


def save(sb: dict, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "storyboard.json").write_text(json.dumps(sb, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "storyboard.md").write_text(to_markdown(sb), encoding="utf-8")
