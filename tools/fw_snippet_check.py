#!/usr/bin/env python3
"""Syntax / type check of the firmware 1.6 additions without the ESP32 toolchain: the new functions are cut out of DeskCompanion.ino, put behind small stand-ins
for the Arduino / TFT_eSPI / Preferences pieces they touch, and compiled with the host g++ against the real ArduinoJson header.

    python3 tools/fw_snippet_check.py [path/to/ArduinoJson.h]        (default: download ArduinoJson 7.2.1 into /tmp)

This catches typos, wrong ArduinoJson calls and type errors in those functions. It is NOT a build of the firmware: the real check is the CI compile matrix
(`arduino-cli compile`) and the QEMU suite."""
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INO = os.path.join(ROOT, "DeskCompanion", "DeskCompanion.ino")
AJ_URL = "https://github.com/bblanchon/ArduinoJson/releases/download/v7.2.1/ArduinoJson-v7.2.1.h"

STUBS = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cmath>
#include <string>
#include <vector>
#include "ArduinoJson.h"
#define constrain(a, lo, hi) ((a) < (lo) ? (lo) : (a) > (hi) ? (hi) : (a))
using String = std::string;
struct Preferences {
  size_t putString(const char*, const String&) { return 1; }
  bool remove(const char*) { return true; } String getString(const char*, const char*) { return String(); } bool isKey(const char*) { return true; }
  size_t putUChar(const char*, uint8_t) { return 1; } uint8_t getUChar(const char*, uint8_t d) { return d; }
};
static Preferences prefs;
static uint32_t millis() { return 1; }
static const uint8_t LAYERS = 3;
static uint8_t mode = 1, brightness = 200, curLayer = 0;
static bool needRedraw = false, okSprite = true; static uint32_t layerToastUntil = 0, toastUntil = 0, flashAt = 0; static int32_t reqId = -1; static uint32_t lastRxMs = 0;
static uint16_t C_BG = 0, C_TXT = 1, C_DIM = 2, C_DIM2 = 3, C_GRAY = 4, C_ACC = 5, C_ACC2 = 6, C_OK = 7, C_WARN = 8, C_RED = 9;
static char toastTxt[10] = "";
static uint8_t accentIdx = 0; static char ctxTxt[17] = ""; static uint32_t ctxUntil = 0; static char lnames[LAYERS][11];
static char msgTxt[25] = ""; static uint8_t msgKind = 0; static uint32_t msgUntil = 0; static bool stateDirty = false;
static uint32_t loopMaxUs = 0, loopCnt = 0, rxOverruns = 0; static uint64_t loopSumUs = 0; static uint32_t usbDrops = 0;
static const uint8_t PROTO_LEVEL = 16;
struct Step { int x; };
struct Spr { const void* getPointer() { return buf; } uint8_t buf[240 * 240 * 2];
  template <class... A> void fillRoundRect(A...) {} template <class... A> void drawRoundRect(A...) {} template <class... A> void setTextDatum(A...) {}
  template <class... A> void setTextColor(A...) {} template <class... A> void drawString(A...) {} } spr;
#define MC_DATUM 0
static bool hostActive() { return true; }
static void sendDoc(JsonDocument&) {} static void ack(const char*) {} static void nack(const char*) {}
static void txRaw(const uint8_t*, size_t) {} static void b64enc(const uint8_t*, size_t, char*) {}
static bool parseSpec(JsonVariantConst, std::vector<Step>&) { return true; }
static void gestureKey(uint8_t, uint8_t, char, char*) {} static void slotKey(uint8_t, uint8_t, char*) {} static void loadGestureFlags() {}
static void activity() {} static bool gameBusy() { return false; }
static void arcBand(int, int, float, float, float, float, uint16_t) {}
'''


def cut(src, start, end):
    a = src.index(start)
    b = src.index(end, a)
    return src[a:b]


def main():
    aj = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ArduinoJson.h"
    if not os.path.exists(aj):
        try:
            urllib.request.urlretrieve(AJ_URL, aj)
        except OSError as e:
            print(f"SKIPPED: cannot download ArduinoJson ({e})")
            return 0
    src = open(INO, encoding="utf-8").read()
    parts = [
        cut(src, "static void evtState() {", "static void setLayer(uint8_t n) {"),
        cut(src, "static void drawOverlays() {", "// ================================================================ drawing helpers" if False else "\n}\n", ) + "\n}\n",
        cut(src, "static uint8_t snapScale = 1;", "static void cmdRun(JsonDocument& doc)"),
        cut(src, "// ---- 1.6 helpers for the companion app 2.0", "// ---- {\"cmd\":\"gesture_test\""),
    ]
    code = STUBS + "\n".join(parts)
    code = re.sub(r"static void drawOverlays\(\);", "", code)
    with tempfile.TemporaryDirectory() as d:
        shutil_aj = os.path.join(d, "ArduinoJson.h")
        os.symlink(os.path.abspath(aj), shutil_aj)
        path = os.path.join(d, "snippet.cpp")
        open(path, "w").write(code)
        cmd = ["g++", "-std=gnu++17", "-fsyntax-only", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-I", d, path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-6000:])
            print("SNIPPET CHECK FAILED")
            return 1
        print("firmware 1.6 snippets: syntax and types OK (g++ + ArduinoJson 7.2.1)" + (("\n" + r.stderr) if r.stderr.strip() else ""))
        return 0


if __name__ == "__main__":
    sys.exit(main())
