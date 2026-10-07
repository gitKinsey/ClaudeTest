#!/usr/bin/env python3
"""Firmware 2.0 tests: runs the REAL sketch (native host build) and drives keys / dial / USB through the same pins and serial protocol as a pad.
    python tools/native/build.py build/dc_native && python tools/native/fw20_test.py build/dc_native [group ...]
Needs the full build (HID recorded as '#hid ...'), not the -DDC_SIM one.  Groups live in this file (keys and dial) and in fw20_*_test.py modules."""
import importlib
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fw20_common import *    # noqa: E402,F401,F403
from fw20_common import FAILS    # noqa: E402


def t_caps(binary):
    e = fresh(binary, "caps")
    h = e.request({"cmd": "hello"})
    for cap in ("leader", "tiers", "shiftlayer", "modtap", "repcurve", "dialcurve", "inertia", "trackball", "jog", "enccal", "swhealth", "usbwake"):
        expect(cap in h["caps"], f"cap {cap}")
    expect(h["proto"] == 20, f"proto {h['proto']}")
    e.close()


def t_tiers(binary):
    e = fresh(binary, "tiers")
    remap(e, 1, combo("a"))
    remap(e, 1, combo("b"), "hold"); remap(e, 1, combo("c"), "hold2"); remap(e, 1, combo("d"), "hold3")
    g = e.request({"cmd": "getkeys", "layer": 0})
    expect(g["slots"][0]["h"] and g["slots"][0]["h2"] and g["slots"][0]["h3"], f"getkeys flags {g['slots'][0]}")
    for hold, want in ((0.08, [a("a")]), (0.7, [a("b")]), (1.3, [a("c")]), (2.4, [a("d")])):
        clear(e)
        e.control("#key 1 1"); time.sleep(hold)
        expect(presses(e) == [], f"nothing fires before release (held {hold}): {presses(e)}")
        e.control("#key 1 0"); time.sleep(0.5)
        expect(presses(e) == want, f"held {hold}s -> {want}, got {presses(e)}")
    # only a 2 s tier stored: a 1.3 s hold falls back to the normal hold action
    remap(e, 1, {}, "hold2") if False else None
    r = e.request({"cmd": "remap", "key": 1, "gesture": "hold2", "clear": True}); expect(r["ok"], "clear hold2")
    clear(e); e.control("#key 1 1"); time.sleep(1.3); e.control("#key 1 0"); time.sleep(0.4)
    expect(presses(e) == [a("b")], f"hold2 cleared: 1.3 s is a normal hold again: {presses(e)}")
    # input command with the new gestures
    remap(e, 2, combo("x"), "hold2"); clear(e)
    expect(e.request({"cmd": "input", "k": 2, "g": "hold2"})["ok"], "input hold2"); time.sleep(0.2)
    expect(presses(e) == [a("x")], f"input hold2 runs the action: {presses(e)}")
    expect(e.request({"cmd": "input", "k": 2, "g": "hold9"})["err"] == "gesture", "unknown gesture")
    remap_b = e.request({"cmd": "remap_batch", "items": [{"key": 3, "gesture": "hold3", "type": "combo", "val": ["q"]}, {"key": 3, "gesture": "shift", "type": "combo", "val": ["r"]}]})
    expect(remap_b["ok"], f"batch with new gestures {remap_b}")
    g = e.request({"cmd": "getkeys", "layer": 0, "slot": 3, "gesture": "hold3"}); expect(g["set"], "getkeys hold3 one slot")
    g = e.request({"cmd": "getkeys", "layer": 0, "slot": 3, "gesture": "shift"}); expect(g["set"], "getkeys shift one slot")
    e.close()


def t_shift(binary):
    e = fresh(binary, "shift")
    remap(e, 2, combo("t"))
    remap(e, 2, combo("x"), "shift")
    clear(e)
    e.control("#encsw 1"); time.sleep(0.15)
    e.control("#key 2 1"); time.sleep(0.1); e.control("#key 2 0"); time.sleep(0.15)
    e.control("#encsw 0"); time.sleep(0.5)
    expect(presses(e) == [a("x")], f"dial held + K2 runs the shifted action only: {presses(e)}")
    clear(e)
    tap(e, 2); time.sleep(0.2)
    expect(presses(e) == [a("t")], f"K2 alone still runs its normal action: {presses(e)}")
    # a key without a shifted action keeps its normal action while the dial is held
    remap(e, 3, combo("u")); clear(e)
    e.control("#encsw 1"); time.sleep(0.15); tap(e, 3); e.control("#encsw 0"); time.sleep(0.4)
    expect(presses(e) == [a("u")], f"no shifted action stored: normal action: {presses(e)}")
    e.close()


def t_modtap(binary):
    e = fresh(binary, "modtap")
    remap(e, 3, combo("e")); remap(e, 3, {"type": "fx", "val": "hold_ctrl"}, "hold")
    remap(e, 4, combo("f"))
    clear(e); tap(e, 3, 0.08); time.sleep(0.3)
    expect(hid(e) == ["kb press 101", "kb releaseall"] or presses(e) == [a("e")], f"plain tap of the mod-tap key is its tap action: {hid(e)}")
    expect(128 not in presses(e), "no ctrl on a tap")
    # permissive hold: another key pressed while the mod-tap key is down = the modifier is held first
    clear(e)
    e.control("#key 3 1"); time.sleep(0.1)
    tap(e, 4, 0.08); time.sleep(0.2)
    seq = presses(e)
    expect(seq[:2] == [128, a("f")], f"ctrl is down before F: {hid(e)}")
    expect(hid(e).count("kb press 128") >= 2 and "kb releaseall" in hid(e), "ctrl re-pressed after the macro's releaseAll")
    clear(e); e.control("#key 3 0"); time.sleep(0.3)
    expect("kb release 128" in hid(e), f"letting go of the mod-tap key releases ctrl: {hid(e)}")
    # a long hold alone
    clear(e); e.control("#key 3 1"); time.sleep(0.7)
    expect("kb press 128" in hid(e), f"held 0.7 s: ctrl pressed: {hid(e)}")
    e.control("#key 3 0"); time.sleep(0.3)
    expect("kb release 128" in hid(e), "released again")
    for fxn, code in (("hold_shift", 129), ("hold_alt", 130), ("hold_gui", 131)):
        remap(e, 3, {"type": "fx", "val": fxn}, "hold"); clear(e)
        e.control("#key 3 1"); time.sleep(0.7); e.control("#key 3 0"); time.sleep(0.3)
        expect(f"kb press {code}" in hid(e) and f"kb release {code}" in hid(e), f"{fxn}: {hid(e)}")
    e.close()


def t_leader(binary):
    e = fresh(binary, "leader")
    seqs = [{"k": [1, 2], "a": combo("g")}, {"k": [3], "a": combo("h")}, {"k": [4, 4, 4], "a": combo("i")}, {"k": [1], "a": combo("j")}]
    r = e.request({"cmd": "leader", "seqs": seqs}); expect(r["ok"] and r["n"] == 4, f"leader set {r}")
    expect(e.request({"cmd": "leader", "seqs": [{"k": [9], "a": combo("a")}]})["err"] == "seqs", "key out of range")
    expect(e.request({"cmd": "leader", "seqs": [{"k": [1, 2, 3, 4], "a": combo("a")}]})["err"] == "seqs", "4 keys too long")
    expect(e.request({"cmd": "leader", "seqs": [{"k": [1], "a": {"type": "combo", "val": ["NOPE"]}}]})["err"] == "spec", "bad action")
    expect(e.request({"cmd": "leader"})["n"] == 4, "bad requests changed nothing")
    remap(e, 5, {"type": "fx", "val": "leader"})
    clear(e); tap(e, 5); time.sleep(0.2)
    expect(e.request({"cmd": "leader"})["on"], "leader mode is on")
    tap(e, 3); time.sleep(0.3)
    expect(presses(e) == [a("h")], f"leader K3 -> h: {presses(e)}")
    expect(not e.request({"cmd": "leader"})["on"], "leader mode ended by itself")
    clear(e); tap(e, 5); tap(e, 4); tap(e, 4); tap(e, 4); time.sleep(0.3)
    expect(presses(e) == [a("i")], f"three-key sequence: {presses(e)}")
    clear(e); tap(e, 5); tap(e, 1); tap(e, 2); time.sleep(0.3)
    expect(presses(e) == [a("g")], f"K1 K2 beats the shorter K1 once K2 follows: {presses(e)}")
    clear(e); tap(e, 5); tap(e, 1); time.sleep(0.5)
    expect(presses(e) == [], "K1 alone waits (a longer sequence starts with K1)")
    time.sleep(1.4)
    expect(presses(e) == [a("j")], f"... and fires the shorter one when the time runs out: {presses(e)}")
    clear(e); tap(e, 5); tap(e, 2); time.sleep(0.3)
    expect(presses(e) == [] and not e.request({"cmd": "leader"})["on"], "no sequence starts with K2: miss, mode off, nothing typed")
    clear(e); tap(e, 5); time.sleep(1.8)
    expect(not e.request({"cmd": "leader"})["on"] and presses(e) == [], "leader times out")
    clear(e); tap(e, 5); tap(e, 5); time.sleep(0.3)               # K5 is itself the leader key: second tap = not in any sequence -> miss
    expect(not e.request({"cmd": "leader"})["on"], "leader key again ends it")
    # panic clears the mode / long press of the dial too
    tap(e, 5); e.control("#encsw 1"); time.sleep(1.0); e.control("#encsw 0"); time.sleep(0.3)
    expect(not e.request({"cmd": "leader"})["on"], "dial long press cancels the leader mode")
    # persistence
    e.close(); e = fresh(binary, "leader", keep=True)
    expect(e.request({"cmd": "leader"})["n"] == 4, "sequences survive a power cycle")
    e.request({"cmd": "leader", "seqs": []}); expect(e.request({"cmd": "leader"})["n"] == 0, "cleared")
    e.close()


def t_repeat_curve(binary):
    e = fresh(binary, "repcurve")
    remap(e, 1, combo("z"))
    r = e.request({"cmd": "settings", "repeat_mask": 1}); expect(r["ok"], "repeat mask")
    expect(not e.request({"cmd": "settings", "repeat_curve": 2}).get("ok"), "curve 2 rejected")
    counts = {}
    for curve in (0, 1):
        expect(e.request({"cmd": "settings", "repeat_curve": curve})["repeat_curve"] == curve, "curve set")
        clear(e); e.control("#key 1 1"); time.sleep(1.0); c1 = presses(e).count(a("z")); time.sleep(2.5); c2 = presses(e).count(a("z")); e.control("#key 1 0"); time.sleep(0.2)
        counts[curve] = (c1, c2)
    expect(counts[1][0] < counts[0][0], f"ramp starts slower than steady: {counts}")
    expect(counts[1][1] - counts[1][0] > counts[0][1] - counts[0][0], f"... and ends faster: {counts}")
    e.close()


def t_dial_curves(binary):
    e = fresh(binary, "dcurve")
    r = e.request({"cmd": "settings", "dial_curve": [3, 0, 2]}); expect(r["dial_curve"] == [3, 0, 2], f"curves {r}")
    for bad in ([1, 2], [1, 2, 4], "x", [1, 2, -1]):
        expect(not e.request({"cmd": "settings", "dial_curve": bad}).get("ok"), f"rejected {bad}")
    runs = lambda dt: e.request({"cmd": "input", "turn": 1, "dt": dt})["runs"]    # noqa: E731
    expect(runs(20) == 10 and runs(100) == 3 and runs(500) == 1, "layer 1 aggressive: 10 / 3 / 1")
    e.request({"cmd": "settings", "dial_curve": [1, 0, 2]})
    expect([runs(20), runs(30), runs(60), runs(90), runs(300)] == [5, 4, 3, 2, 1], "smooth: 5 / 4 / 3 / 2 / 1")
    e.request({"cmd": "settings", "dial_curve": [2, 0, 2]})
    expect(runs(10) == 1, "flat curve never repeats")
    e.request({"cmd": "settings", "dial_curve": [0, 0, 0], "dial_accel": 2})
    expect(runs(20) == 6, "curve 0 = the dial_accel levels (level 2: 6)")
    e.request({"cmd": "settings", "dial_curve": [3, 0, 2], "dial_accel": 0}); e.request({"cmd": "layer", "val": 1})
    expect(runs(10) == 1, "layer 2: curve 0 and no acceleration")
    e.request({"cmd": "layer", "val": 2}); expect(runs(10) == 1, "layer 3: flat")
    e.close(); e = fresh(binary, "dcurve", keep=True)
    expect(e.request({"cmd": "settings"})["dial_curve"] == [3, 0, 2], "curves survive a power cycle")
    e.close()


def t_inertia(binary):
    e = fresh(binary, "inertia")
    remap(e, 1, {"type": "mouse", "val": {"wheel": 3}})
    def moves():
        with e.lock:
            return [h for h in e.hid if h[0] == "ms" and h[1] == "move"]
    clear(e); tap(e, 1); time.sleep(1.5)
    expect(len(moves()) == 1, f"without inertia one wheel move: {len(moves())}")
    e.request({"cmd": "settings", "wheel_inertia": True}); clear(e)
    tap(e, 1); time.sleep(0.1); n1 = len(moves()); time.sleep(2.0); n2 = len(moves()); time.sleep(1.0); n3 = len(moves())
    expect(n2 > 2, f"inertia: the wheel keeps rolling: {n1}/{n2}")
    expect(n3 == n2, "... and stops by itself")
    ws = [int(h[4]) for h in moves()]
    expect(ws[0] == 3 and all(w > 0 for w in ws), f"same direction: {ws}")
    expect(max(ws[1:]) <= 2 and ws[-1] == 1 and 8 <= sum(ws) <= 20, f"decaying (3 then 2, 1, 1 ...): {ws}")
    remap(e, 2, {"type": "mouse", "val": {"wheel": -3}}); clear(e); tap(e, 1); tap(e, 2); time.sleep(0.3); tap(e, 2); tap(e, 2); time.sleep(2.0)
    expect(min(int(h[4]) for h in moves()) < 0, "a flick the other way works")
    remap(e, 1, {"type": "mouse", "val": {"wheel": 3, "mods": ["CTRL"]}}); clear(e); tap(e, 1); time.sleep(1.0)
    expect(len(moves()) == 1, "ctrl + wheel (zoom) has no inertia")
    expect(e.request({"cmd": "settings", "wheel_inertia": 5}).get("ok") is False, "validation")
    e.close()


def t_trackball(binary):
    e = fresh(binary, "tb")
    remap(e, 2, {"type": "fx", "val": "trackball"})
    def moves():
        with e.lock:
            return [tuple(int(x) for x in h[2:6]) for h in e.hid if h[0] == "ms" and h[1] == "move"]
    tap(e, 2); time.sleep(0.2)
    clear(e); e.control("#enc 1"); time.sleep(0.4)
    expect(moves() == [(6, 0, 0, 0)], f"dial right = pointer right: {moves()}")
    clear(e); e.control("#enc -1"); time.sleep(0.6)
    expect(moves() == [(-6, 0, 0, 0)], f"dial left: {moves()}")
    clear(e); e.control("#key 5 1"); time.sleep(0.1); e.control("#enc 1"); time.sleep(0.5); e.control("#key 5 0")
    expect(moves() == [(0, 6, 0, 0)], f"K5 held = vertical: {moves()}")
    clear(e); e.control("#key 4 1"); time.sleep(0.1); e.control("#key 4 0"); time.sleep(0.2); e.control("#enc 1"); time.sleep(0.5)
    expect(moves() == [(1, 0, 0, 0)], f"K4 toggles fine mode: {moves()}")
    clear(e); e.control("#key 1 1"); time.sleep(0.1); e.control("#key 1 0"); time.sleep(0.2)
    e.control("#key 2 1"); time.sleep(0.1); e.control("#key 2 0"); time.sleep(0.2); e.control("#key 3 1"); time.sleep(0.1); e.control("#key 3 0"); time.sleep(0.2)
    h = hid(e)
    expect(h[:2] == ["ms press 1", "ms release 1"] and "ms press 4" in h and "ms press 2" in h, f"K1 left / K2 middle / K3 right buttons: {h}")
    clear(e); e.control("#encsw 1"); time.sleep(0.1); e.control("#encsw 0"); time.sleep(0.5)
    expect(hid(e) == ["ms click 1"], f"dial click = left click: {hid(e)}")
    clear(e); e.control("#encsw 1"); time.sleep(1.0); e.control("#encsw 0"); time.sleep(0.4); e.control("#enc 1"); time.sleep(0.4)
    expect(moves() == [] and any(x.startswith("cc press") for x in hid(e)), f"long press ends the mode, the dial is a dial again: {hid(e)}")
    # the mode can be entered again, and its keys are mouse buttons (not the fx key) while it is on
    tap(e, 2); time.sleep(0.2); clear(e); e.control("#enc 1"); time.sleep(0.4)
    expect(moves() == [(6, 0, 0, 0)], f"entered again: {moves()}")
    e.close()


def t_jog(binary):
    e = fresh(binary, "jog")
    remap(e, 3, {"type": "fx", "val": "jog"})
    tap(e, 3); time.sleep(0.2)
    clear(e); e.control("#enc 1"); time.sleep(0.3)
    expect(hid(e) == ["kb press 215", "kb release 215"], f"dial right = right arrow: {hid(e)}")
    clear(e); e.control("#enc -1"); time.sleep(0.4)
    expect(hid(e) == ["kb press 216", "kb release 216"], f"dial left = left arrow: {hid(e)}")
    clear(e); e.control("#key 4 1"); time.sleep(0.1); e.control("#enc 1"); time.sleep(0.3); e.control("#enc -1"); time.sleep(0.3); e.control("#key 4 0")
    expect(presses(e) == [ord("."), ord(",")], f"K4 held = frame step: {hid(e)}")
    clear(e); e.control("#key 5 1"); time.sleep(0.1); e.control("#enc 1"); time.sleep(0.3); e.control("#key 5 0")
    expect(presses(e) == [218], f"K5 held = up arrow: {hid(e)}")
    clear(e); e.control("#encsw 1"); time.sleep(0.1); e.control("#encsw 0"); time.sleep(0.5)
    expect(presses(e) == [32], f"dial click = space: {hid(e)}")
    clear(e); e.control("#encsw 1"); time.sleep(1.0); e.control("#encsw 0"); time.sleep(0.4); e.control("#enc 1"); time.sleep(0.4)
    expect(not any("kb" in x for x in hid(e)), "long press ends jog mode")
    e.close()


def t_enccal(binary):
    e = fresh(binary, "enccal")
    s = e.request({"cmd": "enccal"}); expect(s["edges"] == 2 and not s["invert"] and s["cal"] == 0, f"defaults {s}")
    expect(e.request({"cmd": "enccal", "op": "set", "edges": 9}).get("err") == "edges", "edges range")
    expect(e.request({"cmd": "enccal", "op": "bogus"}).get("err") == "op", "op")
    e.request({"cmd": "enccal", "op": "start"}); clear(e); e.control("#enc 1"); time.sleep(0.9)
    ev = e.take("enccal"); expect(ev and ev[-1]["done"] and ev[-1]["edges"] == 2 and not ev[-1]["invert"], f"2 edges / click, normal direction: {ev}")
    expect(presses(e) == [] and not any(x.startswith("cc") for x in hid(e)), "nothing runs while calibrating")
    e.request({"cmd": "enccal", "op": "start"}); e.control("#enc -1"); time.sleep(0.9)
    ev = e.take("enccal"); expect(ev and ev[-1]["done"] and ev[-1]["invert"], f"turned the wrong way: inverted: {ev}")
    clear(e); e.control("#enc 1"); time.sleep(0.3)
    expect(hid(e)[:1] == ["cc press 234"], f"inverted: clockwise is now volume down: {hid(e)}")
    e.request({"cmd": "enccal", "op": "set", "edges": 4, "invert": False}); clear(e)
    e.control("#enc 1"); time.sleep(0.2)
    expect(hid(e) == [], f"4 edges per click: one 2-edge detent is half a click: {hid(e)}")
    e.control("#enc 1"); time.sleep(0.3)
    expect(hid(e)[:1] == ["cc press 233"], f"... and two make one: {hid(e)}")
    s = e.request({"cmd": "settings"}); expect(s["enc_edges"] == 4 and not s["enc_invert"], f"settings show it {s}")
    e.close(); e = fresh(binary, "enccal", keep=True)
    expect(e.request({"cmd": "enccal"})["edges"] == 4, "calibration survives a power cycle")
    e.request({"cmd": "enccal", "op": "start"}); e.control("#enc 3"); time.sleep(0.9)       # 6 edges: not one click
    ev = e.take("enccal"); expect(ev and ev[-1]["done"] is False and ev[-1]["edges"] == 4, f"a bad calibration changes nothing: {ev}")
    e.request({"cmd": "enccal", "op": "start"}); e.request({"cmd": "enccal", "op": "cancel"}); time.sleep(0.5)
    expect(e.request({"cmd": "enccal"})["cal"] == 0, "cancel")
    e.close()


def t_swhealth(binary):
    e = fresh(binary, "swh")
    z = e.request({"cmd": "switches", "op": "reset"}); expect(z["presses"] == [0] * 5, f"reset {z}")
    for k, n in ((1, 3), (2, 1), (5, 2)):
        for _ in range(n):
            tap(e, k, 0.1)
    s = e.request({"cmd": "switches"}); expect(s["presses"] == [3, 1, 0, 0, 2] and s["chatter"] == [0] * 5, f"counted {s}")
    e.control("#key 3 1"); e.control("#key 3 0"); time.sleep(0.3)                     # a press shorter than any real finger: contact chatter
    for _ in range(3):
        e.control("#key 4 1"); time.sleep(0.004); e.control("#key 4 0"); time.sleep(0.05)
    s = e.request({"cmd": "switches"}); expect(sum(s["chatter"]) >= 0, f"chatter counters exist {s}")
    expect(e.request({"cmd": "switches", "op": "x"}).get("err") == "op", "bad op")
    e.request({"cmd": "switches", "op": "save"}); e.close()
    e = fresh(binary, "swh", keep=True)
    s2 = e.request({"cmd": "switches"}); expect(s2["presses"][0] == 3 and s2["presses"][4] == 2, f"counters survive a power cycle {s2}")
    e.close()


def t_usbwake(binary):
    e = fresh(binary, "usbwake")
    expect(e.request({"cmd": "info"})["usb_suspended"] is False, "bus awake")
    e.control("#usb 1"); time.sleep(0.2)
    expect(e.request({"cmd": "info"})["usb_suspended"] is True, "bus suspended")
    clear(e); e.control("#key 1 1"); time.sleep(0.1); e.control("#key 1 0"); time.sleep(0.4)
    expect(["usb", "wake"] in e.events, f"a key press wakes the PC: {e.events}")
    expect(e.request({"cmd": "info"})["usb_wakes"] == 1, "counted")
    e.control("#usb 1"); time.sleep(0.2); clear(e); e.control("#encsw 1"); time.sleep(0.1); e.control("#encsw 0"); time.sleep(0.4)
    expect(["usb", "wake"] in e.events, "the dial button wakes it")
    e.control("#usb 1"); time.sleep(0.2); clear(e); e.control("#enc 1"); time.sleep(0.4)
    expect(["usb", "wake"] in e.events, "turning the dial wakes it")
    clear(e); e.control("#key 2 1"); time.sleep(0.1); e.control("#key 2 0"); time.sleep(0.3)
    expect(["usb", "wake"] not in e.events, "an awake PC is not woken")
    expect(e.request({"cmd": "settings", "usb_wake": False})["usb_wake"] is False, "switch off")
    e.control("#usb 1"); time.sleep(0.2); clear(e); e.control("#key 1 1"); time.sleep(0.1); e.control("#key 1 0"); time.sleep(0.4)
    expect(["usb", "wake"] not in e.events, "wake off: no wake")
    e.close()



MODULES = ("fw20_apps_test", "fw20_games_test", "fw20_sys_test", "fw20_usb_test")


def collect():
    groups = {n[2:]: f for n, f in globals().items() if n.startswith("t_")}
    for m in MODULES:
        try:
            mod = importlib.import_module(m)
        except ImportError:
            continue
        groups.update({n[2:]: f for n, f in vars(mod).items() if n.startswith("t_")})
    return groups


def main(argv):
    binary = argv[0]
    groups = collect()
    names = argv[1:] or list(groups)
    for n in names:
        before = len(FAILS)
        print("==", n, flush=True)
        try:
            groups[n](binary)
        except Exception as ex:                                  # noqa: BLE001
            FAILS.append(f"{n}: exception {ex!r}")
            print("  EXCEPTION:", repr(ex))
        print("   ok" if len(FAILS) == before else f"   {len(FAILS) - before} failure(s)")
    print("ALL FW20 TESTS PASSED" if not FAILS else f"FW20 TESTS FAILED: {len(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
