"""Workflows ComfyUI parametres + execution tracee (seeds, parametres, reprise).

Un workflow = deux fichiers dans video-ai/workflows/<categorie>/ :
  <nom>.api.json     export "API" de ComfyUI (graphe executable)
  <nom>.params.json  parametres exposes -> [noeud, entree] a remplacer, avec valeurs par defaut

Chaque execution ecrit <out_dir>/<job_id>.job.json (statut, parametres resolus dont le seed,
prompt_id, sorties, duree, memoire). Un job non termine se relance a l'identique (`vai resume`).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import random
import time
from pathlib import Path

from .comfy import Comfy, ComfyError, output_files
from .config import REPO
from .sysmon import Sampler

WORKFLOWS_DIR = REPO / "workflows"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def find(name: str) -> tuple[Path, Path]:
    hits = list(WORKFLOWS_DIR.rglob(f"{name}.api.json"))
    if not hits:
        raise FileNotFoundError(f"workflow '{name}' introuvable sous {WORKFLOWS_DIR}")
    api = hits[0]
    return api, api.with_name(api.name.replace(".api.json", ".params.json"))


def list_all() -> list[dict]:
    out = []
    for api in sorted(WORKFLOWS_DIR.rglob("*.api.json")):
        spec = json.loads(api.with_name(api.name.replace(".api.json", ".params.json")).read_text(encoding="utf-8"))
        out.append({"name": api.name[:-9], "category": api.parent.name, "description": spec.get("description", ""),
                    "params": {k: v.get("default") for k, v in spec["params"].items()}})
    return out


def coerce(value: str):
    """Conversion des valeurs passees en ligne de commande (key=value)."""
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def build(name: str, overrides: dict, comfy: Comfy | None = None) -> tuple[dict, dict]:
    """Retourne (graphe API pret a executer, parametres resolus)."""
    api_path, params_path = find(name)
    graph = json.loads(api_path.read_text(encoding="utf-8"))
    spec = json.loads(params_path.read_text(encoding="utf-8"))
    unknown = set(overrides) - set(spec["params"])
    if unknown:
        raise ValueError(f"parametres inconnus pour {name} : {sorted(unknown)} (connus : {sorted(spec['params'])})")
    resolved = {}
    for key, p in spec["params"].items():
        val = overrides.get(key, p.get("default"))
        if p.get("required") and val in (None, ""):
            raise ValueError(f"parametre requis : {key}")
        if key == "seed" or p.get("type") == "seed":
            val = int(val) if val is not None else -1
            if val < 0:
                val = random.randint(0, 2**50)
        if p.get("type") == "image" and val:
            src = Path(val)
            if src.exists():
                if comfy is None:
                    raise ValueError("upload d'image impossible sans client ComfyUI")
                resolved[f"{key}_source"] = str(src.resolve())
                val = comfy.upload_image(src)
        if p.get("type") == "frames" and val is not None:
            val = int(val)
            if (val - 1) % 4:
                val = ((val - 1) // 4) * 4 + 1  # Wan : longueur = 4n+1
        resolved[key] = val
        if val is None:
            continue
        for node, inp in p["targets"]:
            if node not in graph:
                raise KeyError(f"{params_path.name} : noeud {node} absent du graphe")
            graph[node]["inputs"][inp] = val
    return graph, resolved


def run(comfy: Comfy, name: str, overrides: dict, out_dir: Path, job_id: str | None = None,
        timeout: float = 7200, quiet: bool = False) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    job_id = job_id or f"{name}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    job_path = out_dir / f"{job_id}.job.json"
    rec = {"job_id": job_id, "workflow": name, "requested": overrides, "status": "preparing",
           "created": _now(), "out_dir": str(out_dir)}

    def save():
        job_path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")

    save()
    t0 = time.time()
    try:
        graph, resolved = build(name, overrides, comfy)
        rec["params"] = resolved
        rec["status"] = "queued"
        save()
        with Sampler() as mon:
            rec["prompt_id"] = comfy.queue(graph)
            rec["status"] = "running"
            save()
            last = [0.0]

            def tick(elapsed):
                if not quiet and elapsed - last[0] >= 15:
                    last[0] = elapsed
                    print(f"    ... {job_id} en cours ({elapsed:.0f}s)", flush=True)

            entry = comfy.wait(rec["prompt_id"], timeout=timeout, on_tick=tick)
        rec["memory"] = mon.summary()
        rec["outputs"] = [str(comfy.download_output(f, out_dir)) for f in output_files(entry)]
        if not rec["outputs"]:
            raise ComfyError("aucune sortie produite")
        rec["status"] = "done"
    except (ComfyError, ValueError, KeyError, FileNotFoundError, OSError) as e:
        rec["status"] = "failed"
        rec["error"] = str(e)
    except KeyboardInterrupt:
        rec["status"] = "interrupted"
        try:
            comfy.interrupt()
        except ComfyError:
            pass
        save()
        raise
    finally:
        rec["duration_s"] = round(time.time() - t0, 1)
        rec["finished"] = _now()
        save()
    return rec


def resume(comfy: Comfy, job_path: Path, **kw) -> dict:
    """Relance un job non termine avec EXACTEMENT les memes parametres (seed compris)."""
    old = json.loads(Path(job_path).read_text(encoding="utf-8"))
    if old["status"] == "done":
        return old
    params = dict(old.get("params") or old["requested"])
    for k in [k for k in params if k.endswith("_source")]:
        params[k[:-7]] = params.pop(k)  # re-upload depuis le fichier source local
    return run(comfy, old["workflow"], params, Path(old["out_dir"]), job_id=old["job_id"], **kw)
