#!/usr/bin/env python3
"""Differential test: the same request script is sent to the simulated pad (core/base.py SimFirmware + core/fw20_sim.py) and to the real sketch (native build); the
answers must agree.  This keeps the simulator - which the app tests and the 'Simulate pad' button use - honest about the 2.0 protocol.
    python tools/native/diff_test.py build/dc_native"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("DESK_COMPANION_HOME", "/tmp/dc_diff_home")
from fw20_common import fresh    # noqa: E402
import core.base as b    # noqa: E402

FAILS = []


class SimPipe:
    """SimFirmware driven synchronously: send a request, read the replies"""

    def __init__(self):
        self.out = []
        self.sim = b.SimFirmware(lambda raw: self.out.append(raw))

    def request(self, obj):
        self.out.clear()
        self.sim.feed((json.dumps(obj) + "\n").encode())
        for raw in self.out:
            for line in raw.splitlines():
                m = json.loads(line)
                if "ok" in m:
                    return m
        raise AssertionError(f"no reply to {obj}")

    def events(self):
        return [json.loads(l) for raw in self.out for l in raw.splitlines()]


def norm(r, ignore):
    return {k: v for k, v in r.items() if k not in ("id",) and k not in ignore}


def same(a, c, tol=None):
    """equality with an absolute tolerance for named float fields"""
    if set(a) != set(c):
        return False
    for k in a:
        if tol and k in tol:
            if abs(a[k] - c[k]) > tol[k]:
                return False
        elif a[k] != c[k]:
            return False
    return True


def run_case(sim, nat, req, ignore=(), tol=None, only=None):
    rs, rn = sim.request(dict(req)), nat.request(dict(req))
    ns, nn = norm(rs, ignore), norm(rn, ignore)
    if only:
        ns, nn = {k: ns[k] for k in only if k in ns}, {k: nn[k] for k in only if k in nn}
    if not same(ns, nn, tol):
        FAILS.append(req)
        print("  MISMATCH", json.dumps(req)[:140], "\n    sim :", json.dumps(ns)[:300], "\n    pad :", json.dumps(nn)[:300])
    return rs, rn


def main(binary):
    sim = SimPipe()
    nat = fresh(binary, "diff")
    run = lambda req, **kw: run_case(sim, nat, req, **kw)    # noqa: E731
    seed_t = 1234567890
    run({"cmd": "time", "epoch": seed_t, "tz": 0})
    # hello: the 2.0 capability list and protocol level agree
    hs, hn = sim.request({"cmd": "hello"}), nat.request({"cmd": "hello"})
    new = set(b.NEW20_CAPS)
    if not (new <= set(hs["caps"]) and new <= set(hn["caps"]) and hs["proto"] == hn["proto"] == 20):
        FAILS.append("hello")
        print("  MISMATCH hello", new - set(hs["caps"]), new - set(hn["caps"]), hs["proto"], hn["proto"])
    # keys: new gestures, confirm flag, batches
    for g in ("hold", "hold2", "hold3", "shift", "double", "triple"):
        run({"cmd": "remap", "key": 2, "layer": 1, "gesture": g, "type": "combo", "val": ["a"]})
        run({"cmd": "getkeys", "layer": 1, "slot": 2, "gesture": g})
    run({"cmd": "remap", "key": 2, "layer": 1, "gesture": "wiggle", "type": "combo", "val": ["a"]})
    run({"cmd": "remap", "key": 8, "gesture": "hold2", "type": "combo", "val": ["a"]})
    run({"cmd": "remap", "key": 1, "type": "combo", "val": ["a"], "confirm": True})
    run({"cmd": "getkeys", "layer": 0, "slot": 1})
    run({"cmd": "getkeys", "layer": 0}, only=("slots",))
    run({"cmd": "getkeys", "layer": 1}, only=("slots",))
    run({"cmd": "remap_batch", "items": [{"key": 3, "gesture": "hold3", "type": "text", "val": "x", "confirm": True}, {"key": 4, "gesture": "shift", "type": "fx", "val": "leader"}]})
    run({"cmd": "getkeys", "layer": 0, "slot": 3, "gesture": "hold3"})
    run({"cmd": "remap_batch", "items": [{"key": 3, "gesture": "hold9", "type": "text", "val": "x"}]})
    for fx in ("leader", "hold_ctrl", "hold_shift", "hold_alt", "hold_gui", "trackball", "jog", "nope"):
        run({"cmd": "remap", "key": 5, "type": "fx", "val": fx})
    run({"cmd": "input", "k": 2, "g": "hold2"})
    run({"cmd": "input", "k": 2, "g": "shift"})
    run({"cmd": "input", "k": 2, "g": "hold9"})
    # leader
    seqs = [{"k": [1, 2], "a": {"type": "combo", "val": ["g"]}}, {"k": [3], "a": {"type": "media", "val": "MUTE"}}]
    run({"cmd": "leader", "seqs": seqs})
    run({"cmd": "leader"})
    for bad in ([{"k": [9], "a": {"type": "combo", "val": ["a"]}}], [{"k": [1, 2, 3, 4], "a": {"type": "combo", "val": ["a"]}}], [{"k": [1], "a": {"type": "combo", "val": ["NOPE"]}}], [{"k": [], "a": {"type": "combo", "val": ["a"]}}], "x", [seqs[0]] * 25):
        run({"cmd": "leader", "seqs": bad})
    # switches / calibration
    run({"cmd": "switches", "op": "reset"})
    run({"cmd": "switches", "op": "bogus"})
    run({"cmd": "enccal"})
    run({"cmd": "enccal", "op": "set", "edges": 4, "invert": True})
    run({"cmd": "enccal", "op": "set", "edges": 9})
    run({"cmd": "enccal", "op": "set", "edges": 2, "invert": False})
    run({"cmd": "enccal", "op": "bogus"})
    # settings: every new field, every range
    keep = ("repeat_curve", "wheel_inertia", "usb_wake", "enc_edges", "enc_invert", "legend", "macro_ring", "suspend_dim", "ripples", "aa", "fold_text", "dial_curve", "scr_bright", "scr_saver",
            "clock_style", "saver_style", "mode_mask")
    good = [{"repeat_curve": 1}, {"wheel_inertia": True}, {"usb_wake": False}, {"enc_edges": 4}, {"enc_invert": True}, {"legend": 2}, {"macro_ring": False}, {"suspend_dim": False}, {"ripples": True}, {"aa": True},
            {"fold_text": True}, {"dial_curve": [1, 2, 3]}, {"scr_bright": [50] + [0] * 31}, {"scr_saver": [-1, 5] + [0] * 30}, {"clock_style": 8}, {"saver_style": 13}, {"mode_mask": 0xFFFFFFFF}, {"mode_mask": 0x3F}]
    for g in good:
        run(dict({"cmd": "settings"}, **g), only=keep)
    for bad in ({"repeat_curve": 2}, {"wheel_inertia": 1}, {"enc_edges": 0}, {"enc_edges": 5}, {"legend": 3}, {"dial_curve": [1, 2]}, {"dial_curve": [1, 2, 4]}, {"scr_bright": [1] + [0] * 31}, {"scr_bright": [0] * 31},
                {"scr_saver": [-2] + [0] * 31}, {"scr_saver": [3601] + [0] * 31}, {"clock_style": 9}, {"saver_style": 14}, {"saver_style": 0}, {"mode_mask": 0}, {"mode_mask": -1}, {"suspend_dim": "yes"}):
        run(dict({"cmd": "settings"}, **bad))
    run({"cmd": "settings", "fold_text": False, "wheel_inertia": False, "ripples": False, "aa": False, "legend": 0, "macro_ring": True, "suspend_dim": True, "usb_wake": True, "enc_edges": 2, "enc_invert": False,
         "repeat_curve": 0, "dial_curve": [0, 0, 0], "scr_bright": [0] * 32, "scr_saver": [0] * 32, "clock_style": 0, "saver_style": 1}, only=keep)
    # snippets
    items = [{"l": "Email", "t": "me@example.com"}, {"l": "Sig", "t": "Best,\nMe"}, {"l": "Hi", "t": "hello"}]
    run({"cmd": "snippets"})
    run({"cmd": "snippets", "op": "set", "items": items})
    run({"cmd": "snippets", "from": 1, "n": 5})
    for bad in ([{"l": "", "t": "x"}], [{"l": "x" * 15, "t": "x"}], [{"l": "ok", "t": "café"}], [{"l": "ok", "t": "x" * 121}], [{"l": "ok", "t": ""}], [{"l": "ok", "t": "x"}] * 13):
        run({"cmd": "snippets", "op": "set", "items": bad})
    run({"cmd": "snippets", "op": "set", "at": 99, "items": items[:2]})
    run({"cmd": "snippets", "op": "set", "at": 5, "total": 3, "items": items[:1]})
    run({"cmd": "snippets", "op": "set", "at": 3, "total": 4, "items": items[:1]})
    run({"cmd": "snippets", "op": "clear"})
    run({"cmd": "snippets", "op": "bogus"})
    # radial / icons
    sl = [{"l": "Copy", "a": {"type": "combo", "val": ["CTRL", "c"]}}, {}, {"l": "Mute", "a": {"type": "media", "val": "MUTE"}}]
    run({"cmd": "radial", "slots": sl})
    for bad in ([{"l": "", "a": {"type": "combo", "val": ["a"]}}], [{"l": "x", "a": {"type": "combo", "val": ["NOPE"]}}], [{"l": "toolongname", "a": {"type": "combo", "val": ["a"]}}], [{}] * 9):
        run({"cmd": "radial", "slots": bad})
    run({"cmd": "radial"})
    bits = "ff" * 72
    run({"cmd": "icon", "op": "set", "target": "key", "layer": 0, "key": 3, "bits": bits})
    run({"cmd": "icon", "op": "set", "target": "radial", "slot": 4, "bits": bits})
    for bad in ({"bits": "ff"}, {"bits": "zz" * 72}, {"key": 6}, {"layer": 3}, {"target": "x"}, {"op": "bogus"}):
        run(dict({"cmd": "icon", "op": "set", "target": "key", "layer": 0, "key": 1, "bits": bits}, **bad))
    run({"cmd": "icon", "op": "clear", "target": "key", "layer": 0, "key": 3})
    run({"cmd": "icon"})
    # totp (accounts, RFC vectors, validation) and the PIN
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    run({"cmd": "totp", "op": "set", "items": [{"n": "GitHub", "s": secret, "d": 8}, {"n": "Mail", "s": secret}]})
    for t in (59, 1111111109, 1111111111, 1234567890, 2000000000, 20000000000):
        run({"cmd": "totp", "op": "code", "i": 0, "t": t})
    run({"cmd": "totp", "op": "code", "i": 1, "t": 59})
    run({"cmd": "totp", "op": "code", "i": 5})
    for bad in ([{"n": "x", "s": "not base32!"}], [{"n": "x", "s": "ABCD"}], [{"n": "", "s": secret}], [{"n": "x", "s": secret, "d": 9}], [{"n": "x", "s": secret, "p": 5}], [{"n": "x" * 11, "s": secret}]):
        run({"cmd": "totp", "op": "set", "items": bad})
    run({"cmd": "totp"})
    for pin in ("12", "123456789", "12a4", ""):
        run({"cmd": "pin", "op": "set", "pin": pin})
    run({"cmd": "pin", "op": "set", "pin": "1234"})
    run({"cmd": "pin", "op": "set", "pin": "9999", "old": "0000"})
    run({"cmd": "pin", "op": "lock"})
    run({"cmd": "totp", "op": "code", "i": 0, "t": 59})
    run({"cmd": "totp", "op": "clear"})
    run({"cmd": "pin", "op": "config", "pin": "1234", "all": True})
    run({"cmd": "pin"})
    run({"cmd": "pin", "op": "clear", "pin": "0000"})
    nat.close()
    nat = fresh(binary, "diff2")            # a new pad: the other PIN cases without the locked state
    sim = SimPipe()
    run = lambda req, **kw: run_case(sim, nat, req, **kw)    # noqa: E731
    run({"cmd": "time", "epoch": seed_t, "tz": 0})
    run({"cmd": "pin", "op": "lock"})
    run({"cmd": "pin", "op": "set", "pin": "1234"})
    run({"cmd": "pin", "op": "config", "pin": "x", "all": True})
    run({"cmd": "pin", "op": "config", "pin": "1234", "all": True, "idle": 60})
    run({"cmd": "pin", "op": "config", "pin": "1234", "idle": 4000})
    run({"cmd": "pin", "op": "bogus"})
    run({"cmd": "pin", "op": "clear", "pin": "1234"})
    run({"cmd": "pin", "op": "clear", "pin": "1234"})
    # calendar, world clock, location, sketch
    run({"cmd": "cal", "months": [{"y": 2027, "m": 1, "days": [3, 15, 3]}]})
    for bad in ([{"y": 2027, "m": 13, "days": [1]}], [{"y": 2027, "m": 2, "days": [29]}], [{"y": 1999, "m": 1, "days": []}], [{"y": 2027, "m": 1, "days": [1]}] * 4):
        run({"cmd": "cal", "months": bad})
    run({"cmd": "worldclock", "zones": [{"n": "NYC", "o": -300}, {"n": "TYO", "o": 540}]})
    for bad in ([{"n": "", "o": 0}], [{"n": "LONGNAME", "o": 0}], [{"n": "X", "o": 9999}], [{"n": "X", "o": 0}] * 5):
        run({"cmd": "worldclock", "zones": bad})
    for lat, lon, t in ((47.37, 8.54, (2027, 6, 21)), (51.51, -0.13, (2027, 12, 21)), (-33.87, 151.21, (2027, 3, 20)), (64.15, -21.94, (2027, 5, 1)), (78.2, 15.6, (2027, 6, 21))):
        import calendar
        ep = calendar.timegm((t[0], t[1], t[2], 12, 0, 0))
        run({"cmd": "time", "epoch": ep, "tz": 0})
        run({"cmd": "geo", "lat": lat, "lon": lon}, tol={"rise": 1, "set": 1, "moon_age": 0.01})
    run({"cmd": "geo", "lat": 95, "lon": 0})
    run({"cmd": "geo", "lat": 1})
    run({"cmd": "sketch", "op": "set", "bits": "ff" * 200, "save": True})
    run({"cmd": "sketch"})
    run({"cmd": "sketch", "op": "set", "bits": "zz"})
    run({"cmd": "sketch", "op": "clear"})
    run({"cmd": "app"}, only=("evt", "name", "locked"))
    # word clock and text folding: the whole day / many strings
    bad = 0
    for h in range(24):
        for m in range(0, 60):
            if bad < 3:
                rs, rn = sim.request({"cmd": "clockwords", "h": h, "m": m}), nat.request({"cmd": "clockwords", "h": h, "m": m})
                if rs["text"] != rn["text"]:
                    bad += 1
                    FAILS.append(("clockwords", h, m))
                    print("  MISMATCH clockwords", h, m, rs["text"], rn["text"])
    run({"cmd": "clockwords", "h": 24, "m": 0})
    run({"cmd": "clockwords", "h": 1})
    for t in ("Müller", "Ærø", "straße", "Привет", "ΑΘΗΝΑ", "€5", "café – ok", "‘hi’", "\U0001F600", "Łódź", "жук", "°C", "…",
              "×÷", "İstanbul", "Ş", "plain"):
        run({"cmd": "fold", "text": t})
    # fold_text on: the text commands accept and fold
    run({"cmd": "settings", "fold_text": True}, only=("fold_text",))
    run({"cmd": "toast", "text": "Müller", "kind": "ok", "secs": 2})
    run({"cmd": "ctx", "text": "Привет"})
    run({"cmd": "layer_names", "names": ["Ärger", "", "Старт"]})
    run({"cmd": "toast", "text": "Ü" * 13})
    run({"cmd": "settings", "fold_text": False}, only=("fold_text",))
    run({"cmd": "toast", "text": "Müller", "kind": "ok", "secs": 2})
    # LED events (definitions and the queue rules)
    evs = [{"n": "build_fail", "p": 3, "rep": 2, "steps": [{"c": "ff0000", "ms": 200}, {"c": "000000", "ms": 200}]}, {"n": "meeting", "p": 2, "rep": 1, "steps": [{"c": "0000ff", "ms": 3000}]},
           {"n": "info", "p": 1, "rep": 1, "steps": [{"c": "00ff00", "ms": 300}]}]
    run({"cmd": "led_events", "events": evs})
    run({"cmd": "led_event", "name": "nope"})
    run({"cmd": "led_event", "name": "info"}, ignore=("played",))
    run({"cmd": "led_event", "name": "meeting"}, ignore=("played",))
    run({"cmd": "led_event", "name": "info"}, ignore=("played",))
    run({"cmd": "led_event", "name": "info"}, ignore=("played",))
    run({"cmd": "led_event", "name": "build_fail"}, ignore=("played",))
    run({"cmd": "led_event", "cancel": True}, ignore=("played",))
    for bad in ([{"n": "", "p": 1, "steps": [{"c": "ff0000", "ms": 100}]}], [{"n": "x", "p": 0, "steps": [{"c": "ff0000", "ms": 100}]}], [{"n": "x", "p": 1, "steps": []}], [{"n": "x", "p": 1, "steps": [{"c": "zz0000", "ms": 100}]}],
                [{"n": "x", "p": 1, "steps": [{"c": "ff0000", "ms": 10}]}], [{"n": "x", "p": 1, "rep": 99, "steps": [{"c": "ff0000", "ms": 100}]}], [{"n": "x", "p": 1, "steps": [{"c": "ff0000", "ms": 100}] * 7}], [evs[0]] * 9):
        run({"cmd": "led_events", "events": bad})
    run({"cmd": "led_events"})
    # pet, crumbs, lifetime, USB modes, drive, stream
    run({"cmd": "pet_event", "kind": "x"})
    run({"cmd": "pet_event", "kind": "cheer"}, only=("cheer", "sad", "ok"))
    run({"cmd": "lifetime", "op": "reset"}, only=("presses", "chatter", "ok", "dial_cw", "dial_ccw", "dial_click"))
    run({"cmd": "lifetime", "op": "bogus"})
    run({"cmd": "usbmode"}, only=("midi", "gamepad", "drive", "active", "reboot_needed", "midi_on", "pad_on", "available", "ok"))
    run({"cmd": "usbmode", "midi_on": True})
    run({"cmd": "usbmode", "midi": True, "gamepad": True}, only=("midi", "gamepad", "drive", "active", "reboot_needed", "ok"))
    for bad in ({"channel": 0}, {"channel": 17}, {"base": 121}, {"cc": 116}, {"velocity": 0}, {"velocity": 128}, {"midi": "yes"}, {"axis_step": 0}, {"axis_step": 65}):
        run(dict({"cmd": "usbmode"}, **bad))
    run({"cmd": "usbmode", "midi": False, "gamepad": False}, only=("midi", "gamepad", "reboot_needed", "ok"))
    run({"cmd": "usbdrive"})
    run({"cmd": "usbdrive", "op": "refresh"})
    run({"cmd": "usbdrive", "op": "image"})
    run({"cmd": "tiles", "t": [[0, 0, "AAAA"]]})
    run({"cmd": "stream", "op": "bogus"})
    run({"cmd": "stream", "op": "start"}, ignore=("heap", "ms"))
    tile = base64.b64encode(b"\x00\x1f\xf8").decode()
    rle = base64.b64encode(b"\x02" + b"".join(bytes([n]) + c for n, c in ((100, b"\x1f\x00"), (100, b"\xe0\xff"), (56, b"\x1f\xf8")))).decode()
    raw = base64.b64encode(b"\x01" + bytes(range(256)) * 2).decode()
    run({"cmd": "tiles", "t": [[0, 0, tile], [14, 14, tile]], "end": True})
    run({"cmd": "tiles", "t": [[3, 2, raw]]})
    run({"cmd": "tiles", "t": [[5, 5, rle]], "end": True})
    for t in ([[15, 0, tile]], [[0, -1, tile]], [[0, 0, "!!!!"]], [[0, 0, base64.b64encode(b"\x01\x00\x00").decode()]], [[0, 0, base64.b64encode(b"\x07\x00\x00").decode()]], [[0, 0, "AAAA"]],
              [{"x": 1}], [[0, 0]], [[0, 0, tile]] * 13, [], [[7, 7, tile], [99, 0, tile]]):
        run({"cmd": "tiles", "t": t})
    run({"cmd": "stream"}, ignore=("heap", "ms", "bytes"))
    run({"cmd": "stream", "op": "stop"}, ignore=("heap", "ms"))
    run({"cmd": "tiles", "t": [[0, 0, tile]]})
    nat.close()
    print("DIFF TEST PASSED" if not FAILS else f"DIFF TEST: {len(FAILS)} mismatch(es)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
