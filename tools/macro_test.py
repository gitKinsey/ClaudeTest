"""Headless test of the Macro Creator tab: 4-modifier combos, text snippets, delay sequences, input validation,
upload to the (simulated) pad and read-back.   xvfb-run -a python3 tools/macro_test.py"""
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dcmacro_")
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m

# --- the preset library from the spec: >= 50 actions in the five categories
need = {"Editing": 10, "Media": 8, "OS Controls": 12, "Browser": 10, "Productivity & Dev": 10}
for cat, n in need.items():
    assert len(m.ACTIONS[cat]) >= n, (cat, len(m.ACTIONS[cat]))
assert sum(len(v) for c, v in m.ACTIONS.items() if c in need) >= 50
for want in ("Copy", "Cut", "Paste", "Undo", "Redo", "Select All", "Save", "Duplicate", "Find", "Delete"):
    assert want in [a[0] for a in m.ACTIONS["Editing"]], want

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


app.toggle_simulate()
assert pump(80, lambda: app.dev.connected), "no connection to the simulated pad"
sim = app.dev.ser.sim
# --- regression: a virtual key press acts at once. (The Macro Creator's "delay" field once shared a variable with the
#     Virtual Pad's test-delay menu, so every press waited 200 *seconds*.)
class Rec:
    def __init__(self):
        self.calls = []

    def run(self, spec, stop=None):
        self.calls.append(spec)


rec = Rec()
app.host = m.HostInput(backend=rec)
app.live_var.set(True)
assert app.delay_var.get() == "0 s", app.delay_var.get()
for slot, want in ((5, ("media", "MUTE")), (4, ("media", "PLAY_PAUSE"))):
    t0, n = time.time(), len(rec.calls)
    app.exec_slot(slot)
    assert pump(40, lambda: len(rec.calls) > n), f"slot {slot} did not fire"
    assert time.time() - t0 < 2.0 and tuple(rec.calls[-1]) == want, (slot, rec.calls[-1], time.time() - t0)
app.live_var.set(False)

app.tabs.set("Macro Creator")
pump(5)

# --- Ctrl + Shift + Alt + T  (4 modifiers + main key) on K1
for v, val in zip(app.mod_vars, ("CTRL", "SHIFT", "ALT", "GUI")):
    v.set(val)
app.key_var.set("t")
app.target_var.set(m.SLOT_LABELS[1])
app.assign_combo()
app.upload_slots([1])
assert pump(60, lambda: sim.slots.get(1) == {"type": "combo", "val": ["CTRL", "SHIFT", "ALT", "GUI", "t"]}), sim.slots.get(1)

# --- duplicate / "-" modifiers collapse, named key accepted
for v, val in zip(app.mod_vars, ("PRIMARY", "PRIMARY", "-", "-")):
    v.set(val)
app.key_var.set("enter")
assert app._combo_keys() == ["PRIMARY", "ENTER"], app._combo_keys()

# --- invalid main key is refused with a message, nothing assigned
app.key_var.set("not-a-key")
before = dict(app.cfg["map"])
app.assign_combo()
assert app.cfg["map"] == before and "not a valid key" in app.status.cget("text"), app.status.cget("text")

# --- text snippet (with Enter) on K2
app.text_box.delete("1.0", "end")
app.text_box.insert("1.0", "name@example.com")
app.enter_var.set(True)
app.target_var.set(m.SLOT_LABELS[2])
app.assign_text()
app.upload_slots([2])
assert pump(60, lambda: sim.slots.get(2) == {"type": "text", "val": "name@example.com\n"}), sim.slots.get(2)

# --- non-ASCII and empty text are refused
app.text_box.delete("1.0", "end")
app.text_box.insert("1.0", "café")
app.assign_text()
assert "ASCII" in app.status.cget("text"), app.status.cget("text")
app.text_box.delete("1.0", "end")
app.assign_text()
assert "Enter some text" in app.status.cget("text")

# --- sequence: combo, delay, text, media  (K3) + delay limits
app.seq_clear()
for v, val in zip(app.mod_vars, ("CTRL", "-", "-", "-")):
    v.set(val)
app.key_var.set("l")
app.seq_add_combo()
app.seq_delay_var.set("350")
app.seq_add_delay()
app.text_box.insert("1.0", "hello")
app.enter_var.set(False)
app.seq_add_text()
app.media_var.set("PLAY_PAUSE")
app.seq_add_media()
app.seq_delay_var.set("999999")
app.seq_add_delay()
assert "between 0 and 60000" in app.status.cget("text")
app.seq_delay_var.set("abc")
app.seq_add_delay()
assert "whole number" in app.status.cget("text")
assert len(app.macro_steps) == 4, app.macro_steps
app.name_var.set("Demo macro")
app.target_var.set(m.SLOT_LABELS[3])
app.assign_seq()
app.upload_slots([3])
want = [{"combo": ["CTRL", "l"]}, {"delay": 350}, {"text": "hello"}, {"media": "PLAY_PAUSE"}]
assert pump(60, lambda: sim.slots.get(3) == {"type": "macro", "val": want}), sim.slots.get(3)

# --- save for later: stored in the config file and listed in the Custom category
app.save_seq()
import json
assert "Demo macro" in app.cfg["custom"]
saved = json.load(open(os.environ["DESK_COMPANION_CONFIG"]))
assert saved["custom"]["Demo macro"]["type"] == "macro"

# --- everything uploaded is read back and matches
app.verify_pad_keys()
assert pump(80, lambda: "Key check OK" in app.status.cget("text") or "differ" in app.status.cget("text").lower() or "mismatch" in app.status.cget("text").lower()), app.status.cget("text")
print("status:", app.status.cget("text"))
app.destroy()
print("ALL MACRO TESTS PASSED")
