"""Headless test of the firmware-1.3 features in the app (against the simulated pad): behaviour settings, gestures, host-op gating,
and compatibility with a 1.2.0 pad.   xvfb-run -a python3 tools/fw13_test.py"""
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dcfw13_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import padextras as px   # noqa: E402


def run(v12):
    if v12:
        os.environ["DESK_COMPANION_SIM_V12"] = "1"
    else:
        os.environ.pop("DESK_COMPANION_SIM_V12", None)
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


# ======================================================================= firmware 1.3 pad
app, sim, pump = run(False)
caps = app.dev.info["caps"]
assert all(c in caps for c in m.NEW13_CAPS) and app.dev.info["fw"] == "1.3.0" == m.FW_BUNDLED
assert app.fw_status()[0] == "ok"
app.tabs.set("Device")
b = app.behaviour
assert pump(40, lambda: all(str(w.cget("state")) == "normal" for w in b.widgets)), "controls enabled for a 1.3 pad"
# changing a control sends the setting, and the pad answers with the new state
b.v["dial_accel"].set("Fast"); b.apply(dial_accel=2)
assert pump(60, lambda: sim.settings["dial_accel"] == 2)
b.v["clock_style"].set("Binary"); b.apply(clock_style=2)
b.v["saver_s"].set("5 minutes"); b.apply(saver_s=300)
b.v["saver_style"].set("Matrix rain"); b.apply(saver_style=2)
assert pump(60, lambda: sim.settings["clock_style"] == 2 and sim.settings["saver_s"] == 300 and sim.settings["saver_style"] == 2)
b.v["night_on"].set(True); b.v["night_from"].set("21:00"); b.v["night_to"].set("06:00"); b.level.set(45); b._night_changed()
assert pump(60, lambda: sim.settings["night_on"] and sim.settings["night_from"] == 21 and sim.settings["night_to"] == 6 and sim.settings["night_level"] == 45), sim.settings
# controls are filled from the pad when it connects
app.dev.disconnect(); app._on_disconnected()
assert all(str(w.cget("state")) == "disabled" for w in b.widgets) and "Connect the pad" in b.note.cget("text")
for k in ("dial_accel", "clock_style", "saver_s", "saver_style"):
    b.v[k].set("?")
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim2 = app.dev.ser.sim                                           # a fresh simulated pad = factory settings
assert pump(60, lambda: b.v["dial_accel"].get() == "Off" and b.v["clock_style"].get() == px.CLOCKS[0][0] and b.v["saver_s"].get() == "Off"), "controls follow the pad"
sim2.settings.update(dial_accel=1, saver_s=600, night_on=True, night_level=60)
b.on_connected()
assert pump(60, lambda: b.v["dial_accel"].get() == "Normal" and b.v["saver_s"].get() == "10 minutes" and b.v["night_on"].get() and int(b.level.get()) == 60)
b.busy = True; b.apply(dial_accel=2); b.busy = False; time.sleep(0.3)
assert sim2.settings["dial_accel"] == 1, "filling the controls must not echo back to the pad"

# ---- gestures
sim = sim2
page = app.automation.gestures
page.layer.set("Layer 2"); page.slot.set("K3"); page.gest.set("Hold the key")
page.cat.set("Media"); page._cat_changed("Media"); page.action.set("Mute")
page.assign()
assert app.cfg["gestures"] == {"1:3:hold": {"cat": "Media", "action": "Mute"}}
assert pump(60, lambda: (1, 3, "hold") in sim.gest), "assigned and sent at once while connected"
assert sim.gest[(1, 3, "hold")] == {"type": "media", "val": "MUTE"}
page.gest.set("Double-tap the key"); page.slot.set("K1"); page.layer.set("Layer 1")
page.cat.set("Editing"); page._cat_changed("Editing"); page.action.set("Copy"); page.assign()
assert pump(60, lambda: (0, 1, "double") in sim.gest) and len(sim.gest) == 2
r = app.dev.request({"cmd": "getkeys", "layer": 1}); assert r["slots"][2]["h"] is True
# upload_all keeps them in step; removing one clears it on the pad
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text")), app.status.cget("text")
assert len(sim.gest) == 2
page.remove("1:3:hold")
assert pump(60, lambda: (1, 3, "hold") not in sim.gest) and len(sim.gest) == 1
sim.gest[(2, 5, "hold")] = {"type": "media", "val": "MUTE"}      # a stale gesture only the pad has: the next upload removes it
app.upload_all()
assert pump(100, lambda: (2, 5, "hold") not in sim.gest and (0, 1, "double") in sim.gest), sim.gest
# gesture host actions are whitelisted like any other key action
page.gest.set("Hold the key"); page.slot.set("K2"); page.layer.set("Layer 1")
app.cfg["custom"]["Site"] = {"type": "host", "val": {"op": "url", "arg": "https://example.org/g"}}
app.refresh_library()
page.cat.set("Custom"); page._cat_changed("Custom"); page.action.set("Site"); page.assign()
assert ("url", "https://example.org/g") in app._allowed_host()
assert pump(60, lambda: (0, 2, "hold") in sim.gest)
# config sanity: malformed gestures are dropped on load
app.cfg["gestures"]["9:9:wiggle"] = {"cat": "x", "action": "y"}; app.cfg["gestures"]["0:1:hold"] = "junk"; app.save_cfg()
g = m.load_config()["gestures"]
assert set(g) == {"0:1:double", "0:2:hold"}, g

# ---- new host actions are allowed on a 1.3 pad
app.act_kind.set("Type a snippet"); app._act_kind_changed("Type a snippet"); app.act_arg.delete(0, "end"); app.act_arg.insert(0, "{date}")
assert app._action_spec() == ("host", {"op": "snippet", "arg": "{date}"})
assert sim.settings["dial_accel"] == 1
app.destroy()

# ======================================================================= firmware 1.2.0 pad (no new features): everything degrades politely
app, sim, pump = run(True)
caps = app.dev.info["caps"]
assert app.dev.info["fw"] == "1.2.0" and not any(c in caps for c in m.NEW13_CAPS)
assert app.fw_status()[0] == "old", app.fw_status()
b = app.behaviour
assert pump(40, lambda: "older than 1.3" in b.note.cget("text"))
assert all(str(w.cget("state")) == "disabled" for w in b.widgets)
before = dict(sim.cmd_count)
b.apply(dial_accel=2); time.sleep(0.2)
assert sim.cmd_count == before, "no settings command is sent to a pad that lacks the capability"
page = app.automation.gestures
page.cat.set("Media"); page._cat_changed("Media"); page.action.set("Mute"); page.assign()
assert app.cfg["gestures"] and "need firmware 1.3" in app.status.cget("text") and not sim.gest, "gesture saved, but nothing sent to the old pad"
app.upload_all()
assert pump(100, lambda: "Uploaded to the pad" in app.status.cget("text")) and not sim.gest and "gesture" not in str(sim.cmd_count)
app.act_kind.set("Type a snippet"); app._act_kind_changed("Type a snippet"); app.act_arg.delete(0, "end"); app.act_arg.insert(0, "x")
try:
    app._action_spec(); raise SystemExit("snippet accepted for a 1.2 pad")
except ValueError as e:
    assert "firmware 1.3" in str(e)
app.destroy()
print("ALL FW13 APP TESTS PASSED")
