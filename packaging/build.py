#!/usr/bin/env python3
"""Build a double-click Desk Companion with PyInstaller (Windows .exe / macOS .app / Linux binary), zipped into dist/.

    pip install pyinstaller customtkinter pyserial psutil pillow pynput esptool
    python packaging/build.py
"""
import os
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NAME = "DeskCompanion"


def main():
    os.chdir(ROOT)
    for d in ("build", "dist"):
        shutil.rmtree(d, ignore_errors=True)
    sep = ";" if platform.system() == "Windows" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
           "--collect-data", "customtkinter", "--collect-all", "esptool", "--add-data", f"firmware{sep}firmware",
           "--hidden-import", "desk_lib.ui", "--hidden-import", "desk_lib.wizards", "--hidden-import", "desk_lib.recorder",
           "--hidden-import", "desk_lib.feeds", "--hidden-import", "desk_lib.activewin", "--hidden-import", "desk_lib.hostactions",
           "--hidden-import", "desk_lib.backup", "--hidden-import", "desk_lib.espota",
           "--hidden-import", "pynput.keyboard", "--hidden-import", "pynput.mouse", "--hidden-import", "PIL._tkinter_finder"]
    if platform.system() == "Linux":
        cmd += ["--hidden-import", "pynput.keyboard._xorg", "--hidden-import", "pynput.mouse._xorg"]
    cmd.append("companion_app.py")
    print("running:", " ".join(cmd))
    subprocess.check_call(cmd)
    plat = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
    zipname = os.path.join("dist", f"{NAME}-{plat}.zip")
    src = os.path.join("dist", NAME + (".app" if platform.system() == "Darwin" else ""))
    with zipfile.ZipFile(zipname, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(src):
            for f in files:
                p = os.path.join(base, f)
                z.write(p, os.path.relpath(p, "dist"))
    print("wrote", zipname, os.path.getsize(zipname) // 1024, "KB")


if __name__ == "__main__":
    main()
