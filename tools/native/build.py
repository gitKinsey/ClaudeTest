#!/usr/bin/env python3
"""Builds the native (host) firmware:  python3 tools/native/build.py [out]   ->  build/dc_native   (needs g++ and ArduinoJson.h; Pillow for the font table)"""
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BUILD = os.path.join(ROOT, "build")
AJ_URL = "https://github.com/bblanchon/ArduinoJson/releases/download/v7.2.1/ArduinoJson-v7.2.1.h"


def main(argv):
    os.makedirs(BUILD, exist_ok=True)
    aj_dir = os.path.join(BUILD, "aj")
    os.makedirs(aj_dir, exist_ok=True)
    aj = os.path.join(aj_dir, "ArduinoJson.h")
    if not os.path.exists(aj):
        urllib.request.urlretrieve(AJ_URL, aj)
    font = os.path.join(BUILD, "font_data.h")
    if not os.path.exists(font) or os.path.getmtime(font) < os.path.getmtime(os.path.join(HERE, "gen_font.py")):
        subprocess.check_call([sys.executable, os.path.join(HERE, "gen_font.py"), font])
    out = argv[0] if argv else os.path.join(BUILD, "dc_native")
    ino = os.path.join(ROOT, "DeskCompanion", "DeskCompanion.ino")
    flags = ["-std=gnu++17", "-O1", "-g", "-w", "-pthread", "-DGC9A01_DRIVER=1", "-DDC_NATIVE=1", "-DARDUINOJSON_ENABLE_ARDUINO_STRING=1", "-DARDUINOJSON_ENABLE_ARDUINO_STREAM=0", "-DARDUINOJSON_ENABLE_ARDUINO_PRINT=0", "-DARDUINOJSON_ENABLE_PROGMEM=0", "-DARDUINO_USB_CDC_ON_BOOT=1", "-DARDUINO_USB_MODE=0", "-I", os.path.join(HERE, "shim"), "-I", aj_dir]
    extra = os.environ.get("DC_NATIVE_FLAGS", "").split()
    cmd = ["g++"] + flags + extra + ["-x", "c++", ino, "-x", "c++", os.path.join(HERE, "native_main.cpp"), "-o", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-12000:])
        print("NATIVE BUILD FAILED")
        return 1
    print("built", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
