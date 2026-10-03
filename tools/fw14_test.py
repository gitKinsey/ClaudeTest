"""Headless test of the firmware-1.4 features in the app (simulated pad): screens card, reminders, habits, dial press+turn, alternating /
random keys, the 12-screen twin, spec rules; and a 1.3.0 pad for compatibility.   xvfb-run -a python3 tools/fw14_test.py"""
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dcfw14_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import padextras as px, scheduler, bridge   # noqa: E402

# ---------------------------------------------------------------- pure rules (no GUI)
ok = lambda t, v: m.spec_ok({"type": t, "val": v})   # noqa: E731
A, B = {"type": "host", "val": {"op": "notify", "arg": "A"}}, {"type": "media", "val": "MUTE"}
assert ok("toggle", [A, B]) and ok("random", [A, B, A, B, A, B]) and ok("panic", None)
assert not ok("toggle", [A]) and not ok("toggle", [A, B, A]) and not ok("random", [A] * 7) and not ok("random", [A]) and not ok("toggle", "x")
assert not ok("toggle", [{"type": "toggle", "val": [A, B]}, B]) and not ok("random", [A, {"type": "banana"}]) and not ok("toggle", [A, {"type": "text"}])
assert ok("mouse", {"wheel": 2, "mods": ["CTRL"]}) and ok("mouse", {"wheel": -2, "mods": ["ctrl", "shift"], "h": True}) and ok("mouse", {"wheel": 1})
assert not ok("mouse", {"wheel": 2, "mods": ["BANANA"]}) and not ok("mouse", {"wheel": 2, "mods": "CTRL"}) and not ok("mouse", {"wheel": 2, "h": "yes"})
assert ok("macro", [{"host": {"op": "notify", "arg": "a"}}, {"panic": True}])
d = m.describe_spec
assert d(("panic", None)).startswith("panic") and d(("toggle", [A, B])) == "alternate: notify: A  /  media key mute"
assert d(("mouse", {"wheel": 2, "mods": ["CTRL"]})) == "scroll up 2 with Ctrl" and d(("mouse", {"wheel": 3, "h": True})) == "scroll left 3" and d(("random", [A, B])).startswith("random one of 2")
assert len(m.MODE_CHOICES) == 20 and m.NUM_MODES == 20 and m.FW_BUNDLED == "1.5.0" and "ledfx" in m.NEW14_CAPS
for act in ("Panic: stop all macros",):
    assert ("Layers & Pad", act) in m.ACTION_INDEX
assert ("Mouse", "Zoom In (Ctrl+Scroll)") in m.ACTION_INDEX and m.ACTION_INDEX[("Mouse", "Zoom In (Ctrl+Scroll)")][0][1]["mods"] == ["CTRL"]
assert scheduler.validate({"when": {"kind": "every", "minutes": 5}, "do": {"kind": "led", "mode": "breathe"}})["do"] == {"kind": "led", "mode": "breathe"}
assert bridge.validate("/v1/led", {"mode": "fire"}) == {"kind": "led", "mode": "fire"}
cfg = m.normalize_config({"gestures": {"0:8:press": {"cat": "a", "action": "b"}, "1:9:press": {"cat": "a", "action": "b"}, "0:3:press": {"cat": "a", "action": "b"},
                                       "0:1:hold": {"cat": "a", "action": "b"}, "2:5:double": {"cat": "a", "action": "b"}}})
assert set(cfg["gestures"]) == {"0:8:press", "1:9:press", "0:1:hold", "2:5:double"}, cfg["gestures"]
assert px.gesture_text(8, "press") == "Dial pressed + turned right" and px.gesture_text(2, "hold") == "K2  Hold the key"

# ---------------------------------------------------------------- the app
def run(env):
    for k in ("DESK_COMPANION_SIM_V12", "DESK_COMPANION_SIM_V13", "DESK_COMPANION_SIM_V14"):
        os.environ.pop(k, None)
    if env:
        os.environ[env] = "1"
    app = m.App()
    app.update()

    def pump(n=20, cond=None):
        for _ in range(n * (3 if cond else 1)):
            app.update()
            time.sleep(0.05)
            if cond and cond():
                return True
        return cond is None
    app.toggle_simulate()
    assert pump(80, lambda: app.dev.connected)
    return app, app.dev.ser.sim, pump


app, sim, pump = run("DESK_COMPANION_SIM_V14")
notes = []
app.notify = lambda t, msg, kind="ok": notes.append((t, msg))
assert app.dev.info["fw"] == "1.4.0" and all(c in app.dev.info["caps"] for c in m.NEW14_CAPS) and app.dev.info["modes"] == 12
sc = app.screens_card
assert pump(40, lambda: all(str(w.cget("state")) == "normal" for w in sc.widgets))
# ---- screen mask
assert [v.get() for v in sc.mask_vars] == [True] * 6 + [False] * 14
sc.mask_vars[6].set(True); sc.mask_vars[10].set(True); sc._mask_changed()
assert pump(60, lambda: sim.settings["mode_mask"] == 0x3F | (1 << 6) | (1 << 10)) and app.pad.mode_mask == 0x3F | (1 << 6) | (1 << 10)
for v in sc.mask_vars:
    v.set(False)
sc._mask_changed()
assert sc.mask_vars[0].get() is True and "At least one screen" in app.status.cget("text"), "the last screen cannot be switched off"
# ---- the twin follows the 12 screens
app.pad.mode_mask = 0x3F | (1 << 6) | (1 << 10)
assert app.pad.next_mode(6, 1) == 7 and app.pad.next_mode(7, 1) == 11 and app.pad.next_mode(11, 1) == 1 and app.pad.next_mode(1, -1) == 11
app.pad.set_mode(10); assert app.pad.mode == 10
img = app.pad.render(); assert img.size == (m.DISP, m.DISP) and len(set(img.resize((24, 24)).tobytes())) > 3
app._mode_changed("11 Snake")
assert pump(60, lambda: sim.mode == 11) and app.pad.mode == 11
app.vp_mode_var.set("12 Habits")
# ---- reminders
sc.rem[0][0].insert(0, "20"); sc.rem[0][1].insert(0, "Look away")
sc.rem[1][0].insert(0, "45"); sc.rem[1][1].insert(0, "Stand up")
sc.save_reminders()
assert pump(60, lambda: sim.reminders[1] == {"m": 45, "t": "Stand up"}) and sim.reminders[0] == {"m": 20, "t": "Look away"}
sc.rem[2][0].insert(0, "x"); sc.rem[2][1].insert(0, "bad")
sc.save_reminders(); assert "minutes must be a number" in app.status.cget("text")
sc.rem[2][0].delete(0, "end"); sc.rem[2][0].insert(0, "5"); sc.rem[2][1].delete(0, "end"); sc.rem[2][1].insert(0, "a;b")
sc.save_reminders(); assert "plain characters" in app.status.cget("text")
sc.rem[2][0].delete(0, "end"); sc.rem[2][1].delete(0, "end")
sc.test_reminder(0)
assert pump(60, lambda: ("Reminder", "Look away") in notes), "the pad's reminder event becomes a notification on the computer"
# ---- habits
sc.hab[0].delete(0, "end"); sc.hab[0].insert(0, "Hydrate"); sc.save_habits()
assert pump(60, lambda: sim.habits["names"][0] == "Hydrate") and "Hydrate" in sc.habit_lbl.cget("text")
sc.hab[1].delete(0, "end"); sc.hab[1].insert(0, "x" * 11); sc.save_habits(); assert "at most 10" in app.status.cget("text")
sim.reminders[2] = {"m": 9, "t": "Tea"}; sim.settings["mode_mask"] = 0xFFF
sc.on_connected()
assert pump(60, lambda: sc.rem[2][1].get() == "Tea" and all(v.get() for v in sc.mask_vars[:12])), "controls follow the pad"

# ---- dial pressed + turned (gestures panel) and alternating / random keys
g = app.automation.gestures
g.layer.set("Layer 1"); g.slot.set("Dial pressed + turned right")
g.cat.set("Media"); g._cat_changed("Media"); g.action.set("Next Track"); g.assign()
assert app.cfg["gestures"] == {"0:8:press": {"cat": "Media", "action": "Next Track"}}
assert pump(60, lambda: 8 in sim.layers[0]) and sim.layers[0][8] == {"type": "media", "val": "NEXT"}
g.layer.set("Layer 2"); g.slot.set("Dial pressed + turned left"); g.action.set("Previous Track"); g.assign()
assert pump(60, lambda: 9 in sim.layers[1])
r = app.dev.request({"cmd": "getkeys", "layer": 0}); assert r["pt"] == [True, False]
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text")) and 8 in sim.layers[0] and 9 in sim.layers[1], app.status.cget("text")
sim.layers[2][8] = {"type": "media", "val": "MUTE"}                   # stale one only the pad has: the next upload removes it
app.upload_all()
assert pump(100, lambda: 8 not in sim.layers[2] and 8 in sim.layers[0])
g.remove("0:8:press")
assert pump(60, lambda: 8 not in sim.layers[0]) and "0:8:press" not in app.cfg["gestures"]
# press + turn through the (simulated) protocol
sim.layers[0][8] = {"type": "host", "val": {"op": "notify", "arg": "pr"}}
sim.layers[0][6] = {"type": "host", "val": {"op": "notify", "arg": "cw"}}
sim.host_log.clear()
app.dev.request({"cmd": "input", "turn": 1, "press": True}); app.dev.request({"cmd": "input", "turn": 1})
assert [h["arg"] for h in sim.host_log] == ["pr", "cw"], sim.host_log

ch = app.automation.choices
ch.kind.set("Alternate between two actions")
ch.cat.set("Media"); ch._cat_changed("Media"); ch.action.set("Mute"); ch.add()
ch.action.set("Play/Pause"); ch.add()
ch.action.set("Stop"); ch.add(); assert len(ch.picks) == 2 and "At most 2" in app.status.cget("text")
slot = int(app._target_slot()); label = "Alternate: Mute / Play/Pause"
ch.assign()
assert app.cfg["custom"][label]["type"] == "toggle" and len(app.cfg["custom"][label]["val"]) == 2 and app.cfg["map"][str(slot)]["action"] == label
assert d(m.resolve_spec(app.cfg, "Custom", label)).startswith("alternate: media key mute")
app.upload_slots([slot])
assert pump(60, lambda: sim.slots.get(slot, {}).get("type") == "toggle") or pump(10, lambda: sim.layers[0].get(slot, {}).get("type") == "toggle")
ch.picks.clear(); ch.kind.set("Pick one at random"); ch._kind_changed()
ch.cat.set("Layers & Pad"); ch._cat_changed("Layers & Pad"); ch.action.set("Layer 1"); ch.add(); ch.action.set("Layer 2"); ch.add()
ch.action.set("Panic: stop all macros"); ch.add()
ch.assign(); rnd = [k for k in app.cfg["custom"] if k.startswith("Random: Layer 1 / Layer 2 / Panic")]
assert rnd and len(rnd[0]) <= 48 and app.cfg["custom"][rnd[0]]["type"] == "random" and len(app.cfg["custom"][rnd[0]]["val"]) == 3, list(app.cfg["custom"])
ch.picks.clear(); ch.cat.set("Media"); ch._cat_changed("Media"); ch.action.set("Mute"); ch.add(); ch.assign()
assert "needs exactly 2" in app.status.cget("text") or "2 to 6" in app.status.cget("text")
app.cfg["custom"]["T"] = {"type": "toggle", "val": [A, B]}; app.refresh_library()
ch.cat.set("Custom"); ch._cat_changed("Custom"); ch.action.set("T"); ch.picks.clear(); ch.add()
assert not ch.picks and "cannot be part" in app.status.cget("text"), "no nesting"
app.destroy()

# ---------------------------------------------------------------- a firmware 1.3.0 pad: nothing new is sent, everything says why
app, sim, pump = run("DESK_COMPANION_SIM_V13")
assert app.dev.info["fw"] == "1.3.0" and not any(c in app.dev.info["caps"] for c in m.NEW14_CAPS) and any(c in app.dev.info["caps"] for c in m.NEW13_CAPS)
sc = app.screens_card
assert pump(40, lambda: "older than 1.4" in sc.note.cget("text")) and all(str(w.cget("state")) == "disabled" for w in sc.widgets)
before = dict(sim.cmd_count)
sc._mask_changed(); sc.save_reminders(); sc.save_habits(); time.sleep(0.3)
assert sim.cmd_count == before, "nothing is sent to a pad without the capability"
g = app.automation.gestures
g.layer.set("Layer 1"); g.slot.set("Dial pressed + turned right"); g.cat.set("Media"); g._cat_changed("Media"); g.action.set("Mute"); g.assign()
assert app.cfg["gestures"] and 8 not in sim.layers[0]
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text")) and 8 not in sim.layers[0], "press+turn stays in the app's settings, the old pad gets nothing"
ch = app.automation.choices
ch.kind.set("Alternate between two actions"); ch.cat.set("Media"); ch._cat_changed("Media"); ch.action.set("Mute"); ch.add(); ch.action.set("Stop"); ch.add(); ch.assign()
assert "need firmware 1.4" in app.status.cget("text")
r = app.dev.request({"cmd": "mode", "val": 9}); assert sim.mode != 9, "a 1.3 pad ignores screens 7-12"
try:
    app.dev.request({"cmd": "remap", "key": 8, "type": "media", "val": "MUTE"}); raise SystemExit("a 1.3 pad accepted slot 8")
except m.DeviceError:
    pass
app.destroy()
print("ALL FW14 APP TESTS PASSED")
