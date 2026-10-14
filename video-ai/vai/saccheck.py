"""Detecte les binaires du venv refuses par Smart App Control / Code Integrity (WinError 4551).

Chaque .dll/.pyd est mappe en image (LoadLibraryExW + DONT_RESOLVE_DLL_REFERENCES) : la verification
de Code Integrity a lieu, mais aucun code du module n'est execute. Un fichier refuse fera echouer
l'import Python correspondant au moment ou ComfyUI en a besoin.

  python -m vai.saccheck [racine]   (defaut : site-packages du venv courant)
"""
from __future__ import annotations

import ctypes
import json
import sys
import sysconfig
from ctypes import wintypes
from pathlib import Path

DONT_RESOLVE_DLL_REFERENCES = 0x00000001
ERROR_BLOCKED_BY_POLICY = (4551, 1260)  # 4551 : controle d'application ; 1260 : strategie de groupe

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
_k32.LoadLibraryExW.restype = wintypes.HMODULE
_k32.FreeLibrary.argtypes = [wintypes.HMODULE]


def check(root: Path) -> dict:
    blocked, other, total = [], [], 0
    for f in sorted(root.rglob("*")):
        if f.suffix.lower() not in (".dll", ".pyd") or not f.is_file():
            continue
        total += 1
        h = _k32.LoadLibraryExW(str(f), None, DONT_RESOLVE_DLL_REFERENCES)
        if h:
            _k32.FreeLibrary(h)
            continue
        err = ctypes.get_last_error()
        (blocked if err in ERROR_BLOCKED_BY_POLICY else other).append({"file": str(f.relative_to(root)), "error": err})
    return {"root": str(root), "checked": total, "blocked": blocked, "other_errors": other}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(sysconfig.get_paths()["purelib"])
    res = check(root)
    print(json.dumps(res, indent=2))
    return 1 if res["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
