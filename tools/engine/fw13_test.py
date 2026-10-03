"""Engine test of the firmware-1.3 features (against the simulated pad): pad settings, gestures, host-op gating, and compatibility with a 1.2.0 pad."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit, isolate    # noqa: E402

isolate("dcfw13_")
import core.base as m    # noqa: E402
from desk_lib import padconst as px    # noqa: E402


def run(v12):
    os.environ.pop("DESK_COMPANION_SIM_V12", None)
    os.environ.pop("DESK_COMPANION_SIM_V13", None)
    os.environ["DESK_COMPANION_SIM_V12" if v12 else "DESK_COMPANION_SIM_V13"] = "1"
    return Kit("dcfw13b_", fresh=False)


# ======================================================================= firmware 1.3 pad
k = run(False)
app, sim, pump = k.e, k.sim, k.pump
status = lambda: k.status    # noqa: E731
caps = app.dev.info["caps"]
assert all(c in caps for c in m.NEW13_CAPS) and app.dev.info["fw"] == "1.3.0" and not any(c in caps for c in m.NEW14_CAPS)
assert app.fw_status()[0] == "old"
assert app.pad_settings_ok() and not app.pad_settings_ok15() and "firmware 1.5" in app.pad_settings_note()
app.pad_settings_apply(dial_accel=2)
assert pump(60, lambda: sim.settings["dial_accel"] == 2)
app.pad_settings_apply(clock_style=2, saver_s=300, saver_style=2)
assert pump(60, lambda: sim.settings["clock_style"] == 2 and sim.settings["saver_s"] == 300 and sim.settings["saver_style"] == 2)
app.pad_settings_apply(night_on=True, night_from=21, night_to=6, night_level=45)
assert pump(60, lambda: sim.settings["night_on"] and sim.settings["night_from"] == 21 and sim.settings["night_to"] == 6 and sim.settings["night_level"] == 45), sim.settings
loaded = []
app.on("pad_settings", lambda st: loaded.append(st))
app.dev.disconnect(); app._on_disconnected(); app.pump()
assert not app.pad_settings_ok() and "Connect the pad" in app.pad_settings_note()
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim2 = app.dev.ser.sim
assert pump(60, lambda: loaded and loaded[-1]["dial_accel"] == 0 and loaded[-1]["saver_s"] == 0), loaded
sim2.settings.update(dial_accel=1, saver_s=600, night_on=True, night_level=60)
app.pad_settings_on_connected()
assert pump(60, lambda: loaded[-1]["dial_accel"] == 1 and loaded[-1]["saver_s"] == 600 and loaded[-1]["night_on"] and loaded[-1]["night_level"] == 60)
app.pad_settings_busy = True
app.pad_settings_apply(dial_accel=2)
app.pad_settings_busy = False
time.sleep(0.3)
assert sim2.settings["dial_accel"] == 1, "filling the controls must not echo back to the pad"

# ---- gestures
sim = sim2
app.gesture_assign(1, "K3", "Hold the key", "Media", "Mute")
assert app.cfg["gestures"] == {"1:3:hold": {"cat": "Media", "action": "Mute"}}
assert pump(60, lambda: (1, 3, "hold") in sim.gest), "assigned and sent at once while connected"
assert sim.gest[(1, 3, "hold")] == {"type": "media", "val": "MUTE"}
app.gesture_assign(0, "K1", "Double-tap the key", "Editing", "Copy")
assert pump(60, lambda: (0, 1, "double") in sim.gest) and len(sim.gest) == 2
r = app.dev.request({"cmd": "getkeys", "layer": 1}); assert r["slots"][2]["h"] is True
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in status()), status()
assert len(sim.gest) == 2
app.gesture_remove("1:3:hold")
assert pump(60, lambda: (1, 3, "hold") not in sim.gest) and len(sim.gest) == 1
sim.gest[(2, 5, "hold")] = {"type": "media", "val": "MUTE"}
app.upload_all()
assert pump(100, lambda: (2, 5, "hold") not in sim.gest and (0, 1, "double") in sim.gest), sim.gest
app.cfg["custom"]["Site"] = {"type": "host", "val": {"op": "url", "arg": "https://example.org/g"}}
app.gesture_assign(0, "K2", "Hold the key", "Custom", "Site")
assert ("url", "https://example.org/g") in app._allowed_host()
assert pump(60, lambda: (0, 2, "hold") in sim.gest)
try:
    app.gesture_assign(0, "K2", "Hold the key", "Custom", "")
    raise SystemExit("empty action accepted")
except ValueError:
    pass
app.cfg["gestures"]["9:9:wiggle"] = {"cat": "x", "action": "y"}; app.cfg["gestures"]["0:1:hold"] = "junk"; app.save_cfg()
g = m.load_config()["gestures"]
assert set(g) == {"0:1:double", "0:2:hold"}, g
assert app.action_spec("Type a snippet", "{date}") == ("host", {"op": "snippet", "arg": "{date}"})
assert sim.settings["dial_accel"] == 1
k.close()

# ======================================================================= firmware 1.2.0 pad
k = run(True)
app, sim, pump = k.e, k.sim, k.pump
status = lambda: k.status    # noqa: E731
caps = app.dev.info["caps"]
assert app.dev.info["fw"] == "1.2.0" and not any(c in caps for c in m.NEW13_CAPS)
assert app.fw_status()[0] == "old", app.fw_status()
assert not app.pad_settings_ok() and "older than 1.3" in app.pad_settings_note()
before = dict(sim.cmd_count)
app.pad_settings_apply(dial_accel=2); time.sleep(0.2)
assert sim.cmd_count == before, "no settings command is sent to a pad that lacks the capability"
app.gesture_assign(0, "K1", "Hold the key", "Media", "Mute")
assert app.cfg["gestures"] and "need firmware 1.3" in status() and not sim.gest, "gesture saved, but nothing sent to the old pad"
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in status()) and not sim.gest and "gesture" not in str(sim.cmd_count)
try:
    app.action_spec("Type a snippet", "x")
    raise SystemExit("snippet accepted for a 1.2 pad")
except ValueError as e:
    assert "firmware 1.3" in str(e)
k.close()
assert px.has_cap
print("ALL ENGINE FW13 TESTS PASSED")
