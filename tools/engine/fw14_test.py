"""Engine test of the firmware-1.4 features (simulated pad): screens, reminders, habits, dial press+turn, alternating / random keys, the twin, spec rules; and a 1.3.0 pad."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit, isolate    # noqa: E402

isolate("dcfw14_")
import core.base as m   # noqa: E402
from desk_lib import padconst as px, scheduler, bridge   # noqa: E402

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

# ---------------------------------------------------------------- the engine
def run(env):
    for k in ("DESK_COMPANION_SIM_V12", "DESK_COMPANION_SIM_V13", "DESK_COMPANION_SIM_V14"):
        os.environ.pop(k, None)
    if env:
        os.environ[env] = "1"
    return Kit("dcfw14b_", fresh=False)


k = run("DESK_COMPANION_SIM_V14")
app, sim, pump = k.e, k.sim, k.pump
status = lambda: k.status    # noqa: E731
notes = []
app.notify = lambda t, msg, kind="ok": notes.append((t, msg))
loaded = []
app.on("screens_loaded", lambda st, rm, hb: loaded.append((st, rm, hb)))
habit_texts = []
app.on("habits", lambda t: habit_texts.append(t))
assert app.dev.info["fw"] == "1.4.0" and all(c in app.dev.info["caps"] for c in m.NEW14_CAPS) and app.dev.info["modes"] == 12
assert app.screens_ok() and app.screens_note() == ""
# ---- screen mask
app.screens_on_connected()
assert pump(40, lambda: loaded), "the screens are read from the pad"
assert [bool((loaded[-1][0]["mode_mask"] >> i) & 1) for i in range(20)] == [True] * 6 + [False] * 14
want = 0x3F | (1 << 6) | (1 << 10)
assert app.set_mode_mask(want) == want
assert pump(60, lambda: sim.settings["mode_mask"] == want) and app.pad.mode_mask == want
assert app.set_mode_mask(0) == 1 and "At least one screen" in status(), "the last screen cannot be switched off"
app.pad.mode_mask = want
assert app.pad.next_mode(6, 1) == 7 and app.pad.next_mode(7, 1) == 11 and app.pad.next_mode(11, 1) == 1 and app.pad.next_mode(1, -1) == 11
app.pad.set_mode(10); assert app.pad.mode == 10
img = app.pad.render(); assert img.size == (m.DISP, m.DISP) and len(set(img.resize((24, 24)).tobytes())) > 3
app.set_mode(11)
assert pump(60, lambda: sim.mode == 11) and app.pad.mode == 11
# ---- reminders
app.save_reminders([("20", "Look away"), ("45", "Stand up"), ("", "")])
assert pump(60, lambda: sim.reminders[1] == {"m": 45, "t": "Stand up"}) and sim.reminders[0] == {"m": 20, "t": "Look away"}
app.save_reminders([("20", "Look away"), ("45", "Stand up"), ("x", "bad")]); assert "minutes must be a number" in status()
app.save_reminders([("20", "Look away"), ("45", "Stand up"), ("5", "a;b")]); assert "plain characters" in status()
app.test_reminder(0)
assert pump(60, lambda: ("Reminder", "Look away") in notes), "the pad's reminder event becomes a notification on the computer"
# ---- habits
app.save_habits(["Hydrate", "Move", "Read", "Sleep", "Focus"])
assert pump(60, lambda: sim.habits["names"][0] == "Hydrate") and pump(20, lambda: habit_texts and "Hydrate" in habit_texts[-1])
app.save_habits(["x" * 11, "Move", "Read", "Sleep", "Focus"]); assert "at most 10" in status()
sim.reminders[2] = {"m": 9, "t": "Tea"}; sim.settings["mode_mask"] = 0xFFF
loaded.clear(); app.screens_on_connected()
assert pump(60, lambda: loaded and loaded[-1][1]["list"][2]["t"] == "Tea" and loaded[-1][0]["mode_mask"] == 0xFFF), "controls follow the pad"

# ---- dial pressed + turned and alternating / random keys
app.gesture_assign(0, "Dial pressed + turned right", "", "Media", "Next Track")
assert app.cfg["gestures"] == {"0:8:press": {"cat": "Media", "action": "Next Track"}}
assert pump(60, lambda: 8 in sim.layers[0]) and sim.layers[0][8] == {"type": "media", "val": "NEXT"}
app.gesture_assign(1, "Dial pressed + turned left", "", "Media", "Previous Track")
assert pump(60, lambda: 9 in sim.layers[1])
r = app.dev.request({"cmd": "getkeys", "layer": 0}); assert r["pt"] == [True, False]
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in status()) and 8 in sim.layers[0] and 9 in sim.layers[1], status()
sim.layers[2][8] = {"type": "media", "val": "MUTE"}
app.upload_all()
assert pump(100, lambda: 8 not in sim.layers[2] and 8 in sim.layers[0])
app.gesture_remove("0:8:press")
assert pump(60, lambda: 8 not in sim.layers[0]) and "0:8:press" not in app.cfg["gestures"]
sim.layers[0][8] = {"type": "host", "val": {"op": "notify", "arg": "pr"}}
sim.layers[0][6] = {"type": "host", "val": {"op": "notify", "arg": "cw"}}
sim.host_log.clear()
app.dev.request({"cmd": "input", "turn": 1, "press": True}); app.dev.request({"cmd": "input", "turn": 1})
assert [h["arg"] for h in sim.host_log] == ["pr", "cw"], sim.host_log

picks = []
picks.append(app.choice_pick(picks, True, "Media", "Mute"))
picks.append(app.choice_pick(picks, True, "Media", "Play/Pause"))
try:
    app.choice_pick(picks, True, "Media", "Stop")
    raise SystemExit("a third alternate action was accepted")
except ValueError as ex:
    assert "At most 2" in str(ex)
slot = app.target_slot
label = "Alternate: Mute / Play/Pause"
app.choice_assign(picks, True)
assert app.cfg["custom"][label]["type"] == "toggle" and len(app.cfg["custom"][label]["val"]) == 2 and app.cfg["map"][str(slot)]["action"] == label
assert d(m.resolve_spec(app.cfg, "Custom", label)).startswith("alternate: media key mute")
app.upload_slots([slot])
assert pump(60, lambda: sim.layers[0].get(slot, {}).get("type") == "toggle")
picks = []
for a in ("Layer 1", "Layer 2", "Panic: stop all macros"):
    cat = "Layers & Pad"
    picks.append(app.choice_pick(picks, False, cat, a))
app.choice_assign(picks, False)
rnd = [kk for kk in app.cfg["custom"] if kk.startswith("Random: Layer 1 / Layer 2 / Panic")]
assert rnd and len(rnd[0]) <= 48 and app.cfg["custom"][rnd[0]]["type"] == "random" and len(app.cfg["custom"][rnd[0]]["val"]) == 3, list(app.cfg["custom"])
try:
    app.choice_assign([picks[0]], False)
    raise SystemExit("a single pick was accepted")
except ValueError as ex:
    assert "2 to 6" in str(ex)
app.cfg["custom"]["T"] = {"type": "toggle", "val": [A, B]}
try:
    app.choice_pick([], True, "Custom", "T")
    raise SystemExit("nesting accepted")
except ValueError as ex:
    assert "cannot be part" in str(ex)
k.close()

# ---------------------------------------------------------------- a firmware 1.3.0 pad: nothing new is sent, everything says why
k = run("DESK_COMPANION_SIM_V13")
app, sim, pump = k.e, k.sim, k.pump
status = lambda: k.status    # noqa: E731
assert app.dev.info["fw"] == "1.3.0" and not any(c in app.dev.info["caps"] for c in m.NEW14_CAPS) and any(c in app.dev.info["caps"] for c in m.NEW13_CAPS)
assert not app.screens_ok() and "older than 1.4" in app.screens_note()
before = dict(sim.cmd_count)
app.set_mode_mask(5); app.save_reminders([("5", "x")] * 3); app.save_habits(["a"] * 5); time.sleep(0.3)
assert sim.cmd_count == before, "nothing is sent to a pad without the capability"
app.gesture_assign(0, "Dial pressed + turned right", "", "Media", "Mute")
assert app.cfg["gestures"] and 8 not in sim.layers[0]
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in status()) and 8 not in sim.layers[0], "press+turn stays in the app's settings, the old pad gets nothing"
pk = [app.choice_pick([], True, "Media", "Mute")]
pk.append(app.choice_pick(pk, True, "Media", "Stop"))
try:
    app.choice_assign(pk, True)
    raise SystemExit("toggle accepted for a 1.3 pad")
except ValueError as ex:
    assert "need firmware 1.4" in str(ex)
r = app.dev.request({"cmd": "mode", "val": 9}); assert sim.mode != 9, "a 1.3 pad ignores screens 7-12"
try:
    app.dev.request({"cmd": "remap", "key": 8, "type": "media", "val": "MUTE"})
    raise SystemExit("a 1.3 pad accepted slot 8")
except m.DeviceError:
    pass
k.close()
print("ALL ENGINE FW14 TESTS PASSED")
