"""CLI `vai` — utiliser via video-ai\\vai.ps1 (venv ComfyUI) :

  vai status                                   etat de l'API / GPU
  vai workflows                                workflows disponibles et parametres
  vai run <workflow> [-o DIR] [key=value ...]  execute un workflow (seed/params traces dans *.job.json)
  vai resume <fichier.job.json>                relance un job non termine a l'identique
  vai audio <musique> [-o cues.json]           analyse musicale (duree, silences, tempo, energie)
  vai project new <nom>                        cree un projet
  vai storyboard <projet> <musique> [--format 9x16|16x9] [--start S --end E] [--character slug]
  vai render keyframes|clips <projet> [--only s001,s002] [--use-ref] [--steps N]
  vai assemble <projet> [--format 9x16 16x9] [--codec h264|h265] [--mode fill|blur] [--no-normalize]
  vai character new <nom> | prompt <slug> [--outfit X] [--scene "..."]
  vai verify <fichier video>                   lisibilite + duree d'un export
  vai saccheck                                 binaires du venv refuses par Smart App Control
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import audio, config, edit, project, storyboard, workflows
from .comfy import Comfy, ComfyError


def _p(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vai", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("workflows")
    r = sub.add_parser("run"); r.add_argument("workflow"); r.add_argument("-o", "--out"); r.add_argument("kv", nargs="*")
    r.add_argument("--timeout", type=float, default=7200)
    rs = sub.add_parser("resume"); rs.add_argument("job")
    a = sub.add_parser("audio"); a.add_argument("music"); a.add_argument("-o", "--out")
    pj = sub.add_parser("project"); pj.add_argument("action", choices=["new", "list"]); pj.add_argument("name", nargs="?")
    sb = sub.add_parser("storyboard"); sb.add_argument("project"); sb.add_argument("music")
    sb.add_argument("--format", default="9x16", choices=["9x16", "16x9"]); sb.add_argument("--start", type=float, default=0)
    sb.add_argument("--end", type=float); sb.add_argument("--character"); sb.add_argument("--min-shot", type=float, default=2.0)
    sb.add_argument("--max-shot", type=float, default=5.0)
    rd = sub.add_parser("render"); rd.add_argument("what", choices=["keyframes", "clips"]); rd.add_argument("project")
    rd.add_argument("--only"); rd.add_argument("--use-ref", action="store_true"); rd.add_argument("--steps", type=int)
    asm = sub.add_parser("assemble"); asm.add_argument("project"); asm.add_argument("--format", nargs="+", default=["9x16"])
    asm.add_argument("--codec", default="h264", choices=["h264", "h265"]); asm.add_argument("--mode", default="fill", choices=["fill", "blur"])
    asm.add_argument("--no-normalize", action="store_true"); asm.add_argument("--hw", default="auto", choices=["auto", "amf", "cpu"])
    ch = sub.add_parser("character"); ch.add_argument("action", choices=["new", "prompt", "list"]); ch.add_argument("name", nargs="?")
    ch.add_argument("--outfit"); ch.add_argument("--scene", default=""); ch.add_argument("--shot", default="")
    v = sub.add_parser("verify"); v.add_argument("file"); v.add_argument("--duration", type=float)
    sub.add_parser("saccheck", help="binaires du venv bloques par Smart App Control")
    args = ap.parse_args(argv)
    if args.cmd == "saccheck":
        from . import saccheck
        return saccheck.main()

    cfg = config.load()
    comfy = Comfy(cfg["url"])
    try:
        if args.cmd == "status":
            st = comfy.system_stats()
            _p({"url": cfg["url"], "comfyui": st["system"].get("comfyui_version"),
                "pytorch": st["system"].get("pytorch_version"), "devices": st["devices"]})
        elif args.cmd == "workflows":
            _p(workflows.list_all())
        elif args.cmd == "run":
            kv = dict(x.split("=", 1) for x in args.kv)
            overrides = {k: workflows.coerce(v) for k, v in kv.items()}
            out = Path(args.out) if args.out else cfg["paths"]["output"].parent / "runs"
            rec = workflows.run(comfy, args.workflow, overrides, out, timeout=args.timeout)
            _p(rec)
            return 0 if rec["status"] == "done" else 1
        elif args.cmd == "resume":
            rec = workflows.resume(comfy, Path(args.job))
            _p(rec)
            return 0 if rec["status"] == "done" else 1
        elif args.cmd == "audio":
            cues = audio.analyze(Path(args.music))
            if args.out:
                Path(args.out).write_text(json.dumps(cues, indent=2, ensure_ascii=False), encoding="utf-8")
            summary = {k: cues[k] for k in ("meta", "loudness", "silences", "tempo_bpm", "energy_changes", "sections")}
            summary["beats"] = f"{len(cues['beats'])} temps, {len(cues['bars'])} mesures"
            _p(summary)
        elif args.cmd == "project":
            if args.action == "list":
                _p(sorted(p.name for p in cfg["paths"]["projects"].glob("*") if p.is_dir()))
            else:
                print(project.new_project(cfg, args.name))
        elif args.cmd == "storyboard":
            pdir = project.project_dir(cfg, args.project)
            music = Path(args.music)
            local = pdir / "inputs" / music.name
            if music.resolve() != local.resolve():
                shutil.copy2(music, local)
            cues = audio.analyze(local)
            (pdir / "storyboard" / "cues.json").write_text(json.dumps(cues, indent=2), encoding="utf-8")
            sbd = storyboard.build(cues, args.format, args.min_shot, args.max_shot, args.start, args.end, args.character)
            storyboard.save(sbd, pdir / "storyboard")
            print(storyboard.to_markdown(sbd))
            print(f"-> {pdir / 'storyboard' / 'storyboard.json'} (remplir keyframe_prompt / video_prompt)")
        elif args.cmd == "render":
            pdir = project.project_dir(cfg, args.project)
            only = set(args.only.split(",")) if args.only else None
            fn = project.render_keyframes if args.what == "keyframes" else project.render_clips
            kw = {"use_ref": args.use_ref} if args.what == "keyframes" else {}
            recs = fn(cfg, comfy, pdir, only=only, steps=args.steps, **kw)
            _p([{k: r.get(k) for k in ("job_id", "status", "duration_s", "outputs", "error", "memory")} for r in recs])
            return 0 if all(r["status"] == "done" for r in recs) else 1
        elif args.cmd == "assemble":
            pdir = project.project_dir(cfg, args.project)
            sbd = project.load_storyboard(pdir)
            ok = True
            for fmt in args.format:
                res = edit.assemble(sbd, pdir, fmt, args.codec, args.mode, not args.no_normalize, args.hw)
                ok &= res["check"]["ok"]
                _p(res)
            return 0 if ok else 1
        elif args.cmd == "character":
            if args.action == "new":
                print(project.new_character(cfg, args.name))
            elif args.action == "list":
                _p(sorted(p.name for p in (cfg["paths"]["assets"] / "characters").glob("*") if p.is_dir()))
            else:
                print(project.character_prompt(project.load_character(cfg, args.name), args.outfit, args.scene, args.shot))
        elif args.cmd == "verify":
            res = edit.verify(Path(args.file), args.duration)
            _p(res)
            return 0 if res["ok"] else 1
    except ComfyError as e:
        print(f"ERREUR ComfyUI : {e}", file=sys.stderr)
        return 2
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
