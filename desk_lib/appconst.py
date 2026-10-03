"""App-card constants and helpers (toolkit-free)."""
import os
import platform
import subprocess

SCALES = {"80 %": 0.8, "90 %": 0.9, "100 %": 1.0, "110 %": 1.1, "125 %": 1.25, "150 %": 1.5}


def open_folder(path):
    path = str(path)
    if platform.system() == "Windows":
        os.startfile(path)                                       # noqa: S606
    else:
        subprocess.Popen(["open" if platform.system() == "Darwin" else "xdg-open", path])      # noqa: S603
