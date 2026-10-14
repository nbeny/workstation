"""Tests de bout en bout adaptes au materiel reel. Statuts : PASS / FAIL / SKIP (un SKIP n'est jamais un succes).

  python -m vai.selftest [--quick] [--only 1,2,3]
--quick : saute les generations (tests 5 et 6 -> SKIP, le 7 utilise des medias synthetiques).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata as md
import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import audio, config, edit, storyboard, workflows
from .comfy import Comfy, ComfyError

REQUIRED_NODES = ["UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "Flux2Scheduler", "EmptyFlux2LatentImage",
                  "SamplerCustomAdvanced", "CFGGuider", "RandomNoise", "ReferenceLatent", "ConditioningZeroOut",
                  "Wan22ImageToVideoLatent", "ModelSamplingSD3", "KSampler", "CreateVideo", "SaveVideo", "SaveImage"]


class Skip(Exception):
    pass


class Suite:
    def __init__(self, cfg):
        self.cfg = cfg
        self.comfy = Comfy(cfg["url"])
        self.results = []
        self.ctx = {}
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.work = cfg["paths"]["home"] / "tests" / ts
        self.work.mkdir(parents=True, exist_ok=True)

    def run(self, num, name, fn):
        t0 = time.time()
        print(f"\n[{num}] {name} ...", flush=True)
        res = {"n": num, "name": name}
        try:
            details = fn() or {}
            res.update(status="PASS", details=details)
        except Skip as e:
            res.update(status="SKIP", reason=str(e))
        except Exception as e:  # noqa: BLE001 - on rapporte tout echec
            res.update(status="FAIL", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc(limit=3))
        res["duration_s"] = round(time.time() - t0, 1)
        color = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m"}[res["status"]]
        extra = res.get("reason") or res.get("error") or ""
        print(f"    {color}{res['status']}\033[0m ({res['duration_s']} s) {extra}", flush=True)
        if res.get("details"):
            for k, v in res["details"].items():
                print(f"      {k}: {v}")
        self.results.append(res)
        return res

    # 1 --------------------------------------------------------------------------------
    def t_deps(self):
        d = {}
        for tool in ("git", "ffmpeg", "ffprobe", "uv"):
            p = shutil.which(tool)
            if not p:
                raise RuntimeError(f"{tool} introuvable")
        ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        d["ffmpeg"] = ff.split(" Copyright")[0]
        want = next(x.split("==")[1] for x in self.cfg["torch"]["packages"] if x.startswith("torch=="))
        have = md.version("torch")
        if have != want:
            raise RuntimeError(f"torch {have} != fige {want}")
        d["torch"] = have
        d["python"] = sys.version.split()[0]
        for pkg in ("numpy", "av", "safetensors", "transformers"):
            d[pkg] = md.version(pkg)
        missing = []
        models = json.loads((config.REPO / "config" / "models.json").read_text(encoding="utf-8"))
        for g, spec in models["groups"].items():
            for f in spec["files"]:
                p = self.cfg["paths"]["models"] / f["folder"] / f["name"]
                if not (p.exists() and p.stat().st_size == f["size"]):
                    missing.append(f"{g}/{f['name']}")
        d["models_missing"] = missing or "aucun"
        self.ctx["models_missing"] = missing
        return d

    # 2 --------------------------------------------------------------------------------
    def t_available(self):
        t0 = time.time()
        st = self.comfy.system_stats()
        return {"url": self.cfg["url"], "latency_ms": round((time.time() - t0) * 1000),
                "comfyui": st["system"].get("comfyui_version"), "listen": self.cfg["host"]}

    # 3 --------------------------------------------------------------------------------
    def t_api(self):
        info = self.comfy.object_info()
        missing = [n for n in REQUIRED_NODES if n not in info]
        if missing:
            raise RuntimeError(f"noeuds absents : {missing}")
        # Lint des workflows du depot contre les definitions reelles des noeuds
        problems, checked = [], 0
        for api in sorted(workflows.WORKFLOWS_DIR.rglob("*.api.json")):
            graph = json.loads(api.read_text(encoding="utf-8"))
            for nid, node in graph.items():
                spec = info.get(node["class_type"])
                if not spec:
                    problems.append(f"{api.name}#{nid}: classe {node['class_type']} inconnue")
                    continue
                known = set(spec["input"].get("required", {})) | set(spec["input"].get("optional", {}))
                for k in node["inputs"]:
                    if k not in known:
                        problems.append(f"{api.name}#{nid}: entree '{k}' inconnue pour {node['class_type']}")
                for k in spec["input"].get("required", {}):
                    if k not in node["inputs"]:
                        problems.append(f"{api.name}#{nid}: entree requise '{k}' manquante ({node['class_type']})")
            checked += 1
        if problems:
            raise RuntimeError("; ".join(problems[:10]))
        q = self.comfy.get_json("/queue")
        # Un graphe invalide doit etre refuse proprement (validation cote serveur)
        try:
            self.comfy.queue({"1": {"class_type": "NoSuchNode", "inputs": {}}})
            raise RuntimeError("graphe invalide accepte")
        except ComfyError as e:
            refused = "400" in str(e) or "refuse" in str(e)
        if not refused:
            raise RuntimeError("refus inattendu")
        return {"nodes": len(info), "workflows_lint": f"{checked} OK", "queue_running": len(q.get("queue_running", [])),
                "invalid_prompt_rejected": True}

    # 4 --------------------------------------------------------------------------------
    def t_gpu(self):
        st = self.comfy.system_stats()
        dev = st["devices"][0]
        if dev["type"] != "cuda" or "AMD" not in dev["name"] and "Radeon" not in dev["name"]:
            raise RuntimeError(f"ComfyUI n'utilise pas le GPU AMD : {dev['type']} {dev['name']}")
        code = ("import torch,time;a=torch.randn(4096,4096,device='cuda',dtype=torch.float16);torch.cuda.synchronize();"
                "t=time.time();[a@a for _ in range(20)];torch.cuda.synchronize();"
                "print(torch.cuda.get_device_properties(0).gcnArchName, round(20*2*4096**3/(time.time()-t)/1e12,1))")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-500:])
        arch, tflops = r.stdout.split()[-2:]
        return {"comfy_device": dev["name"], "vram_total_gb": round(dev["vram_total"] / 2**30, 1),
                "vram_free_gb": round(dev["vram_free"] / 2**30, 1), "arch": arch, "fp16_matmul_tflops": tflops}

    # 5 --------------------------------------------------------------------------------
    def t_image(self, quick):
        if quick:
            raise Skip("--quick")
        if any(m.startswith("flux2-klein-4b/") for m in self.ctx.get("models_missing", [])):
            raise Skip("modeles FLUX.2 Klein absents (download-models.ps1 -Group flux2-klein-4b)")
        rec = workflows.run(self.comfy, "flux2_klein_4b_t2i",
                            {"prompt": "Photorealistic portrait photograph of a fictional young woman with wavy chestnut "
                                       "hair, soft window light, 85mm, natural skin texture", "seed": 42,
                             "width": 704, "height": 1280, "prefix": "vai-tests/flux"},
                            self.work, job_id="t5-image", timeout=1800)
        if rec["status"] != "done":
            raise RuntimeError(rec.get("error"))
        from PIL import Image
        img = Path(rec["outputs"][0])
        with Image.open(img) as im:
            size = im.size
        if size != (704, 1280):
            raise RuntimeError(f"taille inattendue {size}")
        self.ctx["image"] = img
        return {"output": str(img), "size": f"{size[0]}x{size[1]}", "seed": rec["params"]["seed"],
                "job_s": rec["duration_s"], **(rec.get("memory") or {})}

    # 6 --------------------------------------------------------------------------------
    def t_video(self, quick):
        if quick:
            raise Skip("--quick")
        if any(m.startswith("wan22-ti2v-5b/") for m in self.ctx.get("models_missing", [])):
            raise Skip("modeles Wan 2.2 absents (download-models.ps1 -Group wan22-ti2v-5b)")
        if "image" not in self.ctx:
            raise Skip("pas d'image de depart (test 5 non reussi)")
        rec = workflows.run(self.comfy, "wan22_ti2v_5b_i2v",
                            {"prompt": "the woman slowly turns her head and smiles, gentle camera push-in",
                             "image": str(self.ctx["image"]), "seed": 42, "width": 352, "height": 640,
                             "frames": 25, "steps": 12, "prefix": "vai-tests/wan"},
                            self.work, job_id="t6-video", timeout=3600)
        if rec["status"] != "done":
            raise RuntimeError(rec.get("error"))
        clip = Path(rec["outputs"][0])
        chk = edit.verify(clip)
        if not chk["ok"]:
            raise RuntimeError(f"clip illisible : {chk}")
        self.ctx["clip"] = clip
        v = next(s for s in chk["streams"] if s["codec_type"] == "video")
        return {"output": str(clip), "size": f"{v['width']}x{v['height']}", "duration_s": round(chk["duration"], 2),
                "frames": 25, "steps": 12, "job_s": rec["duration_s"], **(rec.get("memory") or {})}

    # 7 --------------------------------------------------------------------------------
    def t_assemble(self):
        music = self.work / "test_music.wav"
        # Musique synthetique 12 s a 120 BPM : clics + nappe, montee d'energie a 6 s
        # Expressions FFmpeg sans virgule (separateur de filtres) : phase = t-0.5*floor(2t), marche = (1+sgn(t-6))/2
        expr = ("0.6*sin(2*PI*110*t)*exp(-30*(t-0.5*floor(2*t)))+0.15*sin(2*PI*220*t)*(1+sgn(t-6))/2"
                "+0.4*sin(2*PI*55*t)*exp(-20*(t-0.5*floor(2*t)))*(1+sgn(t-6))/2")
        edit._ff(["-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100:d=12", "-ac", "2", str(music)], "musique test")
        cues = audio.analyze(music)
        src_img = self.ctx.get("image")
        src_clip = self.ctx.get("clip")
        synth = None
        if not (src_img or src_clip):
            synth = self.work / "synthetic.mp4"
            edit._ff(["-f", "lavfi", "-i", "testsrc2=s=704x1280:r=24:d=3", "-pix_fmt", "yuv420p", str(synth)], "clip synthetique")
        sb = storyboard.build(cues, "9x16", min_shot=2.0, max_shot=4.0, end=8.0)
        for i, s in enumerate(sb["shots"]):
            s["clip"] = str(src_clip or synth) if (i % 2 == 0 or not src_img) else None
            s["keyframe"] = str(src_img) if src_img else None
        proj = self.work / "project"
        storyboard.save(sb, proj / "storyboard")
        outs = []
        for fmt, codec in (("9x16", "h264"), ("16x9", "h265")):
            res = edit.assemble(sb, proj, fmt, codec, "fill" if fmt == "9x16" else "blur")
            outs.append(res)
        self.ctx["exports"] = outs
        return {"tempo_bpm_detected": cues["tempo_bpm"], "energy_changes": cues["energy_changes"],
                "shots": len(sb["shots"]), "sources": "generees" if (src_img or src_clip) else "synthetiques",
                "exports": [f"{Path(o['output']).name} ({o['encoder']})" for o in outs]}

    # 8 --------------------------------------------------------------------------------
    def t_exports(self):
        if not self.ctx.get("exports"):
            raise Skip("aucun export (test 7 non reussi)")
        d = {}
        for o in self.ctx["exports"]:
            chk = edit.verify(Path(o["output"]), o["duration_target"])
            if not chk["ok"]:
                raise RuntimeError(f"{o['output']} : {chk}")
            v = next(s for s in chk["streams"] if s["codec_type"] == "video")
            a = next(s for s in chk["streams"] if s["codec_type"] == "audio")
            d[Path(o["output"]).name] = (f"{v['codec_name']} {v['width']}x{v['height']} + {a['codec_name']}, "
                                         f"{chk['duration']:.2f}s, {chk['bytes'] / 1e6:.1f} Mo, decodage OK")
        return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = config.load()
    s = Suite(cfg)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    tests = [(1, "Dependances", s.t_deps), (2, "Disponibilite ComfyUI", s.t_available), (3, "API + lint des workflows", s.t_api),
             (4, "Detection et calcul GPU", s.t_gpu), (5, "Generation d'image (FLUX.2 Klein 4B)", lambda: s.t_image(a.quick)),
             (6, "Video courte (Wan 2.2 TI2V 5B)", lambda: s.t_video(a.quick)), (7, "Assemblage FFmpeg 9:16 + 16:9", s.t_assemble),
             (8, "Exports lisibles", s.t_exports)]
    for n, name, fn in tests:
        if only and n not in only:
            continue
        s.run(n, name, fn)
    report = s.work / "report.json"
    report.write_text(json.dumps(s.results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    counts = {k: sum(r["status"] == k for r in s.results) for k in ("PASS", "FAIL", "SKIP")}
    print(f"\nResultat : {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['SKIP']} SKIP — rapport : {report}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
