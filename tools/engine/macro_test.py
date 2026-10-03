"""Engine test: the macro builder - 4-modifier combos, text snippets, delay sequences, validation, upload and read-back."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

k = Kit("dcmacro_")
import core.base as m    # noqa: E402
e, sim, pump = k.e, k.sim, k.pump

need = {"Editing": 10, "Media": 8, "OS Controls": 12, "Browser": 10, "Productivity & Dev": 10}
for cat, n in need.items():
    assert len(m.ACTIONS[cat]) >= n, (cat, len(m.ACTIONS[cat]))
assert sum(len(v) for c, v in m.ACTIONS.items() if c in need) >= 50
for want in ("Copy", "Cut", "Paste", "Undo", "Redo", "Select All", "Save", "Duplicate", "Find", "Delete"):
    assert want in [a[0] for a in m.ACTIONS["Editing"]], want


class Rec:
    def __init__(self):
        self.calls = []

    def run(self, spec, stop=None):
        self.calls.append(spec)


# a virtual key press acts at once (the delay field once shared a variable with the test delay: every press waited 200 seconds)
import time    # noqa: E402
rec = Rec()
e.host = m.HostInput(backend=rec)
e.cfg["live_test"] = True
assert e.cfg.get("test_delay", 0) == 0 and e.live_test
for slot, want in ((5, ("media", "MUTE")), (4, ("media", "PLAY_PAUSE"))):
    t0, n = time.time(), len(rec.calls)
    e.exec_slot(slot)
    assert pump(40, lambda: len(rec.calls) > n), f"slot {slot} did not fire"
    assert time.time() - t0 < 2.0 and tuple(rec.calls[-1]) == want, (slot, rec.calls[-1], time.time() - t0)
e.cfg["live_test"] = False

# Ctrl + Shift + Alt + Gui + T on K1
e.set_target_slot(1)
keys = e.combo_keys(["CTRL", "SHIFT", "ALT", "GUI"], "t")
e.assign_spec(("combo", keys), m.describe_spec(("combo", keys)))
e.upload_slots([1])
assert pump(60, lambda: sim.slots.get(1) == {"type": "combo", "val": ["CTRL", "SHIFT", "ALT", "GUI", "t"]}), sim.slots.get(1)

assert e.combo_keys(["PRIMARY", "PRIMARY", "-", "-"], "enter") == ["PRIMARY", "ENTER"]
before = dict(e.cfg["map"])
e.guard(lambda: e.combo_keys(["CTRL"], "not-a-key"))
assert e.cfg["map"] == before and "not a valid key" in k.status, k.status

# text snippet with Enter on K2
e.set_target_slot(2)
t = e.text_value("name@example.com", True)
e.assign_spec(("text", t), "Text: " + t.strip())
e.upload_slots([2])
assert pump(60, lambda: sim.slots.get(2) == {"type": "text", "val": "name@example.com\n"}), sim.slots.get(2)
e.guard(lambda: e.text_value("café"))
assert "ASCII" in k.status
e.guard(lambda: e.text_value(""))
assert "Enter some text" in k.status

# sequence on K3
e.seq_clear()
e.seq_add({"combo": e.combo_keys(["CTRL"], "l")})
e.seq_add_delay("350")
e.seq_add({"text": e.text_value("hello")})
e.seq_add({"media": "PLAY_PAUSE"})
e.guard(lambda: e.seq_add_delay("999999"))
assert "between 0 and 60000" in k.status
e.guard(lambda: e.seq_add_delay("abc"))
assert "whole number" in k.status
assert len(e.macro_steps) == 4 and e.seq_lines()[1] == "DELAY   350 ms", e.seq_lines()
assert e.seq_move(0, 1) == 1 and e.macro_steps[1] == {"combo": ["CTRL", "l"]}
assert e.seq_move(1, -1) == 0
e.seq_remove([3]); e.seq_add({"media": "PLAY_PAUSE"})
e.set_target_slot(3)
e.assign_spec(e.seq_spec(), "Demo macro")
e.upload_slots([3])
want = [{"combo": ["CTRL", "l"]}, {"delay": 350}, {"text": "hello"}, {"media": "PLAY_PAUSE"}]
assert pump(60, lambda: sim.slots.get(3) == {"type": "macro", "val": want}), sim.slots.get(3)
e.save_seq("Demo macro 2")
assert "Demo macro 2" in e.cfg["custom"]
saved = json.load(open(os.environ["DESK_COMPANION_CONFIG"]))
assert saved["custom"]["Demo macro 2"]["type"] == "macro"
for _ in range(65):
    e.seq_add({"delay": 1}) if len(e.macro_steps) < 64 else None
e.guard(lambda: e.seq_add({"delay": 1}))
assert "64 steps" in k.status and len(e.macro_steps) == 64

# computer / mouse / layer actions
def act(kind, arg="", tf=""):
    return e.action_spec(kind, arg, tf)
assert act("Open website", "example.org/x") == ("host", {"op": "url", "arg": "https://example.org/x"})
assert act("Open website", "http://a.b") == ("host", {"op": "url", "arg": "http://a.b"})
assert act("Start program", "calc") == ("host", {"op": "app", "arg": "calc"})
assert act("Type the clipboard") == ("host", {"op": "clipboard"})
assert act("Mouse click", "right") == ("mouse", {"btn": "right", "act": "click"})
assert act("Mouse double click") == ("mouse", {"btn": "left", "act": "double"})
assert act("Mouse scroll", "-3") == ("mouse", {"wheel": -3})
assert act("Switch layer", "2") == ("layer", 1) and act("Switch layer", "") == ("layer", "next")
assert act("Type a snippet", "Hi {date}") == ("host", {"op": "snippet", "arg": "Hi {date}"})
from desk_lib import textops    # noqa: E402
assert act("Transform the clipboard", "", textops.TRANSFORMS["snake"][0]) == ("host", {"op": "clip", "arg": "snake"})
for kind, arg, msg in (("Open website", "", "https://example.com"), ("Mouse click", "sideways", "button must be"), ("Mouse scroll", "0", "not 0"), ("Mouse scroll", "x", "must be a number"),
                       ("Switch layer", "9", "layer must be"), ("Run shell command", "ls", "switched off")):
    try:
        act(kind, arg)
        raise SystemExit(f"{kind} {arg!r} accepted")
    except ValueError as ex:
        assert msg in str(ex), (kind, str(ex))
assert e.action_step(act("Switch layer", "1")) == {"layer": 0}

e.verify_pad_keys()
assert pump(80, lambda: "Key check OK" in k.status or "differ" in k.status.lower() or "mismatch" in k.status.lower()), k.status
print("status:", k.status)
k.close()
print("ALL ENGINE MACRO TESTS PASSED")
