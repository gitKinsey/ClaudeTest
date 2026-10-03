"""Headless test of the firmware-1.5 features in the app (simulated pad): look-and-feel settings, triple tap / chords / dial clicks, pad functions (fx),
key labels, boot report, rollback, dim, sound bars, new card kinds; and a 1.4.0 pad for compatibility.   xvfb-run -a python3 tools/fw15_test.py"""
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dcfw15_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import audio, padextras as px   # noqa: E402

# ---------------------------------------------------------------- pure rules
ok = lambda t, v: m.spec_ok({"type": t, "val": v})   # noqa: E731
assert all(ok("fx", n) for n in m.FX_NAMES) and not ok("fx", "nope") and not ok("fx", None) and len(m.FX_NAMES) == 14
assert ok("macro", [{"fx": "dial_lock"}, {"delay": 10}]) and not ok("macro", [{"fx": "nope"}])
for name in ("Lock / unlock the dial", "Next colour theme", "Popup menu of this layer's keys", "Window switcher: next (use on the dial)", "Sticky Ctrl (for the next key)"):
    assert ("Layers & Pad", name) in m.ACTION_INDEX, name
assert m.ACTION_INDEX[("Layers & Pad", "Lock / unlock the dial")][0] == ("fx", "dial_lock")
assert m.short_label("Play/Pause") == "PLAY/PAU" and m.short_label("Volume Up") == "VOL UP" and m.short_label("Copy") == "COPY" and m.short_label("Café ☕ x") == "CAF X"
assert m.short_label("Previous Track") == "PREV TRK" and "|" not in m.short_label("a|b") and m.short_label("") == ""
cfg = m.normalize_config({})
labs = m.labels_for(cfg, 0)
assert labs == ["COPY", "PASTE", "UNDO", "PLAY/PAU", "MUTE", "VOL UP", "VOL DOWN"], labs
assert all(len(x) <= 8 for l in range(3) for x in m.labels_for(cfg, l))
cfg = m.normalize_config({"gestures": {k: {"cat": "a", "action": "b"} for k in ("0:1:triple", "0:10:press", "1:13:press", "2:14:press", "2:15:press", "0:16:press", "0:6:triple", "0:9:press", "0:1:wiggle")}})
assert set(cfg["gestures"]) == {"0:1:triple", "0:10:press", "1:13:press", "2:14:press", "2:15:press", "0:9:press"}, cfg["gestures"]
assert px.gesture_text(10, "press") == "Chord K1 + K2 together (1.5)" and px.gesture_text(14, "press") == "Dial double-click (1.5)" and px.gesture_text(2, "triple").endswith("Triple-tap the key (1.5)")
cfg = m.normalize_config({"accent": "pink"})
res = lambda c, a: ("host", {"op": "notify", "arg": a})   # noqa: E731
cfg["gestures"] = {"0:1:triple": {"cat": "x", "action": "t"}, "0:10:press": {"cat": "x", "action": "c"}, "0:14:press": {"cat": "x", "action": "d"}, "0:8:press": {"cat": "x", "action": "p"}}
class App:  # noqa: E301,E701
    pass
fake = App(); fake.cfg = cfg
old_caps = {"gestures", "pressturn"}
mm = px.gesture_msgs(fake, set(), res, old_caps)
assert [x["key"] for x in mm] == [8], mm                              # triple / chord / dial click need 1.5: nothing is sent to an older pad
new_caps = old_caps | {"tapdance", "chords", "dialclicks"}
mm = px.gesture_msgs(fake, set(), res, new_caps)
assert sorted((x["key"], x.get("gesture")) for x in mm) == [(1, "triple"), (8, None), (10, None), (14, None)], mm
mm = px.gesture_msgs(fake, {(0, 1, "triple"), (0, 11, "press"), (1, 14, "press")}, res, new_caps)
assert {"cmd": "remap", "key": 11, "clear": True} in mm and {"cmd": "remap", "key": 14, "clear": True, "layer": 1} in mm and {"cmd": "remap", "key": 1, "clear": True, "gesture": "triple"} not in mm
# spectrum bars: log-spaced frequencies, each bar reacts to its own tone
import math   # noqa: E402
tone = lambda f, n=1024: [0.5 * math.sin(2 * math.pi * f * i / 16000) for i in range(n)]   # noqa: E731
low, high = audio.bars(tone(160), 16000), audio.bars(tone(5000), 16000)
assert len(low) == 8 and low[1] > 50 and sum(low[4:]) < 40 and high[6] > 50 and sum(high[:3]) < 40 and audio.bars([0.0] * 512, 16000) == [0] * 8, (low, high)


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


app, sim, pump = run(None)
assert app.dev.info["fw"] == "1.5.0" and all(c in app.dev.info["caps"] for c in m.NEW15_CAPS) and app.dev.info["modes"] == 20
bc = app.behaviour
assert pump(60, lambda: all(str(w.cget("state")) == "normal" for w in bc.w15)), "the 1.5 controls are enabled"

# ---- look and feel: every control reaches the (simulated) pad, and loads back
bc.v["theme"].set("Night red"); bc.apply(theme=px._value(px.THEMES, "Night red"))
assert pump(60, lambda: sim.settings15["theme"] == 2)
bc.apply(rotation=2, tint=True, pixel_shift=True, fade=True, boot_anim=False, detent_led=True, key_toast=True, splash="MY DESK")
assert pump(60, lambda: sim.settings15["splash"] == "MY DESK") and sim.settings15["rotation"] == 2 and sim.settings15["boot_anim"] is False and sim.settings15["key_toast"] is True
bc.rep[0].set(True); bc.rep[3].set(True); bc._repeat_changed()
assert pump(60, lambda: sim.settings15["repeat_mask"] == 9)
bc.apply(clock_style=5, saver_style=7, mode_mask=0xFFFFF)
assert pump(60, lambda: sim.settings["clock_style"] == 5 and sim.settings["saver_style"] == 7 and sim.settings["mode_mask"] == 0xFFFFF)
bc.apply(theme=9)
assert pump(60, lambda: "Pad setting failed" in app.status.cget("text")) and sim.settings15["theme"] == 2, "an out-of-range value is refused by the pad and the app says so"
sim.settings15.update(theme=4, tint=False, splash="X", repeat_mask=2, dial_lock=True)
bc.on_connected()
assert pump(60, lambda: bc.v["theme"].get() == "Amber" and bc.splash.get() == "X" and bc.rep[1].get() and not bc.rep[0].get() and bc.v["dial_lock"].get()), "controls follow the pad"
bc.splash.delete(0, "end"); bc.splash.insert(0, "NEW"); bc.apply(splash=bc.splash.get().strip())
assert pump(60, lambda: sim.settings15["splash"] == "NEW")
sc = app.screens_card
sim.settings["mode_mask"] = 0xFFFFF
sc.on_connected()
assert pump(60, lambda: all(v.get() for v in sc.mask_vars)), "all twenty screens follow the pad"
assert all(str(w.cget("state")) == "normal" for w in sc.new_boxes)
sc.mask_vars[19].set(False); sc._mask_changed()
assert pump(60, lambda: sim.settings["mode_mask"] == 0x7FFFF)
app.pad.mode_mask = 0xFFFFF
assert app.pad.next_mode(19, 1) == 1 or app.pad.next_mode(19, 1) == 20
for mode in (13, 17, 20):                                           # the twin shows a card for screens that run on the pad
    app.pad.set_mode(mode)
    img = app.pad.render()
    assert img.size == (m.DISP, m.DISP) and len(set(img.resize((24, 24)).tobytes())) > 3, mode

# ---- triple tap, chords and dial clicks (gesture panel -> pad -> events)
g = app.automation.gestures
g.layer.set("Layer 1"); g.cat.set("Media"); g._cat_changed("Media")
g.slot.set("K2"); g.gest.set("Triple-tap the key (1.5)"); g.action.set("Mute"); g.assign()
assert app.cfg["gestures"]["0:2:triple"] == {"cat": "Media", "action": "Mute"}
assert pump(60, lambda: (0, 2, "triple") in sim.gest) and sim.gest[(0, 2, "triple")] == {"type": "media", "val": "MUTE"}
g.slot.set("Chord K1 + K2 together (1.5)"); g.action.set("Next Track"); g.assign()
g.slot.set("Dial double-click (1.5)"); g.action.set("Stop"); g.assign()
g.slot.set("Dial triple-click (1.5)"); g.action.set("Previous Track"); g.assign()
assert pump(60, lambda: all(k in sim.layers[0] for k in (10, 14, 15))), sim.layers[0].keys()
r = app.dev.request({"cmd": "getkeys", "layer": 0})
assert r["ch"] == [True, False, False, False] and r["dc"] == [True, True] and r["slots"][1]["t"] is True, r
sim.layers[0][10] = {"type": "host", "val": {"op": "notify", "arg": "chord12"}}
sim.layers[0][14] = {"type": "host", "val": {"op": "notify", "arg": "dbl"}}
sim.layers[0][15] = {"type": "host", "val": {"op": "notify", "arg": "tri"}}
sim.gest[(0, 2, "triple")] = {"type": "host", "val": {"op": "notify", "arg": "t2"}}
sim.host_log.clear()
for msg in ({"cmd": "input", "chord": 1}, {"cmd": "input", "dclick": 2}, {"cmd": "input", "dclick": 3}, {"cmd": "input", "k": 2, "g": "triple"}, {"cmd": "input", "dclick": 1}):
    app.dev.request(msg)
assert [h["arg"] for h in sim.host_log] == ["chord12", "dbl", "tri", "t2"], sim.host_log
for bad in ({"cmd": "input", "chord": 5}, {"cmd": "input", "dclick": 4}, {"cmd": "remap", "key": 16, "type": "media", "val": "MUTE"}, {"cmd": "remap", "key": 8, "gesture": "triple", "type": "media", "val": "MUTE"}):
    try:
        app.dev.request(bad)
        raise SystemExit(f"accepted {bad}")
    except m.DeviceError:
        pass
g.remove("0:14:press")
assert pump(60, lambda: 14 not in sim.layers[0]) and 15 in sim.layers[0]
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text")) and 10 in sim.layers[0] and (0, 2, "triple") in sim.gest, app.status.cget("text")

# ---- pad functions (fx) on keys
app.drop_assign(1, {"cat": "Layers & Pad", "action": "Lock / unlock the dial"})
app.drop_assign(3, {"cat": "Layers & Pad", "action": "Next colour theme"})
app.drop_assign(4, {"cat": "Layers & Pad", "action": "Display brighter"})
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text")) and sim.layers[0][1] == {"type": "fx", "val": "dial_lock"}
sim.settings15["dial_lock"] = False; sim.settings15["theme"] = 0; b0 = sim.bright
app.dev.request({"cmd": "input", "k": 1}); app.dev.request({"cmd": "input", "k": 3}); app.dev.request({"cmd": "input", "k": 4})
assert sim.settings15["dial_lock"] is True and sim.settings15["theme"] == 1 and sim.bright == b0 + 24
assert app.dev.request({"cmd": "input", "turn": 1})["runs"] == 0, "a locked dial does nothing"
app.dev.request({"cmd": "input", "k": 1}); assert app.dev.request({"cmd": "input", "turn": 1})["runs"] >= 1

# ---- key labels reach the pad with every upload and with single-key uploads
assert sim.labels[0] == ["LOCK / U", "PASTE", "NXT COLO", "DISPLAY", "MUTE", "VOL UP", "VOL DOWN"], sim.labels[0]
assert sim.labels[1][0] == "PREV TRK" and all(x for x in sim.labels[2]), sim.labels
before = sim.cmd_count.get("labels", 0)
app.drop_assign(2, {"cat": "Editing", "action": "Find"})
app.upload_slots([2])
assert pump(80, lambda: sim.labels[0][1] == "FIND") and sim.cmd_count["labels"] > before
r = app.dev.request({"cmd": "labels", "layer": 0}); assert r["l"][1] == "FIND"
for bad in ({"cmd": "labels", "layer": 3}, {"cmd": "labels", "l": ["x"] * 6}, {"cmd": "labels", "l": ["123456789"] * 7}, {"cmd": "labels", "l": ["a|b"] * 7}):
    try:
        app.dev.request(bad)
        raise SystemExit(f"accepted {bad}")
    except m.DeviceError:
        pass

# ---- boot report / rollback
app.boot_report()
assert pump(60, lambda: "Boot report written" in app.status.cget("text"))
assert "last reset: power-on" in app.devtab.log_box.get("1.0", "end") if hasattr(app.devtab, "log_box") else True
asked = []
m.messagebox.askyesno = lambda *a, **k: asked.append(a) or True
app.rollback_fw()
assert pump(60, lambda: "previous firmware" in app.status.cget("text")) and asked, app.status.cget("text")
m.messagebox.askyesno = lambda *a, **k: False
sent = sim.cmd_count.get("rollback", 0); app.rollback_fw(); time.sleep(0.2); assert sim.cmd_count.get("rollback", 0) == sent, "declined = nothing sent"
assert app.dev.request({"cmd": "boot_log", "clear": True})["counts"] == [0] * 6

# ---- dim command (level) and the hostile cases
assert app.dev.request({"cmd": "dim", "level": 30})["bl"] == 30 and sim.dim == 30
assert app.dev.request({"cmd": "dim", "on": False})["level"] == 0
for bad in ({"cmd": "dim", "level": 300}, {"cmd": "dim", "level": True}, {"cmd": "dim", "on": 1}):
    try:
        app.dev.request(bad)
        raise SystemExit(f"accepted {bad}")
    except m.DeviceError:
        pass
assert app.dev.request({"cmd": "settings"})["host_dim"] == 0

# ---- sound bars: the app sends the spectrum while the switch is on
made = []


class FakeStream:
    def __init__(self, cb):
        self.cb = cb
        made.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


app.spectrum = audio.Spectrum(stream_factory=FakeStream, rate=16000)
app.cfg["viz_on"] = True
assert pump(60, lambda: made)
made[0].cb([[x] for x in tone(640, 1024)], 1024, None, None)
assert pump(60, lambda: sim.viz[3] > 40), sim.viz
assert sim.viz_at > 0
app.cfg["viz_on"] = False
pump(20)

# ---- ring / progress / scrolling cards: shown as they are on 1.5, as plain cards on 1.4
app.cfg["info"]["extras"] = [{"type": "progress", "label": "YEAR", "arg": "year"}, {"type": "load", "label": "", "arg": ""}]
cards, badges, errs = app._info_collect()
assert [c["k"] for c in cards[-2:]] == ["p", "r"], cards
app.destroy()

# ---------------------------------------------------------------- a firmware 1.4.0 pad: nothing new is sent, everything says why
app, sim, pump = run("DESK_COMPANION_SIM_V14")
assert app.dev.info["fw"] == "1.4.0" and not any(c in app.dev.info["caps"] for c in m.NEW15_CAPS) and app.dev.info["modes"] == 12
bc, sc = app.behaviour, app.screens_card
assert pump(40, lambda: "need firmware 1.5" in bc.note.cget("text")) and all(str(w.cget("state")) == "disabled" for w in bc.w15)
assert pump(40, lambda: str(sc.mask_vars[0] and sc.widgets[0].cget("state")) == "normal")
assert all(str(w.cget("state")) == "disabled" for w in sc.new_boxes), "screens 13-20 are greyed out on a 1.4 pad"
before = dict(sim.cmd_count)
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text"))
assert "labels" not in sim.cmd_count, "no key labels are sent to a pad that does not know them"
for cmd in ("dim", "labels", "boot_log", "rollback"):
    try:
        app.dev.request({"cmd": cmd, "confirm": True})
        raise SystemExit(f"a 1.4 pad answered {cmd}")
    except m.DeviceError:
        pass
try:
    app.dev.request({"cmd": "settings", "clock_style": 5}); raise SystemExit("a 1.4 pad accepted clock style 5")
except m.DeviceError:
    pass
g = app.automation.gestures
g.layer.set("Layer 1"); g.slot.set("Chord K1 + K2 together (1.5)"); g.cat.set("Media"); g._cat_changed("Media"); g.action.set("Mute"); g.assign()
g.slot.set("K1"); g.gest.set("Triple-tap the key (1.5)"); g.assign()
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text")) and 10 not in sim.layers[0] and (0, 1, "triple") not in sim.gest, "chords / triple taps stay in the app's settings, the 1.4 pad gets nothing"
try:
    app.dev.request({"cmd": "remap", "key": 10, "type": "media", "val": "MUTE"}); raise SystemExit("a 1.4 pad accepted slot 10")
except m.DeviceError:
    pass
app.cfg["info"]["extras"] = [{"type": "disk", "label": "", "arg": "/"}, {"type": "progress", "label": "", "arg": "day"}]
cards, badges, errs = app._info_collect()
assert [c["k"] for c in cards[-2:]] == ["c", "c"] and all(c["t"].endswith("%") for c in cards[-2:]), cards
app.destroy()
print("ALL FW15 APP TESTS PASSED")
