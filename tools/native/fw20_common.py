#!/usr/bin/env python3
"""Shared helpers of the firmware 2.0 native tests (see fw20_test.py).
Firmware 2.0 tests: runs the REAL sketch (native host build) and drives keys / dial / USB through the same pins and serial protocol as a pad.
    python tools/native/build.py build/dc_native && python tools/native/fw20_test.py build/dc_native [group ...]
Needs the full build (HID recorded as '#hid ...'), not the -DDC_SIM one."""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from native_emu import NativeEmu    # noqa: E402

FAILS = []


def expect(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("  FAIL:", msg)
    return cond


def combo(*k):
    return {"type": "combo", "val": list(k)}


def remap(e, key, spec, gesture="tap", layer=0):
    r = e.request(dict({"cmd": "remap", "key": key, "layer": layer, "gesture": gesture}, **spec))
    expect(r.get("ok"), f"remap {key}/{gesture}: {r}")
    return r


def presses(e):
    """keyboard key codes pressed since the last clear, in order"""
    with e.lock:
        return [int(h[2]) for h in e.hid if h[0] == "kb" and h[1] in ("press", "write")]


def hid(e):
    with e.lock:
        return [" ".join(h) for h in e.hid]


def clear(e):
    with e.lock:
        e.hid.clear()
        e.events.clear()


def tap(e, k, ms=0.06):
    e.control(f"#key {k} 1"); time.sleep(ms); e.control(f"#key {k} 0"); time.sleep(0.12)


def fresh(binary, name, keep=False):
    wd = os.path.join(tempfile.gettempdir(), "fw20_" + name)
    e = NativeEmu(binary, wd, keep_state=keep)
    time.sleep(0.8)
    e.request({"cmd": "hello"})
    e.request({"cmd": "events", "val": True})
    return e


def a(c):
    return ord(c)




def frame(e, timeout=40):
    """the sprite as raw RGB565 bytes (240 x 240)"""
    import emulator_test as et
    return et.snapshot(e, timeout)


def px(raw, x, y):
    i = (y * 240 + x) * 2
    return raw[i] << 8 | raw[i + 1]


def region(raw, x0, y0, x1, y1):
    """number of non-black pixels in [x0, x1) x [y0, y1)"""
    return sum(1 for y in range(y0, y1) for x in range(x0, x1) if px(raw, x, y))


def setmode(e, m):
    r = e.request({"cmd": "mode", "val": m})
    expect(r.get("ok"), f"mode {m}: {r}")
    time.sleep(0.15)


def appstate(e):
    return e.request({"cmd": "app", "op": "state"})


def turn(e, n, pause=0.12):
    e.control(f"#enc {n}")
    time.sleep(max(pause, abs(n) * 0.0021 + 0.1))                   # the native driver paces every edge (0.9 ms)


def click(e, ms=0.08):
    e.control("#encsw 1"); time.sleep(ms); e.control("#encsw 0"); time.sleep(0.15)


def holddial(e, secs):
    e.control("#encsw 1"); time.sleep(secs); e.control("#encsw 0"); time.sleep(0.3)
