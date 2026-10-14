"""Client minimal de l'API locale ComfyUI (stdlib uniquement)."""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex

    # --- HTTP -------------------------------------------------------------
    def _req(self, path: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 30):
        req = urllib.request.Request(self.url + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyError(f"HTTP {e.code} sur {path} : {body[:2000]}") from None
        except urllib.error.URLError as e:
            raise ComfyError(f"ComfyUI injoignable ({self.url}) : {e.reason}") from None

    def get_json(self, path: str, timeout: float = 30):
        return json.loads(self._req(path, timeout=timeout))

    def post_json(self, path: str, payload: dict, timeout: float = 30):
        data = json.dumps(payload).encode("utf-8")
        return json.loads(self._req(path, data, {"Content-Type": "application/json"}, timeout) or b"{}")

    # --- API ComfyUI --------------------------------------------------------
    def system_stats(self) -> dict:
        return self.get_json("/system_stats", timeout=5)

    def object_info(self, node: str | None = None) -> dict:
        return self.get_json("/object_info" + (f"/{node}" if node else ""), timeout=60)

    def upload_image(self, path: Path, subfolder: str = "vai") -> str:
        """Envoie une image dans le dossier input de ComfyUI ; retourne le nom a passer a LoadImage."""
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode() + path.read_bytes() + b"\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\n{subfolder}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ]
        res = json.loads(self._req("/upload/image", b"".join(parts),
                                   {"Content-Type": f"multipart/form-data; boundary={boundary}"}, 120))
        return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]

    def queue(self, prompt: dict) -> str:
        res = self.post_json("/prompt", {"prompt": prompt, "client_id": self.client_id})
        if res.get("node_errors"):
            raise ComfyError(f"workflow refuse : {json.dumps(res['node_errors'], ensure_ascii=False)[:2000]}")
        return res["prompt_id"]

    def wait(self, prompt_id: str, timeout: float = 3600, poll: float = 1.0, on_tick=None) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            hist = self.get_json(f"/history/{prompt_id}", timeout=15)
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error" or not status.get("completed", True):
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    detail = msgs[0][1] if msgs else status
                    raise ComfyError(f"execution en echec : {json.dumps(detail, ensure_ascii=False)[:3000]}")
                return entry
            if on_tick:
                on_tick(time.time() - t0)
            time.sleep(poll)
        raise ComfyError(f"delai depasse ({timeout}s) pour {prompt_id}")

    def interrupt(self):
        self._req("/interrupt", b"{}", {"Content-Type": "application/json"})

    def download_output(self, file_info: dict, dest_dir: Path) -> Path:
        q = urllib.parse.urlencode({"filename": file_info["filename"],
                                    "subfolder": file_info.get("subfolder", ""),
                                    "type": file_info.get("type", "output")})
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file_info["filename"]
        dest.write_bytes(self._req(f"/view?{q}", timeout=600))
        return dest


def output_files(history_entry: dict) -> list[dict]:
    """Liste a plat des fichiers produits (images, videos, gifs) d'une entree d'historique."""
    files = []
    for node_out in history_entry.get("outputs", {}).values():
        for key in ("images", "videos", "gifs", "audio"):
            for f in node_out.get(key, []) or []:
                if isinstance(f, dict) and f.get("filename") and f.get("type") == "output":
                    files.append(f)
    return files
