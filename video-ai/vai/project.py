"""Projets (clips) et personnages (influenceurs virtuels) : arborescence, prompts, rendus par plan."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import workflows
from .comfy import Comfy
from .config import REPO

PROJECT_DIRS = ("storyboard", "inputs", "renders/keyframes", "renders/clips", "renders/tests", "exports")
CHAR_TEMPLATE = REPO / "assets" / "characters" / "_template"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s:
        raise ValueError("nom invalide")
    return s


# --- Projets -------------------------------------------------------------------
def new_project(cfg: dict, name: str) -> Path:
    root = cfg["paths"]["projects"] / slugify(name)
    for d in PROJECT_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def project_dir(cfg: dict, name: str) -> Path:
    p = cfg["paths"]["projects"] / name
    if not p.exists():
        raise FileNotFoundError(f"projet introuvable : {p} (creer : vai project new {name})")
    return p


def load_storyboard(project: Path) -> dict:
    return json.loads((project / "storyboard" / "storyboard.json").read_text(encoding="utf-8"))


def save_storyboard(project: Path, sb: dict):
    from .storyboard import save
    save(sb, project / "storyboard")


# --- Personnages ------------------------------------------------------------------
def char_dir(cfg: dict, slug: str) -> Path:
    return cfg["paths"]["assets"] / "characters" / slug


def new_character(cfg: dict, name: str) -> Path:
    slug = slugify(name)
    dest = char_dir(cfg, slug)
    if dest.exists():
        raise FileExistsError(f"le personnage existe deja : {dest}")
    shutil.copytree(CHAR_TEMPLATE, dest)
    cj = dest / "character.json"
    data = json.loads(cj.read_text(encoding="utf-8"))
    data["name"], data["slug"] = name, slug
    cj.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def load_character(cfg: dict, slug: str) -> dict:
    return json.loads((char_dir(cfg, slug) / "character.json").read_text(encoding="utf-8"))


def character_prompt(ch: dict, outfit: str | None = None, scene: str = "", shot: str = "") -> str:
    """Prompt canonique = identite (stable) + tenue + scene + cadrage. Ordre fixe pour la coherence."""
    c = ch["canonical"]
    parts = [ch.get("style_prefix", ""),
             ", ".join(v for v in (c.get("age_appearance"), c.get("face"), c.get("eyes"), c.get("skin"),
                                   c.get("hair"), c.get("body")) if v),
             ch.get("outfits", {}).get(outfit or ch.get("default_outfit", ""), ""),
             scene, shot, ch.get("style_suffix", "")]
    return ". ".join(p.strip().rstrip(".") for p in parts if p and p.strip()) + "."


def first_reference(cfg: dict, slug: str, kind: str = "face") -> Path | None:
    d = char_dir(cfg, slug) / "refs" / kind
    imgs = sorted(p for p in d.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    return imgs[0] if imgs else None


def log_generation(cfg: dict, slug: str, rec: dict):
    with (char_dir(cfg, slug) / "generations.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: rec.get(k) for k in ("job_id", "workflow", "params", "outputs", "status",
                                                      "duration_s", "finished")}, ensure_ascii=False) + "\n")


# --- Rendus par plan (reprise : les plans 'done' avec fichier present sont sautes) ----------
def render_keyframes(cfg: dict, comfy: Comfy, project: Path, only: set | None = None, use_ref: bool = False,
                     steps: int | None = None) -> list[dict]:
    sb = load_storyboard(project)
    ch = load_character(cfg, sb["character"]) if sb.get("character") else None
    ref = first_reference(cfg, sb["character"]) if (ch and use_ref) else None
    results = []
    for s in sb["shots"]:
        if only and s["id"] not in only:
            continue
        if s.get("keyframe") and Path(s["keyframe"]).exists():
            continue
        if not s.get("keyframe_prompt"):
            print(f"  {s['id']} : keyframe_prompt vide, plan ignore")
            continue
        prompt = character_prompt(ch, s.get("outfit"), s["keyframe_prompt"]) if ch else s["keyframe_prompt"]
        params = {"prompt": prompt, "seed": s.get("keyframe_seed", s.get("seed", -1)),
                  "width": s["width"], "height": s["height"]}
        if steps:
            params["steps"] = steps
        name = "flux2_klein_4b_t2i"
        if ref is not None:
            name, params["image"] = "flux2_klein_4b_ref", str(ref)
        print(f"  {s['id']} : image-cle ({name}) ...", flush=True)
        rec = workflows.run(comfy, name, params, project / "renders" / "keyframes", job_id=f"{s['id']}-keyframe")
        if rec["status"] == "done":
            s["keyframe"], s["keyframe_seed"] = rec["outputs"][0], rec["params"]["seed"]
            s["status"] = "keyframe"
        else:
            s["status"] = "keyframe_failed"
        if ch:
            log_generation(cfg, sb["character"], rec)
        save_storyboard(project, sb)
        results.append(rec)
    return results


def render_clips(cfg: dict, comfy: Comfy, project: Path, only: set | None = None, steps: int | None = None) -> list[dict]:
    sb = load_storyboard(project)
    results = []
    for s in sb["shots"]:
        if only and s["id"] not in only:
            continue
        if s.get("clip") and Path(s["clip"]).exists():
            continue
        if not (s.get("keyframe") and Path(s["keyframe"]).exists()):
            print(f"  {s['id']} : pas d'image-cle, plan ignore")
            continue
        params = {"prompt": s["video_prompt"], "image": s["keyframe"], "seed": s.get("seed", -1),
                  "width": s["width"], "height": s["height"], "frames": s["frames"]}
        if steps:
            params["steps"] = steps
        print(f"  {s['id']} : clip Wan 2.2 ({s['frames']} images, {s['width']}x{s['height']}) ...", flush=True)
        rec = workflows.run(comfy, "wan22_ti2v_5b_i2v", params, project / "renders" / "clips", job_id=f"{s['id']}-clip")
        if rec["status"] == "done":
            s["clip"], s["seed"], s["status"] = rec["outputs"][0], rec["params"]["seed"], "clip"
        else:
            s["status"] = "clip_failed"
        save_storyboard(project, sb)
        results.append(rec)
    return results
