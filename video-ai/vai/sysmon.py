"""Echantillonnage de la VRAM dediee (compteurs Windows, valable pour AMD) et de la RAM pendant un job."""
from __future__ import annotations

import csv
import io
import subprocess
import threading

try:
    import psutil  # fourni par les dependances ComfyUI
except ImportError:  # pragma: no cover
    psutil = None


class Sampler:
    """Mesure le pic de VRAM dediee (toutes applis confondues) et de RAM systeme utilisee."""

    COUNTER = r"\GPU Adapter Memory(*)\Dedicated Usage"

    def __init__(self, interval: int = 1):
        self.interval = interval
        self.peak_vram = 0.0
        self.peak_ram = 0.0
        self.baseline_vram = None
        self._proc = None
        self._threads = []
        self._stop = threading.Event()

    def _read_typeperf(self):
        for line in io.TextIOWrapper(self._proc.stdout, encoding="utf-8", errors="replace"):
            if self._stop.is_set():
                break
            row = next(csv.reader([line]), [])
            vals = []
            for v in row[1:]:
                try:
                    vals.append(float(v))
                except ValueError:
                    pass
            if vals:
                cur = max(vals) / 2**30
                if self.baseline_vram is None:
                    self.baseline_vram = cur
                self.peak_vram = max(self.peak_vram, cur)

    def _read_ram(self):
        while not self._stop.wait(self.interval):
            if psutil:
                self.peak_ram = max(self.peak_ram, psutil.virtual_memory().used / 2**30)

    def __enter__(self):
        try:
            self._proc = subprocess.Popen(["typeperf", self.COUNTER, "-si", str(self.interval)],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            t = threading.Thread(target=self._read_typeperf, daemon=True)
            t.start()
            self._threads.append(t)
        except OSError:
            self._proc = None
        t = threading.Thread(target=self._read_ram, daemon=True)
        t.start()
        self._threads.append(t)
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._proc:
            self._proc.terminate()
        return False

    def summary(self) -> dict:
        return {
            "peak_vram_dedicated_gb": round(self.peak_vram, 2) if self._proc else None,
            "vram_at_start_gb": round(self.baseline_vram, 2) if self.baseline_vram is not None else None,
            "peak_ram_used_gb": round(self.peak_ram, 2) if psutil else None,
        }
