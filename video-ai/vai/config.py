"""Configuration effective (miroir de scripts/common.ps1 : Get-VaiConfig)."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # .../workstation/video-ai


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load() -> dict:
    cfg = json.loads((REPO / "config" / "video-ai.json").read_text(encoding="utf-8"))
    local = REPO / "config" / "local.json"
    if local.exists():
        _merge(cfg, json.loads(local.read_text(encoding="utf-8")))
    cfg["home"] = os.environ.get("VIDEO_AI_HOME", cfg["home"])
    if os.environ.get("VIDEO_AI_MODELS"):
        cfg["models_dir"] = os.environ["VIDEO_AI_MODELS"]
    if os.environ.get("VIDEO_AI_PORT"):
        cfg["port"] = int(os.environ["VIDEO_AI_PORT"])
    home = Path(cfg["home"])
    cfg["paths"] = {
        "home": home,
        "models": Path(cfg["models_dir"]) if cfg.get("models_dir") else home / "models",
        "input": home / "comfy-io" / "input",
        "output": home / "comfy-io" / "output",
        "projects": home / "projects",
        "assets": home / "assets",
        "logs": home / "logs",
    }
    cfg["url"] = f"http://{cfg['host']}:{cfg['port']}"
    return cfg
